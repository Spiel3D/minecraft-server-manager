"""Test de fumée de l'interface en mode disque unique (MCPANEL_SSD_TIER=0) dans un vrai Chromium.
Usage : python3 tests/ui_single_disk.py [dossier-de-captures]"""
import sys, tempfile, threading, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests")); sys.path.insert(0, str(ROOT))
import uvicorn
from playwright.sync_api import sync_playwright
from conftest import FakeHttp, build_routes, free_port
from mcpanel.app import create_app
from mcpanel.config import Settings
from mcpanel.core import Panel
from mcpanel.security import hash_password
from mcpanel.util import now

t = Path(tempfile.mkdtemp()); (t / "data").mkdir()
s = Settings(data_root=t / "data", state_dir=t / "state", run_dir=t / "run", require_mount=False, ram_reserve_mb=256, ssd_tier=False)
p = Panel(s, FakeHttp(build_routes())); p.java.system_dir = t / "nojvm"
p.db.execute("INSERT INTO users(username,pw_hash,created_at) VALUES(?,?,?)", ("admin", hash_password("Un-Mot-De-Passe-Solide-42"), now()))
srv, jid = p.create_server({"name": "Solo", "server_type": "vanilla", "mc_version": "1.21.1", "loader_version": None, "ram_max_mb": 512, "ram_min_mb": 256, "port": free_port()})
assert p.jobs.wait(jid, 60)["status"] == "done"
port = free_port()
server = uvicorn.Server(uvicorn.Config(create_app(panel=p), host="127.0.0.1", port=port, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start(); time.sleep(1.5)
errors = []
shots = Path(sys.argv[1]) if len(sys.argv) > 1 else None
shots and shots.mkdir(parents=True, exist_ok=True)
with sync_playwright() as pw:
    b = pw.chromium.launch(); pg = b.new_page(viewport={"width": 1280, "height": 900})
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" and "401" not in m.text else None)
    pg.goto(f"http://127.0.0.1:{port}/")
    pg.fill("input[type=text]", "admin"); pg.fill("input[type=password]", "Un-Mot-De-Passe-Solide-42"); pg.keyboard.press("Enter")
    pg.wait_for_selector(".srv", timeout=15000)
    pg.goto(f"http://127.0.0.1:{port}/#/server/{srv['id']}"); pg.wait_for_selector("text=Stockage du monde", timeout=15000)
    txt = pg.inner_text("body")
    assert "un seul disque pour les données" in txt, txt[-600:]
    assert "Déplacer sur le SSD" not in txt and "quota" not in txt.lower().split("stockage du monde")[1][:400]
    assert pg.locator("button:has-text('Archiver le monde')").count() == 1
    shots and pg.screenshot(path=str(shots / "single-serveur.png"))
    pg.goto(f"http://127.0.0.1:{port}/#/settings"); pg.wait_for_selector("text=Stockage des mondes", timeout=15000)
    txt = pg.inner_text("body")
    assert "Machine à disque unique" in txt and "Quota SSD" not in txt, txt[:300]
    shots and pg.screenshot(path=str(shots / "single-parametres.png"))
    b.close()
print("ERREURS NAVIGATEUR :", errors or "aucune")
assert not errors
print("OK mode disque unique dans l'interface")
