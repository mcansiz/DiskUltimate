"""Yeni disk goruntusu olusturma penceresi."""
from __future__ import annotations

import os
from typing import Optional, Tuple

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QDoubleSpinBox, QFileDialog, QFormLayout,
                             QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                             QMessageBox, QPushButton, QVBoxLayout)

from ...core.formatter import all_kinds
from ...core.ptable import human_size

UNITS = {"MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}


class NewImageDialog(QDialog):
    """Yol, boyut, bolum tablosu ve istege bagli ilk bolum secimi."""

    def __init__(self, parent=None, default_dir: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Yeni Disk Goruntusu")
        self.setMinimumWidth(520)
        self._build(default_dir)

    def _build(self, default_dir: str) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        dosya_grup = QGroupBox("Goruntu dosyasi")
        form = QFormLayout(dosya_grup)
        yol_satiri = QHBoxLayout()
        varsayilan = os.path.join(default_dir or os.path.expanduser("~"), "yeni-disk.img")
        self.path_edit = QLineEdit(varsayilan)
        yol_satiri.addWidget(self.path_edit, 1)
        gozat = QPushButton("Gozat...")
        gozat.setFixedWidth(86)
        gozat.clicked.connect(self._browse)
        yol_satiri.addWidget(gozat)
        form.addRow("Konum:", yol_satiri)

        boyut_satiri = QHBoxLayout()
        self.size_spin = QDoubleSpinBox()
        self.size_spin.setRange(0.06, 16384.0)
        self.size_spin.setDecimals(2)
        self.size_spin.setValue(2.00)
        self.size_spin.setFixedWidth(120)
        self.size_spin.valueChanged.connect(self._update_info)
        boyut_satiri.addWidget(self.size_spin)
        self.unit_combo = QComboBox()
        self.unit_combo.addItems(list(UNITS))
        self.unit_combo.setCurrentText("GB")
        self.unit_combo.setFixedWidth(72)
        self.unit_combo.currentTextChanged.connect(self._update_info)
        boyut_satiri.addWidget(self.unit_combo)
        boyut_satiri.addStretch(1)
        form.addRow("Boyut:", boyut_satiri)

        self.sparse_check = QCheckBox(
            "Seyrek dosya olarak olustur (diskte yalnizca kullanilan alani kaplar)")
        self.sparse_check.setChecked(True)
        form.addRow("", self.sparse_check)
        layout.addWidget(dosya_grup)

        yapi_grup = QGroupBox("Baslangic yapisi")
        form2 = QFormLayout(yapi_grup)
        self.scheme_combo = QComboBox()
        self.scheme_combo.addItem("GPT (modern, 128 bolume kadar)", "gpt")
        self.scheme_combo.addItem("MBR (klasik, 4 birincil bolum)", "mbr")
        self.scheme_combo.addItem("Bolum tablosu olusturma", "")
        form2.addRow("Bolum tablosu:", self.scheme_combo)

        self.auto_check = QCheckBox("Tum alani kaplayan tek bolum olustur ve bicimlendir")
        self.auto_check.setChecked(True)
        self.auto_check.toggled.connect(self._toggle_auto)
        form2.addRow("", self.auto_check)

        self.fs_combo = QComboBox()
        from .partition import _doldur_fs_listesi
        _doldur_fs_listesi(self.fs_combo)
        idx = self.fs_combo.findData("fat32")
        if idx >= 0:
            self.fs_combo.setCurrentIndex(idx)
        form2.addRow("Dosya sistemi:", self.fs_combo)

        self.label_edit = QLineEdit("YENI BIRIM")
        self.label_edit.setMaxLength(32)
        form2.addRow("Birim etiketi:", self.label_edit)
        layout.addWidget(yapi_grup)

        self.info_label = QLabel()
        self.info_label.setEnabled(False)   # paletten soluk ton
        layout.addWidget(self.info_label)

        butonlar = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        butonlar.button(QDialogButtonBox.Ok).setText("Olustur")
        butonlar.button(QDialogButtonBox.Cancel).setText("Iptal")
        butonlar.button(QDialogButtonBox.Ok).setProperty("primary", True)
        butonlar.accepted.connect(self._validate)
        butonlar.rejected.connect(self.reject)
        layout.addWidget(butonlar)
        self._update_info()

    def _toggle_auto(self, value: bool) -> None:
        self.fs_combo.setEnabled(value)
        self.label_edit.setEnabled(value)

    def _browse(self) -> None:
        yol, _ = QFileDialog.getSaveFileName(
            self, "Goruntu dosyasi", self.path_edit.text(),
            "Disk goruntusu (*.img);;Tum dosyalar (*)")
        if yol:
            if not os.path.splitext(yol)[1]:
                yol += ".img"
            self.path_edit.setText(yol)

    def _update_info(self) -> None:
        boyut = self.size_bytes()
        sektor = boyut // 512
        self.info_label.setText(
            f"Toplam {human_size(boyut)} — {sektor:,} sektor x 512 bayt".replace(",", "."))

    def size_bytes(self) -> int:
        return int(self.size_spin.value() * UNITS[self.unit_combo.currentText()])

    def _validate(self) -> None:
        yol = self.path_edit.text().strip()
        if not yol:
            QMessageBox.warning(self, "Eksik bilgi", "Bir dosya yolu girin.")
            return
        klasor = os.path.dirname(os.path.abspath(yol))
        if not os.path.isdir(klasor):
            QMessageBox.warning(self, "Gecersiz konum",
                                f"Klasor bulunamadi:\n{klasor}")
            return
        if os.path.exists(yol):
            cevap = QMessageBox.question(
                self, "Dosya var",
                f"{yol}\n\nDosya zaten var. Uzerine yazilsin mi?\n"
                "Mevcut icerik tamamen kaybolur.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if cevap != QMessageBox.Yes:
                return
        if self.size_bytes() < 64 * 1024:
            QMessageBox.warning(self, "Gecersiz boyut", "En az 64 KB olmalidir.")
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
