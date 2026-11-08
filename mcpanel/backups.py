"""Sauvegardes par serveur : /srv/minecraft-data/backups/<NOM>/.

Cohérence : serveur en marche -> `save-off`, `save-all flush`, attente de la confirmation dans la console,
archivage, puis `save-on` (toujours, même en cas d'erreur). Mode « arrêt » : le serveur est arrêté proprement
pendant l'archive puis relancé.
"""
from __future__ import annotations

import os
import shutil
import threading
import time
import zipfile
from pathlib import Path

from .config import Settings
from .db import Database
from .jobs import Job, UserError
from .process import ProcessError, ServerManager
from .security import SecurityError, safe_join
from .util import new_id, now

EXCLUDE_DIRS = {"logs", "crash-reports", "cache", ".fabric", "debug"}
EXCLUDE_FILES = {"session.lock"}
DEFAULT_PREFS = {"enabled": False, "interval_hours": 24, "keep": 7, "mode": "hot", "on_stop": False}


def prefs_of(srv: dict) -> dict:
    p = dict(DEFAULT_PREFS)
    p.update(srv.get("backup_prefs") or {})
    return p


class BackupService:
    def __init__(self, settings: Settings, db: Database, manager: ServerManager):
        self.s, self.db, self.mgr = settings, db, manager
        self._gate = threading.Lock()  # une seule archive à la fois (le disque dur n'aime pas le parallélisme)

    def dir_for(self, name: str) -> Path:
        return self.s.backups_dir / name

    def _wait_saved(self, srv: dict, since_size: int, timeout: int = 90) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            text, _ = self.mgr.read_console(srv, offset=since_size)
            if "Saved the game" in text or "Saved the world" in text or "Gespeichert" in text:
                return True
            if not self.mgr.alive(srv["id"]):
                return False
            time.sleep(0.3)
        return False

    def _iter_files(self, root: Path):
        for dp, dn, fn in os.walk(root, followlinks=False):
            rel = Path(dp).relative_to(root)
            if rel == Path("."):
                dn[:] = [d for d in dn if d not in EXCLUDE_DIRS]
            for f in fn:
                if f in EXCLUDE_FILES or f.endswith((".part", ".panel-tmp")):
                    continue
                p = Path(dp) / f
                if p.is_symlink():
                    continue
                yield p, (rel / f).as_posix()

    def create(self, job: Job, sid: str, kind: str = "manual", protected: bool = False, only_paths: list[str] | None = None) -> dict:
        srv = self.mgr.server(sid)
        prefs = prefs_of(srv)
        root = Path(srv["path"])
        if not root.is_dir():
            raise UserError("Dossier du serveur introuvable.")
        out_dir = self.dir_for(srv["name"])
        out_dir.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(out_dir).free
        need = sum(p.stat().st_size for p, _ in self._iter_files(root)) if not only_paths else 0
        if need and free < need * 0.6 + 512 * 1024 * 1024:
            raise UserError("Espace disque insuffisant pour la sauvegarde.")
        with self._gate:
            job.step("Préparation", 3)
            running = bool(self.mgr.alive(sid))
            restart_after = False
            frozen = False
            if running and prefs["mode"] == "stop" and kind != "pre-restore":
                job.step("Arrêt propre du serveur", 5)
                self.mgr.stop(sid, 90, wait=True)
                restart_after = True
                running = False
            if running:
                job.step("Gel des écritures du monde (save-off / save-all)", 8)
                size = self.mgr.console_path(srv).stat().st_size if self.mgr.console_path(srv).exists() else 0
                self.mgr.send_command(sid, "save-off")
                frozen = True
                time.sleep(0.5)
                size = self.mgr.console_path(srv).stat().st_size if self.mgr.console_path(srv).exists() else size
                self.mgr.send_command(sid, "save-all flush")
                if not self._wait_saved(srv, size):
                    job.log("Attention : confirmation « Saved the game » non reçue (délai dépassé) ; archivage quand même.")
            ts = time.strftime("%Y%m%d-%H%M%S")
            fname = f"{srv['name']}_{ts}_{kind}.zip"
            dest = out_dir / fname
            part = dest.with_name(fname + ".part")
            try:
                job.step("Archivage", 12)
                files = list(self._iter_files(root))
                if only_paths:
                    wanted = set(only_paths)
                    files = [(p, a) for p, a in files if a in wanted]
                n = len(files)
                with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED, compresslevel=3, allowZip64=True) as z:
                    for i, (p, arc) in enumerate(files, 1):
                        try:
                            z.write(p, arc)
                        except OSError as e:
                            job.log(f"Ignoré ({e}) : {arc}")
                        if i % 25 == 0 or i == n:
                            job.progress(12 + 85 * i / max(n, 1))
                job.step("Vérification de l'archive", 97)
                with zipfile.ZipFile(part) as z:                       # jamais de rotation avant une archive vérifiée
                    bad = z.testzip()
                    if bad is not None or len(z.infolist()) != len([1 for _ in z.infolist()]):
                        raise UserError(f"Archive de sauvegarde corrompue ({bad}) : les sauvegardes précédentes sont conservées.")
                os.replace(part, dest)
            finally:
                part.unlink(missing_ok=True)
                if frozen:
                    try:
                        self.mgr.send_command(sid, "save-on")
                    except ProcessError:
                        pass
            if restart_after:
                job.step("Redémarrage du serveur", 98)
                try:
                    self.mgr.start(sid)
                except ProcessError as e:
                    job.log(f"Redémarrage impossible : {e}")
            bid = new_id()
            size = dest.stat().st_size
            self.db.execute("INSERT INTO backups(id,server_id,server_name,filename,size,created_at,kind,protected) VALUES(?,?,?,?,?,?,?,?)",
                            (bid, sid, srv["name"], fname, size, now(), kind, 1 if protected or kind in ("pre-restore", "pre-import", "manual") else 0))
            job.log(f"Sauvegarde créée : {fname} ({size // 1024 // 1024} Mio)")
            if kind == "auto":
                self.apply_retention(srv)
            return {"id": bid, "filename": fname, "size": size}

    def apply_retention(self, srv: dict):
        """Ne supprime que les anciennes sauvegardes AUTOMATIQUES au-delà du nombre choisi par l'utilisateur."""
        keep = max(1, int(prefs_of(srv)["keep"]))
        autos = self.db.query("SELECT * FROM backups WHERE server_id=? AND kind='auto' AND protected=0 ORDER BY created_at DESC", (srv["id"],))
        for b in autos[keep:]:
            (self.dir_for(b["server_name"]) / b["filename"]).unlink(missing_ok=True)
            self.db.execute("DELETE FROM backups WHERE id=?", (b["id"],))

    def list(self, sid: str) -> list[dict]:
        rows = self.db.query("SELECT * FROM backups WHERE server_id=? ORDER BY created_at DESC", (sid,))
        for r in rows:
            r["exists"] = (self.dir_for(r["server_name"]) / r["filename"]).exists()
        return rows

    def path_of(self, bid: str) -> tuple[dict, Path]:
        b = self.db.one("SELECT * FROM backups WHERE id=?", (bid,))
        if not b:
            raise UserError("Sauvegarde introuvable.")
        p = safe_join(self.dir_for(b["server_name"]), b["filename"])
        return b, p

    def delete(self, bid: str):
        b, p = self.path_of(bid)
        p.unlink(missing_ok=True)
        self.db.execute("DELETE FROM backups WHERE id=?", (bid,))

    def restore(self, job: Job, sid: str, bid: str) -> dict:
        srv = self.mgr.server(sid)
        b, archive = self.path_of(bid)
        if b["server_id"] != sid:
            raise UserError("Cette sauvegarde appartient à un autre serveur.")
        if self.mgr.alive(sid):
            raise UserError("Arrêtez le serveur avant de restaurer une sauvegarde.")
        if not archive.exists():
            raise UserError("Le fichier de sauvegarde est absent du disque.")
        root = Path(srv["path"])
        try:
            with zipfile.ZipFile(archive) as z:
                bad = z.testzip()
                if bad:
                    raise UserError(f"Sauvegarde corrompue (entrée {bad}) : restauration annulée, rien n'a été modifié.")
                for i in z.infolist():
                    safe_join(root, i.filename)
        except zipfile.BadZipFile as e:
            raise UserError("Archive de sauvegarde illisible : restauration annulée.") from e
        except SecurityError as e:
            raise UserError("Archive de sauvegarde suspecte : restauration annulée.") from e
        job.step("Sauvegarde de sécurité de l'état actuel", 5)
        safety = self.create(job, sid, kind="pre-restore", protected=True)
        job.step("Restauration", 50)
        with zipfile.ZipFile(archive) as z:
            members = z.infolist()
            tops = {m.filename.split("/")[0] for m in members}
            for t in tops:
                target = safe_join(root, t)
                if target.is_dir():
                    shutil.rmtree(target)
                elif target.exists():
                    target.unlink()
            for i, m in enumerate(members, 1):
                dest = safe_join(root, m.filename)
                if m.is_dir():
                    dest.mkdir(parents=True, exist_ok=True)
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                with z.open(m) as src, open(dest, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                if i % 50 == 0:
                    job.progress(50 + 48 * i / len(members))
        job.log(f"Restauration terminée. L'état précédent reste disponible dans la sauvegarde « {safety['filename']} ».")
        return {"restored": b["filename"], "safety": safety["filename"]}

    def due(self) -> list[dict]:
        out = []
        for srv in self.db.query("SELECT * FROM servers WHERE install_ok=1 AND status='installed'"):
            p = prefs_of(srv)
            if not p["enabled"]:
                continue
            last = self.db.one("SELECT MAX(created_at) AS t FROM backups WHERE server_id=? AND kind='auto'", (srv["id"],))
            t = (last or {}).get("t") or 0
            if time.time() - t >= max(1, float(p["interval_hours"])) * 3600:
                out.append(srv)
        return out
