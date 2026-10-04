"""HFS+ / HFSX yazma — saf Python (ADR 0062).

Okuyucu (`hfsplus.HfsPlusFS`) uzerine kurulur; dugum yazimlari aninda
diske ve okuyucunun onbellegine gider, birim basligi `flush()`ta yazilir.

B-agaci (katalog, kapsam tasmasi, oznitelik):
  * ekleme: yapraga sirali; dugum tasarsa ikiye bolunur, sag dugumun ilk
    anahtari ust dugume eklenir; kok bolunurse yeni kok (derinlik + 1).
    Ust dugumdeki anahtar her zaman cocugun ilk anahtaridir.
  * silme: yaprak bosalirsa dugum serbest birakilir, kardes baglari ve ust
    kayit duzeltilir; tek cocuklu kok coker (derinlik - 1). Az dolu dugumler
    birlestirilmez (HFS+ bunu gerektirmez).
  * bos dugum yoksa agac dosyasi kendi kume boyu kadar buyur (bitisik
    tercih edilir; en fazla 8 kapsam).

Katalog kurallari: her dosya/klasorun (kimlik, "") anahtarli bir iplik
kaydi vardir; klasorun "valence"i dogrudan cocuk sayisidir; birim
basligindaki dosya/klasor sayilari (kok haric) ve sonraki kimlik tutulur.

Yazma reddedilir: kirli gunluk, temiz kapatilmamis birim ("unmounted"
biti yok), yazilim kilidi. Sabit bag ve sikistirilmis dosya silinmez /
degistirilmez (acik hata).
"""
from __future__ import annotations

import io
import re
import struct
import time
from typing import Callable, Dict, List, Optional, Tuple

from .hfsformat import (catalog_key, folder_record, hfs_time, thread_record,
                        build_node)
from .hfsplus import (Fork, HfsEntry, HfsError, HfsPlusFS, REC_FILE,
                      REC_FILE_THREAD, REC_FOLDER, REC_FOLDER_THREAD,
                      ROOT_FOLDER_ID, VOL_JOURNALED, _BTree)
from .hfsunicode import compare_units, hfs_nfd
from .streamio import write_runs
from ..i18n import tr

VOL_UNMOUNTED = 1 << 8
VOL_SOFTWARE_LOCK = 1 << 15
VOL_HARDWARE_LOCK = 1 << 7
FILE_THREAD_EXISTS = 0x0002
UNKNOWN_ID = 99                                  # macOS "unknown" kullanici
MODE_FILE = 0o100644
MODE_DIR = 0o040755
HEADER_FORKS = {"alloc": 112, "extents": 192, "catalog": 272, "attributes": 352}


class HfsWriteError(HfsError):
    pass


# ----------------------------------------------------------------------
# Anahtar karsilastirmalari
# ----------------------------------------------------------------------
def _name_units(key: bytes, off: int) -> List[int]:
    n = struct.unpack_from(">H", key, off)[0]
    return list(struct.unpack_from(f">{n}H", key, off + 2))


def catalog_compare(case_sensitive: bool) -> Callable[[bytes, bytes], int]:
    def cmp(a: bytes, b: bytes) -> int:
        pa, pb = struct.unpack_from(">I", a, 2)[0], struct.unpack_from(">I", b, 2)[0]
        if pa != pb:
            return -1 if pa < pb else 1
        return compare_units(_name_units(a, 6), _name_units(b, 6), case_sensitive)
    return cmp


def extents_compare(a: bytes, b: bytes) -> int:
    ka = (struct.unpack_from(">I", a, 4)[0], a[2], struct.unpack_from(">I", a, 8)[0])
    kb = (struct.unpack_from(">I", b, 4)[0], b[2], struct.unpack_from(">I", b, 8)[0])
    return (ka > kb) - (ka < kb)


def attributes_compare(a: bytes, b: bytes) -> int:
    fa, fb = struct.unpack_from(">I", a, 4)[0], struct.unpack_from(">I", b, 4)[0]
    if fa != fb:
        return -1 if fa < fb else 1
    na, nb = _name_units(a, 12), _name_units(b, 12)
    if na != nb:
        return -1 if na < nb else 1
    sa, sb = struct.unpack_from(">I", a, 8)[0], struct.unpack_from(">I", b, 8)[0]
    return (sa > sb) - (sa < sb)


def extent_key(file_id: int, fork_type: int, start_block: int) -> bytes:
    return struct.pack(">HBBII", 10, fork_type, 0, file_id, start_block)


# ----------------------------------------------------------------------
# B-agaci yazicisi
# ----------------------------------------------------------------------
class _TreeWriter:
    def __init__(self, w: "HfsWriter", tree: _BTree, fork_name: str,
                 compare: Callable[[bytes, bytes], int]):
        self.w = w
        self.tree = tree
        self.fork_name = fork_name
        self.compare = compare
        head = tree.node(0)
        (self.depth, self.root, self.leaf_records, self.first_leaf,
         self.last_leaf, self.node_size, self.max_key, self.total,
         self.free) = struct.unpack_from(">HIIIIHHII", head, 14)
        self.clump = struct.unpack_from(">I", head, 14 + 32)[0]
        self.variable = tree.variable_index_keys

    # -- dugum G/C --------------------------------------------------------
    def read(self, n: int) -> bytearray:
        return bytearray(self.tree.node(n))

    def write(self, n: int, data: bytes) -> None:
        if len(data) != self.node_size:
            raise HfsWriteError(tr("B-agaci dugumu tasti"))
        self.w.write_fork(self.tree.fork, self.tree.file_id, n * self.node_size, data)
        self.tree._cache[n] = bytes(data)

    def save_header(self) -> None:
        head = self.read(0)
        struct.pack_into(">HIIIIHHII", head, 14, self.depth, self.root,
                         self.leaf_records, self.first_leaf, self.last_leaf,
                         self.node_size, self.max_key, self.total, self.free)
        self.write(0, head)
        # okuyucunun agaci ayni degerlerle gezmeye devam etsin
        t = self.tree
        t.depth, t.root, t.first_leaf = self.depth, self.root, self.first_leaf

    def records(self, data: bytes) -> List[bytes]:
        return self.tree.records(data)

    def key_of(self, rec: bytes, index: bool = False) -> bytes:
        klen = struct.unpack_from(">H", rec, 0)[0]
        return bytes(rec[:2 + klen])

    def child_of(self, rec: bytes) -> int:
        klen = struct.unpack_from(">H", rec, 0)[0]
        if not self.variable:
            klen = self.max_key
        return struct.unpack_from(">I", rec, 2 + klen)[0]

    def index_record(self, key: bytes, child: int) -> bytes:
        if not self.variable:
            body = key[2:].ljust(self.max_key, b"\x00")
            key = struct.pack(">H", self.max_key) + body
        return key + struct.pack(">I", child)

    @staticmethod
    def links(data: bytes) -> Tuple[int, int, int, int]:
        flink, blink, kind, height = struct.unpack_from(">IIbB", data, 0)
        return flink, blink, kind, height

    def _fits(self, recs: List[bytes]) -> bool:
        return 14 + sum(len(r) for r in recs) + 2 * (len(recs) + 1) <= self.node_size

    # -- dugum haritasi ------------------------------------------------------
    def _map_segments(self) -> List[Tuple[int, int, int]]:
        """[(dugum, kayit ofseti, uzunluk)] — baslik ve harita dugumleri."""
        out = []
        head = self.read(0)
        offs = [struct.unpack_from(">H", head, self.node_size - 2 * (i + 1))[0]
                for i in range(4)]
        out.append((0, offs[2], offs[3] - offs[2]))
        n = struct.unpack_from(">I", head, 0)[0]
        while n:
            data = self.read(n)
            start = struct.unpack_from(">H", data, self.node_size - 2)[0]
            end = struct.unpack_from(">H", data, self.node_size - 4)[0]
            out.append((n, start, end - start))
            n = struct.unpack_from(">I", data, 0)[0]
        return out

    def _set_bit(self, number: int, value: bool) -> None:
        base = 0
        for node, off, length in self._map_segments():
            if number < base + length * 8:
                data = self.read(node)
                bit = number - base
                if value:
                    data[off + bit // 8] |= 0x80 >> (bit % 8)
                else:
                    data[off + bit // 8] &= ~(0x80 >> (bit % 8)) & 0xFF
                self.write(node, data)
                return
            base += length * 8
        raise HfsWriteError(tr("B-agaci dugum haritasi yetersiz"))

    def alloc_node(self) -> int:
        for _attempt in range(2):
            base = 0
            for node, off, length in self._map_segments():
                data = self.read(node)
                for i in range(length):
                    byte = data[off + i]
                    if byte == 0xFF:
                        continue
                    for bit in range(8):
                        number = base + i * 8 + bit
                        if number >= self.total:
                            break
                        if not byte & (0x80 >> bit):
                            data[off + i] |= 0x80 >> bit
                            self.write(node, data)
                            self.free -= 1
                            return number
                base += length * 8
            self._grow()
        raise HfsWriteError(tr("B-agaci icin bos dugum bulunamadi"))

    def free_node(self, n: int) -> None:
        self._set_bit(n, False)
        self.write(n, bytes(self.node_size))
        self.free += 1

    def _grow(self) -> None:
        bs = self.w.bs
        capacity = sum(length * 8 for _n, _o, length in self._map_segments())
        blocks = max(self.clump, self.node_size) // bs
        add_nodes = blocks * bs // self.node_size
        if self.total + add_nodes > capacity:
            raise HfsWriteError(tr("B-agaci dugum haritasi yetersiz"))
        fork = self.tree.fork
        file_id = self.tree.file_id
        current = list(self.w.fs.fork_runs(fork, file_id))
        goal = current[-1][0] + current[-1][1] if current else 0
        runs = self.w.alloc_blocks(blocks, goal, contiguous_only=False)
        extents = list(current)
        for start, count in runs:
            if extents and extents[-1][0] + extents[-1][1] == start:
                extents[-1] = (extents[-1][0], extents[-1][1] + count)
            else:
                extents.append((start, count))
        if len(extents) > 8 and file_id == 3:
            # kapsam tasmasi agacinin kendisi tasamaz (TN1150)
            self.w.free_runs(runs)
            raise HfsWriteError(tr("Kapsam tasmasi dosyasi 8 parcayi asti; birim cok parcali"))
        for start, count in runs:
            self.w.zero_blocks(start, count)
        if len(extents) > 8:
            self.w.set_overflow(file_id, 0, extents)
        fork.extents = extents[:8]
        fork.blocks += blocks
        fork.size += blocks * bs
        self.w.set_header_fork(self.fork_name, fork)
        self.total += add_nodes
        self.free += add_nodes
        self.save_header()

    # -- arama ------------------------------------------------------------
    def _descend(self, key: bytes) -> Tuple[List[Tuple[int, int]], int]:
        path: List[Tuple[int, int]] = []
        n = self.root
        while True:
            data = self.read(n)
            if self.links(data)[2] != 0:              # yaprak
                return path, n
            recs = self.records(data)
            idx = 0
            for i, rec in enumerate(recs):
                if i and self.compare(self.key_of(rec), key) > 0:
                    break
                idx = i
            path.append((n, idx))
            n = self.child_of(recs[idx])
            if len(path) > 32:
                raise HfsWriteError(tr("B-agaci cok derin (bozuk?)"))

    def get(self, key: bytes) -> Optional[bytes]:
        if not self.root:
            return None
        _path, leaf = self._descend(key)
        for rec in self.records(self.read(leaf)):
            k = self.key_of(rec)
            if self.compare(k, key) == 0:
                return bytes(rec[len(k):])
        return None

    # -- ekleme -------------------------------------------------------------
    def insert(self, key: bytes, value: bytes) -> None:
        rec = key + value
        if not self.root:
            n = self.alloc_node()
            self.write(n, build_node(self.node_size, -1, 1, [rec]))
            self.root = self.first_leaf = self.last_leaf = n
            self.depth = 1
            self.leaf_records = 1
            self.save_header()
            return
        path, leaf = self._descend(key)
        recs = self.records(self.read(leaf))
        pos = len(recs)
        for i, r in enumerate(recs):
            c = self.compare(self.key_of(r), key)
            if c == 0:
                raise HfsWriteError(tr("Kayit zaten var"))
            if c > 0:
                pos = i
                break
        recs.insert(pos, rec)
        self._store(leaf, recs, path)
        self.leaf_records += 1
        self.save_header()

    def _store(self, n: int, recs: List[bytes], path: List[Tuple[int, int]]) -> None:
        data = self.read(n)
        flink, blink, kind, height = self.links(data)
        old_first = self.key_of(self.records(data)[0]) if struct.unpack_from(
            ">H", data, 10)[0] else None
        if self._fits(recs):
            self.write(n, build_node(self.node_size, kind, height, recs, flink, blink))
            new_first = self.key_of(recs[0])
            if path and new_first != old_first:
                pn, pidx = path[-1]
                precs = self.records(self.read(pn))
                precs[pidx] = self.index_record(new_first, n)
                self._store(pn, precs, path[:-1])
            return
        # bolme: bayt olarak yariya en yakin nokta
        half = sum(len(r) for r in recs) // 2
        acc, cut = 0, len(recs) - 1
        for i, r in enumerate(recs):
            acc += len(r)
            if acc >= half:
                cut = i + 1
                break
        cut = min(max(cut, 1), len(recs) - 1)
        left, right = recs[:cut], recs[cut:]
        if not self._fits(left) or not self._fits(right):
            raise HfsWriteError(tr("B-agaci kaydi dugume sigmiyor"))
        m = self.alloc_node()
        self.write(m, build_node(self.node_size, kind, height, right, flink, n))
        if flink:
            fdata = self.read(flink)
            struct.pack_into(">I", fdata, 4, m)
            self.write(flink, fdata)
        elif kind == -1:
            self.last_leaf = m
        self.write(n, build_node(self.node_size, kind, height, left, m, blink))
        left_key, right_key = self.key_of(left[0]), self.key_of(right[0])
        if not path:
            r = self.alloc_node()
            self.write(r, build_node(self.node_size, 0, height + 1, [
                self.index_record(left_key, n), self.index_record(right_key, m)]))
            self.root = r
            self.depth += 1
            return
        pn, pidx = path[-1]
        precs = self.records(self.read(pn))
        precs[pidx] = self.index_record(left_key, n)
        precs.insert(pidx + 1, self.index_record(right_key, m))
        self._store(pn, precs, path[:-1])

    # -- silme ---------------------------------------------------------------
    def delete(self, key: bytes) -> None:
        if not self.root:
            raise HfsWriteError(tr("Kayit bulunamadi"))
        path, leaf = self._descend(key)
        recs = self.records(self.read(leaf))
        for i, r in enumerate(recs):
            if self.compare(self.key_of(r), key) == 0:
                del recs[i]
                break
        else:
            raise HfsWriteError(tr("Kayit bulunamadi"))
        if recs:
            self._store(leaf, recs, path)
        else:
            self._remove_node(leaf, path)
        self.leaf_records -= 1
        self.save_header()

    def replace(self, key: bytes, value: bytes) -> None:
        path, leaf = self._descend(key)
        recs = self.records(self.read(leaf))
        for i, r in enumerate(recs):
            k = self.key_of(r)
            if self.compare(k, key) == 0:
                recs[i] = k + value
                self._store(leaf, recs, path)
                return
        raise HfsWriteError(tr("Kayit bulunamadi"))

    def _remove_node(self, n: int, path: List[Tuple[int, int]]) -> None:
        data = self.read(n)
        flink, blink, kind, _height = self.links(data)
        if blink:
            bdata = self.read(blink)
            struct.pack_into(">I", bdata, 0, flink)
            self.write(blink, bdata)
        elif kind == -1:
            self.first_leaf = flink
        if flink:
            fdata = self.read(flink)
            struct.pack_into(">I", fdata, 4, blink)
            self.write(flink, fdata)
        elif kind == -1:
            self.last_leaf = blink
        self.free_node(n)
        if not path:
            self.root = 0
            self.depth = 0
            self.first_leaf = self.last_leaf = 0
            return
        pn, pidx = path[-1]
        precs = self.records(self.read(pn))
        del precs[pidx]
        if not precs:
            self._remove_node(pn, path[:-1])
            return
        if len(path) == 1 and len(precs) == 1:
            # tek cocuklu kok coker
            self.root = self.child_of(precs[0])
            self.depth -= 1
            self.free_node(pn)
            return
        self._store(pn, precs, path[:-1])

    def scan(self, match: Callable[[bytes], bool]) -> List[bytes]:
        """Kosulu saglayan tum yaprak anahtarlari (silme oncesi toplama)."""
        out = []
        n = self.first_leaf
        while n:
            data = self.read(n)
            for rec in self.records(data):
                k = self.key_of(rec)
                if match(k):
                    out.append(k)
            n = struct.unpack_from(">I", data, 0)[0]
        return out


# ----------------------------------------------------------------------
# Birim yazicisi
# ----------------------------------------------------------------------
class HfsWriter:
    def __init__(self, fs: HfsPlusFS):
        self.fs = fs
        self.dev = fs.dev
        self.bs = fs.block_size
        self.header = bytearray(fs.header)
        self._bitmap: Optional[bytearray] = None
        self._dirty: Tuple[int, int] = (-1, -1)
        self._began = False
        self._trees: Dict[str, _TreeWriter] = {}

    # -- izin -------------------------------------------------------------
    def write_support(self) -> Tuple[bool, str]:
        attrs = self.fs.attributes
        if getattr(self.dev, "readonly", False):
            return False, tr("Goruntu salt okunur acildi")
        if attrs & (VOL_SOFTWARE_LOCK | VOL_HARDWARE_LOCK):
            return False, tr("HFS+ birimi kilitli (yazmaya kapali)")
        if self.fs.journal_dirty:
            return False, tr("HFS+ gunlugu temiz kapatilmamis; birim macOS'ta "
                             "baglanip duzgun cikarilmadan yazilamaz.")
        if not attrs & VOL_UNMOUNTED:
            return False, tr("HFS+ birimi temiz kapatilmamis; once macOS'ta "
                             "Disk Izlencesi ile onarilmali.")
        if self.fs.catalog.fork.blocks == 0:
            return False, tr("HFS+ katalogu bulunamadi")
        return True, ""

    def _require(self) -> None:
        ok, reason = self.write_support()
        if not ok:
            raise HfsWriteError(reason)
        if not self._began:
            # Degisiklik suresince "temiz kapatildi" biti kalkar; yarida
            # kalan islem fsck'a zorlanir (macOS baglarken ayni seyi yapar).
            self._began = True
            struct.pack_into(">I", self.header, 4,
                             struct.unpack_from(">I", self.header, 4)[0] & ~VOL_UNMOUNTED)
            self.dev.write(1024, bytes(self.header))

    # -- agaclar ------------------------------------------------------------
    @property
    def catalog(self) -> _TreeWriter:
        if "catalog" not in self._trees:
            self._trees["catalog"] = _TreeWriter(
                self, self.fs.catalog, "catalog", catalog_compare(self.fs.case_sensitive))
        return self._trees["catalog"]

    @property
    def extents(self) -> _TreeWriter:
        if "extents" not in self._trees:
            self._trees["extents"] = _TreeWriter(self, self.fs.extents_tree,
                                                 "extents", extents_compare)
        return self._trees["extents"]

    @property
    def attributes(self) -> Optional[_TreeWriter]:
        if self.fs.attributes_tree is None:
            return None
        if "attributes" not in self._trees:
            self._trees["attributes"] = _TreeWriter(
                self, self.fs.attributes_tree, "attributes", attributes_compare)
        return self._trees["attributes"]

    def set_header_fork(self, name: str, fork: Fork) -> None:
        off = HEADER_FORKS[name]
        body = struct.pack(">QII", fork.size, struct.unpack_from(">I", self.header, off + 8)[0],
                           fork.blocks)
        ext = b"".join(struct.pack(">II", s, c) for s, c in fork.extents[:8])
        self.header[off:off + 80] = (body + ext).ljust(80, b"\x00")

    # -- catal G/C ----------------------------------------------------------
    def write_fork(self, fork: Fork, file_id: int, offset: int, data: bytes,
                   fork_type: int = 0) -> None:
        bs = self.bs
        pos = 0
        view = memoryview(data)
        for start, count in self.fs.fork_runs(fork, file_id, fork_type):
            run_bytes = count * bs
            if pos + run_bytes <= offset:
                pos += run_bytes
                continue
            skip = max(0, offset - pos)
            take = min(run_bytes - skip, len(view))
            self.dev.write(start * bs + skip, bytes(view[:take]))
            view = view[take:]
            pos += run_bytes
            offset = pos
            if not len(view):
                return
        if len(view):
            raise HfsWriteError(tr("Catal kapsamlari eksik (dosya kimligi {})", file_id))

    def set_overflow(self, file_id: int, fork_type: int,
                     extents: List[Tuple[int, int]]) -> None:
        """Ilk 8'den sonraki kapsamlari tasma agacina yazar (eskileri silinir)."""
        tree = self.extents
        for key in tree.scan(lambda k: struct.unpack_from(">I", k, 4)[0] == file_id
                             and k[2] == fork_type):
            tree.delete(key)
        done = sum(c for _s, c in extents[:8])
        for i in range(8, len(extents), 8):
            group = extents[i:i + 8]
            tree.insert(extent_key(file_id, fork_type, done), b"".join(
                struct.pack(">II", s0, c) for s0, c in group).ljust(64, b"\x00"))
            done += sum(c for _s, c in group)
        self.fs._overflow = None

    def zero_blocks(self, start: int, count: int) -> None:
        chunk = bytes(min(count * self.bs, 4 * 1024 * 1024))
        pos, end = start * self.bs, (start + count) * self.bs
        while pos < end:
            n = min(len(chunk), end - pos)
            self.dev.write(pos, chunk[:n])
            pos += n

    # -- ayirma bitmap'i ---------------------------------------------------
    def _load_bitmap(self) -> bytearray:
        if self._bitmap is None:
            fork = Fork.parse(bytes(self.header), HEADER_FORKS["alloc"])
            need = (self.fs.total_blocks + 7) // 8
            self._bitmap = bytearray(self.fs.read_fork(fork, 0, need, 6))
        return self._bitmap

    def _mark(self, start: int, count: int, used: bool) -> None:
        bm = self._load_bitmap()
        for b in range(start, start + count):
            if used:
                bm[b >> 3] |= 0x80 >> (b & 7)
            else:
                bm[b >> 3] &= ~(0x80 >> (b & 7)) & 0xFF
        lo = start >> 3
        hi = (start + count - 1) >> 3
        d0, d1 = self._dirty
        self._dirty = (lo if d0 < 0 else min(d0, lo), max(d1, hi))
        free = struct.unpack_from(">I", self.header, 48)[0]
        free += -count if used else count
        struct.pack_into(">I", self.header, 48, free)

    def _is_free(self, block: int) -> bool:
        return not self._load_bitmap()[block >> 3] & (0x80 >> (block & 7))

    def _find_contiguous(self, count: int, goal: int) -> int:
        """`count` bitisik bos blogun basi; yoksa -1.

        Once tam `goal` denenir (ardisik yazmalarda tipik durum), sonra en az
        ceil(count/8) sifir bayt aranir (bayt duzeyinde, hizli); bulunan
        kosu onceki baytin bos bitlerine dogru genisletilir.
        """
        bm = self._load_bitmap()
        total = self.fs.total_blocks
        if 0 <= goal and goal + count <= total and all(
                self._is_free(b) for b in range(goal, goal + count)):
            return goal
        need = max(1, (count + 7) // 8)
        pattern = re.compile(b"\x00{%d}" % need)
        for first in (goal >> 3, 0):
            m = pattern.search(bm, max(0, first))
            if not m:
                continue
            start = m.start() * 8
            back = 0
            while back < 7 and start > 0 and self._is_free(start - 1):
                start -= 1
                back += 1
            if start + count <= total:
                return start
        return -1

    def _scattered_runs(self) -> List[Tuple[int, int]]:
        """Tum bos kosular (yalnizca bitisik yer bulunamazsa; yavas)."""
        bm = self._load_bitmap()
        total = self.fs.total_blocks
        runs: List[Tuple[int, int]] = []
        for m in re.finditer(rb"[^\xff]+", bytes(bm)):
            b0, b1 = m.start() * 8, min(m.end() * 8, total)
            run_start = -1
            for b in range(b0, b1):
                free = self._is_free(b)
                if free and run_start < 0:
                    run_start = b
                elif not free and run_start >= 0:
                    runs.append((run_start, b - run_start))
                    run_start = -1
            if run_start >= 0:
                runs.append((run_start, b1 - run_start))
        return runs

    def alloc_blocks(self, count: int, goal: int = 0,
                     contiguous_only: bool = False) -> List[Tuple[int, int]]:
        if count <= 0:
            return []
        start = self._find_contiguous(count, goal)
        if start >= 0:
            self._mark(start, count, True)
            return [(start, count)]
        runs = [] if contiguous_only else self._scattered_runs()
        if sum(c for _s, c in runs) < count:
            raise HfsWriteError(tr("HFS+ biriminde yeterli bos alan yok"))
        out: List[Tuple[int, int]] = []
        left = count
        for s0, c in sorted(runs, key=lambda r: -r[1]):
            take = min(c, left)
            self._mark(s0, take, True)
            out.append((s0, take))
            left -= take
            if not left:
                break
        out.sort()
        return out

    def free_runs(self, runs: List[Tuple[int, int]]) -> None:
        for s, c in runs:
            if c:
                self._mark(s, c, False)

    # -- katalog yardimcilari -----------------------------------------------
    def _invalidate(self) -> None:
        self.fs._dir_cache.clear()
        self.fs._private.clear()
        self.fs._overflow = None

    @staticmethod
    def _disk_name(name: str) -> str:
        if not name or name in (".", "..") or "\x00" in name:
            raise HfsWriteError(tr("Gecersiz ad: {}", name))
        disk = hfs_nfd(name.replace(":", "/"))
        if len(disk.encode("utf-16-be")) // 2 > 255:
            raise HfsWriteError(tr("HFS+ adi en fazla 255 karakter olabilir"))
        return disk

    def _split(self, path: str) -> Tuple[HfsEntry, str]:
        parts = [p for p in path.replace("\\", "/").split("/") if p]
        if not parts:
            raise HfsWriteError(tr("Gecersiz dosya yolu"))
        parent = self.fs.resolve("/" + "/".join(parts[:-1]))
        if not parent.is_dir:
            raise HfsWriteError(tr("Dizin degil: {}", path))
        return parent, parts[-1]

    def _exists(self, parent: HfsEntry, name: str) -> Optional[HfsEntry]:
        want = self.fs._fold(name)
        for child in self.fs._children(parent.cnid, include_private=True):
            if self.fs._fold(child.raw_name.replace("/", ":")) == want:
                return child
        return None

    def _next_cnid(self) -> int:
        cnid = struct.unpack_from(">I", self.header, 64)[0]
        if cnid >= 0xFFFFFFF0:
            raise HfsWriteError(tr("HFS+ katalog kimlikleri tukendi"))
        struct.pack_into(">I", self.header, 64, cnid + 1)
        return cnid

    def _count(self, offset: int, delta: int) -> None:
        value = struct.unpack_from(">I", self.header, offset)[0]
        struct.pack_into(">I", self.header, offset, max(0, value + delta))

    def _adjust_parent(self, parent_cnid: int, delta: int) -> None:
        """Klasorun valence'ini ve icerik tarihini gunceller."""
        thread = self.catalog.get(catalog_key(parent_cnid, ""))
        if thread is None:
            raise HfsWriteError(tr("Klasor iplik kaydi yok: {}", parent_cnid))
        grand = struct.unpack_from(">I", thread, 4)[0]
        name_len = struct.unpack_from(">H", thread, 8)[0]
        name = thread[10:10 + 2 * name_len].decode("utf-16-be")
        key = self._raw_key(grand, name)
        rec = self.catalog.get(key)
        if rec is None:
            raise HfsWriteError(tr("Klasor kaydi yok: {}", parent_cnid))
        rec = bytearray(rec)
        valence = struct.unpack_from(">I", rec, 4)[0]
        struct.pack_into(">I", rec, 4, max(0, valence + delta))
        now = hfs_time()
        struct.pack_into(">I", rec, 16, now)                  # icerik degisti
        struct.pack_into(">I", rec, 20, now)
        self.catalog.replace(key, bytes(rec))

    @staticmethod
    def _raw_key(parent: int, disk_name: str) -> bytes:
        """Diskteki (zaten ayristirilmis) adla anahtar."""
        raw = disk_name.encode("utf-16-be")
        body = struct.pack(">IH", parent, len(raw) // 2) + raw
        return struct.pack(">H", len(body)) + body

    def _thread(self, kind: int, parent: int, disk_name: str) -> bytes:
        raw = disk_name.encode("utf-16-be")
        return struct.pack(">hHIH", kind, 0, parent, len(raw) // 2) + raw

    # -- islemler -----------------------------------------------------------
    def mkdir(self, path: str) -> None:
        self._require()
        parent, name = self._split(path)
        disk = self._disk_name(name)
        if self._exists(parent, name):
            raise HfsWriteError(tr("Zaten var: {}", path))
        cnid = self._next_cnid()
        now = hfs_time()
        rec = bytearray(folder_record(cnid, now))
        struct.pack_into(">IIBBH", rec, 32, UNKNOWN_ID, UNKNOWN_ID, 0, 0, MODE_DIR)
        self.catalog.insert(self._raw_key(parent.cnid, disk), bytes(rec))
        self.catalog.insert(self._raw_key(cnid, ""),
                            self._thread(REC_FOLDER_THREAD, parent.cnid, disk))
        self._adjust_parent(parent.cnid, +1)
        self._count(36, +1)
        self._invalidate()

    def write_file(self, path: str, data: bytes, overwrite: bool = True) -> None:
        self.write_stream(path, io.BytesIO(data), len(data), overwrite)

    def write_stream(self, path: str, src, size: int,
                     overwrite: bool = True) -> None:
        """Kaynaktan `size` bayti parca parca yazar (ADR 0081)."""
        self._require()
        parent, name = self._split(path)
        disk = self._disk_name(name)
        old = self._exists(parent, name)
        if old is not None:
            if old.is_dir or not overwrite:
                raise HfsWriteError(tr("Zaten var: {}", path))
            self.remove(path)
            parent = self.fs.resolve("/".join(path.replace("\\", "/").split("/")[:-1]) or "/")
        bs = self.bs
        blocks = (size + bs - 1) // bs
        goal = struct.unpack_from(">I", self.header, 52)[0]
        runs = self.alloc_blocks(blocks, goal)
        cnid = self._next_cnid()
        inserted: List[Tuple[_TreeWriter, bytes]] = []
        try:
            # Son blogun kalani sifirlanir (eskiden de oyleydi); parcanin geri
            # kalan bloklari ayrilmis ama veri tasimaz.
            write_runs(src, size, runs, bs,
                       lambda start, off, data: self.dev.write(start * bs + off,
                                                               data))
            fork = struct.pack(">QII", size, 0, blocks)
            fork += b"".join(struct.pack(">II", s, c) for s, c in runs[:8])
            fork = fork.ljust(80, b"\x00")
            done = sum(c for _s, c in runs[:8])
            for i in range(8, len(runs), 8):
                group = runs[i:i + 8]
                key = extent_key(cnid, 0, done)
                self.extents.insert(key, b"".join(
                    struct.pack(">II", s, c) for s, c in group).ljust(64, b"\x00"))
                inserted.append((self.extents, key))
                done += sum(c for _s, c in group)
            now = hfs_time()
            rec = bytearray(248)
            struct.pack_into(">hHII", rec, 0, REC_FILE, FILE_THREAD_EXISTS, 0, cnid)
            struct.pack_into(">IIII", rec, 12, now, now, now, now)
            struct.pack_into(">IIBBH", rec, 32, UNKNOWN_ID, UNKNOWN_ID, 0, 0, MODE_FILE)
            rec[88:168] = fork
            key = self._raw_key(parent.cnid, disk)
            self.catalog.insert(key, bytes(rec))
            inserted.append((self.catalog, key))
            tkey = self._raw_key(cnid, "")
            self.catalog.insert(tkey, self._thread(REC_FILE_THREAD, parent.cnid, disk))
            inserted.append((self.catalog, tkey))
            self._adjust_parent(parent.cnid, +1)
        except Exception:
            for tree, key in reversed(inserted):
                try:
                    tree.delete(key)
                except HfsError:
                    pass
            self.free_runs(runs)
            self._invalidate()
            raise
        if runs:
            struct.pack_into(">I", self.header, 52, runs[-1][0] + runs[-1][1])
        self._count(32, +1)
        self._invalidate()

    def remove(self, path: str, recursive: bool = False) -> None:
        self._require()
        parent, name = self._split(path)
        entry = self._exists(parent, name)
        if entry is None:
            raise HfsWriteError(tr("Bulunamadi: {}", path))
        if entry.hardlink:
            raise HfsWriteError(tr("Sabit bagli dosya bu surumde silinemiyor: {}", path))
        if entry.is_dir:
            children = self.fs._children(entry.cnid, include_private=True)
            if children and not recursive:
                raise HfsWriteError(tr("Klasor bos degil: {}", path))
            for child in list(children):
                self.remove(path.rstrip("/") + "/" + child.name, recursive=True)
        else:
            for fork, kind in ((entry.data, 0x00), (entry.rsrc, 0xFF)):
                if fork is not None and fork.blocks:
                    self.free_runs(self.fs.fork_runs(fork, entry.cnid, kind))
            cnid = entry.cnid
            for key in self.extents.scan(
                    lambda k: struct.unpack_from(">I", k, 4)[0] == cnid):
                self.extents.delete(key)
        attrs = self.attributes
        if attrs is not None and attrs.root:
            cnid = entry.cnid
            for key in attrs.scan(lambda k: struct.unpack_from(">I", k, 4)[0] == cnid):
                attrs.delete(key)
        self.catalog.delete(self._raw_key(parent.cnid, entry.raw_name))
        self.catalog.delete(self._raw_key(entry.cnid, ""))
        self._adjust_parent(parent.cnid, -1)
        self._count(36 if entry.is_dir else 32, -1)
        self._invalidate()

    def rename(self, path: str, new_name: str) -> None:
        """Ayni klasorde yeniden adlandirir (`new_name` bir ad ya da yol)."""
        self._require()
        parent, name = self._split(path)
        entry = self._exists(parent, name)
        if entry is None:
            raise HfsWriteError(tr("Bulunamadi: {}", path))
        if entry.hardlink:
            raise HfsWriteError(tr("Sabit bagli dosya bu surumde tasinamiyor: {}", path))
        if "/" in new_name:                      # yol: baska klasore tasima
            target_parent, target = self._split(new_name)
        else:
            target_parent, target = parent, new_name.strip("/")
        disk = self._disk_name(target)
        other = self._exists(target_parent, target)
        if other is not None and other.cnid != entry.cnid:
            raise HfsWriteError(tr("Zaten var: {}", target))
        if entry.is_dir:
            walk = target_parent.cnid
            while walk > ROOT_FOLDER_ID:
                if walk == entry.cnid:
                    raise HfsWriteError(tr("Klasor kendi icine tasinamaz"))
                thread = self.catalog.get(catalog_key(walk, ""))
                walk = struct.unpack_from(">I", thread, 4)[0] if thread else 0
        old_key = self._raw_key(parent.cnid, entry.raw_name)
        value = self.catalog.get(old_key)
        if value is None:
            raise HfsWriteError(tr("Bulunamadi: {}", path))
        self.catalog.delete(old_key)
        self.catalog.insert(self._raw_key(target_parent.cnid, disk), value)
        kind = REC_FOLDER_THREAD if entry.is_dir else REC_FILE_THREAD
        tkey = self._raw_key(entry.cnid, "")
        self.catalog.delete(tkey)
        self.catalog.insert(tkey, self._thread(kind, target_parent.cnid, disk))
        if target_parent.cnid != parent.cnid:
            self._adjust_parent(parent.cnid, -1)
            self._adjust_parent(target_parent.cnid, +1)
        else:
            self._adjust_parent(parent.cnid, 0)
        self._invalidate()

    # -- kapatma -------------------------------------------------------------
    def flush(self) -> None:
        if not self._began:
            return
        if self._bitmap is not None and self._dirty[0] >= 0:
            fork = Fork.parse(bytes(self.header), HEADER_FORKS["alloc"])
            bs = self.bs
            lo = (self._dirty[0] // bs) * bs
            hi = min(len(self._bitmap), (self._dirty[1] // bs + 1) * bs)
            chunk = bytes(self._bitmap[lo:hi])
            if len(chunk) % bs:
                chunk += bytes(bs - len(chunk) % bs)
            self.write_fork(fork, 6, lo, chunk)
            self._dirty = (-1, -1)
        attrs = struct.unpack_from(">I", self.header, 4)[0] | VOL_UNMOUNTED
        struct.pack_into(">I", self.header, 4, attrs)
        struct.pack_into(">I", self.header, 20, hfs_time())       # degistirme
        self._count(68, +1)                                       # writeCount
        self.dev.write(1024, bytes(self.header))
        self.dev.write(self.dev.size - 1024, bytes(self.header))
        f = getattr(self.dev, "flush", None)
        if f:
            f()
        self._began = False
        # okuyucu bas ilgisini tazele
        self.fs.header = bytes(self.header)
        self.fs.free_blocks = struct.unpack_from(">I", self.header, 48)[0]
        self.fs.file_count, self.fs.folder_count = struct.unpack_from(">II", self.header, 32)
