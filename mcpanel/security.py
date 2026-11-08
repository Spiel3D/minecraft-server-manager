"""Sécurité : mots de passe (scrypt), sessions, limitation des tentatives, chemins sûrs, noms valides."""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import os
import re
import secrets
import threading
import time
from pathlib import Path

from .db import Database


class SecurityError(Exception):
    """Erreur de validation de sécurité (message affichable à l'utilisateur)."""


_N, _R, _P = 2 ** 15, 8, 1
COMMON = {"password1234", "123456789012", "azertyuiop12", "motdepasse12", "administrator", "minecraft1234"}


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, maxmem=128 * 1024 * 1024, dklen=32)
    return "scrypt$%d$%d$%d$%s$%s" % (_N, _R, _P, base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, dk = stored.split("$")
        if scheme != "scrypt":
            return False
        calc = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                              maxmem=128 * 1024 * 1024, dklen=len(base64.b64decode(dk)))
        return hmac.compare_digest(calc, base64.b64decode(dk))
    except Exception:
        return False


def check_password_strength(password: str, username: str = "", min_len: int = 12):
    if len(password) < min_len:
        raise SecurityError(f"Le mot de passe doit contenir au moins {min_len} caractères.")
    if password.lower() in COMMON or (username and password.lower() == username.lower()):
        raise SecurityError("Mot de passe trop courant.")
    classes = sum(bool(re.search(p, password)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    if classes < 2 and len(password) < 20:
        raise SecurityError("Mélangez au moins deux types de caractères (lettres, chiffres, symboles) ou allongez la phrase de passe.")


class Sessions:
    def __init__(self, db: Database, hours: int = 12):
        self.db = db
        self.ttl = hours * 3600

    @staticmethod
    def _h(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def create(self, user_id: int, ip: str) -> tuple[str, str]:
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        t = time.time()
        self.db.execute("DELETE FROM sessions WHERE expires_at < ?", (t,))
        self.db.execute("INSERT INTO sessions(token_hash,user_id,csrf,created_at,expires_at,ip) VALUES(?,?,?,?,?,?)",
                        (self._h(token), user_id, csrf, t, t + self.ttl, ip))
        return token, csrf

    def get(self, token: str | None) -> dict | None:
        if not token:
            return None
        row = self.db.one("SELECT s.*, u.username, u.role, u.active, u.display_name FROM sessions s JOIN users u ON u.id=s.user_id WHERE token_hash=?",
                          (self._h(token),))
        if not row or row["expires_at"] < time.time() or not row["active"]:
            return None
        return row

    def delete(self, token: str | None):
        if token:
            self.db.execute("DELETE FROM sessions WHERE token_hash=?", (self._h(token),))

    def delete_user(self, user_id: int, keep_token: str | None = None):
        keep = self._h(keep_token) if keep_token else ""
        self.db.execute("DELETE FROM sessions WHERE user_id=? AND token_hash<>?", (user_id, keep))


class LoginThrottle:
    """Verrouille temporairement une adresse après des échecs répétés (délai exponentiel, plafonné)."""

    def __init__(self, max_free: int = 5, base: int = 30, cap: int = 900):
        self.max_free, self.base, self.cap = max_free, base, cap
        self._d: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def locked_for(self, key: str) -> int:
        with self._lock:
            n, until = self._d.get(key, (0, 0.0))
            return max(0, int(until - time.time()))

    def fail(self, key: str):
        with self._lock:
            n, _ = self._d.get(key, (0, 0.0))
            n += 1
            until = 0.0
            if n >= self.max_free:
                until = time.time() + min(self.cap, self.base * 2 ** (n - self.max_free))
            self._d[key] = (n, until)

    def ok(self, key: str):
        with self._lock:
            self._d.pop(key, None)


def parse_nets(nets) -> list:
    out = []
    for n in nets:
        try:
            out.append(ipaddress.ip_network(n, strict=False))
        except ValueError:
            continue
    return out


def ip_allowed(ip: str | None, nets: list) -> bool:
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if getattr(addr, "ipv4_mapped", None):
        addr = addr.ipv4_mapped
    return any(addr in n for n in nets)


class PathError(SecurityError):
    pass


def safe_join(root: Path, rel: str = "") -> Path:
    """Résout `rel` à l'intérieur de `root` ; refuse toute sortie (.., liens symboliques, NUL, antislash)."""
    rel = rel or ""
    if "\x00" in rel or "\\" in rel:
        raise PathError("Chemin invalide.")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise PathError("Chemin hors du dossier autorisé.")
    root_real = Path(os.path.realpath(root))
    real = Path(os.path.realpath(root_real.joinpath(*parts)))
    if real != root_real and root_real not in real.parents:
        raise PathError("Chemin hors du dossier autorisé.")
    return real


def rel_to(root: Path, p: Path) -> str:
    root_real = Path(os.path.realpath(root))
    r = p.relative_to(root_real).as_posix()
    return "" if r == "." else r


NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,47}$")
RESERVED = {"_panel", "lost+found", "con", "nul", "aux", "prn"}


def validate_server_name(name: str) -> str:
    name = (name or "").strip()
    if not NAME_RE.match(name) or ".." in name or name.endswith("."):
        raise SecurityError("Nom invalide : 1 à 48 caractères parmi lettres, chiffres, « _ », « - » et « . », "
                            "en commençant par une lettre ou un chiffre (pas d'espace).")
    if name.lower() in RESERVED:
        raise SecurityError("Ce nom est réservé.")
    return name


def validate_display_name(name: str) -> str:
    """Nom d'affichage libre (accents, emojis, espaces…) : purement cosmétique, jamais utilisé comme chemin."""
    import unicodedata
    name = unicodedata.normalize("NFC", name if isinstance(name, str) else "").strip()
    if not (1 <= len(name) <= 48):
        raise SecurityError("Nom invalide : 1 à 48 caractères.")
    if any(unicodedata.category(c) in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp") and c != "\u200d" and c != "\ufe0f" for c in name):
        raise SecurityError("Nom invalide : caractères de contrôle ou non affichables interdits.")
    return name


def slugify(display: str) -> str:
    """Dérive un nom de dossier technique sûr (ASCII) depuis un nom d'affichage libre. Peut renvoyer « serveur »."""
    import unicodedata
    txt = unicodedata.normalize("NFKD", display or "").encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^A-Za-z0-9_.-]+", "-", txt).strip("-._")
    s = re.sub(r"\.{2,}", ".", s)[:48].strip("-._")
    if not s or s.lower() in RESERVED:
        s = "serveur"
    return s


def suggest_name(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_.-]+", "-", (text or "").strip()).strip("-.")
    return s[:48]


class RateLimiter:
    """Fenêtre glissante en mémoire : `hit(clé)` renvoie 0 si la requête passe, sinon le nombre de secondes à attendre."""

    def __init__(self):
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window: float) -> int:
        now = time.time()
        with self._lock:
            q = [t for t in self._hits.get(key, ()) if now - t < window]
            if len(q) >= limit:
                self._hits[key] = q
                return max(1, int(window - (now - q[0])) + 1)
            q.append(now)
            self._hits[key] = q
            if len(self._hits) > 5000:      # purge des clés inactives
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] > window]:
                    self._hits.pop(k, None)
            return 0
