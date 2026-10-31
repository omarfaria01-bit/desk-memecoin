"""Compito 3: bordi delle regole uniform-share, stella, finanziatore unico, ticker impostore. Offline, indirizzi finti.
Per ogni soglia: appena sotto / esattamente / appena sopra. Exchange noti esclusi, hub inclusi."""
from sources import cluster_v2 as C

c = C.CFG
BINANCE = "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhLEfqhAF"   # exchange noto in INFRA_FUNDERS
HUB = "HubHub1111111111111111111111111111111111111"
INFRA = {HUB: "hub (auto)"}


def tail(n, start=0):
    """holder di riempimento: quote basse e tutte diverse (rapporto > 1,10 tra vicini), mai un altopiano"""
    return [{"o": f"t{start + i}", "pct": round(0.019 / (1.2 ** i), 6), "ntx": None} for i in range(n)]


def level(rows, infra=None):
    return C.analyze(rows, infra or {}, {}, {})


# ============================================================ uniform-share
def plateau(n, each, top=5.0):
    return [{"o": "top", "pct": top, "ntx": None}] + [{"o": f"u{i}", "pct": each, "ntx": None} for i in range(n)] + tail(30)


# >= UNIFORM_HARD_WALLETS (20) wallet uniformi = hard, anche con quota bassa
assert C.uniform_plateau(plateau(20, 0.05))[0] == 20
r19, r20 = level(plateau(19, 0.05)), level(plateau(20, 0.05))
assert r19["level"] != "hard" and r20["level"] == "hard" and "quote uniformi" in r20["why"], (r19["why"], r20["why"])
assert r19["level"] == "soft" and "quote uniformi" in r19["why"]          # 19 >= UNIFORM_SOFT_WALLETS (12)
# soft da 12: 11 no, 12 si
assert "quote uniformi" not in level(plateau(11, 0.05))["why"]
assert "quote uniformi" in level(plateau(12, 0.05))["why"] and level(plateau(12, 0.05))["level"] == "soft"
# >= 10 wallet che insieme fanno >= 15% = hard (UNIFORM_HARD_MIN_N, UNIFORM_HARD_PCT)
assert level(plateau(10, 1.5))["level"] == "hard"                          # 10 x 1,5 = 15,0% esatto
assert level(plateau(10, 1.49))["level"] != "hard"                         # 14,9%
assert level(plateau(9, 2.0))["level"] != "hard"                           # 18% ma solo 9 wallet
assert level(plateau(11, 1.4))["level"] == "hard"                          # 15,4% con 11
# rapporto primo/ultimo: 1,10 incluso, 1,11 escluso (circa +-5%)
edge = [{"o": "top", "pct": 5.0, "ntx": None}] + [{"o": f"u{i}", "pct": 0.55, "ntx": None} for i in range(10)] + \
       [{"o": "edge", "pct": 0.5, "ntx": None}] + tail(10)
assert C.uniform_plateau(edge)[0] == 11                                    # 0,55 / 0,50 = 1,10
edge[-11]["pct"] = 0.4954                                                  # 0,55 / 0,4954 = 1,11
assert C.uniform_plateau(edge)[0] == 10
# partenza oltre il rango 10 (UNIFORM_MAX_START) non conta
late = [{"o": f"d{i}", "pct": 10.0 / (1.3 ** i), "ntx": None} for i in range(11)] + \
       [{"o": f"u{i}", "pct": 0.3, "ntx": None} for i in range(25)]
assert C.uniform_plateau(late)[0] < 20
# polvere (< UNIFORM_MIN_PCT 0,02%) non fa altopiano; 'ext' esclusi
assert C.uniform_plateau([{"o": f"p{i}", "pct": 0.01, "ntx": None} for i in range(50)])[0] == 0
assert C.uniform_plateau([{"o": f"x{i}", "pct": 0.05, "ext": True} for i in range(30)])[0] == 0


# ============================================================ stella
def stars_rows(sizes, funder_of=lambda k: f"F{k}" + "1" * 40, checked_extra=60):
    rows = []
    for k, n in enumerate(sizes):
        rows += [{"o": f"s{k}_{i}", "pct": 0.05, "ntx": 5, "src": funder_of(k)} for i in range(n)]
    rows += [{"o": f"c{i}", "pct": 0.04 - i * 0.0004, "ntx": 500} for i in range(checked_extra)]   # controllati, vecchi
    return rows + tail(20)


assert C.star_clusters(stars_rows([7])) == []                              # 7 < STAR_MIN_WALLETS
assert [x[1] for x in C.star_clusters(stars_rows([8]))] == [8]             # 8 = stella
assert len(C.star_clusters(stars_rows([8, 8]))) == 2
r2, r3 = level(stars_rows([8, 8])), level(stars_rows([8, 8, 8]))
assert r2["level"] == "soft" and "cluster a stella" in r2["why"], r2["why"]          # 1-2 stelle = soft
assert r3["level"] == "hard" and "3 cluster a stella" in r3["why"], r3["why"]        # >= STAR_HARD_N (3) = hard
assert C.star_clusters(stars_rows([20], funder_of=lambda k: BINANCE)) == []          # exchange noto: escluso
assert [x[1] for x in C.star_clusters(stars_rows([8], funder_of=lambda k: HUB), INFRA)] == [8]   # hub: incluso


# ============================================================ finanziatore unico
def sf_rows(n_same, n_checked, funder="Fun" + "1" * 40, ext=0, unchecked=0):
    rows = [{"o": f"f{i}", "pct": 0.05, "ntx": 5, "src": funder} for i in range(n_same)]
    rows += [{"o": f"o{i}", "pct": 0.04, "ntx": 500, "src": f"Own{i}" + "1" * 38} for i in range(n_checked - n_same)]
    rows += [{"o": f"e{i}", "pct": 0.04, "ntx": 5, "src": funder, "ext": True} for i in range(ext)]
    rows += [{"o": f"n{i}", "pct": 0.03, "ntx": None, "src": funder} for i in range(unchecked)]
    return rows + tail(10)


def sf_hard(rows, infra=None):
    return "un solo finanziatore" in level(rows, infra)["why"]


assert C.single_funder(sf_rows(10, 20))[1:] == (10, 20, 0.5)
assert sf_hard(sf_rows(10, 20))                                            # 10/20 = 50% esatto, 10 wallet: hard
assert not sf_hard(sf_rows(10, 21))                                        # 47,6%
assert not sf_hard(sf_rows(9, 18))                                         # 50% ma 9 < SINGLE_FUNDER_MIN
assert sf_hard(sf_rows(11, 21))                                            # 52%
assert not sf_hard(sf_rows(20, 20, funder=BINANCE))                        # exchange noto: escluso
assert sf_hard(sf_rows(10, 20, funder=HUB), INFRA)                         # hub: incluso
assert C.single_funder(sf_rows(10, 21, ext=5))[1:3] == (10, 21)            # 'ext' fuori dai controllati
assert C.single_funder(sf_rows(10, 21, unchecked=5))[1:3] == (10, 21)      # ntx non letto: fuori


# ============================================================ ticker impostore (desk.py)
import desk as D  # noqa: E402

FAKE = "Fake" + "1" * 36 + "pump"
assert D.CFG["TICKER_IMPOSTOR_ACTION"] == "block"
for sym in ("BNB", "bnb", "B.N.B", "SOL", "USDC", "WETH", "JUP"):
    assert D.ticker_impostor(sym, FAKE) is not None, sym                   # mint non ufficiale: impostore
assert D.ticker_impostor("BNB", "9gP2kCy3wA1ctvYWQk75guqXuHfrEomqydHLtcTCqiLa") is None   # BNB Wormhole ufficiale
assert D.ticker_impostor("JUP", "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN") is None
assert D.ticker_impostor("XYZ", FAKE, "Bitcoin") is not None               # nome per esteso
assert D.ticker_impostor("WBTC2", FAKE, "Wrapped Bitcoin") is not None
assert D.ticker_impostor("BTCCAT", FAKE, "Bitcoin Cat") is None            # solo match esatto: passa
assert D.ticker_impostor("AAPL", FAKE, "Apple") is None                    # azioni: solo annotate, non bloccate
assert D.stock_ticker("AAPL", "Apple") is not None
print("test_cluster_rules OK")
