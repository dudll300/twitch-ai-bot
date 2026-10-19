"""Provider switching, async catalogs, immutable drafts and compact theme checks."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from threading import Event, get_ident
import time
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPalette, QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox, QLabel
import ai_connection
from ai_connection_gui import ConnectionEditor
from configuration import env_content, read_config
import gui


def response(value):
    return io.BytesIO(json.dumps(value, ensure_ascii=False).encode())


def completion(value):
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False)
    return response({'choices': [{'message': {'content': value}, 'finish_reason': 'stop'}]})


def wait_for(predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(15)
    assert predicate(), 'Provider GUI worker did not finish'


def main():
    app = QApplication([])
    app.setStyle('Fusion')
    for name in ('segoeui.ttf', 'seguisb.ttf'):
        path = Path('C:/Windows/Fonts') / name
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    app.setFont(QFont('Segoe UI', 10))
    app.setStyleSheet(gui.STYLE)
    main_thread = get_ident()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        root.joinpath('.env').write_text(env_content(dict(AI_API_KEY='saved-compatible-key',
            AI_MODEL='compatible-answer', AI_FALLBACK_MODELS='compatible-backup',
            TWITCH_CHANNEL='channel', TWITCH_BOT_NAME='helper', TWITCH_CLIENT_ID='client')), encoding='utf-8')
        for name in ('.twitch_token.json', '.twitch_broadcaster_token.json'):
            root.joinpath(name).write_bytes(b'fake fixture token')
        with patch.object(gui, 'data_dir', return_value=root), patch.object(gui, 'available_screen_size', return_value=QSize(1280, 960)), patch('ai_connection.urlopen') as network:
            window = gui.MainWindow()
            window.show()
            app.processEvents()
            assert window.api_key.echoMode() == QLineEdit.Password
            assert window._test_credentials().api_key == 'saved-compatible-key'
            window.provider.setCurrentIndex(window.provider.findData('openai'))
            assert not window.connection_editor.base_field.isVisible()
            assert window.connection_editor.official.isVisible()
            assert window.model.currentText() == 'gpt-4.1-mini'
            assert window.prompt_builder.model_catalog.combo.currentText() == 'gpt-4.1-mini'
            assert window.fallback_models.text() == ''
            try:
                window._test_credentials()
                raise AssertionError('Compatible key leaked into OpenAI')
            except ValueError:
                pass
            window.api_key.setText('draft-official-key')
            original_auth = window._test_credentials()
            window.prompt_builder.model_catalog.combo.setCurrentText('gpt-5-mini')
            window.provider.setCurrentIndex(window.provider.findData('compatible'))
            assert window.api_key.text() == ''
            assert window._test_credentials().api_key == 'saved-compatible-key'
            assert window.model.currentText() == 'compatible-answer'
            assert window.fallback_models.text() == 'compatible-backup'
            window.model.setCurrentText('draft-compatible-answer')
            window.api_key.setText('draft-compatible-key')
            window.provider.setCurrentIndex(window.provider.findData('openai'))
            assert window.api_key.text() == 'draft-official-key'
            assert window.prompt_builder.model_catalog.combo.currentText() == 'gpt-5-mini'
            assert window._test_credentials().identity != original_auth.identity  # A -> B -> A
            assert window._save()
            raw = read_config(root / '.env')
            assert raw['AI_COMPATIBLE_API_KEY'] == 'draft-compatible-key'
            assert raw['AI_COMPATIBLE_MODEL'] == 'draft-compatible-answer'
            assert raw['AI_OPENAI_API_KEY'] == 'draft-official-key'
            assert raw['AI_OPENAI_PROMPT_MODEL'] == 'gpt-5-mini'
            assert window.api_key.text() == ''
            assert 'сохранён' in window.api_key.placeholderText()
            for name in ('.twitch_token.json', '.twitch_broadcaster_token.json'):
                assert root.joinpath(name).read_bytes() == b'fake fixture token'
            network.assert_not_called()

        def catalog(request, timeout):
            assert get_ident() != main_thread
            assert request.full_url == 'https://api.openai.com/v1/models'
            assert request.get_header('Authorization') == 'Bearer draft-official-key'
            return response({'data': [{'id': 'gpt-4.1-mini'}, {'id': 'text-embedding-3-small'}, {'id': 'manual-other'}]})
        with patch('ai_connection.urlopen', side_effect=catalog) as network:
            window.model_catalog.refresh()
            window.model_catalog.refresh()
            wait_for(lambda: not window.model_catalog._busy)
            assert network.call_count == 1
            index = window.model.findData('text-embedding-3-small')
            assert not window.model.model().item(index).isEnabled()
            assert 'не гарантирует' in window.model_catalog.status.text()

        # Old requests cannot replace A's new catalog after A -> B -> A.
        started, release, finished = Event(), Event(), Event()
        def stale(request, timeout):
            started.set()
            try:
                assert release.wait(5)
                return response({'data': [{'id': 'stale-only'}]})
            finally:
                finished.set()
        for control in (window.model_catalog, window.testing_page):
            started.clear(); release.clear(); finished.clear()
            with patch('ai_connection.urlopen', side_effect=stale):
                control.refresh() if control is window.model_catalog else control.refresh_catalog()
                wait_for(started.is_set)
                window.provider.setCurrentIndex(window.provider.findData('compatible'))
                window.provider.setCurrentIndex(window.provider.findData('openai'))
                release.set()
                wait_for(finished.is_set)
                QTest.qWait(90)
            assert window.model.findData('stale-only') == -1
            assert all(window.testing_page.models.item(i).data(Qt.UserRole) != 'stale-only'
                       for i in range(window.testing_page.models.count()))

        # Comparisons fix provider, key, models and prompt once, retaining source labels.
        page = window.testing_page
        page.manual.setText('exact/a'); page.add_manual()
        page.manual.setText('exact/b'); page.add_manual()
        window.prompt.setPlainText('Несохранённый общий характер')
        page.question.setPlainText('Привет')
        captured = []
        started.clear(); release.clear()
        def compare(request, timeout):
            assert get_ident() != main_thread
            payload = json.loads(request.data)
            captured.append((request.full_url, request.get_header('Authorization'), payload))
            if payload['model'] == 'exact/a' and not started.is_set():
                started.set()
                assert release.wait(5)
            return completion({'allowed': True, 'reasons': []} if payload['max_completion_tokens'] == 160 else 'Синтетический ответ')
        with patch('ai_connection.urlopen', side_effect=compare):
            page.start_test()
            wait_for(started.is_set)
            window.provider.setCurrentIndex(window.provider.findData('compatible'))
            window.prompt.setPlainText('Изменён после старта')
            release.set()
            wait_for(lambda: not page._testing_busy)
        assert len(captured) == 4
        assert all(url == 'https://api.openai.com/v1/chat/completions' and key == 'Bearer draft-official-key'
                   for url, key, _ in captured)
        assert 'OpenAI' in page.context.text()
        assert 'Синтетический ответ' in page._result_widgets['exact/a'].toPlainText()
        assert 'Несохранённый общий характер' in str(captured[0][2])
        assert 'Изменён после старта' not in str(captured)

        # A changed compatible origin requires a deliberately entered key.
        window.base_url.setText('https://other.invalid/v1')
        try:
            window._test_credentials()
            raise AssertionError('Saved compatible key moved to a different origin')
        except ValueError:
            pass
        window.api_key.setText('key-for-other-origin')
        assert window._test_credentials().base_url == 'https://other.invalid/v1'
        assert original_auth.base_url == 'https://api.openai.com/v1'
        with patch.object(gui, 'russian_question', return_value=QMessageBox.Discard):
            window.close()

        # The new form inherits Qt/theme colors and wraps at compact widths.
        app.setStyleSheet('')
        for theme in ('dark', 'light'):
            palette = app.style().standardPalette()
            if theme == 'dark':
                for role in (QPalette.Window, QPalette.Base, QPalette.Button):
                    palette.setColor(role, QColor('#14161B'))
                for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
                    palette.setColor(role, QColor('#F3F5FA'))
                palette.setColor(QPalette.PlaceholderText, QColor('#AAB1BD'))
            app.setPalette(palette)
            values = ai_connection.migrate(read_config(root / '.env'))
            editor = ConnectionEditor(values, lambda: read_config(root / '.env'))
            editor.resize(440, 720)
            editor.show()
            app.processEvents()
            for provider in ('openai', 'compatible'):
                editor.provider.setCurrentIndex(editor.provider.findData(provider))
                app.processEvents()
                for child in (editor.provider, editor.model, editor.api_key, editor.fallback_models):
                    assert child.mapTo(editor, child.rect().topLeft()).x() >= 0
                    assert child.mapTo(editor, child.rect().topRight()).x() < editor.width()
                assert editor.api_key.echoMode() == QLineEdit.Password
            if os.environ.get('PROVIDERS_CAPTURE'):
                editor.provider.setCurrentIndex(editor.provider.findData('openai'))
                QTest.qWait(80)
                editor.grab().save(os.environ['PROVIDERS_CAPTURE'] + '-' + theme + '.png')
            editor.model_catalog.shutdown()
            editor.close()
    print('Provider GUI passed: isolated keys/models, no automatic requests, ABA, snapshots and both palettes.')


if __name__ == '__main__':
    main()
