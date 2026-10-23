"""Orbit's native controls, input policy and finite motion. No service calls."""
from math import exp
from PySide6.QtCore import Qt, QRectF, QSize, QPointF, QAbstractAnimation, QPoint
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath
from PySide6.QtWidgets import (QApplication, QAbstractSpinBox, QCheckBox, QScrollBar, QScrollArea,
    QSpinBox, QStyle, QStyleOptionSlider, QStyleOptionSpinBox, QTabBar, QTabWidget, QStackedWidget,
    )
from studio_motion import SpringMotion


def colors():
    from studio_theme import PALETTES
    return PALETTES[QApplication.instance().property("studioTheme") or "dark"]


def mix(a, b, amount):
    a, b = QColor(a), QColor(b)
    return QColor.fromRgbF(*(x + (y - x) * amount for x, y in zip(a.getRgbF(), b.getRgbF())))


def stop_parent_scroll(widget):
    """Consuming input in an inner scroller cancels the outer scroll tail."""
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, OrbitScrollArea):
            parent._manual_scroll()
        parent = parent.parentWidget()


class StudioScrollBar(QScrollBar):
    """A slim floating thumb; dragging, paging and accessibility remain native."""
    def __init__(self, *args):
        super().__init__(*args)
        self._hover = 0.
        self._motion = SpringMotion(self, 0., self._advance, frequency=30)

    def _advance(self, value):
        self._hover = float(value)
        self.update()

    def enterEvent(self, event):
        self._motion.to(1.)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._motion.to(0.)
        super().leaveEvent(event)

    def hideEvent(self, event):
        self._motion.reset(0.)
        super().hideEvent(event)

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
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(mix(colors()["border"], colors()["muted"], .22 + .35 * self._hover))
        p.drawRoundedRect(rect, thickness / 2, thickness / 2)


class ScrollMotion(QAbstractAnimation):
    """Use Qt's frame clock while retaining velocity between wheel events."""
    def __init__(self, area):
        super().__init__(area)
        self.area = area
        self._last_time = 0

    def duration(self):
        return -1

    def updateState(self, state, previous):
        if state == QAbstractAnimation.Running and previous == QAbstractAnimation.Stopped:
            self._last_time = 0
        super().updateState(state, previous)

    def updateCurrentTime(self, current):
        elapsed = min(.05, max(0, current - self._last_time) / 1000)
        self._last_time = current
        if elapsed:
            self.area._spring_step(elapsed)


class OrbitScrollArea(QScrollArea):
    def __init__(self):
        super().__init__()
        self.setVerticalScrollBar(StudioScrollBar(Qt.Vertical, self))
        self.setHorizontalScrollBar(StudioScrollBar(Qt.Horizontal, self))
        self._setting = False
        self._target = 0
        self._position = self._velocity = 0.
        self._scroll = ScrollMotion(self)
        self._reveal_owner = None
        self._reveal_anchor = 0
        self.verticalScrollBar().valueChanged.connect(self._external_scroll)
        self.verticalScrollBar().sliderPressed.connect(self._manual_scroll)
        self.verticalScrollBar().actionTriggered.connect(self._manual_scroll)

    def _manual_scroll(self):
        self._reveal_owner = None
        self.stop_scroll()

    def follow_reveal(self, widget):
        self.stop_scroll()
        self._reveal_owner = widget
        self._reveal_anchor = widget.mapTo(self.viewport(), QPoint()).y()

    def reveal_frame(self, widget, expanding):
        if self._reveal_owner is not widget or not widget.isVisible():
            return
        top = widget.mapTo(self.widget(), QPoint()).y()
        bar = self.verticalScrollBar()
        if expanding:
            height = min(widget.height(), max(1, self.viewport().height() - 32))
            margin = min(16, max(0, widget.reveal.height() / 5))
            desired = max(bar.value(), round(top + height - self.viewport().height() + margin))
            desired = min(desired, max(bar.value(), top - 16))
        else:
            desired = top - self._reveal_anchor
        self._step(max(bar.minimum(), min(bar.maximum(), round(desired))))
        self._position = float(bar.value())
        self._target, self._velocity = self._position, 0.

    def _step(self, value):
        self._setting = True
        self.verticalScrollBar().setValue(round(value))
        self._setting = False

    def _external_scroll(self, value):
        if not self._setting:
            self._scroll.stop()
            self._target = value
            self._position, self._velocity = float(value), 0.

    def stop_scroll(self, *, settle=False):
        self._scroll.stop()
        if settle:
            self._step(self._target)
        self._position = float(self.verticalScrollBar().value())
        self._target, self._velocity = self._position, 0.

    def _spring_step(self, elapsed):
        bar = self.verticalScrollBar()
        self._target = max(bar.minimum(), min(bar.maximum(), self._target))
        offset = self._position - self._target
        decay = exp(-22 * elapsed)
        impulse = self._velocity + 22 * offset
        self._position = self._target + (offset + impulse * elapsed) * decay
        self._velocity = (self._velocity - 22 * impulse * elapsed) * decay
        self._position = max(bar.minimum(), min(bar.maximum(), self._position))
        self._step(self._position)
        if abs(self._position - self._target) < .25 and abs(self._velocity) < 4:
            self._step(self._target)
            self.stop_scroll()

    def hideEvent(self, event):
        self._reveal_owner = None
        self.stop_scroll()
        super().hideEvent(event)

    def wheelEvent(self, event):
        self._reveal_owner = None
        pixel_delta = event.pixelDelta().y()
        stop_parent_scroll(self)
        delta = pixel_delta or event.angleDelta().y() / 120 * 72
        if not delta or event.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier) or QApplication.instance().property("animations") is False:
            super().wheelEvent(event)
            return
        bar = self.verticalScrollBar()
        if pixel_delta:
            # Precision trackpads already supply their own motion curve.
            self.stop_scroll()
            bar.setValue(round(bar.value() - pixel_delta))
            event.accept()
            return
        running = self._scroll.state() == QAbstractAnimation.Running
        origin = self._target if running else bar.value()
        self._target = max(bar.minimum(), min(bar.maximum(), round(origin - delta)))
        if (self._target - self._position) * self._velocity < 0:
            self._velocity = 0.
        if not running:
            self._position, self._velocity = float(bar.value()), 0.
            if self._target == bar.value():
                event.accept()
                return
            self._spring_step(.008)
            self._scroll.start()
        event.accept()

    def keyPressEvent(self, event):
        self._manual_scroll()
        super().keyPressEvent(event)


class NumberInput(QSpinBox):
    """Visible plus/minus controls shared by every numeric field."""
    def wheelEvent(self, event):
        event.ignore()

    def paintEvent(self, event):
        super().paintEvent(event)
        option = QStyleOptionSpinBox()
        self.initStyleOption(option)
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
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
        self._keyboard_focus = False
        self._motion = SpringMotion(self, self._checked, self._advance, frequency=30)
        self.toggled.connect(lambda checked: self._motion.to(float(checked), immediate=not self.isVisible()))

    def _advance(self, value):
        self._checked = float(value)
        self.update()

    def sizeHint(self):
        return QSize(self.fontMetrics().horizontalAdvance(self.text()) + 34, 34)

    def hitButton(self, point):
        return self.rect().contains(point)

    def hideEvent(self, event):
        self._motion.finish()
        super().hideEvent(event)

    def focusInEvent(self, event):
        self._keyboard_focus = event.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason)
        super().focusInEvent(event)

    def mousePressEvent(self, event):
        self._keyboard_focus = False
        super().mousePressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
        if not self.isEnabled():
            p.setOpacity(.45)
        c, t = colors(), self._checked
        rect = QRectF(3, (self.height() - 20) / 2, 20, 20)
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
        if self.hasFocus() and self._keyboard_focus:
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
        self._motion = SpringMotion(self, (0, 0, 0, 0), self._advance, frequency=27, tolerance=.04)
        self.currentChanged.connect(self._select)

    def _advance(self, rect):
        self._indicator = QRectF(*rect)
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
        self._motion.to((target.x(), target.y(), target.width(), target.height()), immediate=self._indicator.isEmpty() or not self.isVisible())

    def tabSizeHint(self, index):
        return QSize(self.fontMetrics().horizontalAdvance(self.tabText(index)) + 36, 48)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._select(self.currentIndex())
        self._motion.finish()

    def hideEvent(self, event):
        self._motion.finish()
        super().hideEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
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
        self._last_page = -1
        self._page_transition = None
        self._prepared_frame = None
        bar = OrbitTabBar(self)
        # Connect before QTabWidget connects its native page-changing slot.
        # The current stack still contains the outgoing page at this point.
        bar.currentChanged.connect(self._prepare_transition)
        self.setTabBar(bar)
        self.currentChanged.connect(self._transition_page)

    def _prepare_transition(self, index):
        self._prepared_frame = None
        if self._last_page < 0 or index == self._last_page or not self.isVisible() or QApplication.instance().property("animations") is False:
            return
        from studio_artwork import PageTransition
        if self._page_transition is None:
            self._page_transition = PageTransition(self.findChild(QStackedWidget), kind="tabs")
        self._prepared_frame = self._page_transition.snapshot()

    def _transition_page(self, index):
        previous, self._last_page = self._last_page, index
        frame, self._prepared_frame = self._prepared_frame, None
        if previous < 0 or previous == index or not self.isVisible() or QApplication.instance().property("animations") is False:
            return
        from studio_artwork import PageTransition
        if self._page_transition is None:
            self._page_transition = PageTransition(self.findChild(QStackedWidget), kind="tabs")
        if previous < self.count():
            if frame is None:
                frame = self._page_transition.snapshot() if self._page_transition.isVisible() else self.widget(previous).grab()
            self._page_transition.start(frame, 1 if index > previous else -1)


class StudioStack(QStackedWidget):
    """The same directional transition as StudioTabs, captured before mutation."""
    def __init__(self, *args):
        super().__init__(*args)
        self._page_transition = None

    def setCurrentIndex(self, index):
        if index != self.currentIndex():
            self.switch(index)

    def switch(self, index, prepare=None, *, direction=None):
        if not 0 <= index < self.count():
            return
        direction = direction if direction is not None else (1 if index >= self.currentIndex() else -1)
        def apply():
            if prepare is not None:
                prepare()
            QStackedWidget.setCurrentIndex(self, index)
        if not self.isVisible() or QApplication.instance().property("animations") is False:
            apply()
            return
        if self._page_transition is None:
            from studio_artwork import PageTransition
            self._page_transition = PageTransition(self, kind="tabs")
        self._page_transition.change(apply, direction)
