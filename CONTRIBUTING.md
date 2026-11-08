# Contribuer

Merci de t'y intéresser. Ce projet est petit et volontairement sobre : peu de dépendances, pas de framework JavaScript, pas de build.

## Lancer les tests
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q                       # suite complète (faux Java, faux serveur Minecraft, réseau simulé)

python -m playwright install chromium     # une fois
python tests/ui_smoke.py /tmp/captures    # test de l'interface dans un vrai Chromium (écrit des captures d'écran)
python tests/ui_single_disk.py            # idem en mode disque unique

sudo bash tests/installer_smoke.sh        # installation → mise à jour → désinstallation dans des dossiers temporaires
```
Aucun test n'a besoin d'Internet ni d'un vrai Java : les téléchargements et les serveurs sont simulés. Ce qui dépend des vrais services
(Mojang, Paper, Modrinth, CurseForge, Adoptium) est listé dans [`docs/TESTS-A-FAIRE-SUR-LA-MACHINE.md`](docs/TESTS-A-FAIRE-SUR-LA-MACHINE.md).

## Règles du projet
- **Aucune donnée n'est jamais supprimée sans confirmation explicite**, aucun disque n'est formaté, aucun monde n'est écrasé. `install.sh`, `update.sh` et `uninstall.sh` respectent cette règle ; un test (`tests/installer_smoke.sh`) le vérifie.
- **Ni le panneau ni les serveurs ne tournent en root.**
- **Frontend** : pas de script ni de style en ligne (la CSP les interdit). Le DOM se construit avec `h()` et `textContent`, jamais avec `innerHTML` sur des données.
- **Base de données** : migrations **additives** uniquement (`PRAGMA user_version`), avec copie `panel.db.avant-vN` avant migration.
- **Anciennes routes** : `tests/legacy_routes.json` fige les routes historiques ; `test_legacy_preserved.py` échoue si l'une disparaît.
- **Nouveaux accès réseau** : HTTPS uniquement, hôtes autorisés explicitement, sommes de contrôle vérifiées quand la source en fournit.
- **Dépendances** : n'en ajoute pas sans en discuter d'abord dans une issue.
- Interface et documentation en français ; messages d'erreur dits à l'utilisateur (ce qui s'est passé, comment corriger).

## Proposer un changement
1. Ouvre une issue pour les changements importants.
2. Une branche par sujet, des commits courts avec un message clair.
3. Ajoute ou adapte un test. Une correction de bug vient avec le test qui l'aurait attrapée.
4. Remplis la liste de la pull request (tests lancés, aucun secret ajouté).

## Types de serveur supplémentaires
Sans toucher au code : dépose un fichier `*.json` dans `server-types.d/` (voir `docs/V3-LOT1.md`).
