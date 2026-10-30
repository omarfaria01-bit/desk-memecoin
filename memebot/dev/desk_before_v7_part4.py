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
    "CRASH_EXIT_PCT": -15.0,          # uscita immediata
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
    "CLUSTER_TOP_N": 30,              # holder analizzati (esclusi pool/curve/locker)
    "CLUSTER_FRESH_TX": 100,          # wallet "nuovo" = meno di 100 transazioni in tutto
    "CLUSTER_MAX_HOLDERS_GPA": 60000, # oltre: si usano solo i top holder di RugCheck
    "CLUSTER_CACHE_SEC": 1800,
    "BUNDLE_NET_MIN_WALLETS": 3,      # una rete conta se ha almeno 3 wallet collegati (stesso finanziatore)
    "BUNDLE_MAX_NETWORKS": 4,         # ESTREMO: >= 4 reti...
    "BUNDLE_TOTAL_HARD_PCT": 20.0,    # ...che insieme tengono >= 20% dell'offerta
    "BUNDLE_MAX_INSIDER_WALLETS": 30, # ESTREMO: >= 30 wallet collegati in totale (reti >100 wallet escluse se tengono poco)
    "CLUSTER_FRESH_HARD": 0.80,       # ESTREMO: >= 80% dei top holder sono wallet nuovi E almeno 2 reti
    "CLUSTER_SOFT_NETWORKS": 2,       # soft: >= 2 reti...
    "CLUSTER_SOFT_PCT": 5.0,          # ...o reti insieme >= 5% dell'offerta...
    "CLUSTER_FRESH_SOFT": 0.50,       # ...o >= 50% dei top holder wallet nuovi
    "CLUSTER_PENALTY": 0.08,          # penalita del segnale soft 'cluster' (piu forte degli altri)
    "CLUSTER_SIZE_MULT": 0.4,         # size x0.4
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
    "FOMO_TREND_BONUS": 0.03,         # solo dalla vera board Fomo (serve FOMOAPI_KEY), non dal sostituto
    "FOMO_SUB_BONUS": 0.0,            # sostituto DexScreener boosts: 0 finche il Learner non vede che rende
    "KOL_BONUS": 0.03,
    "KOL_THESIS_BONUS": 0.02,
    "MOMENTUM_YOUNG_BONUS": 0.03,
    "TOTAL_BONUS_CAP": 0.10,
    "KOL_SOLD_PENALTY": 0.02,
    "KOL_MIN_SOL": 0.1,
    "MOMENTUM_MIN_SCORE": 0.5,
    "FOMO_SCOUT_SEC": 180,
    "LEARN_BONUS_MIN_N": 15,          # esiti minimi CON e SENZA il segnale
    "LEARN_BONUS_WINDOW": 150,
    "LEARN_BONUS_EDGE": 2.0,          # +step solo se la media con il segnale supera quella senza di >= 2 punti
    # v7: casi etichettati: alla chiusura l'esito diventa un esempio per il Learner (chiave = etichetta)
    "LABELLED_CASES": {"npz2FhoufQ7pKqhGpaipYWacrAkidyW2yDpVWSvpump": "cluster"},
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
    "MIN_CHANGE_M5_PCT": -8.0,        # (orig. -5.0)
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
HOST_GAPS = {"api.dexscreener.com": 0.25, "api.geckoterminal.com": 3.0, "api.rugcheck.xyz": 2.5,
             "lite-api.jup.ag": 1.1, "api.jup.ag": 1.1, "api.mainnet-beta.solana.com": 0.5}  # v6: Jupiter e RPC
HARD_STOP_PATH = os.path.join(DATA_DIR, "HARD_STOP")
# v7: moduli opzionali in sources/ (solo lettura). Se mancano il bot gira come prima, senza bonus.
SRC = {}
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from sources import fomo_trending as _fomo, kol_holders as _kol, kol_theses_reader as _theses, \
        momentum as _mom  # noqa: E402
    _kol.CALL_GAP = max(getattr(_kol, "CALL_GAP", 0.15), 0.5)   # RPC pubblica condivisa: piano
    SRC = {"fomo": _fomo, "kol": _kol, "theses": _theses, "mom": _mom}
except Exception as _e:  # noqa: BLE001
    SRC = {"error": f"{type(_e).__name__}: {_e}"[:160]}
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
        # non salvati
        self.fomo_rank = {}
        self.watch = {}
        self.processed = {}
        self.shadow = {}
        self.trending = set()
        self.hot = deque(maxlen=3000)
        self.activity = deque(maxlen=10)
        self.sec_checked = {}

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
            "learner_bonus": self.learner_bonus, "labels": self.labels,
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
    lab = d.get("labels")
    if isinstance(lab, dict):
        st.labels.update(lab)
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
    with open(os.path.join(DATA_DIR, "events.jsonl"), "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def log(st, agent, kind, msg, **data):
    st.activity.append(f"{datetime.now(TZ):%H:%M} {agent}: {msg}"[:110])
    if HEADLESS:
        print(f"{datetime.now(TZ):%H:%M:%S} {agent}: {msg}", flush=True)
    write_event(agent, kind, msg, data)


def lesson(st, msg):
    st.lessons.append(f"{datetime.now(TZ):%d/%m %H:%M} {msg}")
    log(st, "Learner", "lesson", msg)


def reject(st, mint, sym, reason, agent="Filter", cat=None, quiet=False):
    st.reject_reasons[cat or reason] += 1
    st.stats["rejected"] += 1
    msg = f"{sym} scartato: {reason}"
    if quiet:
        write_event(agent, "reject", msg, {"mint": mint})
    else:
        log(st, agent, "reject", msg, mint=mint)


def write_trade(t):
    path = os.path.join(DATA_DIR, "trades.csv")
    fields = list(t.keys())
    if os.path.exists(path):  # intestazione diversa (versione vecchia): il file vecchio viene messo da parte
        with open(path, newline="") as f:
            head = next(csv.reader(f), [])
        if head != fields:
            os.replace(path, os.path.join(DATA_DIR, f"trades_old_{int(time.time())}.csv"))
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
                    await asyncio.sleep(20)
                    return None
                if r.status != 200:
                    return None
                return await r.json(content_type=None)
        except Exception:  # noqa: BLE001
            return None

    async def post(self, url, body, headers=None):
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
    return out


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
        "dex": str(main.get("dexId", "?")), "pair": str(main.get("pairAddress", "")),
        "symbol": str(base.get("symbol", "?"))[:12], "name": str(base.get("name", ""))[:40],
        "buys_h1": int(fnum(tx.get("buys"))), "sells_h1": int(fnum(tx.get("sells"))),
        "buys_m5": int(fnum(tx5.get("buys"))), "sells_m5": int(fnum(tx5.get("sells"))),
        "vol_m5": fnum(vol.get("m5")), "vol_h1_main": fnum(vol.get("h1")),
        "chg_h1": fnum(chg.get("h1")), "chg_m5": fnum(chg.get("m5")),
        "mcap": fnum(main.get("marketCap") or main.get("fdv")), "url": str(main.get("url", "")),
        "twitter": bool(socials & {"twitter", "x"}), "telegram": "telegram" in socials,
        "website": bool(info.get("websites")),
        "age_min": (time.time() - created) / 60 if created else None,
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
    return rows if len(rows) >= 30 else None


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
def add_watch(st, mint, sym, src):
    if not mint or mint in SKIP_MINTS or mint in st.watch or mint in st.positions:
        return
    if mint in st.processed or st.cooldown.get(mint, 0) > time.time():
        return
    if len(st.watch) >= CFG["WATCH_MAX"]:
        oldest = min(st.watch, key=lambda k: st.watch[k]["seen"])
        w = st.watch.pop(oldest)
        reject(st, oldest, w["sym"], "watchlist piena", agent="Scout", cat="watchlist piena", quiet=True)
    st.watch[mint] = {"seen": time.time(), "sym": str(sym)[:12], "src": src}
    st.stats["seen"] += 1


async def scout_fomo(st):
    """v7: board trending di Fomo (con FOMOAPI_KEY) o il sostituto DexScreener boosts. Nessun bypass dei filtri."""
    if "fomo" not in SRC:
        log(st, "Scout", "warn", f"fonte Fomo non disponibile: {SRC.get('error', 'modulo assente')}")
        while True:
            await asyncio.sleep(3600)
    first = True
    while True:
        try:
            res = await SRC["fomo"].aget_trending(25)
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
            if first:
                log(st, "Scout", "info", f"fonte trending: {src} ({len(res.get('tokens') or [])} token)"
                    + (" - SOSTITUTO, non Fomo, nessun bonus" if src == "dex_boosts_substitute" else ""))
                first = False
        except Exception as e:  # noqa: BLE001
            log(st, "Scout", "warn", f"fonte trending: errore ({type(e).__name__})")
        await asyncio.sleep(max(120, CFG["FOMO_SCOUT_SEC"]))


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
        if tick % 5 == 0:  # tendenze ogni 5 minuti
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
    nets = [(len(v), sum(fnum(x.get("pct")) for x in v)) for v in groups.values() if len(v) >= nmin]
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
        if n_nets >= CFG["CLUSTER_SOFT_NETWORKS"]:
            why.append(f"{n_nets} reti collegate ({net_pct:.1f}%)")
        if net_pct >= CFG["CLUSTER_SOFT_PCT"]:
            why.append(f"reti collegate al {net_pct:.1f}%")
        if scanned >= 10 and fresh_share >= CFG["CLUSTER_FRESH_SOFT"]:
            why.append(f"{fresh_share * 100:.0f}% top holder wallet nuovi")
        level = "soft" if why else "none"
    return {"level": level, "why": "; ".join(why), "networks": n_nets, "net_pct": net_pct, "wallets": wallets,
            "fresh_share": fresh_share, "scanned": scanned, "nets": nets[:8]}


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
                    if owner in excl or acc_k in excl or amt / tot * 100 >= 10:  # pool/curva/locker
                        continue
                    owners.append((owner, amt / tot * 100))
                    if len(owners) >= CFG["CLUSTER_TOP_N"]:
                        break
        if not owners:
            owners = [(o, p) for o, p in sec.get("rc_holders") or [] if o and o not in excl][:CFG["CLUSTER_TOP_N"]]
        if not owners:
            return None
        out = []
        for owner, pct in owners:
            sigs = await _crpc(http, "getSignaturesForAddress", [owner, {"limit": int(CFG["CLUSTER_FRESH_TX"])}])
            if not isinstance(sigs, list):
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


def pos_max_hold(p):
    """v7: tenuta massima della posizione (piu corta con LP sbloccata), mai oltre MAX_HOLD_MIN."""
    return min(CFG["MAX_HOLD_MIN"], fnum(p.get("max_hold_min")) or CFG["MAX_HOLD_MIN"])


def is_rug_event(reason, minutes):
    """v7: rug = liquidita -50% (LIQ_DROP_EXIT_PCT) entro RUG_WINDOW_MIN dall'ingresso, o -80% in qualsiasi momento."""
    mt = re.search(r"liquidita crollata del (\d+)%", str(reason))
    if not mt:
        return False
    drop = float(mt.group(1))
    return drop >= 80 or (drop >= CFG["LIQ_DROP_EXIT_PCT"] and fnum(minutes) <= CFG["RUG_WINDOW_MIN"])


# ----------------------------------------------------------------------- v7: bonus dei segnali extra (sources/)
# chiave CFG -> (prefisso del tag in 'signals', passo del Learner). Limiti = OVR_LIMITS (anche per il Learner).
BONUS_KEYS = {"FOMO_TREND_BONUS": ("src:fomo_trending", 0.01), "FOMO_SUB_BONUS": ("src:dex_boosts_substitute", 0.01),
              "KOL_BONUS": ("kol:", 0.01), "KOL_THESIS_BONUS": ("kol_thesis:", 0.005),
              "MOMENTUM_YOUNG_BONUS": ("momentum_young:", 0.01)}
BONUS_LIMITS = {"FOMO_TREND_BONUS": (0.0, 0.08), "FOMO_SUB_BONUS": (0.0, 0.03), "KOL_BONUS": (0.0, 0.08),
                "KOL_THESIS_BONUS": (0.0, 0.06), "MOMENTUM_YOUNG_BONUS": (0.0, 0.08), "TOTAL_BONUS_CAP": (0.0, 0.10),
                "KOL_SOLD_PENALTY": (0.0, 0.05), "KOL_MIN_SOL": (0.05, 1.0), "MOMENTUM_MIN_SCORE": (0.4, 0.8)}


def bonus_eff(st, key):
    lim = BONUS_LIMITS.get(key, (-1e9, 1e9))
    d = fnum(((st.learner_bonus or {}).get("d") or {}).get(key)) if key in BONUS_KEYS else 0.0
    return round(clamp(fnum(CFG.get(key)) + d, lim[0], lim[1]), 4)


async def compute_bonus(st, mint, m):
    """Ritorna (bonus, tag). Mai negativo oltre -KOL_SOLD_PENALTY, mai oltre TOTAL_BONUS_CAP."""
    tags, parts = [], 0.0
    cfgb = {k: bonus_eff(st, k) for k in BONUS_LIMITS}
    fr = st.fomo_rank.get(mint)
    if fr:
        tags += [f"src:{fr['source']}", f"fomo_rank:{fr['rank']}"]
        if fr["source"] == "fomo_trending":
            parts += cfgb["FOMO_TREND_BONUS"]
        elif fr["source"] == "dex_boosts_substitute":
            parts += cfgb["FOMO_SUB_BONUS"]
    if "mom" in SRC:
        mr = await SRC["mom"].amomentum(mint)
        b, t = SRC["mom"].momentum_bonus(mr or {}, cfgb)
        parts, tags = parts + b, tags + t
    if "theses" in SRC:
        b, t = SRC["theses"].thesis_bonus(mint, SRC["kol"].tier_of, cfgb)
        parts, tags = parts + b, tags + t
    if "kol" in SRC:
        async with RPC_HEAVY_LOCK:  # condivide la RPC pubblica con il controllo cluster: uno alla volta
            kr = await SRC["kol"].akol_check(mint)
        b, t = SRC["kol"].kol_bonus(kr or {}, cfgb)
        parts, tags = parts + b, tags + t
        if (kr or {}).get("error"):
            tags.append("kol_err")
    total = clamp(parts, -cfgb["KOL_SOLD_PENALTY"], cfgb["TOTAL_BONUS_CAP"])
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
    return acts


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


def learn_shadow_step(st, key, now=None):
    """Al massimo un passo all'ora per segnale, solo con >= LEARN_SHADOW_MIN_N esiti. Ritorna l'azione o None."""
    now = now or time.time()
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


async def security_check(st, http, mint, m, cache):
    hit = cache.get(mint)
    if not hit or time.time() - hit[0] > 600:
        data = await http.get(f"https://api.rugcheck.xyz/v1/tokens/{mint}/report")
        if not isinstance(data, dict):
            return False, "report di sicurezza non disponibile, scartato per prudenza", "report assente", None
        cache[mint] = (time.time(), data)
        hit = cache[mint]
    r = hit[1]
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
    # pump.fun: curva (niente LP) o token migrati da pump.fun su PumpSwap/Raydium (LP bruciata dal protocollo).
    # Mint che finisce in 'pump' NON basta (si puo generare): serve anche il launchpad Pump.Fun o un mercato
    # pump_fun_amm nel report RugCheck. Tutti gli altri token: regola LP >= MIN_LP_LOCKED_PCT.
    lp_name = str((r.get("launchpad") or {}).get("name", "") if isinstance(r.get("launchpad"), dict) else "").lower()
    pump_amm = any("pump" in str(mk.get("marketType", "")).lower() for mk in markets if isinstance(mk, dict))
    pump_migrated = (mint.endswith("pump") and m["dex"] in ("pumpswap", "pumpfun", "raydium")
                     and ("pump" in lp_name or pump_amm))
    if m["dex"] != "pumpfun" and not pump_migrated:
        lps = [mk.get("lp") for mk in markets if isinstance(mk, dict) and isinstance(mk.get("lp"), dict)]
        if not lps:
            return False, "LP non verificabile", "LP non verificabile", None
        # lpLockedPct di RugCheck conta sia la LP bloccata sia quella bruciata
        lpmax = max(fnum(lp.get("lpLockedPct")) for lp in lps)
        if lpmax < CFG["MIN_LP_LOCKED_PCT"]:
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
        if bp is None:
            soft.append(("bundle", "reti di wallet collegati (quota non calcolabile)"))
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
           "rc_networks": rc_networks(r)}
    if severe:  # soglia grave di un segnale soft: scarto (con shadow trade per imparare)
        f0, why, cat = severe[0]
        sec.update(severe=f0, shadow_keys=[f + "_grave" for f, _, _ in severe],
                   soft=[f for f, _, _ in severe] + sec["soft"])
        return False, why, cat, sec
    if len(base_flags(f for f, _ in soft)) >= CFG["SOFT_MAX_FLAGS"]:
        sec.update(severe="multi", shadow_keys=["multi"])
        return False, "troppi segnali soft insieme: " + "; ".join(sec["soft_txt"]), "troppi segnali soft", sec
    return True, f"top 10 al {top10:.1f}%, {sec['holders']} holder, LP ok" + \
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
        market = await dex_batch(http, list(st.watch.keys()))
        for mint, pairs in market.items():
            try:
                if mint not in st.watch:
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
                for word in words_of(m["name"], m["symbol"]):
                    st.hot.append((now, word, mint))
                if m["price"] <= 0 or m["liq"] <= 0 or m["liq"] < CFG["MIN_LIQUIDITY_USD"]:
                    reject(st, mint, m["symbol"], f"liquidita ${m['liq']:,.0f} troppo bassa", cat="liquidita bassa")
                    continue
                ok, reason, cat, sec = await security_check(st, http, mint, m, cache)
                if not ok:
                    reject(st, mint, m["symbol"], reason, cat=cat)
                    if CFG["SHADOW_TRADES"] and sec and sec.get("severe"):
                        # v6: nessun acquisto possibile; l'Analyst valuta solo se aprire uno shadow trade
                        await q_out.put({"mint": mint, "m": m, "sec": sec, "filter_ok": True,
                                         "shadow_only": reason, "shadow_keys": sec.get("shadow_keys")})
                    continue
                st.stats["filter_pass"] += 1
                log(st, "Filter", "pass", f"{m['symbol']} superato: {reason}", mint=mint)
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
            why = []
            if CFG.get("MAX_CHANGE_H1_PCT") is not None and m["chg_h1"] > CFG["MAX_CHANGE_H1_PCT"]:
                why.append(f"gia +{m['chg_h1']:.0f}% in 1h")
            if m["chg_h1"] < CFG["MIN_CHANGE_H1_PCT"]:
                why.append(f"{m['chg_h1']:.0f}% in 1h")
            if m["chg_m5"] < CFG["MIN_CHANGE_M5_PCT"]:
                why.append(f"{m['chg_m5']:.0f}% in 5 min")
            if why:
                rej("momento sbagliato: " + "; ".join(why), agent="Analyst",
                       cat="momento sbagliato")
                so or add_shadow(st, mint, m, "momento")
                so or analyst_shadow(st, http, mint, m, c, "momento/momentum")
                continue
            # conferma del momentum all'ingresso (gate): pressione d'acquisto e volume che accelera
            bs5 = m["buys_m5"] / max(1, m["sells_m5"])
            if bs5 < CFG["MIN_BUYSELL_M5"]:
                rej(f"momentum non confermato: acq/vend 5m {bs5:.2f}",
                       agent="Analyst", cat="momentum 5m debole")
                so or add_shadow(st, mint, m, "momento")
                so or analyst_shadow(st, http, mint, m, c, "momento/momentum")
                continue
            NARR.current(st)  # carica anche la blocklist (valida anche se le narrative sono vecchie)
            if mint in NARR.blocklist:
                rej("mint nella blocklist di Radar", agent="Analyst", cat="blocklist Radar")
                continue
            candles = await fetch_candles(http, m["pair"])
            ch = analyze_chart(candles) if candles else None
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
                so or analyst_shadow(st, http, mint, m, c, "momento/momentum")
                continue
            if CFG["REQUIRE_ABOVE_VWAP5"] and vwap_gap is not None and vwap_gap <= 0:
                rej(f"prezzo sotto la VWAP 5 min ({vwap_gap:+.1f}%)",
                       agent="Analyst", cat="sotto VWAP 5m")
                so or add_shadow(st, mint, m, "momento")
                so or analyst_shadow(st, http, mint, m, c, "momento/momentum")
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
            bonus = 0.0
            if CFG["SIGNALS_ENABLED"] and not so and score - pen >= st.threshold - bonus_eff(st, "TOTAL_BONUS_CAP"):
                try:
                    bonus, btags = await compute_bonus(st, mint, m)
                    signals += btags
                except Exception as e:  # noqa: BLE001
                    log(st, "Analyst", "warn", f"bonus segnali non calcolati ({type(e).__name__})")
            score += bonus
            score_raw = score
            score -= pen
            # v7: controllo cluster on-chain (gratis, RPC pubblica) solo sui token che passerebbero
            cl = None
            if CFG["CLUSTER_CHECK"] and not so and score >= st.threshold:
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
                    if CFG["SHADOW_TRADES"]:  # la soglia estrema non viene mai allentata: si misura soltanto
                        spawn_shadow(st, http, mint, m, ["cluster_hard"], reason, score, flags + ["cluster"],
                                     tag="cluster", signals=signals)
                    continue
                if cl and cl["level"] == "soft" and "cluster" not in flags:
                    flags.append("cluster")
                    pen += soft_pen(st, "cluster")
                    score -= soft_pen(st, "cluster")
                    smult *= soft_size(st, "cluster")
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
            if score < st.threshold:
                rej(detail, agent="Analyst", cat="punteggio basso")
                add_shadow(st, mint, m, "punteggio", f)
                if CFG["SHADOW_TRADES"] and score_raw >= st.threshold - near:
                    spawn_shadow(st, http, mint, m, flags or ["score"], detail, score, flags, signals=signals)
                continue
            tags = [k for k in ("trend", "macd", "rsi", "trending", "narrative") if f[k] >= 1.0]
            rsi_txt = f"RSI {ch['rsi']:.0f}" if ch and ch["rsi"] is not None else "RSI n/d"
            c.update({"analyst_ok": True, "features": f, "score": score, "narr_bonus": nb,
                      "soft": flags, "size_mult": round(smult, 4), "score_raw": round(score_raw, 4),
                      "signals": "|".join(signals), "cluster": cl,
                      "narr_themes": [t for t, _, _ in nhits], "bs5": round(bs5, 2), "accel": round(accel, 2),
                      "vwap_gap": vwap_gap,
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
    """Aggiorna il prezzo di SOL da GeckoTerminal ogni 10 minuti (solo per stimare la priority fee)."""
    if time.time() - SOL_USD[1] < 600:
        return
    SOL_USD[1] = time.time()
    d = await http.get(f"https://api.geckoterminal.com/api/v2/simple/networks/solana/token_price/{WSOL}",
                       headers={"Accept": "application/json"})
    try:
        v = fnum(d["data"]["attributes"]["token_prices"][WSOL])
        if 1 < v < 100000:
            SOL_USD[0] = v
    except Exception:  # noqa: BLE001
        pass


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
        if not (self.realistic and CFG.get("SIM_USE_QUOTES")):
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
        if not (self.realistic and CFG.get("SIM_USE_QUOTES")):
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
        if not (self.realistic and CFG.get("SIM_USE_QUOTES")) or p.get("tokens", 0) <= 0 or p.get("last_price", 0) <= 0:
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
            p["qmark"] = {"ts": time.time(), "ratio": 1e-9, "no_route": True}  # invendibile: valore ~0
            return
        if not qq:
            return
        gross = int(qq["outAmount"]) / 1e9 * sol_usd()
        disp = p["tokens"] * p["last_price"]
        p["qmark"] = {"ts": time.time(), "ratio": clamp(gross / disp, 1e-9, 2.0), "impact_pct": round((1 - gross / disp) * 100, 3)}


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
            st.cash = await self.cash_usd(st)
            if st.period_start_equity == CFG["START_BALANCE"] and not st.positions:
                st.period_start_equity = st.cash

    async def rpc(self, method, params):
        d = await self.http.post(CFG["RPC_URL"], {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        if not isinstance(d, dict):
            return None, "nodo RPC senza risposta"
        if d.get("error"):
            err = d["error"]
            return None, str(err.get("message", err) if isinstance(err, dict) else err)[:140]
        return d.get("result"), None

    async def sol_balance(self):
        r, _ = await self.rpc("getBalance", [self.pub, {"commitment": "confirmed"}])
        return int(r["value"]) if isinstance(r, dict) and "value" in r else None

    async def token_raw(self, mint):
        r, _ = await self.rpc("getTokenAccountsByOwner",
                              [self.pub, {"mint": mint}, {"encoding": "jsonParsed", "commitment": "confirmed"}])
        total = 0
        for acc in (r or {}).get("value", []) if isinstance(r, dict) else []:
            try:
                total += int(acc["account"]["data"]["parsed"]["info"]["tokenAmount"]["amount"])
            except Exception:  # noqa: BLE001
                pass
        return total

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
        return base64.b64encode(bytes(signed)).decode(), None

    async def send(self, b64tx):
        sig, err = await self.rpc("sendTransaction", [b64tx, {"encoding": "base64", "skipPreflight": False,
                                                              "preflightCommitment": "processed", "maxRetries": 3}])
        if err or not sig:
            return False, err or "invio fallito"
        for _ in range(30):
            await asyncio.sleep(2)
            r, _ = await self.rpc("getSignatureStatuses", [[sig], {"searchTransactionHistory": True}])
            s = (r or {}).get("value", [None])[0] if isinstance(r, dict) else None
            if s:
                if s.get("err"):
                    return False, f"transazione fallita {sig[:10]}"
                if s.get("confirmationStatus") in ("confirmed", "finalized"):
                    return True, sig
        return False, f"conferma non arrivata {sig[:10]}"

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
        before, sb = await self.token_raw(mint), await self.sol_balance()
        ok, info = await self.send(tx)
        if not ok:
            return {"ok": False, "error": info}
        await asyncio.sleep(2)
        after, sa = await self.token_raw(mint), await self.sol_balance()
        got = (after - before) / 10 ** dec if after > before else tokens_q
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
        # FIX: vende solo la quantita registrata dal bot (mai token extra/airdrop nel wallet)
        bal = await self.token_raw(mint)
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
        ok, info = await self.send(tx)
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


async def _execute_buy(st, broker, c, mint, m):
    if st.paused:
        reject(st, mint, m["symbol"], "pausa per limite di perdita", agent="Executor", cat="pausa")
        return
    if os.path.exists(HARD_STOP_PATH):  # v6: limite di perdita totale (si toglie cancellando HARD_STOP)
        reject(st, mint, m["symbol"], "HARD_STOP attivo: perdita totale massima", agent="Executor", cat="HARD_STOP")
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
    if getattr(broker, "realistic", False):  # v6: come in live, mai oltre MAX_SOL_PER_TRADE
        size = min(size, CFG["MAX_SOL_PER_TRADE"] * sol_usd())
    pct = safe_div(size, eq)
    if size < 1:
        reject(st, mint, m["symbol"], "liquidita del conto insufficiente", agent="Executor", cat="conto vuoto")
        return
    # costi: scarta se 5 x costo stimato andata+ritorno supera il primo gradino di profitto
    rt = roundtrip_cost_pct(size, m["liq"])
    pre = None
    if getattr(broker, "realistic", False):
        # v6: preventivo reale d'acquisto + rivendita immediata (anti-honeypot, utile anche in live)
        pre = await broker.precheck(st, mint, m, size)
        if pre and not pre.get("ok", True):
            reject(st, mint, m["symbol"], pre["why"], agent="Executor", cat=pre.get("cat", "preventivo"))
            return
        if pre and pre.get("rt_pct") is not None:
            rt = pre["rt_pct"]  # costo andata+ritorno misurato sui preventivi, non stimato
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
        "liq": m["liq"], "liq_entry": m["liq"], "last_ok": time.time(), "thesis": c["thesis"],
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
    }
    st.stats["buys"] += 1
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
                  "TP1_PCT": "TP1_PCT", "TP1_FRACTION": "TP1_FRACTION",
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
                                    "BUNDLE_MAX_INSIDER_WALLETS", "CLUSTER_FRESH_HARD", "CLUSTER_SOFT_NETWORKS",
                                    "CLUSTER_SOFT_PCT", "CLUSTER_FRESH_SOFT", "CLUSTER_PENALTY", "CLUSTER_SIZE_MULT",
                                    "LP_SOFT_MAX_HOLD_MIN", "LP_EXTREME_LOCKED_PCT", "FOMO_TREND_BONUS",
                                    "FOMO_SUB_BONUS", "KOL_BONUS", "KOL_THESIS_BONUS", "MOMENTUM_YOUNG_BONUS",
                                    "TOTAL_BONUS_CAP", "KOL_SOLD_PENALTY", "KOL_MIN_SOL", "MOMENTUM_MIN_SCORE")}}
OVR_POS_MAP = {"stop_loss_pct": "STOP_LOSS_PCT", "take_profit_pct": "TAKE_PROFIT_PCT",
               "tp_sell_fraction": "TAKE_PROFIT_FRACTION", "trail_dist_pct": "TRAIL_DISTANCE_PCT"}
OVR_ACTIONS = ("hold", "sell_all", "sell_half")
# limiti di sicurezza fissi (min, max) sui valori di CFG
OVR_LIMITS = {"STOP_LOSS_PCT": (-15.0, -3.0), "TRAIL_DISTANCE_PCT": (3.0, 15.0), "TRAIL_ACTIVATE_PCT": (3.0, 100.0),
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
              "CLUSTER_FRESH_HARD": (0.7, 1.0), "CLUSTER_SOFT_NETWORKS": (1.0, 4.0), "CLUSTER_SOFT_PCT": (2.0, 15.0),
              "CLUSTER_FRESH_SOFT": (0.3, 0.8), "CLUSTER_PENALTY": (0.02, 0.15), "CLUSTER_SIZE_MULT": (0.2, 1.0),
              "LP_SOFT_MAX_HOLD_MIN": (15.0, 180.0), "LP_EXTREME_LOCKED_PCT": (0.0, 10.0),
              **BONUS_LIMITS}
OVR_INT_KEYS = {"MAX_HOLD_MIN", "TIME_STOP_MIN", "SOFT_MAX_FLAGS", "BUNDLE_MAX_NETWORKS", "BUNDLE_NET_MIN_WALLETS",
                "BUNDLE_MAX_INSIDER_WALLETS", "CLUSTER_SOFT_NETWORKS", "LP_SOFT_MAX_HOLD_MIN"}
SL_WARN_REF = -10.0  # avviso se uno stop da override e piu largo di -10%
# valori originali (codice + config.json): cancellare overrides.json riporta a questi
OVR_DEFAULTS = {k: CFG[k] for k in OVR_LIMITS}


def ovr_value(cfg_key, raw, where):
    """Valida e limita un valore. Ritorna (valore o None se da ignorare, lista di note)."""
    notes = []
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(float(raw)):
        return None, [f"{where}: valore {raw!r} non numerico, ignorato"]
    v = float(raw)
    if cfg_key == "STOP_LOSS_PCT" and v > 0:
        notes.append(f"{where}: stop loss {v:g} positivo, interpretato come {-v:g}")
        v = -v
    lo, hi = OVR_LIMITS[cfg_key]
    c = clamp(v, lo, hi)
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
                notes.append(("info", f"global.{fk}=null: torna al default {OVR_DEFAULTS[ck]:g}"))
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
        return {k: CFG[k] for k in OVR_LIMITS}

    def eff(self, mint, cfg_key):
        return self.positions.get(mint, {}).get(cfg_key, CFG[cfg_key])

    def _revert(self, st, why):
        changed = [f"{k} {CFG[k]:g}->{OVR_DEFAULTS[k]:g}" for k in OVR_LIMITS if CFG[k] != OVR_DEFAULTS[k]]
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
                changes.append(f"{k} {CFG[k]:g}->{v:g}")
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
    p["prio_fee_sol"] = (p.get("prio_fee_sol") or 0.0) + (res.get("prio_fee_sol") or 0.0)
    p["fill_failed"] = p.get("fill_failed", 0) + res.get("fill_failed", 0)
    p["fee_failed_usd"] = p.get("fee_failed_usd", 0.0) + res.get("fee_failed_usd", 0.0)
    if res.get("quote_impact") is not None:
        p["sell_quote_impact"] = res["quote_impact"]
    if res.get("latency_slip") is not None:
        p["sell_latency_slip"] = res["latency_slip"]
    p["realized"] += res["value"]
    p["tokens"] = max(0.0, p["tokens"] - res["tokens"])
    if fraction < 0.999 and p["tokens"] > 0:
        if mark_tp:  # una vendita parziale da override (sell_half) non consuma la presa di profitto
            p["tp_done"] = True
        log(st, "Risk", "sell", f"VENDE {fraction * 100:.0f}% di {p['symbol']} per ${res['value']:.2f}: {reason}",
            mint=mint, quote_src=res.get("src", "formula"), quote_impact=res.get("quote_impact"),
            latency_slip=res.get("latency_slip"), prio_fee_sol=res.get("prio_fee_sol"),
            fill_failed=res.get("fill_failed", 0))
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
    st.closed.append(t)
    write_trade(t)
    st.cooldown[mint] = time.time() + CFG["COOLDOWN_MIN"] * 60
    log(st, "Risk", "sell", f"CHIUDE {p['symbol']} {t['pnl_pct']:+.1f}% (${pnl:+.2f}): {reason}", mint=mint,
        pnl=t["pnl"], quote_src=res.get("src", "formula"), quote_impact=res.get("quote_impact"),
        latency_slip=res.get("latency_slip"), prio_fee_sol=res.get("prio_fee_sol"), fill_failed=res.get("fill_failed", 0))
    learn_from_trade(st, p, pnl, reason)
    rug = bool(zero or "rug" in reason or "invendibile" in reason or is_rug_event(reason, t["minutes"]))
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
    if p.get("sec_risks") is None:
        p["sec_risks"] = dangers
        return None
    new = [d for d in dangers if d not in p["sec_risks"]]
    return f"nuovo rischio: {', '.join(new[:2])}" if new else None


async def risk_agent(st, http, broker):
    last_cash = 0.0
    while True:
        await asyncio.sleep(CFG["RISK_CHECK_SEC"])
        try:
            stop_now = os.path.exists(STOP_PATH)
            try:
                OVR.check(st)
                OVR.prune(set(st.positions))
            except Exception as e:  # noqa: BLE001
                log(st, "Risk", "warn", f"override non applicati ({type(e).__name__}), tengo gli ultimi valori")
            if st.positions:
                market = await dex_batch(http, list(st.positions.keys()))
                for mint, p in list(st.positions.items()):
                    try:
                        if mint in market:
                            m = summarize(market[mint])
                            if m["price"] > 0:
                                p["last_price"], p["liq"], p["last_ok"] = m["price"], max(m["liq"], 1.0), time.time()
                        if getattr(broker, "realistic", False):
                            try:  # v6: valore di uscita dal preventivo Jupiter reale (ogni MARK_QUOTE_SEC)
                                await broker.refresh_mark(mint, p)
                            except Exception:  # noqa: BLE001
                                pass
                        pnl = mark_pnl_pct(p)
                        p["peak_pct"] = max(p["peak_pct"], pnl)
                        sl, tp = OVR.eff(mint, "STOP_LOSS_PCT"), OVR.eff(mint, "TAKE_PROFIT_PCT")
                        tpf, tdist = OVR.eff(mint, "TAKE_PROFIT_FRACTION"), OVR.eff(mint, "TRAIL_DISTANCE_PCT")
                        stage = int(p.get("tp_stage", 1 if p.get("tp_done") else 0))
                        tp1 = min(CFG["TP1_PCT"], tp)  # il primo gradino non supera mai il secondo (anche con override)
                        rung_pct, rung_frac = (tp1, CFG["TP1_FRACTION"]) if stage == 0 else (tp, tpf)
                        tdist_eff = min(tdist, CFG["TRAIL_TIGHT_PCT"]) if p["peak_pct"] >= CFG["TRAIL_TIGHT_AFTER_PCT"] \
                            else tdist
                        # dopo il TP1 lo stop sale a pareggio +3% netto (mai sopra il TP1, mai sotto lo stop)
                        stop_eff = max(sl, min(CFG["BREAKEVEN_PCT"], tp1 - 1)) if stage >= 1 else sl
                        p["eff"] = {"stop": stop_eff, "stop_base": sl, "tp": tp, "tp1": tp1, "tp_frac": tpf,
                                    "tp1_frac": CFG["TP1_FRACTION"], "tp_stage": stage,
                                    "trail_start": CFG["TRAIL_ACTIVATE_PCT"], "trail_dist": tdist_eff,
                                    "max_hold": pos_max_hold(p), "time_stop": CFG["TIME_STOP_MIN"]}
                        p["stop_hits"] = p["stop_hits"] + 1 if pnl <= stop_eff else 0
                        p["mark_net"], p["pnl_net"] = round(mark_value(p), 4), round(pnl, 3)  # per la dashboard
                        held = (time.time() - p["opened"]) / 60
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
                            await sell_position(st, broker, mint, f"liquidita crollata del {drop:.0f}%")
                        elif pnl <= CFG["CRASH_EXIT_PCT"]:
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
                            tok_before = p["tokens"]
                            await sell_position(st, broker, mint, f"presa di profitto {stage + 1}/2 a {pnl:+.0f}%",
                                                fraction=frac, mark_tp=False)
                            if mint in st.positions and st.positions[mint]["tokens"] >= tok_before - 1e-12:
                                st.positions[mint]["tp_stage"] = stage  # v6: vendita fallita, il gradino resta da fare
                        elif p["peak_pct"] >= CFG["TRAIL_ACTIVATE_PCT"] and pnl <= p["peak_pct"] - tdist_eff:
                            await sell_position(st, broker, mint,
                                                f"trailing {tdist_eff:g}%: massimo {p['peak_pct']:+.1f}%, ora {pnl:+.1f}%")
                        elif stage == 0 and held > CFG["TIME_STOP_MIN"] and pnl < CFG["TIME_STOP_MIN_PCT"]:  # (v4 <=)
                            await sell_position(st, broker, mint, f"stop a tempo: {held:.0f} min e solo {pnl:+.1f}%")
                        elif held > pos_max_hold(p):
                            await sell_position(st, broker, mint, f"tempo massimo {pos_max_hold(p):g} min" +
                                                (" (LP sbloccata)" if pos_max_hold(p) < CFG["MAX_HOLD_MIN"] else ""))
                        elif time.time() - st.sec_checked.get(mint, 0) > CFG["RECHECK_SECURITY_MIN"] * 60:
                            st.sec_checked[mint] = time.time()
                            why = await recheck_security(st, http, mint, p)
                            if why:
                                await sell_position(st, broker, mint, why)
                    except Exception as e:  # noqa: BLE001
                        log(st, "Risk", "error", f"errore su {p.get('symbol')} ({type(e).__name__}), continuo")
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
                 "rug", "tag", "signals"]


def ensure_shadow_csv():
    """v7: shadow_trades.csv con l'intestazione gia all'avvio (anche prima della prima chiusura)."""
    if os.path.exists(SHADOW_CSV):
        with open(SHADOW_CSV, newline="") as f:
            head = next(csv.reader(f), [])
        if head == SHADOW_FIELDS:
            return
        os.replace(SHADOW_CSV, SHADOW_CSV.replace(".csv", f"_old_{int(time.time())}.csv"))
    with open(SHADOW_CSV, "w", newline="") as f:
        csv.writer(f).writerow(SHADOW_FIELDS)


def write_csv_row(path, row):
    fields = list(row.keys())
    if os.path.exists(path):
        with open(path, newline="") as f:
            head = next(csv.reader(f), [])
        if head != fields:
            os.replace(path, path.replace(".csv", f"_old_{int(time.time())}.csv"))
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerow(row)


def shadow_tag(keys, flags):
    ks = list(keys) + list(flags or [])
    for k, tag in (("cluster", "cluster"), ("lp_unlocked", "lp_unlocked"), ("analyst_momentum", "analyst_momentum")):
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


def _close_shadow_row(st, mint, sh, pnl, rug, why, now=None):
    now = now or time.time()
    row = {"aperto": datetime.fromtimestamp(sh["t"], TZ).strftime("%Y-%m-%d %H:%M"),
           "chiuso": datetime.fromtimestamp(now, TZ).strftime("%Y-%m-%d %H:%M"), "symbol": sh["sym"], "mint": mint,
           "motivo_scarto": sh["reason"][:160], "chiavi": "|".join(sh["keys"]), "segnali_soft": "|".join(sh["flags"]),
           "score": sh["score"], "soglia": sh["thr"], "prezzo_entrata": sh["price"], "liq_entrata": round(sh["liq_entry"]),
           "rt_cost_pct": sh["rt"], "quote_src": sh.get("src", "formula"), "pnl_pct": round(pnl, 2),
           "peak_pct": round(sh.get("peak_pct", 0.0), 2), "minuti": int((now - sh["t"]) / 60), "uscita": why,
           "rug": int(bool(rug or pnl <= -50)), "tag": sh.get("tag", ""), "signals": "|".join(sh.get("signals") or [])}
    write_csv_row(SHADOW_CSV, row)
    st.stats["shadow_closed"] += 1
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
          "max_hold": max_hold}
    st.shadow_trades[mint] = sh
    st.stats["shadow_open"] += 1
    write_event("Learner", "shadow_open", f"shadow {m['symbol']} aperto ({'|'.join(keys)}): {reason[:120]}",
                {"mint": mint, "keys": list(keys), "score": round(score, 4), "rt_cost_pct": round(rt, 2), "tag": tag})
    return sh


def shadow_decide(sh, now=None):
    """Stesse regole d'uscita del Risk. Ritorna (motivo, frazione, azzera, rug) o None."""
    now = now or time.time()
    pnl = mark_pnl_pct(sh)
    sh["peak_pct"] = max(sh["peak_pct"], pnl)
    sl, tp, tpf, tdist = CFG["STOP_LOSS_PCT"], CFG["TAKE_PROFIT_PCT"], CFG["TAKE_PROFIT_FRACTION"], CFG["TRAIL_DISTANCE_PCT"]
    stage = int(sh.get("tp_stage", 0))
    tp1 = min(CFG["TP1_PCT"], tp)
    rung_pct, rung_frac = (tp1, CFG["TP1_FRACTION"]) if stage == 0 else (tp, tpf)
    tdist_eff = min(tdist, CFG["TRAIL_TIGHT_PCT"]) if sh["peak_pct"] >= CFG["TRAIL_TIGHT_AFTER_PCT"] else tdist
    stop_eff = max(sl, min(CFG["BREAKEVEN_PCT"], tp1 - 1)) if stage >= 1 else sl
    sh["stop_hits"] = sh["stop_hits"] + 1 if pnl <= stop_eff else 0
    held = (now - sh["t"]) / 60
    if now - sh["last_ok"] > 900:
        return "nessun prezzo da 15 min, possibile rug", 1.0, True, True
    if sh["liq_entry"] > 0 and sh["liq"] < sh["liq_entry"] * (1 - CFG["LIQ_DROP_EXIT_PCT"] / 100):
        drop = (1 - sh["liq"] / sh["liq_entry"]) * 100
        why = f"liquidita crollata del {drop:.0f}%"
        return why, 1.0, False, is_rug_event(why, held)
    if pnl <= CFG["CRASH_EXIT_PCT"]:
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
    sh["realized"] += 0.0 if zero else exit_value(sold, sh["last_price"], sh["liq"])
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
        if not st.shadow_trades:
            continue
        try:
            market = await dex_batch(http, list(st.shadow_trades.keys()))
            now = time.time()
            for mint, sh in list(st.shadow_trades.items()):
                if mint in market:
                    m = summarize(market[mint])
                    if m["price"] > 0:
                        sh["last_price"], sh["liq"], sh["last_ok"] = m["price"], max(m["liq"], 1.0), now
                await shadow_apply(st, http, mint, sh, shadow_decide(sh, now), now)
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
    if st.reject_reasons:
        lines += ["", "Motivi di scarto piu frequenti:"]
        lines += [f"- {r}: {n}" for r, n in st.reject_reasons.most_common(10)]
    return "\n".join(lines)


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


# ======================================================================= avvio
async def supervise(st, name, factory):
    while True:
        try:
            await factory()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log(st, "Logger", "error", f"{name}: {type(e).__name__} {e}. Riavvio tra 10s")
            await asyncio.sleep(10)


async def main():
    st = load_state()
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
        try:
            await readiness_check(st, http, broker)
        except Exception as e:  # noqa: BLE001
            log(st, "Logger", "warn", f"controllo di prontezza non riuscito ({type(e).__name__})")
        q_an, q_ex = asyncio.Queue(), asyncio.Queue()
        ensure_shadow_csv()
        jobs = {
            "Scout pump.fun": lambda: scout_pumpportal(st),
            "Scout pool": lambda: scout_gecko(st, http),
            "Scout trending": lambda: scout_fomo(st),
            "Filter": lambda: filter_agent(st, http, q_an),
            "Analyst": lambda: analyst_agent(st, http, q_an, q_ex),
            "Executor": lambda: executor_agent(st, broker, q_ex),
            "Risk": lambda: risk_agent(st, http, broker),
            "Learner": lambda: shadow_agent(st, http),
            "Shadow": lambda: shadow_trade_agent(st, http),
            "Recap": lambda: recap_agent(st),
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
