"""V4 §7 : SSD (quota, éviction du plus ancien démarré, jamais un serveur allumé), copie vérifiée, archives de mondes."""
import json
import os
import shutil
import time
from pathlib import Path

import pytest

from conftest import tick_until
from test_api import auth, client  # noqa: F401
from mcpanel.jobs import UserError
from mcpanel.storage import GB


def world(panel, srv, mb=1):
    d = Path(panel.manager.server(srv["id"])["path"]) / "world" / "region"
    d.mkdir(parents=True, exist_ok=True)
    (d / "r.0.0.mca").write_bytes(os.urandom(mb * 1024 * 1024))
    (d.parent / "level.dat").write_bytes(b"niveau")
    panel.storage._size_cache.clear()


def run(panel, fn, *a):
    j = panel.jobs.wait(panel.jobs.submit("t", None, fn, *a), 60)
    return j


@pytest.fixture
def ssd(panel, monkeypatch):
    import mcpanel.storage as st
    monkeypatch.setattr(st, "SAFETY_FREE", 0)
    panel.db.set_setting("ssd_auto", "1")
    return panel


def test_move_to_ssd_and_back_verified(ssd, make_server):
    p = ssd
    s = make_server(name="Monde")
    world(p, s, 2)
    data = (Path(s["path"]) / "world" / "region" / "r.0.0.mca").read_bytes()
    j = run(p, p.storage.move, s["id"], "ssd")
    assert j["status"] == "done", j["error"] + j["log"]
    s2 = p.manager.server(s["id"])
    assert p.storage.tier(s2) == "ssd" and Path(s2["path"]).parent == p.storage.ssd_dir and not Path(s["path"]).exists()
    assert (Path(s2["path"]) / "world" / "region" / "r.0.0.mca").read_bytes() == data
    p.manager.start(s["id"])                                                  # le serveur fonctionne depuis le SSD
    assert tick_until(p, lambda: p.manager.state(p.manager.server(s["id"])) == "running", 20)
    j = run(p, p.storage.move, s["id"], "hdd")
    assert j["status"] == "error" and "allumé" in j["error"]                  # jamais un serveur allumé
    p.manager.stop(s["id"]); tick_until(p, lambda: not p.manager.alive(s["id"]), 20)
    assert run(p, p.storage.move, s["id"], "hdd")["status"] == "done"
    assert p.storage.tier(p.manager.server(s["id"])) == "hdd"


def test_quota_evicts_least_recently_started_stopped_server(ssd, make_server):
    p = ssd
    a, b, c = make_server(name="A"), make_server(name="B"), make_server(name="C")
    for x in (a, b, c):
        world(p, x, 3)
    p.db.set_setting("ssd_quota_gb", str(8 / 1024))                          # quota de 8 Mo pour le test
    for x, t in ((a, 100), (b, 200)):
        assert run(p, p.storage.move, x["id"], "ssd")["status"] == "done"
        p.db.execute("UPDATE servers SET last_started=? WHERE id=?", (t, x["id"]))
    plan = p.storage.plan_promotion(p.manager.server(c["id"]))
    assert plan["possible"] and [e["name"] for e in plan["evict"]] == ["A"]  # A a été démarré il y a le plus longtemps
    j = run(p, p.storage.promote_and_start, c["id"])
    assert j["status"] == "done", j["error"] + j["log"]
    tiers = {x["name"]: p.storage.tier(p.manager.server(x["id"])) for x in (a, b, c)}
    assert tiers == {"A": "hdd", "B": "ssd", "C": "ssd"} and p.manager.alive(c["id"])
    p.manager.stop(c["id"])


def test_running_server_never_evicted(ssd, make_server):
    p = ssd
    a, c = make_server(name="A"), make_server(name="C")
    world(p, a, 3); world(p, c, 3)
    p.db.set_setting("ssd_quota_gb", str(5 / 1024))
    assert run(p, p.storage.move, a["id"], "ssd")["status"] == "done"
    p.manager.start(a["id"])
    plan = p.storage.plan_promotion(p.manager.server(c["id"]))
    assert not plan["possible"] and "allumés" in plan["reason"]
    r = p.start_server(c["id"])                                              # démarre quand même, depuis le HDD
    assert r == {"ok": True} and p.storage.tier(p.manager.server(c["id"])) == "hdd"
    p.manager.stop(a["id"]); p.manager.stop(c["id"])


def test_interrupted_copy_leaves_original_and_is_cleaned(ssd, make_server):
    p = ssd
    s = make_server(name="Coupure")
    world(p, s, 1)
    tmp = p.storage.ssd_dir / (Path(s["path"]).name + ".panel-copy")
    tmp.mkdir(parents=True)
    (tmp / "moitie.mca").write_bytes(b"x")                                   # reste d'une copie interrompue
    p.storage.cleanup()
    assert not tmp.exists() and Path(s["path"]).is_dir()


def test_corrupted_copy_aborts_and_keeps_original(ssd, make_server, monkeypatch):
    p = ssd
    s = make_server(name="Abime")
    world(p, s, 1)
    import mcpanel.storage as st
    real = st._sha
    monkeypatch.setattr(st, "_sha", lambda f: "faux" if ".panel-copy" in str(f) and f.name == "r.0.0.mca" else real(f))
    j = run(p, p.storage.move, s["id"], "ssd")
    assert j["status"] == "error" and "original est intact" in j["error"]
    assert Path(s["path"]).is_dir() and p.storage.tier(p.manager.server(s["id"])) == "hdd"
    assert not list(p.storage.ssd_dir.glob("*.panel-copy"))


def test_archive_verify_restore(ssd, make_server):
    p = ssd
    s = make_server(name="Archive")
    world(p, s, 1)
    orig = (Path(s["path"]) / "world" / "region" / "r.0.0.mca").read_bytes()
    j = run(p, p.storage.archive, s["id"], True)
    assert j["status"] == "done", j["error"] + j["log"]
    assert not (Path(s["path"]) / "world").exists()                          # retiré seulement après vérification
    a = p.storage.list_archives(s["id"])[0]
    assert a["worlds"] == ["world"] and a["mc_version"] == "1.21.1" and a["sha256"] and a["present"]
    assert p.storage.verify_archive(a["id"])["ok"]
    (Path(s["path"]) / "world").mkdir(); (Path(s["path"]) / "world" / "nouveau.dat").write_text("n")
    j = run(p, p.storage.restore_archive, a["id"], s["id"])
    assert j["status"] == "done", j["error"]
    assert (Path(s["path"]) / "world" / "region" / "r.0.0.mca").read_bytes() == orig
    assert list(Path(s["path"]).glob("world.avant-restauration-*"))          # le monde présent n'est jamais supprimé
    zp = p.storage.archives_dir / a["file"]
    zp.write_bytes(zp.read_bytes()[:-10] + b"0123456789")
    assert not p.storage.verify_archive(a["id"])["ok"]
    j = run(p, p.storage.restore_archive, a["id"], s["id"])
    assert j["status"] == "error" and "intégrité" in j["error"]


def test_on_stop_backup_and_backup_verification(panel, make_server):
    s = make_server(name="Arret")
    panel.update_server(s["id"], {"backup_prefs": {"on_stop": True}})
    panel.db.execute("UPDATE servers SET desired_state='stopped' WHERE id=?", (s["id"],))
    panel.on_server_stopped(s["id"])
    tick_until(panel, lambda: panel.db.query("SELECT * FROM backups WHERE server_id=?", (s["id"],)), 20)


def test_storage_api(auth, panel, make_server):
    s = make_server(name="ApiSto")
    r = auth.get("/api/storage").json()
    assert r["quota"] == 50 * GB and r["servers"][0]["tier"] == "hdd"
    assert auth.put("/api/storage/settings", json={"quota_gb": 1}).status_code == 400
    assert auth.put("/api/storage/settings", json={"quota_gb": 60, "auto": True}).json()["quota"] == 60 * GB
    assert auth.post(f"/api/servers/{s['id']}/world/archive", json={"remove_after": True}).status_code == 400   # confirmation exigée


# ---------------------------------------------------------------- machine à disque unique (MCPANEL_SSD_TIER=0)
@pytest.fixture
def single(settings, routes):
    import dataclasses
    from conftest import FakeHttp
    from mcpanel.core import Panel
    p = Panel(dataclasses.replace(settings, ssd_tier=False), FakeHttp(routes))
    p.java.system_dir = settings.state_dir / "no-system-jvm"
    p.ensure_data_dirs()
    p.db.set_setting("ssd_auto", "1")                      # même réglé sur « automatique », rien ne doit bouger
    yield p
    try:
        for srv in p.db.query("SELECT id FROM servers"):
            if p.manager.alive(srv["id"]):
                p.manager.kill(srv["id"])
    finally:
        p.jobs.shutdown()


def test_ssd_tier_setting_from_env(monkeypatch):
    from mcpanel.config import Settings
    monkeypatch.delenv("MCPANEL_SSD_TIER", raising=False)
    assert Settings.from_env().ssd_tier is True                       # défaut inchangé : niveau SSD actif
    monkeypatch.setenv("MCPANEL_SSD_TIER", "0")
    assert Settings.from_env().ssd_tier is False


def test_single_disk_mode_has_no_ssd_tier(single, make_server):
    p = single
    s = make_server(name="Unique")
    world(p, s, 1)
    ov = p.storage.overview()
    assert ov["tiering"] is False and ov["auto"] is False
    assert ov["servers"][0]["tier"] == "hdd"
    assert not p.storage.ssd_dir.exists()                              # aucun dossier « ssd » créé
    srv = p.manager.server(s["id"])
    assert p.storage.needs_promotion(srv) is False
    plan = p.storage.plan_promotion(srv)
    assert plan["possible"] is False and "disque unique" in plan["reason"]
    j = run(p, p.storage.move, s["id"], "ssd")
    assert j["status"] == "error" and "disque unique" in j["error"]
    assert Path(p.manager.server(s["id"])["path"]).parent == p.s.servers_dir      # le monde n'a pas bougé
    p.manager.start(s["id"])                                           # le démarrage normal fonctionne
    assert tick_until(p, lambda: p.manager.state(p.manager.server(s["id"])) == "running", 20)
    p.manager.stop(s["id"]); tick_until(p, lambda: not p.manager.alive(s["id"]), 20)
    j = run(p, p.storage.archive, s["id"], False)                      # l'archivage du monde reste disponible
    assert j["status"] == "done", j["error"]
