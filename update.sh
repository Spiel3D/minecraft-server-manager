#!/usr/bin/env bash
# Mise à jour du panneau depuis un dossier de projet plus récent :  sudo ./update.sh
# - sauvegarde la base et les réglages avant de toucher à quoi que ce soit ;
# - conserve l'ancienne version (retour arrière automatique si le panneau ne redémarre pas) ;
# - ne touche ni aux serveurs Minecraft, ni à leurs mondes, ni à /srv/minecraft-data ;
# - les serveurs Minecraft en marche continuent de tourner pendant la mise à jour.
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${MCPANEL_PREFIX:-/opt/minecraft-panel}"; APP_DIR="$PREFIX/app"; VENV="$PREFIX/venv"
CONF_DIR="${MCPANEL_CONF_DIR:-/etc/minecraft-panel}"; STATE_DIR="${MCPANEL_STATE_DIR:-/var/lib/minecraft-panel}"
UNIT_DIR="${MCPANEL_UNIT_DIR:-/etc/systemd/system}"; SKIP_SYSTEMD="${MCPANEL_SKIP_SYSTEMD:-0}"
USER_NAME="${MCPANEL_USER:-minecraft-panel}"
die() { echo "[ERREUR] $*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || die "Lancez avec sudo."
[ -d "$APP_DIR" ] || die "Panneau non installé ($APP_DIR absent) : utilisez install.sh."
[ -f "$SRC/mcpanel/app.py" ] || die "Lancez update.sh depuis le dossier de la nouvelle version."
[ "$SRC" != "$APP_DIR" ] || die "Lancez update.sh depuis le dossier de la nouvelle version, pas depuis $APP_DIR."
OLD="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$APP_DIR/mcpanel/__init__.py")"; NEW="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$SRC/mcpanel/__init__.py")"
echo "Version installée : $OLD — nouvelle version : $NEW"
set -a; . "$CONF_DIR/panel.env"; set +a
PORT="${MCPANEL_PORT:-8080}"

echo "==> Sauvegarde de la configuration"
[ -f "$SRC/backup-panel.sh" ] && install -m 0755 "$SRC/backup-panel.sh" "$APP_DIR/backup-panel.sh"   # la version 1.0.0 de ce script ne trouvait pas le module Python
MCPANEL_APP_DIR="$APP_DIR" MCPANEL_VENV="$VENV" MCPANEL_ENV_FILE="$CONF_DIR/panel.env" runuser -u "$USER_NAME" -- "$APP_DIR/backup-panel.sh" || echo "  (sauvegarde non réalisée : voir message ci-dessus)"

echo "==> Installation de la nouvelle version"
STAMP="$(date +%Y%m%d-%H%M%S)"; PREV="$APP_DIR.precedent-$STAMP"
cp -a "$APP_DIR" "$PREV"
rollback() {
  echo "[ERREUR] La mise à jour a échoué : retour à la version précédente." >&2
  rm -rf "$APP_DIR"; mv "$PREV" "$APP_DIR"
  [ "$SKIP_SYSTEMD" = "1" ] || systemctl restart minecraft-panel || true
  exit 1
}
trap 'rollback' ERR
rm -rf "$APP_DIR/mcpanel" "$APP_DIR/frontend"
cp -a "$SRC/mcpanel" "$SRC/frontend" "$SRC/requirements.txt" "$APP_DIR/"
for f in update.sh backup-panel.sh uninstall.sh; do [ -f "$SRC/$f" ] && install -m 0755 "$SRC/$f" "$APP_DIR/$f"; done
[ -d "$SRC/docs" ] && { rm -rf "$APP_DIR/docs"; cp -a "$SRC/docs" "$APP_DIR/"; }
[ -d "$SRC/tools" ] && { rm -rf "$APP_DIR/tools"; cp -a "$SRC/tools" "$APP_DIR/"; }
find "$APP_DIR" -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
"$VENV/bin/pip" install --quiet --disable-pip-version-check -r "$APP_DIR/requirements.txt"
"$VENV/bin/python" -m compileall -q "$APP_DIR/mcpanel" || true
chown -R root:root "$APP_DIR"; chmod -R go-w "$APP_DIR"
runuser -u "$USER_NAME" -- env PYTHONPATH="$APP_DIR" MCPANEL_STATE_DIR="$STATE_DIR" "$VENV/bin/python" -m mcpanel init-db   # migrations éventuelles

if [ "$SKIP_SYSTEMD" != "1" ]; then
  # mêmes remplacements que install.sh : chemins et compte propres à cette installation (sinon un dossier de données
  # personnalisé serait remplacé par le chemin par défaut et le service perdrait l'accès en écriture)
  DATA_ROOT="${MCPANEL_DATA_ROOT:-/srv/minecraft-data}"; STATE_DIR="${MCPANEL_STATE_DIR:-$STATE_DIR}"
  render_unit() {
    sed -e "s#/srv/minecraft-data#$DATA_ROOT#g" -e "s#/var/lib/minecraft-panel#$STATE_DIR#g" -e "s#/etc/minecraft-panel#$CONF_DIR#g" \
        -e "s#/opt/minecraft-panel#$PREFIX#g" -e "s#User=minecraft-panel#User=$USER_NAME#" -e "s#Group=minecraft-panel#Group=$USER_NAME#" "$1"
  }
  for u in minecraft-panel minecraft-panel-backup; do render_unit "$SRC/systemd/$u.service" > "$UNIT_DIR/$u.service"; done
  install -m 0644 "$SRC/systemd/minecraft-panel-backup.timer" "$UNIT_DIR/"
  [ -f "$UNIT_DIR/minecraft-panel-awake.service" ] && render_unit "$SRC/systemd/minecraft-panel-awake.service" > "$UNIT_DIR/minecraft-panel-awake.service"
  systemctl daemon-reload
  echo "==> Redémarrage du panneau (les serveurs Minecraft en marche ne sont pas interrompus)"
  systemctl restart minecraft-panel
  ok=0
  for _ in $(seq 1 30); do [ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/" || true)" = "200" ] && { ok=1; break; }; sleep 1; done
  [ "$ok" -eq 1 ] || false
fi
trap - ERR
echo "Mise à jour terminée : $OLD -> $NEW. Ancienne version conservée dans $PREV (supprimable après vérification)."
