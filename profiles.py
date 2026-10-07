"""Local, opt-in instructions matched against Twitch event identity."""

import json
import os
import re
from pathlib import Path

from configuration import normalize

MAX_PROFILE_PROMPT = 10000
MAX_PROFILES = 1000
MAX_ALIASES = 20
REWARD_BLOCKED_REFUSAL = "Вам запрещено использовать эту награду. Обратитесь к владельцу канала."


class ProfileError(ValueError):
    def __init__(self, message: str, index: int | None = None):
        super().__init__(message)
        self.index = index


def validate_profiles(profiles: object, *, allow_empty_prompt: bool = False) -> list[dict]:
    if not isinstance(profiles, list) or len(profiles) > MAX_PROFILES:
        raise ProfileError(f"Нужен список максимум из {MAX_PROFILES} профилей.")
    result, logins, ids = [], set(), set()
    for index, raw in enumerate(profiles):
        try:
            if not isinstance(raw, dict):
                raise ValueError("Нужен объект профиля.")
            login, user_id, prompt = (raw.get(key, "") for key in ("login", "user_id", "prompt"))
            if not all(isinstance(value, str) for value in (login, user_id, prompt)):
                raise ValueError("Логин, ID и инструкция должны быть строками.")
            login = normalize("TWITCH_CHANNEL", login) if login.strip() else ""
            user_id, prompt = user_id.strip(), prompt.strip()
            reward_blocked = raw.get("reward_blocked", False)
            if not isinstance(reward_blocked, bool):
                raise ValueError("reward_blocked должен быть true или false.")
            if user_id and not re.fullmatch(r"[0-9]{1,30}", user_id):
                raise ValueError("Twitch ID должен содержать только цифры, до 30 знаков.")
            if not login and not user_id:
                raise ValueError("Укажите Twitch-логин или числовой ID.")
            # Permission-only profiles remain valid when their ban is removed.
            if (not prompt and not allow_empty_prompt and "reward_blocked" not in raw) or len(prompt) > MAX_PROFILE_PROMPT:
                raise ValueError(f"Личная инструкция должна содержать от 1 до {MAX_PROFILE_PROMPT} символов.")
            enabled = raw.get("enabled", True)
            if not isinstance(enabled, bool):
                raise ValueError("enabled должен быть true или false.")
            aliases = raw.get("aliases", [])
            if not isinstance(aliases, list) or len(aliases) > MAX_ALIASES:
                raise ValueError(f"Нужен список максимум из {MAX_ALIASES} альтернативных имён.")
            clean_aliases = []
            for alias in aliases:
                if not isinstance(alias, str):
                    raise ValueError("Альтернативные имена должны быть строками.")
                alias = alias.strip().lstrip("@")
                if not 1 <= len(alias) <= 64 or not re.fullmatch(r"[\w-]+", alias):
                    raise ValueError("Каждое альтернативное имя: 1–64 буквы, цифры, подчёркивания или дефисы, без пробелов.")
                if alias.casefold() not in {item.casefold() for item in clean_aliases}:
                    clean_aliases.append(alias)
            if (login and login in logins) or (user_id and user_id in ids):
                raise ValueError("Профиль с таким логином или Twitch ID уже существует.")
            if login:
                logins.add(login)
            if user_id:
                ids.add(user_id)
            result.append({"login": login, "user_id": user_id, "prompt": prompt,
                           "enabled": enabled, "aliases": clean_aliases,
                           **({"reward_blocked": reward_blocked} if "reward_blocked" in raw else {})})
        except ValueError as exc:
            raise ProfileError(f"Профиль {index + 1}: {exc}", index) from exc
    return result


def load_profiles(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ProfileError(f"Не удалось прочитать {path.name}: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ProfileError(f"{path.name}: ожидается объект с version: 1.")
    return validate_profiles(data.get("profiles"))


def save_profiles(path: Path, profiles: list[dict]) -> None:
    validated = validate_profiles(profiles)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"version": 1, "profiles": validated},
                                    ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    if os.name != "nt":
        path.chmod(0o600)


def profile_for(profiles: list[dict], login: str, user_id: str = "") -> dict | None:
    # A disabled ID profile also blocks an overlapping login-only profile.
    profile = next((row for row in profiles if user_id and row["user_id"] == user_id), None)
    if profile is None:
        profile = next((row for row in profiles if not row["user_id"] and
                        row["login"] == login.casefold()), None)
    return profile


def prompt_for(profiles: list[dict], login: str, user_id: str = "") -> str:
    profile = profile_for(profiles, login, user_id)
    return profile["prompt"] if profile is not None and profile["enabled"] else ""


def reward_is_blocked(profiles, login, user_id=""):
    profile = profile_for(profiles, login, user_id)
    return bool(profile and profile.get("reward_blocked", False))
