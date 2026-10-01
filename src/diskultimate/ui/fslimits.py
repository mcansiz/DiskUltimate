"""Dosya sistemi boyutlandirma sinirlari servisi (ADR 0047 + 0049, 4. asama).

Bolumun "en az / en cok" boyutu dosya sisteminin icinden okunur; NTFS'te bu
butun `$Bitmap`in taranmasidir. Arayuz is parcaciginda yapildiginda ana
ekranin tutamaklari her suruklemede donuyordu (ADR 0047). Bu servis:

* sinirlari **arka planda** hesaplar (her istek icin bir `QThread`),
* sonucu (aygit nesnesi, bolum, baslangic, boyut) anahtariyla **onbellege**
  alir — tutamac yeniden acilinca ya da bolum yer degistirince eski sonuc
  kullanilmaz; anahtar aygit nesnesinin kendisini tuttugu icin kimligi
  baska bir tutamaca verilemez,
* hazir olunca `ready` sinyaliyle haber verir.

Ana ekran, "Bolumu boyutlandir" penceresi ve "Bolum duzeni" penceresi ayni
servisi kullanir. Aygit okumasi `BlockDevice` kilidiyle siraya girer; arayuzun
ayni tutamaktan okumasiyla yarismaz.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from PyQt5.QtCore import QObject, QThread, pyqtSignal

from ..core import diagnostics
from ..core.resize import FsResizeInfo, fs_resize_info_for


class _Worker(QThread):
    done = pyqtSignal(object, object)          # anahtar, FsResizeInfo | hata

    def __init__(self, key, session, index: int):
        super().__init__()
        self.key, self.session, self.index = key, session, index

    def run(self):
        try:
            with diagnostics.span("fs_limits", index=self.index):
                part = self.session.table.get(self.index)
                info = fs_resize_info_for(self.session, part)
            self.done.emit(self.key, info)
        except Exception as exc:                  # noqa: BLE001
            self.done.emit(self.key, exc)


class FsLimitsService(QObject):
    """Arka planda hesaplanan, onbellekli FS sinirlari."""

    ready = pyqtSignal(object)                  # hazir olan anahtar

    def __init__(self, parent=None):
        super().__init__(parent)
        self._results: Dict[tuple, object] = {}
        self._workers: Dict[tuple, _Worker] = {}
        # unutulmus anahtarlar icin suren hesaplar: sonuclari atilir
        self._stale: List[_Worker] = []

    @staticmethod
    def key(session, part) -> tuple:
        return (session.image, part.index, part.start_lba, part.sector_count)

    # -- sorgu ----------------------------------------------------------------
    def get(self, session, part, request: bool = True) -> Optional[FsResizeInfo]:
        """Hazirsa sinirlar; degilse None (ve `request` ise hesap baslar).

        Hesap basarisiz olduysa da None doner: bolum duzenlenemez sayilir.
        """
        key = self.key(session, part)
        result = self._results.get(key)
        if isinstance(result, FsResizeInfo):
            return result
        if result is None and request:
            self._start(key, session, part.index)
        return None

    def error(self, session, part) -> Optional[Exception]:
        result = self._results.get(self.key(session, part))
        return result if isinstance(result, Exception) else None

    def pending(self) -> bool:
        return bool(self._workers)

    def compute_now(self, session, part) -> FsResizeInfo:
        """Onbellekte yoksa **bu is parcaciginda** hesaplar (arka plan
        gorevinin icinden cagrilir; arayuz is parcaciginda cagrilmamali)."""
        key = self.key(session, part)
        result = self._results.get(key)
        if isinstance(result, FsResizeInfo):
            return result
        with diagnostics.span("fs_limits", index=part.index):
            info = fs_resize_info_for(session, part)
        self._results[key] = info
        return info

    # -- yonetim --------------------------------------------------------------
    def _start(self, key, session, index: int) -> None:
        if key in self._workers:
            return
        worker = _Worker(key, session, index)
        worker.done.connect(self._on_done)
        self._workers[key] = worker
        worker.start()

    def _on_done(self, key, result) -> None:
        sender = self.sender()
        if sender in self._stale:
            # Hesap baslarken dosya sistemi farkliydi (forget): sonuc eskidir.
            self._stale.remove(sender)
            sender.wait()
            return
        worker = self._workers.pop(key, None)
        if worker is not None:
            worker.wait()
        self._results[key] = result
        self.ready.emit(key)

    def forget(self, session, index: Optional[int] = None) -> None:
        """Bolumun (ya da oturumun) sinirlarini unutur.

        Anahtar (aygit, no, baslangic, boyut) dosya eklenince/silinince
        DEGISMEZ; unutulmazsa en az boyut dosyalar eklenmeden onceki dolulukla
        kalir ve pencere dolu alanin altina inmeye izin verir (2026-10-01:
        3,27 GB dolu NTFS 3,33 GB'a planlanabildi, gercek en az 3,44 GB).
        """
        def matches(key) -> bool:
            return key[0] is session.image and (index is None or key[1] == index)
        for key in [k for k in self._results if matches(k)]:
            del self._results[key]
        for key in [k for k in self._workers if matches(k)]:
            self._stale.append(self._workers.pop(key))

    def clear(self) -> None:
        """Disk yeniden okundu: dosya sistemi dolulugu degismis olabilir."""
        self._results.clear()

    def wait_all(self, timeout_ms: int = 5000) -> None:
        """Kapanista: suren hesaplar biter, sinyaller kopar."""
        for worker in list(self._workers.values()) + self._stale:
            try:
                worker.done.disconnect()
            except TypeError:
                pass
            worker.wait(timeout_ms)
        self._workers.clear()
        self._stale.clear()

    def errors(self) -> List[Exception]:
        return [r for r in self._results.values() if isinstance(r, Exception)]
