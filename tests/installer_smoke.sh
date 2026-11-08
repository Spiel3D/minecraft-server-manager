#!/usr/bin/env bash
# Test de bout en bout des scripts d'installation, dans des dossiers temporaires (aucun chemin système touché) :
#   sudo bash tests/installer_smoke.sh
# - refus d'installer sans disque dédié sauf --single-disk ; refus des dossiers système ;
# - install.sh → service démarré → update.sh → uninstall.sh, avec un dossier de données PERSONNALISÉ ;
# - les données existantes (dossier « Transfert ») ne sont jamais modifiées ni supprimées.
# systemd est remplacé par un faux « systemctl » : ce test vérifie les scripts et les unités générées, pas systemd lui-même.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Lancez avec sudo." >&2; exit 1; }
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
T="$(mktemp -d /tmp/mcpanel-installer.XXXXXX)"; chmod 755 "$T"
U="mcpaneltest$$"
fails=0; pass=0
ok() { echo "  [OK]    $*"; pass=$((pass+1)); }
ko() { echo "  [ÉCHEC] $*"; fails=$((fails+1)); }
check() { local d="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$d"; else ko "$d"; fi; }
cleanup() {
  [ -f "$T/pid" ] && kill "$(cat "$T/pid")" 2>/dev/null || true
  userdel "$U" 2>/dev/null || true
  rm -rf "$T"
}
trap cleanup EXIT

PORT="$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')"
export MCPANEL_USER="$U" MCPANEL_PREFIX="$T/opt" MCPANEL_STATE_DIR="$T/state" MCPANEL_CONF_DIR="$T/etc" MCPANEL_UNIT_DIR="$T/units"
export MCPANEL_RUN_DIR="$T/run"
DATA="$T/mc-data"
mkdir -p "$T/units" "$T/bin" "$T/run"
printf 'mot-de-passe-de-test-123' > "$T/pw"; chmod 600 "$T/pw"; export MCPANEL_ADMIN_PASSWORD_FILE="$T/pw"

# faux systemctl : « restart » lance vraiment le service décrit par l'unité générée (utilisateur, EnvironmentFile, ExecStart)
cat > "$T/bin/systemctl" <<STUB
#!/usr/bin/env bash
unit="$T/units/minecraft-panel.service"
case "\$1" in
  restart)
    [ "\$2" = minecraft-panel.service ] || exit 0
    [ -f "$T/pid" ] && kill "\$(cat "$T/pid")" 2>/dev/null && sleep 1
    usr="\$(sed -n 's/^User=//p' "\$unit")"; envf="\$(sed -n 's/^EnvironmentFile=//p' "\$unit")"
    wd="\$(sed -n 's/^WorkingDirectory=//p' "\$unit")"; ex="\$(sed -n 's/^ExecStart=//p' "\$unit")"
    chown "\$usr" "$T/run"
    runuser -u "\$usr" -- bash -c "set -a; . '\$envf'; set +a; cd '\$wd'; MCPANEL_RUN_DIR='$T/run' exec \$ex" >"$T/service.log" 2>&1 &
    rm -f "$T/pid"
    for _ in \$(seq 1 40); do   # runuser reste parent en root : on retient le PID du processus python lui-même
      p="\$(pgrep -u "\$usr" -f 'mcpanel serve' | head -1 || true)"; [ -n "\$p" ] && { echo "\$p" > "$T/pid"; break; }; sleep 0.25
    done;;
  show) cat "$T/pid" 2>/dev/null || echo 0;;
  is-active) [ -f "$T/pid" ] && kill -0 "\$(cat "$T/pid")" 2>/dev/null;;
  *) exit 0;;
esac
STUB
chmod +x "$T/bin/systemctl"; export PATH="$T/bin:$PATH"

echo "== 1. Garde-fous"
mkdir -p "$DATA"
out="$(bash "$ROOT/install.sh" --yes --data-root "$DATA" --port "$PORT" 2>&1 || true)"
echo "$out" | grep -q "single-disk" && ok "sans disque dédié : refus avec indication de --single-disk" || ko "pas de refus clair sans disque dédié : $out"
[ ! -e "$T/opt" ] && ok "rien n'a été installé après le refus" || ko "des fichiers ont été installés malgré le refus"
out="$(bash "$ROOT/install.sh" --yes --single-disk --data-root /usr --port "$PORT" 2>&1 || true)"
echo "$out" | grep -qi "dossier système" && ok "dossier de données système refusé (/usr)" || ko "/usr accepté : $out"
out="$(bash "$ROOT/install.sh" --yes --single-disk --data-root "relatif/chemin" --port "$PORT" 2>&1 || true)"
echo "$out" | grep -qi "invalide" && ok "chemin relatif refusé" || ko "chemin relatif accepté"
out="$(bash "$ROOT/install.sh" --yes --single-disk --data-root "/tmp/a b" --port "$PORT" 2>&1 || true)"
echo "$out" | grep -qi "invalide" && ok "chemin avec espace refusé" || ko "chemin avec espace accepté"

echo "== 2. Installation en mode disque unique (dossier de données personnalisé)"
mkdir -p "$DATA/Transfert"; echo "important" > "$DATA/Transfert/note.txt"
sum_before="$(cd "$DATA" && find . -type f -exec sha256sum {} + | sort)"
if bash "$ROOT/install.sh" --yes --single-disk --data-root "$DATA" --port "$PORT" > "$T/install.log" 2>&1; then ok "install.sh réussi"; else ko "install.sh a échoué"; tail -30 "$T/install.log"; fi
ENVF="$T/etc/panel.env"
check "panel.env : dossier de données personnalisé" grep -qx "MCPANEL_DATA_ROOT=$DATA" "$ENVF"
check "panel.env : MCPANEL_SSD_TIER=0" grep -qx "MCPANEL_SSD_TIER=0" "$ENVF"
check "panel.env : MCPANEL_REQUIRE_MOUNT=0" grep -qx "MCPANEL_REQUIRE_MOUNT=0" "$ENVF"
check "dossiers servers/backups/uploads/temporary créés" bash -c "for d in servers backups uploads temporary; do [ -d '$DATA'/\$d ] || exit 1; done"
[ "$(stat -c %U "$DATA/servers")" = "$U" ] && ok "servers appartient à l'utilisateur dédié" || ko "propriétaire de servers inattendu"
UNIT="$T/units/minecraft-panel.service"
check "unité : ReadWritePaths avec les chemins personnalisés" grep -qx "ReadWritePaths=$DATA $T/state" "$UNIT"
check "unité : ExecStart dans le préfixe personnalisé" grep -qx "ExecStart=$T/opt/venv/bin/python -m mcpanel serve" "$UNIT"
check "unité : EnvironmentFile personnalisé" grep -qx "EnvironmentFile=$T/etc/panel.env" "$UNIT"
check "unité : utilisateur dédié" grep -qx "User=$U" "$UNIT"
! grep -q "/srv/minecraft-data\|/opt/minecraft-panel\|/etc/minecraft-panel\|/var/lib/minecraft-panel" "$T"/units/*.service && ok "aucun chemin par défaut résiduel dans les unités" || ko "chemin par défaut résiduel dans les unités"
code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/" || true)"; [ "$code" = 200 ] && ok "interface servie (HTTP 200)" || ko "interface : HTTP $code"
code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/servers" || true)"; [ "$code" = 401 ] && ok "API protégée sans session (401)" || ko "API : HTTP $code"
owner="$(ps -o user= -p "$(cat "$T/pid")" | tr -d ' ')"; [ "$owner" = "$U" ] && ok "service exécuté par l'utilisateur dédié (pas root)" || ko "service exécuté par $owner"
now="$(cd "$DATA" && find . -type f -exec sha256sum {} + | sort | grep Transfert)"
echo "$sum_before" | grep -q "Transfert" && [ "$(echo "$sum_before" | grep Transfert)" = "$now" ] && ok "« Transfert » intact" || ko "« Transfert » modifié"

echo "== 3. Mise à jour sur place (update.sh) : les chemins personnalisés sont conservés"
if bash "$ROOT/update.sh" > "$T/update.log" 2>&1; then ok "update.sh réussi"; else ko "update.sh a échoué"; tail -30 "$T/update.log"; fi
check "unité après mise à jour : ReadWritePaths personnalisés" grep -qx "ReadWritePaths=$DATA $T/state" "$UNIT"
check "unité après mise à jour : ExecStart personnalisé" grep -qx "ExecStart=$T/opt/venv/bin/python -m mcpanel serve" "$UNIT"
! grep -q "/srv/minecraft-data" "$T"/units/*.service && ok "aucun chemin par défaut après mise à jour" || ko "chemin par défaut réintroduit par update.sh"
code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/" || true)"; [ "$code" = 200 ] && ok "panneau toujours servi après mise à jour" || ko "HTTP $code après mise à jour"

echo "== 4. Désinstallation (les données restent)"
kill "$(cat "$T/pid")" 2>/dev/null || true; sleep 1; rm -f "$T/pid"
if bash "$ROOT/uninstall.sh" > "$T/uninstall.log" 2>&1; then ok "uninstall.sh réussi"; else ko "uninstall.sh a échoué"; tail -20 "$T/uninstall.log"; fi
[ ! -e "$T/opt" ] && ok "code retiré" || ko "code encore présent"
[ -f "$DATA/Transfert/note.txt" ] && [ -d "$DATA/servers" ] && ok "données conservées (Transfert, servers)" || ko "données supprimées !"
grep -q "$DATA" "$T/uninstall.log" && ok "message final cite le bon dossier de données" || ko "message final : mauvais dossier"

echo; echo "Résultat : $pass contrôles réussis, $fails échec(s)."
[ "$fails" -eq 0 ]
