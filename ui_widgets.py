"""Small layout primitives shared by the desktop pages."""

from PySide6.QtCore import Qt, QSize, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                              QFrame, QHBoxLayout, QLabel, QListView, QListWidget,
                              QMessageBox, QPlainTextEdit, QScrollArea, QScrollBar,
                              QStyle, QStyleOptionComboBox, QVBoxLayout, QWidget)


class ContainedScrollBar(QScrollBar):
    def wheelEvent(self, event):
        super().wheelEvent(event)
        event.accept()


class ContainedWheel:
    """Keep wheel input in a nested scroller, including at either boundary."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setVerticalScrollBar(ContainedScrollBar(Qt.Vertical, self))
        self.setHorizontalScrollBar(ContainedScrollBar(Qt.Horizontal, self))

    def wheelEvent(self, event):
        super().wheelEvent(event)
        event.accept()


class ScrollListView(ContainedWheel, QListView):
    pass


class ScrollListWidget(ContainedWheel, QListWidget):
    pass


class TextScrollBar(QScrollBar):
    """Use the same boundary handoff for a text editor's scrollbar and viewport."""

    def __init__(self, orientation, editor):
        super().__init__(orientation, editor)
        self._editor = editor

    def wheelEvent(self, event):
        if self._editor._forward_wheel_at_boundary(event):
            return
        super().wheelEvent(event)
        event.accept()


class ScrollPlainTextEdit(QPlainTextEdit):
    """Scroll text first, then continue through the page on the next wheel step."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setVerticalScrollBar(TextScrollBar(Qt.Vertical, self))
        self.setHorizontalScrollBar(ContainedScrollBar(Qt.Horizontal, self))

    def _forward_wheel_at_boundary(self, event):
        delta = event.pixelDelta().y() or event.angleDelta().y()
        # Horizontal gestures and zero-delta phase events belong to the editor.
        if not delta or event.modifiers() & Qt.ShiftModifier:
            return False
        bar = self.verticalScrollBar()
        at_boundary = (bar.value() <= bar.minimum() if delta > 0
                       else bar.value() >= bar.maximum())
        if not at_boundary:
            return False
        parent = self.parentWidget()
        while parent is not None:
            if isinstance(parent, QScrollArea):
                QApplication.sendEvent(parent.viewport(), event)
                event.accept()
                return True
            parent = parent.parentWidget()
        return False

    def wheelEvent(self, event):
        if self._forward_wheel_at_boundary(event):
            return
        super().wheelEvent(event)
        # Even if this step reaches the edge, do not scroll the page as well.
        event.accept()


class NoWheelComboBox(QComboBox):
    """Ignore wheel edits so scrolling a page cannot change a selected value."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setView(ScrollListView(self))
        self.setMaxVisibleItems(15)

    def wheelEvent(self, event):
        event.ignore()

    def paintEvent(self, event):
        super().paintEvent(event)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        center = self.style().subControlRect(QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxArrow, self).center()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("#d5d8de" if self.isEnabled() else "#737781"), 1.5))
        painter.drawLine(center.x() - 4, center.y() - 2, center.x(), center.y() + 2)
        painter.drawLine(center.x(), center.y() + 2, center.x() + 4, center.y() - 2)


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


class StudioQuestionDialog(QDialog):
    """Studio confirmation card with the usual QMessageBox result values."""

    def __init__(self, parent, title, text, buttons, default):
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)
        self.setObjectName("studioQuestion")
        self.setWindowTitle(title)
        self.setWindowModality(Qt.WindowModal if parent is not None else Qt.ApplicationModal)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAccessibleName(title)
        self.setAccessibleDescription(text)
        self.setMinimumWidth(480)
        self.setMaximumWidth(560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        self.card = QFrame()
        self.card.setObjectName("questionCard")
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(28, 26, 28, 26)
        card_layout.setSpacing(20)
        heading = QHBoxLayout()
        heading.setSpacing(14)
        mark = label("?", "body")
        mark.setObjectName("questionMark")
        mark.setAlignment(Qt.AlignCenter)
        mark.setFixedSize(42, 42)
        self.title_label = label(title, "section", True)
        self.title_label.setObjectName("questionTitle")
        heading.addWidget(mark)
        heading.addWidget(self.title_label, 1)
        card_layout.addLayout(heading)
        self.text_label = label(text, "body", True)
        self.text_label.setObjectName("questionText")
        self.text_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        card_layout.addWidget(self.text_label)
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton(buttons.value))
        self.button_box.setCenterButtons(False)
        captions = {QMessageBox.Save: "Сохранить", QMessageBox.Discard: "Не сохранять",
                    QMessageBox.Cancel: "Отмена", QMessageBox.Yes: "Да", QMessageBox.No: "Нет"}
        self._default = default
        self._escape = (QMessageBox.Cancel if self.button(QMessageBox.Cancel) is not None
                        else QMessageBox.No if self.button(QMessageBox.No) is not None
                        else QMessageBox.Cancel)
        for standard, caption in captions.items():
            button = self.button(standard)
            if button is None:
                continue
            button.setText(caption)
            button.setAccessibleName(caption)
            button.setAutoDefault(False)
            if standard in (QMessageBox.Save, QMessageBox.Yes):
                button.setObjectName("primary")
            elif standard == QMessageBox.Discard:
                button.setProperty("variant", "danger")
            else:
                button.setProperty("variant", "quiet")
            button.clicked.connect(lambda _checked=False, result=standard: self.done(result.value))
        self.defaultButton().setDefault(True)
        self.defaultButton().setFocus()
        card_layout.addWidget(self.button_box)
        layout.addWidget(self.card)

    def button(self, standard):
        return self.button_box.button(QDialogButtonBox.StandardButton(standard.value))

    def defaultButton(self):
        return self.button(self._default)

    def reject(self):
        self.done(self._escape.value)

    def showEvent(self, event):
        super().showEvent(event)
        parent = self.parentWidget()
        screen = parent.screen() if parent is not None else self.screen()
        available = screen.availableGeometry()
        center = parent.frameGeometry().center() if parent is not None else available.center()
        x = max(available.left(), min(center.x() - self.width() // 2,
                                     available.right() - self.width() + 1))
        y = max(available.top(), min(center.y() - self.height() // 2,
                                    available.bottom() - self.height() + 1))
        self.move(x, y)
        self.defaultButton().setFocus()


def russian_question(parent, title, text, buttons, default):
    return StudioQuestionDialog(parent, title, text, buttons, default).exec()


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
        painter.setBrush(QColor("#168BFF" if self.isChecked() else "#353B48"))
        painter.drawRoundedRect(0, y, 34, 20, 10, 10)
        painter.setBrush(QColor("#FFFFFF" if self.isChecked() else "#A9B1C1"))
        painter.drawEllipse(round(3 + 13 * self._position), y + 3, 14, 14)
        painter.setPen(QColor("#F3F5FA"))
        painter.drawText(self.rect().adjusted(44, 0, 0, 0), Qt.AlignVCenter, self.text())
        if self.hasFocus():
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor("#65B3FF"), 1, Qt.DotLine))
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
    layout.setContentsMargins(22, 22, 22, 22)
    layout.setSpacing(14)
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
