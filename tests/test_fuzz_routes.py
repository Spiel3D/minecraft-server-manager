"""Audit : aucune route ne doit répondre 500 à des données invalides (types faux, champs absents, identifiants inconnus)."""
import re

from test_api import auth, client  # noqa: F401

JUNK_BODIES = [{}, {"name": None, "port": "abc", "ram_max_mb": "x", "enabled": "peut-être", "changes": "pas un dict", "id": "z", "jar": "../../x"},
               {"name": "ok", "url": 12, "kinds": "x", "changes": {"motd": ["x"]}, "hash": 5}, {"name": ["liste"], "username": 5, "password": None, "scopes": "x", "kinds": 3, "url": 12, "expires_days": "jamais"}]


def test_no_route_crashes(auth, panel, make_server):
    c = auth
    c._transport.raise_server_exceptions = False
    s = make_server(name="Fuzz")
    app = c.app
    seen500 = []
    for r in app.routes:
        path = getattr(r, "path", "")
        methods = getattr(r, "methods", None) or set()
        if not path.startswith("/api/") or "{" not in path and not methods:
            continue
        for m in methods - {"HEAD", "OPTIONS"}:
            for sid in (s["id"], "inconnu"):
                url = re.sub(r"\{sid\}", sid, path)
                url = re.sub(r"\{[a-z_]+(:path)?\}", "x", url)
                if "/kill" in url or url.endswith("/stop") or url.endswith("/restart") or "auth/logout" in url:
                    continue
                for body in JUNK_BODIES:
                    resp = c.request(m, url + "?q=%00&path=../..&type=zz&mc=zz&xmx=-5&jar=..%2Fx&confirm=true", json=body)
                    if resp.status_code >= 500:
                        seen500.append(f"{m} {url} {body} -> {resp.status_code} {resp.text[:120]}")
    assert not seen500, "\n".join(seen500[:30])
