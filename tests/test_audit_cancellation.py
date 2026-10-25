"""Cancellation reaches real reward routing and transport; HTTP alone is fake."""

import asyncio
import json
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import AsyncMock, Mock, patch
import urllib.error

import ai_client
import bot
from participation import RequestCancelled
from test_autonomous_privacy_regression import CFG, response


class RewardCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnected_reward_cannot_start_fallback_after_inflight_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            started, release, finished = Event(), Event(), Event()
            with patch.object(bot, "ROOT", root):
                instance = bot.Bot(dict(CFG, AI_FALLBACK_MODELS="backup,another"))
                writer = Mock(drain=AsyncMock())
                queue = asyncio.Queue()
                queue.put_nowait(("viewer", "123", "Как пройти босса?", "redemption"))
                models = []

                def http(request, **options):
                    models.append(json.loads(request.data)["model"])
                    if len(models) == 1:
                        started.set()
                        if not release.wait(5):
                            raise AssertionError("Test did not release the primary request")
                        raise urllib.error.URLError("synthetic disconnect")
                    return response("Запасной ответ")

                real_ask = instance.ai_router.ask

                def observe(*args, **kwargs):
                    try:
                        return real_ask(*args, **kwargs)
                    finally:
                        finished.set()

                with patch("urllib.request.urlopen", side_effect=http), patch.object(
                        instance.ai_router, "ask", side_effect=observe):
                    task = asyncio.create_task(instance.worker(writer, queue))
                    try:
                        self.assertTrue(await asyncio.to_thread(started.wait, 3))
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        release.set()
                        self.assertTrue(await asyncio.to_thread(finished.wait, 3))
                    finally:
                        release.set()
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        instance.autonomous.close()

                self.assertEqual(models, [CFG["AI_MODEL"]])
                writer.write.assert_not_called()
                record = instance.history.page()["rows"][0]
                self.assertEqual(record["status"], "cancelled")
                self.assertEqual(record["answer"], "")
                self.assertFalse(instance.histories)

    async def test_cancellation_during_payload_preparation_blocks_first_http(self):
        cancelled = Event()
        real_build = ai_client.build_messages

        def build(*args, **kwargs):
            messages = real_build(*args, **kwargs)
            cancelled.set()
            return messages

        with patch("ai_client.build_messages", side_effect=build), patch("urllib.request.urlopen") as http:
            with self.assertRaises(RequestCancelled):
                bot.AIModelRouter().ask(CFG, "viewer", "Как пройти босса?", cancelled=cancelled.is_set)
        http.assert_not_called()

    async def test_late_success_does_not_emit_generated_callback(self):
        cancelled, generated = Event(), Mock()

        def http(*args, **kwargs):
            cancelled.set()
            return response("Полезный ответ")

        with patch("urllib.request.urlopen", side_effect=http) as network:
            with self.assertRaises(RequestCancelled):
                bot.AIModelRouter().ask(CFG, "viewer", "Как пройти босса?",
                                       cancelled=cancelled.is_set, on_answer=generated)
        self.assertEqual(network.call_count, 1)
        generated.assert_not_called()


if __name__ == "__main__":
    unittest.main()
