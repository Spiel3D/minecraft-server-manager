#!/usr/bin/env bash
# Vérifications sur la VRAIE machine (lecture seule, ne modifie rien) :  sudo /opt/minecraft-panel/app/tools/validate.sh
# Couvre les contrôles automatisables du §22 ; les autres (création réelle de serveurs, plantage, redémarrage…) sont
# décrits dans docs/TESTS-A-FAIRE-SUR-LA-MACHINE.md avec la marche à suivre dans le panneau.
set -uo pipefail
CONF="${MCPANEL_CONF_DIR:-/etc/minecraft-panel}/panel.env"
[ -r "$CONF" ] && { set -a; . "$CONF"; set +a; }
DATA="${MCPANEL_DATA_ROOT:-/srv/minecraft-data}"; STATE="${MCPANEL_STATE_DIR:-/var/lib/minecraft-panel}"
PORT="${MCPANEL_PORT:-8080}"; U="${MCPANEL_USER:-minecraft-panel}"
fail=0; pass=0
ok()   { echo "  [OK]    $*"; pass=$((pass+1)); }
ko()   { echo "  [ÉCHEC] $*"; fail=$((fail+1)); }
inf()  { echo "  [INFO]  $*"; }
chk()  { local d="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$d"; else ko "$d"; fi; }

echo "== Service et exécution"
chk "service minecraft-panel actif" systemctl is-active --quiet minecraft-panel
chk "service activé au démarrage de la machine" systemctl is-enabled --quiet minecraft-panel
pid="$(systemctl show -p MainPID --value minecraft-panel 2>/dev/null)"
if [ -n "$pid" ] && [ "$pid" != 0 ]; then
  owner="$(ps -o user= -p "$pid" | tr -d ' ')"; [ "$owner" = "$U" ] && ok "panneau exécuté par $owner (pas root)" || ko "panneau exécuté par $owner"
fi
code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/" || true)"; [ "$code" = 200 ] && ok "interface servie (HTTP 200)" || ko "interface : HTTP $code"
code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/servers" || true)"; [ "$code" = 401 ] && ok "API protégée sans session (HTTP 401)" || ko "API sans session : HTTP $code (attendu 401)"
code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/v1/ping" || true)"; [ "$code" = 200 ] && ok "API v1 disponible (/api/v1/ping)" || ko "API v1 : HTTP $code"
code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/v1/servers" || true)"; [ "$code" = 401 ] && ok "API v1 protégée sans token (HTTP 401)" || ko "API v1 sans token : HTTP $code (attendu 401)"
if [ -n "$pid" ] && [ "$pid" != 0 ]; then id -nG "$U" 2>/dev/null | grep -qw systemd-journal && ok "compte $U dans le groupe systemd-journal (journaux de la supervision)" || inf "compte $U hors groupe systemd-journal : le service l'obtient via SupplementaryGroups (visible dans la page Supervision)"; fi
chk "veille bloquée par le panneau (verrou logind)" bash -c "systemd-inhibit --list --no-pager | grep -q 'Minecraft Server Manager'"

echo "== Disque et isolation (§22-15)"
if [ "${MCPANEL_REQUIRE_MOUNT:-1}" = "0" ]; then inf "mode disque unique (MCPANEL_REQUIRE_MOUNT=0) : $DATA n'est pas un point de montage dédié, contrôle ignoré"
else chk "$DATA est un point de montage" mountpoint -q "$DATA"; fi
for d in servers backups uploads temporary; do
  o="$(stat -c %U "$DATA/$d" 2>/dev/null)"; m="$(stat -c %a "$DATA/$d" 2>/dev/null)"
  [ "$o" = "$U" ] && ok "$DATA/$d appartient à $U (mode $m)" || ko "$DATA/$d : propriétaire $o"
  case "$m" in *[2367]) ko "$DATA/$d accessible en écriture aux autres (mode $m)";; esac
done
[ -d "$DATA/Transfert" ] && ok "dossier existant « Transfert » toujours présent"
big="$(du -sm --exclude=java --exclude=ssd "$STATE" 2>/dev/null | cut -f1)"; [ "${big:-0}" -lt 500 ] && ok "disque système : état du panneau léger (${big} Mo hors Java et hors mondes actifs du niveau SSD)" || ko "disque système : $STATE pèse ${big} Mo hors Java et hors niveau SSD (données Minecraft égarées ?)"
if find / -xdev -name 'level.dat' -not -path '/proc/*' -not -path "$STATE/ssd/*" -not -path "$DATA/*" 2>/dev/null | head -1 | grep -q .; then ko "un monde Minecraft (level.dat) existe en dehors de $DATA et du niveau SSD ($STATE/ssd)"; else ok "aucun monde Minecraft en dehors des emplacements prévus ($DATA, $STATE/ssd)"; fi
[ "$(stat -c %a "$CONF" 2>/dev/null)" = 640 ] && ok "panel.env en 640" || ko "droits de panel.env inattendus"

echo "== Serveurs Minecraft actifs : propriétaire et dossiers"
n=0
while read -r p; do
  [ -z "$p" ] && continue; n=$((n+1))
  own="$(ps -o user= -p "$p" | tr -d ' ')"; cwd="$(readlink "/proc/$p/cwd" 2>/dev/null)"
  [ "$own" = "$U" ] && ok "java PID $p exécuté par $own, dossier $cwd" || ko "java PID $p exécuté par $own (devrait être $U)"
  case "$cwd" in
    "$DATA"/servers/*) ok "  isolé dans son dossier $cwd";;
    "$STATE"/ssd/*) if [ "${MCPANEL_SSD_TIER:-1}" = "0" ]; then ko "  dossier de travail dans le niveau SSD alors qu'il est désactivé : $cwd"; else ok "  isolé dans son dossier $cwd (niveau SSD)"; fi;;
    *) ko "  dossier de travail hors de $DATA/servers et de $STATE/ssd : $cwd";;
  esac
done < <(pgrep -u "$U" java)
[ "$n" -eq 0 ] && inf "aucun serveur Minecraft actif en ce moment (démarrez-en un puis relancez ce script pour ces contrôles)"
dups="$(for p in $(pgrep -u "$U" java); do readlink "/proc/$p/cwd"; done | sort | uniq -d)"
[ -z "$dups" ] && ok "aucun serveur lancé en double" || ko "instances multiples dans : $dups"

echo "== Réseau"
if ss -ltn | awk '{print $4}' | grep -Eq "(^|:)$PORT$"; then ok "le panneau écoute sur le port $PORT"; else ko "rien n'écoute sur $PORT"; fi
inf "accès limité aux réseaux privés par le panneau lui-même (MCPANEL_ALLOWED_NETS) ; vérifiez qu'aucune redirection de port n'existe sur votre box ou routeur"
inf "adresse : http://$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}'):$PORT"

echo "== Sauvegardes du panneau"
ls "$DATA"/backups/_panel/panel-config-*.tar.gz >/dev/null 2>&1 && ok "sauvegarde de configuration présente" || inf "aucune sauvegarde de configuration encore (la minuterie passe chaque nuit vers 4 h 15)"
systemctl is-active --quiet minecraft-panel-backup.timer && ok "minuterie de sauvegarde active" || ko "minuterie de sauvegarde inactive"

echo; echo "Résultat : $pass contrôles réussis, $fail échec(s)."; [ "$fail" -eq 0 ]
