"""Viewer motion, retained disclosure choices and ordered fallback selection."""
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt, QSize, QPoint, QAbstractAnimation
from PySide6.QtTest import QTest
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication, QMessageBox
import gui
from message_history import MessageHistory
from testing import Model


def wait_for(predicate, message, timeout=3000):
    for _ in range(timeout // 20):
        if predicate():
            return
        QTest.qWait(20)
    assert predicate(), message


def stopped(motion):
    return motion.state() == QAbstractAnimation.Stopped


def select_record(page, record_id):
    for index in range(page.entries.count()):
        if page.entries.item(index).data(Qt.UserRole) == record_id:
            page.entries.setCurrentRow(index)
            wait_for(lambda: page._detail_id == record_id, "Record did not load")
            return
    raise AssertionError("Missing fixture record")


def main():
    app = QApplication([])
    app.setStyle("Fusion")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / ".env").write_text("TWITCH_CHANNEL=fixture_channel\nTWITCH_BOT_NAME=fixture_bot\nTWITCH_CLIENT_ID=fixture_client\nAI_API_KEY=fixture_key\nAI_FALLBACK_MODELS=legacy/custom,deepseek-v4-pro\n", encoding="utf-8")
        profiles = [{"login": f"viewer{i}", "user_id": "", "prompt": f"Instruction {i}", "enabled": True} for i in range(3)]
        (root / "profiles.json").write_text(json.dumps({"version": 1, "profiles": profiles}), encoding="utf-8")
        store = MessageHistory(root)
        context = {"conversation": [{"author": "fixture", "text": "Context fixture", "time": 1}],
                   "request_messages": [{"role": "system", "content": "Instruction fixture"}]}
        first = store.add("autonomous", "preview", answer="First", context=context)
        missing = store.add("autonomous", "silent", action="silent")
        last = store.add("autonomous", "preview", answer="Last", context=context)
        with patch.object(gui, "data_dir", return_value=root), patch.object(gui, "available_screen_size", return_value=QSize(1920, 1080)), patch.object(gui, "russian_question", return_value=QMessageBox.Discard), patch("urllib.request.urlopen", side_effect=AssertionError("No APIs in fixtures")):
            window = gui.MainWindow()
            window.resize(1240, 860)
            window.show()
            window.animations_control.setChecked(False)
            window._navigate(gui.PAGE_VIEWERS)
            app.processEvents()
            page = window.profiles_editor
            initial_profiles = [dict(row) for row in page.rows]
            window.animations_control.setChecked(True)
            page.list.setCurrentRow(1)
            QTest.qWait(30)
            selection = page.list.itemDelegate().selection
            assert not stopped(selection.motion), "Viewer rows lack the history spring"
            assert page._detail_transition.isVisible(), "Viewer details lack the history entrance"
            page.list.setCurrentRow(2)
            wait_for(lambda: stopped(selection.motion) and not page._detail_transition.isVisible(), "Rapid viewer selection did not settle")
            assert page.prompt.toPlainText() == "Instruction 2" and page.rows == initial_profiles
            page.prompt.setFocus()
            page.prompt.moveCursor(QTextCursor.End)
            QTest.keyClicks(page.prompt, " draft")
            assert page.rows[2]["prompt"].endswith(" draft")
            page.list.setCurrentRow(0)
            assert page.prompt.toPlainText() == "Instruction 0"
            page.list.setCurrentRow(2)
            assert page.prompt.toPlainText().endswith(" draft"), "Motion lost a viewer draft"
            window.animations_control.setChecked(False)
            window._navigate(gui.PAGE_HISTORY)
            history = window.history_page
            wait_for(lambda: not history._loading and history._detail_id == last, "History did not load")
            disclosures = (history.context_disclosure, history.instructions_disclosure, history.timeline_disclosure)
            for disclosure in disclosures:
                disclosure.button.setChecked(True)
            window.animations_control.setChecked(True)
            select_record(history, first)
            assert all(disclosure.button.isChecked() for disclosure in disclosures), "Record selection collapsed a section"
            select_record(history, missing)
            assert history.context_card.isHidden()
            assert all(disclosure.button.isChecked() for disclosure in disclosures), "Missing context discarded expansion preferences"
            select_record(history, last)
            assert not history.context_card.isHidden()
            assert all(disclosure.reveal.isVisible() for disclosure in disclosures)
            assert "Instruction fixture" in history.instructions.toPlainText()
            history.instructions_button.setChecked(False)
            select_record(history, first)
            assert not history.instructions_button.isChecked() and history.context_button.isChecked()

            for theme in ("dark", "light"):
                window.animations_control.setChecked(False)
                window.theme_control.setCurrentIndex(window.theme_control.findData(theme))
                for size in (QSize(1240, 860), QSize(800, 480)):
                    window.resize(size)
                    for index, disclosure in ((gui.PAGE_AUTONOMOUS, window.autonomous_page.instructions_disclosure),
                                              (gui.PAGE_CONNECTION, window.fallback_disclosure)):
                        window.animations_control.setChecked(False)
                        window._navigate(index)
                        disclosure.button.setChecked(False)
                        app.processEvents()
                        width = disclosure.width()
                        window.animations_control.setChecked(True)
                        disclosure.button.setChecked(True)
                        for _ in range(38):
                            QTest.qWait(20)
                            assert disclosure.reveal.width() == width, ("Scrollbar appearance rewrapped a reveal", theme, size.toTuple(), index, width, disclosure.reveal.width(), window.size().toTuple())
                            assert disclosure.content.y() == 8, "Revealing content drifted under the clip"
                            if stopped(disclosure._reveal_motion):
                                break
                        wait_for(lambda: stopped(disclosure._reveal_motion), "Reveal did not settle")
                        assert abs(disclosure.reveal.height() - disclosure._reveal_motion.value()) <= 1, ("Natural height jumped after reveal", theme, size.toTuple(), index, disclosure.reveal.height(), disclosure._reveal_motion.value(), disclosure.content.sizeHint().toTuple(), disclosure.content.heightForWidth(disclosure.width()))
                        if index == gui.PAGE_AUTONOMOUS:
                            auto = window.autonomous_page
                            parent = auto.reset_prompt_button.parentWidget()
                            top = auto.reset_prompt_button.mapTo(parent, QPoint()).y()
                            bottom = auto.prompt_rule.mapTo(parent, QPoint()).y() + auto.prompt_rule.height()
                            assert top - bottom >= 16, "Reset instructions still touches the divider"
                        disclosure.button.setChecked(False)
                        wait_for(lambda: stopped(disclosure._reveal_motion), "Closing did not settle")

            window.animations_control.setChecked(False)
            window.resize(1240, 860)
            window._navigate(gui.PAGE_CONNECTION)
            window.fallback_disclosure.button.setChecked(True)
            app.processEvents()
            backup, combo = window.fallback_models, window.fallback_models.combo
            original = backup.text()
            window.model_catalog._replace_models((Model("fast/model"), Model("deepseek-v4-pro"), Model("denied/model", False)))
            assert backup.text() == original and combo.findData("legacy/custom") >= 0
            assert not combo.model().item(combo.findData("denied/model")).isEnabled()
            combo.showPopup()
            app.processEvents()
            index = combo.model().index(combo.findData("fast/model"), 0)
            QTest.mouseClick(combo.view().viewport(), Qt.LeftButton, pos=combo.view().visualRect(index).center())
            assert combo.view().isVisible(), "A checkbox closed the multi-model popup"
            assert backup.text() == original + ",fast/model", "Checking did not append the exact ID once"
            index = combo.model().index(combo.findData("deepseek-v4-pro"), 0)
            combo.view().setCurrentIndex(index)
            QTest.keyClick(combo.view(), Qt.Key_Space)
            assert backup.text() == "legacy/custom,fast/model"
            QTest.keyClick(combo.view(), Qt.Key_Space)
            assert backup.text() == "legacy/custom,fast/model,deepseek-v4-pro", "Rechecking lost priority order"
            QTest.keyClick(combo.view(), Qt.Key_Return)
            assert not combo.view().isVisible()
            backup.manual.setText("two,models")
            backup.add_button.click()
            assert backup.error.isVisible()
            backup.manual.setText("private/custom")
            assert backup.error.isHidden(), "Correcting a manual ID left an empty error block"
            backup.add_button.click()
            assert backup.text().endswith(",private/custom") and combo.findData("private/custom") >= 0
            window.model_catalog.invalidate()
            assert backup.text().endswith(",private/custom"), "API invalidation discarded manual IDs"
            assert window._saved_values["AI_FALLBACK_MODELS"] == original, "Checks applied a draft without saving"
            assert window._save()
            assert window._saved_values["AI_FALLBACK_MODELS"] == backup.text()
            window.close()
    print("Orbit continuity: animated viewers with retained drafts, persistent history sections, stable reveal widths/heights, separated reset action and ordered model checkboxes passed.")


if __name__ == "__main__":
    main()
