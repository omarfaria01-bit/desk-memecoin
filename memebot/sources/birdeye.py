"""
birdeye.py - v8.4: Birdeye Data (piano Standard GRATUITO) come fonte dati AGGIUNTIVA. PAPER, sola lettura.

Chiave: SOLO da os.environ["BIRDEYE_API_KEY"]. Non viene mai scritta su file, log, stato o export; ogni testo
che esce da questo modulo passa da mask(). Se la chiave manca il modulo resta spento e il bot usa le fonti di prima.

Piano Standard (documentazione data.birdeye.so, controllata il 30/10/2026):
  - 30.000 CU al mese, 1 richiesta al secondo per account, nessun overage (oltre il limite si viene sospesi).
  - Endpoint usati (tutti inclusi nello Standard):
      /defi/ohlcv          candele 1m, <=100 candele = 25 CU   -> ATR per dyn stop / dyn TP
      /defi/token_trending mode=selective interval=1h, 30 CU   -> fonte trending "birdeye"
      /defi/token_overview 18 CU                               -> controllo incrociato (liquidita, holder,
                                                                  acquirenti unici, vendite) prima dell'ingresso
      /utils/v1/credits    1 CU                                -> consumo VERO del ciclo (contatore ufficiale)
  - /defi/token_security (top10, mint/freeze authority) NON e incluso nello Standard: non usato.

Budget: il residuo del ciclo (da /utils/v1/credits, meno una riserva) viene diviso per i giorni che mancano a fine
ciclo -> budget del giorno, ripartito per uso (ATR, trending, overview). Vicino al limite si degrada: prima solo le
chiamate prioritarie (trade paper veri), poi nessuna chiamata (il bot torna a GeckoTerminal / tick / DexScreener).
Mai eccezioni verso il chiamante: in caso di dubbio ritorna None.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import time
from datetime import datetime
from pathlib import Path

BASE = "https://public-api.birdeye.so"
STATE_FILE = Path(__file__).with_name("birdeye_usage.json")
COST = {"ohlcv": 25, "trending": 30, "overview": 18, "credits": 1}
DEFAULTS = {
    "BIRDEYE_ENABLED": True,
    "BIRDEYE_MONTHLY_CU": 30000,      # piano Standard
    "BIRDEYE_RESERVE_CU": 1500,       # mai sotto questa riserva (5%)
    "BIRDEYE_MIN_GAP_SEC": 1.2,       # limite piano: 1 rps per account
    "BIRDEYE_SHARE_ATR": 0.5,         # quote del budget giornaliero per uso (le prioritarie possono sforare la quota)
    "BIRDEYE_SHARE_TREND": 0.2,
    "BIRDEYE_SHARE_OVERVIEW": 0.3,
    "BIRDEYE_SAVE_AT": 0.8,           # oltre l'80% del budget del giorno: solo chiamate prioritarie
    "BIRDEYE_TREND_SEC": 14400,       # trending ogni 4 ore (6 x 30 = 180 CU/giorno)
    "BIRDEYE_CREDITS_SEC": 3600,      # consumo ufficiale ogni ora (24 CU/giorno)
    "BIRDEYE_OHLCV_BARS": 60,         # 60 candele 1m = 25 CU (<=100)
    "BIRDEYE_CYCLE_SAVE_AT": 0.80,    # v8.4: dall'80% del ciclo: niente trending e niente chiamate non prioritarie
    "BIRDEYE_CYCLE_STOP_AT": 0.95,    # dal 95% del ciclo: nessuna chiamata (solo fonti gratuite di fallback)
}


def _f(x, d=0.0):
    try:
        v = float(x)
        return v if math.isfinite(v) else d
    except (TypeError, ValueError):
        return d


def mask(text):
    """Toglie la chiave (e qualsiasi header X-API-KEY) da un testo prima di loggarlo."""
    s = str(text)
    k = os.environ.get("BIRDEYE_API_KEY") or ""
    if len(k) >= 6:
        s = s.replace(k, "***")
    low = s.lower()
    i = low.find("x-api-key")
    while i >= 0:
        j = i + len("x-api-key")
        end = j
        while end < len(s) and s[end] in " :='\"":
            end += 1
        stop = end
        while stop < len(s) and s[stop] not in " ,'\"}&\n":
            stop += 1
        s = s[:end] + "***" + s[stop:]
        low = s.lower()
        i = low.find("x-api-key", end + 3)
    return s


class Birdeye:
    """Client con limitatore (1 rps), contatore CU e degrado. `cfg` = dizionario di configurazione del bot."""

    def __init__(self, session=None, cfg=None, logf=None, state_file=STATE_FILE, now=None):
        self.s = session
        self.cfg = cfg if cfg is not None else {}
        self.logf = logf or (lambda msg: None)
        self.state_file = Path(state_file)
        self.lock = asyncio.Lock()
        self.last_call = 0.0
        self.cache = {}                 # (tipo, chiave) -> (ts, valore)
        self.cool_until = 0.0           # 429: pausa
        self.off_until = 0.0            # 401/403: chiave rifiutata, pausa lunga
        self.denied = {}                # endpoint -> fino a quando (403 "non incluso nel piano")
        self.level = "ok"
        self.st = self._load(now or time.time())

    # ------------------------------------------------------------------ config e stato
    def c(self, k):
        v = self.cfg.get(k)
        return DEFAULTS[k] if v is None else v

    @staticmethod
    def has_key():
        return bool(os.environ.get("BIRDEYE_API_KEY"))

    def _day(self, now):
        return datetime.fromtimestamp(now).strftime("%Y-%m-%d")

    def _load(self, now):
        st = {}
        try:
            st = json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            st = {}
        st.setdefault("remaining", None)          # ufficiale (ultimo /utils/v1/credits)
        st.setdefault("remaining_ts", 0)
        st.setdefault("since_sync", 0)            # CU stimati spesi dopo l'ultimo controllo ufficiale
        st.setdefault("cycle_end", 0)
        st.setdefault("day", self._day(now))
        st.setdefault("day_budget", None)
        st.setdefault("spent_day", {})
        st.setdefault("calls_day", {})
        st.setdefault("fallback_day", {})
        st.setdefault("errors_day", {})
        st.setdefault("history", [])              # [(giorno, cu stimati)] ultimi 14 giorni
        return st

    def save(self):
        try:
            self.state_file.write_text(json.dumps(self.st, separators=(",", ":")))
        except OSError:
            pass

    def remaining_est(self):
        r = self.st.get("remaining")
        if r is None:
            r = self.c("BIRDEYE_MONTHLY_CU")
        return _f(r) - _f(self.st.get("since_sync"))

    def _roll_day(self, now):
        d = self._day(now)
        if self.st["day"] != d:
            tot = sum(self.st["spent_day"].values())
            self.st["history"] = (self.st["history"] + [[self.st["day"], tot]])[-14:]
            self.st.update(day=d, day_budget=None, spent_day={}, calls_day={}, fallback_day={}, errors_day={})
        if self.st.get("day_budget") is None:
            end = _f(self.st.get("cycle_end"))
            days_left = max(1.0, (end - now) / 86400) if end > now else 30.0
            left = max(0.0, self.remaining_est() - self.c("BIRDEYE_RESERVE_CU"))
            self.st["day_budget"] = round(left / days_left, 1)

    def spent_today(self):
        return sum(self.st["spent_day"].values())

    def cycle_used(self):
        return max(0.0, self.c("BIRDEYE_MONTHLY_CU") - self.remaining_est())

    def cycle_pct(self):
        return self.cycle_used() / max(1.0, float(self.c("BIRDEYE_MONTHLY_CU")))

    def cycle_level(self):
        p = self.cycle_pct()
        return "stop" if p >= self.c("BIRDEYE_CYCLE_STOP_AT") else (
            "risparmio" if p >= self.c("BIRDEYE_CYCLE_SAVE_AT") else "ok")

    def days_left(self, now=None):
        now = now or time.time()
        end = _f(self.st.get("cycle_end"))
        return max(1, int(math.ceil((end - now) / 86400))) if end > now else 30

    def projection(self, now=None):
        """CU a fine ciclo se si continua al ritmo di oggi (stima)."""
        now = now or time.time()
        d = datetime.fromtimestamp(now)
        frac = max(0.1, (d.hour * 3600 + d.minute * 60 + d.second) / 86400)
        rate = self.spent_today() / frac
        return round(self.cycle_used() + rate * (1 - frac) + rate * max(0, self.days_left(now) - 1))

    def allow(self, cat, prio=False, now=None):
        """(True, '') oppure (False, motivo). Non fa chiamate."""
        now = now or time.time()
        if not self.c("BIRDEYE_ENABLED") or not self.has_key():
            return False, "spento" if self.has_key() else "chiave assente"
        if self.s is None:
            return False, "nessuna sessione"
        self._roll_day(now)
        if now < self.off_until:
            return False, "chiave rifiutata (pausa)"
        if now < self.cool_until:
            return False, "429 (pausa)"
        ep = {"ohlcv": "/defi/ohlcv", "trending": "/defi/token_trending", "overview": "/defi/token_overview"}.get(cat)
        if ep and now < self.denied.get(ep, 0):
            return False, "endpoint non incluso nel piano"
        cost = COST.get(cat, 30)
        cl = self.cycle_level()   # v8.4: soglie del ciclo mensile (mai oltre il piano gratuito)
        if cat != "credits" and cl == "stop":
            self._set_level("stop", "95% del ciclo mensile")
            return False, "95% del ciclo"
        if cat == "trending" and cl == "risparmio":
            return False, "80% del ciclo (trending sospeso)"
        if cat != "credits" and cl == "risparmio" and not prio:
            return False, "80% del ciclo (solo prioritarie)"
        if self.remaining_est() - cost < self.c("BIRDEYE_RESERVE_CU"):
            self._set_level("stop", "riserva mensile raggiunta")
            return False, "riserva mensile"
        budget = _f(self.st.get("day_budget"))
        spent = self.spent_today()
        if cat != "credits" and spent + cost > budget:
            self._set_level("stop", "budget del giorno finito")
            return False, "budget del giorno"
        if cat == "credits":
            return True, ""
        if spent + cost > self.c("BIRDEYE_SAVE_AT") * budget:
            self._set_level("risparmio", "budget del giorno quasi finito")
            if not prio:
                return False, "risparmio (solo prioritarie)"
        else:
            self._set_level("ok", "")
        share = {"ohlcv": "BIRDEYE_SHARE_ATR", "trending": "BIRDEYE_SHARE_TREND",
                 "overview": "BIRDEYE_SHARE_OVERVIEW"}.get(cat)
        if share and not prio and _f(self.st["spent_day"].get(cat)) + cost > self.c(share) * budget:
            return False, f"quota {cat} del giorno"
        return True, ""

    def _set_level(self, lvl, why):
        if lvl != self.level:
            self.level = lvl
            self.logf(mask(f"Birdeye: livello {lvl}" + (f" ({why})" if why else "")
                           + f"; oggi {self.spent_today():.0f}/{_f(self.st.get('day_budget')):.0f} CU, "
                           f"residuo ciclo ~{self.remaining_est():.0f} CU"))

    def note_fallback(self, cat, why):
        k = f"{cat}:{why}"
        self.st["fallback_day"][k] = self.st["fallback_day"].get(k, 0) + 1

    # ------------------------------------------------------------------ HTTP
    async def _get(self, path, params, cat):
        key = os.environ.get("BIRDEYE_API_KEY") or ""
        hdr = {"X-API-KEY": key, "x-chain": "solana", "accept": "application/json"}
        async with self.lock:
            dt = time.time() - self.last_call
            if dt < self.c("BIRDEYE_MIN_GAP_SEC"):
                await asyncio.sleep(self.c("BIRDEYE_MIN_GAP_SEC") - dt)
            self.last_call = time.time()
            status, data = 0, None
            try:
                async with self.s.get(BASE + path, params=params, headers=hdr) as r:
                    status = r.status
                    if status == 200:
                        data = await r.json(content_type=None)
            except Exception as e:  # noqa: BLE001
                self.st["errors_day"]["rete"] = self.st["errors_day"].get("rete", 0) + 1
                self.logf(mask(f"Birdeye: errore di rete su {path} ({type(e).__name__})"))
                return None
        now = time.time()
        if status == 429:
            self.cool_until = now + 120
            self.st["errors_day"]["429"] = self.st["errors_day"].get("429", 0) + 1
            self.logf(f"Birdeye: 429 su {path}, pausa 2 minuti (fallback alle fonti di prima)")
            return None
        if status in (401, 403):
            self.st["errors_day"][str(status)] = self.st["errors_day"].get(str(status), 0) + 1
            if path == "/utils/v1/credits" or status == 401:
                self.off_until = now + 3600
                self.logf(f"Birdeye: chiave rifiutata (HTTP {status}), spento per 1 ora")
            else:
                self.denied[path] = now + 6 * 3600
                self.logf(f"Birdeye: {path} non disponibile nel piano (HTTP {status}), saltato per 6 ore")
            return None
        # CU contati per ogni risposta non 429 (stima prudente: anche gli errori 4xx/5xx)
        cost = COST.get(cat, 30)
        self.st["spent_day"][cat] = self.st["spent_day"].get(cat, 0) + cost
        self.st["calls_day"][cat] = self.st["calls_day"].get(cat, 0) + 1
        self.st["since_sync"] = _f(self.st.get("since_sync")) + cost
        if status != 200 or not isinstance(data, dict) or data.get("success") is False:
            self.st["errors_day"][str(status)] = self.st["errors_day"].get(str(status), 0) + 1
            self.save()
            return None
        self.save()
        return data.get("data")

    async def sync_credits(self, force=False, now=None):
        """Consumo ufficiale del ciclo (1 CU), al massimo ogni BIRDEYE_CREDITS_SEC."""
        now = now or time.time()
        if not force and now - _f(self.st.get("remaining_ts")) < self.c("BIRDEYE_CREDITS_SEC"):
            return None
        ok, _ = self.allow("credits", now=now)
        if not ok:
            return None
        d = await self._get("/utils/v1/credits", None, "credits")
        if not isinstance(d, dict):
            return None
        rem = _f(((d.get("remaining") or {}).get("total")), None)
        if rem is None:
            return None
        first = self.st.get("remaining") is None
        self.st.update(remaining=rem, remaining_ts=now, since_sync=0, cycle_end=_f(d.get("end_time")),
                       cycle_start=_f(d.get("start_time")),
                       used_cycle=_f(((d.get("usage") or {}).get("total"))))
        if first:
            self.st["day_budget"] = None     # ricalcolo con il residuo vero
        self._roll_day(now)
        self.save()
        return d

    # ------------------------------------------------------------------ dati
    def _cached(self, kind, key, ttl, now):
        e = self.cache.get((kind, key))
        return e[1] if e and now - e[0] <= ttl else None

    async def ohlcv_1m(self, mint, prio=False, now=None):
        """Ultime BIRDEYE_OHLCV_BARS candele 1m: [(t, o, h, l, c, v_usd)] ordinate, oppure None."""
        now = now or time.time()
        c = self._cached("ohlcv", mint, 90, now)
        if c is not None:
            return c
        ok, why = self.allow("ohlcv", prio, now)
        if not ok:
            self.note_fallback("ohlcv", why)
            return None
        await self.sync_credits(now=now)
        to = int(now)
        frm = to - 60 * int(self.c("BIRDEYE_OHLCV_BARS"))
        d = await self._get("/defi/ohlcv", {"address": mint, "type": "1m", "currency": "usd",
                                            "time_from": frm, "time_to": to}, "ohlcv")
        items = (d or {}).get("items") if isinstance(d, dict) else None
        if not items:
            self.note_fallback("ohlcv", "vuoto")
            return None
        rows = []
        for x in items:
            if not isinstance(x, dict):
                continue
            r = (_f(x.get("unixTime")), _f(x.get("o")), _f(x.get("h")), _f(x.get("l")), _f(x.get("c")),
                 _f(x.get("vUsd"), _f(x.get("v"))))
            if r[0] > 0 and r[4] > 0:
                rows.append(r)
        rows.sort(key=lambda r: r[0])
        self.cache[("ohlcv", mint)] = (now, rows)
        return rows or None

    async def trending(self, now=None):
        """Lista trending Birdeye (mode=selective, 1h): [(mint, rank, extra)]. Usa la cache se non e ora o se il
        budget non lo permette (cache valida fino a 2 x BIRDEYE_TREND_SEC)."""
        now = now or time.time()
        every = self.c("BIRDEYE_TREND_SEC")
        c = self.cache.get(("trend", ""))
        if c and now - c[0] < every:
            return c[1]
        ok, why = self.allow("trending", False, now)
        if not ok:
            self.note_fallback("trending", why)
            return c[1] if c and now - c[0] < 2 * every else []
        await self.sync_credits(now=now)
        d = await self._get("/defi/token_trending", {"sort_by": "rank", "sort_type": "asc", "offset": 0,
                                                     "limit": 20, "mode": "selective", "interval": "1h"}, "trending")
        toks = (d or {}).get("tokens") if isinstance(d, dict) else None
        if not toks:
            return c[1] if c and now - c[0] < 2 * every else []
        out, seen = [], set()
        for i, t in enumerate(toks):
            m = (t or {}).get("address")
            if not m or m in seen:
                continue
            seen.add(m)
            out.append((m, int(_f(t.get("rank"), i + 1)), {"symbol": t.get("symbol"), "liq": _f(t.get("liquidity")),
                                                           "mcap": _f(t.get("marketcap"))}))
        self.cache[("trend", "")] = (now, out)
        return out

    async def overview(self, mint, prio=False, now=None):
        """Sottoinsieme di /defi/token_overview utile ai controlli incrociati, oppure None."""
        now = now or time.time()
        c = self._cached("ov", mint, 600, now)
        if c is not None:
            return c
        ok, why = self.allow("overview", prio, now)
        if not ok:
            self.note_fallback("overview", why)
            return None
        await self.sync_credits(now=now)
        d = await self._get("/defi/token_overview", {"address": mint}, "overview")
        if not isinstance(d, dict) or not d:
            return None
        keys = ("liquidity", "holder", "marketCap", "fdv", "price", "uniqueBuyer5m", "uniqueSeller5m",
                "uniqueWallet1h", "buy5m", "sell5m", "buy1h", "sell1h", "v5mUSD", "v1hUSD",
                "priceChange5mPercent", "priceChange1hPercent", "lastTradeUnixTime", "numberMarkets")
        ov = {k: d.get(k) for k in keys}
        self.cache[("ov", mint)] = (now, ov)
        return ov

    def status(self):
        self._roll_day(time.time())
        return {"level": self.level, "cycle_level": self.cycle_level(), "cycle_pct": round(self.cycle_pct() * 100, 2),
                "cycle_used": round(self.cycle_used()), "limit": self.c("BIRDEYE_MONTHLY_CU"),
                "projection": self.projection(), "days_left": self.days_left(),
                "spent_today": self.spent_today(), "day_budget": self.st.get("day_budget"),
                "remaining_est": round(self.remaining_est()), "calls": dict(self.st["calls_day"]),
                "fallback": dict(self.st["fallback_day"]), "errors": dict(self.st["errors_day"])}


def xcheck(ov, m, min_liq=8000.0, min_buyers=5, min_holders=100, liq_ratio=0.5, now=None):
    """Controllo incrociato Birdeye vs DexScreener/prefiltro/honeypot. Ritorna lista di flag (vuota = coerente).
    Non sostituisce MAI i filtri duri: serve solo a registrare (shadow) o ridurre la size (soft)."""
    now = now or time.time()
    if not isinstance(ov, dict):
        return []
    fl = []
    bl = _f(ov.get("liquidity"), None)
    dl = _f((m or {}).get("liq"), None)
    if bl is not None:
        if bl < min_liq:
            fl.append("be_liq_min")
        if dl and dl > 0 and bl < liq_ratio * dl:
            fl.append("be_liq_mismatch")
    h = _f(ov.get("holder"), None)
    if h is not None and h < min_holders:
        fl.append("be_holders_low")
    ub = _f(ov.get("uniqueBuyer5m"), None)
    if ub is not None and ub < min_buyers:
        fl.append("be_buyers_low")
    b1, s1 = _f(ov.get("buy1h"), 0), _f(ov.get("sell1h"), 0)
    if b1 >= 20 and s1 == 0:
        fl.append("be_no_sells")           # tanti acquisti e nessuna vendita: segnale honeypot
    lt = _f(ov.get("lastTradeUnixTime"), None)
    if lt and now - lt > 600:
        fl.append("be_stale")
    return fl
