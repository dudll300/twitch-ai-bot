import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bot
import settings


class SettingsTests(unittest.TestCase):
    def test_custom_model_prompt_and_existing_key_reach_bot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = {
                "TWITCH_CHANNEL": "https://www.twitch.tv/Sophie",
                "TWITCH_BOT_NAME": "ChundaBot",
                "TWITCH_CLIENT_ID": "client123",
                "AI_BASE_URL": "https://ai.starimg.ru/v1",
                "AI_API_KEY": "sk-test",
                "AI_MODEL": "another-model-from-site",
            }
            settings.save_settings(values, "Новый характер Чунды", root)
            values["AI_API_KEY"] = ""
            settings.save_settings(values, "Новый характер Чунды", root)
            loaded, prompt = settings.load_settings(root)
            self.assertEqual(loaded["TWITCH_CHANNEL"], "sophie")
            self.assertEqual(loaded["TWITCH_BOT_NAME"], "chundabot")
            self.assertEqual(loaded["AI_API_KEY"], "sk-test")
            self.assertEqual(loaded["AI_MODEL"], "another-model-from-site")
            self.assertEqual(prompt.strip(), "Новый характер Чунды")
            with patch.object(bot, "ROOT", root), patch.dict(os.environ, {}, clear=True):
                config = bot.config()
            self.assertEqual(config["AI_MODEL"], "another-model-from-site")
            self.assertEqual(config["AI_PROMPT"], "Новый характер Чунды")

    def test_selected_backup_is_not_tried_twice(self):
        router = bot.AIModelRouter()
        attempts = []

        def unavailable(_cfg, _user, _question, _memory, _user_id, model):
            attempts.append(model)
            raise bot.TemporaryAIError("HTTP 503")

        with patch.object(bot, "call_ai", side_effect=unavailable):
            with self.assertRaises(bot.TemporaryAIError):
                router.ask({"AI_MODEL": "deepseek-v4-pro"}, "viewer", "вопрос")
        self.assertEqual(attempts, [
            "deepseek-v4-pro", "deepseek-v4.1-flash", "deepseek-v4-flash",
            "minimax-m3", "mimo-v2.5-pro",
        ])


if __name__ == "__main__":
    unittest.main()
