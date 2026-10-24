"""Cached, continuous panel halos, clipped to visible scroll content."""
from functools import lru_cache
from math import exp, hypot

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QImage, QPainter, QLinearGradient
from PySide6.QtWidgets import QApplication, QWidget, QFrame, QAbstractScrollArea


@lru_cache(maxsize=2)
def _mask(peak):
    data = bytearray(224 * 224)
    axis = [abs((i + .5) / 2 - 56) - 16 for i in range(224)]
    for y, qy in enumerate(axis):
        for x, qx in enumerate(axis):
            # A 64px rounded panel inside 24px of transparent padding.
            distance = max(0, hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - 16)
            data[y * 224 + x] = round(peak * exp(-.5 * (distance / 9) ** 2))
    return QImage(data, 224, 224, 224, QImage.Format_Alpha8).copy()


@lru_cache(maxsize=2)
def _halo(theme):
    """A small 2x rounded-distance texture; generated once per palette."""
    from studio_theme import PALETTES
    c = PALETTES[theme]
    image = QImage(224, 224, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    gradient = QLinearGradient(0, 0, 224, 224)
    gradient.setColorAt(0, QColor(c["glow"]))
    gradient.setColorAt(1, QColor(c["glowSecondary"]))
    painter.fillRect(image.rect(), gradient)
    painter.setCompositionMode(QPainter.CompositionMode_DestinationIn)
    painter.drawImage(0, 0, _mask(20 if theme == "dark" else 12))
    painter.end()
    return image


def _draw_halo(painter, rect, image):
    bounds = rect.adjusted(-24, -24, 24, 24)
    corner = min(40, bounds.width() / 2, bounds.height() / 2)
    xs = (bounds.left(), bounds.left() + corner, bounds.right() - corner, bounds.right())
    ys = (bounds.top(), bounds.top() + corner, bounds.bottom() - corner, bounds.bottom())
    source = (0, 80, 144, 224)
    for row in range(3):
        for column in range(3):
            if row == column == 1:
                continue  # The actual panel covers the center.
            target = QRectF(xs[column], ys[row], xs[column + 1] - xs[column], ys[row + 1] - ys[row])
            crop = QRectF(source[column], source[row], source[column + 1] - source[column], source[row + 1] - source[row])
            painter.drawImage(target, image, crop)


class GlowSurface(QWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAttribute(Qt.WA_OpaquePaintEvent)

    def paintEvent(self, event):
        from studio_theme import PALETTES
        theme = QApplication.instance().property("studioTheme") or "dark"
        painter = QPainter(self)
        # Halos extend beyond child bounds. Always erase their previous pixels,
        # including after native scroll-copy and a child's move/hide/resize.
        painter.fillRect(event.rect(), QColor(PALETTES[theme]["bg"]))
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        image = _halo(theme)
        for panel in self.findChildren(QFrame, "card"):
            if not panel.isVisibleTo(self):
                continue
            ancestor = panel.parentWidget()
            chain = []
            while ancestor is not None and ancestor is not self:
                if isinstance(ancestor, GlowSurface) or ancestor.objectName() == "card":
                    break
                chain.append(ancestor)
                ancestor = ancestor.parentWidget()
            if ancestor is not self:
                continue
            position = panel.mapTo(self, panel.rect().topLeft())
            rect = QRectF(position.x(), position.y(), panel.width(), panel.height())
            if not rect.adjusted(-24, -24, 24, 24).intersects(QRectF(event.rect())):
                continue
            clip = QRectF(self.rect())
            for parent in chain:
                if parent.property("clipPanelGlow"):
                    origin = parent.mapTo(self, parent.rect().topLeft())
                    visible = QRectF(origin.x(), origin.y(), parent.width(), parent.height()).adjusted(-24, -24, 24, 0)
                    clip = clip.intersected(visible)
                if not isinstance(parent, QAbstractScrollArea):
                    continue
                viewport = parent.viewport()
                origin = viewport.mapTo(self, viewport.rect().topLeft())
                visible = QRectF(origin.x(), origin.y(), viewport.width(), viewport.height())
                # Allow the halo into pane gaps only along unclipped edges.
                visible.adjust(-24 if rect.left() >= visible.left() else 0,
                               -24 if rect.top() >= visible.top() else 0,
                               24 if rect.right() <= visible.right() else 0,
                               24 if rect.bottom() <= visible.bottom() else 0)
                clip = clip.intersected(visible)
            painter.save()
            painter.setClipRect(clip, Qt.IntersectClip)
            _draw_halo(painter, rect, image)
            painter.restore()
