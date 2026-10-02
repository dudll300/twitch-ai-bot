import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bot
import settings
import start
from configuration import env_content


class SettingsTests(unittest.TestCase):
    def test_first_run_fallbacks_can_be_saved_and_cleared(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values, prompt = settings.load_settings(root)
            self.assertEqual(values["AI_FALLBACK_MODELS"], "deepseek-v4-pro,deepseek-v4-flash")
            self.assertFalse((root / ".env").exists())
            settings.save_settings(values, prompt, root, allow_incomplete=True)
            self.assertEqual(settings.load_settings(root)[0]["AI_FALLBACK_MODELS"], values["AI_FALLBACK_MODELS"])
            values["AI_FALLBACK_MODELS"] = ""
            settings.save_settings(values, prompt, root, allow_incomplete=True)
            self.assertEqual(settings.load_settings(root)[0]["AI_FALLBACK_MODELS"], "")

    def test_existing_missing_empty_and_custom_fallback_lists_are_preserved(self):
        for saved, expected in (({}, ""), ({"AI_FALLBACK_MODELS": ""}, ""),
                                ({"AI_FALLBACK_MODELS": "custom/a,custom/b"}, "custom/a,custom/b")):
            with self.subTest(saved=saved), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / ".env").write_text(env_content(saved), encoding="utf-8")
                before = (root / ".env").read_bytes()
                self.assertEqual(settings.load_settings(root)[0]["AI_FALLBACK_MODELS"], expected)
                self.assertEqual((root / ".env").read_bytes(), before)

    def test_console_first_run_offers_same_backups_and_can_clear_them(self):
        for entered, expected in (("", "deepseek-v4-pro,deepseek-v4-flash"), ("-", "")):
            with self.subTest(entered=entered), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / ".env"
                inputs = ["streamer", "helper", "client", "", "", "", entered]
                with patch("builtins.input", side_effect=inputs), patch.object(
                        start.getpass, "getpass", return_value="test-key"):
                    start.setup(path)
                self.assertEqual(settings.read_config(path)["AI_FALLBACK_MODELS"], expected)

    def test_selected_model_prompt_and_existing_key_reach_bot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = {
                "TWITCH_CHANNEL": "https://www.twitch.tv/Streamer",
                "TWITCH_BOT_NAME": "HelperBot",
                "TWITCH_CLIENT_ID": "client123",
                "AI_BASE_URL": "https://ai.starimg.ru/v1",
                "AI_API_KEY": "sk-test",
                "AI_MODEL": "deepseek-v4.1-pro",
            }
            settings.save_settings(values, "Мой системный промпт", root)
            values["AI_API_KEY"] = ""
            settings.save_settings(values, "Мой системный промпт", root)
            loaded, prompt = settings.load_settings(root)
            self.assertEqual(loaded["TWITCH_CHANNEL"], "streamer")
            self.assertEqual(loaded["TWITCH_BOT_NAME"], "helperbot")
            self.assertEqual(loaded["AI_API_KEY"], "sk-test")
            self.assertEqual(loaded["AI_MODEL"], "deepseek-v4.1-pro")
            self.assertEqual(prompt.strip(), "Мой системный промпт")
            with patch.object(bot, "ROOT", root), patch.dict(os.environ, {}, clear=True):
                config = bot.config()
            self.assertEqual(config["AI_MODEL"], "deepseek-v4.1-pro")
            self.assertEqual(config["AI_PROMPT"], "Мой системный промпт")

    def test_custom_model_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text(
                "TWITCH_CHANNEL=streamer\nTWITCH_BOT_NAME=helperbot\n"
                "TWITCH_CLIENT_ID=client123\nAI_BASE_URL=https://ai.starimg.ru/v1\n"
                "AI_API_KEY=sk-test\nAI_MODEL=deepseek-v4-pro\n", encoding="utf-8",
            )
            values, _prompt = settings.load_settings(root)
            self.assertEqual(values["AI_MODEL"], "deepseek-v4-pro")
            with patch.object(bot, "ROOT", root), patch.dict(os.environ, {}, clear=True):
                self.assertEqual(bot.config()["AI_MODEL"], "deepseek-v4-pro")

    def test_selected_backup_is_not_tried_twice(self):
        router = bot.AIModelRouter()
        attempts = []

        def unavailable(_cfg, _user, _question, _memory, _user_id, model, history):
            attempts.append(model)
            raise bot.TemporaryAIError("HTTP 503")

        with patch.object(bot, "call_ai", side_effect=unavailable):
            with self.assertRaises(bot.TemporaryAIError):
                router.ask({"AI_MODEL": "deepseek-v4.1-pro", "AI_FALLBACK_MODELS": ",".join(bot.AI_FALLBACK_MODELS)}, "viewer", "вопрос")
        self.assertEqual(attempts, [
            "deepseek-v4.1-pro", "deepseek-v4.1-flash", "deepseek-v4-flash",
        ])


if __name__ == "__main__":
    unittest.main()
