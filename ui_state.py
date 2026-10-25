"""Non-secret desktop preferences, independent of bot configuration."""
import json
import os
from pathlib import Path

DEFAULTS = {"schema_version": 1, "theme": "dark", "animations": True,
            "setup_done": False, "setup_step": 0, "width": 1240, "height": 860,
            "splitters": {}}


def load_ui_state(root):
    result = dict(DEFAULTS)
    try:
        path = Path(root) / "ui-state.json"
        if path.stat().st_size > 32768:
            return result
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            return result
        if raw.get("theme") in ("dark", "light"):
            result["theme"] = raw["theme"]
        for key in ("animations", "setup_done"):
            if isinstance(raw.get(key), bool):
                result[key] = raw[key]
        for key, lower, upper in (("width", 600, 5000), ("height", 400, 3000), ("setup_step", 0, 4)):
            if type(raw.get(key)) is int and lower <= raw[key] <= upper:
                result[key] = raw[key]
        splitters = raw.get("splitters", {})
        if isinstance(splitters, dict):
            result["splitters"] = {key: value for key, value in splitters.items()
                                   if key in ("profiles", "context", "history") and isinstance(value, list)
                                   and len(value) == 2 and all(type(n) is int and 0 <= n <= 5000 for n in value)}
    except (OSError, ValueError, UnicodeError, RecursionError):
        pass
    return result


def save_ui_state(root, state):
    # Persist only the allowlist. Never serialize widget contents or credentials.
    path = Path(root) / "ui-state.json"
    raw = {key: state.get(key, value) for key, value in DEFAULTS.items()}
    splitters = raw["splitters"]
    raw["splitters"] = {key: value for key, value in splitters.items()
                        if key in ("profiles", "context", "history") and isinstance(value, list)
                        and len(value) == 2 and all(type(n) is int and 0 <= n <= 5000 for n in value)} if isinstance(splitters, dict) else {}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
