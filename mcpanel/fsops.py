"""Opérations de fichiers confinées au dossier d'un serveur (aucune sortie possible)."""
from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

from .security import PathError, SecurityError, rel_to, safe_join

MAX_EDIT_BYTES = 2 * 1024 * 1024
TEXT_HINT = {".properties", ".json", ".txt", ".toml", ".cfg", ".conf", ".yml", ".yaml", ".log", ".md",
             ".ini", ".mcmeta", ".json5", ".js", ".zs", ".csv", ".xml", ".lang", ".snbt", ".mcfunction"}


class FsError(SecurityError):
    pass


def is_text_file(p: Path) -> bool:
    try:
        with open(p, "rb") as f:
            head = f.read(8192)
        if b"\x00" in head:
            return False
        head.decode("utf-8")
        return True
    except UnicodeDecodeError:
        try:
            head[:-3].decode("utf-8")
            return True
        except UnicodeDecodeError:
            return False
    except OSError:
        return False


def list_dir(root: Path, rel: str = "") -> dict:
    d = safe_join(root, rel)
    if not d.is_dir():
        raise FsError("Dossier introuvable.")
    entries = []
    for e in os.scandir(d):
        try:
            st = e.stat(follow_symlinks=False)
        except OSError:
            continue
        is_link = stat.S_ISLNK(st.st_mode)
        is_dir = e.is_dir(follow_symlinks=False)
        entries.append({
            "name": e.name, "type": "dir" if is_dir else ("link" if is_link else "file"),
            "size": 0 if is_dir else st.st_size, "mtime": st.st_mtime,
            "editable": (not is_dir and not is_link and st.st_size <= MAX_EDIT_BYTES
                         and (Path(e.name).suffix.lower() in TEXT_HINT or is_text_file(Path(e.path)))),
        })
    entries.sort(key=lambda x: (x["type"] != "dir", x["name"].lower()))
    return {"path": rel_to(root, d), "entries": entries}


def read_text(root: Path, rel: str) -> dict:
    p = safe_join(root, rel)
    if not p.is_file():
        raise FsError("Fichier introuvable.")
    st = p.stat()
    if st.st_size > MAX_EDIT_BYTES:
        raise FsError("Fichier trop volumineux pour l'éditeur (2 Mio maximum). Téléchargez-le.")
    if not is_text_file(p):
        raise FsError("Fichier binaire : ouverture dans l'éditeur refusée.")
    return {"path": rel_to(root, p), "content": p.read_text(encoding="utf-8", errors="replace"), "mtime": st.st_mtime}


def write_text(root: Path, rel: str, content: str, expected_mtime: float | None = None):
    p = safe_join(root, rel)
    if p == Path(os.path.realpath(root)):
        raise FsError("Chemin invalide.")
    if p.exists():
        if not p.is_file():
            raise FsError("La cible n'est pas un fichier.")
        if expected_mtime is not None and abs(p.stat().st_mtime - expected_mtime) > 0.001:
            raise FsError("Le fichier a été modifié entre-temps (par le serveur ou un autre onglet). Rechargez-le.")
    elif not p.parent.is_dir():
        raise FsError("Dossier parent introuvable.")
    data = content.encode("utf-8")
    if len(data) > MAX_EDIT_BYTES:
        raise FsError("Contenu trop volumineux.")
    tmp = p.with_name(p.name + ".panel-tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    mode = p.stat().st_mode & 0o777 if p.exists() else 0o640
    os.chmod(tmp, mode)
    os.replace(tmp, p)


def mkdir(root: Path, rel: str):
    p = safe_join(root, rel)
    if p.exists():
        raise FsError("Existe déjà.")
    if not p.parent.is_dir():
        raise FsError("Dossier parent introuvable.")
    p.mkdir(mode=0o750)


def _check_not_root(root: Path, p: Path):
    if p == Path(os.path.realpath(root)):
        raise FsError("Le dossier racine du serveur ne peut pas être modifié ici.")


def rename(root: Path, src: str, dst: str):
    a, b = safe_join(root, src), safe_join(root, dst)
    _check_not_root(root, a)
    _check_not_root(root, b)
    if not (a.exists() or a.is_symlink()):
        raise FsError("Source introuvable.")
    if b.exists():
        raise FsError("La destination existe déjà.")
    if a.is_dir() and (b == a or a in b.parents):
        raise FsError("Impossible de déplacer un dossier dans lui-même.")
    if not b.parent.is_dir():
        raise FsError("Dossier de destination introuvable.")
    os.rename(a, b)


def copy(root: Path, src: str, dst: str):
    a, b = safe_join(root, src), safe_join(root, dst)
    _check_not_root(root, b)
    if not a.exists():
        raise FsError("Source introuvable.")
    if b.exists():
        raise FsError("La destination existe déjà.")
    if a.is_dir():
        if b == a or a in b.parents:
            raise FsError("Impossible de copier un dossier dans lui-même.")
        shutil.copytree(a, b, symlinks=True)
    else:
        shutil.copy2(a, b)


def delete(root: Path, rel: str):
    parts = [x for x in (rel or "").split("/") if x not in ("", ".")]
    if not parts:
        raise FsError("Le dossier racine du serveur ne peut pas être modifié ici.")
    name = parts[-1]
    if name == ".." or "\x00" in name or "\\" in name:
        raise PathError("Chemin invalide.")
    parent = safe_join(root, "/".join(parts[:-1]))      # le dossier parent doit rester dans le serveur
    target = parent / name
    if target.is_symlink():                              # on supprime le lien lui-même, jamais sa cible
        target.unlink()
        return
    p = safe_join(root, "/".join(parts))
    _check_not_root(root, p)
    if not p.exists():
        raise FsError("Introuvable.")
    if p.is_dir():
        shutil.rmtree(p)
    else:
        p.unlink()


def disk_usage(path: Path) -> int:
    total = 0
    for dp, _dn, fn in os.walk(path, followlinks=False):
        for f in fn:
            try:
                total += os.lstat(os.path.join(dp, f)).st_size
            except OSError:
                pass
    return total
