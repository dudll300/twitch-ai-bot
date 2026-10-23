"""Readable native event and conversation rows, matching the Orbit prototype."""
from PySide6.QtCore import Qt, QSize, QRectF, QPointF, QObject, QEvent, QTimer
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QTextLayout, QTextOption, QRegion
from PySide6.QtWidgets import QStyledItemDelegate, QStyle, QAbstractItemView
from studio_controls import colors
from studio_motion import SpringMotion


class RowSelectionMotion(QObject):
    """A travelling native row surface, on the same spring as the icon rail."""
    def __init__(self, view):
        super().__init__(view)
        self.view, self.rect = view, QRectF()
        self.motion = SpringMotion(self, (0, 0, 0, 0), self._advance, frequency=25, tolerance=.04)
        view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        view.currentRowChanged.connect(self.select)
        view.viewport().installEventFilter(self)

    def _advance(self, values):
        offset = self.view.verticalScrollBar().value()
        previous = self.rect.translated(0, -offset).adjusted(-2, -2, 2, 2).toAlignedRect()
        self.rect = QRectF(*values)
        current = self.rect.translated(0, -offset).adjusted(-2, -2, 2, 2).toAlignedRect()
        self.view.viewport().update(QRegion(previous) | QRegion(current))

    def reset(self):
        self.motion.reset((0, 0, 0, 0))

    def select(self, row, *, immediate=False):
        item = self.view.item(row) if row >= 0 else None
        if item is None:
            self.reset()
            return
        rect = QRectF(self.view.visualItemRect(item))
        rect.translate(0, self.view.verticalScrollBar().value())
        self.motion.to((rect.x(), rect.y(), rect.width(), rect.height()),
                       immediate=immediate or self.rect.isEmpty() or not self.view.isVisible())

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Resize:
            QTimer.singleShot(0, self, lambda: self.select(self.view.currentRow()))
        elif event.type() == QEvent.Hide:
            self.motion.finish()
        return False

    def paint(self, painter, bounds):
        rect = self.rect.translated(0, -self.view.verticalScrollBar().value()).adjusted(1, 1, -1, -1)
        if rect.isEmpty() or not rect.intersects(QRectF(bounds)):
            return
        c = colors()
        painter.save()
        painter.setClipRect(bounds, Qt.IntersectClip)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(c["selected"]))
        painter.drawRoundedRect(rect, 12, 12)
        painter.setPen(QPen(QColor(c["accent"]), 2, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(rect.left(), rect.top() + 12), QPointF(rect.left(), rect.bottom() - 12))
        painter.restore()


def text_layout(text, font, width):
    layout = QTextLayout(text, font)
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(option)
    layout.beginLayout()
    height = 0
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(max(40, width))
        line.setPosition(QPointF(0, height))
        height += line.height()
    layout.endLayout()
    return layout, height


class OrbitRowDelegate(QStyledItemDelegate):
    def __init__(self, parent, mode="record"):
        super().__init__(parent)
        self.mode = mode
        self.selection = RowSelectionMotion(parent) if mode in ("record", "profile") else None

    def sizeHint(self, option, index):
        data = index.data(Qt.UserRole + 1) or {}
        if self.mode == "chat":
            font = QFont(option.font)
            font.setPixelSize(14)
            _, height = text_layout(data.get("body", ""), font, max(150, option.rect.width() - 92))
            return QSize(200, int(height) + 62)
        return QSize(200, 90 if self.mode == "record" else 80)

    def paint(self, p, option, index):
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        c, rect = colors(), option.rect
        data = index.data(Qt.UserRole + 1) or {}
        raw = str(index.data() or "").splitlines()
        selected = bool(option.state & QStyle.State_Selected)
        bot = data.get("bot", False)
        if self.selection is not None:
            if option.state & QStyle.State_MouseOver and not selected:
                p.fillRect(rect, QColor(c["raised"]))
            self.selection.paint(p, rect)
        elif selected or bot or option.state & QStyle.State_MouseOver:
            p.fillRect(rect, QColor(c["selected"] if selected or bot else c["raised"]))
        if (selected or bot) and self.selection is None:
            p.fillRect(rect.left(), rect.top(), 2, rect.height(), QColor(c["accent"]))
        p.setPen(QPen(QColor(c["border"]), 1))
        p.drawLine(rect.bottomLeft(), rect.bottomRight())
        font = QFont(option.font)
        font.setPixelSize(12)
        p.setFont(font)
        left = rect.left() + 20
        top = rect.top() + 18
        p.setPen(QColor(c["muted"]))
        if self.mode == "record":
            p.drawText(left, top + 14, data.get("time", ""))
            left += 48
        elif self.mode in ("chat", "profile"):
            avatar = QRectF(left, top, 28, 28)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(c["selected"] if not bot else c["primary"]))
            p.drawRoundedRect(avatar, 10, 10)
            p.setPen(QColor(c["accent"] if not bot else c["primaryText"]))
            name = data.get("author", raw[0] if raw else "")
            p.drawText(avatar, Qt.AlignCenter, name[:1].upper() or "O")
            left += 40
        width = max(30, rect.right() - left - 18)
        font.setPixelSize(14)
        font.setWeight(QFont.Medium)
        p.setFont(font)
        p.setPen(QColor(c["text"]))
        title = data.get("title", raw[0] if raw else "")
        if self.mode == "chat":
            title = data.get("author", "Twitch AI Bot") + ("  ·  BOT" if bot else "")
        title = p.fontMetrics().elidedText(title, Qt.ElideRight, width)
        p.drawText(left, top + 15, title)
        font.setWeight(QFont.Normal)
        font.setPixelSize(12 if self.mode != "chat" else 14)
        p.setFont(font)
        p.setPen(QColor(c["muted"] if self.mode != "chat" else c["text"]))
        if self.mode == "chat":
            layout, _ = text_layout(data.get("body", ""), font, width)
            layout.draw(p, QPointF(left, top + 26))
        else:
            subtitle = data.get("subtitle", " · ".join(raw[1:]))
            p.drawText(left, top + 39, p.fontMetrics().elidedText(subtitle, Qt.ElideRight, width))
            if self.mode == "record" and data.get("date"):
                font.setPixelSize(11)
                p.setFont(font)
                p.drawText(left, top + 57, data["date"])
        p.restore()


class ModelChoiceDelegate(QStyledItemDelegate):
    """Native checkable items, with the same pale-blue indicator as switches."""
    def sizeHint(self, option, index):
        return QSize(200, 46)

    def paint(self, p, option, index):
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        c, rect = colors(), option.rect
        enabled = bool(index.flags() & Qt.ItemIsEnabled)
        checked = index.data(Qt.CheckStateRole) == Qt.Checked.value
        if option.state & (QStyle.State_Selected | QStyle.State_MouseOver):
            p.fillRect(rect, QColor(c["selected"]))
        if not enabled:
            p.setOpacity(.45)
        check = QRectF(rect.left() + 12, rect.center().y() - 10, 20, 20)
        p.setBrush(QColor(c["primary"] if checked else c["input"]))
        p.setPen(QPen(QColor(c["primary"] if checked else c["border"]), 1))
        p.drawRoundedRect(check, 6, 6)
        if checked:
            x, y = check.center().x(), check.center().y()
            p.setPen(QPen(QColor(c["primaryText"]), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawLine(QPointF(x - 4, y), QPointF(x - 1, y + 3))
            p.drawLine(QPointF(x - 1, y + 3), QPointF(x + 5, y - 4))
        text_rect = rect.adjusted(44, 0, -12, 0)
        p.setFont(option.font)
        p.setPen(QColor(c["text"]))
        p.drawText(text_rect, Qt.AlignVCenter, option.fontMetrics.elidedText(str(index.data() or ""), Qt.ElideMiddle, text_rect.width()))
        p.restore()
