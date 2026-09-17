"""Uzun suren islemleri arayuzu dondurmadan calistiran yardimci."""
from __future__ import annotations

from typing import Callable, Optional

from PyQt5.QtCore import QThread, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import QApplication, QDialog, QLabel, QProgressBar, QVBoxLayout
from ...i18n import tr


class _Worker(QThread):
    progress = pyqtSignal(str, int)
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, func: Callable):
        super().__init__()
        self.func = func

    def run(self):
        try:
            result = self.func(lambda msg, pct: self.progress.emit(msg, pct))
            self.finished_ok.emit(result)
        except Exception as exc:  # kullanici hatayi gormeli
            self.failed.emit(str(exc))


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

        self.worker = _Worker(func)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_done)
        self.worker.failed.connect(self._on_fail)

    def _on_progress(self, msg: str, pct: int) -> None:
        """Ilerleme bildirimi. `pct < 0` = **belirsiz** (hareketli cubuk).

        Bazi islemler tek bir buyuk adimdir (ornegin bir dosyanin bolume
        yazilmasi): yuzde hesaplanamaz. Cubugu 0'da birakmak kullaniciya
        "takildi" izlenimi verir; belirsiz kipte Qt cubugu hareket ettirir.
        """
        self.label.setText(msg)
        if pct < 0:
            if self.bar.maximum() != 0:
                self.bar.setRange(0, 0)
            return
        if self.bar.maximum() == 0:
            self.bar.setRange(0, 100)
        self.bar.setValue(max(0, min(100, pct)))

    def _on_done(self, value) -> None:
        self.result_value = value
        self.accept()

    def _on_fail(self, msg: str) -> None:
        self.error = msg
        self.reject()

    def exec_(self) -> int:
        QTimer.singleShot(0, self.update)
        self.worker.start()
        result = super().exec_()
        self.worker.wait()
        return result


def run_task(parent, title: str, func: Callable):
    """Islemi calistirir; (basarili, sonuc_veya_hata) dondurur."""
    dlg = TaskDialog(parent, title, func)
    ok = dlg.exec_() == QDialog.Accepted
    return ok, (dlg.result_value if ok else dlg.error)
