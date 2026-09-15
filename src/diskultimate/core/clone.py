"""Yedekleme, geri yukleme ve klonlama.

Yedek bicimi `.dub` (DiskUltimate Backup) — belgesi: .claude/specs/dub.md
  * Blok tabanli: her blok sifir / ham / zlib olarak isaretlenir.
  * Sifir bloklar dosyada yer kaplamaz, bu yuzden bos alani cok olan bolumlerin
    yedegi cok kucuk olur.
  * Basliktan dosya sistemi, etiket, boyut ve tarih okunabilir; geri yukleme
    oncesi hedef boyut denetlenir.
"""
from __future__ import annotations

import datetime
import os
import struct
import zlib
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .image import BlockDevice, DiskImage
from .ptable import human_size

MAGIC = b"DUBACKUP"
VERSION = 1
HEADER_SIZE = 512
INDEX_ENTRY = 16
DEFAULT_BLOCK = 1024 * 1024

BLOCK_ZERO = 0
BLOCK_RAW = 1
BLOCK_ZLIB = 2

Progress = Optional[Callable[[str, int], None]]


class CloneError(Exception):
    pass


@dataclass
class BackupInfo:
    path: str
    total_bytes: int
    sector_size: int
    block_size: int
    block_count: int
    fs_type: str
    label: str
    created: Optional[datetime.datetime]
    compressed: bool
    file_size: int

    @property
    def ratio(self) -> float:
        return self.file_size / self.total_bytes if self.total_bytes else 0.0

    def summary(self) -> dict:
        return {
            "Yedek dosyasi": os.path.basename(self.path),
            "Kaynak boyut": human_size(self.total_bytes),
            "Yedek boyut": human_size(self.file_size),
            "Kazanc": f"%{100 * (1 - self.ratio):.1f}",
            "Dosya sistemi": self.fs_type or "-",
            "Etiket": self.label or "-",
            "Blok boyutu": human_size(self.block_size),
            "Olusturma": self.created.strftime("%Y-%m-%d %H:%M") if self.created else "-",
            "Sikistirma": "zlib" if self.compressed else "yok",
        }


def _report(progress: Progress, message: str, percent: int) -> None:
    if progress:
        progress(message, max(0, min(100, percent)))


# --------------------------------------------------------------------------
# Yedekleme
# --------------------------------------------------------------------------
def backup(device: BlockDevice, dest_path: str, compress: bool = True,
           block_size: int = DEFAULT_BLOCK, fs_type: str = "", label: str = "",
           progress: Progress = None) -> BackupInfo:
    """Aygiti (disk veya bolum) `.dub` dosyasina yedekler."""
    total = device.size
    if total <= 0:
        raise CloneError("Kaynak bos")
    block_size = max(64 * 1024, block_size)
    block_count = (total + block_size - 1) // block_size
    index_bytes = block_count * INDEX_ENTRY
    veri_baslangici = HEADER_SIZE + index_bytes

    klasor = os.path.dirname(os.path.abspath(dest_path))
    if klasor:
        os.makedirs(klasor, exist_ok=True)

    index = bytearray(index_bytes)
    _report(progress, "Yedekleme baslatiliyor...", 0)
    with open(dest_path, "wb") as fh:
        fh.seek(veri_baslangici)
        yazilan = 0
        for i in range(block_count):
            ofset = i * block_size
            length = min(block_size, total - ofset)
            block = device.read(ofset, length)
            if not block.strip(b"\x00"):
                struct.pack_into("<BxxxIQ", index, i * INDEX_ENTRY,
                                 BLOCK_ZERO, 0, 0)
            else:
                if compress:
                    paket = zlib.compress(block, 6)
                    kind = BLOCK_ZLIB if len(paket) < length else BLOCK_RAW
                    if kind == BLOCK_RAW:
                        paket = block
                else:
                    kind, paket = BLOCK_RAW, block
                pos = fh.tell()
                fh.write(paket)
                struct.pack_into("<BxxxIQ", index, i * INDEX_ENTRY,
                                 kind, len(paket), pos)
                yazilan += len(paket)
            if progress and (i % 8 == 0 or i == block_count - 1):
                _report(progress,
                        f"Yedekleniyor... {human_size(ofset + length)} / {human_size(total)}",
                        int(98 * (i + 1) / block_count))

        simdi = datetime.datetime.now()
        header = bytearray(HEADER_SIZE)
        header[0:8] = MAGIC
        struct.pack_into("<HHIQIQI", header, 8,
                         VERSION, 1 if compress else 0, block_size, total,
                         device.sector_size, int(simdi.timestamp()), block_count)
        header[40:72] = fs_type.encode("utf-8")[:32].ljust(32, b"\x00")
        header[72:136] = label.encode("utf-8")[:64].ljust(64, b"\x00")
        struct.pack_into("<H", header, 510, 0xAA55)
        fh.seek(0)
        fh.write(bytes(header))
        fh.write(bytes(index))

    _report(progress, "Tamamlandi", 100)
    return read_backup_info(dest_path)


def is_backup_file(path: str) -> bool:
    """Dosya bir `.dub` yedegi mi? Yalnizca imzaya bakar, hizlidir.

    Bu denetim **acma yolunda** gereklidir: `.dub` basliginin 510. baytinda
    `0xAA55` durur (bkz. specs/dub.md). Yedek ham goruntu gibi acilirsa bolum
    tablosu cozumleyicisi bunu **gecerli ama bos bir MBR** sanar ve kullaniciya
    "bos disk" gosterir — veri kaybi yoktur ama yaniltir.
    """
    try:
        with open(path, "rb") as fh:
            return fh.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def read_backup_info(path: str) -> BackupInfo:
    """Yedek dosyasinin basligini okur."""
    with open(path, "rb") as fh:
        header = fh.read(HEADER_SIZE)
    if len(header) < HEADER_SIZE or header[:8] != MAGIC:
        raise CloneError("Gecerli bir DiskUltimate yedek dosyasi degil")
    (surum, bayraklar, block_size, total, sector_size, zaman,
     block_count) = struct.unpack_from("<HHIQIQI", header, 8)
    if surum > VERSION:
        raise CloneError(f"Yedek surumu desteklenmiyor: {surum}")
    fs_type = header[40:72].rstrip(b"\x00").decode("utf-8", "ignore")
    label = header[72:136].rstrip(b"\x00").decode("utf-8", "ignore")
    try:
        olusturma = datetime.datetime.fromtimestamp(zaman)
    except (OverflowError, OSError, ValueError):
        olusturma = None
    return BackupInfo(path=os.path.abspath(path), total_bytes=total,
                      sector_size=sector_size, block_size=block_size,
                      block_count=block_count, fs_type=fs_type, label=label,
                      created=olusturma, compressed=bool(bayraklar & 1),
                      file_size=os.path.getsize(path))


# --------------------------------------------------------------------------
# Geri yukleme
# --------------------------------------------------------------------------
class DubImage(BlockDevice):
    """`.dub` yedegini **salt okunur bir disk gibi** sunar.

    Yedek blok tablolidir: her blok icin (tur, uzunluk, ofset) bilinir, yani
    rastgele erisim mumkundur. Bu sinif istenen bayt araligini kapsayan bloklari
    bulur, gerekiyorsa `zlib` ile acar ve birlestirir. Boylece bolum tablosu,
    dosya sistemi surucusu ve dosya gezgini yedegin icerigini **geri yuklemeden**
    gezebilir.

    Sifir bloklar dosyada yer kaplamaz; okunduklarinda sifir uretilir.
    Yazma islemi desteklenmez — yedek bir arsivdir, uzerine yazilmaz.
    """

    def __init__(self, path: str, cache_blocks: int = 4):
        self.path = path
        self.info = read_backup_info(path)
        self.sector_size = self.info.sector_size or 512
        self.readonly = True
        self.readonly_reason = ("Yedek dosyasi (.dub) salt okunur acilir; "
                                "degistirmek icin bir diske geri yukleyin")
        self._block_size = self.info.block_size
        self._count = self.info.block_count
        self._fh = open(path, "rb")
        index_bytes = self._count * INDEX_ENTRY
        self._fh.seek(HEADER_SIZE)
        self._index = self._fh.read(index_bytes)
        if len(self._index) < index_bytes:
            raise CloneError("Yedek dosyasi eksik: indeks okunamadi")
        # Kucuk bir LRU: ardisik okumalarda ayni blok tekrar acilmasin.
        self._cache: Dict[int, bytes] = {}
        self._cache_order: List[int] = []
        self._cache_limit = max(1, cache_blocks)

    # -- BlockDevice arayuzu ------------------------------------------------
    @property
    def sector_count(self) -> int:
        return self.info.total_bytes // self.sector_size

    @property
    def size(self) -> int:
        return self.info.total_bytes

    def read(self, offset: int, length: int) -> bytes:
        if offset < 0 or length < 0:
            raise CloneError("Gecersiz okuma araligi")
        total = self.info.total_bytes
        if offset >= total:
            return b""
        length = min(length, total - offset)
        out = bytearray()
        pos = offset
        while len(out) < length:
            no = pos // self._block_size
            ic = pos - no * self._block_size
            alinacak = min(self._block_size - ic, length - len(out))
            out += self._block(no)[ic:ic + alinacak]
            pos += alinacak
        return bytes(out)

    def write(self, offset: int, data: bytes) -> None:
        raise CloneError(self.readonly_reason)

    def close(self) -> None:
        fh, self._fh = self._fh, None
        if fh is not None:
            fh.close()

    # -- ic isleyis ---------------------------------------------------------
    def _block(self, no: int) -> bytes:
        """Bir blogun acilmis icerigini dondurur (son blok kisa olabilir)."""
        hit = self._cache.get(no)
        if hit is not None:
            return hit
        boy = min(self._block_size,
                  self.info.total_bytes - no * self._block_size)
        if no >= self._count:
            return b"\x00" * max(0, boy)
        kind, length, pos = struct.unpack_from("<BxxxIQ", self._index,
                                               no * INDEX_ENTRY)
        if kind == BLOCK_ZERO:
            data = b"\x00" * boy
        else:
            self._fh.seek(pos)
            data = self._fh.read(length)
            if kind == BLOCK_ZLIB:
                data = zlib.decompress(data)
            # Son blok tam blok boyutunda olmayabilir; eksikse sifirla tamamla.
            data = data[:boy].ljust(boy, b"\x00")
        self._cache[no] = data
        self._cache_order.append(no)
        if len(self._cache_order) > self._cache_limit:
            self._cache.pop(self._cache_order.pop(0), None)
        return data


def restore(src_path: str, device: BlockDevice, progress: Progress = None,
            allow_smaller_source: bool = True) -> BackupInfo:
    """`.dub` yedegini aygita geri yukler."""
    info = read_backup_info(src_path)
    if info.total_bytes > device.size:
        raise CloneError(
            f"Hedef cok kucuk: yedek {human_size(info.total_bytes)}, "
            f"hedef {human_size(device.size)}")
    if info.total_bytes < device.size and not allow_smaller_source:
        raise CloneError("Yedek hedeften kucuk")

    _report(progress, "Geri yukleme baslatiliyor...", 0)
    with open(src_path, "rb") as fh:
        index = fh.read(HEADER_SIZE + info.block_count * INDEX_ENTRY)[HEADER_SIZE:]
        for i in range(info.block_count):
            kind, length, pos = struct.unpack_from("<BxxxIQ", index, i * INDEX_ENTRY)
            ofset = i * info.block_size
            target_length = min(info.block_size, info.total_bytes - ofset)
            if target_length <= 0:
                break
            if kind == BLOCK_ZERO:
                # hedefte eski veri varsa temizle, yoksa dokunma (seyreklik korunur)
                if device.read(ofset, target_length).strip(b"\x00"):
                    device.write(ofset, b"\x00" * target_length)
            else:
                fh.seek(pos)
                ham = fh.read(length)
                if kind == BLOCK_ZLIB:
                    ham = zlib.decompress(ham)
                device.write(ofset, ham[:target_length])
            if progress and (i % 8 == 0 or i == info.block_count - 1):
                _report(progress,
                        f"Geri yukleniyor... {human_size(ofset + target_length)} / "
                        f"{human_size(info.total_bytes)}",
                        int(98 * (i + 1) / info.block_count))
    f = getattr(device, "flush", None)
    if f:
        f()
    _report(progress, "Tamamlandi", 100)
    return info


# --------------------------------------------------------------------------
# Dogrudan klonlama
# --------------------------------------------------------------------------
def clone(src: BlockDevice, dst: BlockDevice, block_size: int = DEFAULT_BLOCK,
          progress: Progress = None) -> int:
    """Bir aygitin icerigini digerine kopyalar. Kopyalanan bayti dondurur."""
    if dst.size < src.size:
        raise CloneError(
            f"Hedef cok kucuk: kaynak {human_size(src.size)}, hedef {human_size(dst.size)}")
    if getattr(dst, "readonly", False):
        raise CloneError("Hedef salt okunur")
    total = src.size
    kopyalanan = 0
    _report(progress, "Klonlama baslatiliyor...", 0)
    while kopyalanan < total:
        length = min(block_size, total - kopyalanan)
        block = src.read(kopyalanan, length)
        if block.strip(b"\x00"):
            dst.write(kopyalanan, block)
        elif dst.read(kopyalanan, length).strip(b"\x00"):
            dst.write(kopyalanan, b"\x00" * length)
        kopyalanan += length
        _report(progress,
                f"Klonlaniyor... {human_size(kopyalanan)} / {human_size(total)}",
                int(99 * kopyalanan / total))
    f = getattr(dst, "flush", None)
    if f:
        f()
    _report(progress, "Tamamlandi", 100)
    return kopyalanan


def clone_to_new_image(src: BlockDevice, dest_path: str, size_bytes: int = 0,
                       sparse: bool = True, progress: Progress = None) -> str:
    """Kaynagi yeni bir goruntu dosyasina klonlar."""
    size = size_bytes or src.size
    if size < src.size:
        raise CloneError("Hedef boyut kaynaktan kucuk olamaz")
    target = DiskImage.create(dest_path, size, sparse=sparse, overwrite=True)
    try:
        clone(src, target, progress=progress)
    finally:
        target.close()
    return os.path.abspath(dest_path)
