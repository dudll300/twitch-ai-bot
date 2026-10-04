"""On-demand catalogs, autonomous reset and nested wheel handling; no network."""

import io
import json
import os
import sys
import tempfile
import time
import urllib.error
from dataclasses import asdict
from pathlib import Path
from threading import Event, get_ident
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QPlainTextEdit, QScrollArea, QVBoxLayout, QWidget

import gui
import model_catalog_gui
import testing
from autonomous import AutoSettings, load_settings
from configuration import env_content
from ui_widgets import NoWheelComboBox, ScrollListWidget, ScrollPlainTextEdit, scroll_page


def wait_for(predicate):
    deadline = time.monotonic() + 4
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(15)
    assert predicate(), "Timed out waiting for GUI worker"


def response(data):
    return io.BytesIO(json.dumps(data).encode())


def wheel(widget, delta):
    pos = QPoint(8, 8)
    event = QWheelEvent(QPointF(pos), QPointF(widget.mapToGlobal(pos)), QPoint(), QPoint(0, delta),
                        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)
    return event.isAccepted()


def check_scrolling(app):
    content = QWidget()
    layout = QVBoxLayout(content)
    items = ScrollListWidget()
    items.addItems([f"Item {i}" for i in range(100)])
    items.setFixedHeight(130)
    text = ScrollPlainTextEdit("\n".join(f"Line {i}" for i in range(100)))
    text.setFixedHeight(130)
    combo = NoWheelComboBox()
    combo.addItems([f"Model {i}" for i in range(100)])
    layout.addWidget(items)
    layout.addWidget(text)
    layout.addWidget(combo)
    form_text = ScrollPlainTextEdit()
    form_text.setFixedHeight(130)
    layout.addWidget(form_text)
    layout.addSpacing(1500)
    outer = scroll_page(content)
    outer.resize(450, 450)
    outer.show()
    app.processEvents()
    outer.verticalScrollBar().setValue(30)
    initial = outer.verticalScrollBar().value()
    for nested in (items, text):
        bar = nested.verticalScrollBar()
        assert bar.maximum() > 0
        for value, delta in ((bar.maximum(), -120), (0, 120)):
            bar.setValue(value)
            assert wheel(nested.viewport(), delta), "Boundary event leaked out of the viewport"
            assert wheel(bar, delta), "Boundary event leaked out of the scrollbar"
            assert outer.verticalScrollBar().value() == initial
        bar.setValue(0)
        wheel(nested.viewport(), -120)
        assert bar.value() > 0, "The nested list must still scroll normally"
        assert outer.verticalScrollBar().value() == initial
    combo.showPopup()
    app.processEvents()
    view = combo.view()
    assert view.verticalScrollBar().maximum() > 0
    for value, delta in ((view.verticalScrollBar().maximum(), -120), (0, 120)):
        view.verticalScrollBar().setValue(value)
        assert wheel(view.viewport(), delta)
        assert outer.verticalScrollBar().value() == initial
    combo.hidePopup()
    # Empty/short text forwards to the page, including when focused or read-only.
    for contents, readonly in (("", False), ("Short draft", False), ("Short result", True)):
        form_text.setReadOnly(readonly)
        form_text.setPlainText(contents)
        form_text.setFocus()
        app.processEvents()
        assert form_text.verticalScrollBar().maximum() == 0
        outer.verticalScrollBar().setValue(30)
        assert wheel(form_text.viewport(), -120)
        assert outer.verticalScrollBar().value() > 30
        assert wheel(form_text.viewport(), 120)
        assert outer.verticalScrollBar().value() == 30
    # A short field becomes a contained scroller as text grows, then forwards again.
    form_text.setPlainText("\n".join(f"Draft {i}" for i in range(100)))
    app.processEvents()
    bar = form_text.verticalScrollBar()
    assert bar.maximum() > 0
    outer.verticalScrollBar().setValue(30)
    for position, delta in ((0, -120), (bar.maximum(), -120), (0, 120)):
        bar.setValue(position)
        assert wheel(form_text.viewport(), delta)
        assert outer.verticalScrollBar().value() == 30
    bar.setValue(0)
    wheel(form_text.viewport(), -120)
    assert bar.value() > 0
    form_text.clear()
    app.processEvents()
    assert form_text.verticalScrollBar().maximum() == 0
    wheel(form_text.viewport(), -120)
    assert outer.verticalScrollBar().value() > 30
    outer.verticalScrollBar().setValue(initial)
    # Scrolling outside nested controls must still scroll the page.
    wheel(outer.viewport(), -120)
    assert outer.verticalScrollBar().value() > initial
    outer.close()


def check_all_page_text_fields(app):
    """Exercise actual editors, read-only journals and a generated test result."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / ".env").write_text(env_content({"AI_API_KEY": "scroll-test-key"}), encoding="utf-8")
        with patch.object(gui, "data_dir", return_value=root):
            window = gui.MainWindow()
        window.show()
        window.profiles_editor.add_button.click()
        window.profiles_editor.login.setText("viewer")
        window.prompt_builder.show_preset()
        page = window.testing_page
        page.question.setPlainText("Вопрос для проверки прокрутки")
        page._add_model(testing.Model("scroll/model"), checked=True)
        with patch.object(testing.urllib.request, "urlopen", return_value=response(
                {"choices": [{"message": {"content": "Короткий ответ"}}]})):
            page.run_button.click()
            wait_for(lambda: not page._testing_busy)
        checked, expanded = 0, set()
        for index in range(window.pages.count()):
            window._navigate(index)
            app.processEvents()
            for editor in window.pages.widget(index).findChildren(QPlainTextEdit):
                assert isinstance(editor, ScrollPlainTextEdit), "A page uses an inconsistent text control"
                outer = editor.parentWidget()
                while outer is not None and not isinstance(outer, QScrollArea):
                    outer = outer.parentWidget()
                assert outer is not None, "Text fields must have a scrollable enclosing page"
                if id(outer) not in expanded:
                    # Force vertical overflow independently of the page's layout:
                    # an activity page's QHBoxLayout adds horizontal spacing.
                    outer.widget().setMinimumHeight(outer.viewport().height() + 1200)
                    expanded.add(id(outer))
                editor.setFixedHeight(130)
                editor.setPlainText("Short text")
                editor.setFocus()
                wait_for(lambda: outer.verticalScrollBar().maximum() > 100)
                assert editor.verticalScrollBar().maximum() == 0
                outer.verticalScrollBar().setValue(30)
                wheel(editor.viewport(), -120)
                assert outer.verticalScrollBar().value() > 30, (index, editor.accessibleName(),
                                                               outer.verticalScrollBar().maximum())
                editor.setPlainText("\n".join(f"Long line {i}" for i in range(100)))
                app.processEvents()
                inner = editor.verticalScrollBar()
                assert inner.maximum() > 0
                outer.verticalScrollBar().setValue(30)
                inner.setValue(0)
                wheel(editor.viewport(), -120)
                assert inner.value() > 0 and outer.verticalScrollBar().value() == 30
                inner.setValue(inner.maximum())
                wheel(editor.viewport(), -120)
                assert outer.verticalScrollBar().value() == 30
                editor.clear()
                app.processEvents()
                wheel(editor.viewport(), -120)
                assert outer.verticalScrollBar().value() > 30
                checked += 1
        assert checked >= 9, "Some prompt, profile, question, result or journal fields were missed"
        with patch.object(gui, "russian_question", return_value=QMessageBox.Discard):
            window.close()


def main():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(gui.STYLE)
    main_thread = get_ident()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / ".env").write_text(env_content({"AI_API_KEY": "saved-secret", "AI_MODEL": "draft/model"}), encoding="utf-8")
        with patch.object(gui, "data_dir", return_value=root), patch.object(
                testing.urllib.request, "urlopen") as network:
            window = gui.MainWindow()
            window.show()
            app.processEvents()
            for index in range(window.pages.count()):
                window._navigate(index)
            network.assert_not_called()
        window._navigate(0)
        control = window.model_catalog
        before = (root / ".env").read_bytes()
        def catalog(request, timeout):
            assert get_ident() != main_thread
            assert request.full_url == "https://ai.starimg.ru/v1/models"
            assert request.get_header("Authorization") == "Bearer saved-secret"
            assert timeout == 20
            return response({"data": [{"id": "available/model"}, {"id": "denied/model", "available": False}]})
        with patch.object(testing.urllib.request, "urlopen", side_effect=catalog) as network:
            control.refresh_button.click()
            assert control._busy and not control.refresh_button.isEnabled()
            control.refresh()  # Programmatic double click also stays harmless.
            wait_for(lambda: not control._busy)
            assert network.call_count == 1
        assert window.model.currentText() == "draft/model"
        assert not window._dirty
        assert (root / ".env").read_bytes() == before
        assert window.model.count() == 2
        denied = window.model.findData("denied/model")
        assert "недоступна" in window.model.itemText(denied)
        assert not window.model.model().item(denied).isEnabled()
        window.model.setCurrentIndex(window.model.findData("available/model"))
        assert window._fields()["AI_MODEL"] == "available/model"
        window.model.setEditText("manual/model")
        error = urllib.error.HTTPError("https://ai.starimg.ru/v1/models", 401, "error", {},
                                       response({"error": {"message": "Invalid saved-secret"}}))
        with patch.object(testing.urllib.request, "urlopen", side_effect=error):
            control.refresh_button.click()
            wait_for(lambda: not control._busy)
        assert "HTTP 401" in control.status.text()
        assert "saved-secret" not in control.status.text()
        assert window.model.currentText() == "manual/model"
        assert window.model.isEnabled() and window.model.isEditable()
        # A delayed response for the previous key cannot replace a current catalog.
        started, release, finished = Event(), Event(), Event()
        def stale_catalog(request, timeout):
            started.set()
            try:
                assert release.wait(4)
                return response({"data": [{"id": "old-key-only"}]})
            finally:
                finished.set()
        with patch.object(testing.urllib.request, "urlopen", side_effect=stale_catalog):
            control.refresh_button.click()
            wait_for(started.is_set)
            window.api_key.setText("new-secret")
            release.set()
            wait_for(lambda: not control._busy)
        assert window.model.findData("old-key-only") == -1
        assert window.model.currentText() == "manual/model"
        page = window.autonomous_page
        defaults = AutoSettings()
        assert page.autonomous_prompt.toPlainText() == defaults.autonomous_prompt
        assert page.autonomous_prompt.minimumHeight() >= 260
        page.autonomous_prompt.setPlainText("Поддерживай один разговор и поздравляй с победой без обязательной шутки.")
        prompt_draft = page.autonomous_prompt.toPlainText()
        page.inputs["pause_seconds"].setValue(7)
        assert page.participation.currentData() == "balanced"
        page.participation.setCurrentIndex(page.participation.findData("active"))
        live_saved = load_settings(root / "autonomous.json")[0]
        assert live_saved.participation == "active"
        assert live_saved.autonomous_prompt == defaults.autonomous_prompt
        assert live_saved.pause_seconds == defaults.pause_seconds
        page.enabled.setChecked(True)
        page.mode.setCurrentIndex(page.mode.findData("publish"))
        assert page.autonomous_prompt.toPlainText() == prompt_draft
        assert page.inputs["pause_seconds"].value() == 7
        assert "изменены" in page.hint.text()
        # Returning standard instructions is a draft edit, with no file write.
        previous_auto = (root / "autonomous.json").read_bytes()
        page.reset_prompt_button.click()
        assert page.autonomous_prompt.toPlainText() == defaults.autonomous_prompt
        assert page.inputs["pause_seconds"].value() == 7
        assert (root / "autonomous.json").read_bytes() == previous_auto
        page.autonomous_prompt.setPlainText(prompt_draft)
        changes = {"pause_seconds": 0, "hourly_limit": 50, "context_count": 500,
                   "freshness_seconds": 1000, "active_seconds": 900,
                   "check_min_seconds": 0, "check_max_seconds": 10000,
                   "min_messages": 101, "min_authors": 11, "max_chars": 1000}
        for key, value in changes.items():
            page.inputs[key].setValue(value)
            assert page.inputs[key].value() == value
        assert page.apply()
        saved = load_settings(root / "autonomous.json")[0]
        assert all(getattr(saved, key) == value for key, value in changes.items())
        assert saved.autonomous_prompt == prompt_draft
        assert "сохранены" in page.hint.text()
        # A failed apply preserves both kinds of draft and the previous file.
        previous_auto = (root / "autonomous.json").read_bytes()
        failed_draft = "Не смешивай соседние разговоры. Реагируй по существу."
        page.autonomous_prompt.setPlainText(failed_draft)
        page.inputs["pause_seconds"].setValue(11)
        with patch("autonomous_gui.save_settings", side_effect=OSError("Disk error")):
            assert not page.apply()
        assert page.autonomous_prompt.toPlainText() == failed_draft
        assert page.inputs["pause_seconds"].value() == 11
        assert (root / "autonomous.json").read_bytes() == previous_auto
        with patch("autonomous_gui.save_settings", side_effect=OSError("Disk error")):
            page.mode.setCurrentIndex(page.mode.findData("preview"))
        assert page.mode.currentData() == "publish"
        assert page.autonomous_prompt.toPlainText() == failed_draft
        assert page.inputs["pause_seconds"].value() == 11
        assert (root / "autonomous.json").read_bytes() == previous_auto
        # A malformed prompt cannot be saved, and the complete draft stays editable.
        page.autonomous_prompt.setPlainText("x" * 10001)
        assert not page.apply()
        assert len(page.autonomous_prompt.toPlainText()) == 10001
        assert (root / "autonomous.json").read_bytes() == previous_auto
        page.autonomous_prompt.clear()
        assert page.apply()
        assert load_settings(root / "autonomous.json")[0].autonomous_prompt == ""
        page.autonomous_prompt.setPlainText(failed_draft)
        quota = root / "autonomous-quota.json"
        quota.write_text('{"times":[123],"last_text":"Previous"}', encoding="utf-8")
        quota_before = quota.read_bytes()
        page.inputs["check_min_seconds"].setValue(20000)  # Invalid draft does not block reset.
        page.reset_button.click()
        assert load_settings(root / "autonomous.json")[0] == AutoSettings()
        assert not page.enabled.isChecked() and page.mode.currentData() == "preview"
        assert page.participation.currentData() == "balanced"
        assert all(widget.value() == asdict(AutoSettings())[key] for key, widget in page.inputs.items())
        assert page.autonomous_prompt.toPlainText() == defaults.autonomous_prompt
        assert quota.read_bytes() == quota_before
        page.inputs["hourly_limit"].setValue(20)
        page.autonomous_prompt.setPlainText(failed_draft)
        with patch("autonomous_gui.save_settings", side_effect=OSError("Disk error")):
            assert not page.reset()
        assert page.inputs["hourly_limit"].value() == 20
        assert page.autonomous_prompt.toPlainText() == failed_draft
        assert not page.error.isHidden()
        # Close during a request: the thread only finishes with plain data, no Qt calls.
        started.clear()
        release.clear()
        finished.clear()
        with patch.object(testing.urllib.request, "urlopen", side_effect=stale_catalog), patch.object(
                gui, "russian_question", return_value=QMessageBox.Discard):
            control.refresh_button.click()
            wait_for(started.is_set)
            window.close()
            assert control._closed and not control._timer.isActive()
            release.set()
            wait_for(finished.is_set)
        app.processEvents()
        assert control._events.empty()
    check_scrolling(app)
    check_all_page_text_fields(app)
    app.quit()
    print("Controls work: on-demand catalog, draft preservation, reset and contained scrolling.")


if __name__ == "__main__":
    main()
