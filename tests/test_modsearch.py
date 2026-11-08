"""V4 §3 : recherche Modrinth / CurseForge, relations, plan de dépendances, installation vérifiée, annulation, mises à jour."""
import hashlib
import io
import json
import urllib.parse
import zipfile

import pytest

from conftest import free_port
from test_api import auth, client  # noqa: F401
from mcpanel.jobs import UserError


def jar(mod_id, deps=None):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("fabric.mod.json", json.dumps({"schemaVersion": 1, "id": mod_id, "version": "1.0", "depends": deps or {}}))
    return b.getvalue()


FILES = {"a1": jar("moda", {"modb": "*"}), "a2": jar("moda", {"modb": "*"}), "b1": jar("modb"), "c1": jar("modc")}
PROJECTS = {"AAA": ("moda", "Mod A"), "BBB": ("modb", "Mod B"), "CCC": ("modc", "Client Mod")}
VERSIONS = {  # id -> (projet, date, deps, fichier)
    "a1": ("AAA", "2024-01-01", [{"project_id": "BBB", "version_id": None, "dependency_type": "required"}, {"project_id": "CCC", "version_id": None, "dependency_type": "optional"}], "moda-1.0.jar"),
    "a2": ("AAA", "2024-06-01", [{"project_id": "BBB", "version_id": None, "dependency_type": "required"}], "moda-2.0.jar"),
    "b1": ("BBB", "2024-01-01", [], "modb-1.0.jar"),
    "c1": ("CCC", "2024-01-01", [{"project_id": "AAA", "version_id": None, "dependency_type": "incompatible"}], "modc-1.0.jar"),
}


def mr_version(vid):
    pid, date, deps, fn = VERSIONS[vid]
    data = FILES[vid]
    return {"id": vid, "project_id": pid, "name": fn[:-4], "version_number": fn[:-4], "version_type": "release", "date_published": date, "game_versions": ["1.21.1"],
            "loaders": ["fabric"], "dependencies": deps,
            "files": [{"url": f"https://cdn.modrinth.com/data/{pid}/{fn}", "filename": fn, "primary": True, "size": len(data),
                       "hashes": {"sha512": hashlib.sha512(data).hexdigest(), "sha1": hashlib.sha1(data).hexdigest()}}]}


def modrinth(url, data):
    u = urllib.parse.urlparse(url)
    q = urllib.parse.parse_qs(u.query)
    if u.netloc == "cdn.modrinth.com":
        fn = u.path.rsplit("/", 1)[-1]
        return next(FILES[v] for v, x in VERSIONS.items() if x[3] == fn)
    if u.netloc != "api.modrinth.com":
        return None
    p = u.path[len("/v2"):]
    if p == "/search":
        facets = json.loads(q["facets"][0])
        assert ["versions:1.21.1"] in facets and any("categories:fabric" in f for f in facets)
        hits = [{"project_id": k, "slug": v[0], "title": v[1], "author": "moi", "description": "desc", "downloads": 10, "project_type": "mod", "categories": ["fabric"]}
                for k, v in PROJECTS.items() if q.get("query", [""])[0].lower() in v[1].lower()]
        return {"hits": hits, "total_hits": len(hits)}
    if p == "/tag/category":
        return [{"name": "optimization", "project_type": "mod", "header": "categories"}, {"name": "worldgen", "project_type": "mod", "header": "categories"}]
    if p.startswith("/project/") and p.endswith("/version"):
        pid = p.split("/")[2]
        return [mr_version(v) for v, x in VERSIONS.items() if x[0] == pid]
    if p.startswith("/project/") and p.endswith("/members"):
        return [{"role": "Owner", "user": {"username": "auteur"}}]
    if p.startswith("/project/"):
        pid = p.split("/")[2]
        side = "unsupported" if pid == "CCC" else "required"
        return {"id": pid, "slug": PROJECTS[pid][0], "title": PROJECTS[pid][1], "description": "d", "body": "Corps", "project_type": "mod", "game_versions": ["1.21.1"],
                "loaders": ["fabric"], "server_side": side, "client_side": "required"}
    if p == "/projects":
        ids = json.loads(q["ids"][0])
        return [{"id": i, "title": PROJECTS[i][1]} for i in ids]
    if p.startswith("/version/"):
        return mr_version(p.split("/")[2])
    return None


@pytest.fixture
def fsrv(panel, make_server, routes):
    routes["__fallback__"] = modrinth
    return make_server(name="Modé", stype="fabric", loader="0.16.0")


def test_search_and_relations_are_clickable_data(panel, fsrv):
    r = panel.modsearch.search(fsrv, "modrinth", "mod")
    assert r["total"] == 3 and r["target"]["folder"] == "mods"
    p = panel.modsearch.project(fsrv, "modrinth", "AAA")
    assert p["info"]["title"] == "Mod A" and p["info"]["author"] == "auteur"
    assert p["versions"][0]["id"] == "a2"                                          # plus récente d'abord
    rel = {x["id"]: x for x in p["relations"]}
    assert rel["BBB"]["type"] == "required" and rel["BBB"]["title"] == "Mod B" and rel["BBB"]["installed"] is False
    assert "optimization" in panel.modsearch.categories("modrinth")


def test_plan_resolves_dependencies(panel, fsrv):
    plan = panel.modsearch.plan(fsrv, "modrinth", "a1")
    names = {i["filename"]: i for i in plan["items"]}
    assert set(names) == {"moda-1.0.jar", "modb-1.0.jar"} and "dépendance" in names["modb-1.0.jar"]["reason"]
    assert not plan["conflicts"]


def test_install_verified_and_detects_installed(panel, fsrv):
    j = panel.jobs.wait(panel.jobs.submit("mod-install", fsrv["id"], panel.modsearch.install, fsrv, "modrinth", "a1"), 30)
    assert j["status"] == "done", j["error"] + j["log"]
    d = panel.s.servers_dir / fsrv["name"] / "mods"
    assert (d / "moda-1.0.jar").read_bytes() == FILES["a1"] and (d / "modb-1.0.jar").exists()
    assert "vérifié (SHA512)" in j["log"]
    scan = __import__("mcpanel.mods", fromlist=["x"]).scan_mods(d.parent, "fabric", "1.21.1")
    assert not [i for m in scan["mods"] for i in m["issues"] if i["level"] == "error"]    # dépendance satisfaite
    p = panel.modsearch.project(fsrv, "modrinth", "AAA")
    assert p["installed"] and {x["id"]: x for x in p["relations"]}["BBB"]["installed"]
    plan = panel.modsearch.plan(fsrv, "modrinth", "c1")
    assert plan["conflicts"] and "incompatible" in plan["conflicts"][0]                  # mod déclaré incompatible : signalé
    ups = panel.modsearch.updates(fsrv)
    assert [(u["filename"], u["latest_id"]) for u in ups] == [("moda-1.0.jar", "a2")]
    j = panel.jobs.wait(panel.jobs.submit("mod-install", fsrv["id"], panel.modsearch.install, fsrv, "modrinth", "a2", None, None, "moda-1.0.jar"), 30)
    assert j["status"] == "done", j["error"]
    assert (d / "moda-2.0.jar").exists() and not (d / "moda-1.0.jar").exists()
    assert list((panel.s.state_dir / "mods-remplaces" / fsrv["id"]).glob("*moda-1.0.jar"))       # ancienne version récupérable


def test_corrupted_download_refused(panel, fsrv, routes):
    routes["https://cdn.modrinth.com/data/BBB/modb-1.0.jar"] = b"altere"          # le CDN renvoie un fichier différent de l'empreinte publiée
    j = panel.jobs.wait(panel.jobs.submit("mod-install", fsrv["id"], panel.modsearch.install, fsrv, "modrinth", "b1"), 30)
    assert j["status"] == "error" and "Somme de contrôle" in j["error"]
    assert not list((panel.s.servers_dir / fsrv["name"] / "mods").glob("modb*"))


def test_cancel(panel, fsrv):
    jid = panel.jobs.submit("mod-install", fsrv["id"], lambda job: [job.check_cancel(), __import__("time").sleep(0.2), job.check_cancel()])
    panel.jobs.cancel(jid)
    j = panel.jobs.wait(jid, 10)
    assert j["status"] == "error" and "Annulé" in j["error"]


def test_curseforge_requires_key_and_respects_distribution(panel, fsrv, routes):
    with pytest.raises(UserError, match="clé"):
        panel.modsearch.search(fsrv, "curseforge", "x")
    panel.db.set_setting("curseforge_key", "k")
    base = "https://api.curseforge.com/v1"
    routes[f"{base}/mods/77/files/88"] = {"data": {"id": 88, "displayName": "Interdit 1.0", "fileName": "interdit-1.0.jar", "releaseType": 1, "downloadUrl": None,
                                                    "hashes": [], "gameVersions": ["1.21.1", "Fabric"], "dependencies": [], "fileLength": 5}}
    j = panel.jobs.wait(panel.jobs.submit("mod-install", fsrv["id"], panel.modsearch.install, fsrv, "curseforge", "88", "77"), 30)
    assert j["status"] == "done" and j["result"]["manual"] and not j["result"]["installed"]
    assert "n'autorise pas" in j["log"]                                                  # jamais téléchargé en contournant l'auteur


def test_vanilla_has_no_mods(panel, make_server):
    v = make_server(name="Vanille")
    with pytest.raises(UserError, match="Vanilla"):
        panel.modsearch.search(v, "modrinth", "x")


def test_missing_dependency_and_client_side_detected(panel, make_server):
    s = make_server(name="Deps", stype="fabric", loader="0.16.0")
    d = panel.s.servers_dir / s["name"] / "mods"
    (d / "moda.jar").write_bytes(jar("moda", {"modb": "*", "fabric-api-base": "*"}))
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("fabric.mod.json", json.dumps({"schemaVersion": 1, "id": "minimap", "environment": "client"}))
    (d / "minimap.jar").write_bytes(b.getvalue())
    from mcpanel.mods import scan_mods
    r = {m["file"]: m for m in scan_mods(d.parent, "fabric", "1.21.1")["mods"]}
    assert any("modb" in i["text"] and "fabric-api-base" in i["text"] for i in r["moda.jar"]["issues"])
    assert any("client" in i["text"] for i in r["minimap.jar"]["issues"])


def test_api_routes(auth, panel, fsrv):
    c = auth
    assert c.get(f"/api/servers/{fsrv['id']}/modsearch?q=Mod").json()["total"] == 3
    assert c.get(f"/api/servers/{fsrv['id']}/modsearch/project?source=modrinth&id=AAA").status_code == 200
    assert c.get(f"/api/servers/{fsrv['id']}/modsearch/plan?source=modrinth&version=a1").json()["items"]
    r = c.post(f"/api/servers/{fsrv['id']}/modsearch/install", json={"source": "modrinth", "version": "b1"})
    assert panel.jobs.wait(r.json()["job"], 30)["status"] == "done"
    assert c.post(f"/api/servers/{fsrv['id']}/modsearch/install", json={"source": "evil", "version": "x"}).status_code == 400
