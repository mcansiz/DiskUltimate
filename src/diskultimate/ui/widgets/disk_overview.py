"""Butun disklerin tek ekranda gorunumu (Acronis / EaseUS duzeni).

`DiskMapWidget` **tek** bir acik diski cizer. Bu widget ise sistemdeki butun
diskleri alt alta, her birinin bolumleriyle birlikte gosterir — hicbir diski
acmadan. Kullanici "once ac, sonra bak" adimlarini yapmak zorunda kalmaz.

Veri, arka planda salt okunur yapilan bolum yoklamasindan gelir
(`DiskSession.survey_disk`, ADR 0026); bu widget hicbir aygita dokunmaz.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from PyQt5.QtCore import QRect, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPen
from PyQt5.QtWidgets import QSizePolicy, QWidget

from ...core.ptable import human_size
from ..theme import FREE_COLOR, fs_color, palette_color
from ...i18n import tr

ROW_HEIGHT = 72
ROW_GAP = 8
LABEL_WIDTH = 132
MIN_BLOCK = 36
MARGIN = 8


class DiskOverviewWidget(QWidget):
    """Disk basina bir satir: solda kimlik kutusu, sagda bolum seridi."""

    diskSelected = pyqtSignal(str)                  # aygit yolu
    partitionSelected = pyqtSignal(str, int)        # aygit yolu, bolum numarasi
    partitionActivated = pyqtSignal(str, int)       # cift tiklama

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self._disks: List[object] = []
        self._surveys: Dict[str, object] = {}
        self._blocks: List[Tuple[QRect, str, int]] = []   # (alan, yol, index)
        self._rows: List[Tuple[QRect, str]] = []          # (alan, yol)
        self._hover: Optional[Tuple[str, int]] = None
        self._selected: Optional[Tuple[str, int]] = None

    # -- veri --------------------------------------------------------------
    def set_disks(self, disks, surveys) -> None:
        self._disks = list(disks or [])
        self._surveys = dict(surveys or {})
        self.setMinimumHeight(
            max(ROW_HEIGHT + 2 * MARGIN,
                len(self._disks) * (ROW_HEIGHT + ROW_GAP) + 2 * MARGIN))
        self.updateGeometry()
        self.update()

    def clear(self) -> None:
        self.set_disks([], {})

    # -- yerlesim ----------------------------------------------------------
    def _layout(self) -> None:
        self._blocks = []
        self._rows = []
        y = MARGIN
        strip_x = MARGIN + LABEL_WIDTH + 8
        strip_w = max(80, self.width() - strip_x - MARGIN)
        for info in self._disks:
            self._rows.append((QRect(MARGIN, y, LABEL_WIDTH, ROW_HEIGHT),
                               info.path))
            survey = self._surveys.get(info.path)
            parts = list(getattr(survey, "partitions", []) or [])
            total = max(1, info.size)
            if parts:
                # Oransal genislik + en kucuk genislik telafisi: kucuk bolumler
                # (100 MB EFI gibi) hicbir zaman gorunmez olmamali.
                widths = [max(MIN_BLOCK, int(strip_w * p.size / total))
                          for p in parts]
                extra = sum(widths) - strip_w
                if extra > 0:
                    flex = [i for i, w in enumerate(widths) if w > MIN_BLOCK]
                    span = sum(widths[i] - MIN_BLOCK for i in flex) or 1
                    for i in flex:
                        widths[i] = max(
                            MIN_BLOCK,
                            widths[i] - int(extra * (widths[i] - MIN_BLOCK) / span))
                x = strip_x
                for part, width in zip(parts, widths):
                    self._blocks.append(
                        (QRect(x, y, max(10, width - 2), ROW_HEIGHT),
                         info.path, part.index))
                    x += width
            y += ROW_HEIGHT + ROW_GAP

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout()

    # -- cizim -------------------------------------------------------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        if not self._blocks and not self._rows:
            self._layout()
        text_color = palette_color(self, "text")
        dim = palette_color(self, "dim")
        if not self._disks:
            painter.setPen(dim)
            painter.drawText(self.rect(), Qt.AlignCenter,
                             tr("Fiziksel disk bulunamadi"))
            painter.end()
            return

        small = QFont(self.font())
        small.setPointSize(max(7, small.pointSize() - 1))
        bold = QFont(self.font())
        bold.setBold(True)

        for rect, path in self._rows:
            info = next((d for d in self._disks if d.path == path), None)
            if info is None:
                continue
            survey = self._surveys.get(path)
            painter.setPen(QPen(dim, 1))
            painter.setBrush(palette_color(self, "window"))
            painter.drawRoundedRect(rect, 4, 4)
            painter.setPen(text_color)
            painter.setFont(bold)
            painter.drawText(rect.adjusted(8, 6, -6, 0), Qt.AlignLeft | Qt.AlignTop,
                             info.name)
            painter.setFont(small)
            painter.setPen(dim)
            scheme = getattr(survey, "scheme_name", "") or tr("okunuyor...")
            painter.drawText(rect.adjusted(8, 24, -6, 0),
                             Qt.AlignLeft | Qt.AlignTop, scheme)
            painter.drawText(rect.adjusted(8, 40, -6, 0),
                             Qt.AlignLeft | Qt.AlignTop, human_size(info.size))
            if info.is_system:
                painter.setPen(QColor("#c0392b"))
                painter.drawText(rect.adjusted(8, 54, -6, 0),
                                 Qt.AlignLeft | Qt.AlignTop, tr("SISTEM DISKI"))

            # bolumu olmayan disk: serit yerine aciklama
            if not any(p == path for _r, p, _i in self._blocks):
                strip = QRect(rect.right() + 8, rect.y(),
                              self.width() - rect.right() - 8 - MARGIN,
                              ROW_HEIGHT)
                painter.setPen(QPen(dim, 1, Qt.DashLine))
                painter.setBrush(QColor(FREE_COLOR))
                painter.drawRoundedRect(strip, 4, 4)
                painter.setPen(dim)
                painter.setFont(small)
                note = getattr(survey, "error", "") if survey else ""
                painter.drawText(strip, Qt.AlignCenter,
                                 tr("(bolumler okunamadi: {})", note) if note
                                 else (tr("(bolum yok)") if survey
                                       else tr("(okunuyor...)")))

        for rect, path, index in self._blocks:
            survey = self._surveys.get(path)
            part = next((p for p in getattr(survey, "partitions", [])
                         if p.index == index), None)
            if part is None:
                continue
            base = fs_color(part.fs_type)
            painter.setBrush(base)
            chosen = self._selected == (path, index)
            hovered = self._hover == (path, index)
            painter.setPen(QPen(QColor("#1769c7") if chosen else base.darker(140),
                                2 if chosen else 1))
            painter.drawRoundedRect(rect, 3, 3)
            if hovered and not chosen:
                painter.fillRect(rect.adjusted(2, 2, -2, -2),
                                 QColor(255, 255, 255, 40))
            painter.setPen(QColor("#ffffff"))
            painter.setFont(bold)
            label = part.fs_label or part.name or tr("Bolum {}", part.index)
            painter.drawText(rect.adjusted(6, 6, -4, 0),
                             Qt.AlignLeft | Qt.AlignTop,
                             _elide(painter, label, rect.width() - 10))
            painter.setFont(small)
            painter.drawText(rect.adjusted(6, 24, -4, 0),
                             Qt.AlignLeft | Qt.AlignTop,
                             _elide(painter, part.fs_type or "ham",
                                    rect.width() - 10))
            painter.drawText(rect.adjusted(6, 40, -4, 0),
                             Qt.AlignLeft | Qt.AlignTop,
                             _elide(painter, human_size(part.size),
                                    rect.width() - 10))
        painter.end()

    # -- etkilesim ---------------------------------------------------------
    def _hit(self, pos) -> Optional[Tuple[str, int]]:
        for rect, path, index in self._blocks:
            if rect.contains(pos):
                return path, index
        for rect, path in self._rows:
            if rect.contains(pos):
                return path, -1
        return None

    def mousePressEvent(self, event):
        hit = self._hit(event.pos())
        if hit is None:
            return
        path, index = hit
        self._selected = hit if index >= 0 else None
        self.update()
        if index >= 0:
            self.partitionSelected.emit(path, index)
        else:
            self.diskSelected.emit(path)

    def mouseDoubleClickEvent(self, event):
        hit = self._hit(event.pos())
        if hit is not None and hit[1] >= 0:
            self.partitionActivated.emit(*hit)

    def mouseMoveEvent(self, event):
        hit = self._hit(event.pos())
        hover = hit if hit and hit[1] >= 0 else None
        if hover != self._hover:
            self._hover = hover
            self.setToolTip(self._tooltip(hover) if hover else "")
            self.update()

    def leaveEvent(self, event):
        self._hover = None
        self.setToolTip("")
        self.update()

    def _tooltip(self, key) -> str:
        path, index = key
        survey = self._surveys.get(path)
        part = next((p for p in getattr(survey, "partitions", [])
                     if p.index == index), None)
        if part is None:
            return ""
        return (tr("{} — Bolum {}", path, part.index) + "\n"
                + (part.fs_type or tr("Bicimlendirilmemis"))
                + (f" — {part.fs_label}" if part.fs_label else "")
                + f"\n{human_size(part.size)}\n"
                + tr("LBA {} - {}", part.start_lba, part.end_lba)
                + "\n\n" + tr("Acmak icin tiklayin (salt okunur)"))


def _elide(painter: QPainter, text: str, width: int) -> str:
    metrics = painter.fontMetrics()
    if metrics.horizontalAdvance(text) <= width:
        return text
    return metrics.elidedText(text, Qt.ElideRight, max(10, width))
