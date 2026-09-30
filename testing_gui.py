"""Testing page. Daemon workers only put plain data into queues, never touch Qt."""

from queue import Empty, Queue
from threading import Event, Thread

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QHBoxLayout, QLineEdit, QListWidget,
                              QListWidgetItem, QPlainTextEdit, QPushButton,
                              QVBoxLayout, QWidget)

from ai_client import redact_secret
from configuration import normalize
from testing import Model, fetch_models, test_model
from ui_widgets import NoWheelComboBox, card, field, label, scroll_page


def catalog_worker(auth, output, cancel):
    try:
        result = ("catalog", auth, fetch_models(auth), "")
    except Exception as exc:
        result = ("catalog", auth, (), redact_secret(str(exc), auth.api_key))
    if not cancel.is_set():
        output.put(result)


def comparison_worker(snapshot, pending, output, cancel):
    while not cancel.is_set():
        try:
            model = pending.get_nowait()
        except Empty:
            return
        if cancel.is_set():
            return
        result = test_model(snapshot, model)
        if not cancel.is_set():
            output.put(("result", result))


class TestingPage(QWidget):
    def __init__(self, get_credentials, get_snapshot, get_profiles):
        super().__init__()
        self.get_credentials = get_credentials
        self.get_snapshot = get_snapshot
        self.get_profiles = get_profiles
        self._events = Queue()
        self._cancel = Event()
        self._closed = False
        self._catalog_busy = False
        self._testing_busy = False
        self._catalog_auth = None
        self._remaining = 0
        self._result_widgets = {}
        self.catalog = ()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(16)
        layout.addWidget(label(
            "Тестовые запросы могут расходовать баланс AI API. Base URL и ключ берутся из «Подключения». "
            "Вопросы и ответы теста не записываются в память, историю или журнал бота.", "muted", True))
        request, request_layout = card("Пробный вопрос")
        self.question = QPlainTextEdit()
        self.question.setPlaceholderText("Что спросить у каждой выбранной модели?")
        self.question.setAccessibleName("Пробный вопрос")
        self.question.setMaximumHeight(110)
        request_layout.addWidget(self.question)
        self.sender = NoWheelComboBox()
        for title, mode in (("Обычный зритель", "viewer"), ("Зритель из профилей", "profile"),
                            ("Владелец канала · имитация роли", "owner")):
            self.sender.addItem(title, mode)
        request_layout.addWidget(field("Отправитель", self.sender))
        self.viewer = NoWheelComboBox()
        self.viewer.setAccessibleName("Выбор профиля для теста")
        request_layout.addWidget(self.viewer)
        self.login = QLineEdit()
        self.login.setPlaceholderText("Пробный логин · можно оставить пустым")
        self.login.setAccessibleName("Пробный логин")
        request_layout.addWidget(self.login)
        request_layout.addWidget(label("Владелец канала — имитация роли без входа в Twitch.", "muted", True))
        request_layout.addStretch()
        self.run_button = QPushButton("Получить ответы")
        self.run_button.setObjectName("primary")
        self.run_button.clicked.connect(self.start_test)
        request_layout.addWidget(self.run_button)
        self.status = label("", "muted", True)
        request_layout.addWidget(self.status)

        models, models_layout = card("Каталог моделей", "Выберите несколько моделей для сравнения")
        tools = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setMinimumWidth(120)
        self.search.setPlaceholderText("Найти модель по ID")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter_models)
        tools.addWidget(self.search, 1)
        self.refresh_button = QPushButton("Обновить список")
        self.refresh_button.clicked.connect(self.refresh_catalog)
        tools.addWidget(self.refresh_button)
        models_layout.addLayout(tools)
        selection = QHBoxLayout()
        self.selected_count = label("Выбрано: 0", "muted")
        selection.addWidget(self.selected_count, 1)
        self.reset_button = QPushButton("Сбросить выбор")
        self.reset_button.clicked.connect(self.reset_selection)
        selection.addWidget(self.reset_button)
        models_layout.addLayout(selection)
        self.models = QListWidget()
        self.models.setObjectName("modelList")
        self.models.setAccessibleName("Модели для сравнения")
        self.models.setMinimumHeight(360)
        self.models.setMaximumHeight(520)
        self.models.itemChanged.connect(self._selection_count)
        models_layout.addWidget(self.models, 1)
        manual_row = QHBoxLayout()
        self.manual = QLineEdit()
        self.manual.setPlaceholderText("Ввести точный ID модели вручную")
        manual_row.addWidget(self.manual, 1)
        self.add_button = QPushButton("Добавить ID")
        self.add_button.clicked.connect(self.add_manual)
        self.manual.returnPressed.connect(self.add_manual)
        manual_row.addWidget(self.add_button)
        models_layout.addLayout(manual_row)
        self.catalog_status = label("Нажмите «Обновить список». Доступность без отметки проверяется при запросе.", "muted", True)
        models_layout.addWidget(self.catalog_status)
        layout.addWidget(models)
        layout.addWidget(request)
        results, results_layout = card("Ответы моделей")
        self.context = label("Контекст будет показан после нажатия «Получить ответы».", "muted", True)
        self.context.setTextFormat(Qt.PlainText)
        results_layout.addWidget(self.context)
        self.results_layout = QVBoxLayout()
        results_layout.addLayout(self.results_layout)
        layout.addWidget(results)
        layout.addStretch()
        outer.addWidget(scroll_page(page))
        # Provider errors and IDs are always plain text, never HTML.
        for widget in (self.status, self.catalog_status):
            widget.setTextFormat(Qt.PlainText)
        self.sender.currentIndexChanged.connect(self._sender_changed)
        self.refresh_profiles()
        self._sender_changed()
        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

    def refresh_profiles(self):
        previous = self.viewer.currentData()
        self.viewer.clear()
        for index, row in enumerate(self.get_profiles()):
            name = row["login"].strip() or "ID " + row["user_id"].strip()
            caption = name + (" · отключён" if not row["enabled"] else "")
            self.viewer.addItem(caption, index)
        selected = self.viewer.findData(previous)
        if selected >= 0:
            self.viewer.setCurrentIndex(selected)

    def open_for_profile(self, index):
        self.refresh_profiles()
        self.sender.setCurrentIndex(self.sender.findData("profile"))
        self.viewer.setCurrentIndex(self.viewer.findData(index))

    def open_for_prompt(self):
        self.sender.setCurrentIndex(self.sender.findData("viewer"))

    def _sender_changed(self, *_):
        mode = self.sender.currentData()
        self.viewer.setVisible(mode == "profile")
        self.login.setVisible(mode == "viewer")

    def invalidate_catalog(self, *_):
        self.catalog = ()
        self._catalog_auth = None
        self.models.clear()
        self.catalog_status.setText("Параметры API изменились. Обновите каталог для текущего ключа.")
        self._selection_count()

    def selected_models(self):
        return [self.models.item(i).data(Qt.UserRole) for i in range(self.models.count())
                if self.models.item(i).checkState() == Qt.Checked]

    def _add_model(self, model, checked=False):
        suffix = " · недоступна для ключа" if model.available is False else ""
        item = QListWidgetItem(model.id + suffix)
        item.setToolTip(model.id + suffix)
        item.setData(Qt.UserRole, model.id)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked if checked and model.available is not False else Qt.Unchecked)
        if model.available is False:
            item.setFlags(item.flags() & ~Qt.ItemIsEnabled & ~Qt.ItemIsUserCheckable)
        self.models.addItem(item)

    def add_manual(self):
        if self._testing_busy or self._catalog_busy or self._closed:
            return
        try:
            model_id = normalize("AI_MODEL", self.manual.text())
            auth = self.get_credentials()
            if auth.api_key in model_id:
                raise ValueError("В поле ID модели указан API-ключ.")
            for i in range(self.models.count()):
                item = self.models.item(i)
                if item.data(Qt.UserRole) == model_id:
                    if item.flags() & Qt.ItemIsEnabled:
                        item.setCheckState(Qt.Checked)
                    else:
                        self.status.setText("Эта модель отмечена каталогом как недоступная для ключа.")
                    return
            self._add_model(Model(model_id), checked=True)
            self.manual.clear()
            self._filter_models()
            self._selection_count()
        except (OSError, ValueError) as exc:
            self.status.setText(str(exc))

    def _filter_models(self, *_):
        query = self.search.text().strip().casefold()
        for i in range(self.models.count()):
            item = self.models.item(i)
            item.setHidden(query not in item.data(Qt.UserRole).casefold())

    def _selection_count(self, *_):
        self.selected_count.setText(f"Выбрано: {len(self.selected_models())}")

    def reset_selection(self):
        if self._testing_busy or self._catalog_busy or self._closed:
            return
        self.models.blockSignals(True)
        for i in range(self.models.count()):
            self.models.item(i).setCheckState(Qt.Unchecked)
        self.models.blockSignals(False)
        self.models.clearSelection()
        self._selection_count()

    def _set_busy(self):
        busy = self._testing_busy or self._catalog_busy
        for widget in (self.run_button, self.refresh_button, self.add_button, self.reset_button, self.manual,
                       self.models, self.sender, self.viewer, self.login, self.question):
            widget.setEnabled(not busy)
        self.run_button.setText("Получаем ответы…" if self._testing_busy else "Получить ответы")
        self.refresh_button.setText("Загрузка…" if self._catalog_busy else "Обновить список")

    def refresh_catalog(self):
        if self._testing_busy or self._catalog_busy or self._closed:
            return
        try:
            auth = self.get_credentials()
        except (OSError, ValueError) as exc:
            self.catalog_status.setText(str(exc) + " Можно ввести ID вручную.")
            return
        self._catalog_busy = True
        self._set_busy()
        self.catalog_status.setText("Загружаем модели для текущего API-ключа…")
        Thread(target=catalog_worker, args=(auth, self._events, self._cancel), daemon=True).start()

    def start_test(self):
        if self._testing_busy or self._catalog_busy or self._closed:
            return
        try:
            snapshot = self.get_snapshot(self.selected_models(), self.question.toPlainText(),
                                         self.sender.currentData(),
                                         self.login.text() if self.sender.currentData() == "viewer" else "",
                                         self.viewer.currentData())
        except (OSError, ValueError) as exc:
            self.status.setText(str(exc))
            return
        self._testing_busy = True
        self._remaining = len(snapshot.models)
        self._set_busy()
        self.context.setText(snapshot.context)
        while self.results_layout.count():
            widget = self.results_layout.takeAt(0).widget()
            widget.deleteLater()
        self._result_widgets = {}
        pending = Queue()
        for model in snapshot.models:
            result = QPlainTextEdit()
            result.setReadOnly(True)
            result.setMinimumHeight(90)
            result.setMaximumHeight(140)
            result.setPlainText(model + "\nОжидание ответа…")
            self.results_layout.addWidget(result)
            self._result_widgets[model] = result
            pending.put(model)
        self.status.setText(f"Ожидаем ответы: {self._remaining}. Настройки зафиксированы на момент нажатия.")
        for _ in range(min(4, len(snapshot.models))):
            Thread(target=comparison_worker, args=(snapshot, pending, self._events, self._cancel),
                   daemon=True).start()

    def _poll(self):
        if self._closed:
            return
        while True:
            try:
                event = self._events.get_nowait()
            except Empty:
                return
            if event[0] == "catalog":
                _, auth, models, error = event
                self._catalog_busy = False
                self._set_busy()
                try:
                    current = self.get_credentials()
                except (OSError, ValueError):
                    current = None
                if current != auth:
                    self.invalidate_catalog()
                    continue
                selected = set(self.selected_models())
                self.models.clear()
                self.catalog = models
                self._catalog_auth = auth
                for model in models:
                    self._add_model(model, checked=model.id in selected)
                self._filter_models()
                self._selection_count()
                self.catalog_status.setText((error + " Можно ввести ID вручную.") if error else
                    f"Моделей в каталоге: {len(models)}. Без отметки — доступность проверяется при запросе.")
            elif event[0] == "result":
                result = event[1]
                self._result_widgets[result.model].setPlainText(
                    f"{result.model} · {result.seconds:.2f} с\n" +
                    ("Ошибка: " + result.error if result.error else result.answer))
                self._remaining -= 1
                self.status.setText(f"Ожидаем ответы: {self._remaining}." if self._remaining else "Тест завершён.")
                if not self._remaining:
                    self._testing_busy = False
                    self._set_busy()

    def shutdown(self):
        self._closed = True
        self._cancel.set()
        self._timer.stop()
        # Running HTTP calls finish on daemon threads; they only retain plain data.
        # No wait, Qt signal emission, logging or persistence during window teardown.
