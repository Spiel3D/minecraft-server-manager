"""Liste blanche par serveur (V3 §4).

- Serveur arrêté : le panneau lit/écrit directement ``whitelist.json`` (écriture atomique) et ``server.properties``.
  UUID : mode en ligne → recherche officielle Mojang (jamais inventé) ; mode hors ligne → UUID « OfflinePlayer » calculé.
- Serveur en marche : le panneau envoie les commandes ``whitelist …`` au serveur (qui reste seul maître de ses fichiers)
  puis vérifie le résultat dans ``whitelist.json`` ; sans confirmation, l'erreur est explicite.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid as uuidlib
from pathlib import Path

from .installer import set_properties
from .jobs import UserError
from .process import ProcessError, mc_status
from .sources import SourceError

NAME_RE = re.compile(r"^\.?[A-Za-z0-9_]{1,16}$")      # pseudo Java (le point initial sert aux joueurs Bedrock via Floodgate)
CONFIRM_WAIT = 4.0


def offline_uuid(name: str) -> str:
    h = bytearray(hashlib.md5(("OfflinePlayer:" + name).encode("utf-8")).digest())
    h[6] = (h[6] & 0x0F) | 0x30
    h[8] = (h[8] & 0x3F) | 0x80
    return str(uuidlib.UUID(bytes=bytes(h)))


def _hyphenate(raw: str) -> str:
    raw = raw.replace("-", "").lower()
    if not re.fullmatch(r"[0-9a-f]{32}", raw):
        raise UserError("UUID renvoyé par Mojang invalide.")
    return str(uuidlib.UUID(raw))


def read_props(path: Path) -> dict:
    out = {}
    try:
        for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if ln and not ln.lstrip().startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def _read_json_list(path: Path) -> list:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


class WhitelistService:
    def __init__(self, panel):
        self.P = panel
        self._locks: dict[str, threading.Lock] = {}
        self._glock = threading.Lock()

    def _lock(self, sid: str) -> threading.Lock:
        with self._glock:
            return self._locks.setdefault(sid, threading.Lock())

    def _srv(self, sid: str) -> tuple[dict, Path]:
        srv = self.P.manager.server(sid)
        if srv["status"] != "installed":
            raise UserError("Le serveur n'est pas encore installé.")
        root = Path(srv["path"])
        if not self.P.server_dir_ok(root):
            raise UserError("Dossier du serveur incohérent.")
        return srv, root

    # ------------------------------------------------------------------ lecture
    def status(self, sid: str, query: str = "") -> dict:
        srv, root = self._srv(sid)
        props = read_props(root / "server.properties")
        running = bool(self.P.manager.alive(sid))
        entries = [e for e in _read_json_list(root / "whitelist.json") if isinstance(e, dict) and e.get("name")]
        ops = {str(e.get("name", "")).lower(): e.get("level", 4) for e in _read_json_list(root / "ops.json") if isinstance(e, dict)}
        bans = {str(e.get("name", "")).lower() for e in _read_json_list(root / "banned-players.json") if isinstance(e, dict)}
        seen = {str(e.get("name", "")).lower(): e.get("expiresOn") for e in _read_json_list(root / "usercache.json") if isinstance(e, dict)}
        online = None
        if running:
            st = mc_status(srv["port"], timeout=1.0)
            online = {n.lower() for n in (st or {}).get("names", [])}
        q = (query or "").strip().lower()
        out = []
        for e in sorted(entries, key=lambda x: str(x["name"]).lower()):
            n = str(e["name"])
            if q and q not in n.lower() and q not in str(e.get("uuid", "")).lower():
                continue
            out.append({"name": n, "uuid": e.get("uuid", ""), "op": ops.get(n.lower()), "banned": n.lower() in bans,
                        "online": (n.lower() in online) if online is not None else None, "in_cache": n.lower() in seen})
        return {"enabled": props.get("white-list", "false").lower() == "true",
                "enforce": props.get("enforce-whitelist", "false").lower() == "true",
                "online_mode": props.get("online-mode", "true").lower() != "false",
                "running": running, "total": len(entries), "entries": out,
                "players_online_known": online is not None}

    # ------------------------------------------------------------------ écriture
    def _write_list(self, root: Path, entries: list):
        p = root / "whitelist.json"
        tmp = p.with_name(p.name + ".panel-tmp")
        tmp.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, p)

    def _lookup(self, name: str, online_mode: bool) -> tuple[str, str]:
        if not online_mode:
            return offline_uuid(name), name
        try:
            d = self.P.http.get_json(f"https://api.mojang.com/users/profiles/minecraft/{name}", cache=False)
        except SourceError as e:
            if "HTTP 404" in str(e) or "JSON invalide" in str(e):       # 404 ou 204 sans contenu : pseudo inconnu
                raise UserError(f"Le pseudo « {name} » n'existe pas chez Mojang.") from e
            raise UserError("Impossible de joindre Mojang pour vérifier ce pseudo. Réessayez, ou démarrez le serveur : "
                            "il fera lui-même la vérification.") from e
        if not isinstance(d, dict) or not d.get("id"):
            raise UserError(f"Le pseudo « {name} » n'existe pas chez Mojang.")
        return _hyphenate(str(d["id"])), str(d.get("name") or name)

    def _wait(self, cond) -> bool:
        end = time.time() + CONFIRM_WAIT
        while time.time() < end:
            if cond():
                return True
            time.sleep(0.15)
        return cond()

    def add(self, sid: str, name: str) -> dict:
        name = (name or "").strip()
        if not NAME_RE.match(name):
            raise UserError("Pseudo invalide : 1 à 16 caractères parmi lettres, chiffres et « _ ».")
        srv, root = self._srv(sid)
        with self._lock(sid):
            names = lambda: {str(e.get("name", "")).lower() for e in _read_json_list(root / "whitelist.json") if isinstance(e, dict)}  # noqa: E731
            if name.lower() in names():
                raise UserError(f"« {name} » est déjà sur la liste blanche.")
            if self.P.manager.alive(sid):
                try:
                    self.P.manager.send_command(sid, f"whitelist add {name}")
                except ProcessError as e:
                    raise UserError(str(e)) from e
                if not self._wait(lambda: name.lower() in names()):
                    raise UserError(f"Le serveur n'a pas confirmé l'ajout de « {name} » (pseudo inexistant ou refusé) : consultez la console.")
                return {"added": name, "via": "console"}
            props = read_props(root / "server.properties")
            uid, canon = self._lookup(name, props.get("online-mode", "true").lower() != "false")
            entries = _read_json_list(root / "whitelist.json")
            entries.append({"uuid": uid, "name": canon})
            self._write_list(root, entries)
            return {"added": canon, "via": "fichier", "uuid": uid}

    def remove(self, sid: str, name: str) -> dict:
        name = (name or "").strip()
        if not NAME_RE.match(name):
            raise UserError("Pseudo invalide.")
        srv, root = self._srv(sid)
        with self._lock(sid):
            entries = _read_json_list(root / "whitelist.json")
            if name.lower() not in {str(e.get("name", "")).lower() for e in entries if isinstance(e, dict)}:
                raise UserError(f"« {name} » n'est pas sur la liste blanche.")
            if self.P.manager.alive(sid):
                try:
                    self.P.manager.send_command(sid, f"whitelist remove {name}")
                except ProcessError as e:
                    raise UserError(str(e)) from e
                gone = lambda: name.lower() not in {str(e.get("name", "")).lower() for e in _read_json_list(root / "whitelist.json") if isinstance(e, dict)}  # noqa: E731
                if not self._wait(gone):
                    raise UserError("Le serveur n'a pas confirmé le retrait : consultez la console.")
                return {"removed": name, "via": "console"}
            kept = [e for e in entries if not (isinstance(e, dict) and str(e.get("name", "")).lower() == name.lower())]
            self._write_list(root, kept)
            return {"removed": name, "via": "fichier"}

    def set_enabled(self, sid: str, enabled: bool) -> dict:
        srv, root = self._srv(sid)
        with self._lock(sid):
            prop = lambda: read_props(root / "server.properties").get("white-list", "false").lower() == "true"  # noqa: E731
            if self.P.manager.alive(sid):
                try:
                    self.P.manager.send_command(sid, "whitelist on" if enabled else "whitelist off")
                except ProcessError as e:
                    raise UserError(str(e)) from e
                if not self._wait(lambda: prop() == enabled):
                    raise UserError("Le serveur n'a pas confirmé le changement : consultez la console.")
                return {"enabled": enabled, "via": "console"}
            set_properties(root / "server.properties", {"white-list": "true" if enabled else "false"})
            return {"enabled": enabled, "via": "fichier"}
