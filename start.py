"""First-run setup and launcher for the Twitch bot."""

import getpass
import subprocess
import sys
from pathlib import Path

from configuration import (AI_MODEL, DEFAULTS, FIELDS, read_config, normalize,
                           invalidate_tokens, env_content)
from paths import data_dir


ROOT = data_dir()
ENV_PATH = ROOT / ".env"


def complete(values: dict[str, str]) -> bool:
    try:
        return all(normalize(name, values.get(name, DEFAULTS.get(name, ""))) or name == "AI_FALLBACK_MODELS" for name, _ in FIELDS)
    except ValueError:
        return False


def setup(path: Path, force: bool = False) -> None:
    previous = read_config(path)
    if complete(previous) and not force:
        return
    print("\nПервоначальная настройка бота. Данные сохраняются только в файле .env на этом компьютере.")
    print(f"Основная модель: {previous.get('AI_MODEL', AI_MODEL)}; Base URL по умолчанию — https://ai.starimg.ru/v1.")
    print("Для Twitch нужен отдельный аккаунт бота и Client ID приложения типа Public.\n")
    values = dict(previous)
    for name, label in FIELDS:
        old = previous.get(name, DEFAULTS.get(name, ""))
        while True:
            suffix = (f" [Enter — {old}]" if name in DEFAULTS else
                      " [Enter — оставить прежнее]" if old else "")
            prompt = f"{label}{suffix}: "
            try:
                entered = getpass.getpass(prompt) if name == "AI_API_KEY" else input(prompt)
            except (EOFError, KeyboardInterrupt):
                raise RuntimeError("Настройка отменена; файл .env не изменён.") from None
            try:
                values[name] = normalize(name, "" if name == "AI_FALLBACK_MODELS" and entered == "-" else entered or old)
                break
            except ValueError as exc:
                print(f"Ошибка: {exc}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".env.tmp")
    temporary.write_text(env_content(values), encoding="utf-8")
    temporary.replace(path)
    if sys.platform != "win32":
        path.chmod(0o600)
    invalidate_tokens(path.parent, previous, values)
    prompt_path = path.parent / "prompt.txt"
    if not prompt_path.exists():
        prompt_path.write_text("", encoding="utf-8")
    print(f"Системный промпт можно заполнить в {prompt_path}. Для очистки списка запасных моделей в мастере введите -.")
    print("\nНастройки сохранены. Запускаю бота...\n")


def main() -> int:
    if len(sys.argv) > 2 or (len(sys.argv) == 2 and sys.argv[1] != "--setup"):
        print("Использование: python start.py [--setup]")
        return 2
    try:
        setup(ENV_PATH, force="--setup" in sys.argv)
    except (RuntimeError, ValueError, OSError) as exc:
        print(exc)
        return 1
    return subprocess.call([sys.executable, str(ROOT / "bot.py")], cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
