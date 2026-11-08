#!/usr/bin/env bash
# Sauvegarde la configuration du panneau (base SQLite cohérente + fichier de réglages), pas les serveurs Minecraft.
# Utilisable à la main (sudo) ou via la minuterie systemd. Conserve les 14 dernières sauvegardes.
set -euo pipefail
APP_DIR="${MCPANEL_APP_DIR:-/opt/minecraft-panel/app}"
VENV="${MCPANEL_VENV:-/opt/minecraft-panel/venv}"
ENV_FILE="${MCPANEL_ENV_FILE:-/etc/minecraft-panel/panel.env}"
DATA_ROOT="${MCPANEL_DATA_ROOT:-/srv/minecraft-data}"
STATE_DIR="${MCPANEL_STATE_DIR:-/var/lib/minecraft-panel}"
DEST="${1:-$DATA_ROOT/backups/_panel}"
KEEP=14

if [ -r "$ENV_FILE" ]; then set -a; . "$ENV_FILE"; set +a; fi
DATA_ROOT="${MCPANEL_DATA_ROOT:-$DATA_ROOT}"; STATE_DIR="${MCPANEL_STATE_DIR:-$STATE_DIR}"
[ -d "$DATA_ROOT" ] || { echo "Dossier de données introuvable : $DATA_ROOT" >&2; exit 1; }
if [ "${MCPANEL_REQUIRE_MOUNT:-1}" != "0" ] && ! mountpoint -q "$DATA_ROOT"; then
  echo "$DATA_ROOT n'est pas monté : sauvegarde annulée (jamais d'écriture sur le SSD)." >&2; exit 1
fi
mkdir -p "$DEST"; chmod 750 "$DEST" 2>/dev/null || true
stamp="$(date +%Y%m%d-%H%M%S)"
tmp="$(mktemp -d "$DEST/.tmp.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT
PYTHONPATH="$APP_DIR" MCPANEL_STATE_DIR="$STATE_DIR" "$VENV/bin/python" -m mcpanel backup-db "$tmp/panel.db" >/dev/null
[ -r "$ENV_FILE" ] && cp "$ENV_FILE" "$tmp/panel.env"
out="$DEST/panel-config-$stamp.tar.gz"
tar -C "$tmp" -czf "$out.part" .
mv "$out.part" "$out"
chmod 640 "$out"
ls -1t "$DEST"/panel-config-*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f --
echo "Sauvegarde du panneau : $out"
