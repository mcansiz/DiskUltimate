"""XFS salt okuma (v4 ve v5) — saf Python.

Kaynak: XFS Algorithms & Data Structures (kernel.org, xfs-documentation),
Linux `fs/xfs/libxfs/xfs_format.h`. Tum sayilar big-endian.

* Ustblok: blok/inode boyu, AG sayisi/boyu, kok inode, log2 alanlari
  (inode numarasi = AG | AG icindeki blok | bloktaki sira), ozellik
  bayraklari (v5 incompat: FTYPE, BIGTIME, NREXT64 ...; v4 features2 FTYPE).
* Inode: "IN", mod, bicim (1 yerel, 2 kapsam, 3 B-agaci), boy, zaman;
  v3 cekirdek 176, v2 100 bayt. NREXT64 inode bayragiyla kapsam sayisi
  64 bit alanda.
* Kapsam kaydi 128 bit: bayrak(1) mantiksal(54) fiziksel fsblok(52) sayi(21).
  fsblok = AG << agblklog | AG icindeki blok.
* B-agacli catal: inode icinde bmdr koku, bloklarda "BMA3" (v5, 72 bayt
  baslik) / "BMAP" (v4, 24 bayt).
* Dizin: kisa bicim (inode icinde), veri bloklari (XDB3/XDD3, v4 XD2B/XD2D)
  mantiksal 32 GB'in altinda; girisler inode(8) ad_boyu(1) ad [tur(1)]
  8'e hizali + etiket(2); bos alan 0xFFFF ile baslar. Blok dizinde sonda
  yaprak kuyrugu vardir.
* Sembolik bag: yerel ya da uzak bloklar (v5 "XSLM", 56 bayt baslik).
"""
from __future__ import annotations

import datetime
import os
import struct
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .image import BlockDevice
from ..i18n import tr

XFS_MAGIC = b"XFSB"
INCOMPAT_FTYPE = 0x01
INCOMPAT_BIGTIME = 0x08
INCOMPAT_NEEDSREPAIR = 0x10
INCOMPAT_NREXT64 = 0x20
V2_FTYPE = 0x200
DIFLAG2_BIGTIME = 1 << 3
DIFLAG2_NREXT64 = 1 << 4
FMT_LOCAL, FMT_EXTENTS, FMT_BTREE = 1, 2, 3
S_IFMT, S_IFDIR, S_IFREG, S_IFLNK = 0o170000, 0o040000, 0o100000, 0o120000
DIR_LEAF_OFFSET = 1 << 35                     # bayt: veri bloklari bunun altinda
BIGTIME_EPOCH_OFFSET = 1 << 31


class XfsError(Exception):
    pass


@dataclass
class XfsEntry:
    name: str
    ino: int
    is_dir: bool = False
    size: int = 0
    mode: int = 0
    mtime: Optional[datetime.datetime] = None
    symlink: str = ""
    hidden: bool = False


class XfsFS:
    def __init__(self, dev: BlockDevice):
        self.dev = dev
        sb = dev.read(0, 512)
        if sb[:4] != XFS_MAGIC:
            raise XfsError(tr("XFS ustblogu bulunamadi"))
        self.block_size = struct.unpack_from(">I", sb, 4)[0]
        self.dblocks = struct.unpack_from(">Q", sb, 8)[0]
        self.root_ino = struct.unpack_from(">Q", sb, 56)[0]
        self.agblocks, self.agcount = struct.unpack_from(">II", sb, 84)
        self.version = struct.unpack_from(">H", sb, 100)[0] & 0xF
        self.inode_size = struct.unpack_from(">H", sb, 104)[0]
        self.label = sb[108:120].split(b"\x00")[0].decode("latin-1", "replace")
        self.blocklog, _sectlog, self.inodelog, self.inopblog, self.agblklog = sb[120:125]
        self.icount, self.ifree, self.fdblocks = struct.unpack_from(">QQQ", sb, 128)
        self.dirblklog = sb[192]
        features2 = struct.unpack_from(">I", sb, 200)[0]
        self.incompat = struct.unpack_from(">I", sb, 216)[0] if self.version >= 5 else 0
        self.ftype = bool(self.incompat & INCOMPAT_FTYPE) if self.version >= 5 \
            else bool(features2 & V2_FTYPE)
        if self.block_size < 512 or self.block_size != 1 << self.blocklog:
            raise XfsError(tr("Gecersiz XFS blok boyu: {}", self.block_size))
        known = INCOMPAT_FTYPE | 0x02 | 0x04 | INCOMPAT_BIGTIME | INCOMPAT_NEEDSREPAIR \
            | INCOMPAT_NREXT64 | 0x40 | 0x80
        if self.incompat & ~known:
            raise XfsError(tr("Bilinmeyen XFS ozelligi (0x{:x}); bu surumde okunamiyor",
                              self.incompat & ~known))
        self.dir_block = self.block_size << self.dirblklog
        self.core_size = 176 if self.version >= 5 else 100
        self._cache: Dict[int, List[XfsEntry]] = {}

    # ---- adresleme --------------------------------------------------------
    def fsb_to_byte(self, fsb: int) -> int:
        agno = fsb >> self.agblklog
        agbno = fsb & ((1 << self.agblklog) - 1)
        return (agno * self.agblocks + agbno) * self.block_size

    def ino_to_byte(self, ino: int) -> int:
        shift = self.agblklog + self.inopblog
        agno = ino >> shift
        agbno = (ino >> self.inopblog) & ((1 << self.agblklog) - 1)
        off = ino & ((1 << self.inopblog) - 1)
        return (agno * self.agblocks + agbno) * self.block_size + off * self.inode_size

    # ---- inode ------------------------------------------------------------------
    def inode(self, ino: int) -> bytes:
        raw = self.dev.read(self.ino_to_byte(ino), self.inode_size)
        if raw[:2] != b"IN":
            raise XfsError(tr("XFS inode bozuk: {}", ino))
        return raw

    def _time(self, raw: bytes, off: int) -> Optional[datetime.datetime]:
        try:
            if self.version >= 5 and struct.unpack_from(">Q", raw, 120)[0] & DIFLAG2_BIGTIME:
                ns = struct.unpack_from(">Q", raw, off)[0]
                secs = ns // 1_000_000_000 - BIGTIME_EPOCH_OFFSET
            else:
                secs = struct.unpack_from(">i", raw, off)[0]
            return datetime.datetime.fromtimestamp(secs)
        except (OverflowError, OSError, ValueError):
            return None

    def _nextents(self, raw: bytes) -> int:
        if self.version >= 5 and struct.unpack_from(">Q", raw, 120)[0] & DIFLAG2_NREXT64:
            return struct.unpack_from(">Q", raw, 24)[0]
        return struct.unpack_from(">I", raw, 76)[0]

    def _fork(self, raw: bytes) -> bytes:
        forkoff = raw[82]
        end = self.core_size + forkoff * 8 if forkoff else self.inode_size
        return raw[self.core_size:end]

    @staticmethod
    def _unpack_extent(rec: bytes) -> Tuple[int, int, int, bool]:
        hi, lo = struct.unpack(">QQ", rec)
        unwritten = bool(hi >> 63)
        startoff = (hi >> 9) & ((1 << 54) - 1)
        startblock = ((hi & 0x1FF) << 43) | (lo >> 21)
        count = lo & ((1 << 21) - 1)
        return startoff, startblock, count, unwritten

    def extents(self, raw: bytes) -> List[Tuple[int, int, int, bool]]:
        """[(mantiksal blok, fsblok, blok sayisi, yazilmamis)] — sirali."""
        fmt = raw[5]
        fork = self._fork(raw)
        if fmt == FMT_EXTENTS:
            n = self._nextents(raw)
            return [self._unpack_extent(fork[i * 16:(i + 1) * 16]) for i in range(n)
                    if (i + 1) * 16 <= len(fork)]
        if fmt == FMT_BTREE:
            level, numrecs = struct.unpack_from(">HH", fork, 0)
            maxrecs = (len(fork) - 4) // 16
            ptrs = [struct.unpack_from(">Q", fork, 4 + maxrecs * 8 + i * 8)[0]
                    for i in range(numrecs)]
            out: List[Tuple[int, int, int, bool]] = []
            for p in ptrs:
                self._btree_leaves(p, level - 1, out, 0)
            return out
        return []

    def _btree_leaves(self, fsb: int, level: int, out, depth: int) -> None:
        if depth > 16:
            raise XfsError(tr("XFS kapsam agaci cok derin"))
        blk = self.dev.read(self.fsb_to_byte(fsb), self.block_size)
        magic = blk[:4]
        if magic == b"BMA3":
            hdr = 72
        elif magic == b"BMAP":
            hdr = 24
        else:
            raise XfsError(tr("XFS kapsam agaci blogu bozuk"))
        lvl, numrecs = struct.unpack_from(">HH", blk, 4)
        if lvl == 0:
            for i in range(numrecs):
                out.append(self._unpack_extent(blk[hdr + i * 16:hdr + (i + 1) * 16]))
            return
        maxrecs = (self.block_size - hdr) // 16
        for i in range(numrecs):
            ptr = struct.unpack_from(">Q", blk, hdr + maxrecs * 8 + i * 8)[0]
            self._btree_leaves(ptr, lvl - 1, out, depth + 1)

    def read_data(self, raw: bytes, max_bytes: int = -1) -> bytes:
        size = struct.unpack_from(">Q", raw, 56)[0]
        limit = size if max_bytes < 0 else min(size, max_bytes)
        if raw[5] == FMT_LOCAL:
            return bytes(self._fork(raw)[:limit])
        out = bytearray(limit)
        bs = self.block_size
        for startoff, fsb, count, unwritten in self.extents(raw):
            pos = startoff * bs
            if pos >= limit or unwritten:
                continue
            n = min(count * bs, limit - pos)
            out[pos:pos + n] = self.dev.read(self.fsb_to_byte(fsb), n)
        return bytes(out)

    # ---- dizinler ---------------------------------------------------------------
    def _shortform(self, raw: bytes) -> List[Tuple[str, int]]:
        fork = self._fork(raw)
        count, i8count = fork[0], fork[1]
        i8 = bool(i8count)
        size = 8 if i8 else 4
        pos = 2 + size
        out: List[Tuple[str, int]] = []
        for _ in range(count or i8count):
            namelen = fork[pos]
            name = fork[pos + 3:pos + 3 + namelen]
            pos += 3 + namelen + (1 if self.ftype else 0)
            ino = struct.unpack_from(">Q" if i8 else ">I", fork, pos)[0]
            pos += size
            out.append((name.decode("utf-8", "surrogateescape"), ino))
        return out

    def _data_block_entries(self, blk: bytes) -> List[Tuple[str, int]]:
        magic = blk[:4]
        if magic in (b"XDB3", b"XDD3"):
            pos = 64
        elif magic in (b"XD2B", b"XD2D"):
            pos = 16
        else:
            return []
        end = len(blk)
        if magic in (b"XDB3", b"XD2B"):                   # blok dizin: yaprak kuyrugu
            count = struct.unpack_from(">I", blk, end - 8)[0]
            end = end - 8 - count * 8
        out: List[Tuple[str, int]] = []
        while pos + 8 <= end:
            if blk[pos:pos + 2] == b"\xff\xff":           # bos alan
                length = struct.unpack_from(">H", blk, pos + 2)[0]
                if length < 8:
                    break
                pos += length
                continue
            ino = struct.unpack_from(">Q", blk, pos)[0]
            namelen = blk[pos + 8]
            name = blk[pos + 9:pos + 9 + namelen]
            size = 8 + 1 + namelen + (1 if self.ftype else 0) + 2
            size = (size + 7) & ~7
            out.append((name.decode("utf-8", "surrogateescape"), ino))
            pos += size
        return out

    def _dir_raw_entries(self, ino: int) -> List[Tuple[str, int]]:
        raw = self.inode(ino)
        if raw[5] == FMT_LOCAL:
            return self._shortform(raw)
        bs = self.block_size
        leaf_block = DIR_LEAF_OFFSET // bs
        out: List[Tuple[str, int]] = []
        per = self.dir_block // bs
        for startoff, fsb, count, _unwritten in self.extents(raw):
            if startoff >= leaf_block:
                continue
            count = min(count, leaf_block - startoff)
            # dizin bloklari (dirblksize) mantiksal alanda hizalidir
            base = self.fsb_to_byte(fsb)
            for k in range((-startoff) % per, count - per + 1, per):
                data = self.dev.read(base + k * bs, self.dir_block)
                out.extend(self._data_block_entries(data))
        return [(n, i) for n, i in out if n not in (".", "..")]

    def _entry(self, name: str, ino: int) -> XfsEntry:
        raw = self.inode(ino)
        mode = struct.unpack_from(">H", raw, 2)[0]
        entry = XfsEntry(name=name, ino=ino, mode=mode,
                         is_dir=(mode & S_IFMT) == S_IFDIR,
                         size=struct.unpack_from(">Q", raw, 56)[0],
                         mtime=self._time(raw, 40), hidden=name.startswith("."))
        if (mode & S_IFMT) == S_IFLNK:
            entry.symlink = self._symlink(raw)
        return entry

    def _symlink(self, raw: bytes) -> str:
        size = struct.unpack_from(">Q", raw, 56)[0]
        if raw[5] == FMT_LOCAL:
            return self._fork(raw)[:size].decode("utf-8", "replace")
        out = bytearray()
        for _off, fsb, count, _u in self.extents(raw):
            blk = self.dev.read(self.fsb_to_byte(fsb), count * self.block_size)
            if blk[:4] == b"XSLM":                         # v5 uzak bag basligi
                n = struct.unpack_from(">I", blk, 8)[0]
                out += blk[56:56 + n]
            else:
                out += blk
        return bytes(out[:size]).decode("utf-8", "replace")

    def children(self, ino: int) -> List[XfsEntry]:
        hit = self._cache.get(ino)
        if hit is None:
            hit = [self._entry(n, i) for n, i in self._dir_raw_entries(ino)]
            if len(self._cache) > 256:
                self._cache.clear()
            self._cache[ino] = hit
        return hit

    def resolve(self, path: str) -> XfsEntry:
        node = XfsEntry(name="", ino=self.root_ino, is_dir=True)
        for part in [p for p in path.replace("\\", "/").split("/") if p]:
            if not node.is_dir:
                raise XfsError(tr("Dizin degil: {}", path))
            match = next((c for c in self.children(node.ino) if c.name == part), None)
            if match is None:
                raise XfsError(tr("Bulunamadi: {}", path))
            node = match
        return node

    def listdir(self, path: str = "/") -> List[XfsEntry]:
        node = self.resolve(path)
        if not node.is_dir:
            raise XfsError(tr("Dizin degil: {}", path))
        return list(self.children(node.ino))

    def read_file(self, path: str, max_bytes: int = -1) -> bytes:
        entry = self.resolve(path)
        if entry.is_dir:
            raise XfsError(tr("Dizin okunamaz: {}", path))
        return self.read_data(self.inode(entry.ino), max_bytes)

    def extract(self, path: str, dest: str) -> str:
        entry = self.resolve(path)
        if entry.is_dir:
            os.makedirs(dest, exist_ok=True)
            for child in self.children(entry.ino):
                if not child.symlink:
                    self.extract(path.rstrip("/") + "/" + child.name,
                                 os.path.join(dest, child.name))
            return dest
        os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(self.read_data(self.inode(entry.ino)))
        if entry.mtime:
            ts = entry.mtime.timestamp()
            os.utime(dest, (ts, ts))
        return dest

    def stats(self) -> Dict[str, int]:
        total = self.dblocks * self.block_size
        free = self.fdblocks * self.block_size
        return {"total_bytes": total, "used_bytes": total - free,
                "free_bytes": free, "cluster_size": self.block_size}
