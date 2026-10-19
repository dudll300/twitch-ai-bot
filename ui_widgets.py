"""Small layout primitives shared by the desktop pages."""

import re

from PySide6.QtCore import Qt, QSize, QVariantAnimation, QEasingCurve, QTimer, QRectF, QPointF, QPropertyAnimation, QEvent
from PySide6.QtGui import (QColor, QIcon, QPainter, QPen, QPixmap, QPalette, QTransform,
                          QTextLayout, QTextOption)
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                              QFrame, QGridLayout, QHBoxLayout, QLabel, QListView, QListWidget,
                              QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QScrollBar,
                              QStyle, QStyleOptionComboBox, QVBoxLayout, QWidget,
                              QSplitter, QStackedWidget, QSizePolicy, QStyleOptionButton,
                              QStylePainter, QStyledItemDelegate)
from studio_controls import StudioScrollBar, OrbitScrollArea, colors, mix, animate


class StudioButton(QPushButton):
    """Native button semantics with wrapping captions and a short hover response."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._hover = 0.0
        self._hover_animation = QVariantAnimation(self)
        self._hover_animation.setDuration(180)
        self._hover_animation.setEasingCurve(QEasingCurve.OutCubic)
        self._hover_animation.valueChanged.connect(self._advance_hover)
        self._selected = float(self.isChecked())
        self._selection_animation = QVariantAnimation(self)
        self._selection_animation.setDuration(260)
        self._selection_animation.setEasingCurve(QEasingCurve.OutCubic)
        self._selection_animation.valueChanged.connect(self._advance_selection)
        self.toggled.connect(lambda checked: animate(self._selection_animation, self._selected, float(checked)))
        self._keyboard_focus = False
        policy = self.sizePolicy()
        policy.setHorizontalPolicy(QSizePolicy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def _advance_hover(self, value):
        self._hover = float(value)
        self.update()

    def _advance_selection(self, value):
        self._selected = float(value)
        self.update()

    def focusInEvent(self, event):
        self._keyboard_focus = event.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason)
        super().focusInEvent(event)

    def mousePressEvent(self, event):
        self._keyboard_focus = False
        super().mousePressEvent(event)

    def _animate_hover(self, target):
        self._hover_animation.stop()
        if QApplication.instance().property("animations") is False:
            self._advance_hover(target)
            return
        self._hover_animation.setStartValue(self._hover)
        self._hover_animation.setEndValue(target)
        self._hover_animation.start()

    def enterEvent(self, event):
        self._animate_hover(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._animate_hover(0.0)
        super().leaveEvent(event)

    def _text_layout(self, width):
        caption = self.text().replace("&&", "\0").replace("&", "").replace("\0", "&")
        layout = QTextLayout(caption, self.font())
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        option.setAlignment(Qt.AlignLeft)
        layout.setTextOption(option)
        layout.beginLayout()
        height = 0.0
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(max(8, width))
            line.setPosition(QPointF(0, height))
            height += line.height()
        layout.endLayout()
        return layout, height

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        limit = 62 if self.property("variant") == "chip" else 112
        return QSize(min(hint.width(), limit) if self.text() else hint.width(), max(42, hint.height()))

    def sizeHint(self):
        hint = super().sizeHint()
        caption = self.text().replace("&&", "\0").replace("&", "").replace("\0", "&")
        text = max((self.fontMetrics().horizontalAdvance(line) for line in caption.splitlines()), default=0)
        icon = self.iconSize().width() + (9 if caption else 0) if not self.icon().isNull() else 0
        return QSize(max(hint.width(), text + icon + 38), max(44, hint.height()))

    def heightForWidth(self, width):
        icon = self.iconSize().width() + 9 if not self.icon().isNull() else 0
        _, height = self._text_layout(width - 34 - icon)
        return max(super().sizeHint().height(), round(height + 20), 42)

    def paintEvent(self, event):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        icon = QIcon(self.icon())
        option.text = ""
        option.icon = QIcon()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        c, variant, hover = colors(), self.property("variant"), self._hover if self.isEnabled() else 0.
        primary = self.objectName() == "primary" or variant == "primary"
        foreground = QColor(c["text"])
        border = QColor(c["border"])
        fill = mix(c["input"], c["raised"], hover)
        radius = 22
        if primary:
            fill = mix(c["primary"], c["primaryHover"], hover)
            foreground, border = QColor(c["primaryText"]), QColor(c["primary"])
        elif variant == "danger":
            fill, foreground = QColor(c["errorbg"]), QColor(c["error"])
        elif variant in ("nav", "quiet", "chip"):
            fill = QColor(c["selected"])
            fill.setAlphaF(min(1., self._selected * .92 + hover * .22))
            foreground = mix(c["muted"], c["accent"], max(self._selected, hover))
            if variant != "chip":
                border = QColor(Qt.transparent)
            radius = 16 if variant == "nav" else 20
        if self.isDown():
            fill = mix(fill, c["selected"], .2)
        if not self.isEnabled():
            foreground = QColor(c["muted"])
            fill = QColor(c["surface"])
        rect = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        painter.setBrush(fill)
        painter.setPen(QPen(border, 1))
        painter.drawRoundedRect(rect, radius, radius)
        if self.hasFocus() and self._keyboard_focus:
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(c["accent"]), 1.4))
            painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), radius - 2, radius - 2)
        icon_width = self.iconSize().width() if not icon.isNull() else 0
        gap = 9 if icon_width and self.text() else 0
        available = max(8, self.width() - 34 - icon_width - gap)
        layout, text_height = self._text_layout(available)
        text_width = max((layout.lineAt(i).naturalTextWidth() for i in range(layout.lineCount())), default=0)
        total = icon_width + gap + min(available, text_width)
        left = 17 if self.property("variant") in ("nav", "quiet") and self.text() else max(5, (self.width() - total) / 2)
        if icon_width:
            icon.paint(painter, round(left), (self.height() - self.iconSize().height()) // 2,
                       icon_width, self.iconSize().height(), Qt.AlignCenter,
                       QIcon.Normal if self.isEnabled() else QIcon.Disabled)
        painter.setPen(foreground)
        layout.draw(painter, QPointF(left + icon_width + gap, max(2, (self.height() - text_height) / 2)))


# Internal shared widgets use the same button as the editor modules.
QPushButton = StudioButton


class ComboItemDelegate(QStyledItemDelegate):
    """Opaque, padded popup rows; retain QComboBox's native keyboard behavior."""

    def __init__(self, combo):
        super().__init__(combo)
        self.combo = combo

    def sizeHint(self, option, index):
        return QSize(super().sizeHint(option, index).width() + 48, max(44, option.fontMetrics.height() + 22))

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        selected = index.row() == self.combo.currentIndex()
        hovered = bool(option.state & (QStyle.State_Selected | QStyle.State_MouseOver))
        rect = option.rect.adjusted(4, 2, -4, -2)
        if selected or hovered:
            painter.setPen(Qt.NoPen)
            painter.setBrush(option.palette.color(QPalette.Highlight if selected else QPalette.AlternateBase))
            painter.drawRoundedRect(rect, 11, 11)
        color = option.palette.color(QPalette.Text)
        if not index.flags() & Qt.ItemIsEnabled:
            color = option.palette.color(QPalette.Disabled, QPalette.Text)
        painter.setPen(color)
        painter.setFont(option.font)
        text_rect = rect.adjusted(12, 0, -34, 0)
        text = option.fontMetrics.elidedText(str(index.data() or ""), Qt.ElideMiddle, text_rect.width())
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, text)
        if selected:
            painter.setPen(QPen(option.palette.color(QPalette.Link), 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            x, y = rect.right() - 19, rect.center().y()
            painter.drawLine(x - 4, y, x - 1, y + 3)
            painter.drawLine(x - 1, y + 3, x + 5, y - 3)
        painter.restore()


class StudioLinkLabel(QLabel):
    """Keep rich-text links readable when the native palette changes."""

    def setText(self, text):
        self._source_text = text
        self._render_link()

    def _render_link(self):
        color = self.palette().color(QPalette.Link).name()
        super().setText('<style>a { color: ' + color + '; }</style>' + self._source_text)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.PaletteChange, QEvent.ApplicationPaletteChange) and hasattr(self, "_source_text"):
            self._render_link()


class ContainedScrollBar(StudioScrollBar):
    def wheelEvent(self, event):
        super().wheelEvent(event)
        event.accept()


class ContainedWheel:
    """Keep wheel input in a nested scroller, including at either boundary."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setVerticalScrollBar(ContainedScrollBar(Qt.Vertical, self))
        self.setHorizontalScrollBar(ContainedScrollBar(Qt.Horizontal, self))
        self.setHorizontalScrollBar(ContainedScrollBar(Qt.Horizontal, self))

    def wheelEvent(self, event):
        super().wheelEvent(event)
        event.accept()


class ScrollListView(ContainedWheel, QListView):
    pass


class ScrollListWidget(ContainedWheel, QListWidget):
    pass


class TextScrollBar(StudioScrollBar):
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
        self.setItemDelegate(ComboItemDelegate(self))
        self.view().setObjectName("orbitComboPopup")
        self.view().setSpacing(2)
        self.setMaxVisibleItems(10)
        self.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(8)
        self._popup_animation = None

    def showPopup(self):
        view = self.view()
        screen = self.screen().availableGeometry()
        view.setMinimumWidth(min(max(self.width(), 220), max(120, screen.width() - 24)))
        super().showPopup()
        popup = view.window()
        popup.setObjectName("orbitComboContainer")
        popup.setAttribute(Qt.WA_StyledBackground, True)
        popup.style().unpolish(popup)
        popup.style().polish(popup)
        # Qt owns placement, scrolling, accessibility and focus. Only fade its
        # popup window; never delay the selection or animate model changes.
        if QApplication.instance().property("animations") is not False and QApplication.platformName() != "offscreen":
            if self._popup_animation:
                self._popup_animation.stop()
            if self._popup_animation is None:
                self._popup_animation = QPropertyAnimation(popup, b"windowOpacity", self)
            else:
                self._popup_animation.setTargetObject(popup)
            self._popup_animation.setDuration(160)
            self._popup_animation.setEasingCurve(QEasingCurve.OutCubic)
            self._popup_animation.setStartValue(.65)
            self._popup_animation.setEndValue(1.0)
            self._popup_animation.start()

    def hidePopup(self):
        if self._popup_animation:
            self._popup_animation.stop()
            self.view().window().setWindowOpacity(1.0)
        super().hidePopup()

    def wheelEvent(self, event):
        event.ignore()

    def paintEvent(self, event):
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        if self.isEditable():
            super().paintEvent(event)
        else:
            field = self.style().subControlRect(QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxEditField, self)
            option.currentText = self.fontMetrics().elidedText(self.currentText(), Qt.ElideMiddle, max(8, field.width() - 6))
            style_painter = QStylePainter(self)
            style_painter.drawComplexControl(QStyle.CC_ComboBox, option)
            style_painter.drawControl(QStyle.CE_ComboBoxLabel, option)
            style_painter.end()
        center = self.style().subControlRect(QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxArrow, self).center()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(self.palette().color(QPalette.Text), 1.5))
        painter.drawLine(center.x() - 4, center.y() - 2, center.x(), center.y() + 2)
        painter.drawLine(center.x(), center.y() + 2, center.x() + 4, center.y() - 2)


class ReadingText(ScrollPlainTextEdit):
    """Selectable prose, without an input border or a large empty editor box."""
    def __init__(self, name, *, emphasis=False, maximum=380):
        super().__init__()
        self.setReadOnly(True)
        self.setProperty("presentation", "reading")
        self.setProperty("emphasis", emphasis)
        self.setAccessibleName(name)
        self._maximum_reading_height = maximum
        self.setMinimumHeight(44)
        self.textChanged.connect(self._fit_prose)

    def _fit_prose(self):
        width = max(100, self.viewport().width() - 6)
        rect = self.fontMetrics().boundingRect(0, 0, width, 100000, Qt.TextWordWrap, self.toPlainText() or " ")
        self.setFixedHeight(max(44, min(self._maximum_reading_height, rect.height() + 12)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_prose()


def panel_badge(layout, badge):
    heading = layout.outer.itemAt(0).widget().layout()
    heading.removeWidget(layout.title_label)
    row = QHBoxLayout()
    row.addWidget(layout.title_label, 1)
    row.addWidget(badge, 0, Qt.AlignVCenter)
    heading.insertLayout(0, row)


class SegmentedControl(QWidget):
    """Show a small existing mode combo as explicit, animated tab buttons."""
    def __init__(self, combo):
        super().__init__()
        self.combo, self.buttons = combo, []
        combo.setParent(self)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        for index in range(combo.count()):
            button = StudioButton(combo.itemText(index))
            button.setProperty("variant", "chip")
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, value=index: combo.setCurrentIndex(value))
            row.addWidget(button)
            self.buttons.append(button)
        row.addStretch()
        combo.hide()
        combo.currentIndexChanged.connect(self._sync)
        self._sync()

    def _sync(self, *_):
        for index, button in enumerate(self.buttons):
            button.setChecked(index == self.combo.currentIndex())


def menu_icon():
    pixmap = QPixmap(48, 48)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QApplication.palette().color(QPalette.WindowText), 2)
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
        self._animation.setDuration(240)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)
        self._animation.valueChanged.connect(self._advance)
        self.toggled.connect(self._animate)

    def _advance(self, value):
        self._position = value
        self.update()

    def _animate(self, checked):
        self._animation.stop()
        if QApplication.instance().property("animations") is False:
            self._advance(1.0 if checked else 0.0)
            return
        self._animation.setStartValue(self._position)
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()


    def sizeHint(self):
        return QSize(58 + self.fontMetrics().horizontalAdvance(self.text()), 36)

    def hitButton(self, point):
        return self.rect().contains(point)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setOpacity(1 if self.isEnabled() else 0.45)
        y = (self.height() - 24) // 2
        c = colors()
        painter.setPen(QPen(mix(c["border"], c["primary"], self._position), 1))
        painter.setBrush(mix(c["raised"], c["primary"], self._position))
        painter.drawRoundedRect(QRectF(1, y, 42, 24), 12, 12)
        painter.setPen(Qt.NoPen)
        painter.setBrush(mix(c["muted"], c["primaryText"], self._position))
        painter.drawEllipse(QRectF(5 + 17 * self._position, y + 4, 16, 16))
        painter.setPen(self.palette().color(QPalette.WindowText))
        painter.drawText(self.rect().adjusted(55, 0, 0, 0), Qt.AlignVCenter, self.text())
        if self.hasFocus():
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(self.palette().color(QPalette.Link), 1))
            painter.drawRoundedRect(QRectF(0, y - 2, 46, 28), 14, 14)


def label(text: str, role: str = "body", wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.PlainText)
    widget.setProperty("role", role)
    widget.setWordWrap(wrap or role in ("section", "caption"))
    return widget


class PanelLayout(QVBoxLayout):
    """Keep the existing editor API while giving every card a real header."""
    def __init__(self, frame, title, description):
        self.outer = QVBoxLayout(frame)
        self.outer.setContentsMargins(0, 0, 0, 0)
        self.outer.setSpacing(0)
        heading = QFrame()
        heading.setObjectName("panelHeading")
        row = QVBoxLayout(heading)
        row.setContentsMargins(22, 18, 22, 18)
        row.setSpacing(6)
        self.title_label = label(title, "section")
        row.addWidget(self.title_label)
        if description:
            row.addWidget(label(description, "muted", True))
        heading.setVisible(bool(title or description))
        self.outer.addWidget(heading)
        self.body = QWidget()
        super().__init__(self.body)
        self.outer.addWidget(self.body, 1)

def card(title: str = "", description: str = "") -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    layout = PanelLayout(frame, title, description)
    layout.setContentsMargins(22, 22, 22, 22)
    layout.setSpacing(14)
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
    match = re.search(r"до (\d+) символ", title)
    if match and hasattr(widget, "toPlainText") and not title.startswith("Алиасы"):
        counter = label("", "muted")
        def update_count():
            counter.setText(f"{len(widget.toPlainText())} / {match.group(1)} символов")
        widget.textChanged.connect(update_count)
        update_count()
        layout.addWidget(counter)
    error = label("", "error", True)
    error.hide()
    widget._field_error = error
    layout.addWidget(error)
    signal = getattr(widget, "textChanged", None) or getattr(widget, "currentTextChanged", None)
    if signal is not None:
        signal.connect(lambda *_: error.hide())
    return container


def field_error(widget, message):
    error = getattr(widget, "_field_error", None)
    if error is not None:
        error.setText(message)
        error.setVisible(bool(message))
    widget.setAccessibleDescription(message)
    widget.setFocus()
    ancestor = widget.parentWidget()
    while ancestor is not None:
        if isinstance(ancestor, QScrollArea):
            QTimer.singleShot(0, lambda area=ancestor, target=widget: area.ensureWidgetVisible(target))
        ancestor = ancestor.parentWidget()


class Disclosure(QWidget):
    """Keyboard-accessible secondary content; collapsed by default."""
    def __init__(self, title, content, *, expanded=False):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.button = QPushButton()
        self.button.setProperty("variant", "quiet")
        self.button.setCheckable(True)
        self.button.setAccessibleName(title)
        self.content = content
        self.title = title
        layout.addWidget(self.button)
        layout.addWidget(content)
        self.button.toggled.connect(self.set_expanded)
        QApplication.instance().paletteChanged.connect(self._update_icon)
        self.button.setChecked(expanded)
        self.set_expanded(expanded)

    def set_expanded(self, expanded):
        self.content.setVisible(expanded)
        self.button.setText(self.title)
        self._update_icon()
        self.button.setAccessibleDescription("Развёрнуто" if expanded else "Свёрнуто")

    def _update_icon(self, *_):
        from studio_icons import studio_icon
        icon = studio_icon("arrow-right", QApplication.palette().color(QPalette.WindowText).name(), 16)
        if self.button.isChecked():
            icon = QIcon(icon.pixmap(16, 16).transformed(QTransform().rotate(90)))
        self.button.setIcon(icon)


class ResponsiveColumns(QWidget):
    """Reflow the same widgets; retain their edits and focus across resize."""
    def __init__(self, first, second, threshold=900, ratios=(1, 1)):
        super().__init__()
        self.first, self.second, self.threshold = first, second, threshold
        self.ratios = ratios
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(16)
        self._wide = None
        self.reflow(False)

    def reflow(self, wide):
        if wide == self._wide:
            return
        self._wide = wide
        self.grid.removeWidget(self.first)
        self.grid.removeWidget(self.second)
        self.grid.addWidget(self.first, 0, 0)
        self.grid.addWidget(self.second, 0 if wide else 1, 1 if wide else 0)
        self.grid.setColumnStretch(0, self.ratios[0])
        self.grid.setColumnStretch(1, self.ratios[1] if wide else 0)

    def resizeEvent(self, event):
        self.reflow(event.size().width() >= self.threshold)
        super().resizeEvent(event)

    def minimumSizeHint(self):
        return QSize(0, 0)


class MasterDetail(QWidget):
    """Resizable list/detail on desktop, explicit Back navigation on small windows."""
    def __init__(self, first, second, threshold=760):
        super().__init__()
        self.first, self.second, self.threshold = first, second, threshold
        first.setMinimumWidth(0)
        first.setMaximumWidth(16777215)
        second.setMinimumWidth(0)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setHandleWidth(20)
        self.splitter.setChildrenCollapsible(False)
        self.stack = QStackedWidget()
        self.detail = QWidget()
        detail = QVBoxLayout(self.detail)
        detail.setContentsMargins(0, 0, 0, 0)
        self.back = QPushButton("← Назад к списку")
        self.back.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        detail.addWidget(self.back)
        detail.addWidget(second, 1)
        self._layout.addWidget(self.splitter, 1)
        self._layout.addWidget(self.stack, 1)
        self._small = None
        self._wide_sizes = [280, 560]
        self.reflow(False)

    def reflow(self, small):
        if small == self._small:
            return
        self._small = small
        if small:
            sizes = self.splitter.sizes()
            if len(sizes) == 2 and all(sizes):
                self._wide_sizes = sizes
            self.first.setParent(None)
            self.detail.setParent(None)
            self.stack.addWidget(self.first)
            self.stack.addWidget(self.detail)
            self.stack.setCurrentIndex(0)
        else:
            self.stack.removeWidget(self.first)
            self.stack.removeWidget(self.detail)
            self.splitter.addWidget(self.first)
            self.splitter.addWidget(self.detail)
            self.splitter.setStretchFactor(0, 1)
            self.splitter.setStretchFactor(1, 2)
            self.splitter.setSizes(self._wide_sizes)
            self.first.show()
            self.detail.show()
        self.back.setVisible(small)
        self.stack.setVisible(small)
        self.splitter.setVisible(not small)

    def show_detail(self):
        if self._small:
            self.stack.setCurrentIndex(1)

    def restore_sizes(self, sizes):
        self._wide_sizes = sizes
        if not self._small:
            self.splitter.setSizes(sizes)

    def saved_sizes(self):
        return self._wide_sizes if self._small else self.splitter.sizes()

    def resizeEvent(self, event):
        self.reflow(event.size().width() < self.threshold)
        super().resizeEvent(event)

    def minimumSizeHint(self):
        return QSize(0, 0)

    def sizeHint(self):
        return QSize(840, 460)


def scroll_page(content: QWidget) -> QScrollArea:
    scroll = OrbitScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setWidget(content)
    return scroll
