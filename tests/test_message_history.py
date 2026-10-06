import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
from threading import Event
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

import autonomous
import bot
from local_context import LocalResultError
from message_history import HistoryReadError, MessageHistory
from participation import Plan


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = MessageHistory(self.root, secrets=('secret"value',), warn=Mock())

    def tearDown(self):
        self.temp.cleanup()

    def test_reopen_preserves_full_unicode_and_delivery_after_session_log_reset(self):
        question = "Многострочный запрос\n" + "вопрос " * 100
        answer = "Ответ\n" + "слово " * 100
        record = self.store.add("reward", "generating", channel="channel", viewer="viewer", question=question)
        self.store.update(record, "generated", model="provider/exact-id", answer=answer)
        self.store.update(record, "sent", sent_text="@viewer " + answer)
        (self.root / "bot-session.log").write_text("")
        reopened = MessageHistory(self.root).detail(record)
        self.assertEqual(reopened["question"], question)
        self.assertEqual(reopened["answer"], answer)
        self.assertEqual(reopened["sent_text"], "@viewer " + answer)
        self.assertEqual(reopened["model"], "provider/exact-id")
        self.assertEqual([event["status"] for event in reopened["events"]], ["generating", "generated", "sent"])

    def test_silent_has_no_context_even_if_caller_supplies_one(self):
        record = self.store.add("autonomous", "silent", action="silent", reason="offtopic",
                                context={"conversation": ["private chat"]})
        self.store.update(record, "silent", context={"conversation": ["other private chat"]})
        self.assertIsNone(self.store.detail(record)["context"])
        self.assertNotIn(b"private chat", self.store.path.read_bytes())

    def test_secrets_are_redacted_before_storage_in_every_field_and_nested_context(self):
        secret = 'secret"value'
        record = self.store.add("autonomous", "generated", model=secret, answer=secret,
            reason="oauth:token123", context={"conversation": [{"text": secret}],
                                              "access_token": "never-retain", "prompt": json.dumps(secret)})
        self.store.update(record, "error", reason="Bearer token456 " + secret)
        text = json.dumps(self.store.detail(record), ensure_ascii=False)
        for value in (secret, "never-retain", "token123", "token456"):
            self.assertNotIn(value, text)
        self.assertIn("ключ скрыт", text)

    def test_late_generated_result_does_not_undo_cancellation(self):
        record = self.store.add("reward", "generating", question="Question")
        self.store.update(record, "cancelled", reason="Stopped")
        self.store.update(record, "generated", answer="Late answer", model="exact")
        row = self.store.detail(record)
        self.assertEqual(row["status"], "cancelled")
        self.assertEqual(row["answer"], "Late answer")
        self.assertEqual(row["reason"], "Stopped")
        self.assertEqual(row["sent_text"], "")

    def test_cursor_pages_remain_stable_when_new_messages_arrive_and_search_handles_russian(self):
        for index in range(8):
            self.store.add("reward", "sent", viewer="viewer", question=f"Привет {index}")
        first = self.store.page(limit=3)
        self.store.add("reward", "sent", question="new")
        older = self.store.page(before=first["rows"][-1]["seq"], limit=3)
        self.assertFalse(set(row["id"] for row in first["rows"]) & set(row["id"] for row in older["rows"]))
        self.assertEqual(len(self.store.page(search="ПРИВЕТ")["rows"]), 8)
        self.assertEqual(self.store.page(search="' OR 1=1 --")["rows"], [])

    def test_two_writers_and_reopened_reader_do_not_lose_records(self):
        second = MessageHistory(self.root, warn=Mock())
        self.store.add("reward", "sent", question="initial")
        def insert(index):
            return (self.store if index % 2 else second).add("reward", "sent", question=str(index))
        with ThreadPoolExecutor(max_workers=4) as executor:
            identifiers = list(executor.map(insert, range(30)))
        self.assertNotIn(None, identifiers)
        self.assertEqual(len(MessageHistory(self.root).page()["rows"]), 31)

    def test_corrupt_database_is_not_overwritten_and_write_failure_is_nonfatal(self):
        original = b"not a database"
        self.store.path.write_bytes(original)
        self.assertIsNone(self.store.add("reward", "sent", answer="one"))
        self.assertIsNone(self.store.add("reward", "sent", answer="two"))
        self.store.warn.assert_called_once()
        self.assertEqual(self.store.path.read_bytes(), original)
        with self.assertRaises(HistoryReadError):
            self.store.page()

    def test_read_only_empty_history_does_not_create_files(self):
        self.assertEqual(self.store.page(), {"rows": [], "more": False})
        self.assertIsNone(self.store.detail("missing"))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_unserializable_context_cannot_escape_into_generation(self):
        self.assertIsNone(self.store.add("autonomous", "generated", context={"value": float("nan")}))
        self.store.warn.assert_called_once()


class RewardHistoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.instance = bot.Bot.__new__(bot.Bot)
        self.instance.cfg = {"TWITCH_CHANNEL": "channel", "AI_MODEL": "primary",
                             "AI_FALLBACK_MODELS": "backup", "AI_API_KEY": "private-key"}
        self.instance.history = MessageHistory(self.root, secrets=("private-key",), warn=Mock())
        self.instance.ai_router = bot.AIModelRouter()
        self.instance.memory = None
        self.instance.histories = {}
        self.instance.autonomous = Mock()
        self.instance.say = AsyncMock()

    def tearDown(self):
        self.temp.cleanup()

    async def work(self, *, read=True):
        queue = asyncio.Queue()
        queue.put_nowait(("viewer", "123", "Question", "redemption"))
        task = asyncio.create_task(self.instance.worker(None, queue))
        try:
            await asyncio.wait_for(queue.join(), 3)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return self.instance.history.page()["rows"] if read else None

    async def test_reward_keeps_question_exact_fallback_model_and_actual_sent_text(self):
        with patch.object(bot, "call_ai", side_effect=[bot.TemporaryAIError("failed"), "Answer"]):
            rows = await self.work()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["question"], rows[0]["model"], rows[0]["status"]), ("Question", "backup", "sent"))
        self.assertEqual(rows[0]["answer"], "Answer")
        self.assertEqual(rows[0]["sent_text"], "@viewer Answer")
        self.assertEqual(self.instance.histories["123"][0], ("Question", "Answer"))

    async def test_failed_send_keeps_generated_answer_without_claiming_delivery(self):
        self.instance.say.side_effect = ConnectionResetError("Connection failed private-key")
        with patch.object(bot, "call_ai", return_value="Unsent answer"), patch("builtins.print"):
            rows = await self.work()
        self.assertEqual(rows[0]["status"], "send_error")
        self.assertEqual(rows[0]["answer"], "Unsent answer")
        self.assertEqual(rows[0]["sent_text"], "")
        self.assertNotIn("private-key", rows[0]["reason"])
        self.assertEqual(self.instance.histories, {})

    async def test_ai_error_retains_question_and_successful_service_notice(self):
        with patch.object(bot, "call_ai", side_effect=RuntimeError("Unavailable")), patch("builtins.print"):
            rows = await self.work()
        self.assertEqual(rows[0]["status"], "error")
        self.assertEqual(rows[0]["answer"], "")
        self.assertIn("попробуй позже", rows[0]["sent_text"])
        self.assertEqual(self.instance.histories, {})

    async def test_cancelled_worker_retains_late_generation_without_sending_it(self):
        started, release = Event(), Event()
        def reply(*args, **kwargs):
            started.set()
            release.wait(2)
            return "Late answer"
        queue = asyncio.Queue()
        queue.put_nowait(("viewer", "123", "Question", "redemption"))
        with patch.object(bot, "call_ai", side_effect=reply), patch("builtins.print"):
            task = asyncio.create_task(self.instance.worker(None, queue))
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            release.set()
            for _ in range(100):
                rows = self.instance.history.page()["rows"]
                if rows and rows[0]["answer"] == "Late answer":
                    break
                await asyncio.sleep(.01)
        self.assertEqual(rows[0]["status"], "cancelled")
        self.assertEqual(rows[0]["answer"], "Late answer")
        self.instance.say.assert_not_awaited()

    async def test_regeneration_retains_both_candidates_and_only_one_sent_answer(self):
        self.instance.say.side_effect = [LocalResultError("stale reference"), None]
        with patch.object(bot, "call_ai", side_effect=["First candidate", "Replacement"]), patch("builtins.print"):
            rows = await self.work()
        self.assertEqual([(row["answer"], row["status"]) for row in rows],
                         [("Replacement", "sent"), ("First candidate", "skipped")])
        self.assertEqual(self.instance.histories["123"][0][1], "Replacement")

    async def test_logging_failure_does_not_change_delivery_or_ai_memory(self):
        with patch("message_history.sqlite3.connect", side_effect=OSError("Disk full")), \
             patch.object(bot, "call_ai", return_value="Answer"), patch("builtins.print"):
            await self.work(read=False)
        self.instance.say.assert_awaited_once()
        self.assertEqual(self.instance.histories["123"][0][1], "Answer")
        self.instance.history.warn.assert_called_once()


class Executor:
    def __init__(self):
        self.calls = []

    def submit(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return Future()

    def shutdown(self, **kwargs):
        pass


class AutonomousHistoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = 10000.
        self.store = MessageHistory(self.root, secrets=("private-key",), warn=Mock())
        self.settings = replace(autonomous.AutoSettings(), enabled=True)
        autonomous.save_settings(self.root / "autonomous.json", self.settings)
        cfg = {"TWITCH_CHANNEL": "channel", "TWITCH_BOT_NAME": "helper", "AI_MODEL": "exact-model",
               "AI_API_KEY": "private-key", "AI_PROMPT": "Be useful."}
        self.controller = autonomous.Autonomous(cfg, self.root, clock=lambda: self.now,
            monotonic_clock=lambda: self.now, choose_delay=lambda a, b: a, history_store=self.store, emit=Mock())
        self.controller.executor = Executor()
        async def sender(text, valid, reserve):
            if not valid():
                return False
            reserve()
            return True
        self.sender = AsyncMock(side_effect=sender)
        self.controller.connect(self.sender, lambda: False)

    def tearDown(self):
        self.controller.close()
        self.temp.cleanup()

    async def generate(self, plan, response=None):
        for index, text in enumerate(("Unrelated private topic", "Another unrelated topic", "How to beat the boss?"), 1):
            self.controller.receive(f":viewer{index}!viewer@host PRIVMSG #channel :{text}")
        self.now += 3
        await self.controller.tick()
        pending = self.controller.pending
        args, kwargs = self.controller.executor.calls[-1]
        responses = [json.dumps(plan)]
        if response is not None:
            responses.append(json.dumps({"text": response}))
        with patch("participation.request_completion", side_effect=responses) as api:
            result = args[0](*args[1:], **kwargs)
        pending.future.set_result(result)
        return pending, api.call_count

    async def test_preview_retains_only_selected_conversation_and_exact_request_instructions(self):
        plan = {"action": "reply", "conversation": [3], "basis": [3], "target": "viewer3",
                "reason": "answer", "intent": "Help with boss"}
        _, calls = await self.generate(plan, "Dodge the attack.")
        await self.controller.tick()
        row = MessageHistory(self.root).detail(self.store.page()["rows"][0]["id"])
        self.assertEqual(calls, 2)
        self.assertEqual(row["status"], "preview")
        self.assertEqual(row["model"], "exact-model")
        self.assertEqual(row["answer"], "@viewer3 Dodge the attack.")
        self.assertEqual([message["sequence"] for message in row["context"]["conversation"]], [3])
        self.assertNotIn("Unrelated private topic", json.dumps(row["context"]))
        self.assertIn("Be useful.", json.dumps(row["context"]))
        self.sender.assert_not_awaited()

    async def test_selector_silence_never_persists_chat_context(self):
        _, calls = await self.generate({"action": "silent", "conversation": [], "basis": [],
                                      "target": "", "reason": "offtopic", "intent": ""})
        await self.controller.tick()
        row = self.store.detail(self.store.page()["rows"][0]["id"])
        self.assertEqual(calls, 1)
        self.assertEqual(row["status"], "silent")
        self.assertIsNone(row["context"])
        self.assertNotIn("Unrelated private topic", json.dumps(row))

    async def test_generator_silence_also_drops_selected_context(self):
        await self.generate({"action": "reply", "conversation": [3], "basis": [3], "target": "viewer3",
                             "reason": "answer", "intent": "Help"}, "")
        await self.controller.tick()
        row = self.store.detail(self.store.page()["rows"][0]["id"])
        self.assertEqual(row["status"], "silent")
        self.assertIsNone(row["context"])

    async def test_silent_result_remains_silent_when_reward_interrupts_before_consumption(self):
        await self.generate({"action": "silent", "conversation": [], "basis": [],
                             "target": "", "reason": "offtopic", "intent": ""})
        self.controller.interrupt()
        await self.controller.tick()
        row = self.store.detail(self.store.page(status="silent")["rows"][0]["id"])
        self.assertEqual(row["status"], "silent")
        self.assertIsNone(row["context"])
        self.sender.assert_not_awaited()

    async def test_cancelled_generated_reply_keeps_text_and_context_but_is_not_sent(self):
        await self.generate({"action": "reply", "conversation": [3], "basis": [3], "target": "viewer3",
                             "reason": "answer", "intent": "Help"}, "Dodge the attack.")
        self.controller.interrupt()
        await self.controller.tick()
        row = self.store.detail(self.store.page()["rows"][0]["id"])
        self.assertEqual(row["status"], "skipped")
        self.assertIn("Dodge", row["answer"])
        self.assertEqual(len(row["context"]["conversation"]), 1)
        self.sender.assert_not_awaited()

    async def test_successful_publication_is_recorded_after_sender_returns(self):
        autonomous.save_settings(self.root / "autonomous.json", replace(self.settings, mode="publish"))
        self.controller.refresh()
        await self.generate({"action": "reply", "conversation": [3], "basis": [3], "target": "viewer3",
                             "reason": "answer", "intent": "Help"}, "Dodge the attack.")
        await self.controller.tick()
        row = self.store.page()["rows"][0]
        self.assertEqual(row["status"], "sent")
        self.assertEqual(row["sent_text"], "@viewer3 Dodge the attack.")
        self.sender.assert_awaited_once()

    async def test_failed_autonomous_send_keeps_candidate_and_context(self):
        autonomous.save_settings(self.root / "autonomous.json", replace(self.settings, mode="publish"))
        self.controller.refresh()
        self.sender.side_effect = ConnectionResetError()
        await self.generate({"action": "reply", "conversation": [3], "basis": [3], "target": "viewer3",
                             "reason": "answer", "intent": "Help"}, "Dodge the attack.")
        await self.controller.tick()
        row = self.store.detail(self.store.page()["rows"][0]["id"])
        self.assertEqual(row["status"], "error")
        self.assertEqual(row["sent_text"], "")
        self.assertIn("Dodge", row["answer"])
        self.assertEqual(len(row["context"]["conversation"]), 1)

    async def test_logging_error_does_not_change_preview_or_ai_request_count(self):
        with patch("message_history.sqlite3.connect", side_effect=OSError("Disk full")):
            _, calls = await self.generate({"action": "reply", "conversation": [3], "basis": [3], "target": "viewer3",
                                           "reason": "answer", "intent": "Help"}, "Dodge the attack.")
            await self.controller.tick()
        self.assertEqual(calls, 2)
        self.assertEqual(self.controller.emit.call_args.args[0]["status"], "preview")
        self.sender.assert_not_awaited()
        self.store.warn.assert_called_once()

    async def test_reward_interrupt_during_history_write_prevents_ai_submission(self):
        for index in range(3):
            self.controller.receive(f":viewer{index}!viewer@host PRIVMSG #channel :A new question {index}")
        self.now += 3
        original_add = self.store.add
        def interrupted_add(*args, **kwargs):
            result = original_add(*args, **kwargs)
            self.controller.interrupt()
            return result
        with patch.object(self.store, "add", side_effect=interrupted_add):
            await self.controller.tick()
        self.assertIsNone(self.controller.pending)
        self.assertEqual(self.controller.executor.calls, [])
        self.assertEqual(self.controller.requests, [])
        row = self.store.detail(self.store.page()["rows"][0]["id"])
        self.assertEqual(row["status"], "cancelled")
        self.assertIsNone(row["context"])
