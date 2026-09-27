"""Validated settings shared by the GUI and the existing command-line bot."""

import os
import sys
from pathlib import Path

from bot import AI_FALLBACK_MODELS, AI_MODEL, SYSTEM_PROMPT
from paths import data_dir
from start import FIELDS, normalize, read_config


def load_settings(root: Path | None = None) -> tuple[dict[str, str], str]:
    root = root or data_dir()
    values = read_config(root / ".env")
    values.setdefault("AI_BASE_URL", "https://ai.starimg.ru/v1")
    if values.get("AI_MODEL") not in AI_FALLBACK_MODELS:
        values["AI_MODEL"] = AI_MODEL
    prompt_path = root / "prompt.txt"
    prompt = prompt_path.read_text(encoding="utf-8-sig") if prompt_path.exists() else SYSTEM_PROMPT
    return values, prompt


def save_settings(values: dict[str, str], prompt: str, root: Path | None = None) -> None:
    root = root or data_dir()
    previous = read_config(root / ".env")
    clean = {}
    for name, _label in FIELDS:
        value = values.get(name, "")
        if name == "AI_API_KEY" and not value.strip():
            value = previous.get(name, "")
        clean[name] = normalize(name, value)
    model = values.get("AI_MODEL", "").strip()
    if model not in AI_FALLBACK_MODELS:
        raise ValueError("Выберите одну из трёх моделей DeepSeek в списке.")
    clean["AI_MODEL"] = model
    prompt = prompt.strip()
    if not prompt or len(prompt) > 20000:
        raise ValueError("Промпт должен содержать от 1 до 20000 символов.")

    root.mkdir(parents=True, exist_ok=True)
    env_path = root / ".env"
    env_tmp = root / ".env.tmp"
    prompt_path = root / "prompt.txt"
    prompt_tmp = root / "prompt.tmp"
    content = "# Локальные настройки бота. Не публикуйте этот файл.\n"
    content += "".join(f"{name}={clean[name]}\n" for name, _label in FIELDS)
    content += f"AI_MODEL={model}\n"
    env_tmp.write_text(content, encoding="utf-8")
    prompt_tmp.write_text(prompt + "\n", encoding="utf-8")
    os.replace(env_tmp, env_path)
    os.replace(prompt_tmp, prompt_path)
    if sys.platform != "win32":
        env_path.chmod(0o600)
    if (previous.get("TWITCH_BOT_NAME") and previous["TWITCH_BOT_NAME"] != clean["TWITCH_BOT_NAME"]
            or previous.get("TWITCH_CLIENT_ID") and previous["TWITCH_CLIENT_ID"] != clean["TWITCH_CLIENT_ID"]):
        (root / ".twitch_token.json").unlink(missing_ok=True)
