"""First-run setup and launcher for the Twitch bot."""

import getpass
import subprocess
import sys
from pathlib import Path

from configuration import (AI_MODEL, DEFAULTS, FIRST_RUN_FALLBACK_MODELS, FIELDS, read_config, normalize,
                           invalidate_tokens, env_content)
from paths import data_dir
from ai_connection import migrate, profiles, selected_values, origin, profile_key


ROOT = data_dir()
ENV_PATH = ROOT / ".env"


def complete(values: dict[str, str]) -> bool:
    try:
        values = migrate(values)
        values = selected_values(values, values)
        return all(normalize(name, values.get(name, DEFAULTS.get(name, ""))) or name == "AI_FALLBACK_MODELS" for name, _ in FIELDS)
    except ValueError:
        return False


def setup(path: Path, force: bool = False) -> None:
    first_run = not path.exists()
    previous = read_config(path)
    if complete(previous) and not force:
        return
    print("\nПервоначальная настройка бота. Данные сохраняются только в файле .env на этом компьютере.")
    print(f"Основная модель: {previous.get('AI_MODEL', AI_MODEL)}; Base URL по умолчанию — https://ai.starimg.ru/v1.")
    print("Для Twitch нужен отдельный аккаунт бота и Client ID приложения типа Public.\n")
    values = migrate(previous)
    selected = values['AI_PROVIDER']
    active = profiles(previous)[selected]
    for name, label in FIELDS:
        old = active.get(name.removeprefix('AI_'), '') if name.startswith('AI_') and name != 'AI_PROVIDER' else previous.get(name, DEFAULTS.get(name, ""))
        if name == 'AI_BASE_URL' and selected == 'openai':
            values[name] = active['BASE_URL']
            print('OpenAI: https://api.openai.com/v1; основная модель — ' + active['MODEL'])
            continue
        if name == 'AI_API_KEY' and old:
            bound = active['KEY_ORIGIN'] or origin(active['BASE_URL'])
            if bound != origin(values['AI_BASE_URL']):
                old = ''
                print('Адрес изменился: введите ключ для нового сервера.')
        if name == "AI_FALLBACK_MODELS" and first_run and selected == 'compatible':
            old = FIRST_RUN_FALLBACK_MODELS
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
                if name == 'AI_PROVIDER':
                    selected = values[name]
                    active = profiles(previous)[selected]
                    values.update({'AI_' + key: value for key, value in active.items() if key != 'KEY_ORIGIN'})
                if name == 'AI_API_KEY' and entered.strip():
                    values[profile_key(selected, 'CONFIRMED_ORIGIN')] = origin(values['AI_BASE_URL'])
                break
            except ValueError as exc:
                print(f"Ошибка: {exc}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".env.tmp")
    values = {**values, **selected_values(values, previous)}
    values = {key: value for key, value in values.items() if not key.endswith('_CONFIRMED_ORIGIN')}
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
