"""V4 §4 : port principal unique et réattribution automatique."""
import socket

import pytest

from conftest import free_port, tick_until
from test_api import auth, client  # noqa: F401
from mcpanel.jobs import UserError
from mcpanel.mcsettings import PropsFile
from mcpanel.process import ProcessError, mc_ping


def prop_port(panel, srv):
    return PropsFile((panel.s.servers_dir / srv["name"] / "server.properties").read_text()).values["server-port"]


@pytest.fixture
def primary(panel):
    p = free_port()
    panel.db.set_setting("primary_port", str(p))
    return p


def _mk(panel, make_server, name, port=None):
    s = make_server(name=name)
    if port:
        panel.db.execute("UPDATE servers SET port=? WHERE id=?", (port, s["id"]))
        panel.primary._write_port(s["id"], port)
    return panel.manager.server(s["id"])


def running(panel, sid):
    return tick_until(panel, lambda: panel.manager.state(panel.manager.server(sid)) == "running", 30)


def test_switch_with_stopped_servers(panel, make_server, primary):
    a = _mk(panel, make_server, "A", primary)
    b = _mk(panel, make_server, "B")
    plan = panel.primary.plan(b["id"])
    assert [c["to"] for c in plan["changes"]][0] == primary and not plan["restart"]
    jid = panel.jobs.submit("primary", b["id"], panel.primary.execute, b["id"])
    j = panel.jobs.wait(jid, 60)
    assert j["status"] == "done", j["error"] + j["log"]
    a2, b2 = panel.manager.server(a["id"]), panel.manager.server(b["id"])
    assert b2["port"] == primary and prop_port(panel, b2) == str(primary)
    assert a2["port"] != primary and a2["port"] > primary and prop_port(panel, a2) == str(a2["port"])
    assert panel.primary.holder()["id"] == b["id"]
    assert [o for o in panel.monitor.servers_overview() if o["primary"]][0]["id"] == b["id"]
    with pytest.raises(UserError, match="déjà le serveur principal"):
        panel.primary.plan(b["id"])


def test_switch_with_running_servers_restarts_and_verifies(panel, make_server, primary):
    a = _mk(panel, make_server, "A", primary)
    b = _mk(panel, make_server, "B")
    panel.manager.start(a["id"]); panel.manager.start(b["id"])
    assert running(panel, a["id"]) and running(panel, b["id"])
    plan = panel.primary.plan(b["id"])
    assert sorted(plan["restart"]) == ["A", "B"] and plan["players_affected"] == 4          # le faux serveur annonce 2 joueurs
    assert set(plan["changes"][0]["players"]) == {"Alex", "Steve"}
    j = panel.jobs.wait(panel.jobs.submit("primary", b["id"], panel.primary.execute, b["id"]), 120)
    assert j["status"] == "done", j["error"] + j["log"]
    assert j["result"]["verified"] == "joignable" and mc_ping(primary)
    a2 = panel.manager.server(a["id"])
    assert panel.manager.alive(a["id"]) and panel.manager.alive(b["id"])
    assert tick_until(panel, lambda: mc_ping(a2["port"]), 30)                                # l'ancien principal répond sur son nouveau port
    panel.manager.stop(a["id"]); panel.manager.stop(b["id"])


def test_failure_rolls_back(panel, make_server, primary):
    a = _mk(panel, make_server, "A", primary)
    b = _mk(panel, make_server, "B")
    (panel.s.servers_dir / b["name"] / "no-ping").write_text("")                             # le nouveau principal ne répondra jamais
    panel.manager.start(a["id"]); panel.manager.start(b["id"])
    assert running(panel, a["id"])
    panel.primary.verify_timeout = 4
    j = panel.jobs.wait(panel.jobs.submit("primary", b["id"], panel.primary.execute, b["id"]), 200)
    assert j["status"] != "done" and "ne répond pas" in j["error"]
    assert panel.manager.server(a["id"])["port"] == primary and prop_port(panel, panel.manager.server(a["id"])) == str(primary)
    assert panel.manager.server(b["id"])["port"] == b["port"]
    assert panel.manager.alive(a["id"]) and panel.manager.alive(b["id"])                     # tout est remis comme avant
    assert not panel.manager.blocked
    panel.manager.stop(a["id"]); panel.manager.stop(b["id"])


def test_external_program_on_primary_port_is_refused(panel, make_server, primary):
    b = _mk(panel, make_server, "B")
    sock = socket.socket(); sock.bind(("0.0.0.0", primary)); sock.listen(1)
    try:
        with pytest.raises(UserError, match="programme extérieur"):
            panel.primary.plan(b["id"])
    finally:
        sock.close()


def test_start_blocked_during_operation(panel, make_server):
    s = make_server(name="Bloque")
    panel.manager.blocked[s["id"]] = "test"
    with pytest.raises(ProcessError, match="Opération en cours"):
        panel.manager.start(s["id"])
    panel.manager.blocked.clear()


def test_primary_api_requires_confirmation_when_running(auth, panel, make_server, primary):
    c = auth
    a = _mk(panel, make_server, "A", primary)
    b = _mk(panel, make_server, "B")
    panel.manager.start(b["id"])
    assert running(panel, b["id"])
    assert c.get("/api/primary").json()["server"]["id"] == a["id"]
    assert c.get(f"/api/servers/{b['id']}/primary/plan").json()["restart"] == ["B"]
    assert c.post(f"/api/servers/{b['id']}/primary", json={}).status_code == 400
    r = c.post(f"/api/servers/{b['id']}/primary", json={"confirm": True})
    assert r.status_code == 200
    j = panel.jobs.wait(r.json()["job"], 120)
    assert j["status"] == "done", j["error"]
    panel.manager.stop(b["id"])
