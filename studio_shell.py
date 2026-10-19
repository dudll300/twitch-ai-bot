"""Shared desktop actions, onboarding and preference controls."""
from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QPushButton, QTabWidget,
                              QVBoxLayout, QWidget, QGridLayout)

from configuration import read_config
from gui_theme import apply_theme, PALETTES
from studio_icons import studio_icon
from ui_widgets import menu_icon
from runtime_events import revision_id
from local_context import validate_document
from safety_settings import validate_settings as validate_safety
from autonomous import validate_settings as validate_auto
from ui_state import save_ui_state
from ui_widgets import Disclosure, NoWheelComboBox, ToggleSwitch, card, field, label, scroll_page

SECTION_NAMES = {0: "Подключение", 1: "Поведение", 2: "Зрители", 3: "Самостоятельные реплики", 4: "Безопасность", 7: "Знания канала"}


from ui_widgets import StudioButton as QPushButton

class StudioShell:
    def _section_page(self, content, index):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(content, 1)
        row = QHBoxLayout()
        state = label("Сохранено для следующего запуска" if index in (0, 1, 2) else "Изменения сохранены", "muted", True)
        row.addWidget(state, 1)
        revert = QPushButton("Отменить изменения")
        revert.clicked.connect(lambda: self._revert_section(index))
        row.addWidget(revert)
        save = QPushButton("Сохранить" if index in (0, 1, 2) else "Сохранить и применить")
        save.setProperty("variant", "primary")
        save.clicked.connect(lambda: self._save_section(index))
        row.addWidget(save)
        layout.addLayout(row)
        self._section_bars[index] = (state, save, revert)
        return page

    def _settings_tabs(self, connection):
        tabs = QTabWidget()
        tabs.setAccessibleName("Настройки приложения")
        tabs.addTab(connection, "Подключение")
        prefs = QWidget()
        layout = QVBoxLayout(prefs)
        layout.setContentsMargins(0, 16, 0, 0)
        appearance, box = card("Оформление", "Изменения применяются сразу и не влияют на бота.")
        self.theme_control = NoWheelComboBox()
        self.theme_control.addItem("Тёмная студия", "dark")
        self.theme_control.addItem("Светлая студия", "light")
        self.theme_control.setCurrentIndex(self.theme_control.findData(self._ui_state["theme"]))
        box.addWidget(field("Тема", self.theme_control))
        self.animations_control = ToggleSwitch("Анимации интерфейса")
        self.animations_control.setChecked(self._ui_state["animations"])
        box.addWidget(self.animations_control)
        self.preferences_error = label("", "error", True)
        self.preferences_error.hide()
        box.addWidget(self.preferences_error)
        self.theme_control.currentIndexChanged.connect(self._appearance_changed)
        self.animations_control.toggled.connect(self._appearance_changed)
        layout.addWidget(appearance)
        layout.addStretch()
        tabs.addTab(scroll_page(prefs), "Интерфейс")
        help_page, box = card("Как пользоваться студией")
        for heading, text in (
            ("Первое подключение", "Нужны аккаунт бота, канал с наградой за баллы, Client ID Public-приложения Twitch и данные AI-сервиса. Client Secret не требуется."),
            ("Перед эфиром", "Проверьте ответ без Twitch. Затем запустите бота и подтвердите вход под аккаунтом бота и владельца канала. Состояния чата и наград отображаются отдельно."),
            ("Изменения во время работы", "Любые настройки можно редактировать. Подключение, общий промпт и профили вступают в силу после перезапуска. Остальные разделы применяются без перезапуска; оперативные переключатели используют сохранённые данные."),
            ("Предпросмотр и публикация", "Предпросмотр самостоятельных реплик показывает предложенные ответы в приложении. Публикация отправляет их в Twitch. Общая остановка завершает весь процесс бота."),
            ("Баланс AI", "Генерация промптов, ответы, сравнение моделей и AI-проверки могут расходовать баланс провайдера. Каталог загружается только по вашему действию. Пресеты и локальные проверки работают без AI-запроса."),
            ("Если возникла ошибка", "Прочитайте сообщение рядом с полем или на «Эфире». Ошибка AI не означает потерю связи с Twitch. Подробности доступны в техническом журнале и истории. Папка с данными находится внизу меню.")):
            content = label(text, "caption", True)
            box.addWidget(Disclosure(heading, content))
        setup = QPushButton("Открыть первую настройку")
        setup.clicked.connect(self._open_setup)
        box.addWidget(setup)
        tabs.addTab(scroll_page(help_page), "Справка")
        self.settings_tabs = tabs
        return tabs

    def _appearance_changed(self, *_):
        self._ui_state.update(theme=self.theme_control.currentData(), animations=self.animations_control.isChecked())
        QApplication.instance().setProperty("animations", self._ui_state["animations"])
        if not self._ui_state["animations"]:
            self._page_animation.stop()
            self._page_effect.setOpacity(1.0)
            self.orbit_header._motion.stop()
            self.orbit_header._advance(0)
        apply_theme(QApplication.instance(), self._ui_state["theme"])
        self._refresh_icons()
        self._persist_preferences()

    def _refresh_icons(self):
        colors = PALETTES[self._ui_state["theme"]]
        for index, button in enumerate(self.nav_buttons):
            button.setIcon(studio_icon(self._nav_icons[index], colors["muted"]))
        self.menu_button.setIcon(menu_icon())
        self._folder_button.setIcon(studio_icon("folder", colors["accent"], 16))
        self.start_button.setIcon(studio_icon("play", colors["primaryText"], 16))
        self.save_button.setIcon(studio_icon("save", colors["accent"], 16))
        self.stop_button.setIcon(studio_icon("stop", colors["error"], 16))
        self.restart_button.setIcon(studio_icon("restart", colors["accent"], 18))

    def _persist_preferences(self):
        try:
            save_ui_state(self._root, self._ui_state)
            self.preferences_error.hide()
        except OSError:
            self.preferences_error.setText("Не удалось сохранить оформление. Оно действует в этом окне; проверьте доступ к папке с данными.")
            self.preferences_error.show()

    def _build_setup(self):
        page, layout = card("Первый запуск", "Пройдите пять шагов. Каждый шаг можно повторить, а настройку — продолжить позже.")
        self.setup_step_title = label("", "section", True)
        self.setup_step_text = label("", "caption", True)
        layout.addWidget(self.setup_step_title)
        layout.addWidget(self.setup_step_text)
        self.setup_action = QPushButton()
        self.setup_action.setProperty("variant", "primary")
        self.setup_action.clicked.connect(self._setup_action)
        layout.addWidget(self.setup_action)
        layout.addWidget(label("Проверка AI выполняется только по вашему нажатию и может расходовать баланс. Заполненные поля не подтверждают доступность AI или награды Twitch.", "muted", True))
        controls = QGridLayout()
        self.setup_back = QPushButton("Назад")
        self.setup_back.clicked.connect(lambda: self._setup_move(-1))
        controls.addWidget(self.setup_back, 0, 0)
        self.setup_next = QPushButton("Далее")
        self.setup_next.clicked.connect(lambda: self._setup_move(1))
        controls.addWidget(self.setup_next, 0, 1)
        later = QPushButton("Продолжить позже")
        later.clicked.connect(lambda: self._navigate(5))
        controls.addWidget(later, 1, 0)
        done = QPushButton("Завершить настройку")
        done.clicked.connect(self._finish_setup)
        controls.addWidget(done, 1, 1)
        layout.addLayout(controls)
        layout.addStretch()
        self._update_setup()
        return scroll_page(page)

    def _update_setup(self):
        step = self._ui_state["setup_step"]
        steps = (
            ("AI-сервис", "Введите адрес API, ключ и доступную модель. Для пробного ответа Twitch не нужен.", "Настроить AI"),
            ("Twitch и награда", "Введите канал, аккаунт бота и Client ID. Создайте награду с обязательным вводом текста; укажите её точное название.", "Настроить Twitch"),
            ("Характер бота", "Напишите инструкции или выберите бесплатный пресет. Пустой общий промпт также разрешён.", "Настроить поведение"),
            ("Пробный ответ", "Введите вопрос и проверьте выбранную модель. Ответ не попадёт в Twitch, историю или память.", "Открыть тестирование"),
            ("Запуск и авторизация", "Запустите бота. На «Эфире» появятся инструкции входа для аккаунта бота и владельца канала.", "Открыть эфир"),
        )
        title, text, action = steps[step]
        self.setup_step_title.setText(f"Шаг {step + 1} из 5 · {title}")
        self.setup_step_text.setText(text)
        self.setup_action.setText(action)
        self.setup_back.setEnabled(step > 0)
        self.setup_next.setEnabled(step < 4)

    def _setup_move(self, delta):
        self._ui_state["setup_step"] = max(0, min(4, self._ui_state["setup_step"] + delta))
        self._update_setup()
        self._persist_preferences()

    def _setup_action(self):
        step = self._ui_state["setup_step"]
        self._navigate((0, 0, 1, 6, 5)[step])
        if step in (0, 1):
            self.settings_tabs.setCurrentIndex(0)
            (self.base_url if step == 0 else self.channel).setFocus()

    def _open_setup(self):
        self._navigate(9)

    def _finish_setup(self):
        self._ui_state["setup_done"] = True
        self._persist_preferences()
        self.dashboard.setup_card.hide()
        self._navigate(5)

    def _changed_sections(self):
        changed = []
        if self._fields() != self._saved_fields:
            changed.append(0)
        if self.prompt.toPlainText() != self._saved_prompt:
            changed.append(1)
        if self.profiles_editor.rows != self._saved_profiles:
            changed.append(2)
        for index, editor in ((3, self.autonomous_page), (4, self.safety_page), (7, self.local_context_page)):
            if editor.dirty:
                changed.append(index)
        return changed

    def _update_changes(self, *_):
        if not hasattr(self, "_saved_fields") or self._saving:
            return
        changed = self._changed_sections()
        self._dirty = any(index in changed for index in (0, 1, 2))
        for index, (state, save, revert) in self._section_bars.items():
            dirty = index in changed
            if dirty:
                caption = "Черновик · после сохранения потребуется перезапуск" if index in (0, 1, 2) and self._running_gui else "Черновик · есть несохранённые изменения"
            elif index in (0, 1, 2):
                caption = "Сохранено · требуется перезапуск" if self._pending_restart else "Используется ботом" if self._running_gui else "Сохранено для следующего запуска"
            else:
                versions = {3: ("autonomous", self.autonomous_page.revision),
                            4: ("safety", self.safety_page.applied.version),
                            7: ("context", self.local_context_page.saved.revision)}
                section, version = versions[index]
                confirmed = self._runtime.settings.get(section) == revision_id(version)
                caption = "Используется ботом" if self._running_gui and confirmed else "Сохранено · ожидается обработка ботом" if self._running_gui else "Сохранено для следующего запуска"
            state.setText(caption)
            save.setEnabled(dirty)
            revert.setEnabled(dirty)
            save.setVisible(dirty)
            revert.setVisible(dirty)
        self.save_hint.setText("Есть изменения: " + ", ".join(SECTION_NAMES[index] for index in changed) if changed else "Есть сохранённые изменения для перезапуска" if self._pending_restart else "Все изменения сохранены")
        self.save_button.setEnabled(bool(changed))
        self.footer.setVisible(bool(changed))
        self.restart_button.setVisible(self._running_gui and self._pending_restart)
        self.start_button.setText("Сохранить и запустить" if changed and not self._running_gui else "Запустить бота")

    def _validate_live(self, changed):
        auto = self.autonomous_page
        values = {key: widget.value() for key, widget in auto.inputs.items()}
        values.update(enabled=auto.enabled.isChecked(), mode=auto.mode.currentData(), participation=auto.participation.currentData(), autonomous_prompt=auto.autonomous_prompt.toPlainText())
        for index, validate in ((3, lambda: validate_auto(values)),
                                (4, lambda: validate_safety(self.safety_page.draft())),
                                (7, lambda: validate_document(self.local_context_page.raw_document()))):
            if index in changed:
                try:
                    validate()
                except ValueError:
                    self._navigate(index)
                    raise

    def _save_section(self, index):
        if index in (0, 1, 2):
            return self._save(only=index)
        editor = {3: self.autonomous_page, 4: self.safety_page, 7: self.local_context_page}[index]
        result = editor.apply()
        self._update_changes()
        self._refresh_workspace()
        return result

    def _save_all(self, *, for_start=False):
        changed = self._changed_sections()
        try:
            if for_start:
                self._validate_startup(for_start=True)
            else:
                for index in (0, 1, 2):
                    if index in changed:
                        self._validate_startup(only=index)
            if any(index in changed for index in (3, 4, 7)):
                self._validate_live(changed)
        except (OSError, ValueError, TypeError) as exc:
            self.notice.setText(str(exc))
            self.notice.show()
            return False
        if for_start and not self._save(for_start=True):
            return False
        if not for_start:
            for index in (0, 1, 2):
                if index in changed and not self._save(only=index):
                    return False
        for index in (3, 4, 7):
            if index in changed and not self._save_section(index):
                self._navigate(index)
                self.notice.setText("Часть разделов сохранена. Исправьте ошибку в этом разделе; оставшиеся черновики доступны для повторения.")
                self.notice.show()
                return False
        self._update_changes()
        return True

    def _revert_section(self, index):
        if index == 0:
            for name, widget in self._field_widgets().items():
                value = self._saved_fields[name]
                widget.setCurrentText(value) if hasattr(widget, "setCurrentText") else widget.setText(value)
        elif index == 1:
            self.prompt.setPlainText(self._saved_prompt)
        elif index == 2:
            self.profiles_editor.rows = deepcopy(self._saved_profiles)
            self.profiles_editor._removed = []
            self.profiles_editor.undo_button.hide()
            self.profiles_editor._rebuild(0 if self.profiles_editor.rows else -1)
        else:
            {3: self.autonomous_page, 4: self.safety_page, 7: self.local_context_page}[index].revert()
        self._update_changes()

    def _restart(self):
        if not self._save_all(for_start=True):
            return
        self._restart_pending = True
        self._stop()
