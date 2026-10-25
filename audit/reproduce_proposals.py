"""Read-only observations of unfixed findings using temporary synthetic data.

Run from the repository root. No bot, OAuth flow or live HTTP connection starts.
These observations are separate from the regression suite for approved fixes.
"""

import io
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ai_client
import autonomous
import gui
from message_history import MessageHistory
from recent_context import for_reward
import settings
import twitch_auth
from configuration import DEFAULTS, read_config
from PySide6.QtWidgets import QApplication


def moderated_context():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        store = MessageHistory(root, clock=lambda: 10000)
        record_id = store.add("autonomous", "sent", channel="channel", viewer="viewer", viewer_id="123",
            mode="publish", action="reply", sent_text="@viewer Публичный ответ о боссе",
            context={"basis": [1], "conversation": [{"sequence": 1, "author": "viewer", "user_id": "123",
                "message_id": "source-message", "text": "REMOVED_SCENE_MARKER"}]})
        autonomous.save_settings(root / "autonomous.json", autonomous.AutoSettings(enabled=True))
        controller = autonomous.Autonomous({"TWITCH_CHANNEL": "channel", "TWITCH_BOT_NAME": "helper"},
            root, clock=lambda: 10000, emit=lambda event: None, history_store=store)
        try:
            controller.connect(None, lambda: False)
            controller.remember_reply("@viewer Публичный ответ о боссе", target="viewer", user_id="123",
                basis=(1,), source_rows=({"sequence": 1, "author": "viewer", "user_id": "123",
                                        "message_id": "source-message", "text": "REMOVED_SCENE_MARKER"},))
            controller.receive("@target-msg-id=source-message :tmi.twitch.tv CLEARMSG #channel :deleted")
            rows = for_reward(store, controller.reward_reply_snapshot(), "channel", "viewer", "123", 10001)
            return {"ram_context_cleared": not controller.recent_replies,
                    "sqlite_context_reintroduced": "REMOVED_SCENE_MARKER" in str(rows),
                    "journal_record_preserved": store.detail(record_id) is not None}
        finally:
            controller.close()


def unsaved_autonomous_draft(app):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        with patch.object(gui, "data_dir", return_value=root), patch.object(gui, "russian_question") as ask:
            window = gui.MainWindow()
            window.autonomous_page.autonomous_prompt.setPlainText("UNSAVED_DRAFT_MARKER")
            result = window.close()
            app.processEvents()
            return {"window_closed": result, "close_confirmation_shown": ask.called,
                    "draft_written": (root / "autonomous.json").exists()}


def partially_saved_settings():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        values = dict(DEFAULTS, TWITCH_CHANNEL="channel", TWITCH_BOT_NAME="helper", TWITCH_CLIENT_ID="client",
                      AI_API_KEY="synthetic-key", AI_MODEL="old-model")
        settings.save_settings(values, "old prompt", root)
        real_replace = os.replace

        def replace(source, destination):
            if Path(destination) == root / "prompt.txt":
                raise OSError("synthetic disk failure")
            return real_replace(source, destination)

        with patch.object(settings.os, "replace", side_effect=replace):
            try:
                settings.save_settings(dict(values, AI_MODEL="new-model"), "new prompt", root)
            except OSError:
                pass
        return {"env_already_updated": read_config(root / ".env")["AI_MODEL"] == "new-model",
                "prompt_still_old": (root / "prompt.txt").read_text().strip() == "old prompt"}


def personal_history_scan():
    with tempfile.TemporaryDirectory() as temporary:
        store = MessageHistory(temporary)
        store.add("autonomous", "generating")  # Create the actual production schema.
        context = json.dumps({"basis": [], "conversation": []})
        rows = [(f"row-{i}", "session", 10000, 10000, "autonomous", "sent", "channel",
                 f"other{i}", str(1000+i), f"@other{i} Синтетический ответ", "publish", context) for i in range(2000)]
        rows.append(("target", "session", 9999, 9999, "autonomous", "sent", "channel", "viewer", "123",
                     "@viewer Нужный ответ", "publish", context))
        with closing(sqlite3.connect(store.path)) as connection:
            with connection:
                connection.executemany("INSERT INTO records(id,session,created,updated,kind,status,channel,viewer,viewer_id,sent_text,mode,context) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
                connection.executemany("INSERT INTO events(record_id,time,status) VALUES (?,?,'sent')",
                                       [(row[0], row[2]) for row in rows])
        decoded, real_loads = 0, json.loads

        def load(value, *args, **kwargs):
            nonlocal decoded
            if value == context:
                decoded += 1
            return real_loads(value, *args, **kwargs)

        with patch("message_history.json.loads", side_effect=load):
            result = store.recent_personal_replies("channel", "viewer", "123", 10001)
        return {"returned": len(result), "context_documents_decoded": decoded}


def unbounded_http_body():
    class Body(io.BytesIO):
        requested_size = None

        def read(self, size=-1):
            self.requested_size = size
            return super().read(size)

    raw = json.dumps({"choices": [{"message": {"content": "Ответ"}}], "ignored": "x" * (2 * 1024 * 1024)}).encode()
    body = Body(raw)
    with patch("urllib.request.urlopen", return_value=body):
        answer = ai_client.request_completion({"AI_CHAT_URL": "https://example.invalid/chat", "AI_API_KEY": "synthetic-key"},
                                             "chosen", [{"role": "user", "content": "Привет"}])
    return {"response_bytes": len(raw), "read_size": body.requested_size, "answer_accepted": answer == "Ответ"}


def malformed_oauth_response():
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "tokens.json"
        original = '{"access_token":"synthetic-token","refresh_token":"synthetic-refresh"}'
        path.write_text(original)
        with patch("urllib.request.urlopen", return_value=io.BytesIO(b"{broken-json")) as http:
            try:
                twitch_auth.get_access_token("client", "helper", path)
            except Exception as exc:
                error_type = type(exc).__name__
        return {"unclassified_exception": error_type, "http_calls": http.call_count,
                "original_cache_preserved": path.read_text() == original}


def main():
    app = QApplication([])
    result = {"CHANGE-01": moderated_context(), "CHANGE-02": unsaved_autonomous_draft(app),
              "CHANGE-03": partially_saved_settings(), "CHANGE-04": personal_history_scan(),
              "CHANGE-05": unbounded_http_body(), "CHANGE-06": malformed_oauth_response()}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    app.quit()


if __name__ == "__main__":
    main()
