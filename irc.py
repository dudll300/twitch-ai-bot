"""Bounded IRC parsing and moderation identities; no substring command guessing.

Formats: https://dev.twitch.tv/docs/chat/irc/#clearmsg-command
         https://dev.twitch.tv/docs/chat/irc/#clearchat-tags
"""
from dataclasses import dataclass
import re
import unicodedata


def login(value):
    value = unicodedata.normalize("NFKC", value).casefold()
    return value if re.fullmatch(r"[a-z0-9_]{1,25}", value) else ""


def message_id(value):
    return value if re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value) else ""


@dataclass(frozen=True)
class IRCMessage:
    command: str
    channel: str
    prefix: str
    text: str
    tags: dict
    malformed: bool = False


def parse_irc(line):
    if not isinstance(line, str) or len(line) > 8192 or any(char in line for char in "\r\n\x00"):
        return None
    tags, malformed = {}, False
    if line.startswith("@"):
        raw_tags, space, line = line.partition(" ")
        if not space:
            return None
        for item in raw_tags[1:].split(";"):
            key, _, value = item.partition("=")
            if not key or key in tags:
                malformed = True
            tags[key] = value
    prefix = ""
    if line.startswith(":"):
        prefix, space, line = line[1:].partition(" ")
        if not space:
            return None
    head, _, text = line.partition(" :")
    parts = head.split()
    if not parts:
        return None
    channel = parts[1][1:].casefold() if len(parts) == 2 and parts[1].startswith("#") else ""
    return IRCMessage(parts[0], channel, prefix, text, tags, malformed)


@dataclass(frozen=True)
class ModerationEvent:
    kind: str
    message_id: str = ""
    user_id: str = ""
    login: str = ""
    timestamp: int = 0

    def affects(self, row):
        if self.kind in ("all", "uncertain"):
            return True
        if self.kind == "message":
            return row.get("message_id") == self.message_id
        if self.user_id:
            return row.get("user_id") == self.user_id
        return row.get("author", "").casefold() == self.login


def moderation_event(line, channel):
    message = parse_irc(line)
    if (message is None or message.command not in ("CLEARMSG", "CLEARCHAT")
            or message.channel != channel.casefold() or message.prefix != "tmi.twitch.tv"):
        return None
    tags = message.tags
    timestamp = tags.get("tmi-sent-ts", "")
    timestamp = int(timestamp) if re.fullmatch(r"[0-9]{1,16}", timestamp) else 0
    if message.malformed:
        return ModerationEvent("uncertain")
    if message.command == "CLEARMSG":
        identity = message_id(tags.get("target-msg-id", ""))
        return ModerationEvent("message", message_id=identity, timestamp=timestamp) if identity else ModerationEvent("uncertain")
    if "target-user-id" in tags:
        identity = tags["target-user-id"]
        if not re.fullmatch(r"[0-9]{1,30}", identity):
            return ModerationEvent("uncertain")
        return ModerationEvent("user", user_id=identity, timestamp=timestamp)
    if message.text:
        identity = login(message.text)
        return ModerationEvent("user", login=identity, timestamp=timestamp) if identity else ModerationEvent("uncertain")
    return ModerationEvent("all", timestamp=timestamp)
