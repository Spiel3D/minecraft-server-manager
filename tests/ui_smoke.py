"""Test de fumée de l'interface dans un vrai Chromium (sans interface graphique) : connexion, création, démarrage, console.
Usage : python3 tests/ui_smoke.py   (serveur Minecraft factice, réseau simulé)"""
import sys, tempfile, threading, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent)); sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import uvicorn
from playwright.sync_api import sync_playwright
from conftest import FakeHttp, build_routes, free_port
from mcpanel.app import create_app
from mcpanel.config import Settings
from mcpanel.core import Panel
from mcpanel.security import hash_password
from mcpanel.util import now

t = Path(tempfile.mkdtemp()); (t / "data").mkdir()
s = Settings(data_root=t / "data", state_dir=t / "state", run_dir=t / "run", require_mount=False, ram_reserve_mb=256)
p = Panel(s, FakeHttp(build_routes())); p.java.system_dir = t / "nojvm"
p.db.execute("INSERT INTO users(username,pw_hash,created_at) VALUES(?,?,?)", ("admin", hash_password("Un-Mot-De-Passe-Solide-42"), now()))
port = free_port()
srv = uvicorn.Server(uvicorn.Config(create_app(panel=p), host="127.0.0.1", port=port, log_level="warning"))
threading.Thread(target=srv.run, daemon=True).start()
time.sleep(1.5)
errors = []
with sync_playwright() as pw:
    b = pw.chromium.launch(); pg = b.new_page(viewport={"width": 1280, "height": 900})
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" and "401" not in m.text else None)
    pg.on("response", lambda r: print("HTTP", r.status, r.url, r.request.post_data, r.text()[:200]) if r.status >= 400 and "/api/auth/me" not in r.url else None)
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(f"http://127.0.0.1:{port}/")
    pg.fill("#p", "Un-Mot-De-Passe-Solide-42"); pg.click("button[type=submit]")
    pg.wait_for_selector(".nav")
    pg.goto(f"http://127.0.0.1:{port}/#/create")
    pg.wait_for_selector("text=versions compatibles")
    pg.fill("#n", "UiTest"); pg.fill("#port", str(free_port()))
    pg.click("text=INSTALLER LE SERVEUR")
    try:
        pg.wait_for_selector("text=Installation terminée", timeout=20000)
    except Exception:
        print("DIAG", errors, pg.inner_text("#toasts") if pg.query_selector("#toasts") else "-", [ (j["status"], j["error"]) for j in p.db.query("select * from jobs")], [x.text for x in []]); raise
    pg.click("text=Ouvrir le serveur"); pg.wait_for_selector("text=UiTest")
    pg.goto(f"http://127.0.0.1:{port}/#/servers"); pg.wait_for_selector("text=Démarrer")
    pg.click("button[data-act=start]")
    pg.check("#eulachk"); pg.click("text=Accepter et démarrer")
    pg.wait_for_selector("text=En fonctionnement", timeout=40000)
    sid = p.db.one("SELECT id FROM servers")["id"]
    for tab in ("console", "files", "mods", "backups", "config"):
        pg.goto(f"http://127.0.0.1:{port}/#/server/{sid}/{tab}"); time.sleep(0.8)
    pg.goto(f"http://127.0.0.1:{port}/#/server/{sid}/console"); pg.wait_for_selector("text=Done", timeout=10000)
    for r in ("dashboard", "resources", "settings"):
        pg.goto(f"http://127.0.0.1:{port}/#/{r}"); time.sleep(0.8)
    base = f"http://127.0.0.1:{port}"
    shots = Path(sys.argv[1]) if len(sys.argv) > 1 else t
    shots.mkdir(parents=True, exist_ok=True)
    p.db.set_setting("primary_port", str(p.db.one("SELECT port FROM servers")["port"]))      # le serveur de test devient « principal »
    pg.goto(base + "/#/dashboard"); pg.wait_for_selector(".srv"); pg.wait_for_selector("text=★ Principal"); time.sleep(1.2); pg.screenshot(path=str(shots / "01-dashboard.png"))
    pg.goto(base + f"/#/server/{sid}"); pg.wait_for_selector(".tabs"); time.sleep(0.8); pg.screenshot(path=str(shots / "02-serveur.png"))
    pg.goto(base + f"/#/server/{sid}/console"); time.sleep(1.2); pg.screenshot(path=str(shots / "03-console.png"))
    pg.goto(base + f"/#/server/{sid}/whitelist"); pg.wait_for_selector("text=Ajouter un joueur")
    pg.fill("input[placeholder='Pseudo Minecraft']", "Alex"); pg.click("button:has-text('Ajouter')")
    pg.wait_for_selector("td:has-text('Alex')", timeout=10000); pg.wait_for_selector("text=En ligne", timeout=10000)
    pg.screenshot(path=str(shots / "03b-whitelist.png"))
    import math as _m
    t0 = time.time() - 3600
    for i in range(60):
        p.db.execute("INSERT INTO metrics(server_id,ts,tps,mspt,rss_mb,cpu,players) VALUES(?,?,?,?,?,?,?)",
                     (sid, t0 + i * 60, round(19.2 + 0.8 * _m.sin(i / 6), 2), 18 + 14 * abs(_m.sin(i / 9)), 900 + i * 6, 20 + 10 * _m.sin(i / 5), 2 + (i // 20)))
    pg.goto(base + f"/#/server/{sid}/stats"); pg.wait_for_selector("text=Statistiques Minecraft"); pg.wait_for_selector(".chart-svg")
    box = pg.locator(".chart-svg .hit").first.bounding_box(); pg.mouse.move(box["x"] + box["width"] * 0.6, box["y"] + 20); time.sleep(0.4)
    pg.screenshot(path=str(shots / "03d-statistiques.png"), full_page=True)
    pg.goto(base + f"/#/server/{sid}/mcsettings"); pg.wait_for_selector("text=Joueurs maximum")
    pg.fill("input[placeholder='Rechercher un réglage…']", "distance"); time.sleep(0.4)
    pg.fill("#mcs-view-distance input", "12"); pg.wait_for_selector("text=1 modification non enregistrée")
    pg.screenshot(path=str(shots / "03c-parametres-minecraft.png"))
    pg.click(".savebar button.primary"); pg.wait_for_selector("text=Redémarrage nécessaire", timeout=10000)
    assert "view-distance=12" in (Path(p.db.one("SELECT path FROM servers")["path"]) / "server.properties").read_text()
    pg.goto(base + "/#/create"); pg.wait_for_selector("text=versions compatibles"); pg.screenshot(path=str(shots / "04-creer.png"))
    # --- comptes : création d'un utilisateur limité à ce serveur
    pg.goto(base + "/#/users"); pg.wait_for_selector("text=Nouveau compte")
    pg.click("button:has-text('Nouveau compte')")
    pg.fill(".modal input[type=text] >> nth=0", "alice")
    pg.fill(".modal input.mono", "Utilisateur-Solide-77")
    pg.check(".check-grid input[type=checkbox] >> nth=0")
    pg.screenshot(path=str(shots / "05-compte-modal.png"))
    pg.click("text=Créer le compte"); pg.wait_for_selector("td:has-text('alice')"); time.sleep(0.4)
    pg.screenshot(path=str(shots / "06-comptes.png"))
    # --- token d'API
    pg.goto(base + "/#/api"); pg.wait_for_selector("text=Aucun token")
    pg.click("button:has-text('Nouveau token')"); pg.fill(".modal input[type=text]", "Bot de test"); pg.click("text=Créer le token")
    pg.wait_for_selector("text=Votre token"); tok = pg.inner_text(".secret"); assert tok.startswith("mcp_"), tok
    pg.screenshot(path=str(shots / "07-token.png")); pg.click("text=J'ai copié mon token")
    pg.wait_for_selector("td:has-text('Bot de test')")
    import urllib.request, json as _j
    rq = urllib.request.Request(base + "/api/v1/servers", headers={"Authorization": "Bearer " + tok})
    got = _j.loads(urllib.request.urlopen(rq).read()); assert got["ok"] and got["data"][0]["id"] == sid, got
    pg.goto(base + "/#/api/docs"); pg.wait_for_selector(".endpoint"); pg.screenshot(path=str(shots / "08-api-docs.png"))
    pg.goto(base + "/#/notifications"); pg.wait_for_selector("text=Aucune notification configurée"); pg.screenshot(path=str(shots / "09-notifications.png"))
    pg.goto(base + "/#/supervision"); pg.wait_for_selector("text=Processus Minecraft"); time.sleep(1); pg.screenshot(path=str(shots / "10-supervision.png"), full_page=True)
    pg.goto(base + "/#/resources"); time.sleep(1); pg.goto(base + "/#/settings"); pg.wait_for_selector("text=Démarrages par les utilisateurs"); time.sleep(0.5)
    pg.goto(base + f"/#/server/{sid}/files"); pg.wait_for_selector("table"); pg.screenshot(path=str(shots / "11-fichiers.png"))
    # --- vue mobile (administrateur)
    mob = b.new_page(viewport={"width": 390, "height": 800}); mob.on("pageerror", lambda e: errors.append(str(e)))
    mob.goto(base + "/"); mob.fill("#p", "Un-Mot-De-Passe-Solide-42"); mob.click("button[type=submit]"); mob.wait_for_selector(".srv"); time.sleep(1)
    mob.screenshot(path=str(shots / "12-mobile-admin.png")); mob.click(".menu-btn"); time.sleep(0.4); mob.screenshot(path=str(shots / "13-mobile-menu.png")); mob.close()
    # --- session de l'utilisateur classique
    ctx = b.new_context(viewport={"width": 1280, "height": 800}); up = ctx.new_page()
    up.on("pageerror", lambda e: errors.append(str(e)))
    up.on("console", lambda m: errors.append(m.text) if m.type == "error" and "40" not in m.text else None)
    up.goto(base + "/"); up.fill("#u", "alice"); up.fill("#p", "Utilisateur-Solide-77"); up.click("button[type=submit]")
    up.wait_for_selector("[data-act=user-start]", state="attached"); time.sleep(1)
    assert up.locator(".nav a").count() == 3, up.locator(".nav a").all_inner_texts()
    nav = " ".join(up.locator(".nav a").all_inner_texts()); assert "Console" not in nav and "Paramètres" not in nav and "Comptes" not in nav
    assert up.locator("text=UiTest").count() >= 1 and up.locator("text=Console").count() == 0 and up.locator("text=Arrêter").count() == 0
    up.screenshot(path=str(shots / "14-utilisateur.png"))
    up.goto(base + "/#/settings"); time.sleep(0.8); assert up.evaluate("location.hash") == "#/home", (up.url, up.evaluate("[location.hash, state.role, state.route]"))   # redirigé : jamais d'écran admin
    up.goto(base + "/#/api"); up.wait_for_selector("text=Nouveau token"); up.screenshot(path=str(shots / "15-utilisateur-api.png"))
    mobu = ctx.new_page(); mobu.set_viewport_size({"width": 390, "height": 800}); mobu.goto(base + "/#/home"); mobu.wait_for_selector(".srv"); time.sleep(0.8)
    mobu.screenshot(path=str(shots / "16-mobile-utilisateur.png"))
    # --- plantage : notification immédiate + rapport
    pg.bring_to_front(); pg.goto(base + "/#/dashboard"); time.sleep(1)
    p.manager.send_command(sid, "crash")
    pg.wait_for_selector("#toasts button:has-text('Voir le rapport')", timeout=20000)
    pg.screenshot(path=str(shots / "17-notification-plantage.png"))
    pg.click("#toasts button:has-text('Voir le rapport')"); pg.wait_for_selector("text=Constaté dans les fichiers")
    pg.wait_for_selector("text=Cool Mod (coolmod)"); time.sleep(0.8); pg.screenshot(path=str(shots / "18-rapport-plantage.png"))
    pg.keyboard.press("Escape"); pg.click("#bell"); pg.wait_for_selector(".notif.critical"); time.sleep(0.8); pg.screenshot(path=str(shots / "19-centre-notifications.png"))
    # --- stockage : archive du monde puis retour sur le disque dur (serveur arrêté après le plantage)
    pg.keyboard.press("Escape")
    p.db.execute("UPDATE servers SET auto_restart=0 WHERE id=?", (sid,))
    for _ in range(40):
        if not p.manager.alive(sid): break
        time.sleep(0.5)
    if p.manager.alive(sid): p.manager.stop(sid, wait=True)
    wdir = Path(p.manager.server(sid)["path"]) / "world"; (wdir / "region").mkdir(parents=True, exist_ok=True)
    (wdir / "level.dat").write_bytes(b"\x1f\x8b fake"); (wdir / "region" / "r.0.0.mca").write_bytes(b"x" * 5000)
    pg.goto(base + f"/#/server/{sid}"); pg.wait_for_selector("text=Stockage du monde"); time.sleep(0.5)
    pg.click("button:has-text('Archiver le monde…')"); pg.click(".modal button:has-text('Archiver')")
    pg.wait_for_selector("text=Archivage du monde : terminé", timeout=30000)
    assert p.storage.list_archives(sid), "archive absente"
    pg.wait_for_selector("button:has-text('Mettre sur le disque dur')"); pg.click("button:has-text('Mettre sur le disque dur')")
    pg.click(".modal button:has-text('Déplacer')"); pg.wait_for_selector("text=Déplacement vers le disque dur : terminé", timeout=30000); time.sleep(1)
    assert p.storage.tier(p.manager.server(sid)) == "hdd" and (Path(p.manager.server(sid)["path"]) / "world" / "region" / "r.0.0.mca").stat().st_size == 5000
    pg.screenshot(path=str(shots / "20-stockage.png"))
    # --- tableau de bord en deux catégories : un serveur allumé, deux arrêtés
    for nm in ("Créatif 🎨", "Survie d'hiver"):
        s2, j2 = p.create_server({"name": nm, "server_type": "vanilla", "mc_version": "1.21.1", "ram_max_mb": 1024, "ram_min_mb": 512, "port": free_port()})
        assert p.jobs.wait(j2, 60)["status"] == "done"
    p.manager.start(sid)
    pg.goto(base + "/#/dashboard"); pg.wait_for_selector("h2.cat:has-text('En ligne (1)')", timeout=20000); pg.wait_for_selector("h2.cat:has-text('Arrêtés (2)')")
    assert pg.locator(".grid.live .srv").count() == 1 and pg.locator(".srvcats section:nth-child(2) .srv").count() == 2
    wl = pg.locator(".grid.live .srv").first.bounding_box()["width"]; wo = pg.locator(".srvcats section:nth-child(2) .srv").first.bounding_box()["width"]
    assert wl > wo, (wl, wo)                                  # la case allumée est plus grande
    time.sleep(0.8); pg.screenshot(path=str(shots / "01b-dashboard-categories.png"), full_page=True)
    pg.set_viewport_size({"width": 1600, "height": 1000}); time.sleep(0.5); pg.screenshot(path=str(shots / "01c-dashboard-large.png"), full_page=True); pg.set_viewport_size({"width": 1280, "height": 900})
    pg.goto(base + "/#/settings"); pg.wait_for_selector("text=Stockage des mondes"); time.sleep(0.6); pg.screenshot(path=str(shots / "21-parametres.png"), full_page=True)
    b.close()
p.manager.stop_all(20); time.sleep(1)
for sv in p.db.query("SELECT id FROM servers"):
    if p.manager.alive(sv["id"]): p.manager.kill(sv["id"])
print("captures :", shots)
print("ERREURS NAVIGATEUR :", errors if errors else "aucune")
sys.exit(1 if errors else 0)
