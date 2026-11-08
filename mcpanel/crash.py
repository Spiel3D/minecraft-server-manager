"""Rapports de plantage (V4 §5).

À chaque arrêt inattendu : extrait de console, fichier crash-report éventuel, trace d'exception, et une analyse prudente.
L'analyse sépare toujours :
- ``confirmed`` : ce que les fichiers disent explicitement (description du crash-report, exception levée, message d'erreur exact) ;
- ``possible``  : des hypothèses plausibles, présentées comme telles ;
- ``suspects``  : mods ou composants cités par un indice fiable (champ « Suspected Mods », erreur de chargement d'un mod, mixin en échec).
Rien n'est jamais présenté comme certain sans indice écrit.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .util import now

MAX_REPORT = 200 * 1024

_EXC = re.compile(r"^\s*((?:[a-zA-Z_$][\w$]*\.)+[A-Z][\w$]*(?:Exception|Error|Throwable))(?::\s*(.*))?$", re.M)
_CAUSED = re.compile(r"^\s*Caused by:\s*((?:[\w$]+\.)+[\w$]+)(?::\s*(.*))?$", re.M)
_LOGERR = re.compile(r"^.*?/(ERROR|FATAL)\]:?\s*(.*)$", re.M)
_SUSPECT = re.compile(r"Suspected Mods?:\s*(.+)", re.I)
_MODFILE = re.compile(r"Mod File:\s*(?:.*/)?([^/\s]+\.jar)", re.I)
_MODLOAD = re.compile(r"-- Mod loading issue for:\s*(\S+)\s*--", re.I)
_MIXIN = re.compile(r"Mixin \[([^\]]+)\] from mod (\S+) failed", re.I)
_FABRIC_DEP = re.compile(r"(?:Mod|Incompatible mods? found!|requires|Unmet dependency).*", re.I)


def find_crash_report(server_dir: Path, since: float) -> Path | None:
    d = server_dir / "crash-reports"
    if not d.is_dir():
        return None
    cands = [p for p in d.glob("*.txt") if p.is_file() and not p.is_symlink() and p.stat().st_mtime >= since - 5]
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def analyze(console: str, report: str | None) -> dict:
    confirmed, possible, suspects = [], [], []
    text = (report or "") + "\n" + console
    if report:
        m = re.search(r"^Description:\s*(.+)$", report, re.M)
        if m:
            confirmed.append(f"Le crash-report indique : « {m.group(1).strip()} ».")
    exc = None
    for src in ((report or ""), console):
        m = _EXC.search(src)
        if m:
            exc = (m.group(1), (m.group(2) or "").strip())
            break
    if exc:
        confirmed.append(f"Exception levée : {exc[0]}" + (f" — {exc[1][:300]}" if exc[1] else ""))
    causes = [(c.group(1), (c.group(2) or "").strip()) for c in _CAUSED.finditer(text)]
    if causes:
        root = causes[-1]
        confirmed.append(f"Cause racine de la trace : {root[0]}" + (f" — {root[1][:300]}" if root[1] else ""))
    low = text.lower()
    if "outofmemoryerror" in low:
        possible.append("Mémoire Java épuisée (OutOfMemoryError) : la RAM maximale du serveur est probablement trop basse pour ce monde ou ces mods.")
    if "address already in use" in low or "failed to bind to port" in low:
        confirmed.append("Le port du serveur était déjà utilisé au démarrage (« Failed to bind to port » / « Address already in use »).")
    if "you need to agree to the eula" in low:
        confirmed.append("Le contrat EULA n'est pas accepté.")
    if "unsupported class file major version" in low or "has been compiled by a more recent version of the java runtime" in low:
        possible.append("Version de Java incompatible avec un mod ou le serveur (classe compilée pour un Java plus récent).")
    if "incompatible mods found" in low or "missing or unsupported mandatory dependencies" in low or "unmet dependency" in low:
        possible.append("Dépendance de mod manquante ou incompatible (voir les lignes « requires » / « depends » de la trace).")
    if "exception in server tick loop" in low:
        possible.append("Erreur pendant une tick de jeu : souvent provoquée par un mod, un bloc ou une entité corrompus dans le monde.")
    if "watchdog" in low and "single server tick took" in low:
        confirmed.append("Le chien de garde a arrêté le serveur : une tick a duré trop longtemps (max-tick-time).")
        possible.append("Surcharge ou blocage (boucle infinie d'un mod, génération de monde trop lourde, disque lent).")
    for m in _SUSPECT.finditer(text):
        v = m.group(1).strip()
        if v and v.lower() not in ("none", "unknown", "nothing"):
            suspects.append({"name": v[:120], "evidence": "Champ « Suspected Mods » du crash-report"})
    for m in _MODLOAD.finditer(text):
        suspects.append({"name": m.group(1), "evidence": "Erreur de chargement signalée pour ce mod"})
    for m in _MIXIN.finditer(text):
        suspects.append({"name": m.group(2), "evidence": f"Mixin en échec : {m.group(1)[:80]}"})
    for m in _MODFILE.finditer(report or ""):
        suspects.append({"name": m.group(1), "evidence": "Fichier de mod cité par le crash-report"})
    seen, uniq = set(), []
    for s in suspects:
        k = s["name"].lower()
        if k not in seen:
            seen.add(k)
            uniq.append(s)
    errors = [f"[{m.group(1)}] {m.group(2).strip()[:300]}" for m in _LOGERR.finditer(console)][-12:]
    trace = ""
    m = _EXC.search(report or "") or _EXC.search(console)
    if m:
        src = (report or "") if _EXC.search(report or "") else console
        start = src.find(m.group(0))
        trace = "\n".join(src[start:start + 8000].strip("\n").splitlines()[:60])
    if not confirmed and not possible:
        possible.append("Aucune erreur écrite dans les journaux : le processus a peut-être été tué de l'extérieur "
                        "(manque de RAM sur la machine, arrêt forcé du système). Vérifiez « journalctl -k » pour un éventuel « Out of memory ».")
    return {"confirmed": confirmed, "possible": possible, "suspects": uniq[:10], "errors": errors, "trace": trace}


class CrashReports:
    def __init__(self, panel):
        self.P = panel

    def capture(self, srv: dict, console_tail: str, started: float, during_start: bool) -> dict:
        d = Path(srv["path"])
        rep_path = find_crash_report(d, started or (time.time() - 3600))
        report = None
        if rep_path:
            try:
                report = rep_path.read_bytes()[:MAX_REPORT].decode("utf-8", "replace")
            except OSError:
                report = None
        a = analyze(console_tail[-60000:], report)
        kind = "server.start_failed" if during_start else ("server.crashed" if (report or a["confirmed"]) else "server.unexpected_stop")
        excerpt = "\n".join(console_tail.splitlines()[-200:])
        cid = self.P.db.execute(
            "INSERT INTO crash_reports(server_id,server_name,ts,kind,analysis,console,report_name,report) VALUES(?,?,?,?,?,?,?,?)",
            (srv["id"], srv.get("display_name") or srv["name"], now(), kind, json.dumps(a, ensure_ascii=False), excerpt,
             rep_path.name if rep_path else None, report))
        self.P.db.execute("DELETE FROM crash_reports WHERE server_id=? AND id NOT IN (SELECT id FROM crash_reports WHERE server_id=? ORDER BY id DESC LIMIT 30)",
                          (srv["id"], srv["id"]))
        return {"id": cid, "kind": kind, "analysis": a, "report_name": rep_path.name if rep_path else None}

    def list(self, sid: str | None = None, limit: int = 50) -> list[dict]:
        q = "SELECT id,server_id,server_name,ts,kind,analysis,report_name FROM crash_reports" + (" WHERE server_id=?" if sid else "") + " ORDER BY id DESC LIMIT ?"
        rows = self.P.db.query(q, ((sid, limit) if sid else (limit,)))
        for r in rows:
            r["analysis"] = json.loads(r["analysis"] or "{}")
        return rows

    def get(self, cid: int) -> dict | None:
        r = self.P.db.one("SELECT * FROM crash_reports WHERE id=?", (cid,))
        if r:
            r["analysis"] = json.loads(r["analysis"] or "{}")
        return r
