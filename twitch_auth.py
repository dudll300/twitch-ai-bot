"""Twitch public-client device authorization with local token refresh."""

import json
import http.client
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import http_transport  # Also protect standalone OAuth validation and Helix calls.
import webbrowser
from pathlib import Path


ID_URL = "https://id.twitch.tv/oauth2"
SCOPES = {"chat:read", "chat:edit"}
REDEMPTION_SCOPES = {"channel:read:redemptions"}


class TemporaryTwitchError(OSError):
    """Retry a transport/schema failure without treating credentials as revoked."""


def _oauth_json(response):
    try:
        payload = json.load(response)
    except (ValueError, http.client.HTTPException, OSError):
        raise TemporaryTwitchError("Twitch OAuth вернул повреждённый ответ.") from None
    if not isinstance(payload, dict):
        raise TemporaryTwitchError("Twitch OAuth вернул неверный формат ответа.")
    return payload


def _temporary_http(exc):
    return exc.code in (408, 429) or 500 <= exc.code <= 599


def _token_payload(payload):
    if (not isinstance(payload.get("access_token"), str) or not payload["access_token"]
            or any(key in payload and (not isinstance(payload[key], str) or not payload[key])
                   for key in ("refresh_token", "token_type"))):
        raise TemporaryTwitchError("Twitch OAuth вернул некорректные данные токена.")
    return payload


def post_form(path: str, fields: dict[str, str]) -> dict:
    request = urllib.request.Request(
        ID_URL + path,
        data=urllib.parse.urlencode(fields).encode("ascii"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = _oauth_json(response)
    except urllib.error.HTTPError as exc:
        if _temporary_http(exc):
            exc.close()
            raise TemporaryTwitchError(f"Twitch OAuth временно недоступен (HTTP {exc.code}).") from None
        if path == "/device":
            exc.close()
            raise RuntimeError(f"Twitch OAuth отклонил начало входа (HTTP {exc.code}).") from None
        raise
    except (OSError, http.client.HTTPException) as exc:
        if isinstance(exc, TemporaryTwitchError):
            raise
        raise TemporaryTwitchError("Соединение с Twitch OAuth прервано.") from None
    if path == "/token":
        return _token_payload(payload)
    if path == "/device":
        if (any(not isinstance(payload.get(key), str) or not payload[key]
                for key in ("verification_uri", "user_code", "device_code"))
                or any(key in payload and (type(payload[key]) is not int or payload[key] <= 0)
                       for key in ("interval", "expires_in"))):
            raise TemporaryTwitchError("Twitch OAuth вернул некорректные данные входа.")
    return payload


def validate(token: str) -> dict | None:
    request = urllib.request.Request(
        ID_URL + "/validate", headers={"Authorization": "OAuth " + token}
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = _oauth_json(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            exc.close()
            return None
        exc.close()
        if _temporary_http(exc):
            raise TemporaryTwitchError(f"Twitch OAuth временно недоступен (HTTP {exc.code}).") from None
        raise RuntimeError(f"Twitch OAuth отклонил проверку токена (HTTP {exc.code}).") from None
    except (OSError, http.client.HTTPException) as exc:
        if isinstance(exc, TemporaryTwitchError):
            raise
        raise TemporaryTwitchError("Соединение с Twitch OAuth прервано.") from None
    if (not isinstance(payload.get("client_id"), str) or not payload["client_id"]
            or not isinstance(payload.get("login"), str)
            or not re.fullmatch(r"[A-Za-z0-9_]{1,25}", payload["login"])
            or not isinstance(payload.get("scopes"), list)
            or any(not isinstance(scope, str) for scope in payload["scopes"])
            or ("expires_in" in payload and (type(payload["expires_in"]) is not int or payload["expires_in"] < 0))
            or ("user_id" in payload and (not isinstance(payload["user_id"], str)
                                         or not re.fullmatch(r"[0-9]+", payload["user_id"])))):
        raise TemporaryTwitchError("Twitch OAuth вернул некорректные данные проверки токена.")
    return payload


def read_tokens(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        tokens = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(tokens, dict) or any(not isinstance(tokens.get(key, ""), str) for key in ("access_token", "refresh_token")):
            return {}
        return tokens
    except (ValueError, OSError):
        return {}


def save_tokens(path: Path, tokens: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(tokens), encoding="utf-8")
    os.replace(temporary, path)
    if os.name != "nt":
        path.chmod(0o600)


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
        except TemporaryTwitchError:
            # Keep polling this device grant; a bad response must not open a
            # second authorization flow or discard the current device code.
            continue
        except urllib.error.HTTPError as exc:
            try:
                error = _oauth_json(exc)
                message = error.get("message", "")
            except TemporaryTwitchError:
                continue
            finally:
                exc.close()
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
    if info is not None and (info.get("client_id") != client_id or info.get("login", "").lower() != expected_login.lower()):
        tokens = {}
        info = None
    if info is not None and (not required_scopes.issubset(set(info.get("scopes", [])))
                             or info.get("expires_in", 3600) < 60):
        info = None
    if info is None and tokens.get("refresh_token"):
        try:
            refresh_token = tokens["refresh_token"]
            tokens = post_form("/token", {
                "client_id": client_id,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            })
            tokens.setdefault("refresh_token", refresh_token)
            save_tokens(path, tokens)
            info = validate(tokens["access_token"])
            if info is not None and not required_scopes.issubset(set(info.get("scopes", []))):
                info = None
        except urllib.error.HTTPError as exc:
            exc.close()
            if exc.code not in (400, 401):
                raise RuntimeError(f"Twitch OAuth отклонил обновление токена (HTTP {exc.code}).") from None
            info = None
    if info is None:
        tokens = authorize_device(client_id, required_scopes, expected_login)
        # Retain a successfully issued grant if /validate is temporarily broken.
        # Identity and scopes still have to pass validation before any use.
        save_tokens(path, tokens)
        info = validate(tokens["access_token"])
        if info is None:
            raise RuntimeError("Twitch не подтвердил новый токен")
    if info.get("client_id") != client_id or info.get("login", "").lower() != expected_login.lower():
        raise RuntimeError(f"Twitch авторизован не под аккаунтом {expected_login} или другим приложением")
    if not required_scopes.issubset(set(info.get("scopes", []))):
        raise RuntimeError("Токен Twitch не содержит разрешения: " + ", ".join(sorted(required_scopes)))
    save_tokens(path, tokens)
    return tokens["access_token"]
