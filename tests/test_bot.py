import asyncio
import io
import json
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import bot
import memory
import start
import twitch_auth
import rewards


class BotTests(unittest.TestCase):
    def test_reward_events_only_and_history_per_viewer(self):
        async def scenario():
            instance = bot.Bot.__new__(bot.Bot)
            instance.autonomous = Mock()
            instance.cfg = {"AI_MODEL": bot.AI_MODEL, "AI_FALLBACK_MODELS": ",".join(bot.AI_FALLBACK_MODELS)}
            instance.memory = {"streamer": {"facts": [], "jokes": []}, "viewers": []}
            instance.histories = {}
            calls = []
            class Router:
                def ask(self, cfg, user, question, memory_data, user_id, history):
                    calls.append((user_id, history))
                    return "OK"
            instance.ai_router = Router()
            replies = []
            async def say(writer, message):
                replies.append(message)
            instance.say = say
            queue = asyncio.Queue()
            for index in range(12):
                queue.put_nowait(("one", "1", f"Вопрос {index}", str(index)))
            queue.put_nowait(("two", "2", "Другой вопрос", "other"))
            task = asyncio.create_task(instance.worker(None, queue))
            await asyncio.wait_for(queue.join(), timeout=3)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.assertEqual(len(instance.histories["1"]), 10)
            self.assertEqual(instance.histories["1"][0], ("Вопрос 2", "OK"))
            self.assertEqual(calls[11][1][0], ("Вопрос 1", "OK"))
            self.assertEqual(calls[12], ("2", ()))
            self.assertEqual(len(replies), 13)
            self.assertEqual(instance.autonomous.remember_reply.call_count, 13)
            instance.autonomous.remember_reply.assert_called_with("OK", target="two", source="reward", question="Другой вопрос")

        asyncio.run(scenario())

    def test_reward_deduplication_and_required_input(self):
        async def scenario():
            received = []
            async def on_question(*args):
                received.append(args)
            listener = rewards.RewardListener("client", "channel", Path("token"), on_question)
            event = {"event": {"id": "redeem-1", "reward": {"id": "reward-1", "title": "вопрос ии"},
                               "user_login": "Viewer", "user_id": "123", "user_input": "Привет?"}}
            await listener.handle_notification(event)
            await listener.handle_notification(event)
            await listener.handle_notification({"event": {**event["event"], "id": "redeem-2", "reward": {"id": "other", "title": "другая"}}})
            self.assertEqual(received, [("viewer", "123", "Привет?", "redeem-1")])
        asyncio.run(scenario())

    def test_subscription_includes_manually_created_rewards(self):
        with patch.object(rewards, "api_json", return_value={}) as api:
            rewards.subscribe("client", "token", "123", "session")
        payload = api.call_args.args[3]
        self.assertEqual(payload["condition"], {"broadcaster_user_id": "123"})

    def test_ai_receives_ten_complete_pairs_before_current_question(self):
        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        captured = []
        def fake_urlopen(request, timeout):
            captured.extend(json.loads(request.data)["messages"])
            return Response(b'{"choices":[{"message":{"content":"OK"}}]}')

        cfg = {"AI_MODEL": bot.AI_MODEL, "AI_API_KEY": "key",
               "AI_CHAT_URL": "https://api.example.com/chat/completions"}
        history = tuple((f"Вопрос {i}", f"Ответ {i}") for i in range(12))
        with patch.object(bot.urllib.request, "urlopen", fake_urlopen):
            bot.call_ai(cfg, "viewer", "Новый вопрос", history=history)
        self.assertEqual(len(captured), 22)
        self.assertEqual(captured[1]["content"], "Зритель viewer спрашивает: Вопрос 2")
        self.assertEqual(captured[-2]["content"], "Ответ 11")
        self.assertEqual(captured[-1]["content"], "Зритель viewer спрашивает: Новый вопрос")

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
            self.assertEqual(payload["messages"][0]["role"], "system")
            self.assertIn("400 символов", payload["messages"][0]["content"])
            self.assertEqual(payload["messages"][-1]["role"], "user")
            self.assertEqual(len(payload["messages"]), 2)
            return Response(json.dumps({"choices": [{"message": {"content": "Привет!\nКак дела?"}}]}).encode())

        with patch.object(bot.urllib.request, "urlopen", fake_urlopen):
            answer = bot.call_ai({
                "AI_MODEL": bot.AI_MODEL, "AI_API_KEY": "test-key",
                "AI_CHAT_URL": "https://api.deepseek.com/chat/completions",
            }, "viewer", "Привет")
        self.assertEqual(answer, "Привет! Как дела?")

    def test_channel_owner_has_no_hidden_persona(self):
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
            "TWITCH_CHANNEL": "streamer", "AI_MODEL": bot.AI_MODEL,
            "AI_API_KEY": "test-key", "AI_CHAT_URL": "https://api.example.com/chat/completions",
            "AI_PROMPT": "Пользовательский промпт",
        }
        with patch.object(bot.urllib.request, "urlopen", fake_urlopen):
            bot.call_ai(cfg, "Streamer", "Привет")
            bot.call_ai(cfg, "viewer", "Привет")

        owner, viewer = requests
        self.assertEqual(owner[0]["content"], "Пользовательский промпт")
        self.assertEqual(len(owner), 3)
        self.assertIn("Владелец канала Streamer", owner[-1]["content"])
        self.assertEqual(len(viewer), 3)
        self.assertIn("Зритель viewer", viewer[-1]["content"])

    def test_temporary_failure_uses_backup_and_cooldown_then_probes_primary(self):
        router = bot.AIModelRouter()
        cfg = {"AI_MODEL": bot.AI_MODEL, "AI_FALLBACK_MODELS": ",".join(bot.AI_FALLBACK_MODELS)}
        clock = {"now": 0.0}
        attempts = []
        primary_calls = 0

        def fake_call_ai(_cfg, _user, _question, _memory, _user_id, model, history):
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
            bot.AI_MODEL, "deepseek-v4.1-pro",
            bot.AI_MODEL, "deepseek-v4.1-pro",
            bot.AI_MODEL, "deepseek-v4.1-pro",
            "deepseek-v4.1-pro", bot.AI_MODEL,
        ])
        self.assertEqual(router.primary_failures, 0)

    def test_fallbacks_are_tried_in_order_until_one_answers(self):
        router = bot.AIModelRouter()
        cfg = {"AI_MODEL": bot.AI_MODEL, "AI_FALLBACK_MODELS": ",".join(bot.AI_FALLBACK_MODELS)}
        attempts = []

        def fake_call_ai(_cfg, _user, _question, _memory, _user_id, model, history):
            attempts.append(model)
            if model != "deepseek-v4-flash":
                raise bot.TemporaryAIError("HTTP 503")
            return "Ответ от последней модели"

        with patch.object(bot, "call_ai", side_effect=fake_call_ai):
            self.assertEqual(router.ask(cfg, "viewer", "вопрос"), "Ответ от последней модели")

        self.assertEqual(attempts, [
            bot.AI_MODEL, "deepseek-v4.1-pro", "deepseek-v4-pro", "deepseek-v4-flash",
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

    def test_http_400_reports_reason_and_tries_next_model(self):
        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        cfg = {
            "AI_MODEL": bot.AI_MODEL, "AI_FALLBACK_MODELS": ",".join(bot.AI_FALLBACK_MODELS), "AI_API_KEY": "sk-test",
            "AI_CHAT_URL": "https://api.example.com/chat/completions",
        }
        attempts = []

        def fake_urlopen(request, timeout):
            model = json.loads(request.data)["model"]
            attempts.append(model)
            if model == bot.AI_MODEL:
                body = io.BytesIO(b'{"error":{"message":"model unavailable: sk-test"}}')
                raise urllib.error.HTTPError(request.full_url, 400, "error", {}, body)
            return Response(b'{"choices":[{"message":{"content":"OK"}}]}')

        log = io.StringIO()
        with patch.object(bot.urllib.request, "urlopen", fake_urlopen), redirect_stdout(log):
            self.assertEqual(bot.AIModelRouter().ask(cfg, "viewer", "вопрос"), "OK")
        self.assertEqual(attempts, [bot.AI_MODEL, "deepseek-v4.1-pro"])
        self.assertIn("HTTP 400: model unavailable: [ключ скрыт]", log.getvalue())
        self.assertNotIn("sk-test", log.getvalue())

    def test_memory_is_valid_and_only_current_viewer_is_sent(self):
        data = memory.load_memory(Path(__file__).resolve().parents[1] / "memory.example.json")
        self.assertEqual(data["viewers"], [])  # The example card is ignored.
        data["streamer"]["facts"] = ["Стример любит хорроры"]
        data["viewers"] = [
            {"login": "pelmen", "user_id": "123", "facts": ["Боится скримеров"], "jokes": [], "avoid": []},
            {"login": "other", "user_id": "456", "facts": ["Любит шахматы"], "jokes": [], "avoid": []},
        ]
        context = memory.context_for(data, "renamed_pelmen", "123")
        self.assertIn("Боится скримеров", context)
        self.assertIn("Стример любит хорроры", context)
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
            self.assertIn("Любит хорроры", messages[0]["content"])
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

    def test_wrong_new_twitch_account_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".twitch_token.json"
            path.write_text(json.dumps({"access_token": "valid"}))
            with patch.object(twitch_auth, "validate", return_value={
                "client_id": "client", "login": "someone_else", "scopes": ["chat:read", "chat:edit"]
            }), patch.object(twitch_auth, "authorize_device", return_value={"access_token": "wrong"}):
                with self.assertRaisesRegex(RuntimeError, "не под аккаунтом bot"):
                    twitch_auth.get_access_token("client", "bot", path)

    def test_first_run_setup_and_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            answers = iter([
                "https://www.twitch.tv/Streamer", "Helper_bot", "client123",
                "Ask AI", "https://api.example.com/v1/chat/completions", "", "",
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
