"""Bolum duzenleme seridi — oransal gorunum (ADR 0049, 3. asama).

DiskGenius'un "Goruntu Dosyasindan Diske Geri Yukle" penceresindeki serit:
bolumler diskin boyutuna **oransal** cizilir ve kenarlar suruklenir. Iki
bitisik bolumun ortak sinirinda tek tutamak vardir: suruklenince biri buyur,
oteki kuculur.

Serit hicbir sinir hesabi yapmaz. Surukleme `EdgeDragController`dadir
(ana haritayla ortak) ve hareketi `layoutedit.EditableLayout.move_edge`e
birakir: en az/en cok boyut, komsular, hizalama ve tasinabilirlik tek yerde.
"""
from __future__ import annotations

from PyQt5.QtCore import QRect, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPen
from PyQt5.QtWidgets import QSizePolicy, QWidget

from ...core.ptable import human_size
from ..theme import (FREE_COLOR, darken, draw_usage_bar, fs_color, lighten,
                     palette_color)
from .edgedrag import HANDLE_W, EdgeDragController, draw_handle  # noqa: F401
from ...i18n import tr

MARGIN = 10
BAR_H = 70


class PartitionEditBar(QWidget):
    """`EditableLayout`u oransal cizer; kenarlari surukleyerek degistirir."""

    layoutChanged = pyqtSignal()           # surukleme suruyor
    layoutCommitted = pyqtSignal(list)     # birakildi: degisen bolumler
    partitionSelected = pyqtSignal(int)
    partitionActivated = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(BAR_H + 34)
        self.setMaximumHeight(BAR_H + 34)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self.plan = None
        self.title = ""
        self.selected = -1
        self.edit = EdgeDragController(
            self, self._x, self._lba,
            lambda: (self._track().top(), self._track().bottom()))
        self.edit.changed.connect(self.layoutChanged)
        self.edit.committed.connect(self.layoutCommitted)

    # -- veri ---------------------------------------------------------------
    def set_layout(self, plan, title: str = "") -> None:
        self.plan = plan
        self.title = title
        self.edit.set_layout(plan)
        self.update()

    def refresh(self) -> None:
        """Model disaridan degisti (pencere, hazir duzen): yeniden ciz."""
        self.update()

    def select(self, index: int) -> None:
        self.selected = index
        self.update()

    # -- olcek --------------------------------------------------------------
    def _track(self) -> QRect:
        return QRect(MARGIN, 20, max(1, self.width() - 2 * MARGIN), BAR_H)

    def _x(self, lba: int) -> int:
        track = self._track()
        total = max(1, self.plan.target_sectors) if self.plan else 1
        return track.left() + int(round(lba / total * track.width()))

    def _lba(self, x: int) -> int:
        track = self._track()
        ratio = (x - track.left()) / max(1, track.width())
        return int(round(ratio * (self.plan.target_sectors if self.plan
                                  else 0)))

    # -- cizim --------------------------------------------------------------
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        track = self._track()
        dim = palette_color(self, "dim")
        painter.setPen(dim)
        painter.drawText(QRect(MARGIN, 0, track.width(), 18),
                         Qt.AlignLeft | Qt.AlignVCenter, self.title)
        painter.fillRect(track, QColor(FREE_COLOR))
        painter.setPen(QPen(darken(QColor(FREE_COLOR), 130), 1))
        painter.drawRect(track.adjusted(0, 0, -1, -1))
        if self.plan is None:
            return
        enabled = self.isEnabled()
        ss = self.plan.sector_size
        for part in self.plan.sorted_parts():
            left = self._x(part.new_start)
            right = self._x(part.new_end + 1)
            block = QRect(left, track.top(), max(2, right - left),
                          track.height())
            color = fs_color(part.fs_type)
            if not enabled or part.locked:
                color = QColor(color.lightness(), color.lightness(),
                               color.lightness())
            gradient = QLinearGradient(block.topLeft(), block.bottomLeft())
            gradient.setColorAt(0.0, lighten(color, 135))
            gradient.setColorAt(1.0, darken(color, 112))
            painter.fillRect(block, QBrush(gradient))
            border = palette_color(self, "highlight") \
                if part.index == self.selected else darken(color, 150)
            painter.setPen(QPen(border, 2 if part.index == self.selected
                                else 1))
            painter.drawRect(block.adjusted(0, 0, -1, -1))

            used = getattr(part.part, "fs_used", -1)
            if used is not None and used >= 0 and block.width() > 30:
                ratio = min(1.0, used / max(1, part.new_count * ss))
                draw_usage_bar(painter, QRect(block.left() + 5,
                                              block.top() + 5,
                                              block.width() - 10, 12),
                               ratio, color)
            if block.width() > 50:
                painter.setPen(QColor("#ffffff") if color.value() < 170
                               else QColor("#20262e"))
                font = QFont(self.font())
                font.setBold(True)
                painter.setFont(font)
                text = block.adjusted(4, 20, -4, 0)
                painter.drawText(text, Qt.AlignHCenter | Qt.AlignTop,
                                 tr("Bolum {}", part.index))
                painter.setFont(self.font())
                painter.drawText(text.adjusted(0, 16, 0, 0),
                                 Qt.AlignHCenter | Qt.AlignTop,
                                 part.fs_type or "-")
                painter.drawText(text.adjusted(0, 31, 0, 0),
                                 Qt.AlignHCenter | Qt.AlignTop,
                                 human_size(part.new_count * ss))
        if enabled:
            self.edit.paint(painter)
        painter.setPen(dim)
        free = sum(r.size for r in self.plan.free_regions())
        painter.drawText(QRect(MARGIN, track.bottom() + 2, track.width(), 14),
                         Qt.AlignRight | Qt.AlignVCenter,
                         tr("bos alan {}", human_size(free)) if free else "")

    # -- fare ---------------------------------------------------------------
    def _part_at(self, x: int) -> int:
        if self.plan is None:
            return -1
        lba = self._lba(x)
        for part in self.plan.parts:
            if part.new_start <= lba <= part.new_end:
                return part.index
        return -1

    def mouseMoveEvent(self, event) -> None:
        if self.edit.move(event.pos()):
            return
        if not self.edit.hover(event.pos()):
            self.setCursor(Qt.ArrowCursor)
            self.setToolTip("")

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton or self.plan is None:
            return
        if self.edit.press(event.pos()):
            return
        index = self._part_at(event.x())
        if index >= 0:
            self.select(index)
            self.partitionSelected.emit(index)

    def mouseReleaseEvent(self, event) -> None:
        self.edit.release(event.pos())

    def mouseDoubleClickEvent(self, event) -> None:
        index = self._part_at(event.x())
        if index >= 0:
            self.partitionActivated.emit(index)

    def leaveEvent(self, event) -> None:
        self.edit.leave()

    def sizeHint(self) -> QSize:
        return QSize(600, BAR_H + 34)


# Geriye uyumlu ad (ADR 0045 ile gelen geri yukleme seridi)
LayoutBar = PartitionEditBar
