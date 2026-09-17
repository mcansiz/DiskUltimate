"""Bolum olusturma ve bicimlendirme pencereleri."""
from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QBrush, QPalette
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout,
                             QLabel, QLineEdit, QMessageBox, QSlider,
                             QVBoxLayout)

from ...core.formatter import FS_BY_KEY, all_kinds
from ...core.ptable import FreeRegion, Partition, human_size
from ...i18n import tr

MIB = 1024 * 1024


def _fill_fs_combo(combo: QComboBox, size_bytes: int = 0) -> None:
    """Combo'yu TUM dosya sistemleriyle doldurur.

    Kullanilamayanlar da listelenir ama secilemez; yaninda nedeni yazar
    (orn. "mkfs.ntfs kurulu degil"). Amac, kullanicinin eksik secenegi degil
    eksigin **nedenini** gormesi.
    """
    model = combo.model()
    for kind, reason in all_kinds(size_bytes):
        metin = kind.label if not reason else f"{kind.label}  —  {reason}"
        combo.addItem(metin, kind.key)
        if reason:
            satir = combo.count() - 1
            oge = model.item(satir) if hasattr(model, "item") else None
            if oge is not None:
                oge.setEnabled(False)
                combo.setItemData(satir, reason, Qt.ToolTipRole)


class CreatePartitionDialog(QDialog):
    """Bos alanda yeni bolum tanimlar."""

    def __init__(self, region: FreeRegion, scheme: str, parent=None,
                 can_primary: bool = True, has_extended: bool = False,
                 inside_extended: bool = False):
        super().__init__(parent)
        self.setWindowTitle(tr("Yeni Bolum Olustur"))
        self.setMinimumWidth(520)
        self.region = region
        self.scheme = scheme
        self.sector_size = region.sector_size
        self._can_primary = can_primary
        self._has_extended = has_extended
        self._inside_extended = inside_extended
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        info = QLabel(
            tr("Bos alan: <b>{}</b> (LBA {} – {})",
               human_size(self.region.size), self.region.start_lba,
               self.region.end_lba))
        layout.addWidget(info)

        grup = QGroupBox(tr("Bolum ayarlari"))
        form = QFormLayout(grup)

        # boyut
        size_line = QHBoxLayout()
        self.size_spin = QDoubleSpinBox()
        self.size_spin.setDecimals(2)
        self.size_spin.setRange(1.0, max(1.0, self.region.size / MIB))
        self.size_spin.setValue(max(1.0, self.region.size / MIB))
        self.size_spin.setSuffix(" MB")
        self.size_spin.setFixedWidth(140)
        self.size_spin.valueChanged.connect(self._size_changed)
        size_line.addWidget(self.size_spin)
        self.size_slider = QSlider(Qt.Horizontal)
        self.size_slider.setRange(1, max(1, int(self.region.size / MIB)))
        self.size_slider.setValue(self.size_slider.maximum())
        self.size_slider.valueChanged.connect(self._slider_changed)
        size_line.addWidget(self.size_slider, 1)
        form.addRow(tr("Boyut:"), size_line)

        # tur (MBR)
        if self.scheme == "mbr":
            self.kind_combo = QComboBox()
            if self._inside_extended:
                self.kind_combo.addItem(tr("Mantiksal bolum"), "logical")
            else:
                if self._can_primary:
                    self.kind_combo.addItem(tr("Birincil bolum"), "primary")
                if not self._has_extended and self._can_primary:
                    self.kind_combo.addItem(tr("Genisletilmis bolum "
                                               "(kapsayici)"), "extended")
            form.addRow(tr("Bolum turu:"), self.kind_combo)
            self.kind_combo.currentIndexChanged.connect(self._kind_changed)
        else:
            self.kind_combo = None
            self.name_edit = QLineEdit("")
            self.name_edit.setMaxLength(36)
            self.name_edit.setPlaceholderText(tr("GPT bolum adi (istege bagli)"))
            form.addRow(tr("Bolum adi:"), self.name_edit)

        # dosya sistemi
        self.fs_combo = QComboBox()
        self.fs_combo.addItem(tr("Bicimlendirme (ham bolum)"), "")
        _fill_fs_combo(self.fs_combo, self.region.size)
        idx = self.fs_combo.findData("fat32")
        self.fs_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.fs_combo.currentIndexChanged.connect(self._fs_changed)
        form.addRow(tr("Dosya sistemi:"), self.fs_combo)

        self.label_edit = QLineEdit("")
        self.label_edit.setMaxLength(32)
        self.label_edit.setPlaceholderText(tr("Birim etiketi (istege bagli)"))
        form.addRow(tr("Etiket:"), self.label_edit)

        self.boot_check = QCheckBox(tr("Onyuklenebilir olarak isaretle"))
        form.addRow("", self.boot_check)
        layout.addWidget(grup)

        self.summary = QLabel()
        self.summary.setEnabled(False)   # paletten soluk ton
        layout.addWidget(self.summary)

        butonlar = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        butonlar.button(QDialogButtonBox.Ok).setText(tr("Olustur"))
        butonlar.button(QDialogButtonBox.Cancel).setText(tr("Iptal"))
        butonlar.button(QDialogButtonBox.Ok).setProperty("primary", True)
        butonlar.accepted.connect(self.accept)
        butonlar.rejected.connect(self.reject)
        layout.addWidget(butonlar)
        self._update_summary()

    # -- etkilesim -----------------------------------------------------------
    def _slider_changed(self, value: int) -> None:
        self.size_spin.blockSignals(True)
        self.size_spin.setValue(float(value))
        self.size_spin.blockSignals(False)
        self._update_summary()

    def _size_changed(self, value: float) -> None:
        self.size_slider.blockSignals(True)
        self.size_slider.setValue(int(value))
        self.size_slider.blockSignals(False)
        self._update_summary()

    def _kind_changed(self) -> None:
        genisletilmis = self.kind_combo and self.kind_combo.currentData() == "extended"
        self.fs_combo.setEnabled(not genisletilmis)
        self.label_edit.setEnabled(not genisletilmis)
        self._update_summary()

    def _fs_changed(self) -> None:
        var = bool(self.fs_combo.currentData())
        self.label_edit.setEnabled(var)
        self._update_summary()

    def _update_summary(self) -> None:
        sector = self.sector_count()
        fs = self.fs_combo.currentData()
        name = FS_BY_KEY[fs].label if fs else tr("bicimlendirilmemis")
        self.summary.setText(
            tr("{} — {} sektor — {}", human_size(sector * self.sector_size),
               f"{sector:,}".replace(",", "."), name))

    # -- degerler ------------------------------------------------------------
    def sector_count(self) -> int:
        istenen = int(self.size_spin.value() * MIB) // self.sector_size
        return max(1, min(istenen, self.region.sector_count))

    def values(self) -> dict:
        kind = self.kind_combo.currentData() if self.kind_combo else None
        return {
            "start_lba": self.region.start_lba,
            "sector_count": self.sector_count(),
            "fs": self.fs_combo.currentData() if kind != "extended" else "",
            "label": self.label_edit.text().strip(),
            "name": getattr(self, "name_edit", None).text().strip()
                    if hasattr(self, "name_edit") else "",
            "bootable": self.boot_check.isChecked(),
            "kind": kind,
        }


class FormatDialog(QDialog):
    """Mevcut bolumu bicimlendirir."""

    def __init__(self, partition: Partition, parent=None):
        super().__init__(parent)
        self.partition = partition
        self.setWindowTitle(tr("Bolum {} Bicimlendir", partition.index))
        self.setMinimumWidth(460)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        p = self.partition
        layout.addWidget(QLabel(
            tr("<b>Bolum {}</b> — {}<br>Mevcut dosya sistemi: {}",
               p.index, human_size(p.size), p.fs_type or 'yok')))

        grup = QGroupBox(tr("Bicimlendirme secenekleri"))
        form = QFormLayout(grup)
        self.fs_combo = QComboBox()
        _fill_fs_combo(self.fs_combo, p.size)
        idx = self.fs_combo.findData((p.fs_type or "fat32").lower())
        self.fs_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.fs_combo.currentIndexChanged.connect(self._fs_changed)
        form.addRow(tr("Dosya sistemi:"), self.fs_combo)

        self.label_edit = QLineEdit(p.fs_label or "")
        self.label_edit.setMaxLength(32)
        form.addRow(tr("Birim etiketi:"), self.label_edit)

        self.cluster_combo = QComboBox()
        form.addRow(tr("Kume boyutu:"), self.cluster_combo)

        self.quick_check = QCheckBox(tr("Hizli bicimlendirme (veri alani silinmez)"))
        self.quick_check.setChecked(True)
        form.addRow("", self.quick_check)
        layout.addWidget(grup)

        warning = QLabel(tr("<span style='color:#b23c17'>Uyari: bolumdeki tum "
                            "veriler silinir.</span>"))
        layout.addWidget(warning)

        butonlar = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        butonlar.button(QDialogButtonBox.Ok).setText(tr("Bicimlendir"))
        butonlar.button(QDialogButtonBox.Cancel).setText(tr("Iptal"))
        butonlar.button(QDialogButtonBox.Ok).setProperty("primary", True)
        butonlar.accepted.connect(self.accept)
        butonlar.rejected.connect(self.reject)
        layout.addWidget(butonlar)
        self._fs_changed()

    def _fs_changed(self) -> None:
        self.cluster_combo.clear()
        self.cluster_combo.addItem(tr("Varsayilan"), 0)
        fs = self.fs_combo.currentData() or ""
        if fs.startswith("fat") or fs == "exfat" or fs == "ntfs":
            for kb in (0.5, 1, 2, 4, 8, 16, 32, 64):
                nbytes = int(kb * 1024)
                if nbytes <= self.partition.size // 8:
                    self.cluster_combo.addItem(
                        f"{nbytes} bayt" if nbytes < 1024 else f"{nbytes // 1024} KB", nbytes)
        elif fs.startswith("ext"):
            for nbytes in (1024, 2048, 4096):
                self.cluster_combo.addItem(tr("{} KB blok", nbytes // 1024), nbytes)

    def values(self) -> dict:
        return {
            "fs": self.fs_combo.currentData(),
            "label": self.label_edit.text().strip(),
            "cluster": self.cluster_combo.currentData() or 0,
            "quick": self.quick_check.isChecked(),
        }
