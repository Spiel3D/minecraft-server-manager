"""Application FastAPI : API REST, WebSockets, interface statique, garde réseau et en-têtes de sécurité."""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, fsops, mods as modsmod, modpacks
from .core import Panel
from .errors import AppError
from .jobs import UserError
from .modpacks import ModpackError
from .process import ProcessError
from .security import (SecurityError, check_password_strength, hash_password, ip_allowed, safe_join,
                       verify_password)
from .sources import SourceError, choose_java_major
from .util import new_id, now

COOKIE = "mcpanel_session"
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self' ws: wss:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
SEC_HEADERS = [
    (b"content-security-policy", CSP.encode()), (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"), (b"referrer-policy", b"no-referrer"),
    (b"cache-control", b"no-store"), (b"cross-origin-opener-policy", b"same-origin"),
]


class Guard:
    """Middleware ASGI : refuse les adresses hors réseaux privés autorisés (HTTP et WebSocket) et ajoute les en-têtes de sécurité."""

    def __init__(self, app, panel: Panel):
        self.app, self.panel = app, panel

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        client = scope.get("client")
        ip = client[0] if client else None
        if not ip_allowed(ip, self.panel.nets):
            if scope["type"] == "http":
                body = b'{"detail":"Acc\xc3\xa8s refus\xc3\xa9 : adresse hors du r\xc3\xa9seau autoris\xc3\xa9."}'
                await send({"type": "http.response.start", "status": 403,
                            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
            else:
                await receive()
                await send({"type": "websocket.close", "code": 4403})
            return

        async def send2(msg):
            if msg["type"] == "http.response.start":
                msg = dict(msg)
                have = {k.lower() for k, _ in msg.get("headers", [])}
                msg["headers"] = list(msg.get("headers", [])) + [(k, v) for k, v in SEC_HEADERS if k not in have]
            await send(msg)

        await self.app(scope, receive, send2)


def create_app(settings=None, panel: Panel | None = None) -> FastAPI:
    if panel is None:
        from .config import Settings
        panel = Panel(settings or Settings.from_env())
    P = panel
    S = P.s
    uploads: dict[str, dict] = {}
    usage_cache = {"t": 0.0, "v": {}}
    bg_stop = threading.Event()

    # ------------------------------------------------------------------ tâches de fond
    async def loop_fast():
        n = 0
        while True:
            try:
                await asyncio.to_thread(P.manager.tick)
                await asyncio.to_thread(P.monitor.tick)
                await asyncio.to_thread(P.events.observe, P.monitor.snapshot.get("servers", []))
                await asyncio.to_thread(P.perf.tick)
                n += 1
                if n % 30 == 0:
                    await asyncio.to_thread(schedule_backups)
                if n % 1800 == 0:
                    await asyncio.to_thread(P.cleanup_temp)
            except Exception:  # noqa: BLE001 — la boucle ne doit jamais mourir
                pass
            await asyncio.sleep(2)

    def schedule_backups():
        if not P.data_ready():
            return
        for srv in P.backups.due():
            busy = P.db.one("SELECT 1 FROM jobs WHERE server_id=? AND kind IN ('backup','restore') AND status IN ('queued','running')", (srv["id"],))
            if not busy:
                P.jobs.submit("backup", srv["id"], lambda job, sid=srv["id"]: P.backups.create(job, sid, kind="auto"))

    @asynccontextmanager
    async def lifespan(app):
        await asyncio.to_thread(P.on_startup)
        task = asyncio.create_task(loop_fast())
        try:
            yield
        finally:
            task.cancel()
            await asyncio.to_thread(P.on_shutdown)

    app = FastAPI(title="Minecraft Server Manager", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(Guard, panel=P)
    app.state.panel = P

    # ------------------------------------------------------------------ erreurs
    @app.exception_handler(AppError)
    async def _app_err(request, exc):
        h = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        return JSONResponse({"detail": str(exc), "code": exc.code}, status_code=exc.status, headers=h)

    @app.exception_handler(UserError)
    @app.exception_handler(ProcessError)
    @app.exception_handler(SecurityError)
    @app.exception_handler(SourceError)
    @app.exception_handler(ModpackError)
    async def _bad(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(Exception)
    async def _unexpected(request, exc):
        import logging
        logging.getLogger("mcpanel").exception("Erreur inattendue sur %s %s", request.method, request.url.path)
        return JSONResponse({"detail": "Erreur interne inattendue : l'opération n'a pas abouti. Le détail est dans le journal du panneau."}, status_code=500)

    @app.exception_handler(HTTPException)
    async def _http(request, exc):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    # ------------------------------------------------------------------ authentification
    def ip_of(request: Request) -> str:
        return request.client.host if request.client else "?"

    def any_session(request: Request) -> dict:
        s = P.sessions.get(request.cookies.get(COOKIE))
        if not s:
            raise HTTPException(401, "Non connecté.")
        wait = P.limiter.hit(f"ui:{s['user_id']}", 600, 60)
        if wait:
            raise HTTPException(429, "Trop de requêtes : patientez un instant.", headers={"Retry-After": str(wait)})
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), s["csrf"]):
                raise HTTPException(403, "Jeton CSRF invalide.")
        return s

    def admin_session(s: dict = Depends(any_session)) -> dict:
        if s["role"] != "admin":
            raise HTTPException(403, "Réservé aux administrateurs.")
        return s

    def audit(request: Request, s: dict, action: str, target: str = "", detail: str = ""):
        P.audit(s["username"], ip_of(request), action, target, detail)

    @app.post("/api/auth/login")
    def login(request: Request, response: Response, body: dict = Body(...)):
        ip = ip_of(request)
        wait = P.throttle.locked_for(ip)
        if wait:
            raise HTTPException(429, f"Trop de tentatives : réessayez dans {wait} s.")
        user = P.db.one("SELECT * FROM users WHERE username=?", (str(body.get("username", "")),))
        ok = verify_password(str(body.get("password", "")), user["pw_hash"] if user else "scrypt$1$1$1$AA==$AA==")
        if not user or not ok:
            P.throttle.fail(ip)
            P.audit(str(body.get("username", ""))[:64], ip, "login_failed")
            raise HTTPException(401, "Identifiant ou mot de passe incorrect.")
        if not user["active"]:
            raise HTTPException(403, "Ce compte est désactivé : contactez l'administrateur.")
        P.throttle.ok(ip)
        P.db.execute("UPDATE users SET last_login=? WHERE id=?", (now(), user["id"]))
        token, csrf = P.sessions.create(user["id"], ip)
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=S.cookie_secure, max_age=S.session_hours * 3600, path="/")
        P.audit(user["username"], ip, "login")
        return {"username": user["username"], "role": user["role"], "display_name": user["display_name"] or user["username"], "csrf": csrf}

    @app.get("/api/auth/me")
    def me(s: dict = Depends(any_session)):
        return {"username": s["username"], "role": s["role"], "display_name": s["display_name"] or s["username"], "csrf": s["csrf"]}

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response, s: dict = Depends(any_session)):
        P.sessions.delete(request.cookies.get(COOKIE))
        response.delete_cookie(COOKIE, path="/")
        audit(request, s, "logout")
        return {"ok": True}

    @app.post("/api/auth/password")
    def change_password(request: Request, body: dict = Body(...), s: dict = Depends(any_session)):
        u = P.db.one("SELECT * FROM users WHERE id=?", (s["user_id"],))
        if not verify_password(str(body.get("current", "")), u["pw_hash"]):
            raise HTTPException(400, "Mot de passe actuel incorrect.")
        new = str(body.get("new", ""))
        check_password_strength(new, u["username"], S.min_password_len)
        P.db.execute("UPDATE users SET pw_hash=? WHERE id=?", (hash_password(new), u["id"]))
        P.sessions.delete_user(u["id"], keep_token=request.cookies.get(COOKIE))
        audit(request, s, "password_changed")
        return {"ok": True}

    # ------------------------------------------------------------------ vues générales
    def snapshot() -> dict:
        snap = P.monitor.snapshot or {"system": P.monitor.system(), "servers": P.monitor.servers_overview()}
        return {**snap, "alerts": P.alerts.active()}

    @app.get("/api/system")
    def system(s: dict = Depends(admin_session)):
        return snapshot()

    @app.get("/api/alerts")
    def alerts(s: dict = Depends(admin_session)):
        return {"alerts": P.alerts.active(), "recent": P.alerts.recent(30)}

    @app.post("/api/alerts/{aid}/ack")
    def ack(aid: int, s: dict = Depends(admin_session)):
        P.alerts.ack(aid)
        return {"ok": True}

    @app.get("/api/audit")
    def audit_list(limit: int = Query(60, ge=1, le=500), s: dict = Depends(admin_session)):
        return {"entries": P.db.query("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))}

    @app.get("/api/usage")
    def usage(s: dict = Depends(admin_session)):
        if time.time() - usage_cache["t"] > 120:
            out = {}
            for srv in P.db.query("SELECT id, path FROM servers"):
                try:
                    out[srv["id"]] = fsops.disk_usage(Path(srv["path"]))
                except OSError:
                    out[srv["id"]] = 0
            usage_cache.update(t=time.time(), v=out)
        return {"usage": usage_cache["v"]}

    @app.get("/api/settings")
    def get_settings(s: dict = Depends(admin_session)):
        return {"ram_reserve_mb": P.monitor.ram_reserve_mb(), "ram_overhead_pct": P.monitor.overhead_pct(), "curseforge_key_set": bool(P.db.get_setting("curseforge_key")),
                "version": __version__, "data_root": str(S.data_root), "port": S.port,
                "allowed_nets": list(S.allowed_nets), "max_upload_mb": S.max_upload_mb, **P.accounts.limits()}

    @app.put("/api/settings")
    def put_settings(request: Request, body: dict = Body(...), s: dict = Depends(admin_session)):
        if "ram_reserve_mb" in body:
            try:
                v = int(body["ram_reserve_mb"])
            except (TypeError, ValueError):
                raise UserError("Réserve invalide.")
            if not 512 <= v <= 8192:
                raise UserError("La réserve système doit être comprise entre 512 et 8192 Mo.")
            P.db.set_setting("ram_reserve_mb", str(v))
        if "ram_overhead_pct" in body:
            try:
                v = int(body["ram_overhead_pct"])
            except (TypeError, ValueError):
                raise UserError("Marge Java invalide.")
            if not 5 <= v <= 50:
                raise UserError("La marge Java doit être comprise entre 5 et 50 %.")
            P.db.set_setting("ram_overhead_pct", str(v))
        for key, lo, hi in (("start_cooldown_s", 0, 86400), ("max_starts_per_hour", 1, 100)):
            if key in body:
                v = int(body[key])
                if not lo <= v <= hi:
                    raise UserError(f"{key} doit être compris entre {lo} et {hi}.")
                P.db.set_setting(key, str(v))
        if body.get("curseforge_key"):
            P.db.set_setting("curseforge_key", str(body["curseforge_key"]).strip()[:200])
        audit(request, s, "settings_updated", detail=", ".join(k for k in body if k != "curseforge_key") + (" curseforge_key" if body.get("curseforge_key") else ""))
        return {"ok": True}

    @app.get("/api/jobs/{jid}")
    def job(jid: str, s: dict = Depends(admin_session)):
        j = P.jobs.get(jid)
        if not j:
            raise HTTPException(404, "Tâche introuvable.")
        return j

    # ------------------------------------------------------------------ métadonnées de création
    @app.get("/api/meta/limits")
    def limits(s: dict = Depends(admin_session)):
        return P.limits()

    @app.get("/api/meta/ram-forecast")
    def ram_forecast(xmx: int = 0, server: str | None = None, s: dict = Depends(admin_session)):
        return P.monitor.forecast_with(max(0, int(xmx)), exclude_sid=server)

    @app.get("/api/meta/minecraft")
    def meta_mc(type: str = "vanilla", snapshots: bool = False, s: dict = Depends(admin_session)):
        return {"versions": P.sources.supported_mc(type, snapshots)}

    @app.get("/api/meta/loaders")
    def meta_loaders(type: str, mc: str, s: dict = Depends(admin_session)):
        return {"versions": P.sources.loader_versions(type, mc)}

    @app.get("/api/meta/java-need")
    def java_need(mc: str, s: dict = Depends(admin_session)):
        need = P.sources.mojang.version_info(mc)["java_major"]
        major = choose_java_major(need)
        return {"required": need, "runtime": major, "installed": P.java.find(major) is not None}

    @app.get("/api/java")
    def java_list(s: dict = Depends(admin_session)):
        return {"installed": P.java.installed()}

    @app.post("/api/java/{major}")
    def java_install(major: int, request: Request, s: dict = Depends(admin_session)):
        if major not in (8, 11, 17, 21, 25):
            raise UserError("Version de Java non proposée.")
        audit(request, s, "java_install", str(major))
        jid = P.jobs.submit("java", None, lambda job: {"path": str(P.java.ensure(major, job.log, lambda p: job.progress(100 * p))[0])})
        return {"job": jid}

    # ------------------------------------------------------------------ serveurs
    def overview(sid: str) -> dict:
        srv = P.manager.server(sid)
        ov = next((o for o in P.monitor.servers_overview() if o["id"] == sid), {})
        extra = {k: srv[k] for k in ("path", "java_path", "jvm_extra", "backup_prefs", "created_at", "desired_state", "notify_prefs")}
        return {**ov, **extra}

    @app.get("/api/servers")
    def servers_list(s: dict = Depends(admin_session)):
        return {"servers": P.monitor.servers_overview()}

    @app.post("/api/servers")
    def servers_create(request: Request, body: dict = Body(...), s: dict = Depends(admin_session)):
        srv, jid = P.create_server(body)
        audit(request, s, "server_create", P.label(srv), f"{srv['server_type']} {srv['mc_version']}")
        return {"server": {"id": srv["id"], "name": P.label(srv), "slug": srv["name"], "status": srv["status"]}, "job": jid}

    @app.get("/api/servers/{sid}/whitelist")
    def wl_get(sid: str, q: str = "", s: dict = Depends(admin_session)):
        return P.whitelist.status(sid, q)

    @app.post("/api/servers/{sid}/whitelist")
    def wl_add(sid: str, request: Request, body: dict = Body(...), s: dict = Depends(admin_session)):
        r = P.whitelist.add(sid, str(body.get("name", "")))
        audit(request, s, "whitelist_add", P.label(P.manager.server(sid)), r["added"])
        return r

    @app.delete("/api/servers/{sid}/whitelist/{name}")
    def wl_remove(sid: str, name: str, request: Request, s: dict = Depends(admin_session)):
        r = P.whitelist.remove(sid, name)
        audit(request, s, "whitelist_remove", P.label(P.manager.server(sid)), r["removed"])
        return r

    @app.put("/api/servers/{sid}/whitelist/enabled")
    def wl_enabled(sid: str, request: Request, body: dict = Body(...), s: dict = Depends(admin_session)):
        r = P.whitelist.set_enabled(sid, bool(body.get("enabled")))
        audit(request, s, "whitelist_toggle", P.label(P.manager.server(sid)), "on" if r["enabled"] else "off")
        return r

    @app.get("/api/notifications/center")
    def notif_center(s: dict = Depends(admin_session)):
        return P.events.notifications(s["username"])

    @app.post("/api/notifications/seen")
    def notif_seen(body: dict = Body(default={}), s: dict = Depends(admin_session)):
        up = body.get("upto")
        try:
            up = int(up) if up is not None else None
        except (TypeError, ValueError):
            raise UserError("Identifiant invalide.")
        P.events.mark_seen(s["username"], up)
        return {"ok": True}

    @app.get("/api/crashes")
    def crash_list(server: str | None = None, s: dict = Depends(admin_session)):
        return {"crashes": P.crashes.list(server)}

    @app.get("/api/crashes/{cid}")
    def crash_get(cid: int, s: dict = Depends(admin_session)):
        r = P.crashes.get(cid)
        if not r:
            raise HTTPException(404, "Rapport introuvable.")
        return r

    @app.put("/api/servers/{sid}/notify")
    def notify_prefs(sid: str, request: Request, body: dict = Body(...), s: dict = Depends(admin_session)):
        from .events import KINDS
        P.manager.server(sid)
        muted = [k for k in (body.get("muted") if isinstance(body.get("muted"), list) else []) if k in KINDS]
        P.db.execute("UPDATE servers SET notify_prefs=? WHERE id=?", (json.dumps({"muted": muted}), sid))
        audit(request, s, "notify_prefs", P.label(P.manager.server(sid)), ",".join(muted))
        return {"muted": muted}

    @app.get("/api/servers/{sid}/perf")
    def perf_get(sid: str, hours: float = 24, s: dict = Depends(admin_session)):
        srv = P.manager.server(sid)
        return {"current": P.perf.info(srv), "history": P.perf.history(sid, max(0.1, min(hours, 168)))}

    @app.get("/api/servers/{sid}/mcstats")
    def mcstats_get(sid: str, s: dict = Depends(admin_session)):
        from .mcstats import server_stats
        return server_stats(P, P.manager.server(sid))

    @app.get("/api/ping")
    def ping_rtt(s: dict = Depends(any_session)):
        return {"pong": True}

    # ------------------------------------------------------------------ mods : recherche Modrinth / CurseForge (V4 §3)
    def _kind(kind):
        return kind if kind in ("mod", "plugin") else None

    @app.get("/api/servers/{sid}/modsearch")
    def ms_search(sid: str, source: str = "modrinth", q: str = "", category: str = "", offset: int = 0, kind: str = "", any: bool = False, s: dict = Depends(admin_session)):
        return P.modsearch.search(P.manager.server(sid), source, q, category, offset, _kind(kind), any)

    @app.get("/api/servers/{sid}/modsearch/categories")
    def ms_cats(sid: str, source: str = "modrinth", kind: str = "", s: dict = Depends(admin_session)):
        from .modsearch import target
        t = target(P.manager.server(sid), P.sources.adapters, _kind(kind))
        return {"categories": P.modsearch.categories(source, t["kind"])}

    @app.get("/api/servers/{sid}/modsearch/project")
    def ms_project(sid: str, source: str, id: str, kind: str = "", s: dict = Depends(admin_session)):
        return P.modsearch.project(P.manager.server(sid), source, id, _kind(kind))

    @app.get("/api/servers/{sid}/modsearch/plan")
    def ms_plan(sid: str, source: str, version: str, project: str = "", kind: str = "", s: dict = Depends(admin_session)):
        return P.modsearch.plan(P.manager.server(sid), source, version, project or None, _kind(kind))

    @app.post("/api/servers/{sid}/modsearch/install")
    def ms_install(sid: str, request: Request, body: dict = Body(...), s: dict = Depends(admin_session)):
        srv = P.manager.server(sid)
        src, ver = str(body.get("source", "")), str(body.get("version", ""))
        if src not in ("modrinth", "curseforge") or not ver:
            raise UserError("Source ou version manquante.")
        proj = str(body.get("project") or "") or None
        rep = body.get("replace")
        rep = str(rep) if isinstance(rep, str) and rep else None
        audit(request, s, "mod_install", P.label(srv), f"{src} {proj or ''} {ver}")
        return {"job": P.jobs.submit("mod-install", sid, P.modsearch.install, srv, src, ver, proj, _kind(body.get("kind")), rep)}

    @app.get("/api/servers/{sid}/mods/updates")
    def ms_updates(sid: str, s: dict = Depends(admin_session)):
        return {"updates": P.modsearch.updates(P.manager.server(sid))}

    @app.get("/api/servers/{sid}/configs")
    def cfg_files(sid: str, s: dict = Depends(admin_session)):
        return {"files": P.modconfig.files(sid)}

    @app.get("/api/servers/{sid}/configs/file")
    def cfg_get(sid: str, path: str, s: dict = Depends(admin_session)):
        return P.modconfig.get(sid, path)

    @app.put("/api/servers/{sid}/configs/file")
    def cfg_save(sid: str, path: str, request: Request, body: dict = Body(...), s: dict = Depends(admin_session)):
        r = P.modconfig.save(sid, path, body.get("changes") if isinstance(body.get("changes"), dict) else {}, body.get("mtime"))
        audit(request, s, "config_save", P.label(P.manager.server(sid)), f"{path} : {', '.join(r['saved'])[:200]}")
        return r

    @app.post("/api/servers/{sid}/configs/restore")
    def cfg_restore(sid: str, path: str, request: Request, body: dict = Body(...), s: dict = Depends(admin_session)):
        r = P.modconfig.restore(sid, path, str(body.get("id", "")))
        audit(request, s, "config_restore", P.label(P.manager.server(sid)), f"{path} ← {r['restored']}")
        return r

    @app.post("/api/servers/{sid}/configs/regenerate")
    def cfg_regen(sid: str, path: str, request: Request, s: dict = Depends(admin_session)):
        r = P.modconfig.regenerate(sid, path)
        audit(request, s, "config_regenerate", P.label(P.manager.server(sid)), path)
        return r

    @app.post("/api/jobs/{jid}/cancel")
    def job_cancel(jid: str, s: dict = Depends(admin_session)):
        if not P.jobs.cancel(jid):
            raise UserError("Cette tâche n'est plus en cours.")
        return {"ok": True}

    # ------------------------------------------------------------------ stockage SSD / HDD et archives de mondes (V4 §7)
    @app.get("/api/storage")
    def storage_info(s: dict = Depends(admin_session)):
        return P.storage.overview()

    @app.put("/api/storage/settings")
    def storage_settings(request: Request, body: dict = Body(...), s: dict = Depends(admin_session)):
        if "quota_gb" in body:
            try:
                q = float(body["quota_gb"])
            except (TypeError, ValueError):
                raise UserError("Quota invalide.")
            if not 5 <= q <= 2000:
                raise UserError("Le quota SSD doit être compris entre 5 et 2000 Go.")
            P.db.set_setting("ssd_quota_gb", str(q))
        if "auto" in body:
            P.db.set_setting("ssd_auto", "1" if body["auto"] else "0")
        audit(request, s, "storage_settings", "", json.dumps(body)[:200])
        return P.storage.overview()

    @app.get("/api/servers/{sid}/storage/plan")
    def storage_plan(sid: str, s: dict = Depends(admin_session)):
        return P.storage.plan_promotion(P.manager.server(sid))

    @app.post("/api/servers/{sid}/storage/move")
    def storage_move(sid: str, request: Request, body: dict = Body(...), s: dict = Depends(admin_session)):
        tier = str(body.get("tier", ""))
        srv = P.manager.server(sid)
        if tier == "ssd":
            plan = P.storage.plan_promotion(srv)
            if not plan["possible"]:
                raise UserError(plan["reason"])
            def run(job):
                for e in plan["evict"]:
                    P.storage.move(job, e["id"], "hdd")
                return P.storage.move(job, sid, "ssd")
            jid = P.jobs.submit("storage-move", sid, run)
        else:
            jid = P.jobs.submit("storage-move", sid, P.storage.move, sid, tier)
        audit(request, s, "storage_move", P.label(srv), tier)
        return {"job": jid}

    @app.post("/api/servers/{sid}/world/archive")
    def world_archive(sid: str, request: Request, body: dict = Body(default={}), s: dict = Depends(admin_session)):
        rem = bool(body.get("remove_after"))
        if rem and body.get("confirm") is not True:
            raise UserError("Retirer le monde du serveur demande une confirmation explicite.")
        audit(request, s, "world_archive", P.label(P.manager.server(sid)), "retrait" if rem else "copie")
        return {"job": P.jobs.submit("world-archive", sid, P.storage.archive, sid, rem)}

    @app.get("/api/archives")
    def archives_list(server: str | None = None, s: dict = Depends(admin_session)):
        return {"archives": P.storage.list_archives(server)}

    @app.post("/api/archives/{aid}/verify")
    def archive_verify(aid: str, s: dict = Depends(admin_session)):
        return P.storage.verify_archive(aid)

    @app.post("/api/archives/{aid}/restore")
    def archive_restore(aid: str, request: Request, body: dict = Body(...), s: dict = Depends(admin_session)):
        sid = str(body.get("server_id", ""))
        P.manager.server(sid)
        audit(request, s, "world_restore", P.label(P.manager.server(sid)), aid)
        return {"job": P.jobs.submit("world-restore", sid, P.storage.restore_archive, aid, sid)}

    @app.get("/api/primary")
    def primary_info(s: dict = Depends(admin_session)):
        h = P.primary.holder()
        return {"port": P.primary.port, "server": {"id": h["id"], "name": P.label(h)} if h else None}

    @app.get("/api/servers/{sid}/primary/plan")
    def primary_plan(sid: str, s: dict = Depends(admin_session)):
        return P.primary.plan(sid)

    @app.post("/api/servers/{sid}/primary")
    def primary_set(sid: str, request: Request, body: dict = Body(default={}), s: dict = Depends(admin_session)):
        plan = P.primary.plan(sid)
        if plan["restart"] and not body.get("confirm"):
            raise UserError("Des serveurs vont redémarrer : confirmez l'opération.")
        audit(request, s, "primary_set", P.label(P.manager.server(sid)), ", ".join(f"{c['name']} {c['from']}→{c['to']}" for c in plan["changes"]))
        jid = P.jobs.submit("primary", sid, P.primary.execute, sid)
        return {"job": jid, "plan": plan}

    @app.get("/api/meta/mcsettings-schema")
    def mcs_schema(s: dict = Depends(admin_session)):
        return P.mcsettings.schema()

    @app.get("/api/servers/{sid}/mcsettings")
    def mcs_get(sid: str, s: dict = Depends(admin_session)):
        return P.mcsettings.get(sid)

    @app.put("/api/servers/{sid}/mcsettings")
    def mcs_save(sid: str, request: Request, body: dict = Body(...), s: dict = Depends(admin_session)):
        r = P.mcsettings.save(sid, body.get("changes") or {}, str(body.get("hash", "")), user=s["username"],
                              confirm_offline=bool(body.get("confirm_offline")), unknown_delete=list(body.get("delete") or []), note=str(body.get("note", ""))[:200])
        audit(request, s, "mcsettings_save", P.label(P.manager.server(sid)), ", ".join(r["changed"])[:300])
        return r

    @app.get("/api/servers/{sid}/mcsettings/history")
    def mcs_history(sid: str, s: dict = Depends(admin_session)):
        return {"history": P.mcsettings.history(sid)}

    @app.post("/api/servers/{sid}/mcsettings/revert")
    def mcs_revert(sid: str, request: Request, body: dict = Body(...), s: dict = Depends(admin_session)):
        try:
            hid = int(body.get("id", 0))
        except (TypeError, ValueError):
            raise UserError("Version invalide.")
        r = P.mcsettings.revert(sid, hid, user=s["username"])
        audit(request, s, "mcsettings_revert", P.label(P.manager.server(sid)), f"#{r['reverted']}")
        return r

    @app.get("/api/servers/{sid}/import/inspect")
    def import_inspect(sid: str, jar: str = "server.jar", s: dict = Depends(admin_session)):
        return P.inspect_import(sid, jar)

    @app.post("/api/servers/{sid}/import/finalize")
    def import_finalize(sid: str, request: Request, body: dict = Body(default={}), s: dict = Depends(admin_session)):
        jid = P.finalize_import(sid, str(body.get("jar") or "server.jar"))
        audit(request, s, "server_import_jar", P.label(P.manager.server(sid)), str(body.get("jar") or "server.jar"))
        return {"job": jid}

    @app.get("/api/servers/{sid}")
    def server_get(sid: str, s: dict = Depends(admin_session)):
        return overview(sid)

    @app.patch("/api/servers/{sid}")
    def server_patch(sid: str, request: Request, body: dict = Body(...), s: dict = Depends(admin_session)):
        P.update_server(sid, body)
        audit(request, s, "server_update", P.label(P.manager.server(sid)), ", ".join(body))
        return overview(sid)

    @app.delete("/api/servers/{sid}")
    def server_delete(sid: str, request: Request, body: dict = Body(...), s: dict = Depends(admin_session)):
        name = P.label(P.manager.server(sid))
        P.delete_server(sid, str(body.get("confirm_name", "")), bool(body.get("delete_backups")))
        audit(request, s, "server_delete", name, "avec sauvegardes" if body.get("delete_backups") else "")
        return {"ok": True}

    @app.post("/api/servers/{sid}/reinstall")
    def server_reinstall(sid: str, request: Request, s: dict = Depends(admin_session)):
        audit(request, s, "server_reinstall", P.label(P.manager.server(sid)))
        return {"job": P.reinstall(sid)}

    @app.post("/api/servers/{sid}/eula")
    def server_eula(sid: str, request: Request, body: dict = Body(...), s: dict = Depends(admin_session)):
        if body.get("accept") is not True:
            raise UserError("Vous devez accepter explicitement le CLUF (EULA) de Minecraft.")
        P.accept_eula(sid)
        audit(request, s, "eula_accepted", P.label(P.manager.server(sid)))
        return {"ok": True}

    @app.post("/api/servers/{sid}/start")
    def server_start(sid: str, request: Request, s: dict = Depends(admin_session)):
        srv = P.manager.server(sid)
        if srv["status"] != "installed":
            raise UserError("Le serveur n'est pas (entièrement) installé.")
        if not srv["eula_accepted"]:
            raise UserError("Le CLUF (EULA) de Minecraft doit d'abord être accepté.")
        r = P.start_server(sid)
        audit(request, s, "server_start", P.label(srv))
        return r

    @app.post("/api/servers/{sid}/stop")
    def server_stop(sid: str, request: Request, s: dict = Depends(admin_session)):
        srv = P.manager.server(sid)
        P.manager.stop(sid, 90)
        audit(request, s, "server_stop", P.label(srv))
        return {"ok": True}

    @app.post("/api/servers/{sid}/restart")
    def server_restart(sid: str, request: Request, s: dict = Depends(admin_session)):
        srv = P.manager.server(sid)
        threading.Thread(target=lambda: _quiet(P.manager.restart, sid, 90), daemon=True).start()
        audit(request, s, "server_restart", P.label(srv))
        return {"ok": True}

    @app.post("/api/servers/{sid}/kill")
    def server_kill(sid: str, request: Request, confirm: bool = False, s: dict = Depends(admin_session)):
        if not confirm:
            raise UserError("Arrêt forcé : confirmation requise (risque de perte de données).")
        srv = P.manager.server(sid)
        P.manager.kill(sid)
        audit(request, s, "server_kill", P.label(srv))
        return {"ok": True}

    def _quiet(fn, *a):
        try:
            fn(*a)
        except Exception as e:  # noqa: BLE001
            P.alerts.raise_(f"restart-fail", "warning", f"Redémarrage impossible : {e}")

    # ------------------------------------------------------------------ fichiers
    def root_of(sid: str) -> Path:
        return P.server_root(sid)[1]

    @app.get("/api/servers/{sid}/files")
    def files_list(sid: str, path: str = "", s: dict = Depends(admin_session)):
        return fsops.list_dir(root_of(sid), path)

    @app.get("/api/servers/{sid}/files/read")
    def files_read(sid: str, path: str, s: dict = Depends(admin_session)):
        return fsops.read_text(root_of(sid), path)

    @app.put("/api/servers/{sid}/files/write")
    def files_write(sid: str, request: Request, body: dict = Body(...), s: dict = Depends(admin_session)):
        root = root_of(sid)
        fsops.write_text(root, str(body.get("path", "")), str(body.get("content", "")), body.get("mtime"))
        audit(request, s, "file_write", f"{sid}:{body.get('path')}")
        return {"mtime": safe_join(root, str(body["path"])).stat().st_mtime}

    @app.post("/api/servers/{sid}/files/mkdir")
    def files_mkdir(sid: str, request: Request, body: dict = Body(...), s: dict = Depends(admin_session)):
        fsops.mkdir(root_of(sid), str(body.get("path", "")))
        return {"ok": True}

    @app.post("/api/servers/{sid}/files/rename")
    def files_rename(sid: str, request: Request, body: dict = Body(...), s: dict = Depends(admin_session)):
        fsops.rename(root_of(sid), str(body.get("src", "")), str(body.get("dst", "")))
        audit(request, s, "file_rename", f"{sid}:{body.get('src')}", str(body.get("dst")))
        return {"ok": True}

    @app.post("/api/servers/{sid}/files/copy")
    def files_copy(sid: str, request: Request, body: dict = Body(...), s: dict = Depends(admin_session)):
        fsops.copy(root_of(sid), str(body.get("src", "")), str(body.get("dst", "")))
        return {"ok": True}

    @app.delete("/api/servers/{sid}/files")
    def files_delete(sid: str, request: Request, path: str, s: dict = Depends(admin_session)):
        fsops.delete(root_of(sid), path)
        audit(request, s, "file_delete", f"{sid}:{path}")
        return {"ok": True}

    @app.get("/api/servers/{sid}/files/download")
    def files_download(sid: str, path: str, s: dict = Depends(admin_session)):
        p = safe_join(root_of(sid), path)
        if not p.is_file():
            raise HTTPException(404, "Fichier introuvable.")
        return FileResponse(p, filename=p.name)

    async def receive_to(request: Request, dest: Path, overwrite: bool, limit_mb: int | None = None) -> int:
        """Reçoit le corps de la requête dans un fichier .part puis le renomme (jamais de fichier à moitié écrit)."""
        if dest.exists() and not overwrite:
            raise UserError(f"« {dest.name} » existe déjà.")
        if dest.is_dir():
            raise UserError("La cible est un dossier.")
        if not dest.parent.is_dir():
            dest.parent.mkdir(parents=True, exist_ok=True)
        limit = (limit_mb or S.max_upload_mb) * 1024 * 1024
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise HTTPException(413, f"Fichier trop volumineux (maximum {limit // 1048576} Mo).")
        if shutil.disk_usage(dest.parent).free < int(declared or 0) + 512 * 1024 * 1024:
            raise HTTPException(507, "Espace disque insuffisant.")
        part = dest.with_name(dest.name + ".part")
        n = 0
        try:
            with open(part, "wb") as f:
                async for chunk in request.stream():
                    n += len(chunk)
                    if n > limit:
                        raise HTTPException(413, f"Fichier trop volumineux (maximum {limit // 1048576} Mo).")
                    await asyncio.to_thread(f.write, chunk)
            os.replace(part, dest)
        finally:
            part.unlink(missing_ok=True)
        return n

    @app.put("/api/servers/{sid}/files/upload")
    async def files_upload(sid: str, request: Request, path: str, overwrite: bool = False, s: dict = Depends(admin_session)):
        root = root_of(sid)
        dest = safe_join(root, path)
        if dest == Path(os.path.realpath(root)):
            raise UserError("Chemin invalide.")
        if dest.name.endswith((".part", ".panel-tmp")):
            raise UserError("Extension réservée.")
        n = await receive_to(request, dest, overwrite)
        audit(request, s, "file_upload", f"{sid}:{path}", f"{n} octets")
        return {"size": n}

    # ------------------------------------------------------------------ mods
    def mod_path(sid: str, name: str) -> Path:
        if "/" in name or "\\" in name or not name.endswith((".jar", ".jar.disabled")):
            raise UserError("Nom de mod invalide.")
        p = safe_join(root_of(sid) / "mods", name)
        if not p.is_file():
            raise HTTPException(404, "Mod introuvable.")
        return p

    @app.get("/api/servers/{sid}/mods")
    def mods_list(sid: str, s: dict = Depends(admin_session)):
        srv, root = P.server_root(sid)
        return modsmod.scan_mods(root, srv["server_type"], srv["mc_version"])

    @app.post("/api/servers/{sid}/mods/{name}/toggle")
    def mods_toggle(sid: str, name: str, request: Request, s: dict = Depends(admin_session)):
        p = mod_path(sid, name)
        if P.manager.alive(sid):
            raise UserError("Arrêtez le serveur avant d'activer ou désactiver un mod.")
        new = p.with_name(p.name[:-9] if p.name.endswith(".disabled") else p.name + ".disabled")
        if new.exists():
            raise UserError("Un fichier du même nom existe déjà.")
        p.rename(new)
        audit(request, s, "mod_toggle", f"{sid}:{name}")
        return {"file": new.name}

    @app.delete("/api/servers/{sid}/mods/{name}")
    def mods_delete(sid: str, name: str, request: Request, confirm: bool = False, s: dict = Depends(admin_session)):
        if not confirm:
            raise UserError("Confirmation requise.")
        mod_path(sid, name).unlink()
        audit(request, s, "mod_delete", f"{sid}:{name}")
        return {"ok": True}

    # ------------------------------------------------------------------ modpack
    @app.put("/api/servers/{sid}/modpack/upload")
    async def modpack_upload(sid: str, request: Request, filename: str = "pack.zip", s: dict = Depends(admin_session)):
        srv, root = P.server_root(sid)
        if P.manager.alive(sid):
            raise UserError("Arrêtez le serveur avant d'importer un modpack.")
        P.ensure_data_dirs()
        uid = new_id()
        dest = S.uploads_dir / f"{uid}.zip"
        await receive_to(request, dest, False)
        a = await asyncio.to_thread(modpacks.analyze_zip, dest, srv, root)
        uploads[uid] = {"sid": sid, "path": dest, "analysis": a, "filename": os.path.basename(filename)[:120]}
        audit(request, s, "modpack_upload", f"{sid}:{uid}", uploads[uid]["filename"])
        return {"upload_id": uid, "analysis": a}

    def get_upload(sid: str, uid: str) -> dict:
        u = uploads.get(uid)
        if not u or u["sid"] != sid or not Path(u["path"]).exists():
            raise HTTPException(404, "Import introuvable ou expiré : renvoyez l'archive.")
        return u

    @app.delete("/api/servers/{sid}/modpack/{uid}")
    def modpack_cancel(sid: str, uid: str, s: dict = Depends(admin_session)):
        u = uploads.pop(uid, None)
        if u and u["sid"] == sid:
            Path(u["path"]).unlink(missing_ok=True)
        return {"ok": True}

    @app.post("/api/servers/{sid}/modpack/{uid}/apply")
    def modpack_apply(sid: str, uid: str, request: Request, body: dict = Body(default={}), s: dict = Depends(admin_session)):
        u = get_upload(sid, uid)
        srv, root = P.server_root(sid)
        if P.manager.alive(sid):
            raise UserError("Arrêtez le serveur avant d'importer un modpack.")
        a = u["analysis"]
        if a["errors"]:
            raise UserError("Archive refusée : " + a["errors"][0])
        if a["mismatch"] and not body.get("ignore_mismatch"):
            raise UserError("Le pack semble incompatible avec ce serveur : confirmez explicitement pour l'importer malgré tout.")
        policy = body.get("policy", "skip")
        backup_first = bool(body.get("backup_first", True))
        cf_key = P.db.get_setting("curseforge_key")

        def run(job):
            try:
                if backup_first:
                    job.step("Sauvegarde de sécurité avant import", 2)
                    P.backups.create(job, sid, kind="pre-import", protected=True)
                job.step("Installation du pack", 40)
                return modpacks.apply_plan(Path(u["path"]), a, root, policy=policy, http=P.http, cf_key=cf_key, log=job.log,
                                           progress=lambda p: job.progress(40 + 58 * p))
            finally:
                Path(u["path"]).unlink(missing_ok=True)
                uploads.pop(uid, None)

        audit(request, s, "modpack_apply", f"{sid}:{u['filename']}", f"politique={policy}")
        return {"job": P.jobs.submit("modpack", sid, run)}

    # ------------------------------------------------------------------ sauvegardes
    @app.get("/api/servers/{sid}/backups")
    def backups_list(sid: str, s: dict = Depends(admin_session)):
        P.manager.server(sid)
        return {"backups": P.backups.list(sid)}

    @app.post("/api/servers/{sid}/backups")
    def backups_create(sid: str, request: Request, s: dict = Depends(admin_session)):
        srv = P.manager.server(sid)
        P.ensure_data_dirs()
        audit(request, s, "backup_create", P.label(srv))
        return {"job": P.jobs.submit("backup", sid, lambda job: P.backups.create(job, sid, kind="manual"))}

    @app.post("/api/servers/{sid}/backups/{bid}/restore")
    def backups_restore(sid: str, bid: str, request: Request, s: dict = Depends(admin_session)):
        srv = P.manager.server(sid)
        if P.manager.alive(sid):
            raise UserError("Arrêtez le serveur avant de restaurer une sauvegarde.")
        audit(request, s, "backup_restore", P.label(srv), bid)
        return {"job": P.jobs.submit("restore", sid, lambda job: P.backups.restore(job, sid, bid))}

    @app.get("/api/servers/{sid}/backups/{bid}/download")
    def backups_download(sid: str, bid: str, s: dict = Depends(admin_session)):
        b, p = P.backups.path_of(bid)
        if b["server_id"] != sid or not p.exists():
            raise HTTPException(404, "Sauvegarde introuvable.")
        return FileResponse(p, filename=b["filename"])

    @app.delete("/api/servers/{sid}/backups/{bid}")
    def backups_delete(sid: str, bid: str, request: Request, confirm: bool = False, s: dict = Depends(admin_session)):
        if not confirm:
            raise UserError("Confirmation requise.")
        b, _ = P.backups.path_of(bid)
        if b["server_id"] != sid:
            raise HTTPException(404, "Sauvegarde introuvable.")
        P.backups.delete(bid)
        audit(request, s, "backup_delete", b["server_name"], b["filename"])
        return {"ok": True}

    # ------------------------------------------------------------------ comptes (administrateur)
    def principal(s: dict) -> dict:
        return {"user_id": s["user_id"], "username": s["username"], "role": s["role"], "token_id": None, "scopes": None}

    @app.get("/api/users")
    def users_list(s: dict = Depends(admin_session)):
        return {"users": P.accounts.list_users(), "servers": [{"id": r["id"], "name": P.label(r)} for r in P.db.query("SELECT id,name,display_name FROM servers ORDER BY display_name COLLATE NOCASE")]}

    @app.post("/api/users")
    def users_create(request: Request, body: dict = Body(...), s: dict = Depends(admin_session)):
        u = P.accounts.create(str(body.get("username", "")), str(body.get("password", "")), str(body.get("role", "user")),
                              str(body.get("display_name", "")), body.get("servers"))
        audit(request, s, "user_create", u["username"], f"rôle {u['role']}")
        return u

    @app.patch("/api/users/{uid}")
    def users_update(uid: int, request: Request, body: dict = Body(...), s: dict = Depends(admin_session)):
        u = P.accounts.update(uid, body, acting_uid=s["user_id"])
        audit(request, s, "user_update", u["username"], ", ".join(k for k in body if k != "password") + (" mot_de_passe" if body.get("password") else ""))
        return u

    @app.delete("/api/users/{uid}")
    def users_delete(uid: int, request: Request, s: dict = Depends(admin_session)):
        name = P.accounts.get(uid)["username"]
        P.accounts.delete(uid, acting_uid=s["user_id"])
        audit(request, s, "user_delete", name)
        return {"ok": True}

    # ------------------------------------------------------------------ interface utilisateur simplifiée (tout rôle, serveurs attribués)
    def host_of(request: Request) -> str:
        return request.url.hostname or "localhost"

    def my_views(request: Request, s: dict) -> list[dict]:
        ids = set(P.accounts.server_ids_for(principal(s)))
        host = host_of(request)
        return [P.accounts.public_view(o, host) for o in P.monitor.servers_overview() if o["id"] in ids]

    @app.get("/api/me/servers")
    def me_servers(request: Request, s: dict = Depends(any_session)):
        return {"servers": my_views(request, s), "limits": P.accounts.limits() if s["role"] != "admin" else None}

    @app.post("/api/me/servers/{sid}/start")
    def me_start(sid: str, request: Request, s: dict = Depends(any_session)):
        return P.accounts.request_start(principal(s), sid, ip_of(request), via="interface")

    # ------------------------------------------------------------------ tokens d'API (session requise : un token ne peut pas en créer d'autres)
    from .tokens import SCOPES, ADMIN_ONLY_SCOPES

    @app.get("/api/tokens")
    def tokens_list(all: bool = False, s: dict = Depends(any_session)):
        scopes = {k: v for k, v in SCOPES.items() if s["role"] == "admin" or k not in ADMIN_ONLY_SCOPES}
        return {"tokens": P.tokens.list(None if (all and s["role"] == "admin") else s["user_id"]), "scopes": scopes}

    @app.post("/api/tokens")
    def tokens_create(request: Request, body: dict = Body(...), s: dict = Depends(any_session)):
        target = P.accounts.get(int(body["user_id"])) if body.get("user_id") and s["role"] == "admin" else P.accounts.get(s["user_id"])
        if not target["active"]:
            raise UserError("Ce compte est désactivé.")
        t, plain = P.tokens.create(target, str(body.get("name", "")), list(body.get("scopes") or []), body.get("expires_days"))
        audit(request, s, "token_create", f"{t['name']} ({target['username']})", ", ".join(t["scopes"]))
        return {"token": t, "secret": plain}

    @app.delete("/api/tokens/{tid}")
    def tokens_revoke(tid: str, request: Request, s: dict = Depends(any_session)):
        P.tokens.revoke(tid, principal(s))
        audit(request, s, "token_revoke", tid)
        return {"ok": True}

    # ------------------------------------------------------------------ notifications (webhooks Discord), événements, supervision
    @app.get("/api/webhooks")
    def webhooks_list(s: dict = Depends(admin_session)):
        from .events import KINDS
        return {"webhooks": P.events.webhooks(), "kinds": {k: v[0] for k, v in KINDS.items()}}

    @app.post("/api/webhooks")
    def webhooks_add(request: Request, body: dict = Body(...), s: dict = Depends(admin_session)):
        w = P.events.add_webhook(str(body.get("name", "")), str(body.get("url", "")), body.get("kinds"), body.get("server_ids"), body.get("cooldown_s", 60))
        audit(request, s, "webhook_create", w["name"])
        return w

    @app.patch("/api/webhooks/{wid}")
    def webhooks_update(wid: str, request: Request, body: dict = Body(...), s: dict = Depends(admin_session)):
        return P.events.update_webhook(wid, body)

    @app.delete("/api/webhooks/{wid}")
    def webhooks_delete(wid: str, request: Request, s: dict = Depends(admin_session)):
        P.events.delete_webhook(wid)
        audit(request, s, "webhook_delete", wid)
        return {"ok": True}

    @app.post("/api/webhooks/{wid}/test")
    def webhooks_test(wid: str, s: dict = Depends(admin_session)):
        return {"status": P.events.test_webhook(wid)}

    @app.get("/api/events")
    def events_list(since: int = 0, limit: int = 50, s: dict = Depends(admin_session)):
        if not since:                       # sans repère : les `limit` derniers événements (pas les plus anciens)
            since = max(0, P.db.one("SELECT COALESCE(MAX(id),0) AS m FROM events")["m"] - min(max(limit, 1), 500))
        return {"events": P.events.list(since, limit)}

    @app.get("/api/supervision")
    def supervision(s: dict = Depends(admin_session)):
        return P.supervision.overview()

    @app.get("/api/supervision/logs")
    def supervision_logs(unit: str = "minecraft-panel.service", lines: int = 100, s: dict = Depends(admin_session)):
        return P.supervision.logs(unit, lines)

    # ------------------------------------------------------------------ WebSockets
    def ws_user(ws: WebSocket) -> dict | None:
        sess = P.sessions.get(ws.cookies.get(COOKIE))
        origin = ws.headers.get("origin")
        if origin and origin.split("://", 1)[-1] != ws.headers.get("host"):
            return None
        return sess

    @app.websocket("/ws/stats")
    async def ws_stats(ws: WebSocket):
        if not ws_user(ws):
            await ws.close(code=4401)
            return
        await ws.accept()
        sess = ws_user(ws)
        host = (ws.headers.get("host") or "localhost").rsplit(":", 1)[0]
        try:
            while True:
                if not P.sessions.get(ws.cookies.get(COOKIE)):
                    await ws.close(code=4401)
                    return
                if sess["role"] == "admin":
                    msg = await asyncio.to_thread(snapshot)
                else:                                   # utilisateur classique : uniquement ses serveurs, informations publiques
                    ids = set(P.accounts.server_ids_for(principal(sess)))
                    msg = {"servers": [P.accounts.public_view(o, host) for o in (P.monitor.snapshot.get("servers") or P.monitor.servers_overview()) if o["id"] in ids]}
                await ws.send_text(json.dumps(msg, default=str))
                await asyncio.sleep(2)
        except (WebSocketDisconnect, RuntimeError):
            pass

    @app.websocket("/ws/servers/{sid}/console")
    async def ws_console(ws: WebSocket, sid: str):
        sess = ws_user(ws)
        if not sess:
            await ws.close(code=4401)
            return
        if sess["role"] != "admin":
            await ws.close(code=4403)           # la console est réservée aux administrateurs
            return
        srv = P.db.one("SELECT * FROM servers WHERE id=?", (sid,))
        if not srv:
            await ws.close(code=4404)
            return
        await ws.accept()
        who = (P.sessions.get(ws.cookies.get(COOKIE)) or {}).get("username")
        text, off = await asyncio.to_thread(P.manager.read_console, srv)
        await ws.send_text(json.dumps({"type": "history", "text": text}))

        async def pump():
            nonlocal off
            while True:
                t, off = await asyncio.to_thread(P.manager.read_console, srv, off)
                if t:
                    await ws.send_text(json.dumps({"type": "log", "text": t}))
                await asyncio.sleep(0.5)

        task = asyncio.create_task(pump())
        try:
            while True:
                msg = json.loads(await ws.receive_text())
                if msg.get("type") != "command":
                    continue
                cmd = str(msg.get("text", ""))
                try:
                    await asyncio.to_thread(P.manager.send_command, sid, cmd)
                    P.audit(who, ws.client.host if ws.client else None, "console_command", P.label(srv), P.manager.sanitize_command(cmd)[:200])
                except ProcessError as e:
                    await ws.send_text(json.dumps({"type": "error", "text": str(e)}))
        except (WebSocketDisconnect, RuntimeError, json.JSONDecodeError):
            pass
        finally:
            task.cancel()

    # ------------------------------------------------------------------ interface
    from .api_v1 import build_v1
    app.mount("/api/v1", build_v1(P))
    if S.frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(S.frontend_dir), html=True), name="ui")
    return app
