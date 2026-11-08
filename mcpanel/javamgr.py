"""Runtimes Java : détection (gérés + système), téléchargement Adoptium (sha256), extraction sûre."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tarfile
import threading
from pathlib import Path
from typing import Callable

from .config import Settings
from .sources import Adoptium, Http, SourceError, choose_java_major


def safe_extract_tar(tar_path: Path, dest: Path, max_bytes: int = 2 * 1024 ** 3):
    """Extrait une archive tar.gz sans jamais sortir de `dest` (chemins, liens, périphériques)."""
    dest = Path(os.path.realpath(dest))
    dest.mkdir(parents=True, exist_ok=True)
    total = 0
    with tarfile.open(tar_path, "r:gz") as tf:
        members = tf.getmembers()
        for m in members:
            target = Path(os.path.realpath(dest / m.name))
            if target != dest and dest not in target.parents:
                raise SourceError(f"Archive refusée : chemin hors destination ({m.name}).")
            if m.isdev() or m.isfifo():
                raise SourceError(f"Archive refusée : fichier spécial ({m.name}).")
            if m.issym() or m.islnk():
                link = m.linkname if m.islnk() else os.path.join(os.path.dirname(m.name), m.linkname)
                lt = Path(os.path.realpath(dest / link))
                if lt != dest and dest not in lt.parents:
                    raise SourceError(f"Archive refusée : lien sortant ({m.name}).")
            total += max(m.size, 0)
            if total > max_bytes:
                raise SourceError("Archive trop volumineuse.")
        for m in members:
            m.mode = (m.mode & 0o755) | 0o200 if not m.isdir() else 0o755
            try:
                tf.extract(m, dest, set_attrs=True, filter="fully_trusted")  # déjà validé ci-dessus
            except TypeError:
                tf.extract(m, dest, set_attrs=True)


class JavaManager:
    def __init__(self, settings: Settings, http: Http, adoptium: Adoptium | None = None):
        self.s = settings
        self.http = http
        self.adoptium = adoptium or Adoptium(http)
        self._lock = threading.Lock()
        self.system_dir = Path("/usr/lib/jvm")

    @staticmethod
    def _major_of(home: Path) -> int | None:
        rel = home / "release"
        try:
            txt = rel.read_text(errors="replace")
            m = re.search(r'JAVA_VERSION="?(\d+)(?:\.(\d+))?', txt)
            if m:
                maj = int(m.group(1))
                return int(m.group(2)) if maj == 1 and m.group(2) else maj
        except OSError:
            pass
        return None

    def installed(self) -> list[dict]:
        out = []
        for base, source in ((self.s.java_dir, "géré"), (self.system_dir, "système")):
            if not base.is_dir():
                continue
            for home in sorted(base.iterdir()):
                java = home / "bin" / "java"
                if home.is_dir() and java.exists():
                    maj = self._major_of(home) or (int(home.name) if home.name.isdigit() else None)
                    if maj:
                        out.append({"major": maj, "path": str(java), "home": str(home), "source": source})
        return sorted(out, key=lambda d: (d["major"], d["source"] != "géré"))

    def find(self, major: int) -> Path | None:
        for j in self.installed():
            if j["major"] == major:
                return Path(j["path"])
        return None

    def verify(self, java: Path) -> str:
        try:
            r = subprocess.run([str(java), "-version"], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as e:
            raise SourceError(f"Java inutilisable : {e}") from e
        out = (r.stderr or r.stdout).strip().splitlines()
        if r.returncode != 0 or not out:
            raise SourceError("Le runtime Java téléchargé ne démarre pas.")
        return out[0]

    def ensure(self, required: int, log: Callable[[str], None] = lambda m: None,
               progress: Callable[[float], None] = lambda p: None) -> tuple[Path, int]:
        """Retourne (chemin du java, version majeure retenue) en installant le runtime si nécessaire."""
        major = choose_java_major(required)
        with self._lock:
            have = self.find(major)
            if have:
                log(f"Java {major} déjà présent : {have}")
                return have, major
            log(f"Java {major} absent : téléchargement depuis Adoptium (Temurin)…")
            asset = self.adoptium.asset(major)
            tmp = self.s.state_dir / "tmp-java"
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir(parents=True, exist_ok=True)
            archive = tmp / asset["name"]
            try:
                self.http.download(asset["url"], archive, expected={"sha256": asset["sha256"]},
                                   progress=lambda d, t: progress(d / t if t else 0.0),
                                   allowed_hosts=None)
                log("Somme de contrôle SHA-256 vérifiée. Extraction…")
                ex = tmp / "x"
                safe_extract_tar(archive, ex)
                roots = [p for p in ex.iterdir() if p.is_dir()]
                if len(roots) != 1:
                    raise SourceError("Structure d'archive Java inattendue.")
                dest = self.s.java_dir / str(major)
                self.s.java_dir.mkdir(parents=True, exist_ok=True)
                if dest.exists():
                    shutil.rmtree(dest)
                roots[0].rename(dest)
                java = dest / "bin" / "java"
                log(f"Runtime installé : {self.verify(java)}")
                return java, major
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
