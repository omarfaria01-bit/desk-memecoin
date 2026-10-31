"""Compito 2: esito della regola 'famiglie soft' sugli eventi cluster2 registrati. SOLA LETTURA.
Uso: cp events.jsonl /tmp/events.jsonl && python3 docs/analysis/cluster_combo_eval.py /tmp/events.jsonl
Servono eventi scritti DOPO il commit che registra uniform/single_funder/stars/combo (prima mancano: 'n/d')."""
import json
import sys

MARK = {"BEAST": "cattivo", "DESSA": "cattivo", "PILLSEY": "cattivo", "GLITCH": "cattivo", "BNB": "cattivo",
        "HYPERCHAN": "cattivo", "Bonnie": "controllo", "Dacity": "controllo", "GTA6": "probabile buono",
        "STONK": "probabile buono", "FLOAT": "probabile buono", "neet": "probabile buono", "LUMI": "probabile buono"}


def main(path):
    last = {}
    with open(path) as f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            d = e.get("data") or e
            if e.get("kind") != "cluster2" and d.get("kind") != "cluster2" and "fresh_share" not in d:
                continue
            sym = str(e.get("msg", "")).split(" cluster v2")[0] or d.get("symbol", "?")
            last[(sym, d.get("mint", e.get("mint", "?")))] = d
    print("| token | stato | livello | famiglie (combo) | n | hit |")
    print("|---|---|---|---|---|---|")
    for (sym, mint), d in sorted(last.items(), key=lambda x: (MARK.get(x[0][0], "z"), x[0][0])):
        cb = d.get("combo") or {}
        print(f"| {sym} ({str(mint)[:6]}) | {MARK.get(sym, '-')} | {d.get('level')} | "
              f"{', '.join(cb.get('families', [])) if cb else 'n/d'} | {cb.get('n', 'n/d')} | {cb.get('hit', 'n/d')} |")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/events.jsonl")
