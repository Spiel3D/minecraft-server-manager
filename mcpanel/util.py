"""Petites fonctions utilitaires."""
from __future__ import annotations

import os
import re
import time
import uuid
from pathlib import Path


def now() -> float:
    return time.time()


def new_id() -> str:
    return uuid.uuid4().hex


def human_bytes(n: float) -> str:
    n = float(n)
    for unit in ("o", "Kio", "Mio", "Gio", "Tio"):
        if n < 1024 or unit == "Tio":
            return f"{n:.0f} {unit}" if unit == "o" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} Tio"


def meminfo() -> dict:
    """Valeurs de /proc/meminfo en Mio."""
    out = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                k, _, v = line.partition(":")
                out[k] = int(v.split()[0]) // 1024
    except OSError:
        pass
    return out


def is_mountpoint(path: Path) -> bool:
    return os.path.ismount(str(path))


def natural_key(s: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def clean_text(s: str, limit: int = 4000) -> str:
    return "".join(ch for ch in str(s) if ch == "\n" or ch >= " ")[:limit]
