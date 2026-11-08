"""Faux serveur Minecraft pour les tests : console, save-all, ping de liste de serveurs, plantage simulé."""
import os, socket, struct, sys, threading, time, json

port = 25565
try:
    for ln in open("server.properties"):
        if ln.startswith("server-port="):
            port = int(ln.split("=", 1)[1])
except OSError:
    pass


def varint(n):
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def serve():
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", port))
    s.listen(5)
    while True:
        c, _ = s.accept()
        try:
            c.settimeout(2)
            c.recv(4096)
            body = json.dumps({"version": {"name": "fake"}, "players": {"online": 2, "max": 20, "sample": [{"name": "Alex", "id": "x"}, {"name": "Steve", "id": "y"}]}, "description": {"text": "Faux serveur"}}).encode()
            pkt = b"\x00" + varint(len(body)) + body
            c.sendall(varint(len(pkt)) + pkt)
        except OSError:
            pass
        finally:
            c.close()


if "--no-ping" not in sys.argv and not os.path.exists("no-ping"):
    threading.Thread(target=serve, daemon=True).start()
if os.path.exists("slow-start"):
    time.sleep(3)
if os.path.exists("start-fail"):
    print("[main/ERROR]: Failed to start the minecraft server", flush=True)
    print("java.lang.IllegalStateException: Mod file badmod.jar is invalid", flush=True)
    print("-- Mod loading issue for: badmod --", flush=True)
    sys.exit(1)
print("[Server thread/INFO]: Starting minecraft server", flush=True)
print("[Server thread/INFO]: Done (0.5s)! For help, type \"help\"", flush=True)
for line in sys.stdin:
    cmd = line.strip()
    print(f"[Server thread/INFO]: command: {cmd}", flush=True)
    if cmd == "stop":
        print("[Server thread/INFO]: Stopping server", flush=True)
        sys.exit(0)
    elif cmd.startswith("save-all"):
        open("world-saved.txt", "w").write(str(time.time()))
        print("[Server thread/INFO]: Saved the game", flush=True)
    elif cmd.startswith("whitelist "):
        import hashlib, uuid as _u
        parts = cmd.split()
        def _wl():
            try:
                return json.load(open("whitelist.json"))
            except (OSError, ValueError):
                return []
        def _props(key, val):
            lines = []
            try:
                lines = open("server.properties").read().splitlines()
            except OSError:
                pass
            lines = [l for l in lines if not l.startswith(key + "=")] + [f"{key}={val}"]
            open("server.properties", "w").write("\n".join(lines) + "\n")
        if parts[1] == "add" and len(parts) > 2 and not parts[2].startswith("Inexistant"):
            h = bytearray(hashlib.md5(("OfflinePlayer:" + parts[2]).encode()).digest()); h[6] = (h[6] & 15) | 48; h[8] = (h[8] & 63) | 128
            wl = _wl()
            if not any(e["name"].lower() == parts[2].lower() for e in wl):
                wl.append({"uuid": str(_u.UUID(bytes=bytes(h))), "name": parts[2]})
                json.dump(wl, open("whitelist.json", "w"))
            print(f"[Server thread/INFO]: Added {parts[2]} to the whitelist", flush=True)
        elif parts[1] == "add":
            print("[Server thread/INFO]: That player does not exist", flush=True)
        elif parts[1] == "remove" and len(parts) > 2:
            json.dump([e for e in _wl() if e["name"].lower() != parts[2].lower()], open("whitelist.json", "w"))
            print(f"[Server thread/INFO]: Removed {parts[2]} from the whitelist", flush=True)
        elif parts[1] in ("on", "off"):
            _props("white-list", "true" if parts[1] == "on" else "false")
            print(f"[Server thread/INFO]: Whitelist is now turned {parts[1]}", flush=True)
    elif cmd == "crash":
        print("[Server thread/ERROR]: Exception in server tick loop", flush=True)
        os.makedirs("crash-reports", exist_ok=True)
        open(time.strftime("crash-reports/crash-%Y-%m-%d_%H.%M.%S-server.txt"), "w").write(
            "---- Minecraft Crash Report ----\nDescription: Exception in server tick loop\n\n"
            "java.lang.NullPointerException: Cannot invoke \"Entity.tick()\" because \"e\" is null\n\tat com.example.coolmod.Ticker.run(Ticker.java:42)\n"
            "Caused by: java.lang.IllegalArgumentException: bad block state\n\nSuspected Mods: Cool Mod (coolmod)\n")
        os._exit(1)
    elif cmd == "tps":
        print("[Server thread/INFO]: TPS from last 1m, 5m, 15m: *20.0, 19.5, 19.9", flush=True)
    elif cmd == "mspt":
        print("[Server thread/INFO]: Server tick times (avg/min/max) from last 5s, 10s, 1m:", flush=True)
        print("[Server thread/INFO]: ◴ 12.5/3.1/40.2, 11.0/2.0/45.0, 10.0/2.0/50.0", flush=True)
    elif cmd in ("forge tps", "neoforge tps"):
        print("[Server thread/INFO]: minecraft:overworld: Mean tick time: 6.100 ms. Mean TPS: 20.000", flush=True)
        print("[Server thread/INFO]: Overall: Mean tick time: 62.500 ms. Mean TPS: 16.000", flush=True)
    elif cmd == "tick query":
        print("[Server thread/INFO]: The game is running normally", flush=True)
        print("[Server thread/INFO]: Target tick rate: 20.0 per second.", flush=True)
        print("[Server thread/INFO]: Average time per tick: 7.5ms (Target: 50.0ms)", flush=True)
    elif cmd.startswith("join ") or cmd.startswith("leave "):
        who = cmd.split()[1]
        print(f"[Server thread/INFO]: {who} {'joined' if cmd.startswith('join') else 'left'} the game", flush=True)
    elif cmd == "crash-silent":
        os._exit(9)
    elif cmd == "spam-error":
        for i in range(5):
            print(f"[Server thread/ERROR]: Something broke {i}", flush=True)
    elif cmd == "hang":
        while True:
            time.sleep(1)
