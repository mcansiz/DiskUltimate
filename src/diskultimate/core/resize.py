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
            raise ResizeError("Mantiksal bolumun genisletilmis bolumu bulunamadi")
        alt = kapsayici.start_lba + table.align_sectors
        ust = kapsayici.end_lba
        komsular = [p for p in table.partitions if p.logical and p is not part]
    else:
        alt = table.first_usable_lba()
        ust = table.last_usable_lba()
        komsular = [p for p in table.partitions
                    if not p.logical and p is not part]

    bas = alt
    son = ust
    for p in sorted(komsular, key=lambda x: x.start_lba):
        if p.end_lba < part.start_lba:
            bas = max(bas, p.end_lba + 1)
        elif p.start_lba > part.end_lba:
            son = min(son, p.start_lba - 1)
        else:
            raise ResizeError(f"{p.index} numarali bolum ile cakisma var")
    if part.logical:
        # her mantiksal bolumun onunde kendi EBR'si icin bir hizalama birimi durur
        bas = max(bas, alt)
    bas = table.align_up(bas)
    if bas > part.start_lba:
        bas = part.start_lba          # mevcut yerlesim hizasiz olabilir
    return ResizeWindow(bas, son, table.sector_size)


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
            parcalar.append(f"en cok {human_size(self.max_sectors * 512)}")
        return ", ".join(parcalar)


def fs_resize_info(view: BlockDevice, fs_type: str,
                   native_ok: bool = False) -> FsResizeInfo:
    """Bolumdeki dosya sistemini inceleyip boyut sinirlarini cikarir."""
    tur = (fs_type or "").lower()
    try:
        if tur.startswith("fat"):
            return _fat_info(view)
        if tur == "exfat":
            return _exfat_info(view)
    except (FatError, ExFatError, Exception) as exc:   # noqa: BLE001
        return FsResizeInfo(
            kind="unsupported", fs_type=fs_type, movable=False,
            note=f"Dosya sistemi okunamadi ({exc}); boyutlandirma guvenli degil")

    if tur in ("", "raw", "bos", "unknown"):
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
    _, ust_kume = _fat_type_bounds(fs.fat_type)
    alt, ust = fs.total_sectors, fs.total_sectors
    adim = max(1, fs.total_sectors)
    while True:                       # once ust siniri kabaca bul
        _, kume = _fat_layout_for(ust + adim, fs)
        if kume > ust_kume or (ust + adim) > (1 << 32) - 1:
            break
        ust += adim
        adim *= 2
    alt, yuksek = ust, ust + adim
    while alt < yuksek:               # sonra ikili arama ile daralt
        orta = (alt + yuksek + 1) // 2
        _, kume = _fat_layout_for(orta, fs)
        if kume <= ust_kume and orta <= (1 << 32) - 1:
            alt = orta
        else:
            yuksek = orta - 1
    return alt


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
    alt_kume, _ = _fat_type_bounds(fs.fat_type)
    tepe = _fat_highest_used(fs)
    gerekli_kume = max(tepe - 1, alt_kume)
    # kucultmede FAT tablosunun yeri degismez: asgari boyut mevcut yerlesimden
    asgari = fs.first_data_sector + gerekli_kume * fs.sectors_per_cluster
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
    alt_kume, ust_kume = _fat_type_bounds(fs.fat_type)
    tepe = _fat_highest_used(fs)

    if new_sector_count <= fs.total_sectors:
        # --- kucultme / ayni boyut: yerlesim korunur -------------------------
        veri_sektor = new_sector_count - fs.first_data_sector
        if veri_sektor <= 0:
            raise ResizeError("Yeni boyut FAT ust verisinden kucuk")
        yeni_kume = min(veri_sektor // spc, ust_kume)
        if yeni_kume < alt_kume:
            raise ResizeError(
                f"Yeni boyut FAT{fs.fat_type} icin cok kucuk "
                f"(en az {alt_kume} kume gerekir)")
        if yeni_kume < tepe - 1:
            raise ResizeError(
                "Kucultme veri kaybina yol acar: dosyalar yeni sinirin "
                "otesinde. Once dosyalari tasiyin.")
        toplam = fs.first_data_sector + yeni_kume * spc
        _fat_write_bpb(view, fs, toplam, fs.fat_size)
        taze = FatFS(view)
        for c in range(yeni_kume + 2, fs.cluster_count + 2):
            taze.set_fat(c, 0)
        taze._free_count = None
        taze.flush()
        taze._update_fsinfo()      # FSInfo'daki bos kume sayaci tazelenir
        return

    # --- buyutme --------------------------------------------------------------
    yeni_fat, yeni_kume = _fat_layout_for(new_sector_count, fs)
    if yeni_kume > ust_kume:
        # tip sinirini asmamak icin kume sayisi kirpilir
        yeni_kume = ust_kume
        yeni_fat, _ = _fat_layout_for(new_sector_count, fs)
    if yeni_kume < alt_kume:
        raise ResizeError(f"FAT{fs.fat_type} icin gecersiz kume sayisi")
    if yeni_fat < fs.fat_size:
        yeni_fat = fs.fat_size            # tablo asla kucultulmez
        veri = new_sector_count - (fs.reserved_sectors
                                   + fs.num_fats * yeni_fat
                                   + fs.root_dir_sectors)
        yeni_kume = min(veri // spc, ust_kume)

    kaydirma = fs.num_fats * (yeni_fat - fs.fat_size)
    yeni_ilk_veri = (fs.reserved_sectors + fs.num_fats * yeni_fat
                     + fs.root_dir_sectors)
    toplam = yeni_ilk_veri + yeni_kume * spc
    if toplam > new_sector_count:
        raise ResizeError("Hesaplanan yerlesim bolume sigmiyor")

    if kaydirma:
        # kok dizin + kullanilan veri bolgesi ileri kaydirilir (sondan basa)
        bolge_bas = fs.reserved_sectors + fs.num_fats * fs.fat_size
        kullanilan = fs.root_dir_sectors + max(0, tepe - 1) * spc
        _shift_forward(view, bolge_bas, kaydirma, kullanilan, bps)

    # FAT kopyalari yeni boyutta yeniden yazilir (kume numaralari degismez)
    eski_fat = view.read(fs.reserved_sectors * bps, fs.fat_size * bps)
    tampon = bytearray(yeni_fat * bps)
    tampon[:len(eski_fat)] = eski_fat
    for i in range(fs.num_fats):
        view.write((fs.reserved_sectors + i * yeni_fat) * bps, bytes(tampon))

    _fat_write_bpb(view, fs, toplam, yeni_fat)
    taze = FatFS(view)
    taze._free_count = None
    taze._update_fsinfo()          # FSInfo'daki bos kume sayaci tazelenir
    flush = getattr(view, "flush", None)
    if flush:
        flush()


def _shift_forward(view: BlockDevice, start_lba: int, shift: int,
                   sector_count: int, bps: int) -> None:
    """Sektor blogunu `shift` sektor ileri kaydirir (sondan basa kopyalar)."""
    adim = max(1, COPY_CHUNK // bps)
    kalan = sector_count
    while kalan > 0:
        n = min(adim, kalan)
        kalan -= n
        veri = view.read((start_lba + kalan) * bps, n * bps)
        view.write((start_lba + kalan + shift) * bps, veri)


def _fat_write_bpb(view: BlockDevice, fs: FatFS, toplam: int,
                   fat_size: int) -> None:
    """BPB'deki toplam sektor ve FAT boyutu alanlarini gunceller."""
    bps = fs.bytes_per_sector
    boot = bytearray(view.read(0, bps))
    if toplam < 0x10000 and fs.fat_type != 32:
        struct.pack_into("<H", boot, 19, toplam)
        struct.pack_into("<I", boot, 32, 0)
    else:
        struct.pack_into("<H", boot, 19, 0)
        struct.pack_into("<I", boot, 32, toplam)
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


def _exfat_layout_for(toplam: int, fs: ExFatFS) -> Tuple[int, int, int]:
    """Verilen toplam sektor icin (FAT uzunlugu, heap ofseti, kume sayisi).

    `ExFatFS.format` ile ayni yinelemeli hesap; fat_offset ve kume boyutu
    korunur.
    """
    bps, spc = fs.bytes_per_sector, fs.sectors_per_cluster
    fat_offset = fs.fat_offset
    fat_length = fs.fat_length
    kume = fs.cluster_count
    for _ in range(64):
        heap = fat_offset + fat_length
        artik = heap % spc
        if artik:
            heap += spc - artik
        kalan = toplam - heap
        if kalan <= 0:
            raise ResizeError("Yeni boyut exFAT yerlesimi icin cok kucuk")
        yeni_kume = kalan // spc
        yeni_fat = ((yeni_kume + 2) * 4 + bps - 1) // bps
        if yeni_fat == fat_length and yeni_kume == kume:
            break
        fat_length, kume = yeni_fat, yeni_kume
    heap = fat_offset + fat_length
    artik = heap % spc
    if artik:
        heap += spc - artik
    return fat_length, heap, (toplam - heap) // spc


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
            raise ResizeError("Yeni boyut exFAT ust verisinden kucuk")
        yeni_kume = min(kullanilabilir // spc,
                        (fs.fat_length * fs.bytes_per_sector) // 4 - 2)
        if yeni_kume < 1:
            raise ResizeError("Yeni boyut exFAT icin cok kucuk")
        if yeni_kume < tepe - 1:
            raise ResizeError(
                "Kucultme veri kaybina yol acar: kumeler yeni sinirin otesinde")
        _exfat_set_bitmap_length(fs, (yeni_kume + 7) // 8, fs.cluster_count,
                                 yeni_kume)
        _exfat_write_boot(view, fs, new_sector_count, yeni_kume,
                          fs.fat_length, fs.cluster_heap_offset,
                          partition_offset)
        return

    # --- buyutme ------------------------------------------------------------
    bps = fs.bytes_per_sector
    yeni_fat, yeni_heap, yeni_kume = _exfat_layout_for(new_sector_count, fs)
    if yeni_fat < fs.fat_length:            # FAT bolgesi asla kucultulmez
        yeni_fat, yeni_heap = fs.fat_length, fs.cluster_heap_offset
        yeni_kume = (new_sector_count - yeni_heap) // spc
    kaydirma = yeni_heap - fs.cluster_heap_offset

    if kaydirma:
        _shift_forward(view, fs.cluster_heap_offset, kaydirma,
                       max(0, tepe - 1) * spc, bps)

    eski_fat = view.read(fs.fat_offset * bps, fs.fat_length * bps)
    tampon = bytearray(yeni_fat * bps)
    tampon[:len(eski_fat)] = eski_fat
    view.write(fs.fat_offset * bps, bytes(tampon))

    _exfat_write_boot(view, fs, new_sector_count, yeni_kume, yeni_fat,
                      yeni_heap, partition_offset)
    _exfat_extend_bitmap(view, fs.cluster_count)


def _exfat_set_bitmap_length(fs: ExFatFS, yeni_uzunluk: int,
                             eski_kume: int, yeni_kume: int) -> None:
    """Bitmap'in gecerli uzunlugunu ve sinir disi bitlerini duzeltir."""
    bm = fs._load_bitmap()
    if yeni_uzunluk > len(bm):
        bm.extend(b"\x00" * (yeni_uzunluk - len(bm)))
    fs._bitmap = bm[:yeni_uzunluk]
    artik = yeni_kume % 8
    if artik and fs._bitmap:
        fs._bitmap[-1] &= (1 << artik) - 1
    fs._bitmap_dirty = True
    _exfat_patch_bitmap_entry(fs, fs.bitmap_cluster, yeni_uzunluk)
    fs.bitmap_length = yeni_uzunluk
    fs.cluster_count = yeni_kume
    fs.flush()


def _exfat_patch_bitmap_entry(fs: ExFatFS, ilk_kume: int,
                              uzunluk: int) -> None:
    """Kok dizindeki E_BITMAP girisinin konum ve uzunluk alanlarini yazar."""
    veri = fs._read_chain_data(fs.root_cluster)
    for off in range(0, len(veri) - 31, 32):
        if veri[off] == 0x00:
            break
        if veri[off] == 0x81:                          # E_BITMAP
            struct.pack_into("<I", veri, off + 20, ilk_kume)
            struct.pack_into("<Q", veri, off + 24, uzunluk)
            fs._write_dir(fs.root_cluster, veri)
            return


def _exfat_free_run(icerik: bytearray, kume_sayisi: int,
                    adet: int) -> Optional[int]:
    """Bitmap icerigine gore `adet` ardisik bos kumenin ilkini bulur."""
    seri = 0
    for c in range(2, kume_sayisi + 2):
        i = c - 2
        bayt = i >> 3
        dolu = bool(icerik[bayt] & (1 << (i & 7))) if bayt < len(icerik) else False
        if dolu:
            seri = 0
            continue
        seri += 1
        if seri == adet:
            return c - adet + 1
    return None


def _exfat_extend_bitmap(view: BlockDevice, eski_kume: int) -> None:
    """Buyutme sonrasi ayirma bitmap'ini yeni kume sayisina uyarlar.

    Bitmap mevcut kumelerine sigmiyorsa **en dusuk** bos ardisik alana tasinir.
    Yuksek kumelere tasimak, birimin sonradan kucultulmesini engelledigi icin
    bilerek tercih edilmez.
    """
    fs = ExFatFS(view)                     # yeni yerlesimle yeniden baglanir
    kb = fs.cluster_bytes
    gereken = (fs.cluster_count + 7) // 8
    eski_uzunluk = fs.bitmap_length
    eski_kumeler = fs.chain(fs.bitmap_cluster,
                            max(1, (eski_uzunluk + kb - 1) // kb))

    icerik = bytearray()
    for c in eski_kumeler:
        icerik += view.read(fs.cluster_offset(c), kb)
    icerik = icerik[:eski_uzunluk]
    if len(icerik) < gereken:
        icerik.extend(b"\x00" * (gereken - len(icerik)))

    kapasite = len(eski_kumeler) * kb
    if gereken <= kapasite:
        hedef = eski_kumeler
        ilk = fs.bitmap_cluster
    else:
        adet = (gereken + kb - 1) // kb
        for c in eski_kumeler:             # eski kumeler once serbest sayilir
            i = c - 2
            icerik[i >> 3] &= ~(1 << (i & 7)) & 0xFF
        ilk = _exfat_free_run(icerik, fs.cluster_count, adet)
        if ilk is None:
            raise ResizeError(
                "Ayirma bitmap'i icin yeterli ardisik bos alan bulunamadi")
        hedef = list(range(ilk, ilk + adet))
        for c in hedef:
            i = c - 2
            icerik[i >> 3] |= (1 << (i & 7))

    dolgu = bytes(icerik).ljust(len(hedef) * kb, b"\x00")
    for i, c in enumerate(hedef):
        view.write(fs.cluster_offset(c), dolgu[i * kb:(i + 1) * kb])

    if ilk != fs.bitmap_cluster:
        eskiler = set(eski_kumeler)
        for i, c in enumerate(hedef):      # yeni zincir
            fs.set_fat(c, EXFAT_EOC if i == len(hedef) - 1 else c + 1)
            eskiler.discard(c)
        for c in eskiler:                  # artik kullanilmayan eski zincir
            fs.set_fat(c, 0)
    _exfat_patch_bitmap_entry(fs, ilk, gereken)
    fs.bitmap_cluster = ilk
    fs.bitmap_length = gereken
    fs._bitmap = None
    fs._update_percent()
    f = getattr(view, "flush", None)
    if f:
        f()


def _exfat_write_boot(view: BlockDevice, fs: ExFatFS, toplam_sektor: int,
                      kume_sayisi: int, fat_length: int, heap_offset: int,
                      partition_offset: Optional[int]) -> None:
    """Onyukleme bolgesini yeni boyutlarla yeniden yazar (ana + yedek)."""
    bps = fs.bytes_per_sector
    bolge = bytearray(view.read(0, 12 * bps))
    if bolge[3:11] != b"EXFAT   ":
        raise ResizeError("exFAT onyukleme bolgesi taninmadi")
    if partition_offset is not None:
        struct.pack_into("<Q", bolge, 64, partition_offset)
    struct.pack_into("<Q", bolge, 72, toplam_sektor)
    struct.pack_into("<I", bolge, 84, fat_length)
    struct.pack_into("<I", bolge, 88, heap_offset)
    struct.pack_into("<I", bolge, 92, kume_sayisi)
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
            satir.append(f"{human_size(fark)} {yon} tasinacak "
                         f"({human_size(self.move_bytes)} veri kopyalanir)")
        return "; ".join(satir)


def plan_resize(session, index: int, new_start_lba: int,
                new_sector_count: int) -> ResizePlan:
    """Istenen yerlesimi dogrular ve yapilacak islerin dokumunu dondurur."""
    if session.table is None:
        raise ResizeError("Bolum tablosu yok")
    part = session.table.get(index)
    pencere = window_for(session.table, part)
    sector_size = session.table.sector_size

    if new_sector_count <= 0:
        raise ResizeError("Bolum boyutu sifir olamaz")
    if new_start_lba < pencere.start_lba:
        raise ResizeError(
            f"Baslangic kapsayici alanin disinda (en erken LBA {pencere.start_lba})")
    if new_start_lba + new_sector_count - 1 > pencere.end_lba:
        raise ResizeError(
            f"Bolum kapsayici alani asiyor (en gec LBA {pencere.end_lba})")

    bilgi = fs_resize_info_for(session, part)
    uyarilar: List[str] = []

    if new_sector_count < part.sector_count:
        if not bilgi.resizable:
            raise ResizeError(
                f"Kucultme yapilamaz — {bilgi.note}")
        if bilgi.min_sectors > new_sector_count:
            raise ResizeError(
                f"Bu dosya sistemi {human_size(bilgi.min_sectors * sector_size)} "
                f"altina inemez (veri kaybi olurdu)")
    if new_sector_count > part.sector_count:
        if bilgi.max_sectors and new_sector_count > bilgi.max_sectors:
            uyarilar.append(
                f"Dosya sistemi en fazla "
                f"{human_size(bilgi.max_sectors * sector_size)} olabilir; "
                f"kalan alan bolum icinde **kullanilmadan** kalir")
        elif bilgi.kind == "unsupported":
            uyarilar.append(
                "Bolum buyutuluyor ama dosya sistemi buyutulemiyor; "
                "eklenen alan kullanilamaz")
    if new_start_lba != part.start_lba:
        if not bilgi.movable:
            raise ResizeError(
                f"Bu dosya sistemi tasinamaz — {bilgi.note}")
        uyarilar.append(
            f"{human_size(min(part.sector_count, new_sector_count) * sector_size)} "
            f"veri kopyalanacak; islem yarida kesilirse bolum bozulur")
    if session.is_physical:
        uyarilar.append("Fiziksel disk: islem oncesi yedek alin")

    return ResizePlan(index=index, old_start=part.start_lba,
                      old_count=part.sector_count, new_start=new_start_lba,
                      new_count=new_sector_count, sector_size=sector_size,
                      fs=bilgi, window=pencere, warnings=uyarilar)


def fs_resize_info_for(session, part: Partition) -> FsResizeInfo:
    bilgi_fs = session.detect_fs(part)
    tur = getattr(bilgi_fs, "fs_type", "") or ""
    yerel = False
    try:
        yerel = bool(session.is_physical) and _native_resize_available(session)
    except Exception:
        yerel = False
    return fs_resize_info(session.view(part), tur, native_ok=yerel)


def _native_resize_available(session) -> bool:
    from . import platform as plat
    return bool(getattr(plat, "native_resize_supported", lambda: False)())


# --------------------------------------------------------------------------
#  Uygulama
# --------------------------------------------------------------------------
def apply_resize(session, plan: ResizePlan,
                 progress: Optional[Callable[[str, int], None]] = None) -> Partition:
    """Plani uygular. Sira: kucultmede once FS, buyutmede once tablo."""
    def bildir(mesaj: str, yuzde: int) -> None:
        if progress:
            progress(mesaj, max(0, min(100, yuzde)))

    if session.table is None:
        raise ResizeError("Bolum tablosu yok")
    session._require_writable()
    part = session.table.get(plan.index)
    if (part.start_lba, part.sector_count) != (plan.old_start, plan.old_count):
        raise ResizeError("Bolum plan hazirlandiktan sonra degismis; yenileyin")
    if not plan.changed:
        return part

    session.close_filesystems()
    image = session.image

    # 1) kucultme: once dosya sistemi (eski yerinde)
    if plan.shrinks and plan.fs.kind in ("fat", "exfat"):
        bildir("Dosya sistemi kucultuluyor...", 5)
        _fs_resize(image, plan.old_start, plan.new_count, plan.fs.kind,
                   plan.new_start)

    # 2) tasima
    if plan.moves:
        bildir("Veri tasiniyor...", 10)
        _move_data(image, plan.old_start, plan.new_start,
                   min(plan.old_count, plan.new_count), bildir)

    # 3) tablo guncellenir
    bildir("Bolum tablosu yaziliyor...", 85)
    eski = (part.start_lba, part.sector_count)
    part.start_lba = plan.new_start
    part.sector_count = plan.new_count
    if part.logical:
        part.ebr_lba = 0          # EBR yeni baslangica gore yeniden hesaplanir
    try:
        session.table.check_range(plan.new_start, plan.new_count,
                                  ignore_index=plan.index)
        session.table.write()
    except (PartitionTableError, Exception) as exc:   # noqa: BLE001
        part.start_lba, part.sector_count = eski
        raise ResizeError(f"Bolum tablosu yazilamadi: {exc}") from exc

    # 4) buyutme: dosya sistemi yeni yerinde buyutulur
    if plan.grows and plan.fs.kind in ("fat", "exfat"):
        bildir("Dosya sistemi buyutuluyor...", 90)
        try:
            _fs_resize(image, plan.new_start, plan.new_count, plan.fs.kind,
                       plan.new_start)
        except ResizeError:
            raise
        except Exception as exc:   # noqa: BLE001
            raise ResizeError(f"Dosya sistemi buyutulemedi: {exc}") from exc
    elif plan.moves:
        # boyut degismediyse bile tasima sonrasi "gizli sektor" alani duzeltilir
        bildir("Onyukleme sektoru guncelleniyor...", 92)
        _patch_partition_offset(image, plan.new_start, plan.new_count)

    bildir("Yenileniyor...", 96)
    session.reload()
    bildir("Tamamlandi", 100)
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
               sector_count: int, bildir) -> None:
    """Sektor blogunu tasir; cakisma yonune gore sirasi secilir."""
    if src_lba == dst_lba or sector_count <= 0:
        return
    ss = image.sector_size
    adim = max(1, COPY_CHUNK // ss)
    toplam = sector_count
    ileri = dst_lba < src_lba        # hedef solda ise bastan kopyalamak guvenli
    tasinan = 0
    if ileri:
        pos = 0
        while pos < toplam:
            n = min(adim, toplam - pos)
            image.write_sectors(dst_lba + pos, image.read_sectors(src_lba + pos, n))
            pos += n
            tasinan += n
            bildir(f"Veri tasiniyor... {human_size(tasinan * ss)}",
                   10 + int(70 * tasinan / toplam))
    else:
        pos = toplam
        while pos > 0:
            n = min(adim, pos)
            pos -= n
            image.write_sectors(dst_lba + pos, image.read_sectors(src_lba + pos, n))
            tasinan += n
            bildir(f"Veri tasiniyor... {human_size(tasinan * ss)}",
                   10 + int(70 * tasinan / toplam))
    f = getattr(image, "flush", None)
    if f:
        f()
