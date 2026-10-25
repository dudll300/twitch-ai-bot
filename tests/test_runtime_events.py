"""Public UI event contract, secret isolation and session-scoped history."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import io

from runtime_events import PREFIX, RuntimeState, UiEventEmitter, revision_id, install_ui_events, activation_url
from message_history import MessageHistory
from ui_state import load_ui_state, save_ui_state


class RuntimeTests(unittest.TestCase):
    def test_activation_links_reject_userinfo_and_nonstandard_ports(self):
        for value in ("https://@twitch.tv/activate", "https://:secret@twitch.tv/activate",
                      "https://twitch.tv:444/activate", "https://twitch.tv:bad/activate",
                      "https://twitch.tv.evil.example/activate", "http://twitch.tv/activate"):
            self.assertEqual(activation_url(value), "")
        self.assertEqual(activation_url("https://www.twitch.tv:443/activate"), "https://www.twitch.tv:443/activate")

    def test_behavior_save_is_independent_of_invalid_connection_data(self):
        from settings import save_settings
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / ".env"
            path.write_text('AI_BASE_URL="invalid-url"\n', encoding="utf-8")
            previous = path.read_bytes()
            save_settings({}, "Отвечай дружелюбно", root, sections=("behavior",), allow_incomplete=True)
            self.assertEqual(path.read_bytes(), previous)
            self.assertEqual((root / "prompt.txt").read_text(encoding="utf-8").strip(), "Отвечай дружелюбно")

    def test_live_connection_save_does_not_invalidate_running_tokens(self):
        from settings import save_settings
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = root / ".twitch_token.json"
            token.write_text("existing-token")
            save_settings({"TWITCH_BOT_NAME": "new_bot"}, "", root, sections=("connection",),
                          allow_incomplete=True, invalidate_auth=False)
            self.assertEqual(token.read_text(), "existing-token")

    def test_telemetry_does_not_interleave_the_quota_gate_and_http(self):
        from ai_client import request_completion
        order = []
        emitter = UiEventEmitter(write=lambda line: order.append("event"))
        def gate():
            order.append("gate")
            return 1
        def http(*args, **kwargs):
            order.append("http")
            return io.BytesIO(json.dumps({"choices": [{"message": {"content": "Готово"}}]}).encode())
        install_ui_events(emitter)
        try:
            with patch("urllib.request.urlopen", side_effect=http):
                request_completion({"AI_CHAT_URL": "https://provider.invalid/v1/chat/completions", "AI_API_KEY": "demo-key"},
                                   "demo/model", [{"role": "user", "content": "Привет"}], before_request=gate)
            self.assertEqual(order[order.index("gate") + 1], "http")
        finally:
            install_ui_events(None)

    def test_connection_readiness_is_independent_and_reconnect_clears_it(self):
        lines = []
        emitter = UiEventEmitter("session", lines.append)
        state = RuntimeState()
        emitter.emit("lifecycle", state="starting", channel="demo")
        emitter.emit("eventsub", state="ready")
        emitter.emit("chat", state="ready")
        emitter.emit("chat", state="disconnected")
        for line in lines[:2]:
            self.assertTrue(state.consume(line))
        self.assertEqual(state.chat, "unknown")
        self.assertEqual(state.ai, "unknown")
        self.assertTrue(state.consume(lines[2]))
        self.assertEqual(state.chat, "ready")
        self.assertTrue(state.consume(lines[3]))
        self.assertEqual(state.chat, "disconnected")

    def test_queue_counters_are_exact_and_duplicate_events_are_ignored(self):
        lines = []
        emitter = UiEventEmitter("session", lines.append)
        emitter.emit("lifecycle", state="starting")
        emitter.emit("queue", size=1, phase="queued")
        emitter.emit("queue", size=0, phase="started")
        emitter.emit("reply", state="sent", record_id="id")
        emitter.emit("queue", size=0, phase="finished")
        state = RuntimeState()
        for line in lines:
            state.consume(line)
        self.assertEqual((state.questions, state.answers, state.queue, state.processing), (1, 1, 0, False))
        self.assertFalse(state.consume(lines[3]))
        self.assertEqual(state.answers, 1)

    def test_malformed_and_old_session_events_do_not_replace_current_state(self):
        lines = []
        emitter = UiEventEmitter("current", lines.append)
        emitter.emit("lifecycle", state="starting")
        state = RuntimeState()
        state.consume(lines[0])
        for line in (PREFIX + "{}", PREFIX + "[]", PREFIX + "broken", lines[0].replace('"current"', '"old"'), lines[0].replace('"version": 1', '"version": 2')):
            self.assertFalse(state.consume(line))
        self.assertEqual(state.session, "current")

    def test_credentials_and_prompt_revisions_cannot_leak_in_events(self):
        lines = []
        emitter = UiEventEmitter("session", lines.append, secrets=("private-key",))
        emitter.emit("lifecycle", state="starting", model="private-key", api_key="private-key", prompt="Private instruction")
        emitter.emit("settings", section="autonomous", version="Private instruction")
        emitter.emit("auth", state="required", url="https://evil.example", device_code="private-device")
        text = "\n".join(lines)
        self.assertNotIn("private-key", text)
        self.assertNotIn("Private instruction", text)
        self.assertNotIn("private-device", text)
        self.assertNotIn("evil.example", text)
        event = json.loads(lines[1][len(PREFIX):])
        self.assertEqual(event["data"]["version"], revision_id("Private instruction"))

    def test_display_sink_failure_does_not_break_runtime(self):
        def broken(_):
            raise RuntimeError("display failed")
        UiEventEmitter(write=broken).emit("chat", state="ready")

    def test_history_filter_excludes_other_sessions(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = MessageHistory(directory), MessageHistory(directory)
            first.add("reward", "sent", answer="First")
            second.add("reward", "sent", answer="Second")
            rows = first.page(session=first.session)["rows"]
            self.assertEqual([row["answer"] for row in rows], ["First"])
            self.assertEqual(len(first.page()["rows"]), 2)

    def test_ui_preferences_are_allowlisted_and_corruption_is_recoverable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_ui_state(root, {"theme": "light", "api_key": "private-key", "prompt": "private"})
            self.assertEqual(load_ui_state(root)["theme"], "light")
            self.assertNotIn("private", (root / "ui-state.json").read_text())
            (root / "ui-state.json").write_text("[]")
            self.assertEqual(load_ui_state(root)["theme"], "dark")


if __name__ == "__main__":
    unittest.main()
