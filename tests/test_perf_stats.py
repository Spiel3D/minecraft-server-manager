"""V4 §6 : TPS / MSPT par type de serveur, historique, sessions et statistiques Minecraft."""
import json
import time

import pytest

from conftest import tick_until
from test_api import auth, client  # noqa: F401
from mcpanel.perf import method_for, parse
from mcpanel.mcstats import player_stats


@pytest.mark.parametrize("method,text,tps,mspt", [
    ("spigot", "[12:00:00] [Server thread/INFO]: TPS from last 1m, 5m, 15m: *20.0, 19.5, 19.9", 20.0, None),
    ("paper", "TPS from last 1m, 5m, 15m: 18.2, 19.0, 19.5\nServer tick times (avg/min/max) from last 5s, 10s, 1m:\n◴ 54.9/3.1/80.2, 50/2/60, 40/2/50", 18.2, 54.9),
    ("forge", "[Server thread/INFO]: minecraft:overworld: Mean tick time: 6.100 ms. Mean TPS: 20.000\n[Server thread/INFO]: Overall: Mean tick time: 62.500 ms. Mean TPS: 16.000", 16.0, 62.5),
    ("tick", "The game is running normally\nAverage time per tick: 7.5ms (Target: 50.0ms)", 20.0, 7.5),
    ("tick", "Average time per tick: 80ms", 12.5, 80.0),
    ("spark", "TPS from last 5s, 10s, 1m, 5m, 15m:\n20.0, 20.0, 19.7, 19.9, 20.0\nTick durations (min/med/95%ile/max ms) from last 10s, 1m:\n1.2/4.5/9.0/30.0; 1.0/5.5/10.0/40.0", 19.7, 5.5),
    ("tick", "rien d'utile ici", None, None),
])
def test_parse_formats(method, text, tps, mspt):
    r = parse(method, text)
    assert r["tps"] == tps and r["mspt"] == mspt


def test_method_selection(tmp_path):
    def srv(t, mc):
        return {"server_type": t, "mc_version": mc, "path": str(tmp_path)}
    assert method_for(srv("paper", "1.21.1"))[0] == "paper"
    assert method_for(srv("mohist", "1.20.1"))[0] == "spigot"
    assert method_for(srv("forge", "1.20.1"))[0] == "forge"
    assert method_for(srv("vanilla", "1.21.1"))[0] == "tick"
    m, why = method_for(srv("fabric", "1.20.1"))
    assert m is None and "1.20.3" in why and "spark" in why                       # jamais de valeur inventée : on explique
    (tmp_path / "mods").mkdir(); (tmp_path / "mods" / "spark-1.10.jar").write_text("x")
    assert method_for(srv("fabric", "1.20.1"))[0] == "spark"


def test_live_tps_history_and_sessions(panel, make_server):
    s = make_server(name="Perf", mc="1.21.1")
    panel.perf.interval, panel.perf.wait = 0, 0.3
    panel.manager.start(s["id"])
    assert tick_until(panel, lambda: panel.manager.state(panel.manager.server(s["id"])) == "running", 30)
    panel.perf.tick()                                                          # mémorise la position de la console
    panel.manager.send_command(s["id"], "join Alex")
    def measured():
        panel.perf.tick()
        return panel.perf.latest.get(s["id"])
    assert tick_until(panel, measured, 15)
    cur = panel.perf.info(panel.manager.server(s["id"]))
    assert cur["available"] and cur["mspt"] == 7.5 and cur["tps"] == 20.0
    ov = [o for o in panel.monitor.servers_overview() if o["id"] == s["id"]][0]
    assert ov["tps"] == 20.0 and ov["mspt"] == 7.5 and ov["perf_available"]
    assert panel.db.query("SELECT * FROM metrics WHERE server_id=?", (s["id"],))
    assert tick_until(panel, lambda: (panel.perf.tick(), panel.db.query("SELECT * FROM play_sessions WHERE server_id=?", (s["id"],)))[1], 10)
    panel.manager.send_command(s["id"], "leave Alex")
    assert tick_until(panel, lambda: (panel.perf.tick(), panel.db.one("SELECT left_at FROM play_sessions WHERE server_id=?", (s["id"],))["left_at"])[1], 10)
    panel.manager.stop(s["id"])


def test_unavailable_is_reported_not_invented(panel, make_server):
    s = make_server(name="Vieux", mc="1.20.1")
    ov = [o for o in panel.monitor.servers_overview() if o["id"] == s["id"]][0]
    assert ov["perf_available"] is False and ov["tps"] is None and "1.20.3" in ov["perf_method"]


def test_player_stats_formats():
    new = {"stats": {"minecraft:custom": {"minecraft:play_time": 72000, "minecraft:deaths": 3, "minecraft:mob_kills": 40, "minecraft:walk_one_cm": 150000, "minecraft:sprint_one_cm": 50000},
                     "minecraft:mined": {"minecraft:stone": 100, "minecraft:dirt": 20}, "minecraft:crafted": {"minecraft:torch": 64}}}
    p = player_stats(new)
    assert p["play_time_s"] == 3600 and p["deaths"] == 3 and p["distance_km"] == 2.0 and p["blocks_mined"] == 120 and p["items_crafted"] == 64
    old = {"stat.playOneMinute": 1200, "stat.deaths": 1, "stat.mineBlock.minecraft.stone": 5}
    q = player_stats(old)
    assert q["play_time_s"] == 60 and q["deaths"] == 1 and q["blocks_mined"] == 5


def test_mcstats_api(auth, panel, make_server):
    s = make_server(name="Stats")
    d = panel.s.servers_dir / s["name"]
    uid = "36532b5e-c442-3dbb-a24c-c7e55d0f979a"
    (d / "world" / "stats").mkdir(parents=True); (d / "world" / "advancements").mkdir()
    (d / "world" / "stats" / f"{uid}.json").write_text(json.dumps({"stats": {"minecraft:custom": {"minecraft:play_time": 144000, "minecraft:deaths": 2}}}))
    (d / "world" / "advancements" / f"{uid}.json").write_text(json.dumps({"minecraft:story/root": {"done": True}, "minecraft:recipes/x": {"done": True}, "minecraft:story/mine_stone": {"done": False}}))
    (d / "usercache.json").write_text(json.dumps([{"name": "Alex", "uuid": uid, "expiresOn": "x"}]))
    r = auth.get(f"/api/servers/{s['id']}/mcstats").json()
    assert r["world_found"] and r["summary"]["players"] == 1 and r["summary"]["play_time_s"] == 7200
    pl = r["players"][0]
    assert pl["name"] == "Alex" and pl["advancements"] == 1 and pl["deaths"] == 2
    assert auth.get(f"/api/servers/{s['id']}/perf?hours=1").json()["current"]["available"] is True
