"""Bolum yeniden boyutlandirma, tasima ve dosya sistemi sinirlari.

Tasarim:
  1. **Pencere** (`window_for`) — bolumun buyuyebilecegi kapsayici alan:
     onceki bolumun sonundan sonraki bolumun basina kadar. Kullanici bu
     pencerenin icinde baslangici ve boyutu serbestce secer; "onundeki bosluk"
     ve "arkasindaki bosluk" bu pencereden turer.
  2. **Dosya sistemi sinirlari** (`fs_resize_info`) — verinin gerektirdigi
     **asgari** ve dosya sisteminin destekledigi **azami** sektor sayisi.
     Bu sinirlar olmadan kucultme veri kaybidir; bu yuzden plan asamasinda
     hesaplanir ve asilirsa islem **reddedilir**.
  3. **Plan** (`plan_resize`) — dogrulama + ne yapilacaginin dokumu.
  4. **Uygulama** (`apply_resize`) — sira onemlidir:
        kucultme:  once dosya sistemi  -> sonra tablo
        buyutme:   once tablo          -> sonra dosya sistemi
        tasima:    veri kopyalanir, ardindan onyukleme sektorundeki
                   "gizli sektor" alani yeni baslangica gore duzeltilir.

Gercek dosya sistemi boyutlandirmasi FAT12/16/32, exFAT, **NTFS** ve
**ext2/3/4** icin saf Python'da yapilir; yani her platformda ve goruntu
dosyalarinda da calisir (NTFS: `ntfsresize.py`, ADR 0030; ext: `extresize.py`
buyutme + `extmove.py` kucultme, ADR 0052). Windows'ta fiziksel disklerde
NTFS icin isletim sisteminin kendi boyutlandiricisi yine tercih edilir: bagli
birimi kendisi cozer. Windows ext'i tanimadigi icin ext her yerde saf
Python yolundan gider.
"""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from .exfat import EOC as EXFAT_EOC
from .extmove import ext_shrink
from .extresize import ExtResizeError, ext_grow, ext_limits
from .exfat import ExFatError, ExFatFS, boot_checksum
from .fat import FatError, FatFS
from .image import BlockDevice, PartitionView
from .ntfsread import NtfsError
from .ntfsresize import ntfs_resize, ntfs_size_info
from .xfsgrow import XfsGrowError, xfs_grow, xfs_limits
from .ptable import (MBR_EXTENDED_TYPES, Partition, PartitionTable,
                     PartitionTableError, human_size)
from ..i18n import tr

MIB = 1024 * 1024
COPY_CHUNK = 4 * MIB          # tasima sirasinda tek seferde tasinan bayt

# FAT kume sayisi sinirlari (tip degistirmemek icin)
FAT12_MIN, FAT12_MAX = 1, 4084
FAT16_MIN, FAT16_MAX = 4085, 65524
FAT32_MIN, FAT32_MAX = 65525, 0x0FFFFFF5


class ResizeError(Exception):
    pass


# --------------------------------------------------------------------------
#  Kapsayici pencere
# --------------------------------------------------------------------------
@dataclass
class ResizeWindow:
    """Bolumun icinde hareket edebilecegi alan (bos alanlar dahil)."""
    start_lba: int
    end_lba: int          # dahil
    sector_size: int = 512

    @property
    def sector_count(self) -> int:
        return self.end_lba - self.start_lba + 1

    @property
    def size(self) -> int:
        return self.sector_count * self.sector_size


def window_for(table: PartitionTable, part: Partition) -> ResizeWindow:
    """Bolumun onundeki/arkasindaki bos alanla birlikte kapsayici alani."""
    if part.logical:
        kapsayici = _extended_of(table)
        if kapsayici is None:
            raise ResizeError(tr("Mantiksal bolumun genisletilmis bolumu bulunamadi"))
        lower = kapsayici.start_lba + table.align_sectors
        upper = kapsayici.end_lba
        komsular = [p for p in table.partitions if p.logical and p is not part]
    else:
        lower = table.first_usable_lba()
        upper = table.last_usable_lba()
        komsular = [p for p in table.partitions
                    if not p.logical and p is not part]

    bas = lower
    last = upper
    # Mantiksal bolumun onunde kendi EBR'si durur: onceki mantiksal bolumden
    # sonra bir hizalama birimi bos kalmali (olusturmadaki kuralla ayni).
    # Eskiden pencere bitisik baslangica izin veriyordu; EBR onceki bolumun
    # verisinin uzerine yazilirdi.
    ebr_gap = table.align_sectors if part.logical else 0
    for p in sorted(komsular, key=lambda x: x.start_lba):
        if p.end_lba < part.start_lba:
            bas = max(bas, p.end_lba + 1 + ebr_gap)
        elif p.start_lba > part.end_lba:
            last = min(last, p.start_lba - 1)
        else:
            raise ResizeError(tr("{} numarali bolum ile cakisma var", p.index))
    if part.logical:
        # her mantiksal bolumun onunde kendi EBR'si icin bir hizalama birimi durur
        bas = max(bas, lower)
    bas = table.align_up(bas)
    if bas > part.start_lba:
        bas = part.start_lba          # mevcut yerlesim hizasiz olabilir
    return ResizeWindow(bas, last, table.sector_size)


def _extended_of(table: PartitionTable) -> Optional[Partition]:
    for p in table.partitions:
        if not p.logical and getattr(p, "type_id", 0) in MBR_EXTENDED_TYPES:
            return p
    return None


# --------------------------------------------------------------------------
#  Dosya sistemi sinirlari
# --------------------------------------------------------------------------
@dataclass
class FsResizeInfo:
    """Bir bolumdeki dosya sisteminin boyutlandirma yetenekleri."""
    kind: str = "raw"             # 'fat' | 'exfat' | 'ntfs' | 'ext' | 'raw' | 'native' | 'unsupported'
    fs_type: str = ""
    min_sectors: int = 1          # verinin gerektirdigi asgari
    max_sectors: int = 0          # 0 = dosya sistemi tarafindan sinirsiz
    movable: bool = True          # baslangic degistirilebilir mi
    note: str = ""

    @property
    def resizable(self) -> bool:
        return self.kind in ("fat", "exfat", "ntfs", "ext", "xfs", "raw", "native")

    def limit_note(self) -> str:
        parcalar = []
        if self.min_sectors > 1:
            parcalar.append(tr("en az {}", human_size(self.min_sectors * 512)))
        if self.max_sectors:
            parcalar.append(tr("en cok {}", human_size(self.max_sectors * 512)))
        return ", ".join(parcalar)


def fs_resize_info(view: BlockDevice, fs_type: str,
                   native_ok: bool = False) -> FsResizeInfo:
    """Bolumdeki dosya sistemini inceleyip boyut sinirlarini cikarir."""
    kind = (fs_type or "").lower()
    try:
        if kind.startswith("fat"):
            return _fat_info(view)
        if kind == "exfat":
            return _exfat_info(view)
        if kind == "ntfs" and not native_ok:
            return _ntfs_info(view)
        if kind in ("ext2", "ext3", "ext4"):
            # Yerel arac (Windows Resize-Partition) ext'i tanimaz; ext her
            # platformda saf Python ile boyutlandirilir.
            return _ext_info(view, fs_type)
        if kind == "xfs":
            return _xfs_info(view)
    except (ExtResizeError, XfsGrowError) as exc:
        return FsResizeInfo(kind="unsupported", fs_type=fs_type, movable=False,
                            note=str(exc))
    except (FatError, ExFatError, NtfsError, Exception) as exc:   # noqa: BLE001
        return FsResizeInfo(
            kind="unsupported", fs_type=fs_type, movable=False,
            note=f"Dosya sistemi okunamadi ({exc}); boyutlandirma guvenli degil")

    if kind in ("", "raw", "bos", "unknown"):
        return FsResizeInfo(kind="raw", fs_type=fs_type or "Ham",
                            note="Dosya sistemi yok; alan serbestce degistirilebilir")

    if native_ok:
        return FsResizeInfo(
            kind="native", fs_type=fs_type, min_sectors=1, max_sectors=0,
            movable=False,
            note="Isletim sisteminin kendi boyutlandiricisi kullanilacak")

    return FsResizeInfo(
        kind="unsupported", fs_type=fs_type, movable=False,
        note=f"{fs_type} boyutlandirmasi bu platformda desteklenmiyor; "
             f"bolum kucultulemez")


# ---- NTFS -----------------------------------------------------------------
def _ntfs_info(view: BlockDevice) -> FsResizeInfo:
    """NTFS sinirlarini `$Bitmap` uzerinden cikarir (saf Python, ADR 0030)."""
    info = ntfs_size_info(view)
    note = info.note
    if info.dirty:
        # Kirli birim boyutlandirilmaz; sinir gostermek yerine nedeni soylenir.
        return FsResizeInfo(kind="unsupported", fs_type="NTFS", movable=False,
                            note=note)
    return FsResizeInfo(kind="ntfs", fs_type="NTFS",
                        min_sectors=info.min_sectors,
                        max_sectors=info.max_sectors,
                        movable=True, note=note)


# ---- ext2/3/4 ---------------------------------------------------------------
def _ext_info(view: BlockDevice, fs_type: str) -> FsResizeInfo:
    """ext sinirlari: en az = kullanilan veri + tasima payi (extmove),
    en cok = 32 bit blok sayisi (64bit ozelligi yoksa). Tasinabilir: ext
    bolumun diskteki yerini hicbir yapisinda tutmaz."""
    lim = ext_limits(view)
    ss = view.sector_size
    return FsResizeInfo(kind="ext", fs_type=fs_type,
                        min_sectors=max(1, (lim["min_bytes"] + ss - 1) // ss),
                        max_sectors=lim["max_bytes"] // ss,
                        movable=True)


# ---- XFS ------------------------------------------------------------------
def _xfs_info(view: BlockDevice) -> FsResizeInfo:
    """XFS kuculmez: en az = mevcut boy. Tasinabilir (bolum konumu
    tutulmaz). Buyutme saf Python (ADR 0068); desteklenmeyen birimde neden."""
    lim = xfs_limits(view)
    ss = view.sector_size
    if not lim["growable"]:
        from .xfsgrow import _Sb, _supported_reason
        return FsResizeInfo(kind="unsupported", fs_type="XFS", movable=False,
                            note=_supported_reason(_Sb(view.read(0, 512))))
    return FsResizeInfo(kind="xfs", fs_type="XFS",
                        min_sectors=(lim["min_bytes"] + ss - 1) // ss,
                        max_sectors=0, movable=True,
                        note=tr("XFS kucultulemez; yalnizca buyutulebilir"))


# ---- FAT ------------------------------------------------------------------
def _fat_type_bounds(fat_type: int) -> Tuple[int, int]:
    """Kume sayisinin tip degistirmeden kalabilecegi araligi dondurur."""
    if fat_type == 32:
        return FAT32_MIN, FAT32_MAX
    if fat_type == 16:
        return FAT16_MIN, FAT16_MAX
    return FAT12_MIN, FAT12_MAX


def _fat_layout_for(total: int, fs: FatFS) -> Tuple[int, int]:
    """Verilen toplam sektor icin (FAT boyutu, kume sayisi).

    `FatFS._compute_layout` ile ayni Microsoft formulu; fark, bicimlendirmede
    secilen `reserved` / `root_sectors` / `spc` degerlerinin **korunmasi**.
    Bu sayede yeniden boyutlandirma yerlesimi bozmadan yalnizca FAT tablosunu
    ve veri bolgesini buyutur.
    """
    reserved = fs.reserved_sectors
    root_sectors = fs.root_dir_sectors
    num_fats = fs.num_fats
    spc = fs.sectors_per_cluster
    tmp1 = total - (reserved + root_sectors)
    # 256 = 512 baytlik sektordeki FAT16 girisi; formul sektor boyutuyla
    # olceklenir (512'de ayni sonuc). Sabit 256, 4K sektorde FAT'i 8 kat
    # buyuk hesapliyordu.
    tmp2 = ((fs.bytes_per_sector // 2) * spc) + num_fats
    if fs.fat_type == 32:
        tmp2 //= 2
    if tmp1 <= 0 or tmp2 <= 0:
        return fs.fat_size, 0
    fat_size = max(1, (tmp1 + tmp2 - 1) // tmp2)
    veri = total - (reserved + num_fats * fat_size + root_sectors)
    return fat_size, max(0, veri // spc)


def _fat_max_total(fs: FatFS) -> int:
    """Tip degismeden ulasabilecegi en buyuk toplam sektor sayisi."""
    _, upper_cluster = _fat_type_bounds(fs.fat_type)
    lower, upper = fs.total_sectors, fs.total_sectors
    step = max(1, fs.total_sectors)
    while True:                       # once ust siniri kabaca bul
        _, cluster = _fat_layout_for(upper + step, fs)
        if cluster > upper_cluster or (upper + step) > (1 << 32) - 1:
            break
        upper += step
        step *= 2
    lower, yuksek = upper, upper + step
    while lower < yuksek:               # sonra ikili arama ile daralt
        orta = (lower + yuksek + 1) // 2
        _, cluster = _fat_layout_for(orta, fs)
        if cluster <= upper_cluster and orta <= (1 << 32) - 1:
            lower = orta
        else:
            yuksek = orta - 1
    return lower


def _fat_bounds(fs: FatFS) -> Tuple[int, int]:
    """(asgari, azami) kume sayisi — tip siniri.

    FAT32 BPB yapisindan tanindigi icin 65525'ten az kumeli FAT32 olabilir
    (mkdosfs -F 32). Onun alt siniri mevcut kume sayisidir: birim zaten
    oyledir, yeniden boyutlandirma onu daha da asagi itmez.
    """
    lower, upper = _fat_type_bounds(fs.fat_type)
    if fs.fat_type == 32:
        lower = min(lower, fs.cluster_count)
    return lower, upper


def _fat_highest_used(fs: FatFS) -> int:
    """Kullanilan en yuksek kume numarasi (hicbiri yoksa 1)."""
    for c in range(fs.cluster_count + 1, 1, -1):
        if fs.get_fat(c):
            return c
    return 1


def _to_dev(count: int, unit: int, view: BlockDevice, round_up: bool) -> int:
    """Dosya sistemi sektoru (`unit` bayt) -> aygit sektoru (view.sector_size).

    FAT'in BPB_BytsPerSec'i ve exFAT'in BytesPerSectorShift'i aygitin
    sektorunden farkli olabilir (mkfs.fat -S 4096, mkfs.exfat -s 4096).
    Plan ve arayuz aygit sektoruyle calisir; karistirmak birimi 8 kat
    yanlis boyutlandiriyordu (fsck.vfat: "Seek to ...: Invalid argument").
    """
    ss = view.sector_size
    if round_up:
        return (count * unit + ss - 1) // ss
    return count * unit // ss


def _from_dev(count: int, unit: int, view: BlockDevice) -> int:
    """Aygit sektoru -> dosya sistemi sektoru (asagi yuvarlar)."""
    return count * view.sector_size // unit


def _fat_info(view: BlockDevice) -> FsResizeInfo:
    fs = FatFS(view)
    reason = fs.write_block_reason
    if reason:
        return FsResizeInfo(kind="unsupported", fs_type=fs.fs_type_name,
                            movable=False, note=reason)
    lower_cluster, _ = _fat_bounds(fs)
    tepe = _fat_highest_used(fs)
    needed_clusters = max(tepe - 1, lower_cluster)
    # kucultmede FAT tablosunun yeri degismez: asgari boyut mevcut yerlesimden
    asgari = fs.first_data_sector + needed_clusters * fs.sectors_per_cluster
    azami = _fat_max_total(fs)
    note = ""
    if azami <= fs.total_sectors:
        note = f"FAT{fs.fat_type} kume siniri doldu; bu bolum buyutulemez"
    bps = fs.bytes_per_sector
    return FsResizeInfo(kind="fat", fs_type=fs.fs_type_name,
                        min_sectors=_to_dev(asgari, bps, view, True),
                        max_sectors=_to_dev(azami, bps, view, False),
                        movable=True, note=note)


def fat_resize(view: BlockDevice, new_sector_count: int) -> None:
    """FAT biriminin toplam sektor sayisini degistirir.

    Kucultmede FAT tablosu oldugu gibi birakilir (fazla tablo zararsizdir),
    bu yuzden **veri kopyalanmaz**. Buyutmede yeni kume sayisi daha buyuk bir
    FAT tablosu gerektirebilir; tablo buyudugunde kok dizin ve veri bolgesi
    tumuyle `num_fats * fark` sektor **ileri kaydirilir**. Kume numaralari
    degismedigi icin FAT icerigi oldugu gibi tasinir.

    `new_sector_count` aygit sektorudur (view.sector_size); hesap BPB
    sektorunde yapilir.
    """
    fs = FatFS(view)
    reason = fs.write_block_reason
    if reason:
        raise ResizeError(reason)
    bps = fs.bytes_per_sector
    new_sector_count = _from_dev(new_sector_count, bps, view)
    spc = fs.sectors_per_cluster
    lower_cluster, upper_cluster = _fat_bounds(fs)
    tepe = _fat_highest_used(fs)

    if new_sector_count <= fs.total_sectors:
        # --- kucultme / ayni boyut: yerlesim korunur -------------------------
        data_sectors = new_sector_count - fs.first_data_sector
        if data_sectors <= 0:
            raise ResizeError(tr("Yeni boyut FAT ust verisinden kucuk"))
        new_clusters = min(data_sectors // spc, upper_cluster)
        if new_clusters < lower_cluster:
            raise ResizeError(
                tr("Yeni boyut FAT{} icin cok kucuk (en az {} kume gerekir)",
                   fs.fat_type, lower_cluster))
        if new_clusters < tepe - 1:
            raise ResizeError(
                tr("Kucultme veri kaybina yol acar: dosyalar yeni sinirin "
                "otesinde. Once dosyalari tasiyin."))
        total = fs.first_data_sector + new_clusters * spc
        _fat_write_bpb(view, fs, total, fs.fat_size)
        taze = FatFS(view)
        for c in range(new_clusters + 2, fs.cluster_count + 2):
            taze.set_fat(c, 0)
        taze._free_count = None
        taze.flush()
        taze._update_fsinfo()      # FSInfo'daki bos kume sayaci tazelenir
        return

    # --- buyutme --------------------------------------------------------------
    new_fat, new_clusters = _fat_layout_for(new_sector_count, fs)
    if new_clusters > upper_cluster:
        # tip sinirini asmamak icin kume sayisi kirpilir
        new_clusters = upper_cluster
        new_fat, _ = _fat_layout_for(new_sector_count, fs)
    if new_clusters < lower_cluster:
        raise ResizeError(tr("FAT{} icin gecersiz kume sayisi", fs.fat_type))
    if new_fat < fs.fat_size:
        new_fat = fs.fat_size            # tablo asla kucultulmez
        veri = new_sector_count - (fs.reserved_sectors
                                   + fs.num_fats * new_fat
                                   + fs.root_dir_sectors)
        new_clusters = min(veri // spc, upper_cluster)

    kaydirma = fs.num_fats * (new_fat - fs.fat_size)
    new_first_data = (fs.reserved_sectors + fs.num_fats * new_fat
                     + fs.root_dir_sectors)
    total = new_first_data + new_clusters * spc
    if total > new_sector_count:
        raise ResizeError(tr("Hesaplanan yerlesim bolume sigmiyor"))

    if kaydirma:
        # kok dizin + kullanilan veri bolgesi ileri kaydirilir (sondan basa)
        bolge_bas = fs.reserved_sectors + fs.num_fats * fs.fat_size
        kullanilan = fs.root_dir_sectors + max(0, tepe - 1) * spc
        _shift_forward(view, bolge_bas, kaydirma, kullanilan, bps)

    # FAT kopyalari yeni boyutta yeniden yazilir (kume numaralari degismez).
    # Kaynak etkin FAT'tir (ExtFlags aynalama kapaliysa FAT0 bayat olabilir);
    # tum kopyalara yazmak zararsizdir, surucu yalnizca etkin olani okur.
    old_fat = view.read(fs.fat_offset(fs.active_fat), fs.fat_size * bps)
    tampon = bytearray(new_fat * bps)
    tampon[:len(old_fat)] = old_fat
    for i in range(fs.num_fats):
        view.write((fs.reserved_sectors + i * new_fat) * bps, bytes(tampon))

    _fat_write_bpb(view, fs, total, new_fat)
    taze = FatFS(view)
    taze._free_count = None
    taze._update_fsinfo()          # FSInfo'daki bos kume sayaci tazelenir
    flush = getattr(view, "flush", None)
    if flush:
        flush()


def _shift_forward(view: BlockDevice, start_lba: int, shift: int,
                   sector_count: int, bps: int) -> None:
    """Sektor blogunu `shift` sektor ileri kaydirir (sondan basa kopyalar).

    Sondan basa kopya yalnizca ileri kaydirmada guvenlidir; geri kaydirmada
    her yazma bir sonraki parcanin henuz okunmamis kaynagini ezer.
    """
    if shift < 0:
        raise ResizeError(tr("Ic hata: geri kaydirma istendi ({} sektor); "
                             "hicbir sey yazilmadi", shift))
    step = max(1, COPY_CHUNK // bps)
    kalan = sector_count
    while kalan > 0:
        n = min(step, kalan)
        kalan -= n
        veri = view.read((start_lba + kalan) * bps, n * bps)
        view.write((start_lba + kalan + shift) * bps, veri)


def _fat_write_bpb(view: BlockDevice, fs: FatFS, total: int,
                   fat_size: int) -> None:
    """BPB'deki toplam sektor ve FAT boyutu alanlarini gunceller."""
    bps = fs.bytes_per_sector
    boot = bytearray(view.read(0, bps))
    if total < 0x10000 and fs.fat_type != 32:
        struct.pack_into("<H", boot, 19, total)
        struct.pack_into("<I", boot, 32, 0)
    else:
        struct.pack_into("<H", boot, 19, 0)
        struct.pack_into("<I", boot, 32, total)
    if fs.fat_type == 32:
        struct.pack_into("<H", boot, 22, 0)
        struct.pack_into("<I", boot, 36, fat_size)
    else:
        struct.pack_into("<H", boot, 22, fat_size)
    view.write(0, bytes(boot))
    if fs.fat_type == 32:
        yedek = struct.unpack_from("<H", boot, 50)[0]
        if yedek and yedek < fs.reserved_sectors:
            view.write(yedek * bps, bytes(boot))


# ---- exFAT ----------------------------------------------------------------
def _exfat_bitmap_capacity(fs: ExFatFS) -> int:
    """Bitmap dosyasina ayrilmis kumelere sigan azami kume sayisi."""
    ayrilan = ((fs.bitmap_length + fs.cluster_bytes - 1) // fs.cluster_bytes)
    return max(0, ayrilan * fs.cluster_bytes * 8)


def _exfat_highest_used(fs: ExFatFS) -> int:
    for c in range(fs.cluster_count + 1, 1, -1):
        if fs.is_used(c):
            return c
    return 1


def _exfat_layout_for(total: int, fs: ExFatFS) -> Tuple[int, int, int]:
    """Verilen toplam sektor icin (FAT uzunlugu, heap ofseti, kume sayisi).

    `ExFatFS.format` ile ayni yinelemeli hesap; fat_offset ve kume boyutu
    korunur.
    """
    bps, spc = fs.bytes_per_sector, fs.sectors_per_cluster
    fat_offset = fs.fat_offset
    fat_length = fs.fat_length
    cluster = fs.cluster_count
    for _ in range(64):
        heap = fat_offset + fat_length
        artik = heap % spc
        if artik:
            heap += spc - artik
        kalan = total - heap
        if kalan <= 0:
            raise ResizeError(tr("Yeni boyut exFAT yerlesimi icin cok kucuk"))
        new_clusters = kalan // spc
        new_fat = ((new_clusters + 2) * 4 + bps - 1) // bps
        if new_fat == fat_length and new_clusters == cluster:
            break
        fat_length, cluster = new_fat, new_clusters
    heap = fat_offset + fat_length
    artik = heap % spc
    if artik:
        heap += spc - artik
    return fat_length, heap, (total - heap) // spc


def _exfat_info(view: BlockDevice) -> FsResizeInfo:
    fs = ExFatFS(view)
    reason = fs.write_block_reason
    if reason:
        return FsResizeInfo(kind="unsupported", fs_type="exFAT", movable=False,
                            note=reason)
    tepe = _exfat_highest_used(fs)
    spc = fs.sectors_per_cluster
    asgari = fs.cluster_heap_offset + max(1, tepe - 1) * spc
    # buyutmede FAT bolgesi ve bitmap yeniden yerlestirilir; sinir 32 bitlik
    # kume numarasi ve 64 bitlik VolumeLength alanidir
    azami = min((1 << 32) - 1, fs.cluster_heap_offset + (0xFFFFFFF5 - 2) * spc)
    bps = fs.bytes_per_sector
    return FsResizeInfo(kind="exfat", fs_type="exFAT",
                        min_sectors=_to_dev(asgari, bps, view, True),
                        max_sectors=_to_dev(azami, bps, view, False),
                        movable=True)


def exfat_resize(view: BlockDevice, new_sector_count: int,
                 partition_offset: Optional[int] = None) -> None:
    """exFAT biriminin boyutunu degistirir.

    Kucultmede yerlesim korunur; yalnizca VolumeLength/ClusterCount ve bitmap
    uzunlugu duser. Buyutmede FAT bolgesi gerektikce buyur, kume yigini
    ileri kaydirilir ve gerekirse ayirma bitmap'i yeni kumelere tasinir.
    """
    fs = ExFatFS(view)
    reason = fs.write_block_reason
    if reason:
        raise ResizeError(reason)
    # aygit sektoru -> exFAT sektoru (2^BytesPerSectorShift)
    new_sector_count = _from_dev(new_sector_count, fs.bytes_per_sector, view)
    spc = fs.sectors_per_cluster
    tepe = _exfat_highest_used(fs)

    if new_sector_count <= fs.volume_length:
        kullanilabilir = new_sector_count - fs.cluster_heap_offset
        if kullanilabilir <= 0:
            raise ResizeError(tr("Yeni boyut exFAT ust verisinden kucuk"))
        new_clusters = min(kullanilabilir // spc,
                        (fs.fat_length * fs.bytes_per_sector) // 4 - 2)
        if new_clusters < 1:
            raise ResizeError(tr("Yeni boyut exFAT icin cok kucuk"))
        if new_clusters < tepe - 1:
            raise ResizeError(
                tr("Kucultme veri kaybina yol acar: kumeler yeni sinirin otesinde"))
        _exfat_set_bitmap_length(fs, (new_clusters + 7) // 8, fs.cluster_count,
                                 new_clusters)
        _exfat_write_boot(view, fs, new_sector_count, new_clusters,
                          fs.fat_length, fs.cluster_heap_offset,
                          partition_offset)
        return

    # --- buyutme ------------------------------------------------------------
    bps = fs.bytes_per_sector
    new_fat, new_heap, new_clusters = _exfat_layout_for(new_sector_count, fs)
    new_fat = max(new_fat, fs.fat_length)  # FAT bolgesi asla kucultulmez
    # Kume yigini asla geri cekilmez. Windows yigini FAT'in hemen ardina
    # degil hizali bir sinira koyar (48 MB'ta FAT 128+96, yigin 256); yeniden
    # hesaplanan yer ondan once duser. Buyuyen FAT o bosluga sigiyorsa yigin
    # yerinde kalir (ADR 0084). Daha az kume, FAT'in yetmesini bozmaz.
    new_heap = max(new_heap, fs.cluster_heap_offset)
    new_clusters = (new_sector_count - new_heap) // spc
    kaydirma = new_heap - fs.cluster_heap_offset

    if kaydirma:
        _shift_forward(view, fs.cluster_heap_offset, kaydirma,
                       max(0, tepe - 1) * spc, bps)

    old_fat = view.read(fs.fat_offset * bps, fs.fat_length * bps)
    tampon = bytearray(new_fat * bps)
    tampon[:len(old_fat)] = old_fat
    view.write(fs.fat_offset * bps, bytes(tampon))

    _exfat_write_boot(view, fs, new_sector_count, new_clusters, new_fat,
                      new_heap, partition_offset)
    _exfat_extend_bitmap(view, fs.cluster_count)


def _exfat_set_bitmap_length(fs: ExFatFS, new_length: int,
                             old_clusters: int, new_clusters: int) -> None:
    """Bitmap'in gecerli uzunlugunu ve sinir disi bitlerini duzeltir.

    Bitmap kisalinca zinciri de kisalir: surplus kumeler FAT'tan cikarilip bos
    isaretlenir. Eskiden yalnizca uzunluk alani dusuyordu; Apple'in
    fsck_exfat'i "Cluster chain for Main Bitmap has too many clusters for its
    size" diyordu (uzun testler, macOS, 2026-10-04; Linux fsck.exfat bunu
    denetlemiyor).
    """
    cb = fs.cluster_bytes
    old_count = (fs.bitmap_length + cb - 1) // cb
    new_count = max(1, (new_length + cb - 1) // cb)
    surplus: List[int] = []
    if new_count < old_count:
        chain = fs.chain(fs.bitmap_cluster, old_count, False)
        if len(chain) >= old_count:
            surplus = chain[new_count:old_count]
            fs.set_fat(chain[new_count - 1], EXFAT_EOC)
            for c in surplus:
                fs.set_fat(c, 0)
    bm = fs._load_bitmap()
    if new_length > len(bm):
        bm.extend(b"\x00" * (new_length - len(bm)))
    fs._bitmap = bm[:new_length]
    artik = new_clusters % 8
    if artik and fs._bitmap:
        fs._bitmap[-1] &= (1 << artik) - 1
    for c in surplus:
        i = c - 2
        if 0 <= i < new_clusters:
            fs._bitmap[i >> 3] &= ~(1 << (i & 7)) & 0xFF
    fs._bitmap_dirty = True
    _exfat_patch_bitmap_entry(fs, fs.bitmap_cluster, new_length)
    fs.bitmap_length = new_length
    fs.cluster_count = new_clusters
    fs.flush()


def _exfat_patch_bitmap_entry(fs: ExFatFS, first_cluster: int,
                              length: int) -> None:
    """Kok dizindeki E_BITMAP girisinin konum ve uzunluk alanlarini yazar."""
    veri = fs._read_chain_data(fs.root_cluster)
    for off in range(0, len(veri) - 31, 32):
        if veri[off] == 0x00:
            break
        if veri[off] == 0x81:                          # E_BITMAP
            struct.pack_into("<I", veri, off + 20, first_cluster)
            struct.pack_into("<Q", veri, off + 24, length)
            fs._write_dir(fs.root_cluster, veri)
            return


def _exfat_free_run(data: bytearray, cluster_count: int,
                    adet: int) -> Optional[int]:
    """Bitmap icerigine gore `adet` ardisik bos kumenin ilkini bulur."""
    seri = 0
    for c in range(2, cluster_count + 2):
        i = c - 2
        nbytes = i >> 3
        used = bool(data[nbytes] & (1 << (i & 7))) if nbytes < len(data) else False
        if used:
            seri = 0
            continue
        seri += 1
        if seri == adet:
            return c - adet + 1
    return None


def _exfat_extend_bitmap(view: BlockDevice, old_clusters: int) -> None:
    """Buyutme sonrasi ayirma bitmap'ini yeni kume sayisina uyarlar.

    Bitmap mevcut kumelerine sigmiyorsa **en dusuk** bos ardisik alana tasinir.
    Yuksek kumelere tasimak, birimin sonradan kucultulmesini engelledigi icin
    bilerek tercih edilmez.
    """
    fs = ExFatFS(view)                     # yeni yerlesimle yeniden baglanir
    kb = fs.cluster_bytes
    gereken = (fs.cluster_count + 7) // 8
    old_length = fs.bitmap_length
    old_clusters = fs.chain(fs.bitmap_cluster,
                            max(1, (old_length + kb - 1) // kb))

    data = bytearray()
    for c in old_clusters:
        data += view.read(fs.cluster_offset(c), kb)
    data = data[:old_length]
    if len(data) < gereken:
        data.extend(b"\x00" * (gereken - len(data)))

    capacity = len(old_clusters) * kb
    if gereken <= capacity:
        target = old_clusters
        first = fs.bitmap_cluster
    else:
        adet = (gereken + kb - 1) // kb
        for c in old_clusters:             # eski kumeler once serbest sayilir
            i = c - 2
            data[i >> 3] &= ~(1 << (i & 7)) & 0xFF
        first = _exfat_free_run(data, fs.cluster_count, adet)
        if first is None:
            raise ResizeError(
                tr("Ayirma bitmap'i icin yeterli ardisik bos alan bulunamadi"))
        target = list(range(first, first + adet))
        for c in target:
            i = c - 2
            data[i >> 3] |= (1 << (i & 7))

    dolgu = bytes(data).ljust(len(target) * kb, b"\x00")
    for i, c in enumerate(target):
        view.write(fs.cluster_offset(c), dolgu[i * kb:(i + 1) * kb])

    # Zincir, kume LISTESI degistiyse yeniden yazilir — ilk kume ayni olsa
    # bile: kucultmede kisaltilan zincirin ardindaki kumeler bos kaldigi icin
    # yeni ardisik alan cogu zaman eski ilk kumeden baslar. Eskiden yalnizca
    # ilk kume degisince yaziliyordu; zincir kisa, uzunluk buyuk kaliyordu
    # (Apple fsck_exfat "Main Bitmap has too few clusters", Windows dosya
    # listelemiyor; uzun testler full profil, 6.7 GiB, ADR 0087).
    if target != old_clusters:
        eskiler = set(old_clusters)
        for i, c in enumerate(target):      # yeni zincir
            fs.set_fat(c, EXFAT_EOC if i == len(target) - 1 else c + 1)
            eskiler.discard(c)
        for c in eskiler:                  # artik kullanilmayan eski zincir
            fs.set_fat(c, 0)
    _exfat_patch_bitmap_entry(fs, first, gereken)
    fs.bitmap_cluster = first
    fs.bitmap_length = gereken
    fs._bitmap = None
    fs._update_percent()
    f = getattr(view, "flush", None)
    if f:
        f()


def _exfat_write_boot(view: BlockDevice, fs: ExFatFS, total_sectors: int,
                      cluster_count: int, fat_length: int, heap_offset: int,
                      partition_offset: Optional[int]) -> None:
    """Onyukleme bolgesini yeni boyutlarla yeniden yazar (ana + yedek)."""
    bps = fs.bytes_per_sector
    bolge = bytearray(view.read(0, 12 * bps))
    if bolge[3:11] != b"EXFAT   ":
        raise ResizeError(tr("exFAT onyukleme bolgesi taninmadi"))
    if partition_offset is not None:
        # PartitionOffset exFAT sektorudur; cagiran aygit LBA'si verir
        struct.pack_into("<Q", bolge, 64,
                         _from_dev(partition_offset, bps, view))
    struct.pack_into("<Q", bolge, 72, total_sectors)
    struct.pack_into("<I", bolge, 84, fat_length)
    struct.pack_into("<I", bolge, 88, heap_offset)
    struct.pack_into("<I", bolge, 92, cluster_count)
    saglama = boot_checksum(bytes(bolge[:11 * bps]), bps)
    bolge[11 * bps:12 * bps] = struct.pack("<I", saglama) * (bps // 4)
    for taban in (0, 12):
        view.write(taban * bps, bytes(bolge))
    f = getattr(view, "flush", None)
    if f:
        f()


# --------------------------------------------------------------------------
#  Plan
# --------------------------------------------------------------------------
@dataclass
class ResizePlan:
    index: int
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    sector_size: int
    fs: FsResizeInfo
    window: ResizeWindow
    warnings: List[str] = field(default_factory=list)

    @property
    def moves(self) -> bool:
        return self.new_start != self.old_start

    @property
    def grows(self) -> bool:
        return self.new_count > self.old_count

    @property
    def shrinks(self) -> bool:
        return self.new_count < self.old_count

    @property
    def move_bytes(self) -> int:
        return min(self.old_count, self.new_count) * self.sector_size

    @property
    def changed(self) -> bool:
        return self.moves or self.new_count != self.old_count

    def summary(self) -> str:
        satir = [tr("Bolum {}: {} -> {}", self.index,
                    human_size(self.old_count * self.sector_size),
                    human_size(self.new_count * self.sector_size))]
        if self.moves:
            # Yon ayri kelime olarak cumleye konmaz: cevrilmeden Turkce
            # kaliyordu ve dil dil soz dizimi farkli (cince ceviri yakaladi).
            fark = human_size(abs(self.new_start - self.old_start) * self.sector_size)
            kopya = human_size(self.move_bytes)
            if self.new_start > self.old_start:
                satir.append(tr("{} ileri tasinacak ({} veri kopyalanir)", fark, kopya))
            else:
                satir.append(tr("{} geri tasinacak ({} veri kopyalanir)", fark, kopya))
        return "; ".join(satir)


def plan_resize(session, index: int, new_start_lba: int,
                new_sector_count: int,
                info: Optional[FsResizeInfo] = None,
                window: Optional[ResizeWindow] = None) -> ResizePlan:
    """Istenen yerlesimi dogrular ve yapilacak islerin dokumunu dondurur.

    `info` verilirse dosya sistemi sinirlari yeniden okunmaz. Arayuz onu arka
    planda bir kez hesaplar; NTFS'te okuma butun `$Bitmap` taramasidir ve
    arayuz is parcaciginda tekrarlanmamalidir (ADR 0047).

    `window` verilirse kapsayici alan olarak o kullanilir: arayuz kuyruktaki
    adimlar sonrasi **planlanan** pencereyi verir (onceki adimin actigi bos
    alana buyume). Uygulama aninda `window` verilmez; o an diskteki tabloya
    gore yeniden dogrulanir — plan yanilirsa islem guvenle durur.
    """
    if session.table is None:
        raise ResizeError(tr("Bolum tablosu yok"))
    part = session.table.get(index)
    if window is None:
        window = window_for(session.table, part)
    sector_size = session.table.sector_size

    if new_sector_count <= 0:
        raise ResizeError(tr("Bolum boyutu sifir olamaz"))
    if new_start_lba < window.start_lba:
        raise ResizeError(
            tr("Baslangic kapsayici alanin disinda (en erken LBA {})",
               window.start_lba))
    if new_start_lba + new_sector_count - 1 > window.end_lba:
        raise ResizeError(
            tr("Bolum kapsayici alani asiyor (en gec LBA {})", window.end_lba))

    if info is None:
        info = fs_resize_info_for(session, part)
    if info.kind == "native" and new_start_lba != part.start_lba:
        # Windows'un araci tasiyamaz; tasima saf Python yolundan gider.
        info = fs_resize_info(session.view(part), info.fs_type, native_ok=False)
    uyarilar: List[str] = []

    if new_sector_count < part.sector_count:
        if not info.resizable:
            raise ResizeError(
                tr("Kucultme yapilamaz — {}", info.note))
        if info.min_sectors > new_sector_count:
            raise ResizeError(
                tr("Bu dosya sistemi {} altina inemez (veri kaybi olurdu)",
                   human_size(info.min_sectors * sector_size)))
    if new_sector_count > part.sector_count:
        if info.max_sectors and new_sector_count > info.max_sectors:
            uyarilar.append(
                tr("Dosya sistemi en fazla {} olabilir; kalan alan bolum "
                   "icinde **kullanilmadan** kalir", human_size(info.max_sectors * sector_size)))
        elif info.kind == "unsupported":
            uyarilar.append(
                tr("Bolum buyutuluyor ama dosya sistemi buyutulemiyor; "
                "eklenen alan kullanilamaz"))
    if new_start_lba != part.start_lba:
        if not info.movable:
            raise ResizeError(
                tr("Bu dosya sistemi tasinamaz — {}", info.note))
        uyarilar.append(
            tr("{} veri kopyalanacak; islem yarida kesilirse bolum bozulur",
               human_size(min(part.sector_count, new_sector_count) * sector_size)))
    if session.is_physical:
        uyarilar.append(tr("Fiziksel disk: islem oncesi yedek alin"))

    return ResizePlan(index=index, old_start=part.start_lba,
                      old_count=part.sector_count, new_start=new_start_lba,
                      new_count=new_sector_count, sector_size=sector_size,
                      fs=info, window=window, warnings=uyarilar)


def fs_resize_info_for(session, part: Partition) -> FsResizeInfo:
    if getattr(session.table, "scheme", "") == "none":
        # Tablosuz disk: bolumun sinirlari diskin kendisidir, yazilacak tablo yok
        return FsResizeInfo(kind="unsupported", fs_type=part.fs_type,
                            movable=False,
                            note=tr("Tablosuz disk: dosya sistemi tum diski kapliyor"))
    fs_info = session.detect_fs(part)
    kind = getattr(fs_info, "fs_type", "") or ""
    yerel = False
    try:
        # Yerel arac disk numarasiyla calisir; numara yoksa hic secilmez
        # (eskiden secilip sessizce tablo-yalniz yola dusuyordu, ADR 0084).
        number = getattr(session, "windows_disk_number", lambda: None)()
        yerel = (bool(session.is_physical) and number is not None
                 and _native_resize_available(session))
    except Exception:
        yerel = False
    return fs_resize_info(session.view(part), kind, native_ok=yerel)


def _native_resize_available(session) -> bool:
    from . import platform as plat
    return bool(getattr(plat, "native_resize_supported", lambda: False)())


# --------------------------------------------------------------------------
#  Uygulama
# --------------------------------------------------------------------------
def apply_resize(session, plan: ResizePlan,
                 progress: Optional[Callable[[str, int], None]] = None) -> Partition:
    """Plani uygular. Sira: kucultmede once FS, buyutmede once tablo."""
    def report(message: str, percent: int) -> None:
        if progress:
            progress(message, max(0, min(100, percent)))

    if session.table is None:
        raise ResizeError(tr("Bolum tablosu yok"))
    session._require_writable()
    part = session.table.get(plan.index)
    if (part.start_lba, part.sector_count) != (plan.old_start, plan.old_count):
        raise ResizeError(tr("Bolum plan hazirlandiktan sonra degismis; yenileyin"))
    if not plan.changed:
        return part
    if plan.fs.kind == "native":
        # Bu tur yalnizca isletim sisteminin araciyla uygulanir. Burada tablo
        # tek basina degisirse dosya sistemi eski boyutta kalir; kucultmede
        # bolumun disina tasar (ADR 0084).
        raise ResizeError(tr("Bu dosya sistemi yalnizca isletim sisteminin "
                             "kendi araciyla boyutlandirilabilir; bolum "
                             "tablosu tek basina degistirilmez"))

    # Tablonun yeni araligi kabul edecegi **hicbir sey yazilmadan** denetlenir.
    # Eskiden denetim 3. adimdaydi: dosya sistemi kucultulup tasindiktan
    # sonra tablo reddedince islem yarida kaliyordu.
    try:
        session.table.check_range(plan.new_start, plan.new_count,
                                  ignore_index=plan.index)
    except PartitionTableError as exc:
        raise ResizeError(tr("Bolum tablosu yazilamadi: {}", exc)) from exc

    session.close_filesystems()
    image = session.image

    # 1) kucultme: once dosya sistemi (eski yerinde)
    if plan.shrinks and plan.fs.kind in ("fat", "exfat", "ntfs", "ext"):
        report(tr("Dosya sistemi kucultuluyor..."), 5)
        # Gizli sektor alani bolumun **son** yerine gore yazilir: tasima
        # bundan sonra gelir ve dosya sistemi oraya varacaktir.
        _fs_resize(image, plan.old_start, plan.new_count, plan.fs.kind,
                   plan.new_start, span=plan.old_count,
                   progress=lambda m, p: report(m, 5 + int(0.75 * p)))

    # 2) tasima
    if plan.moves:
        need, free = move_space_needed(image, plan.old_start, plan.new_start,
                                       min(plan.old_count, plan.new_count))
        if free >= 0 and need > free:
            raise ResizeError(tr(
                "Goruntunun bulundugu yerde yeterli bos alan yok: tasima {} "
                "yeni alan gerektiriyor, {} bos. Hicbir sey yazilmadi.",
                human_size(need), human_size(free)))
        report(tr("Veri tasiniyor..."), 10)
        _move_data(image, plan.old_start, plan.new_start,
                   min(plan.old_count, plan.new_count), report)

    # 3) tablo guncellenir
    report(tr("Bolum tablosu yaziliyor..."), 85)
    old = (part.start_lba, part.sector_count)
    part.start_lba = plan.new_start
    part.sector_count = plan.new_count
    if part.logical:
        part.ebr_lba = 0          # EBR yeni baslangica gore yeniden hesaplanir
    try:
        session.table.check_range(plan.new_start, plan.new_count,
                                  ignore_index=plan.index)
        session.table.write()
    except (PartitionTableError, Exception) as exc:   # noqa: BLE001
        part.start_lba, part.sector_count = old
        raise ResizeError(tr("Bolum tablosu yazilamadi: {}", exc)) from exc

    # 4) buyutme: dosya sistemi yeni yerinde buyutulur
    if plan.grows and plan.fs.kind in ("fat", "exfat", "ntfs", "ext", "xfs"):
        report(tr("Dosya sistemi buyutuluyor..."), 90)
        try:
            _fs_resize(image, plan.new_start, plan.new_count, plan.fs.kind,
                       plan.new_start,
                       progress=lambda m, p: report(m, 88 + int(0.11 * p)))
        except ResizeError:
            raise
        except Exception as exc:   # noqa: BLE001
            raise ResizeError(tr("Dosya sistemi buyutulemedi: {}", exc)) from exc
    elif plan.moves and plan.fs.kind not in ("ext", "xfs"):
        # boyut degismediyse bile tasima sonrasi "gizli sektor" alani duzeltilir
        # (ext bolum konumunu tutmaz; ilk 1024 bayti onyukleyiciye aittir)
        report(tr("Onyukleme sektoru guncelleniyor..."), 92)
        _patch_partition_offset(image, plan.new_start, plan.new_count)

    report(tr("Yenileniyor..."), 96)
    session.reload()
    report(tr("Tamamlandi"), 100)
    return session.table.get(plan.index)


def _fs_resize(image: BlockDevice, start_lba: int, sector_count: int,
               kind: str, partition_offset: int, span: int = 0,
               progress=None) -> None:
    """Dosya sistemini `sector_count` sektore getirir.

    `span`: acilacak pencerenin sektor sayisi. Kucultmede bolum **henuz eski
    boyuttadir** ve NTFS'in sondaki yapilarina (yedek onyukleme sektoru,
    birimin sonuna dusmus meta veri) erisilmesi gerekir; pencere yeni boyutta
    acilirsa bu okuma "bolum sinirini asiyor" diye reddedilir.
    """
    view = PartitionView(image, start_lba, max(span, sector_count))
    if kind == "fat":
        fat_resize(view, sector_count)
        _patch_hidden_sectors(view, partition_offset)
    elif kind == "exfat":
        exfat_resize(view, sector_count, partition_offset)
    elif kind == "ntfs":
        ntfs_resize(view, sector_count, partition_offset=partition_offset,
                    progress=progress)
    elif kind == "ext":
        new_bytes = sector_count * view.sector_size
        try:
            if span and span > sector_count:
                ext_shrink(view, new_bytes, progress=progress)
            else:
                ext_grow(view, new_bytes, progress=progress)
        except ExtResizeError as exc:
            raise ResizeError(str(exc)) from exc
    elif kind == "xfs":
        if span and span > sector_count:
            raise ResizeError(tr("XFS kucultulemez"))
        try:
            xfs_grow(view, sector_count * view.sector_size, progress=progress)
        except XfsGrowError as exc:
            raise ResizeError(str(exc)) from exc


def _patch_hidden_sectors(view: BlockDevice, partition_offset: int) -> None:
    """FAT/NTFS BPB'sindeki 'gizli sektor' alanini (ofset 28) gunceller.

    FAT32'de yedek onyukleme sektoru (BPB ofset 50, genelde sektor 6) da
    guncellenir. Eskiden yalnizca asil sektor yaziliyordu; tasimadan sonra
    `fsck.fat` "differences between boot sector and its backup (29, 30)"
    diyordu (uzun testler, full profil, ADR 0087).
    """
    ss = view.sector_size
    boot = bytearray(view.read(0, ss))
    if boot[510:512] != b"\x55\xAA":
        return
    fat32 = (struct.unpack_from("<H", boot, 22)[0] == 0
             and boot[3:11] not in (b"NTFS    ", b"EXFAT   "))
    # Yedek sektor numarasi ve gizli sektor sayisi BPB sektorundedir
    # (BPB_BytsPerSec); aygit sektoru 512 iken -S 4096 FAT'te yedek
    # onyukleme 6*4096'dadir, 6*512'de degil.
    bps = struct.unpack_from("<H", boot, 11)[0]
    if bps not in (512, 1024, 2048, 4096):
        bps = ss
    hidden = _from_dev(partition_offset, bps, view) if bps != ss else partition_offset
    struct.pack_into("<I", boot, 28, hidden & 0xFFFFFFFF)
    view.write(0, bytes(boot))
    if not fat32:
        return
    backup = struct.unpack_from("<H", boot, 50)[0]
    reserved = struct.unpack_from("<H", boot, 14)[0]
    if 0 < backup < reserved:
        yedek = bytearray(view.read(backup * bps, ss))
        if yedek[510:512] == b"\x55\xAA" and yedek[3:11] == boot[3:11]:
            struct.pack_into("<I", yedek, 28, hidden & 0xFFFFFFFF)
            view.write(backup * bps, bytes(yedek))


def _patch_partition_offset(image: BlockDevice, start_lba: int,
                            sector_count: int) -> None:
    """Tasima sonrasi onyukleme sektorundeki bolum konumunu duzeltir.

    FAT ve NTFS icin BPB ofset 28 (gizli sektor), exFAT icin ofset 64
    (PartitionOffset) ve saglama. Alan yanlis kalirsa Windows birimi
    baglayamaz.
    """
    view = PartitionView(image, start_lba, sector_count)
    try:
        boot = bytes(view.read(0, 512))
    except Exception:   # noqa: BLE001
        return
    try:
        if struct.unpack_from("<H", view.read(1024 + 56, 2))[0] == 0xEF53:
            return            # ext: konum tutmaz; ilk sektor onyukleyicinindir
    except Exception:   # noqa: BLE001
        pass
    if boot[3:11] == b"EXFAT   ":
        try:
            fs = ExFatFS(view)
            _exfat_write_boot(view, fs, fs.volume_length, fs.cluster_count,
                              fs.fat_length, fs.cluster_heap_offset, start_lba)
        except Exception:   # noqa: BLE001
            pass
        return
    if boot[510:512] == b"\x55\xAA":
        _patch_hidden_sectors(view, start_lba)
        if boot[3:11] == b"NTFS    ":
            # NTFS'in yedek onyukleme kopyasi: birimin son BPB sektorunde
            # (toplam sektor x BPB sektoru). mkntfs -s 4096 birimde aygitin
            # son 512 baytinda degildir; eskiden oraya bakiliyor, yedek
            # guncellenmiyordu (ntfsfix: "alternate boot sector BAD").
            # Gizli sektor degeri asildan aynen kopyalanir (BPB biriminde).
            try:
                bps = struct.unpack_from("<H", boot, 11)[0]
                if bps not in (512, 1024, 2048, 4096):
                    bps = view.sector_size
                off = struct.unpack_from("<Q", boot, 0x28)[0] * bps
                hidden = struct.unpack_from("<I", view.read(0, 512), 28)[0]
                if off + bps <= sector_count * view.sector_size:
                    yedek = bytearray(view.read(off, bps))
                    if yedek[3:11] == b"NTFS    ":
                        struct.pack_into("<I", yedek, 28, hidden)
                        view.write(off, bytes(yedek))
            except Exception:   # noqa: BLE001
                pass


class MoveInterrupted(ResizeError):
    """Tasima yarida kaldi. `source_intact`: kaynak bolum henuz ezilmedi mi."""

    def __init__(self, message: str, moved_sectors: int, source_intact: bool):
        super().__init__(message)
        self.moved_sectors = moved_sectors
        self.source_intact = source_intact


def move_space_needed(image: BlockDevice, src_lba: int, dst_lba: int,
                      sector_count: int) -> Tuple[int, int]:
    """(gereken, bos) bayt — seyrek goruntu dosyasinda tasimanin dolduracagi
    delikler icin. Fiziksel disk, seyrek olmayan dosya, sanal disk kapsayicisi
    ya da olculemeyen durumda (0, -1). Tasima ayni icerigi tekrar yazmadigi
    icin gereken, kaynaktaki **ayrilmis** veri kadardir (en kotu durum)."""
    path = getattr(image, "path", "")
    if not path or type(image).__name__ != "DiskImage" or not os.path.isfile(path):
        return 0, -1
    from .platform import actual_size, allocated_in_range, free_space
    try:
        apparent = os.path.getsize(path)
    except OSError:
        return 0, -1
    if actual_size(path) >= apparent:
        return 0, -1                              # seyrek degil: yeni yer gerekmez
    ss = image.sector_size
    # Ust sinir: kaynaktaki her ayrilmis blok en fazla bir yeni blok ayirtir.
    # (Hedefle fark almak ortusmede yanlis kucuk sonuc veriyordu.)
    need = allocated_in_range(path, src_lba * ss, sector_count * ss)
    return need, free_space(path)


def _move_data(image: BlockDevice, src_lba: int, dst_lba: int,
               sector_count: int, report) -> int:
    """Sektor blogunu tasir; cakisma yonune gore sira secilir.

    * Hedefte ayni icerik zaten varsa (cogunlukla iki taraf da sifir/delik)
      **yazilmaz**: seyrek goruntu sismez, bos alan kopyalanmaz (ADR 0073 —
      eskiden bolumun tamami yaziliyordu; /dev doldu, tasima yarida kaldi).
    * Hata olursa `MoveInterrupted`: kac sektor tasindigi ve kaynak bolumun
      hala saglam olup olmadigi (ortusmede kaydirma miktarindan fazlasi
      kopyalandiysa kaynagin basi ezilmistir).
    Dondurulen: gercekten yazilan sektor sayisi.
    """
    if src_lba == dst_lba or sector_count <= 0:
        return 0
    ss = image.sector_size
    step = max(1, COPY_CHUNK // ss)
    total = sector_count
    shift = abs(dst_lba - src_lba)
    ileri = dst_lba < src_lba        # hedef solda ise bastan kopyalamak guvenli
    parcalar = [(p, min(step, total - p)) for p in range(0, total, step)]
    if not ileri:
        parcalar.reverse()           # hedef sagda: sondan kopyala
    tasinan = 0
    yazilan = 0
    try:
        for pos, n in parcalar:
            data = image.read_sectors(src_lba + pos, n)
            if image.read_sectors(dst_lba + pos, n) != data:
                image.write_sectors(dst_lba + pos, data)
                yazilan += n
            tasinan += n
            report(tr("Veri tasiniyor... {}", human_size(tasinan * ss)),
                   10 + int(70 * tasinan / total))
        f = getattr(image, "flush", None)
        if f:
            f()
    except Exception as exc:          # noqa: BLE001
        saglam = shift >= total or tasinan <= shift
        from . import diagnostics
        diagnostics.warn(f"tasima yarida kaldi: kaynak={src_lba} hedef={dst_lba} "
                         f"adet={total} tasinan={tasinan} kaynak_saglam={saglam} "
                         f"yon={'sol' if ileri else 'sag'} hata={exc!r}")
        if saglam:
            msg = tr("Veri tasinirken hata: {}. {} / {} kopyalanmisti; kaynak bolum "
                     "henuz ezilmedi, bolum eski yerinde saglam ve tablo "
                     "degismedi.", exc, human_size(tasinan * ss), human_size(total * ss))
        else:
            msg = tr("Veri tasinirken hata: {}. {} / {} kopyalanmisti ve kaynak "
                     "bolumun basi ezildi: bolum su an BOZUK (ne eski ne yeni "
                     "yerinde tam). Tasima: sektor {} -> {}, {} sektor, {} sektor "
                     "tamamlandi (tanilama gunlugunde). Yedekten geri yukleyin ya "
                     "da bos alan acip tasimayi bu sayilarla tamamlatin.",
                     exc, human_size(tasinan * ss), human_size(total * ss),
                     src_lba, dst_lba, total, tasinan)
        raise MoveInterrupted(msg, tasinan, saglam) from exc
    return yazilan
