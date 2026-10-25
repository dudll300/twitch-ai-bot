"""Shared finite motion: continuous springs and a travelling navigation surface."""
from math import exp

from PySide6.QtCore import Qt, QRectF, QAbstractAnimation, Signal, QTimer
from PySide6.QtGui import QPainter, QColor, QLinearGradient
from PySide6.QtWidgets import QApplication, QWidget


def enabled():
    return QApplication.instance().property("animations") is not False


def smooth(value):
    value = min(1., max(0., value))
    return value ** 3 * (10 + value * (-15 + 6 * value))


class SpringMotion(QAbstractAnimation):
    """Analytic damping on Qt's frame clock; retarget without dropping velocity."""
    settled = Signal()

    def __init__(self, parent, initial, sink, *, frequency=26., tolerance=.001):
        super().__init__(parent)
        self._sink, self.frequency, self.tolerance = sink, frequency, tolerance
        self._value = self._vector(initial)
        self._target = self._value[:]
        self._velocity = [0.] * len(self._value)
        self._last_time = 0
        self._age = 0.

    @staticmethod
    def _vector(value):
        return [float(v) for v in value] if isinstance(value, (tuple, list)) else [float(value)]

    def value(self):
        return self._value[0] if len(self._value) == 1 else tuple(self._value)

    def duration(self):
        return -1

    def _publish(self):
        self._sink(self.value())

    def reset(self, value):
        self.stop()
        self._value = self._vector(value)
        self._target = self._value[:]
        self._velocity = [0.] * len(self._value)
        self._publish()

    def finish(self):
        self.reset(self._target)
        self.settled.emit()

    def to(self, value, *, immediate=False):
        self._target = self._vector(value)
        self._age = 0.
        if immediate or not enabled():
            self.finish()
        elif self.state() != QAbstractAnimation.Running:
            self.start()

    def updateState(self, state, previous):
        if state == QAbstractAnimation.Running and previous == QAbstractAnimation.Stopped:
            self._last_time = 0
        super().updateState(state, previous)

    def updateCurrentTime(self, current):
        dt = min(.05, max(0, current - self._last_time) / 1000)
        self._last_time = current
        if not dt:
            return
        self._age += dt
        decay, omega = exp(-self.frequency * dt), self.frequency
        for index, target in enumerate(self._target):
            offset = self._value[index] - target
            impulse = self._velocity[index] + omega * offset
            self._value[index] = target + (offset + impulse * dt) * decay
            self._velocity[index] = (self._velocity[index] - omega * impulse * dt) * decay
        self._publish()
        if self._age >= .72 or (max(abs(a - b) for a, b in zip(self._value, self._target)) < self.tolerance
                                and max(abs(v) for v in self._velocity) < self.tolerance * 30):
            self.finish()


class NavigationSurface(QWidget):
    """One continuous pill behind the native rail's accessible buttons."""
    def __init__(self):
        super().__init__()
        self._button = None
        self._indicator = QRectF()
        self.motion = SpringMotion(self, (0, 0, 0, 0), self._advance, frequency=25, tolerance=.04)

    def _advance(self, values):
        self._indicator = QRectF(*values)
        self.update()

    def select(self, button, *, immediate=False):
        self._button = button
        if button.isHidden():
            self.motion.reset((0, 0, 0, 0))
            return
        origin = button.mapTo(self, button.rect().topLeft())
        target = origin.x(), origin.y(), button.width(), button.height()
        self.motion.to(target, immediate=immediate or self._indicator.isEmpty() or not self.isVisible())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._button is not None:
            QTimer.singleShot(0, self, lambda: self.select(self._button, immediate=True))

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._indicator.isEmpty():
            return
        from studio_theme import PALETTES
        c = PALETTES[QApplication.instance().property("studioTheme") or "dark"]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self._indicator.adjusted(.5, .5, -.5, -.5)
        fill = QLinearGradient(rect.topLeft(), rect.bottomRight())
        fill.setColorAt(0, QColor(c["selected"]))
        fill.setColorAt(1, QColor(c["raised"]))
        painter.setPen(Qt.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, 16, 16)
