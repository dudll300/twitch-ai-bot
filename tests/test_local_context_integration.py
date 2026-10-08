from safety import SafetyReview
from safety_fakes import stub_reviews
"""Publication and recovery integration; no real Twitch or AI calls."""

import asyncio
from concurrent.futures import Future
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

import autonomous as auto
import bot
from local_context import (DOCUMENT_NAME, LocalCard, LocalContextManager, LocalReply,
                           LocalResultError, LocalSettings, LocalSnapshot, save_document)
from participation import ContextDecision, Decision, RequestCancelled


class FakeExecutor:
    def __init__(self):
        self.kwargs = []
        self.futures = []

    def submit(self, *args, **kwargs):
        self.kwargs.append(kwargs)
        future = Future()
        self.futures.append(future)
        return future

    def shutdown(self, **kwargs):
        pass


class ContextIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        stub_reviews(self)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = 10000.0
        card = LocalCard("loss", "Полоса", ("полоса неудач",),
                         "Добродушная местная отсылка к игровым проигрышам.",
                         "Шутки зрителя о собственной серии проигрышей.",
                         "Не использовать при расстройстве.", enabled=True, allow_situational=True)
        save_document(self.root / DOCUMENT_NAME, LocalSnapshot(LocalSettings(enabled=True), (card,), ""))
        self.manager = LocalContextManager(self.root, clock=lambda: self.now)
        self.bundle = self.manager.bundle(self.manager.snapshot(), "полоса неудач")
        self.cfg = {"TWITCH_CHANNEL": "channel", "TWITCH_BOT_NAME": "helper",
                    "AI_MODEL": "primary", "AI_FALLBACK_MODELS": "backup",
                    "AI_API_KEY": "private-test-key", "AI_PROMPT": "Будь полезным",
                    "AI_CHAT_URL": "https://example.invalid/chat/completions"}
        with patch.object(bot, "ROOT", self.root):
            self.bot = bot.Bot(self.cfg)
        self.bot.local_context = self.manager
        self.bot.ai_router.local_context = self.manager
        self.addCleanup(self.bot.autonomous.close)
        self.writer = Mock()
        self.writer.drain = AsyncMock()

    async def test_reward_and_autonomous_share_only_successful_creative_usage(self):
        await self.bot.say(self.writer, "@viewer Полоса!", approval=SafetyReview("allowed", "@viewer Полоса!"), local_bundle=self.bundle, creative_card_id="loss")
        used = json.loads(self.manager.usage_path.read_text(encoding="utf-8"))
        self.assertEqual(used, {"version": 1, "uses": [{"card_id": "loss", "time": self.now}]})
        self.bot.last_sent = 0
        reserve = Mock()
        self.assertFalse(await self.bot.say_autonomous(self.writer, "Полоса!", lambda: True, reserve, approval=SafetyReview("allowed", "Полоса!"),
                                                       local_bundle=self.bundle, creative_card_id="loss"))
        reserve.assert_not_called()
        current = self.manager.bundle(self.manager.snapshot(), "Что значит полоса неудач?")
        self.assertEqual(current.candidate_ids, frozenset())
        self.assertEqual(tuple(card.id for card in current.direct), ("loss",))

    async def test_failed_send_and_cancel_release_creative_reservation_without_counting(self):
        for failure in (OSError("socket"), asyncio.CancelledError()):
            with self.subTest(failure=type(failure).__name__):
                self.writer.drain.side_effect = failure
                self.bot.last_sent = 0
                with self.assertRaises(type(failure)):
                    await self.bot.say(self.writer, "Полоса!", approval=SafetyReview("allowed", "Полоса!"), local_bundle=self.bundle, creative_card_id="loss")
                self.assertFalse(self.manager.usage_path.exists())
                self.assertTrue(self.manager.is_allowed(self.bundle, "loss"))
                with self.assertRaises(type(failure)):
                    await self.bot.say_autonomous(self.writer, "Полоса!", lambda: True, Mock(), approval=SafetyReview("allowed", "Полоса!"),
                                                 local_bundle=self.bundle, creative_card_id="loss")
                self.assertFalse(self.manager.usage_path.exists())
                self.assertTrue(self.manager.is_allowed(self.bundle, "loss"))

    async def test_autonomous_reservation_failure_does_not_consume_local_usage(self):
        with self.assertRaises(OSError):
            await self.bot.say_autonomous(self.writer, "Полоса!", lambda: True,
                Mock(side_effect=OSError("quota")), approval=SafetyReview("allowed", "Полоса!"), local_bundle=self.bundle, creative_card_id="loss")
        self.writer.write.assert_not_called()
        self.assertFalse(self.manager.usage_path.exists())
        self.assertTrue(self.manager.is_allowed(self.bundle, "loss"))

    async def test_competing_sends_cannot_both_publish_a_creative_reference(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow_drain():
            entered.set()
            await release.wait()
        self.writer.drain.side_effect = slow_drain
        first = asyncio.create_task(self.bot.say(self.writer, "Полоса!", approval=SafetyReview("allowed", "Полоса!"), local_bundle=self.bundle,
                                                  creative_card_id="loss"))
        await entered.wait()
        self.assertFalse(await self.bot.say_autonomous(self.writer, "Полоса!", lambda: True, Mock(), approval=SafetyReview("allowed", "Полоса!"),
                                                       local_bundle=self.bundle, creative_card_id="loss"))
        release.set()
        await first
        self.assertEqual(self.writer.write.call_count, 1)
        self.assertEqual(len(json.loads(self.manager.usage_path.read_text())["uses"]), 1)

    async def run_worker(self):
        queue = asyncio.Queue()
        queue.put_nowait(("viewer", "123", "Как пройти босса?", "reward-id"))
        task = asyncio.create_task(self.bot.worker(self.writer, queue))
        try:
            await asyncio.wait_for(queue.join(), 3)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_reward_live_disable_restores_plain_answer_once_without_metadata_in_history(self):
        calls = []
        def fake_ai(_cfg, _user, _question, _memory, _user_id, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                frozen = kwargs["local_bundle"]
                save_document(self.root / DOCUMENT_NAME,
                              replace(frozen.snapshot, settings=LocalSettings(enabled=False)))
                return LocalReply("Полоса!", frozen, "loss")
            self.assertTrue(kwargs["reject_local_service"])
            self.assertNotIn("local_bundle", kwargs)
            return "Попробуй уклониться после третьего удара."
        with patch.object(bot, "call_ai", side_effect=fake_ai):
            await self.run_worker()
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.writer.write.call_count, 1)
        published = self.writer.write.call_args.args[0].decode()
        self.assertIn("Попробуй уклониться", published)
        self.assertNotIn("Полоса", published)
        history_answer = self.bot.histories["123"][0][1]
        self.assertEqual(type(history_answer), str)
        self.assertFalse(self.manager.usage_path.exists())

    async def test_successful_reward_stores_only_text_and_usage_only_id_and_timestamp(self):
        reply = LocalReply("Отдохни между попытками, полоса скоро закончится.", self.bundle, "loss")
        with patch.object(bot, "call_ai", return_value=reply):
            await self.run_worker()
        answer = self.bot.histories["123"][0][1]
        self.assertEqual(type(answer), str)
        self.assertEqual(answer, str(reply))
        self.assertNotIn("creative_card_id", self.writer.write.call_args.args[0].decode())
        used = json.loads(self.manager.usage_path.read_text())["uses"][0]
        self.assertEqual(set(used), {"card_id", "time"})

    async def test_invalid_creative_output_recovers_once_and_preserves_network_fallback(self):
        seen = []
        def fake_ai(*args, **kwargs):
            seen.append(kwargs)
            if len(seen) == 1:
                raise LocalResultError("invalid optional output")
            if kwargs["model"] == "primary":
                raise bot.TemporaryAIError("unavailable")
            return "Обычный полезный ответ."
        with patch.object(bot, "call_ai", side_effect=fake_ai):
            result = self.bot.ai_router.ask(self.cfg, "viewer", "полоса неудач")
        self.assertEqual(result, "Обычный полезный ответ.")
        self.assertEqual([call["model"] for call in seen], ["primary", "primary", "backup"])
        self.assertTrue(seen[0]["local_bundle"].candidate_ids)
        for call in seen[1:]:
            self.assertFalse(call["local_bundle"].candidate_ids)
            self.assertEqual(call["local_bundle"].direct[0].id, "loss")
            self.assertTrue(call["reject_local_service"])

    async def test_second_invalid_result_stops_recovery_and_never_publishes_service_json(self):
        with patch.object(bot, "call_ai", side_effect=LocalResultError("invalid")) as request:
            with self.assertRaises(LocalResultError):
                self.bot.ai_router.ask(self.cfg, "viewer", "полоса неудач")
        self.assertEqual(request.call_count, 2)
        self.assertFalse(self.writer.write.called)

    async def test_direct_only_invalid_output_also_gets_one_ordinary_recovery(self):
        lease = self.manager.reserve_publish(self.bundle, "loss")
        self.manager.complete_publish(lease, success=True)
        with patch.object(bot, "call_ai", side_effect=[LocalResultError("invalid"), "Значение выражения."]) as request:
            answer = self.bot.ai_router.ask(self.cfg, "viewer", "Что значит полоса неудач?")
        self.assertEqual(answer, "Значение выражения.")
        self.assertEqual(request.call_count, 2)
        for call in request.call_args_list:
            self.assertFalse(call.kwargs["local_bundle"].candidate_ids)
            self.assertEqual(call.kwargs["local_bundle"].direct[0].id, "loss")

    async def test_dictionary_preparation_failure_preserves_reward_and_model_fallback(self):
        events = []
        self.manager.emit = events.append
        for method in ("snapshot", "bundle"):
            with self.subTest(method=method), patch.object(
                    self.manager, method, side_effect=RuntimeError("private-dictionary-data")), \
                 patch.object(bot, "call_ai", side_effect=[bot.TemporaryAIError("unavailable"),
                              "Полезный обычный ответ."]) as request:
                answer = self.bot.ai_router.ask(self.cfg, "viewer", "Как пройти босса?")
            self.assertEqual(answer, "Полезный обычный ответ.")
            self.assertEqual([call.kwargs["model"] for call in request.call_args_list], ["primary", "backup"])
            for call in request.call_args_list:
                self.assertNotIn("local_bundle", call.kwargs)
        self.assertTrue(events)
        self.assertNotIn("private-dictionary-data", repr(events))
        self.assertFalse(self.manager.usage_path.exists())

    def make_controller(self, mode="preview"):
        settings = replace(auto.AutoSettings(), enabled=True, mode=mode, min_messages=1, min_authors=1)
        auto.save_settings(self.root / "autonomous.json", settings)
        events = []
        controller = auto.Autonomous(self.cfg, self.root, clock=lambda: self.now,
                                    choose_delay=lambda a, b: a, emit=events.append,
                                    local_manager=self.manager)
        controller.executor = FakeExecutor()
        sender = AsyncMock(return_value=True)
        controller.connect(sender, lambda: False)
        self.addCleanup(controller.close)
        controller.receive(":viewer!viewer@host PRIVMSG #channel :Проиграл ещё раз, сам уже смеюсь")
        self.now += settings.settle_seconds
        controller.next_check = self.now
        return controller, sender, events

    async def test_preview_keeps_local_counters_untouched_but_regular_preview_limits_still_apply(self):
        controller, sender, events = self.make_controller()
        await controller.tick()
        basis = (controller.pending.messages[-1]["sequence"],)
        controller.executor.futures[0].set_result(
            ContextDecision("reply", "Ну и полоса!", "", basis, "reaction", self.bundle, "loss"))
        await controller.tick()
        self.assertEqual(events[-1]["status"], "preview")
        self.assertEqual(len(controller.quota), 1)
        sender.assert_not_called()
        self.assertFalse(self.manager.usage_path.exists())
        self.assertTrue(self.manager.is_allowed(self.bundle, "loss"))

    async def test_card_change_cancels_autonomous_result_and_second_http_stage(self):
        controller, sender, events = self.make_controller("publish")
        await controller.tick()
        pending = controller.pending
        kwargs = controller.executor.kwargs[0]
        save_document(self.root / DOCUMENT_NAME, replace(self.bundle.snapshot, settings=LocalSettings(enabled=False)))
        with self.assertRaises(RequestCancelled):
            kwargs["before_request"]()
        controller.executor.futures[0].set_result(Decision("reply", "Готовый ответ", "", (1,), "reaction"))
        await controller.tick()
        sender.assert_not_called()
        self.assertEqual(events[-1]["status"], "skipped")
        self.assertTrue(pending.cancel.is_set())
        self.assertFalse(self.manager.usage_path.exists())

    async def test_corrupt_dictionary_does_not_disable_ordinary_autonomous_requests(self):
        path = self.root / DOCUMENT_NAME
        path.write_text("{ broken", encoding="utf-8")
        controller, sender, events = self.make_controller()
        await controller.tick()
        kwargs = controller.executor.kwargs[0]
        self.assertTrue(kwargs["local_snapshot"].error)
        self.assertGreater(kwargs["before_request"](), 0)
        controller.executor.futures[0].set_result(Decision("reply", "Обычный ответ", "", (1,), "reaction"))
        await controller.tick()
        self.assertEqual(events[-1]["status"], "preview")
        self.assertEqual(path.read_text(encoding="utf-8"), "{ broken")


if __name__ == "__main__":
    unittest.main()
