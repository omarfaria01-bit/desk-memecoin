#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DESK MEMECOIN SOLANA v6 (paper che simula il live: preventivi Jupiter reali, fee dinamiche, latenza)

Modalita (chiave MODE in config.json):
  paper -> soldi simulati (predefinita)
  dry   -> usa il tuo wallet vero per preventivi Jupiter e simulazioni on-chain,
           ma NON invia nessuna transazione: verifica che il live funzioni
  live  -> compra e vende con soldi veri tramite Jupiter

Comandi (dalla cartella ~/bot):
  ./venv/bin/python bot.py          avvia il desk
  ./venv/bin/python bot.py recap    mostra il recap del periodo in corso
  touch STOP                        arresto di emergenza: niente acquisti e vende tutto
  rm STOP                           riattiva gli acquisti

Agenti: Scout -> Filter -> Analyst -> Executor -> Risk, piu Learner e Logger.
Ogni decisione viene scritta con il motivo in events.jsonl.
"""

import asyncio
import base64
import csv
import json
import math
import os
import random
import re
import sys
import threading
import time
from collections import Counter, deque
from datetime import datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import aiohttp
import websockets
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(DATA_DIR, "state.json")
STOP_PATH = os.path.join(DATA_DIR, "STOP")
WSOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SKIP_MINTS = {WSOL, USDC, "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"}
STOPWORDS = {"the", "coin", "token", "sol", "solana", "inu", "pump", "fun", "official",
             "new", "and", "for", "you", "are", "this", "that", "with", "meme", "wif"}

# ======================================================================= regole
CFG = {
    "MODE": "paper",
    "START_BALANCE": 1000.0,          # saldo simulato in paper e dry
    # dimensione delle operazioni
    # DIMENSIONE (decisioni finali 30/10/2026): size = 3% equity x (punteggio / 0.6), limitata tra 2% e 5%
    # dell'equity e mai oltre l'1% della liquidita della pool. (v4: min(5% equity, 1.5% pool))
    "BASE_TRADE_PCT": 0.03,           # nuovo: 3% dell'equity a punteggio 0.6
    "SIZE_SCORE_REF": 0.6,            # nuovo: punteggio di riferimento della scala
    "MAX_TRADE_PCT": 0.05,            # (orig. 0.10) massimo 5% dell'equity per operazione
    "MIN_TRADE_PCT": 0.02,            # (orig. 0.03) minimo 2% dell'equity (sotto il tetto pool puo scendere)
    "MAX_POSITIONS": 8,               # (orig. 20; v4 6)
    "MAX_POOL_SHARE_PCT": 1.0,        # (orig. 2.0; v4 1.5) mai piu dell'1% della liquidita della pool
    "MAX_EXPOSURE_PCT": 30.0,         # al massimo 30% dell'equity investito in totale
    "COST_GATE_MULT": 5.0,            # nuovo: scarta se 5 x costo stimato andata+ritorno > TP1_PCT
    # uscite
    "STOP_LOSS_PCT": -10.0,           # (orig. -8.0) confermato su due controlli di fila
    "CRASH_EXIT_PCT": -15.0,          # uscita immediata (v8.1: con stop piu largo di -15 scatta a 1.5 x stop)
    # v8.1: stop dinamico = DYN_STOP_ATR_MULT x ATR (candele 1m prima dell'ingresso), tra DYN_STOP_MIN e DYN_STOP_MAX.
    # "off" = niente; "shadow" = calcolato e registrato, le uscite usano ancora STOP_LOSS_PCT; "on" = usato davvero.
    "DYN_STOP_MODE": "shadow",
    "DYN_STOP_ATR_MULT": 3.0,
    "DYN_STOP_MIN": -8.0,             # lato stretto
    "DYN_STOP_MAX": -30.0,            # lato largo
    "DYN_STOP_SIZE_SCALE": True,      # size x |stop base| / |stop dinamico| (perdita in $ allo stop uguale; mai > 1)
    # v8.1b: TP1 e trailing dinamici dall'ATR. "off" = niente; "shadow" = calcolati e registrati a ogni ingresso
    # (reale e shadow), le uscite restano TP1_PCT / TRAIL_ACTIVATE_PCT / TRAIL_DISTANCE_PCT. Nessun modo "on" per ora.
    "DYN_TP_MODE": "shadow",
    "DYN_TP1_ATR_MULT": 6.0,          # TP1 = 6 x ATR, tra +15 e +100
    "DYN_TP1_MIN": 15.0,
    "DYN_TP1_MAX": 100.0,
    "DYN_TRAIL_ACT_ATR_MULT": 6.0,    # attivazione trailing = 6 x ATR, tra +15 e +100
    "DYN_TRAIL_ACT_MIN": 15.0,
    "DYN_TRAIL_ACT_MAX": 100.0,
    "DYN_TRAIL_DIST_ATR_MULT": 2.0,   # distanza trailing = 2 x ATR, tra 5 e 25
    "DYN_TRAIL_DIST_MIN": 5.0,
    "DYN_TRAIL_DIST_MAX": 25.0,
    "DYN_ATR_BARS": 10,               # candele per l'ATR (fallback: quelle disponibili se >= DYN_ATR_MIN_BARS)
    "DYN_ATR_MIN_BARS": 3,
    "DYN_ATR_EXTRA_PER_MIN": 2,       # al massimo 2 richieste OHLCV in piu al minuto (solo shadow senza candele)
    "PRICE_PATHS": True,              # prezzo e liquidita a ogni controllo in price_paths/<mint>.jsonl
    "POST_EXIT_MIN": 180,             # dopo l'uscita il prezzo si segue ancora per 180 min
    "PRICE_PATH_MAX_BYTES": 300000,   # oltre: il file ruota in <mint>.jsonl.1 (una sola copia)
    "PRICE_PATH_MAX_FILES": 3000,     # file piu vecchi cancellati oltre questo numero
    "POST_EXIT_MAX": 150,             # token seguiti dopo l'uscita al massimo insieme
    # SCALA DI PRESE DI PROFITTO (frazioni della quantita iniziale), poi trailing sul resto
    "TP1_PCT": 30.0,                  # (v4 40) primo gradino a +30%...
    "TP1_FRACTION": 0.30,             # (v4 0.25) ...vende il 30%...
    "BREAKEVEN_PCT": 3.0,             # nuovo: ...poi lo stop sale a pareggio +3% netto
    "TAKE_PROFIT_PCT": 80.0,          # TP2 (orig. 100) secondo gradino a +80% (overrides TP2_PCT / TP_PCT)
    "TAKE_PROFIT_FRACTION": 0.30,     # TP2 (orig. 0.5; v4 0.25) vende un altro 30% (TP2_FRACTION / TP_SELL_FRACTION)
    "TRAIL_TIGHT_AFTER_PCT": 150.0,   # (v4 100) dopo un massimo di +150%...
    "TRAIL_TIGHT_PCT": 8.0,           # (v4 7) ...il trailing si stringe all'8% (overrides TRAIL_TIGHT_DIST_PCT)
    "TIME_STOP_MIN": 30,              # (v4 45) dopo 30 min...
    "TIME_STOP_MIN_PCT": 5.0,         # ...esce se non e almeno a +5% (solo prima del primo gradino)
    "TRAIL_ACTIVATE_PCT": 40.0,       # (orig. 15.0) il trailing parte da +40%...
    "TRAIL_DISTANCE_PCT": 15.0,       # (orig. 10.0) ...con distanza 15%
    "MAX_HOLD_MIN": 180,              # (orig. 360)
    "LIQ_DROP_EXIT_PCT": 50.0,        # liquidita dimezzata = possibile rug, esce
    "DAILY_LOSS_LIMIT_PCT": -10.0,    # (orig. -20.0; v4 -15) stop giornaliero, azzerato a mezzanotte Europe/Rome
    "FEE_PCT": 1.0,                   # commissioni simulate per lato (paper)
    # v8.6 (A): PnL "CPMM" solo registrato (pnl_cpmm): x*y=k sulla meta della pool che resta (drain-aware),
    # presa di profitto riempita come LIMITE al target (mai al prezzo oltre), fee della pool per dex
    # v8.6 (C): seconda conferma dopo ENTRY_CONFIRM_SEC (solo registrata in entry_features.confirm2, mai blocca)
    "ENTRY_CONFIRM_MODE": "shadow",   # 'shadow' = solo misura, 'off' = spento
    "ENTRY_CONFIRM_SEC": 45,
    "ENTRY_CONFIRM_LIQ_MIN": 0.85,    # liquidita al secondo controllo >= 85% di quella d'ingresso
    "ENTRY_CONFIRM_MAX_DROP_PCT": 8.0,  # prezzo non sceso oltre l'8% dall'ingresso
    "CPMM_POOL_FEE_PCT": {"pumpfun": 1.25, "pumpswap": 0.30, "raydium": 0.25, "meteora": 0.25},
    "SLIPPAGE_MULT": 2.0,             # nuovo: slippage extra per lato = 2 x size / liquidita
    "PRIORITY_FEE_SOL": 0.001,        # nuovo: priority fee per transazione (lato)
    "SOL_USD_FALLBACK": 150.0,        # nuovo: prezzo SOL prudente se GeckoTerminal non risponde
    # Filter: sicurezza e distribuzione
    # FILTRI ALLENTATI (30/10/2026): tra parentesi il valore originale
    "MIN_VOLUME_H1": 25000.0,         # (orig. 40000.0)
    "MIN_LIQUIDITY_USD": 8000.0,      # (orig. 15000.0) liquidita 0 resta sempre scartata
    "MAX_TOP10_PCT": 35.0,            # (orig. 25.0)
    "MIN_LP_LOCKED_PCT": 50.0,        # (orig. 90.0) LP bruciata o bloccata almeno al 50%; 0% sempre scartata
    "MIN_HOLDERS": 100,               # (orig. 150)
    "MAX_TRANSFER_FEE_PCT": 5.0,
    "MAX_INSIDER_PCT": 10.0,          # quota dei wallet segnalati come insider
    "MAX_IDENTICAL_WALLETS": 4,       # la firma della distribuzione coordinata
    "MAX_CREATOR_PCT": 5.0,
    "REJECT_INSIDER_NETWORKS": True,  # v6: con True le reti collegate sono un segnale SOFT graduato (vedi sotto)
    # v6: segnali SOFT graduati -> penalita sul punteggio + size ridotta; scarto solo oltre la soglia grave.
    # Restano HARD: mint/freeze non revocate, rug, tassa, LP 0% o non verificabile, insider, creatore,
    # pochi holder, rischi gravi RugCheck, nessun percorso di vendita, liquidita bassa, lista nera.
    "TOP10_HARD_PCT": 50.0,           # top 10 tra MAX_TOP10_PCT (35) e 50 = soft; oltre 50 = scarto
    "EQUAL_MIN_PCT": 0.1,             # quote "gemelle" contate solo da 0.1% in su
    "EQUAL_HARD_PCT": 10.0,           # wallet con la stessa quota (>= MAX_IDENTICAL_WALLETS) = soft; se insieme >= 10% = scarto
    "BUNDLE_SOFT_PCT": 3.0,           # reti di wallet collegati: sotto il 3% dell'offerta nessun segnale
    "BUNDLE_HARD_PCT": 25.0,          # reti collegate oltre il 25% dell'offerta = scarto
    "BUNDLE_MAX_NET_SIZE": 100,       # reti con piu di 100 wallet (exchange, airdrop) non contano come bundle
    "LP_HARD_PCT": 25.0,              # LP bloccata tra 25% e MIN_LP_LOCKED_PCT (50) = soft; sotto 25% (o 0%) = scarto
    "SOFT_PENALTY": 0.05,             # penalita sul punteggio per ogni segnale soft
    "SOFT_SIZE_MULT": 0.5,            # size x 0.5 per ogni segnale soft (si moltiplicano)
    "SOFT_MAX_FLAGS": 3,              # 3 o piu segnali soft insieme = scarto
    # v7: cluster di wallet collegati (tipo Bubblemaps) da dati on-chain GRATUITI (RPC pubblica Solana), solo sui
    # candidati gia approvati dall'Analyst. Graduato: segnale soft forte; scarto solo nei casi estremi.
    "CLUSTER_CHECK": True,
    "CLUSTER_RPC": "https://api.mainnet-beta.solana.com",
    "CLUSTER_TOP_N": 40,              # holder analizzati (esclusi pool/curve/locker)
    "CLUSTER_FRESH_TX": 100,          # wallet "nuovo" = meno di 100 transazioni in tutto
    "CLUSTER_MAX_HOLDERS_GPA": 60000, # oltre: si usano solo i top holder di RugCheck
    "CLUSTER_CACHE_SEC": 1800,
    "BUNDLE_NET_MIN_WALLETS": 3,      # una rete conta se ha almeno 3 wallet collegati (stesso finanziatore)
    "BUNDLE_MAX_NETWORKS": 4,         # ESTREMO: >= 4 reti...
    "BUNDLE_TOTAL_HARD_PCT": 20.0,    # ...che insieme tengono >= 20% dell'offerta
    "BUNDLE_MAX_INSIDER_WALLETS": 30, # ESTREMO: >= 30 wallet collegati in totale (reti >100 wallet escluse se tengono poco)
    "CLUSTER_FRESH_HARD": 0.75,       # ESTREMO: >= 75% dei top holder sono wallet nuovi E almeno 2 reti
    "CLUSTER_SERVICE_SHARE": 0.8,     # un solo finanziatore per >= 80% dei wallet e poca offerta = servizio/relayer: soft
    "CLUSTER_SOFT_NETWORKS": 2,       # soft: >= 2 reti...
    "CLUSTER_SOFT_PCT": 5.0,          # ...o reti insieme >= 5% dell'offerta...
    "CLUSTER_FRESH_SOFT": 0.50,       # ...o >= 50% dei top holder wallet nuovi
    "CLUSTER_PENALTY": 0.08,          # penalita del segnale soft 'cluster' (piu forte degli altri)
    "CLUSTER_SIZE_MULT": 0.4,         # size x0.4
    # v8.2: cluster v2 (sources/cluster_v2.py) su OGNI candidato che passa il Filter, prima della decisione
    # (anche per gli shadow, cosi il Learner ha i dati). Fallimento o tempo scaduto = 'unknown', MAI 'ok'.
    "CLUSTER_V2": True,
    "CLUSTER2_WAIT_SEC": 90,          # attesa massima per un trade vero; oltre = 'unknown'
    "CLUSTER2_BUDGET_SEC": 75,        # tempo massimo di una scansione (top 150 holder, ~450 chiamate RPC)
    "CLUSTER2_MAX_QUEUE": 4,          # scansioni in background per gli shadow (quelle dei trade veri passano sempre)
    "CLUSTER_GROUP_HARD_PCT": 20.0,   # un SOLO gruppo di wallet collegati >= 20% dell'offerta = scarto rigido
    "STX_MODE": "shadow",   # v8.7 (E): primi scambi pump.fun (sniper/primi20/dev) - 'off' = spento
    "STX_MAX_PER_H": 30, "STX_PAGES": 3,   # v8.7 (E): max token/ora e pagine da 100 scambi (host pump.fun, gap 5 s)
    "DISCORD_CALLS_MODE": "shadow",   # v8.8: call Discord NKT (sources/discord_calls.json) solo registrate - 'off' = spento
    "SMART_WALLETS_MODE": "shadow",   # v8.5s: lista smart money di Kiwi: 'shadow' = solo registrato, 'off' = spento
    "CLUSTER_FAM_MODE": "shadow",     # review Claude: famiglie soft -> HARD ('shadow' = solo registrato, 'on' = attivo)
    "CLUSTER_FAM_HARD_N": 3,          # famiglie soft indipendenti insieme per l'HARD (override 3-5)
    "CLUSTER_UNKNOWN_ACTION": "skip", # v8.5: cluster non verificabile = niente acquisto (shadow 'cluster_unknown')
    "CLUSTER_UNKNOWN_PENALTY": 0.05,
    "CLUSTER_UNKNOWN_SIZE_MULT": 0.5,
    "CLUSTER_HELD_RECHECK_MIN": 20,   # ricontrollo cluster delle posizioni aperte (es. DESSA)
    "CLUSTER_HELD_ACTION": "exit",    # posizione aperta che diventa 'hard': 'exit' (vende) o 'alert' (solo avviso)
    # v8.2: controllo honeypot prima di ogni ingresso (solo letture e preventivi, mai uno swap)
    "HONEYPOT_CHECK": True,
    "HONEYPOT_SELL_IMPACT_PCT": 25.0, # preventivo di vendita con impatto > 25% = scarto rigido
    "HONEYPOT_MAX_FEE_BPS": 999,      # v8.4: fee di trasferimento Token-2022 >= 10% = scarto rigido (sotto: soft)
    "FEE_PEN_PER_PCT": 0.02,          # v8.4: penalita sul punteggio per ogni 1% di fee di trasferimento
    "FEE_SIZE_PER_PCT": 0.07,         # v8.4: size -7% per ogni 1% di fee (minimo 0.3x)
    "GOPLUS_CHECK": True,             # v8.4: GoPlus token_security nel controllo pre-acquisto
    "GOPLUS_SOFT_PENALTY": 0.03,      # v8.4: metadati modificabili e altri soft GoPlus
    # v8.4: penalita per eta della pool/token (ore) + requisiti di attivita sopra i 3 giorni
    "AGE_PEN_1D": 0.0,                # < 24 h: nessuna
    "AGE_PEN_1_3D": 0.02,             # 1-3 giorni: leggera
    "AGE_PEN_3_7D": 0.05,             # 3-7 giorni: media
    "AGE_PEN_7_30D": 0.10,            # 7-30 giorni: forte
    "AGE_PEN_30D": 0.20,              # > 30 giorni: quasi bloccante
    "AGE_ACT_MIN_DAYS": 3.0,          # sopra i 3 giorni servono attivita e volatilita piu alte:
    "AGE_MIN_VOL_LIQ": 0.3,           #   volume 1h / liquidita >= 0.3 oppure volume 1h / mcap >= 3% (calibrato v8.4)
    "AGE_MIN_VOL_MCAP": 0.03,
    "AGE_MIN_ATR": 0.7,               #   e ATR 1m >= 0.7% oppure |variazione 1h| >= 8%
    "AGE_MIN_CHG_H1": 8.0,
    "AGE_EASE_VOL_LIQ": 2.0,          # attivita davvero alta (vol 1h/liq >= 2 e ATR >= 2% o |chg 1h| >= 25%):
    "AGE_EASE_ATR": 2.0,              #   penalita dimezzata
    "AGE_EASE_CHG_H1": 25.0,
    "AGE_EASE_MULT": 0.5,
    "HONEYPOT_UNKNOWN_ACTION": "skip",# v8.4c (Kiwi): controllo non riuscito = MAI acquisto, solo shadow 'honeypot_unknown'
    "HONEYPOT_RETRY_MIN": 5,          # v8.4c: dopo uno 'sconosciuto' il token puo tornare dopo 5 min (giro successivo)...
    "HONEYPOT_RETRY_MAX": 3,          # ...al massimo 3 volte, poi cooldown normale (COOLDOWN_MIN)
    # v8.4c (BNB "Broke Nigga Baddie"): ticker di grandi asset/exchange su un mint NON ufficiale = blocco nel Filter
    # (storico: impostore HYPE shadow -92.6%, BNB cattivo; nessun impostore vincente). 'block' oppure 'off'
    "TICKER_IMPOSTOR_ACTION": "block",
    "CREATOR_HISTORY": True,         # v8.4e: storico del creatore (pump.fun gratis, cache 12 h): soft graduale, hard >=5/70%
    "STALL_RESET_SEC": 180,          # v8.4d: ciclo fermo oltre 3 min (pausa processo/macchina) = "nessun prezzo" riparte
    "NOPRICE_MAX_WAIT": 15,          # v8.4d: tentativi (~2 min l'uno) con seconda fonte giu prima di "dati non validi"
    # v8.2: etichette 'bad_cluster'/'suspected_rug' diventano 'rug' se il prezzo crolla dal massimo o la LP sparisce
    "RUG_PROMOTE_DROP_PCT": 50.0,
    "RUG_PROMOTE_LIQ_DROP_PCT": 50.0,
    # v7: LP sbloccata = segnale SOFT (penalita, size ridotta, tenuta massima piu corta). HARD solo se estremo.
    "LP_EXTREME_LOCKED_PCT": 5.0,     # HARD: LP bloccata/bruciata <= 5%...
    "LP_CREATOR_HOLD_PCT": 50.0,      # ...e almeno il 50% dei token LP in mano al creatore
    "LP_REMOVAL_DROP_PCT": 30.0,      # HARD: liquidita gia scesa del 30% da quando lo osserviamo (rimozione iniziata)
    "LP_SOFT_MAX_HOLD_MIN": 60,       # trade con LP sbloccata: tenuta massima 60 min
    "RUG_WINDOW_MIN": 60,             # rug = liquidita -50% entro 60 min dall'ingresso (o -80% in qualsiasi momento)
    # v7: shadow anche sugli scarti "soft" dell'Analyst (momentum/momento/VWAP/grafico) dopo i controlli di sicurezza
    "SHADOW_ANALYST_REJECTS": True,
    "SHADOW_MAX_OPEN_ANALYST": 25,    # al massimo 25 shadow aperti di questo tipo (non tolgono posto agli altri)
    # v7: segnali extra (sources/): bonus SOLO dopo tutti i filtri HARD, somma con tetto
    "SIGNALS_ENABLED": True,
    "GMGN_TREND_BONUS": 0.03,         # fonte principale: trending GMGN, SOLO con chiave personale (GMGN_API_KEY)
    "FOMO_TREND_BONUS": 0.03,         # solo dalla vera board Fomo (serve FOMOAPI_KEY), non dal sostituto
    "FOMO_SUB_BONUS": 0.0,            # sostituto DexScreener boosts: 0 finche il Learner non vede che rende
    "KOL_BONUS": 0.03,
    "KOL_THESIS_BONUS": 0.02,
    "MOMENTUM_YOUNG_BONUS": 0.03,
    "TOTAL_BONUS_CAP": 0.10,
    "KOL_SOLD_PENALTY": 0.02,
    "KOL_MIN_SOL": 0.1,
    "MOMENTUM_MIN_SCORE": 0.5,
    # v8: controllo veloce della qualita del progetto (sources/project_check.py): sito, social, GitHub, metadati
    "PROJECT_CHECK": True,
    "PROJECT_TIMEOUT_SEC": 5.0,       # oltre: nessun effetto (mai uno scarto per questo controllo)
    "PROJECT_BONUS": 0.02,            # bonus = PROJECT_BONUS x project_score se score >= PROJECT_MIN_SCORE
    "PROJECT_MIN_SCORE": 0.5,
    "PROJECT_EMPTY_PENALTY": 0.01,    # penalita lieve: niente sito e niente social
    # v8: analisi approfondita (Mark): coda sources/deep_review_queue.jsonl, verdetti sources/deep_reviews.json
    "DEEP_QUEUE_PEAK_PCT": 50.0,      # in coda anche trade/shadow arrivati a +50%
    "DEEP_HOLD_MIN_SCORE": 70.0,      # voto >= 70: tenuta massima (e stop a tempo) piu lunga...
    "DEEP_HOLD_EXTRA_MIN": 60,        # ...di 60 minuti
    "DEEP_LOW_SCORE": 30.0,           # voto <= 30: trailing piu stretto...
    "DEEP_TRAIL_TIGHTEN_PCT": 3.0,    # ...di 3 punti (mai sotto 3%)
    "FOMO_SCOUT_SEC": 180,
    "LEARN_BONUS_MIN_N": 15,          # esiti minimi CON e SENZA il segnale
    "LEARN_BONUS_WINDOW": 150,
    "LEARN_BONUS_EDGE": 2.0,          # +step solo se la media con il segnale supera quella senza di >= 2 punti
    # v7: casi etichettati: alla chiusura l'esito diventa un esempio per il Learner (chiave = etichetta)
    "LABELLED_CASES": {"npz2FhoufQ7pKqhGpaipYWacrAkidyW2yDpVWSvpump": "cluster"},
    # v8.2: casi gia osservati, registrati una sola volta come esempi per il Learner (vedi LABEL_SEEDS)
    # v6: shadow trade (operazioni ipotetiche, nessun denaro) sui token scartati per un segnale soft o di poco
    "SHADOW_TRADES": True,
    "SHADOW_NEAR_SCORE": 0.05,        # anche i punteggi entro 0.05 sotto la soglia
    "SHADOW_MAX_OPEN": 60,
    "SHADOW_CHECK_SEC": 30,
    "SHADOW_SIZE_USD": 30.0,          # size ipotetica (costi stimati con la stessa formula del paper)
    "SHADOW_ROUTE_CHECK": True,       # preventivo Jupiter di vendita all'apertura e alla chiusura dello shadow
    # v6: il Learner impara dagli shadow (per segnale soft) entro limiti fissi; mai sugli scarti HARD
    "LEARN_SHADOW_MIN_N": 15,         # esiti minimi prima di toccare un segnale
    "LEARN_SHADOW_WINDOW": 50,        # esiti recenti considerati per segnale
    "LEARN_STEP_SEC": 3600,           # al massimo un passo all'ora per segnale
    "LEARN_PEN_STEP": 0.01,
    "LEARN_SIZE_STEP": 0.05,
    "LEARN_LOOSEN_AVG": 5.0,          # allenta solo se media netta >= +5%,
    "LEARN_LOOSEN_WIN": 0.45,         # win rate >= 45% (rug contati 3 volte)
    "LEARN_LOOSEN_RUG": 0.10,         # e rug/-50% <= 10%
    "LEARN_TIGHTEN_RUG": 0.20,        # stringe se media < 0 o rug/-50% > 20%
    "LEARN_THR_STEP": 0.005,          # soglia: -0.005 solo se i quasi-promossi sono chiaramente in utile
    "LEARN_THR_WIN": 0.50,
    # Analyst: momento e grafico
    "REQUIRE_CHART": False,           # (orig. True) senza grafico: niente scarto, penalita sul punteggio
    "NO_CHART_NEUTRAL": 0.5,          # nuovo: valore neutro dei segnali di grafico quando manca il grafico
    "NO_CHART_PENALTY": 0.05,         # nuovo: penalita sul punteggio quando manca il grafico
    "RSI_MIN": 45.0,                  # (orig. 50.0)
    "RSI_MAX": 80.0,                  # (orig. 78.0)
    "MAX_CHANGE_H1_PCT": None,        # (orig. 200.0) nessun tetto ai rialzi in 1h: decidono momentum e sicurezza
    "MIN_BUYSELL_M5": 1.3,            # (v4 1.2) acquisti/vendite negli ultimi 5 min almeno 1.3
    "VOL_ACCEL_MIN": 1.0,             # volume 5 min > 5 min precedenti (candele); senza candele > volume 1h / 12
    "REQUIRE_ABOVE_VWAP5": True,      # nuovo: con le candele il prezzo deve stare sopra la VWAP dei 5 min
    "MIN_CHANGE_H1_PCT": -15.0,       # (orig. -10.0)
    "MIN_CHANGE_M5_PCT": 0.0,         # v8.4f (review Claude c): niente acquisti col prezzo in calo a 5 min (prima -8)
    # apprendimento
    "SCORE_THRESHOLD": 0.50,          # (orig. 0.60)
    "THRESHOLD_MIN": 0.50,
    "THRESHOLD_MAX": 0.85,
    "LEARN_MIN_SAMPLES": 25,
    "LEARN_RATE": 0.05,
    "SHADOW_MIN": 60,                 # dopo quanto ricontrolla i token scartati
    "MISSED_WINNER_PCT": 50.0,
    # tempi
    "WATCH_TTL_MIN": 120,
    "WATCH_MAX": 2000,
    "RECHECK_SEC": 60,
    "RISK_CHECK_SEC": 15,
    "RECHECK_SECURITY_MIN": 10,
    "COOLDOWN_MIN": 120,
    "RECAP_HOUR": 8,
    "TIMEZONE": "Europe/Rome",
    # live e dry
    "RPC_URL": "https://api.mainnet-beta.solana.com",
    "JUP_BASE": "https://lite-api.jup.ag/swap/v1",
    "SLIPPAGE_BPS": 300,
    "PANIC_SLIPPAGE_BPS": 1500,
    "MAX_PRICE_IMPACT_PCT": 3.0,
    "PRIORITY_MAX_LAMPORTS": 1000000,
    "SOL_RESERVE": 0.03,              # SOL lasciati sempre per le commissioni
    "MAX_SOL_PER_TRADE": 0.5,
    # ---- v6: simulazione realistica del live (solo paper; nessuna transazione viene mai inviata)
    "SIM_REALISTIC": True,            # paper con preventivi Jupiter, latenza, fallimenti e fee dinamiche
    "SIM_USE_QUOTES": True,           # fill dai preventivi reali Jupiter (se falliscono: formula v5)
    "SIM_LATENCY_MIN_SEC": 1.0,       # ritardo simulato tra segnale e fill...
    "SIM_LATENCY_MAX_SEC": 3.0,       # ...il prezzo e quello del preventivo DOPO il ritardo
    "SIM_FAIL_PROB_BUY": 0.03,        # probabilita che una transazione d'acquisto fallisca
    "SIM_FAIL_PROB_SELL": 0.05,       # ...di vendita normale
    "SIM_FAIL_PROB_SELL_FAST": 0.15,  # ...di vendita durante un crollo (stop, crash, rug)
    "SIM_BUY_RETRIES": 1,             # tentativi extra d'acquisto dopo un fallimento
    "SIM_QUOTE_EXTRA_FEE_PCT": 0.3,   # margine prudente per lato sui preventivi (MEV, deriva del percorso)
    "SIM_BASE_FEE_LAMPORTS": 5000,    # commissione base Solana per firma
    "SIM_ATA_RENT_SOL": 0.00204,      # affitto conto token per posizione (bloccato finche aperta)
    "SIM_SEED": None,                 # seme del caso (None = casuale), utile per i test
    "SELLCHECK_MAX_RT_PCT": 15.0,     # prima di comprare: se rivendere subito costa oltre il 15% -> scarto
    "SELL_NOROUTE_WRITE_OFF": 20,     # dopo 20 vendite senza percorso Jupiter la posizione vale 0 (paper)
    "MARK_QUOTE_SEC": 60,             # valore delle posizioni dal preventivo di vendita reale ogni 60 s
    "MARK_NOROUTE_CONFIRM": 3,        # v8.5: valore ~0 solo dopo 3 preventivi 'no_route' DI FILA (uno solo = dato mancante)
    # priority fee dinamica da getRecentPrioritizationFees (RPC pubblico gratuito)
    "PRIO_DYNAMIC": True,
    "PRIO_REFRESH_SEC": 180,
    "PRIO_PERCENTILE": 75,            # vendita/acquisto normale
    "PRIO_PERCENTILE_URGENT": 90,     # vendite in crollo
    "PRIO_URGENT_MULT": 1.5,
    "SWAP_COMPUTE_UNITS": 300000,     # unita di calcolo stimate per uno swap
    "PRIO_FLOOR_LAMPORTS": 100000,    # minimo 0.0001 SOL di priority (tetto: PRIORITY_MAX_LAMPORTS)
    "PRIO_ACCOUNTS": ["pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",   # PumpSwap AMM
                      "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"],  # Raydium AMM v4
    # ---- v6: limiti per il futuro live (inerti in paper, nessuno invia fondi)
    "LIVE_MAX_TRADE_USD": 5.0,        # primo live: operazioni minuscole
    "MAX_TOTAL_LOSS_PCT": -30.0,      # perdita totale massima: crea il file HARD_STOP e blocca gli acquisti
    "LIVE_CONFIRM_TIMEOUT_SEC": 60,
    "LIVE_USE_JITO": False,           # non implementato: da fare prima del live
    "LIVE_JITO_TIP_SOL": 0.0001,
    "LIVE_DRY_RUN_DONE": False,       # da mettere a true solo dopo un dry run riuscito
}


def _load_env():
    env = {}
    path = os.path.join(DATA_DIR, ".env")
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
    return env


ENV = _load_env()
_cfg_path = os.path.join(DATA_DIR, "config.json")
if os.path.exists(_cfg_path):
    try:
        with open(_cfg_path) as _f:
            CFG.update(json.load(_f))
    except Exception as _e:  # noqa: BLE001
        print(f"config.json non valido, uso i valori predefiniti: {_e}")
for _k in ("RPC_URL", "JUP_BASE"):
    if ENV.get(_k):
        CFG[_k] = ENV[_k]
CFG["MODE"] = str(CFG["MODE"]).lower()
TZ = ZoneInfo(CFG["TIMEZONE"])
# (orig. geckoterminal 2.2 s, rugcheck 1.2 s)
HOST_GAPS = {"api.dexscreener.com": 0.25, "api.geckoterminal.com": 6.0, "frontend-api-v3.pump.fun": 5.0, "api.rugcheck.xyz": 2.5,
             "lite-api.jup.ag": 1.1, "api.jup.ag": 1.1, "api.mainnet-beta.solana.com": 0.5,
             "solana-rpc.publicnode.com": 0.05}  # v6: Jupiter e RPC
HARD_STOP_PATH = os.path.join(DATA_DIR, "HARD_STOP")
# v8.5: transazioni live inviate con esito ancora sconosciuto (firma, mint, lato). Finche c'e una voce non risolta
# nessun nuovo acquisto: si controlla su Solscan e si cancella il file (come HARD_STOP).
PENDING_TX_PATH = os.path.join(DATA_DIR, "live_pending_tx.json")


def pending_tx_load():
    try:
        with open(PENDING_TX_PATH) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:  # noqa: BLE001
        return {"_illeggibile": {"status": "unknown", "side": "?", "mint": "?"}}   # file rovinato: mai 'nessuna'


def pending_tx_save(d):
    if not d:
        try:
            os.remove(PENDING_TX_PATH)
        except FileNotFoundError:
            pass
        return
    tmp = PENDING_TX_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, PENDING_TX_PATH)

# v7: moduli opzionali in sources/ (solo lettura). Se mancano il bot gira come prima, senza bonus.
SRC = {}
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from sources import fomo_trending as _fomo, kol_holders as _kol, kol_theses_reader as _theses, \
        momentum as _mom  # noqa: E402
    _kol.CALL_GAP = max(getattr(_kol, "CALL_GAP", 0.15), 0.5)   # RPC pubblica condivisa: piano
    SRC = {"fomo": _fomo, "kol": _kol, "theses": _theses, "mom": _mom}
    try:  # v8.2
        from sources import cluster_v2 as _cl2, honeypot as _hp  # noqa: E402
        SRC["cluster2"], SRC["honeypot"] = _cl2, _hp
    except Exception as _e2:  # noqa: BLE001
        print(f"v8.2: cluster_v2/honeypot non caricati ({type(_e2).__name__}: {_e2})")
    try:  # v8.5s: lista smart money di Kiwi (SHADOW, solo log)
        from sources import smart_wallets as _sw  # noqa: E402
        SRC["smart"] = _sw
    except Exception as _e3:  # noqa: BLE001
        print(f"v8.5s: smart_wallets non caricato ({type(_e3).__name__}: {_e3})")
    try:  # v8.8: call Discord NKT (SHADOW, solo tag: nessuna rete, nessun peso nello score)
        from sources import discord_calls as _dc  # noqa: E402
        SRC["discord"] = _dc
    except Exception as _e4:  # noqa: BLE001
        print(f"v8.8: discord_calls non caricato ({type(_e4).__name__}: {_e4})")
except Exception as _e:  # noqa: BLE001
    SRC = {"error": f"{type(_e).__name__}: {_e}"[:160]}
try:
    from sources.solkeys import on_curve, DEAD_ADDRS  # noqa: E402  v8.4f
except Exception:  # noqa: BLE001
    on_curve, DEAD_ADDRS = (lambda a: None), frozenset()
try:
    from sources import gmgn_trending as _gmgn  # noqa: E402
    SRC["gmgn"] = _gmgn
except Exception:  # noqa: BLE001
    pass
try:  # v8.4: Birdeye (piano gratuito; chiave solo da os.environ, mai su file o log)
    from sources import birdeye as _be  # noqa: E402
    SRC["be"] = _be
except Exception as _e4:  # noqa: BLE001
    print(f"v8.4: birdeye non caricato ({type(_e4).__name__})")
try:  # v8.4: notifiche Telegram (token solo da os.environ, mai su file o log)
    from sources import notify_telegram as _tg  # noqa: E402
    SRC["tg"] = _tg
except Exception as _e5:  # noqa: BLE001
    print(f"v8.4: telegram non caricato ({type(_e5).__name__})")
try:  # v8.4: GoPlus Security (API pubblica senza chiave)
    from sources import goplus as _gp  # noqa: E402
    SRC["goplus"] = _gp
except Exception as _e6:  # noqa: BLE001
    print(f"v8.4: goplus non caricato ({type(_e6).__name__})")
try:  # v8.4e: storico del creatore (API pubblica pump.fun, cache 12 h, niente Helius)
    from sources import creator_history as _crh  # noqa: E402
    SRC["crh"] = _crh
except Exception as _e7:  # noqa: BLE001
    print(f"v8.4e: creator_history non caricato ({type(_e7).__name__})")
try:  # v8.7 (E): feature dei primi scambi pump.fun (solo shadow, API pubblica, niente Helius)
    from sources import smart_trades as _stx  # noqa: E402
    SRC["stx"] = _stx
except Exception as _e87:  # noqa: BLE001
    print(f"v8.7: smart_trades non caricato ({type(_e87).__name__})")
# v8.7 (F): prior pre-addestrato offline sul corpus Slinky21 (docs/analysis/pretrain_slinky.py). Solo p_prior nei
# campi shadow: NESSUN effetto su score, soglie o Learner. Calibrazione spostata (base 0,25% train vs 10% test):
# va letto come ranking, non come probabilita.
MODEL_PRIOR = {}
try:
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources", "model_prior.json")) as _fh:
        _mp = json.load(_fh)
    if isinstance(_mp.get("w"), dict):
        MODEL_PRIOR = {"w": {k: float(v) for k, v in _mp["w"].items()}, "b": float(_mp.get("b") or 0.0)}
except Exception as _e88:  # noqa: BLE001
    print(f"v8.7: model_prior non caricato ({type(_e88).__name__})")
PRIOR_LAST = {}   # mint -> p_prior dell'ultima valutazione dell'Analyst
STX_STATE = {"t": [], "busy": set()}


def prior_prob(f):
    """v8.7 (F): sigmoid(b + sum w*f) sulle 12 feature dell'Analyst. None se il prior manca."""
    if not MODEL_PRIOR:
        return None
    try:
        z = MODEL_PRIOR["b"] + sum(w * float(f.get(k, 0.0) or 0.0) for k, w in MODEL_PRIOR["w"].items())
        z = max(-40.0, min(40.0, z))
        return round(1.0 / (1.0 + math.exp(-z)), 5)
    except Exception:  # noqa: BLE001
        return None


def prior_note(mint, f):
    """v8.7: memorizza p_prior per entry_features (cache limitata)."""
    p = prior_prob(f)
    if p is not None:
        PRIOR_LAST[mint] = p
        if len(PRIOR_LAST) > 3000:
            for k in list(PRIOR_LAST)[:1000]:
                PRIOR_LAST.pop(k, None)
    return p


try:
    from sources import project_check as _proj  # noqa: E402  (v8)
    SRC["proj"] = _proj
except Exception:  # noqa: BLE001
    pass
try:
    from sources import helius as _helius  # noqa: E402  (v8.2: Helius come RPC principale, chiave solo da env)
    SRC["helius"] = _helius
    _helius.load(os.path.join(DATA_DIR, "helius_usage.json"))
except Exception:  # noqa: BLE001
    pass
PUBLIC_RPC_HOSTS = ("api.mainnet-beta.solana.com", "solana-rpc.publicnode.com")
HELIUS_CORE = ("getProgramAccounts", "getTokenAccounts", "getAccountInfo", "getTokenSupply", "getTokenLargestAccounts",
               "getSlot", "getBalance", "getTokenAccountBalance", "getRecentPrioritizationFees")


async def helius_first(sess, body, tier="core"):
    """v8.2: prova Helius (se c'e la chiave e il budget lo consente); None = usa l'RPC pubblico."""
    H = SRC.get("helius")
    if not H or sess is None or not isinstance(body, dict) or not body.get("jsonrpc"):
        return None
    m = str(body.get("method") or "")
    if m in HELIUS_CORE or m.startswith("getProgramAccounts"):
        tier = "core"
    if tier == "bulk" and H.wait_estimate(m) > 0.5:
        return None   # Helius gia occupato: questa chiamata va sull'RPC pubblico (piu velocita totale)
    if not H.allow(m, tier):
        return None
    await H.acquire(m)
    st_, d = 0, None
    try:
        async with sess.post(H.url(), json=body) as r:
            st_ = r.status
            if st_ == 200:
                d = await r.json(content_type=None)
    except Exception:  # noqa: BLE001
        d = None
    ok = isinstance(d, dict) and "result" in d
    H.note(m, st_, ok)
    return d if ok else None


try:
    from sources import trending_agg as _tagg, dex_prefilter as _pf  # noqa: E402  (v8.3)
    SRC["tagg"], SRC["pf"] = _tagg, _pf
except Exception:  # noqa: BLE001
    pass
TREND_INFO = {}   # v8.3: mint -> ultimo risultato dell'aggregatore trending (score, fonti, rank)
LAST_SRC = {}     # v8.3: mint -> fonte che l'ha messo in watchlist (registrata negli scarti)


def gmgn_personal_key():
    """v7: chiave GMGN PERSONALE (env GMGN_API_KEY, o ~/.config/desk/gmgn_key / gmgn_api_key). La chiave pubblica
    demo condivisa NON va mai usata in produzione: se e quella (o manca) ritorna None. Mai scritta nei log."""
    k = (os.environ.get("GMGN_API_KEY") or "").strip()
    if not k:
        for name in ("gmgn_key", "gmgn_api_key"):
            try:
                k = open(os.path.join(os.path.expanduser("~"), ".config", "desk", name)).read().strip()
            except OSError:
                k = ""
            if k:
                break
    pub = getattr(SRC.get("gmgn"), "PUBLIC_KEY", "gmgn_solbscbaseethmonadtron")
    return k if k and k != pub else None
# FIX: modalita senza terminale interattivo (nohup/servizio): --headless o DESK_HEADLESS=1
HEADLESS = "--headless" in sys.argv or os.environ.get("DESK_HEADLESS") == "1" or not sys.stdout.isatty()
# FIX: un solo lock per acquisti, vendite e aggiornamento della cassa (tutto gira in asyncio)
TRADE_LOCK = asyncio.Lock()


def stop_active():
    return os.path.exists(STOP_PATH)


def safe_div(a, b, default=0.0):
    try:
        return a / b if b else default
    except (TypeError, ZeroDivisionError):
        return default


def fnum(x, default=0.0):
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def sigmoid(x):
    return 1.0 / (1.0 + math.exp(-clamp(x, -30, 30)))


# ======================================================================= modello che impara
FEATURES = ["trend", "macd", "rsi", "bsr", "volsurge", "higher_lows", "social",
            "trending", "narrative", "holders", "concentration", "liquidity"]
CHART_FEATURES = ("trend", "macd", "rsi", "volsurge", "higher_lows")
RULE_W = {"trend": .15, "macd": .10, "rsi": .10, "bsr": .12, "volsurge": .08, "higher_lows": .07,
          "social": .08, "trending": .07, "narrative": .05, "holders": .06, "concentration": .07,
          "liquidity": .05}


class Model:
    """Regressione logistica che impara dagli esiti dei trade e dei token scartati."""

    def __init__(self, d=None):
        d = d or {}
        self.w = {k: fnum((d.get("w") or {}).get(k)) for k in FEATURES}
        self.b = fnum(d.get("b"))
        self.n = int(fnum(d.get("n")))

    def prob(self, f):
        return sigmoid(self.b + sum(self.w[k] * f.get(k, 0.0) for k in FEATURES))

    def update(self, f, y):
        err = self.prob(f) - y
        lr = CFG["LEARN_RATE"]
        for k in FEATURES:
            self.w[k] -= lr * err * f.get(k, 0.0)
        self.b -= lr * err
        self.n += 1

    def to_json(self):
        return {"w": self.w, "b": self.b, "n": self.n}


def rule_score(f):
    return sum(RULE_W[k] * f.get(k, 0.0) for k in FEATURES)


def final_score(st, f):
    r = rule_score(f)
    if st.model.n < CFG["LEARN_MIN_SAMPLES"]:
        return r, r, None
    p = st.model.prob(f)
    return 0.5 * r + 0.5 * p, r, p


# ======================================================================= stato
class State:
    def __init__(self):
        self.mode = CFG["MODE"]
        self.cash = CFG["START_BALANCE"]
        self.positions = {}
        self.period_start_ts = time.time()
        self.period_start_equity = CFG["START_BALANCE"]
        self.closed = []
        self.cooldown = {}
        self.stats = Counter()
        self.reject_reasons = Counter()
        self.paused = False
        self.day_key = datetime.now(TZ).strftime("%Y-%m-%d")  # giornata Europe/Rome dello stop giornaliero
        self.day_start_equity = CFG["START_BALANCE"]
        self.model = Model()
        self.threshold = CFG["SCORE_THRESHOLD"]
        self.outcomes = deque(maxlen=20)
        self.lessons = deque(maxlen=30)
        self.blacklist = set()
        self.equity_hist = deque(maxlen=3000)
        self.total_start = CFG["START_BALANCE"]   # v6: base del limite di perdita totale
        self.hard_tripped = False
        self.learner_shadow = {}   # v6: statistiche degli esiti per segnale soft + correzioni apprese
        self.shadow_trades = {}    # v6: operazioni ipotetiche aperte (nessun denaro)
        self.learner_bonus = {}    # v7: esiti per segnale/bonus + correzioni apprese
        self.labels = dict(CFG.get("LABELLED_CASES") or {})  # v7: casi etichettati (mint -> etichetta)
        self.post_exit = {}        # v8.1: token seguiti per POST_EXIT_MIN dopo l'uscita (rimbalzi dopo lo stop)
        self.label_log = []        # v8.2: storia delle etichette (casi seminati, promozioni a 'rug')
        self.label_watch = {}      # v8.2: massimo di prezzo/liquidita dei token etichettati sospetti
        # non salvati
        self.fomo_rank = {}
        self.watch = {}
        self.processed = {}
        self.shadow = {}
        self.trending = set()
        self.hot = deque(maxlen=3000)
        self.activity = deque(maxlen=10)
        self.sec_checked = {}
        self.cl_checked = {}       # v8.2: ultimo ricontrollo cluster delle posizioni aperte

    def to_json(self):
        return {
            "mode": self.mode, "cash": self.cash, "positions": self.positions,
            "period_start_ts": self.period_start_ts, "period_start_equity": self.period_start_equity,
            "closed": self.closed, "cooldown": self.cooldown, "stats": dict(self.stats),
            "reject_reasons": dict(self.reject_reasons), "paused": self.paused,
            "day_key": self.day_key, "day_start_equity": self.day_start_equity,
            "model": self.model.to_json(), "threshold": self.threshold,
            "threshold_base": CFG["SCORE_THRESHOLD"],
            "outcomes": list(self.outcomes), "lessons": list(self.lessons),
            "blacklist": sorted(self.blacklist), "equity_hist": list(self.equity_hist)[-1000:],
            "total_start": self.total_start, "hard_tripped": self.hard_tripped,
            "learner_shadow": self.learner_shadow, "shadow_trades": self.shadow_trades,
            "learner_bonus": self.learner_bonus, "labels": self.labels, "post_exit": self.post_exit,
            "label_log": self.label_log[-200:], "label_watch": self.label_watch,
        }


def load_state():
    st = State()
    if not os.path.exists(STATE_PATH):
        return st
    try:
        with open(STATE_PATH) as f:
            d = json.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"state.json illeggibile, riparto da zero: {e}")
        return st
    # cio che si impara resta anche cambiando modalita
    st.model = Model(d.get("model"))
    st.threshold = fnum(d.get("threshold"), CFG["SCORE_THRESHOLD"])
    # se la soglia di partenza in CFG e cambiata (es. 0.60 -> 0.50) la soglia salvata riparte da quella nuova
    base = fnum(d.get("threshold_base"), 0.60)
    if abs(base - CFG["SCORE_THRESHOLD"]) > 1e-9:
        print(f"Soglia di partenza cambiata {base:.2f} -> {CFG['SCORE_THRESHOLD']:.2f}: "
              f"soglia {st.threshold:.3f} -> {CFG['SCORE_THRESHOLD']:.3f}")
        st.threshold = CFG["SCORE_THRESHOLD"]
    st.threshold = clamp(st.threshold, CFG["THRESHOLD_MIN"], CFG["THRESHOLD_MAX"])
    st.outcomes.extend(d.get("outcomes", []))
    st.lessons.extend(d.get("lessons", []))
    st.blacklist = set(d.get("blacklist", []))
    ls = d.get("learner_shadow")
    st.learner_shadow = ls if isinstance(ls, dict) else {}
    for old, new in (("lp", "lp_unlocked"), ("lp_grave", "lp_unlocked_grave")):  # v7: nome nuovo del segnale LP
        if old in st.learner_shadow and new not in st.learner_shadow:
            st.learner_shadow[new] = st.learner_shadow.pop(old)
    lb = d.get("learner_bonus")
    st.learner_bonus = lb if isinstance(lb, dict) else {}
    pe = d.get("post_exit")
    st.post_exit = pe if isinstance(pe, dict) else {}
    lab = d.get("labels")
    if isinstance(lab, dict):
        st.labels.update(lab)
    if isinstance(d.get("label_log"), list):
        st.label_log = d["label_log"]
    if isinstance(d.get("label_watch"), dict):
        st.label_watch = d["label_watch"]
    if d.get("mode", "paper") != CFG["MODE"]:
        print(f"Modalita cambiata da {d.get('mode')} a {CFG['MODE']}: saldo e posizioni ripartono da zero, "
              f"quanto appreso resta.")
        return st
    st.cash = fnum(d.get("cash"), st.cash)
    st.positions = d.get("positions", {})
    st.period_start_ts = fnum(d.get("period_start_ts"), st.period_start_ts)
    st.period_start_equity = fnum(d.get("period_start_equity"), st.period_start_equity)
    st.closed = d.get("closed", [])
    st.cooldown = d.get("cooldown", {})
    st.stats = Counter(d.get("stats", {}))
    st.reject_reasons = Counter(d.get("reject_reasons", {}))
    st.paused = bool(d.get("paused", False))
    st.day_key = str(d.get("day_key") or st.day_key)
    st.day_start_equity = fnum(d.get("day_start_equity"), st.period_start_equity)
    st.equity_hist.extend(tuple(x) for x in d.get("equity_hist", []))
    st.total_start = fnum(d.get("total_start"), st.period_start_equity or CFG["START_BALANCE"])
    st.hard_tripped = bool(d.get("hard_tripped", False))
    sh = d.get("shadow_trades")
    st.shadow_trades = sh if isinstance(sh, dict) else {}
    for v in st.shadow_trades.values():  # dopo un riavvio il "nessun prezzo da 15 min" riparte da adesso
        v["last_ok"] = time.time()
    return st


_last_save = [0.0]


def save_state(st, force=False):
    if not force and time.time() - _last_save[0] < 5:
        return
    _last_save[0] = time.time()
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st.to_json(), f)
    os.replace(tmp, STATE_PATH)


# ======================================================================= Logger
def write_event(agent, kind, msg, data=None):
    rec = {"ts": datetime.now(TZ).isoformat(timespec="seconds"), "agent": agent, "kind": kind, "msg": msg}
    if data:
        rec.update(data)
    line = json.dumps(rec, ensure_ascii=False)
    if "api-key" in line.lower() and SRC.get("helius"):   # v8.2: mai la chiave Helius nei file
        line = SRC["helius"].mask(line)
    with open(os.path.join(DATA_DIR, "events.jsonl"), "a") as f:
        f.write(line + "\n")


def log(st, agent, kind, msg, **data):
    if "api-key" in str(msg).lower() and SRC.get("helius"):
        msg = SRC["helius"].mask(msg)
    st.activity.append(f"{datetime.now(TZ):%H:%M} {agent}: {msg}"[:110])
    if HEADLESS:
        print(f"{datetime.now(TZ):%H:%M:%S} {agent}: {msg}", flush=True)
    write_event(agent, kind, msg, data)


def lesson(st, msg):
    st.lessons.append(f"{datetime.now(TZ):%d/%m %H:%M} {msg}")
    log(st, "Learner", "lesson", msg)


TG_REF = [None]   # v8.4: notifier Telegram (None = spento). Mai bloccante, errori silenziosi e mascherati.


def tg_send(text):
    n = TG_REF[0]
    if n is not None:
        try:
            return bool(n.send(text))
        except Exception:  # noqa: BLE001
            pass
    return False


def tg_group(key, line, every=600, title=None):
    n = TG_REF[0]
    if n is not None:
        try:
            n.group(key, line, every=every, title=title)
        except Exception:  # noqa: BLE001
            pass


def discord_tag(mint, now=None):
    """v8.8: {'discord_call': 1, 'discord_call_age_min': x} se il mint e' stato chiamato in NKT, altrimenti {}.
    SOLO registrato (eventi, entry_features, shadow): zero peso, zero chiamate di rete. Mai eccezioni."""
    try:
        if CFG.get("DISCORD_CALLS_MODE", "shadow") == "off" or not isinstance(SRC, dict) or not SRC.get("discord"):
            return {}
        return SRC["discord"].info(mint, now)
    except Exception:  # noqa: BLE001
        return {}


def reject(st, mint, sym, reason, agent="Filter", cat=None, quiet=False):
    if cat in ("cluster", "honeypot"):   # v8.4: rifiuti hard (token gia passati dal prefiltro), max 1 msg / 10 min
        tg_group("hard", f"{sym} ({str(mint)[:6]}..): {cat}: {str(reason)[:100]}", 600,
                 "Rifiuti hard cluster/honeypot (token passati dal prefiltro):")
    st.reject_reasons[cat or reason] += 1
    st.stats["rejected"] += 1
    msg = f"{sym} scartato: {reason}"
    ex = {"mint": mint, "src": LAST_SRC.get(mint)}   # v8.3: fonte del candidato
    ex.update(discord_tag(mint))   # v8.8: tag call NKT (solo log)
    if quiet:
        write_event(agent, "reject", msg, ex)
    else:
        log(st, agent, "reject", msg, **ex)


def csv_migrate(path, fields):
    """v8.1: se l'intestazione e diversa ma tutte le colonne vecchie esistono ancora, riscrive il file con le colonne
    nuove (celle vuote nelle righe vecchie): il CSV resta uno e leggibile. Altrimenti il vecchio va messo da parte."""
    if not os.path.exists(path):
        return
    with open(path, newline="") as f:
        rows = list(csv.reader(f))
    head = rows[0] if rows else []
    if head == fields:
        return
    if head and set(head) <= set(fields):
        tmp = path + ".tmp"
        with open(tmp, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(fields)
            for r in rows[1:]:
                d = dict(zip(head, r))
                w.writerow([d.get(k, "") for k in fields])
        os.replace(tmp, path)
    else:
        base, ext = os.path.splitext(path)
        os.replace(path, f"{base}_old_{int(time.time())}{ext}")


def write_trade(t):
    path = os.path.join(DATA_DIR, "trades.csv")
    fields = list(t.keys())
    csv_migrate(path, fields)  # v8.1 (prima: rotazione in trades_old_*.csv)
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerow(t)


# ======================================================================= rete
class Http:
    """Richieste con limite di frequenza per sito, cosi non veniamo bloccati."""

    def __init__(self, session):
        self.s = session
        self.locks = {}
        self.last = {}
        self.last429 = {}   # v8.1: host -> ora dell'ultimo 429

    async def _wait(self, url):
        host = urlparse(url).netloc
        gap = HOST_GAPS.get(host, 0.15)
        lock = self.locks.setdefault(host, asyncio.Lock())
        async with lock:
            dt = time.time() - self.last.get(host, 0.0)
            if dt < gap:
                await asyncio.sleep(gap - dt)
            self.last[host] = time.time()

    async def get(self, url, headers=None):
        await self._wait(url)
        try:
            async with self.s.get(url, headers=headers) as r:
                if r.status == 429:
                    self.last429[urlparse(url).netloc] = time.time()
                    await asyncio.sleep(20)
                    return None
                if r.status != 200:
                    return None
                return await r.json(content_type=None)
        except Exception:  # noqa: BLE001
            return None

    async def post(self, url, body, headers=None, tier="core"):
        if urlparse(url).netloc in PUBLIC_RPC_HOSTS:   # v8.2: Helius prima, pubblico come ripiego
            d = await helius_first(getattr(self, "s", None), body, tier)
            if d is not None:
                return d
        await self._wait(url)
        try:
            async with self.s.post(url, json=body, headers=headers) as r:
                if r.status == 429:
                    await asyncio.sleep(5)
                    return None
                return json.loads(await r.text())
        except Exception:  # noqa: BLE001
            return None


# ======================================================================= dati di mercato
async def dex_batch(http, mints):
    out = {}
    for i in range(0, len(mints), 30):
        chunk = mints[i:i + 30]
        data = await http.get("https://api.dexscreener.com/tokens/v1/solana/" + ",".join(chunk))
        if isinstance(data, list):
            pairs = data
        elif isinstance(data, dict):
            pairs = data.get("pairs") or []
        else:
            pairs = []
        for pair in pairs:
            if isinstance(pair, dict):
                base = (pair.get("baseToken") or {}).get("address")
                if base in chunk:
                    out.setdefault(base, []).append(pair)
    try:
        tick_record(out)
    except Exception:  # noqa: BLE001
        pass
    return out


# ----------------------------------------------------------------------- v8.1: tick e candele in cache
TICKS = {}          # mint -> deque[(ts, prezzo, liquidita)] dai controlli gia fatti (watch, Risk, shadow)
CANDLE_CACHE = {}   # pool -> (ts, righe) ultime candele 1m lette (anche se meno di 30)
OHLCV_EXTRA = deque(maxlen=20)  # orari delle richieste OHLCV in piu (shadow senza candele)


def tick_record(market, now=None):
    now = now or time.time()
    for mint, pairs in market.items():
        main = max(pairs, key=lambda p: fnum((p.get("liquidity") or {}).get("usd")))
        px, lq = fnum(main.get("priceUsd")), fnum((main.get("liquidity") or {}).get("usd"))
        if px > 0:
            dq = TICKS.setdefault(mint, deque(maxlen=40))
            if not dq or now - dq[-1][0] >= 5:
                dq.append((now, px, lq))
    if len(TICKS) > 4000:
        for k in sorted(TICKS, key=lambda k: TICKS[k][-1][0] if TICKS[k] else 0)[:1000]:
            TICKS.pop(k, None)


def summarize(pairs):
    vol_h1 = sum(fnum((p.get("volume") or {}).get("h1")) for p in pairs)
    main = max(pairs, key=lambda p: (fnum((p.get("liquidity") or {}).get("usd")),
                                     fnum((p.get("volume") or {}).get("h1"))))
    tx = (main.get("txns") or {}).get("h1") or {}
    tx5 = (main.get("txns") or {}).get("m5") or {}
    vol = main.get("volume") or {}
    chg = main.get("priceChange") or {}
    base = main.get("baseToken") or {}
    info = main.get("info") or {}
    socials = {str(s.get("type", "")).lower() for s in (info.get("socials") or []) if isinstance(s, dict)}
    created = fnum(main.get("pairCreatedAt")) / 1000
    return {
        "vol_h1": vol_h1, "price": fnum(main.get("priceUsd")),
        "liq": fnum((main.get("liquidity") or {}).get("usd")),
        # v8.5: campo liquidita assente (opzionale su DexScreener) != liquidita 0: chi lo legge tiene il valore precedente
        "liq_known": (main.get("liquidity") or {}).get("usd") is not None,
        "dex": str(main.get("dexId", "?")), "pair": str(main.get("pairAddress", "")),
        "symbol": str(base.get("symbol", "?"))[:12], "name": str(base.get("name", ""))[:40],
        "buys_h1": int(fnum(tx.get("buys"))), "sells_h1": int(fnum(tx.get("sells"))),
        "buys_m5": int(fnum(tx5.get("buys"))), "sells_m5": int(fnum(tx5.get("sells"))),
        "vol_m5": fnum(vol.get("m5")), "vol_h1_main": fnum(vol.get("h1")),
        "chg_h1": fnum(chg.get("h1")), "chg_m5": fnum(chg.get("m5")),
        "mcap": fnum(main.get("marketCap") or main.get("fdv")), "url": str(main.get("url", "")),
        "pairs_all": [str(p.get("pairAddress")) for p in pairs if p.get("pairAddress")][:30],   # v8.5
        "twitter": bool(socials & {"twitter", "x"}), "telegram": "telegram" in socials,
        "website": bool(info.get("websites")),
        "age_min": (time.time() - created) / 60 if created else None,
        # v8: link completi (siti e social) per il controllo progetto e la coda delle analisi approfondite
        "links": {"websites": [str(w.get("url")) for w in (info.get("websites") or [])
                               if isinstance(w, dict) and w.get("url")][:4],
                  "socials": [{"type": str(x.get("type", "")).lower(), "url": str(x.get("url", ""))}
                              for x in (info.get("socials") or []) if isinstance(x, dict)][:6]},
    }


def gecko_mints(data):
    out = []
    items = data.get("data", []) if isinstance(data, dict) else []
    for p in items:
        rel = p.get("relationships") or {}
        bid = (((rel.get("base_token") or {}).get("data")) or {}).get("id") or ""
        if bid.startswith("solana_"):
            mint = bid.split("_", 1)[1]
        elif 32 <= len(bid) <= 44:
            mint = bid
        else:
            continue
        name = str((p.get("attributes") or {}).get("name", "?")).split("/")[0].strip()
        out.append((mint, name))
    return out


async def fetch_candles(http, pool):
    """Candele da 1 minuto (le ultime 100) da GeckoTerminal."""
    if not pool:
        return None
    url = (f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool}"
           f"/ohlcv/minute?aggregate=1&limit=100")
    d = await http.get(url, headers={"Accept": "application/json"})
    try:
        lst = d["data"]["attributes"]["ohlcv_list"]
    except Exception:  # noqa: BLE001
        return None
    rows = [tuple(fnum(v) for v in x[:6]) for x in lst if isinstance(x, (list, tuple)) and len(x) >= 6]
    rows.sort(key=lambda r: r[0])
    CANDLE_CACHE[pool] = (time.time(), rows)  # v8.1: anche poche candele servono per l'ATR di fallback
    if len(CANDLE_CACHE) > 500:
        for k in sorted(CANDLE_CACHE, key=lambda k: CANDLE_CACHE[k][0])[:100]:
            CANDLE_CACHE.pop(k, None)
    return rows if len(rows) >= 30 else None


def cached_candles(pool, max_age=120):
    e = CANDLE_CACHE.get(pool or "")
    return e[1] if e and time.time() - e[0] <= max_age else None


async def candles_for_entry(http, pool):
    """v8.1: candele per le feature d'ingresso. Prima la cache (nessuna chiamata); poi al massimo
    DYN_ATR_EXTRA_PER_MIN richieste in piu al minuto, mai se GeckoTerminal ha dato 429 negli ultimi 3 minuti."""
    rows = cached_candles(pool)
    if rows is not None or http is None or not pool:
        return rows
    now = time.time()
    if now - fnum(getattr(http, "last429", {}).get("api.geckoterminal.com")) < 180:
        return None
    if sum(1 for t in OHLCV_EXTRA if now - t < 60) >= int(CFG["DYN_ATR_EXTRA_PER_MIN"]):
        return None
    OHLCV_EXTRA.append(now)
    await fetch_candles(http, pool)
    return cached_candles(pool)


# ======================================================================= indicatori
def ema_series(vals, n):
    k = 2 / (n + 1)
    out, e = [], None
    for v in vals:
        e = v if e is None else v * k + e * (1 - k)
        out.append(e)
    return out


def rsi(vals, n=14):
    if len(vals) < n + 1:
        return None
    gains = sum(max(vals[i] - vals[i - 1], 0) for i in range(1, n + 1))
    losses = sum(max(vals[i - 1] - vals[i], 0) for i in range(1, n + 1))
    ag, al = gains / n, losses / n
    for i in range(n + 1, len(vals)):
        d = vals[i] - vals[i - 1]
        ag = (ag * (n - 1) + max(d, 0)) / n
        al = (al * (n - 1) + max(-d, 0)) / n
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


def analyze_chart(candles):
    closes = [c[4] for c in candles]
    vols = [c[5] for c in candles]
    lows = [c[3] for c in candles]
    e9, e21 = ema_series(closes, 9), ema_series(closes, 21)
    macd = [a - b for a, b in zip(ema_series(closes, 12), ema_series(closes, 26))]
    hist = [m - s for m, s in zip(macd, ema_series(macd, 9))]
    v5 = sum(vols[-5:]) / 5
    v20 = sum(vols[-25:-5]) / 20
    return {
        "close": closes[-1], "ema9": e9[-1], "ema21": e21[-1], "rsi": rsi(closes, 14),
        "macd_hist": hist[-1], "macd_rising": hist[-1] > hist[-2],
        "vol_ratio": v5 / v20 if v20 > 0 else 0.0,
        "higher_lows": min(lows[-5:]) > min(lows[-10:-5]),
    }


def window5(candles, now=None):
    """Dalle candele da 1 minuto: volume degli ultimi 5 min, dei 5 min precedenti e VWAP degli ultimi 5 min.
    Finestre per orario (i minuti senza scambi mancano dalle candele). None se le candele sono vecchie."""
    if not candles:
        return None
    now = now or time.time()
    last = candles[-1][0]
    if now - last > 600:  # candele vecchie: meglio il fallback DexScreener
        return None
    end = last + 60
    cur = [c for c in candles if end - 300 <= c[0] < end]
    prev = [c for c in candles if end - 600 <= c[0] < end - 300]
    vn, vp = sum(c[5] for c in cur), sum(c[5] for c in prev)
    pv = sum((c[2] + c[3] + c[4]) / 3 * c[5] for c in cur)
    return {"vol_now": vn, "vol_prev": vp, "vwap": pv / vn if vn > 0 else 0.0}


WORD_RE = re.compile(r"[a-z0-9]{3,}")


def words_of(*texts):
    return {w for t in texts for w in WORD_RE.findall(str(t).lower()) if w not in STOPWORDS}


def hot_words(st):
    cutoff = time.time() - 6 * 3600
    seen = {}
    for t, w, mint in st.hot:
        if t > cutoff:
            seen.setdefault(w, set()).add(mint)
    return {w for w, ms in seen.items() if len(ms) >= 3}


def build_features(st, mint, m, sec, ch):
    ratio = m["buys_h1"] / max(1, m["sells_h1"])
    f = {
        "trend": 1.0 if ch and ch["ema9"] > ch["ema21"] and ch["close"] >= ch["ema9"] else 0.0,
        "macd": ((1.0 if ch["macd_rising"] else 0.5) if ch["macd_hist"] > 0 else 0.0) if ch else 0.0,
        "rsi": 1.0 if ch and ch["rsi"] is not None and CFG["RSI_MIN"] <= ch["rsi"] <= CFG["RSI_MAX"] else 0.0,
        "bsr": clamp(ratio - 1.0),
        "volsurge": clamp((ch["vol_ratio"] - 1) / 2) if ch else 0.0,
        "higher_lows": 1.0 if ch and ch["higher_lows"] else 0.0,
        "social": (m["twitter"] + m["telegram"] + m["website"]) / 3,
        "trending": 1.0 if mint in st.trending else 0.0,
        "narrative": 1.0 if words_of(m["name"], m["symbol"]) & hot_words(st) else 0.0,
        "holders": clamp(math.log10(max(sec.get("holders", 1), 1)) / 4),
        "concentration": (clamp(1 - sec.get("top10", 25.0) / CFG["MAX_TOP10_PCT"]) if CFG["MAX_TOP10_PCT"] > 0
                          else (1.0 if sec.get("top10", 0.0) <= 0 else 0.0)),
        "liquidity": clamp((math.log10(max(m["liq"], 1)) - 4) / 1.5),
    }
    return {k: round(v, 3) for k, v in f.items()}


# ======================================================================= Scout
def add_watch(st, mint, sym, src, prio=False):
    """v8.3: prio=True (trending) -> analizzato per primo e mai sfrattato da una nuova pool. Le fonti secondarie
    (nuove pool, pump.fun) hanno un limite (WATCH_NEW_MAX): oltre, esce la piu vecchia non prioritaria."""
    if not mint or mint in SKIP_MINTS or mint in st.positions:
        return
    if mint in st.watch:
        if prio and not st.watch[mint].get("prio"):
            st.watch[mint]["prio"] = True   # gia osservato da un'altra fonte: ora e anche in trending
        return
    if mint in st.processed or st.cooldown.get(mint, 0) > time.time():
        return
    low = [k for k, v in st.watch.items() if not v.get("prio")]
    if len(st.watch) >= CFG["WATCH_MAX"] or (not prio and len(low) >= CFG["WATCH_NEW_MAX"]):
        pool = low or list(st.watch)
        oldest = min(pool, key=lambda k: st.watch[k]["seen"])
        w = st.watch.pop(oldest)
        reject(st, oldest, w["sym"], "watchlist piena", agent="Scout", cat="watchlist piena", quiet=True)
    st.watch[mint] = {"seen": time.time(), "sym": str(sym)[:12], "src": src, "prio": bool(prio)}
    LAST_SRC[mint] = src
    if len(LAST_SRC) > 20000:
        for k in list(LAST_SRC)[:5000]:
            LAST_SRC.pop(k, None)
    st.stats["seen"] += 1


async def scout_fomo(st):
    """v7: trending. Fonte principale GMGN (OpenAPI ufficiale) SOLO con chiave personale; senza chiave il sostituto
    gratuito (DexScreener boosts via fomo_trending, o la vera board Fomo se c'e FOMOAPI_KEY), bonus 0 al sostituto.
    La chiave pubblica demo di GMGN non viene mai usata. Nessun bypass dei filtri."""
    if "fomo" not in SRC and "gmgn" not in SRC:
        log(st, "Scout", "warn", f"fonte trending non disponibile: {SRC.get('error', 'modulo assente')}")
        while True:
            await asyncio.sleep(3600)
    last_src = None
    while True:
        try:
            res, key = None, gmgn_personal_key()
            if key and "gmgn" in SRC:
                SRC["gmgn"]._key = (lambda k=key: (k, "personal"))   # mai la chiave demo condivisa
                r = await SRC["gmgn"].aget_trending(25, allow_substitute=False)
                if r.get("source") == "gmgn_trending" and r.get("tokens"):
                    res = r
            if res is None and "fomo" in SRC:
                res = await SRC["fomo"].aget_trending(25)
            res = res or {"source": "none", "tokens": []}
            src = res.get("source", "none")
            now = time.time()
            for t in res.get("tokens") or []:
                mint = t.get("mint")
                if not mint:
                    continue
                st.fomo_rank[mint] = {"rank": t.get("rank"), "source": src, "t": now}
                add_watch(st, mint, t.get("symbol") or "?", src)
            for k in [k for k, v in st.fomo_rank.items() if now - v["t"] > 6 * 3600]:
                st.fomo_rank.pop(k, None)
            if src != last_src:
                note = {"gmgn_trending": " - GMGN con chiave personale, bonus GMGN_TREND_BONUS",
                        "fomo_trending": " - board Fomo (FOMOAPI_KEY), bonus FOMO_TREND_BONUS",
                        "dex_boosts_substitute": " - SOSTITUTO gratuito (nessuna chiave GMGN personale), nessun bonus"}
                log(st, "Scout", "info", f"fonte trending: {src} ({len(res.get('tokens') or [])} token)"
                    + note.get(src, " - nessun bonus"))
                last_src = src
        except Exception as e:  # noqa: BLE001
            log(st, "Scout", "warn", f"fonte trending: errore ({type(e).__name__})")
        await asyncio.sleep(max(120, CFG["FOMO_SCOUT_SEC"]))


async def _gmgn_rows():
    """v8.3: GMGN resta nel codice ma e INATTIVO senza chiave personale (oggi nessuna chiave)."""
    key = gmgn_personal_key()
    if not key or "gmgn" not in SRC:
        return []
    SRC["gmgn"]._key = (lambda k=key: (k, "personal"))
    r = await SRC["gmgn"].aget_trending(25, allow_substitute=False)
    if r.get("source") != "gmgn_trending":
        return []
    return [(t["mint"], int(t.get("rank") or i + 1), {"symbol": t.get("symbol")})
            for i, t in enumerate(r.get("tokens") or []) if t.get("mint")]


def pf_cfg(st):
    """v8.3: soglie effettive del prefiltro = CFG (codice + override) + passi del Learner, nei limiti."""
    d = (st.learner_bonus or {}).get("pf_d") or {}
    out = {}
    for k, (lo, hi) in SRC["pf"].LIMITS.items():
        out[k] = clamp(fnum(CFG.get(k, SRC["pf"].DEFAULTS[k])) + fnum(d.get(k)), lo, hi)
    return out


async def pf_learn(st, http, done):
    """v8.3: esito a 60 minuti di ogni valutazione del prefiltro (passati e scartati, prezzo DexScreener) e un passo
    all'ora sulla soglia che sbaglia di piu (allenta se scarta token che poi salgono, stringe se scarta peggio)."""
    lb = st.learner_bonus
    hist = lb.setdefault("pf_hist", [])
    pend = SRC["pf"].load_pending(done=done)[:150]
    if pend:
        market = await dex_batch(http, list({r["mint"] for r in pend}))
        for r in pend:
            done.add((r["mint"], r["t"]))
            pairs = market.get(r["mint"])
            px = summarize(pairs)["price"] if pairs else 0.0
            ret = (px / r["m"]["price"] - 1) * 100 if px > 0 else -100.0
            hist.append([r["fails"], round(max(-100.0, min(ret, 1000.0)), 2), r["t"]])
        del hist[:-600]
    now = time.time()
    if CFG["PF_LEARN"] and now - fnum(lb.get("pf_last")) >= CFG["LEARN_STEP_SEC"]:
        res = SRC["pf"].learn_step([(h[0], h[1]) for h in hist], pf_cfg(st))
        for k, (old, new, ac, nc, ap, npass) in res.items():
            dd = lb.setdefault("pf_d", {})
            dd[k] = round(fnum(dd.get(k)) + new - old, 6)
            lb["pf_last"] = now
            msg = (f"prefiltro {k} {fmtv(old)} -> {fmtv(new)} (scartati solo da questa soglia {ac:+.1f}% a 60 min su "
                   f"{nc}, passati {ap:+.1f}% su {npass})")
            st.lessons.append(f"{datetime.now(TZ):%d/%m %H:%M} {msg}")
            log(st, "Learner", "learn_prefilter", msg, key=k, old=old, new=new)


BE_REF = [None]   # v8.4: client Birdeye unico (limitatore 1 rps e contatore CU condivisi da tutti gli agenti)


def be_client(http):
    """v8.4: client Birdeye o None (modulo assente, spento, chiave assente, nessuna sessione HTTP)."""
    if "be" not in SRC or not CFG.get("BIRDEYE_ENABLED", True) or not SRC["be"].Birdeye.has_key():
        return None
    if BE_REF[0] is None:
        sess = getattr(http, "s", None)
        if sess is None:
            return None

        def _lg(msg):
            if ST_REF[0] is not None:
                log(ST_REF[0], "Birdeye", "info", SRC["be"].mask(msg))
        BE_REF[0] = SRC["be"].Birdeye(sess, CFG, _lg)
    return BE_REF[0]


async def scout_trending(st, http):
    """v8.3: FONTE PRINCIPALE. Aggregatore gratuito (DexScreener principale; GeckoTerminal 5m/1h/6h e pump.fun
    secondari; GMGN solo con chiave personale). I migliori TREND_WATCH_TOP per trending_score entrano in watchlist
    con priorita alta. Nessun filtro viene saltato."""
    be = be_client(http) if CFG.get("BIRDEYE_TREND", True) else None
    agg = SRC["tagg"].TrendingAgg(gmgn_fetch=_gmgn_rows, birdeye_fetch=(be.trending if be else None))
    done, last_note, last_pf = set(), 0.0, 0.0
    while True:
        try:
            toks = await agg.refresh(http.get, cooldown=lambda h: time.time() - fnum(http.last429.get(h)) < 180)
            now = time.time()
            gecko = set()
            for t in toks:
                TREND_INFO[t["mint"]] = {k: t[k] for k in ("trending_score", "families", "n_fam", "sources", "ranks",
                                                           "paid", "boost", "t")}
                if "gecko" in t["families"]:
                    gecko.add(t["mint"])
                br = [t["ranks"][s] for s in ("dex_boost_top", "dex_boost_latest") if s in t["ranks"]]
                if br:   # continuita con il bonus appreso FOMO_SUB_BONUS (src:dex_boosts_substitute)
                    st.fomo_rank[t["mint"]] = {"rank": min(br), "source": "dex_boosts_substitute", "t": now}
            if gecko:
                st.trending = gecko
            for t in toks[: int(CFG["TREND_WATCH_TOP"])]:
                add_watch(st, t["mint"], t["symbol"] or "?", "trending_agg", prio=True)
            for k in [k for k, v in TREND_INFO.items() if now - v["t"] > 6 * 3600]:
                TREND_INFO.pop(k, None)
            for k in [k for k, v in st.fomo_rank.items() if now - v["t"] > 6 * 3600]:
                st.fomo_rank.pop(k, None)
            if now - last_note > 1800:
                c = agg.last["counts"]
                log(st, "Scout", "info", "trending aggregato: " + ", ".join(f"{k} {v}" for k, v in c.items())
                    + f" -> {min(len(toks), int(CFG['TREND_WATCH_TOP']))} in watchlist con priorita",
                    counts=c)
                if be is not None:   # v8.4: consumo Birdeye
                    await be.sync_credits()
                    b = be.status()
                    log(st, "Birdeye", "info", f"consumo oggi {b['spent_today']:.0f}/{fnum(b['day_budget']):.0f} CU, "
                        f"residuo ciclo ~{b['remaining_est']} CU, livello {b['level']}, chiamate {b['calls']}, "
                        f"fallback {b['fallback']}", birdeye=b)
                last_note = now
            if "pf" in SRC and now - last_pf > 600:
                last_pf = now
                await pf_learn(st, http, done)
        except Exception as e:  # noqa: BLE001
            log(st, "Scout", "warn", f"trending aggregato: errore ({type(e).__name__})")
        await asyncio.sleep(max(60, CFG["TREND_SCOUT_SEC"]))


async def scout_pumpportal(st):
    url = "wss://pumpportal.fun/api/data"
    backoff = 5
    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20, max_size=2 ** 20) as ws:
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                await ws.send(json.dumps({"method": "subscribeMigration"}))
                log(st, "Scout", "info", "collegato a pump.fun")
                backoff = 5
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                        mint = msg.get("mint")
                        if mint:
                            add_watch(st, mint, msg.get("symbol") or "?", "pump.fun")
                    except Exception:  # noqa: BLE001
                        continue
        except Exception as e:  # noqa: BLE001
            log(st, "Scout", "warn", f"pump.fun disconnesso ({type(e).__name__}), riprovo tra {backoff}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 120)


async def scout_gecko(st, http):
    headers = {"Accept": "application/json;version=20230302"}
    first, tick = True, 0
    while True:
        data = await http.get("https://api.geckoterminal.com/api/v2/networks/solana/new_pools?page=1",
                              headers=headers)
        found = gecko_mints(data)
        if found and first:
            log(st, "Scout", "info", "collegato alle nuove pool (GeckoTerminal)")
            first = False
        for mint, name in found:
            add_watch(st, mint, name, "nuova pool")
        if tick % 5 == 0 and "tagg" not in SRC:  # tendenze ogni 5 minuti (v8.3: se manca l'aggregatore)
            tdata = await http.get("https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?page=1",
                                   headers=headers)
            tr = gecko_mints(tdata)
            if tr:
                st.trending = {m for m, _ in tr}
                for mint, name in tr:
                    add_watch(st, mint, name, "in tendenza")
        tick += 1
        await asyncio.sleep(60)


# ======================================================================= Filter
# ======================================================================= v6: segnali SOFT graduati
# Per ogni segnale: chiave CFG della soglia grave, verso in cui la soglia "allenta" (+1 = sale), passo del
# Learner e limiti FISSI entro cui il Learner puo muoverla. Gli scarti HARD non sono mai qui.
SOFT_FLAGS = {
    "bundle": {"label": "reti collegate (bundle)", "hard": "BUNDLE_HARD_PCT", "dir": 1, "step": 2.0, "lim": (15.0, 35.0)},
    "equal": {"label": "quote identiche", "hard": "EQUAL_HARD_PCT", "dir": 1, "step": 1.0, "lim": (6.0, 14.0)},
    "top10": {"label": "top 10 concentrati", "hard": "TOP10_HARD_PCT", "dir": 1, "step": 1.0, "lim": (45.0, 55.0)},
    # v7: LP sbloccata e soft; sotto LP_HARD_PCT si aggiunge 'lp_unlocked_grave' (penalita e size doppie).
    # Il Learner sposta LP_HARD_PCT (esiti 'lp_unlocked_grave') e penalita/size (esiti 'lp_unlocked').
    "lp_unlocked": {"label": "LP sbloccata", "hard": "LP_HARD_PCT", "dir": -1, "step": 2.5, "lim": (15.0, 40.0)},
    # v7: cluster graduato. 'hard': None = il Learner regola solo penalita e size (nei due versi, entro i limiti);
    # la soglia ESTREMA (chiave 'cluster_hard') non viene mai allentata dal Learner.
    "cluster": {"label": "cluster di wallet collegati", "hard": None, "dir": 1, "step": 0.0, "lim": (0.0, 0.0),
                "pen": "CLUSTER_PENALTY", "size": "CLUSTER_SIZE_MULT", "pen_lim": (0.04, 0.15),
                "size_lim": (0.2, 0.7)},
    # v8.2: cluster o honeypot NON verificabili (RPC lenta, tempo scaduto): mai 'ok', penalita e size ridotta
    "cluster_unknown": {"label": "cluster non verificato", "hard": None, "dir": 1, "step": 0.0, "lim": (0.0, 0.0),
                        "pen": "CLUSTER_UNKNOWN_PENALTY", "size": "CLUSTER_UNKNOWN_SIZE_MULT",
                        "pen_lim": (0.03, 0.12), "size_lim": (0.3, 0.7)},
    "honeypot_unknown": {"label": "honeypot non verificato", "hard": None, "dir": 1, "step": 0.0, "lim": (0.0, 0.0),
                         "pen": "CLUSTER_UNKNOWN_PENALTY", "size": "CLUSTER_UNKNOWN_SIZE_MULT",
                         "pen_lim": (0.03, 0.12), "size_lim": (0.3, 0.7)},
}
LEARN_PEN_LIM = (0.02, 0.10)    # penalita per segnale: il Learner resta tra 0.02 e 0.10
LEARN_SIZE_LIM = (0.3, 0.8)     # moltiplicatore size per segnale: tra 0.3x e 0.8x
SHADOW_TASKS = set()
BROKER_REF = [None]


def _ls(st, key):
    e = st.learner_shadow.setdefault(key, {})
    for k, v in (("hist", []), ("pen_d", 0.0), ("size_d", 0.0), ("hard_d", 0.0), ("last_adj", 0.0), ("adj", 0)):
        e.setdefault(k, v)
    return e


def _learned(base, delta, lim):
    if not delta:
        return base
    return clamp(base + delta, min(lim[0], base), max(lim[1], base))


def _pen_base(flag):
    spec = SOFT_FLAGS.get(flag) or {}
    return CFG[spec["pen"]] if spec.get("pen") else CFG["SOFT_PENALTY"], spec.get("pen_lim", LEARN_PEN_LIM)


def _size_base(flag):
    spec = SOFT_FLAGS.get(flag) or {}
    return CFG[spec["size"]] if spec.get("size") else CFG["SOFT_SIZE_MULT"], spec.get("size_lim", LEARN_SIZE_LIM)


def soft_pen(st, flag):
    b, lim = _pen_base(flag)
    return _learned(b, (st.learner_shadow.get(flag) or {}).get("pen_d", 0.0), lim)


def soft_size(st, flag):
    b, lim = _size_base(flag)
    return _learned(b, (st.learner_shadow.get(flag) or {}).get("size_d", 0.0), lim)


def soft_hard(st, flag):
    spec = SOFT_FLAGS[flag]
    if not spec.get("hard"):
        return None
    return _learned(CFG[spec["hard"]], (st.learner_shadow.get(flag + "_grave") or {}).get("hard_d", 0.0), spec["lim"])


# v8.4e (Kiwi 07:07): LP per niente bloccata/bruciata (< 0.5%, cioe "0%") o stato LP sconosciuto = MAI acquisto
# vero (ne size ridotta, ne narrativa/KOL, ne override/Learner/deep review): solo shadow. Costanti, non in CFG.
LP_ZERO_MAX_PCT = 0.5
LP_GATE_KEYS = frozenset({"lp_unlocked_zero", "lp_unknown"})


def base_flags(flags):
    """Segnali distinti (lp_unlocked + lp_unlocked_grave contano come uno solo per SOFT_MAX_FLAGS)."""
    return sorted({f[:-6] if f.endswith("_grave") else f for f in flags})


def bundle_pct(r):
    """Quota dell'offerta tenuta dalle reti di wallet collegati (RugCheck insiderNetworks). None = non calcolabile."""
    tok = r.get("token") or {}
    supply = fnum(tok.get("supply"))
    nets = [n for n in (r.get("insiderNetworks") or []) if isinstance(n, dict)
            and 0 < fnum(n.get("size")) <= CFG["BUNDLE_MAX_NET_SIZE"]]
    if supply <= 0:
        return None
    if not nets:  # solo reti enormi (exchange/airdrop) o nessun dettaglio
        return 0.0 if r.get("insiderNetworks") else None
    pct = sum(fnum(n.get("tokenAmount")) for n in nets) / supply * 100
    return round(pct, 2) if 0 <= pct <= 100 else None


def rc_networks(r):
    """v7: reti RugCheck [(wallet, % offerta)]; quelle >BUNDLE_MAX_NET_SIZE (exchange/airdrop) solo se tengono molto."""
    supply = fnum((r.get("token") or {}).get("supply"))
    out = []
    for n in r.get("insiderNetworks") or []:
        if not isinstance(n, dict) or supply <= 0:
            continue
        size, pct = int(fnum(n.get("size"))), fnum(n.get("tokenAmount")) / supply * 100
        if size > CFG["BUNDLE_MAX_NET_SIZE"] and pct < CFG["BUNDLE_SOFT_PCT"]:
            continue
        if 0 <= pct <= 100:
            out.append((size, round(pct, 3)))
    return out


def cluster_metrics(rows, rc_nets=None):
    """v7: rows = [{o: owner, pct, ntx, src}] dei top holder (pool esclusi). Reti = wallet con lo stesso
    finanziatore (prima transazione del wallet). Ritorna metriche + livello 'none' | 'soft' | 'hard'."""
    groups = {}
    for r in rows:
        if r.get("src"):
            groups.setdefault(r["src"], []).append(r)
    nmin = int(CFG["BUNDLE_NET_MIN_WALLETS"])
    nets = sorted([(len(v), sum(fnum(x.get("pct")) for x in v)) for v in groups.values() if len(v) >= nmin],
                  reverse=True)
    service = []
    # solo per i finanziatori on-chain (non per le reti RugCheck, collegate da trasferimenti di token)
    if rows and nets and nets[0][0] >= CFG["CLUSTER_SERVICE_SHARE"] * len(rows) and \
            nets[0][1] < CFG["BUNDLE_TOTAL_HARD_PCT"] / 2:
        service = [nets.pop(0)]   # es. hot wallet di exchange o relayer di un'app: non e un bundle, resta soft
    for size, pct in rc_nets or []:
        if size >= nmin:
            nets.append((size, pct))
    nets.sort(reverse=True)
    n_nets = len(nets)
    net_pct = round(sum(p for _, p in nets), 2)
    wallets = sum(n for n, _ in nets)
    scanned = len(rows)
    fresh = sum(1 for r in rows if r.get("ntx") is not None and r["ntx"] < CFG["CLUSTER_FRESH_TX"])
    fresh_share = round(fresh / scanned, 3) if scanned else 0.0
    why = []
    if n_nets >= CFG["BUNDLE_MAX_NETWORKS"] and net_pct >= CFG["BUNDLE_TOTAL_HARD_PCT"]:
        why.append(f"{n_nets} reti collegate con il {net_pct:.1f}% dell'offerta")
    if wallets >= CFG["BUNDLE_MAX_INSIDER_WALLETS"]:
        why.append(f"{wallets} wallet collegati")
    if scanned >= 10 and fresh_share >= CFG["CLUSTER_FRESH_HARD"] and n_nets >= 2:
        why.append(f"{fresh_share * 100:.0f}% dei top holder sono wallet nuovi e {n_nets} reti")
    level = "hard" if why else "none"
    if level == "none":
        if service:
            why.append(f"un solo finanziatore per {service[0][0]} wallet ({service[0][1]:.1f}%): servizio o bundle?")
        if n_nets >= CFG["CLUSTER_SOFT_NETWORKS"]:
            why.append(f"{n_nets} reti collegate ({net_pct:.1f}%)")
        if net_pct >= CFG["CLUSTER_SOFT_PCT"]:
            why.append(f"reti collegate al {net_pct:.1f}%")
        if scanned >= 10 and fresh_share >= CFG["CLUSTER_FRESH_SOFT"]:
            why.append(f"{fresh_share * 100:.0f}% top holder wallet nuovi")
        level = "soft" if why else "none"
    return {"level": level, "why": "; ".join(why), "networks": n_nets, "net_pct": net_pct, "wallets": wallets,
            "fresh_share": fresh_share, "scanned": scanned, "nets": nets[:8], "service": service}


_B58A = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58(b):
    n, out = int.from_bytes(b, "big"), ""
    while n:
        n, r = divmod(n, 58)
        out = _B58A[r] + out
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + out


RPC_HEAVY_LOCK = asyncio.Lock()   # v7: un solo lavoro RPC pesante alla volta (cluster, KOL): RPC pubblica condivisa
CLUSTER_CACHE = {}


async def _crpc(http, method, params):
    d = await http.post(CFG["CLUSTER_RPC"], {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    return d.get("result") if isinstance(d, dict) else None


async def cluster_scan(st, http, mint, sec):
    """v7: top holder (getProgramAccounts) -> per ogni wallet: n. transazioni e finanziatore della prima.
    Solo lettura, RPC pubblica, al massimo ~2*CLUSTER_TOP_N+1 chiamate, cache 30 min. None se non riesce."""
    hit = CLUSTER_CACHE.get(mint)
    if hit and time.time() - hit[0] < CFG["CLUSTER_CACHE_SEC"]:
        return hit[1]
    excl = set(sec.get("exclude") or [])
    owners = []
    prog = sec.get("token_program") or "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
    async with RPC_HEAVY_LOCK:
        if int(fnum(sec.get("holders"))) <= CFG["CLUSTER_MAX_HOLDERS_GPA"]:
            flt = [{"memcmp": {"offset": 0, "bytes": mint}}]
            if prog.startswith("Tokenkeg"):
                flt.append({"dataSize": 165})
            acc = await _crpc(http, "getProgramAccounts", [prog, {"encoding": "base64", "filters": flt,
                                                                  "dataSlice": {"offset": 32, "length": 40}}])
            if isinstance(acc, list) and acc:
                rows = []
                for a in acc:
                    try:
                        d = base64.b64decode(a["account"]["data"][0])
                        rows.append((int.from_bytes(d[32:40], "little"), _b58(d[:32]), a.get("pubkey")))
                    except Exception:  # noqa: BLE001
                        continue
                tot = sum(x[0] for x in rows) or 1
                rows.sort(reverse=True)
                for amt, owner, acc_k in rows:
                    # v8.4f: niente piu "quota >= 10% = pool": fuori solo exclude, indirizzi morti e owner PDA
                    if owner in excl or acc_k in excl or owner in DEAD_ADDRS or on_curve(owner) is False:
                        continue
                    owners.append((owner, amt / tot * 100))
                    if len(owners) >= CFG["CLUSTER_TOP_N"]:
                        break
        if not owners:
            owners = [(o, p) for o, p in sec.get("rc_holders") or [] if o and o not in excl][:CFG["CLUSTER_TOP_N"]]
        if not owners:
            return None
        out, fails = [], 0
        for owner, pct in owners:
            sigs = await _crpc(http, "getSignaturesForAddress", [owner, {"limit": int(CFG["CLUSTER_FRESH_TX"])}])
            if not isinstance(sigs, list):
                fails += 1
                if fails >= 6:   # RPC pubblica in 429: niente verdetto (nessun effetto), non insistiamo
                    return None
                continue
            rec = {"o": owner, "pct": pct, "ntx": len(sigs), "src": None}
            if sigs and len(sigs) < CFG["CLUSTER_FRESH_TX"]:
                tx = await _crpc(http, "getTransaction", [sigs[-1]["signature"], {
                    "encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
                try:
                    msg = tx["transaction"]["message"]
                    ins = list(msg.get("instructions") or [])
                    for ii in (tx.get("meta") or {}).get("innerInstructions") or []:
                        ins += ii.get("instructions") or []
                    for x in ins:
                        p = x.get("parsed") if isinstance(x, dict) else None
                        if isinstance(p, dict) and p.get("type") == "transfer" and \
                                (p.get("info") or {}).get("destination") == owner and "lamports" in p["info"]:
                            rec["src"] = p["info"].get("source")
                            break
                    payer = msg["accountKeys"][0]["pubkey"] if isinstance(msg["accountKeys"][0], dict) \
                        else msg["accountKeys"][0]
                    if rec["src"] is None and payer != owner:
                        rec["src"] = payer
                except Exception:  # noqa: BLE001
                    pass
            out.append(rec)
    if len(out) < 5:
        return None
    res = cluster_metrics(out, sec.get("rc_networks"))
    CLUSTER_CACHE[mint] = (time.time(), res)
    for k in [k for k, v in CLUSTER_CACHE.items() if time.time() - v[0] > CFG["CLUSTER_CACHE_SEC"]]:
        CLUSTER_CACHE.pop(k, None)
    return res


# ----------------------------------------------------------------------- v8.4d: "nessun prezzo" verificato
# 31/10: 17 shadow chiusi insieme a -100% "possibile rug" alle 01:04:02 dopo una pausa di ~25 min del processo
# (nessun log tra 00:38:35 e 01:03:54): tutti i token erano vivi (DexScreener). Ora: (a) dopo una pausa del ciclo
# il conteggio "nessun prezzo" riparte da adesso; (b) prima di -100/rug serve la conferma di una seconda fonte
# (Jupiter price v3, gratis); se anche lei non risponde si riprova, e dopo NOPRICE_MAX_WAIT tentativi lo shadow
# si chiude come DATI NON VALIDI (al prezzo noto, non rug, mai usato dal Learner).
LOOP_LAST = {}


def stall_reset(st, name, objs, now=None):
    """Ritorna i secondi di pausa se il ciclo 'name' e rimasto fermo oltre STALL_RESET_SEC (e azzera last_ok)."""
    now = now or time.time()
    last = LOOP_LAST.get(name)
    LOOP_LAST[name] = now
    gap = now - last if last else 0.0
    if gap <= float(CFG.get("STALL_RESET_SEC", 180)):
        return 0.0
    objs = list(objs)
    for o in objs:
        o["last_ok"] = max(fnum(o.get("last_ok")), now)
    if objs:
        log(st, "Learner" if name == "shadow" else "Risk", "warn",
            f"ciclo {name} fermo per {gap / 60:.0f} min (pausa del processo/macchina): conteggio 'nessun prezzo' "
            f"ripartito per {len(objs)}")
    return gap


# ----------------------------------------------------------------------- v8.5: pause dell'host (VM congelata)
# 31/10: buchi di ~25 min nei prezzi ogni mezz'ora circa = la VM si congela ('virtual machine fork' in dmesg, ngrok
# in heartbeat timeout). Alla ripresa: evento host_pause + Telegram (max 1 ogni 30 min, pause raggruppate), timer
# 'nessun prezzo'/no_route azzerati, nuova quotazione prima di qualsiasi chiusura, buco gap_host nei price_paths,
# chiusure shadow entro 5 min dalla ripresa = invalid_for_learning.
CFG.setdefault("HOST_PAUSE_SEC", 120)
CFG.setdefault("HOST_PAUSE_MONO_JUMP_SEC", 30)
CFG.setdefault("HOST_PAUSE_ALERT_SEC", 1800)
CFG.setdefault("HOST_PAUSE_INVALID_SEC", 300)
HOST_PAUSE = {"last_resume": 0.0, "alert_t": 0.0, "pending": [], "n": 0, "hold": {}, "last_evt": 0.0}
LOOP_MONO = {}


def host_pause_alert(now=None):
    now = now or time.time()
    hp = HOST_PAUSE
    if not hp["pending"] or now - hp["alert_t"] < CFG["HOST_PAUSE_ALERT_SEC"]:
        return False
    ps, hp["pending"], hp["alert_t"] = hp["pending"], [], now
    tot = sum(b - a for a, b in ps) / 60
    f = lambda t: datetime.fromtimestamp(t, TZ).strftime("%H:%M")
    tg_send(f"Host in pausa (VM congelata): {len(ps)} paus{'a' if len(ps) == 1 else 'e'}, in tutto {tot:.0f} min "
            f"(ultima {f(ps[-1][0])}-{f(ps[-1][1])} Roma). Timer 'nessun prezzo' azzerati, nuova quotazione prima "
            "di chiudere, chiusure shadow entro 5 min = non valide per il Learner.")
    alert_state_save()   # v8.5g
    return True


def host_pause_check(st, name, now=None, mono=None):
    """Da chiamare a OGNI giro del ciclo 'name'. Ritorna i secondi di pausa (0 = nessuna)."""
    now, mono = now or time.time(), mono if mono is not None else time.monotonic()
    last = LOOP_MONO.get(name)
    LOOP_MONO[name] = (now, mono)
    host_pause_alert(now)
    if not last:
        return 0.0
    gw, gm = now - last[0], mono - last[1]
    if gw <= CFG["HOST_PAUSE_SEC"] and abs(gw - gm) <= CFG["HOST_PAUSE_MONO_JUMP_SEC"]:
        return 0.0
    hp = HOST_PAUSE
    hp["hold"] = {"risk": True, "shadow": True}   # primo giro di ogni ciclo dopo la ripresa: solo quotazioni
    if now - hp["last_evt"] < 60:                  # gia registrata dall'altro ciclo
        return gw
    hp["last_evt"], hp["last_resume"], hp["n"] = now, now, hp["n"] + 1
    hp["pending"].append((last[0], now))
    objs = [("real", k, v) for k, v in st.positions.items()] + [("shadow", k, v) for k, v in st.shadow_trades.items()]
    for kind, mint, o in objs:
        o["last_ok"] = max(fnum(o.get("last_ok")), now)
        for k in ("np_wait", "np_src", "qmark"):
            o.pop(k, None)
        for k in ("mark_noroute", "noroute_fails"):
            if k in o:
                o[k] = 0
        path_write(mint, {"t": int(now), "k": "gap_host", "from": int(last[0]), "gap": int(gw), "kind": kind})
    for o in LOOP_LAST:
        LOOP_LAST[o] = now
    write_event("Risk", "host_pause", f"host in pausa {gw / 60:.1f} min (ciclo {name}, monotonic {gm:.0f}s): "
                f"timer azzerati per {len(objs)} tra posizioni e shadow", {"gap_s": round(gw), "mono_s": round(gm),
                                                                          "loop": name, "from": int(last[0])})
    log(st, "Risk", "warn", f"pausa dell'host di {gw / 60:.0f} min: timer 'nessun prezzo' azzerati, "
                            "nuova quotazione prima di qualsiasi chiusura")
    host_pause_alert(now)
    return gw


HOST_RETRO_PATH = os.path.join(DATA_DIR, "host_pause_retro.json")


def apply_host_pause_retro(st):
    """v8.5: una sola volta. host_pause_retro.json = {"closes": [[mint, ts_chiusura, pnl], ...]}: chiusure shadow
    entro 5 min dalla ripresa dopo le pause di stanotte -> riga CSV valido=0 + invalid_for_learning, esiti tolti
    dal Learner (learner_shadow e learner_bonus)."""
    try:
        d = json.load(open(HOST_RETRO_PATH))
    except Exception:  # noqa: BLE001
        return 0
    if d.get("applied"):
        return 0
    closes = [(str(x[0]), int(x[1]), float(x[2])) for x in d.get("closes") or []]
    why_of = {(str(x[0]), int(x[1])): (x[3] if len(x) > 3 else "host_pause_retro") for x in d.get("closes") or []}
    def hit(pnl, ts):
        return any(abs(ts - t) <= 5 and abs(pnl - p) <= 0.02 for _, t, p in closes)
    n_l = 0
    for key, e in st.learner_shadow.items():
        h = e.get("hist") or []
        keep = [x for x in h if not (len(x) >= 4 and x[2] == "shadow" and hit(float(x[0]), int(x[3])))]
        n_l += len(h) - len(keep)
        e["hist"] = keep
    lb = st.learner_bonus.get("hist") or []
    keep = [x for x in lb if not (len(x) >= 5 and x[4] and hit(float(x[0]), int(x[3])))]
    n_b = len(lb) - len(keep)
    if "hist" in st.learner_bonus:
        st.learner_bonus["hist"] = keep
    n_r = 0
    if os.path.exists(SHADOW_CSV):
        with open(SHADOW_CSV, newline="") as f:
            rd = csv.DictReader(f)
            fields, rows = list(rd.fieldnames or []), list(rd)
        if "invalid_for_learning" not in fields:
            fields.append("invalid_for_learning")
        def row_why(r):   # stesso mint, stesso pnl, chiusura entro 30 min (la riga puo avere l'ora di prima della pausa)
            try:
                tr = datetime.strptime(r.get("chiuso", ""), "%Y-%m-%d %H:%M").replace(tzinfo=TZ).timestamp()
            except ValueError:
                return None
            for m, t, p in closes:
                if r.get("mint") == m and abs(fnum(r.get("pnl_pct")) - p) <= 0.02 and abs(tr - t) <= 1800:
                    return why_of[(m, t)]
            return None
        for r in rows:
            wr = row_why(r)
            if wr:
                n_r += 1
                if r.get("valido") != "0":
                    r["valido"], r["invalid_for_learning"] = "0", wr
                    r["uscita"] = ("NON USATO DAL LEARNER (lezione negativa): " if wr == "lezione_negativa" else
                                   "DATI NON VALIDI (ripresa dopo pausa dell'host, retroattivo): ") + r.get("uscita", "")
                elif not r.get("invalid_for_learning"):
                    r["invalid_for_learning"] = "no_price"
        tmp = SHADOW_CSV + ".tmp"
        with open(tmp, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
        os.replace(tmp, SHADOW_CSV)
    d.update(applied=True, applied_at=datetime.now(TZ).isoformat(timespec="seconds"),
             removed_learner=n_l, removed_bonus=n_b, csv_rows=n_r)
    with open(HOST_RETRO_PATH, "w") as f:
        json.dump(d, f, indent=1)
    write_event("Learner", "host_pause_retro", f"chiusure shadow a ridosso delle pause host: {n_r} righe non valide, "
                f"{n_l} esiti tolti dal Learner, {n_b} dai bonus", {})
    return n_r


LIQ_RETRO_PATH = os.path.join(DATA_DIR, "liq_crash_retro.json")


def _retro_shadow_pnl(r, tp1_frac):
    """v8.5h: PnL di uno shadow uscito per liquidita crollata con la regola nuova (parte non venduta = 0).
    Ricostruisce le prese di profitto dal percorso prezzi (salti oltre PRICE_SPIKE_MULT ignorati).
    Ritorna (pnl, fonte) o (None, motivo) se non ricostruibile."""
    mint = r.get("mint", "")
    try:
        t_open = datetime.strptime(r.get("aperto", ""), "%Y-%m-%d %H:%M").replace(tzinfo=TZ).timestamp()
        t_close = datetime.strptime(r.get("chiuso", ""), "%Y-%m-%d %H:%M").replace(tzinfo=TZ).timestamp()
    except ValueError:
        return None, "date"
    tp1, tp2 = fnum(CFG["TP1_PCT"]), fnum(CFG["TAKE_PROFIT_PCT"])
    if fnum(r.get("peak_pct")) < min(tp1, tp2):
        return -100.0, "nessuna presa di profitto"
    recs = []
    for fp in (os.path.join(PATH_DIR, f"{mint}.jsonl.1"), os.path.join(PATH_DIR, f"{mint}.jsonl")):
        try:
            with open(fp) as f:
                for ln in f:
                    try:
                        d = json.loads(ln)
                    except ValueError:
                        continue
                    if d.get("k") == "shadow" and t_open - 90 <= fnum(d.get("t")) <= t_close + 90:
                        recs.append(d)
        except OSError:
            pass
    if not any(d.get("ev") == "entry" for d in recs):
        return None, "percorso prezzi assente"
    recs.sort(key=lambda d: fnum(d.get("t")))
    start = max(i for i, d in enumerate(recs) if d.get("ev") == "entry")
    last, realized, stage = None, 0.0, 0
    for d in recs[start:]:
        if str(d.get("ev", "")).startswith("exit"):
            break
        if d.get("pnl") is None or fnum(d.get("p")) <= 0:
            continue
        if last is not None and price_spike(last, d["p"]):
            continue
        last = fnum(d["p"])
        pnl = fnum(d["pnl"])
        if stage == 0 and pnl >= tp1:
            realized += tp1_frac * (1 + pnl / 100)
            stage = 1
        elif stage == 1 and pnl >= tp2:
            realized += fnum(CFG["TAKE_PROFIT_FRACTION"]) * (1 + pnl / 100)
            stage = 2
    return round(realized * 100 - 100, 2), "percorso prezzi"


def apply_liq_crash_retro(st):
    """v8.5h: una sola volta (liq_crash_retro.json). Righe shadow valido=1 con PnL > PNL_IMPLAUSIBLE_PCT -> valido=0
    (pnl_implausibile, rug=1). Righe valido=1 uscite per 'liquidita crollata' -> PnL rifatto con la regola nuova (parte
    non venduta a 0, prese di profitto dal percorso prezzi; mai piu alto di prima), rug=1. Esiti del Learner
    (learner_shadow, learner_bonus) tolti o corretti allo stesso modo. trades.csv: stessa regola sulle righe vere."""
    if os.path.exists(LIQ_RETRO_PATH):
        try:
            if json.load(open(LIQ_RETRO_PATH)).get("applied"):
                return 0
        except Exception:  # noqa: BLE001
            pass
    # TP1_FRACTION 0.5 da overrides.json (dalle 08:22 del 31/10); prima il valore di config
    ovr_t, ovr_f = None, None
    try:
        od = json.load(open(os.path.join(DATA_DIR, "overrides.json")))
        ovr_f = (od.get("global") or {}).get("TP1_FRACTION")
        ovr_t = datetime.fromisoformat(od.get("updated_at")).timestamp() if ovr_f is not None else None
    except Exception:  # noqa: BLE001
        pass
    changes, log_rows = [], []   # (mint, ts_chiusura, pnl_vecchio, pnl_nuovo | None = togli)
    n_inv = n_fix = 0
    if os.path.exists(SHADOW_CSV):
        with open(SHADOW_CSV, newline="") as f:
            rd = csv.DictReader(f)
            fields, rows = list(rd.fieldnames or []), list(rd)
        for k in ("valido", "invalid_for_learning", "rug"):
            if k not in fields:
                fields.append(k)
        for r in rows:
            if str(r.get("valido", "1")).strip() == "0":
                continue
            old = fnum(r.get("pnl_pct"))
            try:
                tc = datetime.strptime(r.get("chiuso", ""), "%Y-%m-%d %H:%M").replace(tzinfo=TZ).timestamp()
            except ValueError:
                tc = 0
            liq = str(r.get("uscita", "")).startswith("liquidita crollata")
            if pnl_implausible(old):
                r["valido"], r["invalid_for_learning"], r["rug"] = "0", "pnl_implausibile", "1"
                r["uscita"] = "DATI NON VALIDI (PnL implausibile, pool svuotata, retroattivo v8.5h): " + r.get("uscita", "")
                changes.append((r.get("mint"), tc, old, None))
                log_rows.append([r.get("symbol"), r.get("mint"), r.get("chiuso"), old, "valido=0"])
                n_inv += 1
            elif liq and old > -100:
                frac = fnum(ovr_f) if ovr_t and tc >= ovr_t - 60 else fnum(CFG["TP1_FRACTION"])
                new, src = _retro_shadow_pnl(r, frac)
                if new is None:   # percorso non ricostruibile: fuori dal Learner, resta rug
                    r["valido"], r["invalid_for_learning"], r["rug"] = "0", "liq_crash_retro_" + src.replace(" ", "_"), "1"
                    changes.append((r.get("mint"), tc, old, None))
                    log_rows.append([r.get("symbol"), r.get("mint"), r.get("chiuso"), old, "valido=0 (" + src + ")"])
                    n_inv += 1
                    continue
                new = min(old, new)
                r["pnl_pct"], r["rug"] = f"{new:.2f}", "1"
                r["uscita"] = r.get("uscita", "") + f" [v8.5h: parte non venduta a 0, PnL prima {old:+.2f}%]"
                changes.append((r.get("mint"), tc, old, new))
                log_rows.append([r.get("symbol"), r.get("mint"), r.get("chiuso"), old, f"{new:+.2f} ({src})"])
                n_fix += 1
        if changes:
            tmp = SHADOW_CSV + ".tmp"
            with open(tmp, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fields)
                w.writeheader()
                w.writerows(rows)
            os.replace(tmp, SHADOW_CSV)

    def hit(pnl, ts):
        for c in changes:
            if abs(fnum(pnl) - c[2]) <= 0.02 and -90 <= ts - c[1] <= 150:
                return c
        return None
    n_l = 0
    for key, e in st.learner_shadow.items():
        keep = []
        for x in e.get("hist") or []:
            c = hit(x[0], int(x[3])) if len(x) >= 4 and x[2] == "shadow" else None
            if c is None:
                keep.append(x)
                continue
            n_l += 1
            if c[3] is not None:
                keep.append([round(c[3], 2), 1] + list(x[2:]))
        e["hist"] = keep
    n_b = 0
    if st.learner_bonus.get("hist"):
        keep = []
        for x in st.learner_bonus["hist"]:
            c = hit(x[0], int(x[3])) if len(x) >= 5 and x[4] else None
            if c is None:
                keep.append(x)
                continue
            n_b += 1
            if c[3] is not None:
                keep.append([round(c[3], 2), 1] + list(x[2:]))
        st.learner_bonus["hist"] = keep
    # trade veri (paper): per ora nessuna riga con PnL implausibile; se ce ne fossero, solo marcate valido=0
    n_t = 0
    tp = os.path.join(DATA_DIR, "trades.csv")
    if os.path.exists(tp):
        with open(tp, newline="") as f:
            rd = csv.DictReader(f)
            tf, trs = list(rd.fieldnames or []), list(rd)
        bad = [r for r in trs if pnl_implausible(r.get("pnl_pct")) and str(r.get("valido", "1")).strip() != "0"]
        if bad:
            for k in ("valido", "invalid_for_learning"):
                if k not in tf:
                    tf.append(k)
            for r in trs:
                r["valido"] = r.get("valido") or "1"
                if pnl_implausible(r.get("pnl_pct")) and str(r["valido"]).strip() != "0":
                    r["valido"], r["invalid_for_learning"] = "0", "pnl_implausibile"
                    n_t += 1
            with open(tp + ".tmp", "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=tf)
                w.writeheader()
                w.writerows(trs)
            os.replace(tp + ".tmp", tp)
    d = {"applied": True, "applied_at": datetime.now(TZ).isoformat(timespec="seconds"), "shadow_invalid": n_inv,
         "shadow_fixed": n_fix, "trades_invalid": n_t, "removed_or_fixed_learner": n_l, "removed_or_fixed_bonus": n_b,
         "rows": log_rows}
    with open(LIQ_RETRO_PATH, "w") as f:
        json.dump(d, f, indent=1)
    write_event("Learner", "liq_crash_retro", f"v8.5h: shadow liquidita crollata/PnL implausibile: {n_inv} non validi, "
                f"{n_fix} PnL corretti, {n_t} trade non validi; Learner {n_l} esiti, bonus {n_b}", {})
    save_state(st, force=True)
    return n_inv + n_fix + n_t


async def second_price(http, mints):
    """Seconda fonte prezzi (Jupiter price v3, gratis): {mint: prezzo $} oppure None se la fonte non risponde."""
    if http is None or not mints:
        return None
    out = {}
    for i in range(0, len(mints), 50):
        chunk = list(mints)[i:i + 50]
        d = await http.get("https://lite-api.jup.ag/price/v3?ids=" + ",".join(chunk),
                           headers={"Accept": "application/json"})
        if not isinstance(d, dict):
            return None
        for mt in chunk:
            v = fnum((d.get(mt) or {}).get("usdPrice")) if isinstance(d.get(mt), dict) else 0.0
            if v > 0:
                out[mt] = v
    return out


async def verify_no_price(st, http, items, now=None):
    """items {mint: shadow/posizione} senza prezzo da 15 min -> {mint: 'alive' | 'wait' | 'invalid' | 'dead'}.
    alive = prezzo dalla seconda fonte (aggiornato); dead = la seconda fonte risponde ma senza prezzo (rug possibile);
    wait = seconda fonte non disponibile, riprova tra ~2 min; invalid = troppi tentativi senza nessuna fonte."""
    now = now or time.time()
    if not items:
        return {}
    px = await second_price(http, list(items))
    out = {}
    for mt, o in items.items():
        if px is not None and px.get(mt):
            o["last_price"], o["last_ok"], o["np_alive"] = px[mt], now, int(o.get("np_alive", 0)) + 1
            o.pop("np_wait", None)
            out[mt] = "alive"
        elif px is not None:
            o["np_src"] = "jupiter"
            out[mt] = "dead"
        else:
            o["np_wait"] = int(o.get("np_wait", 0)) + 1
            if o["np_wait"] >= int(CFG.get("NOPRICE_MAX_WAIT", 15)):
                out[mt] = "invalid"
            else:
                o["last_ok"] = now - 900 + 120
                out[mt] = "wait"
    log(st, "Learner", "noprice", "nessun prezzo da 15 min, seconda fonte: " +
        ", ".join(f"{(o.get('sym') or o.get('symbol') or mt[:6])} {out[mt]}" for mt, o in items.items()))
    return out


# ----------------------------------------------------------------------- v8.4c: honeypot sconosciuto, ticker impostori
HP_RETRY = {}   # mint -> tentativi dopo un honeypot 'sconosciuto' (in memoria)


HP_SHADOW_KEYS = frozenset({"goplus_hard", "honeypot_hard", "honeypot_soft"})   # v8.4f: solo misura, Learner fermo


def hp_shadow(st, http, mint, m, c, hp, kind):
    """v8.4f (review Claude g): ogni scarto GoPlus/honeypot va in shadow, per misurare i falsi positivi.
    Mai un acquisto; tag goplus_hard / honeypot_hard / honeypot_soft, piu fee_auth_known se GoPlus lo segnala."""
    try:
        if not CFG.get("HP_REJECT_SHADOW", True):
            return
        gf = list(hp.get("goplus_flags") or [])
        key = ("goplus_hard" if hp.get("goplus_level") == "hard" else "honeypot_hard") if kind == "hard" \
            else "honeypot_soft"
        flags = list(c.get("soft") or []) + [f"gp:{f}" for f in gf][:8] + (["fee_auth_known"] if "fee_auth_known" in gf else [])
        sig = c.get("signals") or []
        sig = [x for x in (sig.split("|") if isinstance(sig, str) else sig) if x] + \
            (["fee_auth_known"] if "fee_auth_known" in gf else [])
        spawn_shadow(st, http, mint, m, [key], f"{key}: {str(hp.get('why') or '')[:160]}", fnum(c.get("score")),
                     flags, tag=key, signals=sig)
    except Exception:  # noqa: BLE001
        pass


def honeypot_unknown_skip(st, http, mint, m, c, hp, now=None):
    """Honeypot/sicurezza 'sconosciuto': niente acquisto. Shadow 'honeypot_unknown' e nuovo controllo al giro
    successivo (cooldown breve HONEYPOT_RETRY_MIN, al massimo HONEYPOT_RETRY_MAX volte, poi cooldown normale)."""
    now = now or time.time()
    n = HP_RETRY[mint] = HP_RETRY.get(mint, 0) + 1
    retry = n <= int(CFG.get("HONEYPOT_RETRY_MAX", 3))
    reject(st, mint, m["symbol"], f"honeypot non verificabile ({'riprovo' if retry else 'basta tentativi'} "
           f"{min(n, int(CFG.get('HONEYPOT_RETRY_MAX', 3)))}/{int(CFG.get('HONEYPOT_RETRY_MAX', 3))}): {hp.get('why')}",
           agent="Executor", cat="honeypot non verificato")
    if retry:   # libera il mint (st.processed blocca 6 h) con un cooldown breve: lo Scout lo riprende al giro dopo
        st.processed.pop(mint, None)
        st.cooldown[mint] = now + float(CFG.get("HONEYPOT_RETRY_MIN", 5)) * 60
    if CFG.get("SHADOW_TRADES"):
        spawn_shadow(st, http, mint, m, ["honeypot_unknown"], f"honeypot sconosciuto: {hp.get('why')}",
                     fnum(c.get("score")), list(c.get("soft") or []), tag="honeypot_unknown")
    return retry


MAJOR_TICKERS = {"BNB", "WBNB", "BTC", "WBTC", "CBBTC", "XBT", "ETH", "WETH", "SOL", "WSOL", "USDC", "USDT", "USD1",
                 "PYUSD", "XRP", "DOGE", "ADA", "TRX", "TON", "AVAX", "DOT", "LINK", "LTC", "SUI", "BCH", "XLM", "HYPE",
                 "JUP", "PUMP", "TRUMP", "BINANCE", "COINBASE", "OKX", "BYBIT", "KRAKEN", "KUCOIN", "BITGET", "MEXC"}
OFFICIAL_MINTS = {   # mint ufficiali su Solana (gli unici ammessi con questi ticker)
    "So11111111111111111111111111111111111111112",    # SOL/WSOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",   # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",   # USDT
    "9gP2kCy3wA1ctvYWQk75guqXuHfrEomqydHLtcTCqiLa",   # BNB (Wormhole)
    "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh",   # WBTC (Wormhole)
    "cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij",    # cbBTC
    "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs",   # WETH (Wormhole)
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",    # JUP
    "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn",    # PUMP
    "98sMhvDwXj1RQi5c5Mndm3vPe9cBqPrbLaufMXFNMh5g",   # HYPE
    "6p6xgHyF7AeE6TZkSmFsko444wqoP15icUSqi2jfGiPN",   # TRUMP
}


# v8.4d: nomi scritti per esteso dei grandi asset/exchange (confronto esatto dopo la normalizzazione, con prefissi
# "THE"/"WRAPPED" e suffissi "COIN"/"TOKEN"/"ONSOL..." ammessi): "Bitcoin" (HytpiyuR..pump) era passato come simbolo
MAJOR_NAMES = {"BITCOIN", "ETHEREUM", "ETHER", "SOLANA", "BINANCE", "BINANCECOIN", "TETHER", "USDCOIN", "RIPPLE",
               "DOGECOIN", "CARDANO", "TRON", "TONCOIN", "AVALANCHE", "POLKADOT", "CHAINLINK", "LITECOIN",
               "BITCOINCASH", "STELLAR", "HYPERLIQUID", "JUPITER", "PUMPFUN", "COINBASE", "KRAKEN", "BYBIT",
               "KUCOIN", "BITGET", "OKX", "MEXC", "SUI"}
_NAME_SUFFIX = ("TOKEN", "COIN", "ONSOLANA", "ONSOL")
# v8.4d: ticker/nomi di AZIONI (non bloccati: solo annotati negli eventi 'ticker_azione' con l'esito)
STOCK_TICKERS = {"AAPL", "APPLE", "NVDA", "NVIDIA", "TSLA", "TESLA", "MSFT", "MICROSOFT", "AMZN", "AMAZON", "GOOGL",
                 "GOOG", "GOOGLE", "ALPHABET", "META", "NFLX", "NETFLIX", "AMD", "INTC", "INTEL", "COIN", "MSTR",
                 "MICROSTRATEGY", "GME", "GAMESTOP", "AMC", "PLTR", "PALANTIR", "SPY", "QQQ", "SPX", "NIO"}


def _norm_tk(x):
    return re.sub(r"[^A-Z0-9]", "", str(x or "").upper())


def ticker_impostor(symbol, mint, name=None):
    """v8.4c: ticker di un grande asset/exchange su un mint non ufficiale -> ticker, altrimenti None.
    v8.4d: anche simbolo o nome scritti per esteso (Bitcoin, Ethereum, Solana, Binance, Tether...)."""
    if mint in OFFICIAL_MINTS:
        return None
    for raw in (symbol, name):
        t = _norm_tk(raw)
        if not t:
            continue
        if t in MAJOR_TICKERS or t in MAJOR_NAMES:
            return t
        x = re.sub(r"^(?:THE|WRAPPED)", "", t)
        cands = {x} | {x[:-len(sf)] for sf in _NAME_SUFFIX if x.endswith(sf) and len(x) > len(sf)}
        if any(c in MAJOR_NAMES or (c in MAJOR_TICKERS and len(c) >= 3) for c in cands):
            return t
    return None


def stock_ticker(symbol, name=None):
    """v8.4d: ticker o nome di un'azione (AAPL, Apple...) -> ticker normalizzato, altrimenti None. Solo annotazione."""
    for raw in (symbol, name):
        t = _norm_tk(raw)
        if t in STOCK_TICKERS:
            return t
    return None


def note_stock_outcome(kind, sym, mint, pnl, why, name=None):
    tk = stock_ticker(sym, name)
    if tk:
        write_event("Learner", "ticker_azione", f"{kind} {sym} (ticker azione {tk}) chiuso {pnl:+.1f}%: {why}",
                    {"mint": mint, "ticker": tk, "pnl_pct": round(fnum(pnl), 2), "kind": kind})
    return tk


# ----------------------------------------------------------------------- v8.2: cluster v2, honeypot, etichette
ST_REF = [None]   # stato corrente (per path_tick -> label_watch_tick)
CL2 = {"tasks": {}, "cache": {}, "sem_bg": None, "sem_rt": None}
SUSPECT_LABELS = ("bad_cluster", "suspected_rug")
# casi gia osservati: registrati UNA volta come esempi per il Learner (chiave 'cluster_hard' + etichetta)
LABEL_SEEDS = [
    {"id": "beast-2026-10-30", "mint": "HRJPbDu8h4cH2gT1rKcmzNcvzX1M7jHKogS7zRJkpump", "sym": "BEAST",
     "label": "rug", "from_label": "suspected_rug", "pnl_pct": -98.9, "rug": True,
     "note": "cluster sybil: 1997 wallet con saldo identico al 76.4% dell'offerta, distributore dedicato "
             "9dxrgj..DKn3 (fee payer e mittente per 132 dei top 150); non un falso positivo fomo/CEX. "
             "Honeypot NO (vendibile, impatto 1.9%). Shadow +375% al massimo (22:18), poi alle 22:19:07 "
             "dump coordinato: 100 vendite nello stesso secondo, ~309 SOL tolti dal pool (top holder 2wpaTV "
             "8.6% = 56 SOL; H8fEoy 50.7; FkH6xB 33.6; 8QzUkG 26.2); prezzo -99.7%, liquidita da $68.6k a $1.8k. "
             "LP non rimossa: dump, non rug di liquidita."},
    {"id": "dessa-2026-10-30", "mint": "npz2FhoufQ7pKqhGpaipYWacrAkidyW2yDpVWSvpump", "sym": "DESSA",
     "label": "cluster", "pnl_pct": None, "rug": False,
     "note": "comprata alle 20:14 (v6, prima che esistesse il controllo cluster); l'analisi v7 a posteriori "
             "la dava al limite (78% top holder nuovi, 2 reti). Ricontrollo v8.2 alle 22:22: 15.6k holder, "
             "gruppo massimo 0.38%, nessuno sciame (foto successiva, non prova di pulizia: vedi dessa-2026-10-30-cattivo). Posizione tenuta con ricontrollo "
             "ogni CLUSTER_HELD_RECHECK_MIN."},
    {"id": "dessa-2026-10-30-cattivo", "mint": "npz2FhoufQ7pKqhGpaipYWacrAkidyW2yDpVWSvpump", "sym": "DESSA",
     "label": "cluster", "pnl_pct": None, "rug": False,   # esito del trade (+1.92%) gia registrato: niente doppio conteggio
     "note": "TOKEN CATTIVO (Kiwi, 30/10 23:38): confermata la lezione 'cluster': comprata alle 20:14 OLTRE il limite "
             "cluster (v7: 78% top holder nuovi, 2 reti). Chiusa alle 23:15 per tempo massimo a +1.92% (picco +4.9%): "
             "l'esito positivo NON la rende un buon token e non va usata come esempio 'deve restare pulito'. "
             "v8.4b: wallet nuovi 87% dei controllati (soft), eta 4 g con vol 1h/liq 0.11 = scartata dalla regola eta."},
    {"id": "pillsey-2026-10-30", "mint": "J5jxG3PFaWpj6BLEQExyC6FSqSvaTKNdQ9G3sZwBpump", "sym": "PILLSEY",
     "label": "cluster", "pnl_pct": None, "rug": False,   # esito shadow gia registrato dal Learner
     "note": "solo shadow: chiusa a -13.7% alle 22:46, riaperta alle 22:56. Bubblemaps: 13+ cluster ~33% (87 wallet "
             "8.62%, 71 wallet 6.82%). v8.3 la dava soft (gruppo 10.7%/43 wallet, collegati 16.8%, 150/3665 holder). "
             "Causa: l'hub 9Ja2iw (>=1000 tx/h) che finanzia in raffica i wallet era trattato da infrastruttura. "
             "v8.4: 'hub burst' (60 wallet nuovi, acquisto entro 1h, quote uguali 0.18%) + stima estesa ~150 wallet "
             "al 25.0% = HARD."},
    {"id": "bnb-2026-10-30", "mint": "EYqygbhZjP7EbM4fSn8devctnksAvPAD1Bt57XBFpump", "sym": "BNB",
     "label": "cluster", "pnl_pct": None, "rug": False,   # posizione paper aperta: esito registrato dalle uscite normali
     "note": "TOKEN CATTIVO (Kiwi, 30/10 23:54), 5a lezione negativa: 'Broke Nigga Baddie', ticker BNB impostore "
             "(non il BNB Wormhole 9gP2kC), eta ~2 h, liq ~$67k, +1089% in 6 h. Bubblemaps: centinaia di holder "
             "con esattamente lo 0.10% (sciame) e 5-6 cluster a stella da 8-12 wallet. v8.4b la dava soft (collegati "
             "6.1%, 40% nuovi: tutti i 60 controllati finanziati dall'hub 36MESj); honeypot 'sconosciuto' per errore "
             "GoPlus (TypeError) e comprata a size x0.2. v8.4c: quote uniformi (125 wallet al 12.3%) = HARD, "
             "ticker impostore = blocco, honeypot sconosciuto = niente acquisto. MAI esempio pulito."},
    {"id": "glitch-2026-10-30", "mint": "8Xg89xT8Ve34sKiYqYuMs46mycREsCA2F362UscRpump", "sym": "GLITCH",
     "label": "cluster", "pnl_pct": None, "rug": False,
     "note": "SOSPETTO (Kiwi). Eta 14 g, Token-2022 senza transfer fee (Taxes 1.25% = fee pool). 5 top holder "
             "nuovi finanziati da MEXC 0.2-4 h prima di comprare nei primi 47 min, quote simili 1.3-2.9% = 11.1%; "
             "GMGN: phishing 13.65%, bundler 2.73%, top10 26.74%. v8.4: regola CEX-fresh = HARD; eta 14 g = "
             "penalita forte."},
    {"id": "hyperchan-2026-10-31-rug", "mint": "3ApVr1xphYMpi9dn3rpqU4hwvpmCQDVwxET1rdCDpump", "sym": "HYPERCHAN",
     "label": "rug", "pnl_pct": None, "rug": True,   # trade paper (+6.4%, 30/10 20:06) e shadow gia registrati: niente doppio conteggio
     "note": "RUG (Kiwi, 31/10 07:04), 6a lezione negativa: GMGN alle 07:02 prezzo -99.84%, liquidita $1.33k, mcap "
             "$2.58k, storico creatore 'Rug pull' 51.73% (stima Kiwi ~55%). Schema: 59 dei 60 holder controllati "
             "finanziati da un solo wallet BXTDKd (gruppo stimato ~148 wallet al 14.3%); v8.4c la dava soft. "
             "Shadow 00:04 chiuso a -100 alle 01:04 = DATO NON VALIDO (pausa processo): GeckoTerminal dava -55% "
             "alle 00:45 e ~-43% alle 01:00-01:05; il rug completo e arrivato dopo. v8.4e: regola 'un solo "
             "finanziatore >= 50% dei controllati' = HARD. MAI controllo pulito o vincente."},
    # v8.4f: casi Kiwi 31/10 (solo registro: pnl None -> nessun effetto sul Learner; MAI regole rilassate da qui)
    {"id": "dacity-2026-10-31-buono", "mint": "nxPqZ4F7ux8Vph5NFu25rEP6Kx9Gr1U6Fmj36Rjpump", "sym": "Dacity",
     "label": "buono_kiwi", "pnl_pct": None, "rug": False,
     "note": "CONTROLLO POSITIVO confermato da Kiwi (31/10 07:39): Dacity Coin, pumpswap ~20 g, liq ~$392k, mcap "
             "~$8.5M. Il bot lo scartava per reti RugCheck (bundle 30.6%, hard) e regole eta/attivita."},
    {"id": "gta6-2026-10-31-dacf", "mint": "4kjruqGHDsSqkMdFfCjV7Vb62HPFhtXyWc2t6JpQzDnz", "sym": "GTA6",
     "label": "probabile_buono", "pnl_pct": None, "rug": False,
     "note": "caso Kiwi 31/10: probabile buono, DA CONFERMARE (GTA VI, Meteora DLMM, ~85 g, mcap ~$10.6M)."},
    {"id": "stonk-2026-10-31-dacf", "mint": "6GmAFSYs4gk3FDao5FzzySQpPZaWsa4rUJHacpMpUNgx", "sym": "STONK",
     "label": "probabile_buono", "pnl_pct": None, "rug": False,
     "note": "caso Kiwi 31/10: probabile buono, DA CONFERMARE (Meteora/Orca, ~87 g, mcap ~$103M)."},
    {"id": "float-2026-10-31-dacf", "mint": "Bv1a7cEj5aRnJQhMw4kRQqAFesMSd5LwcDnUzq52BMmr", "sym": "FLOAT",
     "label": "probabile_buono", "pnl_pct": None, "rug": False,
     "note": "caso Kiwi 31/10: probabile buono, DA CONFERMARE (Raydium CPMM, ~56 h, mcap ~$663k)."},
    {"id": "neet-2026-10-31-dacf", "mint": "Ce2gx9KGXJ6C9Mp5b5x1sn9Mg87JwEbrQby4Zqo3pump", "sym": "neet",
     "label": "probabile_buono", "pnl_pct": None, "rug": False,
     "note": "caso Kiwi 31/10: probabile buono, DA CONFERMARE (pumpswap ~552 g, liq ~$1.6M, mcap ~$25.7M)."},
    {"id": "lumi-2026-10-31-dacf", "mint": "HvitmAeh6TkToQonoiNwqWcrZsYdYiaSZHP7yMmspump", "sym": "LUMI",
     "label": "probabile_buono", "pnl_pct": None, "rug": False,
     "note": "caso Kiwi 31/10: probabile buono, DA CONFERMARE (pumpswap ~6 h, mcap ~$700k, +1509% 24h)."},
]


def apply_label_seeds(st):
    done = {x.get("id") for x in st.label_log}
    for sd in LABEL_SEEDS:
        if sd["id"] in done:
            continue
        st.label_log.append(dict(sd, t=int(time.time()), kind="seed"))
        if sd.get("pnl_pct") is not None:
            record_outcome(st, ["cluster_hard", "label:" + sd["label"]], sd["pnl_pct"], sd["rug"], "label-seed")
        st.lessons.append(f"{datetime.now(TZ):%d/%m %H:%M} caso '{sd['label']}' {sd['sym']}: {sd['note'][:150]}")
        write_event("Learner", "label_seed", f"caso etichettato {sd['sym']} = '{sd['label']}'",
                    {"mint": sd["mint"], "label": sd["label"], "note": sd["note"]})


# v8.5: casi da seguire fino alla fine (solo osservazione, nessun acquisto, nessun effetto sul Learner).
# Ogni CASE_WATCH_SEC un controllo DexScreener gratuito di prezzo e liquidita, fino al rug o a 'days' giorni.
CASE_WATCH = [
    {"id": "leverhedge-2026-10-31", "mint": "FWatMYieKhRuJ1yuTcmVG1njRhZWwqHwjR11pBympump", "sym": "Leverhedge",
     "label": "caso_stelle_aperto", "days": 7,
     "note": "caso stelle aperto (Kiwi 31/10): Bubblemaps ~14 cluster a stella, GMGN rischio rug 46.9%, top10 4.76%, "
             "+25.000% in 24 h. Ne buono ne cattivo: si segue fino al rug o 7 giorni."},
]
CASE_MINTS = {c["mint"] for c in CASE_WATCH}
CASE_WATCH_PATH = os.path.join(DATA_DIR, "case_watch.json")
CASE_WATCH_LOG = os.path.join(DATA_DIR, "case_watch.jsonl")
CFG.setdefault("CASE_WATCH_SEC", 3600)
CFG.setdefault("CASE_RUG_DROP_PCT", 80.0)      # rug: prezzo -80% dal massimo...
CFG.setdefault("CASE_RUG_LIQ_DROP_PCT", 80.0)  # ...o liquidita -80% dal massimo...
CFG.setdefault("CASE_RUG_MIN_LIQ", 1000.0)     # ...o liquidita sotto $1k (o nessuna coppia)


def case_watch_load():
    try:
        with open(CASE_WATCH_PATH) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def case_watch_save(d):
    tmp = CASE_WATCH_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, CASE_WATCH_PATH)


def apply_case_watch(st, now=None):
    """Registra i casi aperti: etichetta neutra (sostituisce un eventuale 'bad_cluster' automatico) e niente
    promozione automatica a 'rug' dal label_watch (quella conterebbe come esito)."""
    now, d = now or time.time(), case_watch_load()
    for cs in CASE_WATCH:
        st.labels[cs["mint"]] = cs["label"]
        st.label_watch.pop(cs["mint"], None)
        if cs["id"] not in d:
            d[cs["id"]] = {"mint": cs["mint"], "sym": cs["sym"], "label": cs["label"], "t0": now,
                           "until": now + cs["days"] * 86400, "status": "aperto", "pk": 0.0, "lpk": 0.0, "n": 0}
            write_event("Learner", "case_open", f"caso {cs['sym']} = '{cs['label']}' seguito per {cs['days']} giorni",
                        {"mint": cs["mint"], "note": cs["note"]})
    case_watch_save(d)
    return d


def case_watch_step(c, m, now):
    """Aggiorna un caso con un dato DexScreener (m=None: nessuna coppia). Restituisce la riga di log."""
    c.setdefault("status", "aperto")
    price, liq = (fnum(m.get("price")), fnum(m.get("liq"))) if m else (0.0, 0.0)
    if m and not m.get("liq_known", True):
        liq = c.get("last_l", 0.0)
    c["pk"], c["lpk"] = max(c.get("pk", 0.0), price), max(c.get("lpk", 0.0), liq)
    dp = (1 - price / c["pk"]) * 100 if c["pk"] > 0 else 0.0
    dl = (1 - liq / c["lpk"]) * 100 if c["lpk"] > 0 else 0.0
    c.update(last=price, last_l=liq, last_t=now, n=c.get("n", 0) + 1)
    row = {"t": datetime.fromtimestamp(now, TZ).strftime("%Y-%m-%d %H:%M"), "id": None, "sym": c["sym"],
           "price": price, "liq": round(liq, 2), "mcap": (m or {}).get("mcap"), "chg_h24": (m or {}).get("chg_h24"),
           "from_peak_pct": round(-dp, 1), "liq_from_peak_pct": round(-dl, 1)}
    if c["n"] > 1 and (not m or dp >= CFG["CASE_RUG_DROP_PCT"] or dl >= CFG["CASE_RUG_LIQ_DROP_PCT"]
                       or liq < CFG["CASE_RUG_MIN_LIQ"]):
        c["status"], c["end_t"] = "rug", now
        c["end_why"] = "nessuna coppia" if not m else (f"prezzo {-dp:.0f}% dal massimo, liquidita {-dl:.0f}% "
                                                       f"(${liq:,.0f})")
    elif now >= c["until"]:
        c["status"], c["end_t"], c["end_why"] = "chiuso_7g", now, f"7 giorni passati, prezzo {-dp:+.0f}% dal massimo"
    row["status"] = c["status"]
    return row


# ----------------------------------------------------------------------- v8.5: token ancora sulla bonding curve
# 31/10: 186 token scartati da mezzanotte per "liquidita $0" perche ancora sulla curva pump.fun (DexScreener da 0),
# tra cui iCoin (poi x41). Ora: in watchlist, ricontrollo UNA volta sola dopo la migrazione (entro 6 ore), al massimo
# CURVE_RECHECK_MAX_H all'ora. Alla ricontrollata il token rientra dall'inizio della pipeline: nessuna eccezione.
CURVE_WATCH_PATH = os.path.join(DATA_DIR, "curve_watch.json")
CFG.setdefault("CURVE_WATCH", True)
CFG.setdefault("CURVE_WATCH_TTL_SEC", 6 * 3600)
CFG.setdefault("CURVE_WATCH_CHECK_SEC", 300)
CFG.setdefault("CURVE_RECHECK_MAX_H", 12)
CFG.setdefault("CURVE_WATCH_MAX", 600)
CURVE = {"d": None, "sent": []}


def curve_load():
    if CURVE["d"] is None:
        try:
            CURVE["d"] = json.load(open(CURVE_WATCH_PATH))
        except Exception:  # noqa: BLE001
            CURVE["d"] = {}
        CURVE["d"].setdefault("watch", {}), CURVE["d"].setdefault("done", {})
    return CURVE["d"]


def curve_save():
    try:
        tmp = CURVE_WATCH_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(CURVE["d"], f)
        os.replace(tmp, CURVE_WATCH_PATH)
    except Exception:  # noqa: BLE001
        pass


def curve_watch_add(mint, m, now=None):
    """Token scartato per liquidita 0 mentre e ancora sulla curva pump.fun -> watchlist (una sola volta)."""
    if not CFG.get("CURVE_WATCH") or mint in CASE_MINTS or not str(mint).endswith("pump"):
        return False
    if m.get("dex") not in ("pumpfun", "") and fnum(m.get("liq")) > 0:
        return False
    d, now = curve_load(), now or time.time()
    if mint in d["watch"] or mint in d["done"]:
        return False
    if len(d["watch"]) >= CFG["CURVE_WATCH_MAX"]:
        d["watch"].pop(min(d["watch"], key=lambda k: d["watch"][k]["t"]))
    d["watch"][mint] = {"t": now, "sym": str(m.get("symbol", ""))[:12]}
    curve_save()
    return True


def curve_watch_step(st, market, now=None):
    """market = {mint: pairs DexScreener}. Ritorna i mint rimessi in pipeline. Pura sul contenuto (test)."""
    d, now = curve_load(), now or time.time()
    CURVE["sent"] = [t for t in CURVE["sent"] if now - t < 3600]
    out = []
    for mint, w in list(d["watch"].items()):
        if now - w["t"] > CFG["CURVE_WATCH_TTL_SEC"]:
            d["watch"].pop(mint)
            d["done"][mint] = {"t": now, "esito": "scaduto (6 h senza migrazione)"}
            continue
        pairs = market.get(mint)
        if not pairs:
            continue
        m = summarize(pairs)
        if m.get("dex") == "pumpfun" or m["liq"] <= 0:
            continue   # ancora sulla curva
        if len(CURVE["sent"]) >= int(CFG["CURVE_RECHECK_MAX_H"]):
            break      # tetto orario: gli altri aspettano (entro le 6 ore)
        d["watch"].pop(mint)
        d["done"][mint] = {"t": now, "esito": f"migrato su {m.get('dex')}, liq ${m['liq']:,.0f}: ricontrollo"}
        CURVE["sent"].append(now)
        st.processed.pop(mint, None)
        st.cooldown.pop(mint, None)
        if mint not in st.watch and mint not in st.positions:
            st.watch[mint] = {"seen": now, "sym": w["sym"], "src": "curve_recheck", "prio": False}
            LAST_SRC[mint] = "curve_recheck"
        st.stats["curve_recheck"] = st.stats.get("curve_recheck", 0) + 1
        out.append(mint)
    for k in [k for k, v in d["done"].items() if now - v["t"] > 86400]:
        d["done"].pop(k)
    curve_save()
    return out


async def curve_watch_agent(st, http):
    while True:
        await asyncio.sleep(CFG["CURVE_WATCH_CHECK_SEC"])
        try:
            d = curve_load()
            if not d["watch"]:
                continue
            market = await dex_batch(http, list(d["watch"]))   # DexScreener, gratis
            for mint in curve_watch_step(st, market):
                log(st, "Scout", "info", f"{d['done'][mint]['esito']} ({mint[:6]}...): rientra dalla pipeline completa",
                    mint=mint)
        except Exception as e:  # noqa: BLE001
            log(st, "Scout", "error", f"watchlist curva: errore ({type(e).__name__}), continuo")


async def case_watch_agent(st, http):
    while True:
        d = case_watch_load()
        live = {k: c for k, c in d.items() if c.get("status") == "aperto"}
        if live:
            mk = await dex_batch(http, [c["mint"] for c in live.values()])
            now = time.time()
            for k, c in live.items():
                pairs = mk.get(c["mint"])
                m = summarize(pairs) if pairs else None
                if m is not None:
                    m["mcap"] = fnum((max(pairs, key=lambda p: fnum((p.get("liquidity") or {}).get("usd")))
                                      ).get("marketCap"))
                    m["chg_h24"] = fnum((max(pairs, key=lambda p: fnum((p.get("liquidity") or {}).get("usd")))
                                         .get("priceChange") or {}).get("h24"))
                row = case_watch_step(c, m, now)
                row["id"] = k
                with open(CASE_WATCH_LOG, "a") as f:
                    f.write(json.dumps(row) + "\n")
                if c["status"] != "aperto":
                    write_event("Learner", "case_close", f"caso {c['sym']} chiuso: {c['status']} ({c.get('end_why')})",
                                {"mint": c["mint"], "label": c["label"]})
            case_watch_save(d)
        await asyncio.sleep(CFG["CASE_WATCH_SEC"])


def label_watch_tick(st, mint, price, liq, now=None):
    """'bad_cluster'/'suspected_rug' -> 'rug' se il prezzo scende >= RUG_PROMOTE_DROP_PCT dal massimo o la
    liquidita >= RUG_PROMOTE_LIQ_DROP_PCT dal massimo. Registra quando e come (dump o rimozione LP)."""
    lab = st.labels.get(mint)
    if lab not in SUSPECT_LABELS:
        return None
    price, liq, now = fnum(price), fnum(liq), now or time.time()
    if price <= 0:
        return None
    w = st.label_watch.setdefault(mint, {"label": lab, "t0": now, "pk": price, "pk_t": now, "lpk": liq,
                                         "last": price, "last_l": liq, "last_t": now})
    dp = (1 - price / w["pk"]) * 100 if w["pk"] > 0 else 0.0
    dl = (1 - liq / w["lpk"]) * 100 if w["lpk"] > 0 and liq > 0 else 0.0
    out = None
    if dp >= CFG["RUG_PROMOTE_DROP_PCT"] or dl >= CFG["RUG_PROMOTE_LIQ_DROP_PCT"]:
        how = "rimozione LP" if dl >= CFG["RUG_PROMOTE_LIQ_DROP_PCT"] and dp < 30 else "dump (vendite massicce)"
        ev = {"id": f"promote-{mint[:6]}-{int(now)}", "mint": mint, "label": "rug", "from_label": lab,
              "kind": "promote", "t": int(now), "how": how, "drop_from_peak_pct": round(dp, 1),
              "liq_drop_pct": round(dl, 1), "peak": w["pk"], "peak_t": int(w["pk_t"]), "price": price, "liq": liq,
              "last_ok_t": int(w["last_t"]), "window": [int(w["last_t"]), int(now)]}
        st.labels[mint] = "rug"
        st.label_log.append(ev)
        st.label_watch.pop(mint, None)
        when = datetime.fromtimestamp(now, TZ).strftime("%d/%m %H:%M")
        st.lessons.append(f"{when} '{lab}' diventato 'rug' ({how}): -{dp:.0f}% dal massimo, liquidita -{dl:.0f}%")
        log(st, "Learner", "label_promote", f"{mint[:6]}.. '{lab}' -> 'rug': {how}, -{dp:.0f}% dal massimo, "
            f"liquidita -{dl:.0f}% (tra {datetime.fromtimestamp(w['last_t'], TZ):%H:%M:%S} e {when})",
            **{k: v for k, v in ev.items() if k not in ("kind", "mint")}, mint=mint)
        try:   # chi ha venduto o tolto la LP: analisi on-chain fatta dopo (coda, nessun costo qui)
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources",
                                   "rug_forensics_queue.jsonl"), "a") as fh:
                fh.write(json.dumps(ev) + "\n")
        except Exception:  # noqa: BLE001
            pass
        out = ev
    else:
        if price > w["pk"]:
            w["pk"], w["pk_t"] = price, now
        w["lpk"] = max(w["lpk"], liq)
        w["last"], w["last_l"], w["last_t"] = price, liq, now
    if len(st.label_watch) > 200:
        st.label_watch.pop(min(st.label_watch, key=lambda k: st.label_watch[k]["last_t"]), None)
    return out


def cluster2_on():
    return bool(CFG.get("CLUSTER_V2") and SRC.get("cluster2"))


def cluster2_brief(cl):
    if not cl:
        return None
    keys = ("level", "why", "max_group_pct", "max_group_wallets", "linked_pct", "cluster_pct", "cluster_infra_pct",
            "infra_group_pct", "infra_group_fresh", "swarm_pct", "swarm_wallets", "fresh_share", "scanned",
            "holders_total", "calls", "sec",
            # review Claude (compito 2): feature delle regole v8.4c/e, prima non registrate negli eventi: senza
            # di loro una regola nuova non si puo calibrare sullo storico
            "uniform", "single_funder", "stars", "cexf_pct", "cexf_best", "recv_pct", "multi_groups", "combo",
            "smart")   # v8.5s: wallet smart money tra gli holder (SHADOW: solo registrato)
    return {k: cl.get(k) for k in keys if k in cl}


def smart_annotate(res, sec):
    """v8.5s (SHADOW): aggiunge al risultato cluster v2 il creatore se e un wallet della lista (di solito un 'dev').
    Nessuna chiamata di rete, nessun effetto su punteggio/size/acquisti. Mai eccezioni."""
    try:
        if CFG.get("SMART_WALLETS_MODE") == "off" or not SRC.get("smart"):
            res.pop("smart", None)
            return res
        sm = res.get("smart")
        if not isinstance(sm, dict):
            sm = res["smart"] = SRC["smart"].match({})
        cd = SRC["smart"].creator_info((sec or {}).get("creator"))
        if cd:
            sm["creator_dev"] = cd
    except Exception:  # noqa: BLE001
        pass
    return res


def smart_signal(res):
    try:
        return SRC["smart"].signal((res or {}).get("smart")) if SRC.get("smart") else None
    except Exception:  # noqa: BLE001
        return None


def cluster2_signal(cl):
    lv = (cl or {}).get("level", "unknown")
    pct = fnum((cl or {}).get("cluster_pct"))
    b = "0-5" if pct < 5 else "5-10" if pct < 10 else "10-20" if pct < 20 else "20+"
    return f"cluster2:{lv}" + ("" if lv == "unknown" else f":{b}")


async def _cl2_post(http, url, body, tier="core"):
    """POST JSON-RPC senza il passo fisso di Http (la cadenza la decide cluster_v2); 429 = None.
    v8.2: verso gli RPC pubblici prova prima Helius (core: holder/mint; bulk: analisi per holder, entro il budget)."""
    if not hasattr(http, "s") or not hasattr(http, "_wait"):
        return await http.post(url, body)
    if urlparse(url).netloc in PUBLIC_RPC_HOSTS:
        d = await helius_first(http.s, body, tier)
        if d is not None:
            return d
    await http._wait(url)
    try:
        async with http.s.post(url, json=body) as r:
            if r.status != 200:
                return None
            return await r.json(content_type=None)
    except Exception:  # noqa: BLE001
        return None


async def _rc_sec(http, mint):
    """Pool/curve/locker da escludere e programma del token, dal report RugCheck (per posizioni gia aperte)."""
    r = await http.get(f"https://api.rugcheck.xyz/v1/tokens/{mint}/report")
    if not isinstance(r, dict):
        return None
    ex = set()
    for mk in r.get("markets") or []:
        for v in list(mk.values()) + list((mk.get("lp") or {}).values()):
            if isinstance(v, str):
                ex.add(v)
    for a, i in (r.get("knownAccounts") or {}).items():
        if isinstance(i, dict) and str(i.get("type", "")).upper() in ("AMM", "LOCKER", "BURN", "PROGRAM"):
            ex.add(a)
    return {"exclude": sorted(ex), "token_program": r.get("tokenProgram")}


async def cluster2_scan(st, http, mint, sec, sym="?", rt=False):
    hit = CL2["cache"].get(mint)
    if hit and time.time() - hit[0] < CFG["CLUSTER_CACHE_SEC"]:
        return hit[1]
    key = "sem_rt" if rt else "sem_bg"
    if CL2[key] is None:
        CL2[key] = asyncio.Semaphore(1)
    async with CL2[key]:
        hit = CL2["cache"].get(mint)
        if hit and time.time() - hit[0] < CFG["CLUSTER_CACHE_SEC"]:
            return hit[1]
        cfg = {"BUDGET_SEC": CFG["CLUSTER2_BUDGET_SEC"],
               "GROUP_HARD_PCT": min(20.0, fnum(CFG["CLUSTER_GROUP_HARD_PCT"], 20.0)),
               "FAM_MODE": CFG["CLUSTER_FAM_MODE"], "FAM_HARD_N": int(CFG["CLUSTER_FAM_HARD_N"])}
        try:
            res = await SRC["cluster2"].scan(lambda u, b: _cl2_post(http, u, b, "bulk"), mint,
                                             exclude=sec.get("exclude") or [], token_program=sec.get("token_program"),
                                             cfg=cfg)
        except Exception as e:  # noqa: BLE001
            res = {"level": "unknown", "why": f"errore {type(e).__name__}"}
    res = res or {"level": "unknown", "why": "nessun risultato"}
    smart_annotate(res, sec)
    if res.get("level") != "unknown":   # un 'unknown' non resta in cache: al prossimo giro si riprova
        CL2["cache"][mint] = (time.time(), res)
    for k in [k for k, v in CL2["cache"].items() if time.time() - v[0] > CFG["CLUSTER_CACHE_SEC"]]:
        CL2["cache"].pop(k, None)
    br = cluster2_brief(res)
    log(st, "Analyst", "cluster2", f"{sym} cluster v2 {res['level']}: {res.get('why') or 'nessun gruppo'} "
        f"(gruppo {fnum(res.get('max_group_pct')):.1f}%, via exchange/app {fnum(res.get('cluster_infra_pct')):.1f}%, "
        f"sciame {fnum(res.get('swarm_pct')):.1f}%)", mint=mint, **{k: v for k, v in br.items() if k != "why"})
    sm = res.get("smart") or {}
    if CFG.get("SMART_WALLETS_MODE") != "off" and (sm.get("n") or sm.get("creator_dev")):
        log(st, "Analyst", "smart_wallets", f"{sym} smart money (SHADOW, nessun effetto): {sm.get('trader_groups', 0)} "
            f"trader, {sm.get('dev', 0)} dev, {sm.get('unknown', 0)} altri" +
            (f", creatore = {sm['creator_dev']['name']}" if sm.get("creator_dev") else "") +
            (f" ({', '.join(sm.get('names') or [])})" if sm.get("names") else ""), mint=mint, smart=sm)
    sh = st.shadow_trades.get(mint)
    if sh is not None:   # dati per il Learner anche sugli shadow (es. scarti per momentum come BEAST)
        sh["cluster2"] = br
        sig = cluster2_signal(res)
        if sig not in (sh.get("signals") or []):
            sh.setdefault("signals", []).append(sig)
        ssig = smart_signal(res)   # v8.5s: tag 'smart:' solo informativo (non e un BONUS_KEYS)
        if ssig and ssig not in (sh.get("signals") or []):
            sh.setdefault("signals", []).append(ssig)
    if res["level"] == "hard":
        st.labels.setdefault(mint, "bad_cluster")
    return res


def cluster2_start(st, http, mint, sec, sym="?", rt=False):
    t = CL2["tasks"].get(mint)
    if t is not None and not t.done():
        return t
    hit = CL2["cache"].get(mint)
    if not rt and hit and time.time() - hit[0] < CFG["CLUSTER_CACHE_SEC"]:
        return None
    for k in [k for k, v in CL2["tasks"].items() if v.done()]:
        CL2["tasks"].pop(k, None)
    if not rt and len(CL2["tasks"]) >= int(CFG["CLUSTER2_MAX_QUEUE"]):
        return None   # coda piena: lo shadow resta senza dati cluster (un trade vero lo rilancia con rt=True)
    t = asyncio.ensure_future(cluster2_scan(st, http, mint, sec, sym, rt))
    CL2["tasks"][mint] = t
    return t


async def cluster2_wait(st, http, mint, sec, task, sym="?"):
    """Risultato per un trade vero: attende al massimo CLUSTER2_WAIT_SEC. Mai None: 'unknown' se non riesce."""
    hit = CL2["cache"].get(mint)
    if hit and time.time() - hit[0] < CFG["CLUSTER_CACHE_SEC"]:
        return hit[1]
    t = task
    if t is not None and t.done() and (t.cancelled() or t.exception() is not None):
        t = None
    if t is None:
        t = cluster2_start(st, http, mint, sec, sym, rt=True)
    try:
        res = await asyncio.wait_for(asyncio.shield(t), CFG["CLUSTER2_WAIT_SEC"])
    except asyncio.TimeoutError:
        res = {"level": "unknown", "why": f"tempo scaduto ({CFG['CLUSTER2_WAIT_SEC']:g}s)"}
    except Exception as e:  # noqa: BLE001
        res = {"level": "unknown", "why": f"errore {type(e).__name__}"}
    return res if isinstance(res, dict) and res.get("level") else {"level": "unknown", "why": "nessun risultato"}


async def cluster2_held(st, http, mint, p):
    """Ricontrollo cluster di una posizione aperta: 'hard' -> uscita (o solo avviso) al prossimo giro del Risk."""
    try:
        sec = p.get("cl_sec") or await _rc_sec(http, mint)
        if not sec:
            return
        p.setdefault("cl_sec", sec)
        res = await cluster2_scan(st, http, mint, sec, p.get("symbol", "?"))
        p["cluster2"] = cluster2_brief(res)
        if res["level"] == "hard" and mint in st.positions:
            p["cluster_exit"] = res.get("why") or "cluster rigido"
            log(st, "Risk", "cluster_held", f"{p.get('symbol')}: cluster rigido sulla posizione aperta "
                f"({res.get('why')}) -> {CFG['CLUSTER_HELD_ACTION']}", mint=mint)
    except Exception as e:  # noqa: BLE001
        log(st, "Risk", "warn", f"ricontrollo cluster non riuscito ({type(e).__name__})")


async def _hp_quote(http, inp, out, amount, tries=4):
    """v8.2: solo PREVENTIVO Jupiter (mai swap), con riprova su 429/rete."""
    q, err = None, None
    for i in range(tries):
        q, err = await jup_quote(http, inp, out, amount)
        if q is not None or err == "no_route" or not (str(err).startswith("http 429") or str(err).startswith("rete") or str(err).startswith("http 5")):
            return q, err
        await asyncio.sleep(1.5 * (i + 1))
    return q, err


def age_penalty(m, atr=None, cfg=None):
    """v8.4: penalita graduata per eta della pool. -> (penalita, motivo_scarto|None, info).
    Sopra AGE_ACT_MIN_DAYS il token deve avere volume E volatilita alti, altrimenti si scarta;
    con attivita davvero alta la penalita si dimezza (mai azzerata)."""
    c = cfg or CFG
    am = m.get("age_min")
    if am is None:
        return 0.0, None, {"age_d": None}
    d = fnum(am) / 1440.0
    pen = (c["AGE_PEN_1D"] if d < 1 else c["AGE_PEN_1_3D"] if d < 3 else c["AGE_PEN_3_7D"] if d < 7
           else c["AGE_PEN_7_30D"] if d < 30 else c["AGE_PEN_30D"])
    vl = safe_div(fnum(m.get("vol_h1")), fnum(m.get("liq")), 0.0)
    vm = safe_div(fnum(m.get("vol_h1")), fnum(m.get("mcap")), 0.0)
    ch1 = abs(fnum(m.get("chg_h1")))
    a = fnum(atr, 0.0) if atr is not None else None
    info = {"age_d": round(d, 2), "vol_liq": round(vl, 2), "vol_mcap": round(vm, 3), "atr": a, "chg_h1": ch1}
    if d >= c["AGE_ACT_MIN_DAYS"]:
        act = vl >= c["AGE_MIN_VOL_LIQ"] or vm >= c["AGE_MIN_VOL_MCAP"]
        vol = (a is not None and a >= c["AGE_MIN_ATR"]) or ch1 >= c["AGE_MIN_CHG_H1"]
        if not (act and vol):
            return pen, (f"token vecchio ({d:.0f} g) poco attivo: vol 1h/liq {vl:.2f}, vol 1h/mcap {vm * 100:.1f}%, "
                         f"ATR {a if a is not None else 'n/d'}, var 1h {ch1:.0f}%"), info
        if vl >= c["AGE_EASE_VOL_LIQ"] and ((a is not None and a >= c["AGE_EASE_ATR"]) or ch1 >= c["AGE_EASE_CHG_H1"]):
            pen *= c["AGE_EASE_MULT"]
            info["eased"] = True
    info["pen"] = round(pen, 4)
    return pen, None, info


async def honeypot_check(st, http, mint, size_usd, sec):
    """Solo letture: account del mint (estensioni Token-2022 + authority), preventivo Jupiter di acquisto e di
    VENDITA alla nostra size (mai uno swap), rischi RugCheck gia letti dal Filter.
    Ritorna {level: ok|hard|unknown, why, ...}. 'unknown' non vale mai come ok."""
    hp = SRC["honeypot"]
    out = {"level": "ok", "why": ""}
    hard, soft, miss = [], [], []
    if http is None:
        return {"level": "unknown", "why": "nessuna connessione"}
    acc = None
    for url in ("https://solana-rpc.publicnode.com", CFG["RPC_URL"]):
        d = await _cl2_post(http, url, {"jsonrpc": "2.0", "id": 1, "method": "getAccountInfo",
                                        "params": [mint, {"encoding": "jsonParsed"}]})
        acc = ((d or {}).get("result") or {}).get("value") if isinstance(d, dict) else None
        if acc:
            break
    parsed = ((acc or {}).get("data") or {}).get("parsed") if isinstance((acc or {}).get("data"), dict) else None
    if parsed:
        h, s_, info = hp.mint_flags(parsed, {"MAX_TRANSFER_FEE_BPS": CFG["HONEYPOT_MAX_FEE_BPS"]})
        hard += h
        soft += s_
        out["fee_pct"] = round(fnum(info.get("fee_bps")) / 100.0, 3)
        out["extensions"] = info.get("extensions")
        out["token_program"] = (acc or {}).get("owner")
        if info.get("t22_flags"):   # v8.6 (B): estensioni Token-2022 sensibili, registrate
            out["t22_flags"] = info["t22_flags"]
            write_event("Executor", "t22_flags", f"Token-2022 {mint[:6]}..: {', '.join(info['t22_flags'])}",
                        {"mint": mint, "t22_flags": info["t22_flags"], "hard": list(h)})
    else:
        miss.append("account del mint non letto")
    # RugCheck (gia letto dal Filter): rug segnalato o authority = rigido
    if sec.get("rugged"):
        hard.append("RugCheck: token segnalato come rug")
    sol = sol_usd() if sol_known() else 0.0
    lam = int((size_usd / sol if sol > 0 else 0.1) * 1e9)
    q, err = await _hp_quote(http, WSOL, mint, lam)
    if err == "no_route":
        hard.append("nessun percorso d'acquisto su Jupiter")
    elif not q:
        miss.append(f"preventivo d'acquisto non disponibile ({err})")
    else:
        q2, err2 = await _hp_quote(http, mint, WSOL, int(q["outAmount"]))
        why, info = hp.sell_verdict(q2, err2, lam, {"SELL_IMPACT_HARD_PCT": min(25.0, CFG["HONEYPOT_SELL_IMPACT_PCT"])})
        out.update(info)
        if why:
            hard.append(why)
        elif not q2:
            miss.append(f"preventivo di vendita non disponibile ({err2})")
    if CFG.get("GOPLUS_CHECK") and SRC.get("goplus"):   # v8.4: errore = unknown, mai ok
        g = await SRC["goplus"].check(getattr(http, "s", http), mint)   # v8.4c: sessione aiohttp, non il wrapper Http
        out["goplus_flags"] = g.get("flags") or []
        out["goplus_level"] = g["level"]
        if g["level"] == "hard":
            hard += g.get("hard") or [g.get("why")]
        elif g["level"] == "unknown":
            miss.append(g.get("why") or "GoPlus non disponibile")
        else:
            soft += g.get("soft") or []
        out["fee_pct"] = max(fnum(out.get("fee_pct")), fnum(g.get("fee_pct")))
    out["soft"] = soft
    if hard:
        out.update(level="hard", why="; ".join(hard))
    elif miss:
        out.update(level="unknown", why="; ".join(miss))
    else:
        out["why"] = "; ".join(soft)
    return out


# ----------------------------------------------------------------------- v8.1: stop dinamico e feature d'ingresso
def crash_level(stop):
    """Uscita immediata per crollo relativa allo stop: CRASH_EXIT_PCT se lo stop e piu stretto, altrimenti
    1.5 x stop (come nel backtest di Mark). Con stop -12 resta -15 (identico a v8)."""
    stop = fnum(stop, CFG["STOP_LOSS_PCT"])
    return CFG["CRASH_EXIT_PCT"] if stop > CFG["CRASH_EXIT_PCT"] else round(1.5 * stop, 2)


def _std(xs):
    if len(xs) < 2:
        return None
    mu = sum(xs) / len(xs)
    return math.sqrt(sum((x - mu) ** 2 for x in xs) / (len(xs) - 1))


def vol_from_candles(rows, now=None, nbars=None):
    """ATR% (media di (high-low)/close, come sl_bt.py) sulle ultime nbars candele CHIUSE prima di now.
    Con meno candele usa quelle disponibili se >= DYN_ATR_MIN_BARS. Ritorna dict o None."""
    now = now or time.time()
    nbars = int(nbars or CFG["DYN_ATR_BARS"])
    cur = int(now // 60) * 60
    done = [r for r in (rows or []) if r[0] < cur and r[4] > 0]
    pre = done[-nbars:]
    if len(pre) < int(CFG["DYN_ATR_MIN_BARS"]):
        return None
    atr = sum((r[2] - r[3]) / r[4] for r in pre) / len(pre) * 100
    closes = [r[4] for r in done]
    r1 = [(closes[i] / closes[i - 1] - 1) * 100 for i in range(max(1, len(closes) - 10), len(closes))]
    c5 = closes[::-1][::5][::-1]  # una chiusura ogni 5 minuti, fino all'ultima
    r5 = [(c5[i] / c5[i - 1] - 1) * 100 for i in range(max(1, len(c5) - 12), len(c5))]
    ret5 = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 and closes[-6] > 0 else None
    return {"atr": round(atr, 3), "bars": len(pre), "src": "candles_1m" if len(pre) >= nbars else "candles_partial",
            "std1": None if _std(r1) is None else round(_std(r1), 3),
            "std5": None if _std(r5) is None else round(_std(r5), 3),
            "ret5": None if ret5 is None else round(ret5, 3)}


def vol_from_ticks(ticks, now=None):
    """Fallback senza candele: dai prezzi dei controlli gia fatti (ultimi 15 min). Ogni minuto -> (max-min)/ultimo;
    se c'e un solo prezzo al minuto, |rendimento| tra prezzi consecutivi riportato a 1 minuto."""
    now = now or time.time()
    pts = [(t, p) for t, p, *_ in (ticks or []) if now - t <= 900 and p > 0]
    if len(pts) < 3:
        return None
    by_min = {}
    for t, p in pts:
        by_min.setdefault(int(t // 60), []).append(p)
    rng = [(max(v) - min(v)) / v[-1] * 100 for v in by_min.values() if len(v) >= 2]
    rets = [abs(pts[i][1] / pts[i - 1][1] - 1) * 100 / math.sqrt(max(1.0, (pts[i][0] - pts[i - 1][0]) / 60))
            for i in range(1, len(pts))]
    atr = sum(rng) / len(rng) if len(rng) >= 2 else sum(rets) / len(rets)
    return {"atr": round(atr, 3), "bars": len(pts), "src": "ticks", "std1": None if _std(rets) is None else round(_std(rets), 3),
            "std5": None, "ret5": None}


def dyn_stop_calc(atr, base_stop=None):
    """(stop dinamico, size_mult). atr None -> stop base e size 1. Stop = -clamp(k x atr, |MIN|, |MAX|)."""
    base = fnum(base_stop, CFG["STOP_LOSS_PCT"])
    if atr is None or not math.isfinite(fnum(atr, float("nan"))) or fnum(atr) <= 0:
        return base, 1.0
    lo, hi = abs(CFG["DYN_STOP_MIN"]), abs(CFG["DYN_STOP_MAX"])
    stop = -min(max(lo, hi), max(min(lo, hi), CFG["DYN_STOP_ATR_MULT"] * fnum(atr)))
    mult = 1.0
    if CFG["DYN_STOP_SIZE_SCALE"]:
        mult = clamp(abs(base) / abs(stop), 0.05, 1.0)  # mai maggiorata (regola dell'Executor)
    return round(stop, 2), round(mult, 4)


def dyn_tp_calc(atr, tp1_base=None, act_base=None, dist_base=None):
    """v8.1b: (tp1, trail_act, trail_dist) dinamici; atr None -> valori base (quelli usati davvero)."""
    b = (fnum(tp1_base, CFG["TP1_PCT"]), fnum(act_base, CFG["TRAIL_ACTIVATE_PCT"]),
         fnum(dist_base, CFG["TRAIL_DISTANCE_PCT"]))
    if atr is None or not math.isfinite(fnum(atr, float("nan"))) or fnum(atr) <= 0:
        return b
    a = fnum(atr)
    f = lambda k, lo, hi: round(clamp(CFG[k] * a, min(CFG[lo], CFG[hi]), max(CFG[lo], CFG[hi])), 2)
    return (f("DYN_TP1_ATR_MULT", "DYN_TP1_MIN", "DYN_TP1_MAX"),
            f("DYN_TRAIL_ACT_ATR_MULT", "DYN_TRAIL_ACT_MIN", "DYN_TRAIL_ACT_MAX"),
            f("DYN_TRAIL_DIST_ATR_MULT", "DYN_TRAIL_DIST_MIN", "DYN_TRAIL_DIST_MAX"))


def _tag_val(tags, pref):
    for t in tags:
        if t.startswith(pref):
            try:
                return float(t.split(":")[-1])
            except ValueError:
                return None
    return None


async def entry_features(st, http, mint, m, signals=None, candles=None, soft=None, narr=None, accel=None,
                         size_mult=1.0, base_stop=None, allow_fetch=True, prio=False):
    """v8.1: feature d'ingresso (trade veri e shadow) + stop dinamico. Mai eccezioni, valori mancanti = None."""
    fe = {}
    try:
        tags = [t for t in (signals.split("|") if isinstance(signals, str) else (signals or [])) if t]
        # v8.4: ATR prima da Birdeye (candele 1m, solo candidati arrivati all'ingresso: trade paper o shadow),
        # poi GeckoTerminal (cache o richiesta), poi tick. prio=True per i trade paper (budget prioritario).
        v, be_rows, be_why = None, None, "spento"
        be = be_client(http) if CFG.get("BIRDEYE_ATR", True) and allow_fetch else None
        if be is not None:
            be_rows = await be.ohlcv_1m(mint, prio=prio)
            v = vol_from_candles(be_rows) if be_rows else None
            if v is not None:
                v = dict(v, src="birdeye_" + v["src"])
                be_why = "ok"
            else:
                be_why = "nessuna candela" if be_rows is not None else "budget/errore"
        rows = candles or cached_candles(m.get("pair"))
        if rows is None and allow_fetch and v is None:
            rows = await candles_for_entry(http, m.get("pair"))
        vg = vol_from_candles(rows) if rows else None
        if v is None:
            v = vg
        if v is None:
            v = vol_from_ticks(TICKS.get(mint))
        rows = rows or be_rows
        base = fnum(base_stop, CFG["STOP_LOSS_PCT"])
        dyn, dmult = dyn_stop_calc(v["atr"] if v else None, base)
        mode = str(CFG["DYN_STOP_MODE"])
        tp_mode = str(CFG["DYN_TP_MODE"])
        tpb = (fnum(CFG["TP1_PCT"]), fnum(CFG["TRAIL_ACTIVATE_PCT"]), fnum(CFG["TRAIL_DISTANCE_PCT"]))
        tpd = dyn_tp_calc(v["atr"] if v else None, *tpb) if tp_mode != "off" else (None, None, None)
        if accel is None and rows:
            w5 = window5(rows)
            if w5:
                accel = round(safe_div(w5["vol_now"], w5["vol_prev"], 99.0 if w5["vol_now"] > 0 else 0.0), 2)
        if accel is None:
            accel = round(safe_div(m.get("vol_m5", 0.0), fnum(m.get("vol_h1_main")) / 12, 0.0), 2)
        ks = [t for t in tags if t.startswith("kol:")]
        pr = PROJ_LAST.get(mint) or {}
        fe = {"atr_1m_10": v["atr"] if v else None, "atr_bars_used": v["bars"] if v else 0,
              "atr_src": v["src"] if v else "none",
              "std_ret_1m": v["std1"] if v else None, "std_ret_5m": v["std5"] if v else None,
              "ret_5m": v["ret5"] if v and v["ret5"] is not None else round(fnum(m.get("chg_m5")), 3),
              "vol_accel": accel, "liq_entry": round(fnum(m.get("liq")), 0),
              "narrative": round(fnum(narr), 4) if narr is not None else 0.0,
              "gmgn_trend": _tag_val(tags, "gmgn_rank:") if any(t.startswith("src:gmgn_trending") for t in tags) else None,
              "kol_count": len(ks), "kol_thesis": sum(1 for t in tags if t.startswith("kol_thesis:")),
              "project_score": _tag_val(tags, "proj_score:") if _tag_val(tags, "proj_score:") is not None
              else pr.get("score"),
              "deep_review": deep_effect(mint)["score"], "soft_flags": list(soft or []),
              "stop_base": base, "dyn_stop_shadow": dyn, "dyn_size_mult": dmult,
              "dyn_mode": mode, "stop_applied": dyn if mode == "on" else base,
              "size_mult": round(fnum(size_mult, 1.0) * (dmult if mode == "on" else 1.0), 4),
              # v8.1b: TP / trailing (base = usati davvero; dyn = solo registrati)
              "tp1_base": tpb[0], "trail_act_base": tpb[1], "trail_dist_base": tpb[2],
              "dyn_tp1": tpd[0], "dyn_trail_act": tpd[1], "dyn_trail_dist": tpd[2], "dyn_tp_mode": tp_mode}
        ti = TREND_INFO.get(mint) or {}   # v8.3
        fe.update({"src": m.get("src"), "trending_score": ti.get("trending_score"),
                   "trend_families": ti.get("families"), "trend_sources": ti.get("sources"),
                   "trend_paid": ti.get("paid")})
        fe.update({"atr_gecko": vg["atr"] if vg and v is not vg else None, "atr_birdeye": be_why})   # v8.4
        if CFG.get("V84F_SHADOW_FIELDS", True):   # v8.4f: solo registrati
            if allow_fetch and http is not None and time.time() - MKT_CTX["t"] >= 900 and not MKT_CTX["busy"]:
                asyncio.ensure_future(market_ctx(http))   # in background: mai attese sull'ingresso
            mk = MKT_CTX["d"]
            fe.update(shadow_fields(mint, m, rows, fe.get("ret_5m"), mk))
        try:
            log(st, "Analyst", "atr", f"{m.get('symbol', '?')} ATR 1m {fe['atr_1m_10']}% fonte {fe['atr_src']}"
                + (f" (Birdeye: {be_why})" if be_why not in ("ok", "spento") else ""),
                mint=mint, atr=fe["atr_1m_10"], atr_src=fe["atr_src"], atr_gecko=fe["atr_gecko"])
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        fe = dict(fe, error=type(e).__name__)
    v87_fields(st, http, mint, m, fe, allow_fetch)
    fe.update(discord_tag(mint))   # v8.8: call NKT (SHADOW, nessun effetto)
    return fe


def v87_fields(st, http, mint, m, fe, allow_fetch=True, now=None):
    """v8.7 (D+E+F): campi SOLO shadow. D seme creatore (nessuna rete), F p_prior (cache Analyst),
    E primi scambi pump.fun in background (al massimo STX_MAX_PER_H token/ora, STX_PAGES pagine). Mai eccezioni."""
    try:
        if MODEL_PRIOR:
            fe["p_prior"] = PRIOR_LAST.get(mint)
        cr = None
        if "crh" in SRC and hasattr(SRC["crh"], "seed_for_mint"):
            cr, sd = SRC["crh"].seed_for_mint(mint)
            fe["creator_seed"] = [sd["n"], sd["g"], sd["rug"], sd["fail"]] if sd else None
            fe["serial_launcher"] = int(bool(sd and sd["serial_launcher"])) if cr else None
        now = now or time.time()
        if (CFG.get("STX_MODE", "shadow") == "shadow" and "stx" in SRC and allow_fetch and http is not None
                and str(mint).endswith("pump")):
            STX_STATE["t"] = [t for t in STX_STATE["t"] if now - t < 3600]
            if len(STX_STATE["t"]) >= int(CFG.get("STX_MAX_PER_H", 30)) or mint in STX_STATE["busy"]:
                fe["stx"] = {"ok": False, "why": "limite orario"}
            else:
                STX_STATE["t"].append(now)
                fe["stx"] = {"ok": None, "why": "in corso"}
                asyncio.ensure_future(stx_task(st, http, mint, m, fe, cr))
    except Exception as e:  # noqa: BLE001
        fe["v87_err"] = type(e).__name__


async def stx_task(st, http, mint, m, fe, creator=None):
    """v8.7 (E): sniper_pct, first20_sold_pct, dev_first_sell_dt nel dict fe (letto alla chiusura)."""
    STX_STATE["busy"].add(mint)
    try:
        created = None
        if "crh" in SRC:
            mi = (SRC["crh"]._load()["mint"].get(mint) or {})
            created = mi.get("created") or None
            creator = creator or mi.get("creator")
        r = await SRC["stx"].fetch(http.get, mint, creator, created, {"MAX_PAGES": int(CFG.get("STX_PAGES", 3))})
        fe["stx"] = r
        for k in ("sniper_pct", "first20_sold_pct", "dev_first_sell_dt"):
            fe[k] = r.get(k)
        write_event("Analyst", "early_trades", f"primi scambi {m.get('symbol')}: "
                    + (f"sniper {r.get('sniper_pct')}%, primi20 venduto {r.get('first20_sold_pct')}%, "
                       f"dev vende dopo {r.get('dev_first_sell_dt')}s" if r.get("ok") else str(r.get("why")))
                    + " - solo misura", {"mint": mint, **{k: v for k, v in r.items()}})
    except Exception as e:  # noqa: BLE001
        fe["stx"] = {"ok": False, "why": f"errore {type(e).__name__}"}
    finally:
        STX_STATE["busy"].discard(mint)


# ----------------------------------------------------------------------- v8.4f: campi SOLO shadow (review Claude)
SEC_LAST = {}      # mint -> {"rc_holders", "supply", "ts"} dall'ultimo controllo sicurezza (nessuna chiamata)
MKT_CTX = {"t": 0.0, "d": {}, "busy": False}   # BTC/SOL da CoinGecko, cache 15 min


def _pct_back(series, now, sec):
    """series = [(ts, prezzo)] in ordine: variazione % rispetto al punto piu vicino a now - sec."""
    if not series:
        return None
    tgt = now - sec
    old = min(series, key=lambda x: abs(x[0] - tgt))
    if abs(old[0] - tgt) > max(900, sec * 0.25) or old[1] <= 0:
        return None
    return round((series[-1][1] / old[1] - 1) * 100, 2)


async def market_ctx(http, now=None):
    """v8.4f: btc_1h/4h/24h, sol_1h/4h (CoinGecko gratis, 2 chiamate ogni 15 min). Errore = campi None."""
    now = now or time.time()
    if now - MKT_CTX["t"] < 900 or http is None or MKT_CTX["busy"] or not CFG.get("MKT_CTX", True):
        return MKT_CTX["d"]
    MKT_CTX["busy"] = True
    try:
        d = {}
        for coin, pre in (("bitcoin", "btc"), ("solana", "sol")):
            r = await http.get(f"https://api.coingecko.com/api/v3/coins/{coin}/market_chart?vs_currency=usd&days=1")
            pr = (r or {}).get("prices") if isinstance(r, dict) else None
            ser = [(x[0] / 1000.0, fnum(x[1])) for x in pr or [] if isinstance(x, (list, tuple)) and len(x) >= 2]
            if len(ser) >= 10:
                t_last = ser[-1][0]
                d[pre + "_1h"] = _pct_back(ser, t_last, 3600)
                d[pre + "_4h"] = _pct_back(ser, t_last, 4 * 3600)
                if pre == "btc":
                    d["btc_24h"] = round((ser[-1][1] / ser[0][1] - 1) * 100, 2) if ser[0][1] > 0 else None
        MKT_CTX.update(t=now, d=d)
    except Exception:  # noqa: BLE001
        MKT_CTX["t"] = now
    finally:
        MKT_CTX["busy"] = False
    return MKT_CTX["d"]


def shadow_fields(mint, m, rows, ret5, mkt=None):
    """v8.4f: pool sottile, ombra/candela rossa, posizione nel range 30m, lama, BTC/SOL, supply d'ingresso.
    Solo registrati in entry_features (nessun effetto sulle decisioni). Tag in 'v84f_tags'."""
    out, tags = {}, []
    try:
        sl = SEC_LAST.get(mint) or {}
        price, liq = fnum(m.get("price")), fnum(m.get("liq"))
        sup = fnum(sl.get("supply")) or (1e9 if str(mint).endswith("pump") else 0.0)
        hs = sorted((fnum(p_) for _, p_ in sl.get("rc_holders") or []), reverse=True)
        out["supply_entry"] = sup or None
        if price > 0 and sup > 0 and liq > 0:
            f = (liq / 2) / (price * sup)
            out["pool_supply_pct"] = round(f * 100, 3)
            if hs:
                t1, t3 = hs[0] / 100, sum(hs[:3]) / 100
                out["top1_pct"], out["top3_pct"] = round(t1 * 100, 3), round(t3 * 100, 3)
                out["imp_top1_pct"] = round((1 - (f / (f + t1)) ** 2) * 100, 1) if f + t1 > 0 else None
                out["imp_top3_pct"] = round((1 - (f / (f + t3)) ** 2) * 100, 1) if f + t3 > 0 else None
                if fnum(out["imp_top1_pct"]) >= 50 or fnum(out["imp_top3_pct"]) >= 75:
                    tags.append("thin_pool")
        cur = int(time.time() // 60) * 60
        done = [r for r in (rows or []) if r[0] < cur and r[4] > 0]
        if done:
            o, h, l_, c_ = done[-1][1], done[-1][2], done[-1][3], done[-1][4]
            rng = h - l_
            out["wick_entry"] = int(bool(rng > 0 and ((h - max(o, c_)) / rng > 0.5 or c_ < o)))
            w30 = done[-30:]
            hi, lo = max(r[2] for r in w30), min(r[3] for r in w30)
            out["range_pos_30m"] = round((c_ - lo) / (hi - lo), 3) if hi > lo else None
            if out["range_pos_30m"] is not None and 0.33 <= out["range_pos_30m"] <= 0.66:
                tags.append("mid_range")
            out["ret_15m"] = round((c_ / done[-16][4] - 1) * 100, 2) if len(done) >= 16 and done[-16][4] > 0 else None
            if out["ret_15m"] is not None and out["ret_15m"] <= -10:
                tags.append("falling_knife")
        mk = dict(mkt or {})
        for k in ("btc_1h", "btc_4h", "btc_24h", "sol_1h", "sol_4h"):
            out[k] = mk.get(k)
        if (out["btc_4h"] is not None and out["btc_4h"] <= -3) or (out["btc_24h"] is not None and out["btc_24h"] <= -5):
            tags.append("risk_off")
        if out["sol_1h"] is not None and out["sol_1h"] >= 1 and fnum(ret5) < 0:
            tags.append("rel_weak")
    except Exception as e:  # noqa: BLE001
        out["v84f_error"] = type(e).__name__
    out["v84f_tags"] = tags
    return out


def alt_exits(mint, kind, t0=None, now=None):
    """v8.4f: uscite alternative simulate sul price path (zero chiamate). Piano base semplificato (stop, TP1,
    trailing, tempo massimo); alt_tp3x = base + TP3 a +200% che vende il 33% del residuo; alt_moon15 = base sul
    85% e 15% tenuto senza trailing fino al tempo massimo o al crollo della liquidita (o all'uscita reale)."""
    try:
        fp = os.path.join(PATH_DIR, f"{mint}.jsonl")
        if not os.path.exists(fp):
            return {}
        recs = []
        with open(fp) as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                except Exception:  # noqa: BLE001
                    continue
                if r.get("k") == kind:
                    recs.append(r)
        starts = [i for i, r in enumerate(recs) if r.get("ev") == "entry" and (t0 is None or r["t"] >= t0 - 120)]
        if not starts:
            return {}
        path = [r for r in recs[starts[-1]:] if not str(r.get("ev") or "").startswith("exit") and fnum(r.get("p")) > 0]
        if len(path) < 2:
            return {}
        p0, l0, ts0 = fnum(path[0]["p"]), fnum(path[0].get("l")), path[0]["t"]
        sl, tp1, tp1f = fnum(CFG["STOP_LOSS_PCT"]), fnum(CFG["TP1_PCT"]), fnum(CFG["TP1_FRACTION"])
        ta, td, mh = fnum(CFG["TRAIL_ACTIVATE_PCT"]), fnum(CFG["TRAIL_DISTANCE_PCT"]), fnum(CFG["MAX_HOLD_MIN"]) * 60

        def sim(tp3=False, moon=0.0):
            rem, real, peak, tp1d, tp3d = 1.0 - moon, 0.0, 0.0, False, False
            moon_left, last = moon, 0.0
            for r in path[1:]:
                g = (fnum(r["p"]) / p0 - 1) * 100
                last = g
                crash = l0 > 0 and 0 < fnum(r.get("l")) < 0.3 * l0
                over = r["t"] - ts0 >= mh
                if moon_left > 0 and (crash or over):
                    real += moon_left * g
                    moon_left = 0.0
                if rem <= 0:
                    if moon_left <= 0:
                        break
                    continue
                peak = max(peak, g)
                if g <= sl or crash or over:
                    real += rem * g
                    rem = 0.0
                    continue
                if not tp1d and g >= tp1:
                    real += rem * tp1f * g
                    rem *= 1 - tp1f
                    tp1d = True
                if tp3 and not tp3d and g >= 200:
                    real += rem * 0.33 * g
                    rem *= 0.67
                    tp3d = True
                if peak >= ta and g <= peak - td * (1 + peak / 100):
                    real += rem * g
                    rem = 0.0
            real += (rem + moon_left) * last
            return round(real, 2)
        return {"alt_base": sim(), "alt_tp3x": sim(tp3=True), "alt_moon15": sim(moon=0.15),
                "alt_bars": len(path)}
    except Exception as e:  # noqa: BLE001
        return {"alt_error": type(e).__name__}



def fe_columns(fe):
    """Colonne CSV v8.1 (piatte + JSON completo)."""
    fe = fe or {}
    return {"atr_1m_10": fe.get("atr_1m_10"), "stop_applied": fe.get("stop_applied"),
            "dyn_stop_shadow": fe.get("dyn_stop_shadow"), "size_mult": fe.get("size_mult"),
            "dyn_tp1": fe.get("dyn_tp1"), "dyn_trail_act": fe.get("dyn_trail_act"),
            "dyn_trail_dist": fe.get("dyn_trail_dist"),
            "entry_features": json.dumps(fe, ensure_ascii=False, separators=(",", ":")) if fe else ""}


# ----------------------------------------------------------------------- v8.1: price paths (nessuna chiamata in piu)
PATH_DIR = os.path.join(DATA_DIR, "price_paths")
_PATH_PRUNE = [0.0, 0.0]  # ultima pulizia, ultimo batch post-uscita senza posizioni


def path_write(mint, rec):
    if not CFG["PRICE_PATHS"]:
        return
    try:
        os.makedirs(PATH_DIR, exist_ok=True)
        fp = os.path.join(PATH_DIR, f"{mint}.jsonl")
        try:
            if os.path.getsize(fp) > CFG["PRICE_PATH_MAX_BYTES"]:
                os.replace(fp, fp + ".1")
        except OSError:
            pass
        with open(fp, "a") as f:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
        if time.time() - _PATH_PRUNE[0] > 3600:
            _PATH_PRUNE[0] = time.time()
            fs = sorted((os.path.join(PATH_DIR, x) for x in os.listdir(PATH_DIR)), key=os.path.getmtime)
            for x in fs[:max(0, len(fs) - int(CFG["PRICE_PATH_MAX_FILES"]))]:
                os.remove(x)
    except Exception:  # noqa: BLE001
        pass


def path_tick(mint, kind, price, liq, pnl=None, now=None, ev=None):
    if ST_REF[0] is not None and not ev:
        try:
            label_watch_tick(ST_REF[0], mint, price, liq, now)   # v8.2
        except Exception:  # noqa: BLE001
            pass
    rec = {"t": int(now or time.time()), "k": kind, "p": float(f"{fnum(price):.6g}"), "l": round(fnum(liq))}
    if pnl is not None:
        rec["pnl"] = round(fnum(pnl), 2)
    if ev:
        rec["ev"] = ev
    path_write(mint, rec)


def post_exit_start(st, mint, kind, sym, entry_price, exit_price, reason, stop_applied=None, dyn_stop=None, now=None):
    """Dopo l'uscita il token resta seguito POST_EXIT_MIN minuti (prezzi gia presi con i dex_batch esistenti)."""
    if not CFG["PRICE_PATHS"] or CFG["POST_EXIT_MIN"] <= 0:
        return
    now = now or time.time()
    path_tick(mint, kind, exit_price, 0, now=now, ev="exit:" + str(reason)[:60])
    if len(st.post_exit) >= CFG["POST_EXIT_MAX"]:
        st.post_exit.pop(min(st.post_exit, key=lambda k: st.post_exit[k]["t"]), None)
    st.post_exit[mint] = {"t": now, "until": now + CFG["POST_EXIT_MIN"] * 60, "kind": kind, "sym": sym,
                          "entry": entry_price, "exit": exit_price, "reason": str(reason)[:80], "max": exit_price,
                          "min": exit_price, "stop": stop_applied, "dyn": dyn_stop, "last": exit_price}


def post_exit_update(st, market, now=None):
    """Aggiorna i token gia usciti con i prezzi di un dex_batch gia fatto; chiude quelli scaduti con un riepilogo."""
    now = now or time.time()
    for mint, e in list(st.post_exit.items()):
        if mint in market:
            m = summarize(market[mint])
            if m["price"] > 0:
                e["max"], e["min"], e["last"] = max(e["max"], m["price"]), min(e["min"], m["price"]), m["price"]
                path_tick(mint, e["kind"] + "_post", m["price"], m["liq"], now=now)
        if now >= e["until"]:
            st.post_exit.pop(mint, None)
            ent, ex = fnum(e.get("entry")), fnum(e.get("exit"))
            summ = {"mint": mint, "sym": e.get("sym"), "kind": e["kind"], "reason": e.get("reason"),
                    "exit_ts": int(e["t"]), "minutes": round((now - e["t"]) / 60),
                    "max_vs_exit_pct": round((e["max"] / ex - 1) * 100, 2) if ex > 0 else None,
                    "min_vs_exit_pct": round((e["min"] / ex - 1) * 100, 2) if ex > 0 else None,
                    "max_vs_entry_pct": round((e["max"] / ent - 1) * 100, 2) if ent > 0 else None,
                    "last_vs_exit_pct": round((e["last"] / ex - 1) * 100, 2) if ex > 0 else None,
                    "rebound_above_entry": bool(ent > 0 and e["max"] > ent), "stop": e.get("stop"), "dyn": e.get("dyn")}
            path_write("_post_exit_summary", summ)


def pos_max_hold(p, mint=None):
    """v7: tenuta massima della posizione (piu corta con LP sbloccata), mai oltre MAX_HOLD_MIN.
    v8: con voto deep review >= DEEP_HOLD_MIN_SCORE +DEEP_HOLD_EXTRA_MIN (non per LP sbloccata), max 720 min."""
    base = min(CFG["MAX_HOLD_MIN"], fnum(p.get("max_hold_min")) or CFG["MAX_HOLD_MIN"])
    if mint and not p.get("max_hold_min") and deep_effect(mint)["hold_extra"] > 0:
        return min(720, base + deep_effect(mint)["hold_extra"])
    return base


# ----------------------------------------------------------------------- v8: analisi approfondite (Mark)
DEEP_QUEUE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources", "deep_review_queue.jsonl")
DEEP_REVIEWS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources", "deep_reviews.json")
DEEP = {"key": None, "reviews": {}, "queued": None}


def deep_reviews():
    """Rilegge deep_reviews.json solo quando cambia. File assente/non valido: ultimi valori validi."""
    try:
        stt = os.stat(DEEP_REVIEWS_PATH)
        key = (stt.st_mtime_ns, stt.st_size)
    except OSError:
        return DEEP["reviews"]
    if key != DEEP["key"]:
        DEEP["key"] = key
        try:
            d = json.load(open(DEEP_REVIEWS_PATH))
            if isinstance(d, dict):
                DEEP["reviews"] = {k: v for k, v in d.items() if isinstance(v, dict)}
        except Exception:  # noqa: BLE001
            pass
    return DEEP["reviews"]


def deep_effect(mint):
    """{score, hold_extra (min), trail_tighten (punti)} dal verdetto di Mark (voto 0-100)."""
    rv = deep_reviews().get(mint) or {}
    sc = rv.get("score")
    out = {"score": None, "hold_extra": 0, "trail_tighten": 0.0}
    if isinstance(sc, (int, float)) and not isinstance(sc, bool) and math.isfinite(sc):
        sc = clamp(float(sc), 0.0, 100.0)
        out["score"] = sc
        if sc >= CFG["DEEP_HOLD_MIN_SCORE"]:
            out["hold_extra"] = int(CFG["DEEP_HOLD_EXTRA_MIN"])
        elif sc <= CFG["DEEP_LOW_SCORE"]:
            out["trail_tighten"] = float(CFG["DEEP_TRAIL_TIGHTEN_PCT"])
    return out


def deep_queue_add(mint, symbol, links, reason, url="", score=None, chain="solana"):
    """Accoda un token per l'analisi approfondita (una volta per mint e motivo). Mai eccezioni."""
    try:
        if DEEP["queued"] is None:
            DEEP["queued"] = set()
            if os.path.exists(DEEP_QUEUE_PATH):
                for line in open(DEEP_QUEUE_PATH):
                    try:
                        e = json.loads(line)
                        DEEP["queued"].add((e.get("mint"), str(e.get("reason", "")).split(":")[0]))
                    except ValueError:
                        pass
        k = (mint, reason.split(":")[0])
        if k in DEEP["queued"]:
            return False
        DEEP["queued"].add(k)
        pr = PROJ_LAST.get(mint) or {}
        lk = dict(links or {})
        if pr.get("links"):
            lk.setdefault("project", pr["links"])
        e = {"mint": mint, "symbol": symbol, "chain": chain, "links": lk, "dexscreener": url, "reason": reason,
             "project_score": pr.get("score"), "project_tags": pr.get("tags"), "bot_score": score,
             "ts": datetime.now(TZ).isoformat(timespec="seconds")}
        with open(DEEP_QUEUE_PATH, "a") as f:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
        return True
    except Exception:  # noqa: BLE001
        return False


def deep_queue_peak(mint, sym, peak, links, url, kind):
    if peak >= CFG["DEEP_QUEUE_PEAK_PCT"]:
        deep_queue_add(mint, sym, links, f"{kind}_peak:+{peak:.0f}%", url)


def is_rug_event(reason, minutes):
    """v7: rug = liquidita -50% (LIQ_DROP_EXIT_PCT) entro RUG_WINDOW_MIN dall'ingresso, o -80% in qualsiasi momento."""
    mt = re.search(r"liquidita crollata del (\d+)%", str(reason))
    if not mt:
        return False
    drop = float(mt.group(1))
    return drop >= 80 or (drop >= CFG["LIQ_DROP_EXIT_PCT"] and fnum(minutes) <= CFG["RUG_WINDOW_MIN"])


# v8.5h (bug segnalato da ticher, 31/10): pool svuotato/sostituito -> DexScreener da un prezzo esploso (x10.000) e il
# PnL calcolato diventava +405.686%. Ora: 1) uscita per liquidita crollata = token rimasti a 0 (rug, -100% sulla parte
# non ancora venduta); 2) PnL sopra PNL_IMPLAUSIBLE_PCT = dato non valido, mai al Learner; 3) un salto di prezzo oltre
# PRICE_SPIKE_MULT volte in un solo aggiornamento non viene preso (la liquidita in calo si, e segnale di rug).
CFG.setdefault("LIQ_CRASH_ZERO", True)
CFG.setdefault("PNL_IMPLAUSIBLE_PCT", 1000.0)
CFG.setdefault("PRICE_SPIKE_MULT", 20.0)


def pnl_implausible(pnl_pct):
    """v8.5h: True se il PnL e troppo alto per essere un'uscita vendibile (prezzo esploso a pool svuotato)."""
    lim = fnum(CFG.get("PNL_IMPLAUSIBLE_PCT"), 0.0)
    return lim > 0 and fnum(pnl_pct) > lim


def price_spike(last_price, new_price):
    """v8.5h: True se il nuovo prezzo e oltre PRICE_SPIKE_MULT volte l'ultimo buono (dato della pool non credibile)."""
    mult = fnum(CFG.get("PRICE_SPIKE_MULT"), 0.0)
    lp, np_ = fnum(last_price), fnum(new_price)
    return mult > 1 and lp > 0 and np_ > lp * mult


def apply_market_tick(o, m, now):
    """v8.5h: aggiorna prezzo/liquidita di una posizione o di uno shadow. Ritorna False se il prezzo e scartato
    (salto oltre PRICE_SPIKE_MULT): in quel caso si prende solo una liquidita PIU BASSA (segnale di rug) e last_ok
    non avanza, cosi dopo 15 min decide la seconda fonte (Jupiter) come per un prezzo mancante."""
    if price_spike(o.get("last_price"), m["price"]):
        o["spike_n"] = int(o.get("spike_n", 0)) + 1
        if m.get("liq_known", True) and fnum(m.get("liq")) < fnum(o.get("liq"), 1.0):
            o["liq"] = max(fnum(m.get("liq")), 1.0)
        return False
    o["last_price"], o["last_ok"] = m["price"], now
    if m.get("liq_known", True):  # v8.5: liquidita assente = dato mancante, non rug
        o["liq"] = max(m["liq"], 1.0)
    return True


# ----------------------------------------------------------------------- v7: bonus dei segnali extra (sources/)
# chiave CFG -> (prefisso del tag in 'signals', passo del Learner). Limiti = OVR_LIMITS (anche per il Learner).
BONUS_KEYS = {"GMGN_TREND_BONUS": ("src:gmgn_trending", 0.01), "FOMO_TREND_BONUS": ("src:fomo_trending", 0.01), "FOMO_SUB_BONUS": ("src:dex_boosts_substitute", 0.01),
              "KOL_BONUS": ("kol:", 0.01), "KOL_THESIS_BONUS": ("kol_thesis:", 0.005),
              "MOMENTUM_YOUNG_BONUS": ("momentum_young:", 0.01),
              "PROJECT_BONUS": ("proj_good:", 0.01),  # v8
              "TREND_MULTI_BONUS": ("trend_multi:", 0.005)}  # v8.3
# v8: penalita apprese al contrario: se gli esiti CON il tag sono peggiori -> +passo, altrimenti -passo fino a 0
PENALTY_KEYS = {"PROJECT_EMPTY_PENALTY": ("proj_empty", 0.005)}
BONUS_LIMITS = {"TREND_MULTI_BONUS": (0.0, 0.04), "PROJECT_BONUS": (0.0, 0.06), "PROJECT_EMPTY_PENALTY": (0.0, 0.03), "PROJECT_MIN_SCORE": (0.3, 0.8),
                "GMGN_TREND_BONUS": (0.0, 0.08), "FOMO_TREND_BONUS": (0.0, 0.08), "FOMO_SUB_BONUS": (0.0, 0.03), "KOL_BONUS": (0.0, 0.08),
                "KOL_THESIS_BONUS": (0.0, 0.06), "MOMENTUM_YOUNG_BONUS": (0.0, 0.08), "TOTAL_BONUS_CAP": (0.0, 0.10),
                "KOL_SOLD_PENALTY": (0.0, 0.05), "KOL_MIN_SOL": (0.05, 1.0), "MOMENTUM_MIN_SCORE": (0.4, 0.8)}


def bonus_eff(st, key):
    lim = BONUS_LIMITS.get(key, (-1e9, 1e9))
    d = fnum(((st.learner_bonus or {}).get("d") or {}).get(key)) if key in BONUS_KEYS or key in PENALTY_KEYS else 0.0
    return round(clamp(fnum(CFG.get(key)) + d, lim[0], lim[1]), 4)


PROJ_LAST = {}   # v8: mint -> ultimo risultato di project_check (link per la coda deep review)


async def project_eval(mint, m, sec):
    """v8: controllo progetto con scadenza PROJECT_TIMEOUT_SEC. Timeout/errore -> None (nessun effetto)."""
    if not CFG["PROJECT_CHECK"] or "proj" not in SRC:
        return None
    sec = sec or {}
    try:
        r = await asyncio.wait_for(SRC["proj"].acheck(
            mint, symbol=m.get("symbol"), name=m.get("name"), links=m.get("links") or None,
            token_meta=sec.get("token_meta"), is_pump=sec.get("is_pump"),
            deadline=max(1.0, CFG["PROJECT_TIMEOUT_SEC"] - 0.5)), timeout=CFG["PROJECT_TIMEOUT_SEC"])
    except Exception:  # noqa: BLE001  (timeout compreso)
        return {"score": None, "tags": ["proj_timeout"]}
    if r and r.get("score") is not None:
        PROJ_LAST[mint] = r
        for k in [k for k in PROJ_LAST if time.time() - fnum(PROJ_LAST[k].get("ts")) > 7200][:50]:
            PROJ_LAST.pop(k, None)
    return r


async def compute_bonus(st, mint, m, sec=None):
    """Ritorna (bonus, tag). Mai negativo oltre -(KOL_SOLD_PENALTY + PROJECT_EMPTY_PENALTY), mai oltre TOTAL_BONUS_CAP."""
    tags, parts = [], 0.0
    cfgb = {k: bonus_eff(st, k) for k in BONUS_LIMITS}
    proj_task = asyncio.ensure_future(project_eval(mint, m, sec)) if CFG["PROJECT_CHECK"] and "proj" in SRC else None
    fr = st.fomo_rank.get(mint)
    if fr:
        rk = "gmgn_rank" if fr["source"] == "gmgn_trending" else "fomo_rank"
        tags += [f"src:{fr['source']}", f"{rk}:{fr['rank']}"]
        if fr["source"] == "gmgn_trending":
            parts += cfgb["GMGN_TREND_BONUS"]
        elif fr["source"] == "fomo_trending":
            parts += cfgb["FOMO_TREND_BONUS"]
        elif fr["source"] == "dex_boosts_substitute":
            parts += cfgb["FOMO_SUB_BONUS"]
    ti = TREND_INFO.get(mint)
    if ti and ti.get("n_fam", 0) >= 2:   # v8.3: in trending su 2+ fonti indipendenti
        tags.append(f"trend_multi:{ti['n_fam']}")
        parts += cfgb["TREND_MULTI_BONUS"]
    if "mom" in SRC:
        mr = await SRC["mom"].amomentum(mint)
        b, t = SRC["mom"].momentum_bonus(mr or {}, cfgb)
        parts, tags = parts + b, tags + t
    if "theses" in SRC:
        b, t = SRC["theses"].thesis_bonus(mint, SRC["kol"].tier_of, cfgb, getattr(SRC["kol"], "weight_of", None))
        parts, tags = parts + b, tags + t
    if "kol" in SRC:
        async with RPC_HEAVY_LOCK:  # condivide la RPC pubblica con il controllo cluster: uno alla volta
            kr = await SRC["kol"].akol_check(mint)
        b, t = SRC["kol"].kol_bonus(kr or {}, cfgb)
        parts, tags = parts + b, tags + t
        if (kr or {}).get("error"):
            tags.append("kol_err")
    if proj_task is not None:  # v8: qualita del progetto (gia partito in parallelo; timeout = nessun effetto)
        try:
            pr = await proj_task
            b, t = SRC["proj"].project_bonus(pr or {}, cfgb)
            parts, tags = parts + b, tags + [x for x in t if x not in tags]
        except Exception:  # noqa: BLE001
            tags.append("proj_err")
    total = clamp(parts, -(cfgb["KOL_SOLD_PENALTY"] + cfgb["PROJECT_EMPTY_PENALTY"]), cfgb["TOTAL_BONUS_CAP"])
    return round(total, 4), tags


def record_signals(st, mint, signals, pnl_pct, rug, shadow):
    """v7: esito per i bonus (trade veri + shadow) e per il registro dei KOL (kol_ledger.json)."""
    tags = [t for t in str(signals or "").split("|") if t]
    if not tags:
        return
    lb = st.learner_bonus
    lb.setdefault("hist", []).append([round(float(pnl_pct), 2), 1 if rug else 0, tags, int(time.time()), int(shadow)])
    del lb["hist"][:-int(CFG["LEARN_BONUS_WINDOW"])]
    if "kol" in SRC:
        led = None
        for t in tags:
            if t.startswith("kol:"):
                try:
                    led = led or SRC["kol"].KolLedger()
                    led.record(t.split(":")[1], -100.0 if rug else pnl_pct, mint, shadow)
                except Exception:  # noqa: BLE001
                    pass
    learn_bonus_step(st)


def learn_bonus_step(st, now=None):
    """Per ogni bonus: media netta degli esiti CON il segnale contro quelli SENZA (>= LEARN_BONUS_MIN_N ciascuno).
    Meglio di LEARN_BONUS_EDGE punti -> +passo; peggio o neutro -> -passo fino a 0. Un passo all'ora, nei limiti."""
    now = now or time.time()
    lb = st.learner_bonus
    hist, d, last = lb.get("hist", []), lb.setdefault("d", {}), lb.setdefault("last", {})
    acts = []
    for key, (pref, step) in BONUS_KEYS.items():
        if now - fnum(last.get(key)) < CFG["LEARN_STEP_SEC"]:
            continue
        w = [(-100.0 if h[1] else h[0]) for h in hist if any(t.startswith(pref) for t in h[2])]
        wo = [(-100.0 if h[1] else h[0]) for h in hist if not any(t.startswith(pref) for t in h[2])]
        n = int(CFG["LEARN_BONUS_MIN_N"])
        if len(w) < n or len(wo) < n:
            continue
        aw, awo = sum(w) / len(w), sum(wo) / len(wo)
        old = bonus_eff(st, key)
        lim = BONUS_LIMITS[key]
        new = clamp(old + (step if aw >= awo + CFG["LEARN_BONUS_EDGE"] else -step), lim[0], lim[1])
        if abs(new - old) < 1e-9:
            continue
        d[key], last[key] = round(new - fnum(CFG.get(key)), 4), now
        msg = (f"bonus {key} {old:.3f} -> {new:.3f} (con il segnale {aw:+.1f}% su {len(w)}, senza {awo:+.1f}% "
               f"su {len(wo)})")
        st.lessons.append(f"{datetime.now(TZ):%d/%m %H:%M} {msg}")
        log(st, "Learner", "learn_bonus", msg, key=key, old=old, new=new, n_with=len(w), n_without=len(wo))
        acts.append(key)
    for key, (pref, step) in PENALTY_KEYS.items():  # v8: penalita (verso opposto)
        if now - fnum(last.get(key)) < CFG["LEARN_STEP_SEC"]:
            continue
        w = [(-100.0 if h[1] else h[0]) for h in hist if any(t.startswith(pref) for t in h[2])]
        wo = [(-100.0 if h[1] else h[0]) for h in hist if not any(t.startswith(pref) for t in h[2])]
        n = int(CFG["LEARN_BONUS_MIN_N"])
        if len(w) < n or len(wo) < n:
            continue
        aw, awo = sum(w) / len(w), sum(wo) / len(wo)
        old = bonus_eff(st, key)
        lim = BONUS_LIMITS[key]
        new = clamp(old + (step if aw <= awo - CFG["LEARN_BONUS_EDGE"] else -step), lim[0], lim[1])
        if abs(new - old) < 1e-9:
            continue
        d[key], last[key] = round(new - fnum(CFG.get(key)), 4), now
        msg = (f"penalita {key} {old:.3f} -> {new:.3f} (con il segnale {aw:+.1f}% su {len(w)}, senza {awo:+.1f}% "
               f"su {len(wo)})")
        st.lessons.append(f"{datetime.now(TZ):%d/%m %H:%M} {msg}")
        log(st, "Learner", "learn_bonus", msg, key=key, old=old, new=new, n_with=len(w), n_without=len(wo))
        acts.append(key)
    return acts


def narr_relax_match(st, mint, m):
    """v8.5: narrativa di narratives.json (fresca) con fonte attendibile (trusted_mentions > 0) per questo token.
    Ritorna (tema, tipo) o None. Copie del ticker con mint diverso e blocklist = None."""
    try:
        narrs = NARR.current(st)
    except Exception:  # noqa: BLE001
        return None
    sym = str(m.get("symbol") or "").lstrip("$").upper().strip()
    if not narrs or mint in NARR.blocklist or mint in SKIP_MINTS or sym in NARR_STABLE_TICKERS:
        return None
    lname = str(m.get("name") or "").lower()
    for n in narrs:
        if not fnum(n.get("trusted_mentions")) > 0 or n.get("trend") == "fading":
            continue
        conf = n.get("mint_conf", {})
        if mint in n["mints"]:
            return n["theme"], "mint"
        if sym and sym in n["tickers"]:
            if any(v >= NARR_COPYCAT_CONF for v in conf.values()) and mint not in conf:
                continue   # copia del ticker
            return n["theme"], "ticker"
        if lname and any(k in lname for k in n["keywords"]):
            return n["theme"], "parola"
    return None


def narr_relax_shadow(st, http, mint, m, c, why, score=0.0):
    """v8.5 (solo misura): scartato SOLO per momentum/VWAP/punteggio ma legato a una narrativa attendibile ->
    shadow con tag narrative_relax. Mai un acquisto vero. Ritorna True se lo shadow e stato chiesto."""
    if not (CFG["SHADOW_TRADES"] and CFG.get("NARR_RELAX_SHADOW", True)):
        return False
    hit = narr_relax_match(st, mint, m)
    if not hit:
        return False
    st.stats["narrative_relax"] = st.stats.get("narrative_relax", 0) + 1
    spawn_shadow(st, http, mint, m, ["narrative_relax"], f"Analyst: {why} (narrativa '{hit[0]}', {hit[1]})", score,
                 list((c.get("sec") or {}).get("soft") or []), tag="narrative_relax",
                 signals=[f"src:{m.get('src') or '?'}", "reason:narrative_relax", f"narr:{str(hit[0])[:30]}"])
    return True


def analyst_shadow(st, http, mint, m, c, why):
    """v7: shadow sugli scarti soft dell'Analyst (dopo i controlli di sicurezza), tag analyst_momentum."""
    if not (CFG["SHADOW_TRADES"] and CFG["SHADOW_ANALYST_REJECTS"]):
        return
    flags = list((c.get("sec") or {}).get("soft") or [])
    spawn_shadow(st, http, mint, m, ["analyst_momentum"], f"Analyst: {why}", 0.0, flags, tag="analyst_momentum",
                 signals=[f"src:{m.get('src') or '?'}", "reason:analyst_momentum"])



def shadow_stats(hist):
    """n, win rate (rug contati 3 volte), media netta % (rug = -100%), quota rug/-50%."""
    n = len(hist)
    if not n:
        return {"n": 0, "win": 0.0, "avg": 0.0, "rug": 0.0}
    rugs = sum(1 for h in hist if h[1])
    wins = sum(1 for h in hist if h[0] > 0 and not h[1])
    pnls = [-100.0 if h[1] else h[0] for h in hist]
    return {"n": n, "win": round(wins / (n + 2 * rugs), 3), "avg": round(sum(pnls) / n, 2), "rug": round(rugs / n, 3)}


def record_outcome(st, keys, pnl_pct, rug, src, now=None):
    now = now or time.time()
    rug = bool(rug or pnl_pct <= -50)
    for key in keys:
        e = _ls(st, key)
        e["hist"].append([round(float(pnl_pct), 2), 1 if rug else 0, src, int(now)])
        del e["hist"][:-int(CFG["LEARN_SHADOW_WINDOW"])]
        learn_shadow_step(st, key, now)


# compito 12 (Claude): etichette notturne di tools/retro_label.py (data/retro_labels.jsonl). Il Learner NON le usa finche
# RETRO_LABELS_IN_LEARNING e falso (default): l'attivazione e collegamento sono decisione del reviewer.
CFG.setdefault("RETRO_LABELS_IN_LEARNING", False)
RETRO_LABELS_PATH = os.path.join(DATA_DIR, "data", "retro_labels.jsonl")


def retro_labels_load(path=None, max_rows=20000):
    """Righe valide di retro_labels.jsonl ([] se il flag e spento o il file manca). 'positive' solo se win e non cattivo."""
    if not CFG.get("RETRO_LABELS_IN_LEARNING"):
        return []
    out = []
    try:
        with open(path or RETRO_LABELS_PATH) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("label") in ("win", "loss", "rug"):
                    r["positive"] = bool(r.get("positive")) and r.get("label") == "win" and not r.get("bad")
                    out.append(r)
    except OSError:
        return []
    return out[-max_rows:]


MEASURE_ONLY_KEYS = {"narrative_relax", "rugcheck_down", "bundle_established", "buysell_relax", "score_band"}
# v8.5e (analisi di Mark, decisione Kiwi 08:47): fascia di punteggio sotto soglia = acquisto PAPER a meta size;
# acquisti/vendite 5m appena sotto il minimo = solo shadow buysell_relax.
CFG.setdefault("SCORE_BAND", True)
CFG.setdefault("SCORE_BAND_LOW", 0.45)
CFG.setdefault("SCORE_BAND_SIZE_MULT", 0.5)
CFG.setdefault("BUYSELL_RELAX_SHADOW", True)
CFG.setdefault("BUYSELL_RELAX_LOW", 1.20)   # v8.5: solo misura, mai passi


def learn_shadow_step(st, key, now=None):
    """Al massimo un passo all'ora per segnale, solo con >= LEARN_SHADOW_MIN_N esiti. Ritorna l'azione o None."""
    now = now or time.time()
    if key in LP_GATE_KEYS or key in HP_SHADOW_KEYS or key in MEASURE_ONLY_KEYS:  # v8.4e (Kiwi): regola LP 0%/sconosciuta bloccata, il Learner la misura e basta
        return None
    e = _ls(st, key)
    sts = shadow_stats(e["hist"])
    if sts["n"] < CFG["LEARN_SHADOW_MIN_N"] or now - e["last_adj"] < CFG["LEARN_STEP_SEC"]:
        return None
    loosen = sts["avg"] >= CFG["LEARN_LOOSEN_AVG"] and sts["win"] >= CFG["LEARN_LOOSEN_WIN"] \
        and sts["rug"] <= CFG["LEARN_LOOSEN_RUG"]
    tighten = sts["avg"] < 0 or sts["rug"] > CFG["LEARN_TIGHTEN_RUG"]
    if not (loosen or tighten):
        return None
    d = 1 if loosen else -1
    if key == "score":  # i quasi-promossi alimentano la soglia: solo verso il basso e solo se chiaramente in utile
        if not loosen or sts["win"] < CFG["LEARN_THR_WIN"]:
            return None
        old = st.threshold
        st.threshold = max(CFG["THRESHOLD_MIN"], st.threshold - CFG["LEARN_THR_STEP"])
        if st.threshold >= old - 1e-12:
            return None
        what = f"soglia {old:.3f} -> {st.threshold:.3f}"
    elif key.endswith("_grave") and key[:-6] in SOFT_FLAGS and SOFT_FLAGS[key[:-6]].get("hard"):
        flag = key[:-6]
        spec = SOFT_FLAGS[flag]
        old = soft_hard(st, flag)
        base = CFG[spec["hard"]]
        new = clamp(old + d * spec["dir"] * spec["step"], min(spec["lim"][0], base), max(spec["lim"][1], base))
        if abs(new - old) < 1e-9:
            return None
        e["hard_d"] = round(new - base, 4)
        what = f"soglia grave {spec['hard']} {old:g} -> {new:g}"
    elif key in SOFT_FLAGS:
        op, osz = soft_pen(st, key), soft_size(st, key)
        (bp, pl), (bs, sl) = _pen_base(key), _size_base(key)
        np_ = clamp(op - d * CFG["LEARN_PEN_STEP"], min(pl[0], bp), max(pl[1], bp))
        ns = clamp(osz + d * CFG["LEARN_SIZE_STEP"], min(sl[0], bs), max(sl[1], bs))
        if abs(np_ - op) < 1e-9 and abs(ns - osz) < 1e-9:
            return None
        e["pen_d"], e["size_d"] = round(np_ - bp, 4), round(ns - bs, 4)
        what = f"penalita {op:.3f} -> {np_:.3f}, size x{osz:.2f} -> x{ns:.2f}"
    else:
        return None
    e["last_adj"], e["adj"] = now, e["adj"] + 1
    act = "allenta" if loosen else "stringe"
    msg = (f"{act} '{key}' su {sts['n']} esiti (win {sts['win'] * 100:.0f}%, media {sts['avg']:+.1f}%, "
           f"rug {sts['rug'] * 100:.0f}%): {what}")
    st.lessons.append(f"{datetime.now(TZ):%d/%m %H:%M} {msg}")
    log(st, "Learner", "learn_adjust", msg, flag=key, action=act, n=sts["n"], win=sts["win"], avg=sts["avg"],
        rug=sts["rug"], change=what)
    return act


# ---------------------------------------------------------------- v8.5: RugCheck giu -> ripiego on-chain (Kiwi 08:08)
RC_HEALTH = {"down": False, "fails": 0, "last_ok": 0.0, "down_since": None, "alert_t": 0.0, "alerted": "up",
             "fb_ok": 0, "fb_skip": 0}
CFG.setdefault("RC_DOWN_FAILS", 5)            # errori di fila (senza un report riuscito negli ultimi 5 min)
CFG.setdefault("RC_ALERT_MIN_SEC", 3600)      # al massimo un avviso Telegram all'ora
CFG.setdefault("RC_FALLBACK", True)
CFG.setdefault("BUNDLE_EST_MIN_DAYS", 7.0)     # v8.5: bundle RugCheck soft solo per i token affermati
CFG.setdefault("BUNDLE_EST_MIN_MCAP", 5e6)
CFG.setdefault("BUNDLE_EST_MIN_HOLDERS", 5000)
CFG.setdefault("RC_DOWN_SIZE_MULT", 0.5)
PUMP_AMM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
SYSTEM_PROGRAM = "11111111111111111111111111111111"
AMM_AUTHORITIES = {"5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",   # Raydium AMM v4
                   "GpMZbSM2GgvTKHJirzeGfMFoaZ8UR2X7F4v8vHTvxFbL",   # Raydium CPMM
                   "WLHv2UAZm6z4KyaaELi5pjdbJh6RESMva1Rnn8pJVVh"}     # Raydium launchpad
SAFE_T22_EXT = {"metadataPointer", "tokenMetadata", "groupPointer", "groupMemberPointer", "tokenGroup",
                "tokenGroupMember", "immutableOwner"}
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58(b):
    n, out = int.from_bytes(b, "big"), ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + out


def rc_health(ok, now=None):
    """Stato di RugCheck + avviso Telegram alla caduta e al ritorno (massimo uno ogni RC_ALERT_MIN_SEC)."""
    now, h = now or time.time(), RC_HEALTH
    if ok:
        h["fails"], h["last_ok"] = 0, now
        if h["down"]:
            h["down"] = False
            write_event("Filter", "rugcheck_up", "RugCheck di nuovo disponibile", {"down_since": h["down_since"]})
            alert_state_save()   # v8.5g
    else:
        h["fails"] += 1
        if not h["down"] and h["fails"] >= CFG["RC_DOWN_FAILS"] and now - h["last_ok"] >= 300:
            h["down"], h["down_since"] = True, now
            write_event("Filter", "rugcheck_down", f"RugCheck non disponibile ({h['fails']} errori di fila): "
                        "ripiego on-chain attivo", {})
            alert_state_save()   # v8.5g
    want = "down" if h["down"] else "up"
    if want != h["alerted"] and now - h["alert_t"] >= CFG["RC_ALERT_MIN_SEC"]:
        h["alerted"], h["alert_t"] = want, now
        if want == "down":
            tg_send(f"RugCheck NON disponibile da {datetime.fromtimestamp(h['down_since'] or now, TZ):%H:%M} (Roma): "
                    "ripiego on-chain attivo (passa solo se authority, GoPlus, honeypot, cluster v2, LP bruciata e "
                    "top 10 sono tutti ok; size x0.5, tag rugcheck_down)")
        else:
            tg_send(f"RugCheck di nuovo disponibile ({datetime.now(TZ):%H:%M} Roma): ripiego spento. "
                    f"Nel frattempo: {h['fb_ok']} passati col ripiego, {h['fb_skip']} scartati/shadow")
        alert_state_save()   # v8.5g
    return h["down"]


def rc_down_doubts(st, cl, crh=None):
    """Review Claude: dubbi del ripiego che si possono verificare solo nell'Analyst (cluster v2 gia fatto).
    Lista vuota = nessun dubbio. Ogni dato mancante e un dubbio, mai un 'ok'."""
    d = []
    lv = (cl or {}).get("level", "unknown")
    if lv not in ("ok", "none", "soft"):   # v8.5e: 'none' = cluster v2 pulito
        d.append(f"cluster v2 '{lv}' (serve ok o soft)")
    # col ripiego totalHolders non c'e e MIN_HOLDERS in security_check veniva saltato: conta di cluster v2
    n = int(fnum((cl or {}).get("holders_total")))
    if n < CFG["MIN_HOLDERS"]:
        d.append(f"holder non verificati o pochi ({n} < {CFG['MIN_HOLDERS']})" if n else "numero di holder non verificato")
    # col ripiego r['creator'] non c'e: lista nera e quota del creatore in security_check venivano saltate.
    # Il creatore arriva dallo storico creatore (pump.fun); se non si conosce, la lista nera non e controllabile.
    cr = str((crh or {}).get("creator") or "")
    if not cr:
        d.append("creatore non verificabile (lista nera non controllata)")
    elif cr in st.blacklist:
        d.append("creatore in lista nera per un rug precedente")
    return d


async def _rpc_core(http, method, params):
    d = await _cl2_post(http, "https://solana-rpc.publicnode.com",
                        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, "core")
    return d.get("result") if isinstance(d, dict) else None


async def rc_fallback(http, mint, m):
    """Report 'stile RugCheck' costruito gratis on-chain (+ reti insider RugCheck, endpoint ancora attivo).
    None = non costruibile (=> scarto). Campi mancanti restano assenti: chi legge li tratta come dubbio."""
    acc = await _rpc_core(http, "getAccountInfo", [mint, {"encoding": "jsonParsed"}])
    acc = (acc or {}).get("value") if isinstance(acc, dict) else None
    info = (((acc or {}).get("data") or {}).get("parsed") or {}).get("info") if isinstance((acc or {}).get("data"), dict) else None
    if not info:
        return None
    exts = [str(e.get("extension")) for e in (info.get("extensions") or []) if isinstance(e, dict)]
    tf = 0.0
    for e in info.get("extensions") or []:
        if isinstance(e, dict) and e.get("extension") == "transferFeeConfig":
            tf = fnum(((e.get("state") or {}).get("newerTransferFee") or {}).get("transferFeeBasisPoints")) / 100
    r = {"_fallback": True, "mintAuthority": info.get("mintAuthority"), "freezeAuthority": info.get("freezeAuthority"),
         "token": {"supply": info.get("supply"), "decimals": info.get("decimals")}, "tokenProgram": (acc or {}).get("owner"),
         "transferFee": {"pct": tf}, "t22_risky": sorted(set(exts) - SAFE_T22_EXT), "risks": [], "knownAccounts": {}}
    # LP: solo pool PumpSwap del token con LP mint a offerta 0 (LP bruciata dalla migrazione pump.fun)
    markets = [{"marketType": "unknown", "pubkey": p, "lp": {"lpLockedPct": None}} for p in (m.get("pairs_all") or [])]
    pair = m.get("pair")
    if str(mint).endswith("pump") and m.get("dex") == "pumpswap" and pair:
        pa = await _rpc_core(http, "getAccountInfo", [pair, {"encoding": "base64"}])
        pa = (pa or {}).get("value") if isinstance(pa, dict) else None
        try:
            raw = base64.b64decode(pa["data"][0]) if pa and pa.get("owner") == PUMP_AMM else b""
        except Exception:  # noqa: BLE001
            raw = b""
        if len(raw) >= 139 and _b58(raw[43:75]) == mint:
            lpm = _b58(raw[107:139])
            sup = await _rpc_core(http, "getTokenSupply", [lpm])
            amt = (sup or {}).get("value", {}).get("amount") if isinstance(sup, dict) else None
            r["lp_mint"], r["lp_supply"] = lpm, amt
            if amt is not None and int(amt) == 0:
                r["launchpad"] = {"name": "Pump.Fun (verificato on-chain: LP bruciata)"}
                markets = [mk for mk in markets if mk["pubkey"] != pair] + \
                    [{"marketType": "pump_amm", "pubkey": pair, "lp": {"lpLockedPct": 100.0, "lpMint": lpm}}]
    r["markets"] = markets
    # top holder on-chain: 20 conti piu grandi -> proprietari -> esclusi pool/programmi (PDA) e authority AMM
    la = await _rpc_core(http, "getTokenLargestAccounts", [mint])
    la = (la or {}).get("value") if isinstance(la, dict) else None
    if not la:
        return None
    ta = await _rpc_core(http, "getMultipleAccounts", [[a["address"] for a in la], {"encoding": "jsonParsed"}])
    ta = (ta or {}).get("value") if isinstance(ta, dict) else None
    if not ta or len(ta) != len(la):
        return None
    owners = [((((x or {}).get("data") or {}).get("parsed") or {}).get("info") or {}).get("owner")
              if isinstance((x or {}).get("data"), dict) else None for x in ta]
    uniq = sorted({o for o in owners if o})
    oa = await _rpc_core(http, "getMultipleAccounts", [uniq, {"encoding": "base64", "dataSlice": {"offset": 0, "length": 0}}])
    oa = (oa or {}).get("value") if isinstance(oa, dict) else None
    if oa is None or len(oa) != len(uniq):
        return None
    for o, x in zip(uniq, oa):
        if o in AMM_AUTHORITIES or o in (m.get("pairs_all") or []):
            r["knownAccounts"][o] = {"type": "AMM", "name": "pool/authority"}
        elif x and x.get("owner") not in (SYSTEM_PROGRAM, None):
            r["knownAccounts"][o] = {"type": "PROGRAM", "name": str(x.get("owner"))[:8]}
    r["topHolders"] = [{"address": a["address"], "owner": o, "uiAmount": fnum(a.get("uiAmount")),
                        "pct": 0.0, "insider": False} for a, o in zip(la, owners)]
    # reti insider: endpoint RugCheck separato (ancora attivo durante il guasto dei report); errore = quota ignota
    nets = await http.get(f"https://api.rugcheck.xyz/v1/tokens/{mint}/insiders/networks")
    if isinstance(nets, list):   # schema dell'endpoint: currentHolding = quota attuale (come tokenAmount del report)
        nets = [dict(n, tokenAmount=n.get("tokenAmount", n.get("currentHolding"))) for n in nets if isinstance(n, dict)]
        r["insiderNetworks"], r["graphInsidersDetected"] = nets, len(nets)
    return r


async def security_check(st, http, mint, m, cache):
    hit = cache.get(mint)
    if not hit or time.time() - hit[0] > 600:
        data = await http.get(f"https://api.rugcheck.xyz/v1/tokens/{mint}/report")
        rc_health(isinstance(data, dict))
        if not isinstance(data, dict):
            data = await rc_fallback(http, mint, m) if CFG.get("RC_FALLBACK") else None
            if not isinstance(data, dict):
                return False, "report di sicurezza non disponibile, scartato per prudenza", "report assente", None
        cache[mint] = (time.time(), data)
        hit = cache[mint]
    r = hit[1]
    rc_down = bool(r.get("_fallback"))
    tok = r.get("token") or {}
    if r.get("mintAuthority") or tok.get("mintAuthority"):
        return False, "mint non revocata", "mint non revocata", None
    if r.get("freezeAuthority") or tok.get("freezeAuthority"):
        return False, "freeze attiva", "freeze attiva", None
    if r.get("rugged"):
        return False, "segnalato come rug", "rug", None
    tf = fnum((r.get("transferFee") or {}).get("pct"))
    if tf > CFG["MAX_TRANSFER_FEE_PCT"]:
        return False, f"tassa di trasferimento {tf:.1f}%", "tassa alta", None
    markets = r.get("markets") or []
    soft, severe = [], []  # v6: (segnale, testo) e (segnale, testo, categoria)
    bundle_est = False
    # pump.fun: curva (niente LP) o token migrati da pump.fun su PumpSwap/Raydium (LP bruciata dal protocollo).
    # Mint che finisce in 'pump' NON basta (si puo generare): serve anche il launchpad Pump.Fun o un mercato
    # pump_fun_amm nel report RugCheck. Tutti gli altri token: regola LP >= MIN_LP_LOCKED_PCT.
    lp_name = str((r.get("launchpad") or {}).get("name", "") if isinstance(r.get("launchpad"), dict) else "").lower()
    pump_amm = any("pump" in str(mk.get("marketType", "")).lower() for mk in markets if isinstance(mk, dict))
    pump_migrated = (mint.endswith("pump") and m["dex"] in ("pumpswap", "pumpfun", "raydium")
                     and ("pump" in lp_name or pump_amm))
    # v8.4e (Kiwi 07:07): stato LP per la barriera finale dell'Executor (lp_gate). 'zero' (0% bloccata/bruciata)
    # e 'unknown' (LP non verificabile) = MAI acquisto vero, solo shadow (tag lp_unlocked_zero / lp_unknown).
    lp_status, lp_block, lpmax = ("curve" if m["dex"] == "pumpfun" else "protocol"), None, None
    if m["dex"] != "pumpfun" and not pump_migrated:
        lps = [mk.get("lp") for mk in markets if isinstance(mk, dict) and isinstance(mk.get("lp"), dict)]
        if not lps or all(lp.get("lpLockedPct") is None for lp in lps):
            lp_status = "unknown"
            lp_block = ("lp_unknown", "LP non verificabile: stato LP sconosciuto, solo shadow (nessun acquisto)",
                        "LP non verificabile")
            lps = []
        # lpLockedPct di RugCheck conta sia la LP bloccata sia quella bruciata
        lpmax = max(fnum(lp.get("lpLockedPct")) for lp in lps) if lps else None
        if lpmax is not None and lpmax < LP_ZERO_MAX_PCT:
            lp_status = "zero"
            lp_block = ("lp_unlocked_zero", f"LP per niente bloccata o bruciata ({lpmax:.0f}%): solo shadow, "
                        f"mai acquisto", "LP sbloccata 0%")
        elif lpmax is not None:
            lp_status = "ok" if lpmax >= CFG["MIN_LP_LOCKED_PCT"] else "partial"
        if lpmax is not None and lp_status == "partial":
            # v7: LP sbloccata = SOFT. HARD solo se estremo: quasi tutta sbloccata E in mano al creatore,
            # oppure rimozione della liquidita gia iniziata.
            creator = str(r.get("creator") or "")
            creator_lp = max((sum(fnum(h.get("pct")) for h in (lp.get("holders") or []) if isinstance(h, dict)
                                  and creator and creator in (h.get("owner"), h.get("address"))) for lp in lps),
                             default=0.0)
            if lpmax <= CFG["LP_EXTREME_LOCKED_PCT"] and creator_lp >= CFG["LP_CREATOR_HOLD_PCT"]:
                return False, (f"LP bloccata solo al {lpmax:.0f}% e il {creator_lp:.0f}% dei token LP e del creatore"), \
                    "LP in mano al creatore", None
            liq_max = fnum(m.get("liq_max"))
            drop = (1 - m["liq"] / liq_max) * 100 if liq_max > 0 and m["liq"] > 0 else 0.0
            if drop >= CFG["LP_REMOVAL_DROP_PCT"]:
                return False, f"rimozione della liquidita gia iniziata (-{drop:.0f}% da quando lo osserviamo)", \
                    "rimozione LP", None
            lh = soft_hard(st, "lp_unlocked")
            soft.append(("lp_unlocked", f"LP bloccata/bruciata solo al {lpmax:.0f}%"))
            if lpmax < lh:  # fascia pesante: penalita e size doppie, il Learner sposta LP_HARD_PCT
                soft.append(("lp_unlocked_grave", f"LP sotto il {lh:g}%"))

    exclude = set()
    for mk in markets:
        for v in list(mk.values()) + list((mk.get("lp") or {}).values()):
            if isinstance(v, str):
                exclude.add(v)
    for addr, info in (r.get("knownAccounts") or {}).items():
        if isinstance(info, dict) and str(info.get("type", "")).upper() in ("AMM", "LOCKER", "BURN", "PROGRAM"):
            exclude.add(addr)
    holders = [h for h in (r.get("topHolders") or [])
               if h.get("address") not in exclude and h.get("owner") not in exclude]
    # v8.4f (review Claude f): indirizzi morti (incinerator) ed exchange noti fuori dal top 10, mostrati a parte
    supply_ui0 = fnum(tok.get("supply")) / 10 ** int(fnum(tok.get("decimals"))) if tok.get("supply") else 0.0
    _hp = lambda h: (fnum(h.get("uiAmount")) / supply_ui0 * 100 if supply_ui0 > 0 and fnum(h.get("uiAmount")) > 0  # noqa: E731
                     else fnum(h.get("pct")))
    dead_pct, cex_pct, cex_names, keep = 0.0, 0.0, [], []
    for h in holders:
        o, a = h.get("owner"), h.get("address")
        lab = holder_cex_label(o) or holder_cex_label(a)
        if o in DEAD_ADDRS or a in DEAD_ADDRS:
            dead_pct += _hp(h)
            exclude.add(a) if a else None
        elif lab:
            cex_pct += _hp(h)
            cex_names.append(lab)
        else:
            keep.append(h)
    holders = keep
    if not holders:
        return False, "lista holder non disponibile", "holder non disponibili", None
    # (v5) pcts = [pct]; se tutte <= 1 venivano moltiplicate x100 -> falsi "top 10 al 99%" sui token molto
    # distribuiti. v6: quota = uiAmount / offerta (RugCheck "pct" e gia in percento, usato come riserva)
    supply_ui = fnum(tok.get("supply")) / 10 ** int(fnum(tok.get("decimals"))) if tok.get("supply") else 0.0
    pcts = [fnum(h.get("uiAmount")) / supply_ui * 100 if supply_ui > 0 and fnum(h.get("uiAmount")) > 0
            else fnum(h.get("pct")) for h in holders]
    top10 = sum(sorted(pcts, reverse=True)[:10])
    t10h = soft_hard(st, "top10")
    if top10 > t10h:
        severe.append(("top10", f"top 10 holder al {top10:.1f}% (> {t10h:g}%)", "top 10 troppo alti"))
    elif top10 > CFG["MAX_TOP10_PCT"]:
        soft.append(("top10", f"top 10 al {top10:.1f}%"))

    # la lezione di Pangea: tanti wallet con la stessa identica quota (v6: grave se insieme tengono molto)
    same = Counter(round(p, 2) for p in pcts if p >= CFG["EQUAL_MIN_PCT"])
    if same:
        val, n = same.most_common(1)[0]
        if n >= CFG["MAX_IDENTICAL_WALLETS"]:
            tot, eh = val * n, soft_hard(st, "equal")
            if tot >= eh:
                severe.append(("equal", f"{n} wallet con la stessa quota ({val}%, insieme {tot:.1f}%): "
                                        f"distribuzione coordinata", "wallet identici"))
            else:
                soft.append(("equal", f"{n} wallet con quota {val}% (insieme {tot:.1f}%)"))
    insider_pct = sum(p for h, p in zip(holders, pcts) if h.get("insider"))
    if insider_pct > CFG["MAX_INSIDER_PCT"]:
        return False, f"insider al {insider_pct:.1f}%", "insider", None
    if CFG["REJECT_INSIDER_NETWORKS"] and fnum(r.get("graphInsidersDetected")) > 0:
        # v6: conta la quota dell'offerta nelle reti collegate (prima bastava una rete qualsiasi)
        bp, bh = bundle_pct(r), soft_hard(st, "bundle")
        _th = int(fnum(r.get("totalHolders")))
        established = (fnum(m.get("age_min")) >= CFG["BUNDLE_EST_MIN_DAYS"] * 1440
                       and fnum(m.get("mcap")) >= CFG["BUNDLE_EST_MIN_MCAP"] and _th >= CFG["BUNDLE_EST_MIN_HOLDERS"])
        if bp is None:
            soft.append(("bundle", "reti di wallet collegati (quota non calcolabile)"))
        elif bp >= bh and established:   # v8.5 (Kiwi): token affermato -> soft, ma serve cluster v2 ok/soft
            soft.append(("bundle", f"reti collegate al {bp:.1f}% (token affermato: {fnum(m.get('age_min')) / 1440:.0f} g, "
                                   f"mcap ${fnum(m.get('mcap')) / 1e6:.1f}M, {_th} holder)"))
            bundle_est = True
        elif bp >= bh:
            severe.append(("bundle", f"reti di wallet collegati al {bp:.1f}% dell'offerta (bundle)", "insider/bundle"))
        elif bp >= CFG["BUNDLE_SOFT_PCT"]:
            soft.append(("bundle", f"reti collegate al {bp:.1f}%"))
    creator = str(r.get("creator") or "")
    if creator and creator in st.blacklist:
        return False, "creatore in lista nera per un rug precedente", "creatore in lista nera", None
    creator_pct = sum(p for h, p in zip(holders, pcts)
                      if creator and creator in (h.get("owner"), h.get("address")))
    if creator_pct > CFG["MAX_CREATOR_PCT"]:
        return False, f"il creatore tiene il {creator_pct:.1f}%", "creatore con troppa quota", None
    total_holders = int(fnum(r.get("totalHolders")))
    if total_holders and total_holders < CFG["MIN_HOLDERS"]:
        return False, f"solo {total_holders} holder", "pochi holder", None
    # v6: i "danger" di RugCheck su concentrazione top 10 e LP sbloccata sono gia graduati sopra
    dangers = [x.get("name", "?") for x in (r.get("risks") or []) if str(x.get("level", "")).lower() == "danger"
               and not any(k in str(x.get("name", "")).lower() for k in ("top 10 holders", "lp unlocked"))]
    if dangers:
        return False, "rischi gravi: " + ", ".join(dangers[:3]), "rischi gravi RugCheck", None
    sec = {"top10": round(top10, 2), "holders": total_holders or len(holders), "creator": creator,
           "insider_pct": round(insider_pct, 2), "soft": [f for f, _ in soft], "soft_txt": [t for _, t in soft],
           # v7: per il controllo cluster (solo sui candidati): esclusi pool/locker, holder RugCheck, reti RugCheck
           "exclude": sorted(exclude)[:200], "token_program": r.get("tokenProgram"),
           "rc_holders": [(h.get("owner") or h.get("address"), round(p, 4)) for h, p in zip(holders, pcts)][:20],
           "rc_networks": rc_networks(r),
           # v8: metadati on-chain (RugCheck tokenMeta: name, symbol, uri, mutable) per il controllo progetto
           "token_meta": {k: (r.get("tokenMeta") or {}).get(k) for k in ("name", "symbol", "uri", "mutable",
                                                                           "updateAuthority")}
           if isinstance(r.get("tokenMeta"), dict) else None,
           "is_pump": bool(m["dex"] == "pumpfun" or pump_migrated),
           "cex_pct": round(cex_pct, 2), "cex_names": sorted(set(cex_names))[:5], "dead_pct": round(dead_pct, 2),
           "lp_status": lp_status, "lp_locked_pct": None if lpmax is None else round(lpmax, 2),
           "rc_down": rc_down, "bundle_established": bundle_est}
    if rc_down:   # v8.5: ripiego on-chain, ogni dubbio = niente acquisto (shadow rugcheck_down)
        doubt = []
        if r.get("t22_risky"):
            doubt.append("Token-2022 con estensioni " + ",".join(r["t22_risky"][:3]))
        if lp_status != "protocol":
            doubt.append(f"LP non verificata bruciata/bloccata (stato {lp_status})")
        if not r.get("topHolders"):
            doubt.append("top 10 non calcolato")
        if not isinstance(r.get("insiderNetworks"), list):
            # review Claude: senza l'endpoint delle reti insider (errore, 429, schema diverso) graphInsidersDetected
            # resta assente e il controllo bundle sopra veniva SALTATO: un bundle che RugCheck dava hard passava
            doubt.append("reti insider RugCheck non verificabili (controllo bundle non fatto)")
        if doubt:
            RC_HEALTH["fb_skip"] += 1
            sec.update(severe="rugcheck_down", shadow_keys=["rugcheck_down"], soft=["rugcheck_down"] + sec["soft"])
            return False, "RugCheck giu, ripiego on-chain con dubbi: " + "; ".join(doubt), "RugCheck giu (dubbio)", sec
    SEC_LAST[mint] = {"rc_holders": sec["rc_holders"][:5], "supply": supply_ui or None, "ts": time.time()}  # v8.4f
    if len(SEC_LAST) > 3000:
        for k in sorted(SEC_LAST, key=lambda k: SEC_LAST[k]["ts"])[:500]:
            SEC_LAST.pop(k, None)
    if lp_block:  # v8.4e: prima di ogni altro esito "soft": niente acquisto, shadow col tag dedicato
        sec.update(severe=lp_block[0], shadow_keys=[lp_block[0]], lp_block=lp_block[0])
        return False, lp_block[1], lp_block[2], sec
    if severe:  # soglia grave di un segnale soft: scarto (con shadow trade per imparare)
        f0, why, cat = severe[0]
        sec.update(severe=f0, shadow_keys=[f + "_grave" for f, _, _ in severe],
                   soft=[f for f, _, _ in severe] + sec["soft"])
        return False, why, cat, sec
    if len(base_flags(f for f, _ in soft)) >= CFG["SOFT_MAX_FLAGS"]:
        sec.update(severe="multi", shadow_keys=["multi"])
        return False, "troppi segnali soft insieme: " + "; ".join(sec["soft_txt"]), "troppi segnali soft", sec
    return True, f"top 10 al {top10:.1f}%" + (f" (exchange a parte {cex_pct:.1f}%)" if cex_pct >= 0.05 else "") + \
        (f", {sec['holders']} holder" if not rc_down else ", holder totali n/d (ripiego on-chain, size x0.5)") + ", LP ok" + \
        (f", segnali soft: {'; '.join(sec['soft_txt'])}" if soft else ""), None, sec


async def filter_agent(st, http, q_out):
    cache = {}
    while True:
        await asyncio.sleep(CFG["RECHECK_SEC"])
        now = time.time()
        for d, ttl in ((st.processed, 6 * 3600),):
            for k in [k for k, v in d.items() if now - v > ttl]:
                del d[k]
        for k in [k for k, v in st.cooldown.items() if v < now]:
            del st.cooldown[k]
        for k in [k for k, v in cache.items() if now - v[0] > 3600]:
            del cache[k]
        for mint, w in list(st.watch.items()):
            if now - w["seen"] > CFG["WATCH_TTL_MIN"] * 60:
                st.watch.pop(mint, None)
                reject(st, mint, w["sym"], "volume insufficiente nel periodo di osservazione",
                       cat="volume insufficiente", quiet=True)
        if not st.watch:
            continue
        order = sorted(st.watch, key=lambda k: (0 if st.watch[k].get("prio") else 1,
                                                -fnum((TREND_INFO.get(k) or {}).get("trending_score")),
                                                st.watch[k]["seen"]))
        market = await dex_batch(http, order)
        for mint in order:
            pairs = market.get(mint)
            try:
                if mint not in st.watch or not pairs:
                    continue
                m = summarize(pairs)
                w = st.watch[mint]
                w["liq_max"] = max(fnum(w.get("liq_max")), m["liq"])  # v7: per vedere una rimozione LP in corso
                m["liq_max"], m["src"] = w["liq_max"], w.get("src", "")
                if m["vol_h1"] < CFG["MIN_VOLUME_H1"]:
                    continue
                st.watch.pop(mint, None)
                st.processed[mint] = now
                st.stats["volume_ok"] += 1
                if mint in CASE_MINTS:   # v8.5: caso aperto = solo osservazione, mai acquisto (neanche paper)
                    reject(st, mint, m["symbol"], "caso aperto in osservazione (nessun acquisto)", cat="caso aperto",
                           quiet=True)
                    continue
                for word in words_of(m["name"], m["symbol"]):
                    st.hot.append((now, word, mint))
                if m["price"] <= 0 or m["liq"] <= 0 or m["liq"] < CFG["MIN_LIQUIDITY_USD"]:
                    reject(st, mint, m["symbol"], f"liquidita ${m['liq']:,.0f} troppo bassa", cat="liquidita bassa")
                    if m["liq"] <= 0 and m.get("dex") == "pumpfun" and w.get("src") != "curve_recheck":
                        curve_watch_add(mint, m, now)   # v8.5: ancora sulla curva -> ricontrollo dopo la migrazione
                    continue
                imp = ticker_impostor(m["symbol"], mint, m.get("name")) \
                    if CFG.get("TICKER_IMPOSTOR_ACTION") == "block" else None
                if imp:   # v8.4c: prima di ogni chiamata a pagamento (cluster, honeypot, Birdeye)
                    reject(st, mint, m["symbol"], f"ticker/nome {imp} di un grande asset/exchange ma mint non ufficiale "
                           f"(impostore)", cat="ticker impostore")
                    if CFG["SHADOW_TRADES"]:
                        spawn_shadow(st, http, mint, m, ["ticker_impostor"], f"ticker impostore {imp}", 0.0, [],
                                     tag="ticker_impostor")
                    continue
                if CFG["PF_ENABLED"] and "pf" in SRC:   # v8.3: prefiltro economico stile DexScreener
                    ti = TREND_INFO.get(mint) or {}
                    fails, snap = SRC["pf"].evaluate(m, pf_cfg(st), paid=bool(ti.get("paid")))
                    SRC["pf"].log_eval(mint, m["symbol"], m["src"], fails, snap)
                    if fails:
                        st.stats["prefilter_reject"] = st.stats.get("prefilter_reject", 0) + 1
                        reject(st, mint, m["symbol"], "prefiltro: " + ", ".join(fails), cat="prefiltro " + fails[0])
                        continue
                ok, reason, cat, sec = await security_check(st, http, mint, m, cache)
                if not ok:
                    reject(st, mint, m["symbol"], reason, cat=cat)
                    if sec and sec.get("lp_block"):  # v8.4e: LP 0%/sconosciuta: shadow sempre, mai Analyst/acquisto
                        if CFG["SHADOW_TRADES"]:
                            spawn_shadow(st, http, mint, m, [sec["lp_block"]], reason, 0.0, [],
                                         tag=sec["lp_block"], signals=[f"src:{m.get('src') or '?'}",
                                                                       "reason:" + sec["lp_block"]])
                        continue
                    if sec and sec.get("severe") == "rugcheck_down":   # v8.5: dubbio col ripiego = shadow, mai acquisto
                        if CFG["SHADOW_TRADES"]:
                            spawn_shadow(st, http, mint, m, ["rugcheck_down"], reason, 0.0, [], tag="rugcheck_down",
                                         signals=[f"src:{m.get('src') or '?'}", "reason:rugcheck_down"])
                        continue
                    if CFG["SHADOW_TRADES"] and sec and sec.get("severe"):
                        # v6: nessun acquisto possibile; l'Analyst valuta solo se aprire uno shadow trade
                        await q_out.put({"mint": mint, "m": m, "sec": sec, "filter_ok": True,
                                         "shadow_only": reason, "shadow_keys": sec.get("shadow_keys")})
                    continue
                st.stats["filter_pass"] += 1
                log(st, "Filter", "pass", f"{m['symbol']} superato: {reason}", mint=mint, src=m["src"])
                await q_out.put({"mint": mint, "m": m, "sec": sec, "filter_ok": True})
            except Exception as e:  # noqa: BLE001
                log(st, "Filter", "error", f"errore su un token ({type(e).__name__}), continuo")


# ======================================================================= narrative da X (narratives.json, Radar)
# Piccolo bonus sul punteggio dell'Analyst, applicato SOLO dopo Filter e controlli anti-rug: non salta mai un filtro.
NARR_PATH = os.path.join(DATA_DIR, "narratives.json")
NARR_MAX_AGE_H = 3.0          # updated_at piu vecchio di 3 ore = dati vecchi, nessun bonus
NARR_CAP = 0.08               # effetto totale massimo sul punteggio: tra -0.08 e +0.08
NARR_MIN_KW = 5               # parole chiave piu corte non contano
# (v4) bonus = forza x peso (1.0 / 0.5) x trend (rising 1.0, stable 0.6, fading 0) x scala, solo positivo
NARR_W_STRONG, NARR_W_WEAK = 1.0, 0.5   # (v4, non piu usati nella formula)
NARR_SCALE = 1.0                        # (v4, non piu usato)
NARR_TREND = {"rising": 1.0, "stable": 0.6, "fading": 0.0}
# decisioni finali: bonus solo per narrative giovani in crescita, penalita per quelle tardive e per le copie
NARR_BONUS_STRONG = 0.05      # ticker o mint uguale
NARR_BONUS_WEAK = 0.025       # parola chiave nel nome
NARR_BONUS_TRUSTED = 0.02     # in piu se trusted_mentions > 0
NARR_MAX_STRENGTH = 0.7       # bonus solo se strength < 0.7 (oltre e gia affollata)
NARR_PEN_LATE = -0.05         # strength >= 0.7 e velocity <= 0, oppure trend fading
NARR_PEN_COPYCAT = -0.05      # ticker uguale ma mint diverso da quelli citati, se uno ha confidenza >= 0.7
NARR_COPYCAT_CONF = 0.7
NARR_STABLE_TICKERS = {"USDC", "USDT", "SOL", "WSOL", "USD1", "PYUSD", "USDS", "DAI", "USDE", "FDUSD", "USDG",
                       "USDH", "UXD", "EURC"}


def _narr_list(x):
    return [str(v).strip() for v in x if isinstance(v, (str, int)) and not isinstance(v, bool) and str(v).strip()] \
        if isinstance(x, list) else []


def parse_narratives(text):
    """Funzione pura. Ritorna (updated_at o None, lista narrative pulite, blocklist dei mint).
    Solleva ValueError se il file non e valido. Campi null, assenti o di tipo sbagliato sono ignorati."""
    d = json.loads(text)
    if not isinstance(d, dict) or not isinstance(d.get("narratives"), list):
        raise ValueError("manca la lista 'narratives'")
    ts = None
    if isinstance(d.get("updated_at"), str):
        try:
            ts = datetime.fromisoformat(d["updated_at"].strip().replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=TZ)
        except ValueError:
            ts = None
    out = []
    for n in d["narratives"]:
        if not isinstance(n, dict):
            continue
        strength = clamp(fnum(n.get("strength")), 0.0, 1.0)
        trend = str(n.get("trend", "")).lower().strip()
        tickers = {t.lstrip("$").upper() for t in _narr_list(n.get("tickers"))} - NARR_STABLE_TICKERS
        mints = set(_narr_list(n.get("mints"))) - SKIP_MINTS
        kws = {k.lower() for k in _narr_list(n.get("keywords")) if len(k) >= NARR_MIN_KW}
        mc = n.get("mint_confidence") if isinstance(n.get("mint_confidence"), dict) else {}
        conf = {str(k): clamp(fnum(v, 1.0), 0.0, 1.0) for k, v in mc.items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)}
        vel = n.get("velocity")
        vel = float(vel) if isinstance(vel, (int, float)) and not isinstance(vel, bool) and math.isfinite(vel) else None
        tm = n.get("trusted_mentions")
        tm = int(tm) if isinstance(tm, int) and not isinstance(tm, bool) else 0
        out.append({"theme": str(n.get("theme") or "?")[:80], "strength": strength, "trend": trend,
                    "mult": NARR_TREND.get(trend, 0.0), "tickers": tickers, "mints": mints, "keywords": kws,
                    "mint_conf": conf, "velocity": vel, "trusted_mentions": tm})
    bl = set(_narr_list(d.get("blocklist_mints"))) - SKIP_MINTS
    return ts, out, bl


def narrative_bonus(narrs, mint, symbol, name, blocklist=()):
    """Funzione pura. Ritorna (effetto totale tra -NARR_CAP e +NARR_CAP, [(tema, tipo, effetto)]).
    Forte: simbolo uguale a un ticker o mint nella lista. Debole: il nome contiene una parola chiave (>=5 lettere).
    Bonus solo se trend rising, velocity > 0 (null = niente bonus) e strength < 0.7:
      +0.05 forte (per un mint x mint_confidence, se presente), +0.025 debole, +0.02 se trusted_mentions > 0.
    Penalita tardiva -0.05: strength >= 0.7 e velocity <= 0, oppure trend fading.
    Penalita copia -0.05: ticker uguale ma mint non tra quelli della narrativa, che ne ha uno con confidenza >= 0.7.
    Un mint in lista NON significa token 'ufficiale': e solo un segnale come gli altri."""
    sym = str(symbol or "").lstrip("$").upper().strip()
    if mint in SKIP_MINTS or sym in NARR_STABLE_TICKERS or mint in blocklist:
        return 0.0, []
    lname = str(name or "").lower()
    hits, total = [], 0.0
    for n in narrs:
        conf = n.get("mint_conf", {})
        if sym and sym in n["tickers"]:
            kind, w = "forte", 1.0
        elif mint in n["mints"]:
            kind, w = "forte", conf.get(mint, 1.0)
        elif lname and any(k in lname for k in n["keywords"]):
            kind, w = "debole", 1.0
        else:
            continue
        vel, strength, trend = n.get("velocity"), n["strength"], n["trend"]
        b = 0.0
        copycat = (kind == "forte" and sym in n["tickers"] and mint not in n["mints"] and mint not in conf
                   and any(v >= NARR_COPYCAT_CONF for v in conf.values()))
        if copycat:
            b += NARR_PEN_COPYCAT
            kind = "copia"
        elif trend == "rising" and vel is not None and vel > 0 and strength < NARR_MAX_STRENGTH:
            b += (NARR_BONUS_STRONG * w if kind == "forte" else NARR_BONUS_WEAK)
            if n.get("trusted_mentions", 0) > 0:
                b += NARR_BONUS_TRUSTED
        if trend == "fading" or (strength >= NARR_MAX_STRENGTH and vel is not None and vel <= 0):
            b += NARR_PEN_LATE
            kind += "+tardiva"
        if b == 0:
            continue
        hits.append((n["theme"], kind, round(b, 4)))
        total += b
    return round(clamp(total, -NARR_CAP, NARR_CAP), 4), hits


class Narratives:
    def __init__(self):
        self.key = None
        self.ts = None
        self.items = []
        self.blocklist = set()
        self.warned = None

    def _warn(self, st, tag, msg):
        if self.warned != tag:  # un solo avviso per situazione
            self.warned = tag
            log(st, "Analyst", "warn", msg, source="narratives.json")

    def current(self, st):
        """Rilegge il file solo se mtime/size cambiano. Ritorna le narrative valide e fresche, altrimenti []."""
        try:
            s = os.stat(NARR_PATH)
        except OSError:
            self.key, self.items, self.ts, self.blocklist = None, [], None, set()
            self._warn(st, "missing", "narratives.json assente: nessun bonus narrativa")
            return []
        key = (s.st_mtime_ns, s.st_size)
        if key != self.key:
            self.key = key
            try:
                with open(NARR_PATH, encoding="utf-8") as f:
                    self.ts, self.items, self.blocklist = parse_narratives(f.read(512 * 1024))
                self.warned = None
                write_event("Analyst", "info", f"narratives.json caricato: {len(self.items)} narrative",
                            {"source": "narratives.json"})
            except Exception as e:  # noqa: BLE001
                self.ts, self.items = None, []  # la blocklist precedente resta valida (prudenza)
                self._warn(st, f"invalid:{key}", f"narratives.json non valido ({type(e).__name__}): nessun bonus")
                return []
        if not self.items:
            return []
        if self.ts is None:
            self._warn(st, f"nots:{self.key}", "narratives.json senza updated_at valido: trattato come vecchio")
            return []
        age_h = (datetime.now(TZ) - self.ts).total_seconds() / 3600
        if age_h > NARR_MAX_AGE_H:
            self._warn(st, f"stale:{self.key}", f"narratives.json vecchio di {age_h:.1f} h (> {NARR_MAX_AGE_H:g} h): "
                                                f"nessun bonus")
            return []
        return self.items


NARR = Narratives()


# ======================================================================= Analyst
def add_shadow(st, mint, m, why, features=None):
    if len(st.shadow) >= 300:
        st.shadow.pop(min(st.shadow, key=lambda k: st.shadow[k]["t"]))
    st.shadow[mint] = {"t": time.time(), "price": m["price"], "sym": m["symbol"], "why": why,
                       "features": features}


async def analyst_agent(st, http, q_in, q_out):
    while True:
        c = await q_in.get()
        try:
            mint, m = c["mint"], c["m"]
            so = c.get("shadow_only")  # v6: scartato dal Filter per un segnale soft grave: solo shadow trade

            def rej(*a, _s=so, _mint=mint, _m=m, **k):
                if not _s:  # lo scarto e gia contato dal Filter
                    reject(st, _mint, _m["symbol"], *a, **k)
            if not c.get("filter_ok"):
                log(st, "Analyst", "block", f"{m['symbol']} bloccato: non arriva dal Filter")
                continue
            # v8.2: scansione cluster v2 per OGNI candidato che passa il Filter (in background: i dati servono
            # anche agli shadow); per un trade vero il risultato viene atteso prima della decisione
            cl_task = cluster2_start(st, http, mint, c.get("sec") or {}, sym=m["symbol"]) if cluster2_on() else None
            why = []
            if CFG.get("MAX_CHANGE_H1_PCT") is not None and m["chg_h1"] > CFG["MAX_CHANGE_H1_PCT"]:
                why.append(f"gia +{m['chg_h1']:.0f}% in 1h")
            if m["chg_h1"] < CFG["MIN_CHANGE_H1_PCT"]:
                why.append(f"{m['chg_h1']:.0f}% in 1h")
            # v8.4f: qui solo il taglio grossolano (come prima, -8); il gate vero (MIN_CHANGE_M5_PCT, 0) e m5_gate
            # dopo le candele, sullo stesso dato di ret_5m. Stesso numero di chiamate di prima.
            if m["chg_m5"] < min(fnum(CFG["MIN_CHANGE_M5_PCT"]), -8.0):
                why.append(f"{m['chg_m5']:.0f}% in 5 min")
            if why:
                rej("momento sbagliato: " + "; ".join(why), agent="Analyst",
                       cat="momento sbagliato")
                so or add_shadow(st, mint, m, "momento")
                so or narr_relax_shadow(st, http, mint, m, c, "momento/momentum/VWAP") or analyst_shadow(st, http, mint, m, c, "momento/momentum")
                continue
            # conferma del momentum all'ingresso (gate): pressione d'acquisto e volume che accelera
            bs5 = m["buys_m5"] / max(1, m["sells_m5"])
            if bs5 < CFG["MIN_BUYSELL_M5"]:
                rej(f"momentum non confermato: acq/vend 5m {bs5:.2f}",
                       agent="Analyst", cat="momentum 5m debole")
                so or add_shadow(st, mint, m, "momento")
                if not so and CFG["SHADOW_TRADES"] and CFG.get("BUYSELL_RELAX_SHADOW") \
                        and CFG["BUYSELL_RELAX_LOW"] <= bs5 < CFG["MIN_BUYSELL_M5"]:
                    # v8.5e: sicurezza ok, acq/vend 5m tra 1.20 e 1.30 -> SOLO shadow (misura), mai acquisto
                    st.stats["buysell_relax"] = st.stats.get("buysell_relax", 0) + 1
                    spawn_shadow(st, http, mint, m, ["buysell_relax"], f"Analyst: acq/vend 5m {bs5:.2f} "
                                 f"(fascia {CFG['BUYSELL_RELAX_LOW']:.2f}-{CFG['MIN_BUYSELL_M5']:.2f})", 0.0,
                                 list((c.get("sec") or {}).get("soft") or []), tag="buysell_relax",
                                 signals=[f"src:{m.get('src') or '?'}", "reason:buysell_relax", f"bs5:{bs5:.2f}"])
                    continue
                so or narr_relax_shadow(st, http, mint, m, c, "momento/momentum/VWAP") or analyst_shadow(st, http, mint, m, c, "momento/momentum")
                continue
            NARR.current(st)  # carica anche la blocklist (valida anche se le narrative sono vecchie)
            if mint in NARR.blocklist:
                rej("mint nella blocklist di Radar", agent="Analyst", cat="blocklist Radar")
                continue
            candles = await fetch_candles(http, m["pair"])
            ch = analyze_chart(candles) if candles else None
            g5 = m5_gate(m, candles)   # v8.4f: stesso dato di ret_5m (candele se presenti)
            if g5:
                rej(f"momento sbagliato: {g5}", agent="Analyst", cat="momento sbagliato")
                so or add_shadow(st, mint, m, "momento")
                so or narr_relax_shadow(st, http, mint, m, c, "momento/momentum/VWAP") or analyst_shadow(st, http, mint, m, c, "momento/momentum")
                continue
            # volume 5 min che accelera e prezzo sopra la VWAP dei 5 min (dalle candele, se ci sono)
            w5 = window5(candles) if candles else None
            vwap_gap = None
            if w5 is not None:
                accel = safe_div(w5["vol_now"], w5["vol_prev"], 99.0 if w5["vol_now"] > 0 else 0.0)
                ok_vol, src = w5["vol_now"] > w5["vol_prev"] * CFG["VOL_ACCEL_MIN"], "vs 5 min precedenti"
                if w5["vwap"] > 0:
                    vwap_gap = round((m["price"] / w5["vwap"] - 1) * 100, 2)
            else:  # (v4) fallback: volume 5 min sopra la media di 5 min dell'ultima ora
                avg5 = m["vol_h1_main"] / 12
                accel = safe_div(m["vol_m5"], avg5, 0.0)
                ok_vol, src = m["vol_m5"] > avg5 * CFG["VOL_ACCEL_MIN"], "vs media 1h"
            if not ok_vol:
                rej(f"momentum non confermato: volume 5m {accel:.2f}x {src}",
                       agent="Analyst", cat="momentum 5m debole")
                so or add_shadow(st, mint, m, "momento")
                so or narr_relax_shadow(st, http, mint, m, c, "momento/momentum/VWAP") or analyst_shadow(st, http, mint, m, c, "momento/momentum")
                continue
            if CFG["REQUIRE_ABOVE_VWAP5"] and vwap_gap is not None and vwap_gap <= 0:
                rej(f"prezzo sotto la VWAP 5 min ({vwap_gap:+.1f}%)",
                       agent="Analyst", cat="sotto VWAP 5m")
                so or add_shadow(st, mint, m, "momento")
                so or narr_relax_shadow(st, http, mint, m, c, "momento/momentum/VWAP") or analyst_shadow(st, http, mint, m, c, "momento/momentum")
                continue
            if ch is None and CFG["REQUIRE_CHART"]:
                rej("grafico non disponibile", agent="Analyst", cat="grafico assente")
                so or add_shadow(st, mint, m, "grafico")
                so or analyst_shadow(st, http, mint, m, c, "grafico assente")
                continue
            f = build_features(st, mint, m, c["sec"], ch)
            if ch is None:  # grafico assente: segnali di grafico neutri e penalita invece dello scarto
                for k in CHART_FEATURES:
                    f[k] = CFG["NO_CHART_NEUTRAL"]
            prior_note(mint, f)   # v8.7 (F): solo registrato
            score, rule, prob = final_score(st, f)
            if ch is None:
                score -= CFG["NO_CHART_PENALTY"]
            # bonus narrativa da X: qui il token ha gia superato Filter, anti-rug e controlli di momento
            nb, nhits = 0.0, []
            try:
                nb, nhits = narrative_bonus(NARR.current(st), mint, m["symbol"], m.get("name", ""), NARR.blocklist)
            except Exception as e:  # noqa: BLE001
                log(st, "Analyst", "warn", f"bonus narrativa non calcolato ({type(e).__name__})")
            if nb != 0:
                score += nb
                st.stats["narrative" if nb > 0 else "narrative_penalty"] += 1
                themes = "; ".join(f"{t} ({k} {b:+.3f})" for t, k, b in nhits)
                log(st, "Analyst", "narrative", f"{m['symbol']} narrativa {nb:+.3f}: {themes}", mint=mint,
                    bonus=nb, themes=[t for t, _, _ in nhits])
            # v6: segnali soft graduati -> penalita sul punteggio e size ridotta (valori appresi entro limiti)
            flags = list((c.get("sec") or {}).get("soft") or [])
            pen = sum(soft_pen(st, fl) for fl in flags)
            smult = 1.0
            for fl in flags:
                smult *= soft_size(st, fl)
            # v7: segnali extra (Fomo, KOL, tesi KOL, momentum giovane): solo qui, dopo TUTTI i filtri HARD;
            # la somma ha un tetto (TOTAL_BONUS_CAP) e un bonus non salva mai uno scarto HARD.
            signals = [f"src:{m.get('src') or '?'}"]
            fr = st.fomo_rank.get(mint)
            if fr and f"src:{fr['source']}" not in signals:
                signals.append(f"src:{fr['source']}")   # in trending anche se scoperto da un'altra fonte
            ti = TREND_INFO.get(mint)
            if ti:   # v8.3
                for tg in ("src:trending_agg", f"trend_score:{ti['trending_score']:.3f}",
                           "trend_src:" + "+".join(ti["families"])):
                    if tg not in signals:
                        signals.append(tg)
            bonus = 0.0
            # v8.5e: i controlli prima dell'acquisto (cluster v2, creatore, ripiego RugCheck) valgono anche per la fascia
            gate_thr = min(st.threshold, CFG["SCORE_BAND_LOW"]) if CFG.get("SCORE_BAND") else st.threshold
            if CFG["SIGNALS_ENABLED"] and not so and score - pen >= gate_thr - bonus_eff(st, "TOTAL_BONUS_CAP"):
                try:
                    bonus, btags = await compute_bonus(st, mint, m, c.get("sec"))
                    signals += [t for t in btags if t not in signals]
                except Exception as e:  # noqa: BLE001
                    log(st, "Analyst", "warn", f"bonus segnali non calcolati ({type(e).__name__})")
            score += bonus
            score_raw = score
            score -= pen
            # v8.4: penalita graduata per eta della pool + requisiti di attivita sopra i 3 giorni
            try:
                _vc = vol_from_candles(candles) if candles else None
            except Exception:  # noqa: BLE001
                _vc = None
            age_pen, age_rej, age_info = age_penalty(m, (_vc or {}).get("atr"))
            if age_rej:
                rej(age_rej, agent="Analyst", cat="token vecchio poco attivo")
                so or add_shadow(st, mint, m, "eta")
                continue
            if age_pen > 0:
                score -= age_pen
                pen += age_pen
                flags.append("eta_vecchia")
                signals.append(f"age:{age_info['age_d']:.1f}d/-{age_pen:.3f}" + ("/eased" if age_info.get("eased") else ""))
            # v8.2: cluster v2 prima della decisione. 'unknown' (errore/tempo scaduto) non vale mai come 'ok'.
            cl = None
            if cluster2_on() and not so and score >= gate_thr:
                cl = await cluster2_wait(st, http, mint, c.get("sec") or {}, cl_task, m["symbol"])
                signals.append(cluster2_signal(cl))
                if cl["level"] == "hard":
                    reason = f"cluster di wallet collegati: {cl['why']}"
                    rej(reason, agent="Analyst", cat="cluster")
                    st.labels.setdefault(mint, "bad_cluster")
                    if CFG["SHADOW_TRADES"]:  # la soglia rigida non viene mai allentata: si misura soltanto
                        spawn_shadow(st, http, mint, m, ["cluster_hard"], reason, score, flags + ["cluster"],
                                     tag="cluster", signals=signals)
                    continue
                if cl["level"] == "unknown":
                    if CFG["CLUSTER_UNKNOWN_ACTION"] == "skip":
                        rej(f"cluster non verificabile: {cl.get('why')}", agent="Analyst", cat="cluster non verificato")
                        st.stats["cluster_unknown_skip"] += 1   # v8.5: conteggio dei token finiti nello skip
                        if CFG["SHADOW_TRADES"]:
                            spawn_shadow(st, http, mint, m, ["cluster_unknown"], "cluster non verificabile", score,
                                         flags + ["cluster_unknown"], tag="cluster_unknown", signals=signals)
                        continue
                    if "cluster_unknown" not in flags:
                        flags.append("cluster_unknown")
                        pen += soft_pen(st, "cluster_unknown")
                        score -= soft_pen(st, "cluster_unknown")
                        smult *= soft_size(st, "cluster_unknown")
                if cl["level"] == "soft" and "cluster" not in flags:
                    flags.append("cluster")
                    pen += soft_pen(st, "cluster")
                    score -= soft_pen(st, "cluster")
                    smult *= soft_size(st, "cluster")
            elif CFG["CLUSTER_CHECK"] and not so and score >= gate_thr:   # v7 (solo se cluster_v2 manca)
                try:
                    cl = await cluster_scan(st, http, mint, c.get("sec") or {})
                except Exception as e:  # noqa: BLE001
                    log(st, "Analyst", "warn", f"controllo cluster non riuscito ({type(e).__name__})")
                if cl:
                    signals.append(f"cluster:{cl['level']}:{cl['networks']}n/{cl['wallets']}w/"
                                   f"{cl['net_pct']:.1f}%/fresh{cl['fresh_share']:.2f}")
                    log(st, "Analyst", "cluster", f"{m['symbol']} cluster {cl['level']}: {cl['why'] or 'nessuno'}",
                        mint=mint, **{k: cl[k] for k in ("level", "networks", "net_pct", "wallets", "fresh_share",
                                                         "scanned")})
                if cl and cl["level"] == "hard":
                    reason = f"cluster estremo di wallet collegati: {cl['why']}"
                    rej(reason, agent="Analyst", cat="cluster")
                    if CFG["SHADOW_TRADES"]:
                        spawn_shadow(st, http, mint, m, ["cluster_hard"], reason, score, flags + ["cluster"],
                                     tag="cluster", signals=signals)
                    continue
                flag = "cluster" if cl and cl["level"] == "soft" else ("cluster_unknown" if not cl else None)
                if flag and flag not in flags:   # v8.2: anche qui un fallimento non vale come 'ok'
                    flags.append(flag)
                    pen += soft_pen(st, flag)
                    score -= soft_pen(st, flag)
                    smult *= soft_size(st, flag)
            # v8.4e (Kiwi): storico del creatore, solo per i candidati a un acquisto vero (2 chiamate pump.fun
            # gratuite per creatore nuovo, poi cache). 'unknown' = nessun effetto.
            crh = None   # review Claude: mai il valore del token precedente (letto dal ripiego RugCheck qui sotto)
            if CFG.get("CREATOR_HISTORY", True) and "crh" in SRC and not so and score >= gate_thr \
                    and str(mint).endswith("pump") and callable(getattr(http, "get", None)):
                crh = await SRC["crh"].lookup(http.get, mint, (c.get("sec") or {}).get("creator") or None)
                signals.append(f"creator:{crh['level']}:{crh.get('dead', 0)}/{crh.get('prev', 0)}")
                if crh["level"] != "unknown":
                    log(st, "Analyst", "creator", f"{m['symbol']} {crh['why']} -> {crh['level']}", mint=mint,
                        level=crh["level"], prev=crh.get("prev"), dead=crh.get("dead"), share=crh.get("share"),
                        creator=str(crh.get("creator") or "")[:6])
                if crh["level"] == "hard":
                    reason = f"storico del creatore: {crh['why']}"
                    rej(reason, agent="Analyst", cat="creatore seriale")
                    if CFG["SHADOW_TRADES"]:
                        spawn_shadow(st, http, mint, m, ["creator_serial"], reason, score, flags + ["creatore"],
                                     tag="creator_serial", signals=signals)
                    continue
                if crh["level"] == "soft" and fnum(crh.get("pen")) > 0:
                    score -= fnum(crh["pen"])
                    pen += fnum(crh["pen"])
                    flags.append("creatore")
            # v8.5 (Kiwi): RugCheck giu (ripiego) o bundle soft da token affermato -> serve cluster v2 ok/soft
            _sec = c.get("sec") or {}
            if (_sec.get("rc_down") or _sec.get("bundle_established")) and not so and score >= gate_thr:
                _lv = (cl or {}).get("level", "unknown")
                why_tag = "rugcheck_down" if _sec.get("rc_down") else "bundle_established"
                doubts = rc_down_doubts(st, cl, crh) if why_tag == "rugcheck_down" else \
                    ([] if _lv in ("ok", "none", "soft") else [f"cluster v2 '{_lv}' (serve ok o soft)"])
                if doubts:   # v8.5e: 'none' = cluster v2 pulito (accettato anche qui e in rc_down_doubts)
                    reason = f"{'RugCheck giu' if why_tag == 'rugcheck_down' else 'bundle su token affermato'}: " \
                             + "; ".join(doubts)
                    rej(reason, agent="Analyst", cat="RugCheck giu (dubbio)" if why_tag == "rugcheck_down"
                        else "bundle affermato senza cluster")
                    if why_tag == "rugcheck_down":
                        RC_HEALTH["fb_skip"] += 1
                    if CFG["SHADOW_TRADES"]:
                        spawn_shadow(st, http, mint, m, [why_tag], reason, score, flags + [why_tag], tag=why_tag,
                                     signals=signals)
                    continue
                if why_tag == "rugcheck_down" or _sec.get("rc_down"):
                    smult *= CFG["RC_DOWN_SIZE_MULT"]
                    flags.append("rugcheck_down")
                    signals.append("rugcheck_down")
            if bonus:
                signals.append(f"bonus:{bonus:.3f}")
            detail = f"punteggio {score:.2f} (soglia {st.threshold:.2f})" + \
                (f", grafico assente -{CFG['NO_CHART_PENALTY']:.2f}" if ch is None else "") + \
                (f", narrativa {nb:+.3f}" if nb != 0 else "") + \
                (f", segnali {bonus:+.3f}" if bonus else "") + \
                (f", segnali soft {'/'.join(flags)} -{pen:.2f}" if flags else "")
            near = CFG["SHADOW_NEAR_SCORE"]
            if so:
                if CFG["SHADOW_TRADES"] and score >= st.threshold - near:
                    spawn_shadow(st, http, mint, m, c.get("shadow_keys") or ["multi"], f"scartato: {so}", score,
                                 flags, signals=signals)
                continue
            band = (CFG.get("SCORE_BAND") and not so and CFG["MODE"] == "paper" and cl is not None
                    and gate_thr <= score < st.threshold)
            if band:   # v8.5e: fascia 0.45-0.50, tutti i controlli passati -> acquisto PAPER a size x0.5
                smult *= CFG["SCORE_BAND_SIZE_MULT"]
                flags.append("score_band")
                signals.append(f"score_band:{score:.3f}")
                st.stats["score_band"] = st.stats.get("score_band", 0) + 1
                detail += f", fascia {gate_thr:.2f}-{st.threshold:.2f}: PAPER size x{CFG['SCORE_BAND_SIZE_MULT']:g}"
            elif score < st.threshold:
                rej(detail, agent="Analyst", cat="punteggio basso")
                add_shadow(st, mint, m, "punteggio", f)
                if not flags and narr_relax_shadow(st, http, mint, m, c, "punteggio basso", score):
                    continue   # v8.5: solo shadow narrative_relax (misura), nessun acquisto
                if CFG["SHADOW_TRADES"] and score_raw >= st.threshold - near:
                    spawn_shadow(st, http, mint, m, flags or ["score"], detail, score, flags, signals=signals)
                continue
            tags = [k for k in ("trend", "macd", "rsi", "trending", "narrative") if f[k] >= 1.0]
            rsi_txt = f"RSI {ch['rsi']:.0f}" if ch and ch["rsi"] is not None else "RSI n/d"
            c.update({"analyst_ok": True, "features": f, "score": score, "narr_bonus": nb,
                      "soft": flags, "size_mult": round(smult, 4), "score_raw": round(score_raw, 4),
                      "signals": "|".join(signals), "cluster": cl,
                      "narr_themes": [t for t, _, _ in nhits], "bs5": round(bs5, 2), "accel": round(accel, 2),
                      "vwap_gap": vwap_gap, "candles": candles,  # v8.1: per le feature d'ingresso
                      "thesis": f"{detail}, {rsi_txt}, acq/vend {m['buys_h1']}/{m['sells_h1']}, "
                                f"vol ${m['vol_h1']:,.0f}, segnali: {', '.join(tags) or 'nessuno forte'}"})
            log(st, "Analyst", "approve", f"{m['symbol']} approvato: {c['thesis']}", mint=mint)
            await q_out.put(c)
        except Exception as e:  # noqa: BLE001
            log(st, "Analyst", "error", f"errore su un token ({type(e).__name__}), continuo")


# ======================================================================= Executor (broker)
SOL_USD = [0.0, 0.0]  # (prezzo, ora dell'ultimo aggiornamento) per la priority fee simulata


def sol_usd():
    return SOL_USD[0] if SOL_USD[0] > 0 else CFG["SOL_USD_FALLBACK"]


async def refresh_sol_usd(http):
    """Aggiorna il prezzo di SOL ogni 10 minuti. v8b: GeckoTerminal, poi Jupiter price v3, poi DexScreener
    (coppia SOL/USDC); se tutte falliscono riprova dopo 60 s invece di 10 min. Finche il prezzo non e noto
    (SOL_USD[0] == 0) il broker paper NON usa i preventivi Jupiter per valutare/vendere (vedi sol_known)."""
    if time.time() - SOL_USD[1] < 600:
        return
    SOL_USD[1] = time.time()
    srcs = [
        (f"https://api.geckoterminal.com/api/v2/simple/networks/solana/token_price/{WSOL}",
         lambda d: d["data"]["attributes"]["token_prices"][WSOL]),
        (f"https://lite-api.jup.ag/price/v3?ids={WSOL}", lambda d: d[WSOL]["usdPrice"]),
        ("https://api.dexscreener.com/latest/dex/pairs/solana/58oQChx4yWmvKdwLLZzBi4ChoCc2fqCUWBkwMihLYQo2",
         lambda d: d["pairs"][0]["priceUsd"]),
    ]
    for url, pick in srcs:
        try:
            d = await http.get(url, headers={"Accept": "application/json"})
            v = fnum(pick(d))
            if 1 < v < 100000:
                SOL_USD[0] = v
                return
        except Exception:  # noqa: BLE001
            continue
    SOL_USD[1] = time.time() - 540  # nessuna fonte: riprova tra 60 s


def sol_known():
    """v8b: True solo con un prezzo SOL reale (non il fallback). Bug del 30/10: GeckoTerminal in 429 al riavvio,
    fallback $150 contro SOL reale ~$95 -> valori di uscita gonfiati del 58% e due prese di profitto finte."""
    return SOL_USD[0] > 0


# v6: priority fee dinamica (SOL per transazione, base fee inclusa). Se i dati sono vecchi o mancanti si usa
# la fee fissa PRIORITY_FEE_SOL della v5.
PRIO_DYN = {"sol": 0.0, "urgent": 0.0, "ts": 0.0, "last_try": 0.0, "micro": 0.0, "micro_urgent": 0.0}


def percentile(vals, pct):
    v = sorted(vals)
    if not v:
        return 0.0
    i = min(len(v) - 1, max(0, int(round(pct / 100 * (len(v) - 1)))))
    return float(v[i])


def prio_sol_from_micro(micro, mult=1.0):
    """micro-lamport per unita di calcolo -> SOL per transazione, con minimo, tetto e base fee."""
    cap = max(CFG["PRIO_FLOOR_LAMPORTS"], CFG["PRIORITY_MAX_LAMPORTS"])
    lam = clamp(micro * CFG["SWAP_COMPUTE_UNITS"] / 1e6, CFG["PRIO_FLOOR_LAMPORTS"], cap)
    lam = min(cap, lam * mult)  # urgente: moltiplicatore anche sul minimo
    return (lam + CFG["SIM_BASE_FEE_LAMPORTS"]) / 1e9


def prio_fee_sol(urgent=False):
    fresh = PRIO_DYN["ts"] and time.time() - PRIO_DYN["ts"] < 3 * max(60, CFG["PRIO_REFRESH_SEC"])
    if CFG.get("PRIO_DYNAMIC") and fresh:
        return PRIO_DYN["urgent" if urgent else "sol"]
    return CFG["PRIORITY_FEE_SOL"]


def prio_fee_usd(urgent=False):
    # (v5) return CFG["PRIORITY_FEE_SOL"] * sol_usd()
    return prio_fee_sol(urgent) * sol_usd()


async def refresh_prio_fee(http, st=None, force=False):
    """Legge getRecentPrioritizationFees (ultimi ~150 slot) per gli AMM usati dai memecoin."""
    if http is None or not CFG.get("PRIO_DYNAMIC"):
        return False
    if not force and time.time() - PRIO_DYN["last_try"] < CFG["PRIO_REFRESH_SEC"]:
        return False
    PRIO_DYN["last_try"] = time.time()
    norm, urg = [], []
    for acc in CFG["PRIO_ACCOUNTS"]:
        d = await http.post(CFG["RPC_URL"], {"jsonrpc": "2.0", "id": 1, "method": "getRecentPrioritizationFees",
                                             "params": [[acc]]})
        res = d.get("result") if isinstance(d, dict) else None
        if not isinstance(res, list) or not res:
            continue
        vals = [fnum(x.get("prioritizationFee")) for x in res if isinstance(x, dict)]
        norm.append(percentile(vals, CFG["PRIO_PERCENTILE"]))
        urg.append(percentile(vals, CFG["PRIO_PERCENTILE_URGENT"]))
    if not norm:
        return False
    PRIO_DYN.update({"micro": max(norm), "micro_urgent": max(urg), "ts": time.time(),
                     "sol": prio_sol_from_micro(max(norm)),
                     "urgent": prio_sol_from_micro(max(urg), CFG["PRIO_URGENT_MULT"])})
    write_event("Executor", "prio_fee", "priority fee aggiornata",
                {"prio_fee_sol": round(PRIO_DYN["sol"], 8), "prio_fee_sol_urgent": round(PRIO_DYN["urgent"], 8),
                 "p_micro": PRIO_DYN["micro"], "p_micro_urgent": PRIO_DYN["micro_urgent"]})
    return True


# v6: preventivi Jupiter (API gratuita, nessuna chiave) e decimali dei token
SIM = {"rng": random.Random(), "dec": {}}


async def jup_quote(http, inp, out, amount, slip_bps=None):
    """Ritorna (preventivo, errore). errore 'no_route' = Jupiter dice che non c'e percorso (come in live)."""
    amount = int(amount)
    if amount <= 0:
        return None, "importo nullo"
    url = (f"{CFG['JUP_BASE']}/quote?inputMint={inp}&outputMint={out}&amount={amount}"
           f"&slippageBps={int(slip_bps or CFG['SLIPPAGE_BPS'])}&restrictIntermediateTokens=true")
    await http._wait(url)
    try:
        async with http.s.get(url) as r:
            txt = await r.text()
            if r.status == 200:
                q = json.loads(txt)
                if isinstance(q, dict) and int(fnum(q.get("outAmount"))) > 0:
                    return q, None
                return None, "no_route"
            if r.status == 400 and "errorCode" in txt:
                return None, "no_route"
            return None, f"http {r.status}"
    except Exception as e:  # noqa: BLE001
        return None, f"rete ({type(e).__name__})"


async def token_decimals(http, mint):
    if mint in SIM["dec"]:
        return SIM["dec"][mint]
    d = await http.post(CFG["RPC_URL"], {"jsonrpc": "2.0", "id": 1, "method": "getTokenSupply", "params": [mint]})
    try:
        dec = int(d["result"]["value"]["decimals"])
    except Exception:  # noqa: BLE001
        if not mint.endswith("pump"):
            return None
        dec = 6  # i token pump.fun hanno sempre 6 decimali (l'RPC pubblico a volte risponde 429)
    SIM["dec"][mint] = dec
    return dec


def is_urgent(reason):
    r = str(reason).lower()
    return any(k in r for k in ("crollo", "stop loss", "liquidita crollata", "rug", "arresto", "nuovo rischio"))


def side_slippage(usd, liq):
    """Slippage extra per lato: SLIPPAGE_MULT x size / liquidita (in aggiunta all'impatto sulla pool)."""
    return clamp(CFG["SLIPPAGE_MULT"] * usd / max(liq, 1.0), 0.0, 0.5)


def exit_value(tokens, price, liq):
    """Valore netto di una vendita: impatto sulla pool + slippage 2 x size/liq + commissione + priority fee.
    (v4: solo impatto + commissione, stessa formula del bot Terminus)"""
    gross = tokens * price
    if gross <= 0:
        return 0.0
    impact = gross / (max(liq, 1.0) / 2 + gross)
    v = gross * (1 - impact) * (1 - side_slippage(gross, liq)) * (1 - CFG["FEE_PCT"] / 100) - prio_fee_usd()
    return max(0.0, v)


def cpmm_exit(tokens, price, liq, drained=0.0, dex=None):
    """v8.6 (A, solo log): valore netto di una vendita su pool x*y=k. La riserva in quote che resta e
    liq/2 meno quanto gia drenato dalle nostre vendite precedenti; uscita esatta out = R*g/(R+g), poi slippage
    2 x size/liq, fee della pool del dex (CPMM_POOL_FEE_PCT, altrimenti FEE_PCT) e priority fee.
    Ritorna (valore_netto, quote_tolte_dalla_pool)."""
    gross = tokens * price
    if gross <= 0 or price <= 0:
        return 0.0, 0.0
    rem = max(max(liq, 1.0) / 2 - max(drained, 0.0), 1.0)
    out = rem * gross / (rem + gross)
    fee = fnum((CFG.get("CPMM_POOL_FEE_PCT") or {}).get(str(dex or "")), CFG["FEE_PCT"])
    v = out * (1 - side_slippage(gross, liq)) * (1 - fee / 100) - prio_fee_usd()
    return max(0.0, v), out


def cpmm_track(o, tokens, price, liq, zero=False, cap_price=None, just_realized=0.0):
    """v8.6 (A): aggiorna realized_cpmm/drained_cpmm di posizione o shadow (inizializzati dal realized attuale
    per le posizioni aperte prima della v8.6). Mai eccezioni."""
    try:
        if "realized_cpmm" not in o:
            # v8.7 fix: 'realized' include gia la vendita in corso (just_realized): non contarla due volte
            o["realized_cpmm"], o["drained_cpmm"] = fnum(o.get("realized")) - fnum(just_realized), 0.0
        if zero:
            return
        px = min(price, cap_price) if cap_price and cap_price > 0 else price
        v, out = cpmm_exit(tokens, px, liq, o.get("drained_cpmm", 0.0), o.get("dex"))
        o["realized_cpmm"] += v
        o["drained_cpmm"] = o.get("drained_cpmm", 0.0) + out
    except Exception:  # noqa: BLE001
        pass


def tp_cap_price(price, pnl, rung):
    """v8.6 (A): prezzo a cui la presa di profitto si sarebbe riempita come limite (pnl = gradino)."""
    if pnl is None or rung is None or pnl <= rung or pnl <= -99:
        return None
    return price * (1 + rung / 100) / (1 + pnl / 100)


STOPISH = ("stop loss", "stop a pareggio", "crollo a", "liquidita crollata")


def exit_lag_fields(o, reason, value, tokens_sold, now=None):
    """v8.6 (A, solo log): per le uscite stop/crollo: livello teorico, fill effettivo netto (% sull'ingresso)
    e secondi dal primo controllo sotto lo stop alla vendita (+ intervallo prima del primo controllo sotto)."""
    out = {"stop_level": None, "exit_fill": None, "exit_lag_s": None}
    try:
        if not any(k in reason for k in STOPISH):
            return out, {}
        now = now or time.time()
        eff = o.get("eff") or {}
        lvl = eff.get("crash") if reason.startswith("crollo") else eff.get("stop")
        basis = tokens_sold * fnum(o.get("entry_mark"))
        out["stop_level"] = round(fnum(lvl), 2) if lvl is not None else None
        out["exit_fill"] = round(value / basis * 100 - 100, 2) if basis > 0 else None
        t0 = o.get("stop_t0")
        out["exit_lag_s"] = round(now - t0, 1) if t0 else 0.0
        return out, {"exit_gap_prev_s": o.get("stop_t0_gap"), "exit_mark_pct": o.get("stop_t0_pnl"),
                     "chk_gap_avg_s": round(o["chk_sum"] / o["chk_n"], 1) if o.get("chk_n") else None}
    except Exception:  # noqa: BLE001
        return out, {}


def stop_watch(o, pnl, stop_eff, crash_eff, now=None):
    """v8.6 (A): primo controllo con pnl <= stop (o crollo) e intervallo medio tra i controlli. Solo misura."""
    now = now or time.time()
    last = o.get("last_chk")
    if last:
        o["chk_sum"], o["chk_n"] = o.get("chk_sum", 0.0) + (now - last), o.get("chk_n", 0) + 1
    if pnl <= max(stop_eff, crash_eff):
        if not o.get("stop_t0"):
            o["stop_t0"], o["stop_t0_pnl"] = now, round(pnl, 2)
            o["stop_t0_gap"] = round(now - last, 1) if last else None
    else:
        o.pop("stop_t0", None)
    o["last_chk"] = now


def buy_tokens(size_usd, price, liq):
    """Simulazione d'acquisto: ritorna (token, fill, impatto, commissione) con gli stessi costi di exit_value."""
    fee = size_usd * CFG["FEE_PCT"] / 100
    net = size_usd - fee - prio_fee_usd()
    if net <= 0 or price <= 0:
        return 0.0, 0.0, 0.0, fee
    impact = net / (max(liq, 1.0) / 2 + net)
    fill = price * (1 + impact) / (1 - side_slippage(net, liq))
    return net / fill, fill, impact, fee


def roundtrip_cost_pct(size_usd, liq):
    """Costo stimato andata e ritorno in % della size, a prezzo invariato."""
    tokens, _, _, _ = buy_tokens(size_usd, 1.0, liq)
    if tokens <= 0:
        return 100.0
    return (1 - exit_value(tokens, 1.0, liq) / size_usd) * 100


def mark_value(p):
    # (orig.) return p["tokens"] * p["last_price"] * (1 - CFG["FEE_PCT"] / 100)
    # v6: se c'e un preventivo di vendita Jupiter recente usa il suo rapporto di esecuzione reale
    q = p.get("qmark")
    if q and CFG.get("SIM_REALISTIC") and p.get("tokens", 0) > 0 and q.get("ratio", 0) > 0 \
            and time.time() - q.get("ts", 0) < 3 * max(15, CFG["MARK_QUOTE_SEC"]):
        v = p["tokens"] * p["last_price"] * q["ratio"] * (1 - CFG["SIM_QUOTE_EXTRA_FEE_PCT"] / 100) - prio_fee_usd()
        return max(0.0, v)
    return exit_value(p["tokens"], p["last_price"], p.get("liq", 1.0))


def mark_pnl_pct(p):
    # (orig.) prezzo contro prezzo d'ingresso, senza costi d'uscita:
    # return (p["last_price"] / p["entry_mark"] - 1) * 100 if p["entry_mark"] > 0 else 0.0
    basis = p["tokens"] * p["entry_mark"]  # costo reale della quantita ancora in mano
    return (mark_value(p) / basis - 1) * 100 if basis > 0 else 0.0


def equity(st):
    return st.cash + sum(mark_value(p) for p in st.positions.values())


class PaperBroker:
    """Paper. Senza http (test) usa la formula v5. Con http e SIM_REALISTIC simula il live: preventivi Jupiter
    reali alla size vera, latenza 1-3 s, transazioni che falliscono (pagando comunque la fee), slippage
    massimo come in live, priority fee dinamica e SOL di riserva. NON firma e NON invia nulla."""
    mode = "paper"

    def __init__(self, http=None):
        self.http = http
        self.realistic = http is not None and bool(CFG.get("SIM_REALISTIC"))
        if CFG.get("SIM_SEED") is not None:
            SIM["rng"].seed(CFG["SIM_SEED"])

    async def setup(self, st):
        return None

    async def cash_usd(self, st):
        if not self.realistic:
            return st.cash
        # v6: come in live restano sempre SOL_RESERVE SOL per le fee + l'affitto dei conti token aperti
        locked = (CFG["SOL_RESERVE"] + CFG["SIM_ATA_RENT_SOL"] * len(st.positions)) * sol_usd()
        return max(0.0, st.cash - locked)

    # ---------------------------------------------------------------- formula v5 (fallback)
    def _buy_formula(self, st, m, size_usd, src="formula", why=None):
        # (v4) fee 1% + impatto; (v5) anche slippage 2 x size/liq e priority fee (vedi buy_tokens)
        tokens, fill, impact, fee = buy_tokens(size_usd, m["price"], m["liq"])
        if fill <= 0 or tokens <= 0:
            return {"ok": False, "error": "prezzo non valido"}
        st.cash -= size_usd
        return {"ok": True, "cost": size_usd, "tokens": tokens, "fill": fill, "impact": impact, "fee": fee,
                "src": src, "prio_fee_sol": prio_fee_sol(), "note": f"formula ({why})" if why else None}

    def _sell_formula(self, st, p, fraction, urgent=False):
        tokens = p["tokens"] * fraction
        value = exit_value(tokens, p["last_price"], p["liq"])  # stessi costi del valore di mercato
        st.cash += value
        return {"ok": True, "value": value, "tokens": tokens, "src": "formula", "prio_fee_sol": prio_fee_sol(urgent)}

    def _sol_in(self, size_usd):
        net = size_usd * (1 - CFG["SIM_QUOTE_EXTRA_FEE_PCT"] / 100) - prio_fee_usd()
        return max(0.0, min(net / sol_usd(), CFG["MAX_SOL_PER_TRADE"]))

    def _fail_fee(self, st, urgent=False):
        """Una transazione fallita on-chain paga comunque base fee + priority fee."""
        usd = prio_fee_usd(urgent)
        st.cash -= usd
        st.stats["fee_failed_usd"] = round(st.stats.get("fee_failed_usd", 0.0) + usd, 6)
        st.stats["fill_failed"] += 1
        return usd

    def _latency(self):
        lo, hi = CFG["SIM_LATENCY_MIN_SEC"], max(CFG["SIM_LATENCY_MIN_SEC"], CFG["SIM_LATENCY_MAX_SEC"])
        return SIM["rng"].uniform(lo, hi)

    # ---------------------------------------------------------------- controllo prima dell'acquisto
    async def precheck(self, st, mint, m, size_usd):
        """Preventivo d'acquisto alla size reale + preventivo di rivendita dei token ottenuti.
        Ritorna {"ok": False, "why", "cat"} se va scartato, None se i preventivi non sono disponibili."""
        if not (self.realistic and CFG.get("SIM_USE_QUOTES") and sol_known()):
            return None
        dec = await token_decimals(self.http, mint)
        if dec is None:
            return None
        sol_in = self._sol_in(size_usd)
        lam = int(sol_in * 1e9)
        q, err = await jup_quote(self.http, WSOL, mint, lam)
        if err == "no_route":
            return {"ok": False, "why": "nessun percorso d'acquisto su Jupiter", "cat": "nessun percorso Jupiter"}
        if not q:
            return None
        raw_out = int(q["outAmount"])
        tokens = raw_out / 10 ** dec
        expected = sol_in * sol_usd() / m["price"] if m["price"] > 0 else 0.0
        impact = (1 - tokens / expected) * 100 if expected > 0 else None
        pre = {"ok": True, "src": "jupiter", "dec": dec, "lam": lam, "sol_in": sol_in, "raw_out": raw_out,
               "tokens": tokens, "quote_impact": round(impact, 3) if impact is not None else None,
               "jup_impact_pct": round(fnum(q.get("priceImpactPct")) * 100, 4), "rt_pct": None}
        q2, err2 = await jup_quote(self.http, mint, WSOL, raw_out)
        if err2 == "no_route":
            return {"ok": False, "why": "nessun percorso di vendita su Jupiter (possibile honeypot)",
                    "cat": "non rivendibile"}
        if q2:
            back = int(q2["outAmount"]) / 1e9 * sol_usd() * (1 - CFG["SIM_QUOTE_EXTRA_FEE_PCT"] / 100) - prio_fee_usd()
            rt = (1 - back / size_usd) * 100 if size_usd > 0 else 100.0
            pre["rt_pct"] = round(rt, 3)
            if rt > CFG["SELLCHECK_MAX_RT_PCT"]:
                return {"ok": False, "why": f"rivendita immediata perderebbe il {rt:.1f}% (> "
                                            f"{CFG['SELLCHECK_MAX_RT_PCT']:g}%)", "cat": "rivendita troppo cara"}
        return pre

    # ---------------------------------------------------------------- acquisto
    async def buy(self, st, mint, m, size_usd, pre=None):
        if not pre or pre.get("src") != "jupiter":
            return self._buy_formula(st, m, size_usd, why=None if not self.realistic else "preventivo non disponibile")
        fails, fee_lost, lat_tot = 0, 0.0, 0.0
        for _ in range(1 + max(0, int(CFG["SIM_BUY_RETRIES"]))):
            lat = self._latency()
            lat_tot += lat
            await asyncio.sleep(lat)
            if stop_active():
                return {"ok": False, "error": "arresto di emergenza attivo, ordine non inviato", "fill_failed": fails}
            q, err = await jup_quote(self.http, WSOL, mint, pre["lam"])
            if err == "no_route":
                return {"ok": False, "error": "percorso Jupiter sparito al momento del fill", "fill_failed": fails + 1,
                        "fee_failed_usd": fee_lost}
            if not q:  # rete: il preventivo del segnale resta il migliore dato disponibile
                q = {"outAmount": pre["raw_out"]}
            raw = int(q["outAmount"])
            slip = (1 - raw / pre["raw_out"]) * 100  # >0 = prezzo peggiorato durante la latenza
            if slip * 100 > CFG["SLIPPAGE_BPS"]:
                fails += 1
                fee_lost += self._fail_fee(st)
                log(st, "Executor", "fill_failed", f"acquisto simulato fallito: prezzo salito del {slip:.1f}% in "
                    f"{lat:.1f}s (> slippage {CFG['SLIPPAGE_BPS']} bps)", mint=mint, fill_failed=True,
                    latency_slip=round(slip, 3))
                continue
            if SIM["rng"].random() < CFG["SIM_FAIL_PROB_BUY"]:
                fails += 1
                fee_lost += self._fail_fee(st)
                log(st, "Executor", "fill_failed", "acquisto simulato fallito: transazione non confermata",
                    mint=mint, fill_failed=True)
                continue
            tokens = raw / 10 ** pre["dec"]
            st.cash -= size_usd
            expected = pre["sol_in"] * sol_usd() / m["price"] if m["price"] > 0 else 0.0
            impact = (1 - tokens / expected) if expected > 0 else 0.0
            return {"ok": True, "cost": size_usd, "tokens": tokens, "fill": size_usd / tokens, "impact": impact,
                    "fee": size_usd * CFG["SIM_QUOTE_EXTRA_FEE_PCT"] / 100 + prio_fee_usd(), "dec": pre["dec"],
                    "src": "jupiter", "quote_impact": round(impact * 100, 3), "latency_slip": round(slip, 3),
                    "prio_fee_sol": round(prio_fee_sol() * (1 + fails), 8), "fill_failed": fails,
                    "fee_failed_usd": round(fee_lost, 6), "latency_sec": round(lat_tot, 2),
                    "note": f"preventivo Jupiter, latenza {lat_tot:.1f}s" + (f", {fails} tentativi falliti" if fails else "")}
        return {"ok": False, "error": f"{fails} transazioni simulate fallite", "fill_failed": fails,
                "fee_failed_usd": fee_lost}

    # ---------------------------------------------------------------- vendita
    async def sell(self, st, mint, p, fraction, urgent=False):
        if not (self.realistic and CFG.get("SIM_USE_QUOTES") and sol_known()):
            return self._sell_formula(st, p, fraction, urgent)
        dec = p.get("dec")
        if dec is None:
            dec = await token_decimals(self.http, mint)
        if dec is None:
            return self._sell_formula(st, p, fraction, urgent)
        tokens = p["tokens"] * fraction
        raw = int(tokens * 10 ** dec)
        if raw <= 0:
            return {"ok": True, "value": 0.0, "tokens": tokens, "src": "jupiter"}
        slip_bps = CFG["SLIPPAGE_BPS"] if p.get("sell_fails", 0) < 2 else CFG["PANIC_SLIPPAGE_BPS"]
        q0, err = await jup_quote(self.http, mint, WSOL, raw, slip_bps)
        if err == "no_route":  # come in live: senza percorso non si vende, il Risk riprova
            return {"ok": False, "error": "nessun percorso di vendita su Jupiter", "no_route": True, "fill_failed": 1}
        if not q0:
            return self._sell_formula(st, p, fraction, urgent)
        lat = self._latency()
        await asyncio.sleep(lat)
        q1, err1 = await jup_quote(self.http, mint, WSOL, raw, slip_bps)
        if err1 == "no_route":
            return {"ok": False, "error": "percorso di vendita sparito durante la latenza", "no_route": True,
                    "fill_failed": 1}
        out0 = int(q0["outAmount"])
        out1 = int(q1["outAmount"]) if q1 else out0
        slip = (1 - out1 / out0) * 100
        prio = prio_fee_sol(urgent)
        if slip * 100 > slip_bps:
            fee = self._fail_fee(st, urgent)
            return {"ok": False, "error": f"prezzo sceso del {slip:.1f}% in {lat:.1f}s (> slippage {slip_bps} bps)",
                    "fill_failed": 1, "fee_failed_usd": fee, "prio_fee_sol": prio, "latency_slip": round(slip, 3)}
        pfail = CFG["SIM_FAIL_PROB_SELL_FAST"] if urgent else CFG["SIM_FAIL_PROB_SELL"]
        if SIM["rng"].random() < pfail:
            fee = self._fail_fee(st, urgent)
            return {"ok": False, "error": "transazione simulata non confermata" + (" (rete congestionata)" if urgent else ""),
                    "fill_failed": 1, "fee_failed_usd": fee, "prio_fee_sol": prio}
        gross = out1 / 1e9 * sol_usd()
        value = max(0.0, gross * (1 - CFG["SIM_QUOTE_EXTRA_FEE_PCT"] / 100) - prio * sol_usd())
        disp = tokens * p["last_price"]
        st.cash += value
        return {"ok": True, "value": value, "tokens": tokens, "src": "jupiter",
                "quote_impact": round((1 - gross / disp) * 100, 3) if disp > 0 else None,
                "latency_slip": round(slip, 3), "prio_fee_sol": round(prio, 8), "fill_failed": 0, "latency_sec": round(lat, 2)}

    # ---------------------------------------------------------------- valore di mercato realistico
    async def refresh_mark(self, mint, p):
        if not (self.realistic and CFG.get("SIM_USE_QUOTES") and sol_known()) or p.get("tokens", 0) <= 0 or p.get("last_price", 0) <= 0:
            return
        q = p.get("qmark") or {}
        if time.time() - q.get("ts", 0) < CFG["MARK_QUOTE_SEC"]:
            return
        dec = p.get("dec")
        if dec is None:
            dec = await token_decimals(self.http, mint)
            if dec is None:
                return
            p["dec"] = dec
        qq, err = await jup_quote(self.http, mint, WSOL, int(p["tokens"] * 10 ** dec))
        if err == "no_route":
            # v8.5: un solo 'no_route' (anche un 400 qualsiasi di Jupiter) portava la posizione a -100%, crollo,
            # vendita urgente e creatore in lista nera. Ora vale ~0 solo dopo MARK_NOROUTE_CONFIRM di fila;
            # prima resta l'ultimo valore valido (o la formula, quando scade).
            p["mark_noroute"] = p.get("mark_noroute", 0) + 1
            if p["mark_noroute"] >= int(CFG.get("MARK_NOROUTE_CONFIRM", 3)):
                p["qmark"] = {"ts": time.time(), "ratio": 1e-9, "no_route": True}  # invendibile: valore ~0
            return
        if not qq:
            return
        p["mark_noroute"] = 0
        gross = int(qq["outAmount"]) / 1e9 * sol_usd()
        disp = p["tokens"] * p["last_price"]
        if gross > disp * 1.10:  # v8b: preventivo oltre il 10% sopra il prezzo mostrato = dato sospetto, lo ignoro
            return
        p["qmark"] = {"ts": time.time(), "ratio": clamp(gross / disp, 1e-9, 2.0), "impact_pct": round((1 - gross / disp) * 100, 3)}


RPC_NO_RESPONSE = "nodo RPC senza risposta"


class SolanaBroker:
    """Scambi veri tramite Jupiter. In dry simula senza inviare nulla."""

    def __init__(self, http, mode):
        self.http, self.mode = http, mode
        self.kp = self.pub = None
        self._solp = (0.0, 0.0)
        self.dec_cache = {}
        key = ENV.get("JUP_API_KEY") or os.environ.get("JUP_API_KEY")
        self.jh = {"x-api-key": key} if key else None

    async def setup(self, st):
        try:
            from solders.keypair import Keypair
        except ImportError as e:
            raise RuntimeError("manca la libreria solders: ./venv/bin/pip install solders") from e
        raw = (ENV.get("PRIVATE_KEY") or os.environ.get("PRIVATE_KEY") or "").strip()
        if not raw:
            raise RuntimeError("PRIVATE_KEY mancante nel file .env")
        self.kp = Keypair.from_bytes(bytes(json.loads(raw))) if raw.startswith("[") else Keypair.from_base58_string(raw)
        self.pub = str(self.kp.pubkey())
        bal = await self.sol_balance()
        if bal is None:
            raise RuntimeError("il nodo RPC non risponde: controlla RPC_URL nel file .env")
        solp = await self.sol_price()
        if not solp:
            raise RuntimeError("Jupiter non risponde: controlla JUP_BASE o JUP_API_KEY")
        log(st, "Executor", "info", f"wallet {self.pub[:4]}...{self.pub[-4:]} collegato: {bal / 1e9:.4f} SOL "
                                    f"(SOL a ${solp:,.2f})")
        if self.mode == "live":
            # v8.5: cassa dal saldo appena letto (cash_usd su errore restituisce st.cash = START_BALANCE)
            st.cash = max(0.0, bal / 1e9 - CFG["SOL_RESERVE"]) * solp
            if st.cash <= 0:
                raise RuntimeError(f"wallet senza SOL oltre la riserva ({CFG['SOL_RESERVE']} SOL)")
            if not st.positions and not st.closed and st.total_start == CFG["START_BALANCE"]:
                # v8.5: primo avvio live (stato nuovo): TUTTE le basi dei limiti dal saldo vero, non dai 1000 $
                # di partenza. Prima solo period_start_equity: con 50 $ nel wallet scattava subito HARD_STOP
                # (-95%), con piu di ~1430 $ il -30% arrivava molto oltre.
                st.total_start = st.day_start_equity = st.period_start_equity = st.cash
                log(st, "Risk", "info", f"live: basi dei limiti di perdita dal wallet ${st.cash:,.2f}")
        await self.resolve_pending(st)

    async def rpc(self, method, params):
        d = await self.http.post(CFG["RPC_URL"], {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        if not isinstance(d, dict):
            return None, RPC_NO_RESPONSE
        if d.get("error"):
            err = d["error"]
            return None, str(err.get("message", err) if isinstance(err, dict) else err)[:140]
        return d.get("result"), None

    async def sol_balance(self):
        r, _ = await self.rpc("getBalance", [self.pub, {"commitment": "confirmed"}])
        return int(r["value"]) if isinstance(r, dict) and "value" in r else None

    async def token_raw(self, mint):
        """Saldo grezzo del token nel wallet. v8.5: None = non verificabile (errore RPC, risposta illeggibile),
        MAI 0: prima un 429 dava saldo 0 e la vendita chiudeva la posizione a -100% con i token ancora nel wallet."""
        r, err = await self.rpc("getTokenAccountsByOwner",
                                [self.pub, {"mint": mint}, {"encoding": "jsonParsed", "commitment": "confirmed"}])
        if err or not isinstance(r, dict) or not isinstance(r.get("value"), list):
            return None
        total = 0
        for acc in r["value"]:
            try:
                total += int(acc["account"]["data"]["parsed"]["info"]["tokenAmount"]["amount"])
            except Exception:  # noqa: BLE001
                return None
        return total

    async def token_raw_retry(self, mint, tries=3):
        for i in range(tries):
            v = await self.token_raw(mint)
            if v is not None:
                return v
            await asyncio.sleep(1 + i)
        return None

    async def decimals(self, mint):
        if mint not in self.dec_cache:
            r, _ = await self.rpc("getTokenSupply", [mint])
            try:
                self.dec_cache[mint] = int(r["value"]["decimals"])
            except Exception:  # noqa: BLE001
                return None
        return self.dec_cache[mint]

    async def quote(self, inp, out, amount, slip=None):
        url = (f"{CFG['JUP_BASE']}/quote?inputMint={inp}&outputMint={out}&amount={int(amount)}"
               f"&slippageBps={int(slip or CFG['SLIPPAGE_BPS'])}&restrictIntermediateTokens=true")
        q = await self.http.get(url, headers=self.jh)
        return q if isinstance(q, dict) and q.get("outAmount") else None

    async def sol_price(self):
        if time.time() - self._solp[0] < 60 and self._solp[1]:
            return self._solp[1]
        q = await self.quote(WSOL, USDC, 10 ** 9, 50)
        price = fnum(q.get("outAmount")) / 1e6 if q else 0.0
        if not price:
            pairs = (await dex_batch(self.http, [WSOL])).get(WSOL)
            price = summarize(pairs)["price"] if pairs else 0.0
        if price:
            self._solp = (time.time(), price)
        return price

    async def cash_usd(self, st):
        if self.mode != "live":
            return st.cash
        bal, price = await self.sol_balance(), await self.sol_price()
        if bal is None or not price:
            return st.cash
        return max(0.0, bal / 1e9 - CFG["SOL_RESERVE"]) * price

    async def build_signed(self, q):
        from solders.transaction import VersionedTransaction
        body = {"quoteResponse": q, "userPublicKey": self.pub, "wrapAndUnwrapSol": True,
                "dynamicComputeUnitLimit": True,
                "prioritizationFeeLamports": {"priorityLevelWithMaxLamports": {
                    "maxLamports": int(CFG["PRIORITY_MAX_LAMPORTS"]), "priorityLevel": "high"}}}
        d = await self.http.post(f"{CFG['JUP_BASE']}/swap", body, headers=self.jh)
        if not isinstance(d, dict) or not d.get("swapTransaction"):
            return None, "Jupiter non ha restituito la transazione"
        tx = VersionedTransaction.from_bytes(base64.b64decode(d["swapTransaction"]))
        signed = VersionedTransaction(tx.message, [self.kp])
        self.last_valid = int(fnum(d.get("lastValidBlockHeight"))) or None   # v8.5: scadenza del blockhash
        return base64.b64encode(bytes(signed)).decode(), None

    @staticmethod
    def tx_sig(b64tx):
        from solders.transaction import VersionedTransaction
        return str(VersionedTransaction.from_bytes(base64.b64decode(b64tx)).signatures[0])

    async def tx_status(self, sig, last_valid=None):
        """'ok' confermata, 'failed' errore on-chain o blockhash scaduto senza traccia, 'pending' non ancora
        decidibile, 'unknown' RPC senza risposta."""
        r, err = await self.rpc("getSignatureStatuses", [[sig], {"searchTransactionHistory": True}])
        if err or not isinstance(r, dict) or not isinstance(r.get("value"), list):
            return "unknown"
        s = r["value"][0] if r["value"] else None
        if s:
            if s.get("err"):
                return "failed"
            return "ok" if s.get("confirmationStatus") in ("confirmed", "finalized") else "pending"
        if last_valid:
            h, herr = await self.rpc("getBlockHeight", [{"commitment": "confirmed"}])
            if not herr and isinstance(h, int) and h > last_valid:
                return "failed"   # blockhash scaduto e nessuna traccia: non puo piu arrivare
        return "pending"

    async def send(self, b64tx, entry=None):
        """v8.5: (True, sig) confermata; (False, motivo) sicuramente NON arrivata; (None, motivo) esito SCONOSCIUTO.
        Prima un timeout di 60 s valeva 'fallita' anche se la tx arrivava dopo: posizione fantasma o doppio ordine.
        Ora: firma salvata su disco prima dell'invio, rinvio della stessa tx firmata (idempotente) finche e
        confermata o finche il blockhash scade; se la RPC non risponde l'esito resta 'sconosciuto'."""
        sig = self.tx_sig(b64tx)
        last_valid = getattr(self, "last_valid", None)
        pend = pending_tx_load()
        pend[sig] = dict(entry or {}, sig=sig, last_valid=last_valid, ts=time.time(), status="sending")
        pending_tx_save(pend)

        def done(ok, why):
            d = pending_tx_load()
            if ok is None:
                d.setdefault(sig, {"sig": sig})["status"] = "unknown"
            else:
                d.pop(sig, None)
            pending_tx_save(d)
            return ok, why
        opts = {"encoding": "base64", "skipPreflight": False, "preflightCommitment": "processed", "maxRetries": 0}
        res, err = await self.rpc("sendTransaction", [b64tx, opts])
        if err and err != RPC_NO_RESPONSE:   # errore del nodo (preflight): non e stata inoltrata
            return done(False, err)
        # senza risposta (timeout) la tx puo essere partita lo stesso: si controlla la firma
        t0 = time.time()
        cap = max(30, int(CFG["LIVE_CONFIRM_TIMEOUT_SEC"])) * (2 if last_valid else 1)
        while time.time() - t0 < cap:
            await asyncio.sleep(2)
            stt = await self.tx_status(sig, last_valid)
            if stt == "ok":
                return done(True, sig)
            if stt == "failed":
                return done(False, f"transazione non andata a segno {sig[:10]}")
            await self.rpc("sendTransaction", [b64tx, dict(opts, skipPreflight=True)])   # stessa firma: idempotente
        return done(None, f"esito sconosciuto dopo {cap} s {sig[:10]}: controlla su Solscan")

    async def resolve_pending(self, st, mint=None):
        """Risolve le transazioni in attesa (all'avvio e prima di vendere lo stesso mint). Restano nel file solo
        quelle ancora sconosciute o confermate senza essere registrate dal bot (da riconciliare a mano)."""
        d = pending_tx_load()
        out = {}
        for sig, e in list(d.items()):
            if mint and e.get("mint") != mint:
                continue
            stt = await self.tx_status(sig, e.get("last_valid")) if not sig.startswith("_") else "unknown"
            out[sig] = stt
            if stt == "failed":
                d.pop(sig, None)
            elif stt == "ok":
                e["status"] = "confirmed_untracked"
                log(st, "Executor", "error", f"tx {e.get('side')} {e.get('symbol', e.get('mint', '?'))} {sig[:10]} "
                    f"confermata DOPO il timeout: verifica wallet e posizione, poi cancella live_pending_tx.json")
            else:
                e["status"] = "unknown"
        pending_tx_save(d)
        return out

    async def simulate(self, b64tx):
        r, err = await self.rpc("simulateTransaction", [b64tx, {"encoding": "base64", "sigVerify": False,
                                                                "replaceRecentBlockhash": True}])
        if err:
            return False, err
        e = ((r or {}).get("value") or {}).get("err")
        return e is None, str(e) if e else "ok"

    async def buy(self, st, mint, m, size_usd):
        solp = await self.sol_price()
        dec = await self.decimals(mint)
        if not solp or dec is None:
            return {"ok": False, "error": "prezzo SOL o decimali del token non disponibili"}
        if self.mode == "live":  # v6: limite per i primi trade live (solo riduce il rischio)
            size_usd = min(size_usd, CFG["LIVE_MAX_TRADE_USD"])
        sol_amt = min(size_usd / solp, CFG["MAX_SOL_PER_TRADE"])
        lam = int(sol_amt * 1e9)
        q = await self.quote(WSOL, mint, lam)
        if not q:
            return {"ok": False, "error": "nessun percorso su Jupiter"}
        tokens_q = int(q["outAmount"]) / 10 ** dec
        if tokens_q <= 0:
            return {"ok": False, "error": "preventivo Jupiter senza token in uscita"}
        expected = sol_amt * solp / m["price"] if m["price"] > 0 else 0
        impact = 1 - tokens_q / expected if expected > 0 else 1.0
        if impact * 100 > CFG["MAX_PRICE_IMPACT_PCT"]:
            return {"ok": False, "error": f"impatto sul prezzo {impact * 100:.1f}%"}
        tx, err = await self.build_signed(q)
        if err:
            return {"ok": False, "error": err}
        cost = sol_amt * solp
        if stop_active():  # FIX: STOP ricontrollato subito prima dell'invio
            return {"ok": False, "error": "arresto di emergenza attivo, ordine non inviato"}
        if self.mode == "dry":
            ok, info = await self.simulate(tx)
            if not ok:
                return {"ok": False, "error": f"simulazione fallita: {info}"}
            st.cash -= cost
            return {"ok": True, "cost": cost, "tokens": tokens_q, "fill": cost / tokens_q, "impact": impact,
                    "fee": 0.0, "dec": dec, "note": "simulato, nessun invio"}
        if pending_tx_load():   # v8.5: c'e una transazione dall'esito sconosciuto: niente nuovi acquisti
            return {"ok": False, "error": "transazione precedente da verificare (live_pending_tx.json)"}
        before, sb = await self.token_raw_retry(mint), await self.sol_balance()
        if before is None:   # v8.5: senza il saldo di partenza 'got' conterebbe token gia nel wallet
            return {"ok": False, "error": "saldo token non verificabile prima dell'invio, ordine non inviato"}
        ok, info = await self.send(tx, {"side": "buy", "mint": mint, "symbol": m.get("symbol"),
                                        "sol": round(sol_amt, 6), "tokens_q": tokens_q})
        if ok is None:
            tg_send(f"LIVE: acquisto {m.get('symbol')} con esito SCONOSCIUTO ({info}). Acquisti bloccati finche "
                    f"non verifichi il wallet e cancelli live_pending_tx.json")
            return {"ok": False, "error": info, "uncertain": True}
        if not ok:
            return {"ok": False, "error": info}
        await asyncio.sleep(2)
        after, sa = await self.token_raw_retry(mint), await self.sol_balance()
        if after is None:
            log(st, "Executor", "warn", f"{m.get('symbol')}: saldo dopo l'acquisto non verificabile, uso il preventivo")
        got = (after - before) / 10 ** dec if after is not None and after > before else tokens_q
        spent = (sb - sa) / 1e9 * solp if sb and sa and sb > sa else cost
        if got <= 0:
            got = tokens_q
        return {"ok": True, "cost": spent, "tokens": got, "fill": spent / got, "impact": impact, "fee": 0.0,
                "dec": dec, "sig": info}

    async def sell(self, st, mint, p, fraction, urgent=False):
        solp = await self.sol_price()
        dec = p.get("dec")
        if dec is None:
            dec = await self.decimals(mint)
        if not solp or dec is None:
            return {"ok": False, "error": "prezzo SOL o decimali non disponibili"}
        if self.mode == "dry":
            raw = int(p["tokens"] * fraction * 10 ** dec)
            q = await self.quote(mint, WSOL, raw) if raw > 0 else None
            if not q:
                return {"ok": False, "error": "nessun percorso di vendita"}
            value = int(q["outAmount"]) / 1e9 * solp
            st.cash += value
            return {"ok": True, "value": value, "tokens": p["tokens"] * fraction}
        # v8.5: una vendita precedente dello stesso token con esito sconosciuto va risolta prima di rivendere
        # (altrimenti un TP parziale arrivato dopo il timeout veniva ripetuto)
        prev = [(sg, e) for sg, e in pending_tx_load().items() if e.get("mint") == mint and e.get("side") == "sell"]
        if prev:
            stt = await self.resolve_pending(st, mint)
            for sg, e in prev:
                if stt.get(sg) == "ok":
                    d = pending_tx_load()
                    d.pop(sg, None)
                    pending_tx_save(d)
                    value = fnum(e.get("quote_sol")) * solp   # importo esatto non noto: preventivo
                    log(st, "Risk", "warn", f"vendita {p.get('symbol')} {sg[:10]} confermata dopo il timeout: "
                        f"registrata con il preventivo (${value:.2f})", mint=mint)
                    # resolved_prev: il chiamante NON deve chiudere la posizione se restano token (vendita precedente parziale)
                    return {"ok": True, "value": value, "tokens": fnum(e.get("tokens")), "sig": sg, "resolved_prev": True}
                if stt.get(sg) != "failed":
                    return {"ok": False, "error": f"vendita precedente {sg[:10]} ancora con esito sconosciuto"}
        # FIX: vende solo la quantita registrata dal bot (mai token extra/airdrop nel wallet)
        bal = await self.token_raw_retry(mint)
        if bal is None:   # v8.5: errore RPC != saldo 0 (prima: posizione chiusa a -100% con i token nel wallet)
            return {"ok": False, "error": "saldo token non verificabile (RPC), riprovo"}
        tracked_raw = int(p["tokens"] * min(max(fraction, 0.0), 1.0) * 10 ** dec)
        raw = min(bal, tracked_raw)
        if raw <= 0:
            return {"ok": True, "value": 0.0, "tokens": p["tokens"] * fraction}
        sold_tokens = p["tokens"] if fraction >= 0.999 else raw / 10 ** dec
        slip = CFG["SLIPPAGE_BPS"] if p.get("sell_fails", 0) < 2 else CFG["PANIC_SLIPPAGE_BPS"]
        q = await self.quote(mint, WSOL, raw, slip)
        if not q:
            return {"ok": False, "error": "nessun percorso di vendita"}
        tx, err = await self.build_signed(q)
        if err:
            return {"ok": False, "error": err}
        sb = await self.sol_balance()
        ok, info = await self.send(tx, {"side": "sell", "mint": mint, "symbol": p.get("symbol"),
                                        "tokens": sold_tokens, "quote_sol": int(q["outAmount"]) / 1e9})
        if ok is None:
            tg_send(f"LIVE: vendita {p.get('symbol')} con esito SCONOSCIUTO ({info}). Verifica su Solscan.")
            return {"ok": False, "error": info, "uncertain": True}
        if not ok:
            return {"ok": False, "error": info}
        await asyncio.sleep(2)
        sa = await self.sol_balance()
        value = (sa - sb) / 1e9 * solp if sa and sb and sa > sb else int(q["outAmount"]) / 1e9 * solp
        return {"ok": True, "value": value, "tokens": sold_tokens, "sig": info}


async def executor_agent(st, broker, q_in):
    while True:
        c = await q_in.get()
        try:
            mint, m = c["mint"], c["m"]
            if not (c.get("filter_ok") and c.get("analyst_ok")) or c.get("shadow_only"):
                log(st, "Executor", "block", f"{m['symbol']} bloccato: manca l'ok di Filter o Analyst")
                continue
            if stop_active():
                reject(st, mint, m["symbol"], "arresto di emergenza attivo", agent="Executor", cat="STOP")
                continue
            async with TRADE_LOCK:
                await _execute_buy(st, broker, c, mint, m)
        except Exception as e:  # noqa: BLE001
            log(st, "Executor", "error", f"errore ({type(e).__name__}: {e}), continuo")


def holder_cex_label(addr):
    """v8.4f: etichetta exchange (hot wallet CEX statici di cluster_v2), None se non e un exchange.
    Esclusi hub scoperti in automatico e fee pump.fun (non sono exchange)."""
    cl2 = SRC.get("cluster2") if isinstance(SRC, dict) else None
    if not addr or cl2 is None:
        return None
    lab = (getattr(cl2, "INFRA_FUNDERS", {}) or {}).get(addr)
    if not lab:
        for a, b, l2 in getattr(cl2, "INFRA_PATTERNS", []) or []:
            if addr.startswith(a) and addr.endswith(b):
                lab = l2
                break
    if not lab or lab.lower().startswith(("hub", "pump")):
        return None
    return lab


def ret5_entry(m, candles=None):
    """v8.4f: ritorno a 5 min usato dal gate e registrato in ret_5m (candele 1m se ci sono, altrimenti chg_m5)."""
    try:
        v = vol_from_candles(candles) if candles else None
    except Exception:  # noqa: BLE001
        v = None
    if v and v.get("ret5") is not None:
        return fnum(v["ret5"]), "candele"
    return fnum(m.get("chg_m5")), "chg_m5"


async def confirm2_task(st, http, mint, m, fe, kind):
    """v8.6 (C, idea warp-id CONSECUTIVE_FILTER_MATCHES): dopo ENTRY_CONFIRM_SEC ricontrolla il token (1 chiamata
    DexScreener gratuita): liquidita tenuta, prezzo non crollato, momentum 5m ancora ok, acquisti >= vendite.
    Solo registrato in fe['confirm2'] (finisce in entry_features del CSV). Nessun effetto sugli acquisti."""
    try:
        await asyncio.sleep(fnum(CFG["ENTRY_CONFIRM_SEC"], 45))
        mk = await dex_batch(http, [mint])
        if mint not in mk:
            fe["confirm2"] = {"ok": None, "why": "nessun dato"}
            return
        m2 = summarize(mk[mint])
        why = []
        dl = safe_div(m2["liq"], m["liq"], 0.0) if m.get("liq") else None
        dp = (m2["price"] / m["price"] - 1) * 100 if m.get("price") and m2["price"] > 0 else None
        if dl is not None and dl < CFG["ENTRY_CONFIRM_LIQ_MIN"]:
            why.append(f"liq {dl * 100:.0f}%")
        if dp is None or dp < -CFG["ENTRY_CONFIRM_MAX_DROP_PCT"]:
            why.append("prezzo " + (f"{dp:+.1f}%" if dp is not None else "assente"))
        g5 = m5_gate(m2, None) if m2["price"] > 0 else "nessun prezzo"
        if g5:
            why.append("m5")
        if m2.get("buys_m5", 0) < m2.get("sells_m5", 0):
            why.append(f"acq/vend 5m {m2.get('buys_m5', 0)}/{m2.get('sells_m5', 0)}")
        fe["confirm2"] = {"ok": not why, "why": ",".join(why), "dp": round(dp, 2) if dp is not None else None,
                          "dl": round(dl, 3) if dl is not None else None, "sec": int(CFG["ENTRY_CONFIRM_SEC"])}
        st.stats["confirm2_" + ("ok" if not why else "ko")] = st.stats.get("confirm2_" + ("ok" if not why else "ko"), 0) + 1
        write_event("Analyst", "confirm2", f"conferma 2 ({kind}) {m.get('symbol')}: "
                    f"{'OK' if not why else 'NO (' + ','.join(why) + ')'} - solo misura", {"mint": mint, "pos_kind": kind,
                                                                                         **fe["confirm2"]})
    except Exception as e:  # noqa: BLE001
        try:
            fe["confirm2"] = {"ok": None, "why": f"errore {type(e).__name__}"}
        except Exception:  # noqa: BLE001
            pass


def spawn_confirm2(st, http, mint, m, fe, kind):
    if CFG.get("ENTRY_CONFIRM_MODE") == "off" or not isinstance(fe, dict):
        return
    http = http or getattr(BROKER_REF[0], "http", None)
    if http is None:
        return
    t = asyncio.ensure_future(confirm2_task(st, http, mint, dict(m), fe, kind))
    SHADOW_TASKS.add(t)
    t.add_done_callback(SHADOW_TASKS.discard)


def m5_gate(m, candles=None):
    """v8.4f: None se ok, altrimenti il motivo. Nessuna eccezione da bonus KOL, narrativa o trending."""
    r5, src = ret5_entry(m, candles)
    if r5 < fnum(CFG["MIN_CHANGE_M5_PCT"]):
        return f"prezzo in calo a 5 min ({r5:+.1f}% da {src}, minimo {fnum(CFG['MIN_CHANGE_M5_PCT']):+g}%)"
    return None


def lp_gate(c):
    """v8.4e (Kiwi 07:07): barriera finale, indipendente da punteggio, soft, Learner, override, narrativa/KOL e
    deep review. Ritorna la chiave di blocco ('lp_unlocked_zero' / 'lp_unknown') o None. Nessun CFG modificabile."""
    sec = c.get("sec")
    if not isinstance(sec, dict):
        return "lp_unknown"
    if sec.get("lp_block") in LP_GATE_KEYS:
        return sec["lp_block"]
    stt = sec.get("lp_status")
    if stt == "zero":
        return "lp_unlocked_zero"
    if stt == "unknown":
        return "lp_unknown"
    lpp = sec.get("lp_locked_pct")
    if stt in ("ok", "partial") and lpp is not None and fnum(lpp) < LP_ZERO_MAX_PCT:
        return "lp_unlocked_zero"
    return None


async def _execute_buy(st, broker, c, mint, m):
    if mint in CASE_MINTS:   # v8.5: caso aperto, solo osservazione
        reject(st, mint, m["symbol"], "caso aperto in osservazione (nessun acquisto)", agent="Executor", cat="caso aperto")
        return
    g5 = m5_gate(m, c.get("candles"))   # v8.4f: barriera anche qui, per ogni percorso
    if g5:
        reject(st, mint, m["symbol"], g5, agent="Executor", cat="momento sbagliato")
        return
    g = lp_gate(c)
    if g:
        reject(st, mint, m["symbol"], f"LP {'0% bloccata/bruciata' if g == 'lp_unlocked_zero' else 'sconosciuta'}: "
               f"mai acquisto vero, solo shadow", agent="Executor", cat="LP sbloccata 0%" if g == "lp_unlocked_zero"
               else "LP non verificabile")
        if CFG["SHADOW_TRADES"]:
            spawn_shadow(st, getattr(broker, "http", None), mint, m, [g], "Executor: barriera LP", 0.0,
                         list(c.get("soft") or []), tag=g, signals=[f"src:{m.get('src') or '?'}", "reason:" + g])
        return
    if st.paused:
        reject(st, mint, m["symbol"], "pausa per limite di perdita", agent="Executor", cat="pausa")
        return
    if os.path.exists(HARD_STOP_PATH):  # v6: limite di perdita totale (si toglie cancellando HARD_STOP)
        reject(st, mint, m["symbol"], "HARD_STOP attivo: perdita totale massima", agent="Executor", cat="HARD_STOP")
        return
    if os.path.exists(PENDING_TX_PATH):  # v8.5: tx live dall'esito sconosciuto (si toglie cancellando il file)
        reject(st, mint, m["symbol"], "transazione live da verificare (live_pending_tx.json)", agent="Executor",
               cat="tx da verificare")
        return
    if mint in st.positions:
        return
    if len(st.positions) >= CFG["MAX_POSITIONS"]:
        reject(st, mint, m["symbol"], "troppe posizioni aperte", agent="Executor", cat="troppe posizioni")
        return
    eq = equity(st)
    # (orig.) dimensione scalata per fiducia tra MIN_TRADE_PCT e MAX_TRADE_PCT, 2% della pool
    exposure = sum(mark_value(p) for p in st.positions.values())
    room = eq * CFG["MAX_EXPOSURE_PCT"] / 100 - exposure
    if room < 1:
        reject(st, mint, m["symbol"], f"esposizione massima {CFG['MAX_EXPOSURE_PCT']:.0f}% raggiunta",
               agent="Executor", cat="esposizione massima")
        return
    cash = await broker.cash_usd(st)
    # (v4) size = min(eq * 5%, 1.5% pool, cassa, spazio)
    frac = clamp(CFG["BASE_TRADE_PCT"] * safe_div(c.get("score", 0.0), CFG["SIZE_SCORE_REF"], 1.0),
                 CFG["MIN_TRADE_PCT"], CFG["MAX_TRADE_PCT"])
    size = min(eq * frac, m["liq"] * CFG["MAX_POOL_SHARE_PCT"] / 100, cash, room)
    size *= clamp(fnum(c.get("size_mult"), 1.0), 0.05, 1.0)  # v6: segnali soft -> size ridotta (mai maggiorata)
    # v8.1: feature d'ingresso + stop dinamico ("shadow": solo registrato; "on": stop e size dinamici)
    fe = {}
    if CFG["DYN_STOP_MODE"] != "off" or CFG["PRICE_PATHS"]:
        fe = await entry_features(st, getattr(broker, "http", None), mint, m, c.get("signals", ""),
                                  candles=c.get("candles"), soft=c.get("soft"), narr=c.get("narr_bonus"),
                                  accel=c.get("accel"), size_mult=c.get("size_mult", 1.0),
                                  base_stop=CFG["STOP_LOSS_PCT"], prio=True)
    if fe is not None and (c.get("hp_fee_pct") or c.get("goplus_flags")):
        fe["hp_fee_pct"] = c.get("hp_fee_pct")
        fe["goplus_flags"] = c.get("goplus_flags") or []
    dyn_on = CFG["DYN_STOP_MODE"] == "on" and fe.get("atr_1m_10") is not None
    if dyn_on:
        size *= clamp(fnum(fe.get("dyn_size_mult"), 1.0), 0.05, 1.0)
    if getattr(broker, "realistic", False):  # v6: come in live, mai oltre MAX_SOL_PER_TRADE
        size = min(size, CFG["MAX_SOL_PER_TRADE"] * sol_usd())
    pct = safe_div(size, eq)
    if size < 1:
        reject(st, mint, m["symbol"], "liquidita del conto insufficiente", agent="Executor", cat="conto vuoto")
        return
    if "score_band" in (c.get("soft") or []) and (CFG["MODE"] != "paper" or not (CFG["HONEYPOT_CHECK"]
                                                                                and SRC.get("honeypot"))):
        # v8.5e: la fascia 0.45-0.50 e solo PAPER e solo con il controllo honeypot attivo
        reject(st, mint, m["symbol"], "fascia di punteggio: solo PAPER con controllo honeypot attivo",
               agent="Executor", cat="fascia punteggio (dubbio)")
        return
    # v8.2: honeypot (estensioni Token-2022, authority, preventivo di VENDITA, RugCheck) prima di ogni ingresso
    if CFG["HONEYPOT_CHECK"] and SRC.get("honeypot"):
        hp = await honeypot_check(st, getattr(broker, "http", None), mint, size, c.get("sec") or {})
        log(st, "Executor", "honeypot", f"{m['symbol']} honeypot {hp['level']}: {hp['why'] or 'nessun problema'}",
            mint=mint, **{k: v for k, v in hp.items() if k not in ("why",)})
        if hp["level"] == "hard":
            reject(st, mint, m["symbol"], f"honeypot: {hp['why']}", agent="Executor", cat="honeypot")
            st.labels.setdefault(mint, "suspected_rug")
            hp_shadow(st, getattr(broker, "http", None), mint, m, c, hp, "hard")
            return
        hp_fee = fnum(hp.get("fee_pct"))
        hp_pen = CFG["FEE_PEN_PER_PCT"] * hp_fee + (CFG["GOPLUS_SOFT_PENALTY"] if any(
            "GoPlus" in x and "fee" not in x for x in hp.get("soft") or []) else 0.0)
        c["hp_fee_pct"], c["goplus_flags"] = hp_fee, hp.get("goplus_flags") or []
        if hp_pen > 0 and fnum(c.get("score")) - hp_pen < st.threshold:
            reject(st, mint, m["symbol"], f"punteggio sotto soglia dopo segnali honeypot soft (fee {hp_fee:g}%, "
                   f"-{hp_pen:.2f}): {hp['why']}", agent="Executor", cat="honeypot soft")
            hp_shadow(st, getattr(broker, "http", None), mint, m, c, hp, "soft")
            return
        if hp_fee > 0:
            size *= max(0.3, 1.0 - CFG["FEE_SIZE_PER_PCT"] * hp_fee)
        if hp["level"] == "unknown":   # v8.4c (Kiwi): MAI acquisto (niente size ridotta), shadow + nuovo tentativo
            honeypot_unknown_skip(st, getattr(broker, "http", None), mint, m, c, hp)
            return
        _rcd, _band = bool((c.get("sec") or {}).get("rc_down")), "score_band" in (c.get("soft") or [])
        if _rcd or _band:   # v8.5: RugCheck giu / v8.5e fascia di punteggio: servono GoPlus ok e honeypot pulito
            _tag = "rugcheck_down" if _rcd else "score_band"
            bad = ([f"GoPlus {hp.get('goplus_level')}"] if hp.get("goplus_level") != "ok" else []) + \
                ([f"honeypot {hp.get('level')}"] if hp.get("level") != "ok" else []) + \
                [str(x) for x in (hp.get("soft") or [])][:3]
            if bad:
                reason = ("RugCheck giu: controlli di ripiego" if _rcd else "fascia di punteggio 0.45-0.50: controlli") \
                    + " non tutti ok (" + "; ".join(bad) + ")"
                reject(st, mint, m["symbol"], reason, agent="Executor",
                       cat="RugCheck giu (dubbio)" if _rcd else "fascia punteggio (dubbio)")
                if _rcd:
                    RC_HEALTH["fb_skip"] += 1
                if CFG["SHADOW_TRADES"]:
                    spawn_shadow(st, getattr(broker, "http", None), mint, m, [_tag], reason,
                                 fnum(c.get("score")), (c.get("soft") or []) + [_tag], tag=_tag,
                                 signals=(c.get("signals") or "").split("|"))
                return
            if _rcd:
                RC_HEALTH["fb_ok"] += 1
    elif (c.get("sec") or {}).get("rc_down"):
        # review Claude: con HONEYPOT_CHECK spento (config.json) o il modulo honeypot non caricato il blocco sopra
        # non gira, e con lui il requisito "GoPlus ok + honeypot pulito" del ripiego: si comprava con i soli
        # controlli on-chain. Ora niente acquisto, shadow rugcheck_down.
        reason = "RugCheck giu: controllo honeypot/GoPlus non disponibile (HONEYPOT_CHECK spento o modulo assente)"
        reject(st, mint, m["symbol"], reason, agent="Executor", cat="RugCheck giu (dubbio)")
        RC_HEALTH["fb_skip"] += 1
        if CFG["SHADOW_TRADES"]:
            spawn_shadow(st, getattr(broker, "http", None), mint, m, ["rugcheck_down"], reason,
                         fnum(c.get("score")), (c.get("soft") or []) + ["rugcheck_down"], tag="rugcheck_down",
                         signals=(c.get("signals") or "").split("|"))
        return
    # v8.4: controllo incrociato Birdeye (overview) per prefiltro e honeypot. MAI un filtro duro:
    # "shadow" = solo registrato, "soft" = size ridotta se Birdeye non conferma.
    if str(CFG.get("BIRDEYE_XCHECK_MODE", "shadow")) != "off":
        be = be_client(getattr(broker, "http", None))
        ov = await be.overview(mint, prio=True) if be is not None else None
        if ov is not None:
            xf = SRC["be"].xcheck(ov, m, min_liq=fnum(CFG.get("PF_MIN_LIQ_USD"), 8000.0),
                                  min_buyers=fnum(CFG.get("PF_MIN_BUYS_M5"), 5.0),
                                  min_holders=fnum(CFG.get("BIRDEYE_MIN_HOLDERS"), 100.0),
                                  liq_ratio=fnum(CFG.get("BIRDEYE_LIQ_RATIO"), 0.5))
            fe.update({"be_flags": xf, "be_liq": ov.get("liquidity"), "be_holders": ov.get("holder"),
                       "be_buyers_5m": ov.get("uniqueBuyer5m"), "be_sell_1h": ov.get("sell1h")})
            log(st, "Executor", "birdeye", f"{m['symbol']} Birdeye: liq {fnum(ov.get('liquidity')):,.0f}$ "
                f"holder {ov.get('holder')} acquirenti unici 5m {ov.get('uniqueBuyer5m')} -> "
                + (", ".join(xf) if xf else "coerente") + f" (modo {CFG.get('BIRDEYE_XCHECK_MODE')})",
                mint=mint, be_flags=xf)
            if xf and str(CFG.get("BIRDEYE_XCHECK_MODE")) == "soft":
                size *= clamp(fnum(CFG.get("BIRDEYE_XCHECK_SIZE_MULT"), 0.5), 0.05, 1.0)
                c["soft"] = list(c.get("soft") or []) + ["birdeye_xcheck"]
                if size < 1:
                    reject(st, mint, m["symbol"], "size troppo piccola dopo il controllo Birdeye", agent="Executor",
                           cat="conto vuoto")
                    return
        else:
            fe["be_flags"] = None
    # costi: scarta se 5 x costo stimato andata+ritorno supera il primo gradino di profitto
    rt = roundtrip_cost_pct(size, m["liq"])
    hp_fee_rt = 2.0 * fnum(c.get("hp_fee_pct"))   # v8.4: fee di trasferimento pagata in acquisto e in vendita
    rt += hp_fee_rt
    pre = None
    if getattr(broker, "realistic", False):
        # v6: preventivo reale d'acquisto + rivendita immediata (anti-honeypot, utile anche in live)
        pre = await broker.precheck(st, mint, m, size)
        if pre and not pre.get("ok", True):
            reject(st, mint, m["symbol"], pre["why"], agent="Executor", cat=pre.get("cat", "preventivo"))
            return
        if pre and pre.get("rt_pct") is not None:
            rt = pre["rt_pct"] + hp_fee_rt  # costo a/r dai preventivi (+ fee di trasferimento, v8.4)
    if rt * CFG["COST_GATE_MULT"] > CFG["TP1_PCT"]:
        reject(st, mint, m["symbol"], f"costi troppo alti: andata+ritorno {rt:.1f}% x {CFG['COST_GATE_MULT']:g} > "
               f"TP1 {CFG['TP1_PCT']:g}%", agent="Executor", cat="costi troppo alti")
        return
    if stop_active():  # FIX: STOP ricontrollato subito prima di eseguire l'ordine
        reject(st, mint, m["symbol"], "arresto di emergenza attivo", agent="Executor", cat="STOP")
        return
    res = await broker.buy(st, mint, m, size, pre=pre) if pre is not None else await broker.buy(st, mint, m, size)
    if not res.get("ok"):
        reject(st, mint, m["symbol"], f"acquisto non eseguito: {res.get('error')}", agent="Executor",
               cat="acquisto fallito")
        return
    # FIX: prezzo d'ingresso = costo reale per token (fill con commissioni e slippage)
    entry = safe_div(res["cost"], res["tokens"], res.get("fill") or m["price"])
    st.positions[mint] = {
        "symbol": m["symbol"], "cost": res["cost"], "tokens": res["tokens"], "fill": res["fill"],
        "entry_mark": entry, "quote_price": m["price"], "opened": time.time(), "peak_pct": 0.0, "last_price": m["price"],
        "liq": m["liq"], "liq_entry": m["liq"], "last_ok": time.time(), "thesis": c["thesis"], "dex": m.get("dex"),
        "url": m["url"], "features": c["features"], "score": c["score"],
        "creator": c["sec"].get("creator", ""), "realized": 0.0, "tp_done": False,
        "stop_hits": 0, "sell_fails": 0, "dec": res.get("dec"), "sec_risks": None,
        "tokens_entry": res["tokens"], "tp_stage": 0, "narr_bonus": c.get("narr_bonus", 0.0),
        "narr_themes": c.get("narr_themes", []), "bs5": c.get("bs5"), "accel": c.get("accel"),
        "rt_cost_pct": round(rt, 2), "vwap_gap": c.get("vwap_gap"),
        # v6: dati di esecuzione per ticher
        "quote_src": res.get("src", "formula"), "quote_impact": res.get("quote_impact"),
        "latency_slip": res.get("latency_slip"), "prio_fee_sol": res.get("prio_fee_sol", 0.0) or 0.0,
        "fill_failed": res.get("fill_failed", 0), "fee_failed_usd": res.get("fee_failed_usd", 0.0),
        "sell_quote_impact": None, "sell_latency_slip": None, "latency_sec": res.get("latency_sec"),
        "soft": c.get("soft") or [], "size_mult": c.get("size_mult", 1.0),
        "signals": c.get("signals", ""),  # v7
        "max_hold_min": CFG["LP_SOFT_MAX_HOLD_MIN"] if "lp_unlocked" in (c.get("soft") or []) else None,
        "links": m.get("links") or {},  # v8
        # v8.1
        "entry_features": fe, "stop_dyn": fe.get("dyn_stop_shadow"), "stop_dyn_on": fe.get("dyn_stop_shadow") if dyn_on else None,
        "trough_pct": 0.0, "dyn_hits": 0, "dyn_hit": None,
        # v8.2: per il ricontrollo cluster della posizione
        "cl_sec": {"exclude": (c.get("sec") or {}).get("exclude") or [],
                   "token_program": (c.get("sec") or {}).get("token_program")},
        "cluster2": cluster2_brief(c.get("cluster")),
    }
    st.stats["buys"] += 1
    spawn_confirm2(st, None, mint, m, fe, "real")   # v8.6 (C): solo misura
    path_tick(mint, "real", m["price"], m["liq"], 0.0, ev="entry")  # v8.1
    deep_queue_add(mint, m["symbol"], m.get("links"), "opened", m.get("url", ""), c.get("score"))  # v8
    extra = f", tx {res['sig'][:10]}" if res.get("sig") else (f", {res['note']}" if res.get("note") else "")
    log(st, "Executor", "buy",
        f"COMPRA {m['symbol']} ${res['cost']:.2f} ({pct * 100:.1f}% equity, impatto "
        f"{res['impact'] * 100:.1f}%, costo a/r {'misurato' if pre and pre.get('rt_pct') is not None else 'stimato'} "
        f"{rt:.1f}%{extra})" + (f", segnali soft {'/'.join(c.get('soft'))} size x{c.get('size_mult', 1):.2f}"
                                if c.get("soft") else "") + f". {c['thesis']}", mint=mint, size=round(res["cost"], 2),
        soft=c.get("soft") or [], size_mult=c.get("size_mult", 1.0),
        url=m["url"], quote_src=res.get("src", "formula"), quote_impact=res.get("quote_impact"),
        latency_slip=res.get("latency_slip"), prio_fee_sol=res.get("prio_fee_sol"),
        fill_failed=res.get("fill_failed", 0), rt_cost_pct=round(rt, 2))
    _fe = st.positions[mint].get("entry_features") or {}
    tg_send(f"ACQUISTO {CFG['MODE'].upper()} {m['symbol']}\n{mint}\nPrezzo {entry:.8g} | size ${res['cost']:.2f}\n"
            f"Score {fnum(c.get('score')):.2f}: {str(c.get('thesis') or '')[:200]}\n"
            f"Stop applicato: {fnum(_fe.get('stop_applied'), CFG['STOP_LOSS_PCT']):g}%")
    save_state(st, force=True)


# ======================================================================= override esterni (overrides.json)
# Un consulente esterno puo scrivere overrides.json; il Risk lo rilegge solo quando cambia (mtime/size).
# Convenzioni: percentuali in punti (8.0 = 8%), stop loss NEGATIVO (-8.0), frazioni 0-1, tempi in minuti.
OVERRIDES_PATH = os.path.join(DATA_DIR, "overrides.json")
# chiave nel file -> chiave in CFG
OVR_GLOBAL_MAP = {"STOP_LOSS_PCT": "STOP_LOSS_PCT", "TRAIL_START_PCT": "TRAIL_ACTIVATE_PCT",
                  "TRAIL_DIST_PCT": "TRAIL_DISTANCE_PCT", "TP_PCT": "TAKE_PROFIT_PCT",
                  "TP_SELL_FRACTION": "TAKE_PROFIT_FRACTION", "MAX_HOLD_MIN": "MAX_HOLD_MIN",
                  # nuove chiavi (decisioni finali); TP2_* vince su TP_PCT/TP_SELL_FRACTION se ci sono entrambe
                  "TP1_PCT": "TP1_PCT", "TP1_FRACTION": "TP1_FRACTION", "MIN_CHANGE_M5_PCT": "MIN_CHANGE_M5_PCT",
                  "TP2_PCT": "TAKE_PROFIT_PCT", "TP2_FRACTION": "TAKE_PROFIT_FRACTION",
                  "BREAKEVEN_PCT": "BREAKEVEN_PCT", "TIME_STOP_MIN": "TIME_STOP_MIN",
                  "TIME_STOP_MIN_PCT": "TIME_STOP_MIN_PCT", "TRAIL_TIGHT_AFTER_PCT": "TRAIL_TIGHT_AFTER_PCT",
                  "TRAIL_TIGHT_DIST_PCT": "TRAIL_TIGHT_PCT",
                  # v6: soglie dei segnali soft (le regole HARD non sono modificabili da override)
                  "TOP10_HARD_PCT": "TOP10_HARD_PCT", "EQUAL_HARD_PCT": "EQUAL_HARD_PCT",
                  "BUNDLE_SOFT_PCT": "BUNDLE_SOFT_PCT", "BUNDLE_HARD_PCT": "BUNDLE_HARD_PCT",
                  "LP_HARD_PCT": "LP_HARD_PCT", "SOFT_PENALTY": "SOFT_PENALTY", "SOFT_SIZE_MULT": "SOFT_SIZE_MULT",
                  "SOFT_MAX_FLAGS": "SOFT_MAX_FLAGS", "SHADOW_NEAR_SCORE": "SHADOW_NEAR_SCORE",
                  # v7: cluster, LP soft, bonus dei segnali (tutti con limiti fissi)
                  **{k: k for k in ("BUNDLE_MAX_NETWORKS", "BUNDLE_NET_MIN_WALLETS", "BUNDLE_TOTAL_HARD_PCT",
                                    "BUNDLE_MAX_INSIDER_WALLETS", "CLUSTER_FRESH_HARD", "CLUSTER_SERVICE_SHARE", "CLUSTER_SOFT_NETWORKS",
                                    "CLUSTER_SOFT_PCT", "CLUSTER_FRESH_SOFT", "CLUSTER_PENALTY", "CLUSTER_SIZE_MULT",
                                    "LP_SOFT_MAX_HOLD_MIN", "LP_EXTREME_LOCKED_PCT", "GMGN_TREND_BONUS", "FOMO_TREND_BONUS",
                                    "FOMO_SUB_BONUS", "KOL_BONUS", "KOL_THESIS_BONUS", "MOMENTUM_YOUNG_BONUS",
                                    "TOTAL_BONUS_CAP", "KOL_SOLD_PENALTY", "KOL_MIN_SOL", "MOMENTUM_MIN_SCORE",
                                    # v8: progetto e analisi approfondite
                                    "PROJECT_BONUS", "PROJECT_EMPTY_PENALTY", "PROJECT_MIN_SCORE",
                                    "DEEP_QUEUE_PEAK_PCT", "DEEP_HOLD_MIN_SCORE", "DEEP_HOLD_EXTRA_MIN",
                                    "DEEP_LOW_SCORE", "DEEP_TRAIL_TIGHTEN_PCT",
                                    # v8.1
                                    "DYN_STOP_MODE", "DYN_STOP_ATR_MULT", "DYN_STOP_MIN", "DYN_STOP_MAX",
                                    "DYN_STOP_SIZE_SCALE", "DYN_TP_MODE", "DYN_TP1_ATR_MULT", "DYN_TP1_MIN",
                                    "DYN_TP1_MAX", "DYN_TRAIL_ACT_ATR_MULT", "DYN_TRAIL_ACT_MIN",
                                    "DYN_TRAIL_ACT_MAX", "DYN_TRAIL_DIST_ATR_MULT", "DYN_TRAIL_DIST_MIN",
                                    "DYN_TRAIL_DIST_MAX",
                                    # v8.2
                                    "CLUSTER_GROUP_HARD_PCT", "CLUSTER2_WAIT_SEC", "CLUSTER_UNKNOWN_PENALTY",
                                    "CLUSTER_UNKNOWN_SIZE_MULT", "CLUSTER_HELD_RECHECK_MIN", "HONEYPOT_SELL_IMPACT_PCT",
                                    "CLUSTER_UNKNOWN_ACTION", "CLUSTER_HELD_ACTION", "HONEYPOT_UNKNOWN_ACTION")}}
OVR_POS_MAP = {"stop_loss_pct": "STOP_LOSS_PCT", "take_profit_pct": "TAKE_PROFIT_PCT",
               "tp_sell_fraction": "TAKE_PROFIT_FRACTION", "trail_dist_pct": "TRAIL_DISTANCE_PCT"}
OVR_ACTIONS = ("hold", "sell_all", "sell_half")
# limiti di sicurezza fissi (min, max) sui valori di CFG
# v8.1: STOP_LOSS_PCT fino a -30 (prima -15); con stop piu largo di -15 il crollo scatta a 1.5 x stop
OVR_LIMITS = {"MIN_CHANGE_M5_PCT": (-8.0, 5.0),   # v8.4f
              "STOP_LOSS_PCT": (-30.0, -3.0), "TRAIL_DISTANCE_PCT": (3.0, 25.0), "TRAIL_ACTIVATE_PCT": (3.0, 100.0),
              "TAKE_PROFIT_FRACTION": (0.1, 1.0), "TAKE_PROFIT_PCT": (5.0, 500.0), "MAX_HOLD_MIN": (5.0, 720.0),
              "TP1_PCT": (5.0, 200.0), "TP1_FRACTION": (0.05, 0.6), "BREAKEVEN_PCT": (-5.0, 15.0),
              "TIME_STOP_MIN": (5.0, 240.0), "TIME_STOP_MIN_PCT": (-10.0, 30.0),
              "TRAIL_TIGHT_AFTER_PCT": (20.0, 1000.0), "TRAIL_TIGHT_PCT": (2.0, 15.0),
              # v6: segnali soft
              "TOP10_HARD_PCT": (40.0, 60.0), "EQUAL_HARD_PCT": (5.0, 20.0), "BUNDLE_SOFT_PCT": (1.0, 10.0),
              "BUNDLE_HARD_PCT": (10.0, 40.0), "LP_HARD_PCT": (10.0, 50.0), "SOFT_PENALTY": (0.0, 0.15),
              "SOFT_SIZE_MULT": (0.2, 1.0), "SOFT_MAX_FLAGS": (2.0, 4.0), "SHADOW_NEAR_SCORE": (0.0, 0.10),
              # v7
              "BUNDLE_MAX_NETWORKS": (3.0, 8.0), "BUNDLE_NET_MIN_WALLETS": (2.0, 6.0),
              "BUNDLE_TOTAL_HARD_PCT": (10.0, 40.0), "BUNDLE_MAX_INSIDER_WALLETS": (20.0, 60.0),
              "CLUSTER_FRESH_HARD": (0.7, 1.0), "CLUSTER_SERVICE_SHARE": (0.6, 1.0), "CLUSTER_SOFT_NETWORKS": (1.0, 4.0), "CLUSTER_SOFT_PCT": (2.0, 15.0),
              "CLUSTER_FRESH_SOFT": (0.3, 0.8), "CLUSTER_PENALTY": (0.02, 0.15), "CLUSTER_SIZE_MULT": (0.2, 1.0),
              "LP_SOFT_MAX_HOLD_MIN": (15.0, 180.0), "LP_EXTREME_LOCKED_PCT": (0.0, 10.0),
              # v8
              "DEEP_QUEUE_PEAK_PCT": (20.0, 200.0), "DEEP_HOLD_MIN_SCORE": (50.0, 95.0),
              "DEEP_HOLD_EXTRA_MIN": (0.0, 180.0), "DEEP_LOW_SCORE": (0.0, 50.0), "DEEP_TRAIL_TIGHTEN_PCT": (0.0, 8.0),
              # v8.1: stop dinamico (lato stretto e lato largo; mai oltre -30)
              "DYN_STOP_ATR_MULT": (1.0, 6.0), "DYN_STOP_MIN": (-15.0, -3.0), "DYN_STOP_MAX": (-30.0, -8.0),
              "DYN_STOP_SIZE_SCALE": (0.0, 1.0),
              # v8.1b: TP1 / trailing dinamici (solo shadow)
              "DYN_TP1_ATR_MULT": (1.0, 20.0), "DYN_TP1_MIN": (5.0, 100.0), "DYN_TP1_MAX": (15.0, 200.0),
              "DYN_TRAIL_ACT_ATR_MULT": (1.0, 20.0), "DYN_TRAIL_ACT_MIN": (3.0, 100.0),
              "DYN_TRAIL_ACT_MAX": (15.0, 200.0),
              "DYN_TRAIL_DIST_ATR_MULT": (0.5, 8.0), "DYN_TRAIL_DIST_MIN": (2.0, 15.0), "DYN_TRAIL_DIST_MAX": (5.0, 25.0),
              # v8.2: le soglie rigide si possono solo STRINGERE (massimo = valore di partenza)
              "CLUSTER_GROUP_HARD_PCT": (10.0, 20.0), "HONEYPOT_SELL_IMPACT_PCT": (10.0, 25.0),
              "CLUSTER2_WAIT_SEC": (20.0, 180.0), "CLUSTER_UNKNOWN_PENALTY": (0.02, 0.15),
              "CLUSTER_UNKNOWN_SIZE_MULT": (0.2, 1.0), "CLUSTER_HELD_RECHECK_MIN": (5.0, 120.0),
              **BONUS_LIMITS}
OVR_STR_KEYS = {"DYN_STOP_MODE": ("off", "shadow", "on"), "DYN_TP_MODE": ("off", "shadow"),
                "CLUSTER_UNKNOWN_ACTION": ("skip",), "CLUSTER_HELD_ACTION": ("exit", "alert"),
                "HONEYPOT_UNKNOWN_ACTION": ("skip",)}  # v8.1: valori testuali ammessi
OVR_BOOL_KEYS = {"DYN_STOP_SIZE_SCALE"}
OVR_INT_KEYS = {"MAX_HOLD_MIN", "TIME_STOP_MIN", "SOFT_MAX_FLAGS", "BUNDLE_MAX_NETWORKS", "BUNDLE_NET_MIN_WALLETS",
                "BUNDLE_MAX_INSIDER_WALLETS", "CLUSTER_SOFT_NETWORKS", "LP_SOFT_MAX_HOLD_MIN", "DEEP_HOLD_EXTRA_MIN",
                "DYN_STOP_SIZE_SCALE", "CLUSTER2_WAIT_SEC", "CLUSTER_HELD_RECHECK_MIN"}
SL_WARN_REF = -10.0  # avviso se uno stop da override e piu largo di -10%
# valori originali (codice + config.json): cancellare overrides.json riporta a questi
# v8.3: trending come fonte principale + prefiltro DexScreener (soglie PF_* in sources/dex_prefilter.py)
CFG.setdefault("TREND_SCOUT_SEC", 120)       # un giro dell'aggregatore ogni 2 minuti
CFG.setdefault("TREND_WATCH_TOP", 60)        # quanti trending entrano in watchlist (priorita alta) per giro
CFG.setdefault("WATCH_NEW_MAX", 1200)        # limite per le fonti secondarie (nuove pool, pump.fun)
CFG.setdefault("TREND_MULTI_BONUS", 0.02)    # bonus solo se in trending su 2+ fonti (dentro TOTAL_BONUS_CAP)
CFG.setdefault("PF_ENABLED", True)
CFG.setdefault("PF_LEARN", True)
if "pf" in SRC:
    for _k, _v in SRC["pf"].DEFAULTS.items():
        CFG.setdefault(_k, _v)
    OVR_LIMITS.update(SRC["pf"].LIMITS)
OVR_LIMITS.update({"TREND_WATCH_TOP": (10.0, 150.0), "WATCH_NEW_MAX": (200.0, 2000.0), "TREND_SCOUT_SEC": (60.0, 900.0)})
OVR_INT_KEYS |= {"TREND_WATCH_TOP", "WATCH_NEW_MAX", "TREND_SCOUT_SEC"}
OVR_GLOBAL_MAP.update({k: k for k in list(SRC["pf"].LIMITS if "pf" in SRC else []) + ["TREND_WATCH_TOP", "WATCH_NEW_MAX",
                                                                                      "TREND_SCOUT_SEC", "TREND_MULTI_BONUS"]})
# v8.4: Birdeye (piano gratuito Standard: 30.000 CU/mese, 1 rps)
if "be" in SRC:
    for _k, _v in SRC["be"].DEFAULTS.items():
        CFG.setdefault(_k, _v)
CFG.setdefault("TELEGRAM_ENABLED", True)      # v8.4: notifiche Telegram (token da env TELEGRAM_BOT_TOKEN)
CFG.setdefault("TELEGRAM_RECAP_HOUR", 23)     # ora di Roma del riepilogo giornaliero
CFG.setdefault("HELIUS_MONTHLY_CREDITS", 1_000_000)  # piano Free Helius (helius.dev/docs/billing): 1M crediti/mese
CFG.setdefault("HELIUS_SAVE_AT", 0.80)        # dall'80%: solo chiamate core
CFG.setdefault("HELIUS_STOP_AT", 0.95)        # dal 95%: solo RPC pubblici
CFG.setdefault("BIRDEYE_CYCLE_SAVE_AT", 0.80)
CFG.setdefault("BIRDEYE_CYCLE_STOP_AT", 0.95)
if "helius" in SRC:   # i limiti del ciclo Helius vengono dal CFG
    SRC["helius"].CFG.update(MONTHLY_CREDITS=int(CFG["HELIUS_MONTHLY_CREDITS"]),
                             SAVE_AT=float(CFG["HELIUS_SAVE_AT"]), STOP_AT=float(CFG["HELIUS_STOP_AT"]))
CFG.setdefault("BIRDEYE_ATR", True)            # ATR 1m da Birdeye come fonte primaria (fallback GeckoTerminal / tick)
CFG.setdefault("BIRDEYE_TREND", True)          # trending Birdeye come fonte in piu (famiglia "birdeye")
CFG.setdefault("BIRDEYE_XCHECK_MODE", "shadow")  # off / shadow (solo registra) / soft (size ridotta)
CFG.setdefault("BIRDEYE_XCHECK_SIZE_MULT", 0.5)
CFG.setdefault("BIRDEYE_MIN_HOLDERS", 100)
CFG.setdefault("BIRDEYE_LIQ_RATIO", 0.5)
OVR_LIMITS.update({"BIRDEYE_XCHECK_SIZE_MULT": (0.2, 1.0), "BIRDEYE_MIN_HOLDERS": (10.0, 1000.0),
                   "BIRDEYE_LIQ_RATIO": (0.2, 0.9), "BIRDEYE_TREND_SEC": (3600.0, 86400.0),
                   "BIRDEYE_RESERVE_CU": (500.0, 10000.0), "BIRDEYE_SAVE_AT": (0.5, 0.95)})
OVR_INT_KEYS |= {"BIRDEYE_MIN_HOLDERS", "BIRDEYE_TREND_SEC", "BIRDEYE_RESERVE_CU"}
OVR_LIMITS.update({"MARK_NOROUTE_CONFIRM": (2.0, 6.0)})   # v8.5
OVR_INT_KEYS |= {"MARK_NOROUTE_CONFIRM"}
OVR_GLOBAL_MAP["MARK_NOROUTE_CONFIRM"] = "MARK_NOROUTE_CONFIRM"   # senza questa riga la chiave era ignorata
OVR_LIMITS.update({"CLUSTER_FAM_HARD_N": (3.0, 5.0)})   # review Claude (compito 2)
OVR_INT_KEYS |= {"CLUSTER_FAM_HARD_N"}
OVR_STR_KEYS["CLUSTER_FAM_MODE"] = ("shadow", "on")
OVR_GLOBAL_MAP.update({"CLUSTER_FAM_HARD_N": "CLUSTER_FAM_HARD_N", "CLUSTER_FAM_MODE": "CLUSTER_FAM_MODE"})
OVR_GLOBAL_MAP.update({k: k for k in ("BIRDEYE_XCHECK_SIZE_MULT", "BIRDEYE_MIN_HOLDERS", "BIRDEYE_LIQ_RATIO",
                                      "BIRDEYE_TREND_SEC", "BIRDEYE_RESERVE_CU", "BIRDEYE_SAVE_AT")})
OVR_DEFAULTS = {k: CFG[k] for k in list(OVR_LIMITS) + list(OVR_STR_KEYS)}


def fmtv(v):
    return f"{v:g}" if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v)


def ovr_value(cfg_key, raw, where):
    """Valida e limita un valore. Ritorna (valore o None se da ignorare, lista di note)."""
    notes = []
    if cfg_key in OVR_STR_KEYS:  # v8.1
        if isinstance(raw, str) and raw.strip().lower() in OVR_STR_KEYS[cfg_key]:
            return raw.strip().lower(), notes
        return None, [f"{where}: valore {raw!r} non valido (ammessi {'/'.join(OVR_STR_KEYS[cfg_key])}), ignorato"]
    if cfg_key in OVR_BOOL_KEYS and isinstance(raw, bool):
        raw = int(raw)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(float(raw)):
        return None, [f"{where}: valore {raw!r} non numerico, ignorato"]
    v = float(raw)
    if cfg_key == "STOP_LOSS_PCT" and v > 0:
        notes.append(f"{where}: stop loss {v:g} positivo, interpretato come {-v:g}")
        v = -v
    lo, hi = OVR_LIMITS[cfg_key]
    c = clamp(v, lo, hi)
    if cfg_key in ("DYN_STOP_MIN", "DYN_STOP_MAX") and v > 0:
        c = clamp(-v, lo, hi)
    if c != v:
        notes.append(f"{where}: {v:g} fuori dai limiti [{lo:g}, {hi:g}], limitato a {c:g}")
    if cfg_key == "STOP_LOSS_PCT" and c < SL_WARN_REF:  # (v4: confronto con l'originale -8)
        notes.append(f"{where}: ATTENZIONE stop loss {c:g}% piu largo di {SL_WARN_REF:g}%")
    if cfg_key in OVR_INT_KEYS:
        c = int(round(c))
    return c, notes


def parse_overrides(text, prev_globals, prev_positions, held):
    """Funzione pura (testabile offline). prev_globals: {chiave CFG: valore} in uso ora.
    prev_positions: {mint: {chiave CFG|action|action_id|reason: ...}}. held: mint posseduti.
    Ritorna dict ok, globals, positions, notes [(livello, testo)], updated_at, reason."""
    out = {"ok": False, "globals": dict(prev_globals), "positions": {k: dict(v) for k, v in prev_positions.items()},
           "notes": [], "updated_at": None, "reason": ""}
    notes = out["notes"]
    try:
        d = json.loads(text)
    except (ValueError, TypeError) as e:
        notes.append(("warn", f"overrides.json non valido ({type(e).__name__}: {str(e)[:80]}), tengo gli ultimi valori"))
        return out
    if not isinstance(d, dict):
        notes.append(("warn", "overrides.json non e un oggetto JSON, tengo gli ultimi valori"))
        return out
    out["ok"] = True
    out["updated_at"] = str(d.get("updated_at"))[:40] if d.get("updated_at") is not None else None
    out["reason"] = str(d.get("reason") or "")[:200]
    g = d.get("global")
    if g is not None and not isinstance(g, dict):
        notes.append(("warn", "'global' non e un oggetto, ignorato"))
    elif isinstance(g, dict):
        if not out["reason"] and g.get("reason"):
            out["reason"] = str(g["reason"])[:200]
        for k in g:
            if k not in OVR_GLOBAL_MAP and k != "reason":
                notes.append(("warn", f"global.{k} sconosciuto, ignorato"))
        for fk, ck in OVR_GLOBAL_MAP.items():
            if fk not in g:
                continue  # campo assente: resta l'ultimo valore valido
            if g[fk] is None:
                out["globals"][ck] = OVR_DEFAULTS[ck]
                notes.append(("info", f"global.{fk}=null: torna al default {fmtv(OVR_DEFAULTS[ck])}"))
                continue
            v, n = ovr_value(ck, g[fk], f"global.{fk}")
            notes += [("clamp", x) for x in n]
            if v is not None:
                out["globals"][ck] = v
    pos = d.get("positions")
    if pos is not None and not isinstance(pos, dict):
        notes.append(("warn", "'positions' non e un oggetto, tengo gli ultimi override per posizione"))
        pos = None
    if isinstance(pos, dict):
        newp = {}
        for mint, e in pos.items():
            if mint not in held:
                notes.append(("info", f"override per {str(mint)[:8]}... ignorato: posizione non aperta"))
                continue
            prev = prev_positions.get(mint, {})
            if not isinstance(e, dict):
                notes.append(("warn", f"override per {mint[:8]}... non e un oggetto, tengo il precedente"))
                if prev:
                    newp[mint] = dict(prev)
                continue
            cur = {}
            for fk, ck in OVR_POS_MAP.items():
                if fk not in e or e[fk] is None:
                    continue
                v, n = ovr_value(ck, e[fk], f"{mint[:8]}....{fk}")
                notes += [("clamp", x) for x in n]
                if v is not None:
                    cur[ck] = v
                elif ck in prev:
                    cur[ck] = prev[ck]
            a = e.get("action", "hold")
            if a is None:
                a = "hold"
            if a not in OVR_ACTIONS:
                notes.append(("warn", f"{mint[:8]}....action {a!r} non valida, uso hold"))
                a = "hold"
            cur["action"] = a
            if isinstance(e.get("action_id"), (str, int)) and not isinstance(e.get("action_id"), bool):
                cur["action_id"] = str(e["action_id"])[:60]
            cur["reason"] = str(e.get("reason") or "")[:200]
            newp[mint] = cur
        out["positions"] = newp
    else:
        out["positions"] = {k: v for k, v in out["positions"].items() if k in held}
    return out


class Overrides:
    def __init__(self):
        self.key = None          # (mtime_ns, size) dell'ultima versione letta
        self.loaded = False      # True se i valori in uso vengono dal file
        self.version = None
        self.positions = {}
        self.err = None

    def values(self):
        return {k: CFG[k] for k in list(OVR_LIMITS) + list(OVR_STR_KEYS)}

    def eff(self, mint, cfg_key):
        return self.positions.get(mint, {}).get(cfg_key, CFG[cfg_key])

    def _revert(self, st, why):
        changed = [f"{k} {fmtv(CFG[k])}->{fmtv(OVR_DEFAULTS[k])}" for k in OVR_DEFAULTS if CFG[k] != OVR_DEFAULTS[k]]
        for k, v in OVR_DEFAULTS.items():
            CFG[k] = v
        n = len(self.positions)
        self.positions, self.loaded, self.version = {}, False, None
        log(st, "Risk", "override", f"{why}: ritorno ai valori originali"
            + (f" ({', '.join(changed)})" if changed else "") + (f", {n} override per posizione rimossi" if n else ""),
            source="overrides.json", reverted=True)

    def check(self, st):
        try:
            sres = os.stat(OVERRIDES_PATH)
        except FileNotFoundError:
            if self.key is not None:
                self.key = None
                self._revert(st, "overrides.json rimosso")
            return
        except OSError as e:
            if self.err != str(e):
                self.err = str(e)
                log(st, "Risk", "warn", f"overrides.json non leggibile ({type(e).__name__}), tengo gli ultimi valori")
            return
        key = (sres.st_mtime_ns, sres.st_size)
        if key == self.key:
            return
        self.key = key
        try:
            with open(OVERRIDES_PATH, encoding="utf-8") as f:
                text = f.read(256 * 1024)
        except OSError as e:
            log(st, "Risk", "warn", f"overrides.json non leggibile ({type(e).__name__}), tengo gli ultimi valori")
            return
        self.err = None
        res = parse_overrides(text, self.values(), self.positions, set(st.positions))
        for lvl, msg in res["notes"]:
            log(st, "Risk", "override" if lvl == "clamp" else ("warn" if lvl == "warn" else "override"),
                f"override: {msg}", source="overrides.json", level=lvl)
        if not res["ok"]:
            return
        self.version = res["updated_at"] or f"mtime:{key[0]}"
        why = res["reason"] or "nessun motivo indicato"
        changes = []
        for k, v in res["globals"].items():
            if CFG[k] != v:
                changes.append(f"{k} {fmtv(CFG[k])}->{fmtv(v)}")
                CFG[k] = v
        self.loaded = True
        if changes:
            log(st, "Risk", "override", f"override globali (versione {self.version}): {', '.join(changes)}; "
                f"motivo: {why}", source="overrides.json", version=self.version, values=self.values())
        for mint, e in res["positions"].items():
            if e != self.positions.get(mint):
                sym = st.positions.get(mint, {}).get("symbol", mint[:8])
                vals = ", ".join(f"{k}={v:g}" for k, v in e.items() if k in OVR_LIMITS)
                log(st, "Risk", "override", f"override {sym} (versione {self.version}): {vals or 'nessun valore'}, "
                    f"azione {e['action']}; motivo: {e.get('reason') or 'nessuno'}", mint=mint,
                    source="overrides.json", version=self.version, override=e)
        for mint in set(self.positions) - set(res["positions"]):
            log(st, "Risk", "override", f"override per {mint[:8]}... rimosso (non piu nel file)", mint=mint,
                source="overrides.json", version=self.version)
        pos_changed = res["positions"] != self.positions
        self.positions = res["positions"]
        if not changes and not pos_changed and not res["notes"]:
            write_event("Risk", "override", f"overrides.json versione {self.version}: nessuna modifica",
                        {"source": "overrides.json", "version": self.version})

    def pending_action(self, mint, p):
        """Azione sell_all/sell_half da eseguire UNA volta per versione (o per action_id)."""
        e = self.positions.get(mint)
        if not e or e.get("action") not in ("sell_all", "sell_half"):
            return None
        akey = f"{e['action']}|{e.get('action_id') or self.version}"
        if p.get("ovr_action_done") == akey:
            return None
        p["ovr_action_done"] = akey  # segnato prima di vendere: mai ripetuta in loop
        return e["action"], e.get("reason") or "nessun motivo", akey

    def prune(self, held):
        for mint in [m for m in self.positions if m not in held]:
            del self.positions[mint]


OVR = Overrides()


# ======================================================================= Risk e Learner
def learn_from_trade(st, p, pnl, reason):
    y = 1.0 if pnl > 0 else 0.0
    if p.get("features"):
        st.model.update(p["features"], y)
    st.outcomes.append(y)
    if len(st.outcomes) >= 10:
        wr = sum(st.outcomes) / len(st.outcomes)
        old = st.threshold
        if wr < 0.45:
            st.threshold = min(CFG["THRESHOLD_MAX"], st.threshold + 0.02)
        elif wr > 0.60:
            st.threshold = max(CFG["THRESHOLD_MIN"], st.threshold - 0.01)
        if abs(st.threshold - old) > 1e-9:
            lesson(st, f"win rate {wr * 100:.0f}% sugli ultimi {len(st.outcomes)}: soglia {old:.2f} -> "
                       f"{st.threshold:.2f}")
    if p.get("creator") and any(k in reason for k in ("liquidita crollata", "crollo", "nuovo rischio")):
        st.blacklist.add(p["creator"])
        lesson(st, f"creatore di {p['symbol']} in lista nera ({reason})")
    elif y == 0:
        weak = [k for k in FEATURES if (p.get("features") or {}).get(k, 0) < 0.5]
        lesson(st, f"{p['symbol']} in perdita ({reason}); segnali deboli all'entrata: {', '.join(weak[:4]) or '-'}")


async def sell_position(st, broker, mint, reason, fraction=1.0, zero=False, mark_tp=True):
    async with TRADE_LOCK:
        await _sell_position(st, broker, mint, reason, fraction, zero, mark_tp)


async def _sell_position(st, broker, mint, reason, fraction=1.0, zero=False, mark_tp=True):
    p = st.positions.get(mint)
    if not p:
        return
    urgent = is_urgent(reason)
    res = {"ok": True, "value": 0.0, "tokens": p["tokens"] * fraction} if zero else \
        await broker.sell(st, mint, p, fraction, urgent=urgent)
    if not res.get("ok"):
        p["sell_fails"] = p.get("sell_fails", 0) + 1
        p["fill_failed"] = p.get("fill_failed", 0) + res.get("fill_failed", 1)
        p["fee_failed_usd"] = p.get("fee_failed_usd", 0.0) + res.get("fee_failed_usd", 0.0)
        p["noroute_fails"] = p.get("noroute_fails", 0) + 1 if res.get("no_route") else 0
        log(st, "Risk", "fill_failed", f"vendita {p['symbol']} non riuscita ({res.get('error')}), riprovo", mint=mint,
            fill_failed=True, urgent=urgent, prio_fee_sol=res.get("prio_fee_sol"))
        if broker.mode == "paper" and p["noroute_fails"] >= CFG["SELL_NOROUTE_WRITE_OFF"]:
            log(st, "Risk", "warn", f"{p['symbol']}: {p['noroute_fails']} vendite senza percorso, valore azzerato")
            await _sell_position(st, broker, mint, "invendibile su Jupiter (nessun percorso)", zero=True)
        return
    p["noroute_fails"] = 0
    cpmm_track(p, res["tokens"], fnum(p.get("last_price")), fnum(p.get("liq"), 1.0), zero, p.pop("_cpmm_cap", None))   # realized aggiornato dopo
    _lag, _lagx = exit_lag_fields(p, reason, res["value"], res["tokens"])   # v8.6 (A): solo registrato
    p["prio_fee_sol"] = (p.get("prio_fee_sol") or 0.0) + (res.get("prio_fee_sol") or 0.0)
    p["fill_failed"] = p.get("fill_failed", 0) + res.get("fill_failed", 0)
    p["fee_failed_usd"] = p.get("fee_failed_usd", 0.0) + res.get("fee_failed_usd", 0.0)
    if res.get("quote_impact") is not None:
        p["sell_quote_impact"] = res["quote_impact"]
    if res.get("latency_slip") is not None:
        p["sell_latency_slip"] = res["latency_slip"]
    p["realized"] += res["value"]
    p["tokens"] = max(0.0, p["tokens"] - res["tokens"])
    if (fraction < 0.999 or res.get("resolved_prev")) and p["tokens"] > 0:   # v8.5: vendita precedente risolta != chiusura
        if mark_tp:  # una vendita parziale da override (sell_half) non consuma la presa di profitto
            p["tp_done"] = True
        log(st, "Risk", "sell", f"VENDE {fraction * 100:.0f}% di {p['symbol']} per ${res['value']:.2f}: {reason}",
            mint=mint, quote_src=res.get("src", "formula"), quote_impact=res.get("quote_impact"),
            latency_slip=res.get("latency_slip"), prio_fee_sol=res.get("prio_fee_sol"),
            fill_failed=res.get("fill_failed", 0))
        _pp = (p["realized"] + mark_value(p)) / p["cost"] * 100 - 100 if p.get("cost") else 0.0
        tg_send(f"VENDITA {CFG['MODE'].upper()} parziale {p['symbol']}: {fraction * 100:.0f}% per ${res['value']:.2f} "
                f"(PnL posizione {_pp:+.1f}%)\nMotivo: {reason}")
        save_state(st, force=True)
        return
    st.positions.pop(mint, None)
    pnl = p["realized"] - p["cost"]
    t = {"chiuso": datetime.now(TZ).strftime("%Y-%m-%d %H:%M"), "symbol": p["symbol"], "mint": mint,
         "cost": round(p["cost"], 2), "value": round(p["realized"], 2), "pnl": round(pnl, 2),
         "pnl_pct": round(pnl / p["cost"] * 100, 2) if p["cost"] else 0.0,
         "minutes": int((time.time() - p["opened"]) / 60), "peak_pct": round(p["peak_pct"], 2),
         "score": round(p.get("score", 0), 3), "reason": reason, "thesis": p.get("thesis", ""),
         "url": p.get("url", ""), "narr_bonus": round(p.get("narr_bonus", 0.0), 4),
         "narr_themes": "|".join(p.get("narr_themes") or []), "tp_stage": p.get("tp_stage", 0),
         "bs5": p.get("bs5"), "accel": p.get("accel"), "liq_entry": round(p.get("liq_entry", 0), 0),
         "rt_cost_pct": p.get("rt_cost_pct"), "vwap_gap": p.get("vwap_gap"),
         # v6: esecuzione (impatti e slittamenti in %, fee in SOL sommate su tutti i lati)
         "quote_src": p.get("quote_src", "formula"), "quote_impact": p.get("quote_impact"),
         "sell_quote_impact": p.get("sell_quote_impact"), "latency_slip": p.get("latency_slip"),
         "sell_latency_slip": p.get("sell_latency_slip"), "prio_fee_sol": round(p.get("prio_fee_sol") or 0.0, 8),
         "fill_failed": p.get("fill_failed", 0), "fee_failed_usd": round(p.get("fee_failed_usd", 0.0), 4),
         "soft_flags": "|".join(p.get("soft") or []), "size_mult": p.get("size_mult", 1.0),
         "signals": p.get("signals", "")}
    t.update({f"f_{k}": (p.get("features") or {}).get(k) for k in FEATURES})
    fe = dict(p.get("entry_features") or {})  # v8.1 (posizioni aperte prima della v8.1: vuoto)
    if fe:
        fe.update({"trough_pct": round(fnum(p.get("trough_pct")), 2), "dyn_stop_hit": p.get("dyn_hit")})
        fe.update(alt_exits(mint, "real", p.get("opened")))   # v8.4f: solo registrato
    t.update(fe_columns(fe))
    _bad = pnl_implausible(t["pnl_pct"])   # v8.5h: PnL non credibile -> registrato ma mai al Learner
    t["valido"], t["invalid_for_learning"] = (0, "pnl_implausibile") if _bad else (1, "")
    t["pnl_cpmm"] = round((fnum(p.get("realized_cpmm"), p["realized"]) / p["cost"] - 1) * 100, 2) if p["cost"] else None
    t.update(_lag)   # v8.6 (A): stop_level, exit_fill, exit_lag_s
    if _lagx and fe:
        fe.update(_lagx)
        t["entry_features"] = fe_columns(fe)["entry_features"]
    st.closed.append(t)
    write_trade(t)
    st.cooldown[mint] = time.time() + CFG["COOLDOWN_MIN"] * 60
    note_stock_outcome("trade", p["symbol"], mint, t["pnl_pct"], reason)   # v8.4d: azioni solo annotate
    post_exit_start(st, mint, "real", p["symbol"], p.get("quote_price") or p.get("entry_mark"), p.get("last_price"),
                    reason, (fe or {}).get("stop_applied"), (fe or {}).get("dyn_stop_shadow"))
    log(st, "Risk", "sell", f"CHIUDE {p['symbol']} {t['pnl_pct']:+.1f}% (${pnl:+.2f}): {reason}", mint=mint,
        pnl=t["pnl"], quote_src=res.get("src", "formula"), quote_impact=res.get("quote_impact"),
        latency_slip=res.get("latency_slip"), prio_fee_sol=res.get("prio_fee_sol"), fill_failed=res.get("fill_failed", 0))
    tg_send(f"VENDITA {CFG['MODE'].upper()} {p['symbol']}: chiusa {t['pnl_pct']:+.1f}% (${pnl:+.2f}) dopo "
            f"{t['minutes']} min\nMotivo: {reason}")
    rug = bool(zero or "rug" in reason or "invendibile" in reason or is_rug_event(reason, t["minutes"]))
    if _bad:   # v8.5h: solo la lista nera del creatore (pool svuotata = rug), nessun esempio per il Learner
        if p.get("creator") and "liquidita crollata" in reason:
            st.blacklist.add(p["creator"])
        log(st, "Learner", "warn", f"{p['symbol']}: PnL {t['pnl_pct']:+.0f}% non credibile, escluso dal Learner",
            mint=mint)
        save_state(st, force=True)
        return
    learn_from_trade(st, p, pnl, reason)
    if rug and reason.startswith("liquidita crollata"):
        log(st, "Risk", "rug_event", f"{p['symbol']}: {reason} dopo {t['minutes']} min", mint=mint,
            soft=p.get("soft") or [], tag="lp_unlocked" if "lp_unlocked" in (p.get("soft") or []) else "")
    if p.get("soft"):  # v6: anche i trade veri con segnali soft alimentano le statistiche per segnale
        record_outcome(st, p["soft"], t["pnl_pct"], rug, "real")
    record_signals(st, mint, p.get("signals", ""), t["pnl_pct"], rug, shadow=False)
    label = st.labels.pop(mint, None)
    if label:  # v7: caso etichettato (es. il cluster visto su Bubblemaps): esempio per il Learner + lezione
        record_outcome(st, [label], t["pnl_pct"], rug, "real-label")
        st.lessons.append(f"{datetime.now(TZ):%d/%m %H:%M} caso etichettato '{label}' chiuso {t['pnl_pct']:+.1f}%"
                          f" in {t['minutes']} min ({reason[:60]})")
        log(st, "Learner", "label_outcome", f"caso '{label}' chiuso {t['pnl_pct']:+.1f}%", mint=mint, label=label,
            pnl_pct=t["pnl_pct"], rug=rug)
    save_state(st, force=True)


async def recheck_security(st, http, mint, p):
    data = await http.get(f"https://api.rugcheck.xyz/v1/tokens/{mint}/report")
    if not isinstance(data, dict):
        return None
    if data.get("rugged") or data.get("mintAuthority") or data.get("freezeAuthority"):
        return "nuovo rischio: autorizzazioni o rug"
    dangers = sorted(x.get("name", "?") for x in (data.get("risks") or [])
                     if str(x.get("level", "")).lower() == "danger")
    try:   # v8.4f: supply all'ingresso vs ricontrollo dopo 20 min (solo registrato, nessuna uscita)
        fe = p.get("entry_features") or {}
        tk = data.get("token") or {}
        if fe.get("supply_entry") and "supply_20m" not in fe and time.time() - fnum(p.get("opened"), time.time()) >= 1200 \
                and tk.get("supply"):
            s20 = fnum(tk.get("supply")) / 10 ** int(fnum(tk.get("decimals")))
            fe["supply_20m"] = s20
            fe["supply_up_pct"] = round((s20 / fnum(fe["supply_entry"]) - 1) * 100, 4)
            if fe["supply_up_pct"] > 0.1:
                fe.setdefault("v84f_tags", []).append("supply_up")
    except Exception:  # noqa: BLE001
        pass
    if p.get("sec_risks") is None:
        p["sec_risks"] = dangers
        return None
    new = [d for d in dangers if d not in p["sec_risks"]]
    return f"nuovo rischio: {', '.join(new[:2])}" if new else None


async def risk_agent(st, http, broker):
    last_cash = 0.0
    while True:
        await asyncio.sleep(CFG["RISK_CHECK_SEC"])
        host_pause_check(st, "risk")   # v8.5
        _hold = HOST_PAUSE["hold"].pop("risk", False)
        try:
            stop_now = os.path.exists(STOP_PATH)
            try:
                OVR.check(st)
                OVR.prune(set(st.positions))
            except Exception as e:  # noqa: BLE001
                log(st, "Risk", "warn", f"override non applicati ({type(e).__name__}), tengo gli ultimi valori")
            market = {}
            if st.positions:
                pe_r = [k for k, v in st.post_exit.items() if v.get("kind") == "real" and k not in st.positions]
                stall_reset(st, "risk", st.positions.values())   # v8.4d
                market = await dex_batch(http, list(st.positions.keys()) + pe_r)
                _now = time.time()
                _stale = {}
                for _k, _p in st.positions.items():
                    _mm = summarize(market[_k]) if _k in market else None
                    if not (_mm and _mm["price"] > 0 and not price_spike(_p.get("last_price"), _mm["price"])) \
                            and _now - fnum(_p.get("last_ok"), _now) > 900:
                        _stale[_k] = _p
                for _k, _vd in (await verify_no_price(st, http, _stale, _now)).items():   # v8.4d: seconda fonte
                    if _vd == "invalid":   # posizione: mai vendita a zero senza conferma, si continua ad aspettare
                        _stale[_k]["np_wait"], _stale[_k]["last_ok"] = 0, _now - 900 + 120
                for mint, p in list(st.positions.items()):
                    if mint in market:  # v8.1: price path (stessi dati, nessuna chiamata in piu)
                        try:
                            _m = summarize(market[mint])
                            if _m["price"] > 0:
                                path_tick(mint, "real", _m["price"], _m["liq"],
                                          (_m["price"] / fnum(p.get("quote_price") or p.get("entry_mark"), _m["price"]) - 1) * 100)
                        except Exception:  # noqa: BLE001
                            pass
                    try:
                        if mint in market:
                            m = summarize(market[mint])
                            if m["price"] > 0 and not apply_market_tick(p, m, time.time()) \
                                    and p.get("spike_n") in (1, 20, 200):   # v8.5h (log solo ogni tanto)
                                log(st, "Risk", "warn", f"{p['symbol']}: prezzo x{safe_div(m['price'], p['last_price']):.0f} "
                                    f"in un giro (pool svuotata o sostituita?), non preso", mint=mint,
                                    spike_n=p.get("spike_n"))
                        if getattr(broker, "realistic", False):
                            try:  # v6: valore di uscita dal preventivo Jupiter reale (ogni MARK_QUOTE_SEC)
                                await broker.refresh_mark(mint, p)
                            except Exception:  # noqa: BLE001
                                pass
                        pnl = mark_pnl_pct(p)
                        p["peak_pct"] = max(p["peak_pct"], pnl)
                        sl, tp = OVR.eff(mint, "STOP_LOSS_PCT"), OVR.eff(mint, "TAKE_PROFIT_PCT")
                        # v8.1: stop dinamico solo con DYN_STOP_MODE "on" (e mai sopra un override per posizione)
                        if CFG["DYN_STOP_MODE"] == "on" and p.get("stop_dyn_on") is not None and \
                                "STOP_LOSS_PCT" not in OVR.positions.get(mint, {}):
                            sl = fnum(p["stop_dyn_on"], sl)
                        crash_eff = crash_level(sl)
                        p["trough_pct"] = min(fnum(p.get("trough_pct")), pnl)
                        if p.get("stop_dyn") is not None:  # in shadow: lo stop dinamico sarebbe scattato?
                            p["dyn_hits"] = p.get("dyn_hits", 0) + 1 if pnl <= p["stop_dyn"] else 0
                            if p["dyn_hits"] >= 2 and not p.get("dyn_hit"):
                                p["dyn_hit"] = {"pnl": round(pnl, 2), "min": round((time.time() - p["opened"]) / 60, 1)}
                        tpf, tdist = OVR.eff(mint, "TAKE_PROFIT_FRACTION"), OVR.eff(mint, "TRAIL_DISTANCE_PCT")
                        stage = int(p.get("tp_stage", 1 if p.get("tp_done") else 0))
                        tp1 = min(CFG["TP1_PCT"], tp)  # il primo gradino non supera mai il secondo (anche con override)
                        rung_pct, rung_frac = (tp1, CFG["TP1_FRACTION"]) if stage == 0 else (tp, tpf)
                        tdist_eff = min(tdist, CFG["TRAIL_TIGHT_PCT"]) if p["peak_pct"] >= CFG["TRAIL_TIGHT_AFTER_PCT"] \
                            else tdist
                        dx = deep_effect(mint)  # v8: verdetto di Mark (voto <= 30: trailing piu stretto)
                        if dx["trail_tighten"] > 0:
                            tdist_eff = max(3.0, tdist_eff - dx["trail_tighten"])
                        time_stop_eff = CFG["TIME_STOP_MIN"] + (dx["hold_extra"] if not p.get("max_hold_min") else 0)
                        deep_queue_peak(mint, p["symbol"], p["peak_pct"], p.get("links"), p.get("url", ""), "real")
                        # dopo il TP1 lo stop sale a pareggio +3% netto (mai sopra il TP1, mai sotto lo stop)
                        stop_eff = max(sl, min(CFG["BREAKEVEN_PCT"], tp1 - 1)) if stage >= 1 else sl
                        p["eff"] = {"stop": stop_eff, "stop_base": sl, "tp": tp, "tp1": tp1, "tp_frac": tpf,
                                    "tp1_frac": CFG["TP1_FRACTION"], "tp_stage": stage,
                                    "trail_start": CFG["TRAIL_ACTIVATE_PCT"], "trail_dist": tdist_eff,
                                    "max_hold": pos_max_hold(p, mint), "time_stop": time_stop_eff,
                                    "deep_score": dx["score"], "crash": crash_eff, "stop_dyn": p.get("stop_dyn"),
                                    "dyn_mode": CFG["DYN_STOP_MODE"]}
                        p["stop_hits"] = p["stop_hits"] + 1 if pnl <= stop_eff else 0
                        stop_watch(p, pnl, stop_eff, crash_eff)   # v8.6 (A): solo misura della latenza
                        p["mark_net"], p["pnl_net"] = round(mark_value(p), 4), round(pnl, 3)  # per la dashboard
                        held = (time.time() - p["opened"]) / 60
                        if _hold and not (stop_now or stop_active()):
                            continue   # v8.5: ripresa dopo una pausa dell'host: prima si riquota, si decide al giro dopo
                        act = None if (stop_now or stop_active()) else OVR.pending_action(mint, p)
                        if stop_now or stop_active():
                            await sell_position(st, broker, mint, "arresto di emergenza")
                        elif act:
                            what, why, akey = act
                            if broker.mode != "paper":  # azioni del consulente solo in paper
                                log(st, "Risk", "warn", f"override {what} su {p['symbol']} ignorato: "
                                    f"modalita {broker.mode}, solo paper", mint=mint)
                            else:
                                frac = 1.0 if what == "sell_all" else 0.5
                                log(st, "Risk", "override", f"override {what} su {p['symbol']} ({akey}); motivo: {why}",
                                    mint=mint, source="overrides.json", action=what)
                                await sell_position(st, broker, mint, f"consulente: {what} ({why[:60]})",
                                                    fraction=frac, mark_tp=False)
                        elif time.time() - p["last_ok"] > 900:
                            await sell_position(st, broker, mint, "nessun prezzo da 15 min, possibile rug",
                                                zero=broker.mode == "paper")
                        elif p["liq_entry"] > 0 and p["liq"] < p["liq_entry"] * (1 - CFG["LIQ_DROP_EXIT_PCT"] / 100):
                            drop = (1 - p["liq"] / p["liq_entry"]) * 100
                            await sell_position(st, broker, mint, f"liquidita crollata del {drop:.0f}%",   # v8.5h
                                                zero=broker.mode == "paper" and bool(CFG["LIQ_CRASH_ZERO"]))
                        elif pnl <= crash_eff:
                            await sell_position(st, broker, mint, f"crollo a {pnl:.1f}%")
                        elif p["stop_hits"] >= 2:
                            await sell_position(st, broker, mint, (f"stop a pareggio +{stop_eff:g}% dopo TP1, ora "
                                                                   if stage >= 1 else "stop loss a ") + f"{pnl:.1f}%")
                        # (orig.) un solo take profit: 50% a +100%, poi tp_done
                        elif stage < 2 and pnl >= rung_pct:
                            # gradini in frazioni della quantita INIZIALE: 30% a +30%, 30% a +80% (v4: 25%/+40, 25%/+100)
                            frac = clamp(safe_div(rung_frac * p.get("tokens_entry", p["tokens"]), p["tokens"], 1.0),
                                         0.0, 1.0)
                            p["tp_stage"] = stage + 1
                            p["_cpmm_cap"] = tp_cap_price(p["last_price"], pnl, rung_pct)   # v8.6 (A): solo pnl_cpmm
                            tok_before = p["tokens"]
                            await sell_position(st, broker, mint, f"presa di profitto {stage + 1}/2 a {pnl:+.0f}%",
                                                fraction=frac, mark_tp=False)
                            if mint in st.positions and st.positions[mint]["tokens"] >= tok_before - 1e-12:
                                st.positions[mint]["tp_stage"] = stage  # v6: vendita fallita, il gradino resta da fare
                        elif p["peak_pct"] >= CFG["TRAIL_ACTIVATE_PCT"] and pnl <= p["peak_pct"] - tdist_eff:
                            await sell_position(st, broker, mint,
                                                f"trailing {tdist_eff:g}%: massimo {p['peak_pct']:+.1f}%, ora {pnl:+.1f}%")
                        elif stage == 0 and held > time_stop_eff and pnl < CFG["TIME_STOP_MIN_PCT"]:  # (v4 <=)
                            await sell_position(st, broker, mint, f"stop a tempo: {held:.0f} min e solo {pnl:+.1f}%")
                        elif held > pos_max_hold(p, mint):
                            mh = pos_max_hold(p, mint)
                            await sell_position(st, broker, mint, f"tempo massimo {mh:g} min" +
                                                (" (LP sbloccata)" if mh < CFG["MAX_HOLD_MIN"] else "") +
                                                (f" (deep review {dx['score']:.0f})" if mh > CFG["MAX_HOLD_MIN"] else ""))
                        elif p.get("cluster_exit") and CFG["CLUSTER_HELD_ACTION"] == "exit":
                            await sell_position(st, broker, mint, f"cluster rigido sulla posizione: {p['cluster_exit']}")
                        elif time.time() - st.sec_checked.get(mint, 0) > CFG["RECHECK_SECURITY_MIN"] * 60:
                            st.sec_checked[mint] = time.time()
                            why = await recheck_security(st, http, mint, p)
                            if why:
                                await sell_position(st, broker, mint, why)
                        if mint in st.positions and cluster2_on() and \
                                time.time() - st.cl_checked.get(mint, 0) > CFG["CLUSTER_HELD_RECHECK_MIN"] * 60:
                            st.cl_checked[mint] = time.time()   # v8.2: in background, nessuna attesa qui
                            asyncio.ensure_future(cluster2_held(st, http, mint, p))
                    except Exception as e:  # noqa: BLE001
                        log(st, "Risk", "error", f"errore su {p.get('symbol')} ({type(e).__name__}), continuo")
            # v8.1: trade veri gia chiusi, seguiti per POST_EXIT_MIN: nella stessa chiamata delle posizioni aperte;
            # senza posizioni aperte una chiamata al minuto
            pe = [k for k, v in st.post_exit.items() if v.get("kind") == "real" and k not in st.positions]
            if pe and http is not None:
                try:
                    if st.positions:
                        post_exit_update(st, {k: v for k, v in market.items() if k in pe})
                    elif time.time() - _PATH_PRUNE[1] >= 60:
                        _PATH_PRUNE[1] = time.time()
                        post_exit_update(st, await dex_batch(http, pe))
                except Exception:  # noqa: BLE001
                    pass
            if http is not None:
                try:
                    await refresh_sol_usd(http)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    await refresh_prio_fee(http, st)
                except Exception:  # noqa: BLE001
                    pass
            if broker.mode == "live" and time.time() - last_cash > 60:
                async with TRADE_LOCK:
                    st.cash = await broker.cash_usd(st)
                last_cash = time.time()
            eq = equity(st)
            st.equity_hist.append((time.time(), eq))
            with open(os.path.join(DATA_DIR, "equity.csv"), "a") as f:
                f.write(f"{datetime.now(TZ).isoformat(timespec='seconds')},{eq:.2f}\n")
            # stop giornaliero: base = equity a mezzanotte Europe/Rome (v4: equity dall'ultimo recap delle 8)
            today = datetime.now(TZ).strftime("%Y-%m-%d")
            if today != st.day_key:
                st.day_key, st.day_start_equity = today, eq
                if st.paused:
                    st.paused = False
                    log(st, "Risk", "pause", "nuova giornata (mezzanotte Europe/Rome): acquisti di nuovo attivi")
            # v6: limite di perdita totale -> file HARD_STOP (cancellalo per ripartire da equity attuale)
            if st.hard_tripped and not os.path.exists(HARD_STOP_PATH):
                st.hard_tripped, st.total_start = False, eq
                log(st, "Risk", "pause", f"HARD_STOP rimosso: acquisti riattivati, nuova base ${eq:,.2f}")
            tot_pct = (eq / st.total_start - 1) * 100 if st.total_start else 0.0
            if not st.hard_tripped and tot_pct <= CFG["MAX_TOTAL_LOSS_PCT"]:
                st.hard_tripped = True
                with open(HARD_STOP_PATH, "w") as f:
                    f.write(f"perdita totale {tot_pct:.1f}% <= {CFG['MAX_TOTAL_LOSS_PCT']}% il "
                            f"{datetime.now(TZ):%d/%m %H:%M} (Europe/Rome)\n")
                log(st, "Risk", "pause", f"perdita totale {tot_pct:.1f}%: HARD_STOP, nessun nuovo acquisto")
            day_pct = (eq / st.day_start_equity - 1) * 100 if st.day_start_equity else 0.0
            if not st.paused and day_pct <= CFG["DAILY_LOSS_LIMIT_PCT"]:
                st.paused = True
                log(st, "Risk", "pause", f"perdita giornaliera {day_pct:.1f}%: nuovi acquisti bloccati fino a "
                                         f"mezzanotte (Europe/Rome)")
            save_state(st)
        except Exception as e:  # noqa: BLE001
            log(st, "Risk", "error", f"errore nel ciclo ({type(e).__name__}), continuo")


async def shadow_agent(st, http):
    """Ricontrolla i token scartati: se erano vincenti abbassa la soglia, se erano perdenti la conferma."""
    while True:
        await asyncio.sleep(120)
        try:
            due = [k for k, v in st.shadow.items() if time.time() - v["t"] > CFG["SHADOW_MIN"] * 60]
            if not due:
                continue
            market = await dex_batch(http, due)
            for mint in due:
                v = st.shadow.pop(mint, None)
                if not v or mint not in market:
                    continue
                price = summarize(market[mint])["price"]
                if v["price"] <= 0 or price <= 0:
                    continue
                chg = (price / v["price"] - 1) * 100
                if chg >= CFG["MISSED_WINNER_PCT"]:
                    st.stats["missed"] += 1
                    if v.get("features"):
                        st.model.update(v["features"], 1.0)
                    if v["why"] == "punteggio":
                        old = st.threshold
                        st.threshold = max(CFG["THRESHOLD_MIN"], st.threshold - 0.005)
                        lesson(st, f"{v['sym']} scartato e poi salito {chg:+.0f}%: soglia {old:.3f} -> "
                                   f"{st.threshold:.3f}")
                    else:
                        lesson(st, f"{v['sym']} scartato ({v['why']}) e poi salito {chg:+.0f}%")
                elif chg <= -30:
                    st.stats["dodged"] += 1
                    if v.get("features"):
                        st.model.update(v["features"], 0.0)
            save_state(st)
        except Exception as e:  # noqa: BLE001
            log(st, "Learner", "error", f"errore ({type(e).__name__}), continuo")


# ======================================================================= v6: shadow trade
# Operazioni ipotetiche (nessun denaro, nessun ordine) sui token scartati per un segnale soft o con un punteggio
# appena sotto la soglia. Stesse regole d'uscita del Risk, costi con la formula del paper. Gli esiti vanno in
# shadow_trades.csv e nelle statistiche del Learner (learner_shadow).
SHADOW_CSV = os.path.join(DATA_DIR, "shadow_trades.csv")
SHADOW_FIELDS = ["aperto", "chiuso", "symbol", "mint", "motivo_scarto", "chiavi", "segnali_soft", "score", "soglia",
                 "prezzo_entrata", "liq_entrata", "rt_cost_pct", "quote_src", "pnl_pct", "peak_pct", "minuti", "uscita",
                 "rug", "tag", "signals",
                 # v8.1
                 "atr_1m_10", "stop_applied", "dyn_stop_shadow", "size_mult",
                 "dyn_tp1", "dyn_trail_act", "dyn_trail_dist", "entry_features",
                 # v8.4d: 0 = dati non validi (es. nessun prezzo per un problema nostro), il Learner non li usa
                 "valido",
                 # v8.5: perche non valido (no_price, host_pause, host_pause_retro)
                 "invalid_for_learning",
                 # v8.6 (A): PnL con CPMM drain-aware + latenza d'uscita (solo registrati)
                 "pnl_cpmm", "stop_level", "exit_fill", "exit_lag_s"]


def ensure_shadow_csv():
    """v7: shadow_trades.csv con l'intestazione gia all'avvio (anche prima della prima chiusura).
    v8.1: colonne nuove aggiunte senza perdere le righe vecchie."""
    if os.path.exists(SHADOW_CSV):
        csv_migrate(SHADOW_CSV, SHADOW_FIELDS)
        return
    with open(SHADOW_CSV, "w", newline="") as f:
        csv.writer(f).writerow(SHADOW_FIELDS)


def write_csv_row(path, row):
    fields = list(row.keys())
    csv_migrate(path, fields)
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerow(row)


def shadow_tag(keys, flags):
    ks = list(keys) + list(flags or [])
    for k, tag in (("buysell_relax", "buysell_relax"), ("rugcheck_down", "rugcheck_down"), ("lp_unlocked_zero", "lp_unlocked_zero"), ("lp_unknown", "lp_unknown"),
                   ("creator_serial", "creator_serial"), ("cluster", "cluster"), ("lp_unlocked", "lp_unlocked"), ("analyst_momentum", "analyst_momentum")):
        if any(x.startswith(k) for x in ks):
            return tag
    return "score" if list(keys) == ["score"] else "soft"


def spawn_shadow(st, http, mint, m, keys, reason, score, flags, tag=None, signals=None):
    if mint in st.shadow_trades or mint in st.positions:
        return
    t = asyncio.ensure_future(open_shadow(st, http, mint, m, keys, reason, score, flags,
                                          tag=tag or shadow_tag(keys, flags), signals=signals))
    SHADOW_TASKS.add(t)
    t.add_done_callback(SHADOW_TASKS.discard)


POS_LABELS = {"buono_kiwi", "probabile_buono", "caso_stelle_aperto"}


def neg_lesson_mints():
    """v8.5: mint delle lezioni negative (GLITCH ecc.): un loro esito positivo non va MAI al Learner."""
    return {str(x.get("mint")) for x in LABEL_SEEDS if x.get("label") not in POS_LABELS}


def _close_shadow_row(st, mint, sh, pnl, rug, why, now=None):
    now = now or time.time()
    if pnl_implausible(pnl):   # v8.5h: prezzo esploso a pool svuotata: mai al Learner (resta rug nel CSV)
        sh["np_invalid"], sh["inv_why"] = True, "pnl_implausibile"
        why = "DATI NON VALIDI (PnL implausibile, pool svuotata): " + why
    elif pnl > 0 and mint in neg_lesson_mints():
        sh["np_invalid"], sh["inv_why"] = True, "lezione_negativa"
        why = "NON USATO DAL LEARNER (lezione negativa, mai esempio positivo): " + why
    if HOST_PAUSE["last_resume"] and 0 <= now - HOST_PAUSE["last_resume"] <= CFG["HOST_PAUSE_INVALID_SEC"]:
        sh["np_invalid"], sh["inv_why"] = True, "host_pause"   # v8.5: chiusura a ridosso della ripresa
        why = "DATI NON VALIDI (ripresa dopo pausa dell'host): " + why
    row = {"aperto": datetime.fromtimestamp(sh["t"], TZ).strftime("%Y-%m-%d %H:%M"),
           "chiuso": datetime.fromtimestamp(now, TZ).strftime("%Y-%m-%d %H:%M"), "symbol": sh["sym"], "mint": mint,
           "motivo_scarto": sh["reason"][:160], "chiavi": "|".join(sh["keys"]), "segnali_soft": "|".join(sh["flags"]),
           "score": sh["score"], "soglia": sh["thr"], "prezzo_entrata": sh["price"], "liq_entrata": round(sh["liq_entry"]),
           "rt_cost_pct": sh["rt"], "quote_src": sh.get("src", "formula"), "pnl_pct": round(pnl, 2),
           "peak_pct": round(sh.get("peak_pct", 0.0), 2), "minuti": int((now - sh["t"]) / 60), "uscita": why,
           "rug": int(bool(rug or pnl <= -50)) if (not sh.get("np_invalid") or sh.get("inv_why") == "pnl_implausibile")
           else 0, "tag": sh.get("tag", ""),
           "signals": "|".join(sh.get("signals") or []), "valido": 0 if sh.get("np_invalid") else 1,
           "invalid_for_learning": (sh.get("inv_why") or "no_price") if sh.get("np_invalid") else ""}
    _lag = dict(sh.get("_lag") or {})   # v8.6 (A)
    fe = dict(sh.get("fe") or {})  # v8.1
    if fe and sh.get("_lagx"):
        fe.update(sh["_lagx"])
    if fe:
        fe.update({"trough_pct": round(fnum(sh.get("trough_pct")), 2), "dyn_stop_hit": sh.get("dyn_hit")})
        fe.update(alt_exits(mint, "shadow", sh.get("t")))   # v8.4f: solo registrato
    row.update(fe_columns(fe))
    row["pnl_cpmm"] = (round((fnum(sh.get("realized_cpmm"), sh.get("realized", 0.0)) / sh["cost"] - 1) * 100, 2)
                       if sh.get("cost") and "realized_cpmm" in sh else None)
    row.update({k: _lag.get(k) for k in ("stop_level", "exit_fill", "exit_lag_s")})
    write_csv_row(SHADOW_CSV, row)
    if sh.get("last_price"):
        post_exit_start(st, mint, "shadow", sh["sym"], sh.get("price"), sh.get("last_price"), why,
                        fe.get("stop_applied"), fe.get("dyn_stop_shadow"), now)
    st.stats["shadow_closed"] += 1
    note_stock_outcome("shadow", sh["sym"], mint, pnl, why)   # v8.4d: azioni solo annotate
    if sh.get("np_invalid"):   # v8.4d: dati non validi: il Learner non li usa
        st.stats["shadow_invalid"] = st.stats.get("shadow_invalid", 0) + 1
        write_event("Learner", "shadow_invalid", f"shadow {sh['sym']} chiuso come DATI NON VALIDI ({why})",
                    {"mint": mint, "keys": sh["keys"]})
        return
    record_outcome(st, sh["keys"], pnl, rug, "shadow", now)
    record_signals(st, mint, row["signals"], pnl, rug, shadow=True)
    write_event("Learner", "shadow_close", f"shadow {sh['sym']} chiuso {pnl:+.1f}%: {why}",
                {"mint": mint, "keys": sh["keys"], "pnl_pct": round(pnl, 2), "rug": row["rug"]})


async def open_shadow(st, http, mint, m, keys, reason, score, flags, now=None, tag=None, signals=None):
    """Apre uno shadow trade. Ritorna lo shadow, 'rug' se non c'e percorso di vendita, None se non aperto."""
    if not CFG["SHADOW_TRADES"] or mint in st.shadow_trades or mint in st.positions or m["price"] <= 0:
        return None
    tag = tag or shadow_tag(keys, flags)
    n_an = sum(1 for v in st.shadow_trades.values() if v.get("tag") == "analyst_momentum")
    if tag == "analyst_momentum":  # v7: tetto a parte, cosi non tolgono posto agli shadow dei segnali soft
        if n_an >= CFG["SHADOW_MAX_OPEN_ANALYST"]:
            st.stats["shadow_full"] += 1
            return None
    elif len(st.shadow_trades) - n_an >= CFG["SHADOW_MAX_OPEN"]:
        st.stats["shadow_full"] += 1
        return None
    signals = list(signals or [])
    max_hold = CFG["LP_SOFT_MAX_HOLD_MIN"] if any(str(f).startswith("lp_unlocked") for f in list(flags) + list(keys)) \
        else None
    now = now or time.time()
    size = CFG["SHADOW_SIZE_USD"]
    rt = roundtrip_cost_pct(size, m["liq"])
    tokens, fill, _, _ = buy_tokens(size, m["price"], m["liq"])
    src, dec = "formula", None
    br = BROKER_REF[0]
    if CFG["SHADOW_ROUTE_CHECK"] and br is not None and getattr(br, "realistic", False):
        pre = await br.precheck(st, mint, m, size)  # solo preventivi, nessuna transazione
        if pre and not pre.get("ok", True):
            if pre.get("cat") in ("non rivendibile", "nessun percorso Jupiter"):
                sh = {"t": now, "sym": m["symbol"], "reason": reason, "keys": list(keys), "flags": list(flags),
                      "score": round(score, 4), "thr": round(st.threshold, 4), "price": m["price"],
                      "liq_entry": m["liq"], "rt": None, "src": "jupiter", "peak_pct": 0.0, "tag": tag,
                      "signals": signals}
                _close_shadow_row(st, mint, sh, -100.0, True, pre["why"], now)
                return "rug"
            return None  # rivendita troppo cara: neanche il trade vero sarebbe partito
        if pre and pre.get("rt_pct") is not None:
            rt, src, dec = pre["rt_pct"], "jupiter", pre.get("dec")
            tokens = pre.get("tokens") or tokens
    if rt * CFG["COST_GATE_MULT"] > CFG["TP1_PCT"] or tokens <= 0:
        return None  # stesso filtro costi dell'Executor
    sh = {"t": now, "sym": m["symbol"], "reason": reason, "keys": list(keys), "flags": list(flags),
          "score": round(score, 4), "thr": round(st.threshold, 4), "price": m["price"], "last_price": m["price"],
          "liq": max(m["liq"], 1.0), "liq_entry": m["liq"], "last_ok": now, "cost": size, "tokens": tokens,
          "tokens_entry": tokens, "entry_mark": size / tokens, "realized": 0.0, "tp_stage": 0, "peak_pct": 0.0,
          "stop_hits": 0, "rt": round(rt, 2), "src": src, "dec": dec, "tag": tag, "signals": signals,
          "max_hold": max_hold, "links": m.get("links") or {}, "url": m.get("url", ""), "dex": m.get("dex"),
          "realized_cpmm": 0.0, "drained_cpmm": 0.0}
    try:  # v8.1: feature d'ingresso e stop dinamico anche per gli shadow
        fe = await entry_features(st, http, mint, m, signals, soft=flags, accel=None, base_stop=CFG["STOP_LOSS_PCT"])
    except Exception:  # noqa: BLE001
        fe = {}
    sh.update({"fe": fe, "stop_dyn": fe.get("dyn_stop_shadow"), "trough_pct": 0.0, "dyn_hits": 0, "dyn_hit": None,
               "stop_dyn_on": fe.get("dyn_stop_shadow") if CFG["DYN_STOP_MODE"] == "on" and fe.get("atr_1m_10") is not None
               else None})
    if mint in st.shadow_trades or mint in st.positions:
        return None
    st.shadow_trades[mint] = sh
    spawn_confirm2(st, http, mint, m, sh["fe"], "shadow")   # v8.6 (C): solo misura
    path_tick(mint, "shadow", m["price"], m["liq"], 0.0, now=now, ev="entry")
    st.stats["shadow_open"] += 1
    _dc = discord_tag(mint, now)   # v8.8: call NKT sugli shadow (solo dato)
    if _dc:
        sh.update(_dc)
        sig = "discord:nkt"   # tag informativo, non e un BONUS_KEYS
        if sig not in (sh.get("signals") or []):
            sh.setdefault("signals", []).append(sig)
    write_event("Learner", "shadow_open", f"shadow {m['symbol']} aperto ({'|'.join(keys)}): {reason[:120]}",
                {"mint": mint, "keys": list(keys), "score": round(score, 4), "rt_cost_pct": round(rt, 2), "tag": tag,
                 **_dc})
    return sh


def shadow_decide(sh, now=None):
    """Stesse regole d'uscita del Risk. Ritorna (motivo, frazione, azzera, rug) o None."""
    now = now or time.time()
    pnl = mark_pnl_pct(sh)
    sh["peak_pct"] = max(sh["peak_pct"], pnl)
    sl, tp, tpf, tdist = CFG["STOP_LOSS_PCT"], CFG["TAKE_PROFIT_PCT"], CFG["TAKE_PROFIT_FRACTION"], CFG["TRAIL_DISTANCE_PCT"]
    if CFG["DYN_STOP_MODE"] == "on" and sh.get("stop_dyn_on") is not None:  # v8.1
        sl = fnum(sh["stop_dyn_on"], sl)
    crash_eff = crash_level(sl)
    sh["trough_pct"] = min(fnum(sh.get("trough_pct")), pnl)
    if sh.get("stop_dyn") is not None:
        sh["dyn_hits"] = sh.get("dyn_hits", 0) + 1 if pnl <= sh["stop_dyn"] else 0
        if sh["dyn_hits"] >= 2 and not sh.get("dyn_hit"):
            sh["dyn_hit"] = {"pnl": round(pnl, 2), "min": round((now - sh["t"]) / 60, 1)}
    stage = int(sh.get("tp_stage", 0))
    tp1 = min(CFG["TP1_PCT"], tp)
    rung_pct, rung_frac = (tp1, CFG["TP1_FRACTION"]) if stage == 0 else (tp, tpf)
    tdist_eff = min(tdist, CFG["TRAIL_TIGHT_PCT"]) if sh["peak_pct"] >= CFG["TRAIL_TIGHT_AFTER_PCT"] else tdist
    stop_eff = max(sl, min(CFG["BREAKEVEN_PCT"], tp1 - 1)) if stage >= 1 else sl
    sh["stop_hits"] = sh["stop_hits"] + 1 if pnl <= stop_eff else 0
    stop_watch(sh, pnl, stop_eff, crash_eff, now)   # v8.6 (A): solo misura
    sh["eff"] = {"stop": stop_eff, "crash": crash_eff}
    sh["_rung"], sh["_pnl"] = rung_pct, pnl
    held = (now - sh["t"]) / 60
    if now - sh["last_ok"] > 900:
        if sh.get("np_invalid"):   # v8.4d: nessuna fonte prezzi: dati non validi, al prezzo noto, non rug
            return "DATI NON VALIDI: nessun prezzo da nessuna fonte (non rug)", 1.0, False, False
        return ("nessun prezzo da 15 min, possibile rug" + (" (confermato da Jupiter)" if sh.get("np_src") else ""),
                1.0, True, True)
    if sh["liq_entry"] > 0 and sh["liq"] < sh["liq_entry"] * (1 - CFG["LIQ_DROP_EXIT_PCT"] / 100):
        drop = (1 - sh["liq"] / sh["liq_entry"]) * 100
        why = f"liquidita crollata del {drop:.0f}%"
        if CFG["LIQ_CRASH_ZERO"]:   # v8.5h: parte non venduta a 0, sempre rug
            return why, 1.0, True, True
        return why, 1.0, False, is_rug_event(why, held)
    if pnl <= crash_eff:
        return f"crollo a {pnl:.1f}%", 1.0, False, False
    if sh["stop_hits"] >= 2:
        return ("stop a pareggio dopo TP1, ora " if stage >= 1 else "stop loss a ") + f"{pnl:.1f}%", 1.0, False, False
    if stage < 2 and pnl >= rung_pct:
        frac = clamp(safe_div(rung_frac * sh["tokens_entry"], sh["tokens"], 1.0), 0.0, 1.0)
        sh["tp_stage"] = stage + 1
        return f"presa di profitto {stage + 1}/2 a {pnl:+.0f}%", frac, False, False
    if sh["peak_pct"] >= CFG["TRAIL_ACTIVATE_PCT"] and pnl <= sh["peak_pct"] - tdist_eff:
        return f"trailing {tdist_eff:g}%: massimo {sh['peak_pct']:+.1f}%, ora {pnl:+.1f}%", 1.0, False, False
    if stage == 0 and held > CFG["TIME_STOP_MIN"] and pnl < CFG["TIME_STOP_MIN_PCT"]:
        return f"stop a tempo: {held:.0f} min e solo {pnl:+.1f}%", 1.0, False, False
    mh = min(CFG["MAX_HOLD_MIN"], sh.get("max_hold") or CFG["MAX_HOLD_MIN"])
    if held > mh:
        return f"tempo massimo {mh:g} min", 1.0, False, False
    return None


async def shadow_apply(st, http, mint, sh, dec, now=None):
    """Applica una decisione d'uscita allo shadow. Ritorna il pnl finale se chiuso, None se resta aperto."""
    if not dec:
        return None
    why, frac, zero, rug = dec
    if frac >= 0.999 and not zero and CFG["SHADOW_ROUTE_CHECK"] and http is not None and sh.get("dec") is not None:
        raw = int(sh["tokens"] * 10 ** int(sh["dec"]))
        _, err = await jup_quote(http, mint, WSOL, raw)  # solo preventivo: c'e ancora un percorso di vendita?
        if err == "no_route":
            why, zero, rug = why + "; nessun percorso di vendita su Jupiter", True, True
    sold = sh["tokens"] * frac
    _val = 0.0 if zero else exit_value(sold, sh["last_price"], sh["liq"])
    sh["realized"] += _val
    cpmm_track(sh, sold, sh["last_price"], sh["liq"], zero,   # v8.6 (A): solo pnl_cpmm
               tp_cap_price(sh["last_price"], sh.get("_pnl"), sh.get("_rung")) if why.startswith("presa di profitto") else None,
               _val)
    if frac >= 0.999 or not why.startswith("presa"):
        sh["_lag"], sh["_lagx"] = exit_lag_fields(sh, why, _val, sold, now)
    sh["tokens"] = max(0.0, sh["tokens"] - sold)
    if frac < 0.999 and sh["tokens"] > 0:
        return None
    st.shadow_trades.pop(mint, None)
    pnl = (sh["realized"] / sh["cost"] - 1) * 100 if sh["cost"] else 0.0
    _close_shadow_row(st, mint, sh, pnl, rug, why, now)
    return pnl


async def shadow_trade_agent(st, http):
    while True:
        await asyncio.sleep(CFG["SHADOW_CHECK_SEC"])
        host_pause_check(st, "shadow")   # v8.5
        pe = [k for k, v in st.post_exit.items() if v.get("kind") == "shadow" and k not in st.shadow_trades]
        if not st.shadow_trades and not pe:
            HOST_PAUSE["hold"].pop("shadow", None)
            continue
        _hold = HOST_PAUSE["hold"].pop("shadow", False)
        try:
            # v8.1: gli shadow gia chiusi (seguiti 180 min) viaggiano nella stessa chiamata (30 token per chiamata)
            stall_reset(st, "shadow", st.shadow_trades.values())   # v8.4d
            market = await dex_batch(http, list(st.shadow_trades.keys()) + pe)
            now = time.time()
            post_exit_update(st, {k: v for k, v in market.items() if k in pe}, now)
            for mint, sh in list(st.shadow_trades.items()):
                if mint in market:
                    m = summarize(market[mint])
                    if m["price"] > 0 and apply_market_tick(sh, m, now):   # v8.5h: salto di prezzo non preso
                        sh.pop("np_wait", None), sh.pop("np_src", None)
                        path_tick(mint, "shadow", m["price"], m["liq"], mark_pnl_pct(sh), now=now)
                    elif m["price"] > 0:
                        path_tick(mint, "shadow", m["price"], m["liq"], None, now=now, ev="spike_ignored")
            stale = {k: v for k, v in st.shadow_trades.items() if now - fnum(v.get("last_ok"), now) > 900}
            for mint, vd in (await verify_no_price(st, http, stale, now)).items():   # v8.4d: seconda fonte
                if vd == "invalid":
                    st.shadow_trades[mint]["np_invalid"] = True
            for mint, sh in list(st.shadow_trades.items()):
                if _hold:   # v8.5: dopo una pausa dell'host solo nuova quotazione, nessuna chiusura in questo giro
                    break
                dec = shadow_decide(sh, now)
                deep_queue_peak(mint, sh["sym"], sh.get("peak_pct", 0.0), sh.get("links"), sh.get("url", ""), "shadow")
                await shadow_apply(st, http, mint, sh, dec, now)
            save_state(st)
        except Exception as e:  # noqa: BLE001
            log(st, "Learner", "error", f"shadow trade: errore ({type(e).__name__}), continuo")


# ======================================================================= recap
def build_recap(st):
    eq = equity(st)
    start = st.period_start_equity or CFG["START_BALANCE"]
    pnl = eq - start
    tr = st.closed
    wins = [t for t in tr if t["pnl"] > 0]
    since = datetime.fromtimestamp(st.period_start_ts, TZ).strftime("%d/%m %H:%M")
    s = st.stats
    lines = [
        f"RECAP DESK - modalita {CFG['MODE'].upper()}",
        f"Periodo: dal {since} al {datetime.now(TZ):%d/%m %H:%M}",
        f"Equity: ${start:,.2f} -> ${eq:,.2f} ({pnl:+,.2f}, {safe_div(pnl, start) * 100:+.1f}%)",
        f"Liquidita: ${st.cash:,.2f} | Posizioni aperte: {len(st.positions)}",
        f"Token visti {s.get('seen', 0)} | volume ok {s.get('volume_ok', 0)} | filtro ok {s.get('filter_pass', 0)}"
        f" | acquisti {s.get('buys', 0)}",
        f"Trade chiusi {len(tr)} | vinti {len(wins)} | persi {len(tr) - len(wins)}"
        + (f" | win rate {len(wins) / len(tr) * 100:.0f}%" if tr else ""),
        f"Apprendimento: soglia {st.threshold:.3f}, esempi {st.model.n}, lista nera {len(st.blacklist)}, "
        f"vincenti persi {s.get('missed', 0)}, perdenti evitati {s.get('dodged', 0)}",
    ]
    if tr:
        best = max(tr, key=lambda t: t["pnl_pct"])
        worst = min(tr, key=lambda t: t["pnl_pct"])
        lines += ["", f"Migliore: {best['symbol']} {best['pnl_pct']:+.1f}% ({best['reason']})",
                  f"Peggiore: {worst['symbol']} {worst['pnl_pct']:+.1f}% ({worst['reason']})", "", "Trade chiusi:"]
        lines += [f"- {t['symbol']}: {t['pnl_pct']:+.1f}% (${t['pnl']:+.2f}) in {t['minutes']} min, "
                  f"uscita: {t['reason']}" for t in tr]
    if st.positions:
        lines += ["", "Ancora aperte:"] + [f"- {p['symbol']}: {mark_pnl_pct(p):+.1f}%" for p in st.positions.values()]
    if st.lessons:
        lines += ["", "Lezioni recenti:"] + [f"- {x}" for x in list(st.lessons)[-10:]]
    if st.stats.get("cluster_unknown_skip"):
        lines += ["", f"Cluster non verificabile -> niente acquisto (shadow cluster_unknown): "
                      f"{st.stats['cluster_unknown_skip']} token"]
    if st.reject_reasons:
        lines += ["", "Motivi di scarto piu frequenti:"]
        lines += [f"- {r}: {n}" for r, n in st.reject_reasons.most_common(10)]
    return "\n".join(lines)


async def helius_agent(st):
    """v8.2: ogni 30 minuti scrive nel log quanti crediti Helius abbiamo usato (mai la chiave)."""
    H = SRC.get("helius")
    if not H:
        return
    log(st, "Helius", "info", "chiave Helius presente: RPC principale (pubblici come ripiego)" if H.on()
        else "nessuna chiave Helius: uso gli RPC pubblici")
    while True:
        await asyncio.sleep(60 if H.U.get("calls", 0) == 0 else 1800)
        H.save(force=True)
        if not H.on():
            continue
        if hasattr(H, "fetch_official"):   # v8.4 (solo con env HELIUS_PROJECT_ID; altrimenti stima locale)
            try:
                await asyncio.to_thread(H.fetch_official)
            except Exception:  # noqa: BLE001
                pass
        u = H.summary()
        top = ", ".join(f"{m} {c}" for m, c in u["top"])
        log(st, "Helius", "info", f"crediti oggi {u['credits_today']}/{u['day_budget']} (questa ora {u['credits_this_hour']}, "
            f"media {u['avg_per_hour']}/h, mese {u['month_credits']}), chiamate {u['calls_today']}, 429 {u['r429']}, "
            f"rinviate al pubblico per budget {u['skipped']}, ciclo {u.get('cycle_pct')}% ({u.get('level')}) | {top}", **{k: u[k] for k in ("credits_today", "credits_this_hour", "avg_per_hour", "month_credits", "r429")})


async def recap_agent(st):
    while True:
        now = datetime.now(TZ)
        target = now.replace(hour=int(CFG["RECAP_HOUR"]), minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        path = os.path.join(DATA_DIR, f"recap_{datetime.now(TZ):%Y-%m-%d}.txt")
        with open(path, "w") as f:
            f.write(build_recap(st) + "\n")
        log(st, "Logger", "recap", f"recap salvato in {os.path.basename(path)}")
        st.period_start_ts, st.period_start_equity = time.time(), equity(st)
        # (v4) azzerava anche la pausa; ora la pausa dura fino a mezzanotte Europe/Rome (Risk)
        st.closed, st.stats, st.reject_reasons = [], Counter(), Counter()
        save_state(st, force=True)


# ======================================================================= terminale
SPARK = "▁▂▃▄▅▆▇█"


def sparkline(vals, width=34):
    vals = list(vals)
    if len(vals) > width:
        step = len(vals) / width
        vals = [vals[int(i * step)] for i in range(width)]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        return SPARK[3] * len(vals)
    return "".join(SPARK[int((v - lo) / (hi - lo) * 7)] for v in vals)


def render(st):
    eq = equity(st)
    start = st.period_start_equity or 1
    pnl = eq - start
    color = "green" if pnl >= 0 else "red"
    tr = st.closed
    wr = f"{sum(1 for t in tr if t['pnl'] > 0) / len(tr) * 100:.0f}%" if tr else "-"
    mode = CFG["MODE"].upper()
    head = Text()
    head.append(f" {mode} ", style="bold white on red" if mode == "LIVE" else "bold black on yellow")
    head.append(f"  Equity ${eq:,.2f}  ", style="bold")
    head.append(f"{pnl:+,.2f} ({pnl / start * 100:+.1f}%)\n", style=color)
    head.append(f"Liquidita ${st.cash:,.2f} | Posizioni {len(st.positions)} | Trade {len(tr)} | Win {wr}\n")
    head.append(f"Visti {st.stats.get('seen', 0)} | Osservati {len(st.watch)} | Filtro ok "
                f"{st.stats.get('filter_pass', 0)} | Scartati {st.stats.get('rejected', 0)}\n")
    head.append(f"Soglia {st.threshold:.2f} | Esempi appresi {st.model.n} | Lista nera {len(st.blacklist)}")
    if st.paused:
        head.append("\nPAUSA: limite di perdita raggiunto", style="bold red")
    if os.path.exists(STOP_PATH):
        head.append("\nSTOP attivo: niente acquisti, vendita di tutto", style="bold red")
    tbl = Table(expand=True, show_edge=False, pad_edge=False)
    for col in ("Token", "PnL", "Max", "Min"):
        tbl.add_column(col)
    for p in st.positions.values():
        pp = mark_pnl_pct(p)
        tbl.add_row(p["symbol"][:10], Text(f"{pp:+.1f}%", style="green" if pp >= 0 else "red"),
                    f"{p['peak_pct']:+.1f}%", str(int((time.time() - p["opened"]) / 60)))
    if not st.positions:
        tbl.add_row("nessuna", "", "", "")
    spark = sparkline([e for _, e in st.equity_hist])
    acts = Text("\n".join(st.activity) if st.activity else "in attesa dei primi dati...")
    return Group(Panel(head, title="Desk", border_style="cyan"), Panel(tbl, title="Posizioni"),
                 Panel(Text(spark or "dati in arrivo", style=color), title="Equity"), Panel(acts, title="Agenti"))


async def dashboard(st):
    if HEADLESS:  # FIX: senza TTY stampa una riga di stato al minuto invece del pannello rich
        while True:
            await asyncio.sleep(60)
            s = st.stats
            print(f"{datetime.now(TZ):%H:%M:%S} [{CFG['MODE'].upper()}] equity ${equity(st):,.2f} cassa ${st.cash:,.2f} "
                  f"pos {len(st.positions)} chiusi {len(st.closed)} | visti {s.get('seen', 0)} watch {len(st.watch)} "
                  f"vol ok {s.get('volume_ok', 0)} filtro ok {s.get('filter_pass', 0)} scartati {s.get('rejected', 0)} "
                  f"acquisti {s.get('buys', 0)}", flush=True)
    with Live(render(st), refresh_per_second=1) as live:
        while True:
            live.update(render(st))
            await asyncio.sleep(2)


# ======================================================================= prontezza al live (solo controlli, inerte)
READINESS_PATH = os.path.join(DATA_DIR, "live_readiness.json")


async def readiness_check(st, http, broker):
    """Controlla all'avvio cosa e pronto per il live e scrive live_readiness.json. Non legge chiavi, non firma,
    non invia nulla: verifica solo che i servizi pubblici rispondano e che i limiti siano impostati."""
    checks = []

    def add(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    add("modalita paper", CFG["MODE"] == "paper", f"MODE={CFG['MODE']}")
    add("blocco DESK_PAPER_ONLY", os.environ.get("DESK_PAPER_ONLY") == "1", "DESK_PAPER_ONLY=1 rifiuta dry/live")
    t0 = time.time()
    q, err = await jup_quote(http, WSOL, USDC, 10 ** 8, 50)
    ms = (time.time() - t0) * 1000
    solp = int(q["outAmount"]) / 1e5 if q else 0.0  # 0.1 SOL -> USDC (6 decimali)
    if solp > 1:
        SOL_USD[0], SOL_USD[1] = solp, time.time()
    add("Jupiter quote API", q is not None, f"{CFG['JUP_BASE']} in {ms:.0f} ms, SOL ${solp:,.2f}" if q else f"errore {err}")
    t0 = time.time()
    d = await http.post(CFG["RPC_URL"], {"jsonrpc": "2.0", "id": 1, "method": "getSlot"})
    ms = (time.time() - t0) * 1000
    add("RPC Solana", isinstance(d, dict) and "result" in d, f"slot {d.get('result') if isinstance(d, dict) else '-'} "
                                                           f"in {ms:.0f} ms")
    add("RPC privato/veloce", "api.mainnet-beta.solana.com" not in CFG["RPC_URL"],
        "RPC pubblico: ok per paper, per il live serve un RPC dedicato (Helius/Triton/QuickNode free tier)")
    ok = await refresh_prio_fee(http, st, force=True)
    add("priority fee dinamica", ok, f"normale {prio_fee_sol():.6f} SOL, urgente {prio_fee_sol(True):.6f} SOL"
        if ok else "RPC senza dati: uso la fee fissa")
    try:
        import importlib.util
        has_solders = importlib.util.find_spec("solders") is not None
    except Exception:  # noqa: BLE001
        has_solders = False
    add("libreria solders (firma)", has_solders, "installata" if has_solders else "manca: pip install solders")
    add("file .env presente", os.path.exists(os.path.join(DATA_DIR, ".env")), "contenuto non letto dal controllo")
    add("kill switch STOP", True, "touch STOP vende tutto e blocca gli acquisti")
    add("limite perdita giornaliera", CFG["DAILY_LOSS_LIMIT_PCT"] < 0, f"{CFG['DAILY_LOSS_LIMIT_PCT']}%")
    add("limite perdita totale (HARD_STOP)", CFG["MAX_TOTAL_LOSS_PCT"] < 0, f"{CFG['MAX_TOTAL_LOSS_PCT']}%")
    add("size minuscola per il primo live", CFG["LIVE_MAX_TRADE_USD"] <= 10, f"LIVE_MAX_TRADE_USD=${CFG['LIVE_MAX_TRADE_USD']}")
    add("Jito / invio protetto da MEV", bool(CFG["LIVE_USE_JITO"]), "non implementato")
    add("dry run riuscito", bool(CFG["LIVE_DRY_RUN_DONE"]), "da fare con MODE=dry e wallet dedicato")
    add("simulazione realistica attiva", getattr(broker, "realistic", False), "preventivi Jupiter, latenza, fallimenti")
    n_ok = sum(1 for c in checks if c["ok"])
    rep = {"ts": datetime.now(TZ).isoformat(timespec="seconds"), "ok": n_ok, "total": len(checks), "checks": checks}
    with open(READINESS_PATH, "w") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    missing = [c["check"] for c in checks if not c["ok"] and c["check"] not in ("file .env presente",)]
    log(st, "Logger", "readiness", f"prontezza al live {n_ok}/{len(checks)}; manca: {', '.join(missing) or 'niente'}",
        readiness=rep)
    return rep


# ======================================================================= v8.4: budget mensile e Telegram
TG_STATE = os.path.join(DATA_DIR, "telegram_state.json")   # solo date e soglie gia avvisate (niente token/chat)
BUDGET_ALERTS = (50, 80, 95)


def budget_rows():
    """Consumo del ciclo mensile gratuito di Helius (crediti) e Birdeye (CU)."""
    rows = []
    H = SRC.get("helius")
    if H and H.on():
        u = H.summary()
        rows.append({"name": "Helius", "unit": "crediti", "used": int(u["cycle_credits"]), "limit": int(u["limit"]),
                     "pct": u["cycle_pct"], "today": int(u["credits_today"]), "proj": H.projection(),
                     "pace": int(u["day_budget"]), "days_left": u["days_left"], "level": u["level"],
                     "cycle": f"helius:{u['cycle_start']}", "official": u["official"]})
    be = BE_REF[0]
    if be is not None:
        b = be.status()
        rows.append({"name": "Birdeye", "unit": "CU", "used": int(b["cycle_used"]), "limit": int(b["limit"]),
                     "pct": b["cycle_pct"], "today": int(b["spent_today"]), "proj": int(b["projection"]),
                     "pace": int(fnum(b.get("day_budget"))), "days_left": b["days_left"], "level": b["cycle_level"],
                     "cycle": f"birdeye:{int(fnum(be.st.get('cycle_end')))}", "official": True})
    return rows


def budget_line(r):
    pp = r["proj"] / r["limit"] * 100 if r["limit"] else 0.0
    lv = {"ok": "normale", "risparmio": "RISPARMIO (solo chiamate essenziali)",
          "stop": "STOP (solo fonti gratuite)"}.get(r["level"], r["level"])
    return (f"{r['name']}: {r['pct']:.1f}% del mese ({r['used']}/{r['limit']} {r['unit']}), oggi {r['today']}, "
            f"proiezione fine ciclo {r['proj']} ({pp:.0f}%), ritmo max {r['pace']}/giorno, "
            f"{r['days_left']} gg rimasti, stato {lv}")


def tg_load():
    try:
        with open(TG_STATE) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def tg_save(d):
    try:
        tmp = TG_STATE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f)
        os.replace(tmp, TG_STATE)
    except Exception:  # noqa: BLE001
        pass


def budget_alerts(tgs, rows=None):
    """Avvisa una volta per ciclo quando si superano 50/80/95%. Ritorna i messaggi mandati."""
    out = []
    al = tgs.setdefault("alerts", {})
    for r in (budget_rows() if rows is None else rows):
        done = al.setdefault(r["cycle"], [])
        hit = [t for t in BUDGET_ALERTS if r["pct"] >= t and t not in done]
        if not hit:
            continue
        t = max(hit)
        what = {50: "nessuna limitazione per ora", 80: "ridotte le chiamate non essenziali (trending, arricchimenti)",
                95: "solo fonti gratuite di fallback (controllo cluster via RPC pubblico)"}[t]
        msg = f"AVVISO budget {r['name']}: superato il {t}% del limite mensile gratuito. {what}.\n{budget_line(r)}"
        tg_send(msg)
        out.append(msg)
        done.extend(x for x in BUDGET_ALERTS if x <= t and x not in done)
    if len(al) > 8:   # tieni solo i cicli recenti
        for k in sorted(al)[:-8]:
            al.pop(k, None)
    return out


def tg_recap_text(st):
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    tr = [t for t in st.closed if str(t.get("chiuso", "")).startswith(today)]
    pnl = sum(fnum(t.get("pnl")) for t in tr)
    win = sum(1 for t in tr if fnum(t.get("pnl")) > 0)
    lines = [f"Riepilogo {today} ({CFG['MODE'].upper()})", f"Equity ${equity(st):.2f} (cassa ${st.cash:.2f})",
             f"Trade chiusi oggi: {len(tr)} (in utile {win}), PnL ${pnl:+.2f}"]
    for t in tr[-10:]:
        lines.append(f"- {t.get('symbol')} {fnum(t.get('pnl_pct')):+.1f}% ({str(t.get('reason'))[:50]})")
    lines.append(f"Posizioni aperte: {len(st.positions)}")
    for p in list(st.positions.values())[:10]:
        pp = (p["realized"] + mark_value(p)) / p["cost"] * 100 - 100 if p.get("cost") else 0.0
        lines.append(f"- {p['symbol']} {pp:+.1f}% (${mark_value(p):.2f})")
    rows = budget_rows()
    if rows:
        lines.append("Budget API (piano gratuito):")
        lines.extend(budget_line(r) for r in rows)
    return "\n".join(lines)


# ======================================================================= v8.5g: stato anti-doppioni su file
# Prima viveva solo in memoria: a ogni riavvio tornavano gli stessi avvisi (RugCheck giu, pausa host, gruppi
# hard/err, messaggio di avvio). alert_state.json accanto a state.json: solo orari e chiavi (mai token/chat id).
ALERT_STATE_PATH = os.path.join(DATA_DIR, "alert_state.json")
ALERT_LOCK = threading.Lock()
ALERT_START = {"t": 0.0}
CFG.setdefault("TG_START_DEDUP_SEC", 600)
_RC_KEYS = ("alerted", "alert_t", "down", "down_since", "fb_ok", "fb_skip")


def alert_state_dump():
    n = TG_REF[0]
    groups = {}
    if n is not None and hasattr(n, "group_state"):
        try:
            groups = n.group_state()
        except Exception:  # noqa: BLE001
            groups = {}
    return {"v": 1, "saved": round(time.time(), 1),
            "rugcheck": {k: RC_HEALTH.get(k) for k in _RC_KEYS},
            "host_pause": {"alert_t": HOST_PAUSE["alert_t"]},
            "start": {"t": ALERT_START["t"]},
            "groups": groups}


def alert_state_save():
    """Scrittura atomica (file temporaneo + os.replace). Mai un'eccezione verso il bot."""
    try:
        with ALERT_LOCK:
            d = alert_state_dump()
            tmp = f"{ALERT_STATE_PATH}.tmp{os.getpid()}"
            with open(tmp, "w") as f:
                json.dump(d, f)
            os.replace(tmp, ALERT_STATE_PATH)
        return True
    except Exception:  # noqa: BLE001
        return False


def _ts(x, now):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return 0.0
    return x if 0 < x <= now + 60 else 0.0   # niente orari nel futuro (orologio sbagliato = nessun blocco infinito)


def alert_state_load(now=None):
    """All'avvio: ripristina orari e stati gia avvisati. File assente o rotto = come prima (tutto da zero)."""
    now = now or time.time()
    try:
        with open(ALERT_STATE_PATH) as f:
            d = json.load(f)
        if not isinstance(d, dict):
            return {}
    except Exception:  # noqa: BLE001
        return {}
    rc = d.get("rugcheck") or {}
    if rc.get("alerted") in ("up", "down"):
        RC_HEALTH["alerted"] = rc["alerted"]
    RC_HEALTH["alert_t"] = _ts(rc.get("alert_t"), now)
    for k in ("fb_ok", "fb_skip"):
        try:
            RC_HEALTH[k] = max(0, int(rc.get(k) or 0))
        except (TypeError, ValueError):
            pass
    if rc.get("down") is True:   # RugCheck era giu: ripiego (piu severo) finche non arriva un report riuscito
        RC_HEALTH["down"], RC_HEALTH["fails"] = True, int(CFG["RC_DOWN_FAILS"])
        RC_HEALTH["down_since"] = _ts(rc.get("down_since"), now) or now
    HOST_PAUSE["alert_t"] = _ts((d.get("host_pause") or {}).get("alert_t"), now)
    ALERT_START["t"] = _ts((d.get("start") or {}).get("t"), now)
    return d


def alert_restore_groups(d):
    n = TG_REF[0]
    if n is not None and hasattr(n, "restore_groups"):
        try:
            n.restore_groups((d or {}).get("groups") or {})
        except Exception:  # noqa: BLE001
            pass


def tg_start_message(text, now=None):
    """Messaggio 'Desk avviato': saltato se un altro e stato inviato meno di TG_START_DEDUP_SEC fa.
    Ritorna (inviato, secondi dall'ultimo)."""
    now = now or time.time()
    ago = now - ALERT_START["t"] if ALERT_START["t"] else None
    if ago is not None and ago < CFG["TG_START_DEDUP_SEC"]:
        return False, ago
    ok = tg_send(text)
    if ok:
        ALERT_START["t"] = now
        alert_state_save()
    return ok, ago


async def telegram_agent(st):
    """v8.4: riepilogo giornaliero alle TELEGRAM_RECAP_HOUR (ora di Roma) e avvisi di budget 50/80/95%."""
    tgs = tg_load()
    while True:
        await asyncio.sleep(30)
        try:
            budget_alerts(tgs)
            now = datetime.now(TZ)
            day = now.strftime("%Y-%m-%d")
            if now.hour >= int(CFG.get("TELEGRAM_RECAP_HOUR", 23)) and tgs.get("recap_day") != day:
                tg_send(tg_recap_text(st))
                tgs["recap_day"] = day
                log(st, "Telegram", "info", "riepilogo giornaliero inviato")
            tg_save(tgs)
            alert_state_save()   # v8.5g: anche gli orari dei gruppi (hard, err)
        except Exception as e:  # noqa: BLE001
            log(st, "Telegram", "warn", f"riepilogo/avvisi: {type(e).__name__}")


# ======================================================================= avvio
async def supervise(st, name, factory):
    while True:
        try:
            await factory()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log(st, "Logger", "error", f"{name}: {type(e).__name__} {e}. Riavvio tra 10s")
            _m = SRC["tg"].mask(f"{name}: {type(e).__name__} {e}") if "tg" in SRC else name
            if "helius" in SRC:
                _m = SRC["helius"].mask(_m)
            tg_group("err", f"{_m[:200]}", 60, "ERRORE GRAVE (agente riavviato):")
            await asyncio.sleep(10)


async def main():
    st = load_state()
    ST_REF[0] = st
    apply_label_seeds(st)   # v8.2
    apply_case_watch(st)
    try:
        apply_host_pause_retro(st)   # v8.5
    except Exception as e:  # noqa: BLE001
        print(f"retro host_pause non applicato: {type(e).__name__}")    # v8.5
    try:
        apply_liq_crash_retro(st)   # v8.5h
    except Exception as e:  # noqa: BLE001
        print(f"retro liquidita crollata non applicato: {type(e).__name__}")
    mode = CFG["MODE"]
    if mode not in ("paper", "dry", "live"):
        print(f"MODE '{mode}' non valido: usa paper, dry o live")
        return
    if os.environ.get("DESK_PAPER_ONLY") == "1" and mode != "paper":
        print(f"DESK_PAPER_ONLY=1: rifiuto di avviare in modalita {mode}")
        return
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": "Mozilla/5.0 desk-bot"}) as s:
        http = Http(s)
        broker = PaperBroker(http) if mode == "paper" else SolanaBroker(http, mode)
        BROKER_REF[0] = broker  # v6: gli shadow trade usano solo i preventivi (precheck), mai buy/sell
        try:
            await broker.setup(st)
        except Exception as e:  # noqa: BLE001
            print(f"Impossibile avviare in modalita {mode}: {e}")
            return
        log(st, "Logger", "info", f"avvio in modalita {mode.upper()}"
            + (" - SOLDI VERI" if mode == "live" else ", nessun ordine reale"))
        save_state(st, force=True)
        _ast = alert_state_load()   # v8.5g: orari degli avvisi gia mandati (anti-doppioni dopo un riavvio)
        if CFG.get("TELEGRAM_ENABLED") and "tg" in SRC:   # v8.4
            try:
                TG_REF[0] = SRC["tg"].Notifier(enabled=True, logf=lambda m: write_event("Telegram", "warn", m, {}),
                                               okf=lambda m: log(st, "Telegram", "info", str(m).replace("Telegram: ", "", 1)),
                                               onflush=alert_state_save)
            except Exception as e:  # noqa: BLE001
                TG_REF[0] = None
                print(f"telegram non avviato ({type(e).__name__})")
        alert_restore_groups(_ast)
        _tok = TG_REF[0] is not None and TG_REF[0].status().get("enabled")
        log(st, "Telegram", "info", "notifiche Telegram attive" if _tok else "notifiche Telegram spente "
            "(TELEGRAM_ENABLED falso, token o chat id mancanti)")
        log(st, "Telegram", "info", f"stato avvisi caricato da alert_state.json: RugCheck avvisato "
            f"'{RC_HEALTH['alerted']}'{' (ancora giu, ripiego attivo)' if RC_HEALTH['down'] else ''}, "
            f"{len((_ast or {}).get('groups') or {})} gruppi" if _ast else "stato avvisi: nessun alert_state.json, "
            "parto da zero")
        _sent, _ago = tg_start_message(
            f"Desk avviato in {mode.upper()} - PID {os.getpid()} - {datetime.now(TZ).strftime('%d/%m %H:%M')} "
            f"(Roma)\nEquity ${equity(st):.2f}, posizioni aperte {len(st.positions)}")
        if not _sent and _ago is not None and _ago < CFG["TG_START_DEDUP_SEC"]:
            log(st, "Telegram", "info", f"messaggio di avvio NON inviato: un altro e partito {_ago / 60:.1f} min fa "
                "(anti-doppione, soglia 10 min)")
        alert_state_save()   # crea/aggiorna alert_state.json subito
        try:
            await readiness_check(st, http, broker)
        except Exception as e:  # noqa: BLE001
            log(st, "Logger", "warn", f"controllo di prontezza non riuscito ({type(e).__name__})")
        q_an, q_ex = asyncio.Queue(), asyncio.Queue()
        ensure_shadow_csv()
        jobs = {
            "Scout pump.fun": lambda: scout_pumpportal(st),
            "Scout pool": lambda: scout_gecko(st, http),
            "Scout trending": (lambda: scout_trending(st, http)) if "tagg" in SRC else (lambda: scout_fomo(st)),
            "Filter": lambda: filter_agent(st, http, q_an),
            "Analyst": lambda: analyst_agent(st, http, q_an, q_ex),
            "Executor": lambda: executor_agent(st, broker, q_ex),
            "Risk": lambda: risk_agent(st, http, broker),
            "Learner": lambda: shadow_agent(st, http),
            "Shadow": lambda: shadow_trade_agent(st, http),
            "Recap": lambda: recap_agent(st),
            "Helius": lambda: helius_agent(st),
            "Casi": lambda: case_watch_agent(st, http),
            "Curva": lambda: curve_watch_agent(st, http),
            "Telegram": lambda: telegram_agent(st),
            "Dashboard": lambda: dashboard(st),
        }
        await asyncio.gather(*(supervise(st, n, f) for n, f in jobs.items()))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "recap":
        print(build_recap(load_state()))
        sys.exit(0)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nFermato. Lo stato e salvato in state.json")
    except Exception as _fatal:  # noqa: BLE001  (v8.4: avviso Telegram, poi il crash resta visibile nel log)
        _txt = f"ERRORE GRAVE: il desk si e fermato ({type(_fatal).__name__})"
        tg_send(_txt)
        if TG_REF[0] is not None:
            TG_REF[0].flush(5)
        raise
