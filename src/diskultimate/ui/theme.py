"""Gorunum: tema (Sistem/Acik/Koyu), dosya sistemi renkleri, cizim yardimcilari."""
from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import (QBrush, QColor, QIcon, QLinearGradient, QPainter,
                         QPainterPath, QPen, QPixmap, QPolygonF)
from PyQt5.QtWidgets import QApplication

from ..core import fsregistry
from ..i18n import mark, tr, trc


# Dosya sistemine gore bolum renkleri — tek kaynak `core/fsregistry` (ADR 0056)
FS_COLORS = fsregistry.colors()
# Plan onizlemesi (ADR 0031): henuz diske yazilmamis bolumler bu renkle
# isaretlenir. Anlamsal bir renktir — "bu gercek degil, planlanan" demek icin
# kullanilir, bu yuzden paletten gelmez (CLAUDE.md renk kurali).
PLAN_COLOR = "#8e44ad"
PLAN_LABELS = {
    "new": mark("YENI"),
    "changed": mark("DEGISECEK"),
    "format": mark("BICIMLENECEK"),
    "wipe": mark("SILINECEK"),
}


def plan_label(part) -> str:
    """Bolumun plan durumunun kisa adi (diskteki bolumlerde bostur)."""
    key = getattr(part, "plan_state", "")
    return tr(PLAN_LABELS[key]) if key in PLAN_LABELS else ""


FREE_COLOR = "#dfe4ea"
FREE_HATCH = "#c9d1d9"
USED_BAR = "#2c5aa0"
SELECTED = "#1769c7"


def fs_color(fs_type: str) -> QColor:
    return QColor(fsregistry.fs_color(fs_type))


def lighten(color: QColor, factor: int = 150) -> QColor:
    return QColor(color).lighter(factor)


def darken(color: QColor, factor: int = 120) -> QColor:
    return QColor(color).darker(factor)


# --------------------------------------------------------------------------
# Doluluk cubugu
# --------------------------------------------------------------------------
# Cubuk iki yerde cizilir (harita blogu ve boyutlandirma seridi); ayni koddan
# gelmezse ikisi zamanla birbirinden ayrilir.
#
# Renk iki isi birden yapar: govde blogun kendi tonundan turedigi icin uyumlu
# kalir, doluluk arttikca uyari tonuna kayar — yeri bitmek uzere olan bir
# bolum yuzdeyi okumadan, bakar bakmaz gorulmelidir. Esikler anlamsaldir,
# bu yuzden paletten degil buradan gelir (CLAUDE.md renk kurali).
USAGE_WARN = "#e0a33c"       # %75'ten sonra karisan ton
USAGE_FULL = "#d94f4f"       # %90'dan sonra karisan "yer bitiyor" tonu
USAGE_WARN_AT = 0.75
USAGE_FULL_AT = 0.90


def blend(first: QColor, second: QColor, ratio: float) -> QColor:
    """Iki rengi orana gore karistirir (0 = birinci, 1 = ikinci)."""
    ratio = max(0.0, min(1.0, ratio))
    return QColor(round(first.red() + (second.red() - first.red()) * ratio),
                  round(first.green() + (second.green() - first.green()) * ratio),
                  round(first.blue() + (second.blue() - first.blue()) * ratio))


def readable_text(color: QColor) -> QColor:
    """Verilen zemin uzerinde okunacak yazi rengi.

    Karar HSL "lightness" ile degil, algilanan parlaklikla verilir: kehribar
    (#e0a33c) HSL'de orta cikar ve koyu sayilip uzerine beyaz yazi
    konuyordu; goz ise onu acik bir renk olarak gorur.
    """
    lum = (0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue())
    return QColor("#1b2430") if lum > 150 else QColor("#f7f9fc")


def usage_fill(base: QColor, ratio: float) -> QColor:
    """Doluluk cubugunun dolu kisminin rengi.

    Esigin altinda blogun kendi tonunda kalir — daha doygun ve orta
    parlaklikta, cunku cubuk acik bir olugun icindedir: koyulastirmak camur
    gibi gosterir, aydinlatmak olugun icinde kaybeder.

    Esikten sonra dogrudan uyari renklerine gecer. Iki ton **karistirilmaz**:
    mavi/mor/yesil bir blokla kehribar karisimi her seferinde donuk bir zeytin
    tonu veriyordu (grid onizlemesinde goruldu) — hem cirkin hem de uyari
    oldugu anlasilmiyordu.
    """
    if ratio >= USAGE_FULL_AT:
        return QColor(USAGE_FULL)
    if ratio >= USAGE_WARN_AT:
        return QColor(USAGE_WARN)
    hue, sat, val, _ = base.getHsv()
    if hue < 0:                       # gri tonlarda doygunluk yok
        return darken(base, 165)
    return QColor.fromHsv(hue, min(255, int(sat * 1.25)),
                          max(120, min(val, 195)))


def draw_usage_bar(painter: QPainter, rect, ratio: float, base: QColor,
                   label: str = "") -> None:
    """Doluluk cubugu: acik oluk + doygun dolgu + ince cerceve.

    Bicim, ornek alinan bolum araclarindan gelir (EaseUS, Macrorit, Acronis —
    `example gui/`): cubuk blogun **ustunde** durur ve renk yuku ondadir;
    blogun zemini acik kalir. Renkli bir zemin uzerine kucuk bir cubuk
    koymak denendi, iki renk birbiriyle yarisiyordu.

    `ratio` sifirdan kucukse doluluk **bilinmiyor** demektir: oluk taramali
    cizilir ve yuzde yazilmaz. "Bilinmiyor"u bos cubukla gostermek onu
    "bombos" gibi okuturdu.
    """
    if rect.width() < 6 or rect.height() < 5:
        return
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    area = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
    path = QPainterPath()
    path.addRoundedRect(area, 2.5, 2.5)

    known = ratio >= 0.0
    ratio = min(1.0, ratio) if known else 0.0
    track = lighten(base, 178) if known else lighten(base, 160)
    painter.fillPath(path, QBrush(track))

    if not known:
        painter.save()
        painter.setClipPath(path)
        painter.setPen(QPen(darken(track, 112), 1))
        step = 7
        for x in range(int(area.left() - area.height()), int(area.right()), step):
            painter.drawLine(QPointF(x, area.bottom()),
                             QPointF(x + area.height(), area.top()))
        painter.restore()

    fill = usage_fill(base, ratio)
    filled = area.width() * ratio
    if known and filled > 0.5:
        painter.save()
        painter.setClipPath(path)
        gradient = QLinearGradient(area.topLeft(), area.bottomLeft())
        gradient.setColorAt(0.0, lighten(fill, 124))
        gradient.setColorAt(0.55, fill)
        gradient.setColorAt(1.0, darken(fill, 108))
        painter.fillRect(QRectF(area.left(), area.top(), filled, area.height()),
                         QBrush(gradient))
        painter.restore()

    edge = darken(base, 145)
    edge.setAlpha(200)
    painter.setPen(QPen(edge, 1))
    painter.setBrush(Qt.NoBrush)
    painter.drawPath(path)

    if label and known:
        # Etiket iki kez cizilir: once oluk, sonra dolu kisim kirpilarak.
        # Boylece cubugun her iki yarisinda da okunur kalir.
        painter.setPen(QColor("#1b2430"))
        painter.drawText(rect, Qt.AlignCenter, label)
        if filled > 1:
            painter.save()
            painter.setClipRect(QRectF(area.left(), area.top(), filled,
                                       area.height()))
            painter.setPen(readable_text(fill))
            painter.drawText(rect, Qt.AlignCenter, label)
            painter.restore()
    painter.restore()


# --------------------------------------------------------------------------
# Tema: Sistem / Acik / Koyu (ADR 0088)
# --------------------------------------------------------------------------
# ADR 0013 sabit renkli stil sayfasini kaldirdi: masaustunun (ozellikle koyu)
# temasiyla catisip ogeleri okunmaz yapiyordu, Windows'ta sekme basliklarini
# kirpiyordu. Acik/koyu tema bu yuzden **palet** ile yapilir: Fusion stili +
# QPalette; butun pencereler (ve `palette_color` kullanan cizimler) rengini
# paletten aldigi icin kendiliginden uyar. QSS yalnizca paletin kapsamadigi
# iki ayrinti icindir (ipucu kenarligi, odak cercevesi) ve renklerini de
# paletten alir — sabit renk yazilmaz.
SETTING_KEY = "theme"
ENV_NAME = "DISKULTIMATE_THEME"
DEFAULT_THEME = "system"

# (anahtar, Turkce ad). Gorunen ad `theme_label()`'dan gelir: "Acik" baska
# yerde "On" (acik/kapali) diye cevrilir; tema adlari bu yuzden "tema"
# baglamiyla ayri girislerdir (`trc`).
THEMES = [
    ("system", "Sistem"),
    ("light", "Acik"),
    ("dark", "Koyu"),
]

# Rol -> (etkin renk, devre disi renk). Devre disi verilmezse etkinle ayni.
_LIGHT = {
    "Window": ("#f3f4f6", None), "WindowText": ("#1f2933", "#9aa3ad"),
    "Base": ("#ffffff", "#f3f4f6"), "AlternateBase": ("#f5f7fa", None),
    "ToolTipBase": ("#ffffff", None), "ToolTipText": ("#1f2933", None),
    "PlaceholderText": ("#6b7785", None), "Text": ("#1f2933", "#9aa3ad"),
    "Button": ("#eceff3", None), "ButtonText": ("#1f2933", "#9aa3ad"),
    "BrightText": ("#d0312d", None), "Link": ("#1769c7", None),
    "LinkVisited": ("#7a4fb5", None), "Highlight": ("#1769c7", "#c8d0da"),
    "HighlightedText": ("#ffffff", None), "Light": ("#ffffff", None),
    "Midlight": ("#e6e9ee", None), "Mid": ("#c8d0da", None),
    "Dark": ("#a0a8b3", None), "Shadow": ("#6b7785", None),
}
_DARK = {
    "Window": ("#2b2d31", None), "WindowText": ("#e4e6eb", "#7d828a"),
    "Base": ("#1e1f22", "#26282c"), "AlternateBase": ("#25272b", None),
    "ToolTipBase": ("#3a3d42", None), "ToolTipText": ("#e4e6eb", None),
    "PlaceholderText": ("#8b9099", None), "Text": ("#e4e6eb", "#7d828a"),
    "Button": ("#35383d", None), "ButtonText": ("#e4e6eb", "#7d828a"),
    "BrightText": ("#ff6b6b", None), "Link": ("#5aa9ff", None),
    "LinkVisited": ("#b48ef0", None), "Highlight": ("#2f6fd0", "#3a3d42"),
    "HighlightedText": ("#ffffff", None), "Light": ("#4a4e54", None),
    "Midlight": ("#3c3f44", None), "Mid": ("#2f3236", None),
    "Dark": ("#1a1b1e", None), "Shadow": ("#000000", None),
}
_PALETTES = {"light": _LIGHT, "dark": _DARK}

# Ilk cagrida masaustunun stili ve paleti saklanir: "Sistem"e donulurken
# bunlar geri yuklenir (uygulama yeniden baslatilmadan).
_system_style: Optional[str] = None
_system_palette = None
_current = DEFAULT_THEME


def theme_label(key: str) -> str:
    """Temanin etkin dildeki adi."""
    if key == "system":
        return trc("tema", "Sistem")
    if key == "light":
        return trc("tema", "Acik")
    if key == "dark":
        return trc("tema", "Koyu")
    return key


def build_palette(name: str):
    """Tema adindan QPalette uretir ("light" / "dark")."""
    from PyQt5.QtGui import QPalette

    roles = _PALETTES[name]
    palette = QPalette()
    for role_name, (active, disabled) in roles.items():
        role = getattr(QPalette, role_name)
        color = QColor(active)
        palette.setColor(QPalette.Active, role, color)
        palette.setColor(QPalette.Inactive, role, color)
        palette.setColor(QPalette.Disabled, role, QColor(disabled or active))
    return palette


def theme_stylesheet(palette) -> str:
    """Paletin kapsamadigi ayrintilar icin kucuk QSS (renkler paletten)."""
    from PyQt5.QtGui import QPalette

    kenar = palette.color(QPalette.Mid).name()
    vurgu = palette.color(QPalette.Highlight).name()
    return (f"QToolTip {{ border: 1px solid {kenar}; padding: 3px 5px; }}\n"
            f"QLineEdit:focus, QSpinBox:focus, QComboBox:focus, "
            f"QPlainTextEdit:focus {{ border: 1px solid {vurgu}; }}\n")


def saved_theme() -> str:
    """Acilista kullanilacak tema: ortam degiskeni > kayitli secim > Sistem."""
    import os

    istenen = os.environ.get(ENV_NAME, "").strip().lower()
    if istenen in dict(THEMES):
        return istenen
    try:
        from ..core import settings
        kayitli = str(settings.get(SETTING_KEY, DEFAULT_THEME))
    except Exception:
        kayitli = DEFAULT_THEME
    return kayitli if kayitli in dict(THEMES) else DEFAULT_THEME


def current_theme() -> str:
    return _current


def apply_theme(app, name: str = DEFAULT_THEME, remember: bool = False) -> str:
    """Temayi uygular; uygulanan anahtari dondurur.

    'system': masaustunun kendi stili ve paleti, stil sayfasi yok (ADR 0013).
    'light' / 'dark': Fusion + tema paleti + kucuk QSS. Bilinmeyen ad
    'system' sayilir. `remember=True` secimi ayar dosyasina yazar.
    """
    from PyQt5.QtWidgets import QStyleFactory

    global _system_style, _system_palette, _current
    if _system_style is None:
        _system_style = app.style().objectName()
        _system_palette = app.palette()
    if name not in _PALETTES:
        name = DEFAULT_THEME
    if name == DEFAULT_THEME:
        app.setStyleSheet("")
        stil = QStyleFactory.create(_system_style) if _system_style else None
        if stil is not None:
            app.setStyle(stil)
        app.setPalette(_system_palette)
    else:
        app.setStyle(QStyleFactory.create("Fusion"))
        palette = build_palette(name)
        app.setPalette(palette)
        app.setStyleSheet(theme_stylesheet(palette))
    _current = name
    if remember:
        from ..core import settings
        settings.set_value(SETTING_KEY, name)
    return name


def palette_color(widget, role: str = "text"):
    """Widget'in etkin paletinden renk dondurur.

    Sabit renk yazmak yerine bu kullanilir; boylece koyu/acik sistem temasinin
    ikisinde de metin okunabilir kalir.
    """
    from PyQt5.QtGui import QPalette

    roller = {
        "text": QPalette.WindowText,
        "dim": QPalette.PlaceholderText,
        "base": QPalette.Base,
        "window": QPalette.Window,
        "highlight": QPalette.Highlight,
        "highlight_text": QPalette.HighlightedText,
    }
    palet = widget.palette()
    renk = palet.color(roller.get(role, QPalette.WindowText))
    if role == "dim" and not renk.isValid():
        renk = palet.color(QPalette.WindowText)
        renk.setAlpha(150)
    return renk


def is_dark(widget) -> bool:
    """Etkin paletin koyu olup olmadigini soyler."""
    return palette_color(widget, "window").value() < 128


# --------------------------------------------------------------------------
# Isletim sistemi amblemleri
# --------------------------------------------------------------------------
# Marka logolarinin birebir kopyasi kullanilmaz; her biri stilize, taninabilir
# bir geometrik amblemdir. Amac diskleri hizlica ayirt edebilmek.
OS_COLORS = {
    "windows": "#0078D4",
    "linux": "#E8B71A",
    "macos": "#8E8E93",
}


def os_icon(os_name: str, size: int = 16) -> QIcon:
    """Isletim sistemi amblemi dondurur ('windows' | 'linux' | 'macos')."""
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing, True)
    try:
        if os_name == "windows":
            _draw_windows(p, size)
        elif not _draw_simple_icon(p, os_name, size):
            # Gomulu veri yoksa eski cizim (paket modulu silinmis olabilir)
            if os_name == "linux":
                _draw_linux(p, size)
            elif os_name == "macos":
                _draw_macos(p, size)
    finally:
        p.end()
    return QIcon(pix)


# Simple Icons amblemleri tek renklidir; renk burada verilir. Koyu temada
# koyu amblem zeminde kaybolur, bu yuzden acik tona cevrilir.
OS_INK = {"linux": "#222222", "macos": "#555555"}


def _draw_simple_icon(p: QPainter, os_name: str, size: int) -> bool:
    """Linux / macOS amblemi Simple Icons'tan (CC0; ADR 0046).

    Windows burada **yoktur**: Simple Icons butun Microsoft logolarini
    Microsoft hukuk ekibinin talebiyle v13.0.0'da kaldirdi
    (simple-icons#11236). Windows icin kendi geometrik amblemimiz kalir.
    """
    from . import iconpacks

    if os_name not in OS_INK:
        return False
    app = QApplication.instance()
    dark = bool(app and app.palette().color(app.palette().Window).lightness() < 128)
    color = QColor("#e6e8eb") if dark else QColor(OS_INK[os_name])
    return iconpacks.draw(p, "simpleicons", os_name, size, color)


def _draw_windows(p: QPainter, size: int) -> None:
    """Dort kareli pencere amblemi."""
    renk = QColor(OS_COLORS["windows"])
    gap = max(1, size // 12)
    kenar = (size - gap * 3) // 2
    upper = gap
    for sutun in range(2):
        for satir in range(2):
            x = gap + sutun * (kenar + gap)
            y = upper + satir * (kenar + gap)
            p.fillRect(x, y, kenar, kenar, renk)


def _draw_linux(p: QPainter, size: int) -> None:
    """Stilize penguen: koyu govde, acik karin, sari gaga."""
    govde = QColor("#22252A")
    karin = QColor("#F2F2F2")
    gaga = QColor(OS_COLORS["linux"])
    kenar = size / 16.0

    p.setPen(Qt.NoPen)
    p.setBrush(govde)
    p.drawEllipse(QRectF(3 * kenar, 1.5 * kenar, 10 * kenar, 13 * kenar))
    p.setBrush(karin)
    p.drawEllipse(QRectF(5 * kenar, 6 * kenar, 6 * kenar, 8 * kenar))
    p.setBrush(gaga)
    gaga_sekli = QPolygonF([
        QPointF(6.5 * kenar, 5.5 * kenar),
        QPointF(9.5 * kenar, 5.5 * kenar),
        QPointF(8.0 * kenar, 7.5 * kenar),
    ])
    p.drawPolygon(gaga_sekli)
    # ayaklar
    p.drawEllipse(QRectF(3.5 * kenar, 13 * kenar, 4 * kenar, 2 * kenar))
    p.drawEllipse(QRectF(8.5 * kenar, 13 * kenar, 4 * kenar, 2 * kenar))
    # gozler
    p.setBrush(QColor("#FFFFFF"))
    p.drawEllipse(QRectF(5.6 * kenar, 3.2 * kenar, 2.0 * kenar, 2.6 * kenar))
    p.drawEllipse(QRectF(8.4 * kenar, 3.2 * kenar, 2.0 * kenar, 2.6 * kenar))
    p.setBrush(govde)
    p.drawEllipse(QRectF(6.2 * kenar, 3.9 * kenar, 1.0 * kenar, 1.4 * kenar))
    p.drawEllipse(QRectF(9.0 * kenar, 3.9 * kenar, 1.0 * kenar, 1.4 * kenar))


def _draw_macos(p: QPainter, size: int) -> None:
    """Yuvarlak kose amblem (marka logosu kullanilmaz)."""
    renk = QColor(OS_COLORS["macos"])
    kenar = size / 16.0
    p.setPen(Qt.NoPen)
    p.setBrush(renk)
    p.drawRoundedRect(QRectF(2 * kenar, 2 * kenar, 12 * kenar, 12 * kenar),
                      3 * kenar, 3 * kenar)
    p.setBrush(QColor("#FFFFFF"))
    p.drawEllipse(QRectF(6 * kenar, 5 * kenar, 4 * kenar, 4 * kenar))
    p.drawRoundedRect(QRectF(5 * kenar, 9.5 * kenar, 6 * kenar, 1.6 * kenar),
                      0.8 * kenar, 0.8 * kenar)


# --------------------------------------------------------------------------
# Ortak arayuz yardimcilari
# --------------------------------------------------------------------------
# `standard_icon()` kaldirildi: sistem `QStyle` ikonlari platforma gore
# bambaska goruntuluyordu ve aradigimiz islemlerin cogunun karsiligi yoktu.
# Butun ikonlar artik `ui/icons.py` icinde cizilir ve uc platformda da
# **birebir ayni** cikar (olculdu: ayni sha256, ADR 0025).
