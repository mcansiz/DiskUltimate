"""Fiziksel disk listesini ve bolum ozetlerini arka planda toplayan is parcacigi.

Neden: disk listeleme isletim sisteminin aygit arayuzlerine dokunur ve suresi
**ongorulemez**. Windows'ta bos bir kart okuyucu yuvasi veya yanit vermeyen bir
USB aygit yuzunden tek bir `CreateFileW` cagrisi onlarca saniye surebilir. Bu
tarama arayuz is parcaciginda calistigi surece uygulama o sure boyunca donar
(kullanicinin bildirdigi ~30 sn donma boyle olusmustu).

Guvenlik: listeleme **salt okunurdur** — `list_disks()` hicbir sektor okumaz ve
hicbir yazma yapmaz (bkz. `core/physical.py` guvenlik katmani 1).

Bolum ozeti (`survey`) ise bolum tablosunu okur; bu da salt okunurdur ama
**sektor okur**. Bu yuzden her yoklama turunda degil, yalnizca disk listesi
gercekten degistiginde yapilir (ADR 0026): aygita bosu bosuna dokunmak, ADR
0021'de ogrenildigi gibi, surucu yigininda sikisma uretir.
"""
from __future__ import annotations

from typing import Dict

from PyQt5.QtCore import QThread, pyqtSignal

from ..core import diagnostics
from ..core.session import DiskSession


def disk_signature(info) -> tuple:
    """Bir diskin "degisti mi" olcutu: yol, boyut, model, bagli bolumler."""
    return (info.path, info.size, info.model, tuple(info.mounted))


class DiskScanner(QThread):
    """`list_physical_disks()` ve gerekiyorsa bolum yoklamasini arka planda yapar."""

    # (diskler, {yol: DiskSurvey})
    ready = pyqtSignal(object, object)
    failed = pyqtSignal(str)

    def __init__(self, known: Dict[str, tuple] = None, parent=None):
        """`known`: {yol: imza} — imzasi degismeyen disk yeniden yoklanmaz."""
        super().__init__(parent)
        self.setObjectName("disk-scan")
        self._known = dict(known or {})

    def run(self) -> None:
        try:
            with diagnostics.span("disk_scan.run"):
                disks = DiskSession.list_physical_disks()
                surveys = {}
                for info in disks:
                    if not info.info_complete:
                        continue        # yetki yok: acmaya calismak anlamsiz
                    if self._known.get(info.path) == disk_signature(info):
                        continue        # degismemis: onceki ozet gecerli
                    surveys[info.path] = DiskSession.survey_disk(info)
        except Exception as exc:                    # tarama hatasi olumcul degil
            diagnostics.error("disk taramasi basarisiz", exc)
            self.failed.emit(str(exc))
            return
        self.ready.emit(disks, surveys)
