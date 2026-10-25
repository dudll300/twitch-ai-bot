"""Safe startup provenance, also usable without Git and in a frozen EXE."""

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys


VERSION = "2026.10.20"


def build_id():
    root = Path(__file__).resolve().parent
    try:
        value = json.loads((root / "build-info.json").read_text(encoding="utf-8"))["commit"]
        if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{7,40}", value):
            return value
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if getattr(sys, "frozen", False):
        return "packaged-unknown"
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                                text=True, timeout=2, check=False,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        value = result.stdout.strip()
        if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", value):
            return value
    except (OSError, subprocess.SubprocessError):
        pass
    return "source-unknown"


def log_startup():
    print(f"APP_BUILD version={VERSION} commit={build_id()} started_at={datetime.now(timezone.utc).isoformat()} log_timezone=UTC", flush=True)
