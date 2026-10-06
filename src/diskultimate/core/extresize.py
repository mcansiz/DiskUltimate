"""ext2/3/4 boyutlandirma — saf Python (cevrimdisi `resize2fs` karsiligi).

Neden: ext boyutlandirmasi daha once yalnizca Windows'un `Resize-Partition`
aracina yonlendiriliyordu; o arac ext'i tanimadigi icin ext bolumu **hicbir
platformda** buyutulemiyordu (kullanici bildirimi, 2026-09-29).

## Buyutme
1. Son grup yarim ise bitmap'inde yeni bloklar bosaltilir.
2. Yeni gruplar eklenir; her grubun bitmap'leri ve inode tablosu **kendi
   icine** yerlestirilir (flex_bg birimde de gecerli bir yerlesimdir).
3. GDT buyumesi gerekiyorsa:
   * `resize_inode` varsa ayrilmis GDT bloklari kullanilir ve inode 7
     yeniden kurulur (e2fsprogs `ext2fs_create_resize_inode` ile ayni duzen);
   * ayrilmis blok yetmezse birim `meta_bg`ye gecirilir (`resize2fs` de
     boyle yapar): eski GDT bloklari `s_first_meta_bg` olur, yeni bloklar
     meta gruplarinin 0./1./son grubunda durur.
4. Ustblok ve tum yedekleri, GDT'nin tum kopyalari yazilir.

Yazma sirasi: once yeni gruplarin metaverisi (eski birimin disinda), sonra
GDT kopyalari, **en son** birincil ustblok. Yarida kesilirse birim eski
boyutuyla tutarli kalir.

## Kucultme
Kesilen bolgedeki bloklar ve inode'lar kullanilmiyorsa dogrudan yapilir;
kullaniliyorsa once `extmove` ile tasinir.

## Reddedilenler (nedeniyle)
Ozellik denetimi bir izin listesidir (`RESIZE_*`): listede olmayan her bit
(bilinmeyen gelecek ozellikler dahil) reddedilir — ornegin `bigalloc`, ayri
gunluk aygiti, `mmp`, `sparse_super2`. Gunlugu tekrar oynatilmamis, hata
kayitli ya da bekleyen yetim inode'u olan birim de reddedilir. Kucultmede
`stable_inodes` birimde inode tasinmasi gerekiyorsa reddedilir (`extmove`).

Dogrulama: `tests/ext_resize_check.py` — mkfs.ext4 ve kendi bicimlendiricimizin
urettigi birimler buyutulur/kucultulur, her adimda `e2fsck -fn` temiz ve
dosya icerikleri ayni kalmali.
"""
from __future__ import annotations

import struct
import time
from typing import Callable, Dict, List, Optional, Tuple

from .extlayout import (BG_BLOCK_UNINIT, BG_INODE_UNINIT,
                        COMPAT_RESIZE_INODE, COMPAT_SPARSE_SUPER2,
                        GD_BLOCK_BITMAP, GD_FLAGS, GD_FREE_BLOCKS,
                        GD_FREE_INODES, GD_INODE_BITMAP, GD_INODE_TABLE,
                        GD_ITABLE_UNUSED, GD_USED_DIRS, INCOMPAT_64BIT,
                        INCOMPAT_JOURNAL_DEV, INCOMPAT_META_BG, INCOMPAT_MMP,
                        INCOMPAT_RECOVER, RO_BIGALLOC, SUPERBLOCK_OFFSET,
                        ExtCsum, ExtGeometry, desc_field, set_desc_field,
                        unsupported_features)
from .image import BlockDevice
from ..i18n import tr

INO_RESIZE = 7
RO_ORPHAN_PRESENT = 0x10000
COMPAT_STABLE_INODES = 0x0800

# Boyutlandiricinin (buyutme + kucultme) dogru isledigi ozellikler.
# compat: dir_prealloc, imagic_inodes, has_journal, ext_attr, resize_inode,
#   dir_index, fast_commit, stable_inodes (kucultmede ayrica), orphan_file
RESIZE_COMPAT = 0x0001 | 0x0002 | 0x0004 | 0x0008 | 0x0010 | 0x0020 | \
    0x0400 | 0x0800 | 0x1000
# incompat: filetype, meta_bg, extent, 64bit, flex_bg, ea_inode ve
#   inline_data (yalnizca buyutme; kucultme `extmove.check`te reddeder),
#   csum_seed, large_dir, encrypt, casefold
RESIZE_INCOMPAT = 0x0002 | 0x0010 | 0x0040 | 0x0080 | 0x0200 | 0x0400 | \
    0x2000 | 0x4000 | 0x8000 | 0x10000 | 0x20000 | INCOMPAT_RECOVER
# ro_compat: sparse_super, large_file, huge_file, gdt_csum, dir_nlink,
#   extra_isize, quota/project (kucultmede inode tasima ayrica reddedilir),
#   metadata_csum, verity
RESIZE_RO_COMPAT = 0x0001 | 0x0002 | 0x0008 | 0x0010 | 0x0020 | 0x0040 | \
    0x0100 | 0x2000 | 0x0400 | 0x8000 | RO_ORPHAN_PRESENT
MIN_LAST_GROUP_FREE = 50         # resize2fs ile ayni: son grup bundan kucukse atilir


class ExtResizeError(Exception):
    pass


Progress = Optional[Callable[[str, int], None]]


# ======================================================================
# Birim durumu (ustblok + tum tanimlayicilar bellekte)
# ======================================================================
class _Volume:
    def __init__(self, dev: BlockDevice):
        self.dev = dev
        sb = dev.read(SUPERBLOCK_OFFSET, 1024)
        if len(sb) < 1024 or struct.unpack_from("<H", sb, 0x38)[0] != 0xEF53:
            raise ExtResizeError(tr("ext imzasi yok"))
        self.geo = ExtGeometry(sb)
        self.csum = ExtCsum(self.geo)
        g = self.geo
        self.bs = g.block_size
        self.descs: List[bytearray] = []
        for group in range(g.group_count):
            off = g.descriptor_offset(group)
            self.descs.append(bytearray(dev.read(off, g.desc_size)))

    # --- ham blok -----------------------------------------------------
    def read_block(self, block: int) -> bytearray:
        return bytearray(self.dev.read(block * self.bs, self.bs))

    def write_block(self, block: int, data: bytes) -> None:
        if len(data) != self.bs:
            raise ExtResizeError(tr("Blok boyutu uyusmuyor"))
        self.dev.write(block * self.bs, bytes(data))

    # --- ustblok alanlari ---------------------------------------------
    def sb_u32(self, off: int) -> int:
        return struct.unpack_from("<I", self.geo.sb, off)[0]

    def sb_set_u32(self, off: int, value: int) -> None:
        struct.pack_into("<I", self.geo.sb, off, value & 0xFFFFFFFF)

    def sb_u64(self, lo: int, hi: int) -> int:
        value = self.sb_u32(lo)
        if self.geo.incompat & INCOMPAT_64BIT:
            value |= self.sb_u32(hi) << 32
        return value

    def sb_set_u64(self, lo: int, hi: int, value: int) -> None:
        self.sb_set_u32(lo, value)
        if self.geo.incompat & INCOMPAT_64BIT:
            self.sb_set_u32(hi, value >> 32)

    # --- sayaclar -----------------------------------------------------
    @property
    def blocks_count(self) -> int:
        return self.sb_u64(0x04, 0x150)

    def d(self, group: int, field) -> int:
        return desc_field(self.descs[group], field, self.geo.desc_size)

    def set_d(self, group: int, field, value: int) -> None:
        set_desc_field(self.descs[group], field, self.geo.desc_size, value)

    # --- denetim ------------------------------------------------------
    def check_supported(self) -> None:
        """Izin listesi: yalnizca boyutlandiricinin bildigi ozellikler.

        Eskiden yalnizca bigalloc/journal_dev/mmp/sparse_super2 reddediliyordu;
        bilinmeyen (gelecekteki) bir ozellik sessizce kabul ediliyordu.
        """
        g = self.geo
        missing = []
        if g.incompat & INCOMPAT_JOURNAL_DEV:
            missing.append(tr("ayri gunluk aygiti"))
        for name in unsupported_features(g.compat, g.incompat, g.ro_compat,
                                         RESIZE_COMPAT, RESIZE_INCOMPAT,
                                         RESIZE_RO_COMPAT):
            if name not in ("journal_dev", "needs_recovery", "orphan_present"):
                missing.append(name)
        if missing:
            raise ExtResizeError(tr(
                "Bu ext birimi su ozellikleri kullaniyor ve boyutlandirmasi "
                "desteklenmiyor: {}", ", ".join(missing)))
        if g.incompat & INCOMPAT_RECOVER:
            raise ExtResizeError(tr(
                "Birimin gunlugunde islenmemis kayitlar var (temiz "
                "kapatilmamis). Once birimi baglayip duzgun ayirin veya "
                "e2fsck ile onarin."))
        state = struct.unpack_from("<H", g.sb, 0x3A)[0]
        # 0x0004: yetim inode'lar isleniyor; s_last_orphan/orphan_present:
        # bekleyen yetim inode'lar (cekirdek baglarken siler)
        if not state & 0x0001 or state & 0x0006 or \
                struct.unpack_from("<I", g.sb, 0xE8)[0] or \
                g.ro_compat & RO_ORPHAN_PRESENT:
            raise ExtResizeError(tr(
                "Birim temiz degil veya hata kaydi var. Boyutlandirmadan once "
                "e2fsck ile denetlenmeli."))

    # --- metaveri yerleri ---------------------------------------------
    def metadata_blocks_in(self, group: int) -> List[int]:
        """Grubun icine dusen tum metaveri bloklari (yerel ofset olarak).

        BLOCK_UNINIT grupta bitmap diskte yoktur; cekirdek onu bu kurala gore
        uretir. Burada ayni kural gerceklenir ve flex_bg ile baska gruplarin
        bu gruba yerlesmis bitmap/inode tablolari da hesaba katilir.
        """
        g = self.geo
        start = g.group_first_block(group)
        size = g.group_block_count(group)
        used = set()
        super_blk, old_start, old_count, new_blk = g.base_meta(group)
        if super_blk is not None and start <= super_blk < start + size:
            used.add(super_blk - start)
        for b in range(old_start, old_start + old_count):
            used.add(b - start)
        if new_blk is not None:
            used.add(new_blk - start)
        itb = g.inode_table_blocks
        for other in range(len(self.descs)):
            for field, length in ((GD_BLOCK_BITMAP, 1), (GD_INODE_BITMAP, 1),
                                  (GD_INODE_TABLE, itb)):
                first = self.d(other, field)
                for b in range(first, first + length):
                    if start <= b < start + size:
                        used.add(b - start)
        return sorted(used)

    def block_bitmap(self, group: int) -> bytearray:
        """Grubun blok bitmap'i; BLOCK_UNINIT ise kurala gore uretilir."""
        g = self.geo
        if self.d(group, GD_FLAGS) & BG_BLOCK_UNINIT and g.csum_kind:
            buf = bytearray(self.bs)
            for i in self.metadata_blocks_in(group):
                buf[i >> 3] |= 1 << (i & 7)
            _set_padding(buf, g.group_block_count(group), self.bs * 8)
            return buf
        return self.read_block(self.d(group, GD_BLOCK_BITMAP))


def _set_padding(buf: bytearray, used_bits: int, total_bits: int) -> None:
    for i in range(used_bits, total_bits):
        buf[i >> 3] |= 1 << (i & 7)


def _clear_bits(buf: bytearray, first: int, last: int) -> None:
    for i in range(first, last):
        buf[i >> 3] &= ~(1 << (i & 7)) & 0xFF


def _count_zero_bits(buf: bytes, nbits: int) -> int:
    full, rest = divmod(nbits, 8)
    ones = sum(bin(b).count("1") for b in buf[:full])
    if rest:
        ones += bin(buf[full] & ((1 << rest) - 1)).count("1")
    return nbits - ones


# ======================================================================
# Sinirlar
# ======================================================================
def ext_limits(dev: BlockDevice) -> Dict[str, int]:
    """Boyutlandirma sinirlari (bayt).

    `min_bytes`: kucultmenin inebilecegi en kucuk boyut (kullanilan en yuksek
    grup + kullanilan bloklar icin gereken alan). `max_bytes`: 0 = sinirsiz
    (bolum ne kadarsa); 64bit olmayan birimde 2^32 blok.
    """
    vol = _Volume(dev)
    vol.check_supported()
    g = vol.geo
    max_blocks = (1 << 64) - 1 if g.incompat & INCOMPAT_64BIT else 0xFFFFFFFF
    # inode sayisi da 32 bitle sinirli
    max_groups = 0xFFFFFFFF // g.inodes_per_group
    max_blocks = min(max_blocks, g.first_data_block + max_groups * g.blocks_per_group)
    from .extmove import min_blocks_for          # dongusel ice aktarma
    return {"block_size": g.block_size,
            "blocks": g.blocks_count,
            "min_bytes": min_blocks_for(vol) * g.block_size,
            "max_bytes": max_blocks * g.block_size}


# ======================================================================
# Buyutme
# ======================================================================
def _fit_block_count(geo: ExtGeometry, blocks: int) -> int:
    """Son grup kendi metaverisini tasiyamayacak kadar kucukse onu at."""
    trial = ExtGeometry(bytes(geo.sb))
    for _ in range(4):
        trial.set_blocks_count(blocks)
        last = trial.group_count - 1
        if last == 0:
            return blocks
        overhead = trial.base_meta_count(last) + 2 + trial.inode_table_blocks
        if trial.group_block_count(last) > overhead + MIN_LAST_GROUP_FREE:
            return blocks
        blocks = trial.group_first_block(last)
    return blocks


def ext_grow(dev: BlockDevice, new_size_bytes: int = 0,
             progress: Progress = None) -> int:
    """ext birimini `dev` sonuna (veya `new_size_bytes`e) kadar buyutur.

    `dev` yeni boyuttaki bolum penceresidir. Donus: yeni blok sayisi.
    """
    def report(msg: str, pct: int) -> None:
        if progress:
            progress(msg, pct)

    vol = _Volume(dev)
    vol.check_supported()
    g = vol.geo
    bs = g.block_size
    old_blocks = g.blocks_count
    old_groups = g.group_count
    target = (new_size_bytes or dev.size) // bs
    if not g.incompat & INCOMPAT_64BIT:
        target = min(target, 0xFFFFFFFF)
    max_groups = 0xFFFFFFFF // g.inodes_per_group
    target = min(target, g.first_data_block + max_groups * g.blocks_per_group)
    target = _fit_block_count(g, target)
    if target <= old_blocks:
        return old_blocks

    report(tr("ext yerlesimi hesaplaniyor..."), 2)
    old_desc_blocks = g.desc_blocks
    old_reserved = g.reserved_gdt
    had_resize_inode = bool(g.compat & COMPAT_RESIZE_INODE)

    # --- yeni geometri ------------------------------------------------
    g.set_blocks_count(target)
    new_groups = g.group_count
    need = g.desc_blocks - old_desc_blocks
    drop_resize_inode = False
    if not g.meta_bg and need > 0:
        if need <= old_reserved:
            g.reserved_gdt = old_reserved - need
        else:
            # Ayrilmis bloklar yetmiyor: meta_bg'ye gecilir. Klasik alan
            # (eski GDT + ayrilmis bloklar) aynen korunur, boylece eski
            # gruplarin hicbir blogu yer degistirmez.
            g.incompat |= INCOMPAT_META_BG
            g.first_meta_bg = old_desc_blocks + old_reserved
            g.reserved_gdt = 0
            drop_resize_inode = had_resize_inode
            if drop_resize_inode:
                g.compat &= ~COMPAT_RESIZE_INODE

    ipg = g.inodes_per_group
    itb = g.inode_table_blocks
    now = int(time.time())
    # Saglama ozelligi olmayan birimde "baslatilmamis inode tablosu" kavrami
    # yoktur: tablo sifirlanmak zorundadir, yoksa e2fsck eski cop veriyi
    # inode sanar. Saglamali birimde INODE_UNINIT yeterlidir; cekirdek
    # tabloyu ilk baglamada arka planda sifirlar (lazy_itable_init).
    zero_itable = not g.csum_kind

    # --- 1) son eski grup: yeni bloklari bosalt -------------------------
    last = old_groups - 1
    old_last_len = old_blocks - g.group_first_block(last)
    new_last_len = g.group_block_count(last)
    if new_last_len > old_last_len:
        bitmap = vol.block_bitmap(last)
        _clear_bits(bitmap, old_last_len, new_last_len)
        _set_padding(bitmap, new_last_len, bs * 8)
        gained = new_last_len - old_last_len
        vol.set_d(last, GD_FREE_BLOCKS, vol.d(last, GD_FREE_BLOCKS) + gained)
        flags = vol.d(last, GD_FLAGS) & ~BG_BLOCK_UNINIT
        vol.set_d(last, GD_FLAGS, flags)
        bb = vol.d(last, GD_BLOCK_BITMAP)
        vol.write_block(bb, bitmap)
        vol.csum.stamp_bitmap(vol.descs[last], bitmap[:g.blocks_per_group // 8],
                              False, g.desc_size)

    # --- 2) yeni gruplar ---------------------------------------------
    span = max(1, new_groups - old_groups)
    for group in range(old_groups, new_groups):
        report(tr("Yeni grup {} / {}", group - old_groups + 1, span),
               5 + int(80 * (group - old_groups) / span))
        start = g.group_first_block(group)
        size = g.group_block_count(group)
        overhead = g.base_meta_count(group)
        bb = start + overhead
        ib = bb + 1
        it = ib + 1
        used = overhead + 2 + itb
        if used >= size:
            raise ExtResizeError(tr("Son grup metaveri icin cok kucuk"))
        desc = bytearray(g.desc_size)
        vol.descs.append(desc)
        vol.set_d(group, GD_BLOCK_BITMAP, bb)
        vol.set_d(group, GD_INODE_BITMAP, ib)
        vol.set_d(group, GD_INODE_TABLE, it)
        vol.set_d(group, GD_FREE_BLOCKS, size - used)
        vol.set_d(group, GD_FREE_INODES, ipg)
        vol.set_d(group, GD_USED_DIRS, 0)
        flags = 0
        if g.csum_kind:
            flags |= BG_INODE_UNINIT
            vol.set_d(group, GD_ITABLE_UNUSED, ipg)
        vol.set_d(group, GD_FLAGS, flags)

        bitmap = bytearray(bs)
        for i in range(used):
            bitmap[i >> 3] |= 1 << (i & 7)
        _set_padding(bitmap, size, bs * 8)
        vol.write_block(bb, bitmap)
        vol.csum.stamp_bitmap(desc, bitmap[:g.blocks_per_group // 8], False,
                              g.desc_size)
        ibitmap = bytearray(bs)
        _set_padding(ibitmap, ipg, bs * 8)
        vol.write_block(ib, ibitmap)
        vol.csum.stamp_bitmap(desc, ibitmap[:(ipg + 7) // 8], True, g.desc_size)
        if zero_itable:
            _zero_blocks(dev, it, itb, bs)
        # Yedek ustblok bolgesinin ayrilmis GDT kopyalari sifirlanir
        _s, old_start, old_count, _n = g.base_meta(group)
        if old_count > g.desc_blocks:
            _zero_blocks(dev, old_start + g.desc_blocks, old_count - g.desc_blocks, bs)

    # --- 3) resize inode ---------------------------------------------
    report(tr("Grup tanimlayicilari yaziliyor..."), 88)
    if drop_resize_inode:
        _clear_resize_inode(vol)
    elif had_resize_inode:
        _build_resize_inode(vol)

    # --- 4) ustblok sayaclari ----------------------------------------
    old_r = vol.sb_u64(0x08, 0x154)
    struct.pack_into("<H", g.sb, 0xCE, g.reserved_gdt)
    vol.sb_set_u32(0x104, g.first_meta_bg)
    vol.sb_set_u32(0x5C, g.compat)
    vol.sb_set_u32(0x60, g.incompat)
    vol.sb_set_u64(0x04, 0x150, target)
    new_r = old_r * target // old_blocks if old_blocks else 0
    vol.sb_set_u64(0x08, 0x154, new_r)
    free_blocks = sum(vol.d(i, GD_FREE_BLOCKS) for i in range(new_groups))
    vol.sb_set_u64(0x0C, 0x158, free_blocks)
    free_inodes = sum(vol.d(i, GD_FREE_INODES) for i in range(new_groups))
    vol.sb_set_u32(0x10, free_inodes)
    vol.sb_set_u32(0x00, ipg * new_groups)
    vol.sb_set_u32(0x2C, now)                    # s_wtime

    # --- 5) GDT ve ustblok kopyalari ---------------------------------
    _write_gdt(vol)
    report(tr("Ustblok yaziliyor..."), 95)
    _write_superblocks(vol)
    flush = getattr(dev, "flush", None)
    if flush:
        flush()
    report(tr("ext buyutuldu"), 100)
    return target


def _zero_blocks(dev: BlockDevice, first: int, count: int, bs: int) -> None:
    chunk = max(1, (4 * 1024 * 1024) // bs)
    zero = b"\x00" * (chunk * bs)
    pos = 0
    while pos < count:
        n = min(chunk, count - pos)
        dev.write((first + pos) * bs, zero[:n * bs])
        pos += n


def _write_gdt(vol: _Volume) -> None:
    g = vol.geo
    bs = g.block_size
    dpb = g.descs_per_block
    for group, desc in enumerate(vol.descs):
        vol.csum.stamp_desc(group, desc)
    for index in range(g.desc_blocks):
        block = bytearray(bs)
        for slot in range(dpb):
            group = index * dpb + slot
            if group >= g.group_count:
                break
            block[slot * g.desc_size:(slot + 1) * g.desc_size] = vol.descs[group]
        for location in g.gdt_block_locations(index):
            vol.write_block(location, block)


def _write_superblocks(vol: _Volume) -> None:
    """Once yedekler, en son birincil ustblok."""
    g = vol.geo
    bs = g.block_size
    for group in g.backup_groups():
        sb = bytearray(g.sb)
        struct.pack_into("<H", sb, 0x5A, group)       # s_block_group_nr
        vol.csum.stamp_superblock(sb)
        vol.dev.write(g.group_first_block(group) * bs, bytes(sb))
    sb = bytearray(g.sb)
    struct.pack_into("<H", sb, 0x5A, 0)
    vol.csum.stamp_superblock(sb)
    vol.dev.write(SUPERBLOCK_OFFSET, bytes(sb))
    vol.geo.sb[:] = sb


# ======================================================================
# resize inode (inode 7)
# ======================================================================
def _inode_location(vol: _Volume, ino: int) -> int:
    g = vol.geo
    group, index = divmod(ino - 1, g.inodes_per_group)
    return vol.d(group, GD_INODE_TABLE) * g.block_size + index * g.inode_size


def _read_inode(vol: _Volume, ino: int) -> bytearray:
    return bytearray(vol.dev.read(_inode_location(vol, ino), vol.geo.inode_size))


def _write_inode(vol: _Volume, ino: int, raw: bytearray) -> None:
    vol.csum.stamp_inode(ino, raw, vol.geo.inode_size)
    vol.dev.write(_inode_location(vol, ino), bytes(raw))


def _free_block(vol: _Volume, block: int) -> None:
    g = vol.geo
    group, local = divmod(block - g.first_data_block, g.blocks_per_group)
    bitmap = vol.block_bitmap(group)
    if not bitmap[local >> 3] & (1 << (local & 7)):
        return
    bitmap[local >> 3] &= ~(1 << (local & 7)) & 0xFF
    vol.set_d(group, GD_FLAGS, vol.d(group, GD_FLAGS) & ~BG_BLOCK_UNINIT)
    vol.write_block(vol.d(group, GD_BLOCK_BITMAP), bitmap)
    vol.csum.stamp_bitmap(vol.descs[group], bitmap[:g.blocks_per_group // 8],
                          False, g.desc_size)
    vol.set_d(group, GD_FREE_BLOCKS, vol.d(group, GD_FREE_BLOCKS) + 1)


def _clear_resize_inode(vol: _Volume) -> None:
    """resize_inode birakilirken inode 7 ve cift dolayli blogu serbest kalir.

    Ayrilmis GDT bloklarinin kendisi zaten klasik alanda kalir (meta_bg'ye
    gecerken `s_first_meta_bg` onlari kapsar), serbest birakilmaz.
    """
    raw = _read_inode(vol, INO_RESIZE)
    dind = struct.unpack_from("<I", raw, 0x28 + 13 * 4)[0]
    if dind:
        _free_block(vol, dind)
    keep_extra = bytes(raw[128:]) if vol.geo.inode_size > 128 else b""
    new = bytearray(128) + bytearray(len(keep_extra))
    if keep_extra:
        # i_extra_isize korunur; saglama alanlari yeniden hesaplanir
        struct.pack_into("<H", new, 0x80, struct.unpack_from("<H", raw, 0x80)[0])
    _write_inode(vol, INO_RESIZE, new)


def _build_resize_inode(vol: _Volume) -> None:
    """Inode 7'yi guncel GDT/ayrilmis blok sayisina gore bastan kurar.

    Duzen (e2fsprogs `ext2fs_create_resize_inode`, e2fsck `check_resize_inode`):
      * i_block[13] = cift dolayli blok (dind)
      * dind[(desc_blocks + i) % apb] = birincil ayrilmis blok
        `sb_block + 1 + desc_blocks + i`
      * o blok bir dolayli tablodur: yedek ustbloklu her grup j icin
        `birincil + j * blocks_per_group`
    """
    g = vol.geo
    bs = g.block_size
    apb = bs // 4
    raw = _read_inode(vol, INO_RESIZE)
    dind = struct.unpack_from("<I", raw, 0x28 + 13 * 4)[0]
    if not dind:
        raise ExtResizeError(tr("resize inode'un cift dolayli blogu yok"))
    backups = g.backup_groups()
    table = bytearray(bs)
    blocks = 1
    for i in range(g.reserved_gdt):
        pblk = g.sb_block + 1 + g.desc_blocks + i
        struct.pack_into("<I", table, ((g.desc_blocks + i) % apb) * 4, pblk)
        ind = bytearray(bs)
        for n, j in enumerate(backups):
            if n >= apb:
                break
            struct.pack_into("<I", ind, n * 4, pblk + j * g.blocks_per_group)
        vol.write_block(pblk, ind)
        blocks += 1 + min(len(backups), apb)
    vol.write_block(dind, table)

    # i_block: yalnizca dind
    for k in range(15):
        struct.pack_into("<I", raw, 0x28 + k * 4, 0)
    struct.pack_into("<I", raw, 0x28 + 13 * 4, dind)
    sectors = blocks * (bs // 512)
    struct.pack_into("<I", raw, 0x1C, sectors & 0xFFFFFFFF)
    struct.pack_into("<H", raw, 0x74, (sectors >> 32) & 0xFFFF)
    size = (apb * apb + apb + 12) * bs
    struct.pack_into("<I", raw, 0x04, size & 0xFFFFFFFF)
    struct.pack_into("<I", raw, 0x6C, size >> 32)
    if not struct.unpack_from("<H", raw, 0)[0]:
        struct.pack_into("<H", raw, 0, 0o100600)
        struct.pack_into("<H", raw, 0x1A, 1)
    _write_inode(vol, INO_RESIZE, raw)
