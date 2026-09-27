import asyncio
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import bot
import memory
import start
import twitch_auth


class BotTests(unittest.TestCase):
    def test_command_and_irc_message(self):
        line = "@id=abc;user-id=123 :viewer!viewer@viewer.tmi.twitch.tv PRIVMSG #channel :!бот Привет?"
        self.assertEqual(bot.IRC_MESSAGE.match(line).groups(), ("viewer", "channel", "!бот Привет?"))
        self.assertEqual(bot.irc_user_id(line), "123")
        self.assertEqual(bot.extract_question("!бот Привет?"), "Привет?")
        self.assertIsNone(bot.extract_question("!ботинок"))

    def test_burst_from_multiple_viewers_and_repeat_question(self):
        async def scenario():
            answered = asyncio.Event()
            replies = []
            users = ["one", "two", "three", "four", "one"]
            messages = [
                f":{user}!{user}@{user}.tmi.twitch.tv PRIVMSG #channel :!бот Вопрос {number}\r\n".encode()
                for number, user in enumerate(users)
            ]

            class Reader:
                async def readline(self):
                    if messages:
                        return messages.pop(0)
                    await asyncio.wait_for(answered.wait(), timeout=2)
                    return b""

            class Writer:
                def write(self, data):
                    pass

                async def drain(self):
                    pass

                def close(self):
                    pass

                async def wait_closed(self):
                    pass

            async def open_connection(*args, **kwargs):
                return Reader(), Writer()

            async def say(self, writer, message):
                replies.append(message)
                if len(replies) == len(users):
                    answered.set()

            instance = bot.Bot.__new__(bot.Bot)
            instance.cfg = {
                "TWITCH_CLIENT_ID": "client", "TWITCH_BOT_NAME": "helper",
                "TWITCH_CHANNEL": "channel", "AI_MODEL": bot.AI_MODEL,
            }
            instance.memory = {"streamer": {"facts": [], "jokes": []}, "viewers": []}
            instance.ai_router = bot.AIModelRouter()
            with patch.object(bot, "get_access_token", return_value="token"), patch.object(
                bot.asyncio, "open_connection", open_connection
            ), patch.object(bot, "call_ai", return_value="OK"), patch.object(bot.Bot, "say", say):
                await instance.connection()
            self.assertEqual(len(replies), len(users))
            self.assertEqual(sum(reply.startswith("@one ") for reply in replies), 2)
            self.assertTrue(all(reply.endswith(" OK") for reply in replies))

        asyncio.run(scenario())

    def test_ai_request_and_one_line_reply(self):
        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        def fake_urlopen(request, timeout):
            self.assertEqual(timeout, 20)
            self.assertEqual(request.full_url, "https://api.deepseek.com/chat/completions")
            self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
            payload = json.loads(request.data)
            self.assertEqual(payload["model"], bot.AI_MODEL)
            self.assertIn("Ты Чунда", payload["messages"][0]["content"])
            self.assertIn("Мат — привычная часть", payload["messages"][0]["content"])
            self.assertEqual(len(payload["messages"]), 2)
            return Response(json.dumps({"choices": [{"message": {"content": "Привет!\nКак дела?"}}]}).encode())

        with patch.object(bot.urllib.request, "urlopen", fake_urlopen):
            answer = bot.call_ai({
                "AI_MODEL": bot.AI_MODEL, "AI_API_KEY": "test-key",
                "AI_CHAT_URL": "https://api.deepseek.com/chat/completions",
            }, "viewer", "Привет")
        self.assertEqual(answer, "Привет! Как дела?")

    def test_channel_owner_gets_a_distinct_tone(self):
        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        requests = []

        def fake_urlopen(request, timeout):
            requests.append(json.loads(request.data)["messages"])
            return Response(b'{"choices":[{"message":{"content":"OK"}}]}')

        cfg = {
            "TWITCH_CHANNEL": "chundon", "AI_MODEL": bot.AI_MODEL,
            "AI_API_KEY": "test-key", "AI_CHAT_URL": "https://api.example.com/chat/completions",
            "AI_PROMPT": "Пользовательский промпт",
        }
        with patch.object(bot.urllib.request, "urlopen", fake_urlopen):
            bot.call_ai(cfg, "Chundon", "Привет")
            bot.call_ai(cfg, "viewer", "Привет")

        owner, viewer = requests
        self.assertEqual(owner[0]["content"], "Пользовательский промпт")
        self.assertIn("госпожа", owner[1]["content"])
        self.assertIn("Стримерша Chundon", owner[-1]["content"])
        self.assertEqual(len(viewer), 2)
        self.assertIn("Зритель viewer", viewer[-1]["content"])

    def test_temporary_failure_uses_backup_and_cooldown_then_probes_primary(self):
        router = bot.AIModelRouter()
        cfg = {"AI_MODEL": bot.AI_MODEL}
        clock = {"now": 0.0}
        attempts = []
        primary_calls = 0

        def fake_call_ai(_cfg, _user, _question, _memory, _user_id, model):
            nonlocal primary_calls
            attempts.append(model)
            if model == bot.AI_MODEL:
                primary_calls += 1
                if primary_calls <= 3:
                    raise bot.TemporaryAIError("HTTP 503")
            return "OK"

        with patch.object(bot, "call_ai", side_effect=fake_call_ai), patch.object(
            bot.time, "monotonic", side_effect=lambda: clock["now"]
        ):
            for _ in range(3):
                self.assertEqual(router.ask(cfg, "viewer", "вопрос"), "OK")
            self.assertEqual(router.primary_failures, 3)
            self.assertEqual(router.primary_disabled_until, 300.0)
            clock["now"] = 299.0
            self.assertEqual(router.ask(cfg, "viewer", "вопрос"), "OK")
            clock["now"] = 300.0
            self.assertEqual(router.ask(cfg, "viewer", "вопрос"), "OK")

        self.assertEqual(attempts, [
            bot.AI_MODEL, "deepseek-v4-pro",
            bot.AI_MODEL, "deepseek-v4-pro",
            bot.AI_MODEL, "deepseek-v4-pro",
            "deepseek-v4-pro", bot.AI_MODEL,
        ])
        self.assertEqual(router.primary_failures, 0)

    def test_fallbacks_are_tried_in_order_until_one_answers(self):
        router = bot.AIModelRouter()
        cfg = {"AI_MODEL": bot.AI_MODEL}
        attempts = []

        def fake_call_ai(_cfg, _user, _question, _memory, _user_id, model):
            attempts.append(model)
            if model != "mimo-v2.5-pro":
                raise bot.TemporaryAIError("HTTP 503")
            return "Ответ от последней модели"

        with patch.object(bot, "call_ai", side_effect=fake_call_ai):
            self.assertEqual(router.ask(cfg, "viewer", "вопрос"), "Ответ от последней модели")

        self.assertEqual(attempts, [
            bot.AI_MODEL, "deepseek-v4-pro", "deepseek-v4-flash",
            "minimax-m3", "mimo-v2.5-pro",
        ])
        self.assertEqual(router.primary_failures, 1)

    def test_http_503_is_retryable_but_401_is_not(self):
        cfg = {
            "AI_MODEL": bot.AI_MODEL, "AI_API_KEY": "test-key",
            "AI_CHAT_URL": "https://api.example.com/chat/completions",
        }

        def http_error(status):
            return urllib.error.HTTPError(cfg["AI_CHAT_URL"], status, "error", {}, None)

        with patch.object(bot.urllib.request, "urlopen", side_effect=http_error(503)):
            with self.assertRaises(bot.TemporaryAIError):
                bot.call_ai(cfg, "viewer", "вопрос")
        with patch.object(bot.urllib.request, "urlopen", side_effect=http_error(401)):
            with self.assertRaisesRegex(RuntimeError, "HTTP 401"):
                bot.call_ai(cfg, "viewer", "вопрос")

    def test_memory_is_valid_and_only_current_viewer_is_sent(self):
        data = memory.load_memory(Path(__file__).resolve().parents[1] / "memory.example.json")
        self.assertEqual(data["viewers"], [])  # The example card is ignored.
        data["streamer"]["facts"] = ["Софи любит хорроры"]
        data["viewers"] = [
            {"login": "pelmen", "user_id": "123", "facts": ["Боится скримеров"], "jokes": [], "avoid": []},
            {"login": "other", "user_id": "456", "facts": ["Любит шахматы"], "jokes": [], "avoid": []},
        ]
        context = memory.context_for(data, "renamed_pelmen", "123")
        self.assertIn("Боится скримеров", context)
        self.assertIn("Софи любит хорроры", context)
        self.assertNotIn("Любит шахматы", context)
        self.assertNotIn("Боится скримеров", memory.context_for(data, "pelmen", "456"))
        self.assertNotIn("Боится скримеров", memory.context_for(data, "pelmen", ""))

    def test_local_memory_is_created_once_from_template(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / "memory.example.json"
            local = root / "memory.json"
            template.write_text('{"streamer":{"facts":[],"jokes":[]},"viewers":[]}', encoding="utf-8")
            memory.ensure_local_memory(local)
            self.assertEqual(local.read_bytes(), template.read_bytes())
            local.write_text("private notes", encoding="utf-8")
            memory.ensure_local_memory(local)
            self.assertEqual(local.read_text(encoding="utf-8"), "private notes")

    def test_memory_notes_are_in_ai_request(self):
        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        data = {"streamer": {"facts": [], "jokes": []}, "viewers": [
            {"login": "pelmen", "user_id": "", "facts": ["Любит хорроры"], "jokes": [], "avoid": []},
        ]}

        def fake_urlopen(request, timeout):
            messages = json.loads(request.data)["messages"]
            self.assertEqual(len(messages), 3)
            self.assertIn("Любит хорроры", messages[1]["content"])
            return Response(b'{"choices":[{"message":{"content":"OK"}}]}')

        with patch.object(bot.urllib.request, "urlopen", fake_urlopen):
            self.assertEqual(bot.call_ai({
                "AI_MODEL": bot.AI_MODEL, "AI_API_KEY": "test-key",
                "AI_CHAT_URL": "https://api.deepseek.com/chat/completions",
            }, "pelmen", "Привет", data), "OK")

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
                "https://api.example.com/v1/chat/completions",
            ])
            with patch("builtins.input", side_effect=lambda _: next(answers)), patch.object(
                start.getpass, "getpass", return_value="secret-key"
            ) as secret:
                start.setup(path)
                self.assertTrue(start.complete(start.read_config(path)))
                self.assertEqual(start.read_config(path)["TWITCH_CHANNEL"], "streamer")
                self.assertEqual(start.read_config(path)["AI_BASE_URL"], "https://api.example.com/v1")
                self.assertEqual(start.AI_MODEL, bot.AI_MODEL)
                start.setup(path)
                secret.assert_called_once()


if __name__ == "__main__":
    unittest.main()
