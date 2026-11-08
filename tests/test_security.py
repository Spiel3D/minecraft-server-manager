import os
import zipfile
from pathlib import Path

import pytest

from mcpanel import fsops
from mcpanel.security import (LoginThrottle, PathError, SecurityError, check_password_strength, hash_password, ip_allowed,
                              parse_nets, safe_join, validate_server_name, verify_password)
from mcpanel.config import PRIVATE_NETS


def test_password_hash_and_strength():
    h = hash_password("Correct-Horse-Battery-9")
    assert verify_password("Correct-Horse-Battery-9", h) and not verify_password("wrong", h)
    assert "Correct" not in h
    assert not verify_password("x", "garbage")
    for bad in ("court", "aaaaaaaaaaaa", "password1234"):
        with pytest.raises(SecurityError):
            check_password_strength(bad, "admin")
    check_password_strength("une phrase de passe assez longue", "admin")


def test_ip_allowlist():
    nets = parse_nets(PRIVATE_NETS)
    for ok in ("192.168.1.50", "10.1.2.3", "172.16.0.9", "127.0.0.1", "::1"):
        assert ip_allowed(ok, nets), ok
    for bad in ("8.8.8.8", "172.32.0.1", "2001:4860:4860::8888", None, "pas-une-ip"):
        assert not ip_allowed(bad, nets), bad


def test_login_throttle():
    t = LoginThrottle(max_free=3, base=10, cap=60)
    for _ in range(3):
        assert t.locked_for("a") == 0
        t.fail("a")
    t.fail("a")
    assert t.locked_for("a") > 0
    assert t.locked_for("b") == 0
    t.ok("a")
    assert t.locked_for("a") == 0


def test_safe_join(tmp_path):
    root = tmp_path / "srv"
    root.mkdir()
    (tmp_path / "secret").write_text("s")
    os.symlink(tmp_path / "secret", root / "lien")
    os.symlink(tmp_path, root / "dirlien")
    assert safe_join(root, "a/b.txt") == Path(os.path.realpath(root)) / "a/b.txt"
    assert safe_join(root, "/etc/passwd") == Path(os.path.realpath(root)) / "etc/passwd"   # slash initial : reste confiné dans la racine
    for bad in ("../secret", "a/../../secret", "lien", "dirlien/x", "a\\b", "a\0b", "..", "x/../../.."):
        with pytest.raises(SecurityError):
            safe_join(root, bad)


def test_server_names():
    assert validate_server_name("Survie_1.21-b") == "Survie_1.21-b"
    for bad in ("", " ", "a b", "../a", "a/b", ".caché", "x" * 100, "é", "CON\n", "-rf"):
        with pytest.raises(SecurityError):
            validate_server_name(bad)


def test_fsops_confinement_and_delete(tmp_path):
    root = tmp_path / "srv"
    root.mkdir()
    (root / "a.txt").write_text("1")
    outside = tmp_path / "out"
    outside.mkdir()
    (outside / "keep.txt").write_text("k")
    os.symlink(outside, root / "evil")
    fsops.delete(root, "evil")              # supprime le lien lui-même
    assert (outside / "keep.txt").exists() and not (root / "evil").exists()
    with pytest.raises(SecurityError):
        fsops.delete(root, "")
    with pytest.raises(SecurityError):
        fsops.rename(root, "a.txt", "../a.txt")
    fsops.rename(root, "a.txt", "b.txt")
    fsops.copy(root, "b.txt", "c.txt")
    assert fsops.read_text(root, "c.txt")["content"] == "1"


def test_editor_conflict_and_binary(tmp_path):
    root = tmp_path
    (root / "f.txt").write_text("v1")
    m = fsops.read_text(root, "f.txt")["mtime"]
    fsops.write_text(root, "f.txt", "v2", m)
    os.utime(root / "f.txt", (m + 100, m + 100))
    with pytest.raises(SecurityError):
        fsops.write_text(root, "f.txt", "v3", m)                 # modifié entre-temps
    (root / "bin.dat").write_bytes(b"\x00\x01\x02" * 100)
    with pytest.raises(SecurityError):
        fsops.read_text(root, "bin.dat")
    assert not list(root.glob("*.panel-tmp"))
