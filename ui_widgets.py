"""Small layout primitives shared by the desktop pages."""

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QCheckBox, QFrame, QLabel, QScrollArea, QVBoxLayout, QWidget


class ToggleSwitch(QCheckBox):
    """Keyboard-accessible checkbox with a compact switch indicator."""

    def sizeHint(self):
        return QSize(46 + self.fontMetrics().horizontalAdvance(self.text()), 30)

    def hitButton(self, point):
        return self.rect().contains(point)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setOpacity(1 if self.isEnabled() else 0.45)
        y = (self.height() - 20) // 2
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#d0d5de" if self.isChecked() else "#4b515d"))
        painter.drawRoundedRect(0, y, 34, 20, 10, 10)
        painter.setBrush(QColor("#242830" if self.isChecked() else "#b8bfcb"))
        painter.drawEllipse(16 if self.isChecked() else 3, y + 3, 14, 14)
        painter.setPen(QColor("#c5c9d1"))
        painter.drawText(self.rect().adjusted(44, 0, 0, 0), Qt.AlignVCenter, self.text())
        if self.hasFocus():
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor("#c5c9d1"), 1, Qt.DotLine))
            painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 6, 6)


def label(text: str, role: str = "body", wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.PlainText)
    widget.setProperty("role", role)
    widget.setWordWrap(wrap)
    return widget


def card(title: str = "", description: str = "") -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(24, 24, 24, 24)
    layout.setSpacing(16)
    if title:
        layout.addWidget(label(title, "section"))
    if description:
        layout.addWidget(label(description, "muted", True))
    return frame, layout


def field(title: str, widget: QWidget, hint: str = "") -> QWidget:
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    caption = label(title, "caption")
    caption.setBuddy(widget)
    widget.setAccessibleName(title)
    layout.addWidget(caption)
    layout.addWidget(widget)
    if hint:
        layout.addWidget(label(hint, "muted", True))
    return container


def scroll_page(content: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setWidget(content)
    return scroll
