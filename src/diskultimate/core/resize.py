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

Su an gercek dosya sistemi boyutlandirmasi FAT12/16/32 ve exFAT icin saf
Python'da yapilir. NTFS ve ext icin Windows'un kendi araci denenir; o da
yoksa islem reddedilir (bkz. `FsResizeInfo.note`).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from .exfat import EOC as EXFAT_EOC
from .exfat import ExFatError, ExFatFS, boot_checksum
from .fat import FatError, FatFS
from .image import BlockDevice, PartitionView
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
    for p in sorted(komsular, key=lambda x: x.start_lba):
        if p.end_lba < part.start_lba:
            bas = max(bas, p.end_lba + 1)
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
    kind: str = "raw"             # 'fat' | 'exfat' | 'raw' | 'native' | 'unsupported'
    fs_type: str = ""
    min_sectors: int = 1          # verinin gerektirdigi asgari
    max_sectors: int = 0          # 0 = dosya sistemi tarafindan sinirsiz
    movable: bool = True          # baslangic degistirilebilir mi
    note: str = ""

    @property
    def resizable(self) -> bool:
        return self.kind in ("fat", "exfat", "raw", "native")

    def limit_note(self) -> str:
        parcalar = []
        if self.min_sectors > 1:
            parcalar.append(f"en az {human_size(self.min_sectors * 512)}")
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
    except (FatError, ExFatError, Exception) as exc:   # noqa: BLE001
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
    tmp2 = (256 * spc) + num_fats
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
    """(asgari, azami) kume sayisi — tip siniri."""
    return _fat_type_bounds(fs.fat_type)


def _fat_highest_used(fs: FatFS) -> int:
    """Kullanilan en yuksek kume numarasi (hicbiri yoksa 1)."""
    for c in range(fs.cluster_count + 1, 1, -1):
        if fs.get_fat(c):
            return c
    return 1


def _fat_info(view: BlockDevice) -> FsResizeInfo:
    fs = FatFS(view)
    lower_cluster, _ = _fat_type_bounds(fs.fat_type)
    tepe = _fat_highest_used(fs)
    needed_clusters = max(tepe - 1, lower_cluster)
    # kucultmede FAT tablosunun yeri degismez: asgari boyut mevcut yerlesimden
    asgari = fs.first_data_sector + needed_clusters * fs.sectors_per_cluster
    azami = _fat_max_total(fs)
    not_ = ""
    if azami <= fs.total_sectors:
        not_ = f"FAT{fs.fat_type} kume siniri doldu; bu bolum buyutulemez"
    return FsResizeInfo(kind="fat", fs_type=fs.fs_type_name,
                        min_sectors=asgari, max_sectors=azami,
                        movable=True, note=not_)


def fat_resize(view: BlockDevice, new_sector_count: int) -> None:
    """FAT biriminin toplam sektor sayisini degistirir.

    Kucultmede FAT tablosu oldugu gibi birakilir (fazla tablo zararsizdir),
    bu yuzden **veri kopyalanmaz**. Buyutmede yeni kume sayisi daha buyuk bir
    FAT tablosu gerektirebilir; tablo buyudugunde kok dizin ve veri bolgesi
    tumuyle `num_fats * fark` sektor **ileri kaydirilir**. Kume numaralari
    degismedigi icin FAT icerigi oldugu gibi tasinir.
    """
    fs = FatFS(view)
    bps = fs.bytes_per_sector
    spc = fs.sectors_per_cluster
    lower_cluster, upper_cluster = _fat_type_bounds(fs.fat_type)
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

    # FAT kopyalari yeni boyutta yeniden yazilir (kume numaralari degismez)
    old_fat = view.read(fs.reserved_sectors * bps, fs.fat_size * bps)
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
    """Sektor blogunu `shift` sektor ileri kaydirir (sondan basa kopyalar)."""
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
    tepe = _exfat_highest_used(fs)
    spc = fs.sectors_per_cluster
    asgari = fs.cluster_heap_offset + max(1, tepe - 1) * spc
    # buyutmede FAT bolgesi ve bitmap yeniden yerlestirilir; sinir 32 bitlik
    # kume numarasi ve 64 bitlik VolumeLength alanidir
    azami = min((1 << 32) - 1, fs.cluster_heap_offset + (0xFFFFFFF5 - 2) * spc)
    return FsResizeInfo(kind="exfat", fs_type="exFAT",
                        min_sectors=asgari, max_sectors=azami, movable=True)


def exfat_resize(view: BlockDevice, new_sector_count: int,
                 partition_offset: Optional[int] = None) -> None:
    """exFAT biriminin boyutunu degistirir.

    Kucultmede yerlesim korunur; yalnizca VolumeLength/ClusterCount ve bitmap
    uzunlugu duser. Buyutmede FAT bolgesi gerektikce buyur, kume yigini
    ileri kaydirilir ve gerekirse ayirma bitmap'i yeni kumelere tasinir.
    """
    fs = ExFatFS(view)
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
    if new_fat < fs.fat_length:            # FAT bolgesi asla kucultulmez
        new_fat, new_heap = fs.fat_length, fs.cluster_heap_offset
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
    """Bitmap'in gecerli uzunlugunu ve sinir disi bitlerini duzeltir."""
    bm = fs._load_bitmap()
    if new_length > len(bm):
        bm.extend(b"\x00" * (new_length - len(bm)))
    fs._bitmap = bm[:new_length]
    artik = new_clusters % 8
    if artik and fs._bitmap:
        fs._bitmap[-1] &= (1 << artik) - 1
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

    if first != fs.bitmap_cluster:
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
        struct.pack_into("<Q", bolge, 64, partition_offset)
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
        satir = [f"Bolum {self.index}: "
                 f"{human_size(self.old_count * self.sector_size)} -> "
                 f"{human_size(self.new_count * self.sector_size)}"]
        if self.moves:
            yon = "ileri" if self.new_start > self.old_start else "geri"
            fark = abs(self.new_start - self.old_start) * self.sector_size
            satir.append(tr("{} {} tasinacak ({} veri kopyalanir)",
                            human_size(fark), yon,
                            human_size(self.move_bytes)))
        return "; ".join(satir)


def plan_resize(session, index: int, new_start_lba: int,
                new_sector_count: int) -> ResizePlan:
    """Istenen yerlesimi dogrular ve yapilacak islerin dokumunu dondurur."""
    if session.table is None:
        raise ResizeError(tr("Bolum tablosu yok"))
    part = session.table.get(index)
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

    info = fs_resize_info_for(session, part)
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
    fs_info = session.detect_fs(part)
    kind = getattr(fs_info, "fs_type", "") or ""
    yerel = False
    try:
        yerel = bool(session.is_physical) and _native_resize_available(session)
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

    session.close_filesystems()
    image = session.image

    # 1) kucultme: once dosya sistemi (eski yerinde)
    if plan.shrinks and plan.fs.kind in ("fat", "exfat"):
        report(tr("Dosya sistemi kucultuluyor..."), 5)
        _fs_resize(image, plan.old_start, plan.new_count, plan.fs.kind,
                   plan.new_start)

    # 2) tasima
    if plan.moves:
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
    if plan.grows and plan.fs.kind in ("fat", "exfat"):
        report(tr("Dosya sistemi buyutuluyor..."), 90)
        try:
            _fs_resize(image, plan.new_start, plan.new_count, plan.fs.kind,
                       plan.new_start)
        except ResizeError:
            raise
        except Exception as exc:   # noqa: BLE001
            raise ResizeError(tr("Dosya sistemi buyutulemedi: {}", exc)) from exc
    elif plan.moves:
        # boyut degismediyse bile tasima sonrasi "gizli sektor" alani duzeltilir
        report(tr("Onyukleme sektoru guncelleniyor..."), 92)
        _patch_partition_offset(image, plan.new_start, plan.new_count)

    report(tr("Yenileniyor..."), 96)
    session.reload()
    report(tr("Tamamlandi"), 100)
    return session.table.get(plan.index)


def _fs_resize(image: BlockDevice, start_lba: int, sector_count: int,
               kind: str, partition_offset: int) -> None:
    view = PartitionView(image, start_lba, sector_count)
    if kind == "fat":
        fat_resize(view, sector_count)
        _patch_hidden_sectors(view, partition_offset)
    elif kind == "exfat":
        exfat_resize(view, sector_count, partition_offset)


def _patch_hidden_sectors(view: BlockDevice, partition_offset: int) -> None:
    """FAT/NTFS BPB'sindeki 'gizli sektor' alanini (ofset 28) gunceller."""
    boot = bytearray(view.read(0, 512))
    if boot[510:512] != b"\x55\xAA":
        return
    struct.pack_into("<I", boot, 28, partition_offset & 0xFFFFFFFF)
    view.write(0, bytes(boot))


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
            # NTFS'in son sektordeki yedek onyukleme kopyasi
            try:
                yedek = bytearray(view.read((sector_count - 1) * view.sector_size,
                                            view.sector_size))
                if yedek[3:11] == b"NTFS    ":
                    struct.pack_into("<I", yedek, 28, start_lba & 0xFFFFFFFF)
                    view.write((sector_count - 1) * view.sector_size, bytes(yedek))
            except Exception:   # noqa: BLE001
                pass


def _move_data(image: BlockDevice, src_lba: int, dst_lba: int,
               sector_count: int, report) -> None:
    """Sektor blogunu tasir; cakisma yonune gore sirasi secilir."""
    if src_lba == dst_lba or sector_count <= 0:
        return
    ss = image.sector_size
    step = max(1, COPY_CHUNK // ss)
    total = sector_count
    ileri = dst_lba < src_lba        # hedef solda ise bastan kopyalamak guvenli
    tasinan = 0
    if ileri:
        pos = 0
        while pos < total:
            n = min(step, total - pos)
            image.write_sectors(dst_lba + pos, image.read_sectors(src_lba + pos, n))
            pos += n
            tasinan += n
            report(tr("Veri tasiniyor... {}", human_size(tasinan * ss)),
                   10 + int(70 * tasinan / total))
    else:
        pos = total
        while pos > 0:
            n = min(step, pos)
            pos -= n
            image.write_sectors(dst_lba + pos, image.read_sectors(src_lba + pos, n))
            tasinan += n
            report(tr("Veri tasiniyor... {}", human_size(tasinan * ss)),
                   10 + int(70 * tasinan / total))
    f = getattr(image, "flush", None)
    if f:
        f()
