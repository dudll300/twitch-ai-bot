"""Receive questions from one Twitch Channel Points reward over EventSub."""

import asyncio
import http.client
import json
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path
from typing import Awaitable, Callable

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from twitch_auth import REDEMPTION_SCOPES, TemporaryTwitchError, get_access_token, validate
from ai_client import http_error_detail


API_URL = "https://api.twitch.tv/helix"
WEBSOCKET_URL = "wss://eventsub.wss.twitch.tv/ws"
REWARD_TITLE = "Вопрос ИИ"


class SubscriptionRevokedError(RuntimeError):
    """An explicit permission/account/version change requires user action."""


def event_message(raw):
    try:
        message = json.loads(raw)
    except (ValueError, TypeError):
        raise TemporaryTwitchError("EventSub вернул повреждённый пакет.") from None
    if (not isinstance(message, dict) or not isinstance(message.get("metadata"), dict)
            or not isinstance(message["metadata"].get("message_type"), str)
            or not isinstance(message.get("payload"), dict)):
        raise TemporaryTwitchError("EventSub вернул неверный формат пакета.")
    return message


def api_json(path: str, client_id: str, token: str, payload: dict | None = None) -> dict:
    request = urllib.request.Request(
        API_URL + path,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers={
            "Client-Id": client_id,
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.load(response)
            if not isinstance(result, dict):
                raise ValueError
            return result
    except urllib.error.HTTPError as exc:
        message = f"Twitch API вернул HTTP {exc.code}{http_error_detail(exc, token)}"
        error = TemporaryTwitchError if exc.code in (408, 429) or 500 <= exc.code <= 599 else RuntimeError
        raise error(message) from None
    except (ValueError, http.client.HTTPException):
        raise TemporaryTwitchError("Twitch API вернул повреждённый ответ.") from None


def subscribe(client_id: str, token: str, broadcaster_id: str,
              session_id: str) -> None:
    api_json("/eventsub/subscriptions", client_id, token, {
        "type": "channel.channel_points_custom_reward_redemption.add",
        "version": "1",
        "condition": {"broadcaster_user_id": broadcaster_id},
        "transport": {"method": "websocket", "session_id": session_id},
    })


class RewardListener:
    def __init__(self, client_id: str, channel_login: str, token_path: Path,
                 on_question: Callable[[str, str, str, str], Awaitable[None]],
                 reward_title: str = REWARD_TITLE):
        self.reward_title = reward_title.strip()
        self.client_id = client_id
        self.channel_login = channel_login
        self.token_path = token_path
        self.on_question = on_question
        self.broadcaster_id = ""
        self.seen_ids: set[str] = set()
        self.recent_ids: deque[str] = deque()

    async def _token(self) -> str:
        return await asyncio.to_thread(
            get_access_token, self.client_id, self.channel_login,
            self.token_path, REDEMPTION_SCOPES,
        )

    async def prepare(self) -> None:
        token = await self._token()
        info = await asyncio.to_thread(validate, token)
        self.broadcaster_id = str((info or {}).get("user_id", ""))
        if not self.broadcaster_id:
            raise RuntimeError("Twitch не вернул ID аккаунта владельца канала.")

    async def _welcome(self, websocket) -> tuple[str, int]:
        message = event_message(await asyncio.wait_for(websocket.recv(), timeout=15))
        if message.get("metadata", {}).get("message_type") != "session_welcome":
            raise TemporaryTwitchError("EventSub не прислал подтверждение подключения.")
        session = message["payload"].get("session")
        if not isinstance(session, dict) or not isinstance(session.get("id"), str) or not session["id"]:
            raise TemporaryTwitchError("EventSub вернул неверные данные сессии.")
        keepalive = session.get("keepalive_timeout_seconds")
        keepalive = 10 if keepalive is None else keepalive
        if type(keepalive) is not int or not 1 <= keepalive <= 600:
            raise TemporaryTwitchError("EventSub вернул неверное время ожидания.")
        return session["id"], max(10, keepalive)

    def _remember_redemption(self, redemption_id: str) -> bool:
        if redemption_id in self.seen_ids:
            return False
        if len(self.recent_ids) >= 1000:
            self.seen_ids.remove(self.recent_ids.popleft())
        self.recent_ids.append(redemption_id)
        self.seen_ids.add(redemption_id)
        return True

    async def handle_notification(self, payload: dict) -> None:
        event = payload.get("event", {})
        if not isinstance(event, dict) or not isinstance(event.get("reward", {}), dict):
            raise TemporaryTwitchError("EventSub вернул неверный формат награды.")
        title = event.get("reward", {}).get("title", "")
        if not isinstance(title, str):
            raise TemporaryTwitchError("EventSub вернул неверный формат награды.")
        if title.strip().casefold() != self.reward_title.casefold():
            return
        redemption_id = event.get("id", "")
        if (not isinstance(redemption_id, str) or not isinstance(event.get("user_login", ""), str)
                or not isinstance(event.get("user_input", ""), str)):
            raise TemporaryTwitchError("EventSub вернул неверные данные награды.")
        if not redemption_id or not self._remember_redemption(redemption_id):
            return
        login = event.get("user_login", "").lower()
        user_id = str(event.get("user_id", ""))
        # Preserve the full input for privacy checks before truncation.
        question = str(event.get("user_input", "")).strip()
        if not login or not question:
            print(f"Погашение {redemption_id}: нет логина или текста вопроса; проверьте награду в Twitch.", flush=True)
            return
        await self.on_question(login, user_id, question, redemption_id)

    async def _listen(self, token: str) -> None:
        websocket = await connect(WEBSOCKET_URL, ping_interval=None)
        try:
            session_id, keepalive = await self._welcome(websocket)
            await asyncio.to_thread(
                subscribe, self.client_id, token, self.broadcaster_id,
                session_id,
            )
            print(f"Жду вопросов по награде «{self.reward_title}».", flush=True)
            while True:
                message = event_message(await asyncio.wait_for(websocket.recv(), timeout=keepalive + 10))
                kind = message.get("metadata", {}).get("message_type")
                if kind == "notification":
                    await self.handle_notification(message.get("payload", {}))
                elif kind == "session_reconnect":
                    session = message["payload"].get("session")
                    if not isinstance(session, dict) or not isinstance(session.get("reconnect_url"), str) or not session["reconnect_url"]:
                        raise TemporaryTwitchError("EventSub вернул неверные данные переподключения.")
                    reconnect_url = session["reconnect_url"]
                    replacement = await connect(reconnect_url, ping_interval=None)
                    try:
                        _, keepalive = await self._welcome(replacement)
                    except BaseException:
                        await replacement.close()
                        raise
                    await websocket.close()
                    websocket = replacement
                    print("EventSub переподключён.", flush=True)
                elif kind == "revocation":
                    raise SubscriptionRevokedError("Twitch отозвал подписку на награду; проверьте разрешения аккаунта владельца канала.")
        finally:
            await websocket.close()

    async def run(self) -> None:
        while True:
            try:
                await self._listen(await self._token())
                print("EventSub отключился; повтор через 5 секунд.", flush=True)
            except (OSError, TimeoutError, ConnectionClosed, InvalidHandshake) as exc:
                reason = str(exc) if isinstance(exc, TemporaryTwitchError) else type(exc).__name__
                print(f"Соединение EventSub прервано: {reason}; повтор через 5 секунд.", flush=True)
            await asyncio.sleep(5)
