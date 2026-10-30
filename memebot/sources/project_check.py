"""
project_check.py - controllo VELOCE della qualita' del progetto di un token (sito, social, GitHub, metadati).
PAPER / sola lettura. Solo stdlib. Tutto gratis: DexScreener (link), metadati on-chain da RugCheck (gia' scaricati
dal bot) + JSON dei metadati (IPFS/Arweave), sito del progetto, RDAP per l'eta' del dominio, API pubblica GitHub
senza chiave (60 chiamate/ora, con cache e rispetto del rate limit).

Da usare SOLO sui candidati che hanno gia' superato i filtri HARD. Budget: ~4,5 s per token (tutte le richieste
in parallelo con una scadenza unica); quello che non arriva in tempo viene ignorato (tag proj_partial).
Non solleva mai eccezioni. Non blocca mai un trade: produce solo un punteggio e dei tag.

Uso:
    from sources.project_check import check, acheck, project_bonus
    r = check(mint, symbol="DESSA", name="Dessa", links=None, token_meta=None, is_pump=None)
    r -> {"score": 0..1, "tags": [...], "site": {...}, "github": {...}, "links": {...}, "elapsed": s, ...}
    bonus, tags = project_bonus(r, cfg)   # cfg: PROJECT_BONUS, PROJECT_EMPTY_PENALTY, PROJECT_MIN_SCORE

Tag principali: proj_site, proj_site_down, proj_template, proj_small, proj_nosite, proj_spa, proj_builder,
proj_freehost, proj_domain_new:<giorni>, proj_domain_old, proj_x, proj_tg, proj_nosocials, proj_empty,
proj_github, proj_github_active, proj_github_stale, proj_github_ratelimited, proj_utility, proj_meme,
proj_meta_ok, proj_meta_mismatch, proj_meta_mutable, proj_partial, proj_score:<x>
"""
from __future__ import annotations

import asyncio
import concurrent.futures as cf
import html as _html
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

UA = "Mozilla/5.0 (X11; Linux x86_64) DeskMemecoin-paper/1.0"
DEX = "https://api.dexscreener.com/tokens/v1/solana/{}"
DEADLINE_SEC = 4.5          # budget totale per token
SITE_MAX_BYTES = 250_000
META_MAX_BYTES = 64_000
CACHE_SEC = 1800            # risultato completo per mint
LINKS_CACHE_SEC = 600
DOMAIN_CACHE_SEC = 86400
GITHUB_CACHE_SEC = 6 * 3600
FRESH_DOMAIN_DAYS = 14
OLD_DOMAIN_DAYS = 60
GITHUB_ACTIVE_DAYS = 30
GITHUB_MIN_REMAINING = 5    # lascia sempre qualche chiamata libera
_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(_DIR, "project_check_cache.json")

_lock = threading.Lock()
_cache: dict = {}           # mint -> (ts, result)
_links: dict = {}           # mint -> (ts, links, dex_meta)
_domains: dict = {}         # domain -> (ts, age_days|None)
_github: dict = {}          # owner/repo -> (ts, info)
_gh_rate = {"remaining": 60, "reset": 0.0}
_pool = cf.ThreadPoolExecutor(max_workers=6, thread_name_prefix="projchk")
_loaded = [False]

FREE_HOSTS = ("vercel.app", "netlify.app", "github.io", "pages.dev", "carrd.co", "linktr.ee", "webflow.io",
              "framer.website", "framer.app", "wixsite.com", "gitbook.io", "notion.site", "replit.app", "web.app",
              "firebaseapp.com", "onrender.com", "herokuapp.com", "glitch.me", "bio.link", "beacons.ai",
              "super.site", "mystrikingly.com", "weebly.com", "blogspot.com", "medium.com", "substack.com")
NOT_A_SITE = ("x.com", "twitter.com", "t.me", "telegram.me", "discord.gg", "discord.com", "pump.fun",
              "dexscreener.com", "birdeye.so", "solscan.io", "instagram.com", "tiktok.com", "youtube.com",
              "youtu.be", "facebook.com", "reddit.com", "gmgn.ai", "photon-sol.tinyastro.io")
PARKED = ("domain is for sale", "this domain is for sale", "buy this domain", "domain may be for sale",
          "is parked", "parked free", "parkingcrew", "sedoparking", "sedo.com", "hugedomains", "dan.com",
          "afternic", "godaddy.com/domains", "domain has expired", "this domain has expired", "namecheap parking",
          "coming soon", "under construction", "welcome to nginx", "apache2 ubuntu default page",
          "it works!", "default web page", "index of /", "site not found", "deployment not found",
          "404: not_found", "there isn't a github pages site here", "lorem ipsum", "your token name",
          "token name here", "insert contract address", "ca: coming", "contract address: tba")
BUILDERS = ("carrd", "wix.com", "framer", "webflow", "squarespace", "linktree", "beacons", "notion", "gitbook",
            "wordpress", "durable.co", "hostinger", "site123")
UTILITY = ("dapp", "protocol", "staking", "stake ", "bridge", "sdk", " api", "whitepaper", "litepaper", "docs",
           "documentation", "roadmap", "platform", "launch app", "open app", "dashboard", "tokenomics", "defi",
           "swap", "dex ", "ai agent", "agent framework", "infrastructure", "audit", "github", "testnet", "mainnet",
           "telegram bot", "trading bot", "analytics", "marketplace", "game", "play to earn", "nft", "oracle",
           "wallet", "privacy", "zk", "depin", "rwa", "yield", "lending", "app.", "beta")
MEME = ("meme", "memecoin", "no utility", "zero utility", "just a meme", "for the culture", "community token",
        "community-driven", "community driven", "vibes", "to the moon", "moon", "lfg", "degen", "pepe", "doge",
        "wif", "bonk", "frog", "cat coin", "dog coin", "send it", "100x", "1000x")
GH_RE = re.compile(r"https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+)(?:/([A-Za-z0-9_.-]+))?", re.I)


# ----------------------------------------------------------------------------------------------- utilita'
def _now():
    return time.time()


def _get(url, timeout, max_bytes, headers=None):
    """GET con timeout e tetto di byte. Ritorna (status, bytes, headers, final_url, errore)."""
    h = {"User-Agent": UA, "Accept": "*/*"}
    h.update(headers or {})
    try:
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=max(0.5, timeout)) as r:
            return r.status, r.read(max_bytes), dict(r.headers), r.geturl(), None
    except urllib.error.HTTPError as e:
        try:
            body = e.read(4000)
        except Exception:  # noqa: BLE001
            body = b""
        return e.code, body, dict(e.headers or {}), url, f"http {e.code}"
    except Exception as e:  # noqa: BLE001
        return 0, b"", {}, url, type(e).__name__


def _host(url):
    try:
        h = urllib.parse.urlparse(url if "://" in url else "https://" + url).hostname or ""
        return h.lower().removeprefix("www.")
    except Exception:  # noqa: BLE001
        return ""


def _is_social(url, kinds):
    h = _host(url)
    return any(h == k or h.endswith("." + k) for k in kinds)


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _load_disk():
    if _loaded[0]:
        return
    _loaded[0] = True
    try:
        d = json.load(open(CACHE_PATH))
        now = _now()
        for k, v in (d.get("domains") or {}).items():
            if now - v[0] < DOMAIN_CACHE_SEC:
                _domains[k] = tuple(v)
        for k, v in (d.get("github") or {}).items():
            if now - v[0] < GITHUB_CACHE_SEC:
                _github[k] = tuple(v)
        _gh_rate.update(d.get("gh_rate") or {})
    except Exception:  # noqa: BLE001
        pass


def _save_disk():
    try:
        with _lock:
            d = {"domains": dict(_domains), "github": dict(_github), "gh_rate": dict(_gh_rate)}
        tmp = CACHE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f)
        os.replace(tmp, CACHE_PATH)
    except Exception:  # noqa: BLE001
        pass


# ----------------------------------------------------------------------------------------------- fonti
def dex_links(mint, timeout=2.5):
    """Link (siti, social) dal pair DexScreener piu' liquido. Cache 10 min."""
    hit = _links.get(mint)
    if hit and _now() - hit[0] < LINKS_CACHE_SEC:
        return hit[1], hit[2]
    st, body, _, _, err = _get(DEX.format(mint), timeout, 2_000_000)
    links, meta = {"websites": [], "socials": []}, {}
    if st == 200:
        try:
            pairs = json.loads(body)
            best = max(pairs, key=lambda p: ((p.get("liquidity") or {}).get("usd") or 0)) if pairs else {}
            info = best.get("info") or {}
            links = links_from_info(info)
            bt = best.get("baseToken") or {}
            meta = {"symbol": bt.get("symbol"), "name": bt.get("name")}
        except Exception:  # noqa: BLE001
            pass
    _links[mint] = (_now(), links, meta)
    return links, meta


def links_from_info(info):
    """info DexScreener -> {"websites": [url], "socials": [{"type","url"}]}."""
    ws = [str(w.get("url")) for w in (info.get("websites") or []) if isinstance(w, dict) and w.get("url")]
    so = [{"type": str(s.get("type") or s.get("platform") or "").lower(), "url": str(s.get("url") or "")}
          for s in (info.get("socials") or []) if isinstance(s, dict)]
    return {"websites": ws[:4], "socials": [s for s in so if s["url"] or s["type"]][:6]}


def fetch_metadata_json(uri, timeout):
    """JSON dei metadati del token (pump.fun: name, symbol, description, twitter, telegram, website)."""
    if not uri or not str(uri).startswith(("http://", "https://", "ipfs://", "ar://")):
        return None
    u = str(uri)
    if u.startswith("ipfs://"):
        u = "https://ipfs.io/ipfs/" + u[7:]
    elif u.startswith("ar://"):
        u = "https://arweave.net/" + u[5:]
    st, body, _, _, _ = _get(u, timeout, META_MAX_BYTES, {"Accept": "application/json"})
    if st != 200:
        return None
    try:
        d = json.loads(body)
        return d if isinstance(d, dict) else None
    except Exception:  # noqa: BLE001
        return None


def analyze_html(raw, final_url=""):
    """Analisi pura (testabile offline) di una pagina: testo visibile, template/parcheggio, SPA, builder, link."""
    text_html = raw.decode("utf-8", "ignore") if isinstance(raw, (bytes, bytearray)) else str(raw or "")
    low = text_html.lower()
    title = ""
    mt = re.search(r"<title[^>]*>(.*?)</title>", text_html, re.I | re.S)
    if mt:
        title = _html.unescape(re.sub(r"\s+", " ", mt.group(1))).strip()[:120]
    desc = ""
    md = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']*)', text_html, re.I)
    if md:
        desc = _html.unescape(md.group(1))[:300]
    body = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", text_html)
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    visible = re.sub(r"\s+", " ", _html.unescape(body)).strip()
    n_scripts = len(re.findall(r"<script\b", low))
    n_links = len(re.findall(r"<a\b", low))
    spa = len(visible) < 400 and (n_scripts >= 2 or 'id="root"' in low or 'id="__next"' in low or
                                   'id="app"' in low or "/_next/" in low or "type=\"module\"" in low)
    parked = [p for p in PARKED if p in low]
    builder = next((b for b in BUILDERS if b in low), None)
    small = len(text_html) < 1500 or (len(visible) < 200 and not spa)
    gh = sorted({f"{a}/{b}" if b else a for a, b in GH_RE.findall(text_html)
                 if a.lower() not in ("features", "about", "pricing", "login", "sponsors", "topics", "orgs")})[:3]
    return {"bytes": len(text_html), "text_len": len(visible), "title": title, "desc": desc,
            "parked": parked[:3], "builder": builder, "spa": spa, "small": small, "links": n_links,
            "scripts": n_scripts, "github": gh, "text": (title + " " + desc + " " + visible)[:6000]}


def check_site(url, timeout):
    st, body, hdr, final, err = _get(url, timeout, SITE_MAX_BYTES, {"Accept": "text/html,*/*"})
    out = {"url": url, "final": final, "status": st, "ok": 200 <= st < 400 and bool(body), "error": err}
    if body and st and st < 500:
        out.update(analyze_html(body, final))
    return out


def domain_age_days(domain, timeout):
    """Eta' del dominio da RDAP (rdap.org, gratis). None se non disponibile. Cache 24 h (anche su disco)."""
    if not domain:
        return None
    parts = domain.split(".")
    reg = ".".join(parts[-2:]) if len(parts) >= 2 else domain
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in ("co", "com", "org", "net", "gov", "ac"):
        reg = ".".join(parts[-3:])
    hit = _domains.get(reg)
    if hit and _now() - hit[0] < DOMAIN_CACHE_SEC:
        return hit[1]
    st, body, _, _, _ = _get("https://rdap.org/domain/" + reg, timeout, 200_000,
                             {"Accept": "application/rdap+json, application/json"})
    age = None
    if st == 200:
        try:
            d = json.loads(body)
            for e in d.get("events") or []:
                if str(e.get("eventAction", "")).lower() == "registration":
                    t = datetime.fromisoformat(str(e["eventDate"]).replace("Z", "+00:00"))
                    age = round((datetime.now(timezone.utc) - t).total_seconds() / 86400, 1)
                    break
        except Exception:  # noqa: BLE001
            age = None
    if st in (200, 404):      # 404 = RDAP non supportato per quel TLD: inutile riprovare
        with _lock:
            _domains[reg] = (_now(), age)
    return age


def _gh_api(path, timeout):
    if _gh_rate["remaining"] <= GITHUB_MIN_REMAINING and _now() < _gh_rate["reset"]:
        return None, "ratelimited"
    st, body, hdr, _, err = _get("https://api.github.com" + path, timeout, 500_000,
                                 {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
    h = {k.lower(): v for k, v in (hdr or {}).items()}
    try:
        if "x-ratelimit-remaining" in h:
            _gh_rate["remaining"] = int(h["x-ratelimit-remaining"])
            _gh_rate["reset"] = float(h.get("x-ratelimit-reset") or 0)
    except ValueError:
        pass
    if st in (403, 429):
        _gh_rate["remaining"] = 0
        _gh_rate["reset"] = max(_gh_rate["reset"], _now() + 900)
        return None, "ratelimited"
    if st != 200:
        return None, f"http {st or err}"
    try:
        return json.loads(body), None
    except Exception:  # noqa: BLE001
        return None, "json"


def github_activity(owner, repo, timeout):
    """Attivita' pubblica: repo (o i 5 repo spinti di recente dell'owner) e commit negli ultimi 30 giorni.
    Max 2 chiamate per owner/repo, cache 6 h, si ferma se restano <= 5 chiamate nell'ora."""
    key = f"{owner}/{repo or ''}".lower()
    hit = _github.get(key)
    if hit and _now() - hit[0] < GITHUB_CACHE_SEC:
        return hit[1]
    t0 = _now()
    info = {"owner": owner, "repo": repo, "exists": False, "pushed_days": None, "commits_30d": None,
            "stars": 0, "fork": False, "error": None}
    if repo:
        d, err = _gh_api(f"/repos/{owner}/{repo}", timeout)
        repos = [d] if isinstance(d, dict) else []
    else:
        d, err = _gh_api(f"/users/{owner}/repos?sort=pushed&per_page=5", timeout)
        repos = [r for r in d if isinstance(r, dict)] if isinstance(d, list) else []
    if err:
        info["error"] = err
    if repos:
        own = [r for r in repos if not r.get("fork")] or repos
        best = max(own, key=lambda r: str(r.get("pushed_at") or ""))
        info.update(exists=True, repo=best.get("name"), fork=bool(best.get("fork")),
                    stars=int(best.get("stargazers_count") or 0), size=int(best.get("size") or 0))
        try:
            pushed = datetime.fromisoformat(str(best.get("pushed_at")).replace("Z", "+00:00"))
            info["pushed_days"] = round((datetime.now(timezone.utc) - pushed).total_seconds() / 86400, 1)
        except Exception:  # noqa: BLE001
            pass
        if info["pushed_days"] is not None and info["pushed_days"] <= GITHUB_ACTIVE_DAYS:
            since = datetime.fromtimestamp(_now() - GITHUB_ACTIVE_DAYS * 86400, timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
            left = timeout - (_now() - t0)
            if left > 0.5:
                c, err2 = _gh_api(f"/repos/{owner}/{info['repo']}/commits?since={since}&per_page=30", left)
                if isinstance(c, list):
                    info["commits_30d"] = len(c)
                elif err2:
                    info["error"] = err2
    if info["error"] is None or info["error"] == "http 404":   # errori di rete/limite: non in cache
        with _lock:
            _github[key] = (_now(), info)
    return info


# ----------------------------------------------------------------------------------------------- punteggio
def score_project(site, socials, gh, age_days, meta, text, is_pump, timed_out=False):
    """Funzione pura (testabile offline). Ritorna (score 0..1, tags)."""
    tags, s = [], 0.0
    has_x, has_tg = socials.get("x"), socials.get("tg")
    if site is None:
        tags.append("proj_nosite")
    elif not site.get("ok"):
        tags.append("proj_site_down")
        s += 0.03
    else:
        bad = bool(site.get("parked")) or bool(site.get("small"))
        if site.get("parked"):
            tags.append("proj_template")
        if site.get("small"):
            tags.append("proj_small")
        if site.get("spa"):
            tags.append("proj_spa")
        if site.get("builder"):
            tags.append("proj_builder")
        if not bad:
            tags.append("proj_site")
            s += 0.30 if site.get("text_len", 0) >= 400 or site.get("spa") else 0.22
        else:
            s += 0.05
        if site.get("freehost"):
            tags.append("proj_freehost")
    if age_days is not None:
        if age_days < FRESH_DOMAIN_DAYS:
            tags.append(f"proj_domain_new:{int(age_days)}")
        elif age_days >= OLD_DOMAIN_DAYS:
            tags.append("proj_domain_old")
            s += 0.10
        else:
            s += 0.05
    if has_x:
        tags.append("proj_x")
        s += 0.15
    if has_tg:
        tags.append("proj_tg")
        s += 0.08
    if not has_x and not has_tg:
        tags.append("proj_nosocials")
    if (site is None or not site.get("ok")) and not has_x and not has_tg:
        tags.append("proj_empty")
    if gh:
        if gh.get("error") == "ratelimited" and not gh.get("exists"):
            tags.append("proj_github_ratelimited")
        elif gh.get("exists"):
            tags.append("proj_github")
            active = (gh.get("pushed_days") is not None and gh["pushed_days"] <= GITHUB_ACTIVE_DAYS
                      and not gh.get("fork") and (gh.get("commits_30d") is None or gh["commits_30d"] >= 3))
            if active:
                tags.append("proj_github_active")
                s += 0.20
            else:
                tags.append("proj_github_stale")
                s += 0.05
    low = " " + str(text or "").lower() + " "
    u_hits = sorted({k.strip() for k in UTILITY if k in low})
    m_hits = sorted({k.strip() for k in MEME if k in low})
    if len(u_hits) >= 2 and len(u_hits) > len(m_hits):
        tags.append("proj_utility")
        s += 0.10 if (site and site.get("ok") and not site.get("parked")) else 0.04
    elif m_hits and not u_hits:
        tags.append("proj_meme")
    if meta:
        if meta.get("mismatch"):
            tags.append("proj_meta_mismatch")
            s -= 0.15
        elif meta.get("checked"):
            tags.append("proj_meta_ok")
            if not is_pump:
                s += 0.05
        if meta.get("mutable") and not is_pump:
            tags.append("proj_meta_mutable")
            s -= 0.05
    if timed_out:
        tags.append("proj_partial")
    s = round(max(0.0, min(1.0, s)), 3)
    tags.append(f"proj_score:{s:.2f}")
    return s, tags


def meta_consistency(token_meta, meta_json, dex_meta, symbol, name):
    """Coerenza tra metadati on-chain (RugCheck tokenMeta), JSON dei metadati e DexScreener."""
    out = {"checked": False, "mismatch": False, "mutable": False, "why": []}
    tm = token_meta if isinstance(token_meta, dict) else {}
    syms = [x for x in (tm.get("symbol"), (meta_json or {}).get("symbol"), (dex_meta or {}).get("symbol"), symbol)
            if x]
    if len(syms) >= 2:
        out["checked"] = True
        base = {_norm(x) for x in syms if _norm(x)}
        if len(base) > 1:
            out["mismatch"] = True
            out["why"].append("symbol diversi: " + "/".join(sorted(str(x) for x in set(syms)))[:80])
    if tm.get("mutable") and str(tm.get("updateAuthority") or "") not in ("", "11111111111111111111111111111111"):
        out["mutable"] = True
    return out


# ----------------------------------------------------------------------------------------------- API
def check(mint, symbol=None, name=None, links=None, token_meta=None, is_pump=None, chain="solana",
          deadline=DEADLINE_SEC, use_cache=True):
    """Controllo completo con scadenza unica. Mai eccezioni. Ritorna dict con score e tags."""
    t0 = _now()
    try:
        _load_disk()
        hit = _cache.get(mint)
        if use_cache and hit and t0 - hit[0] < CACHE_SEC:
            return dict(hit[1], cached=True)
        if is_pump is None:
            is_pump = str(mint).endswith("pump")
        end = t0 + deadline
        left = lambda: max(0.3, end - _now())  # noqa: E731
        timed_out = False
        # 1) link: DexScreener (se non passati) + JSON dei metadati, in parallelo
        dex_meta = {}
        f_links = None if links else _pool.submit(dex_links, mint, min(2.5, left()))
        uri = (token_meta or {}).get("uri") if isinstance(token_meta, dict) else None
        f_meta = _pool.submit(fetch_metadata_json, uri, min(2.5, left())) if uri else None
        if f_links:
            try:
                links, dex_meta = f_links.result(timeout=left())
            except Exception:  # noqa: BLE001
                links, timed_out = {"websites": [], "socials": []}, True
        meta_json = None
        if f_meta:
            try:
                meta_json = f_meta.result(timeout=min(2.6, left()))
            except Exception:  # noqa: BLE001
                timed_out = True
        websites = list(dict.fromkeys(links.get("websites") or []))
        socials_l = list(links.get("socials") or [])
        if isinstance(meta_json, dict):
            for k in ("website", "twitter", "telegram"):
                v = str(meta_json.get(k) or "").strip()
                if v.startswith("http"):
                    (websites if k == "website" else socials_l).append(v if k == "website" else {"type": k, "url": v})
        urls_social = list(dict.fromkeys(s["url"] for s in socials_l if isinstance(s, dict) and s.get("url")))
        types = {s.get("type") for s in socials_l if isinstance(s, dict)}
        has_x = "twitter" in types or "x" in types or any(_is_social(u, ("x.com", "twitter.com")) for u in urls_social) \
            or any(_is_social(u, ("x.com", "twitter.com")) for u in websites)
        has_tg = "telegram" in types or any(_is_social(u, ("t.me", "telegram.me")) for u in urls_social + websites)
        gh_links = [u for u in websites + urls_social if _is_social(u, ("github.com",))]
        sites = []
        for u in websites:
            if u and not _is_social(u, NOT_A_SITE + ("github.com",)) and u not in sites:
                sites.append(u)
        site_url = sites[0] if sites else None
        # 2) sito + RDAP in parallelo
        f_site = _pool.submit(check_site, site_url, min(3.0, left())) if site_url else None
        dom = _host(site_url) if site_url else ""
        free = any(dom == h or dom.endswith("." + h) for h in FREE_HOSTS)
        f_age = _pool.submit(domain_age_days, dom, min(2.5, left())) if dom and not free else None
        site = None
        if f_site:
            try:
                site = f_site.result(timeout=left())
            except Exception:  # noqa: BLE001
                site, timed_out = {"url": site_url, "ok": False, "error": "timeout"}, True
            site["freehost"] = free
        # 3) GitHub (link diretto o trovato nel sito), in parallelo con l'RDAP ancora in corso
        gh, f_gh = None, None
        cand = [m.groups() for u in gh_links for m in [GH_RE.match(u)] if m]
        if not cand and site and site.get("github"):
            cand = [tuple((g.split("/") + [None])[:2]) for g in site["github"]]
        if cand and end - _now() > 0.6:
            owner, repo = cand[0]
            repo = (repo or "").removesuffix(".git") or None
            f_gh = _pool.submit(github_activity, owner, repo, min(2.5, left()))
        elif cand:
            timed_out = True
        age = None
        if f_age:
            try:
                age = f_age.result(timeout=left())
            except Exception:  # noqa: BLE001
                timed_out = True
        if f_gh:
            try:
                gh = f_gh.result(timeout=left())
            except Exception:  # noqa: BLE001
                timed_out = True
        # 4) metadati e parole chiave
        meta = meta_consistency(token_meta, meta_json, dex_meta, symbol, None)
        text = " ".join(str(x) for x in ((meta_json or {}).get("description"), (site or {}).get("text"), name) if x)
        score, tags = score_project(site, {"x": has_x, "tg": has_tg}, gh, age, meta, text, is_pump, timed_out)
        res = {"mint": mint, "chain": chain, "score": score, "tags": tags, "is_pump": bool(is_pump),
               "links": {"websites": websites[:4], "socials": urls_social[:6], "github": gh_links[:2]},
               "site": {k: v for k, v in (site or {}).items() if k != "text"} if site else None,
               "domain_age_days": age, "github": gh, "meta": meta,
               "description": str((meta_json or {}).get("description") or "")[:200],
               "elapsed": round(_now() - t0, 2), "ts": int(_now())}
        with _lock:
            _cache[mint] = (_now(), res)
            for k in [k for k, v in _cache.items() if _now() - v[0] > CACHE_SEC]:
                _cache.pop(k, None)
        if gh is not None or age is not None:
            _pool.submit(_save_disk)
        return res
    except Exception as e:  # noqa: BLE001
        return {"mint": mint, "score": None, "tags": ["proj_err"], "error": f"{type(e).__name__}"[:60],
                "elapsed": round(_now() - t0, 2)}


async def acheck(mint, **kw):
    return await asyncio.to_thread(check, mint, **kw)


def project_bonus(r, cfg):
    """(bonus, tags). Bonus = PROJECT_BONUS x score se score >= PROJECT_MIN_SCORE (tag proj_good).
    Penalita' lieve -PROJECT_EMPTY_PENALTY se niente sito e niente social (tag proj_empty). Timeout/errore: 0."""
    if not r or r.get("score") is None:
        return 0.0, list((r or {}).get("tags") or [])
    tags = list(r.get("tags") or [])
    b = 0.0
    if "proj_empty" in tags:
        b -= float(cfg.get("PROJECT_EMPTY_PENALTY", 0.01))
    elif r["score"] >= float(cfg.get("PROJECT_MIN_SCORE", 0.5)):
        b += float(cfg.get("PROJECT_BONUS", 0.02)) * r["score"]
        tags.append(f"proj_good:{r['score']:.2f}")
    return round(b, 4), tags


if __name__ == "__main__":
    import sys
    for a in sys.argv[1:] or ["npz2FhoufQ7pKqhGpaipYWacrAkidyW2yDpVWSvpump"]:
        print(json.dumps(check(a, use_cache=False), indent=1, default=str))
