"""Statistiques Minecraft d'un serveur (V4 §6) : lues dans les fichiers du monde, en lecture seule.

- Par joueur : temps de jeu, morts, créatures tuées, joueurs tués, distance parcourue, blocs minés, objets fabriqués,
  progrès accomplis, dernière connexion connue.
- Global : joueurs uniques, temps de jeu cumulé, pic de joueurs et sessions (journal du panneau).
Formats pris en charge : statistiques 1.13+ (« minecraft:custom ») et anciennes clés (« stat.playOneMinute »).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .whitelist import read_props

MAX_FILE = 4 * 1024 * 1024
DIST_KEYS = ("walk_one_cm", "sprint_one_cm", "crouch_one_cm", "swim_one_cm", "fly_one_cm", "boat_one_cm", "horse_one_cm",
             "minecart_one_cm", "aviate_one_cm", "climb_one_cm", "fall_one_cm", "walk_on_water_one_cm", "walk_under_water_one_cm", "pig_one_cm", "strider_one_cm")


def _load(p: Path):
    try:
        if p.is_symlink() or p.stat().st_size > MAX_FILE:
            return None
        return json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None


def player_stats(d: dict) -> dict:
    st = d.get("stats") if isinstance(d, dict) else None
    out = {"play_time_s": 0, "deaths": 0, "mob_kills": 0, "player_kills": 0, "distance_km": 0.0, "blocks_mined": 0, "items_crafted": 0, "jumps": 0}
    if isinstance(st, dict):
        c = st.get("minecraft:custom") or {}
        g = lambda k: int(c.get("minecraft:" + k, 0) or 0)  # noqa: E731
        out["play_time_s"] = (g("play_time") or g("play_one_minute")) // 20
        out["deaths"], out["mob_kills"], out["player_kills"], out["jumps"] = g("deaths"), g("mob_kills"), g("player_kills"), g("jump")
        out["distance_km"] = round(sum(g(k) for k in DIST_KEYS) / 100000, 2)
        out["blocks_mined"] = sum(int(v or 0) for v in (st.get("minecraft:mined") or {}).values())
        out["items_crafted"] = sum(int(v or 0) for v in (st.get("minecraft:crafted") or {}).values())
    elif isinstance(d, dict):                                  # format d'avant 1.13
        g = lambda k: int(d.get("stat." + k, 0) or 0)  # noqa: E731
        out["play_time_s"] = g("playOneMinute") // 20
        out["deaths"], out["mob_kills"], out["player_kills"], out["jumps"] = g("deaths"), g("mobKills"), g("playerKills"), g("jump")
        out["distance_km"] = round(sum(g(k) for k in ("walkOneCm", "sprintOneCm", "swimOneCm", "flyOneCm", "boatOneCm", "horseOneCm")) / 100000, 2)
        out["blocks_mined"] = sum(int(v or 0) for k, v in d.items() if k.startswith("stat.mineBlock."))
        out["items_crafted"] = sum(int(v or 0) for k, v in d.items() if k.startswith("stat.craftItem."))
    return out


def server_stats(panel, srv: dict) -> dict:
    root = Path(srv["path"])
    level = read_props(root / "server.properties").get("level-name", "world") or "world"
    world = root / level
    if world.resolve().parent != root.resolve():
        world = root / "world"
    cache = {str(e.get("uuid", "")).lower(): e for e in (_load(root / "usercache.json") or []) if isinstance(e, dict)}
    online = set()
    try:
        from .process import mc_status
        if panel.manager.alive(srv["id"]):
            online = {n.lower() for n in (mc_status(srv["port"], timeout=1.0) or {}).get("names", [])}
    except Exception:  # noqa: BLE001
        pass
    sess = {}
    for r in panel.db.query("SELECT player, COUNT(*) n, MAX(joined) last, SUM(COALESCE(left_at, ?) - joined) dur FROM play_sessions WHERE server_id=? GROUP BY player",
                            (time.time(), srv["id"])):
        sess[r["player"].lower()] = r
    players = []
    sdir = world / "stats"
    if sdir.is_dir():
        for f in sorted(sdir.glob("*.json"))[:2000]:
            uuid = f.stem.lower()
            data = _load(f)
            if data is None:
                continue
            ps = player_stats(data)
            adv = _load(world / "advancements" / f.name) or {}
            done = sum(1 for k, v in adv.items() if isinstance(v, dict) and v.get("done") and not k.startswith("minecraft:recipes/"))
            name = (cache.get(uuid) or {}).get("name") or uuid[:8]
            s = sess.get(name.lower()) or {}
            players.append({"uuid": uuid, "name": name, **ps, "advancements": done, "online": name.lower() in online,
                            "sessions": s.get("n", 0), "last_seen": s.get("last") or f.stat().st_mtime})
    players.sort(key=lambda p: p["play_time_s"], reverse=True)
    peak = panel.db.one("SELECT MAX(players) m FROM metrics WHERE server_id=?", (srv["id"],))["m"]
    recent = panel.db.query("SELECT player, joined, left_at FROM play_sessions WHERE server_id=? ORDER BY id DESC LIMIT 20", (srv["id"],))
    tot = lambda k: sum(p[k] for p in players)  # noqa: E731
    return {"world": level, "world_found": sdir.is_dir(),
            "summary": {"players": len(players), "play_time_s": tot("play_time_s"), "deaths": tot("deaths"), "mob_kills": tot("mob_kills"),
                        "blocks_mined": tot("blocks_mined"), "distance_km": round(tot("distance_km"), 1), "peak_players": peak,
                        "sessions": panel.db.one("SELECT COUNT(*) c FROM play_sessions WHERE server_id=?", (srv["id"],))["c"]},
            "players": players, "recent_sessions": recent}
