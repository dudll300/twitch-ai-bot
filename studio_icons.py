"""Small native line icons for Studio; no external assets or icon font needed."""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

ICON_NAMES = (
    "sparkles", "plug", "sliders", "users", "messages", "activity", "flask",
    "book", "play", "stop", "save", "folder", "arrow-right",
)


def studio_icon(name: str, color: str = "#98A0B0", size: int = 20) -> QIcon:
    """Draw an antialiased 24-unit icon at double device resolution."""
    if name not in ICON_NAMES:
        raise ValueError(f"Unknown Studio icon: {name}")
    if size <= 0:
        raise ValueError("Icon size must be positive")
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.scale(size / 24, size / 24)
    painter.setPen(QPen(QColor(color), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.setBrush(Qt.NoBrush)

    def line(x1, y1, x2, y2):
        painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

    def polyline(points, close=False):
        path = QPainterPath(QPointF(*points[0]))
        for point in points[1:]:
            path.lineTo(QPointF(*point))
        if close:
            path.closeSubpath()
        painter.drawPath(path)

    def sparkle(x, y, extent):
        polyline(((x, y - extent), (x + extent * .3, y - extent * .3),
                  (x + extent, y), (x + extent * .3, y + extent * .3),
                  (x, y + extent), (x - extent * .3, y + extent * .3),
                  (x - extent, y), (x - extent * .3, y - extent * .3)), close=True)

    if name == "sparkles":
        sparkle(13.5, 13.5, 6.7)
        line(5.5, 3, 5.5, 8)
        line(3, 5.5, 8, 5.5)
        line(20, 2, 20, 6)
        line(18, 4, 22, 4)
    elif name == "plug":
        line(8, 3, 8, 8)
        line(16, 3, 16, 8)
        path = QPainterPath(QPointF(6, 8))
        path.lineTo(18, 8)
        path.lineTo(18, 11)
        path.cubicTo(18, 15, 15.5, 17, 12, 17)
        path.cubicTo(8.5, 17, 6, 15, 6, 11)
        path.closeSubpath()
        painter.drawPath(path)
        line(12, 17, 12, 21)
    elif name == "sliders":
        for x, y in ((5, 8), (12, 16), (19, 6)):
            line(x, 3, x, y - 2.3)
            line(x, y + 2.3, x, 21)
            painter.drawEllipse(QRectF(x - 2.3, y - 2.3, 4.6, 4.6))
    elif name == "users":
        painter.drawEllipse(QRectF(5, 4, 6, 6))
        path = QPainterPath(QPointF(2, 20))
        path.lineTo(2, 18)
        path.cubicTo(2, 12, 14, 12, 14, 18)
        path.lineTo(14, 20)
        painter.drawPath(path)
        path = QPainterPath(QPointF(16, 4.5))
        path.cubicTo(21, 4.5, 21, 10, 16, 10)
        painter.drawPath(path)
        path = QPainterPath(QPointF(17, 13.5))
        path.cubicTo(20, 14, 22, 15.5, 22, 18)
        path.lineTo(22, 20)
        painter.drawPath(path)
    elif name == "messages":
        polyline(((3, 4), (18, 4), (18, 14), (8, 14), (3, 18), (3, 4)))
        polyline(((21, 8), (21, 21), (16, 18), (11, 18)))
        line(7, 8, 14, 8)
        line(7, 11, 12, 11)
    elif name == "activity":
        polyline(((2, 12), (6, 12), (9, 4), (14, 20), (17, 12), (22, 12)))
    elif name == "flask":
        line(8, 3, 16, 3)
        polyline(((10, 3), (10, 10), (4.7, 18.2), (5.7, 21),
                  (18.3, 21), (19.3, 18.2), (14, 10), (14, 3)))
        line(7.6, 14, 16.4, 14)
        painter.drawEllipse(QRectF(10.4, 16.5, 1.2, 1.2))
        painter.drawEllipse(QRectF(14, 18, 1.1, 1.1))
    elif name == "book":
        path = QPainterPath(QPointF(12, 6))
        path.cubicTo(9, 3.5, 5, 3.5, 2.5, 4.5)
        path.lineTo(2.5, 19)
        path.cubicTo(6, 18, 9, 18.4, 12, 21)
        path.cubicTo(15, 18.4, 18, 18, 21.5, 19)
        path.lineTo(21.5, 4.5)
        path.cubicTo(19, 3.5, 15, 3.5, 12, 6)
        path.closeSubpath()
        painter.drawPath(path)
        line(12, 6, 12, 21)
    elif name == "play":
        polyline(((8, 4.5), (20, 12), (8, 19.5)), close=True)
    elif name == "stop":
        painter.drawRoundedRect(QRectF(5.5, 5.5, 13, 13), 2.5, 2.5)
    elif name == "save":
        polyline(((4, 3), (17, 3), (21, 7), (21, 21), (3, 21), (3, 4), (4, 3)))
        polyline(((7, 3), (7, 9), (16, 9), (16, 3)))
        polyline(((7, 21), (7, 14), (17, 14), (17, 21)))
    elif name == "folder":
        polyline(((3, 7), (3, 5), (9, 5), (11, 7), (21, 7), (21, 19), (3, 19), (3, 7)))
        line(3, 10, 21, 10)
    elif name == "arrow-right":
        line(4, 12, 20, 12)
        polyline(((14, 6), (20, 12), (14, 18)))
    painter.end()
    return QIcon(pixmap)
