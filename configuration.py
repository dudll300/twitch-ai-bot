"""Shared defaults, validation and local configuration file handling."""

import json
import re
from pathlib import Path
from urllib.parse import urlparse

AI_MODEL = "deepseek-v4.1-flash"
AI_FALLBACK_MODELS = ("deepseek-v4.1-flash", "deepseek-v4.1-pro", "deepseek-v4-flash")
SYSTEM_PROMPT = ""
DEFAULTS = {
    "AI_BASE_URL": "https://ai.starimg.ru/v1",
    "AI_MODEL": AI_MODEL,
    "AI_FALLBACK_MODELS": "",
    "TWITCH_REWARD_TITLE": "Вопрос ИИ",
}
FIELDS = (
    ("TWITCH_CHANNEL", "Логин Twitch-канала (или ссылка на него)"),
    ("TWITCH_BOT_NAME", "Логин отдельного Twitch-аккаунта бота"),
    ("TWITCH_CLIENT_ID", "Client ID приложения Twitch типа Public"),
    ("TWITCH_REWARD_TITLE", "Название награды за баллы канала"),
    ("AI_BASE_URL", "Base URL совместимого Chat Completions API"),
    ("AI_API_KEY", "Ключ AI API"),
    ("AI_MODEL", "ID основной модели у вашего провайдера"),
    ("AI_FALLBACK_MODELS", "Запасные модели через запятую (необязательно)"),
)
LOGIN = re.compile(r"^[a-zA-Z0-9_]{1,25}$")


def read_config(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            if value[0] == '"':
                try:
                    value = json.loads(value)
                except ValueError:
                    value = value[1:-1]
            else:
                value = value[1:-1]
        values[key.strip()] = value
    return values


def normalize(name: str, value: str) -> str:
    value = value.strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{name}: управляющие символы недопустимы.")
    if name == "AI_FALLBACK_MODELS":
        return ",".join(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))
    if not value:
        raise ValueError(f"Заполните {name}.")
    if name in ("TWITCH_CHANNEL", "TWITCH_BOT_NAME"):
        if name == "TWITCH_CHANNEL" and "twitch.tv/" in value.lower():
            parsed = urlparse(value if "://" in value else "https://" + value)
            if parsed.hostname not in ("twitch.tv", "www.twitch.tv", "m.twitch.tv"):
                raise ValueError("Нужна ссылка на канал Twitch.")
            value = parsed.path.strip("/").split("/")[0]
        value = value.lstrip("#@").lower()
        if not LOGIN.fullmatch(value):
            raise ValueError("Нужен Twitch-логин из букв, цифр или подчёркивания.")
    elif name == "AI_BASE_URL":
        value = value.rstrip("/").removesuffix("/chat/completions")
        parsed = urlparse(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.query or parsed.fragment
                or parsed.username or parsed.password or any(char.isspace() for char in value)):
            raise ValueError("Нужен Base URL, начинающийся с https://, без пароля, параметров и пробелов.")
    elif name == "TWITCH_REWARD_TITLE" and len(value) > 45:
        raise ValueError("Название награды должно содержать не более 45 символов.")
    return value


def invalidate_tokens(root: Path, previous: dict[str, str], values: dict[str, str]) -> None:
    for filename, account in ((".twitch_token.json", "TWITCH_BOT_NAME"),
                              (".twitch_broadcaster_token.json", "TWITCH_CHANNEL")):
        if any(previous.get(key) != values.get(key) for key in (account, "TWITCH_CLIENT_ID")):
            (root / filename).unlink(missing_ok=True)


def env_content(values: dict[str, str]) -> str:
    return "# Локальные настройки бота. Не публикуйте этот файл.\n" + "".join(
        f"{name}={json.dumps(value, ensure_ascii=False)}\n" for name, value in values.items()
    )
