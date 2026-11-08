#!/usr/bin/env bash
# Désinstallation du panneau :  sudo ./uninstall.sh [--stop-servers] [--purge-state] [--remove-user]
# Par défaut : retire le service et le code ; CONSERVE les serveurs, mondes, sauvegardes (/srv/minecraft-data),
# la base du panneau et les runtimes Java (/var/lib/minecraft-panel) et l'utilisateur dédié.
# Rien dans /srv/minecraft-data n'est JAMAIS supprimé par ce script.
set -euo pipefail
PREFIX="${MCPANEL_PREFIX:-/opt/minecraft-panel}"; APP_DIR="$PREFIX/app"; VENV="$PREFIX/venv"
CONF_DIR="${MCPANEL_CONF_DIR:-/etc/minecraft-panel}"; STATE_DIR="${MCPANEL_STATE_DIR:-/var/lib/minecraft-panel}"
UNIT_DIR="${MCPANEL_UNIT_DIR:-/etc/systemd/system}"; SKIP_SYSTEMD="${MCPANEL_SKIP_SYSTEMD:-0}"
USER_NAME="${MCPANEL_USER:-minecraft-panel}"
STOP=0; PURGE=0; RMUSER=0
for a in "$@"; do case "$a" in
  --stop-servers) STOP=1;; --purge-state) PURGE=1;; --remove-user) RMUSER=1;;
  -h|--help) sed -n '2,6p' "$0"; exit 0;; *) echo "Option inconnue : $a" >&2; exit 1;; esac; done
[ "$(id -u)" -eq 0 ] || { echo "Lancez avec sudo." >&2; exit 1; }
DATA_ROOT="${MCPANEL_DATA_ROOT:-}"      # uniquement pour les messages : ce script ne supprime jamais rien dedans
[ -n "$DATA_ROOT" ] || { [ -r "$CONF_DIR/panel.env" ] && DATA_ROOT="$(sed -n 's/^MCPANEL_DATA_ROOT=//p' "$CONF_DIR/panel.env" | tail -1)"; }
DATA_ROOT="${DATA_ROOT:-/srv/minecraft-data}"
case "$PREFIX:$STATE_DIR:$CONF_DIR" in ::*|/:*|*:/:*|*:/|/usr*|/etc:*|/var:*|/srv*|/home:*) echo "Chemins d'installation suspects : abandon." >&2; exit 1;; esac

if [ -x "$VENV/bin/python" ] && [ -f "$CONF_DIR/panel.env" ]; then
  if [ "$STOP" -eq 1 ]; then
    echo "==> Arrêt propre des serveurs Minecraft"
    (set -a; . "$CONF_DIR/panel.env"; set +a; cd "$APP_DIR"; runuser -u "$USER_NAME" -- env MCPANEL_STATE_DIR="$STATE_DIR" MCPANEL_DATA_ROOT="${MCPANEL_DATA_ROOT:-/srv/minecraft-data}" MCPANEL_RUN_DIR="${MCPANEL_RUN_DIR:-/run/minecraft-panel}" PYTHONPATH="$APP_DIR" "$VENV/bin/python" -m mcpanel stop-all) || { echo "Certains serveurs sont encore actifs : arrêtez-les depuis le panneau." >&2; exit 1; }
  else
    live="$(cd "$APP_DIR"; runuser -u "$USER_NAME" -- env MCPANEL_STATE_DIR="$STATE_DIR" MCPANEL_RUN_DIR="${MCPANEL_RUN_DIR:-/run/minecraft-panel}" PYTHONPATH="$APP_DIR" "$VENV/bin/python" -m mcpanel running 2>/dev/null || true)"
    if [ -n "$live" ]; then
      echo "Des serveurs Minecraft sont en marche :"; echo "$live" | sed 's/^/  - /'
      echo "Arrêtez-les depuis le panneau, ou relancez avec --stop-servers (arrêt propre)." >&2; exit 1
    fi
  fi
fi
if [ "$SKIP_SYSTEMD" != "1" ]; then
  echo "==> Retrait du service"
  systemctl disable --now minecraft-panel-awake.service minecraft-panel-backup.timer minecraft-panel.service 2>/dev/null || true
  rm -f "$UNIT_DIR"/minecraft-panel.service "$UNIT_DIR"/minecraft-panel-awake.service "$UNIT_DIR"/minecraft-panel-backup.service "$UNIT_DIR"/minecraft-panel-backup.timer
  systemctl daemon-reload
fi
echo "==> Suppression du code ($PREFIX) et de la configuration ($CONF_DIR)"
rm -rf "$PREFIX" "$CONF_DIR"
if [ "$PURGE" -eq 1 ]; then
  if [ -d "$STATE_DIR/ssd" ] && [ -n "$(ls -A "$STATE_DIR/ssd" 2>/dev/null)" ]; then
    echo "==> Suppression de l'état du panneau ($STATE_DIR : base, runtimes Java) — SAUF $STATE_DIR/ssd qui contient des serveurs Minecraft (conservé)"
    find "$STATE_DIR" -mindepth 1 -maxdepth 1 ! -name ssd -exec rm -rf {} +
  else echo "==> Suppression de l'état du panneau ($STATE_DIR : base, runtimes Java)"; rm -rf "$STATE_DIR"; fi
else echo "État conservé : $STATE_DIR (base du panneau, Java). Supprimable avec --purge-state."; fi
if [ "$RMUSER" -eq 1 ]; then userdel "$USER_NAME" 2>/dev/null && echo "Utilisateur $USER_NAME supprimé." || true
  echo "Attention : les fichiers de $DATA_ROOT appartenant à cet utilisateur restent intacts (propriétaire numérique conservé)."; fi
echo "Terminé. Vos serveurs, mondes et sauvegardes dans $DATA_ROOT n'ont PAS été touchés."
