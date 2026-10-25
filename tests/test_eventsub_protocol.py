"""Recover malformed EventSub transport without retrying revoked permissions."""

import asyncio
from contextlib import redirect_stdout
import io
import json
import unittest
import urllib.error
from unittest.mock import AsyncMock, Mock, patch

import bot
import privacy
import rewards
from twitch_auth import TemporaryTwitchError


def packet(kind, payload):
    return json.dumps(dict(metadata=dict(message_type=kind), payload=payload))


def welcome(**changes):
    return packet("session_welcome", dict(session=dict(id="fixture-session", keepalive_timeout_seconds=10, **changes)))


def socket(*packets):
    ws = AsyncMock()
    ws.recv.side_effect = packets
    return ws


class EventSubProtocolTests(unittest.IsolatedAsyncioTestCase):
    def listener(self, callback=None):
        return rewards.RewardListener("client", "channel", None, callback or AsyncMock())

    async def test_corrupt_packets_retry_then_deliver_one_reward_without_losing_deduplication(self):
        event = dict(event=dict(id="redemption", user_login="viewer", user_id="123", user_input="Привет",
                                reward=dict(title="Вопрос ИИ")))
        broken = socket(welcome(), '{"PRIVATE_PROVIDER_PAYLOAD":')
        resumed = socket(welcome(), packet("notification", event), packet("notification", event), asyncio.CancelledError())
        listener = self.listener()
        listener._token = AsyncMock(return_value="fixture-token")
        output = io.StringIO()
        with patch.object(rewards, "connect", new_callable=AsyncMock, side_effect=[broken, resumed]) as connect, \
                patch.object(rewards, "subscribe") as subscribe, \
                patch.object(rewards.asyncio, "sleep", new_callable=AsyncMock) as sleep, redirect_stdout(output):
            with self.assertRaises(asyncio.CancelledError):
                await listener.run()
        self.assertEqual(connect.call_count, 2)
        self.assertEqual(subscribe.call_count, 2)
        sleep.assert_awaited_once_with(5)
        listener.on_question.assert_awaited_once_with("viewer", "123", "Привет", "redemption")
        broken.close.assert_awaited_once()
        resumed.close.assert_awaited_once()
        self.assertNotIn("PRIVATE_PROVIDER_PAYLOAD", output.getvalue())

    async def test_invalid_welcome_and_envelope_are_temporary_not_programming_errors(self):
        values = ["null", "[]", "{}", packet("session_welcome", {}),
                  packet("session_welcome", {"session": {"id": "fixture", "keepalive_timeout_seconds": True}}),
                  packet("session_welcome", {"session": {"id": 5}}), packet("session_keepalive", {})]
        for raw in values:
            with self.subTest(raw=raw), self.assertRaises(TemporaryTwitchError):
                await self.listener()._welcome(socket(raw))

    async def test_bad_notification_or_reconnect_closes_socket_and_is_retryable(self):
        packets = [packet("notification", {"event": []}), packet("notification", {"event": {"reward": []}}),
                   packet("notification", {"event": {"id": [], "reward": {"title": "Вопрос ИИ"}}}),
                   packet("session_reconnect", {}), packet("session_reconnect", {"session": {"reconnect_url": 42}})]
        for raw in packets:
            ws = socket(welcome(), raw)
            with self.subTest(raw=raw), patch.object(rewards, "connect", new_callable=AsyncMock, return_value=ws), \
                    patch.object(rewards, "subscribe"), self.assertRaises(TemporaryTwitchError):
                await self.listener()._listen("fixture-token")
            ws.close.assert_awaited_once()

    async def test_cancelled_reconnect_closes_both_sockets_and_does_not_retry(self):
        original = socket(welcome(), packet("session_reconnect", {"session": {"reconnect_url": "wss://eventsub.wss.twitch.tv/ws"}}))
        replacement = socket(asyncio.CancelledError())
        with patch.object(rewards, "connect", new_callable=AsyncMock, side_effect=[original, replacement]), patch.object(rewards, "subscribe"):
            with self.assertRaises(asyncio.CancelledError):
                await self.listener()._listen("fixture-token")
        original.close.assert_awaited_once()
        replacement.close.assert_awaited_once()

    async def test_revocation_remains_a_terminal_user_action_not_an_infinite_retry(self):
        for status in ("authorization_revoked", "user_removed", "version_removed"):
            ws = socket(welcome(), packet("revocation", {"subscription": {"status": status}}))
            listener = self.listener()
            listener._token = AsyncMock(return_value="fixture-token")
            with self.subTest(status=status), patch.object(rewards, "connect", new_callable=AsyncMock, return_value=ws) as connect, \
                    patch.object(rewards, "subscribe"), patch.object(rewards.asyncio, "sleep", new_callable=AsyncMock) as sleep:
                with self.assertRaises(rewards.SubscriptionRevokedError):
                    await listener.run()
                connect.assert_awaited_once()
                sleep.assert_not_awaited()
            ws.close.assert_awaited_once()

    async def test_callback_runtime_error_is_not_misclassified_as_twitch_outage(self):
        event = dict(event=dict(id="redemption", user_login="viewer", user_id="123", user_input="Привет",
                                reward=dict(title="Вопрос ИИ")))
        ws = socket(welcome(), packet("notification", event))
        listener = self.listener(AsyncMock(side_effect=RuntimeError("Application bug")))
        listener._token = AsyncMock(return_value="fixture-token")
        with patch.object(rewards, "connect", new_callable=AsyncMock, return_value=ws), patch.object(rewards, "subscribe"), \
                patch.object(rewards.asyncio, "sleep", new_callable=AsyncMock) as sleep:
            with self.assertRaisesRegex(RuntimeError, "Application bug"):
                await listener.run()
            sleep.assert_not_awaited()

    async def test_bot_retries_temporary_failure_but_stops_on_permanent_error_and_cancellation(self):
        instance = bot.Bot.__new__(bot.Bot)
        instance.cfg = dict(TWITCH_CHANNEL="channel")
        instance.autonomous = Mock()
        instance.connection = AsyncMock(side_effect=[TemporaryTwitchError("Temporary"), asyncio.CancelledError()])
        with patch.object(bot.asyncio, "sleep", new_callable=AsyncMock) as sleep:
            with self.assertRaises(asyncio.CancelledError):
                await instance.run()
            sleep.assert_awaited_once_with(5)
        instance.autonomous.close.assert_called_once()
        instance.autonomous.reset_mock()
        instance.connection = AsyncMock(side_effect=RuntimeError("Requires user action"))
        with patch.object(bot.asyncio, "sleep", new_callable=AsyncMock) as sleep:
            with self.assertRaisesRegex(RuntimeError, "Requires user action"):
                await instance.run()
            sleep.assert_not_awaited()
        instance.connection.assert_awaited_once()
        instance.autonomous.close.assert_called_once()


class ReviewDisagreementsTests(unittest.TestCase):
    def test_helix_server_errors_are_temporary_and_forbidden_is_terminal(self):
        for code in (501, 505, 599, 403):
            failure = urllib.error.HTTPError("https://api.twitch.tv/helix", code, "synthetic", {}, io.BytesIO())
            with self.subTest(code=code), patch("urllib.request.urlopen", side_effect=failure):
                with self.assertRaises(TemporaryTwitchError if code >= 500 else RuntimeError):
                    rewards.api_json("/eventsub/subscriptions", "client", "fixture-token", {})

    def test_non_object_helix_json_is_a_temporary_protocol_error(self):
        for raw in (b"[]", b"null", b'"PRIVATE_PROVIDER_PAYLOAD"'):
            with self.subTest(raw=raw), patch("urllib.request.urlopen", return_value=io.BytesIO(raw)):
                with self.assertRaises(TemporaryTwitchError) as caught:
                    rewards.api_json("/eventsub/subscriptions", "client", "fixture-token", {})
                self.assertNotIn("PRIVATE_PROVIDER_PAYLOAD", str(caught.exception))

    def test_given_steam_id_already_passes_but_incomplete_bin_list_must_not_weaken_protection(self):
        self.assertFalse(privacy.unsafe_question("Мой steamid 76561198012345678, добавь в друзья"))
        # Synthetic checksum-valid number excluded by the proposed BIN allowlist.
        self.assertTrue(privacy.contains_private_data("6200000000000005"))
        self.assertTrue(privacy.unsafe_question("Номер карты 6200000000000005"))
