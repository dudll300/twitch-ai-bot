"""Orbit's native, resolution independent page horizon and session heading."""
from math import cos, sin, radians
from random import Random
from PySide6.QtCore import Qt, QRectF, QRect, QVariantAnimation, QEasingCurve, QTimer, QPointF, QPoint, QEvent, QElapsedTimer
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient, QLinearGradient, QPixmap, QRegion
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QVBoxLayout, QSizePolicy, QWidget

from studio_theme import PALETTES
from ui_widgets import label
from studio_motion import SpringMotion, enabled, smooth


class PageTransition(QWidget):
    """A short composited transition, with crisp native text after completion."""
    def __init__(self, parent, kind="page"):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._old, self._new = QPixmap(), QPixmap()
        self.kind, self.direction = kind, 1
        self._layers = []
        self._caption_children = []
        self._input_widgets = []
        self._progress, self._generation = 1., 0
        self._motion = QVariantAnimation(self)
        self._motion.setDuration(480 if kind == "page" else 220 if kind == "caption" else 360)
        self._motion.setEasingCurve(QEasingCurve.Linear)
        self._motion.valueChanged.connect(self._advance)
        self._motion.finished.connect(self.finish)
        parent.installEventFilter(self)
        self.hide()

    def _advance(self, value):
        self._progress = float(value)
        self.update()

    def change(self, mutation, direction=1):
        previous = self.snapshot() if self.parentWidget().isVisible() and enabled() else None
        mutation()
        if previous is not None:
            self.start(previous, direction)

    def start(self, previous, direction=1):
        self.finish()
        self.direction = direction
        generation = self._generation
        self._old = previous if self.kind == "caption" else self._opaque(previous)
        def capture():
            if generation != self._generation or not self.parentWidget().isVisible() or QApplication.instance().property("animations") is False:
                return
            incoming = self.snapshot()
            self._new = incoming if self.kind == "caption" else self._opaque(incoming)
            self._collect_layers()
            if self.kind == "caption":
                self._caption_children = [child for child in self.parentWidget().findChildren(QWidget)
                                          if not child.isHidden() and child is not self]
                for child in self._caption_children:
                    child.installEventFilter(self)
            self.setGeometry(self.parentWidget().rect())
            self._progress = 0.
            self.raise_()
            self.show()
            # A transition is presentation only. The very next input exposes
            # native controls before they handle it, rather than hiding edits.
            if self.kind != "caption":
                self._input_widgets = [child for child in self.parentWidget().findChildren(QWidget)
                                       if child is not self and child.isVisibleTo(self.parentWidget())]
                for child in self._input_widgets:
                    child.installEventFilter(self)
            self._motion.setStartValue(0.)
            self._motion.setEndValue(1.)
            self._motion.start()
        QTimer.singleShot(0, self, capture)

    def snapshot(self):
        widget = self.parentWidget()
        if self.kind != "caption":
            if self.isVisible():
                return self.grab()
            return widget.grab()
        # Render only the caption's children, leaving the moving sky visible.
        ratio = widget.devicePixelRatioF()
        result = QPixmap(round(widget.width() * ratio), round(widget.height() * ratio))
        result.setDevicePixelRatio(ratio)
        result.fill(Qt.transparent)
        painter = QPainter(result)
        widget.render(painter, QPoint(), QRegion(), QWidget.DrawChildren)
        painter.end()
        return result

    def _collect_layers(self):
        self._layers = []
        if self.kind != "page":
            return
        parent = self.parentWidget()
        candidates = []
        for panel in parent.findChildren(QFrame, "card"):
            if not panel.isVisibleTo(parent):
                continue
            ancestor = panel.parentWidget()
            while ancestor is not None and ancestor is not parent and ancestor.objectName() != "card":
                ancestor = ancestor.parentWidget()
            if ancestor is not parent:
                continue
            visible = panel.visibleRegion().boundingRect()
            if visible.isEmpty():
                continue
            origin = panel.mapTo(parent, visible.topLeft())
            rect = QRect(origin, visible.size()).adjusted(-7, -7, 7, 7).intersected(parent.rect())
            if rect.width() > 50 and rect.height() > 40:
                candidates.append(rect)
        ratio = self._new.devicePixelRatio()
        for index, rect in enumerate(sorted(candidates, key=lambda r: (r.y() // 40, r.x()))[:6]):
            pixels = QRect(round(rect.x() * ratio), round(rect.y() * ratio), round(rect.width() * ratio), round(rect.height() * ratio))
            patch = self._new.copy(pixels)
            patch.setDevicePixelRatio(ratio)
            self._layers.append((QRectF(rect), patch, min(.25, index * .065)))

    @staticmethod
    def _opaque(snapshot):
        result = QPixmap(snapshot.size())
        result.setDevicePixelRatio(snapshot.devicePixelRatio())
        c = PALETTES[QApplication.instance().property("studioTheme") or "dark"]
        result.fill(QColor(c["bg"]))
        painter = QPainter(result)
        painter.drawPixmap(0, 0, snapshot)
        painter.end()
        return result

    def finish(self):
        self._generation += 1
        self._motion.stop()
        self.hide()
        for child in self._input_widgets:
            try:
                child.removeEventFilter(self)
            except RuntimeError:
                pass  # An async list refresh may have removed a row widget.
        self._input_widgets = []
        for child in self._caption_children:
            child.removeEventFilter(self)
            child.update()
        self._caption_children = []
        self._old, self._new = QPixmap(), QPixmap()
        self._layers = []

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Paint and watched in self._caption_children:
            # Keep native caption geometry and visibility intact while the
            # transparent overlay paints the text over the live sky.
            return True
        if (watched is self.parentWidget() and (event.type() == QEvent.Hide or
                (event.type() == QEvent.Resize and self.isVisible()))) or event.type() in (
                QEvent.MouseButtonPress, QEvent.KeyPress, QEvent.Wheel, QEvent.TouchBegin):
            self.finish()
        return False

    def paintEvent(self, event):
        p = QPainter(self)
        c = PALETTES[QApplication.instance().property("studioTheme") or "dark"]
        if self.kind != "caption":
            p.fillRect(self.rect(), QColor(c["bg"]))
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        progress = smooth(self._progress)
        if self.kind == "caption":
            p.setOpacity(1 - smooth(self._progress / .4))
        p.drawPixmap(QPointF(0, -3 * progress if self.kind in ("page", "caption") else 0), self._old)
        if self._layers:
            background = QRegion(self.rect())
            for rect, _, _ in self._layers:
                background -= QRegion(rect.toRect())
            p.save()
            p.setClipRegion(background)
            p.setOpacity(progress)
            p.drawPixmap(0, 0, self._new)
            p.restore()
            for rect, patch, delay in self._layers:
                local = smooth((self._progress - delay) / (1 - delay))
                p.setOpacity(progress)
                p.fillRect(rect, QColor(c["bg"]))
                p.save()
                p.setOpacity(local)
                center = rect.center()
                p.translate(center.x(), center.y() + 16 * (1 - local))
                p.scale(.985 + .015 * local, .985 + .015 * local)
                p.drawPixmap(QPointF(-rect.width() / 2, -rect.height() / 2), patch)
                p.restore()
        else:
            p.setOpacity(smooth((self._progress - .4) / .6) if self.kind == "caption" else progress)
            offset = QPointF(12 * self.direction * (1 - progress), 0) if self.kind == "tabs" else QPointF(0, 0 if self.kind == "theme" else 6 * (1 - progress))
            p.drawPixmap(offset, self._new)


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
        self._parallax = QPointF()
        self._star_phase = 0.
        random = Random(73019)
        self._stars = tuple((random.uniform(.015, .985), random.uniform(.06, .92),
                             random.uniform(.5, 1.45), random.uniform(0, 2 * 3.14159),
                             random.uniform(.35, .9), random.uniform(55, 145),
                             random.random() < .12) for _ in range(42))
        self._star_clock = QElapsedTimer()
        self._star_window = None
        self._twinkle_timer = QTimer(self)
        self._twinkle_timer.setInterval(33)
        self._twinkle_timer.timeout.connect(self._twinkle)
        self.setMouseTracking(True)
        self._motion = SpringMotion(self, 0., self._advance, frequency=20)
        self._pointer_motion = SpringMotion(self, (0, 0), self._advance_pointer, frequency=24)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 16, 14, 24)
        row.setSpacing(20)
        self.caption = QWidget()
        copy = QVBoxLayout(self.caption)
        copy.setContentsMargins(0, 0, 0, 0)
        copy.setSpacing(8)
        self.eyebrow = label("СТУДИЯ / ЭФИР", "eyebrow")
        copy.addWidget(self.eyebrow)
        copy.addWidget(title)
        copy.addWidget(description)
        row.addWidget(self.caption, 1)
        self.caption_transition = PageTransition(self.caption, kind="caption")
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
        for child in self.findChildren(QWidget):
            child.setAttribute(Qt.WA_TransparentForMouseEvents)

    def set_page(self, title, stream=False, description=None):
        previous = self.caption_transition.snapshot() if self.title.text() != title and self.isVisible() and enabled() else None
        self.caption_transition.finish()
        self.title.setText(title)
        if description is not None:
            self.description.setText(description)
        self._stream = stream
        self.eyebrow.setText("СТУДИЯ / " + title.upper())
        self.stats.setVisible(stream and self.width() >= 650)
        if previous is not None:
            self.caption_transition.start(previous)

    def update_state(self, state):
        self.answers.setText(str(state.answers))
        self.queue.setText(str(state.queue))

    def _advance(self, value):
        self._shift = float(value)
        self.update()

    def _advance_pointer(self, value):
        self._parallax = QPointF(*value)
        self.update()

    def _move_pointer(self, target):
        self._pointer_motion.to((target.x(), target.y()) if enabled() else (0, 0))

    def reset_motion(self):
        self._motion.reset(0.)
        self._pointer_motion.reset((0, 0))
        self.sync_star_motion()

    def sync_star_motion(self):
        enabled = QApplication.instance().property("animations") is not False
        if enabled and self.isVisible() and not self.window().isMinimized():
            if not self._twinkle_timer.isActive():
                self._star_clock.start()
                self._twinkle_timer.start()
        else:
            self._twinkle_timer.stop()
        self.update()

    def _twinkle(self):
        self._star_phase += min(.1, self._star_clock.restart() / 1000)
        self.update(self.rect().adjusted(0, 0, 0, -1))

    def showEvent(self, event):
        super().showEvent(event)
        if self._star_window is not self.window():
            self._star_window = self.window()
            self._star_window.installEventFilter(self)
        self.sync_star_motion()

    def hideEvent(self, event):
        self._twinkle_timer.stop()
        self._motion.reset(0.)
        self._pointer_motion.reset((0, 0))
        super().hideEvent(event)

    def eventFilter(self, watched, event):
        if watched is self._star_window and event.type() == QEvent.WindowStateChange:
            QTimer.singleShot(0, self.sync_star_motion)
        return False

    def mouseMoveEvent(self, event):
        point = event.position()
        self._move_pointer(QPointF(2 * point.x() / max(1, self.width()) - 1,
                                  2 * point.y() / max(1, self.height()) - 1))
        super().mouseMoveEvent(event)

    def _move_horizon(self, value):
        self._motion.to(float(value) if enabled() else 0.)

    def enterEvent(self, event):
        self._move_horizon(4.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._move_horizon(0)
        self._move_pointer(QPointF())
        super().leaveEvent(event)

    def resizeEvent(self, event):
        self.stats.setVisible(self._stream and self.width() >= 650)
        compact = event.size().width() < 900
        self.eyebrow.setVisible(not compact)
        self.layout().setContentsMargins(14, 4 if compact else 16, 14, 10 if compact else 24)
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
        hover = self._shift / 4
        dx, dy = self._parallax.x(), self._parallax.y()
        p.save()
        p.translate(width - 225 + dx * 9, -67 + self._shift + dy * 5)
        p.rotate(-10 + dx * 1.4)
        p.scale(310, 165)
        halo = QRadialGradient(0, .12, 1.25)
        halo.setColorAt(0, QColor(Qt.transparent))
        halo.setColorAt(.70, QColor(Qt.transparent))
        glow = QColor(colors["glow"])
        glow.setAlpha(round(65 + 70 * hover))
        halo.setColorAt(.83, glow)
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
        p.translate(width - 220 + dx * 4, 39 + self._shift + dy * 3)
        p.rotate(-15)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor("#244264" if theme == "dark" else "#CADBF0"), 1))
        p.drawEllipse(QRectF(-290, -79, 580, 158))
        p.setPen(Qt.NoPen)
        angle = radians(120 + dx * 9 + hover * 5)
        satellite = QPointF(290 * cos(angle), 79 * sin(angle))
        radius = 14 + hover * 5
        light = QRadialGradient(satellite, radius)
        tint = QColor(colors["glow"])
        tint.setAlpha(round(100 + hover * 65))
        light.setColorAt(0, tint)
        light.setColorAt(1, QColor(Qt.transparent))
        p.setBrush(light)
        p.drawEllipse(satellite, radius, radius)
        p.setBrush(QColor(colors["accent"]))
        p.drawEllipse(satellite, 3, 3)
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
        # Randomized once, stable through navigation and resize. Independent
        # sizes, phases and periods keep the sky irregular across the full header.
        for horizontal, vertical, radius, phase, rate, brightness, sparkle in self._stars:
            x = width * horizontal + dx * 2
            y = 5 + (self.height() - 14) * vertical + dy
            pulse = .5 + .5 * sin(self._star_phase * rate + phase)
            tint = QColor(colors["accent"])
            tint.setAlpha(round(25 + pulse * brightness))
            p.setBrush(tint)
            p.drawEllipse(QPointF(x, y), radius, radius)
            if sparkle:
                tint.setAlpha(round(tint.alpha() * .45))
                p.setPen(QPen(tint, .7))
                p.drawLine(QPointF(x - 3, y), QPointF(x + 3, y))
                p.drawLine(QPointF(x, y - 3), QPointF(x, y + 3))
                p.setPen(Qt.NoPen)
        p.setPen(QPen(QColor(colors["border"]), 1))
        p.drawLine(14, self.height() - 1, width - 14, self.height() - 1)
