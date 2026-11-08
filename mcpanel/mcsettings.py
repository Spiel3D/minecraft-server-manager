"""Éditeur complet de server.properties (V3 §5).

- Schéma déclaratif : catégorie, type, valeur par défaut, bornes, description, redémarrage requis, version minimale.
- Lecture/écriture fidèles : commentaires, ordre et propriétés inconnues conservés ; échappements Java (\\uXXXX, \\\\, \\:) gérés.
- Écriture atomique, protégée contre une modification concurrente (empreinte) et historisée (retour arrière possible).
- Validations bloquantes (erreurs) et avertissements (incohérences) ; mode hors ligne = confirmation explicite.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from pathlib import Path

from .jobs import UserError
from .util import now

CATEGORIES = [
    ("general", "Général"), ("game", "Jeu"), ("world", "Monde"), ("perf", "Performances"),
    ("network", "Réseau"), ("security", "Sécurité et accès"), ("resource", "Pack de ressources"), ("advanced", "Avancé et administration"),
]


def F(key, cat, typ, default, desc, *, label=None, lo=None, hi=None, options=None, restart=True, min_mc=None, secret=False, managed=None, maxlen=200, new_world=False):
    return {"key": key, "category": cat, "type": typ, "default": default, "description": desc, "label": label or key,
            "min": lo, "max": hi, "options": options, "restart": restart, "min_mc": min_mc, "secret": secret, "managed": managed,
            "maxlen": maxlen, "new_world": new_world}


SCHEMA: list[dict] = [
    # ---- général
    F("motd", "general", "string", "A Minecraft Server", "Message affiché dans la liste des serveurs (codes couleur « § » acceptés).", label="Message du jour (MOTD)", maxlen=256),
    F("max-players", "general", "int", "20", "Nombre maximal de joueurs connectés en même temps.", label="Joueurs maximum", lo=0, hi=2147483647),
    F("server-port", "general", "int", "25565", "Port du serveur : géré par le panneau (onglet Configuration) pour éviter les conflits entre serveurs.", label="Port", lo=1024, hi=65535, managed="config"),
    F("server-ip", "general", "string", "", "Adresse réseau à laquelle le serveur se lie. Laissez vide, sauf besoin précis.", label="Adresse de liaison", maxlen=64),
    F("enable-status", "general", "bool", "true", "Répondre aux requêtes de la liste des serveurs (si désactivé, le serveur apparaît « hors ligne »).", label="Apparaître dans la liste"),
    F("hide-online-players", "general", "bool", "false", "Masquer la liste des joueurs connectés dans la liste des serveurs.", label="Masquer les joueurs en ligne"),
    # ---- jeu
    F("gamemode", "game", "enum", "survival", "Mode de jeu des nouveaux joueurs.", label="Mode de jeu", options=["survival", "creative", "adventure", "spectator"]),
    F("force-gamemode", "game", "bool", "false", "Imposer le mode de jeu à chaque connexion.", label="Forcer le mode de jeu"),
    F("difficulty", "game", "enum", "easy", "Difficulté du monde.", label="Difficulté", options=["peaceful", "easy", "normal", "hard"]),
    F("hardcore", "game", "bool", "false", "Mode hardcore : un joueur mort est banni (difficulté forcée à « hard »).", label="Hardcore"),
    F("pvp", "game", "bool", "true", "Autoriser les combats entre joueurs.", label="Combat entre joueurs (PvP)"),
    F("allow-flight", "game", "bool", "false", "Autoriser le vol en survie (nécessaire pour certains mods ; sinon expulsion pour « vol »).", label="Autoriser le vol"),
    F("allow-nether", "game", "bool", "true", "Activer le Nether.", label="Nether"),
    F("spawn-monsters", "game", "bool", "true", "Faire apparaître des monstres.", label="Monstres"),
    F("spawn-animals", "game", "bool", "true", "Faire apparaître des animaux.", label="Animaux"),
    F("spawn-npcs", "game", "bool", "true", "Faire apparaître des villageois.", label="Villageois"),
    F("spawn-protection", "game", "int", "16", "Rayon (en blocs) autour du point d'apparition que seuls les opérateurs peuvent modifier. 0 désactive.", label="Protection du spawn", lo=0, hi=29999984),
    F("player-idle-timeout", "game", "int", "0", "Minutes d'inactivité avant expulsion. 0 = jamais.", label="Délai d'inactivité (min)", lo=0, hi=1440, restart=False),
    F("op-permission-level", "game", "int", "4", "Niveau de permission des opérateurs (1 à 4).", label="Niveau des opérateurs", lo=1, hi=4),
    F("function-permission-level", "game", "int", "2", "Niveau de permission des fonctions de datapack (1 à 4).", label="Niveau des fonctions", lo=1, hi=4),
    # ---- monde
    F("level-name", "world", "string", "world", "Nom du dossier du monde. Le changer crée (ou ouvre) un AUTRE monde : l'ancien n'est pas supprimé.", label="Nom du monde", maxlen=64),
    F("level-seed", "world", "string", "", "Graine de génération. Ne s'applique qu'à un monde qui n'existe pas encore.", label="Graine", maxlen=64, new_world=True),
    F("level-type", "world", "string", "minecraft:normal", "Type de monde (normal, flat, large_biomes, amplified…). Ne s'applique qu'à un nouveau monde.", label="Type de monde", maxlen=64, new_world=True),
    F("generate-structures", "world", "bool", "true", "Générer villages, forteresses, etc. (nouveau monde).", label="Générer les structures", new_world=True),
    F("generator-settings", "world", "string", "{}", "Réglages du générateur (JSON, pour les mondes plats ou personnalisés).", label="Réglages du générateur", maxlen=1000, new_world=True),
    F("max-world-size", "world", "int", "29999984", "Rayon maximal du monde en blocs.", label="Taille maximale du monde", lo=1, hi=29999984),
    # ---- performances
    F("view-distance", "perf", "int", "10", "Distance d'affichage en chunks (2 à 32). Plus grand = plus de RAM et de CPU.", label="Distance d'affichage", lo=2, hi=32),
    F("simulation-distance", "perf", "int", "10", "Distance de simulation en chunks (3 à 32). Plus grand = plus de CPU.", label="Distance de simulation", lo=3, hi=32),
    F("max-tick-time", "perf", "int", "60000", "Millisecondes avant que le chien de garde arrête un serveur bloqué. -1 désactive.", label="Délai du chien de garde (ms)", lo=-1, hi=2147483647),
    F("entity-broadcast-range-percentage", "perf", "int", "100", "Portée d'envoi des entités aux joueurs, en pourcentage (10 à 1000).", label="Portée des entités (%)", lo=10, hi=1000),
    F("sync-chunk-writes", "perf", "bool", "true", "Écriture synchrone des chunks (plus sûr en cas de coupure, un peu plus lent).", label="Écriture synchrone"),
    F("max-chained-neighbor-updates", "perf", "int", "1000000", "Limite des mises à jour de blocs en chaîne. Négatif = illimité.", label="Mises à jour en chaîne", lo=-2147483648, hi=2147483647, min_mc="1.19.4"),
    F("pause-when-empty-seconds", "perf", "int", "60", "Met le serveur en pause après N secondes sans joueur. -1 désactive.", label="Pause sans joueur (s)", lo=-1, hi=2147483647, min_mc="1.21.2"),
    # ---- réseau
    F("network-compression-threshold", "network", "int", "256", "Taille (octets) à partir de laquelle les paquets sont compressés. -1 désactive la compression.", label="Seuil de compression", lo=-1, hi=2147483647),
    F("rate-limit", "network", "int", "0", "Paquets maximum par seconde et par joueur avant expulsion. 0 = illimité.", label="Limite de paquets", lo=0, hi=2147483647),
    F("use-native-transport", "network", "bool", "true", "Optimiser le réseau sous Linux (epoll).", label="Transport natif Linux"),
    F("accepts-transfers", "network", "bool", "false", "Accepter les transferts depuis un autre serveur.", label="Accepter les transferts", min_mc="1.20.5"),
    F("prevent-proxy-connections", "network", "bool", "false", "Refuser les connexions via proxy/VPN connus (vérification auprès de Mojang).", label="Bloquer les proxys"),
    F("enable-query", "network", "bool", "false", "Activer le protocole de requête GameSpy4.", label="Protocole de requête (query)"),
    F("query.port", "network", "int", "25565", "Port du protocole de requête.", label="Port query", lo=1024, hi=65535),
    # ---- sécurité
    F("online-mode", "security", "bool", "true", "Vérifier les comptes auprès de Mojang. DÉSACTIVER permet à n'importe qui d'usurper n'importe quel pseudo (y compris un opérateur).", label="Authentification Mojang (online-mode)"),
    F("white-list", "security", "bool", "false", "Whitelist : se gère depuis l'onglet Whitelist (la commande est envoyée au serveur en marche).", label="Whitelist activée", managed="whitelist"),
    F("enforce-whitelist", "security", "bool", "false", "Expulser immédiatement les joueurs retirés de la whitelist.", label="Appliquer la whitelist en direct"),
    F("enforce-secure-profile", "security", "bool", "true", "Exiger des messages de chat signés par un compte Mojang.", label="Profil sécurisé obligatoire", min_mc="1.19"),
    F("log-ips", "security", "bool", "true", "Écrire les adresses IP des joueurs dans les journaux.", label="Journaliser les IP"),
    # ---- pack de ressources
    F("resource-pack", "resource", "string", "", "Adresse (URL) du pack de ressources proposé aux joueurs.", label="URL du pack", maxlen=500),
    F("resource-pack-sha1", "resource", "string", "", "Empreinte SHA-1 (40 caractères hexadécimaux) du pack, pour vérifier son intégrité.", label="Empreinte SHA-1", maxlen=40),
    F("resource-pack-id", "resource", "string", "", "Identifiant UUID du pack.", label="Identifiant du pack", maxlen=40, min_mc="1.20.3"),
    F("resource-pack-prompt", "resource", "string", "", "Message affiché avec la demande d'installation.", label="Message de demande", maxlen=500),
    F("require-resource-pack", "resource", "bool", "false", "Expulser les joueurs qui refusent le pack.", label="Pack obligatoire"),
    # ---- avancé
    F("enable-rcon", "advanced", "bool", "false", "Console à distance RCON. Désactivée par le panneau : n'activez que si un outil externe en a besoin, avec un mot de passe fort.", label="RCON"),
    F("rcon.port", "advanced", "int", "25575", "Port RCON.", label="Port RCON", lo=1024, hi=65535),
    F("rcon.password", "advanced", "string", "", "Mot de passe RCON (jamais affiché une fois enregistré).", label="Mot de passe RCON", secret=True, maxlen=128),
    F("broadcast-console-to-ops", "advanced", "bool", "true", "Montrer aux opérateurs les commandes tapées dans la console.", label="Diffuser la console aux opérateurs"),
    F("broadcast-rcon-to-ops", "advanced", "bool", "true", "Montrer aux opérateurs les commandes envoyées par RCON.", label="Diffuser RCON aux opérateurs"),
    F("enable-jmx-monitoring", "advanced", "bool", "false", "Exposer des métriques JMX (usage avancé).", label="Surveillance JMX"),
    F("initial-enabled-packs", "advanced", "string", "vanilla", "Datapacks activés à la création du monde.", label="Datapacks activés", maxlen=300, new_world=True),
    F("initial-disabled-packs", "advanced", "string", "", "Datapacks désactivés à la création du monde.", label="Datapacks désactivés", maxlen=300, new_world=True),
    F("region-file-compression", "advanced", "enum", "deflate", "Compression des fichiers de région.", label="Compression des régions", options=["deflate", "lz4", "none"], min_mc="1.20.5"),
    F("text-filtering-config", "advanced", "string", "", "Fichier de configuration du filtrage de texte.", label="Filtrage du texte", maxlen=200),
    F("debug", "advanced", "bool", "false", "Journalisation de débogage.", label="Mode débogage"),
]
BY_KEY = {f["key"]: f for f in SCHEMA}
KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_PROP_ESC = {"n": "\n", "t": "\t", "r": "\r", "f": "\f"}


def _vt(v: str):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


def available(field: dict, mc: str) -> bool:
    return not field["min_mc"] or not mc or _vt(mc) >= _vt(field["min_mc"])


def unescape(s: str) -> str:
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            n = s[i + 1]
            if n == "u" and re.fullmatch(r"[0-9a-fA-F]{4}", s[i + 2:i + 6] or ""):
                out.append(chr(int(s[i + 2:i + 6], 16)))
                i += 6
                continue
            out.append(_PROP_ESC.get(n, n))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def escape(v: str) -> str:
    out = []
    for i, c in enumerate(v):
        if c == "\\":
            out.append("\\\\")
        elif c == ":" or c == "=" or c in "#!":
            out.append("\\" + c)
        elif c == " " and i == 0:
            out.append("\\ ")
        elif ord(c) > 126:
            for u in c.encode("utf-16-be").hex(" ", 2).split():
                out.append("\\u" + u)
        elif ord(c) < 32:
            raise UserError("Les retours à la ligne et caractères de contrôle ne sont pas autorisés.")
        else:
            out.append(c)
    return "".join(out)


class PropsFile:
    """server.properties lu sans rien perdre : lignes brutes conservées, valeurs décodées à part."""

    def __init__(self, text: str):
        self.lines = text.splitlines()
        self.index: dict[str, int] = {}
        self.values: dict[str, str] = {}
        for i, ln in enumerate(self.lines):
            s = ln.lstrip()
            if not s or s[0] in "#!" or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            k = unescape(k.strip())
            self.index[k] = i
            self.values[k] = unescape(v.lstrip())

    def set(self, key: str, value: str):
        line = f"{key}={escape(value)}"
        if key in self.index:
            self.lines[self.index[key]] = line
        else:
            self.index[key] = len(self.lines)
            self.lines.append(line)
        self.values[key] = value

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


def validate_value(f: dict, raw) -> str:
    v = "true" if raw is True else "false" if raw is False else str(raw if raw is not None else "")
    if any(ord(c) < 32 for c in v):
        raise UserError(f"« {f['label']} » : retours à la ligne et caractères de contrôle interdits.")
    if len(v) > f["maxlen"]:
        raise UserError(f"« {f['label']} » : {f['maxlen']} caractères maximum.")
    t = f["type"]
    if t == "bool":
        if v.lower() not in ("true", "false"):
            raise UserError(f"« {f['label']} » : oui ou non attendu.")
        return v.lower()
    if t == "int":
        if not re.fullmatch(r"-?\d{1,12}", v.strip()):
            raise UserError(f"« {f['label']} » : nombre entier attendu.")
        n = int(v)
        if (f["min"] is not None and n < f["min"]) or (f["max"] is not None and n > f["max"]):
            raise UserError(f"« {f['label']} » : valeur entre {f['min']} et {f['max']}.")
        return str(n)
    if t == "enum":
        if v not in f["options"]:
            raise UserError(f"« {f['label']} » : choisissez parmi {', '.join(f['options'])}.")
        return v
    return v


def cross_check(values: dict, own_port: int, other_ports: set[int]) -> tuple[list[str], list[str]]:
    """Retourne (erreurs bloquantes, avertissements) sur l'ensemble des valeurs finales."""
    g = lambda k: values.get(k, BY_KEY[k]["default"] if k in BY_KEY else "")  # noqa: E731
    err, warn = [], []
    if g("enable-rcon") == "true" and len(g("rcon.password")) < 8:
        err.append("RCON activé sans mot de passe d'au moins 8 caractères : refusé (risque d'accès à distance).")
    ports = {"query.port": g("query.port"), "rcon.port": g("rcon.port")}
    for k, label, on in (("query.port", "query", g("enable-query") == "true"), ("rcon.port", "RCON", g("enable-rcon") == "true")):
        if on:
            p = int(ports[k] or 0)
            if p == own_port:
                err.append(f"Le port {label} ({p}) est identique au port du serveur ({own_port}).")
            elif p in other_ports:
                err.append(f"Le port {label} ({p}) est déjà attribué à un autre serveur du panneau.")
    if g("enable-rcon") == "true" and g("enable-query") == "true" and g("query.port") == g("rcon.port"):
        err.append("Les ports query et RCON doivent être différents.")
    if g("hardcore") == "true" and g("difficulty") != "hard":
        warn.append("Hardcore force la difficulté « hard » : le réglage de difficulté sera ignoré.")
    if g("enforce-whitelist") == "true" and g("white-list") != "true":
        warn.append("« Appliquer la whitelist en direct » n'a aucun effet tant que la whitelist n'est pas activée.")
    if int(g("simulation-distance") or 0) > int(g("view-distance") or 0):
        warn.append("La distance de simulation dépasse la distance d'affichage : le surplus est invisible mais coûte du CPU.")
    if g("resource-pack") and not g("resource-pack-sha1"):
        warn.append("Pack de ressources sans empreinte SHA-1 : les joueurs retéléchargeront le pack à chaque connexion et son intégrité n'est pas vérifiée.")
    if g("resource-pack") and not g("resource-pack").lower().startswith("https://"):
        warn.append("L'URL du pack n'est pas en https : le téléchargement n'est pas protégé.")
    if g("resource-pack-sha1") and not re.fullmatch(r"[0-9a-fA-F]{40}", g("resource-pack-sha1")):
        err.append("L'empreinte SHA-1 doit contenir exactement 40 caractères hexadécimaux.")
    if g("require-resource-pack") == "true" and not g("resource-pack"):
        warn.append("Pack obligatoire sans URL de pack : aucun pack ne sera proposé.")
    if g("network-compression-threshold") == "-1":
        warn.append("Compression réseau désactivée : plus de bande passante utilisée.")
    return err, warn


class McSettings:
    def __init__(self, panel):
        self.P = panel
        self._locks: dict[str, threading.Lock] = {}
        self._glock = threading.Lock()

    def _lock(self, sid):
        with self._glock:
            return self._locks.setdefault(sid, threading.Lock())

    def _srv(self, sid):
        srv = self.P.manager.server(sid)
        if srv["status"] != "installed":
            raise UserError("Le serveur n'est pas encore installé.")
        root = Path(srv["path"])
        if not self.P.server_dir_ok(root):
            raise UserError("Dossier du serveur incohérent.")
        return srv, root / "server.properties"

    @staticmethod
    def schema() -> dict:
        return {"categories": [{"key": k, "label": l} for k, l in CATEGORIES], "fields": SCHEMA}

    def get(self, sid: str) -> dict:
        srv, path = self._srv(sid)
        text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        pf = PropsFile(text)
        known = {f["key"] for f in SCHEMA}
        vals = {}
        for f in SCHEMA:
            if not available(f, srv["mc_version"]):
                continue
            raw = pf.values.get(f["key"])
            vals[f["key"]] = {"value": "" if f["secret"] and raw else (raw if raw is not None else None),
                              "set": raw is not None, "secret_set": bool(f["secret"] and raw)}
        return {"values": vals, "unknown": {k: v for k, v in pf.values.items() if k not in known},
                "running": bool(self.P.manager.alive(sid)), "hash": hashlib.sha256(text.encode()).hexdigest(),
                "mc_version": srv["mc_version"], "history_count": self.P.db.one("SELECT COUNT(*) c FROM props_history WHERE server_id=?", (sid,))["c"]}

    def save(self, sid: str, changes: dict, base_hash: str, user: str = "", confirm_offline: bool = False, unknown_delete: list | None = None, note: str = "") -> dict:
        srv, path = self._srv(sid)
        if not isinstance(changes, dict) or not (changes or unknown_delete):
            raise UserError("Aucune modification à enregistrer.")
        with self._lock(sid):
            text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
            if hashlib.sha256(text.encode()).hexdigest() != base_hash:
                raise UserError("Le fichier server.properties a changé depuis son chargement (modifié par le serveur ou un autre onglet). Rechargez la page : rien n'a été écrit.")
            pf = PropsFile(text)
            before = dict(pf.values)
            applied: dict[str, str] = {}
            for k, raw in changes.items():
                f = BY_KEY.get(k)
                if f:
                    if f["managed"]:
                        raise UserError(f"« {f['label']} » se gère ailleurs ({'onglet Configuration' if f['managed'] == 'config' else 'onglet Whitelist'}).")
                    if f["secret"] and (raw is None or raw == "") and k in pf.values:
                        continue                                  # champ secret laissé vide : on garde l'ancien
                    v = validate_value(f, raw)
                else:
                    if not KEY_RE.match(k):
                        raise UserError(f"Nom de propriété invalide : « {k[:40]} ».")
                    v = str(raw if raw is not None else "")
                    if any(ord(c) < 32 for c in v) or len(v) > 1000:
                        raise UserError(f"Valeur invalide pour « {k} ».")
                applied[k] = v
            if applied.get("online-mode") == "false" and before.get("online-mode", "true") != "false" and not confirm_offline:
                raise UserError("Désactiver l'authentification Mojang (online-mode) permet à n'importe qui d'usurper un pseudo, y compris un opérateur. "
                                "Confirmez explicitement pour continuer.")
            final = {**{f["key"]: f["default"] for f in SCHEMA}, **before, **applied}
            for k in unknown_delete or []:
                final.pop(k, None)
            others = {r["port"] for r in self.P.db.query("SELECT port FROM servers WHERE id<>?", (sid,))}
            err, warn = cross_check(final, srv["port"], others)
            if err:
                raise UserError(" ".join(err))
            changed = {k: v for k, v in applied.items() if before.get(k) != v}
            removed = [k for k in (unknown_delete or []) if k in before and k not in BY_KEY]
            if not changed and not removed:
                return {"changed": [], "warnings": warn, "restart_required": [], "running": bool(self.P.manager.alive(sid))}
            self.P.db.execute("INSERT INTO props_history(server_id,ts,username,content,summary) VALUES(?,?,?,?,?)",
                              (sid, now(), user, text, json.dumps({"changed": sorted(changed), "removed": removed, "note": note}, ensure_ascii=False)))
            self.P.db.execute("DELETE FROM props_history WHERE server_id=? AND id NOT IN (SELECT id FROM props_history WHERE server_id=? ORDER BY id DESC LIMIT 50)", (sid, sid))
            for k, v in changed.items():
                pf.set(k, v)
            for k in removed:
                if k in pf.index:
                    i = pf.index[k]
                    pf.lines[i] = None
            pf.lines = [ln for ln in pf.lines if ln is not None]
            tmp = path.with_name(path.name + ".panel-tmp")
            tmp.write_text(pf.text(), encoding="utf-8")
            os.replace(tmp, path)
            running = bool(self.P.manager.alive(sid))
            restart = sorted(k for k in changed if BY_KEY.get(k, {"restart": True})["restart"]) if running else []
            if "level-name" in changed:
                warn.append(f"Nouveau monde « {changed['level-name']} » : il sera créé (ou ouvert s'il existe déjà) au prochain démarrage ; l'ancien monde reste intact.")
            nw = [k for k in changed if BY_KEY.get(k, {}).get("new_world")]
            if nw:
                warn.append("Ces réglages ne s'appliquent qu'à un monde qui n'existe pas encore : " + ", ".join(sorted(nw)) + ".")
            return {"changed": sorted(changed), "removed": removed, "warnings": warn, "restart_required": restart, "running": running}

    def history(self, sid: str) -> list[dict]:
        self._srv(sid)
        out = []
        for r in self.P.db.query("SELECT id,ts,username,summary FROM props_history WHERE server_id=? ORDER BY id DESC LIMIT 50", (sid,)):
            try:
                s = json.loads(r["summary"])
            except ValueError:
                s = {}
            out.append({"id": r["id"], "ts": r["ts"], "user": r["username"], "changed": s.get("changed", []), "removed": s.get("removed", []), "note": s.get("note", "")})
        return out

    def revert(self, sid: str, hid: int, user: str = "") -> dict:
        srv, path = self._srv(sid)
        with self._lock(sid):
            row = self.P.db.one("SELECT * FROM props_history WHERE id=? AND server_id=?", (hid, sid))
            if not row:
                raise UserError("Version introuvable pour ce serveur.")
            cur = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
            self.P.db.execute("INSERT INTO props_history(server_id,ts,username,content,summary) VALUES(?,?,?,?,?)",
                              (sid, now(), user, cur, json.dumps({"changed": [], "removed": [], "note": f"avant retour à la version #{hid}"})))
            tmp = path.with_name(path.name + ".panel-tmp")
            tmp.write_text(row["content"], encoding="utf-8")
            os.replace(tmp, path)
        return {"reverted": hid, "running": bool(self.P.manager.alive(sid))}
