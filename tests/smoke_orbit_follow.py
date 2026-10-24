"""Disclosure visibility, interruption-safe tabs and asynchronous row selection."""
import os
from pathlib import Path
import sys
import tempfile
from threading import Event
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt, QSize, QPoint, QPointF, QAbstractAnimation
from PySide6.QtGui import QImage, QPixmap, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QVBoxLayout, QStackedWidget
import gui
from message_history import MessageHistory
from studio_controls import OrbitScrollArea, StudioTabs
from studio_artwork import PageTransition
from studio_surfaces import GlowSurface
from ui_widgets import Disclosure, card, label


def wait_for(predicate, message, timeout=2500):
    for _ in range(timeout // 20):
        if predicate():
            return
        QTest.qWait(20)
    assert predicate(), message


def stopped(motion):
    return motion.state() == QAbstractAnimation.Stopped


def area_of(widget):
    parent = widget.parentWidget()
    while parent is not None and not isinstance(parent, OrbitScrollArea):
        parent = parent.parentWidget()
    assert parent is not None
    return parent


def wheel(area):
    target = area.viewport()
    point = QPoint(10, 10)
    event = QWheelEvent(QPointF(point), QPointF(target.mapToGlobal(point)), QPoint(), QPoint(0, 120), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(target, event)


def main():
    app = QApplication([])
    app.setStyle("Fusion")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / ".env").write_text("TWITCH_CHANNEL=fixture_channel\nTWITCH_BOT_NAME=fixture_bot\nTWITCH_CLIENT_ID=fixture_client\nAI_API_KEY=fixture_key\n", encoding="utf-8")
        store = MessageHistory(root)
        ids = [store.add("reward", "sent", viewer="fixture", question=f"Question {i}", answer=f"Answer {i}", sent_text=f"Answer {i}") for i in range(4)]
        with patch.object(gui, "data_dir", return_value=root), patch.object(gui, "available_screen_size", return_value=QSize(1920, 1080)), patch("urllib.request.urlopen", side_effect=AssertionError("No APIs in UI fixtures")):
            window = gui.MainWindow()
            window.show()
            for theme in ("dark", "light"):
                window.animations_control.setChecked(False)
                window.theme_control.setCurrentIndex(window.theme_control.findData(theme))
                for size in (QSize(1240, 860), QSize(800, 480)):
                    window.resize(size)
                    window._navigate(gui.PAGE_ACTIVITY)
                    app.processEvents()
                    targets = [row for row in window.dashboard.findChildren(Disclosure) if row.title in ("Состояние подключений", "Настройки сессии")]
                    targets.append(window.testing_page.catalog_disclosure)
                    for disclosure in targets:
                        window.animations_control.setChecked(False)
                        window._navigate(gui.PAGE_TESTING if disclosure is window.testing_page.catalog_disclosure else gui.PAGE_ACTIVITY)
                        disclosure.button.setChecked(False)
                        app.processEvents()
                        area = area_of(disclosure)
                        bar = area.verticalScrollBar()
                        top = disclosure.button.mapTo(area.widget(), QPoint()).y()
                        bar.setValue(max(0, top + disclosure.button.height() - area.viewport().height() + 8))
                        app.processEvents()
                        before = bar.value()
                        window.animations_control.setChecked(True)
                        QTest.mouseClick(disclosure.button, Qt.LeftButton)
                        QTest.qWait(80)
                        first = disclosure.content.mapTo(area.viewport(), QPoint()).y()
                        assert first < area.viewport().height() - 40, (disclosure.title, size, first, area.viewport().height())
                        assert bar.value() >= before, "Opening jumped backwards"
                        wait_for(lambda: stopped(disclosure._reveal_motion) and not disclosure._visibility_pending, "Disclosure did not settle")
                        top = disclosure.button.mapTo(area.viewport(), QPoint()).y()
                        first = disclosure.content.mapTo(area.viewport(), QPoint()).y()
                        assert top >= -2 and first <= area.viewport().height() - 40, (disclosure.title, size, top, first)
                        if disclosure is window.testing_page.catalog_disclosure and area.viewport().height() >= 340:
                            model_top = window.testing_page.models.mapTo(area.viewport(), QPoint()).y()
                            assert model_top < area.viewport().height() - 40, "Comparison models are still below the viewport"
                        QTest.mouseClick(disclosure.button, Qt.LeftButton)
                        wait_for(lambda: stopped(disclosure._reveal_motion) and not disclosure._visibility_pending, "Collapse did not settle")
                        assert not disclosure.reveal.isVisible()
                        top = disclosure.button.mapTo(area.viewport(), QPoint()).y()
                        assert -2 <= top < area.viewport().height(), "Closing lost the disclosure heading"
                        QTest.mouseClick(disclosure.button, Qt.LeftButton)
                        QTest.qWait(50)
                        wheel(area)
                        assert area._reveal_owner is None, "Auto-follow ignored manual scrolling"
                        window.animations_control.setChecked(False)
                        disclosure.button.setChecked(False)
            window.resize(1240, 860)
            window._navigate(gui.PAGE_HISTORY)
            page = window.history_page
            wait_for(lambda: page._detail_id == ids[-1], "History fixture did not load")
            window.animations_control.setChecked(True)
            old = page.answer.toPlainText()
            entered, release = Event(), Event()
            real_detail = MessageHistory.detail
            def slow_detail(store, **kwargs):
                entered.set()
                assert release.wait(3)
                return real_detail(store, **kwargs)
            with patch.object(MessageHistory, "detail", slow_detail):
                page.entries.setCurrentRow(1)
                wait_for(entered.is_set, "Detail read did not start")
                assert page.answer.toPlainText() == old, "Selecting a row cleared the previous answer"
                assert all(not button.isEnabled() for button in page.copy_buttons)
                release.set()
                wait_for(lambda: page._detail_id == ids[-2], "New detail did not arrive")
            assert page.answer.toPlainText() == "Answer 2"
            assert all(button.isEnabled() for button in page.copy_buttons)
            assert page._detail_transition._motion.duration() == window._transition._motion.duration()
            selection = page.entries.itemDelegate().selection
            page.entries.setCurrentRow(2)
            QTest.qWait(45)
            assert selection.motion.state() == QAbstractAnimation.Running
            item, position = page.entries.currentItem(), selection.motion.value()
            page._events.put(("page", page._tokens["page"], {"rows": page._rows[:], "more": page._more}, ""))
            page._drain()
            assert page.entries.currentItem() is item, "Unchanged history refresh rebuilt the rows"
            assert selection.motion.value() == position and not stopped(selection.motion), "Refresh interrupted the selection spring"
            page.entries.setCurrentRow(3)
            wait_for(lambda: page._detail_id == ids[0] and stopped(selection.motion), "Rapid row navigation did not settle")
            assert page.answer.toPlainText() == "Answer 0"
            wait_for(lambda: not page._detail_transition.isVisible(), "Detail transition did not settle")
            window.close()
    # The outgoing frame must be captured before native QTabWidget changes it.
    tabs = StudioTabs()
    tabs.resize(520, 360)
    for caption in ("First", "Second"):
        surface = GlowSurface()
        layout = QVBoxLayout(surface)
        panel, body = card("Identical chrome")
        body.addWidget(label(caption))
        layout.addWidget(panel)
        tabs.addTab(surface, caption)
    tabs.show()
    app.processEvents()
    app.setProperty("animations", True)
    stack = tabs.findChild(QStackedWidget)
    for iteration in range(12):
        before = stack.grab().toImage().convertToFormat(QImage.Format_RGBA8888)
        tabs.setCurrentIndex(1 - tabs.currentIndex())
        transition = tabs._page_transition
        assert transition.isVisible() and transition._capture_pending, "The pending capture exposed the destination"
        assert transition._old.toImage().convertToFormat(QImage.Format_RGBA8888) == before, "Captured an already switched tab"
        QTest.qWait(45)
    wait_for(lambda: not transition.isVisible(), "Rapid tabs did not settle")
    assert transition._old.isNull() and not transition._input_widgets
    assert all(surface.testAttribute(Qt.WA_OpaquePaintEvent) for surface in tabs.findChildren(GlowSurface))
    # Outside the narrow seam, pixels must belong to one frame, rather than
    # superimposed outgoing/incoming labels across the whole content area.
    transition.finish()
    transition = PageTransition(tabs, kind="tabs")
    transition.setGeometry(tabs.rect())
    transition._old, transition._new = QPixmap(tabs.size()), QPixmap(tabs.size())
    transition._old.fill(Qt.red)
    transition._new.fill(Qt.green)
    transition.show()
    ratio = tabs.devicePixelRatioF()
    for direction in (-1, 1):
        transition.direction = direction
        for step in range(21):
            transition._advance(step / 20)
            picture = transition.grab().toImage()
            mixed = sum(picture.pixelColor(round(x * ratio), round(50 * ratio)).red() > 0 and
                        picture.pixelColor(round(x * ratio), round(50 * ratio)).green() > 0
                        for x in range(tabs.width()))
            assert mixed <= 26, "Inner tabs superimposed both pages outside the soft seam"
            assert all(sum(picture.pixelColor(round(x * ratio), round(50 * ratio)).getRgb()[:2]) >= 253
                       for x in range(tabs.width())), "The replacement seam darkened opaque captures"
    transition.finish()
    tabs.close()
    print("Orbit follow: visible disclosure opening, anchored closing, manual takeover, retained async history details, travelling row selection and atomic rapid tab captures passed.")


if __name__ == "__main__":
    main()
