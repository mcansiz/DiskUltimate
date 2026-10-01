"""ext2/3/4 kucultme — kesilen bolgedeki blok ve inode'larin tasinmasi.

`extresize.ext_grow` buyutur; bu modul kucultur. Kucultmenin zor yani,
kesilecek bolgede (yeni sonun otesinde) **kullanimdaki** her seyin once
kalan alana tasinmasidir:

* dosya/dizin veri bloklari, extent agaci dugumleri, dolayli blok tablolari,
  ek oznitelik (xattr) bloklari, gunluk (journal) bloklari;
* silinecek gruplardaki **inode'lar** — numaralari degisir, bu yuzden onlara
  isaret eden her dizin girisi (`..` dahil) duzeltilir;
* kalan gruplarin kesilen bolgeye dusmus bitmap/inode tablolari (flex_bg).

Inode numarasi degisince ona bagli tum saglamalar da degisir
(metadata_csum): inode, extent dugumleri, dizin bloklari, htree dugumleri.
Hepsi yeniden hesaplanir.

Extent kullanan bir dosyanin tasinmasi agaci **bastan kurar**: eski esleme
okunur, kesilen bolgedeki araliklar yeni yerlerine kopyalanir, yeni esleme
gerekirse daha derin bir agacla yazilir. (Ayni yardimci, `extwrite`in
"extent agaci buyutme" eksigi icin de kullanilir: `build_extent_tree`.)

Reddedilenler: `inline_data`, `ea_inode`, kotali birimde inode tasima,
kesilen bolgede kotuk blok (inode 1) kaydi.
"""
from __future__ import annotations

import struct
from typing import Callable, Dict, List, Optional, Set, Tuple

from .extlayout import (BG_BLOCK_UNINIT, BG_INODE_UNINIT, BG_INODE_ZEROED,
                        COMPAT_RESIZE_INODE, GD_BLOCK_BITMAP, GD_FLAGS,
                        GD_FREE_BLOCKS, GD_FREE_INODES, GD_INODE_BITMAP,
                        GD_INODE_TABLE, GD_ITABLE_UNUSED, GD_USED_DIRS,
                        INCOMPAT_META_BG, ExtGeometry, raw_crc32c)
from .extresize import (INO_RESIZE, MIN_LAST_GROUP_FREE, ExtResizeError,
                        _build_resize_inode, _set_padding, _Volume,
                        _write_gdt, _write_superblocks, _zero_blocks)
from .image import BlockDevice
from ..i18n import tr

# inode alanlari
I_MODE, I_SIZE, I_BLOCKS, I_FLAGS, I_BLOCK = 0x00, 0x04, 0x1C, 0x20, 0x28
I_GENERATION, I_FILE_ACL, I_FILE_ACL_HI = 0x64, 0x68, 0x76
S_IFMT, S_IFDIR, S_IFREG, S_IFLNK = 0xF000, 0x4000, 0x8000, 0xA000
EXTENTS_FL = 0x00080000
INDEX_FL = 0x00001000
HUGE_FILE_FL = 0x00040000
INLINE_DATA_FL = 0x10000000
EA_INODE_FL = 0x00200000
EXT_MAGIC = 0xF30A
MAX_INIT_LEN = 32768
MAX_UNINIT_LEN = 32767

INCOMPAT_EA_INODE = 0x0400
INCOMPAT_INLINE_DATA = 0x8000
RO_HUGE_FILE = 0x0008
RO_QUOTA = 0x0100
RO_PROJECT = 0x2000

# ustbloktaki inode numarasi alanlari
SB_INODE_FIELDS = (0xE0, 0x240, 0x244, 0x268, 0x26C, 0x280)
SB_JNL_BLOCKS = 0x10C
SB_JNL_BACKUP_TYPE = 0xFD
ORPHAN_MAGIC = 0x0B10CA04

Progress = Optional[Callable[[str, int], None]]


# ======================================================================
# Extent agaci
# ======================================================================
def _ext_header(data: bytes, off: int = 0) -> Tuple[int, int, int, int]:
    magic, entries, maximum, depth = struct.unpack_from("<HHHH", data, off)
    if magic != EXT_MAGIC:
        raise ExtResizeError(tr("Extent basligi bozuk"))
    return entries, maximum, depth, off + 12


def read_extents(vol: _Volume, i_block: bytes
                 ) -> Tuple[List[Tuple[int, int, int, bool]], List[int]]:
    """(yapraklar[(mantiksal, uzunluk, fiziksel, baslatilmamis)], dugum_bloklari)."""
    leaves: List[Tuple[int, int, int, bool]] = []
    nodes: List[int] = []

    def walk(buf: bytes, off: int) -> None:
        entries, _max, depth, pos = _ext_header(buf, off)
        for i in range(entries):
            e = pos + i * 12
            if depth == 0:
                lblk, length, hi, lo = struct.unpack_from("<IHHI", buf, e)
                uninit = length > MAX_INIT_LEN
                if uninit:
                    length -= MAX_INIT_LEN
                leaves.append((lblk, length, (hi << 32) | lo, uninit))
            else:
                _lblk, lo, hi = struct.unpack_from("<IIH", buf, e)
                child = (hi << 32) | lo
                nodes.append(child)
                walk(vol.read_block(child), 0)

    walk(i_block, 0)
    return leaves, nodes


def _pack_leaf(entries: List[Tuple[int, int, int, bool]]) -> bytes:
    out = bytearray()
    for lblk, length, pblk, uninit in entries:
        stored = length + (MAX_INIT_LEN if uninit else 0)
        out += struct.pack("<IHHI", lblk, stored, pblk >> 32, pblk & 0xFFFFFFFF)
    return bytes(out)


def _pack_index(entries: List[Tuple[int, int]]) -> bytes:
    out = bytearray()
    for lblk, child in entries:
        out += struct.pack("<IIHH", lblk, child & 0xFFFFFFFF, child >> 32, 0)
    return bytes(out)


def build_extent_tree(vol: _Volume, ino: int, generation: int,
                      extents: List[Tuple[int, int, int, bool]],
                      alloc: Callable[[], int]) -> Tuple[bytes, List[int]]:
    """Yaprak listesinden agac kurar. (i_block[60 bayt], yeni_dugum_bloklari).

    Kok (inode icinde) en fazla 4 giris tasir; asarsa yaprak bloklari, o da
    asarsa ara dizin bloklari eklenir. Her blok `ext4_extent_tail`
    saglamasiyla muhurlenir.
    """
    bs = vol.bs
    per_block = (bs - 12) // 12
    new_nodes: List[int] = []
    level_entries: List = list(extents)
    depth = 0
    while len(level_entries) > 4:
        parents: List[Tuple[int, int]] = []
        for i in range(0, len(level_entries), per_block):
            chunk = level_entries[i:i + per_block]
            blk = alloc()
            new_nodes.append(blk)
            buf = bytearray(bs)
            struct.pack_into("<HHHHI", buf, 0, EXT_MAGIC, len(chunk), per_block,
                             depth, 0)
            body = _pack_leaf(chunk) if depth == 0 else _pack_index(chunk)
            buf[12:12 + len(body)] = body
            _stamp_extent_block(vol, ino, generation, buf, per_block)
            vol.write_block(blk, buf)
            parents.append((chunk[0][0], blk))
        level_entries = parents
        depth += 1
    root = bytearray(60)
    struct.pack_into("<HHHHI", root, 0, EXT_MAGIC, len(level_entries), 4, depth, 0)
    body = _pack_leaf(level_entries) if depth == 0 else _pack_index(level_entries)
    root[12:12 + len(body)] = body
    return bytes(root), new_nodes


def _inode_seed(vol: _Volume, ino: int, generation: int) -> int:
    crc = raw_crc32c(vol.csum.seed, struct.pack("<I", ino))
    return raw_crc32c(crc, struct.pack("<I", generation))


def _stamp_extent_block(vol: _Volume, ino: int, generation: int,
                        buf: bytearray, maximum: int) -> None:
    if vol.csum.kind != "crc32c":
        return
    tail = 12 + 12 * maximum
    crc = raw_crc32c(_inode_seed(vol, ino, generation), bytes(buf[:tail]))
    struct.pack_into("<I", buf, tail, crc)


def _restamp_tree(vol: _Volume, ino: int, generation: int, nodes: List[int]) -> None:
    if vol.csum.kind != "crc32c":
        return
    for blk in nodes:
        buf = vol.read_block(blk)
        _e, maximum, _d, _p = _ext_header(buf)
        _stamp_extent_block(vol, ino, generation, buf, maximum)
        vol.write_block(blk, buf)


# ======================================================================
# Blok tahsisi (kalan bolgede, bellek ici bitmap'lerle)
# ======================================================================
class _Allocator:
    def __init__(self, vol: _Volume, new_geo: ExtGeometry, keep_groups: int):
        self.vol = vol
        self.geo = new_geo
        self.keep_groups = keep_groups
        self.maps: Dict[int, bytearray] = {}
        self.dirty: Set[int] = set()
        self.cursor = 0

    def bitmap(self, group: int) -> bytearray:
        bm = self.maps.get(group)
        if bm is None:
            bm = self.vol.block_bitmap(group)
            # Kesilen son grubun sinirin otesi dolu sayilir (dolgu).
            _set_padding(bm, self.geo.group_block_count(group), self.vol.bs * 8)
            self.maps[group] = bm
        return bm

    def _locate(self, block: int) -> Tuple[int, int]:
        return divmod(block - self.geo.first_data_block, self.geo.blocks_per_group)

    def is_used(self, block: int) -> bool:
        g, i = self._locate(block)
        return bool(self.bitmap(g)[i >> 3] & (1 << (i & 7)))

    def mark(self, block: int, used: bool) -> None:
        g, i = self._locate(block)
        if g >= self.keep_groups or block >= self.geo.blocks_count:
            return                      # kesilen bolge: iz tutulmaz
        bm = self.bitmap(g)
        if used:
            bm[i >> 3] |= 1 << (i & 7)
        else:
            bm[i >> 3] &= ~(1 << (i & 7)) & 0xFF
        self.dirty.add(g)

    def alloc_run(self, want: int) -> Tuple[int, int]:
        """En fazla `want` uzunlugunda bitisik bos aralik: (baslangic, uzunluk)."""
        geo = self.geo
        for step in range(self.keep_groups):
            g = (self.cursor + step) % self.keep_groups
            bm = self.bitmap(g)
            limit = geo.group_block_count(g)
            i = _find_zero(bm, 0, limit)
            while i is not None:
                j = i
                while j < limit and j - i < want and not bm[j >> 3] & (1 << (j & 7)):
                    j += 1
                length = j - i
                if length:
                    start = geo.group_first_block(g) + i
                    for b in range(i, j):
                        bm[b >> 3] |= 1 << (b & 7)
                    self.dirty.add(g)
                    self.cursor = g
                    return start, length
                i = _find_zero(bm, j, limit)
        raise ExtResizeError(tr("Kucultme icin yeterli bos alan yok"))

    def alloc(self) -> int:
        return self.alloc_run(1)[0]

    def alloc_contiguous(self, count: int) -> int:
        """Tam `count` bitisik blok (inode tablosu icin)."""
        geo = self.geo
        for g in range(self.keep_groups):
            bm = self.bitmap(g)
            limit = geo.group_block_count(g)
            i = _find_zero(bm, 0, limit)
            while i is not None:
                j = i
                while j < limit and j - i < count and not bm[j >> 3] & (1 << (j & 7)):
                    j += 1
                if j - i == count:
                    for b in range(i, j):
                        bm[b >> 3] |= 1 << (b & 7)
                    self.dirty.add(g)
                    return geo.group_first_block(g) + i
                i = _find_zero(bm, j, limit)
        raise ExtResizeError(tr("Inode tablosu icin bitisik bos alan yok"))


def _find_zero(bm: bytearray, start: int, limit: int) -> Optional[int]:
    """`start`tan itibaren ilk sifir bit (bayt duzeyinde hizli atlama)."""
    i = start
    while i < limit:
        if i & 7 == 0:
            byte_index = i >> 3
            end_byte = (limit + 7) >> 3
            while byte_index < end_byte and bm[byte_index] == 0xFF:
                byte_index += 1
            i = byte_index << 3
            if i >= limit:
                return None
        if not bm[i >> 3] & (1 << (i & 7)):
            return i
        i += 1
    return None


# ======================================================================
# Kucultucu
# ======================================================================
class _Shrinker:
    def __init__(self, dev: BlockDevice, target_blocks: int, progress: Progress):
        self.dev = dev
        self.progress = progress
        self.vol = _Volume(dev)
        self.vol.check_supported()
        v = self.vol
        g = v.geo
        self.bs = g.block_size
        self.old_blocks = g.blocks_count
        self.old_groups = g.group_count
        self.ipg = g.inodes_per_group
        self.itb = g.inode_table_blocks
        self.huge = bool(g.ro_compat & RO_HUGE_FILE)

        self.new = ExtGeometry(bytes(g.sb))
        self.new.desc_size = g.desc_size
        self.new.set_blocks_count(target_blocks)
        self.target = target_blocks
        self.keep = self.new.group_count
        self.new_desc_blocks = self.new.desc_blocks
        # GDT kuculunce: resize_inode varsa bosalan bloklar ayrilmis alana
        # eklenir (klasik alanin boyu sabit kalir), yoksa serbest kalir.
        self.freed_gdt: List[int] = []
        if not self.new.meta_bg:
            shrink_by = g.desc_blocks - self.new_desc_blocks
            if shrink_by > 0:
                # Ayrilmis GDT sayisi blok basina adres sayisini asamaz
                # (e2fsck: "reserved_gdt_blocks = 257" -> ustblok bozuk).
                # Asan kisim serbest birakilir.
                if g.compat & COMPAT_RESIZE_INODE:
                    self.new.reserved_gdt = min(g.reserved_gdt + shrink_by,
                                                g.block_size // 4)
                keep_len = self.new_desc_blocks + self.new.reserved_gdt
                old_len = g.desc_blocks + g.reserved_gdt
                for gno in range(self.keep):
                    _s, os_, _oc, _n = g.base_meta(gno)
                    if os_ and old_len > keep_len:
                        self.freed_gdt.extend(range(os_ + keep_len, os_ + old_len))
        self.alloc = _Allocator(v, self.new, self.keep)
        self.ino_map: Dict[int, int] = {}
        self.block_moves: Dict[int, int] = {}
        self.inode_extents: Dict[int, List[int]] = {}

    def report(self, msg: str, pct: int) -> None:
        if self.progress:
            self.progress(msg, pct)

    # ---- yardimcilar -------------------------------------------------
    def in_cut(self, block: int) -> bool:
        return block >= self.target

    def inode_offset(self, ino: int) -> int:
        gno, idx = divmod(ino - 1, self.ipg)
        return self.vol.d(gno, GD_INODE_TABLE) * self.bs + idx * self.vol.geo.inode_size

    def read_inode(self, ino: int) -> bytearray:
        return bytearray(self.dev.read(self.inode_offset(ino), self.vol.geo.inode_size))

    def write_inode(self, ino: int, raw: bytearray, location_ino: Optional[int] = None) -> None:
        self.vol.csum.stamp_inode(ino, raw, self.vol.geo.inode_size)
        self.dev.write(self.inode_offset(location_ino or ino), bytes(raw))

    def used_inodes(self) -> List[int]:
        out = []
        v = self.vol
        for gno in range(self.old_groups):
            if v.d(gno, GD_FLAGS) & BG_INODE_UNINIT and v.geo.csum_kind:
                continue
            bm = v.read_block(v.d(gno, GD_INODE_BITMAP))
            limit = self.ipg
            if v.geo.csum_kind:
                limit = self.ipg - v.d(gno, GD_ITABLE_UNUSED)
            for i in range(limit):
                if bm[i >> 3] & (1 << (i & 7)):
                    out.append(gno * self.ipg + i + 1)
        return out

    def copy_blocks(self, src: int, dst: int, count: int) -> None:
        chunk = max(1, (4 * 1024 * 1024) // self.bs)
        pos = 0
        while pos < count:
            n = min(chunk, count - pos)
            data = self.dev.read((src + pos) * self.bs, n * self.bs)
            self.dev.write((dst + pos) * self.bs, data)
            pos += n

    # ---- denetim -----------------------------------------------------
    def check(self) -> None:
        g = self.vol.geo
        if g.incompat & (INCOMPAT_INLINE_DATA | INCOMPAT_EA_INODE):
            raise ExtResizeError(tr(
                "inline_data / ea_inode kullanan ext birimi kucultulemez"))
        last = self.keep - 1
        overhead = self.new.base_meta_count(last) + 2 + self.itb
        if last and self.new.group_block_count(last) <= overhead:
            raise ExtResizeError(tr("Son grup metaveri icin cok kucuk"))

    # ---- 1) inode yeniden numaralama --------------------------------
    def plan_inodes(self, used: List[int]) -> None:
        limit = self.keep * self.ipg
        movers = [ino for ino in used if ino > limit]
        if not movers:
            return
        g = self.vol.geo
        if g.ro_compat & (RO_QUOTA | RO_PROJECT):
            raise ExtResizeError(tr(
                "Kotali (quota) ext biriminde inode tasinamaz; birimi "
                "silinecek gruplarda dosya kalmayacak boyuta kucultun"))
        v = self.vol
        free: List[int] = []
        need = len(movers)
        first_ino = struct.unpack_from("<I", g.sb, 0x54)[0] or 11
        for gno in range(self.keep):
            if len(free) >= need:
                break
            flags = v.d(gno, GD_FLAGS)
            if flags & BG_INODE_UNINIT and g.csum_kind:
                if not flags & BG_INODE_ZEROED:
                    _zero_blocks(self.dev, v.d(gno, GD_INODE_TABLE), self.itb, self.bs)
                    v.set_d(gno, GD_FLAGS, flags | BG_INODE_ZEROED)
                bm = bytearray(self.bs)
                _set_padding(bm, self.ipg, self.bs * 8)
                v.set_d(gno, GD_FLAGS, v.d(gno, GD_FLAGS) & ~BG_INODE_UNINIT)
                v.set_d(gno, GD_ITABLE_UNUSED, self.ipg)
                v.write_block(v.d(gno, GD_INODE_BITMAP), bm)
                v.csum.stamp_bitmap(v.descs[gno], bm[:(self.ipg + 7) // 8], True,
                                    g.desc_size)
            bm = v.read_block(v.d(gno, GD_INODE_BITMAP))
            for i in range(self.ipg):
                ino = gno * self.ipg + i + 1
                if ino < first_ino:
                    continue
                if not bm[i >> 3] & (1 << (i & 7)):
                    free.append(ino)
                    if len(free) >= need:
                        break
        if len(free) < need:
            raise ExtResizeError(tr("Kalan gruplarda yeterli bos inode yok"))
        for old, new in zip(movers, free):
            self.ino_map[old] = new

    # ---- 2) kalan gruplarin metaverisi -------------------------------
    def relocate_group_metadata(self) -> None:
        v = self.vol
        for gno in range(self.keep):
            for field, count in ((GD_BLOCK_BITMAP, 1), (GD_INODE_BITMAP, 1),
                                 (GD_INODE_TABLE, self.itb)):
                first = v.d(gno, field)
                if first + count <= self.target:
                    continue
                if count == 1:
                    new = self.alloc.alloc()
                else:
                    new = self.alloc.alloc_contiguous(count)
                self.copy_blocks(first, new, count)
                for b in range(first, first + count):
                    self.alloc.mark(b, False)
                v.set_d(gno, field, new)

    # ---- 3) veri bloklari --------------------------------------------
    def move_extent_file(self, ino: int, raw: bytearray) -> bool:
        leaves, nodes = read_extents(self.vol, bytes(raw[I_BLOCK:I_BLOCK + 60]))
        needs = any(self.in_cut(b) for b in nodes) or any(
            p + n > self.target for _l, n, p, _u in leaves)
        if not needs:
            self.inode_extents[ino] = nodes
            return False
        new_leaves: List[Tuple[int, int, int, bool]] = []
        for lblk, length, pblk, uninit in leaves:
            done = 0
            while done < length:
                seg_start = pblk + done
                if not self.in_cut(seg_start):
                    # kalan bolgede baslayan parca: sinira kadar oldugu gibi
                    keep_len = min(length - done, self.target - seg_start)
                    new_leaves.append((lblk + done, keep_len, seg_start, uninit))
                    done += keep_len
                    continue
                start, got = self.alloc.alloc_run(length - done)
                if not uninit:
                    self.copy_blocks(seg_start, start, got)
                new_leaves.append((lblk + done, got, start, uninit))
                for b in range(seg_start, seg_start + got):
                    self.block_moves[b] = start + (b - seg_start)
                done += got
        new_leaves = _merge_extents(new_leaves)
        for blk in nodes:
            self.alloc.mark(blk, False)
        generation = struct.unpack_from("<I", raw, I_GENERATION)[0]
        final_ino = self.ino_map.get(ino, ino)
        root, new_nodes = build_extent_tree(self.vol, final_ino, generation,
                                            new_leaves, self.alloc.alloc)
        raw[I_BLOCK:I_BLOCK + 60] = root
        self._adjust_iblocks(raw, len(new_nodes) - len(nodes))
        self.inode_extents[ino] = new_nodes
        return True

    def _adjust_iblocks(self, raw: bytearray, delta_blocks: int) -> None:
        if not delta_blocks:
            return
        lo = struct.unpack_from("<I", raw, I_BLOCKS)[0]
        hi = struct.unpack_from("<H", raw, 0x74)[0]
        flags = struct.unpack_from("<I", raw, I_FLAGS)[0]
        unit = 1 if (self.huge and flags & HUGE_FILE_FL) else self.bs // 512
        value = ((hi << 32) | lo) + delta_blocks * unit
        struct.pack_into("<I", raw, I_BLOCKS, value & 0xFFFFFFFF)
        struct.pack_into("<H", raw, 0x74, (value >> 32) & 0xFFFF)

    def move_indirect_file(self, raw: bytearray) -> bool:
        changed = False

        def relocate(block: int) -> int:
            new = self.alloc.alloc()
            self.copy_blocks(block, new, 1)
            self.block_moves[block] = new
            return new

        def table(block: int, level: int) -> int:
            nonlocal changed
            buf = self.vol.read_block(block)
            dirty = False
            for i in range(self.bs // 4):
                ptr = struct.unpack_from("<I", buf, i * 4)[0]
                if not ptr:
                    continue
                new = table(ptr, level - 1) if level > 1 else (
                    relocate(ptr) if self.in_cut(ptr) else ptr)
                if new != ptr:
                    struct.pack_into("<I", buf, i * 4, new)
                    dirty = True
            target = block
            if self.in_cut(block):
                target = self.alloc.alloc()
                self.block_moves[block] = target
                dirty = True
            if dirty:
                changed = True
                self.vol.write_block(target, buf)
            return target

        for i in range(15):
            off = I_BLOCK + i * 4
            ptr = struct.unpack_from("<I", raw, off)[0]
            if not ptr:
                continue
            if i < 12:
                new = relocate(ptr) if self.in_cut(ptr) else ptr
            else:
                new = table(ptr, i - 11)
            if new != ptr:
                struct.pack_into("<I", raw, off, new)
                changed = True
        return changed

    def move_xattr_block(self, raw: bytearray) -> bool:
        acl = struct.unpack_from("<I", raw, I_FILE_ACL)[0] | \
            (struct.unpack_from("<H", raw, I_FILE_ACL_HI)[0] << 32)
        if not acl or not self.in_cut(acl):
            return False
        new = self.block_moves.get(acl)
        if new is None:
            new = self.alloc.alloc()
            buf = self.vol.read_block(acl)
            if self.vol.csum.kind == "crc32c":
                crc = raw_crc32c(self.vol.csum.seed, struct.pack("<Q", new))
                crc = raw_crc32c(crc, bytes(buf[:0x10]))
                crc = raw_crc32c(crc, b"\x00\x00\x00\x00")
                crc = raw_crc32c(crc, bytes(buf[0x14:]))
                struct.pack_into("<I", buf, 0x10, crc)
            self.vol.write_block(new, buf)
            self.block_moves[acl] = new
        struct.pack_into("<I", raw, I_FILE_ACL, new & 0xFFFFFFFF)
        struct.pack_into("<H", raw, I_FILE_ACL_HI, new >> 32)
        return True

    @staticmethod
    def has_block_map(raw: bytes) -> bool:
        mode = struct.unpack_from("<H", raw, I_MODE)[0]
        kind = mode & S_IFMT
        if kind not in (S_IFREG, S_IFDIR, S_IFLNK):
            return False                  # aygit, fifo, soket
        flags = struct.unpack_from("<I", raw, I_FLAGS)[0]
        if kind == S_IFLNK and not flags & EXTENTS_FL:
            size = struct.unpack_from("<I", raw, I_SIZE)[0]
            if size < 60:
                return False              # hizli sembolik bag
        return True

    # ---- 4) dizin girisleri ------------------------------------------
    def dir_blocks(self, raw: bytes) -> List[Tuple[int, int]]:
        """(mantiksal, fiziksel) — dizin bloklari."""
        flags = struct.unpack_from("<I", raw, I_FLAGS)[0]
        out = []
        if flags & EXTENTS_FL:
            leaves, _n = read_extents(self.vol, bytes(raw[I_BLOCK:I_BLOCK + 60]))
            for lblk, length, pblk, _u in leaves:
                for k in range(length):
                    out.append((lblk + k, pblk + k))
            return out
        size = struct.unpack_from("<I", raw, I_SIZE)[0]
        count = (size + self.bs - 1) // self.bs
        ptrs = self._indirect_list(raw, count)
        return [(i, p) for i, p in enumerate(ptrs) if p]

    def _indirect_list(self, raw: bytes, count: int) -> List[int]:
        out = list(struct.unpack_from("<12I", raw, I_BLOCK))
        per = self.bs // 4

        def expand(block: int, level: int) -> List[int]:
            if not block:
                return [0] * min(per ** level, max(0, count - len(out)))
            buf = self.vol.read_block(block)
            ptrs = list(struct.unpack_from("<%dI" % per, buf))
            if level == 1:
                return ptrs
            res: List[int] = []
            for p in ptrs:
                res.extend(expand(p, level - 1))
                if len(res) + 12 >= count:
                    break
            return res

        for i, level in ((12, 1), (13, 2), (14, 3)):
            if len(out) >= count:
                break
            ptr = struct.unpack_from("<I", raw, I_BLOCK + i * 4)[0]
            out.extend(expand(ptr, level))
        return out[:count]

    def fix_directory(self, dir_ino: int, raw: bytes) -> None:
        final_ino = self.ino_map.get(dir_ino, dir_ino)
        generation = struct.unpack_from("<I", raw, I_GENERATION)[0]
        flags = struct.unpack_from("<I", raw, I_FLAGS)[0]
        blocks = self.dir_blocks(raw)
        dx_nodes: Dict[int, int] = {}
        if flags & INDEX_FL and blocks:
            dx_nodes = self._dx_nodes(blocks)
        moved_dir = final_ino != dir_ino
        csum = self.vol.csum.kind == "crc32c"
        seed = _inode_seed(self.vol, final_ino, generation) if csum else 0
        for lblk, pblk in blocks:
            buf = self.vol.read_block(pblk)
            dirty = moved_dir
            if lblk in dx_nodes:
                count_off = dx_nodes[lblk]
                if count_off == 0x20:          # dx_root: '.' ve '..'
                    for off in (0, 12):
                        ino = struct.unpack_from("<I", buf, off)[0]
                        if ino in self.ino_map:
                            struct.pack_into("<I", buf, off, self.ino_map[ino])
                            dirty = True
                if dirty and csum:
                    limit, count = struct.unpack_from("<HH", buf, count_off)
                    tail = count_off + limit * 8
                    if tail + 8 <= self.bs:
                        crc = raw_crc32c(seed, bytes(buf[:count_off + count * 8]))
                        crc = raw_crc32c(crc, bytes(buf[tail:tail + 4]))
                        # cekirdek ext4_dx_csum: dt_reserved'dan sonra sifir
                        # sayilan dt_checksum alani da katilir
                        crc = raw_crc32c(crc, b"\x00\x00\x00\x00")
                        struct.pack_into("<I", buf, tail + 4, crc)
            else:
                pos = 0
                end = self.bs
                has_tail = csum and _is_dirent_tail(buf, self.bs)
                if has_tail:
                    end = self.bs - 12
                while pos + 8 <= end:
                    ino, rec_len = struct.unpack_from("<IH", buf, pos)
                    if rec_len < 8:
                        break
                    if ino in self.ino_map:
                        struct.pack_into("<I", buf, pos, self.ino_map[ino])
                        dirty = True
                    pos += rec_len
                if dirty and has_tail:
                    crc = raw_crc32c(seed, bytes(buf[:self.bs - 12]))
                    struct.pack_into("<I", buf, self.bs - 4, crc)
            if dirty:
                self.vol.write_block(pblk, buf)

    def _dx_nodes(self, blocks: List[Tuple[int, int]]) -> Dict[int, int]:
        """htree dugumlerinin mantiksal numarasi -> sayac ofseti."""
        phys = dict(blocks)
        if 0 not in phys:
            return {}
        root = self.vol.read_block(phys[0])
        levels = root[0x1E]
        nodes = {0: 0x20}
        frontier = [(0, 0x20)]
        for _lvl in range(levels):
            nxt = []
            for lblk, off in frontier:
                buf = root if lblk == 0 else self.vol.read_block(phys[lblk])
                _limit, count = struct.unpack_from("<HH", buf, off)
                first = struct.unpack_from("<I", buf, off + 4)[0]
                children = [first] + [struct.unpack_from("<I", buf, off + 8 * k + 4)[0]
                                      for k in range(1, count)]
                for child in children:
                    if child in phys and child not in nodes:
                        nodes[child] = 8
                        nxt.append((child, 8))
            frontier = nxt
        return nodes

    # ---- ana akis ----------------------------------------------------
    def run(self) -> int:
        self.check()
        v = self.vol
        self.report(tr("Kullanilan inode'lar taraniyor..."), 3)
        used = self.used_inodes()
        self.plan_inodes(used)

        # Tasinacak inode'lar silinecek gruplarin tablolarindadir; o tablolar
        # serbest birakilmadan once hepsi bellege alinir.
        self.mover_raw = {ino: self.read_inode(ino) for ino in self.ino_map}
        self.report(tr("Grup metaverisi yerlestiriliyor..."), 8)
        self.relocate_group_metadata()
        self.release_removed_metadata()

        # Veri tasima
        total = max(1, len(used))
        dirs: List[int] = []
        rewritten: Dict[int, bytearray] = {}
        jnl_ino = struct.unpack_from("<I", v.geo.sb, 0xE0)[0] \
            if v.geo.compat & 0x0004 else 0
        orphan_ino = struct.unpack_from("<I", v.geo.sb, 0x280)[0] \
            if v.geo.compat & 0x1000 else 0
        for n, ino in enumerate(used):
            if n % 256 == 0:
                self.report(tr("Veri tasiniyor... {} / {}", n, total),
                            10 + int(60 * n / total))
            if ino == INO_RESIZE:
                continue
            raw = self.mover_raw.get(ino) or self.read_inode(ino)
            mode = struct.unpack_from("<H", raw, I_MODE)[0]
            links = struct.unpack_from("<H", raw, 0x1A)[0]
            if mode == 0 and links == 0 and ino >= 11:
                continue
            flags = struct.unpack_from("<I", raw, I_FLAGS)[0]
            if flags & INLINE_DATA_FL:
                raise ExtResizeError(tr("inline_data inode'u: {}", ino))
            changed = False
            if self.has_block_map(raw):
                if flags & EXTENTS_FL:
                    changed = self.move_extent_file(ino, raw)
                else:
                    changed = self.move_indirect_file(raw)
                if ino == 1 and changed:
                    raise ExtResizeError(tr("Kesilen bolgede kotu blok kaydi var"))
            changed = self.move_xattr_block(raw) or changed
            if ino == orphan_ino and (changed or ino in self.ino_map):
                self._restamp_orphan_file(ino, raw)
            if changed or ino in self.ino_map:
                rewritten[ino] = raw
            if (mode & S_IFMT) == S_IFDIR:
                dirs.append(ino)
            if ino == jnl_ino and changed:
                self._backup_journal_blocks(raw)

        # Inode'lari yaz: tasinanlar yeni yerine, digerleri yerinde
        self.report(tr("Inode'lar yaziliyor..."), 72)
        for ino, raw in rewritten.items():
            new_ino = self.ino_map.get(ino)
            if new_ino:
                self._place_inode(ino, new_ino, raw)
            else:
                self.write_inode(ino, raw)
        # Numarasi degisen ama agaci degismeyen inode'larin extent dugumleri
        for ino, new_ino in self.ino_map.items():
            nodes = self.inode_extents.get(ino)
            if nodes and all(not self.in_cut(b) for b in nodes):
                generation = struct.unpack_from("<I", rewritten[ino], I_GENERATION)[0]
                _restamp_tree(v, new_ino, generation, nodes)

        # Dizin girisleri
        self.report(tr("Dizin girisleri duzeltiliyor..."), 80)
        if self.ino_map:
            for ino in dirs:
                raw = rewritten.get(ino) or self.read_inode(ino)
                self.fix_directory(ino, raw)
            for off in SB_INODE_FIELDS:
                value = struct.unpack_from("<I", v.geo.sb, off)[0]
                if value in self.ino_map:
                    struct.pack_into("<I", v.geo.sb, off, self.ino_map[value])

        self.report(tr("Grup tanimlayicilari guncelleniyor..."), 88)
        self._finish()
        return self.target

    def _backup_journal_blocks(self, raw: bytes) -> None:
        sb = self.vol.geo.sb
        if sb[SB_JNL_BACKUP_TYPE] != 1:
            return
        sb[SB_JNL_BLOCKS:SB_JNL_BLOCKS + 60] = raw[I_BLOCK:I_BLOCK + 60]
        struct.pack_into("<I", sb, SB_JNL_BLOCKS + 60,
                         struct.unpack_from("<I", raw, 0x6C)[0])
        struct.pack_into("<I", sb, SB_JNL_BLOCKS + 64,
                         struct.unpack_from("<I", raw, I_SIZE)[0])

    def _restamp_orphan_file(self, ino: int, raw: bytes) -> None:
        if self.vol.csum.kind != "crc32c":
            return
        final = self.ino_map.get(ino, ino)
        generation = struct.unpack_from("<I", raw, I_GENERATION)[0]
        per = (self.bs - 8) // 4
        leaves, _n = read_extents(self.vol, bytes(raw[I_BLOCK:I_BLOCK + 60]))
        for _l, length, pblk, _u in leaves:
            for blk in range(pblk, pblk + length):
                buf = self.vol.read_block(blk)
                if struct.unpack_from("<I", buf, self.bs - 8)[0] != ORPHAN_MAGIC:
                    continue
                crc = raw_crc32c(self.vol.csum.seed, struct.pack("<I", final))
                crc = raw_crc32c(crc, struct.pack("<I", generation))
                crc = raw_crc32c(crc, struct.pack("<Q", blk))
                crc = raw_crc32c(crc, bytes(buf[:per * 4]))
                struct.pack_into("<I", buf, self.bs - 4, crc)
                self.vol.write_block(blk, buf)

    def release_removed_metadata(self) -> None:
        """Silinecek gruplarin kalan bolgedeki metaverisini serbest birakir.

        flex_bg'de 16 grubun inode tablolari ilk grupta durur; buyuk bir
        kucultmede bu alan (4K blokta ~32 MB) tasima icin gereklidir. Guvenli
        olmasi icin tasinacak inode'larin hepsi ONCEDEN belleğe okunmustur
        (`mover_raw`); bu tablolardan bir daha okunmaz.
        """
        v = self.vol
        for gno in range(self.keep, self.old_groups):
            for field, count in ((GD_BLOCK_BITMAP, 1), (GD_INODE_BITMAP, 1),
                                 (GD_INODE_TABLE, self.itb)):
                first = v.d(gno, field)
                for b in range(first, first + count):
                    if not self.in_cut(b):
                        self.alloc.mark(b, False)
        for b in self.freed_gdt:
            self.alloc.mark(b, False)

    def _place_inode(self, old: int, new: int, raw: bytearray) -> None:
        v = self.vol
        gno, idx = divmod(new - 1, self.ipg)
        bm_block = v.d(gno, GD_INODE_BITMAP)
        bm = v.read_block(bm_block)
        bm[idx >> 3] |= 1 << (idx & 7)
        v.write_block(bm_block, bm)
        v.csum.stamp_bitmap(v.descs[gno], bm[:(self.ipg + 7) // 8], True,
                            v.geo.desc_size)
        v.set_d(gno, GD_FREE_INODES, v.d(gno, GD_FREE_INODES) - 1)
        mode = struct.unpack_from("<H", raw, I_MODE)[0]
        if (mode & S_IFMT) == S_IFDIR:
            v.set_d(gno, GD_USED_DIRS, v.d(gno, GD_USED_DIRS) + 1)
        if v.geo.csum_kind:
            unused = v.d(gno, GD_ITABLE_UNUSED)
            v.set_d(gno, GD_ITABLE_UNUSED, min(unused, self.ipg - idx - 1))
        self.write_inode(new, raw)

    def _finish(self) -> None:
        v = self.vol
        new = self.new
        bs = self.bs
        # Kalan gruplarin bitmap'leri yazilir, sayaclar bitmap'ten sayilir.
        for gno in range(self.keep):
            if gno == self.keep - 1 or gno in self.alloc.dirty:
                bm = self.alloc.bitmap(gno)
                length = new.group_block_count(gno)
                _set_padding(bm, length, bs * 8)
                free = _zero_bits(bm, length)
                v.set_d(gno, GD_FREE_BLOCKS, free)
                v.set_d(gno, GD_FLAGS, v.d(gno, GD_FLAGS) & ~BG_BLOCK_UNINIT)
                v.write_block(v.d(gno, GD_BLOCK_BITMAP), bm)
                v.csum.stamp_bitmap(v.descs[gno], bm[:new.blocks_per_group // 8],
                                    False, new.desc_size)
        del v.descs[self.keep:]

        old_r = v.sb_u64(0x08, 0x154)
        old_geo = v.geo
        new.sb[:] = old_geo.sb                    # inode alani duzeltmeleri dahil
        v.geo = new
        struct.pack_into("<H", new.sb, 0xCE, new.reserved_gdt)
        v.sb_set_u64(0x04, 0x150, self.target)
        v.sb_set_u64(0x08, 0x154, old_r * self.target // max(1, self.old_blocks))
        v.sb_set_u64(0x0C, 0x158, sum(v.d(i, GD_FREE_BLOCKS) for i in range(self.keep)))
        v.sb_set_u32(0x10, sum(v.d(i, GD_FREE_INODES) for i in range(self.keep)))
        v.sb_set_u32(0x00, self.ipg * self.keep)
        if new.compat & COMPAT_RESIZE_INODE:
            _build_resize_inode(v)
        _write_gdt(v)
        _write_superblocks(v)
        flush = getattr(self.dev, "flush", None)
        if flush:
            flush()


def _merge_extents(extents: List[Tuple[int, int, int, bool]]
                   ) -> List[Tuple[int, int, int, bool]]:
    out: List[Tuple[int, int, int, bool]] = []
    for lblk, length, pblk, uninit in extents:
        if out:
            pl, plen, pp, pu = out[-1]
            cap = MAX_UNINIT_LEN if uninit else MAX_INIT_LEN
            if pu == uninit and pl + plen == lblk and pp + plen == pblk \
                    and plen + length <= cap:
                out[-1] = (pl, plen + length, pp, pu)
                continue
        out.append((lblk, length, pblk, uninit))
    return out


def _zero_bits(bm: bytes, nbits: int) -> int:
    full, rest = divmod(nbits, 8)
    ones = sum(bin(b).count("1") for b in bm[:full])
    if rest:
        ones += bin(bm[full] & ((1 << rest) - 1)).count("1")
    return nbits - ones


def _is_dirent_tail(buf: bytes, bs: int) -> bool:
    pos = bs - 12
    ino, rec_len, name_len, ftype = struct.unpack_from("<IHBB", buf, pos)
    return ino == 0 and rec_len == 12 and name_len == 0 and ftype == 0xDE


# ======================================================================
# Genel arayuz
# ======================================================================
def min_blocks_for(vol: _Volume) -> int:
    """Kucultmenin inebilecegi yaklasik en kucuk blok sayisi.

    Gereken = kullanilan veri bloklari (metaveri disinda) + tasima payi.
    Kullanilan inode'lar da kalan gruplara sigmali. Pay: tasinan dosyalarin
    yeni extent dugumleri ve parcalanma icin %2 + 256 blok.
    """
    g = vol.geo
    itb = g.inode_table_blocks
    meta_total = sum(g.base_meta_count(gno) + 2 + itb for gno in range(g.group_count))
    free = vol.sb_u64(0x0C, 0x158)
    data = max(0, g.blocks_count - free - meta_total)
    need = data + data // 50 + 256
    used_inodes = struct.unpack_from("<I", g.sb, 0x00)[0] - \
        struct.unpack_from("<I", g.sb, 0x10)[0]
    min_groups = max(1, (used_inodes + g.inodes_per_group - 1) // g.inodes_per_group)
    capacity = 0
    for gno in range(g.group_count):
        overhead = g.base_meta_count(gno) + 2 + itb
        room = g.blocks_per_group - overhead
        if gno + 1 >= min_groups and capacity + room >= need:
            last_needed = overhead + max(need - capacity, MIN_LAST_GROUP_FREE + 1)
            return min(g.blocks_count, g.group_first_block(gno) + last_needed)
        capacity += room
    return g.blocks_count


def ext_shrink(dev: BlockDevice, new_size_bytes: int,
               progress: Progress = None) -> int:
    """ext birimini `new_size_bytes`a kucultur. `dev` ESKI boyuttaki penceredir.

    Donus: yeni blok sayisi. Tasima gerekirse yapilir; yetmezse hata verir
    ve birime dokunulmaz (hata, yazmadan onceki denetimlerde cikar).
    """
    vol = _Volume(dev)
    vol.check_supported()
    g = vol.geo
    target = new_size_bytes // g.block_size
    if target >= g.blocks_count:
        return g.blocks_count
    minimum = min_blocks_for(vol)
    if target < minimum:
        raise ExtResizeError(tr(
            "ext birimi bu boyuta kucultulemez; en az {} blok gerekli", minimum))
    # Son grup cok kucuk kalacaksa grup sinirina yuvarlanir (resize2fs gibi)
    trial = ExtGeometry(bytes(g.sb))
    trial.set_blocks_count(target)
    last = trial.group_count - 1
    overhead = trial.base_meta_count(last) + 2 + trial.inode_table_blocks
    if last and trial.group_block_count(last) <= overhead + MIN_LAST_GROUP_FREE:
        target = trial.group_first_block(last)
    return _Shrinker(dev, target, progress).run()
