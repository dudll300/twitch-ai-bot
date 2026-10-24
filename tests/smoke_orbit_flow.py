"""Pixel message scrolling, a quiet live horizon and stable disclosure collapse."""
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt, QSize, QPoint, QPointF, QAbstractAnimation, QMimeData
from PySide6.QtGui import QWheelEvent, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QListWidgetItem, QAbstractItemView, QMessageBox
import gui
from studio_artwork import OrbitHeader, PageTransition
from ui_widgets import ScrollListWidget, ScrollPlainTextEdit, Disclosure


def stopped(motion):
    return motion.state() == QAbstractAnimation.Stopped


def wait_for(predicate, message, timeout=2500):
    for _ in range(timeout // 16):
        if predicate():
            return
        QTest.qWait(16)
    assert predicate(), message


def wheel(view, angle=-120, pixels=0, scrollbar=False):
    target = view.verticalScrollBar() if scrollbar else view.viewport()
    point = QPoint(8, 8)
    event = QWheelEvent(QPointF(point), QPointF(target.mapToGlobal(point)), QPoint(0, pixels),
                        QPoint(0, angle), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(target, event)
    assert event.isAccepted()


def check_pixels(app):
    rows = ScrollListWidget()
    rows.resize(420, 240)
    for index in range(40):
        item = QListWidgetItem(f"Message {index}\nA wrapped explanation of the event", rows)
        item.setSizeHint(QSize(300, 140 + (index % 3) * 30))
    rows.show()
    app.processEvents()
    rows.setCurrentRow(0)
    assert rows.verticalScrollMode() == QAbstractItemView.ScrollPerPixel
    bar = rows.verticalScrollBar()
    wheel(rows)
    assert 0 < bar.value() < 5, "A wheel tick jumped to the next message"
    values = []
    for _ in range(38):
        QTest.qWait(16)
        values.append(bar.value())
    assert values == sorted(values) and len(set(values)) > 8, values
    assert bar.value() == 72 and rows.visualItemRect(rows.item(0)).top() == -72
    assert rows.currentRow() == 0, "Wheel input selected a different message"
    wheel(rows)
    QTest.qWait(40)
    before, velocity = bar.value(), rows._wheel_scroll.motion._velocity[:]
    wheel(rows)
    assert bar.value() == before and rows._wheel_scroll.motion._velocity == velocity
    wait_for(lambda: stopped(rows._wheel_scroll.motion), "Repeated ticks never settled")
    assert bar.value() == 216
    wheel(rows, pixels=-13)
    assert bar.value() == 229 and stopped(rows._wheel_scroll.motion)
    wheel(rows, scrollbar=True)
    assert bar.value() < 234
    wait_for(lambda: stopped(rows._wheel_scroll.motion), "Scrollbar wheel did not settle")
    assert bar.value() == 301
    wheel(rows)
    bar.setValue(17)
    QTest.qWait(100)
    assert bar.value() == 17 and stopped(rows._wheel_scroll.motion)
    wheel(rows)
    QTest.keyClick(rows, Qt.Key_Home)
    assert stopped(rows._wheel_scroll.motion), "Keyboard selection left a scroll tail running"
    wheel(rows)
    QTest.mouseClick(rows.viewport(), Qt.LeftButton, pos=QPoint(20, 20))
    assert stopped(rows._wheel_scroll.motion), "Picking a message left it moving under the pointer"
    wheel(rows)
    rows.hide()
    assert stopped(rows._wheel_scroll.motion)
    rows.close()

    text = ScrollPlainTextEdit("<b>Literal text, never HTML</b>\n" + "Long wrapped paragraph. " * 600)
    text.resize(420, 240)
    text.show()
    app.processEvents()
    bar = text.verticalScrollBar()
    assert text.toPlainText().startswith("<b>Literal") and not text.acceptRichText()
    assert bar.maximum() > 1000, "Text is still scrolling in paragraph/block units"
    cursor = QTextCursor(text.document())
    before = text.cursorRect(cursor).top()
    bar.setValue(7)
    assert text.cursorRect(cursor).top() == before - 7, "Text did not move by exactly seven pixels"
    wheel(text)
    wait_for(lambda: stopped(text._wheel_scroll.motion), "Text did not settle")
    assert bar.value() == 79
    wheel(text)
    QTest.keyClick(text, Qt.Key_Left)
    assert stopped(text._wheel_scroll.motion), "Typing/navigation kept the editor drifting"
    # Appending plain log data keeps a reader's position and selection; at the
    # bottom it continues following the tail. No rich-text auto-detection.
    cursor.setPosition(3)
    cursor.setPosition(10, QTextCursor.KeepAnchor)
    text.setTextCursor(cursor)
    bar.setValue(79)
    selected = text.textCursor().selectedText()
    text.appendPlainText("<i>Also literal</i>")
    assert bar.value() == 79 and text.textCursor().selectedText() == selected
    assert text.toPlainText().endswith("<i>Also literal</i>")
    bar.setValue(bar.maximum())
    text.appendPlainText("A new final line")
    assert bar.value() == bar.maximum()
    mime = QMimeData()
    mime.setHtml("<b>Formatted</b>")
    mime.setText("Plain paste")
    text.moveCursor(QTextCursor.End)
    text.insertFromMimeData(mime)
    assert text.toPlainText().endswith("Plain paste")
    text.document().setMaximumBlockCount(3)
    for index in range(5):
        text.appendPlainText(f"Line {index}")
    assert text.document().blockCount() == 3 and text.toPlainText() == "Line 2\nLine 3\nLine 4"
    text.close()


def check_window(app):
    with tempfile.TemporaryDirectory() as directory:
        with patch.object(gui, "data_dir", return_value=Path(directory)), patch.object(gui, "available_screen_size", return_value=QSize(1920, 1080)), patch.object(gui, "russian_question", return_value=QMessageBox.Discard), patch("urllib.request.urlopen", side_effect=AssertionError("No API calls in motion fixtures")):
            window = gui.MainWindow()
            window.show()
            header = window.orbit_header
            for theme in ("dark", "light"):
                window.animations_control.setChecked(False)
                window.theme_control.setCurrentIndex(window.theme_control.findData(theme))
                for size in (QSize(1240, 860), QSize(800, 480)):
                    window.resize(size)
                    window._navigate(gui.PAGE_BEHAVIOR)
                    window.behavior_mode.setCurrentIndex(1)
                    app.processEvents()
                    targets = [d for d in window.prompt_builder.findChildren(Disclosure) if d.title == "Модель генерации и подключение"]
                    targets += window.dashboard.findChildren(Disclosure)[:2]
                    targets += [window.fallback_disclosure, window.testing_page.catalog_disclosure]
                    for disclosure in targets:
                        window.animations_control.setChecked(False)
                        index = (gui.PAGE_BEHAVIOR if window.prompt_builder.isAncestorOf(disclosure) else
                                 gui.PAGE_CONNECTION if disclosure is window.fallback_disclosure else
                                 gui.PAGE_TESTING if disclosure is window.testing_page.catalog_disclosure else gui.PAGE_ACTIVITY)
                        window._navigate(index)
                        disclosure.button.setChecked(True)
                        app.processEvents()
                        original_limits = disclosure.content.minimumHeight(), disclosure.content.maximumHeight()
                        height = disclosure.content.height()
                        window.animations_control.setChecked(True)
                        disclosure.button.setChecked(False)
                        samples = []
                        for time in range(16, 736, 16):
                            if stopped(disclosure._reveal_motion):
                                break
                            disclosure._reveal_motion.setCurrentTime(time)
                            app.processEvents()
                            samples.append(disclosure.reveal.maximumHeight())
                            if disclosure.content.isVisible():
                                assert disclosure.content.y() == 8
                                assert disclosure.content.height() == height, "Closing reflowed the child layout"
                        wait_for(lambda: stopped(disclosure._reveal_motion), "Collapse did not settle")
                        assert samples == sorted(samples, reverse=True), (disclosure.title, samples)
                        assert not disclosure.reveal.isVisible() and disclosure.reveal.maximumHeight() == 0
                        assert (disclosure.content.minimumHeight(), disclosure.content.maximumHeight()) == original_limits
                    for view in (window.dashboard.conversation, window.history_page.entries, window.safety_page.decisions,
                                 window.profiles_editor.list, window.testing_page.models):
                        assert view.verticalScrollMode() == QAbstractItemView.ScrollPerPixel
                    # Programmatic/keyboard reveals must also cancel an old
                    # composited frame; otherwise live content moves behind it.
                    window.animations_control.setChecked(False)
                    window._navigate(gui.PAGE_ACTIVITY)
                    app.processEvents()
                    window.animations_control.setChecked(True)
                    window._navigate(gui.PAGE_BEHAVIOR)
                    target = targets[0]
                    target.button.setChecked(True)
                    assert not window._transition.isVisible()
                    wait_for(lambda: stopped(target._reveal_motion), "Opening did not settle")
            before = header._star_phase
            QTest.qWait(100)
            assert header._star_phase > before and header._twinkle_timer.isActive()
            window.animations_control.setChecked(False)
            frozen = header._star_phase
            QTest.qWait(80)
            assert header._star_phase == frozen and not header._twinkle_timer.isActive()
            window.animations_control.setChecked(True)
            window.hide()
            assert not header._twinkle_timer.isActive()
            window.show()
            assert header._twinkle_timer.isActive()
            window.close()
            QTest.qWait(300)


def main():
    app = QApplication([])
    app.setStyle("Fusion")
    app.setProperty("animations", True)
    check_pixels(app)
    for second in range(180):
        before, after = OrbitHeader._orbit_pose(second), OrbitHeader._orbit_pose(second + 1 / 60)
        assert abs(after[0] - before[0]) < .02 and abs(after[1] - before[1]) < .02
        assert abs(before[0]) <= 4 and abs(before[1]) <= 2.5 and abs(before[2]) <= .65
    assert OrbitHeader._orbit_pose(0) != OrbitHeader._orbit_pose(5)
    check_window(app)
    print("Orbit flow: continuous pixel lists/text, plain-text logs, quiet live orbit, stable collapse and both themes/layouts passed.")


if __name__ == "__main__":
    main()
