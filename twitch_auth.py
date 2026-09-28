"""Twitch public-client device authorization with local token refresh."""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path


ID_URL = "https://id.twitch.tv/oauth2"
SCOPES = {"chat:read", "chat:edit"}
REDEMPTION_SCOPES = {"channel:read:redemptions"}


def post_form(path: str, fields: dict[str, str]) -> dict:
    request = urllib.request.Request(
        ID_URL + path,
        data=urllib.parse.urlencode(fields).encode("ascii"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def validate(token: str) -> dict | None:
    request = urllib.request.Request(
        ID_URL + "/validate", headers={"Authorization": "OAuth " + token}
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return None
        raise


def read_tokens(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def save_tokens(path: Path, tokens: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(tokens), encoding="utf-8")
    os.replace(temporary, path)


def authorize_device(client_id: str, scopes: set[str], expected_login: str) -> dict:
    scope_text = " ".join(sorted(scopes))
    device = post_form("/device", {"client_id": client_id, "scopes": scope_text})
    uri = device["verification_uri"]
    print(f"Откройте ссылку и войдите в Twitch под аккаунтом {expected_login}:", flush=True)
    print(uri, flush=True)
    print("Код: " + device["user_code"], flush=True)
    webbrowser.open(uri)
    interval = max(5, int(device.get("interval", 5)))
    deadline = time.monotonic() + int(device.get("expires_in", 1800))
    while time.monotonic() < deadline:
        time.sleep(interval)
        try:
            return post_form("/token", {
                "client_id": client_id,
                "scopes": scope_text,
                "device_code": device["device_code"],
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            })
        except urllib.error.HTTPError as exc:
            try:
                message = json.loads(exc.read()).get("message", "")
            except ValueError:
                message = ""
            if message == "authorization_pending":
                continue
            if message == "slow_down":
                interval += 5
                continue
            raise RuntimeError(f"Авторизация Twitch не завершилась (HTTP {exc.code})") from None
    raise RuntimeError("Время авторизации Twitch истекло; запустите бота снова")


def get_access_token(client_id: str, expected_login: str, path: Path,
                     scopes: set[str] | None = None) -> str:
    required_scopes = SCOPES if scopes is None else scopes
    tokens = read_tokens(path)
    access = tokens.get("access_token", "")
    info = validate(access) if access else None
    if info is not None and not required_scopes.issubset(set(info.get("scopes", []))):
        info = None
    if info is None and tokens.get("refresh_token"):
        try:
            tokens = post_form("/token", {
                "client_id": client_id,
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
            })
            save_tokens(path, tokens)
            info = validate(tokens["access_token"])
            if info is not None and not required_scopes.issubset(set(info.get("scopes", []))):
                info = None
        except urllib.error.HTTPError:
            info = None
    if info is None:
        tokens = authorize_device(client_id, required_scopes, expected_login)
        info = validate(tokens["access_token"])
        if info is None:
            raise RuntimeError("Twitch не подтвердил новый токен")
    if info.get("client_id") != client_id or info.get("login", "").lower() != expected_login:
        raise RuntimeError(f"Twitch авторизован не под аккаунтом {expected_login} или другим приложением")
    if not required_scopes.issubset(set(info.get("scopes", []))):
        raise RuntimeError("Токен Twitch не содержит разрешения: " + ", ".join(sorted(required_scopes)))
    save_tokens(path, tokens)
    return tokens["access_token"]
