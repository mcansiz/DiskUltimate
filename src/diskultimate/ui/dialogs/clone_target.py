"""Diski baska bir diske klonlama: hedef secimi ve onay.

Hedefteki her sey silinir. Fiziksel disk guvenlik katmanlari (CLAUDE.md):
* uygun olmayan disk (kaynagin kendisi, bilgisi eksik, yazma korumali,
  kaynaktan kucuk) listede gri ve nedeniyle durur, secilemez;
* silme onayi kutusu isaretlenmeden "Klonla" etkin olmaz;
* sistem diski secilirse disk adinin yazilmasi istenir;
* bagli bolumu olan disk icin uyari gosterilir.
Uygunluk kurali cekirdektedir (`disksource.clone_target_problem`); bu pencere
yalnizca sunar.
"""
from __future__ import annotations

from typing import List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QLabel,
                             QLineEdit, QListWidget, QListWidgetItem,
                             QVBoxLayout)

from ...core.disksource import DiskSource, clone_target_problem
from ...core.ptable import human_size
from ...i18n import tr
from ..icons import icon as app_icon

COLOR_WARN = "#c0392b"


class CloneTargetDialog(QDialog):
    """Klon hedefi olarak bir fiziksel disk secer."""

    def __init__(self, parent, source_name: str, source_path: str,
                 source_size: int, sources: List[DiskSource],
                 pending_steps: int = 0):
        super().__init__(parent)
        self.setWindowTitle(tr("Diski baska bir diske klonla"))
        self.setMinimumWidth(560)
        self.sources = sources
        self.source_size = source_size
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(
            tr("Kaynak: <b>{}</b> ({})", source_name, human_size(source_size))))
        info = QLabel(tr("Kaynagin butun sektorleri (bolum tablosu, bolumler, "
                         "onyukleme alani) hedef diske birebir kopyalanir. "
                         "Kaynak salt okunur kalir."))
        info.setWordWrap(True)
        layout.addWidget(info)
        if pending_steps:
            note = QLabel(tr("Bekleyen {} adim klona DAHIL DEGIL: diskin su "
                             "anki hali kopyalanir.", pending_steps))
            note.setWordWrap(True)
            layout.addWidget(note)

        layout.addWidget(QLabel(tr("Hedef disk:")))
        self.list = QListWidget()
        for src in sources:
            disk = src.disk
            problem = clone_target_problem(source_path, source_size, disk)
            text = f"{disk.name} — {disk.model or tr('bilinmeyen')} ({human_size(disk.size)})"
            tags = []
            if disk.is_system:
                tags.append(tr("SISTEM DISKI"))
            if disk.mounted:
                tags.append(tr("bagli bolum var"))
            if tags:
                text += "  [" + ", ".join(tags) + "]"
            if problem:
                text += "  — " + problem
            item = QListWidgetItem(app_icon("disk"), text)
            item.setData(Qt.UserRole, src)
            if problem:
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
            self.list.addItem(item)
        self.list.currentItemChanged.connect(lambda *_: self._update())
        layout.addWidget(self.list)

        self.warn = QLabel("")
        self.warn.setWordWrap(True)
        self.warn.setTextFormat(Qt.RichText)
        layout.addWidget(self.warn)

        self.name_label = QLabel("")
        self.name_label.setWordWrap(True)
        self.name_edit = QLineEdit()
        self.name_edit.textChanged.connect(lambda *_: self._update())
        layout.addWidget(self.name_label)
        layout.addWidget(self.name_edit)

        self.confirm = QCheckBox(tr("Hedef diskteki BUTUN veriler silinecek; "
                                    "anladim"))
        self.confirm.stateChanged.connect(lambda *_: self._update())
        layout.addWidget(self.confirm)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText(tr("Klonla"))
        self.buttons.button(QDialogButtonBox.Cancel).setText(tr("Iptal"))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._update()

    # ------------------------------------------------------------------
    def selected(self) -> Optional[DiskSource]:
        item = self.list.currentItem()
        if item is None or not item.flags() & Qt.ItemIsEnabled:
            return None
        return item.data(Qt.UserRole)

    @property
    def allow_system(self) -> bool:
        src = self.selected()
        return bool(src is not None and src.disk.is_system)

    def problem(self) -> str:
        """Klonla dugmesini kapatan neden (bos = hazir)."""
        src = self.selected()
        if src is None:
            return tr("Hedef diski secin.")
        if src.disk.is_system and self.name_edit.text().strip() != src.disk.name:
            return tr("Sistem diski: onaylamak icin disk adini yazin.")
        if not self.confirm.isChecked():
            return tr("Silme onayini isaretleyin.")
        return ""

    def _update(self) -> None:
        src = self.selected()
        system = bool(src is not None and src.disk.is_system)
        self.name_label.setVisible(system)
        self.name_edit.setVisible(system)
        if system:
            self.name_label.setText(
                tr("<b>{}</b> isletim sistemi diskidir. Uzerine yazmak sistemi "
                   "acilamaz hale getirir. Onaylamak icin disk adini yazin: "
                   "<b>{}</b>", src.disk.path, src.disk.name))
        notes = []
        if src is not None:
            notes.append(tr("<b>{}</b> uzerindeki bolum tablosu ve butun "
                            "bolumler kaybolacak.", src.disk.name))
            if src.disk.size > self.source_size:
                # Kopya kaynak boyu kadardir; kalan ayrilmamis alan olur ama
                # eski veri orada fiziksel olarak durur — dogruyu soyle.
                notes.append(tr("Hedef kaynaktan {} buyuk: bu kisim ayrilmamis "
                                "alan olur, eski veri fiziksel olarak orada "
                                "kalir (tamamen yok etmek icin Guvenli silme).",
                                human_size(src.disk.size - self.source_size)))
            if src.disk.mounted:
                notes.append(tr("Bu diskte bagli bolumler var: {} — yazmadan "
                                "once cikarmaniz onerilir.",
                                ", ".join(src.disk.mounted)))
        self.warn.setText("<br>".join(
            f"<span style='color:{COLOR_WARN}'>{n}</span>" for n in notes))
        problem = self.problem()
        ok = self.buttons.button(QDialogButtonBox.Ok)
        ok.setEnabled(not problem)
        ok.setToolTip(problem)
