"""User-facing validation must identify settings without leaking environment keys."""

import unittest

from configuration import normalize


class ConfigurationTests(unittest.TestCase):
    def test_missing_required_fields_explain_what_to_enter(self):
        for name, expected in (
            ("TWITCH_CHANNEL", "ваш канал Twitch"),
            ("TWITCH_BOT_NAME", "Twitch-аккаунта бота"),
            ("TWITCH_CLIENT_ID", "Client ID"),
            ("TWITCH_REWARD_TITLE", "название награды"),
            ("AI_BASE_URL", "адрес AI API"),
            ("AI_API_KEY", "API-ключ"),
            ("AI_MODEL", "основную модель"),
        ):
            with self.subTest(field=name), self.assertRaises(ValueError) as error:
                normalize(name, "  ")
            self.assertIn(expected, str(error.exception))
            self.assertNotIn(name, str(error.exception))
        self.assertEqual(normalize("AI_FALLBACK_MODELS", ""), "")

    def test_control_characters_explain_the_field_without_echoing_its_value(self):
        for name, caption in (
            ("TWITCH_CHANNEL", "Канал Twitch"),
            ("TWITCH_BOT_NAME", "Аккаунт бота"),
            ("TWITCH_CLIENT_ID", "Client ID приложения Twitch"),
            ("TWITCH_REWARD_TITLE", "Название награды"),
            ("AI_BASE_URL", "Адрес AI API"),
            ("AI_API_KEY", "API-ключ"),
            ("AI_MODEL", "Основная модель"),
            ("AI_FALLBACK_MODELS", "Запасные модели"),
        ):
            with self.subTest(field=name), self.assertRaises(ValueError) as error:
                normalize(name, "private-value\x00suffix")
            message = str(error.exception)
            self.assertIn(caption, message)
            self.assertIn("Удалите", message)
            self.assertNotIn(name, message)
            self.assertNotIn("private-value", message)

    def test_login_error_explains_alphabet_and_length(self):
        for value in ("имя", "two words", "a" * 26):
            with self.subTest(value=value), self.assertRaises(ValueError) as error:
                normalize("TWITCH_CHANNEL", value)
            self.assertIn("от 1 до 25 латинских букв", str(error.exception))
        self.assertEqual(normalize("TWITCH_CHANNEL", "https://www.twitch.tv/Streamer"), "streamer")
        self.assertEqual(normalize("TWITCH_BOT_NAME", "@Helper_Bot"), "helper_bot")

    def test_url_validation_remains_strict_and_names_the_visible_field(self):
        for value in ("http://example.com/v1", "https://user:password@example.com/v1",
                      "https://example.com/v1?key=private", "https://example.com/a b"):
            with self.subTest(value=value), self.assertRaises(ValueError) as error:
                normalize("AI_BASE_URL", value)
            self.assertIn("Адрес AI API (Base URL)", str(error.exception))
            self.assertNotIn(value, str(error.exception))
        self.assertEqual(normalize("AI_BASE_URL", "https://example.com/v1/chat/completions"),
                         "https://example.com/v1")


if __name__ == "__main__":
    unittest.main()
