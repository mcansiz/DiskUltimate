"""Uzun suren islemleri arayuzu dondurmadan calistiran yardimci."""
from __future__ import annotations

import time
from typing import Callable, Optional

from PyQt5.QtCore import QThread, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import QApplication, QDialog, QLabel, QProgressBar, QVBoxLayout
from ...i18n import tr


class _Worker(QThread):
    progress = pyqtSignal(str, float)
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, func: Callable):
        super().__init__()
        self.func = func

    def run(self):
        try:
            result = self.func(lambda msg, pct: self.progress.emit(msg, float(pct)))
            self.finished_ok.emit(result)
        except Exception as exc:  # kullanici hatayi gormeli
            self.failed.emit(str(exc))


def clock_text(seconds: float) -> str:
    """Sure gosterimi: 0:07, 12:40, 1:05:09."""
    seconds = max(0, int(round(seconds)))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


class Estimator:
    """Gecen ve kalan sure (yuzde ilerlemeden).

    Kalan sure, olcum baslangicindan bu yana ortalama hizla hesaplanir;
    anlik hiz yerine ortalama kullanmak sayinin her bildirimde ziplamasini
    onler. Yuzde geri giderse (cok asamali is) olcum yeniden baslar. Ilk
    2 saniye ve %1'den az ilerlemede tahmin yapilmaz — erken tahmin
    yaniltir.
    """

    MIN_SECONDS = 2.0
    MIN_PERCENT = 1.0

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.start = clock()
        self.base_time: Optional[float] = None
        self.base_pct = 0.0
        self.pct: Optional[float] = None       # None = belirsiz

    def update(self, pct: float) -> None:
        now = self.clock()
        if pct < 0:
            self.pct = None
            self.base_time = None
            return
        if self.base_time is None or (self.pct is not None and pct < self.pct - 1):
            self.base_time, self.base_pct = now, pct
        self.pct = pct

    def remaining(self) -> Optional[float]:
        if self.pct is None or self.base_time is None or self.pct >= 100:
            return None
        spent = self.clock() - self.base_time
        gained = self.pct - self.base_pct
        if spent < self.MIN_SECONDS or gained < self.MIN_PERCENT:
            return None
        return spent * (100 - self.pct) / gained

    def text(self) -> str:
        elapsed = clock_text(self.clock() - self.start)
        if self.pct is None:
            return tr("Gecen: {}", elapsed)
        left = self.remaining()
        if left is None:
            return tr("Gecen: {} · kalan sure hesaplaniyor...", elapsed)
        return tr("Gecen: {} · Kalan: yaklasik {}", elapsed, clock_text(left))


class TaskDialog(QDialog):
    """Ilerleme cubugu ile islem penceresi.

    func(progress_cb) imzali bir cagrilabilir alir; progress_cb(mesaj, yuzde).
    """

    def __init__(self, parent, title: str, func: Callable):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setFixedSize(420, 128)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowCloseButtonHint)
        self.result_value = None
        self.error: Optional[str] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        self.label = QLabel(tr("Hazirlaniyor..."))
        layout.addWidget(self.label)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        layout.addWidget(self.bar)
        self.detail = QLabel("")
        self.detail.setEnabled(False)   # paletten soluk ton
        layout.addWidget(self.detail)

        self.estimator = Estimator()
        self.detail.setText(self.estimator.text())
        # Gecen sure ilerleme gelmese de yurusun (belirsiz isler dahil)
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(
            lambda: self.detail.setText(self.estimator.text()))

        self.worker = _Worker(func)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_done)
        self.worker.failed.connect(self._on_fail)

    def _on_progress(self, msg: str, pct: float) -> None:
        """Ilerleme bildirimi. `pct < 0` = **belirsiz** (hareketli cubuk).

        Bazi islemler tek bir buyuk adimdir (ornegin bir dosyanin bolume
        yazilmasi): yuzde hesaplanamaz. Cubugu 0'da birakmak kullaniciya
        "takildi" izlenimi verir; belirsiz kipte Qt cubugu hareket ettirir.
        """
        self.label.setText(msg)
        self.estimator.update(pct)
        self.detail.setText(self.estimator.text())
        if pct < 0:
            if self.bar.maximum() != 0:
                self.bar.setRange(0, 0)
            return
        if self.bar.maximum() == 0:
            self.bar.setRange(0, 100)
        self.bar.setValue(int(max(0, min(100, pct))))

    def _on_done(self, value) -> None:
        self.result_value = value
        self.accept()

    def _on_fail(self, msg: str) -> None:
        self.error = msg
        self.reject()

    def exec_(self) -> int:
        QTimer.singleShot(0, self.update)
        self._tick.start()
        self.worker.start()
        try:
            result = super().exec_()
        finally:
            # Pencere kapandiktan sonra sayac durur: eskiden hic durmuyordu,
            # her gorev penceresi saniyede bir calisan bir sayac birakiyordu;
            # macOS'ta silinmis etikete yazip cokuyordu (ui_smoke, SIGSEGV,
            # 2026-10-04).
            self._tick.stop()
        self.worker.wait()
        return result


def run_task(parent, title: str, func: Callable):
    """Islemi calistirir; (basarili, sonuc_veya_hata) dondurur."""
    dlg = TaskDialog(parent, title, func)
    try:
        ok = dlg.exec_() == QDialog.Accepted
        return ok, (dlg.result_value if ok else dlg.error)
    finally:
        dlg.deleteLater()           # ebeveyne bagli pencere birikmesin
