"""Soft panel lighting painted behind native controls, without drop-shadow effects."""
from PySide6.QtCore import Qt, QRectF
from math import exp
from PySide6.QtGui import QColor, QPainter, QLinearGradient, QPen
from PySide6.QtWidgets import QWidget, QFrame
from studio_controls import colors


class GlowSurface(QWidget):
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(Qt.NoBrush)
        c = colors()
        for panel in self.findChildren(QFrame, "card"):
            if not panel.isVisibleTo(self):
                continue
            ancestor = panel.parentWidget()
            while ancestor is not None and ancestor is not self:
                if isinstance(ancestor, GlowSurface) or ancestor.objectName() == "card":
                    break
                ancestor = ancestor.parentWidget()
            if ancestor is not self:
                continue
            position = panel.mapTo(self, panel.rect().topLeft())
            rect = QRectF(position.x(), position.y(), panel.width(), panel.height())
            if not rect.adjusted(-28, -28, 28, 28).intersects(QRectF(event.rect())):
                continue
            # Dense translucent contours form a smooth falloff around every
            # edge, including the space between adjacent cards.
            for spread in range(20, 0, -1):
                gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
                alpha = round(22 * exp(-spread * spread / 80))
                for stop, token in ((0, "glow"), (1, "glowSecondary")):
                    tint = QColor(c[token])
                    tint.setAlpha(alpha)
                    gradient.setColorAt(stop, tint)
                painter.setPen(QPen(gradient, 2))
                painter.drawRoundedRect(rect.adjusted(-spread, -spread, spread, spread), 16 + spread, 16 + spread)
