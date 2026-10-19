"""Generation, candidate comparisons and shutdown; all HTTP calls are fake."""

import io
import json
import os
import sys
import tempfile
import time
import urllib.error
from pathlib import Path
from threading import Event, get_ident
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

import ai_client
import gui
from configuration import env_content
from prompt_builder import CORE_RULES, DEFAULT_TOPICS, TEMPERAMENTS, TEST_CASES
from testing import Model


def response(data):
    return io.BytesIO(json.dumps(data, ensure_ascii=False).encode("utf-8"))


def wait_for(predicate):
    deadline = time.monotonic() + 4
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(15)
    assert predicate(), "Timed out waiting for generator worker"


def main():
    from safety_fakes import approved
    patch("testing.review_candidate", side_effect=approved).start()
    app = QApplication.instance() or QApplication([])
    main_thread = get_ident()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        with patch.object(gui, "data_dir", return_value=root), patch.object(ai_client.urllib.request, "urlopen") as http:
            window = gui.MainWindow()
            builder = window.prompt_builder
            builder.wishes.setPlainText("Будь язвительным")
            builder.generate_button.click()
            assert "Укажите API-ключ вашего AI-провайдера" in builder.status.text()
            assert not builder._busy
            builder.preset_button.click()
            assert CORE_RULES in builder.preview.toPlainText()
            assert builder.preview.minimumHeight() >= 520
            calm = builder.preview.toPlainText()
            builder.temperament.setCurrentIndex(3)
            builder.preset_button.click()
            assert builder.preview.toPlainText() != calm
            assert TEMPERAMENTS[3][1] in builder.preview.toPlainText()
            builder.improve_button.click()
            assert "Текущий общий промпт пуст" in builder.status.text()
            assert not window._dirty
            assert not window.prompt.toPlainText()
            http.assert_not_called()  # Neither startup nor offline presets load the catalog.
        window.close()

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / ".env").write_text(env_content({"AI_API_KEY": "saved-builder-key"}), encoding="utf-8")
        (root / "prompt.txt").write_text("Сохранённый общий промпт", encoding="utf-8")
        (root / "profiles.json").write_text(json.dumps({"version": 1, "profiles": [
            {"login": "lopotik", "user_id": "123", "prompt": "Сохранённая инструкция", "enabled": True}]}), encoding="utf-8")
        before = {path.name: path.read_bytes() for path in root.iterdir()}
        with patch.object(gui, "data_dir", return_value=root):
            window = gui.MainWindow()
        window.show()
        app.processEvents()
        window._navigate(1)
        builder, page = window.prompt_builder, window.testing_page
        builder.preset_button.click()
        QTest.qWait(80)
        scroll = window.pages.widget(1)
        assert 0 <= builder.preview.mapTo(scroll.viewport(), QPoint(0, 0)).y() <= 24
        window.profiles_editor.prompt.setPlainText("Несохранённая инструкция Лопотика")
        window.prompt.setPlainText("Несохранённый общий промпт")
        builder.preset_button.click()
        candidate = builder.preview.toPlainText()
        builder.apply_button.click()
        builder.apply_button.click()  # Double apply must not lose the original undo target.
        assert window.prompt.toPlainText() == candidate
        builder.undo_button.click()
        assert window.prompt.toPlainText() == "Несохранённый общий промпт"
        builder.preview.setPlainText("Удалён блок защиты")
        assert not builder.apply_button.isEnabled() and not builder.test_button.isEnabled()
        builder.preset_button.click()

        builder.wishes.setPlainText("Эти пожелания относятся только к новому промпту")
        builder.improve_wishes.setPlainText("Шути часто, мат изредка")
        builder.topics.setText("Игры и чат")
        builder.temperament.setCurrentIndex(3)
        builder.model_catalog.combo.setCurrentText("exact/prompt-generator")
        assert window._test_credentials().api_key == "saved-builder-key"
        window.api_key.setText("entered-builder-key")
        entered, release = Event(), Event()
        captured = []
        def generate(request, timeout):
            assert get_ident() != main_thread
            assert timeout == 20
            assert request.get_header("Authorization") == "Bearer entered-builder-key"
            payload = json.loads(request.data)
            assert payload["model"] == "exact/prompt-generator"
            assert payload["max_tokens"] == 2048
            captured.append(payload)
            entered.set()
            assert release.wait(4)
            return response({"choices": [{"message": {"content": json.dumps({"style": "Отвечай по существу, используй язвительный юмор и мат изредка."})}, "finish_reason": "stop"}]})
        with patch.object(ai_client.urllib.request, "urlopen", side_effect=generate), patch.object(
                window.process, "start") as start, patch.object(gui, "save_settings") as save:
            builder.improve_button.click()
            wait_for(entered.is_set)
            assert builder._busy and not builder.generate_button.isEnabled() and not builder.improve_button.isEnabled()
            builder.start_generation()
            builder.improve_wishes.setPlainText("Изменение пожеланий после клика")
            window.prompt.setPlainText("Правка общего во время запроса")
            assert len(captured) == 1
            data = json.loads(captured[0]["messages"][-1]["content"])
            assert data["wishes"] == "Шути часто, мат изредка"
            assert data["current_prompt"] == "Несохранённый общий промпт"
            assert data["operation"] == "improve" and data["base_style"] == ""
            assert data["topics"] == DEFAULT_TOPICS  # New-prompt settings do not leak into improvement.
            release.set()
            wait_for(lambda: not builder._busy)
            assert window.prompt.toPlainText() == "Правка общего во время запроса"
            assert CORE_RULES in builder.preview.toPlainText()
            assert "entered-builder-key" not in builder.preview.toPlainText()
            start.assert_not_called()
            save.assert_not_called()

        # Creation uses its own wishes/temperament and never the current general prompt.
        with patch.object(ai_client.urllib.request, "urlopen", side_effect=generate):
            builder.generate_button.click()
            wait_for(lambda: not builder._busy)
        created = json.loads(captured[-1]["messages"][-1]["content"])
        assert created["operation"] == "create" and created["current_prompt"] == ""
        assert created["wishes"] == "Эти пожелания относятся только к новому промпту"
        assert TEMPERAMENTS[3][1] in created["base_style"]
        assert created["topics"] == "Игры и чат"
        assert window.prompt.toPlainText() == "Правка общего во время запроса"

        builder.test_button.click()
        assert window.pages.currentIndex() == gui.PAGE_TESTING
        assert page.prompt_override == builder.preview.toPlainText()
        snapshot = window._test_snapshot(["exact/a"], "Вопрос?", "profile", "", 0,
                                         prompt_override=page.prompt_override)
        assert snapshot.messages[0][1] == builder.preview.toPlainText()
        assert snapshot.messages[1][1] == "Несохранённая инструкция Лопотика"
        assert window.prompt.toPlainText() == "Правка общего во время запроса"
        assert not page.restore_prompt_button.isHidden()
        page._add_model(Model("exact/a"), checked=True)
        page._add_model(Model("exact/b"), checked=True)
        page.case_combo.setCurrentIndex(3)
        assert "калькулятор" in page.question.toPlainText()
        assert "кода" in page.expectation.text()
        comparisons = []
        def compare(request, timeout):
            assert get_ident() != main_thread
            payload = json.loads(request.data)
            comparisons.append(payload)
            return response({"choices": [{"message": {"content": "Давай вернёмся к играм и чату."}}]})
        with patch.object(ai_client.urllib.request, "urlopen", side_effect=compare):
            page.run_button.click()
            wait_for(lambda: not page._testing_busy)
        assert len(comparisons) == 2
        assert comparisons[0]["messages"] == comparisons[1]["messages"]
        assert CORE_RULES in comparisons[0]["messages"][0]["content"]
        assert comparisons[0]["messages"][0]["content"] == builder.preview.toPlainText()
        assert "временный вариант" in page.context.text()
        for index, (_, question, expected) in enumerate(TEST_CASES, 1):
            page.case_combo.setCurrentIndex(index)
            assert page.question.toPlainText() == question
            assert expected in page.expectation.text()
        page.restore_prompt_button.click()
        assert page.prompt_override is None
        snapshot = window._test_snapshot(["exact/a"], "Вопрос?", "viewer", "", None)
        assert snapshot.messages[0][1] == "Правка общего во время запроса"
        window._set_running(True)
        assert not builder.generate_button.isEnabled() and not builder.improve_button.isEnabled() and not builder.apply_button.isEnabled()
        window._set_running(False)

        # Provider errors cannot expose the key or overwrite the previous valid candidate.
        previous = builder.preview.toPlainText()
        def failure(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 503, "Error", {},
                                         response({"error": {"message": "Error entered-builder-key"}}))
        with patch.object(ai_client.urllib.request, "urlopen", side_effect=failure):
            builder.start_generation()
            wait_for(lambda: not builder._busy)
        assert "503" in builder.status.text() and "entered-builder-key" not in builder.status.text()
        assert builder.preview.toPlainText() == previous
        entered.clear()
        release.clear()
        with patch.object(ai_client.urllib.request, "urlopen", side_effect=generate):
            builder.start_generation()
            wait_for(entered.is_set)
            window.api_key.setText("changed-builder-key")
            release.set()
            wait_for(lambda: not builder._busy)
        assert "Параметры AI API изменились" in builder.status.text()
        assert builder.preview.toPlainText() == previous
        window.api_key.setText("entered-builder-key")
        assert before == {path.name: path.read_bytes() for path in root.iterdir()}

        # Closing drops an outstanding result without touching Qt from the worker.
        entered.clear()
        release.clear()
        with patch.object(ai_client.urllib.request, "urlopen", side_effect=generate):
            builder.start_generation()
            wait_for(entered.is_set)
            with patch.object(gui, "russian_question", return_value=QMessageBox.Discard):
                window.close()
            assert builder._closed and builder._cancel.is_set()
            assert not builder._timer.isActive()
            release.set()
            QTest.qWait(100)
        assert builder._events.empty()
        assert before == {path.name: path.read_bytes() for path in root.iterdir()}
    print("Prompt builder works: offline presets, drafts, defenses, comparisons and teardown.")


if __name__ == "__main__":
    main()
