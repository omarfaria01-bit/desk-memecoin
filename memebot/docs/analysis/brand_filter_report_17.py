"""Compito 17: verifica sui dati del filtro "nomi di marchi famosi" prima di attivarlo.
Offline, solo lettura, su copie /tmp (shadow_trades.csv, trades.csv del branch data-snapshot).
Usa la stessa lista/normalizzazione di sources/brand_filter.py (vedi quel modulo)."""
import csv
import sys

sys.path.insert(0, ".")
from sources.brand_filter import brand_tag  # noqa: E402

NEG_LESSONS = {"BEAST", "DESSA", "PILLSEY", "GLITCH", "BNB", "HYPERCHAN"}


def load(path):
    with open(path) as f:
        return list(csv.DictReader(f))


def valid_rows(rows):
    return [r for r in rows if r.get("valido", "1") == "1" and not r.get("invalid_for_learning")]


def stats(rows, pnl_key):
    n = len(rows)
    if n == 0:
        return {"n": 0}
    pnls = sorted(float(r[pnl_key] or 0) for r in rows)
    rugs = sum(1 for r in rows if r.get("rug") == "1")
    crashes = sum(1 for p in pnls if p <= -50)
    avg = sum(pnls) / n
    med = pnls[n // 2] if n % 2 else (pnls[n // 2 - 1] + pnls[n // 2]) / 2
    return {"n": n, "avg_pnl": round(avg, 1), "med_pnl": round(med, 1), "rug": rugs,
            "rug_pct": round(100 * rugs / n, 1), "crash50_pct": round(100 * crashes / n, 1)}


def main(shadow_path, trades_path):
    shadow = valid_rows(load(shadow_path))
    trades = load(trades_path)
    brand_rows, other_rows = [], []
    for r in shadow:
        tag = brand_tag(r["symbol"], None)
        (brand_rows if tag else other_rows).append(r)
    bt_rows, ot_rows = [], []
    for r in trades:
        tag = brand_tag(r["symbol"], None)
        (bt_rows if tag else ot_rows).append(r)

    print("# Compito 17: token-brand nei dati shadow/trade (verifica prima del filtro)\n")
    print(f"Shadow validi: {len(shadow)} (brand: {len(brand_rows)}). Trade reali: {len(trades)} (brand: {len(bt_rows)}).\n")
    print("| popolazione | n | pnl medio | pnl mediano | rug | crash <=-50% |")
    print("|---|---|---|---|---|---|")
    brand_rows_no_lesson = [r for r in brand_rows if r["symbol"].upper() not in NEG_LESSONS]
    for label, rows, key in (("shadow con brand", brand_rows, "pnl_pct"),
                              ("shadow con brand (escl. lezioni negative)", brand_rows_no_lesson, "pnl_pct"),
                              ("shadow senza brand", other_rows, "pnl_pct"),
                              ("trade reali con brand", bt_rows, "pnl_pct"), ("trade reali senza brand", ot_rows, "pnl_pct")):
        s = stats(rows, key)
        if s["n"] == 0:
            print(f"| {label} | 0 | - | - | - | - |")
            continue
        print(f"| {label} | {s['n']} | {s['avg_pnl']:+.1f}% | {s['med_pnl']:+.1f}% | "
              f"{s['rug']}/{s['n']} ({s['rug_pct']:.0f}%) | {s['crash50_pct']:.0f}% |")
    print("\n## Righe brand trovate (simbolo, marchio riconosciuto, pnl, rug)\n")
    for r in brand_rows:
        les = " [LEZIONE NEGATIVA]" if r["symbol"].upper() in NEG_LESSONS else ""
        print(f"- {r['symbol']} ({brand_tag(r['symbol'], None)}): pnl {float(r['pnl_pct']):+.1f}%, "
              f"rug={r.get('rug')}{les}")


if __name__ == "__main__":
    main(*(sys.argv[1:] or ["/tmp/desk_data/data/shadow_trades.csv", "/tmp/desk_data/data/trades.csv"]))
