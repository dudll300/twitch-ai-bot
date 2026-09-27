"""First-run setup and launcher for the Twitch bot."""

import getpass
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from bot import AI_MODEL
from paths import data_dir


ROOT = data_dir()
ENV_PATH = ROOT / ".env"
FIELDS = (
    ("TWITCH_CHANNEL", "Логин Twitch-канала (или ссылка на него)"),
    ("TWITCH_BOT_NAME", "Логин отдельного Twitch-аккаунта бота"),
    ("TWITCH_CLIENT_ID", "Client ID приложения Twitch типа Public"),
    ("AI_BASE_URL", "Base URL API ai.starimg.ru"),
    ("AI_API_KEY", "Ключ AI API"),
)
LOGIN = re.compile(r"^[a-zA-Z0-9_]{1,25}$")


def read_config(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def normalize(name: str, value: str) -> str:
    value = value.strip()
    if name in ("TWITCH_CHANNEL", "TWITCH_BOT_NAME"):
        if name == "TWITCH_CHANNEL" and "twitch.tv/" in value.lower():
            value = urlparse(value if "://" in value else "https://" + value).path.strip("/").split("/")[0]
        value = value.lstrip("#").lower()
        if not LOGIN.fullmatch(value):
            raise ValueError("Нужен Twitch-логин из букв, цифр или подчёркивания.")
    elif name == "AI_BASE_URL":
        value = value.rstrip("/")
        if value.endswith("/chat/completions"):
            value = value.removesuffix("/chat/completions")
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("Нужен Base URL, начинающийся с https://")
    elif not value or "\n" in value or "\r" in value:
        raise ValueError("Это поле нельзя оставлять пустым.")
    return value


def complete(values: dict[str, str]) -> bool:
    try:
        return all(normalize(name, values.get(name, "")) for name, _ in FIELDS)
    except ValueError:
        return False


def setup(path: Path, force: bool = False) -> None:
    previous = read_config(path)
    if complete(previous) and not force:
        return
    print("\nПервоначальная настройка бота. Данные сохраняются только в файле .env на этом компьютере.")
    print(f"Основная модель: {previous.get('AI_MODEL', AI_MODEL)}; Base URL по умолчанию — https://ai.starimg.ru/v1.")
    print("Для Twitch нужен отдельный аккаунт бота и Client ID приложения типа Public.\n")
    values = {}
    for name, label in FIELDS:
        old = previous.get(name, "") or ("https://ai.starimg.ru/v1" if name == "AI_BASE_URL" else "")
        while True:
            suffix = (f" [Enter — {old}]" if name == "AI_BASE_URL" else
                      " [Enter — оставить прежнее]" if old else "")
            prompt = f"{label}{suffix}: "
            try:
                entered = getpass.getpass(prompt) if name == "AI_API_KEY" else input(prompt)
            except (EOFError, KeyboardInterrupt):
                raise RuntimeError("Настройка отменена; файл .env не изменён.") from None
            try:
                values[name] = normalize(name, entered or old)
                break
            except ValueError as exc:
                print(f"Ошибка: {exc}")
    content = "# Локальные настройки бота. Не публикуйте этот файл.\n"
    content += "".join(f"{name}={values[name]}\n" for name, _ in FIELDS)
    if previous.get("AI_MODEL"):
        content += f"AI_MODEL={previous['AI_MODEL']}\n"
    path.write_text(content, encoding="utf-8")
    if sys.platform != "win32":
        path.chmod(0o600)
    if (previous.get("TWITCH_BOT_NAME") and previous.get("TWITCH_BOT_NAME") != values["TWITCH_BOT_NAME"]
            or previous.get("TWITCH_CLIENT_ID") and previous.get("TWITCH_CLIENT_ID") != values["TWITCH_CLIENT_ID"]):
        (path.parent / ".twitch_token.json").unlink(missing_ok=True)
    print("\nНастройки сохранены. Запускаю бота...\n")


def main() -> int:
    if len(sys.argv) > 2 or (len(sys.argv) == 2 and sys.argv[1] != "--setup"):
        print("Использование: python start.py [--setup]")
        return 2
    try:
        setup(ENV_PATH, force="--setup" in sys.argv)
    except RuntimeError as exc:
        print(exc)
        return 1
    return subprocess.call([sys.executable, str(ROOT / "bot.py")], cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
