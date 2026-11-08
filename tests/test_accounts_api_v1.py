"""Comptes, permissions côté serveur, tokens, API /api/v1, événements."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from conftest import wait_for
from mcpanel.app import COOKIE, create_app
from test_api import PW, auth, client, create, wsh  # noqa: F401

UPW = "Utilisateur-Solide-77"


def login(client, username, password):
    c = TestClient(client.app, base_url="http://192.168.1.3:8080", client=("192.168.1.60", 7000))
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return c, r.json()


@pytest.fixture
def world(auth, panel):
    """Deux serveurs ; 'alice' (utilisateur) n'a accès qu'au premier."""
    s1 = create(auth, "Survie")
    s2 = create(auth, "Secret")
    for s in (s1, s2):
        auth.post(f"/api/servers/{s}/eula", json={"accept": True})
    r = auth.post("/api/users", json={"username": "alice", "password": UPW, "role": "user", "servers": [s1]})
    assert r.status_code == 200, r.text
    alice, _ = login(auth, "alice", UPW)
    return {"admin": auth, "alice": alice, "s1": s1, "s2": s2, "uid": r.json()["id"]}


# ------------------------------------------------------------------ permissions de l'utilisateur classique
def test_user_login_returns_role(world):
    assert world["alice"].get("/api/auth/me").json()["role"] == "user"


ALLOWED_FOR_USERS = {("GET", "/api/auth/me"), ("POST", "/api/auth/logout"), ("POST", "/api/auth/password"),
                     ("GET", "/api/me/servers"), ("POST", "/api/me/servers/{sid}/start"),
                     ("GET", "/api/tokens"), ("POST", "/api/tokens"), ("DELETE", "/api/tokens/{tid}"),
                     ("POST", "/api/auth/login"), ("GET", "/api/health"), ("GET", "/api/ping")}   # ping : mesure du temps de réponse, sans donnée


def test_user_forbidden_on_every_admin_route(world):
    """Parcourt TOUTES les routes /api/ réelles : hors liste blanche, un utilisateur classique reçoit 403 (jamais 2xx)."""
    import re
    a = world["alice"]
    checked = 0
    for r in a.app.routes:
        path = getattr(r, "path", "")
        if not path.startswith("/api/") or path.startswith("/api/v1"):
            continue
        for m in getattr(r, "methods", set()) - {"HEAD", "OPTIONS"}:
            if (m, path) in ALLOWED_FOR_USERS:
                continue
            url = re.sub(r"\{[^}]+\}", lambda x: world["s1"] if x.group(0) in ("{sid}", "{server_id}") else "x", path)
            kw = {"json": {}} if m in ("POST", "PUT", "PATCH", "DELETE") else {}
            resp = a.request(m, url, **kw)
            assert resp.status_code in (401, 403), (m, path, resp.status_code)
            checked += 1
    assert checked >= 40


def test_user_sees_only_assigned_servers_and_public_fields(world):
    a = world["alice"]
    r = a.get("/api/me/servers").json()
    assert [s["id"] for s in r["servers"]] == [world["s1"]]
    s = r["servers"][0]
    assert set(s) >= {"id", "name", "mc_version", "state", "players", "address", "port"}
    assert not ({"path", "ram_max_mb", "java", "jvm_args", "rcon_password"} & set(s))
    assert s["address"].endswith(f":{s['port']}") and s["address"].startswith("192.168.1.3")


def test_user_start_authorized_and_foreign_is_404(world):
    a = world["alice"]
    assert a.post(f"/api/me/servers/{world['s2']}/start").status_code == 404
    r = a.post(f"/api/me/servers/{world['s1']}/start")
    assert r.status_code == 200, r.text
    wait_for(lambda: world["admin"].get(f"/api/servers/{world['s1']}").json()["state"] == "running", 30, 0.3)
    r = a.post(f"/api/me/servers/{world['s1']}/start")
    assert r.status_code == 409 and r.json()["code"] == "already_running"
    world["admin"].post(f"/api/servers/{world['s1']}/stop")


def test_start_cooldown_for_users_not_admin(world):
    adm, a, s1 = world["admin"], world["alice"], world["s1"]
    adm.put("/api/settings", json={**{k: v for k, v in adm.get("/api/settings").json().items() if k in ("start_cooldown_s", "max_starts_per_hour")}, "start_cooldown_s": 120, "max_starts_per_hour": 6})
    assert a.post(f"/api/me/servers/{s1}/start").status_code == 200
    wait_for(lambda: adm.get(f"/api/servers/{s1}").json()["state"] == "running", 30, 0.3)
    adm.post(f"/api/servers/{s1}/stop")
    wait_for(lambda: adm.get(f"/api/servers/{s1}").json()["state"] == "stopped", 30, 0.3)
    r = a.post(f"/api/me/servers/{s1}/start")
    assert r.status_code == 429 and r.headers.get("retry-after")
    # l'administrateur n'est pas soumis à la limite
    assert adm.post(f"/api/me/servers/{s1}/start").status_code == 200
    adm.post(f"/api/servers/{s1}/stop")


def test_user_websocket_filtered_and_console_closed(world):
    a = world["alice"]
    with a.websocket_connect("/ws/stats", headers=wsh(a)) as ws:
        snap = json.loads(ws.receive_text())
        assert [s["id"] for s in snap["servers"]] == [world["s1"]]
        assert "system" not in snap or "alerts" not in snap
    with pytest.raises(Exception):
        with a.websocket_connect(f"/ws/servers/{world['s1']}/console", headers=wsh(a)) as ws:
            ws.receive_text()


def test_user_cannot_make_system_token_or_escalate(world):
    a = world["alice"]
    r = a.post("/api/tokens", json={"name": "x", "scopes": ["system:read"]})
    assert r.status_code in (400, 403)
    r = a.post("/api/tokens", json={"name": "x", "scopes": ["servers:read"], "user_id": 1})
    assert r.status_code == 200
    mine = a.get("/api/tokens?all=1").json()["tokens"]
    assert mine and all(t.get("user_id", t.get("username")) in (world["uid"], "alice") for t in mine)


# ------------------------------------------------------------------ gestion des comptes
def test_admin_account_rules(world):
    adm = world["admin"]
    me = adm.get("/api/auth/me").json()
    users = adm.get("/api/users").json()["users"]
    admin_id = next(u["id"] for u in users if u["username"] == "admin")
    assert adm.delete(f"/api/users/{admin_id}").status_code in (400, 409)            # ni soi-même, ni le dernier admin
    assert adm.patch(f"/api/users/{admin_id}", json={"role": "user"}).status_code in (400, 409)
    assert adm.patch(f"/api/users/{admin_id}", json={"active": False}).status_code in (400, 409)
    assert adm.post("/api/users", json={"username": "x", "password": "court", "role": "user"}).status_code == 400
    assert adm.post("/api/users", json={"username": "alice", "password": UPW, "role": "user"}).status_code >= 400   # doublon
    assert adm.post("/api/users", json={"username": "bob", "password": UPW, "role": "root"}).status_code == 400
    assert me["role"] == "admin"


def test_disable_revokes_session_and_tokens(world):
    adm, a = world["admin"], world["alice"]
    tok = a.post("/api/tokens", json={"name": "bot", "scopes": ["servers:read"]}).json()["secret"]
    H = {"Authorization": f"Bearer {tok}"}
    assert adm.get("/api/v1/servers", headers=H).status_code == 200
    assert adm.patch(f"/api/users/{world['uid']}", json={"active": False}).status_code == 200
    assert a.get("/api/auth/me").status_code == 401
    assert adm.get("/api/v1/servers", headers=H).status_code == 401
    assert TestClient(adm.app, base_url="http://192.168.1.3:8080", client=("192.168.1.60", 1)).post(
        "/api/auth/login", json={"username": "alice", "password": UPW}).status_code == 403


def test_role_change_and_server_reassignment_take_effect_immediately(world):
    adm, a = world["admin"], world["alice"]
    assert adm.patch(f"/api/users/{world['uid']}", json={"servers": [world["s1"], world["s2"]]}).status_code == 200
    assert len(a.get("/api/me/servers").json()["servers"]) == 2
    assert adm.patch(f"/api/users/{world['uid']}", json={"servers": []}).status_code == 200
    assert a.get("/api/me/servers").json()["servers"] == []


def test_delete_user_keeps_servers(world):
    adm = world["admin"]
    assert adm.delete(f"/api/users/{world['uid']}").status_code == 200
    assert adm.get(f"/api/servers/{world['s1']}").status_code == 200
    assert any(e["action"] in ("user_delete", "user_create") for e in adm.get("/api/audit?limit=50").json()["entries"])


# ------------------------------------------------------------------ API v1
def mk_token(c, scopes, **kw):
    r = c.post("/api/tokens", json={"name": "t", "scopes": scopes, **kw})
    assert r.status_code == 200, r.text
    return r.json()["secret"], r.json()["token"]


def bearer(t):
    return {"Authorization": f"Bearer {t}"}


def test_v1_requires_auth_and_envelope(world, client):
    anon = TestClient(client.app, base_url="http://192.168.1.3:8080", client=("192.168.1.70", 1))
    assert anon.get("/api/v1/ping").json() == {"ok": True, "data": {"status": "ok"}, "meta": {"api_version": "v1"}}
    r = anon.get("/api/v1/servers")
    assert r.status_code == 401 and r.json()["ok"] is False and r.json()["error"]["code"] == "unauthenticated"
    r = anon.get("/api/v1/servers", headers=bearer("mcp_faux"))
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_token"
    assert anon.get("/api/v1/nope").json()["error"]["code"] == "not_found"


def test_v1_scopes_and_user_filter(world):
    adm = world["admin"]
    t, _ = mk_token(world["alice"], ["servers:read", "players:read"])
    c = TestClient(adm.app, base_url="http://192.168.1.3:8080", client=("192.168.1.70", 2))
    r = c.get("/api/v1/servers", headers=bearer(t)).json()
    assert [s["id"] for s in r["data"]] == [world["s1"]] and r["meta"]["count"] == 1
    assert c.get(f"/api/v1/servers/{world['s2']}", headers=bearer(t)).status_code == 404
    assert c.get(f"/api/v1/servers/{world['s1']}/players", headers=bearer(t)).status_code == 200
    r = c.post(f"/api/v1/servers/{world['s1']}/start", headers=bearer(t))
    assert r.status_code == 403 and r.json()["error"]["code"] == "insufficient_scope"
    assert c.get("/api/v1/system", headers=bearer(t)).status_code == 403
    assert c.get("/api/v1/events", headers=bearer(t)).status_code == 403
    # token admin sans portée système : refusé aussi
    ta, _ = mk_token(adm, ["servers:read"])
    assert c.get("/api/v1/system", headers=bearer(ta)).status_code == 403
    tsys, _ = mk_token(adm, ["system:read"])
    r = c.get("/api/v1/system", headers=bearer(tsys))
    assert r.status_code == 200 and "cpu" in json.dumps(r.json()["data"]).lower()
    # un utilisateur ne peut pas obtenir system:read même via un token
    assert c.get("/api/v1/me", headers=bearer(t)).json()["data"]["role"] == "user"


def test_v1_start_players_and_events(world):
    adm, s1 = world["admin"], world["s1"]
    t, _ = mk_token(world["alice"], ["servers:read", "players:read", "servers:start", "events:read"])
    c = TestClient(adm.app, base_url="http://192.168.1.3:8080", client=("192.168.1.70", 3))
    r = c.post(f"/api/v1/servers/{s1}/start", headers=bearer(t))
    assert r.status_code == 202 and r.json()["data"]["state"] == "starting"
    wait_for(lambda: c.get(f"/api/v1/servers/{s1}", headers=bearer(t)).json()["data"]["state"] == "running", 30, 0.3)
    p = c.get(f"/api/v1/servers/{s1}/players", headers=bearer(t)).json()["data"]
    assert p["online"] == 2 and "Alex" in p["names"]
    assert c.get(f"/api/v1/servers/{s1}/status", headers=bearer(t)).json()["data"]["state"] == "running"
    assert c.get("/api/v1/status", headers=bearer(t)).json()["data"]["running"] == 1
    assert c.post(f"/api/v1/servers/{s1}/start", headers=bearer(t)).json()["error"]["code"] == "already_running"
    wait_for(lambda: any(e["type"] == "server.started" for e in c.get("/api/v1/events", headers=bearer(t)).json()["data"]), 15, 0.5)
    adm.post(f"/api/servers/{s1}/stop")
    wait_for(lambda: any(e["type"] == "server.stopped" for e in c.get("/api/v1/events", headers=bearer(t)).json()["data"]), 30, 0.5)
    last = c.get("/api/v1/events?since=0", headers=bearer(t)).json()["meta"]["last_id"]
    assert c.get(f"/api/v1/events?since={last}", headers=bearer(t)).json()["data"] == []


def test_v1_token_revocation_expiry_and_rate_limit(world):
    adm = world["admin"]
    c = TestClient(adm.app, base_url="http://192.168.1.3:8080", client=("192.168.1.70", 4))
    t, meta = mk_token(adm, ["servers:read"])
    assert c.get("/api/v1/servers", headers=bearer(t)).status_code == 200
    assert adm.delete(f"/api/tokens/{meta['id']}").status_code == 200
    assert c.get("/api/v1/servers", headers=bearer(t)).status_code == 401


def test_v1_expiry_and_rate_limit(world, panel):
    adm = world["admin"]
    c = TestClient(adm.app, base_url="http://192.168.1.3:8080", client=("192.168.1.70", 5))
    t, meta = mk_token(adm, ["servers:read"], expires_days=1)
    panel.db.execute("UPDATE api_tokens SET expires_at=? WHERE id=?", (time.time() - 5, meta["id"]))
    assert c.get("/api/v1/servers", headers=bearer(t)).status_code == 401
    t, _ = mk_token(adm, ["servers:read"])
    codes = [c.get("/api/v1/servers", headers=bearer(t)).status_code for _ in range(125)]
    assert 429 in codes and codes[0] == 200
    r = c.get("/api/v1/servers", headers=bearer(t))
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1 and r.json()["error"]["code"] == "rate_limited"


def test_v1_token_cannot_create_tokens_and_secret_hashed(world, panel):
    adm = world["admin"]
    t, meta = mk_token(adm, ["servers:read"])
    c = TestClient(adm.app, base_url="http://192.168.1.3:8080", client=("192.168.1.70", 6))
    assert c.post("/api/tokens", headers=bearer(t), json={"name": "n", "scopes": ["servers:read"]}).status_code == 401
    assert c.get("/api/servers", headers=bearer(t)).status_code == 401           # l'ancienne API n'accepte pas les tokens
    row = panel.db.one("SELECT * FROM api_tokens WHERE id=?", (meta["id"],))
    assert t not in json.dumps(dict(row), default=str)
    assert t not in json.dumps(adm.get("/api/tokens").json())


def test_v1_failed_auth_audited(world, panel):
    c = TestClient(world["admin"].app, base_url="http://192.168.1.3:8080", client=("192.168.1.70", 7))
    c.get("/api/v1/servers", headers=bearer("mcp_xxxxxxxx"))
    assert any(e["action"] == "api_auth_failed" for e in world["admin"].get("/api/audit?limit=20").json()["entries"])


def test_v1_openapi_documented(world):
    c = TestClient(world["admin"].app, base_url="http://192.168.1.3:8080", client=("192.168.1.70", 8))
    spec = c.get("/api/v1/openapi.json").json()
    assert spec["openapi"].startswith("3") and "/servers" in spec["paths"] and "/servers/{server_id}/start" in spec["paths"]


def test_v1_websocket_token(world):
    adm, s1 = world["admin"], world["s1"]
    t, _ = mk_token(world["alice"], ["servers:read", "events:read"])
    c = TestClient(adm.app, base_url="http://192.168.1.3:8080", client=("192.168.1.70", 9))
    with c.websocket_connect("/api/v1/ws", headers=bearer(t)) as ws:
        assert json.loads(ws.receive_text())["type"] == "ready"
        m = json.loads(ws.receive_text())
        assert m["type"] == "servers" and [s["id"] for s in m["data"]] == [s1]
    with c.websocket_connect("/api/v1/ws") as ws:
        ws.send_text(json.dumps({"type": "auth", "token": "mcp_mauvais"}))
        assert json.loads(ws.receive_text())["type"] == "error"


# ------------------------------------------------------------------ événements / webhooks
def test_event_transitions_and_webhook_discord(world, panel, routes):
    sent = []
    panel.http.routes["https://discord.com/api/webhooks/123/SECRETJETON99"] = lambda url, data: (sent.append(json.loads(data)) or b"")
    adm = world["admin"]
    w = adm.post("/api/webhooks", json={"name": "Discord", "url": "https://discord.com/api/webhooks/123/SECRETJETON99"})
    assert w.status_code == 200, w.text
    assert "SECRETJETON99" not in json.dumps(w.json())
    assert adm.post("/api/webhooks", json={"name": "x", "url": "http://discord.com/api/webhooks/1/2"}).status_code == 400
    assert adm.post("/api/webhooks", json={"name": "x", "url": "https://127.0.0.1/hook"}).status_code == 400
    adm.post(f"/api/servers/{world['s1']}/start")
    wait_for(lambda: adm.get(f"/api/servers/{world['s1']}").json()["state"] == "running", 30, 0.3)
    adm.post(f"/api/servers/{world['s1']}/stop")
    wait_for(lambda: any(e["type"] == "server.stopped" for e in adm.get("/api/events").json()["events"]), 30, 0.5)
    types = [e["type"] for e in adm.get("/api/events").json()["events"]]
    assert "server.started" in types and "server.stopped" in types
    wait_for(lambda: len(sent) >= 2, 10, 0.3)
    assert "embeds" in sent[0]


def test_v1_websocket_cut_when_token_revoked(world):
    adm = world["admin"]
    t, meta = mk_token(world["alice"], ["servers:read"])
    c = TestClient(adm.app, base_url="http://192.168.1.3:8080", client=("192.168.1.70", 11))
    with c.websocket_connect("/api/v1/ws") as ws:                       # authentification par premier message
        ws.send_text(json.dumps({"type": "auth", "token": t}))
        assert json.loads(ws.receive_text())["type"] == "ready"
        assert world["alice"].delete(f"/api/tokens/{meta['id']}").status_code == 200
        end, got_error = time.time() + 15, False
        with pytest.raises(Exception):
            while time.time() < end:
                m = json.loads(ws.receive_text())
                got_error = got_error or m.get("type") == "error"
        assert got_error


def test_v1_session_writes_need_csrf_and_users_cannot_reach_system(world):
    a, s1 = world["alice"], world["s1"]
    csrf = a.headers.pop("X-CSRF-Token")
    r = a.post(f"/api/v1/servers/{s1}/start")
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf"
    a.headers["X-CSRF-Token"] = csrf
    assert a.get("/api/v1/system").status_code == 403                    # session d'un utilisateur : jamais les stats système
    assert a.get("/api/v1/servers").status_code == 200
