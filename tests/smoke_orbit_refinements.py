"""Mode transitions, readable filter widths and clipped disclosure artwork."""
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt, QSize, QAbstractAnimation, QPoint
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout
import gui
from studio_artwork import PageTransition
from studio_controls import OrbitTabBar
from studio_surfaces import GlowSurface
from ui_widgets import Disclosure, SegmentedControl, card, label


def wait_for(predicate, message):
    for _ in range(90):
        if predicate():
            return
        QTest.qWait(20)
    assert predicate(), message


def settled(motion):
    return motion.state() == QAbstractAnimation.Stopped


def main():
    app = QApplication([])
    app.setStyle("Fusion")
    # Colored stand-ins make simultaneous old/new caption pixels observable.
    host = QWidget()
    host.setStyleSheet("background: black")
    host.resize(200, 80)
    host.show()
    transition = PageTransition(host, kind="caption")
    transition.setGeometry(host.rect())
    transition._old = QPixmap(host.size())
    transition._old.fill(Qt.red)
    transition._new = QPixmap(host.size())
    transition._new.fill(Qt.green)
    transition.show()
    for step in range(21):
        transition._advance(step / 20)
        picture = transition.grab().toImage()
        color = picture.pixelColor(round(20 * host.devicePixelRatioF()), round(20 * host.devicePixelRatioF()))
        assert not (color.red() > 2 and color.green() > 2), "Caption generations overlap"
    assert transition._motion.duration() == 220
    transition.finish()
    host.close()
    with tempfile.TemporaryDirectory() as directory:
        with patch.object(gui, "data_dir", return_value=Path(directory)), patch.object(gui, "available_screen_size", return_value=QSize(1920, 1080)), patch("urllib.request.urlopen", side_effect=AssertionError("No API calls in UI fixtures")):
            window = gui.MainWindow()
            window.show()
            window.animations_control.setChecked(False)
            for theme in ("dark", "light"):
                window.theme_control.setCurrentIndex(window.theme_control.findData(theme))
                for size in (QSize(1240, 860), QSize(1000, 720), QSize(800, 480)):
                    window.resize(size)
                    window._navigate(gui.PAGE_HISTORY)
                    app.processEvents()
                    master = window.history_page.master_detail
                    if not master._small:
                        master.splitter.setSizes([80, 900])
                        app.processEvents()
                        for pane in (master.list_scroll, master.detail):
                            origin = pane.mapTo(master, QPoint())
                            assert origin.x() >= 0 and origin.x() + pane.width() <= master.width(), "A pane escaped its viewport"
                    for button, _ in window.history_page.status_chips:
                        assert button.width() >= button.sizeHint().width(), (size, button.text(), button.width(), button.sizeHint())
                        assert button._text_layout(button.width() - 34)[0].lineCount() == 1
                    window._navigate(gui.PAGE_BEHAVIOR)
                    window.behavior_mode.setCurrentIndex(0)
                    app.processEvents()
                    selector = window.pages.currentWidget().findChild(SegmentedControl)
                    assert isinstance(selector.bar, OrbitTabBar)
                    stack = window.behavior_pages
                    before = stack.geometry()
                    window.animations_control.setChecked(True)
                    for mode in (1, 2, 1, 0):
                        QTest.mouseClick(selector.bar, Qt.LeftButton, pos=selector.bar.tabRect(mode).center())
                        assert window.behavior_mode.currentIndex() == mode
                        wait_for(lambda: stack._page_transition.isVisible(), "Behavior transition was cancelled by layout")
                        assert stack.geometry() == before, "Behavior viewport jumped"
                        assert stack._page_transition._motion.duration() == 360
                        wait_for(lambda: not stack._page_transition.isVisible(), "Behavior did not settle")
                        assert selector.bar.currentIndex() == mode
                    window._navigate(gui.PAGE_TESTING)
                    wait_for(lambda: not window._transition.isVisible(), "Testing page did not settle")
                    stack = window.testing_page.modes
                    selector = window.testing_page.findChild(SegmentedControl)
                    before = stack.geometry()
                    for mode in (1, 0):
                        QTest.mouseClick(selector.bar, Qt.LeftButton, pos=selector.bar.tabRect(mode).center())
                        assert window.testing_page.mode.currentIndex() == mode
                        wait_for(lambda: stack._page_transition.isVisible(), "Testing transition was cancelled by footer")
                        assert stack.geometry() == before, "Testing viewport jumped"
                        wait_for(lambda: not stack._page_transition.isVisible(), "Testing did not settle")
                    window.animations_control.setChecked(False)
            window._navigate(gui.PAGE_AUTONOMOUS)
            window.animations_control.setChecked(True)
            app.processEvents()
            window.autonomous_page.advanced_button.setChecked(True)
            assert window.autonomous_page.advanced_disclosure._reveal_motion.state() == QAbstractAnimation.Running
            window.animations_control.setChecked(False)
            window.close()
    # The lower halo of a full-height card must follow its visible reveal,
    # rather than drawing below the still-clipped content.
    surface = GlowSurface()
    layout = QVBoxLayout(surface)
    layout.setContentsMargins(32, 32, 32, 32)
    panel, box = card("Connection fixture")
    box.addWidget(label("Status fixture"))
    panel.setFixedHeight(260)
    disclosure = Disclosure("Connection details", panel)
    layout.addWidget(disclosure)
    layout.addStretch()
    surface.resize(520, 550)
    surface.show()
    app.processEvents()
    app.setProperty("animations", True)
    disclosure.button.setChecked(True)
    disclosure._reveal_motion.pause()
    disclosure._advance_reveal(100.)
    app.processEvents()
    origin = panel.mapTo(surface, QPoint())
    picture = surface.grab().toImage()
    ratio = surface.devicePixelRatioF()
    probe = QPoint(round((origin.x() + panel.width() / 2) * ratio), round((origin.y() + 265) * ratio))
    panel.setObjectName("fixtureWithoutHalo")
    reference = surface.grab().toImage()
    assert picture.pixelColor(probe) == reference.pixelColor(probe), "Halo leaked below a revealing panel"
    disclosure.button.setChecked(False)
    disclosure._reveal_motion.finish()
    assert disclosure.reveal.maximumHeight() == 0 and not disclosure.reveal.property("clipPanelGlow")
    surface.close()
    print("Orbit refinements: captions without overlap, full-width filter labels, shared mode tabs, stable viewports, advanced reveals and clipped card halos passed.")


if __name__ == "__main__":
    main()
