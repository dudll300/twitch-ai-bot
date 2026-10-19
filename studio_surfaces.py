"""Soft panel lighting painted behind native controls, without drop-shadow effects."""
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter, QRadialGradient
from PySide6.QtWidgets import QWidget, QFrame
from studio_controls import colors


class GlowSurface(QWidget):
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
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
            # Paint soft elliptical light behind the lower edge. The card hides
            # its center; there is no blur of native controls or text.
            for position, token in ((.30, "glow"), (.72, "glowSecondary")):
                painter.save()
                painter.translate(rect.left() + rect.width() * position, rect.bottom() - 5)
                painter.scale(max(50, rect.width() * .55), 28)
                gradient = QRadialGradient(0, 0, 1)
                tint = QColor(c[token])
                tint.setAlpha(48)
                gradient.setColorAt(0, tint)
                tint.setAlpha(20)
                gradient.setColorAt(.48, tint)
                gradient.setColorAt(1, QColor(Qt.transparent))
                painter.setBrush(gradient)
                painter.drawEllipse(QRectF(-1, -1, 2, 2))
                painter.restore()
