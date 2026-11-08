"""V3 §3 : types de serveur universels (Paper, Purpur, imports vérifiés, extension déclarative)."""
import io
import json
import zipfile

import pytest

from mcpanel.adapters import detect_jar, load_adapters
from mcpanel.jobs import UserError
from mcpanel.sources import SourceError
from conftest import free_port


def make_jar(path, names=(), manifest="Main-Class: org.bukkit.craftbukkit.Main\n", version=None):
    with zipfile.ZipFile(path, "wb" if False else "w") as z:
        z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n" + manifest)
        for n in names:
            z.writestr(n, "x")
        if version:
            z.writestr("version.json", json.dumps({"id": version}))


def test_paper_versions_builds_and_install(panel, make_server):
    s = panel.sources
    assert {v["id"] for v in s.supported_mc("paper")} == {"1.21.1", "1.20.1"} & {v["id"] for v in s.supported_mc("vanilla")}
    builds = s.loader_versions("paper", "1.21.1")
    assert [b["version"] for b in builds] == ["12", "11", "10"]
    assert [b["version"] for b in builds if b["recommended"]] == ["11"]          # dernier build STABLE
    srv = make_server(name="Pap", stype="paper", loader="11")
    assert (panel.s.servers_dir / srv["name"] / "server.jar").read_bytes().startswith(b"FAKE-SERVER")
    assert srv["launch_json"]["args"] == ["-jar", "server.jar", "nogui"]
    assert (panel.s.servers_dir / srv["name"] / "plugins").is_dir() and not (panel.s.servers_dir / srv["name"] / "mods").exists()


def test_paper_corrupted_download_is_refused(panel, make_server):
    srv, jid = panel.create_server({"name": "Bad", "server_type": "paper", "mc_version": "1.21.1", "loader_version": "10",
                                    "ram_max_mb": 512, "ram_min_mb": 256, "port": free_port()})
    j = panel.jobs.wait(jid, 60)
    assert j["status"] == "failed" or j["status"] == "error"
    assert "Somme de contrôle" in (j["error"] + j["log"])
    assert panel.manager.server(srv["id"])["status"] == "error"
    assert not (panel.s.servers_dir / srv["name"] / "server.jar").exists()


def test_purpur_install_checks_md5(panel, make_server):
    assert panel.sources.loader_versions("purpur", "1.21.1")[0]["recommended"]
    srv = make_server(name="Pur", stype="purpur", loader="2300")
    assert (panel.s.servers_dir / srv["name"] / "server.jar").exists()


def test_unknown_loader_for_paper_rejected(panel):
    with pytest.raises(SourceError):
        panel.sources.resolve("paper", "1.21.1", "999")
    with pytest.raises(SourceError):
        panel.sources.resolve("paper", "1.12.2", "1")


def test_import_flow_hybrid(panel):
    srv, jid = panel.create_server({"name": "Hybride", "server_type": "mohist", "mc_version": "1.20.1",
                                    "ram_max_mb": 512, "ram_min_mb": 256, "port": free_port()})
    assert jid is None and srv["status"] == "awaiting_jar"
    d = panel.s.servers_dir / srv["name"]
    with pytest.raises(UserError, match="introuvable"):
        panel.inspect_import(srv["id"])                                   # rien n'a encore été déposé
    (d / "server.jar").write_bytes(b"pas un zip")
    info = panel.inspect_import(srv["id"])
    assert not info["valid"] and info["warnings"]
    with pytest.raises(UserError, match="pas un jar valide"):
        panel.finalize_import(srv["id"])
    make_jar(d / "server.jar", names=["com/mohistmc/Main.class", "org/bukkit/Bukkit.class"], version="1.20.1")
    info = panel.inspect_import(srv["id"])
    assert info["valid"] and info["guess"] == "mohist" and info["mc_version"] == "1.20.1" and not [w for w in info["warnings"] if "ressemble" in w]
    jid = panel.finalize_import(srv["id"])
    j = panel.jobs.wait(jid, 60)
    assert j["status"] == "done", j["log"] + j["error"]
    s2 = panel.manager.server(srv["id"])
    assert s2["status"] == "installed" and s2["launch_json"]["args"] == ["-jar", "server.jar", "nogui"]
    assert (d / "mods").is_dir() and (d / "plugins").is_dir()               # hybride : les deux
    panel.accept_eula(srv["id"])
    panel.manager.start(srv["id"])
    panel.manager.stop(srv["id"])


def test_import_rejects_path_tricks_and_mismatch_warns(panel):
    srv, _ = panel.create_server({"name": "Imp", "server_type": "spigot", "mc_version": "1.21.1",
                                  "ram_max_mb": 512, "ram_min_mb": 256, "port": free_port()})
    for bad in ("../x.jar", "a/b.jar", "x.txt", ".jar", "-r.jar"):
        with pytest.raises(UserError):
            panel.inspect_import(srv["id"], bad)
    d = panel.s.servers_dir / srv["name"]
    outside = d.parent / "dehors.jar"
    make_jar(outside)
    (d / "lien.jar").symlink_to(outside)
    with pytest.raises(UserError):
        panel.inspect_import(srv["id"], "lien.jar")                      # lien symbolique refusé
    make_jar(d / "x.jar", names=["io/papermc/paper/Main.class"], version="1.20.1")
    info = panel.inspect_import(srv["id"], "x.jar")
    assert info["guess"] == "paper"
    assert any("ressemble" in w for w in info["warnings"]) and any("Version Minecraft détectée" in w for w in info["warnings"])


def test_declarative_extension(tmp_path):
    d = tmp_path / "types"
    d.mkdir()
    (d / "a.json").write_text(json.dumps({"key": "monfork", "label": "Mon fork", "family": "plugins", "plugins": True}))
    (d / "b.json").write_text("{pas du json")
    (d / "c.json").write_text(json.dumps({"key": "paper", "label": "Piraté"}))     # ne remplace jamais un type automatique
    (d / "d.json").write_text(json.dumps({"key": "../mal", "label": "x"}))
    reg = load_adapters(d)
    assert reg["monfork"].mode == "import" and reg["monfork"].plugins
    assert reg["paper"].label == "Paper" and "../mal" not in reg
