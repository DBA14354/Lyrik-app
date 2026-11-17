from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


def config_path() -> Path:
    return Path.home() / ".config" / "lyrik" / "config.json"


def cache_dir() -> Path:
    path = Path.home() / ".cache" / "lyrik"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class Settings:
    compact: bool = False
    click_through: bool = False
    opacity: float = 0.92
    width: int = 420
    height: int = 560
    prefer_spotify: bool = True
    follow_playback: bool = True
    show_album_art: bool = True

    def clamped(self) -> Settings:
        self.opacity = min(1.0, max(0.35, float(self.opacity)))
        self.width = min(900, max(280, int(self.width)))
        self.height = min(1200, max(120, int(self.height)))
        return self


def load_settings() -> Settings:
    path = config_path()
    if not path.exists():
        return Settings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {field.name for field in Settings.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return Settings(**filtered).clamped()
    except (OSError, json.JSONDecodeError, TypeError):
        return Settings()


def save_settings(settings: Settings) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(settings.clamped()), indent=2), encoding="utf-8")
