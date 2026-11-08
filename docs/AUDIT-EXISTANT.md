# Audit de l'existant (version 1.0.0) — base de l'upgrade 1.1.0

Sauvegarde complète avant toute modification : `minecraft-panel-1.0.0-avant-upgrade.tar.gz`.
Les 51 routes et WebSockets de la 1.0.0 sont figées dans `tests/legacy_routes.json` ; `tests/test_legacy_preserved.py`
échoue si l'une d'elles ou l'un des libellés de fonction de l'interface disparaît.

## Fonctions conservées (toutes)
- Serveurs : création Vanilla/Forge/NeoForge/Fabric/Quilt, installation avec journal, réinstallation, configuration (RAM, port,
  JVM, démarrage auto, relance après plantage, description), suppression avec saisie du nom, EULA explicite.
- Processus : démarrer, arrêter proprement, redémarrer, arrêt forcé confirmé, plantage détecté, réadoption après redémarrage du panneau.
- Console en direct, commandes, historique, filtres. Fichiers : navigation, édition, envoi, téléchargement, renommage, copie, suppression.
- Mods : liste, diagnostics, activer/désactiver, supprimer, envoi. Modpacks : analyse, import Modrinth/CurseForge/pack serveur.
- Sauvegardes : manuelle/automatique, téléchargement, restauration, rétention. Ressources, alertes, journal d'audit, paramètres, Java.
- Sécurité : scrypt, sessions, CSRF, limitation des connexions, réseaux privés, CSP stricte.

## Ce que l'upgrade ajoute (sans rien retirer)
Comptes et rôles, interface utilisateur simplifiée, API `/api/v1`, tokens, événements et webhooks (Discord), supervision Debian, refonte graphique.

## Migration et retour arrière
- Base : migration additive (nouvelles colonnes et tables uniquement). Une copie `panel.db.avant-v2` est faite automatiquement avant
  la migration, et `update.sh` sauvegarde en plus la configuration.
- Code : `update.sh` garde l'ancienne version et revient en arrière seul si le panneau ne redémarre pas ; `tools/rollback.sh`
  permet un retour manuel (code + base).
