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
from history_gui import HistoryPage
from local_context_gui import LocalContextPage
from model_catalog_gui import ModelCatalogControl
from paths import data_dir, resource_path
from profiles import ProfileError, save_profiles
from profiles_gui import ProfilesEditor
from prompt_builder_gui import PromptBuilder
from reply_rules import ANSWER_MAX_CHARS, upgrade_generated_prompt
from settings import load_settings, save_settings
from studio_icons import studio_icon
from testing import credentials, make_snapshot, read_test_memory
from testing_gui import TestingPage
from safety_gui import SafetyPage
from safety_settings import policy_scope
from ui_widgets import ScrollPlainTextEdit, SlidingSidebar, card, field, label, menu_icon, russian_question, scroll_page

PAGES = (
    ("Подключение", "Подключите Twitch и выберите сервис для ответов."),
    ("Поведение", "Задайте общий характер, язык и правила общения."),
    ("Зрители", "Личные инструкции для тех, кого бот должен узнавать."),
    ("Самостоятельные реплики", "Ответы и реакции по свежему разговору — с приоритетом наград."),
    ("Безопасность", "Проверки, политика публикации и понятные причины решений."),
    ("Активность", "Подключение, вопросы и ответы текущего запуска."),
    ("Тестирование", "Проверьте промпт и сравните модели без подключения Twitch."),
    ("Локальный контекст", "Пояснения выражений и уместные отсылки вашего канала."),
    ("История", "Сохранённые ответы, самостоятельные реплики и решения бота за все запуски."),
)
PAGE_CONNECTION, PAGE_BEHAVIOR, PAGE_VIEWERS, PAGE_AUTONOMOUS, PAGE_SAFETY, PAGE_ACTIVITY, PAGE_TESTING, PAGE_LOCAL_CONTEXT, PAGE_HISTORY = range(len(PAGES))


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
        side.setContentsMargins(16, 24, 16, 20)
        side.setSpacing(6)
        brand = QHBoxLayout()
        brand.setSpacing(10)
        mark = QFrame()
        mark.setObjectName("brandMark")
        mark.setFixedSize(38, 38)
        mark_layout = QVBoxLayout(mark)
        mark_layout.setContentsMargins(1, 1, 1, 1)
        glyph = QLabel()
        glyph.setFixedSize(36, 36)
        glyph.setAlignment(Qt.AlignCenter)
        glyph.setPixmap(QIcon(str(resource_path("assets/app.ico"))).pixmap(36, 36))
        glyph.setAccessibleName("Twitch AI Bot")
        mark_layout.addWidget(glyph)
        brand.addWidget(mark)
        brand_name = QVBoxLayout()
        brand_name.setSpacing(0)
        brand_name.addWidget(label("Twitch AI", "brand"))
        brand_name.addWidget(label("Студия бота", "muted"))
        brand.addLayout(brand_name, 1)
        side.addLayout(brand)
        side.addSpacing(28)
        side.addWidget(label("РАБОЧЕЕ ПРОСТРАНСТВО", "eyebrow"))
        side.addSpacing(6)
        self.nav_group = QButtonGroup(self)
        self.nav_buttons = []
        nav_icons = ("plug", "sliders", "users", "messages", "sliders", "activity", "flask", "book", "history")
        for index, (name, _) in enumerate(PAGES):
            button = QPushButton(name.replace("Самостоятельные реплики", "Самостоятельные\nреплики"))
            button.setProperty("variant", "nav")
            button.setIcon(studio_icon(nav_icons[index]))
            button.setIconSize(QSize(18, 18))
            button.setCheckable(True)
            self.nav_group.addButton(button, index)
            self.nav_buttons.append(button)
            side.addWidget(button)
        self.nav_group.idClicked.connect(self._navigate)
        side.addStretch()
        self.status = label("Остановлен")
        self.status.setObjectName("status")
        self.status.setProperty("state", "idle")
        self.status.setWordWrap(True)
        profile = QFrame()
        profile.setObjectName("sidebarProfile")
        profile_layout = QVBoxLayout(profile)
        profile_layout.setContentsMargins(14, 14, 14, 14)
        profile_layout.setSpacing(5)
        profile_layout.addWidget(label("ВАШ КАНАЛ", "eyebrow"))
        self.sidebar_channel = label("Канал не выбран", wrap=True)
        self.sidebar_channel.setObjectName("sidebarChannel")
        profile_layout.addWidget(self.sidebar_channel)
        self.sidebar_hint = label("Настройте подключение", "muted", True)
        self.sidebar_hint.setObjectName("sidebarHint")
        profile_layout.addWidget(self.sidebar_hint)
        side.addWidget(profile)
        side.addSpacing(12)
        folder = QPushButton("Папка с данными")
        folder.setProperty("variant", "quiet")
        folder.setIcon(studio_icon("folder", size=16))
        folder.clicked.connect(self._open_folder)
        side.addWidget(folder)
        shell.addWidget(self.sidebar)

        main_area = QWidget()
        main_area.setObjectName("mainArea")
        main = QVBoxLayout(main_area)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        topbar = QFrame()
        topbar.setObjectName("topbar")
        header = QHBoxLayout(topbar)
        header.setContentsMargins(28, 18, 28, 18)
        header.setSpacing(14)
        self.menu_button = QPushButton()
        self.menu_button.setIcon(menu_icon())
        self.menu_button.setIconSize(QSize(22, 22))
        self.menu_button.setObjectName("menuToggle")
        self.menu_button.setFixedSize(40, 40)
        self.menu_button.setCheckable(True)
        self.menu_button.clicked.connect(self._toggle_sidebar)
        header.addWidget(self.menu_button)
        workspace_heading = QVBoxLayout()
        workspace_heading.setSpacing(2)
        self.workspace_channel = label("Ваш канал", "channel", True)
        workspace_heading.addWidget(self.workspace_channel)
        workspace_heading.addWidget(label("TWITCH AI / СТУДИЯ", "eyebrow"))
        header.addLayout(workspace_heading, 1)
        self.status.setMaximumWidth(190)
        header.addWidget(self.status, 0, Qt.AlignVCenter)
        self.start_button = QPushButton("Запустить бота")
        self.start_button.setObjectName("primary")
        self.start_button.setIcon(studio_icon("play", "#FFFFFF", 16))
        self.start_button.clicked.connect(self._start)
        header.addWidget(self.start_button)
        self.stop_button = QPushButton("Остановить бота")
        self.stop_button.setProperty("variant", "danger")
        self.stop_button.setIcon(studio_icon("stop", "#FF9B9B", 16))
        self.stop_button.clicked.connect(self._stop)
        self.stop_button.hide()
        header.addWidget(self.stop_button)
        main.addWidget(topbar)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(28, 24, 28, 24)
        content_layout.setSpacing(22)
        self.page_title = label("", "title")
        self.page_description = label("", "muted", True)
        heading = QVBoxLayout()
        heading.setSpacing(6)
        heading.addWidget(self.page_title)
        heading.addWidget(self.page_description)
        content_layout.addLayout(heading)
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
        self.safety_page = SafetyPage(self._root, self._test_credentials, lambda: self.model.currentText())
        self.pages.addWidget(self.safety_page)
        self.pages.addWidget(self._build_activity())
        self.testing_page = TestingPage(self._test_credentials, self._test_snapshot,
                                        lambda: self.profiles_editor.rows, safety_store=self.safety_page.store,
                                        get_answer_model=lambda: self.model.currentText())
        self.pages.addWidget(self.testing_page)
        self.local_context_page = LocalContextPage(self._root)
        self.pages.addWidget(self.local_context_page)
        self.history_page = HistoryPage(self._root)
        self.pages.addWidget(self.history_page)
        self.safety_page.test_requested.connect(self._test_security)
        self.safety_page.history_requested.connect(self._security_history)
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
        actions.setContentsMargins(28, 14, 28, 14)
        actions.setSpacing(12)
        self.save_hint = label("Все изменения сохранены", "muted", True)
        actions.addWidget(self.save_hint, 1)
        self.save_button = QPushButton("Сохранить")
        self.save_button.setIcon(studio_icon("save", "#F3F5FA", 16))
        self.save_button.setToolTip("Сохранить настройки и профили · Ctrl+S")
        self.save_button.clicked.connect(lambda: self._save())
        actions.addWidget(self.save_button)
        main.addWidget(footer)
        shell.addWidget(main_area, 1)
        self.setCentralWidget(body)
        self._set_sidebar(self._sidebar_expanded, animate=False)
        self._navigate(PAGE_CONNECTION)
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
        self.prompt_builder.connection_requested.connect(lambda: self._navigate(PAGE_CONNECTION))
        self.local_context_page.changed.connect(self._local_context_changed)
        self.safety_page.changed.connect(self._local_context_changed)
        self.api_key.textChanged.connect(self.safety_page.model_catalog.invalidate)
        self.base_url.textChanged.connect(self.safety_page.model_catalog.invalidate)
        self.model.currentTextChanged.connect(self.safety_page._changed)
        self.autonomous_page.enabled.toggled.connect(self._refresh_workspace)
        self.autonomous_page.mode.currentIndexChanged.connect(self._refresh_workspace)
        self.autonomous_page.apply_button.clicked.connect(self._refresh_workspace)
        self.autonomous_page.reset_button.clicked.connect(self._refresh_workspace)
        QShortcut(QKeySequence.Save, self, activated=lambda: self._save())
        self._update_prompt_count()
        self._refresh_workspace()
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
        if index == PAGE_TESTING:
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
        self._refresh_workspace()

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
        with policy_scope(self.safety_page.store):
            return make_snapshot(auth, models, question, prompt, sender,
                                 login, profile, read_test_memory(self._root),
                                 profiles=deepcopy(self.profiles_editor.rows))

    def _test_security(self):
        self.testing_page.open_diagnostic()
        self._navigate(PAGE_TESTING)

    def _security_history(self, scenario):
        kind = 'reward' if scenario == 'reward' else 'autonomous' if scenario in ('autonomous', 'preview') else ''
        self.history_page.kind.setCurrentIndex(self.history_page.kind.findData(kind))
        self.history_page.status.setCurrentIndex(self.history_page.status.findData('preview' if scenario == 'preview' else ''))
        self.history_page.search.clear()
        self.history_page.refresh(reset=True)
        self._navigate(PAGE_HISTORY)

    def _test_prompt(self):
        self.testing_page.open_for_prompt()
        self._navigate(PAGE_TESTING)

    def _test_prompt_draft(self, prompt):
        self.testing_page.open_for_prompt(prompt)
        self._navigate(PAGE_TESTING)

    def _test_profile(self, index):
        self.testing_page.open_for_profile(index)
        self._navigate(PAGE_TESTING)

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
        docs = QLabel('<a style="color: #65B3FF;" href="https://dev.twitch.tv/console/apps">Создать приложение в Twitch ↗</a>')
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
        columns = QHBoxLayout(page)
        columns.setContentsMargins(0, 0, 8, 0)
        columns.setSpacing(20)
        layout = QVBoxLayout()
        layout.setSpacing(16)
        stats = QFrame()
        stats.setObjectName("activityStats")
        metrics = QHBoxLayout(stats)
        metrics.setContentsMargins(20, 16, 20, 16)
        metrics.setSpacing(24)
        for title, attribute, value in (("Вопросов принято", "questions_label", "0"),
                                        ("Ответов отправлено", "answers_label", "0")):
            frame = QFrame()
            frame.setProperty("role", "stat")
            frame_layout = QVBoxLayout(frame)
            frame_layout.setContentsMargins(0, 0, 0, 0)
            frame_layout.setSpacing(4)
            number = label(value, "statValue")
            setattr(self, attribute, number)
            frame_layout.addWidget(number)
            frame_layout.addWidget(label(title, "statLabel"))
            metrics.addWidget(frame, 1)
        layout.addWidget(stats)
        self.auth_hint = QLabel()
        self.auth_hint.setObjectName("authHint")
        self.auth_hint.setWordWrap(True)
        self.auth_hint.setOpenExternalLinks(True)
        self.auth_hint.hide()
        layout.addWidget(self.auth_hint)
        journal, journal_layout = card("Журнал работы", "События текущего запуска · Twitch и AI")
        self.log = ScrollPlainTextEdit()
        self.log.setObjectName("activityLog")
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(260)
        self.log.setAccessibleName("Журнал работы бота")
        self.log.setPlaceholderText("Пока здесь тихо.\n\nЗапустите бота — в журнале появятся события подключения,\nпринятые вопросы и отправленные ответы.")
        self.log.setFont(QFont("Consolas", 10))
        self.log.document().setMaximumBlockCount(2000)
        journal_layout.addWidget(self.log, 1)
        layout.addWidget(journal, 1)
        columns.addLayout(layout, 1)

        inspector = QWidget()
        inspector.setFixedWidth(260)
        inspector_layout = QVBoxLayout(inspector)
        inspector_layout.setContentsMargins(0, 0, 0, 0)
        inspector_layout.setSpacing(16)
        session, session_layout = card("Текущие настройки")
        session.setObjectName("inspectorCard")
        session_layout.addWidget(label("ПОДКЛЮЧЕНИЕ", "eyebrow"))
        self.connection_pill = label("Остановлен", wrap=True)
        self.connection_pill.setObjectName("connectionPill")
        session_layout.addWidget(self.connection_pill)
        self.inspector_values = {}
        for key, caption in (("channel", "Канал Twitch"), ("model", "Основная модель"),
                             ("reward", "Награда за вопрос"), ("autonomous", "Самостоятельные реплики"),
                             ("context", "Локальный контекст")):
            group = QVBoxLayout()
            group.setSpacing(4)
            group.addWidget(label(caption, "muted"))
            value = label("—", "body", True)
            self.inspector_values[key] = value
            group.addWidget(value)
            session_layout.addLayout(group)
        inspector_layout.addWidget(session)
        shortcuts, shortcuts_layout = card("Быстрые действия")
        shortcuts.setObjectName("inspectorCard")
        shortcuts_layout.setContentsMargins(20, 18, 20, 18)
        shortcuts_layout.setSpacing(10)
        for caption, index, icon in (("Поведение бота", PAGE_BEHAVIOR, "sliders"), ("Проверить ответ", PAGE_TESTING, "flask")):
            button = QPushButton(caption)
            button.setProperty("variant", "inspector")
            button.setIcon(studio_icon(icon, size=16))
            button.clicked.connect(lambda checked=False, target=index: self._navigate(target))
            shortcuts_layout.addWidget(button)
        inspector_layout.addWidget(shortcuts)
        inspector_layout.addStretch()
        columns.addWidget(inspector)
        return scroll_page(page)

    def _refresh_workspace(self, *_):
        """Display drafts and saved live settings without changing their persistence."""
        channel = self.channel.text().strip()
        try:
            channel = normalize("TWITCH_CHANNEL", channel) if channel else ""
        except ValueError:
            channel = ""
        channel_caption = f"@{channel}" if channel else "Канал не выбран"
        self.workspace_channel.setText(channel_caption)
        self.sidebar_channel.setText(channel_caption)
        bot = self.bot_name.text().strip()
        self.sidebar_hint.setText(f"Бот: {bot}" if bot else "Настройте подключение")
        self.inspector_values["channel"].setText(channel_caption)
        self.inspector_values["model"].setText(self.model.currentText().strip() or "Не выбрана")
        self.inspector_values["reward"].setText(self.reward_title.text().strip() or "Не указана")
        auto = self.autonomous_page.saved
        self.inspector_values["autonomous"].setText(
            ("Предпросмотр" if auto.mode == "preview" else "Публикация в чат") if auto.enabled else "Выключены")
        context = self.local_context_page.saved
        active_cards = sum(entry.enabled for entry in context.cards)
        self.inspector_values["context"].setText(
            f"Включён · активных карточек: {active_cards}" if context.settings.enabled else "Выключен")
        self.connection_pill.setText(self.status.text())

    def _set_status(self, text, state):
        self.status.setText(text)
        self.status.setProperty("state", state)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.connection_pill.setText(text)

    def _update_prompt_count(self):
        self.prompt_count.setText(f"{len(self.prompt.toPlainText()):,} / 20 000 символов".replace(",", " "))

    def _mark_dirty(self, *_):
        if self._saving:
            return
        self._dirty = True
        self.save_hint.setText("Есть несохранённые изменения")
        self.notice.hide()
        self._refresh_workspace()

    def _local_context_changed(self):
        if self.safety_page.dirty:
            self.save_hint.setText("Есть несохранённые настройки безопасности — нажмите «Применить»")
        elif self.local_context_page.dirty:
            self.save_hint.setText("Локальный контекст изменён — примените его в своей вкладке")
        else:
            self.save_hint.setText("Есть несохранённые изменения" if self._dirty else "Все изменения сохранены")
        self._refresh_workspace()

    def _save(self, *, for_start=False):
        if self.process.state() != QProcess.NotRunning:
            return False
        self.notice.hide()
        self._saving = True
        try:
            try:
                rows = self.profiles_editor.validated()
            except ProfileError:
                self._navigate(PAGE_VIEWERS)
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
                        self._navigate(PAGE_CONNECTION)
                        self._field_widgets()[name].setFocus()
                        raise
            if len(self.prompt.toPlainText().strip()) > 20000:
                self._navigate(PAGE_BEHAVIOR)
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
            self._local_context_changed()
            return True
        except OSError:
            self.notice.setText("Не удалось сохранить настройки. Проверьте доступ к папке с данными приложения и повторите попытку.")
            self.notice.show()
            return False
        except ValueError as exc:
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
        self.local_context_page.set_running(running)
        self.prompt_builder.set_editable(not running)
        self.model_catalog.set_editable(not running)
        for widget in (*self._field_widgets().values(), self.prompt, self.reset_prompt, self.save_button):
            widget.setEnabled(not running)
        self.profiles_editor.set_editable(not running)
        self.start_button.setVisible(not running)
        self.start_button.setEnabled(not running)
        self.stop_button.setVisible(running)
        self.stop_button.setEnabled(running)
        self.save_hint.setText("Самостоятельные реплики и локальный контекст можно менять во время работы" if running else
                               "Есть несохранённые изменения" if self._dirty else "Все изменения сохранены")
        if self.local_context_page.dirty or self.safety_page.dirty:
            self._local_context_changed()
        self._refresh_workspace()

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
        except OSError:
            self.notice.setText("Не удалось создать журнал работы. Проверьте доступ к папке с данными приложения и повторите запуск.")
            self.notice.show()
            return
        self._log_decoder = codecs.getincrementaldecoder("utf-8")()
        self._log_offset = 0
        self._output_tail = ""
        self._auth_url = ""
        self._auth_account = ""
        self.auth_hint.setVisible(False)
        self._navigate(PAGE_ACTIVITY)
        self.autonomous_page.log.clear()
        self._question_count = self._answer_count = 0
        self.questions_label.setText("0")
        self.answers_label.setText("0")
        self.log.clear()
        self.log.appendPlainText("Запускаю бота…")
        self._set_status("Запускается…", "starting")
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
        self._set_status("Останавливается…", "stopping")
        self.stop_button.setEnabled(False)
        self.process.terminate()
        self._kill_timer.start(3000)

    def _kill_if_running(self) -> None:
        if self.process.state() != QProcess.NotRunning:
            self.process.kill()

    def _on_started(self) -> None:
        self._set_status("Подключается…", "starting")

    def _on_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        self._kill_timer.stop()
        self._poll_log()
        self._log_timer.stop()
        if self._output_tail:
            self._append_log(self._output_tail)
            self._output_tail = ""
        self.log.appendPlainText(f"Бот остановлен (код {exit_code}).")
        self._set_status("Остановлен", "idle")
        self._set_running(False)

    def _on_process_error(self, error: QProcess.ProcessError) -> None:
        self._poll_log()
        self.log.appendPlainText(f"Ошибка запуска бота: {self.process.errorString()}")
        if error == QProcess.FailedToStart:
            self._log_timer.stop()
            self._set_status("Не удалось запустить", "error")
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
            self._set_status("Подключён к Twitch", "running")
            self.auth_hint.hide()
        if line.startswith("Откройте ссылку и войдите в Twitch под аккаунтом "):
            self._set_status("Ожидает входа в Twitch", "starting")
            self._auth_account = line.removeprefix("Откройте ссылку и войдите в Twitch под аккаунтом ").rstrip(":")
        elif line.startswith("https://") and "twitch.tv/activate" in line:
            self._auth_url = line
        elif line.startswith("Код: ") and self._auth_url:
            url = html.escape(self._auth_url, quote=True)
            code = html.escape(line[5:].strip())
            account = html.escape(self._auth_account or "нужным аккаунтом")
            self.auth_hint.setText(f'Подтвердите вход под аккаунтом {account}: <a style="color: #65B3FF;" href="{url}">открыть Twitch</a>. Код: <b>{code}</b>')
            self.auth_hint.setVisible(True)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._dirty or self.local_context_page.dirty or self.safety_page.dirty:
            choice = russian_question(self, "Несохранённые изменения",
                "Сохранить изменения настроек, безопасности, профилей и карточек перед закрытием?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
            if choice == QMessageBox.Cancel:
                event.ignore()
                return
            if choice == QMessageBox.Save:
                if self.safety_page.dirty and not self.safety_page.apply():
                    self._navigate(PAGE_SAFETY)
                    event.ignore()
                    return
                if self.local_context_page.dirty and not self.local_context_page.apply():
                    self._navigate(PAGE_LOCAL_CONTEXT)
                    event.ignore()
                    return
                if self._dirty and not self._save():
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
        self.history_page.shutdown()
        self.safety_page.shutdown()
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
