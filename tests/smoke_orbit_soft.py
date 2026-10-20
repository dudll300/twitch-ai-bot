"""Soft typography, compact forms, complete halos and reduced-motion lifecycle."""
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt, QSize, QPoint, QAbstractAnimation
from PySide6.QtGui import QFontInfo
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QHBoxLayout, QMessageBox

import gui
from studio_surfaces import GlowSurface
from ui_widgets import Disclosure, card, label


def main():
    app = QApplication([])
    app.setStyle("Fusion")
    with tempfile.TemporaryDirectory() as directory:
        with patch.object(gui, "data_dir", return_value=Path(directory)), patch.object(gui, "available_screen_size", return_value=QSize(1920, 1080)), patch("urllib.request.urlopen", side_effect=AssertionError("No API calls in UI fixtures")):
            window = gui.MainWindow()
            window.show()
            window.animations_control.setChecked(False)
            for theme in ("dark", "light"):
                window.theme_control.setCurrentIndex(window.theme_control.findData(theme))
                for size in (QSize(1240, 950), QSize(800, 480)):
                    window.resize(size)
                    window._navigate(gui.PAGE_BEHAVIOR)
                    window.behavior_mode.setCurrentIndex(2)
                    app.processEvents()
                    wishes = window.prompt_builder.improve_wishes
                    container = wishes.parentWidget()
                    caption = container.layout().itemAt(0).widget()
                    assert 0 <= wishes.y() - caption.geometry().bottom() <= 10
                    assert caption.height() < 30
                    assert window.prompt_builder.improve_card.height() < 450
                    window._navigate(gui.PAGE_SAFETY)
                    app.processEvents()
                    error = window.safety_page.error
                    assert error.isHidden(), "Empty error must not paint a red block"
                    error.setText("Fixture validation error")
                    app.processEvents()
                    assert error.isVisible()
                    error.setText("")
                    assert error.isHidden()
                    window._navigate(gui.PAGE_CONNECTION)
                    window.settings_tabs.setCurrentIndex(2)
                    app.processEvents()
                    help_rows = window.settings_tabs.widget(2).findChildren(Disclosure)
                    assert len(help_rows) == 6
                    assert all(row.height() <= 48 for row in help_rows)
                    window._navigate(gui.PAGE_VIEWERS)
                    app.processEvents()
                    editor = window.profiles_editor
                    assert editor.empty_card.height() < 500
                    assert editor.empty_copy.alignment() & Qt.AlignJustify
                    assert QFontInfo(editor.empty_copy.font()).family() == "Nunito"
                    assert editor.empty_copy.font().weight() >= 600
                    nav = window.nav_buttons[gui.PAGE_VIEWERS]
                    assert abs(nav.mapTo(window, nav.rect().center()).x() - 37) <= 3
            # The empty-state action leads to a real draft, using the existing flow.
            window.resize(1240, 860)
            window._navigate(gui.PAGE_VIEWERS)
            app.processEvents()
            window.profiles_editor.empty_add.click()
            assert len(window.profiles_editor.rows) == 1
            assert window.profiles_editor.stack.currentIndex() == 1
            app.processEvents()
            editor = window.profiles_editor
            left = editor.count.parentWidget().parentWidget()
            assert abs(left.mapTo(window, QPoint()).y() - editor.profile_card.mapTo(window, QPoint()).y()) <= 1
            window._revert_section(gui.PAGE_VIEWERS)
            # Expanded content animates, finishes on rapid changes and motion-off.
            window._navigate(gui.PAGE_CONNECTION)
            window.settings_tabs.setCurrentIndex(2)
            window.animations_control.setChecked(True)
            app.processEvents()
            row = help_rows[0]
            row.button.setChecked(True)
            QTest.qWait(60)
            assert row._reveal_motion.state() == QAbstractAnimation.Running
            row.button.setChecked(False)
            row.button.setChecked(True)
            QTest.qWait(300)
            assert row.content.isVisible() and row.reveal.maximumHeight() == 16777215
            row.button.setChecked(False)
            window.animations_control.setChecked(False)
            assert not row.content.isVisible() and row.reveal.maximumHeight() == 0
            # Inner tab transitions also settle to crisp native controls.
            window.settings_tabs.setCurrentIndex(0)
            window.animations_control.setChecked(True)
            window.settings_tabs.setCurrentIndex(2)
            QTest.qWait(30)
            assert window.settings_tabs._page_transition.isVisible()
            QTest.qWait(400)
            assert not window.settings_tabs._page_transition.isVisible()
            window.animations_control.setChecked(False)
            # Star animation is local and pauses in every required state.
            header = window.orbit_header
            assert not header._twinkle_timer.isActive()
            phase = header._star_phase
            QTest.qWait(100)
            assert header._star_phase == phase
            window.animations_control.setChecked(True)
            QTest.qWait(120)
            assert header._twinkle_timer.isActive() and header._star_phase > phase
            window.showMinimized()
            QTest.qWait(80)
            assert not header._twinkle_timer.isActive()
            window.showNormal()
            QTest.qWait(80)
            assert header._twinkle_timer.isActive()
            window.hide()
            assert not header._twinkle_timer.isActive()
            with patch.object(gui, "russian_question", return_value=QMessageBox.Discard):
                window.close()
    # Halos must reach the left edge and the gap between neighboring tiles.
    surface = GlowSurface()
    layout = QHBoxLayout(surface)
    layout.setContentsMargins(24, 24, 24, 24)
    layout.setSpacing(24)
    for _ in range(2):
        panel, _ = card()
        layout.addWidget(panel)
    surface.resize(520, 220)
    surface.show()
    app.processEvents()
    image = surface.grab().toImage()
    base = image.pixelColor(1, 110)
    assert image.pixelColor(18, 110) != base, "Missing left halo"
    assert image.pixelColor(260, 110) != base, "Missing halo between tiles"
    surface.close()
    print("Orbit soft: Nunito, bounded captions, compact help, empty-state action, hidden empty errors, complete halos, disclosures and paused stars passed.")


if __name__ == "__main__":
    main()
