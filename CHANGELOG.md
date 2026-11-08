# Journal des changements

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/).

## 4.1.0 — 2026-11-08 — portabilité et publication
### Ajouté
- `install.sh --data-root CHEMIN` : dossier de données personnalisé (validé : chemin absolu, caractères simples, jamais un dossier système).
- `install.sh --single-disk` : machines à disque unique. Le dossier de données est créé s'il manque, le contrôle « point de montage » est désactivé
  et le niveau SSD est coupé (`MCPANEL_SSD_TIER=0` dans `panel.env`) : les mondes restent dans le dossier de données.
- Téléchargement automatique de Java pour `aarch64` (ARM) en plus de `x86_64`.
- `tests/installer_smoke.sh` : installation → mise à jour → désinstallation avec dossiers personnalisés, garde-fous, données intactes ; `tests/ui_single_disk.py` : interface en mode disque unique.
- Dépôt : licence, `SECURITE`, contribution, CI GitHub Actions, `docs/PORTABILITE.md`.
### Corrigé
- `update.sh` réécrivait les unités systemd avec les chemins par défaut : avec un dossier de données personnalisé, le service perdait l'accès en écriture.
  Les unités sont maintenant générées comme à l'installation (chemins, compte, préfixe).
- `install.sh` échouait à la dernière étape quand la commande `ip` (iproute2) est absente.
- `tools/validate.sh` signalait à tort des « échecs » avec des mondes sur le niveau SSD (V4) ; il accepte maintenant ce niveau et le mode disque unique.
- `uninstall.sh` citait toujours `/srv/minecraft-data` dans ses messages.
- « SSD système » devient « Disque système » dans l'interface (la machine peut n'avoir qu'un disque).

## 4.0.0
RAM 10 Go / 12 Go / maximum et prévisionnel, configuration complète à la création, port principal 25565 avec reprise automatique,
notifications et rapports de crash, supervision (TPS, latence, statistiques Minecraft par joueur), recherche de mods Modrinth + CurseForge
avec relations cliquables, éditeur de configuration de mods, mondes SSD/HDD avec quota et archivage vérifié, tableau de bord en deux catégories.
Détails : [`docs/V4.md`](docs/V4.md). Migration de base v4 (copie `panel.db.avant-v4`).

## 3.0.0-lot1
RAM par serveur calculée sur la RAM totale moins la réserve système, noms libres (accents, espaces, emojis), nouveaux types (Paper, Purpur, Spigot,
CraftBukkit, Mohist, Magma, Arclight, jar personnalisé), whitelist par serveur, ~60 paramètres Minecraft avec historique et retour arrière.
Détails : [`docs/V3-LOT1.md`](docs/V3-LOT1.md). Migration de base v3.

## 1.1.0
Interface refaite, comptes administrateur/utilisateur avec permissions vérifiées côté serveur, API REST `/api/v1` (tokens, OpenAPI, WebSocket),
webhooks Discord, supervision Debian en lecture seule, retour arrière (`tools/rollback.sh`). Migration de base v2.

## 1.0.0
Première version : création et gestion de serveurs Minecraft (Vanilla, Forge, NeoForge, Fabric, Quilt), console, fichiers, mods, modpacks, sauvegardes.
