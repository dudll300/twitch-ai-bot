"""Editable channel glossary, independent from viewer profiles and bot startup."""

from copy import deepcopy
from dataclasses import replace
import unicodedata
from uuid import uuid4

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractSpinBox, QGridLayout, QHBoxLayout, QLineEdit, QListWidgetItem,
    QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from autonomous_gui import NumberInput
from local_context import document_raw, load_document, save_document, validate_document
from ui_widgets import (
    NoWheelComboBox, ScrollListWidget, ScrollPlainTextEdit, ToggleSwitch,
    card, field, label, scroll_page,
)


def _search_text(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().replace("ё", "е").split())


class LocalContextPage(QWidget):
    changed = Signal()

    def __init__(self, root):
        super().__init__()
        self.path = root / "local-context.json"
        self.saved = load_document(self.path)
        self.rows = deepcopy(document_raw(self.saved)["cards"])
        self.load_error = self.saved.error
        self._loading = True
        self._removed = None
        self._running = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(20)
        system, system_layout = card("Знания вашего канала",
            "Поясните локальные выражения и отсылки. Бот сможет понять их в вопросе и иногда вспомнить по подходящей ситуации. Отсылка необязательна: содержательный ответ важнее.")
        self.enabled = ToggleSwitch("Включить локальный контекст")
        self.enabled.setAccessibleName("Локальный контекст включён")
        self.enabled.setChecked(self.saved.settings.enabled)
        system_layout.addWidget(self.enabled)
        self.state = label("", "muted", True)
        system_layout.addWidget(self.state)
        self.error = label("", "error", True)
        self.error.hide()
        system_layout.addWidget(self.error)
        layout.addWidget(system)

        options, options_layout = card("Частота творческих отсылок",
            "Общие ограничения для наград и самостоятельных реплик. Прямое объяснение выражения доступно даже во время паузы и не расходует эти лимиты.")
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(16)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self.inputs = {}
        numeric_fields = (
            ("global_pause_seconds", "Пауза между отсылками", " с"),
            ("card_pause_seconds", "Пауза повторения одной карточки", " с"),
            ("hourly_limit", "Максимум творческих отсылок за час", ""),
        )
        for index, (key, title, suffix) in enumerate(numeric_fields):
            spin = NumberInput()
            spin.setButtonSymbols(QAbstractSpinBox.PlusMinus)
            spin.setRange(0, 2147483647)
            spin.setValue(getattr(self.saved.settings, key))
            spin.setSuffix(suffix)
            spin.setKeyboardTracking(False)
            spin.setAccessibleName(title)
            spin.valueChanged.connect(self._edited)
            self.inputs[key] = spin
            grid.addWidget(field(title, spin), index // 2, index % 2)
        self.max_per_reply = NoWheelComboBox()
        self.max_per_reply.addItem("Без творческих отсылок", 0)
        self.max_per_reply.addItem("Не больше одной", 1)
        self.max_per_reply.setCurrentIndex(self.max_per_reply.findData(self.saved.settings.max_per_reply))
        self.max_per_reply.setAccessibleName("Максимум отсылок в ответе")
        self.max_per_reply.currentIndexChanged.connect(self._edited)
        grid.addWidget(field("Максимум отсылок в ответе", self.max_per_reply), 1, 1)
        options_layout.addLayout(grid)
        options_layout.addWidget(label(
            "Ноль в лимите за час отключает творческий подбор. Счётчики пополняются только после успешной публикации: ошибки, отмены и предпросмотр их не расходуют. Перезапуск и выключение системы не сбрасывают паузы.",
            "muted", True))

        columns = QHBoxLayout()
        columns.setSpacing(20)
        left, left_layout = card()
        left.setMinimumWidth(260)
        left.setMaximumWidth(330)
        heading = QHBoxLayout()
        self.count = label("", "section")
        heading.addWidget(self.count, 1)
        self.add_button = QPushButton("+ Добавить")
        self.add_button.setProperty("variant", "quiet")
        self.add_button.clicked.connect(self.add_card)
        heading.addWidget(self.add_button)
        left_layout.addLayout(heading)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Название или алиас")
        self.search.setAccessibleName("Поиск локальных карточек")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        left_layout.addWidget(self.search)
        self.list = ScrollListWidget()
        self.list.setAccessibleName("Карточки локального контекста")
        self.list.setMinimumHeight(400)
        self.list.setMaximumHeight(560)
        self.list.currentRowChanged.connect(self._select)
        left_layout.addWidget(self.list)
        self.empty_search = label("", "muted", True)
        left_layout.addWidget(self.empty_search)
        self.undo_button = QPushButton("Отменить удаление")
        self.undo_button.setProperty("variant", "quiet")
        self.undo_button.clicked.connect(self.undo_remove)
        self.undo_button.hide()
        left_layout.addWidget(self.undo_button)
        left_layout.addStretch()
        columns.addWidget(left, 1, Qt.AlignTop)

        self.stack = QStackedWidget()
        empty, empty_layout = card("Словарь начинается с ваших карточек")
        empty_layout.addWidget(label(
            "Добавьте выражение и объясните его значение. Варианты написания впишите в алиасы. Готовые мемы автоматически не добавляются.",
            "muted", True))
        empty_layout.addWidget(label(
            "Для использования без прямого упоминания отдельно разрешите подбор по ситуации и опишите, когда он уместен. По умолчанию эта возможность выключена.",
            "muted", True))
        empty_layout.addStretch()
        self.stack.addWidget(empty)
        editor, editor_layout = card()
        header = QHBoxLayout()
        self.editor_title = label("Карточка", "section")
        header.addWidget(self.editor_title, 1)
        self.card_enabled = ToggleSwitch("Активна")
        self.card_enabled.setAccessibleName("Карточка активна")
        header.addWidget(self.card_enabled)
        editor_layout.addLayout(header)
        self.name = QLineEdit()
        self.name.setAccessibleName("Название карточки")
        self.name.setPlaceholderText("Локальное выражение или название отсылки")
        editor_layout.addWidget(field("Название · до 80 символов", self.name))
        self.aliases = ScrollPlainTextEdit()
        self.aliases.setMinimumHeight(110)
        self.aliases.setAccessibleName("Алиасы карточки")
        self.aliases.setPlaceholderText("Один вариант на строку. Многословную фразу пишите целиком.")
        editor_layout.addWidget(field("Алиасы · до 20 вариантов, до 80 символов каждый", self.aliases))
        self.meaning = ScrollPlainTextEdit()
        self.meaning.setMinimumHeight(130)
        self.meaning.setAccessibleName("Значение карточки")
        self.meaning.setPlaceholderText("Кратко объясните локальный смысл и интонацию выражения.")
        editor_layout.addWidget(field("Что означает · обязательно, до 600 символов", self.meaning))
        self.allow_situational = ToggleSwitch("Использовать по ситуации")
        self.allow_situational.setAccessibleName("Разрешить использование по ситуации без прямого упоминания")
        editor_layout.addWidget(self.allow_situational)
        editor_layout.addWidget(label(
            "Разрешает необязательную отсылку без прямого упоминания названия или алиаса. Требует описания уместной ситуации; по умолчанию выключено.",
            "muted", True))
        self.situations = ScrollPlainTextEdit()
        self.situations.setMinimumHeight(130)
        self.situations.setAccessibleName("Уместные ситуации")
        self.situations.setPlaceholderText("Свяжите отсылку с конкретной ситуацией. Одного «чат весёлый» недостаточно.")
        editor_layout.addWidget(field("Когда уместно · обязательно для подбора по ситуации, до 600 символов", self.situations))
        self.avoid = ScrollPlainTextEdit()
        self.avoid.setMinimumHeight(130)
        self.avoid.setAccessibleName("Когда не использовать")
        self.avoid.setPlaceholderText("Опишите оговорки: серьёзная тема, спор, нежелательный подкол и другие границы.")
        editor_layout.addWidget(field("Когда не использовать · до 600 символов", self.avoid))
        self.example = ScrollPlainTextEdit()
        self.example.setMinimumHeight(100)
        self.example.setAccessibleName("Пример употребления")
        self.example.setPlaceholderText("Необязательный пример, который показывает смысл. Бот не обязан его копировать.")
        editor_layout.addWidget(field("Пример · необязательно, до 300 символов", self.example))
        self.remove_button = QPushButton("Удалить карточку")
        self.remove_button.setProperty("variant", "danger")
        self.remove_button.clicked.connect(self.remove_card)
        editor_layout.addWidget(self.remove_button, 0, Qt.AlignRight)
        self.stack.addWidget(editor)
        columns.addWidget(self.stack, 3)
        layout.addLayout(columns)
        layout.addWidget(options)
        layout.addWidget(label(
            "До 40 карточек. Ситуационный каталог — до 10 000 символов описаний, общий контекст карточек в запросе вместе с инструкциями и резервом протокола — до 16 000. При превышении сократите описания или выключите подбор по ситуации у части карточек. Карточки добавляют входные токены AI API; дополнительных постоянных запросов для них нет.",
            "muted", True))
        actions = QHBoxLayout()
        self.hint = label("", "muted", True)
        actions.addWidget(self.hint, 1)
        self.apply_button = QPushButton("Применить")
        self.apply_button.clicked.connect(self.apply)
        actions.addWidget(self.apply_button)
        self.scroll = scroll_page(content)
        outer.addWidget(self.scroll, 1)
        outer.addLayout(actions)
        for widget in (self.name, self.aliases, self.meaning, self.situations, self.avoid, self.example):
            widget.textChanged.connect(self._store)
        self.card_enabled.toggled.connect(self._store)
        self.allow_situational.toggled.connect(self._store)
        self.enabled.toggled.connect(self._live_change)
        self._rebuild(0 if self.rows else -1)
        self._loading = False
        self._update_hint()
        self.set_running(False)
        self._show_load_error()

    def raw_document(self):
        settings = document_raw(self.saved)["settings"]
        settings.update(enabled=self.enabled.isChecked(), max_per_reply=self.max_per_reply.currentData())
        settings.update({key: widget.value() for key, widget in self.inputs.items()})
        return {"version": 1, "settings": settings, "cards": deepcopy(self.rows)}

    @property
    def dirty(self):
        return self.raw_document() != document_raw(self.saved)

    def _update_hint(self):
        self.hint.setText("Есть несохранённые карточки или параметры — нажмите «Применить»" if self.dirty else
                          "Карточки и параметры сохранены")

    def _edited(self, *_):
        if not self._loading:
            self._update_hint()
            self.changed.emit()

    def _show_load_error(self):
        if self.load_error:
            self.error.setText(self.load_error + " «Применить» сохранит текущие карточки вместо повреждённого файла. До этого бот работает без локального контекста.")
            self.error.show()

    def _show_error(self, message):
        self.error.setText(message)
        self.error.show()
        self.scroll.ensureWidgetVisible(self.error)

    def _item_text(self, row):
        return (row["name"].strip() or "Новая карточка") + "\n" + (
            "Отключена" if not row["enabled"] else
            "Понимание и подбор по ситуации" if row["allow_situational"] else "Только прямое упоминание")

    def _rebuild(self, selected):
        self.list.blockSignals(True)
        self.list.clear()
        for row in self.rows:
            item = QListWidgetItem(self._item_text(row))
            item.setToolTip("\n".join([row["name"]] + row["aliases"]))
            self.list.addItem(item)
        self.list.setCurrentRow(selected)
        self.list.blockSignals(False)
        self.count.setText(f"Карточки · {len(self.rows)}")
        self.add_button.setEnabled(len(self.rows) < 40)
        self.undo_button.setEnabled(self._removed is not None and len(self.rows) < 40)
        self._select(selected)
        self._filter()

    def _select(self, index):
        if index < 0 or index >= len(self.rows):
            self.stack.setCurrentIndex(0)
            return
        was_loading = self._loading
        self._loading = True
        row = self.rows[index]
        self.name.setText(row["name"])
        self.aliases.setPlainText("\n".join(row["aliases"]))
        for key in ("meaning", "situations", "avoid", "example"):
            getattr(self, key).setPlainText(row[key])
        self.card_enabled.setChecked(row["enabled"])
        self.allow_situational.setChecked(row["allow_situational"])
        self.editor_title.setText(row["name"].strip() or "Новая карточка")
        self.stack.setCurrentIndex(1)
        self._loading = was_loading

    def _store(self, *_):
        index = self.list.currentRow()
        if self._loading or index < 0 or index >= len(self.rows):
            return
        row = self.rows[index]
        row.update(name=self.name.text(), aliases=[part.strip() for part in self.aliases.toPlainText().splitlines() if part.strip()],
                   enabled=self.card_enabled.isChecked(), allow_situational=self.allow_situational.isChecked())
        row.update({key: getattr(self, key).toPlainText() for key in ("meaning", "situations", "avoid", "example")})
        self.list.item(index).setText(self._item_text(row))
        self.list.item(index).setToolTip("\n".join([row["name"]] + row["aliases"]))
        self.editor_title.setText(row["name"].strip() or "Новая карточка")
        self._filter()
        self._edited()

    def _filter(self, *_):
        needle = _search_text(self.search.text())
        visible = 0
        for index, row in enumerate(self.rows):
            found = not needle or any(needle in _search_text(value) for value in [row["name"]] + row["aliases"])
            self.list.item(index).setHidden(not found)
            visible += found
        self.empty_search.setText("Ничего не найдено. Выбранная карточка остаётся в редакторе." if self.rows and not visible else "")

    def add_card(self):
        if len(self.rows) >= 40:
            return
        self.rows.append({"id": uuid4().hex, "name": "", "aliases": [], "meaning": "", "situations": "",
                          "avoid": "", "example": "", "enabled": True, "allow_situational": False})
        self.search.clear()
        self._rebuild(len(self.rows) - 1)
        self.name.setFocus()
        self.scroll.ensureWidgetVisible(self.name)
        self._edited()

    def remove_card(self):
        index = self.list.currentRow()
        if index < 0:
            return
        self._removed = (index, self.rows.pop(index))
        self.undo_button.show()
        self._rebuild(min(index, len(self.rows) - 1))
        self._edited()

    def undo_remove(self):
        if self._removed is None or len(self.rows) >= 40:
            return
        index, row = self._removed
        self.rows.insert(index, row)
        self._removed = None
        self.undo_button.hide()
        self.search.clear()
        self._rebuild(index)
        self._edited()

    def _live_change(self, *_):
        if self._loading:
            return
        if self.load_error:
            self._loading = True
            self.enabled.setChecked(self.saved.settings.enabled)
            self._loading = False
            self._show_load_error()
            return
        # A live switch applies only the saved dictionary, never an unfinished card.
        snapshot = replace(self.saved, settings=replace(self.saved.settings, enabled=self.enabled.isChecked()))
        try:
            saved = save_document(self.path, snapshot)
        except (OSError, ValueError, TypeError):
            self._loading = True
            self.enabled.setChecked(self.saved.settings.enabled)
            self._loading = False
            self._show_error("Не удалось переключить локальный контекст. Карточки и черновики сохранены в окне; проверьте доступ к папке данных.")
            return
        self.saved = saved
        self.error.hide()
        self._edited()

    def apply(self):
        try:
            snapshot = validate_document(self.raw_document())
        except (ValueError, TypeError) as exc:
            self._show_error(str(exc))
            return False
        selected_id = self.rows[self.list.currentRow()]["id"] if self.list.currentRow() >= 0 else None
        try:
            saved = save_document(self.path, snapshot)
        except (OSError, ValueError, TypeError):
            self._show_error("Не удалось сохранить локальный контекст. Введённые карточки остались в окне; проверьте доступ к папке данных и повторите применение.")
            return False
        self.saved = saved
        self.rows = deepcopy(document_raw(saved)["cards"])
        self.load_error = ""
        self._removed = None
        self.undo_button.hide()
        self.error.hide()
        self._rebuild(next((index for index, row in enumerate(self.rows) if row["id"] == selected_id), -1))
        self._edited()
        return True

    def set_running(self, running):
        self._running = running
        self.state.setText(
            "Тумблер сразу переключает уже применённый словарь. Карточки и частота сохраняются по кнопке «Применить», даже во время работы бота. Черновики остаются при переходе между вкладками.")
