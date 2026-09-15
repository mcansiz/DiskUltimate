"""Ham disk goruntu dosyasi (.img) erisim katmani.

Bu modul saf Python'dur ve GUI'den tamamen bagimsizdir.
Tum adresleme LBA (sektor) cinsindendir; bayta cevirme yalnizca bu katmanda yapilir.
"""
from __future__ import annotations

import os
from typing import Optional

from .platform import make_sparse, truncate_sparse

DEFAULT_SECTOR_SIZE = 512


class DiskImageError(Exception):
    """Disk goruntusu ile ilgili hatalar."""


class BlockDevice:
    """Blok aygiti arayuzu: DiskImage ve PartitionView bunu uygular."""

    sector_size: int = DEFAULT_SECTOR_SIZE

    @property
    def sector_count(self) -> int:  # pragma: no cover - arayuz
        raise NotImplementedError

    @property
    def size(self) -> int:
        return self.sector_count * self.sector_size

    def read(self, offset: int, length: int) -> bytes:  # pragma: no cover - arayuz
        raise NotImplementedError

    def write(self, offset: int, data: bytes) -> None:  # pragma: no cover - arayuz
        raise NotImplementedError

    # -- sektor yardimcilari -------------------------------------------------
    def read_sectors(self, lba: int, count: int = 1) -> bytes:
        return self.read(lba * self.sector_size, count * self.sector_size)

    def write_sectors(self, lba: int, data: bytes) -> None:
        if len(data) % self.sector_size:
            raise DiskImageError("Sektor sinirina hizalanmamis yazma islemi")
        self.write(lba * self.sector_size, data)

    def zero_sectors(self, lba: int, count: int) -> None:
        """Belirtilen sektorleri sifirlar (parcali yazar, bellegi sismez)."""
        chunk = 1024  # sektor
        remaining = count
        cur = lba
        buf = b"\x00" * (chunk * self.sector_size)
        while remaining > 0:
            n = min(chunk, remaining)
            self.write_sectors(cur, buf[: n * self.sector_size])
            cur += n
            remaining -= n


class DiskImage(BlockDevice):
    """`.img` ham disk goruntu dosyasi."""

    def __init__(self, path: str, readonly: bool = False,
                 sector_size: int = DEFAULT_SECTOR_SIZE):
        self.path = os.path.abspath(path)
        self.readonly = readonly
        self.sector_size = sector_size
        if not os.path.isfile(self.path):
            raise DiskImageError(f"Dosya bulunamadi: {self.path}")
        mode = "rb" if readonly else "r+b"
        self.readonly_reason = "Salt okunur acilmasi istendi" if readonly else ""
        try:
            self._fh = open(self.path, mode)
        except PermissionError as exc:
            # Windows'ta "baska surec kullaniyor" (WinError 32/33) hatasi da
            # PermissionError olarak gelir; bu yuzden ayrim BU blokta yapilir.
            # Dosya yine de salt okunur acilir, ama neden acikca saptanir.
            self._fh = open(self.path, "rb")
            self.readonly = True
            self.readonly_reason = self._readonly_reason_for(exc)
        except OSError as exc:
            if getattr(exc, "winerror", None) in (32, 33):
                self._fh = open(self.path, "rb")
                self.readonly = True
                self.readonly_reason = self._readonly_reason_for(exc)
            else:
                raise
        self._size = os.path.getsize(self.path)
        if self._size % self.sector_size:
            # sektor sinirina tam oturmayan dosyalar da acilir, artik bayt yok sayilir
            self._size -= self._size % self.sector_size

    def _readonly_reason_for(self, exc: OSError) -> str:
        """Yazma icin acilamama nedenini insan diliyle acikla."""
        import stat as _stat

        win = getattr(exc, "winerror", None)
        if win in (32, 33):
            return ("Dosya baska bir program tarafindan kilitlenmis "
                    "(ornegin baska bir disk araci acik olabilir)")
        try:
            kip = os.stat(self.path).st_mode
            if not (kip & _stat.S_IWUSR):
                return "Dosya salt okunur isaretli (oznitelik/izin)"
        except OSError:
            pass
        return ("Yazma izni reddedildi — dosya baska bir program tarafindan "
                "kullaniliyor ya da erisim engellendi")

    # -- olusturma -----------------------------------------------------------
    @staticmethod
    def create(path: str, size_bytes: int, sparse: bool = True,
               sector_size: int = DEFAULT_SECTOR_SIZE,
               overwrite: bool = False) -> "DiskImage":
        """Yeni bir bos disk goruntusu olusturur.

        sparse=True ise dosya seyrek (sparse) olarak olusturulur; diskte yer
        kaplamaz, yalnizca yazilan bloklar gercek alan tuketir.
        """
        path = os.path.abspath(path)
        if os.path.exists(path) and not overwrite:
            raise DiskImageError(f"Dosya zaten var: {path}")
        if size_bytes < 64 * 1024:
            raise DiskImageError("Goruntu boyutu en az 64 KiB olmalidir")
        size_bytes -= size_bytes % sector_size
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "wb") as fh:
            if sparse:
                make_sparse(fh.fileno())        # Windows/NTFS icin gerekli
                truncate_sparse(fh.fileno(), size_bytes)
            else:
                block = b"\x00" * (1024 * 1024)
                remaining = size_bytes
                while remaining > 0:
                    n = min(len(block), remaining)
                    fh.write(block[:n])
                    remaining -= n
        return DiskImage(path, sector_size=sector_size)

    # -- temel G/C -----------------------------------------------------------
    @property
    def sector_count(self) -> int:
        return self._size // self.sector_size

    @property
    def size(self) -> int:
        return self._size

    def read(self, offset: int, length: int) -> bytes:
        if offset < 0 or length < 0:
            raise DiskImageError("Negatif ofset/uzunluk")
        if offset + length > self._size:
            raise DiskImageError(
                f"Okuma goruntu sinirini asiyor (ofset={offset}, uzunluk={length}, boyut={self._size})")
        self._fh.seek(offset)
        data = self._fh.read(length)
        if len(data) < length:  # seyrek dosyada olmamali ama garanti
            data += b"\x00" * (length - len(data))
        return data

    def write(self, offset: int, data: bytes) -> None:
        if self.readonly:
            raise DiskImageError("Goruntu salt okunur acildi")
        if offset < 0:
            raise DiskImageError("Negatif ofset")
        if offset + len(data) > self._size:
            raise DiskImageError(
                f"Yazma goruntu sinirini asiyor (ofset={offset}, uzunluk={len(data)}, boyut={self._size})")
        self._fh.seek(offset)
        self._fh.write(data)

    def flush(self) -> None:
        if not self.readonly:
            self._fh.flush()
            os.fsync(self._fh.fileno())

    def resize(self, new_size: int) -> None:
        """Goruntuyu buyutur/kucultur (kucultmek veri kaybettirir)."""
        if self.readonly:
            raise DiskImageError("Goruntu salt okunur acildi")
        new_size -= new_size % self.sector_size
        self._fh.flush()
        self._fh.truncate(new_size)
        self._size = new_size

    def close(self) -> None:
        try:
            if not self.readonly:
                self._fh.flush()
        finally:
            self._fh.close()

    def __enter__(self) -> "DiskImage":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"<DiskImage {self.path} {self.size} bayt / {self.sector_count} sektor>"


class PartitionView(BlockDevice):
    """Bir bolumun disk goruntusu uzerindeki penceresi.

    Dosya sistemi katmani her zaman bunun uzerinden calisir; boylece FS kodu
    bolumun diskteki yerini bilmek zorunda kalmaz.
    """

    def __init__(self, device: BlockDevice, start_lba: int, sector_count: int,
                 label: Optional[str] = None):
        if start_lba < 0 or sector_count <= 0:
            raise DiskImageError("Gecersiz bolum penceresi")
        if start_lba + sector_count > device.sector_count:
            raise DiskImageError("Bolum disk sinirlarinin disinda")
        self._dev = device
        self.sector_size = device.sector_size
        self.start_lba = start_lba
        self._sector_count = sector_count
        self.label = label or ""

    @property
    def sector_count(self) -> int:
        return self._sector_count

    @property
    def readonly(self) -> bool:
        return getattr(self._dev, "readonly", False)

    def _base(self) -> int:
        return self.start_lba * self.sector_size

    def read(self, offset: int, length: int) -> bytes:
        if offset + length > self.size:
            raise DiskImageError("Okuma bolum sinirini asiyor")
        return self._dev.read(self._base() + offset, length)

    def write(self, offset: int, data: bytes) -> None:
        if offset + len(data) > self.size:
            raise DiskImageError("Yazma bolum sinirini asiyor")
        self._dev.write(self._base() + offset, data)

    def flush(self) -> None:
        flush = getattr(self._dev, "flush", None)
        if flush:
            flush()

    def __repr__(self) -> str:
        return f"<PartitionView lba={self.start_lba} sektor={self.sector_count}>"
