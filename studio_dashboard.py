"""Live session dashboard. Background history reads never touch Qt widgets."""
from queue import Queue, Empty
from threading import Event, Thread
from datetime import datetime

from PySide6.QtCore import QTimer, Signal, Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QGridLayout, QListWidgetItem

from message_history import MessageHistory, HistoryReadError
from ui_widgets import card, label, scroll_page, ScrollPlainTextEdit, Disclosure, ResponsiveColumns
from ui_widgets import panel_badge, ScrollListWidget
from studio_rows import OrbitRowDelegate


from ui_widgets import StudioButton as QPushButton

class Dashboard(QWidget):
    history_requested = Signal()
    settings_requested = Signal()
    test_requested = Signal()
    setup_requested = Signal()
    autonomy_requested = Signal()

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
        layout.setSpacing(20)
        self.setup_card, setup = card("Готовы к первому эфиру?", "Подключите Twitch и проверьте первый ответ.")
        button = QPushButton("Настроить подключения")
        button.setProperty("variant", "primary")
        button.clicked.connect(self.setup_requested.emit)
        setup.addWidget(button)
        layout.addWidget(self.setup_card)
        conversation, box = card("Разговор с ботом")
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        self.conversation_state = label("На паузе", "historyStatus")
        panel_badge(box, self.conversation_state)
        self.conversation = ScrollListWidget()
        self.conversation.setProperty("presentation", "rows")
        self.conversation.setItemDelegate(OrbitRowDelegate(self.conversation, "chat"))
        self.conversation.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.conversation.setAccessibleName("Вопросы и ответы текущей сессии")
        self.conversation.setMinimumHeight(310)
        self.conversation.setSelectionMode(ScrollListWidget.NoSelection)
        self.chat_empty = label("Пока тихо. Здесь появятся вопросы через награду и реплики бота в этом запуске.", "muted", True)
        empty_box = QWidget()
        empty_layout = QVBoxLayout(empty_box)
        empty_layout.setContentsMargins(24, 32, 24, 32)
        empty_layout.addWidget(self.chat_empty)
        self.empty_conversation = empty_box
        box.addWidget(empty_box)
        box.addWidget(self.conversation, 1)
        participation = QWidget()
        row = QHBoxLayout(participation)
        row.setContentsMargins(22, 18, 22, 18)
        copy = QVBoxLayout()
        copy.setSpacing(5)
        self.participation_title = label("Участие выключено", "caption")
        self.participation_hint = label("Вопросы через награды остаются доступны", "muted", True)
        copy.addWidget(self.participation_title)
        copy.addWidget(self.participation_hint)
        row.addLayout(copy, 1)
        button = QPushButton("Настроить")
        button.setProperty("variant", "quiet")
        button.clicked.connect(self.autonomy_requested.emit)
        row.addWidget(button)
        box.addWidget(participation)
        self.feed = ScrollPlainTextEdit(self)
        self.feed.setReadOnly(True)
        self.feed.hide()
        reply, reply_box = card("Последняя реплика")
        self.reply_time = label("—", "muted")
        panel_badge(reply_box, self.reply_time)
        self.reply_origin = label("Ждём первый опубликованный ответ", "caption", True)
        self.reply_question = label("", "muted", True)
        self.last_reply = label("Разговор начнётся с первого вопроса.", "reply", True)
        self.last_reply.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.reply_meta = label("", "muted", True)
        for value in (self.reply_origin, self.reply_question, self.last_reply, self.reply_meta):
            value.setTextFormat(Qt.PlainText)
            reply_box.addWidget(value)
        button = QPushButton("Открыть историю")
        button.setProperty("variant", "quiet")
        button.clicked.connect(self.history_requested.emit)
        reply_box.addWidget(button, 0, Qt.AlignRight)
        queue, queue_box = card("Дальше в очереди")
        self.queue_count = label("0", "eyebrow")
        panel_badge(queue_box, self.queue_count)
        self.processing = label("Ожидание новых вопросов", "caption", True)
        self.queue_summary = label("Очередь пуста. Следующий вопрос появится после активации награды.", "muted", True)
        queue_box.addWidget(self.processing)
        queue_box.addWidget(self.queue_summary)
        states, state_box = card("Подключения")
        self.states, self.metrics = {}, {}
        for key, name in (("chat", "Чат Twitch"), ("eventsub", "Получение наград"), ("ai", "Последний запрос AI")):
            line = QHBoxLayout()
            line.addWidget(label(name, "muted"), 1)
            value = label("Ещё не проверено", "caption", True)
            line.addWidget(value, 1)
            self.states[key] = value
            state_box.addLayout(line)
        for key in ("questions", "answers", "queue"):
            self.metrics[key] = label("0", "statValue")
            self.metrics[key].setParent(self)
            self.metrics[key].hide()
        self.auth_hint = label("", "caption", True)
        self.auth_hint.hide()
        layout.addWidget(self.auth_hint)
        inspector, box = card("Сохранённые настройки")
        self.inspector_title = box.title_label
        self.inspector_values = {}
        for key, title in (("channel", "Канал"), ("model", "Модель ответов"), ("reward", "Награда"), ("autonomous", "Самостоятельные реплики"), ("context", "Знания канала")):
            line = QHBoxLayout()
            line.addWidget(label(title, "muted"), 1)
            value = label("—", "caption", True)
            self.inspector_values[key] = value
            line.addWidget(value, 1)
            box.addLayout(line)
        self.connection_pill = label("Остановлен", "caption", True)
        box.addWidget(self.connection_pill)
        for text, signal in (("Настройки подключения", self.settings_requested), ("Проверить ответ", self.test_requested)):
            button = QPushButton(text)
            button.clicked.connect(signal.emit)
            box.addWidget(button)
        right = QWidget()
        right_box = QVBoxLayout(right)
        right_box.setContentsMargins(0, 0, 0, 0)
        right_box.setSpacing(20)
        right_box.addWidget(reply)
        right_box.addWidget(queue)
        right_box.addWidget(Disclosure("Состояние подключений", states))
        right_box.addWidget(Disclosure("Настройки сессии", inspector))
        right_box.addStretch()
        layout.addWidget(ResponsiveColumns(conversation, right, threshold=850, ratios=(1, 1)))
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
            self.conversation.clear()
            self.last_reply.setText("Пока без реплик. Первый ответ появится здесь после отправки в чат.")
            self.reply_meta.clear()
            self.reply_question.clear()
            self.reply_time.setText("—")
            self.reply_origin.setText("Ждём первый опубликованный ответ")
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
        self.conversation_state.setText("В эфире" if state.process == "running" or state.chat == "ready" else "На паузе")
        self.queue_count.setText(str(state.queue))
        self.queue_summary.setText(f"Вопросов в ожидании: {state.queue}. Награды обрабатываются по порядку."
                                   if state.queue else "Очередь пуста. Следующий вопрос появится после активации награды.")

    def refresh(self):
        if self._loading or self._closed.is_set() or not self.session:
            return
        self._loading = True
        session, output, closed, store = self.session, self._events, self._closed, self.store
        def read():
            try:
                result = store.page(session=session, limit=12, cancel=closed)["rows"]
                latest = store.page(session=session, status="sent", limit=1, cancel=closed)["rows"]
                output.put((session, result, "", latest[0] if latest else None))
            except HistoryReadError as exc:
                output.put((session, [], str(exc)))
        Thread(target=read, daemon=True).start()

    def _drain(self):
        try:
            event = self._events.get_nowait()
            session, rows, error = event[:3]
        except Empty:
            return
        self._loading = False
        if session != self.session or self._closed.is_set():
            return
        names = {"sent": "Отправлено", "preview": "Предпросмотр", "generating": "Готовится", "generated": "Подготовлено", "silent": "Промолчал", "skipped": "Пропущено", "cancelled": "Отменено", "error": "Ошибка", "send_error": "Ошибка отправки", "rejected": "Отклонено"}
        text = error or "\n\n".join(names.get(row["status"], row["status"]) + (" · @" + row["viewer"] if row["viewer"] else "") + "\n" + (row["sent_text"] or row["answer"] or row["reason"] or "Ожидание результата…") for row in rows)
        sent = event[3] if len(event) > 3 else next((row for row in rows if row["status"] == "sent" and row["sent_text"]), None)
        if sent:
            self.last_reply.setText(sent["sent_text"])
            self.reply_meta.setText("В чат Twitch" + (" · @" + sent["viewer"] if sent["viewer"] else ""))
            self.reply_origin.setText((sent.get("viewer") or "Orbit") + (" · вопрос через награду" if sent.get("kind") == "reward" else " · самостоятельная реплика"))
            self.reply_question.setText(sent.get("question", ""))
            if sent.get("created"):
                self.reply_time.setText(datetime.fromtimestamp(sent["created"]).strftime("%H:%M"))
        if text != self._signature:
            bar = self.feed.verticalScrollBar()
            position = bar.value()
            self.feed.setPlainText(text)
            bar.setValue(position)
            self._signature = text
            position = self.conversation.verticalScrollBar().value()
            self.conversation.clear()
            for row in reversed(rows):
                if row.get("question"):
                    item = QListWidgetItem(row.get("viewer") or "Зритель")
                    item.setData(Qt.UserRole + 1, dict(author=row.get("viewer") or "Зритель", body=row["question"]))
                    self.conversation.addItem(item)
                body = row["sent_text"] if row["status"] == "sent" else row["answer"] or row["reason"]
                if body:
                    item = QListWidgetItem(body)
                    author = "Orbit" if row["status"] == "sent" else "Orbit · " + names.get(row["status"], row["status"])
                    item.setData(Qt.UserRole + 1, dict(author=author, body=body, bot=row["status"] == "sent"))
                    self.conversation.addItem(item)
            self.conversation.verticalScrollBar().setValue(position)
            self.empty_conversation.setVisible(not self.conversation.count())
            self.chat_empty.setText(error or "Пока тихо. Здесь появятся вопросы через награду и реплики бота в этом запуске.")
            self.conversation.setVisible(bool(self.conversation.count()))

    def update_participation(self, settings):
        self.participation_title.setText("Участие включено" if settings.enabled else "Участие выключено")
        self.participation_hint.setText(("Только предпросмотр" if settings.mode == "preview" else "Публикация в чат") + f" · до {settings.hourly_limit} реплик в час")

    def shutdown(self):
        self._closed.set()
        self._timer.stop()
        self._poll.stop()
