"""Locations for editable user data and bundled read-only resources."""

import os
import sys
from pathlib import Path


def resource_path(name: str) -> Path:
    return Path(__file__).resolve().parent / name


def data_dir() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(os.environ.get("APPDATA") or Path.home())
        path = base / "TwitchAIBot"
        path.mkdir(parents=True, exist_ok=True)
        return path
    return Path(__file__).resolve().parent
