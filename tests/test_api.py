import io
import json
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import free_port, wait_for
from mcpanel.app import COOKIE, create_app
from mcpanel.security import hash_password
from mcpanel.util import now

PW = "Un-Mot-De-Passe-Solide-42"


@pytest.fixture
def client(panel):
    panel.db.execute("INSERT INTO users(username,pw_hash,created_at) VALUES(?,?,?)", ("admin", hash_password(PW), now()))
    app = create_app(panel=panel)
    with TestClient(app, base_url="http://192.168.1.3:8080", client=("192.168.1.50", 5555)) as c:
        yield c


@pytest.fixture
def auth(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": PW})
    assert r.status_code == 200
    client.headers["X-CSRF-Token"] = r.json()["csrf"]
    return client


def wsh(c, **extra):
    """Le client de test n'envoie pas le cookie de session aux WebSockets : on le transmet à la main."""
    return {"cookie": f"{COOKIE}={c.cookies.get(COOKIE)}", **extra}


def job_done(c, jid, timeout=60):
    def f():
        j = c.get(f"/api/jobs/{jid}").json()
        return j if j["status"] in ("done", "error") else None
    return wait_for(f, timeout, 0.2)


def create(c, name="Api", stype="vanilla", mc="1.21.1", loader=None):
    r = c.post("/api/servers", json={"name": name, "server_type": stype, "mc_version": mc, "loader_version": loader,
                                     "ram_max_mb": 512, "ram_min_mb": 256, "port": free_port()})
    assert r.status_code == 200, r.text
    j = job_done(c, r.json()["job"])
    assert j["status"] == "done", j["log"]
    return r.json()["server"]["id"]


def test_ui_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "Minecraft Server Manager" in r.text
    for f in ("core.js", "views.js", "app.js", "style.css", "config.js"):
        assert client.get("/" + f).status_code == 200, f
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    assert "<script>" not in r.text and "onclick=" not in r.text      # aucun script en ligne (CSP stricte)


def test_requires_login(client):
    for path in ("/api/servers", "/api/system", "/api/settings", "/api/java"):
        assert client.get(path).status_code == 401, path


def test_login_logout_and_cookie_flags(client):
    bad = client.post("/api/auth/login", json={"username": "admin", "password": "non"})
    assert bad.status_code == 401
    r = client.post("/api/auth/login", json={"username": "admin", "password": PW})
    sc = r.headers["set-cookie"].lower()
    assert "httponly" in sc and "samesite=strict" in sc
    assert client.get("/api/auth/me").json()["username"] == "admin"
    client.headers["X-CSRF-Token"] = r.json()["csrf"]
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_csrf_required_for_writes(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": PW})
    assert client.post("/api/servers", json={}).status_code == 403                     # pas de jeton
    client.headers["X-CSRF-Token"] = "faux"
    assert client.post("/api/servers", json={}).status_code == 403
    client.headers["X-CSRF-Token"] = r.json()["csrf"]
    assert client.post("/api/servers", json={}).status_code == 400                     # jeton ok, données invalides


def test_login_throttling(client):
    codes = [client.post("/api/auth/login", json={"username": "admin", "password": "x"}).status_code for _ in range(8)]
    assert 429 in codes
    assert client.post("/api/auth/login", json={"username": "admin", "password": PW}).status_code == 429


def test_public_ip_refused(panel):
    app = create_app(panel=panel)
    with TestClient(app, client=("8.8.8.8", 1234)) as c:
        r = c.get("/")
        assert r.status_code == 403
        assert c.post("/api/auth/login", json={}).status_code == 403
    with TestClient(app, client=("203.0.113.9", 1234)) as c:
        with pytest.raises(Exception):
            with c.websocket_connect("/ws/stats"):
                pass


def test_password_change(auth):
    assert auth.post("/api/auth/password", json={"current": "faux", "new": "Nouveau-Mot-De-Passe-77"}).status_code == 400
    assert auth.post("/api/auth/password", json={"current": PW, "new": "court"}).status_code == 400
    assert auth.post("/api/auth/password", json={"current": PW, "new": "Nouveau-Mot-De-Passe-77"}).status_code == 200
    auth.cookies.clear()
    assert auth.post("/api/auth/login", json={"username": "admin", "password": PW}).status_code == 401
    assert auth.post("/api/auth/login", json={"username": "admin", "password": "Nouveau-Mot-De-Passe-77"}).status_code == 200


def test_meta_endpoints(auth):
    assert {v["id"] for v in auth.get("/api/meta/minecraft?type=fabric").json()["versions"]} == {"1.21.1", "1.20.1"}
    assert auth.get("/api/meta/loaders?type=forge&mc=1.20.1").json()["versions"][0]["version"]
    j = auth.get("/api/meta/java-need?mc=1.21.1").json()
    assert j["runtime"] == 21 and j["installed"] is False
    lim = auth.get("/api/meta/limits").json()
    assert lim["suggested_port"] >= 25565 and lim["ram"]["max_xmx_mb"] > 0
    assert auth.get("/api/meta/loaders?type=forge&mc=9.9").status_code == 400


def test_full_server_flow(auth, panel):
    sid = create(auth)
    s = auth.get(f"/api/servers/{sid}").json()
    assert s["state"] == "stopped" and s["java_major"] == 21
    assert auth.post(f"/api/servers/{sid}/start").status_code == 400                    # EULA non acceptée
    assert auth.post(f"/api/servers/{sid}/eula", json={"accept": False}).status_code == 400
    assert auth.post(f"/api/servers/{sid}/eula", json={"accept": True}).status_code == 200
    assert auth.post(f"/api/servers/{sid}/start").status_code == 200
    wait_for(lambda: auth.get(f"/api/servers/{sid}").json()["state"] == "running", 30, 0.3)
    assert auth.post(f"/api/servers/{sid}/start").status_code == 400                    # déjà lancé
    assert auth.post(f"/api/servers/{sid}/kill").status_code == 400                     # confirmation requise
    assert auth.request("DELETE", f"/api/servers/{sid}", json={"confirm_name": "Api"}).status_code == 400
    assert auth.post(f"/api/servers/{sid}/stop").status_code == 200
    wait_for(lambda: auth.get(f"/api/servers/{sid}").json()["state"] == "stopped", 30, 0.3)
    assert auth.request("DELETE", f"/api/servers/{sid}", json={"confirm_name": "faux"}).status_code == 400
    assert auth.request("DELETE", f"/api/servers/{sid}", json={"confirm_name": "Api"}).status_code == 200
    assert auth.get("/api/servers").json()["servers"] == []
    actions = [a["action"] for a in auth.get("/api/audit?limit=50").json()["entries"]]
    assert {"server_create", "eula_accepted", "server_start", "server_delete"} <= set(actions)


def test_patch_validation(auth):
    sid = create(auth)
    assert auth.patch(f"/api/servers/{sid}", json={"jvm_extra": "-XX:+UseG1GC"}).status_code == 200
    for bad in ("-Xmx1G; rm -rf /", "$(id)", "foo", "-a\nb"):
        assert auth.patch(f"/api/servers/{sid}", json={"jvm_extra": bad}).status_code == 400, bad
    assert auth.patch(f"/api/servers/{sid}", json={"port": 80}).status_code == 400
    assert auth.patch(f"/api/servers/{sid}", json={"ram_max_mb": 10 ** 7}).status_code == 400
    assert auth.patch(f"/api/servers/{sid}", json={"backup_prefs": {"enabled": True, "interval_hours": 6, "keep": 3, "mode": "stop"}}).status_code == 200


def test_files_api_and_traversal(auth, panel):
    sid = create(auth)
    root = Path(panel.manager.server(sid)["path"])
    (root.parent / "secret.txt").write_text("top")
    ls = auth.get(f"/api/servers/{sid}/files").json()
    assert "server.properties" in {e["name"] for e in ls["entries"]}
    for bad in ("../secret.txt", "..%2Fsecret.txt", "a/../../secret.txt"):
        assert auth.get(f"/api/servers/{sid}/files/read", params={"path": bad}).status_code == 400
        assert auth.get(f"/api/servers/{sid}/files/download", params={"path": bad}).status_code in (400, 404)
    r = auth.get(f"/api/servers/{sid}/files/read", params={"path": "server.properties"}).json()
    w = auth.put(f"/api/servers/{sid}/files/write", json={"path": "server.properties", "content": r["content"] + "\n# test\n", "mtime": r["mtime"]})
    assert w.status_code == 200
    assert auth.put(f"/api/servers/{sid}/files/write", json={"path": "server.properties", "content": "x", "mtime": r["mtime"] - 50}).status_code == 400
    up = auth.put(f"/api/servers/{sid}/files/upload", params={"path": "config/a.txt"}, content=b"hello")
    assert up.status_code == 200 or up.status_code == 400
    up = auth.put(f"/api/servers/{sid}/files/upload", params={"path": "a.txt"}, content=b"hello")
    assert up.status_code == 200 and (root / "a.txt").read_bytes() == b"hello"
    assert auth.put(f"/api/servers/{sid}/files/upload", params={"path": "a.txt"}, content=b"2").status_code == 400
    assert auth.put(f"/api/servers/{sid}/files/upload", params={"path": "a.txt", "overwrite": "true"}, content=b"2").status_code == 200
    assert auth.put(f"/api/servers/{sid}/files/upload", params={"path": "../x.txt"}, content=b"2").status_code == 400
    assert auth.get(f"/api/servers/{sid}/files/download", params={"path": "a.txt"}).content == b"2"
    assert auth.post(f"/api/servers/{sid}/files/mkdir", json={"path": "dossier"}).status_code == 200
    assert auth.post(f"/api/servers/{sid}/files/rename", json={"src": "a.txt", "dst": "dossier/b.txt"}).status_code == 200
    assert auth.delete(f"/api/servers/{sid}/files", params={"path": "dossier"}).status_code == 200
    assert auth.delete(f"/api/servers/{sid}/files", params={"path": ""}).status_code == 400
    assert (root.parent / "secret.txt").exists()
    assert not list(root.rglob("*.part"))


def test_upload_size_limit(auth, panel, settings):
    import dataclasses
    object.__setattr__(panel.s, "max_upload_mb", 1)
    sid = create(auth)
    r = auth.put(f"/api/servers/{sid}/files/upload", params={"path": "gros.bin"}, content=b"0" * (2 * 1024 * 1024))
    assert r.status_code == 413
    assert not (Path(panel.manager.server(sid)["path"]) / "gros.bin").exists()


def test_mods_api(auth, panel):
    sid = create(auth, "Fab", "fabric", "1.21.1", "0.16.0")
    root = Path(panel.manager.server(sid)["path"])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("fabric.mod.json", json.dumps({"id": "demo", "version": "1", "depends": {"minecraft": "~1.21"}}))
    assert auth.put(f"/api/servers/{sid}/files/upload", params={"path": "mods/demo.jar"}, content=buf.getvalue()).status_code == 200
    m = auth.get(f"/api/servers/{sid}/mods").json()["mods"]
    assert m[0]["file"] == "demo.jar" and m[0]["loader"] == "fabric" and not m[0]["issues"]
    assert auth.post(f"/api/servers/{sid}/mods/demo.jar/toggle").json()["file"] == "demo.jar.disabled"
    assert auth.post(f"/api/servers/{sid}/mods/demo.jar.disabled/toggle").json()["file"] == "demo.jar"
    assert auth.delete(f"/api/servers/{sid}/mods/demo.jar").status_code == 400            # confirmation requise
    for bad in ("..%2Fserver.properties", "x.txt"):
        assert auth.post(f"/api/servers/{sid}/mods/{bad}/toggle").status_code in (400, 404, 405)
    assert auth.delete(f"/api/servers/{sid}/mods/demo.jar", params={"confirm": "true"}).status_code == 200
    assert not (root / "mods/demo.jar").exists()


def test_modpack_import_flow(auth, panel):
    sid = create(auth, "Pack", "fabric", "1.21.1", "0.16.0")
    root = Path(panel.manager.server(sid)["path"])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mods/m.jar", b"zz")
        z.writestr("config/c.toml", "a=1")
        z.writestr("evil.sh", "x")
    up = auth.put(f"/api/servers/{sid}/modpack/upload", params={"filename": "pack.zip"}, content=buf.getvalue())
    assert up.status_code == 200, up.text
    uid, a = up.json()["upload_id"], up.json()["analysis"]
    assert a["kind"] == "serverpack" and len(a["entries"]) == 2
    r = auth.post(f"/api/servers/{sid}/modpack/{uid}/apply", json={"policy": "skip", "backup_first": True})
    j = job_done(auth, r.json()["job"])
    assert j["status"] == "done", j["log"]
    assert j["result"]["extracted"] == 2 and (root / "mods/m.jar").exists() and not (root / "evil.sh").exists()
    kinds = {b["kind"] for b in auth.get(f"/api/servers/{sid}/backups").json()["backups"]}
    assert "pre-import" in kinds                                  # sauvegarde automatique avant import
    assert auth.post(f"/api/servers/{sid}/modpack/{uid}/apply", json={}).status_code == 404   # upload consommé
    bad = auth.put(f"/api/servers/{sid}/modpack/upload", params={"filename": "x.zip"}, content=b"pas un zip")
    assert bad.status_code == 200 and bad.json()["analysis"]["errors"]
    r = auth.post(f"/api/servers/{sid}/modpack/{bad.json()['upload_id']}/apply", json={})
    assert r.status_code == 400


def test_modpack_mismatch_needs_explicit_confirmation(auth, panel):
    sid = create(auth, "Mis", "fabric", "1.21.1", "0.16.0")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("modrinth.index.json", json.dumps({"name": "n", "dependencies": {"minecraft": "1.12.2", "forge": "14.1"}, "files": []}))
        z.writestr("overrides/config/c.toml", "a=1")
    up = auth.put(f"/api/servers/{sid}/modpack/upload", params={"filename": "p.mrpack"}, content=buf.getvalue()).json()
    assert up["analysis"]["mismatch"]
    assert auth.post(f"/api/servers/{sid}/modpack/{up['upload_id']}/apply", json={}).status_code == 400
    ok = auth.post(f"/api/servers/{sid}/modpack/{up['upload_id']}/apply", json={"ignore_mismatch": True})
    assert job_done(auth, ok.json()["job"])["status"] == "done"


def test_backup_api(auth, panel):
    sid = create(auth)
    root = Path(panel.manager.server(sid)["path"])
    (root / "world").mkdir()
    (root / "world/level.dat").write_text("v1")
    j = job_done(auth, auth.post(f"/api/servers/{sid}/backups").json()["job"])
    assert j["status"] == "done", j["log"]
    b = auth.get(f"/api/servers/{sid}/backups").json()["backups"][0]
    assert b["exists"]
    assert auth.get(f"/api/servers/{sid}/backups/{b['id']}/download").content[:2] == b"PK"
    (root / "world/level.dat").write_text("v2")
    j = job_done(auth, auth.post(f"/api/servers/{sid}/backups/{b['id']}/restore").json()["job"])
    assert j["status"] == "done", j["log"]
    assert (root / "world/level.dat").read_text() == "v1"
    assert auth.delete(f"/api/servers/{sid}/backups/{b['id']}").status_code == 400
    assert auth.delete(f"/api/servers/{sid}/backups/{b['id']}", params={"confirm": "true"}).status_code == 200


def test_settings_and_secret_not_leaked(auth, panel):
    assert auth.put("/api/settings", json={"ram_reserve_mb": 100}).status_code == 400
    assert auth.put("/api/settings", json={"ram_reserve_mb": 1024, "curseforge_key": "SECRET-KEY-123"}).status_code == 200
    s = auth.get("/api/settings").json()
    assert s["ram_reserve_mb"] == 1024 and s["curseforge_key_set"] is True and "SECRET-KEY-123" not in json.dumps(s)
    assert "SECRET-KEY-123" not in json.dumps(auth.get("/api/audit?limit=100").json())


def test_java_install_job(auth):
    jid = auth.post("/api/java/17").json()["job"]
    assert job_done(auth, jid)["status"] == "done"
    assert 17 in [j["major"] for j in auth.get("/api/java").json()["installed"]]
    assert auth.post("/api/java/13").status_code == 400


def test_websockets(auth, panel):
    sid = create(auth)
    auth.post(f"/api/servers/{sid}/eula", json={"accept": True})
    assert auth.post(f"/api/servers/{sid}/start").status_code == 200
    wait_for(lambda: auth.get(f"/api/servers/{sid}").json()["state"] == "running", 30, 0.3)
    with auth.websocket_connect("/ws/stats", headers=wsh(auth)) as ws:
        snap = json.loads(ws.receive_text())
        assert "system" in snap and snap["servers"][0]["id"] == sid and "alerts" in snap
    with auth.websocket_connect(f"/ws/servers/{sid}/console", headers=wsh(auth)) as ws:
        hist = json.loads(ws.receive_text())
        assert hist["type"] == "history" and "Done" in hist["text"]
        ws.send_text(json.dumps({"type": "command", "text": "list"}))
        got = ""
        end = time.time() + 10
        while "command: list" not in got and time.time() < end:
            m = json.loads(ws.receive_text())
            got += m.get("text", "")
        assert "command: list" in got
    assert any(a["action"] == "console_command" for a in auth.get("/api/audit?limit=20").json()["entries"])
    auth.post(f"/api/servers/{sid}/stop")


def test_websocket_requires_session_and_same_origin(client, auth):
    c2 = TestClient(client.app, base_url="http://192.168.1.3:8080", client=("192.168.1.50", 1))
    with pytest.raises(Exception):
        with c2.websocket_connect("/ws/stats"):
            pass
    with pytest.raises(Exception):
        with auth.websocket_connect("/ws/stats", headers=wsh(auth, origin="http://evil.example")):
            pass
