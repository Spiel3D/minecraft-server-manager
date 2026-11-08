"""Pipeline d'installation d'un serveur : Java, Minecraft, chargeur, fichiers de lancement, EULA."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from .jobs import Job, UserError
from .sources import SourceError

INSTALL_TIMEOUT = 20 * 60


class InstallError(Exception):
    pass


def run_logged(cmd: list[str], cwd: Path, job: Job, env: dict, timeout: int = INSTALL_TIMEOUT) -> tuple[int, str]:
    job.log("$ " + " ".join(cmd))
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            errors="replace", env=env, start_new_session=True)
    tail: list[str] = []

    def pump():
        assert proc.stdout
        for line in proc.stdout:
            tail.append(line.rstrip())
            del tail[:-80]
            job.log("  " + line.rstrip())

    t = threading.Thread(target=pump, daemon=True)
    t.start()
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, 9)
        except OSError:
            pass
        raise InstallError("L'installateur a dépassé le délai de 20 minutes et a été interrompu.")
    t.join(timeout=5)
    return code, "\n".join(tail)


def set_properties(path: Path, updates: dict[str, str]):
    """Met à jour server.properties en conservant le reste du fichier."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else []
    done = set()
    for i, ln in enumerate(lines):
        if ln and not ln.lstrip().startswith("#") and "=" in ln:
            k = ln.split("=", 1)[0].strip()
            if k in updates:
                lines[i] = f"{k}={updates[k]}"
                done.add(k)
    for k, v in updates.items():
        if k not in done:
            lines.append(f"{k}={v}")
    tmp = path.with_name(path.name + ".panel-tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def write_eula(server_dir: Path, accepted: bool):
    stamp = time.strftime("%a %b %d %H:%M:%S UTC %Y", time.gmtime())
    (server_dir / "eula.txt").write_text(
        "#By changing the setting below to TRUE you are indicating your agreement to our EULA (https://aka.ms/MinecraftEULA).\n"
        f"#{stamp}\neula={'true' if accepted else 'false'}\n", encoding="utf-8")


def detect_launch(server_dir: Path, server_type: str, jar_name: str | None = None) -> list[str]:
    if jar_name:                                  # jar téléchargé (Paper, Purpur…) ou importé (hybrides, Spigot, personnalisé)
        if not (server_dir / jar_name).is_file():
            raise InstallError(f"Fichier de lancement attendu introuvable : {jar_name}")
        return ["-jar", jar_name, "nogui"]
    if server_type == "vanilla":
        need = "server.jar"
        args = ["-jar", "server.jar", "nogui"]
    elif server_type == "fabric":
        need, args = "fabric-server-launch.jar", ["-jar", "fabric-server-launch.jar", "nogui"]
    elif server_type == "quilt":
        need, args = "quilt-server-launch.jar", ["-jar", "quilt-server-launch.jar", "nogui"]
    else:  # forge / neoforge
        found = sorted(server_dir.glob("libraries/net/*/*/*/unix_args.txt"))
        if found:
            rel = found[-1].relative_to(server_dir).as_posix()
            pre = ["@user_jvm_args.txt"] if (server_dir / "user_jvm_args.txt").exists() else []
            return pre + [f"@{rel}", "nogui"]
        jars = sorted(p.name for p in server_dir.glob("forge-*.jar") if "installer" not in p.name and "shim" not in p.name)
        if not jars:
            raise InstallError("Fichiers de lancement introuvables après l'installation du chargeur (voir le journal).")
        return ["-jar", jars[-1], "nogui"]
    if not (server_dir / need).exists():
        raise InstallError(f"Fichier de lancement attendu introuvable : {need}")
    return args


def apply_initial_config(panel, job, srv: dict, d: Path):
    """Écrit server.properties (port, MOTD, réglages choisis à la création) et la whitelist initiale. Ne touche pas aux réglages
    déjà présents s'ils n'ont pas été choisis (réinstallation : rien n'est écrasé)."""
    from .mcsettings import PropsFile
    from .whitelist import offline_uuid
    props = d / "server.properties"
    pf = PropsFile(props.read_text(encoding="utf-8", errors="replace") if props.exists() else "")
    pend = srv.get("pending_config") or {}
    pf.set("server-port", str(srv["port"]))
    if "motd" not in pf.values:
        pf.set("motd", srv.get("display_name") or srv["name"])
    if "enable-rcon" not in pf.values:
        pf.set("enable-rcon", "false")
    for k, v in (pend.get("properties") or {}).items():
        pf.set(k, v)
    wl = pend.get("whitelist") or {}
    if wl:
        pf.set("white-list", "true" if wl.get("enabled") else "false")
    tmp = props.with_name(props.name + ".panel-tmp")
    tmp.write_text(pf.text(), encoding="utf-8")
    os.replace(tmp, props)
    if pend.get("properties"):
        job.log("Réglages Minecraft appliqués : " + ", ".join(sorted(pend["properties"])))
    players = wl.get("players") or []
    if players:
        offline = pf.values.get("online-mode", "true").lower() == "false"
        wfile = d / "whitelist.json"
        try:
            entries = json.loads(wfile.read_text(encoding="utf-8")) if wfile.exists() else []
            entries = entries if isinstance(entries, list) else []
        except ValueError:
            entries = []
        have = {str(e.get("name", "")).lower() for e in entries if isinstance(e, dict)}
        failed = []
        for n in players:
            if n.lower() in have:
                continue
            if offline:
                entries.append({"uuid": offline_uuid(n), "name": n})
                continue
            try:
                uid, canon = panel.whitelist._lookup(n, True)
                entries.append({"uuid": uid, "name": canon})
            except UserError as e:
                failed.append(f"{n} ({e})")
        tmp = wfile.with_name(wfile.name + ".panel-tmp")
        tmp.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, wfile)
        job.log(f"Whitelist initiale : {len(players) - len(failed)} joueur(s) ajouté(s).")
        if failed:
            job.log("À ajouter plus tard depuis l'onglet Whitelist (non vérifiés) : " + "; ".join(failed))
            panel.alerts.raise_(f"wl:{srv['id']}", "warning", f"Whitelist de « {srv.get('display_name') or srv['name']} » : {len(failed)} pseudo(s) non ajouté(s), voir le journal d'installation.")
    panel.db.execute("UPDATE servers SET pending_config='{}' WHERE id=?", (srv["id"],))


def run_install(panel, job: Job, sid: str) -> dict:
    s, db = panel.s, panel.db
    srv = db.one("SELECT * FROM servers WHERE id=?", (sid,))
    if not srv:
        raise UserError("Serveur introuvable.")
    d = Path(srv["path"])
    tmp = s.temp_dir / job.id
    report: dict = {"steps": []}
    try:
        db.execute("UPDATE servers SET status='installing', install_ok=0, error='' WHERE id=?", (sid,))
        job.step("Validation des paramètres", 2)
        res = panel.sources.resolve(srv["server_type"], srv["mc_version"], srv["loader_version"])
        job.log(f"Combinaison valide : Minecraft {srv['mc_version']} / {srv['server_type']} {srv['loader_version'] or ''}")

        job.step("Vérification des prérequis Java", 5)
        required = res["java_major"]
        job.step("Détermination de la version Java", 8)
        job.log(f"Minecraft {srv['mc_version']} demande Java {required} ou plus (source : métadonnées Mojang).")

        job.step("Installation du runtime Java", 10)
        java, major = panel.java.ensure(required, job.log, lambda p: job.progress(10 + 22 * p))
        db.execute("UPDATE servers SET java_path=?, java_major=? WHERE id=?", (str(java), major, sid))
        env = {"PATH": "/usr/bin:/bin", "HOME": str(s.home_dir), "LANG": "C.UTF-8", "JAVA_HOME": str(java.parent.parent)}
        s.home_dir.mkdir(parents=True, exist_ok=True)

        job.step("Création du dossier du serveur", 33)
        d.mkdir(parents=True, exist_ok=True)
        tmp.mkdir(parents=True, exist_ok=True)

        v = res["vanilla"]
        ad = panel.sources.adapters[srv["server_type"]]
        launch = srv.get("launch_json") or {}
        jar_name = None
        job.step("Téléchargement de Minecraft", 35)
        if ad.mode == "import":
            jar_name = launch.get("import_jar") or "server.jar"
            if not (d / jar_name).is_file():
                raise InstallError(f"Jar à importer introuvable : {jar_name}")
            job.log(f"Jar fourni par l'utilisateur : {jar_name} (analysé sans être exécuté).")
        elif ad.mode == "download":
            build = res["loader"]
            jar_name = "server.jar"
            exp = {"sha256": build["sha256"]} if build.get("sha256") else {}
            if srv["server_type"] == "purpur":
                md5 = panel.sources.purpur.md5(srv["mc_version"], build["version"])
                if md5:
                    exp = {"md5": md5}
            if not exp:
                job.log("Aucune somme de contrôle publiée pour ce build : vérification d'intégrité impossible.")
            panel.http.download(build["url"], d / jar_name, expected=exp or None,
                                progress=lambda done, tot: job.progress(35 + 20 * (done / tot if tot else 0)))
            job.log(f"{ad.label} build {build['version']} téléchargé" + (" et vérifié." if exp else "."))
        elif srv["server_type"] == "vanilla":
            panel.http.download(v["server_url"], d / "server.jar", expected={"sha1": v["sha1"]} if v.get("sha1") else None,
                                progress=lambda done, tot: job.progress(35 + 20 * (done / tot if tot else 0)))
            job.log("server.jar téléchargé et vérifié (SHA-1).")
        else:
            job.log("Le serveur Mojang sera téléchargé et vérifié par l'installateur du chargeur.")

        job.step("Installation de Minecraft", 56)
        if srv["server_type"] == "vanilla":
            job.log("Rien d'autre à installer pour Vanilla.")

        job.step("Installation du chargeur de mods", 58)
        if ad.mode == "builtin":
            _install_loader(panel, job, srv, res, d, tmp, java, major, env)
        else:
            job.log("Aucun installateur séparé : le jar est lancé directement.")

        job.step("Génération des fichiers de lancement", 92)
        args = detect_launch(d, srv["server_type"], jar_name)
        db.execute("UPDATE servers SET launch_json=? WHERE id=?", (json.dumps({"args": args}), sid))
        job.log("Commande de lancement : java … " + " ".join(args))

        job.step("Préparation de la configuration", 95)
        props = d / "server.properties"
        if not props.exists():
            props.write_text("", encoding="utf-8")
        apply_initial_config(panel, job, srv, d)
        for sub in ("mods", "plugins", "config", "logs"):
            if (sub == "mods" and ad.mods) or (sub == "plugins" and ad.plugins) or sub in ("config", "logs"):
                (d / sub).mkdir(exist_ok=True)

        job.step("Préparation du fichier EULA", 97)
        write_eula(d, False)
        job.log("eula.txt créé avec eula=false : le serveur ne démarrera qu'après acceptation explicite dans le panneau.")

        job.step("Enregistrement du serveur", 99)
        db.execute("UPDATE servers SET status='installed', install_ok=1, error='' WHERE id=?", (sid,))
        job.step("Installation terminée", 100)
        job.log(f"Compte rendu : {srv['name']} — Minecraft {srv['mc_version']} — {srv['server_type']} "
                f"{srv['loader_version'] or ''} — Java {major} — port {srv['port']} — dossier {d}")
        report.update(java=major, args=args, path=str(d))
        return report
    except Exception as e:
        db.execute("UPDATE servers SET status='error', install_ok=0, error=? WHERE id=?", (str(e)[:500], sid))
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _install_loader(panel, job, srv, res, d: Path, tmp: Path, java: Path, major: int, env: dict):
    t, mc, lv = srv["server_type"], srv["mc_version"], srv["loader_version"]
    if t == "vanilla":
        return
    http = panel.http
    if t in ("forge", "neoforge"):
        if t == "forge":
            inst = panel.sources.forge.installer(res["loader"]["full"])
        else:
            inst = panel.sources.neoforge.installer(res["loader"]["artifact"], res["loader"]["full"])
        jar = tmp / "installer.jar"
        expected = None
        try:
            expected = {"sha1": http.get_text(inst["sha1_url"], cache=False).split()[0]}
        except (SourceError, IndexError):
            job.log("Somme de contrôle SHA-1 non publiée pour cet installateur : vérification ignorée.")
        job.log(f"Téléchargement de l'installateur {t} {lv}…")
        http.download(inst["url"], jar, expected=expected, progress=lambda dn, tt: job.progress(58 + 10 * (dn / tt if tt else 0)))
        job.progress(68)
        flags = ["--installServer"]
        code, tail = run_logged([str(java), "-jar", str(jar), *flags], d, job, env)
        if code != 0 and "nrecognized" in tail:
            job.log("Option refusée : nouvelle tentative avec --install-server.")
            code, tail = run_logged([str(java), "-jar", str(jar), "--install-server"], d, job, env)
        if code != 0:
            raise InstallError(f"L'installateur {t} a échoué (code {code}). Dernières lignes :\n" + "\n".join(tail.splitlines()[-8:]))
    elif t == "fabric":
        inst = panel.sources.fabric.installer()
        jar = tmp / "fabric-installer.jar"
        http.download(inst["url"], jar)
        job.progress(65)
        code, tail = run_logged([str(java), "-jar", str(jar), "server", "-dir", str(d), "-mcversion", mc,
                                 "-loader", lv, "-downloadMinecraft"], d, job, env)
        if code != 0:
            raise InstallError(f"L'installateur Fabric a échoué (code {code}).\n" + "\n".join(tail.splitlines()[-8:]))
    elif t == "quilt":
        inst = panel.sources.quilt.installer()
        jar = tmp / "quilt-installer.jar"
        http.download(inst["url"], jar)
        tool_java = java
        if major < 17:
            tool_java, _ = panel.java.ensure(17, job.log)
        job.progress(65)
        code, tail = run_logged([str(tool_java), "-jar", str(jar), "install", "server", mc, lv,
                                 "--download-server", f"--install-dir={d}"], d, job, env)
        if code != 0:
            raise InstallError(f"L'installateur Quilt a échoué (code {code}).\n" + "\n".join(tail.splitlines()[-8:]))
