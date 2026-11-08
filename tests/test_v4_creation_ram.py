"""V4 §1-2 : propositions de RAM et configuration complète dès la création."""
import json

import pytest

from conftest import free_port
from mcpanel.jobs import UserError
from mcpanel.whitelist import offline_uuid

BASE = {"server_type": "vanilla", "mc_version": "1.21.1", "ram_max_mb": 512, "ram_min_mb": 256}


def test_presets_10_12_max(panel, monkeypatch):
    import mcpanel.monitor as mon
    monkeypatch.setattr(mon, "meminfo", lambda: {"MemTotal": 15800, "MemAvailable": 12000})
    panel.db.set_setting("ram_reserve_mb", "2048")
    b = panel.monitor.ram_budget()
    assert b["max_xmx_mb"] == 11460                                    # (15800 − 2048) / 1,20
    pr = {p["label"].split(" ")[0]: p for p in b["presets"]}
    assert pr["10"]["ok"] and not pr["12"]["ok"] and pr["Maximum"]["mb"] == 11264 and pr["Maximum"]["ok"]
    panel.db.set_setting("ram_overhead_pct", "10")                      # marge réduite : 12 Go devient proposable
    pr = {p["label"].split(" ")[0]: p for p in panel.monitor.ram_budget()["presets"]}
    assert pr["12"]["ok"]
    panel.db.set_setting("ram_overhead_pct", "20")
    monkeypatch.setattr(mon, "meminfo", lambda: {"MemTotal": 12000, "MemAvailable": 9000})
    pr = {p["label"].split(" ")[0]: p for p in panel.monitor.ram_budget()["presets"]}
    assert pr["10"]["ok"] is False                                                     # 10 Go > 9952 Mo : non proposé
    assert pr["12"]["ok"] is False and "dépasse" in pr["12"]["reason"]


def test_ram_summary_fields(panel, make_server):
    s = make_server(name="Mem", ram=768)
    panel.manager.start(s["id"])
    b = panel.monitor.ram_budget()
    assert b["assigned_mb"] == 768 and b["used_mb"] >= 0 and b["remaining_mb"] == b["free_budget_mb"]
    panel.manager.stop(s["id"])


def test_max_preset_can_really_start(panel, make_server):
    """Bug trouvé en test navigateur : le « maximum » proposé ne pouvait jamais démarrer (marge Java oubliée dans le plafond)."""
    b = panel.monitor.ram_budget()
    mx = [p for p in b["presets"] if p["label"].startswith("Maximum")][0]["mb"]
    s = make_server(name="Max", ram=512)
    panel.db.execute("UPDATE servers SET ram_max_mb=? WHERE id=?", (mx, s["id"]))
    panel.manager.start(s["id"])                                        # ne doit pas lever « Mémoire insuffisante »
    panel.manager.stop(s["id"])


def test_reconfig_uses_same_cap(panel, make_server):
    s = make_server(name="Cap")
    cap = panel.monitor.ram_budget(exclude_sid=s["id"])["max_xmx_mb"]
    panel.update_server(s["id"], {"ram_max_mb": cap, "ram_min_mb": 512})                 # le maximum exact est accepté
    with pytest.raises(UserError, match="maximum"):
        panel.update_server(s["id"], {"ram_max_mb": cap + 256})


def test_full_config_at_creation(panel):
    props = {"gamemode": "creative", "difficulty": "hard", "hardcore": True, "pvp": False, "level-seed": "12345", "max-players": 8,
             "motd": "Salut ✨", "view-distance": 12, "simulation-distance": 8, "spawn-protection": 0, "online-mode": False, "level-type": "minecraft:flat"}
    srv, jid = panel.create_server({**BASE, "name": "Complet", "port": free_port(), "properties": props, "confirm_offline": True,
                                    "whitelist": {"enabled": True, "players": "Alex, Steve Alex"}})
    j = panel.jobs.wait(jid, 60)
    assert j["status"] == "done", j["error"] + j["log"]
    d = panel.s.servers_dir / srv["name"]
    from mcpanel.mcsettings import PropsFile
    v = PropsFile((d / "server.properties").read_text()).values
    assert v["gamemode"] == "creative" and v["hardcore"] == "true" and v["level-seed"] == "12345" and v["motd"] == "Salut ✨"
    assert v["white-list"] == "true" and v["online-mode"] == "false" and v["level-type"] == "minecraft:flat"
    assert v["server-port"] == str(srv["port"])
    assert json.loads((d / "whitelist.json").read_text()) == [{"uuid": offline_uuid("Alex"), "name": "Alex"}, {"uuid": offline_uuid("Steve"), "name": "Steve"}]
    assert panel.manager.server(srv["id"])["pending_config"] == {}


def test_creation_config_validated_before_anything(panel):
    bad = [({"properties": {"view-distance": 99}}, "entre"), ({"properties": {"online-mode": False}}, "usurper"), ({"properties": {"server-port": 1}}, "propre champ"),
           ({"properties": {"inconnu": 1}}, "inconnu"), ({"whitelist": {"players": ["pas valide!"]}}, "pseudo invalide"),
           ({"properties": {"enable-rcon": True}}, "RCON")]
    for extra, msg in bad:
        with pytest.raises(UserError, match=msg):
            panel.create_server({**BASE, "name": "Refus", "port": free_port(), **extra})
    assert panel.db.query("SELECT * FROM servers") == [] and not (panel.s.servers_dir / "Refus").exists()


def test_online_mode_whitelist_lookup_failure_does_not_break_install(panel, routes):
    routes["https://api.mojang.com/users/profiles/minecraft/Dream"] = {"id": "ec70bcaf702f4bb8b48d276fa52a780c", "name": "Dream"}
    srv, jid = panel.create_server({**BASE, "name": "En ligne", "port": free_port(), "whitelist": {"players": ["Dream", "Personne"]}})
    j = panel.jobs.wait(jid, 60)
    assert j["status"] == "done" and "Personne" in j["log"]
    d = panel.s.servers_dir / srv["name"]
    assert [e["name"] for e in json.loads((d / "whitelist.json").read_text())] == ["Dream"]
    assert any("Whitelist" in a["message"] for a in panel.alerts.active())
