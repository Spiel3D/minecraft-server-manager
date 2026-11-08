"""Supervision Debian en LECTURE SEULE : températures, réseau, services, processus, journaux.

Aucune action système n'est exposée : les commandes lancées (systemctl show, journalctl, ip) ont des arguments fixes,
un nom d'unité pris dans une liste blanche, jamais de shell et un délai maximal.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

from . import __version__
from .errors import AppError

WATCHED_UNITS = ["minecraft-panel.service", "minecraft-panel-awake.service", "minecraft-panel-backup.timer",
                 "ssh.service", "systemd-timesyncd.service", "NetworkManager.service"]
LOG_UNITS = ["minecraft-panel.service", "minecraft-panel-backup.service", "ssh.service"]
IFACE_SKIP = re.compile(r"^(lo|veth|docker|br-|virbr)")


def _run(cmd: list[str], timeout: float = 4.0) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.SubprocessError) as e:
        return 127, str(e)


class Supervision:
    def __init__(self, panel, sysfs: Path = Path("/sys"), procfs: Path = Path("/proc"), runner=_run):
        self.P, self.sys, self.proc, self.run = panel, Path(sysfs), Path(procfs), runner
        self._net_prev: dict[str, tuple[float, int, int]] = {}
        self._cache: dict[str, tuple[float, object]] = {}

    def _cached(self, key: str, ttl: float, fn):
        t, v = self._cache.get(key, (0, None))
        if time.time() - t < ttl:
            return v
        v = fn()
        self._cache[key] = (time.time(), v)
        return v

    # ------------------------------------------------------------------ températures
    def temperatures(self) -> list[dict]:
        out = []
        for z in sorted(self.sys.glob("class/thermal/thermal_zone*")):
            try:
                c = int((z / "temp").read_text()) / 1000
                out.append({"label": (z / "type").read_text().strip(), "celsius": round(c, 1)})
            except (OSError, ValueError):
                continue
        for h in sorted(self.sys.glob("class/hwmon/hwmon*")):
            try:
                name = (h / "name").read_text().strip()
            except OSError:
                continue
            for t in sorted(h.glob("temp*_input")):
                try:
                    label_f = t.with_name(t.name.replace("_input", "_label"))
                    label = label_f.read_text().strip() if label_f.exists() else t.name.split("_")[0]
                    out.append({"label": f"{name} {label}", "celsius": round(int(t.read_text()) / 1000, 1)})
                except (OSError, ValueError):
                    continue
        seen, uniq = set(), []
        for t in out:                      # doublons thermal/hwmon
            k = (t["label"], t["celsius"])
            if k not in seen:
                seen.add(k)
                uniq.append(t)
        return uniq

    # ------------------------------------------------------------------ réseau
    def network(self) -> list[dict]:
        now = time.time()
        out = []
        try:
            lines = (self.proc / "net/dev").read_text().splitlines()[2:]
        except OSError:
            return []
        addrs = self._addresses()
        for ln in lines:
            name, _, rest = ln.partition(":")
            name = name.strip()
            if IFACE_SKIP.match(name):
                continue
            f = rest.split()
            rx, tx = int(f[0]), int(f[8])
            prev = self._net_prev.get(name)
            rx_s = tx_s = 0.0
            if prev and now > prev[0]:
                rx_s, tx_s = max(0, rx - prev[1]) / (now - prev[0]), max(0, tx - prev[2]) / (now - prev[0])
            self._net_prev[name] = (now, rx, tx)
            state = ""
            try:
                state = (self.sys / "class/net" / name / "operstate").read_text().strip()
            except OSError:
                pass
            out.append({"name": name, "state": state, "addresses": addrs.get(name, []), "rx_bytes": rx, "tx_bytes": tx,
                        "rx_per_s": round(rx_s), "tx_per_s": round(tx_s)})
        return out

    def _addresses(self) -> dict[str, list[str]]:
        def get():
            code, txt = self.run(["ip", "-4", "-o", "addr", "show"])
            res: dict[str, list[str]] = {}
            if code == 0:
                for ln in txt.splitlines():
                    p = ln.split()
                    if len(p) >= 4:
                        res.setdefault(p[1], []).append(p[3])
            return res
        return self._cached("addrs", 30, get)

    # ------------------------------------------------------------------ services
    def services(self) -> list[dict]:
        def get():
            out = []
            for u in WATCHED_UNITS:
                code, txt = self.run(["systemctl", "show", u, "-p", "LoadState,ActiveState,SubState,UnitFileState"])
                d = dict(l.split("=", 1) for l in txt.splitlines() if "=" in l) if code == 0 else {}
                if not d or d.get("LoadState") == "not-found":
                    continue                     # service absent de cette machine : on ne l'affiche pas
                out.append({"unit": u, "active": d.get("ActiveState", "unknown"), "sub": d.get("SubState", ""), "enabled": d.get("UnitFileState", "")})
            return out
        return self._cached("services", 10, get)

    # ------------------------------------------------------------------ journaux
    def logs(self, unit: str, lines: int = 100) -> dict:
        if unit not in LOG_UNITS:
            raise AppError("Journal non disponible pour cette unité.")
        lines = min(max(int(lines), 10), 300)
        code, txt = self.run(["journalctl", "-u", unit, "-n", str(lines), "--no-pager", "-o", "short-iso"], timeout=6)
        if code != 0 or "No journal files" in txt or "not seeing messages" in txt:
            return {"unit": unit, "available": False, "lines": [],
                    "hint": "Le panneau n'a pas accès au journal système. Vérifiez que le service a le groupe systemd-journal."}
        return {"unit": unit, "available": True, "lines": [re.sub(r"[\x00-\x08\x0b-\x1f]", "", l)[:400] for l in txt.splitlines()[-lines:]]}

    # ------------------------------------------------------------------ processus et vue d'ensemble
    def processes(self) -> list[dict]:
        out = []
        for o in self.P.monitor.servers_overview():
            if o["pid"]:
                out.append({"server": o["name"], "pid": o["pid"], "rss_mb": o["rss_mb"], "cpu": o["cpu"], "uptime": o["uptime"], "state": o["state"]})
        return out

    def panel_info(self) -> dict:
        rss = 0
        try:
            for ln in (self.proc / "self/status").read_text().splitlines():
                if ln.startswith("VmRSS:"):
                    rss = int(ln.split()[1]) // 1024
        except OSError:
            pass
        return {"version": __version__, "pid": os.getpid(), "rss_mb": rss, "python": ".".join(map(str, __import__("sys").version_info[:3]))}

    def overview(self) -> dict:
        return {"system": self.P.monitor.system(), "temperatures": self.temperatures(), "network": self.network(),
                "services": self.services(), "processes": self.processes(), "panel": self.panel_info()}
