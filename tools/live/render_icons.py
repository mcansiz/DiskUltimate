"""Live masaustu simgelerini cizer (GELISTIRME ARACI, ADR 0101).

    QT_QPA_PLATFORM=offscreen python3 tools/live/render_icons.py

Cikti: tools/live/base/config/includes.chroot/usr/share/diskultimate-live/icons/
  diskultimate.png  uygulamanin marka ikonu (ui/resources/app-icon-256.png)
  terminal.png, reboot.png, power.png, folder.png

Debian 13'un Adwaita temasinda (48.x) ucbirim / yeniden baslat / kapat icin
renkli simge yok (yalnizca `-symbolic`); klasorun de PNG'si yalnizca 16 px
(48 px'lik masaustunde bulanik). Bu yuzden simgeler uygulamanin kendi ikonlariyla
ayni yontemle, QPainter ile cizilir (ADR 0025) ve PNG olarak depoya girer;
taban yalnizca bu dosyalar degisince yeniden kurulur.
"""
from __future__ import annotations

import math
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))

from PyQt5.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PyQt5.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

OUT = os.path.join(ROOT, "tools", "live", "base", "config", "includes.chroot",
                   "usr", "share", "diskultimate-live", "icons")
SIZE = 96
APP_ICON = os.path.join(ROOT, "src", "diskultimate", "ui", "resources",
                        "app-icon-256.png")


def _tile(p: QPainter, color: str) -> None:
    """Koyu masaustunde okunur kalsin diye yuvarlak koseli renkli zemin."""
    m = SIZE * 0.06
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawRoundedRect(QRectF(m, m, SIZE - 2 * m, SIZE - 2 * m),
                      SIZE * 0.18, SIZE * 0.18)


def _white_pen(p: QPainter, width: float) -> None:
    pen = QPen(QColor("#ffffff"), width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)


def draw_terminal(p: QPainter) -> None:
    _tile(p, "#4a5566")
    _white_pen(p, SIZE * 0.07)
    s = SIZE
    p.drawPolyline(QPointF(s * 0.27, s * 0.34), QPointF(s * 0.45, s * 0.50),
                   QPointF(s * 0.27, s * 0.66))
    p.drawLine(QPointF(s * 0.52, s * 0.68), QPointF(s * 0.74, s * 0.68))


def _arc_with_head(p: QPainter, cx: float, cy: float, r: float,
                   start: float, span: float) -> None:
    box = QRectF(cx - r, cy - r, 2 * r, 2 * r)
    path = QPainterPath()
    path.arcMoveTo(box, start)
    path.arcTo(box, start, span)
    p.drawPath(path)
    # Ok basi: yayin bittigi yerde, gidis yonune teget (ui/icons._arc_arrowhead).
    a = math.radians(start + span)
    tip = QPointF(cx + r * math.cos(a), cy - r * math.sin(a))
    d = math.copysign(1.0, span)
    tangent = (-math.sin(a) * d, -math.cos(a) * d)
    normal = (math.cos(a), -math.sin(a))
    h = SIZE * 0.13
    back = QPointF(tip.x() - tangent[0] * h, tip.y() - tangent[1] * h)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#ffffff"))
    p.drawPolygon(QPointF(tip.x() + tangent[0] * h * 0.6, tip.y() + tangent[1] * h * 0.6),
                  QPointF(back.x() + normal[0] * h * 0.7, back.y() + normal[1] * h * 0.7),
                  QPointF(back.x() - normal[0] * h * 0.7, back.y() - normal[1] * h * 0.7))


def draw_reboot(p: QPainter) -> None:
    _tile(p, "#1769c7")
    _white_pen(p, SIZE * 0.08)
    _arc_with_head(p, SIZE / 2, SIZE / 2, SIZE * 0.25, 70.0, 290.0)


def draw_power(p: QPainter) -> None:
    _tile(p, "#c0392b")
    _white_pen(p, SIZE * 0.08)
    c, r = SIZE / 2, SIZE * 0.24
    box = QRectF(c - r, c - r + SIZE * 0.04, 2 * r, 2 * r)
    path = QPainterPath()
    path.arcMoveTo(box, 120.0)
    path.arcTo(box, 120.0, 300.0)
    p.drawPath(path)
    p.drawLine(QPointF(c, SIZE * 0.22), QPointF(c, SIZE * 0.50))


def draw_folder(p: QPainter) -> None:
    _tile(p, "#c98a1b")
    _white_pen(p, SIZE * 0.06)
    s = SIZE
    path = QPainterPath()
    path.moveTo(s * 0.24, s * 0.70)
    path.lineTo(s * 0.24, s * 0.30)
    path.lineTo(s * 0.42, s * 0.30)
    path.lineTo(s * 0.48, s * 0.37)
    path.lineTo(s * 0.76, s * 0.37)
    path.lineTo(s * 0.76, s * 0.70)
    path.closeSubpath()
    p.drawPath(path)
    p.drawLine(QPointF(s * 0.24, s * 0.46), QPointF(s * 0.76, s * 0.46))


def render(name: str, painter_fn) -> str:
    img = QImage(SIZE, SIZE, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    painter_fn(p)
    p.end()
    path = os.path.join(OUT, name)
    if not img.save(path, "PNG"):
        raise SystemExit(f"HATA: yazilamadi: {path}")
    return path


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841
    os.makedirs(OUT, exist_ok=True)
    shutil.copy2(APP_ICON, os.path.join(OUT, "diskultimate.png"))
    for name, fn in (("terminal.png", draw_terminal), ("reboot.png", draw_reboot),
                     ("power.png", draw_power), ("folder.png", draw_folder)):
        print(render(name, fn))
    return 0


if __name__ == "__main__":
    sys.exit(main())
