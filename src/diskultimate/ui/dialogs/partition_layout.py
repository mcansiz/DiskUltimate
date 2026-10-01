"""Ortak "Bolum duzeni" penceresi (ADR 0049, 5. asama).

Iki yerde ayni pencere kullanilir:

* **Geri yukleme** ("Bolumleri yonet", `mode="restore"`): yedekteki
  bolumlerin hedef diske nasil yerlesecegi.
* **Ana ekran** (Bolum > Bolum duzenini degistir, `mode="disk"`): diskteki
  bolumlerin birlikte buyutulup kucultulmesi; sonuc bekleyen islemlere
  eklenir (ADR 0025).

Serit (`PartitionEditBar`) surukleyerek, tablo + `ResizePartitionDialog`
sayisal olarak duzenler; hazir duzenler: eski haline don, son bolumu
genislet, diske orantili yay. Pencere hicbir sey yazmaz; duzenlenmis
`EditableLayout` kopyasini dondurur. Sinirlar `core.layoutedit`te.
"""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout,
                             QHeaderView, QLabel, QPushButton, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout)

from ...core.fsregistry import fs_display
from ...core.ptable import human_size
from ...core.resize import FsResizeInfo, ResizeWindow
from ...core.layoutedit import EditableLayout
from ..theme import fs_color
from ..widgets.layout_bar import LayoutBar
from ..widgets.partition_table import color_chip
from .resize import ResizePartitionDialog
from ...i18n import tr

COLOR_WARN = "#c0392b"


class PartitionLayoutDialog(QDialog):
    """Ortak yerlesim modelini duzenler (geri yukleme ve ana ekran)."""

    def __init__(self, layout: EditableLayout, target_label: str, parent=None,
                 mode: str = "restore"):
        super().__init__(parent)
        self.mode = mode
        restore = mode == "restore"
        self.setWindowTitle(tr("Bolumleri Yonet") if restore
                            else tr("Bolum Duzeni"))
        self.resize(820, 520)
        self.plan = layout.copy()
        self.target_label = target_label

        root = QVBoxLayout(self)
        ss = self.plan.sector_size
        if restore:
            head = QLabel(tr("<b>{}</b> — {} &nbsp;·&nbsp; yedek diski {}",
                             target_label,
                             human_size(self.plan.target_sectors * ss),
                             human_size(self.plan.source_sectors * ss)))
        else:
            head = QLabel(tr("<b>{}</b> — {} &nbsp;·&nbsp; degisiklikler "
                             "bekleyen islemlere eklenir, diske \"Uygula\" "
                             "ile yazilir", target_label,
                             human_size(self.plan.target_sectors * ss)))
        root.addWidget(head)

        # Kenarlar surukleyerek boyutlandirilir; ayrintili ayar tablodan
        self.bar = LayoutBar()
        self.bar.partitionSelected.connect(self._select_row)
        self.bar.partitionActivated.connect(lambda _index: self._resize())
        self.bar.layoutChanged.connect(
            lambda: self._refresh(keep=self._current_index()))
        root.addWidget(self.bar)

        self.tree = QTreeWidget()
        self.tree.setRootIsDecorated(False)
        self.tree.setHeaderLabels([tr("Bolum"), tr("Dosya Sistemi"),
                                   tr("Yedekte") if restore else tr("Diskte"),
                                   tr("Yeni boyut"),
                                   tr("Baslangic"), tr("Sinirlar"),
                                   tr("Degisiklik")])
        self.tree.header().setSectionResizeMode(6, QHeaderView.Stretch)
        for col, width in ((0, 110), (1, 90), (2, 90), (3, 90), (4, 100),
                           (5, 170)):
            self.tree.setColumnWidth(col, width)
        self.tree.currentItemChanged.connect(lambda *_: self._on_row())
        self.tree.itemDoubleClicked.connect(lambda *_: self._resize())
        root.addWidget(self.tree, 1)

        buttons = QHBoxLayout()
        self.btn_resize = QPushButton(tr("Boyutlandir / tasi..."))
        self.btn_resize.clicked.connect(self._resize)
        buttons.addWidget(self.btn_resize)
        buttons.addStretch(1)
        for text, tip, action in (
                (tr("Yedekteki gibi") if restore else tr("Diskteki gibi"),
                 tr("Bolumler yedekteki yer ve boyutlarina doner") if restore
                 else tr("Bolumler diskteki yer ve boyutlarina doner"),
                 self._reset),
                (tr("Son bolumu genislet"),
                 tr("Diskin sonundaki bos alan son bolume eklenir"),
                 self._extend_last),
                (tr("Diske orantili yay"),
                 tr("Butun bolumler hedef diske oranla buyutulur ya da "
                    "kucultulur"),
                 self._fit)):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(action)
            buttons.addWidget(button)
        root.addLayout(buttons)

        self.message = QLabel("")
        self.message.setWordWrap(True)
        root.addWidget(self.message)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        self.btn_ok = box.button(QDialogButtonBox.Ok)
        root.addWidget(box)

        self._refresh()
        if self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))

    # ------------------------------------------------------------------
    def result_layout(self) -> EditableLayout:
        return self.plan

    def _current_index(self) -> int:
        item = self.tree.currentItem()
        return -1 if item is None else item.data(0, Qt.UserRole)

    def _refresh(self, keep: int = -1) -> None:
        plan = self.plan
        ss = plan.sector_size
        if self.bar.plan is not plan:
            self.bar.set_layout(plan, f"{self.target_label} — "
                                f"{human_size(plan.target_sectors * ss)}")
        else:
            self.bar.refresh()      # surukleme suruyorsa kesilmesin
        self.tree.blockSignals(True)
        self.tree.clear()
        select = None
        for part in plan.sorted_parts():
            limits = [tr("en az {}", human_size(part.min_count * ss))]
            if part.max_count:
                limits.append(tr("en cok {}", human_size(part.max_count * ss)))
            changes = []
            if part.grows:
                changes.append(tr("{} buyuyecek", human_size(
                    (part.new_count - part.old_count) * ss)))
            elif part.shrinks:
                changes.append(tr("{} kuculecek", human_size(
                    (part.old_count - part.new_count) * ss)))
            if part.moves:
                changes.append(tr("yeri degisiyor"))
            if part.locked:
                changes.append(tr("kilitli"))
            elif not part.resizable:
                changes.append(tr("boyut sabit ({})",
                                  fs_display(part.fs_type) or tr("bilinmeyen")))
            elif not part.fs_resizable and part.grows:
                changes.append(tr("eklenen alan bos kalir"))
            item = QTreeWidgetItem(self.tree, [
                tr("Bolum {}", part.index), fs_display(part.fs_type) or "-",
                human_size(part.old_count * ss),
                human_size(part.new_count * ss),
                f"{part.new_start:,}", ", ".join(limits),
                ", ".join(changes) or tr("degismiyor")])
            item.setIcon(0, color_chip(fs_color(part.fs_type), 12))
            item.setData(0, Qt.UserRole, part.index)
            if part.index == keep:
                select = item
        self.tree.blockSignals(False)
        if select is not None:
            self.tree.setCurrentItem(select)

        problem = plan.validate()
        if problem:
            self.message.setText(problem)
            self.message.setStyleSheet(f"color: {COLOR_WARN}")
        else:
            self.message.setStyleSheet("")
            self.message.setText(
                "; ".join(plan.summary())
                or (tr("Bolumler yedekteki yer ve boyutlarinda yazilacak.")
                    if self.mode == "restore"
                    else tr("Diskteki yerlesim degismiyor.")))
        self.btn_ok.setEnabled(not problem)
        self._on_row()

    def _on_row(self) -> None:
        index = self._current_index()
        self.btn_resize.setEnabled(index is not None and index >= 0)
        if index is not None and index >= 0:
            self.bar.select(index)

    def _select_row(self, index: int) -> None:
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.data(0, Qt.UserRole) == index:
                self.tree.setCurrentItem(item)
                return

    # ------------------------------------------------------------------
    def _resize(self) -> None:
        index = self._current_index()
        if index is None or index < 0:
            return
        part = self.plan.get(index)
        if part.locked:
            self.message.setText(
                tr("Bu bolum bu pencerede duzenlenemez (kuyrukta yeni ya da "
                   "sinirlari okunamadi)."))
            return
        lower, upper = self.plan.window(index)
        if upper < lower:
            return
        window = ResizeWindow(min(lower, part.new_start),
                              max(upper, part.new_end),
                              self.plan.sector_size)
        if part.fs_resizable:
            kind, note = part.kind, part.note
        elif part.resizable:
            kind = "raw"
            note = tr("Dosya sistemi tanimlanamadi; bolum kucultulemez, "
                      "buyutulurse eklenen alan bos kalir")
        else:
            kind = "unsupported"
            note = tr("{} bu surumde boyutlandirilamaz; yalnizca yeri "
                      "degisebilir", fs_display(part.fs_type) or tr("Bu dosya sistemi"))
        fs_info = FsResizeInfo(kind=kind, fs_type=part.fs_type,
                               min_sectors=part.min_count,
                               max_sectors=part.max_count, movable=True,
                               note=note)
        dialog = ResizePartitionDialog(
            part.as_partition(), window, fs_info,
            align_sectors=self.plan.align,
            used_bytes=getattr(part.part, "fs_used", -1),
            parent=self, accept_text=tr("Tamam"))
        if dialog.exec_() != QDialog.Accepted:
            return
        values = dialog.values()
        part.new_start = values["start_lba"]
        part.new_count = values["sector_count"]
        self._refresh(keep=index)

    def _reset(self) -> None:
        self.plan.reset()
        self._refresh(keep=self._current_index())

    def _extend_last(self) -> None:
        if not self.plan.extend_last():
            self.message.setText(tr("Genisletilebilecek bir bolum yok."))
            return
        self._refresh(keep=self._current_index())

    def _fit(self) -> None:
        if not self.plan.fit(expand=True):
            self.message.setText(
                tr("Bolumler bu diske sigdirilamiyor: gereken en az {}.",
                   human_size(self.plan.required_sectors()
                              * self.plan.sector_size)))
            self.message.setStyleSheet(f"color: {COLOR_WARN}")
            return
        self._refresh(keep=self._current_index())
