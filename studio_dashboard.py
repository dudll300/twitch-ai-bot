"""Live session dashboard. Background history reads never touch Qt widgets."""
from queue import Queue, Empty
from threading import Event, Thread
from datetime import datetime

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QGridLayout

from message_history import MessageHistory, HistoryReadError
from ui_widgets import card, label, scroll_page, ScrollPlainTextEdit, Disclosure, ResponsiveColumns


class Dashboard(QWidget):
    history_requested = Signal()
    settings_requested = Signal()
    test_requested = Signal()
    setup_requested = Signal()

    def __init__(self, root):
        super().__init__()
        self.store = MessageHistory(root)
        self.session = ""
        self._loading = False
        self._closed = Event()
        self._events = Queue()
        self._signature = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        self.setup_card, setup = card("Готовы к первому эфиру?", "Настройте AI, подключите Twitch и проверьте первый ответ.")
        button = QPushButton("Пройти настройку")
        button.setProperty("variant", "primary")
        button.clicked.connect(self.setup_requested.emit)
        setup.addWidget(button)
        layout.addWidget(self.setup_card)
        states, box = card("Состояние сессии")
        grid = QGridLayout()
        grid.setSpacing(12)
        state_box = box
        self.states = {}
        for i, (key, name) in enumerate((("chat", "Чат Twitch"), ("eventsub", "Получение наград"), ("ai", "Последний запрос AI"))):
            grid.addWidget(label(name, "caption"), i, 0)
            value = label("Ещё не проверено", "caption", True)
            value.setProperty("role", "historyStatus")
            self.states[key] = value
            grid.addWidget(value, i, 1)
        box.addLayout(grid)
        self.auth_hint = label("", "caption", True)
        self.auth_hint.hide()
        box.addWidget(self.auth_hint)
        layout.addWidget(states)
        stats, box = card()
        metrics = QHBoxLayout()
        self.metrics = {}
        for key, title in (("questions", "Вопросов принято"), ("answers", "Ответов отправлено"), ("queue", "В очереди")):
            group = QVBoxLayout()
            value = label("0", "statValue")
            self.metrics[key] = value
            group.addWidget(value)
            group.addWidget(label(title, "muted"))
            metrics.addLayout(group)
        box.addLayout(metrics)
        self.processing = label("Новых вопросов пока нет", "muted", True)
        box.addWidget(self.processing)
        box.removeItem(metrics)
        box.removeWidget(self.processing)
        state_box.addLayout(metrics)
        state_box.addWidget(self.processing)
        stats.deleteLater()
        feed, box = card("Последние ответы и решения", "Только текущая сессия. Подробности доступны в истории.")
        self.feed = ScrollPlainTextEdit()
        self.feed.setReadOnly(True)
        self.feed.setAccessibleName("Ответы текущей сессии")
        self.feed.setMinimumHeight(150)
        self.feed.setPlaceholderText("Запустите бота — здесь появятся ответы и решения.")
        box.addWidget(self.feed)
        button = QPushButton("Открыть историю")
        button.clicked.connect(self.history_requested.emit)
        box.addWidget(button)
        inspector, box = card("Сохранённые настройки")
        self.inspector_title = box.itemAt(0).widget()
        self.inspector_values = {}
        for key, title in (("channel", "Канал"), ("model", "Модель ответов"), ("reward", "Награда"), ("autonomous", "Самостоятельные реплики"), ("context", "Знания канала")):
            box.addWidget(label(title, "muted"))
            value = label("—", "caption", True)
            self.inspector_values[key] = value
            box.addWidget(value)
        self.connection_pill = label("Остановлен", "caption", True)
        box.addWidget(self.connection_pill)
        for text, signal in (("Настройки подключения", self.settings_requested), ("Проверить ответ", self.test_requested)):
            button = QPushButton(text)
            button.clicked.connect(signal.emit)
            box.addWidget(button)
        layout.addWidget(ResponsiveColumns(feed, inspector, threshold=900, ratios=(2, 1)))
        self.log = ScrollPlainTextEdit()
        self.log.setObjectName("activityLog")
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(180)
        self.log.setAccessibleName("Технический журнал работы")
        self.log.document().setMaximumBlockCount(2000)
        layout.addWidget(Disclosure("Технический журнал", self.log))
        outer.addWidget(scroll_page(page))
        self._timer = QTimer(self)
        self._timer.setInterval(1500)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self._poll = QTimer(self)
        self._poll.setInterval(100)
        self._poll.timeout.connect(self._drain)
        self._poll.start()

    def update_state(self, state):
        if state.session != self.session:
            self._signature = None
            self.feed.clear()
        self.session = state.session
        captions = {"unknown": "Ещё не проверено", "connecting": "Подключение…", "ready": "Подключено", "disconnected": "Связь прервана", "error": "Ошибка"}
        for key in ("chat", "eventsub"):
            value = getattr(state, key)
            self.states[key].setText(captions.get(value, "Ещё не проверено"))
            self.states[key].setProperty("state", "sent" if value == "ready" else "error" if value in ("error", "disconnected") else "idle")
            self.states[key].style().unpolish(self.states[key])
            self.states[key].style().polish(self.states[key])
        ai = {"unknown": "Ещё не проверен", "pending": "Получаем ответ…", "success": "Последний запрос успешен", "error": "Ошибка последнего запроса", "cancelled": "Запрос отменён до отправки"}
        suffix = " · " + state.ai_model if state.ai_model else ""
        if state.ai_time and state.ai in ("success", "error"):
            try:
                suffix += " · " + datetime.fromtimestamp(state.ai_time).strftime("%H:%M:%S")
            except (ValueError, OverflowError, OSError):
                pass
        self.states["ai"].setText(ai.get(state.ai, "Ещё не проверен") + suffix)
        self.states["ai"].setProperty("state", "sent" if state.ai == "success" else "error" if state.ai == "error" else "idle")
        self.states["ai"].style().unpolish(self.states["ai"])
        self.states["ai"].style().polish(self.states["ai"])
        for key in self.metrics:
            self.metrics[key].setText(str(getattr(state, key)))
        self.processing.setText("Обрабатывается вопрос" if state.processing else "Ожидание новых вопросов")

    def refresh(self):
        if self._loading or self._closed.is_set() or not self.session:
            return
        self._loading = True
        session, output, closed, store = self.session, self._events, self._closed, self.store
        def read():
            try:
                result = store.page(session=session, limit=12, cancel=closed)["rows"]
                output.put((session, result, ""))
            except HistoryReadError as exc:
                output.put((session, [], str(exc)))
        Thread(target=read, daemon=True).start()

    def _drain(self):
        try:
            session, rows, error = self._events.get_nowait()
        except Empty:
            return
        self._loading = False
        if session != self.session or self._closed.is_set():
            return
        names = {"sent": "Отправлено", "preview": "Предпросмотр", "generating": "Готовится", "generated": "Подготовлено", "silent": "Промолчал", "skipped": "Пропущено", "cancelled": "Отменено", "error": "Ошибка", "send_error": "Ошибка отправки", "rejected": "Отклонено"}
        text = error or "\n\n".join(names.get(row["status"], row["status"]) + (" · @" + row["viewer"] if row["viewer"] else "") + "\n" + (row["sent_text"] or row["answer"] or row["reason"] or "Ожидание результата…") for row in rows)
        if text != self._signature:
            bar = self.feed.verticalScrollBar()
            position = bar.value()
            self.feed.setPlainText(text)
            bar.setValue(position)
            self._signature = text

    def shutdown(self):
        self._closed.set()
        self._timer.stop()
        self._poll.stop()
