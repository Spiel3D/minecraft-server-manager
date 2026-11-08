# Politique de sécurité

## Signaler une faille
**N'ouvre pas d'issue publique** pour une faille de sécurité. Utilise le signalement privé de GitHub :
onglet **Security → Report a vulnerability** du dépôt (le mainteneur doit l'avoir activé dans *Settings → Code security*).
Indique la version (Paramètres → Informations), les étapes pour reproduire et l'impact que tu constates. N'envoie jamais de vrais mots de passe ni de clés API.

Seule la **dernière version publiée** reçoit des correctifs.

## Ce contre quoi le panneau est conçu
- Un panneau **privé, pour un réseau local de confiance** : il refuse toute adresse hors réseaux privés (modifiable, déconseillé).
- Authentification (scrypt, sessions `HttpOnly`/`SameSite=Strict`, CSRF), rôles vérifiés côté serveur, tokens d'API limités par portée.
- Entrées non fiables traitées comme telles : chemins confinés au dossier du serveur, archives ZIP analysées avant extraction,
  téléchargements en HTTPS avec sommes de contrôle vérifiées, CSP stricte.
- Panneau et serveurs Minecraft exécutés sous un utilisateur système dédié, jamais en root.

Détails : [`docs/SECURITE-ET-ARCHITECTURE.md`](docs/SECURITE-ET-ARCHITECTURE.md).

## Limites connues (ce ne sont pas des failles à signaler, mais à connaître)
- **Pas de HTTPS** : ne l'expose pas sur Internet. Si tu dois y accéder à distance, passe par un VPN (WireGuard, Tailscale…) plutôt que par une redirection de port.
- **Les mods et plugins sont du code arbitraire.** Un serveur Minecraft exécute ce que contiennent ses mods/plugins avec les droits de
  l'utilisateur `minecraft-panel`. L'isolation entre serveurs repose sur les dossiers, pas sur un conteneur : n'installe que des mods de sources de confiance.
- Pas de plafond mémoire par cgroup : `-Xmx` plus un budget calculé, mais la consommation hors tas d'un serveur peut dépasser la marge.
- Le mode « accès non officiel » (online-mode désactivé) d'un serveur Minecraft est possible mais demande une confirmation explicite : à n'utiliser que sur un réseau de confiance.
- Les clés API (CurseForge) sont stockées dans la base du panneau : protège l'accès au dossier d'état (`/var/lib/minecraft-panel` par défaut, droits 750).

## Bonnes pratiques pour qui installe le panneau
- Garde `MCPANEL_ALLOWED_NETS` sur des réseaux privés ; ne crée aucune redirection de port vers le panneau.
- Choisis un mot de passe administrateur long et unique ; crée des comptes « utilisateur » limités à leurs serveurs plutôt que de partager le compte admin.
- Ne publie jamais `panel.env`, la base `panel.db`, ni de captures d'écran montrant des adresses, noms de joueurs ou jetons.
