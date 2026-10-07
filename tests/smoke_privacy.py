"""Reward permission drafts, persistence and rejected-history UI; no network."""

import os
from pathlib import Path
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from gui_theme import STYLE
from history_gui import HistoryPage
from message_history import MessageHistory
from privacy import HIDDEN_DATA, PRIVACY_REFUSAL
from profiles import load_profiles, reward_is_blocked, save_profiles
from profiles_gui import ProfilesEditor


def wait_for(predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(20)
    assert predicate(), "History did not load"


def main():
    app = QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        editor = ProfilesEditor(root / "profiles.json")
        editor.resize(1100, 800)
        editor.show()
        editor.add_profile()
        editor.login.setText("@Viewer")
        editor.enabled.setChecked(False)
        editor.reward_blocked.setChecked(True)
        rows = editor.validated()
        assert rows[0]["prompt"] == ""
        assert reward_is_blocked(rows, "viewer", "123")
        assert not (root / "profiles.json").exists(), "Draft was saved implicitly"
        save_profiles(root / "profiles.json", rows)
        editor.saved(load_profiles(root / "profiles.json"))
        assert editor.reward_blocked.isChecked()
        assert not editor.enabled.isChecked()
        editor.set_editable(False)
        assert not editor.reward_blocked.isEnabled()
        editor.set_editable(True)
        editor.reward_blocked.setChecked(False)
        save_profiles(root / "profiles.json", editor.validated())
        assert not reward_is_blocked(load_profiles(root / "profiles.json"), "viewer", "123")
        editor.close()

        store = MessageHistory(root)
        store.add("reward", "rejected", viewer="viewer", question="viewer@example.com",
                  answer=PRIVACY_REFUSAL, sent_text="@viewer " + PRIVACY_REFUSAL)
        page = HistoryPage(root)
        page.resize(1100, 800)
        page.show()
        page.status.setCurrentIndex(page.status.findData("rejected"))
        wait_for(lambda: not page._loading and page._detail_id is not None)
        assert page.entries.count() == 1
        assert page.badge.text() == "Отказ"
        assert page.question.toPlainText() == HIDDEN_DATA
        assert "viewer@example.com" not in page.answer.toPlainText()
        page.shutdown()
        page.close()
        app.processEvents()
    print("Reward permissions and private-data refusals work in GUI.")


if __name__ == "__main__":
    main()
