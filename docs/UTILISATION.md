# Utilisation du panneau

Ouvrez `http://ADRESSE-DE-LA-MACHINE:8080` dans un navigateur du réseau local, connectez-vous.

## Créer un serveur
1. **Créer un serveur** → nom (sert de nom de dossier), version de Minecraft, type (Vanilla, Forge, NeoForge, Fabric, Quilt).
   Seules les versions réellement disponibles pour le type choisi sont listées ; le chargeur est proposé avec sa version recommandée.
2. RAM (la limite affichée tient compte de la réserve système, des serveurs actifs et d'une marge de 20 % pour Java) et port
   (premier port libre proposé à partir de 25565).
3. **Installer le serveur** : Java adapté (8, 11, 17, 21 ou 25) téléchargé depuis Adoptium si besoin, Minecraft et chargeur
   téléchargés depuis les sources officielles avec vérification des sommes de contrôle. Le journal d'installation est affiché en direct.
4. Le serveur est créé dans `/srv/minecraft-data/servers/NOM/`, avec `eula=false`.

## Démarrer, arrêter
- **Démarrer** : la première fois, le panneau affiche le contrat de licence Minecraft ; vous devez cocher l'acceptation.
- **Arrêter** envoie `stop` (arrêt propre, mondes sauvegardés). **Arrêt forcé** : uniquement avec confirmation explicite (risque de perte).
- Un même serveur ne peut jamais être lancé deux fois. Plusieurs serveurs peuvent tourner ensemble tant que la RAM le permet.
- Option « redémarrer après plantage » (3 essais maximum par 10 minutes) et « démarrer avec la machine ».
- Les serveurs continuent de tourner si le panneau est redémarré ou mis à jour ; ils sont arrêtés proprement à l'extinction de Debian.

## Modpacks et mods
- Onglet **Mods & modpack** : glissez un ZIP / `.mrpack`. L'archive est **analysée sans rien extraire** : type (Modrinth, CurseForge,
  pack serveur), versions, incompatibilités, conflits, éléments ignorés (scripts `.sh/.bat/.exe`, mondes, `server.properties`…).
  Vous validez avant l'import ; une sauvegarde de sécurité est faite automatiquement.
- Modrinth : mods téléchargés depuis leur manifeste, somme de contrôle vérifiée. CurseForge : nécessite une clé API (Paramètres) ;
  les mods dont l'auteur interdit le téléchargement par des outils tiers sont listés « à ajouter manuellement ».
- Mods individuels : glisser des `.jar`, activer/désactiver (renomme en `.jar.disabled`), supprimer. Les incompatibilités de chargeur,
  de version Minecraft et les doublons sont signalés.

## Console, fichiers, sauvegardes
- **Console** en direct (WebSocket), filtre, niveaux, commandes, historique. Les journaux restent sur le disque.
- **Fichiers** : navigation limitée au dossier du serveur, éditeur texte (détection de modification concurrente), envoi, téléchargement, renommage, copie, suppression avec confirmation.
- **Sauvegardes** : manuelle ou planifiée. Serveur actif : `save-off` → `save-all flush` → archive → `save-on` (ou arrêt temporaire, au choix).
  Restauration : refusée si le serveur tourne ; l'état actuel est d'abord archivé (« pre-restore »). Seules les sauvegardes **automatiques**
  sont supprimées au-delà du nombre à conserver. Aucun monde n'est jamais supprimé automatiquement.

## Ressources et alertes
Tableau de bord et page *Ressources système* : CPU, RAM, swap, disques, joueurs, PID, durée d'activité. Alertes : RAM faible, disque
presque plein, disque de données non monté, plantage, serveur qui ne répond plus.

## Paramètres
Mot de passe, réserve RAM, clé CurseForge, runtimes Java, journal des actions administratives (les mots de passe et clés n'y figurent jamais).

## Comptes et accès (v1.1)
Page **Comptes** (administrateur) : créez un compte, choisissez son rôle et les serveurs qu'il peut voir.
- **Administrateur** : tout, comme avant.
- **Utilisateur** : une interface simplifiée « Mes serveurs » avec, pour chaque serveur attribué, son état, le nombre de joueurs, la version de Minecraft, l'adresse à copier dans le jeu et un bouton **Démarrer**.
  Il n'a ni console, ni commandes, ni arrêt/redémarrage, ni fichiers, ni mods, ni paramètres, et les autres serveurs n'existent pas pour lui.
- Désactiver un compte coupe immédiatement ses sessions et ses tokens. On ne peut ni supprimer, ni rétrograder, ni désactiver le dernier administrateur, ni son propre compte.
- Anti-abus : un utilisateur ne peut pas relancer un même serveur avant la pause réglée dans **Paramètres → Démarrages par les utilisateurs** (180 s par défaut) et au plus 6 fois par heure. Les administrateurs ne sont pas limités.
- Le bouton de copie fonctionne aussi en HTTP local (repli automatique quand le navigateur refuse l'accès au presse-papiers).

## API et tokens (v1.1)
Page **API & tokens** : créez des tokens à permissions limitées (serveurs, joueurs, démarrage, événements ; « système » réservé aux administrateurs),
avec une expiration facultative. Le token n'est affiché **qu'une fois** ; seul son hash est conservé. Révocation immédiate en un clic.
L'onglet **Documentation** liste tous les points d'accès (généré depuis la spécification OpenAPI, aussi disponible sur `/api/v1/openapi.json`).
Exemple : `curl -H "Authorization: Bearer mcp_…" http://IP-DU-PANNEAU:8080/api/v1/servers`.
Un token ne peut jamais dépasser les droits de son compte, ni créer d'autres tokens.

## Notifications Discord (v1.1)
Page **Notifications** : collez l'adresse d'un webhook Discord (salon → Intégrations → Webhooks) pour recevoir un message quand un serveur démarre, s'arrête ou plante.
Aucun bot n'est nécessaire. L'adresse est enregistrée mais jamais ré-affichée en entier ; seul le https est accepté, jamais une adresse locale.
Le bouton **Tester** envoie un message d'essai. L'API (WebSocket `/api/v1/ws`, `GET /api/v1/events`) permet de brancher plus tard un vrai bot sans modifier le panneau.

## Supervision Debian (v1.1)
Page **Supervision Debian** : processeur, mémoire, disque, températures disponibles, réseau (débits), état des services du panneau, processus Minecraft et journaux utiles.
**Lecture seule** : aucune action système, aucun shell. Les commandes utilisées ont des arguments fixes et des noms de services pris dans une liste fermée.
