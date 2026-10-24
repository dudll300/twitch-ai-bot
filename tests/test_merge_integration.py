"""Regressions where current backend contracts meet Orbit worker telemetry."""

import asyncio
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

import ai_client
import bot
from runtime_events import PREFIX, UiEventEmitter, emit_ui, install_ui_events
import worker


CFG = dict(TWITCH_CHANNEL="channel", TWITCH_BOT_NAME="helper", TWITCH_CLIENT_ID="fixture-client",
           TWITCH_REWARD_TITLE="Fixture reward", AI_MODEL="fixture-model", AI_FALLBACK_MODELS="",
           AI_CHAT_URL="https://provider.invalid/v1/chat/completions", AI_API_KEY="fixture-private-key",
           AI_PROMPT="Fixture style")


def events(lines):
    return [json.loads(line[len(PREFIX):]) for line in lines if line.startswith(PREFIX)]


class WorkerTelemetryTests(unittest.TestCase):
    def tearDown(self):
        install_ui_events(None)

    def test_new_backend_error_branches_finish_ai_state_without_exposing_provider_data(self):
        cases = [(None, "length", ai_client.TruncatedAIError),
                 ({"text": "PRIVATE_PROVIDER_DATA"}, "stop", ai_client.TemporaryAIError),
                 ("<think>PRIVATE_PROVIDER_DATA</think>", "stop", ai_client.TemporaryAIError)]
        for content, finish, expected in cases:
            lines = []
            install_ui_events(UiEventEmitter(write=lines.append, secrets=(CFG["AI_API_KEY"],)))
            payload = {"choices": [{"finish_reason": finish, "message": {
                "content": content, "reasoning_content": "PRIVATE_INTERNAL_THOUGHT"}}]}
            with self.subTest(finish=finish, content=type(content).__name__), \
                    patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(payload).encode())) as http:
                with self.assertRaises(expected):
                    ai_client.request_completion(CFG, "o3-mini", [])
                self.assertEqual(http.call_count, 1)
            self.assertEqual([row["data"]["state"] for row in events(lines)], ["pending", "error"])
            self.assertNotIn("PRIVATE_PROVIDER_DATA", str(lines))
            self.assertNotIn("PRIVATE_INTERNAL_THOUGHT", str(lines))

    def test_startup_provenance_and_worker_lifecycle_coexist_once_and_emitter_is_cleared(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(bot, "ROOT", Path(temporary)):
            instance = bot.Bot(dict(CFG))
            output = io.StringIO()
            try:
                with patch.object(worker, "config", return_value=dict(CFG)), \
                        patch.object(worker, "Bot", return_value=instance), \
                        patch.object(instance, "run", new_callable=AsyncMock), \
                        patch.object(sys, "argv", ["worker.py"]), redirect_stdout(output):
                    self.assertEqual(worker.main(), 0)
                    emit_ui("chat", state="ready")  # No old worker emitter remains.
                text = output.getvalue()
                self.assertEqual(text.count("APP_BUILD version="), 1)
                runtime = events(text.splitlines())
                self.assertEqual([row["type"] for row in runtime], ["lifecycle", "settings"])
                self.assertEqual(runtime[0]["data"]["state"], "starting")
                self.assertEqual(runtime[0]["session"], instance.history.session)
                self.assertNotIn(CFG["AI_API_KEY"], text)
            finally:
                instance.autonomous.close()


class BotTelemetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root_patch = patch.object(bot, "ROOT", Path(self.temp.name))
        self.root_patch.start()
        self.instance = bot.Bot(dict(CFG))
        self.lines = []
        install_ui_events(UiEventEmitter(write=self.lines.append))

    def tearDown(self):
        install_ui_events(None)
        self.instance.autonomous.close()
        self.root_patch.stop()
        self.temp.cleanup()

    async def test_reader_preserves_tagged_ping_and_only_confirms_its_own_channel(self):
        reader = asyncio.StreamReader()
        reader.feed_data(("\r\n".join([
            "@a=b :helper!u JOIN #channel_extra",
            ":server 366 helper #channel_extra :End of names",
            ":other!u JOIN #channel",
            "@a=b :helper!u JOIN #channel",
            ":server 366 helper #channel :End of names",
            ":server ROOMSTATE #channel",
            "@a=b :server PING :server",
        ]) + "\r\n").encode())
        reader.feed_eof()
        writer = AsyncMock()
        writer.write, writer.close = Mock(), Mock()
        listener = AsyncMock()
        async def listen():
            await asyncio.Event().wait()
        listener.run.side_effect = listen
        with patch.object(bot, "get_access_token", return_value="fixture-token"), \
                patch.object(bot, "RewardListener", return_value=listener), \
                patch.object(bot.asyncio, "open_connection", return_value=(reader, writer)):
            await asyncio.wait_for(self.instance.connection(), 3)
        ready = [row for row in events(self.lines) if row["type"] == "chat" and row["data"]["state"] == "ready"]
        self.assertEqual(len(ready), 3)
        self.assertIn(b"PONG :server\r\n", [call.args[0] for call in writer.write.call_args_list])

    async def test_reward_keeps_one_completion_local_review_and_one_set_of_publication_events(self):
        queue = asyncio.Queue()
        queue.put_nowait(("viewer", "123", "Пошути про игру", "fixture-redemption"))
        payload = {"choices": [{"message": {"content": "Эта игра опять решила проверить нервы."}}]}
        self.instance.say = AsyncMock()
        with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(payload).encode())) as http:
            task = asyncio.create_task(self.instance.worker(None, queue))
            try:
                await asyncio.wait_for(queue.join(), 3)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(http.call_count, 1)  # No semantic AI review for rewards.
        self.instance.say.assert_awaited_once()
        runtime = events(self.lines)
        self.assertEqual([row["data"]["phase"] for row in runtime if row["type"] == "queue"], ["started", "finished"])
        self.assertEqual(len([row for row in runtime if row["type"] == "reply"]), 1)
        rows = self.instance.history.page(kind="reward")["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "sent")
        review = self.instance.history.safety_recent()[0]
        self.assertFalse(review["ai_attempted"])
