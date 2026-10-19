"""Real builders with a replaced HTTPS boundary; no keys, Twitch or paid API."""
import asyncio
from dataclasses import asdict, replace
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest.mock import AsyncMock, Mock, patch

import ai_client
import ai_connection as connection
import bot
import prompt_builder
import settings
import start
import testing
from configuration import env_content, read_config
from participation import Plan, request_decision
from safety import review_candidate
from safety_diagnostics import diagnose
from safety_settings import PolicyStore, SafetySettings, policy_scope, validate_settings


COMPATIBLE = dict(TWITCH_CHANNEL='channel', TWITCH_BOT_NAME='helper', TWITCH_CLIENT_ID='client',
    AI_BASE_URL='https://compatible.invalid/v1', AI_API_KEY='compatible-test-key',
    AI_MODEL='custom-answer', AI_FALLBACK_MODELS='custom-backup')
OPENAI = dict(AI_PROVIDER='openai', AI_OPENAI_API_KEY='official-test-key',
              AI_OPENAI_MODEL=connection.OPENAI_MODEL)


def response(content, *, finish='stop', refusal=None, reasoning=None):
    if not isinstance(content, str) and content is not None:
        content = json.dumps(content, ensure_ascii=False)
    return io.BytesIO(json.dumps(dict(choices=[dict(message=dict(content=content, refusal=refusal,
                         reasoning_content=reasoning), finish_reason=finish)])).encode())


def official(**extra):
    return connection.Credentials('https://ignored.invalid/v1', 'official-test-key', 'openai',
                                  model=connection.OPENAI_MODEL).config() | extra


class ProfileSettingsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def save(self, values):
        settings.save_settings(values, 'Сохранённый характер', self.root, allow_incomplete=True)
        return read_config(self.root / '.env')

    def test_legacy_even_official_looking_url_stays_compatible_without_writes(self):
        for url in (COMPATIBLE['AI_BASE_URL'], connection.OPENAI_BASE_URL):
            raw = COMPATIBLE | dict(AI_BASE_URL=url)
            self.root.joinpath('.env').write_text(env_content(raw), encoding='utf-8')
            before = self.root.joinpath('.env').read_bytes()
            values, _ = settings.load_settings(self.root)
            self.assertEqual(values['AI_PROVIDER'], 'compatible')
            self.assertEqual(values['AI_API_KEY'], raw['AI_API_KEY'])
            self.assertEqual(values['AI_BASE_URL'], url)
            self.assertEqual(before, self.root.joinpath('.env').read_bytes())

    def test_repeatable_migration_and_switching_keep_separate_secrets_and_models(self):
        self.save(COMPATIBLE)
        for name in ('memory.json', 'profiles.json', 'message-history.sqlite3'):
            self.root.joinpath(name).write_bytes(b'unchanged fixture')
        values = self.save(OPENAI)
        self.assertEqual(values['AI_BASE_URL'], connection.OPENAI_BASE_URL)
        self.assertEqual(values['AI_API_KEY'], OPENAI['AI_OPENAI_API_KEY'])
        self.assertEqual(values['AI_FALLBACK_MODELS'], '')
        for _ in range(3):
            before = read_config(self.root / '.env')
            self.assertEqual(connection.migrate(before), connection.migrate(connection.migrate(before)))
            values = self.save(dict(AI_PROVIDER='compatible', AI_API_KEY=''))
            self.assertEqual(values['AI_API_KEY'], COMPATIBLE['AI_API_KEY'])
            self.assertEqual(values['AI_MODEL'], 'custom-answer')
            values = self.save(dict(AI_PROVIDER='openai', AI_API_KEY=''))
            self.assertEqual(values['AI_API_KEY'], OPENAI['AI_OPENAI_API_KEY'])
            self.assertEqual(values['AI_MODEL'], connection.OPENAI_MODEL)
            self.assertEqual(values['AI_COMPATIBLE_FALLBACK_MODELS'], 'custom-backup')
        for name in ('memory.json', 'profiles.json', 'message-history.sqlite3'):
            self.assertEqual(self.root.joinpath(name).read_bytes(), b'unchanged fixture')

    def test_blank_openai_key_never_uses_compatible_key_or_deepseek_defaults(self):
        old = self.save(COMPATIBLE)
        draft = dict(AI_PROVIDER='openai', AI_API_KEY='')
        with self.assertRaisesRegex(ValueError, 'API-ключ'):
            connection.selected_values(draft, old)
        result = self.save(draft)
        self.assertEqual(result['AI_API_KEY'], '')
        self.assertEqual(result['AI_MODEL'], connection.OPENAI_MODEL)
        self.assertEqual(result['AI_FALLBACK_MODELS'], '')
        self.assertEqual(result['AI_COMPATIBLE_API_KEY'], COMPATIBLE['AI_API_KEY'])

    def test_changed_host_cannot_reuse_saved_key_but_explicit_new_key_works(self):
        old = self.save(COMPATIBLE)
        for url in ('https://another.invalid/v1', 'https://compatible.invalid:9443/v1'):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, 'заново'):
                connection.selected_values(dict(AI_BASE_URL=url, AI_API_KEY=''), old)
        result = connection.selected_values(dict(AI_BASE_URL='https://another.invalid/v1',
                                AI_API_KEY='new-server-test-key'), old)
        self.assertEqual(result['AI_API_KEY'], 'new-server-test-key')
        # A same-origin API path change is safe, but still invalidates catalogs.
        result = connection.selected_values(dict(AI_BASE_URL='https://compatible.invalid/api', AI_API_KEY=''), old)
        self.assertEqual(result['AI_API_KEY'], COMPATIBLE['AI_API_KEY'])

    def test_manual_endpoint_edit_cannot_bypass_persisted_origin_binding(self):
        raw = self.save(COMPATIBLE)
        raw['AI_COMPATIBLE_BASE_URL'] = 'https://another.invalid/v1'
        raw['AI_BASE_URL'] = 'https://another.invalid/v1'
        self.root.joinpath('.env').write_text(env_content(raw), encoding='utf-8')
        with patch.object(bot, 'ROOT', self.root), patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'заново'):
                bot.config()

    def test_ai_only_save_preserves_both_twitch_tokens_and_all_twitch_fields(self):
        self.save(COMPATIBLE)
        for name in ('.twitch_token.json', '.twitch_broadcaster_token.json'):
            self.root.joinpath(name).write_bytes(b'fake-token-not-used')
        result = self.save(OPENAI)
        self.assertEqual(result['TWITCH_CHANNEL'], 'channel')
        for name in ('.twitch_token.json', '.twitch_broadcaster_token.json'):
            self.assertEqual(self.root.joinpath(name).read_bytes(), b'fake-token-not-used')

    def test_cli_official_needs_no_endpoint_and_bot_resolves_same_profile(self):
        self.root.joinpath('.env').write_text(env_content(COMPATIBLE), encoding='utf-8')
        answers = ['', '', '', '', 'openai', '', '']
        with patch('builtins.input', side_effect=answers), patch('start.getpass.getpass', return_value='official-test-key'):
            start.setup(self.root / '.env', force=True)
        raw = read_config(self.root / '.env')
        self.assertEqual(raw['AI_PROVIDER'], 'openai')
        self.assertTrue(start.complete(raw))
        self.assertEqual(raw['AI_COMPATIBLE_API_KEY'], COMPATIBLE['AI_API_KEY'])
        with patch.object(bot, 'ROOT', self.root), patch.dict(os.environ, {}, clear=True):
            cfg = bot.config()
        self.assertEqual(cfg['AI_API_KEY'], 'official-test-key')
        self.assertEqual(cfg['AI_CHAT_URL'], connection.OPENAI_BASE_URL + '/chat/completions')
        self.assertEqual(cfg['AI_MODEL'], connection.OPENAI_MODEL)
        self.assertEqual(cfg['AI_FALLBACK_MODELS'], '')

    def test_old_review_settings_migrate_in_memory_and_models_are_provider_scoped(self):
        legacy = asdict(SafetySettings(review_model_mode='separate', review_model='compatible-review'))
        legacy.pop('openai_review_model_mode')
        legacy.pop('openai_review_model')
        policy = PolicyStore(self.root).apply(validate_settings(legacy))
        self.assertEqual(policy.model_for('official-answer', 'openai'), 'official-answer')
        self.assertEqual(policy.model_for('compatible-answer'), 'compatible-review')

    @unittest.skipUnless(os.name == 'nt', 'Windows sharing violations only')
    def test_short_windows_lock_recovers_and_permanent_failure_preserves_saved_config(self):
        self.save(COMPATIBLE)
        original_replace = os.replace
        calls = []
        error = PermissionError('fixture sharing violation')
        error.winerror = 32
        def transient(source, destination):
            calls.append(source)
            if len(calls) == 1:
                raise error
            return original_replace(source, destination)
        with patch('settings.os.replace', side_effect=transient):
            self.save(OPENAI)
        self.assertEqual(read_config(self.root / '.env')['AI_PROVIDER'], 'openai')
        before = self.root.joinpath('.env').read_bytes()
        with patch('settings.os.replace', side_effect=error) as replace_mock:
            with self.assertRaises(PermissionError):
                self.save(dict(AI_PROVIDER='compatible', AI_API_KEY=''))
        self.assertEqual(replace_mock.call_count, 4)
        self.assertEqual(before, self.root.joinpath('.env').read_bytes())


class ProviderTransportTests(unittest.TestCase):
    def complete(self, cfg=None, model=connection.OPENAI_MODEL, **kwargs):
        return ai_client.request_completion(cfg or official(), model,
            [{'role': 'system', 'content': 'Обязательная инструкция'}, {'role': 'user', 'content': 'Привет'}], **kwargs)

    def test_official_address_key_payload_and_untrusted_url_override(self):
        with patch('ai_connection.urlopen', return_value=response('Привет!')) as network:
            self.assertEqual(self.complete(official(AI_BASE_URL='https://other.invalid', AI_CHAT_URL='https://other.invalid/steal')), 'Привет!')
        request = network.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.openai.com/v1/chat/completions')
        self.assertEqual(request.get_header('Authorization'), 'Bearer official-test-key')
        payload = json.loads(request.data)
        self.assertEqual(payload['model'], connection.OPENAI_MODEL)
        self.assertEqual(payload['max_completion_tokens'], 512)
        self.assertFalse(payload['store'])
        for key in ('max_tokens', 'temperature', 'top_p', 'reasoning_effort'):
            self.assertNotIn(key, payload)
        self.assertEqual(payload['messages'][0]['role'], 'system')

    def test_compatible_keeps_exact_legacy_payload_on_any_https_provider(self):
        cfg = COMPATIBLE | dict(AI_CHAT_URL=COMPATIBLE['AI_BASE_URL'] + '/chat/completions')
        with patch('ai_connection.urlopen', return_value=response('Привет')) as network:
            self.complete(cfg, 'custom-exact-id', max_tokens=160)
        request = network.call_args.args[0]
        self.assertEqual(request.full_url, cfg['AI_CHAT_URL'])
        self.assertEqual(request.get_header('Authorization'), 'Bearer compatible-test-key')
        self.assertEqual(set(json.loads(request.data)), {'model', 'messages', 'max_tokens', 'stream'})
        self.assertEqual(json.loads(request.data)['max_tokens'], 160)

    def test_openai_compatible_standard_is_not_restricted_to_openai_models(self):
        for model in ('deepseek-chat', 'anthropic/synthetic-claude-id', 'qwen/synthetic-model', 'custom/vendor-id'):
            with self.subTest(model=model), patch('ai_connection.urlopen', return_value=response('Текст провайдера')) as network:
                self.assertEqual(self.complete(COMPATIBLE, model), 'Текст провайдера')
                request = network.call_args.args[0]
                self.assertEqual(request.full_url, 'https://compatible.invalid/v1/chat/completions')
                self.assertEqual(request.get_header('Authorization'), 'Bearer compatible-test-key')
                self.assertEqual(json.loads(request.data)['model'], model)
                self.assertEqual(json.loads(request.data)['max_tokens'], 512)
                self.assertEqual(connection.chat_compatibility('compatible', model), '')

    def test_reasoning_budgets_and_instruction_roles_are_bounded_without_parameter_probes(self):
        for model in ('gpt-5-mini', 'gpt-5.4', 'o3-mini', 'o4-mini', 'o1'):
            with self.subTest(model=model), patch('ai_connection.urlopen', return_value=response('Ответ')) as network:
                self.complete(model=model, max_tokens=160)
                self.assertEqual(network.call_count, 1)
                payload = json.loads(network.call_args.args[0].data)
                self.assertEqual(payload['max_completion_tokens'], 4096)
                self.assertEqual(payload['reasoning_effort'], 'low')
                self.assertEqual(payload['messages'][0]['role'], 'developer')
                self.assertNotIn('temperature', payload)
                self.assertEqual(network.call_args.kwargs['timeout'], 20)

    def test_catalog_uses_authenticated_get_and_does_not_promise_chat_support(self):
        auth = connection.Credentials('', 'official-test-key', 'openai')
        data = {'data': [{'id': 'gpt-4.1-mini'}, {'id': 'text-embedding-3-small'}, {'id': 'unknown-exact-id'}]}
        with patch('ai_connection.urlopen', return_value=io.BytesIO(json.dumps(data).encode())) as network:
            models = testing.fetch_models(auth)
        request = network.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.openai.com/v1/models')
        self.assertEqual(request.get_method(), 'GET')
        self.assertEqual(request.get_header('Authorization'), 'Bearer official-test-key')
        self.assertTrue(next(m for m in models if m.id.startswith('text-embedding')).chat_error)
        self.assertIsNone(next(m for m in models if m.id == 'unknown-exact-id').available)
        self.assertEqual(next(m for m in models if m.id == 'unknown-exact-id').chat_error, '')

    def test_chat_variants_do_not_receive_reasoning_only_parameters(self):
        for model in ('gpt-5-chat-latest', 'gpt-5.1-chat-latest'):
            with self.subTest(model=model), patch('ai_connection.urlopen', return_value=response('Ответ')) as network:
                self.complete(model=model)
                payload = json.loads(network.call_args.args[0].data)
                self.assertNotIn('reasoning_effort', payload)
                self.assertEqual(payload['max_completion_tokens'], 512)
                self.assertEqual(payload['messages'][0]['role'], 'developer')

    def test_known_nonchat_model_rejected_before_http_or_quota_reservation(self):
        reserve = Mock()
        with patch('ai_connection.urlopen') as network, self.assertRaisesRegex(ValueError, 'Chat Completions'):
            self.complete(model='text-embedding-3-small', before_request=reserve)
        network.assert_not_called()
        reserve.assert_not_called()

    def test_redirects_never_forward_credentials_or_start_another_http_attempt(self):
        request = connection.authenticated_request('https://api.openai.com/v1/models', 'official-test-key')
        handler = connection.NoRedirect()
        for code in (301, 302, 303, 307, 308):
            self.assertIsNone(handler.redirect_request(request, None, code, 'redirect', {}, 'https://other.invalid/steal'))
        self.assertNotIn('Authorization', request.headers)
        self.assertEqual(request.get_header('Authorization'), 'Bearer official-test-key')
        with patch('ai_connection.urlopen', side_effect=urllib.error.HTTPError(request.full_url, 302, 'redirect', {}, io.BytesIO())):
            with self.assertRaisesRegex(RuntimeError, 'Перенаправление'):
                self.complete()

    def test_refusal_empty_reasoning_only_and_truncated_never_become_bot_text(self):
        cases = [response(None, refusal='PRIVATE REFUSAL'), response(None, reasoning='PRIVATE REASONING'),
                 response('', finish='length', reasoning='PRIVATE REASONING'), response('Обрезанный', finish='length')]
        for result in cases:
            with patch('ai_connection.urlopen', return_value=result), self.assertRaises(RuntimeError) as caught:
                self.complete()
            self.assertNotIn('PRIVATE', str(caught.exception))
        with patch('ai_connection.urlopen', return_value=response('Видимый ответ', reasoning='PRIVATE REASONING')):
            self.assertEqual(self.complete(), 'Видимый ответ')

    def test_errors_are_actionable_and_never_echo_keys(self):
        cases = [(401, 'invalid_api_key', 'Ключ отклонён'), (403, 'permission_denied', 'Нет доступа'),
                 (404, 'model_not_found', 'Модель не найдена'), (429, 'rate_limit_exceeded', 'частоты'),
                 (429, 'insufficient_quota', 'API-квота')]
        for status, code, caption in cases:
            error = urllib.error.HTTPError('https://api.openai.com/v1/chat/completions', status, 'err', {},
                io.BytesIO(json.dumps({'error': {'code': code, 'message': 'official-test-key rejected'}}).encode()))
            with self.subTest(status=status, code=code), patch('ai_connection.urlopen', side_effect=error):
                with self.assertRaises(RuntimeError) as caught:
                    self.complete()
                self.assertIn(caption, str(caught.exception))
                self.assertNotIn('official-test-key', str(caught.exception))
        auth = connection.Credentials('', 'official-test-key', 'openai')
        self.assertNotIn(auth.api_key, repr(auth))
        self.assertNotIn(auth.api_key, auth.identity)

    def test_generation_improvement_testing_and_diagnosis_share_official_transport(self):
        auth = connection.Credentials('', 'official-test-key', 'openai', model=connection.OPENAI_MODEL)
        for operation in ('create', 'improve'):
            generation = prompt_builder.prepare_generation(auth, connection.OPENAI_MODEL, 'Короче и живее',
                'Дружелюбно', 'Игры', prompt_builder.compose_prompt('Дружелюбный характер', 'Игры') if operation == 'improve' else '', operation)
            with patch('ai_connection.urlopen', return_value=response({'style': 'Дружелюбный характер, короткие ясные ответы.'})) as network:
                result = prompt_builder.generate_prompt(generation)
            self.assertIn('Дружелюбный', result)
            self.assertEqual(network.call_args.args[0].full_url, 'https://api.openai.com/v1/chat/completions')
        with tempfile.TemporaryDirectory() as temp, policy_scope(PolicyStore(temp)):
            snapshot = testing.make_snapshot(auth, ['exact-good', 'exact-bad'], 'Привет', 'Несохранённый характер', 'viewer')
            with patch('ai_connection.urlopen', side_effect=[response('Ответ'), response({'allowed': True, 'reasons': []}),
                    urllib.error.URLError('offline')]) as network:
                good = testing.test_model(snapshot, 'exact-good')
                bad = testing.test_model(snapshot, 'exact-bad')
            self.assertEqual(good.answer, 'Ответ')
            self.assertTrue(bad.error)
            self.assertEqual([json.loads(call.args[0].data)['model'] for call in network.call_args_list],
                             ['exact-good', 'exact-good', 'exact-bad'])
            self.assertIn('Несохранённый характер', str(json.loads(network.call_args_list[0].args[0].data)))
            store = PolicyStore(temp)
            with patch('ai_connection.urlopen', return_value=response({'allowed': True, 'reasons': []})) as network:
                verdict = diagnose(store, store.snapshot(), 'Ответ', 'answer', ai=True,
                                   auth=auth, answer_model=auth.model)
            self.assertTrue(verdict.allowed)
            self.assertEqual(json.loads(network.call_args.args[0].data)['model'], connection.OPENAI_MODEL)
            self.assertFalse(Path(temp, 'message-history.sqlite3').exists())

    def test_separate_review_models_do_not_cross_provider_boundary(self):
        with tempfile.TemporaryDirectory() as temp:
            store = PolicyStore(temp)
            policy = store.apply(SafetySettings(review_model_mode='separate', review_model='compatible-review',
                openai_review_model_mode='separate', openai_review_model='official-review'))
            for cfg, expected in ((official(), 'official-review'), (COMPATIBLE, 'compatible-review')):
                with policy_scope(store, policy), patch('ai_connection.urlopen', return_value=response({'allowed': True, 'reasons': []})) as network:
                    self.assertTrue(review_candidate(cfg, 'answer', 'Ответ').allowed)
                self.assertEqual(json.loads(network.call_args.args[0].data)['model'], expected)


class LiveProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_official_autonomous_selector_generator_reviewer_and_preview_keep_http_accounting(self):
        import test_autonomous_privacy_regression as fixtures
        for mode in ('publish', 'preview'):
            with self.subTest(mode=mode):
                fixture = fixtures.ControllerPrivacyRegression('run')
                fixture.setUp()
                try:
                    fixture.settings = replace(fixture.settings, mode=mode)
                    http, pending, record, event = await fixture.execute(cfg=official())
                    self.assertEqual(http.call_count, 3)
                    self.assertEqual(event['http_attempts'], (1, 1, 1))
                    self.assertEqual(record['status'], 'sent' if mode == 'publish' else 'preview')
                    self.assertEqual(len(fixture.sent), 1 if mode == 'publish' else 0)
                    for call in http.call_args_list:
                        self.assertEqual(call.args[0].full_url, 'https://api.openai.com/v1/chat/completions')
                        self.assertEqual(call.args[0].get_header('Authorization'), 'Bearer official-test-key')
                        self.assertIn('max_completion_tokens', json.loads(call.args[0].data))
                finally:
                    fixture.tearDown()

    async def test_official_reward_only_generates_and_updates_successful_memory(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(bot, 'ROOT', Path(temp)):
            instance = bot.Bot(COMPATIBLE | official())
            self.addCleanup(instance.autonomous.close)
            writer = Mock()
            writer.drain = AsyncMock()
            queue = asyncio.Queue()
            queue.put_nowait(('viewer', '1', 'Привет', 'redeem'))
            with patch('ai_connection.urlopen', return_value=response('Обычный ответ')) as network:
                task = asyncio.create_task(instance.worker(writer, queue))
                try:
                    await asyncio.wait_for(queue.join(), 4)
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            self.assertEqual(network.call_count, 1)
            self.assertEqual(network.call_args.args[0].full_url, 'https://api.openai.com/v1/chat/completions')
            self.assertIn('Обычный ответ', writer.write.call_args.args[0].decode())
            self.assertFalse(instance.history.safety_recent()[0]['ai_attempted'])
            self.assertEqual(instance.histories['1'][-1][1], 'Обычный ответ')
