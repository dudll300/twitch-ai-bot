"""Durable local message journal, independent of AI memory and session logs."""

from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
from threading import RLock
import time
import uuid

from ai_client import redact_secret
from privacy import safe_history_text
from recent_context import PERSONAL_REPLY_SECONDS, PERSONAL_REPLY_LIMIT, personal_reply

STATUSES = {"generating", "generated", "sent", "preview", "silent", "skipped",
            "cancelled", "error", "send_error", "rejected"}
FIELDS = ("channel", "viewer", "viewer_id", "model", "action", "reason", "question",
          "answer", "sent_text", "mode")
SUMMARY = "seq, id, created, updated, kind, status, " + ", ".join(FIELDS)
SECRET_FIELDS = {"api_key", "ai_api_key", "access_token", "refresh_token", "authorization",
                 "client_secret", "password"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
    session TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
    kind TEXT NOT NULL, status TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT '', viewer TEXT NOT NULL DEFAULT '',
    viewer_id TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '',
    question TEXT NOT NULL DEFAULT '', answer TEXT NOT NULL DEFAULT '',
    sent_text TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL DEFAULT '',
    context TEXT, search TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS records_kind_seq ON records(kind, seq);
CREATE INDEX IF NOT EXISTS records_status_seq ON records(status, seq);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, record_id TEXT NOT NULL,
    time REAL NOT NULL, status TEXT NOT NULL, note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS events_record ON events(record_id, seq);
CREATE TABLE IF NOT EXISTS safety_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, time REAL NOT NULL, scenario TEXT NOT NULL,
    record_id TEXT, status TEXT NOT NULL, stage TEXT NOT NULL, reasons TEXT NOT NULL,
    ai_attempted INTEGER NOT NULL, model TEXT NOT NULL, seconds REAL, policy_version TEXT NOT NULL,
    http_attempts TEXT, twitch_attempted INTEGER, context_metadata TEXT
);
"""


class HistoryReadError(RuntimeError):
    pass


class MessageHistory:
    def __init__(self, root, *, secrets=(), clock=time.time, warn=None):
        self.path = Path(root) / "message-history.sqlite3"
        self.secrets = tuple(value for value in secrets if value)
        self.clock = clock
        self.session = uuid.uuid4().hex
        self._lock = RLock()
        self._ready = False
        self._warned = False
        self.warn = warn or (lambda message: print(message, flush=True))

    def redact(self, value):
        if isinstance(value, str):
            for secret in self.secrets:
                value = redact_secret(value, secret)
            # Never retain obvious credentials echoed in chat or an error.
            return safe_history_text(re.sub(r"(?i)\b(?:bearer\s+|oauth:)[a-z0-9._~+/=-]+",
                          "[токен скрыт]", value))
        if isinstance(value, dict):
            return {safe_history_text(str(key)): "[секрет скрыт]" if str(key).casefold() in SECRET_FIELDS
                    else self.redact(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [self.redact(item) for item in value]
        return value

    def _write(self, operation):
        # All callers execute this in a worker. A storage failure must not
        # change the bot's generation, delivery, reward or quota behavior.
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with closing(sqlite3.connect(self.path, timeout=2)) as connection:
                    connection.row_factory = sqlite3.Row
                    if not self._ready:
                        connection.execute("PRAGMA journal_mode=WAL")
                        connection.executescript(SCHEMA)
                        columns = {row[1] for row in connection.execute('PRAGMA table_info(safety_events)')}
                        for name, definition in (('http_attempts', "TEXT"), ('twitch_attempted', 'INTEGER'), ('context_metadata', 'TEXT')):
                            if name not in columns:
                                connection.execute(f'ALTER TABLE safety_events ADD COLUMN {name} {definition}')
                        self._ready = True
                    connection.execute("PRAGMA synchronous=FULL")
                    with connection:
                        result = operation(connection)
                self._warned = False
                return result
            except (OSError, sqlite3.Error, ValueError, TypeError):
                self._ready = False
                if not self._warned:
                    self._warned = True
                    try:
                        self.warn("История: не удалось сохранить запись. Проверьте папку с данными и свободное место.")
                    except (OSError, RuntimeError, ValueError):
                        pass
                return None

    @staticmethod
    def _search(values):
        return "\n".join(str(values.get(field, "")) for field in FIELDS).casefold()

    def _fields(self, values):
        redacted = self.redact(values)
        # This column is supplied by verified IRC identity, not by parsing a
        # viewer's JSON. Preserve it for ID-first reward history matching.
        uid = values.get('viewer_id', '')
        if isinstance(uid, str) and re.fullmatch(r'[0-9]{1,30}', uid):
            redacted['viewer_id'] = uid
        return redacted

    def safety_event(self, scenario, review, *, record_id=None):
        """Only enumerated metadata; never candidate text or evaluator output."""
        from safety import REASON_NAMES
        if scenario not in {'reward', 'autonomous', 'preview'}:
            raise ValueError('Unknown safety scenario')
        if review.status not in {'allowed', 'local_allowed', 'blocked', 'error', 'cancelled'}:
            raise ValueError('Unknown safety status')
        stage = review.stage if review.stage in {'question', 'answer', 'ai_review', 'publication',
            'selector_request', 'selector_response', 'generator_request', 'generator_response',
            'review_request', 'review_response'} else 'answer'
        reasons = [code for code in review.reasons if code in REASON_NAMES]
        model = self.redact(review.model[:200]) if review.ai_attempted else ''
        def insert(connection):
            counts = tuple(review.http_attempts) if review.http_attempts is not None else None
            if counts is not None and (len(counts) != 3 or any(type(n) is not int or not 0 <= n <= 3 for n in counts)):
                raise ValueError('Invalid HTTP counters')
            metadata = {key: value for key, value in review.context_metadata
                        if key in {'reward_pairs', 'autonomous_replies', 'truncated'}
                        and type(value) in (int, bool) and 0 <= value <= 10}
            connection.execute('INSERT INTO safety_events(time,scenario,record_id,status,stage,reasons,ai_attempted,model,seconds,policy_version,http_attempts,twitch_attempted,context_metadata) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (self.clock(), scenario, record_id, review.status, stage, json.dumps(reasons),
                 int(review.ai_attempted), model, review.seconds, review.policy_version[:64],
                 json.dumps(counts) if counts is not None else None,
                  int(review.twitch_attempted) if review.twitch_attempted is not None else None,
                  json.dumps(metadata) if metadata else None))
            connection.execute('DELETE FROM safety_events WHERE seq NOT IN (SELECT seq FROM safety_events ORDER BY seq DESC LIMIT 200)')
        return self._write(insert)

    def safety_recent(self, limit=20):
        def select(connection):
            if connection is None or not connection.execute("SELECT 1 FROM sqlite_master WHERE name='safety_events'").fetchone():
                return []
            rows = [dict(row) for row in connection.execute('SELECT * FROM safety_events ORDER BY seq DESC LIMIT ?',
                    (max(1, min(int(limit), 200)),))]
            for row in rows:
                row['reasons'] = tuple(json.loads(row['reasons']))
                row['context_metadata'] = json.loads(row['context_metadata']) if row.get('context_metadata') is not None else None
                row['http_attempts'] = tuple(json.loads(row['http_attempts'])) if row.get('http_attempts') is not None else None
                row['twitch_attempted'] = bool(row['twitch_attempted']) if row.get('twitch_attempted') is not None else None
                # Older reward events wrote default zeros without a transport trace.
                # Correct the read view, preserving the database bytes unchanged.
                if row['scenario'] == 'reward' and row['http_attempts'] == (0, 0, 0) and row['context_metadata'] is None:
                    row['http_attempts'] = None
                    row['twitch_attempted'] = None
            return rows
        return self._read(select)

    def add(self, kind, status, *, context=None, **values):
        if kind not in {"reward", "autonomous"} or status not in STATUSES:
            raise ValueError("Unknown history entry")
        record_id, now = uuid.uuid4().hex, self.clock()
        values = self._fields({field: str(values.get(field, "")) for field in FIELDS})
        if status == "silent" or values["action"] == "silent":
            context = None
        def insert(connection):
            stored_context = json.dumps(self.redact(context), ensure_ascii=False, allow_nan=False) if context is not None else None
            connection.execute(
                "INSERT INTO records (id, session, created, updated, kind, status, "
                + ", ".join(FIELDS) + ", context, search) VALUES ("
                + ",".join("?" for _ in range(8 + len(FIELDS))) + ")",
                (record_id, self.session, now, now, kind, status,
                 *(values[field] for field in FIELDS), stored_context, self._search(values)))
            connection.execute("INSERT INTO events(record_id, time, status, note) VALUES (?, ?, ?, ?)",
                               (record_id, now, status, values["reason"]))
            return record_id
        return self._write(insert)

    def update(self, record_id, status, *, context=None, **values):
        if not record_id:
            return None
        if status not in STATUSES:
            raise ValueError("Unknown history status")
        values = self._fields({key: str(value) for key, value in values.items() if key in FIELDS})
        now = self.clock()

        def update(connection):
            row = connection.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
            if row is None:
                return None
            merged = {**dict(row), **values}
            # A late AI result can be retained after cancellation, but cannot
            # turn a cancelled request back into a candidate for delivery.
            new_status = row["status"] if status == "generated" and row["status"] in {
                "cancelled", "skipped", "error", "send_error", "rejected"} else status
            if merged["action"] == "silent":
                new_status = "silent"
            stored_context = row["context"]
            if context is not None:
                stored_context = json.dumps(self.redact(context), ensure_ascii=False, allow_nan=False)
            if (new_status in ("silent", "rejected", "error") or merged["action"] == "silent"
                    or values.get("reason") == "moderation_cancelled"):
                stored_context = None
            connection.execute(
                "UPDATE records SET status=?, updated=?, "
                + ", ".join(field + "=?" for field in FIELDS) + ", context=?, search=? WHERE id=?",
                (new_status, now, *(merged[field] for field in FIELDS), stored_context,
                 self._search(merged), record_id))
            connection.execute("INSERT INTO events(record_id, time, status, note) VALUES (?, ?, ?, ?)",
                               (record_id, now, status, values.get("reason", "")))
            return record_id
        return self._write(update)

    def _read(self, operation, cancel=None):
        if not self.path.exists():
            return operation(None)
        try:
            with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro",
                                         uri=True, timeout=2)) as connection:
                connection.row_factory = sqlite3.Row
                if cancel is not None:
                    connection.set_progress_handler(lambda: int(cancel.is_set()), 1000)
                return operation(connection)
        except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
            raise HistoryReadError("Не удалось прочитать историю. Проверьте папку с данными; исходный файл сохранён.") from exc

    def page(self, *, kind="", status="", search="", before=None, limit=50, cancel=None):
        limit = max(1, min(int(limit), 100))
        clauses, args = [], []
        if kind:
            clauses.append("kind=?")
            args.append(kind)
        if status == "not_sent":
            clauses.append("status IN ('generated', 'generating', 'skipped', 'cancelled')")
        elif status == "errors":
            clauses.append("status IN ('error', 'send_error')")
        elif status:
            clauses.append("status=?")
            args.append(status)
        if search.strip():
            clauses.append("instr(search, ?) > 0")
            args.append(search.strip().casefold())
        if before is not None:
            clauses.append("seq < ?")
            args.append(int(before))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""

        def select(connection):
            if connection is None:
                return {"rows": [], "more": False}
            rows = [dict(row) for row in connection.execute(
                "SELECT " + SUMMARY + " FROM records" + where + " ORDER BY seq DESC LIMIT ?",
                (*args, limit + 1))]
            return {"rows": rows[:limit], "more": len(rows) > limit}
        return self._read(select, cancel)

    def detail(self, record_id, *, cancel=None):
        def select(connection):
            if connection is None:
                return None
            row = connection.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["context"] = json.loads(result["context"]) if result["context"] else None
            result["events"] = [dict(event) for event in connection.execute(
                "SELECT time, status, note FROM events WHERE record_id=? ORDER BY seq", (record_id,))]
            return result
        return self._read(select, cancel)

    def recent_personal_replies(self, channel, login, user_id, now):
        """Read successful publications by sent-event time, including older schemas.

        Existing autonomous records may lack viewer_id: derive it only from the
        selected basis authored by the actual tagged recipient, never mentions.
        No schema migration, prompts, profiles or full conversations are returned.
        """
        def select(connection):
            if connection is None:
                return []
            rows = connection.execute("""
                SELECT r.viewer, r.viewer_id, r.sent_text, r.context, r.mode, r.channel,
                       MIN(e.time) AS sent_at
                FROM records r JOIN events e ON e.record_id=r.id AND e.status='sent'
                WHERE r.kind='autonomous' AND r.status='sent' AND r.mode!='preview'
                      AND lower(r.channel)=?
                GROUP BY r.id HAVING sent_at BETWEEN ? AND ? ORDER BY sent_at DESC, r.seq DESC
                """, (channel.casefold(), now - PERSONAL_REPLY_SECONDS, now))
            result = []
            for row in rows:
                try:
                    context = json.loads(row["context"]) if row["context"] else {}
                    basis = context.get("basis", [])
                    anchors = [item for item in context.get("conversation", [])
                               if isinstance(item, dict) and item.get("sequence") in basis]
                    ids = {item.get("user_id") for item in anchors
                           if item.get("author", "").casefold() == row["viewer"].casefold() and item.get("user_id")}
                    saved_id = row["viewer_id"] or (next(iter(ids)) if len(ids) == 1 else "")
                    # Ambiguous identities in a damaged/legacy record are never login fallback.
                    if not row["viewer_id"] and len(ids) > 1:
                        continue
                    candidate = {"source": "autonomous", "status": "sent", "mode": row["mode"],
                                 "channel": row["channel"], "time": row["sent_at"], "text": row["sent_text"],
                                 "target": row["viewer"], "user_id": saved_id, "basis_messages": anchors}
                    if personal_reply(candidate, login, user_id, now, channel=channel):
                        result.append(self.redact(candidate))
                    if len(result) >= PERSONAL_REPLY_LIMIT:
                        break
                except (ValueError, TypeError, AttributeError):
                    continue
            return result
        return self._read(select)
