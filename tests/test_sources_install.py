import json
from pathlib import Path

import pytest

from conftest import wait_for
from mcpanel.jobs import UserError
from mcpanel.sources import SourceError, choose_java_major


def test_choose_java_major():
    assert choose_java_major(8) == 8
    assert choose_java_major(6) == 8
    assert choose_java_major(16) == 17
    assert choose_java_major(17) == 17
    assert choose_java_major(18) == 21
    assert choose_java_major(22) == 25


def test_supported_and_resolve(panel):
    s = panel.sources
    assert {v["id"] for v in s.supported_mc("vanilla")} == {"1.21.1", "1.20.1", "1.12.2"}
    assert "24w01a" in {v["id"] for v in s.supported_mc("vanilla", True)}
    assert {v["id"] for v in s.supported_mc("forge")} == {"1.20.1", "1.12.2"}
    assert {v["id"] for v in s.supported_mc("neoforge")} == {"1.21.1", "1.20.1"}
    assert {v["id"] for v in s.supported_mc("fabric")} == {"1.21.1", "1.20.1"}
    with pytest.raises(SourceError):
        s.resolve("forge", "1.21.1", "47.2.0")          # Forge n'existe pas pour 1.21.1 ici
    with pytest.raises(SourceError):
        s.resolve("fabric", "1.21.1", "9.9.9")          # chargeur inconnu
    with pytest.raises(SourceError):
        s.resolve("fabric", "1.21.1", None)             # chargeur obligatoire
    with pytest.raises(SourceError):
        s.resolve("vanilla", "9.9.9", None)
    assert s.resolve("forge", "1.20.1", "47.2.0")["java_major"] == 17
    rec = [v for v in s.loader_versions("forge", "1.20.1") if v["recommended"]]
    assert rec and rec[0]["version"] == "47.2.0"


@pytest.mark.parametrize("stype,mc,loader,java", [
    ("vanilla", "1.21.1", None, 21), ("vanilla", "1.12.2", None, 8), ("forge", "1.20.1", "47.2.0", 17),
    ("neoforge", "1.21.1", "21.1.60", 21), ("fabric", "1.20.1", "0.16.0", 17), ("quilt", "1.21.1", "0.16.0", 21)])
def test_install_each_type(panel, make_server, stype, mc, loader, java):
    srv = make_server(name=f"S-{stype}", stype=stype, mc=mc, loader=loader)
    assert srv["status"] == "installed" and srv["install_ok"] == 1
    assert srv["java_major"] == java
    d = Path(srv["path"])
    assert "eula=true" in (d / "eula.txt").read_text()
    assert f"server-port={srv['port']}" in (d / "server.properties").read_text()
    assert srv["launch_json"]["args"]


def test_install_refuses_invalid_combo_before_touching_disk(panel):
    with pytest.raises(SourceError):
        panel.create_server({"name": "Bad", "server_type": "forge", "mc_version": "1.21.1", "loader_version": "47.2.0",
                             "ram_max_mb": 512, "ram_min_mb": 256, "port": 25999})
    assert not (panel.s.servers_dir / "Bad").exists()
    assert panel.db.query("SELECT * FROM servers") == []


def test_bad_names_ports_ram_and_duplicates(panel, make_server):
    base = {"server_type": "vanilla", "mc_version": "1.21.1", "ram_max_mb": 512, "ram_min_mb": 256}
    from conftest import free_port
    for bad in ("", "   ", "x" * 80, "a\nb", "a\x00b"):
        with pytest.raises(Exception):
            panel.create_server({**base, "name": bad, "port": free_port()})
    with pytest.raises(UserError):
        panel.create_server({**base, "name": "A", "port": 80})
    with pytest.raises(UserError):
        panel.create_server({**base, "name": "A", "port": free_port(), "ram_max_mb": 10 ** 7})
    s = make_server(name="Un")
    with pytest.raises(UserError):
        panel.create_server({**base, "name": "un", "port": free_port()})        # nom déjà pris (insensible à la casse)
    with pytest.raises(UserError):
        panel.create_server({**base, "name": "Deux", "port": s["port"]})         # port déjà attribué


def test_existing_directory_never_overwritten(panel):
    (panel.s.servers_dir / "Prise").mkdir()
    (panel.s.servers_dir / "Prise" / "important.txt").write_text("a")
    srv, _ = panel.create_server({"name": "Prise", "server_type": "vanilla", "mc_version": "1.21.1", "ram_max_mb": 512,
                                  "ram_min_mb": 256, "port": 25888})
    assert srv["name"] != "Prise" and srv["name"].startswith("Prise")        # un autre dossier est choisi
    assert (panel.s.servers_dir / "Prise" / "important.txt").read_text() == "a"


def test_free_display_names_and_safe_folder(panel):
    from conftest import free_port
    base = {"server_type": "vanilla", "mc_version": "1.21.1", "ram_max_mb": 512, "ram_min_mb": 256}
    names = ["Survie d'été 🌞", "../../etc/passwd", "日本語 サーバー", "a/b", "  Ça marche  "]
    seen = set()
    for n in names:
        srv, _ = panel.create_server({**base, "name": n, "port": free_port()})
        assert srv["display_name"] == n.strip()
        slug = srv["name"]
        assert slug.isascii() and "/" not in slug and ".." not in slug and slug not in seen
        assert panel.s.servers_dir.resolve() in (panel.s.servers_dir / slug).resolve().parents
        seen.add(slug)
    ov = {o["name"]: o for o in panel.monitor.servers_overview()}
    assert "Survie d'été 🌞" in ov and ov["日本語 サーバー"]["slug"]
    with pytest.raises(UserError):
        panel.create_server({**base, "name": "SURVIE D'ÉTÉ 🌞", "port": free_port()})       # doublon (casse ignorée)


def test_rename_is_cosmetic(panel, make_server):
    s = make_server(name="Alpha")
    folder = s["path"]
    out = panel.update_server(s["id"], {"display_name": "Nouveau nom ✨"})
    assert out["display_name"] == "Nouveau nom ✨" and out["name"] == "Alpha" and out["path"] == folder
    assert (panel.s.servers_dir / "Alpha").exists()
    with pytest.raises(Exception, match="Nom invalide"):
        panel.update_server(s["id"], {"display_name": "\x07"})


def test_corrupted_download_detected(panel, routes):
    routes["https://piston-data.mojang.com/v1/objects/1.21.1/server.jar"] = b"TAMPERED"
    srv, jid = panel.create_server({"name": "Corr", "server_type": "vanilla", "mc_version": "1.21.1", "ram_max_mb": 512,
                                    "ram_min_mb": 256, "port": 25777})
    j = panel.jobs.wait(jid, 30)
    assert j["status"] == "error" and "contrôle" in j["error"]
    assert panel.manager.server(srv["id"])["status"] == "error"
    assert not (Path(srv["path"]) / "server.jar").exists()


def test_installer_failure_reported_and_reinstall(panel):
    from conftest import FAIL_FLAG
    FAIL_FLAG.write_text("x")
    srv, jid = panel.create_server({"name": "Echec", "server_type": "forge", "mc_version": "1.20.1", "loader_version": "47.2.0",
                                    "ram_max_mb": 512, "ram_min_mb": 256, "port": 25666})
    try:
        j = panel.jobs.wait(jid, 60)
    finally:
        FAIL_FLAG.unlink(missing_ok=True)
    assert j["status"] == "error" and "a échoué" in j["error"]
    j2 = panel.jobs.wait(panel.reinstall(srv["id"]), 60)
    assert j2["status"] == "done", j2["log"][-800:]
    assert panel.manager.server(srv["id"])["status"] == "installed"


def test_java_download_is_verified(panel, routes):
    key = "https://github.com/adoptium/temurin17-binaries/releases/download/jre-17.tar.gz"
    routes[key] = routes[key] + b"x"
    with pytest.raises(SourceError):
        panel.java.ensure(17)
    assert panel.java.find(17) is None


def test_non_https_refused(panel):
    with pytest.raises(SourceError):
        panel.http.get_bytes("http://example.com/x")


def test_adoptium_architecture_mapping_and_arm_url():
    from mcpanel.sources import Adoptium, SourceError, adoptium_arch
    assert adoptium_arch("x86_64") == "x64" and adoptium_arch("AMD64") == "x64"
    assert adoptium_arch("aarch64") == "aarch64" and adoptium_arch("arm64") == "aarch64"
    assert adoptium_arch("riscv64") is None and adoptium_arch("armv7l") is None
    seen = []

    class Rec:
        def get_json(self, url, **kw):
            seen.append(url)
            return [{"release_name": "jdk-21", "binary": {"package": {"link": "https://x/y.tar.gz", "checksum": "ab", "name": "j.tar.gz", "size": 1}}}]
    assert Adoptium(Rec(), machine="aarch64").asset(21)["name"] == "j.tar.gz"
    assert "architecture=aarch64" in seen[0]
    with pytest.raises(SourceError) as e:
        Adoptium(Rec(), machine="riscv64").asset(21)
    assert "openjdk-21-jre-headless" in str(e.value)               # message explicite avec la marche à suivre
    assert len(seen) == 1                                          # aucune requête pour une architecture non prise en charge
