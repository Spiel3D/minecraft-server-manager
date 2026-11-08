"""Configuration du panneau : variables d'environnement MCPANEL_*, valeurs par défaut sûres."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PRIVATE_NETS = (
    "127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12",
    "192.168.0.0/16", "fc00::/7", "fe80::/10",
)


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "oui", "on")


@dataclass(frozen=True)
class Settings:
    data_root: Path = Path("/srv/minecraft-data")
    state_dir: Path = Path("/var/lib/minecraft-panel")
    run_dir: Path = Path("/run/minecraft-panel")
    frontend_dir: Path = Path(__file__).resolve().parent.parent / "frontend"
    host: str = "0.0.0.0"
    port: int = 8080
    allowed_nets: tuple = PRIVATE_NETS
    max_upload_mb: int = 4096
    ram_reserve_mb: int = 2048
    ram_overhead_pct: int = 20
    require_mount: bool = True
    ssd_tier: bool = True           # False = machine à disque unique : pas de niveau « SSD » (mondes toujours dans data_root)
    session_hours: int = 12
    cookie_secure: bool = False
    min_password_len: int = 12

    @property
    def servers_dir(self) -> Path:
        return self.data_root / "servers"

    @property
    def backups_dir(self) -> Path:
        return self.data_root / "backups"

    @property
    def uploads_dir(self) -> Path:
        return self.data_root / "uploads"

    @property
    def temp_dir(self) -> Path:
        return self.data_root / "temporary"

    @property
    def db_path(self) -> Path:
        return self.state_dir / "panel.db"

    @property
    def java_dir(self) -> Path:
        return self.state_dir / "java"

    @property
    def jobs_dir(self) -> Path:
        return self.state_dir / "jobs"

    @property
    def home_dir(self) -> Path:
        return self.state_dir / "home"

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get
        nets = e("MCPANEL_ALLOWED_NETS")
        return cls(
            data_root=Path(e("MCPANEL_DATA_ROOT", "/srv/minecraft-data")),
            state_dir=Path(e("MCPANEL_STATE_DIR", "/var/lib/minecraft-panel")),
            run_dir=Path(e("MCPANEL_RUN_DIR", "/run/minecraft-panel")),
            host=e("MCPANEL_HOST", "0.0.0.0"),
            port=_int("MCPANEL_PORT", 8080),
            allowed_nets=tuple(x.strip() for x in nets.split(",") if x.strip()) if nets else PRIVATE_NETS,
            max_upload_mb=_int("MCPANEL_MAX_UPLOAD_MB", 4096),
            ram_reserve_mb=_int("MCPANEL_RAM_RESERVE_MB", 2048),
            ram_overhead_pct=_int("MCPANEL_RAM_OVERHEAD_PCT", 20),
            require_mount=_bool("MCPANEL_REQUIRE_MOUNT", True),
            ssd_tier=_bool("MCPANEL_SSD_TIER", True),
            session_hours=_int("MCPANEL_SESSION_HOURS", 12),
            cookie_secure=_bool("MCPANEL_COOKIE_SECURE", False),
        )
