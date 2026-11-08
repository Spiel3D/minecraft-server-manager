"""Gestion des processus Minecraft.

Chaque serveur est un processus Java détaché (sa propre session) :
 - stdin : FIFO ouvert en lecture/écriture par le processus lui-même (jamais d'EOF) ;
 - stdout/stderr : fichier logs/panel-console.log (ajout) ;
 - état : fichier JSON dans run_dir (pid + heure de démarrage pour éviter la réutilisation d'un PID).
Le panneau peut être redémarré sans arrêter les serveurs : ils sont réadoptés au démarrage.
"""
from __future__ import annotations

import errno
import gzip
import json
import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import threading
import time
from pathlib import Path

from .config import Settings
from .db import Database

CLK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
DONE_RE = re.compile(r"Done \([\d.,]+s\)!")
MAX_CONSOLE = 20 * 1024 * 1024
LIVE_ROTATE = 50 * 1024 * 1024


class ProcessError(Exception):
    """Erreur de gestion de processus (message affichable)."""


def read_stat(pid: int) -> dict | None:
    try:
        data = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    rp = data.rfind(")")
    rest = data[rp + 2:].split()
    try:
        return {"state": rest[0], "utime": int(rest[11]), "stime": int(rest[12]), "start": int(rest[19])}
    except (IndexError, ValueError):
        return None


def rss_mb(pid: int) -> float:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    except (OSError, ValueError):
        pass
    return 0.0


def port_free(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _varint(n: int) -> bytes:
    n &= 0xFFFFFFFF
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def _read_varint(s: socket.socket) -> int:
    num = 0
    for i in range(5):
        b = s.recv(1)
        if not b:
            raise OSError("connexion fermée")
        num |= (b[0] & 0x7F) << (7 * i)
        if not b[0] & 0x80:
            return num
    raise OSError("varint trop long")


def _recv_exact(s: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        c = s.recv(n - len(buf))
        if not c:
            raise OSError("connexion fermée")
        buf += c
    return buf


def mc_status(port: int, host: str = "127.0.0.1", timeout: float = 1.5) -> dict | None:
    """Ping de liste de serveurs : {online, max, names (échantillon, souvent incomplet), version, motd} ou None."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            h = host.encode()
            payload = b"\x00" + _varint(-1) + _varint(len(h)) + h + struct.pack(">H", port) + b"\x01"
            s.sendall(_varint(len(payload)) + payload + b"\x01\x00")
            length = _read_varint(s)
            if length > 1_000_000:
                return None
            data = _recv_exact(s, length)
        i = 0
        _pid = 0
        shift = 0
        while True:
            b = data[i]
            i += 1
            _pid |= (b & 0x7F) << shift
            shift += 7
            if not b & 0x80:
                break
        n, shift = 0, 0
        while True:
            b = data[i]
            i += 1
            n |= (b & 0x7F) << shift
            shift += 7
            if not b & 0x80:
                break
        js = json.loads(data[i:i + n].decode("utf-8", "replace"))
        p = js.get("players") or {}
        names = [str(x.get("name", ""))[:32] for x in (p.get("sample") or []) if isinstance(x, dict) and x.get("name")]
        motd = js.get("description")
        if isinstance(motd, dict):
            motd = motd.get("text", "")
        return {"online": int(p.get("online", 0)), "max": int(p.get("max", 0)), "names": names,
                "version": str((js.get("version") or {}).get("name", ""))[:40], "motd": str(motd or "")[:120]}
    except (OSError, ValueError, IndexError, KeyError, AttributeError, TypeError):
        return None


def mc_ping(port: int, host: str = "127.0.0.1", timeout: float = 1.5) -> tuple[int, int] | None:
    """Retourne (joueurs en ligne, maximum) ou None si le serveur ne répond pas."""
    r = mc_status(port, host, timeout)
    return (r["online"], r["max"]) if r else None


class ServerManager:
    def __init__(self, settings: Settings, db: Database, alert=None, ram_check=None, mount_ok=None):
        self.s, self.db = settings, db
        self.alert = alert or (lambda key, level, msg: None)
        self.clear_alert = lambda key: None
        self.ram_check = ram_check
        self.mount_ok = mount_ok or (lambda: True)
        self._children: dict[str, subprocess.Popen] = {}
        self._stopping: dict[str, float] = {}
        self._ready: set[str] = set()
        self._ping: dict[str, tuple[float, tuple[int, int] | None]] = {}
        self._cpu: dict[str, tuple[float, int]] = {}
        self._cpu_pct: dict[str, float] = {}
        self._unreach_since: dict[str, float] = {}
        self._restarts: dict[str, list[float]] = {}
        self._locks: dict[str, threading.Lock] = {}
        self.blocked: dict[str, str] = {}          # serveurs dont le démarrage est momentanément interdit (raison)
        self.event = lambda kind, srv, data=None: None      # branché sur le bus d'événements par le panneau
        self.crash_hook = None                              # capture d'un rapport de plantage
        self._ever_ready: dict[str, bool] = {}
        self._started_at: dict[str, float] = {}
        self._log_off: dict[str, int] = {}
        self._log_last: dict[str, float] = {}
        self._log_count: dict[str, int] = {}
        self._unreach_emitted: set[str] = set()
        self._glock = threading.RLock()

    def server(self, sid: str) -> dict:
        row = self.db.one("SELECT * FROM servers WHERE id=?", (sid,))
        if not row:
            raise ProcessError("Serveur introuvable.")
        return row

    def _lock(self, sid: str) -> threading.Lock:
        with self._glock:
            return self._locks.setdefault(sid, threading.Lock())

    def runfile(self, sid: str) -> Path:
        return self.s.run_dir / f"{sid}.json"

    def fifo(self, sid: str) -> Path:
        return self.s.run_dir / f"{sid}.stdin"

    def console_path(self, srv: dict) -> Path:
        return Path(srv["path"]) / "logs" / "panel-console.log"

    def _read_run(self, sid: str) -> dict | None:
        try:
            return json.loads(self.runfile(sid).read_text())
        except (OSError, ValueError):
            return None

    def alive(self, sid: str) -> dict | None:
        """Retourne le fichier d'exécution si le processus tourne réellement, sinon nettoie et retourne None."""
        run = self._read_run(sid)
        if not run:
            return None
        st = read_stat(run["pid"])
        if st and st["state"] != "Z" and st["start"] == run.get("start_ticks"):
            return run
        ch = self._children.pop(sid, None)
        if ch:
            try:
                ch.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                pass
        self._cleanup_run(sid)
        return None

    def _cleanup_run(self, sid: str):
        for p in (self.runfile(sid), self.fifo(sid)):
            try:
                p.unlink()
            except OSError:
                pass
        self._ready.discard(sid)
        self._cpu.pop(sid, None)
        self._cpu_pct.pop(sid, None)
        self._ping.pop(sid, None)

    def state(self, srv: dict) -> str:
        sid = srv["id"]
        if srv["status"] == "installing":
            return "installing"
        if srv["status"] == "awaiting_jar":
            return "awaiting_jar"
        if srv["status"] == "error" or not srv["install_ok"]:
            return "error"
        run = self.alive(sid)
        if run:
            if sid in self._stopping:
                return "stopping"
            return "running" if sid in self._ready else "starting"
        if srv["desired_state"] == "crashed":
            return "error"
        return "stopped"

    def build_command(self, srv: dict) -> list[str]:
        launch = srv["launch_json"] or {}
        args = launch.get("args")
        if not args or not srv.get("java_path"):
            raise ProcessError("Installation incomplète : commande de lancement absente.")
        cmd = [srv["java_path"], f"-Xms{srv['ram_min_mb']}M", f"-Xmx{srv['ram_max_mb']}M"]
        cmd += [a for a in (srv.get("jvm_extra") or "").split() if a]
        cmd += list(args)
        return cmd

    def start(self, sid: str, internal: bool = False) -> dict:
        srv = self.server(sid)
        if not internal and sid in self.blocked:
            raise ProcessError(f"Opération en cours sur ce serveur ({self.blocked[sid]}) : réessayez dans un instant.")
        try:
            return self._start(sid, srv)
        except ProcessError as e:
            if "déjà en cours" not in str(e):
                self.event("server.start_failed", srv, {"reason": str(e)})
            raise

    def _start(self, sid: str, srv: dict) -> dict:
        with self._lock(sid):
            if self.alive(sid):
                raise ProcessError("Ce serveur est déjà en cours d'exécution.")
            if srv["status"] != "installed" or not srv["install_ok"]:
                raise ProcessError("L'installation de ce serveur n'est pas terminée : démarrage refusé.")
            if not srv["eula_accepted"]:
                raise ProcessError("Le contrat de licence Minecraft (EULA) doit d'abord être accepté.")
            if not self.mount_ok():
                raise ProcessError("Le disque de données n'est pas monté : démarrage refusé.")
            server_dir = Path(srv["path"])
            if not server_dir.is_dir():
                raise ProcessError("Le dossier du serveur est introuvable.")
            if not Path(srv["java_path"] or "").exists():
                raise ProcessError("Le runtime Java de ce serveur est introuvable (réinstallez-le depuis Paramètres).")
            if not port_free(srv["port"]):
                raise ProcessError(f"Le port {srv['port']} est déjà utilisé.")
            if self.ram_check:
                self.ram_check(srv)
            cmd = self.build_command(srv)
            self.s.run_dir.mkdir(parents=True, exist_ok=True)
            self.s.home_dir.mkdir(parents=True, exist_ok=True)
            fifo = self.fifo(sid)
            fifo.unlink(missing_ok=True)
            os.mkfifo(fifo, 0o600)
            console = self.console_path(srv)
            console.parent.mkdir(parents=True, exist_ok=True)
            self._rotate_console(console)
            out_fd = os.open(console, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o640)
            in_fd = os.open(fifo, os.O_RDWR)
            env = {"PATH": "/usr/bin:/bin", "HOME": str(self.s.home_dir), "LANG": "C.UTF-8",
                   "JAVA_HOME": str(Path(srv["java_path"]).parent.parent)}
            try:
                os.write(out_fd, f"[panneau] Démarrage : {' '.join(cmd)}\n".encode())
                proc = subprocess.Popen(cmd, cwd=server_dir, stdin=in_fd, stdout=out_fd, stderr=subprocess.STDOUT,
                                        env=env, start_new_session=True, close_fds=True)
            finally:
                os.close(in_fd)
                os.close(out_fd)
            st = None
            for _ in range(50):
                st = read_stat(proc.pid)
                if st:
                    break
                time.sleep(0.02)
            run = {"pid": proc.pid, "start_ticks": st["start"] if st else 0, "started": time.time(),
                   "port": srv["port"], "cmd": cmd}
            self.runfile(sid).write_text(json.dumps(run))
            self._children[sid] = proc
            self._stopping.pop(sid, None)
            self._ready.discard(sid)
            self._ever_ready[sid] = False
            self._started_at[sid] = time.time()
            try:
                self._log_off[sid] = console.stat().st_size
            except OSError:
                self._log_off[sid] = 0
            self.db.execute("UPDATE servers SET desired_state='running', error='', last_started=? WHERE id=?", (time.time(), sid))
            self.clear_alert(f"crash:{sid}")
            return run

    @staticmethod
    def sanitize_command(text: str) -> str:
        t = "".join(ch for ch in (text or "") if ch >= " " and ch != "\x7f").strip()
        if not t:
            raise ProcessError("Commande vide.")
        if len(t) > 1000:
            raise ProcessError("Commande trop longue.")
        return t.lstrip("/") if t.startswith("//") else t

    def send_command(self, sid: str, text: str):
        t = self.sanitize_command(text)
        if not self.alive(sid):
            raise ProcessError("Le serveur n'est pas en cours d'exécution.")
        try:
            fd = os.open(self.fifo(sid), os.O_WRONLY | os.O_NONBLOCK)
        except OSError as e:
            raise ProcessError("Console indisponible : le serveur ne répond pas.") from e
        try:
            os.write(fd, (t + "\n").encode("utf-8", "replace"))
        except OSError as e:
            if e.errno == errno.EAGAIN:
                raise ProcessError("La console est saturée, réessayez.") from e
            raise ProcessError("Envoi impossible.") from e
        finally:
            os.close(fd)

    def stop(self, sid: str, timeout: int = 60, wait: bool = False) -> bool:
        """Arrêt propre : commande `stop`, puis SIGTERM si le délai est dépassé. Jamais de SIGKILL ici."""
        run = self.alive(sid)
        self.db.execute("UPDATE servers SET desired_state='stopped' WHERE id=?", (sid,))
        if not run:
            return True
        self._stopping[sid] = time.time() + timeout
        try:
            self.send_command(sid, "stop")
        except ProcessError:
            self._signal(run, signal.SIGTERM)

        def worker():
            end = time.time() + timeout
            while time.time() < end:
                if not self.alive(sid):
                    break
                time.sleep(0.25)
            else:
                r = self.alive(sid)
                if r:
                    self._signal(r, signal.SIGTERM)
                    end2 = time.time() + 30
                    while time.time() < end2 and self.alive(sid):
                        time.sleep(0.25)
                    if self.alive(sid):
                        self.alert(f"stuck:{sid}", "critical",
                                   f"Le serveur « {(self.server(sid).get('display_name') or self.server(sid)['name'])} » ne s'arrête pas. Utilisez « Forcer l'arrêt » en dernier recours.")
            self._stopping.pop(sid, None)

        if wait:
            worker()
        else:
            threading.Thread(target=worker, daemon=True, name=f"stop-{sid[:6]}").start()
        return True

    def restart(self, sid: str, timeout: int = 60):
        def worker():
            self.stop(sid, timeout, wait=True)
            if not self.alive(sid):
                try:
                    self.start(sid)
                except ProcessError as e:
                    self.alert(f"restart:{sid}", "warning", f"Redémarrage impossible : {e}")
        threading.Thread(target=worker, daemon=True, name=f"restart-{sid[:6]}").start()

    def kill(self, sid: str):
        run = self.alive(sid)
        self.db.execute("UPDATE servers SET desired_state='stopped' WHERE id=?", (sid,))
        if run:
            self._signal(run, signal.SIGKILL)
            time.sleep(0.2)
            self.alive(sid)

    @staticmethod
    def _signal(run: dict, sig: int):
        try:
            os.kill(run["pid"], sig)
        except OSError:
            pass

    def stop_all(self, timeout: int = 90):
        """Arrêt propre de tous les serveurs (utilisé à l'extinction de la machine)."""
        sids = [r["id"] for r in self.db.query("SELECT id FROM servers")]
        running = [s for s in sids if self.alive(s)]
        for s in running:
            self.db.execute("UPDATE servers SET desired_state='running' WHERE id=?", (s,))  # relancés au prochain boot si autostart
            try:
                self._stopping[s] = time.time() + timeout
                self.send_command(s, "stop")
            except ProcessError:
                pass
        end = time.time() + timeout
        while time.time() < end and any(self.alive(s) for s in running):
            time.sleep(0.25)

    def _rotate_console(self, console: Path):
        try:
            if console.exists() and console.stat().st_size > MAX_CONSOLE:
                self._rotate_copytruncate(console)
        except OSError:
            pass

    @staticmethod
    def _rotate_copytruncate(console: Path, keep: int = 3):
        for i in range(keep, 0, -1):
            old = console.with_name(f"{console.name}.{i}.gz")
            if i == keep:
                old.unlink(missing_ok=True)
            elif old.exists():
                old.rename(console.with_name(f"{console.name}.{i + 1}.gz"))
        with open(console, "rb") as src, gzip.open(console.with_name(console.name + ".1.gz"), "wb", compresslevel=3) as dst:
            shutil.copyfileobj(src, dst)
        with open(console, "r+b") as f:
            f.truncate(0)

    def read_console(self, srv: dict, offset: int | None = None, max_bytes: int = 256 * 1024) -> tuple[str, int]:
        """offset None : dernières lignes ; sinon octets depuis offset (remis à 0 si le fichier a été tronqué)."""
        p = self.console_path(srv)
        try:
            size = p.stat().st_size
            with open(p, "rb") as f:
                if offset is None:
                    start = max(0, size - 64 * 1024)
                    f.seek(start)
                    data = f.read()
                    if start:
                        data = data.split(b"\n", 1)[-1]
                    lines = data.splitlines()[-500:]
                    return b"\n".join(lines).decode("utf-8", "replace") + ("\n" if lines else ""), size
                if offset > size:
                    offset = 0
                f.seek(offset)
                data = f.read(max_bytes)
                cut = data.rfind(b"\n")
                if len(data) < max_bytes:
                    if cut == -1:
                        return "", offset  # ligne incomplète : on attend la suite
                    data = data[:cut + 1]
                return data.decode("utf-8", "replace"), offset + len(data)
        except OSError:
            return "", 0

    def stats(self, srv: dict) -> dict:
        sid = srv["id"]
        out = {"state": self.state(srv), "pid": None, "uptime": 0, "rss_mb": 0, "cpu": 0.0, "players": None, "max_players": None}
        run = self.alive(sid) if out["state"] in ("starting", "running", "stopping") else None
        if run:
            out.update(pid=run["pid"], uptime=int(time.time() - run["started"]), rss_mb=round(rss_mb(run["pid"]), 1),
                       cpu=round(self._cpu_pct.get(sid, 0.0), 1))
            pg = self._ping.get(sid)
            if pg and pg[1]:
                out["players"], out["max_players"] = pg[1]
        return out

    def tick(self):
        """Appelé toutes les ~2 s : prêt/crash, CPU, ping joueurs, rotation de la console, redémarrages auto."""
        now = time.time()
        for srv in self.db.query("SELECT * FROM servers WHERE install_ok=1 AND status='installed'"):
            sid = srv["id"]
            run = self.alive(sid)
            if run:
                st = read_stat(run["pid"])
                if st:
                    j = st["utime"] + st["stime"]
                    prev = self._cpu.get(sid)
                    if prev and now > prev[0]:
                        self._cpu_pct[sid] = max(0.0, (j - prev[1]) / CLK / (now - prev[0]) * 100)
                    self._cpu[sid] = (now, j)
                if sid not in self._ready:
                    tail, _ = self.read_console(srv)
                    if DONE_RE.search(tail[-20000:]) or (now - run["started"] > 5 and mc_ping(srv["port"])):
                        self._ready.add(sid)
                        self._ever_ready[sid] = True
                self._watch_log(srv, now)
                if sid in self._ready:
                    last = self._ping.get(sid, (0, None))[0]
                    if now - last >= 5:
                        res = mc_ping(srv["port"])
                        self._ping[sid] = (now, res)
                        if res is None:
                            since = self._unreach_since.setdefault(sid, now)
                            if now - since > 120:
                                self.alert(f"unreach:{sid}", "warning",
                                           f"Le serveur « {srv.get('display_name') or srv['name']} » est actif mais ne répond plus au ping depuis plus de 2 minutes.")
                                if sid not in self._unreach_emitted:
                                    self._unreach_emitted.add(sid)
                                    self.event("server.unreachable", srv, {"since": int(since)})
                        else:
                            if sid in self._unreach_emitted:
                                self._unreach_emitted.discard(sid)
                                self.event("server.reachable", srv, {})
                            self._unreach_since.pop(sid, None)
                            self.clear_alert(f"unreach:{sid}")
                            self.clear_alert(f"stuck:{sid}")
                try:
                    c = self.console_path(srv)
                    if c.exists() and c.stat().st_size > LIVE_ROTATE:
                        self._rotate_copytruncate(c)
                except OSError:
                    pass
            else:
                if srv["desired_state"] == "running" and sid not in self._stopping:
                    self._handle_exit(srv)

    CRIT_RE = re.compile(r"(/(?:ERROR|FATAL)\]|java\.lang\.OutOfMemoryError|Exception in server tick loop)")

    def _watch_log(self, srv: dict, now_ts: float):
        """Erreurs critiques dans la console : au plus un événement par serveur toutes les 10 minutes (avec le nombre d'erreurs)."""
        sid = srv["id"]
        try:
            text, off = self.read_console(srv, self._log_off.get(sid, 0), max_bytes=256 * 1024)
        except Exception:  # noqa: BLE001
            return
        self._log_off[sid] = off
        hits = [ln for ln in text.splitlines() if self.CRIT_RE.search(ln)]
        if not hits:
            return
        self._log_count[sid] = self._log_count.get(sid, 0) + len(hits)
        if now_ts - self._log_last.get(sid, 0) >= 600:
            self.event("log.critical", srv, {"line": hits[-1][-400:], "count": self._log_count[sid]})
            self._log_last[sid] = now_ts
            self._log_count[sid] = 0

    def _handle_exit(self, srv: dict):
        sid = srv["id"]
        tail, _ = self.read_console(srv)
        if "Stopping server" in tail[-4000:]:
            self.db.execute("UPDATE servers SET desired_state='stopped' WHERE id=?", (sid,))
            return
        self.db.execute("UPDATE servers SET desired_state='crashed' WHERE id=?", (sid,))
        self.alert(f"crash:{sid}", "critical", f"Le serveur « {srv.get('display_name') or srv['name']} » s'est arrêté de façon inattendue (plantage).")
        during_start = not self._ever_ready.get(sid, True)
        info = {}
        if self.crash_hook:
            try:
                full, _ = self.read_console(srv)
                info = self.crash_hook(srv, full, self._started_at.get(sid, 0), during_start) or {}
            except Exception as e:  # noqa: BLE001 — l'analyse ne doit jamais empêcher la suite (redémarrage auto)
                info = {"kind": "server.crashed", "error": str(e)[:200]}
        a = info.get("analysis") or {}
        self.event(info.get("kind") or ("server.start_failed" if during_start else "server.crashed"), srv,
                   {"crash_id": info.get("id"), "confirmed": a.get("confirmed", [])[:3], "possible": a.get("possible", [])[:2],
                    "suspects": [x["name"] for x in a.get("suspects", [])][:5], "report": info.get("report_name")})
        if srv["auto_restart"]:
            hist = [t for t in self._restarts.get(sid, []) if time.time() - t < 600]
            if len(hist) < 3:
                hist.append(time.time())
                self._restarts[sid] = hist
                self.event("server.auto_restart", srv, {"attempt": len(hist)})
                try:
                    self.start(sid)
                except ProcessError as e:
                    self.alert(f"restart:{sid}", "warning", f"Redémarrage automatique impossible : {e}")
            else:
                self.alert(f"loop:{sid}", "critical",
                           f"« {srv.get('display_name') or srv['name']} » a planté 3 fois en 10 minutes : redémarrage automatique suspendu.")

    def reconcile(self):
        """Réadopte les serveurs encore vivants, nettoie les restes, relance l'autostart."""
        for srv in self.db.query("SELECT * FROM servers"):
            sid = srv["id"]
            run = self.alive(sid)
            if run:
                tail, _ = self.read_console(srv)
                if DONE_RE.search(tail[-20000:]):
                    self._ready.add(sid)
                continue
            if srv["desired_state"] == "running" and srv["autostart"] and srv["install_ok"] and srv["status"] == "installed":
                try:
                    self.start(sid)
                except ProcessError as e:
                    self.alert(f"autostart:{sid}", "warning", f"Démarrage automatique de « {srv.get('display_name') or srv['name']} » impossible : {e}")
            elif srv["desired_state"] == "running":
                self.db.execute("UPDATE servers SET desired_state='stopped' WHERE id=?", (sid,))

    def running_ram_mb(self, exclude: str | None = None) -> int:
        total = 0
        for srv in self.db.query("SELECT * FROM servers"):
            if srv["id"] != exclude and self.alive(srv["id"]):
                total += srv["ram_max_mb"]
        return total
