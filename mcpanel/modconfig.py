"""Éditeur graphique des fichiers de configuration (mods, plugins, serveur) — V4 §3.

Principe de sûreté : les fichiers sont modifiés LIGNE PAR LIGNE (seule la valeur modifiée est réécrite) ; commentaires,
ordre, paramètres inconnus et mise en forme sont conservés. Après écriture, le fichier est relu par un analyseur strict
(TOML, JSON) : si le résultat n'est pas valide, rien n'est écrit. Une copie est faite avant chaque modification, et la
première version vue par le panneau est gardée comme « original » (retour possible aux valeurs d'origine).

Formats : TOML (Forge/NeoForge), JSON, YAML (valeurs simples), .properties, INI, .cfg Forge 1.12 (préfixes B:/I:/D:/S:).
Les descriptions viennent des commentaires ; « Range: a ~ b », « Allowed Values: … » et « Default: … » (Forge/NeoForge)
deviennent bornes, listes de choix et valeurs par défaut.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
import tomllib
from pathlib import Path

from .jobs import UserError

EXTS = {".toml": "toml", ".json": "json", ".json5": "json", ".yaml": "yaml", ".yml": "yaml", ".properties": "properties", ".ini": "ini", ".cfg": "cfg", ".conf": "ini"}
ROOTS = ("config", "defaultconfigs", "plugins")
MAX_SIZE = 1024 * 1024
RANGE = re.compile(r"Range:\s*(-?[\d.eE+]+|-?∞|~)?\s*~\s*(-?[\d.eE+]+|∞)?", re.I)
ALLOWED = re.compile(r"Allowed Values:\s*(.+)", re.I)
DEFAULT = re.compile(r"Default:\s*(.+)", re.I)


def _num(x):
    try:
        return float(x) if x not in (None, "", "∞", "-∞", "~") else None
    except ValueError:
        return None


def _meta_from_comments(comments: list[str]) -> dict:
    text = "\n".join(comments)
    m = {"description": "\n".join(c for c in comments if not RANGE.search(c) and not ALLOWED.search(c) and not DEFAULT.search(c)).strip()}
    r = RANGE.search(text)
    if r:
        m["min"], m["max"] = _num(r.group(1)), _num(r.group(2))
    a = ALLOWED.search(text)
    if a:
        m["options"] = [x.strip().strip('"') for x in a.group(1).split(",") if x.strip()]
    d = DEFAULT.search(text)
    if d:
        m["default"] = d.group(1).strip().strip('"')
    return m


def _infer(v: str) -> str:
    if v.lower() in ("true", "false"):
        return "bool"
    if re.fullmatch(r"-?\d+", v):
        return "int"
    if re.fullmatch(r"-?\d+\.\d*([eE][-+]?\d+)?|-?\d+[eE][-+]?\d+", v):
        return "float"
    return "string"


# ---------------------------------------------------------------------------------------------- analyseurs
def parse_toml(text: str) -> list[dict]:
    out, section, comments = [], "", []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        if not s:
            comments = []
        elif s.startswith("#"):
            comments.append(s.lstrip("#").strip())
        elif s.startswith("["):
            section = s.strip("[]").strip().strip('"')
            comments = []
        else:
            m = re.match(r'^(\s*)("?[A-Za-z0-9_.\- ]+"?)\s*=\s*(.*)$', ln)
            if m:
                key, rest = m.group(2).strip().strip('"'), m.group(3)
                val, trail = _split_toml_value(rest)
                e = {"key": f"{section}.{key}" if section else key, "section": section, "name": key, "line": i, **_meta_from_comments(comments)}
                if val.startswith("[") and not val.rstrip().endswith("]"):
                    e.update(type="raw", value=val, readonly=True)                  # tableau sur plusieurs lignes : affiché, non modifiable ici
                elif val.startswith('"') or val.startswith("'"):
                    e.update(type="enum" if e.get("options") else "string", value=_toml_unquote(val))
                elif val.startswith("["):
                    e.update(type="list", value=val)
                else:
                    e.update(type=_infer(val), value=val)
                e["trail"] = trail
                out.append(e)
            comments = []
        i += 1
    return out


def _split_toml_value(rest: str) -> tuple[str, str]:
    q, esc = None, False
    for j, c in enumerate(rest):
        if esc:
            esc = False
            continue
        if c == "\\" and q == '"':
            esc = True
        elif c in "\"'" and (q is None or q == c):
            q = None if q else c
        elif c == "#" and q is None:
            return rest[:j].strip(), rest[j:]
    return rest.strip(), ""


def _toml_unquote(v: str) -> str:
    try:
        return tomllib.loads("x = " + v)["x"]
    except tomllib.TOMLDecodeError:
        return v.strip("\"'")


def parse_kv(text: str, kind: str) -> list[dict]:
    out, section, comments = [], "", []
    for i, ln in enumerate(text.splitlines()):
        s = ln.strip()
        if not s:
            comments = []
            continue
        if s[0] in "#;!":
            comments.append(s.lstrip("#;!").strip())
            continue
        if kind in ("ini", "cfg") and s.startswith("[") and s.endswith("]"):
            section, comments = s[1:-1].strip(), []
            continue
        if kind == "cfg":
            if s.endswith("{"):
                section = (section + "." if section else "") + s[:-1].strip().strip('"')
                comments = []
                continue
            if s == "}":
                section = section.rsplit(".", 1)[0] if "." in section else ""
                continue
            m = re.match(r'^\s*([BIDS]):"?([^"=]+)"?\s*([=<])\s*(.*)$', ln)
            if m:
                t = {"B": "bool", "I": "int", "D": "float", "S": "string"}[m.group(1)]
                nm = m.group(2).strip()
                e = {"key": f"{section}.{nm}" if section else nm, "section": section, "name": nm, "line": i, **_meta_from_comments(comments)}
                e.update(type=t, value=m.group(4).strip()) if m.group(3) == "=" else e.update(type="raw", value="(liste)", readonly=True)
                out.append(e)
                comments = []
                continue
        sep = re.match(r"^(\s*)([^=:\s][^=:]*?)\s*([=:])\s*(.*)$", ln)
        if sep:
            from .mcsettings import unescape
            raw = sep.group(4)
            val = unescape(raw) if kind == "properties" else raw.strip()
            e = {"key": f"{section}.{sep.group(2).strip()}" if section else sep.group(2).strip(), "section": section, "name": sep.group(2).strip(), "line": i,
                 "type": _infer(val), "value": val, **_meta_from_comments(comments)}
            if e.get("options"):
                e["type"] = "enum"
            out.append(e)
        comments = []
    return out


def parse_yaml(text: str) -> list[dict]:
    out, stack, comments = [], [], []
    for i, ln in enumerate(text.splitlines()):
        s = ln.strip()
        if not s or s == "---":
            comments = [] if not s else comments
            continue
        if s.startswith("#"):
            comments.append(s.lstrip("#").strip())
            continue
        m = re.match(r"^(\s*)([A-Za-z0-9_.\-]+|'[^']+'|\"[^\"]+\"):(?:\s+(.*))?$", ln)
        if not m:
            comments = []
            continue
        ind, key, val = len(m.group(1)), m.group(2).strip("'\""), (m.group(3) or "").strip()
        while stack and stack[-1][0] >= ind:
            stack.pop()
        path = ".".join([k for _, k in stack] + [key])
        if val == "" or val in ("|", ">", "|-", ">-"):
            stack.append((ind, key))
            comments = []
            continue
        trail = ""
        if " #" in val and not val.startswith(("'", '"')):
            val, trail = val.split(" #", 1)[0].rstrip(), " #" + val.split(" #", 1)[1]
        e = {"key": path, "section": ".".join(k for _, k in stack), "name": key, "line": i, "trail": trail, **_meta_from_comments(comments)}
        if val.startswith(("[", "{", "&", "*", "!")):
            e.update(type="raw", value=val, readonly=True)
        elif val[0] in "'\"":
            e.update(type="string", value=val[1:-1] if len(val) > 1 and val[-1] == val[0] else val, quoted=val[0])
        else:
            v = val
            e.update(type="bool" if v.lower() in ("true", "false", "yes", "no", "on", "off") else _infer(v), value=v)
        out.append(e)
        comments = []
    return out


def parse_json(text: str) -> tuple[list[dict], object]:
    data = json.loads(text, strict=False)
    out = []

    def walk(o, path):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, path + [str(k)])
        elif isinstance(o, list) and all(not isinstance(x, (dict, list)) for x in o):
            out.append({"key": ".".join(path), "section": ".".join(path[:-1]), "name": path[-1] if path else "", "type": "list", "value": json.dumps(o, ensure_ascii=False), "jpath": path})
        elif isinstance(o, list):
            for idx, v in enumerate(o):
                walk(v, path + [str(idx)])
        else:
            t = "bool" if isinstance(o, bool) else "int" if isinstance(o, int) else "float" if isinstance(o, float) else "null" if o is None else "string"
            out.append({"key": ".".join(path), "section": ".".join(path[:-1]), "name": path[-1] if path else "", "type": t,
                        "value": json.dumps(o) if t in ("bool", "null") else str(o), "jpath": path, "readonly": t == "null"})
    walk(data, [])
    return out, data


def parse(text: str, fmt: str) -> list[dict]:
    if fmt == "toml":
        return parse_toml(text)
    if fmt == "json":
        return parse_json(text)[0]
    if fmt == "yaml":
        return parse_yaml(text)
    return parse_kv(text, fmt)


# ---------------------------------------------------------------------------------------------- écriture
def _validate(e: dict, raw) -> str:
    v = "true" if raw is True else "false" if raw is False else str(raw if raw is not None else "")
    if "\n" in v or "\r" in v:
        raise UserError(f"« {e['name']} » : retours à la ligne interdits.")
    t = e["type"]
    if e.get("readonly"):
        raise UserError(f"« {e['name']} » n'est pas modifiable dans l'éditeur graphique (structure complexe) : utilisez l'onglet Fichiers.")
    if t == "bool":
        if v.lower() not in ("true", "false"):
            raise UserError(f"« {e['name']} » : vrai ou faux attendu.")
        return v.lower()
    if t in ("int", "float"):
        try:
            n = int(v) if t == "int" else float(v)
        except ValueError:
            raise UserError(f"« {e['name']} » : nombre {'entier ' if t == 'int' else ''}attendu.")
        if (e.get("min") is not None and n < e["min"]) or (e.get("max") is not None and n > e["max"]):
            raise UserError(f"« {e['name']} » : valeur entre {e.get('min')} et {e.get('max')}.")
        return v.strip()
    if t == "enum" and e.get("options") and v not in e["options"]:
        raise UserError(f"« {e['name']} » : choisissez parmi {', '.join(e['options'])}.")
    if t == "list":
        try:
            parsed = json.loads(v)
        except ValueError:
            try:
                parsed = tomllib.loads("x = " + v)["x"]
            except tomllib.TOMLDecodeError:
                raise UserError(f"« {e['name']} » : liste invalide (format attendu : [\"a\", \"b\"]).")
        if not isinstance(parsed, list):
            raise UserError(f"« {e['name']} » : liste attendue.")
    return v


def apply(text: str, fmt: str, changes: dict) -> str:
    entries = {e["key"]: e for e in parse(text, fmt)}
    vals = {}
    for k, raw in changes.items():
        if k not in entries:
            raise UserError(f"Paramètre inconnu dans ce fichier : « {str(k)[:60]} ».")
        vals[k] = _validate(entries[k], raw)
    if fmt == "json":
        _, data = parse_json(text)
        for k, v in vals.items():
            e = entries[k]
            node = data
            for p in e["jpath"][:-1]:
                node = node[int(p)] if isinstance(node, list) else node[p]
            last = e["jpath"][-1]
            nv = json.loads(v) if e["type"] in ("bool", "list") else int(v) if e["type"] == "int" else float(v) if e["type"] == "float" else v
            if isinstance(node, list):
                node[int(last)] = nv
            else:
                node[last] = nv
        m = re.search(r"\n([ \t]+)\S", text)
        indent = m.group(1) if m else "  "
        out = json.dumps(data, indent=indent if "\t" not in indent else "\t", ensure_ascii=False) + ("\n" if text.endswith("\n") else "")
        json.loads(out)
        return out
    lines = text.splitlines()
    for k, v in vals.items():
        e = entries[k]
        ln = lines[e["line"]]
        if fmt == "toml":
            m = re.match(r"^(\s*[^=]+?=\s*)", ln)
            if e["type"] in ("string", "enum"):
                nv = json.dumps(v, ensure_ascii=False)
            elif e["type"] == "list":
                nv = v if v.startswith("[") else "[" + v + "]"
                nv = re.sub(r"\[\s*\]", "[]", nv)
            else:
                nv = v
            lines[e["line"]] = m.group(1) + nv + ((" " + e["trail"]) if e.get("trail") else "")
        elif fmt == "cfg":
            lines[e["line"]] = re.sub(r"=.*$", "=" + v, ln, count=1)
        elif fmt == "yaml":
            m = re.match(r"^(\s*[^:]+:\s+)", ln)
            q = e.get("quoted")
            nv = (q + v.replace(q, "\\" + q if q == '"' else q + q) + q) if q else (json.dumps(v, ensure_ascii=False) if e["type"] == "string" and re.search(r"[:#\[\]{},&*!|>'\"%@`]|^\s|\s$", v) else v)
            lines[e["line"]] = m.group(1) + nv + e.get("trail", "")
        else:
            from .mcsettings import escape
            m = re.match(r"^(\s*[^=:]+?\s*[=:]\s*)", ln)
            lines[e["line"]] = m.group(1) + (escape(v) if fmt == "properties" else v)
    out = "\n".join(lines) + ("\n" if text.endswith("\n") or not text else "")
    if fmt == "toml":
        try:
            tomllib.loads(out)
        except tomllib.TOMLDecodeError as ex:
            raise UserError(f"La modification rendrait le fichier TOML invalide ({ex}) : rien n'a été écrit.")
    return out


# ---------------------------------------------------------------------------------------------- service
class ModConfig:
    def __init__(self, panel):
        self.P = panel

    def _root(self, sid: str) -> tuple[dict, Path]:
        srv = self.P.manager.server(sid)
        root = Path(srv["path"])
        if not self.P.server_dir_ok(root):
            raise UserError("Dossier du serveur incohérent.")
        return srv, root

    def _file(self, root: Path, rel: str) -> tuple[Path, str]:
        rel = (rel or "").replace("\\", "/").lstrip("/")
        p = (root / rel)
        try:
            rp = p.resolve()
        except OSError:
            raise UserError("Fichier introuvable.")
        if root.resolve() not in rp.parents or p.is_symlink() or not p.is_file():
            raise UserError("Fichier introuvable ou hors du dossier du serveur.")
        if not (rel.split("/")[0] in ROOTS or rel.startswith("world/serverconfig/") or "/serverconfig/" in rel):
            raise UserError("Seuls les fichiers de configuration (config/, defaultconfigs/, plugins/, serverconfig/) sont modifiables ici.")
        fmt = EXTS.get(p.suffix.lower())
        if not fmt:
            raise UserError("Format de fichier non pris en charge par l'éditeur graphique.")
        if p.stat().st_size > MAX_SIZE:
            raise UserError("Fichier trop volumineux pour l'éditeur graphique (1 Mo maximum).")
        if fmt == "cfg" and not re.search(r"^\s*[BIDS]:", p.read_text(encoding="utf-8", errors="replace"), re.M):
            fmt = "ini"
        return p, fmt

    def files(self, sid: str) -> list[dict]:
        srv, root = self._root(sid)
        out = []
        bases = [root / r for r in ROOTS] + [d for d in root.glob("*/serverconfig") if d.is_dir()]
        for base in bases:
            if not base.is_dir() or base.is_symlink():
                continue
            for p in sorted(base.rglob("*")):
                if len(out) >= 1500:
                    break
                rel = p.relative_to(root).as_posix()
                if p.is_symlink() or not p.is_file() or p.suffix.lower() not in EXTS or rel.count("/") > 5 or p.stat().st_size > MAX_SIZE:
                    continue
                if rel.startswith("plugins/") and rel.count("/") < 2:
                    continue                                 # fichiers posés à la racine de plugins/ : rarement des configurations
                mod = rel.split("/")[1] if rel.startswith("plugins/") else (Path(rel).stem.split("-")[0] if rel.count("/") == 1 else rel.split("/")[1])
                out.append({"path": rel, "format": EXTS[p.suffix.lower()], "size": p.stat().st_size, "mtime": p.stat().st_mtime, "group": mod})
        return out

    def _bdir(self, sid: str, rel: str) -> Path:
        d = self.P.s.state_dir / "config-backups" / sid / rel.replace("/", "__")
        d.mkdir(parents=True, exist_ok=True)
        return d

    def get(self, sid: str, rel: str) -> dict:
        srv, root = self._root(sid)
        p, fmt = self._file(root, rel)
        text = p.read_text(encoding="utf-8", errors="replace")
        orig = self._bdir(sid, rel) / "original"
        if not orig.exists():
            orig.write_text(text, encoding="utf-8")                  # première version vue par le panneau
        try:
            entries = parse(text, fmt)
            err = None
        except (ValueError, tomllib.TOMLDecodeError) as e:
            entries, err = [], f"Fichier illisible par l'éditeur graphique ({e}). Modifiez-le dans l'onglet Fichiers."
        oentries = {}
        try:
            oentries = {e["key"]: e["value"] for e in parse(orig.read_text(encoding="utf-8", errors="replace"), fmt)}
        except Exception:  # noqa: BLE001
            pass
        for e in entries:
            e["original"] = oentries.get(e["key"])
            e.pop("jpath", None)
        return {"path": rel, "format": fmt, "entries": entries, "error": err, "mtime": p.stat().st_mtime,
                "running": bool(self.P.manager.alive(sid)), "backups": self.backups(sid, rel), "ui": self.custom_ui(rel)}

    def save(self, sid: str, rel: str, changes: dict, mtime: float | None = None) -> dict:
        srv, root = self._root(sid)
        p, fmt = self._file(root, rel)
        if not isinstance(changes, dict) or not changes:
            raise UserError("Aucune modification.")
        if mtime is not None and abs(p.stat().st_mtime - float(mtime)) > 0.001:
            raise UserError("Le fichier a été modifié depuis son ouverture (par le serveur ou un autre onglet). Rechargez : rien n'a été écrit.")
        text = p.read_text(encoding="utf-8", errors="replace")
        new = apply(text, fmt, changes)
        self._backup(sid, rel, text)
        tmp = p.with_name(p.name + ".panel-tmp")
        tmp.write_text(new, encoding="utf-8")
        os.replace(tmp, p)
        return {"saved": sorted(changes), "restart_required": bool(self.P.manager.alive(sid))}

    def _backup(self, sid, rel, text):
        d = self._bdir(sid, rel)
        (d / f"{time.time():.3f}.bak").write_text(text, encoding="utf-8")
        baks = sorted(d.glob("*.bak"))
        for old in baks[:-20]:
            old.unlink(missing_ok=True)

    def backups(self, sid: str, rel: str) -> list[dict]:
        d = self._bdir(sid, rel)
        out = [{"id": b.stem, "ts": float(b.stem), "size": b.stat().st_size} for b in sorted(d.glob("*.bak"), reverse=True)]
        if (d / "original").exists():
            out.append({"id": "original", "ts": (d / "original").stat().st_mtime, "size": (d / "original").stat().st_size, "original": True})
        return out

    def restore(self, sid: str, rel: str, bid: str) -> dict:
        srv, root = self._root(sid)
        p, fmt = self._file(root, rel)
        d = self._bdir(sid, rel)
        src = d / ("original" if bid == "original" else f"{bid}.bak")
        if not re.fullmatch(r"original|\d+\.\d+", bid or "") or not src.exists():
            raise UserError("Copie introuvable.")
        self._backup(sid, rel, p.read_text(encoding="utf-8", errors="replace"))
        tmp = p.with_name(p.name + ".panel-tmp")
        shutil.copyfile(src, tmp)
        os.replace(tmp, p)
        return {"restored": bid, "restart_required": bool(self.P.manager.alive(sid))}

    def regenerate(self, sid: str, rel: str) -> dict:
        """Valeurs par défaut du mod : le fichier est mis de côté (copie) puis retiré ; le mod le recrée au prochain démarrage."""
        srv, root = self._root(sid)
        p, fmt = self._file(root, rel)
        if self.P.manager.alive(sid):
            raise UserError("Arrêtez le serveur avant de régénérer un fichier de configuration (le mod le recrée au démarrage).")
        self._backup(sid, rel, p.read_text(encoding="utf-8", errors="replace"))
        p.unlink()
        return {"removed": rel}

    def custom_ui(self, rel: str) -> list[dict]:
        d = self.P.s.state_dir / "config-ui.d"
        out = []
        if d.is_dir():
            for f in sorted(d.glob("*.json")):
                try:
                    j = json.loads(f.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if isinstance(j, dict) and j.get("file") == rel and isinstance(j.get("categories"), list):
                    out.append(j)
        return out
