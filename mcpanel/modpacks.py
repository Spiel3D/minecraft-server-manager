"""Import de modpacks.

Principe : un ZIP n'est JAMAIS extrait aveuglément. On l'analyse d'abord (sans rien écrire), on produit un
plan (fichiers retenus / ignorés / en conflit), puis l'extraction ne fait que suivre ce plan, entrée par entrée,
avec contrôle des chemins et des tailles réellement écrites.
"""
from __future__ import annotations

import json
import os
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable

from .security import safe_join
from .sources import CurseForge, Http, SourceError

SERVER_DIRS = {"mods", "config", "defaultconfigs", "kubejs", "scripts", "datapacks", "global_packs", "openloader", "paxi"}
ROOT_FILES = {"ops.json", "whitelist.json", "banned-players.json", "banned-ips.json"}
BAD_EXT = {".sh", ".bat", ".cmd", ".exe", ".ps1", ".com", ".msi", ".dll", ".so", ".dylib", ".command", ".scr", ".vbs"}
MODRINTH_HOSTS = {"cdn.modrinth.com", "github.com", "raw.githubusercontent.com", "gitlab.com", "objects.githubusercontent.com"}
CURSE_HOSTS = {"edge.forgecdn.net", "mediafilez.forgecdn.net", "media.forgecdn.net"}

MAX_ENTRIES = 100_000
MAX_NAME = 300
LOADER_KEYS = {"forge": "forge", "neoforge": "neoforge", "fabric-loader": "fabric", "quilt-loader": "quilt"}


class ModpackError(Exception):
    pass


def classify_dest(rel: str, allow_properties: bool = False) -> tuple[bool, str]:
    p = PurePosixPath(rel)
    first = p.parts[0] if p.parts else ""
    if p.suffix.lower() in BAD_EXT:
        return False, "fichier exécutable ou binaire natif (refusé par sécurité)"
    if first in SERVER_DIRS and len(p.parts) > 1:
        return True, ""
    if rel in ROOT_FILES:
        return True, ""
    if rel == "server.properties":
        return (True, "") if allow_properties else (False, "server.properties peut changer le port et le monde : importez-le via Fichiers si voulu")
    if first in ("world", "world_nether", "world_the_end"):
        return False, "dossier de monde : import manuel uniquement"
    return False, "hors des dossiers serveur autorisés (mods, config, defaultconfigs, kubejs, scripts, datapacks…)"


def _bad_name(name: str) -> str | None:
    if len(name) > MAX_NAME:
        return "nom trop long"
    if "\x00" in name or "\\" in name or any(ord(c) < 32 for c in name):
        return "caractères interdits"
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return "chemin absolu"
    if any(part == ".." for part in name.split("/")):
        return "chemin sortant (..)"
    return None


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


def _read_json(z: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int = 8 * 1024 * 1024):
    if info.file_size > limit:
        raise ModpackError(f"{info.filename} est trop volumineux.")
    with z.open(info) as f:
        return json.loads(f.read(limit).decode("utf-8", "replace"))


def analyze_zip(zip_path: Path, server: dict, server_dir: Path, *, max_total: int = 8 * 1024 ** 3,
                allow_properties: bool = False, bomb_min_bytes: int = 1024 ** 3) -> dict:
    zip_path = Path(zip_path)
    res = {"kind": "unknown", "root_prefix": "", "name": None, "mc_version": None, "loader": None,
           "loader_version": None, "entries": [], "skipped": [], "downloads": [], "cf_files": [],
           "warnings": [], "errors": [], "mismatch": [], "totals": {"files": 0, "bytes": 0, "conflicts": 0}}
    try:
        z = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        res["errors"].append("Archive ZIP invalide ou corrompue.")
        return res
    with z:
        bad = z.testzip()
        if bad:
            res["errors"].append(f"Archive corrompue (entrée défectueuse : {bad}).")
            return res
        infos = z.infolist()
        if len(infos) > MAX_ENTRIES:
            res["errors"].append("Archive refusée : trop d'entrées.")
            return res
        total = 0
        lowered: dict[str, str] = {}
        for i in infos:
            reason = _bad_name(i.filename)
            if reason:
                res["errors"].append(f"Archive refusée : entrée « {i.filename[:80]} » ({reason}).")
                return res
            if _is_symlink(i):
                res["errors"].append(f"Archive refusée : lien symbolique « {i.filename[:80]} ».")
                return res
            total += i.file_size
            k = i.filename.lower()
            if not i.is_dir() and k in lowered and lowered[k] != i.filename:
                res["warnings"].append(f"Deux entrées ne diffèrent que par la casse : {i.filename}")
            lowered[k] = i.filename
        asize = max(zip_path.stat().st_size, 1)
        if total > max_total:
            res["errors"].append("Archive refusée : contenu décompressé trop volumineux.")
            return res
        if total > bomb_min_bytes and total / asize > 200:
            res["errors"].append("Archive refusée : taux de compression anormal (bombe de décompression possible).")
            return res

        files = [i for i in infos if not i.is_dir()]
        names = [i.filename for i in files]
        tops = {n.split("/")[0] for n in names if "/" in n}
        prefix = ""
        if names and all("/" in n for n in names) and len(tops) == 1:
            top = next(iter(tops))
            if top not in SERVER_DIRS | {"overrides", "server-overrides"}:
                prefix = top + "/"
        res["root_prefix"] = prefix
        by_name = {n[len(prefix):]: i for n, i in zip(names, files) if n.startswith(prefix)}

        plan: dict[str, dict] = {}

        def add(src: zipfile.ZipInfo, dest: str):
            ok, why = classify_dest(dest, allow_properties)
            if not ok:
                res["skipped"].append({"name": dest, "reason": why})
                return
            plan[dest] = {"src": src.filename, "dest": dest, "size": src.file_size}

        if "modrinth.index.json" in by_name:
            res["kind"] = "modrinth"
            idx = _read_json(z, by_name["modrinth.index.json"])
            res["name"] = idx.get("name")
            deps = idx.get("dependencies") or {}
            res["mc_version"] = deps.get("minecraft")
            for k, ltype in LOADER_KEYS.items():
                if k in deps:
                    res["loader"], res["loader_version"] = ltype, deps[k]
            for f in idx.get("files", []):
                path = f.get("path", "")
                if _bad_name(path) or not path:
                    res["errors"].append(f"Manifeste refusé : chemin dangereux « {path[:80]} ».")
                    return res
                if (f.get("env") or {}).get("server") == "unsupported":
                    res["skipped"].append({"name": path, "reason": "mod réservé au client"})
                    continue
                ok, why = classify_dest(path, allow_properties)
                if not ok:
                    res["skipped"].append({"name": path, "reason": why})
                    continue
                res["downloads"].append({"dest": path, "urls": f.get("downloads", []), "hashes": f.get("hashes", {}),
                                         "size": f.get("fileSize", 0)})
            for pref in ("overrides/", "server-overrides/"):
                for n, i in by_name.items():
                    if n.startswith(pref):
                        add(i, n[len(pref):])
        elif "manifest.json" in by_name:
            man = _read_json(z, by_name["manifest.json"])
            if man.get("manifestType") != "minecraftModpack":
                res["warnings"].append("manifest.json présent mais ce n'est pas un modpack CurseForge.")
            else:
                res["kind"] = "curseforge"
                res["name"] = man.get("name")
                mc = man.get("minecraft") or {}
                res["mc_version"] = mc.get("version")
                ml = next((m for m in mc.get("modLoaders", []) if m.get("primary")), None) or (mc.get("modLoaders") or [None])[0]
                if ml and "-" in ml.get("id", ""):
                    lt, lv = ml["id"].split("-", 1)
                    res["loader"], res["loader_version"] = lt, lv
                res["cf_files"] = [{"projectID": f["projectID"], "fileID": f["fileID"]} for f in man.get("files", [])
                                   if f.get("required", True) and "projectID" in f and "fileID" in f]
                ov = (man.get("overrides") or "overrides").strip("/") + "/"
                for n, i in by_name.items():
                    if n.startswith(ov):
                        add(i, n[len(ov):])
        else:
            if any(n.startswith(("mods/", "config/")) for n in by_name):
                res["kind"] = "serverpack"
            for n, i in by_name.items():
                add(i, n)
            if res["kind"] == "unknown" and not plan:
                res["errors"].append("Aucun fichier exploitable : ce ZIP ne ressemble ni à un modpack ni à un pack serveur.")

        st, sm = server["server_type"], server["mc_version"]
        if res["mc_version"] and res["mc_version"] != sm:
            res["mismatch"].append(f"Le modpack vise Minecraft {res['mc_version']}, le serveur est en {sm}.")
        if res["loader"] and res["loader"] != st:
            res["mismatch"].append(f"Le modpack utilise {res['loader']}, le serveur est de type {st}.")
        if st in ("vanilla", "paper", "purpur", "spigot", "bukkit") and (res["kind"] != "unknown"):
            res["mismatch"].append(f"Un serveur {st.capitalize()} ne charge pas de mods.")

        for e in plan.values():
            dest = safe_join(server_dir, e["dest"])
            e["conflict"] = dest.exists()
        res["entries"] = sorted(plan.values(), key=lambda e: e["dest"])
        for d in res["downloads"]:
            d["conflict"] = safe_join(server_dir, d["dest"]).exists()
        res["totals"] = {
            "files": len(res["entries"]) + len(res["downloads"]) + len(res["cf_files"]),
            "bytes": sum(e["size"] for e in res["entries"]) + sum(d.get("size", 0) for d in res["downloads"]),
            "conflicts": sum(1 for e in res["entries"] if e["conflict"]) + sum(1 for d in res["downloads"] if d["conflict"]),
        }
    return res


def conflict_paths(analysis: dict) -> list[str]:
    return [e["dest"] for e in analysis["entries"] if e.get("conflict")] + [d["dest"] for d in analysis["downloads"] if d.get("conflict")]


def apply_plan(zip_path: Path, analysis: dict, server_dir: Path, *, policy: str = "skip", http: Http | None = None,
               cf_key: str | None = None, max_total: int = 8 * 1024 ** 3, log: Callable[[str], None] = lambda m: None,
               progress: Callable[[float], None] = lambda p: None) -> dict:
    if analysis["errors"]:
        raise ModpackError("Archive refusée : " + analysis["errors"][0])
    if policy not in ("skip", "replace"):
        raise ModpackError("Politique de conflit inconnue.")
    out = {"extracted": 0, "kept": 0, "downloaded": 0, "failed": [], "manual": [], "skipped": analysis["skipped"]}
    entries, downloads = analysis["entries"], analysis["downloads"]
    total_steps = max(1, len(entries) + len(downloads) + (1 if analysis["cf_files"] else 0))
    step = 0
    written_total = 0
    with zipfile.ZipFile(zip_path) as z:
        for e in entries:
            dest = safe_join(server_dir, e["dest"])
            step += 1
            progress(step / total_steps)
            if dest.exists() and policy == "skip":
                out["kept"] += 1
                continue
            if dest.is_dir():
                out["failed"].append({"name": e["dest"], "reason": "un dossier porte ce nom"})
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".part")
            declared = e["size"]
            n = 0
            try:
                with z.open(e["src"]) as src, open(tmp, "wb") as dst:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        n += len(chunk)
                        written_total += len(chunk)
                        if n > declared + 1024 or written_total > max_total:
                            raise ModpackError(f"Taille réelle de {e['dest']} supérieure à celle déclarée : extraction interrompue.")
                        dst.write(chunk)
                os.replace(tmp, dest)
                out["extracted"] += 1
            finally:
                tmp.unlink(missing_ok=True)
    http = http or Http()
    for d in downloads:
        step += 1
        progress(step / total_steps)
        dest = safe_join(server_dir, d["dest"])
        if dest.exists() and policy == "skip":
            out["kept"] += 1
            continue
        expected = {k: v for k, v in (d.get("hashes") or {}).items() if k in ("sha1", "sha512", "sha256")}
        done = False
        for url in d["urls"]:
            try:
                log(f"Téléchargement : {d['dest']}")
                http.download(url, dest, expected=expected, allowed_hosts=MODRINTH_HOSTS, max_bytes=1024 ** 3)
                out["downloaded"] += 1
                done = True
                break
            except SourceError as ex:
                log(f"  échec : {ex}")
        if not done:
            out["failed"].append({"name": d["dest"], "reason": "téléchargement impossible ou somme de contrôle invalide"})
    if analysis["cf_files"]:
        step += 1
        progress(step / total_steps)
        if not cf_key:
            out["manual"].append({"name": f"{len(analysis['cf_files'])} mods CurseForge",
                                  "reason": "clé API CurseForge absente (Paramètres) : ces mods ne peuvent pas être installés automatiquement"})
        else:
            cf = CurseForge(http, cf_key)
            try:
                files = cf.files([f["fileID"] for f in analysis["cf_files"]])
            except SourceError as ex:
                raise ModpackError(f"CurseForge : {ex}") from ex
            for f in files:
                fname = os.path.basename(f.get("fileName", ""))
                url = f.get("downloadUrl")
                if not fname.endswith(".jar"):
                    out["skipped"].append({"name": fname, "reason": "pas un mod .jar (ressource client probable)"})
                    continue
                if not url:
                    out["manual"].append({"name": fname, "reason": "l'auteur interdit le téléchargement par des outils tiers : à ajouter manuellement"})
                    continue
                dest = safe_join(server_dir, f"mods/{fname}")
                if dest.exists() and policy == "skip":
                    out["kept"] += 1
                    continue
                sha1 = next((h["value"] for h in f.get("hashes", []) if h.get("algo") == 1), None)
                try:
                    http.download(url, dest, expected={"sha1": sha1} if sha1 else None, allowed_hosts=CURSE_HOSTS, max_bytes=1024 ** 3)
                    out["downloaded"] += 1
                except SourceError as ex:
                    out["failed"].append({"name": fname, "reason": str(ex)})
    progress(1.0)
    return out
