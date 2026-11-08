"""V3 §4 : liste blanche par serveur, serveur arrêté et en marche."""
import json

import pytest

from conftest import tick_until
from test_api import auth, client  # noqa: F401  (fixtures)
from mcpanel.jobs import UserError
from mcpanel.whitelist import offline_uuid


def wl(panel, srv):
    return json.loads((panel.s.servers_dir / srv["name"] / "whitelist.json").read_text())


def test_offline_uuid_known_value():
    assert offline_uuid("Notch") == "b50ad385-829d-3141-a216-7e7d7539ba7f"      # valeur connue du mode hors ligne


def test_stopped_offline_mode_add_remove_toggle(panel, make_server):
    s = make_server(name="Liste")
    from mcpanel.installer import set_properties
    set_properties(panel.s.servers_dir / s["name"] / "server.properties", {"online-mode": "false"})
    st = panel.whitelist.status(s["id"])
    assert st["entries"] == [] and not st["enabled"] and not st["online_mode"] and not st["running"]
    r = panel.whitelist.add(s["id"], "Alex_1")
    assert r["via"] == "fichier" and wl(panel, s) == [{"uuid": offline_uuid("Alex_1"), "name": "Alex_1"}]
    with pytest.raises(UserError, match="déjà"):
        panel.whitelist.add(s["id"], "alex_1")
    for bad in ("", "a b", "x" * 17, "../x", "a;b"):
        with pytest.raises(UserError):
            panel.whitelist.add(s["id"], bad)
    panel.whitelist.add(s["id"], "Steve")
    assert [e["name"] for e in panel.whitelist.status(s["id"], "ste")["entries"]] == ["Steve"]       # recherche
    panel.whitelist.set_enabled(s["id"], True)
    assert panel.whitelist.status(s["id"])["enabled"]
    panel.whitelist.remove(s["id"], "alex_1")
    assert [e["name"] for e in wl(panel, s)] == ["Steve"]
    with pytest.raises(UserError, match="pas sur"):
        panel.whitelist.remove(s["id"], "Inconnu")


def test_stopped_online_mode_uses_mojang_and_never_invents(panel, make_server, routes):
    s = make_server(name="Online")
    routes["https://api.mojang.com/users/profiles/minecraft/Dream"] = {"id": "ec70bcaf702f4bb8b48d276fa52a780c", "name": "Dream"}
    r = panel.whitelist.add(s["id"], "Dream")
    assert r["added"] == "Dream" and r["uuid"] == "ec70bcaf-702f-4bb8-b48d-276fa52a780c"
    with pytest.raises(UserError, match="n'existe pas"):
        panel.whitelist.add(s["id"], "Personne")                      # 404 Mojang
    from mcpanel.sources import SourceError

    def boom(u, d):
        raise SourceError("Échec réseau pour " + u)
    routes["https://api.mojang.com/users/profiles/minecraft/Reseau"] = boom
    with pytest.raises(UserError, match="Impossible de joindre Mojang"):
        panel.whitelist.add(s["id"], "Reseau")
    assert [e["name"] for e in wl(panel, s)] == ["Dream"]               # rien d'ajouté en cas d'échec


def test_running_uses_console_and_confirms(panel, make_server):
    s = make_server(name="Vivant")
    panel.manager.start(s["id"])
    assert tick_until(panel, lambda: panel.manager.state(panel.manager.server(s["id"])) == "running")
    r = panel.whitelist.add(s["id"], "Alex")
    assert r["via"] == "console" and [e["name"] for e in wl(panel, s)] == ["Alex"]
    st = panel.whitelist.status(s["id"])
    assert st["running"] and st["entries"][0]["online"] is True and st["players_online_known"]          # « Alex » est dans l'échantillon du faux ping
    with pytest.raises(UserError, match="n'a pas confirmé"):
        panel.whitelist.add(s["id"], "Inexistant1")                      # le serveur refuse : on le dit
    assert panel.whitelist.set_enabled(s["id"], True)["via"] == "console"
    assert panel.whitelist.status(s["id"])["enabled"]
    panel.whitelist.remove(s["id"], "Alex")
    assert wl(panel, s) == []
    panel.manager.stop(s["id"])


def test_isolation_between_servers(panel, make_server):
    a, b = make_server(name="A"), make_server(name="B")
    from mcpanel.installer import set_properties
    for x in (a, b):
        set_properties(panel.s.servers_dir / x["name"] / "server.properties", {"online-mode": "false"})
    panel.whitelist.add(a["id"], "Seul")
    assert panel.whitelist.status(b["id"])["total"] == 0


def test_whitelist_api(auth, panel, make_server):
    c = auth
    s = make_server(name="Api")
    from mcpanel.installer import set_properties
    set_properties(panel.s.servers_dir / s["name"] / "server.properties", {"online-mode": "false"})
    assert c.post(f"/api/servers/{s['id']}/whitelist", json={"name": "Zed"}).status_code == 200
    assert c.get(f"/api/servers/{s['id']}/whitelist?q=ze").json()["entries"][0]["name"] == "Zed"
    assert c.post(f"/api/servers/{s['id']}/whitelist", json={"name": "Zed"}).status_code == 400
    assert c.put(f"/api/servers/{s['id']}/whitelist/enabled", json={"enabled": True}).json()["enabled"] is True
    assert c.delete(f"/api/servers/{s['id']}/whitelist/Zed").status_code == 200
    c.headers.pop("X-CSRF-Token")
    c.cookies.clear()
    assert c.get(f"/api/servers/{s['id']}/whitelist").status_code in (401, 403)
