"""V3 §5 : éditeur complet de server.properties."""
import pytest

from conftest import tick_until
from test_api import auth, client  # noqa: F401
from mcpanel.jobs import UserError
from mcpanel.mcsettings import BY_KEY, PropsFile, SCHEMA, escape, unescape, validate_value


def props_path(panel, s):
    return panel.s.servers_dir / s["name"] / "server.properties"


def test_schema_is_complete_and_coherent():
    keys = [f["key"] for f in SCHEMA]
    assert len(keys) == len(set(keys)) and len(keys) >= 55
    for must in ("motd", "max-players", "online-mode", "view-distance", "simulation-distance", "difficulty", "gamemode", "pvp", "hardcore",
                 "level-name", "level-seed", "white-list", "enforce-whitelist", "spawn-protection", "allow-flight", "enable-rcon", "resource-pack",
                 "network-compression-threshold", "max-tick-time", "enable-query", "player-idle-timeout", "allow-nether", "generate-structures"):
        assert must in keys, must
    for f in SCHEMA:
        assert f["description"] and f["label"] and f["category"] in {"general", "game", "world", "perf", "network", "security", "resource", "advanced"}
        if f["type"] == "enum":
            assert f["default"] in f["options"]
        if f["type"] == "int":
            assert validate_value(f, f["default"]) == str(int(f["default"]))      # chaque défaut respecte ses propres bornes


def test_escape_roundtrip_and_unicode():
    for v in ["minecraft:normal", "Été ☀ 日本", "a\\b", " début", "x=y#z!", "§aVert §rnormal"]:
        assert unescape(escape(v)) == v
    assert "é" not in escape("é") and "\\u00e9" in escape("é")
    with pytest.raises(UserError):
        escape("a\nb")


def test_file_fidelity_comments_unknown_keys(panel, make_server):
    s = make_server(name="Fid")
    p = props_path(panel, s)
    p.write_text("# commentaire\nmotd=Salut\nmon-mod.option=42\nlevel-type=minecraft\\:flat\n", encoding="utf-8")
    g = panel.mcsettings.get(s["id"])
    assert g["values"]["motd"]["value"] == "Salut" and g["values"]["level-type"]["value"] == "minecraft:flat"
    assert g["unknown"] == {"mon-mod.option": "42"}
    r = panel.mcsettings.save(s["id"], {"max-players": 8, "motd": "Bienvenue ✨"}, g["hash"], user="admin")
    assert r["changed"] == ["max-players", "motd"]
    text = p.read_text(encoding="utf-8")
    assert text.startswith("# commentaire\n") and "mon-mod.option=42" in text and "level-type=minecraft\\:flat" in text
    assert "motd=Bienvenue \\u2728" in text and "max-players=8" in text
    assert PropsFile(text).values["motd"] == "Bienvenue ✨"


def test_validation_blocks_bad_values_without_writing(panel, make_server):
    s = make_server(name="Val")
    p = props_path(panel, s)
    before = p.read_text()
    h = panel.mcsettings.get(s["id"])["hash"]
    for bad in ({"view-distance": 99}, {"view-distance": "abc"}, {"difficulty": "extreme"}, {"pvp": "peut-être"}, {"motd": "a\nb"},
                {"max-players": -1}, {"server-port": 25570}, {"white-list": True}, {"bad key": "x"}, {"enable-rcon": True}):
        with pytest.raises(UserError):
            panel.mcsettings.save(s["id"], bad, h)
    assert p.read_text() == before                                      # aucune écriture partielle
    assert panel.db.one("SELECT COUNT(*) c FROM props_history")["c"] == 0


def test_online_mode_needs_explicit_confirmation(panel, make_server):
    s = make_server(name="Off")
    h = panel.mcsettings.get(s["id"])["hash"]
    with pytest.raises(UserError, match="usurper"):
        panel.mcsettings.save(s["id"], {"online-mode": False}, h)
    panel.mcsettings.save(s["id"], {"online-mode": False}, h, confirm_offline=True)
    assert "online-mode=false" in props_path(panel, s).read_text()


def test_cross_checks_and_warnings(panel, make_server):
    s = make_server(name="Cx")
    g = panel.mcsettings.get(s["id"])
    r = panel.mcsettings.save(s["id"], {"hardcore": True, "difficulty": "easy", "enforce-whitelist": True, "level-seed": "42", "level-name": "autre"}, g["hash"])
    w = " ".join(r["warnings"])
    assert "Hardcore" in w and "whitelist" in w and "Nouveau monde" in w and "n'existe pas encore" in w
    g = panel.mcsettings.get(s["id"])
    with pytest.raises(UserError, match="identique au port du serveur"):
        panel.mcsettings.save(s["id"], {"enable-query": True, "query.port": s["port"]}, g["hash"])
    other = make_server(name="Autre")
    with pytest.raises(UserError, match="autre serveur"):
        panel.mcsettings.save(s["id"], {"enable-query": True, "query.port": other["port"]}, g["hash"])
    with pytest.raises(UserError, match="SHA-1"):
        panel.mcsettings.save(s["id"], {"resource-pack": "https://x/p.zip", "resource-pack-sha1": "xyz"}, g["hash"])
    panel.mcsettings.save(s["id"], {"enable-rcon": True, "rcon.password": "motdepasse-long"}, g["hash"])
    g2 = panel.mcsettings.get(s["id"])
    assert g2["values"]["rcon.password"]["value"] == "" and g2["values"]["rcon.password"]["secret_set"]      # le secret n'est jamais renvoyé
    panel.mcsettings.save(s["id"], {"rcon.password": "", "max-players": 3}, g2["hash"])                        # vide = on garde
    assert "rcon.password=motdepasse-long" in props_path(panel, s).read_text()


def test_concurrent_change_is_detected(panel, make_server):
    s = make_server(name="Conc")
    g = panel.mcsettings.get(s["id"])
    p = props_path(panel, s)
    p.write_text(p.read_text() + "pvp=false\n")                          # le serveur (ou un autre onglet) modifie le fichier
    with pytest.raises(UserError, match="a changé"):
        panel.mcsettings.save(s["id"], {"max-players": 5}, g["hash"])
    assert "max-players=5" not in p.read_text()


def test_history_and_revert(panel, make_server):
    s = make_server(name="Hist")
    p = props_path(panel, s)
    original = p.read_text()
    g = panel.mcsettings.get(s["id"])
    panel.mcsettings.save(s["id"], {"max-players": 7}, g["hash"], user="admin")
    g = panel.mcsettings.get(s["id"])
    panel.mcsettings.save(s["id"], {"pvp": False}, g["hash"], user="admin", note="test")
    h = panel.mcsettings.history(s["id"])
    assert [x["changed"] for x in h] == [["pvp"], ["max-players"]] and h[0]["note"] == "test"
    panel.mcsettings.revert(s["id"], h[-1]["id"], user="admin")           # retour à l'état d'origine
    assert p.read_text() == original
    assert len(panel.mcsettings.history(s["id"])) == 3                    # le retour est lui-même historisé (annulable)
    with pytest.raises(UserError):
        panel.mcsettings.revert(s["id"], 99999)


def test_delete_unknown_key_and_running_restart_flag(panel, make_server):
    s = make_server(name="Run")
    p = props_path(panel, s)
    p.write_text(p.read_text() + "vieille.option=1\n")
    g = panel.mcsettings.get(s["id"])
    panel.mcsettings.save(s["id"], {}, g["hash"], unknown_delete=["vieille.option", "motd"])             # « motd » est connu : jamais supprimé
    assert "vieille.option" not in p.read_text() and "motd=" in p.read_text()
    panel.manager.start(s["id"])
    assert tick_until(panel, lambda: panel.manager.state(panel.manager.server(s["id"])) == "running")
    g = panel.mcsettings.get(s["id"])
    r = panel.mcsettings.save(s["id"], {"max-players": 9, "player-idle-timeout": 5}, g["hash"])
    assert r["running"] and r["restart_required"] == ["max-players"]      # idle-timeout est appliqué à chaud
    panel.manager.stop(s["id"])


def test_version_gated_fields(panel, make_server):
    s = make_server(name="Ver", mc="1.20.1")
    keys = set(panel.mcsettings.get(s["id"])["values"])
    assert "pause-when-empty-seconds" not in keys and "accepts-transfers" not in keys and "max-players" in keys
    s2 = make_server(name="Ver2", mc="1.21.1")
    assert "accepts-transfers" in panel.mcsettings.get(s2["id"])["values"]


def test_mcsettings_api(auth, panel, make_server):
    c = auth
    s = make_server(name="Api2")
    assert len(c.get("/api/meta/mcsettings-schema").json()["fields"]) >= 55
    g = c.get(f"/api/servers/{s['id']}/mcsettings").json()
    r = c.put(f"/api/servers/{s['id']}/mcsettings", json={"changes": {"max-players": 12}, "hash": g["hash"]})
    assert r.status_code == 200 and r.json()["changed"] == ["max-players"]
    assert c.put(f"/api/servers/{s['id']}/mcsettings", json={"changes": {"max-players": 13}, "hash": g["hash"]}).status_code == 400      # empreinte périmée
    assert c.get(f"/api/servers/{s['id']}/mcsettings/history").json()["history"]
    assert BY_KEY["motd"]["label"]
