"""NTFS denetle ve onar penceresi (`core/ntfsfix.py`, ADR 0077).

Pencere yalnizca **bulgulari gosterir ve secenek toplar**; diske hicbir sey
yazmaz. Onarim bekleyen islem kuyruguna girer ve "Uygula" ile calisir
(ADR 0025). Denetim (aygit okumasi) pencere acilmadan once is
parcaciginda yapilmistir; burada yalnizca sonucu cizilir.
"""
from __future__ import annotations

from PyQt5.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QLabel,
                             QRadioButton, QVBoxLayout)

from ...core import ntfsfix
from ...i18n import tr


class NtfsFixDialog(QDialog):
    """Bulgular + onarim secenekleri."""

    def __init__(self, parent, target_label: str, health: "ntfsfix.NtfsHealth"):
        super().__init__(parent)
        self.setWindowTitle(tr("NTFS'i Denetle ve Onar"))
        self.setMinimumWidth(600)
        self.health = health
        self._build(target_label)

    def _build(self, target: str) -> None:
        h = self.health
        duzen = QVBoxLayout(self)
        duzen.setSpacing(6)
        duzen.addWidget(QLabel(tr("<b>Hedef:</b> {}", target)))

        # Grup kutusu kullanilmaz: icindeki satir kaydirmali etiketler Qt'de
        # tek satir yuksekliginde kaliyor ve metin kesiliyordu (olculdu).
        duzen.addWidget(self._heading(tr("Denetim sonucu")))
        bulgu_duzen = duzen
        sorunlar = h.problems() or [tr("Sorun bulunmadi: birim temiz kapatilmis.")]
        self.findings = []
        for sorun in sorunlar:
            satir = QLabel(("• " if h.problems() else "") + sorun)
            satir.setWordWrap(True)
            bulgu_duzen.addWidget(satir)
            self.findings.append(satir)
        if h.blocks_linux_mount:
            bulgu_duzen.addWidget(self._hint(tr(
                "Linux bu durumdaki bir NTFS birimini baglamayi reddeder "
                "(ntfs3: kirli birim, ntfs-3g: Windows onbelleginde ustveri).")))
        if h.version:
            surum = QLabel(tr("NTFS surumu: {}", h.version))
            surum.setEnabled(False)
            bulgu_duzen.addWidget(surum)

        duzen.addWidget(self._heading(tr("Onarim")))
        secenek_duzen = duzen
        aciklama = QLabel(tr(
            "Onyukleme sektoru ve $MFTMirr denetlenip gerekirse duzeltilir, "
            "islem gunlugu ($LogFile) bosaltilir. Bu, Linux'taki "
            "'ntfsfix' ile ayni istir."))
        aciklama.setWordWrap(True)
        secenek_duzen.addWidget(aciklama)
        self.radio_clear = QRadioButton(tr("Kirli bayragini temizle (ntfsfix -d)"))
        self.radio_clear.setChecked(True)
        secenek_duzen.addWidget(self.radio_clear)
        secenek_duzen.addWidget(self._hint(tr(
            "Birim Linux'ta hemen baglanabilir.")))
        self.radio_chkdsk = QRadioButton(tr("Windows'ta chkdsk iste"))
        secenek_duzen.addWidget(self.radio_chkdsk)
        secenek_duzen.addWidget(self._hint(tr(
            "Bayrak acik kalir; Windows bir sonraki acilista birimi denetler. "
            "Linux o zamana kadar baglamaz.")))
        self.hiber_check = QCheckBox(tr("Hazirda bekletme dosyasini gecersiz kil"))
        self.hiber_hint = self._hint(tr(
            "Windows kaydedilmis oturuma donmez, soguk acilir; kaydedilmemis "
            "isler kaybolur."))
        for w in (self.hiber_check, self.hiber_hint):
            w.setVisible(h.hibernated)
            secenek_duzen.addWidget(w)

        warning = QLabel(tr(
            "<span style='color:#b23c17'><b>Uyari:</b> Windows'un diske henuz "
            "islemedigi son degisiklikler kaybolabilir. Mumkunse Windows'u "
            "acip <b>Yeniden baslat</b> ile kapatmak ve Hizli baslatmayi "
            "kapatmak daha guvenlidir. Onarimdan sonra Windows'ta chkdsk "
            "onerilir.</span>"))
        warning.setWordWrap(True)
        duzen.addWidget(warning)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok_button = self.buttons.button(QDialogButtonBox.Ok)
        ok_button.setText(tr("Kuyruga ekle"))
        ok_button.setProperty("primary", True)
        self.buttons.button(QDialogButtonBox.Cancel).setText(tr("Iptal"))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        duzen.addWidget(self.buttons)

        self.hiber_check.toggled.connect(self._refresh)
        self._refresh()

    def showEvent(self, event) -> None:
        """Yuksekligi gercek genislige gore yeniden hesaplar.

        Satir kaydirmali etiketlerin yuksekligi genislige baglidir; Qt pencereyi
        ilk boyutlarken bunu hesaba katmiyor ve metin tek satirda kesiliyordu.
        """
        super().showEvent(event)
        genislik = max(self.width(), self.minimumWidth())
        yukseklik = self.layout().totalHeightForWidth(genislik)
        if yukseklik > self.height():
            self.resize(genislik, yukseklik)

    @staticmethod
    def _heading(text: str) -> QLabel:
        label = QLabel(text)
        font = label.font()
        font.setBold(True)
        label.setFont(font)
        return label

    @staticmethod
    def _hint(text: str) -> QLabel:
        """Secenegin altindaki soluk aciklama satiri."""
        label = QLabel(text)
        label.setWordWrap(True)
        label.setEnabled(False)              # paletten soluk ton
        label.setContentsMargins(22, 0, 0, 4)
        return label

    def _refresh(self) -> None:
        """Hazirda bekletme varken onay kutusu secilmeden onarim baslamaz."""
        h = self.health
        ok_button = self.buttons.button(QDialogButtonBox.Ok)
        reason = ""
        if not h.repairable:
            reason = h.error or tr("NTFS onyukleme sektoru bulunamadi")
        elif h.hibernated and not self.hiber_check.isChecked():
            reason = tr("Windows hazirda bekletmede: once hazirda bekletme "
                       "dosyasini gecersiz kilmayi secin")
        ok_button.setEnabled(not reason)
        ok_button.setToolTip(reason)

    def values(self) -> dict:
        clear = self.radio_clear.isChecked()
        return {"clear_dirty": clear, "schedule_chkdsk": not clear,
                "remove_hibernation": self.health.hibernated
                and self.hiber_check.isChecked()}
