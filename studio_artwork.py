"""Orbit's native, resolution independent page horizon and session heading."""
from math import cos, sin, radians, floor, ceil, pi
from random import Random
from weakref import ref
from PySide6.QtCore import Qt, QRectF, QRect, QVariantAnimation, QEasingCurve, QTimer, QPointF, QPoint, QEvent, QElapsedTimer, QObject
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient, QLinearGradient, QPixmap, QRegion
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QVBoxLayout, QSizePolicy, QWidget
from shiboken6 import isValid

from studio_theme import PALETTES
from ui_widgets import label
from studio_motion import SpringMotion, enabled, smooth


class TransitionEvents(QObject):
    """Keep native event-filter lifetime separate from the painted QWidget."""
    def __init__(self, owner):
        super().__init__(owner)
        self._owner = ref(owner)

    def eventFilter(self, watched, event):
        # Keep the borrowed event inside the native callback.
        kind = QEvent.type(event)
        owner = self._owner()
        if owner is None or not isValid(owner) or not isValid(watched):
            return False
        return owner._filter_event(watched, kind)


class PageTransition(QWidget):
    """A short composited transition, with crisp native text after completion."""
    def __init__(self, parent, kind="page"):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_OpaquePaintEvent, kind != "caption")
        self._old, self._new = QPixmap(), QPixmap()
        self.kind, self.direction = kind, 1
        self._layers = []
        self._caption_children = []
        self._input_widgets = []
        self._capture_pending = False
        self._capturing = False
        self._progress, self._generation = 1., 0
        self._motion = QVariantAnimation(self)
        self._motion.setDuration(480 if kind == "page" else 220 if kind == "caption" else 360)
        self._motion.setEasingCurve(QEasingCurve.Linear)
        self._motion.valueChanged.connect(self._advance)
        self._motion.finished.connect(self.finish)
        self._events_filter = TransitionEvents(self)
        parent.installEventFilter(self._events_filter)
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
        self._capture_pending = True
        self._progress = 0.
        self.setGeometry(self.parentWidget().rect())
        if self.kind == "caption":
            self._caption_children = [child for child in self.parentWidget().findChildren(QWidget)
                                      if not child.isHidden() and child is not self]
            for child in self._caption_children:
                child.installEventFilter(self._events_filter)
        else:
            self._input_widgets = [child for child in self.parentWidget().findChildren(QWidget)
                                   if child is not self and child.isVisibleTo(self.parentWidget())]
            for child in self._input_widgets:
                child.installEventFilter(self._events_filter)
        # Keep the outgoing frame on screen while native layouts settle. Hiding
        # it until the deferred capture can flash the destination for one frame.
        self.raise_()
        self.show()
        def capture():
            if generation != self._generation or not self.parentWidget().isVisible() or QApplication.instance().property("animations") is False:
                return
            self._capturing = True
            self.hide()
            for child in self._caption_children:
                child.removeEventFilter(self._events_filter)
            try:
                incoming = self.snapshot()
            finally:
                for child in self._caption_children:
                    child.installEventFilter(self._events_filter)
                self._capturing = False
                self._capture_pending = False
            if generation != self._generation or not self.parentWidget().isVisible():
                return
            self._new = incoming if self.kind == "caption" else self._opaque(incoming)
            self._collect_layers()
            self.setGeometry(self.parentWidget().rect())
            self._progress = 0.
            self.raise_()
            self.show()
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
        self._capture_pending = False
        self.hide()
        for child in self._input_widgets:
            try:
                child.removeEventFilter(self._events_filter)
            except RuntimeError:
                pass  # An async list refresh may have removed a row widget.
        self._input_widgets = []
        for child in self._caption_children:
            child.removeEventFilter(self._events_filter)
            child.update()
        self._caption_children = []
        self._old, self._new = QPixmap(), QPixmap()
        self._layers = []

    def _filter_event(self, watched, kind):
        if kind == QEvent.Paint and any(watched is child for child in self._caption_children):
            # Keep native caption geometry and visibility intact while the
            # transparent overlay paints the text over the live sky.
            return True
        if watched is self.parentWidget() and kind == QEvent.Resize and (self._capture_pending or self._capturing):
            self.setGeometry(self.parentWidget().rect())
            return False
        if (watched is self.parentWidget() and (kind == QEvent.Hide or
                (kind == QEvent.Resize and self.isVisible()))) or kind in (
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
        if self.kind == "tabs":
            self._paint_replacement(p, progress)
            return
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
            offset = QPointF(0, 0 if self.kind == "theme" else 6 * (1 - progress))
            p.drawPixmap(offset, self._new)

    def _paint_replacement(self, painter, progress):
        """Replace stationary chrome through a soft edge, without doubled text.

        Only the 24px seam blends; the rest is one opaque, unscaled native frame.
        A small seam patch avoids allocating a full-size opacity mask each frame.
        """
        painter.drawPixmap(0, 0, self._old)
        if self._new.isNull():
            return
        width, height, feather = self.width(), self.height(), 24
        edge = (width + 2 * feather) * progress - feather
        forward = self.direction >= 0
        if not forward:
            edge = width - edge
        start, end = edge - feather / 2, edge + feather / 2
        solid = QRectF(0, 0, max(0, start), height) if forward else QRectF(end, 0, max(0, width - end), height)
        painter.save()
        painter.setClipRect(solid, Qt.IntersectClip)
        painter.drawPixmap(0, 0, self._new)
        painter.restore()
        left, right = max(0, floor(start)), min(width, ceil(end))
        if right <= left:
            return
        ratio = self._new.devicePixelRatio()
        crop = self._new.copy(QRect(round(left * ratio), 0, round((right - left) * ratio), self._new.height()))
        # Native opaque captures can have no alpha plane. Masking them directly
        # would multiply their RGB to black instead of exposing the old frame.
        patch = QPixmap(crop.size())
        patch.setDevicePixelRatio(ratio)
        patch.fill(Qt.transparent)
        mask = QLinearGradient(start - left, 0, end - left, 0)
        mask.setColorAt(0, QColor(0, 0, 0, 255 if forward else 0))
        mask.setColorAt(1, QColor(0, 0, 0, 0 if forward else 255))
        blend = QPainter(patch)
        blend.drawPixmap(0, 0, crop)
        blend.setCompositionMode(QPainter.CompositionMode_DestinationIn)
        blend.fillRect(QRectF(0, 0, right - left, height), mask)
        blend.end()
        painter.drawPixmap(QPointF(left, 0), patch)


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
        self._twinkle_timer.setTimerType(Qt.PreciseTimer)
        self._twinkle_timer.setInterval(16)
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

    @staticmethod
    def _orbit_pose(seconds):
        # Independent long periods keep the horizon alive without a repeated
        # bobbing gesture. The light completes one orbit in three minutes.
        return (4 * sin(seconds * 2 * pi / 31),
                2.5 * sin(seconds * 2 * pi / 23),
                .65 * sin(seconds * 2 * pi / 37),
                .5 - .5 * cos(seconds * 2 * pi / 9),
                seconds * 2)

    def showEvent(self, event):
        super().showEvent(event)
        window = self.window().windowHandle()
        if self._star_window is not window:
            if self._star_window is not None:
                try:
                    self._star_window.windowStateChanged.disconnect(self._window_state_changed)
                except RuntimeError:
                    pass
            self._star_window = window
            if window is not None:
                window.windowStateChanged.connect(self._window_state_changed)
        self.sync_star_motion()

    def hideEvent(self, event):
        self._twinkle_timer.stop()
        self._motion.reset(0.)
        self._pointer_motion.reset((0, 0))
        super().hideEvent(event)

    def _window_state_changed(self, *_):
        # Observe just the native state signal instead of routing every main
        # window event through a Python filter (including native teardown).
        QTimer.singleShot(0, self, self.sync_star_motion)

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
        sway_x, sway_y, tilt, breath, travel = self._orbit_pose(self._star_phase)
        p.save()
        p.translate(width - 225 + dx * 9 + sway_x, -67 + self._shift + dy * 5 + sway_y)
        p.rotate(-10 + dx * 1.4 + tilt)
        p.scale(310, 165)
        # A broad corona breathes outside the horizon, while the actual body
        # keeps its geometry. Use translucent gradients rather than blur effects.
        halo = QRadialGradient(0, .12, 1.35 + .035 * breath)
        halo.setColorAt(0, QColor(Qt.transparent))
        halo.setColorAt(.58, QColor(Qt.transparent))
        secondary = QColor(colors["glowSecondary"])
        secondary.setAlpha(round(15 + 12 * breath))
        halo.setColorAt(.72, secondary)
        glow = QColor(colors["glow"])
        glow.setAlpha(round(70 + 45 * hover + 44 * breath))
        halo.setColorAt(.83, glow)
        halo.setColorAt(1, QColor(Qt.transparent))
        p.setPen(Qt.NoPen)
        p.setBrush(halo)
        p.drawEllipse(QRectF(-1.42, -1.42, 2.84, 2.84))
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
        # The crescent itself emits light as well; its pulse is independent of
        # the satellite and mouse hover, and never changes caption geometry.
        emission = QRadialGradient(0, .2, 1.12)
        emission.setColorAt(0, QColor(Qt.transparent))
        emission.setColorAt(.55, QColor(Qt.transparent))
        rim = QColor(colors["accent"])
        rim.setAlpha(round((12 + 42 * breath) * (1 if theme == "dark" else .65)))
        emission.setColorAt(.705, rim)
        rim.setAlpha(round(8 + 20 * breath))
        emission.setColorAt(.78, rim)
        emission.setColorAt(.91, QColor(Qt.transparent))
        emission.setColorAt(1, QColor(Qt.transparent))
        p.setBrush(emission)
        p.drawEllipse(QRectF(-1, -1, 2, 2))
        p.restore()
        p.save()
        p.translate(width - 220 + dx * 4 + sway_x * .65, 39 + self._shift + dy * 3 + sway_y * .65)
        p.rotate(-15 + tilt * .4)
        p.save()
        p.scale(290, 79)
        ring_glow = QRadialGradient(0, 0, 1.2)
        ring_glow.setColorAt(0, QColor(Qt.transparent))
        ring_glow.setColorAt(.73, QColor(Qt.transparent))
        aura = QColor(colors["glow"])
        aura.setAlpha(round((20 + 32 * breath) * (1 if theme == "dark" else .65)))
        ring_glow.setColorAt(1 / 1.2, aura)
        ring_glow.setColorAt(1, QColor(Qt.transparent))
        p.setPen(Qt.NoPen)
        p.setBrush(ring_glow)
        p.drawEllipse(QRectF(-1.2, -1.2, 2.4, 2.4))
        p.restore()
        p.setBrush(Qt.NoBrush)
        ring = QColor(colors["glow"])
        ring.setAlpha(round(64 + 40 * breath))
        p.setPen(QPen(ring, 1))
        p.drawEllipse(QRectF(-290, -79, 580, 158))
        p.setPen(Qt.NoPen)
        angle = radians(120 + dx * 9 + hover * 5 + travel)
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
