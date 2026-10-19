"""Studio workflows and screenshot matrix. No Twitch or provider requests."""
import json
import os
from pathlib import Path
from queue import Queue
import sys
import tempfile
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QSize
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtTest import QTest

import gui
from runtime_events import UiEventEmitter
from gui_theme import apply_theme, PALETTES
from ui_state import load_ui_state


def main():
    def luminance(color):
        channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [n / 12.92 if n <= .04045 else ((n + .055) / 1.055) ** 2.4 for n in channels]
        return sum(weight * channel for weight, channel in zip((.2126, .7152, .0722), linear))
    for theme, colors in PALETTES.items():
        pairs = [(colors[fg], colors[bg]) for fg in ("text", "muted") for bg in ("bg", "surface", "input", "raised")]
        pairs += [(colors["success"], colors["successbg"]), (colors["error"], colors["errorbg"]),
                  (colors["primaryText"], colors["primary"])]
        for foreground, background in pairs:
            levels = sorted((luminance(foreground), luminance(background)))
            assert (levels[1] + .05) / (levels[0] + .05) >= 4.5, (theme, foreground, background)
    app = QApplication([])
    app.setStyle("Fusion")
    for name in ("segoeui.ttf", "seguisb.ttf"):
        font = Path("C:/Windows/Fonts") / name
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))
    app.setFont(QFont("Segoe UI", 10))
    shots = Path("build/windows-studio-review")
    if "--screenshots" in sys.argv:
        shots.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        with patch.object(gui, "data_dir", return_value=root), patch.object(gui, "available_screen_size", return_value=QSize(1920, 1080)), patch("urllib.request.urlopen", side_effect=AssertionError("No API calls in UI fixtures")):
            window = gui.MainWindow()
            window.show()
            QTest.qWait(50)
            assert window.pages.currentIndex() == gui.PAGE_SETUP
            window._setup_move(1)
            assert load_ui_state(root)["setup_step"] == 1
            window._finish_setup()
            assert window.pages.currentIndex() == gui.PAGE_ACTIVITY
            assert load_ui_state(root)["setup_done"]
            window.channel.setText("demo_channel")
            window.bot_name.setText("demo_bot")
            window.client_id.setText("demo_client")
            window.api_key.setText("demo-key")
            window.prompt.setPlainText("Отвечай кратко и по делу.")
            assert window._save()
            window._active_values = dict(window._saved_values)
            window._active_revision = window._startup_revision
            window._set_running(True)
            lines = []
            emitter = UiEventEmitter("studio-fixture", lines.append)
            emitter.emit("lifecycle", state="starting", channel="demo_channel", model=window.model.currentText(), reward="Вопрос ИИ")
            emitter.emit("eventsub", state="ready")
            for line in lines:
                window._append_log(line)
            assert window.status.property("state") != "running"
            emitter.emit("chat", state="ready")
            window._append_log(lines[-1])
            assert window.status.property("state") == "running"
            before_env = (root / ".env").read_bytes()
            before_profiles = (root / "profiles.json").read_bytes()
            window.prompt.setPlainText("Черновик во время эфира")
            assert window.prompt.isEnabled()
            assert window._save_section(gui.PAGE_BEHAVIOR)
            assert (root / ".env").read_bytes() == before_env
            assert (root / "profiles.json").read_bytes() == before_profiles
            assert window._pending_restart
            window.autonomous_page.autonomous_prompt.setPlainText("Самостоятельный черновик")
            with patch.object(gui, "russian_question", return_value=QMessageBox.Cancel) as question:
                window.close()
                assert question.called and window.isVisible()
            window._revert_section(gui.PAGE_AUTONOMOUS)
            assert not window.autonomous_page.dirty
            page = window.testing_page
            old_queue = page._events
            page._testing_busy = True
            page.cancel_operation()
            old_queue.put(("result", None))
            page._poll()
            assert not page._testing_busy and page._events is not old_queue
            builder = window.prompt_builder
            old_queue = builder._events
            builder._busy = True
            builder.cancel_generation()
            old_queue.put((None, "Late result", ""))
            builder._poll()
            assert not builder._busy and builder._events is not old_queue
            emitter.emit("chat", state="disconnected")
            window._append_log(lines[-1])
            assert window.status.property("state") == "error"
            window._on_finished(1, window.process.ExitStatus.CrashExit)
            assert window.status.property("state") == "error"
            # Populated deterministic fixtures make visual review meaningful.
            editor = window.profiles_editor
            editor.add_profile()
            editor.login.setText("demo_viewer")
            editor.prompt.setPlainText("Обращайся дружелюбно, отвечай по существу.")
            assert window._save_section(gui.PAGE_VIEWERS)
            context = window.local_context_page
            context.add_card()
            context.name.setText("Турбокот")
            context.meaning.setPlainText("Шутливое название очень быстрого прохождения игры.")
            assert window._save_section(gui.PAGE_LOCAL_CONTEXT)
            window._active_values = dict(window._saved_values)
            window._active_revision = window._startup_revision
            window._pending_restart = False
            window._set_running(True)
            emitter.emit("chat", state="ready")
            window._append_log(lines[-1])
            emitter.emit("eventsub", state="ready")
            window._append_log(lines[-1])
            emitter.emit("ai", state="success", model=window.model.currentText())
            window._append_log(lines[-1])
            for phase, size in (("queued", 1), ("started", 0), ("finished", 0)):
                emitter.emit("queue", phase=phase, size=size)
                window._append_log(lines[-1])
            emitter.emit("reply", state="sent", record_id="fixture")
            window._append_log(lines[-1])
            window.dashboard.store.session = window._runtime.session
            window.dashboard.store.add("reward", "sent", viewer="demo_viewer", question="Как пройти этот момент?", answer="Попробуй дождаться паузы после атаки и зайти сбоку.", sent_text="@demo_viewer попробуй дождаться паузы после атаки и зайти сбоку.")
            window.dashboard.refresh()
            QTest.qWait(120)
            window.history_page.refresh(reset=True)
            for theme in ("dark", "light"):
                window.theme_control.setCurrentIndex(window.theme_control.findData(theme))
                for size_name, size in (("wide", QSize(1240, 860)), ("compact", QSize(800, 480))):
                    window.resize(size)
                    for index in range(len(gui.PAGES)):
                        window._navigate(index)
                        QTest.qWait(320)
                        app.processEvents()
                        assert window.width() <= size.width(), (theme, size_name, index, window.width())
                        assert window.height() <= size.height(), (theme, size_name, index, window.height())
                        for action in (window.start_button, window.stop_button, window.restart_button):
                            if action.isVisible():
                                assert action.geometry().right() <= action.parentWidget().width()
                        if "--screenshots" in sys.argv:
                            window.grab().save(str(shots / f"{theme}-{size_name}-{index}.png"))
            with patch.object(gui, "russian_question", return_value=QMessageBox.Discard):
                window.close()
            # Closing is nonblocking; allow read-only daemon queries to release
            # their Windows file handles before fixture cleanup.
            QTest.qWait(250)
    app.quit()
    print("Windows studio works: onboarding, active settings, live drafts, close protection, cancellation, themes and compact layout.")


if __name__ == "__main__":
    main()
