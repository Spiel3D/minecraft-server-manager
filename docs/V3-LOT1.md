# V3 — Lot 1 : administration et stabilité

Version `3.0.0-lot1`. Migration de base v3 (additive, copie `panel.db.avant-v3` automatique).

- **RAM (§2)** : plafond par serveur = RAM totale − réserve système (2048 Mo par défaut). Il ne dépend plus des serveurs en marche
  ni de la marge Java. Un prévisionnel (somme des serveurs configurés) avertit sans bloquer ; le contrôle réel reste fait au démarrage.
- **Noms libres (§3)** : accents, espaces, emojis. Le dossier technique est un identifiant ASCII dérivé automatiquement ; renommer ne déplace rien.
- **Types de serveur (§3)** : Paper et Purpur (téléchargement vérifié), Spigot, CraftBukkit, Mohist, Magma, Arclight, jar personnalisé
  (jar fourni, analysé sans être exécuté). Extension sans reconstruction : fichiers `*.json` dans `/var/lib/minecraft-panel/server-types.d/`.
- **Whitelist par serveur (§4)** : onglet dédié, serveur arrêté (fichier + Mojang) ou en marche (commandes + confirmation).
- **Paramètres Minecraft (§5)** : ~60 réglages par catégories, validations, historique avec retour arrière, propriétés inconnues conservées.

À valider chez vous (réseau réel) : API Paper/Purpur, recherche Mojang, comportement des jars hybrides.
