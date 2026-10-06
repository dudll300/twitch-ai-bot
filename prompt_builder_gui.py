"""Prompt drafts: explicit apply/undo, local checks and cancellable HTTP worker."""

from queue import Empty, Queue
from threading import Event, Thread

from PySide6.QtCore import QPoint, QTimer, Qt, Signal
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget

from ai_client import clean_text, redact_secret
from model_catalog_gui import ModelCatalogControl
from prompt_builder import (DEFAULT_TOPICS, GENERATOR_MODEL, PRESETS, TEMPERAMENTS, check_prompt, compose_prompt,
                            generate_prompt, prepare_generation, style_for, topics_from_prompt)
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
        self._operation = None
        self._previous = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(20)
        api_card, api_layout = card("Модель генератора", "Общая для создания нового промпта и улучшения текущего")
        self.model_catalog = ModelCatalogControl(GENERATOR_MODEL, get_credentials)
        api_layout.addWidget(field("Модель для генерации · отдельная от модели ответов", self.model_catalog))
        api_layout.addWidget(label(
            "Каталог загружается по кнопке. Укажите точный ID доступной модели; автоматической замены нет. "
            "Генерация и проверка ответов расходуют баланс AI API. Пресеты бесплатны и работают без ключа.", "muted", True))
        self.connection_button = QPushButton("К подключению")
        self.connection_button.clicked.connect(lambda: self.connection_requested.emit())
        api_layout.addWidget(self.connection_button, 0, Qt.AlignLeft)
        outer.addWidget(api_card)

        self.create_card, layout = card("Создать новый промпт", "Выберите готовый пресет или опишите новый характер для AI-генератора")
        grid = QGridLayout()
        self.preset = NoWheelComboBox()
        self.preset.addItems([name for name, _ in PRESETS])
        self.humor = NoWheelComboBox()
        self.humor.addItems(("По просьбе", "Иногда", "Часто"))
        self.humor.setCurrentIndex(1)
        self.profanity = NoWheelComboBox()
        self.profanity.addItems(("Без мата", "Изредка", "Свободно"))
        self.temperament = NoWheelComboBox()
        self.temperament.addItems([name for name, _ in TEMPERAMENTS])
        self.temperament.setCurrentIndex(1)
        grid.addWidget(field("Основа", self.preset), 0, 0)
        grid.addWidget(field("Темперамент", self.temperament), 0, 1)
        grid.addWidget(field("Юмор", self.humor), 1, 0)
        grid.addWidget(field("Ненормативная лексика", self.profanity), 1, 1)
        for column in range(2):
            grid.setColumnStretch(column, 1)
        layout.addLayout(grid)
        layout.addWidget(label("Темперамент задаёт эмоциональность и подачу. Частота шуток и мат настраиваются отдельно.", "muted", True))
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
        self.generate_button = QPushButton("Сгенерировать промпт")
        self.generate_button.setObjectName("primary")
        self.generate_button.clicked.connect(lambda: self.start_generation("create"))
        layout.addWidget(self.generate_button, 0, Qt.AlignLeft)
        outer.addWidget(self.create_card)

        self.improve_card, improve_layout = card("Улучшить текущий промпт", "Берём текст из поля «Общий промпт», включая несохранённые изменения")
        self.improve_wishes = ScrollPlainTextEdit()
        self.improve_wishes.setPlaceholderText("Например: сохрани характер, но убери повторяющиеся фразы и сделай подколы более конкретными")
        self.improve_wishes.setMinimumHeight(100)
        self.improve_wishes.setMaximumHeight(150)
        improve_layout.addWidget(field("Что доработать", self.improve_wishes))
        improve_layout.addWidget(label("Можно оставить пожелания пустыми: AI уточнит формулировки, сохраняя характер текущего промпта. Настройки нового пресета здесь не применяются.", "muted", True))
        self.improve_button = QPushButton("Улучшить текущий промпт")
        self.improve_button.clicked.connect(lambda: self.start_generation("improve"))
        improve_layout.addWidget(self.improve_button, 0, Qt.AlignLeft)
        outer.addWidget(self.improve_card)
        self.status = label("Защита темы и служебных инструкций добавляется в каждый пресет и результат генерации.", "muted", True)
        self.status.setTextFormat(Qt.PlainText)
        outer.addWidget(self.status)

        self.draft_card, draft_layout = card("Новый вариант промпта", "Текущий общий промпт не меняется до нажатия «Применить»")
        self.preview = ScrollPlainTextEdit()
        self.preview.setAccessibleName("Новый вариант промпта")
        self.preview.setMinimumHeight(520)
        draft_layout.addWidget(self.preview)
        draft_layout.addWidget(label("Колесо прокручивает длинный текст внутри поля. Если весь текст помещается, прокручивается вкладка.", "muted", True))
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
        return style_for(self.preset.currentIndex(), self.humor.currentIndex(), self.profanity.currentIndex(),
                         self.temperament.currentIndex())

    def _update_state(self):
        editable = self._editable and not self._closed and not self._busy
        for widget in (self.preset, self.temperament, self.humor, self.profanity, self.topics, self.preset_button,
                       self.wishes, self.improve_wishes, self.generate_button, self.improve_button, self.preview):
            widget.setEnabled(editable)
        self.model_catalog.set_editable(editable)
        self.generate_button.setText("Генерируем…" if self._busy and self._operation == "create" else "Сгенерировать промпт")
        self.improve_button.setText("Улучшаем…" if self._busy and self._operation == "improve" else "Улучшить текущий промпт")
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
        QTimer.singleShot(0, self._reveal_draft)

    def _reveal_draft(self):
        if self._closed:
            return
        parent = self.parentWidget()
        while parent is not None:
            if isinstance(parent, QScrollArea):
                top = self.preview.mapTo(parent.widget(), QPoint(0, 0)).y()
                parent.verticalScrollBar().setValue(top - 24)
                return
            parent = parent.parentWidget()

    def start_generation(self, operation="create"):
        if not self._editable or self._busy or self._closed or self.model_catalog._busy:
            return
        try:
            current_prompt = self.get_prompt() if operation == "improve" else ""
            if operation == "improve" and not current_prompt.strip():
                raise ValueError("Текущий общий промпт пуст. Сначала создайте новый вариант.")
            auth = self.get_credentials()
            snapshot = prepare_generation(auth, self.model_catalog.combo.currentText(),
                self.improve_wishes.toPlainText() if operation == "improve" else self.wishes.toPlainText(),
                "" if operation == "improve" else self._style(),
                topics_from_prompt(current_prompt) if operation == "improve" else self.topics.text(),
                current_prompt, operation=operation)
        except (OSError, ValueError) as exc:
            self.status.setText(str(exc))
            return
        self._busy = True
        self._operation = operation
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
