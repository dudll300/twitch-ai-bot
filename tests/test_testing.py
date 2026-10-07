import io
import json
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

import ai_client
import testing
from profiles import prompt_for, validate_profiles


def response(payload):
    return io.BytesIO(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def profile(login="viewer", user_id="", enabled=True):
    return {"login": login, "user_id": user_id, "prompt": "Личная инструкция", "enabled": enabled}


def card(login="viewer", user_id="", fact="Карточка по логину"):
    return {"login": login, "user_id": user_id, "facts": [fact], "jokes": [], "avoid": []}


class TestingTests(unittest.TestCase):
    def setUp(self):
        self.auth = testing.credentials("https://ai.starimg.ru/v1", "secret-test-key")
        self.memory = {"streamer": {"facts": ["Заметка о канале"], "jokes": []},
                       "viewers": [card(), card("old_login", "123", "Карточка по ID")]}

    def snapshot(self, **kwargs):
        values = dict(auth=self.auth, models=["exact/model"], question="Вопрос?",
                      prompt="Текущий общий промпт", sender="viewer", memory_data=self.memory)
        return testing.make_snapshot(**{**values, **kwargs})

    def test_only_ai_credentials_required_and_entered_key_overrides_saved(self):
        self.assertEqual(testing.credentials("https://ai.starimg.ru/v1/", "new", "old").api_key, "new")
        self.assertEqual(testing.credentials("https://ai.starimg.ru/v1", "", "old").api_key, "old")
        self.assertNotIn(self.auth.api_key, repr(self.auth))
        with patch.object(ai_client.urllib.request, "urlopen", return_value=response(
                {"choices": [{"message": {"content": "Ответ"}}]})):
            result = testing.test_model(self.snapshot(), "exact/model")
        self.assertEqual(result.answer, "Ответ")
        self.assertEqual(result.error, "")

    def test_owner_role_without_channel_or_login_and_explicit_viewer(self):
        owner = self.snapshot(sender="owner", login="")
        self.assertIn("Владелец канала", owner.messages[-1][1])
        viewer = ai_client.build_messages({"TWITCH_CHANNEL": "viewer"}, "viewer", "Q", sender_role="viewer")
        self.assertIn("Зритель viewer", viewer[-1]["content"])
        self.assertIn("Зритель test_viewer", self.snapshot().messages[-1][1])

    def test_unsaved_prompt_and_profile_are_immutable_and_share_bot_logic(self):
        draft = profile("@VIEWER", "123")
        draft["prompt"] = "Новая несохранённая инструкция"
        snapshot = self.snapshot(sender="profile", profile=draft)
        draft["prompt"] = "Изменение после нажатия"
        self.memory["streamer"]["facts"][0] = "После нажатия"
        self.assertEqual(snapshot.messages[0][1], "Текущий общий промпт")
        self.assertEqual(snapshot.messages[1][1], "Новая несохранённая инструкция")
        self.assertNotIn("После нажатия", str(snapshot.messages))
        self.assertIn("Новая несохранённая инструкция", snapshot.context)
        with self.assertRaises(FrozenInstanceError):
            snapshot.models = ("different",)
        rows = validate_profiles([profile("viewer", "123")])
        original = self.snapshot(sender="profile", profile=rows[0])
        live_messages = ai_client.build_messages({"AI_PROMPT": "Текущий общий промпт"}, "viewer", "Вопрос?",
            self.memory, "123", personal_prompt=prompt_for(rows, "viewer", "123"), sender_role="viewer")
        self.assertEqual(original.messages, tuple((m["role"], m["content"]) for m in live_messages))

    def test_profile_and_memory_match_id_before_login_even_after_rename(self):
        snapshot = self.snapshot(sender="profile", profile=profile("viewer", "123"))
        memory_context = snapshot.messages[2][1]
        self.assertIn("Карточка по ID", memory_context)
        self.assertNotIn("Карточка по логину", memory_context)
        self.assertIn("Карточка по ID", snapshot.context)
        self.assertIn("Заметка о канале", snapshot.context)
        id_only = self.snapshot(sender="profile", profile=profile("", "123"))
        self.assertIn("Карточка по ID", str(id_only.messages))

    def test_login_profile_and_probe_login_use_same_memory_matching(self):
        snapshot = self.snapshot(sender="profile", profile=profile("@VIEWER"))
        self.assertIn("Карточка по логину", str(snapshot.messages))
        self.assertNotIn("Карточка по ID", str(snapshot.messages))
        ordinary = self.snapshot(login="@VIEWER")
        self.assertIn("Карточка по логину", str(ordinary.messages))
        self.assertNotIn("Личная инструкция", str(ordinary.messages))

    def test_disabled_profile_omits_instruction_but_keeps_memory(self):
        snapshot = self.snapshot(sender="profile", profile=profile("viewer", "123", False))
        self.assertNotIn("Личная инструкция", str(snapshot.messages))
        self.assertIn("Карточка по ID", str(snapshot.messages))
        self.assertIn("Личная инструкция: не применяется", snapshot.context)

    def test_empty_draft_instruction_does_not_require_saving_a_profile(self):
        for enabled in (True, False):
            draft = {**profile("viewer", "123", enabled), "prompt": ""}
            snapshot = self.snapshot(sender="profile", profile=draft)
            self.assertIn("Карточка по ID", str(snapshot.messages))
            self.assertIn("Личная инструкция: не применяется", snapshot.context)
            with self.assertRaises(ValueError):
                validate_profiles([draft])

    def test_no_real_history_and_bot_limits(self):
        snapshot = self.snapshot(prompt="", memory_data=None, question="x" * 450)
        self.assertEqual(len(snapshot.messages), 3)
        self.assertIn("не учитывалась", snapshot.context)
        self.assertIn("ответ — 400", snapshot.context)
        self.assertTrue(snapshot.messages[-1][1].endswith("x" * 400))
        with patch.object(ai_client.urllib.request, "urlopen", return_value=response(
                {"choices": [{"message": {"content": " x\n" * 400}}]})):
            result = testing.test_model(snapshot, "exact/model")
        self.assertGreater(len(result.answer), 300)
        self.assertLessEqual(len(result.answer), 400)
        self.assertNotIn("\n", result.answer)

    def test_multiple_models_exact_ids_same_snapshot_one_error_does_not_hide_other(self):
        snapshot = self.snapshot(models=["provider/a", "provider/b", "provider/a"])
        payloads = []
        def request(req, timeout):
            self.assertEqual(timeout, 20)
            payload = json.loads(req.data)
            payloads.append(payload)
            if payload["model"] == "provider/a":
                raise urllib.error.HTTPError(req.full_url, 503, "error", {}, response({
                    "error": {"message": "Ошибка " + self.auth.api_key}}))
            return response({"choices": [{"message": {"content": "Успешный ответ"}}]})
        output = io.StringIO()
        with patch.object(ai_client.urllib.request, "urlopen", side_effect=request), redirect_stdout(output):
            results = [testing.test_model(snapshot, model) for model in snapshot.models]
        self.assertEqual([p["model"] for p in payloads], ["provider/a", "provider/b"])
        self.assertEqual(payloads[0]["messages"], payloads[1]["messages"])
        self.assertIn("HTTP 503", results[0].error)
        self.assertNotIn(self.auth.api_key, str(results))
        self.assertEqual(results[1].answer, "Успешный ответ")
        self.assertTrue(all(r.seconds >= 0 for r in results))
        self.assertEqual(output.getvalue(), "")

    def test_catalog_all_ids_search_source_not_configured_models_and_access_flags(self):
        payload = {"data": [{"id": "z/custom"}, {"id": "a/model", "available": True},
                            {"id": "denied", "available": False},
                            {"id": "permission-denied", "permission": [{"allow_view": False}]}]}
        with patch.object(testing.urllib.request, "urlopen", return_value=response(payload)) as request:
            models = testing.fetch_models(self.auth)
        req = request.call_args.args[0]
        self.assertEqual(req.full_url, "https://ai.starimg.ru/v1/models")
        self.assertEqual(req.get_method(), "GET")
        self.assertEqual(req.get_header("Authorization"), "Bearer " + self.auth.api_key)
        self.assertEqual(request.call_args.kwargs["timeout"], 20)
        self.assertEqual(len(models), 4)
        self.assertEqual({m.id for m in models}, {row["id"] for row in payload["data"]})
        self.assertFalse(next(m for m in models if m.id == "denied").available)
        self.assertFalse(next(m for m in models if m.id == "permission-denied").available)

    def test_catalog_http_and_timeout_errors_redact_key_and_bad_schema_is_visible(self):
        failures = [urllib.error.HTTPError("https://example/models", 401, "err", {}, response(
                        {"error": {"message": "Ключ " + self.auth.api_key}})), TimeoutError()]
        for failure in failures:
            with patch.object(testing.urllib.request, "urlopen", side_effect=failure):
                with self.assertRaises(RuntimeError) as raised:
                    testing.fetch_models(self.auth)
                self.assertNotIn(self.auth.api_key, str(raised.exception))
        with patch.object(testing.urllib.request, "urlopen", return_value=response({"html": "oops"})):
            with self.assertRaisesRegex(ValueError, "data"):
                testing.fetch_models(self.auth)

    def test_timeout_and_malformed_ai_responses_are_per_model_errors(self):
        for failure in (TimeoutError(), urllib.error.URLError("down")):
            with patch.object(ai_client.urllib.request, "urlopen", side_effect=failure):
                self.assertTrue(testing.test_model(self.snapshot(), "exact/model").error)
        with patch.object(ai_client.urllib.request, "urlopen", return_value=response({"choices": []})):
            self.assertIn("некорректный ответ", testing.test_model(self.snapshot(), "exact/model").error)

    def test_echoed_key_redacted_before_response_truncation_and_context_preview(self):
        with patch.object(ai_client.urllib.request, "urlopen", return_value=response(
                {"choices": [{"message": {"content": "x" * 392 + self.auth.api_key}}]})):
            result = testing.test_model(self.snapshot(), "exact/model")
        self.assertNotIn(self.auth.api_key[:8], result.answer)
        snapshot = self.snapshot(prompt=self.auth.api_key)
        self.assertNotIn(self.auth.api_key, snapshot.context)

    def test_testing_never_creates_memory_and_reads_existing_cards(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = {"streamer": {"facts": [], "jokes": []}, "viewers": []}
            (root / "memory.example.json").write_text(json.dumps(template), encoding="utf-8")
            self.assertEqual(testing.read_test_memory(root), template)
            self.assertFalse((root / "memory.json").exists())
            (root / "memory.json").write_text(json.dumps(self.memory), encoding="utf-8")
            before = (root / "memory.json").read_bytes()
            self.snapshot(memory_data=testing.read_test_memory(root))
            self.assertEqual((root / "memory.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
