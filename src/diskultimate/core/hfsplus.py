"""HFS+ / HFSX salt okuma (Apple TN1150).

Yerlesim (tum sayilar big-endian):
  * birim basligi birimin 1024. baytinda: imza "H+" (HFS+) / "HX" (HFSX),
    blok boyutu @40, toplam/bos blok @44/@48, ozel dosyalarin catal (fork)
    tanimlari: ayirma @112, kapsam tasmasi @192, katalog @272, oznitelik @352.
  * catal (ForkData, 80 bayt): mantiksal boy(8) clump(4) blok(4) + 8 kapsam
    (baslangic blogu(4), blok sayisi(4)). 8'den fazla kapsam "kapsam tasmasi"
    B-agacindadir (anahtar: catal turu, dosya kimligi, baslangic blogu).
  * B-agaci dugumu: ileri(4) geri(4) tur(1, -1 yaprak / 0 ara / 1 baslik)
    yukseklik(1) kayit sayisi(2); kayit ofsetleri dugumun sonunda, tersten.
  * katalog anahtari: uzunluk(2) ust kimlik(4) ad(HFSUniStr255, UTF-16BE,
    ayristirilmis). Kayit turleri: 1 klasor, 2 dosya, 3/4 klasor/dosya
    "iplik" kaydi (kimlik -> ust + ad; anahtari (kimlik, "")).

Bir klasorun cocuklari yaprak duzeyinde **ardisik** durur: once kendi iplik
kaydi (kimlik, ""), sonra (kimlik, ad) kayitlari. Bu yuzden listeleme icin
ad karsilastirmasi gerekmez: (kimlik, "") anahtarina inilip ust kimlik
degisene kadar yaprak zinciri izlenir. Ad aramasi listelenen cocuklar
uzerinde NFD + (HFS+ icin) buyuk/kucuk harf katlamasiyla yapilir.

Desteklenenler: sabit baglar (dosya: "iNode<n>", klasor: "dir_<n>"),
sembolik baglar, decmpfs zlib sikistirmasi (tur 3 satir ici, tur 4 kaynak
catalinda), eski HFS sarmalayicisi icine gomulu HFS+ birimi.
Desteklenmeyenler (acik hata): LZVN/LZFSE sikistirma; gunluk yeniden
oynatilmaz (kirli gunlukte `journal_dirty` bayragi).
"""
from __future__ import annotations

import datetime
import os
import struct
import zlib
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple

from .hfsunicode import fold_key, hfs_display, hfs_nfd, legacy_tonos
from .image import BlockDevice, PartitionView
from ..i18n import tr

HEADER_OFFSET = 1024
SIG_HFSPLUS = b"H+"
SIG_HFSX = b"HX"
SIG_HFS = b"BD"

ROOT_PARENT_ID = 1
ROOT_FOLDER_ID = 2
EXTENTS_FILE_ID = 3
CATALOG_FILE_ID = 4
ATTRIBUTES_FILE_ID = 8

REC_FOLDER, REC_FILE, REC_FOLDER_THREAD, REC_FILE_THREAD = 1, 2, 3, 4
NODE_LEAF, NODE_INDEX, NODE_HEADER = -1, 0, 1
FORK_DATA, FORK_RSRC = 0x00, 0xFF

VOL_JOURNALED = 1 << 13
UF_COMPRESSED = 0x20
FINDER_INVISIBLE = 0x4000
S_IFMT, S_IFLNK = 0o170000, 0o120000

FILE_LINK_PRIVATE = "\0\0\0\0HFS+ Private Data"
DIR_LINK_PRIVATE = ".HFS+ Private Directory Data\r"
HIDDEN_METADATA = {FILE_LINK_PRIVATE, DIR_LINK_PRIVATE}

HFS_EPOCH = datetime.datetime(1904, 1, 1, tzinfo=datetime.timezone.utc)
DECMPFS_ATTR = "com.apple.decmpfs"
DECMPFS_MAGIC = b"fpmc"


class HfsError(Exception):
    pass


def _date(value: int) -> Optional[datetime.datetime]:
    if not value:
        return None
    try:
        return (HFS_EPOCH + datetime.timedelta(seconds=value)).astimezone()
    except (OverflowError, ValueError, OSError):
        return None


def _extents(raw: bytes, off: int) -> List[Tuple[int, int]]:
    out = []
    for i in range(8):
        start, count = struct.unpack_from(">II", raw, off + i * 8)
        if count:
            out.append((start, count))
    return out


@dataclass
class Fork:
    size: int
    blocks: int
    extents: List[Tuple[int, int]]

    @classmethod
    def parse(cls, raw: bytes, off: int) -> "Fork":
        size, _clump, blocks = struct.unpack_from(">QII", raw, off)
        return cls(size, blocks, _extents(raw, off + 16))


@dataclass
class HfsEntry:
    name: str                       # gosterim adi (NFC, '/' -> ':')
    cnid: int
    parent: int
    is_dir: bool
    size: int = 0
    mtime: Optional[datetime.datetime] = None
    ctime: Optional[datetime.datetime] = None
    mode: int = 0
    uid: int = 0
    gid: int = 0
    hidden: bool = False
    symlink: str = ""
    compressed: bool = False
    hardlink: bool = False
    data: Optional[Fork] = None
    rsrc: Optional[Fork] = None
    raw_name: str = field(default="", repr=False)   # diskteki (NFD) ad


def _decode_name(raw: bytes, off: int) -> Tuple[str, int]:
    length = struct.unpack_from(">H", raw, off)[0]
    text = raw[off + 2:off + 2 + length * 2].decode("utf-16-be", "replace")
    return text, off + 2 + length * 2


class _BTree:
    """HFS+ B-agaci (katalog, kapsam tasmasi, oznitelik)."""

    def __init__(self, fs: "HfsPlusFS", file_id: int, fork: Fork):
        self.fs = fs
        self.file_id = file_id
        self.fork = fork
        head = fs.read_fork(fork, 0, 512, file_id=file_id)
        if len(head) < 0x70:
            raise HfsError(tr("B-agaci basligi okunamadi"))
        (self.depth, self.root, _leafs, self.first_leaf, _last,
         self.node_size, self.max_key_len) = struct.unpack_from(">HIIIIHH", head, 14)
        self.attributes = struct.unpack_from(">I", head, 14 + 0x26)[0]
        self.compare_type = head[14 + 0x25]
        if self.node_size < 512 or self.node_size & (self.node_size - 1):
            raise HfsError(tr("Gecersiz B-agaci dugum boyutu: {}", self.node_size))
        self._cache: Dict[int, bytes] = {}

    @property
    def variable_index_keys(self) -> bool:
        return bool(self.attributes & 0x04)

    def node(self, number: int) -> bytes:
        hit = self._cache.get(number)
        if hit is None:
            hit = self.fs.read_fork(self.fork, number * self.node_size,
                                    self.node_size, file_id=self.file_id)
            if len(hit) != self.node_size:
                raise HfsError(tr("B-agaci dugumu okunamadi: {}", number))
            if len(self._cache) > 512:
                self._cache.clear()
            self._cache[number] = hit
        return hit

    def records(self, data: bytes) -> List[bytes]:
        count = struct.unpack_from(">H", data, 10)[0]
        ends = [struct.unpack_from(">H", data, self.node_size - 2 * (i + 1))[0]
                for i in range(count + 1)]
        return [data[ends[i]:ends[i + 1]] for i in range(count)]

    @staticmethod
    def kind(data: bytes) -> int:
        return struct.unpack_from(">b", data, 8)[0]

    def split(self, rec: bytes, index: bool) -> Tuple[bytes, bytes]:
        """(anahtar, deger). Anahtar uzunluk alanini icerir."""
        key_len = struct.unpack_from(">H", rec, 0)[0]
        if index and not self.variable_index_keys:
            key_len = self.max_key_len
        end = 2 + key_len
        if not index and end & 1:
            end += 1
        return rec[:2 + key_len], rec[end:] if not index else rec[2 + key_len:]

    def leaf_from(self, less_or_equal) -> Iterator[Tuple[bytes, bytes]]:
        """`less_or_equal(anahtar)` dogru olan son kayittan baslayip yaprak
        zincirini sirayla dolasir."""
        number = self.root
        if not number:
            return
        for _ in range(self.depth + 1):
            data = self.node(number)
            if self.kind(data) == NODE_LEAF:
                break
            child = None
            for rec in self.records(data):
                key, value = self.split(rec, index=True)
                if child is not None and not less_or_equal(key):
                    break
                child = struct.unpack_from(">I", value, 0)[0]
            if child is None:
                return
            number = child
        seen = 0
        while number:
            data = self.node(number)
            for rec in self.records(data):
                yield self.split(rec, index=False)
            number = struct.unpack_from(">I", data, 0)[0]
            seen += 1
            if seen > 10_000_000:
                raise HfsError(tr("B-agaci yaprak zinciri dongude"))

    def all_leaves(self) -> Iterator[Tuple[bytes, bytes]]:
        number = self.first_leaf
        while number:
            data = self.node(number)
            for rec in self.records(data):
                yield self.split(rec, index=False)
            number = struct.unpack_from(">I", data, 0)[0]


class HfsPlusFS:
    """HFS+ / HFSX birimi (salt okunur)."""

    def __init__(self, dev: BlockDevice):
        self.dev = dev
        header = dev.read(HEADER_OFFSET, 512)
        if header[:2] == SIG_HFS and header[0x7C:0x7E] in (SIG_HFSPLUS, SIG_HFSX):
            self.dev = self._embedded(dev, header)
            header = self.dev.read(HEADER_OFFSET, 512)
        if header[:2] not in (SIG_HFSPLUS, SIG_HFSX):
            raise HfsError(tr("HFS+ birim basligi bulunamadi"))
        self.header = header
        self.case_sensitive = header[:2] == SIG_HFSX
        self.fs_type = "HFSX" if self.case_sensitive else "HFS+"
        self.attributes = struct.unpack_from(">I", header, 4)[0]
        self.journal_block = struct.unpack_from(">I", header, 12)[0]
        self.block_size = struct.unpack_from(">I", header, 40)[0]
        self.total_blocks, self.free_blocks = struct.unpack_from(">II", header, 44)
        self.file_count, self.folder_count = struct.unpack_from(">II", header, 32)
        if self.block_size < 512 or self.block_size & (self.block_size - 1):
            raise HfsError(tr("Gecersiz HFS+ blok boyutu: {}", self.block_size))
        self._overflow: Optional[Dict[Tuple[int, int], List[Tuple[int, int]]]] = None
        self.extents_tree = _BTree(self, EXTENTS_FILE_ID, Fork.parse(header, 192))
        self.catalog = _BTree(self, CATALOG_FILE_ID, Fork.parse(header, 272))
        if self.catalog.compare_type == 0xBC:          # ikili karsilastirma
            self.case_sensitive = True
        attr_fork = Fork.parse(header, 352)
        self.attributes_tree = (_BTree(self, ATTRIBUTES_FILE_ID, attr_fork)
                                if attr_fork.blocks else None)
        self._dir_cache: Dict[int, List[HfsEntry]] = {}
        self._private: Dict[str, int] = {}
        self.label = self._volume_name()
        self.journal_dirty = self._journal_dirty()

    # ---- yerlesim ---------------------------------------------------------
    @staticmethod
    def _embedded(dev: BlockDevice, mdb: bytes) -> BlockDevice:
        """Eski HFS sarmalayicisinin icindeki HFS+ birimi."""
        alloc_size = struct.unpack_from(">I", mdb, 0x14)[0]
        first_sector = struct.unpack_from(">H", mdb, 0x1C)[0]
        start, count = struct.unpack_from(">HH", mdb, 0x7E)
        offset = first_sector * 512 + start * alloc_size
        ss = dev.sector_size
        if offset % ss:
            raise HfsError(tr("Gomulu HFS+ birimi sektor sinirinda degil"))
        return PartitionView(dev, offset // ss, count * alloc_size // ss)

    def _journal_dirty(self) -> bool:
        if not (self.attributes & VOL_JOURNALED) or not self.journal_block:
            return False
        try:
            info = self.dev.read(self.journal_block * self.block_size, 52)
            flags = struct.unpack_from(">I", info, 0)[0]
            if flags & 0x2:                       # gunluk baska aygitta
                return False
            offset = struct.unpack_from(">Q", info, 36)[0]
            head = self.dev.read(offset, 32)
            magic = head[:4]
            if magic not in (b"JNLx", b"xLNJ"):
                return False
            fmt = ">" if magic == b"JNLx" else "<"
            start, end = struct.unpack_from(fmt + "QQ", head, 8)
            return start != end
        except Exception:                          # noqa: BLE001
            return False

    def _overflow_map(self) -> Dict[Tuple[int, int], List[Tuple[int, int]]]:
        if self._overflow is None:
            found: Dict[Tuple[int, int], List[Tuple[int, int, int]]] = {}
            if self.extents_tree.fork.blocks and self.extents_tree.root:
                for key, value in self.extents_tree.all_leaves():
                    if len(key) < 12:
                        continue
                    fork_type = key[2]
                    file_id, start_block = struct.unpack_from(">II", key, 4)
                    found.setdefault((file_id, fork_type), []).append(
                        (start_block, value))
            self._overflow = {}
            for k, items in found.items():
                runs: List[Tuple[int, int]] = []
                for _start, value in sorted(items, key=lambda it: it[0]):
                    runs.extend(_extents(value, 0))
                self._overflow[k] = runs
        return self._overflow

    def fork_runs(self, fork: Fork, file_id: int = 0,
                  fork_type: int = FORK_DATA) -> List[Tuple[int, int]]:
        runs = list(fork.extents)
        have = sum(c for _s, c in runs)
        if have < fork.blocks and file_id and file_id != EXTENTS_FILE_ID:
            runs += self._overflow_map().get((file_id, fork_type), [])
        return runs

    def read_fork(self, fork: Fork, offset: int = 0, length: int = -1,
                  file_id: int = 0, fork_type: int = FORK_DATA) -> bytes:
        size = fork.size
        if length < 0 or offset + length > size:
            length = max(0, size - offset)
        out = bytearray()
        bs = self.block_size
        pos = 0                                    # catal icindeki bayt
        for start, count in self.fork_runs(fork, file_id, fork_type):
            run_bytes = count * bs
            if pos + run_bytes <= offset:
                pos += run_bytes
                continue
            skip = max(0, offset - pos)
            take = min(run_bytes - skip, length - len(out))
            out += self.dev.read(start * bs + skip, take)
            pos += run_bytes
            if len(out) >= length:
                break
        if len(out) < length:
            raise HfsError(tr("Catal kapsamlari eksik (dosya kimligi {})", file_id))
        return bytes(out)

    # ---- katalog ----------------------------------------------------------
    @staticmethod
    def _key_parent(key: bytes) -> int:
        return struct.unpack_from(">I", key, 2)[0]

    def _entry(self, key: bytes, value: bytes) -> Optional[HfsEntry]:
        kind = struct.unpack_from(">h", value, 0)[0]
        if kind not in (REC_FOLDER, REC_FILE):
            return None
        parent = self._key_parent(key)
        raw_name, _ = _decode_name(key, 6)
        cnid = struct.unpack_from(">I", value, 8)[0]
        create, modify = struct.unpack_from(">II", value, 12)
        uid, gid, _admin, owner_flags, mode, special = \
            struct.unpack_from(">IIBBHI", value, 32)
        finder_flags = struct.unpack_from(">H", value, 48 + 8)[0]
        name = hfs_display(raw_name).replace("/", ":")
        entry = HfsEntry(name=name, cnid=cnid, parent=parent,
                         is_dir=kind == REC_FOLDER, mtime=_date(modify),
                         ctime=_date(create), mode=mode, uid=uid, gid=gid,
                         hidden=bool(finder_flags & FINDER_INVISIBLE)
                         or name.startswith("."),
                         raw_name=raw_name)
        if kind == REC_FOLDER:
            return entry
        entry.data = Fork.parse(value, 88)
        entry.rsrc = Fork.parse(value, 168)
        entry.size = entry.data.size
        entry.compressed = bool(owner_flags & UF_COMPRESSED)
        ftype, creator = value[48:52], value[52:56]
        if ftype == b"hlnk" and creator == b"hfs+":
            self._follow_file_link(entry, special)
        elif ftype == b"fdrp" and creator == b"MACS":
            self._follow_dir_link(entry, special)
        if (entry.mode & S_IFMT) == S_IFLNK:
            try:
                entry.symlink = self.read_fork(entry.data, 0, 4096,
                                               entry.cnid).decode("utf-8", "replace")
            except HfsError:
                entry.symlink = "?"
        if entry.compressed:
            entry.size = self._decmpfs_size(entry)
        return entry

    def _private_dir(self, name: str) -> int:
        """Sabit bag klasoru; kok, bag izlenmeden ham kayitlarla taranir
        (kok listelemesi baglari izlerken buraya geri doner)."""
        if name not in self._private:
            self._private[name] = 0
            for key, value in self._raw_children(ROOT_FOLDER_ID):
                if struct.unpack_from(">h", value, 0)[0] != REC_FOLDER:
                    continue
                if _decode_name(key, 6)[0] == name:
                    self._private[name] = struct.unpack_from(">I", value, 8)[0]
        return self._private[name]

    def _raw_children(self, parent: int) -> Iterator[Tuple[bytes, bytes]]:
        def not_after(key: bytes) -> bool:
            pid = self._key_parent(key)
            return pid < parent or (pid == parent
                                    and struct.unpack_from(">H", key, 6)[0] == 0)

        for key, value in self.catalog.leaf_from(not_after):
            pid = self._key_parent(key)
            if pid < parent:
                continue
            if pid > parent:
                break
            yield key, value

    def _find_raw(self, parent: int, raw_name: str) -> Optional[HfsEntry]:
        for child in self._children(parent, include_private=True):
            if child.raw_name == raw_name:
                return child
        return None

    def _follow_file_link(self, entry: HfsEntry, inode: int) -> None:
        folder = self._private_dir(FILE_LINK_PRIVATE)
        target = self._find_raw(folder, f"iNode{inode}") if folder else None
        if target is None:
            return
        entry.hardlink = True
        entry.data, entry.rsrc = target.data, target.rsrc
        entry.size, entry.compressed = target.size, target.compressed
        entry.mode = target.mode or entry.mode
        entry.cnid = target.cnid                   # veri ve oznitelikler burada

    def _follow_dir_link(self, entry: HfsEntry, inode: int) -> None:
        folder = self._private_dir(DIR_LINK_PRIVATE)
        target = self._find_raw(folder, f"dir_{inode}") if folder else None
        if target is None:
            return
        entry.hardlink = True
        entry.is_dir = True
        entry.cnid = target.cnid
        entry.data = entry.rsrc = None
        entry.size = 0

    def _children(self, parent: int, include_private: bool = False) -> List[HfsEntry]:
        cached = self._dir_cache.get(parent)
        if cached is None:
            cached = []
            for key, value in self._raw_children(parent):
                entry = self._entry(key, value)
                if entry is not None:
                    cached.append(entry)
            if len(self._dir_cache) > 256:
                self._dir_cache.clear()
            self._dir_cache[parent] = cached
        if include_private or parent != ROOT_FOLDER_ID:
            return cached
        return [e for e in cached if e.raw_name not in HIDDEN_METADATA]

    def _volume_name(self) -> str:
        def not_after(key: bytes) -> bool:
            return self._key_parent(key) <= ROOT_PARENT_ID
        for key, value in self.catalog.leaf_from(not_after):
            if self._key_parent(key) == ROOT_PARENT_ID:
                name, _ = _decode_name(key, 6)
                return hfs_display(name)
            if self._key_parent(key) > ROOT_PARENT_ID:
                break
        return ""

    # ---- yol --------------------------------------------------------------
    def _fold(self, name: str):
        """Katalogun esitlik kurali: HFS+ Apple katlamasi, HFSX birebir."""
        # eski (Unicode 2.1 / Linux) tonos'lu ad da ayni anahtara katlanir
        name = hfs_nfd(legacy_tonos(name.replace(":", "/")))
        return name if self.case_sensitive else fold_key(name)

    def root(self) -> HfsEntry:
        return HfsEntry(name="", cnid=ROOT_FOLDER_ID, parent=ROOT_PARENT_ID,
                        is_dir=True)

    def resolve(self, path: str) -> HfsEntry:
        node = self.root()
        for part in [p for p in path.replace("\\", "/").split("/") if p]:
            if not node.is_dir:
                raise HfsError(tr("Dizin degil: {}", path))
            want = self._fold(part)
            match = None
            for child in self._children(node.cnid):
                if self._fold(child.raw_name.replace("/", ":")) == want:
                    match = child
                    break
            if match is None:
                raise HfsError(tr("Bulunamadi: {}", path))
            node = match
        return node

    def listdir(self, path: str = "/") -> List[HfsEntry]:
        node = self.resolve(path)
        if not node.is_dir:
            raise HfsError(tr("Dizin degil: {}", path))
        return list(self._children(node.cnid))

    # ---- sikistirma (decmpfs) ----------------------------------------------
    def _xattr(self, file_id: int, name: str) -> Optional[bytes]:
        tree = self.attributes_tree
        if tree is None or not tree.root:
            return None

        def not_after(key: bytes) -> bool:
            return struct.unpack_from(">I", key, 4)[0] < file_id

        for key, value in tree.leaf_from(not_after):
            fid = struct.unpack_from(">I", key, 4)[0]
            if fid < file_id:
                continue
            if fid > file_id:
                break
            attr_name, _ = _decode_name(key, 12)
            if attr_name != name:
                continue
            kind = struct.unpack_from(">I", value, 0)[0]
            if kind == 0x10:                           # satir ici
                size = struct.unpack_from(">I", value, 12)[0]
                return bytes(value[16:16 + size])
            if kind == 0x20:                           # catal
                return self.read_fork(Fork.parse(value, 8), 0, -1, file_id)
            return None
        return None

    def _decmpfs_header(self, entry: HfsEntry) -> Tuple[int, int, bytes]:
        raw = self._xattr(entry.cnid, DECMPFS_ATTR)
        if not raw or len(raw) < 16 or raw[:4] != DECMPFS_MAGIC:
            raise HfsError(tr("Sikistirma ozniteligi bozuk: {}", entry.name))
        kind = struct.unpack_from("<I", raw, 4)[0]
        size = struct.unpack_from("<Q", raw, 8)[0]
        return kind, size, raw[16:]

    def _decmpfs_size(self, entry: HfsEntry) -> int:
        try:
            return self._decmpfs_header(entry)[1]
        except HfsError:
            return entry.size

    @staticmethod
    def _chunk(data: bytes) -> bytes:
        if data[:1] == b"\xff":                        # sikistirilmamis parca
            return data[1:]
        return zlib.decompress(data)

    def _decompress(self, entry: HfsEntry) -> bytes:
        kind, size, payload = self._decmpfs_header(entry)
        if kind == 1:
            return payload[:size]
        if kind == 3:
            return self._chunk(payload)[:size] if size else b""
        if kind == 4:
            rsrc = self.read_fork(entry.rsrc, 0, -1, entry.cnid, FORK_RSRC)
            data_off = struct.unpack_from(">I", rsrc, 0)[0]
            base = data_off + 4                       # kaynak uzunlugu atlanir
            blocks = struct.unpack_from("<I", rsrc, base)[0]
            out = bytearray()
            for i in range(blocks):
                off, length = struct.unpack_from("<II", rsrc, base + 4 + i * 8)
                out += self._chunk(rsrc[base + off:base + off + length])
            return bytes(out[:size])
        raise HfsError(tr("Desteklenmeyen HFS+ sikistirmasi (tur {}): {} — "
                          "LZVN/LZFSE bu surumde acilamiyor.", kind, entry.name))

    # ---- okuma ------------------------------------------------------------
    def read_entry(self, entry: HfsEntry, max_bytes: int = -1) -> bytes:
        if entry.is_dir:
            raise HfsError(tr("Dizin okunamaz: {}", entry.name))
        if entry.compressed:
            data = self._decompress(entry)
            return data if max_bytes < 0 else data[:max_bytes]
        if entry.data is None:
            return b""
        return self.read_fork(entry.data, 0, max_bytes, entry.cnid)

    def iter_file(self, path: str, chunk: int = 4 * 1024 * 1024):
        """Dosyanin veri catalini parca parca verir (ADR 0081)."""
        entry = self.resolve(path)
        if entry.is_dir:
            raise HfsError(tr("Dizin okunamaz: {}", path))
        if entry.compressed or entry.data is None:
            yield self.read_entry(entry)
            return
        pos = 0
        while pos < entry.data.size:
            piece = self.read_fork(entry.data, pos, chunk, entry.cnid)
            if not piece:
                break
            yield piece
            pos += len(piece)

    def read_file(self, path: str, max_bytes: int = -1) -> bytes:
        return self.read_entry(self.resolve(path), max_bytes)

    def extract(self, path: str, dest: str) -> str:
        entry = self.resolve(path)
        if entry.is_dir:
            os.makedirs(dest, exist_ok=True)
            for child in self._children(entry.cnid):
                self.extract(path.rstrip("/") + "/" + child.name,
                             os.path.join(dest, child.name))
            return dest
        os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
        chunk = 8 * 1024 * 1024
        with open(dest, "wb") as fh:
            if entry.compressed or entry.data is None:
                fh.write(self.read_entry(entry))
            else:
                pos = 0
                while pos < entry.data.size:
                    piece = self.read_fork(entry.data, pos, chunk, entry.cnid)
                    fh.write(piece)
                    pos += len(piece)
        if entry.mtime:
            ts = entry.mtime.timestamp()
            os.utime(dest, (ts, ts))
        return dest

    def stats(self) -> Dict[str, int]:
        total = self.total_blocks * self.block_size
        free = self.free_blocks * self.block_size
        return {"total_bytes": total, "used_bytes": total - free,
                "free_bytes": free, "cluster_size": self.block_size}
