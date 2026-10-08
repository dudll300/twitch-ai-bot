import asyncio
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ai_client
import bot
import prompt_builder
import reply_rules as rules
import settings
import testing


def legacy_prompt():
    return prompt_builder.compose_prompt(
        "Мой характер. Иногда упоминай 300 попыток.", "Настольные игры"
    ).replace("максимум 400 символов,", "максимум 300 символов,")


class ReplyRulesTests(unittest.TestCase):
    def test_exact_old_block_upgrades_without_changing_style_topics_or_line_endings(self):
        for newline in ("\n", "\r\n"):
            previous = legacy_prompt().replace("\n", newline) + newline * 2
            expected = prompt_builder.compose_prompt(
                "Мой характер. Иногда упоминай 300 попыток.", "Настольные игры"
            ).replace("\n", newline) + newline * 2
            with self.subTest(newline=repr(newline)):
                self.assertEqual(rules.upgrade_generated_prompt(previous), expected)
                self.assertEqual(rules.upgrade_generated_prompt(expected), expected)
                # The GUI text editor normalizes CRLF before its validation.
                self.assertEqual(prompt_builder.check_prompt(expected.replace("\r\n", "\n")), ())

    def test_custom_limits_modified_policy_and_unrecognized_blocks_are_preserved(self):
        old = legacy_prompt()
        for prompt in (
            "Отвечай максимум 300 символов.",
            rules.LEGACY_CORE_RULES,
            old.replace("Не выдумывай факты", "Не фантазируй о фактах"),
            old.replace("максимум 300 символов,", "максимум 250 символов,"),
            old.replace('"Настольные игры"', 'null'),
            old + "\nДополнительное правило пользователя.",
        ):
            with self.subTest(prompt=prompt[-80:]):
                self.assertEqual(rules.upgrade_generated_prompt(prompt), prompt)

    def test_bot_and_comparison_use_same_migrated_draft_and_400_limit(self):
        previous = legacy_prompt()
        cfg = {"AI_PROMPT": previous}
        live = ai_client.build_messages(cfg, "viewer", "Вопрос?", sender_role="viewer")
        snapshot = testing.make_snapshot(
            testing.credentials("https://example.com/v1", "test-key"),
            ["model"], "Вопрос?", previous, "viewer", login="viewer",
        )
        self.assertEqual(snapshot.messages, tuple((m["role"], m["content"]) for m in live))
        self.assertEqual(live[0]["content"], rules.upgrade_generated_prompt(previous))
        self.assertIn({"role": "system", "content": rules.ANSWER_LENGTH_RULE}, live)
        self.assertIn("ответ — 400", snapshot.context)
        self.assertEqual(cfg["AI_PROMPT"], previous)

    def test_prompt_improvement_does_not_send_conflicting_old_service_limit(self):
        current = legacy_prompt()
        generation = prompt_builder.prepare_generation(
            testing.credentials("https://example.com/v1", "test-key"),
            "model", "", "", "Настольные игры", current, operation="improve",
        )
        data = json.loads(generation.messages[-1][1])
        self.assertEqual(data["current_prompt"], rules.upgrade_generated_prompt(current))
        self.assertIn("максимум 400 символов", generation.messages[0][1])
        self.assertNotIn("максимум 300 символов", str(generation.messages))

    def test_reading_settings_never_writes_and_explicit_save_migrates_known_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "prompt.txt"
            path.write_text(legacy_prompt() + "\n", encoding="utf-8")
            previous = path.read_bytes()
            values, prompt = settings.load_settings(root)
            ai_client.build_messages({"AI_PROMPT": prompt}, "viewer", "Вопрос?")
            self.assertEqual(path.read_bytes(), previous)
            self.assertFalse((root / ".env").exists())
            settings.save_settings(values, prompt, root, allow_incomplete=True)
            self.assertEqual(path.read_text(encoding="utf-8").strip(), rules.upgrade_generated_prompt(prompt).strip())
            settings.save_settings(values, "Мой предел — 300 символов", root, allow_incomplete=True)
            self.assertEqual(path.read_text(encoding="utf-8").strip(), "Мой предел — 300 символов")

    def test_long_unicode_reply_survives_25_character_login_without_second_truncation(self):
        async def scenario():
            raw = "Я" * 500
            response = io.BytesIO(json.dumps({"choices": [{"message": {"content": raw}}]}).encode())
            with patch.object(ai_client.urllib.request, "urlopen", return_value=response):
                answer = ai_client.call_ai(
                    {"AI_API_KEY": "test-key", "AI_CHAT_URL": "https://example.com/v1/chat/completions", "AI_MODEL": "model"},
                    "a" * 25, "Вопрос?",
                )
            self.assertEqual(answer, "Я" * 400)
            instance = bot.Bot.__new__(bot.Bot)
            instance.cfg = {"TWITCH_CHANNEL": "channel"}
            instance._say_lock = asyncio.Lock()
            instance.last_sent = 0
            sent = []
            class Writer:
                def write(self, data):
                    sent.append(data.decode().rstrip('\r\n'))
                async def drain(self):
                    pass
            from safety import SafetyReview
            final = "@" + "a" * 25 + " " + answer
            await instance.say(Writer(), final, approval=SafetyReview("allowed", final))
            self.assertEqual(sent, ["PRIVMSG #channel :@" + "a" * 25 + " " + "Я" * 400])
            self.assertEqual(len(sent[0].partition(" :")[2]), 427)
        asyncio.run(scenario())

    def test_short_reply_is_not_padded_and_secret_output_is_rejected_whole(self):
        cfg = {"AI_API_KEY": "secret-test-key", "AI_CHAT_URL": "https://example.com/v1/chat/completions"}
        for raw, expected in (("Кратко.\nПо делу.", "Кратко. По делу."), ("x" * 700, "x" * 400)):
            response = io.BytesIO(json.dumps({"choices": [{"message": {"content": raw}}]}).encode())
            with patch.object(ai_client.urllib.request, "urlopen", return_value=response):
                answer = ai_client.send_messages(cfg, "model", [])
            self.assertEqual(answer, expected)
            self.assertNotIn(cfg["AI_API_KEY"], answer)
        response = io.BytesIO(json.dumps({"choices": [{"message": {"content": "x" * 500 + cfg["AI_API_KEY"]}}]}).encode())
        with patch.object(ai_client.urllib.request, "urlopen", return_value=response), self.assertRaises(ai_client.PrivacyViolation):
            ai_client.send_messages(cfg, "model", [])


if __name__ == "__main__":
    unittest.main()
