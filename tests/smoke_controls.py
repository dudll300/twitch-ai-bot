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
from PySide6.QtWidgets import QApplication, QMessageBox, QVBoxLayout, QWidget

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
    # Scrolling outside nested controls must still scroll the page.
    wheel(outer.viewport(), -120)
    assert outer.verticalScrollBar().value() > initial
    outer.close()


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
        page.enabled.setChecked(True)
        page.mode.setCurrentIndex(page.mode.findData("publish"))
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
        quota = root / "autonomous-quota.json"
        quota.write_text('{"times":[123],"last_text":"Previous"}', encoding="utf-8")
        quota_before = quota.read_bytes()
        page.inputs["check_min_seconds"].setValue(20000)  # Invalid draft does not block reset.
        page.reset_button.click()
        assert load_settings(root / "autonomous.json")[0] == AutoSettings()
        assert not page.enabled.isChecked() and page.mode.currentData() == "preview"
        assert all(widget.value() == asdict(AutoSettings())[key] for key, widget in page.inputs.items())
        assert quota.read_bytes() == quota_before
        page.inputs["hourly_limit"].setValue(20)
        with patch("autonomous_gui.save_settings", side_effect=OSError("Disk error")):
            assert not page.reset()
        assert page.inputs["hourly_limit"].value() == 20
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
    app.quit()
    print("Controls work: on-demand catalog, draft preservation, reset and contained scrolling.")


if __name__ == "__main__":
    main()
