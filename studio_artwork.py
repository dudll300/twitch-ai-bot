"""Orbit's native, resolution independent page horizon and session heading."""
from PySide6.QtCore import Qt, QRectF, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient, QLinearGradient
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QVBoxLayout, QSizePolicy

from studio_theme import PALETTES
from ui_widgets import label


class OrbitHeader(QFrame):
    def __init__(self, title, description, parent=None):
        super().__init__(parent)
        self._stream = False
        self.title = title
        self.description = description
        self.setObjectName("orbitHeader")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.setMinimumHeight(148)
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
        self.setMinimumHeight(92 if compact else 148)
        super().resizeEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        theme = QApplication.instance().property("studioTheme") or "dark"
        colors = PALETTES[theme]
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setClipRect(self.rect())
        # A clipped ellipse creates the same quiet blue horizon at every DPI.
        width = self.width()
        planet = QRectF(width - 615, -270 + self._shift, 690, 365)
        gradient = QRadialGradient(planet.center(), planet.width() / 2)
        stops = ((0, colors["bg"]), (.68, colors["bg"]), (.82, "#122D50" if theme == "dark" else "#DDEBFF"),
                 (.95, "#396CA9" if theme == "dark" else "#93B7E8"),
                 (1, "#0C1B30" if theme == "dark" else "#EEF5FF"))
        for position, color in stops:
            gradient.setColorAt(position, QColor(color))
        p.setOpacity(.95 if width >= 800 else .5)
        p.setPen(QPen(QColor("#385C88" if theme == "dark" else "#ABC6E9"), 1))
        p.setBrush(gradient)
        p.drawEllipse(planet)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor("#244264" if theme == "dark" else "#CADBF0"), 1))
        p.drawEllipse(QRectF(width - 630, -75 + self._shift, 660, 170))
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
