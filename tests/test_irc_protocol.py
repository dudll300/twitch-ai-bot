"""One bounded IRC parser for transport, moderation and autonomous messages."""

import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from autonomous import AutoSettings, ChatBuffer
import bot
from irc import moderation_event, parse_irc, pong_payload
from test_autonomous_privacy_regression import CFG


class IRCProtocolTests(unittest.TestCase):
    def test_tagged_prefix_and_unprefixed_ping_have_the_same_payload(self):
        for line in ("PING :server", ":tmi.twitch.tv PING :server", "@tag=value :tmi.twitch.tv PING :server"):
            with self.subTest(line=line):
                message = parse_irc(line)
                self.assertEqual(message.command, "PING")
                self.assertEqual(pong_payload(message), ":server")
        self.assertEqual(pong_payload(parse_irc("PING server")), "server")

    def test_channel_and_join_author_come_from_metadata_not_substrings(self):
        for line in ("@a=b :helper!u@host JOIN #channel", "@a=b :helper!u@host JOIN :#channel",
                     ":tmi.twitch.tv 366 helper #CHANNEL :End of names", ":tmi.twitch.tv ROOMSTATE #channel"):
            message = parse_irc(line)
            self.assertEqual(message.channel, "channel")
        self.assertEqual(parse_irc(":server 366 helper #channel_extra :End of names").channel, "channel_extra")
        self.assertEqual(parse_irc("@a=b :helper!u@host JOIN #channel").prefix, "helper!u@host")

    def test_chat_body_cannot_become_a_server_command(self):
        message = parse_irc("@id=1 :viewer!u PRIVMSG #channel :PING :server RECONNECT Login authentication failed")
        self.assertEqual((message.command, message.channel), ("PRIVMSG", "channel"))
        self.assertEqual(bot.irc_command("@id=1 :viewer!u PRIVMSG #channel :RECONNECT"), "PRIVMSG")
        for value in (None, "", "@tags", ":prefix", "PING :x\r\nJOIN #other", "x" * 8193, "BAD! #channel"):
            self.assertIsNone(parse_irc(value))

    def test_shared_buffer_keeps_reply_metadata_and_rejects_duplicate_tags(self):
        buffer = ChatBuffer()
        line = "@id=one;user-id=123;reply-parent-msg-id=parent;reply-parent-user-id=456;reply-parent-user-login=other :viewer!u PRIVMSG #channel :@other Хорошая попытка"
        self.assertTrue(buffer.add_irc(line, "channel", "helper", 1, AutoSettings()))
        saved = buffer.messages[-1]
        self.assertEqual((saved["user_id"], saved["message_id"], saved["reply_parent_id"], saved["reply_parent_user_id"]),
                         ("123", "one", "parent", "456"))
        self.assertFalse(buffer.add_irc(line.replace("id=one;", "id=one;id=two;"), "channel", "helper", 2, AutoSettings()))
        self.assertEqual(len(buffer.messages), 1)
        self.assertEqual(moderation_event("@target-msg-id=one;target-msg-id=two :tmi.twitch.tv CLEARMSG #channel :text", "channel").kind,
                         "uncertain")


class IRCTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_reader_sends_correct_pong_for_tagged_and_prefixed_ping(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(bot, "ROOT", Path(temporary)):
            instance = bot.Bot(dict(CFG, TWITCH_CLIENT_ID="client", TWITCH_REWARD_TITLE="Synthetic reward"))
            reader = asyncio.StreamReader()
            reader.feed_data(b"@tag=value :tmi.twitch.tv PING :server\r\nPING bare-server\r\n")
            reader.feed_eof()
            writer = AsyncMock()
            writer.write, writer.close = Mock(), Mock()
            listener = AsyncMock()
            async def listen():
                await asyncio.Event().wait()
            listener.run.side_effect = listen
            try:
                with patch.object(bot, "get_access_token", return_value="fixture-token"), \
                        patch.object(bot, "RewardListener", return_value=listener), \
                        patch.object(bot.asyncio, "open_connection", return_value=(reader, writer)):
                    await asyncio.wait_for(instance.connection(), 3)
                sent = [call.args[0] for call in writer.write.call_args_list]
                self.assertIn(b"PONG :server\r\n", sent)
                self.assertIn(b"PONG bare-server\r\n", sent)
            finally:
                instance.autonomous.close()
