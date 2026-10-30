#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Desk memecoin Solana - SOLO PAPER TRADING.
Nessun soldo vero, nessuna chiave privata, nessun ordine reale.

Agenti con passaggi obbligati:
  Scout -> Filter -> Analyst -> Executor -> Risk   (Logger registra tutto)

Avvio:        ./venv/bin/python bot.py
Recap subito: ./venv/bin/python bot.py recap

File creati nella stessa cartella:
  state.json, events.jsonl (ogni decisione con il motivo), trades.csv,
  equity.csv, recap_AAAA-MM-GG.txt (ogni mattina all'ora RECAP_HOUR)

Per cambiare le regole crea config.json con le chiavi di CFG da modificare,
per esempio: {"MIN_VOLUME_H1": 20000, "STOP_LOSS_PCT": -15}
"""

import asyncio
import csv
import json
import os
import sys
import time
from collections import Counter, deque
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import aiohttp
import websockets
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
WSOL = "So11111111111111111111111111111111111111112"
SKIP_MINTS = {
    WSOL,
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}

# ------------------------------------------------------------------ regole
CFG = {
    "START_BALANCE": 1000.0,       # saldo simulato in dollari
    "MAX_TRADE_PCT": 0.10,         # massimo 10% dell'equity per operazione
    "MAX_POSITIONS": 3,            # posizioni aperte contemporaneamente
    "STOP_LOSS_PCT": -8.0,         # stop fisso
    "TRAIL_ACTIVATE_PCT": 15.0,    # il trailing si attiva da +15%
    "TRAIL_DISTANCE_PCT": 10.0,    # esce se perde 10 punti dal massimo
    "MAX_HOLD_MIN": 360,           # uscita dopo 6 ore comunque
    "DAILY_LOSS_LIMIT_PCT": -20.0, # sotto questa perdita: niente nuovi acquisti
    "FEE_PCT": 1.0,                # commissioni simulate per lato
    "MIN_VOLUME_H1": 40000.0,      # volume minimo nell'ultima ora
    "MAX_TOP10_PCT": 25.0,         # top 10 holder, pool escluse
    "MIN_LP_LOCKED_PCT": 90.0,     # LP bloccata o bruciata (token graduati)
    "MIN_LIQUIDITY_USD": 15000.0,  # liquidita minima per uscire senza disastri
    "MAX_TRANSFER_FEE_PCT": 5.0,   # tassa massima sul trasferimento
    "REJECT_INSIDERS": True,       # scarta se RugCheck vede reti di wallet collegati
    "MIN_BUY_SELL_RATIO": 1.2,     # Analyst: piu acquisti che vendite
    "MAX_CHANGE_H1_PCT": 150.0,    # Analyst: troppo salito = troppo tardi
    "MIN_CHANGE_H1_PCT": -10.0,    # Analyst: non comprare cio che crolla
    "MIN_CHANGE_M5_PCT": -5.0,     # Analyst: non comprare mentre scende ora
    "WATCH_TTL_MIN": 120,          # quanto resta un token in osservazione
    "WATCH_MAX": 1500,             # token osservati al massimo
    "RECHECK_SEC": 90,             # ogni quanto il Filter ricontrolla
    "RISK_CHECK_SEC": 20,          # ogni quanto il Risk controlla i prezzi
    "COOLDOWN_MIN": 120,           # dopo un'uscita non ricompra lo stesso token
    "RECAP_HOUR": 8,               # ora del recap mattutino
    "TIMEZONE": "Europe/Rome",
}

_cfg_path = os.path.join(DATA_DIR, "config.json")
if os.path.exists(_cfg_path):
    try:
        with open(_cfg_path) as _f:
            CFG.update(json.load(_f))
    except Exception as _e:  # noqa: BLE001
        print(f"config.json non valido, uso i valori predefiniti: {_e}")

TZ = ZoneInfo(CFG["TIMEZONE"])


def fnum(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------ stato
class State:
    def __init__(self):
        self.cash = CFG["START_BALANCE"]
        self.positions = {}
        self.period_start_ts = time.time()
        self.period_start_equity = CFG["START_BALANCE"]
        self.closed = []
        self.cooldown = {}
        self.stats = Counter()
        self.reject_reasons = Counter()
        self.paused = False
        self.equity_hist = deque(maxlen=3000)
        self.watch = {}
        self.processed = {}
        self.activity = deque(maxlen=10)

    def to_json(self):
        return {
            "cash": self.cash,
            "positions": self.positions,
            "period_start_ts": self.period_start_ts,
            "period_start_equity": self.period_start_equity,
            "closed": self.closed,
            "cooldown": self.cooldown,
            "stats": dict(self.stats),
            "reject_reasons": dict(self.reject_reasons),
            "paused": self.paused,
            "equity_hist": list(self.equity_hist)[-1000:],
        }


STATE_PATH = os.path.join(DATA_DIR, "state.json")


def load_state():
    st = State()
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH) as f:
                d = json.load(f)
            st.cash = d.get("cash", st.cash)
            st.positions = d.get("positions", {})
            st.period_start_ts = d.get("period_start_ts", st.period_start_ts)
            st.period_start_equity = d.get("period_start_equity", st.period_start_equity)
            st.closed = d.get("closed", [])
            st.cooldown = d.get("cooldown", {})
            st.stats = Counter(d.get("stats", {}))
            st.reject_reasons = Counter(d.get("reject_reasons", {}))
            st.paused = d.get("paused", False)
            st.equity_hist.extend(tuple(x) for x in d.get("equity_hist", []))
        except Exception as e:  # noqa: BLE001
            print(f"state.json illeggibile, riparto da zero: {e}")
    return st


def save_state(st):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st.to_json(), f)
    os.replace(tmp, STATE_PATH)


# ------------------------------------------------------------------ Logger
def write_event(agent, kind, msg, data=None):
    rec = {
        "ts": datetime.now(TZ).isoformat(timespec="seconds"),
        "agent": agent,
        "kind": kind,
        "msg": msg,
    }
    if data:
        rec.update(data)
    with open(os.path.join(DATA_DIR, "events.jsonl"), "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def log(st, agent, kind, msg, **data):
    t = datetime.now(TZ).strftime("%H:%M")
    line = f"{t} {agent}: {msg}"
    st.activity.append(line[:110])
    write_event(agent, kind, msg, data)


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
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(t.keys()))
        if new:
            w.writeheader()
        w.writerow(t)


# ------------------------------------------------------------------ rete
async def get_json(session, url, headers=None):
    try:
        async with session.get(url, headers=headers) as r:
            if r.status == 429:
                await asyncio.sleep(15)
                return None
            if r.status != 200:
                return None
            return await r.json(content_type=None)
    except Exception:  # noqa: BLE001
        return None


async def dex_batch(session, mints):
    """Dati di mercato da DexScreener, 30 token per richiesta."""
    out = {}
    for i in range(0, len(mints), 30):
        chunk = mints[i:i + 30]
        data = await get_json(
            session, "https://api.dexscreener.com/tokens/v1/solana/" + ",".join(chunk)
        )
        if isinstance(data, list):
            for pair in data:
                base = (pair.get("baseToken") or {}).get("address")
                if base in chunk:
                    out.setdefault(base, []).append(pair)
        await asyncio.sleep(0.3)
    return out


def summarize(pairs):
    vol_h1 = sum(fnum((p.get("volume") or {}).get("h1")) for p in pairs)
    main = max(
        pairs,
        key=lambda p: (
            fnum((p.get("liquidity") or {}).get("usd")),
            fnum((p.get("volume") or {}).get("h1")),
        ),
    )
    tx = (main.get("txns") or {}).get("h1") or {}
    chg = main.get("priceChange") or {}
    return {
        "vol_h1": vol_h1,
        "price": fnum(main.get("priceUsd")),
        "liq": fnum((main.get("liquidity") or {}).get("usd")),
        "dex": str(main.get("dexId", "?")),
        "symbol": str((main.get("baseToken") or {}).get("symbol", "?"))[:12],
        "buys_h1": int(fnum(tx.get("buys"))),
        "sells_h1": int(fnum(tx.get("sells"))),
        "chg_h1": fnum(chg.get("h1")),
        "chg_m5": fnum(chg.get("m5")),
        "mcap": fnum(main.get("marketCap") or main.get("fdv")),
        "url": str(main.get("url", "")),
    }


# ------------------------------------------------------------------ Scout
def add_watch(st, mint, sym, src):
    if not mint or mint in SKIP_MINTS or mint in st.watch or mint in st.positions:
        return
    if mint in st.processed or st.cooldown.get(mint, 0) > time.time():
        return
    if len(st.watch) >= CFG["WATCH_MAX"]:
        oldest = min(st.watch, key=lambda k: st.watch[k]["seen"])
        w = st.watch.pop(oldest)
        reject(st, oldest, w["sym"], "watchlist piena, tolto il piu vecchio",
               agent="Scout", cat="watchlist piena", quiet=True)
    st.watch[mint] = {"seen": time.time(), "sym": str(sym)[:12], "src": src}
    st.stats["seen"] += 1


async def scout_pumpportal(st):
    """Nuovi token e migrazioni di pump.fun in tempo reale."""
    url = "wss://pumpportal.fun/api/data"
    backoff = 5
    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20,
                                          max_size=2 ** 20) as ws:
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                await ws.send(json.dumps({"method": "subscribeMigration"}))
                log(st, "Scout", "info", "collegato a pump.fun")
                backoff = 5
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except Exception:  # noqa: BLE001
                        continue
                    mint = msg.get("mint")
                    if mint:
                        kind = "migrazione" if "migrat" in str(msg.get("txType", "")) else "nuovo"
                        add_watch(st, mint, msg.get("symbol") or "?", f"pump.fun {kind}")
        except Exception as e:  # noqa: BLE001
            log(st, "Scout", "warn", f"pump.fun disconnesso ({type(e).__name__}), riprovo tra {backoff}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 120)


async def scout_gecko(st, session):
    """Nuove pool su Raydium, PumpSwap, Meteora e altri DEX Solana."""
    url = "https://api.geckoterminal.com/api/v2/networks/solana/new_pools?page=1"
    headers = {"Accept": "application/json;version=20230302"}
    first = True
    while True:
        data = await get_json(session, url, headers=headers)
        if data:
            if first:
                log(st, "Scout", "info", "collegato alle nuove pool (GeckoTerminal)")
                first = False
            for p in data.get("data", []):
                rel = p.get("relationships") or {}
                bid = (((rel.get("base_token") or {}).get("data")) or {}).get("id") or ""
                dex = (((rel.get("dex") or {}).get("data")) or {}).get("id") or "?"
                if bid.startswith("solana_"):
                    name = str((p.get("attributes") or {}).get("name", "?"))
                    add_watch(st, bid.split("_", 1)[1], name.split("/")[0].strip(), f"{dex} nuova pool")
        await asyncio.sleep(60)


# ------------------------------------------------------------------ Filter
async def security_check(session, mint, m, cache):
    hit = cache.get(mint)
    if not hit or time.time() - hit[0] > 600:
        await asyncio.sleep(1.5)
        data = await get_json(session, f"https://api.rugcheck.xyz/v1/tokens/{mint}/report")
        if not data:
            return False, "report di sicurezza non disponibile, scartato per prudenza", "report assente"
        cache[mint] = (time.time(), data)
        hit = cache[mint]
    r = hit[1]
    tok = r.get("token") or {}

    if r.get("mintAuthority") or tok.get("mintAuthority"):
        return False, "mint non revocata: possono creare nuovi token", "mint non revocata"
    if r.get("freezeAuthority") or tok.get("freezeAuthority"):
        return False, "freeze attiva: possono bloccare i wallet", "freeze attiva"
    if r.get("rugged"):
        return False, "segnalato come rug", "rug"

    tf = fnum((r.get("transferFee") or {}).get("pct"))
    if tf > CFG["MAX_TRANSFER_FEE_PCT"]:
        return False, f"tassa di trasferimento {tf:.1f}%", "tassa alta"

    markets = r.get("markets") or []
    if m["dex"] != "pumpfun":  # sulla curva di pump.fun non c'e una LP da bloccare
        locked = [fnum((mk.get("lp") or {}).get("lpLockedPct")) for mk in markets if mk.get("lp")]
        if not locked:
            return False, "stato della LP non verificabile", "LP non verificabile"
        if max(locked) < CFG["MIN_LP_LOCKED_PCT"]:
            return False, f"LP bloccata solo al {max(locked):.0f}%", "LP non bloccata"

    exclude = set()
    for mk in markets:
        for v in list(mk.values()) + list((mk.get("lp") or {}).values()):
            if isinstance(v, str):
                exclude.add(v)
    for addr, info in (r.get("knownAccounts") or {}).items():
        if str((info or {}).get("type", "")).upper() in ("AMM", "LOCKER", "BURN", "PROGRAM"):
            exclude.add(addr)
    holders = [
        h for h in (r.get("topHolders") or [])
        if h.get("address") not in exclude and h.get("owner") not in exclude
    ]
    if not holders:
        return False, "lista holder non disponibile", "holder non disponibili"
    pcts = [fnum(h.get("pct")) for h in holders]
    if max(pcts) <= 1.0 and sum(pcts) <= 1.0:
        pcts = [p * 100 for p in pcts]
    top10 = sum(sorted(pcts, reverse=True)[:10])
    if top10 > CFG["MAX_TOP10_PCT"]:
        return False, f"top 10 holder al {top10:.1f}%", "top 10 troppo alti"

    dangers = [x.get("name", "?") for x in (r.get("risks") or [])
               if str(x.get("level", "")).lower() == "danger"]
    if dangers:
        return False, "rischi gravi: " + ", ".join(dangers[:3]), "rischi gravi RugCheck"
    if CFG["REJECT_INSIDERS"] and fnum(r.get("graphInsidersDetected")) > 0:
        return False, "wallet collegati tra loro (insider o bundle)", "insider/bundle"

    return True, f"mint revocata, top 10 al {top10:.1f}%, LP ok", None


async def filter_agent(st, session, q_out):
    cache = {}
    while True:
        await asyncio.sleep(CFG["RECHECK_SEC"])
        now = time.time()
        for k in [k for k, v in st.processed.items() if now - v > 6 * 3600]:
            del st.processed[k]
        for k in [k for k, v in st.cooldown.items() if v < now]:
            del st.cooldown[k]
        for k in [k for k, v in cache.items() if now - v[0] > 3600]:
            del cache[k]
        for mint, w in list(st.watch.items()):
            if now - w["seen"] > CFG["WATCH_TTL_MIN"] * 60:
                st.watch.pop(mint, None)
                reject(st, mint, w["sym"],
                       f"volume 1h mai sopra ${CFG['MIN_VOLUME_H1']:,.0f} in {CFG['WATCH_TTL_MIN']} min",
                       cat="volume insufficiente", quiet=True)
        if not st.watch:
            continue
        market = await dex_batch(session, list(st.watch.keys()))
        for mint, pairs in market.items():
            if mint not in st.watch:
                continue
            m = summarize(pairs)
            if m["vol_h1"] < CFG["MIN_VOLUME_H1"]:
                continue  # resta in osservazione
            st.watch.pop(mint, None)
            st.processed[mint] = now
            st.stats["volume_ok"] += 1
            if m["price"] <= 0 or m["liq"] < CFG["MIN_LIQUIDITY_USD"]:
                reject(st, mint, m["symbol"], f"liquidita ${m['liq']:,.0f} troppo bassa",
                       cat="liquidita bassa")
                continue
            ok, reason, cat = await security_check(session, mint, m, cache)
            if not ok:
                reject(st, mint, m["symbol"], reason, cat=cat)
                continue
            st.stats["filter_pass"] += 1
            log(st, "Filter", "pass", f"{m['symbol']} superato: {reason}, vol 1h ${m['vol_h1']:,.0f}",
                mint=mint)
            await q_out.put({"mint": mint, "m": m, "filter_ok": True})


# ------------------------------------------------------------------ Analyst
async def analyst_agent(st, q_in, q_out):
    while True:
        c = await q_in.get()
        m = c["m"]
        if not c.get("filter_ok"):
            log(st, "Analyst", "block", f"{m['symbol']} bloccato: non arriva dal Filter")
            continue
        ratio = m["buys_h1"] / max(1, m["sells_h1"])
        why = []
        if ratio < CFG["MIN_BUY_SELL_RATIO"]:
            why.append(f"acquisti/vendite {ratio:.2f}")
        if m["chg_h1"] > CFG["MAX_CHANGE_H1_PCT"]:
            why.append(f"gia +{m['chg_h1']:.0f}% in 1h, tardi")
        if m["chg_h1"] < CFG["MIN_CHANGE_H1_PCT"]:
            why.append(f"{m['chg_h1']:.0f}% in 1h, in calo")
        if m["chg_m5"] < CFG["MIN_CHANGE_M5_PCT"]:
            why.append(f"{m['chg_m5']:.0f}% in 5 min, scende ora")
        if why:
            reject(st, c["mint"], m["symbol"], "; ".join(why), agent="Analyst", cat="Analyst: momento sbagliato")
            continue
        c["analyst_ok"] = True
        c["thesis"] = (f"acq/vend {ratio:.2f}, {m['chg_h1']:+.0f}% 1h, vol ${m['vol_h1']:,.0f}, "
                       f"liq ${m['liq']:,.0f}")
        log(st, "Analyst", "approve", f"{m['symbol']} approvato: {c['thesis']}", mint=c["mint"])
        await q_out.put(c)


# ------------------------------------------------------------------ Executor (paper)
def pos_value(p):
    gross = p["tokens"] * p["last_price"]
    if gross <= 0:
        return 0.0
    impact = gross / (max(p["liq"], 1.0) / 2 + gross)
    return gross * (1 - impact) * (1 - CFG["FEE_PCT"] / 100)


def pos_pnl_pct(p):
    return (pos_value(p) / p["cost"] - 1) * 100 if p["cost"] else 0.0


def equity(st):
    return st.cash + sum(pos_value(p) for p in st.positions.values())


async def executor_agent(st, q_in):
    while True:
        c = await q_in.get()
        mint, m = c["mint"], c["m"]
        if not (c.get("filter_ok") and c.get("analyst_ok")):
            log(st, "Executor", "block", f"{m['symbol']} bloccato: manca l'ok di Filter o Analyst")
            continue
        if st.paused:
            reject(st, mint, m["symbol"], "pausa per limite di perdita", agent="Executor", cat="pausa")
            continue
        if mint in st.positions:
            continue
        if len(st.positions) >= CFG["MAX_POSITIONS"]:
            reject(st, mint, m["symbol"], f"gia {len(st.positions)} posizioni aperte",
                   agent="Executor", cat="troppe posizioni")
            continue
        size = min(equity(st) * CFG["MAX_TRADE_PCT"], st.cash)
        if size < 1:
            reject(st, mint, m["symbol"], "liquidita del conto insufficiente", agent="Executor",
                   cat="conto vuoto")
            continue
        fee = size * CFG["FEE_PCT"] / 100
        net = size - fee
        impact = net / (m["liq"] / 2 + net)
        fill = m["price"] * (1 + impact)
        st.cash -= size
        st.positions[mint] = {
            "symbol": m["symbol"], "cost": size, "tokens": net / fill, "entry_price": fill,
            "opened": time.time(), "peak_pct": 0.0, "last_price": m["price"], "liq": m["liq"],
            "last_ok": time.time(), "thesis": c["thesis"], "url": m["url"],
        }
        st.stats["buys"] += 1
        log(st, "Executor", "buy",
            f"COMPRA {m['symbol']} ${size:.2f} (slippage {impact * 100:.1f}%, fee ${fee:.2f}). "
            f"Motivo: {c['thesis']}", mint=mint, size=round(size, 2), url=m["url"])
        save_state(st)


# ------------------------------------------------------------------ Risk
def close_position(st, mint, reason, value=None):
    p = st.positions.pop(mint)
    val = pos_value(p) if value is None else value
    st.cash += val
    pnl = val - p["cost"]
    t = {
        "chiuso": datetime.now(TZ).strftime("%Y-%m-%d %H:%M"),
        "symbol": p["symbol"], "mint": mint, "cost": round(p["cost"], 2),
        "value": round(val, 2), "pnl": round(pnl, 2),
        "pnl_pct": round(pnl / p["cost"] * 100, 2) if p["cost"] else 0.0,
        "minutes": int((time.time() - p["opened"]) / 60),
        "peak_pct": round(p["peak_pct"], 2), "reason": reason,
        "thesis": p.get("thesis", ""), "url": p.get("url", ""),
    }
    st.closed.append(t)
    write_trade(t)
    st.cooldown[mint] = time.time() + CFG["COOLDOWN_MIN"] * 60
    log(st, "Risk", "sell", f"VENDE {p['symbol']} {t['pnl_pct']:+.1f}% (${pnl:+.2f}). Motivo: {reason}",
        mint=mint, pnl=t["pnl"])


async def risk_agent(st, session):
    while True:
        await asyncio.sleep(CFG["RISK_CHECK_SEC"])
        if st.positions:
            market = await dex_batch(session, list(st.positions.keys()))
            for mint, p in list(st.positions.items()):
                if mint in market:
                    m = summarize(market[mint])
                    if m["price"] > 0:
                        p["last_price"] = m["price"]
                        p["liq"] = max(m["liq"], 1.0)
                        p["last_ok"] = time.time()
                pnl = pos_pnl_pct(p)
                p["peak_pct"] = max(p["peak_pct"], pnl)
                held = (time.time() - p["opened"]) / 60
                if time.time() - p["last_ok"] > 900:
                    close_position(st, mint, "nessun prezzo da 15 min, valutato zero per prudenza", 0.0)
                elif pnl <= CFG["STOP_LOSS_PCT"]:
                    close_position(st, mint, f"stop loss a {pnl:.1f}%")
                elif (p["peak_pct"] >= CFG["TRAIL_ACTIVATE_PCT"]
                      and pnl <= p["peak_pct"] - CFG["TRAIL_DISTANCE_PCT"]):
                    close_position(st, mint, f"trailing stop: massimo {p['peak_pct']:+.1f}%, ora {pnl:+.1f}%")
                elif held > CFG["MAX_HOLD_MIN"]:
                    close_position(st, mint, f"tempo massimo {CFG['MAX_HOLD_MIN']} min")
        eq = equity(st)
        st.equity_hist.append((time.time(), eq))
        with open(os.path.join(DATA_DIR, "equity.csv"), "a") as f:
            f.write(f"{datetime.now(TZ).isoformat(timespec='seconds')},{eq:.2f}\n")
        day_pct = (eq / st.period_start_equity - 1) * 100 if st.period_start_equity else 0.0
        if not st.paused and day_pct <= CFG["DAILY_LOSS_LIMIT_PCT"]:
            st.paused = True
            log(st, "Risk", "pause", f"perdita {day_pct:.1f}%: nuovi acquisti bloccati fino al recap")
        save_state(st)


# ------------------------------------------------------------------ Recap
def build_recap(st):
    eq = equity(st)
    start = st.period_start_equity or CFG["START_BALANCE"]
    pnl = eq - start
    tr = st.closed
    wins = [t for t in tr if t["pnl"] > 0]
    since = datetime.fromtimestamp(st.period_start_ts, TZ).strftime("%d/%m %H:%M")
    lines = [
        "RECAP DESK - PAPER, nessun soldo vero",
        f"Periodo: dal {since} al {datetime.now(TZ):%d/%m %H:%M}",
        f"Equity: ${start:,.2f} -> ${eq:,.2f} ({pnl:+,.2f}, {pnl / start * 100:+.1f}%)",
        f"Liquidita: ${st.cash:,.2f} | Posizioni aperte: {len(st.positions)}",
        f"Token visti: {st.stats.get('seen', 0)} | Volume ok: {st.stats.get('volume_ok', 0)} | "
        f"Filtro superato: {st.stats.get('filter_pass', 0)} | Acquisti: {st.stats.get('buys', 0)}",
        f"Trade chiusi: {len(tr)} | Vinti: {len(wins)} | Persi: {len(tr) - len(wins)}"
        + (f" | Win rate: {len(wins) / len(tr) * 100:.0f}%" if tr else ""),
    ]
    if tr:
        best = max(tr, key=lambda t: t["pnl_pct"])
        worst = min(tr, key=lambda t: t["pnl_pct"])
        lines += ["", f"Migliore: {best['symbol']} {best['pnl_pct']:+.1f}% ({best['reason']})",
                  f"Peggiore: {worst['symbol']} {worst['pnl_pct']:+.1f}% ({worst['reason']})",
                  "", "Trade chiusi:"]
        lines += [f"- {t['symbol']}: {t['pnl_pct']:+.1f}% (${t['pnl']:+.2f}) in {t['minutes']} min. "
                  f"Entrata: {t['thesis']}. Uscita: {t['reason']}" for t in tr]
    if st.positions:
        lines += ["", "Ancora aperte:"]
        lines += [f"- {p['symbol']}: {pos_pnl_pct(p):+.1f}%" for p in st.positions.values()]
    if st.reject_reasons:
        lines += ["", "Motivi di scarto piu frequenti:"]
        lines += [f"- {r}: {n}" for r, n in st.reject_reasons.most_common(10)]
    return "\n".join(lines)


async def recap_agent(st):
    while True:
        now = datetime.now(TZ)
        target = now.replace(hour=CFG["RECAP_HOUR"], minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        path = os.path.join(DATA_DIR, f"recap_{datetime.now(TZ):%Y-%m-%d}.txt")
        with open(path, "w") as f:
            f.write(build_recap(st) + "\n")
        log(st, "Logger", "recap", f"recap salvato in {os.path.basename(path)}")
        st.period_start_ts = time.time()
        st.period_start_equity = equity(st)
        st.closed = []
        st.stats = Counter()
        st.reject_reasons = Counter()
        st.paused = False
        save_state(st)


# ------------------------------------------------------------------ terminale
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
    head = Text()
    head.append(" PAPER ", style="bold black on yellow")
    head.append(f"  Equity ${eq:,.2f}  ", style="bold")
    head.append(f"{pnl:+,.2f} ({pnl / start * 100:+.1f}%)\n", style=color)
    head.append(f"Liquidita ${st.cash:,.2f} | Posizioni {len(st.positions)}/{CFG['MAX_POSITIONS']}\n")
    head.append(f"Visti {st.stats.get('seen', 0)} | In osservazione {len(st.watch)} | "
                f"Filtro ok {st.stats.get('filter_pass', 0)} | Scartati {st.stats.get('rejected', 0)}")
    if st.paused:
        head.append("\nPAUSA: limite di perdita raggiunto", style="bold red")

    tbl = Table(expand=True, show_edge=False, pad_edge=False)
    for col in ("Token", "PnL", "Max", "Min"):
        tbl.add_column(col)
    for p in st.positions.values():
        pp = pos_pnl_pct(p)
        tbl.add_row(p["symbol"][:10], Text(f"{pp:+.1f}%", style="green" if pp >= 0 else "red"),
                    f"{p['peak_pct']:+.1f}%", str(int((time.time() - p["opened"]) / 60)))
    if not st.positions:
        tbl.add_row("nessuna", "", "", "")

    spark = sparkline([e for _, e in st.equity_hist])
    acts = Text("\n".join(st.activity) if st.activity else "in attesa dei primi dati...")
    return Group(
        Panel(head, title="Desk", border_style="cyan"),
        Panel(tbl, title="Posizioni"),
        Panel(Text(spark or "dati in arrivo", style=color), title="Equity"),
        Panel(acts, title="Agenti"),
    )


async def dashboard(st):
    with Live(render(st), refresh_per_second=1) as live:
        while True:
            live.update(render(st))
            await asyncio.sleep(2)


# ------------------------------------------------------------------ avvio
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
    log(st, "Logger", "info", "avvio in modalita PAPER, nessun ordine reale")
    q_an, q_ex = asyncio.Queue(), asyncio.Queue()
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout,
                                     headers={"User-Agent": "Mozilla/5.0 paper-desk"}) as s:
        jobs = {
            "Scout pump.fun": lambda: scout_pumpportal(st),
            "Scout pool": lambda: scout_gecko(st, s),
            "Filter": lambda: filter_agent(st, s, q_an),
            "Analyst": lambda: analyst_agent(st, q_an, q_ex),
            "Executor": lambda: executor_agent(st, q_ex),
            "Risk": lambda: risk_agent(st, s),
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
