"""Compito 17 (idea di Kiwi): token con nomi/ticker di aziende, celebrita' o piattaforme crypto famose sono
spesso rug o honeypot. Lista chiusa, nessuna chiamata di rete, nessuna eccezione.

Diverso dal controllo "ticker impostore" gia' esistente in desk.py (MAJOR_TICKERS/MAJOR_NAMES,
ticker_impostor): quello copre gli ASSET/EXCHANGE crypto piu' grandi e scarta sempre (TICKER_IMPOSTOR_ACTION).
Questo copre aziende/celebrita'/piattaforme NON crypto e resta SOLO in shadow (tag + penalita' sul
punteggio): diventa uno scarto rigido solo se i dati mostrano un rug rate chiaramente piu' alto
(vedi docs/analysis/brand_filter_report_17.py). Le azioni quotate in borsa restano un elenco a parte
(STOCK_TICKERS in desk.py, gia' solo annotate): qui si aggiungono solo celebrita', marchi non quotati
e piattaforme/exchange non crypto, piu' le aziende quotate per completare il tag unico.
"""
import re

# aziende/marchi (quotati e non), celebrita', piattaforme/exchange non crypto. Nomi scritti per esteso E ticker
# dove sensato; confronto esatto dopo normalizzazione (mai substring libera, per non scartare a vuoto nomi a caso).
BRANDS = {
    # aziende/marchi (gia' in STOCK_TICKERS per le quotate; qui si aggiungono quelle NON in borsa + duplicati
    # per avere un solo elenco/tag)
    "AAPL", "APPLE", "AMZN", "AMAZON", "GOOGL", "GOOG", "GOOGLE", "ALPHABET", "META", "FACEBOOK", "NFLX", "NETFLIX",
    "NVDA", "NVIDIA", "TSLA", "TESLA", "MSFT", "MICROSOFT", "NIO",
    "LEGO", "NIKE", "ADIDAS", "DISNEY", "MCDONALDS", "STARBUCKS", "SAMSUNG", "SONY", "XPENG", "SPACEX", "XAI", "GROK",
    "OPENAI", "CHATGPT",
    # celebrita' (mai esempio buono: BEAST/MrBeast e' gia' una lezione negativa confermata)
    "MRBEAST", "BEAST", "ELONMUSK", "ELON", "MUSK", "KANYE", "TRUMP2024",
    # franchise/media famosi usati come esca
    "GTA", "GTA6", "GRANDTHEFTAUTO", "FORTNITE", "MINECRAFT", "POKEMON",
    # piattaforme/exchange NON crypto (quelle crypto grandi sono gia' in MAJOR_TICKERS/MAJOR_NAMES)
    "GEMINI", "ROBINHOOD", "PAYPAL", "VISA", "MASTERCARD", "REVOLUT", "WISE", "VENMO",
}
# suffissi ammessi dopo la normalizzazione (come in desk.py: COIN/TOKEN/ONSOLANA si staccano da sole)
_SUFFIXES = ("COIN", "TOKEN", "ONSOLANA", "ONSOL")
_LEET = str.maketrans({"0": "O", "1": "I", "3": "E", "4": "A", "5": "S", "7": "T", "8": "B"})


def _norm(x):
    """Maiuscolo, solo lettere/cifre, poi leetspeak piegato su lettere (AMAZ0N -> AMAZON)."""
    t = re.sub(r"[^A-Za-z0-9]", "", str(x or "")).upper()
    return t.translate(_LEET)


def brand_tag(symbol, name=None):
    """Simbolo/nome -> marchio riconosciuto (stringa) o None. Confronto esatto dopo normalizzazione,
    con gli stessi suffissi ammessi di ticker_impostor (COIN/TOKEN/ONSOL...)."""
    for raw in (symbol, name):
        t = _norm(raw)
        if not t:
            continue
        if t in BRANDS:
            return t
        cands = {t} | {t[:-len(sf)] for sf in _SUFFIXES if t.endswith(sf) and len(t) > len(sf)}
        hit = next((c for c in cands if c in BRANDS and len(c) >= 3), None)
        if hit:
            return hit
    return None
