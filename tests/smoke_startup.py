"""Startup must never flash detached card headings or other extra windows."""
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QSize
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFrame

import gui
import ui_widgets


def main():
    app = QApplication([])
    app.setStyle("Fusion")
    for configured in (False, True):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            if configured:
                (root / ".env").write_text(
                    "TWITCH_CHANNEL=fixture_channel\nTWITCH_BOT_NAME=fixture_bot\n"
                    "TWITCH_CLIENT_ID=fixture_client\nAI_API_KEY=fixture_key\n", encoding="utf-8")
            shown = []

            class StartupFrame(QFrame):
                # Observe real show events on shared frames, without an
                # application-wide Python event filter or keeping widgets alive.
                def showEvent(self, event):
                    if self.isWindow():
                        shown.append((self.objectName(), self.windowTitle()))
                    super().showEvent(event)

            window = None
            try:
                with patch.object(gui, "data_dir", return_value=root), \
                        patch.object(gui, "available_screen_size", return_value=QSize(1920, 1080)), \
                        patch.object(ui_widgets, "QFrame", StartupFrame), \
                        patch("urllib.request.urlopen", side_effect=AssertionError("Offline startup fixture")):
                    window = gui.MainWindow()
                    assert not shown, (configured, "Windows shown during construction", shown)
                    assert not any(widget.isVisible() for widget in app.topLevelWidgets())
                    window.show()
                    QTest.qWait(500)
                    assert not shown, (configured, "Detached frames shown", shown)
                    assert [widget for widget in app.topLevelWidgets() if widget.isVisible()] == [window]
                    assert window.pages.currentIndex() == (gui.PAGE_ACTIVITY if configured else gui.PAGE_SETUP)
            finally:
                if window is not None:
                    window.close()
                    QTest.qWait(250)
    print("Startup: first run and saved settings show only the main window, with no detached-window flashes.")


if __name__ == "__main__":
    main()
