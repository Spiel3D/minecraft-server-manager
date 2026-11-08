"""Registre des types de serveur (V3 §3).

Chaque type est décrit par un *adaptateur* : ses capacités (mods, plugins), la manière de l'installer
(``builtin`` = chargeur géré par sources.py/installer.py, ``download`` = jar téléchargé depuis une API officielle,
``import`` = jar fourni par l'utilisateur, vérifié avant usage) et les dossiers à surveiller.

Extensible sans reconstruire le panneau : tout fichier ``*.json`` placé dans le dossier ``server-types.d`` de la configuration
ajoute (ou remplace) un type de mode ``import``, par exemple :
    {"key": "monfork", "label": "Mon fork", "family": "plugins", "mods": false, "plugins": true, "needs_loader": false}
"""
from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass(frozen=True)
class Adapter:
    key: str
    label: str
    family: str                    # vanilla | loader | plugins | hybrid | custom
    mode: str                      # builtin | download | import
    mods: bool = False
    plugins: bool = False
    needs_loader: bool = False     # version de chargeur/build à choisir
    loader_label: str = "Version du chargeur"
    notes: str = ""
    marks: tuple = field(default_factory=tuple)     # indices de reconnaissance dans un jar (chemins internes)

    def public(self) -> dict:
        d = asdict(self)
        d.pop("marks", None)
        d["auto_install"] = self.mode in ("builtin", "download")
        return d


_BUILTIN = [
    Adapter("vanilla", "Vanilla", "vanilla", "builtin", marks=("net/minecraft/server/", "net/minecraft/bundler/")),
    Adapter("forge", "Forge", "loader", "builtin", mods=True, needs_loader=True, loader_label="Version de Forge", marks=("net/minecraftforge/",)),
    Adapter("neoforge", "NeoForge", "loader", "builtin", mods=True, needs_loader=True, loader_label="Version de NeoForge", marks=("net/neoforged/",)),
    Adapter("fabric", "Fabric", "loader", "builtin", mods=True, needs_loader=True, loader_label="Version du loader Fabric", marks=("net/fabricmc/",)),
    Adapter("quilt", "Quilt", "loader", "builtin", mods=True, needs_loader=True, loader_label="Version du loader Quilt", marks=("org/quiltmc/",)),
    Adapter("paper", "Paper", "plugins", "download", plugins=True, needs_loader=True, loader_label="Build Paper",
            notes="Plugins Bukkit/Spigot/Paper.", marks=("io/papermc/paper/", "com/destroystokyo/paper/")),
    Adapter("purpur", "Purpur", "plugins", "download", plugins=True, needs_loader=True, loader_label="Build Purpur",
            notes="Fork de Paper, plugins.", marks=("org/purpurmc/purpur/", "net/pl3x/purpur/")),
    Adapter("spigot", "Spigot", "plugins", "import", plugins=True, notes="Fournir le jar (BuildTools) : aucune distribution directe.", marks=("org/spigotmc/",)),
    Adapter("bukkit", "CraftBukkit / Bukkit", "plugins", "import", plugins=True, notes="Fournir le jar.", marks=("org/bukkit/craftbukkit/",)),
    Adapter("mohist", "Mohist", "hybrid", "import", mods=True, plugins=True, notes="Hybride mods + plugins : fournir le jar.", marks=("com/mohistmc/", "mohist")),
    Adapter("magma", "Magma", "hybrid", "import", mods=True, plugins=True, notes="Hybride mods + plugins : fournir le jar.", marks=("org/magmafoundation/", "magma")),
    Adapter("arclight", "Arclight", "hybrid", "import", mods=True, plugins=True, notes="Hybride mods + plugins : fournir le jar.", marks=("io/izzel/arclight/", "arclight")),
    Adapter("custom", "Jar personnalisé", "custom", "import", mods=True, plugins=True, notes="Tout autre serveur : le panneau lance « java -jar »."),
]

_KEY_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")


def load_adapters(extra_dir: Path | None = None) -> dict[str, Adapter]:
    reg = {a.key: a for a in _BUILTIN}
    if extra_dir and extra_dir.is_dir():
        for f in sorted(extra_dir.glob("*.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                key = str(d["key"]).lower()
                if not _KEY_RE.match(key) or (key in reg and reg[key].mode != "import"):
                    continue           # on ne remplace jamais un type géré automatiquement
                reg[key] = Adapter(key, str(d.get("label") or key)[:40], str(d.get("family") or "custom"), "import",
                                   mods=bool(d.get("mods")), plugins=bool(d.get("plugins")), needs_loader=False,
                                   notes=str(d.get("notes") or "")[:200])
            except (OSError, ValueError, KeyError, TypeError):
                continue                # fichier invalide : ignoré, jamais bloquant
    return reg


def detect_jar(path: Path) -> dict:
    """Examine un jar SANS l'exécuter : classe principale, version Minecraft, famille probable. Aucune confiance accordée."""
    out = {"valid": False, "main_class": None, "mc_version": None, "guess": None, "warnings": []}
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if len(names) > 200000:
                out["warnings"].append("Archive anormalement volumineuse.")
                return out
            out["valid"] = True
            if "META-INF/MANIFEST.MF" in names:
                mf = z.read("META-INF/MANIFEST.MF")[:20000].decode("utf-8", "replace")
                m = re.search(r"^Main-Class:\s*(\S+)", mf, re.M)
                out["main_class"] = m.group(1) if m else None
            if "version.json" in names:
                try:
                    v = json.loads(z.read("version.json")[:200000].decode("utf-8", "replace"))
                    out["mc_version"] = v.get("id") or v.get("name")
                except ValueError:
                    pass
            low = [n.lower() for n in names[:50000]]
            for a in _BUILTIN:
                if any(any(mk.lower() in n for n in low) for mk in a.marks if "/" in mk):
                    out["guess"] = a.key
                    break
            if not out["guess"]:
                base = path.name.lower()
                for a in _BUILTIN:
                    if any(mk in base for mk in a.marks if "/" not in mk):
                        out["guess"] = a.key
                        break
            if not out["mc_version"]:
                m = re.search(r"(1\.\d{1,2}(?:\.\d{1,2})?)", path.name)
                if m:
                    out["mc_version"] = m.group(1)
            if not out["main_class"]:
                out["warnings"].append("Aucune classe principale déclarée : ce jar n'est peut-être pas un serveur exécutable.")
    except (zipfile.BadZipFile, OSError):
        out["warnings"].append("Fichier illisible ou non-jar (archive ZIP invalide).")
    return out
