"""V4 §5 : événements, centre de notifications, rapports de plantage, Discord (transport simulé) et anti-spam."""
import json
import time

import pytest

from conftest import tick_until, wait_for
from test_api import auth, client  # noqa: F401
from mcpanel.crash import analyze
from mcpanel.process import ProcessError


def events(panel, kind=None, sid=None):
    rows = panel.db.query("SELECT * FROM events ORDER BY id")
    return [r for r in rows if (kind is None or r["type"] == kind) and (sid is None or r["server_id"] == sid)]


def run_until_running(panel, s):
    panel.manager.start(s["id"])
    assert tick_until(panel, lambda: panel.manager.state(panel.manager.server(s["id"])) == "running", 30)


def test_analysis_separates_confirmed_and_possible():
    rep = "Description: Ticking entity\njava.lang.OutOfMemoryError: Java heap space\nSuspected Mods: Create (create)\n-- Mod loading issue for: jei --\n"
    a = analyze("[Server thread/ERROR]: boom\n", rep)
    assert any("Ticking entity" in x for x in a["confirmed"]) and any("OutOfMemoryError" in x for x in a["confirmed"])
    assert any("Mémoire Java épuisée" in x for x in a["possible"])                         # hypothèse, jamais affirmée comme certaine
    assert not any("Mémoire" in x for x in a["confirmed"])
    assert {s["name"] for s in a["suspects"]} == {"Create (create)", "jei"}
    silent = analyze("", None)
    assert not silent["confirmed"] and "tué de l'extérieur" in silent["possible"][0]


def test_crash_with_report_creates_event_and_report(panel, make_server):
    s = make_server(name="Plante")
    run_until_running(panel, s)
    panel.manager.send_command(s["id"], "crash")
    assert tick_until(panel, lambda: events(panel, "server.crashed", s["id"]), 20)
    ev = events(panel, "server.crashed", s["id"])[-1]
    cr = panel.crashes.get(ev["data"]["crash_id"])
    assert cr["report_name"].startswith("crash-") and "Exception in server tick loop" in cr["report"]
    a = cr["analysis"]
    assert any("Exception in server tick loop" in x for x in a["confirmed"])
    assert any("NullPointerException" in x for x in a["confirmed"]) and any("IllegalArgumentException" in x for x in a["confirmed"])
    assert a["suspects"][0]["name"] == "Cool Mod (coolmod)" and "Ticker.java:42" in a["trace"]
    assert "Exception in server tick loop" in cr["console"]
    assert ev["data"]["suspects"] == ["Cool Mod (coolmod)"]


def test_silent_death_is_unexpected_stop(panel, make_server):
    s = make_server(name="Muet")
    run_until_running(panel, s)
    panel.manager.send_command(s["id"], "crash-silent")
    assert tick_until(panel, lambda: events(panel, "server.unexpected_stop", s["id"]), 20)
    assert not events(panel, "server.crashed", s["id"])


def test_start_failures(panel, make_server):
    s = make_server(name="Echec")
    (panel.s.servers_dir / s["name"] / "start-fail").write_text("")
    panel.manager.start(s["id"])
    assert tick_until(panel, lambda: events(panel, "server.start_failed", s["id"]), 20)
    ev = events(panel, "server.start_failed", s["id"])[-1]
    assert "badmod" in ev["data"]["suspects"]
    s2 = make_server(name="TropGros")
    panel.db.execute("UPDATE servers SET ram_max_mb=? WHERE id=?", (10 ** 7, s2["id"]))
    with pytest.raises(ProcessError):
        panel.manager.start(s2["id"])
    assert "Mémoire insuffisante" in events(panel, "server.start_failed", s2["id"])[-1]["data"]["reason"]


def test_auto_restart_event(panel, make_server):
    s = make_server(name="Relance")
    panel.db.execute("UPDATE servers SET auto_restart=1 WHERE id=?", (s["id"],))
    run_until_running(panel, s)
    panel.manager.send_command(s["id"], "crash")
    assert tick_until(panel, lambda: events(panel, "server.auto_restart", s["id"]), 20)
    assert tick_until(panel, lambda: panel.manager.alive(s["id"]), 20)
    panel.manager.stop(s["id"])


def test_log_critical_with_antispam(panel, make_server):
    s = make_server(name="Logs")
    run_until_running(panel, s)
    panel.manager.send_command(s["id"], "spam-error")
    assert tick_until(panel, lambda: events(panel, "log.critical", s["id"]), 10)
    panel.manager.send_command(s["id"], "spam-error")
    for _ in range(10):
        panel.manager.tick(); time.sleep(0.1)
    assert len(events(panel, "log.critical", s["id"])) == 1                               # au plus un toutes les 10 min
    panel.manager.stop(s["id"])


def test_webhook_filters_cooldown_and_mute(panel, make_server, routes):
    sent = []
    routes["https://discord.com/api/webhooks/1/abc"] = lambda u, d: sent.append(json.loads(d)) or b""
    a, b = make_server(name="A"), make_server(name="B")
    panel.events.add_webhook("Tout A", "https://discord.com/api/webhooks/1/abc", ["server.crashed", "server.started"], [a["id"]], 3600)
    panel.events.emit("server.crashed", b["id"], "B", {})                                 # serveur non sélectionné
    panel.events.emit("server.crashed", a["id"], "A", {"confirmed": ["Le crash-report indique : « x »."], "possible": ["peut-être"], "suspects": ["coolmod"]})
    panel.events.emit("server.crashed", a["id"], "A", {})                                 # anti-spam : ignoré
    panel.events.emit("server.stopped", a["id"], "A", {})                                 # type non choisi
    wait_for(lambda: sent, 5)
    time.sleep(0.3)
    assert len(sent) == 1
    f = {x["name"]: x["value"] for x in sent[0]["embeds"][0]["fields"]}
    assert "non certaine" in " ".join(f) and "coolmod" in f["Mods à vérifier"]
    panel.db.execute("UPDATE servers SET notify_prefs=? WHERE id=?", (json.dumps({"muted": ["server.started"]}), a["id"]))
    panel.events.emit("server.started", a["id"], "A", {})
    time.sleep(0.4)
    assert len(sent) == 1                                                               # coupé pour ce serveur


def test_notification_center_and_system_events(auth, panel, make_server):
    c = auth
    s = make_server(name="Centre")
    panel.alerts.raise_("ram_low", "critical", "RAM disponible très faible")
    panel.alerts.raise_("ram_low", "critical", "RAM disponible très faible (maj)")         # mise à jour : pas de 2e événement
    assert len(events(panel, "system.ram_low")) == 1
    panel.jobs.wait(panel.jobs.submit("backup", s["id"], lambda job: (_ for _ in ()).throw(RuntimeError("disque plein"))), 10)
    assert "disque plein" in events(panel, "backup.failed", s["id"])[-1]["data"]["reason"]
    r = c.get("/api/notifications/center").json()
    assert r["critical_unread"] >= 2 and r["items"][0]["severity"] == "critical"
    assert c.post("/api/notifications/seen", json={}).status_code == 200
    assert c.get("/api/notifications/center").json()["unread"] == 0
    assert c.put(f"/api/servers/{s['id']}/notify", json={"muted": ["server.started", "inconnu"]}).json()["muted"] == ["server.started"]
