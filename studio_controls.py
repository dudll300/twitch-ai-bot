"""Orbit's native controls, input policy and finite motion. No service calls."""
from PySide6.QtCore import Qt, QRectF, QSize, QVariantAnimation, QEasingCurve, QPointF
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath
from PySide6.QtWidgets import (QApplication, QAbstractSpinBox, QCheckBox, QScrollBar, QScrollArea,
    QSpinBox, QStyle, QStyleOptionSlider, QStyleOptionSpinBox, QTabBar, QTabWidget, QStackedWidget,
    )


def colors():
    from studio_theme import PALETTES
    return PALETTES[QApplication.instance().property("studioTheme") or "dark"]


def mix(a, b, amount):
    a, b = QColor(a), QColor(b)
    return QColor.fromRgbF(*(x + (y - x) * amount for x, y in zip(a.getRgbF(), b.getRgbF())))


def animate(animation, start, end):
    animation.stop()
    animation.setStartValue(start)
    animation.setEndValue(end)
    if QApplication.instance().property("animations") is False:
        animation.setCurrentTime(animation.duration())
    else:
        animation.start()


class StudioScrollBar(QScrollBar):
    """A slim floating thumb; dragging, paging and accessibility remain native."""
    def __init__(self, *args):
        super().__init__(*args)
        self._hover = 0.
        self._motion = QVariantAnimation(self)
        self._motion.setDuration(200)
        self._motion.setEasingCurve(QEasingCurve.OutCubic)
        self._motion.valueChanged.connect(self._advance)

    def _advance(self, value):
        self._hover = float(value)
        self.update()

    def enterEvent(self, event):
        animate(self._motion, self._hover, 1.)
        super().enterEvent(event)

    def leaveEvent(self, event):
        animate(self._motion, self._hover, 0.)
        super().leaveEvent(event)

    def paintEvent(self, event):
        if self.maximum() <= self.minimum():
            return
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        rect = QRectF(self.style().subControlRect(QStyle.CC_ScrollBar, option, QStyle.SC_ScrollBarSlider, self))
        thickness = 4. + 2. * self._hover
        if self.orientation() == Qt.Vertical:
            rect.setLeft((self.width() - thickness) / 2)
            rect.setWidth(thickness)
        else:
            rect.setTop((self.height() - thickness) / 2)
            rect.setHeight(thickness)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(mix(colors()["border"], colors()["muted"], .22 + .35 * self._hover))
        p.drawRoundedRect(rect, thickness / 2, thickness / 2)


class OrbitScrollArea(QScrollArea):
    def __init__(self):
        super().__init__()
        self.setVerticalScrollBar(StudioScrollBar(Qt.Vertical, self))
        self.setHorizontalScrollBar(StudioScrollBar(Qt.Horizontal, self))
        self._setting = False
        self._target = 0
        self._scroll = QVariantAnimation(self)
        self._scroll.setDuration(180)
        self._scroll.setEasingCurve(QEasingCurve.OutCubic)
        self._scroll.valueChanged.connect(self._step)
        self.verticalScrollBar().valueChanged.connect(self._external_scroll)
        self.verticalScrollBar().sliderPressed.connect(self._scroll.stop)

    def _step(self, value):
        self._setting = True
        self.verticalScrollBar().setValue(round(value))
        self._setting = False

    def _external_scroll(self, value):
        if not self._setting:
            self._scroll.stop()
            self._target = value

    def wheelEvent(self, event):
        delta = event.pixelDelta().y() or event.angleDelta().y() / 120 * 72
        if not delta or event.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier) or QApplication.instance().property("animations") is False:
            super().wheelEvent(event)
            return
        bar = self.verticalScrollBar()
        origin = self._target if self._scroll.state() == QVariantAnimation.Running else bar.value()
        self._target = max(bar.minimum(), min(bar.maximum(), round(origin - delta)))
        self._scroll.stop()
        # Respond immediately, then ease the remaining distance into place.
        self._step(bar.value() + (self._target - bar.value()) * .22)
        self._scroll.setStartValue(float(bar.value()))
        self._scroll.setEndValue(float(self._target))
        self._scroll.start()
        event.accept()


class NumberInput(QSpinBox):
    """Visible plus/minus controls shared by every numeric field."""
    def wheelEvent(self, event):
        event.ignore()

    def paintEvent(self, event):
        super().paintEvent(event)
        option = QStyleOptionSpinBox()
        self.initStyleOption(option)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 13, 13)
        p.setClipPath(clip)
        if self.buttonSymbols() != QAbstractSpinBox.NoButtons:
            up = self.style().subControlRect(QStyle.CC_SpinBox, option, QStyle.SC_SpinBoxUp, self)
            down = self.style().subControlRect(QStyle.CC_SpinBox, option, QStyle.SC_SpinBoxDown, self)
            rail = QRectF(up.united(down))
            rail.setTop(1)
            rail.setBottom(self.height() - 1)
            rail.setRight(self.width() - 1)
            p.fillRect(rail, QColor(colors()["raised"]))
            p.setPen(QPen(QColor(colors()["border"]), 1))
            p.drawLine(rail.topLeft(), rail.bottomLeft())
            p.drawLine(QPointF(rail.left() + 8, self.height() / 2), QPointF(rail.right() - 8, self.height() / 2))
        for control, flag in ((QStyle.SC_SpinBoxUp, QAbstractSpinBox.StepUpEnabled),
                              (QStyle.SC_SpinBoxDown, QAbstractSpinBox.StepDownEnabled)):
            if self.buttonSymbols() == QAbstractSpinBox.NoButtons:
                continue
            rect = self.style().subControlRect(QStyle.CC_SpinBox, option, control, self)
            if option.activeSubControls & control:
                p.fillRect(rect, QColor(colors()["selected"]))
            color = QColor(colors()["accent"] if self.isEnabled() and self.stepEnabled() & flag else colors()["muted"])
            p.setPen(QPen(color, 1.6, Qt.SolidLine, Qt.RoundCap))
            center = rect.center()
            p.drawLine(center.x() - 4, center.y(), center.x() + 4, center.y())
            if control == QStyle.SC_SpinBoxUp:
                p.drawLine(center.x(), center.y() - 4, center.x(), center.y() + 4)


class StudioCheckBox(QCheckBox):
    def __init__(self, *args):
        super().__init__(*args)
        self._checked = float(self.isChecked())
        self._motion = QVariantAnimation(self)
        self._motion.setDuration(200)
        self._motion.setEasingCurve(QEasingCurve.OutCubic)
        self._motion.valueChanged.connect(self._advance)
        self.toggled.connect(lambda checked: animate(self._motion, self._checked, float(checked)))

    def _advance(self, value):
        self._checked = float(value)
        self.update()

    def sizeHint(self):
        return QSize(self.fontMetrics().horizontalAdvance(self.text()) + 34, 34)

    def hitButton(self, point):
        return self.rect().contains(point)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(.45)
        c, t = colors(), self._checked
        rect = QRectF(1, (self.height() - 20) / 2, 20, 20)
        p.setBrush(mix(c["input"], c["primary"], t))
        p.setPen(QPen(mix(c["border"], c["primary"], t), 1))
        p.drawRoundedRect(rect, 6, 6)
        p.setOpacity(t * (1 if self.isEnabled() else .45))
        p.setPen(QPen(QColor(c["primaryText"]), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        x, y = rect.center().x(), rect.center().y()
        p.drawLine(QPointF(x - 4, y), QPointF(x - 1, y + 3))
        p.drawLine(QPointF(x - 1, y + 3), QPointF(x + 5, y - 4))
        p.setOpacity(1 if self.isEnabled() else .45)
        p.setPen(QColor(c["text"]))
        p.drawText(self.rect().adjusted(32, 0, 0, 0), Qt.AlignVCenter, self.text())
        if self.hasFocus() and not self.isDown():
            p.setPen(QPen(QColor(c["accent"]), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect.adjusted(-2, -2, 2, 2), 8, 8)


class OrbitTabBar(QTabBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDrawBase(False)
        self.setExpanding(False)
        self._keyboard_focus = False
        self._indicator = QRectF()
        self._motion = QVariantAnimation(self)
        self._motion.setDuration(260)
        self._motion.setEasingCurve(QEasingCurve.OutCubic)
        self._motion.valueChanged.connect(self._advance)
        self.currentChanged.connect(self._select)

    def _advance(self, rect):
        self._indicator = rect
        self.update()

    def wheelEvent(self, event):
        event.ignore()

    def focusInEvent(self, event):
        self._keyboard_focus = event.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason)
        super().focusInEvent(event)

    def mousePressEvent(self, event):
        self._keyboard_focus = False
        super().mousePressEvent(event)

    def _select(self, index):
        target = QRectF(self.tabRect(index)).adjusted(3, 4, -3, -4)
        animate(self._motion, self._indicator if not self._indicator.isNull() else target, target)

    def tabSizeHint(self, index):
        return QSize(self.fontMetrics().horizontalAdvance(self.tabText(index)) + 36, 48)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._motion.stop()
        self._indicator = QRectF(self.tabRect(self.currentIndex())).adjusted(3, 4, -3, -4)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = colors()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(c["selected"]))
        p.drawRoundedRect(self._indicator, 18, 18)
        for i in range(self.count()):
            p.setPen(QColor(c["text"] if i == self.currentIndex() else c["muted"]))
            p.drawText(self.tabRect(i), Qt.AlignCenter, self.tabText(i))
        if self.hasFocus() and self._keyboard_focus:
            p.setPen(QPen(QColor(c["accent"]), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(self._indicator.adjusted(2, 2, -2, -2), 16, 16)


class StudioTabs(QTabWidget):
    def __init__(self, *args):
        super().__init__(*args)
        self.setTabBar(OrbitTabBar(self))
        self._last_page = -1
        self._page_transition = None
        self.currentChanged.connect(self._transition_page)

    def _transition_page(self, index):
        previous, self._last_page = self._last_page, index
        if previous < 0 or previous == index or not self.isVisible() or QApplication.instance().property("animations") is False:
            return
        from studio_artwork import PageTransition
        if self._page_transition is None:
            self._page_transition = PageTransition(self.findChild(QStackedWidget))
            self._page_transition._motion.setDuration(280)
        self._page_transition.finish()
        if previous < self.count():
            self._page_transition.start(self.widget(previous).grab())
