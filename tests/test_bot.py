import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bot
import start
import twitch_auth


class BotTests(unittest.TestCase):
    def test_command_and_irc_message(self):
        line = "@id=abc :viewer!viewer@viewer.tmi.twitch.tv PRIVMSG #channel :!бот Привет?"
        self.assertEqual(bot.IRC_MESSAGE.match(line).groups(), ("viewer", "channel", "!бот Привет?"))
        self.assertEqual(bot.extract_question("!бот Привет?"), "Привет?")
        self.assertIsNone(bot.extract_question("!ботинок"))

    def test_ai_request_and_one_line_reply(self):
        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        def fake_urlopen(request, timeout):
            self.assertEqual(request.full_url, "https://api.deepseek.com/chat/completions")
            self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
            self.assertEqual(json.loads(request.data)["model"], "deepseek-flash")
            return Response(json.dumps({"choices": [{"message": {"content": "Привет!\nКак дела?"}}]}).encode())

        with patch.object(bot.urllib.request, "urlopen", fake_urlopen):
            answer = bot.call_ai({
                "AI_MODEL": "deepseek-flash", "AI_API_KEY": "test-key",
                "AI_CHAT_URL": "https://api.deepseek.com/chat/completions",
            }, "viewer", "Привет")
        self.assertEqual(answer, "Привет! Как дела?")

    def test_expired_twitch_token_is_refreshed_and_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".twitch_token.json"
            path.write_text(json.dumps({"access_token": "expired", "refresh_token": "refresh"}))

            def fake_validate(token):
                if token == "fresh":
                    return {"client_id": "client", "login": "bot", "scopes": ["chat:read", "chat:edit"]}
                return None

            with patch.object(twitch_auth, "validate", fake_validate), patch.object(
                twitch_auth, "post_form", return_value={"access_token": "fresh", "refresh_token": "next"}
            ) as post:
                self.assertEqual(twitch_auth.get_access_token("client", "bot", path), "fresh")
            self.assertEqual(post.call_args.args[0], "/token")
            self.assertEqual(json.loads(path.read_text())["refresh_token"], "next")

    def test_wrong_twitch_account_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".twitch_token.json"
            path.write_text(json.dumps({"access_token": "valid"}))
            with patch.object(twitch_auth, "validate", return_value={
                "client_id": "client", "login": "someone_else", "scopes": ["chat:read", "chat:edit"]
            }):
                with self.assertRaisesRegex(RuntimeError, "не под аккаунтом бота"):
                    twitch_auth.get_access_token("client", "bot", path)

    def test_first_run_setup_and_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            answers = iter([
                "https://www.twitch.tv/Streamer", "Helper_bot", "client123",
                "https://api.example.com/v1/chat/completions", "model-x",
            ])
            with patch("builtins.input", side_effect=lambda _: next(answers)), patch.object(
                start.getpass, "getpass", return_value="secret-key"
            ) as secret:
                start.setup(path)
                self.assertTrue(start.complete(start.read_config(path)))
                self.assertEqual(start.read_config(path)["TWITCH_CHANNEL"], "streamer")
                self.assertEqual(start.read_config(path)["AI_BASE_URL"], "https://api.example.com/v1")
                start.setup(path)
                secret.assert_called_once()


if __name__ == "__main__":
    unittest.main()
