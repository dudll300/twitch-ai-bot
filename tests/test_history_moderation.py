"""Durable moderation exclusions through real IRC parsing and SQLite queries."""

import asyncio
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import autonomous
from irc import ModerationEvent
from message_history import MessageHistory
from recent_context import for_reward


class HistoryModerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = 10000
        self.store = MessageHistory(self.root, clock=lambda: self.now, warn=lambda _: None)

    def seed(self, *, viewer='viewer', uid='123', message='source-message', channel='channel', legacy=False):
        context = dict(basis=[1], conversation=[dict(sequence=1, author=viewer, user_id=uid,
                       message_id=message, text='REMOVED_SCENE_MARKER')])
        return self.store.add('autonomous', 'sent', channel=channel, viewer=viewer,
                              viewer_id='' if legacy else uid, mode='publish', action='reply',
                              sent_text=f'@{viewer} Публичный ответ о боссе', context=context)

    def read(self, store=None, channel='channel'):
        return for_reward(store or self.store, (), channel, 'renamed', '123', self.now+1)

    def test_clearmsg_fences_immediately_and_survives_restart_without_deleting_archive(self):
        record = self.seed()
        other = self.seed(message='other-message', channel='otherchannel')
        before = self.store.detail(record)
        controller = autonomous.Autonomous(dict(TWITCH_CHANNEL='channel', TWITCH_BOT_NAME='helper'),
            self.root, clock=lambda: self.now, emit=lambda _: None, history_store=self.store)
        self.addCleanup(controller.close)
        controller.connect(None, lambda: False)
        self.assertIn('REMOVED_SCENE_MARKER', str(self.read()))
        controller.receive('@target-msg-id=source-message :tmi.twitch.tv CLEARMSG #channel :deleted')
        self.assertEqual(self.read(), ())
        self.assertTrue(self.read(channel='otherchannel'))
        self.assertTrue(self.store.flush_moderation())
        self.assertEqual(self.read(MessageHistory(self.root)), ())
        after = self.store.detail(record)
        for field in ('status', 'sent_text', 'context'):
            self.assertEqual(after[field], before[field])
        self.assertEqual(after['events'][-1]['status'], 'context_invalidated')
        self.assertEqual(len(self.store.detail(other)['events']), 1)

    def test_precise_message_and_id_matching_with_legacy_and_login_fallback(self):
        cases = [
            (ModerationEvent('message', message_id='source-message'), {}, False),
            (ModerationEvent('message', message_id='unrelated'), {}, True),
            (ModerationEvent('user', user_id='123', login='renamed'), {}, False),
            (ModerationEvent('user', user_id='456', login='viewer'), {}, True),
            (ModerationEvent('user', user_id='123'), dict(legacy=True), False),
            (ModerationEvent('user', login='viewer'), {}, False),
            (ModerationEvent('all'), {}, False),
            (ModerationEvent('uncertain'), {}, False),
        ]
        for index, (event, options, remains) in enumerate(cases):
            with self.subTest(event=event):
                channel = f'channel{index}'
                self.seed(channel=channel, **options)
                self.store.note_moderation(channel, event)
                self.assertTrue(self.store.flush_moderation())
                self.assertEqual(bool(self.read(channel=channel)), remains)

    def test_repeated_events_do_not_duplicate_and_future_publications_are_eligible(self):
        record = self.seed()
        event = ModerationEvent('user', user_id='123')
        for _ in range(2):
            self.store.note_moderation('channel', event)
            self.assertTrue(self.store.flush_moderation())
        self.assertEqual(len(self.store.detail(record)['events']), 2)
        self.now += 10
        self.seed(message='new-message')
        self.assertEqual(len(self.read(MessageHistory(self.root))), 1)

    def test_storage_failure_keeps_context_fenced_and_retry_is_durable(self):
        self.seed()
        self.store.note_moderation('channel', ModerationEvent('all'))
        with patch('message_history.sqlite3.connect', side_effect=sqlite3.OperationalError('locked')):
            self.assertFalse(self.store.flush_moderation())
        self.assertEqual(self.read(), ())
        self.assertTrue(self.store.flush_moderation())
        self.assertEqual(self.read(MessageHistory(self.root)), ())

    def test_moderation_arriving_during_read_discards_its_stale_result(self):
        self.seed()
        original = self.store._read
        def interrupted(operation):
            result = original(operation)
            self.store.note_moderation('channel', ModerationEvent('all'))
            self.store.flush_moderation()
            return result
        with patch.object(self.store, '_read', side_effect=interrupted):
            self.assertEqual(self.read(), ())

    def test_corrupt_provenance_is_excluded_without_blocking_other_records(self):
        record = self.seed()
        other = self.seed(uid='456', message='unrelated')
        with closing(sqlite3.connect(self.store.path)) as connection, connection:
            connection.execute("UPDATE records SET context='[broken' WHERE id=?", (record,))
        self.store.note_moderation('channel', ModerationEvent('message', message_id='source-message'))
        self.assertTrue(self.store.flush_moderation())
        self.assertEqual(self.store.detail(other)['events'][-1]['status'], 'sent')
        with closing(sqlite3.connect(self.store.path)) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM events WHERE record_id=? AND status='context_invalidated'", (record,)).fetchone()[0], 1)

    def test_backlog_coalesces_and_never_becomes_a_permanent_user_ban(self):
        self.seed()
        for index in range(1100):
            self.store.note_moderation('channel', ModerationEvent('message', message_id=str(index)))
        self.assertLess(len(self.store._moderations), 1024)
        self.assertTrue(self.store.flush_moderation())
        self.assertEqual(self.read(), ())
        self.now += 10
        self.seed(message='new-message')
        self.assertTrue(self.read())

    def test_controller_flushes_even_when_autonomous_mode_is_disabled(self):
        self.seed()
        controller = autonomous.Autonomous(dict(TWITCH_CHANNEL='channel', TWITCH_BOT_NAME='helper'),
            self.root, clock=lambda: self.now, emit=lambda _: None, history_store=self.store)
        self.addCleanup(controller.close)
        controller.connect(None, lambda: False)
        controller.receive(':tmi.twitch.tv CLEARCHAT #channel')
        asyncio.run(controller.tick())
        self.assertEqual(self.read(MessageHistory(self.root)), ())


if __name__ == '__main__':
    unittest.main()
