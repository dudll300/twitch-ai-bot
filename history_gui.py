"""Studio history browser. SQLite reads run in a cancellable daemon worker."""

from datetime import datetime
from queue import Empty, Queue
from threading import Event, Thread

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QHBoxLayout, QLineEdit, QListWidgetItem, QPushButton,
                              QSplitter, QVBoxLayout, QWidget)

from message_history import MessageHistory
from ui_widgets import NoWheelComboBox, ScrollListWidget, ScrollPlainTextEdit, card, label, scroll_page

STATUS_NAMES = {
    "generating": "Запрос начат · результата нет", "generated": "Сгенерировано · отправка не подтверждена",
    "sent": "Отправлено", "preview": "Предпросмотр", "silent": "Решение: молчать",
    "skipped": "Не отправлено", "cancelled": "Отменено", "error": "Ошибка",
    "send_error": "Ошибка отправки · доставка не подтверждена",
}
REASONS = {"answer": "Ответ на вопрос", "reaction": "Реакция", "joke": "Шутка", "question": "Уточнение",
           "no_reason": "Нет повода для участия", "offtopic": "Вне темы канала",
           "already_answered": "На этот повод уже ответили", "insufficient_context": "Недостаточно контекста"}
EVENT_NAMES = {**STATUS_NAMES, "generating": "Запрос начат", "generated": "Сгенерировано",
               "send_error": "Ошибка отправки"}


def timestamp(value, *, short=False):
    try:
        return datetime.fromtimestamp(value).strftime("%H:%M:%S" if short else "%d.%m.%Y %H:%M:%S")
    except (ValueError, TypeError, OverflowError, OSError):
        return "Время неизвестно"


def plain(text="", role="body", wrap=True):
    widget = label(text, role, wrap)
    widget.setTextFormat(Qt.PlainText)
    widget.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return widget


def history_worker(store, tasks, output, closed):
    while not closed.is_set():
        task = tasks.get()
        if task is None:
            return
        kind, token, args, cancel = task
        if cancel.is_set():
            continue
        try:
            result = store.page(**args, cancel=cancel) if kind == "page" else store.detail(**args, cancel=cancel)
            error = ""
        except Exception:
            result, error = None, "Не удалось прочитать историю. Проверьте папку с данными и свободное место."
        if not closed.is_set() and not cancel.is_set():
            output.put((kind, token, result, error))


class HistoryPage(QWidget):
    def __init__(self, root):
        super().__init__()
        self.store = MessageHistory(root)
        self._tasks, self._events = Queue(), Queue()
        self._closed = Event()
        self._tokens = {"page": 0, "detail": 0}
        self._cancel = {"page": Event(), "detail": Event()}
        self._cursors = [None]
        self._rows = []
        self._more = False
        self._loading = False
        self._selected = None
        self._detail_id = None
        self._fingerprint = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(16)
        filters, filter_layout = card()
        controls = QHBoxLayout()
        self.kind = NoWheelComboBox()
        for title, value in (("Все записи", ""), ("Ответы на награды", "reward"), ("Самостоятельные реплики", "autonomous")):
            self.kind.addItem(title, value)
        self.status = NoWheelComboBox()
        for title, value in (("Все статусы", ""), ("Отправлено", "sent"), ("Предпросмотр", "preview"),
                             ("Не отправлено", "not_sent"), ("Молчание", "silent"), ("Ошибки", "errors")):
            self.status.addItem(title, value)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по вопросам, ответам и логинам")
        self.search.setAccessibleName("Поиск в истории сообщений")
        self.refresh_button = QPushButton("Обновить")
        controls.addWidget(self.kind)
        controls.addWidget(self.status)
        controls.addWidget(self.search, 1)
        controls.addWidget(self.refresh_button)
        filter_layout.addLayout(controls)
        self.hint = plain("История сохраняется между запусками. Тестирование и генератор промптов сюда не попадают.", "muted")
        filter_layout.addWidget(self.hint)
        self.error = plain("", "error")
        self.error.hide()
        filter_layout.addWidget(self.error)
        outer.addWidget(filters)

        splitter = QSplitter(Qt.Horizontal)
        left, left_layout = card("Записи")
        left.setMinimumWidth(300)
        self.entries = ScrollListWidget()
        self.entries.setObjectName("historyEntries")
        self.entries.setWordWrap(True)
        self.entries.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.entries.setAccessibleName("Сохранённые ответы и самостоятельные реплики")
        self.entries.setSpacing(4)
        left_layout.addWidget(self.entries, 1)
        pager = QHBoxLayout()
        self.newer = QPushButton("Новее")
        self.older = QPushButton("Старее")
        self.page_label = plain("", "muted")
        pager.addWidget(self.newer)
        pager.addWidget(self.page_label, 1)
        pager.addWidget(self.older)
        left_layout.addLayout(pager)
        splitter.addWidget(left)

        detail_page = QWidget()
        detail_layout = QVBoxLayout(detail_page)
        detail_layout.setContentsMargins(0, 0, 8, 0)
        detail_layout.setSpacing(14)
        details, details_layout = card("Детали сообщения")
        self.badge = plain("Выберите запись", "historyStatus")
        details_layout.addWidget(self.badge)
        self.meta = plain("", "muted")
        details_layout.addWidget(self.meta)
        self.reason = plain("", "caption")
        details_layout.addWidget(self.reason)
        self.question_label = plain("Запрос зрителя", "section")
        self.question = self._editor("Полный запрос зрителя", 100)
        details_layout.addWidget(self.question_label)
        details_layout.addWidget(self.question)
        self.answer_label = plain("Ответ", "section")
        self.answer = self._editor("Сгенерированный или отправленный ответ", 150)
        details_layout.addWidget(self.answer_label)
        details_layout.addWidget(self.answer)
        self.notice_label = plain("Уведомление, переданное Twitch", "caption")
        self.notice = self._editor("Отправленное служебное уведомление", 90)
        details_layout.addWidget(self.notice_label)
        details_layout.addWidget(self.notice)
        self.timeline = plain("", "muted")
        details_layout.addWidget(self.timeline)
        detail_layout.addWidget(details)
        context_card, context_layout = card()
        self.context_button = QPushButton("Показать контекст")
        self.context_button.setCheckable(True)
        self.context_button.toggled.connect(self._toggle_context)
        context_layout.addWidget(self.context_button)
        self.context = self._editor("Разговор, по которому сформирована реплика", 280)
        context_layout.addWidget(self.context)
        self.instructions_button = QPushButton("Показать инструкции запроса")
        self.instructions_button.setCheckable(True)
        self.instructions_button.toggled.connect(self._toggle_instructions)
        context_layout.addWidget(self.instructions_button)
        self.instructions = self._editor("Инструкции, использованные при генерации", 260)
        context_layout.addWidget(self.instructions)
        self.context_card = context_card
        detail_layout.addWidget(context_card)
        detail_layout.addStretch()
        right = scroll_page(detail_page)
        right.setMinimumWidth(340)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setChildrenCollapsible(False)
        outer.addWidget(splitter, 1)

        self.refresh_button.clicked.connect(lambda: self.refresh(reset=True))
        self.newer.clicked.connect(self._newer)
        self.older.clicked.connect(self._older)
        self.entries.currentItemChanged.connect(self._selection)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(lambda: self.refresh(reset=True))
        self.search.textChanged.connect(lambda: self._search_timer.start())
        self.kind.currentIndexChanged.connect(lambda: self.refresh(reset=True))
        self.status.currentIndexChanged.connect(lambda: self.refresh(reset=True))
        self._poll = QTimer(self)
        self._poll.setInterval(100)
        self._poll.timeout.connect(self._drain)
        self._poll.start()
        self._live = QTimer(self)
        self._live.setInterval(2000)
        self._live.timeout.connect(self._live_refresh)
        self._live.start()
        self._clear_detail()
        self.newer.setEnabled(False)
        self.older.setEnabled(False)
        Thread(target=history_worker, args=(self.store, self._tasks, self._events, self._closed),
               daemon=True, name="history-reader").start()

    @staticmethod
    def _editor(name, height):
        editor = ScrollPlainTextEdit()
        editor.setReadOnly(True)
        editor.setAccessibleName(name)
        editor.setMinimumHeight(height)
        editor.setMaximumHeight(height + 60)
        return editor

    def _request(self, category, **args):
        self._cancel[category].set()
        self._cancel[category] = Event()
        self._tokens[category] += 1
        self._tasks.put((category, self._tokens[category], args, self._cancel[category]))

    def refresh(self, *, reset=False):
        if self._closed.is_set():
            return
        if reset:
            self._cursors = [None]
        self._loading = True
        self.hint.setText("Загружаю историю…")
        self.newer.setEnabled(False)
        self.older.setEnabled(False)
        self._request("page", kind=self.kind.currentData(), status=self.status.currentData(),
                      search=self.search.text(), before=self._cursors[-1])

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def _live_refresh(self):
        if not self.isVisible() or self._loading or len(self._cursors) != 1:
            return
        try:
            paths = (self.store.path, self.store.path.with_name(self.store.path.name + "-wal"))
            stamp = tuple((path.stat().st_mtime_ns, path.stat().st_size) if path.exists() else None for path in paths)
        except OSError:
            stamp = None
        if stamp != self._fingerprint:
            self._fingerprint = stamp
            self.refresh()

    def _newer(self):
        if len(self._cursors) > 1:
            self._cursors.pop()
            self.refresh()

    def _older(self):
        if self._more and self._rows:
            self._cursors.append(self._rows[-1]["seq"])
            self.refresh()

    def _drain(self):
        while not self._closed.is_set():
            try:
                kind, token, result, error = self._events.get_nowait()
            except Empty:
                break
            if token != self._tokens[kind]:
                continue
            self.error.setText(error)
            self.error.setVisible(bool(error))
            if kind == "page":
                self._loading = False
                if error:
                    self.hint.setText("История недоступна. Сохранённые файлы не изменены.")
                    continue
                self._rows, self._more = result["rows"], result["more"]
                selected = self._selected
                self.entries.blockSignals(True)
                self.entries.clear()
                select_index = 0
                for index, row in enumerate(self._rows):
                    source = "Награда" if row["kind"] == "reward" else "Самостоятельная реплика"
                    target = " · @" + row["viewer"] if row["viewer"] else ""
                    body = row["answer"] or row["sent_text"] or row["question"] or REASONS.get(row["reason"], row["reason"])
                    body = " ".join(body.split())
                    item = QListWidgetItem(f"{timestamp(row['created'])} · {source}{target}\n"
                                           f"{STATUS_NAMES.get(row['status'], row['status'])}\n{body[:140]}")
                    item.setData(Qt.UserRole, row["id"])
                    self.entries.addItem(item)
                    if row["id"] == selected:
                        select_index = index
                self.entries.blockSignals(False)
                self.newer.setEnabled(len(self._cursors) > 1)
                self.older.setEnabled(self._more)
                self.page_label.setText(f"Страница {len(self._cursors)}")
                self.hint.setText(f"Записей на странице: {len(self._rows)}. Самые новые — сверху." if self._rows else
                                  "Записей пока нет или они не подходят под фильтры. История появится после работы бота.")
                if self._rows:
                    self.entries.setCurrentRow(select_index)
                else:
                    self._selected = None
                    self._cancel["detail"].set()
                    self._clear_detail()
            elif result is not None and result["id"] == self._selected:
                self._render(result)

    def _selection(self, item, previous=None):
        if item is None:
            return
        record_id = item.data(Qt.UserRole)
        if record_id != self._selected:
            self._clear_detail()
        self._selected = record_id
        self._request("detail", record_id=record_id)

    def _clear_detail(self):
        self._detail_id = None
        self.badge.setText("Выберите запись")
        self.meta.clear()
        self.reason.clear()
        self.question.clear()
        self.answer.clear()
        self.timeline.clear()
        self.notice.clear()
        for widget in (self.question, self.question_label, self.notice, self.notice_label, self.context_card):
            widget.hide()
        self.context_button.setChecked(False)
        self.instructions_button.setChecked(False)

    def _render(self, row):
        self._detail_id = row["id"]
        self.badge.setText(STATUS_NAMES.get(row["status"], row["status"]))
        self.badge.setProperty("state", row["status"])
        self.badge.style().unpolish(self.badge)
        self.badge.style().polish(self.badge)
        source = "Ответ на награду" if row["kind"] == "reward" else "Самостоятельная реплика"
        self.meta.setText(" · ".join(part for part in (timestamp(row["created"]), source,
            "#" + row["channel"] if row["channel"] else "", "@" + row["viewer"] if row["viewer"] else "",
            row["model"]) if part))
        self.reason.setText(REASONS.get(row["reason"], row["reason"]))
        has_question = bool(row["question"])
        self.question_label.setVisible(has_question)
        self.question.setVisible(has_question)
        self.question.setPlainText(row["question"])
        self.answer_label.setText("Решение" if row["action"] == "silent" else "Ответ")
        self.answer.setPlainText("Бот решил промолчать." if row["action"] == "silent" else
            (row["sent_text"] if row["status"] == "sent" else row["answer"] or row["sent_text"]) or "Ответ не получен.")
        show_notice = bool(row["sent_text"] and row["answer"] and row["status"] != "sent")
        self.notice_label.setVisible(show_notice)
        self.notice.setVisible(show_notice)
        self.notice.setPlainText(row["sent_text"])
        self.timeline.setText(" → ".join(f"{timestamp(event['time'], short=True)} {EVENT_NAMES.get(event['status'], event['status'])}"
                                         for event in row["events"]))
        context = row["context"]
        self.context_card.setVisible(bool(context) and row["action"] != "silent")
        if context:
            lines = []
            if context.get("intent"):
                lines.append("Цель реплики: " + context["intent"] + "\n")
            basis = set(context.get("basis", ()))
            for message in context.get("conversation", ()):
                mark = " · повод" if message.get("sequence") in basis else ""
                lines.append(f"{timestamp(message.get('time'), short=True)} @{message.get('author', '')}{mark}\n{message.get('text', '')}\n")
            if context.get("recent_bot_replies"):
                lines.append("Предыдущие ответы бота, учтённые в запросе:\n")
                lines.extend(str(message.get("text", "")) + "\n" for message in context["recent_bot_replies"])
            self.context.setPlainText("\n".join(lines))
            instructions = "\n\n".join(message["content"] for message in context.get("request_messages", ())
                                        if message.get("role") == "system")
            self.instructions.setPlainText(instructions)
            self.instructions_button.setVisible(bool(instructions))
        self._toggle_context(self.context_button.isChecked())
        self._toggle_instructions(self.instructions_button.isChecked())

    def _toggle_context(self, checked):
        self.context.setVisible(checked)
        self.context_button.setText("Скрыть контекст" if checked else "Показать контекст")

    def _toggle_instructions(self, checked):
        self.instructions.setVisible(checked)
        self.instructions_button.setText("Скрыть инструкции запроса" if checked else "Показать инструкции запроса")

    def shutdown(self):
        self._closed.set()
        for cancel in self._cancel.values():
            cancel.set()
        self._poll.stop()
        self._live.stop()
        self._search_timer.stop()
        self._tasks.put(None)
