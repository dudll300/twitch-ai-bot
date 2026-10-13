"""Locations for editable user data and bundled read-only resources."""

import os
import sys
from pathlib import Path


def resource_path(name: str) -> Path:
    return Path(__file__).resolve().parent / name


def data_dir() -> Path:
    if getattr(sys, "frozen", False):
        if sys.platform == "win32":
            base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        elif sys.platform == "darwin":
            base = Path.home() / "Library" / "Application Support"
        else:
            configured = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
            base = configured if configured.is_absolute() else Path.home() / ".config"
        path = base / "TwitchAIBot"
        path.mkdir(parents=True, exist_ok=True)
        return path
    return Path(__file__).resolve().parent
