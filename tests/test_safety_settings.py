"""Policy, exact hostnames, real pipeline integration and safe metadata; fake HTTP."""
import asyncio
from dataclasses import asdict, replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock, AsyncMock
import urllib.error
import subprocess
import sys
from concurrent.futures import Future

import ai_client
import autonomous as auto
import bot
import testing
from participation import parse_decision, Decision
from message_history import MessageHistory
from safety_settings import SafetySettings, PolicyStore, validate_settings, domain_name, policy_scope, current_policy
from safety_diagnostics import diagnose
from safety import SafetyReview, SafetyBlocked, local_review, review_candidate, check_candidate_source, validate_publication, publication_guard, describe_review

CFG = {'TWITCH_CHANNEL': 'channel', 'TWITCH_BOT_NAME': 'bot', 'AI_MODEL': 'answer',
       'AI_FALLBACK_MODELS': 'backup', 'AI_API_KEY': 'test-key', 'AI_CHAT_URL': 'https://example.invalid/chat/completions'}
AUTH = testing.Credentials('https://example.invalid', 'test-key')


def response(value):
    content = json.dumps(value) if isinstance(value, dict) else value
    return io.BytesIO(json.dumps({'choices': [{'message': {'content': content}}]}).encode())


class PolicyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = PolicyStore(self.root)
        self.scope = policy_scope(self.store)
        self.scope.__enter__()
        self.addCleanup(self.scope.__exit__, None, None, None)

    def allow(self, **extra):
        return self.store.apply(replace(SafetySettings(link_mode='allow_domains', allowed_domains=('twitch.tv', 'пример.рф')), **extra))

    def test_defaults_missing_file_and_round_trip(self):
        self.assertEqual(self.store.snapshot().settings, SafetySettings())
        policy = self.allow(review_model_mode='separate', review_model='reviewer', review_timeout_seconds=15)
        self.assertEqual(self.store.snapshot(), policy)
        self.assertEqual(policy.settings.allowed_domains, ('twitch.tv', 'xn--e1afmkfd.xn--p1ai'))
        self.assertNotIn('test-key', self.store.path.read_text(encoding='utf-8'))

    def test_strict_fields_types_ranges(self):
        cases = [('schema_version', True), ('schema_version', 2), ('link_mode', None), ('link_mode', 'allow_all'),
                 ('allowed_domains', 'twitch.tv'), ('allowed_domains', [12]), ('review_model_mode', False),
                 ('review_model', []), ('review_timeout_seconds', True), ('review_timeout_seconds', 1),
                 ('review_timeout_seconds', 16), ('review_timeout_seconds', 8.0)]
        for name, value in cases:
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                validate_settings({**asdict(SafetySettings()), name: value})
        for raw in ({}, {**asdict(SafetySettings()), 'api_key': 'x'},
                    {**asdict(SafetySettings()), 'review_model_mode': 'separate'}):
            with self.assertRaises(ValueError):
                validate_settings(raw)

    def test_domain_validation_and_dedup(self):
        settings = validate_settings({**asdict(SafetySettings()), 'allowed_domains': ['TWITCH.TV', 'twitch.tv', 'ПРИМЕР.РФ', 'xn--e1afmkfd.xn--p1ai']})
        self.assertEqual(settings.allowed_domains, ('twitch.tv', 'xn--e1afmkfd.xn--p1ai'))
        for value in ('https://twitch.tv', 'twitch.tv/path', 'twitch.tv?q=1', 'a@twitch.tv', '*.twitch.tv',
                      'twitch.tv:443', ' twitch.tv', 'twitch.tv.', '127.0.0.1', 'xn--invalid-.tv', '-x.tv', 'single'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                domain_name(value)

    def test_corrupt_file_denies_without_mutation_and_can_repair(self):
        for content in ('{bad', '[]', '{"schema_version":1,"schema_version":1}', 'x' * 33000):
            self.store.path.write_text(content)
            policy = self.store.snapshot()
            self.assertTrue(policy.error)
            self.assertEqual(local_review('Ответ', policy=policy).status, 'error')
            self.assertEqual(self.store.path.read_text(), content)
        policy = self.store.apply(SafetySettings())
        self.assertFalse(policy.error)

    def test_atomic_failure_preserves_old_file_and_no_temporary_files(self):
        first = self.store.apply(SafetySettings())
        with patch('safety_settings.os.replace', side_effect=OSError('disk')), self.assertRaises(OSError):
            self.allow()
        self.assertEqual(self.store.snapshot(), first)
        self.assertFalse(list(self.root.glob('.safety-settings-*.tmp')))

    def test_allow_exact_https_bare_unicode_masked_and_markdown(self):
        policy = self.allow()
        with policy_scope(self.store, policy), patch('urllib.request.urlopen') as network:
            for text in ('https://twitch.tv/channel?a=b', 'https://twitch.tv/path/video.html?q=other.invalid', 'TWITCH.TV', 'twitch[.]tv', 'twitch [.] tv',
                         '[канал](https://twitch.tv/channel)', 'пример.рф', 'https://пример.рф',
                         'xn--e1afmkfd.xn--p1ai'):
                with self.subTest(text=text):
                    self.assertEqual(local_review(text).status, 'local_allowed')
                    check_candidate_source(text)
                    decision = parse_decision(json.dumps(dict(action='reply', text=text, target='', basis=[1], reason='answer')), 450)
                    self.assertEqual(decision.text, text)
                    validate_publication(text, SafetyReview('allowed', text))
            network.assert_not_called()

    def test_subdomains_lookalikes_userinfo_and_schemes_remain_blocked(self):
        policy = self.allow()
        for text in ('https://www.twitch.tv', 'www.twitch.tv', 'twitch.tv.evil.invalid', 'evil-twitch.tv',
                     'https://twitch.tv@evil.invalid', 'https://evil@twitch.tv', 'http://twitch.tv',
                     'ftp://twitch.tv', 'hxxps://twitch[.]tv', 'mailto:twitch.tv', 'tg://twitch.tv',
                     'javascript://twitch.tv', 'data://twitch.tv', '//twitch.tv', 'https://twitch.tv:8080',
                     '[канал](evil.invalid)', 'twitch.tv evil[.]invalid'):
            with self.subTest(text=text), policy_scope(self.store, policy):
                self.assertEqual(local_review(text).status, 'blocked')
        policy = self.store.apply(replace(policy.settings, allowed_domains=('www.twitch.tv',)))
        self.assertEqual(local_review('https://www.twitch.tv', policy=policy).status, 'local_allowed')

    def test_full_candidate_and_mandatory_rules_survive_allowlist(self):
        policy = self.allow()
        with policy_scope(self.store, policy):
            for text in ('a' * 500 + ' evil.invalid', 'twitch.tv\r\nPASS secret', '/ban viewer',
                         'alex @ example . invalid', 'twitch.tv +7 999 555 11 22'):
                with self.subTest(text=text), self.assertRaises((SafetyBlocked, __import__('privacy').PrivacyViolation)):
                    check_candidate_source(text)
            self.assertEqual(local_review('twitch.tv', target='viewer').status, 'blocked')

    def test_unicode_other_scripts_and_exact_allowlist(self):
        policy = self.store.apply(SafetySettings(link_mode='allow_domains', allowed_domains=('例子.みんな',)))
        self.assertEqual(local_review('例子.みんな', policy=policy).status, 'local_allowed')
        self.assertEqual(local_review('別名.みんな', policy=policy).status, 'blocked')
        self.assertEqual(local_review('例子.みんな').status, 'blocked')

    def test_cross_process_apply_cannot_interleave_publication(self):
        approval = SafetyReview('allowed', 'Ответ')
        code = "import sys; from safety_settings import PolicyStore,SafetySettings\ntry: PolicyStore(sys.argv[1]).apply(SafetySettings())\nexcept OSError: sys.exit(12)"
        with publication_guard('Ответ', approval):
            result = subprocess.run([sys.executable, '-c', code, str(self.root)], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 12, result.stderr)
        self.assertFalse(self.store.path.exists())
        self.store.apply(SafetySettings())

    def test_diagnostic_does_not_send_known_connection_key(self):
        with patch('urllib.request.urlopen') as network:
            for ai in (False, True):
                review = diagnose(self.store, self.store.snapshot(), 'test-key', 'answer', ai=ai, auth=AUTH, answer_model='answer')
                self.assertEqual(review.reasons, ('privacy_blocked',))
        network.assert_not_called()

    def test_version_changes_even_for_identical_apply_and_old_approval_cancelled(self):
        old = self.store.snapshot()
        with policy_scope(self.store, old):
            approval = SafetyReview('allowed', 'Ответ')
            newer = self.store.apply(old.settings)
            self.assertNotEqual(old.version, newer.version)
            self.assertFalse(self.store.is_current(old))
            with self.assertRaises(SafetyBlocked) as caught:
                validate_publication('Ответ', approval)
            self.assertEqual(caught.exception.review.reasons, ('policy_changed',))

    def test_same_model_separate_timeout_and_no_fallback(self):
        for separate in (False, True):
            policy = self.allow(review_model_mode='separate', review_model='reviewer', review_timeout_seconds=15) if separate else self.store.snapshot()
            expected, seconds = ('reviewer', 15) if separate else ('backup', 8)
            with policy_scope(self.store, policy), patch('urllib.request.urlopen', return_value=response({'allowed': True, 'reasons': []})) as network:
                result = review_candidate(CFG, 'backup', 'Ответ')
                self.assertTrue(result.allowed)
                self.assertTrue(result.ai_attempted)
                self.assertEqual(result.model, expected)
                self.assertEqual(json.loads(network.call_args.args[0].data)['model'], expected)
                self.assertEqual(network.call_args.kwargs['timeout'], seconds)
                self.assertEqual(result.policy_version, policy.version)
        with policy_scope(self.store, policy), patch('urllib.request.urlopen', side_effect=urllib.error.URLError('offline')) as network:
            result = review_candidate(CFG, 'backup', 'Ответ')
            self.assertEqual(result.status, 'error')
            self.assertEqual(network.call_count, 1)
            self.assertEqual(result.model, 'reviewer')

    def test_remaining_ttl_bounds_timeout_and_callback_runs_once(self):
        policy = self.allow(review_timeout_seconds=15)
        before = Mock(return_value=1.25)
        with policy_scope(self.store, policy), patch('urllib.request.urlopen', return_value=response({'allowed': True, 'reasons': []})) as network:
            self.assertTrue(review_candidate(CFG, 'answer', 'Ответ', before_request=before).allowed)
        before.assert_called_once()
        self.assertEqual(network.call_args.kwargs['timeout'], 1.25)

    def test_invalid_json_timeout_semantic_rejection_are_distinct(self):
        for value, status in [('not json', 'error'), ({'allowed': False, 'reasons': ['threat']}, 'blocked'),
                              ({'allowed': False, 'reasons': ['fraud']}, 'blocked')]:
            with patch('urllib.request.urlopen', return_value=response(value)):
                review = review_candidate(CFG, 'answer', 'Ответ')
                self.assertEqual(review.status, status)
                self.assertTrue(review.ai_attempted)
                self.assertFalse(review.allowed)
        with patch('urllib.request.urlopen', side_effect=TimeoutError):
            self.assertEqual(review_candidate(CFG, 'answer', 'Ответ').status, 'error')

    def test_policy_change_during_review_discards_exact_candidate(self):
        def http(*args, **kwargs):
            self.store.apply(SafetySettings())
            return response({'allowed': True, 'reasons': []})
        with patch('urllib.request.urlopen', side_effect=http):
            review = review_candidate(CFG, 'answer', 'Ответ')
        self.assertEqual(review.reasons, ('policy_changed',))
        self.assertFalse(review.text)
        self.assertTrue(review.ai_attempted)

    def test_comparison_separate_reviewer_does_not_change_generators(self):
        policy = self.allow(review_model_mode='separate', review_model='reviewer')
        with policy_scope(self.store, policy):
            snapshot = testing.make_snapshot(AUTH, ['one', 'two'], 'Привет.Как дела?', '', 'viewer')
        models = []
        def http(req, timeout):
            payload = json.loads(req.data)
            models.append(payload['model'])
            return response({'allowed': True, 'reasons': []} if payload['max_tokens'] == 160 else 'https://twitch.tv')
        with patch('urllib.request.urlopen', side_effect=http):
            for model in snapshot.models:
                self.assertEqual(testing.test_model(snapshot, model).answer, 'https://twitch.tv')
        self.assertEqual(models, ['one', 'reviewer', 'two', 'reviewer'])

    def test_diagnosis_local_no_api_and_exact_answer_format(self):
        policy = self.store.snapshot()
        with patch('urllib.request.urlopen') as network:
            for text in ('Расскажи, как защитить почту от спама', 'Покажи телефон в Cyberpunk', 'Привет.Как дела?'):
                review = diagnose(self.store, policy, text, 'question')
                self.assertEqual(review.status, 'local_allowed')
                self.assertEqual(diagnose(self.store, policy, text, 'answer').status, 'local_allowed')
            self.assertEqual(diagnose(self.store, policy, 'Привет\nКак дела?', 'answer').reasons, ('invalid_text',))
            self.assertEqual(diagnose(self.store, policy, '@viewer Привет', 'answer', target='viewer').status, 'local_allowed')
            network.assert_not_called()
        self.assertFalse(self.store.path.exists())
        self.assertFalse((self.root / 'message-history.sqlite3').exists())

    def test_diagnosis_ai_exact_candidate_one_call_without_generation(self):
        policy = self.allow(review_model_mode='separate', review_model='reviewer')
        text = '  Заебись, опять катка!  '
        with patch('urllib.request.urlopen', return_value=response({'allowed': True, 'reasons': []})) as network:
            result = diagnose(self.store, policy, text, 'answer', ai=True, auth=AUTH, answer_model='answer')
        self.assertEqual(network.call_count, 1)
        payload = json.loads(network.call_args.args[0].data)
        self.assertEqual(payload['model'], 'reviewer')
        self.assertEqual(json.loads(payload['messages'][-1]['content'])['candidate'], text)
        self.assertEqual(result.status, 'allowed')
        self.assertFalse(result.text)
        self.assertFalse((self.root / 'message-history.sqlite3').exists())

    def test_diagnosis_local_block_question_and_cancel_never_call_ai(self):
        with patch('urllib.request.urlopen') as network:
            for text, kind in [('evil.invalid', 'answer'), ('alex @ example . invalid', 'question'), ('Привет', 'question')]:
                result = diagnose(self.store, self.store.snapshot(), text, kind, ai=True, auth=AUTH, answer_model='answer')
                self.assertFalse(result.ai_attempted)
                self.assertNotIn(text, describe_review(result))
                self.assertFalse(result.text)
            result = diagnose(self.store, self.store.snapshot(), 'Привет', 'answer', ai=True, auth=AUTH, answer_model='answer', cancelled=lambda: True)
            self.assertEqual(result.status, 'cancelled')
            network.assert_not_called()

    def test_bounded_history_safe_metadata_compatible_old_database(self):
        journal = MessageHistory(self.root)
        journal.add('reward', 'sent', question='Привет', answer='Ответ')
        for _ in range(205):
            journal.safety_event('reward', SafetyReview('blocked', 'secret candidate', ('privacy_blocked',)))
        rows = journal.safety_recent(200)
        self.assertEqual(len(rows), 200)
        self.assertEqual(len(journal.safety_recent()), 20)
        self.assertNotIn('secret candidate', json.dumps(rows))
        self.assertNotIn('secret candidate', journal.path.read_bytes().decode('latin1'))
        self.assertEqual(len(journal.page()['rows']), 1)


class RuntimePolicyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        with patch.object(bot, 'ROOT', self.root):
            self.instance = bot.Bot(dict(CFG))
        self.addCleanup(self.instance.autonomous.close)
        self.store = self.instance.safety_store
        self.writer = Mock()
        self.writer.drain = AsyncMock()

    async def reward(self):
        queue = asyncio.Queue()
        queue.put_nowait(('viewer', '1', 'Привет', 'redeem'))
        task = asyncio.create_task(self.instance.worker(self.writer, queue))
        try:
            await asyncio.wait_for(queue.join(), 4)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_live_apply_allowlist_reward_and_real_fallback_reviewer(self):
        self.store.apply(SafetySettings(link_mode='allow_domains', allowed_domains=('twitch.tv',)))
        calls = []
        def http(req, timeout):
            payload = json.loads(req.data)
            calls.append(payload['model'])
            if len(calls) == 1:
                raise urllib.error.URLError('offline')
            return response({'allowed': True, 'reasons': []} if payload['max_tokens'] == 160 else 'https://twitch.tv')
        with patch('urllib.request.urlopen', side_effect=http):
            await self.reward()
        self.assertEqual(calls, ['answer', 'backup', 'backup'])
        self.assertIn('https://twitch.tv', self.writer.write.call_args.args[0].decode())
        self.assertEqual(self.instance.history.safety_recent()[0]['model'], 'backup')
        self.instance.last_sent = 0
        self.instance.histories.clear()
        self.store.apply(SafetySettings())
        with patch('urllib.request.urlopen', return_value=response('https://twitch.tv')) as network:
            await self.reward()
        self.assertEqual(network.call_count, 1)
        self.assertFalse(self.instance.histories)
        self.assertNotIn('https://twitch.tv', self.writer.write.call_args.args[0].decode())

    async def test_policy_change_after_review_before_write_never_reserves(self):
        with policy_scope(self.store):
            approval = SafetyReview('allowed', '@viewer Ответ')
            self.store.apply(SafetySettings())
            reserve = Mock()
            with self.assertRaises(SafetyBlocked) as caught:
                await self.instance.say_autonomous(self.writer, '@viewer Ответ', lambda: True, reserve, approval=approval)
        self.assertEqual(caught.exception.review.reasons, ('policy_changed',))
        reserve.assert_not_called()
        self.writer.write.assert_not_called()

    async def test_policy_change_during_reward_rate_wait_denies_before_card_reservation(self):
        with policy_scope(self.store):
            approval = SafetyReview('allowed', '@viewer Ответ')
            self.instance.last_sent = __import__('time').monotonic()
            async def wait(seconds):
                self.store.apply(SafetySettings())
            with patch('bot.asyncio.sleep', side_effect=wait), patch.object(self.instance.local_context, 'reserve_publish') as reserve:
                with self.assertRaises(SafetyBlocked):
                    await self.instance.say(self.writer, '@viewer Ответ', approval=approval, creative_card_id='card')
            reserve.assert_not_called()
        self.writer.write.assert_not_called()

    async def test_policy_change_during_reward_review_refuses_and_no_memory(self):
        def http(req, timeout):
            payload = json.loads(req.data)
            if payload['max_tokens'] == 160:
                self.store.apply(SafetySettings())
                return response({'allowed': True, 'reasons': []})
            return response('Неопубликованный кандидат')
        with patch('urllib.request.urlopen', side_effect=http):
            await self.reward()
        self.assertFalse(self.instance.histories)
        self.assertNotIn('Неопубликованный кандидат', self.writer.write.call_args.args[0].decode())
        self.assertEqual(self.instance.history.safety_recent()[0]['reasons'], ('policy_changed',))

    async def test_corrupt_settings_cannot_publish_even_fixed_notice(self):
        self.store.path.write_text('{broken')
        with policy_scope(self.store), self.assertRaises(SafetyBlocked):
            await self.instance.say(self.writer, '@viewer ' + __import__('safety').SAFETY_REFUSAL)
        self.writer.write.assert_not_called()


class AutonomousPolicyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.now = 100.
        auto.save_settings(self.root / 'autonomous.json', replace(auto.AutoSettings(), enabled=True,
            mode='publish', min_messages=1, min_authors=1, request_hourly_limit=3))
        self.journal = MessageHistory(self.root)
        self.events = []
        self.controller = auto.Autonomous(dict(CFG), self.root, clock=lambda: self.now,
            choose_delay=lambda a, b: a, emit=self.events.append, history_store=self.journal)
        self.addCleanup(self.controller.close)
        self.store = self.controller.safety_store
        self.store.apply(SafetySettings(link_mode='allow_domains', allowed_domains=('twitch.tv',),
            review_model_mode='separate', review_model='reviewer'))
        class Executor:
            def submit(self, callback, *args, **kwargs):
                self.callback, self.args, self.kwargs = callback, args, kwargs
                self.future = Future()
                return self.future
            def shutdown(self, **kwargs):
                pass
        self.executor = Executor()
        self.controller.executor = self.executor
        self.sender = AsyncMock(return_value=True)
        self.controller.connect(self.sender, lambda: False)
        self.controller.receive('@id=msg;user-id=1 :viewer!x PRIVMSG #channel :Где посмотреть игровой канал?')

    async def start(self):
        self.now += 3
        await self.controller.tick()
        return self.controller.pending

    async def generate(self):
        await self.start()
        models = []
        def http(req, timeout):
            payload = json.loads(req.data)
            models.append(payload['model'])
            if len(models) == 1:
                return response(dict(action='reply', conversation=[1], basis=[1], target='viewer', reason='answer', intent='Ссылка на канал'))
            if len(models) == 2:
                return response({'text': 'https://twitch.tv'})
            return response({'allowed': True, 'reasons': []})
        with patch('urllib.request.urlopen', side_effect=http):
            result = await asyncio.to_thread(self.executor.callback, *self.executor.args, **self.executor.kwargs)
            self.executor.future.set_result(result)
            await self.controller.tick()
        return models

    async def test_allowed_domain_all_stages_three_attempts_and_separate_reviewer(self):
        models = await self.generate()
        self.assertEqual(models, ['answer', 'answer', 'reviewer'])
        self.assertEqual(len(self.controller.requests), 3)
        self.sender.assert_awaited_once()
        self.assertEqual(self.sender.call_args.args[0], '@viewer https://twitch.tv')
        self.assertEqual(self.journal.safety_recent()[0]['model'], 'reviewer')

    async def test_preview_uses_same_allowlist_review_and_no_card_publication(self):
        self.controller.settings = replace(self.controller.settings, mode='preview')
        models = await self.generate()
        self.assertEqual(models[-1], 'reviewer')
        self.sender.assert_not_called()
        self.assertEqual(self.events[-1]['status'], 'preview')
        self.assertEqual(self.journal.safety_recent()[0]['scenario'], 'preview')

    async def test_policy_change_before_result_consumption_no_review_no_quota(self):
        pending = await self.start()
        self.executor.future.set_result(Decision('reply', 'https://twitch.tv', 'viewer', (1,), 'answer'))
        self.store.apply(SafetySettings())
        with patch('urllib.request.urlopen') as network:
            await self.controller.tick()
        network.assert_not_called()
        self.sender.assert_not_called()
        self.assertFalse(self.controller.quota)
        self.assertEqual(self.journal.safety_recent()[0]['reasons'], ('policy_changed',))

    async def test_policy_change_during_review_keeps_attempt_count_no_publication(self):
        await self.start()
        self.executor.future.set_result(Decision('reply', 'https://twitch.tv', 'viewer', (1,), 'answer'))
        def http(*args, **kwargs):
            self.store.apply(SafetySettings())
            return response({'allowed': True, 'reasons': []})
        with patch('urllib.request.urlopen', side_effect=http) as network:
            await self.controller.tick()
        self.assertEqual(network.call_count, 1)
        self.assertEqual(len(self.controller.requests), 1)
        self.sender.assert_not_called()
        self.assertFalse(self.controller.quota)
        row = self.journal.safety_recent()[0]
        self.assertEqual(row['reasons'], ('policy_changed',))
        self.assertTrue(row['ai_attempted'])

    async def test_unavailable_reviewer_and_exhausted_budget_never_publish(self):
        for remaining in (3, 0):
            await self.start()
            self.executor.future.set_result(Decision('reply', 'https://twitch.tv', 'viewer', (1,), 'answer'))
            self.controller.requests = [] if remaining else [self.now] * 3
            with patch('urllib.request.urlopen', side_effect=urllib.error.URLError('offline')) as network:
                await self.controller.tick()
            self.assertEqual(network.call_count, 1 if remaining else 0)
            self.sender.assert_not_called()
            self.assertFalse(self.controller.quota)
            if remaining:
                self.assertEqual(self.journal.safety_recent()[0]['status'], 'error')
            self.now += 300
            self.controller.next_check = 0
            self.controller.last_checked = 0
            self.controller.receive(f'@id=msg{remaining};user-id=1 :viewer!x PRIVMSG #channel :Ещё новый вопрос про канал {remaining}?')


if __name__ == '__main__':
    unittest.main()
