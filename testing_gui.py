"""Testing page. Daemon workers only put plain data into queues, never touch Qt."""

from queue import Empty, Queue
from threading import Event, Thread

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QHBoxLayout, QLineEdit,
                              QListWidgetItem, QPushButton,
                              QVBoxLayout, QWidget, QStackedWidget, QApplication)

from ai_client import redact_secret
from configuration import normalize
from testing import Model, test_model
from model_catalog_gui import catalog_worker
from safety_gui import DiagnosticPanel
from safety_settings import PolicyStore
from paths import data_dir
from prompt_builder import TEST_CASES
from ui_widgets import NoWheelComboBox, ScrollListWidget, ScrollPlainTextEdit, card, field, label, scroll_page, Disclosure


def comparison_worker(snapshot, pending, output, cancel):
    while not cancel.is_set():
        try:
            model = pending.get_nowait()
        except Empty:
            return
        if cancel.is_set():
            return
        result = test_model(snapshot, model, cancelled=cancel.is_set)
        if not cancel.is_set():
            output.put(("result", result))


from ui_widgets import StudioButton as QPushButton
from ui_widgets import SegmentedControl, ResponsiveColumns
from studio_rows import ModelChoiceDelegate

class TestingPage(QWidget):
    def __init__(self, get_credentials, get_snapshot, get_profiles, *, safety_store=None, get_answer_model=lambda: '', get_diagnostic_model=None):
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
        self._result_headers = {}
        self.get_answer_model = get_answer_model
        self.catalog = ()
        self.prompt_override = None

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
        self.prompt_source = label("Следующий тест использует текущий общий промпт из «Поведения».", "muted", True)
        request_layout.addWidget(self.prompt_source)
        self.restore_prompt_button = QPushButton("Вернуться к текущему общему промпту")
        self.restore_prompt_button.clicked.connect(self.clear_prompt_override)
        self.restore_prompt_button.hide()
        request_layout.addWidget(self.restore_prompt_button)
        self.answer_model = NoWheelComboBox()
        self.answer_model.setEditable(True)
        self.answer_model.setInsertPolicy(NoWheelComboBox.NoInsert)
        self.answer_model.setAccessibleName("Модель для пробного ответа")
        self.answer_model.setToolTip("Выберите модель из каталога или введите её точный ID")
        self.answer_model.addItem(self.get_answer_model(), "")
        self.load_models_button = QPushButton("Загрузить модели")
        self.load_models_button.clicked.connect(self.refresh_catalog)
        model_row = QHBoxLayout()
        model_row.addWidget(field("Модель для пробного ответа", self.answer_model,
                                  "Можно выбрать из каталога или ввести ID вручную."), 1)
        model_row.addWidget(self.load_models_button, 0, Qt.AlignVCenter)
        request_layout.addLayout(model_row)
        self.model_hint = label("Для сравнения нескольких моделей раскройте каталог ниже.", "muted", True)
        request_layout.addWidget(self.model_hint)
        self.case_combo = NoWheelComboBox()
        self.case_combo.addItem("Свой вопрос", None)
        for index, (title, _, _) in enumerate(TEST_CASES):
            self.case_combo.addItem(title, index)
        examples = QWidget()
        examples_layout = QVBoxLayout(examples)
        examples_layout.setContentsMargins(0, 0, 0, 0)
        examples_layout.addWidget(field("Примеры для проверки поведения и защиты", self.case_combo))
        self.expectation = label("Оцените ответ самостоятельно: проверки примеров не гарантируют защиту от всех обходов.", "muted", True)
        examples_layout.addWidget(self.expectation)
        request_layout.addWidget(Disclosure("Примеры вопросов", examples))
        self.question = ScrollPlainTextEdit()
        self.question.setPlaceholderText("Что спросить у каждой выбранной модели?")
        self.question.setAccessibleName("Пробный вопрос")
        self.question.setMaximumHeight(110)
        request_layout.addWidget(self.question)
        self.case_combo.currentIndexChanged.connect(self._select_case)
        self.question.textChanged.connect(self._case_expectation)
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
        self.sender.setToolTip("Владелец канала — имитация роли. Тест использует текущие черновики профилей; другого зрителя можно упомянуть в вопросе.")
        request_layout.addStretch()
        self.run_button = QPushButton("Получить ответы")
        self.run_button.setObjectName("primary")
        self.run_button.clicked.connect(self.start_test)
        self.cancel_button = QPushButton("Отменить запросы")
        self.cancel_button.clicked.connect(self.cancel_operation)
        self.cancel_button.hide()
        action_row = QHBoxLayout()
        action_row.addWidget(self.run_button, 1)
        action_row.addWidget(self.cancel_button)
        request_layout.addLayout(action_row)
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
        self.models = ScrollListWidget()
        self.models.setObjectName("modelList")
        self.models.setItemDelegate(ModelChoiceDelegate(self.models))
        self.models.setAccessibleName("Модели для сравнения")
        self.models.setMinimumHeight(180)
        self.models.setMaximumHeight(260)
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
        self.catalog_disclosure = Disclosure("Выбрать модели для сравнения", models)
        results, results_layout = card("Ответы моделей")
        self.context = label("Контекст будет показан после нажатия «Получить ответы».", "muted", True)
        self.context.setTextFormat(Qt.PlainText)
        results_layout.addWidget(Disclosure("Состав контекста теста", self.context))
        self.results_layout = QVBoxLayout()
        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        empty_layout.setContentsMargins(0, 16, 0, 22)
        empty_layout.addWidget(label("Пока тихо.", "reply"))
        empty_layout.addWidget(label("Задайте вопрос слева. Здесь появятся ответы выбранных моделей и результат их проверки.", "muted", True))
        self.results_layout.addWidget(empty)
        results_layout.addLayout(self.results_layout)
        results_layout.addStretch(1)
        layout.addWidget(ResponsiveColumns(request, results, threshold=850))
        layout.addWidget(self.catalog_disclosure)
        layout.addStretch()
        self.mode = NoWheelComboBox()
        self.mode.addItem('Сравнение моделей')
        self.mode.addItem('Проверка точного текста')
        self.mode.setAccessibleName('Режим тестирования')
        outer.addWidget(SegmentedControl(self.mode))
        self.modes = QStackedWidget()
        self.modes.addWidget(scroll_page(page))
        self.diagnostic = DiagnosticPanel(safety_store or PolicyStore(data_dir()), get_credentials, get_diagnostic_model or get_answer_model)
        self.modes.addWidget(self.diagnostic)
        outer.addWidget(self.modes, 1)
        request_layout.removeItem(action_row)
        action_row.setStretch(0, 0)
        action_row.addStretch(1)
        outer.addLayout(action_row)
        request_layout.removeWidget(self.status)
        outer.addWidget(self.status)
        self.mode.currentIndexChanged.connect(self._mode_changed)
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
        self._sync_answer_models()
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
        self.mode.setCurrentIndex(0)
        self.clear_prompt_override()
        self.refresh_profiles()
        self.sender.setCurrentIndex(self.sender.findData("profile"))
        self.viewer.setCurrentIndex(self.viewer.findData(index))

    def open_for_prompt(self, prompt=None):
        self.mode.setCurrentIndex(0)
        self.prompt_override = prompt
        self.prompt_source.setText("Следующий тест использует копию варианта из генератора. Общий промпт не заменён и не сохранён."
                                   if prompt is not None else "Следующий тест использует текущий общий промпт из «Поведения».")
        self.restore_prompt_button.setVisible(prompt is not None)
        self.sender.setCurrentIndex(self.sender.findData("viewer"))

    def clear_prompt_override(self):
        self.prompt_override = None
        self.prompt_source.setText("Следующий тест использует текущий общий промпт из «Поведения».")
        self.restore_prompt_button.hide()

    def _select_case(self, *_):
        index = self.case_combo.currentData()
        if index is not None:
            self.question.setPlainText(TEST_CASES[index][1])
        self._case_expectation()

    def _case_expectation(self):
        index = self.case_combo.currentData()
        if index is not None and self.question.toPlainText() == TEST_CASES[index][1]:
            self.expectation.setText("Ожидаемое поведение: " + TEST_CASES[index][2] + " Оцените фактический ответ самостоятельно.")
        else:
            self.expectation.setText("Свой вопрос. Оцените ответ самостоятельно: эти проверки не гарантируют защиту от всех обходов.")

    def _sender_changed(self, *_):
        mode = self.sender.currentData()
        self.viewer.setVisible(mode == "profile")
        self.login.setVisible(mode == "viewer")

    def invalidate_catalog(self, *_):
        self.catalog = ()
        self._catalog_auth = None
        self.models.clear()
        self._sync_answer_models()
        self.catalog_status.setText("Параметры API изменились. Обновите каталог для текущего ключа.")
        self._selection_count()

    def selected_models(self):
        return [self.models.item(i).data(Qt.UserRole) for i in range(self.models.count())
                if self.models.item(i).checkState() == Qt.Checked]

    def _sync_answer_models(self):
        use_default = self.answer_model.currentIndex() == 0 and self.answer_model.currentText() == self.answer_model.itemText(0)
        previous = self.answer_model.currentText()
        self.answer_model.blockSignals(True)
        self.answer_model.clear()
        self.answer_model.addItem(self.get_answer_model(), "")
        for model in self.catalog:
            self.answer_model.addItem(model.id, model.id)
            if model.available is False:
                self.answer_model.model().item(self.answer_model.count() - 1).setEnabled(False)
        if not use_default and previous:
            self.answer_model.setCurrentText(previous)
        self.answer_model.lineEdit().setCursorPosition(0)
        self.answer_model.blockSignals(False)

    def _test_models(self):
        selected = self.selected_models()
        if selected:
            return selected
        combo = self.answer_model
        if combo.currentIndex() == 0 and combo.currentText() == combo.itemText(0):
            return [self.get_answer_model()]
        return [combo.currentText().strip()]

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
        selected = self.selected_models()
        self.selected_count.setText("Выбрано: " + ", ".join(selected) if selected else "Для одного ответа используйте модель над вопросом")
        self.model_hint.setText("Сравниваем: " + ", ".join(selected) + ". Сбросьте выбор в каталоге, чтобы проверить одну модель."
                                if selected else "Для сравнения нескольких моделей раскройте каталог ниже.")
        self.answer_model.setEnabled(not selected and not self._testing_busy and not self._catalog_busy)
        if hasattr(self, "catalog_disclosure"):
            self.catalog_disclosure.title = "Модели: " + ", ".join(self.selected_models()) if self.selected_models() else "Модель ответов · выбрать другие"
            self.catalog_disclosure.set_expanded(self.catalog_disclosure.button.isChecked())

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
        for widget in (self.run_button, self.refresh_button, self.load_models_button, self.add_button, self.reset_button, self.manual,
                       self.models, self.sender, self.viewer, self.login, self.question,
                       self.case_combo, self.restore_prompt_button):
            widget.setEnabled(not busy)
        self.run_button.setText("Получаем ответы…" if self._testing_busy else "Получить ответы")
        self.refresh_button.setText("Загрузка…" if self._catalog_busy else "Обновить список")
        self.load_models_button.setText("Загрузка…" if self._catalog_busy else "Загрузить модели")
        self.answer_model.setEnabled(not busy and not self.selected_models())
        self.cancel_button.setVisible(busy)

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
            kwargs = {"prompt_override": self.prompt_override} if self.prompt_override is not None else {}
            models = self._test_models()
            snapshot = self.get_snapshot(models, self.question.toPlainText(),
                                         self.sender.currentData(),
                                         self.login.text() if self.sender.currentData() == "viewer" else "",
                                         self.viewer.currentData(), **kwargs)
        except (OSError, ValueError) as exc:
            self.status.setText(str(exc))
            return
        self._testing_busy = True
        self._remaining = len(snapshot.models)
        self._set_busy()
        source = "Проверяемый промпт: временный вариант из генератора." if kwargs else "Проверяемый промпт: общий из «Поведения»."
        self.context.setText(source + "\n" + snapshot.context)
        while self.results_layout.count():
            widget = self.results_layout.takeAt(0).widget()
            widget.deleteLater()
        self._result_widgets = {}
        self._result_headers = {}
        pending = Queue()
        for model in snapshot.models:
            result = ScrollPlainTextEdit()
            result.setReadOnly(True)
            result.setMinimumHeight(90)
            result.setMaximumHeight(140)
            result.setPlainText(model + "\nОжидание ответа…")
            result_card, box = card()
            header = label(model + " · ожидание", "section", True)
            self._result_headers[model] = header
            box.addWidget(header)
            box.addWidget(result)
            copy = QPushButton("Копировать ответ")
            copy.clicked.connect(lambda checked=False, editor=result: QApplication.clipboard().setText(editor.toPlainText()))
            box.addWidget(copy)
            self.results_layout.addWidget(result_card)
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
                self._sync_answer_models()
                self._filter_models()
                self._selection_count()
                self.catalog_status.setText((error + " Можно ввести ID вручную.") if error else
                    f"Моделей в каталоге: {len(models)}. Без отметки — доступность проверяется при запросе.")
            elif event[0] == "result":
                result = event[1]
                self._result_headers[result.model].setText(f"{result.model} · {result.seconds:.2f} с · " + ("ошибка" if result.error else "проверка пройдена"))
                self._result_widgets[result.model].setPlainText(
                    f"{result.model} · {result.seconds:.2f} с\n" +
                    ("Ошибка: " + result.error if result.error else
                     "Проверка безопасности: разрешено\n" + result.answer))
                self._remaining -= 1
                self.status.setText(f"Ожидаем ответы: {self._remaining}." if self._remaining else "Тест завершён.")
                if not self._remaining:
                    self._testing_busy = False
                    self._set_busy()

    def cancel_operation(self):
        self._cancel.set()
        self._cancel = Event()
        self._events = Queue()
        self._testing_busy = self._catalog_busy = False
        self._remaining = 0
        self._set_busy()
        self.status.setText("Операция отменена. Поздние результаты не будут показаны.")

    def _mode_changed(self, index):
        self._cancel.set()
        self._cancel = Event()
        # Old workers retain their old queue; late results cannot enter a new run.
        self._events = Queue()
        self._testing_busy = self._catalog_busy = False
        self._remaining = 0
        self._set_busy()
        self.diagnostic.invalidate()
        self.modes.setCurrentIndex(index)
        self.run_button.setVisible(index == 0)
        self.status.setVisible(index == 0)

    def open_diagnostic(self):
        self.mode.setCurrentIndex(1)

    def shutdown(self):
        self.diagnostic.shutdown()
        self._closed = True
        self._cancel.set()
        self._timer.stop()
        # Running HTTP calls finish on daemon threads; they only retain plain data.
        # No wait, Qt signal emission, logging or persistence during window teardown.
