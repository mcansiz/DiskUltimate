"""Tum ikonlarin alternatif katalogu (yalnizca onizleme; uygulamaya baglanmaz).

Uc aile yan yana cizilir:
  A  mevcut      — `ui/icons.py` ve `ui/theme.py` icindeki cizim
  B  kutucuk     — anlamsal renkli yuvarlak kare + beyaz sembol
  C  cizgi       — notr ince hat, anlamsal renk yalnizca vurguda

B ve C **ayni sembol geometrisini** paylasir (`G_*` islevleri); boylece hangi
aile secilirse secilsin set kendi icinde tutarli kalir.

    python3 .claude/logs/ikonlar/ikon_katalogu.py <cikis_klasoru>
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPointF, QRect, QRectF, Qt          # noqa: E402
from PyQt5.QtGui import (QColor, QFont, QImage, QPainter,       # noqa: E402
                         QPainterPath, QPen, QPolygonF)
from PyQt5.QtWidgets import QApplication                        # noqa: E402

app = QApplication(sys.argv)
from diskultimate.ui import icons, theme                       # noqa: E402

GREEN, RED, ORANGE, BLUE = "#2e9e4f", "#c0392b", "#e08b12", "#1769c7"
PURPLE, TEAL, GREY, YELLOW = "#7a5bd6", "#2f9e9e", "#6b7684", "#e0a800"
WIN, TUX = "#0078D4", "#e8b71a"


# ==========================================================================
# Sembol cizim baglami: ink = ana hat, acc = vurgu, fill = ic dolgu var mi
# ==========================================================================
class Pen:
    def __init__(self, p, u, ink, acc, solid):
        self.p, self.u, self.ink, self.acc, self.solid = p, u, QColor(ink), QColor(acc), solid

    def P(self, x, y):
        return QPointF(x * self.u, y * self.u)

    def stroke(self, color=None, w=1.5):
        pen = QPen(QColor(color) if color else self.ink, max(1.0, w * self.u))
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        self.p.setPen(pen)
        self.p.setBrush(Qt.NoBrush)

    def fill(self, color=None):
        self.p.setPen(Qt.NoPen)
        self.p.setBrush(QColor(color) if color else self.ink)

    def rect(self, x, y, w, h, r=1.0, color=None, filled=False, width=1.5):
        (self.fill if filled else lambda c: self.stroke(c, width))(color)
        self.p.drawRoundedRect(QRectF(x * self.u, y * self.u, w * self.u, h * self.u),
                               r * self.u, r * self.u)

    def ellipse(self, x, y, w, h, color=None, filled=False, width=1.5):
        (self.fill if filled else lambda c: self.stroke(c, width))(color)
        self.p.drawEllipse(QRectF(x * self.u, y * self.u, w * self.u, h * self.u))

    def line(self, pts, color=None, width=1.5):
        self.stroke(color, width)
        self.p.drawPolyline(QPolygonF([self.P(*q) for q in pts]))

    def poly(self, pts, color=None):
        self.fill(color)
        self.p.drawPolygon(QPolygonF([self.P(*q) for q in pts]))

    def arc(self, cx, cy, r, start, sweep, color=None, width=1.5, head=False):
        self.stroke(color, width)
        box = QRectF((cx - r) * self.u, (cy - r) * self.u, 2 * r * self.u, 2 * r * self.u)
        path = QPainterPath()
        path.arcMoveTo(box, start)
        path.arcTo(box, start, sweep)
        self.p.drawPath(path)
        if head:
            a = math.radians(start + sweep)
            sgn = 1 if sweep > 0 else -1
            px, py = cx + r * math.cos(a), cy - r * math.sin(a)
            dx, dy = -math.sin(a) * sgn, -math.cos(a) * sgn
            nx, ny = math.cos(a), -math.sin(a)
            self.poly([(px + dx * 2.2, py + dy * 2.2), (px + nx * 2.0, py + ny * 2.0),
                       (px - nx * 2.0, py - ny * 2.0)], color)

    def text(self, x, y, w, h, s, color=None, size=6.0, bold=True):
        f = QFont("DejaVu Sans")
        f.setPixelSize(max(6, int(size * self.u)))
        f.setBold(bold)
        self.p.setFont(f)
        self.p.setPen(QColor(color) if color else self.ink)
        self.p.drawText(QRectF(x * self.u, y * self.u, w * self.u, h * self.u), Qt.AlignCenter, s)


# -- paylasilan parcalar ---------------------------------------------------
def bar(g, fill=0.55, color=None):
    """Bolum seridi (cogu bolum isleminin tabani)."""
    g.rect(1.5, 4.5, 13, 6, 1.2)
    if fill > 0:
        g.rect(3.0, 6.0, 10 * fill, 3, 0.5, color or g.acc, filled=True)


def mini(g, kind, color=None):
    """Sag alt kose isareti (rozet yerine cizgi dili)."""
    c = color or g.acc
    if kind == "plus":
        g.line([(12, 9.5), (12, 14.5)], c, 1.8); g.line([(9.5, 12), (14.5, 12)], c, 1.8)
    elif kind == "minus":
        g.line([(9.5, 12), (14.5, 12)], c, 1.8)
    elif kind == "x":
        g.line([(10, 10), (14, 14)], c, 1.8); g.line([(14, 10), (10, 14)], c, 1.8)
    elif kind == "check":
        g.line([(9.6, 12.2), (11.4, 14), (14.6, 10.4)], c, 1.8)
    elif kind == "arrows":
        g.line([(9.5, 12), (14.5, 12)], c, 1.5)
        g.line([(11, 10.6), (9.5, 12), (11, 13.4)], c, 1.5); g.line([(13, 10.6), (14.5, 12), (13, 13.4)], c, 1.5)


def cylinder(g, y=3.0):
    g.ellipse(2.5, y, 11, 3.2)
    g.line([(2.5, y + 1.6), (2.5, y + 8.4)]); g.line([(13.5, y + 1.6), (13.5, y + 8.4)])
    g.arc(8, y + 8.4, 5.5, 180, 180)


def drive(g):
    g.rect(1.5, 4.5, 13, 7, 1.4)
    g.line([(4, 7), (4, 9)], width=1.2); g.line([(6, 7), (6, 9)], width=1.2)
    g.ellipse(10.8, 7.2, 1.8, 1.8, g.acc, filled=True)


def doc(g, x=3.0, y=1.5, w=10.0, h=13.0):
    k = 3.0
    g.line([(x, y), (x + w - k, y), (x + w, y + k), (x + w, y + h), (x, y + h), (x, y)])
    g.line([(x + w - k, y), (x + w - k, y + k), (x + w, y + k)], width=1.2)


def folder(g, open_=False):
    g.line([(1.5, 4), (1.5, 13), (14.5, 13), (14.5, 5.5), (7.5, 5.5), (6, 3.5), (2, 3.5), (1.5, 4)])
    if open_:
        g.line([(1.5, 13), (3.5, 8), (16, 8), (14.5, 13)])


def arrow(g, x1, y1, x2, y2, color=None, width=1.6):
    g.line([(x1, y1), (x2, y2)], color, width)
    a = math.atan2(y2 - y1, x2 - x1)
    for s in (+1, -1):
        g.line([(x2, y2), (x2 - 3 * math.cos(a + s * 0.6), y2 - 3 * math.sin(a + s * 0.6))], color, width)


# ==========================================================================
# Semboller (ad -> (anlamsal renk, cizim))
# ==========================================================================
def s_apply(g):
    g.line([(3.5, 14.5), (3.5, 1.5)], width=1.6)
    g.poly([(4.2, 2), (13.5, 2), (11.2, 5.2), (13.5, 8.4), (4.2, 8.4)], g.acc)

def s_pending(g):
    g.line([(4, 1.8), (12, 1.8)]); g.line([(4, 14.2), (12, 14.2)])
    g.line([(5, 2), (5, 4.5), (8, 8), (5, 11.5), (5, 14)]); g.line([(11, 2), (11, 4.5), (8, 8), (11, 11.5), (11, 14)])
    g.poly([(6, 13.4), (10, 13.4), (8, 11)], g.acc)

def s_discard(g):
    g.ellipse(2, 2, 12, 12)
    g.line([(5.5, 5.5), (10.5, 10.5)], g.acc, 1.8); g.line([(10.5, 5.5), (5.5, 10.5)], g.acc, 1.8)

def s_undo(g):
    g.arc(8.5, 9, 5, 150, -190, g.acc, 1.7, head=False)
    g.poly([(1.5, 5.5), (6.5, 3.2), (6.2, 8.2)], g.acc)

def s_redo(g):
    g.arc(7.5, 9, 5, 30, 190, g.acc, 1.7)
    g.poly([(14.5, 5.5), (9.5, 3.2), (9.8, 8.2)], g.acc)

def s_refresh(g):
    g.arc(8, 8, 5.2, 70, 290, g.acc, 1.8, head=True)

def s_table(g):
    g.rect(1.5, 3, 13, 10, 1.2); g.line([(1.5, 6.3), (14.5, 6.3)], width=1.2)
    g.line([(6, 3), (6, 13)], width=1.2); g.line([(10.3, 6.3), (10.3, 13)], width=1.2)
    g.rect(1.5, 3, 13, 3.3, 1.0, g.acc, filled=True)

def s_table_clear(g):
    g.rect(1.5, 2, 11, 9, 1.2); g.line([(1.5, 5.2), (12.5, 5.2)], width=1.2); g.line([(5.5, 5.2), (5.5, 11)], width=1.2)
    mini(g, "x", RED if g.acc != QColor("#ffffff") else None)

def s_convert(g):
    arrow(g, 2, 5.5, 13.5, 5.5, g.acc); arrow(g, 14, 10.5, 2.5, 10.5)

def s_partition_new(g):
    g.rect(1.5, 3, 13, 6, 1.2); g.rect(3, 4.5, 5, 3, 0.5, g.ink, filled=True); mini(g, "plus")

def s_partition_delete(g):
    g.rect(1.5, 3, 13, 6, 1.2); g.rect(3, 4.5, 5, 3, 0.5, g.ink, filled=True); mini(g, "x")

def s_format(g):
    bar(g, 0)
    for x in (4, 7, 10):
        g.line([(x, 9.5), (x + 3, 5.5)], g.acc, 1.4)

def s_resize(g):
    g.rect(1.5, 3, 13, 6, 1.2); g.rect(3, 4.5, 5, 3, 0.5, g.ink, filled=True)
    arrow(g, 7, 12.5, 14.5, 12.5, g.acc, 1.5); arrow(g, 7, 12.5, 1.5, 12.5, g.acc, 1.5)

def s_label(g):
    g.line([(2, 3), (8.5, 3), (14, 8.5), (8.5, 14), (2, 8.5), (2, 3)])
    g.ellipse(4.2, 4.8, 2.2, 2.2, g.acc, filled=True)

def s_rename(g):
    g.line([(1.5, 13.5), (7, 13.5)], width=1.3)
    g.line([(4, 12), (4.8, 9.4), (11.6, 2.6), (13.4, 4.4), (6.6, 11.2), (4, 12)])
    g.line([(10.2, 4), (12, 5.8)], g.acc, 1.5)

def s_type(g):
    g.rect(1.5, 2.5, 13, 11, 1.6)
    g.text(1.5, 2.5, 13, 11, "ID", g.acc, 5.6)

def s_boot(g):
    bar(g, 0)
    g.poly([(8, 5.2), (8.9, 7.2), (11, 7.3), (9.3, 8.6), (9.9, 10.6), (8, 9.4), (6.1, 10.6),
            (6.7, 8.6), (5, 7.3), (7.1, 7.2)], g.acc)

def s_wipe(g):
    g.line([(2, 14.5), (14.5, 14.5)], width=1.2)
    g.line([(3.2, 11.2), (9.6, 4.8), (13.6, 8.8), (9.2, 13.2), (5.2, 13.2), (3.2, 11.2)])
    g.poly([(9.6, 4.8), (13.6, 8.8), (11.6, 10.8), (7.6, 6.8)], g.acc)

def s_wipe_free(g):
    pen = QPen(g.ink, 1.2 * g.u, Qt.DashLine); g.p.setPen(pen); g.p.setBrush(Qt.NoBrush)
    g.p.drawRoundedRect(QRectF(1.5 * g.u, 3 * g.u, 13 * g.u, 6 * g.u), 1.2 * g.u, 1.2 * g.u)
    g.line([(7, 14.5), (15, 14.5)], width=1.1)
    g.poly([(8, 12.8), (11.5, 9.3), (14, 11.8), (11.5, 14.3), (9.5, 14.3)], g.acc)

def s_image_resize(g):
    doc(g, 2.5, 1.5, 9, 12)
    arrow(g, 8, 12.5, 15, 12.5, g.acc, 1.5)

def s_mount(g):
    g.rect(1.5, 9.5, 13, 5, 1.2)
    arrow(g, 8, 1.5, 8, 8.2, g.acc, 1.7)

def s_unmount(g):
    g.poly([(8, 2.5), (14, 9), (2, 9)], g.acc); g.rect(2, 11, 12, 2.6, 0.6, g.acc, filled=True)

def s_disk(g):
    drive(g)

def s_disk_system(g):
    drive(g)
    g.rect(1.5, 1.5, 3.2, 2.2, 0.3, g.acc, filled=True)     # sistem isareti (bayrak)
    mini(g, "check", GREEN if g.acc != QColor("#ffffff") else None)

def s_disk_removable(g):
    g.rect(4.5, 1.5, 7, 4, 0.6); g.rect(3, 5.5, 10, 9, 1.4)
    g.rect(6, 2.8, 1.4, 1.4, 0.2, g.acc, filled=True); g.rect(8.6, 2.8, 1.4, 1.4, 0.2, g.acc, filled=True)

def s_image(g):
    doc(g); g.ellipse(5.5, 7.2, 5, 5); g.ellipse(7.4, 9.1, 1.2, 1.2, g.acc, filled=True)

def s_backup(g):
    g.rect(2, 3, 12, 3.2, 0.8); g.line([(3, 6.2), (3, 13.5), (13, 13.5), (13, 6.2)])
    g.line([(6.5, 8.8), (9.5, 8.8)], g.acc, 1.8)

def s_partition(g):
    bar(g, 0.6)

def s_search(g):
    g.ellipse(2, 2, 8.6, 8.6); g.line([(9.4, 9.4), (14, 14)], g.acc, 2.0)

def s_recover(g):
    doc(g, 3, 1.5, 10, 13)
    g.arc(8, 9.2, 2.8, 100, 260, g.acc, 1.5, head=True)

def s_clone(g):
    g.rect(1.5, 2, 9, 7, 1.2); g.rect(5.5, 7, 9, 7, 1.2, g.acc)
    g.rect(5.5, 7, 9, 7, 1.2, g.acc, filled=False, width=1.6)

def s_info(g):
    g.ellipse(2, 2, 12, 12); g.ellipse(7.2, 4.3, 1.6, 1.6, g.acc, filled=True)
    g.line([(8, 7.4), (8, 11.6)], g.acc, 1.9)

def s_warning(g):
    g.line([(8, 2), (14.5, 13.8), (1.5, 13.8), (8, 2)])
    g.line([(8, 6.2), (8, 9.8)], g.acc, 1.9); g.ellipse(7.3, 11.1, 1.4, 1.4, g.acc, filled=True)

def s_shield(g):
    path = [(8, 1.5), (13.5, 3.5), (13.5, 8), (8, 14.5), (2.5, 8), (2.5, 3.5), (8, 1.5)]
    g.line(path); g.line([(8, 1.5), (8, 14.5)], g.acc, 1.2)

def s_save(g):
    g.line([(2, 2), (11.5, 2), (14, 4.5), (14, 14), (2, 14), (2, 2)])
    g.rect(4.5, 2, 6, 3.6, 0.3); g.rect(4, 9, 8, 5, 0.3, g.acc, filled=True)

def s_open(g):
    folder(g, open_=True)

def s_folder(g):
    folder(g); g.rect(1.8, 7, 12.4, 5.8, 0.5, g.acc, filled=True)

def s_folder_new(g):
    folder(g); mini(g, "plus")

def s_folder_add(g):
    folder(g); arrow(g, 8, 6.5, 8, 12, g.acc, 1.6)

def s_file(g):
    doc(g); g.line([(5.5, 8), (10.5, 8)], g.acc, 1.2); g.line([(5.5, 10.5), (10.5, 10.5)], g.acc, 1.2)

def s_up(g):
    arrow(g, 8, 14, 8, 2.5, g.acc, 1.9)

def s_export(g):
    g.line([(5, 6), (2, 6), (2, 14), (14, 14), (14, 6), (11, 6)]); arrow(g, 8, 11, 8, 1.5, g.acc, 1.7)

def s_import(g):
    g.line([(5, 6), (2, 6), (2, 14), (14, 14), (14, 6), (11, 6)]); arrow(g, 8, 1.5, 8, 11, g.acc, 1.7)

def s_trash(g):
    g.line([(2, 4), (14, 4)]); g.line([(6, 4), (6.5, 2), (9.5, 2), (10, 4)], width=1.2)
    g.line([(3.5, 4), (4.5, 14.5), (11.5, 14.5), (12.5, 4)])
    g.line([(6.5, 7), (6.8, 12)], g.acc, 1.2); g.line([(9.5, 7), (9.2, 12)], g.acc, 1.2)

def s_bootloader(g):
    g.arc(8, 8.6, 5.4, 60, 300 - 0, None, 1.7)
    g.line([(8, 1.5), (8, 8)], g.acc, 2.0)

def s_efi(g):
    g.rect(3.5, 3.5, 9, 9, 1.2); g.rect(6, 6, 4, 4, 0.4, g.acc, filled=True)
    for t in (5.5, 8, 10.5):
        g.line([(t, 1.2), (t, 3.5)], width=1.1); g.line([(t, 12.5), (t, 14.8)], width=1.1)
        g.line([(1.2, t), (3.5, t)], width=1.1); g.line([(12.5, t), (14.8, t)], width=1.1)

def s_boot_order(g):
    for i, y in enumerate((3.5, 8, 12.5)):
        g.ellipse(1.6, y - 1.2, 2.4, 2.4, g.acc if i == 0 else None, filled=True)
        g.line([(6, y), (14.5, y)], width=1.6)

# -- isletim sistemleri ve dosya sistemi karesi ---------------------------
def s_windows(g):
    for x, y in ((2, 2), (8.6, 2), (2, 8.6), (8.6, 8.6)):
        g.rect(x, y, 5.4, 5.4, 0.3, g.acc, filled=True)

def s_linux(g):          # penguen kafasi (L4) — cizgi dilinde
    g.ellipse(2, 1.6, 12, 12.8)
    g.ellipse(4.4, 4.2, 2.6, 3.2); g.ellipse(9.0, 4.2, 2.6, 3.2)
    g.poly([(6.2, 8.4), (9.8, 8.4), (8, 11)], g.acc)

def s_macos(g):          # marka logosu yerine "komut" sembolu
    for cx, cy in ((4.5, 4.5), (11.5, 4.5), (4.5, 11.5), (11.5, 11.5)):
        g.ellipse(cx - 2, cy - 2, 4, 4, width=1.4)
    g.rect(4.5, 4.5, 7, 7, 0.2, width=1.4)

def s_fs_chip(g):
    g.ellipse(3, 3, 10, 10, g.acc, filled=True)


# (ad, grup, anlamsal renk, sembol, mevcut cizim)
def cur(name):
    return lambda p, u, dark: icons.DRAWERS[name](p, u, dark)

def cur_os(name):
    return lambda p, u, dark: getattr(theme, "_draw_" + name)(p, int(round(16 * u)))

def cur_chip(p, u, dark):
    p.setBrush(QColor("#3a7bd5")); p.setPen(QColor("#3a7bd5").darker(140))
    p.drawRoundedRect(QRectF(2 * u, 2 * u, 12 * u, 12 * u), 2 * u, 2 * u)

ITEMS = [
    ("Bekleyen islemler", [
        ("apply", "Uygula", GREEN, s_apply), ("pending", "Bekleyen", ORANGE, s_pending),
        ("discard", "Vazgec", RED, s_discard), ("undo", "Geri al", BLUE, s_undo),
        ("redo", "Yinele", BLUE, s_redo), ("refresh", "Yenile", BLUE, s_refresh)]),
    ("Bolum islemleri", [
        ("table", "Bolum tablosu", BLUE, s_table), ("table-clear", "Tabloyu sil", RED, s_table_clear),
        ("convert", "MBR/GPT donustur", PURPLE, s_convert), ("partition-new", "Yeni bolum", GREEN, s_partition_new),
        ("partition-delete", "Bolumu sil", RED, s_partition_delete), ("format", "Bicimlendir", ORANGE, s_format),
        ("resize", "Boyutlandir", BLUE, s_resize), ("label", "Etiket", TEAL, s_label),
        ("rename", "Yeniden adlandir", BLUE, s_rename), ("type", "Bolum turu", PURPLE, s_type),
        ("boot", "Etkin/onyukleme", ORANGE, s_boot)]),
    ("Silme, goruntu, baglama", [
        ("wipe", "Guvenli sil", RED, s_wipe), ("wipe-free", "Bos alani sil", RED, s_wipe_free),
        ("image-resize", "Goruntu boyutu", BLUE, s_image_resize), ("mount", "Bagla / harf ata", GREEN, s_mount),
        ("unmount", "Cikar", ORANGE, s_unmount)]),
    ("Kaynaklar ve araclar", [
        ("disk", "Disk", GREY, s_disk), ("disk-system", "Sistem diski", BLUE, s_disk_system),
        ("disk-removable", "Cikarilabilir", TEAL, s_disk_removable), ("image", "Goruntu dosyasi", BLUE, s_image),
        ("backup", "Yedek", TEAL, s_backup), ("partition", "Bolum", BLUE, s_partition),
        ("search", "Ara/tara", TEAL, s_search), ("recover", "Kurtar", GREEN, s_recover),
        ("clone", "Klonla", PURPLE, s_clone), ("info", "Bilgi", BLUE, s_info),
        ("warning", "Uyari", ORANGE, s_warning), ("shield", "Yetki", ORANGE, s_shield),
        ("save", "Kaydet", BLUE, s_save), ("open", "Ac", ORANGE, s_open)]),
    ("Dosya gezgini", [
        ("folder", "Klasor", YELLOW, s_folder), ("folder-new", "Yeni klasor", GREEN, s_folder_new),
        ("folder-add", "Klasor ekle", GREEN, s_folder_add), ("file", "Dosya", GREY, s_file),
        ("up", "Ust klasor", BLUE, s_up), ("export", "Disa aktar", BLUE, s_export),
        ("import", "Ice aktar", GREEN, s_import), ("trash", "Sil", RED, s_trash)]),
    ("Onyukleme, isletim sistemi, dosya sistemi", [
        ("bootloader", "Onyukleyici", PURPLE, s_bootloader), ("efi", "UEFI", PURPLE, s_efi),
        ("boot-order", "Onyukleme sirasi", PURPLE, s_boot_order),
        ("os:windows", "Windows", WIN, s_windows), ("os:linux", "Linux", "#e0a800", s_linux),
        ("os:macos", "macOS", GREY, s_macos), ("chip", "FS renk karesi", "#3a7bd5", s_fs_chip)]),
]


def current(name):
    if name.startswith("os:"):
        return cur_os(name[3:])
    if name == "chip":
        return cur_chip
    return cur(name)


def fam_b(fn, color):
    """Kutucuk: anlamsal renkli zemin, beyaz sembol (%72 olcek)."""
    def draw(p, u, dark):
        c = QColor(color)
        if dark:
            c = c.lighter(115)
        p.setPen(Qt.NoPen); p.setBrush(c)
        p.drawRoundedRect(QRectF(0.5 * u, 0.5 * u, 15 * u, 15 * u), 3.4 * u, 3.4 * u)
        p.save(); p.translate(2.4 * u, 2.4 * u)
        fn(Pen(p, u * 0.7, "#ffffff", "#ffffff", True)); p.restore()
    return draw


def fam_c(fn, color):
    """Cizgi: notr hat + anlamsal vurgu."""
    def draw(p, u, dark):
        ink = "#d6dae0" if dark else "#3b4652"
        acc = QColor(color).lighter(135) if dark else QColor(color)
        fn(Pen(p, u, ink, acc.name(), False))
    return draw


SIZES = (16, 24, 32)
COL_W, ROW_H, NAME_W = 250, 76, 170


def render_page(title, rows, path):
    width = NAME_W + COL_W * 3 + 10
    height = 40 + ROW_H * len(rows) + 10
    img = QImage(width, height, QImage.Format_ARGB32)
    img.fill(QColor("#ffffff"))
    p = QPainter(img); p.setRenderHint(QPainter.Antialiasing)
    f = QFont("DejaVu Sans"); f.setPixelSize(13); f.setBold(True); p.setFont(f)
    p.setPen(QColor("#111")); p.drawText(8, 20, title)
    f.setPixelSize(12)
    for i, h in enumerate(("A  mevcut", "B  kutucuk", "C  cizgi")):
        p.setFont(f); p.drawText(NAME_W + i * COL_W + 4, 34, h)
    f.setBold(False)
    y = 40
    for key, label, color, sym in rows:
        p.setFont(f); p.setPen(QColor("#222"))
        p.drawText(QRect(8, y, NAME_W - 10, ROW_H // 2), Qt.AlignVCenter, label)
        p.setPen(QColor("#888"))
        p.drawText(QRect(8, y + ROW_H // 2 - 6, NAME_W - 10, ROW_H // 2), Qt.AlignVCenter, key)
        for c, fn in enumerate((current(key), fam_b(sym, color), fam_c(sym, color))):
            x0 = NAME_W + c * COL_W
            for r, (bg, dark) in enumerate((("#f6f7f9", False), ("#2b2f35", True))):
                yy = y + 2 + r * 36
                p.fillRect(QRect(x0, yy, COL_W - 12, 34), QColor(bg))
                xx = x0 + 8
                for s in SIZES:
                    sub = QImage(s, s, QImage.Format_ARGB32); sub.fill(Qt.transparent)
                    q = QPainter(sub); q.setRenderHint(QPainter.Antialiasing)
                    fn(q, s / 16.0, dark); q.end()
                    p.drawImage(xx, yy + (34 - s) // 2, sub)
                    xx += s + 14
        y += ROW_H
    p.end()
    img.save(path)
    return path


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else HERE
    os.makedirs(out, exist_ok=True)
    for n, (title, rows) in enumerate(ITEMS, 1):
        print(render_page(f"{n}. {title}", rows, os.path.join(out, f"katalog-{n}.png")))
