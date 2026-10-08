from safety_fakes import stub_reviews
import asyncio
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import bot
import configuration
import rewards
import settings
import twitch_auth


class RegressionTests(unittest.TestCase):
    def setUp(self):
        stub_reviews(self)

    def values(self):
        return {**configuration.DEFAULTS, "TWITCH_CHANNEL": "streamer",
                "TWITCH_BOT_NAME": "helper", "TWITCH_CLIENT_ID": "client",
                "AI_API_KEY": "secret", "AI_MODEL": "custom/model"}

    def test_empty_prompt_survives_save_reload_and_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(settings.load_settings(root)[1], "")
            settings.save_settings(self.values(), "", root)
            self.assertEqual(settings.load_settings(root)[1].strip(), "")
            with patch.object(bot, "ROOT", root), patch.dict(os.environ, {}, clear=True):
                self.assertEqual(bot.config()["AI_PROMPT"], "")

    def test_setting_quotes_and_backslashes_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = self.values()
            values["TWITCH_REWARD_TITLE"] = 'Ask "AI"'
            values["AI_API_KEY"] = "key=abc'\\xyz\""
            settings.save_settings(values, "", root)
            loaded, _ = settings.load_settings(root)
            self.assertEqual(loaded["TWITCH_REWARD_TITLE"], values["TWITCH_REWARD_TITLE"])
            self.assertEqual(loaded["AI_API_KEY"], values["AI_API_KEY"])

    def test_saved_settings_override_inherited_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings.save_settings(self.values(), "", root)
            with patch.object(bot, "ROOT", root), patch.dict(os.environ, {"AI_MODEL": "old"}):
                self.assertEqual(bot.config()["AI_MODEL"], "custom/model")

    def test_account_and_client_changes_invalidate_correct_tokens(self):
        for field, removed in (("TWITCH_CHANNEL", {"broadcaster"}),
                               ("TWITCH_BOT_NAME", {"bot"}),
                               ("TWITCH_CLIENT_ID", {"bot", "broadcaster"}),
                               ("AI_MODEL", set())):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                values = self.values()
                settings.save_settings(values, "", root)
                paths = {"bot": root / ".twitch_token.json",
                         "broadcaster": root / ".twitch_broadcaster_token.json"}
                for path in paths.values():
                    path.write_text("{}", encoding="utf-8")
                values[field] = "changed"
                settings.save_settings(values, "", root)
                for account, path in paths.items():
                    self.assertEqual(path.exists(), account not in removed)

    def test_invalid_values_do_not_change_saved_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = self.values()
            settings.save_settings(values, "keep", root)
            before = (root / ".env").read_bytes()
            for field in ("AI_MODEL", "AI_API_KEY", "TWITCH_REWARD_TITLE", "AI_BASE_URL"):
                with self.subTest(field=field), self.assertRaises(ValueError):
                    settings.save_settings({**values, field: "abc\nINJECTED=value"}, "", root)
                self.assertEqual((root / ".env").read_bytes(), before)
            self.assertEqual((root / "prompt.txt").read_text().strip(), "keep")

    def test_custom_model_has_no_implicit_fallback_or_dead_cooldown(self):
        router = bot.AIModelRouter()
        with patch.object(bot, "call_ai", side_effect=bot.TemporaryAIError("unavailable")) as call:
            for _ in range(4):
                with self.assertRaises(bot.TemporaryAIError):
                    router.ask(self.values(), "viewer", "question")
        self.assertEqual(call.call_count, 4)
        self.assertTrue(all(args.kwargs["model"] == "custom/model" for args in call.call_args_list))
        self.assertEqual(router.primary_disabled_until, 0)

    def test_answer_that_cleans_to_empty_is_rejected(self):
        response = io.BytesIO(json.dumps({"choices": [{"message": {"content": "\u0000"}}]}).encode())
        cfg = {**self.values(), "AI_CHAT_URL": "https://example.com/chat/completions"}
        with patch.object(bot.urllib.request, "urlopen", return_value=response):
            from safety import SafetyBlocked
            with self.assertRaises(SafetyBlocked):
                bot.call_ai(cfg, "viewer", "question")

    def test_custom_reward_and_deduplication(self):
        async def scenario():
            callback = AsyncMock()
            listener = rewards.RewardListener("client", "streamer", Path("unused"), callback,
                                              reward_title="My reward")
            event = {"event": {"id": "id", "reward": {"title": " MY REWARD "},
                               "user_login": "viewer", "user_id": "123", "user_input": "question"}}
            await listener.handle_notification(event)
            await listener.handle_notification(event)
            callback.assert_awaited_once_with("viewer", "123", "question", "id")
        asyncio.run(scenario())

    def test_chat_text_cannot_be_mistaken_for_server_command(self):
        self.assertEqual(bot.irc_command(":server RECONNECT"), "RECONNECT")
        self.assertEqual(bot.irc_command("PING :server"), "PING")
        self.assertEqual(bot.irc_command("@id=1 :viewer!viewer PRIVMSG #channel : RECONNECT Login authentication failed"), "PRIVMSG")

    def test_malformed_token_cache_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "token.json"
            for content in ("[]", "null", '{"access_token": 123}', "broken"):
                path.write_text(content, encoding="utf-8")
                self.assertEqual(twitch_auth.read_tokens(path), {})

    def test_cached_wrong_account_triggers_new_authorization(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "token.json"
            path.write_text('{"access_token":"old","refresh_token":"old-refresh"}', encoding="utf-8")
            def validate(token):
                return {"client_id": "client", "login": "helper" if token == "new" else "wrong",
                        "scopes": list(twitch_auth.SCOPES)}
            with patch.object(twitch_auth, "validate", side_effect=validate), patch.object(
                twitch_auth, "authorize_device", return_value={"access_token": "new"}
            ) as authorize, patch.object(twitch_auth, "post_form") as refresh:
                self.assertEqual(twitch_auth.get_access_token("client", "helper", path), "new")
                authorize.assert_called_once()
                refresh.assert_not_called()

    def test_authorization_completes_before_irc_connection(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as directory, patch.object(bot, "ROOT", Path(directory)):
                instance = bot.Bot(self.values())
                reader = asyncio.StreamReader()
                reader.feed_data(b"PING :server\r\n:viewer!viewer PRIVMSG #streamer : RECONNECT\r\n")
                reader.feed_eof()
                writer = AsyncMock()
                sent = []
                writer.write = sent.append
                writer.close = Mock()
                listener = AsyncMock()
                async def listen():
                    await asyncio.Event().wait()
                listener.run.side_effect = listen
                async def open_connection(*args, **kwargs):
                    listener.prepare.assert_awaited_once()
                    return reader, writer
                with patch.object(bot, "get_access_token", return_value="token"), patch.object(
                    bot, "RewardListener", return_value=listener
                ), patch.object(bot.asyncio, "open_connection", side_effect=open_connection):
                    await asyncio.wait_for(instance.connection(), 2)
                self.assertIn(b"PONG :server\r\n", sent)
                self.assertIn(b"CAP REQ :twitch.tv/tags twitch.tv/commands\r\n", sent)
                writer.wait_closed.assert_awaited_once()
        asyncio.run(scenario())
