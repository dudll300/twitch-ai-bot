"""On-demand provider catalog for the editable primary-model selector."""

from queue import Empty, Queue
from threading import Event, Thread

from PySide6.QtCore import QTimer, Qt, Signal, QEvent
from PySide6.QtWidgets import QComboBox, QPushButton, QVBoxLayout, QWidget, QCompleter, QHBoxLayout, QLineEdit

from configuration import AI_FALLBACK_MODELS, normalize
from ai_client import redact_secret
from testing import Model, fetch_models
from ui_widgets import NoWheelComboBox, label
from studio_rows import ModelChoiceDelegate


def catalog_worker(auth, output, cancel):
    try:
        result = ("catalog", auth, fetch_models(auth), "")
    except Exception as exc:
        result = ("catalog", auth, (), redact_secret(str(exc), auth.api_key))
    if not cancel.is_set():
        output.put(result)


from ui_widgets import StudioButton as QPushButton

class ModelCatalogControl(QWidget):
    models_changed = Signal(object)
    def __init__(self, selected, get_credentials, *, role="Модель ответов"):
        super().__init__()
        self.get_credentials = get_credentials
        self._events, self._cancel = Queue(), Event()
        self._busy, self._closed, self._editable = False, False, True
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.combo = NoWheelComboBox()
        self.combo.setEditable(True)
        self.combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.combo.setAccessibleName(role)
        self.combo.completer().setFilterMode(Qt.MatchContains)
        self.combo.completer().setCaseSensitivity(Qt.CaseInsensitive)
        self.combo.completer().setCompletionMode(QCompleter.PopupCompletion)
        self.combo.addItems(AI_FALLBACK_MODELS)
        self.combo.setCurrentText(selected)
        self.combo.setToolTip("Раскройте список стрелкой справа или введите точный ID модели")
        layout.addWidget(self.combo)
        self.refresh_button = QPushButton("Загрузить модели провайдера")
        self.refresh_button.clicked.connect(self.refresh)
        self.cancel_button = QPushButton("Отменить")
        self.cancel_button.clicked.connect(self.cancel)
        self.cancel_button.hide()
        actions = QHBoxLayout()
        actions.addWidget(self.refresh_button)
        actions.addWidget(self.cancel_button)
        actions.addStretch()
        layout.addLayout(actions)
        self.status = label("Доступность не проверена. Каталог загружается по кнопке.", "muted", True)
        layout.addWidget(self.status)
        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

    def _button_state(self):
        self.refresh_button.setEnabled(self._editable and not self._busy and not self._closed)
        self.refresh_button.setText("Загрузка…" if self._busy else "Загрузить модели провайдера")
        self.cancel_button.setVisible(self._busy)

    def cancel(self):
        self._cancel.set()
        self._cancel = Event()
        self._events = Queue()
        self._busy = False
        self._button_state()
        self.status.setText("Загрузка отменена. Выбранный ID модели сохранён в поле.")

    def set_editable(self, editable):
        self._editable = editable
        self.combo.setEnabled(editable)
        self._button_state()

    def _replace_models(self, models):
        # Loading a catalog must never select a new model or discard a typed ID.
        models = tuple(models)
        current = self.combo.currentText()
        self.combo.blockSignals(True)
        try:
            self.combo.clear()
            for model in models:
                denied = model.available is False
                caption = model.id + (" · недоступна для ключа" if denied else "")
                self.combo.addItem(caption, model.id)
                index = self.combo.count() - 1
                self.combo.setItemData(index, caption, Qt.ToolTipRole)
                self.combo.model().item(index).setEnabled(not denied)
            self.combo.setCurrentIndex(-1)
            self.combo.setEditText(current)
        finally:
            self.combo.blockSignals(False)
        self.models_changed.emit(tuple(models))

    def invalidate(self, *_):
        if self._closed:
            return
        # Keep the manual selection, but discard access flags from another key.
        self._replace_models(tuple(Model(model) for model in AI_FALLBACK_MODELS))
        self.status.setText("Параметры API изменились. Загрузите каталог по кнопке или введите ID вручную.")

    def refresh(self):
        if self._busy or self._closed or not self._editable:
            return
        try:
            auth = self.get_credentials()
        except (OSError, ValueError) as exc:
            self.status.setText(str(exc) + " Можно ввести ID вручную.")
            return
        self._busy = True
        self._button_state()
        self.status.setText("Загружаем модели для текущего API-ключа…")
        Thread(target=catalog_worker, args=(auth, self._events, self._cancel), daemon=True).start()

    def _poll(self):
        if self._closed:
            return
        while True:
            try:
                _, auth, models, error = self._events.get_nowait()
            except Empty:
                return
            self._busy = False
            self._button_state()
            try:
                current = self.get_credentials()
            except (OSError, ValueError):
                current = None
            if current != auth:
                self.invalidate()
            elif error:
                self.status.setText(error + " Можно ввести ID вручную.")
            else:
                self._replace_models(models)
                self.status.setText(f"Моделей в каталоге: {len(models)}. Недоступные для ключа отмечены в списке.")

    def shutdown(self):
        self._closed = True
        self._cancel.set()
        self._timer.stop()


class CheckedModelCombo(NoWheelComboBox):
    """A native model popup that stays open while checking multiple choices."""
    selection_changed = Signal(object)

    def __init__(self):
        super().__init__()
        self._selected, self._syncing = [], False
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        self.setCompleter(None)
        self.lineEdit().setReadOnly(True)
        self._pressed_row = None
        self.lineEdit().setPlaceholderText("Выбрать запасные модели")
        self.setAccessibleName("Запасные модели")
        self.setItemDelegate(ModelChoiceDelegate(self))
        self.model().itemChanged.connect(self._changed)
        self.currentIndexChanged.connect(self._caption)
        for widget in (self.view(), self.view().viewport(), self.lineEdit()):
            widget.installEventFilter(self)

    def replace_models(self, models, selected):
        self._selected = list(dict.fromkeys(selected))
        self._syncing = True
        self.blockSignals(True)
        try:
            self.clear()
            choices = {model.id: model for model in models}
            for model_id in self._selected:
                choices.setdefault(model_id, Model(model_id))
            for model in choices.values():
                denied = model.available is False
                self.addItem(model.id + (" · недоступна для ключа" if denied else ""), model.id)
                item = self.model().item(self.count() - 1)
                item.setData(denied, Qt.UserRole + 1)
                item.setCheckable(True)
                item.setCheckState(Qt.Checked if model.id in self._selected else Qt.Unchecked)
                # A saved, unavailable choice can still be removed.
                item.setEnabled(not denied or model.id in self._selected)
            self.setCurrentIndex(-1)
        finally:
            self.blockSignals(False)
            self._syncing = False
        self._caption()

    def _changed(self, item):
        if self._syncing:
            return
        model_id = item.data(Qt.UserRole)
        if item.checkState() == Qt.Checked and model_id not in self._selected:
            self._selected.append(model_id)
        elif item.checkState() != Qt.Checked and model_id in self._selected:
            self._selected.remove(model_id)
        self._syncing = True
        item.setEnabled(not item.data(Qt.UserRole + 1) or item.checkState() == Qt.Checked)
        self._syncing = False
        self._caption()
        self.selection_changed.emit(tuple(self._selected))

    def _caption(self, *_):
        summary = self._selected[0] if self._selected else ""
        if len(self._selected) > 1:
            summary += f" (+{len(self._selected) - 1})"
        self.setEditText(summary)
        self.lineEdit().setCursorPosition(0)
        self.setToolTip("Порядок вызова:\n" + "\n".join(f"{i + 1}. {model}" for i, model in enumerate(self._selected))
                        if self._selected else "Отметьте модели галочками. Esc или Enter закрывает список.")

    def _toggle(self, index):
        item = self.model().itemFromIndex(index)
        if item is not None and item.isEnabled():
            item.setCheckState(Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)

    def eventFilter(self, watched, event):
        if watched is self.lineEdit() and event.type() == QEvent.MouseButtonPress:
            self.showPopup()
            return True
        if watched is self.view().viewport() and event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self._pressed_row = self.view().indexAt(event.position().toPoint()).row()
        if watched is self.view().viewport() and event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            index = self.view().indexAt(event.position().toPoint())
            if index.row() == self._pressed_row:
                self._toggle(index)
            self._pressed_row = None
            return True
        if watched in (self.view(), self.view().viewport()) and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Space:
                self._toggle(self.view().currentIndex())
                return True
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                self.hidePopup()
                return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Space, Qt.Key_Down, Qt.Key_Up) and not self.view().isVisible():
            self.showPopup()
            event.accept()
        else:
            super().keyPressEvent(event)

    def hidePopup(self):
        super().hidePopup()
        self._pressed_row = None
        self._caption()


class FallbackModelControl(QWidget):
    """Ordered model checks, with the existing comma-separated settings API."""
    textChanged = Signal(str)

    def __init__(self, selected):
        super().__init__()
        self._raw, self._models = "", tuple(Model(model) for model in AI_FALLBACK_MODELS)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        self.combo = CheckedModelCombo()
        self.combo.selection_changed.connect(self._checked)
        layout.addWidget(self.combo)
        layout.addWidget(label("Модели вызываются в порядке отметок. Чтобы переместить модель в конец, снимите галочку и поставьте снова. Каталог общий с основной моделью.", "muted", True))
        row = QHBoxLayout()
        self.manual = QLineEdit()
        self.manual.setPlaceholderText("Свой ID модели")
        self.manual.setAccessibleName("Добавить запасную модель по ID")
        row.addWidget(self.manual, 1)
        self.add_button = QPushButton("Добавить")
        self.add_button.clicked.connect(self._add_manual)
        self.manual.returnPressed.connect(self._add_manual)
        row.addWidget(self.add_button)
        layout.addLayout(row)
        self.error = label("", "error", True)
        self._field_error = self.error
        layout.addWidget(self.error)
        self.manual.textChanged.connect(lambda *_: self.error.setText(""))
        self.textChanged.connect(lambda *_: self.error.setText(""))
        self.setFocusProxy(self.combo)
        self.setText(selected)

    def text(self):
        return self._raw

    def setText(self, value):
        previous, self._raw = self._raw, value
        selected = [part.strip() for part in value.split(",") if part.strip()]
        self.combo.replace_models(self._models, selected)
        if previous != value:
            self.textChanged.emit(value)

    def replace_models(self, models):
        self._models = tuple(models)
        self.combo.replace_models(self._models, self.combo._selected)

    def _checked(self, selected):
        value = ",".join(selected)
        if value != self._raw:
            self._raw = value
            self.textChanged.emit(value)

    def _add_manual(self):
        try:
            model = normalize("AI_MODEL", self.manual.text())
            if "," in model:
                raise ValueError("Добавляйте по одному ID модели.")
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        self.setText(",".join(dict.fromkeys([*self.combo._selected, model])))
        self.manual.clear()
