"""Real offline pipes: auth card, UTF-8, EOF, stop/restart and failed launch."""
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt, QSize, QProcess, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
import gui
from runtime_events import UiEventEmitter


def wait_for(predicate, message, timeout=5000):
    for _ in range(timeout // 20):
        if predicate():
            return
        QTest.qWait(20)
    assert predicate(), message


def main():
    app = QApplication([])
    app.setStyle("Fusion")
    fixture = Path(__file__).parent / "fixtures" / "runtime_pipe_worker.py"
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / ".env").write_text("TWITCH_CHANNEL=fixture_channel\nTWITCH_BOT_NAME=fixture_bot\nTWITCH_CLIENT_ID=fixture_client\nAI_API_KEY=fixture_key\n", encoding="utf-8")
        with patch.object(gui, "data_dir", return_value=root), patch.object(gui, "available_screen_size", return_value=QSize(1920, 1080)), patch.object(gui, "russian_question", return_value=QMessageBox.Discard):
            window = gui.MainWindow()
            window.show()
            window.animations_control.setChecked(False)
            real_start = window.process.start
            launches = []
            def start_fixture(program, arguments):
                assert arguments[-1] == "--pipe"
                session = "fixture" + str(len(launches))
                launches.append(session)
                real_start(sys.executable, ["-u", str(fixture), str(window._log_path), session, "interactive"])
            with patch.object(window.process, "start", side_effect=start_fixture):
                window._start()
                wait_for(lambda: window.auth_hint._code == "ABC-123", "Auth did not arrive over the pipe")
                wait_for(lambda: "WRONG" in window.log.toPlainText(), "Fixture console logs did not arrive")
                assert not hasattr(window, "_log_timer"), "Periodic disk polling remains"
                assert window.status.property("state") == "starting"
                assert window._runtime.questions == window._runtime.answers == 0
                assert "WRONG" in window.log.toPlainText()
                assert window.auth_hint.code.text() == "ABC-123", "Legacy console text changed authorization"
                window.auth_hint.copy_button.click()
                assert app.clipboard().text() == "ABC-123"
                with patch.object(QDesktopServices, "openUrl", return_value=True) as open_url:
                    window.auth_hint.open_button.click()
                    open_url.assert_called_once_with(QUrl("https://www.twitch.tv/activate"))
                window._render_runtime()
                assert window.auth_hint.copy_button.text() == "Скопировано"
                for theme in ("dark", "light"):
                    window.theme_control.setCurrentIndex(window.theme_control.findData(theme))
                    window.resize(800, 480)
                    app.processEvents()
                    for control in (window.auth_hint.code, window.auth_hint.copy_button, window.auth_hint.open_button):
                        assert control.geometry().right() < control.parentWidget().width(), "Auth controls overflowed"
                window.process.write(b"continue\n")
                wait_for(lambda: "Разбитая строка: привет 🌌" in window.log.toPlainText(), "UTF-8 chunks were corrupted")
                assert "Служебная строка stderr" in window.log.toPlainText()
                assert "Нет уместного повода" in window.autonomous_page.log.toPlainText()
                assert window.status.property("state") == "running"
                assert window.auth_hint.isHidden() and not window.auth_hint._code
                assert (window._runtime.questions, window._runtime.answers) == (1, 1)
                assert window.questions_label.text() == window.answers_label.text() == "1"
                window.process.write(b"exit\n")
                wait_for(lambda: not window._running_gui, "EOF did not finalize the process")
                assert "Хвост перед завершением" in window.log.toPlainText()
                assert window.status.property("state") == "idle"
                window._start()
                wait_for(lambda: window.auth_hint._code == "ABC-123", "Second session did not start")
                assert window._runtime.session == launches[-1]
                assert window._runtime.questions == window._runtime.answers == 0
                window._restart_pending = True
                window._stop()
                assert window.auth_hint.isHidden() and not window.auth_hint._code
                wait_for(lambda: len(launches) == 3 and window.auth_hint._code == "ABC-123", "Restart failed")
                window._stop()
                wait_for(lambda: not window._running_gui, "Stop did not settle")
                assert window.status.property("state") == "idle" and not window._kill_timer.isActive()
            # Batching must preserve every event while leaving time for Qt frames.
            lines = []
            emitter = UiEventEmitter("batch", lines.append)
            emitter.emit("lifecycle", state="starting")
            for _ in range(400):
                emitter.emit("queue", size=1, phase="queued")
            window._runtime = gui.RuntimeState()
            window._output = gui.ProcessLines()
            with patch.object(window, "_render_runtime", wraps=window._render_runtime) as render:
                window._consume("\n".join(lines) + "\n")
                assert window._runtime.questions == 0, "Pipe parsing blocked the signal handler"
                wait_for(lambda: window._runtime.questions == 400, "Batched events were lost")
                assert 1 < render.call_count < 50, render.call_count
            with patch.object(window.process, "start", side_effect=lambda *_: real_start(sys.executable, ["-u", str(fixture), str(window._log_path), "authfail", "authfail"])):
                window._start()
                wait_for(lambda: window.auth_hint._code == "ABC-123", "Failure fixture did not start")
                window.process.write(b"continue\n")
                wait_for(lambda: not window._running_gui, "Auth failure did not finish")
                assert window.status.text() == "Ошибка авторизации"
                assert window.auth_hint.isVisible() and not window.auth_hint._code
                assert "другой аккаунт" in window.auth_hint.hint.text()
            banner = window.auth_hint
            auth = dict(account="<b>literal account</b>", role="broadcaster", code="EXPIRE", url="https://evil.example", expires_in=60)
            banner.show_auth(auth)
            assert banner.info.textFormat() == Qt.PlainText and not banner.open_button.isEnabled()
            banner._expiry.start(1)
            wait_for(lambda: not banner._code, "Expired code stayed active")
            assert not banner.show_auth(auth), "Unrelated render reactivated an expired code"
            assert not banner.copy_button.isEnabled() and banner.controls.isHidden()
            banner.reset()
            window._consume('AUTO_EVENT {"status": [], "target": 1}\nAFTER_BAD_AUTO\n')
            wait_for(lambda: "AFTER_BAD_AUTO" in window.log.toPlainText(), "Malformed autonomous event stalled the pipe drain")
            # A rejected launch cannot leave an authorization banner or live state.
            with patch.object(window.process, "start", side_effect=lambda *_: real_start(str(root / "missing-program.exe"), [])):
                window._start()
                wait_for(lambda: window.status.text() == "Не удалось запустить", "Failed launch remained in starting")
                assert window.auth_hint.isHidden() and not window._running_gui
            window.close()
    print("Runtime transport: native merged pipes, chunked UTF-8, structured-only state, auth copy/link, both themes, bounded bursts, final tails, stop/restart and failed launch passed.")


if __name__ == "__main__":
    main()
