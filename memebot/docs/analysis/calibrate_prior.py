#!/usr/bin/env python3
"""Compito 9: ricalibrazione OFFLINE di p_prior sui dati del bot. SOLA LETTURA su COPIE dei CSV.

Uso:
    cp shadow_trades.csv trades.csv /tmp/
    python3 docs/analysis/calibrate_prior.py --out /tmp /tmp/shadow_trades.csv /tmp/trades.csv
    [--model sources/model_prior.json]   # per ricalcolare p_prior dalle colonne f_* dove manca

Righe: valido != 0, invalid_for_learning vuoto, p_prior presente (entry_features) o ricalcolabile. y = pnl > 0
(come learn_from_trade). Split TEMPORALE su 'aperto': primo 70% = calibrazione, ultimo 30% = verifica.
Metodi: Platt (logistica su logit(p)) e isotonica (PAV); si sceglie quello con Brier piu basso sulla verifica.
Output: <out>/model_prior_calibrated.json + report Markdown su stdout. NESSUN peso sullo score del bot.
Solo libreria standard."""
import csv
import json
import math
import sys
import time

FEATURES = ["trend", "macd", "rsi", "bsr", "volsurge", "higher_lows", "social", "trending", "narrative", "holders",
            "concentration", "liquidity"]


def fnum(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def sigmoid(z):
    z = max(-40.0, min(40.0, z))
    return 1.0 / (1.0 + math.exp(-z))


def logit(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


LESSONS = {"HRJPbDu8h4cH2gT1rKcmzNcvzX1M7jHKogS7zRJkpump", "npz2FhoufQ7pKqhGpaipYWacrAkidyW2yDpVWSvpump",
           "J5jxG3PFaWpj6BLEQExyC6FSqSvaTKNdQ9G3sZwBpump", "EYqygbhZjP7EbM4fSn8devctnksAvPAD1Bt57XBFpump",
           "8Xg89xT8Ve34sKiYqYuMs46mycREsCA2F362UscRpump", "3ApVr1xphYMpi9dn3rpqU4hwvpmCQDVwxET1rdCDpump"}
BAD_KEY_MARKS = ("_grave", "_hard", "impostor", "honeypot", "t22", "token2022", "blacklist")


def _is_rug(r):
    """v8.9: un token segnato rug o cattivo (lezione negativa, chiave di scarto rigido) non e MAI un esempio
    positivo, anche con pnl > 0 (TP1 prima del rug)."""
    if str(r.get("rug", "")).strip().lower() in ("1", "true", "si", "yes") or r.get("mint", "") in LESSONS:
        return True
    keys = str(r.get("chiavi", "") or "").lower().split("|") + [str(r.get("tag", "") or "").lower()]
    return any(m in k for k in keys for m in BAD_KEY_MARKS)


def load(paths, model=None):
    rows = []
    for path in paths:
        try:
            with open(path, newline="") as f:
                for r in csv.DictReader(f):
                    if str(r.get("valido", "1")).strip() == "0" or (r.get("invalid_for_learning") or "").strip():
                        continue
                    pnl = fnum(r.get("pnl_pct"))
                    if pnl is None:
                        continue
                    try:
                        fe = json.loads(r.get("entry_features") or "{}") or {}
                    except ValueError:
                        fe = {}
                    p = fnum(fe.get("p_prior"))
                    if p is None and model:
                        fs = {k: fnum(r.get(f"f_{k}")) for k in FEATURES}
                        if all(v is not None for v in fs.values()):
                            p = sigmoid(model["b"] + sum(w * fs.get(k, 0.0) for k, w in model["w"].items()))
                    if p is None:
                        continue
                    rows.append({"t": r.get("aperto") or r.get("opened") or "", "p": p, "y": int(pnl > 0 and not _is_rug(r)),
                                 "mint": r.get("mint", "")})
        except OSError as e:
            print(f"> file non letto: {path} ({e})", file=sys.stderr)
    rows.sort(key=lambda r: r["t"])
    return rows


def auc(ps, ys):
    pos = [p for p, y in zip(ps, ys) if y]
    neg = [p for p, y in zip(ps, ys) if not y]
    if not pos or not neg:
        return None
    # Mann-Whitney con pareggi a meta
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    ranks, i = [0.0] * len(ps), 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and ps[order[j + 1]] == ps[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    rp = sum(r for r, y in zip(ranks, ys) if y)
    return round((rp - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)), 4)


def brier(ps, ys):
    return round(sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ps), 5) if ps else None


def fit_platt(ps, ys, iters=200, lr=0.1, l2=1e-3):
    """p_cal = sigmoid(a * logit(p) + b), discesa del gradiente sulla log-loss (dati piccoli: basta)."""
    a, b = 1.0, 0.0
    xs = [logit(p) for p in ps]
    n = len(xs)
    for _ in range(iters):
        ga = gb = 0.0
        for x, y in zip(xs, ys):
            e = sigmoid(a * x + b) - y
            ga += e * x
            gb += e
        a -= lr * (ga / n + l2 * (a - 1.0))
        b -= lr * gb / n
    return a, b


def fit_isotonic(ps, ys):
    """PAV: blocchi crescenti (x_max del blocco, media y)."""
    pts = sorted(zip(ps, ys))
    blocks = []   # [x_min, x_max, somma_y, n]
    for x, y in pts:
        blocks.append([x, x, float(y), 1])
        while len(blocks) > 1 and blocks[-2][2] / blocks[-2][3] >= blocks[-1][2] / blocks[-1][3]:
            b2 = blocks.pop()
            b1 = blocks[-1]
            b1[1], b1[2], b1[3] = b2[1], b1[2] + b2[2], b1[3] + b2[3]
    return [round(b[1], 6) for b in blocks], [round(b[2] / b[3], 6) for b in blocks]


def apply_iso(p, xs, ys):
    for x, y in zip(xs, ys):
        if p <= x:
            return y
    return ys[-1] if ys else p


def main(argv):
    out, model, paths = "/tmp", None, []
    i = 0
    while i < len(argv):
        if argv[i] == "--out":
            out, i = argv[i + 1], i + 2
        elif argv[i] == "--model":
            with open(argv[i + 1]) as f:
                m = json.load(f)
            model, i = {"w": {k: float(v) for k, v in m["w"].items()}, "b": float(m.get("b") or 0.0)}, i + 2
        else:
            paths.append(argv[i])
            i += 1
    rows = load(paths or ["/tmp/shadow_trades.csv"], model)
    print("# Calibrazione di p_prior (compito 9)\n")
    if len(rows) < 30:
        print(f"Righe utili: {len(rows)} (servono almeno 30 con p_prior): nessuna calibrazione.")
        return None
    cut = int(len(rows) * 0.7)
    tr, te = rows[:cut], rows[cut:]
    ptr, ytr = [r["p"] for r in tr], [r["y"] for r in tr]
    pte, yte = [r["p"] for r in te], [r["y"] for r in te]
    a, b = fit_platt(ptr, ytr)
    xs, ys = fit_isotonic(ptr, ytr)
    p_pl = [sigmoid(a * logit(p) + b) for p in pte]
    p_is = [apply_iso(p, xs, ys) for p in pte]
    res = {"raw": brier(pte, yte), "platt": brier(p_pl, yte), "isotonic": brier(p_is, yte)}
    best = min(("platt", "isotonic"), key=lambda k: res[k])
    doc = {"created": time.strftime("%Y-%m-%dT%H:%M:%S"), "method": best, "label": "pnl > 0",
           "split": {"train": [tr[0]["t"], tr[-1]["t"]], "test": [te[0]["t"], te[-1]["t"]]},
           "n_train": len(tr), "n_test": len(te), "base_rate_train": round(sum(ytr) / len(ytr), 4),
           "base_rate_test": round(sum(yte) / len(yte), 4), "auc_test": auc(pte, yte),
           "brier_test": res, "platt": {"a": round(a, 6), "b": round(b, 6)}, "isotonic": {"x": xs, "y": ys},
           "note": "solo calibrazione: nessun effetto su score, soglie o Learner"}
    path = out.rstrip("/") + "/model_prior_calibrated.json"
    with open(path, "w") as f:
        json.dump(doc, f, indent=1)
    print(f"Righe: {len(rows)} (calibrazione {len(tr)}, verifica {len(te)}); quota vincenti {doc['base_rate_train']} "
          f"-> {doc['base_rate_test']}\n")
    print("| metrica (verifica) | valore |\n|---|---|")
    print(f"| AUC (ordinamento, uguale per tutti i metodi monotoni) | {doc['auc_test']} |")
    for k in ("raw", "platt", "isotonic"):
        print(f"| Brier {k} | {res[k]} |")
    print(f"\nScelto: **{best}**. File: `{path}`. Platt a={doc['platt']['a']}, b={doc['platt']['b']}; "
          f"isotonica {len(xs)} gradini.")
    return doc


if __name__ == "__main__":
    main(sys.argv[1:])
