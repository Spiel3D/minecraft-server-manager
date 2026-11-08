"""Assemblage des services du panneau et règles métier (création, suppression, configuration d'un serveur)."""
from __future__ import annotations

import json
import re
import os
import shutil
import time
from pathlib import Path

from .backups import BackupService, DEFAULT_PREFS
from .config import Settings
from .db import Database
from .installer import run_install, set_properties, write_eula
from .javamgr import JavaManager
from .jobs import JobManager, UserError
from .monitor import Alerts, Monitor
from .process import ServerManager, port_free
from .accounts import AccountService
from .events import EventBus
from .security import LoginThrottle, RateLimiter, Sessions, parse_nets, slugify, validate_display_name
from .supervision import Supervision
from .tokens import TokenService
from .mcsettings import McSettings
from .primary import PrimaryPort
from .crash import CrashReports
from .perf import PerfService
from .modsearch import ModSearch
from .modconfig import ModConfig
from .storage import Storage
from .whitelist import WhitelistService
from .adapters import detect_jar, load_adapters
from .sources import Http, Sources
from .util import is_mountpoint, new_id, now

PORT_MIN, PORT_START, PORT_MAX = 1024, 25565, 65535
RAM_MIN_MB = 512


class Panel:
    def __init__(self, settings: Settings, http: Http | None = None):
        self.s = settings
        self.s.state_dir.mkdir(parents=True, exist_ok=True)
        self.s.run_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(self.s.db_path)
        self.http = http or Http()
        self.sources = Sources(self.http)
        self.sources.adapters = load_adapters(self.s.state_dir / "server-types.d")
        self.java = JavaManager(self.s, self.http, self.sources.adoptium)
        self.jobs = JobManager(self.db, self.s)
        self.alerts = Alerts(self.db)
        self.storage = Storage(self)
        self.manager = ServerManager(self.s, self.db, alert=self.alerts.raise_, mount_ok=self.data_ready)
        self.manager.clear_alert = self.alerts.clear
        self.monitor = Monitor(self.s, self.db, self.manager, self.alerts)
        self.manager.ram_check = self.monitor.check_start_ram
        self.backups = BackupService(self.s, self.db, self.manager)
        self.sessions = Sessions(self.db, self.s.session_hours)
        self.throttle = LoginThrottle()
        self.limiter = RateLimiter()
        self.accounts = AccountService(self)
        self.tokens = TokenService(self)
        self.events = EventBus(self)
        self.supervision = Supervision(self)
        self.whitelist = WhitelistService(self)
        self.mcsettings = McSettings(self)
        self.primary = PrimaryPort(self)
        self.crashes = CrashReports(self)
        self.perf = PerfService(self)
        self.modsearch = ModSearch(self)
        self.modconfig = ModConfig(self)
        self.monitor.perf = self.perf
        self.manager.event = lambda kind, srv, data=None: self.events.emit(kind, srv["id"], self.label(srv), data)
        self.manager.crash_hook = self.crashes.capture
        self.alerts.on_new = self._alert_event
        self.jobs.on_failed = self._job_failed
        self.nets = parse_nets(self.s.allowed_nets)
        self._ready_cache = (0.0, True)

    def data_ready(self) -> bool:
        t, v = self._ready_cache
        if time.time() - t < 3:
            return v
        if self.s.require_mount:
            v = is_mountpoint(self.s.data_root)
        else:
            v = self.s.data_root.is_dir()
        self._ready_cache = (time.time(), v)
        return v

    def ensure_data_dirs(self):
        if not self.data_ready():
            raise UserError(f"{self.s.data_root} n'est pas monté ou n'existe pas.")
        for d in (self.s.servers_dir, self.s.backups_dir, self.s.uploads_dir, self.s.temp_dir):
            d.mkdir(parents=True, exist_ok=True)

    def cleanup_temp(self, max_age_h: float = 24):
        """Nettoie uniquement temporary/ et les .part orphelins de uploads/ (jamais les serveurs ni les sauvegardes)."""
        if not self.data_ready():
            return
        limit = time.time() - max_age_h * 3600
        for base in (self.s.temp_dir, self.s.uploads_dir):
            if not base.is_dir():
                continue
            for p in base.iterdir():
                try:
                    if p.stat().st_mtime < limit and (base == self.s.temp_dir or p.name.endswith(".part") or p.suffix == ".zip"):
                        shutil.rmtree(p) if p.is_dir() else p.unlink()
                except OSError:
                    pass
        for b in self.s.backups_dir.glob("*/*.part") if self.s.backups_dir.is_dir() else []:
            try:
                if b.stat().st_mtime < time.time() - 3600:
                    b.unlink()
            except OSError:
                pass

    def audit(self, user: str | None, ip: str | None, action: str, target: str = "", detail: str = ""):
        self.db.execute("INSERT INTO audit_log(ts,user,ip,action,target,detail) VALUES(?,?,?,?,?,?)",
                        (now(), user, ip, action, target, detail[:500]))

    def used_ports(self, exclude_sid: str | None = None) -> set[int]:
        return {r["port"] for r in self.db.query("SELECT id, port FROM servers") if r["id"] != exclude_sid}

    def server_roots(self) -> list[Path]:
        """Dossiers autorisés pour les serveurs : disque de données (HDD) et zone rapide du SSD."""
        return [Path(os.path.realpath(self.s.servers_dir)), Path(os.path.realpath(self.storage.ssd_dir))]

    def server_dir_ok(self, d: Path) -> bool:
        return not d.is_symlink() and d.parent.resolve() in self.server_roots()

    @staticmethod
    def label(srv: dict) -> str:
        """Nom affiché à l'utilisateur (le champ « name » reste l'identifiant technique/dossier)."""
        return srv.get("display_name") or srv["name"]

    def unique_slug(self, display: str) -> str:
        base = slugify(display)
        taken = {r["name"].lower() for r in self.db.query("SELECT name FROM servers")}
        for i in range(1, 1000):
            cand = base if i == 1 else f"{base[:44]}-{i}"
            if cand.lower() not in taken and not (self.s.servers_dir / cand).exists() and not (self.storage.ssd_dir / cand).exists():
                return cand
        raise UserError("Impossible de trouver un nom de dossier libre.")

    def suggest_port(self) -> int:
        used = self.used_ports()
        p = PORT_START
        while p <= PORT_MAX:
            if p not in used and port_free(p):
                return p
            p += 1
        raise UserError("Aucun port libre.")

    def limits(self) -> dict:
        b = self.monitor.ram_budget()
        return {"ram": b, "suggested_port": self.suggest_port(), "ram_min_mb": RAM_MIN_MB,
                "types": list(self.sources.adapters), "adapters": [a.public() for a in self.sources.adapters.values()], "java_installed": self.java.installed()}

    def create_server(self, params: dict) -> tuple[dict, str]:
        self.ensure_data_dirs()
        display = validate_display_name(params.get("name", ""))
        if any(r["display_name"].casefold() == display.casefold() for r in self.db.query("SELECT display_name FROM servers")):
            raise UserError("Un serveur porte déjà ce nom.")
        name = self.unique_slug(display)
        desc = str(params.get("description") or "")[:500]
        stype = str(params.get("server_type", "")).lower()
        mc = str(params.get("mc_version", "")).strip()
        loader = (params.get("loader_version") or None)
        adapter = self.sources.adapters.get(stype)
        if not adapter:
            raise UserError("Type de serveur invalide.")
        try:
            xmx, xms = int(params.get("ram_max_mb", 0)), int(params.get("ram_min_mb", 0))
            port = int(params.get("port", 0))
        except (TypeError, ValueError):
            raise UserError("RAM ou port invalide.")
        if xmx < RAM_MIN_MB:
            raise UserError(f"Mémoire maximale minimale : {RAM_MIN_MB} Mo.")
        xms = xms or max(RAM_MIN_MB, xmx // 2)
        if xms > xmx:
            raise UserError("La mémoire minimale (-Xms) ne peut pas dépasser la mémoire maximale (-Xmx).")
        b = self.monitor.ram_budget()
        if xmx > b["max_xmx_mb"]:
            raise UserError(f"Mémoire demandée indisponible : maximum {b['max_xmx_mb']} Mo par serveur "
                            f"(RAM totale {b['total_mb']} Mo − réserve système {b['reserve_mb']} Mo).")
        if not (PORT_MIN <= port <= PORT_MAX):
            raise UserError(f"Port invalide (entre {PORT_MIN} et {PORT_MAX}).")
        if port in self.used_ports():
            raise UserError(f"Le port {port} est déjà attribué à un autre serveur.")
        if not port_free(port):
            raise UserError(f"Le port {port} est déjà utilisé sur cette machine.")
        path = self.s.servers_dir / name
        if path.exists():
            raise UserError("Le dossier existe déjà sur le disque : choisissez un autre nom (rien n'a été écrasé).")
        pending = self._initial_config(params, port)
        self.sources.resolve(stype, mc, loader)
        import_mode = adapter.mode == "import"
        if import_mode:
            loader = None
        sid = new_id()
        self.db.execute(
            "INSERT INTO servers(id,name,display_name,description,path,mc_version,server_type,loader_version,java_major,port,ram_min_mb,ram_max_mb,"
            "created_at,status,backup_prefs,pending_config) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, name, display, desc, str(path), mc, stype, loader, 0, port, xms, xmx, now(), "awaiting_jar" if import_mode else "installing", json.dumps(DEFAULT_PREFS), json.dumps(pending)))
        if import_mode:
            path.mkdir(parents=True, exist_ok=True)
            return self.db.one("SELECT * FROM servers WHERE id=?", (sid,)), None
        jid = self.jobs.submit("install", sid, run_install_wrapper, self, sid)
        return self.db.one("SELECT * FROM servers WHERE id=?", (sid,)), jid

    def _initial_config(self, params: dict, port: int) -> dict:
        """V4 §2 : réglages Minecraft choisis dès la création. Tout est validé AVANT de créer quoi que ce soit."""
        from .mcsettings import BY_KEY, cross_check, validate_value
        from .whitelist import NAME_RE
        props_in = params.get("properties") or {}
        if not isinstance(props_in, dict):
            raise UserError("Réglages Minecraft invalides.")
        props = {}
        for k, raw in props_in.items():
            f = BY_KEY.get(k)
            if not f:
                raise UserError(f"Réglage inconnu : « {str(k)[:40]} ».")
            if f["managed"]:
                raise UserError(f"« {f['label']} » se règle par son propre champ.")
            props[k] = validate_value(f, raw)
        if props.get("online-mode") == "false" and not params.get("confirm_offline"):
            raise UserError("Désactiver l'authentification Mojang (online-mode) permet à n'importe qui d'usurper un pseudo, y compris un opérateur. "
                            "Confirmez explicitement pour continuer.")
        wl = params.get("whitelist") or {}
        if not isinstance(wl, dict):
            raise UserError("Whitelist invalide.")
        players = wl.get("players") or []
        if isinstance(players, str):
            players = [x for x in re.split(r"[\s,;]+", players) if x]
        if not isinstance(players, list) or len(players) > 200:
            raise UserError("Whitelist : 200 joueurs maximum.")
        clean, seen = [], set()
        for n in players:
            n = str(n).strip()
            if not NAME_RE.match(n):
                raise UserError(f"Whitelist : pseudo invalide « {n[:20]} ».")
            if n.lower() not in seen:
                seen.add(n.lower())
                clean.append(n)
        enabled = bool(wl.get("enabled")) or (bool(clean) and wl.get("enabled") is None)
        values = {**{f["key"]: f["default"] for f in BY_KEY.values()}, **props, "white-list": "true" if enabled else "false"}
        others = {r["port"] for r in self.db.query("SELECT port FROM servers")}
        err, _ = cross_check(values, port, others)
        if err:
            raise UserError(" ".join(err))
        return {"properties": props, "whitelist": {"enabled": enabled, "players": clean}} if (props or clean or enabled) else {}

    def _alert_event(self, key: str, level: str, message: str):
        kind = "system.ram_low" if key == "ram_low" else "system.disk_low" if key.startswith("disk") else None
        if kind and level == "critical":
            self.events.emit(kind, None, None, {"message": message})

    def _job_failed(self, kind: str, sid: str | None, msg: str):
        if kind == "backup":
            srv = self.db.one("SELECT * FROM servers WHERE id=?", (sid,)) if sid else None
            self.events.emit("backup.failed", sid, self.label(srv) if srv else None, {"reason": msg[:500]})

    def inspect_import(self, sid: str, jar_name: str = "server.jar") -> dict:
        """Examine (sans l'exécuter) le jar déposé dans le dossier d'un serveur de type « import »."""
        srv = self.manager.server(sid)
        jar = self._import_jar(srv, jar_name)
        info = detect_jar(jar)
        info["jar"] = jar.name
        info["size"] = jar.stat().st_size
        if info["guess"] and info["guess"] != srv["server_type"] and srv["server_type"] != "custom":
            info["warnings"].append(f"Ce jar ressemble à « {info['guess']} » alors que le serveur est déclaré « {srv['server_type']} ».")
        if info["mc_version"] and info["mc_version"] != srv["mc_version"]:
            info["warnings"].append(f"Version Minecraft détectée : {info['mc_version']} (déclarée : {srv['mc_version']}).")
        return info

    def _import_jar(self, srv: dict, jar_name: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,100}\.jar", jar_name or ""):
            raise UserError("Nom de jar invalide (lettres, chiffres, « _ », « - », « . », « + » ; extension .jar).")
        root = Path(srv["path"])
        jar = root / jar_name
        if jar.is_symlink() or not jar.is_file() or jar.parent.resolve() != root.resolve():
            raise UserError(f"Jar introuvable dans le dossier du serveur : déposez « {jar_name} » depuis l'onglet Fichiers.")
        return jar

    def finalize_import(self, sid: str, jar_name: str = "server.jar") -> str:
        srv = self.manager.server(sid)
        if srv["status"] not in ("awaiting_jar", "error"):
            raise UserError("Ce serveur n'attend pas de jar.")
        if self.manager.alive(sid):
            raise UserError("Arrêtez le serveur d'abord.")
        info = self.inspect_import(sid, jar_name)
        if not info["valid"]:
            raise UserError("Ce fichier n'est pas un jar valide (archive ZIP illisible).")
        self.db.execute("UPDATE servers SET launch_json=? WHERE id=?", (json.dumps({"import_jar": jar_name}), sid))
        return self.jobs.submit("install", sid, run_install_wrapper, self, sid)

    def reinstall(self, sid: str) -> str:
        srv = self.manager.server(sid)
        if self.manager.alive(sid):
            raise UserError("Arrêtez le serveur avant de relancer l'installation.")
        if srv["status"] == "installed":
            raise UserError("Ce serveur est déjà installé.")
        return self.jobs.submit("install", sid, run_install_wrapper, self, sid)

    def accept_eula(self, sid: str):
        srv = self.manager.server(sid)
        if srv["status"] != "installed":
            raise UserError("Installation incomplète.")
        write_eula(Path(srv["path"]), True)
        self.db.execute("UPDATE servers SET eula_accepted=1 WHERE id=?", (sid,))

    def update_server(self, sid: str, data: dict) -> dict:
        srv = self.manager.server(sid)
        running = bool(self.manager.alive(sid))
        sets, vals = [], []

        def put(col, v):
            sets.append(f"{col}=?")
            vals.append(v)

        if "display_name" in data or ("name" in data and str(data["name"]) != srv["name"]):
            new = validate_display_name(data.get("display_name", data.get("name")))
            if any(r["display_name"].casefold() == new.casefold() for r in self.db.query("SELECT display_name FROM servers WHERE id<>?", (sid,))):
                raise UserError("Un serveur porte déjà ce nom.")
            put("display_name", new)           # renommage cosmétique : le dossier, les sauvegardes et les chemins ne bougent pas
        if "description" in data:
            put("description", str(data["description"])[:500])
        for k in ("autostart", "auto_restart"):
            if k in data:
                put(k, 1 if data[k] else 0)
        if "jvm_extra" in data:
            extra = str(data["jvm_extra"]).strip()
            if len(extra) > 500 or any(c in extra for c in ";&|`$<>\\\n\r\"'"):
                raise UserError("Arguments JVM invalides (caractères interdits).")
            toks = extra.split()
            if any(not t.startswith("-") for t in toks):
                raise UserError("Chaque argument JVM doit commencer par « - ».")
            put("jvm_extra", extra)
        if "ram_max_mb" in data or "ram_min_mb" in data:
            try:
                xmx = int(data.get("ram_max_mb", srv["ram_max_mb"]))
                xms = int(data.get("ram_min_mb", srv["ram_min_mb"]))
            except (TypeError, ValueError):
                raise UserError("Valeurs de mémoire invalides.")
        if ("ram_max_mb" in data or "ram_min_mb" in data) and (xmx, xms) != (srv["ram_max_mb"], srv["ram_min_mb"]):
            if running:
                raise UserError("Arrêtez le serveur pour changer sa mémoire.")
            if xmx < RAM_MIN_MB or xms < 128 or xms > xmx:
                raise UserError("Valeurs de mémoire invalides.")
            b = self.monitor.ram_budget(exclude_sid=sid)
            if xmx > b["max_xmx_mb"]:
                raise UserError(f"Mémoire demandée indisponible : maximum {b['max_xmx_mb']} Mo par serveur "
                                f"(RAM totale {b['total_mb']} Mo − réserve système {b['reserve_mb']} Mo).")
            put("ram_max_mb", xmx)
            put("ram_min_mb", xms)
        if "port" in data:
            try:
                data = {**data, "port": int(data["port"])}
            except (TypeError, ValueError):
                raise UserError("Port invalide.")
        if "port" in data and data["port"] != srv["port"]:
            if running:
                raise UserError("Arrêtez le serveur pour changer son port.")
            port = int(data["port"])
            if not (PORT_MIN <= port <= PORT_MAX):
                raise UserError("Port invalide.")
            if port in self.used_ports(exclude_sid=sid) or not port_free(port):
                raise UserError(f"Le port {port} est déjà utilisé.")
            put("port", port)
            if srv["status"] == "installed":
                set_properties(Path(srv["path"]) / "server.properties", {"server-port": str(port)})
        if "backup_prefs" in data:
            p = dict(DEFAULT_PREFS)
            bp = data["backup_prefs"] or {}
            p["enabled"] = bool(bp.get("enabled"))
            p["interval_hours"] = min(max(float(bp.get("interval_hours", 24)), 1), 24 * 30)
            p["keep"] = min(max(int(bp.get("keep", 7)), 1), 100)
            p["mode"] = bp.get("mode") if bp.get("mode") in ("hot", "stop") else "hot"
            p["on_stop"] = bool(bp.get("on_stop"))
            put("backup_prefs", json.dumps(p))
        if sets:
            self.db.execute(f"UPDATE servers SET {', '.join(sets)} WHERE id=?", (*vals, sid))
        return self.manager.server(sid)

    def delete_server(self, sid: str, confirm_name: str, delete_backups: bool = False):
        srv = self.manager.server(sid)
        if confirm_name not in (srv["name"], self.label(srv)):
            raise UserError("Confirmation incorrecte : tapez exactement le nom du serveur.")
        if self.manager.alive(sid):
            raise UserError("Arrêtez le serveur avant de le supprimer.")
        if srv["status"] == "installing" and self.db.one("SELECT 1 FROM jobs WHERE server_id=? AND status IN ('queued','running')", (sid,)):
            raise UserError("Installation en cours : attendez sa fin.")
        d = Path(srv["path"])
        if not self.server_dir_ok(d) or d.name != srv["name"]:
            raise UserError("Chemin du serveur incohérent : suppression refusée par sécurité.")
        if d.exists():
            shutil.rmtree(d)
        self.db.execute("DELETE FROM servers WHERE id=?", (sid,))
        self.db.execute("DELETE FROM jobs WHERE server_id=?", (sid,))
        if delete_backups:
            bdir = self.backups.dir_for(srv["name"])
            if bdir.is_dir() and bdir.parent.resolve() == Path(os.path.realpath(self.s.backups_dir)):
                shutil.rmtree(bdir)
            self.db.execute("DELETE FROM backups WHERE server_id=?", (sid,))
        for f in (self.manager.runfile(sid), self.manager.fifo(sid)):
            f.unlink(missing_ok=True)

    def server_root(self, sid: str) -> tuple[dict, Path]:
        self.ensure_data_dirs()
        srv = self.db.one("SELECT * FROM servers WHERE id=?", (sid,))
        if not srv:
            raise UserError("Serveur introuvable.")
        root = Path(srv["path"])
        if not self.server_dir_ok(root):
            raise UserError("Chemin du serveur incohérent.")
        if not root.is_dir():
            raise UserError("Dossier du serveur introuvable.")
        return srv, root

    def start_server(self, sid: str) -> dict:
        """Démarrage demandé par une personne : met d'abord le monde sur le SSD si la règle automatique s'applique."""
        srv = self.manager.server(sid)
        if self.storage.needs_promotion(srv) and self.storage.plan_promotion(srv)["possible"]:
            self.manager.blocked.pop(sid, None)
            return {"job": self.jobs.submit("ssd-promote", sid, self.storage.promote_and_start, sid), "moving": True}
        self.manager.start(sid)
        return {"ok": True}

    def on_server_stopped(self, sid: str):
        srv = self.db.one("SELECT * FROM servers WHERE id=?", (sid,))
        if srv and (srv.get("backup_prefs") or {}).get("on_stop") and srv["desired_state"] == "stopped":
            self.jobs.submit("backup", sid, lambda job: self.backups.create(job, sid, kind="auto"))

    def on_startup(self):
        if self.data_ready():
            try:
                self.ensure_data_dirs()
                self.storage.cleanup()
                self.cleanup_temp()
            except OSError:
                pass
        self.manager.reconcile()

    def system_is_shutting_down(self) -> bool:
        import subprocess
        try:
            r = subprocess.run(["systemctl", "is-system-running"], capture_output=True, text=True, timeout=5)
            return r.stdout.strip() == "stopping"
        except (OSError, subprocess.SubprocessError):
            return False

    def on_shutdown(self):
        if self.system_is_shutting_down():
            self.manager.stop_all(90)
        self.jobs.shutdown()


def run_install_wrapper(job, panel: Panel, sid: str):
    return run_install(panel, job, sid)
