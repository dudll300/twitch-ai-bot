"""Startup must never flash detached card headings or other extra windows."""
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QEvent, QObject, QSize
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

import gui


class StartupWindows(QObject):
    """Short-lived test observer; record even windows reparented immediately."""
    def __init__(self):
        super().__init__()
        self.shown = []

    def eventFilter(self, widget, event):
        if QEvent.type(event) == QEvent.Show and isinstance(widget, QWidget) and widget.isWindow():
            self.shown.append((type(widget).__name__, widget.windowTitle()))
        return False


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
            observer = StartupWindows()
            app.installEventFilter(observer)
            window = None
            try:
                with patch.object(gui, "data_dir", return_value=root), \
                        patch.object(gui, "available_screen_size", return_value=QSize(1920, 1080)), \
                        patch("urllib.request.urlopen", side_effect=AssertionError("Offline startup fixture")):
                    window = gui.MainWindow()
                    assert not observer.shown, (configured, "Windows shown during construction", observer.shown)
                    window.show()
                    QTest.qWait(500)
                    assert observer.shown == [("MainWindow", "Twitch AI Bot")], (configured, observer.shown)
                    assert window.isVisible()
                    assert window.pages.currentIndex() == (gui.PAGE_ACTIVITY if configured else gui.PAGE_SETUP)
            finally:
                app.removeEventFilter(observer)
                if window is not None:
                    window.close()
                    QTest.qWait(250)
    print("Startup: first run and saved settings show only the main window, with no detached-window flashes.")


if __name__ == "__main__":
    main()
