"""Fiziksel disk listesini arka planda toplayan is parcacigi.

Neden: disk listeleme isletim sisteminin aygit arayuzlerine dokunur ve suresi
**ongorulemez**. Windows'ta bos bir kart okuyucu yuvasi veya yanit vermeyen bir
USB aygit yuzunden tek bir `CreateFileW` cagrisi onlarca saniye surebilir. Bu
tarama arayuz is parcaciginda calistigi surece uygulama o sure boyunca donar
(kullanicinin bildirdigi ~30 sn donma boyle olusmustu).

Guvenlik: tarama **salt okunurdur** — `list_disks()` hicbir sektor okumaz ve
hicbir yazma yapmaz (bkz. `core/physical.py` guvenlik katmani 1). Bu yuzden
arka planda calistirilmasi yeni bir risk getirmez.
"""
from __future__ import annotations

from PyQt5.QtCore import QThread, pyqtSignal

from ..core import diagnostics
from ..core.session import DiskSession


class DiskScanner(QThread):
    """`list_physical_disks()` cagrisini arka planda yapar."""

    ready = pyqtSignal(object)      # List[DiskInfo]
    failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("disk-scan")

    def run(self) -> None:
        try:
            with diagnostics.span("disk_scan.run"):
                disks = DiskSession.list_physical_disks()
        except Exception as exc:                    # tarama hatasi olumcul degil
            diagnostics.error("disk taramasi basarisiz", exc)
            self.failed.emit(str(exc))
            return
        self.ready.emit(disks)
