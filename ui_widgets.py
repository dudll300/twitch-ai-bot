"""Small layout primitives shared by the desktop pages."""

from PySide6.QtCore import Qt, QSize, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QCheckBox, QFrame, QLabel, QMessageBox, QScrollArea, QVBoxLayout, QWidget


def menu_icon():
    pixmap = QPixmap(48, 48)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor("#d5d8df"), 2)
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    for y in (6, 12, 18):
        painter.drawLine(4, y, 20, y)
    painter.end()
    return QIcon(pixmap)


class SlidingSidebar(QWidget):
    """Clip a fixed-width panel while its enclosing column animates."""

    def __init__(self, width=200):
        super().__init__()
        self.panel_width = width
        self.panel = QFrame(self)
        self.panel.setObjectName("sidebar")
        self.panel.setFixedWidth(width)
        self.setFixedWidth(width)
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(200)
        self.animation.setEasingCurve(QEasingCurve.InOutCubic)
        self.animation.valueChanged.connect(lambda value: self.setFixedWidth(round(value)))
        self.animation.finished.connect(lambda: self.panel.setVisible(self.width() > 0))

    def set_expanded(self, expanded, animate=True):
        self.animation.stop()
        self.panel.show()
        target = self.panel_width if expanded else 0
        if animate:
            self.animation.setStartValue(self.width())
            self.animation.setEndValue(target)
            self.animation.start()
        else:
            self.setFixedWidth(target)
            self.panel.setVisible(expanded)

    def resizeEvent(self, event):
        self.panel.setFixedHeight(event.size().height())
        super().resizeEvent(event)


def russian_question(parent, title, text, buttons, default):
    dialog = QMessageBox(parent)
    dialog.setWindowTitle(title)
    dialog.setText(text)
    dialog.setIcon(QMessageBox.Question)
    dialog.setStandardButtons(buttons)
    captions = {QMessageBox.Save: "Сохранить", QMessageBox.Discard: "Не сохранять",
                QMessageBox.Cancel: "Отмена", QMessageBox.Yes: "Да", QMessageBox.No: "Нет"}
    for standard, caption in captions.items():
        button = dialog.button(standard)
        if button is not None:
            button.setText(caption)
    dialog.setDefaultButton(default)
    return dialog.exec()


class ToggleSwitch(QCheckBox):
    """Keyboard-accessible checkbox with a compact switch indicator."""

    def __init__(self, text=""):
        super().__init__(text)
        self._position = 0.0
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(150)
        self._animation.setEasingCurve(QEasingCurve.InOutCubic)
        self._animation.valueChanged.connect(self._advance)
        self.toggled.connect(self._animate)

    def _advance(self, value):
        self._position = value
        self.update()

    def _animate(self, checked):
        self._animation.stop()
        self._animation.setStartValue(self._position)
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()


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
        painter.drawEllipse(round(3 + 13 * self._position), y + 3, 14, 14)
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
    widget.setWordWrap(wrap or role in ("section", "caption"))
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
