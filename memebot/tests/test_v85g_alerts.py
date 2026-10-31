"""v8.5g: stato anti-doppioni degli avvisi Telegram su file (alert_state.json). Da lanciare SOLO su una copia in /tmp."""
import json
import os
import time

import desk as D
from sources import notify_telegram as N

assert D.DATA_DIR.startswith("/tmp"), "test solo su copia in /tmp"
P = D.ALERT_STATE_PATH
assert os.path.dirname(P) == D.DATA_DIR and os.path.basename(P) == "alert_state.json"
if os.path.exists(P):
    os.remove(P)

FAKE_TOKEN = "1234567890:" + "A" * 35
os.environ["TELEGRAM_BOT_TOKEN"] = " " + FAKE_TOKEN[:5] + " " + FAKE_TOKEN[5:] + "\n"   # spazio come nel .env vero
chat = "/tmp/_t85g_chat"
open(chat, "w").write("-100999")
sent, oklog = [], []


def fake_sender(token, ch, text, timeout):
    assert token == FAKE_TOKEN   # spazi tolti
    sent.append(text)
    return (True, "", None)


def mk():
    return N.Notifier(enabled=True, chat_file=chat, min_gap=0.01, sender=fake_sender, okf=oklog.append,
                      onflush=D.alert_state_save)


def reset_mem():
    D.RC_HEALTH.update({"down": False, "fails": 0, "last_ok": 0.0, "down_since": None, "alert_t": 0.0,
                        "alerted": "up", "fb_ok": 0, "fb_skip": 0})
    D.HOST_PAUSE.update({"alert_t": 0.0, "pending": []})
    D.ALERT_START["t"] = 0.0


def wait_sent(n, t=5):
    end = time.time() + t
    while len(sent) < n and time.time() < end:
        time.sleep(0.05)


# 1) file assente/rotto = parte da zero, niente crash
reset_mem()
assert D.alert_state_load() == {}
open(P, "w").write("{rotto")
assert D.alert_state_load() == {}
os.remove(P)

# 2) RugCheck giu: avviso una volta, salvato; dopo il "riavvio" nessun nuovo avviso e ripiego ancora attivo
D.TG_REF[0] = mk()
now = time.time()
for i in range(D.CFG["RC_DOWN_FAILS"]):
    D.rc_health(False, now=now + i)
wait_sent(1)
assert len(sent) == 1 and "RugCheck NON disponibile" in sent[0], sent
d = json.load(open(P))
assert d["rugcheck"]["alerted"] == "down" and d["rugcheck"]["down"] is True
reset_mem()                       # == riavvio del processo
D.TG_REF[0] = mk()
D.alert_state_load(now=now + 60)
assert D.RC_HEALTH["down"] and D.RC_HEALTH["alerted"] == "down"
for i in range(10):
    D.rc_health(False, now=now + 70 + i)
time.sleep(0.3)
assert len(sent) == 1, "avviso RugCheck rimandato dopo il riavvio"
# niente falso "di nuovo disponibile" dopo il riavvio anche passata l'ora
D.rc_health(False, now=now + 4000)
time.sleep(0.3)
assert len(sent) == 1, sent
# ritorno reale: un avviso (limite 1h rispettato)
D.rc_health(True, now=now + 4001)
wait_sent(2)
assert len(sent) == 2 and "di nuovo disponibile" in sent[1]
assert json.load(open(P))["rugcheck"]["alerted"] == "up"

# 3) limite orario di RugCheck rispettato anche dopo il riavvio
reset_mem()
D.alert_state_load(now=now + 4002)
assert D.RC_HEALTH["alert_t"] == now + 4001 and not D.RC_HEALTH["down"]
for i in range(D.CFG["RC_DOWN_FAILS"]):
    D.rc_health(False, now=now + 4400 + i)
time.sleep(0.3)
assert D.RC_HEALTH["down"] and len(sent) == 2, "avviso entro l'ora dopo il riavvio"

# 4) pausa host: max 1 ogni 30 min anche tra riavvii
sent.clear()
reset_mem()
D.HOST_PAUSE["pending"] = [(now - 1500, now)]
assert D.host_pause_alert(now)
wait_sent(1)
assert len(sent) == 1, sent
reset_mem()
D.alert_state_load(now=now + 10)
assert D.HOST_PAUSE["alert_t"] == now
D.HOST_PAUSE["pending"] = [(now - 100, now + 5)]
assert not D.host_pause_alert(now + 600)
assert D.host_pause_alert(now + 1801)
wait_sent(2)
assert len(sent) == 2, sent

# 5) messaggio di avvio: secondo entro 10 min saltato, dopo 10 min inviato
sent.clear()
reset_mem()
ok, ago = D.tg_start_message("Desk avviato in PAPER - test", now=now)
assert ok and ago is None
wait_sent(1)
reset_mem()
D.alert_state_load(now=now + 120)
ok, ago = D.tg_start_message("Desk avviato in PAPER - test 2", now=now + 120)
assert not ok and 119 <= ago <= 121
time.sleep(0.3)
assert len(sent) == 1, sent
ok, ago = D.tg_start_message("Desk avviato in PAPER - test 3", now=now + 700)
assert ok
wait_sent(2)
assert len(sent) == 2

# 6) gruppi (hard/err): orario dell'ultimo invio salvato e ripristinato
sent.clear()
n = mk()
D.TG_REF[0] = n
D.tg_group("hard", "X (abc..): cluster: test", 600, "Rifiuti hard:")
wait_sent(1)
assert len(sent) == 1, sent
time.sleep(0.2)
g = json.load(open(P))["groups"]
assert "hard" in g and abs(g["hard"] - time.time()) < 10
n2 = mk()
D.TG_REF[0] = n2
D.alert_restore_groups(json.load(open(P)))
D.tg_group("hard", "Y (def..): cluster: test", 600, "Rifiuti hard:")
time.sleep(1.5)
assert len(sent) == 1, "gruppo rimandato subito dopo il riavvio"

# 7) orari nel futuro ignorati (nessun blocco infinito)
json.dump({"start": {"t": time.time() + 10 ** 6}, "host_pause": {"alert_t": "x"}}, open(P, "w"))
reset_mem()
D.alert_state_load()
assert D.ALERT_START["t"] == 0.0 and D.HOST_PAUSE["alert_t"] == 0.0

# 8) log a invio riuscito, mai token o chat id; scrittura atomica senza file .tmp rimasti; niente segreti nel file
assert oklog and all(x.startswith("Telegram: messaggio inviato") for x in oklog)
blob = "\n".join(oklog) + open(P).read()
assert FAKE_TOKEN not in blob and FAKE_TOKEN[:10] not in blob and "-100999" not in blob
assert not [f for f in os.listdir(D.DATA_DIR) if f.startswith("alert_state.json.tmp")]
leak = N.Notifier(enabled=True, chat_file=chat, min_gap=0.01, sender=fake_sender, okf=oklog.append)
leak.send(f"prova https://api.telegram.org/bot{FAKE_TOKEN}/x")
time.sleep(0.5)
assert FAKE_TOKEN not in oklog[-1]
print("test_v85g_alerts OK")
