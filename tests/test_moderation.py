"""Moderator deletions invalidate every stage; no real IRC or HTTP."""
import asyncio
from concurrent.futures import Future
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import AsyncMock, Mock, patch

import autonomous as auto
import bot
from irc import moderation_event
from local_context import DOCUMENT_NAME, LocalCard, LocalReply, LocalSettings, LocalSnapshot, save_document
from participation import Decision, RequestCancelled
from safety import SafetyReview, SAFETY_REFUSAL


def line(mid="a", uid="1", login="viewer", text="Сложный босс, но победа близко", timestamp="1000"):
    return f"@id={mid};user-id={uid};tmi-sent-ts={timestamp} :{login}!x PRIVMSG #channel :{text}"


def clear(mid="a", channel="channel"):
    return f"@target-msg-id={mid} :tmi.twitch.tv CLEARMSG #{channel} :private deleted body"


class Executor:
    def __init__(self):
        self.kwargs, self.futures = [], []
    def submit(self, *args, **kwargs):
        self.kwargs.append(kwargs)
        future = Future()
        self.futures.append(future)
        return future
    def shutdown(self, **kwargs):
        pass


class ModerationBufferTests(unittest.TestCase):
    def setUp(self):
        self.buffer = auto.ChatBuffer()
        self.settings = auto.AutoSettings()
        self.buffer.add_irc(line(), "channel", "bot", 0, self.settings)
        self.buffer.add_irc(line("b", "2", "other", "Та же тема, ещё попытка"), "channel", "bot", 0, self.settings)

    def moderate(self, message):
        event = moderation_event(message, "channel")
        if event:
            self.buffer.moderate(event, 1)
        return event

    def test_clearmsg_exact_identity_tombstones_replay_and_unknown_id(self):
        self.moderate(clear())
        self.assertEqual([row["message_id"] for row in self.buffer.messages], ["b"])
        self.assertFalse(self.buffer.add_irc(line(), "channel", "bot", 2, self.settings))
        self.moderate(clear())
        self.moderate(clear("unknown"))
        self.assertEqual(len(self.buffer.messages), 1)
        self.moderate(clear("future"))
        self.assertFalse(self.buffer.add_irc(line("future", "3", "new"), "channel", "bot", 2, self.settings))

    def test_user_id_priority_rename_and_login_fallback(self):
        self.moderate("@target-user-id=1 :tmi.twitch.tv CLEARCHAT #channel :other")
        self.assertEqual([row["user_id"] for row in self.buffer.messages], ["2"])
        self.moderate(":tmi.twitch.tv CLEARCHAT #channel :OTHER")
        self.assertFalse(self.buffer.messages)

    def test_full_clear_new_context_and_timestamp_replays(self):
        self.moderate("@tmi-sent-ts=1000 :tmi.twitch.tv CLEARCHAT #channel")
        self.assertFalse(self.buffer.messages)
        self.assertFalse(self.buffer.add_irc(line("old", timestamp="999"), "channel", "bot", 2, self.settings))
        self.assertTrue(self.buffer.add_irc(line("new", timestamp="1001"), "channel", "bot", 2, self.settings))

    def test_older_moderation_event_does_not_weaken_timestamp_cutoff(self):
        self.moderate("@target-user-id=1;tmi-sent-ts=2000 :tmi.twitch.tv CLEARCHAT #channel :viewer")
        self.moderate("@target-user-id=1;tmi-sent-ts=1000 :tmi.twitch.tv CLEARCHAT #channel :viewer")
        self.assertFalse(self.buffer.add_irc(line("late", timestamp="1500"), "channel", "bot", 2, self.settings))
        self.assertTrue(self.buffer.add_irc(line("new", timestamp="2001"), "channel", "bot", 2, self.settings))

    def test_other_channel_command_in_text_missing_and_malformed_tags(self):
        self.assertIsNone(self.moderate(clear(channel="other")))
        self.assertIsNone(self.moderate(line("c", text="CLEARMSG #channel :a")))
        self.assertEqual(len(self.buffer.messages), 2)
        for raw in [":tmi.twitch.tv CLEARMSG #channel :text", "@target-user-id=bad :tmi.twitch.tv CLEARCHAT #channel :viewer",
                    "@target-msg-id=a;target-msg-id=b :tmi.twitch.tv CLEARMSG #channel :text"]:
            self.assertEqual(moderation_event(raw, "channel").kind, "uncertain")

    def test_tombstones_bounded_expiring_and_reset(self):
        for index in range(1100):
            self.moderate(clear(f"id-{index}"))
        self.assertLessEqual(len(self.buffer.deleted), 1000)
        self.buffer.add_irc(line("after", "7", "new"), "channel", "bot", 302, self.settings)
        self.assertFalse(self.buffer.deleted)
        self.moderate(clear("again"))
        self.buffer.reset_session()
        self.assertFalse(self.buffer.deleted)


class ModerationPipelineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = 10.
        settings = replace(auto.AutoSettings(), enabled=True, mode="publish", min_messages=1, min_authors=1)
        auto.save_settings(self.root / "autonomous.json", settings)
        self.cfg = {"TWITCH_CHANNEL": "channel", "TWITCH_BOT_NAME": "bot", "AI_MODEL": "chosen",
                    "AI_API_KEY": "test-secret", "AI_CHAT_URL": "https://example.invalid/v1/chat/completions"}
        self.events = []
        self.controller = auto.Autonomous(self.cfg, self.root, clock=lambda: self.now,
                                          choose_delay=lambda a, b: a, emit=self.events.append)
        self.addCleanup(self.controller.close)
        self.executor = Executor()
        self.controller.executor = self.executor
        self.sender = AsyncMock(return_value=True)
        self.controller.connect(self.sender, lambda: False)
        self.controller.receive(line())

    async def start(self):
        self.now += 3
        await self.controller.tick()
        return self.controller.pending

    async def test_deletion_before_selection_never_calls_model(self):
        self.controller.receive(clear())
        await self.start()
        self.assertFalse(self.executor.futures)

    async def test_mid_selector_and_generator_cancel_late_result_and_next_stage(self):
        for mode in ("publish", "preview"):
            self.controller.settings = replace(self.controller.settings, mode=mode)
            pending = await self.start()
            self.assertGreater(self.executor.kwargs[-1]["before_request"](), 0)
            self.controller.receive(clear(pending.messages[-1]["message_id"]))
            with self.assertRaises(RequestCancelled):
                self.executor.kwargs[-1]["before_request"]()
            pending.future.set_result(Decision("reply", "Поздравляю!", "viewer", (1,), "reaction"))
            await self.controller.tick()
            self.sender.assert_not_called()
            self.assertFalse(self.controller.quota)
            self.assertFalse(self.controller.recent_replies)
            self.assertTrue(pending.cancel.is_set())
            self.controller.receive(line("new", text="Новый разговор, ещё одна победа", timestamp="2000"))
            self.controller.next_check = self.now

    async def test_deleted_neighbor_in_snapshot_invalidates_selector_even_when_not_basis(self):
        self.controller.receive(line("b", "2", "other", "Совсем соседний разговор"))
        pending = await self.start()
        self.controller.receive(clear("b"))
        pending.future.set_result(Decision("reply", "Полезный ответ", "viewer", (1,), "answer"))
        await self.controller.tick()
        self.sender.assert_not_called()

    async def test_deletion_during_review_preserves_http_attempt_without_publication_or_memory(self):
        pending = await self.start()
        pending.future.set_result(Decision("reply", "Поздравляю!", "viewer", (1,), "reaction"))
        started, release = Event(), Event()
        def http(*args, **kwargs):
            started.set()
            release.wait(2)
            return io.BytesIO(b'{"choices":[{"message":{"content":"{\\"allowed\\":true,\\"reasons\\":[]}"}}]}')
        with patch("urllib.request.urlopen", side_effect=http):
            tick = asyncio.create_task(self.controller.tick())
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            self.controller.receive(clear())
            release.set()
            await tick
        self.sender.assert_not_called()
        self.assertEqual(len(self.controller.requests), 1)
        self.assertFalse(self.controller.quota)
        self.assertFalse(self.controller.recent_replies)
        self.assertEqual(self.events[-1]["reason"], "moderation_cancelled")

    async def test_unknown_deletion_and_new_messages_do_not_cancel_unaffected_request(self):
        pending = await self.start()
        generation = self.controller.generation
        self.controller.receive(clear("unrelated"))
        self.controller.receive(line("b", "2", "other", "Продолжай, всё получится"))
        self.assertEqual(self.controller.generation, generation)
        self.assertFalse(pending.cancel.is_set())

    async def test_final_writer_gate_and_moderation_no_memory(self):
        with patch.object(bot, "ROOT", self.root):
            instance = bot.Bot(self.cfg)
        self.addCleanup(instance.autonomous.close)
        writer = Mock()
        writer.drain = AsyncMock()
        self.controller.sender = lambda text, valid, reserve, **kw: instance.say_autonomous(writer, text, valid, reserve, **kw)
        pending = await self.start()
        pending.future.set_result(Decision("reply", "Поздравляю!", "viewer", (1,), "reaction"))
        def review(cfg, model, text, **options):
            self.controller.receive(clear())
            return SafetyReview("allowed", text)
        with patch("autonomous.review_candidate", side_effect=review):
            await self.controller.tick()
        writer.write.assert_not_called()
        self.assertFalse(self.controller.quota)
        self.assertFalse(self.controller.recent_replies)

    async def test_review_is_third_counted_http_attempt_and_budget_is_mandatory(self):
        for budget in (3, 2):
            settings = replace(self.controller.settings, request_hourly_limit=budget)
            auto.save_settings(self.root / "autonomous.json", settings)
            self.controller.refresh()
            self.controller.requests = []
            self.controller.receive(line(f"budget-{budget}", text=f"Новый босс номер {budget}, справимся"))
            self.controller.next_check = self.now
            pending = await self.start()
            self.executor.kwargs[-1]["before_request"]()
            self.executor.kwargs[-1]["before_request"]()
            sequence = pending.messages[-1]["sequence"]
            pending.future.set_result(Decision("reply", f"Победа уже близко, попытка {budget}!", "viewer", (sequence,), "reaction"))
            with patch("urllib.request.urlopen", return_value=io.BytesIO(b'{"choices":[{"message":{"content":"{\\"allowed\\":true,\\"reasons\\":[]}"}}]}')) as network:
                await self.controller.tick()
            self.assertEqual(len(self.controller.requests), budget)
            self.assertEqual(network.call_count, 1 if budget == 3 else 0)
            self.assertEqual(self.sender.call_count, 1)  # Only the budget=3 candidate.

    async def test_deletion_during_preview_history_write_prevents_display_and_quota(self):
        auto.save_settings(self.root / "autonomous.json", replace(self.controller.settings, mode="preview"))
        self.controller.refresh()
        self.controller.receive(line())
        pending = await self.start()
        pending.future.set_result(Decision("reply", "Поздравляю!", "viewer", (1,), "reaction"))
        async def finish(pending, status, **kwargs):
            if status == "preview":
                self.controller.receive(clear())
        with patch("autonomous.review_candidate", side_effect=lambda cfg, model, text, **kw: SafetyReview("allowed", text)), patch.object(self.controller, "finish_history", side_effect=finish):
            await self.controller.tick()
        self.assertFalse(self.controller.quota)
        self.assertFalse(any(event["status"] == "preview" for event in self.events))

    async def test_moderation_clears_reply_provenance_after_buffer_expiry(self):
        self.controller.remember_reply("Прошлый ответ", target="viewer", user_id="1", source_rows=self.controller.buffer.messages)
        self.controller.buffer.messages.clear()
        self.controller.receive(clear())
        self.assertFalse(self.controller.recent_replies)

    async def test_expiry_during_evaluation_keeps_attempt_but_never_publishes(self):
        pending = await self.start()
        pending.future.set_result(Decision("reply", "Поздравляю!", "viewer", (1,), "reaction"))
        def http(*args, **kwargs):
            self.now += self.controller.settings.reply_ttl_seconds + 1
            return io.BytesIO(b'{"choices":[{"message":{"content":"{\\"allowed\\":true,\\"reasons\\":[]}"}}]}')
        with patch("urllib.request.urlopen", side_effect=http):
            await self.controller.tick()
        self.assertEqual(len(self.controller.requests), 1)
        self.sender.assert_not_called()
        self.assertFalse(self.controller.quota)

    async def test_paid_priority_during_evaluation_never_publishes(self):
        pending = await self.start()
        pending.future.set_result(Decision("reply", "Поздравляю!", "viewer", (1,), "reaction"))
        def http(*args, **kwargs):
            self.controller.paid_busy = lambda: True
            return io.BytesIO(b'{"choices":[{"message":{"content":"{\\"allowed\\":true,\\"reasons\\":[]}"}}]}')
        with patch("urllib.request.urlopen", side_effect=http):
            await self.controller.tick()
        self.assertEqual(len(self.controller.requests), 1)
        self.sender.assert_not_called()
        self.assertFalse(self.controller.quota)

    async def test_deletion_after_write_does_not_restore_deleted_context(self):
        from message_history import MessageHistory
        store = MessageHistory(self.root)
        self.controller.history_store = store
        pending = await self.start()
        pending.history_ref[1] = {"conversation": list(pending.messages)}
        pending.future.set_result(Decision("reply", "Поздравляю!", "viewer", (1,), "reaction"))
        async def written_then_deleted(text, valid, reserve, **kwargs):
            self.assertTrue(valid())
            reserve()
            self.controller.receive(clear())
            self.controller.receive(clear())  # Duplicate events keep the cause.
            return True
        self.controller.sender = written_then_deleted
        with patch("autonomous.review_candidate", side_effect=lambda cfg, model, text, **kw: SafetyReview("allowed", text)):
            await self.controller.tick()
        detail = store.detail(pending.history_ref[0])
        self.assertEqual(detail["status"], "sent")
        self.assertEqual(detail["reason"], "moderation_cancelled")
        self.assertIsNone(detail["context"])
        self.assertEqual(len(self.controller.quota), 1)  # Already written stays counted.
        self.assertFalse(self.controller.recent_replies)


class PublicationIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = {"TWITCH_CHANNEL": "channel", "TWITCH_BOT_NAME": "bot", "AI_MODEL": "chosen",
                    "AI_FALLBACK_MODELS": "backup", "AI_API_KEY": "test-secret", "AI_CHAT_URL": "https://example.invalid/v1/chat/completions"}
        card = LocalCard("card", "Мем", meaning="Значение", situations="Игровая неудача", allow_situational=True)
        save_document(self.root / DOCUMENT_NAME, LocalSnapshot(LocalSettings(enabled=True), (card,), ""))
        with patch.object(bot, "ROOT", self.root):
            self.bot = bot.Bot(self.cfg)
        self.addCleanup(self.bot.autonomous.close)
        self.writer = Mock()
        self.writer.drain = AsyncMock()

    async def worker(self):
        queue = asyncio.Queue()
        queue.put_nowait(("viewer", "1", "Как пройти босса?", "redeem"))
        task = asyncio.create_task(self.bot.worker(self.writer, queue))
        try:
            await asyncio.wait_for(queue.join(), 3)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_reward_local_block_cannot_fallback_regenerate_spend_card_or_store_candidate(self):
        bundle = self.bot.local_context.bundle(self.bot.local_context.snapshot(), "")
        answer = LocalReply("Запрещённая ссылка https://example.com", bundle, "card")
        with patch("bot.call_ai", return_value=answer) as generator, patch("safety.review_candidate") as reviewer:
            await self.worker()
        self.assertEqual(generator.call_count, 1)
        reviewer.assert_not_called()
        published = self.writer.write.call_args[0][0].decode()
        self.assertIn(SAFETY_REFUSAL, published)
        self.assertNotIn("example.com", published)
        self.assertFalse(self.bot.histories)
        self.assertFalse(self.bot.local_context.usage_path.exists())
        self.assertNotIn("example.com", json.dumps(self.bot.history.page(), ensure_ascii=False))

    async def test_final_text_change_missing_review_and_unsafe_source_never_write(self):
        for text, approval in [("Изменённый текст", SafetyReview("allowed", "Другой текст")),
                               ("Обычный кандидат", None), ("example.com", SafetyReview("allowed", "example.com"))]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                await self.bot.say(self.writer, text, approval=approval)
        self.writer.write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
