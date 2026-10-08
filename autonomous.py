"""Low-priority, bounded chat participation with live local settings."""

import asyncio
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import wraps
import json
import hashlib
import math
from pathlib import Path
import random
import re
import time
from threading import Event, RLock
# Shared module reference retained for existing network test hooks.
import urllib.request
import uuid

from ai_client import clean_text, redact_secret
from privacy import FragmentGuard, PrivacyViolation, unsafe_question
from irc import moderation_event
from safety import SafetyBlocked, SafetyReview, review_candidate, validate_publication
from local_context import LocalBundle, LocalResultError
from participation import (DEFAULT_AUTONOMOUS_PROMPT, Decision, PARTICIPATION, ParticipationError, RequestCancelled, check_basis, parse_decision,
                           repeats, request_decision, still_relevant)


def state_locked(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self._state_lock:
            return method(self, *args, **kwargs)
    return locked


@dataclass(frozen=True)
class AutoSettings:
    enabled: bool = False
    mode: str = "preview"
    participation: str = "balanced"
    autonomous_prompt: str = DEFAULT_AUTONOMOUS_PROMPT
    context_count: int = 20
    freshness_seconds: int = 240
    active_seconds: int = 60
    check_min_seconds: int = 12
    check_max_seconds: int = 20
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
    prompt = values["autonomous_prompt"]
    if (not isinstance(prompt, str) or len(prompt) > 10000
            or any((ord(char) < 32 and char not in "\n\r\t") or 127 <= ord(char) <= 159 for char in prompt)):
        raise ValueError("Инструкции самостоятельного режима: нужен текст до 10000 символов без управляющих знаков.")
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
        self.deleted = OrderedDict()
        self.cutoffs = OrderedDict()
        self.fragments = FragmentGuard()

    def clear(self):
        self.messages.clear()
        self.seen.clear()
        self.fragments.clear()

    def reset_session(self):
        self.clear()
        self.deleted.clear()
        self.cutoffs.clear()

    def moderate(self, event, now):
        removed = [row for row in self.messages if event.affects(row)]
        if event.kind in ("all", "uncertain"):
            self.seen.clear()
        else:
            removed_ids = {row.get("message_id") for row in removed}
            self.seen = deque((row for row in self.seen if row[2] not in removed_ids), maxlen=1000)
        self.messages = deque(row for row in self.messages if not event.affects(row))
        ids = {row["message_id"] for row in removed if row.get("message_id")}
        if event.message_id:
            ids.add(event.message_id)
        for identity in ids:
            self.deleted[identity] = now
            self.deleted.move_to_end(identity)
        while len(self.deleted) > 1000:
            self.deleted.popitem(last=False)
        if event.timestamp and event.kind in ("user", "all"):
            key = ("id", event.user_id) if event.user_id else ("login", event.login) if event.login else ("all", "")
            self.cutoffs[key] = (now, max(event.timestamp, self.cutoffs.get(key, (now, 0))[1]))
            self.cutoffs.move_to_end(key)
            while len(self.cutoffs) > 256:
                self.cutoffs.popitem(last=False)
        if event.user_id:
            self.fragments.clear(event.user_id)
        else:
            self.fragments.clear()
        return removed

    def fresh(self, now, settings):
        while self.messages and now - self.messages[0]["time"] > settings.freshness_seconds:
            self.messages.popleft()
        while len(self.messages) > settings.context_count:
            self.messages.popleft()
        return list(self.messages)

    def add_irc(self, line, channel, bot_name, now, settings):
        self.deleted = OrderedDict((key, at) for key, at in self.deleted.items() if now - at <= 300)
        self.cutoffs = OrderedDict((key, row) for key, row in self.cutoffs.items() if now - row[0] <= 300)
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
        if unsafe_question(text):
            return False
        uid = tags.get("user-id", "")
        mid = tags.get("id", "")
        if mid and mid in self.deleted:
            return False
        sent = tags.get("tmi-sent-ts", "")
        if re.fullmatch(r"[0-9]{1,16}", sent):
            for key in (("all", ""), ("id", uid), ("login", author)):
                if key in self.cutoffs and int(sent) <= self.cutoffs[key][1]:
                    return False
        conversation = tags.get("reply-thread-parent-msg-id") or tags.get("reply-parent-msg-id") or "sender"
        if self.fragments.check(text, uid, conversation, remember=True):
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
                              "user_id": tags.get("user-id", "") if re.fullmatch(r"[0-9]{1,30}", tags.get("user-id", "")) else "",
                              "message_id": tags.get("id", "")[:100],
                              "reply_parent_id": tags.get("reply-parent-msg-id", "")[:100],
                              "reply_parent_user_id": tags.get("reply-parent-user-id", "") if re.fullmatch(r"[0-9]{1,30}", tags.get("reply-parent-user-id", "")) else "",
                              "reply_parent_login": tags.get("reply-parent-user-login", "").casefold() if re.fullmatch(r"[A-Za-z0-9_]{1,25}", tags.get("reply-parent-user-login", "")) else "",
                              "thread_id": tags.get("reply-thread-parent-msg-id", "")[:100],
                              "mentions": tuple(dict.fromkeys(re.findall(r"(?<!\w)@([a-z0-9_]{1,25})(?!\w)", text.casefold())))})
        self.fresh(now, settings)
        return True


@dataclass(frozen=True)
class Pending:
    future: object
    generation: int
    messages: tuple
    new_ids: frozenset
    started_at: float
    cancel: object = None
    window: object = None
    local_snapshot: object = None
    history_ref: object = None


@dataclass
class RequestWindow:
    deadline: float
    monotonic_deadline: float

    def remaining(self, now, monotonic_now):
        return min(self.deadline - now, self.monotonic_deadline - monotonic_now)

    def tighten(self, deadline):
        # Preserve the wall/monotonic offset captured at submission, even when
        # the system clock changes while the selector is running.
        difference = min(0, deadline - self.deadline)
        self.deadline += difference
        self.monotonic_deadline += difference


class Autonomous:
    def __init__(self, cfg, root, *, profiles=None, memory_data=None,
                 clock=time.time, monotonic_clock=time.monotonic,
                 choose_delay=random.uniform, request=request_decision, emit=None, local_manager=None,
                 history_store=None, fragment_guard=None):
        self.cfg, self.root = cfg, root
        self.clock, self.choose_delay, self.request = clock, choose_delay, request
        self.monotonic_clock = monotonic_clock
        self.profiles, self.memory_data = profiles or [], memory_data
        self.emit = emit or self._print_event
        self.local_manager = local_manager
        self.history_store = history_store
        self.output_fragments = fragment_guard or FragmentGuard()
        self.local_revision = local_manager.snapshot().revision if local_manager is not None else None
        self.settings = AutoSettings()
        # HTTP reservations and publication share one atomic quota file.
        self._state_lock = RLock()
        self.revision = ""
        self.buffer = ChatBuffer()
        self.generation = 0
        self.connected = False
        self.last_checked = 0
        self.next_check = 0
        self.batch_started = None
        self.last_message_at = 0
        self.pending = None
        self.reviewing = None
        self.moderation_generation = -1
        self.executor = None
        self.sender = None
        self.paid_busy = lambda: False
        self.quota = []
        self.requests = []
        self.last_text = ""
        self.recent_replies = deque(maxlen=10)
        self.recent_candidates = deque(maxlen=20)
        self.consumed_ids = deque(maxlen=1000)
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

    @state_locked
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
        with self._state_lock:
            self.generation += 1
            for pending in (self.pending, self.reviewing):
                if pending is not None and pending.cancel is not None:
                    pending.cancel.set()
            # A reward or settings change closes the old opportunity, even if
            # a later chat batch still contains its original messages.
            self.consume(row["sequence"] for row in self.buffer.messages)
            self.last_checked = self.buffer.sequence
            self.batch_started = None

    @state_locked
    def connect(self, sender, paid_busy):
        self.sender, self.paid_busy = sender, paid_busy
        self.connected = True
        self.interrupt()
        self.buffer.reset_session()
        self.next_check = self.clock()

    @state_locked
    def disconnect(self):
        self.connected = False
        self.interrupt()
        self.buffer.reset_session()
        self.recent_replies.clear()
        self.recent_candidates.clear()
        self.output_fragments.clear()
        self.reviewing = None

    def receive(self, line):
        with self._state_lock:
            event = moderation_event(line, self.cfg["TWITCH_CHANNEL"])
            if event is not None and self.connected:
                removed = self.buffer.moderate(event, self.clock())
                self.output_fragments.clear(event.user_id or None)
                active = self.pending or self.reviewing
                affected = active is not None and any(event.affects(row) for row in active.messages)
                if affected or event.kind in ("all", "uncertain"):
                    if active is not None and active.cancel is not None:
                        active.cancel.set()
                    self.moderation_generation = active.generation if active is not None else self.generation
                    self.generation += 1
                removed_sequences = {row["sequence"] for row in removed}
                self.recent_replies = deque((row for row in self.recent_replies
                    if not removed_sequences.intersection(row.get("basis", ()))
                    and not any(event.affects(source) for source in row.get("source_rows", ()))
                    and not (event.user_id and row.get("user_id") == event.user_id)
                    and not (event.login and row["target"] == event.login)), maxlen=10)
                if event.kind in ("all", "uncertain") or affected:
                    self.recent_replies.clear()
                # Candidate history and the old publication fingerprint can
                # otherwise bring a removed discussion back into a later prompt.
                if removed or affected or event.kind in ("all", "uncertain"):
                    self.recent_candidates.clear()
                    self.last_text = ""
                if event.kind == "all":
                    self.recent_replies.clear()
                    self.batch_started = None
                    self.last_checked = self.buffer.sequence
                self.event("skipped", reason="moderation_cancelled")
                return
            if self.connected and self.settings.enabled:
                now = self.clock()
                if self.buffer.add_irc(line, self.cfg["TWITCH_CHANNEL"], self.cfg["TWITCH_BOT_NAME"], now, self.settings):
                    if self.batch_started is None or now - self.last_message_at > self.settings.max_wait_seconds:
                        self.batch_started = now
                    self.last_message_at = now

    @state_locked
    def remember_reply(self, text, *, target="", source="autonomous", question="", basis=(), user_id="", source_rows=()):
        if not self.connected or not self.settings.enabled:
            return
        self.recent_replies.append({"time": self.clock(), "text": clean_text(
            redact_secret(text, self.cfg.get("AI_API_KEY", "")), 450), "target": target, "source": source,
            "question": clean_text(redact_secret(question, self.cfg.get("AI_API_KEY", "")), 400), "basis": tuple(basis),
            "user_id": user_id, "status": "sent", "channel": self.cfg.get("TWITCH_CHANNEL", ""),
            "basis_messages": tuple({"author": row["author"], "text": row["text"]} for row in source_rows
                                    if row["sequence"] in basis),
            "source_rows": tuple({key: row.get(key, "") for key in ("message_id", "user_id", "author")} for row in source_rows)})

    @state_locked
    def reward_reply_snapshot(self):
        return tuple(deepcopy(row) for row in self.recent_replies)

    def eligible(self, *, new_context=True):
        with self._state_lock:
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

    def consume(self, ids):
        for value in ids:
            if value not in self.consumed_ids:
                self.consumed_ids.append(value)

    def reserve(self, text, basis=()):
        with self._state_lock:
            now = self.clock()
            quota = [t for t in self.quota if now - t < 3600] + [now]
            self.write_quota(quota, self.requests, text)
            self.quota, self.last_text = quota, text
            self.recent_candidates.append((now, text))
            self.consume(basis)

    def before_request(self, generation, cancel, window, local_snapshot=None):
        """Reserve each attempted HTTP call durably, from the AI worker."""
        with self._state_lock:
            remaining = window.remaining(self.clock(), self.monotonic_clock())
            try:
                _, revision = load_settings(self.root / "autonomous.json")
            except (OSError, ValueError, TypeError):
                revision = "invalid"
            if (cancel.is_set() or generation != self.generation or revision != self.revision
                    or (local_snapshot is not None and not self.local_manager.is_current(local_snapshot))
                    or remaining <= 0 or not self.eligible(new_context=False)):
                raise RequestCancelled("Реплика отменена или повод устарел.")
            if len(self.requests) >= self.settings.request_hourly_limit:
                raise RequestCancelled("Исчерпан часовой лимит AI-запросов.")
            requests = self.requests + [self.clock()]
            try:
                self.write_quota(self.quota, requests, self.last_text)
            except OSError:
                self.quota_error = True
                raise RequestCancelled("Не удалось сохранить счётчик AI-запросов. Самостоятельные запросы остановлены.") from None
            self.requests = requests
            remaining = window.remaining(self.clock(), self.monotonic_clock())
            if remaining <= 0:
                raise RequestCancelled("Время на самостоятельную реплику истекло.")
            return min(20, remaining)

    def before_generation(self, plan, messages, new_ids, generation, cancel, window):
        with self._state_lock:
            window.tighten(max(row["time"] for row in messages if row["sequence"] in plan.basis)
                           + self.settings.reply_ttl_seconds)
            decision = Decision("reply", target=plan.target, basis=plan.basis, reason=plan.reason)
            if (cancel.is_set() or generation != self.generation
                    or window.remaining(self.clock(), self.monotonic_clock()) <= 0
                    or set(plan.basis).intersection(self.consumed_ids)
                    or not still_relevant(decision, messages, self.buffer.fresh(self.clock(), self.settings),
                                          new_ids, self.clock(), self.settings)):
                raise RequestCancelled("Выбранный повод уже использован или устарел.")

    async def finish_history(self, pending, status, *, decision=None, reason="", sent_text=""):
        if self.history_store is None:
            return
        ref = pending.history_ref
        record_id = ref[0] if ref else None
        if record_id is None:
            # Injectable request implementations may omit the observer. Keep
            # only their explicit anchors, never attach the entire chat batch.
            context = None
            if (status not in ("rejected", "skipped", "cancelled") and reason != "moderation_cancelled"
                    and decision is not None and decision.action != "silent"):
                context = {"conversation": [dict(row) for row in pending.messages
                            if row["sequence"] in decision.basis], "basis": list(decision.basis)}
            record_id = await asyncio.to_thread(self.history_store.add, "autonomous",
                "silent" if decision is not None and decision.action == "silent" else "generated" if decision else status,
                channel=self.cfg.get("TWITCH_CHANNEL", ""), model=self.cfg.get("AI_MODEL", ""),
                mode=self.settings.mode, viewer=decision.target if decision else "",
                action=decision.action if decision else "", answer=decision.reply if context is not None else "",
                reason=decision.reason if decision else reason, context=context)
            if ref is not None:
                ref[0] = record_id
        if record_id:
            # A silent model result has no context or outgoing message, even
            # when its request was cancelled before the controller consumed it.
            detail_status = "silent" if decision is not None and decision.action == "silent" else status
            fields = {"sent_text": sent_text}
            if status in ("rejected", "skipped", "cancelled"):
                fields["answer"] = ""
                fields["context"] = None
            elif decision is not None and decision.action != "silent":
                fields["answer"] = decision.reply
                recipient = next((row for row in pending.messages if row["sequence"] in decision.basis
                                  and decision.target and row["author"] == decision.target), {})
                fields["viewer_id"] = recipient.get("user_id", "")
                if ref is not None and len(ref) > 1:
                    fields["context"] = ref[1]
            if reason:
                fields["reason"] = reason
            await asyncio.to_thread(self.history_store.update, record_id, detail_status, **fields)

    async def tick(self):
        self.refresh()
        self.refresh_local()
        if self.pending is not None and self.pending.future.done():
            pending = self.pending
            self.pending = None
            self.reviewing = pending
            def current():
                self.refresh()
                return (pending.generation == self.generation and self.eligible(new_context=False)
                        and (pending.local_snapshot is None or self.local_manager.is_current(pending.local_snapshot))
                        and (pending.window is None or pending.window.remaining(self.clock(), self.monotonic_clock()) > 0)
                        and self.clock() - pending.started_at <= self.settings.reply_ttl_seconds)
            if not current():
                await self.finish_history(pending, "skipped", reason="moderation_cancelled" if pending.generation == self.moderation_generation else "Результат отменён: настройки, очередь наград или срок актуальности изменились.")
                self.event("error" if self.quota_error else "skipped", reason=(
                    "Не удалось сохранить счётчик AI-запросов. Самостоятельные запросы остановлены."
                    if self.quota_error else
                    "Подготовленная реплика отменена: изменились настройки, контекст или очередь наград либо истёк срок ответа."))
            else:
                try:
                    stage = "autonomous_validation"
                    result = pending.future.result()
                    # Injected request implementations are checked by the same protocol.
                    decision = parse_decision(json.dumps({key: getattr(result, key)
                        for key in ("action", "text", "target", "basis", "reason")}), self.settings.max_chars)
                    local_bundle = getattr(result, "local_bundle", None)
                    creative_card_id = getattr(result, "creative_card_id", None)
                    if creative_card_id is not None and (self.local_manager is None
                            or not isinstance(local_bundle, LocalBundle)
                            or creative_card_id not in local_bundle.candidate_ids):
                        raise LocalResultError("Некорректная локальная отсылка.")
                    check_basis(decision, pending.messages, pending.new_ids)
                    if redact_secret(decision.reply, self.cfg.get("AI_API_KEY", "")) != decision.reply:
                        raise ValueError("secret in reply")
                    def valid():
                        return (current() and not set(decision.basis).intersection(self.consumed_ids)
                                and (not isinstance(local_bundle, LocalBundle)
                                     or self.local_manager.is_current(local_bundle.snapshot))
                                and (creative_card_id is None
                                     or self.local_manager.is_allowed(local_bundle, creative_card_id))
                                and still_relevant(decision, pending.messages,
                                    self.buffer.fresh(self.clock(), self.settings), pending.new_ids, self.clock(), self.settings))
                    text = decision.reply
                    approval = None
                    if decision.action != "silent" and valid():
                        addressed = next((row for row in pending.messages if row["sequence"] in decision.basis
                                          and (not decision.target or row["author"] == decision.target)), {})
                        target_id = addressed.get("user_id", "")
                        if self.output_fragments.check(decision.text, target_id, "public-replies"):
                            raise SafetyBlocked(SafetyReview("blocked", "", ("privacy_blocked",)))
                        scene = "\n".join(row["text"] for row in pending.messages if row["sequence"] in decision.basis)
                        approval = await asyncio.to_thread(review_candidate, self.cfg, self.cfg.get("AI_MODEL", ""), text,
                            kind="autonomous", target=decision.target, context=scene, limit=min(self.settings.max_chars, 450),
                            before_request=lambda: self.before_request(pending.generation, pending.cancel, pending.window, pending.local_snapshot),
                            cancelled=lambda: not current())
                        if not approval.allowed:
                            raise SafetyBlocked(approval)
                        validate_publication(text, approval, limit=min(self.settings.max_chars, 450), target=decision.target)
                    previous = [row[1] for row in self.recent_candidates if self.clock() - row[0] < 3600]
                    previous += [row["text"] for row in self.recent_replies if self.clock() - row["time"] < 3600]
                    if self.last_text:
                        previous.append(self.last_text)
                    if decision.action == "silent":
                        await self.finish_history(pending, "silent", decision=decision)
                        self.event("silent", action=decision.action, reason=decision.reason)
                    elif not valid():
                        await self.finish_history(pending, "skipped", decision=decision, reason="Повод устарел или обсуждение сменилось.")
                        self.event("skipped", reason="Повод устарел или обсуждение сменилось.")
                    elif repeats(text, previous):
                        await self.finish_history(pending, "skipped", decision=decision, reason="Повтор недавней реплики или вопроса.")
                        self.event("skipped", reason="Повтор недавней реплики или вопроса.")
                    elif self.settings.mode == "preview":
                        await self.finish_history(pending, "preview", decision=decision)
                        if not valid():
                            await self.finish_history(pending, "skipped", reason="moderation_cancelled" if pending.generation == self.moderation_generation else "Повод устарел.")
                            self.event("skipped", reason="Реплика отменена до предпросмотра.")
                        else:
                            self.reserve(text, decision.basis)
                            self.event("preview", text, decision.reason,
                                       reason=("Творческая карточка в предпросмотре: " + redact_secret(
                                           creative_card_id, self.cfg.get("AI_API_KEY", ""))) if creative_card_id else "",
                                       target=decision.target, basis=decision.basis)
                    else:
                        kwargs = {"local_bundle": local_bundle, "creative_card_id": creative_card_id} if creative_card_id is not None else {}
                        kwargs["approval"] = approval
                        stage = "autonomous_send"
                        sent = await self.sender(text, valid, lambda: self.reserve(text, decision.basis), **kwargs)
                        if (sent and pending.generation == self.generation and self.connected
                                and (pending.cancel is None or not pending.cancel.is_set())):
                            self.output_fragments.check(decision.text, target_id, "public-replies", remember=True)
                            self.remember_reply(text, target=decision.target, basis=decision.basis,
                                                user_id=target_id, source_rows=pending.messages)
                        final_reason = ("moderation_cancelled" if pending.generation == self.moderation_generation else
                                        "" if sent else "Реплика уступила приоритет или была отменена.")
                        await self.finish_history(pending, "sent" if sent else "skipped", decision=decision,
                            sent_text=text if sent else "", reason=final_reason)
                        self.event("published" if sent else "skipped", text if sent else "", decision.reason,
                                   final_reason,
                                   target=decision.target if sent else "", basis=decision.basis if sent else ())
                except (SafetyBlocked, PrivacyViolation) as exc:
                    reason = ",".join(exc.review.reasons) if isinstance(exc, SafetyBlocked) else "privacy_blocked"
                    await self.finish_history(pending, "rejected", reason=reason)
                    self.event("skipped", reason=reason)
                except RequestCancelled:
                    await self.finish_history(pending, "cancelled", reason="Подготовка отменена: повод устарел или исчерпан лимит AI-запросов.")
                    self.event("skipped", reason=("Не удалось сохранить счётчик AI-запросов. Самостоятельные запросы остановлены."
                        if self.quota_error else "Подготовка отменена: повод устарел, уже использован или исчерпан лимит AI-запросов."))
                except Exception as exc:
                    code = exc.code if isinstance(exc, ParticipationError) else "autonomous_timeout" if isinstance(exc, TimeoutError) else stage
                    await self.finish_history(pending, "send_error" if code == "autonomous_send" else "error", reason=code)
                    # Raw rejected provider payloads and credentials are never retained.
                    self.event("error", reason=code)
                    self.next_check = self.clock() + max(120, self.settings.check_max_seconds)
            self.reviewing = None
        if self.pending is not None or self.reviewing is not None or self.clock() < self.next_check:
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
        self.schedule()
        if self.executor is None:
            self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="autonomous-ai")
        # Recognition and HTTP both run in the same background worker, never in IRC's event loop.
        generation, cancel = self.generation, Event()
        deadline = min(now + self.settings.reply_ttl_seconds,
                       max(row["time"] for row in rows if row["sequence"] in new_ids) + self.settings.reply_ttl_seconds)
        window = RequestWindow(deadline, self.monotonic_clock() + max(0, deadline - now))
        local_snapshot = self.local_manager.snapshot() if self.local_manager is not None else None
        kwargs = {"profiles": deepcopy(self.profiles), "memory_data": deepcopy(self.memory_data), "new_ids": new_ids,
                  "consumed_ids": tuple(self.consumed_ids),
                  "recent_replies": tuple(dict(row) for row in self.recent_replies if now - row["time"] < 3600),
                  "before_request": lambda: self.before_request(generation, cancel, window, local_snapshot),
                  "before_generation": lambda plan: self.before_generation(plan, rows, new_ids, generation, cancel, window)}
        if self.local_manager is not None:
            kwargs.update(local_manager=self.local_manager, local_snapshot=local_snapshot)
        history_ref = [None, None]
        if self.history_store is not None:
            mode = self.settings.mode
            request_cfg = dict(self.cfg)
            history_ref[0] = await asyncio.to_thread(self.history_store.add, "autonomous", "generating",
                channel=request_cfg.get("TWITCH_CHANNEL", ""), model=request_cfg.get("AI_MODEL", ""), mode=mode)
            if generation != self.generation or not self.connected:
                await asyncio.to_thread(self.history_store.update, history_ref[0], "cancelled",
                    reason="Повод отменён до обращения к AI.")
                return
            def generated(decision, context):
                status = "silent" if decision.action == "silent" else "cancelled" if cancel.is_set() else "generated"
                fields = dict(channel=request_cfg.get("TWITCH_CHANNEL", ""), model=request_cfg.get("AI_MODEL", ""),
                    mode=mode, viewer=decision.target, action=decision.action,
                    answer="", reason="moderation_cancelled" if cancel.is_set() else decision.reason)
                # Do not persist an unreviewed candidate or a late deleted scene.
                history_ref[1] = context if not cancel.is_set() else None
                context = None
                if history_ref[0]:
                    self.history_store.update(history_ref[0], status, context=context, **fields)
                else:
                    history_ref[0] = self.history_store.add("autonomous", status, context=context, **fields)
            kwargs["on_result"] = generated
        future = self.executor.submit(self.request, dict(self.cfg), rows, self.settings, **kwargs)
        self.pending = Pending(future, generation, rows, new_ids, now, cancel, window, local_snapshot, history_ref)
        self.event("pending", reason="Выбираю один разговор; при уместном поводе подготовлю реплику.")

    @state_locked
    def refresh_local(self):
        if self.local_manager is not None:
            snapshot = self.local_manager.snapshot()
            if snapshot.revision != self.local_revision:
                self.local_revision = snapshot.revision
                self.interrupt()

    async def run(self):
        while True:
            await self.tick()
            await asyncio.sleep(0.25)

    def close(self):
        self.disconnect()
        if self.executor is not None:
            self.executor.shutdown(wait=False, cancel_futures=True)
