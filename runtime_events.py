"""Optional structured UI telemetry. No Qt, chat text, or credential payloads."""
from dataclasses import dataclass, field
import json
import hashlib
from threading import Lock
import time
import uuid
from urllib.parse import urlsplit

PREFIX = "UI_EVENT "
ALLOWED = {
    "lifecycle": {"state", "channel", "model", "reward"},
    "chat": {"state"}, "eventsub": {"state"},
    "auth": {"state", "account", "role", "url", "code", "expires_in", "reason"},
    "queue": {"size", "phase"},
    "ai": {"state", "model"},
    "reply": {"state", "record_id"},
    "settings": {"section", "version"},
}
_emitter = None


def revision_id(value):
    """Revisions may include prompt text internally; only an opaque digest leaves the worker."""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def activation_url(value):
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme == "https" and parsed.hostname in ("twitch.tv", "www.twitch.tv") and parsed.path == "/activate" and not parsed.username else ""
    except (ValueError, TypeError, AttributeError):
        return ""


class UiEventEmitter:
    def __init__(self, session=None, write=None, clock=time.time, secrets=()):
        self.session = session or uuid.uuid4().hex
        self.write = write or (lambda line: print(line, flush=True))
        self.clock = clock
        self.sequence = 0
        self.lock = Lock()
        self.secrets = tuple(value for value in secrets if value)
        self.settings = {}

    def emit(self, kind, **payload):
        if kind not in ALLOWED:
            return
        clean = {key: value for key, value in payload.items() if key in ALLOWED[kind]
                 and type(value) in (str, int, float, bool)}
        if kind == "settings" and "version" in clean:
            clean["version"] = revision_id(clean["version"])
        for key, value in tuple(clean.items()):
            if isinstance(value, str):
                for secret in self.secrets:
                    value = value.replace(secret, "[секрет скрыт]")
                value = " ".join(value.split())
                clean[key] = value[:300]
        if "url" in clean and not activation_url(clean["url"]):
            clean.pop("url")
        with self.lock:
            if kind == "settings":
                section, revision = clean.get("section"), clean.get("version")
                if self.settings.get(section) == revision:
                    return
                self.settings[section] = revision
            self.sequence += 1
            envelope = {"version": 1, "session": self.session, "seq": self.sequence,
                        "time": self.clock(), "type": kind, "data": clean}
            try:
                self.write(PREFIX + json.dumps(envelope, ensure_ascii=False))
            except Exception:
                # Display telemetry must never affect publication or queue processing.
                pass


def install_ui_events(emitter):
    global _emitter
    _emitter = emitter


def emit_ui(kind, **payload):
    if _emitter is not None:
        _emitter.emit(kind, **payload)


@dataclass
class RuntimeState:
    session: str = ""
    sequence: int = 0
    process: str = "idle"
    chat: str = "unknown"
    eventsub: str = "unknown"
    ai: str = "unknown"
    ai_model: str = ""
    ai_time: float = 0
    queue: int = 0
    processing: bool = False
    questions: int = 0
    answers: int = 0
    active: dict = field(default_factory=dict)
    auth: dict = field(default_factory=dict)
    settings: dict = field(default_factory=dict)

    def consume(self, line):
        if not line.startswith(PREFIX):
            return False
        try:
            event = json.loads(line[len(PREFIX):])
            if not isinstance(event, dict) or type(event.get("version")) is not int or event.get("version") != 1:
                return False
            kind, data = event.get("type"), event.get("data")
            session, seq = event.get("session"), event.get("seq")
            if kind not in ALLOWED or not isinstance(data, dict) or not isinstance(session, str):
                return False
            if not session or len(session) > 128:
                return False
            if any(type(value) not in (str, int, float, bool) for key, value in data.items() if key in ALLOWED[kind]):
                return False
            if any(not isinstance(data[key], str) for key in ALLOWED[kind] - {"size", "expires_in"} if key in data):
                return False
            if type(seq) is not int or seq <= 0:
                return False
            if self.session and session != self.session:
                return False
            if seq <= self.sequence:
                return False
            if not self.session:
                if kind != "lifecycle" or data.get("state") != "starting":
                    return False
                self.session = session
            if kind == "queue" and (type(data.get("size")) is not int or data["size"] < 0):
                return False
            self.sequence = seq
            if kind == "lifecycle":
                self.process = data.get("state", self.process)
                self.active.update({key: data[key] for key in ("channel", "model", "reward") if isinstance(data.get(key), str)})
            elif kind in ("chat", "eventsub"):
                state = data.get("state")
                if state in ("connecting", "ready", "disconnected", "error"):
                    setattr(self, kind, state)
            elif kind == "auth":
                self.auth = {key: data[key] for key in ALLOWED[kind] if key in data}
                self.auth["url"] = activation_url(self.auth.get("url", ""))
            elif kind == "queue":
                self.queue = data["size"]
                phase = data.get("phase")
                if phase == "queued":
                    self.questions += 1
                elif phase == "started":
                    self.processing = True
                elif phase == "finished":
                    self.processing = False
            elif kind == "reply" and data.get("state") == "sent":
                self.answers += 1
            elif kind == "ai":
                self.ai = data.get("state", "unknown")
                self.ai_model = data.get("model", "")
                stamp = event.get("time")
                self.ai_time = stamp if type(stamp) in (int, float) else 0
            elif kind == "settings" and isinstance(data.get("section"), str):
                self.settings[data["section"]] = str(data.get("version", ""))
            return True
        except (ValueError, TypeError, RecursionError):
            return False
