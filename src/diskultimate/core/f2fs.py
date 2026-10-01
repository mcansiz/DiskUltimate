"""F2FS salt okuma — saf Python (ADR 0070).

Kaynak: Linux `include/linux/f2fs_fs.h`, `fs/f2fs/` ve f2fs-tools.
Little-endian, blok 4096.

* Ustblok 1024. bayttan (iki kopya: blok 0 ve 1). Alanlar: log boyutlari,
  segment sayilari, cp/sit/nat/ssa/main blok adresleri, kok inode, ozellik.
* Checkpoint: iki paket (cp_blkaddr ve bir segment sonrasi); basligi ve
  sonundaki kopyasi ayni surumu tasiyan ve surumu buyuk olan gecerlidir.
* NAT (dugum kimligi -> blok): her NAT blogunun iki kopyasi vardir; hangisi
  gecerli oldugunu checkpoint'teki NAT surum bitmap'i (MSB-once) soyler.
  Henuz yazilmamis guncel girisler sicak veri ozetindeki **NAT gunlugunde**
  durur (sikistirilmis ozette ilk blok basinda) — once oraya bakilir.
* Inode: i_addr[923] (extra_attr ile ilk i_extra_isize bayt ek alan, satir
  ici xattr ile sondan 50 kelime eksik), i_nid[5]: 2 dogrudan, 2 dolayli,
  1 cift dolayli dugum (1018 giris). Satir ici veri ve satir ici dizin
  i_addr[ek + 1]'den baslar.
* Dizin blogu: bitmap(27) + ayrilmis(3) + 214 x giris(11) + 214 x 8 ad.
  Listeleme icin karma gerekmez; tum bloklar taranir.
* Sikistirma (F2FS_COMPR_FL): kume basi COMPRESS_ADDR, ardindan sikistirilmis
  bloklar; basligi clen + saglama; LZO, LZ4, zstd, LZO-RLE desteklenir.
"""
from __future__ import annotations

import datetime
import os
import struct
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .image import BlockDevice
from ..i18n import tr

F2FS_MAGIC = 0xF2F52010
BLOCK = 4096
NAT_ENTRY_SIZE = 9
NAT_PER_BLOCK = BLOCK // NAT_ENTRY_SIZE                     # 455
ADDRS_PER_INODE = 923
ADDRS_PER_BLOCK = 1018
NIDS_PER_BLOCK = 1018
NULL_ADDR, NEW_ADDR, COMPRESS_ADDR = 0, 0xFFFFFFFF, 0xFFFFFFFE
INLINE_XATTR, INLINE_DATA, INLINE_DENTRY, EXTRA_ATTR = 0x01, 0x02, 0x04, 0x20
DEF_INLINE_XATTR_ADDRS = 50
CP_COMPACT_SUM = 0x04
CP_LARGE_NAT_BITMAP = 0x400
FEATURE_ENCRYPT = 0x0001
FEATURE_FLEX_INLINE_XATTR = 0x0040
FEATURE_COMPRESSION = 0x2000
FADVISE_ENCRYPT = 0x04
F2FS_COMPR_FL = 0x00000004
DENTRY_PER_BLOCK = 214
S_IFMT, S_IFDIR, S_IFLNK = 0o170000, 0o040000, 0o120000
COMPR_LZO, COMPR_LZ4, COMPR_ZSTD, COMPR_LZORLE = 0, 1, 2, 3


class F2fsError(Exception):
    pass


@dataclass
class F2fsEntry:
    name: str
    ino: int
    is_dir: bool = False
    size: int = 0
    mode: int = 0
    mtime: Optional[datetime.datetime] = None
    symlink: str = ""
    hidden: bool = False


def _bit(bitmap: bytes, nr: int) -> bool:
    return bool(bitmap[nr >> 3] & (0x80 >> (nr & 7)))


def lz4_block_decompress(src: bytes, size: int) -> bytes:
    """LZ4 blok bicimi (cercevesiz)."""
    out = bytearray()
    i, n = 0, len(src)
    while i < n:
        token = src[i]
        i += 1
        lit = token >> 4
        if lit == 15:
            while True:
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        out += src[i:i + lit]
        i += lit
        if i >= n or len(out) >= size:
            break
        off = src[i] | (src[i + 1] << 8)
        i += 2
        ml = token & 15
        if ml == 15:
            while True:
                b = src[i]
                i += 1
                ml += b
                if b != 255:
                    break
        ml += 4
        start = len(out) - off
        if off == 0 or start < 0:
            raise F2fsError(tr("LZ4: gecersiz geri basvuru"))
        if off >= ml:
            out += out[start:start + ml]
        else:
            chunk = out[start:]
            reps, rem = divmod(ml, off)
            out += chunk * reps + chunk[:rem]
    return bytes(out[:size])


class F2fsFS:
    def __init__(self, dev: BlockDevice):
        self.dev = dev
        sb = None
        for base in (1024, BLOCK + 1024):
            raw = dev.read(base, 3072)
            if struct.unpack_from("<I", raw, 0)[0] == F2FS_MAGIC:
                sb = raw
                break
        if sb is None:
            raise F2fsError(tr("F2FS ustblogu bulunamadi"))
        self.sb = sb
        log_blocksize, self.log_bps = struct.unpack_from("<II", sb, 16)
        if log_blocksize != 12:
            raise F2fsError(tr("F2FS blok boyu desteklenmiyor: {}", 1 << log_blocksize))
        self.bps = 1 << self.log_bps
        self.block_count = struct.unpack_from("<Q", sb, 36)[0]
        self.segment_count_main = struct.unpack_from("<I", sb, 68)[0]
        self.cp_blkaddr, self.sit_blkaddr, self.nat_blkaddr, _ssa, self.main_blkaddr = \
            struct.unpack_from("<IIIII", sb, 76)
        self.root_ino = struct.unpack_from("<I", sb, 96)[0]
        self.label = sb[124:124 + 1024].decode("utf-16-le", "replace").split("\x00")[0]
        self.cp_payload = struct.unpack_from("<I", sb, 1664)[0]
        self.feature = struct.unpack_from("<I", sb, 2180)[0]
        self._load_checkpoint()
        self._nat_cache: Dict[int, bytes] = {}
        self._cache: Dict[int, List[F2fsEntry]] = {}

    # ---- checkpoint / NAT ----------------------------------------------------
    def _block(self, addr: int) -> bytes:
        return self.dev.read(addr * BLOCK, BLOCK)

    def _load_checkpoint(self) -> None:
        best = None
        for start in (self.cp_blkaddr, self.cp_blkaddr + self.bps):
            cp = self._block(start)
            ver = struct.unpack_from("<Q", cp, 0)[0]
            total = struct.unpack_from("<I", cp, 136)[0]
            if not 0 < total <= self.bps:
                continue
            tail = self._block(start + total - 1)
            if struct.unpack_from("<Q", tail, 0)[0] != ver:
                continue
            if best is None or ver > best[0]:
                best = (ver, start, cp)
        if best is None:
            raise F2fsError(tr("Gecerli F2FS checkpoint bulunamadi"))
        _ver, start, cp = best
        self.cp = cp
        self.flags = struct.unpack_from("<I", cp, 132)[0]
        self.user_blocks, self.valid_blocks = struct.unpack_from("<QQ", cp, 8)
        sit_size, nat_size = struct.unpack_from("<II", cp, 156)
        if self.flags & CP_LARGE_NAT_BITMAP:
            self.nat_bitmap = cp[192 + 4:192 + 4 + nat_size]
        elif self.cp_payload > 0:
            self.nat_bitmap = cp[192:192 + nat_size]
        else:
            self.nat_bitmap = cp[192 + sit_size:192 + sit_size + nat_size]
        # NAT gunlugu: sicak veri ozeti
        sum_start = struct.unpack_from("<I", cp, 140)[0]
        summary = self._block(start + sum_start)
        if self.flags & CP_COMPACT_SUM:
            journal = summary[0:507]
        else:
            journal = summary[3584:3584 + 507]
        self._nat_journal: Dict[int, int] = {}
        n = struct.unpack_from("<H", journal, 0)[0]
        for i in range(min(n, 38)):
            nid = struct.unpack_from("<I", journal, 2 + 13 * i)[0]
            blk = struct.unpack_from("<I", journal, 2 + 13 * i + 9)[0]
            self._nat_journal[nid] = blk

    def nat_lookup(self, nid: int) -> int:
        if nid in self._nat_journal:
            return self._nat_journal[nid]
        block_off = nid // NAT_PER_BLOCK
        blk = self._nat_cache.get(block_off)
        if blk is None:
            seg_off = block_off >> self.log_bps
            addr = self.nat_blkaddr + (seg_off << self.log_bps << 1) + (block_off & (self.bps - 1))
            if _bit(self.nat_bitmap, block_off):
                addr += self.bps
            blk = self._block(addr)
            if len(self._nat_cache) > 1024:
                self._nat_cache.clear()
            self._nat_cache[block_off] = blk
        return struct.unpack_from("<I", blk, (nid % NAT_PER_BLOCK) * NAT_ENTRY_SIZE + 5)[0]

    def node(self, nid: int) -> bytes:
        addr = self.nat_lookup(nid)
        if addr in (NULL_ADDR, NEW_ADDR):
            raise F2fsError(tr("F2FS dugumu bulunamadi: {}", nid))
        blk = self._block(addr)
        if struct.unpack_from("<I", blk, BLOCK - 24)[0] != nid:
            raise F2fsError(tr("F2FS dugumu bozuk: {}", nid))
        return blk

    # ---- inode -----------------------------------------------------------------
    def _extra(self, inode: bytes) -> int:
        """i_addr icindeki ek alan baslangici (kelime)."""
        if inode[3] & EXTRA_ATTR:
            return struct.unpack_from("<H", inode, 360)[0] // 4
        return 0

    def _xattr_words(self, inode: bytes) -> int:
        if not inode[3] & INLINE_XATTR:
            return 0
        if self.feature & FEATURE_FLEX_INLINE_XATTR and inode[3] & EXTRA_ATTR:
            return struct.unpack_from("<H", inode, 360 + 4)[0]
        return DEF_INLINE_XATTR_ADDRS

    def _addrs(self, inode: bytes) -> Tuple[int, int]:
        """(ilk adres kelimesi, adres sayisi)"""
        extra = self._extra(inode)
        return extra, ADDRS_PER_INODE - extra - self._xattr_words(inode)

    def _inline(self, inode: bytes) -> bytes:
        first, count = self._addrs(inode)
        off = 360 + (first + 1) * 4
        return inode[off:off + (count - 1) * 4]

    def _block_map(self, ino: int, inode: bytes, nblocks: int) -> List[int]:
        """Dosyanin ilk `nblocks` blogunun adresleri."""
        first, count = self._addrs(inode)
        out = list(struct.unpack_from(f"<{count}I", inode, 360 + first * 4))[:nblocks]
        nids = struct.unpack_from("<5I", inode, 4052)

        def direct(nid: int, need: int) -> List[int]:
            if not nid:
                return [NULL_ADDR] * need
            return list(struct.unpack_from(f"<{ADDRS_PER_BLOCK}I", self.node(nid), 0))[:need]

        def indirect(nid: int, need: int, depth: int) -> List[int]:
            if not nid:
                return [NULL_ADDR] * need
            kids = struct.unpack_from(f"<{NIDS_PER_BLOCK}I", self.node(nid), 0)
            res: List[int] = []
            per = ADDRS_PER_BLOCK * (NIDS_PER_BLOCK ** (depth - 1))
            for kid in kids:
                if len(res) >= need:
                    break
                take = min(per, need - len(res))
                res += direct(kid, take) if depth == 1 else indirect(kid, take, depth - 1)
            return res

        plan = [(nids[0], 0), (nids[1], 0), (nids[2], 1), (nids[3], 1), (nids[4], 2)]
        for nid, depth in plan:
            if len(out) >= nblocks:
                break
            need = nblocks - len(out)
            cap = ADDRS_PER_BLOCK * (NIDS_PER_BLOCK ** depth)
            take = min(cap, need)
            out += direct(nid, take) if depth == 0 else indirect(nid, take, depth)
        return out[:nblocks]

    def read_inode(self, ino: int, max_bytes: int = -1) -> bytes:
        inode = self.node(ino)
        if inode[2] & FADVISE_ENCRYPT:
            raise F2fsError(tr("Sifreli F2FS dosyasi okunamiyor"))
        size = struct.unpack_from("<Q", inode, 16)[0]
        limit = size if max_bytes < 0 else min(size, max_bytes)
        if inode[3] & INLINE_DATA:
            return self._inline(inode)[:limit]
        nblocks = (limit + BLOCK - 1) // BLOCK
        flags = struct.unpack_from("<I", inode, 80)[0]
        compressed = bool(flags & F2FS_COMPR_FL) and bool(self.feature & FEATURE_COMPRESSION)
        if compressed:
            per = 1 << self._cluster_log(inode)
            nblocks = -(-nblocks // per) * per
        addrs = self._block_map(ino, inode, nblocks)
        out = bytearray()
        if compressed:
            algo = self._compress_algo(inode)
            for c in range(0, len(addrs), per):
                cluster = addrs[c:c + per]
                if cluster[0] == COMPRESS_ADDR:
                    raw = b"".join(self._block(a) for a in cluster[1:]
                                   if a not in (NULL_ADDR, NEW_ADDR))
                    out += self._decompress(algo, raw, per * BLOCK)
                else:
                    out += self._plain(cluster)
        else:
            out = bytearray(self._plain(addrs))
        return bytes(out[:limit])

    def _plain(self, addrs: List[int]) -> bytes:
        out = bytearray()
        i = 0
        while i < len(addrs):
            a = addrs[i]
            if a in (NULL_ADDR, NEW_ADDR, COMPRESS_ADDR):
                out += bytes(BLOCK)
                i += 1
                continue
            j = i + 1                                  # ardisik bloklar tek okuma
            while j < len(addrs) and addrs[j] == addrs[j - 1] + 1:
                j += 1
            out += self.dev.read(a * BLOCK, (j - i) * BLOCK)
            i = j
        return bytes(out)

    # ---- sikistirma ------------------------------------------------------------
    def _cluster_log(self, inode: bytes) -> int:
        # f2fs_inode ek alanlari: extra_isize(2) inline_xattr_size(2) projid(4)
        # inode_checksum(4) crtime(8) crtime_nsec(4) compr_blocks(8)
        # compress_algorithm(1) log_cluster_size(1) compress_flag(2)
        return inode[360 + 33]

    def _compress_algo(self, inode: bytes) -> int:
        return inode[360 + 32]

    def _decompress(self, algo: int, raw: bytes, size: int) -> bytes:
        from .compress import DecompressError, lzo1x_decompress, zstd_decompress
        clen = struct.unpack_from("<I", raw, 0)[0]
        data = raw[24:24 + clen]
        try:
            if algo == COMPR_LZ4:
                return lz4_block_decompress(data, size)
            if algo == COMPR_ZSTD:
                return zstd_decompress(data)[:size]
            if algo == COMPR_LZO:
                return lzo1x_decompress(data, size)[:size]
        except (DecompressError, IndexError) as exc:
            raise F2fsError(str(exc)) from exc
        raise F2fsError(tr("Desteklenmeyen F2FS sikistirmasi: {}", algo))   # LZO-RLE

    # ---- dizinler -----------------------------------------------------------------
    def _dentries(self, block: bytes, nr: int, bitmap_off: int, dentry_off: int,
                  name_off: int) -> List[Tuple[str, int, int]]:
        out = []
        i = 0
        while i < nr:
            if not block[bitmap_off + (i >> 3)] & (1 << (i & 7)):
                i += 1
                continue
            _h, ino, nlen, ftype = struct.unpack_from("<IIHB", block, dentry_off + 11 * i)
            name = block[name_off + 8 * i:name_off + 8 * i + nlen]
            out.append((name.decode("utf-8", "surrogateescape"), ino, ftype))
            i += max(1, (nlen + 7) // 8)
        return out

    def _dir_raw(self, ino: int) -> List[Tuple[str, int, int]]:
        inode = self.node(ino)
        out: List[Tuple[str, int, int]] = []
        if inode[3] & INLINE_DENTRY:
            area = self._inline(inode)
            size = len(area)
            nr = size * 8 // ((11 + 8) * 8 + 1)
            bm = (nr + 7) // 8
            reserved = size - (nr * (11 + 8) + bm)
            out = self._dentries(area, nr, 0, bm + reserved, bm + reserved + nr * 11)
        else:
            size = struct.unpack_from("<Q", inode, 16)[0]
            nblocks = (size + BLOCK - 1) // BLOCK
            for addr in self._block_map(ino, inode, nblocks):
                if addr in (NULL_ADDR, NEW_ADDR):
                    continue
                blk = self._block(addr)
                out += self._dentries(blk, DENTRY_PER_BLOCK, 0, 30, 30 + DENTRY_PER_BLOCK * 11)
        return [(n, i, t) for n, i, t in out if n not in (".", "..")]

    def _entry(self, name: str, ino: int) -> F2fsEntry:
        inode = self.node(ino)
        mode = struct.unpack_from("<H", inode, 0)[0]
        size = struct.unpack_from("<Q", inode, 16)[0]
        try:
            sec = struct.unpack_from("<Q", inode, 48)[0]
            mtime = datetime.datetime.fromtimestamp(sec)
        except (OverflowError, OSError, ValueError):
            mtime = None
        e = F2fsEntry(name=name, ino=ino, mode=mode, size=size, mtime=mtime,
                      is_dir=(mode & S_IFMT) == S_IFDIR, hidden=name.startswith("."))
        if (mode & S_IFMT) == S_IFLNK:
            e.symlink = self.read_inode(ino).decode("utf-8", "replace")
        return e

    def children(self, ino: int) -> List[F2fsEntry]:
        hit = self._cache.get(ino)
        if hit is None:
            hit = [self._entry(n, i) for n, i, _t in self._dir_raw(ino)]
            if len(self._cache) > 256:
                self._cache.clear()
            self._cache[ino] = hit
        return hit

    def resolve(self, path: str) -> F2fsEntry:
        node = F2fsEntry(name="", ino=self.root_ino, is_dir=True)
        for part in [p for p in path.replace("\\", "/").split("/") if p]:
            if not node.is_dir:
                raise F2fsError(tr("Dizin degil: {}", path))
            match = next((c for c in self.children(node.ino) if c.name == part), None)
            if match is None:
                raise F2fsError(tr("Bulunamadi: {}", path))
            node = match
        return node

    def listdir(self, path: str = "/") -> List[F2fsEntry]:
        node = self.resolve(path)
        if not node.is_dir:
            raise F2fsError(tr("Dizin degil: {}", path))
        return list(self.children(node.ino))

    def read_file(self, path: str, max_bytes: int = -1) -> bytes:
        e = self.resolve(path)
        if e.is_dir:
            raise F2fsError(tr("Dizin okunamaz: {}", path))
        return self.read_inode(e.ino, max_bytes)

    def extract(self, path: str, dest: str) -> str:
        e = self.resolve(path)
        if e.is_dir:
            os.makedirs(dest, exist_ok=True)
            for c in self.children(e.ino):
                if not c.symlink:
                    self.extract(path.rstrip("/") + "/" + c.name, os.path.join(dest, c.name))
            return dest
        os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(self.read_inode(e.ino))
        if e.mtime:
            ts = e.mtime.timestamp()
            os.utime(dest, (ts, ts))
        return dest

    def stats(self) -> Dict[str, int]:
        total = self.user_blocks * BLOCK
        used = self.valid_blocks * BLOCK
        return {"total_bytes": total, "used_bytes": used,
                "free_bytes": max(0, total - used), "cluster_size": BLOCK}
