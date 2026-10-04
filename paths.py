"""Locations for editable user data and bundled read-only resources."""

import os
import sys
from pathlib import Path


def resource_path(name: str) -> Path:
    return Path(__file__).resolve().parent / name


def is_packaged() -> bool:
    return bool(getattr(sys, "frozen", False) or "__compiled__" in globals())


def worker_command(log_path: Path, *, self_test: bool = False) -> tuple[str, list[str]]:
    args = ["--bot", str(log_path)]
    if self_test:
        args.append("--self-test")
    if "__compiled__" in globals():
        # Keep the actual launch filename, including when a portable EXE is renamed.
        return str(Path(sys.argv[0]).resolve()), args
    if not is_packaged():
        args = ["-u", str(resource_path("app.py")), *args]
    return sys.executable, args


def data_dir() -> Path:
    if is_packaged():
        base = Path(os.environ.get("APPDATA") or Path.home())
        path = base / "TwitchAIBot"
        path.mkdir(parents=True, exist_ok=True)
        return path
    return Path(__file__).resolve().parent
