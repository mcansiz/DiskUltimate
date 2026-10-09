"""Uzun islemlerde guc secenekleri: uyku engeli + "islem bitince" eylemi.

Klonlama, geri yukleme, guvenli silme gercek donanimda saatler surer
(DiskGenius "When Finished" / "Prevent System From Sleeping"). Bilesen
Uygula, Yedekleme ve uzun gorev pencerelerinde ortaktir.

Kurallar:
  * Uyku engeli varsayilan **acik** ve hatirlanir; is surerken de
    acilip kapatilabilir.
  * "Islem bitince" her pencerede **kapali** baslar ve hatirlanmaz: bir
    onceki gecenin "kapat" secimi bugunku kisa isin sonunda bilgisayari
    kapatmamali.
  * Eylem yalnizca islem **basariyla** bittiginde uygulanir. Hata ya da
    durdurmada bilgisayar acik kalir: kullanici hatayi gormelidir.
  * Eylemden once geri sayim penceresi gosterilir; iptal edilebilir.

Isletim sistemi cagrilari `core/platform.py` icindedir.
"""
from __future__ import annotations

from typing import Callable, Optional

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout,
                             QLabel, QMessageBox, QPushButton, QVBoxLayout,
                             QWidget)

from ...core import settings
from ...core.platform import (POWER_ACTIONS, SleepInhibitor, power_action,
                              power_action_supported)
from ...i18n import mark, tr

PREVENT_SLEEP_KEY = "power_prevent_sleep"
COUNTDOWN_SECONDS = 60

ACTION_LABELS = {
    "shutdown": mark("Bilgisayari kapat"),
    "reboot": mark("Yeniden baslat"),
    "suspend": mark("Uyku"),
    "hibernate": mark("Hazirda beklet"),
}

COUNTDOWN_TEXTS = {
    "shutdown": mark("Bilgisayar {} saniye icinde kapatilacak."),
    "reboot": mark("Bilgisayar {} saniye icinde yeniden baslatilacak."),
    "suspend": mark("Bilgisayar {} saniye icinde uyku moduna gececek."),
    "hibernate": mark("Bilgisayar {} saniye icinde hazirda bekletilecek."),
}


class PowerOptions(QWidget):
    """"Islem bitince: [eylem]" ve "Uyku modunu engelle" satirlari.

    Kullanim: is baslarken `begin()`, bitince `action = end(basarili)`;
    `action` doluysa pencere kapandiktan sonra `run_power_action`.
    """

    def __init__(self, parent=None, reason: str = "", after: bool = True):
        """`after=False`: yalnizca uyku engeli. Sonucu bellekte duran
        islerde (taramalar) "bitince kapat" sonucu yok ederdi."""
        super().__init__(parent)
        self._inhibitor = SleepInhibitor(reason or "DiskUltimate")
        self._running = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.after_check = QCheckBox(tr("Islem bitince:"))
        self.action_combo = QComboBox()
        model = self.action_combo.model()
        for kind in POWER_ACTIONS:
            self.action_combo.addItem(tr(ACTION_LABELS[kind]), kind)
            ok, why = power_action_supported(kind)
            if not ok:
                item = model.item(self.action_combo.count() - 1)
                item.setEnabled(False)
                item.setToolTip(why)
        self.action_combo.setEnabled(False)
        self.after_check.toggled.connect(self.action_combo.setEnabled)
        row.addWidget(self.after_check)
        row.addWidget(self.action_combo)
        row.addStretch(1)
        if after:
            layout.addLayout(row)
        else:
            for widget in (self.after_check, self.action_combo):
                widget.setParent(self)
                widget.setVisible(False)

        self.sleep_check = QCheckBox(tr("Islem surerken uyku modunu engelle"))
        self.sleep_check.setChecked(bool(settings.get(PREVENT_SLEEP_KEY, True)))
        self.sleep_check.toggled.connect(self._on_sleep_toggled)
        layout.addWidget(self.sleep_check)

        # Engel konamazsa nedeni burada yazar ("bilinmiyor" sessiz kalmaz).
        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setEnabled(False)               # paletten soluk ton
        self.note.setVisible(False)
        layout.addWidget(self.note)

    # -- durum ----------------------------------------------------------------
    @property
    def selected_action(self) -> Optional[str]:
        if not self.after_check.isChecked():
            return None
        kind = self.action_combo.currentData()
        return kind if power_action_supported(kind)[0] else None

    @property
    def inhibiting(self) -> bool:
        return self._inhibitor.active

    # -- yasam dongusu ----------------------------------------------------------
    def begin(self) -> None:
        """Is basladi: secildiyse uyku engellenir (arayuz parcaciginda)."""
        self._running = True
        if self.sleep_check.isChecked():
            self._acquire()

    def end(self, success: bool) -> Optional[str]:
        """Is bitti: engel kalkar; basariliysa secilen eylem doner."""
        self._running = False
        self._inhibitor.release()
        return self.selected_action if success else None

    def _on_sleep_toggled(self, checked: bool) -> None:
        settings.set_value(PREVENT_SLEEP_KEY, bool(checked))
        if not self._running:
            return
        if checked:
            self._acquire()
        else:
            self._inhibitor.release()
            self.note.setVisible(False)

    def _acquire(self) -> None:
        ok, why = self._inhibitor.acquire()
        self.note.setText("" if ok else tr("Uyku engellenemedi: {}", why))
        self.note.setVisible(not ok)

    def hideEvent(self, event):
        # Pencere beklenmedik yoldan kapanirsa engel asili kalmasin.
        if not self._running:
            self._inhibitor.release()
        super().hideEvent(event)


class CountdownDialog(QDialog):
    """"Bilgisayar 60 saniye icinde kapatilacak" — Simdi / Iptal."""

    def __init__(self, parent, kind: str, seconds: int = COUNTDOWN_SECONDS):
        super().__init__(parent)
        self.kind = kind
        self.remaining = seconds
        self.setWindowTitle(tr(ACTION_LABELS[kind]))
        self.setModal(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        title = QLabel(tr("Islem tamamlandi."))
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)
        self.label = QLabel("")
        layout.addWidget(self.label)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.btn_now = QPushButton(tr("Simdi"))
        self.btn_now.clicked.connect(self.accept)
        self.btn_cancel = QPushButton(tr("Iptal"))
        self.btn_cancel.setDefault(True)          # Enter yanlislikla kapatmasin
        self.btn_cancel.clicked.connect(self.reject)
        buttons.addWidget(self.btn_now)
        buttons.addWidget(self.btn_cancel)
        layout.addLayout(buttons)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._update_text()

    def _update_text(self) -> None:
        self.label.setText(tr(COUNTDOWN_TEXTS[self.kind], self.remaining))

    def _tick(self) -> None:
        self.remaining -= 1
        if self.remaining <= 0:
            self._timer.stop()
            self.accept()
            return
        self._update_text()

    def exec_(self) -> int:
        self._timer.start()
        try:
            return super().exec_()
        finally:
            self._timer.stop()


def run_power_action(parent, kind: Optional[str],
                     seconds: int = COUNTDOWN_SECONDS,
                     prepare: Optional[Callable[[], None]] = None) -> bool:
    """Geri sayimdan sonra eylemi uygular; iptal/hata: False.

    `prepare` eylemden hemen once cagrilir: acik goruntunun tamponlari diske
    yazilir. Kapatmada isletim sistemi sureci sonlandirir; Python'un
    yazilmamis tamponu o anda kaybolurdu.
    """
    if not kind:
        return False
    dlg = CountdownDialog(parent, kind, seconds)
    try:
        if dlg.exec_() != QDialog.Accepted:
            return False
    finally:
        dlg.deleteLater()
    if prepare is not None:
        try:
            prepare()
        except Exception as exc:                      # noqa: BLE001
            QMessageBox.warning(parent, tr(ACTION_LABELS[kind]),
                                tr("Islem uygulanamadi: {}", exc))
            return False
    ok, why = power_action(kind)
    if not ok:
        QMessageBox.warning(parent, tr(ACTION_LABELS[kind]),
                            tr("Islem uygulanamadi: {}", why))
    return ok
