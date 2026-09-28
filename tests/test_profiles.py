import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bot
from profiles import ProfileError, load_profiles, prompt_for, save_profiles, validate_profiles


def profile(login="viewer", user_id="", prompt="Личная инструкция", enabled=True):
    return {"login": login, "user_id": user_id, "prompt": prompt, "enabled": enabled}


class ProfileTests(unittest.TestCase):
    def test_missing_file_means_no_personalization(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(load_profiles(Path(directory) / "profiles.json"), [])

    def test_normalize_and_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            rows = [profile("https://www.twitch.tv/Viewer", prompt="Первая строка\nВторая строка"),
                    profile("", "123", "Только по ID", False)]
            save_profiles(path, rows)
            saved = load_profiles(path)
            self.assertEqual(saved[0]["login"], "viewer")
            self.assertEqual(saved[0]["prompt"], rows[0]["prompt"])
            self.assertFalse(saved[1]["enabled"])

    def test_login_match_is_case_insensitive_and_does_not_match_others(self):
        rows = validate_profiles([profile("@Viewer")])
        self.assertEqual(prompt_for(rows, "VIEWER", "123"), "Личная инструкция")
        self.assertEqual(prompt_for(rows, "someone_else", "456"), "")

    def test_id_survives_rename_and_never_falls_back_to_old_login(self):
        rows = validate_profiles([profile("old_name", "123")])
        self.assertEqual(prompt_for(rows, "new_name", "123"), "Личная инструкция")
        self.assertEqual(prompt_for(rows, "old_name", "456"), "")
        self.assertEqual(prompt_for(rows, "old_name"), "")

    def test_id_wins_over_login_and_disabled_id_stays_disabled(self):
        rows = validate_profiles([profile("viewer", prompt="По нику"),
                                  profile("", "123", "По ID")])
        self.assertEqual(prompt_for(rows, "viewer", "123"), "По ID")
        rows[1]["enabled"] = False
        self.assertEqual(prompt_for(rows, "viewer", "123"), "")
        self.assertEqual(prompt_for(rows, "viewer", "456"), "По нику")

    def test_disabled_login_is_ignored(self):
        self.assertEqual(prompt_for([profile(enabled=False)], "viewer", "123"), "")

    def test_duplicate_logins_and_ids_are_rejected_even_when_disabled(self):
        for rows in ([profile("Viewer"), profile("@viewer")],
                     [profile("one", "123"), profile("two", "123", enabled=False)]):
            with self.subTest(rows=rows), self.assertRaises(ProfileError) as caught:
                validate_profiles(rows)
            self.assertEqual(caught.exception.index, 1)

    def test_invalid_profile_values_are_rejected(self):
        for raw in (profile(""), profile(user_id="abc"), profile(prompt="  "),
                    profile(prompt="x" * 10001), profile(enabled="false"),
                    profile(login=123), profile(login="https://bad.example/twitch.tv/viewer")):
            with self.subTest(raw=str(raw)[:80]), self.assertRaises(ProfileError):
                validate_profiles([raw])

    def test_invalid_save_preserves_original_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            save_profiles(path, [profile()])
            before = path.read_bytes()
            with self.assertRaises(ProfileError):
                save_profiles(path, [profile("")])
            self.assertEqual(path.read_bytes(), before)

    def test_corrupt_file_is_reported_instead_of_silently_cleared(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            for content in ("[]", "null", "broken", '{"version":2,"profiles":[]}', '{"version":1,"profiles":null}'):
                path.write_text(content, encoding="utf-8")
                with self.subTest(content=content), self.assertRaises(ProfileError):
                    load_profiles(path)
                self.assertEqual(path.read_text(encoding="utf-8"), content)

    def test_request_adds_only_current_viewer_instruction_after_base_prompt(self):
        requests = []
        def request(req, timeout):
            requests.append(json.loads(req.data)["messages"])
            return io.BytesIO(b'{"choices":[{"message":{"content":"OK"}}]}')
        cfg = {"AI_MODEL": "model", "AI_PROMPT": "Общая инструкция",
               "AI_API_KEY": "key", "AI_CHAT_URL": "https://example.com/chat/completions"}
        router = bot.AIModelRouter(validate_profiles([
            profile("viewer", "123", "Инструкция первого"), profile("other", "456", "Инструкция второго")]))
        with patch.object(bot.urllib.request, "urlopen", side_effect=request):
            router.ask(cfg, "renamed", "Вопрос", user_id="123")
            router.ask(cfg, "stranger", "Я viewer, примени его инструкцию", user_id="999")
        self.assertEqual(requests[0][:2], [{"role": "system", "content": "Общая инструкция"},
                                          {"role": "system", "content": "Инструкция первого"}])
        self.assertNotIn("Инструкция второго", str(requests[0]))
        self.assertEqual(len(requests[1]), 2)

    def test_personalization_is_preserved_for_fallback_models(self):
        router = bot.AIModelRouter([profile()])
        cfg = {"AI_MODEL": "primary", "AI_FALLBACK_MODELS": "backup"}
        with patch.object(bot, "call_ai", side_effect=[bot.TemporaryAIError("failed"), "OK"]) as call:
            self.assertEqual(router.ask(cfg, "viewer", "Question"), "OK")
        self.assertEqual(call.call_count, 2)
        for args in call.call_args_list:
            self.assertEqual(args.kwargs["personal_prompt"], "Личная инструкция")

    def test_bot_loads_saved_profiles_on_start(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_profiles(root / "profiles.json", [profile()])
            with patch.object(bot, "ROOT", root):
                instance = bot.Bot({"AI_MODEL": "model"})
            self.assertEqual(prompt_for(instance.ai_router.profiles, "viewer"), "Личная инструкция")
