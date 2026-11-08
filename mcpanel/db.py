"""SQLite : schéma, migrations, accès thread-safe."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 4

SCHEMA = """
CREATE TABLE users(
  id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
  pw_hash TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE sessions(
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, csrf TEXT NOT NULL,
  created_at REAL NOT NULL, expires_at REAL NOT NULL, ip TEXT);
CREATE TABLE servers(
  id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL COLLATE NOCASE,
  description TEXT NOT NULL DEFAULT '', path TEXT NOT NULL,
  mc_version TEXT NOT NULL, server_type TEXT NOT NULL, loader_version TEXT,
  java_major INTEGER NOT NULL, java_path TEXT, port INTEGER NOT NULL,
  ram_min_mb INTEGER NOT NULL, ram_max_mb INTEGER NOT NULL,
  created_at REAL NOT NULL, status TEXT NOT NULL,
  launch_json TEXT NOT NULL DEFAULT '{}', backup_prefs TEXT NOT NULL DEFAULT '{}',
  desired_state TEXT NOT NULL DEFAULT 'stopped',
  autostart INTEGER NOT NULL DEFAULT 0, auto_restart INTEGER NOT NULL DEFAULT 0,
  eula_accepted INTEGER NOT NULL DEFAULT 0, install_ok INTEGER NOT NULL DEFAULT 0,
  jvm_extra TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '');
CREATE TABLE jobs(
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, server_id TEXT, status TEXT NOT NULL,
  step TEXT NOT NULL DEFAULT '', progress REAL NOT NULL DEFAULT 0,
  log TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '', result TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE backups(
  id TEXT PRIMARY KEY, server_id TEXT, server_name TEXT NOT NULL, filename TEXT NOT NULL,
  size INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, kind TEXT NOT NULL,
  protected INTEGER NOT NULL DEFAULT 0);
CREATE TABLE audit_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, user TEXT, ip TEXT,
  action TEXT NOT NULL, target TEXT, detail TEXT);
CREATE TABLE alerts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL, level TEXT NOT NULL,
  message TEXT NOT NULL, created_at REAL NOT NULL, cleared_at REAL, ack INTEGER NOT NULL DEFAULT 0);
CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX idx_alerts_key ON alerts(key, cleared_at);
CREATE INDEX idx_backups_server ON backups(server_id, created_at);
"""

MIGRATION_4 = """
ALTER TABLE servers ADD COLUMN pending_config TEXT NOT NULL DEFAULT '{}';
ALTER TABLE servers ADD COLUMN notify_prefs TEXT NOT NULL DEFAULT '{}';
ALTER TABLE servers ADD COLUMN last_started REAL;
ALTER TABLE webhooks ADD COLUMN server_ids TEXT NOT NULL DEFAULT '[]';
ALTER TABLE webhooks ADD COLUMN cooldown_s INTEGER NOT NULL DEFAULT 60;
CREATE TABLE crash_reports(
  id INTEGER PRIMARY KEY AUTOINCREMENT, server_id TEXT NOT NULL REFERENCES servers(id) ON DELETE CASCADE, server_name TEXT NOT NULL,
  ts REAL NOT NULL, kind TEXT NOT NULL, analysis TEXT NOT NULL DEFAULT '{}', console TEXT NOT NULL DEFAULT '',
  report_name TEXT, report TEXT);
CREATE INDEX idx_crash_server ON crash_reports(server_id, id);
CREATE TABLE metrics(
  server_id TEXT NOT NULL REFERENCES servers(id) ON DELETE CASCADE, ts REAL NOT NULL,
  tps REAL, mspt REAL, rss_mb REAL, cpu REAL, players INTEGER);
CREATE INDEX idx_metrics ON metrics(server_id, ts);
CREATE TABLE play_sessions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, server_id TEXT NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
  player TEXT NOT NULL, joined REAL NOT NULL, left_at REAL);
CREATE INDEX idx_play_sessions ON play_sessions(server_id, player);
CREATE TABLE installed_mods(
  server_id TEXT NOT NULL REFERENCES servers(id) ON DELETE CASCADE, filename TEXT NOT NULL, folder TEXT NOT NULL DEFAULT 'mods',
  source TEXT NOT NULL, project_id TEXT NOT NULL, version_id TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', version_name TEXT NOT NULL DEFAULT '',
  sha1 TEXT, installed_at REAL NOT NULL, PRIMARY KEY(server_id, folder, filename));
"""

MIGRATION_3 = """
ALTER TABLE servers ADD COLUMN display_name TEXT NOT NULL DEFAULT '';
UPDATE servers SET display_name=name;
CREATE TABLE props_history(
  id INTEGER PRIMARY KEY AUTOINCREMENT, server_id TEXT NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
  ts REAL NOT NULL, username TEXT NOT NULL DEFAULT '', content TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '{}');
CREATE INDEX idx_props_history ON props_history(server_id, id);
"""

MIGRATION_2 = """
ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'admin';
ALTER TABLE users ADD COLUMN active INTEGER NOT NULL DEFAULT 1;
ALTER TABLE users ADD COLUMN display_name TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN last_login REAL;
CREATE TABLE user_servers(
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  server_id TEXT NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
  PRIMARY KEY(user_id, server_id));
CREATE TABLE api_tokens(
  id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL, prefix TEXT NOT NULL, scopes TEXT NOT NULL DEFAULT '[]',
  created_at REAL NOT NULL, expires_at REAL, last_used REAL, last_ip TEXT, revoked_at REAL);
CREATE TABLE events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, type TEXT NOT NULL,
  server_id TEXT, server_name TEXT, data TEXT NOT NULL DEFAULT '{}');
CREATE INDEX idx_events_ts ON events(ts);
CREATE TABLE webhooks(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL, kinds TEXT NOT NULL DEFAULT '[]',
  enabled INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL, last_status TEXT NOT NULL DEFAULT '', last_at REAL);
CREATE TABLE start_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, user_id INTEGER, server_id TEXT NOT NULL);
CREATE INDEX idx_start_log ON start_log(server_id, ts);
"""

JSON_COLS = ("pending_config", "notify_prefs", "server_ids", "launch_json", "backup_prefs", "result", "scopes", "kinds", "data")


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self._migrate()
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def _migrate(self):
        with self.lock:
            ver = self.conn.execute("PRAGMA user_version").fetchone()[0]
            if ver == 0:
                self.conn.executescript("BEGIN;" + SCHEMA + "PRAGMA user_version=1;COMMIT;")
                ver = 1
            for target, mig in ((2, MIGRATION_2), (3, MIGRATION_3), (4, MIGRATION_4)):
                if ver >= target:
                    continue
                if self.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] or self.conn.execute("SELECT COUNT(*) FROM servers").fetchone()[0]:
                    out = sqlite3.connect(str(self.path.with_name(self.path.name + f".avant-v{target}")))   # copie de sécurité avant migration
                    try:
                        self.conn.backup(out)
                    finally:
                        out.close()
                self.conn.executescript("BEGIN;" + mig + f"PRAGMA user_version={target};COMMIT;")
                ver = target

    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self.lock:
            cur = self.conn.execute(sql, tuple(params))
            return cur.lastrowid

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self.lock:
            return [self._row(r) for r in self.conn.execute(sql, tuple(params)).fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    @staticmethod
    def _row(r: sqlite3.Row) -> dict:
        d = dict(r)
        for c in JSON_COLS:
            if c in d and isinstance(d[c], str):
                try:
                    d[c] = json.loads(d[c] or "{}")
                except ValueError:
                    d[c] = {}
        return d

    def backup_to(self, dest: Path):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self.lock:
            out = sqlite3.connect(str(dest))
            try:
                self.conn.backup(out)
            finally:
                out.close()

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        r = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return r["value"] if r else default

    def set_setting(self, key: str, value: str):
        self.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def close(self):
        with self.lock:
            self.conn.close()
