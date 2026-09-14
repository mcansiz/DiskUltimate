"""Veri kurtarma: silinmis dosyalar, kayip bolumler, imza tabanli tarama.

Uc bagimsiz yetenek sunar:
  1. `scan_deleted`      — FAT/exFAT dizinlerinde silinmis giris tarama
  2. `scan_lost_partitions` — diskte dosya sistemi imzasi tarayarak kayip bolum bulma
  3. `carve_files`       — dosya imzalarindan ham veri kurtarma (dizin kaydi olmadan)

Tum islemler **salt okunur**dur: kaynak goruntuye hicbir sey yazilmaz, kurtarilan
veriler yerel diske cikarilir.
"""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterator, List, Optional, Tuple

from .exfat import (ATTR_DIRECTORY as EX_DIR, E_FILE, E_NAME, E_STREAM,
                    ExFatFS, ExFatError)
from .fat import ATTR_DIRECTORY, ATTR_LFN, ATTR_VOLUME_ID, FatFS, FatError
from .image import BlockDevice
from .ptable import human_size

Progress = Optional[Callable[[str, int], None]]


class RecoveryError(Exception):
    pass


# ==========================================================================
# 1. Silinmis dosya tarama
# ==========================================================================
@dataclass
class DeletedFile:
    name: str
    path: str
    size: int
    cluster: int
    is_dir: bool = False
    contiguous: bool = True
    condition: str = "bilinmiyor"     # 'iyi' | 'kismen uzerine yazilmis' | 'kayip'
    recoverable_bytes: int = 0
    fs_type: str = ""

    @property
    def confidence(self) -> int:
        """Kurtarilabilirlik yuzdesi (kaba tahmin)."""
        if self.size <= 0:
            return 0
        return int(100 * min(1.0, self.recoverable_bytes / self.size))


def scan_deleted(fs, progress: Progress = None) -> List[DeletedFile]:
    """Silinmis dosya girislerini tarar (FatFS veya ExFatFS)."""
    if isinstance(fs, FatFS):
        return _scan_deleted_fat(fs, progress)
    if isinstance(fs, ExFatFS):
        return _scan_deleted_exfat(fs, progress)
    raise RecoveryError("Bu dosya sisteminde silinmis dosya taramasi desteklenmiyor")


def _cluster_free_run(fs, start: int, needed: int) -> int:
    """Kac kume hala **bos** (yani uzerine yazilmamis) — kurtarilabilirlik olcusu."""
    if start < 2 or needed <= 0:
        return 0
    saglam = 0
    for i in range(needed):
        c = start + i
        if c > fs.max_cluster if isinstance(fs, FatFS) else c > fs.cluster_count + 1:
            break
        try:
            bos = (fs.get_fat(c) == 0) if isinstance(fs, FatFS) else (not fs.is_used(c))
        except Exception:
            break
        if not bos:
            break
        saglam += 1
    return saglam


def _scan_deleted_fat(fs: FatFS, progress: Progress = None,
                      path: str = "/", depth: int = 0) -> List[DeletedFile]:
    bulunan: List[DeletedFile] = []
    if depth > 12:
        return bulunan
    try:
        cluster = fs._dir_cluster(path)
        veri, _zincir = fs._read_dir(cluster)
    except FatError:
        return bulunan

    # Silinen girislerde LFN sira bayti da 0xE5 ile ezildigi icin sira numarasi
    # guvenilmezdir. LFN girisleri diskte ters sirada durdugundan fiziksel sira
    # kullanilir: toplanan parcalar sonunda ters cevrilir.
    lfn_parcalari: List[str] = []
    for off in range(0, len(veri) - 31, 32):
        ham = veri[off:off + 32]
        ilk = ham[0]
        if ilk == 0x00:
            break
        attr = ham[11]
        if ilk == 0xE5:
            if attr == ATTR_LFN:
                metin = (ham[1:11] + ham[14:26] + ham[28:32]).decode("utf-16-le", "ignore")
                for kesici in ("￿", "\x00"):
                    kes = metin.find(kesici)
                    if kes >= 0:
                        metin = metin[:kes]
                lfn_parcalari.append(metin)
                continue
            if attr & ATTR_VOLUME_ID:
                lfn_parcalari = []
                continue
            # 8.3 adin ilk karakteri silinirken ezildigi icin '_' ile temsil edilir
            ad = fs._decode_short(b"_" + ham[1:11], ham[12])
            if lfn_parcalari:
                birlesik = "".join(reversed(lfn_parcalari))
                if birlesik:
                    ad = birlesik
            lfn_parcalari = []
            kume = (struct.unpack_from("<H", ham, 20)[0] << 16) | \
                struct.unpack_from("<H", ham, 26)[0]
            boyut = struct.unpack_from("<I", ham, 28)[0]
            klasor_mu = bool(attr & ATTR_DIRECTORY)
            gereken = max(1, (boyut + fs.cluster_bytes - 1) // fs.cluster_bytes)
            saglam = _cluster_free_run(fs, kume, gereken)
            bulunan.append(DeletedFile(
                name=ad, path=path.rstrip("/") + "/" + ad, size=boyut, cluster=kume,
                is_dir=klasor_mu, contiguous=True,
                recoverable_bytes=min(boyut, saglam * fs.cluster_bytes),
                condition=("iyi" if saglam >= gereken else
                           ("kismen uzerine yazilmis" if saglam else "kayip")),
                fs_type=fs.fs_type_name))
            continue
        lfn_parcalari = []

    # alt dizinlere in
    try:
        for giris in fs.listdir(path):
            if giris.is_dir:
                bulunan += _scan_deleted_fat(fs, progress,
                                             path.rstrip("/") + "/" + giris.name,
                                             depth + 1)
    except FatError:
        pass
    if progress and depth == 0:
        progress(f"{len(bulunan)} silinmis giris bulundu", 100)
    return bulunan


def _scan_deleted_exfat(fs: ExFatFS, progress: Progress = None,
                        path: str = "/", depth: int = 0) -> List[DeletedFile]:
    bulunan: List[DeletedFile] = []
    if depth > 12:
        return bulunan
    try:
        cluster = fs._dir_cluster(path)
        veri = fs._read_chain_data(cluster)
    except ExFatError:
        return bulunan

    i = 0
    while i + 32 <= len(veri):
        tur = veri[i]
        if tur == 0x00:
            break
        # InUse biti temizse giris silinmis demektir
        if tur == (E_FILE & 0x7F):
            ikincil = veri[i + 1]
            toplam = (ikincil + 1) * 32
            if i + toplam > len(veri):
                break
            kume_verisi = veri[i:i + toplam]
            attr = struct.unpack_from("<H", kume_verisi, 4)[0]
            stream = kume_verisi[32:64]
            if len(stream) >= 32 and stream[0] in (E_STREAM, E_STREAM & 0x7F):
                bayraklar = stream[1]
                ad_uzunlugu = stream[3]
                ilk_kume = struct.unpack_from("<I", stream, 20)[0]
                boyut = struct.unpack_from("<Q", stream, 24)[0]
                ad = ""
                for n in range(2, ikincil + 1):
                    giris = kume_verisi[n * 32:(n + 1) * 32]
                    if giris and giris[0] in (E_NAME, E_NAME & 0x7F):
                        ad += giris[2:32].decode("utf-16-le", "ignore")
                ad = ad[:ad_uzunlugu] or f"(adsiz@{ilk_kume})"
                gereken = max(1, (boyut + fs.cluster_bytes - 1) // fs.cluster_bytes)
                saglam = _cluster_free_run(fs, ilk_kume, gereken)
                bulunan.append(DeletedFile(
                    name=ad, path=path.rstrip("/") + "/" + ad, size=boyut,
                    cluster=ilk_kume, is_dir=bool(attr & EX_DIR),
                    contiguous=bool(bayraklar & 0x02),
                    recoverable_bytes=min(boyut, saglam * fs.cluster_bytes),
                    condition=("iyi" if saglam >= gereken else
                               ("kismen uzerine yazilmis" if saglam else "kayip")),
                    fs_type="exFAT"))
            i += toplam
            continue
        i += 32

    try:
        for giris in fs.listdir(path):
            if giris.is_dir:
                bulunan += _scan_deleted_exfat(fs, progress,
                                               path.rstrip("/") + "/" + giris.name,
                                               depth + 1)
    except ExFatError:
        pass
    if progress and depth == 0:
        progress(f"{len(bulunan)} silinmis giris bulundu", 100)
    return bulunan


def recover_deleted(fs, item: DeletedFile, dest_path: str) -> int:
    """Silinmis dosyayi yerel diske kurtarir. Yazilan bayt sayisini dondurur.

    FAT zinciri silme sirasinda temizlendigi icin veri **ardisik** varsayilir;
    parcalanmis dosyalarda sonuc eksik olabilir (durum alani bunu belirtir).
    """
    if item.size <= 0 or item.cluster < 2:
        raise RecoveryError("Bu giriste kurtarilabilir veri yok")
    if os.path.isdir(dest_path):
        dest_path = os.path.join(dest_path, item.name)
    os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
    kume_bayt = fs.cluster_bytes
    gereken = (item.size + kume_bayt - 1) // kume_bayt
    yazilan = 0
    with open(dest_path, "wb") as fh:
        for i in range(gereken):
            c = item.cluster + i
            try:
                blok = fs.read_cluster(c) if isinstance(fs, FatFS) else \
                    fs.dev.read(fs.cluster_offset(c), kume_bayt)
            except Exception:
                break
            kalan = item.size - yazilan
            fh.write(blok[:kalan])
            yazilan += min(len(blok), kalan)
    return yazilan


# ==========================================================================
# 2. Kayip bolum tarama
# ==========================================================================
@dataclass
class LostPartition:
    start_lba: int
    sector_count: int
    fs_type: str
    label: str = ""
    sector_size: int = 512

    @property
    def end_lba(self) -> int:
        return self.start_lba + max(1, self.sector_count) - 1

    @property
    def size(self) -> int:
        return self.sector_count * self.sector_size


def _probe_signature(blok: bytes, dev: BlockDevice, lba: int) -> Optional[LostPartition]:
    """Tek bir sektorde dosya sistemi imzasi arar ve boyutu basliktan okur."""
    ss = dev.sector_size
    if len(blok) < 512:
        return None
    # exFAT
    if blok[3:11] == b"EXFAT   ":
        toplam = struct.unpack_from("<Q", blok, 72)[0]
        if 0 < toplam <= dev.sector_count:
            return LostPartition(lba, toplam, "exFAT", "", ss)
    # NTFS
    if blok[3:11] == b"NTFS    ":
        toplam = struct.unpack_from("<Q", blok, 40)[0] + 1
        if 0 < toplam <= dev.sector_count:
            return LostPartition(lba, toplam, "NTFS", "", ss)
    # FAT12/16/32
    if blok[510:512] == b"\x55\xAA" and blok[0] in (0xEB, 0xE9, 0xE8):
        bps = struct.unpack_from("<H", blok, 11)[0]
        spc = blok[13]
        rezerve = struct.unpack_from("<H", blok, 14)[0]
        fat_sayisi = blok[16]
        if (bps in (512, 1024, 2048, 4096) and spc in (1, 2, 4, 8, 16, 32, 64, 128)
                and rezerve and fat_sayisi in (1, 2)):
            toplam = (struct.unpack_from("<H", blok, 19)[0]
                      or struct.unpack_from("<I", blok, 32)[0])
            if 0 < toplam <= dev.sector_count:
                kok = struct.unpack_from("<H", blok, 17)[0]
                tur = "FAT32" if kok == 0 else "FAT16"
                etiket_off = 0x47 if kok == 0 else 0x2B
                etiket = blok[etiket_off:etiket_off + 11].decode("latin-1", "ignore").strip()
                return LostPartition(lba, toplam, tur, etiket, ss)
    return None


def scan_lost_partitions(device: BlockDevice, step_sectors: int = 2048,
                         deep: bool = False, progress: Progress = None,
                         known_ranges: Optional[List[Tuple[int, int]]] = None
                         ) -> List[LostPartition]:
    """Diski tarayarak bolum tablosunda olmayan dosya sistemlerini bulur.

    `step_sectors` tarama adimi (varsayilan 1 MiB). `deep=True` ile 64 KiB
    adimla daha yavas ama kapsamli tarama yapilir.
    """
    if deep:
        step_sectors = max(1, 65536 // device.sector_size)
    step_sectors = max(1, step_sectors)
    bilinen = known_ranges or []
    toplam_sektor = device.sector_count
    bulunanlar: List[LostPartition] = []
    okuma_blogu = max(step_sectors, 2048)

    lba = 0
    while lba < toplam_sektor:
        adet = min(okuma_blogu, toplam_sektor - lba)
        try:
            veri = device.read_sectors(lba, adet)
        except Exception:
            break
        for i in range(0, adet, step_sectors):
            mevcut = lba + i
            aday = _probe_signature(veri[i * device.sector_size:
                                         (i + 1) * device.sector_size],
                                    device, mevcut)
            if aday is None:
                continue
            if any(bas <= mevcut <= son for bas, son in bilinen):
                continue          # zaten tabloda olan bolum
            if any(b.start_lba == mevcut for b in bulunanlar):
                continue
            bulunanlar.append(aday)
        lba += adet
        if progress:
            progress(f"Taraniyor... {human_size(lba * device.sector_size)} / "
                     f"{human_size(device.size)} — {len(bulunanlar)} aday",
                     int(99 * lba / max(1, toplam_sektor)))
    if progress:
        progress(f"Tarama bitti: {len(bulunanlar)} aday bolum", 100)
    return bulunanlar


# ==========================================================================
# 3. Imza tabanli dosya kurtarma (file carving)
# ==========================================================================
@dataclass
class FileSignature:
    key: str
    label: str
    header: bytes
    footer: bytes = b""
    max_size: int = 32 * 1024 * 1024
    extension: str = "bin"


SIGNATURES: List[FileSignature] = [
    FileSignature("jpg", "JPEG goruntu", b"\xFF\xD8\xFF", b"\xFF\xD9", 24 * 1024 * 1024, "jpg"),
    FileSignature("png", "PNG goruntu", b"\x89PNG\r\n\x1a\n", b"IEND\xaeB`\x82", 64 * 1024 * 1024, "png"),
    FileSignature("gif", "GIF goruntu", b"GIF8", b"\x00\x3B", 16 * 1024 * 1024, "gif"),
    FileSignature("pdf", "PDF belgesi", b"%PDF-", b"%%EOF", 128 * 1024 * 1024, "pdf"),
    FileSignature("zip", "ZIP / Office belgesi", b"PK\x03\x04", b"", 256 * 1024 * 1024, "zip"),
    FileSignature("rar", "RAR arsivi", b"Rar!\x1a\x07", b"", 256 * 1024 * 1024, "rar"),
    FileSignature("7z", "7-Zip arsivi", b"7z\xbc\xaf\x27\x1c", b"", 256 * 1024 * 1024, "7z"),
    FileSignature("gz", "GZIP arsivi", b"\x1f\x8b\x08", b"", 128 * 1024 * 1024, "gz"),
    FileSignature("mp3", "MP3 ses", b"ID3", b"", 32 * 1024 * 1024, "mp3"),
    FileSignature("mp4", "MP4 video", b"\x00\x00\x00\x18ftyp", b"", 512 * 1024 * 1024, "mp4"),
    FileSignature("exe", "Windows calistirilabilir", b"MZ", b"", 128 * 1024 * 1024, "exe"),
    FileSignature("elf", "ELF calistirilabilir", b"\x7fELF", b"", 128 * 1024 * 1024, "elf"),
    FileSignature("sqlite", "SQLite veritabani", b"SQLite format 3\x00", b"", 256 * 1024 * 1024, "db"),
]
SIGNATURES_BY_KEY = {s.key: s for s in SIGNATURES}


@dataclass
class CarvedFile:
    signature: str
    label: str
    offset: int
    size: int
    extension: str

    @property
    def suggested_name(self) -> str:
        return f"kurtarilan_{self.offset:012X}.{self.extension}"


def carve_files(device: BlockDevice, keys: Optional[List[str]] = None,
                block_size: int = 4 * 1024 * 1024, max_results: int = 5000,
                progress: Progress = None) -> List[CarvedFile]:
    """Ham veride dosya imzasi arar (dizin kaydi olmadan kurtarma).

    Bicimlendirilmis veya bolum tablosu bozulmus alanlarda calisir; yalnizca
    okuma yapar.
    """
    secilen = [s for s in SIGNATURES if not keys or s.key in keys]
    if not secilen:
        raise RecoveryError("Hicbir dosya turu secilmedi")
    en_uzun_imza = max(len(s.header) for s in secilen)
    toplam = device.size
    bulunanlar: List[CarvedFile] = []
    onceki_kuyruk = b""
    konum = 0

    while konum < toplam and len(bulunanlar) < max_results:
        uzunluk = min(block_size, toplam - konum)
        blok = device.read(konum, uzunluk)
        tampon = onceki_kuyruk + blok
        taban = konum - len(onceki_kuyruk)
        for imza in secilen:
            ara = 0
            while True:
                bulundu = tampon.find(imza.header, ara)
                if bulundu < 0:
                    break
                mutlak = taban + bulundu
                ara = bulundu + 1
                if any(c.offset == mutlak for c in bulunanlar):
                    continue
                boyut = _carve_size(device, mutlak, imza, toplam)
                if boyut > 0:
                    bulunanlar.append(CarvedFile(imza.key, imza.label, mutlak,
                                                 boyut, imza.extension))
                    if len(bulunanlar) >= max_results:
                        break
        onceki_kuyruk = tampon[-(en_uzun_imza - 1):] if en_uzun_imza > 1 else b""
        konum += uzunluk
        if progress:
            progress(f"Imza taraniyor... {human_size(konum)} / {human_size(toplam)} — "
                     f"{len(bulunanlar)} dosya", int(99 * konum / max(1, toplam)))
    if progress:
        progress(f"Tarama bitti: {len(bulunanlar)} dosya", 100)
    return bulunanlar


def _carve_size(device: BlockDevice, offset: int, sig: FileSignature,
                total: int) -> int:
    """Imzadan itibaren dosya boyutunu tahmin eder."""
    sinir = min(sig.max_size, total - offset)
    if sinir <= 0:
        return 0
    if not sig.footer:
        return min(sinir, sig.max_size)
    parca = 1024 * 1024
    okunan = 0
    kuyruk = b""
    while okunan < sinir:
        uzunluk = min(parca, sinir - okunan)
        veri = kuyruk + device.read(offset + okunan, uzunluk)
        bulundu = veri.find(sig.footer)
        if bulundu >= 0:
            return okunan - len(kuyruk) + bulundu + len(sig.footer)
        kuyruk = veri[-(len(sig.footer) - 1):] if len(sig.footer) > 1 else b""
        okunan += uzunluk
    return 0        # bitis imzasi bulunamadi: guvenilir degil, atlanir


def extract_carved(device: BlockDevice, item: CarvedFile, dest_dir: str,
                   name: Optional[str] = None) -> str:
    """Imzayla bulunan dosyayi yerel diske yazar."""
    os.makedirs(dest_dir, exist_ok=True)
    hedef = os.path.join(dest_dir, name or item.suggested_name)
    kalan = item.size
    konum = item.offset
    with open(hedef, "wb") as fh:
        while kalan > 0:
            uzunluk = min(4 * 1024 * 1024, kalan)
            fh.write(device.read(konum, uzunluk))
            konum += uzunluk
            kalan -= uzunluk
    return hedef
