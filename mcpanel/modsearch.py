"""Recherche et installation de mods / plugins depuis Modrinth et CurseForge (V4 §3).

- Modrinth : API publique v2, sans clé.  CurseForge : API v1, clé personnelle obligatoire (Paramètres).
- Respect des plateformes : un fichier CurseForge dont l'auteur interdit la distribution par des outils tiers
  (downloadUrl absent) n'est JAMAIS téléchargé par le panneau : un lien vers la page est proposé pour l'import manuel.
- Chaque fichier est téléchargé en « .part », vérifié par empreinte (SHA-512 / SHA-1) puis renommé ; annulable.
- Dépendances obligatoires résolues récursivement, déjà-installés ignorés, incompatibilités signalées avant toute action.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
import urllib.parse
from pathlib import Path

from .jobs import UserError
from .sources import SourceError
from .util import now

MR = "https://api.modrinth.com/v2"
CF = "https://api.curseforge.com/v1"
CF_GAME, CF_MODS, CF_PLUGINS = 432, 6, 5
CF_LOADER = {"forge": 1, "fabric": 4, "quilt": 5, "neoforge": 6}
CF_REL = {1: "embedded", 2: "optional", 3: "required", 4: "tool", 5: "incompatible", 6: "include"}
CF_TYPE = {1: "release", 2: "beta", 3: "alpha"}
DL_HOSTS = {"cdn.modrinth.com", "edge.forgecdn.net", "mediafilez.forgecdn.net", "media.forgecdn.net"}
FNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.+()\[\]-]{0,150}\.jar$")


def target(srv: dict, adapters: dict, kind: str | None = None) -> dict:
    """Ce que ce serveur peut charger : dossier, loaders Modrinth, type CurseForge."""
    ad = adapters.get(srv["server_type"])
    t = srv["server_type"]
    if kind not in (None, "mod", "plugin"):
        raise UserError("Type de contenu invalide.")
    mods_ok, plugins_ok = bool(ad and ad.mods), bool(ad and ad.plugins)
    if kind is None:
        kind = "mod" if mods_ok else "plugin" if plugins_ok else None
    if kind == "mod" and mods_ok:
        base = {"forge": ["forge"], "neoforge": ["neoforge"], "fabric": ["fabric"], "quilt": ["quilt", "fabric"],
                "mohist": ["forge"], "magma": ["forge"], "arclight": ["forge"]}.get(t, ["forge", "neoforge", "fabric", "quilt"])
        return {"kind": "mod", "folder": "mods", "loaders": base, "project_type": "mod", "cf_class": CF_MODS, "cf_loader": CF_LOADER.get(base[0])}
    if kind == "plugin" and plugins_ok:
        base = {"paper": ["paper", "spigot", "bukkit", "purpur"], "purpur": ["purpur", "paper", "spigot", "bukkit"], "spigot": ["spigot", "bukkit"],
                "bukkit": ["bukkit"]}.get(t, ["spigot", "bukkit", "paper"])
        return {"kind": "plugin", "folder": "plugins", "loaders": base, "project_type": "plugin", "cf_class": CF_PLUGINS, "cf_loader": None}
    raise UserError("Ce type de serveur ne charge ni mods ni plugins (Vanilla) : choisissez un serveur Forge, NeoForge, Fabric, Quilt, Paper…")


class ModSearch:
    def __init__(self, panel):
        self.P = panel

    # ------------------------------------------------------------------ outils
    @property
    def http(self):
        return self.P.http

    def _cf_key(self) -> str:
        k = self.P.db.get_setting("curseforge_key") or ""
        if not k:
            raise UserError("CurseForge demande une clé d'API personnelle (gratuite, sur console.curseforge.com) : ajoutez-la dans Paramètres.")
        return k

    def _cf(self, path: str, params: dict | None = None):
        q = ("?" + urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v not in (None, "")})) if params else ""
        return self.http.get_json(CF + path + q, {"x-api-key": self._cf_key(), "Accept": "application/json"})

    def _mr(self, path: str, params: dict | None = None):
        q = ("?" + urllib.parse.urlencode({k: (json.dumps(v) if isinstance(v, list) else v) for k, v in (params or {}).items() if v not in (None, "")})) if params else ""
        return self.http.get_json(MR + path + q)

    # ------------------------------------------------------------------ normalisation
    @staticmethod
    def _mr_hit(h: dict) -> dict:
        return {"source": "modrinth", "id": h.get("project_id") or h.get("id"), "slug": h.get("slug"), "title": h.get("title"), "author": h.get("author"),
                "description": h.get("description") or "", "icon": h.get("icon_url"), "downloads": h.get("downloads", 0), "categories": h.get("display_categories") or h.get("categories") or [],
                "server_side": h.get("server_side"), "client_side": h.get("client_side"), "updated": h.get("date_modified"),
                "url": f"https://modrinth.com/{h.get('project_type') or 'mod'}/{h.get('slug') or h.get('project_id') or h.get('id')}"}

    @staticmethod
    def _cf_hit(m: dict) -> dict:
        return {"source": "curseforge", "id": str(m["id"]), "slug": m.get("slug"), "title": m.get("name"), "author": ", ".join(a.get("name", "") for a in m.get("authors") or []),
                "description": m.get("summary") or "", "icon": (m.get("logo") or {}).get("thumbnailUrl"), "downloads": int(m.get("downloadCount") or 0),
                "categories": [c.get("name") for c in m.get("categories") or []], "server_side": None, "client_side": None, "updated": m.get("dateModified"),
                "url": (m.get("links") or {}).get("websiteUrl") or f"https://www.curseforge.com/minecraft/mc-mods/{m.get('slug')}",
                "distribution": m.get("allowModDistribution") is not False}

    @staticmethod
    def _mr_ver(v: dict) -> dict:
        files = [{"url": f["url"], "filename": f["filename"], "sha1": (f.get("hashes") or {}).get("sha1"), "sha512": (f.get("hashes") or {}).get("sha512"),
                  "size": f.get("size", 0), "primary": bool(f.get("primary"))} for f in v.get("files") or []]
        return {"source": "modrinth", "id": v["id"], "project_id": v["project_id"], "name": v.get("name") or v.get("version_number"), "number": v.get("version_number"),
                "type": v.get("version_type", "release"), "date": v.get("date_published"), "mc_versions": v.get("game_versions") or [], "loaders": v.get("loaders") or [],
                "files": files, "distributable": True,
                "deps": [{"project_id": d.get("project_id"), "version_id": d.get("version_id"), "type": d.get("dependency_type")} for d in v.get("dependencies") or []]}

    @staticmethod
    def _cf_ver(f: dict, mod_id: str) -> dict:
        sha1 = next((h["value"] for h in f.get("hashes") or [] if h.get("algo") == 1), None)
        return {"source": "curseforge", "id": str(f["id"]), "project_id": str(mod_id), "name": f.get("displayName") or f.get("fileName"), "number": f.get("displayName"),
                "type": CF_TYPE.get(f.get("releaseType"), "release"), "date": f.get("fileDate"), "mc_versions": [g for g in f.get("gameVersions") or [] if re.match(r"^\d", g)],
                "loaders": [g.lower() for g in f.get("gameVersions") or [] if g.lower() in CF_LOADER], "distributable": bool(f.get("downloadUrl")),
                "files": [{"url": f.get("downloadUrl"), "filename": f.get("fileName"), "sha1": sha1, "sha512": None, "size": f.get("fileLength", 0), "primary": True}],
                "deps": [{"project_id": str(d.get("modId")), "version_id": None, "type": CF_REL.get(d.get("relationType"), "optional")} for d in f.get("dependencies") or []]}

    # ------------------------------------------------------------------ recherche
    def categories(self, source: str, kind: str = "mod") -> list[str]:
        if source == "modrinth":
            return sorted({c["name"] for c in self._mr("/tag/category") if c.get("project_type") == kind and c.get("header") in ("categories", None)})
        return sorted({c["name"] for c in self._cf("/categories", {"gameId": CF_GAME, "classId": CF_MODS if kind == "mod" else CF_PLUGINS}).get("data", []) if not c.get("isClass")})

    def search(self, srv: dict, source: str, query: str = "", category: str = "", offset: int = 0, kind: str | None = None, any_version: bool = False) -> dict:
        t = target(srv, self.P.sources.adapters, kind)
        offset, limit = max(0, int(offset)), 20
        if source == "modrinth":
            facets = [[f"project_type:{t['project_type']}"], [f"categories:{x}" for x in t["loaders"]]]
            if not any_version:
                facets.append([f"versions:{srv['mc_version']}"])
            if category:
                facets.append([f"categories:{category}"])
            r = self._mr("/search", {"query": query[:100], "facets": facets, "limit": limit, "offset": offset, "index": "relevance" if query else "downloads"})
            return {"total": r.get("total_hits", 0), "hits": [self._mr_hit(h) for h in r.get("hits", [])], "target": t}
        if source == "curseforge":
            r = self._cf("/mods/search", {"gameId": CF_GAME, "classId": t["cf_class"], "searchFilter": query[:100], "gameVersion": None if any_version else srv["mc_version"],
                                          "modLoaderType": t["cf_loader"], "sortField": 2, "sortOrder": "desc", "index": offset, "pageSize": limit,
                                          "categoryId": category or None})
            return {"total": (r.get("pagination") or {}).get("totalCount", 0), "hits": [self._cf_hit(m) for m in r.get("data", [])], "target": t}
        raise UserError("Source inconnue.")

    def project(self, srv: dict, source: str, pid: str, kind: str | None = None) -> dict:
        """Fiche complète : informations, versions compatibles et relations (dépendances / incompatibilités) cliquables."""
        t = target(srv, self.P.sources.adapters, kind)
        if source == "modrinth":
            p = self._mr(f"/project/{urllib.parse.quote(pid, safe='')}")
            info = self._mr_hit({**p, "project_id": p["id"], "author": None})
            info["body"] = (p.get("body") or "")[:20000]
            info["mc_versions"], info["loaders"] = p.get("game_versions") or [], p.get("loaders") or []
            try:
                team = self._mr(f"/project/{p['id']}/members")
                info["author"] = ", ".join(m.get("user", {}).get("username", "") for m in team if m.get("role") in ("Owner", "owner")) or ", ".join(m.get("user", {}).get("username", "") for m in team[:2])
            except SourceError:
                pass
        else:
            info = self._cf_hit(self._cf(f"/mods/{int(pid)}")["data"])
            info["body"] = ""
        versions = self.versions(srv, source, info["id"], kind=t["kind"])
        rel_ids = {}
        for v in versions[:1]:
            for d in v["deps"]:
                if d["project_id"] and d["type"] in ("required", "optional", "incompatible", "embedded"):
                    rel_ids[d["project_id"]] = d["type"]
        relations = []
        if rel_ids:
            try:
                if source == "modrinth":
                    for p in self._mr("/projects", {"ids": list(rel_ids)}):
                        relations.append({"source": source, "id": p["id"], "title": p.get("title"), "type": rel_ids[p["id"]], "icon": p.get("icon_url")})
                else:
                    data = self.http.post_json(CF + "/mods", {"modIds": [int(x) for x in rel_ids]}, {"x-api-key": self._cf_key()}).get("data", [])
                    for m in data:
                        relations.append({"source": source, "id": str(m["id"]), "title": m.get("name"), "type": rel_ids[str(m["id"])], "icon": (m.get("logo") or {}).get("thumbnailUrl")})
            except (SourceError, KeyError, ValueError):
                relations = [{"source": source, "id": k, "title": k, "type": v, "icon": None} for k, v in rel_ids.items()]
        inst = self.installed_projects(srv)
        for r in relations:
            r["installed"] = (source, r["id"]) in inst
        return {"info": info, "versions": versions[:30], "relations": sorted(relations, key=lambda r: ["required", "optional", "incompatible", "embedded"].index(r["type"]) if r["type"] in ("required", "optional", "incompatible", "embedded") else 9),
                "installed": (source, info["id"]) in inst, "target": t}

    def versions(self, srv: dict, source: str, pid: str, kind: str | None = None) -> list[dict]:
        t = target(srv, self.P.sources.adapters, kind)
        if source == "modrinth":
            vs = self._mr(f"/project/{urllib.parse.quote(pid, safe='')}/version", {"loaders": t["loaders"], "game_versions": [srv["mc_version"]]})
            out = [self._mr_ver(v) for v in vs]
        else:
            r = self._cf(f"/mods/{int(pid)}/files", {"gameVersion": srv["mc_version"], "modLoaderType": t["cf_loader"], "pageSize": 50})
            out = [self._cf_ver(f, pid) for f in r.get("data", [])]
        rank = {"release": 0, "beta": 1, "alpha": 2}
        out.sort(key=lambda v: (v["date"] or ""), reverse=True)
        out.sort(key=lambda v: rank.get(v["type"], 3))
        return out

    # ------------------------------------------------------------------ installés
    def installed_projects(self, srv: dict) -> set:
        return {(r["source"], r["project_id"]) for r in self.P.db.query("SELECT source, project_id FROM installed_mods WHERE server_id=?", (srv["id"],))}

    def _get_version(self, srv, source, vid, pid=None, kind=None) -> dict:
        if source == "modrinth":
            return self._mr_ver(self._mr(f"/version/{urllib.parse.quote(vid, safe='')}"))
        if pid is None:
            raise UserError("Projet CurseForge manquant.")
        return self._cf_ver(self._cf(f"/mods/{int(pid)}/files/{int(vid)}")["data"], pid)

    def plan(self, srv: dict, source: str, version_id: str, project_id: str | None = None, kind: str | None = None) -> dict:
        t = target(srv, self.P.sources.adapters, kind)
        inst = self.installed_projects(srv)
        root = self._get_version(srv, source, version_id, project_id, kind)
        todo, done, conflicts, warnings, optional, already = [root], {}, [], [], [], []
        reasons = {root["project_id"]: "demandé"}
        while todo:
            v = todo.pop(0)
            if v["project_id"] in done:
                continue
            done[v["project_id"]] = v
            for d in v["deps"]:
                pid = d["project_id"]
                if d["type"] == "incompatible" and pid and (source, pid) in inst:
                    conflicts.append(f"{v['name']} est déclaré incompatible avec un mod déjà installé ({pid}).")
                if d["type"] == "optional" and pid:
                    optional.append(pid)
                if d["type"] != "required" or not (pid or d["version_id"]):
                    continue
                if pid and ((source, pid) in inst or pid in done):
                    if (source, pid) in inst:
                        already.append(pid)
                    continue
                try:
                    if d["version_id"]:
                        dv = self._get_version(srv, source, d["version_id"], pid, kind)
                    else:
                        cands = self.versions(srv, source, pid, kind=t["kind"])
                        if not cands:
                            conflicts.append(f"Dépendance obligatoire {pid} : aucune version compatible avec Minecraft {srv['mc_version']} ({', '.join(t['loaders'])}).")
                            continue
                        dv = cands[0]
                except SourceError as e:
                    conflicts.append(f"Dépendance {pid} introuvable : {e}")
                    continue
                reasons.setdefault(dv["project_id"], f"dépendance de {v['name']}")
                todo.append(dv)
        items = []
        for pid, v in done.items():
            f = next((x for x in v["files"] if x["primary"]), (v["files"] or [None])[0])
            if not f:
                conflicts.append(f"{v['name']} : aucun fichier.")
                continue
            dest = Path(srv["path"]) / t["folder"] / str(f["filename"])
            items.append({"source": source, "project_id": pid, "version_id": v["id"], "name": v["name"], "filename": f["filename"], "size": f["size"],
                          "reason": reasons.get(pid, ""), "distributable": v["distributable"] and bool(f["url"]), "exists": dest.exists(),
                          "page": f"https://www.curseforge.com/projects/{pid}" if source == "curseforge" else f"https://modrinth.com/project/{pid}"})
        if (source, root["project_id"]) in inst:
            warnings.append("Ce mod est déjà installé : la version choisie sera ajoutée, pensez à retirer l'ancienne (ou utilisez « Mettre à jour »).")
        return {"items": items, "conflicts": conflicts, "warnings": warnings, "already_installed": sorted(set(already)), "folder": t["folder"]}

    def install(self, job, srv: dict, source: str, version_id: str, project_id: str | None = None, kind: str | None = None,
                replace: str | None = None) -> dict:
        t = target(srv, self.P.sources.adapters, kind)
        plan = self.plan(srv, source, version_id, project_id, kind)
        if plan["conflicts"]:
            raise UserError("Installation annulée : " + " ".join(plan["conflicts"]))
        folder = Path(srv["path"]) / t["folder"]
        folder.mkdir(exist_ok=True)
        installed, manual = [], []
        total = len(plan["items"]) or 1
        for i, it in enumerate(plan["items"]):
            job.check_cancel()
            if not it["distributable"]:
                manual.append(it)
                job.log(f"« {it['name']} » : l'auteur n'autorise pas le téléchargement par des outils tiers. Téléchargez-le depuis {it['page']} puis déposez le .jar.")
                continue
            if not FNAME.match(it["filename"] or ""):
                raise UserError(f"Nom de fichier refusé : {it['filename']!r}")
            v = self._get_version(srv, source, it["version_id"], it["project_id"], kind)
            f = next((x for x in v["files"] if x["filename"] == it["filename"]), None)
            dest = folder / it["filename"]
            exp = {"sha512": f["sha512"]} if f.get("sha512") else {"sha1": f["sha1"]} if f.get("sha1") else None
            if dest.exists():
                if exp and hashlib.new(list(exp)[0], dest.read_bytes()).hexdigest() == list(exp.values())[0]:
                    job.log(f"{it['filename']} déjà présent (identique) : conservé.")
                    self._record(srv, t["folder"], it, f)
                    continue
                raise UserError(f"Un autre fichier « {it['filename']} » existe déjà : rien n'a été écrasé.")
            job.step(f"Téléchargement de {it['name']}", 5 + 90 * i / total)
            self.http.download(f["url"], dest, expected=exp, allowed_hosts=DL_HOSTS, max_bytes=512 * 1024 * 1024,
                               progress=lambda dn, tt, i=i: (job.check_cancel(), job.progress(5 + 90 * (i + (dn / tt if tt else 0)) / total)))
            job.log(f"{it['filename']} installé" + (" et vérifié (" + list(exp)[0].upper() + ")." if exp else " (aucune empreinte publiée : non vérifié)."))
            self._record(srv, t["folder"], it, f)
            installed.append(it["filename"])
        if replace and installed:
            old = folder / replace
            if old.exists() and old.name not in installed and FNAME.match(old.name):
                keep = self.P.s.state_dir / "mods-remplaces" / srv["id"]
                keep.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old), keep / f"{int(time.time())}-{old.name}")
                self.P.db.execute("DELETE FROM installed_mods WHERE server_id=? AND folder=? AND filename=?", (srv["id"], t["folder"], old.name))
                job.log(f"Ancienne version {old.name} mise de côté (récupérable) : {keep}")
        return {"installed": installed, "manual": manual, "restart_needed": bool(installed) and bool(self.P.manager.alive(srv["id"]))}

    def _record(self, srv, folder, it, f):
        self.P.db.execute("INSERT OR REPLACE INTO installed_mods(server_id,filename,folder,source,project_id,version_id,title,version_name,sha1,installed_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                          (srv["id"], it["filename"], folder, it["source"], it["project_id"], it["version_id"], it["name"], it["name"], f.get("sha1"), now()))

    def updates(self, srv: dict) -> list[dict]:
        out = []
        for r in self.P.db.query("SELECT * FROM installed_mods WHERE server_id=?", (srv["id"],)):
            if not (Path(srv["path"]) / r["folder"] / r["filename"]).exists():
                continue
            try:
                vs = [v for v in self.versions(srv, r["source"], r["project_id"], kind="plugin" if r["folder"] == "plugins" else "mod") if v["type"] == "release"]
            except (SourceError, UserError):
                continue
            if vs and vs[0]["id"] != r["version_id"]:
                out.append({"filename": r["filename"], "folder": r["folder"], "source": r["source"], "project_id": r["project_id"], "current": r["version_name"],
                            "latest": vs[0]["name"], "latest_id": vs[0]["id"], "date": vs[0]["date"]})
        return out
