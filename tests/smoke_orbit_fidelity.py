"""Regression checks for the reviewed Orbit controls and legacy installations."""
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt, QSize, QPoint, QPointF, QAbstractAnimation
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication, QAbstractScrollArea, QComboBox, QSpinBox,
                              QTabBar, QVBoxLayout, QWidget)
import gui
from configuration import env_content
from studio_controls import NumberInput, OrbitScrollArea, StudioScrollBar, StudioCheckBox
from ui_widgets import StudioButton, ReadingText


def wheel(widget, delta):
    point = QPoint(8, 8)
    event = QWheelEvent(QPointF(point), QPointF(widget.mapToGlobal(point)), QPoint(), QPoint(0, delta),
                        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)


def main():
    app = QApplication([])
    app.setStyle("Fusion")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        settings = env_content(dict(TWITCH_CHANNEL="fixture_channel", TWITCH_BOT_NAME="fixture_bot",
            TWITCH_CLIENT_ID="fixture_client", AI_API_KEY="fixture-secret-key", AI_BASE_URL="https://example.invalid/v1", AI_MODEL="fixture/model"))
        (root / ".env").write_text(settings, encoding="utf-8")
        (root / "ui-state.json").write_text(json.dumps(dict(schema_version=1, setup_done=False)), encoding="utf-8")
        with patch.object(gui, "data_dir", return_value=root), patch.object(gui, "available_screen_size", return_value=QSize(1920, 1080)), patch("urllib.request.urlopen", side_effect=AssertionError("No provider calls in UI fixtures")):
            window = gui.MainWindow()
            window.show()
            QTest.qWait(450)
            assert window.pages.currentIndex() == gui.PAGE_ACTIVITY
            assert window.dashboard.setup_card.isHidden(), "Existing settings must dismiss onboarding"
            assert window._ui_state["setup_done"]
            window.animations_control.setChecked(False)
            nav = window.nav_buttons[gui.PAGE_HISTORY]
            nav.setFocus(Qt.TabFocusReason)
            assert nav._keyboard_focus
            QTest.mouseClick(nav, Qt.LeftButton)
            assert not nav._keyboard_focus
            checked = 0
            for index in range(len(gui.PAGES)):
                window._navigate(index)
                app.processEvents()
                for widget in window.findChildren(QWidget):
                    if not isinstance(widget, (QComboBox, QSpinBox, QTabBar)):
                        continue
                    before = widget.value() if isinstance(widget, QSpinBox) else widget.currentIndex()
                    widget.setFocus()
                    for delta in (120, -120):
                        wheel(widget, delta)
                        after = widget.value() if isinstance(widget, QSpinBox) else widget.currentIndex()
                        assert after == before, (index, type(widget).__name__, before, after)
                    if isinstance(widget, QSpinBox):
                        wheel(widget.lineEdit(), -120)
                        assert widget.value() == before
                    checked += 1
                for area in window.findChildren(QAbstractScrollArea):
                    if area.isVisible():
                        assert isinstance(area.verticalScrollBar(), StudioScrollBar), type(area).__name__
                        assert isinstance(area.horizontalScrollBar(), StudioScrollBar), type(area).__name__
            assert checked >= 40
            assert isinstance(window.safety_page.timeout, NumberInput)
            assert isinstance(window.profiles_editor.reward_blocked, StudioCheckBox)
            assert isinstance(window.history_page.answer, ReadingText)
            # The custom glyphs use the same native hit rectangles as the step controls.
            spin = window.safety_page.timeout
            spin.setValue(10)
            from PySide6.QtWidgets import QStyleOptionSpinBox, QStyle
            option = QStyleOptionSpinBox()
            spin.initStyleOption(option)
            up = spin.style().subControlRect(QStyle.CC_SpinBox, option, QStyle.SC_SpinBoxUp, spin).center()
            down = spin.style().subControlRect(QStyle.CC_SpinBox, option, QStyle.SC_SpinBoxDown, spin).center()
            QTest.mouseClick(spin, Qt.LeftButton, pos=up)
            assert spin.value() == 11
            QTest.mouseClick(spin, Qt.LeftButton, pos=down)
            assert spin.value() == 10
            # New numeric fields must use the same safe shared control.
            plain_spin = NumberInput(window)
            plain_spin.setRange(0, 20)
            plain_spin.setValue(10)
            wheel(plain_spin, 120)
            wheel(plain_spin.lineEdit(), -120)
            assert plain_spin.value() == 10
            plain_spin.deleteLater()
            window.animations_control.setChecked(True)
            window._navigate(gui.PAGE_CONNECTION)
            QTest.qWait(30)
            assert window._transition.isVisible()
            window._navigate(gui.PAGE_HISTORY)
            QTest.qWait(window._transition._motion.duration() + 80)
            assert not window._transition.isVisible()
            window._navigate(gui.PAGE_BEHAVIOR)
            QTest.qWait(30)
            window.animations_control.setChecked(False)
            assert not window._transition.isVisible()
            window.history_page.master_detail.restore_sizes([520, 520])
            for theme in ("dark", "light"):
                window.theme_control.setCurrentIndex(window.theme_control.findData(theme))
                for size in (QSize(1240, 860), QSize(800, 480)):
                    window.resize(size)
                    for index in range(len(gui.PAGES)):
                        window._navigate(index)
                        app.processEvents()
                        assert window.size() == size
                        for button in window.findChildren(StudioButton):
                            if not button.isVisible():
                                continue
                            assert button.geometry().right() <= button.parentWidget().width(), (index, button.text())
                        if index == gui.PAGE_HISTORY and size.width() == 1240:
                            splitter = window.history_page.master_detail.splitter
                            assert all(value >= 400 for value in splitter.sizes()), splitter.sizes()
                        if size.width() == 1240:
                            for view in window._detail_views.values():
                                if not view.isVisible():
                                    continue
                                splitter = view.splitter
                                gap = splitter.widget(1).x() - (splitter.widget(0).x() + splitter.widget(0).width())
                                assert gap >= 20, (index, gap)
            window._revert_section(gui.PAGE_SAFETY)
            window.close()
            assert (root / ".env").read_text(encoding="utf-8") == settings
    # Smooth scrolling has an immediate response, a finite tail and an instant reduced-motion mode.
    content = QWidget()
    layout = QVBoxLayout(content)
    layout.addSpacing(1800)
    area = OrbitScrollArea()
    area.setWidget(content)
    area.resize(320, 220)
    area.show()
    app.processEvents()
    app.setProperty("animations", True)
    wheel(area.viewport(), -120)
    assert 0 < area.verticalScrollBar().value() < 72
    QTest.qWait(440)
    assert area.verticalScrollBar().value() == 72
    app.setProperty("animations", False)
    wheel(area.viewport(), -120)
    assert area._scroll.state() == QAbstractAnimation.State.Stopped
    area.close()
    print("Orbit fidelity: every page, unchanged wheel values, native scrollbars, step buttons, motion and legacy settings passed.")


if __name__ == "__main__":
    main()
