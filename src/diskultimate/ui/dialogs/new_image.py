"""Yeni disk goruntusu olusturma penceresi."""
from __future__ import annotations

import os
from typing import Optional, Tuple

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QDoubleSpinBox, QFileDialog, QFormLayout,
                             QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                             QMessageBox, QPushButton, QVBoxLayout)

from ...core import platform
from ...core.formatter import all_kinds
from ...core.ptable import human_size
from ...i18n import tr

UNITS = {"MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}


class NewImageDialog(QDialog):
    """Yol, boyut, bolum tablosu ve istege bagli ilk bolum secimi."""

    def __init__(self, parent=None, default_dir: str = ""):
        super().__init__(parent)
        self.setWindowTitle(tr("Yeni Disk Goruntusu"))
        self.setMinimumWidth(520)
        self._build(default_dir)

    def _build(self, default_dir: str) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        file_group = QGroupBox(tr("Goruntu dosyasi"))
        form = QFormLayout(file_group)
        path_line = QHBoxLayout()
        default = os.path.join(default_dir or platform.default_image_dir(), "yeni-disk.img")
        self.path_edit = QLineEdit(default)
        path_line.addWidget(self.path_edit, 1)
        gozat = QPushButton(tr("Gozat..."))
        gozat.setFixedWidth(86)
        gozat.clicked.connect(self._browse)
        path_line.addWidget(gozat)
        form.addRow(tr("Konum:"), path_line)

        size_line = QHBoxLayout()
        self.size_spin = QDoubleSpinBox()
        self.size_spin.setRange(0.06, 16384.0)
        self.size_spin.setDecimals(2)
        self.size_spin.setValue(2.00)
        self.size_spin.setFixedWidth(120)
        self.size_spin.valueChanged.connect(self._update_info)
        size_line.addWidget(self.size_spin)
        self.unit_combo = QComboBox()
        self.unit_combo.addItems(list(UNITS))
        self.unit_combo.setCurrentText("GB")
        self.unit_combo.setFixedWidth(72)
        self.unit_combo.currentTextChanged.connect(self._update_info)
        size_line.addWidget(self.unit_combo)
        size_line.addStretch(1)
        form.addRow(tr("Boyut:"), size_line)

        self.sparse_check = QCheckBox(
            tr("Seyrek dosya olarak olustur (diskte yalnizca kullanilan alani kaplar)"))
        self.sparse_check.setChecked(True)
        form.addRow("", self.sparse_check)
        layout.addWidget(file_group)

        yapi_grup = QGroupBox(tr("Baslangic yapisi"))
        form2 = QFormLayout(yapi_grup)
        self.scheme_combo = QComboBox()
        self.scheme_combo.addItem(tr("GPT (modern, 128 bolume kadar)"), "gpt")
        self.scheme_combo.addItem(tr("MBR (klasik, 4 birincil bolum)"), "mbr")
        self.scheme_combo.addItem(tr("Bolum tablosu olusturma"), "")
        form2.addRow(tr("Bolum tablosu:"), self.scheme_combo)

        self.auto_check = QCheckBox(tr("Tum alani kaplayan tek bolum olustur "
                                       "ve bicimlendir"))
        self.auto_check.setChecked(True)
        self.auto_check.toggled.connect(self._toggle_auto)
        form2.addRow("", self.auto_check)

        self.fs_combo = QComboBox()
        from .partition import _fill_fs_combo
        _fill_fs_combo(self.fs_combo)
        idx = self.fs_combo.findData("fat32")
        if idx >= 0:
            self.fs_combo.setCurrentIndex(idx)
        form2.addRow(tr("Dosya sistemi:"), self.fs_combo)

        self.label_edit = QLineEdit(tr("YENI BIRIM"))
        self.label_edit.setMaxLength(32)
        form2.addRow(tr("Birim etiketi:"), self.label_edit)
        layout.addWidget(yapi_grup)

        self.info_label = QLabel()
        self.info_label.setEnabled(False)   # paletten soluk ton
        layout.addWidget(self.info_label)

        butonlar = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        butonlar.button(QDialogButtonBox.Ok).setText(tr("Olustur"))
        butonlar.button(QDialogButtonBox.Cancel).setText(tr("Iptal"))
        butonlar.button(QDialogButtonBox.Ok).setProperty("primary", True)
        butonlar.accepted.connect(self._validate)
        butonlar.rejected.connect(self.reject)
        layout.addWidget(butonlar)
        self._update_info()

    def _toggle_auto(self, value: bool) -> None:
        self.fs_combo.setEnabled(value)
        self.label_edit.setEnabled(value)

    def _browse(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, tr("Goruntu dosyasi"), self.path_edit.text(),
            tr("Disk goruntusu (*.img);;Tum dosyalar (*)"))
        if path:
            if not os.path.splitext(path)[1]:
                path += ".img"
            self.path_edit.setText(path)

    def _update_info(self) -> None:
        size = self.size_bytes()
        sector = size // 512
        self.info_label.setText(
            tr("Toplam {} — {} sektor x 512 bayt", human_size(size),
               f"{sector:,}".replace(",", ".")))

    def size_bytes(self) -> int:
        return int(self.size_spin.value() * UNITS[self.unit_combo.currentText()])

    def _validate(self) -> None:
        path = self.path_edit.text().strip()
        if not path:
            QMessageBox.warning(self, tr("Eksik bilgi"), tr("Bir dosya yolu girin."))
            return
        klasor = os.path.dirname(os.path.abspath(path))
        if not os.path.isdir(klasor):
            QMessageBox.warning(self, tr("Gecersiz konum"),
                                tr("Klasor bulunamadi:\n{}", klasor))
            return
        blocker, warning = platform.image_location_problem(
            klasor, self.size_bytes(), self.sparse_check.isChecked())
        if blocker:
            QMessageBox.warning(self, tr("Uygun olmayan konum"), blocker)
            return
        if warning:
            cevap = QMessageBox.question(
                self, tr("Konum uyarisi"),
                tr("{}\n\nYine de bu konumda olusturulsun mu?", warning),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if cevap != QMessageBox.Yes:
                return
        if os.path.exists(path):
            cevap = QMessageBox.question(
                self, tr("Dosya var"),
                tr("{}\n\nDosya zaten var. Uzerine yazilsin mi?\nMevcut "
                   "icerik tamamen kaybolur.", path),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if cevap != QMessageBox.Yes:
                return
        if self.size_bytes() < 64 * 1024:
            QMessageBox.warning(self, tr("Gecersiz boyut"), tr("En az 64 KB "
                                                            "olmalidir."))
            return
        self.accept()

    def values(self) -> dict:
        return {
            "path": self.path_edit.text().strip(),
            "size": self.size_bytes(),
            "sparse": self.sparse_check.isChecked(),
            "scheme": self.scheme_combo.currentData(),
            "auto_partition": self.auto_check.isChecked() and bool(self.scheme_combo.currentData()),
            "fs": self.fs_combo.currentData(),
            "label": self.label_edit.text().strip(),
        }
