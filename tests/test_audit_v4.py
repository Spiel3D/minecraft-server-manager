"""Régressions trouvées pendant l'audit V4."""
import pytest

from conftest import tick_until
from mcpanel.jobs import UserError


def test_save_config_while_running_with_unchanged_ram_and_port(panel, make_server):
    """Bug : l'interface renvoie toujours RAM et port ; un serveur en marche refusait tout enregistrement."""
    s = make_server(name="EnMarche")
    panel.manager.start(s["id"])
    assert tick_until(panel, lambda: panel.manager.state(panel.manager.server(s["id"])) == "running")
    out = panel.update_server(s["id"], {"description": "nouvelle", "ram_max_mb": s["ram_max_mb"], "ram_min_mb": s["ram_min_mb"], "port": s["port"]})
    assert out["description"] == "nouvelle"
    with pytest.raises(UserError, match="Arrêtez"):
        panel.update_server(s["id"], {"ram_max_mb": s["ram_max_mb"] + 256})
    with pytest.raises(UserError, match="Arrêtez"):
        panel.update_server(s["id"], {"port": s["port"] + 1})
    panel.manager.stop(s["id"])


def test_bad_port_value_is_a_clean_error(panel, make_server):
    s = make_server(name="Port")
    with pytest.raises(UserError, match="Port invalide"):
        panel.update_server(s["id"], {"port": "abc"})
    with pytest.raises(UserError, match="mémoire invalides"):
        panel.update_server(s["id"], {"ram_max_mb": "beaucoup"})
