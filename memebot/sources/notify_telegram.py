"""
notify_telegram.py - v8.4: avvisi Telegram NON bloccanti per il Desk Memecoin (PAPER).

- Token: SOLO da os.environ["TELEGRAM_BOT_TOKEN"], ripulito da ogni spazio (re.sub(r"\\s", "", ...)).
  Non viene mai scritto su file o log; ogni errore passa da mask() (le URL Telegram contengono il token).
- Chat id: dal file /workspace/memebot/.telegram_chat_id (chmod 600, escluso dal sync GitHub).
- Invio in un thread separato con coda: send() non aspetta mai la rete. Timeout 10 s, 2 tentativi in piu,
  al massimo 1 messaggio al secondo; i messaggi in coda nello stesso secondo vengono uniti in uno solo.
- group(chiave, riga, ogni): righe raggruppate e inviate al massimo una volta ogni `ogni` secondi per chiave.
- Fallisce in silenzio: nessuna eccezione verso il bot.
- v8.5g: okf (riga di log a invio riuscito), onflush, group_state/restore_groups (anti-doppioni dopo un riavvio).
"""
from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

CHAT_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".telegram_chat_id")
TOKEN_RE = re.compile(r"\d{8,10}:[A-Za-z0-9_-]{35}")
MAX_LEN = 3900


def _token():
    return re.sub(r"\s", "", os.environ.get("TELEGRAM_BOT_TOKEN") or "")


def mask(text):
    s = str(text)
    t = _token()
    if t:
        s = s.replace(t, "***")
    s = TOKEN_RE.sub("***", s)
    return re.sub(r"/bot[^/\s]+/", "/bot***/", s)


class Notifier:
    def __init__(self, enabled=True, chat_file=CHAT_FILE, min_gap=1.0, timeout=10.0, retries=2, sender=None,
                 logf=None, okf=None, onflush=None):
        self.token = _token()
        self.chat = ""
        try:
            with open(chat_file) as f:
                self.chat = f.read().strip()
        except OSError:
            self.chat = ""
        self.enabled = bool(enabled and self.token and self.chat)
        self.min_gap, self.timeout, self.retries = min_gap, timeout, retries
        self.sender = sender or self._http_send       # sostituibile nei test
        self.logf = logf or (lambda msg: None)
        self.okf = okf or (lambda msg: None)           # v8.5g: riga di log a invio riuscito (mai token/chat id)
        self.onflush = onflush or (lambda: None)       # v8.5g: salva lo stato anti-doppioni dopo un invio di gruppo
        self.q = queue.Queue(maxsize=300)
        self.groups = {}                               # chiave -> {"lines": [], "last": ts, "every": s, "title": str}
        self.glock = threading.Lock()
        self.sent = self.failed = self.dropped = 0
        self.last_err = ""
        self._last_err_log = 0.0
        self._last_send = 0.0
        self.th = None
        if self.enabled:
            self.th = threading.Thread(target=self._run, name="telegram", daemon=True)
            self.th.start()

    # ------------------------------------------------------------------ API pubblica (mai bloccante)
    def send(self, text):
        if not self.enabled or not text:
            return False
        try:
            self.q.put_nowait(str(text)[:MAX_LEN])
            return True
        except queue.Full:
            self.dropped += 1
            return False

    def group(self, key, line, every=600, title=None):
        """Raggruppa righe per chiave; il thread le invia insieme al massimo una volta ogni `every` secondi."""
        if not self.enabled:
            return False
        with self.glock:
            g = self.groups.setdefault(key, {"lines": [], "last": 0.0, "every": every, "title": title or key})
            g["every"], g["title"] = every, title or g["title"]
            if len(g["lines"]) < 50:
                g["lines"].append(str(line))
            else:
                g["extra"] = g.get("extra", 0) + 1
        return True

    def group_state(self):
        """v8.5g: chiave -> ultimo invio del gruppo (per salvarlo su file e non rimandare dopo un riavvio)."""
        with self.glock:
            return {k: g["last"] for k, g in self.groups.items() if g.get("last")}

    def restore_groups(self, d):
        """v8.5g: ripristina l'ultimo invio per chiave; le righe nuove aspettano il loro intervallo."""
        with self.glock:
            for k, t in (d or {}).items():
                try:
                    t = float(t)
                except (TypeError, ValueError):
                    continue
                g = self.groups.setdefault(str(k), {"lines": [], "last": 0.0, "every": 600, "title": str(k)})
                g["last"] = max(g["last"], min(t, time.time()))

    def flush(self, wait=5.0):
        """Svuota gruppi e coda (usato alla chiusura); aspetta al massimo `wait` secondi."""
        self._flush_groups(force=True)
        end = time.time() + wait
        while self.enabled and not self.q.empty() and time.time() < end:
            time.sleep(0.1)

    # ------------------------------------------------------------------ thread
    def _flush_groups(self, force=False):
        now, sent = time.time(), False
        with self.glock:
            for k, g in self.groups.items():
                if g["lines"] and (force or now - g["last"] >= g["every"]):
                    extra = g.pop("extra", 0)
                    txt = g["title"] + "\n" + "\n".join("- " + x for x in g["lines"])
                    if extra:
                        txt += f"\n(+{extra} altri)"
                    g["lines"], g["last"], sent = [], now, True
                    try:
                        self.q.put_nowait(txt[:MAX_LEN])
                    except queue.Full:
                        self.dropped += 1
        if sent:
            try:
                self.onflush()
            except Exception:  # noqa: BLE001
                pass

    def _run(self):
        while True:
            try:
                self._flush_groups()
                try:
                    msg = self.q.get(timeout=1.0)
                except queue.Empty:
                    continue
                parts = [msg]                     # unisce quello che e gia in coda (max 1 msg/s)
                while sum(len(p) + 2 for p in parts) < MAX_LEN - 500:
                    try:
                        parts.append(self.q.get_nowait())
                    except queue.Empty:
                        break
                text = "\n\n".join(parts)[:MAX_LEN]
                dt = time.time() - self._last_send
                if dt < self.min_gap:
                    time.sleep(self.min_gap - dt)
                self._deliver(text)
                self._last_send = time.time()
            except Exception as e:  # noqa: BLE001  (mai fermare il thread)
                self._err(f"errore interno {type(e).__name__}")
                time.sleep(1.0)

    def _deliver(self, text):
        for i in range(self.retries + 1):
            try:
                r = self.sender(self.token, self.chat, text, self.timeout)
            except Exception as e:  # noqa: BLE001
                r = (False, f"{type(e).__name__}: {e}", None)
            ok, err, retry_after = r if isinstance(r, tuple) else (bool(r), "", None)
            if ok:
                self.sent += 1
                try:   # v8.5g: solo lunghezza e inizio del testo (mascherato), mai token o chat id
                    head = mask(text.split("\n", 1)[0])[:60]
                    self.okf(f"Telegram: messaggio inviato (n. {self.sent}, {len(text)} caratteri): {head}")
                except Exception:  # noqa: BLE001
                    pass
                return True
            self._err(err)
            if i < self.retries:
                time.sleep(min(30.0, float(retry_after)) if retry_after else 2.0 * (i + 1))
        self.failed += 1
        return False

    def _err(self, err):
        self.last_err = mask(err)[:200]
        if time.time() - self._last_err_log > 1800:   # al massimo una riga di log ogni 30 minuti
            self._last_err_log = time.time()
            try:
                self.logf("Telegram: invio non riuscito (" + self.last_err + ")")
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _http_send(token, chat, text, timeout):
        data = urllib.parse.urlencode({"chat_id": chat, "text": text, "disable_web_page_preview": "true"}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                ok = json.loads(r.read().decode() or "{}").get("ok", False)
                return (bool(ok), "" if ok else "risposta non ok", None)
        except urllib.error.HTTPError as e:
            ra = None
            try:
                body = json.loads(e.read().decode() or "{}")
                ra = (body.get("parameters") or {}).get("retry_after")
            except Exception:  # noqa: BLE001
                pass
            return (False, mask(f"HTTP {e.code}"), ra)
        except Exception as e:  # noqa: BLE001
            return (False, mask(f"{type(e).__name__}: {e}"), None)

    def status(self):
        return {"enabled": self.enabled, "sent": self.sent, "failed": self.failed, "dropped": self.dropped,
                "queue": self.q.qsize(), "last_err": self.last_err}
