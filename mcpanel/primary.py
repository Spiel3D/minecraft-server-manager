"""Port principal unique (V4 §4).

Un seul serveur à la fois écoute sur le port public (25565 par défaut). Désigner un autre serveur principal :
1. calcule le plan (le nouveau prend 25565, l'ancien prend le premier port secondaire libre) ;
2. arrête proprement les serveurs concernés qui tournent (après confirmation, joueurs connectés listés) ;
3. vérifie que le port principal est réellement libre, met à jour la base et server.properties ;
4. redémarre ce qui tournait et vérifie que le nouveau principal répond sur le port principal ;
5. en cas d'échec : remet les anciens ports et redémarre comme avant.
Pendant l'opération, les démarrages des serveurs concernés sont bloqués (pas de concurrence possible).
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from .jobs import UserError
from .process import ProcessError, mc_ping, mc_status, port_free

DEFAULT_PRIMARY = 25565


class PrimaryPort:
    verify_timeout = 300          # secondes laissées au nouveau principal pour répondre (gros modpacks : démarrage lent)

    def __init__(self, panel):
        self.P = panel
        self._op = threading.Lock()

    @property
    def port(self) -> int:
        v = self.P.db.get_setting("primary_port")
        try:
            return int(v) if v else DEFAULT_PRIMARY
        except ValueError:
            return DEFAULT_PRIMARY

    def holder(self) -> dict | None:
        return self.P.db.one("SELECT * FROM servers WHERE port=?", (self.port,))

    def _next_secondary(self, taken: set[int]) -> int:
        p = self.port + 1
        while p <= 65535:
            if p not in taken and port_free(p):
                return p
            p += 1
        raise UserError("Aucun port secondaire libre.")

    def plan(self, sid: str) -> dict:
        target = self.P.manager.server(sid)
        if target["port"] == self.port:
            raise UserError("Ce serveur est déjà le serveur principal.")
        if target["status"] != "installed":
            raise UserError("Le serveur doit être installé pour devenir principal.")
        old = self.holder()
        taken = {r["port"] for r in self.P.db.query("SELECT port FROM servers")} | {self.port}
        changes = [{"id": sid, "name": self.P.label(target), "from": target["port"], "to": self.port}]
        if old:
            changes.append({"id": old["id"], "name": self.P.label(old), "from": old["port"], "to": self._next_secondary(taken)})
        elif not port_free(self.port):
            raise UserError(f"Le port {self.port} est occupé par un programme extérieur au panneau : impossible d'en faire le port principal.")
        for c in changes:
            srv = self.P.manager.server(c["id"])
            c["running"] = bool(self.P.manager.alive(c["id"]))
            st = mc_status(srv["port"], timeout=1.0) if c["running"] else None
            c["players"] = (st or {}).get("names", []) if c["running"] else []
            c["players_online"] = (st or {}).get("online", 0) if c["running"] else 0
        return {"primary_port": self.port, "changes": changes, "restart": [c["name"] for c in changes if c["running"]],
                "players_affected": sum(c["players_online"] for c in changes)}

    # ------------------------------------------------------------------ exécution (dans une tâche de fond)
    def _write_port(self, sid: str, port: int):
        from .mcsettings import PropsFile
        srv = self.P.manager.server(sid)
        props = Path(srv["path"]) / "server.properties"
        pf = PropsFile(props.read_text(encoding="utf-8", errors="replace") if props.exists() else "")
        pf.set("server-port", str(port))
        if pf.values.get("query.port") == str(srv["port"]):
            pf.set("query.port", str(port))
        tmp = props.with_name(props.name + ".panel-tmp")
        tmp.write_text(pf.text(), encoding="utf-8")
        os.replace(tmp, props)
        self.P.db.execute("UPDATE servers SET port=? WHERE id=?", (port, sid))

    def execute(self, job, sid: str) -> dict:
        if not self._op.acquire(blocking=False):
            raise UserError("Un changement de serveur principal est déjà en cours.")
        mgr = self.P.manager
        try:
            plan = self.plan(sid)
            changes = plan["changes"]
            ids = [c["id"] for c in changes]
            for i in ids:
                mgr.blocked[i] = "changement de serveur principal"
            was_running = [c["id"] for c in changes if c["running"]]
            try:
                job.step("Arrêt des serveurs concernés", 10)
                for i in was_running:
                    job.log(f"Arrêt propre de « {self.P.label(mgr.server(i))} »…")
                    mgr.stop(i, wait=True)
                    end = time.time() + 90
                    while mgr.alive(i) and time.time() < end:
                        time.sleep(0.3)
                    if mgr.alive(i):
                        raise UserError(f"« {self.P.label(mgr.server(i))} » ne s'est pas arrêté : opération annulée, rien n'a été modifié.")
                job.step("Vérification des ports", 35)
                time.sleep(0.3)
                for c in changes:
                    if not port_free(c["to"]):
                        raise UserError(f"Le port {c['to']} est encore occupé : opération annulée, rien n'a été modifié.")
                job.step("Mise à jour de la configuration", 50)
                done = []
                try:
                    for c in sorted(changes, key=lambda c: c["to"] == self.port):      # libérer d'abord l'ancien principal
                        self._write_port(c["id"], c["to"])
                        done.append(c)
                        job.log(f"« {c['name']} » : port {c['from']} → {c['to']}")
                except Exception:
                    for c in done:
                        self._write_port(c["id"], c["from"])
                    raise
                job.step("Redémarrage", 70)
                for i in sorted(was_running, key=lambda x: x != sid):
                    mgr.start(i, internal=True)
                    job.log(f"Redémarrage de « {self.P.label(mgr.server(i))} »")
                job.step("Vérification de la joignabilité", 85)
                verified = "configuration"
                if mgr.alive(sid):
                    end = time.time() + self.verify_timeout
                    while time.time() < end and not mc_ping(self.port):
                        mgr.tick()
                        time.sleep(0.5)
                    if not mc_ping(self.port):
                        raise UserError(f"Le nouveau serveur principal ne répond pas sur le port {self.port} : retour à la configuration précédente.")
                    verified = "joignable"
                    job.log(f"Vérifié : le serveur répond sur le port {self.port}.")
                else:
                    job.log(f"Le serveur était arrêté : il écoutera sur le port {self.port} à son prochain démarrage.")
                job.step("Terminé", 100)
                return {"changes": changes, "verified": verified}
            except Exception as e:
                job.log(f"Échec : {e}. Restauration de la situation précédente…")
                for c in changes:
                    if mgr.alive(c["id"]) and self.P.manager.server(c["id"])["port"] != c["from"]:
                        mgr.stop(c["id"], wait=True)
                        end = time.time() + 60
                        while mgr.alive(c["id"]) and time.time() < end:
                            time.sleep(0.3)
                for c in changes:
                    try:
                        if self.P.manager.server(c["id"])["port"] != c["from"]:
                            self._write_port(c["id"], c["from"])
                    except Exception as e2:      # noqa: BLE001
                        job.log(f"Restauration du port de « {c['name']} » impossible : {e2}")
                for i in was_running:
                    try:
                        if not mgr.alive(i):
                            mgr.start(i, internal=True)
                    except ProcessError as e3:
                        job.log(f"Redémarrage de « {self.P.label(mgr.server(i))} » impossible : {e3}")
                raise
            finally:
                for i in ids:
                    mgr.blocked.pop(i, None)
        finally:
            self._op.release()
