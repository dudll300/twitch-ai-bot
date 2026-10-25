"""Orbit typography, controls and explicit testing model; fake providers only."""
import json
import os
from pathlib import Path
import sys
import tempfile
from threading import Event
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QFontInfo
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import gui
import testing_gui
from testing import Model, Result
from ui_widgets import StudioButton, ComboItemDelegate


def wait_for(predicate):
    for _ in range(200):
        if predicate():
            return
        QTest.qWait(15)
    assert predicate(), "Timed out waiting for the fixture worker"


def main():
    app = QApplication([])
    app.setStyle("Fusion")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        with patch.object(gui, "data_dir", return_value=root), patch.object(gui, "available_screen_size", return_value=QSize(1920, 1080)), patch("urllib.request.urlopen", side_effect=AssertionError("No network in Orbit fixtures")):
            window = gui.MainWindow()
            window.show()
            window.api_key.setText("fixture-secret-key")
            page = window.testing_page
            window._navigate(gui.PAGE_TESTING)
            app.processEvents()
            assert page.answer_model.isVisible()
            assert not page.catalog_disclosure.button.isChecked()
            assert app.property("studioFontFamily") == "Nunito"
            assert QFontInfo(page.answer_model.font()).family() == "Nunito"
            assert page._test_models() == [window.model.currentText()]
            window.model.setCurrentText("configured/new-model")
            page.refresh_profiles()
            assert "configured/new-model" in page.answer_model.currentText()
            page.catalog = (Model("catalog/first"), Model("catalog/second"), Model("denied", False))
            page._sync_answer_models()
            page.answer_model.setCurrentIndex(page.answer_model.findData("catalog/second"))
            assert page._test_models() == ["catalog/second"]
            page.refresh_profiles()
            assert page._test_models() == ["catalog/second"]
            assert not page.answer_model.model().item(page.answer_model.findData("denied")).isEnabled()
            page.answer_model.setCurrentText("manual/exact-model")
            page.question.setPlainText("Пробный вопрос")
            entered, release = Event(), Event()
            captured = []

            def answer(snapshot, model, **kwargs):
                captured.append((snapshot.models, model))
                entered.set()
                release.wait(3)
                return Result(model, .01, answer="Ответ из фикстуры")

            with patch.object(testing_gui, "test_model", side_effect=answer):
                try:
                    page.start_test()
                    wait_for(entered.is_set)
                    assert not page.answer_model.isEnabled()
                    assert not page.load_models_button.isEnabled()
                finally:
                    release.set()
                wait_for(lambda: not page._testing_busy)
            assert captured == [(("manual/exact-model",), "manual/exact-model")]
            assert "Ответ из фикстуры" in page._result_widgets["manual/exact-model"].toPlainText()
            assert page.answer_model.isEnabled()
            page._add_model(Model("compare/one"), checked=True)
            page._selection_count()
            assert not page.answer_model.isEnabled() and page._test_models() == ["compare/one"]
            assert "compare/one" in page.model_hint.text()
            page.reset_selection()
            assert page.answer_model.isEnabled() and page._test_models() == ["manual/exact-model"]
            page.invalidate_catalog()
            assert page._test_models() == ["manual/exact-model"]
            # Native popup retains keyboard activation and the shared padded delegate.
            combo = page.mode
            assert isinstance(combo.itemDelegate(), ComboItemDelegate)
            combo.showPopup()
            app.processEvents()
            QTest.keyClick(combo.view(), Qt.Key_Down)
            QTest.keyClick(combo.view(), Qt.Key_Return)
            assert combo.currentIndex() == 1
            combo.setCurrentIndex(0)
            window.animations_control.setChecked(False)
            for theme in ("dark", "light"):
                window.theme_control.setCurrentIndex(window.theme_control.findData(theme))
                for size in (QSize(1240, 860), QSize(800, 480)):
                    window.resize(size)
                    for index in range(window.pages.count()):
                        window._navigate(index)
                        app.processEvents()
                        assert window.size() == size, (theme, size, index, window.size())
                        for button in window.findChildren(StudioButton):
                            if not button.isVisible() or not button.text():
                                continue
                            icon = button.iconSize().width() + 9 if not button.icon().isNull() else 0
                            _, height = button._text_layout(button.width() - 34 - icon)
                            assert height <= button.height() - 8, (theme, size, index, button.text(), button.size(), height)
                            assert button.geometry().right() <= button.parentWidget().width(), (index, button.text())
            # Previews do not masquerade as published replies; stale sessions are ignored.
            dashboard = window.dashboard
            dashboard.session = "fixture"
            dashboard._events.put(("fixture", [{"status":"preview", "viewer":"viewer", "sent_text":"", "answer":"Preview", "reason":""}], ""))
            dashboard._drain()
            assert "Preview" not in dashboard.last_reply.text()
            row = {"status":"sent", "viewer":"viewer", "sent_text":"Published", "answer":"", "reason":""}
            dashboard._events.put(("fixture", [row], ""))
            dashboard._drain()
            assert dashboard.last_reply.text() == "Published"
            dashboard._events.put(("old-session", [dict(row, sent_text="Stale")], ""))
            dashboard._drain()
            assert dashboard.last_reply.text() == "Published"
            window._revert_section(gui.PAGE_CONNECTION)
            window.close()
    print("Orbit works: Nunito, wrapped buttons, keyboard dropdowns, model selection and compact themes.")


if __name__ == "__main__":
    main()
