"""Native regressions for the final header, halos, focus and scroll movement."""
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt, QSize, QPoint, QPointF, QAbstractAnimation
from PySide6.QtGui import QWheelEvent, QPixmap, QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout
import gui
from studio_artwork import PageTransition
from studio_controls import OrbitScrollArea
from studio_surfaces import GlowSurface
from ui_widgets import card, scroll_page, ScrollPlainTextEdit


def wheel(area, delta=0, pixels=0):
    widget = area.viewport()
    point = QPoint(10, 10)
    event = QWheelEvent(QPointF(point), QPointF(widget.mapToGlobal(point)), QPoint(0, pixels), QPoint(0, delta), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)


def main():
    app = QApplication([])
    app.setStyle("Fusion")
    with tempfile.TemporaryDirectory() as directory:
        with patch.object(gui, "data_dir", return_value=Path(directory)), patch.object(gui, "available_screen_size", return_value=QSize(1920, 1080)), patch("urllib.request.urlopen", side_effect=AssertionError("No API calls in UI fixtures")):
            window = gui.MainWindow()
            window.show()
            window.animations_control.setChecked(False)
            assert window.brand_label.text() == "Twitch AI Bot"
            header = window.orbit_header
            for size in (QSize(1240, 860), QSize(800, 480)):
                window.resize(size)
                for page in range(len(gui.PAGES)):
                    window._navigate(page)
                    app.processEvents()
                    sky = header.grab().toImage()
                    stars = header._stars
                    header._stars = ()
                    empty = header.grab().toImage()
                    header._stars = stars
                    coverage = [0, 0, 0]
                    for y in range(sky.height()):
                        for x in range(sky.width()):
                            if sky.pixel(x, y) != empty.pixel(x, y):
                                coverage[min(2, x * 3 // sky.width())] += 1
                    assert min(coverage) > 20, (page, size, coverage)
                    assert header._stars == stars
            window._navigate(gui.PAGE_AUTONOMOUS)
            toggle = window.autonomous_page.enabled
            app.processEvents()
            toggle.setChecked(True)
            QTest.mouseClick(toggle, Qt.LeftButton)
            assert not toggle.isChecked() and not toggle._keyboard_focus
            clicked = toggle.grab().toImage()
            toggle.clearFocus()
            assert clicked == toggle.grab().toImage(), "Mouse focus must not leave a second outline"
            toggle.setFocus(Qt.TabFocusReason)
            assert toggle._keyboard_focus and clicked != toggle.grab().toImage(), "Keyboard focus must remain visible"
            window._navigate(gui.PAGE_SAFETY)
            app.processEvents()
            combo = window.safety_page.link_mode
            caption = combo.parentWidget().layout().itemAt(0).widget()
            assert combo.y() - caption.geometry().bottom() <= 10, "Fixed-height fields must not stretch their caption gaps"
            window._revert_section(gui.PAGE_AUTONOMOUS)
            window.close()
    content = QWidget()
    content_layout = QVBoxLayout(content)
    editor = ScrollPlainTextEdit("\n".join(f"Line {i}" for i in range(100)))
    editor.setFixedHeight(140)
    content_layout.addWidget(editor)
    content_layout.addSpacing(2000)
    area = OrbitScrollArea()
    area.setWidget(content)
    area.resize(400, 260)
    area.show()
    app.processEvents()
    app.setProperty("animations", True)
    wheel(area, -120)
    assert 0 < area.verticalScrollBar().value() <= 2, "Wheel input must not jump a fifth of the distance"
    QTest.qWait(48)
    before = area.verticalScrollBar().value()
    wheel(area, -240)
    assert area.verticalScrollBar().value() == before, "Repeated wheel input must retain continuous motion"
    values = [before]
    for _ in range(30):
        QTest.qWait(16)
        values.append(area.verticalScrollBar().value())
    assert values == sorted(values) and values[-1] == 216
    assert area._scroll.state() == QAbstractAnimation.Stopped
    wheel(area, 1200)
    QTest.qWait(48)
    assert 0 < area.verticalScrollBar().value() < 216
    QTest.qWait(450)
    assert area.verticalScrollBar().value() == 0
    wheel(area, 120)
    assert area._scroll.state() == QAbstractAnimation.Stopped
    area.verticalScrollBar().setValue(100)
    wheel(area, pixels=-13)
    assert area.verticalScrollBar().value() == 113 and area._scroll.state() == QAbstractAnimation.Stopped
    wheel(area, -120)
    area.verticalScrollBar().setValue(50)
    QTest.qWait(100)
    assert area.verticalScrollBar().value() == 50 and area._scroll.state() == QAbstractAnimation.Stopped
    wheel(area, -120)
    QTest.qWait(32)
    page_value = area.verticalScrollBar().value()
    wheel(editor, -120)
    QTest.qWait(160)
    assert editor.verticalScrollBar().value() > 0
    assert area.verticalScrollBar().value() == page_value, "Inner input must stop the outer scroll tail"
    area.close()
    # A same-color transition must not darken at the midpoint.
    host = QWidget()
    host.resize(320, 220)
    host.show()
    transition = PageTransition(host)
    frame = QPixmap(host.size())
    frame.fill(QColor(70, 100, 130))
    transition._old = transition._opaque(frame)
    transition._new = transition._opaque(frame)
    transition.setGeometry(host.rect())
    transition._advance(.5)
    transition.show()
    color = transition.grab().toImage().pixelColor(150, 100)
    assert max(abs(a - b) for a, b in zip(color.getRgb()[:3], (70, 100, 130))) <= 1, color.name()
    transition.finish()
    host.close()
    # A scrolled card may not leave a halo above its viewport.
    surface = GlowSurface()
    layout = QVBoxLayout(surface)
    layout.setContentsMargins(32, 32, 32, 32)
    body = QWidget()
    body_layout = QVBoxLayout(body)
    body_layout.setContentsMargins(0, 0, 0, 0)
    panel, _ = card()
    panel.setFixedHeight(400)
    body_layout.addWidget(panel)
    body_layout.addSpacing(1000)
    nested = scroll_page(body, ambient=False)
    layout.addWidget(nested)
    surface.resize(420, 300)
    surface.show()
    app.processEvents()
    nested.verticalScrollBar().setValue(80)
    app.processEvents()
    image = surface.grab().toImage()
    origin = nested.viewport().mapTo(surface, QPoint())
    assert image.pixelColor(origin.x() - 6, origin.y() - 6) == image.pixelColor(1, 1), "Halo leaked above clipped content"
    surface.close()
    print("Orbit motion: full-width stars on every tab, product name, mouse/keyboard focus, continuous wheel motion, trackpads, opaque transitions and clipped halos passed.")


if __name__ == "__main__":
    main()
