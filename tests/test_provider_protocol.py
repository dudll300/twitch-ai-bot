"""Provider compatibility at the real HTTP boundary, without real credentials."""

import io
import json
import unittest
from unittest.mock import Mock, patch

import ai_client as ai
import bot
from privacy import PrivacyViolation


CFG = dict(AI_CHAT_URL="https://provider.invalid/v1/chat/completions", AI_API_KEY="synthetic-key")


def response(content="Готово", finish="stop", **message):
    return io.BytesIO(json.dumps({"choices": [{"finish_reason": finish,
        "message": {"content": content, **message}}]}).encode())


class ProviderProtocolTests(unittest.TestCase):
    def request(self, model, result=None, **kwargs):
        gate = Mock(return_value=3)
        trace = ai.RequestTrace()
        with patch("urllib.request.urlopen", return_value=result or response()) as http:
            text = ai.request_completion(CFG, model, [{"role": "user", "content": "Привет"}], max_tokens=321,
                                         before_request=gate, trace=trace, request_stage="generator", **kwargs)
        self.assertEqual(gate.call_count, 1)
        self.assertEqual(http.call_count, 1)
        self.assertEqual(trace.counts(), (0, 1, 0))
        self.assertEqual(http.call_args.kwargs["timeout"], 3)
        return text, json.loads(http.call_args.args[0].data)

    def test_exact_o_series_ids_use_completion_limit_without_changing_budget_or_model(self):
        for model in ("o1", "o1-mini", "o1-preview", "o1-mini-2024-09-12", "o3-mini", "o3", "o4-mini",
                      "openai/o3-mini-2025-01-31"):
            with self.subTest(model=model):
                text, payload = self.request(model)
                self.assertEqual(text, "Готово")
                self.assertEqual(payload["model"], model)
                self.assertEqual(payload["max_completion_tokens"], 321)
                self.assertNotIn("max_tokens", payload)

    def test_deepseek_and_custom_substrings_keep_existing_contract(self):
        for model in ("deepseek-r1", "deepseek-reasoner", "deepseek-v4-pro", "custom-reasoning", "vendor/o1",
                      "not-o3-mini", "o1-my-custom-model", "r1", "gpt-4o"):
            with self.subTest(model=model):
                _, payload = self.request(model)
                self.assertEqual(payload["max_tokens"], 321)
                self.assertNotIn("max_completion_tokens", payload)

    def test_only_final_content_is_returned_and_reasoning_is_never_used_as_a_fallback(self):
        text, _ = self.request("deepseek-r1", response(reasoning_content="PRIVATE_INTERNAL_THOUGHT"))
        self.assertEqual(text, "Готово")
        for content in (None, "", "   "):
            with self.subTest(content=content), patch("urllib.request.urlopen", return_value=response(
                    content, reasoning_content="PRIVATE_INTERNAL_THOUGHT")) as http:
                with self.assertRaises(ai.TemporaryAIError) as error:
                    ai.request_completion(CFG, "deepseek-r1", [])
                self.assertNotIn("PRIVATE_INTERNAL_THOUGHT", str(error.exception))
                self.assertEqual(http.call_count, 1)

    def test_empty_truncated_final_is_typed_even_for_plain_requests(self):
        for reject in (False, True):
            with self.subTest(reject=reject), patch("urllib.request.urlopen", return_value=response(
                    None, "length", reasoning_content="PRIVATE_INTERNAL_THOUGHT")):
                with self.assertRaises(ai.TruncatedAIError):
                    ai.request_completion(CFG, "o3-mini", [], reject_truncated=reject)

    def test_think_blocks_are_not_stripped_and_published(self):
        for text in ("<think>internal</think>Готово", " \n<THINK>internal", "<think mode='x'>internal</think>"):
            with self.subTest(text=text), patch("urllib.request.urlopen", return_value=response(text)):
                with self.assertRaises(ai.TemporaryAIError):
                    ai.request_completion(CFG, "deepseek-r1", [])

    def test_privacy_checks_still_precede_truncation_and_reasoning_rejection(self):
        for content in ("viewer@example.invalid", "<think>viewer@example.invalid</think>"):
            with self.subTest(content=content), patch("urllib.request.urlopen", return_value=response(content, "length")):
                with self.assertRaises(PrivacyViolation):
                    ai.request_completion(CFG, "o3-mini", [], reject_truncated=True)

    def test_existing_router_falls_back_once_using_each_models_own_limit_contract(self):
        calls = []
        outputs = iter((response(None, "length", reasoning_content="PRIVATE_INTERNAL_THOUGHT"), response("Запасной ответ")))
        def http(request, **kwargs):
            calls.append(json.loads(request.data))
            return next(outputs)
        cfg = dict(CFG, AI_MODEL="o3-mini", AI_FALLBACK_MODELS="deepseek-r1")
        with patch("urllib.request.urlopen", side_effect=http):
            answer = bot.AIModelRouter().ask(cfg, "viewer", "Привет")
        self.assertEqual(answer, "Запасной ответ")
        self.assertEqual(len(calls), 2)
        self.assertIn("max_completion_tokens", calls[0])
        self.assertIn("max_tokens", calls[1])

    def test_invalid_response_shapes_fail_without_reasoning_or_raw_provider_data(self):
        for message in ([], "PRIVATE_PROVIDER_DATA", {"content": {"text": "PRIVATE_PROVIDER_DATA"}}):
            payload = {"choices": [{"message": message}]}
            with self.subTest(message=message), patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(payload).encode())):
                with self.assertRaises(ai.TemporaryAIError) as error:
                    ai.request_completion(CFG, "model", [])
                self.assertNotIn("PRIVATE_PROVIDER_DATA", str(error.exception))
