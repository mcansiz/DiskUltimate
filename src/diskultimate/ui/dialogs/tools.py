"""Arac pencereleri: guvenli silme, kurtarma, imza taramasi, yedek bilgisi."""
from __future__ import annotations

import os
from typing import List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFontDatabase
from PyQt5.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog,
                             QDialogButtonBox, QFormLayout, QGroupBox,
                             QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QPlainTextEdit,
                             QPushButton,
                             QRadioButton, QTreeWidget, QTreeWidgetItem,
                             QVBoxLayout, QWidget)

from ...core.ptable import human_size
from ...core.recovery import SIGNATURES
from ...core.wipe import WIPE_METHODS
from ..theme import fs_color
from ..widgets.partition_table import color_chip
from ...i18n import tr


class WipeDialog(QDialog):
    """Guvenli silme secenekleri."""

    def __init__(self, parent=None, target_label: str = "", size: int = 0,
                 allow_free_space: bool = False):
        super().__init__(parent)
        self.setWindowTitle(tr("Guvenli Silme"))
        self.setMinimumWidth(520)
        self._build(target_label, size, allow_free_space)

    def _build(self, target: str, size_bytes: int, allow_free_space: bool) -> None:
        duzen = QVBoxLayout(self)
        duzen.setSpacing(10)
        duzen.addWidget(QLabel(tr("<b>Hedef:</b> {} — {}",
                                  target, human_size(size_bytes))))

        kapsam = QGroupBox(tr("Kapsam"))
        kapsam_duzen = QVBoxLayout(kapsam)
        self.radio_tum = QRadioButton(tr("Tum alani sil (icindeki her sey yok olur)"))
        self.radio_tum.setChecked(True)
        kapsam_duzen.addWidget(self.radio_tum)
        self.radio_bos = QRadioButton(
            tr("Yalnizca bos alani sil (mevcut dosyalar korunur, silinmis dosyalarin "
            "artigi yok edilir)"))
        self.radio_bos.setEnabled(allow_free_space)
        if not allow_free_space:
            self.radio_bos.setToolTip(
                tr("Bu secenek yalnizca okunabilir bir dosya sistemi varsa "
                   "kullanilabilir"))
        kapsam_duzen.addWidget(self.radio_bos)
        duzen.addWidget(kapsam)

        yontem_grup = QGroupBox(tr("Yontem"))
        form = QFormLayout(yontem_grup)
        self.method_combo = QComboBox()
        for m in WIPE_METHODS:
            self.method_combo.addItem(tr(m.label), m.key)
        self.method_combo.currentIndexChanged.connect(self._method_changed)
        form.addRow(tr("Silme yontemi:"), self.method_combo)
        self.method_info = QLabel()
        self.method_info.setWordWrap(True)
        self.method_info.setEnabled(False)   # paletten soluk ton
        form.addRow("", self.method_info)
        self.verify_check = QCheckBox(tr("Silme sonrasi dogrula (yalnizca "
                                         "sifirlamada)"))
        form.addRow("", self.verify_check)
        duzen.addWidget(yontem_grup)
        self.radio_bos.toggled.connect(lambda v: yontem_grup.setEnabled(not v))

        warning = QLabel(tr("<span style='color:#b23c17'><b>Uyari:</b> Bu islem geri "
                       "alinamaz. Silinen veriler kurtarilamaz.</span>"))
        warning.setWordWrap(True)
        duzen.addWidget(warning)

        butonlar = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        butonlar.button(QDialogButtonBox.Ok).setText(tr("Sil"))
        butonlar.button(QDialogButtonBox.Ok).setProperty("primary", True)
        butonlar.button(QDialogButtonBox.Cancel).setText(tr("Iptal"))
        butonlar.accepted.connect(self.accept)
        butonlar.rejected.connect(self.reject)
        duzen.addWidget(butonlar)
        self._method_changed()

    def _method_changed(self) -> None:
        key = self.method_combo.currentData()
        for m in WIPE_METHODS:
            if m.key == key:
                self.method_info.setText(
                    tr("{} ({} gecis)", tr(m.description), m.pass_count))
                self.verify_check.setEnabled(m.passes[-1] == "zero")
                break

    def values(self) -> dict:
        return {
            "scope": "free" if self.radio_bos.isChecked() else "all",
            "method": self.method_combo.currentData(),
            "verify": self.verify_check.isChecked() and self.verify_check.isEnabled(),
        }


class DeletedFilesDialog(QDialog):
    """Silinmis dosya tarama sonuclari."""

    def __init__(self, items: List, parent=None, title: str = ""):
        super().__init__(parent)
        self.items = items
        self.setWindowTitle(title or tr("Silinmis Dosyalar"))
        self.resize(900, 560)
        self._build()

    def _build(self) -> None:
        duzen = QVBoxLayout(self)
        kurtarilabilir = sum(1 for i in self.items if i.confidence >= 100)
        duzen.addWidget(QLabel(
            tr("<b>{}</b> silinmis giris bulundu — <b>{}</b> tanesi eksiksiz "
               "kurtarilabilir gorunuyor.", len(self.items), kurtarilabilir)))

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("Ad"), tr("Yol"), tr("Boyut"), tr("Durum"), tr("Kurtarilabilirlik")])
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setAlternatingRowColors(True)
        self.tree.setRootIsDecorated(False)
        self.tree.setSortingEnabled(True)
        self.tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        for oge in self.items:
            item = QTreeWidgetItem(self.tree)
            item.setText(0, oge.name)
            item.setText(1, oge.path)
            item.setText(2, human_size(oge.size))
            item.setText(3, oge.condition)
            item.setText(4, f"%{oge.confidence}")
            item.setTextAlignment(2, Qt.AlignRight | Qt.AlignVCenter)
            item.setTextAlignment(4, Qt.AlignRight | Qt.AlignVCenter)
            item.setData(0, Qt.UserRole, oge)
            if oge.is_dir or oge.size == 0:
                item.setDisabled(True)
        for i, w in enumerate((240, 0, 90, 170, 120)):
            if w:
                self.tree.setColumnWidth(i, w)
        duzen.addWidget(self.tree, 1)

        lower = QHBoxLayout()
        sec_tum = QPushButton(tr("Tumunu sec"))
        sec_tum.clicked.connect(self.tree.selectAll)
        lower.addWidget(sec_tum)
        lower.addStretch(1)
        duzen.addLayout(lower)

        butonlar = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        butonlar.button(QDialogButtonBox.Ok).setText(tr("Secilenleri kurtar..."))
        butonlar.button(QDialogButtonBox.Ok).setProperty("primary", True)
        butonlar.button(QDialogButtonBox.Cancel).setText(tr("Kapat"))
        butonlar.accepted.connect(self.accept)
        butonlar.rejected.connect(self.reject)
        duzen.addWidget(butonlar)

    def selected(self) -> List:
        return [i.data(0, Qt.UserRole) for i in self.tree.selectedItems()
                if not i.isDisabled()]


class LostPartitionsDialog(QDialog):
    """Kayip bolum tarama sonuclari."""

    def __init__(self, items: List, parent=None):
        super().__init__(parent)
        self.items = items
        self.setWindowTitle(tr("Kayip Bolum Tarama Sonuclari"))
        self.resize(760, 460)
        self._build()

    def _build(self) -> None:
        duzen = QVBoxLayout(self)
        duzen.addWidget(QLabel(
            tr("Bolum tablosunda bulunmayan <b>{}</b> dosya sistemi tespit "
               "edildi. Tabloya eklemek istediginizi secin.", len(self.items))))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("Baslangic LBA"), tr("Boyut"), tr("Dosya "
                                                                     "sistemi"), tr("Etiket")])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        for oge in self.items:
            item = QTreeWidgetItem(self.tree)
            item.setText(0, str(oge.start_lba))
            item.setText(1, human_size(oge.size))
            item.setText(2, oge.fs_type)
            item.setText(3, oge.label or "-")
            item.setData(0, Qt.UserRole, oge)
        for i, w in enumerate((120, 110, 120, 200)):
            self.tree.setColumnWidth(i, w)
        duzen.addWidget(self.tree, 1)
        not_etiketi = QLabel(
            tr("Not: Eklenen bolum bicimlendirilmez; yalnizca tabloya kaydedilir. "
            "Cakisma varsa islem reddedilir."))
        not_etiketi.setEnabled(False)
        not_etiketi.setWordWrap(True)
        duzen.addWidget(not_etiketi)

        butonlar = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        butonlar.button(QDialogButtonBox.Ok).setText(tr("Tabloya ekle"))
        butonlar.button(QDialogButtonBox.Ok).setProperty("primary", True)
        butonlar.button(QDialogButtonBox.Cancel).setText(tr("Kapat"))
        butonlar.accepted.connect(self.accept)
        butonlar.rejected.connect(self.reject)
        duzen.addWidget(butonlar)

    def selected(self):
        selected = self.tree.selectedItems()
        return selected[0].data(0, Qt.UserRole) if selected else None


class CarveOptionsDialog(QDialog):
    """Imza tabanli tarama icin dosya turu secimi."""

    def __init__(self, parent=None, scope_label: str = ""):
        super().__init__(parent)
        self.setWindowTitle(tr("Imza Tabanli Dosya Kurtarma"))
        self.setMinimumWidth(460)
        self._build(scope_label)

    def _build(self, kapsam: str) -> None:
        duzen = QVBoxLayout(self)
        duzen.addWidget(QLabel(
            tr("<b>Tarama alani:</b> {}<br>Dizin kaydi olmadan, dosya "
               "imzalarindan kurtarma yapilir. Bicimlendirilmis alanlarda da "
               "calisir.", kapsam)))
        self.liste = QListWidget()
        self.liste.setSelectionMode(QAbstractItemView.NoSelection)
        for imza in SIGNATURES:
            item = QListWidgetItem(
                tr("{}  (.{})", tr(imza.label), imza.extension))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if imza.key in
                               ("jpg", "png", "pdf", "zip") else Qt.Unchecked)
            item.setData(Qt.UserRole, imza.key)
            self.liste.addItem(item)
        duzen.addWidget(self.liste, 1)

        lower = QHBoxLayout()
        tumu = QPushButton(tr("Tumunu sec"))
        tumu.clicked.connect(lambda: self._set_all(Qt.Checked))
        hicbiri = QPushButton(tr("Temizle"))
        hicbiri.clicked.connect(lambda: self._set_all(Qt.Unchecked))
        lower.addWidget(tumu)
        lower.addWidget(hicbiri)
        lower.addStretch(1)
        duzen.addLayout(lower)

        butonlar = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        butonlar.button(QDialogButtonBox.Ok).setText(tr("Taramayi baslat"))
        butonlar.button(QDialogButtonBox.Ok).setProperty("primary", True)
        butonlar.button(QDialogButtonBox.Cancel).setText(tr("Iptal"))
        butonlar.accepted.connect(self.accept)
        butonlar.rejected.connect(self.reject)
        duzen.addWidget(butonlar)

    def _set_all(self, state) -> None:
        for i in range(self.liste.count()):
            self.liste.item(i).setCheckState(state)

    def selected_keys(self) -> List[str]:
        return [self.liste.item(i).data(Qt.UserRole)
                for i in range(self.liste.count())
                if self.liste.item(i).checkState() == Qt.Checked]


class CarvedFilesDialog(QDialog):
    """Imza taramasi sonuclari."""

    def __init__(self, items: List, parent=None):
        super().__init__(parent)
        self.items = items
        self.setWindowTitle(tr("Bulunan Dosyalar"))
        self.resize(820, 520)
        duzen = QVBoxLayout(self)
        duzen.addWidget(QLabel(tr("<b>{}</b> dosya imzasi bulundu.", len(items))))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("Tur"), tr("Ofset"), tr("Boyut"), tr("Onerilen "
                                                                        "ad")])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setSortingEnabled(True)
        self.tree.header().setSectionResizeMode(3, QHeaderView.Stretch)
        for oge in items:
            item = QTreeWidgetItem(self.tree)
            item.setText(0, tr(oge.label))
            item.setText(1, f"0x{oge.offset:X}")
            item.setText(2, human_size(oge.size))
            item.setText(3, oge.suggested_name)
            item.setTextAlignment(2, Qt.AlignRight | Qt.AlignVCenter)
            item.setData(0, Qt.UserRole, oge)
        for i, w in enumerate((190, 130, 100)):
            self.tree.setColumnWidth(i, w)
        duzen.addWidget(self.tree, 1)
        sec = QPushButton(tr("Tumunu sec"))
        sec.clicked.connect(self.tree.selectAll)
        lower = QHBoxLayout()
        lower.addWidget(sec)
        lower.addStretch(1)
        duzen.addLayout(lower)
        butonlar = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        butonlar.button(QDialogButtonBox.Ok).setText(tr("Secilenleri cikar..."))
        butonlar.button(QDialogButtonBox.Ok).setProperty("primary", True)
        butonlar.button(QDialogButtonBox.Cancel).setText(tr("Kapat"))
        butonlar.accepted.connect(self.accept)
        butonlar.rejected.connect(self.reject)
        duzen.addWidget(butonlar)

    def selected(self) -> List:
        return [i.data(0, Qt.UserRole) for i in self.tree.selectedItems()]


class InfoDialog(QDialog):
    """Anahtar/deger listesi gosteren basit bilgi penceresi."""

    def __init__(self, title: str, rows: dict, parent=None, note: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(480)
        duzen = QVBoxLayout(self)
        grup = QGroupBox(title)
        form = QFormLayout(grup)
        for key, value in rows.items():
            etiket = QLabel(str(value))
            etiket.setTextInteractionFlags(Qt.TextSelectableByMouse)
            form.addRow(f"{key}:", etiket)
        duzen.addWidget(grup)
        if note:
            n = QLabel(note)
            n.setWordWrap(True)
            n.setEnabled(False)
            duzen.addWidget(n)
        butonlar = QDialogButtonBox(QDialogButtonBox.Close)
        butonlar.button(QDialogButtonBox.Close).setText(tr("Kapat"))
        butonlar.rejected.connect(self.reject)
        duzen.addWidget(butonlar)


class TextViewDialog(QDialog):
    """Uzun duz metni (donma raporu, gunluk) sabit genislikli gosterir."""

    def __init__(self, title: str, text: str, parent=None, note: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(900, 620)
        duzen = QVBoxLayout(self)
        if note:
            n = QLabel(note)
            n.setWordWrap(True)
            n.setEnabled(False)
            duzen.addWidget(n)
        view = QPlainTextEdit(text)
        view.setReadOnly(True)
        mono = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        mono.setPointSize(9)
        view.setFont(mono)
        view.setLineWrapMode(QPlainTextEdit.NoWrap)
        duzen.addWidget(view, 1)
        butonlar = QDialogButtonBox(QDialogButtonBox.Close)
        butonlar.button(QDialogButtonBox.Close).setText(tr("Kapat"))
        butonlar.rejected.connect(self.reject)
        duzen.addWidget(butonlar)
