#!/usr/bin/env bash
# Retour arrière vers la version précédente du panneau :  sudo /opt/minecraft-panel/app/tools/rollback.sh [--keep-db] [--yes]
#
# - Remet en place le dossier « app.precedent-… » conservé par update.sh.
# - Par défaut, restaure aussi la base d'avant la migration correspondant à la version restaurée (1.0.x : panel.db.avant-v2 ;
#   1.1.x : panel.db.avant-v3 ; 3.x : panel.db.avant-v4) : ce qui a été créé depuis la mise à jour est perdu (copie de la base actuelle conservée à côté).
# - Avec --keep-db : la base actuelle est conservée (les migrations sont additives). Vers une 1.0.x, les comptes NON
#   administrateurs sont neutralisés (mot de passe invalidé), car la 1.0.x ne connaît pas les rôles et leur donnerait tous les droits.
# - Les serveurs Minecraft en marche ne sont pas interrompus et aucun monde n'est touché.
set -euo pipefail
main() {
  local KEEP_DB=0 YES=0 a
  for a in "$@"; do case "$a" in --keep-db) KEEP_DB=1;; --yes|-y) YES=1;; *) echo "Option inconnue : $a" >&2; exit 2;; esac; done
  local PREFIX="${MCPANEL_PREFIX:-/opt/minecraft-panel}" APP_DIR VENV
  APP_DIR="$PREFIX/app"; VENV="$PREFIX/venv"
  local CONF_DIR="${MCPANEL_CONF_DIR:-/etc/minecraft-panel}" STATE_DIR="${MCPANEL_STATE_DIR:-/var/lib/minecraft-panel}"
  local SKIP_SYSTEMD="${MCPANEL_SKIP_SYSTEMD:-0}" USER_NAME="${MCPANEL_USER:-minecraft-panel}"
  die() { echo "[ERREUR] $*" >&2; exit 1; }
  [ "$(id -u)" -eq 0 ] || die "Lancez avec sudo."
  [ -d "$APP_DIR" ] || die "Panneau non installé ($APP_DIR absent)."
  local PREV; PREV="$(ls -d "$APP_DIR".precedent-* 2>/dev/null | sort | tail -n 1 || true)"
  [ -n "$PREV" ] && [ -d "$PREV" ] || die "Aucune version précédente conservée (dossiers $APP_DIR.precedent-*). Rien à restaurer."
  local CUR OLD; CUR="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$APP_DIR/mcpanel/__init__.py")"; OLD="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$PREV/mcpanel/__init__.py")"
  echo "Version actuelle : $CUR — version restaurée : $OLD ($PREV)"
  case "$OLD" in 1.*|2.*|3.*)
    if [ -d "$STATE_DIR/ssd" ] && [ -n "$(ls -A "$STATE_DIR/ssd" 2>/dev/null)" ]; then
      die "Des serveurs sont sur le SSD ($STATE_DIR/ssd), ce que la version $OLD ne sait pas gérer. Remettez-les d'abord sur le disque dur depuis le panneau (fiche du serveur > « Mettre sur le disque dur »), puis relancez ce script. Rien n'a été modifié."
    fi;;
  esac
  local DBF="$STATE_DIR/panel.db" PREDB="" NEUT=0 STAMP; STAMP="$(date +%Y%m%d-%H%M%S)"
  case "$OLD" in 1.0.*) PREDB="$STATE_DIR/panel.db.avant-v2"; NEUT=1;; 1.1.*) PREDB="$STATE_DIR/panel.db.avant-v3";; 3.*) PREDB="$STATE_DIR/panel.db.avant-v4";; esac
  if [ "$KEEP_DB" -eq 0 ] && [ -n "$PREDB" ] && [ -f "$PREDB" ]; then echo "Base : restauration de la copie d'avant migration (comptes, tokens et notifications récents perdus)."
  else KEEP_DB=1; echo "Base : conservée$([ "$NEUT" -eq 1 ] && echo " ; les comptes non administrateurs seront neutralisés")."; fi
  if [ "$YES" -ne 1 ]; then read -r -p "Continuer ? [o/N] " r; case "$r" in o|O|oui|OUI) ;; *) echo "Annulé."; exit 1;; esac; fi
  [ -r "$CONF_DIR/panel.env" ] && { set -a; . "$CONF_DIR/panel.env"; set +a; }
  local PORT="${MCPANEL_PORT:-8080}"

  [ "$SKIP_SYSTEMD" = "1" ] || systemctl stop minecraft-panel          # KillMode=process : les serveurs Minecraft continuent de tourner
  if [ "$KEEP_DB" -eq 0 ]; then
    mkdir -p "$STATE_DIR/retour-$STAMP"
    for f in panel.db panel.db-wal panel.db-shm; do [ -f "$STATE_DIR/$f" ] && mv "$STATE_DIR/$f" "$STATE_DIR/retour-$STAMP/$f"; done
    cp -a "$PREDB" "$DBF"; chmod 600 "$DBF"; chown "$USER_NAME": "$DBF" 2>/dev/null || true
    echo "Base actuelle conservée dans $STATE_DIR/retour-$STAMP/"
  else
    cp -a "$DBF" "$STATE_DIR/panel.db.avant-retour-$STAMP"
    local PYSQL='import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
try:
    n = c.execute("UPDATE users SET pw_hash=? WHERE role!=?", ("!neutralise", "admin")).rowcount
    c.commit(); print(n, "compte(s) non administrateur neutralise(s).")
except sqlite3.OperationalError:
    print("Aucune colonne de role : rien a neutraliser.")'
    if [ "$NEUT" -eq 1 ]; then "$VENV/bin/python" -c "$PYSQL" "$DBF" || python3 -c "$PYSQL" "$DBF"; fi
  fi
  mv "$APP_DIR" "$APP_DIR.retire-$STAMP"; mv "$PREV" "$APP_DIR"
  if [ "$SKIP_SYSTEMD" != "1" ]; then
    systemctl daemon-reload; systemctl start minecraft-panel
    local ok=0 _
    for _ in $(seq 1 30); do [ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/" || true)" = "200" ] && { ok=1; break; }; sleep 1; done
    [ "$ok" -eq 1 ] || die "Le panneau ne répond pas après le retour arrière : consultez « journalctl -u minecraft-panel -n 50 »."
  fi
  echo "Retour arrière terminé : version $OLD. La version $CUR est conservée dans $APP_DIR.retire-$STAMP (supprimable après vérification)."
}
main "$@"
exit 0
