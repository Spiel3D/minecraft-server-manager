# Sécurité et architecture

## Exécution
- Le panneau et tous les serveurs Minecraft tournent sous l'utilisateur système `minecraft-panel` (jamais root, pas de sudo, pas de shell).
- Unité systemd durcie : `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, écriture limitée à `/srv/minecraft-data` et `/var/lib/minecraft-panel`.
- Les serveurs sont des processus Java détachés (nouvelle session), pilotés par un tube nommé pour la console ; état dans `/run/minecraft-panel`
  (PID + date de démarrage pour détecter la réutilisation d'un PID). Ils survivent au redémarrage du panneau et sont réadoptés.
- Limite mémoire : `-Xmx` + budget calculé (RAM − réserve système − serveurs actifs, avec marge Java de 20 %). Il n'y a pas de plafond cgroup :
  la consommation hors tas d'un serveur peut dépasser la marge ; surveillez la page *Ressources*.

## Réseau
- Écoute sur toutes les interfaces mais **refuse toute adresse hors réseaux privés** (127/8, 10/8, 172.16/12, 192.168/16, fc00::/7, fe80::/10) ; modifiable.
- Aucune redirection de port Internet à créer. HTTPS n'est pas fourni : le panneau est prévu pour le réseau local uniquement.
  Ne l'exposez jamais publiquement sans reverse proxy HTTPS et authentification renforcée.
- WebSockets : session et origine vérifiées.

## Authentification
Mots de passe hachés (scrypt, sel aléatoire), 12 caractères minimum ; sessions à jeton aléatoire (seul son hash SHA-256 est stocké), cookie `HttpOnly` +
`SameSite=Strict`, jeton CSRF obligatoire pour toute écriture, temporisation exponentielle après échecs de connexion, journal des actions administratives.

## Entrées non fiables
- Chemins : jamais de `..`, liens symboliques sortants, NUL, antislash ; tout est confiné au dossier du serveur.
- ZIP importés : analysés avant extraction (traversées, liens, bombes de décompression, tailles mensongères, extensions exécutables, mondes, `server.properties`) ; extraction fichier par fichier avec contrôle de taille réelle.
- Commandes console : fins de ligne retirées (pas d'injection d'un second ordre) ; aucune commande shell n'est exécutée depuis le navigateur ; arguments JVM validés.
- Téléchargements : HTTPS uniquement, redirections non HTTPS refusées, hôtes autorisés pour les mods, sommes de contrôle vérifiées (SHA-1/256/512) ; archives Java extraites en sécurité.
- CSP stricte (aucun script ni style en ligne), `X-Frame-Options: DENY`, `nosniff`.

## Données
SQLite (WAL) dans `/var/lib/minecraft-panel` (SSD, léger). Tout le reste dans `/srv/minecraft-data`. Écritures atomiques (`.part` puis renommage).
Nettoyage automatique de `temporary/` et des téléchargements incomplets ; jamais des serveurs ni des sauvegardes.

## Comptes, rôles et permissions (v1.1)
Deux rôles (`admin`, `user`). **Toutes** les routes historiques `/api/*` exigent le rôle administrateur côté serveur (test automatique : chaque route réelle est appelée avec un compte utilisateur et doit répondre 401/403) ;
l'interface masque ce qui est interdit, mais ce n'est jamais elle qui protège. Un utilisateur n'accède qu'à `/api/me/*` et à l'API v1 limitée à ses serveurs ; un serveur non attribué répond « introuvable » (404), sans révéler qu'il existe.
Le WebSocket de la console est fermé (4403) pour un non-administrateur ; le flux d'état ne contient que les informations publiques de ses serveurs.
Désactivation, changement de rôle ou de mot de passe : sessions et tokens coupés immédiatement.

## API v1 et tokens
Tokens `mcp_…` (256 bits aléatoires), stockés en SHA-256, révocables, avec expiration facultative et permissions (*scopes*) ; droits effectifs = permissions du token ∩ droits du compte.
Les tokens ne servent que sur `/api/v1` (l'ancienne API les refuse) et ne peuvent pas créer d'autres tokens. Limites : 300 requêtes/min par IP, 120/min par token, 6 démarrages/min, 20 échecs d'authentification/5 min par IP (HTTP 429 + `Retry-After`).
Les échecs d'authentification et les démarrages sont consignés dans le journal d'audit. Réponses JSON normalisées `{ok, data, meta}` / `{ok:false, error:{code,message}}`.

## Webhooks sortants et supervision
Webhooks : https uniquement, adresses locales/privées refusées, secret jamais ré-affiché, envoi en tâche de fond avec délai maximal. Supervision : lecture seule de `/sys`, `/proc`, `systemctl show` et `journalctl -u` pour des unités d'une liste fermée, sans shell.
Le service reçoit le groupe `systemd-journal` (lecture des journaux uniquement).

## Mise à jour et retour arrière
La base passe en version 2 par une migration **additive** (aucune table ni colonne supprimée) précédée d'une copie `panel.db.avant-v2`. `update.sh` garde l'ancien code (`app.precedent-…`) et revient seul en arrière si le panneau ne redémarre pas.
Retour manuel : `sudo /opt/minecraft-panel/app/tools/rollback.sh` (restaure code **et** base d'avant ; `--keep-db` garde la base mais neutralise les comptes non administrateurs, car la 1.0.0 ne connaît pas les rôles).
