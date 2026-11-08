# Portabilité : sur quelles machines ça marche

Réponse courte : **sur n'importe quelle machine Linux avec systemd et Python 3.11+, via `install.sh`**. Le projet n'est pas lié à
la machine de son auteur. Mais seul Debian 13 sert au quotidien ; le reste est soit testé en simulation, soit non testé. Voici l'état exact.

## Systèmes
| Environnement | État |
|---|---|
| **Debian 13** (x86-64), systemd, Python 3.11+ | Cible principale, utilisée par l'auteur. |
| **Ubuntu 24.04** (x86-64), Python 3.13 | `install.sh` → mise à jour → désinstallation testés dans un conteneur **sans systemd** (systemd simulé : le service est vraiment lancé sous l'utilisateur dédié à partir de l'unité générée). Suite `pytest` complète verte. systemd réel non testé. |
| Autres Debian / Ubuntu récents avec systemd | Devrait fonctionner : l'installateur avertit et demande confirmation (`--yes` pour passer). Non testé. |
| Autres distributions Linux (Fedora, Arch…) | Peut fonctionner si `python3-venv`, `useradd`, `runuser` et systemd sont présents. Non testé ; l'installateur n'installe `python3-venv` que via `apt`. |
| Raspberry Pi / serveurs ARM (`aarch64`) | Le téléchargement automatique de Java gère `aarch64` (test unitaire de l'URL, **jamais exécuté sur une vraie machine ARM**). Sinon, installe Java avec `apt` : le panneau détecte les Java de `/usr/lib/jvm`. |
| 32 bits (`armv7l`, `i386`) | Pas de téléchargement automatique de Java : installe-le avec le gestionnaire de paquets. |
| **Windows, macOS** | **Non pris en charge** (systemd, `/proc`, utilisateurs Unix, `runuser`). WSL2 avec systemd activé : non testé. |
| **Docker** | Pas d'image fournie (le panneau pilote des processus Java et lit `/proc`, `/sys`, `systemctl` : ce n'est pas un bon candidat pour un conteneur sans adaptation). |

Autres prérequis : accès Internet (Java, versions de Minecraft, mods), 4 Go de RAM au minimum conseillés pour un serveur (la RAM totale
détermine le maximum proposé), environ 10 Go libres pour commencer.

## Disques : deux modes
### Disque de données dédié (défaut, recommandé)
Le dossier de données (par défaut `/srv/minecraft-data`) doit être **un point de montage** : l'installateur refuse sinon, pour qu'un disque
absent ne remplisse jamais le disque système sans prévenir. Le niveau « SSD » (mondes actifs copiés sur le disque rapide avec quota) est actif.

### Disque unique : `--single-disk`
```bash
sudo ./install.sh --single-disk                              # données dans /srv/minecraft-data (créé s'il manque)
sudo ./install.sh --single-disk --data-root /home/mc/data    # ou ailleurs
```
- Le dossier de données est créé s'il n'existe pas ; le contrôle « point de montage » est désactivé (`MCPANEL_REQUIRE_MOUNT=0`).
- Le niveau SSD est coupé (`MCPANEL_SSD_TIER=0`) : les mondes restent dans `<dossier de données>/servers`. L'archivage de monde reste disponible.
- Rien n'est jamais formaté ni partitionné ; un dossier système (`/`, `/usr`, `/etc`, `/home`, `/var`…) est refusé comme dossier de données.
- Cela se règle aussi après coup dans `/etc/minecraft-panel/panel.env` (puis `sudo systemctl restart minecraft-panel`).

## Chemins personnalisés
`MCPANEL_PREFIX`, `MCPANEL_STATE_DIR`, `MCPANEL_CONF_DIR`, `MCPANEL_USER` et `--data-root` peuvent être changés à l'installation. Les unités
systemd sont générées avec ces valeurs, **à l'installation comme à chaque `update.sh`** (testé avec des chemins personnalisés). Les chemins doivent être absolus
et ne contenir que lettres, chiffres, `_ . - /`.

## Ce qui est testé automatiquement (et ce qui ne l'est pas)
- `python -m pytest` : plus de 200 tests, avec faux Java, faux serveur Minecraft et réseau simulé. Aucun accès à Internet.
- `tests/ui_smoke.py`, `tests/ui_single_disk.py` : interface dans un vrai Chromium (mode normal et mode disque unique).
- `tests/installer_smoke.sh` : installation, mise à jour et désinstallation avec dossiers personnalisés, garde-fous, données existantes intactes.
- **Pas testé automatiquement** : systemd réel, démarrage au boot, blocage de veille, vrais serveurs Minecraft (Vanilla/Forge/Fabric…), vraies API
  (Mojang, Paper, Modrinth, CurseForge, Adoptium), déplacement de vrais mondes volumineux, ARM. La liste complète à valider sur ta machine :
  [`TESTS-A-FAIRE-SUR-LA-MACHINE.md`](TESTS-A-FAIRE-SUR-LA-MACHINE.md).

Si tu installes sur une autre machine, ouvre une issue avec le résultat (système, ce qui a marché ou non) : c'est ainsi que ce tableau s'améliore.
