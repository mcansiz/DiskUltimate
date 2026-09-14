"""Bolum listesi tablosu."""
from __future__ import annotations

from typing import List, Optional, Tuple

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPixmap
from PyQt5.QtWidgets import (QAbstractItemView, QHeaderView, QTableWidget,
                             QTableWidgetItem)

from ...core.ptable import FreeRegion, Partition, human_size
from ..theme import FREE_COLOR, fs_color, palette_color

COLUMNS = ["Bolum", "Dosya Sistemi", "Etiket", "Boyut", "Kullanilan", "Bos",
           "Baslangic LBA", "Bitis LBA", "Tur", "Bayrak"]


def color_chip(color: QColor, size: int = 11) -> QIcon:
    pix = QPixmap(size + 3, size + 3)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(color)
    p.setPen(QColor(color).darker(140))
    p.drawRoundedRect(1, 1, size, size, 2, 2)
    p.end()
    return QIcon(pix)


class PartitionTableWidget(QTableWidget):
    """Bolumleri ve bos alanlari listeler."""

    partitionSelected = pyqtSignal(int)
    freeSelected = pyqtSignal(int, int)
    partitionActivated = pyqtSignal(int)
    contextMenuRequested = pyqtSignal(object, object)

    def __init__(self, parent=None):
        super().__init__(0, len(COLUMNS), parent)
        self.setHorizontalHeaderLabels(COLUMNS)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(24)
        self.setShowGrid(False)
        self.setContextMenuPolicy(Qt.DefaultContextMenu)
        header = self.horizontalHeader()
        header.setStretchLastSection(True)
        for i, mode in enumerate([QHeaderView.Interactive] * len(COLUMNS)):
            header.setSectionResizeMode(i, mode)
        self.setColumnWidth(0, 120)
        self.setColumnWidth(1, 110)
        self.setColumnWidth(2, 120)
        self.setColumnWidth(3, 90)
        self.setColumnWidth(4, 90)
        self.setColumnWidth(5, 90)
        self.setColumnWidth(6, 100)
        self.setColumnWidth(7, 100)
        self.setColumnWidth(8, 160)
        self._rows: List[Tuple[str, object]] = []
        self.itemSelectionChanged.connect(self._on_selection)
        self.itemDoubleClicked.connect(self._on_double)

    # -- doldurma ------------------------------------------------------------
    def set_data(self, partitions: List[Partition],
                 free: List[FreeRegion]) -> None:
        self.blockSignals(True)
        self._rows = []
        satirlar: List[Tuple[int, str, object]] = []
        for p in partitions:
            satirlar.append((p.start_lba, "part", p))
        for r in free:
            satirlar.append((r.start_lba, "free", r))
        satirlar.sort(key=lambda x: (x[0], 0 if x[1] == "part" else 1))

        self.setRowCount(len(satirlar))
        for row, (_lba, kind, obj) in enumerate(satirlar):
            self._rows.append((kind, obj))
            if kind == "part":
                self._fill_partition(row, obj)
            else:
                self._fill_free(row, obj)
        self.blockSignals(False)

    def _set(self, row: int, col: int, text: str, *, dim: bool = False,
             align=Qt.AlignLeft, icon: Optional[QIcon] = None,
             bold: bool = False) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setTextAlignment(int(align) | int(Qt.AlignVCenter))
        if dim:
            item.setForeground(QBrush(palette_color(self, "dim")))
        if bold:
            f = item.font(); f.setBold(True); item.setFont(f)
        if icon is not None:
            item.setIcon(icon)
        self.setItem(row, col, item)
        return item

    def _fill_partition(self, row: int, p: Partition) -> None:
        etiket = f"{p.index}" + (" (mantiksal)" if p.logical else "")
        self._set(row, 0, f"Bolum {etiket}", icon=color_chip(fs_color(p.fs_type)),
                  bold=True)
        self._set(row, 1, p.fs_type or "-")
        self._set(row, 2, p.name or p.fs_label or "-")
        self._set(row, 3, human_size(p.size), align=Qt.AlignRight)
        self._set(row, 4, human_size(p.fs_used) if p.fs_used >= 0 else "-",
                  align=Qt.AlignRight)
        bos = p.fs_total - p.fs_used if (p.fs_total >= 0 and p.fs_used >= 0) else -1
        self._set(row, 5, human_size(bos) if bos >= 0 else "-", align=Qt.AlignRight)
        self._set(row, 6, str(p.start_lba), align=Qt.AlignRight)
        self._set(row, 7, str(p.end_lba), align=Qt.AlignRight)
        self._set(row, 8, p.type_name)
        bayraklar = []
        if p.bootable:
            bayraklar.append("Onyukleme")
        if p.logical:
            bayraklar.append("Mantiksal")
        self._set(row, 9, ", ".join(bayraklar) or "-", dim=not bayraklar)

    def _fill_free(self, row: int, r: FreeRegion) -> None:
        self._set(row, 0, "Bos alan", icon=color_chip(QColor(FREE_COLOR)), dim=True)
        for col in (1, 2, 4, 5):
            self._set(row, col, "-", dim=True)
        self._set(row, 3, human_size(r.size), align=Qt.AlignRight, dim=True)
        self._set(row, 6, str(r.start_lba), align=Qt.AlignRight, dim=True)
        self._set(row, 7, str(r.end_lba), align=Qt.AlignRight, dim=True)
        self._set(row, 8, "Bolumlenmemis", dim=True)
        self._set(row, 9, "-", dim=True)

    # -- secim ---------------------------------------------------------------
    def current_target(self) -> Optional[Tuple[str, object]]:
        row = self.currentRow()
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    def select_partition(self, index: int) -> None:
        for row, (kind, obj) in enumerate(self._rows):
            if kind == "part" and obj.index == index:
                self.blockSignals(True)
                self.selectRow(row)
                self.blockSignals(False)
                return

    def select_free(self, start_lba: int) -> None:
        for row, (kind, obj) in enumerate(self._rows):
            if kind == "free" and obj.start_lba == start_lba:
                self.blockSignals(True)
                self.selectRow(row)
                self.blockSignals(False)
                return

    def _on_selection(self) -> None:
        target = self.current_target()
        if not target:
            return
        kind, obj = target
        if kind == "part":
            self.partitionSelected.emit(obj.index)
        else:
            self.freeSelected.emit(obj.start_lba, obj.sector_count)

    def _on_double(self, _item) -> None:
        target = self.current_target()
        if target and target[0] == "part":
            self.partitionActivated.emit(target[1].index)

    def contextMenuEvent(self, event):
        row = self.rowAt(event.pos().y())
        if 0 <= row < len(self._rows):
            self.selectRow(row)
            self.contextMenuRequested.emit(self._rows[row], event.globalPos())
        else:
            self.contextMenuRequested.emit(None, event.globalPos())
