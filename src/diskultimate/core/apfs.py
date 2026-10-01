"""APFS salt okuma — saf Python (ADR 0071).

Kaynak: Apple File System Reference (Apple, 2020). Little-endian.

* Her nesnenin 32 bayt basligi: Fletcher-64 saglama, oid, xid, tur, alt tur.
* Kapsayici ustblogu (NXSB) blok 0'da; gecerli olan, checkpoint tanimlayici
  alanindaki saglamasi dogru ve xid'i en buyuk NXSB'dir.
* Nesne haritasi (omap): sanal oid -> fiziksel blok, B-agaci (anahtar oid +
  xid; istenen xid'e esit ya da kucuk en buyuk kayit).
* Birim ustblogu (APSB): kapsayici omap'iyle cozulur; dosya sistemi agaci
  birimin kendi omap'iyle cozulen sanal dugumlerden olusur (muhurlu birimde
  fiziksel).
* Dosya sistemi kayitlari: anahtar ilk 8 bayt = nesne kimligi (60 bit) +
  tur (4 bit): 3 inode, 4 xattr, 8 dosya kapsami, 9 dizin girisi. Dizin
  girisleri ust kimlige gore ardisik; ad karmasina gerek kalmadan taranir.
* Dosya verisi: inode'un xfield'indaki dstream boyu + private_id'ye bagli
  kapsamlar (fiziksel blok 0 = seyrek). Sembolik bag "com.apple.fs.symlink"
  xattr'inda. decmpfs (zlib) sikistirmasi HFS+ ile ayni bicim.

Sifreli birim (APFS_FS_UNENCRYPTED biti yok) acik hatayla reddedilir.
LZVN/LZFSE sikistirmasi desteklenmez (acik hata).
"""
from __future__ import annotations

import datetime
import os
import struct
import zlib
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple

from .image import BlockDevice
from ..i18n import tr

NX_MAGIC = b"NXSB"
APSB_MAGIC = b"APSB"
OBJ_TYPE_MASK = 0x0000FFFF
OBJ_PHYSICAL = 0x40000000
OBJECT_TYPE_NX_SUPERBLOCK = 0x01
OBJECT_TYPE_BTREE = 0x02
OBJECT_TYPE_BTREE_NODE = 0x03
OBJECT_TYPE_OMAP = 0x0B
BTNODE_ROOT, BTNODE_LEAF, BTNODE_FIXED = 1, 2, 4
J_INODE, J_XATTR, J_FILE_EXTENT, J_DIR_REC = 3, 4, 8, 9
OBJ_ID_MASK = (1 << 60) - 1
ROOT_DIR_INO = 2
APFS_FS_UNENCRYPTED = 0x1
INCOMPAT_CASE_INSENSITIVE = 0x1
INO_EXT_TYPE_DSTREAM = 8
XATTR_DATA_STREAM, XATTR_DATA_EMBEDDED = 1, 2
S_IFMT, S_IFDIR, S_IFLNK = 0o170000, 0o040000, 0o120000
UF_COMPRESSED = 0x20
APFS_PART_GUID = "7C3457EF-0000-11AA-AA11-00306543ECAC"


class ApfsError(Exception):
    pass


def fletcher64(block: bytes) -> int:
    words = struct.unpack_from(f"<{(len(block) - 8) // 4}I", block, 8)
    mod = 0xFFFFFFFF
    s1 = s2 = 0
    for w in words:
        s1 = (s1 + w) % mod
        s2 = (s2 + s1) % mod
    c1 = mod - ((s1 + s2) % mod)
    c2 = mod - ((s1 + c1) % mod)
    return (c2 << 32) | c1


def _valid(block: bytes) -> bool:
    return struct.unpack_from("<Q", block, 0)[0] == fletcher64(block)


@dataclass
class ApfsEntry:
    name: str
    ino: int
    is_dir: bool = False
    size: int = 0
    mode: int = 0
    mtime: Optional[datetime.datetime] = None
    symlink: str = ""
    hidden: bool = False
    volume: Optional["_Volume"] = field(default=None, repr=False)
    is_volume: bool = False


def _time(ns: int) -> Optional[datetime.datetime]:
    try:
        return datetime.datetime.fromtimestamp(ns / 1e9)
    except (OverflowError, OSError, ValueError):
        return None


class _BTree:
    """APFS B-agaci (omap ya da dosya sistemi agaci)."""

    def __init__(self, fs: "ApfsFS", root: int, resolve=None):
        self.fs = fs
        self.root = root
        self.resolve = resolve                    # sanal oid -> fiziksel (yoksa fiziksel)

    def _node(self, oid: int) -> bytes:
        addr = self.resolve(oid) if self.resolve else oid
        blk = self.fs.block(addr)
        if (struct.unpack_from("<I", blk, 24)[0] & OBJ_TYPE_MASK) not in (
                OBJECT_TYPE_BTREE, OBJECT_TYPE_BTREE_NODE):
            raise ApfsError(tr("APFS B-agaci dugumu bozuk: {}", addr))
        return blk

    def _entries(self, blk: bytes) -> Tuple[List[Tuple[bytes, bytes]], bool]:
        flags, level, nkeys = struct.unpack_from("<HHI", blk, 32)
        toff, tlen = struct.unpack_from("<HH", blk, 40)
        keys_start = 56 + toff + tlen
        val_end = len(blk) - (40 if flags & BTNODE_ROOT else 0)
        out = []
        for i in range(nkeys):
            if flags & BTNODE_FIXED:
                ko, vo = struct.unpack_from("<HH", blk, 56 + toff + 4 * i)
                kl, vl = 16, (16 if flags & BTNODE_LEAF else 8)
            else:
                ko, kl, vo, vl = struct.unpack_from("<HHHH", blk, 56 + toff + 8 * i)
            key = blk[keys_start + ko:keys_start + ko + kl]
            val = blk[val_end - vo:val_end - vo + vl] if vo != 0xFFFF else b""
            out.append((key, val))
        return out, bool(flags & BTNODE_LEAF)

    def walk(self, start, keyfn, oid: Optional[int] = None,
             depth: int = 0) -> Iterator[Tuple[bytes, bytes]]:
        """`keyfn(anahtar) >= start` olan kayitlari sirayla uretir."""
        if depth > 16:
            raise ApfsError(tr("APFS B-agaci cok derin"))
        entries, leaf = self._entries(self._node(self.root if oid is None else oid))
        if leaf:
            for k, v in entries:
                if keyfn(k) >= start:
                    yield k, v
            return
        first = 0
        for i, (k, _v) in enumerate(entries):
            if keyfn(k) < start:
                first = i
            else:
                break
        for k, v in entries[first:]:
            yield from self.walk(start, keyfn, struct.unpack_from("<Q", v, 0)[0], depth + 1)


class _Omap:
    def __init__(self, fs: "ApfsFS", omap_paddr: int, xid: int):
        blk = fs.block(omap_paddr)
        if (struct.unpack_from("<I", blk, 24)[0] & OBJ_TYPE_MASK) != OBJECT_TYPE_OMAP:
            raise ApfsError(tr("APFS nesne haritasi bozuk"))
        self.tree = _BTree(fs, struct.unpack_from("<Q", blk, 48)[0])
        self.xid = xid
        self._cache: Dict[int, int] = {}

    def lookup(self, oid: int) -> int:
        hit = self._cache.get(oid)
        if hit is not None:
            return hit
        best = None
        for k, v in self.tree.walk((oid, 0), lambda k: struct.unpack_from("<QQ", k, 0)):
            koid, kxid = struct.unpack_from("<QQ", k, 0)
            if koid != oid:
                break
            if kxid <= self.xid:
                best = struct.unpack_from("<Q", v, 8)[0]
        if best is None:
            raise ApfsError(tr("APFS nesnesi haritada yok: {}", oid))
        self._cache[oid] = best
        return best


def _jkey(k: bytes) -> Tuple[int, int]:
    v = struct.unpack_from("<Q", k, 0)[0]
    return v & OBJ_ID_MASK, v >> 60


class _Volume:
    def __init__(self, fs: "ApfsFS", paddr: int):
        self.fs = fs
        sb = fs.block(paddr)
        if sb[32:36] != APSB_MAGIC:
            raise ApfsError(tr("APFS birim ustblogu bulunamadi"))
        self.sb = sb
        self.incompat = struct.unpack_from("<Q", sb, 56)[0]
        self.fs_flags = struct.unpack_from("<Q", sb, 264)[0]
        self.name = sb[704:960].split(b"\x00")[0].decode("utf-8", "replace")
        self.root_tree_type = struct.unpack_from("<I", sb, 116)[0]
        self.encrypted = not self.fs_flags & APFS_FS_UNENCRYPTED
        self.case_insensitive = bool(self.incompat & INCOMPAT_CASE_INSENSITIVE)
        omap_oid, root_oid = struct.unpack_from("<QQ", sb, 128)
        self.files, self.dirs = struct.unpack_from("<QQ", sb, 184)
        self.alloc_blocks = struct.unpack_from("<Q", sb, 88)[0]
        self.omap = None
        self.tree = None
        self._cache: Dict[int, List[ApfsEntry]] = {}
        if self.encrypted:
            return
        self.omap = _Omap(fs, omap_oid, fs.xid)
        resolve = None if self.root_tree_type & OBJ_PHYSICAL else self.omap.lookup
        self.tree = _BTree(fs, root_oid, resolve)

    def _require(self) -> None:
        if self.encrypted:
            raise ApfsError(tr("Sifreli APFS birimi ({}); anahtar olmadan okunamaz.", self.name))

    def records(self, oid: int, typ: int) -> Iterator[Tuple[bytes, bytes]]:
        self._require()
        for k, v in self.tree.walk((oid, typ), _jkey):
            if _jkey(k) != (oid, typ):
                break
            yield k, v

    def inode(self, ino: int) -> bytes:
        for _k, v in self.records(ino, J_INODE):
            return v
        raise ApfsError(tr("APFS inode bulunamadi: {}", ino))

    @staticmethod
    def _xfield(val: bytes, start: int, wanted: int) -> Optional[bytes]:
        if len(val) < start + 4:
            return None
        n, _used = struct.unpack_from("<HH", val, start)
        pos = start + 4 + 4 * n
        for i in range(n):
            typ, _flags, size = struct.unpack_from("<BBH", val, start + 4 + 4 * i)
            if typ == wanted:
                return val[pos:pos + size]
            pos += (size + 7) & ~7
        return None

    def xattr(self, ino: int, name: str) -> Optional[bytes]:
        for k, v in self.records(ino, J_XATTR):
            nlen = struct.unpack_from("<H", k, 8)[0]
            if k[10:10 + nlen].rstrip(b"\x00").decode("utf-8", "replace") != name:
                continue
            flags, dlen = struct.unpack_from("<HH", v, 0)
            data = v[4:4 + dlen]
            if flags & XATTR_DATA_EMBEDDED:
                return data
            oid, size = struct.unpack_from("<QQ", data, 0)
            return self.read_stream(oid, size)
        return None

    def read_stream(self, private_id: int, size: int, max_bytes: int = -1) -> bytes:
        limit = size if max_bytes < 0 else min(size, max_bytes)
        out = bytearray(limit)
        bs = self.fs.block_size
        for k, v in self.records(private_id, J_FILE_EXTENT):
            logical = struct.unpack_from("<Q", k, 8)[0]
            if logical >= limit:
                break
            length = struct.unpack_from("<Q", v, 0)[0] & ((1 << 56) - 1)
            phys, crypto = struct.unpack_from("<QQ", v, 8)
            if phys == 0:
                continue                                 # seyrek
            n = min(length, limit - logical)
            out[logical:logical + n] = self.fs.dev.read(phys * bs, n)
        return bytes(out)

    def _decmpfs(self, ino: int, size: int) -> bytes:
        raw = self.xattr(ino, "com.apple.decmpfs")
        if not raw or raw[:4] != b"fpmc":
            raise ApfsError(tr("APFS sikistirma ozniteligi bozuk"))
        kind = struct.unpack_from("<I", raw, 4)[0]
        usize = struct.unpack_from("<Q", raw, 8)[0]
        payload = raw[16:]
        if kind == 1:
            return payload[:usize]
        if kind == 3:
            return (payload[1:] if payload[:1] == b"\xff" else zlib.decompress(payload))[:usize]
        if kind == 4:
            rsrc = self.xattr(ino, "com.apple.ResourceFork") or b""
            data_off = struct.unpack_from(">I", rsrc, 0)[0]
            base = data_off + 4
            blocks = struct.unpack_from("<I", rsrc, base)[0]
            out = bytearray()
            for i in range(blocks):
                off, length = struct.unpack_from("<II", rsrc, base + 4 + i * 8)
                chunk = rsrc[base + off:base + off + length]
                out += chunk[1:] if chunk[:1] == b"\xff" else zlib.decompress(chunk)
            return bytes(out[:usize])
        raise ApfsError(tr("Desteklenmeyen APFS sikistirmasi (tur {}) — LZVN/LZFSE "
                           "bu surumde acilamiyor.", kind))

    def read_inode(self, ino: int, max_bytes: int = -1) -> bytes:
        val = self.inode(ino)
        bsd_flags = struct.unpack_from("<I", val, 68)[0]
        private_id = struct.unpack_from("<Q", val, 8)[0]
        dstream = self._xfield(val, 92, INO_EXT_TYPE_DSTREAM)
        size = struct.unpack_from("<Q", dstream, 0)[0] if dstream else 0
        if bsd_flags & UF_COMPRESSED:
            data = self._decmpfs(ino, size)
            return data if max_bytes < 0 else data[:max_bytes]
        return self.read_stream(private_id, size, max_bytes)

    def _entry(self, name: str, ino: int) -> ApfsEntry:
        val = self.inode(ino)
        mode = struct.unpack_from("<H", val, 80)[0]
        bsd_flags = struct.unpack_from("<I", val, 68)[0]
        dstream = self._xfield(val, 92, INO_EXT_TYPE_DSTREAM)
        size = struct.unpack_from("<Q", dstream, 0)[0] if dstream else 0
        if bsd_flags & UF_COMPRESSED and len(val) >= 92:
            size = struct.unpack_from("<Q", val, 84)[0] or size
        e = ApfsEntry(name=name, ino=ino, mode=mode, size=size,
                      is_dir=(mode & S_IFMT) == S_IFDIR,
                      mtime=_time(struct.unpack_from("<Q", val, 24)[0]),
                      hidden=name.startswith("."), volume=self)
        if (mode & S_IFMT) == S_IFLNK:
            target = self.xattr(ino, "com.apple.fs.symlink") or b""
            e.symlink = target.rstrip(b"\x00").decode("utf-8", "replace")
        return e

    def children(self, ino: int) -> List[ApfsEntry]:
        self._require()
        hit = self._cache.get(ino)
        if hit is not None:
            return hit
        out = []
        for k, v in self.records(ino, J_DIR_REC):
            if len(k) >= 12 and len(k) == 12 + (struct.unpack_from("<I", k, 8)[0] & 0x3FF):
                nlen, name_off = struct.unpack_from("<I", k, 8)[0] & 0x3FF, 12   # karmali
            else:
                nlen, name_off = struct.unpack_from("<H", k, 8)[0], 10
            name = k[name_off:name_off + nlen].rstrip(b"\x00").decode("utf-8", "surrogateescape")
            file_id = struct.unpack_from("<Q", v, 0)[0]
            out.append(self._entry(name, file_id))
        if len(self._cache) > 256:
            self._cache.clear()
        self._cache[ino] = out
        return out


class ApfsFS:
    def __init__(self, dev: BlockDevice):
        self.dev = dev
        nx = dev.read(0, 4096)
        if nx[32:36] != NX_MAGIC:
            raise ApfsError(tr("APFS kapsayici ustblogu bulunamadi"))
        self.block_size = struct.unpack_from("<I", nx, 36)[0]
        if self.block_size < 4096 or self.block_size & (self.block_size - 1):
            raise ApfsError(tr("Gecersiz APFS blok boyu: {}", self.block_size))
        nx = self._latest(dev.read(0, self.block_size))
        self.nx = nx
        self.xid = struct.unpack_from("<Q", nx, 16)[0]
        self.block_count = struct.unpack_from("<Q", nx, 40)[0]
        omap = _Omap(self, struct.unpack_from("<Q", nx, 160)[0], self.xid)
        max_fs = struct.unpack_from("<I", nx, 180)[0]
        self.volumes: List[_Volume] = []
        for i in range(min(max_fs, 100)):
            oid = struct.unpack_from("<Q", nx, 184 + 8 * i)[0]
            if oid:
                self.volumes.append(_Volume(self, omap.lookup(oid)))
        if not self.volumes:
            raise ApfsError(tr("APFS kapsayicisinda birim yok"))
        self.label = self.volumes[0].name if len(self.volumes) == 1 else \
            ", ".join(v.name for v in self.volumes)

    def block(self, addr: int) -> bytes:
        return self.dev.read(addr * self.block_size, self.block_size)

    def _latest(self, nx0: bytes) -> bytes:
        """Checkpoint tanimlayici alanindaki en yeni gecerli NXSB."""
        desc_blocks, _data_blocks, desc_base = struct.unpack_from("<IIQ", nx0, 104)
        best = nx0 if _valid(nx0) else None
        if desc_blocks & 0x80000000:
            return best or nx0                         # B-agacli alan (seyrek): blok 0
        for i in range(desc_blocks):
            blk = self.block(desc_base + i)
            if blk[32:36] != NX_MAGIC or not _valid(blk):
                continue
            if (struct.unpack_from("<I", blk, 24)[0] & OBJ_TYPE_MASK) != OBJECT_TYPE_NX_SUPERBLOCK:
                continue
            if best is None or struct.unpack_from("<Q", blk, 16)[0] > \
                    struct.unpack_from("<Q", best, 16)[0]:
                best = blk
        if best is None:
            raise ApfsError(tr("Gecerli APFS checkpoint bulunamadi"))
        return best

    # ---- yol: tek birimde dogrudan, cok birimde birimler klasor ------------
    def _roots(self) -> List[ApfsEntry]:
        return [ApfsEntry(name=v.name or f"Volume {i + 1}", ino=ROOT_DIR_INO, is_dir=True,
                          volume=v, is_volume=True) for i, v in enumerate(self.volumes)]

    def resolve(self, path: str) -> ApfsEntry:
        parts = [p for p in path.replace("\\", "/").split("/") if p]
        if len(self.volumes) == 1:
            node = self._roots()[0]
        else:
            node = ApfsEntry(name="", ino=0, is_dir=True)
        for part in parts:
            if not node.is_dir:
                raise ApfsError(tr("Dizin degil: {}", path))
            kids = self._roots() if node.volume is None else node.volume.children(node.ino)
            match = next((c for c in kids if c.name == part), None)
            if match is None and (node.volume is None or node.volume.case_insensitive):
                low = part.casefold()
                match = next((c for c in kids if c.name.casefold() == low), None)
            if match is None:
                raise ApfsError(tr("Bulunamadi: {}", path))
            node = match
        return node

    def listdir(self, path: str = "/") -> List[ApfsEntry]:
        node = self.resolve(path)
        if not node.is_dir:
            raise ApfsError(tr("Dizin degil: {}", path))
        if node.volume is None:
            return self._roots()
        return list(node.volume.children(node.ino))

    def read_file(self, path: str, max_bytes: int = -1) -> bytes:
        e = self.resolve(path)
        if e.is_dir:
            raise ApfsError(tr("Dizin okunamaz: {}", path))
        return e.volume.read_inode(e.ino, max_bytes)

    def extract(self, path: str, dest: str) -> str:
        e = self.resolve(path)
        if e.is_dir:
            os.makedirs(dest, exist_ok=True)
            for c in self.listdir(path):
                if not c.symlink:
                    self.extract(path.rstrip("/") + "/" + c.name, os.path.join(dest, c.name))
            return dest
        os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(e.volume.read_inode(e.ino))
        if e.mtime:
            ts = e.mtime.timestamp()
            os.utime(dest, (ts, ts))
        return dest

    def stats(self) -> Dict[str, int]:
        total = self.block_count * self.block_size
        used = sum(v.alloc_blocks for v in self.volumes) * self.block_size
        return {"total_bytes": total, "used_bytes": used,
                "free_bytes": max(0, total - used), "cluster_size": self.block_size}
