"""Bolum yeniden boyutlandirma penceresi.

Ustte suruklenebilir serit (`ResizeBar`), altta ayni degerleri sayisal olarak
gosteren kutular bulunur. Iki taraf cift yonlu baglidir: serit suruklenince
kutular, kutulara yazilinca serit guncellenir.

Pencere hicbir sey yazmaz; yalnizca **istenen yerlesimi** dondurur. Dogrulama
ve uygulama `core.resize` tarafindadir.
"""
from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QDialog, QDialogButtonBox, QDoubleSpinBox,
                             QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                             QLayout, QMessageBox, QPushButton, QSpinBox,
                             QVBoxLayout, QWidget)

from ...core.fsregistry import fs_display
from ...core.ptable import Partition, human_size
from ...core.resize import FsResizeInfo, ResizeError, ResizeWindow
from ..widgets.resize_bar import ResizeBar
from ...i18n import tr

MIB = 1024 * 1024


class ResizePartitionDialog(QDialog):
    """Bolumu surukleyerek veya sayi girerek yeniden boyutlandirir."""

    def __init__(self, part: Partition, window: ResizeWindow,
                 fs_info: FsResizeInfo, align_sectors: int = 2048,
                 used_bytes: int = -1, parent=None, accept_text: str = ""):
        super().__init__(parent)
        self.accept_text = accept_text or tr("Baslat")
        self.setWindowTitle(tr("Bolumu Boyutlandir"))
        self.setMinimumWidth(660)
        self.part = part
        self.win = window
        self.fs = fs_info
        self.align = max(1, align_sectors)
        self.ss = part.sector_size
        self.used_bytes = used_bytes
        self._sessiz = False

        self.min_count = max(1, fs_info.min_sectors)
        tavan = fs_info.max_sectors or window.sector_count
        self.max_count = max(self.min_count, min(tavan, window.sector_count))
        self._build()
        self._from_bar(part.start_lba, part.sector_count)

    # ----------------------------------------------------------------- kurulum
    def _build(self) -> None:
        duzen = QVBoxLayout(self)
        duzen.setSpacing(10)
        # pencere kucultuldugunde kutular ezilmesin
        duzen.setSizeConstraint(QLayout.SetMinimumSize)

        title = QLabel(
            tr("<b>{}</b> &nbsp; {} &nbsp; {} &nbsp; {} &nbsp;—&nbsp; "
               "kapsayici alan <b>{}</b>",
               self.part.display_name, self.part.type_name,
               fs_display(self.fs.fs_type) or tr('bicimlendirilmemis'),
               human_size(self.part.size), human_size(self.win.size)))
        duzen.addWidget(title)

        self.bar = ResizeBar(self)
        self.bar.setup(self.win.start_lba, self.win.sector_count,
                       self.part.start_lba, self.part.sector_count,
                       self.ss, self.align, self.min_count, self.max_count,
                       fs_type=self.fs.fs_type, label=self.part.display_name,
                       used_bytes=self.used_bytes, can_move=self.fs.movable)
        self.bar.rangeChanged.connect(self._from_bar)
        duzen.addWidget(self.bar)

        ipucu = QLabel(tr("Serit uzerindeki tutamaklari fareyle saga/sola surukleyin. "
                       "Ok tuslari ince ayar yapar (Shift ile hizli)."))
        ipucu.setWordWrap(True)
        duzen.addWidget(ipucu)

        grup = QGroupBox(tr("(Gecerli Veri Ayrimi)"))
        govde = QHBoxLayout(grup)
        sol = QFormLayout()
        sag = QFormLayout()
        govde.addLayout(sol, 1)
        govde.addLayout(sag, 1)

        self.kapasite = self._mb_spin()
        self.kapasite.valueChanged.connect(self._on_capacity)
        sol.addRow(tr("Yeni Kapasite:"), self.kapasite)

        self.on_bosluk = self._mb_spin()
        self.on_bosluk.valueChanged.connect(self._on_gap_before)
        sol.addRow(tr("Onundeki Bosluk:"), self.on_bosluk)

        self.arka_bosluk = self._mb_spin()
        self.arka_bosluk.valueChanged.connect(self._on_gap_after)
        sol.addRow(tr("Arkasindaki Bosluk:"), self.arka_bosluk)

        self.start_offset = self._offset_spin()
        self.start_offset.setRange(0, 2 ** 31 - 1)
        self.start_offset.setGroupSeparatorShown(True)
        self.start_offset.valueChanged.connect(self._on_offset)
        sag.addRow(tr("Baslangic Kesimi:"), self.start_offset)

        self.end_offset = self._offset_spin()
        self.end_offset.setRange(0, 2 ** 31 - 1)
        self.end_offset.setGroupSeparatorShown(True)
        self.end_offset.valueChanged.connect(self._on_offset)
        sag.addRow(tr("Bitis Kesimi:"), self.end_offset)

        duzen.addWidget(grup)

        self.sinir = QLabel()
        self.sinir.setWordWrap(True)
        self.sinir.setTextFormat(Qt.RichText)
        duzen.addWidget(self.sinir)

        self.ozet = QLabel()
        self.ozet.setWordWrap(True)
        self.ozet.setTextFormat(Qt.RichText)
        duzen.addWidget(self.ozet)

        dugmeler = QDialogButtonBox()
        self.basla = dugmeler.addButton(self.accept_text,
                                        QDialogButtonBox.AcceptRole)
        dugmeler.addButton(tr("Iptal"), QDialogButtonBox.RejectRole)
        sifirla = QPushButton(tr("Eski haline dondur"))
        sifirla.clicked.connect(
            lambda: self._from_bar(self.part.start_lba, self.part.sector_count,
                                   update_bar=True))
        dugmeler.addButton(sifirla, QDialogButtonBox.ResetRole)
        dugmeler.accepted.connect(self.accept)
        dugmeler.rejected.connect(self.reject)
        duzen.addWidget(dugmeler)

        self.sinir.setText(self._limits_text())
        # Dikey yonde kucultmeye izin verilmez: QFormLayout satirlarinin
        # "en kucuk" yuksekligi tercih edilenden dusuktur ve kutular ust uste
        # biner. Genislik serbest kalir (serit genisledikce daha hassas olur).
        self.setMinimumHeight(self.layout().sizeHint().height())

    def _mb_spin(self) -> QDoubleSpinBox:
        kutu = QDoubleSpinBox()
        kutu.setDecimals(2)
        kutu.setRange(0.0, 1024.0 * 1024.0 * 16)
        kutu.setSuffix(" MB")
        kutu.setFixedWidth(150)
        kutu.setKeyboardTracking(False)
        # pencere en kucuk boyuta inince kutu metni kirpilmasin
        kutu.setMinimumHeight(kutu.sizeHint().height())
        return kutu

    def _offset_spin(self) -> QSpinBox:
        kutu = QSpinBox()
        kutu.setKeyboardTracking(False)
        kutu.setMinimumHeight(kutu.sizeHint().height())
        return kutu

    def _limits_text(self) -> str:
        parcalar = []
        if self.used_bytes >= 0:
            # Dolu alan ve en az boyut yan yana: kullanici neden daha fazla
            # kuculemedigini (ya da ext'te neden dolunun altina inebildigini)
            # gorur (ADR 0074).
            parcalar.append(tr("<b>Dolu:</b> {}", human_size(self.used_bytes)))
        parcalar += [tr("<b>Sinirlar:</b> en az {}",
                        human_size(self.min_count * self.ss)),
                     tr("en cok {}", human_size(self.max_count * self.ss))]
        if self.fs.note:
            parcalar.append(self.fs.note)
        if not self.fs.movable:
            parcalar.append(tr("baslangic degistirilemez"))
        return " · ".join(parcalar)

    # ------------------------------------------------------------- baglantilar
    def _from_bar(self, start: int, count: int, update_bar: bool = False) -> None:
        if self._sessiz:
            return
        self._sessiz = True
        try:
            if update_bar:
                self.bar.set_range(start, count)
            self.kapasite.setValue(count * self.ss / MIB)
            self.on_bosluk.setValue((start - self.win.start_lba) * self.ss / MIB)
            self.arka_bosluk.setValue(
                (self.win.start_lba + self.win.sector_count - start - count)
                * self.ss / MIB)
            self.start_offset.setValue(start)
            self.end_offset.setValue(start + count - 1)
        finally:
            self._sessiz = False
        self._write_summary()

    def _on_capacity(self, mb: float) -> None:
        if self._sessiz:
            return
        self._set_range(self.bar.start, self._sectors(mb))

    def _on_gap_before(self, mb: float) -> None:
        if self._sessiz:
            return
        self._set_range(self.win.start_lba + self._sectors(mb), self.bar.count)

    def _on_gap_after(self, mb: float) -> None:
        if self._sessiz:
            return
        end = self.win.start_lba + self.win.sector_count - self._sectors(mb)
        self._set_range(self.bar.start, end - self.bar.start)

    def _on_offset(self) -> None:
        if self._sessiz:
            return
        start_lba = self.start_offset.value()
        end = self.end_offset.value()
        self._set_range(start_lba, end - start_lba + 1)

    def _sectors(self, mb: float) -> int:
        ham = int(round(mb * MIB / self.ss))
        return (ham // self.align) * self.align

    def _set_range(self, start: int, count: int) -> None:
        """Kutulardan gelen degeri sinirlara kirpip serite yansitir."""
        if not self.fs.movable:
            start = self.part.start_lba
        count = max(self.min_count, min(self.max_count, count))
        start = max(self.win.start_lba, start)
        upper = self.win.start_lba + self.win.sector_count
        if start + count > upper:
            count = upper - start
        self.bar.set_range(start, count)
        self._from_bar(start, count)

    # ------------------------------------------------------------------- ozet
    def _write_summary(self) -> None:
        start, count = self.bar.start, self.bar.count
        delta = (count - self.part.sector_count) * self.ss
        tasima = (start - self.part.start_lba) * self.ss
        lines = []
        if delta > 0:
            lines.append(tr("Bolum <b>{} buyuyecek</b>", human_size(delta)))
        elif delta < 0:
            lines.append(tr("Bolum <b>{} kuculecek</b>", human_size(-delta)))
        if tasima:
            kopya = min(count, self.part.sector_count) * self.ss
            if tasima > 0:
                lines.append(
                    tr("Bolum <b>{} ileri tasinacak</b> ({} veri kopyalanir — "
                       "uzun surebilir)", human_size(tasima), human_size(kopya)))
            else:
                lines.append(
                    tr("Bolum <b>{} geri tasinacak</b> ({} veri kopyalanir — "
                       "uzun surebilir)", human_size(-tasima), human_size(kopya)))
        if self.used_bytes >= 0 and count * self.ss < self.used_bytes:
            # Pencere en az boyutun altina inmez; buraya yalnizca en az <
            # dolu olan dosya sistemlerinde (ext) gelinir: dolu alanin bir
            # kismi her grubun kendi yonetim alanidir ve kuculunce azalir.
            lines.append(tr("Yeni boyut simdiki doluluktan kucuk; dosya "
                            "sisteminin yonetim alani da kuculecegi icin "
                            "veri sigar"))
        if not lines:
            lines.append(tr("Degisiklik yok"))
        self.ozet.setText(" · ".join(lines))
        self.basla.setEnabled(
            (start, count) != (self.part.start_lba, self.part.sector_count))

    # ------------------------------------------------------------------ sonuc
    def values(self) -> dict:
        return {"start_lba": self.bar.start, "sector_count": self.bar.count}
