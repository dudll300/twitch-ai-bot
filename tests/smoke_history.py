"""History navigation, filters, pagination, disclosure and safe async teardown."""

import os
from pathlib import Path
import sys
import tempfile
from threading import Event
import time
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import gui
from message_history import MessageHistory


def wait_for(predicate, message, seconds=5):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(20)
    assert predicate(), message


def select(page, record_id):
    for index in range(page.entries.count()):
        if page.entries.item(index).data(Qt.UserRole) == record_id:
            page.entries.setCurrentRow(index)
            wait_for(lambda: page._detail_id == record_id, "Details did not load")
            return
    raise AssertionError("Record missing from list")


def window_at(root):
    with patch.object(gui, "data_dir", return_value=root), \
         patch.object(gui, "available_screen_size", return_value=QSize(1920, 1080)):
        window = gui.MainWindow()
    window.resize(1400, 940)
    window.show()
    window._navigate(7)
    return window


def main():
    app = QApplication([])
    app.setStyle("Fusion")
    for filename in ("segoeui.ttf", "seguisb.ttf"):
        font_path = Path("C:/Windows/Fonts") / filename
        if font_path.exists():
            QFontDatabase.addApplicationFont(str(font_path))
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(gui.STYLE)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        store = MessageHistory(root, secrets=("private-key",))
        for index in range(51):
            entry = store.add("reward", "generated", question=f"Вопрос зрителя {index}",
                              viewer="viewer", channel="channel", model="provider/model", answer=f"Ответ {index}")
            store.update(entry, "sent", sent_text=f"@viewer Ответ {index}")
        now = time.time()
        preview = store.add("autonomous", "generated", channel="channel", model="provider/model",
            viewer="viewer", action="reply", reason="reaction", answer="@viewer Поздравляю с победой!", context={
                "conversation": [{"author": "viewer", "text": "Наконец прошёл сложного босса!", "time": now, "sequence": 1},
                                 {"author": "another", "text": "Поздравляю, отличная попытка.", "time": now, "sequence": 2}],
                "basis": [1], "intent": "Коротко поздравить с победой",
                "request_messages": [{"role": "system", "content": "Отвечай дружелюбно. private-key"}]})
        store.update(preview, "preview")
        silent = store.add("autonomous", "silent", action="silent", reason="no_reason")
        window = window_at(root)
        page = window.history_page
        assert window.nav_buttons[7].text() == "История"
        wait_for(lambda: not page._loading and page._detail_id == silent, "Initial history did not load")
        assert page.entries.count() == 50
        assert page.context_card.isHidden()
        assert page.answer.toPlainText() == "Бот решил промолчать."
        select(page, preview)
        assert not page.context_card.isHidden()
        assert page.context.isHidden()
        assert page.instructions.isHidden()
        page.context_button.click()
        assert not page.context.isHidden()
        assert "Наконец прошёл" in page.context.toPlainText()
        assert "Цель реплики" in page.context.toPlainText()
        page.instructions_button.click()
        assert "private-key" not in page.instructions.toPlainText()
        assert "ключ скрыт" in page.instructions.toPlainText()
        page.context_button.click()
        assert page.context.isHidden()

        page.older.click()
        wait_for(lambda: not page._loading and len(page._cursors) == 2 and page.entries.count() == 3, "Older page missing")
        assert not page.older.isEnabled()
        page.newer.click()
        wait_for(lambda: not page._loading and len(page._cursors) == 1 and page.entries.count() == 50, "Newer page missing")
        page.search.setText("ВОПРОС ЗРИТЕЛЯ 50")
        wait_for(lambda: not page._loading and page.entries.count() == 1 and "50" in page.question.toPlainText(), "Russian search failed")
        page.search.clear()
        page.kind.setCurrentIndex(2)
        wait_for(lambda: not page._loading and page.entries.count() == 2, "Kind filter failed")
        page.status.setCurrentIndex(4)
        wait_for(lambda: not page._loading and page.entries.count() == 1 and page._detail_id == silent, "Silent filter failed")
        assert page.context_card.isHidden()
        assert not (root / ".env").exists()
        assert not (root / ".twitch_token.json").exists()
        window.close()
        assert page._closed.is_set()

        # Opening history again must not require Twitch or AI credentials.
        reopened = window_at(root)
        page = reopened.history_page
        wait_for(lambda: not page._loading and page.entries.count() == 50, "Reopened history missing")
        select(page, preview)
        page.context_button.click()
        QTest.qWait(80)
        assert page.timeline.geometry().top() > page.answer.geometry().bottom(), "Details overlap"
        assert page.timeline.geometry().bottom() < page.timeline.parentWidget().height(), "Details clipped"
        if "--screenshot" in sys.argv:
            destination = Path(__file__).resolve().parents[1] / "build" / "history-preview.png"
            destination.parent.mkdir(parents=True, exist_ok=True)
            assert reopened.grab().save(str(destination))

        entered, release, finished = Event(), Event(), Event()
        real_page = MessageHistory.page
        def slow_read(store, **kwargs):
            entered.set()
            try:
                release.wait(3)
                return real_page(store, **kwargs)
            finally:
                finished.set()
        with patch.object(MessageHistory, "page", slow_read):
            page.refresh()
            wait_for(entered.is_set, "Reader did not enter background query")
            start = time.monotonic()
            reopened.close()
            assert time.monotonic() - start < .5
            assert page._closed.is_set()
            release.set()
            wait_for(finished.is_set, "Reader failed to shut down")
        app.processEvents()

    # Empty browsing must not create a database or settings files.
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        window = window_at(root)
        page = window.history_page
        wait_for(lambda: not page._loading, "Empty history query did not complete")
        assert page.entries.count() == 0
        assert not page.store.path.exists()
        window.close()
    print("History GUI works: persistence, filters, pagination, collapsed context, redaction and async teardown.")


if __name__ == "__main__":
    main()
