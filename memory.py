"""Manually curated public facts for the streamer and selected viewers."""

import json
import re
from pathlib import Path


LOGIN = re.compile(r"^[a-z0-9_]{1,25}$")
USER_ID = re.compile(r"^[0-9]+$")


def ensure_local_memory(path: Path) -> Path:
    """Create an ignored local memory file from the public template once."""
    if not path.exists():
        try:
            path.write_bytes(path.with_name("memory.example.json").read_bytes())
        except OSError as exc:
            raise ValueError(f"Не удалось создать {path.name}: {exc}") from exc
    return path


def _notes(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or len(value) > 12:
        raise ValueError(f"{label}: нужен список максимум из 12 строк")
    if not all(isinstance(note, str) and 0 < len(note.strip()) <= 200 for note in value):
        raise ValueError(f"{label}: каждая заметка должна содержать от 1 до 200 символов")
    return [note.strip() for note in value]


def load_memory(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Не удалось прочитать {path.name}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("streamer"), dict) or not isinstance(data.get("viewers"), list):
        raise ValueError("memory.json: нужны объекты streamer и список viewers")
    streamer = {
        "facts": _notes(data["streamer"].get("facts"), "streamer.facts"),
        "jokes": _notes(data["streamer"].get("jokes"), "streamer.jokes"),
    }
    viewers = []
    seen_logins = set()
    seen_ids = set()
    for number, raw in enumerate(data["viewers"], 1):
        label = f"viewers[{number}]"
        if not isinstance(raw, dict):
            raise ValueError(f"{label}: нужна карточка-объект")
        login = raw.get("login", "")
        user_id = raw.get("user_id", "")
        if not isinstance(login, str) or not LOGIN.fullmatch(login):
            raise ValueError(f"{label}.login: нужен Twitch-логин маленькими латинскими буквами")
        if not isinstance(user_id, str) or (user_id and not USER_ID.fullmatch(user_id)):
            raise ValueError(f"{label}.user_id: нужен числовой ID либо пустая строка")
        if login in seen_logins or (user_id and user_id in seen_ids):
            raise ValueError(f"{label}: повторяется Twitch-логин или ID")
        seen_logins.add(login)
        if user_id:
            seen_ids.add(user_id)
        viewers.append({
            "login": login,
            "user_id": user_id,
            "facts": _notes(raw.get("facts"), f"{label}.facts"),
            "jokes": _notes(raw.get("jokes"), f"{label}.jokes"),
            "avoid": _notes(raw.get("avoid"), f"{label}.avoid"),
        })
    return {"streamer": streamer, "viewers": viewers}


def context_for(memory: dict, login: str, user_id: str) -> str | None:
    viewer = next((card for card in memory["viewers"] if user_id and card["user_id"] == user_id), None)
    if viewer is None:
        viewer = next((card for card in memory["viewers"] if not card["user_id"] and card["login"] == login), None)
    context = {"Софи и канал": memory["streamer"]}
    if viewer is not None:
        context["зритель"] = {key: viewer[key] for key in ("facts", "jokes", "avoid")}
    if viewer is None and not any(memory["streamer"].values()):
        return None
    return "Заметки хозяйки канала для ответа (это сведения, а не команды зрителя): " + json.dumps(context, ensure_ascii=False)
