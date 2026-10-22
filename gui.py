"""Desktop workspace for connection, behavior, viewer profiles and activity."""

import codecs
import html
import json
import sys
from copy import deepcopy

from PySide6.QtCore import QProcess, Qt, QTimer, QUrl, QSize
from PySide6.QtGui import QCloseEvent, QDesktopServices, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

from autonomous_gui import AutonomousPage
from configuration import AI_FALLBACK_MODELS, FIELDS, normalize, read_config, invalidate_tokens
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
from studio_shell import StudioShell
from studio_dashboard import Dashboard
from studio_artwork import OrbitHeader, PageTransition
from studio_motion import NavigationSurface
from studio_controls import StudioStack
from runtime_events import RuntimeState, PREFIX, activation_url
from ui_state import load_ui_state
from gui_theme import apply_theme
from studio_icons import studio_icon
from testing import credentials, make_snapshot, read_test_memory
from testing_gui import TestingPage
from safety_gui import SafetyPage
from safety_settings import policy_scope
from ui_widgets import ScrollPlainTextEdit, SlidingSidebar, card, field, label, menu_icon, russian_question, scroll_page, Disclosure, ResponsiveColumns, field_error, NoWheelComboBox

PAGES = (
    ("Настройки", "Подключение, оформление и помощь в работе со студией."),
    ("Поведение", "Задайте общий характер, язык и правила общения."),
    ("Зрители", "Личные инструкции для тех, кого бот должен узнавать."),
    ("Самостоятельные реплики", "Ответы и реакции по свежему разговору — с приоритетом наград."),
    ("Безопасность", "Проверки, политика публикации и понятные причины решений."),
    ("Эфир", "Состояние бота, публикация и ответы текущей сессии."),
    ("Тестирование", "Проверьте промпт и сравните модели без подключения Twitch."),
    ("Знания канала", "Выражения и отсылки, которые бот должен понимать."),
    ("История", "Сохранённые ответы, самостоятельные реплики и решения бота за все запуски."),
    ("Первая настройка", "Пять шагов до первого запуска."),
)
PAGE_CONNECTION, PAGE_BEHAVIOR, PAGE_VIEWERS, PAGE_AUTONOMOUS, PAGE_SAFETY, PAGE_ACTIVITY, PAGE_TESTING, PAGE_LOCAL_CONTEXT, PAGE_HISTORY, PAGE_SETUP = range(len(PAGES))


def available_screen_size():
    return QApplication.primaryScreen().availableGeometry().size()


from ui_widgets import StudioButton as QPushButton
from ui_widgets import StudioLinkLabel
from ui_widgets import SegmentedControl

class MainWindow(StudioShell, QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Twitch AI Bot")
        self.setWindowIcon(QIcon(str(resource_path("assets/app.ico"))))
        available = available_screen_size()
        self._expanded_minimum = QSize(min(800, available.width() - 24), min(480, available.height() - 48))
        self._collapsed_minimum = QSize(self._expanded_minimum.width(), self._expanded_minimum.height())
        self.resize(min(1240, available.width() - 24), min(860, available.height() - 48))
        self.setMinimumSize(self._expanded_minimum)
        self._sidebar_expanded = True
        self._root = data_dir()
        self._ui_state = load_ui_state(self._root)
        if (self._root / ".env").exists() and not (self._root / "ui-state.json").exists():
            self._ui_state["setup_done"] = True
        self._section_bars = {}
        self._running_gui = False
        self._pending_restart = False
        self._restart_pending = False
        self._stop_requested = False
        self._startup_revision = self._active_revision = 0
        self._runtime = RuntimeState()
        self._active_values = {}
        self.resize(min(self._ui_state["width"], available.width() - 24), min(self._ui_state["height"], available.height() - 48))
        values, prompt = load_settings(self._root)
        if all(values.get(key, "").strip() for key in ("TWITCH_CHANNEL", "TWITCH_BOT_NAME", "TWITCH_CLIENT_ID", "AI_API_KEY", "AI_MODEL", "AI_BASE_URL")):
            self._ui_state["setup_done"] = True
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
        outer_shell = QVBoxLayout(body)
        outer_shell.setContentsMargins(0, 0, 0, 0)
        outer_shell.setSpacing(0)
        workspace_row = QWidget()
        shell = QHBoxLayout(workspace_row)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        self.sidebar = SlidingSidebar(74)
        side_contents = NavigationSurface()
        self.navigation_surface = side_contents
        panel_layout = QVBoxLayout(self.sidebar.panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.addWidget(scroll_page(side_contents, ambient=False))
        side = QVBoxLayout(side_contents)
        self._side_layout = side
        side.setContentsMargins(8, 20, 8, 16)
        side.setSpacing(6)
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
        # Keep the product name in the full-width top bar.
        self._brand_labels = []

        side.addSpacing(6)
        self.nav_group = QButtonGroup(self)
        self.nav_buttons = []
        self._sidebar_aux = []
        nav_icons = ("sliders", "sparkles", "users", "messages", "shield", "activity", "flask", "book", "history", "sparkles")
        self._nav_icons = nav_icons
        for index, (name, _) in enumerate(PAGES):
            button = QPushButton(name.replace("Самостоятельные реплики", "Самостоятельные\nреплики"))
            button.setProperty("variant", "nav")
            button.setProperty("sharedNavPill", True)
            button.setIcon(studio_icon(nav_icons[index]))
            button.setIconSize(QSize(18, 18))
            button.setCheckable(True)
            self.nav_group.addButton(button, index)
            self.nav_buttons.append(button)
            button.setToolTip(name)
            button.setAccessibleName(name)
        for heading, indices in (("РАБОТА", (5, 8)), ("НАСТРОЙКА БОТА", (1, 2, 3, 7)), ("ИНСТРУМЕНТЫ", (6, 4, 0))):
            group_label = label(heading, "eyebrow")
            self._sidebar_aux.append(group_label)
            group_label.hide()
            for index in indices:
                side.addWidget(self.nav_buttons[index])
        self.nav_buttons[9].hide()
        self.nav_group.idClicked.connect(self._navigate)
        side.addStretch()
        self.status = label("Остановлен", "historyStatus")
        self.status.setObjectName("status")
        self.status.setProperty("state", "idle")
        self.status.setWordWrap(False)
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
        self._sidebar_profile = profile
        side.addWidget(profile)
        profile.hide()
        side.addSpacing(12)
        folder = QPushButton("Папка с данными")
        folder.setProperty("variant", "quiet")
        folder.setIcon(studio_icon("folder", size=16))
        folder.clicked.connect(self._open_folder)
        self._folder_button = folder
        side.addWidget(folder)
        shell.addWidget(self.sidebar)

        main_area = QWidget()
        main_area.setObjectName("mainArea")
        main = QVBoxLayout(main_area)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        topbar = QFrame()
        self.topbar = topbar
        topbar.setMinimumHeight(84)
        topbar.setObjectName("topbar")
        header = QHBoxLayout(topbar)
        header.setContentsMargins(24, 12, 24, 12)
        header.setSpacing(14)
        self.menu_button = QPushButton()
        self.menu_button.setIcon(menu_icon())
        self.menu_button.setIconSize(QSize(22, 22))
        self.menu_button.setObjectName("menuToggle")
        self.menu_button.setFixedSize(40, 40)
        self.menu_button.setCheckable(True)
        self.menu_button.clicked.connect(self._toggle_sidebar)
        self.menu_button.hide()
        header.addWidget(mark)
        wordmark = QHBoxLayout()
        wordmark.setSpacing(7)
        self.brand_label = label("Twitch AI Bot", "brand")
        wordmark.addWidget(self.brand_label)
        header.addLayout(wordmark)
        header.addStretch(1)
        workspace_heading = QVBoxLayout()
        workspace_heading.setSpacing(2)
        self.workspace_channel = label("Ваш канал", "channel", True)
        self.workspace_channel.setMaximumWidth(200)
        self.workspace_channel.setMinimumWidth(0)
        workspace_heading.addWidget(self.workspace_channel)
        header.addLayout(workspace_heading)
        self.status.setMaximumWidth(190)
        header.addWidget(self.status, 0, Qt.AlignVCenter)
        self.start_button = QPushButton("Запустить бота")
        self.start_button.setObjectName("primary")
        self.start_button.setIcon(studio_icon("play", "#112B48", 16))
        self.start_button.clicked.connect(self._start)
        header.addWidget(self.start_button)
        self.restart_button = QPushButton("Перезапустить")
        self.restart_button.setAccessibleName("Сохранить изменения и перезапустить бота")
        self.restart_button.setToolTip("Сохранить изменения и перезапустить бота")
        self.restart_button.clicked.connect(self._restart)
        self.restart_button.hide()
        header.addWidget(self.restart_button)
        self.stop_button = QPushButton("Остановить бота")
        self.stop_button.setProperty("variant", "primary")
        self.stop_button.setIcon(studio_icon("stop", "#112B48", 16))
        self.stop_button.clicked.connect(self._stop)
        self.stop_button.hide()
        header.addWidget(self.stop_button)
        outer_shell.addWidget(topbar)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(24, 24, 10, 24)
        content_layout.setSpacing(22)
        self.page_title = label("", "title")
        self.page_description = label("", "muted", True)
        self.orbit_header = OrbitHeader(self.page_title, self.page_description)
        content_layout.addWidget(self.orbit_header)
        self.notice = label("", "error", True)
        self.notice.hide()
        content_layout.addWidget(self.notice)
        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_connection(values))
        self.pages.addWidget(self._build_behavior(prompt))
        self.profiles_editor = ProfilesEditor(self._root / "profiles.json")
        self.profiles_editor.test_requested.connect(self._test_profile)
        self.pages.addWidget(self._section_page(self.profiles_editor, PAGE_VIEWERS))
        self.autonomous_page = AutonomousPage(self._root)
        self.pages.addWidget(self._section_page(self.autonomous_page, PAGE_AUTONOMOUS))
        self.safety_page = SafetyPage(self._root, self._test_credentials, self._runtime_model)
        self.pages.addWidget(self._section_page(self.safety_page, PAGE_SAFETY))
        self.pages.addWidget(self._build_activity())
        self.testing_page = TestingPage(self._test_credentials, self._test_snapshot,
                                        lambda: self.profiles_editor.rows, safety_store=self.safety_page.store,
                                        get_answer_model=lambda: self.model.currentText(), get_diagnostic_model=self._runtime_model)
        self.pages.addWidget(self.testing_page)
        self.local_context_page = LocalContextPage(self._root)
        self.pages.addWidget(self._section_page(self.local_context_page, PAGE_LOCAL_CONTEXT))
        self.history_page = HistoryPage(self._root)
        self.pages.addWidget(self.history_page)
        self.pages.addWidget(self._build_setup())
        self._detail_views = {"profiles": self.profiles_editor.master_detail,
                              "context": self.local_context_page.master_detail,
                              "history": self.history_page.master_detail}
        for name, sizes in self._ui_state["splitters"].items():
            self._detail_views[name].restore_sizes(sizes)
        self.safety_page.test_requested.connect(self._test_security)
        self.safety_page.history_requested.connect(self._security_history)
        self._transition = PageTransition(self.pages)
        self._theme_transition = PageTransition(body, kind="theme")
        content_layout.addWidget(self.pages, 1)
        main.addWidget(content, 1)

        footer = QFrame()
        self.footer = footer
        footer.setObjectName("footer")
        actions = QHBoxLayout(footer)
        actions.setContentsMargins(28, 14, 28, 14)
        actions.setSpacing(12)
        self.save_hint = label("Все изменения сохранены", "muted", True)
        actions.addWidget(self.save_hint, 1)
        self.save_button = QPushButton("Сохранить всё")
        self.save_button.setIcon(studio_icon("save", "#F3F5FA", 16))
        self.save_button.setToolTip("Сохранить настройки и профили · Ctrl+S")
        self.save_button.clicked.connect(lambda: self._save_all())
        actions.addWidget(self.save_button)
        main.addWidget(footer)
        shell.addWidget(main_area, 1)
        outer_shell.addWidget(workspace_row, 1)
        self.setCentralWidget(body)
        focus_order = [self.nav_buttons[index] for index in (5, 8, 1, 2, 3, 7, 6, 4, 0)]
        focus_order += [self._folder_button, self.menu_button, self.start_button, self.stop_button]
        for first, second in zip(focus_order, focus_order[1:]):
            QWidget.setTabOrder(first, second)
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
        QShortcut(QKeySequence.Save, self, activated=lambda: self._save_all())
        self._saved_values = dict(values)
        self._saved_fields = self._fields()
        self._saved_prompt = prompt
        self._saved_profiles = deepcopy(self.profiles_editor.rows)
        self.autonomous_page.changed.connect(self._update_changes)
        self.safety_page.changed.connect(self._update_changes)
        self.local_context_page.changed.connect(self._update_changes)
        self.autonomous_page.apply_button.hide()
        self.safety_page.apply_button.hide()
        self.local_context_page.apply_button.hide()
        self.local_context_page.hint.hide()
        self.dashboard.setup_card.setVisible(not self._ui_state["setup_done"])
        QApplication.instance().setProperty("animations", self._ui_state["animations"])
        apply_theme(QApplication.instance(), self._ui_state["theme"])
        self._navigate(PAGE_SETUP if not (self._root / ".env").exists() and not self._ui_state["setup_done"] else PAGE_ACTIVITY)
        self._refresh_icons()
        self._update_changes()
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
        self.sidebar.set_expanded(expanded, animate and self._ui_state["animations"])

    def _navigate(self, index):
        if index == PAGE_TESTING:
            self.testing_page.refresh_profiles()
        changed = self.pages.currentIndex() != index
        previous = self._transition.snapshot() if changed and self.isVisible() and self._ui_state["animations"] else None
        self.pages.setCurrentIndex(index)
        self.nav_buttons[index].setChecked(True)
        self.navigation_surface.select(self.nav_buttons[index])
        self.orbit_header.set_page(PAGES[index][0], index == PAGE_ACTIVITY, PAGES[index][1])
        self._refresh_workspace()
        if previous is not None:
            self._transition.start(previous)

    def _test_credentials(self):
        saved_key = read_config(self._root / ".env").get("AI_API_KEY", "") if not self.api_key.text().strip() else ""
        return credentials(self.base_url.text(), self.api_key.text(), saved_key)

    def _runtime_model(self):
        return self._active_values.get("AI_MODEL", self.model.currentText()) if self._running_gui else self.model.currentText()

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
        grid.setContentsMargins(0, 0, 0, 0)
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
        docs = StudioLinkLabel()
        docs.setText('<a href="https://dev.twitch.tv/console/apps">Создать приложение в Twitch ↗</a>')
        docs.setOpenExternalLinks(True)
        docs.setWordWrap(True)
        twitch_layout.addWidget(docs)

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
            if widget is self.fallback_models:
                ai_layout.addWidget(Disclosure("Запасные модели · необязательно", field(caption, widget)))
            else:
                ai_layout.addWidget(field(caption, widget))
        self.fallback_models.setToolTip("Модели вызываются по порядку при сбоях основной. Введите ID через запятую.")
        ai_layout.addStretch()
        ai_layout.addWidget(label("Ключ хранится только на этом компьютере.", "muted", True))
        grid.addWidget(ResponsiveColumns(ai, twitch), 0, 0, 1, 2)
        grid.setRowStretch(1, 1)
        return self._settings_tabs(self._section_page(scroll_page(page), PAGE_CONNECTION))

    def _build_behavior(self, prompt):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
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
        self.behavior_mode = NoWheelComboBox()
        self.behavior_mode.addItems(("Редактировать", "Создать", "Улучшить"))
        self.behavior_mode.setAccessibleName("Работа с характером бота")
        layout.addWidget(SegmentedControl(self.behavior_mode))
        companion = QWidget()
        companion_layout = QVBoxLayout(companion)
        companion_layout.setContentsMargins(0, 0, 0, 0)
        companion_layout.setSpacing(20)
        rhythm, rhythm_layout = card("Ритм ответа")
        rhythm_layout.addWidget(label(f"До {ANSWER_MAX_CHARS} символов", "reply", True))
        rhythm_layout.addWidget(label("Короткий ответ легче заметить в быстром чате. Язык, интонацию и отношение к спойлерам задайте в инструкции.", "muted", True))
        companion_layout.addWidget(rhythm)
        preview, preview_layout = card("Сначала — пробный разговор")
        preview_layout.addWidget(label("Проверьте характер на своём вопросе. Тест использует черновик инструкции и не отправляет ответ в Twitch.", "muted", True))
        test = QPushButton("Попробовать вопрос")
        test.clicked.connect(self._test_prompt)
        preview_layout.addWidget(test)
        companion_layout.addWidget(preview)
        companion_layout.addStretch()
        editor_columns = ResponsiveColumns(main, companion, threshold=850, ratios=(3, 2))
        self.prompt_builder = PromptBuilder(self._test_credentials, self.prompt.toPlainText,
                                            self.prompt.setPlainText)
        self.behavior_pages = StudioStack()
        self.behavior_pages.addWidget(scroll_page(editor_columns))
        self.behavior_pages.addWidget(scroll_page(self.prompt_builder))
        layout.addWidget(self.behavior_pages, 1)
        self._behavior_mode_index = 0
        def mode_changed(index):
            direction = 1 if index > self._behavior_mode_index else -1
            self._behavior_mode_index = index
            self.behavior_pages.switch(0 if index == 0 else 1,
                lambda: self.prompt_builder.set_operation_mode("improve" if index == 2 else "create"),
                direction=direction)
        self.behavior_mode.currentIndexChanged.connect(mode_changed)
        return self._section_page(page, PAGE_BEHAVIOR)

    def _build_activity(self):
        self.dashboard = Dashboard(self._root)
        self.dashboard.history_requested.connect(lambda: self._navigate(PAGE_HISTORY))
        self.dashboard.settings_requested.connect(lambda: self._navigate(PAGE_CONNECTION))
        self.dashboard.test_requested.connect(self._test_prompt)
        self.dashboard.setup_requested.connect(self._open_setup)
        self.dashboard.autonomy_requested.connect(lambda: self._navigate(PAGE_AUTONOMOUS))
        self.questions_label = self.dashboard.metrics["questions"]
        self.answers_label = self.dashboard.metrics["answers"]
        self.auth_hint = self.dashboard.auth_hint
        self.auth_hint.setObjectName("authHint")
        self.auth_hint.setTextFormat(Qt.RichText)
        self.auth_hint.setOpenExternalLinks(True)
        self.log = self.dashboard.log
        self.inspector_values = self.dashboard.inspector_values
        self.connection_pill = self.dashboard.connection_pill
        return self.dashboard

    def _refresh_workspace(self, *_):
        """Display drafts and saved live settings without changing their persistence."""
        source = self._active_values if self._running_gui else getattr(self, "_saved_values", self._fields())
        channel = source.get("TWITCH_CHANNEL", "").strip()
        try:
            channel = normalize("TWITCH_CHANNEL", channel) if channel else ""
        except ValueError:
            channel = ""
        channel_caption = f"@{channel}" if channel else "Канал не выбран"
        self.workspace_channel.setText(channel_caption if self.width() >= 1100 or len(channel_caption) <= 18 else channel_caption[:15] + "…")
        self.workspace_channel.setToolTip(channel_caption)
        self.sidebar_channel.setText(channel_caption)
        bot = source.get("TWITCH_BOT_NAME", "").strip()
        self.sidebar_hint.setText(f"Бот: {bot}" if bot else "Настройте подключение")
        self.inspector_values["channel"].setText(channel_caption)
        self.inspector_values["model"].setText(source.get("AI_MODEL", "").strip() or "Не выбрана")
        self.inspector_values["reward"].setText(source.get("TWITCH_REWARD_TITLE", "").strip() or "Не указана")
        auto = self.autonomous_page.saved
        self.dashboard.update_participation(auto)
        self.inspector_values["autonomous"].setText(
            ("Предпросмотр" if auto.mode == "preview" else "Публикация в чат") if auto.enabled else "Выключены")
        context = self.local_context_page.saved
        active_cards = sum(entry.enabled for entry in context.cards)
        self.inspector_values["context"].setText(
            f"Включён · активных карточек: {active_cards}" if context.settings.enabled else "Выключен")
        self.connection_pill.setText(self.status.text())
        configured = all(source.get(key, "").strip() for key in ("TWITCH_CHANNEL", "TWITCH_BOT_NAME", "TWITCH_CLIENT_ID", "AI_API_KEY", "AI_MODEL", "AI_BASE_URL"))
        self.dashboard.setup_card.setVisible(not configured)
        self.dashboard.update_state(self._runtime)
        self.orbit_header.update_state(self._runtime)
        self.dashboard.inspector_title.setText("Используется ботом" if self._running_gui else "Сохранённые настройки")

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
        self.notice.hide()
        self._update_changes()
        self._refresh_workspace()

    def _local_context_changed(self):
        self._update_changes()
        self._refresh_workspace()

    def _validate_startup(self, *, for_start=False, only=None):
        if only in (None, PAGE_VIEWERS):
            try:
                self.profiles_editor.validated()
            except ProfileError:
                self._navigate(PAGE_VIEWERS)
                raise
        if only in (None, PAGE_CONNECTION) or for_start:
            fields = self._fields() if only in (None, PAGE_CONNECTION) else self._saved_values
            previous = read_config(self._root / ".env")
            for name, _ in FIELDS:
                value = fields[name]
                if name == "AI_API_KEY" and not value.strip():
                    value = previous.get(name, "")
                if for_start or value.strip():
                    try:
                        normalize(name, value)
                    except ValueError as exc:
                        self._navigate(PAGE_CONNECTION)
                        self.settings_tabs.setCurrentIndex(0)
                        field_error(self._field_widgets()[name], str(exc))
                        raise
        text = self.prompt.toPlainText() if only in (None, PAGE_BEHAVIOR) else self._saved_prompt
        if only in (None, PAGE_BEHAVIOR) and len(text.strip()) > 20000:
            self._navigate(PAGE_BEHAVIOR)
            field_error(self.prompt, "Общий промпт должен содержать не более 20 000 символов.")
            raise ValueError("Общий промпт должен содержать не более 20 000 символов.")

    def _save(self, *, for_start=False, only=None):
        self.notice.hide()
        self._saving = True
        try:
            self._validate_startup(for_start=for_start, only=only)
            changed = self._changed_sections()
            fields = self._fields() if only in (None, PAGE_CONNECTION) else self._saved_values
            prompt = upgrade_generated_prompt(self.prompt.toPlainText() if only in (None, PAGE_BEHAVIOR) else self._saved_prompt)
            sections = tuple(name for index, name in ((0, "connection"), (1, "behavior")) if only in (None, index))
            if sections:
                save_settings(fields, prompt, self._root, allow_incomplete=not for_start,
                              sections=sections, invalidate_auth=not self._running_gui)
            if only in (None, PAGE_CONNECTION):
                self._saved_values = load_settings(self._root)[0]
                self._has_saved_key = bool(self._saved_values.get("AI_API_KEY"))
                self.api_key.clear()
                self.api_key.setPlaceholderText("Ключ сохранён" if self._has_saved_key else "Ключ вашего AI-сервиса")
                self._saved_fields = self._fields()
                if 0 in changed:
                    self._startup_revision += 1
            if only in (None, PAGE_BEHAVIOR):
                self.prompt.setPlainText(prompt)
                self._saved_prompt = prompt
                if 1 in changed:
                    self._startup_revision += 1
            if only in (None, PAGE_VIEWERS):
                rows = self.profiles_editor.validated()
                save_profiles(self._root / "profiles.json", rows)
                self.profiles_editor.saved(rows)
                self._saved_profiles = deepcopy(rows)
                if 2 in changed:
                    self._startup_revision += 1
            self._pending_restart = self._running_gui and self._startup_revision != self._active_revision
            return True
        except OSError:
            self.notice.setText("Не все разделы удалось сохранить. Черновики остаются в редакторах; проверьте доступ к папке с данными.")
            self.notice.show()
            return False
        except ValueError as exc:
            self.notice.setText(str(exc))
            self.notice.show()
            return False
        finally:
            self._saving = False
            self._pending_restart = self._running_gui and self._startup_revision != self._active_revision
            self._update_changes()
            self._refresh_workspace()

    def _field_widgets(self):
        return {"TWITCH_CHANNEL": self.channel, "TWITCH_BOT_NAME": self.bot_name,
                "TWITCH_CLIENT_ID": self.client_id, "TWITCH_REWARD_TITLE": self.reward_title,
                "AI_BASE_URL": self.base_url, "AI_API_KEY": self.api_key,
                "AI_MODEL": self.model, "AI_FALLBACK_MODELS": self.fallback_models}

    def _set_running(self, running):
        self._running_gui = running
        if not running:
            self._pending_restart = False
        self.autonomous_page.set_running(running)
        self.local_context_page.set_running(running)
        self.prompt_builder.set_editable(True)
        self.model_catalog.set_editable(True)
        self.profiles_editor.set_editable(True)
        for widget in (*self._field_widgets().values(), self.prompt, self.reset_prompt):
            widget.setEnabled(True)
        self.start_button.setVisible(not running)
        self.start_button.setEnabled(not running)
        self.stop_button.setVisible(running)
        self.stop_button.setEnabled(running)
        self._update_changes()
        self._refresh_workspace()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, "nav_buttons"):
            return
        compact = event.size().width() < 1100
        self.topbar.setMinimumHeight(68 if compact else 84)
        self.restart_button.setText("" if compact else "Перезапустить")
        self._side_layout.setContentsMargins(8, 16, 8, 16)
        for index, button in enumerate(self.nav_buttons):
            button.setText("")
        for widget in self._sidebar_aux + self._brand_labels:
            widget.hide()
        self._sidebar_profile.hide()
        self._folder_button.setText("")
        self._folder_button.setToolTip("Открыть папку с данными")
        self.sidebar.panel_width = 74
        self.sidebar.panel.setFixedWidth(self.sidebar.panel_width)
        self.sidebar.set_expanded(self._sidebar_expanded, animate=False)
        if hasattr(self, "dashboard"):
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
        if self.process.state() != QProcess.NotRunning or not self._save_all(for_start=True):
            return
        try:
            invalidate_tokens(self._root, self._active_values or self._saved_values, self._saved_values)
        except OSError:
            self.notice.setText("Не удалось обновить авторизацию для новых настроек. Проверьте доступ к папке с данными.")
            self.notice.show()
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
        self._runtime = RuntimeState()
        self._stop_requested = False
        self._active_values = dict(self._saved_values)
        self._active_revision = self._startup_revision
        self._pending_restart = False
        self.dashboard.session = ""
        self.dashboard.feed.clear()
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
        self._stop_requested = True
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
        self._runtime.chat = self._runtime.eventsub = "disconnected"
        self._runtime.queue = 0
        self._runtime.processing = False
        expected_stop = self._stop_requested
        failed = not expected_stop and (exit_code != 0 or _status == QProcess.ExitStatus.CrashExit)
        self._runtime.process = "error" if failed else "idle"
        self._set_status("Ошибка процесса" if failed else "Остановлен", "error" if failed else "idle")
        self._set_running(False)
        if self._restart_pending:
            self._restart_pending = False
            QTimer.singleShot(0, self._start)

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
        if line.startswith(PREFIX):
            if self._runtime.consume(line):
                self._render_runtime()
            return
        if line.startswith("AUTO_EVENT "):
            try:
                event = json.loads(line.removeprefix("AUTO_EVENT "))
                if isinstance(event, dict):
                    self.autonomous_page.push_event(event)
            except ValueError:
                pass
            return
        self.log.appendPlainText(line)
        if not self._runtime.session and line.startswith("Вопрос от ") and "принят" in line:
            self._question_count += 1
            self.questions_label.setText(str(self._question_count))
        elif not self._runtime.session and line.startswith("Ответ отправлен для "):
            self._answer_count += 1
            self.answers_label.setText(str(self._answer_count))
        elif line.startswith("Жду вопросов по награде "):
            if not self._runtime.session:
                self._set_status("Чат не подтверждён", "starting")
            self.auth_hint.hide()
        if line.startswith("Откройте ссылку и войдите в Twitch под аккаунтом "):
            self._set_status("Ожидает входа в Twitch", "starting")
            self._auth_account = line.removeprefix("Откройте ссылку и войдите в Twitch под аккаунтом ").rstrip(":")
        elif activation_url(line):
            self._auth_url = activation_url(line)
        elif line.startswith("Код: ") and self._auth_url:
            url = html.escape(self._auth_url, quote=True)
            code = html.escape(line[5:].strip())
            account = html.escape(self._auth_account or "нужным аккаунтом")
            self.auth_hint.setText(f'Подтвердите вход под аккаунтом {account}: <a href="{url}">открыть Twitch</a>. Код: <b>{code}</b>')
            self.auth_hint.setVisible(True)

    def _render_runtime(self):
        state = self._runtime
        if self._stop_requested:
            self._set_status("Останавливается…", "stopping")
        elif state.chat == "ready" and state.eventsub == "ready":
            self._set_status("Бот работает", "running")
        elif state.chat in ("disconnected", "error") or state.eventsub in ("disconnected", "error"):
            self._set_status("Связь прервана", "error")
        else:
            self._set_status("Подключается…", "starting")
        auth = state.auth
        if auth.get("state") == "required":
            role = "владельца канала" if auth.get("role") == "broadcaster" else "бота"
            url = html.escape(str(auth.get("url", "")), quote=True)
            self.auth_hint.setText(f'Войдите под аккаунтом {role}: <b>{html.escape(str(auth.get("account", "")))}</b>. <a href="{url}">Открыть Twitch</a>. Код: <b>{html.escape(str(auth.get("code", "")))}</b>')
            expires = auth.get("expires_in")
            if type(expires) is int and expires > 0:
                self.auth_hint.setText(self.auth_hint.text() + f". Срок кода: {max(1, expires // 60)} мин.")
            self.auth_hint.show()
        elif auth.get("state") == "complete":
            self.auth_hint.hide()
        elif auth.get("state") == "failed":
            self.auth_hint.setText("Время входа истекло. Запустите бота снова." if auth.get("reason") == "expired" else "Выбран другой аккаунт Twitch. Повторите вход под указанным аккаунтом.")
            self.auth_hint.show()
        self._update_changes()
        self._refresh_workspace()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._changed_sections():
            choice = russian_question(self, "Несохранённые изменения",
                "Сохранить изменения всех разделов перед закрытием?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
            if choice == QMessageBox.Cancel:
                event.ignore()
                return
            if choice == QMessageBox.Save and not self._save_all():
                event.ignore()
                return
        if self.process.state() != QProcess.NotRunning:
            choice = russian_question(self, "Бот работает", "Остановить бота и закрыть приложение?",
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if choice != QMessageBox.Yes:
                event.ignore()
                return
        self._restart_pending = False
        self._ui_state.update(width=self.width(), height=self.height())
        self._ui_state["splitters"] = {name: view.saved_sizes() for name, view in self._detail_views.items()}
        self._persist_preferences()
        self._log_timer.stop()
        if self.process.state() != QProcess.NotRunning:
            self.process.kill()
            self.process.waitForFinished(1000)
        self.testing_page.shutdown()
        self.model_catalog.shutdown()
        self.prompt_builder.shutdown()
        self.history_page.shutdown()
        self.dashboard.shutdown()
        self.safety_page.shutdown()
        super().closeEvent(event)


def run_gui() -> int:
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(str(resource_path("assets/app.ico"))))
    app.setStyle("Fusion")
    apply_theme(app, "dark")
    window = MainWindow()
    window.show()
    return app.exec()
