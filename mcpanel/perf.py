"""Mesures de performance et statistiques de jeu (V4 §6).

- TPS (capacité à tenir 20 ticks/s) et MSPT (« latence serveur » : temps de calcul moyen d'une tick, en ms), lus avec la
  commande propre à chaque type de serveur. Si aucune commande fiable n'existe pour ce type/version, la mesure est
  déclarée « non disponible » avec la raison : jamais de valeur inventée ou approximée.
- Historique (une mesure par minute et par serveur) : TPS, MSPT, RAM, CPU, joueurs.
- Sessions de jeu (connexions / déconnexions lues dans la console) pour les statistiques Minecraft.
Les formats exacts des réponses de commandes sont analysés de façon tolérante ; ils restent à valider sur de vrais serveurs.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .process import ProcessError

PREFIX = re.compile(r"^(?:\[[^\]]*\]:?\s*){1,2}")
NUM = r"(\d+(?:[.,]\d+)?)"
JOIN = re.compile(r"\]:\s*(\.?[A-Za-z0-9_]{1,16}) joined the game")
LEFT = re.compile(r"\]:\s*(\.?[A-Za-z0-9_]{1,16}) left the game")
PLUGIN_TYPES = {"paper", "purpur", "spigot", "mohist", "magma", "arclight"}


def _f(x: str) -> float:
    return float(x.replace(",", "."))


def _vt(v: str):
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3]) or (0,)


def method_for(srv: dict) -> tuple[str | None, str]:
    """(méthode, explication). Méthode None = mesure non disponible."""
    d = Path(srv["path"])
    for sub in ("mods", "plugins"):
        if any((d / sub).glob("spark*.jar")):
            return "spark", "via le mod/plugin spark"
    t, mc = srv["server_type"], srv["mc_version"]
    if t in ("paper", "purpur"):
        return "paper", "commandes /tps et /mspt"
    if t in PLUGIN_TYPES:
        return "spigot", "commande /tps"
    if t == "forge":
        return "forge", "commande /forge tps"
    if t == "neoforge":
        return "neoforge", "commande /neoforge tps"
    if t in ("vanilla", "fabric", "quilt") and _vt(mc) >= (1, 20, 3):
        return "tick", "commande /tick query (Minecraft 1.20.3 et plus)"
    if t in ("vanilla", "fabric", "quilt"):
        return None, f"Minecraft {mc} n'a aucune commande de mesure du TPS (ajoutée en 1.20.3) : installez le mod spark pour l'obtenir."
    return None, f"Aucune commande de mesure connue pour le type « {t} » : installez spark si compatible."


COMMANDS = {"spark": ["spark tps"], "paper": ["tps", "mspt"], "spigot": ["tps"], "forge": ["forge tps"], "neoforge": ["neoforge tps"], "tick": ["tick query"]}


ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|§[0-9a-fk-or]")


def parse(method: str, text: str) -> dict:
    lines = [PREFIX.sub("", ANSI.sub("", ln)).strip() for ln in text.splitlines()]
    tps = mspt = None
    for i, ln in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if method in ("paper", "spigot") and "TPS from last" in ln:
            m = re.search(r"TPS from last[^:]*:\s*\*?" + NUM, ln)
            if m:
                tps = _f(m.group(1))
        if method == "paper" and "tick times" in ln.lower():
            m = re.search(NUM + r"\s*/\s*" + NUM + r"\s*/\s*" + NUM, nxt) or re.search(NUM + r"\s*/\s*" + NUM + r"\s*/\s*" + NUM, ln.split(":", 1)[-1])
            if m:
                mspt = _f(m.group(1))
        if method == "spark":
            if ln.startswith("TPS from last"):
                nums = re.findall(NUM, nxt)
                if len(nums) >= 3:
                    tps = _f(nums[2])
            if ln.lower().startswith("tick durations"):
                parts = [p for p in nxt.split(";") if p.strip()]
                q = re.findall(NUM, parts[-1] if parts else "")
                if len(q) >= 2:
                    mspt = _f(q[1])
        if method in ("forge", "neoforge") and ln.lower().startswith("overall"):
            m = re.search(NUM + r"\s*ms", ln)
            if m:
                mspt = _f(m.group(1))
            m = re.search(r"TPS[:\s]*" + NUM, ln) or re.search(NUM + r"\s*TPS", ln)
            if m:
                tps = _f(m.group(1))
        if method == "tick" and "verage" in ln and "ms" in ln:
            m = re.search(NUM + r"\s*ms", ln)
            if m:
                mspt = _f(m.group(1))
    if tps is None and mspt is not None:
        tps = round(min(20.0, 1000.0 / mspt) if mspt > 0 else 20.0, 2)
    if tps is not None:
        tps = min(tps, 20.0) if method != "spark" else tps
    return {"tps": tps, "mspt": mspt}


class PerfService:
    interval = 60          # secondes entre deux mesures de TPS (évite d'encombrer la console)
    wait = 3.0             # délai laissé au serveur pour répondre

    def __init__(self, panel):
        self.P = panel
        self.latest: dict[str, dict] = {}
        self._pending: dict[str, tuple] = {}
        self._last_probe: dict[str, float] = {}
        self._off: dict[str, int] = {}
        self._last_sample: dict[str, float] = {}

    def info(self, srv: dict) -> dict:
        m, why = method_for(srv)
        cur = self.latest.get(srv["id"], {})
        return {"available": m is not None, "method": why, "tps": cur.get("tps"), "mspt": cur.get("mspt"), "at": cur.get("at")}

    def tick(self):
        now = time.time()
        mgr = self.P.manager
        for srv in self.P.db.query("SELECT * FROM servers WHERE status='installed'"):
            sid = srv["id"]
            if not mgr.alive(sid) or sid not in mgr._ready:
                if sid in self._off and not mgr.alive(sid):
                    self.close_sessions(sid)
                self._pending.pop(sid, None)
                self.latest.pop(sid, None)
                self._off.pop(sid, None)
                continue
            self._read_console(srv)
            m, _ = method_for(srv)
            p = self._pending.get(sid)
            if p and now - p[1] >= self.wait:
                text, _ = mgr.read_console(srv, p[2], max_bytes=128 * 1024)
                r = parse(p[0], text)
                if r["tps"] is not None or r["mspt"] is not None:
                    self.latest[sid] = {**r, "at": now}
                self._pending.pop(sid, None)
            if m and not p and now - self._last_probe.get(sid, 0) >= self.interval:
                try:
                    off = mgr.console_path(srv).stat().st_size
                    for c in COMMANDS[m]:
                        mgr.send_command(sid, c)
                    self._pending[sid] = (m, now, off)
                    self._last_probe[sid] = now
                except (ProcessError, OSError):
                    pass
            if now - self._last_sample.get(sid, 0) >= 60:
                self._sample(srv, now)

    def _sample(self, srv: dict, now: float):
        sid = srv["id"]
        st = self.P.manager.stats(srv)
        cur = self.latest.get(sid, {})
        self.P.db.execute("INSERT INTO metrics(server_id,ts,tps,mspt,rss_mb,cpu,players) VALUES(?,?,?,?,?,?,?)",
                          (sid, now, cur.get("tps"), cur.get("mspt"), st.get("rss_mb"), st.get("cpu"), st.get("players")))
        self._last_sample[sid] = now
        if int(now) % 97 == 0:
            self.P.db.execute("DELETE FROM metrics WHERE ts < ?", (now - 7 * 86400,))

    def _read_console(self, srv: dict):
        sid = srv["id"]
        mgr = self.P.manager
        if sid not in self._off:
            try:
                self._off[sid] = mgr.console_path(srv).stat().st_size
            except OSError:
                self._off[sid] = 0
            return
        text, off = mgr.read_console(srv, self._off[sid], max_bytes=256 * 1024)
        self._off[sid] = off
        for ln in text.splitlines():
            m = JOIN.search(ln)
            if m:
                self.P.db.execute("INSERT INTO play_sessions(server_id,player,joined) VALUES(?,?,?)", (sid, m.group(1), time.time()))
                continue
            m = LEFT.search(ln)
            if m:
                self.P.db.execute("UPDATE play_sessions SET left_at=? WHERE id=(SELECT id FROM play_sessions WHERE server_id=? AND player=? AND left_at IS NULL ORDER BY id DESC LIMIT 1)",
                                  (time.time(), sid, m.group(1)))

    def close_sessions(self, sid: str):
        self.P.db.execute("UPDATE play_sessions SET left_at=? WHERE server_id=? AND left_at IS NULL", (time.time(), sid))

    def history(self, sid: str, hours: float = 24) -> list[dict]:
        since = time.time() - hours * 3600
        rows = self.P.db.query("SELECT ts,tps,mspt,rss_mb,cpu,players FROM metrics WHERE server_id=? AND ts>=? ORDER BY ts", (sid, since))
        if len(rows) > 400:                       # sous-échantillonnage pour l'affichage
            step = len(rows) / 400
            rows = [rows[int(i * step)] for i in range(400)]
        return rows
