"""Desktop workspace for connection, behavior, viewer profiles and activity."""

import codecs
import html
import json
import sys
from copy import deepcopy

from PySide6.QtCore import QProcess, Qt, QTimer, QUrl, QSize, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QCloseEvent, QDesktopServices, QFont, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QGraphicsOpacityEffect,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

from autonomous_gui import AutonomousPage
from configuration import AI_FALLBACK_MODELS, FIELDS, normalize, read_config
from gui_theme import STYLE
from model_catalog_gui import ModelCatalogControl
from paths import data_dir, resource_path
from profiles import ProfileError, save_profiles
from profiles_gui import ProfilesEditor
from prompt_builder_gui import PromptBuilder
from reply_rules import ANSWER_MAX_CHARS, upgrade_generated_prompt
from settings import load_settings, save_settings
from testing import credentials, make_snapshot, read_test_memory
from testing_gui import TestingPage
from ui_widgets import ScrollPlainTextEdit, SlidingSidebar, card, field, label, menu_icon, russian_question, scroll_page

PAGES = (
    ("Подключение", "Подключите Twitch и выберите сервис для ответов."),
    ("Поведение", "Задайте общий характер, язык и правила общения."),
    ("Зрители", "Личные инструкции для тех, кого бот должен узнавать."),
    ("Самостоятельные реплики", "Ответы и реакции по свежему разговору — с приоритетом наград."),
    ("Активность", "Подключение, вопросы и ответы текущего запуска."),
    ("Тестирование", "Проверьте промпт и сравните модели без подключения Twitch."),
)


def available_screen_size():
    return QApplication.primaryScreen().availableGeometry().size()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Twitch AI Bot")
        self.setWindowIcon(QIcon(str(resource_path("assets/app.ico"))))
        available = available_screen_size()
        self._expanded_minimum = QSize(min(1240, available.width() - 24), min(820, available.height() - 48))
        self._collapsed_minimum = QSize(min(1000, available.width() - 24), self._expanded_minimum.height())
        self.resize(min(1240, available.width() - 24), min(860, available.height() - 48))
        self.setMinimumSize(self._expanded_minimum)
        self._sidebar_expanded = available.width() >= 1200
        self._root = data_dir()
        values, prompt = load_settings(self._root)
        self._has_saved_key = bool(values.get("AI_API_KEY"))
        self._dirty = False
        self._saving = False
        self._log_decoder = codecs.getincrementaldecoder("utf-8")()
        self._log_path = self._root / "bot-session.log"
        self._log_offset = 0
        self._output_tail = ""
        self._auth_url = ""
        self._auth_account = ""
        self._question_count = 0
        self._answer_count = 0

        self.process = QProcess(self)
        self.process.started.connect(self._on_started)
        self.process.finished.connect(self._on_finished)
        self.process.errorOccurred.connect(self._on_process_error)
        self._kill_timer = QTimer(self)
        self._kill_timer.setSingleShot(True)
        self._kill_timer.timeout.connect(self._kill_if_running)
        self._log_timer = QTimer(self)
        self._log_timer.setInterval(250)
        self._log_timer.timeout.connect(self._poll_log)

        body = QWidget()
        body.setObjectName("workspace")
        shell = QHBoxLayout(body)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        self.sidebar = SlidingSidebar(240)
        side = QVBoxLayout(self.sidebar.panel)
        side.setContentsMargins(20, 32, 20, 24)
        side.setSpacing(6)
        side.addWidget(label("Twitch AI", "brand"))
        side.addWidget(label("Панель управления", "muted"))
        side.addSpacing(36)
        self.nav_group = QButtonGroup(self)
        self.nav_buttons = []
        for index, (name, _) in enumerate(PAGES):
            button = QPushButton(name.replace("Самостоятельные реплики", "Самостоятельные\nреплики"))
            button.setProperty("variant", "nav")
            button.setCheckable(True)
            self.nav_group.addButton(button, index)
            self.nav_buttons.append(button)
            side.addWidget(button)
        self.nav_group.idClicked.connect(self._navigate)
        side.addStretch()
        self.status = label("Остановлен")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        side.addSpacing(12)
        folder = QPushButton("Папка с данными")
        folder.setProperty("variant", "quiet")
        folder.clicked.connect(self._open_folder)
        side.addWidget(folder)
        shell.addWidget(self.sidebar)

        main = QVBoxLayout()
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(32, 28, 32, 24)
        content_layout.setSpacing(24)
        self.page_title = label("", "title")
        self.page_description = label("", "muted", True)
        heading = QVBoxLayout()
        heading.setSpacing(6)
        heading.addWidget(self.page_title)
        heading.addWidget(self.page_description)
        header = QHBoxLayout()
        header.setSpacing(16)
        self.menu_button = QPushButton()
        self.menu_button.setIcon(menu_icon())
        self.menu_button.setIconSize(QSize(22, 22))
        self.menu_button.setObjectName("menuToggle")
        self.menu_button.setFixedSize(44, 44)
        self.menu_button.setCheckable(True)
        self.menu_button.clicked.connect(self._toggle_sidebar)
        header.addWidget(self.menu_button, 0, Qt.AlignVCenter)
        header.addLayout(heading, 1)
        self.status.setMaximumWidth(220)
        header.addWidget(self.status, 0, Qt.AlignVCenter)
        content_layout.addLayout(header)
        self.notice = label("", "error", True)
        self.notice.hide()
        content_layout.addWidget(self.notice)
        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_connection(values))
        self.pages.addWidget(self._build_behavior(prompt))
        self.profiles_editor = ProfilesEditor(self._root / "profiles.json")
        self.profiles_editor.test_requested.connect(self._test_profile)
        self.pages.addWidget(self.profiles_editor)
        self.autonomous_page = AutonomousPage(self._root)
        self.pages.addWidget(self.autonomous_page)
        self.pages.addWidget(self._build_activity())
        self.testing_page = TestingPage(self._test_credentials, self._test_snapshot,
                                        lambda: self.profiles_editor.rows)
        self.pages.addWidget(self.testing_page)
        self._page_effect = QGraphicsOpacityEffect(self.pages)
        self.pages.setGraphicsEffect(self._page_effect)
        self._page_effect.setOpacity(1.0)
        self._page_animation = QPropertyAnimation(self._page_effect, b"opacity", self)
        self._page_animation.setDuration(150)
        self._page_animation.setEasingCurve(QEasingCurve.OutCubic)
        content_layout.addWidget(self.pages, 1)
        main.addWidget(content, 1)

        footer = QFrame()
        footer.setObjectName("footer")
        actions = QHBoxLayout(footer)
        actions.setContentsMargins(32, 18, 32, 18)
        actions.setSpacing(12)
        self.save_hint = label("Все изменения сохранены", "muted", True)
        actions.addWidget(self.save_hint, 1)
        self.save_button = QPushButton("Сохранить")
        self.save_button.setToolTip("Сохранить настройки и профили · Ctrl+S")
        self.save_button.clicked.connect(lambda: self._save())
        actions.addWidget(self.save_button)
        self.start_button = QPushButton("Запустить бота")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self._start)
        actions.addWidget(self.start_button)
        self.stop_button = QPushButton("Остановить бота")
        self.stop_button.clicked.connect(self._stop)
        self.stop_button.hide()
        actions.addWidget(self.stop_button)
        main.addWidget(footer)
        shell.addLayout(main, 1)
        self.setCentralWidget(body)
        self._set_sidebar(self._sidebar_expanded, animate=False)
        self._navigate(0)
        for widget in (self.channel, self.bot_name, self.client_id, self.reward_title,
                       self.base_url, self.api_key, self.fallback_models):
            widget.textChanged.connect(self._mark_dirty)
        self.model.currentTextChanged.connect(self._mark_dirty)
        self.prompt.textChanged.connect(self._mark_dirty)
        self.prompt.textChanged.connect(self._update_prompt_count)
        self.profiles_editor.changed.connect(self._mark_dirty)
        self.profiles_editor.changed.connect(self.testing_page.refresh_profiles)
        self.api_key.textChanged.connect(self.testing_page.invalidate_catalog)
        self.base_url.textChanged.connect(self.testing_page.invalidate_catalog)
        self.api_key.textChanged.connect(self.model_catalog.invalidate)
        self.base_url.textChanged.connect(self.model_catalog.invalidate)
        self.api_key.textChanged.connect(self.prompt_builder.model_catalog.invalidate)
        self.base_url.textChanged.connect(self.prompt_builder.model_catalog.invalidate)
        self.prompt_builder.test_requested.connect(self._test_prompt_draft)
        self.prompt_builder.connection_requested.connect(lambda: self._navigate(0))
        QShortcut(QKeySequence.Save, self, activated=lambda: self._save())
        self._update_prompt_count()
        if upgrade_generated_prompt(prompt) != prompt:
            self._mark_dirty()
            self.save_hint.setText(f"Служебный лимит обновлён до {ANSWER_MAX_CHARS} символов — нажмите «Сохранить»")

    def _toggle_sidebar(self, expanded):
        self._set_sidebar(expanded)

    def _set_sidebar(self, expanded, animate=True):
        self._sidebar_expanded = expanded
        self.menu_button.setChecked(expanded)
        caption = "Скрыть меню" if expanded else "Показать меню"
        self.menu_button.setToolTip(caption)
        self.menu_button.setAccessibleName(caption)
        self.setMinimumSize(self._expanded_minimum if expanded else self._collapsed_minimum)
        self.sidebar.set_expanded(expanded, animate)

    def _navigate(self, index):
        if index == 5:
            self.testing_page.refresh_profiles()
        changed = self.pages.currentIndex() != index
        self.pages.setCurrentIndex(index)
        if changed:
            self._page_animation.stop()
            self._page_animation.setStartValue(0.65)
            self._page_animation.setEndValue(1.0)
            self._page_animation.start()
        self.nav_buttons[index].setChecked(True)
        self.page_title.setText(PAGES[index][0])
        self.page_description.setText(PAGES[index][1])

    def _test_credentials(self):
        saved_key = read_config(self._root / ".env").get("AI_API_KEY", "") if not self.api_key.text().strip() else ""
        return credentials(self.base_url.text(), self.api_key.text(), saved_key)

    def _test_snapshot(self, models, question, sender, login, profile_index, *, prompt_override=None):
        # Only the key may come from saved settings. All drafts come from widgets.
        auth = self._test_credentials()
        if self.profiles_editor.load_error:
            raise ValueError(self.profiles_editor.load_error)
        profile = None
        if sender == "profile":
            if profile_index is not None and 0 <= profile_index < len(self.profiles_editor.rows):
                profile = deepcopy(self.profiles_editor.rows[profile_index])
        prompt = self.prompt.toPlainText() if prompt_override is None else prompt_override
        return make_snapshot(auth, models, question, prompt, sender,
                             login, profile, read_test_memory(self._root),
                             profiles=deepcopy(self.profiles_editor.rows))

    def _test_prompt(self):
        self.testing_page.open_for_prompt()
        self._navigate(5)

    def _test_prompt_draft(self, prompt):
        self.testing_page.open_for_prompt(prompt)
        self._navigate(5)

    def _test_profile(self, index):
        self.testing_page.open_for_profile(index)
        self._navigate(5)

    def _open_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._root)))

    def _build_connection(self, values):
        page = QWidget()
        grid = QGridLayout(page)
        grid.setContentsMargins(0, 0, 8, 0)
        grid.setHorizontalSpacing(20)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        twitch, twitch_layout = card("Twitch", "Канал, аккаунт бота и награда")
        self.channel = QLineEdit(values.get("TWITCH_CHANNEL", ""))
        self.channel.setPlaceholderText("@channel или ссылка")
        self.bot_name = QLineEdit(values.get("TWITCH_BOT_NAME", ""))
        self.bot_name.setPlaceholderText("Логин аккаунта бота")
        self.client_id = QLineEdit(values.get("TWITCH_CLIENT_ID", ""))
        self.client_id.setPlaceholderText("Client ID приложения Public")
        self.reward_title = QLineEdit(values.get("TWITCH_REWARD_TITLE", "Вопрос ИИ"))
        for caption, widget in (("Канал", self.channel), ("Аккаунт бота", self.bot_name),
                                ("Client ID", self.client_id), ("Название награды", self.reward_title)):
            twitch_layout.addWidget(field(caption, widget))
        twitch_layout.addWidget(label("В награде Twitch включите обязательный ввод текста.", "muted", True))
        twitch_layout.addStretch()
        docs = QLabel('<a style="color: #c5c9d2;" href="https://dev.twitch.tv/console/apps">Создать приложение в Twitch ↗</a>')
        docs.setOpenExternalLinks(True)
        docs.setWordWrap(True)
        twitch_layout.addWidget(docs)
        grid.addWidget(twitch, 0, 0)
        ai, ai_layout = card("Нейросеть", "API, совместимый с Chat Completions")
        self.base_url = QLineEdit(values.get("AI_BASE_URL", "https://ai.starimg.ru/v1"))
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.Password)
        self.api_key.setPlaceholderText("Ключ сохранён" if self._has_saved_key else "Ключ вашего AI-сервиса")
        self.model_catalog = ModelCatalogControl(values.get("AI_MODEL", AI_FALLBACK_MODELS[0]), self._test_credentials)
        self.model = self.model_catalog.combo
        self.fallback_models = QLineEdit(values.get("AI_FALLBACK_MODELS", ""))
        self.fallback_models.setPlaceholderText("model-a, model-b")
        for caption, widget in (("Base URL", self.base_url), ("API-ключ", self.api_key),
                                ("Основная модель", self.model_catalog), ("Запасные модели · необязательно", self.fallback_models)):
            ai_layout.addWidget(field(caption, widget))
        ai_layout.addWidget(label("Запасные модели вызываются по порядку при сбоях основной.", "muted", True))
        ai_layout.addStretch()
        ai_layout.addWidget(label("Ключ хранится только на этом компьютере.", "muted", True))
        grid.addWidget(ai, 0, 1)
        grid.setRowStretch(1, 1)
        return scroll_page(page)

    def _build_behavior(self, prompt):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(20)
        main, main_layout = card("Общий промпт", f"Эти инструкции действуют для всех зрителей. Обычные ответы — до {ANSWER_MAX_CHARS} символов. Можно оставить пустым.")
        self.prompt = ScrollPlainTextEdit(upgrade_generated_prompt(prompt))
        self.prompt.setAccessibleName("Общий системный промпт")
        self.prompt.setPlaceholderText(f"Опишите характер бота, язык и стиль ответов.\n\nНапример: отвечай по-русски, дружелюбно и кратко. Укладывайся в {ANSWER_MAX_CHARS} символов. Не используй Markdown.")
        self.prompt.setMinimumHeight(240)
        main_layout.addWidget(self.prompt, 1)
        row = QHBoxLayout()
        self.prompt_count = label("", "muted")
        row.addWidget(self.prompt_count)
        row.addStretch()
        self.test_prompt_button = QPushButton("Проверить ответ")
        self.test_prompt_button.clicked.connect(self._test_prompt)
        row.addWidget(self.test_prompt_button)
        self.reset_prompt = QPushButton("Очистить")
        self.reset_prompt.setProperty("variant", "quiet")
        self.reset_prompt.clicked.connect(self.prompt.clear)
        row.addWidget(self.reset_prompt)
        main_layout.addLayout(row)
        layout.addWidget(main, 1)
        self.prompt_builder = PromptBuilder(self._test_credentials, self.prompt.toPlainText,
                                            self.prompt.setPlainText)
        layout.addWidget(self.prompt_builder)
        return scroll_page(page)

    def _build_activity(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)
        metrics = QHBoxLayout()
        metrics.setSpacing(20)
        for title, attribute, value in (("Вопросов принято", "questions_label", "0"),
                                        ("Ответов отправлено", "answers_label", "0")):
            frame, frame_layout = card()
            frame_layout.setSpacing(8)
            frame_layout.addWidget(label(title, "muted"))
            number = label(value, "number")
            setattr(self, attribute, number)
            frame_layout.addWidget(number)
            metrics.addWidget(frame, 1)
        layout.addLayout(metrics)
        self.auth_hint = QLabel()
        self.auth_hint.setWordWrap(True)
        self.auth_hint.setOpenExternalLinks(True)
        self.auth_hint.setStyleSheet("background: #2a2d33; border-radius: 8px; padding: 16px;")
        self.auth_hint.hide()
        layout.addWidget(self.auth_hint)
        journal, journal_layout = card("Журнал работы")
        self.log = ScrollPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(240)
        self.log.setAccessibleName("Журнал работы бота")
        self.log.setPlaceholderText("Здесь появятся события подключения и ответы.\nЗапустите бота, когда настройки будут готовы.")
        self.log.setFont(QFont("Consolas", 10))
        self.log.document().setMaximumBlockCount(2000)
        journal_layout.addWidget(self.log, 1)
        layout.addWidget(journal, 1)
        return scroll_page(page)

    def _update_prompt_count(self):
        self.prompt_count.setText(f"{len(self.prompt.toPlainText()):,} / 20 000 символов".replace(",", " "))

    def _mark_dirty(self, *_):
        if self._saving:
            return
        self._dirty = True
        self.save_hint.setText("Есть несохранённые изменения")
        self.notice.hide()

    def _save(self, *, for_start=False):
        if self.process.state() != QProcess.NotRunning:
            return False
        self.notice.hide()
        self._saving = True
        try:
            try:
                rows = self.profiles_editor.validated()
            except ProfileError:
                self._navigate(2)
                raise
            fields = self._fields()
            previous = read_config(self._root / ".env")
            for name, _ in FIELDS:
                value = fields[name]
                if name == "AI_API_KEY" and not value.strip():
                    value = previous.get(name, "")
                if for_start or value.strip():
                    try:
                        normalize(name, value)
                    except ValueError:
                        self._navigate(0)
                        self._field_widgets()[name].setFocus()
                        raise
            if len(self.prompt.toPlainText().strip()) > 20000:
                self._navigate(1)
                raise ValueError("Общий промпт должен содержать не более 20 000 символов.")
            prompt = upgrade_generated_prompt(self.prompt.toPlainText())
            save_settings(fields, prompt, self._root, allow_incomplete=not for_start)
            save_profiles(self._root / "profiles.json", rows)
            if prompt != self.prompt.toPlainText():
                self.prompt.setPlainText(prompt)
            self.profiles_editor.saved(rows)
            self._has_saved_key = bool(read_config(self._root / ".env").get("AI_API_KEY"))
            self.api_key.clear()
            self.api_key.setPlaceholderText("Ключ сохранён" if self._has_saved_key else "Ключ вашего AI-сервиса")
            self._dirty = False
            self.save_hint.setText("Все изменения сохранены")
            return True
        except (OSError, ValueError) as exc:
            self.notice.setText(str(exc))
            self.notice.show()
            return False
        finally:
            self._saving = False

    def _field_widgets(self):
        return {"TWITCH_CHANNEL": self.channel, "TWITCH_BOT_NAME": self.bot_name,
                "TWITCH_CLIENT_ID": self.client_id, "TWITCH_REWARD_TITLE": self.reward_title,
                "AI_BASE_URL": self.base_url, "AI_API_KEY": self.api_key,
                "AI_MODEL": self.model, "AI_FALLBACK_MODELS": self.fallback_models}

    def _set_running(self, running):
        self.autonomous_page.set_running(running)
        self.prompt_builder.set_editable(not running)
        self.model_catalog.set_editable(not running)
        for widget in (*self._field_widgets().values(), self.prompt, self.reset_prompt, self.save_button):
            widget.setEnabled(not running)
        self.profiles_editor.set_editable(not running)
        self.start_button.setVisible(not running)
        self.start_button.setEnabled(not running)
        self.stop_button.setVisible(running)
        self.stop_button.setEnabled(running)
        self.save_hint.setText("Самостоятельные реплики можно менять во время работы" if running else
                               "Есть несохранённые изменения" if self._dirty else "Все изменения сохранены")

    def _fields(self) -> dict[str, str]:
        return {
            "TWITCH_CHANNEL": self.channel.text(),
            "TWITCH_BOT_NAME": self.bot_name.text(),
            "TWITCH_CLIENT_ID": self.client_id.text(),
            "AI_BASE_URL": self.base_url.text(),
            "AI_API_KEY": self.api_key.text(),
            "AI_MODEL": self.model.currentText(),
            "AI_FALLBACK_MODELS": self.fallback_models.text(),
            "TWITCH_REWARD_TITLE": self.reward_title.text(),
        }

    def _start(self) -> None:
        if self.process.state() != QProcess.NotRunning or not self._save(for_start=True):
            return
        try:
            self._log_path.write_text("", encoding="utf-8")
        except OSError as exc:
            self.notice.setText(f"Не удалось создать журнал: {exc}")
            self.notice.show()
            return
        self._log_decoder = codecs.getincrementaldecoder("utf-8")()
        self._log_offset = 0
        self._output_tail = ""
        self._auth_url = ""
        self._auth_account = ""
        self.auth_hint.setVisible(False)
        self._navigate(4)
        self.autonomous_page.log.clear()
        self._question_count = self._answer_count = 0
        self.questions_label.setText("0")
        self.answers_label.setText("0")
        self.log.clear()
        self.log.appendPlainText("Запускаю бота…")
        self.status.setText("Запускается…")
        self._set_running(True)
        self.process.setWorkingDirectory(str(self._root))
        self._log_timer.start()
        if getattr(sys, "frozen", False):
            self.process.start(sys.executable, ["--bot", str(self._log_path)])
        else:
            self.process.start(sys.executable, ["-u", str(resource_path("app.py")), "--bot", str(self._log_path)])

    def _stop(self) -> None:
        if self.process.state() == QProcess.NotRunning:
            return
        self.status.setText("Останавливается…")
        self.stop_button.setEnabled(False)
        self.process.terminate()
        self._kill_timer.start(3000)

    def _kill_if_running(self) -> None:
        if self.process.state() != QProcess.NotRunning:
            self.process.kill()

    def _on_started(self) -> None:
        self.status.setText("Подключается…")

    def _on_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        self._kill_timer.stop()
        self._poll_log()
        self._log_timer.stop()
        if self._output_tail:
            self._append_log(self._output_tail)
            self._output_tail = ""
        self.log.appendPlainText(f"Бот остановлен (код {exit_code}).")
        self.status.setText("Остановлен")
        self._set_running(False)

    def _on_process_error(self, error: QProcess.ProcessError) -> None:
        self._poll_log()
        self.log.appendPlainText(f"Ошибка запуска бота: {self.process.errorString()}")
        if error == QProcess.FailedToStart:
            self._log_timer.stop()
            self.status.setText("Не удалось запустить")
            self._set_running(False)

    def _poll_log(self) -> None:
        try:
            with self._log_path.open("rb") as stream:
                stream.seek(self._log_offset)
                chunk = stream.read()
                self._log_offset = stream.tell()
        except OSError:
            return
        if chunk:
            self._consume(self._log_decoder.decode(chunk))

    def _consume(self, text: str) -> None:
        self._output_tail += text
        while "\n" in self._output_tail:
            line, self._output_tail = self._output_tail.split("\n", 1)
            self._append_log(line.rstrip("\r"))

    def _append_log(self, line: str) -> None:
        if line.startswith("AUTO_EVENT "):
            try:
                event = json.loads(line.removeprefix("AUTO_EVENT "))
                if isinstance(event, dict):
                    self.autonomous_page.push_event(event)
            except ValueError:
                pass
            return
        self.log.appendPlainText(line)
        if line.startswith("Вопрос от ") and "принят" in line:
            self._question_count += 1
            self.questions_label.setText(str(self._question_count))
        elif line.startswith("Ответ отправлен для "):
            self._answer_count += 1
            self.answers_label.setText(str(self._answer_count))
        elif line.startswith("Жду вопросов по награде "):
            self.status.setText("Подключён к Twitch")
            self.auth_hint.hide()
        if line.startswith("Откройте ссылку и войдите в Twitch под аккаунтом "):
            self.status.setText("Ожидает входа в Twitch")
            self._auth_account = line.removeprefix("Откройте ссылку и войдите в Twitch под аккаунтом ").rstrip(":")
        elif line.startswith("https://") and "twitch.tv/activate" in line:
            self._auth_url = line
        elif line.startswith("Код: ") and self._auth_url:
            url = html.escape(self._auth_url, quote=True)
            code = html.escape(line[5:].strip())
            account = html.escape(self._auth_account or "нужным аккаунтом")
            self.auth_hint.setText(f'Подтвердите вход под аккаунтом {account}: <a style="color: #d0d0d0;" href="{url}">открыть Twitch</a>. Код: <b>{code}</b>')
            self.auth_hint.setVisible(True)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._dirty:
            choice = russian_question(self, "Несохранённые изменения",
                "Сохранить настройки и профили перед закрытием?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
            if choice == QMessageBox.Cancel or (choice == QMessageBox.Save and not self._save()):
                event.ignore()
                return
        if self.process.state() != QProcess.NotRunning:
            choice = russian_question(self, "Бот работает", "Остановить бота и закрыть приложение?",
                                          QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if choice != QMessageBox.Yes:
                event.ignore()
                return
        self._log_timer.stop()
        if self.process.state() != QProcess.NotRunning:
            self.process.kill()
            self.process.waitForFinished(1000)
        self.testing_page.shutdown()
        self.model_catalog.shutdown()
        self.prompt_builder.shutdown()
        super().closeEvent(event)


def run_gui() -> int:
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(str(resource_path("assets/app.ico"))))
    app.setFont(QFont("Segoe UI", 10))
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    window = MainWindow()
    window.show()
    return app.exec()
