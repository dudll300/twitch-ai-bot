"""Orbit's native, resolution independent page horizon and session heading."""
from PySide6.QtCore import Qt, QRectF, QVariantAnimation, QEasingCurve, QTimer, QPointF, QEvent
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient, QLinearGradient, QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QVBoxLayout, QSizePolicy, QWidget

from studio_theme import PALETTES
from ui_widgets import label


class PageTransition(QWidget):
    """A short composited transition, with crisp native text after completion."""
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._old, self._new = QPixmap(), QPixmap()
        self._progress, self._generation = 1., 0
        self._motion = QVariantAnimation(self)
        self._motion.setDuration(340)
        self._motion.setEasingCurve(QEasingCurve.OutCubic)
        self._motion.valueChanged.connect(self._advance)
        self._motion.finished.connect(self.finish)
        parent.installEventFilter(self)
        self.hide()

    def _advance(self, value):
        self._progress = float(value)
        self.update()

    def start(self, previous):
        self.finish()
        generation = self._generation
        self._old = previous
        def capture():
            if generation != self._generation or not self.parentWidget().isVisible() or QApplication.instance().property("animations") is False:
                return
            self._new = self.parentWidget().grab()
            self.setGeometry(self.parentWidget().rect())
            self._progress = 0.
            self.raise_()
            self.show()
            self._motion.setStartValue(0.)
            self._motion.setEndValue(1.)
            self._motion.start()
        QTimer.singleShot(0, capture)

    def finish(self):
        self._generation += 1
        self._motion.stop()
        self.hide()
        self._old, self._new = QPixmap(), QPixmap()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Resize, QEvent.Hide):
            self.finish()
        return False

    def paintEvent(self, event):
        p = QPainter(self)
        c = PALETTES[QApplication.instance().property("studioTheme") or "dark"]
        p.fillRect(self.rect(), QColor(c["bg"]))
        p.setOpacity(1 - self._progress)
        p.drawPixmap(QPointF(0, -4 * self._progress), self._old)
        p.setOpacity(self._progress)
        p.drawPixmap(QPointF(0, 10 * (1 - self._progress)), self._new)


class OrbitHeader(QFrame):
    def __init__(self, title, description, parent=None):
        super().__init__(parent)
        self._stream = False
        self.title = title
        self.description = description
        self.setObjectName("orbitHeader")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.setMinimumHeight(154)
        self._shift = 0.0
        self._motion = QVariantAnimation(self)
        self._motion.setDuration(420)
        self._motion.setEasingCurve(QEasingCurve.OutCubic)
        self._motion.valueChanged.connect(self._advance)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 16, 8, 24)
        row.setSpacing(20)
        copy = QVBoxLayout()
        copy.setSpacing(8)
        self.eyebrow = label("СТУДИЯ / ЭФИР", "eyebrow")
        copy.addWidget(self.eyebrow)
        copy.addWidget(title)
        copy.addWidget(description)
        row.addLayout(copy, 1)
        self.stats = QFrame()
        metrics = QHBoxLayout(self.stats)
        metrics.setContentsMargins(0, 30, 0, 0)
        metrics.setSpacing(28)
        self.answers, self.queue = label("0", "heroValue"), label("0", "heroValue")
        for value, caption in ((self.answers, "ответов за эфир"), (self.queue, "в очереди")):
            column = QVBoxLayout()
            column.setSpacing(2)
            column.addWidget(value)
            column.addWidget(label(caption, "muted"))
            metrics.addLayout(column)
        row.addWidget(self.stats, 0, Qt.AlignBottom)

    def set_page(self, title, stream=False):
        self._stream = stream
        self.eyebrow.setText("СТУДИЯ / " + title.upper())
        self.stats.setVisible(stream and self.width() >= 650)

    def update_state(self, state):
        self.answers.setText(str(state.answers))
        self.queue.setText(str(state.queue))

    def _advance(self, value):
        self._shift = float(value)
        self.update()

    def _move_horizon(self, value):
        self._motion.stop()
        if QApplication.instance().property("animations") is False:
            self._advance(0)
            return
        self._motion.setStartValue(self._shift)
        self._motion.setEndValue(value)
        self._motion.start()

    def enterEvent(self, event):
        self._move_horizon(4.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._move_horizon(0)
        super().leaveEvent(event)

    def resizeEvent(self, event):
        self.stats.setVisible(self._stream and self.width() >= 650)
        compact = event.size().width() < 900
        self.eyebrow.setVisible(not compact)
        self.layout().setContentsMargins(0, 4 if compact else 16, 8, 10 if compact else 24)
        self.setMinimumHeight(92 if compact else 154)
        super().resizeEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        theme = QApplication.instance().property("studioTheme") or "dark"
        colors = PALETTES[theme]
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setClipRect(self.rect())
        # Paint the gradient in ellipse coordinates: a circular radial gradient
        # in window coordinates previously hid almost the entire illuminated rim.
        width = self.width()
        p.save()
        p.translate(width - 225, -67 + self._shift)
        p.rotate(-10)
        p.scale(310, 165)
        halo = QRadialGradient(0, .12, 1.25)
        halo.setColorAt(0, QColor(Qt.transparent))
        halo.setColorAt(.70, QColor(Qt.transparent))
        halo.setColorAt(.83, QColor("#443C8FF0" if theme == "dark" else "#384D8DE0"))
        halo.setColorAt(1, QColor(Qt.transparent))
        p.setPen(Qt.NoPen)
        p.setBrush(halo)
        p.drawEllipse(QRectF(-1.3, -1.3, 2.6, 2.6))
        gradient = QRadialGradient(0, .2, 1.12)
        stops = ((0, "#02050B" if theme == "dark" else "#F7FAFF"),
                 (.47, "#02050B" if theme == "dark" else "#F7FAFF"),
                 (.59, "#102B56" if theme == "dark" else "#D7E8FF"),
                 (.68, "#3565B0" if theme == "dark" else "#A0C1F3"),
                 (.71, "#ABD7FF" if theme == "dark" else "#6E9BD7"),
                 (.73, "#0C55D4" if theme == "dark" else "#9CBCE9"),
                 (.88, colors["bg"]))
        for position, color in stops:
            gradient.setColorAt(position, QColor(color))
        p.setOpacity(.95 if width >= 800 else .6)
        p.setBrush(gradient)
        p.drawEllipse(QRectF(-1, -1, 2, 2))
        p.restore()
        p.save()
        p.translate(width - 220, 39 + self._shift)
        p.rotate(-15)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor("#244264" if theme == "dark" else "#CADBF0"), 1))
        p.drawEllipse(QRectF(-290, -79, 580, 158))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#304E83B0"))
        p.drawEllipse(QRectF(-153, 58, 16, 16))
        p.setBrush(QColor(colors["accent"]))
        p.drawEllipse(QRectF(-148, 63, 6, 6))
        p.restore()
        # Fade the artwork behind page copy instead of tinting its text.
        fade = QLinearGradient(0, 0, width, 0)
        opaque = QColor(colors["bg"])
        transparent = QColor(opaque)
        transparent.setAlpha(0)
        fade.setColorAt(0, opaque)
        fade.setColorAt(.42, opaque)
        fade.setColorAt(.8, transparent)
        p.setOpacity(1)
        p.setPen(Qt.NoPen)
        p.setBrush(fade)
        p.drawRect(self.rect())
        p.setPen(QPen(QColor(colors["border"]), 1))
        p.drawLine(0, self.height() - 1, width, self.height() - 1)
