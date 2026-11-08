"""Outils en ligne de commande : init-db, create-admin, backup-db."""
from __future__ import annotations

import argparse
import getpass
import sys
import time

from .config import Settings
from .db import Database
from .security import SecurityError, check_password_strength, hash_password
from .util import now


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mcpanel")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    ca = sub.add_parser("create-admin")
    ca.add_argument("--username", default="admin")
    ca.add_argument("--password-stdin", action="store_true")
    ca.add_argument("--reset", action="store_true", help="remplace le mot de passe d'un compte existant")
    bd = sub.add_parser("backup-db")
    bd.add_argument("dest")
    sub.add_parser("serve")
    sub.add_parser("stop-all", help="arrête proprement tous les serveurs Minecraft actifs")
    sub.add_parser("running", help="liste les serveurs Minecraft actifs")
    args = ap.parse_args(argv)
    s = Settings.from_env()
    s.state_dir.mkdir(parents=True, exist_ok=True)

    if args.cmd == "serve":
        import uvicorn
        uvicorn.run("mcpanel.app:create_app", factory=True, host=s.host, port=s.port, log_level="info",
                    proxy_headers=False, server_header=False)
        return 0
    if args.cmd in ("stop-all", "running"):
        from .core import Panel
        p = Panel(s)
        live = [r for r in p.db.query("SELECT id, name FROM servers") if p.manager.alive(r["id"])]
        if args.cmd == "running":
            for r in live:
                print(r["name"])
            return 0 if not live else 3
        for r in live:
            print(f"Arrêt de {r['name']}…")
        p.manager.stop_all(120)
        left = [r["name"] for r in live if p.manager.alive(r["id"])]
        if left:
            print("Encore actifs : " + ", ".join(left), file=sys.stderr)
            return 1
        return 0
    db = Database(s.db_path)
    if args.cmd == "init-db":
        print(f"Base prête : {s.db_path}")
        return 0
    if args.cmd == "backup-db":
        from pathlib import Path
        db.backup_to(Path(args.dest))
        print(f"Base sauvegardée : {args.dest}")
        return 0
    if args.cmd == "create-admin":
        existing = db.one("SELECT id FROM users WHERE username=?", (args.username,))
        if existing and not args.reset:
            print("Ce compte existe déjà (utilisez --reset pour changer son mot de passe).", file=sys.stderr)
            return 2
        if args.password_stdin:
            pw = sys.stdin.readline().rstrip("\n")
        else:
            pw = getpass.getpass("Mot de passe : ")
            if pw != getpass.getpass("Confirmer : "):
                print("Les mots de passe diffèrent.", file=sys.stderr)
                return 2
        try:
            check_password_strength(pw, args.username, s.min_password_len)
        except SecurityError as e:
            print(str(e), file=sys.stderr)
            return 2
        h = hash_password(pw)
        if existing:
            db.execute("UPDATE users SET pw_hash=? WHERE id=?", (h, existing["id"]))
            db.execute("DELETE FROM sessions WHERE user_id=?", (existing["id"],))
        else:
            db.execute("INSERT INTO users(username,pw_hash,created_at) VALUES(?,?,?)", (args.username, h, now()))
        print("Compte enregistré.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
