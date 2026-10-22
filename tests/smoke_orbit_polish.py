"""Native regressions from the Orbit visual review; temporary data, no APIs."""
import os
from pathlib import Path
import sys
import tempfile
from time import perf_counter
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt, QSize, QPoint, QPointF, QEvent, QAbstractAnimation
from PySide6.QtGui import QColor, QPalette, QMouseEvent, QEnterEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionSpinBox, QStyleOptionComboBox

import gui
from studio_theme import PALETTES
from studio_controls import NumberInput, OrbitScrollArea
from ui_widgets import NoWheelComboBox, MasterDetail, card


def near(actual, expected):
    target = QColor(expected)
    return max(abs(a - b) for a, b in zip(actual.getRgb()[:3], target.getRgb()[:3])) < 5


def main():
    app = QApplication([])
    app.setStyle("Fusion")
    timings = []
    popup_count = 0
    with tempfile.TemporaryDirectory() as directory:
        with patch.object(gui, "data_dir", return_value=Path(directory)), patch.object(gui, "available_screen_size", return_value=QSize(1920, 1080)), patch("urllib.request.urlopen", side_effect=AssertionError("No API calls in UI fixtures")):
            window = gui.MainWindow()
            window.show()
            QTest.qWait(450)
            window.animations_control.setChecked(False)
            # Preference changes must never rebuild the global stylesheet tree.
            with patch.object(app, "setStyleSheet", side_effect=AssertionError("Unnecessary global restyle")):
                window.animations_control.setChecked(True)
                window.animations_control.setChecked(False)
                for theme in ("light", "dark"):
                    start = perf_counter()
                    window.theme_control.setCurrentIndex(window.theme_control.findData(theme))
                    app.processEvents()
                    timings.append(round((perf_counter() - start) * 1000))
                    c = PALETTES[theme]
                    assert near(window.palette().color(QPalette.Window), c["bg"])
                    assert near(window.page_title.palette().color(QPalette.Text), c["text"])
                    for size in (QSize(1240, 860), QSize(800, 480)):
                        window.resize(size)
                        for index in range(len(gui.PAGES)):
                            window._navigate(index)
                            app.processEvents()
                            for combo in window.pages.currentWidget().findChildren(NoWheelComboBox):
                                if not combo.isVisible():
                                    continue
                                assert near(combo.palette().color(QPalette.Base), c["input"])
                                combo.showPopup()
                                app.processEvents()
                                popup = combo.view().window()
                                assert not popup.mask().contains(QPoint(0, 0)), "Square popup underlay"
                                assert popup.mask().contains(popup.rect().center())
                                image = popup.grab().toImage()
                                point = QPoint(4, popup.height() // 2)
                                color = image.pixelColor(point)
                                assert color.alpha() == 255 and near(color, c["raised"]), (theme, color.name())
                                popup_count += 1
                                combo.hidePopup()
                            for spin in window.pages.currentWidget().findChildren(NumberInput):
                                if not spin.isVisible():
                                    continue
                                option = QStyleOptionSpinBox()
                                spin.initStyleOption(option)
                                for control in (QStyle.SC_SpinBoxUp, QStyle.SC_SpinBoxDown):
                                    rect = spin.style().subControlRect(QStyle.CC_SpinBox, option, control, spin)
                                    assert spin.rect().adjusted(2, 2, -2, -2).contains(rect), (spin.accessibleName(), rect)
                            for area in window.pages.currentWidget().findChildren(OrbitScrollArea):
                                ancestor = area.parentWidget()
                                pane_scroll = False
                                while ancestor is not None and not isinstance(ancestor, OrbitScrollArea):
                                    if isinstance(ancestor, MasterDetail):
                                        pane_scroll = True
                                    ancestor = ancestor.parentWidget()
                                if ancestor is not None:
                                    continue
                                bar = area.verticalScrollBar()
                                if area.isVisible() and bar.isVisible():
                                    right = bar.mapTo(window, QPoint(bar.width(), 0)).x()
                                    boundary = area.mapTo(window, QPoint(area.width(), 0)).x() if pane_scroll else window.width()
                                    assert 0 <= boundary - right <= 12, (index, boundary - right)
                        window._navigate(gui.PAGE_AUTONOMOUS)
                        app.processEvents()
                        mode = window.autonomous_page.mode
                        option = QStyleOptionComboBox()
                        mode.initStyleOption(option)
                        text = mode.style().subControlRect(QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxEditField, mode)
                        assert text.width() - 6 >= max(mode.fontMetrics().horizontalAdvance(mode.itemText(i)) for i in range(mode.count())), (mode.width(), text.width())
                        window._navigate(gui.PAGE_LOCAL_CONTEXT)
                        context = window.local_context_page
                        heights = []
                        for tab in (0, 1, 0):
                            context.knowledge_tabs.setCurrentIndex(tab)
                            app.processEvents()
                            heights.append(context.system_card.height())
                        assert max(heights) - min(heights) <= 2, heights
                        assert max(heights) < 115, heights
            assert popup_count >= 20
            # Rounding the native popup must retain keyboard selection.
            combo = NoWheelComboBox()
            combo.addItems(["First", "Second", "Third"])
            combo.show()
            combo.showPopup()
            app.processEvents()
            QTest.keyClick(combo.view(), Qt.Key_End)
            QTest.keyClick(combo.view(), Qt.Key_Return)
            assert combo.currentIndex() == 2
            assert not combo.view().window().isVisible()
            combo.close()
            combo.deleteLater()
            # The shared card must actually paint a gradient in both themes.
            panel, _ = card()
            panel.resize(300, 160)
            panel.show()
            app.processEvents()
            image = panel.grab().toImage()
            assert image.pixelColor(20, 20) != image.pixelColor(280, 140)
            panel.close()
            panel.deleteLater()
            # Pointer parallax is finite, eases back on leave and stops with motion off.
            window._navigate(gui.PAGE_ACTIVITY)
            window.animations_control.setChecked(True)
            header = window.orbit_header
            app.sendEvent(header, QEnterEvent(QPointF(10, 10), QPointF(10, 10), QPointF(header.mapToGlobal(QPoint(10, 10)))))
            point = QPointF(header.width() * .9, header.height() * .8)
            app.sendEvent(header, QMouseEvent(QEvent.MouseMove, point, QPointF(header.mapToGlobal(point.toPoint())), Qt.NoButton, Qt.NoButton, Qt.NoModifier))
            for _ in range(60):
                if header._motion.state() == QAbstractAnimation.Stopped and header._pointer_motion.state() == QAbstractAnimation.Stopped:
                    break
                QTest.qWait(20)
            assert header._shift > 3.9 and header._parallax.x() > .7
            assert header._motion.state() == QAbstractAnimation.Stopped
            app.sendEvent(header, QEvent(QEvent.Leave))
            for _ in range(60):
                if header._motion.state() == QAbstractAnimation.Stopped and header._pointer_motion.state() == QAbstractAnimation.Stopped:
                    break
                QTest.qWait(20)
            assert header._shift == 0 and header._parallax == QPointF()
            app.sendEvent(header, QEnterEvent(QPointF(10, 10), QPointF(10, 10), QPointF(header.mapToGlobal(QPoint(10, 10)))))
            window.animations_control.setChecked(False)
            assert header._shift == 0 and header._parallax == QPointF()
            assert header._pointer_motion.state() == QAbstractAnimation.Stopped
            window.close()
    print(f"Orbit polish: {popup_count} popup checks, step bounds, theme colors, mode captions, stable knowledge strip, scroll placement and finite hover passed. Theme changes: {timings} ms.")


if __name__ == "__main__":
    main()
