"""ext2/3/4 yerlesim geometrisi — okuma, yazma ve boyutlandirmanin ortak dili.

Grup tanimlayicilarinin (GDT) nerede durdugu, hangi grubun yedek ustblok
tasidigi ve bir grubun basindaki "taban metaveri" alani birimin ozellik
bayraklarina baglidir. Bu hesap daha once okuyucuda ve yazicida ayri ayri,
yalnizca en basit durum (`meta_bg` yok, GDT ustblogun hemen ardinda) icin
yapiliyordu. `resize2fs` ile buyutulmus gercek birimler `meta_bg` tasiyabilir;
bu birimlerde eski hesap **yanlis tanimlayiciyi** okuyordu.

Kurallar e2fsprogs `ext2fs_super_and_bgd_loc2()` ve cekirdek
`descriptor_loc()` ile aynidir:

* `sparse_super`: yedek ustblok yalnizca 0, 1 ve 3/5/7'nin kuvvetleri olan
  gruplarda. Ozellik kapaliysa her grupta.
* `sparse_super2`: yalnizca 0 ve `s_backup_bgs[0..1]` gruplarinda.
* "Klasik" GDT: yedek ustblok tasiyan grupta ustbloktan hemen sonra,
  `desc_blocks + s_reserved_gdt_blocks` blok (meta_bg varsa `s_first_meta_bg`).
* `meta_bg`: `s_first_meta_bg` ve sonrasindaki her GDT blogu kendi "meta
  grubunun" (blok basina tanimlayici sayisi kadar grup) 0., 1. ve son
  grubunda durur; o grup yedek ustblok tasiyorsa ondan sonra.
"""
from __future__ import annotations

import struct
from typing import List, Optional, Tuple

from .crc32c import TABLE as _CRC32C_TABLE

SUPERBLOCK_OFFSET = 1024

# s_feature_compat
COMPAT_HAS_JOURNAL = 0x0004
COMPAT_RESIZE_INODE = 0x0010
COMPAT_SPARSE_SUPER2 = 0x0200
# s_feature_incompat
INCOMPAT_RECOVER = 0x0004
INCOMPAT_JOURNAL_DEV = 0x0008
INCOMPAT_META_BG = 0x0010
INCOMPAT_64BIT = 0x0080
INCOMPAT_MMP = 0x0100
INCOMPAT_FLEX_BG = 0x0200
INCOMPAT_CSUM_SEED = 0x2000
# s_feature_ro_compat
RO_SPARSE_SUPER = 0x0001
RO_GDT_CSUM = 0x0010
RO_BIGALLOC = 0x0200
RO_METADATA_CSUM = 0x0400

# Bilinen ozellik bitlerinin adlari ("c"=compat, "i"=incompat, "r"=ro_compat)
FEATURE_NAMES = {
    ("c", 0x0001): "dir_prealloc", ("c", 0x0002): "imagic_inodes",
    ("c", 0x0004): "has_journal", ("c", 0x0008): "ext_attr",
    ("c", 0x0010): "resize_inode", ("c", 0x0020): "dir_index",
    ("c", 0x0040): "lazy_bg", ("c", 0x0080): "snapshot_bitmap",
    ("c", 0x0100): "exclude_bitmap", ("c", 0x0200): "sparse_super2",
    ("c", 0x0400): "fast_commit", ("c", 0x0800): "stable_inodes",
    ("c", 0x1000): "orphan_file",
    ("i", 0x0001): "compression", ("i", 0x0002): "filetype",
    ("i", 0x0004): "needs_recovery", ("i", 0x0008): "journal_dev",
    ("i", 0x0010): "meta_bg", ("i", 0x0040): "extent",
    ("i", 0x0080): "64bit", ("i", 0x0100): "mmp", ("i", 0x0200): "flex_bg",
    ("i", 0x0400): "ea_inode", ("i", 0x1000): "dirdata",
    ("i", 0x2000): "metadata_csum_seed", ("i", 0x4000): "large_dir",
    ("i", 0x8000): "inline_data", ("i", 0x10000): "encrypt",
    ("i", 0x20000): "casefold",
    ("r", 0x0001): "sparse_super", ("r", 0x0002): "large_file",
    ("r", 0x0004): "btree_dir", ("r", 0x0008): "huge_file",
    ("r", 0x0010): "uninit_bg", ("r", 0x0020): "dir_nlink",
    ("r", 0x0040): "extra_isize", ("r", 0x0080): "has_snapshot",
    ("r", 0x0100): "quota", ("r", 0x0200): "bigalloc",
    ("r", 0x0400): "metadata_csum", ("r", 0x0800): "replica",
    ("r", 0x1000): "read-only", ("r", 0x2000): "project",
    ("r", 0x4000): "shared_blocks", ("r", 0x8000): "verity",
    ("r", 0x10000): "orphan_present",
}


def unsupported_features(compat: int, incompat: int, ro_compat: int,
                         allow_compat: int, allow_incompat: int,
                         allow_ro: int) -> List[str]:
    """Izin listesi disinda kalan ozellik bitlerinin adlari (bilinmeyenler
    "i:0x40000" bicimiyle). Bos liste = hepsi destekleniyor."""
    out: List[str] = []
    for kind, value, allowed in (("c", compat, allow_compat),
                                 ("i", incompat, allow_incompat),
                                 ("r", ro_compat, allow_ro)):
        extra = value & ~allowed & 0xFFFFFFFF
        bit = 1
        while extra:
            if extra & bit:
                out.append(FEATURE_NAMES.get((kind, bit), "%s:0x%X" % (kind, bit)))
                extra &= ~bit
            bit <<= 1
    return out


def ext_kind(compat: int, incompat: int, ro_compat: int) -> str:
    """ext2 / ext3 / ext4 / jbd ayrimi (blkid kurali).

    Eskiden yalnizca extents/huge_file'a bakiliyordu (denetim E15): ornegin
    `-O ^extent,^huge_file` ile yapilmis flex_bg/metadata_csum'lu birim ext3
    goruluyordu. ext3 yalnizca gunluk + ext3'un bildigi bitleri tasir;
    disinda bir bit varsa ext4'tur. Ayri gunluk aygiti (journal_dev) bir
    dosya sistemi degildir: "jbd" doner ve ext erisimine yonlenmez.
    """
    if incompat & INCOMPAT_JOURNAL_DEV:
        return "jbd"
    ext3_incompat = 0x0002 | INCOMPAT_RECOVER | INCOMPAT_META_BG
    ext3_ro = RO_SPARSE_SUPER | 0x0002 | 0x0004
    if incompat & ~ext3_incompat or ro_compat & ~ext3_ro:
        return "ext4"
    return "ext3" if compat & COMPAT_HAS_JOURNAL else "ext2"


# grup tanimlayici bayraklari
BG_INODE_UNINIT = 0x0001
BG_BLOCK_UNINIT = 0x0002
BG_INODE_ZEROED = 0x0004

GD_CHECKSUM_OFFSET = 0x1E
SB_CHECKSUM_OFFSET = 0x3FC


# ----------------------------------------------------------------------
# Saglamalar
# ----------------------------------------------------------------------
def _crc16_table() -> list:
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
        table.append(crc)
    return table


_CRC16_TABLE = _crc16_table()


def crc16(crc: int, data: bytes) -> int:
    """e2fsprogs `ext2fs_crc16` (CRC-16/ARC, polinom 0x8005 yansitilmis)."""
    for b in data:
        crc = (crc >> 8) ^ _CRC16_TABLE[(crc ^ b) & 0xFF]
    return crc & 0xFFFF


def raw_crc32c(crc: int, data: bytes) -> int:
    for b in data:
        crc = _CRC32C_TABLE[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return crc & 0xFFFFFFFF


def has_super_sparse(group: int) -> bool:
    """sparse_super kurali: 0, 1 ve 3/5/7'nin kuvvetleri."""
    if group in (0, 1):
        return True
    for base in (3, 5, 7):
        power = base
        while power < group:
            power *= base
        if power == group:
            return True
    return False


class ExtGeometry:
    """Ustbloktan cikarilan yerlesim. Ham ustblok `sb` icinde tutulur.

    Yazma yapmaz; boyutlandirici `sb`yi degistirip `group_count` gibi
    turetilmis degerleri `set_blocks_count()` ile yeniden hesaplatir.
    """

    def __init__(self, sb: bytes):
        self.sb = bytearray(sb)
        u32 = lambda off: struct.unpack_from("<I", self.sb, off)[0]  # noqa: E731
        self.block_size = 1024 << u32(0x18)
        self.first_data_block = u32(0x14)
        self.blocks_per_group = u32(0x20)
        self.inodes_per_group = u32(0x28)
        rev = u32(0x4C)
        self.compat = u32(0x5C) if rev >= 1 else 0
        self.incompat = u32(0x60) if rev >= 1 else 0
        self.ro_compat = u32(0x64) if rev >= 1 else 0
        self.inode_size = (struct.unpack_from("<H", self.sb, 0x58)[0] or 128) \
            if rev >= 1 else 128
        desc_size = struct.unpack_from("<H", self.sb, 0xFE)[0]
        self.desc_size = desc_size if (
            self.incompat & INCOMPAT_64BIT and desc_size >= 64) else 32
        self.reserved_gdt = struct.unpack_from("<H", self.sb, 0xCE)[0]
        self.first_meta_bg = u32(0x104)
        self.backup_bgs = (u32(0x24C), u32(0x250))
        blocks_hi = u32(0x150) if self.incompat & INCOMPAT_64BIT else 0
        self.set_blocks_count((blocks_hi << 32) | u32(0x04))

    # ------------------------------------------------------------------
    def set_blocks_count(self, blocks: int) -> None:
        self.blocks_count = blocks
        self.group_count = max(1, (blocks - self.first_data_block
                                   + self.blocks_per_group - 1)
                               // self.blocks_per_group)

    @property
    def descs_per_block(self) -> int:
        return self.block_size // self.desc_size

    @property
    def desc_blocks(self) -> int:
        return (self.group_count + self.descs_per_block - 1) // self.descs_per_block

    @property
    def meta_bg(self) -> bool:
        return bool(self.incompat & INCOMPAT_META_BG)

    @property
    def csum_kind(self) -> str:
        if self.ro_compat & RO_METADATA_CSUM:
            return "crc32c"
        if self.ro_compat & RO_GDT_CSUM:
            return "crc16"
        return ""

    @property
    def inode_table_blocks(self) -> int:
        return (self.inodes_per_group * self.inode_size + self.block_size - 1) \
            // self.block_size

    @property
    def sb_block(self) -> int:
        """Birincil ustblogun bulundugu blok (1K blokta 1, digerlerinde 0)."""
        if self.block_size == 1024 and self.first_data_block == 0:
            return 1
        return self.first_data_block

    # ------------------------------------------------------------------
    def group_first_block(self, group: int) -> int:
        return self.first_data_block + group * self.blocks_per_group

    def group_block_count(self, group: int) -> int:
        if group < self.group_count - 1:
            return self.blocks_per_group
        return self.blocks_count - self.group_first_block(group)

    def has_super(self, group: int) -> bool:
        if group == 0:
            return True
        if self.compat & COMPAT_SPARSE_SUPER2:
            return group in self.backup_bgs
        if not (self.ro_compat & RO_SPARSE_SUPER):
            return True
        return has_super_sparse(group)

    def old_desc_blocks(self) -> int:
        """Yedek ustbloklu gruplarda ustbloktan sonraki klasik alan (blok)."""
        if self.meta_bg:
            return self.first_meta_bg
        return self.desc_blocks + self.reserved_gdt

    def base_meta(self, group: int) -> Tuple[Optional[int], int, int, Optional[int]]:
        """(ustblok, klasik_gdt_baslangici, klasik_blok_sayisi, meta_gdt_blogu).

        e2fsprogs `ext2fs_super_and_bgd_loc2` ile ayni. Olmayanlar None / 0.
        """
        start = self.group_first_block(group)
        has = self.has_super(group)
        super_blk = start if has else None
        if group == 0 and self.block_size == 1024 and self.first_data_block == 0:
            super_blk = 1
        old_start, old_count, new_blk = 0, 0, None
        dpb = self.descs_per_block
        meta = group // dpb
        if not self.meta_bg or meta < self.first_meta_bg:
            if has:
                old_start = start + 1
                old_count = self.old_desc_blocks()
        else:
            if group % dpb in (0, 1, dpb - 1):
                new_blk = start + (1 if has else 0)
        return super_blk, old_start, old_count, new_blk

    def base_meta_count(self, group: int) -> int:
        super_blk, _s, old_count, new_blk = self.base_meta(group)
        return (1 if super_blk is not None else 0) + old_count + \
            (1 if new_blk is not None else 0)

    def gdt_block_locations(self, index: int) -> List[int]:
        """GDT'nin `index`. blogunun birincil ve yedek konumlari (birincil once)."""
        dpb = self.descs_per_block
        if not self.meta_bg or index < self.first_meta_bg:
            # Klasik kopyalar yalnizca klasik alandaki yedek gruplarda durur;
            # meta_bg bolgesindeki yedek gruplarda ustbloktan sonra bitmap
            # gelir (kopya oraya yazilinca o grubun bitmap'i eziliyordu).
            limit = self.group_count
            if self.meta_bg:
                limit = min(limit, self.first_meta_bg * dpb)
            out = [self.sb_block + 1 + index]
            for g in range(1, limit):
                if self.has_super(g):
                    out.append(self.group_first_block(g) + 1 + index)
            return out
        out = []
        first = index * dpb
        for g in (first, first + 1, first + dpb - 1):
            if g < self.group_count:
                out.append(self.group_first_block(g) + (1 if self.has_super(g) else 0))
        return out

    def primary_gdt_block(self, index: int) -> int:
        """GDT'nin `index`. blogunun birincil konumu (cekirdek `descriptor_loc`)."""
        if not self.meta_bg or index < self.first_meta_bg:
            return self.sb_block + 1 + index
        group = index * self.descs_per_block
        return self.group_first_block(group) + (1 if self.has_super(group) else 0)

    def descriptor_offset(self, group: int) -> int:
        """Birincil tanimlayicinin bayt ofseti."""
        index, slot = divmod(group, self.descs_per_block)
        return self.primary_gdt_block(index) * self.block_size + \
            slot * self.desc_size

    def backup_groups(self) -> List[int]:
        return [g for g in range(1, self.group_count) if self.has_super(g)]


# ----------------------------------------------------------------------
# Grup tanimlayicisi alanlari
# ----------------------------------------------------------------------
def gd_get(desc: bytes, lo: int, hi: int, desc_size: int, width: int = 4) -> int:
    fmt = "<I" if width == 4 else "<H"
    value = struct.unpack_from(fmt, desc, lo)[0]
    if desc_size >= 64 and hi:
        value |= struct.unpack_from(fmt, desc, hi)[0] << (32 if width == 4 else 16)
    return value


def gd_set(desc: bytearray, lo: int, hi: int, desc_size: int, value: int,
           width: int = 4) -> None:
    fmt, bits = ("<I", 32) if width == 4 else ("<H", 16)
    mask = (1 << bits) - 1
    struct.pack_into(fmt, desc, lo, value & mask)
    if desc_size >= 64 and hi:
        struct.pack_into(fmt, desc, hi, (value >> bits) & mask)


# alan ofsetleri (lo, hi, genislik)
GD_BLOCK_BITMAP = (0x00, 0x20, 4)
GD_INODE_BITMAP = (0x04, 0x24, 4)
GD_INODE_TABLE = (0x08, 0x28, 4)
GD_FREE_BLOCKS = (0x0C, 0x2C, 2)
GD_FREE_INODES = (0x0E, 0x2E, 2)
GD_USED_DIRS = (0x10, 0x30, 2)
GD_FLAGS = (0x12, 0, 2)
GD_BB_CSUM = (0x18, 0x38, 2)
GD_IB_CSUM = (0x1A, 0x3A, 2)
GD_ITABLE_UNUSED = (0x1C, 0x32, 2)


def desc_field(desc: bytes, field, desc_size: int) -> int:
    lo, hi, width = field
    return gd_get(desc, lo, hi, desc_size, width)


def set_desc_field(desc: bytearray, field, desc_size: int, value: int) -> None:
    lo, hi, width = field
    gd_set(desc, lo, hi, desc_size, value, width)


class ExtCsum:
    """Ustblok / tanimlayici / bitmap / inode saglamalari (iki algoritma)."""

    def __init__(self, geo: ExtGeometry):
        self.geo = geo
        self.kind = geo.csum_kind
        uuid = bytes(geo.sb[0x68:0x78])
        self.uuid = uuid
        if geo.incompat & INCOMPAT_CSUM_SEED:
            self.seed = struct.unpack_from("<I", geo.sb, 0x270)[0]
        else:
            self.seed = raw_crc32c(0xFFFFFFFF, uuid)

    def group_desc(self, group: int, desc: bytes) -> int:
        if self.kind == "crc32c":
            crc = raw_crc32c(self.seed, struct.pack("<I", group))
            crc = raw_crc32c(crc, bytes(desc[:GD_CHECKSUM_OFFSET]))
            crc = raw_crc32c(crc, b"\x00\x00")
            if len(desc) > GD_CHECKSUM_OFFSET + 2:
                crc = raw_crc32c(crc, bytes(desc[GD_CHECKSUM_OFFSET + 2:]))
            return crc & 0xFFFF
        if self.kind == "crc16":
            crc = crc16(0xFFFF, self.uuid)
            crc = crc16(crc, struct.pack("<I", group))
            crc = crc16(crc, bytes(desc[:GD_CHECKSUM_OFFSET]))
            if len(desc) > GD_CHECKSUM_OFFSET + 2:
                crc = crc16(crc, bytes(desc[GD_CHECKSUM_OFFSET + 2:]))
            return crc
        return 0

    def stamp_desc(self, group: int, desc: bytearray) -> None:
        if self.kind:
            struct.pack_into("<H", desc, GD_CHECKSUM_OFFSET,
                             self.group_desc(group, desc))

    def bitmap(self, data: bytes) -> int:
        return raw_crc32c(self.seed, bytes(data))

    def stamp_bitmap(self, desc: bytearray, data: bytes, inode_map: bool,
                     desc_size: int) -> None:
        if self.kind != "crc32c":
            return
        value = self.bitmap(data)
        field = GD_IB_CSUM if inode_map else GD_BB_CSUM
        if desc_size < 64:
            value &= 0xFFFF
        set_desc_field(desc, field, desc_size, value)

    def stamp_superblock(self, sb: bytearray) -> None:
        if self.kind == "crc32c":
            struct.pack_into("<I", sb, SB_CHECKSUM_OFFSET,
                             raw_crc32c(0xFFFFFFFF, bytes(sb[:SB_CHECKSUM_OFFSET])))

    def stamp_inode(self, ino: int, raw: bytearray, inode_size: int) -> None:
        if self.kind != "crc32c":
            return
        generation = struct.unpack_from("<I", raw, 0x64)[0]
        crc = raw_crc32c(self.seed, struct.pack("<I", ino))
        crc = raw_crc32c(crc, struct.pack("<I", generation))
        crc = raw_crc32c(crc, bytes(raw[:0x7C]))
        crc = raw_crc32c(crc, b"\x00\x00")
        hi = False
        if inode_size > 128:
            extra = struct.unpack_from("<H", raw, 0x80)[0]
            hi = 0x84 <= 128 + extra
        if hi:
            crc = raw_crc32c(crc, bytes(raw[0x7E:0x82]))
            crc = raw_crc32c(crc, b"\x00\x00")
            crc = raw_crc32c(crc, bytes(raw[0x84:inode_size]))
        else:
            crc = raw_crc32c(crc, bytes(raw[0x7E:inode_size]))
        struct.pack_into("<H", raw, 0x7C, crc & 0xFFFF)
        if hi:
            struct.pack_into("<H", raw, 0x82, (crc >> 16) & 0xFFFF)
