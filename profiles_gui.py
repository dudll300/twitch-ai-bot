"""Searchable viewer profiles with an inline editor and reversible removal."""

from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QGridLayout, QHBoxLayout, QLineEdit, QListWidgetItem,
    QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from profiles import MAX_PROFILE_PROMPT, ProfileError, load_profiles, validate_profiles
from ui_widgets import ScrollListWidget, ScrollPlainTextEdit, ToggleSwitch, card, field, label, scroll_page


class ProfilesEditor(QWidget):
    changed = Signal()
    test_requested = Signal(int)

    def __init__(self, path: Path):
        super().__init__()
        self.path = path
        self.rows = []
        self.load_error = ""
        self._loading = False
        self._editable = True
        self._removed = []
        try:
            self.rows = load_profiles(path)
        except ProfileError as exc:
            self.load_error = str(exc)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(16)
        self.error = label(self.load_error, "error", True)
        self.error.setVisible(bool(self.load_error))
        outer.addWidget(self.error)
        self.retry = QPushButton("Перечитать файл профилей")
        self.retry.clicked.connect(self._retry_load)
        self.retry.setVisible(bool(self.load_error))
        outer.addWidget(self.retry, 0, Qt.AlignLeft)

        columns = QHBoxLayout()
        columns.setSpacing(20)
        left, left_layout = card()
        left.setMinimumWidth(260)
        left.setMaximumWidth(300)
        left_layout.setContentsMargins(16, 16, 16, 16)
        header = QHBoxLayout()
        self.count = label("", "section")
        header.addWidget(self.count)
        header.addStretch()
        self.add_button = QPushButton("+ Добавить")
        self.add_button.setProperty("variant", "quiet")
        self.add_button.clicked.connect(self.add_profile)
        header.addWidget(self.add_button)
        left_layout.addLayout(header)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Найти логин или ID")
        self.search.setAccessibleName("Поиск зрителей")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        left_layout.addWidget(self.search)
        self.list = ScrollListWidget()
        self.list.setObjectName("profileList")
        self.list.setAccessibleName("Профили зрителей")
        self.list.currentRowChanged.connect(self._select)
        left_layout.addWidget(self.list, 1)
        self.empty_search = label("", "muted", True)
        left_layout.addWidget(self.empty_search)
        self.undo_button = QPushButton("Отменить удаление")
        self.undo_button.clicked.connect(self._undo)
        self.undo_button.setVisible(False)
        left_layout.addWidget(self.undo_button)
        columns.addWidget(left, 1)

        self.stack = QStackedWidget()
        empty, empty_layout = card()
        empty_layout.addStretch()
        empty_layout.addWidget(label("Каждому — свой подход", "section"))
        empty_layout.addWidget(label(
            "Добавьте зрителя и задайте личную инструкцию: как обращаться, о чём шутить или какой тон использовать.",
            "muted", True))
        empty_layout.addWidget(label("Общий промпт продолжит действовать для всех.", "muted", True))
        empty_layout.addStretch()
        self.stack.addWidget(empty)

        editor, editor_layout = card()
        heading = QHBoxLayout()
        self.editor_title = label("Новый профиль", "section")
        heading.addWidget(self.editor_title, 1)
        self.enabled = ToggleSwitch("Включён")
        self.enabled.setAccessibleName("Персонализация включена")
        heading.addWidget(self.enabled)
        editor_layout.addLayout(heading)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self.login = QLineEdit()
        self.login.setPlaceholderText("@username или ссылка")
        self.user_id = QLineEdit()
        self.user_id.setPlaceholderText("Например, 123456789")
        grid.addWidget(field("Twitch-логин", self.login), 0, 0)
        grid.addWidget(field("Twitch ID · необязательно", self.user_id), 0, 1)
        editor_layout.addLayout(grid)
        editor_layout.addWidget(label(
            "Достаточно логина или ID. Если указан ID, используем его — даже после смены ника.", "muted", True))
        self.prompt = ScrollPlainTextEdit()
        self.prompt.setPlaceholderText("Например: обращайся по имени Алекс. Отвечай дружелюбно и кратко. Не шути про возраст.")
        self.prompt.setMinimumHeight(180)
        editor_layout.addWidget(field("Личная инструкция", self.prompt), 1)
        bottom = QHBoxLayout()
        self.counter = label("", "muted")
        bottom.addWidget(self.counter)
        bottom.addStretch()
        self.test_button = QPushButton("Проверить ответ")
        self.test_button.clicked.connect(lambda: self.test_requested.emit(self.list.currentRow()))
        bottom.addWidget(self.test_button)
        self.remove_button = QPushButton("Удалить профиль")
        self.remove_button.setProperty("variant", "danger")
        self.remove_button.clicked.connect(self._remove)
        bottom.addWidget(self.remove_button)
        editor_layout.addLayout(bottom)
        editor_layout.addWidget(label(
            "Добавляется к общему промпту только для этого зрителя. Изменения применяются после сохранения и перезапуска бота.",
            "muted", True))
        self.stack.addWidget(scroll_page(editor))
        columns.addWidget(self.stack, 3)
        outer.addLayout(columns, 1)
        for widget in (self.login, self.user_id):
            widget.textChanged.connect(self._store)
        self.prompt.textChanged.connect(self._store)
        self.enabled.toggled.connect(self._store)
        self._rebuild(0 if self.rows else -1)
        self.set_editable(True)

    def _retry_load(self):
        try:
            self.rows = load_profiles(self.path)
        except ProfileError as exc:
            self.error.setText(str(exc))
            return
        self.load_error = ""
        self.error.hide()
        self.retry.hide()
        self._rebuild(0 if self.rows else -1)
        self.set_editable(self._editable)

    def _item_text(self, row):
        name = row["login"].strip() or ("ID " + row["user_id"].strip() if row["user_id"].strip() else "Новый профиль")
        detail = "По Twitch ID" if row["user_id"].strip() else "По логину"
        return name + "\n" + (detail if row["enabled"] else "Отключён")

    def _rebuild(self, selected):
        self.list.blockSignals(True)
        self.list.clear()
        for row in self.rows:
            item = QListWidgetItem(self._item_text(row))
            item.setToolTip(row["login"] or row["user_id"])
            self.list.addItem(item)
        self.list.setCurrentRow(selected)
        self.list.blockSignals(False)
        self.count.setText(f"Зрители · {len(self.rows)}")
        self._select(selected)
        self._filter()

    def _select(self, index):
        if index < 0 or index >= len(self.rows):
            self.stack.setCurrentIndex(0)
            return
        self._loading = True
        row = self.rows[index]
        self.login.setText(row["login"])
        self.user_id.setText(row["user_id"])
        self.prompt.setPlainText(row["prompt"])
        self.enabled.setChecked(row["enabled"])
        self._loading = False
        self._update_caption()
        self.stack.setCurrentIndex(1)

    def _update_caption(self):
        self.editor_title.setText(self.login.text().strip() or ("ID " + self.user_id.text().strip() if self.user_id.text().strip() else "Новый профиль"))
        self.counter.setText(f"{len(self.prompt.toPlainText()):,} / {MAX_PROFILE_PROMPT:,}".replace(",", " "))

    def _store(self, *_):
        index = self.list.currentRow()
        if self._loading or index < 0:
            return
        self.rows[index] = {"login": self.login.text(), "user_id": self.user_id.text(),
                            "prompt": self.prompt.toPlainText(), "enabled": self.enabled.isChecked()}
        self.list.item(index).setText(self._item_text(self.rows[index]))
        self._update_caption()
        self.error.hide()
        self.changed.emit()

    def _filter(self, *_):
        query = self.search.text().strip().casefold().lstrip("@")
        visible = 0
        for index, row in enumerate(self.rows):
            matches = query in row["login"].casefold() or query in row["user_id"]
            self.list.item(index).setHidden(not matches)
            visible += matches
        self.empty_search.setText("Ничего не найдено" if self.rows else "Пока нет персональных инструкций")
        self.empty_search.setVisible(visible == 0)

    def add_profile(self):
        if not self._editable or self.load_error:
            return
        self.search.clear()
        self.rows.append({"login": "", "user_id": "", "prompt": "", "enabled": True})
        self._rebuild(len(self.rows) - 1)
        self.login.setFocus()
        self.changed.emit()

    def _remove(self):
        index = self.list.currentRow()
        if index < 0 or not self._editable:
            return
        self._removed.append((index, self.rows.pop(index)))
        self.undo_button.show()
        self._rebuild(min(index, len(self.rows) - 1))
        self.changed.emit()

    def _undo(self):
        if not self._removed or not self._editable:
            return
        index, row = self._removed.pop()
        self.rows.insert(index, row)
        self.search.clear()
        self._rebuild(index)
        self.undo_button.setVisible(bool(self._removed))
        self.changed.emit()

    def validated(self):
        if self.load_error:
            raise ProfileError(self.load_error)
        try:
            return validate_profiles(self.rows)
        except ProfileError as exc:
            self.search.clear()
            if exc.index is not None:
                self.list.setCurrentRow(exc.index)
            self.error.setText(str(exc))
            self.error.show()
            raise

    def saved(self, rows):
        self.rows = deepcopy(rows)
        self._removed.clear()
        self.undo_button.hide()
        self._rebuild(self.list.currentRow())

    def set_editable(self, editable):
        self._editable = editable
        for widget in (self.add_button, self.remove_button, self.undo_button, self.login,
                       self.user_id, self.prompt, self.enabled):
            widget.setEnabled(editable and not self.load_error)
