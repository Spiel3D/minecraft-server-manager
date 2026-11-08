"""Accès aux sources officielles (HTTPS uniquement) : versions, installateurs, runtimes Java.

Rien n'est codé en dur côté versions : tout vient des métadonnées officielles.
"""
from __future__ import annotations

import hashlib
import json
import platform
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable

UA = "minecraft-panel/1.2 (private LAN panel; contact: local administrator)"
LTS_JAVA = (8, 11, 17, 21, 25)


class SourceError(Exception):
    """Source indisponible, version inconnue ou téléchargement invalide."""


class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://"):
            raise urllib.error.URLError("redirection non HTTPS refusée")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Http:
    def __init__(self, timeout: int = 25, retries: int = 3, cache_ttl: int = 600):
        self.timeout, self.retries, self.cache_ttl = timeout, retries, cache_ttl
        self._cache: dict[str, tuple[float, object]] = {}
        self._opener = urllib.request.build_opener(_HttpsOnlyRedirect)

    def _open(self, url: str, headers: dict | None = None, data: bytes | None = None):
        if not url.lower().startswith("https://"):
            raise SourceError(f"URL non HTTPS refusée : {url}")
        req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                return self._opener.open(req, timeout=self.timeout)
            except urllib.error.HTTPError as e:
                if e.code in (400, 401, 403, 404):
                    raise SourceError(f"HTTP {e.code} pour {url}") from e
                last = e
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                last = e
            if attempt < self.retries - 1:
                time.sleep(min(2 ** attempt, 4))
        raise SourceError(f"Échec réseau pour {url} : {last}")

    def get_bytes(self, url: str, headers: dict | None = None) -> bytes:
        with self._open(url, headers) as r:
            return r.read()

    def get_text(self, url: str, headers: dict | None = None, cache: bool = True) -> str:
        key = "t:" + url
        hit = self._cache.get(key)
        if cache and hit and time.time() - hit[0] < self.cache_ttl:
            return hit[1]  # type: ignore[return-value]
        text = self.get_bytes(url, headers).decode("utf-8", "replace")
        self._cache[key] = (time.time(), text)
        return text

    def get_json(self, url: str, headers: dict | None = None, cache: bool = True):
        try:
            return json.loads(self.get_text(url, headers, cache))
        except ValueError as e:
            raise SourceError(f"Réponse JSON invalide pour {url}") from e

    def post_json(self, url: str, payload: dict, headers: dict | None = None):
        h = {"Content-Type": "application/json", "Accept": "application/json", **(headers or {})}
        with self._open(url, h, json.dumps(payload).encode()) as r:
            try:
                return json.loads(r.read().decode("utf-8", "replace"))
            except ValueError as e:
                raise SourceError(f"Réponse JSON invalide pour {url}") from e

    def download(self, url: str, dest: Path, *, expected: dict | None = None,
                 progress: Callable[[int, int], None] | None = None,
                 max_bytes: int | None = None, allowed_hosts: set | None = None) -> Path:
        """Télécharge vers dest (.part puis renommage) et vérifie les empreintes (sha1/sha256/sha512)."""
        host = urllib.parse.urlparse(url).hostname or ""
        if allowed_hosts is not None and host not in allowed_hosts:
            raise SourceError(f"Hôte de téléchargement non autorisé : {host}")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        algos = {a: hashlib.new(a) for a in (expected or {}) if a in ("sha1", "sha256", "sha512", "md5")}
        try:
            with self._open(url) as r, open(part, "wb") as f:
                total = int(r.headers.get("Content-Length") or 0)
                if max_bytes and total > max_bytes:
                    raise SourceError("Fichier trop volumineux.")
                done = 0
                while True:
                    chunk = r.read(1024 * 1024)
                    if not chunk:
                        break
                    done += len(chunk)
                    if max_bytes and done > max_bytes:
                        raise SourceError("Fichier trop volumineux.")
                    f.write(chunk)
                    for h in algos.values():
                        h.update(chunk)
                    if progress:
                        progress(done, total)
            for a, h in algos.items():
                want = (expected or {})[a].lower()
                if h.hexdigest() != want:
                    raise SourceError(f"Somme de contrôle {a} invalide pour {dest.name} (téléchargement corrompu ou altéré).")
            part.replace(dest)
            return dest
        except BaseException:
            part.unlink(missing_ok=True)
            raise


MOJANG_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"


class Mojang:
    def __init__(self, http: Http):
        self.http = http

    def manifest(self) -> dict:
        return self.http.get_json(MOJANG_MANIFEST)

    def versions(self, include_snapshots: bool = False) -> list[dict]:
        out = []
        for v in self.manifest().get("versions", []):
            t = v.get("type")
            if t == "release" or (include_snapshots and t == "snapshot"):
                out.append({"id": v["id"], "type": t, "stable": t == "release", "releaseTime": v.get("releaseTime")})
        return out

    def exists(self, vid: str) -> bool:
        return any(v["id"] == vid for v in self.manifest().get("versions", []))

    def version_info(self, vid: str) -> dict:
        entry = next((v for v in self.manifest().get("versions", []) if v["id"] == vid), None)
        if not entry:
            raise SourceError(f"Version Minecraft inconnue : {vid}")
        meta = self.http.get_json(entry["url"])
        srv = (meta.get("downloads") or {}).get("server")
        if not srv:
            raise SourceError(f"Mojang ne publie pas de serveur pour la version {vid}.")
        return {
            "id": vid, "type": entry.get("type"), "server_url": srv["url"], "sha1": srv.get("sha1"),
            "size": srv.get("size"), "java_major": int((meta.get("javaVersion") or {}).get("majorVersion", 8)),
        }


FORGE_META = "https://maven.minecraftforge.net/net/minecraftforge/forge/maven-metadata.xml"
FORGE_PROMOS = "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"
FORGE_MAVEN = "https://maven.minecraftforge.net/net/minecraftforge/forge"


def maven_versions(xml_text: str) -> list[str]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise SourceError("Métadonnées Maven illisibles.") from e
    return [v.text.strip() for v in root.iter("version") if v.text and v.text.strip()]


def _vkey(s: str):
    return [int(x) if x.isdigit() else 0 for x in re.split(r"[.\-_+]", s)]


class Forge:
    def __init__(self, http: Http):
        self.http = http

    def _all(self) -> list[str]:
        return maven_versions(self.http.get_text(FORGE_META))

    def mc_versions(self) -> set[str]:
        return {f.split("-", 1)[0] for f in self._all() if "-" in f}

    def versions(self, mc: str) -> list[dict]:
        promos = {}
        try:
            promos = (self.http.get_json(FORGE_PROMOS) or {}).get("promos", {})
        except SourceError:
            pass
        rec, lat = promos.get(f"{mc}-recommended"), promos.get(f"{mc}-latest")
        out = []
        for full in self._all():
            if "-" not in full or full.split("-", 1)[0] != mc:
                continue
            rest = full.split("-", 1)[1]
            out.append({"version": rest, "full": full, "recommended": bool(rec) and rest.split("-")[0] == rec,
                        "latest": bool(lat) and rest.split("-")[0] == lat})
        out.sort(key=lambda d: _vkey(d["version"]), reverse=True)
        return out

    def installer(self, full: str) -> dict:
        base = f"{FORGE_MAVEN}/{full}/forge-{full}-installer.jar"
        return {"url": base, "sha1_url": base + ".sha1"}


NEO_BASE = "https://maven.neoforged.net/releases/net/neoforged"
NEO_META = f"{NEO_BASE}/neoforge/maven-metadata.xml"
NEO_LEGACY_META = f"{NEO_BASE}/forge/maven-metadata.xml"  # 1.20.1 uniquement


def neo_prefix(mc: str) -> str | None:
    m = re.fullmatch(r"1\.(\d+)(?:\.(\d+))?", mc)
    if m:
        return f"{m.group(1)}.{m.group(2) or 0}."
    if re.fullmatch(r"\d+\.\d+(\.\d+)?", mc):
        return mc + "."
    return None


class NeoForge:
    def __init__(self, http: Http):
        self.http = http

    def versions(self, mc: str) -> list[dict]:
        out = []
        if mc == "1.20.1":
            try:
                for full in maven_versions(self.http.get_text(NEO_LEGACY_META)):
                    if full.startswith("1.20.1-"):
                        out.append({"version": full, "full": full, "artifact": "forge", "beta": "beta" in full})
            except SourceError:
                pass
        else:
            prefix = neo_prefix(mc)
            if prefix:
                for v in maven_versions(self.http.get_text(NEO_META)):
                    if v.startswith(prefix):
                        out.append({"version": v, "full": v, "artifact": "neoforge", "beta": "beta" in v or "alpha" in v})
        out.sort(key=lambda d: _vkey(d["version"]), reverse=True)
        return out

    def supported(self, mc_ids: list[str]) -> set[str]:
        allv = maven_versions(self.http.get_text(NEO_META))
        ok = {m for m in mc_ids if (p := neo_prefix(m)) and m != "1.20.1" and any(v.startswith(p) for v in allv)}
        if "1.20.1" in mc_ids:
            try:
                if any(f.startswith("1.20.1-") for f in maven_versions(self.http.get_text(NEO_LEGACY_META))):
                    ok.add("1.20.1")
            except SourceError:
                pass
        return ok

    def installer(self, artifact: str, full: str) -> dict:
        base = f"{NEO_BASE}/{artifact}/{full}/{artifact}-{full}-installer.jar"
        return {"url": base, "sha1_url": base + ".sha1"}


class FabricLike:
    def __init__(self, http: Http, base: str):
        self.http, self.base = http, base

    def games(self) -> list[dict]:
        return self.http.get_json(f"{self.base}/game")

    def loaders(self, mc: str) -> list[dict]:
        data = self.http.get_json(f"{self.base}/loader/{urllib.parse.quote(mc, safe='')}")
        out = []
        for e in data or []:
            l = e.get("loader") or {}
            if l.get("version"):
                out.append({"version": l["version"], "stable": bool(l.get("stable", True)), "full": l["version"]})
        return out

    def installer(self) -> dict:
        lst = self.http.get_json(f"{self.base}/installer")
        pick = next((i for i in lst if i.get("stable", True)), None) or (lst[0] if lst else None)
        if not pick or not pick.get("url"):
            raise SourceError("Aucun installateur disponible.")
        return {"version": pick["version"], "url": pick["url"]}


ADOPTIUM = "https://api.adoptium.net/v3"


def choose_java_major(required: int, lts=LTS_JAVA) -> int:
    """Java 8 reste 8 (les modpacks anciens cassent dès Java 9) ; sinon le plus petit LTS >= requis."""
    if required <= 8:
        return 8
    for m in sorted(lts):
        if m >= required:
            return m
    return max(lts)


ADOPTIUM_ARCH = {"x86_64": "x64", "amd64": "x64", "aarch64": "aarch64", "arm64": "aarch64"}


def adoptium_arch(machine: str | None = None) -> str | None:
    """Architecture Adoptium correspondant à la machine (None si le téléchargement automatique n'est pas possible)."""
    return ADOPTIUM_ARCH.get((machine or platform.machine()).lower())


class Adoptium:
    def __init__(self, http: Http, machine: str | None = None):
        self.http = http
        self.machine = machine or platform.machine()

    def asset(self, major: int) -> dict:
        arch = adoptium_arch(self.machine)
        if not arch:
            raise SourceError(f"Téléchargement automatique de Java impossible pour l'architecture « {self.machine} » (x86_64 et aarch64 seulement). "
                              f"Installez Java avec le gestionnaire de paquets du système (ex. openjdk-{major}-jre-headless) : le panneau détecte les Java installés dans /usr/lib/jvm.")
        url = (f"{ADOPTIUM}/assets/latest/{major}/hotspot?architecture={arch}&image_type=jre&os=linux&vendor=eclipse")
        data = self.http.get_json(url, cache=False)
        for a in data or []:
            pkg = (a.get("binary") or {}).get("package") or {}
            if pkg.get("link") and pkg.get("checksum") and str(pkg.get("name", "")).endswith(".tar.gz"):
                return {"url": pkg["link"], "sha256": pkg["checksum"], "size": pkg.get("size"),
                        "name": pkg["name"], "release": a.get("release_name", "")}
        raise SourceError(f"Aucun runtime Java {major} (Linux {arch}) publié par Adoptium.")


CF_API = "https://api.curseforge.com/v1"


class CurseForge:
    def __init__(self, http: Http, key: str):
        self.http, self.key = http, key

    def files(self, ids: list[int]) -> list[dict]:
        out = []
        for i in range(0, len(ids), 200):
            data = self.http.post_json(f"{CF_API}/mods/files", {"fileIds": ids[i:i + 200]}, {"x-api-key": self.key})
            out.extend(data.get("data", []))
        return out


from .adapters import load_adapters  # noqa: E402

SERVER_TYPES = ("vanilla", "forge", "neoforge", "fabric", "quilt", "paper", "purpur", "spigot", "bukkit", "mohist", "magma", "arclight", "custom")
PAPER_API = "https://fill.papermc.io/v3/projects/paper"
PURPUR_API = "https://api.purpurmc.org/v2/purpur"


class PaperFill:
    """Paper via le service officiel « Fill » v3 (à valider chez vous : le réseau est simulé dans les tests)."""

    def __init__(self, http: Http):
        self.http = http

    def mc_versions(self) -> set[str]:
        d = self.http.get_json(PAPER_API)
        vs = d.get("versions", {})
        flat = [v for grp in vs.values() for v in grp] if isinstance(vs, dict) else list(vs)
        return {str(v) for v in flat}

    def builds(self, mc: str) -> list[dict]:
        data = self.http.get_json(f"{PAPER_API}/versions/{mc}/builds")
        if isinstance(data, dict):
            data = data.get("builds", [])
        out = []
        for b in data:
            dl = (b.get("downloads") or {}).get("server:default")
            if not dl or not dl.get("url"):
                continue
            out.append({"version": str(b["id"]), "channel": str(b.get("channel", "STABLE")).upper(), "url": dl["url"],
                        "sha256": (dl.get("checksums") or {}).get("sha256"), "name": dl.get("name") or f"paper-{mc}-{b['id']}.jar"})
        out.sort(key=lambda x: int(x["version"]), reverse=True)
        stable = [b for b in out if b["channel"] == "STABLE"]
        for i, b in enumerate(out):
            b["stable"] = b["channel"] == "STABLE"
            b["recommended"] = bool(stable) and b is stable[0]
            b["latest"] = i == 0
        return out


class Purpur:
    """Purpur via son API publique v2 (à valider chez vous)."""

    def __init__(self, http: Http):
        self.http = http

    def mc_versions(self) -> set[str]:
        return {str(v) for v in self.http.get_json(PURPUR_API).get("versions", [])}

    def builds(self, mc: str) -> list[dict]:
        d = self.http.get_json(f"{PURPUR_API}/{mc}")
        b = d.get("builds", {})
        allb = b.get("all", []) if isinstance(b, dict) else list(b)
        latest = str(b.get("latest", "")) if isinstance(b, dict) else ""
        out = []
        for n in sorted((str(x) for x in allb), key=int, reverse=True)[:60]:
            out.append({"version": n, "url": f"{PURPUR_API}/{mc}/{n}/download", "name": f"purpur-{mc}-{n}.jar", "stable": True,
                        "recommended": n == latest or (not latest and n == str(max(map(int, allb)))), "latest": n == latest})
        return out

    def md5(self, mc: str, build: str) -> str | None:
        try:
            return self.http.get_json(f"{PURPUR_API}/{mc}/{build}", cache=False).get("md5")
        except SourceError:
            return None


class Sources:
    def __init__(self, http: Http | None = None):
        self.http = http or Http()
        self.mojang = Mojang(self.http)
        self.forge = Forge(self.http)
        self.neoforge = NeoForge(self.http)
        self.fabric = FabricLike(self.http, "https://meta.fabricmc.net/v2/versions")
        self.quilt = FabricLike(self.http, "https://meta.quiltmc.org/v3/versions")
        self.adoptium = Adoptium(self.http)
        self.paper = PaperFill(self.http)
        self.purpur = Purpur(self.http)
        self.adapters = load_adapters()

    def loader_versions(self, server_type: str, mc: str) -> list[dict]:
        ad = self.adapters.get(server_type)
        if server_type == "vanilla" or (ad and ad.mode == "import"):
            return []
        if not self.mojang.exists(mc):
            raise SourceError(f"Version Minecraft inconnue : {mc}")
        if server_type == "forge":
            return self.forge.versions(mc)
        if server_type == "neoforge":
            return self.neoforge.versions(mc)
        if server_type == "fabric":
            return self.fabric.loaders(mc)
        if server_type == "quilt":
            return self.quilt.loaders(mc)
        if server_type == "paper":
            return self.paper.builds(mc)
        if server_type == "purpur":
            return self.purpur.builds(mc)
        raise SourceError(f"Type de serveur inconnu : {server_type}")

    def supported_mc(self, server_type: str, snapshots: bool = False) -> list[dict]:
        """Versions Minecraft réellement compatibles avec le type choisi (intersection avec Mojang)."""
        base = self.mojang.versions(snapshots)
        ad = self.adapters.get(server_type)
        if server_type == "vanilla" or (ad and ad.mode == "import"):
            return base          # jar fourni par l'utilisateur : toute version Minecraft officielle
        if server_type == "forge":
            ok = self.forge.mc_versions()
        elif server_type == "neoforge":
            ok = self.neoforge.supported([v["id"] for v in base])
        elif server_type == "paper":
            ok = self.paper.mc_versions()
        elif server_type == "purpur":
            ok = self.purpur.mc_versions()
        elif server_type in ("fabric", "quilt"):
            games = (self.fabric if server_type == "fabric" else self.quilt).games()
            ok = {g["version"] for g in games}
        else:
            raise SourceError(f"Type de serveur inconnu : {server_type}")
        return [v for v in base if v["id"] in ok]

    def resolve(self, server_type: str, mc: str, loader: str | None) -> dict:
        """Valide la combinaison et renvoie tout ce qu'il faut pour installer. Ne devine jamais."""
        if server_type not in self.adapters:
            raise SourceError("Type de serveur inconnu.")
        info = self.mojang.version_info(mc)
        res = {"mc": mc, "type": server_type, "java_major": info["java_major"], "vanilla": info,
               "loader": None, "loader_version": None}
        if server_type == "vanilla" or self.adapters[server_type].mode == "import":
            return res
        if not loader:
            raise SourceError("Choisissez une version du chargeur de mods.")
        versions = self.loader_versions(server_type, mc)
        match = next((v for v in versions if v["version"] == loader), None)
        if not match:
            raise SourceError(f"{server_type} {loader} n'est pas disponible (ou pas compatible) pour Minecraft {mc}.")
        res["loader"], res["loader_version"] = match, match["version"]
        return res
