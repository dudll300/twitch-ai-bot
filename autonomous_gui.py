"""Live participation controls and a human-readable decision journal."""

from dataclasses import asdict
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QAbstractSpinBox, QGridLayout, QHBoxLayout, QPlainTextEdit, QPushButton, QSpinBox, QStyle, QStyleOptionSpinBox, QVBoxLayout, QWidget

from autonomous import AutoSettings, RANGES, load_settings, save_settings, validate_settings
from ui_widgets import NoWheelComboBox, ToggleSwitch, card, field, label, scroll_page


PARAMETERS = (
    ("pause_seconds", "Пауза между репликами", " с"),
    ("hourly_limit", "Максимум за час", ""),
    ("context_count", "Сообщений в контексте", ""),
    ("freshness_seconds", "Срок свежести контекста", " с"),
    ("check_min_seconds", "Интервал проверки · от", " с"),
    ("check_max_seconds", "Интервал проверки · до", " с"),
    ("active_seconds", "Последнее сообщение не старше", " с"),
    ("min_messages", "Минимум сообщений для проверки", ""),
    ("min_authors", "Минимум разных авторов", ""),
    ("max_chars", "Максимальная длина реплики", " симв."),
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
        main_layout.addWidget(label(
            "Предпросмотр показывает решения только здесь. Публикация отправляет реплики в Twitch. "
            "При выключении чат не собирается, контекст очищается, подготовленные реплики отменяются.", "muted", True))
        self.state = label("", "caption", True)
        main_layout.addWidget(self.state)
        layout.addWidget(main)
        self.error = label(error, "error", True)
        self.error.setVisible(bool(error))
        layout.addWidget(self.error)
        options, options_layout = card("Частота и контекст", "Проверки выполняются в случайные моменты заданного интервала, только при свежей активности.")
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
            spin.setRange(*RANGES[key])
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
            "Лимит — до 8 реплик за скользящий час. Предпросмотр учитывает те же паузы и лимиты. "
            "Вопросы по награде всегда имеют приоритет. Проверки AI могут расходовать баланс API даже при решении промолчать.",
            "muted", True))
        actions = QHBoxLayout()
        self.hint = label("Параметры сохранены", "muted", True)
        actions.addWidget(self.hint, 1)
        self.apply_button = QPushButton("Применить параметры")
        self.apply_button.clicked.connect(self.apply)
        actions.addWidget(self.apply_button)
        options_layout.addLayout(actions)
        layout.addWidget(options)
        journal, journal_layout = card("Решения и реплики", "Текущий запуск. Сообщения зрителей в журнал не записываются.")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(240)
        self.log.document().setMaximumBlockCount(1000)
        self.log.setPlaceholderText("После запуска бота здесь появятся решения: промолчать, пошутить или задать вопрос.")
        self.log.setAccessibleName("Журнал самостоятельных реплик")
        journal_layout.addWidget(self.log)
        layout.addWidget(journal)
        outer.addWidget(scroll_page(content))
        self.enabled.toggled.connect(self._live_change)
        self.mode.currentIndexChanged.connect(self._live_change)
        self._loading = False
        self.set_running(False)

    def _edited(self, *_):
        if not self._loading:
            self.hint.setText("Параметры изменены — нажмите «Применить»")

    def _toggle_advanced(self, expanded):
        self.advanced_panel.setVisible(expanded)
        self.advanced_button.setText("Скрыть расширенные настройки" if expanded else "Расширенные настройки")
        self.advanced_button.setAccessibleName("Скрыть расширенные настройки" if expanded else
                                               "Показать расширенные настройки")

    def _live_change(self, *_):
        if self._loading:
            return
        # Disabling must work even while numeric edits are inconsistent.
        values = asdict(self.saved)
        values.update(enabled=self.enabled.isChecked(), mode=self.mode.currentData())
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
            self._loading = False
            return False
        self.saved = settings
        self.error.hide()
        self.set_running(self._running)
        if all(widget.value() == getattr(settings, key) for key, widget in self.inputs.items()):
            self.hint.setText("Параметры сохранены")
        return True

    def apply(self):
        values = {key: widget.value() for key, widget in self.inputs.items()}
        values.update(enabled=self.enabled.isChecked(), mode=self.mode.currentData())
        try:
            settings = validate_settings(values)
        except ValueError as exc:
            self.advanced_button.setChecked(True)
            self.error.setText(str(exc))
            self.error.show()
            return False
        return self._save(settings)

    def set_running(self, running):
        self._running = running
        self.state.setText("Тумблер и режим сохраняются сразу. Бот применяет изменения без перезапуска." if running else
                           "Настройки сохранены для следующего запуска бота. Тумблер и режим сохраняются сразу.")

    def push_event(self, event):
        statuses = {"settings": "Настройки", "pending": "Проверка AI", "silent": "Промолчать",
                    "preview": "Предпросмотр", "published": "Отправлено в чат",
                    "skipped": "Пропущено", "error": "Ошибка"}
        actions = {"joke": "шутка", "question": "вопрос", "silent": "молчание"}
        status = statuses.get(event.get("status"), "Событие")
        action = actions.get(event.get("action"), "")
        body = event.get("text") or event.get("reason") or "Без реплики."
        heading = f"[{event.get('time', '')}] {status}" + (f" · {action}" if action else "")
        self.log.appendPlainText(heading + "\n" + str(body) + "\n")
