"""btrfs salt okuma — saf Python (ADR 0069).

Kaynak: btrfs.readthedocs.io "On-disk format", Linux `fs/btrfs/` ve
btrfs-progs `kernel-shared/`. Tum sayilar little-endian.

* Ustblok 64 KiB'de ("_BHRfS_M"); sys_chunk_array ilk mantiksal->fiziksel
  eslemeyi verir, chunk agaci (CHUNK_ITEM 228) gerisini tamamlar.
* Agac dugumu: 101 bayt baslik (csum, fsid, bytenr, flags, chunk uuid,
  nesil, sahip, oge sayisi, duzey). Yaprak ogesi: anahtar(17) + ofset(4)
  + boy(4), veri baslik sonuna goredir; ic dugum: anahtar + blok(8) + nesil(8).
  Yapraklar birbirine bagli degildir — gezinti yigitla yapilir.
* Kok agaci: FS agaci (5) ve alt hacimler (>= 256) icin ROOT_ITEM (132).
* FS agaci: INODE_ITEM (1), DIR_INDEX (96; tek giris, sirali), EXTENT_DATA
  (108): satir ici (tip 0) ya da diskteki kapsam (1 normal, 2 onceden
  ayrilmis = sifir). Kapsam yoksa delik (NO_HOLES) sifirdir.
* Sikistirma: 1 zlib, 2 LZO (btrfs cercevesi + LZO1X), 3 zstd —
  `compress.py`deki saf cozuculerle.
* Alt hacim: dizin girisinin konum anahtari ROOT_ITEM ise baska agaca
  gecilir (kok dizini 256).

Tek aygitli birimler okunur (SINGLE/DUP); cok aygitli birimde bu aygitta
kopyasi olan (RAID1/1C3/1C4) kapsamlar okunur, seritli (RAID0/10/5/6)
birimler acik hatayla reddedilir.
"""
from __future__ import annotations

import datetime
import os
import struct
import zlib
from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Tuple

from .image import BlockDevice
from ..i18n import tr

SUPER_OFFSET = 0x10000
MAGIC = b"_BHRfS_M"
HEADER = 101
FS_TREE = 5
FIRST_FREE = 256
ROOT_DIR = 256

INODE_ITEM, INODE_REF, DIR_ITEM, DIR_INDEX = 1, 12, 84, 96
EXTENT_DATA, ROOT_ITEM, CHUNK_ITEM = 108, 132, 228
EXT_INLINE, EXT_REG, EXT_PREALLOC = 0, 1, 2
COMP_NONE, COMP_ZLIB, COMP_LZO, COMP_ZSTD = 0, 1, 2, 3
BG_RAID0, BG_RAID1, BG_DUP, BG_RAID10 = 0x08, 0x10, 0x20, 0x40
BG_RAID5, BG_RAID6, BG_RAID1C3, BG_RAID1C4 = 0x80, 0x100, 0x200, 0x400
STRIPED = BG_RAID0 | BG_RAID10 | BG_RAID5 | BG_RAID6
INCOMPAT_EXTENT_TREE_V2 = 1 << 13
INCOMPAT_RAID_STRIPE_TREE = 1 << 14
S_IFMT, S_IFDIR, S_IFLNK = 0o170000, 0o040000, 0o120000
FT_DIR = 2


class BtrfsError(Exception):
    pass


Key = Tuple[int, int, int]


@dataclass
class BtrfsEntry:
    name: str
    root: int                       # agac (alt hacim) kimligi
    ino: int
    is_dir: bool = False
    size: int = 0
    mode: int = 0
    mtime: Optional[datetime.datetime] = None
    symlink: str = ""
    hidden: bool = False
    subvolume: bool = False


def _key(raw: bytes, off: int) -> Key:
    obj, typ, offset = struct.unpack_from("<QBQ", raw, off)
    return obj, typ, offset


class BtrfsFS:
    def __init__(self, dev: BlockDevice):
        self.dev = dev
        sb = dev.read(SUPER_OFFSET, 4096)
        if sb[0x40:0x48] != MAGIC:
            raise BtrfsError(tr("btrfs ustblogu bulunamadi"))
        self.sb = sb
        self.fsid = sb[0x20:0x30]
        self.root_tree = struct.unpack_from("<Q", sb, 0x50)[0]
        self.chunk_root = struct.unpack_from("<Q", sb, 0x58)[0]
        self.total_bytes, self.bytes_used = struct.unpack_from("<QQ", sb, 0x70)
        self.num_devices = struct.unpack_from("<Q", sb, 0x88)[0]
        self.sectorsize, self.nodesize = struct.unpack_from("<II", sb, 0x90)
        sys_size = struct.unpack_from("<I", sb, 0xA0)[0]
        self.incompat = struct.unpack_from("<Q", sb, 0xBC)[0]
        self.devid = struct.unpack_from("<Q", sb, 0xC9)[0]
        self.label = sb[0x12B:0x12B + 256].split(b"\x00")[0].decode("utf-8", "replace")
        if self.incompat & (INCOMPAT_EXTENT_TREE_V2 | INCOMPAT_RAID_STRIPE_TREE):
            raise BtrfsError(tr("Bu btrfs ozelligi (extent-tree-v2 / raid-stripe-tree) "
                                "bu surumde okunamiyor"))
        self._chunks: List[Tuple[int, int, int]] = []   # (mantiksal, boy, fiziksel)
        self._parse_chunks(sb[0x32B:0x32B + sys_size])
        for key, data in self._walk(self.chunk_root, (0, 0, 0)):
            if key[1] == CHUNK_ITEM:
                self._add_chunk(key[2], data)
        self._roots: Dict[int, int] = {}
        for key, data in self._walk(self.root_tree, (FS_TREE, ROOT_ITEM, 0)):
            if key[1] != ROOT_ITEM:
                continue
            if key[0] == FS_TREE or key[0] >= FIRST_FREE:
                if key[0] not in self._roots:
                    self._roots[key[0]] = struct.unpack_from("<Q", data, 176)[0]
        if FS_TREE not in self._roots:
            raise BtrfsError(tr("btrfs dosya agaci bulunamadi"))
        self._cache: Dict[Tuple[int, int], List[BtrfsEntry]] = {}

    # ---- adresleme ----------------------------------------------------------
    def _parse_chunks(self, arr: bytes) -> None:
        pos = 0
        while pos + 17 + 48 <= len(arr):
            key = _key(arr, pos)
            pos += 17
            n = struct.unpack_from("<H", arr, pos + 44)[0]
            size = 48 + 32 * n
            self._add_chunk(key[2], arr[pos:pos + size])
            pos += size

    def _add_chunk(self, logical: int, item: bytes) -> None:
        length, _owner, _slen, typ = struct.unpack_from("<QQQQ", item, 0)
        n = struct.unpack_from("<H", item, 44)[0]
        if any(c[0] == logical for c in self._chunks):
            return
        if typ & STRIPED and n > 1:
            raise BtrfsError(tr("Seritli btrfs (RAID0/10/5/6) bu surumde okunamiyor"))
        for i in range(n):
            devid, offset = struct.unpack_from("<QQ", item, 48 + 32 * i)
            if devid == self.devid:
                self._chunks.append((logical, length, offset))
                self._chunks.sort()
                return
        raise BtrfsError(tr("btrfs verisinin bir kismi baska aygitta; tek aygitla okunamiyor"))

    def physical(self, logical: int) -> int:
        lo, hi = 0, len(self._chunks) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            start, length, phys = self._chunks[mid]
            if logical < start:
                hi = mid - 1
            elif logical >= start + length:
                lo = mid + 1
            else:
                return phys + logical - start
        raise BtrfsError(tr("btrfs mantiksal adres eslenemedi: {}", logical))

    def read_logical(self, logical: int, length: int) -> bytes:
        out = bytearray()
        while length > 0:
            phys = self.physical(logical)
            start, clen, _p = next(c for c in self._chunks
                                   if c[0] <= logical < c[0] + c[1])
            n = min(length, start + clen - logical)
            out += self.dev.read(phys, n)
            logical += n
            length -= n
        return bytes(out)

    # ---- agaclar ------------------------------------------------------------
    def _node(self, logical: int) -> bytes:
        node = self.read_logical(logical, self.nodesize)
        if node[0x20:0x30] != self.fsid and \
                node[0x20:0x30] != self.sb[0x23B:0x24B]:      # metadata_uuid
            raise BtrfsError(tr("btrfs agac dugumu bozuk: {}", logical))
        return node

    def _walk(self, root: int, lo: Key, depth: int = 0) -> Iterator[Tuple[Key, bytes]]:
        """`lo` anahtarindan baslayip sirali (anahtar, veri) uretir."""
        if depth > 16:
            raise BtrfsError(tr("btrfs agaci cok derin"))
        node = self._node(root)
        n = struct.unpack_from("<I", node, 0x60)[0]
        level = node[0x64]
        if level == 0:
            for i in range(n):
                off = HEADER + 25 * i
                key = _key(node, off)
                if key < lo:
                    continue
                doff, dsize = struct.unpack_from("<II", node, off + 17)
                yield key, node[HEADER + doff:HEADER + doff + dsize]
            return
        start = 0
        for i in range(n):
            if _key(node, HEADER + 33 * i) <= lo:
                start = i
            else:
                break
        for i in range(start, n):
            ptr = struct.unpack_from("<Q", node, HEADER + 33 * i + 17)[0]
            yield from self._walk(ptr, lo, depth + 1)

    def _items(self, root: int, objectid: int, typ: int) -> Iterator[Tuple[Key, bytes]]:
        for key, data in self._walk(self._roots[root], (objectid, typ, 0)):
            if key[0] != objectid or key[1] != typ:
                break
            yield key, data

    def _inode(self, root: int, ino: int) -> bytes:
        for _key_, data in self._items(root, ino, INODE_ITEM):
            return data
        raise BtrfsError(tr("btrfs inode bulunamadi: {}", ino))

    # ---- girisler ------------------------------------------------------------
    @staticmethod
    def _time(raw: bytes, off: int) -> Optional[datetime.datetime]:
        try:
            sec, nsec = struct.unpack_from("<QI", raw, off)
            return datetime.datetime.fromtimestamp(sec + nsec / 1e9)
        except (OverflowError, OSError, ValueError):
            return None

    def _entry(self, name: str, root: int, ino: int, subvolume: bool = False) -> BtrfsEntry:
        raw = self._inode(root, ino)
        mode = struct.unpack_from("<I", raw, 52)[0]
        e = BtrfsEntry(name=name, root=root, ino=ino, mode=mode,
                       is_dir=(mode & S_IFMT) == S_IFDIR,
                       size=struct.unpack_from("<Q", raw, 16)[0],
                       mtime=self._time(raw, 136), hidden=name.startswith("."),
                       subvolume=subvolume)
        if (mode & S_IFMT) == S_IFLNK:
            e.symlink = self.read_inode(root, ino, e.size).decode("utf-8", "replace")
        return e

    def children(self, root: int, ino: int) -> List[BtrfsEntry]:
        hit = self._cache.get((root, ino))
        if hit is not None:
            return hit
        out: List[BtrfsEntry] = []
        for _key_, data in self._items(root, ino, DIR_INDEX):
            loc = _key(data, 0)
            name_len = struct.unpack_from("<H", data, 27)[0]
            name = data[30:30 + name_len].decode("utf-8", "surrogateescape")
            if loc[1] == ROOT_ITEM:
                if loc[0] not in self._roots:
                    continue                         # silinmis alt hacim
                out.append(self._entry(name, loc[0], ROOT_DIR, subvolume=True))
            else:
                out.append(self._entry(name, root, loc[0]))
        if len(self._cache) > 256:
            self._cache.clear()
        self._cache[(root, ino)] = out
        return out

    def resolve(self, path: str) -> BtrfsEntry:
        node = BtrfsEntry(name="", root=FS_TREE, ino=ROOT_DIR, is_dir=True)
        for part in [p for p in path.replace("\\", "/").split("/") if p]:
            if not node.is_dir:
                raise BtrfsError(tr("Dizin degil: {}", path))
            match = next((c for c in self.children(node.root, node.ino) if c.name == part), None)
            if match is None:
                raise BtrfsError(tr("Bulunamadi: {}", path))
            node = match
        return node

    def listdir(self, path: str = "/") -> List[BtrfsEntry]:
        node = self.resolve(path)
        if not node.is_dir:
            raise BtrfsError(tr("Dizin degil: {}", path))
        return list(self.children(node.root, node.ino))

    # ---- veri -------------------------------------------------------------------
    def _decompress(self, kind: int, data: bytes, ram: int) -> bytes:
        from . import compress
        if kind == COMP_ZLIB:
            return zlib.decompress(data)[:ram]
        if kind == COMP_LZO:
            return compress.btrfs_lzo_decompress(data, ram, self.sectorsize)
        if kind == COMP_ZSTD:
            return compress.zstd_decompress(data)[:ram]
        raise BtrfsError(tr("Bilinmeyen btrfs sikistirmasi: {}", kind))

    def read_inode(self, root: int, ino: int, size: int, max_bytes: int = -1) -> bytes:
        limit = size if max_bytes < 0 else min(size, max_bytes)
        out = bytearray(limit)
        for key, data in self._items(root, ino, EXTENT_DATA):
            fpos = key[2]
            if fpos >= limit:
                break
            ram = struct.unpack_from("<Q", data, 8)[0]
            comp, enc = data[16], data[17]
            typ = data[20]
            if enc:
                raise BtrfsError(tr("Sifreli btrfs verisi okunamiyor"))
            if typ == EXT_INLINE:
                raw = data[21:]
                chunk = self._decompress(comp, raw, ram) if comp else raw
            else:
                disk, disk_len, off, num = struct.unpack_from("<QQQQ", data, 21)
                if typ == EXT_PREALLOC or disk == 0:
                    continue                          # sifir / delik
                if comp:
                    whole = self._decompress(comp, self.read_logical(disk, disk_len), ram)
                    chunk = whole[off:off + num]
                else:
                    chunk = self.read_logical(disk + off, min(num, limit - fpos))
            n = min(len(chunk), limit - fpos)
            out[fpos:fpos + n] = chunk[:n]
        return bytes(out)

    def read_file(self, path: str, max_bytes: int = -1) -> bytes:
        e = self.resolve(path)
        if e.is_dir:
            raise BtrfsError(tr("Dizin okunamaz: {}", path))
        return self.read_inode(e.root, e.ino, e.size, max_bytes)

    def extract(self, path: str, dest: str) -> str:
        e = self.resolve(path)
        if e.is_dir:
            os.makedirs(dest, exist_ok=True)
            for c in self.children(e.root, e.ino):
                if not c.symlink:
                    self.extract(path.rstrip("/") + "/" + c.name, os.path.join(dest, c.name))
            return dest
        os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(self.read_inode(e.root, e.ino, e.size))
        if e.mtime:
            ts = e.mtime.timestamp()
            os.utime(dest, (ts, ts))
        return dest

    def stats(self) -> Dict[str, int]:
        return {"total_bytes": self.total_bytes, "used_bytes": self.bytes_used,
                "free_bytes": max(0, self.total_bytes - self.bytes_used),
                "cluster_size": self.sectorsize}
