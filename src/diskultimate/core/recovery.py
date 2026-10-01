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
from .image import BlockDevice, PartitionView
from .platform import restore_owner
from .ptable import human_size
from ..i18n import mark, tr
from ..i18n import tr

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
    raise RecoveryError(tr("Bu dosya sisteminde silinmis dosya taramasi "
                           "desteklenmiyor"))


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
            free = (fs.get_fat(c) == 0) if isinstance(fs, FatFS) else (not fs.is_used(c))
        except Exception:
            break
        if not free:
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
        first = ham[0]
        if first == 0x00:
            break
        attr = ham[11]
        if first == 0xE5:
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
            name = fs._decode_short(b"_" + ham[1:11], ham[12])
            if lfn_parcalari:
                birlesik = "".join(reversed(lfn_parcalari))
                if birlesik:
                    name = birlesik
            lfn_parcalari = []
            cluster_no = (struct.unpack_from("<H", ham, 20)[0] << 16) | \
                struct.unpack_from("<H", ham, 26)[0]
            size = struct.unpack_from("<I", ham, 28)[0]
            klasor_mu = bool(attr & ATTR_DIRECTORY)
            gereken = max(1, (size + fs.cluster_bytes - 1) // fs.cluster_bytes)
            saglam = _cluster_free_run(fs, cluster_no, gereken)
            bulunan.append(DeletedFile(
                name=name, path=path.rstrip("/") + "/" + name, size=size, cluster=cluster_no,
                is_dir=klasor_mu, contiguous=True,
                recoverable_bytes=min(size, saglam * fs.cluster_bytes),
                condition=("iyi" if saglam >= gereken else
                           ("kismen uzerine yazilmis" if saglam else "kayip")),
                fs_type=fs.fs_type_name))
            continue
        lfn_parcalari = []

    # alt dizinlere in
    try:
        for entry in fs.listdir(path):
            if entry.is_dir:
                bulunan += _scan_deleted_fat(fs, progress,
                                             path.rstrip("/") + "/" + entry.name,
                                             depth + 1)
    except FatError:
        pass
    if progress and depth == 0:
        progress(tr("{} silinmis giris bulundu", len(bulunan)), 100)
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
        kind = veri[i]
        if kind == 0x00:
            break
        # InUse biti temizse giris silinmis demektir
        if kind == (E_FILE & 0x7F):
            ikincil = veri[i + 1]
            total = (ikincil + 1) * 32
            if i + total > len(veri):
                break
            cluster_data = veri[i:i + total]
            attr = struct.unpack_from("<H", cluster_data, 4)[0]
            stream = cluster_data[32:64]
            if len(stream) >= 32 and stream[0] in (E_STREAM, E_STREAM & 0x7F):
                bayraklar = stream[1]
                name_length = stream[3]
                first_cluster = struct.unpack_from("<I", stream, 20)[0]
                size = struct.unpack_from("<Q", stream, 24)[0]
                name = ""
                for n in range(2, ikincil + 1):
                    entry = cluster_data[n * 32:(n + 1) * 32]
                    if entry and entry[0] in (E_NAME, E_NAME & 0x7F):
                        name += entry[2:32].decode("utf-16-le", "ignore")
                name = name[:name_length] or f"(adsiz@{first_cluster})"
                gereken = max(1, (size + fs.cluster_bytes - 1) // fs.cluster_bytes)
                saglam = _cluster_free_run(fs, first_cluster, gereken)
                bulunan.append(DeletedFile(
                    name=name, path=path.rstrip("/") + "/" + name, size=size,
                    cluster=first_cluster, is_dir=bool(attr & EX_DIR),
                    contiguous=bool(bayraklar & 0x02),
                    recoverable_bytes=min(size, saglam * fs.cluster_bytes),
                    condition=("iyi" if saglam >= gereken else
                               ("kismen uzerine yazilmis" if saglam else "kayip")),
                    fs_type="exFAT"))
            i += total
            continue
        i += 32

    try:
        for entry in fs.listdir(path):
            if entry.is_dir:
                bulunan += _scan_deleted_exfat(fs, progress,
                                               path.rstrip("/") + "/" + entry.name,
                                               depth + 1)
    except ExFatError:
        pass
    if progress and depth == 0:
        progress(tr("{} silinmis giris bulundu", len(bulunan)), 100)
    return bulunan


def recover_deleted(fs, item: DeletedFile, dest_path: str) -> int:
    """Silinmis dosyayi yerel diske kurtarir. Yazilan bayt sayisini dondurur.

    FAT zinciri silme sirasinda temizlendigi icin veri **ardisik** varsayilir;
    parcalanmis dosyalarda sonuc eksik olabilir (durum alani bunu belirtir).
    """
    if item.size <= 0 or item.cluster < 2:
        raise RecoveryError(tr("Bu giriste kurtarilabilir veri yok"))
    if os.path.isdir(dest_path):
        dest_path = os.path.join(dest_path, item.name)
    os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
    cluster_bytes = fs.cluster_bytes
    gereken = (item.size + cluster_bytes - 1) // cluster_bytes
    yazilan = 0
    with open(dest_path, "wb") as fh:
        for i in range(gereken):
            c = item.cluster + i
            try:
                block = fs.read_cluster(c) if isinstance(fs, FatFS) else \
                    fs.dev.read(fs.cluster_offset(c), cluster_bytes)
            except Exception:
                break
            kalan = item.size - yazilan
            fh.write(block[:kalan])
            yazilan += min(len(block), kalan)
    restore_owner(dest_path)    # yetkili kopyada dosya root'a ait kalmasin
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


def _probe_signature(block: bytes, dev: BlockDevice, lba: int) -> Optional[LostPartition]:
    """Tek bir sektorde dosya sistemi imzasi arar ve boyutu basliktan okur."""
    ss = dev.sector_size
    if len(block) < 512:
        return None
    # exFAT
    if block[3:11] == b"EXFAT   ":
        total = struct.unpack_from("<Q", block, 72)[0]
        if 0 < total <= dev.sector_count:
            return LostPartition(lba, total, "exFAT", "", ss)
    # NTFS
    if block[3:11] == b"NTFS    ":
        total = struct.unpack_from("<Q", block, 40)[0] + 1
        if 0 < total <= dev.sector_count:
            return LostPartition(lba, total, "NTFS", "", ss)
    extra = _probe_modern(block, dev)
    if extra is not None:
        fs_type, size_bytes, label = extra
        count = size_bytes // ss
        if 0 < count and lba + count <= dev.sector_count:
            return LostPartition(lba, count, fs_type, label, ss)
        return None
    # FAT12/16/32
    if block[510:512] == b"\x55\xAA" and block[0] in (0xEB, 0xE9, 0xE8):
        bps = struct.unpack_from("<H", block, 11)[0]
        spc = block[13]
        rezerve = struct.unpack_from("<H", block, 14)[0]
        fat_count = block[16]
        if (bps in (512, 1024, 2048, 4096) and spc in (1, 2, 4, 8, 16, 32, 64, 128)
                and rezerve and fat_count in (1, 2)):
            total = (struct.unpack_from("<H", block, 19)[0]
                      or struct.unpack_from("<I", block, 32)[0])
            if 0 < total <= dev.sector_count:
                root = struct.unpack_from("<H", block, 17)[0]
                kind = "FAT32" if root == 0 else "FAT16"
                etiket_off = 0x47 if root == 0 else 0x2B
                etiket = block[etiket_off:etiket_off + 11].decode("latin-1", "ignore").strip()
                return LostPartition(lba, total, kind, etiket, ss)
    return None


# Adaydan itibaren okunan pencere: btrfs ustblogu 64 KiB'dedir.
PROBE_WINDOW = 0x10000 + 0x1000


def _probe_modern(head: bytes, dev: BlockDevice) -> Optional[Tuple[str, int, str]]:
    """(kind_name, boyut_bayt, etiket) — boyutu ustbloktan okunabilen turler.

    Boyutu bilinemeyen turler (LUKS, UDF, LVM) burada **aranmaz**: kayip bolum
    tabloya boyutuyla eklenir; tahmini boyut yanlis bolum demektir.
    """
    ss = dev.sector_size
    # ext2/3/4: ustblok +1024; yalnizca birincil (s_block_group_nr == 0)
    sb = head[1024:2048]
    if len(sb) == 1024 and struct.unpack_from("<H", sb, 0x38)[0] == 0xEF53 \
            and struct.unpack_from("<H", sb, 0x5A)[0] == 0:
        log_bs = struct.unpack_from("<I", sb, 0x18)[0]
        if log_bs <= 6:
            blocks = struct.unpack_from("<I", sb, 0x04)[0]
            incompat = struct.unpack_from("<I", sb, 0x60)[0]
            if incompat & 0x0080:
                blocks |= struct.unpack_from("<I", sb, 0x150)[0] << 32
            compat = struct.unpack_from("<I", sb, 0x5C)[0]
            ro = struct.unpack_from("<I", sb, 0x64)[0]
            kind_name = "ext4" if incompat & 0x0040 or ro & 0x0008 else \
                ("ext3" if compat & 0x0004 else "ext2")
            label = sb[0x78:0x88].split(b"\x00")[0].decode("utf-8", "ignore")
            return kind_name, blocks * (1024 << log_bs), label
    # XFS (buyuk sonlu)
    if head[:4] == b"XFSB" and len(head) >= 512:
        bs = struct.unpack_from(">I", head, 4)[0]
        dblocks = struct.unpack_from(">Q", head, 8)[0]
        if bs in (512, 1024, 2048, 4096, 8192, 16384, 32768, 65536):
            label = head[0x6C:0x78].split(b"\x00")[0].decode("utf-8", "ignore")
            return "XFS", bs * dblocks, label
    # HFS+ / HFSX
    h = head[1024:1536]
    if len(h) >= 64 and h[:2] in (b"H+", b"HX") and \
            struct.unpack_from(">H", h, 2)[0] in (4, 5):
        bs = struct.unpack_from(">I", h, 40)[0]
        total = struct.unpack_from(">I", h, 44)[0]
        if bs and bs & (bs - 1) == 0:
            return ("HFSX" if h[:2] == b"HX" else "HFS+"), bs * total, ""
    # APFS kapsayicisi (nesne turu 1 = NX ustblogu)
    if head[32:36] == b"NXSB" and struct.unpack_from("<I", head, 24)[0] & 0xFFFF == 1:
        bs = struct.unpack_from("<I", head, 36)[0]
        count = struct.unpack_from("<Q", head, 40)[0]
        if 4096 <= bs <= 65536:
            return "APFS", bs * count, ""
    # F2FS: ustblok +1024
    f = head[1024:1024 + 128]
    if len(f) >= 64 and f[:4] == b"\x10\x20\xF5\xF2":
        bs = 1 << struct.unpack_from("<I", f, 16)[0]
        count = struct.unpack_from("<Q", f, 36)[0]
        label = head[1024 + 0x7C:1024 + 0x7C + 64].decode("utf-16-le", "ignore").split("\x00")[0]
        return "F2FS", bs * count, label
    # btrfs: ustblok 64 KiB, bytenr alani kendini gostermeli (yansilar degil)
    b = head[0x10000:0x10000 + 0x1000]
    if len(b) >= 0x200 and b[0x40:0x48] == b"_BHRfS_M" and \
            struct.unpack_from("<Q", b, 0x30)[0] == 0x10000:
        label = b[0x12B:0x12B + 64].split(b"\x00")[0].decode("utf-8", "ignore")
        return "btrfs", struct.unpack_from("<Q", b, 0x70)[0], label
    # Linux takas
    if len(head) >= 4096 and head[4086:4096] == b"SWAPSPACE2":
        last_page = struct.unpack_from("<I", head, 1028)[0]
        label = head[1052:1068].split(b"\x00")[0].decode("utf-8", "ignore")
        return "Linux Takas", (last_page + 1) * 4096, label
    # ReFS
    if head[3:11] == b"ReFS\x00\x00\x00\x00" and head[16:20] == b"FSRS":
        sectors = struct.unpack_from("<Q", head, 0x18)[0]
        bps = struct.unpack_from("<I", head, 0x20)[0]
        if bps in (512, 4096):
            return "ReFS", sectors * bps, ""
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
    total_sectors = device.sector_count
    bulunanlar: List[LostPartition] = []
    okuma_blogu = max(step_sectors, 2048)

    lba = 0
    while lba < total_sectors:
        adet = min(okuma_blogu, total_sectors - lba)
        try:
            veri = device.read_sectors(lba, adet)
        except Exception:
            break
        ss = device.sector_size
        for i in range(0, adet, step_sectors):
            mevcut = lba + i
            if any(bas <= mevcut <= last for bas, last in bilinen):
                continue          # zaten tabloda olan bolum
            # Bulunmus bir bolumun icindeki aday atlanir: ext/XFS/APFS/btrfs
            # yedek ustbloklari ayri "kayip bolum" gibi gorunurdu.
            if any(b.start_lba <= mevcut <= b.end_lba for b in bulunanlar):
                continue
            window = veri[i * ss:i * ss + PROBE_WINDOW]
            if len(window) < PROBE_WINDOW and mevcut * ss + len(window) < device.size:
                try:
                    window = device.read(mevcut * ss, min(
                        PROBE_WINDOW, device.size - mevcut * ss))
                except Exception:          # noqa: BLE001
                    pass
            aday = _probe_signature(window, device, mevcut)
            if aday is None:
                continue
            if not aday.label:
                # NTFS ve HFS+ etiketi ustveri dosyasindadir ($Volume,
                # katalog); bulunan aday icin tam tespit bir kez calistirilir.
                try:
                    from .fsdetect import detect
                    aday.label = detect(PartitionView(
                        device, aday.start_lba, aday.sector_count)).label or ""
                except Exception:          # noqa: BLE001
                    pass
            bulunanlar.append(aday)
        lba += adet
        if progress:
            progress(tr("Taraniyor... {} / {} — {} aday",
                        human_size(lba * device.sector_size),
                        human_size(device.size), len(bulunanlar)),
                     int(99 * lba / max(1, total_sectors)))
    if progress:
        progress(tr("Tarama bitti: {} aday bolum", len(bulunanlar)), 100)
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
    FileSignature("jpg", mark("JPEG goruntu"), b"\xFF\xD8\xFF", b"\xFF\xD9", 24 * 1024 * 1024, "jpg"),
    FileSignature("png", mark("PNG goruntu"), b"\x89PNG\r\n\x1a\n", b"IEND\xaeB`\x82", 64 * 1024 * 1024, "png"),
    FileSignature("gif", mark("GIF goruntu"), b"GIF8", b"\x00\x3B", 16 * 1024 * 1024, "gif"),
    FileSignature("pdf", mark("PDF belgesi"), b"%PDF-", b"%%EOF", 128 * 1024 * 1024, "pdf"),
    FileSignature("zip", mark("ZIP / Office belgesi"), b"PK\x03\x04", b"", 256 * 1024 * 1024, "zip"),
    FileSignature("rar", mark("RAR arsivi"), b"Rar!\x1a\x07", b"", 256 * 1024 * 1024, "rar"),
    FileSignature("7z", mark("7-Zip arsivi"), b"7z\xbc\xaf\x27\x1c", b"", 256 * 1024 * 1024, "7z"),
    FileSignature("gz", mark("GZIP arsivi"), b"\x1f\x8b\x08", b"", 128 * 1024 * 1024, "gz"),
    FileSignature("mp3", mark("MP3 ses"), b"ID3", b"", 32 * 1024 * 1024, "mp3"),
    FileSignature("mp4", mark("MP4 video"), b"\x00\x00\x00\x18ftyp", b"", 512 * 1024 * 1024, "mp4"),
    FileSignature("exe", mark("Windows calistirilabilir"), b"MZ", b"", 128 * 1024 * 1024, "exe"),
    FileSignature("elf", mark("ELF calistirilabilir"), b"\x7fELF", b"", 128 * 1024 * 1024, "elf"),
    FileSignature("sqlite", mark("SQLite veritabani"), b"SQLite format 3\x00", b"", 256 * 1024 * 1024, "db"),
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
        return tr("kurtarilan_{:012X}.{}", self.offset, self.extension)


def carve_files(device: BlockDevice, keys: Optional[List[str]] = None,
                block_size: int = 4 * 1024 * 1024, max_results: int = 5000,
                progress: Progress = None) -> List[CarvedFile]:
    """Ham veride dosya imzasi arar (dizin kaydi olmadan kurtarma).

    Bicimlendirilmis veya bolum tablosu bozulmus alanlarda calisir; yalnizca
    okuma yapar.
    """
    secilen = [s for s in SIGNATURES if not keys or s.key in keys]
    if not secilen:
        raise RecoveryError(tr("Hicbir dosya turu secilmedi"))
    en_uzun_imza = max(len(s.header) for s in secilen)
    total = device.size
    bulunanlar: List[CarvedFile] = []
    onceki_kuyruk = b""
    pos = 0

    while pos < total and len(bulunanlar) < max_results:
        length = min(block_size, total - pos)
        block = device.read(pos, length)
        tampon = onceki_kuyruk + block
        taban = pos - len(onceki_kuyruk)
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
                size = _carve_size(device, mutlak, imza, total)
                if size > 0:
                    bulunanlar.append(CarvedFile(imza.key, imza.label, mutlak,
                                                 size, imza.extension))
                    if len(bulunanlar) >= max_results:
                        break
        onceki_kuyruk = tampon[-(en_uzun_imza - 1):] if en_uzun_imza > 1 else b""
        pos += length
        if progress:
            progress(tr("Imza taraniyor... {} / {} — {} dosya",
                        human_size(pos), human_size(total), len(bulunanlar)), int(99 * pos / max(1, total)))
    if progress:
        progress(tr("Tarama bitti: {} dosya", len(bulunanlar)), 100)
    return bulunanlar


def _carve_size(device: BlockDevice, offset: int, sig: FileSignature,
                total: int) -> int:
    """Imzadan itibaren dosya boyutunu tahmin eder."""
    sinir = min(sig.max_size, total - offset)
    if sinir <= 0:
        return 0
    if not sig.footer:
        return min(sinir, sig.max_size)
    chunk = 1024 * 1024
    okunan = 0
    kuyruk = b""
    while okunan < sinir:
        length = min(chunk, sinir - okunan)
        veri = kuyruk + device.read(offset + okunan, length)
        bulundu = veri.find(sig.footer)
        if bulundu >= 0:
            return okunan - len(kuyruk) + bulundu + len(sig.footer)
        kuyruk = veri[-(len(sig.footer) - 1):] if len(sig.footer) > 1 else b""
        okunan += length
    return 0        # bitis imzasi bulunamadi: guvenilir degil, atlanir


def extract_carved(device: BlockDevice, item: CarvedFile, dest_dir: str,
                   name: Optional[str] = None) -> str:
    """Imzayla bulunan dosyayi yerel diske yazar."""
    os.makedirs(dest_dir, exist_ok=True)
    target = os.path.join(dest_dir, name or item.suggested_name)
    kalan = item.size
    pos = item.offset
    with open(target, "wb") as fh:
        while kalan > 0:
            length = min(4 * 1024 * 1024, kalan)
            fh.write(device.read(pos, length))
            pos += length
            kalan -= length
    restore_owner(target)       # yetkili kopyada dosya root'a ait kalmasin
    return target
