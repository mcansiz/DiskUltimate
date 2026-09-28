"""Ikon setleri: secim, saklama ve cizim yonlendirmesi (ADR 0046).

Kullanici **Araclar > Ikon seti** menusunden sekiz setten birini secer;
secim ayarlara yazilir ve arayuz yeniden baslatilmadan yenilenir
(`icons.icon` canli bir `QIconEngine` dondurur).

| Anahtar  | Kaynak                                                   |
|----------|----------------------------------------------------------|
| classic  | `icons.DRAWERS` — ilk set (ADR 0025)                     |
| tile     | renkli yuvarlak kare + beyaz sembol (asagidaki semboller) |
| line     | notr ince hat + anlamsal renk vurgusu (ayni semboller)   |
| tabler, phosphor, lucide, bootstrap, material | gomulu paket (`iconpacks`) |

`tile` ve `line` **ayni sembol geometrisini** paylasir; boylece iki aile de
kendi icinde tutarlidir. Paketlerde karsiligi olmayan ikon klasik setten
cizilir (bos ikon hic gosterilmez).

Isletim sistemi amblemleri setten bagimsizdir: Linux ve macOS Simple Icons,
Windows kendi geometrik amblemimiz (`theme.os_icon`; Simple Icons Microsoft
logolarini Microsoft'un talebiyle kaldirdi).
"""
from __future__ import annotations

import math
from typing import Callable, Dict, List

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF

from . import iconpacks
from ..core import settings
from ..i18n import mark, tr

SETTING_KEY = "icon_set"
DEFAULT = "classic"

# (anahtar, gorunen ad) — paket adlari ozel isimdir, cevrilmez
SETS = (
    ("classic", mark("Klasik")),
    ("tile", mark("Kutucuk")),
    ("line", mark("Cizgi")),
    ("tabler", "Tabler"),
    ("phosphor", "Phosphor"),
    ("lucide", "Lucide"),
    ("bootstrap", "Bootstrap"),
    ("material", "Material"),
)
PACK_SETS = ("tabler", "phosphor", "lucide", "bootstrap", "material")

GREEN, RED, ORANGE, BLUE = "#2e9e4f", "#c0392b", "#e08b12", "#1769c7"
PURPLE, TEAL, GREY, YELLOW = "#7a5bd6", "#2f9e9e", "#6b7684", "#e0a800"

_current = None
_listeners: List[Callable[[str], None]] = []


def label(key: str) -> str:
    for k, text in SETS:
        if k == key:
            return tr(text) if k in ("classic", "tile", "line") else text
    return key


def current() -> str:
    """Etkin set (ilk cagrida ayarlardan okunur)."""
    global _current
    if _current is None:
        saved = settings.get(SETTING_KEY, DEFAULT)
        _current = saved if saved in dict(SETS) else DEFAULT
    return _current


def set_current(key: str) -> str:
    """Seti degistirir, saklar ve dinleyicilere haber verir."""
    global _current
    if key not in dict(SETS):
        key = DEFAULT
    _current = key
    settings.set_value(SETTING_KEY, key)
    for listener in list(_listeners):
        listener(key)
    return key


def add_listener(callback: Callable[[str], None]) -> None:
    _listeners.append(callback)


def ink(dark: bool) -> QColor:
    """Paket ikonlarinin murekkep rengi (temaya gore)."""
    return QColor("#d6dae0") if dark else QColor("#3b4652")


def draw(painter: QPainter, name: str, size: float, dark: bool,
         key: str) -> bool:
    """`name` ikonunu `key` setiyle cizer; set o ikonu bilmiyorsa False."""
    u = size / 16.0
    if key in PACK_SETS:
        return iconpacks.draw(painter, key, name, size, ink(dark))
    entry = SYMBOLS.get(name)
    if entry is None:
        return False
    color, symbol = entry
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    try:
        if key == "tile":
            _draw_tile(painter, u, dark, color, symbol)
        elif key == "line":
            accent = QColor(color).lighter(135) if dark else QColor(color)
            symbol(Pen(painter, u, ink(dark).name(), accent.name(), False))
        else:
            return False
    finally:
        painter.restore()
    return True


def _draw_tile(p: QPainter, u: float, dark: bool, color: str, symbol) -> None:
    base = QColor(color)
    if dark:
        base = base.lighter(115)
    p.setPen(Qt.NoPen)
    p.setBrush(base)
    p.drawRoundedRect(QRectF(0.5 * u, 0.5 * u, 15 * u, 15 * u), 3.4 * u, 3.4 * u)
    p.translate(2.4 * u, 2.4 * u)
    symbol(Pen(p, u * 0.7, "#ffffff", "#ffffff", True))


# ==========================================================================
# Sembol geometrisi (kutucuk ve cizgi aileleri)
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


# ad -> (anlamsal renk, sembol)
SYMBOLS: Dict[str, tuple] = {
    "apply": (GREEN, s_apply), "pending": (ORANGE, s_pending),
    "discard": (RED, s_discard), "undo": (BLUE, s_undo), "redo": (BLUE, s_redo),
    "refresh": (BLUE, s_refresh), "table": (BLUE, s_table),
    "table-clear": (RED, s_table_clear), "convert": (PURPLE, s_convert),
    "partition-new": (GREEN, s_partition_new),
    "partition-delete": (RED, s_partition_delete), "format": (ORANGE, s_format),
    "resize": (BLUE, s_resize), "label": (TEAL, s_label),
    "rename": (BLUE, s_rename), "type": (PURPLE, s_type),
    "boot": (ORANGE, s_boot), "wipe": (RED, s_wipe),
    "wipe-free": (RED, s_wipe_free), "image-resize": (BLUE, s_image_resize),
    "mount": (GREEN, s_mount), "unmount": (ORANGE, s_unmount),
    "disk": (GREY, s_disk), "disk-system": (BLUE, s_disk_system),
    "disk-removable": (TEAL, s_disk_removable), "image": (BLUE, s_image),
    "backup": (TEAL, s_backup), "partition": (BLUE, s_partition),
    "search": (TEAL, s_search), "recover": (GREEN, s_recover),
    "clone": (PURPLE, s_clone), "info": (BLUE, s_info),
    "warning": (ORANGE, s_warning), "shield": (ORANGE, s_shield),
    "save": (BLUE, s_save), "open": (ORANGE, s_open),
    "folder": (YELLOW, s_folder), "folder-new": (GREEN, s_folder_new),
    "folder-add": (GREEN, s_folder_add), "file": (GREY, s_file),
    "up": (BLUE, s_up), "export": (BLUE, s_export), "import": (GREEN, s_import),
    "trash": (RED, s_trash), "bootloader": (PURPLE, s_bootloader),
    "efi": (PURPLE, s_efi), "boot-order": (PURPLE, s_boot_order),
}
