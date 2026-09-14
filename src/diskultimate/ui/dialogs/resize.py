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

from ...core.ptable import Partition, human_size
from ...core.resize import FsResizeInfo, ResizeError, ResizeWindow
from ..widgets.resize_bar import ResizeBar

MIB = 1024 * 1024


class ResizePartitionDialog(QDialog):
    """Bolumu surukleyerek veya sayi girerek yeniden boyutlandirir."""

    def __init__(self, part: Partition, window: ResizeWindow,
                 fs_info: FsResizeInfo, align_sectors: int = 2048,
                 used_bytes: int = -1, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Bolumu Boyutlandir")
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
        self._seritten(part.start_lba, part.sector_count)

    # ----------------------------------------------------------------- kurulum
    def _build(self) -> None:
        duzen = QVBoxLayout(self)
        duzen.setSpacing(10)
        # pencere kucultuldugunde kutular ezilmesin
        duzen.setSizeConstraint(QLayout.SetMinimumSize)

        baslik = QLabel(
            f"<b>{self.part.display_name}</b> &nbsp; "
            f"{self.part.type_name} &nbsp; "
            f"{self.fs.fs_type or 'bicimlendirilmemis'} &nbsp; "
            f"{human_size(self.part.size)}"
            f" &nbsp;—&nbsp; kapsayici alan "
            f"<b>{human_size(self.win.size)}</b>")
        duzen.addWidget(baslik)

        self.bar = ResizeBar(self)
        self.bar.setup(self.win.start_lba, self.win.sector_count,
                       self.part.start_lba, self.part.sector_count,
                       self.ss, self.align, self.min_count, self.max_count,
                       fs_type=self.fs.fs_type, label=self.part.display_name,
                       used_bytes=self.used_bytes, can_move=self.fs.movable)
        self.bar.changed.connect(self._seritten)
        duzen.addWidget(self.bar)

        ipucu = QLabel("Serit uzerindeki tutamaklari fareyle saga/sola surukleyin. "
                       "Ok tuslari ince ayar yapar (Shift ile hizli).")
        ipucu.setWordWrap(True)
        duzen.addWidget(ipucu)

        grup = QGroupBox("(Gecerli Veri Ayrimi)")
        govde = QHBoxLayout(grup)
        sol = QFormLayout()
        sag = QFormLayout()
        govde.addLayout(sol, 1)
        govde.addLayout(sag, 1)

        self.kapasite = self._mb_kutusu()
        self.kapasite.valueChanged.connect(self._kapasiteden)
        sol.addRow("Yeni Kapasite:", self.kapasite)

        self.on_bosluk = self._mb_kutusu()
        self.on_bosluk.valueChanged.connect(self._on_bosluktan)
        sol.addRow("Onundeki Bosluk:", self.on_bosluk)

        self.arka_bosluk = self._mb_kutusu()
        self.arka_bosluk.valueChanged.connect(self._arka_bosluktan)
        sol.addRow("Arkasindaki Bosluk:", self.arka_bosluk)

        self.bas_kesim = self._kesim_kutusu()
        self.bas_kesim.setRange(0, 2 ** 31 - 1)
        self.bas_kesim.setGroupSeparatorShown(True)
        self.bas_kesim.valueChanged.connect(self._kesimden)
        sag.addRow("Baslangic Kesimi:", self.bas_kesim)

        self.son_kesim = self._kesim_kutusu()
        self.son_kesim.setRange(0, 2 ** 31 - 1)
        self.son_kesim.setGroupSeparatorShown(True)
        self.son_kesim.valueChanged.connect(self._kesimden)
        sag.addRow("Bitis Kesimi:", self.son_kesim)

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
        self.basla = dugmeler.addButton("Baslat", QDialogButtonBox.AcceptRole)
        dugmeler.addButton("Iptal", QDialogButtonBox.RejectRole)
        sifirla = QPushButton("Eski haline dondur")
        sifirla.clicked.connect(
            lambda: self._seritten(self.part.start_lba, self.part.sector_count,
                                   serit_de=True))
        dugmeler.addButton(sifirla, QDialogButtonBox.ResetRole)
        dugmeler.accepted.connect(self.accept)
        dugmeler.rejected.connect(self.reject)
        duzen.addWidget(dugmeler)

        self.sinir.setText(self._sinir_metni())
        # Dikey yonde kucultmeye izin verilmez: QFormLayout satirlarinin
        # "en kucuk" yuksekligi tercih edilenden dusuktur ve kutular ust uste
        # biner. Genislik serbest kalir (serit genisledikce daha hassas olur).
        self.setMinimumHeight(self.layout().sizeHint().height())

    def _mb_kutusu(self) -> QDoubleSpinBox:
        kutu = QDoubleSpinBox()
        kutu.setDecimals(2)
        kutu.setRange(0.0, 1024.0 * 1024.0 * 16)
        kutu.setSuffix(" MB")
        kutu.setFixedWidth(150)
        kutu.setKeyboardTracking(False)
        # pencere en kucuk boyuta inince kutu metni kirpilmasin
        kutu.setMinimumHeight(kutu.sizeHint().height())
        return kutu

    def _kesim_kutusu(self) -> QSpinBox:
        kutu = QSpinBox()
        kutu.setKeyboardTracking(False)
        kutu.setMinimumHeight(kutu.sizeHint().height())
        return kutu

    def _sinir_metni(self) -> str:
        parcalar = [f"<b>Sinirlar:</b> en az {human_size(self.min_count * self.ss)}",
                    f"en cok {human_size(self.max_count * self.ss)}"]
        if self.fs.note:
            parcalar.append(self.fs.note)
        if not self.fs.movable:
            parcalar.append("baslangic degistirilemez")
        return " · ".join(parcalar)

    # ------------------------------------------------------------- baglantilar
    def _seritten(self, start: int, count: int, serit_de: bool = False) -> None:
        if self._sessiz:
            return
        self._sessiz = True
        try:
            if serit_de:
                self.bar.set_values(start, count)
            self.kapasite.setValue(count * self.ss / MIB)
            self.on_bosluk.setValue((start - self.win.start_lba) * self.ss / MIB)
            self.arka_bosluk.setValue(
                (self.win.start_lba + self.win.sector_count - start - count)
                * self.ss / MIB)
            self.bas_kesim.setValue(start)
            self.son_kesim.setValue(start + count - 1)
        finally:
            self._sessiz = False
        self._ozet_yaz()

    def _kapasiteden(self, mb: float) -> None:
        if self._sessiz:
            return
        self._degistir(self.bar.start, self._sektor(mb))

    def _on_bosluktan(self, mb: float) -> None:
        if self._sessiz:
            return
        self._degistir(self.win.start_lba + self._sektor(mb), self.bar.count)

    def _arka_bosluktan(self, mb: float) -> None:
        if self._sessiz:
            return
        son = self.win.start_lba + self.win.sector_count - self._sektor(mb)
        self._degistir(self.bar.start, son - self.bar.start)

    def _kesimden(self) -> None:
        if self._sessiz:
            return
        bas = self.bas_kesim.value()
        son = self.son_kesim.value()
        self._degistir(bas, son - bas + 1)

    def _sektor(self, mb: float) -> int:
        ham = int(round(mb * MIB / self.ss))
        return (ham // self.align) * self.align

    def _degistir(self, start: int, count: int) -> None:
        """Kutulardan gelen degeri sinirlara kirpip serite yansitir."""
        if not self.fs.movable:
            start = self.part.start_lba
        count = max(self.min_count, min(self.max_count, count))
        start = max(self.win.start_lba, start)
        ust = self.win.start_lba + self.win.sector_count
        if start + count > ust:
            count = ust - start
        self.bar.set_values(start, count)
        self._seritten(start, count)

    # ------------------------------------------------------------------- ozet
    def _ozet_yaz(self) -> None:
        start, count = self.bar.start, self.bar.count
        fark = (count - self.part.sector_count) * self.ss
        tasima = (start - self.part.start_lba) * self.ss
        satirlar = []
        if fark > 0:
            satirlar.append(f"Bolum <b>{human_size(fark)} buyuyecek</b>")
        elif fark < 0:
            satirlar.append(f"Bolum <b>{human_size(-fark)} kuculecek</b>")
        if tasima:
            yon = "ileri" if tasima > 0 else "geri"
            kopya = min(count, self.part.sector_count) * self.ss
            satirlar.append(
                f"Bolum <b>{human_size(abs(tasima))} {yon} tasinacak</b> "
                f"({human_size(kopya)} veri kopyalanir — uzun surebilir)")
        if self.used_bytes >= 0 and count * self.ss < self.used_bytes:
            satirlar.append("<b>Dikkat:</b> yeni boyut kullanilan alandan kucuk")
        if not satirlar:
            satirlar.append("Degisiklik yok")
        self.ozet.setText(" · ".join(satirlar))
        self.basla.setEnabled(
            (start, count) != (self.part.start_lba, self.part.sector_count))

    # ------------------------------------------------------------------ sonuc
    def values(self) -> dict:
        return {"start_lba": self.bar.start, "sector_count": self.bar.count}
