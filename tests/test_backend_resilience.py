"""Failure-path checks without Twitch accounts or paid API calls."""

import asyncio
import http.client
import io
import json
import unittest
import urllib.error
from contextlib import redirect_stdout
from unittest.mock import patch

import ai_client
import autonomous
import bot
import rewards
import testing


CFG = {"AI_MODEL": "primary", "AI_FALLBACK_MODELS": "backup",
       "AI_API_KEY": "sk-private-test", "AI_CHAT_URL": "https://example.com/v1/chat/completions"}


def response(content):
    return io.BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())


class BackendResilienceTests(unittest.TestCase):
    def test_interrupted_ai_response_uses_backup_without_logging_partial_body(self):
        for failure in (http.client.IncompleteRead(b"sk-private-test", 20),
                        ConnectionResetError("sk-private-test"),
                        http.client.RemoteDisconnected("sk-private-test")):
            with self.subTest(failure=type(failure).__name__):
                requests = []
                def request(req, timeout):
                    requests.append(json.loads(req.data)["model"])
                    if len(requests) == 1:
                        raise failure
                    return response("OK")
                log = io.StringIO()
                with patch.object(ai_client.urllib.request, "urlopen", side_effect=request), redirect_stdout(log):
                    self.assertEqual(bot.AIModelRouter().ask(CFG, "viewer", "Question"), "OK")
                self.assertEqual(requests, ["primary", "backup"])
                self.assertNotIn(CFG["AI_API_KEY"], log.getvalue())

    def test_http_error_with_broken_body_still_reports_status(self):
        class BrokenBody(io.BytesIO):
            def read(self, *args):
                raise http.client.IncompleteRead(b"sk-private-test", 20)
        failure = urllib.error.HTTPError(CFG["AI_CHAT_URL"], 503, "error", {}, BrokenBody())
        with patch.object(ai_client.urllib.request, "urlopen", side_effect=failure):
            with self.assertRaisesRegex(ai_client.TemporaryAIError, "HTTP 503") as error:
                ai_client.call_ai(CFG, "viewer", "Question")
        self.assertNotIn(CFG["AI_API_KEY"], str(error.exception))

    def test_catalog_interruption_has_understandable_safe_error(self):
        for failure in (http.client.IncompleteRead(b"sk-private-test", 20),
                        ConnectionResetError("sk-private-test")):
            with self.subTest(failure=type(failure).__name__), patch.object(
                    testing.urllib.request, "urlopen", side_effect=failure):
                with self.assertRaisesRegex(RuntimeError, "Каталог недоступен") as error:
                    testing.fetch_models(testing.credentials("https://example.com/v1", CFG["AI_API_KEY"]))
                self.assertNotIn(CFG["AI_API_KEY"], str(error.exception))

    def test_comparison_continues_after_interrupted_response_without_fallback(self):
        auth = testing.credentials("https://example.com/v1", CFG["AI_API_KEY"])
        snapshot = testing.make_snapshot(auth, ["primary", "backup"], "Question", "Draft", "viewer")
        calls = []
        def request(req, timeout):
            payload = json.loads(req.data)
            calls.append(payload)
            if payload["model"] == "primary":
                raise http.client.IncompleteRead(b"sk-private-test", 20)
            return response("OK")
        with patch.object(ai_client.urllib.request, "urlopen", side_effect=request):
            results = [testing.test_model(snapshot, model) for model in snapshot.models]
        self.assertTrue(results[0].error)
        self.assertEqual(results[1].answer, "OK")
        self.assertEqual([row["model"] for row in calls], ["primary", "backup"])
        self.assertEqual(calls[0]["messages"], calls[1]["messages"])
        self.assertNotIn(CFG["AI_API_KEY"], results[0].error)

    def test_control_characters_cannot_escape_into_chat_or_questions(self):
        dirty = "Hello\x01ACTION\x00world\x7f\x9b\r\nfriend"
        self.assertEqual(ai_client.clean_question(dirty), "Hello ACTION world friend")
        with patch.object(ai_client.urllib.request, "urlopen", return_value=response(dirty)):
            self.assertEqual(ai_client.call_ai(CFG, "viewer", "Question"), "Hello ACTION world friend")

    def test_primary_repeated_as_backup_never_enters_cooldown(self):
        router = bot.AIModelRouter()
        with patch.object(bot, "call_ai", side_effect=bot.TemporaryAIError("Unavailable")) as call:
            for _ in range(4):
                with self.assertRaises(bot.TemporaryAIError):
                    router.ask({**CFG, "AI_FALLBACK_MODELS": "primary,primary"}, "viewer", "Question")
        self.assertEqual(call.call_count, 4)
        self.assertEqual(router.primary_disabled_until, 0)

    def test_histories_keep_recent_viewers_and_only_successful_pairs(self):
        async def scenario():
            instance = bot.Bot.__new__(bot.Bot)
            instance.cfg, instance.memory, instance.histories = CFG, {}, {}
            class Router:
                def ask(self, *args):
                    return "OK"
            instance.ai_router = Router()
            async def say(*args):
                pass
            instance.say = say
            queue = asyncio.Queue()
            task = asyncio.create_task(instance.worker(None, queue))
            try:
                for user_id in ("1", "2", "1", "3"):
                    queue.put_nowait(("viewer" + user_id, user_id, "Question", user_id))
                    await asyncio.wait_for(queue.join(), 2)
                self.assertEqual(set(instance.histories), {"1", "3"})
                self.assertEqual(len(instance.histories["1"]), 2)
                async def failed_send(*args):
                    raise ConnectionResetError()
                instance.say = failed_send
                queue.put_nowait(("viewer4", "4", "Unsent", "4"))
                await asyncio.wait_for(queue.join(), 2)
                self.assertEqual(set(instance.histories), {"1", "3"})
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        with patch.object(bot, "MAX_HISTORY_VIEWERS", 2, create=True):
            asyncio.run(scenario())

    def test_autonomous_reply_never_returns_echoed_api_key(self):
        # Quotes also check redaction after decoding the JSON string.
        cfg = {**CFG, "AI_API_KEY": 'sk-"private"'}
        content = json.dumps({"action": "joke", "text": "Hello " + cfg["AI_API_KEY"]})
        with patch.object(ai_client.urllib.request, "urlopen", return_value=response(content)):
            action, text = autonomous.request_decision(cfg, [], autonomous.AutoSettings())
        self.assertEqual(action, "joke")
        self.assertNotIn(cfg["AI_API_KEY"], text)
        self.assertIn("[ключ скрыт]", text)

    def test_autonomous_request_remains_on_primary_without_fallback(self):
        calls = []
        def request(req, timeout):
            calls.append(json.loads(req.data))
            raise http.client.IncompleteRead(b"sk-private-test", 20)
        with patch.object(ai_client.urllib.request, "urlopen", side_effect=request):
            with self.assertRaises(ai_client.TemporaryAIError):
                autonomous.request_decision(CFG, [], autonomous.AutoSettings())
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["model"], "primary")
        self.assertEqual(calls[0]["max_tokens"], 512)
        self.assertFalse(calls[0]["stream"])

    def test_damaged_twitch_response_is_retryable(self):
        for failure in (io.BytesIO(b"{broken-json"),
                        http.client.IncompleteRead(b"private-token", 20)):
            with self.subTest(failure=type(failure).__name__):
                kwargs = {"return_value": failure} if isinstance(failure, io.BytesIO) else {"side_effect": failure}
                with patch.object(rewards.urllib.request, "urlopen", **kwargs):
                    with self.assertRaises(rewards.TemporaryTwitchError) as error:
                        rewards.api_json("/eventsub/subscriptions", "client", "private-token", {})
                self.assertNotIn("private-token", str(error.exception))

    def test_eventsub_transient_api_errors_retry_but_auth_errors_stop(self):
        for code in (429, 500, 502, 503, 504):
            with self.subTest(code=code):
                failure = urllib.error.HTTPError("https://api.twitch.tv/helix", code, "error", {},
                                                 io.BytesIO(b'{"message":"sk-private-test"}'))
                with patch.object(rewards.urllib.request, "urlopen", side_effect=failure):
                    with self.assertRaises(OSError) as error:
                        rewards.api_json("/eventsub/subscriptions", "client", CFG["AI_API_KEY"], {})
                self.assertNotIn(CFG["AI_API_KEY"], str(error.exception))
        for code in (400, 401, 403):
            with self.subTest(code=code), patch.object(rewards.urllib.request, "urlopen", side_effect=
                    urllib.error.HTTPError("https://api.twitch.tv/helix", code, "error", {}, io.BytesIO())):
                with self.assertRaisesRegex(RuntimeError, "HTTP " + str(code)):
                    rewards.api_json("/eventsub/subscriptions", "client", "token", {})

    def test_eventsub_retries_after_transient_subscription_failure(self):
        async def scenario():
            listener = rewards.RewardListener("client", "channel", None, None)
            attempts = []
            async def token():
                return "token"
            async def listen(_token):
                attempts.append(_token)
                if len(attempts) == 1:
                    rewards.api_json("/eventsub/subscriptions", "client", "token", {})
                raise asyncio.CancelledError()
            async def sleep(seconds):
                self.assertEqual(seconds, 5)
            listener._token, listener._listen = token, listen
            failure = urllib.error.HTTPError("https://api.twitch.tv/helix", 503, "error", {}, io.BytesIO())
            with patch.object(rewards.urllib.request, "urlopen", side_effect=failure), patch.object(
                    rewards.asyncio, "sleep", side_effect=sleep):
                with self.assertRaises(asyncio.CancelledError):
                    await listener.run()
            self.assertEqual(attempts, ["token", "token"])
        asyncio.run(scenario())
