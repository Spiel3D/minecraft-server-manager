"""Comptes, rôles (admin / user), attribution des serveurs et règles de démarrage par les utilisateurs classiques."""
from __future__ import annotations

import re
import time

from .errors import AppError, Conflict, Forbidden, NotFound, Throttled
from .security import check_password_strength, hash_password
from .util import now

ROLES = ("admin", "user")
USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,31}$")
DEFAULT_COOLDOWN_S = 180
DEFAULT_MAX_STARTS_PER_HOUR = 6


def principal_from_session(s: dict) -> dict:
    return {"user_id": s["user_id"], "username": s["username"], "role": s["role"], "token_id": None, "scopes": None}


class AccountService:
    def __init__(self, panel):
        self.P = panel
        self.db = panel.db

    # ------------------------------------------------------------ lecture
    def _public(self, u: dict) -> dict:
        servers = [r["server_id"] for r in self.db.query("SELECT server_id FROM user_servers WHERE user_id=?", (u["id"],))]
        return {"id": u["id"], "username": u["username"], "display_name": u["display_name"] or u["username"], "role": u["role"],
                "active": bool(u["active"]), "created_at": u["created_at"], "last_login": u["last_login"], "servers": servers}

    def list_users(self) -> list[dict]:
        return [self._public(u) for u in self.db.query("SELECT * FROM users ORDER BY role, username COLLATE NOCASE")]

    def get(self, uid: int) -> dict:
        u = self.db.one("SELECT * FROM users WHERE id=?", (uid,))
        if not u:
            raise NotFound("Compte introuvable.")
        return u

    def server_ids_for(self, principal: dict) -> list[str]:
        if principal["role"] == "admin":
            return [r["id"] for r in self.db.query("SELECT id FROM servers ORDER BY name COLLATE NOCASE")]
        return [r["server_id"] for r in self.db.query("SELECT server_id FROM user_servers WHERE user_id=?", (principal["user_id"],))]

    def can_access(self, principal: dict, sid: str) -> bool:
        if principal["role"] == "admin":
            return bool(self.db.one("SELECT 1 FROM servers WHERE id=?", (sid,)))
        return bool(self.db.one("SELECT 1 FROM user_servers WHERE user_id=? AND server_id=?", (principal["user_id"], sid)))

    def require_access(self, principal: dict, sid: str):
        if not self.can_access(principal, sid):
            raise NotFound("Serveur introuvable.")           # 404 volontaire : on ne révèle pas l'existence d'un serveur non attribué

    # ------------------------------------------------------------ écriture
    def _check_servers(self, sids) -> list[str]:
        out = []
        for sid in dict.fromkeys(sids or []):
            if not self.db.one("SELECT 1 FROM servers WHERE id=?", (sid,)):
                raise AppError(f"Serveur inconnu : {sid}")
            out.append(sid)
        return out

    def _active_admins(self, excluding: int | None = None) -> int:
        return self.db.one("SELECT COUNT(*) AS n FROM users WHERE role='admin' AND active=1 AND id<>?", (excluding or -1,))["n"]

    def _set_servers(self, uid: int, sids: list[str]):
        self.db.execute("DELETE FROM user_servers WHERE user_id=?", (uid,))
        for sid in sids:
            self.db.execute("INSERT INTO user_servers(user_id,server_id) VALUES(?,?)", (uid, sid))

    def create(self, username: str, password: str, role: str = "user", display_name: str = "", servers=None) -> dict:
        username = (username or "").strip()
        if not USERNAME_RE.match(username):
            raise AppError("Identifiant invalide : 2 à 32 caractères (lettres, chiffres, « . », « _ », « - »).")
        if role not in ROLES:
            raise AppError("Rôle invalide.")
        if self.db.one("SELECT 1 FROM users WHERE username=? COLLATE NOCASE", (username,)):
            raise Conflict("Cet identifiant existe déjà.")
        from .security import SecurityError
        try:
            check_password_strength(password, username, self.P.s.min_password_len)
        except SecurityError as e:
            raise AppError(str(e)) from e
        sids = self._check_servers(servers)
        uid = self.db.execute("INSERT INTO users(username,pw_hash,created_at,role,active,display_name) VALUES(?,?,?,?,1,?)",
                              (username, hash_password(password), now(), role, (display_name or "")[:60]))
        self._set_servers(uid, sids if role == "user" else [])
        return self._public(self.get(uid))

    def update(self, uid: int, data: dict, acting_uid: int | None = None) -> dict:
        u = self.get(uid)
        sets, vals = [], []
        revoke_sessions = False
        if "display_name" in data:
            sets.append("display_name=?"); vals.append(str(data["display_name"] or "")[:60])
        if "role" in data and data["role"] != u["role"]:
            if data["role"] not in ROLES:
                raise AppError("Rôle invalide.")
            if u["role"] == "admin" and u["active"] and self._active_admins(excluding=uid) == 0:
                raise Conflict("Impossible : il doit rester au moins un administrateur actif.")
            if uid == acting_uid:
                raise Conflict("Vous ne pouvez pas modifier votre propre rôle.")
            sets.append("role=?"); vals.append(data["role"]); revoke_sessions = True
        if "active" in data and bool(data["active"]) != bool(u["active"]):
            if not data["active"]:
                if uid == acting_uid:
                    raise Conflict("Vous ne pouvez pas désactiver votre propre compte.")
                if u["role"] == "admin" and self._active_admins(excluding=uid) == 0:
                    raise Conflict("Impossible : il doit rester au moins un administrateur actif.")
                revoke_sessions = True
            sets.append("active=?"); vals.append(1 if data["active"] else 0)
        if data.get("password"):
            from .security import SecurityError
            try:
                check_password_strength(str(data["password"]), u["username"], self.P.s.min_password_len)
            except SecurityError as e:
                raise AppError(str(e)) from e
            sets.append("pw_hash=?"); vals.append(hash_password(str(data["password"]))); revoke_sessions = True
        if sets:
            self.db.execute(f"UPDATE users SET {', '.join(sets)} WHERE id=?", (*vals, uid))
        if "servers" in data:
            self._set_servers(uid, self._check_servers(data["servers"]) if (data.get("role") or u["role"]) == "user" else [])
        if revoke_sessions:
            self.P.sessions.delete_user(uid)
        return self._public(self.get(uid))

    def delete(self, uid: int, acting_uid: int | None = None):
        u = self.get(uid)
        if uid == acting_uid:
            raise Conflict("Vous ne pouvez pas supprimer votre propre compte.")
        if u["role"] == "admin" and u["active"] and self._active_admins(excluding=uid) == 0:
            raise Conflict("Impossible : il doit rester au moins un administrateur actif.")
        self.db.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        self.db.execute("DELETE FROM users WHERE id=?", (uid,))          # tokens et attributions supprimés en cascade

    # ------------------------------------------------------------ vue publique et démarrage
    @staticmethod
    def public_view(o: dict, host: str) -> dict:
        """Ce qu'un utilisateur classique a le droit de voir d'un serveur (rien de technique ni d'administratif)."""
        return {"id": o["id"], "name": o["name"], "description": o["description"], "mc_version": o["mc_version"],
                "server_type": o["server_type"], "state": o["state"], "players": o["players"], "max_players": o["max_players"],
                "port": o["port"], "address": f"{host}:{o['port']}"}

    def limits(self) -> dict:
        def num(key, default):
            try:
                return int(self.db.get_setting(key) or default)
            except ValueError:
                return default
        return {"start_cooldown_s": num("start_cooldown_s", DEFAULT_COOLDOWN_S),
                "max_starts_per_hour": num("max_starts_per_hour", DEFAULT_MAX_STARTS_PER_HOUR)}

    def request_start(self, principal: dict, sid: str, ip: str | None = None, via: str = "ui") -> dict:
        """Démarrage demandé par un administrateur ou un utilisateur autorisé (jamais d'arrêt ni de redémarrage ici)."""
        self.require_access(principal, sid)
        srv = self.P.manager.server(sid)
        if self.P.manager.alive(sid):
            raise Conflict("Ce serveur est déjà en marche ou en cours de démarrage.", code="already_running")
        if principal["role"] != "admin":
            lim = self.limits()
            last = self.db.one("SELECT MAX(ts) AS t FROM start_log WHERE server_id=?", (sid,))["t"] or 0
            wait = int(lim["start_cooldown_s"] - (time.time() - last))
            if wait > 0:
                raise Throttled(f"Ce serveur vient d'être démarré : réessayez dans {wait} s.", retry_after=wait)
            n = self.db.one("SELECT COUNT(*) AS n FROM start_log WHERE user_id=? AND ts>?", (principal["user_id"], time.time() - 3600))["n"]
            if n >= lim["max_starts_per_hour"]:
                raise Throttled("Limite de démarrages atteinte pour cette heure.", retry_after=600)
        from .process import ProcessError
        try:
            r = self.P.start_server(sid)
        except ProcessError as e:
            raise Conflict(str(e), code="cannot_start") from e
        self.db.execute("INSERT INTO start_log(ts,user_id,server_id) VALUES(?,?,?)", (now(), principal["user_id"], sid))
        self.P.audit(principal["username"], ip, "server_start", self.P.label(srv), f"via {via}")
        return {"id": sid, "name": self.P.label(srv)}
