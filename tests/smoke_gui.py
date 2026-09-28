"""Exercise the settings form without Twitch or AI network calls."""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox

import gui
from start import read_config
from profiles import load_profiles


def main() -> None:
    app = QApplication([])
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        with patch.object(gui, "data_dir", return_value=root):
            window = gui.MainWindow()
        assert window.api_key.echoMode() == QLineEdit.Password
        assert window.model.count() == 3
        assert window.model.isEditable()
        assert window.prompt.toPlainText() == ""
        window.channel.setText("https://www.twitch.tv/Streamer")
        window.bot_name.setText("HelperBot")
        window.client_id.setText("client123")
        window.api_key.setText("sk-test")
        window.model.setCurrentText("custom/model")
        window.reward_title.setText("Ask AI")
        window.fallback_models.setText("backup/model")
        window.prompt.setPlainText("Новый промпт")
        assert window._save()
        values = read_config(root / ".env")
        assert values["AI_MODEL"] == "custom/model"
        assert values["TWITCH_REWARD_TITLE"] == "Ask AI"
        assert values["AI_FALLBACK_MODELS"] == "backup/model"
        assert values["AI_API_KEY"] == "sk-test"
        assert window.api_key.text() == ""
        assert (root / "prompt.txt").read_text(encoding="utf-8").strip() == "Новый промпт"
        window._log_path = root / "bot-session.log"
        window._log_path.write_text("GUI_LOG_TEST\n", encoding="utf-8")
        window._poll_log()
        assert "GUI_LOG_TEST" in window.log.toPlainText()
        window.reset_prompt.click()
        assert window._save()
        assert not (root / "prompt.txt").read_text(encoding="utf-8").strip()
        window._kill_timer.start(3000)
        window._on_finished(0, window.process.ExitStatus.NormalExit)
        assert not window._kill_timer.isActive()
        window._set_running(True)
        assert not window.reset_prompt.isEnabled()
        window.close()
    # Drafts and profiles can be saved before API credentials are entered.
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        with patch.object(gui, "data_dir", return_value=root):
            window = gui.MainWindow()
        window.show()
        app.processEvents()
        assert window.pages.count() == 4
        for index, button in enumerate(window.nav_buttons):
            button.click()
            assert window.pages.currentIndex() == index
        window._navigate(2)
        editor = window.profiles_editor
        assert editor.stack.currentIndex() == 0
        editor.add_button.click()
        editor.login.setText("@Viewer")
        editor.user_id.setText("123")
        editor.prompt.setPlainText("Личная инструкция")
        assert window._dirty
        assert window._save()
        assert load_profiles(root / "profiles.json")[0]["login"] == "viewer"
        assert not window._dirty
        # Starting a draft points to the missing connection fields, without launching.
        with patch.object(window.process, "start") as start:
            window._start()
            start.assert_not_called()
        assert window.pages.currentIndex() == 0
        assert not window.notice.isHidden()
        window._navigate(2)
        editor.add_profile()
        editor.user_id.setText("456")
        editor.prompt.setPlainText("Другой зритель")
        editor.enabled.setFocus()
        QTest.keyClick(editor.enabled, Qt.Key_Space)
        assert not editor.enabled.isChecked()
        editor.list.setCurrentRow(0)
        assert editor.prompt.toPlainText() == "Личная инструкция"
        editor.search.setText("456")
        assert editor.list.item(0).isHidden()
        assert not editor.list.item(1).isHidden()
        editor.search.clear()
        editor.list.setCurrentRow(1)
        editor.remove_button.click()
        assert len(editor.rows) == 1
        editor.undo_button.click()
        assert len(editor.rows) == 2
        assert not editor.rows[1]["enabled"]
        assert window._save()
        assert len(load_profiles(root / "profiles.json")) == 2
        # Duplicates must not overwrite the last valid saved data.
        before = (root / "profiles.json").read_bytes()
        editor.user_id.setText("123")
        assert not window._save()
        assert (root / "profiles.json").read_bytes() == before
        editor.user_id.setText("456")
        assert window._save()
        window._set_running(True)
        assert not editor.add_button.isEnabled()
        assert not editor.prompt.isEnabled()
        window._set_running(False)
        window._append_log("Вопрос от viewer принят (в очереди: 1)")
        window._append_log("Ответ отправлен для viewer")
        assert window.questions_label.text() == "1"
        assert window.answers_label.text() == "1"
        window.resize(1000, 720)
        app.processEvents()
        assert window.width() == 1000
        assert window.start_button.geometry().right() <= window.start_button.parentWidget().width()
        window.close()
        with patch.object(gui, "data_dir", return_value=root):
            reopened = gui.MainWindow()
        assert len(reopened.profiles_editor.rows) == 2
        assert not reopened.profiles_editor.rows[1]["enabled"]
        reopened.prompt.setPlainText("Черновик")
        with patch.object(gui.QMessageBox, "question", return_value=QMessageBox.Cancel):
            reopened.show()
            reopened.close()
            assert reopened.isVisible()
        with patch.object(gui.QMessageBox, "question", return_value=QMessageBox.Save):
            reopened.close()
        assert (root / "prompt.txt").read_text(encoding="utf-8").strip() == "Черновик"
        # Corrupt profile files are visible and never silently overwritten.
        (root / "profiles.json").write_text("broken", encoding="utf-8")
        with patch.object(gui, "data_dir", return_value=root):
            broken = gui.MainWindow()
        assert broken.profiles_editor.load_error
        assert not broken._save()
        assert (root / "profiles.json").read_text(encoding="utf-8") == "broken"
        (root / "profiles.json").write_text('{"version":1,"profiles":[]}', encoding="utf-8")
        broken.profiles_editor.retry.click()
        assert not broken.profiles_editor.load_error
        assert broken._save()
        broken.close()
    app.quit()
    print("GUI settings form works.")


if __name__ == "__main__":
    main()
