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
        assert window.model.count() == 3
        assert window.model.isEditable()
        assert window.prompt.toPlainText() == ""
        window.channel.setText("https://www.twitch.tv/Streamer")
        window.bot_name.setText("HelperBot")
        window.client_id.setText("client123")
        window.api_key.setText("sk-test")
        window.model.setCurrentText("custom/model")
        window.reward_title.setText("Ask AI")
        window.fallback_models.setText("backup/model")
        window.prompt.setPlainText("Новый промпт")
        assert window._save()
        values = read_config(root / ".env")
        assert values["AI_MODEL"] == "custom/model"
        assert values["TWITCH_REWARD_TITLE"] == "Ask AI"
        assert values["AI_FALLBACK_MODELS"] == "backup/model"
        assert values["AI_API_KEY"] == "sk-test"
        assert window.api_key.text() == ""
        assert (root / "prompt.txt").read_text(encoding="utf-8").strip() == "Новый промпт"
        window._log_path = root / "bot-session.log"
        window._log_path.write_text("GUI_LOG_TEST\n", encoding="utf-8")
        window._poll_log()
        assert "GUI_LOG_TEST" in window.log.toPlainText()
        window.reset_prompt.click()
        assert window._save()
        assert not (root / "prompt.txt").read_text(encoding="utf-8").strip()
        window._kill_timer.start(3000)
        window._on_finished(0, window.process.ExitStatus.NormalExit)
        assert not window._kill_timer.isActive()
        window._set_running(True)
        assert not window.reset_prompt.isEnabled()
        window.close()
    app.quit()
    print("GUI settings form works.")


if __name__ == "__main__":
    main()
