import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bot
import settings


class SettingsTests(unittest.TestCase):
    def test_selected_model_prompt_and_existing_key_reach_bot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = {
                "TWITCH_CHANNEL": "https://www.twitch.tv/Sophie",
                "TWITCH_BOT_NAME": "ChundaBot",
                "TWITCH_CLIENT_ID": "client123",
                "AI_BASE_URL": "https://ai.starimg.ru/v1",
                "AI_API_KEY": "sk-test",
                "AI_MODEL": "deepseek-v4.1-pro",
            }
            settings.save_settings(values, "Новый характер Чунды", root)
            values["AI_API_KEY"] = ""
            settings.save_settings(values, "Новый характер Чунды", root)
            loaded, prompt = settings.load_settings(root)
            self.assertEqual(loaded["TWITCH_CHANNEL"], "sophie")
            self.assertEqual(loaded["TWITCH_BOT_NAME"], "chundabot")
            self.assertEqual(loaded["AI_API_KEY"], "sk-test")
            self.assertEqual(loaded["AI_MODEL"], "deepseek-v4.1-pro")
            self.assertEqual(prompt.strip(), "Новый характер Чунды")
            with patch.object(bot, "ROOT", root), patch.dict(os.environ, {}, clear=True):
                config = bot.config()
            self.assertEqual(config["AI_MODEL"], "deepseek-v4.1-pro")
            self.assertEqual(config["AI_PROMPT"], "Новый характер Чунды")

    def test_previous_model_is_replaced_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text(
                "TWITCH_CHANNEL=sophie\nTWITCH_BOT_NAME=chundabot\n"
                "TWITCH_CLIENT_ID=client123\nAI_BASE_URL=https://ai.starimg.ru/v1\n"
                "AI_API_KEY=sk-test\nAI_MODEL=deepseek-v4-pro\n", encoding="utf-8",
            )
            values, _prompt = settings.load_settings(root)
            self.assertEqual(values["AI_MODEL"], bot.AI_MODEL)
            with patch.object(bot, "ROOT", root), patch.dict(os.environ, {}, clear=True):
                self.assertEqual(bot.config()["AI_MODEL"], bot.AI_MODEL)

    def test_selected_backup_is_not_tried_twice(self):
        router = bot.AIModelRouter()
        attempts = []

        def unavailable(_cfg, _user, _question, _memory, _user_id, model):
            attempts.append(model)
            raise bot.TemporaryAIError("HTTP 503")

        with patch.object(bot, "call_ai", side_effect=unavailable):
            with self.assertRaises(bot.TemporaryAIError):
                router.ask({"AI_MODEL": "deepseek-v4.1-pro"}, "viewer", "вопрос")
        self.assertEqual(attempts, [
            "deepseek-v4.1-pro", "deepseek-v4.1-flash", "deepseek-v4-flash",
        ])


if __name__ == "__main__":
    unittest.main()
