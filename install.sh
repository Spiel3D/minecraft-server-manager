#!/usr/bin/env bash
# Installation de Minecraft Server Manager (testé sur Debian 13 ; Debian/Ubuntu récents avec systemd et Python >= 3.11 en principe).
# À lancer avec : sudo ./install.sh [--port 8080] [--no-awake] [--admin NOM] [--yes] [--data-root CHEMIN] [--single-disk]
#   --data-root CHEMIN : dossier des serveurs/sauvegardes (défaut /srv/minecraft-data)
#   --single-disk      : machine à disque unique (sans disque de données dédié) : le dossier est créé s'il manque,
#                        la vérification « point de montage » est désactivée et le niveau SSD est coupé
#
# Garanties : ne formate ni ne partitionne jamais un disque ; ne supprime ni n'écrase rien dans /srv/minecraft-data ;
# ne touche ni à KDE, ni au réseau, ni au pare-feu, ni au Wi-Fi/Bluetooth ; n'exécute jamais le panneau ni les serveurs en root.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_NAME="${MCPANEL_USER:-minecraft-panel}"
PREFIX="${MCPANEL_PREFIX:-/opt/minecraft-panel}"
APP_DIR="$PREFIX/app"; VENV="$PREFIX/venv"
STATE_DIR="${MCPANEL_STATE_DIR:-/var/lib/minecraft-panel}"
CONF_DIR="${MCPANEL_CONF_DIR:-/etc/minecraft-panel}"
DATA_ROOT="${MCPANEL_DATA_ROOT:-/srv/minecraft-data}"
UNIT_DIR="${MCPANEL_UNIT_DIR:-/etc/systemd/system}"
PORT=8080; AWAKE=1; ADMIN="admin"; ASSUME_YES=0
SKIP_SYSTEMD="${MCPANEL_SKIP_SYSTEMD:-0}"           # réservé aux tests hors Debian
ALLOW_UNMOUNTED="${MCPANEL_ALLOW_UNMOUNTED:-0}"     # ancien nom de --single-disk (conservé pour les tests)

c_ok=$'\e[32m'; c_ko=$'\e[31m'; c_wa=$'\e[33m'; c_0=$'\e[0m'
ok()   { echo "  ${c_ok}[OK]${c_0} $*"; }
warn() { echo "  ${c_wa}[ATTENTION]${c_0} $*"; }
die()  { echo "  ${c_ko}[ERREUR]${c_0} $*" >&2; exit 1; }
step() { echo; echo "==> $*"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="${2:?port manquant}"; shift 2;;
    --admin) ADMIN="${2:?nom manquant}"; shift 2;;
    --no-awake) AWAKE=0; shift;;
    --data-root) DATA_ROOT="${2:?chemin manquant}"; shift 2;;
    --single-disk) ALLOW_UNMOUNTED=1; shift;;
    --yes|-y) ASSUME_YES=1; shift;;
    -h|--help) sed -n '2,10p' "$0"; exit 0;;
    *) die "Option inconnue : $1";;
  esac
done
[[ "$PORT" =~ ^[0-9]+$ ]] && [ "$PORT" -ge 1024 ] && [ "$PORT" -le 65535 ] || die "Port invalide (1024-65535)."
[[ "$ADMIN" =~ ^[A-Za-z0-9_.-]{2,32}$ ]] || die "Nom d'administrateur invalide."

[ "$(id -u)" -eq 0 ] || die "Lancez ce script avec sudo : sudo ./install.sh"
# Chemins : absolus, sans caractère spécial (ils sont recopiés dans les unités systemd), et jamais un dossier système.
for v in DATA_ROOT PREFIX STATE_DIR CONF_DIR; do
  val="${!v}"
  [[ "$val" =~ ^/[A-Za-z0-9_./-]+$ ]] && [[ "$val" != *..* ]] || die "$v « $val » invalide : chemin absolu, lettres, chiffres, _ . - / uniquement."
done
case "${DATA_ROOT%/}" in ""|/bin|/boot|/dev|/etc|/home|/lib|/lib64|/opt|/proc|/root|/run|/sbin|/sys|/tmp|/usr|/var|/var/lib|/srv) die "DATA_ROOT « $DATA_ROOT » est un dossier système : choisissez un sous-dossier dédié (ex. /srv/minecraft-data).";; esac

step "1/13 Vérification de Debian et des prérequis"
if [ -r /etc/os-release ]; then . /etc/os-release; fi
if [ "${ID:-}" = "debian" ] && [ "${VERSION_ID:-}" = "13" ]; then ok "Debian 13 (${VERSION_CODENAME:-trixie})"
else
  warn "Système détecté : ${PRETTY_NAME:-inconnu} (Debian 13 attendu)."
  [ "$ASSUME_YES" -eq 1 ] || { read -r -p "  Continuer quand même ? [o/N] " a; [[ "$a" =~ ^[oOyY]$ ]] || die "Installation annulée."; }
fi
command -v python3 >/dev/null || die "python3 est introuvable."
python3 - <<'PY' || die "Python 3.11 minimum requis."
import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)
PY
ok "Python $(python3 -c 'import platform;print(platform.python_version())')"
if [ "$SKIP_SYSTEMD" != "1" ]; then command -v systemctl >/dev/null || die "systemd est requis."; ok "systemd présent"; fi
[ -f "$SRC/mcpanel/app.py" ] && [ -f "$SRC/frontend/index.html" ] || die "Lancez install.sh depuis le dossier du projet complet."
if ss -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "[:.]${PORT}$"; then
  if systemctl is-active --quiet minecraft-panel 2>/dev/null; then ok "Le port $PORT est celui du panneau déjà installé (mise à jour sur place : préférez update.sh)."
  else die "Le port $PORT est déjà utilisé : choisissez-en un autre avec --port."; fi
fi

step "2/13 Vérification du dossier de données $DATA_ROOT"
if [ "$ALLOW_UNMOUNTED" = "1" ] && [ ! -d "$DATA_ROOT" ]; then
  install -d -m 0755 "$DATA_ROOT"; ok "dossier créé : $DATA_ROOT (mode disque unique)"
fi
[ -d "$DATA_ROOT" ] || die "$DATA_ROOT n'existe pas. Montez le disque de données avant d'installer (le script ne crée ni ne formate aucun disque), ou utilisez --single-disk si la machine n'a qu'un disque."
if mountpoint -q "$DATA_ROOT"; then
  fstype="$(findmnt -no FSTYPE "$DATA_ROOT" || true)"; src="$(findmnt -no SOURCE "$DATA_ROOT" || true)"
  ok "$DATA_ROOT est un point de montage dédié ($src, $fstype)"
  case "$fstype" in ext4|xfs|btrfs) ;; *) warn "Système de fichiers inhabituel : $fstype";; esac
  avail_gb=$(df -BG --output=avail "$DATA_ROOT" | tail -1 | tr -dc 0-9)
  [ "${avail_gb:-0}" -ge 10 ] || die "Moins de 10 Go libres sur $DATA_ROOT."
  ok "${avail_gb} Go libres"
  grep -E "[[:space:]]${DATA_ROOT}[[:space:]]" /etc/fstab | grep -qw nofail || warn "fstab : l'option « nofail » est absente pour $DATA_ROOT (un disque absent peut bloquer le démarrage). Non modifié : à ajouter vous-même si souhaité."
elif [ "$ALLOW_UNMOUNTED" = "1" ]; then
  warn "MODE DISQUE UNIQUE : $DATA_ROOT n'est pas un point de montage dédié ; les données Minecraft seront sur le même disque que le système."
  avail_gb=$(df -BG --output=avail "$DATA_ROOT" | tail -1 | tr -dc 0-9)
  if [ "${avail_gb:-0}" -ge 10 ]; then ok "${avail_gb} Go libres"; else warn "Seulement ${avail_gb:-0} Go libres : prévoyez plus de place pour les mondes et les sauvegardes."; fi
else die "$DATA_ROOT n'est pas un point de montage : refus d'installer les données Minecraft sur le disque système. (Machine à disque unique ? Relancez avec --single-disk.)"; fi

step "3/13 Utilisateur dédié « $USER_NAME »"
if id "$USER_NAME" >/dev/null 2>&1; then
  [ "$(id -u "$USER_NAME")" -ne 0 ] || die "$USER_NAME ne doit pas être root."
  ok "L'utilisateur existe déjà"
else
  useradd --system --user-group --home-dir "$STATE_DIR/home" --no-create-home --shell /usr/sbin/nologin \
          --comment "Minecraft Server Manager" "$USER_NAME"
  ok "Utilisateur système créé (sans connexion, sans sudo)"
fi

step "4/13 Création des répertoires (aucune donnée existante n'est modifiée)"
for d in servers backups uploads temporary; do
  if [ -e "$DATA_ROOT/$d" ]; then ok "$DATA_ROOT/$d existe déjà (conservé tel quel)"
  else install -d -o "$USER_NAME" -g "$USER_NAME" -m 0750 "$DATA_ROOT/$d"; ok "créé : $DATA_ROOT/$d"; fi
done
[ -e "$DATA_ROOT/Transfert" ] && ok "Dossier existant « Transfert » laissé intact"
install -d -o "$USER_NAME" -g "$USER_NAME" -m 0750 "$STATE_DIR" "$STATE_DIR/home"
install -d -o root -g "$USER_NAME" -m 0750 "$CONF_DIR"
if [ "$ALLOW_UNMOUNTED" = "1" ]; then ok "état du panneau : $STATE_DIR (base SQLite, runtimes Java ; les mondes restent dans $DATA_ROOT/servers)"
else ok "état du panneau : $STATE_DIR (SSD : base SQLite, Java, et mondes actifs dans $STATE_DIR/ssd dans la limite du quota)"; fi

step "5/13 Dépendances système"
if ! python3 -c "import venv, ensurepip" >/dev/null 2>&1; then
  command -v apt-get >/dev/null || die "python3-venv manquant et apt-get introuvable."
  DEBIAN_FRONTEND=noninteractive apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3-venv
  ok "python3-venv installé"
else ok "python3-venv déjà présent"; fi

step "6/13 Installation du backend"
install -d -m 0755 "$PREFIX"
if [ -d "$APP_DIR" ]; then mv "$APP_DIR" "$APP_DIR.avant-installation.$(date +%s)"; warn "Ancienne version conservée à côté de $APP_DIR"; fi
install -d -m 0755 "$APP_DIR"
cp -a "$SRC/mcpanel" "$SRC/requirements.txt" "$APP_DIR/"
find "$APP_DIR" -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
[ -d "$VENV" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --disable-pip-version-check -r "$APP_DIR/requirements.txt"
ok "environnement Python prêt ($("$VENV/bin/python" -c 'import fastapi,uvicorn;print("fastapi",fastapi.__version__,"uvicorn",uvicorn.__version__)'))"

step "7/13 Installation du frontend et des scripts"
cp -a "$SRC/frontend" "$APP_DIR/"
for f in update.sh backup-panel.sh uninstall.sh; do [ -f "$SRC/$f" ] && install -m 0755 "$SRC/$f" "$APP_DIR/$f"; done
[ -d "$SRC/docs" ] && cp -a "$SRC/docs" "$APP_DIR/"
[ -d "$SRC/tools" ] && cp -a "$SRC/tools" "$APP_DIR/"
"$VENV/bin/python" -m compileall -q "$APP_DIR/mcpanel" || true
chown -R root:root "$APP_DIR" "$VENV"
chmod -R go-w "$APP_DIR"
ok "frontend : $APP_DIR/frontend"

step "8/13 Configuration"
ENV_FILE="$CONF_DIR/panel.env"
if [ -f "$ENV_FILE" ]; then ok "$ENV_FILE conservé (vos réglages ne sont pas modifiés)"
else
  cat > "$ENV_FILE" <<ENV
# Réglages du panneau Minecraft (modifiables ; redémarrer le service ensuite : sudo systemctl restart minecraft-panel)
MCPANEL_HOST=0.0.0.0
MCPANEL_PORT=$PORT
MCPANEL_DATA_ROOT=$DATA_ROOT
MCPANEL_STATE_DIR=$STATE_DIR
MCPANEL_RUN_DIR=/run/minecraft-panel
# Seuls ces réseaux peuvent atteindre le panneau (réseau local par défaut). N'ajoutez jamais d'adresses publiques.
#MCPANEL_ALLOWED_NETS=192.168.1.0/24,127.0.0.0/8
MCPANEL_RAM_RESERVE_MB=2048
MCPANEL_MAX_UPLOAD_MB=4096
ENV
  if [ "$ALLOW_UNMOUNTED" = "1" ]; then
    printf '%s\n' "# Machine à disque unique : pas de disque de données dédié, pas de niveau SSD" "MCPANEL_REQUIRE_MOUNT=0" "MCPANEL_SSD_TIER=0" >> "$ENV_FILE"
  fi
  chown root:"$USER_NAME" "$ENV_FILE"; chmod 0640 "$ENV_FILE"
  ok "créé : $ENV_FILE"
fi
set -a; . "$ENV_FILE"; set +a
PORT="${MCPANEL_PORT:-$PORT}"

step "9/13 Base de données SQLite"
runuser -u "$USER_NAME" -- env MCPANEL_STATE_DIR="$STATE_DIR" MCPANEL_DATA_ROOT="$DATA_ROOT" PYTHONPATH="$APP_DIR" "$VENV/bin/python" -m mcpanel init-db
if runuser -u "$USER_NAME" -- env MCPANEL_STATE_DIR="$STATE_DIR" PYTHONPATH="$APP_DIR" "$VENV/bin/python" - <<'PY'
import sys
from mcpanel.config import Settings; from mcpanel.db import Database
sys.exit(0 if Database(Settings.from_env().db_path).one("SELECT 1 FROM users LIMIT 1") else 1)
PY
then ok "Un compte administrateur existe déjà (conservé)"
else
  echo "  Création du compte administrateur « $ADMIN » (12 caractères minimum)."
  if [ -n "${MCPANEL_ADMIN_PASSWORD_FILE:-}" ]; then
    runuser -u "$USER_NAME" -- env MCPANEL_STATE_DIR="$STATE_DIR" PYTHONPATH="$APP_DIR" "$VENV/bin/python" -m mcpanel create-admin --username "$ADMIN" --password-stdin < "$MCPANEL_ADMIN_PASSWORD_FILE"
  else
    until runuser -u "$USER_NAME" -- env MCPANEL_STATE_DIR="$STATE_DIR" PYTHONPATH="$APP_DIR" "$VENV/bin/python" -m mcpanel create-admin --username "$ADMIN"; do
      echo "  Recommencez."; done
  fi
fi

step "10/13 Service systemd"
if [ "$SKIP_SYSTEMD" = "1" ]; then warn "systemd ignoré (mode test)"; else
  render_unit() {   # recopie une unité modèle en y remplaçant les chemins et le compte par ceux de cette installation
    sed -e "s#/srv/minecraft-data#$DATA_ROOT#g" -e "s#/var/lib/minecraft-panel#$STATE_DIR#g" -e "s#/etc/minecraft-panel#$CONF_DIR#g" \
        -e "s#/opt/minecraft-panel#$PREFIX#g" -e "s#User=minecraft-panel#User=$USER_NAME#" -e "s#Group=minecraft-panel#Group=$USER_NAME#" "$1"
  }
  render_unit "$SRC/systemd/minecraft-panel.service" > "$UNIT_DIR/minecraft-panel.service"
  render_unit "$SRC/systemd/minecraft-panel-backup.service" > "$UNIT_DIR/minecraft-panel-backup.service"
  install -m 0644 "$SRC/systemd/minecraft-panel-backup.timer" "$UNIT_DIR/"
  if [ "$AWAKE" -eq 1 ]; then render_unit "$SRC/systemd/minecraft-panel-awake.service" > "$UNIT_DIR/minecraft-panel-awake.service"; ok "veille bloquée tant que le panneau tourne (verrou logind ; KDE et logind non modifiés)"
  else rm -f "$UNIT_DIR/minecraft-panel-awake.service"; warn "veille non bloquée (--no-awake) : le portable peut s'endormir"; fi
  systemctl daemon-reload
  systemctl enable minecraft-panel.service minecraft-panel-backup.timer >/dev/null 2>&1
  [ "$AWAKE" -eq 1 ] && systemctl enable minecraft-panel-awake.service >/dev/null 2>&1 || true
  ok "unités installées et activées au démarrage"
fi

step "11/13 Permissions"
chown -R "$USER_NAME:$USER_NAME" "$STATE_DIR"
chmod 0750 "$STATE_DIR"
ok "permissions appliquées (données existantes de $DATA_ROOT non touchées)"

step "12/13 Démarrage et vérification"
if [ "$SKIP_SYSTEMD" = "1" ]; then warn "démarrage ignoré (mode test)"; else
  systemctl restart minecraft-panel.service
  [ "$AWAKE" -eq 1 ] && systemctl start minecraft-panel-awake.service || true
  systemctl start minecraft-panel-backup.timer || true
  up=0
  for _ in $(seq 1 30); do
    code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/" || true)"
    [ "$code" = "200" ] && { up=1; break; }; sleep 1
  done
  if [ "$up" -eq 1 ]; then ok "le panneau répond (HTTP 200)"; else
    journalctl -u minecraft-panel -n 30 --no-pager || true; die "le panneau ne répond pas : voir journalctl -u minecraft-panel"; fi
  owner="$(ps -o user= -p "$(systemctl show -p MainPID --value minecraft-panel)" | tr -d ' ')"
  [ "$owner" = "$USER_NAME" ] && ok "processus exécuté par « $owner » (pas root)" || die "le panneau tourne sous « $owner » !"
fi

step "13/13 Terminé"
IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}' || true)"   # « ip » (iproute2) peut manquer sur une installation minimale
echo
echo "  Panneau accessible depuis le réseau local : http://${IP:-IP-DE-LA-MACHINE}:$PORT"
echo "  Compte administrateur : $ADMIN"
echo
echo "  Recommandations :"
echo "   - Réservez un bail DHCP fixe pour cette machine dans votre box ou routeur (adresse stable)."
echo "   - N'ouvrez jamais le port $PORT sur Internet (pas de redirection de port)."
echo "   - Validation sur la machine : sudo $APP_DIR/tools/validate.sh"
echo "   - Documentation : $APP_DIR/docs/UTILISATION.md"
