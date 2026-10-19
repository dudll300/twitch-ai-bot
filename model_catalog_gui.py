"""On-demand provider catalog for the editable primary-model selector."""

from queue import Empty, Queue
from threading import Event, Thread

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QComboBox, QPushButton, QVBoxLayout, QWidget

from configuration import AI_FALLBACK_MODELS
from ai_client import redact_secret
from testing import Model, fetch_models
from ui_widgets import NoWheelComboBox, label


def catalog_worker(auth, output, cancel):
    try:
        result = ("catalog", auth, fetch_models(auth), "")
    except Exception as exc:
        result = ("catalog", auth, (), redact_secret(str(exc), auth.api_key))
    if not cancel.is_set():
        output.put(result)


class ModelCatalogControl(QWidget):
    def __init__(self, selected, get_credentials):
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
        self.combo.setAccessibleName("Основная модель")
        self.combo.addItems(AI_FALLBACK_MODELS)
        self.combo.setCurrentText(selected)
        self.combo.setToolTip("Раскройте список стрелкой справа или введите точный ID модели")
        layout.addWidget(self.combo)
        self.refresh_button = QPushButton("Загрузить модели провайдера")
        self.refresh_button.clicked.connect(self.refresh)
        layout.addWidget(self.refresh_button, 0, Qt.AlignLeft)
        self.status = label("Полный каталог загружается только по кнопке, для вашего API-ключа.", "muted", True)
        layout.addWidget(self.status)
        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

    def _button_state(self):
        self.refresh_button.setEnabled(self._editable and not self._busy and not self._closed)
        self.refresh_button.setText("Загрузка…" if self._busy else "Загрузить модели провайдера")

    def set_editable(self, editable):
        self._editable = editable
        self.combo.setEnabled(editable)
        self._button_state()

    def _replace_models(self, models):
        # Loading a catalog must never select a new model or discard a typed ID.
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
