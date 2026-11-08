## Ce que change cette PR

## Comment je l'ai testé
- [ ] `python -m pytest -q` passe
- [ ] Si l'interface change : `python tests/ui_smoke.py` et `python tests/ui_single_disk.py` passent
- [ ] Si un script d'installation change : `sudo bash tests/installer_smoke.sh` passe

## Sécurité
- [ ] Aucun secret, mot de passe ou adresse privée dans le code ou les tests
- [ ] Pas de script ni de style en ligne dans le frontend (la CSP les interdit)
