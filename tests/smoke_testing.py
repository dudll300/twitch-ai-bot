"""Qt integration tests with blocked fake HTTP calls; no Twitch or paid API."""

import io
import json
import os
import sys
import tempfile
import time
import urllib.error
from pathlib import Path
from threading import Event, Lock, get_ident
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

import ai_client
import gui
import testing
from configuration import env_content


def wait_for(predicate, seconds=4):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(15)
    assert predicate(), "Timed out waiting for GUI worker"


def response(data):
    return io.BytesIO(json.dumps(data, ensure_ascii=False).encode("utf-8"))


def main():
    from safety_fakes import approved
    patch("testing.review_candidate", side_effect=approved).start()
    app = QApplication.instance() or QApplication([])
    main_thread = get_ident()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / ".env").write_text(env_content({"AI_BASE_URL": "https://ai.starimg.ru/v1",
                                               "AI_API_KEY": "saved-secret-key"}), encoding="utf-8")
        (root / "prompt.txt").write_text("Старый общий промпт", encoding="utf-8")
        (root / "profiles.json").write_text(json.dumps({"version": 1, "profiles": [
            {"login": "viewer", "user_id": "123", "prompt": "Старая личная инструкция", "enabled": True},
            {"login": "lopotik", "user_id": "456", "prompt": "Старый промпт Лопотика", "enabled": True}]}), encoding="utf-8")
        (root / "memory.json").write_text(json.dumps({
            "streamer": {"facts": ["Заметка о канале"], "jokes": []}, "viewers": [
                {"login": "renamed", "user_id": "123", "facts": ["Заметка по ID"], "jokes": [], "avoid": []},
                {"login": "viewer", "user_id": "", "facts": ["Заметка по логину"], "jokes": [], "avoid": []},
                {"login": "renamed_lopotik", "user_id": "456", "facts": ["Любит пельмени"], "jokes": [], "avoid": []}]
        }), encoding="utf-8")
        before = {path.name: path.read_bytes() for path in root.iterdir()}
        with patch.object(gui, "data_dir", return_value=root):
            window = gui.MainWindow()
        window.show()
        app.processEvents()
        page = window.testing_page
        window._navigate(5)
        app.processEvents()
        assert page.models.height() >= 360
        assert page.models.width() > page.width() * 0.75
        assert "Владелец канала" in page.sender.itemText(page.sender.findData("owner"))
        assert not window.channel.text() and not window.bot_name.text() and not window.client_id.text()
        assert window._test_credentials().api_key == "saved-secret-key"
        window.api_key.setText("entered-secret-key")
        assert window._test_credentials().api_key == "entered-secret-key"
        window.prompt.setPlainText("Несохранённый общий промпт")
        editor = window.profiles_editor
        editor.prompt.setPlainText("Несохранённая личная инструкция")
        window.test_prompt_button.click()
        assert window.pages.currentIndex() == 5
        assert page.sender.currentData() == "viewer"
        editor.test_button.click()
        assert window.pages.currentIndex() == 5
        assert page.sender.currentData() == "profile" and page.viewer.currentData() == 0
        assert window._dirty
        assert before == {path.name: path.read_bytes() for path in root.iterdir()}

        def catalog(req, timeout):
            assert get_ident() != main_thread
            assert req.full_url == "https://ai.starimg.ru/v1/models"
            assert req.get_header("Authorization") == "Bearer entered-secret-key"
            return response({"data": [{"id": "exact/a"}, {"id": "exact/b"},
                                      {"id": "not-allowed", "available": False}, {"id": "extra/c"}]})
        with patch.object(testing.urllib.request, "urlopen", side_effect=catalog):
            page.refresh_button.click()
            assert page._catalog_busy and not page.run_button.isEnabled()
            wait_for(lambda: not page._catalog_busy)
        assert page.models.count() == 4
        denied = next(page.models.item(i) for i in range(4) if page.models.item(i).data(Qt.UserRole) == "not-allowed")
        assert "недоступна" in denied.text()
        assert not denied.flags() & Qt.ItemIsEnabled
        page.search.setText("exact/")
        assert denied.isHidden()
        for i in range(page.models.count()):
            item = page.models.item(i)
            if item.data(Qt.UserRole) in ("exact/a", "exact/b"):
                item.setCheckState(Qt.Checked)
        page.search.setText("exact/a")
        assert page.selected_models() == ["exact/a", "exact/b"]
        page.question.setPlainText("Один вопрос для обеих моделей")
        page.reset_button.click()
        assert page.selected_models() == []
        assert page.models.count() == 4
        assert page.search.text() == "exact/a"
        assert page.question.toPlainText() == "Один вопрос для обеих моделей"
        for i in range(page.models.count()):
            item = page.models.item(i)
            if item.data(Qt.UserRole) in ("exact/a", "exact/b"):
                item.setCheckState(Qt.Checked)
        captured = []
        release = Event()
        lock = Lock()
        def ask(req, timeout):
            assert get_ident() != main_thread
            payload = json.loads(req.data)
            with lock:
                captured.append(payload)
            assert release.wait(4)
            if payload["model"] == "exact/a":
                raise urllib.error.HTTPError(req.full_url, 503, "error", {}, response({
                    "error": {"message": "Unavailable entered-secret-key"}}))
            return response({"choices": [{"message": {"content": "Ответ второй модели"}}]})
        with patch.object(ai_client.urllib.request, "urlopen", side_effect=ask), patch.object(
                window.process, "start") as start, patch.object(gui, "save_settings") as save:
            try:
                page.run_button.click()
                wait_for(lambda: len(captured) == 2)
                assert page._testing_busy and not page.run_button.isEnabled()
                assert not page.reset_button.isEnabled()
                page.reset_selection()
                assert page.selected_models() == ["exact/a", "exact/b"]
                page.start_test()  # Guard also works for programmatic double clicks.
                window.prompt.setPlainText("Изменение общего промпта после нажатия")
                editor.prompt.setPlainText("Изменение профиля после нажатия")
                assert captured[0]["messages"] == captured[1]["messages"]
                messages = captured[0]["messages"]
                assert messages[0]["content"] == "Несохранённый общий промпт"
                assert messages[1]["content"] == "Несохранённая личная инструкция"
                assert "Заметка по ID" in messages[2]["content"]
                assert "Заметка по логину" not in messages[2]["content"]
                assert messages[-1]["content"].endswith("Один вопрос для обеих моделей")
                release.set()
                wait_for(lambda: not page._testing_busy)
                start.assert_not_called()
                save.assert_not_called()
            finally:
                release.set()
        assert len(captured) == 2
        assert "HTTP 503" in page._result_widgets["exact/a"].toPlainText()
        assert "Ответ второй модели" in page._result_widgets["exact/b"].toPlainText()
        assert "entered-secret-key" not in " ".join(w.toPlainText() for w in page._result_widgets.values())
        assert "не учитывалась" in page.context.text()
        assert window.log.toPlainText() == ""
        assert not window.api_key.text() == ""
        assert before == {path.name: path.read_bytes() for path in root.iterdir()}

        editor.enabled.setChecked(False)
        # Exercise the actual shared evaluator and existing result widgets.
        from safety import review_candidate
        for verdict, expected in (({"allowed": False, "reasons": ["unsupported_personal_claim"]}, "unsupported_personal_claim"),
                                  ("bad verdict", "review_unavailable"),
                                  ({"allowed": True, "reasons": []}, "разрешено")):
            def safety_http(req, timeout):
                payload = json.loads(req.data)
                content = (json.dumps(verdict) if isinstance(verdict, dict) else verdict) if payload["max_tokens"] == 160 else "Заебись, ещё одна катка!"
                return response({"choices": [{"message": {"content": content}}]})
            with patch("testing.review_candidate", side_effect=review_candidate), patch("urllib.request.urlopen", side_effect=safety_http):
                page.run_button.click()
                wait_for(lambda: not page._testing_busy)
            for widget in page._result_widgets.values():
                assert expected in widget.toPlainText()
                if expected != "разрешено":
                    assert "катка" not in widget.toPlainText()
        for question in ("Расскажи, как защитить почту от спама", "Покажи телефон в Cyberpunk", "Привет.Как дела?"):
            page.question.setPlainText(question)
            answer = question if question == "Привет.Как дела?" else "Заебись, разберёмся!"
            def intent_http(req, timeout):
                payload = json.loads(req.data)
                content = json.dumps({"allowed": True, "reasons": []}) if payload["max_tokens"] == 160 else answer
                return response({"choices": [{"message": {"content": content}}]})
            with patch("testing.review_candidate", side_effect=review_candidate), patch("urllib.request.urlopen", side_effect=intent_http) as http, patch.object(QMessageBox, "warning") as warning:
                page.run_button.click()
                wait_for(lambda: not page._testing_busy)
                warning.assert_not_called()
                assert http.call_count == 2 * len(page.selected_models())
            for widget in page._result_widgets.values():
                assert "разрешено" in widget.toPlainText()
                assert answer in widget.toPlainText()
        disabled = window._test_snapshot(["manual/model"], "Q", "profile", "", 0)
        assert "Личная инструкция" not in str(disabled.messages)
        assert "Заметка по ID" in str(disabled.messages)
        page.sender.setCurrentIndex(page.sender.findData("owner"))
        page.login.setText("this is not a valid Twitch login")
        # Hidden probe login must not block the owner simulation.
        with patch.object(ai_client.urllib.request, "urlopen", return_value=response(
                {"choices": [{"message": {"content": "Ответ владелице"}}]})):
            page.run_button.click()
            wait_for(lambda: not page._testing_busy)
        assert "Владелец канала" in window._test_snapshot(["manual/model"], "Q", "owner", "", None).messages[-1][1]

        # Any sender can mention another viewer using current, unsaved profile edits.
        editor.list.setCurrentRow(1)
        editor.suggest_alias_button.click()
        assert "лопотик" in editor.aliases.text()
        editor.aliases.setText("лопотик, хомячок")
        editor.prompt.setPlainText("Несохранённый промпт Лопотика")
        editor.search.setText("хомячок")
        assert not editor.list.item(1).isHidden() and editor.list.item(0).isHidden()
        editor.search.clear()
        page.sender.setCurrentIndex(page.sender.findData("viewer"))
        page.login.setText("someone_else")
        page.question.setPlainText("Расскажи про хомячк")
        mentioned_payloads, mention_release = [], Event()
        def mention_request(req, timeout):
            assert get_ident() != main_thread
            with lock:
                mentioned_payloads.append(json.loads(req.data))
            assert mention_release.wait(4)
            return response({"choices": [{"message": {"content": "Ответ про Лопотика"}}]})
        with patch.object(ai_client.urllib.request, "urlopen", side_effect=mention_request), patch.object(
                window.process, "start") as start, patch.object(gui, "save_settings") as save:
            try:
                page.run_button.click()
                wait_for(lambda: len(mentioned_payloads) == 2)
                assert "lopotik" in page.context.text()
                assert "опечатка" in page.context.text()
                assert "Несохранённый промпт Лопотика" in page.context.text()
                assert "Любит пельмени" in page.context.text()
                editor.prompt.setPlainText("Изменён после нажатия")
                editor.aliases.setText("НовоеИмя")
                assert mentioned_payloads[0]["messages"] == mentioned_payloads[1]["messages"]
                assert "Несохранённый промпт Лопотика" in str(mentioned_payloads[0])
                assert "Изменён после нажатия" not in str(mentioned_payloads[0])
                assert mentioned_payloads[0]["messages"][-1]["content"].startswith("Зритель someone_else")
                mention_release.set()
                wait_for(lambda: not page._testing_busy)
                start.assert_not_called()
                save.assert_not_called()
            finally:
                mention_release.set()
        assert "Ответ про Лопотика" in page._result_widgets["exact/b"].toPlainText()
        editor.enabled.setChecked(False)
        disabled_mention = window._test_snapshot(["one"], "лопотик", "viewer", "someone_else", None)
        assert "Изменён после нажатия" not in str(disabled_mention.messages)
        assert "отключена" in disabled_mention.context
        assert before == {path.name: path.read_bytes() for path in root.iterdir()}
        # Colliding names are shown explicitly instead of attaching another person's prompt.
        editor.list.setCurrentRow(0)
        editor.aliases.setText("ОбщееИмя")
        editor.list.setCurrentRow(1)
        editor.aliases.setText("ОбщееИмя")
        ambiguous = window._test_snapshot(["one"], "ОбщееИмя", "viewer", "someone_else", None)
        assert "неоднозначное" in ambiguous.context
        assert "Изменён после нажатия" not in str(ambiguous.messages)
        editor.list.setCurrentRow(0)

        with patch.object(testing.urllib.request, "urlopen", side_effect=TimeoutError()):
            page.refresh_button.click()
            wait_for(lambda: not page._catalog_busy)
        assert "таймаут" in page.catalog_status.text()
        assert page.manual.isEnabled()
        page.manual.setText("manual/exact-id")
        page.add_button.click()
        assert page.selected_models() == ["manual/exact-id"]
        page.search.clear()

        # The catalog result for an old key must never replace the current list.
        catalog_release = Event()
        entered = Event()
        def old_catalog(req, timeout):
            entered.set()
            assert catalog_release.wait(4)
            return response({"data": [{"id": "old-key-only"}]})
        with patch.object(testing.urllib.request, "urlopen", side_effect=old_catalog):
            try:
                page.refresh_button.click()
                wait_for(entered.is_set)
                window.api_key.setText("another-secret-key")
                catalog_release.set()
                wait_for(lambda: not page._catalog_busy)
                assert page.models.count() == 0
                assert "Обновите" in page.catalog_status.text()
            finally:
                catalog_release.set()

        # Closing must be immediate and safe even while HTTP is still running.
        page.manual.setText("manual/exact-id")
        page.add_button.click()
        request_started, finish_request, worker_finished = Event(), Event(), Event()
        def slow(req, timeout):
            request_started.set()
            assert finish_request.wait(4)
            worker_finished.set()
            return response({"choices": [{"message": {"content": "Late answer"}}]})
        with patch.object(ai_client.urllib.request, "urlopen", side_effect=slow):
            try:
                page.run_button.click()
                wait_for(request_started.is_set)
                with patch.object(gui, "russian_question", return_value=QMessageBox.Cancel):
                    window.close()
                assert window.isVisible() and not page._closed
                with patch.object(gui, "russian_question", return_value=QMessageBox.Discard):
                    started = time.monotonic()
                    window.close()
                assert time.monotonic() - started < 1
                assert page._closed and not page._timer.isActive()
                finish_request.set()
                wait_for(worker_finished.is_set)
                QTest.qWait(50)
                assert page._events.empty()
                assert before == {path.name: path.read_bytes() for path in root.iterdir()}
            finally:
                finish_request.set()
        # Also verify catalog teardown when the Qt object is actually destroyed.
        with patch.object(gui, "data_dir", return_value=root):
            second = gui.MainWindow()
        catalog_started, catalog_finish, catalog_done = Event(), Event(), Event()
        second_page = second.testing_page
        def slow_catalog(req, timeout):
            catalog_started.set()
            assert catalog_finish.wait(4)
            catalog_done.set()
            return response({"data": [{"id": "late/model"}]})
        with patch.object(testing.urllib.request, "urlopen", side_effect=slow_catalog):
            try:
                second_page.refresh_catalog()
                wait_for(catalog_started.is_set)
                second.close()
                second.deleteLater()
                app.processEvents()
                catalog_finish.set()
                wait_for(catalog_done.is_set)
                QTest.qWait(50)
                assert second_page._events.empty()
            finally:
                catalog_finish.set()
    print("Testing GUI works: drafts, catalog, comparisons, errors and teardown.")


if __name__ == "__main__":
    main()
