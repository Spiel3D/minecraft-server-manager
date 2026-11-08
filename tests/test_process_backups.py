import os
import signal
import time
import zipfile
from pathlib import Path

import pytest

from conftest import tick_until, wait_for
from mcpanel.jobs import UserError
from mcpanel.process import ProcessError, mc_ping


def state(panel, sid):
    return panel.manager.state(panel.manager.server(sid))


def start_ready(panel, srv):
    panel.manager.start(srv["id"])
    tick_until(panel, lambda: state(panel, srv["id"]) == "running")


def test_start_requires_eula_and_install(panel, make_server):
    s = make_server(accept_eula=False)
    with pytest.raises(ProcessError, match="EULA"):
        panel.manager.start(s["id"])
    panel.accept_eula(s["id"])
    start_ready(panel, s)
    with pytest.raises(ProcessError):
        panel.manager.start(s["id"])          # jamais deux instances du même serveur


def test_lifecycle_command_stop(panel, make_server):
    s = make_server()
    start_ready(panel, s)
    st = panel.manager.stats(panel.manager.server(s["id"]))
    assert st["pid"] and st["rss_mb"] >= 0
    tick_until(panel, lambda: panel.manager.stats(panel.manager.server(s["id"]))["players"] == 2)
    panel.manager.send_command(s["id"], "save-all")
    wait_for(lambda: "Saved the game" in panel.manager.read_console(panel.manager.server(s["id"]))[0])
    assert panel.manager.stop(s["id"], 20, wait=True)
    wait_for(lambda: state(panel, s["id"]) == "stopped")
    assert panel.manager.server(s["id"])["desired_state"] == "stopped"
    assert not panel.db.query("SELECT * FROM alerts WHERE key LIKE 'crash:%'")


def test_command_sanitization(panel, make_server):
    s = make_server()
    start_ready(panel, s)
    panel.manager.send_command(s["id"], "list\nstop")      # la fin de ligne ne doit pas injecter un 2e ordre
    time.sleep(1)
    assert panel.manager.alive(s["id"])
    with pytest.raises(ProcessError):
        panel.manager.send_command(s["id"], "   ")


def test_crash_detected_and_alert(panel, make_server):
    s = make_server()
    start_ready(panel, s)
    panel.manager.send_command(s["id"], "crash")
    tick_until(panel, lambda: panel.db.query("SELECT * FROM alerts WHERE key=?", (f"crash:{s['id']}",)))
    assert state(panel, s["id"]) == "error"


def test_auto_restart_limited(panel, make_server):
    s = make_server()
    panel.update_server(s["id"], {"auto_restart": True})
    start_ready(panel, s)
    for i in range(3):                                   # 3 plantages : relancé à chaque fois
        pid = panel.manager.alive(s["id"])["pid"]
        panel.manager.send_command(s["id"], "crash")
        tick_until(panel, lambda: (panel.manager.alive(s["id"]) or {}).get("pid") not in (None, pid), 30)
        tick_until(panel, lambda: state(panel, s["id"]) == "running", 30)
    panel.manager.send_command(s["id"], "crash")         # 4e plantage : plus de relance automatique
    tick_until(panel, lambda: not panel.manager.alive(s["id"]), 30)
    for _ in range(5):
        panel.manager.tick()
        time.sleep(0.2)
    assert not panel.manager.alive(s["id"])
    assert panel.db.query("SELECT * FROM alerts WHERE key=?", (f"loop:{s['id']}",))


def test_panel_restart_readopts_running_server(panel, make_server, settings, routes):
    from conftest import FakeHttp
    from mcpanel.core import Panel
    s = make_server()
    start_ready(panel, s)
    pid = panel.manager.alive(s["id"])["pid"]
    p2 = Panel(settings, FakeHttp(routes))
    p2.on_startup()
    assert p2.manager.alive(s["id"])["pid"] == pid       # le serveur survit au redémarrage du panneau
    p2.manager.send_command(s["id"], "list")
    p2.jobs.shutdown()


def test_ram_budget_blocks_second_server(panel, make_server):
    b = panel.monitor.ram_budget()
    s1 = make_server(name="A", ram=512)
    big = panel.monitor.ram_budget()["max_xmx_mb"]
    s2 = make_server(name="B", ram=512)
    panel.db.execute("UPDATE servers SET ram_max_mb=? WHERE id=?", (b["total_mb"] * 2, s2["id"]))
    with pytest.raises(ProcessError, match="Mémoire insuffisante"):
        panel.manager.start(s2["id"])


def test_port_in_use_blocks_start(panel, make_server):
    import socket
    s = make_server()
    sock = socket.socket()
    sock.bind(("0.0.0.0", s["port"]))
    try:
        with pytest.raises(ProcessError, match="port"):
            panel.manager.start(s["id"])
    finally:
        sock.close()


def test_unmounted_disk_blocks_start(panel, make_server):
    s = make_server()
    panel.manager.mount_ok = lambda: False
    with pytest.raises(ProcessError, match="monté"):
        panel.manager.start(s["id"])


def test_kill_is_forced(panel, make_server):
    s = make_server()
    start_ready(panel, s)
    panel.manager.send_command(s["id"], "hang")
    panel.manager.kill(s["id"])
    wait_for(lambda: not panel.manager.alive(s["id"]))


def test_delete_requires_exact_name_and_stopped(panel, make_server):
    s = make_server(name="Suppr")
    with pytest.raises(UserError):
        panel.delete_server(s["id"], "suppr")
    start_ready(panel, s)
    with pytest.raises(UserError, match="Arrêtez"):
        panel.delete_server(s["id"], "Suppr")
    panel.manager.stop(s["id"], 20, wait=True)
    wait_for(lambda: not panel.manager.alive(s["id"]))
    other = make_server(name="Autre")
    panel.delete_server(s["id"], "Suppr")
    assert not Path(s["path"]).exists() and Path(other["path"]).exists()


# ---------------------------------------------------------------- sauvegardes
def run_backup(panel, sid, kind="manual"):
    jid = panel.jobs.submit("backup", sid, lambda job: panel.backups.create(job, sid, kind=kind))
    j = panel.jobs.wait(jid, 60)
    assert j["status"] == "done", j["log"] + j["error"]
    return j["result"]


def test_backup_stopped_and_restore_with_safety_copy(panel, make_server):
    s = make_server()
    d = Path(s["path"])
    (d / "world").mkdir()
    (d / "world" / "level.dat").write_text("v1")
    (d / "logs").mkdir(exist_ok=True)
    (d / "logs" / "latest.log").write_text("x")
    b = run_backup(panel, s["id"])
    with zipfile.ZipFile(panel.s.backups_dir / s["name"] / b["filename"]) as z:
        names = z.namelist()
    assert "world/level.dat" in names and not any(n.startswith("logs/") for n in names)
    (d / "world" / "level.dat").write_text("v2-modifié")
    jid = panel.jobs.submit("restore", s["id"], lambda job: panel.backups.restore(job, s["id"], b["id"]))
    j = panel.jobs.wait(jid, 60)
    assert j["status"] == "done", j["log"] + j["error"]
    assert (d / "world" / "level.dat").read_text() == "v1"
    kinds = {r["kind"] for r in panel.backups.list(s["id"])}
    assert "pre-restore" in kinds                        # l'état avant restauration est conservé


def test_restore_refused_while_running_and_for_corrupt(panel, make_server):
    s = make_server()
    b = run_backup(panel, s["id"])
    start_ready(panel, s)
    j = panel.jobs.wait(panel.jobs.submit("restore", s["id"], lambda job: panel.backups.restore(job, s["id"], b["id"])), 30)
    assert j["status"] == "error" and "Arrêtez" in j["error"]
    panel.manager.stop(s["id"], 20, wait=True)
    wait_for(lambda: not panel.manager.alive(s["id"]))
    p = panel.s.backups_dir / s["name"] / b["filename"]
    p.write_bytes(p.read_bytes()[:50])
    (Path(s["path"]) / "marqueur.txt").write_text("intact")
    j = panel.jobs.wait(panel.jobs.submit("restore", s["id"], lambda job: panel.backups.restore(job, s["id"], b["id"])), 30)
    assert j["status"] == "error"
    assert (Path(s["path"]) / "marqueur.txt").read_text() == "intact"   # rien n'a été touché


def test_hot_backup_freezes_and_unfreezes(panel, make_server):
    s = make_server()
    start_ready(panel, s)
    b = run_backup(panel, s["id"])
    text = panel.manager.read_console(panel.manager.server(s["id"]))[0]
    assert "command: save-off" in text and "command: save-all flush" in text
    assert text.rfind("command: save-on") > text.rfind("command: save-all flush")
    with zipfile.ZipFile(panel.s.backups_dir / s["name"] / b["filename"]) as z:
        assert "world-saved.txt" in z.namelist()         # fichier écrit par le « save-all » du faux serveur


def test_retention_only_deletes_old_auto_backups(panel, make_server):
    s = make_server()
    panel.update_server(s["id"], {"backup_prefs": {"enabled": True, "interval_hours": 1, "keep": 2, "mode": "hot"}})
    manual = run_backup(panel, s["id"], "manual")
    for _ in range(4):
        run_backup(panel, s["id"], "auto")
        time.sleep(1.1)
    rows = panel.backups.list(s["id"])
    assert sum(r["kind"] == "auto" for r in rows) == 2
    assert any(r["id"] == manual["id"] for r in rows)    # la sauvegarde manuelle n'est jamais supprimée


def test_due_schedule(panel, make_server):
    s = make_server()
    assert panel.backups.due() == []
    panel.update_server(s["id"], {"backup_prefs": {"enabled": True, "interval_hours": 1, "keep": 3, "mode": "hot"}})
    assert [x["id"] for x in panel.backups.due()] == [s["id"]]
    run_backup(panel, s["id"], "auto")
    assert panel.backups.due() == []


def test_ram_cap_ignores_running_servers(panel, make_server):
    """Le plafond par serveur part toujours de la RAM totale − réserve (V3 §2)."""
    before = panel.monitor.ram_budget()
    assert before["max_xmx_mb"] == int((before["total_mb"] - before["reserve_mb"]) * 100 / (100 + before["overhead_pct"]))
    s = make_server(name="Actif", ram=512)
    panel.manager.start(s["id"])
    try:
        after = panel.monitor.ram_budget()
        assert after["reserved_mb"] > 0
        assert after["max_xmx_mb"] == before["max_xmx_mb"]          # inchangé malgré le serveur actif
        assert after["free_budget_mb"] < before["free_budget_mb"]    # seul le contrôle au démarrage en tient compte
    finally:
        panel.manager.stop(s["id"])


def test_ram_forecast_warns_without_blocking(panel, make_server):
    b = panel.monitor.ram_budget()
    make_server(name="P1", ram=512)
    f = panel.monitor.forecast_with(b["budget_mb"])             # un gros serveur de plus : dépasse le budget
    assert f["overcommit"] and "Prévisionnel" in f["message"]
    ok = panel.monitor.forecast_with(256)
    assert not ok["overcommit"] and ok["message"] == ""
