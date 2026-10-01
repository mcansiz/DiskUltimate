"""Bolum listesi tablosu."""
from __future__ import annotations

from typing import List, Optional, Tuple

from PyQt5.QtCore import QSize, Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPixmap
from PyQt5.QtWidgets import (QAbstractItemView, QHeaderView, QTableWidget,
                             QTableWidgetItem)

from ...core.fsregistry import fs_display
from ...core.platform import mount_point_label
from ...core.ptable import FreeRegion, Partition, human_size
from ..theme import (FREE_COLOR, PLAN_COLOR, fs_color, palette_color,
                     plan_label)
from ...i18n import mark, tr

# Kaynak metinler; gosterilirken `columns()` ile cevrilir (ADR 0027).
# "Plan" sutunu yalnizca bekleyen adim varken gorunur (`set_partitions`
# gizler/gosterir): bos bir sutunu surekli tasimak yer israfi olurdu.
COLUMNS = [mark("Bolum"), mark("Plan"), mark("Dosya Sistemi"), mark("Etiket"),
           mark("Baglama"), mark("Boyut"), mark("Kullanilan"), mark("Bos"),
           mark("Baslangic LBA"), mark("Bitis LBA"), mark("Tur"),
           mark("Bayrak")]
PLAN_COLUMN = 1
# "Baglama" sutunu isletim sisteminin bolumu bagladigi yeri gosterir:
# Linux/macOS'ta dizin, Windows'ta surucu harfi (ADR 0043). Basligi
# platforma gore degisir (`columns()`).
MOUNT_COLUMN = 4




def columns() -> List[str]:
    """Sutun basliklarinin etkin dildeki hali."""
    names = [tr(name) for name in COLUMNS]
    names[MOUNT_COLUMN] = mount_point_label()
    return names


def part_label(p: Partition) -> str:
    """Tablodaki bolum adi — tek kaynak.

    Bekleyen islem isareti eklenirken metin yeniden uretildigi icin bu ad iki
    yerde ayni olmak zorunda; yoksa isaret kalkinca "(mantiksal)" eki kaybolur.
    """
    return (tr("Bolum {}", p.index)
            + (" " + tr("(mantiksal)") if p.logical else ""))


def color_chip(color: QColor, size: int = 13) -> QIcon:
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
        self.setHorizontalHeaderLabels(columns())
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(26)
        self.setIconSize(QSize(18, 18))
        self.setShowGrid(False)
        self.setContextMenuPolicy(Qt.DefaultContextMenu)
        header = self.horizontalHeader()
        header.setStretchLastSection(True)
        for i, mode in enumerate([QHeaderView.Interactive] * len(COLUMNS)):
            header.setSectionResizeMode(i, mode)
        self.setColumnWidth(0, 150)
        self.setColumnWidth(1, 140)
        self.setColumnWidth(2, 110)
        self.setColumnWidth(3, 120)
        self.setColumnWidth(MOUNT_COLUMN, 150)
        self.setColumnWidth(5, 90)
        self.setColumnWidth(6, 90)
        self.setColumnWidth(7, 90)
        self.setColumnWidth(8, 100)
        self.setColumnWidth(9, 100)
        self.setColumnWidth(10, 160)
        self.setColumnHidden(PLAN_COLUMN, True)
        self._rows: List[Tuple[str, object]] = []
        self._pending: dict = {}
        self.itemSelectionChanged.connect(self._on_selection)
        self.itemDoubleClicked.connect(self._on_double)

    def retranslate(self) -> None:
        """Dil degisince sutun basliklarini yeniler.

        Satirlarin icerigi `set_partitions()` ile yeniden doldurulur; ana
        pencere dil degisiminde zaten tabloyu tazeler.
        """
        self.setHorizontalHeaderLabels(columns())

    # -- doldurma ------------------------------------------------------------
    def set_pending(self, marks: dict) -> None:
        """{bolum numarasi: [aciklama, ...]} — bekleyen islem isaretleri.

        Bekleyen adimlar diske yazilmadan once burada gorunur: kullanici
        "Uygula" demeden neyin degisecegini tabloda da gormelidir, yalnizca
        kenardaki listede degil.
        """
        self._pending = dict(marks or {})
        self._apply_pending()

    def _apply_pending(self) -> None:
        """Isaretleri satirlara isler.

        Metin **her seferinde bastan** yazilir. Yalnizca isaret eklemek, adim
        uygulandiktan sonra kum saatinin satirda asili kalmasina yol aciyordu.
        """
        for row, (kind, obj) in enumerate(self._rows):
            if kind != "part":
                continue
            notes = self._pending.get(obj.index)
            item = self.item(row, 0)
            if item is None:
                continue
            font = item.font()
            font.setItalic(bool(notes))
            item.setFont(font)
            item.setText(part_label(obj) + ("  ⏳" if notes else ""))
            item.setToolTip(tr("Bekleyen islemler:") + "\n• "
                            + "\n• ".join(notes) if notes else "")

    def set_partitions(self, partitions: List[Partition],
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
        # Plan sutunu yalnizca gerektiginde gorunur
        self.setColumnHidden(
            PLAN_COLUMN,
            not any(kind == "part" and plan_label(obj)
                    for kind, obj in self._rows))
        self._apply_pending()
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
        # Plan onizlemesindeki bolumler durumlariyla birlikte yazilir;
        # diskteki bolumlerde `plan_state` bostur ve hicbir sey eklenmez.
        # Durum **ilk sutunda** durur: ayri bir sutun saga kayip ekrandan
        # cikiyordu ve kullanici planlanan satiri fark etmiyordu.
        state_text = plan_label(p)
        name_item = self._set(row, 0, part_label(p),
                              icon=color_chip(fs_color(p.fs_type)), bold=True)
        plan_item = self._set(row, PLAN_COLUMN, state_text or "-",
                              dim=not state_text)
        if state_text:
            for cell in (name_item, plan_item):
                cell.setForeground(QBrush(QColor(PLAN_COLOR)))
                cell.setToolTip(
                    tr("Bekleyen islemlerden geliyor: {}", state_text))
            font = plan_item.font()
            font.setBold(True)
            plan_item.setFont(font)
        self._set(row, 2, fs_display(p.fs_type) or "-")
        self._set(row, 3, p.name or p.fs_label or "-")
        self._set(row, MOUNT_COLUMN, p.mount_point or "-",
                  dim=not p.mount_point, bold=bool(p.mount_point))
        self._set(row, 5, human_size(p.size), align=Qt.AlignRight)
        self._set(row, 6, human_size(p.fs_used) if p.fs_used >= 0 else "-",
                  align=Qt.AlignRight)
        free = p.fs_total - p.fs_used if (p.fs_total >= 0 and p.fs_used >= 0) else -1
        self._set(row, 7, human_size(free) if free >= 0 else "-", align=Qt.AlignRight)
        self._set(row, 8, str(p.start_lba), align=Qt.AlignRight)
        self._set(row, 9, str(p.end_lba), align=Qt.AlignRight)
        self._set(row, 10, p.type_name)
        flags = []
        if p.bootable:
            flags.append(tr("Onyukleme"))
        if p.logical:
            flags.append(tr("Mantiksal"))
        self._set(row, 11, ", ".join(flags) or "-", dim=not flags)


    def _fill_free(self, row: int, r: FreeRegion) -> None:
        self._set(row, 0, tr("Bos alan"), icon=color_chip(QColor(FREE_COLOR)), dim=True)
        for col in (1, 2, 3, MOUNT_COLUMN, 6, 7):
            self._set(row, col, "-", dim=True)
        self._set(row, 5, human_size(r.size), align=Qt.AlignRight, dim=True)
        self._set(row, 8, str(r.start_lba), align=Qt.AlignRight, dim=True)
        self._set(row, 9, str(r.end_lba), align=Qt.AlignRight, dim=True)
        self._set(row, 10, tr("Bolumlenmemis"), dim=True)
        self._set(row, 11, "-", dim=True)

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
