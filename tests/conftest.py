import hashlib
import io
import json
import os
import socket
import stat
import sys
import tarfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcpanel.config import Settings  # noqa: E402
from mcpanel.core import Panel  # noqa: E402
from mcpanel.sources import Http, SourceError  # noqa: E402

HERE = Path(__file__).resolve().parent
FAIL_FLAG = HERE / ".fail-install"


class FakeResp:
    def __init__(self, data: bytes):
        self._b = io.BytesIO(data)
        self.headers = {"Content-Length": str(len(data))}

    def read(self, n=-1):
        return self._b.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeHttp(Http):
    """Vrai code de téléchargement/validation ; seul le transport réseau est simulé."""

    def __init__(self, routes):
        super().__init__(retries=1)
        self.routes = routes
        self.requests = []

    def _open(self, url, headers=None, data=None):
        if not url.lower().startswith("https://"):
            raise SourceError(f"URL non HTTPS refusée : {url}")
        self.requests.append(url)
        v = self.routes.get(url)
        if v is None and "__fallback__" in self.routes:
            v = self.routes["__fallback__"](url, data)
        if v is None:
            raise SourceError(f"HTTP 404 pour {url}")
        if callable(v):
            v = v(url, data)
        return FakeResp(v if isinstance(v, bytes) else json.dumps(v).encode())


FAKE_JAVA = '''#!{py}
import os, sys, re
args = sys.argv[1:]
here = os.path.dirname(os.path.abspath(__file__))
def major():
    try:
        m = re.search(r'JAVA_VERSION="?(\\d+)', open(os.path.join(here, "..", "release")).read())
        return m.group(1)
    except OSError:
        return "17"
if "-version" in args:
    sys.stderr.write('openjdk version "%s.0.1" 2024-01-01\\n' % major())
    sys.exit(0)
def run_server():
    os.execv("{py}", ["{py}", "{fake_mc}"])
cwd = os.getcwd()
if "-jar" in args:
    jar = args[args.index("-jar") + 1]
    head = open(jar, "rb").read(40).decode("latin1")
    if head.startswith("FORGE-INSTALLER") or head.startswith("NEOFORGE-INSTALLER"):
        if os.path.exists("{fail_flag}"):
            print("boom"); sys.exit(1)
        org = "minecraftforge/forge" if head.startswith("FORGE") else "neoforged/neoforge"
        d = os.path.join(cwd, "libraries", "net", *org.split("/"), "1.0")
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "unix_args.txt"), "w").write("-DlibraryDirectory=libraries\\n")
        open(os.path.join(cwd, "user_jvm_args.txt"), "w").write("# jvm\\n")
        print("Installer finished"); sys.exit(0)
    if head.startswith("FABRIC-INSTALLER"):
        d = args[args.index("-dir") + 1]
        open(os.path.join(d, "fabric-server-launch.jar"), "w").write("FAKE-SERVER\\n")
        open(os.path.join(d, "server.jar"), "w").write("FAKE-SERVER\\n")
        sys.exit(0)
    if head.startswith("QUILT-INSTALLER"):
        d = [a for a in args if a.startswith("--install-dir=")][0].split("=", 1)[1]
        open(os.path.join(d, "quilt-server-launch.jar"), "w").write("FAKE-SERVER\\n")
        open(os.path.join(d, "server.jar"), "w").write("FAKE-SERVER\\n")
        sys.exit(0)
    run_server()
if any(a.startswith("@") for a in args):
    run_server()
sys.exit(2)
'''


def make_jdk_tar(major: int) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        def add(name, data, mode):
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            ti.mode = mode
            tf.addfile(ti, io.BytesIO(data))
        root = f"jdk-{major}.0.1+1-jre"
        add(f"{root}/release", f'JAVA_VERSION="{major}.0.1"\n'.encode(), 0o644)
        script = FAKE_JAVA.format(py=sys.executable, fake_mc=str(HERE / "fake_mc.py"), fail_flag=str(FAIL_FLAG))
        add(f"{root}/bin/java", script.encode(), 0o755)
    return buf.getvalue()


def sha(data: bytes, algo="sha1"):
    return hashlib.new(algo, data).hexdigest()


def build_routes() -> dict:
    r = {}
    versions = {"1.21.1": 21, "1.20.1": 17, "1.12.2": 8}
    manifest = {"versions": [{"id": v, "type": "release", "url": f"https://piston-meta.mojang.com/v1/packages/{v}.json", "releaseTime": "2024-01-01"}
                              for v in versions]
                + [{"id": "24w01a", "type": "snapshot", "url": "https://piston-meta.mojang.com/v1/packages/24w01a.json", "releaseTime": "2024-01-02"}]}
    r["https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"] = manifest
    server_jar = b"FAKE-SERVER\n"
    for v, j in versions.items():
        url = f"https://piston-data.mojang.com/v1/objects/{v}/server.jar"
        r[f"https://piston-meta.mojang.com/v1/packages/{v}.json"] = {
            "downloads": {"server": {"url": url, "sha1": sha(server_jar), "size": len(server_jar)}}, "javaVersion": {"majorVersion": j}}
        r[url] = server_jar
    r["https://piston-meta.mojang.com/v1/packages/24w01a.json"] = {"downloads": {}, "javaVersion": {"majorVersion": 21}}
    # Forge
    fm = "https://maven.minecraftforge.net/net/minecraftforge/forge"
    r[fm + "/maven-metadata.xml"] = ("<metadata><versioning><versions><version>1.20.1-47.2.0</version><version>1.20.1-47.3.0</version>"
                                     "<version>1.12.2-14.23.5.2860</version></versions></versioning></metadata>").encode()
    r["https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"] = {"promos": {"1.20.1-recommended": "47.2.0", "1.20.1-latest": "47.3.0"}}
    fi = b"FORGE-INSTALLER\n"
    for full in ("1.20.1-47.2.0", "1.20.1-47.3.0", "1.12.2-14.23.5.2860"):
        u = f"{fm}/{full}/forge-{full}-installer.jar"
        r[u], r[u + ".sha1"] = fi, sha(fi).encode()
    # NeoForge
    nb = "https://maven.neoforged.net/releases/net/neoforged"
    r[nb + "/neoforge/maven-metadata.xml"] = b"<metadata><versioning><versions><version>21.1.50</version><version>21.1.60</version></versions></versioning></metadata>"
    r[nb + "/forge/maven-metadata.xml"] = b"<metadata><versioning><versions><version>1.20.1-47.1.106</version></versions></versioning></metadata>"
    ni = b"NEOFORGE-INSTALLER\n"
    u = f"{nb}/neoforge/21.1.60/neoforge-21.1.60-installer.jar"
    r[u], r[u + ".sha1"] = ni, sha(ni).encode()
    # Fabric / Quilt
    for base, tag, inst in (("https://meta.fabricmc.net/v2/versions", "FABRIC", "https://maven.fabricmc.net/installer.jar"),
                            ("https://meta.quiltmc.org/v3/versions", "QUILT", "https://maven.quiltmc.org/installer.jar")):
        r[base + "/game"] = [{"version": "1.21.1", "stable": True}, {"version": "1.20.1", "stable": True}]
        for mc in ("1.21.1", "1.20.1"):
            r[f"{base}/loader/{mc}"] = [{"loader": {"version": "0.16.0", "stable": True}}, {"loader": {"version": "0.15.0", "stable": True}}]
        r[base + "/installer"] = [{"version": "1.0.1", "stable": True, "url": inst}]
        r[inst] = f"{tag}-INSTALLER\n".encode()
    # Paper (Fill v3) / Purpur (API v2)
    pa = "https://fill.papermc.io/v3/projects/paper"
    r[pa] = {"versions": {"1.21": ["1.21.1"], "1.20": ["1.20.1"]}}
    pj = b"FAKE-SERVER\npaper\n"
    r[pa + "/versions/1.21.1/builds"] = [
        {"id": 12, "channel": "ALPHA", "downloads": {"server:default": {"name": "paper-1.21.1-12.jar", "url": "https://fill-data.papermc.io/v1/objects/b12/paper-1.21.1-12.jar", "checksums": {"sha256": sha(pj, "sha256")}}}},
        {"id": 11, "channel": "STABLE", "downloads": {"server:default": {"name": "paper-1.21.1-11.jar", "url": "https://fill-data.papermc.io/v1/objects/b11/paper-1.21.1-11.jar", "checksums": {"sha256": sha(pj, "sha256")}}}},
        {"id": 10, "channel": "STABLE", "downloads": {"server:default": {"name": "paper-1.21.1-10.jar", "url": "https://fill-data.papermc.io/v1/objects/b10/paper-1.21.1-10.jar", "checksums": {"sha256": "0" * 64}}}}]
    for n in (12, 11, 10):
        r[f"https://fill-data.papermc.io/v1/objects/b{n}/paper-1.21.1-{n}.jar"] = pj
    pu = "https://api.purpurmc.org/v2/purpur"
    pur = b"FAKE-SERVER\npurpur\n"
    r[pu] = {"project": "purpur", "versions": ["1.20.1", "1.21.1"]}
    r[pu + "/1.21.1"] = {"builds": {"latest": "2300", "all": ["2298", "2300"]}}
    r[pu + "/1.21.1/2300"] = {"md5": hashlib.md5(pur).hexdigest(), "result": "SUCCESS"}
    r[pu + "/1.21.1/2300/download"] = pur
    # Adoptium
    for m in (8, 11, 17, 21, 25):
        blob = make_jdk_tar(m)
        link = f"https://github.com/adoptium/temurin{m}-binaries/releases/download/jre-{m}.tar.gz"
        for arch in ("x64", "aarch64"):          # les tests ne dépendent pas de l'architecture de la machine qui les lance
            r[f"https://api.adoptium.net/v3/assets/latest/{m}/hotspot?architecture={arch}&image_type=jre&os=linux&vendor=eclipse"] = [
                {"release_name": f"jdk-{m}", "binary": {"package": {"link": link, "checksum": sha(blob, "sha256"), "name": f"OpenJDK{m}U-jre.tar.gz", "size": len(blob)}}}]
        r[link] = blob
    return r


def free_port() -> int:
    s = socket.socket()
    s.bind(("0.0.0.0", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def wait_for(cond, timeout=20.0, step=0.1):
    end = time.time() + timeout
    while time.time() < end:
        v = cond()
        if v:
            return v
        time.sleep(step)
    raise AssertionError("délai dépassé en attendant la condition")


@pytest.fixture
def routes():
    return build_routes()


@pytest.fixture
def settings(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    return Settings(data_root=data, state_dir=tmp_path / "state", run_dir=tmp_path / "run", require_mount=False,
                    ram_reserve_mb=256, frontend_dir=HERE.parent / "frontend")


@pytest.fixture
def panel(settings, routes):
    p = Panel(settings, FakeHttp(routes))
    p.java.system_dir = settings.state_dir / "no-system-jvm"
    p.ensure_data_dirs()
    p.db.set_setting("ssd_auto", "0")          # les tests de stockage l'activent explicitement
    yield p
    try:
        for srv in p.db.query("SELECT id FROM servers"):
            if p.manager.alive(srv["id"]):
                p.manager.kill(srv["id"])
    finally:
        p.jobs.shutdown()


@pytest.fixture
def make_server(panel):
    def _make(name="Survie", stype="vanilla", mc="1.21.1", loader=None, ram=512, accept_eula=True, expect="done"):
        srv, jid = panel.create_server({"name": name, "server_type": stype, "mc_version": mc, "loader_version": loader,
                                        "ram_max_mb": ram, "ram_min_mb": 256, "port": free_port()})
        j = panel.jobs.wait(jid, 60)
        assert j["status"] == expect, j["log"][-1500:] + j["error"]
        if accept_eula and expect == "done":
            panel.accept_eula(srv["id"])
        return panel.manager.server(srv["id"])
    return _make


def tick_until(panel, cond, timeout=20):
    def f():
        panel.manager.tick()
        return cond()
    return wait_for(f, timeout, 0.2)
