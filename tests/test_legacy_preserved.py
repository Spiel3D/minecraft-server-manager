"""Non-régression de l'upgrade : toute route, tout onglet et toute fonction de la version 1.0.0 doit rester présent."""
import json
import re
from pathlib import Path

from mcpanel.app import create_app

HERE = Path(__file__).resolve().parent
FRONT = HERE.parent / "frontend"


def test_all_v1_0_routes_still_exist(panel):
    legacy = json.loads((HERE / "legacy_routes.json").read_text())
    app = create_app(panel=panel)
    have = {(m, r.path) for r in app.routes if hasattr(r, "methods") for m in r.methods}
    have |= {("WS", r.path) for r in app.routes if r.__class__.__name__ == "APIWebSocketRoute"}
    missing = [tuple(x) for x in legacy if tuple(x) not in have]
    assert not missing, missing


def test_all_v1_0_ui_features_still_in_frontend():
    """Chaque fonction visible de la 1.0.0 doit rester dans les fichiers de l'interface (libellés d'origine)."""
    text = "\n".join(p.read_text() for p in FRONT.glob("*.js"))
    for label in ["Tableau de bord", "Mes serveurs", "Créer un serveur", "Gestion des fichiers", "Gestion des mods", "Sauvegardes",
                  "Ressources système", "Paramètres", "Démarrer", "Arrêter", "Redémarrer", "Console", "Fichiers", "Configuration",
                  "Importer un modpack", "Arrêt forcé", "Relancer l'installation", "Mot de passe administrateur", "Clé API CurseForge",
                  "Runtimes Java", "Journal des actions administratives", "Supprimer définitivement", "Marquer comme vue",
                  "Afficher les snapshots", "Sauvegarder maintenant", "Restaurer", "Télécharger", "Renommer ou déplacer",
                  "Réserve RAM pour le système", "Arguments JVM supplémentaires", "Démarrer automatiquement avec la machine",
                  "Redémarrer automatiquement après un plantage", "Sauvegardes automatiques", "Contrat de licence Minecraft",
                  "Effacer l'affichage", "Détail des fichiers", "Mods installés"]:
        assert label in text, f"fonction de la 1.0.0 disparue de l'interface : {label}"
