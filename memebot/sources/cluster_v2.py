"""v8.2 - Controllo cluster on-chain (tipo Bubblemaps) dalla RPC pubblica Solana. Solo lettura, gratis.

Cosa fa (per un mint):
1. holder: getProgramAccounts (Token / Token-2022) -> quote per proprietario, pool/curve/locker esclusi;
   analizzati i primi TOP_N (100) holder.
2. per ogni holder: getSignaturesForAddress (fino a FRESH_TX) -> wallet "nuovo" se < FRESH_TX transazioni;
   per i nuovi: getTransaction della prima transazione -> finanziatore (chi ha mandato i SOL) e ora/slot.
3. collegamenti (union-find):
   - VERI: stesso finanziatore NON infrastrutturale; finanziatore che e' a sua volta holder (trasferimento
     diretto fra holder); finanziatore del finanziatore in comune (2 salti, solo funder piccoli);
     wallet nuovi finanziati nello stesso slot o entro SAME_MIN_SEC secondi (gruppi di almeno 3).
   - INFRA: stesso hot wallet di exchange o fee payer di app (fomo, pump.fun, Photon...). NON contano come
     insider: vengono misurati a parte (cluster_infra_pct).
   Un finanziatore e' "infrastrutturale" se e' nella lista nota (INFRA_FUNDERS + sources/infra_funders.json)
   oppure se ha >= 1000 transazioni nell'ultima ora (hot wallet / relayer): quelli scoperti cosi vengono
   salvati nella lista.
Ritorna metriche + livello 'none' | 'soft' | 'hard' (funzione pura `analyze`, testabile senza rete).
"""
import asyncio
import base64
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
INFRA_PATH = os.path.join(HERE, "infra_funders.json")

# Hot wallet di exchange e fee payer noti (indirizzi pubblici). Ampliata a runtime con quelli scoperti.
INFRA_FUNDERS = {
    "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhLEfqhAF": "Binance",
    "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM": "Binance 2",
    "2ojv9BAiHUrvsm9gxDe7fJSzbNZSJcxZvf8dqmWGHG8S": "Binance 3",
    "3gd3dqgtJ4jWfBfLYTX67DALFetjc5iS72sCgRhCkW2u": "Binance",
    "H8sMJSCQxfKiFTCfDR3DUMLPwcRbM61LGFJ8N4dK3WjS": "Coinbase",
    "2AQdpHJ2JpcEgPiATUXjQxA8QmafFegfQwSLWSprPicm": "Coinbase 2",
    "GJRs4FwHtemZ5ZE9x3FNvJ8TMwitKTh21yxdRPqn7npE": "Coinbase hot",
    "FWznbcNXWQuHTawe9RxvQ2LdCENssh12dsznf4RiouN5": "Kraken",
    "5VCwKtCXgCJ6kit5FybXjvriW3xELsFDhYrPSqtJNmcD": "OKX",
    "is6MTRHEgyFLNTfYcuV4QBWLjrZBfmhVNYR6ccgr8KV": "OKX 2",
    "AC5RDfQFmDS1deWZos921JfqscXdByf8BKHs5ACWjtW2": "Bybit",
    "A77HErqtfN1hLLpvZ9pCtu66FEtM8BveoaKbbMoZ4RiR": "Bitget",
    "ASTyfSima4LLAdDgoFGkgqoKowG1LZFDr9fAQrg7iaJZ": "MEXC",
    "BmFdpraQhkiDQE6SnfG5omcA1VwzqfXrwtNYBwWTymy6": "KuCoin",
    "u6PJ8DtQuPFnfmwHbGFULQ4u4EgjDiyYKjVEsynXq2w": "Gate.io",
    "CebN5WGQ4jvEPvsVU4EoHEpgzq1VV7AbicfhtW4xC9iM": "pump.fun fee",
    "62qc2CNXwrYqQScmEdiZFFAnJR262PxWEuNQtxfafNgV": "pump.fun fee",
}
# Prefisso...suffisso visti su Bubblemaps (magic node nascosti) quando l'indirizzo intero non e noto
INFRA_PATTERNS = [("6LY1", "zkzF", "Kraken hot"), ("BmFd", "ymy6", "KuCoin"), ("u6PJ", "Xq2w", "Gate.io"),
                  ("iGdF", "Sdwu", "Bybit hot"), ("ASTy", "iaJZ", "MEXC")]

CFG = {
    "TOP_N": 150,             # holder analizzati (prima acquisizione del token)
    "MIN_PCT": 0.0,           # quota minima per entrare fra gli analizzati
    "FRESH_TX": 100,          # wallet nuovo = meno di 100 transazioni
    "SAME_MIN_SEC": 60,       # wallet nuovi finanziati nello stesso minuto
    "SAME_MIN_GROUP": 3,
    "NET_MIN_WALLETS": 3,     # un gruppo "conta" con almeno 3 wallet
    "HUB_TX_HOUR": 1000,      # funder con >= 1000 tx nell'ultima ora = infrastruttura (relayer/hot wallet)
    "MAX_FUNDER_LOOKUPS": 25,  # funder controllati (attivita + 2 salti)
    "CALL_GAP": 0.05,         # PublicNode (gratis) regge ~20 richieste/s; la RPC Solana pubblica molto meno
    "WALLET_TOP_N": 60,       # controllo "wallet nuovo + finanziatore" sui primi 60 (2 chiamate ciascuno)
    "SAME_SLOT_GROUP": 3,
    "SWARM_MIN_WALLETS": 10,  # sciame: >= 10 wallet con lo stesso saldo esatto...
    "SWARM_MIN_PCT": 0.005,   # ...e ciascuno >= 0.005% (la polvere non conta)
    "SWARM_HARD_PCT": 20.0,   # HARD: sciame >= 20% dell'offerta con >= 50 wallet
    "SWARM_HARD_WALLETS": 50,
    "SWARM_SOFT_PCT": 5.0,     # >= 3 top holder che comprano nello stesso slot (+-1) = bundle
    "BUDGET_SEC": 75.0,       # oltre: verdetto 'unknown' (MAI 'ok')
    # livelli
    "GROUP_HARD_PCT": 20.0,      # HARD: un solo gruppo con collegamenti veri >= 20% dell'offerta
    "INFRA_GROUP_HARD_PCT": 20.0,  # HARD prudente: gruppo collegato solo via infrastruttura >= 20%
    "INFRA_GROUP_MIN_FRESH": 0.5,  # ...se almeno meta dei suoi wallet sono nuovi o dentro c'e un gruppo vero >= 3%
    "INFRA_GROUP_ALWAYS_PCT": 25.0,  # HARD sempre (prudenza): gruppo anche solo via infrastruttura >= 25%
    "LINKED_HARD_PCT": 25.0,     # HARD: tutti i collegamenti veri insieme >= 25%
    "BIG_GROUP_WALLETS": 50,     # HARD: gruppo da 50+ wallet...
    "BIG_GROUP_PCT": 15.0,       # ...con >= 15%
    "GROUP_SOFT_PCT": 8.0,       # SOFT: un gruppo vero >= 8%
    "LINKED_SOFT_PCT": 5.0,      # SOFT: collegati veri >= 5%
    "INFRA_SOFT_PCT": 10.0,      # SOFT: gruppo infra >= 10%
    "FRESH_SOFT": 0.5,           # SOFT: >= 50% dei top holder nuovi
}

_infra_cache = {"t": 0.0, "d": {}}


def infra_list():
    if time.time() - _infra_cache["t"] > 300:
        d = dict(INFRA_FUNDERS)
        try:
            with open(INFRA_PATH) as f:
                d.update({k: str(v) for k, v in (json.load(f) or {}).items()})
        except Exception:  # noqa: BLE001
            pass
        _infra_cache.update(t=time.time(), d=d)
    return _infra_cache["d"]


def infra_label(addr, extra=None):
    if not addr:
        return None
    d = infra_list()
    if addr in d:
        return d[addr]
    if extra and addr in extra:
        return extra[addr]
    for a, b, lab in INFRA_PATTERNS:
        if addr.startswith(a) and addr.endswith(b):
            return lab
    return None


def save_infra(new):
    if not new:
        return
    try:
        cur = {}
        if os.path.exists(INFRA_PATH):
            with open(INFRA_PATH) as f:
                cur = json.load(f) or {}
        cur.update(new)
        tmp = INFRA_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cur, f, indent=1, sort_keys=True)
        os.replace(tmp, INFRA_PATH)
        _infra_cache["t"] = 0.0
    except Exception:  # noqa: BLE001
        pass


class _UF:
    def __init__(self):
        self.p = {}

    def f(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def u(self, a, b):
        ra, rb = self.f(a), self.f(b)
        if ra != rb:
            self.p[rb] = ra


def analyze(rows, infra=None, funder_of_funder=None, cfg=None, swarm=None, distributors=None):
    """rows: [{o, pct, ntx, src, ts, slot}] (src/ts/slot solo per i wallet nuovi).
    infra: {indirizzo: etichetta} dei funder infrastrutturali (oltre alla lista nota).
    funder_of_funder: {funder: suo finanziatore} per i 2 salti. Funzione pura."""
    c = dict(CFG)
    c.update(cfg or {})
    infra = infra or {}
    fof = funder_of_funder or {}
    holders = {r["o"] for r in rows}
    dist = set(distributors or ())
    is_infra = lambda a: a not in dist and bool(infra_label(a, infra))  # noqa: E731
    uf_true, uf_inf = _UF(), _UF()
    reasons = {}
    by_src = {}
    for r in rows:
        s = r.get("src")
        if s:
            by_src.setdefault(s, []).append(r["o"])
    for s, ws in by_src.items():
        if is_infra(s):
            for w in ws[1:]:
                uf_inf.u(ws[0], w)
            continue
        if s in holders:   # trasferimento diretto da un altro holder
            for w in ws:
                uf_true.u(s, w)
            reasons["holder_funded"] = reasons.get("holder_funded", 0) + len(ws)
        if len(ws) >= 2:
            for w in ws[1:]:
                uf_true.u(ws[0], w)
            reasons["common_funder"] = reasons.get("common_funder", 0) + len(ws)
    # 2 salti: funder diversi (non infra) con lo stesso finanziatore (non infra)
    grand = {}
    for s, ws in by_src.items():
        g = fof.get(s)
        if g and not is_infra(s) and not is_infra(g):
            grand.setdefault(g, []).append(ws[0])
            if g in holders:
                uf_true.u(g, ws[0])
    for g, firsts in grand.items():
        if len(firsts) >= 2:
            for w in firsts[1:]:
                uf_true.u(firsts[0], w)
            reasons["two_hop"] = reasons.get("two_hop", 0) + len(firsts)
    # wallet nuovi finanziati nello stesso slot/minuto (anche da exchange: ritiri in serie di una sola persona)
    fresh_t = sorted((r["ts"], r.get("slot") or 0, r["o"]) for r in rows if r.get("ts"))
    i = 0
    while i < len(fresh_t):
        j = i
        while j + 1 < len(fresh_t) and fresh_t[j + 1][0] - fresh_t[i][0] <= c["SAME_MIN_SEC"]:
            j += 1
        if j - i + 1 >= c["SAME_MIN_GROUP"]:
            for k in range(i + 1, j + 1):
                uf_true.u(fresh_t[i][2], fresh_t[k][2])
            reasons["same_minute"] = reasons.get("same_minute", 0) + (j - i + 1)
        i = j + 1
    # prima acquisizione del token: fee payer comune, token ricevuti da un altro wallet, stesso slot
    by_payer, by_from = {}, {}
    for r in rows:
        a = r.get("acq") or {}
        if a.get("payer"):
            by_payer.setdefault(a["payer"], []).append(r["o"])
        if a.get("from"):
            by_from.setdefault(a["from"], []).append(r["o"])
    for pyr, ws in by_payer.items():
        if is_infra(pyr):      # relayer di un'app (fomo...) o servizio: solo gruppo "infra"
            for w in ws[1:]:
                uf_inf.u(ws[0], w)
            continue
        if pyr in holders:
            for w in ws:
                uf_true.u(pyr, w)
            reasons["holder_paid_fee"] = reasons.get("holder_paid_fee", 0) + len(ws)
        if len(ws) >= 2:       # stesso fee payer NON infrastrutturale = stesso operatore
            for w in ws[1:]:
                uf_true.u(ws[0], w)
            reasons["common_fee_payer"] = reasons.get("common_fee_payer", 0) + len(ws)
    for snd, ws in by_from.items():
        # chi CEDE i token a tanti holder non e mai "infrastruttura" scoperta in automatico (solo lista nota CEX)
        if snd in INFRA_FUNDERS or any(snd.startswith(a) and snd.endswith(b) for a, b, _ in INFRA_PATTERNS):
            for w in ws[1:]:
                uf_inf.u(ws[0], w)
            continue
        if snd in holders:
            for w in ws:
                uf_true.u(snd, w)
            reasons["token_from_holder"] = reasons.get("token_from_holder", 0) + len(ws)
        elif len(ws) >= 2:
            for w in ws[1:]:
                uf_true.u(ws[0], w)
            reasons["common_token_sender"] = reasons.get("common_token_sender", 0) + len(ws)
    acq_s = sorted(((r["acq"]["slot"], r["o"]) for r in rows if (r.get("acq") or {}).get("slot")))
    i = 0
    while i < len(acq_s):
        j = i
        while j + 1 < len(acq_s) and acq_s[j + 1][0] - acq_s[i][0] <= 1:
            j += 1
        if j - i + 1 >= c["SAME_SLOT_GROUP"]:
            for k in range(i + 1, j + 1):
                uf_true.u(acq_s[i][1], acq_s[k][1])
            reasons["same_slot_buy"] = reasons.get("same_slot_buy", 0) + (j - i + 1)
        i = j + 1
    pct = {r["o"]: float(r.get("pct") or 0) for r in rows}
    fresh = {r["o"] for r in rows if r.get("ntx") is not None and r["ntx"] < c["FRESH_TX"]}

    def groups(uf, extra_infra=False):
        g = {}
        for r in rows:
            g.setdefault(uf.f(r["o"]), []).append(r["o"])
        out = [(sum(pct[w] for w in ws), len(ws), ws) for ws in g.values() if len(ws) >= c["NET_MIN_WALLETS"]]
        return sorted(out, reverse=True)

    tg = groups(uf_true)
    # gruppi "con infrastruttura": collegamenti veri + collegamenti via hot wallet/fee payer
    uf_all = _UF()
    for r in rows:
        uf_all.u(uf_true.f(r["o"]), r["o"])
        uf_all.u(uf_inf.f(r["o"]), r["o"])
    ag = groups(uf_all)
    max_true = round(tg[0][0], 3) if tg else 0.0
    max_true_n = tg[0][1] if tg else 0
    linked_pct = round(sum(x[0] for x in tg), 3)
    linked_n = sum(x[1] for x in tg)
    max_all = ag[0] if ag else (0.0, 0, [])
    infra_pct = round(max(0.0, max_all[0] - (max_true if tg and set(tg[0][2]) <= set(max_all[2]) else 0.0)), 3)
    all_fresh = round(sum(1 for w in max_all[2] if w in fresh) / max_all[1], 3) if max_all[1] else 0.0
    scanned = len(rows)
    fresh_share = round(len(fresh) / scanned, 3) if scanned else 0.0
    infra_used = sorted({infra_label(s, infra) for s in list(by_src) + list(by_payer) + list(by_from)
                         if is_infra(s)} - {None})
    why, level = [], "none"
    if max_true >= c["GROUP_HARD_PCT"]:
        why.append(f"un gruppo di {max_true_n} wallet collegati tiene il {max_true:.1f}% dell'offerta")
    if linked_pct >= c["LINKED_HARD_PCT"]:
        why.append(f"wallet collegati in tutto al {linked_pct:.1f}%")
    if max_true_n >= c["BIG_GROUP_WALLETS"] and max_true >= c["BIG_GROUP_PCT"]:
        why.append(f"gruppo enorme: {max_true_n} wallet con il {max_true:.1f}%")
    if max_all[0] >= c["INFRA_GROUP_ALWAYS_PCT"] or (
            max_all[0] >= c["INFRA_GROUP_HARD_PCT"] and (all_fresh >= c["INFRA_GROUP_MIN_FRESH"] or max_true >= 3)):
        why.append(f"gruppo collegato anche via exchange/app ({max_all[1]} wallet, {all_fresh * 100:.0f}% nuovi) "
                   f"al {max_all[0]:.1f}%")
    sw = swarm or {}
    sw_pct, sw_n = float(sw.get("pct") or 0), int(sw.get("wallets") or 0)
    sw_in_group = bool(tg) and any(r.get("swarm") for r in rows if r["o"] in set(tg[0][2]))
    if sw_pct >= c["SWARM_HARD_PCT"] and sw_n >= c["SWARM_HARD_WALLETS"]:
        why.append(f"{sw_n} wallet con saldo identico (wallet fantoccio) tengono il {sw_pct:.1f}%")
    if why:
        level = "hard"
    else:
        if max_true >= c["GROUP_SOFT_PCT"]:
            why.append(f"gruppo collegato al {max_true:.1f}% ({max_true_n} wallet)")
        if linked_pct >= c["LINKED_SOFT_PCT"]:
            why.append(f"collegati veri al {linked_pct:.1f}%")
        if max_all[0] >= c["INFRA_SOFT_PCT"]:
            why.append(f"gruppo via exchange/app al {max_all[0]:.1f}% (non insider di per se)")
        if scanned >= 10 and fresh_share >= c["FRESH_SOFT"]:
            why.append(f"{fresh_share * 100:.0f}% top holder nuovi")
        if sw_pct >= c["SWARM_SOFT_PCT"]:
            why.append(f"{sw_n} wallet con saldo identico al {sw_pct:.1f}%")
        level = "soft" if why else "none"
    covered = round(sum(pct.values()), 3)
    return {"level": level, "why": "; ".join(why), "max_group_pct": max_true, "max_group_wallets": max_true_n,
            "linked_pct": linked_pct, "linked_wallets": linked_n, "groups": [(round(p, 3), n) for p, n, _ in tg[:6]],
            "cluster_infra_pct": infra_pct, "infra_group_pct": round(max_all[0], 3),
            "infra_group_wallets": max_all[1], "infra_group_fresh": all_fresh, "infra_used": infra_used,
            "fresh_share": fresh_share, "scanned": scanned, "swarm_pct": round(sw_pct, 3), "swarm_wallets": sw_n,
            "swarm_values": sw.get("values", [])[:4], "swarm_in_group": sw_in_group,
            "cluster_pct": round(max(max_true, sw_pct if sw_in_group else 0.0), 3), "covered_pct": covered, "reasons": reasons,
            # compatibilita con il formato v7 (signals/log)
            "networks": len(tg), "net_pct": linked_pct, "wallets": linked_n}



RPC_GPA = "https://api.mainnet-beta.solana.com"         # getProgramAccounts (PublicNode lo vuole a pagamento)
RPC_FAST = ["https://solana-rpc.publicnode.com", "https://api.mainnet-beta.solana.com"]


class _Rpc:
    def __init__(self, post, urls, gap):
        self.post, self.urls, self.gap, self.last, self.calls, self.fails = post, list(urls), gap, 0.0, 0, 0
        self.lock = asyncio.Lock()

    async def __call__(self, method, params, url=None):
        for attempt in range(3):
            async with self.lock:
                dt = time.time() - self.last
                if dt < self.gap:
                    await asyncio.sleep(self.gap - dt)
                self.last = time.time()
            self.calls += 1
            u = url or self.urls[min(attempt, len(self.urls) - 1)]
            d = await self.post(u, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
            if isinstance(d, dict) and "result" in d:
                return d["result"]
            await asyncio.sleep(0.6 * (attempt + 1))
        self.fails += 1
        return None


def _b58(b):
    a = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    n, out = int.from_bytes(b, "big"), ""
    while n:
        n, r = divmod(n, 58)
        out = a[r] + out
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + out


def _holders_out(per, main_acc, tot, top_n, min_pct):
    tot = tot or 1
    rows = sorted(((amt / tot * 100, o) for o, amt in per.items()), reverse=True)
    # v8.2: "sciame" = tanti wallet con lo STESSO saldo esatto (distribuzione a wallet fantoccio)
    same = {}
    for o, amt in per.items():
        same.setdefault(amt, []).append(o)
    vals = [(amt, ws) for amt, ws in same.items() if len(ws) >= CFG["SWARM_MIN_WALLETS"]
            and amt / tot * 100 >= CFG["SWARM_MIN_PCT"]]
    swarm = {"pct": round(sum(amt * len(ws) for amt, ws in vals) / tot * 100, 3),
             "wallets": sum(len(ws) for _, ws in vals),
             "values": sorted(((round(amt / tot * 100, 5), len(ws)) for amt, ws in vals),
                              key=lambda x: -x[0] * x[1])[:6],
             "owners": {o for _, ws in vals for o in ws}}
    return [(o, p, main_acc[o][0]) for p, o in rows if p >= min_pct][:top_n], len(per), swarm


def _add(per, main_acc, excl, owner, acc, amt):
    if owner in excl or acc in excl or amt <= 0:
        return
    per[owner] = per.get(owner, 0) + amt
    if amt > main_acc.get(owner, ("", 0))[1]:
        main_acc[owner] = (acc, amt)


async def top_holders(rpc, mint, exclude=(), token_program=None, top_n=150, min_pct=0.0, das_pages=10):
    """([(owner, % dell'offerta, token account principale)], n. holder, sciame). Quota sul totale dei saldi (pool
    incluse, come Bubblemaps); pool/curve/locker (exclude) tolti dalla lista. v8.2: getProgramAccounts (Helius se
    c'e la chiave, poi RPC pubblico); se fallisce, DAS getTokenAccounts di Helius (1000 account per pagina)."""
    excl = set(exclude or [])
    progs = [token_program] if token_program else ["TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                                                   "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"]
    for prog in progs:
        flt = [{"memcmp": {"offset": 0, "bytes": mint}}]
        if prog.startswith("Tokenkeg"):
            flt.append({"dataSize": 165})
        acc = await rpc("getProgramAccounts", [prog, {"encoding": "base64", "filters": flt,
                                                      "dataSlice": {"offset": 32, "length": 40}}], url=RPC_GPA)
        if isinstance(acc, list) and acc:
            per, main_acc, tot = {}, {}, 0
            for a in acc:
                try:
                    d = base64.b64decode(a["account"]["data"][0])
                    o, amt = _b58(d[:32]), int.from_bytes(d[32:40], "little")
                except Exception:  # noqa: BLE001
                    continue
                tot += amt
                _add(per, main_acc, excl, o, a.get("pubkey"), amt)
            return _holders_out(per, main_acc, tot, top_n, min_pct)
    # ripiego DAS (solo Helius): pagine da 1000 account
    per, main_acc, tot, complete = {}, {}, 0, False
    for page in range(1, int(das_pages) + 1):
        r = await rpc("getTokenAccounts", {"mint": mint, "page": page, "limit": 1000}, url=RPC_GPA)
        rows = (r or {}).get("token_accounts") if isinstance(r, dict) else None
        if not isinstance(rows, list):
            break
        for t in rows:
            amt = int(t.get("amount") or 0)
            tot += amt
            _add(per, main_acc, excl, t.get("owner"), t.get("address"), amt)
        if len(rows) < 1000:
            complete = True
            break
    if not per:
        return None, 0, None
    if not complete:   # holder oltre le pagine lette: la quota va sulla supply intera
        sup = await rpc("getTokenSupply", [mint])
        try:
            tot = max(tot, int(((sup or {}).get("value") or {}).get("amount")))
        except Exception:  # noqa: BLE001
            pass
    return _holders_out(per, main_acc, tot, top_n, min_pct)


def _keys(msg):
    return [k["pubkey"] if isinstance(k, dict) else k for k in msg.get("accountKeys") or []]


async def _funding(rpc, owner, sig):
    """Prima transazione di un wallet: (finanziatore, blockTime, slot)."""
    tx = await rpc("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
    if not isinstance(tx, dict):
        return None, None, None
    src = None
    try:
        msg = tx["transaction"]["message"]
        ins = list(msg.get("instructions") or [])
        for ii in (tx.get("meta") or {}).get("innerInstructions") or []:
            ins += ii.get("instructions") or []
        for x in ins:
            p = x.get("parsed") if isinstance(x, dict) else None
            info = (p or {}).get("info") or {} if isinstance(p, dict) else {}
            if isinstance(p, dict) and "lamports" in info and \
                    owner in (info.get("destination"), info.get("newAccount")):
                src = info.get("source")
                break
        ks = _keys(msg)
        if src is None and ks and ks[0] != owner:
            src = ks[0]
    except Exception:  # noqa: BLE001
        pass
    return src, tx.get("blockTime"), tx.get("slot")


async def _acquisition(rpc, mint, owner, token_acc, excl):
    """Prima transazione del token account: come il wallet ha avuto il token.
    -> {slot, ts, payer (fee payer se diverso dal wallet), from (proprietario che ha ceduto i token se non e una
    pool), sig}"""
    sigs = await rpc("getSignaturesForAddress", [token_acc, {"limit": 100}])
    if not isinstance(sigs, list) or not sigs:
        return None
    first = sigs[-1]
    out = {"slot": first.get("slot"), "ts": first.get("blockTime"), "payer": None, "from": None,
           "sig": first.get("signature"), "complete": len(sigs) < 100}
    tx = await rpc("getTransaction", [first["signature"], {"encoding": "jsonParsed",
                                                            "maxSupportedTransactionVersion": 0}])
    if not isinstance(tx, dict):
        return out
    try:
        ks = _keys(tx["transaction"]["message"])
        if ks and ks[0] != owner:
            out["payer"] = ks[0]
        meta = tx.get("meta") or {}
        pre = {(b.get("owner"), b.get("accountIndex")): float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0)
               for b in meta.get("preTokenBalances") or [] if b.get("mint") == mint}
        post = {(b.get("owner"), b.get("accountIndex")): float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0)
                for b in meta.get("postTokenBalances") or [] if b.get("mint") == mint}
        senders = []
        for k in set(pre) | set(post):
            dlt = post.get(k, 0.0) - pre.get(k, 0.0)
            if dlt < 0 and k[0] and k[0] != owner:
                acc_k = ks[k[1]] if k[1] is not None and k[1] < len(ks) else None
                senders.append((dlt, k[0], acc_k))
        senders.sort()
        if senders:
            _, so, sa = senders[0]
            if so not in excl and sa not in excl:
                out["from"] = so
            else:
                out["swap"] = True
        out["n_recv"] = sum(1 for k in set(post) if post.get(k, 0) - pre.get(k, 0) > 0)
    except Exception:  # noqa: BLE001
        pass
    return out


async def scan(post, mint, rpc_urls=None, exclude=(), token_program=None, cfg=None, log=None):
    """Analisi completa. Ritorna dict (vedi analyze) con 'level'; 'unknown' se dati insufficienti o tempo
    scaduto (il chiamante NON deve trattarlo come ok). Non solleva eccezioni di rete."""
    c = dict(CFG)
    c.update(cfg or {})
    t0 = time.time()
    rpc = _Rpc(post, rpc_urls or RPC_FAST, c["CALL_GAP"])
    unk = lambda why, **k: dict({"level": "unknown", "why": why, "calls": rpc.calls,  # noqa: E731
                                 "sec": round(time.time() - t0, 1)}, **k)
    excl = set(exclude or [])
    holders, n_hold, swarm = await top_holders(rpc, mint, excl, token_program, int(c["TOP_N"]), c["MIN_PCT"])
    if not holders:
        return unk("lista holder non disponibile dalla RPC")
    rows = {o: {"o": o, "pct": p, "acc": a, "ntx": None, "src": None, "ts": None, "slot": None}
            for o, p, a in holders}
    sem = asyncio.Semaphore(6)

    async def acq(o):
        async with sem:
            if time.time() - t0 > c["BUDGET_SEC"]:
                return
            r = rows[o]
            a = await _acquisition(rpc, mint, o, r["acc"], excl)
            if a:
                r["acq"] = a

    async def wal(o):
        async with sem:
            if time.time() - t0 > c["BUDGET_SEC"]:
                return
            r = rows[o]
            sigs = await rpc("getSignaturesForAddress", [o, {"limit": int(c["FRESH_TX"])}])
            if not isinstance(sigs, list):
                return
            r["ntx"] = len(sigs)
            if sigs and len(sigs) < c["FRESH_TX"]:
                r["src"], r["ts"], r["slot"] = await _funding(rpc, o, sigs[-1]["signature"])
    await asyncio.gather(*(acq(o) for o, _, _ in holders),
                         *(wal(o) for o, _, _ in holders[:int(c["WALLET_TOP_N"])]))
    done = sum(1 for r in rows.values() if r.get("acq"))
    if done < max(5, int(0.6 * len(holders))):
        return unk(f"solo {done}/{len(holders)} holder letti (RPC lenta o in 429)", scanned=done,
                   timeout=time.time() - t0 > c["BUDGET_SEC"])
    # funder e fee payer piu usati: infrastruttura (attivita altissima) o finanziatore del funder (2 salti)
    cnt = {}
    for r in rows.values():
        for s in (r.get("src"), (r.get("acq") or {}).get("payer")):
            if s:
                cnt[s] = cnt.get(s, 0) + 1
    found_infra, fof, distributors = {}, {}, {}
    # chi ha MANDATO i token a >= 3 holder e un distributore di questo token, mai infrastruttura
    snd = {}
    for r in rows.values():
        f = (r.get("acq") or {}).get("from")
        if f:
            snd[f] = snd.get(f, 0) + 1
    for f, n in snd.items():
        if n >= 3 and f not in INFRA_FUNDERS:
            distributors[f] = n
    for s, n in sorted(cnt.items(), key=lambda x: -x[1])[:int(c["MAX_FUNDER_LOOKUPS"])]:
        if time.time() - t0 > c["BUDGET_SEC"] + 20 or s in distributors or infra_label(s):
            continue
        sigs = await rpc("getSignaturesForAddress", [s, {"limit": 1000}])
        if not isinstance(sigs, list):
            continue
        if len(sigs) >= 1000 and sigs[0].get("blockTime") and sigs[-1].get("blockTime") and \
                sigs[0]["blockTime"] - sigs[-1]["blockTime"] <= 3600 * 1000 / c["HUB_TX_HOUR"]:
            # hub molto attivo: e un servizio (tanti token diversi) o un distributore DEDICATO a questo token?
            hits = 0
            for sg in sigs[:3] + sigs[500:502]:   # piu recenti + meta finestra
                tx = await rpc("getTransaction", [sg["signature"], {"encoding": "jsonParsed",
                                                                   "maxSupportedTransactionVersion": 0}])
                bals = ((tx or {}).get("meta") or {}).get("postTokenBalances") or []
                hits += any(b.get("mint") == mint for b in bals)
            if hits >= 3:
                distributors[s] = hits
            else:
                found_infra[s] = "hub (>=1000 tx/h)"
            continue
        if sigs and len(sigs) < 1000:
            g, _, _ = await _funding(rpc, s, sigs[-1]["signature"])
            if g:
                fof[s] = g
    save_infra(found_infra)
    for r in rows.values():
        r["swarm"] = r["o"] in (swarm or {}).get("owners", ())
    res = analyze(list(rows.values()), found_infra, fof, c, swarm=swarm, distributors=distributors)
    res.update(calls=rpc.calls, sec=round(time.time() - t0, 1), holders_total=n_hold, acq_read=done,
               new_infra=sorted(found_infra), distributors=sorted(distributors),
               top_funders=[(s[:6] + ".." + s[-4:], n, infra_label(s, found_infra))
                            for s, n in sorted(cnt.items(), key=lambda x: -x[1])[:8]])
    if c.get("KEEP_ROWS"):
        res["_rows"], res["_fof"] = list(rows.values()), fof
    if log:
        log(res)
    return res
