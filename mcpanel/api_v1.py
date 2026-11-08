"""API REST publique /api/v1 : réponses normalisées, tokens à permissions, limitation de débit, documentée par OpenAPI.

Conçue pour accueillir un bot Discord sans modifier le backend : liste et statut des serveurs, joueurs, démarrage autorisé,
événements (démarrage, arrêt, plantage) et WebSocket de mises à jour en temps réel.
"""
from __future__ import annotations

import asyncio
import json
import secrets
from typing import Any

from fastapi import Depends, FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .accounts import principal_from_session
from .errors import AppError
from .jobs import UserError
from .process import ProcessError, mc_status
from .tokens import SCOPES

COOKIE = "mcpanel_session"
bearer = HTTPBearer(auto_error=False, description="Token d'API (créé dans le panneau : API → Tokens), préfixe mcp_")


# ----------------------------------------------------------------------------- modèles (documentation OpenAPI)
class Meta(BaseModel):
    model_config = {"extra": "allow"}
    api_version: str = "v1"


class Envelope(BaseModel):
    ok: bool = True
    data: Any = None
    meta: Meta = Field(default_factory=Meta)


class ErrorBody(BaseModel):
    code: str = Field(description="Code stable : unauthenticated, invalid_token, insufficient_scope, forbidden, not_found, rate_limited, already_running, cannot_start, bad_request")
    message: str


class ErrorEnvelope(BaseModel):
    ok: bool = False
    error: ErrorBody
    meta: Meta = Field(default_factory=Meta)


class ServerOut(BaseModel):
    id: str
    name: str
    description: str = ""
    mc_version: str
    server_type: str
    loader_version: str | None = None
    state: str = Field(description="stopped | starting | running | stopping | installing | error")
    players: int | None = None
    max_players: int | None = None
    port: int
    address: str = Field(description="Adresse de connexion à copier dans Minecraft")


class PlayersOut(BaseModel):
    online: int | None
    max: int | None
    names: list[str] = Field(default_factory=list, description="Échantillon fourni par le serveur : peut être incomplet ou masqué")
    names_complete: bool = False


class EventOut(BaseModel):
    id: int
    type: str = Field(description="server.starting | server.started | server.stopping | server.stopped | server.crashed")
    server_id: str | None
    server_name: str | None
    ts: float
    data: dict = {}


class ServerEnvelope(Envelope):
    data: ServerOut


class ServerListEnvelope(Envelope):
    data: list[ServerOut]


class PlayersEnvelope(Envelope):
    data: PlayersOut


class EventsEnvelope(Envelope):
    data: list[EventOut]


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, retry_after: int | None = None):
        self.status, self.code, self.message, self.retry_after = status, code, message, retry_after


def ok(data=None, **meta):
    return {"ok": True, "data": data, "meta": {"api_version": "v1", **meta}}


def fail(status: int, code: str, message: str, retry_after: int | None = None) -> JSONResponse:
    h = {"Retry-After": str(retry_after)} if retry_after else None
    return JSONResponse({"ok": False, "error": {"code": code, "message": message}, "meta": {"api_version": "v1"}}, status_code=status, headers=h)


def build_v1(P) -> FastAPI:
    api = FastAPI(
        title="Minecraft Server Manager — API",
        version="4.1.0",
        description=("API REST du panneau.\n\n**Authentification** : en-tête `Authorization: Bearer mcp_…` (token créé dans le panneau). "
                     "Les permissions d'un token sont l'intersection de ses *scopes* et des droits du compte : un utilisateur classique ne voit que les serveurs qui lui sont attribués.\n\n"
                     "**Réponses** : `{ok, data, meta}` en succès, `{ok:false, error:{code,message}}` en erreur. "
                     "**Limites** : 120 requêtes/minute par token (6 démarrages/minute) ; en cas de dépassement, HTTP 429 avec `Retry-After`.\n\n"
                     "**Temps réel** : WebSocket `/api/v1/ws` (voir la page API du panneau)."),
        docs_url=None, redoc_url=None, openapi_url="/openapi.json")

    # ------------------------------------------------------------------ erreurs normalisées
    @api.exception_handler(ApiError)
    async def _api_err(request, exc: ApiError):
        return fail(exc.status, exc.code, exc.message, exc.retry_after)

    @api.exception_handler(AppError)
    async def _app_err(request, exc: AppError):
        return fail(exc.status, exc.code, str(exc), exc.retry_after)

    @api.exception_handler(UserError)
    @api.exception_handler(ProcessError)
    async def _user_err(request, exc):
        return fail(400, "bad_request", str(exc))

    @api.exception_handler(RequestValidationError)
    async def _val(request, exc):
        return fail(422, "validation_error", "Paramètres invalides : " + "; ".join(str(e.get("loc", ["?"])[-1]) for e in exc.errors()[:5]))

    @api.exception_handler(StarletteHTTPException)
    async def _http(request, exc):
        return fail(exc.status_code, "not_found" if exc.status_code == 404 else "error", str(exc.detail))

    # ------------------------------------------------------------------ authentification et limites
    def authenticate(request: Request, cred=Depends(bearer)) -> dict:
        ip = request.client.host if request.client else "?"
        wait = P.limiter.hit(f"v1ip:{ip}", 300, 60)
        if wait:
            raise ApiError(429, "rate_limited", "Trop de requêtes depuis cette adresse.", wait)
        pr = None
        if cred and cred.credentials:
            pr = P.tokens.authenticate(cred.credentials, ip)
            if not pr:
                w = P.limiter.hit(f"v1fail:{ip}", 20, 300)          # limite les essais de tokens invalides
                if w:
                    raise ApiError(429, "rate_limited", "Trop d'échecs d'authentification.", w)
                P.audit(None, ip, "api_auth_failed", "", "token invalide, révoqué ou expiré")
                raise ApiError(401, "invalid_token", "Token invalide, révoqué ou expiré.")
        else:
            s = P.sessions.get(request.cookies.get(COOKIE))
            if not s:
                raise ApiError(401, "unauthenticated", "Authentification requise : en-tête Authorization: Bearer mcp_…")
            if request.method not in ("GET", "HEAD", "OPTIONS") and not secrets.compare_digest(request.headers.get("x-csrf-token", ""), s["csrf"]):
                raise ApiError(403, "csrf", "Jeton CSRF invalide.")
            pr = principal_from_session(s)
        wait = P.limiter.hit(f"v1p:{pr['token_id'] or pr['user_id']}", 120, 60)
        if wait:
            raise ApiError(429, "rate_limited", "Limite de 120 requêtes par minute atteinte.", wait)
        request.state.principal = pr
        return pr

    def need(scope: str, admin: bool = False):
        def dep(pr: dict = Depends(authenticate)) -> dict:
            if pr["scopes"] is not None and scope not in pr["scopes"]:
                raise ApiError(403, "insufficient_scope", f"Ce token n'a pas la permission « {scope} ».")
            if admin and pr["role"] != "admin":
                raise ApiError(403, "forbidden", "Réservé aux administrateurs.")
            return pr
        return dep

    # ------------------------------------------------------------------ helpers
    def views(request: Request, pr: dict) -> list[dict]:
        ids = set(P.accounts.server_ids_for(pr))
        host = request.url.hostname or "localhost"
        out = []
        for o in P.monitor.servers_overview():
            if o["id"] in ids:
                v = P.accounts.public_view(o, host)
                v["loader_version"] = o["loader_version"]
                out.append(v)
        return out

    def one(request: Request, pr: dict, sid: str) -> dict:
        P.accounts.require_access(pr, sid)
        return next(v for v in views(request, pr) if v["id"] == sid)

    # ------------------------------------------------------------------ routes
    @api.get("/ping", summary="Test de disponibilité (sans authentification)", tags=["Application"])
    def ping():
        return ok({"status": "ok"})

    @api.get("/me", summary="Compte et permissions du token courant", tags=["Application"])
    def me(pr: dict = Depends(authenticate)):
        return ok({"username": pr["username"], "role": pr["role"], "scopes": sorted(pr["scopes"]) if pr["scopes"] is not None else sorted(SCOPES),
                   "token": pr["token_id"] is not None})

    @api.get("/status", summary="État global de l'application", tags=["Application"])
    def status(request: Request, pr: dict = Depends(need("servers:read"))):
        v = views(request, pr)
        return ok({"version": __version__, "servers": len(v), "running": sum(1 for x in v if x["state"] == "running"),
                   "starting": sum(1 for x in v if x["state"] == "starting"), "players": sum(x["players"] or 0 for x in v)})

    @api.get("/servers", summary="Liste des serveurs accessibles", tags=["Serveurs"], response_model=ServerListEnvelope,
             responses={401: {"model": ErrorEnvelope}, 403: {"model": ErrorEnvelope}, 429: {"model": ErrorEnvelope}})
    def servers(request: Request, pr: dict = Depends(need("servers:read"))):
        v = views(request, pr)
        return ok(v, count=len(v))

    @api.get("/servers/{server_id}", summary="Détail d'un serveur", tags=["Serveurs"], response_model=ServerEnvelope,
             responses={404: {"model": ErrorEnvelope}})
    def server(server_id: str, request: Request, pr: dict = Depends(need("servers:read"))):
        return ok(one(request, pr, server_id))

    @api.get("/servers/{server_id}/status", summary="Statut d'un serveur", tags=["Serveurs"], responses={404: {"model": ErrorEnvelope}})
    def server_status(server_id: str, request: Request, pr: dict = Depends(need("servers:read"))):
        v = one(request, pr, server_id)
        o = next(x for x in P.monitor.servers_overview() if x["id"] == server_id)
        return ok({"id": v["id"], "name": v["name"], "state": v["state"], "uptime": o["uptime"], "mc_version": v["mc_version"], "address": v["address"]})

    @api.get("/servers/{server_id}/players", summary="Joueurs connectés", tags=["Serveurs"], response_model=PlayersEnvelope,
             responses={404: {"model": ErrorEnvelope}})
    def players(server_id: str, request: Request, pr: dict = Depends(need("players:read"))):
        P.accounts.require_access(pr, server_id)
        srv = P.manager.server(server_id)
        if P.manager.state(srv) != "running":
            return ok({"online": 0, "max": None, "names": [], "names_complete": True}, state=P.manager.state(srv))
        st = mc_status(srv["port"])
        if not st:
            return ok({"online": None, "max": None, "names": [], "names_complete": False}, state="unreachable")
        return ok({"online": st["online"], "max": st["max"], "names": st["names"], "names_complete": len(st["names"]) >= st["online"]})

    @api.post("/servers/{server_id}/start", summary="Démarrer un serveur autorisé", tags=["Serveurs"], status_code=202,
              responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}, 429: {"model": ErrorEnvelope}})
    def start(server_id: str, request: Request, pr: dict = Depends(need("servers:start"))):
        wait = P.limiter.hit(f"v1start:{pr['token_id'] or pr['user_id']}", 6, 60)
        if wait:
            raise ApiError(429, "rate_limited", "Trop de demandes de démarrage.", wait)
        ip = request.client.host if request.client else None
        r = P.accounts.request_start(pr, server_id, ip, via="api" + (f" (token {pr['token_id'][:6]})" if pr["token_id"] else ""))
        return ok({"id": r["id"], "name": r["name"], "state": "starting"})

    @api.get("/system", summary="Statistiques système (administrateur)", tags=["Système"],
             responses={403: {"model": ErrorEnvelope}})
    def system(pr: dict = Depends(need("system:read", admin=True))):
        return ok(P.supervision.overview())

    @api.get("/events", summary="Événements récents (démarrage, arrêt, plantage)", tags=["Événements"], response_model=EventsEnvelope)
    def events(request: Request, since: int = Query(0, ge=0, description="Retourne les événements dont l'id est supérieur"),
               limit: int = Query(50, ge=1, le=500), type: str | None = Query(None, description="Filtrer par type"),
               pr: dict = Depends(need("events:read"))):
        ids = None if pr["role"] == "admin" else P.accounts.server_ids_for(pr)
        ev = P.events.list(since, limit, [type] if type else None, ids)
        return ok(ev, count=len(ev), last_id=ev[-1]["id"] if ev else since)

    # ------------------------------------------------------------------ WebSocket temps réel
    @api.websocket("/ws")
    async def stream(ws: WebSocket):
        """Authentification : en-tête Authorization: Bearer … ou, premier message, {"type":"auth","token":"mcp_…"} (10 s)."""
        ip = ws.client.host if ws.client else "?"
        origin = ws.headers.get("origin")
        if origin and origin.split("://", 1)[-1] != ws.headers.get("host"):
            await ws.close(code=4403)
            return
        await ws.accept()
        pr = None
        plain = ""                                           # token présenté (en-tête ou premier message) : revérifié pendant la connexion
        h = ws.headers.get("authorization", "")
        if h.lower().startswith("bearer "):
            plain = h[7:].strip()
            pr = P.tokens.authenticate(plain, ip)
        elif ws.cookies.get(COOKIE):
            s = P.sessions.get(ws.cookies.get(COOKIE))
            pr = principal_from_session(s) if s else None
        else:
            try:
                msg = json.loads(await asyncio.wait_for(ws.receive_text(), 10))
                if msg.get("type") == "auth":
                    plain = str(msg.get("token", ""))
                    pr = P.tokens.authenticate(plain, ip)
            except (asyncio.TimeoutError, ValueError, WebSocketDisconnect):
                pr = None
        if not pr or P.limiter.hit(f"v1ws:{ip}", 10, 60):
            await ws.send_text(json.dumps({"type": "error", "code": "invalid_token"}))
            await ws.close(code=4401)
            return
        scopes = pr["scopes"]
        can = lambda sc: scopes is None or sc in scopes   # noqa: E731
        host = (ws.headers.get("host") or "localhost").rsplit(":", 1)[0]
        last_ev = P.db.one("SELECT COALESCE(MAX(id),0) AS m FROM events")["m"]
        last_sent = None
        tick = 0
        await ws.send_text(json.dumps({"type": "ready", "scopes": sorted(scopes) if scopes is not None else sorted(SCOPES)}))
        try:
            while True:
                tick += 1
                if tick % 3 == 0:                          # token révoqué / expiré / compte désactivé, ou session fermée : on coupe
                    still = P.tokens.authenticate(plain, ip) if plain else P.sessions.get(ws.cookies.get(COOKIE))
                    if not still:
                        await ws.send_text(json.dumps({"type": "error", "code": "invalid_token"}))
                        await ws.close(code=4401)
                        return
                ids = set(P.accounts.server_ids_for(pr))
                if can("servers:read"):
                    cur = [P.accounts.public_view(o, host) for o in (P.monitor.snapshot.get("servers") or P.monitor.servers_overview()) if o["id"] in ids]
                    if cur != last_sent:
                        last_sent = cur
                        await ws.send_text(json.dumps({"type": "servers", "data": cur}, default=str))
                if can("events:read"):
                    for e in P.events.list(last_ev, 100, None, None if pr["role"] == "admin" else list(ids)):
                        last_ev = e["id"]
                        await ws.send_text(json.dumps({"type": "event", "data": e}, default=str))
                    if P.db.one("SELECT COALESCE(MAX(id),0) AS m FROM events")["m"] > last_ev and pr["role"] != "admin":
                        last_ev = P.db.one("SELECT COALESCE(MAX(id),0) AS m FROM events")["m"]
                await asyncio.sleep(1)
        except (WebSocketDisconnect, RuntimeError):
            pass

    return api
