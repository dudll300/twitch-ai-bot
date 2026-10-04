"""Exercise the settings form without Twitch or AI network calls."""

import os
import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt, QSize, QPoint, QPointF
from PySide6.QtGui import QFont, QFontDatabase, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox

import gui
from start import read_config
from profiles import load_profiles
from autonomous import load_settings as load_auto_settings
from ui_widgets import russian_question
from prompt_builder import compose_prompt


def wait_for(predicate, message, seconds=3):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(20)
    assert predicate(), message


def main() -> None:
    app = QApplication([])
    app.setStyle("Fusion")
    for font_file in ("segoeui.ttf", "seguisb.ttf"):
        font_path = Path("C:/Windows/Fonts") / font_file
        if font_path.exists():
            QFontDatabase.addApplicationFont(str(font_path))
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(gui.STYLE)
    screen_patch = patch.object(gui, "available_screen_size", return_value=QSize(1920, 1080))
    screen_patch.start()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        with patch.object(gui, "data_dir", return_value=root):
            window = gui.MainWindow()
        assert window.api_key.echoMode() == QLineEdit.Password
        assert window.model.count() == 4
        assert window.model.isEditable()
        assert not window.windowIcon().isNull()
        assert window.prompt.toPlainText() == ""
        assert window.fallback_models.text() == "deepseek-v4-pro,deepseek-v4-flash"
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
        assert window.pages.count() == 7
        auto_page = window.autonomous_page
        standard_instructions = auto_page.saved.autonomous_prompt
        assert auto_page.autonomous_prompt.toPlainText() == standard_instructions
        auto_page.autonomous_prompt.setPlainText("Сначала пойми повод. Не смешивай темы. Шутка необязательна.")
        instruction_draft = auto_page.autonomous_prompt.toPlainText()
        window._navigate(1)
        window._navigate(3)
        assert auto_page.autonomous_prompt.toPlainText() == instruction_draft
        assert auto_page.advanced_panel.isHidden()
        assert not auto_page.advanced_button.isChecked()
        auto_page.advanced_button.click()
        assert not auto_page.advanced_panel.isHidden()
        auto_page.inputs["context_count"].setValue(31)
        auto_page.advanced_button.click()
        assert auto_page.advanced_panel.isHidden()
        auto_page.advanced_button.click()
        assert auto_page.inputs["context_count"].value() == 31
        assert auto_page.autonomous_prompt.toPlainText() == instruction_draft
        auto_page.inputs["context_count"].setValue(20)
        auto_page.advanced_button.click()
        # Wheel events must not edit values, even when controls have focus.
        for widget in (auto_page.mode, auto_page.participation, window.model, *auto_page.inputs.values()):
            before_value = widget.value() if hasattr(widget, "value") else widget.currentText()
            widget.setFocus()
            for delta in (-120, 120):
                event = QWheelEvent(QPointF(5, 5), QPointF(widget.mapToGlobal(QPoint(5, 5))),
                                    QPoint(), QPoint(0, delta), Qt.NoButton, Qt.NoModifier,
                                    Qt.NoScrollPhase, False)
                QApplication.sendEvent(widget, event)
                after_value = widget.value() if hasattr(widget, "value") else widget.currentText()
                assert after_value == before_value
            if hasattr(widget, "value"):
                before_value = widget.value()
                QTest.keyClick(widget, Qt.Key_Up)
                assert widget.value() == min(before_value + 1, widget.maximum())
                widget.setValue(before_value)
        assert not (root / "autonomous.json").exists()
        assert not auto_page.enabled.isChecked()
        assert auto_page.mode.currentData() == "preview"
        assert auto_page.inputs["hourly_limit"].value() == 8
        assert auto_page.inputs["hourly_limit"].maximum() > 8
        window._set_running(True)
        assert auto_page.enabled.isEnabled()
        auto_page.enabled.setChecked(True)
        assert load_auto_settings(root / "autonomous.json")[0].enabled
        assert load_auto_settings(root / "autonomous.json")[0].autonomous_prompt == standard_instructions
        assert auto_page.autonomous_prompt.toPlainText() == instruction_draft
        auto_page.mode.setCurrentIndex(1)
        assert load_auto_settings(root / "autonomous.json")[0].mode == "publish"
        auto_page.inputs["context_count"].setValue(30)
        auto_page.inputs["hourly_limit"].setValue(6)
        assert auto_page.apply()
        assert load_auto_settings(root / "autonomous.json")[0].context_count == 30
        assert load_auto_settings(root / "autonomous.json")[0].hourly_limit == 6
        assert load_auto_settings(root / "autonomous.json")[0].autonomous_prompt == instruction_draft
        auto_page.inputs["check_min_seconds"].setValue(500)
        auto_page.inputs["check_max_seconds"].setValue(20)
        assert not auto_page.apply()
        # Disabling is never blocked by invalid numeric edits.
        auto_page.enabled.setChecked(False)
        assert not load_auto_settings(root / "autonomous.json")[0].enabled
        window._append_log("AUTO_EVENT " + json.dumps({"time":"12:00:00", "status":"preview", "action":"joke", "text":"Тестовая шутка"}))
        assert "Тестовая шутка" in auto_page.log.toPlainText()
        assert "AUTO_EVENT" not in window.log.toPlainText()
        window._set_running(False)
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
        editor.aliases.setText("Ваня, вьювер")
        assert window._dirty
        assert window._save()
        assert load_profiles(root / "profiles.json")[0]["login"] == "viewer"
        assert load_profiles(root / "profiles.json")[0]["aliases"] == ["Ваня", "вьювер"]
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
        editor.aliases.setText("Другой")
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
        assert editor.rows[1]["aliases"] == ["Другой"]
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
        assert not editor.aliases.isEnabled()
        window._set_running(False)
        window._append_log("Вопрос от viewer принят (в очереди: 1)")
        window._append_log("Ответ отправлен для viewer")
        assert window.questions_label.text() == "1"
        assert window.answers_label.text() == "1"
        window.resize(1000, 720)
        app.processEvents()
        assert window.width() >= 1200
        assert window.height() >= 820
        assert window.start_button.geometry().right() <= window.start_button.parentWidget().width()
        # The menu can be reversed mid-animation and restored at the minimum width.
        window.menu_button.click()
        wait_for(lambda: window.sidebar.width() == 0, "Sidebar did not finish collapsing")
        assert window.sidebar.width() == 0
        assert window.status.isVisible()
        assert window.menu_button.accessibleName() == "Показать меню"
        window.resize(900, 700)
        assert window.width() >= 1000
        window.menu_button.click()
        wait_for(lambda: window.sidebar.width() == 240, "Sidebar did not finish expanding")
        assert window.sidebar.width() == 240
        assert window.width() >= 1200
        window.menu_button.click()
        QTest.qWait(50)
        window.menu_button.click()
        wait_for(lambda: window.sidebar.width() == 240, "Sidebar did not recover after reversing animation")
        assert window.sidebar.width() == 240
        assert window.profiles_editor.remove_button.property("variant") == "danger"
        window._navigate(1)
        wait_for(lambda: window._page_effect.opacity() == 1.0, "Page transition did not finish")
        assert window._page_effect.opacity() == 1.0
        assert not any("Настроить" in button.text() for button in window.pages.widget(1).findChildren(gui.QPushButton))
        window.close()
        with patch.object(gui, "data_dir", return_value=root):
            reopened = gui.MainWindow()
        assert len(reopened.profiles_editor.rows) == 2
        assert reopened.autonomous_page.inputs["context_count"].value() == 30
        assert reopened.autonomous_page.inputs["hourly_limit"].value() == 6
        assert reopened.autonomous_page.mode.currentData() == "publish"
        assert reopened.autonomous_page.autonomous_prompt.toPlainText() == instruction_draft
        assert not reopened.autonomous_page.enabled.isChecked()
        assert not reopened.profiles_editor.rows[1]["enabled"]
        assert reopened.profiles_editor.rows[0]["aliases"] == ["Ваня", "вьювер"]
        reopened.prompt.setPlainText("Черновик")
        with patch.object(gui, "russian_question", return_value=QMessageBox.Cancel):
            reopened.show()
            reopened.close()
            assert reopened.isVisible()
        with patch.object(gui, "russian_question", return_value=QMessageBox.Save):
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
    # A recognized old generated policy becomes a visible, unsaved draft.
    # Opening the form or testing it must not rewrite the saved prompt.
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        original = compose_prompt("Мой стиль, 300 попыток", "Настольные игры").replace(
            "максимум 400 символов,", "максимум 300 символов,")
        path = root / "prompt.txt"
        path.write_text(original, encoding="utf-8")
        previous = path.read_bytes()
        with patch.object(gui, "data_dir", return_value=root):
            migrated = gui.MainWindow()
        assert migrated._dirty
        assert "Служебный лимит обновлён до 400" in migrated.save_hint.text()
        assert "максимум 400 символов" in migrated.prompt.toPlainText()
        assert "Мой стиль, 300 попыток" in migrated.prompt.toPlainText()
        assert path.read_bytes() == previous
        migrated.api_key.setText("test-key")
        snapshot = migrated._test_snapshot(["exact/model"], "Вопрос?", "viewer", "", None)
        assert snapshot.messages[0][1] == migrated.prompt.toPlainText()
        assert "ответ — 400" in snapshot.context
        assert path.read_bytes() == previous
        assert migrated.autonomous_page.saved.max_chars == 220
        assert migrated._save()
        assert path.read_text(encoding="utf-8").strip() == migrated.prompt.toPlainText().strip()
        assert not migrated._dirty
        # Pasting an old recognized draft and explicitly saving also aligns
        # the text shown in the editor with the newly written file.
        migrated.prompt.setPlainText(original)
        assert migrated._save()
        assert "максимум 400 символов" in migrated.prompt.toPlainText()
        assert path.read_text(encoding="utf-8").strip() == migrated.prompt.toPlainText().strip()
        migrated.close()
    for buttons, default, expected in (
        (QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save,
         {QMessageBox.Save: "Сохранить", QMessageBox.Discard: "Не сохранять", QMessageBox.Cancel: "Отмена"}),
        (QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
         {QMessageBox.Yes: "Да", QMessageBox.No: "Нет"}),
    ):
        def check_dialog(dialog):
            for standard, caption in expected.items():
                assert dialog.button(standard).text() == caption
            assert dialog.defaultButton() == dialog.button(default)
            return default
        with patch.object(QMessageBox, "exec", check_dialog):
            assert russian_question(None, "Проверка", "Текст", buttons, default) == default
    with tempfile.TemporaryDirectory() as directory, patch.object(gui, "data_dir", return_value=Path(directory)), patch.object(
        gui, "available_screen_size", return_value=QSize(1280, 720)
    ):
        small = gui.MainWindow()
        small.show()
        app.processEvents()
        assert small.height() <= 672
        assert small.width() <= 1256
        small.close()
    screen_patch.stop()
    app.quit()
    print("GUI settings form works.")


if __name__ == "__main__":
    main()
