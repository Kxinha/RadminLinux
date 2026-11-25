"""
icons.py - draws the clone's icons (logo/tray, signal, power) with QPainter,
without relying on external files. Colors based on the real Radmin VPN GUI.
"""
from __future__ import annotations
from PySide6.QtGui import QPixmap, QPainter, QColor, QBrush, QPen, QPainterPath, QLinearGradient, QIcon
from PySide6.QtCore import Qt, QRectF, QPointF

CYAN = QColor("#39b6d8")
CYAN_DK = QColor("#2b8fab")
GREEN = QColor("#54c15a")
RED = QColor("#c0504d")
GREY = QColor("#6b7378")


def _aa(p: QPainter) -> None:
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)


def logo_pixmap(size: int = 64) -> QPixmap:
    """Escudo/triangulo ciano estilo Radmin VPN."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    _aa(p)
    s = size
    # escudo arredondado
    path = QPainterPath()
    path.moveTo(s * 0.5, s * 0.08)
    path.lineTo(s * 0.90, s * 0.24)
    path.lineTo(s * 0.90, s * 0.55)
    path.quadTo(s * 0.90, s * 0.86, s * 0.5, s * 0.96)
    path.quadTo(s * 0.10, s * 0.86, s * 0.10, s * 0.55)
    path.lineTo(s * 0.10, s * 0.24)
    path.closeSubpath()
    g = QLinearGradient(0, 0, 0, s)
    g.setColorAt(0, CYAN)
    g.setColorAt(1, CYAN_DK)
    p.setBrush(QBrush(g))
    p.setPen(Qt.NoPen)
    p.drawPath(path)
    # "V" branco central
    pen = QPen(QColor("#ffffff"))
    pen.setWidthF(s * 0.08)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawPolyline([QPointF(s*0.32, s*0.36), QPointF(s*0.5, s*0.66), QPointF(s*0.68, s*0.36)])
    p.end()
    return pm


def logo_icon(size: int = 64) -> QIcon:
    return QIcon(logo_pixmap(size))


def signal_pixmap(online: bool, size: int = 16) -> QPixmap:
    """Radmin's member icon: four thin rising green bars when connected; a small
    x and grey bars when not."""
    w, h = 20, size
    pm = QPixmap(w, h)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    _aa(p)
    p.setPen(Qt.NoPen)
    bw, gap, x0 = 2.4, 1.6, 7.0
    base = h * 0.78
    for i in range(4):
        bh = h * (0.18 + 0.14 * i)
        p.setBrush(QColor("#00ab00") if online else QColor("#59636e"))
        p.drawRect(QRectF(x0 + i * (bw + gap), base - bh, bw, bh))
    if not online:
        pen = QPen(QColor("#c3cbd3")); pen.setWidthF(1.3)
        p.setPen(pen)
        c, r = QPointF(3.0, base - 2.6), 2.0
        p.drawLine(QPointF(c.x() - r, c.y() - r), QPointF(c.x() + r, c.y() + r))
        p.drawLine(QPointF(c.x() + r, c.y() - r), QPointF(c.x() - r, c.y() + r))
    p.end()
    return pm


def power_pixmap(on: bool, size: int = 56) -> QPixmap:
    """Botao circular de power do card do no."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    _aa(p)
    ring = GREEN if on else GREY
    pen = QPen(ring)
    pen.setWidthF(size * 0.08)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    m = size * 0.16
    p.drawArc(QRectF(m, m, size - 2*m, size - 2*m), int(-120*16), int(300*16))
    # haste
    p.drawLine(QPointF(size/2, size*0.20), QPointF(size/2, size*0.46))
    p.end()
    return pm


_TRAY_CACHE: dict = {}


def tray_icon(state: str) -> QIcon:
    """Logo with a status dot: on = green, warn = amber (booting/disconnected),
    off = the logo greyed out. Cached, so a refresh never repaints it."""
    if state in _TRAY_CACHE:
        return _TRAY_CACHE[state]
    icon = QIcon()
    for size in (16, 22, 24, 32, 48, 64):
        pm = logo_pixmap(size)
        p = QPainter(pm)
        _aa(p)
        if state == "off":
            p.setCompositionMode(QPainter.CompositionMode_SourceAtop)
            p.fillRect(pm.rect(), QColor(40, 44, 48, 170))
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        else:
            r = size * 0.36
            col = GREEN if state == "on" else QColor("#e0a030")
            p.setPen(QPen(QColor("#1d2023"), max(1.0, size * 0.06)))
            p.setBrush(col)
            p.drawEllipse(QRectF(size - r - 0.5, size - r - 0.5, r, r))
        p.end()
        icon.addPixmap(pm)
    _TRAY_CACHE[state] = icon
    return icon
