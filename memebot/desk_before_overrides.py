#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DESK MEMECOIN SOLANA v2

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
    "MAX_TRADE_PCT": 0.10,            # massimo 10% dell'equity per operazione
    "MIN_TRADE_PCT": 0.03,            # minimo quando la fiducia e bassa
    "MAX_POSITIONS": 20,              # nessun limite di trade, solo di posizioni aperte insieme
    "MAX_POOL_SHARE_PCT": 2.0,        # mai piu del 2% della liquidita della pool
    # uscite
    "STOP_LOSS_PCT": -8.0,            # confermato su due controlli di fila
    "CRASH_EXIT_PCT": -15.0,          # uscita immediata
    "TAKE_PROFIT_PCT": 100.0,         # a +100% vende una parte...
    "TAKE_PROFIT_FRACTION": 0.5,      # ...la meta: capitale recuperato
    "TRAIL_ACTIVATE_PCT": 15.0,
    "TRAIL_DISTANCE_PCT": 10.0,
    "MAX_HOLD_MIN": 360,
    "LIQ_DROP_EXIT_PCT": 50.0,        # liquidita dimezzata = possibile rug, esce
    "DAILY_LOSS_LIMIT_PCT": -20.0,
    "FEE_PCT": 1.0,                   # commissioni simulate per lato (paper)
    # Filter: sicurezza e distribuzione
    "MIN_VOLUME_H1": 40000.0,
    "MIN_LIQUIDITY_USD": 15000.0,
    "MAX_TOP10_PCT": 25.0,
    "MIN_LP_LOCKED_PCT": 90.0,
    "MIN_HOLDERS": 150,
    "MAX_TRANSFER_FEE_PCT": 5.0,
    "MAX_INSIDER_PCT": 10.0,          # quota dei wallet segnalati come insider
    "MAX_IDENTICAL_WALLETS": 4,       # la firma della distribuzione coordinata
    "MAX_CREATOR_PCT": 5.0,
    "REJECT_INSIDER_NETWORKS": True,
    # Analyst: momento e grafico
    "REQUIRE_CHART": True,
    "RSI_MIN": 50.0,
    "RSI_MAX": 78.0,
    "MAX_CHANGE_H1_PCT": 200.0,
    "MIN_CHANGE_H1_PCT": -10.0,
    "MIN_CHANGE_M5_PCT": -5.0,
    # apprendimento
    "SCORE_THRESHOLD": 0.60,
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
HOST_GAPS = {"api.dexscreener.com": 0.25, "api.geckoterminal.com": 2.2, "api.rugcheck.xyz": 1.2}
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
        self.model = Model()
        self.threshold = CFG["SCORE_THRESHOLD"]
        self.outcomes = deque(maxlen=20)
        self.lessons = deque(maxlen=30)
        self.blacklist = set()
        self.equity_hist = deque(maxlen=3000)
        # non salvati
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
            "model": self.model.to_json(), "threshold": self.threshold,
            "outcomes": list(self.outcomes), "lessons": list(self.lessons),
            "blacklist": sorted(self.blacklist), "equity_hist": list(self.equity_hist)[-1000:],
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
    st.outcomes.extend(d.get("outcomes", []))
    st.lessons.extend(d.get("lessons", []))
    st.blacklist = set(d.get("blacklist", []))
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
    st.equity_hist.extend(tuple(x) for x in d.get("equity_hist", []))
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
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(t.keys()))
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
    if m["dex"] != "pumpfun":
        locked = [fnum((mk.get("lp") or {}).get("lpLockedPct")) for mk in markets if mk.get("lp")]
        if not locked:
            return False, "LP non verificabile", "LP non verificabile", None
        if max(locked) < CFG["MIN_LP_LOCKED_PCT"]:
            return False, f"LP bloccata solo al {max(locked):.0f}%", "LP non bloccata", None

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
    pcts = [fnum(h.get("pct")) for h in holders]
    if max(pcts) <= 1.0 and sum(pcts) <= 1.0:
        pcts = [p * 100 for p in pcts]
    top10 = sum(sorted(pcts, reverse=True)[:10])
    if top10 > CFG["MAX_TOP10_PCT"]:
        return False, f"top 10 holder al {top10:.1f}%", "top 10 troppo alti", None

    # la lezione di Pangea: tanti wallet con la stessa identica quota
    same = Counter(round(p, 2) for p in pcts if p >= 0.1)
    if same:
        val, n = same.most_common(1)[0]
        if n >= CFG["MAX_IDENTICAL_WALLETS"]:
            return False, f"{n} wallet con la stessa quota ({val}%): distribuzione coordinata", \
                "wallet identici", None
    insider_pct = sum(p for h, p in zip(holders, pcts) if h.get("insider"))
    if insider_pct > CFG["MAX_INSIDER_PCT"]:
        return False, f"insider al {insider_pct:.1f}%", "insider", None
    if CFG["REJECT_INSIDER_NETWORKS"] and fnum(r.get("graphInsidersDetected")) > 0:
        return False, "reti di wallet collegati (bundle)", "insider/bundle", None
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
    dangers = [x.get("name", "?") for x in (r.get("risks") or []) if str(x.get("level", "")).lower() == "danger"]
    if dangers:
        return False, "rischi gravi: " + ", ".join(dangers[:3]), "rischi gravi RugCheck", None
    sec = {"top10": round(top10, 2), "holders": total_holders or len(holders), "creator": creator,
           "insider_pct": round(insider_pct, 2)}
    return True, f"top 10 al {top10:.1f}%, {sec['holders']} holder, LP ok", None, sec


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
                if m["vol_h1"] < CFG["MIN_VOLUME_H1"]:
                    continue
                st.watch.pop(mint, None)
                st.processed[mint] = now
                st.stats["volume_ok"] += 1
                for word in words_of(m["name"], m["symbol"]):
                    st.hot.append((now, word, mint))
                if m["price"] <= 0 or m["liq"] < CFG["MIN_LIQUIDITY_USD"]:
                    reject(st, mint, m["symbol"], f"liquidita ${m['liq']:,.0f} troppo bassa", cat="liquidita bassa")
                    continue
                ok, reason, cat, sec = await security_check(st, http, mint, m, cache)
                if not ok:
                    reject(st, mint, m["symbol"], reason, cat=cat)
                    continue
                st.stats["filter_pass"] += 1
                log(st, "Filter", "pass", f"{m['symbol']} superato: {reason}", mint=mint)
                await q_out.put({"mint": mint, "m": m, "sec": sec, "filter_ok": True})
            except Exception as e:  # noqa: BLE001
                log(st, "Filter", "error", f"errore su un token ({type(e).__name__}), continuo")


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
            if not c.get("filter_ok"):
                log(st, "Analyst", "block", f"{m['symbol']} bloccato: non arriva dal Filter")
                continue
            why = []
            if m["chg_h1"] > CFG["MAX_CHANGE_H1_PCT"]:
                why.append(f"gia +{m['chg_h1']:.0f}% in 1h")
            if m["chg_h1"] < CFG["MIN_CHANGE_H1_PCT"]:
                why.append(f"{m['chg_h1']:.0f}% in 1h")
            if m["chg_m5"] < CFG["MIN_CHANGE_M5_PCT"]:
                why.append(f"{m['chg_m5']:.0f}% in 5 min")
            if why:
                reject(st, mint, m["symbol"], "momento sbagliato: " + "; ".join(why), agent="Analyst",
                       cat="momento sbagliato")
                add_shadow(st, mint, m, "momento")
                continue
            candles = await fetch_candles(http, m["pair"])
            ch = analyze_chart(candles) if candles else None
            if ch is None and CFG["REQUIRE_CHART"]:
                reject(st, mint, m["symbol"], "grafico non disponibile", agent="Analyst", cat="grafico assente")
                add_shadow(st, mint, m, "grafico")
                continue
            f = build_features(st, mint, m, c["sec"], ch)
            score, rule, prob = final_score(st, f)
            detail = f"punteggio {score:.2f} (soglia {st.threshold:.2f})"
            if score < st.threshold:
                reject(st, mint, m["symbol"], detail, agent="Analyst", cat="punteggio basso")
                add_shadow(st, mint, m, "punteggio", f)
                continue
            tags = [k for k in ("trend", "macd", "rsi", "trending", "narrative") if f[k] >= 1.0]
            rsi_txt = f"RSI {ch['rsi']:.0f}" if ch and ch["rsi"] is not None else "RSI n/d"
            c.update({"analyst_ok": True, "features": f, "score": score,
                      "thesis": f"{detail}, {rsi_txt}, acq/vend {m['buys_h1']}/{m['sells_h1']}, "
                                f"vol ${m['vol_h1']:,.0f}, segnali: {', '.join(tags) or 'nessuno forte'}"})
            log(st, "Analyst", "approve", f"{m['symbol']} approvato: {c['thesis']}", mint=mint)
            await q_out.put(c)
        except Exception as e:  # noqa: BLE001
            log(st, "Analyst", "error", f"errore su un token ({type(e).__name__}), continuo")


# ======================================================================= Executor (broker)
def mark_value(p):
    return p["tokens"] * p["last_price"] * (1 - CFG["FEE_PCT"] / 100)


def mark_pnl_pct(p):
    return (p["last_price"] / p["entry_mark"] - 1) * 100 if p["entry_mark"] > 0 else 0.0


def equity(st):
    return st.cash + sum(mark_value(p) for p in st.positions.values())


class PaperBroker:
    mode = "paper"

    async def setup(self, st):
        return None

    async def cash_usd(self, st):
        return st.cash

    async def buy(self, st, mint, m, size_usd):
        fee = size_usd * CFG["FEE_PCT"] / 100
        net = size_usd - fee
        impact = net / (max(m["liq"], 1.0) / 2 + net)
        fill = m["price"] * (1 + impact)
        if fill <= 0 or net <= 0:
            return {"ok": False, "error": "prezzo non valido"}
        st.cash -= size_usd
        return {"ok": True, "cost": size_usd, "tokens": net / fill, "fill": fill, "impact": impact, "fee": fee}

    async def sell(self, st, mint, p, fraction):
        tokens = p["tokens"] * fraction
        gross = tokens * p["last_price"]
        impact = gross / (max(p["liq"], 1.0) / 2 + gross) if gross > 0 else 0.0
        value = gross * (1 - impact) * (1 - CFG["FEE_PCT"] / 100)
        st.cash += value
        return {"ok": True, "value": value, "tokens": tokens}


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

    async def sell(self, st, mint, p, fraction):
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
            if not (c.get("filter_ok") and c.get("analyst_ok")):
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
    if mint in st.positions:
        return
    if len(st.positions) >= CFG["MAX_POSITIONS"]:
        reject(st, mint, m["symbol"], "troppe posizioni aperte", agent="Executor", cat="troppe posizioni")
        return
    eq = equity(st)
    conf = clamp((c["score"] - st.threshold) / max(1e-6, 1 - st.threshold))
    pct = CFG["MIN_TRADE_PCT"] + (CFG["MAX_TRADE_PCT"] - CFG["MIN_TRADE_PCT"]) * conf
    cash = await broker.cash_usd(st)
    size = min(eq * pct, eq * CFG["MAX_TRADE_PCT"], cash, m["liq"] * CFG["MAX_POOL_SHARE_PCT"] / 100)
    if size < 1:
        reject(st, mint, m["symbol"], "liquidita del conto insufficiente", agent="Executor", cat="conto vuoto")
        return
    if stop_active():  # FIX: STOP ricontrollato subito prima di eseguire l'ordine
        reject(st, mint, m["symbol"], "arresto di emergenza attivo", agent="Executor", cat="STOP")
        return
    res = await broker.buy(st, mint, m, size)
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
    }
    st.stats["buys"] += 1
    extra = f", tx {res['sig'][:10]}" if res.get("sig") else (f", {res['note']}" if res.get("note") else "")
    log(st, "Executor", "buy",
        f"COMPRA {m['symbol']} ${res['cost']:.2f} ({pct * 100:.1f}% equity, impatto "
        f"{res['impact'] * 100:.1f}%{extra}). {c['thesis']}", mint=mint, size=round(res["cost"], 2),
        url=m["url"])
    save_state(st, force=True)


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


async def sell_position(st, broker, mint, reason, fraction=1.0, zero=False):
    async with TRADE_LOCK:
        await _sell_position(st, broker, mint, reason, fraction, zero)


async def _sell_position(st, broker, mint, reason, fraction=1.0, zero=False):
    p = st.positions.get(mint)
    if not p:
        return
    res = {"ok": True, "value": 0.0, "tokens": p["tokens"] * fraction} if zero else \
        await broker.sell(st, mint, p, fraction)
    if not res.get("ok"):
        p["sell_fails"] = p.get("sell_fails", 0) + 1
        log(st, "Risk", "warn", f"vendita {p['symbol']} non riuscita ({res.get('error')}), riprovo")
        return
    p["realized"] += res["value"]
    p["tokens"] = max(0.0, p["tokens"] - res["tokens"])
    if fraction < 0.999 and p["tokens"] > 0:
        p["tp_done"] = True
        log(st, "Risk", "sell", f"VENDE {fraction * 100:.0f}% di {p['symbol']} per ${res['value']:.2f}: {reason}",
            mint=mint)
        save_state(st, force=True)
        return
    st.positions.pop(mint, None)
    pnl = p["realized"] - p["cost"]
    t = {"chiuso": datetime.now(TZ).strftime("%Y-%m-%d %H:%M"), "symbol": p["symbol"], "mint": mint,
         "cost": round(p["cost"], 2), "value": round(p["realized"], 2), "pnl": round(pnl, 2),
         "pnl_pct": round(pnl / p["cost"] * 100, 2) if p["cost"] else 0.0,
         "minutes": int((time.time() - p["opened"]) / 60), "peak_pct": round(p["peak_pct"], 2),
         "score": round(p.get("score", 0), 3), "reason": reason, "thesis": p.get("thesis", ""),
         "url": p.get("url", "")}
    st.closed.append(t)
    write_trade(t)
    st.cooldown[mint] = time.time() + CFG["COOLDOWN_MIN"] * 60
    log(st, "Risk", "sell", f"CHIUDE {p['symbol']} {t['pnl_pct']:+.1f}% (${pnl:+.2f}): {reason}", mint=mint,
        pnl=t["pnl"])
    learn_from_trade(st, p, pnl, reason)
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
            if st.positions:
                market = await dex_batch(http, list(st.positions.keys()))
                for mint, p in list(st.positions.items()):
                    try:
                        if mint in market:
                            m = summarize(market[mint])
                            if m["price"] > 0:
                                p["last_price"], p["liq"], p["last_ok"] = m["price"], max(m["liq"], 1.0), time.time()
                        pnl = mark_pnl_pct(p)
                        p["peak_pct"] = max(p["peak_pct"], pnl)
                        p["stop_hits"] = p["stop_hits"] + 1 if pnl <= CFG["STOP_LOSS_PCT"] else 0
                        held = (time.time() - p["opened"]) / 60
                        if stop_now or stop_active():
                            await sell_position(st, broker, mint, "arresto di emergenza")
                        elif time.time() - p["last_ok"] > 900:
                            await sell_position(st, broker, mint, "nessun prezzo da 15 min, possibile rug",
                                                zero=broker.mode == "paper")
                        elif p["liq_entry"] > 0 and p["liq"] < p["liq_entry"] * (1 - CFG["LIQ_DROP_EXIT_PCT"] / 100):
                            drop = (1 - p["liq"] / p["liq_entry"]) * 100
                            await sell_position(st, broker, mint, f"liquidita crollata del {drop:.0f}%")
                        elif pnl <= CFG["CRASH_EXIT_PCT"]:
                            await sell_position(st, broker, mint, f"crollo a {pnl:.1f}%")
                        elif p["stop_hits"] >= 2:
                            await sell_position(st, broker, mint, f"stop loss a {pnl:.1f}%")
                        elif not p["tp_done"] and pnl >= CFG["TAKE_PROFIT_PCT"]:
                            await sell_position(st, broker, mint, f"presa di profitto a {pnl:+.0f}%",
                                                fraction=CFG["TAKE_PROFIT_FRACTION"])
                        elif p["peak_pct"] >= CFG["TRAIL_ACTIVATE_PCT"] and pnl <= p["peak_pct"] - CFG["TRAIL_DISTANCE_PCT"]:
                            await sell_position(st, broker, mint,
                                                f"trailing: massimo {p['peak_pct']:+.1f}%, ora {pnl:+.1f}%")
                        elif held > CFG["MAX_HOLD_MIN"]:
                            await sell_position(st, broker, mint, f"tempo massimo {CFG['MAX_HOLD_MIN']} min")
                        elif time.time() - st.sec_checked.get(mint, 0) > CFG["RECHECK_SECURITY_MIN"] * 60:
                            st.sec_checked[mint] = time.time()
                            why = await recheck_security(st, http, mint, p)
                            if why:
                                await sell_position(st, broker, mint, why)
                    except Exception as e:  # noqa: BLE001
                        log(st, "Risk", "error", f"errore su {p.get('symbol')} ({type(e).__name__}), continuo")
            if broker.mode == "live" and time.time() - last_cash > 60:
                async with TRADE_LOCK:
                    st.cash = await broker.cash_usd(st)
                last_cash = time.time()
            eq = equity(st)
            st.equity_hist.append((time.time(), eq))
            with open(os.path.join(DATA_DIR, "equity.csv"), "a") as f:
                f.write(f"{datetime.now(TZ).isoformat(timespec='seconds')},{eq:.2f}\n")
            day_pct = (eq / st.period_start_equity - 1) * 100 if st.period_start_equity else 0.0
            if not st.paused and day_pct <= CFG["DAILY_LOSS_LIMIT_PCT"]:
                st.paused = True
                log(st, "Risk", "pause", f"perdita {day_pct:.1f}%: nuovi acquisti bloccati fino al recap")
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
        st.closed, st.stats, st.reject_reasons, st.paused = [], Counter(), Counter(), False
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
        broker = PaperBroker() if mode == "paper" else SolanaBroker(http, mode)
        try:
            await broker.setup(st)
        except Exception as e:  # noqa: BLE001
            print(f"Impossibile avviare in modalita {mode}: {e}")
            return
        log(st, "Logger", "info", f"avvio in modalita {mode.upper()}"
            + (" - SOLDI VERI" if mode == "live" else ", nessun ordine reale"))
        save_state(st, force=True)
        q_an, q_ex = asyncio.Queue(), asyncio.Queue()
        jobs = {
            "Scout pump.fun": lambda: scout_pumpportal(st),
            "Scout pool": lambda: scout_gecko(st, http),
            "Filter": lambda: filter_agent(st, http, q_an),
            "Analyst": lambda: analyst_agent(st, http, q_an, q_ex),
            "Executor": lambda: executor_agent(st, broker, q_ex),
            "Risk": lambda: risk_agent(st, http, broker),
            "Learner": lambda: shadow_agent(st, http),
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
