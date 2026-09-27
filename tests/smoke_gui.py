"""Exercise the settings form without Twitch or AI network calls."""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication, QLineEdit

import gui
from start import read_config


def main() -> None:
    app = QApplication([])
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        with patch.object(gui, "data_dir", return_value=root):
            window = gui.MainWindow()
        assert window.api_key.echoMode() == QLineEdit.Password
        assert window.model.count() == 5
        window.channel.setText("https://www.twitch.tv/Sophie")
        window.bot_name.setText("ChundaBot")
        window.client_id.setText("client123")
        window.api_key.setText("sk-test")
        window.model.setCurrentText("my-custom-model")
        window.prompt.setPlainText("Новый промпт")
        assert window._save()
        values = read_config(root / ".env")
        assert values["AI_MODEL"] == "my-custom-model"
        assert values["AI_API_KEY"] == "sk-test"
        assert window.api_key.text() == ""
        assert (root / "prompt.txt").read_text(encoding="utf-8").strip() == "Новый промпт"
        window.process.start(sys.executable, ["-u", "-c", "print('GUI_LOG_TEST')"])
        assert window.process.waitForFinished(5000)
        app.processEvents()
        assert "GUI_LOG_TEST" in window.log.toPlainText()
        window.close()
    app.quit()
    print("GUI settings form works.")


if __name__ == "__main__":
    main()
