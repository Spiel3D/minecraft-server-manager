# Tests : ce qui est vérifié, et ce qu'il reste à valider chez vous

## Déjà vérifié automatiquement (suite `tests/`, plus de 110 tests + test de fumée Chromium)
Avec un **faux Java** et un **faux serveur Minecraft** et un réseau simulé (le vrai code de téléchargement et de validation est exécuté) :
installation Vanilla/Forge/NeoForge/Fabric/Quilt (installateurs simulés), refus des combinaisons invalides, corruption détectée,
Java 8/17/21 choisi selon la version, démarrage/arrêt/plantage/relance automatique limitée, réadoption après redémarrage du panneau,
budget RAM, ports, EULA, sauvegarde à chaud/restauration/rétention, import de ZIP (Modrinth, CurseForge, pack serveur, attaques),
mods, fichiers, traversées de chemin, CSRF, sessions, limitation des connexions, refus des IP publiques, WebSockets, interface dans Chromium,
scripts d'installation/mise à jour/désinstallation (dans un environnement de test, sans systemd).
Ajouts v1.1 : comptes et rôles, **toutes** les routes d'administration refusées à un utilisateur, filtrage des serveurs, désactivation immédiate, anti-abus de démarrage, tokens (portées, révocation, expiration, limitation 429),
API v1 (enveloppe JSON, OpenAPI, WebSocket), événements et webhook Discord (transport simulé), supervision sur de faux `/sys` et `/proc`, mise à jour 1.0.0 → 1.1.0 et retour arrière (deux modes) avec un vrai utilisateur système,
interface dans Chromium pour l'administrateur et pour un utilisateur limité (bureau et mobile), règles CSP de l'interface.

## À valider sur ta machine (rien de cela ne peut être prouvé sans la vraie machine ni Internet)
Lancez d'abord : `sudo /opt/minecraft-panel/app/tools/validate.sh` (contrôles automatiques : service, utilisateur, montage, permissions, isolation).

| § 22 | Test | Comment |
|---|---|---|
| 1 | Serveur Vanilla réel | Créer → Vanilla, dernière version stable → Démarrer → se connecter avec le client Minecraft |
| 2 | Serveur Forge réel | Créer → Forge, version recommandée ; vérifier que l'installateur officiel aboutit |
| 3 | Serveur Fabric réel | Idem avec Fabric |
| 4–5 | Deux serveurs, ports différents | Créer deux serveurs, les démarrer ensemble ; contrôle : `ss -ltnp \| grep java` |
| 6 | Deux versions de Java | Un serveur 1.12.2 (Java 8) et un 1.21.x (Java 21) : les deux runtimes apparaissent dans *Paramètres* |
| 7 | Import d'un vrai modpack | Onglet *Mods & modpack* avec un pack serveur et un `.mrpack` réels |
| 8 | Import d'un mod | Glisser un `.jar` compatible ; vérifier au démarrage |
| 9 | Modifier `server.properties` | Onglet *Fichiers* → éditer → redémarrer |
| 10–11 | Démarrer/arrêter, console en direct | Depuis l'interface |
| 12 | Plantage réel | `sudo kill -9 $(pgrep -u minecraft-panel -f 'server.jar')` : une alerte doit apparaître |
| 13 | Sauvegarde/restauration d'un vrai monde | Sauvegarder, modifier le monde en jeu, arrêter, restaurer, vérifier |
| 14 | Redémarrage de la machine | `sudo reboot` ; ensuite `systemctl status minecraft-panel`, panneau joignable ; serveur avec « démarrer avec la machine » relancé |
| 15 | Permissions et isolation | `validate.sh` avec un serveur actif ; essayer d'ouvrir `../` dans l'éditeur de fichiers |

Points d'attention connus : disque dur mécanique (sauvegardes et chargement de monde lents, une seule archive à la fois) ; pas de plafond
mémoire cgroup (voir *Sécurité et architecture*) ; HTTPS non fourni ; le test du blocage de veille (capot fermé, inactivité) est à faire sur place.

### Ajouts v1.1 à valider sur la machine
- `sudo /opt/minecraft-panel/app/tools/validate.sh` : contrôle aussi l'API v1 et l'accès aux journaux.
- **Supervision** : la page affiche-t-elle vos vraies températures (certaines machines n'en exposent aucune) et les journaux du panneau (nécessite le groupe `systemd-journal`, ajouté par la nouvelle unité) ?
- **Discord** : créez un webhook de test dans un salon, ajoutez-le dans Notifications, cliquez **Tester**, puis démarrez/arrêtez un serveur (le test réseau réel vers discord.com ne peut pas être fait sans Internet).
- **Utilisateur** : créez un compte limité à un serveur, connectez-vous depuis un autre appareil du réseau, vérifiez que seul ce serveur apparaît, que « Démarrer » fonctionne et que la copie d'adresse marche dans votre navigateur.
- **Mise à jour réelle** : `sudo ./update.sh` depuis le dossier 1.1.0 ; en cas de doute, `tools/rollback.sh`.
- Le **véritable bot Discord** n'est pas fourni : seule l'API et les webhooks sont prêts.
