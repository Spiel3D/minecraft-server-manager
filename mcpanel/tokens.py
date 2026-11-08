"""Tokens d'API : générés une seule fois, stockés hachés, limités par des permissions précises, révocables."""
from __future__ import annotations

import hashlib
import json
import secrets
import time

from .errors import AppError, Forbidden, NotFound
from .util import new_id, now

SCOPES = {
    "servers:read": "Voir les serveurs attribués et leur statut",
    "players:read": "Voir les joueurs connectés",
    "servers:start": "Démarrer les serveurs attribués",
    "events:read": "Lire les événements (démarrage, arrêt, plantage)",
    "system:read": "Lire les statistiques système (administrateur uniquement)",
}
ADMIN_ONLY_SCOPES = {"system:read"}
MAX_TOKENS_PER_USER = 20


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class TokenService:
    def __init__(self, panel):
        self.P, self.db = panel, panel.db
        self._touch: dict[str, float] = {}

    def create(self, user: dict, name: str, scopes: list[str], expires_days: int | None = None) -> tuple[dict, str]:
        name = (name or "").strip()
        if not 1 <= len(name) <= 60:
            raise AppError("Donnez un nom au token (60 caractères maximum).")
        scopes = list(dict.fromkeys(scopes or []))
        if not scopes or any(s not in SCOPES for s in scopes):
            raise AppError("Permissions invalides : " + ", ".join(sorted(SCOPES)))
        if user["role"] != "admin" and set(scopes) & ADMIN_ONLY_SCOPES:
            raise Forbidden("Cette permission est réservée aux administrateurs.")
        if self.db.one("SELECT COUNT(*) AS n FROM api_tokens WHERE user_id=? AND revoked_at IS NULL", (user["id"],))["n"] >= MAX_TOKENS_PER_USER:
            raise AppError("Trop de tokens actifs pour ce compte (20 maximum) : révoquez-en.")
        exp = None
        if expires_days:
            if not 1 <= int(expires_days) <= 3650:
                raise AppError("Durée de validité invalide.")
            exp = time.time() + int(expires_days) * 86400
        plain = "mcp_" + secrets.token_urlsafe(32)
        tid = new_id()
        self.db.execute("INSERT INTO api_tokens(id,user_id,name,token_hash,prefix,scopes,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?)",
                        (tid, user["id"], name, _h(plain), plain[:10], json.dumps(scopes), now(), exp))
        return self._view(self.db.one("SELECT t.*, u.username FROM api_tokens t JOIN users u ON u.id=t.user_id WHERE t.id=?", (tid,))), plain

    @staticmethod
    def _view(t: dict) -> dict:
        return {"id": t["id"], "name": t["name"], "prefix": t["prefix"], "scopes": t["scopes"], "user_id": t["user_id"],
                "username": t.get("username"), "created_at": t["created_at"], "expires_at": t["expires_at"],
                "last_used": t["last_used"], "last_ip": t["last_ip"], "revoked": t["revoked_at"] is not None,
                "expired": bool(t["expires_at"] and t["expires_at"] < time.time())}

    def list(self, user_id: int | None = None) -> list[dict]:
        sql = "SELECT t.*, u.username FROM api_tokens t JOIN users u ON u.id=t.user_id"
        rows = self.db.query(sql + (" WHERE t.user_id=?" if user_id else "") + " ORDER BY t.created_at DESC", (user_id,) if user_id else ())
        return [self._view(r) for r in rows]

    def revoke(self, tid: str, principal: dict):
        t = self.db.one("SELECT * FROM api_tokens WHERE id=?", (tid,))
        if not t or (principal["role"] != "admin" and t["user_id"] != principal["user_id"]):
            raise NotFound("Token introuvable.")
        self.db.execute("UPDATE api_tokens SET revoked_at=? WHERE id=? AND revoked_at IS NULL", (now(), tid))

    def authenticate(self, plain: str, ip: str | None = None) -> dict | None:
        if not plain or not plain.startswith("mcp_") or len(plain) > 100:
            return None
        row = self.db.one("SELECT t.*, u.username, u.role, u.active FROM api_tokens t JOIN users u ON u.id=t.user_id WHERE t.token_hash=?", (_h(plain),))
        if not row or row["revoked_at"] is not None or not row["active"]:
            return None
        if row["expires_at"] and row["expires_at"] < time.time():
            return None
        if time.time() - self._touch.get(row["id"], 0) > 60:            # last_used mis à jour au plus une fois par minute
            self._touch[row["id"]] = time.time()
            self.db.execute("UPDATE api_tokens SET last_used=?, last_ip=? WHERE id=?", (now(), ip, row["id"]))
        return {"user_id": row["user_id"], "username": row["username"], "role": row["role"], "token_id": row["id"], "scopes": set(row["scopes"])}
