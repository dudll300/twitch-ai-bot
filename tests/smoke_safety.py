"""Security page, drafts, navigation, errors, diagnosis cancellation; no real API."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from threading import Event
import time
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QSize, QProcess
from PySide6.QtGui import QFontDatabase, QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import gui
from message_history import MessageHistory
from safety import SafetyReview


def wait_for(predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(20)
    assert predicate(), 'GUI worker did not finish'


def response(value):
    return io.BytesIO(json.dumps({'choices': [{'message': {'content': json.dumps(value)}}]}).encode())


def main():
    app = QApplication([])
    for filename in ('segoeui.ttf', 'seguisb.ttf'):
        font_path = Path('C:/Windows/Fonts') / filename
        if font_path.exists():
            QFontDatabase.addApplicationFont(str(font_path))
    app.setFont(QFont('Segoe UI', 10))
    app.setStyleSheet(gui.STYLE)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        with patch.object(gui, 'data_dir', return_value=root), patch.object(gui, 'available_screen_size', return_value=QSize(1400, 950)):
            window = gui.MainWindow()
        window.show()
        page = window.safety_page
        window._navigate(gui.PAGE_SAFETY)
        assert window.pages.currentWidget() is page
        assert window.nav_buttons[gui.PAGE_SAFETY].text() == 'Безопасность'
        assert 'ещё не выполнялась' in page.last_review.text()
        assert 'Локальные проверки: действуют' in page.state.text()
        with patch('urllib.request.urlopen') as network:
            for index in range(len(gui.PAGES)):
                window._navigate(index)
                app.processEvents()
            network.assert_not_called()
        page.link_mode.setCurrentIndex(1)
        page.domains.setPlainText('TWITCH.TV\ntwitch.tv')
        page.model_mode.setCurrentIndex(1)
        page.model_catalog.combo.setCurrentText('review/manual')
        page.timeout.setValue(12)
        assert page.dirty
        for index in (gui.PAGE_CONNECTION, gui.PAGE_TESTING, gui.PAGE_HISTORY, gui.PAGE_SAFETY):
            window._navigate(index)
        assert page.model_catalog.combo.currentText() == 'review/manual'
        page.model_catalog._replace_models((__import__('testing').Model('other'),))
        assert page.model_catalog.combo.currentText() == 'review/manual'
        page.domains.setPlainText('https://twitch.tv')
        assert not page.apply() and 'без протокола' in page.error.text()
        page.domains.setPlainText('TWITCH.TV\ntwitch.tv')
        with patch('safety_settings.os.replace', side_effect=OSError('disk')):
            assert not page.apply()
        assert page.dirty and page.domains.toPlainText() == 'TWITCH.TV\ntwitch.tv'
        assert 'Черновик' in page.error.text()
        # Applying security while the bot process is running is allowed.
        with patch.object(window.process, 'state', return_value=QProcess.Running):
            assert page.apply()
        assert not page.dirty
        assert page.store.snapshot().settings.allowed_domains == ('twitch.tv',)
        page.reset_button.click()
        assert page.dirty and page.timeout.value() == 8
        page.test_button.click()
        assert window.pages.currentIndex() == gui.PAGE_TESTING
        assert window.testing_page.mode.currentIndex() == 1
        diagnostic = window.testing_page.diagnostic
        diagnostic.kind.setCurrentIndex(1)
        diagnostic.text.setPlainText('twitch.tv')
        with patch('urllib.request.urlopen') as network:
            diagnostic.local_button.click()
            assert 'Локальные проверки пройдены' in diagnostic.result.text()
            network.assert_not_called()
        assert not (root / 'message-history.sqlite3').exists()
        window.api_key.setText('fake-test-key')
        captured = []
        def http(req, timeout):
            captured.append(json.loads(req.data))
            return response({'allowed': True, 'reasons': []})
        with patch('urllib.request.urlopen', side_effect=http):
            diagnostic.ai_button.click()
            wait_for(lambda: not diagnostic._busy)
        assert len(captured) == 1 and captured[0]['model'] == 'review/manual'
        assert json.loads(captured[0]['messages'][-1]['content'])['candidate'] == 'twitch.tv'
        assert not (root / 'message-history.sqlite3').exists()
        release, started = Event(), Event()
        def late(req, timeout):
            started.set()
            release.wait(4)
            return response({'allowed': True, 'reasons': []})
        try:
            with patch('urllib.request.urlopen', side_effect=late):
                diagnostic.ai_button.click()
                wait_for(started.is_set)
                window.testing_page.mode.setCurrentIndex(0)
                window.testing_page.mode.setCurrentIndex(1)
                diagnostic.text.setPlainText('Новый запрос')
                diagnostic.local_button.click()
                new_result = diagnostic.result.text()
                release.set()
                QTest.qWait(200)
                assert diagnostic.result.text() == new_result
        finally:
            release.set()
        journal = MessageHistory(root)
        record_id = journal.add('reward', 'rejected', question='Привет')
        journal.safety_event('reward', SafetyReview('error', '', ('review_unavailable',), ai_attempted=True, model='review/manual'), record_id=record_id)
        page.refresh()
        wait_for(lambda: not page._loading)
        assert page.decisions.count() == 1
        assert 'Опасность ответа не установлена' in page.decisions.item(0).text()
        assert 'ошибка' in page.last_review.text()
        page.decisions.setCurrentRow(0)
        page.history_button.click()
        assert window.pages.currentIndex() == gui.PAGE_HISTORY
        assert window.history_page.kind.currentData() == 'reward'
        window._test_prompt()
        assert window.pages.currentIndex() == gui.PAGE_TESTING and window.testing_page.mode.currentIndex() == 0
        # A damaged file remains visible and repairable without credentials.
        page.store.path.write_text('{broken')
        with patch.object(gui, 'data_dir', return_value=root), patch.object(gui, 'available_screen_size', return_value=QSize(1200, 850)):
            damaged = gui.MainWindow()
        damaged._navigate(gui.PAGE_SAFETY)
        assert 'Ошибка загрузки' in damaged.safety_page.settings_state.text()
        assert damaged.safety_page.apply()
        if os.environ.get('SAFETY_CAPTURE'):
            window.safety_page._fill(window.safety_page.applied.settings)
            window._navigate(gui.PAGE_SAFETY)
            window._set_sidebar(False, animate=False)
            window.resize(1100, 820)
            QTest.qWait(200)
            window.grab().save(os.environ['SAFETY_CAPTURE'])
        for instance in (window, damaged):
            instance._dirty = False
            instance.safety_page._fill(instance.safety_page.applied.settings)
            instance.close()
        print('Security GUI: drafts, live apply, errors, exact diagnosis, cancellation and navigation passed.')


if __name__ == '__main__':
    main()
