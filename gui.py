"""Desktop control panel for Chunda."""

import codecs
import html
import sys

from PySide6.QtCore import QProcess, Qt, QTimer, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices, QFont
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QSizePolicy, QTabWidget, QVBoxLayout, QWidget,
)

from bot import AI_FALLBACK_MODELS, SYSTEM_PROMPT
from paths import data_dir, resource_path
from settings import load_settings, save_settings
from start import read_config


STYLE = """
QWidget { background: #17151d; color: #f3eff8; font-size: 14px; }
QMainWindow { background: #17151d; }
QGroupBox { border: 1px solid #3c3548; border-radius: 10px; margin-top: 16px;
            padding: 16px 12px 12px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
QLineEdit, QPlainTextEdit, QComboBox { background: #25212e; border: 1px solid #51475f;
    border-radius: 7px; padding: 8px; selection-background-color: #9c73d0; }
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus { border-color: #bb91ed; }
QPushButton { background: #373043; border: 1px solid #544863; border-radius: 7px;
              padding: 9px 16px; font-weight: 600; }
QPushButton:hover { background: #473a58; }
QPushButton:disabled { color: #8f8999; background: #26232d; }
QPushButton#primary { background: #ae7ddd; color: #1c1427; border-color: #ae7ddd; }
QPushButton#primary:hover { background: #c39af0; }
QTabWidget::pane { border: 0; }
QTabBar::tab { background: #27222f; padding: 10px 20px; border-radius: 6px; margin-right: 6px; }
QTabBar::tab:selected { background: #634e7b; }
QScrollArea { border: 0; }
"""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Чунда — Twitch-бот")
        self.resize(900, 720)
        self._root = data_dir()
        values, prompt = load_settings(self._root)
        self._has_saved_key = bool(values.get("AI_API_KEY"))
        self._log_decoder = codecs.getincrementaldecoder("utf-8")()
        self._log_path = self._root / "bot-session.log"
        self._log_offset = 0
        self._output_tail = ""
        self._auth_url = ""

        self.process = QProcess(self)
        self.process.started.connect(self._on_started)
        self.process.finished.connect(self._on_finished)
        self.process.errorOccurred.connect(self._on_process_error)
        self._log_timer = QTimer(self)
        self._log_timer.setInterval(250)
        self._log_timer.timeout.connect(self._poll_log)

        body = QWidget()
        outer = QVBoxLayout(body)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(14)
        title = QLabel("Чунда")
        title.setStyleSheet("font-size: 28px; font-weight: 700; color: #e5c7ff;")
        outer.addWidget(title)
        subtitle = QLabel("Настройте бота, запустите его и следите за ответами в одном окне.")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #bbb2c8;")
        outer.addWidget(subtitle)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_settings_tab(values, prompt), "Настройки")
        self.tabs.addTab(self._build_activity_tab(), "Работа")
        outer.addWidget(self.tabs, 1)

        actions = QHBoxLayout()
        self.status = QLabel("Остановлен")
        self.status.setStyleSheet("color: #c9bfd4; font-weight: 600;")
        actions.addWidget(self.status)
        actions.addStretch()
        self.save_button = QPushButton("Сохранить")
        self.save_button.clicked.connect(self._save)
        actions.addWidget(self.save_button)
        self.start_button = QPushButton("Запустить бота")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self._start)
        actions.addWidget(self.start_button)
        self.stop_button = QPushButton("Остановить")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop)
        actions.addWidget(self.stop_button)
        outer.addLayout(actions)
        self.setCentralWidget(body)

    def _build_settings_tab(self, values: dict[str, str], prompt: str) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(4, 16, 16, 8)
        layout.setSpacing(16)

        twitch = QGroupBox("Twitch")
        twitch_form = QFormLayout(twitch)
        twitch_form.setSpacing(12)
        self.channel = QLineEdit(values.get("TWITCH_CHANNEL", ""))
        self.channel.setPlaceholderText("Логин канала или ссылка на него")
        twitch_form.addRow("Канал", self.channel)
        self.bot_name = QLineEdit(values.get("TWITCH_BOT_NAME", ""))
        self.bot_name.setPlaceholderText("Логин отдельного аккаунта бота")
        twitch_form.addRow("Аккаунт бота", self.bot_name)
        self.client_id = QLineEdit(values.get("TWITCH_CLIENT_ID", ""))
        self.client_id.setPlaceholderText("Client ID приложения типа Public")
        twitch_form.addRow("Client ID", self.client_id)
        twitch_help = QLabel('Приложение создаётся в <a href="https://dev.twitch.tv/console/apps">Twitch Developer Console</a>. При первом запуске Twitch попросит войти под аккаунтом бота.')
        twitch_help.setWordWrap(True)
        twitch_help.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        twitch_help.setOpenExternalLinks(True)
        twitch_form.addRow("", twitch_help)
        layout.addWidget(twitch)

        ai = QGroupBox("AI")
        ai_form = QFormLayout(ai)
        ai_form.setSpacing(12)
        self.base_url = QLineEdit(values.get("AI_BASE_URL", "https://ai.starimg.ru/v1"))
        ai_form.addRow("Base URL", self.base_url)
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.Password)
        self.api_key.setPlaceholderText("Ключ сохранён — оставьте пустым" if self._has_saved_key else "Введите ключ ai.starimg.ru")
        ai_form.addRow("API-ключ", self.api_key)
        self.model = QComboBox()
        self.model.addItems(AI_FALLBACK_MODELS)
        self.model.setCurrentText(values.get("AI_MODEL", AI_FALLBACK_MODELS[0]))
        self.model.setToolTip("Выберите основную модель DeepSeek")
        ai_form.addRow("Основная модель", self.model)
        model_help = QLabel("При временной ошибке бот пробует остальные модели DeepSeek из списка по порядку.")
        model_help.setWordWrap(True)
        model_help.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        ai_form.addRow("", model_help)
        layout.addWidget(ai)

        persona = QGroupBox("Характер Чунды")
        persona_layout = QVBoxLayout(persona)
        prompt_help = QLabel("Системный промпт. Изменения вступят в силу после следующего запуска бота.")
        prompt_help.setWordWrap(True)
        persona_layout.addWidget(prompt_help)
        self.prompt = QPlainTextEdit(prompt)
        self.prompt.setMinimumHeight(190)
        persona_layout.addWidget(self.prompt)
        reset_row = QHBoxLayout()
        reset_row.addStretch()
        reset_prompt = QPushButton("Вернуть исходный промпт")
        reset_prompt.clicked.connect(lambda: self.prompt.setPlainText(SYSTEM_PROMPT))
        reset_row.addWidget(reset_prompt)
        persona_layout.addLayout(reset_row)
        layout.addWidget(persona)
        layout.addStretch()
        scroll.setWidget(content)
        return scroll

    def _build_activity_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 18, 4, 8)
        self.auth_hint = QLabel()
        self.auth_hint.setWordWrap(True)
        self.auth_hint.setOpenExternalLinks(True)
        self.auth_hint.setVisible(False)
        self.auth_hint.setStyleSheet("background: #31263f; border-radius: 8px; padding: 12px;")
        layout.addWidget(self.auth_hint)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Здесь появятся сообщения после запуска бота.")
        self.log.setFont(QFont("Consolas", 10))
        self.log.document().setMaximumBlockCount(2000)
        layout.addWidget(self.log, 1)
        folder = QPushButton("Открыть папку с настройками и памятью")
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._root))))
        layout.addWidget(folder, 0, Qt.AlignLeft)
        return page

    def _fields(self) -> dict[str, str]:
        return {
            "TWITCH_CHANNEL": self.channel.text(),
            "TWITCH_BOT_NAME": self.bot_name.text(),
            "TWITCH_CLIENT_ID": self.client_id.text(),
            "AI_BASE_URL": self.base_url.text(),
            "AI_API_KEY": self.api_key.text(),
            "AI_MODEL": self.model.currentText(),
        }

    def _save(self) -> bool:
        try:
            save_settings(self._fields(), self.prompt.toPlainText(), self._root)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Не удалось сохранить настройки", str(exc))
            return False
        self._has_saved_key = bool(read_config(self._root / ".env").get("AI_API_KEY"))
        self.api_key.clear()
        self.api_key.setPlaceholderText("Ключ сохранён — оставьте пустым")
        self.status.setText("Настройки сохранены")
        return True

    def _set_running(self, running: bool) -> None:
        for widget in (self.channel, self.bot_name, self.client_id, self.base_url,
                       self.api_key, self.model, self.prompt, self.save_button):
            widget.setEnabled(not running)
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)

    def _start(self) -> None:
        if self.process.state() != QProcess.NotRunning or not self._save():
            return
        try:
            self._log_path.write_text("", encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Не удалось создать журнал", str(exc))
            return
        self._log_decoder = codecs.getincrementaldecoder("utf-8")()
        self._log_offset = 0
        self._output_tail = ""
        self._auth_url = ""
        self.auth_hint.setVisible(False)
        self.tabs.setCurrentIndex(1)
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
        QTimer.singleShot(3000, self._kill_if_running)

    def _kill_if_running(self) -> None:
        if self.process.state() != QProcess.NotRunning:
            self.process.kill()

    def _on_started(self) -> None:
        self.status.setText("Бот запущен")

    def _on_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
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
        self.log.appendPlainText(line)
        if line.startswith("https://") and "twitch.tv/activate" in line:
            self._auth_url = line
        elif line.startswith("Код: ") and self._auth_url:
            url = html.escape(self._auth_url, quote=True)
            code = html.escape(line[5:].strip())
            self.auth_hint.setText(f'Подтвердите вход под аккаунтом бота: <a href="{url}">открыть Twitch</a>. Код: <b>{code}</b>')
            self.auth_hint.setVisible(True)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._log_timer.stop()
        if self.process.state() != QProcess.NotRunning:
            self.process.kill()
            self.process.waitForFinished(1000)
        super().closeEvent(event)


def run_gui() -> int:
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    window = MainWindow()
    window.show()
    return app.exec()
