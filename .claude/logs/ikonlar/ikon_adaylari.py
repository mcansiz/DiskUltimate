"""Ikon adaylari karsilastirma sayfasi (yalnizca onizleme)."""
import math, os, sys
sys.path.insert(0, "/home/pc/Belgeler/GitHub/DiskUltimate/src")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtCore import QPointF, QRectF, Qt, QRect
from PyQt5.QtGui import (QColor, QFont, QImage, QPainter, QPainterPath, QPen,
                         QPolygonF, QLinearGradient, QRadialGradient)
from PyQt5.QtWidgets import QApplication
app = QApplication(sys.argv)
from diskultimate.ui import icons, theme

def pen(p, c, w):
    q = QPen(QColor(c), w); q.setCapStyle(Qt.RoundCap); q.setJoinStyle(Qt.RoundJoin); p.setPen(q)

# ---------------- yenile ----------------
def refresh_old(p, u, dark):
    color = icons._tone(icons.BLUE, dark)
    icons._pen(p, color, u, 1.8); p.setBrush(Qt.NoBrush)
    path = QPainterPath(); path.moveTo(13.0*u, 8.0*u)
    path.arcTo(QRectF(3*u, 3*u, 10*u, 10*u), 0, 280); p.drawPath(path)
    p.setPen(Qt.NoPen); p.setBrush(color)
    p.drawPolygon(QPolygonF([QPointF(13*u, 2.4*u), QPointF(15.2*u, 6.2*u), QPointF(10.6*u, 6*u)]))

def refresh_new(p, u, dark):
    icons._refresh(p, u, dark)

def refresh_single(p, u, dark):
    """Tek yay + tek ok (daha sade)."""
    color = icons._tone(icons.BLUE, dark)
    cx = cy = 8*u; r = 5.2*u; box = QRectF(cx-r, cy-r, 2*r, 2*r)
    icons._pen(p, color, u, 1.8); p.setBrush(Qt.NoBrush)
    path = QPainterPath(); path.arcMoveTo(box, 70); path.arcTo(box, 70, 290); p.drawPath(path)
    icons._arc_arrowhead(p, u, color, cx, cy, r, 360)

# ---------------- disk ----------------
def disk_d1(p, u, dark):          # mevcut
    icons._disk(p, u, dark)

def disk_d2(p, u, dark):          # HDD on gorunus: kasa + plaka + kol
    body = QColor("#8a94a0") if not dark else QColor("#aab3bd")
    p.setPen(Qt.NoPen)
    g = QLinearGradient(0, 1*u, 0, 15*u); g.setColorAt(0, body.lighter(125)); g.setColorAt(1, body.darker(115))
    p.setBrush(g); p.drawRoundedRect(QRectF(2.2*u, 1.2*u, 11.6*u, 13.6*u), 1.6*u, 1.6*u)
    p.setBrush(QColor("#e9edf1")); p.drawEllipse(QRectF(3.6*u, 2.4*u, 8.8*u, 8.8*u))
    p.setBrush(QColor("#9aa4ae")); p.drawEllipse(QRectF(7.1*u, 5.9*u, 1.8*u, 1.8*u))
    pen(p, "#3b4652", 1.1*u); p.drawLine(QPointF(11.6*u, 12.6*u), QPointF(8.6*u, 7.4*u))
    p.setPen(Qt.NoPen); p.setBrush(QColor("#2e9e4f")); p.drawEllipse(QRectF(3.4*u, 12.4*u, 1.6*u, 1.6*u))

def disk_d3(p, u, dark):          # SSD: kart + yongalar
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#34495e") if not dark else QColor("#5d7186"))
    p.drawRoundedRect(QRectF(1.4*u, 3.4*u, 13.2*u, 9.2*u), 1.4*u, 1.4*u)
    p.setBrush(QColor("#1769c7")); p.drawRect(QRectF(1.4*u, 3.4*u+0.0, 13.2*u, 2.2*u))
    p.setBrush(QColor("#cfd6dd"))
    for i in range(3):
        p.drawRoundedRect(QRectF((2.8+i*3.9)*u, 7.0*u, 3.0*u, 3.8*u), 0.4*u, 0.4*u)

def disk_d4(p, u, dark):          # surucu yuvasi: yatay kutu + LED + yarik
    base = QColor("#8a94a0") if not dark else QColor("#aab3bd")
    p.setPen(Qt.NoPen)
    g = QLinearGradient(0, 4*u, 0, 12*u); g.setColorAt(0, base.lighter(130)); g.setColorAt(1, base.darker(120))
    p.setBrush(g); p.drawRoundedRect(QRectF(1.0*u, 4.6*u, 14*u, 7.2*u), 1.3*u, 1.3*u)
    p.setBrush(QColor(0, 0, 0, 60)); p.drawRect(QRectF(1.0*u, 10.2*u, 14*u, 1.6*u))
    pen(p, "#4a5561", 0.9*u)
    for x in (3.0, 4.6, 6.2): p.drawLine(QPointF(x*u, 6.4*u), QPointF(x*u, 9.0*u))
    p.setPen(Qt.NoPen); p.setBrush(QColor("#2ecc71")); p.drawEllipse(QRectF(11.6*u, 7.0*u, 1.9*u, 1.9*u))

def disk_d5(p, u, dark):          # yigin plakalar (silindir, 3 katman)
    c = QColor("#6d7c8c") if not dark else QColor("#9aa7b4")
    p.setPen(Qt.NoPen)
    for i, y in enumerate((9.4, 6.4, 3.4)):
        p.setBrush(c.darker(110 + i*0)); p.drawRoundedRect(QRectF(2.2*u, (y+1.2)*u, 11.6*u, 2.6*u), 0.4*u, 0.4*u)
        p.setBrush(c.lighter(130)); p.drawEllipse(QRectF(2.2*u, y*u, 11.6*u, 2.6*u))
    p.setBrush(QColor("#2ecc71")); p.drawEllipse(QRectF(11.0*u, 11.2*u, 1.4*u, 1.4*u))

# ---------------- linux ----------------
def linux_l1(p, u, dark):         # mevcut
    theme._draw_linux(p, int(16*u))

def linux_l2(p, u, dark):         # Tux: yuvarlak kafa+govde, gaga, sari ayaklar
    black = QColor("#1f2328"); white = QColor("#f4f4f4"); yellow = QColor("#f2b61c")
    p.setPen(Qt.NoPen)
    p.setBrush(black); p.drawEllipse(QRectF(3.4*u, 1.0*u, 9.2*u, 8.6*u))
    p.drawEllipse(QRectF(2.6*u, 5.0*u, 10.8*u, 9.6*u))
    p.setBrush(white); p.drawEllipse(QRectF(4.8*u, 6.6*u, 6.4*u, 7.4*u))
    p.drawEllipse(QRectF(5.4*u, 2.8*u, 2.2*u, 2.6*u)); p.drawEllipse(QRectF(8.4*u, 2.8*u, 2.2*u, 2.6*u))
    p.setBrush(black); p.drawEllipse(QRectF(6.2*u, 3.6*u, 1.0*u, 1.2*u)); p.drawEllipse(QRectF(8.9*u, 3.6*u, 1.0*u, 1.2*u))
    p.setBrush(yellow)
    p.drawEllipse(QRectF(6.5*u, 5.2*u, 3.0*u, 1.8*u))
    p.drawEllipse(QRectF(2.4*u, 13.0*u, 4.6*u, 2.4*u)); p.drawEllipse(QRectF(9.0*u, 13.0*u, 4.6*u, 2.4*u))

def linux_l3(p, u, dark):         # terminal: koyu kare + ">_"
    p.setPen(Qt.NoPen); p.setBrush(QColor("#2d3238"))
    p.drawRoundedRect(QRectF(1.2*u, 2.0*u, 13.6*u, 12.0*u), 2.0*u, 2.0*u)
    p.setBrush(QColor("#f2b61c")); p.drawRect(QRectF(1.2*u, 2.0*u, 13.6*u, 0.01))
    pen(p, "#f2b61c", 1.6*u)
    p.drawPolyline(QPolygonF([QPointF(4.0*u, 6.0*u), QPointF(6.6*u, 8.2*u), QPointF(4.0*u, 10.4*u)]))
    pen(p, "#e8eaed", 1.6*u); p.drawLine(QPointF(8.0*u, 10.6*u), QPointF(11.8*u, 10.6*u))

def linux_l4(p, u, dark):         # yalnizca penguen kafasi (16 px'te okunur)
    black = QColor("#1f2328"); white = QColor("#f4f4f4"); yellow = QColor("#f2b61c")
    p.setPen(Qt.NoPen); p.setBrush(black); p.drawEllipse(QRectF(1.6*u, 1.4*u, 12.8*u, 13.2*u))
    p.setBrush(white); p.drawEllipse(QRectF(3.4*u, 5.8*u, 9.2*u, 8.2*u))
    p.drawEllipse(QRectF(4.4*u, 3.4*u, 3.0*u, 3.6*u)); p.drawEllipse(QRectF(8.6*u, 3.4*u, 3.0*u, 3.6*u))
    p.setBrush(black); p.drawEllipse(QRectF(5.4*u, 4.4*u, 1.4*u, 1.8*u)); p.drawEllipse(QRectF(9.2*u, 4.4*u, 1.4*u, 1.8*u))
    p.setBrush(yellow); p.drawPolygon(QPolygonF([QPointF(6.0*u, 7.4*u), QPointF(10.0*u, 7.4*u), QPointF(8.0*u, 10.2*u)]))

def linux_l5(p, u, dark):         # sari daire rozet + siyah penguen silueti
    p.setPen(Qt.NoPen); p.setBrush(QColor("#f2b61c")); p.drawEllipse(QRectF(0.8*u, 0.8*u, 14.4*u, 14.4*u))
    black = QColor("#1f2328"); white = QColor("#fafafa")
    p.setBrush(black); p.drawEllipse(QRectF(5.4*u, 2.6*u, 5.2*u, 5.0*u)); p.drawEllipse(QRectF(4.4*u, 5.6*u, 7.2*u, 8.0*u))
    p.setBrush(white); p.drawEllipse(QRectF(5.8*u, 7.4*u, 4.4*u, 5.6*u))
    p.setBrush(QColor("#e67e22")); p.drawPolygon(QPolygonF([QPointF(7.0*u, 5.4*u), QPointF(9.0*u, 5.4*u), QPointF(8.0*u, 6.6*u)]))

ROWS = [
    ("Yenile", [("mevcut", refresh_old), ("A: iki yay (uygulandi)", refresh_new), ("B: tek yay", refresh_single)]),
    ("Disk", [("D1 mevcut silindir", disk_d1), ("D2 HDD on yuz", disk_d2), ("D3 SSD", disk_d3),
              ("D4 surucu yuvasi", disk_d4), ("D5 plaka yigini", disk_d5)]),
    ("Linux", [("L1 mevcut", linux_l1), ("L2 Tux (tam)", linux_l2), ("L3 terminal", linux_l3),
               ("L4 penguen kafasi", linux_l4), ("L5 sari rozet", linux_l5)]),
]
SIZES = (16, 24, 32, 48)
CELL_W, CELL_H = 190, 150
W = 110 + CELL_W * 5
H = 20 + sum(CELL_H for _ in ROWS) * 1
img = QImage(W, H, QImage.Format_ARGB32); img.fill(QColor("#ffffff"))
p = QPainter(img); p.setRenderHint(QPainter.Antialiasing)
f = QFont("DejaVu Sans", 9); p.setFont(f)
y = 10
for title, items in ROWS:
    p.setPen(QColor("#222")); fb = QFont(f); fb.setBold(True); fb.setPointSize(11); p.setFont(fb)
    p.drawText(QRect(8, y, 100, CELL_H), Qt.AlignVCenter, title); p.setFont(f)
    for i, (name, fn) in enumerate(items):
        x0 = 110 + i * CELL_W
        for row, (bg, dark) in enumerate((("#ffffff", False), ("#2b2f35", True))):
            yy = y + 18 + row * 58
            p.fillRect(QRect(x0, yy, CELL_W - 10, 54), QColor(bg))
            xx = x0 + 6
            for s in SIZES:
                sub = QImage(s, s, QImage.Format_ARGB32); sub.fill(Qt.transparent)
                q = QPainter(sub); q.setRenderHint(QPainter.Antialiasing); fn(q, s / 16.0, dark); q.end()
                p.drawImage(xx, yy + (54 - s) // 2, sub); xx += s + 8
        p.setPen(QColor("#333")); p.drawText(QRect(x0, y, CELL_W - 10, 16), Qt.AlignLeft, name)
    y += CELL_H
p.end()
out = sys.argv[1]
img.save(out); print(out)
