"""Live participation controls and a human-readable decision journal."""

from dataclasses import asdict
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QAbstractSpinBox, QGridLayout, QHBoxLayout, QPushButton, QSpinBox, QStyle, QStyleOptionSpinBox, QVBoxLayout, QWidget

from autonomous import AutoSettings, MINIMUM_VALUES, load_settings, save_settings, validate_settings
from participation import PARTICIPATION
from ui_widgets import NoWheelComboBox, ScrollPlainTextEdit, ToggleSwitch, card, field, label, scroll_page


PARAMETERS = (
    ("pause_seconds", "Пауза между репликами", " с"),
    ("hourly_limit", "Максимум за час", ""),
    ("context_count", "Сообщений в контексте", ""),
    ("freshness_seconds", "Срок свежести контекста", " с"),
    ("check_min_seconds", "Пауза между AI-проверками · от", " с"),
    ("check_max_seconds", "Пауза между AI-проверками · до", " с"),
    ("active_seconds", "Последнее сообщение не старше", " с"),
    ("min_messages", "Минимум сообщений для проверки", ""),
    ("min_authors", "Минимум разных авторов", ""),
    ("max_chars", "Максимальная длина реплики", " симв."),
    ("settle_seconds", "Ожидание паузы в разговоре", " с"),
    ("max_wait_seconds", "Максимальное ожидание фрагмента", " с"),
    ("reply_ttl_seconds", "Срок актуальности ответа", " с"),
    ("request_hourly_limit", "Максимум AI-запросов за час", ""),
)


class NumberInput(QSpinBox):
    """Keep step controls legible under the dark stylesheet on Windows."""

    def wheelEvent(self, event):
        event.ignore()

    def paintEvent(self, event):
        super().paintEvent(event)
        option = QStyleOptionSpinBox()
        self.initStyleOption(option)
        painter = QPainter(self)
        painter.setPen(QPen(QColor("#d5d8de"), 1.5))
        for control in (QStyle.SC_SpinBoxUp, QStyle.SC_SpinBoxDown):
            center = self.style().subControlRect(QStyle.CC_SpinBox, option, control, self).center()
            painter.drawLine(center.x() - 4, center.y(), center.x() + 4, center.y())
            if control == QStyle.SC_SpinBoxUp:
                painter.drawLine(center.x(), center.y() - 4, center.x(), center.y() + 4)


class AutonomousPage(QWidget):
    def __init__(self, root):
        super().__init__()
        self.path = root / "autonomous.json"
        self._loading = True
        self._running = False
        try:
            self.saved, _ = load_settings(self.path)
            error = ""
        except (OSError, ValueError, TypeError):
            self.saved = AutoSettings()
            error = "Файл настроек повреждён. Режим выключен; проверьте параметры и сохраните их заново."
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(20)
        main, main_layout = card("Самостоятельные реплики")
        controls = QHBoxLayout()
        self.enabled = ToggleSwitch("Включить режим")
        self.enabled.setAccessibleName("Самостоятельные реплики включены")
        self.enabled.setChecked(self.saved.enabled)
        controls.addWidget(self.enabled)
        controls.addStretch()
        self.mode = NoWheelComboBox()
        self.mode.addItem("Предпросмотр", "preview")
        self.mode.addItem("Публикация", "publish")
        self.mode.setCurrentIndex(0 if self.saved.mode == "preview" else 1)
        self.mode.setAccessibleName("Режим отправки самостоятельных реплик")
        controls.addWidget(self.mode)
        main_layout.addLayout(controls)
        self.participation = NoWheelComboBox()
        for value, (caption, _) in PARTICIPATION.items():
            self.participation.addItem(caption, value)
        self.participation.setCurrentIndex(self.participation.findData(self.saved.participation))
        main_layout.addWidget(field("Характер участия", self.participation,
            "Осторожный — явные поводы; собеседник — поддержка разговора; активный — больше инициативы по свежей теме. Стиль ответов задаёт общий промпт."))
        main_layout.addWidget(label(
            "Предпросмотр показывает решения только здесь. Публикация отправляет реплики в Twitch. "
            "При выключении чат не собирается, контекст очищается, подготовленные реплики отменяются.", "muted", True))
        self.state = label("", "caption", True)
        main_layout.addWidget(self.state)
        layout.addWidget(main)
        self.error = label(error, "error", True)
        self.error.setVisible(bool(error))
        layout.addWidget(self.error)
        instructions, instructions_layout = card("Инструкции самостоятельного участия",
            "Общий промпт задаёт характер и манеру общения. Эти инструкции объясняют, как поддержать один выбранный разговор: ответить, отреагировать, уточнить или пошутить.")
        self.autonomous_prompt = ScrollPlainTextEdit(self.saved.autonomous_prompt)
        self.autonomous_prompt.setMinimumHeight(260)
        self.autonomous_prompt.setAccessibleName("Инструкции самостоятельного участия")
        self.autonomous_prompt.setPlaceholderText("Опишите, какие реплики уместны для вашего канала. Сохраняйте привязку к одному разговору и не требуйте шутку в каждом ответе.")
        self.autonomous_prompt.textChanged.connect(self._edited)
        instructions_layout.addWidget(self.autonomous_prompt)
        self.reset_prompt_button = QPushButton("Вернуть стандартные инструкции")
        self.reset_prompt_button.setProperty("variant", "quiet")
        self.reset_prompt_button.clicked.connect(self.reset_prompt)
        instructions_layout.addWidget(self.reset_prompt_button, 0, Qt.AlignLeft)
        instructions_layout.addWidget(label(
            "Изменения текста и числовых параметров применяются вместе по кнопке «Применить». "
            "До 10 000 символов; пустое поле оставляет обязательные правила приложения. "
            "Сначала AI выбирает одну цепочку и повод, затем пишет реплику только по ней. "
            "Шутка необязательна; качество и уместность оценивайте в предпросмотре.",
            "muted", True))
        layout.addWidget(instructions)
        options, options_layout = card("Частота и контекст", "Новые сообщения собираются в короткий фрагмент. AI выбирает один разговор и адресата; проверки не выполняются на каждую строку.")
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(16)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self.inputs = {}
        self.advanced_panel = QWidget()
        advanced_grid = QGridLayout(self.advanced_panel)
        advanced_grid.setContentsMargins(0, 0, 0, 0)
        advanced_grid.setHorizontalSpacing(24)
        advanced_grid.setVerticalSpacing(16)
        advanced_grid.setColumnStretch(0, 1)
        advanced_grid.setColumnStretch(1, 1)
        for index, (key, title, suffix) in enumerate(PARAMETERS):
            spin = NumberInput()
            spin.setButtonSymbols(QAbstractSpinBox.PlusMinus)
            spin.setRange(MINIMUM_VALUES[key], 2147483647)  # QSpinBox's native integer capacity.
            spin.setValue(getattr(self.saved, key))
            spin.setSuffix(suffix)
            spin.setKeyboardTracking(False)
            self.inputs[key] = spin
            target, position = (grid, index) if index < 2 else (advanced_grid, index - 2)
            target.addWidget(field(title, spin), position // 2, position % 2)
            spin.valueChanged.connect(self._edited)
        options_layout.addLayout(grid)
        self.advanced_button = QPushButton("Расширенные настройки")
        self.advanced_button.setCheckable(True)
        self.advanced_button.setProperty("variant", "quiet")
        self.advanced_button.setAccessibleName("Показать расширенные настройки")
        self.advanced_button.toggled.connect(self._toggle_advanced)
        options_layout.addWidget(self.advanced_button, 0, Qt.AlignLeft)
        self.advanced_panel.hide()
        options_layout.addWidget(self.advanced_panel)
        options_layout.addWidget(label(
            "Максимум за час задаётся вами. Предпросмотр учитывает те же паузы и лимиты. "
            "Вопросы по награде всегда имеют приоритет. AI расходует баланс API: выбор разговора — один запрос, "
            "написание ответа — ещё один. Оба входят в лимит AI-запросов и общий срок актуальности; "
            "молчание тоже может расходовать баланс.",
            "muted", True))
        actions = QHBoxLayout()
        self.hint = label("Инструкции и параметры сохранены", "muted", True)
        actions.addWidget(self.hint, 1)
        self.reset_button = QPushButton("Сбросить настройки")
        self.reset_button.clicked.connect(self.reset)
        actions.addWidget(self.reset_button)
        self.apply_button = QPushButton("Применить")
        self.apply_button.clicked.connect(self.apply)
        actions.addWidget(self.apply_button)
        options_layout.addLayout(actions)
        layout.addWidget(options)
        journal, journal_layout = card("Решения и реплики", "Текущий запуск. Сообщения зрителей в журнал не записываются.")
        self.log = ScrollPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(240)
        self.log.document().setMaximumBlockCount(1000)
        self.log.setPlaceholderText("После запуска бота здесь появятся решения: промолчать, ответить, отреагировать, пошутить или задать вопрос.")
        self.log.setAccessibleName("Журнал самостоятельных реплик")
        journal_layout.addWidget(self.log)
        layout.addWidget(journal)
        outer.addWidget(scroll_page(content))
        self.enabled.toggled.connect(self._live_change)
        self.mode.currentIndexChanged.connect(self._live_change)
        self.participation.currentIndexChanged.connect(self._live_change)
        self._loading = False
        self.set_running(False)

    def _edited(self, *_):
        if not self._loading:
            self._update_hint()

    def _update_hint(self):
        changed = self.autonomous_prompt.toPlainText() != self.saved.autonomous_prompt or any(
            widget.value() != getattr(self.saved, key) for key, widget in self.inputs.items())
        self.hint.setText("Текст или параметры изменены — нажмите «Применить»" if changed else
                          "Инструкции и параметры сохранены")

    def reset_prompt(self):
        # Restoring instructions is an editor action; it does not enable the mode
        # or save unrelated numeric drafts.
        self.autonomous_prompt.setPlainText(AutoSettings().autonomous_prompt)

    def _toggle_advanced(self, expanded):
        self.advanced_panel.setVisible(expanded)
        self.advanced_button.setText("Скрыть расширенные настройки" if expanded else "Расширенные настройки")
        self.advanced_button.setAccessibleName("Скрыть расширенные настройки" if expanded else
                                               "Показать расширенные настройки")

    def _live_change(self, *_):
        if self._loading:
            return
        # Live controls use saved instructions and numbers, leaving all drafts intact.
        values = asdict(self.saved)
        values.update(enabled=self.enabled.isChecked(), mode=self.mode.currentData(), participation=self.participation.currentData())
        self._save(validate_settings(values))

    def _save(self, settings):
        try:
            save_settings(self.path, settings)
        except (OSError, ValueError) as exc:
            self.error.setText(f"Не удалось применить настройки: {exc}")
            self.error.show()
            self._loading = True
            self.enabled.setChecked(self.saved.enabled)
            self.mode.setCurrentIndex(0 if self.saved.mode == "preview" else 1)
            self.participation.setCurrentIndex(self.participation.findData(self.saved.participation))
            self._loading = False
            return False
        self.saved = settings
        self.error.hide()
        self.set_running(self._running)
        self._update_hint()
        return True

    def apply(self):
        values = {key: widget.value() for key, widget in self.inputs.items()}
        values.update(enabled=self.enabled.isChecked(), mode=self.mode.currentData(), participation=self.participation.currentData(),
                      autonomous_prompt=self.autonomous_prompt.toPlainText())
        try:
            settings = validate_settings(values)
        except ValueError as exc:
            self.advanced_button.setChecked(True)
            self.error.setText(str(exc))
            self.error.show()
            return False
        return self._save(settings)

    def reset(self):
        defaults = AutoSettings()
        if not self._save(defaults):
            return False
        self._loading = True
        try:
            self.enabled.setChecked(defaults.enabled)
            self.mode.setCurrentIndex(self.mode.findData(defaults.mode))
            self.participation.setCurrentIndex(self.participation.findData(defaults.participation))
            for key, widget in self.inputs.items():
                widget.setValue(getattr(defaults, key))
            self.autonomous_prompt.setPlainText(defaults.autonomous_prompt)
        finally:
            self._loading = False
        self.hint.setText("Настройки по умолчанию сохранены. Режим выключен.")
        return True

    def set_running(self, running):
        self._running = running
        self.state.setText("Тумблер, режим и характер участия сохраняются сразу. Бот применяет изменения без перезапуска." if running else
                           "Настройки сохранены для следующего запуска бота. Тумблер, режим и характер участия сохраняются сразу.")

    def push_event(self, event):
        statuses = {"settings": "Настройки", "pending": "Проверка AI", "silent": "Промолчать",
                    "preview": "Предпросмотр", "published": "Отправлено в чат",
                    "skipped": "Пропущено", "error": "Ошибка"}
        actions = {"answer": "ответ", "reaction": "реакция", "joke": "шутка", "question": "вопрос", "silent": "молчание"}
        status = statuses.get(event.get("status"), "Событие")
        action = actions.get(event.get("action"), "")
        reasons = {"no_reason": "Нет уместного повода.", "offtopic": "Постороннее задание или тема.",
                   "already_answered": "Вопрос уже закрыт.", "insufficient_context": "Недостаточно контекста."}
        reason = event.get("reason", "")
        body = event.get("text") or reasons.get(reason, reason) or "Без реплики."
        heading = f"[{event.get('time', '')}] {status}" + (f" · {action}" if action else "")
        if event.get("target"):
            heading += " · @" + event["target"]
        if event.get("basis"):
            heading += " · сообщения " + ", ".join(str(value) for value in event["basis"])
        self.log.appendPlainText(heading + "\n" + str(body) + "\n")
