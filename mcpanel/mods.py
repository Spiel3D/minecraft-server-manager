"""Lecture des mods installés : métadonnées (Fabric/Quilt/Forge/NeoForge), doublons, compatibilité détectable."""
from __future__ import annotations

import json
import re
import tomllib
import zipfile
from pathlib import Path

from .util import natural_key

IGNORED_DEPS = {"minecraft", "java", "fabricloader", "fabric-loader", "quilt_loader", "forge", "neoforge", "javafml", "lowcodefml", "mixinextras"}
NO_MODS = {"vanilla", "paper", "purpur", "spigot", "bukkit"}      # ces serveurs chargent des plugins, pas des mods
ACCEPTS = {"fabric": {"fabric"}, "quilt": {"quilt", "fabric"}, "forge": {"forge"}, "neoforge": {"neoforge", "forge"}}


def _read(z: zipfile.ZipFile, name: str, limit: int = 2 * 1024 * 1024) -> bytes | None:
    try:
        info = z.getinfo(name)
    except KeyError:
        return None
    if info.file_size > limit:
        return None
    with z.open(info) as f:
        return f.read(limit)


def parse_jar(path: Path) -> dict:
    """Retourne {loader, id, name, version, mc_range} ; champs None si indétectables."""
    meta = {"loader": None, "id": None, "name": None, "version": None, "mc_range": None, "error": None,
            "depends": [], "breaks": [], "provides": [], "side": None}
    try:
        with zipfile.ZipFile(path) as z:
            raw = _read(z, "fabric.mod.json")
            if raw is not None:
                j = json.loads(raw.decode("utf-8", "replace"), strict=False)
                dep = (j.get("depends") or {}).get("minecraft")
                if isinstance(dep, list):
                    dep = " || ".join(map(str, dep))
                meta.update(loader="fabric", id=j.get("id"), name=j.get("name") or j.get("id"),
                            version=str(j.get("version")) if j.get("version") is not None else None, mc_range=dep,
                            depends=[k for k in (j.get("depends") or {}) if k not in IGNORED_DEPS],
                            breaks=[k for k in {**(j.get("breaks") or {}), **(j.get("conflicts") or {})} if k not in IGNORED_DEPS],
                            provides=[str(x) for x in (j.get("provides") or []) if isinstance(x, str)],
                            side={"client": "client", "server": "server"}.get(str(j.get("environment") or "*")))
                return meta
            raw = _read(z, "quilt.mod.json")
            if raw is not None:
                j = json.loads(raw.decode("utf-8", "replace"), strict=False)
                ql = j.get("quilt_loader") or {}
                md = ql.get("metadata") or {}
                dep = None
                for d in ql.get("depends") or []:
                    if isinstance(d, dict) and d.get("id") == "minecraft":
                        dep = d.get("versions")
                        if isinstance(dep, (list, dict)):
                            dep = json.dumps(dep)
                ids = lambda lst: [d if isinstance(d, str) else d.get("id") for d in (lst or []) if isinstance(d, (str, dict))]  # noqa: E731
                meta.update(loader="quilt", id=ql.get("id"), name=md.get("name") or ql.get("id"),
                            version=ql.get("version"), mc_range=dep,
                            depends=[x for x in ids(ql.get("depends")) if x and x not in IGNORED_DEPS],
                            breaks=[x for x in ids(ql.get("breaks")) if x and x not in IGNORED_DEPS],
                            provides=[x for x in ids(ql.get("provides")) if x])
                return meta
            for fname, ld in (("META-INF/neoforge.mods.toml", "neoforge"), ("META-INF/mods.toml", "forge")):
                raw = _read(z, fname)
                if raw is None:
                    continue
                t = tomllib.loads(raw.decode("utf-8", "replace"))
                mod = (t.get("mods") or [{}])[0]
                mid = mod.get("modId")
                rng = None
                deps, brk = [], []
                for d in (t.get("dependencies") or {}).get(mid, []) or []:
                    if not isinstance(d, dict):
                        continue
                    if d.get("modId") == "minecraft":
                        rng = d.get("versionRange")
                    dep_id, typ = d.get("modId"), str(d.get("type") or ("required" if d.get("mandatory") else "optional")).lower()
                    if not dep_id or dep_id in IGNORED_DEPS or str(d.get("side", "BOTH")).upper() == "CLIENT":
                        continue
                    if typ == "required":
                        deps.append(dep_id)
                    elif typ == "incompatible":
                        brk.append(dep_id)
                meta["depends"], meta["breaks"] = deps, brk
                if str(t.get("displayTest", "")).upper() == "IGNORE_ALL_VERSION" or str(mod.get("displayTest", "")).upper() == "IGNORE_ALL_VERSION":
                    meta["side"] = None
                ver = mod.get("version")
                if ver == "${file.jarVersion}":
                    mf = _read(z, "META-INF/MANIFEST.MF")
                    m = re.search(rb"Implementation-Version:\s*(\S+)", mf or b"")
                    ver = m.group(1).decode() if m else None
                meta.update(loader=ld, id=mid, name=mod.get("displayName") or mid, version=ver, mc_range=rng)
                return meta
            raw = _read(z, "mcmod.info")
            if raw is not None:
                j = json.loads(raw.decode("utf-8", "replace"), strict=False)
                j = j.get("modList", [{}])[0] if isinstance(j, dict) else (j[0] if j else {})
                meta.update(loader="forge", id=j.get("modid"), name=j.get("name"), version=j.get("version"),
                            mc_range=j.get("mcversion"))
                return meta
    except (zipfile.BadZipFile, OSError):
        meta["error"] = "Archive JAR illisible"
    except (ValueError, tomllib.TOMLDecodeError):
        meta["error"] = "Métadonnées illisibles"
    return meta


def _vt(s: str):
    return tuple(int(x) for x in re.findall(r"\d+", s)[:3]) + (0,) * (3 - min(3, len(re.findall(r"\d+", s))))


def mc_in_range(mc: str, rng: str | None) -> bool | None:
    """True/False si la plage est comprise, None si indécidable. Plages Maven et prédicats Fabric simples."""
    if not rng:
        return None
    rng = rng.strip()
    v = _vt(mc)
    try:
        if rng.startswith(("[", "(")):
            for part in re.findall(r"[\[(][^\])]*[\])]", rng):
                lo_in, hi_in = part[0] == "[", part[-1] == "]"
                body = part[1:-1]
                if "," not in body:
                    if _vt(body) == v:
                        return True
                    continue
                lo, hi = [x.strip() for x in body.split(",", 1)]
                ok = True
                if lo:
                    ok &= v > _vt(lo) or (lo_in and v == _vt(lo))
                if hi:
                    ok &= v < _vt(hi) or (hi_in and v == _vt(hi))
                if ok:
                    return True
            return False
        results = []
        for alt in rng.split("||"):
            ok = True
            for tok in alt.split():
                m = re.fullmatch(r"(>=|<=|>|<|=|~|\^)?\s*([\d.x*]+)", tok.strip())
                if not m:
                    return None
                op, val = m.group(1) or "=", m.group(2)
                if "x" in val or "*" in val:
                    pre = [p for p in val.replace("*", "x").split(".") if p != "x"]
                    ok &= list(map(str, v[:len(pre)])) == pre
                    continue
                t = _vt(val)
                if op == ">=":
                    ok &= v >= t
                elif op == "<=":
                    ok &= v <= t
                elif op == ">":
                    ok &= v > t
                elif op == "<":
                    ok &= v < t
                elif op in ("~", "^"):
                    ok &= v >= t and v[:2] == t[:2]
                else:
                    n = len(re.findall(r"\d+", val))
                    ok &= v[:n] == t[:n]
            results.append(ok)
        return any(results)
    except (ValueError, IndexError):
        return None


def scan_mods(server_dir: Path, server_type: str, mc_version: str) -> dict:
    mods_dir = server_dir / "mods"
    items = []
    if mods_dir.is_dir():
        for p in sorted(mods_dir.iterdir(), key=lambda x: natural_key(x.name.lower())):
            if not p.is_file():
                continue
            disabled = p.name.endswith(".jar.disabled")
            if not (p.name.endswith(".jar") or disabled):
                continue
            meta = parse_jar(p)
            issues = []
            if meta["error"]:
                issues.append({"level": "warning", "text": meta["error"]})
            if meta["loader"] and server_type in ACCEPTS and meta["loader"] not in ACCEPTS[server_type]:
                issues.append({"level": "error", "text": f"Mod {meta['loader']} sur un serveur {server_type} : incompatible."})
            if server_type in NO_MODS:
                issues.append({"level": "warning", "text": f"Un serveur {server_type.capitalize()} ne charge pas les mods."})
            ok = mc_in_range(mc_version, meta["mc_range"])
            if ok is False:
                issues.append({"level": "error", "text": f"Minecraft {mc_version} hors de la plage déclarée ({meta['mc_range']})."})
            items.append({"file": p.name, "size": p.stat().st_size, "disabled": disabled, "issues": issues, **{k: meta[k] for k in ("loader", "id", "name", "version", "mc_range", "depends", "breaks", "provides", "side")}})
    seen: dict[str, list[str]] = {}
    for it in items:
        if it["id"] and not it["disabled"]:
            seen.setdefault(it["id"], []).append(it["file"])
    dups = {k: v for k, v in seen.items() if len(v) > 1}
    present = set(seen)
    for it in items:
        if not it["disabled"]:
            present.update(it.get("provides") or [])
    fabric_api = bool(present & {"fabric-api", "fabric"})
    for it in items:
        if it["disabled"]:
            continue
        missing = [d for d in it.get("depends") or [] if d not in present and not (fabric_api and d.startswith("fabric-")) and not (d == "fabric" and fabric_api)]
        if missing:
            it["issues"].append({"level": "error", "text": "Dépendance manquante : " + ", ".join(missing), "missing": missing})
        clash = [d for d in it.get("breaks") or [] if d in present]
        if clash:
            it["issues"].append({"level": "error", "text": "Incompatible avec : " + ", ".join(clash)})
        if it.get("side") == "client":
            it["issues"].append({"level": "warning", "text": "Mod côté client uniquement : inutile sur un serveur (peut l'empêcher de démarrer)."})
    for it in items:
        if it["id"] in dups:
            it["issues"].append({"level": "warning", "text": "Doublon : " + ", ".join(dups[it["id"]])})
    return {"mods": items, "duplicates": dups, "count": len(items)}
