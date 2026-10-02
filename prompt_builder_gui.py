"""Prompt drafts: explicit apply/undo, local checks and cancellable HTTP worker."""

from queue import Empty, Queue
from threading import Event, Thread

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import QCheckBox, QGridLayout, QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout, QWidget

from ai_client import clean_text, redact_secret
from model_catalog_gui import ModelCatalogControl
from prompt_builder import (DEFAULT_TOPICS, GENERATOR_MODEL, PRESETS, check_prompt, compose_prompt,
                            generate_prompt, prepare_generation, style_for)
from ui_widgets import NoWheelComboBox, ScrollPlainTextEdit, card, field, label


def generation_worker(snapshot, output, cancel):
    try:
        result = (snapshot.auth, generate_prompt(snapshot), "")
    except Exception as exc:
        result = (snapshot.auth, "", redact_secret(str(exc), snapshot.auth.api_key))
    if not cancel.is_set():
        output.put(result)


class PromptBuilder(QWidget):
    test_requested = Signal(str)
    connection_requested = Signal()

    def __init__(self, get_credentials, get_prompt, set_prompt):
        super().__init__()
        self.get_credentials, self.get_prompt, self.set_prompt = get_credentials, get_prompt, set_prompt
        self._events, self._cancel = Queue(), Event()
        self._closed, self._busy, self._editable = False, False, True
        self._previous = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(20)
        controls, layout = card("Пресеты и генератор", "Подготовьте новый вариант, проверьте его и примените вручную")
        grid = QGridLayout()
        self.preset = NoWheelComboBox()
        self.preset.addItems([name for name, _ in PRESETS])
        self.humor = NoWheelComboBox()
        self.humor.addItems(("По просьбе", "Иногда", "Часто"))
        self.humor.setCurrentIndex(1)
        self.profanity = NoWheelComboBox()
        self.profanity.addItems(("Без мата", "Изредка", "Свободно"))
        grid.addWidget(field("Основа", self.preset), 0, 0)
        grid.addWidget(field("Юмор", self.humor), 0, 1)
        grid.addWidget(field("Ненормативная лексика", self.profanity), 0, 2)
        for column in range(3):
            grid.setColumnStretch(column, 1)
        layout.addLayout(grid)
        self.topics = QLineEdit(DEFAULT_TOPICS)
        self.topics.setMaxLength(400)
        layout.addWidget(field("Темы канала", self.topics))
        self.preset_button = QPushButton("Показать пресет · без AI API")
        self.preset_button.clicked.connect(self.show_preset)
        layout.addWidget(self.preset_button, 0, Qt.AlignLeft)
        self.wishes = ScrollPlainTextEdit()
        self.wishes.setPlaceholderText("Например: часто шути, используй мат и подкалывай зрителей, но отвечай на вопросы по делу")
        self.wishes.setMinimumHeight(85)
        self.wishes.setMaximumHeight(130)
        layout.addWidget(field("Пожелания для генерации", self.wishes))
        layout.addWidget(label("Явные пожелания могут переопределять тон, юмор и лексику выбранной основы.", "muted", True))
        self.improve = QCheckBox("Улучшить текущий общий промпт с учётом пожеланий")
        layout.addWidget(self.improve)
        self.model_catalog = ModelCatalogControl(GENERATOR_MODEL, get_credentials)
        self.model_catalog.combo.setAccessibleName("Модель для генерации промпта")
        layout.addWidget(field("Модель для генерации · отдельная от модели ответов", self.model_catalog))
        layout.addWidget(label(
            "Каталог загружается по кнопке. Укажите точный ID доступной модели; автоматической замены нет. "
            "Генерация и проверка ответов расходуют баланс AI API. Пресеты бесплатны и работают без ключа.", "muted", True))
        buttons = QHBoxLayout()
        self.generate_button = QPushButton("Сгенерировать промпт")
        self.generate_button.setObjectName("primary")
        self.generate_button.clicked.connect(self.start_generation)
        buttons.addWidget(self.generate_button)
        self.connection_button = QPushButton("К подключению")
        self.connection_button.clicked.connect(lambda: self.connection_requested.emit())
        buttons.addWidget(self.connection_button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.status = label("Защита темы и служебных инструкций добавляется в каждый пресет и результат генерации.", "muted", True)
        self.status.setTextFormat(Qt.PlainText)
        layout.addWidget(self.status)
        outer.addWidget(controls)

        self.draft_card, draft_layout = card("Новый вариант промпта", "Текущий общий промпт не меняется до нажатия «Применить»")
        self.preview = ScrollPlainTextEdit()
        self.preview.setMinimumHeight(220)
        self.preview.setMaximumHeight(360)
        draft_layout.addWidget(self.preview)
        self.validation = label("", "muted", True)
        self.validation.setTextFormat(Qt.PlainText)
        draft_layout.addWidget(self.validation)
        draft_buttons = QHBoxLayout()
        self.apply_button = QPushButton("Применить к общему промпту")
        self.apply_button.clicked.connect(self.apply_draft)
        draft_buttons.addWidget(self.apply_button)
        self.test_button = QPushButton("Проверить этот вариант")
        self.test_button.clicked.connect(self.test_draft)
        draft_buttons.addWidget(self.test_button)
        self.undo_button = QPushButton("Отменить замену")
        self.undo_button.clicked.connect(self.undo_apply)
        draft_buttons.addWidget(self.undo_button)
        draft_layout.addLayout(draft_buttons)
        outer.addWidget(self.draft_card)
        self.draft_card.hide()
        self.preview.textChanged.connect(self._update_state)
        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        self._update_state()

    def _style(self):
        return style_for(self.preset.currentIndex(), self.humor.currentIndex(), self.profanity.currentIndex())

    def _update_state(self):
        editable = self._editable and not self._closed and not self._busy
        for widget in (self.preset, self.humor, self.profanity, self.topics, self.preset_button,
                       self.wishes, self.improve, self.generate_button, self.preview):
            widget.setEnabled(editable)
        self.model_catalog.set_editable(editable)
        self.generate_button.setText("Генерируем…" if self._busy else "Сгенерировать промпт")
        problems = check_prompt(self.preview.toPlainText())
        self.validation.setText(" ".join(problems) if problems else
            "Формат и обязательный блок защиты проверены. Это не оценка качества ответов: "
            "проверьте вариант на обычных вопросах и попытках обхода в «Тестировании».")
        self.apply_button.setEnabled(editable and not problems)
        self.test_button.setEnabled(not self._closed and not self._busy and not problems)
        self.undo_button.setEnabled(editable and self._previous is not None)

    def set_editable(self, editable):
        self._editable = editable
        self._update_state()

    def show_preset(self):
        if not self._editable or self._busy or self._closed:
            return
        self._show_draft(compose_prompt(self._style(), self.topics.text()))
        self.status.setText("Пресет подготовлен локально. Его можно проверить без замены текущего промпта.")

    def _show_draft(self, text):
        self.preview.setPlainText(text)
        self.draft_card.show()
        self._update_state()

    def start_generation(self):
        if not self._editable or self._busy or self._closed or self.model_catalog._busy:
            return
        try:
            auth = self.get_credentials()
            snapshot = prepare_generation(auth, self.model_catalog.combo.currentText(),
                self.wishes.toPlainText(), self._style(), self.topics.text(),
                self.get_prompt() if self.improve.isChecked() else "")
        except (OSError, ValueError) as exc:
            self.status.setText("Вставьте ваш AI API-ключ во вкладке «Подключение» или используйте сохранённый ключ."
                                if str(exc) == "Заполните AI_API_KEY." else str(exc))
            return
        self._busy = True
        self._update_state()
        self.status.setText("Готовим новый вариант. Пожелания, темы и параметры зафиксированы на момент нажатия.")
        Thread(target=generation_worker, args=(snapshot, self._events, self._cancel), daemon=True).start()

    def _poll(self):
        if self._closed:
            return
        while True:
            try:
                auth, prompt, error = self._events.get_nowait()
            except Empty:
                return
            self._busy = False
            try:
                current = self.get_credentials()
            except (OSError, ValueError):
                current = None
            if current != auth:
                self.status.setText("Параметры AI API изменились. Результат отброшен; повторите генерацию.")
            elif error:
                self.status.setText(clean_error(error, auth.api_key))
            else:
                self._show_draft(prompt)
                self.status.setText("Вариант готов. Проверьте ответы, затем примените промпт, если результат устраивает.")
            self._update_state()

    def apply_draft(self):
        if not self.apply_button.isEnabled():
            return
        if self.get_prompt() == self.preview.toPlainText():
            self.status.setText("Этот вариант уже находится в общем промпте.")
            return
        self._previous = self.get_prompt()
        self.set_prompt(self.preview.toPlainText())
        self.status.setText("Вариант применён к черновику. Файлы ещё не изменены; доступна отмена замены.")
        self._update_state()

    def undo_apply(self):
        if not self.undo_button.isEnabled():
            return
        self.set_prompt(self._previous)
        self._previous = None
        self.status.setText("Предыдущий общий промпт восстановлен в интерфейсе.")
        self._update_state()

    def test_draft(self):
        if self.test_button.isEnabled():
            self.test_requested.emit(self.preview.toPlainText())

    def shutdown(self):
        self._closed = True
        self._cancel.set()
        self._timer.stop()
        self.model_catalog.shutdown()


def clean_error(error, key):
    return clean_text(redact_secret(error, key), 500) or "Ошибка генерации промпта."
