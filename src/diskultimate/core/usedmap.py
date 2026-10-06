"""Yedekleme icin kullanilan alan haritasi (ADR 0092).

DiskGenius gibi: bolum yedeginde dosya sisteminin **dolu** kumeleri okunur,
bos alan ve bolumlenmemis buyuk alan okunmaz. 64 GB'lik bir SD kartta 500 MB
veri varsa yedek 64 GB degil ~500 MB okur.

Donen deger, aygita gore (bayt ofseti, uzunluk) araliklaridir. `None`
"bilinmiyor" demektir ve **tamami yedeklenir** — taninmayan dosya sistemi,
okunamayan bitmap ya da guvenilmez durum (islenmemis ext gunlugu) asla
"bos" sayilmaz.

Dosya sistemi haritasi kaba taneciklidir: bitmap ~1 MiB'lik gruplar halinde
taranir, grupta tek bir dolu birim varsa grubun tamami alinir. Yedek zaten
1 MiB'lik bloklarla calistigi icin daha ince harita kazanc getirmez; buna
karsilik Python dongusu birim basina degil grup basina doner (64 GB'ta
~65 bin tur).

Her zaman alinanlar:
  * her bolumun ilk ve son `EDGE` bayti — onyukleme sektoru, NTFS yedek
    onyukleme sektoru (bolumun son sektoru), ustbloklar;
  * diskin ilk bolumden onceki alani — ham onyukleyiciler (U-Boot, SPL)
    bolum tablosu disinda burada durur;
  * diskin son `EDGE` bayti — GPT yedek basligi ve girisleri;
  * `SMALL_GAP`'ten kucuk bolumler arasi bosluklar ve EBR sektorleri.
"""
from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from . import diagnostics
from .image import BlockDevice, PartitionView, is_zero
from .ptable import MBR_EXTENDED_TYPES

Ranges = List[Tuple[int, int]]

MIB = 1024 * 1024
EDGE = MIB                 # her bolumun basi/sonu ve diskin sonu
SMALL_GAP = 16 * MIB       # bundan kucuk bolum disi bosluklar tam alinir
GROUP_BYTES = MIB          # bitmap tarama tanecigi


# --------------------------------------------------------------------------
# Yardimcilar
# --------------------------------------------------------------------------
def merge(ranges: Ranges, limit: int = -1) -> Ranges:
    """Araliklari siralar, kesisen/bitisikleri birlestirir, `limit`e kirpar."""
    out: Ranges = []
    for start, length in sorted(r for r in ranges if r[1] > 0):
        end = start + length
        if limit >= 0:
            end = min(end, limit)
        start = max(0, start)
        if end <= start:
            continue
        if out and start <= out[-1][0] + out[-1][1]:
            prev_start, prev_len = out[-1]
            out[-1] = (prev_start, max(prev_len, end - prev_start))
        else:
            out.append((start, end - start))
    return out


def total_bytes(ranges: Ranges) -> int:
    return sum(length for _, length in ranges)


def _table_ranges(table: bytes, entry_bits: int, first: int, count: int,
                  base: int, unit: int) -> Ranges:
    """Bitmap / FAT tablosunu gruplar halinde tarar.

    `table` icinde `first`. girdiden baslayan `count` girdi, her biri
    `entry_bits` bit; girdi `i` (first'ten itibaren) `base + i * unit`
    baytindaki birimi anlatir. Sifir olmayan her grup dolu sayilir —
    bitmap'te bir bit, FAT'te bos olmayan bir girdi. Kesin degil, **ihtiyatli**:
    FAT32 girdisinin ayrilmis ust 4 biti de dolu sayilir.
    """
    per_group = max(8, (GROUP_BYTES // max(1, unit)) // 8 * 8)
    out: Ranges = []
    for g in range(0, count, per_group):
        n = min(per_group, count - g)
        lo = ((first + g) * entry_bits) // 8
        hi = ((first + g + n) * entry_bits + 7) // 8
        if not is_zero(table[lo:hi]):
            out.append((base + g * unit, n * unit))
    return out


# --------------------------------------------------------------------------
# Dosya sistemleri
# --------------------------------------------------------------------------
def _fat(view: BlockDevice) -> Ranges:
    from .fat import FatFS

    fs = FatFS(view)
    bps = fs.bytes_per_sector
    data_start = fs.first_data_sector * bps
    raw = view.read(fs.reserved_sectors * bps, fs.fat_size * bps)
    bits = {12: 12, 16: 16, 32: 32}[fs.fat_type]
    need = ((fs.cluster_count + 2) * bits + 7) // 8
    if len(raw) < need:
        raise ValueError("FAT tablosu kisa")
    # Ayrilmis alan + FAT kopyalari + (FAT12/16) kok dizin: hep dolu.
    out: Ranges = [(0, data_start)]
    out += _table_ranges(raw, bits, 2, fs.cluster_count, data_start,
                         fs.cluster_bytes)
    return out


def _exfat(view: BlockDevice) -> Ranges:
    from .exfat import ExFatFS

    fs = ExFatFS(view)
    heap = fs.cluster_offset(2)
    bitmap = bytes(fs._load_bitmap())
    count = fs.cluster_count
    if len(bitmap) * 8 < count:
        raise ValueError("exFAT bitmap'i kisa")
    out: Ranges = [(0, heap)]             # onyukleme bolgeleri + FAT
    out += _table_ranges(bitmap, 1, 0, count, heap, fs.cluster_bytes)
    return out


def _ext(view: BlockDevice) -> Optional[Ranges]:
    from .extlayout import (BG_BLOCK_UNINIT, GD_BLOCK_BITMAP, GD_FLAGS,
                            GD_INODE_BITMAP, GD_INODE_TABLE, INCOMPAT_RECOVER,
                            RO_BIGALLOC, desc_field)
    from .extread import ExtFS

    fs = ExtFS(view)
    g = fs.geometry
    # Islenmemis gunlukte, diskteki bitmap'e henuz yansimamis ayirmalar
    # olabilir: o bloklar atlanirsa gunluk oynatildiginda dosya cop gosterir.
    if fs.feature_incompat & INCOMPAT_RECOVER:
        return None
    if fs.ro_compat & RO_BIGALLOC:          # bitmap biti blok degil kume
        return None
    bs = fs.block_size
    itb = g.inode_table_blocks
    csum = bool(g.csum_kind)
    out: Ranges = [(0, (g.sb_block + 1) * bs)]
    for group in range(fs.group_count):
        desc = fs._gdt[group * fs.desc_size:(group + 1) * fs.desc_size]
        if len(desc) < fs.desc_size:
            raise ValueError("grup tanimlayicisi eksik")

        def field(f):
            return desc_field(desc, f, fs.desc_size)

        # Metaveri: ustblok, GDT (+ayrilmis), bitmapler, inode tablosu.
        # Mutlak blok numaralariyla eklenir; flex_bg baska gruba koysa da dogru.
        super_blk, old_start, old_count, new_blk = g.base_meta(group)
        if super_blk is not None:
            out.append((super_blk * bs, bs))
        if old_count:
            out.append((old_start * bs, old_count * bs))
        if new_blk is not None:
            out.append((new_blk * bs, bs))
        out.append((field(GD_BLOCK_BITMAP) * bs, bs))
        out.append((field(GD_INODE_BITMAP) * bs, bs))
        out.append((field(GD_INODE_TABLE) * bs, itb * bs))
        # BLOCK_UNINIT grupta (saglama toplamli birimde) bitmap diskte yoktur
        # ve grupta metaveriden baska dolu blok yoktur — yukarida eklendi.
        if csum and field(GD_FLAGS) & BG_BLOCK_UNINIT:
            continue
        bitmap = view.read(field(GD_BLOCK_BITMAP) * bs, bs)
        if len(bitmap) < bs:
            raise ValueError("blok bitmap'i okunamadi")
        out += _table_ranges(bitmap, 1, 0, g.group_block_count(group),
                             g.group_first_block(group) * bs, bs)
    return out


def _ntfs(view: BlockDevice) -> Optional[Ranges]:
    from .ntfsread import AT_DATA, NtfsFS

    fs = NtfsFS(view)
    count = fs.cluster_count
    need = (count + 7) // 8
    attr = fs.record(6).find(AT_DATA)              # $Bitmap
    if attr is None:
        return None
    bitmap = fs.read_attribute(attr, need)
    if len(bitmap) < need:
        return None
    return _table_ranges(bitmap, 1, 0, count, 0, fs.cluster_size)


_READERS = {
    "FAT12": _fat, "FAT16": _fat, "FAT32": _fat,
    "exFAT": _exfat,
    "ext2": _ext, "ext3": _ext, "ext4": _ext,
    "NTFS": _ntfs,
}


def supports(fs_type: str) -> bool:
    return fs_type in _READERS


def fs_used_ranges(view: BlockDevice, fs_type: str = "") -> Optional[Ranges]:
    """Bolumun (gorunumun) dolu araliklari; bilinmiyorsa None (= tamami)."""
    if not fs_type:
        from .fsdetect import detect
        try:
            fs_type = detect(view).fs_type
        except Exception:                        # noqa: BLE001
            return None
    reader = _READERS.get(fs_type)
    if reader is None:
        return None
    try:
        with diagnostics.span("usedmap.fs", fs=fs_type, size=view.size):
            ranges = reader(view)
    except Exception as exc:                     # noqa: BLE001
        diagnostics.warn("usedmap: %s okunamadi, tamami alinacak: %s"
                        % (fs_type, exc))
        return None
    if ranges is None:
        return None
    size = view.size
    ranges = list(ranges)
    ranges.append((0, min(EDGE, size)))
    ranges.append((max(0, size - EDGE), min(EDGE, size)))
    return merge(ranges, size)


# --------------------------------------------------------------------------
# Disk
# --------------------------------------------------------------------------
def disk_used_ranges(device: BlockDevice,
                     progress: Optional[Callable[[str, int], None]] = None
                     ) -> Optional[Ranges]:
    """Butun disk icin dolu araliklar; tablo okunamazsa None (= tamami)."""
    from .ptable import WholeDiskTable
    from .session import read_partition_table

    with diagnostics.span("usedmap.disk", size=device.size):
        table = read_partition_table(device)
        if table is None:
            return None
        if isinstance(table, WholeDiskTable):
            return fs_used_ranges(device)
        ss = device.sector_size
        size = device.size
        parts = sorted(table.partitions, key=lambda p: p.start_lba)
        if not parts:
            return None
        out: Ranges = []
        covered: Ranges = []
        for no, part in enumerate(parts):
            start = part.start_lba * ss
            length = min(part.sector_count * ss, size - start)
            if length <= 0:
                continue
            covered.append((start, length))
            if part.scheme == "mbr" and part.type_id in MBR_EXTENDED_TYPES \
                    and not part.logical:
                out.append((start, ss))            # ilk EBR
                continue
            if part.logical and part.ebr_lba:
                out.append((part.ebr_lba * ss, ss))
            if progress:
                progress(_progress_text(part.index), -1)
            view = PartitionView(device, part.start_lba, length // ss)
            ranges = fs_used_ranges(view)
            if ranges is None:
                out.append((start, length))
            else:
                out += [(start + o, n) for o, n in ranges]

        # Bolum disi alan: bastaki bolge tamamen, kucuk bosluklar tamamen,
        # diskin sonu (GPT yedegi). Buyuk bos alan alinmaz.
        covered = merge(covered, size)
        pos = 0
        for i, (start, length) in enumerate(covered):
            gap = start - pos
            if gap > 0 and (i == 0 or gap <= SMALL_GAP):
                out.append((pos, gap))
            pos = start + length
        if 0 < size - pos <= SMALL_GAP:
            out.append((pos, size - pos))
        out.append((max(0, size - EDGE), min(EDGE, size)))
        return merge(out, size)


def _progress_text(index: int) -> str:
    from ..i18n import tr
    return tr("Kullanilan alan hesaplaniyor: bolum {}", index)


def block_flags(ranges: Optional[Ranges], total: int, block_size: int) -> bytearray:
    """Her yedek blogu icin 1 = okunacak, 0 = atlanacak."""
    count = (total + block_size - 1) // block_size
    if ranges is None:
        return bytearray(b"\x01" * count)
    flags = bytearray(count)
    for start, length in merge(ranges, total):
        first = start // block_size
        last = (start + length - 1) // block_size
        flags[first:last + 1] = b"\x01" * (last - first + 1)
    return flags
