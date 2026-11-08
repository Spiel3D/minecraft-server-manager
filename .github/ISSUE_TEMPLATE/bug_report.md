---
name: Signaler un bug
about: Quelque chose ne marche pas comme prévu
labels: bug
---

**Ce qui se passe** (et ce que tu attendais)

**Comment le reproduire**

**Ton installation**
- Système (`cat /etc/os-release`) :
- Version du panneau (Paramètres → Informations) :
- Installation avec un disque de données dédié, ou `--single-disk` ?

**Journal** (`journalctl -u minecraft-panel -n 80 --no-pager`) — retire tout ce qui est privé (adresses IP, noms de joueurs) :

> Ne colle jamais ici une clé API, un mot de passe ou le contenu de `panel.env`.
