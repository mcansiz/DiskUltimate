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
from typing import Callable, Optional

from .image import BlockDevice, DiskImage
from .ptable import human_size

MAGIC = b"DUBACKUP"
VERSION = 1
HEADER_SIZE = 512
INDEX_ENTRY = 16
DEFAULT_BLOCK = 1024 * 1024

BLOK_SIFIR = 0
BLOK_HAM = 1
BLOK_ZLIB = 2

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


def _bildir(progress: Progress, mesaj: str, yuzde: int) -> None:
    if progress:
        progress(mesaj, max(0, min(100, yuzde)))


# --------------------------------------------------------------------------
# Yedekleme
# --------------------------------------------------------------------------
def backup(device: BlockDevice, dest_path: str, compress: bool = True,
           block_size: int = DEFAULT_BLOCK, fs_type: str = "", label: str = "",
           progress: Progress = None) -> BackupInfo:
    """Aygiti (disk veya bolum) `.dub` dosyasina yedekler."""
    toplam = device.size
    if toplam <= 0:
        raise CloneError("Kaynak bos")
    block_size = max(64 * 1024, block_size)
    blok_sayisi = (toplam + block_size - 1) // block_size
    index_bayt = blok_sayisi * INDEX_ENTRY
    veri_baslangici = HEADER_SIZE + index_bayt

    klasor = os.path.dirname(os.path.abspath(dest_path))
    if klasor:
        os.makedirs(klasor, exist_ok=True)

    index = bytearray(index_bayt)
    _bildir(progress, "Yedekleme baslatiliyor...", 0)
    with open(dest_path, "wb") as fh:
        fh.seek(veri_baslangici)
        yazilan = 0
        for i in range(blok_sayisi):
            ofset = i * block_size
            uzunluk = min(block_size, toplam - ofset)
            blok = device.read(ofset, uzunluk)
            if not blok.strip(b"\x00"):
                struct.pack_into("<BxxxIQ", index, i * INDEX_ENTRY,
                                 BLOK_SIFIR, 0, 0)
            else:
                if compress:
                    paket = zlib.compress(blok, 6)
                    tur = BLOK_ZLIB if len(paket) < uzunluk else BLOK_HAM
                    if tur == BLOK_HAM:
                        paket = blok
                else:
                    tur, paket = BLOK_HAM, blok
                konum = fh.tell()
                fh.write(paket)
                struct.pack_into("<BxxxIQ", index, i * INDEX_ENTRY,
                                 tur, len(paket), konum)
                yazilan += len(paket)
            if progress and (i % 8 == 0 or i == blok_sayisi - 1):
                _bildir(progress,
                        f"Yedekleniyor... {human_size(ofset + uzunluk)} / {human_size(toplam)}",
                        int(98 * (i + 1) / blok_sayisi))

        simdi = datetime.datetime.now()
        baslik = bytearray(HEADER_SIZE)
        baslik[0:8] = MAGIC
        struct.pack_into("<HHIQIQI", baslik, 8,
                         VERSION, 1 if compress else 0, block_size, toplam,
                         device.sector_size, int(simdi.timestamp()), blok_sayisi)
        baslik[40:72] = fs_type.encode("utf-8")[:32].ljust(32, b"\x00")
        baslik[72:136] = label.encode("utf-8")[:64].ljust(64, b"\x00")
        struct.pack_into("<H", baslik, 510, 0xAA55)
        fh.seek(0)
        fh.write(bytes(baslik))
        fh.write(bytes(index))

    _bildir(progress, "Tamamlandi", 100)
    return read_backup_info(dest_path)


def read_backup_info(path: str) -> BackupInfo:
    """Yedek dosyasinin basligini okur."""
    with open(path, "rb") as fh:
        baslik = fh.read(HEADER_SIZE)
    if len(baslik) < HEADER_SIZE or baslik[:8] != MAGIC:
        raise CloneError("Gecerli bir DiskUltimate yedek dosyasi degil")
    (surum, bayraklar, blok_boyutu, toplam, sektor_boyutu, zaman,
     blok_sayisi) = struct.unpack_from("<HHIQIQI", baslik, 8)
    if surum > VERSION:
        raise CloneError(f"Yedek surumu desteklenmiyor: {surum}")
    fs_type = baslik[40:72].rstrip(b"\x00").decode("utf-8", "ignore")
    label = baslik[72:136].rstrip(b"\x00").decode("utf-8", "ignore")
    try:
        olusturma = datetime.datetime.fromtimestamp(zaman)
    except (OverflowError, OSError, ValueError):
        olusturma = None
    return BackupInfo(path=os.path.abspath(path), total_bytes=toplam,
                      sector_size=sektor_boyutu, block_size=blok_boyutu,
                      block_count=blok_sayisi, fs_type=fs_type, label=label,
                      created=olusturma, compressed=bool(bayraklar & 1),
                      file_size=os.path.getsize(path))


# --------------------------------------------------------------------------
# Geri yukleme
# --------------------------------------------------------------------------
def restore(src_path: str, device: BlockDevice, progress: Progress = None,
            allow_smaller_source: bool = True) -> BackupInfo:
    """`.dub` yedegini aygita geri yukler."""
    bilgi = read_backup_info(src_path)
    if bilgi.total_bytes > device.size:
        raise CloneError(
            f"Hedef cok kucuk: yedek {human_size(bilgi.total_bytes)}, "
            f"hedef {human_size(device.size)}")
    if bilgi.total_bytes < device.size and not allow_smaller_source:
        raise CloneError("Yedek hedeften kucuk")

    _bildir(progress, "Geri yukleme baslatiliyor...", 0)
    with open(src_path, "rb") as fh:
        index = fh.read(HEADER_SIZE + bilgi.block_count * INDEX_ENTRY)[HEADER_SIZE:]
        for i in range(bilgi.block_count):
            tur, uzunluk, konum = struct.unpack_from("<BxxxIQ", index, i * INDEX_ENTRY)
            ofset = i * bilgi.block_size
            hedef_uzunluk = min(bilgi.block_size, bilgi.total_bytes - ofset)
            if hedef_uzunluk <= 0:
                break
            if tur == BLOK_SIFIR:
                # hedefte eski veri varsa temizle, yoksa dokunma (seyreklik korunur)
                if device.read(ofset, hedef_uzunluk).strip(b"\x00"):
                    device.write(ofset, b"\x00" * hedef_uzunluk)
            else:
                fh.seek(konum)
                ham = fh.read(uzunluk)
                if tur == BLOK_ZLIB:
                    ham = zlib.decompress(ham)
                device.write(ofset, ham[:hedef_uzunluk])
            if progress and (i % 8 == 0 or i == bilgi.block_count - 1):
                _bildir(progress,
                        f"Geri yukleniyor... {human_size(ofset + hedef_uzunluk)} / "
                        f"{human_size(bilgi.total_bytes)}",
                        int(98 * (i + 1) / bilgi.block_count))
    f = getattr(device, "flush", None)
    if f:
        f()
    _bildir(progress, "Tamamlandi", 100)
    return bilgi


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
    toplam = src.size
    kopyalanan = 0
    _bildir(progress, "Klonlama baslatiliyor...", 0)
    while kopyalanan < toplam:
        uzunluk = min(block_size, toplam - kopyalanan)
        blok = src.read(kopyalanan, uzunluk)
        if blok.strip(b"\x00"):
            dst.write(kopyalanan, blok)
        elif dst.read(kopyalanan, uzunluk).strip(b"\x00"):
            dst.write(kopyalanan, b"\x00" * uzunluk)
        kopyalanan += uzunluk
        _bildir(progress,
                f"Klonlaniyor... {human_size(kopyalanan)} / {human_size(toplam)}",
                int(99 * kopyalanan / toplam))
    f = getattr(dst, "flush", None)
    if f:
        f()
    _bildir(progress, "Tamamlandi", 100)
    return kopyalanan


def clone_to_new_image(src: BlockDevice, dest_path: str, size_bytes: int = 0,
                       sparse: bool = True, progress: Progress = None) -> str:
    """Kaynagi yeni bir goruntu dosyasina klonlar."""
    boyut = size_bytes or src.size
    if boyut < src.size:
        raise CloneError("Hedef boyut kaynaktan kucuk olamaz")
    hedef = DiskImage.create(dest_path, boyut, sparse=sparse, overwrite=True)
    try:
        clone(src, hedef, progress=progress)
    finally:
        hedef.close()
    return os.path.abspath(dest_path)
