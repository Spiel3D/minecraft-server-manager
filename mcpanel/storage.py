"""Stockage des mondes (V4 §7) : mondes actifs sur le SSD (quota), le reste sur le disque de données (HDD).

Règles de sûreté, toutes vérifiées par les tests :
- un serveur n'est JAMAIS déplacé s'il tourne (démarrages bloqués pendant l'opération) ;
- copie dans un dossier temporaire « .panel-copy », vérification fichier par fichier (taille + SHA-256),
  bascule du chemin en base, et seulement ensuite suppression de l'original ;
- espace libre contrôlé avant de copier (marge comprise) ; une copie interrompue (coupure, redémarrage du panneau)
  ne laisse qu'un dossier temporaire, nettoyé au démarrage, l'original n'ayant pas été touché ;
- quota SSD (50 Go par défaut) : pour faire de la place, les serveurs arrêtés démarrés il y a le plus longtemps
  repartent sur le HDD ; un serveur allumé n'est jamais déplacé.
Archives de mondes : ZIP vérifié + métadonnées (serveur, version, tailles, statistiques) sur le HDD ; restauration via
dossier temporaire, l'éventuel monde présent étant renommé (jamais supprimé).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import zipfile
from pathlib import Path

from .jobs import UserError
from .util import new_id, now

GB = 1024 ** 3
SAFETY_FREE = 10 * GB            # jamais moins de 10 Go libres sur le SSD


def dir_size(p: Path) -> int:
    total = 0
    for dp, dns, fns in os.walk(p):
        for f in fns:
            fp = Path(dp) / f
            try:
                if not fp.is_symlink():
                    total += fp.stat().st_size
            except OSError:
                pass
    return total


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class Storage:
    def __init__(self, panel):
        self.P = panel
        self._size_cache: dict[str, tuple[float, int]] = {}

    # ------------------------------------------------------------------ réglages
    @property
    def ssd_dir(self) -> Path:
        return Path(self.P.db.get_setting("ssd_dir") or (self.P.s.state_dir / "ssd"))

    @property
    def quota(self) -> int:
        try:
            return int(float(self.P.db.get_setting("ssd_quota_gb") or 50) * GB)
        except ValueError:
            return 50 * GB

    @property
    def tiering(self) -> bool:
        """False sur une machine à disque unique (MCPANEL_SSD_TIER=0) : pas de niveau SSD, les mondes restent dans data_root."""
        return bool(self.P.s.ssd_tier)

    @property
    def auto(self) -> bool:
        return self.tiering and (self.P.db.get_setting("ssd_auto") or "1") == "1"

    @property
    def archives_dir(self) -> Path:
        return self.P.s.data_root / "archives"

    def tier(self, srv: dict) -> str:
        return "ssd" if Path(srv["path"]).parent.resolve() == Path(os.path.realpath(self.ssd_dir)) else "hdd"

    def size(self, srv: dict, max_age: float = 120) -> int:
        c = self._size_cache.get(srv["id"])
        if c and time.time() - c[0] < max_age:
            return c[1]
        n = dir_size(Path(srv["path"])) if Path(srv["path"]).is_dir() else 0
        self._size_cache[srv["id"]] = (time.time(), n)
        return n

    def overview(self) -> dict:
        rows = []
        for srv in self.P.db.query("SELECT * FROM servers"):
            last_bk = self.P.db.one("SELECT MAX(created_at) t FROM backups WHERE server_id=?", (srv["id"],))["t"]
            rows.append({"id": srv["id"], "name": self.P.label(srv), "tier": self.tier(srv), "path": srv["path"], "size": self.size(srv),
                         "last_started": srv.get("last_started"), "last_backup": last_bk, "running": bool(self.P.manager.alive(srv["id"]))})
        used = sum(r["size"] for r in rows if r["tier"] == "ssd")
        if self.tiering:
            self.ssd_dir.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(self.ssd_dir if self.ssd_dir.exists() else self.P.s.state_dir).free
        return {"tiering": self.tiering, "ssd_dir": str(self.ssd_dir), "quota": self.quota, "ssd_used": used, "ssd_free_disk": free, "auto": self.auto,
                "hdd_dir": str(self.P.s.servers_dir), "hdd_free_disk": shutil.disk_usage(self.P.s.servers_dir).free if self.P.s.servers_dir.exists() else 0,
                "servers": rows, "archives": self.list_archives()}

    # ------------------------------------------------------------------ déplacement
    def plan_promotion(self, srv: dict) -> dict:
        """Ce qu'il faudrait faire pour mettre ce serveur sur le SSD (évictions comprises)."""
        if not self.tiering:
            return {"possible": False, "reason": "Machine à disque unique : il n'y a pas de niveau SSD séparé.", "evict": []}
        need = self.size(srv, 0)
        if need > self.quota:
            return {"possible": False, "reason": f"Le serveur ({need / GB:.1f} Go) dépasse à lui seul le quota SSD ({self.quota / GB:.0f} Go).", "evict": []}
        on_ssd = [s for s in self.P.db.query("SELECT * FROM servers") if s["id"] != srv["id"] and self.tier(s) == "ssd"]
        used = sum(self.size(s) for s in on_ssd)
        evict = []
        for s in sorted(on_ssd, key=lambda x: x.get("last_started") or 0):           # démarrés il y a le plus longtemps d'abord
            if used + need <= self.quota:
                break
            if self.P.manager.alive(s["id"]):
                continue                                                             # jamais un serveur allumé
            evict.append(s)
            used -= self.size(s)
        if used + need > self.quota:
            return {"possible": False, "reason": "Quota SSD atteint et les serveurs qui l'occupent sont allumés.", "evict": []}
        self.ssd_dir.mkdir(parents=True, exist_ok=True)
        freed = sum(self.size(s) for s in evict)
        if shutil.disk_usage(self.ssd_dir).free + freed - need < SAFETY_FREE:
            return {"possible": False, "reason": "Pas assez d'espace libre sur le SSD (10 Go doivent toujours rester libres).", "evict": []}
        return {"possible": True, "need": need, "evict": [{"id": s["id"], "name": self.P.label(s), "size": self.size(s)} for s in evict]}

    def move(self, job, sid: str, tier: str) -> dict:
        if tier not in ("ssd", "hdd"):
            raise UserError("Destination invalide.")
        mgr = self.P.manager
        srv = mgr.server(sid)
        if self.tier(srv) == tier:
            return {"moved": False, "tier": tier}
        if tier == "ssd" and not self.tiering:
            raise UserError("Machine à disque unique : il n'y a pas de niveau SSD séparé.")
        if mgr.alive(sid):
            raise UserError("Arrêtez le serveur avant de le déplacer : un serveur allumé n'est jamais déplacé.")
        if tier == "hdd" and not self.P.data_ready():
            raise UserError("Le disque de données n'est pas monté : déplacement impossible.")
        mgr.blocked[sid] = "déplacement de stockage"
        try:
            src = Path(srv["path"])
            if not self.P.server_dir_ok(src) or not src.is_dir():
                raise UserError("Dossier du serveur incohérent : déplacement refusé.")
            dst_root = self.ssd_dir if tier == "ssd" else self.P.s.servers_dir
            dst_root.mkdir(parents=True, exist_ok=True)
            dst = dst_root / src.name
            tmp = dst_root / (src.name + ".panel-copy")
            need = self.size(srv, 0)
            if shutil.disk_usage(dst_root).free < need * 1.1 + (SAFETY_FREE if tier == "ssd" else GB):
                raise UserError(f"Espace insuffisant sur la destination ({need / GB:.1f} Go nécessaires + marge).")
            if dst.exists():
                orphan = dst_root / f"{src.name}.orphelin-{int(time.time())}"
                dst.rename(orphan)
                job.log(f"Un dossier « {dst.name} » non référencé existait déjà : renommé en {orphan.name} (rien n'est supprimé).")
            shutil.rmtree(tmp, ignore_errors=True)
            job.step(f"Copie vers le {'SSD' if tier == 'ssd' else 'HDD'}", 5)
            files = [Path(dp) / f for dp, _, fs in os.walk(src) for f in fs]
            done_b = 0
            for i, f in enumerate(files):
                job.check_cancel()
                rel = f.relative_to(src)
                out = tmp / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                if f.is_symlink():
                    continue
                shutil.copy2(f, out)
                done_b += out.stat().st_size
                if i % 20 == 0:
                    job.progress(5 + 60 * done_b / max(need, 1))
            job.step("Vérification de la copie (taille et SHA-256 de chaque fichier)", 66)
            for i, f in enumerate(files):
                if f.is_symlink():
                    continue
                c = tmp / f.relative_to(src)
                if not c.is_file() or c.stat().st_size != f.stat().st_size or _sha(c) != _sha(f):
                    shutil.rmtree(tmp, ignore_errors=True)
                    raise UserError(f"Copie invalide pour {f.relative_to(src)} : déplacement annulé, l'original est intact.")
                if i % 20 == 0:
                    job.progress(66 + 28 * i / max(len(files), 1))
            tmp.rename(dst)
            self.P.db.execute("UPDATE servers SET path=? WHERE id=?", (str(dst), sid))
            job.step("Suppression de l'original (copie vérifiée)", 96)
            shutil.rmtree(src)
            self._size_cache.pop(sid, None)
            job.log(f"Serveur déplacé : {src} → {dst}")
            return {"moved": True, "tier": tier, "path": str(dst)}
        finally:
            mgr.blocked.pop(sid, None)

    def promote_and_start(self, job, sid: str) -> dict:
        srv = self.P.manager.server(sid)
        plan = self.plan_promotion(srv)
        if plan["possible"]:
            for e in plan["evict"]:
                job.log(f"Quota SSD : « {e['name']} » (démarré il y a le plus longtemps, arrêté) repart sur le HDD.")
                self.move(job, e["id"], "hdd")
            self.move(job, sid, "ssd")
        else:
            job.log("Démarrage depuis le HDD : " + plan["reason"])
        job.step("Démarrage du serveur", 99)
        self.P.manager.start(sid)
        return {"tier": self.tier(self.P.manager.server(sid)), "promoted": plan["possible"]}

    def needs_promotion(self, srv: dict) -> bool:
        return self.auto and self.tier(srv) == "hdd" and srv["status"] == "installed" and not self.P.manager.alive(srv["id"])

    def cleanup(self):
        for root in (self.ssd_dir, self.P.s.servers_dir):
            if root.is_dir():
                for d in root.glob("*.panel-copy"):
                    shutil.rmtree(d, ignore_errors=True)          # copie interrompue : l'original n'a jamais été touché
        if self.archives_dir.is_dir():
            for f in self.archives_dir.glob("*.part"):
                f.unlink(missing_ok=True)

    # ------------------------------------------------------------------ archives de mondes
    def _worlds(self, srv: dict) -> list[Path]:
        from .whitelist import read_props
        root = Path(srv["path"])
        level = read_props(root / "server.properties").get("level-name", "world") or "world"
        cands = [root / level, root / f"{level}_nether", root / f"{level}_the_end"]
        return [c for c in cands if c.is_dir() and not c.is_symlink() and c.parent == root]

    def archive(self, job, sid: str, remove_after: bool = False) -> dict:
        mgr = self.P.manager
        srv = mgr.server(sid)
        if mgr.alive(sid):
            raise UserError("Arrêtez le serveur avant d'archiver son monde.")
        worlds = self._worlds(srv)
        if not worlds:
            raise UserError("Aucun monde à archiver (le monde est créé au premier démarrage).")
        if not self.P.data_ready():
            raise UserError("Le disque de données (HDD) n'est pas monté.")
        self.archives_dir.mkdir(parents=True, exist_ok=True)
        size = sum(dir_size(w) for w in worlds)
        if shutil.disk_usage(self.archives_dir).free < size + GB:
            raise UserError("Espace insuffisant sur le HDD pour l'archive.")
        mgr.blocked[sid] = "archivage du monde"
        try:
            aid = new_id()
            name = f"{srv['name']}_{time.strftime('%Y%m%d-%H%M%S')}_{aid[:6]}.zip"
            dest, part = self.archives_dir / name, self.archives_dir / (name + ".part")
            root = Path(srv["path"])
            try:
                from .mcstats import server_stats
                stats = server_stats(self.P, srv)
            except Exception:  # noqa: BLE001
                stats = None
            meta = {"server_id": sid, "server_name": self.P.label(srv), "server_type": srv["server_type"], "mc_version": srv["mc_version"],
                    "loader_version": srv["loader_version"], "worlds": [w.name for w in worlds], "size": size, "archived_at": now(),
                    "stats": stats["summary"] if stats else None, "players": [{k: p[k] for k in ("name", "uuid", "play_time_s")} for p in (stats or {}).get("players", [])][:200]}
            files = [(Path(dp) / f) for w in worlds for dp, _, fs in os.walk(w) for f in fs]
            job.step("Archivage du monde", 5)
            try:
                with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED, compresslevel=3, allowZip64=True) as z:
                    z.writestr("panel-world.json", json.dumps(meta, ensure_ascii=False, indent=1))
                    for i, f in enumerate(files):
                        job.check_cancel()
                        if not f.is_symlink():
                            z.write(f, f.relative_to(root).as_posix())
                        if i % 25 == 0:
                            job.progress(5 + 70 * i / max(len(files), 1))
                job.step("Vérification de l'archive", 80)
                with zipfile.ZipFile(part) as z:
                    if z.testzip() is not None:
                        raise UserError("Archive corrompue : rien n'a été supprimé.")
                    listed = {i.filename: i.file_size for i in z.infolist()}
                for f in files:
                    if not f.is_symlink() and listed.get(f.relative_to(root).as_posix()) != f.stat().st_size:
                        raise UserError(f"Fichier manquant ou différent dans l'archive ({f.name}) : rien n'a été supprimé.")
                os.replace(part, dest)
            finally:
                part.unlink(missing_ok=True)
            sha = _sha(dest)
            meta.update(id=aid, file=name, zip_size=dest.stat().st_size, sha256=sha)
            dest.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
            job.log(f"Archive vérifiée : {name} ({dest.stat().st_size / GB:.2f} Go, SHA-256 {sha[:12]}…)")
            if remove_after:
                for w in worlds:
                    shutil.rmtree(w)
                self._size_cache.pop(sid, None)
                job.log("Monde retiré du serveur après vérification de l'archive. Un nouveau monde sera créé au prochain démarrage.")
            return {"archive": name, "removed": bool(remove_after)}
        finally:
            mgr.blocked.pop(sid, None)

    def list_archives(self, sid: str | None = None) -> list[dict]:
        out = []
        if self.archives_dir.is_dir():
            for j in sorted(self.archives_dir.glob("*.json"), reverse=True):
                try:
                    m = json.loads(j.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if sid and m.get("server_id") != sid:
                    continue
                m["present"] = (self.archives_dir / m.get("file", "")).is_file()
                out.append(m)
        return out

    def _archive_path(self, aid: str) -> tuple[Path, dict]:
        for m in self.list_archives():
            if m.get("id") == aid:
                p = self.archives_dir / m["file"]
                if not p.is_file() or p.is_symlink():
                    raise UserError("Fichier d'archive absent du disque.")
                return p, m
        raise UserError("Archive introuvable.")

    def verify_archive(self, aid: str) -> dict:
        p, m = self._archive_path(aid)
        ok_sha = _sha(p) == m.get("sha256")
        try:
            with zipfile.ZipFile(p) as z:
                bad = z.testzip()
        except (zipfile.BadZipFile, OSError) as e:
            bad = str(e) or "illisible"
        return {"ok": ok_sha and bad is None, "sha256_ok": ok_sha, "zip_ok": bad is None}

    def restore_archive(self, job, aid: str, sid: str) -> dict:
        mgr = self.P.manager
        srv = mgr.server(sid)
        if mgr.alive(sid):
            raise UserError("Arrêtez le serveur avant de restaurer un monde.")
        p, m = self._archive_path(aid)
        v = self.verify_archive(aid)
        if not v["ok"]:
            raise UserError("L'archive ne passe pas la vérification d'intégrité : restauration refusée.")
        root = Path(srv["path"])
        if shutil.disk_usage(root).free < int(m.get("size", 0)) * 1.1 + (SAFETY_FREE if self.tier(srv) == "ssd" else GB):
            raise UserError("Espace insuffisant pour restaurer ce monde.")
        mgr.blocked[sid] = "restauration du monde"
        try:
            tmp = root / ".panel-restore"
            shutil.rmtree(tmp, ignore_errors=True)
            job.step("Extraction dans un dossier temporaire", 10)
            from .security import safe_join
            with zipfile.ZipFile(p) as z:
                infos = [i for i in z.infolist() if i.filename != "panel-world.json"]
                for n, info in enumerate(infos):
                    job.check_cancel()
                    out = safe_join(tmp, info.filename)
                    if info.is_dir():
                        out.mkdir(parents=True, exist_ok=True)
                        continue
                    out.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(info) as src, open(out, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    if n % 25 == 0:
                        job.progress(10 + 70 * n / max(len(infos), 1))
            job.step("Mise en place", 85)
            stamp = int(time.time())
            for w in m.get("worlds", []):
                cur = root / w
                if cur.exists():
                    cur.rename(root / f"{w}.avant-restauration-{stamp}")
                    job.log(f"Monde actuel « {w} » conservé sous {w}.avant-restauration-{stamp}")
                if (tmp / w).exists():
                    (tmp / w).rename(cur)
            shutil.rmtree(tmp, ignore_errors=True)
            self._size_cache.pop(sid, None)
            return {"restored": m["file"], "worlds": m.get("worlds", [])}
        finally:
            mgr.blocked.pop(sid, None)
