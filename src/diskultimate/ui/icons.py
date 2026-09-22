"""Uygulamanin kendi ikon seti — **cizilerek** uretilir.

## Neden dosya veya SVG degil

- Sistem ikon temasina guvenilemez: `QStyle` standart ikonlari platforma gore
  bambaska gorunur ve aradigimiz islemlerin cogunun (bicimlendir, boyutlandir,
  onyukleme bayragi) karsiligi **yoktur**.
- SVG denendi ve elendi: `PyQt5.QtSvg` Ubuntu 24.04'te ayri pakettir ve
  kurulu degildi (olculdu). Harici bagimlilik yok kurali geregi (CLAUDE.md)
  SVG kullanilamaz.

Geriye kalan ve uc platformda da ayni gorunen tek yol: `QPainter` ile cizmek.
Butun cizimler 16 birimlik bir izgaraya gore yapilir (`u = size / 16`), bu
yuzden her boyutta ve HiDPI ekranda keskin kalir.

## Renk kurali

## Kapsam: burasi ISLEM ikonlaridir

Yukaridaki "dosya degil, cizim" karari arac cubugu / menu / agac ikonlari
icindir. Uygulamanin kendi **marka ikonu** (pencere, gorev cubugu, exe) bunun
disindadir ve `ui/appicon.py` icinde dosyadan okunur — gerekce ADR 0044.
Kisaca: o ikon kodla yeniden uretilemez ve paketleyicinin de ayni dosyaya
ihtiyaci vardir; `.ico` cozucusu Qt'nin kendi eklentisinde oldugu icin yeni
bagimlilik dogurmaz (SVG'den farki budur).

## Renk kurali

ADR 0013: ozel stil sayfasi yok, sistem temasi gecerli. Buradaki renkler
**anlamsaldir** — yesil ekleme, kirmizi silme, turuncu uyari — ve dosya sistemi
renkleri gibi sabit kalmasi gerekir. Notr parcalar (govde cizgileri) paletten
alinir, boylece koyu temada da gorunur.
"""
from __future__ import annotations

from typing import Dict, Optional

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import (QBrush, QColor, QIcon, QImage, QPainter,
                         QPainterPath, QPen, QPixmap, QPolygonF)
from PyQt5.QtWidgets import QApplication

# Anlamsal renkler (acik tema degerleri; koyu temada acilirlar)
GREEN = "#2e9e4f"       # ekleme, onay
RED = "#c0392b"         # silme, tehlike
ORANGE = "#e08b12"      # uyari, degistirme
BLUE = "#1769c7"        # notr islem
PURPLE = "#7a5bd6"      # donusum
TEAL = "#2f9e9e"        # tarama / inceleme
GREY = "#8a94a0"

_cache: Dict[tuple, QIcon] = {}


# ==========================================================================
# Ortak yardimcilar
# ==========================================================================
def _is_dark() -> bool:
    app = QApplication.instance()
    if app is None:
        return False
    color = app.palette().color(app.palette().Window)
    return color.lightness() < 128


def _tone(name: str, dark: bool) -> QColor:
    """Anlamsal rengi temaya gore ayarlar (koyu temada acilir)."""
    color = QColor(name)
    return color.lighter(135) if dark else color


def _ink(dark: bool) -> QColor:
    """Notr cizgi rengi — govde hatlari icin."""
    return QColor("#d6dae0") if dark else QColor("#3b4652")


def _pen(p: QPainter, color: QColor, u: float, width: float = 1.4) -> None:
    pen = QPen(color, max(1.0, u * width))
    pen.setJoinStyle(Qt.RoundJoin)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)


def _volume(p: QPainter, u: float, dark: bool, color: QColor = None,
            fill: float = 0.62) -> None:
    """Bir bolum serviti: cerceve + doluluk. Cogu islem ikonunun tabani."""
    body = QRectF(1.4 * u, 4.6 * u, 13.2 * u, 6.0 * u)
    p.setBrush(QColor(255, 255, 255, 28) if dark else QColor("#ffffff"))
    _pen(p, _ink(dark), u, 1.2)
    p.drawRoundedRect(body, 1.2 * u, 1.2 * u)
    if color is not None and fill > 0:
        inner = QRectF(body.x() + 0.9 * u, body.y() + 0.9 * u,
                       (body.width() - 1.8 * u) * fill, body.height() - 1.8 * u)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(inner, 0.6 * u, 0.6 * u)


def _disk_body(p: QPainter, u: float, dark: bool, color: QColor) -> None:
    """Silindir: fiziksel disk / goruntu tabani."""
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawRoundedRect(QRectF(2.0 * u, 4.5 * u, 12.0 * u, 7.0 * u),
                      1.4 * u, 1.4 * u)
    p.setBrush(color.lighter(125))
    p.drawEllipse(QRectF(2.0 * u, 3.0 * u, 12.0 * u, 3.4 * u))
    p.setBrush(QColor("#ffffff") if not dark else QColor("#20242a"))
    p.drawEllipse(QRectF(7.2 * u, 4.1 * u, 1.6 * u, 1.2 * u))


def _badge(p: QPainter, u: float, glyph: str, color: QColor,
           dark: bool) -> None:
    """Sag alt kosede yuvarlak rozet: +, x, onay, kalem, ok...

    Rozet, ayni tabani (bolum serviti) farkli islemler icin ayirt etmenin en
    ucuz ve en okunakli yoludur; 16 pikselde bile secilir.
    """
    box = QRectF(8.6 * u, 8.6 * u, 6.8 * u, 6.8 * u)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#1b1f24") if dark else QColor("#ffffff"))
    p.drawEllipse(box.adjusted(-0.5 * u, -0.5 * u, 0.5 * u, 0.5 * u))
    p.setBrush(color)
    p.drawEllipse(box)

    center = box.center()
    arm = 1.7 * u
    _pen(p, QColor("#ffffff"), u, 1.5)
    if glyph == "plus":
        p.drawLine(QPointF(center.x() - arm, center.y()),
                   QPointF(center.x() + arm, center.y()))
        p.drawLine(QPointF(center.x(), center.y() - arm),
                   QPointF(center.x(), center.y() + arm))
    elif glyph == "minus":
        p.drawLine(QPointF(center.x() - arm, center.y()),
                   QPointF(center.x() + arm, center.y()))
    elif glyph == "cross":
        p.drawLine(QPointF(center.x() - arm, center.y() - arm),
                   QPointF(center.x() + arm, center.y() + arm))
        p.drawLine(QPointF(center.x() + arm, center.y() - arm),
                   QPointF(center.x() - arm, center.y() + arm))
    elif glyph == "check":
        p.drawPolyline(QPolygonF([
            QPointF(center.x() - arm, center.y()),
            QPointF(center.x() - 0.3 * u, center.y() + arm * 0.8),
            QPointF(center.x() + arm, center.y() - arm * 0.9)]))
    elif glyph == "pencil":
        p.drawLine(QPointF(center.x() - arm * 0.8, center.y() + arm * 0.8),
                   QPointF(center.x() + arm * 0.8, center.y() - arm * 0.8))
        p.drawLine(QPointF(center.x() - arm, center.y() + arm),
                   QPointF(center.x() - arm * 0.5, center.y() + arm * 0.6))
    elif glyph == "arrows":
        p.drawLine(QPointF(center.x() - arm, center.y()),
                   QPointF(center.x() + arm, center.y()))
        p.drawPolyline(QPolygonF([
            QPointF(center.x() - arm * 0.4, center.y() - arm * 0.6),
            QPointF(center.x() - arm, center.y()),
            QPointF(center.x() - arm * 0.4, center.y() + arm * 0.6)]))
        p.drawPolyline(QPolygonF([
            QPointF(center.x() + arm * 0.4, center.y() - arm * 0.6),
            QPointF(center.x() + arm, center.y()),
            QPointF(center.x() + arm * 0.4, center.y() + arm * 0.6)]))
    elif glyph in ("down", "up"):
        yon = 1.0 if glyph == "down" else -1.0
        p.drawLine(QPointF(center.x(), center.y() - arm * yon),
                   QPointF(center.x(), center.y() + arm * yon))
        p.drawPolyline(QPolygonF([
            QPointF(center.x() - arm * 0.7, center.y() + arm * 0.3 * yon),
            QPointF(center.x(), center.y() + arm * yon),
            QPointF(center.x() + arm * 0.7, center.y() + arm * 0.3 * yon)]))
    elif glyph == "clock":
        p.drawLine(center, QPointF(center.x(), center.y() - arm * 0.8))
        p.drawLine(center, QPointF(center.x() + arm * 0.7, center.y()))


def _draw_arrow(p: QPainter, u: float, color: QColor, back: bool) -> None:
    """Geri al / yinele oku."""
    _pen(p, color, u, 1.8)
    p.setBrush(Qt.NoBrush)
    path = QPainterPath()
    if back:
        path.moveTo(4.0 * u, 10.5 * u)
        path.arcTo(QRectF(4.0 * u, 4.0 * u, 9.0 * u, 9.0 * u), 170, -150)
    else:
        path.moveTo(12.0 * u, 10.5 * u)
        path.arcTo(QRectF(3.0 * u, 4.0 * u, 9.0 * u, 9.0 * u), 10, 150)
    p.drawPath(path)
    p.setBrush(color)
    p.setPen(Qt.NoPen)
    tip_x = 4.0 * u if back else 12.0 * u
    p.drawPolygon(QPolygonF([
        QPointF(tip_x, 11.8 * u),
        QPointF(tip_x - 2.0 * u, 8.6 * u),
        QPointF(tip_x + 2.0 * u, 8.6 * u)]))


# ==========================================================================
# Tek tek ikonlar
# ==========================================================================
def _apply(p, u, dark):
    """Damali bayrak — Acronis'teki "bekleyen islemleri uygula" dugmesi gibi."""
    _pen(p, _ink(dark), u, 1.4)
    p.drawLine(QPointF(3.2 * u, 2.0 * u), QPointF(3.2 * u, 14.5 * u))
    flag = QRectF(3.2 * u, 2.4 * u, 10.0 * u, 6.4 * u)
    cell = flag.width() / 4.0
    light = QColor("#f2f4f7") if not dark else QColor("#cfd4da")
    dark_cell = _tone(GREEN, dark)
    p.setPen(Qt.NoPen)
    for row in range(2):
        for col in range(4):
            box = QRectF(flag.x() + col * cell, flag.y() + row * (flag.height() / 2),
                         cell, flag.height() / 2)
            p.setBrush(dark_cell if (row + col) % 2 == 0 else light)
            p.drawRect(box)
    _pen(p, _ink(dark), u, 1.0)
    p.setBrush(Qt.NoBrush)
    p.drawRect(flag)


def _pending(p, u, dark):
    """Bekleyen islem listesi: satirlar + saat."""
    _pen(p, _ink(dark), u, 1.4)
    for i in range(3):
        y = (4.0 + i * 3.0) * u
        p.drawLine(QPointF(2.0 * u, y), QPointF(9.5 * u, y))
    accent = _tone(ORANGE, dark)
    p.setPen(Qt.NoPen)
    p.setBrush(accent)
    p.drawEllipse(QRectF(9.0 * u, 8.4 * u, 6.4 * u, 6.4 * u))
    _pen(p, QColor("#ffffff"), u, 1.3)
    center = QPointF(12.2 * u, 11.6 * u)
    p.drawLine(center, QPointF(center.x(), center.y() - 2.0 * u))
    p.drawLine(center, QPointF(center.x() + 1.6 * u, center.y()))


def _discard(p, u, dark):
    accent = _tone(RED, dark)
    p.setPen(Qt.NoPen)
    p.setBrush(accent)
    p.drawEllipse(QRectF(2.0 * u, 2.0 * u, 12.0 * u, 12.0 * u))
    _pen(p, QColor("#ffffff"), u, 1.8)
    p.drawLine(QPointF(5.8 * u, 5.8 * u), QPointF(10.2 * u, 10.2 * u))
    p.drawLine(QPointF(10.2 * u, 5.8 * u), QPointF(5.8 * u, 10.2 * u))


def _undo(p, u, dark):
    _draw_arrow(p, u, _tone(BLUE, dark), back=True)


def _redo(p, u, dark):
    _draw_arrow(p, u, _tone(BLUE, dark), back=False)


def _refresh(p, u, dark):
    color = _tone(BLUE, dark)
    _pen(p, color, u, 1.8)
    p.setBrush(Qt.NoBrush)
    path = QPainterPath()
    path.moveTo(13.0 * u, 8.0 * u)
    path.arcTo(QRectF(3.0 * u, 3.0 * u, 10.0 * u, 10.0 * u), 0, 280)
    p.drawPath(path)
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawPolygon(QPolygonF([QPointF(13.0 * u, 2.4 * u),
                             QPointF(15.2 * u, 6.2 * u),
                             QPointF(10.6 * u, 6.0 * u)]))


def _table(p, u, dark):
    """Bolum tablosu: uc kutucuklu serit."""
    _pen(p, _ink(dark), u, 1.2)
    p.setBrush(Qt.NoBrush)
    body = QRectF(1.6 * u, 4.4 * u, 12.8 * u, 7.2 * u)
    p.drawRoundedRect(body, 1.0 * u, 1.0 * u)
    for i, name in enumerate((BLUE, GREEN, PURPLE)):
        p.setPen(Qt.NoPen)
        p.setBrush(_tone(name, dark))
        p.drawRect(QRectF(body.x() + 0.8 * u + i * 3.9 * u, body.y() + 0.9 * u,
                          3.2 * u, body.height() - 1.8 * u))


def _table_clear(p, u, dark):
    _table(p, u, dark)
    _badge(p, u, "cross", _tone(RED, dark), dark)


def _convert(p, u, dark):
    """Donusum: iki karsit ok."""
    color = _tone(PURPLE, dark)
    _pen(p, color, u, 1.7)
    p.drawLine(QPointF(3.4 * u, 6.0 * u), QPointF(12.0 * u, 6.0 * u))
    p.drawLine(QPointF(12.6 * u, 10.2 * u), QPointF(4.0 * u, 10.2 * u))
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawPolygon(QPolygonF([QPointF(14.0 * u, 6.0 * u),
                             QPointF(11.2 * u, 4.2 * u),
                             QPointF(11.2 * u, 7.8 * u)]))
    p.drawPolygon(QPolygonF([QPointF(2.0 * u, 10.2 * u),
                             QPointF(4.8 * u, 8.4 * u),
                             QPointF(4.8 * u, 12.0 * u)]))


def _partition_new(p, u, dark):
    _volume(p, u, dark, _tone(BLUE, dark), fill=0.45)
    _badge(p, u, "plus", _tone(GREEN, dark), dark)


def _partition_delete(p, u, dark):
    _volume(p, u, dark, _tone(GREY, dark), fill=0.55)
    _badge(p, u, "cross", _tone(RED, dark), dark)


def _format(p, u, dark):
    """Bicimlendirme: bolum + firca izi."""
    _volume(p, u, dark, _tone(ORANGE, dark), fill=0.9)
    _pen(p, QColor("#ffffff"), u, 1.2)
    for i in range(3):
        x = (4.0 + i * 3.0) * u
        p.drawLine(QPointF(x, 5.6 * u), QPointF(x - 1.6 * u, 9.6 * u))
    _badge(p, u, "check", _tone(ORANGE, dark), dark)


def _resize(p, u, dark):
    _volume(p, u, dark, _tone(BLUE, dark), fill=0.5)
    _badge(p, u, "arrows", _tone(BLUE, dark), dark)


def _label(p, u, dark):
    """Etiket: kose kesik kart."""
    color = _tone(TEAL, dark)
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawPolygon(QPolygonF([
        QPointF(2.0 * u, 4.0 * u), QPointF(10.5 * u, 4.0 * u),
        QPointF(14.0 * u, 8.0 * u), QPointF(10.5 * u, 12.0 * u),
        QPointF(2.0 * u, 12.0 * u)]))
    p.setBrush(QColor("#ffffff"))
    p.drawEllipse(QRectF(10.0 * u, 7.0 * u, 2.0 * u, 2.0 * u))


def _rename(p, u, dark):
    _label(p, u, dark)
    _badge(p, u, "pencil", _tone(BLUE, dark), dark)


def _type(p, u, dark):
    """Bolum turu: etiketli kimlik kutusu."""
    _volume(p, u, dark, _tone(PURPLE, dark), fill=0.35)
    _pen(p, _ink(dark), u, 1.3)
    p.drawLine(QPointF(8.0 * u, 6.2 * u), QPointF(8.0 * u, 9.0 * u))
    p.drawLine(QPointF(9.8 * u, 6.2 * u), QPointF(9.8 * u, 9.0 * u))
    p.drawLine(QPointF(9.8 * u, 6.2 * u), QPointF(11.4 * u, 6.2 * u))
    p.drawLine(QPointF(11.4 * u, 6.2 * u), QPointF(11.4 * u, 9.0 * u))


def _boot(p, u, dark):
    """Onyukleme bayragi."""
    color = _tone(GREEN, dark)
    _pen(p, _ink(dark), u, 1.4)
    p.drawLine(QPointF(4.0 * u, 2.2 * u), QPointF(4.0 * u, 14.2 * u))
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawPolygon(QPolygonF([
        QPointF(4.0 * u, 2.8 * u), QPointF(13.4 * u, 5.2 * u),
        QPointF(4.0 * u, 8.4 * u)]))


def _wipe(p, u, dark):
    """Guvenli silme: bolum + capraz tarama."""
    _volume(p, u, dark, _tone(RED, dark), fill=1.0)
    _pen(p, QColor(255, 255, 255, 170), u, 1.0)
    for i in range(5):
        x = (2.4 + i * 2.6) * u
        p.drawLine(QPointF(x, 10.2 * u), QPointF(x + 2.4 * u, 5.0 * u))


def _wipe_free(p, u, dark):
    _volume(p, u, dark, _tone(BLUE, dark), fill=0.45)
    _pen(p, QColor(_tone(RED, dark)), u, 1.0)
    for i in range(3):
        x = (8.6 + i * 1.9) * u
        p.drawLine(QPointF(x, 10.0 * u), QPointF(x + 1.6 * u, 5.4 * u))


def _image_resize(p, u, dark):
    _disk_body(p, u, dark, _tone(BLUE, dark))
    _badge(p, u, "arrows", _tone(BLUE, dark), dark)


# -- agac ve arac cubugu ------------------------------------------------
def _disk(p, u, dark):
    _disk_body(p, u, dark, _tone(GREY, dark))


def _disk_system(p, u, dark):
    _disk_body(p, u, dark, _tone(BLUE, dark))
    _badge(p, u, "check", _tone(ORANGE, dark), dark)


def _disk_removable(p, u, dark):
    _disk_body(p, u, dark, _tone(TEAL, dark))


def _image(p, u, dark):
    """Goruntu dosyasi: kose kivrik belge + disk."""
    _pen(p, _ink(dark), u, 1.2)
    p.setBrush(QColor(255, 255, 255, 30) if dark else QColor("#ffffff"))
    p.drawPolygon(QPolygonF([
        QPointF(3.0 * u, 1.8 * u), QPointF(10.0 * u, 1.8 * u),
        QPointF(13.0 * u, 4.8 * u), QPointF(13.0 * u, 14.2 * u),
        QPointF(3.0 * u, 14.2 * u)]))
    p.setPen(Qt.NoPen)
    p.setBrush(_tone(BLUE, dark))
    p.drawRoundedRect(QRectF(4.6 * u, 8.0 * u, 6.8 * u, 4.4 * u),
                      0.8 * u, 0.8 * u)


def _backup(p, u, dark):
    _image(p, u, dark)
    _badge(p, u, "clock", _tone(TEAL, dark), dark)


def _partition(p, u, dark):
    _volume(p, u, dark, _tone(BLUE, dark), fill=0.6)


def _search(p, u, dark):
    color = _tone(TEAL, dark)
    _pen(p, color, u, 1.7)
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(QRectF(2.6 * u, 2.6 * u, 8.4 * u, 8.4 * u))
    p.drawLine(QPointF(10.4 * u, 10.4 * u), QPointF(14.0 * u, 14.0 * u))


def _recover(p, u, dark):
    _volume(p, u, dark, _tone(TEAL, dark), fill=0.4)
    _badge(p, u, "clock", _tone(GREEN, dark), dark)


def _clone(p, u, dark):
    _disk_body(p, u, dark, _tone(GREY, dark))
    p.setPen(Qt.NoPen)
    p.setBrush(_tone(BLUE, dark))
    p.drawRoundedRect(QRectF(7.6 * u, 7.6 * u, 7.4 * u, 7.4 * u),
                      1.0 * u, 1.0 * u)
    _pen(p, QColor("#ffffff"), u, 1.2)
    p.drawRect(QRectF(9.2 * u, 9.2 * u, 4.2 * u, 4.2 * u))


def _info(p, u, dark):
    color = _tone(BLUE, dark)
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawEllipse(QRectF(2.0 * u, 2.0 * u, 12.0 * u, 12.0 * u))
    p.setBrush(QColor("#ffffff"))
    p.drawEllipse(QRectF(7.2 * u, 4.2 * u, 1.6 * u, 1.6 * u))
    p.drawRoundedRect(QRectF(7.2 * u, 6.8 * u, 1.6 * u, 5.0 * u),
                      0.6 * u, 0.6 * u)


def _warning(p, u, dark):
    color = _tone(ORANGE, dark)
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    path = QPainterPath()
    path.moveTo(8.0 * u, 1.8 * u)
    path.lineTo(15.2 * u, 14.0 * u)
    path.lineTo(0.8 * u, 14.0 * u)
    path.closeSubpath()
    p.drawPath(path)
    p.setBrush(QColor("#ffffff"))
    p.drawRoundedRect(QRectF(7.2 * u, 5.6 * u, 1.6 * u, 4.6 * u),
                      0.6 * u, 0.6 * u)
    p.drawEllipse(QRectF(7.2 * u, 11.2 * u, 1.6 * u, 1.6 * u))


def _shield(p, u, dark):
    """Yetki: kalkan."""
    color = _tone(ORANGE, dark)
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    path = QPainterPath()
    path.moveTo(8.0 * u, 1.6 * u)
    path.lineTo(14.0 * u, 4.0 * u)
    path.lineTo(14.0 * u, 8.6 * u)
    path.quadTo(14.0 * u, 12.6 * u, 8.0 * u, 14.6 * u)
    path.quadTo(2.0 * u, 12.6 * u, 2.0 * u, 8.6 * u)
    path.lineTo(2.0 * u, 4.0 * u)
    path.closeSubpath()
    p.drawPath(path)
    _pen(p, QColor("#ffffff"), u, 1.6)
    p.drawPolyline(QPolygonF([QPointF(5.4 * u, 8.0 * u),
                              QPointF(7.4 * u, 10.0 * u),
                              QPointF(10.8 * u, 5.8 * u)]))


def _save(p, u, dark):
    color = _tone(BLUE, dark)
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawRoundedRect(QRectF(2.0 * u, 2.0 * u, 12.0 * u, 12.0 * u),
                      1.2 * u, 1.2 * u)
    p.setBrush(QColor("#ffffff"))
    p.drawRect(QRectF(5.0 * u, 2.8 * u, 6.0 * u, 4.0 * u))
    p.drawRect(QRectF(4.2 * u, 8.6 * u, 7.6 * u, 4.6 * u))
    p.setBrush(color)
    p.drawRect(QRectF(8.6 * u, 3.4 * u, 1.6 * u, 2.6 * u))


def _open(p, u, dark):
    """Acik klasor + icinden cikan belge.

    Duz `folder` ikonuyla 16 pikselde karisiyordu; belge parcasi ikisini
    ayirir (biri "klasor", oteki "ac").
    """
    color = _tone(ORANGE, dark)
    p.setPen(Qt.NoPen)
    p.setBrush(color.darker(115))
    p.drawRoundedRect(QRectF(1.6 * u, 3.0 * u, 7.0 * u, 3.0 * u),
                      0.6 * u, 0.6 * u)
    # icindeki belge (klasorden yukari tasar)
    _pen(p, _ink(dark), u, 1.1)
    p.setBrush(QColor("#ffffff") if not dark else QColor("#e6e9ed"))
    p.drawRect(QRectF(5.6 * u, 1.6 * u, 6.0 * u, 6.4 * u))
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawRoundedRect(QRectF(1.6 * u, 6.4 * u, 12.8 * u, 7.2 * u),
                      1.0 * u, 1.0 * u)


# -- dosya gezgini ------------------------------------------------------
def _folder(p, u, dark):
    color = _tone(ORANGE, dark)
    p.setPen(Qt.NoPen)
    p.setBrush(color.darker(115))
    p.drawRoundedRect(QRectF(1.6 * u, 3.2 * u, 6.6 * u, 3.0 * u),
                      0.6 * u, 0.6 * u)
    p.setBrush(color)
    p.drawRoundedRect(QRectF(1.6 * u, 4.8 * u, 12.8 * u, 8.4 * u),
                      1.0 * u, 1.0 * u)


def _folder_new(p, u, dark):
    _folder(p, u, dark)
    _badge(p, u, "plus", _tone(GREEN, dark), dark)


def _folder_add(p, u, dark):
    _folder(p, u, dark)
    _badge(p, u, "arrows", _tone(BLUE, dark), dark)


def _file(p, u, dark):
    """Belge: kose kivrik sayfa."""
    _pen(p, _ink(dark), u, 1.2)
    p.setBrush(QColor(255, 255, 255, 30) if dark else QColor("#ffffff"))
    p.drawPolygon(QPolygonF([
        QPointF(3.4 * u, 1.8 * u), QPointF(9.6 * u, 1.8 * u),
        QPointF(12.6 * u, 4.8 * u), QPointF(12.6 * u, 14.2 * u),
        QPointF(3.4 * u, 14.2 * u)]))
    p.drawPolyline(QPolygonF([
        QPointF(9.6 * u, 1.8 * u), QPointF(9.6 * u, 4.8 * u),
        QPointF(12.6 * u, 4.8 * u)]))
    _pen(p, _tone(GREY, dark), u, 1.0)
    for i in range(3):
        y = (7.4 + i * 2.0) * u
        p.drawLine(QPointF(5.2 * u, y), QPointF(10.8 * u, y))


def _up(p, u, dark):
    """Ust klasor: yukari ok."""
    color = _tone(BLUE, dark)
    _pen(p, color, u, 1.9)
    p.drawLine(QPointF(8.0 * u, 13.4 * u), QPointF(8.0 * u, 5.2 * u))
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawPolygon(QPolygonF([QPointF(8.0 * u, 2.2 * u),
                             QPointF(3.4 * u, 7.2 * u),
                             QPointF(12.6 * u, 7.2 * u)]))


def _export(p, u, dark):
    """Disa aktar: aygittan disari cikan ok."""
    color = _tone(GREEN, dark)
    _pen(p, _ink(dark), u, 1.3)
    p.setBrush(Qt.NoBrush)
    p.drawPolyline(QPolygonF([
        QPointF(4.4 * u, 3.0 * u), QPointF(2.0 * u, 3.0 * u),
        QPointF(2.0 * u, 14.0 * u), QPointF(14.0 * u, 14.0 * u),
        QPointF(14.0 * u, 3.0 * u), QPointF(11.6 * u, 3.0 * u)]))
    _pen(p, color, u, 1.9)
    p.drawLine(QPointF(8.0 * u, 2.0 * u), QPointF(8.0 * u, 9.6 * u))
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawPolygon(QPolygonF([QPointF(8.0 * u, 12.0 * u),
                             QPointF(4.6 * u, 8.0 * u),
                             QPointF(11.4 * u, 8.0 * u)]))


def _import(p, u, dark):
    """Ice aktar: aygita giren ok."""
    color = _tone(BLUE, dark)
    _pen(p, _ink(dark), u, 1.3)
    p.setBrush(Qt.NoBrush)
    p.drawPolyline(QPolygonF([
        QPointF(4.4 * u, 14.0 * u), QPointF(2.0 * u, 14.0 * u),
        QPointF(2.0 * u, 3.0 * u), QPointF(14.0 * u, 3.0 * u),
        QPointF(14.0 * u, 14.0 * u), QPointF(11.6 * u, 14.0 * u)]))
    _pen(p, color, u, 1.9)
    p.drawLine(QPointF(8.0 * u, 15.0 * u), QPointF(8.0 * u, 7.4 * u))
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawPolygon(QPolygonF([QPointF(8.0 * u, 5.0 * u),
                             QPointF(4.6 * u, 9.0 * u),
                             QPointF(11.4 * u, 9.0 * u)]))


def _trash(p, u, dark):
    """Cop kutusu."""
    color = _tone(RED, dark)
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawRoundedRect(QRectF(3.0 * u, 2.6 * u, 10.0 * u, 1.8 * u),
                      0.8 * u, 0.8 * u)
    p.drawRoundedRect(QRectF(6.2 * u, 1.4 * u, 3.6 * u, 1.6 * u),
                      0.6 * u, 0.6 * u)
    p.drawRoundedRect(QRectF(4.0 * u, 4.8 * u, 8.0 * u, 9.4 * u),
                      1.0 * u, 1.0 * u)
    _pen(p, QColor("#ffffff"), u, 1.1)
    for x in (6.2, 8.0, 9.8):
        p.drawLine(QPointF(x * u, 6.6 * u), QPointF(x * u, 12.4 * u))



# ==========================================================================
# Onyukleme
# ==========================================================================
def _bootloader(p, u, dark):
    """Onyukleyici: disk + guc dugmesi. GRUB yonetimi ve onyukleme kodu."""
    color = _tone(PURPLE, dark)
    _pen(p, _ink(dark), u, 1.2)
    p.setBrush(QColor(255, 255, 255, 28) if dark else QColor("#ffffff"))
    p.drawRoundedRect(QRectF(1.6 * u, 2.2 * u, 12.8 * u, 11.6 * u),
                      1.4 * u, 1.4 * u)
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawEllipse(QRectF(4.2 * u, 4.8 * u, 7.6 * u, 7.6 * u))
    _pen(p, QColor("#ffffff"), u, 1.5)
    p.drawArc(QRectF(5.9 * u, 6.5 * u, 4.2 * u, 4.2 * u), 60 * 16, 240 * 16)
    p.drawLine(QPointF(8.0 * u, 6.0 * u), QPointF(8.0 * u, 8.4 * u))


def _efi(p, u, dark):
    """UEFI bellenimi: yonga + baglanti bacaklari."""
    color = _tone(TEAL, dark)
    _pen(p, _ink(dark), u, 1.2)
    for i in range(3):
        y = (5.0 + i * 3.0) * u
        p.drawLine(QPointF(0.8 * u, y), QPointF(3.4 * u, y))
        p.drawLine(QPointF(12.6 * u, y), QPointF(15.2 * u, y))
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawRoundedRect(QRectF(3.4 * u, 3.4 * u, 9.2 * u, 9.2 * u),
                      1.2 * u, 1.2 * u)
    _pen(p, QColor("#ffffff"), u, 1.4)
    p.drawRect(QRectF(6.0 * u, 6.0 * u, 4.0 * u, 4.0 * u))


def _boot_order(p, u, dark):
    """Onyukleme sirasi: numarali satirlar + yon oku."""
    accent = _tone(BLUE, dark)
    _pen(p, _ink(dark), u, 1.4)
    for i in range(3):
        y = (4.2 + i * 3.4) * u
        p.drawLine(QPointF(5.6 * u, y), QPointF(14.4 * u, y))
    _pen(p, accent, u, 1.5)
    p.drawLine(QPointF(2.6 * u, 3.0 * u), QPointF(2.6 * u, 12.4 * u))
    p.setPen(Qt.NoPen)
    p.setBrush(accent)
    p.drawPolygon(QPolygonF([
        QPointF(2.6 * u, 14.2 * u), QPointF(0.7 * u, 11.2 * u),
        QPointF(4.5 * u, 11.2 * u)]))

def _mount(p, u, dark):
    """Bagla: bolum serviti + asagi inen ok (sisteme takilir)."""
    _volume(p, u, dark, _tone(GREEN, dark), 0.5)
    _badge(p, u, "down", _tone(GREEN, dark), dark)


def _unmount(p, u, dark):
    """Cikar: bolum serviti + yukari cikan ok (sistemden ayrilir)."""
    _volume(p, u, dark, _tone(BLUE, dark), 0.5)
    _badge(p, u, "up", _tone(BLUE, dark), dark)


DRAWERS = {
    # kuyruk
    "apply": _apply, "pending": _pending, "discard": _discard,
    "undo": _undo, "redo": _redo, "refresh": _refresh,
    # islemler
    "table": _table, "table-clear": _table_clear, "convert": _convert,
    "partition-new": _partition_new, "partition-delete": _partition_delete,
    "format": _format, "resize": _resize, "label": _label, "rename": _rename,
    "type": _type, "boot": _boot, "wipe": _wipe, "wipe-free": _wipe_free,
    "image-resize": _image_resize, "mount": _mount, "unmount": _unmount,
    # kaynaklar ve araclar
    "disk": _disk, "disk-system": _disk_system,
    "disk-removable": _disk_removable, "image": _image, "backup": _backup,
    "partition": _partition, "search": _search, "recover": _recover,
    "clone": _clone, "info": _info, "warning": _warning, "shield": _shield,
    "save": _save, "open": _open,
    # dosya gezgini
    "folder": _folder, "folder-new": _folder_new, "folder-add": _folder_add,
    "file": _file, "up": _up, "export": _export, "import": _import,
    "trash": _trash,
    # onyukleme
    "bootloader": _bootloader, "efi": _efi, "boot-order": _boot_order,
}


# ==========================================================================
# Genel arayuz
# ==========================================================================
# Her ikon bu boyutlarda **ayri ayri cizilir**. Tek bir pixmap tutup Qt'ye
# olceklettirmek bulaniklik uretiyordu: arac cubugu 24 piksel isterken elimizde
# 18 piksellik bir goruntu vardi. Cizim vektoreldir, her boyut keskin olur.
SIZES = (16, 20, 24, 32, 48)


def draw(name: str, size: int, dark: bool = None) -> QPixmap:
    """Tek bir ikonu istenen boyutta cizer (test ve ozel kullanim icin)."""
    if dark is None:
        dark = _is_dark()
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    drawer = DRAWERS.get(name)
    if drawer is not None:
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing, True)
        try:
            drawer(p, size / 16.0, dark)
        finally:
            p.end()
    return pix


def icon(name: str, size: int = 0) -> QIcon:
    """Adiyla ikon dondurur. Bilinmeyen ad **bos** ikon dondurur, cokmez.

    Sonuc **cok boyutludur**: widget hangi boyutu isterse istesin o boyutta
    cizilmis pixmap bulur. `size` verilirse o boyut da listeye eklenir.
    """
    dark = _is_dark()
    key = (name, size, dark)
    hit = _cache.get(key)
    if hit is not None:
        return hit
    result = QIcon()
    wanted = sorted(set(SIZES) | ({size} if size else set()))
    for each in wanted:
        result.addPixmap(draw(name, each, dark))
    _cache[key] = result
    return result


def clear_cache() -> None:
    """Tema degisince cagrilir: ikonlar yeni palete gore yeniden cizilir."""
    _cache.clear()


def digest(sizes=(16, 24, 32, 48)) -> str:
    """Butun ikon cizimlerinin **piksel** ozeti (sha256, ilk 32 karakter).

    "Ikonlar her platformda ayni" iddiasinin olcusudur: ayni deger cikiyorsa
    cizimler birebir aynidir.

    Karsilastirma **PNG baytlari uzerinden yapilmaz**. PNG kodlamasi Qt
    platform eklentisine gore degisiyor (olculdu: ayni makinede `offscreen`
    ile `windows` farkli bayt uretti) cunku `QPixmap` bicimi ekrana gore
    secilir. Piksel degerleri ise aynidir; bu yuzden once sabit bir bicime
    (`ARGB32`) cevrilir.
    """
    import hashlib

    total = hashlib.sha256()
    for name in names():
        for size in sizes:
            image = draw(name, size, dark=False).toImage().convertToFormat(
                QImage.Format_ARGB32)
            total.update(image.constBits().asstring(
                image.sizeInBytes() if hasattr(image, "sizeInBytes")
                else image.byteCount()))
    return total.hexdigest()[:32]


def names() -> list:
    """Tanimli ikon adlari (duman testi bunlari tek tek cizer)."""
    return sorted(DRAWERS)
