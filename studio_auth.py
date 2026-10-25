"""Theme-aware Twitch confirmation card; links are allowlisted, text is plain."""
from PySide6.QtCore import QUrl, QTimer
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from runtime_events import activation_url
from studio_icons import studio_icon
from ui_widgets import StudioButton, card, label


class AuthNotificationBanner(QWidget):
    def __init__(self):
        super().__init__()
        self._signature, self._code, self._url = None, "", ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        panel, body = card("Подтвердите вход в Twitch")
        self.info = label("", "caption", True)
        self.hint = label("", "muted", True)
        self.code = QLineEdit()
        self.code.setReadOnly(True)
        self.code.setAccessibleName("Код подтверждения Twitch")
        self.code.setMaximumWidth(260)
        self.copy_button = StudioButton("Скопировать код")
        self.copy_button.setIcon(studio_icon("copy", size=16))
        self.copy_button.setMinimumWidth(170)
        self.copy_button.clicked.connect(self._copy)
        self.open_button = StudioButton("Открыть Twitch")
        self.open_button.setProperty("variant", "primary")
        self.open_button.clicked.connect(self._open)
        self.controls = QWidget()
        row = QHBoxLayout(self.controls)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        row.addWidget(self.code, 1)
        row.addWidget(self.copy_button)
        row.addWidget(self.open_button)
        body.addWidget(self.info)
        body.addWidget(self.controls)
        body.addWidget(self.hint)
        layout.addWidget(panel)
        self._expiry = QTimer(self)
        self._expiry.setSingleShot(True)
        self._expiry.timeout.connect(lambda: self.show_error("expired"))
        self.reset()

    def show_auth(self, auth):
        signature = tuple(auth.get(key) for key in ("account", "role", "url", "code", "expires_in"))
        if signature == self._signature:
            return False  # Preserve copied/expired state on unrelated events.
        self._signature = signature
        self._expiry.stop()
        self._code = str(auth.get("code", ""))
        self._url = activation_url(auth.get("url", ""))
        role = "владельца канала" if auth.get("role") == "broadcaster" else "бота"
        account = str(auth.get("account", ""))
        self.info.setText(f"Войдите под аккаунтом {role}: {account}")
        self.code.setText(self._code)
        self.copy_button.setText("Скопировать код")
        self.copy_button.setEnabled(bool(self._code))
        self.open_button.setEnabled(bool(self._url))
        expires = auth.get("expires_in")
        text = "Откройте Twitch, проверьте аккаунт и введите код."
        if type(expires) is int and expires > 0:
            seconds = min(expires, 86400)
            text += f" Срок действия кода: {max(1, (seconds + 59) // 60)} мин."
            self._expiry.start(seconds * 1000)
        self.hint.setText(text)
        self.controls.show()
        self.show()
        return True

    def show_error(self, reason):
        self._expiry.stop()
        self._code = self._url = ""
        self.code.clear()
        self.copy_button.setEnabled(False)
        self.open_button.setEnabled(False)
        messages = {
            "expired": "Срок действия кода истёк. Запустите бота снова, чтобы получить новый.",
            "wrong_account": "Выбран другой аккаунт Twitch. Повторите вход под указанным аккаунтом.",
        }
        self.info.setText("Не удалось подтвердить вход в Twitch")
        self.hint.setText(messages.get(reason, "Авторизация не завершилась. Запустите бота снова."))
        self.controls.hide()
        self.show()

    def reset(self):
        self._expiry.stop()
        self._signature, self._code, self._url = None, "", ""
        self.code.clear()
        self.copy_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.hide()

    def _copy(self):
        if self._code:
            QApplication.clipboard().setText(self._code)
            self.copy_button.setText("Скопировано")

    def _open(self):
        if self._url:
            QDesktopServices.openUrl(QUrl(self._url))
