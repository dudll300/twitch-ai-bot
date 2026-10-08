"""Security settings and exact-text diagnostic mode. Workers never access Qt."""
from dataclasses import asdict
from datetime import datetime
from queue import Queue, Empty
from threading import Thread, Event

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton, QHBoxLayout, QLineEdit, QSpinBox

from safety_settings import PolicyStore, SafetySettings, validate_settings
from safety import SafetyReview, describe_review
from safety_diagnostics import diagnose
from message_history import MessageHistory
from model_catalog_gui import ModelCatalogControl
from ui_widgets import card, field, label, scroll_page, NoWheelComboBox, ScrollPlainTextEdit, ScrollListWidget


def plain(text='', role='muted'):
    widget = label(text, role, True)
    widget.setTextFormat(Qt.PlainText)
    return widget


PROTECTIONS = (
    ('Контакты, адреса и документы', 'Вопрос, подготовленный контекст, полный ответ и история · локально. Распознаваемые данные отклоняются до AI и публикации, в истории скрываются. Ники, игровые числа и обсуждение устройств допустимы.'),
    ('Платёжные данные', 'Вопрос, контекст, ответ и история · локально. Распознаваемые номера карт и платёжные реквизиты блокируются; в истории скрываются.'),
    ('Пароли, токены и API-ключи', 'Вопрос, контекст, ответ и история · локально. Известный ключ подключения и распознаваемые секреты не публикуются, в истории скрываются.'),
    ('Попытки раскрыть приватные сведения', 'Вопрос · локально; ответ · локально и AI. Распознаваемые запросы раскрытия отклоняются, готовый ответ отдельно оценивается на раскрытие приватного контекста.'),
    ('Ссылки', 'Полный кандидат и готовый ответ, включая финальную отправку · локально. По умолчанию запрещены; разрешённый список допускает только точные домены и HTTPS либо домен без протокола.'),
    ('Команды и опасные управляющие символы', 'Полный кандидат и готовый ответ · локально. Недопустимые команды, символы, длина и неверное обращение к адресату запрещают публикацию.'),
    ('Угрозы, травля и мошенничество', 'Готовый ответ · AI-оценка. Ответ отклоняется при отрицательной оценке. Ошибка проверки означает недоступность, а не установленную опасность.'),
    ('Серьёзные личные обвинения и приватные утверждения', 'Готовый ответ · AI-оценка. Отклоняются обвинения, диагнозы и утверждения о личной жизни конкретных людей, раскрытие приватных инструкций.'),
    ('Актуальность контекста после модерации Twitch', 'Самостоятельная реплика и её контекст · состояние Twitch. Удаление сообщения, тайм-аут или очистка чата отменяют связанные ещё не отправленные реплики и удаляют связанный временный контекст.'),
)
SCENARIOS = {'reward': 'Награда', 'autonomous': 'Самостоятельная реплика', 'preview': 'Предпросмотр'}
STAGES = {'question': 'Вопрос', 'answer': 'Ответ', 'ai_review': 'AI-оценка', 'publication': 'Публикация'}


class SafetyPage(QWidget):
    changed = Signal()
    test_requested = Signal()
    history_requested = Signal(str)

    def __init__(self, root, get_credentials, get_answer_model):
        super().__init__()
        self.store = PolicyStore(root)
        self.journal = MessageHistory(root)
        self.get_answer_model = get_answer_model
        self._events, self._closed = Queue(), Event()
        self._loading = False
        self._rows = []
        self.applied = self.store.snapshot()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(16)
        status, box = card('Состояние проверок')
        self.state = plain()
        self.last_review = plain('Последний результат AI-оценки: ещё не выполнялась')
        self.settings_state = plain()
        for item in (self.state, self.last_review, self.settings_state):
            box.addWidget(item)
        box.addWidget(plain('AI-оценка добавляет один запрос на подготовленный кандидат ответа. Входящие сообщения чата не проверяются отдельным AI-запросом каждое. Наличие ключа не подтверждает доступность модели.'))
        layout.addWidget(status)
        protection, box = card('Действующие защиты')
        for title, description in PROTECTIONS:
            box.addWidget(plain(title, 'body'))
            box.addWidget(plain(description))
        box.addWidget(plain('Мат и дружеские игровые подколы сами по себе разрешены. Характер общения настраивается в «Поведении». Распознавание ограничено известными шаблонами и оценкой модели; все обходы распознать невозможно.'))
        layout.addWidget(protection)
        settings, box = card('Ссылки и AI-оценка')
        self.link_mode = NoWheelComboBox()
        self.link_mode.addItem('Запретить ссылки', 'block_all')
        self.link_mode.addItem('Разрешать только указанные домены', 'allow_domains')
        box.addWidget(field('Политика ссылок', self.link_mode))
        self.domains = ScrollPlainTextEdit()
        self.domains.setAccessibleName('Разрешённые домены, один на строку')
        self.domains.setMinimumHeight(90)
        self.domains.setMaximumHeight(160)
        box.addWidget(field('Разрешённые домены — один на строку', self.domains))
        box.addWidget(plain('Без https://, пути и параметров. Только точное совпадение: www.twitch.tv нужно добавить отдельно от twitch.tv. Unicode сопоставляется через IDNA. Список не отменяет остальные защиты. Неподдерживаемые схемы запрещены.'))
        self.model_mode = NoWheelComboBox()
        self.model_mode.addItem('Использовать модель ответа', 'same_as_answer')
        self.model_mode.addItem('Выбрать отдельную модель', 'separate')
        box.addWidget(field('Модель AI-оценки', self.model_mode))
        self.model_catalog = ModelCatalogControl('', get_credentials)
        self.model_catalog.combo.setAccessibleName('Отдельная модель AI-оценки')
        box.addWidget(self.model_catalog)
        self.timeout = QSpinBox()
        self.timeout.setRange(2, 15)
        self.timeout.setSuffix(' с')
        box.addWidget(field('Время ожидания AI-проверки', self.timeout))
        box.addWidget(plain('В самостоятельном режиме время дополнительно ограничено актуальностью разговора. Ошибка или недоступность оценки не разрешает публикацию. Модель генерации не меняется.'))
        self.error = plain('', 'error')
        box.addWidget(self.error)
        buttons = QHBoxLayout()
        self.apply_button = QPushButton('Применить')
        self.reset_button = QPushButton('Сбросить настройки')
        buttons.addWidget(self.apply_button)
        buttons.addWidget(self.reset_button)
        buttons.addStretch()
        box.addLayout(buttons)
        layout.addWidget(settings)
        recent, box = card('Последние решения')
        self.recent_status = plain('Ещё нет решений реальной работы бота.')
        box.addWidget(self.recent_status)
        self.decisions = ScrollListWidget()
        self.decisions.setAccessibleName('Последние решения безопасности')
        self.decisions.setMinimumHeight(180)
        self.decisions.setMaximumHeight(300)
        self.decisions.setWordWrap(True)
        box.addWidget(self.decisions)
        self.history_button = QPushButton('Открыть историю')
        self.history_button.clicked.connect(self.open_history)
        box.addWidget(self.history_button)
        layout.addWidget(recent)
        test, box = card('Проверка произвольного текста')
        box.addWidget(plain('Откроется отдельный режим «Тестирования». Локальная проверка бесплатна и доступна без подключения. Используется применённая политика; черновик нужно сначала явно применить. Текст не публикуется и не сохраняется в историю или память.'))
        self.test_button = QPushButton('Проверить текст')
        self.test_button.clicked.connect(self.test_requested.emit)
        box.addWidget(self.test_button)
        layout.addWidget(test)
        outer.addWidget(scroll_page(body))
        self._fill(self.applied.settings)
        for signal in (self.link_mode.currentIndexChanged, self.domains.textChanged, self.model_mode.currentIndexChanged,
                       self.model_catalog.combo.currentTextChanged, self.timeout.valueChanged):
            signal.connect(self._changed)
        self.apply_button.clicked.connect(self.apply)
        self.reset_button.clicked.connect(lambda: self._fill(SafetySettings()))
        self._timer = QTimer(self)
        self._timer.setInterval(1500)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self._poll = QTimer(self)
        self._poll.setInterval(80)
        self._poll.timeout.connect(self._drain)
        self._poll.start()
        self._changed()
        self.refresh()

    def _fill(self, settings):
        self.link_mode.setCurrentIndex(self.link_mode.findData(settings.link_mode))
        self.domains.setPlainText('\n'.join(settings.allowed_domains))
        self.model_mode.setCurrentIndex(self.model_mode.findData(settings.review_model_mode))
        self.model_catalog.combo.setCurrentText(settings.review_model)
        self.timeout.setValue(settings.review_timeout_seconds)
        self._changed()

    def draft(self):
        return {'schema_version': 1, 'link_mode': self.link_mode.currentData(),
                'allowed_domains': [line.strip() for line in self.domains.toPlainText().splitlines() if line.strip()],
                'review_model_mode': self.model_mode.currentData(), 'review_model': self.model_catalog.combo.currentText(),
                'review_timeout_seconds': self.timeout.value()}

    @property
    def dirty(self):
        applied = asdict(self.applied.settings)
        applied['allowed_domains'] = list(applied['allowed_domains'])
        return self.draft() != applied

    def _changed(self, *_):
        self.model_catalog.set_editable(self.model_mode.currentData() == 'separate')
        self.domains.setEnabled(self.link_mode.currentData() == 'allow_domains')
        model = self.applied.settings.review_model if self.applied.settings.review_model_mode == 'separate' else 'фактическая модель каждого ответа (основная: ' + self.get_answer_model() + ')'
        self.state.setText('Локальные проверки: действуют\nAI-оценка: ' + model)
        self.settings_state.setText(self.applied.error or ('Есть несохранённые изменения — диагностика использует применённые настройки' if self.dirty else 'Настройки применены') + '\nВерсия политики: ' + self.applied.version[:12])
        self.error.setText(self.applied.error)
        self.changed.emit()

    def apply(self):
        try:
            settings = validate_settings(self.draft())
            auth_key = ''
            try:
                auth_key = self.model_catalog.get_credentials().api_key
            except (OSError, ValueError):
                pass
            if auth_key and auth_key in settings.review_model:
                raise ValueError('В поле модели указан API-ключ. Удалите его.')
        except ValueError as exc:
            self.error.setText(str(exc))
            return False
        try:
            self.applied = self.store.apply(settings)
        except (OSError, ValueError):
            self.error.setText('Не удалось сохранить настройки. Черновик сохранён в форме; повторите применение.')
            return False
        self._fill(self.applied.settings)
        self.error.setText('')
        return True

    def refresh(self):
        if self._closed.is_set() or self._loading:
            return
        self._loading = True
        journal, output, closed, store = self.journal, self._events, self._closed, self.store
        def read():
            try:
                result, error = journal.safety_recent(200), ''
            except Exception:
                result, error = [], 'Не удалось прочитать последние решения. Исходная история сохранена.'
            if not closed.is_set():
                output.put((result, error, store.snapshot()))
        Thread(target=read, daemon=True).start()

    def _drain(self):
        if self._closed.is_set():
            return
        try:
            rows, error, policy = self._events.get_nowait()
        except Empty:
            return
        self._loading = False
        if policy != self.applied:
            preserve = self.dirty or bool(policy.error)
            self.applied = policy
            if not preserve:
                self._fill(policy.settings)
            else:
                self._changed()
        self._rows = rows[:20]
        self.decisions.clear()
        self.recent_status.setText(error or ('Последние 20 решений. Только безопасные метаданные.' if rows else 'Ещё нет решений реальной работы бота.'))
        for row in self._rows:
            review = SafetyReview(row['status'], '', row['reasons'])
            ai = ('AI-оценка: ' + row['model']) if row['ai_attempted'] else 'AI-оценка не выполнялась'
            self.decisions.addItem(datetime.fromtimestamp(row['time']).strftime('%H:%M:%S') + ' · ' + SCENARIOS[row['scenario']] + ' · ' + STAGES.get(row['stage'], 'Проверка') + '\n' + describe_review(review) + '\n' + ai)
        last = next((row for row in rows if row['ai_attempted']), None)
        if last:
            state = 'успешно' if last['status'] in ('allowed', 'blocked') else 'отменена' if last['status'] == 'cancelled' else 'ошибка'
            self.last_review.setText('Последний результат AI-оценки: ' + state + ' · ' + last['model'])

    def open_history(self):
        selected = self.decisions.currentRow()
        row = self._rows[selected] if 0 <= selected < len(self._rows) else None
        self.history_requested.emit(row['scenario'] if row else '')

    def shutdown(self):
        self._closed.set()
        self._timer.stop()
        self._poll.stop()
        self.model_catalog.shutdown()


class DiagnosticPanel(QWidget):
    def __init__(self, store, get_credentials, get_answer_model):
        super().__init__()
        self.store, self.get_credentials, self.get_answer_model = store, get_credentials, get_answer_model
        self._events, self._cancel = Queue(), Event()
        self._generation, self._closed, self._busy = 0, False, False
        outer = QVBoxLayout(self)
        body = QWidget()
        layout = QVBoxLayout(body)
        form, box = card('Проверка точного текста')
        self.policy_label = plain()
        box.addWidget(self.policy_label)
        box.addWidget(plain('Черновик из «Безопасности» не используется. Сначала нажмите там «Применить». Локальная проверка не вызывает API. AI-проверка расходует один запрос оценки; новый ответ не генерируется.'))
        self.kind = NoWheelComboBox()
        self.kind.addItem('Вопрос зрителя', 'question')
        self.kind.addItem('Готовый ответ бота', 'answer')
        box.addWidget(field('Что проверяем', self.kind))
        self.text = ScrollPlainTextEdit()
        self.text.setMinimumHeight(130)
        box.addWidget(field('Точный текст — без обрезки и исправлений', self.text))
        self.target = QLineEdit()
        self.target.setPlaceholderText('viewer (необязательно; текст должен начинаться с @viewer )')
        box.addWidget(field('Адресат готового ответа', self.target))
        buttons = QHBoxLayout()
        self.local_button, self.ai_button, self.cancel_button = QPushButton('Проверить локально'), QPushButton('Проверить с AI'), QPushButton('Отменить')
        for button in (self.local_button, self.ai_button, self.cancel_button):
            buttons.addWidget(button)
        box.addLayout(buttons)
        self.result = plain('', 'body')
        box.addWidget(self.result)
        layout.addWidget(form)
        layout.addStretch()
        outer.addWidget(scroll_page(body))
        self.kind.currentIndexChanged.connect(self.invalidate)
        self.text.textChanged.connect(self.invalidate)
        self.target.textChanged.connect(self.invalidate)
        self.local_button.clicked.connect(lambda: self.run(False))
        self.ai_button.clicked.connect(lambda: self.run(True))
        self.cancel_button.clicked.connect(self.invalidate)
        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        self.invalidate()

    def invalidate(self, *_):
        self._cancel.set()
        self._generation += 1
        self._busy = False
        self.result.setText('Проверка отменена.' if self.cancel_button.isEnabled() else '')
        self.ai_button.setEnabled(self.kind.currentData() == 'answer')
        self.target.setEnabled(self.kind.currentData() == 'answer')
        self.cancel_button.setEnabled(False)
        self.local_button.setEnabled(True)

    def run(self, ai):
        if self._closed or self._busy:
            return
        self.invalidate()
        policy = self.store.snapshot()
        text, kind, target = self.text.toPlainText(), self.kind.currentData(), self.target.text()
        auth, model = None, self.get_answer_model()
        try:
            auth = self.get_credentials()
        except (OSError, ValueError):
            pass
        # Run local first, even when credentials are missing; no paid call on a
        # locally rejected candidate and no modal prompt for every rejection.
        local = diagnose(self.store, policy, text, kind, target=target, auth=auth)
        if not ai or local.status != 'local_allowed' or kind != 'answer':
            self.show_result(local)
            return
        try:
            auth = self.get_credentials()
        except (OSError, ValueError):
            self.result.setText('AI-проверка недоступна: заполните подключение. Локальные проверки пройдены.')
            return
        self._cancel = Event()
        cancel, generation, output, store = self._cancel, self._generation, self._events, self.store
        self._busy = True
        self.ai_button.setEnabled(False)
        self.local_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.result.setText('Выполняется один платный запрос AI-оценки…')
        def work():
            try:
                result = diagnose(store, policy, text, kind, target=target, ai=True, auth=auth, answer_model=model, cancelled=cancel.is_set)
            except Exception:
                result = SafetyReview('error', '', ('review_unavailable',), policy_version=policy.version)
            if not cancel.is_set():
                output.put((generation, result))
        Thread(target=work, daemon=True).start()

    def show_result(self, review):
        self._busy = False
        self.local_button.setEnabled(True)
        self.ai_button.setEnabled(self.kind.currentData() == 'answer')
        self.cancel_button.setEnabled(False)
        self.result.setText(STAGES.get(review.stage, 'Проверка') + ': ' + describe_review(review) + '\n' +
            ('AI-оценка: ' + review.model + (f' · {review.seconds:.2f} с' if review.seconds is not None else '') if review.ai_attempted else 'AI-запрос не выполнялся') + '\nВерсия политики: ' + review.policy_version[:12])

    def _poll(self):
        if self._closed:
            return
        policy = self.store.snapshot()
        setting = policy.settings
        model = setting.review_model if setting.review_model_mode == 'separate' else self.get_answer_model()
        self.policy_label.setText(policy.error or f'Применённая политика: {policy.version[:12]} · ' + ('ссылки запрещены' if setting.link_mode == 'block_all' else 'только указанные домены') + f' · модель оценки: {model} · ожидание: {setting.review_timeout_seconds} с')
        while True:
            try:
                generation, review = self._events.get_nowait()
            except Empty:
                return
            if generation == self._generation:
                if review.policy_version != policy.version:
                    review = SafetyReview('cancelled', '', ('policy_changed',), policy_version=policy.version)
                self.show_result(review)

    def hideEvent(self, event):
        self.invalidate()
        super().hideEvent(event)

    def shutdown(self):
        self._closed = True
        self.invalidate()
        self._timer.stop()
