"""Client di sola lettura per l'API pubblica gratuita di Hyperliquid (/info).

Nessuna chiave, nessuna firma. Rate limit di Hyperliquid: 1200 di "peso" al minuto per IP
(l2Book/allMids peso 2, gli altri ~20, candleSnapshot +1 ogni 60 candele). Qui teniamo un
budget prudente (di default 400/min) e gestiamo 429/5xx con backoff.
"""
import json, time, threading, urllib.request, urllib.error

INFO_URL = "https://api.hyperliquid.xyz/info"

WEIGHTS = {"l2Book": 2, "allMids": 2, "clearinghouseState": 2, "orderStatus": 2}


class RateBudget:
    def __init__(self, per_min=400):
        self.per_min = per_min
        self.events = []
        self.lock = threading.Lock()

    def take(self, w):
        while True:
            with self.lock:
                now = time.time()
                self.events = [(t, x) for t, x in self.events if now - t < 60]
                used = sum(x for _, x in self.events)
                if used + w <= self.per_min:
                    self.events.append((now, w))
                    return
            time.sleep(0.5)


class HLInfo:
    def __init__(self, url=INFO_URL, budget_per_min=400, timeout=15):
        self.url = url
        self.budget = RateBudget(budget_per_min)
        self.timeout = timeout
        self.calls = 0

    def post(self, body, extra_weight=0):
        w = WEIGHTS.get(body.get("type"), 20) + extra_weight
        self.budget.take(w)
        data = json.dumps(body).encode()
        delay = 2.0
        for attempt in range(5):
            try:
                req = urllib.request.Request(self.url, data=data, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    self.calls += 1
                    return json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < 4:
                    time.sleep(delay); delay *= 2; continue
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if attempt < 4:
                    time.sleep(delay); delay *= 2; continue
                raise

    # ---- endpoint ----
    def meta_and_ctxs(self):
        """Ritorna (meta, ctxs): meta con universe+marginTables, ctxs con funding/markPx/OI/volume."""
        meta, ctxs = self.post({"type": "metaAndAssetCtxs"})
        return meta, ctxs

    def meta(self):
        return self.post({"type": "meta"})

    def all_mids(self):
        return {k: float(v) for k, v in self.post({"type": "allMids"}).items()}

    def l2_book(self, coin):
        d = self.post({"type": "l2Book", "coin": coin})
        bids = [(float(x["px"]), float(x["sz"])) for x in d["levels"][0]]
        asks = [(float(x["px"]), float(x["sz"])) for x in d["levels"][1]]
        return {"bids": bids, "asks": asks, "time": d.get("time")}

    def candles(self, coin, interval, start_ms, end_ms=None):
        end_ms = end_ms or int(time.time() * 1000) + 60_000
        d = self.post({"type": "candleSnapshot", "req": {"coin": coin, "interval": interval,
                                                         "startTime": int(start_ms), "endTime": int(end_ms)}},
                      extra_weight=2)
        return [{"t": int(c["t"]), "T": int(c["T"]), "o": float(c["o"]), "h": float(c["h"]),
                 "l": float(c["l"]), "c": float(c["c"]), "v": float(c["v"])} for c in d]

    def funding_history(self, coin, start_ms, end_ms=None):
        out, cur = [], int(start_ms)
        end_ms = end_ms or int(time.time() * 1000)
        while cur < end_ms:
            d = self.post({"type": "fundingHistory", "coin": coin, "startTime": cur, "endTime": end_ms})
            if not d:
                break
            out += [{"t": int(x["time"]), "rate": float(x["fundingRate"])} for x in d]
            nxt = int(d[-1]["time"]) + 1
            if nxt <= cur or len(d) < 500:
                break
            cur = nxt
        return out


def margin_info(meta):
    """{coin: {maxLev, szDecimals, tiers:[(lowerBound, maxLev)], delisted}} dalla meta."""
    tables = {tid: t["marginTiers"] for tid, t in meta.get("marginTables", [])}
    out = {}
    for a in meta["universe"]:
        tiers = tables.get(a.get("marginTableId"))
        tiers = [(float(x["lowerBound"]), int(x["maxLeverage"])) for x in tiers] if tiers else [(0.0, int(a["maxLeverage"]))]
        out[a["name"]] = {"maxLev": int(a["maxLeverage"]), "szDecimals": int(a["szDecimals"]),
                          "tiers": tiers, "delisted": bool(a.get("isDelisted"))}
    return out


def maint_rate(info, notional):
    """Margine di mantenimento Hyperliquid = metà del margine iniziale alla leva massima del tier."""
    lev = info["tiers"][0][1]
    for lb, ml in info["tiers"]:
        if notional >= lb:
            lev = ml
    return 1.0 / (2.0 * lev)
