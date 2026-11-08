"""Tâches longues : exécution en arrière-plan, progression, journaux conservés sur disque."""
from __future__ import annotations

import json
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

from .config import Settings
from .db import Database
from .util import new_id, now


class UserError(Exception):
    """Erreur dont le message est directement affichable à l'utilisateur."""


class Job:
    def __init__(self, mgr: "JobManager", jid: str):
        self.mgr, self.id = mgr, jid
        self._buf: list[str] = []
        self._last_flush = 0.0
        self.progress_value = 0.0
        self.step_name = ""

    def step(self, name: str, pct: float | None = None):
        self.step_name = name
        if pct is not None:
            self.progress_value = pct
        self.log(f"=== {name}")
        self.flush(force=True)

    @property
    def cancelled(self) -> bool:
        return self.id in self.mgr.cancelled

    def check_cancel(self):
        if self.cancelled:
            raise UserError("Annulé à votre demande : les fichiers partiels ont été supprimés.")

    def progress(self, pct: float):
        self.progress_value = max(self.progress_value, min(100.0, pct))
        self.flush()

    def log(self, msg: str):
        line = f"[{time.strftime('%H:%M:%S')}] {msg.rstrip()}"
        self._buf.append(line)
        try:
            with open(self.mgr.s.jobs_dir / f"{self.id}.log", "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass
        self.flush()

    def flush(self, force: bool = False):
        t = time.time()
        if not force and t - self._last_flush < 0.5:
            return
        self._last_flush = t
        self.mgr.db.execute("UPDATE jobs SET step=?, progress=?, log=?, updated_at=? WHERE id=?",
                            (self.step_name, self.progress_value, "\n".join(self._buf[-400:]), now(), self.id))


class JobManager:
    def __init__(self, db: Database, settings: Settings, workers: int = 4):
        self.db, self.s = db, settings
        self.on_failed = None         # (type de tâche, serveur, message) — branché sur les notifications
        self.cancelled: set[str] = set()
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="job")
        self.s.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.db.execute("UPDATE jobs SET status='error', error='Interrompu par un redémarrage du panneau.', updated_at=? "
                        "WHERE status IN ('running','queued')", (now(),))

    def submit(self, kind: str, server_id: str | None, fn, *args, **kwargs) -> str:
        jid = new_id()
        self.db.execute("INSERT INTO jobs(id,kind,server_id,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                        (jid, kind, server_id, "queued", now(), now()))

        def run():
            job = Job(self, jid)
            self.db.execute("UPDATE jobs SET status='running', updated_at=? WHERE id=?", (now(), jid))
            try:
                result = fn(job, *args, **kwargs)
                job.progress_value = 100.0
                job.flush(force=True)
                self.db.execute("UPDATE jobs SET status='done', progress=100, result=?, updated_at=? WHERE id=?",
                                (json.dumps(result or {}), now(), jid))
            except Exception as e:  # noqa: BLE001 — toute erreur doit finir dans le journal de la tâche
                msg = str(e) if isinstance(e, (UserError,)) or e.__class__.__module__.startswith("mcpanel") else f"Erreur interne : {e}"
                job.log("ERREUR : " + msg)
                if not isinstance(e, UserError):
                    job.log(traceback.format_exc())
                job.flush(force=True)
                self.db.execute("UPDATE jobs SET status='error', error=?, updated_at=? WHERE id=?", (msg[:2000], now(), jid))
                if self.on_failed:
                    try:
                        self.on_failed(kind, server_id, msg)
                    except Exception:  # noqa: BLE001
                        pass

        self.pool.submit(run)
        return jid

    def cancel(self, jid: str) -> bool:
        j = self.get(jid)
        if not j or j["status"] not in ("queued", "running"):
            return False
        self.cancelled.add(jid)
        return True

    def get(self, jid: str) -> dict | None:
        return self.db.one("SELECT * FROM jobs WHERE id=?", (jid,))

    def wait(self, jid: str, timeout: float = 60) -> dict:
        end = time.time() + timeout
        while time.time() < end:
            j = self.get(jid)
            if j and j["status"] in ("done", "error"):
                return j
            time.sleep(0.05)
        raise TimeoutError("tâche trop longue")

    def shutdown(self):
        self.pool.shutdown(wait=False, cancel_futures=True)
