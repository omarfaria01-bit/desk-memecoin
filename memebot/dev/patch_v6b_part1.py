import re
P = "/workspace/memebot/desk.py"
s = open(P).read()

def rep(old, new, cnt=1):
    global s
    assert s.count(old) == cnt, (old[:80], s.count(old))
    s = s.replace(old, new)

# ---------------------------------------------------------------- CFG
rep('''    "REJECT_INSIDER_NETWORKS": True,
''', '''    "REJECT_INSIDER_NETWORKS": True,  # v6: con True le reti collegate sono un segnale SOFT graduato (vedi sotto)
    # v6: segnali SOFT graduati -> penalita sul punteggio + size ridotta; scarto solo oltre la soglia grave.
    # Restano HARD: mint/freeze non revocate, rug, tassa, LP 0% o non verificabile, insider, creatore,
    # pochi holder, rischi gravi RugCheck, nessun percorso di vendita, liquidita bassa, lista nera.
    "TOP10_HARD_PCT": 50.0,           # top 10 tra MAX_TOP10_PCT (35) e 50 = soft; oltre 50 = scarto
    "EQUAL_MIN_PCT": 0.1,             # quote "gemelle" contate solo da 0.1% in su
    "EQUAL_HARD_PCT": 10.0,           # wallet con la stessa quota (>= MAX_IDENTICAL_WALLETS) = soft; se insieme >= 10% = scarto
    "BUNDLE_SOFT_PCT": 3.0,           # reti di wallet collegati: sotto il 3% dell'offerta nessun segnale
    "BUNDLE_HARD_PCT": 25.0,          # reti collegate oltre il 25% dell'offerta = scarto
    "LP_HARD_PCT": 25.0,              # LP bloccata tra 25% e MIN_LP_LOCKED_PCT (50) = soft; sotto 25% (o 0%) = scarto
    "SOFT_PENALTY": 0.05,             # penalita sul punteggio per ogni segnale soft
    "SOFT_SIZE_MULT": 0.5,            # size x 0.5 per ogni segnale soft (si moltiplicano)
    "SOFT_MAX_FLAGS": 3,              # 3 o piu segnali soft insieme = scarto
    # v6: shadow trade (operazioni ipotetiche, nessun denaro) sui token scartati per un segnale soft o di poco
    "SHADOW_TRADES": True,
    "SHADOW_NEAR_SCORE": 0.05,        # anche i punteggi entro 0.05 sotto la soglia
    "SHADOW_MAX_OPEN": 60,
    "SHADOW_CHECK_SEC": 30,
    "SHADOW_SIZE_USD": 30.0,          # size ipotetica (costi stimati con la stessa formula del paper)
    "SHADOW_ROUTE_CHECK": True,       # preventivo Jupiter di vendita all'apertura e alla chiusura dello shadow
    # v6: il Learner impara dagli shadow (per segnale soft) entro limiti fissi; mai sugli scarti HARD
    "LEARN_SHADOW_MIN_N": 15,         # esiti minimi prima di toccare un segnale
    "LEARN_SHADOW_WINDOW": 50,        # esiti recenti considerati per segnale
    "LEARN_STEP_SEC": 3600,           # al massimo un passo all'ora per segnale
    "LEARN_PEN_STEP": 0.01,
    "LEARN_SIZE_STEP": 0.05,
    "LEARN_LOOSEN_AVG": 5.0,          # allenta solo se media netta >= +5%,
    "LEARN_LOOSEN_WIN": 0.45,         # win rate >= 45% (rug contati 3 volte)
    "LEARN_LOOSEN_RUG": 0.10,         # e rug/-50% <= 10%
    "LEARN_TIGHTEN_RUG": 0.20,        # stringe se media < 0 o rug/-50% > 20%
    "LEARN_THR_STEP": 0.005,          # soglia: -0.005 solo se i quasi-promossi sono chiaramente in utile
    "LEARN_THR_WIN": 0.50,
''')
open(P, "w").write(s)
print("ok")
