"""Gorunum sabitleri: renk paleti, dosya sistemi renkleri, stil sayfasi."""
from __future__ import annotations

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QIcon, QPainter, QPixmap, QPolygonF

# Ana palet (DiskGenius'a yakin, acik tema)
BG = "#f4f6f9"
PANEL = "#ffffff"
BORDER = "#c8d0da"
TEXT = "#1f2933"
TEXT_DIM = "#6b7785"
ACCENT = "#1769c7"
ACCENT_LIGHT = "#e3eefb"
HEADER_TOP = "#fbfcfe"
HEADER_BOTTOM = "#e7ecf3"

# Dosya sistemine gore bolum renkleri
FS_COLORS = {
    "FAT12": "#7fb069",
    "FAT16": "#5a9e4b",
    "FAT32": "#3d7fd6",
    "exFAT": "#2f9e9e",
    "NTFS": "#7a5bd6",
    "ext2": "#d98324",
    "ext3": "#d9731f",
    "ext4": "#d4621a",
    "btrfs": "#c04a3f",
    "XFS": "#b0543e",
    "F2FS": "#a0522d",
    "Linux Takas": "#8e8e93",
    "ISO9660": "#6a7fa0",
    "Bilinmeyen": "#9aa5b1",
    "": "#b6bfc9",          # bicimlendirilmemis
}
FREE_COLOR = "#dfe4ea"
FREE_HATCH = "#c9d1d9"
USED_BAR = "#2c5aa0"
SELECTED = "#1769c7"


def fs_color(fs_type: str) -> QColor:
    return QColor(FS_COLORS.get(fs_type or "", FS_COLORS[""]))


def lighten(color: QColor, factor: int = 150) -> QColor:
    return QColor(color).lighter(factor)


def darken(color: QColor, factor: int = 120) -> QColor:
    return QColor(color).darker(factor)


# NOT: Bu stil sayfasi su an UYGULANMIYOR. Uygulama sistemin varsayilan Qt
# gorunumunu kullanir (bkz. ADR 0013). Ileride "Tema" bolumu eklendiginde
# `apply_theme(app, "diskultimate")` ile secenek olarak sunulacak.
STYLESHEET = f"""
QMainWindow, QWidget {{
    background: {BG};
    color: {TEXT};
    font-size: 12px;
}}
QMenuBar {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {HEADER_TOP}, stop:1 {HEADER_BOTTOM});
    border-bottom: 1px solid {BORDER};
    padding: 2px;
}}
QMenuBar::item {{ padding: 5px 11px; background: transparent; border-radius: 3px; }}
QMenuBar::item:selected {{ background: {ACCENT_LIGHT}; }}
QMenu {{ background: {PANEL}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 26px 6px 22px; border-radius: 3px; }}
QMenu::item:selected {{ background: {ACCENT_LIGHT}; color: {ACCENT}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}
QToolBar {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {HEADER_TOP}, stop:1 {HEADER_BOTTOM});
    border-bottom: 1px solid {BORDER};
    spacing: 2px; padding: 4px;
}}
QToolButton {{ padding: 5px 9px; border-radius: 4px; border: 1px solid transparent; }}
QToolButton:hover {{ background: {ACCENT_LIGHT}; border-color: #bcd6f2; }}
QToolButton:pressed {{ background: #cfe0f5; }}
QToolButton:disabled {{ color: #a8b0ba; }}
QStatusBar {{ background: {HEADER_BOTTOM}; border-top: 1px solid {BORDER}; }}
QStatusBar::item {{ border: none; }}
QTreeWidget, QTableWidget, QListWidget, QTreeView, QTableView {{
    background: {PANEL};
    border: 1px solid {BORDER};
    alternate-background-color: #f8fafc;
    selection-background-color: {ACCENT_LIGHT};
    selection-color: {TEXT};
    outline: 0;
}}
QTreeWidget::item, QListWidget::item {{ padding: 3px 2px; }}
QTreeWidget::item:selected, QListWidget::item:selected, QTableWidget::item:selected {{
    background: {ACCENT_LIGHT}; color: {TEXT};
}}
QHeaderView::section {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {HEADER_TOP}, stop:1 {HEADER_BOTTOM});
    border: none; border-right: 1px solid {BORDER}; border-bottom: 1px solid {BORDER};
    padding: 5px 6px; font-weight: 600; color: #45505c;
}}
QTabWidget::pane {{ border: 1px solid {BORDER}; background: {PANEL}; top: -1px; }}
/* Sekmelerde font-weight KULLANILMAZ: Qt sekme genisligini stil sayfasindaki
   fontu hesaba katmadan olctugu icin kalin metin kirpiliyor (Windows'ta
   "Dosya Gezgini" -> "osya Gezgin"). Secili sekme yalnizca renk ve zeminle
   vurgulanir; min-width metnin sigmasini garantiler. */
QTabBar::tab {{
    background: {HEADER_BOTTOM}; border: 1px solid {BORDER}; border-bottom: none;
    padding: 6px 18px; margin-right: 2px; min-width: 96px; color: #55606c;
    border-top-left-radius: 4px; border-top-right-radius: 4px;
}}
QTabBar::tab:selected {{ background: {PANEL}; color: {ACCENT}; }}
QTabBar::tab:hover:!selected {{ background: #eef2f7; }}
QPushButton {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #ffffff, stop:1 #eef1f5);
    border: 1px solid {BORDER}; border-radius: 4px; padding: 6px 16px; min-width: 76px;
}}
QPushButton:hover {{ border-color: {ACCENT}; background: {ACCENT_LIGHT}; }}
QPushButton:pressed {{ background: #cfe0f5; }}
QPushButton:disabled {{ color: #a8b0ba; background: #f0f2f5; }}
QPushButton[primary="true"] {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #3a8ae0, stop:1 {ACCENT});
    color: white; border-color: #1560b4; font-weight: 600;
}}
QPushButton[primary="true"]:hover {{ background: #2d7fd6; }}
QLineEdit, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: 3px; padding: 4px 6px;
    selection-background-color: {ACCENT};
}}
/* QSpinBox/QDoubleSpinBox'a kutu kurali verilmez: stil sayfasi alt kontrolleri
   devraldiginda Fusion artir/azalt oklarini cizmiyor. Yalnizca renk ayarlanir. */
QSpinBox, QDoubleSpinBox {{
    background: {PANEL}; selection-background-color: {ACCENT}; min-height: 20px;
}}
QLineEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox QAbstractItemView {{
    background: {PANEL}; border: 1px solid {BORDER}; selection-background-color: {ACCENT_LIGHT};
    selection-color: {TEXT};
}}
QGroupBox {{
    border: 1px solid {BORDER}; border-radius: 4px; margin-top: 10px;
    padding-top: 8px; background: {PANEL};
}}
QGroupBox::title {{
    subcontrol-origin: margin; left: 10px; padding: 0 5px;
    color: {ACCENT}; font-weight: 600;
}}
QProgressBar {{
    border: 1px solid {BORDER}; border-radius: 3px; background: {PANEL};
    text-align: center; height: 18px;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #4a93e8, stop:1 {ACCENT});
    border-radius: 2px;
}}
QSplitter::handle {{ background: {BORDER}; }}
QSplitter::handle:horizontal {{ width: 3px; }}
QSplitter::handle:vertical {{ height: 3px; }}
QScrollBar:vertical {{ background: {BG}; width: 12px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #b9c2cc; border-radius: 6px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: #9aa5b1; }}
QScrollBar:horizontal {{ background: {BG}; height: 12px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: #b9c2cc; border-radius: 6px; min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QToolTip {{
    background: #2f3b47; color: white; border: none; padding: 5px 8px; border-radius: 3px;
}}
"""


def apply_icon_theme() -> str:
    """Acik arayuz temasiyla uyumlu bir ikon seti secer.

    Masaustu koyu bir ikon temasi kullaniyorsa (orn. `breeze-dark`) ikonlar
    acik renkli olur ve uygulamanin acik zemininde gorunmez. Bu durumda ayni
    temanin acik surumune gecilir; o da yoksa Qt'nin gomulu ikonlarina dusulur.

    QApplication olusturulduktan SONRA cagrilmalidir. Secilen tema adini dondurur.
    """
    mevcut = QIcon.themeName()
    if not mevcut or not mevcut.lower().endswith("-dark"):
        return mevcut
    for aday in (mevcut[:-len("-dark")], "breeze", "Adwaita"):
        if not aday:
            continue
        QIcon.setThemeName(aday)
        if QIcon.hasThemeIcon("folder"):
            return aday
    QIcon.setThemeName("")      # Qt'nin gomulu ikon seti
    return ""


# --------------------------------------------------------------------------
# Tema uygulama (su an yalnizca "sistem")
# --------------------------------------------------------------------------
THEMES = {
    "system": "Sistem varsayilani",
    "diskultimate": "DiskUltimate (acik)",
}


def apply_theme(app, name: str = "system") -> str:
    """Uygulamaya tema uygular. Su an varsayilan 'system'dir.

    'system' : hicbir stil sayfasi uygulanmaz; masaustunun kendi gorunumu,
               paleti ve ikon temasi aynen kullanilir.
    'diskultimate' : proje icindeki acik tema (ileride Tema bolumunden secilir).
    """
    if name == "diskultimate":
        app.setStyleSheet(STYLESHEET)
        apply_icon_theme()
        return "diskultimate"
    app.setStyleSheet("")
    return "system"


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
        elif os_name == "linux":
            _draw_linux(p, size)
        elif os_name == "macos":
            _draw_macos(p, size)
    finally:
        p.end()
    return QIcon(pix)


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
