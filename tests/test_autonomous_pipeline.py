from safety_fakes import stub_reviews
"""Replay cancellation, quota and snapshot boundaries without AI or Twitch access."""

from dataclasses import asdict, replace
from concurrent.futures import ThreadPoolExecutor
import io
import json
import tempfile
from pathlib import Path
from threading import Event
import unittest
from unittest.mock import patch

import autonomous as auto
from participation import Plan
from test_autonomous import FakeExecutor, line


def response(value):
    return io.BytesIO(json.dumps({"choices": [{"message": {"content": json.dumps(value)}}]}).encode())


class PipelineControllerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        stub_reviews(self)
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.now, self.monotonic = 10000., 5000.
        self.settings = replace(auto.AutoSettings(), enabled=True, pause_seconds=0,
                                check_min_seconds=0, check_max_seconds=0)
        auto.save_settings(self.root / "autonomous.json", self.settings)
        self.cfg = {"TWITCH_CHANNEL": "channel", "TWITCH_BOT_NAME": "helper",
                    "AI_MODEL": "exact/primary", "AI_FALLBACK_MODELS": "never",
                    "AI_PROMPT": "Характер", "AI_API_KEY": "test-secret",
                    "AI_CHAT_URL": "https://example.test/v1/chat/completions"}
        self.events = []
        self.controller = auto.Autonomous(self.cfg, self.root, clock=lambda: self.now,
            monotonic_clock=lambda: self.monotonic, choose_delay=lambda low, high: low, emit=self.events.append)
        self.executor = FakeExecutor()
        self.controller.executor = self.executor
        self.controller.connect(None, lambda: False)

    def tearDown(self):
        self.controller.close()
        self.temporary.cleanup()

    async def start(self):
        for index in range(3):
            self.controller.receive(line(f"viewer{index}", f"Победа над боссом {index}", f"id=msg{index};user-id={index+1}"))
        self.now += 3
        self.monotonic += 3
        await self.controller.tick()
        self.assertIsNotNone(self.controller.pending)

    def execute(self, *, plan=None, after_selection=None, generation_failure=None):
        pending = self.controller.pending
        basis = pending.messages[-1]["sequence"]
        plan = plan or Plan("reply", (basis,), (basis,), "viewer2", "reaction", "Поздравить с победой")
        calls = []
        def http(request, timeout):
            calls.append((json.loads(request.data), timeout))
            if len(calls) == 1:
                if after_selection:
                    after_selection()
                return response(asdict(plan))
            if generation_failure:
                raise generation_failure
            return response({"text": "Поздравляю с победой!"})
        function, *args = self.executor.calls[-1]
        with patch("ai_client.urllib.request.urlopen", side_effect=http):
            try:
                result = function(*args, **self.executor.kwargs[-1])
            except Exception as exc:
                pending.future.set_exception(exc)
            else:
                pending.future.set_result(result)
        return calls

    async def test_reply_uses_two_counted_calls_and_silent_uses_one(self):
        await self.start()
        calls = self.execute()
        await self.controller.tick()
        self.assertEqual(len(calls), 2)
        self.assertEqual([entry[0]["model"] for entry in calls], ["exact/primary"] * 2)
        self.assertEqual(len(self.controller.requests), 2)
        self.assertEqual(self.events[-1]["status"], "preview")
        persisted = json.loads((self.root / "autonomous-quota.json").read_text(encoding="utf-8"))
        self.assertEqual(len(persisted["requests"]), 2)
        self.assertEqual(len(persisted["times"]), 1)
        self.assertIn(3, self.controller.consumed_ids)
        self.controller.receive(line("another", "Ещё новый вопрос"))
        self.now += 3
        await self.controller.tick()
        calls = self.execute(plan=Plan("silent"))
        await self.controller.tick()
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(self.controller.requests), 3)

    async def test_reward_between_stages_closes_opportunity_and_skips_second_call(self):
        await self.start()
        calls = self.execute(after_selection=self.controller.interrupt)
        await self.controller.tick()
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(self.controller.requests), 1)
        self.assertEqual(set(self.controller.consumed_ids), {1, 2, 3})
        self.assertFalse(self.controller.quota)

    async def test_settings_change_without_refresh_blocks_generation(self):
        await self.start()
        def edit():
            auto.save_settings(self.root / "autonomous.json", replace(self.settings, autonomous_prompt="Новый текст"))
        calls = self.execute(after_selection=edit)
        await self.controller.tick()
        self.assertEqual(len(calls), 1)
        self.assertFalse(self.controller.quota)

    async def test_close_between_stages_never_starts_generator(self):
        await self.start()
        calls = self.execute(after_selection=self.controller.close)
        self.assertEqual(len(calls), 1)
        self.assertFalse(self.controller.quota)

    async def test_cancel_before_first_http_does_not_spend_quota(self):
        await self.start()
        self.controller.interrupt()
        calls = self.execute()
        await self.controller.tick()
        self.assertFalse(calls)
        self.assertFalse(self.controller.requests)

    async def test_one_remaining_call_allows_selection_but_no_generation(self):
        auto.save_settings(self.root / "autonomous.json", replace(self.settings, request_hourly_limit=1))
        self.controller.refresh()
        await self.start()
        calls = self.execute()
        await self.controller.tick()
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(self.controller.requests), 1)
        self.assertFalse(self.controller.quota)
        self.assertEqual(self.events[-1]["status"], "skipped")
        self.assertLess(self.controller.next_check, self.now + 120)

    async def test_silent_is_possible_with_one_remaining_call(self):
        auto.save_settings(self.root / "autonomous.json", replace(self.settings, request_hourly_limit=1))
        self.controller.refresh()
        await self.start()
        calls = self.execute(plan=Plan("silent"))
        await self.controller.tick()
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.events[-1]["status"], "silent")

    async def test_failure_of_generator_is_still_counted_and_no_private_error_is_logged(self):
        await self.start()
        calls = self.execute(generation_failure=TimeoutError("test-secret private body"))
        await self.controller.tick()
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(self.controller.requests), 2)
        self.assertFalse(self.controller.quota)
        self.assertEqual(self.events[-1]["status"], "error")
        self.assertEqual(self.events[-1]["reason"], "autonomous_timeout")
        self.assertNotIn("private body", str(self.events))
        self.assertNotIn("test-secret", str(self.events))

    async def test_shared_deadline_reduces_second_timeout(self):
        await self.start()
        def elapsed():
            self.now += 19
            self.monotonic += 19
        calls = self.execute(after_selection=elapsed)
        await self.controller.tick()
        self.assertEqual(calls[0][1], 20)
        self.assertEqual(calls[1][1], 8)
        self.assertEqual(self.events[-1]["status"], "preview")

    async def test_expired_deadline_and_wall_clock_rollback_both_stop_second_call(self):
        await self.start()
        def elapsed():
            self.monotonic += 31
            self.now -= 100
        calls = self.execute(after_selection=elapsed)
        await self.controller.tick()
        self.assertEqual(len(calls), 1)
        self.assertFalse(self.controller.quota)

    async def test_selected_older_anchor_tightens_generator_and_publication_deadline(self):
        self.controller.receive(line("old", "Победил босса"))
        self.now += 15
        self.monotonic += 15
        await self.start()
        calls = self.execute(plan=Plan("reply", (1,), (1,), "old", "reaction", "Поздравить"))
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][1], 12)
        # Newer rows in the batch and a rollback cannot revive the old anchor.
        self.monotonic += 13
        self.now -= 100
        await self.controller.tick()
        self.assertFalse(self.controller.quota)

    async def test_selected_anchor_can_expire_during_selection_despite_newer_rows(self):
        self.controller.receive(line("old", "Победил босса"))
        self.now += 15
        self.monotonic += 15
        await self.start()
        def elapsed():
            self.monotonic += 13
            self.now -= 100
        calls = self.execute(plan=Plan("reply", (1,), (1,), "old", "reaction", "Поздравить"), after_selection=elapsed)
        self.assertEqual(len(calls), 1)
        self.assertFalse(self.controller.quota)

    async def test_slow_quota_write_does_not_launch_http_after_deadline(self):
        await self.start()
        write = self.controller.write_quota
        def slow_write(*args):
            write(*args)
            self.now += 31
            self.monotonic += 31
        with patch.object(self.controller, "write_quota", side_effect=slow_write):
            calls = self.execute()
        await self.controller.tick()
        self.assertFalse(calls)
        self.assertFalse(self.controller.quota)
        self.assertEqual(len(self.controller.requests), 1)  # Reservation stays conservative after an expired attempt.

    async def test_counter_write_failure_blocks_http_and_fails_closed(self):
        await self.start()
        with patch.object(auto, "write_json", side_effect=OSError("private path")):
            calls = self.execute()
        await self.controller.tick()
        self.assertFalse(calls)
        self.assertTrue(self.controller.quota_error)
        self.assertNotIn("private path", str(self.events))
        self.assertEqual(self.events[-1]["status"], "error")
        self.assertIn("счётчик", self.events[-1]["reason"])

    async def test_http_and_publication_reservations_preserve_both_counters_across_threads(self):
        await self.start()
        entered, release = Event(), Event()
        write = self.controller.write_quota
        def delayed_write(quota, requests, text):
            if requests and not quota:
                entered.set()
                if not release.wait(2):
                    raise TimeoutError("test barrier")
            write(quota, requests, text)
        with patch.object(self.controller, "write_quota", side_effect=delayed_write), ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(self.executor.kwargs[-1]["before_request"])
            try:
                self.assertTrue(entered.wait(1))
                second = workers.submit(self.controller.reserve, "Опубликованная реплика", (3,))
            finally:
                release.set()
            self.assertGreater(first.result(timeout=2), 0)
            second.result(timeout=2)
        persisted = json.loads((self.root / "autonomous-quota.json").read_text(encoding="utf-8"))
        self.assertEqual(len(persisted["requests"]), 1)
        self.assertEqual(len(persisted["times"]), 1)
        self.assertEqual(persisted["last_text"], "Опубликованная реплика")

    async def test_one_snapshot_includes_profile_and_memory_mutations(self):
        self.controller.profiles = [{"login": "viewer2", "user_id": "3", "enabled": True,
            "prompt": "Первоначальная личная инструкция", "aliases": []}]
        self.controller.memory_data = {"streamer": {"facts": ["Играем в хоррор"], "jokes": [], "avoid": []},
            "viewers": [{"login": "viewer2", "user_id": "3", "facts": ["Первоначальный факт"], "jokes": [], "avoid": []}]}
        await self.start()
        def mutate():
            self.controller.profiles[0]["prompt"] = "Изменённая инструкция"
            self.controller.memory_data["viewers"][0]["facts"][0] = "Изменённый факт"
            self.controller.cfg["AI_PROMPT"] = "Изменённый характер"
        calls = self.execute(after_selection=mutate)
        payload = str(calls[1][0]["messages"])
        self.assertIn("Первоначальная личная инструкция", payload)
        self.assertIn("Первоначальный факт", payload)
        self.assertNotIn("Изменённ", payload)

    async def test_already_used_basis_cannot_be_reanswered_with_new_wording(self):
        await self.start()
        self.execute()
        await self.controller.tick()
        self.controller.receive(line("other", "Продолжение разговора"))
        self.now += 3
        await self.controller.tick()
        calls = self.execute(plan=Plan("reply", (3, 4), (3, 4), "viewer2", "reaction", "Ещё раз поздравить"))
        await self.controller.tick()
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(self.controller.quota), 1)
        self.assertEqual(self.events[-1]["status"], "skipped")


class IRCMetadataTests(unittest.TestCase):
    def test_reply_links_ids_mentions_and_author_identity_survive_buffering(self):
        buffer = auto.ChatBuffer()
        tags = "id=reply1;user-id=123;reply-parent-msg-id=parent1;reply-parent-user-id=456;reply-parent-user-login=Other;reply-thread-parent-msg-id=root1"
        self.assertTrue(buffer.add_irc(line("viewer", "@Other согласен, @OTHER!", tags), "channel", "helper", 10, auto.AutoSettings()))
        row = buffer.messages[-1]
        self.assertEqual(row["message_id"], "reply1")
        self.assertEqual(row["reply_parent_id"], "parent1")
        self.assertEqual(row["reply_parent_user_id"], "456")
        self.assertEqual(row["reply_parent_login"], "other")
        self.assertEqual(row["thread_id"], "root1")
        self.assertEqual(row["mentions"], ("other",))
        self.assertEqual(row["user_id"], "123")

