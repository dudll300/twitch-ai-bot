"""Validated settings shared by the GUI and the existing command-line bot."""

import os
import sys
from pathlib import Path

from configuration import DEFAULTS, FIRST_RUN_FALLBACK_MODELS, SYSTEM_PROMPT, FIELDS, normalize, read_config, invalidate_tokens, env_content
from paths import data_dir
from reply_rules import upgrade_generated_prompt


def load_settings(root: Path | None = None) -> tuple[dict[str, str], str]:
    root = root or data_dir()
    env_path = root / ".env"
    first_run = not env_path.exists()
    values = read_config(env_path)
    values = {**DEFAULTS, **values}
    if first_run:
        values["AI_FALLBACK_MODELS"] = FIRST_RUN_FALLBACK_MODELS
    prompt_path = root / "prompt.txt"
    prompt = prompt_path.read_text(encoding="utf-8-sig") if prompt_path.exists() else SYSTEM_PROMPT
    return values, prompt


def save_settings(values: dict[str, str], prompt: str, root: Path | None = None, *,
                  allow_incomplete: bool = False) -> None:
    root = root or data_dir()
    previous = read_config(root / ".env")
    clean = dict(previous)
    for name, _label in FIELDS:
        value = values.get(name, DEFAULTS.get(name, ""))
        if name == "AI_API_KEY" and not value.strip():
            value = previous.get(name, "")
        clean[name] = "" if allow_incomplete and not value.strip() else normalize(name, value)
    prompt = upgrade_generated_prompt(prompt.strip())
    if len(prompt) > 20000:
        raise ValueError("Промпт должен содержать не более 20000 символов.")

    root.mkdir(parents=True, exist_ok=True)
    env_path = root / ".env"
    env_tmp = root / ".env.tmp"
    prompt_path = root / "prompt.txt"
    prompt_tmp = root / "prompt.tmp"
    content = env_content(clean)
    env_tmp.write_text(content, encoding="utf-8")
    prompt_tmp.write_text(prompt + "\n", encoding="utf-8")
    os.replace(env_tmp, env_path)
    os.replace(prompt_tmp, prompt_path)
    if sys.platform != "win32":
        env_path.chmod(0o600)
    invalidate_tokens(root, previous, clean)
