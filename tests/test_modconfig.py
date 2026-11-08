"""V4 §3 : éditeur graphique des configurations (préservation, types, bornes, sauvegardes, retour à l'original)."""
import json
import tomllib

import pytest

from test_api import auth, client  # noqa: F401
from mcpanel.jobs import UserError
from mcpanel.modconfig import apply, parse

TOML = """# Configuration de test
[general]
\t#Active la fonctionnalité
\tenabled = true
\t#Nombre de joueurs
\t#Range: 1 ~ 64
\tmaxPlayers = 8 # commentaire en ligne
\t#Allowed Values: EASY, HARD
\tmode = "EASY"
\tblocks = ["minecraft:stone", "minecraft:dirt"]
\tbig = [
\t\t"a",
\t\t"b"
\t]

[client.render]
\tdistance = 1.5
\tunknownKey = "garde-moi"
"""


def test_toml_parse_and_metadata():
    e = {x["key"]: x for x in parse(TOML, "toml")}
    assert e["general.enabled"]["type"] == "bool" and e["general.enabled"]["description"] == "Active la fonctionnalité"
    assert e["general.maxPlayers"]["min"] == 1 and e["general.maxPlayers"]["max"] == 64 and e["general.maxPlayers"]["value"] == "8"
    assert e["general.mode"]["type"] == "enum" and e["general.mode"]["options"] == ["EASY", "HARD"]
    assert e["general.blocks"]["type"] == "list" and e["general.big"]["readonly"]
    assert e["client.render.distance"]["type"] == "float"


def test_toml_edit_preserves_everything_else():
    out = apply(TOML, "toml", {"general.maxPlayers": 12, "general.enabled": False, "general.mode": "HARD", "general.blocks": '["minecraft:sand"]'})
    assert "\tmaxPlayers = 12 # commentaire en ligne" in out and "\tenabled = false" in out and 'mode = "HARD"' in out
    assert "# Configuration de test" in out and 'unknownKey = "garde-moi"' in out and '\t\t"a",' in out
    d = tomllib.loads(out)
    assert d["general"]["blocks"] == ["minecraft:sand"] and d["client"]["render"]["unknownKey"] == "garde-moi"
    for bad in ({"general.maxPlayers": 100}, {"general.maxPlayers": "x"}, {"general.mode": "MOYEN"}, {"general.enabled": "peut-être"},
                {"general.big": "[]"}, {"inconnu.cle": 1}, {"general.blocks": "pas une liste"}):
        with pytest.raises(UserError):
            apply(TOML, "toml", bad)


def test_json_yaml_properties_cfg():
    js = '{\n    "a": {"b": 3, "c": "x", "flag": true},\n    "list": [1, 2]\n}\n'
    out = apply(js, "json", {"a.b": 5, "a.flag": False, "list": "[3]"})
    d = json.loads(out)
    assert d == {"a": {"b": 5, "c": "x", "flag": False}, "list": [3]} and out.startswith('{\n    "a"')
    yml = "# plugin\nsettings:\n  # Délai\n  delay: 20\n  name: 'Serveur'\n  items:\n    - a\nother: true # fin\n"
    e = {x["key"]: x for x in parse(yml, "yaml")}
    assert e["settings.delay"]["description"] == "Délai" and e["settings.name"]["value"] == "Serveur" and e["other"]["type"] == "bool"
    out = apply(yml, "yaml", {"settings.delay": 40, "settings.name": "L'été", "other": "false"})
    assert "  delay: 40" in out and "  name: 'L''été'" in out and "other: false # fin" in out and "    - a" in out
    pr = "# x\nkey=valeur\nnum=3\n"
    assert "num=4" in apply(pr, "properties", {"num": 4})
    cfg = "general {\n    # Active\n    B:enabled=true\n    I:count=5\n    S:list <\n        a\n     >\n}\n"
    e = {x["key"]: x for x in parse(cfg, "cfg")}
    assert e["general.enabled"]["type"] == "bool" and e["general.list"]["readonly"]
    assert "    I:count=9" in apply(cfg, "cfg", {"general.count": 9})


def test_service_backups_original_and_concurrency(panel, make_server):
    s = make_server(name="Cfg", stype="forge", loader="47.2.0", mc="1.20.1")
    d = panel.s.servers_dir / s["name"]
    (d / "config").mkdir(exist_ok=True)
    (d / "config" / "monmod-common.toml").write_text(TOML)
    (d / "config" / "evil.toml").symlink_to("/etc/passwd")
    files = [f["path"] for f in panel.modconfig.files(s["id"])]
    assert "config/monmod-common.toml" in files and "config/evil.toml" not in files
    g = panel.modconfig.get(s["id"], "config/monmod-common.toml")
    panel.modconfig.save(s["id"], "config/monmod-common.toml", {"general.maxPlayers": 20}, g["mtime"])
    g2 = panel.modconfig.get(s["id"], "config/monmod-common.toml")
    assert {e["key"]: e for e in g2["entries"]}["general.maxPlayers"]["original"] == "8"
    assert len([b for b in g2["backups"] if not b.get("original")]) == 1
    with pytest.raises(UserError, match="modifié depuis"):
        panel.modconfig.save(s["id"], "config/monmod-common.toml", {"general.maxPlayers": 30}, g["mtime"] - 100)
    panel.modconfig.restore(s["id"], "config/monmod-common.toml", "original")
    assert (d / "config" / "monmod-common.toml").read_text() == TOML
    for bad in ("../../etc/passwd", "server.properties", "config/evil.toml", "mods/x.jar"):
        with pytest.raises(UserError):
            panel.modconfig.get(s["id"], bad)
    panel.modconfig.regenerate(s["id"], "config/monmod-common.toml")
    assert not (d / "config" / "monmod-common.toml").exists()


def test_custom_ui_definitions(panel, make_server, auth):
    s = make_server(name="UI", stype="fabric", loader="0.16.0")
    d = panel.s.servers_dir / s["name"] / "config"
    (d / "x.json").write_text('{"enabled": true, "radius": 4}')
    ui = panel.s.state_dir / "config-ui.d"
    ui.mkdir(parents=True, exist_ok=True)
    (ui / "x.json").write_text(json.dumps({"title": "Mod X", "file": "config/x.json", "categories": [{"title": "Base", "fields": [{"key": "enabled", "label": "Activer"},
                                {"key": "radius", "label": "Rayon", "show_if": {"key": "enabled", "equals": "true"}}]}], "presets": [{"name": "Grand", "values": {"radius": 16}}]}))
    r = auth.get(f"/api/servers/{s['id']}/configs/file?path=config/x.json").json()
    assert r["ui"][0]["title"] == "Mod X" and r["format"] == "json"
    assert auth.put(f"/api/servers/{s['id']}/configs/file?path=config/x.json", json={"changes": {"radius": 16}, "mtime": r["mtime"]}).status_code == 200
    assert json.loads((d / "x.json").read_text())["radius"] == 16
