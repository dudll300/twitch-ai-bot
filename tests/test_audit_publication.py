"""Exercise late publication cancellation through the real three-call pipeline."""

import asyncio
from dataclasses import replace
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

import autonomous as auto
import bot
from local_context import (DOCUMENT_NAME, LocalCard, LocalContextManager, LocalSettings,
                           LocalSnapshot, save_document)
import test_autonomous_privacy_regression as fixtures


class PublicationReservationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixtures.ControllerPrivacyRegression.setUp(self)
        self.bot = bot.Bot.__new__(bot.Bot)
        self.bot.cfg = dict(fixtures.CFG)
        self.bot._say_lock = asyncio.Lock()
        self.bot.last_sent = 0
        self.writer = Mock(drain=AsyncMock())
        self.controller.sender = lambda text, valid, reserve, **options: self.bot.say_autonomous(
            self.writer, text, valid, reserve, **options)

    def tearDown(self):
        fixtures.ControllerPrivacyRegression.tearDown(self)

    async def execute(self, **options):
        return await fixtures.ControllerPrivacyRegression.execute(self, **options)

    async def check_cancelled(self, change, reason):
        write = self.controller.write_quota

        def slow_write(quota, requests, text):
            write(quota, requests, text)
            if quota:
                change()

        with patch.object(self.controller, "write_quota", side_effect=slow_write):
            http, pending, record, event = await self.execute()
        self.assertEqual(http.call_count, 3)
        self.writer.write.assert_not_called()
        self.assertFalse(self.controller.quota)
        self.assertEqual(self.controller.last_text, "")
        persisted = json.loads((self.root / "autonomous-quota.json").read_text())
        self.assertEqual(persisted["times"], [])
        self.assertEqual(len(persisted["requests"]), 3)
        self.assertEqual(persisted["last_text"], "")
        self.assertEqual(record["status"], "cancelled")
        self.assertEqual(record["reason"], reason)
        self.assertFalse(event["twitch_attempted"])
        self.assertEqual(event["http_attempts"], (1, 1, 1))
        self.assertFalse(any(e["status"] in {"published", "preview"} for e in self.events))

    async def test_deadline_expiring_during_publication_quota_write_prevents_send(self):
        def expire():
            self.now += self.settings.reply_ttl_seconds + 1

        await self.check_cancelled(expire, "ttl_expired")

    async def test_disable_during_publication_quota_write_prevents_send(self):
        def disable():
            auto.save_settings(self.root / "autonomous.json", replace(self.settings, enabled=False))

        await self.check_cancelled(disable, "cancelled")

    async def test_preview_deadline_expiring_during_quota_write_prevents_display(self):
        self.settings = replace(self.settings, mode="preview")

        def expire():
            self.now += self.settings.reply_ttl_seconds + 1

        await self.check_cancelled(expire, "ttl_expired")

    async def test_healthy_creative_reservation_is_sent_once_and_completes_lease(self):
        card = LocalCard("drop", "Дроп", (), "Отсылка к редкому игровому предмету.",
                         "Получение редкого игрового меча.", "Не использовать в серьёзном разговоре.",
                         enabled=True, allow_situational=True)
        save_document(self.root / DOCUMENT_NAME,
                      LocalSnapshot(LocalSettings(enabled=True), (card,), ""))
        manager = LocalContextManager(self.root, clock=lambda: self.now)
        self.controller.local_manager = self.bot.local_context = manager
        self.controller.local_revision = manager.snapshot().revision
        http, pending, record, event = await self.execute(outputs=[
            fixtures.response(fixtures.asdict(fixtures.plan())),
            fixtures.response({"text": "Подожду такого же дропа", "creative_card_id": "drop"}),
            fixtures.response({"allowed": True, "reasons": [], "context_allowed": True, "context_reasons": []}),
        ])
        self.assertEqual(http.call_count, 3)
        self.writer.write.assert_called_once()
        self.assertEqual(record["status"], "sent")
        self.assertTrue(event["twitch_attempted"])
        self.assertEqual(len(self.controller.quota), 1)
        uses = json.loads(manager.usage_path.read_text())["uses"]
        self.assertEqual(len(uses), 1)
        self.assertEqual(uses[0]["card_id"], "drop")
        await self.controller.tick()
        self.writer.write.assert_called_once()

    async def test_failed_rollback_stops_future_requests_without_sending_or_exposing_error(self):
        write = self.controller.write_quota
        publication_started = False

        def broken_rollback(quota, requests, text):
            nonlocal publication_started
            if publication_started and not quota:
                raise OSError("PRIVATE_STORAGE_DETAIL")
            write(quota, requests, text)
            if quota:
                publication_started = True
                self.now += self.settings.reply_ttl_seconds + 1

        with patch.object(self.controller, "write_quota", side_effect=broken_rollback):
            http, pending, record, event = await self.execute()
        self.assertEqual(http.call_count, 3)
        self.writer.write.assert_not_called()
        self.assertTrue(self.controller.quota_error)
        self.assertFalse(self.controller.eligible())
        self.assertEqual(record["status"], "error")
        self.assertEqual(record["reason"], "quota_storage_error")
        self.assertFalse(event["twitch_attempted"])
        self.assertNotIn("PRIVATE_STORAGE_DETAIL", str(self.history.detail(record["id"])))
        self.assertNotIn("PRIVATE_STORAGE_DETAIL", str(self.events))


if __name__ == "__main__":
    unittest.main()
