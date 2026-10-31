#!/usr/bin/env python3
"""Compito 12: ri-etichettatura notturna di tutti i token visti dal bot. OFFLINE, sola lettura dei file del bot.

Cron consigliato sul server (ora di Roma), a bassa priorita:
    CRON_TZ=Europe/Rome
    0 4 * * *  cd /opt/desk/memebot && nice -n 10 python3 tools/retro_label.py >> retro_label.log 2>&1

Per ogni mint visto (eventi reject/pass/shadow_open/buy di events.jsonl, shadow_trades.csv, trades.csv) tra
--max-age-h (48) e --min-age-h (24) ore fa, prende le candele 1m da GeckoTerminal (>= 3,2 s tra richieste, backoff
su 429, cache su disco, riprendibile) e assegna l'etichetta con le convenzioni del bot:
    win  = +TP1_PCT (30%) toccato PRIMA di SL (-12%), senza rug nelle 24 h
    rug  = minimo <= RUG_FRAC (10%) del prezzo d'ingresso entro 24 h (anche dopo il TP1: allora NON e positivo)
    loss = SL toccato prima del TP1, oppure nessuno dei due entro 24 h
    unknown = candele assenti o meno di 30 minuti di dati
Output: data/retro_labels.jsonl (idempotente: chiave mint + ora in cui e stato visto) + data/retro_labels_summary.json.
'positive' e vero SOLO per 'win' di token non cattivi (lezioni negative, scarti duri di sicurezza, rug).
Il bot lo legge solo con RETRO_LABELS_IN_LEARNING (default off). Solo libreria standard."""
import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "https://api.geckoterminal.com/api/v2"
TP1, SL, RUG_FRAC, WINDOW_S = 30.0, -12.0, 0.10, 24 * 3600
MIN_INTERVAL = 3.2
SOURCE_KINDS = {"reject", "pass", "shadow_open", "buy"}
HARD_BAD = ("mint non revocata", "freeze attiva", "rug", "tassa alta", "honeypot", "ticker impostore")
NEG_LESSONS = {"HRJPbDu8h4cH2gT1rKcmzNcvzX1M7jHKogS7zRJkpump", "npz2FhoufQ7pKqhGpaipYWacrAkidyW2yDpVWSvpump",
               "J5jxG3PFaWpj6BLEQExyC6FSqSvaTKNdQ9G3sZwBpump", "EYqygbhZjP7EbM4fSn8devctnksAvPAD1Bt57XBFpump",
               "8Xg89xT8Ve34sKiYqYuMs46mycREsCA2F362UscRpump", "3ApVr1xphYMpi9dn3rpqU4hwvpmCQDVwxET1rdCDpump"}
STATS = {"requests": 0, "http_429": 0}
_last = [0.0]


def _ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        try:   # CSV del bot: "YYYY-mm-dd HH:MM" ora di Roma
            from zoneinfo import ZoneInfo
            return datetime.strptime(str(s), "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo("Europe/Rome")).timestamp()
        except Exception:  # noqa: BLE001
            return None


def get(path, params=None):
    """GeckoTerminal con limite di frequenza e backoff. None = errore persistente o 404."""
    url = BASE + path + ("?" + "&".join(f"{k}={v}" for k, v in params.items()) if params else "")
    for attempt in range(6):
        wait = MIN_INTERVAL - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        STATS["requests"] += 1
        req = urllib.request.Request(url, headers={"Accept": "application/json;version=20230302",
                                                   "User-Agent": "desk-retro-label/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 429 or e.code >= 500:
                STATS["http_429"] += e.code == 429
                time.sleep(min(60, 10 * 2 ** attempt))
                continue
            return None
        except Exception:  # noqa: BLE001
            time.sleep(5 * (attempt + 1))
    return None


def seen_mints(data_dir, now, min_age_h, max_age_h):
    """mint -> {t, src, why, cat, fe}: prima volta vista nella finestra [now-max_age, now-min_age]."""
    lo, hi = now - max_age_h * 3600, now - min_age_h * 3600
    out = {}

    def add(mint, t, src, why="", cat="", fe=None):
        if not mint or t is None or not (lo <= t <= hi):
            return
        cur = out.get(mint)
        if cur is None or t < cur["t"]:
            out[mint] = {"t": t, "src": src, "why": str(why)[:160], "cat": str(cat or "")[:60], "fe": fe or {}}
    ev = os.path.join(data_dir, "events.jsonl")
    if os.path.exists(ev):
        with open(ev) as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("kind") in SOURCE_KINDS and e.get("mint"):
                    add(e["mint"], _ts(e.get("ts")), e["kind"], e.get("msg", ""), e.get("cat", ""))
    for name, src in (("shadow_trades.csv", "shadow"), ("trades.csv", "trade")):
        p = os.path.join(data_dir, name)
        if not os.path.exists(p):
            continue
        with open(p, newline="") as f:
            for r in csv.DictReader(f):
                try:
                    fe = json.loads(r.get("entry_features") or "{}") or {}
                except ValueError:
                    fe = {}
                add(r.get("mint"), _ts(r.get("aperto") or r.get("opened")), src, r.get("motivo_scarto", ""), "", fe)
    return out


def top_pool(mint, cache):
    if mint in cache:
        return cache[mint]
    d = get(f"/networks/solana/tokens/{mint}/pools", {"page": 1})
    pools = (d or {}).get("data") or []
    best = max(pools, key=lambda p: float((p.get("attributes") or {}).get("reserve_in_usd") or 0), default=None)
    addr = (best or {}).get("attributes", {}).get("address") if best else None
    if d is not None:   # errore di rete: nessuna voce in cache, si riprova al prossimo giro
        cache[mint] = addr
    return addr


def candles(pool, t0, cache_dir):
    """Candele 1m da t0 a t0 + 24 h (2 pagine da 1000). Cache su disco: riprendibile, nessun doppio download."""
    fn = os.path.join(cache_dir, f"{pool}_{int(t0 // 60)}.json")
    if os.path.exists(fn):
        return json.load(open(fn))
    rows, before = {}, int(t0 + WINDOW_S + 60)
    for _ in range(2):
        d = get(f"/networks/solana/pools/{pool}/ohlcv/minute",
                {"aggregate": 1, "limit": 1000, "currency": "usd", "token": "base", "before_timestamp": before})
        if d is None:
            if not rows:
                return None   # errore: niente file, si riprova
            break
        lst = (((d.get("data") or {}).get("attributes") or {}).get("ohlcv_list")) or []
        for r in lst:
            rows[int(r[0])] = [float(v) for v in r[1:6]]
        if not lst or min(int(r[0]) for r in lst) <= t0:
            break
        before = min(int(r[0]) for r in lst)
    out = [[t] + rows[t] for t in sorted(rows) if t0 - 60 <= t <= t0 + WINDOW_S]
    with open(fn, "w") as f:
        json.dump(out, f)
    return out


def label(bars, t0):
    """Etichetta con le convenzioni del bot. bars = [[t, o, h, l, c, v], ...]."""
    bars = [b for b in bars or [] if b[0] >= t0 - 60]
    if len(bars) < 30:
        return {"label": "unknown", "why": f"{len(bars)} minuti di dati"}
    entry = bars[0][1] or bars[0][4]
    if not entry or entry <= 0:
        return {"label": "unknown", "why": "prezzo d'ingresso nullo"}
    tp, sl, rug = entry * (1 + TP1 / 100), entry * (1 + SL / 100), entry * RUG_FRAC
    first, t_first = None, None
    low = min(b[3] for b in bars)
    for b in bars:
        if first is None and b[3] <= sl:
            first, t_first = "sl", b[0]
        if first is None and b[2] >= tp:
            first, t_first = "tp", b[0]
    out = {"entry": entry, "min_pct": round((low / entry - 1) * 100, 2),
           "max_pct": round((max(b[2] for b in bars) / entry - 1) * 100, 2), "minutes": len(bars)}
    if low <= rug:
        out.update(label="rug", why="minimo <= 10% dell'ingresso entro 24 h" + (" (dopo il TP1)" if first == "tp" else ""))
    elif first == "tp":
        out.update(label="win", why=f"TP1 +{TP1:g}% prima dello SL", min_to_tp=round((t_first - t0) / 60, 1))
    else:
        out.update(label="loss", why="SL prima del TP1" if first == "sl" else "ne TP1 ne SL in 24 h")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=HERE)
    ap.add_argument("--out", default=os.path.join(HERE, "data", "retro_labels.jsonl"))
    ap.add_argument("--min-age-h", type=float, default=24.0)
    ap.add_argument("--max-age-h", type=float, default=48.0)
    ap.add_argument("--max-mints", type=int, default=600)
    ap.add_argument("--now", type=float, default=None)
    a = ap.parse_args(argv)
    t_start, now = time.time(), a.now or time.time()
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    cache_dir = os.path.join(os.path.dirname(a.out), "retro_cache")
    os.makedirs(cache_dir, exist_ok=True)
    pc_path = os.path.join(cache_dir, "pools.json")
    pool_cache = json.load(open(pc_path)) if os.path.exists(pc_path) else {}
    done = set()
    if os.path.exists(a.out):
        with open(a.out) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    done.add((r["mint"], int(r["seen_t"])))
                except (ValueError, KeyError):
                    continue
    seen = seen_mints(a.data_dir, now, a.min_age_h, a.max_age_h)
    todo = [(m, s) for m, s in sorted(seen.items(), key=lambda x: x[1]["t"]) if (m, int(s["t"])) not in done]
    n_new, counts = 0, {}
    with open(a.out, "a") as fo:
        for mint, s in todo[:a.max_mints]:
            pool = top_pool(mint, pool_cache)
            if pool is None and mint not in pool_cache:
                continue   # errore di rete: si riprova al prossimo giro (nessuna riga)
            bars = candles(pool, s["t"], cache_dir) if pool else []
            if bars is None:
                continue
            lab = label(bars, s["t"])
            bad = mint in NEG_LESSONS or lab["label"] == "rug" or any(k in s["cat"].lower() for k in HARD_BAD)
            rec = {"mint": mint, "seen_t": int(s["t"]), "seen_src": s["src"], "seen_why": s["why"], "seen_cat": s["cat"],
                   "features": s["fe"], "pool": pool, **lab, "bad": bool(bad),
                   "positive": bool(lab["label"] == "win" and not bad), "labeled_at": int(time.time())}
            fo.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fo.flush()
            n_new += 1
            counts[lab["label"]] = counts.get(lab["label"], 0) + 1
            if n_new % 20 == 0:
                json.dump(pool_cache, open(pc_path, "w"))
    json.dump(pool_cache, open(pc_path, "w"))
    summary = {"run_at": int(time.time()), "seen_in_window": len(seen), "already_done": len(seen) - len(todo),
               "labeled_now": n_new, "labels": counts, "requests": STATS["requests"], "http_429": STATS["http_429"],
               "seconds": round(time.time() - t_start, 1), "window_h": [a.min_age_h, a.max_age_h]}
    json.dump(summary, open(os.path.join(os.path.dirname(a.out), "retro_labels_summary.json"), "w"), indent=1)
    print(json.dumps(summary))
    return summary


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
