"""Local glossary editor drafts, persistence and close lifecycle; no network."""

import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QProcess, QSize
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QMessageBox

import gui
import local_context_gui
from local_context import load_document


def new_window(root):
    with patch.object(gui, "data_dir", return_value=root):
        window = gui.MainWindow()
    window.show()
    QApplication.processEvents()
    return window


def fill_card(page, name, meaning):
    page.add_card()
    page.name.setText(name)
    page.meaning.setPlainText(meaning)
    return page.rows[-1]["id"]


def main():
    app = QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(gui.STYLE)
    with patch.object(gui, "available_screen_size", return_value=QSize(1920, 1080)), \
         patch("urllib.request.urlopen", side_effect=AssertionError("GUI smoke must not call API")):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = new_window(root)
            page = window.local_context_page
            path = root / "local-context.json"
            assert window.pages.count() == len(gui.PAGES)
            assert not page.enabled.isChecked() and not page.rows and not page.dirty
            assert not path.exists(), "Opening the tab must not create a dictionary"
            window._navigate(gui.PAGE_LOCAL_CONTEXT)
            first = fill_card(page, "Тихие шаги", "Локальная отсылка к осторожному плану.")
            page.aliases.setPlainText("шаги в тишине\nТИХИЙ план")
            page.avoid.setPlainText("Не употреблять в серьёзном споре.")
            assert not page.allow_situational.isChecked()
            assert page.dirty
            # The global live switch saves only the previous dictionary.
            page.enabled.setChecked(True)
            saved = load_document(path)
            assert saved.settings.enabled and not saved.cards
            assert page.name.text() == "Тихие шаги" and page.dirty
            assert page.apply()
            assert load_document(path).cards[0].id == first and not page.dirty
            assert not (root / "local-context-usage.json").exists()

            page.name.setText("Очень тихие шаги")
            page.meaning.setPlainText("Черновик пояснения.")
            page.inputs["global_pause_seconds"].setValue(600)
            window._navigate(1)
            window._navigate(gui.PAGE_LOCAL_CONTEXT)
            assert page.name.text() == "Очень тихие шаги" and page.meaning.toPlainText() == "Черновик пояснения."
            assert window._save(), "General save remains available without Twitch"
            assert page.dirty, "General save must preserve separate local drafts"
            assert load_document(path).cards[0].name == "Тихие шаги"
            second = fill_card(page, "Большой финал", "Удачное завершение трудной попытки.")
            page.example.setPlainText("Вот это большой финал.")
            page.list.setCurrentRow(0)
            assert page.meaning.toPlainText() == "Черновик пояснения."
            assert page.rows[0]["id"] == first
            page.search.setText("ШАГИ В ТИШИНЕ")
            assert not page.list.item(0).isHidden() and page.list.item(1).isHidden()
            page.search.setText("ничего не найдено")
            assert page.meaning.toPlainText() == "Черновик пояснения.", "Filtering cannot discard an editor draft"
            page.search.clear()
            page.list.setCurrentRow(1)
            page.remove_card()
            page.undo_remove()
            assert page.rows[1]["id"] == second
            assert page.example.toPlainText() == "Вот это большой финал."
            page.list.setCurrentRow(0)
            page.allow_situational.setChecked(True)
            assert not page.apply(), "Situation-enabled cards require an explanation"
            assert page.meaning.toPlainText() == "Черновик пояснения."
            page.situations.setPlainText("Дружеское обсуждение осторожного плана, если автор сам шутит.")
            with patch.object(local_context_gui, "save_document", side_effect=OSError("private-error-detail")):
                assert not page.apply()
            assert "private-error-detail" not in page.error.text()
            assert page.dirty and page.meaning.toPlainText() == "Черновик пояснения."
            # Live switching does not apply the numeric or card draft either.
            page.enabled.setChecked(False)
            saved = load_document(path)
            assert not saved.settings.enabled and saved.cards[0].name == "Тихие шаги"
            assert saved.settings.global_pause_seconds == 300
            assert page.inputs["global_pause_seconds"].value() == 600
            window._set_running(True)
            assert page.apply_button.isEnabled() and page.name.isEnabled()
            assert page.apply()
            saved = load_document(path)
            assert saved.cards[0].id == first and saved.cards[0].name == "Очень тихие шаги"
            assert saved.settings.global_pause_seconds == 600

            # Local dirty state prompts even when main settings are clean.
            page.meaning.setPlainText("Закрываемый черновик.")
            assert not window._dirty and page.dirty
            close = QCloseEvent()
            with patch.object(gui, "russian_question", return_value=QMessageBox.Save), \
                 patch.object(local_context_gui, "save_document", side_effect=OSError("private-save-failure")):
                window.closeEvent(close)
            assert not close.isAccepted() and page.dirty and window.pages.currentIndex() == gui.PAGE_LOCAL_CONTEXT
            assert page.meaning.toPlainText() == "Закрываемый черновик."
            assert load_document(path).cards[0].meaning == "Черновик пояснения."
            close = QCloseEvent()
            with patch.object(gui, "russian_question", return_value=QMessageBox.Cancel) as question:
                window.closeEvent(close)
            assert not close.isAccepted() and question.call_count == 1
            assert page.meaning.toPlainText() == "Закрываемый черновик."
            # Saving local edits while running succeeds before the stop question.
            close = QCloseEvent()
            with patch.object(window.process, "state", return_value=QProcess.Running), \
                 patch.object(gui, "russian_question", side_effect=[QMessageBox.Save, QMessageBox.No]) as question:
                window.closeEvent(close)
            assert not close.isAccepted() and question.call_count == 2
            assert not page.dirty and load_document(path).cards[0].meaning == "Закрываемый черновик."
            page.meaning.setPlainText("Не сохранять эту правку.")
            with patch.object(gui, "russian_question", return_value=QMessageBox.Discard):
                window.close()
            assert load_document(path).cards[0].meaning == "Закрываемый черновик."

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "local-context.json"
            original = "{broken"
            path.write_text(original, encoding="utf-8")
            window = new_window(root)
            page = window.local_context_page
            assert page.load_error and not page.enabled.isChecked()
            assert path.read_text(encoding="utf-8") == original
            page.enabled.setChecked(True)
            assert not page.enabled.isChecked(), "A toggle cannot overwrite a broken dictionary"
            assert path.read_text(encoding="utf-8") == original
            fill_card(page, "Новый словарь", "Явно введённая владельцем карточка.")
            assert page.apply()
            assert not page.load_error and load_document(path).cards[0].name == "Новый словарь"
            backups = list(root.glob("local-context.corrupt-*.bak"))
            assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == original
            window.close()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = new_window(root)
            page = window.local_context_page
            fill_card(page, "Сохранённая карточка", "Объяснение владельца.")
            assert page.apply()
            page.meaning.setPlainText("Черновик после сохранения.")
            path = root / "local-context.json"
            # Corruption can happen after the page was loaded too. The live
            # switch must not turn that file into an automatic dictionary repair.
            path.write_text("{ damaged after opening", encoding="utf-8")
            page.enabled.setChecked(True)
            assert not page.enabled.isChecked() and page.load_error
            assert path.read_text(encoding="utf-8") == "{ damaged after opening"
            assert page.meaning.toPlainText() == "Черновик после сохранения." and page.dirty
            assert not list(root.glob("local-context.corrupt-*.bak"))
            assert page.apply(), "Explicit application can repair the file and back it up"
            assert load_document(path).cards[0].meaning == "Черновик после сохранения."
            assert len(list(root.glob("local-context.corrupt-*.bak"))) == 1
            window.close()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = new_window(root)
            page = window.local_context_page
            fill_card(page, "Сохранить всё", "Корректная новая карточка.")
            window.prompt.setPlainText("Общий черновик.")
            close = QCloseEvent()
            with patch.object(gui, "russian_question", return_value=QMessageBox.Save) as question:
                window.closeEvent(close)
            assert close.isAccepted() and question.call_count == 1
            assert load_document(root / "local-context.json").cards[0].name == "Сохранить всё"
            assert (root / "prompt.txt").read_text(encoding="utf-8").strip() == "Общий черновик."
            window.deleteLater()
    app.processEvents()
    print("Local context GUI smoke passed")


if __name__ == "__main__":
    main()
