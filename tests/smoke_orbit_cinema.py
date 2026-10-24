"""Interrupted motion must stay continuous, responsive and exact at rest."""
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt, QSize, QAbstractAnimation, QPointF, QEvent, QCoreApplication
from PySide6.QtGui import QImage, QResizeEvent, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget, QMessageBox, QVBoxLayout

import gui
from studio_artwork import PageTransition
from studio_motion import SpringMotion
from ui_widgets import Disclosure, MasterDetail, StudioButton


def wait_for(predicate, message, limit=1400):
    for _ in range(limit // 20):
        if predicate():
            return
        QTest.qWait(20)
    assert predicate(), message


def stopped(motion):
    return motion.state() == QAbstractAnimation.Stopped


def main():
    app = QApplication([])
    app.setStyle("Fusion")
    app.setProperty("animations", True)
    host = QWidget()
    values = []
    spring = SpringMotion(host, 0., values.append)
    spring.to(1.)
    spring.setCurrentTime(80)
    before, velocity = spring.value(), spring._velocity[:]
    assert 0 < before < 1 and velocity[0] > 0
    spring.to(0.)
    assert spring.value() == before and spring._velocity == velocity, "Reversing must retain position and velocity"
    for time in range(96, 900, 16):
        if stopped(spring):
            break
        spring.setCurrentTime(time)
    assert stopped(spring) and spring.value() == 0.
    assert all(0 <= value <= 1 for value in values)
    app.setProperty("animations", False)
    spring.to(.75)
    assert stopped(spring) and spring.value() == .75
    panes = MasterDetail(QWidget(), QWidget())
    panes.resize(400, 300)
    app.sendEvent(panes, QResizeEvent(QSize(400, 300), QSize(840, 460)))
    assert not panes._small, "A snapshot must not reparent inactive panes"
    panes.show()
    app.processEvents()
    assert panes._small and panes.first.isVisible()
    panes.hide()
    panes.resize(1000, 300)
    app.sendEvent(panes, QResizeEvent(QSize(1000, 300), QSize(400, 300)))
    assert panes._small
    panes.show()
    app.processEvents()
    assert not panes._small and panes.first.isVisible() and panes.detail.isVisible()
    panes.close()
    # Close and delete during pending capture and while filters are installed.
    # No deferred frame may run against a destroyed native window.
    app.setProperty("animations", True)
    for attempt in range(20):
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.addWidget(StudioButton("Fixture action"))
        host.resize(320, 220)
        host.show()
        app.processEvents()
        transition = PageTransition(host)
        transition.start(host.grab())
        if attempt % 2:
            app.processEvents()
        host.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
    with tempfile.TemporaryDirectory() as directory:
        with patch.object(gui, "data_dir", return_value=Path(directory)), patch.object(gui, "available_screen_size", return_value=QSize(1920, 1080)), patch("urllib.request.urlopen", side_effect=AssertionError("No API calls in UI fixtures")):
            window = gui.MainWindow()
            window.show()
            window.animations_control.setChecked(False)
            for theme in ("dark", "light"):
                window.theme_control.setCurrentIndex(window.theme_control.findData(theme))
                for size in (QSize(1240, 860), QSize(800, 480)):
                    window.resize(size)
                    window._navigate(gui.PAGE_ACTIVITY)
                    app.processEvents()
                    window.animations_control.setChecked(True)
                    window._navigate(gui.PAGE_HISTORY)
                    wait_for(window._transition.isVisible, f"Page transition never started: {theme}, {size}")
                    wait_for(lambda: not window._transition._capture_pending, "Page capture did not settle")
                    assert window._transition._layers, "Native cards must participate in the entrance"
                    QTest.qWait(96)
                    current = window._transition.snapshot().toImage().convertToFormat(QImage.Format_RGBA8888)
                    window._navigate(gui.PAGE_VIEWERS)
                    assert window._transition._old.toImage().convertToFormat(QImage.Format_RGBA8888) == current, "Rapid navigation must start from the displayed frame"
                    wait_for(window._transition.isVisible, "Replacement transition never started")
                    # Clicking native content immediately ends the visual layer,
                    # then performs the original action exactly once.
                    editor = window.profiles_editor
                    if editor.empty_add.isVisible():
                        QTest.mouseClick(editor.empty_add, Qt.LeftButton)
                        assert not window._transition.isVisible() and len(editor.rows) == 1
                        window._revert_section(gui.PAGE_VIEWERS)
                    wait_for(lambda: not window._transition.isVisible(), "Page did not settle")
                    wait_for(lambda: not window.orbit_header.caption_transition.isVisible(), "Caption did not settle")
                    caption = window.orbit_header
                    assert caption.title.isVisible() and caption.description.isVisible()
                    assert caption.eyebrow.isVisible() == (caption.width() >= 900)
                    assert all(not child.sizePolicy().retainSizeWhenHidden() for child in (caption.title, caption.description, caption.eyebrow)), "Temporary caption hiding must not alter the compact layout"
                    assert window._transition._old.isNull() and not window._transition._input_widgets
                    wait_for(lambda: stopped(window.navigation_surface.motion), "Rail indicator did not settle")
                    nav = window.nav_buttons[gui.PAGE_VIEWERS]
                    assert window.navigation_surface._indicator.toRect().center() == nav.geometry().center()
                    # A held press deforms smoothly; release returns exactly to
                    # the native hit rectangle, including after reversals.
                    button = window.start_button
                    QTest.mousePress(button, Qt.LeftButton)
                    QTest.qWait(65)
                    assert 0 < button._press < 1
                    QTest.mouseRelease(button, Qt.LeftButton, pos=button.rect().topLeft() - button.rect().bottomRight())
                    wait_for(lambda: stopped(button._press_motion), "Button release did not settle")
                    assert button._press == 0 and not window._running_gui
                    window._navigate(gui.PAGE_CONNECTION)
                    window.settings_tabs.setCurrentIndex(2)
                    wait_for(lambda: not window._transition.isVisible(), "Settings did not settle")
                    disclosure = window.settings_tabs.widget(2).findChildren(Disclosure)[0]
                    disclosure.button.setChecked(True)
                    QTest.qWait(64)
                    height = disclosure._reveal_motion.value()
                    disclosure.button.setChecked(False)
                    assert disclosure._reveal_motion.value() == height, "Disclosure jumped on reversal"
                    disclosure.button.setChecked(True)
                    window.resize(window.width() - 24, window.height())
                    app.processEvents()
                    wait_for(lambda: stopped(disclosure._reveal_motion), "Resized disclosure did not settle")
                    assert disclosure.content.isVisible() and disclosure.reveal.maximumHeight() == 16777215
                    disclosure.button.setChecked(False)
                    window._navigate(gui.PAGE_TESTING)
                    window.testing_page.mode.setCurrentIndex(1)
                    window.testing_page.mode.setCurrentIndex(0)
                    window.animations_control.setChecked(False)
                    app.processEvents()
                    assert all(not transition.isVisible() for transition in window.findChildren(PageTransition))
                    assert all(stopped(motion) for motion in window.findChildren(SpringMotion))
                    assert window.testing_page.modes.currentIndex() == 0
                    assert window.orbit_header._parallax == QPointF()
            # Resizing during a caption reveal must retain the compact header's
            # intended visibility, rather than restoring a formerly wide label.
            window.resize(1240, 860)
            window.animations_control.setChecked(True)
            window._navigate(gui.PAGE_HISTORY)
            wait_for(window.orbit_header.caption_transition.isVisible, "Header transition never started")
            window.resize(800, 480)
            app.processEvents()
            assert window.orbit_header.title.isVisible() and window.orbit_header.description.isVisible()
            assert not window.orbit_header.eyebrow.isVisible()
            # Theme overlays retain native fields and leave no retained image.
            window.animations_control.setChecked(True)
            window.theme_control.setCurrentIndex(window.theme_control.findData("dark"))
            wait_for(window._theme_transition.isVisible, "Theme transition never started")
            window.theme_control.setCurrentIndex(window.theme_control.findData("light"))
            wait_for(window._theme_transition.isVisible, "Replacement theme transition never started")
            wait_for(lambda: not window._theme_transition.isVisible(), "Theme did not settle")
            assert window._theme_transition._old.isNull()
            assert app.property("studioTheme") == "light"
            with patch.object(gui, "russian_question", return_value=QMessageBox.Discard):
                window.close()
    print("Orbit cinema: continuous springs, interrupted card entrances, immediate native input, exact resting geometry, button response, disclosure resizing, both layouts/themes and reduced motion passed.")


if __name__ == "__main__":
    main()
