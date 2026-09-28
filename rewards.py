"""Receive questions from one Twitch Channel Points reward over EventSub."""

import asyncio
import json
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path
from typing import Awaitable, Callable

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from twitch_auth import REDEMPTION_SCOPES, get_access_token, validate


API_URL = "https://api.twitch.tv/helix"
WEBSOCKET_URL = "wss://eventsub.wss.twitch.tv/ws"
REWARD_TITLE = "Вопрос ИИ"


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
            return json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.load(exc).get("message", "")
        except (ValueError, AttributeError):
            detail = ""
        raise RuntimeError(f"Twitch API вернул HTTP {exc.code}: {str(detail)[:200]}") from None


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
        message = json.loads(await asyncio.wait_for(websocket.recv(), timeout=15))
        if message.get("metadata", {}).get("message_type") != "session_welcome":
            raise RuntimeError("EventSub не прислал подтверждение подключения.")
        session = message["payload"]["session"]
        return session["id"], max(10, int(session.get("keepalive_timeout_seconds") or 10))

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
        if event.get("reward", {}).get("title", "").strip().casefold() != self.reward_title.casefold():
            return
        redemption_id = event.get("id", "")
        if not redemption_id or not self._remember_redemption(redemption_id):
            return
        login = event.get("user_login", "").lower()
        user_id = str(event.get("user_id", ""))
        question = " ".join(str(event.get("user_input", "")).split())[:400].rstrip()
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
                message = json.loads(await asyncio.wait_for(websocket.recv(), timeout=keepalive + 10))
                kind = message.get("metadata", {}).get("message_type")
                if kind == "notification":
                    await self.handle_notification(message.get("payload", {}))
                elif kind == "session_reconnect":
                    reconnect_url = message["payload"]["session"]["reconnect_url"]
                    replacement = await connect(reconnect_url, ping_interval=None)
                    try:
                        _, keepalive = await self._welcome(replacement)
                    except Exception:
                        await replacement.close()
                        raise
                    await websocket.close()
                    websocket = replacement
                    print("EventSub переподключён.", flush=True)
                elif kind == "revocation":
                    raise RuntimeError("Twitch отозвал подписку на награду; проверьте разрешения аккаунта владельца канала.")
        finally:
            await websocket.close()

    async def run(self) -> None:
        while True:
            try:
                await self._listen(await self._token())
                print("EventSub отключился; повтор через 5 секунд.", flush=True)
            except (OSError, TimeoutError, ConnectionClosed, InvalidHandshake) as exc:
                print(f"Соединение EventSub прервано: {type(exc).__name__}; повтор через 5 секунд.", flush=True)
            await asyncio.sleep(5)
