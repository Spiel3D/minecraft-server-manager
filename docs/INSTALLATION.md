# Installation

Système cible : **Debian 13** (voir [`PORTABILITE.md`](PORTABILITE.md) pour Ubuntu, ARM, etc. et ce qui est testé).

## Avant de commencer
- Un système Linux avec **systemd** et **Python 3.11+** (Debian 13 en a un ; sinon `python3 --version`).
- **Un disque de données dédié monté sur `/srv/minecraft-data`** (le script refuse sinon : jamais de données Minecraft sur le disque système sans que tu l'aies demandé).
  Une seule machine, un seul disque ? Ajoute `--single-disk` (voir plus bas).
- Connexion Internet (téléchargement des dépendances Python, de Java et des versions de Minecraft).
- Réservez une **adresse IP fixe** à la machine dans votre box ou routeur (bail DHCP) pour que l'adresse du panneau ne change pas.

## Installer
```bash
cd minecraft-panel          # dossier du projet décompressé
sudo ./install.sh           # options : --port 8080  --admin NOM  --no-awake  --yes  --data-root CHEMIN  --single-disk
```
- `--data-root CHEMIN` : autre dossier de données que `/srv/minecraft-data` (chemin absolu, simple, jamais un dossier système).
- `--single-disk` : machine **à disque unique**. Le dossier de données est créé s'il manque, le contrôle « point de montage » est désactivé et le niveau SSD est coupé
  (`MCPANEL_SSD_TIER=0`) : les mondes restent dans le dossier de données. Rien n'est formaté ni partitionné.
- Installation non interactive : `MCPANEL_ADMIN_PASSWORD_FILE=/chemin/fichier` (12 caractères minimum).
Le script : vérifie Debian et le disque, crée l'utilisateur système `minecraft-panel` (sans connexion), crée seulement
`servers/ backups/ uploads/ temporary/` dans `/srv/minecraft-data` (le dossier existant `Transfert` n'est pas touché),
installe `python3-venv` si besoin, installe le code dans `/opt/minecraft-panel`, initialise la base SQLite dans
`/var/lib/minecraft-panel`, vous demande le mot de passe administrateur (12 caractères minimum), installe et démarre le
service systemd puis vérifie qu'il répond et qu'il ne tourne pas en root. Il affiche ensuite l'adresse d'accès.

Il **ne formate, ne partitionne et ne supprime jamais rien**, et ne modifie ni KDE, ni le réseau, ni le pare-feu, ni le Wi-Fi/Bluetooth.
Si une installation existe, les réglages (`/etc/minecraft-panel/panel.env`) et le compte administrateur sont conservés.

## Mettre à jour
Décompressez la nouvelle version dans un dossier puis : `sudo ./update.sh`
(sauvegarde de la configuration, remplacement du code, migration de la base, redémarrage du panneau, retour arrière
automatique en cas d'échec). **Les serveurs Minecraft en marche ne sont pas interrompus.**
Depuis la 1.0.0 : la base est migrée en version 2 (comptes, tokens, notifications) avec une copie `panel.db.avant-v2` dans `/var/lib/minecraft-panel/`.

## Revenir en arrière
`sudo /opt/minecraft-panel/app/tools/rollback.sh` remet la version précédente (code et base). Option `--keep-db` : garde la base actuelle (comptes non administrateurs neutralisés). Aucun monde Minecraft n'est touché.

## Sauvegarder la configuration du panneau
`sudo -u minecraft-panel /opt/minecraft-panel/app/backup-panel.sh` — une minuterie le fait aussi chaque nuit.
Les archives (base + réglages, 14 conservées) vont dans `/srv/minecraft-data/backups/_panel/`.

## Désinstaller
`sudo ./uninstall.sh` retire le service et le code mais **conserve** serveurs, mondes, sauvegardes, base et Java.
Options : `--stop-servers` (arrêt propre des serveurs actifs d'abord), `--purge-state` (supprime base et Java),
`--remove-user`. Rien dans `/srv/minecraft-data` n'est jamais supprimé par ce script.

## Réglages (`/etc/minecraft-panel/panel.env`)
| Variable | Rôle |
|---|---|
| `MCPANEL_PORT` | port du panneau (8080) |
| `MCPANEL_ALLOWED_NETS` | réseaux autorisés (par défaut : réseaux privés uniquement) |
| `MCPANEL_RAM_RESERVE_MB` | RAM jamais attribuée aux serveurs (2048) |
| `MCPANEL_MAX_UPLOAD_MB` | taille maximale d'un envoi (4096) |
| `MCPANEL_DATA_ROOT` | dossier des serveurs, sauvegardes, envois (`/srv/minecraft-data`) |
| `MCPANEL_REQUIRE_MOUNT` | `0` = le dossier de données n'a pas besoin d'être un point de montage (mode disque unique) |
| `MCPANEL_SSD_TIER` | `0` = pas de niveau SSD (mondes toujours dans le dossier de données) |

Après modification : `sudo systemctl restart minecraft-panel` (les serveurs Minecraft continuent de tourner).

## Dépannage
- Journal : `journalctl -u minecraft-panel -e`
- État : `systemctl status minecraft-panel`
- Contrôles : `sudo /opt/minecraft-panel/app/tools/validate.sh`
- Mot de passe oublié : `cd /opt/minecraft-panel/app && sudo -u minecraft-panel env MCPANEL_STATE_DIR=/var/lib/minecraft-panel /opt/minecraft-panel/venv/bin/python -m mcpanel create-admin --reset` (à lancer depuis `/opt/minecraft-panel/app`).
