"""Statistiques système (lues dans /proc), budget mémoire et alertes."""
from __future__ import annotations

import math
import os
import shutil
import time
from pathlib import Path

from .config import Settings
from .db import Database
from .util import is_mountpoint, meminfo, now


class Alerts:
    def __init__(self, db: Database):
        self.db = db
        self.on_new = None            # appelé (clé, niveau, message) quand une alerte apparaît (et non à chaque mise à jour)

    def raise_(self, key: str, level: str, message: str):
        cur = self.db.one("SELECT id, message FROM alerts WHERE key=? AND cleared_at IS NULL", (key,))
        if cur:
            if cur["message"] != message:
                self.db.execute("UPDATE alerts SET message=?, level=? WHERE id=?", (message, level, cur["id"]))
            return
        self.db.execute("INSERT INTO alerts(key,level,message,created_at) VALUES(?,?,?,?)", (key, level, message, now()))
        if self.on_new:
            try:
                self.on_new(key, level, message)
            except Exception:  # noqa: BLE001
                pass

    def clear(self, key: str):
        self.db.execute("UPDATE alerts SET cleared_at=? WHERE key=? AND cleared_at IS NULL", (now(), key))

    def active(self) -> list[dict]:
        return self.db.query("SELECT * FROM alerts WHERE cleared_at IS NULL ORDER BY created_at DESC")

    def recent(self, limit: int = 50) -> list[dict]:
        return self.db.query("SELECT * FROM alerts ORDER BY created_at DESC LIMIT ?", (limit,))

    def ack(self, aid: int):
        self.db.execute("UPDATE alerts SET ack=1 WHERE id=?", (aid,))


class Monitor:
    def __init__(self, settings: Settings, db: Database, manager, alerts: Alerts):
        self.s, self.db, self.mgr, self.alerts = settings, db, manager, alerts
        self._cpu_prev: tuple[int, int] | None = None
        self._last_eval = 0.0
        self.snapshot: dict = {}

    def ram_reserve_mb(self) -> int:
        v = self.db.get_setting("ram_reserve_mb")
        try:
            return int(v) if v else self.s.ram_reserve_mb
        except ValueError:
            return self.s.ram_reserve_mb

    def overhead_pct(self) -> int:
        v = self.db.get_setting("ram_overhead_pct")
        try:
            return max(0, min(100, int(v))) if v else self.s.ram_overhead_pct
        except ValueError:
            return self.s.ram_overhead_pct

    def effective_mb(self, xmx_mb: int) -> int:
        return math.ceil(xmx_mb * (100 + self.overhead_pct()) / 100)

    def ram_budget(self, exclude_sid: str | None = None) -> dict:
        """Budget RAM.

        - ``max_xmx_mb`` (plafond d'un serveur à la création/modification) part TOUJOURS de la RAM totale moins la réserve
          système, sans dépendre des serveurs en marche. La marge Java (mémoire du processus au-delà de -Xmx) est déduite,
          pour qu'un serveur réglé au maximum puisse réellement démarrer seul : le plafond et le contrôle au démarrage
          utilisent la même règle.
        - ``free_budget_mb`` (budget restant, serveurs actifs déduits) sert uniquement au contrôle au démarrage.
        - ``forecast_*`` : prévisionnel de tous les serveurs configurés (même arrêtés), avertissement sans blocage.
        """
        mi = meminfo()
        total = mi.get("MemTotal", 0)
        reserve = self.ram_reserve_mb()
        budget = max(0, total - reserve)
        reserved = sum(self.effective_mb(r) for r in self._running_xmx(exclude_sid))
        free = max(0, budget - reserved)
        configured = [r["ram_max_mb"] for r in self.db.query("SELECT id, ram_max_mb FROM servers") if r["id"] != exclude_sid]
        conf_mb = sum(configured)
        conf_eff = sum(self.effective_mb(x) for x in configured)
        return {"total_mb": total, "reserve_mb": reserve, "budget_mb": budget, "reserved_mb": reserved,
                "free_budget_mb": free, "max_xmx_mb": int(budget * 100 / (100 + self.overhead_pct())), "overhead_pct": self.overhead_pct(),
                "available_mb": mi.get("MemAvailable", 0),
                "forecast_configured_mb": conf_mb, "forecast_effective_mb": conf_eff,
                "forecast_free_mb": budget - conf_eff, "forecast_overcommit": conf_eff > budget,
                "forecast_servers": len(configured),
                # V4 §1 : vue claire « utilisée / attribuée / disponible / restante »
                "used_mb": round(sum(self._rss(sid) for sid in self._running_ids(exclude_sid))),
                "assigned_mb": sum(self._running_xmx(exclude_sid)),
                "remaining_mb": free,
                "presets": self.presets(int(budget * 100 / (100 + self.overhead_pct())))}

    PRESETS_MB = ((10240, "10 Go"), (12288, "12 Go"))

    def presets(self, max_mb: int) -> list[dict]:
        """Propositions de création : 10 Go, 12 Go, maximum. Une option qui dépasse le plafond n'est pas proposée (ok=False)."""
        out = [{"mb": mb, "label": lbl, "ok": mb <= max_mb, "reason": "" if mb <= max_mb else f"dépasse le maximum ({max_mb} Mo)"} for mb, lbl in self.PRESETS_MB]
        mx = (max_mb // 256) * 256
        out.append({"mb": mx, "label": f"Maximum ({mx / 1024:.1f} Go)".replace(".", ","), "ok": mx >= 512, "reason": "" if mx >= 512 else "RAM insuffisante"})
        return out

    def _running_ids(self, exclude_sid):
        return [r["id"] for r in self.db.query("SELECT id FROM servers") if r["id"] != exclude_sid and self.mgr.alive(r["id"])]

    def _rss(self, sid) -> float:
        from .process import rss_mb
        run = self.mgr.alive(sid)
        return rss_mb(run["pid"]) if run else 0.0

    def forecast_with(self, xmx_mb: int, exclude_sid: str | None = None) -> dict:
        """Prévisionnel si un serveur de ``xmx_mb`` Mo existait en plus (ou remplaçait ``exclude_sid``)."""
        b = self.ram_budget(exclude_sid=exclude_sid)
        eff = b["forecast_effective_mb"] + self.effective_mb(xmx_mb)
        over = max(0, eff - b["budget_mb"])
        return {"effective_mb": eff, "budget_mb": b["budget_mb"], "overcommit": over > 0, "over_mb": over,
                "message": (f"Prévisionnel : tous vos serveurs configurés demanderaient ≈ {eff} Mo (marge Java comprise) pour "
                            f"{b['budget_mb']} Mo utilisables. Ils ne pourront pas tourner tous en même temps ; "
                            "le panneau refusera simplement un démarrage si la RAM manque.") if over else ""}

    def _running_xmx(self, exclude_sid):
        out = []
        for srv in self.db.query("SELECT id, ram_max_mb FROM servers"):
            if srv["id"] != exclude_sid and self.mgr.alive(srv["id"]):
                out.append(srv["ram_max_mb"])
        return out

    def check_start_ram(self, srv: dict):
        from .process import ProcessError
        b = self.ram_budget(exclude_sid=srv["id"])
        need = self.effective_mb(srv["ram_max_mb"])
        if need > b["free_budget_mb"]:
            raise ProcessError(
                f"Mémoire insuffisante : ce serveur demande {srv['ram_max_mb']} Mo (≈ {need} Mo avec la marge Java) "
                f"mais il ne reste que {b['free_budget_mb']} Mo de budget "
                f"(RAM {b['total_mb']} Mo − réserve système {b['reserve_mb']} Mo − serveurs actifs {b['reserved_mb']} Mo). "
                "Arrêtez un autre serveur ou réduisez la RAM de celui-ci.")

    def _cpu_percent(self) -> float:
        try:
            with open("/proc/stat") as f:
                parts = [int(x) for x in f.readline().split()[1:8]]
        except (OSError, ValueError):
            return 0.0
        idle, total = parts[3] + parts[4], sum(parts)
        prev, self._cpu_prev = self._cpu_prev, (idle, total)
        if not prev or total == prev[1]:
            return 0.0
        return round(100.0 * (1 - (idle - prev[0]) / (total - prev[1])), 1)

    @staticmethod
    def _disk(path: Path) -> dict:
        try:
            u = shutil.disk_usage(path)
            return {"path": str(path), "total": u.total, "used": u.used, "free": u.free,
                    "percent": round(100 * u.used / u.total, 1) if u.total else 0}
        except OSError:
            return {"path": str(path), "total": 0, "used": 0, "free": 0, "percent": 0}

    def system(self) -> dict:
        mi = meminfo()
        total, avail = mi.get("MemTotal", 0), mi.get("MemAvailable", 0)
        try:
            load = os.getloadavg()
        except OSError:
            load = (0, 0, 0)
        try:
            up = float(open("/proc/uptime").read().split()[0])
        except OSError:
            up = 0
        return {
            "cpu_percent": self._cpu_percent(), "cpus": os.cpu_count() or 1, "load": [round(x, 2) for x in load],
            "mem": {"total_mb": total, "available_mb": avail, "used_mb": total - avail,
                    "swap_total_mb": mi.get("SwapTotal", 0), "swap_used_mb": mi.get("SwapTotal", 0) - mi.get("SwapFree", 0)},
            "disk_ssd": self._disk(Path("/")), "disk_data": self._disk(self.s.data_root),
            "data_mounted": is_mountpoint(self.s.data_root) if self.s.require_mount else self.s.data_root.is_dir(),
            "uptime": int(up), "ts": time.time(), "ram_budget": self.ram_budget(),
        }

    def servers_overview(self) -> list[dict]:
        out = []
        try:
            primary = int(self.db.get_setting("primary_port") or 25565)
        except ValueError:
            primary = 25565
        for srv in self.db.query("SELECT * FROM servers ORDER BY COALESCE(NULLIF(display_name,''),name) COLLATE NOCASE"):
            st = self.mgr.stats(srv)
            out.append({
                "id": srv["id"], "name": srv.get("display_name") or srv["name"], "slug": srv["name"], "description": srv["description"], "mc_version": srv["mc_version"],
                "server_type": srv["server_type"], "loader_version": srv["loader_version"], "port": srv["port"],
                "ram_min_mb": srv["ram_min_mb"], "ram_max_mb": srv["ram_max_mb"], "java_major": srv["java_major"],
                "status": srv["status"], "error": srv["error"], "eula_accepted": bool(srv["eula_accepted"]),
                "autostart": bool(srv["autostart"]), "auto_restart": bool(srv["auto_restart"]), "primary": srv["port"] == primary, **st, **self._perf(srv),
            })
        return out

    def _perf(self, srv: dict) -> dict:
        p = getattr(self, "perf", None)
        if not p or srv["status"] != "installed":
            return {"tps": None, "mspt": None, "perf_available": False, "perf_method": ""}
        i = p.info(srv)
        return {"tps": i["tps"], "mspt": i["mspt"], "perf_available": i["available"], "perf_method": i["method"]}

    def tick(self):
        """Évalue le système, met à jour l'instantané et les alertes (au plus toutes les 10 s)."""
        sysd = self.system()
        self.snapshot = {"system": sysd, "servers": self.servers_overview()}
        if time.time() - self._last_eval < 10:
            return
        self._last_eval = time.time()
        a, m = self.alerts, sysd["mem"]
        if m["available_mb"] < 400:
            a.raise_("ram_low", "critical", f"RAM disponible très faible : {m['available_mb']} Mo.")
        elif m["available_mb"] < 800:
            a.raise_("ram_low", "warning", f"RAM disponible faible : {m['available_mb']} Mo.")
        else:
            a.clear("ram_low")
        dd = sysd["disk_data"]
        if dd["total"] and (dd["free"] < 5 * 1024 ** 3 or dd["percent"] > 95):
            a.raise_("disk_data", "critical", f"Disque de données presque plein ({dd['percent']} %).")
        elif dd["total"] and (dd["free"] < 20 * 1024 ** 3 or dd["percent"] > 90):
            a.raise_("disk_data", "warning", f"Disque de données bientôt plein ({dd['percent']} %).")
        else:
            a.clear("disk_data")
        ss = sysd["disk_ssd"]
        if ss["total"] and ss["free"] < 5 * 1024 ** 3:
            a.raise_("disk_ssd", "warning", f"Disque système presque plein ({ss['percent']} %).")
        else:
            a.clear("disk_ssd")
        if self.s.require_mount and not sysd["data_mounted"]:
            a.raise_("mount", "critical", f"{self.s.data_root} n'est pas monté : aucune opération sur les serveurs n'est possible.")
        else:
            a.clear("mount")
