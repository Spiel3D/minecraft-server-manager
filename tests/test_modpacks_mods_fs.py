import io
import json
import os
import stat
import zipfile
from pathlib import Path

import pytest

from conftest import sha
from mcpanel import modpacks
from mcpanel.mods import mc_in_range, parse_jar, scan_mods

SERVER = {"server_type": "fabric", "mc_version": "1.21.1"}


def mkzip(path: Path, files: dict, symlink: dict | None = None) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for n, d in files.items():
            z.writestr(n, d)
        for n, target in (symlink or {}).items():
            zi = zipfile.ZipInfo(n)
            zi.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(zi, target)
    return path


def fabric_jar(path: Path, mid="sodium", mc="~1.21"):
    mkzip(path, {"fabric.mod.json": json.dumps({"schemaVersion": 1, "id": mid, "version": "1.0", "name": mid, "depends": {"minecraft": mc}})})
    return path


def test_mc_range():
    assert mc_in_range("1.21.1", "~1.21") is True
    assert mc_in_range("1.20.1", "~1.21") is False
    assert mc_in_range("1.20.1", "[1.20,1.21)") is True
    assert mc_in_range("1.21", "[1.20,1.21)") is False
    assert mc_in_range("1.21.1", ">=1.20 <1.22") is True
    assert mc_in_range("1.21.1", "1.21.x") is True
    assert mc_in_range("1.21.1", None) is None


def test_scan_mods_reports_issues(tmp_path):
    mods = tmp_path / "mods"
    mods.mkdir()
    fabric_jar(mods / "ok.jar")
    fabric_jar(mods / "dup.jar", "sodium")
    fabric_jar(mods / "old.jar", "oldmod", "[1.12,1.13)")
    mkzip(mods / "forgey.jar", {"META-INF/mods.toml": 'modLoader="javafml"\n[[mods]]\nmodId="x"\nversion="1"\n'})
    (mods / "broken.jar").write_bytes(b"pas un zip")
    fabric_jar(mods / "off.jar.disabled", "offmod")
    r = scan_mods(tmp_path, "fabric", "1.21.1")
    by = {m["file"]: m for m in r["mods"]}
    assert r["count"] == 6 and by["off.jar.disabled"]["disabled"]
    assert any("hors de la plage" in i["text"] for i in by["old.jar"]["issues"])
    assert any("incompatible" in i["text"] for i in by["forgey.jar"]["issues"])
    assert by["broken.jar"]["issues"]
    assert "sodium" in r["duplicates"]


def test_parse_forge_toml(tmp_path):
    p = mkzip(tmp_path / "f.jar", {"META-INF/mods.toml": 'modLoader="javafml"\n[[mods]]\nmodId="jei"\nversion="2.0"\ndisplayName="JEI"\n'
                                    '[[dependencies.jei]]\nmodId="minecraft"\nversionRange="[1.20,1.21)"\n'})
    m = parse_jar(p)
    assert (m["loader"], m["id"], m["mc_range"]) == ("forge", "jei", "[1.20,1.21)")


def test_analyze_serverpack_and_filters(tmp_path):
    z = mkzip(tmp_path / "p.zip", {"pack/mods/a.jar": b"x", "pack/config/a.toml": "k=1", "pack/start.sh": "rm -rf /",
                                   "pack/server.properties": "port=1", "pack/world/level.dat": b"w", "pack/readme.txt": "hi"})
    server_dir = tmp_path / "srv"
    (server_dir / "mods").mkdir(parents=True)
    (server_dir / "mods" / "a.jar").write_bytes(b"old")
    a = modpacks.analyze_zip(z, SERVER, server_dir)
    assert a["kind"] == "serverpack" and a["root_prefix"] == "pack/" and not a["errors"]
    dests = {e["dest"]: e for e in a["entries"]}
    assert set(dests) == {"mods/a.jar", "config/a.toml"}
    assert dests["mods/a.jar"]["conflict"] and not dests["config/a.toml"]["conflict"]
    skipped = {s["name"] for s in a["skipped"]}
    assert {"start.sh", "server.properties", "world/level.dat", "readme.txt"} <= skipped


@pytest.mark.parametrize("name", ["../evil.jar", "/abs/x.jar", "mods/../../x", "a\\b", "C:/x"])
def test_analyze_rejects_dangerous_names(tmp_path, name):
    z = mkzip(tmp_path / "p.zip", {"mods/ok.jar": b"x", name: b"x"})
    a = modpacks.analyze_zip(z, SERVER, tmp_path / "srv")
    assert a["errors"], name


def test_analyze_rejects_symlink_corrupt_bomb(tmp_path):
    a = modpacks.analyze_zip(mkzip(tmp_path / "s.zip", {"mods/a.jar": b"x"}, {"mods/lien": "/etc/passwd"}), SERVER, tmp_path)
    assert a["errors"] and "lien" in a["errors"][0]
    (tmp_path / "c.zip").write_bytes(b"PK\x03\x04 pas un vrai zip")
    assert modpacks.analyze_zip(tmp_path / "c.zip", SERVER, tmp_path)["errors"]
    z = mkzip(tmp_path / "b.zip", {"mods/zero.bin": b"\0" * (20 * 1024 * 1024)})
    a = modpacks.analyze_zip(z, SERVER, tmp_path, bomb_min_bytes=1024 * 1024)
    assert a["errors"] and "compression" in a["errors"][0]
    a = modpacks.analyze_zip(tmp_path / "b.zip", SERVER, tmp_path, max_total=1024)
    assert a["errors"]


def test_analyze_empty_unknown(tmp_path):
    a = modpacks.analyze_zip(mkzip(tmp_path / "e.zip", {"readme.txt": "x"}), SERVER, tmp_path)
    assert a["errors"]


def test_modrinth_pack_mismatch_and_apply(tmp_path, panel):
    jar = b"JARDATA"
    index = {"name": "Mon pack", "dependencies": {"minecraft": "1.20.1", "fabric-loader": "0.15.0"},
             "files": [{"path": "mods/lib.jar", "downloads": ["https://cdn.modrinth.com/data/x/lib.jar"], "hashes": {"sha1": sha(jar)}, "fileSize": len(jar)},
                       {"path": "mods/client.jar", "env": {"server": "unsupported"}, "downloads": ["https://cdn.modrinth.com/c.jar"], "hashes": {}},
                       {"path": "mods/bad.jar", "downloads": ["https://cdn.modrinth.com/data/x/bad.jar"], "hashes": {"sha1": "0" * 40}, "fileSize": 3}]}
    z = mkzip(tmp_path / "p.mrpack", {"modrinth.index.json": json.dumps(index), "overrides/config/x.toml": "a=1", "overrides/run.bat": "x"})
    panel.http.routes["https://cdn.modrinth.com/data/x/lib.jar"] = jar
    panel.http.routes["https://cdn.modrinth.com/data/x/bad.jar"] = b"abc"
    d = tmp_path / "srv"
    d.mkdir()
    a = modpacks.analyze_zip(z, SERVER, d)
    assert a["kind"] == "modrinth" and a["mismatch"] and not a["errors"]
    assert any("client.jar" in s["name"] for s in a["skipped"]) and any("run.bat" in s["name"] for s in a["skipped"])
    out = modpacks.apply_plan(z, a, d, http=panel.http)
    assert (d / "mods/lib.jar").read_bytes() == jar and (d / "config/x.toml").read_text() == "a=1"
    assert out["downloaded"] == 1 and len(out["failed"]) == 1 and "bad.jar" in out["failed"][0]["name"]
    assert not (d / "mods/bad.jar").exists() and not list(d.rglob("*.part"))     # mod corrompu : refusé et nettoyé


def test_curseforge_without_key_is_manual(tmp_path, panel):
    man = {"manifestType": "minecraftModpack", "name": "CF", "minecraft": {"version": "1.21.1", "modLoaders": [{"id": "fabric-0.16.0", "primary": True}]},
           "files": [{"projectID": 1, "fileID": 2, "required": True}], "overrides": "overrides"}
    z = mkzip(tmp_path / "cf.zip", {"manifest.json": json.dumps(man), "overrides/config/c.toml": "z"})
    d = tmp_path / "srv"
    d.mkdir()
    a = modpacks.analyze_zip(z, SERVER, d)
    assert a["kind"] == "curseforge" and not a["mismatch"] and len(a["cf_files"]) == 1
    out = modpacks.apply_plan(z, a, d, http=panel.http, cf_key=None)
    assert out["manual"] and (d / "config/c.toml").exists()


def test_curseforge_download_url_null_never_bypassed(tmp_path, panel):
    man = {"manifestType": "minecraftModpack", "minecraft": {"version": "1.21.1", "modLoaders": []},
           "files": [{"projectID": 1, "fileID": 2}, {"projectID": 1, "fileID": 3}], "overrides": "overrides"}
    z = mkzip(tmp_path / "cf.zip", {"manifest.json": json.dumps(man)})
    jar = b"CFJAR"
    panel.http.routes["https://api.curseforge.com/v1/mods/files"] = lambda url, data: {"data": [
        {"fileName": "ok.jar", "downloadUrl": "https://edge.forgecdn.net/files/ok.jar", "hashes": [{"algo": 1, "value": sha(jar)}]},
        {"fileName": "locked.jar", "downloadUrl": None, "hashes": []}]}
    panel.http.routes["https://edge.forgecdn.net/files/ok.jar"] = jar
    d = tmp_path / "srv"
    d.mkdir()
    a = modpacks.analyze_zip(z, {"server_type": "fabric", "mc_version": "1.21.1"}, d)
    out = modpacks.apply_plan(z, a, d, http=panel.http, cf_key="K")
    assert (d / "mods/ok.jar").exists() and not (d / "mods/locked.jar").exists()
    assert [m["name"] for m in out["manual"]] == ["locked.jar"]


def test_apply_policy_skip_keeps_existing(tmp_path):
    z = mkzip(tmp_path / "p.zip", {"mods/a.jar": b"new", "config/c.toml": "n"})
    d = tmp_path / "srv"
    (d / "mods").mkdir(parents=True)
    (d / "mods/a.jar").write_bytes(b"old")
    a = modpacks.analyze_zip(z, SERVER, d)
    out = modpacks.apply_plan(z, a, d, policy="skip")
    assert (d / "mods/a.jar").read_bytes() == b"old" and out["kept"] == 1
    out = modpacks.apply_plan(z, a, d, policy="replace")
    assert (d / "mods/a.jar").read_bytes() == b"new"


def test_apply_refuses_size_lie(tmp_path):
    z = mkzip(tmp_path / "p.zip", {"mods/a.jar": b"x" * 5000})
    d = tmp_path / "srv"
    d.mkdir()
    a = modpacks.analyze_zip(z, SERVER, d)
    a["entries"][0]["size"] = 10          # le manifeste ment sur la taille
    with pytest.raises(modpacks.ModpackError):
        modpacks.apply_plan(z, a, d)
    assert not (d / "mods/a.jar").exists() and not list(d.rglob("*.part"))
