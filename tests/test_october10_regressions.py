"""Three confirmed Oct 10 defects, exercised through the real HTTP preflight."""
from dataclasses import asdict
import asyncio
from collections import deque
import base64
import io
from pathlib import Path
import sqlite3
import tempfile
import urllib.error
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

import ai_client
import autonomous
import bot
from message_history import MessageHistory
from reward_context import capture_reward_context, PUBLIC_CONTEXT_LIMIT
from reply_rules import CONTENT_SAFETY_RULE
import safety
from safety_settings import PolicyStore, SafetySettings
import privacy
from participation import Plan, RequestCancelled, request_decision
import test_autonomous_privacy_regression as fixtures


def nested_phone(symbol='\u200b', reverse=False):
    valid = json.dumps({'x': '+7\t999\t123\t45\t67'}, ensure_ascii=False)
    invalid = valid.replace(': ', ': ' + symbol)
    fields = [('valid', valid), ('invalid', invalid)]
    if reverse:
        fields.reverse()
    return json.dumps(dict(fields), ensure_ascii=False)


class NestedPrivacyTests(unittest.TestCase):
    def test_invisible_normalization_cannot_suppress_independent_json_parsing(self):
        for symbol in ('\u200b', '\ufeff', '\u2060'):
            for reverse in (False, True):
                for layers in (0, 1, 2):
                    content = nested_phone(symbol, reverse)
                    for _ in range(layers):
                        content = json.dumps({'nested': content}, ensure_ascii=True)
                    with self.subTest(symbol=hex(ord(symbol)), reverse=reverse, layers=layers):
                        with self.assertRaises(privacy.PrivacyViolation):
                            privacy.check_output(content)
                        gate = fixtures.Mock(return_value=20)
                        with patch('urllib.request.urlopen') as http:
                            with self.assertRaises(privacy.PrivacyViolation):
                                ai_client.request_completion(fixtures.CFG, 'chosen',
                                    [{'role':'user', 'content': content}], before_request=gate)
                        gate.assert_not_called()
                        http.assert_not_called()

    def test_separate_encoding_label_and_contact_remain_linked(self):
        contact = fixtures.CONTACT
        for label, encoded in (('base64', base64.b64encode(contact.encode()).decode()),
                               ('hex', contact.encode().hex())):
            for reverse in (False, True):
                fields = [('topic', label), ('intent', encoded)]
                if reverse:
                    fields.reverse()
                payload = dict(fields)
                for content in (json.dumps(payload), privacy.application_json(payload),
                                privacy.application_text('Data: ', payload)):
                    with self.subTest(label=label, reverse=reverse, application=isinstance(content, privacy.ApplicationContent)):
                        with self.assertRaises(privacy.PrivacyViolation):
                            privacy.check_output(content)

    def test_codec_scope_survives_unicode_labels_nested_fields_and_typed_metadata(self):
        encoded = base64.b64encode(fixtures.CONTACT.encode()).decode()
        escaped = ''.join('\\u%04x' % ord(c) for c in encoded)
        for label in ('base64', 'b\u200base64', r'\u0062ase64'):
            for value in (encoded, escaped):
                for name in ('user_id', 'message_id', 'intent'):
                    payload = dict(topic=label, nested={name:value},
                                   verified=privacy.protocol_id('79991234567'))
                    with self.subTest(label=label, name=name, escaped=value==escaped):
                        with self.assertRaises(privacy.PrivacyViolation):
                            privacy.check_output(privacy.application_json(payload))
        with self.assertRaises(privacy.PrivacyViolation):
            privacy.check_output(privacy.application_json(dict(meta=dict(format='base64'),
                                                             payload=dict(intent=encoded))))
        with self.assertRaises(privacy.PrivacyViolation):
            privacy.check_output(privacy.application_text('base64', {'intent':encoded}))
        privacy.check_output(privacy.application_json(dict(topic='base64',
                    user_id=privacy.protocol_id('79991234567'), message_id=privacy.protocol_id('4532015112830366'))))

    def test_valid_selector_plan_cannot_send_encoded_contact_to_generator(self):
        for label, encoded in (('base64', base64.b64encode(fixtures.CONTACT.encode()).decode()),
                               ('hex', fixtures.CONTACT.encode().hex())):
            plan = asdict(fixtures.plan())
            plan.update(topic=label, intent=encoded)
            with self.subTest(label=label), patch('urllib.request.urlopen',
                    return_value=fixtures.response(plan)) as http:
                with self.assertRaises(privacy.PrivacyViolation) as caught:
                    request_decision(fixtures.CFG, fixtures.rows(), autonomous.AutoSettings())
                self.assertEqual(caught.exception.stage, 'selector_response')
                self.assertEqual(http.call_count, 1)


class CompletedFutureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixtures.ControllerPrivacyRegression.setUp(self)

    def tearDown(self):
        fixtures.ControllerPrivacyRegression.tearDown(self)

    async def execute(self, **options):
        return await fixtures.ControllerPrivacyRegression.execute(self, **options)

    async def test_quota_write_error_survives_ineligible_completed_future(self):
        with patch.object(self.controller, 'write_quota', side_effect=OSError('sensitive storage detail')):
            http, pending, record, event = await self.execute()
        self.assertEqual(pending.future.exception().code, 'quota_storage_error')
        self.assertEqual(http.call_count, 0)
        self.assertEqual(record['status'], 'error')
        self.assertEqual(record['reason'], 'quota_storage_error')
        self.assertEqual(event['status'], 'error')
        self.assertEqual(event['reasons'], ('quota_storage_error',))
        self.assertEqual(event['http_attempts'], (0,0,0))
        self.assertFalse(self.sent)
        self.assertNotIn('sensitive storage detail', json.dumps(self.history.detail(record['id'])))

    async def test_controlled_reason_survives_current_false(self):
        for code in ('ttl_expired','paid_priority','settings_changed','policy_changed',
                     'moderation_cancelled','quota_exhausted','cancelled'):
            with self.subTest(code=code):
                def stop(*args, **kwargs):
                    self.controller.generation += 1
                    raise RequestCancelled(code=code)
                with patch.object(self.controller, 'before_request', side_effect=stop):
                    http, pending, record, event = await self.execute()
                self.assertEqual(http.call_count, 0)
                self.assertEqual(record['reason'], code)
                self.assertEqual(event['reasons'], (code,))
                self.assertFalse(self.sent)
                self.controller.buffer.reset_session()
                self.controller.last_checked = 0
                self.controller.next_check = 0
                self.controller.batch_started = None


class RewardLocalPublicationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.instance = bot.Bot.__new__(bot.Bot)
        self.instance.cfg = dict(fixtures.CFG, AI_PROMPT='PRIVATE_POLICY_MARKER')
        self.instance.history = MessageHistory(self.root, clock=lambda: 10000)
        self.instance.safety_store = PolicyStore(self.root)
        self.instance.memory = dict(streamer=dict(facts=['PRIVATE_NOTE_MARKER'], jokes=[], avoid=[]), viewers=[])
        self.instance.ai_router = bot.AIModelRouter([dict(login='viewer', user_id='123', enabled=True,
                                            prompt='PRIVATE_PROFILE_MARKER', aliases=[])])
        self.instance.histories = {'123': deque([('Обсуждаем Сумерки: что ест Эдвард?',
                                                'Эдвард — персонаж Сумерек, вампир.')], maxlen=10)}
        self.instance.history_times = {'123': deque([9800], maxlen=10)}
        self.instance.history_recipients = {'123': deque(['oldlogin'], maxlen=10)}
        self.instance.autonomous = Mock()
        self.instance.last_sent = 0
        self.instance._say_lock = asyncio.Lock()
        self.writer = Mock()
        self.writer.drain = AsyncMock()
        self.payloads = []

    def tearDown(self):
        self.temp.cleanup()

    def seed(self, login='viewer', user_id='123', status='sent', mode='publish', text='Недавняя публичная реплика'):
        return self.instance.history.add('autonomous', status, channel='channel', viewer=login,
            viewer_id=user_id, action='reply', mode=mode, sent_text=f'@{login} {text}',
            context=dict(basis=[1], conversation=[dict(sequence=1, author=login, user_id=user_id,
                                                    text='Вопрос о вампире')]))

    async def request(self, *, login='viewer', user_id='123', question='Эдвар НЕ гей!',
                      candidate='Ты исправляешь описание персонажа Сумерек.', review=None, mutate=None):
        self.payloads = []
        self.writer.reset_mock()
        self.instance.last_sent = 0
        queue = asyncio.Queue()
        queue.put_nowait((login, user_id, question, 'synthetic-redemption'))
        def http(request, **options):
            payload = json.loads(request.data)
            self.payloads.append(payload)
            if payload['max_tokens'] != 160:
                if mutate:
                    mutate()
                return fixtures.response(candidate)
            if isinstance(review, Exception):
                raise review
            return review() if callable(review) else fixtures.response(review or dict(allowed=True, reasons=[]))
        with patch('bot.time.time', return_value=10000), patch('urllib.request.urlopen', side_effect=http) as network:
            task = asyncio.create_task(self.instance.worker(self.writer, queue))
            try:
                await asyncio.wait_for(queue.join(), 5)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        detail = self.instance.history.detail(self.instance.history.page(kind='reward')['rows'][0]['id'])
        return network, detail, self.instance.history.safety_recent()[0]

    def generator_messages(self):
        self.assertEqual(len(self.payloads), 1)
        return self.payloads[0]['messages']

    async def test_one_generation_keeps_dialogue_context_and_publishes_after_local_checks(self):
        self.seed()
        self.seed('other', '456', text='Чужая история')
        for status, mode in (('preview', 'preview'), ('rejected', 'publish'), ('generated', 'publish')):
            self.seed(status=status, mode=mode, text='Неотправленный кандидат')
        network, detail, event = await self.request()
        self.assertEqual(network.call_count, 1)
        messages = self.generator_messages()
        self.assertIn('Эдвар НЕ гей!', messages[-1]['content'])
        self.assertIn('Сумерки', str(messages))
        self.assertIn('Зритель oldlogin спрашивает:', str(messages))
        automatic = [json.loads(row['content'])['recent_autonomous_reply'] for row in messages
                     if row['role'] == 'user' and row['content'].startswith('{')]
        self.assertEqual(len(automatic), 1)
        self.assertEqual(automatic[0]['recipient'], dict(login='viewer', user_id='123'))
        self.assertEqual(automatic[0]['basis_messages'][0]['author'], 'viewer')
        for forbidden in ('Чужая история', 'Неотправленный кандидат'):
            self.assertNotIn(forbidden, str(messages))
        for marker in ('PRIVATE_POLICY_MARKER', 'PRIVATE_PROFILE_MARKER', 'PRIVATE_NOTE_MARKER'):
            self.assertIn(marker, str(messages))
        self.assertIn(CONTENT_SAFETY_RULE, str(messages))
        self.assertEqual(detail['status'], 'sent')
        self.assertIn('Ты исправляешь описание персонажа Сумерек.', self.writer.write.call_args.args[0].decode())
        events = self.instance.history.safety_recent(200)
        self.assertEqual(len(events), 1)
        self.assertEqual(event['record_id'], detail['id'])
        self.assertEqual(event['status'], 'local_allowed')
        self.assertEqual(event['stage'], 'answer')
        self.assertFalse(event['ai_attempted'])
        self.assertEqual(event['model'], '')
        self.assertIsNone(event['context_metadata'])
        self.assertIsNone(event['http_attempts'])

    async def test_generator_uses_original_snapshot_despite_history_change_during_generation(self):
        self.seed()
        def mutate():
            self.instance.histories['123'].append(('Новая запись', 'После снимка'))
            self.seed(text='После снимка')
        network, _, _ = await self.request(mutate=mutate)
        self.assertEqual(network.call_count, 1)
        messages = str(self.generator_messages())
        self.assertIn('Сумерки', messages)
        self.assertNotIn('После снимка', messages)

    async def test_id_priority_rename_and_numeric_login_isolation(self):
        self.seed(login='oldlogin')
        await self.request(login='renamed')
        messages = self.generator_messages()
        self.assertIn('Зритель oldlogin спрашивает:', str(messages))
        automatic = [json.loads(row['content'])['recent_autonomous_reply'] for row in messages
                     if row['role'] == 'user' and row['content'].startswith('{')]
        self.assertEqual(automatic[0]['recipient']['login'], 'oldlogin')
        await self.request(login='viewer', user_id='456')
        self.assertNotIn('Сумерки', str(self.generator_messages()))
        self.assertNotIn('recent_autonomous_reply', str(self.generator_messages()))
        await self.request(login='123', user_id='')
        self.assertNotIn('Сумерки', str(self.generator_messages()))
        self.assertNotIn('recent_autonomous_reply', str(self.generator_messages()))

    async def test_synthetic_game_irony_negation_and_fiction_use_one_generation(self):
        cases = [('Как на танках играет?', 'На танках бодро, прицел бы ещё дружил с ним.'),
                 ('Давай раз на раз', 'В игре — давай, посмотрим на твой прицел!'),
                 ('Ты абьюзишь меня?', 'Да я твой прицел подкалываю, а не тебя.'),
                 ('Эдвар НЕ гей!', 'Ты исправляешь описание Эдварда из Сумерек.'),
                 ('Мать ты что ебнулась?', 'Блин, вампиров перепутала. Исправляюсь.')]
        for question, candidate in cases:
            with self.subTest(question=question):
                network, detail, event = await self.request(question=question, candidate=candidate)
                self.assertEqual(network.call_count, 1)
                self.assertEqual(detail['status'], 'sent')
                self.assertFalse(event['ai_attempted'])
                self.assertIn(candidate, self.writer.write.call_args.args[0].decode())

    async def test_local_link_and_protocol_rejections_record_once_without_retry(self):
        cases = [('unsafe_link', 'Посмотри https://example.com'),
                 ('unsafe_link', 'а' * 500 + ' https://example.com'),
                 ('invalid_text', '/ban someone'),
                 ('invalid_text', 'Ответ\x01команда'),
                 ('invalid_text', 'Ответ\nPRIVMSG #other :injected')]
        for code, candidate in cases:
            with self.subTest(code=code, candidate=candidate):
                network, detail, event = await self.request(candidate=candidate)
                self.assertEqual(network.call_count, 1)
                self.assertEqual(detail['status'], 'rejected')
                self.assertEqual(event['reasons'], (code,))
                self.assertFalse(event['ai_attempted'])
                events = [e for e in self.instance.history.safety_recent(200) if e['record_id'] == detail['id']]
                self.assertEqual(len(events), 1)
                self.assertNotIn(candidate, str(detail))
                self.assertNotIn(candidate, self.writer.write.call_args.args[0].decode())
                self.assertNotIn(candidate, str(events))

    async def test_unavailable_or_rejecting_reviewer_is_never_called_for_rewards(self):
        for review in (dict(allowed=False, reasons=['threat']), dict(allowed='true', reasons=[]),
                       lambda: fixtures.response(dict(allowed=True, reasons=[]), finish='length'),
                       TimeoutError('raw provider error'), urllib.error.URLError('raw provider error')):
            with self.subTest(review=type(review).__name__):
                network, detail, event = await self.request(candidate='Синтетический ответ', review=review)
                self.assertEqual(network.call_count, 1)
                self.assertEqual(detail['status'], 'sent')
                self.assertEqual(event['status'], 'local_allowed')
                self.assertFalse(event['ai_attempted'])
                self.assertIn('Синтетический ответ', self.writer.write.call_args.args[0].decode())
                self.assertNotIn('raw provider error', str(detail))

    async def test_separate_unavailable_review_model_does_not_add_reward_request(self):
        self.instance.safety_store.apply(SafetySettings(review_model_mode='separate', review_model='unavailable-reviewer'))
        network, detail, event = await self.request(review=urllib.error.URLError('offline reviewer'))
        self.assertEqual(network.call_count, 1)
        self.assertEqual(self.payloads[0]['model'], fixtures.CFG['AI_MODEL'])
        self.assertEqual(detail['status'], 'sent')
        self.assertFalse(event['ai_attempted'])

    async def test_private_generation_blocks_locally_and_records_once(self):
        network, detail, event = await self.request(candidate=fixtures.CONTACT)
        self.assertEqual(network.call_count, 1)
        self.assertEqual(event['stage'], 'generator_response')
        self.assertEqual(event['reasons'], ('privacy_blocked',))
        self.assertFalse(event['ai_attempted'])
        self.assertEqual(len(self.instance.history.safety_recent(200)), 1)
        self.assertNotIn(fixtures.CONTACT, str(detail))
        self.assertNotIn(fixtures.CONTACT, self.writer.write.call_args.args[0].decode())


class ContextBoundsAndLegacyTests(unittest.TestCase):
    def test_current_question_candidate_bounds_scrubbing_and_duplicate_history(self):
        history = tuple((f'Вопрос {i} '+ 'я'*390, 'а'*440) for i in range(10))
        snapshot = capture_reward_context(fixtures.CFG, 'viewer', '79991234567', 'Текущий вопрос',
                        history, tuple(range(10)), (), ('viewer',)*10)
        self.assertLessEqual(len(snapshot.wire), PUBLIC_CONTEXT_LIMIT)
        payload = snapshot.request_content('@viewer Текущий кандидат', 'reward', 'viewer')
        privacy.check_output(payload)  # verified numeric metadata is exempt
        data = json.loads(payload)
        self.assertEqual(data['candidate'], '@viewer Текущий кандидат')
        self.assertEqual(data['current_question'], 'Текущий вопрос')
        self.assertTrue(dict(snapshot.metadata)['truncated'])
        snapshot = capture_reward_context(fixtures.CFG, 'viewer', '123', 'Сейчас',
                        (('Ранее', 'Ответ'), ('Ранее', 'Ответ'), ('Ранее', fixtures.CONTACT)))
        self.assertEqual(dict(snapshot.metadata)['reward_pairs'], 2)
        self.assertNotIn(fixtures.CONTACT, snapshot.wire)
        self.assertIn(privacy.HIDDEN_DATA, snapshot.wire)

    def test_reviewer_envelope_keeps_codec_relation_to_current_question(self):
        snapshot = capture_reward_context(fixtures.CFG, 'viewer', '79991234567', 'base64')
        encoded = base64.b64encode(fixtures.CONTACT.encode()).decode()
        payload = snapshot.request_content(encoded, 'reward', 'viewer')
        with self.assertRaises(privacy.PrivacyViolation):
            privacy.check_output(payload)
        with patch('urllib.request.urlopen') as network:
            review = safety.review_candidate(fixtures.CFG, 'chosen', '@viewer ' + encoded,
                                            target='viewer', context=snapshot)
        self.assertEqual(review.reasons, ('privacy_blocked',))
        self.assertEqual(review.stage, 'review_request')
        self.assertFalse(review.ai_attempted)
        network.assert_not_called()

    def test_old_reward_default_zeros_are_unknown_without_rewriting_file(self):
        with tempfile.TemporaryDirectory() as temp:
            store = MessageHistory(temp)
            with sqlite3.connect(store.path) as connection:
                connection.execute('CREATE TABLE safety_events(seq INTEGER PRIMARY KEY,time REAL,scenario TEXT,record_id TEXT,status TEXT,stage TEXT,reasons TEXT,ai_attempted INTEGER,model TEXT,seconds REAL,policy_version TEXT,http_attempts TEXT,twitch_attempted INTEGER)')
                connection.execute("INSERT INTO safety_events VALUES (1,0,'reward','old-record','blocked','ai_review','[\"threat\"]',1,'chosen',1,'default-v1','[0,0,0]',0)")
            connection.close()
            original = store.path.read_bytes()
            row = store.safety_recent()[0]
            self.assertIsNone(row['http_attempts'])
            self.assertIsNone(row['twitch_attempted'])
            self.assertEqual(row['record_id'], 'old-record')
            self.assertEqual(store.path.read_bytes(), original)
            store.safety_event('reward', safety.SafetyReview('blocked','',('threat',)))
            self.assertEqual(len(store.safety_recent()), 2)
            self.assertEqual(store.safety_recent()[1]['record_id'], 'old-record')
