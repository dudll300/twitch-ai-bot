"""Real selector -> transport -> privacy; only urlopen is replaced, never AI stages."""
import asyncio
from contextlib import closing
import base64
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, replace
import io
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Event
import unittest
from unittest.mock import Mock, patch
import urllib.error

import ai_client
import autonomous as auto
from conversation_roles import SourceRole, impersonates_recipient
from message_history import MessageHistory
from participation import Plan, ParticipationError, RequestCancelled, request_decision
import privacy
from safety import review_candidate
from safety_diagnostics import diagnose
from safety_settings import PolicyStore, policy_scope

CFG = dict(TWITCH_CHANNEL='channel', TWITCH_BOT_NAME='helper', AI_MODEL='chosen',
           AI_PROMPT='Характер бота', AI_API_KEY='fixture-key', AI_CHAT_URL='https://example.invalid/chat')
CONTACT = 'private@example.invalid'


def response(content, finish='stop'):
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)
    return io.BytesIO(json.dumps({'choices': [{'message': {'content': content}, 'finish_reason': finish}]}).encode())


def rows(count=20):
    return [dict(sequence=i+1, author=f'viewer{i%3}', user_id=str(79991234567+i%3),
                 message_id=str(4532015112830366+i), time=10000+i,
                 text=f'Дроп редкого меча {i}') for i in range(count)]


def plan(count=20):
    return Plan('reply', (count-1, count), (count,), f'viewer{(count-1)%3}', 'reaction',
                'Реакция наблюдателя на редкий меч',
                (SourceRole(count-1, 'unknown', subject='редкий меч'),
                 SourceRole(count, 'unknown', subject='редкий меч', linked_to=count-1,
                            relation='Продолжение обсуждения дропа редкого меча')), 'редкий меч')


class TransportPrivacyRegression(unittest.TestCase):
    def run_scene(self, messages=None, outputs=None, **options):
        messages = messages or rows()
        outputs = outputs or [asdict(plan(len(messages))), {'text': 'Подожду такого же дропа'}]
        trace, gate = ai_client.RequestTrace(), Mock(return_value=20)
        with patch('urllib.request.urlopen', side_effect=[response(item) for item in outputs]) as http:
            result = request_decision(CFG, messages, replace(auto.AutoSettings(), context_count=len(messages)),
                                      before_request=gate, request_trace=trace, **options)
        return result, http, gate, trace

    def test_safe_twenty_and_larger_context_reach_http_with_numeric_metadata(self):
        for count in (20, 80, 200):
            with self.subTest(count=count):
                result, http, gate, trace = self.run_scene(rows(count))
                self.assertEqual(result.text, 'Подожду такого же дропа')
                self.assertEqual(http.call_count, gate.call_count)
                self.assertEqual(trace.counts(), (1, 1, 0))
                payload = json.loads(http.call_args_list[0].args[0].data)['messages'][-1]['content']
                self.assertEqual(len(json.loads(payload)['chat_context']), count)

    def test_aliases_reply_metadata_history_and_valid_roles(self):
        messages = rows()
        messages[-1].update(text='@viewer0 Дроп редкого меча!', reply_parent_id=messages[-2]['message_id'],
                            reply_parent_user_id=messages[0]['user_id'], reply_parent_login='viewer0',
                            thread_id=messages[-2]['message_id'], mentions=('viewer0',))
        selected = replace(plan(), source_roles=(plan().source_roles[0],
            SourceRole(20, 'viewer', 'viewer0', messages[0]['user_id'], '@viewer0', 'редкий меч',
                       19, 'Продолжение обсуждения дропа редкого меча')))
        profiles = [dict(login='viewer0', user_id=messages[0]['user_id'], aliases=['Рыцарь'],
                         enabled=True, prompt='Короткая реакция', reward_enabled=False)]
        history = (dict(text='Красивый меч', target='viewer0', source='autonomous', question='Редкий меч',
                        basis=(19,), time=9990),)
        result, http, gate, trace = self.run_scene(messages, [asdict(selected), {'text': 'Красивый дроп'}],
                                                  profiles=profiles, recent_replies=history)
        self.assertEqual(result.action, 'reply')
        self.assertEqual(trace.counts(), (1, 1, 0))
        for call in http.call_args_list:
            self.assertIn('Рыцарь', call.args[0].data.decode())

    def test_private_first_middle_last_is_excluded_before_http(self):
        for index in (0, 10, 19):
            messages = rows()
            messages[index]['text'] = json.dumps({'user_id': '+79991234567', 'note': CONTACT})
            with self.subTest(index=index), patch('urllib.request.urlopen', return_value=response(asdict(Plan('silent')))) as http:
                result = request_decision(CFG, messages, auto.AutoSettings(), new_ids={index+1})
                self.assertEqual(result.action, 'silent')
                http.assert_not_called()
            # Other safe new messages remain usable, but no private row reaches HTTP.
            with patch('urllib.request.urlopen', return_value=response(asdict(Plan('silent')))) as http:
                request_decision(CFG, messages, auto.AutoSettings())
                self.assertNotIn(CONTACT, http.call_args.args[0].data.decode())
                self.assertNotIn('+79991234567', http.call_args.args[0].data.decode())

    def test_prompt_notes_history_and_alias_data_block_without_reservation(self):
        cases = [(dict(CFG, AI_PROMPT=CONTACT), {}),
                 (CFG, dict(memory_data={'streamer': {'facts': [CONTACT], 'jokes': [], 'avoid': []}, 'viewers': []})),
                 (CFG, dict(recent_replies=(dict(text=CONTACT, target='viewer0', source='autonomous', basis=(1,), time=9999),))),
                 (CFG, dict(profiles=[dict(login='viewer0', user_id=rows()[0]['user_id'], aliases=[CONTACT], enabled=True)]))]
        for cfg, options in cases:
            gate, trace = Mock(return_value=20), ai_client.RequestTrace()
            with self.subTest(options=options), patch('urllib.request.urlopen') as http:
                with self.assertRaises(privacy.PrivacyViolation) as caught:
                    request_decision(cfg, rows(), auto.AutoSettings(), before_request=gate, request_trace=trace, **options)
                self.assertEqual(caught.exception.stage, 'selector_request')
                self.assertEqual(trace.counts(), (0, 0, 0))
                gate.assert_not_called()
                http.assert_not_called()

    def test_response_data_and_supported_encodings_are_blocked_at_exact_phase(self):
        encodings = [CONTACT, json.dumps({'text': CONTACT}, ensure_ascii=True).replace('p', '\\u0070'),
                     'base64 ' + base64.b64encode(CONTACT.encode()).decode(), 'hex ' + CONTACT.encode().hex()]
        for encoded in encodings:
            for phase in ('selector_response', 'generator_response'):
                outputs = [encoded] if phase == 'selector_response' else [asdict(plan()), {'text': encoded}]
                trace, gate = ai_client.RequestTrace(), Mock(return_value=20)
                with self.subTest(phase=phase, encoded=encoded), patch('urllib.request.urlopen',
                        side_effect=[response(item) for item in outputs]) as http:
                    with self.assertRaises(privacy.PrivacyViolation) as caught:
                        request_decision(CFG, rows(), auto.AutoSettings(), before_request=gate, request_trace=trace)
                    self.assertEqual(caught.exception.stage, phase)
                    self.assertEqual(http.call_count, len(outputs))
                    self.assertEqual(gate.call_count, len(outputs))

    def test_user_json_field_names_never_make_numbers_trusted(self):
        for value in ('79991234567', '4532015112830366'):
            for text in (value, json.dumps({'user_id': value}), json.dumps({'message_id': int(value)})):
                with self.subTest(text=text), self.assertRaises(privacy.PrivacyViolation):
                    privacy.check_output(text)
        # The SAME number as verified IRC metadata passed the large-context test.

    def test_late_private_fields_and_analysis_limits_fail_closed_distinctly(self):
        for position in (0, 700, 1399):
            data = {f'field{i}': f'обычное значение {i}' for i in range(1400)}
            data[f'field{position}'] = CONTACT
            with self.assertRaises(privacy.PrivacyViolation):
                privacy.check_output(json.dumps(data))
        for complex_text in ('[' * 20 + '0' + ']' * 20, json.dumps(list(range(17000))),
                             'base64 ' + base64.b64encode(b'x' * 1100).decode()):
            gate = Mock(return_value=20)
            with patch('urllib.request.urlopen') as http, self.assertRaises(privacy.PrivacyAnalysisLimit):
                ai_client.request_completion(CFG, 'chosen', [{'role': 'user', 'content': complex_text}], before_request=gate)
            gate.assert_not_called()
            http.assert_not_called()
            self.assertEqual(privacy.safe_history_text(complex_text), privacy.HIDDEN_DATA)

    def test_selection_diagnostics_and_no_retries(self):
        invalid_schema = asdict(plan())
        invalid_schema['basis'] = 'wrong'
        disconnected = asdict(plan())
        disconnected['source_roles'][1]['linked_to'] = 0
        cases = [(urllib.error.URLError('private body'), 'api'), (TimeoutError('private body'), 'timeout'),
                 (response(asdict(plan()), 'length'), 'truncated'), (response('not-json'), 'json'),
                 (response(invalid_schema), 'schema'), (response(disconnected), 'roles')]
        for output, suffix in cases:
            with self.subTest(suffix=suffix), patch('urllib.request.urlopen', side_effect=[output]) as http:
                with self.assertRaises(ParticipationError) as caught:
                    request_decision(CFG, rows(), auto.AutoSettings())
                self.assertEqual(caught.exception.code, 'autonomous_selection_' + suffix)
                self.assertEqual(http.call_count, 1)
                self.assertNotIn('private body', str(caught.exception))

    def test_observer_and_impersonation_have_different_conditions(self):
        owner = SourceRole(1, 'owner', 'channel', '123', 'софа')
        unknown = SourceRole(1, 'unknown')
        self.assertTrue(impersonates_recipient('Терплю', (owner,), (1,),
                        [dict(sequence=1, text='потерпи софа')]))
        self.assertTrue(impersonates_recipient('Подожду', (owner,), (1,),
                        [dict(sequence=1, text='Софа, подожди')]))
        for role in (owner, unknown):
            self.assertFalse(impersonates_recipient('Подожду такого же дропа', (role,), (1,),
                             [dict(sequence=1, text='Выпал редкий меч')]))


class Executor:
    def submit(self, function, *args, **kwargs):
        self.call = (function, args, kwargs)
        self.future = Future()
        return self.future

    def shutdown(self, **kwargs):
        pass


class ControllerPrivacyRegression(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = 10040.
        self.settings = replace(auto.AutoSettings(), enabled=True, mode='publish', pause_seconds=0,
                                context_count=20, check_min_seconds=0, check_max_seconds=0)
        auto.save_settings(self.root / 'autonomous.json', self.settings)
        self.history = MessageHistory(self.root)
        self.events, self.sent = [], []
        self.controller = auto.Autonomous(dict(CFG), self.root, clock=lambda: self.now, monotonic_clock=lambda: self.now,
                         choose_delay=lambda a, b: a, history_store=self.history, emit=self.events.append)
        self.executor = Executor()
        self.controller.executor = self.executor
        async def send(text, valid, reserve, **options):
            if not valid():
                return False
            reserve()
            self.sent.append(text)
            return True
        self.controller.connect(send, lambda: False)

    def tearDown(self):
        self.controller.close()
        self.temp.cleanup()

    async def execute(self, outputs=None, limit=40, profiles=(), cfg=None):
        auto.save_settings(self.root / 'autonomous.json', replace(self.settings, request_hourly_limit=limit))
        self.controller.refresh()
        self.controller.profiles = profiles
        if cfg:
            self.controller.cfg.update(cfg)
        for row in rows():
            line = f"@id={row['message_id']};user-id={row['user_id']} :{row['author']}!u@host PRIVMSG #channel :{row['text']}"
            self.controller.receive(line)
            self.now += 2  # Keep normal per-author IRC flood protection active.
        self.now += 3
        await self.controller.tick()
        pending = self.controller.pending
        outputs = outputs or [response(asdict(plan())), response({'text': 'Подожду такого же дропа'}),
                              response({'allowed': True, 'reasons': []})]
        with patch('urllib.request.urlopen', side_effect=outputs) as http:
            function, args, kwargs = self.executor.call
            try:
                result = function(*args, **kwargs)
            except Exception as exc:
                self.executor.future.set_exception(exc)
            else:
                self.executor.future.set_result(result)
            await self.controller.tick()
        persisted = json.loads((self.root / 'autonomous-quota.json').read_text()) if (self.root / 'autonomous-quota.json').exists() else {'requests': []}
        self.assertEqual(http.call_count, len(self.controller.requests))
        self.assertEqual(http.call_count, len(persisted['requests']))
        return http, pending, self.history.page()['rows'][0], self.history.safety_recent()[0]

    async def test_full_pipeline_counts_three_calls_once_and_preserves_history(self):
        http, pending, record, event = await self.execute()
        self.assertEqual(http.call_count, 3)
        self.assertEqual(record['status'], 'sent')
        self.assertEqual(event['http_attempts'], (1, 1, 1))
        self.assertTrue(event['ai_attempted'])  # Only the publication reviewer.
        self.assertTrue(event['twitch_attempted'])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(record['viewer_id'], rows()[-1]['user_id'])
        self.assertEqual(self.controller.recent_replies[-1]['user_id'], rows()[-1]['user_id'])
        await self.controller.tick()
        self.assertEqual(len(self.sent), 1)

    async def test_preflight_block_consumes_zero_and_records_request_phase(self):
        http, pending, record, event = await self.execute(cfg=dict(AI_PROMPT=CONTACT))
        self.assertEqual(http.call_count, 0)
        self.assertEqual(record['reason'], 'privacy_blocked')
        self.assertEqual(event['stage'], 'selector_request')
        self.assertEqual(event['http_attempts'], (0, 0, 0))
        self.assertFalse(event['ai_attempted'])
        self.assertNotIn(CONTACT, json.dumps(self.history.detail(record['id'])))

    async def test_generator_prompt_blocks_after_one_counted_selection(self):
        profiles = [dict(login='viewer1', user_id=rows()[-1]['user_id'], enabled=True, prompt=CONTACT, aliases=[])]
        http, pending, record, event = await self.execute(profiles=profiles)
        self.assertEqual(http.call_count, 1)
        self.assertEqual(event['stage'], 'generator_request')
        self.assertEqual(event['http_attempts'], (1, 0, 0))
        self.assertFalse(self.sent)

    async def test_quota_in_reviewer_retains_reason_and_zero_review_http(self):
        http, pending, record, event = await self.execute(limit=2)
        self.assertEqual(http.call_count, 2)
        self.assertEqual(record['reason'], 'quota_exhausted')
        self.assertEqual(event['http_attempts'], (1, 1, 0))
        self.assertFalse(event['ai_attempted'])
        self.assertFalse(event['twitch_attempted'])
        self.assertFalse(self.sent)

    async def test_network_failure_and_timeout_are_counted_without_publication(self):
        http, pending, record, event = await self.execute(outputs=[urllib.error.URLError('private response')])
        self.assertEqual(http.call_count, 1)
        self.assertEqual(record['reason'], 'autonomous_selection_api')
        self.assertEqual(event['http_attempts'], (1, 0, 0))
        self.assertFalse(event['ai_attempted'])
        self.assertFalse(self.sent)
        self.assertNotIn('private response', json.dumps(self.history.detail(record['id'])))

    async def test_analysis_limit_is_error_not_private_data(self):
        http, pending, record, event = await self.execute(cfg=dict(AI_PROMPT='['*20 + '0' + ']'*20))
        self.assertEqual(http.call_count, 0)
        self.assertEqual(record['status'], 'error')
        self.assertEqual(record['reason'], 'privacy_analysis_limit')
        self.assertEqual(event['stage'], 'selector_request')
        self.assertIsNone(self.history.detail(record['id'])['context'])

    async def test_concurrent_pre_http_gates_cannot_exceed_quota(self):
        auto.save_settings(self.root / 'autonomous.json', replace(self.settings, request_hourly_limit=3))
        self.controller.refresh()
        for row in rows(3):
            self.controller.receive(f":{row['author']}!u@host PRIVMSG #channel :{row['text']}")
        window = auto.RequestWindow(self.now+30, self.now+30)
        def call():
            with policy_scope(self.controller.safety_store):
                try:
                    ai_client.request_completion(CFG, 'chosen', [{'role':'user', 'content':'Игровой вопрос'}],
                        before_request=lambda: self.controller.before_request(self.controller.generation, Event(), window))
                    return 'attempted'
                except RequestCancelled as exc:
                    return exc.code
        with patch('urllib.request.urlopen', side_effect=lambda *a, **k: response('Ответ')) as http:
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda _: call(), range(8)))
        self.assertEqual(results.count('attempted'), 3)
        self.assertEqual(results.count('quota_exhausted'), 5)
        self.assertEqual(http.call_count, 3)
        self.assertEqual(len(self.controller.requests), 3)
        self.assertEqual(len(json.loads((self.root/'autonomous-quota.json').read_text())['requests']), 3)

    async def test_internal_privacy_error_is_not_reported_as_detected_data(self):
        original = privacy._literal_private
        def fail(value):
            if value == 'Характер бота':
                raise RuntimeError('private internal detail')
            return original(value)
        with patch.object(privacy, '_literal_private', side_effect=fail):
            http, pending, record, event = await self.execute()
        self.assertEqual(http.call_count, 0)
        self.assertEqual(record['reason'], 'privacy_check_error')
        self.assertEqual(record['status'], 'error')
        self.assertNotIn('private internal detail', json.dumps(self.history.detail(record['id'])))


class ReviewCancellationRegression(unittest.TestCase):
    def test_review_local_failure_and_controlled_cancellations_make_no_attempt(self):
        with tempfile.TemporaryDirectory() as folder:
            store = PolicyStore(Path(folder))
            with policy_scope(store):
                for code in ('quota_exhausted', 'ttl_expired', 'paid_priority', 'settings_changed', 'cancelled'):
                    gate = Mock(side_effect=RequestCancelled(code=code))
                    trace = ai_client.RequestTrace()
                    with self.subTest(code=code), patch('urllib.request.urlopen') as http:
                        result = review_candidate(CFG, 'chosen', 'Безопасный ответ', before_request=gate, request_trace=trace)
                    self.assertEqual(result.reasons, (code,))
                    self.assertFalse(result.ai_attempted)
                    self.assertEqual(trace.counts(), (0, 0, 0))
                    http.assert_not_called()
                with patch('urllib.request.urlopen') as http:
                    result = review_candidate(CFG, 'chosen', CONTACT)
                self.assertEqual(result.reasons, ('privacy_blocked',))
                http.assert_not_called()
                result = diagnose(store, store.snapshot(), '['*20+'0'+']'*20, 'question')
                self.assertEqual(result.reasons, ('privacy_analysis_limit',))

    def test_old_safety_table_migrates_without_losing_records(self):
        with tempfile.TemporaryDirectory() as folder:
            store = MessageHistory(Path(folder))
            with closing(sqlite3.connect(store.path)) as conn, conn:
                conn.execute('CREATE TABLE safety_events(seq INTEGER PRIMARY KEY, time REAL, scenario TEXT, record_id TEXT, '
                    'status TEXT, stage TEXT, reasons TEXT, ai_attempted INTEGER, model TEXT, seconds REAL, policy_version TEXT)')
                conn.execute("INSERT INTO safety_events VALUES(1,0,'autonomous',NULL,'blocked','answer','[\"privacy_blocked\"]',0,'',NULL,'old')")
            from safety import SafetyReview
            store.safety_event('autonomous', SafetyReview('error', '', ('privacy_analysis_limit',), stage='selector_request'))
            events = store.safety_recent()
            self.assertEqual(len(events), 2)
            self.assertEqual(events[1]['reasons'], ('privacy_blocked',))
            self.assertIsNone(events[1]['http_attempts'])
            self.assertIsNone(events[1]['twitch_attempted'])
