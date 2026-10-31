"""Compito 2: famiglie di segnali soft -> HARD (BEAST/DESSA). Offline, fixture sintetiche costruite sui valori
REGISTRATI dal bot (CHANGES.md v8.4b/c/e, LABEL_SEEDS, docs/kiwi_controls.md). I valori non registrati restano
'non noti' e sono provati nel caso peggiore. Token cattivi usati SOLO come casi da scartare."""
from sources import cluster_v2 as C

KUCOIN = "BmFdpraQhkiDQE6SnfG5omcA1VwzqfXrwtNYBwWTymy6"
MEXC = "ASTyfSima4LLAdDgoFGkgqoKowG1LZFDr9fAQrg7iaJZ"
c = dict(C.CFG)


def fams(fresh=0.0, scanned=150, cex=None, funder=0, stars=0, uni=0, group=0.0, linked=0.0):
    rows = [{"o": f"w{i}", "src": src} for src, n in (cex or {}).items() for i in range(n)]
    return C.combo_families(rows, {}, c, fresh, scanned, (None, funder, 60, funder / 60), [("s", 8, 1.0)] * stars,
                            uni, group, linked)[0]


N = c["FAM_HARD_N"]
# --- lezioni negative (devono essere HARD con la regola attiva)
dessa = fams(fresh=0.37, cex={KUCOIN: 3}, funder=1, uni=4)          # 87% nuovi tra i controllati, 3 KuCoin, uniformi 4
assert set(dessa) >= {"fresh", "cex_fresh", "uniform"} and len(dessa) >= N, dessa
glitch = fams(cex={MEXC: 7}, funder=5, uni=5)                       # gia hard (CEX-fresh); qui solo verifica
assert len(glitch) >= N, glitch
# BEAST: registrati finanziatore 5 e uniformi 4 (2 famiglie). Wallet nuovi e gruppo NON registrati:
beast_known = fams(funder=5, uni=4)
assert beast_known == ["funder", "uniform"], beast_known            # 2 < 3: con i soli dati noti NON basta
assert len(fams(funder=5, uni=4, fresh=0.3)) >= N                   # basta una terza famiglia (es. wallet nuovi)
# --- controlli positivi (NON devono essere HARD)
bonnie = fams(fresh=1 / 60, scanned=60, funder=1, uni=4, linked=5.9)   # 1 solo wallet nuovo, max 1 per finanziatore
assert len(bonnie) < N, bonnie
dacity_known = fams(stars=1, funder=17, group=16.1)                 # stella 17 wallet, gruppo 16.1%
assert dacity_known == ["funder", "group"], dacity_known            # 2 famiglie: oggi NON hard
# Dacity caso peggiore (wallet nuovi e uniformi NON registrati): con una di queste diventerebbe hard
assert len(fams(stars=1, funder=17, group=16.1, uni=4)) >= N
# --- cablaggio in analyze: 'shadow' registra soltanto, 'on' rende hard
rows = ([{"o": f"k{i}", "pct": 0.4, "ntx": 3, "src": KUCOIN, "ts": 1000 + i, "slot": i} for i in range(3)]
        + [{"o": f"n{i}", "pct": 0.4 - i * 0.001, "ntx": 5, "src": f"F{i}", "ts": 5000 + 400 * i, "slot": 100 + i}
           for i in range(57)]
        + [{"o": f"h{i}", "pct": 0.05, "ntx": None} for i in range(90)])
r = C.analyze(rows, {}, {}, {"FAM_MODE": "shadow"})
assert r["combo"]["hit"] and r["combo"]["mode"] == "shadow" and "famiglie" not in r["why"], r["combo"]
r_on = C.analyze(rows, {}, {}, {"FAM_MODE": "on"})
assert r_on["level"] == "hard" and "famiglie di segnali soft" in r_on["why"], (r_on["level"], r_on["why"])
print("test_cluster_beast_dessa OK", {"DESSA": dessa, "BEAST(noti)": beast_known, "Bonnie": bonnie,
                                      "Dacity(noti)": dacity_known})
