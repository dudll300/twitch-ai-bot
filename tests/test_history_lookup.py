"""Recipient SQL filtering preserves the legacy query's observable results."""

from contextlib import closing
import json
from pathlib import Path
import random
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from message_history import MessageHistory
from recent_context import PERSONAL_REPLY_SECONDS, PUBLIC_CANDIDATE_LIMIT


def legacy_rows(connection, channel, now, recipient=None, **options):
    kind = "r.kind='autonomous'" if recipient is not None else "r.kind IN ('autonomous','reward')"
    return connection.execute("""
        SELECT r.kind,r.viewer,r.viewer_id,r.sent_text,
               CASE WHEN r.kind='autonomous' THEN r.context ELSE NULL END AS context,r.mode,r.channel,
               MIN(e.time) AS sent_at
        FROM records r JOIN events e ON e.record_id=r.id AND e.status='sent'
        WHERE """ + kind + """ AND r.status='sent' AND r.mode!='preview' AND lower(r.channel)=?
        GROUP BY r.id HAVING sent_at BETWEEN ? AND ? ORDER BY sent_at DESC,r.seq DESC LIMIT ?
        """, (channel.casefold(), now-PERSONAL_REPLY_SECONDS, now,
               PUBLIC_CANDIDATE_LIMIT if recipient is None else -1))


class HistoryLookupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = MessageHistory(Path(self.temp.name), clock=lambda: 10000)
        self.store.add('autonomous', 'generating')

    def seed(self, rows):
        with closing(sqlite3.connect(self.store.path)) as connection:
            with connection:
                connection.executemany('INSERT INTO records(id,session,created,updated,kind,status,channel,viewer,viewer_id,sent_text,mode,context) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', rows)
                connection.executemany("INSERT INTO events(record_id,time,status) VALUES (?,?,'sent')",
                                       [(row[0], row[2]) for row in rows])

    def test_only_matching_modern_recipient_json_is_decoded(self):
        context = json.dumps(dict(basis=[], conversation=[]))
        rows = [(f'other-{i}', 'session', 10000, 10000, 'autonomous', 'sent', 'channel', f'other{i}',
                 str(1000+i), f'@other{i} Синтетический ответ', 'publish', context) for i in range(2000)]
        rows.append(('target', 'session', 9999, 9999, 'autonomous', 'sent', 'channel', 'viewer', '123',
                     '@viewer Нужный ответ', 'publish', context))
        self.seed(rows)
        decoded, original = [], json.loads

        def load(value, *args, **kwargs):
            if value == context:
                decoded.append(value)
            return original(value, *args, **kwargs)

        with patch('message_history.json.loads', side_effect=load):
            result = self.store.recent_personal_replies('channel', 'renamed', '123', 10001)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['text'], '@viewer Нужный ответ')
        self.assertEqual(len(decoded), 1)

    def test_results_equal_legacy_query_for_identity_time_status_and_order(self):
        randomizer = random.Random(22)
        rows = []
        for index in range(350):
            viewer = randomizer.choice(('viewer', 'oldlogin', 'other'))
            uid = randomizer.choice(('123', '456', ''))
            context_id = randomizer.choice(('123', '456', ''))
            context = json.dumps(dict(basis=[1], conversation=[dict(sequence=1, author=viewer,
                                      user_id=context_id, text='Вопрос о боссе')]))
            sent = randomizer.choice((5000, 8200, 9900, 10000, 10001))
            rows.append((str(index), 'session', sent, sent, randomizer.choice(('autonomous', 'reward')),
                randomizer.choice(('sent', 'preview', 'cancelled')), randomizer.choice(('channel', 'otherchannel')),
                viewer, uid, f'@{viewer} Ответ {index}', randomizer.choice(('publish', 'preview')), context))
        self.seed(rows)
        # A later sent update must never revive a publication whose first send
        # is outside the window, even though it has an event inside the window.
        with closing(sqlite3.connect(self.store.path)) as connection:
            with connection:
                connection.executemany("INSERT INTO events(record_id,time,status) VALUES (?,9900,'sent')",
                                       [(row[0],) for row in rows if row[2] == 5000])
        for login, uid in (('viewer', '123'), ('renamed', '123'), ('viewer', '456'), ('VIEWER', '')):
            with self.subTest(login=login, uid=uid):
                result = self.store.recent_personal_replies('CHANNEL', login, uid, 10000)
                with patch.object(MessageHistory, '_publication_rows', side_effect=legacy_rows):
                    old = self.store.recent_personal_replies('CHANNEL', login, uid, 10000)
                self.assertEqual(result, old)
        result = self.store.recent_public_replies('channel', 10000)
        with patch.object(MessageHistory, '_publication_rows', side_effect=legacy_rows):
            self.assertEqual(result, self.store.recent_public_replies('channel', 10000))

    def test_lookup_remains_read_only_for_an_older_database_without_new_indexes(self):
        self.seed([('legacy', 'session', 9900, 9900, 'autonomous', 'sent', 'channel', 'oldlogin', '',
                    '@oldlogin Ответ', 'publish', json.dumps(dict(basis=[1], conversation=[
                        dict(sequence=1, author='oldlogin', user_id='123', text='Босс')])) )])
        with closing(sqlite3.connect(self.store.path)) as connection:
            for name in ('events_status_time_record', 'events_record_status_time', 'records_context_recipient'):
                connection.execute('DROP INDEX ' + name)
        before = self.store.path.read_bytes()
        result = MessageHistory(self.store.path.parent).recent_personal_replies('channel', 'renamed', '123', 10000)
        self.assertEqual(len(result), 1)
        self.assertEqual(self.store.path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
