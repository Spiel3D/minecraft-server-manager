"""La base d'une installation existante (v1 puis v2) migre sans perte vers la v3, avec copie de sécurité."""
import sqlite3

from mcpanel.db import MIGRATION_2, SCHEMA, Database


def _old_db(path, upto):
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    if upto >= 2:
        c.executescript(MIGRATION_2)
    c.execute("INSERT INTO servers(id,name,description,path,mc_version,server_type,java_major,port,ram_min_mb,ram_max_mb,created_at,status) "
              "VALUES('s1','Survie','d','/x','1.21.1','vanilla',21,25565,512,1024,1,'installed')")
    c.execute(f"PRAGMA user_version={upto}")
    c.commit()
    c.close()


def test_v1_and_v2_migrate_to_latest(tmp_path):
    for start in (1, 2):
        p = tmp_path / f"p{start}.db"
        _old_db(p, start)
        db = Database(p)
        row = db.one("SELECT name, display_name FROM servers")
        assert row == {"name": "Survie", "display_name": "Survie"}                       # rien perdu, nom d'affichage initialisé
        assert db.conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert db.one("SELECT COUNT(*) c FROM props_history")["c"] == 0
        assert (tmp_path / f"p{start}.db.avant-v3").exists()                              # copie de sécurité avant chaque migration
        if start == 1:
            assert (tmp_path / "p1.db.avant-v2").exists()
        db.conn.close()
        Database(p).conn.close()                                                          # ré-ouverture : idempotent
