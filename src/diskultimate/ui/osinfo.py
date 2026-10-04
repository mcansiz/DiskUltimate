"""Bolum basina kurulu isletim sistemi — arka plan servisi (ADR 0089).

Disk satirindaki amblem (`DiskInfo.os_hint`) bolum turlerinden bir tahmindir;
bir diskte Windows, Linux ve bir veri bolumu yan yana olabilir. Burada her
bolumun **dosya sistemi icine bakilir** (`bootloader.partition_os`): Windows
surumu `ntoskrnl.exe`'den, Linux dagitimi `os-release` dosyasindan, macOS
`SystemVersion.plist`'ten, ESP'de onyukleyicisi bulunan sistemler.

Fiziksel diskte bu okumalarin suresi ongorulemez (CLAUDE.md donma kurali):
bir oturumun butun bolumleri **tek bir `QThread`te** sirayla incelenir, bitince
`ready` bir kez yayilir. Okuma oturumun kendi tutamaciyla yapilir (ikinci
tutamac acilmaz, ADR 0021); `BlockDevice` kilidi arayuzun okumalariyla siraya
koyar (`ui/fslimits.py` ile ayni model).

Sonuc (aygit nesnesi, baslangic, boyut, dosya sistemi) anahtariyla onbellege
alinir: bicimlendirme ya da tasima anahtari degistirir, eski ad gosterilmez.
"""
from __future__ import annotations

from typing import Dict, List

from PyQt5.QtCore import QObject, QThread, pyqtSignal

from ..core import diagnostics
from ..core.bootloader import partition_os


def _key(session, part) -> tuple:
    return (session.image, part.start_lba, part.sector_count,
            (part.fs_type or "").lower())


class _Worker(QThread):
    done = pyqtSignal(object, object)        # oturum, {anahtar: sonuc}

    def __init__(self, session, parts):
        super().__init__()
        self.session, self.parts = session, parts

    def run(self):
        results = {}
        for part in self.parts:
            try:
                with diagnostics.span("os_detect", index=part.index):
                    results[_key(self.session, part)] = partition_os(self.session, part)
            except Exception as exc:              # noqa: BLE001
                results[_key(self.session, part)] = {"name": "", "kind": "",
                                                    "reason": str(exc)}
        self.done.emit(self.session, results)


class OsInfoService(QObject):
    """Bolumlerdeki isletim sistemlerini arka planda bulur ve saklar."""

    ready = pyqtSignal(object)                # sonuclari hazir olan oturum

    def __init__(self, parent=None):
        super().__init__(parent)
        self._results: Dict[tuple, dict] = {}
        self._workers: Dict[int, _Worker] = {}   # id(oturum) -> calisan

    def annotate(self, session, partitions, request: bool = True) -> None:
        """Bolum nesnelerine bilinen sonucu yazar; eksikleri arka planda ister.

        Plan onizlemesindeki bolumler de buradan gecer: anahtar konum ve
        dosya sistemidir, numara degil — yeniden numaralanan bolum de adini
        tasir. Henuz diske yazilmamis (planlanan) bolum icin istek yapilmaz.
        """
        missing = []
        for part in partitions:
            result = self._results.get(_key(session, part))
            if result is not None:
                part.os_name = result.get("name", "")
                part.os_kind = result.get("kind", "")
            elif not getattr(part, "plan_state", ""):
                part.os_name = part.os_kind = ""
                missing.append(part)
        if request and missing and session.table is not None:
            self._start(session, missing)

    def pending(self, session=None) -> bool:
        if session is None:
            return bool(self._workers)
        return id(session) in self._workers

    def forget(self, session) -> None:
        """Oturumun sonuclarini unutur (disk yeniden okununca)."""
        for key in [k for k in self._results if k[0] is session.image]:
            del self._results[key]

    def _start(self, session, parts: List) -> None:
        if id(session) in self._workers:
            return
        worker = _Worker(session, list(parts))
        worker.done.connect(self._on_done)
        self._workers[id(session)] = worker
        worker.start()

    def _on_done(self, session, results) -> None:
        worker = self._workers.pop(id(session), None)
        if worker is not None:
            worker.wait()
        self._results.update(results)
        self.ready.emit(session)

    def wait(self, session) -> None:
        """Oturumun suren taramasini bekler (kapatma / yazma oncesi).

        Tarama oturumun tutamaciyla okur; oturum kapanirken ya da Uygula
        yazarken ayni aygittan okumaya devam etmemeli.
        """
        worker = self._workers.get(id(session))
        if worker is not None:
            worker.wait()

    def wait_all(self) -> None:
        """Kapatmadan once suren taramalari bekler (oturum kapanmadan)."""
        for worker in list(self._workers.values()):
            worker.wait()
