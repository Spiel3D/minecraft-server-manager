"""Événements (démarrage, arrêt, plantage) conservés pour l'API et envoyés aux webhooks (format Discord pris en charge)."""
from __future__ import annotations

import ipaddress
import json
import queue
import threading
import urllib.parse

from .errors import AppError, NotFound
from .util import new_id, now

KINDS = {
    "server.starting": ("démarre", 0xE0A82E),
    "server.started": ("est ouvert : les joueurs peuvent se connecter", 0x16A37F),
    "server.start_failed": ("n'a pas pu démarrer", 0xD23B3B),
    "server.stopping": ("s'arrête", 0xE0A82E),
    "server.stopped": ("est arrêté normalement", 0x7A8294),
    "server.unexpected_stop": ("s'est arrêté de façon inattendue", 0xD23B3B),
    "server.crashed": ("a planté", 0xD23B3B),
    "server.auto_restart": ("redémarre automatiquement", 0xE0A82E),
    "server.unreachable": ("ne répond plus (connexions impossibles)", 0xD23B3B),
    "server.reachable": ("répond de nouveau", 0x16A37F),
    "system.ram_low": ("mémoire de la machine presque saturée", 0xD23B3B),
    "system.disk_low": ("disque presque plein", 0xD23B3B),
    "backup.failed": ("sauvegarde en échec", 0xD23B3B),
    "log.critical": ("erreurs critiques dans les journaux", 0xE0A82E),
}
SEVERITY = {"server.started": "ok", "server.reachable": "ok", "server.stopped": "info", "server.starting": "info", "server.stopping": "info",
            "server.auto_restart": "warning", "log.critical": "warning", "server.start_failed": "critical", "server.unexpected_stop": "critical",
            "server.crashed": "critical", "server.unreachable": "critical", "system.ram_low": "critical", "system.disk_low": "critical",
            "backup.failed": "critical"}
KEEP_EVENTS = 3000


def validate_webhook_url(url: str) -> str:
    url = url if isinstance(url, str) else ""
    u = urllib.parse.urlparse(url.strip())
    if u.scheme != "https" or not u.hostname or len(url) > 500:
        raise AppError("L'adresse du webhook doit commencer par https://.")
    host = u.hostname.lower()
    if host == "localhost" or host.endswith(".local"):
        raise AppError("Adresse de webhook locale refusée.")
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            raise AppError("Adresse de webhook privée refusée.")
    except ValueError:
        pass
    return url.strip()


def is_discord(url: str) -> bool:
    u = urllib.parse.urlparse(url)
    return (u.hostname or "") in ("discord.com", "discordapp.com", "canary.discord.com", "ptb.discord.com") and u.path.startswith("/api/webhooks/")


class EventBus:
    def __init__(self, panel):
        self.P, self.db = panel, panel.db
        self._last: dict[str, str] = {}
        self._primed = False
        self._q: queue.Queue = queue.Queue(maxsize=200)
        self._sent: dict[tuple, float] = {}           # anti-spam : (webhook, type, serveur) -> dernier envoi
        self._worker: threading.Thread | None = None
        self._count = 0

    # ---------------------------------------------------------------- émission
    def emit(self, kind: str, sid: str | None, name: str | None, data: dict | None = None) -> int:
        eid = self.db.execute("INSERT INTO events(ts,type,server_id,server_name,data) VALUES(?,?,?,?,?)",
                              (now(), kind, sid, name, json.dumps(data or {})))
        self._count += 1
        if self._count % 100 == 0:
            self.db.execute("DELETE FROM events WHERE id <= (SELECT MAX(id) FROM events) - ?", (KEEP_EVENTS,))
        ev = {"id": eid, "type": kind, "server_id": sid, "server_name": name, "data": data or {}, "ts": now()}
        muted = []
        if sid:
            row = self.db.one("SELECT notify_prefs FROM servers WHERE id=?", (sid,))
            muted = ((row or {}).get("notify_prefs") or {}).get("muted", []) if row else []
        for wh in self.db.query("SELECT * FROM webhooks WHERE enabled=1"):
            if wh["kinds"] and kind not in wh["kinds"]:
                continue
            if sid and wh.get("server_ids") and sid not in wh["server_ids"]:
                continue
            if kind in muted:
                continue
            key = (wh["id"], kind, sid)
            if now() - self._sent.get(key, 0) < int(wh.get("cooldown_s") or 0):
                continue                                   # anti-spam : même événement, même serveur, trop rapproché
            self._sent[key] = now()
            try:
                self._q.put_nowait((wh, ev))
            except queue.Full:
                pass
        self._ensure_worker()
        return eid

    def observe(self, servers: list[dict]):
        """Compare les états à ceux du passage précédent et émet les événements de transition."""
        for o in servers:
            sid, st = o["id"], o["state"]
            prev = self._last.get(sid)
            if prev is None and self._primed:
                prev = "stopped"          # serveur apparu après le démarrage du panneau : il était arrêté
            self._last[sid] = st
            if prev is None or prev == st:
                continue
            data = {"players": o.get("players"), "max_players": o.get("max_players"), "mc_version": o["mc_version"], "port": o["port"]}
            kind = None
            if st == "starting":
                kind = "server.starting"
            elif st == "running":
                kind = "server.started"
            elif st == "stopping":
                kind = "server.stopping"
            elif st == "stopped" and prev in ("running", "starting", "stopping"):
                kind = "server.stopped"
            # plantages, échecs de démarrage et redémarrages : émis directement par le gestionnaire de processus (avec rapport)
            if kind:
                self.emit(kind, sid, o["name"], data)
                if kind == "server.stopped" and hasattr(self.P, "on_server_stopped"):
                    try:
                        self.P.on_server_stopped(sid)
                    except Exception:  # noqa: BLE001
                        pass
        self._primed = True               # le premier passage ne fait que mémoriser l'état (pas d'événement au démarrage du panneau)
        for gone in set(self._last) - {o["id"] for o in servers}:
            self._last.pop(gone, None)

    def list(self, since: int = 0, limit: int = 100, kinds: list[str] | None = None, server_ids: list[str] | None = None) -> list[dict]:
        rows = self.db.query("SELECT * FROM events WHERE id>? ORDER BY id ASC LIMIT ?", (since, min(max(limit, 1), 500) * (1 if not (kinds or server_ids is not None) else 5)))
        out = []
        for r in rows:
            if kinds and r["type"] not in kinds:
                continue
            if server_ids is not None and r["server_id"] not in server_ids:
                continue
            out.append({"id": r["id"], "type": r["type"], "server_id": r["server_id"], "server_name": r["server_name"], "ts": r["ts"], "data": r["data"]})
        return out[:min(max(limit, 1), 500)]

    # ---------------------------------------------------------------- webhooks
    def webhooks(self) -> list[dict]:
        rows = self.db.query("SELECT * FROM webhooks ORDER BY created_at")
        return [{"id": r["id"], "name": r["name"], "url_hint": self._hint(r["url"]), "kinds": r["kinds"], "enabled": bool(r["enabled"]),
                 "server_ids": r.get("server_ids") or [], "cooldown_s": r.get("cooldown_s", 60),
                 "discord": is_discord(r["url"]), "last_status": r["last_status"], "last_at": r["last_at"]} for r in rows]

    @staticmethod
    def _hint(url: str) -> str:
        u = urllib.parse.urlparse(url)
        return f"{u.scheme}://{u.hostname}/…{url[-6:]}"          # le jeton du webhook n'est jamais ré-affiché

    def add_webhook(self, name: str, url: str, kinds: list[str] | None = None, server_ids: list | None = None, cooldown_s=60) -> dict:
        name = name.strip() if isinstance(name, str) else ""
        if not 1 <= len(name) <= 60:
            raise AppError("Donnez un nom à la notification.")
        kinds = [k for k in (kinds if isinstance(kinds, list) else []) if k in KINDS]
        if self.db.one("SELECT COUNT(*) AS n FROM webhooks", ())["n"] >= 10:
            raise AppError("10 notifications maximum.")
        wid = new_id()
        sids = self._clean_sids(server_ids)
        self.db.execute("INSERT INTO webhooks(id,name,url,kinds,enabled,created_at,server_ids,cooldown_s) VALUES(?,?,?,?,1,?,?,?)",
                        (wid, name, validate_webhook_url(url), json.dumps(kinds), now(), json.dumps(sids), self._clean_cd(cooldown_s)))
        return next(w for w in self.webhooks() if w["id"] == wid)

    def _clean_sids(self, v) -> list:
        if not isinstance(v, list):
            return []
        known = {r["id"] for r in self.db.query("SELECT id FROM servers")}
        return [x for x in v if isinstance(x, str) and x in known]

    @staticmethod
    def _clean_cd(v) -> int:
        try:
            return max(0, min(86400, int(v)))
        except (TypeError, ValueError):
            return 60

    def update_webhook(self, wid: str, data: dict) -> dict:
        wh = self.db.one("SELECT * FROM webhooks WHERE id=?", (wid,))
        if not wh:
            raise NotFound("Notification introuvable.")
        if "enabled" in data:
            self.db.execute("UPDATE webhooks SET enabled=? WHERE id=?", (1 if data["enabled"] else 0, wid))
        if "kinds" in data:
            self.db.execute("UPDATE webhooks SET kinds=? WHERE id=?", (json.dumps([k for k in (data["kinds"] if isinstance(data["kinds"], list) else []) if k in KINDS]), wid))
        if "server_ids" in data:
            self.db.execute("UPDATE webhooks SET server_ids=? WHERE id=?", (json.dumps(self._clean_sids(data["server_ids"])), wid))
        if "cooldown_s" in data:
            self.db.execute("UPDATE webhooks SET cooldown_s=? WHERE id=?", (self._clean_cd(data["cooldown_s"]), wid))
        if "name" in data and str(data["name"]).strip():
            self.db.execute("UPDATE webhooks SET name=? WHERE id=?", (str(data["name"]).strip()[:60], wid))
        return next(w for w in self.webhooks() if w["id"] == wid)

    def notifications(self, username: str, limit: int = 60) -> dict:
        seen = int(self.db.get_setting(f"notif_seen:{username}") or 0)
        rows = self.db.query("SELECT * FROM events ORDER BY id DESC LIMIT ?", (min(max(limit, 1), 200),))
        items = [{"id": r["id"], "type": r["type"], "server_id": r["server_id"], "server_name": r["server_name"], "ts": r["ts"], "data": r["data"],
                  "label": KINDS.get(r["type"], (r["type"], 0))[0], "severity": SEVERITY.get(r["type"], "info"), "unread": r["id"] > seen} for r in rows]
        unread = self.db.one("SELECT COUNT(*) c FROM events WHERE id>? AND type IN (%s)" % ",".join("?" * len(SEVERITY)), (seen, *SEVERITY))["c"]
        important = self.db.one("SELECT COUNT(*) c FROM events WHERE id>? AND type IN (%s)" % ",".join("?" * len([k for k, v in SEVERITY.items() if v == "critical"])),
                                (seen, *[k for k, v in SEVERITY.items() if v == "critical"]))["c"]
        return {"items": items, "unread": unread, "critical_unread": important, "last_seen": seen}

    def mark_seen(self, username: str, upto: int | None = None):
        mx = self.db.one("SELECT MAX(id) m FROM events")["m"] or 0
        self.db.set_setting(f"notif_seen:{username}", str(min(int(upto), mx) if upto else mx))

    def delete_webhook(self, wid: str):
        if not self.db.one("SELECT 1 FROM webhooks WHERE id=?", (wid,)):
            raise NotFound("Notification introuvable.")
        self.db.execute("DELETE FROM webhooks WHERE id=?", (wid,))

    def test_webhook(self, wid: str) -> str:
        wh = self.db.one("SELECT * FROM webhooks WHERE id=?", (wid,))
        if not wh:
            raise NotFound("Notification introuvable.")
        return self._deliver(wh, {"id": 0, "type": "server.started", "server_id": None, "server_name": "Test du panneau", "ts": now(), "data": {"players": 0}})

    # ---------------------------------------------------------------- envoi
    def _ensure_worker(self):
        if self._worker and self._worker.is_alive():
            return
        self._worker = threading.Thread(target=self._run, name="webhooks", daemon=True)
        self._worker.start()

    def _run(self):
        while True:
            try:
                wh, ev = self._q.get(timeout=30)
            except queue.Empty:
                return
            self._deliver(wh, ev)

    @staticmethod
    def payload(url: str, ev: dict) -> dict:
        label, color = KINDS.get(ev["type"], (ev["type"], 0x7A8294))
        title = f"{ev['server_name'] or 'Serveur'} {label}"
        d = ev.get("data") or {}
        if is_discord(url):
            fields = []
            if d.get("players") is not None:
                fields.append({"name": "Joueurs", "value": f"{d['players']}/{d.get('max_players') or '?'}", "inline": True})
            if d.get("mc_version"):
                fields.append({"name": "Minecraft", "value": str(d["mc_version"]), "inline": True})
            if d.get("confirmed"):
                fields.append({"name": "Constaté", "value": "\n".join("• " + x for x in d["confirmed"])[:1000]})
            if d.get("possible"):
                fields.append({"name": "Cause possible (non certaine)", "value": "\n".join("• " + x for x in d["possible"])[:1000]})
            if d.get("suspects"):
                fields.append({"name": "Mods à vérifier", "value": ", ".join(d["suspects"])[:1000]})
            for k, lbl in (("reason", "Raison"), ("line", "Dernière erreur")):
                if d.get(k):
                    fields.append({"name": lbl, "value": str(d[k])[:1000]})
            if d.get("count"):
                fields.append({"name": "Erreurs", "value": str(d["count"]), "inline": True})
            return {"username": "Minecraft Server Manager", "embeds": [{"title": title[:250], "color": color, "fields": fields[:10]}]}
        return {"event": ev["type"], "server_id": ev["server_id"], "server_name": ev["server_name"], "message": title, "ts": ev["ts"], "data": d}

    def _deliver(self, wh: dict, ev: dict) -> str:
        status = "ok"
        try:
            body = json.dumps(self.payload(wh["url"], ev)).encode()
            with self.P.http._open(wh["url"], {"Content-Type": "application/json"}, body) as r:
                r.read(1024)
        except Exception as e:  # noqa: BLE001 — un webhook défaillant ne doit jamais perturber le panneau
            status = "erreur : " + str(e)[:120].replace(wh["url"], "[adresse masquée]")
        self.db.execute("UPDATE webhooks SET last_status=?, last_at=? WHERE id=?", (status, now(), wh["id"]))
        return status
