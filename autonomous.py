"""Low-priority, bounded chat participation with live local settings."""

import asyncio
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import hashlib
import math
from pathlib import Path
import random
import re
import time
# Shared module reference retained for existing network test hooks.
import urllib.request
import uuid

from ai_client import clean_text, redact_secret
from participation import (Decision, PARTICIPATION, check_basis, parse_decision,
                           repeats, request_decision, still_relevant)


@dataclass(frozen=True)
class AutoSettings:
    enabled: bool = False
    mode: str = "preview"
    participation: str = "balanced"
    context_count: int = 20
    freshness_seconds: int = 240
    active_seconds: int = 60
    check_min_seconds: int = 45
    check_max_seconds: int = 90
    pause_seconds: int = 300
    hourly_limit: int = 8
    min_messages: int = 3
    min_authors: int = 2
    max_chars: int = 220
    settle_seconds: int = 3
    max_wait_seconds: int = 12
    reply_ttl_seconds: int = 30
    request_hourly_limit: int = 40


MINIMUM_VALUES = {
    "context_count": 1, "freshness_seconds": 0,
    "active_seconds": 0, "check_min_seconds": 0,
    "check_max_seconds": 0, "pause_seconds": 0,
    "hourly_limit": 0, "min_messages": 1,
    "min_authors": 1, "max_chars": 1,
    "settle_seconds": 0, "max_wait_seconds": 0,
    "reply_ttl_seconds": 0, "request_hourly_limit": 0,
}


def validate_settings(raw: dict) -> AutoSettings:
    if not isinstance(raw, dict):
        raise ValueError("Настройки самостоятельных реплик должны быть объектом.")
    values = asdict(AutoSettings())
    values.update({key: value for key, value in raw.items() if key in values})
    if type(values["enabled"]) is not bool or values["mode"] not in ("preview", "publish"):
        raise ValueError("Некорректный режим самостоятельных реплик.")
    if not isinstance(values["participation"], str) or values["participation"] not in PARTICIPATION:
        raise ValueError("Некорректный характер участия в разговоре.")
    for key, minimum in MINIMUM_VALUES.items():
        if type(values[key]) is not int or values[key] < minimum:
            raise ValueError(f"{key}: нужно целое число не меньше {minimum}.")
    if values["check_min_seconds"] > values["check_max_seconds"]:
        raise ValueError("Минимальный интервал проверки не может быть больше максимального.")
    if values["settle_seconds"] > values["max_wait_seconds"]:
        raise ValueError("Ожидание паузы не может быть больше максимального ожидания фрагмента.")
    if values["active_seconds"] > values["freshness_seconds"]:
        raise ValueError("Окно активности не может быть больше срока хранения контекста.")
    if max(values["min_messages"], values["min_authors"]) > values["context_count"]:
        raise ValueError("Минимум сообщений и авторов не может превышать размер контекста.")
    return AutoSettings(**values)


def load_settings(path: Path) -> tuple[AutoSettings, str]:
    if not path.exists():
        return AutoSettings(), "missing"
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ValueError("autonomous.json: неизвестный формат настроек.")
    settings = validate_settings(raw.get("settings"))
    # Include the actual values so hand-edited files invalidate pending decisions too.
    return settings, str(raw.get("revision", "")) + json.dumps(asdict(settings), sort_keys=True)


def write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def save_settings(path: Path, settings: AutoSettings):
    settings = validate_settings(asdict(settings))
    write_json(path, {"version": 1, "revision": uuid.uuid4().hex, "settings": asdict(settings)})


class ChatBuffer:
    def __init__(self):
        self.messages = deque()
        self.sequence = 0
        self.seen = deque(maxlen=1000)

    def clear(self):
        self.messages.clear()
        self.seen.clear()

    def fresh(self, now, settings):
        while self.messages and now - self.messages[0]["time"] > settings.freshness_seconds:
            self.messages.popleft()
        while len(self.messages) > settings.context_count:
            self.messages.popleft()
        return list(self.messages)

    def add_irc(self, line, channel, bot_name, now, settings):
        tags = {}
        if line.startswith("@"):
            tag_text, _, line = line.partition(" ")
            tags = dict(part.split("=", 1) if "=" in part else (part, "") for part in tag_text[1:].split(";"))
        header, separator, text = line.partition(" :")
        parts = header.split()
        if not separator or len(parts) != 3 or parts[1] != "PRIVMSG" or parts[2].casefold() != "#" + channel.casefold():
            return False
        author = parts[0].lstrip(":").split("!", 1)[0].casefold()
        if not re.fullmatch(r"[a-z0-9_]{1,25}", author) or author == bot_name.casefold() or tags.get("custom-reward-id"):
            return False
        text = " ".join(text.split())
        if (not text or len(text) > 500 or text.startswith(("!", "/", "."))
                or any(ord(c) < 32 for c in text) or not any(c.isalnum() for c in text)
                or re.search(r"(.)\1{7,}", text, re.IGNORECASE)
                or len(re.findall(r"https?://", text)) >= 2):
            return False
        normalized = " ".join(re.findall(r"\w+", text.casefold()))
        words = normalized.split()
        if len(words) >= 8 and len(set(words)) <= 2:
            return False
        self.fresh(now, settings)
        while self.seen and now - self.seen[0][0] > settings.freshness_seconds:
            self.seen.popleft()
        fingerprint = hashlib.sha256(normalized.encode("utf-8")).digest()
        if any(row[1] == fingerprint or (tags.get("id") and row[2] == tags["id"]) for row in self.seen):
            return False
        if sum(row[3] == author and now - row[0] < 10 for row in self.seen) >= 3:
            return False
        self.seen.append((now, fingerprint, tags.get("id", ""), author))
        self.sequence += 1
        self.messages.append({"author": author, "text": text, "time": now,
                              "sequence": self.sequence,
                              "user_id": tags.get("user-id", "") if re.fullmatch(r"[0-9]{1,30}", tags.get("user-id", "")) else ""})
        self.fresh(now, settings)
        return True


@dataclass(frozen=True)
class Pending:
    future: object
    generation: int
    messages: tuple
    new_ids: frozenset
    started_at: float


class Autonomous:
    def __init__(self, cfg, root, *, profiles=None, memory_data=None,
                 clock=time.time, choose_delay=random.uniform, request=request_decision, emit=None):
        self.cfg, self.root = cfg, root
        self.clock, self.choose_delay, self.request = clock, choose_delay, request
        self.profiles, self.memory_data = profiles or [], memory_data
        self.emit = emit or self._print_event
        self.settings = AutoSettings()
        self.revision = ""
        self.buffer = ChatBuffer()
        self.generation = 0
        self.connected = False
        self.last_checked = 0
        self.next_check = 0
        self.batch_started = None
        self.last_message_at = 0
        self.pending = None
        self.executor = None
        self.sender = None
        self.paid_busy = lambda: False
        self.quota = []
        self.requests = []
        self.last_text = ""
        self.recent_replies = deque(maxlen=10)
        self.recent_candidates = deque(maxlen=20)
        self.quota_error = False
        self._load_quota()
        self.refresh()

    def _print_event(self, event):
        print("AUTO_EVENT " + json.dumps(event, ensure_ascii=False), flush=True)

    def event(self, status, text="", action="", reason="", *, target="", basis=()):
        self.emit({"time": datetime.fromtimestamp(self.clock()).strftime("%H:%M:%S"),
                   "status": status, "action": action, "text": redact_secret(text, self.cfg.get("AI_API_KEY", "")),
                   "reason": reason, "target": target, "basis": list(basis)})

    def _load_quota(self):
        path = self.root / "autonomous-quota.json"
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            times = raw["times"]
            requests = raw.get("requests", [])
            if any(not isinstance(rows, list) or any(type(t) not in (int, float) or not math.isfinite(t) for t in rows)
                   for rows in (times, requests)):
                raise ValueError("invalid quota")
            self.quota = sorted(t for t in times if self.clock() - t < 3600)
            self.requests = sorted(t for t in requests if self.clock() - t < 3600)
            self.last_text = clean_text(redact_secret(str(raw.get("last_text", "")), self.cfg.get("AI_API_KEY", "")), 450)
        except (OSError, ValueError, KeyError, TypeError):
            self.quota_error = True
            self.event("error", reason="Не удалось прочитать счётчик самостоятельных реплик. Проверьте autonomous-quota.json.")

    def refresh(self):
        try:
            settings, revision = load_settings(self.root / "autonomous.json")
        except (OSError, ValueError, TypeError):
            settings, revision = AutoSettings(), "invalid"
        if revision == self.revision:
            return
        self.settings, self.revision = settings, revision
        self.interrupt()
        self.buffer.clear()
        if not settings.enabled:
            self.recent_replies.clear()
            self.recent_candidates.clear()
        self.next_check = self.clock()
        self.event("settings", reason=("Настройки повреждены: режим выключен." if revision == "invalid" else
                   "Режим выключен." if not settings.enabled else
                   "Предпросмотр включён." if settings.mode == "preview" else "Публикация включена."))

    def schedule(self):
        self.next_check = self.clock() + self.choose_delay(self.settings.check_min_seconds, self.settings.check_max_seconds)

    def interrupt(self):
        self.generation += 1
        self.last_checked = self.buffer.sequence
        self.batch_started = None

    def connect(self, sender, paid_busy):
        self.sender, self.paid_busy = sender, paid_busy
        self.connected = True
        self.interrupt()
        self.buffer.clear()
        self.next_check = self.clock()

    def disconnect(self):
        self.connected = False
        self.interrupt()
        self.buffer.clear()
        self.recent_replies.clear()
        self.recent_candidates.clear()

    def receive(self, line):
        if self.connected and self.settings.enabled:
            now = self.clock()
            if self.buffer.add_irc(line, self.cfg["TWITCH_CHANNEL"], self.cfg["TWITCH_BOT_NAME"], now, self.settings):
                if self.batch_started is None or now - self.last_message_at > self.settings.max_wait_seconds:
                    self.batch_started = now
                self.last_message_at = now

    def remember_reply(self, text, *, target="", source="autonomous", question=""):
        if not self.connected or not self.settings.enabled:
            return
        self.recent_replies.append({"time": self.clock(), "text": clean_text(
            redact_secret(text, self.cfg.get("AI_API_KEY", "")), 450), "target": target, "source": source,
            "question": clean_text(redact_secret(question, self.cfg.get("AI_API_KEY", "")), 400)})

    def eligible(self, *, new_context=True):
        now, settings = self.clock(), self.settings
        self.quota = [t for t in self.quota if now - t < 3600]
        self.requests = [t for t in self.requests if now - t < 3600]
        rows = self.buffer.fresh(now, settings)
        return (self.connected and settings.enabled and not self.quota_error and not self.paid_busy()
                and len(self.quota) < settings.hourly_limit
                and (not self.quota or now - self.quota[-1] >= settings.pause_seconds)
                and len(rows) >= settings.min_messages
                and len({row["author"] for row in rows}) >= settings.min_authors
                and now - rows[-1]["time"] <= settings.active_seconds
                and (not new_context or (rows[-1]["sequence"] > self.last_checked
                                        and len(self.requests) < settings.request_hourly_limit)))

    def write_quota(self, quota, requests, text):
        write_json(self.root / "autonomous-quota.json", {"times": quota, "requests": requests, "last_text": text})

    def reserve(self, text):
        now = self.clock()
        quota = [t for t in self.quota if now - t < 3600] + [now]
        self.write_quota(quota, self.requests, text)
        self.quota, self.last_text = quota, text
        self.recent_candidates.append((now, text))

    async def tick(self):
        self.refresh()
        if self.pending is not None and self.pending.future.done():
            pending = self.pending
            self.pending = None
            def current():
                self.refresh()
                return (pending.generation == self.generation and self.eligible(new_context=False)
                        and self.clock() - pending.started_at <= self.settings.reply_ttl_seconds)
            if not current():
                self.event("skipped", reason="Подготовленная реплика отменена: изменились настройки, контекст или очередь наград.")
            else:
                try:
                    decision = pending.future.result()
                    # Injected request implementations are checked by the same protocol.
                    decision = parse_decision(json.dumps(asdict(decision)), self.settings.max_chars)
                    check_basis(decision, pending.messages, pending.new_ids)
                    if redact_secret(decision.reply, self.cfg.get("AI_API_KEY", "")) != decision.reply:
                        raise ValueError("secret in reply")
                    def valid():
                        return current() and still_relevant(decision, pending.messages,
                            self.buffer.fresh(self.clock(), self.settings), pending.new_ids, self.clock(), self.settings)
                    text = decision.reply
                    previous = [row[1] for row in self.recent_candidates if self.clock() - row[0] < 3600]
                    previous += [row["text"] for row in self.recent_replies if self.clock() - row["time"] < 3600]
                    if self.last_text:
                        previous.append(self.last_text)
                    if decision.action == "silent":
                        self.event("silent", action=decision.action, reason=decision.reason)
                    elif not valid():
                        self.event("skipped", reason="Повод устарел или обсуждение сменилось.")
                    elif repeats(text, previous):
                        self.event("skipped", reason="Повтор недавней реплики или вопроса.")
                    elif self.settings.mode == "preview":
                        self.reserve(text)
                        self.event("preview", text, decision.reason, target=decision.target, basis=decision.basis)
                    else:
                        sent = await self.sender(text, valid, lambda: self.reserve(text))
                        if sent:
                            self.remember_reply(text, target=decision.target)
                        self.event("published" if sent else "skipped", text if sent else "", decision.reason,
                                   "" if sent else "Реплика уступила приоритет или была отменена.",
                                   target=decision.target if sent else "", basis=decision.basis if sent else ())
                except Exception:
                    # Do not log provider payloads, chat contents, credentials or error bodies.
                    self.event("error", reason="AI или отправка недоступны, либо ответ некорректен. Реплика пропущена.")
                    self.next_check = self.clock() + max(120, self.settings.check_max_seconds)
        if self.pending is not None or self.clock() < self.next_check:
            return
        now = self.clock()
        if (not self.eligible() or self.batch_started is None or
                (now - self.last_message_at < self.settings.settle_seconds
                 and now - self.batch_started < self.settings.max_wait_seconds)):
            return
        rows = tuple(dict(row) for row in self.buffer.fresh(now, self.settings))
        new_ids = frozenset(row["sequence"] for row in rows if row["sequence"] > self.last_checked
                            and now - row["time"] <= self.settings.reply_ttl_seconds)
        self.last_checked = rows[-1]["sequence"]
        self.batch_started = None
        if not new_ids:
            return
        try:
            requests = [t for t in self.requests if now - t < 3600] + [now]
            self.write_quota(self.quota, requests, self.last_text)
            self.requests = requests
        except OSError:
            self.quota_error = True
            self.event("error", reason="Не удалось сохранить счётчик AI-проверок. Самостоятельные запросы остановлены.")
            return
        self.schedule()
        if self.executor is None:
            self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="autonomous-ai")
        # Recognition and HTTP both run in the same background worker, never in IRC's event loop.
        kwargs = {"profiles": self.profiles, "memory_data": self.memory_data, "new_ids": new_ids,
                  "recent_replies": tuple(dict(row) for row in self.recent_replies if now - row["time"] < 3600)}
        future = self.executor.submit(self.request, dict(self.cfg), rows, self.settings, **kwargs)
        self.pending = Pending(future, self.generation, rows, new_ids, now)
        self.event("pending", reason="Проверяю, уместна ли реплика.")

    async def run(self):
        while True:
            await self.tick()
            await asyncio.sleep(0.25)

    def close(self):
        self.disconnect()
        if self.executor is not None:
            self.executor.shutdown(wait=False, cancel_futures=True)
