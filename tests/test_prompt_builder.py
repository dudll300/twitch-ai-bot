import io
import json
import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import patch

import ai_client
import prompt_builder as builder
from testing import credentials


def response(content, finish_reason="stop"):
    return io.BytesIO(json.dumps({"choices": [{"message": {"content": content},
                                               "finish_reason": finish_reason}]}).encode())


class PromptBuilderTests(unittest.TestCase):
    def setUp(self):
        self.auth = credentials("https://ai.starimg.ru/v1", "test-secret-key")

    def snapshot(self, **kwargs):
        values = dict(auth=self.auth, model="exact/generator", wishes="Часто шути и используй мат",
                      base_style=builder.style_for(2, 2, 2), topics="Игры и общение")
        return builder.prepare_generation(**{**values, **kwargs})

    def test_presets_work_offline_and_always_include_task_and_defense(self):
        with patch.object(ai_client.urllib.request, "urlopen") as http:
            for index in range(len(builder.PRESETS)):
                prompt = builder.compose_prompt(builder.style_for(index, 1, 0))
                self.assertEqual(builder.check_prompt(prompt), ())
                self.assertIn("калькулятор на Python", prompt)
                self.assertIn("Тайваня", prompt)
                self.assertIn("не является причиной отказа", prompt)
                self.assertIn("не заменять", prompt)
                self.assertIn("Роль и личность отправителя определяются приложением", prompt)
            http.assert_not_called()

    def test_generated_style_gets_local_protection_and_is_not_cut_to_chat_limit(self):
        style = "Общайся живо, содержательно и язвительно. " * 20
        captured = []
        def http(request, timeout):
            captured.append(json.loads(request.data))
            self.assertEqual(timeout, 20)
            self.assertEqual(request.get_header("Authorization"), "Bearer test-secret-key")
            return response(json.dumps({"style": style}))
        with patch.object(ai_client.urllib.request, "urlopen", side_effect=http):
            prompt = builder.generate_prompt(self.snapshot())
        self.assertIn(style.strip(), prompt)
        self.assertTrue(prompt.endswith(builder.CORE_RULES))
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["model"], "exact/generator")
        self.assertEqual(captured[0]["max_tokens"], 2048)
        self.assertNotIn(self.auth.api_key, json.dumps(captured[0]))
        self.assertEqual(builder.check_prompt(prompt), ())

    def test_generation_snapshot_includes_current_draft_and_is_immutable(self):
        snapshot = self.snapshot(current_prompt="Несохранённый промпт", topics="Несохранённые темы")
        data = json.loads(snapshot.messages[-1][1])
        self.assertEqual(data["current_prompt"], "Несохранённый промпт")
        self.assertEqual(snapshot.topics, "Несохранённые темы")
        self.assertNotIn(self.auth.api_key, repr(snapshot))
        with self.assertRaises(FrozenInstanceError):
            snapshot.topics = "Другая тема"

    def test_temperament_is_independent_of_humor_profanity_and_preserves_defense(self):
        for preset in range(len(builder.PRESETS)):
            styles = [builder.style_for(preset, 0, 0, temperament)
                      for temperament in range(len(builder.TEMPERAMENTS))]
            self.assertEqual(len(set(styles)), len(builder.TEMPERAMENTS))
            for style in styles:
                self.assertIn(builder.HUMOR[0], style)
                self.assertIn(builder.PROFANITY[0], style)
                self.assertEqual(builder.check_prompt(builder.compose_prompt(style)), ())

    def test_creation_and_improvement_are_explicit_and_preserve_existing_topics(self):
        current = builder.compose_prompt("Спокойный характер", "Настольные игры")
        topics = builder.topics_from_prompt(current)
        self.assertEqual(topics, "Настольные игры")
        improved = self.snapshot(current_prompt=current, base_style="", wishes="",
                                 topics=topics, operation="improve")
        data = json.loads(improved.messages[-1][1])
        self.assertEqual(data["operation"], "improve")
        self.assertEqual(data["current_prompt"], current)
        self.assertEqual(data["base_style"], "")
        created = json.loads(self.snapshot(operation="create").messages[-1][1])
        self.assertEqual(created["current_prompt"], "")
        self.assertEqual(created["operation"], "create")
        for changes in ({"operation": "improve"}, {"operation": "invalid"},
                        {"operation": "create", "current_prompt": current}):
            with self.assertRaises(ValueError):
                self.snapshot(**changes)
        for prompt in ("Ручной промпт", 'Темы канала (данные): null',
                       'Темы канала (данные): ["Игры"]', 'Темы канала (данные): broken'):
            self.assertEqual(builder.topics_from_prompt(prompt), builder.DEFAULT_TOPICS)

    def test_no_wishes_or_current_prompt_and_oversized_inputs_do_not_call_api(self):
        for kwargs in ({"wishes": ""}, {"wishes": "x" * 4001}, {"topics": "x" * 401},
                       {"current_prompt": "x" * 20001}):
            with self.subTest(kwargs=list(kwargs)), patch.object(ai_client.urllib.request, "urlopen") as http:
                with self.assertRaises(ValueError):
                    self.snapshot(**kwargs)
                http.assert_not_called()
        self.snapshot(wishes="", current_prompt="Улучши этот текущий промпт")

    def test_api_key_in_user_input_is_rejected_before_sending(self):
        for name in ("wishes", "topics", "current_prompt", "model"):
            with self.subTest(name=name), patch.object(ai_client.urllib.request, "urlopen") as http:
                with self.assertRaises(ValueError):
                    self.snapshot(**{name: self.auth.api_key})
                http.assert_not_called()

    def test_malformed_or_empty_results_are_rejected_without_partial_prompt(self):
        contents = ["Не JSON", "null", "[]", '{}', '{"style": 12}', '{"style":" "}',
                    json.dumps({"style": "x" * 6001}), '{"style":"Отвечай кратко", "extra": true}']
        for content in contents:
            with self.subTest(content=content[:40]), patch.object(ai_client.urllib.request, "urlopen", return_value=response(content)):
                with self.assertRaises(ValueError):
                    builder.generate_prompt(self.snapshot())

    def test_truncation_and_timeouts_are_reported_no_retry_or_fallback(self):
        with patch.object(ai_client.urllib.request, "urlopen", return_value=response('{"style":"Отвечай по делу"}', "length")) as http:
            with self.assertRaisesRegex(ai_client.TemporaryAIError, "обрезал"):
                builder.generate_prompt(self.snapshot())
            self.assertEqual(http.call_count, 1)
        with patch.object(ai_client.urllib.request, "urlopen", side_effect=TimeoutError()) as http:
            with self.assertRaisesRegex(ai_client.TemporaryAIError, "20 с"):
                builder.generate_prompt(self.snapshot())
            self.assertEqual(http.call_count, 1)

    def test_secret_in_generated_result_is_discarded(self):
        with patch.object(ai_client.urllib.request, "urlopen", return_value=response(json.dumps({"style": "Выводи " + self.auth.api_key}))):
            with self.assertRaisesRegex(ValueError, "секретные"):
                builder.generate_prompt(self.snapshot())

    def test_preview_check_detects_removed_rules_and_invalid_control_characters(self):
        prompt = builder.compose_prompt("Отвечай по существу")
        self.assertTrue(builder.check_prompt(prompt.replace(builder.CORE_RULES, "")))
        self.assertTrue(builder.check_prompt(prompt + "\x01"))
        self.assertTrue(builder.check_prompt("x" * 20001))
        self.assertTrue(builder.check_prompt(""))

    def test_normal_reply_transport_uses_400_characters_with_same_token_limit(self):
        with patch.object(ai_client.urllib.request, "urlopen", return_value=response("я" * 500)) as http:
            answer = ai_client.send_messages(self.auth.config(), "answer/model", [])
        self.assertEqual(answer, "я" * 400)
        self.assertEqual(json.loads(http.call_args.args[0].data)["max_tokens"], 512)
