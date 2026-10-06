"""NTFS dizin indeksi ($I30) — B+ agaci ekleme, silme, bolme.

`ntfswrite` eskiden yalnizca indeksi `$INDEX_ROOT`a sigan dizinlere ve dolu
olmayan INDX bloklarina yazabiliyordu; kok dolunca ya da yaprak dolunca yazma
reddediliyordu. Ara dugumde duran bir girisi de silemiyordu: NTFS B+
agacinda ara dugumler de gercek dosya girisi tasir, arama o girisi atlayip
alta iniyordu.

Yapi (ntfs-3g `index.c` ile ayni kurallar):
  * Her dugum sirali girisler + bir END girisi tasir. Alt dugumu olan
    dugumde her giris (END dahil) sonunda 8 baytlik alt dugum VCN'i tasir;
    girisin alt dugumu, o girisin anahtarindan KUCUK anahtarlari tutar.
  * Kok `$INDEX_ROOT`tadir (MFT kaydinda); diger dugumler
    `$INDEX_ALLOCATION` icindeki INDX bloklaridir; hangi blogun kullanimda
    oldugunu `$BITMAP:$I30` tutar.
  * Bolme: dugum tasinca ortanca giris ust dugume cikar; soldaki girisler
    yeni bir INDX bloguna gider (ortancanin eski alt dugumu yeni blogun END
    girisinin alt dugumu olur), sagdakiler yerinde kalir.
  * Kok tasinca butun girisleri yeni bir INDX bloguna tasinir, kok tek bir
    END girisi (alt dugum = yeni blok) olur ("reparent"); gerekirse yeni
    blok bolunur.
  * Silme: yapraktaki giris dogrudan silinir; ara dugumdeki giris sol alt
    agacinin en buyuk girisiyle (onculu) yer degistirir. Bir yaprak
    bosalirsa indeks **bastan, dengeli** kurulur (nadir; guvenli yol).

Siralama `$UpCase` ile buyuk harfe cevrilmis ad uzerindendir (COLLATION_FILE_NAME).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .ntfsread import (AT_BITMAP, AT_END, AT_INDEX_ALLOCATION, AT_INDEX_ROOT,
                       INDEX_ENTRY_END, INDEX_ENTRY_NODE, NtfsError)
from ..i18n import tr

INDX_MAGIC = b"INDX"
LARGE_INDEX = 0x01
ROOT = -1                      # kok dugumun kimligi


def _align8(n: int) -> int:
    return (n + 7) & ~7


@dataclass
class Entry:
    """Indeks girisi. `$I30`da `ref` MFT basvurusudur; gorunum
    indekslerinde ($SII, $SDH) giris bir anahtar + VERI tasir, ilk 8 bayt
    (veri ofseti, veri uzunlugu) olur."""
    ref: int                   # MFT basvurusu (sira numarasi ust 16 bitte)
    key: bytes                 # $FILE_NAME degeri ya da gorunum anahtari
    child: Optional[int] = None
    data: bytes = b""          # yalnizca gorunum indekslerinde

    def size(self) -> int:
        return _align8(0x10 + len(self.key) + len(self.data)) + \
            (8 if self.child is not None else 0)

    def pack(self) -> bytes:
        base = _align8(0x10 + len(self.key) + len(self.data))
        out = bytearray(self.size())
        flags = INDEX_ENTRY_NODE if self.child is not None else 0
        if self.data:
            data_off = 0x10 + len(self.key)
            struct.pack_into("<HHIHHHH", out, 0, data_off, len(self.data), 0,
                             len(out), len(self.key), flags, 0)
            out[0x10:0x10 + len(self.key)] = self.key
            out[data_off:data_off + len(self.data)] = self.data
            pad = base - (data_off + len(self.data))
            if pad == 4:
                out[base - 4:base] = b"I\x00I\x00"   # Windows'un dolgu imzasi
        else:
            struct.pack_into("<QHHH", out, 0, self.ref, len(out), len(self.key), flags)
            out[0x10:0x10 + len(self.key)] = self.key
        if self.child is not None:
            struct.pack_into("<Q", out, base, self.child)
        return bytes(out)

    @property
    def name(self) -> str:
        n = self.key[0x40]
        return self.key[0x42:0x42 + 2 * n].decode("utf-16-le", "replace")


def _end_entry(child: Optional[int]) -> bytes:
    out = bytearray(0x10 + (8 if child is not None else 0))
    flags = INDEX_ENTRY_END | (INDEX_ENTRY_NODE if child is not None else 0)
    struct.pack_into("<QHHH", out, 0, 0, len(out), 0, flags)
    if child is not None:
        struct.pack_into("<Q", out, 0x10, child)
    return bytes(out)


def parse_entries(buf: bytes, pos: int, end: int,
                  view: bool = False) -> Tuple[List[Entry], Optional[int]]:
    """Dugum govdesi -> (girisler, END'in alt dugumu)."""
    out: List[Entry] = []
    end_child: Optional[int] = None
    while pos + 0x10 <= min(end, len(buf)):
        ref, length, key_len, flags = struct.unpack_from("<QHHH", buf, pos)
        if length < 0x10:
            break
        child = struct.unpack_from("<Q", buf, pos + length - 8)[0] \
            if flags & INDEX_ENTRY_NODE else None
        if flags & INDEX_ENTRY_END:
            end_child = child
            break
        key = bytes(buf[pos + 0x10:pos + 0x10 + key_len])
        if view:
            data_off, data_len = struct.unpack_from("<HH", buf, pos)
            data = bytes(buf[pos + data_off:pos + data_off + data_len])
            out.append(Entry(0, key, child, data))
        else:
            out.append(Entry(ref, key, child))
        pos += length
    return out, end_child


def body_size(entries: List[Entry], end_child: Optional[int]) -> int:
    return sum(e.size() for e in entries) + 0x10 + (8 if end_child is not None else 0)


class DirIndex:
    """Bir indeks ($I30 dizin, ya da $SII/$SDH gorunum) uzerinde B+ agaci.

    Adi tarihsel: ilk kullanimi dizin indeksiydi. `name` ve `collate`
    verilirse ayni kod `$Secure`in gorunum indekslerinde calisir.
    """

    def __init__(self, writer, dir_no: int, name: str = "$I30",
                 collate=None, view: bool = False):
        self.w = writer
        self.fs = writer.fs
        self.dir_no = dir_no
        self.name = name
        self.view = view
        self._collate = collate
        self.cs = self.fs.cluster_size
        rec = self.fs.record(dir_no)
        # Dagilmis kayitta ($ATTRIBUTE_LIST) indeks oznitelikleri uzanti
        # kaydinda olabilir; bu surum listeyi guncellemez (N3/N4).
        writer._refuse_attr_list(dir_no, rec.has_attribute_list)
        root = rec.find(AT_INDEX_ROOT, name)
        if root is None:
            raise NtfsError(tr("Dizin indeksi bulunamadi"))
        self.index_size = struct.unpack_from("<I", root.value, 8)[0] or self.fs.index_size
        # Indeks blogu kumeden kucukse VCN 512 baytlik birimle sayilir
        self.vcn_unit = self.cs if self.index_size >= self.cs else 512
        self.vcns_per_block = max(1, self.index_size // self.vcn_unit)
        self._written: set = set()

    # ------------------------------------------------------------------
    # anahtar karsilastirma
    # ------------------------------------------------------------------
    def ckey(self, entry_or_name):
        if self._collate is not None:
            key = entry_or_name if isinstance(entry_or_name, (bytes, bytearray)) \
                else entry_or_name.key
            return self._collate(key)
        name = entry_or_name if isinstance(entry_or_name, str) else entry_or_name.name
        return self.w.collation_key(name)

    # ------------------------------------------------------------------
    # dugum okuma / yazma
    # ------------------------------------------------------------------
    def _rec(self):
        return self.fs.record(self.dir_no)

    def _alloc_attr(self):
        return self._rec().find(AT_INDEX_ALLOCATION, self.name)

    def load(self, node: int) -> Tuple[List[Entry], Optional[int]]:
        if node == ROOT:
            value = self._rec().find(AT_INDEX_ROOT, self.name).value
            e_off, e_len = struct.unpack_from("<II", value, 0x10)
            return parse_entries(value, 0x10 + e_off, 0x10 + e_len, self.view)
        block = self._read_block(node)
        e_off, e_len = struct.unpack_from("<II", block, 0x18)
        return parse_entries(block, 0x18 + e_off, 0x18 + e_len, self.view)

    def _read_block(self, vcn: int) -> bytearray:
        alloc = self._alloc_attr()
        if alloc is None:
            raise NtfsError(tr("INDX blogu okunamadi (VCN {})", vcn))
        raw = bytearray(self.fs._run_read(alloc.runs, vcn * self.vcn_unit,
                                          self.index_size))
        if raw[:4] != INDX_MAGIC:
            raise NtfsError(tr("INDX blogu okunamadi (VCN {})", vcn))
        self.fs._apply_fixup(raw)
        return raw

    def node_capacity(self) -> int:
        usa_count = self.index_size // 512 + 1
        entries_off = _align8(0x28 + usa_count * 2) - 0x18
        return (self.index_size - 0x18) - entries_off

    def root_capacity(self, entries: List[Entry], end_child: Optional[int]) -> bool:
        """Yeni kok kayda sigar mi?"""
        raw = self.w._raw_record(self.dir_no)
        root = self._rec().find(AT_INDEX_ROOT, self.name)
        pos = self.w._find_attr_offset(raw, root)
        old_len = struct.unpack_from("<I", raw, pos + 4)[0]
        name_len = raw[pos + 9]
        new_value = 0x20 + body_size(entries, end_child)
        new_len = _align8(_align8(0x18 + name_len * 2) + new_value)
        in_use = struct.unpack_from("<I", raw, 0x18)[0]
        return in_use - old_len + new_len + 8 <= self.fs.record_size

    def store(self, node: int, entries: List[Entry], end_child: Optional[int]) -> None:
        body = b"".join(e.pack() for e in entries) + _end_entry(end_child)
        flags = LARGE_INDEX if end_child is not None else 0
        if node == ROOT:
            value = bytearray(0x20 + len(body))
            old = self._rec().find(AT_INDEX_ROOT, self.name).value
            value[0:0x10] = old[0:0x10]           # tur, siralama, blok boyutu
            struct.pack_into("<IIIB", value, 0x10, 0x10, 0x10 + len(body),
                             0x10 + len(body), flags)
            value[0x20:] = body
            self.w._set_record_attr(self.dir_no, AT_INDEX_ROOT, self.name,
                                    self.w._resident_attr(AT_INDEX_ROOT, bytes(value),
                                                          name=self.name))
            return
        if len(body) > self.node_capacity():
            raise NtfsError(tr("INDX blogu tasti (ic hata)"))
        usa_count = self.index_size // 512 + 1
        entries_off = _align8(0x28 + usa_count * 2) - 0x18
        block = bytearray(self.index_size)
        block[0:4] = INDX_MAGIC
        struct.pack_into("<HHQQ", block, 4, 0x28, usa_count, 0, node)
        struct.pack_into("<IIIB", block, 0x18, entries_off, entries_off + len(body),
                         self.index_size - 0x18, flags)
        struct.pack_into("<H", block, 0x28, 1)
        start = 0x18 + entries_off
        block[start:start + len(body)] = body
        self._write_block(node, block)

    def _write_block(self, vcn: int, block: bytearray) -> None:
        alloc = self._alloc_attr()
        usn = (struct.unpack_from("<H", block, 0x28)[0] + 1) & 0xFFFF or 1
        self.w._apply_fixup_out(block, usn)
        # Kume indeks blogundan kucukse (512 B / 1 KiB / 2 KiB kume) blok
        # birden cok kosuya bolunebilir; kume zinciri uzerinden yazilir (N6).
        self.w._write_mapped(alloc.runs, vcn * self.vcn_unit, bytes(block),
                             tr("INDX blogu diskte bulunamadi (VCN {})", vcn))

    # ------------------------------------------------------------------
    # $INDEX_ALLOCATION / $BITMAP tahsisi
    # ------------------------------------------------------------------
    def _bitmap(self) -> bytearray:
        attr = self._rec().find(AT_BITMAP, self.name)
        if attr is None:
            return bytearray()
        return bytearray(self.fs.read_attribute(attr))

    def _set_bitmap(self, bmp: bytes) -> None:
        self.w._set_record_attr(self.dir_no, AT_BITMAP, self.name,
                                self.w._resident_attr(AT_BITMAP, bytes(bmp),
                                                      name=self.name))

    def _block_count(self) -> int:
        alloc = self._alloc_attr()
        if alloc is None:
            return 0
        return alloc.data_size // self.index_size

    def alloc_node(self) -> int:
        bmp = self._bitmap()
        count = self._block_count()
        need = _align8((count + 7) // 8)
        if len(bmp) < need:               # ayrilmis ama bitmap'te olmayan bloklar
            bmp += bytearray(need - len(bmp))
        for i in range(count):
            if not bmp[i >> 3] & (1 << (i & 7)):
                bmp[i >> 3] |= 1 << (i & 7)
                self._set_bitmap(bmp)
                return i * self.vcns_per_block
        # yer yok: $INDEX_ALLOCATION bir blok buyur
        self._grow_allocation()
        i = count
        need = _align8((i >> 3) + 1)
        if len(bmp) < need:
            bmp += bytearray(need - len(bmp))
        bmp[i >> 3] |= 1 << (i & 7)
        self._set_bitmap(bmp)
        return i * self.vcns_per_block

    def free_node(self, vcn: int) -> None:
        i = vcn // self.vcns_per_block
        bmp = self._bitmap()
        if i >> 3 < len(bmp):
            bmp[i >> 3] &= ~(1 << (i & 7)) & 0xFF
            self._set_bitmap(bmp)

    def _take_clusters(self, blocks: int = 1, goal: int = -1) -> List[Tuple[int, int]]:
        clusters = max(1, (self.index_size * blocks + self.cs - 1) // self.cs)
        new_runs = self.w.alloc_clusters_near(goal, clusters)
        # yeni alan sifirlanir (eski veri INDX sanilmasin)
        for lcn, count in new_runs:
            self.fs.dev.write(lcn * self.cs, b"\x00" * (count * self.cs))
        return new_runs

    def _install_allocation(self, runs: List[Tuple[int, int]],
                            first_used: bool) -> None:
        """Ilk $INDEX_ALLOCATION ve $BITMAP:$I30 oznitelikleri."""
        clusters = sum(c for _l, c in runs)
        size = clusters * self.cs if self.index_size < self.cs else self.index_size
        attr = self.w._nonresident_attr(AT_INDEX_ALLOCATION, runs, size, name=self.name)
        self.w._set_record_attr(self.dir_no, AT_INDEX_ALLOCATION, self.name, attr)
        bmp = bytearray(8)
        if first_used:
            bmp[0] = 1
        self._set_bitmap(bmp)

    def _grow_allocation(self) -> None:
        alloc = self._alloc_attr()
        if alloc is None:
            self._install_allocation(self._take_clusters(), first_used=False)
            return
        # Buyume payi: mevcutun yarisi (1..64 blok) ve once onceki parcanin
        # hemen ardi. Eskiden her seferinde TEK blok, rastgele yere: parcali
        # dizinde kosu listesi kayda sigmiyordu (1670 dosyada olculdu).
        blocks = max(1, min(64, self._block_count() // 2))
        last = alloc.runs[-1] if alloc.runs else (-1, 0)
        new_runs = self._take_clusters(blocks, last[0] + last[1] if last[0] >= 0 else -1)
        clusters = sum(c for _l, c in new_runs)
        runs = list(alloc.runs)
        size = alloc.data_size
        for lcn, count in new_runs:
            if runs and runs[-1][0] >= 0 and runs[-1][0] + runs[-1][1] == lcn:
                runs[-1] = (runs[-1][0], runs[-1][1] + count)
            else:
                runs.append((lcn, count))
        size += clusters * self.cs
        attr = self.w._nonresident_attr(AT_INDEX_ALLOCATION, runs, size, name=self.name)
        try:
            self.w._set_record_attr(self.dir_no, AT_INDEX_ALLOCATION, self.name, attr)
        except NtfsError:
            self.w.free_clusters(new_runs)
            raise

    # ------------------------------------------------------------------
    # arama
    # ------------------------------------------------------------------
    def _descend(self, name):
        """Koktan yapraga yol: [(dugum, girisler, end_child, sira)], bulunan.

        `name` dizin indeksinde ad, gorunum indeksinde anahtar baytlaridir."""
        key = self.ckey(name)
        path = []
        node = ROOT
        for _guard in range(64):
            entries, end_child = self.load(node)
            idx = len(entries)
            found = False
            for i, e in enumerate(entries):
                k = self.ckey(e)
                if key == k:
                    idx, found = i, True
                    break
                if key < k:
                    idx = i
                    break
            path.append([node, entries, end_child, idx])
            if found:
                return path, True
            child = entries[idx].child if idx < len(entries) else end_child
            if child is None:
                return path, False
            node = child
        raise NtfsError(tr("Dizin indeksi cok derin (bozuk olabilir)"))

    def contains(self, name) -> bool:
        return self._descend(name)[1]

    def find(self, key) -> Optional[Entry]:
        path, found = self._descend(key)
        if not found:
            return None
        node, entries, _end, idx = path[-1]
        return entries[idx]

    def scan(self, low, high) -> List[Entry]:
        """`low <= anahtar <= high` araligindaki girisler (sirali gezinti)."""
        out: List[Entry] = []

        def walk(node: int) -> None:
            entries, end_child = self.load(node)
            for e in entries:
                k = self.ckey(e)
                if e.child is not None and k >= low:
                    walk(e.child)
                if low <= k <= high:
                    out.append(e)
                if k > high:
                    return
            if end_child is not None:
                walk(end_child)

        walk(ROOT)
        return out

    # ------------------------------------------------------------------
    # ekleme
    # ------------------------------------------------------------------
    def insert(self, new: Entry) -> None:
        path, found = self._descend(new.key if self._collate else new.name)
        if found:
            raise NtfsError(tr("Zaten var: {}", new.name if not self._collate
                               else new.key.hex()))
        path[-1][1].insert(path[-1][3], new)
        level = len(path) - 1
        while True:
            node, entries, end_child, _idx = path[level]
            if node == ROOT:
                if self.root_capacity(entries, end_child):
                    self.store(ROOT, entries, end_child)
                    return
                # Kok tasti: girisleri yeni INDX bloguna tasi (reparent).
                # Sira onemli: dizinin ilk INDX blogunda $INDEX_ALLOCATION
                # kayda ancak kok kuculunce sigar. Once kumeler alinir
                # (basarisizsa hicbir sey degismemistir), sonra kok kuculur,
                # sonra oznitelikler eklenir.
                if self._alloc_attr() is None:
                    runs = self._take_clusters()
                    vcn = 0
                    self.store(ROOT, [], vcn)
                    self._install_allocation(runs, first_used=True)
                else:
                    vcn = self.alloc_node()
                    self.store(ROOT, [], vcn)
                path[level] = [ROOT, [], vcn, 0]
                path.insert(level + 1, [vcn, entries, end_child, 0])
                level += 1
                continue
            if body_size(entries, end_child) <= self.node_capacity():
                self.store(node, entries, end_child)
                return
            median_at = self._median(entries)
            left, median, right = entries[:median_at], entries[median_at], entries[median_at + 1:]
            new_vcn = self.alloc_node()
            self.store(new_vcn, left, median.child)
            self.store(node, right, end_child)
            median = Entry(median.ref, median.key, new_vcn, median.data)
            parent = path[level - 1]
            parent[1].insert(parent[3], median)
            level -= 1

    @staticmethod
    def _median(entries: List[Entry]) -> int:
        total = sum(e.size() for e in entries)
        acc = 0
        for i, e in enumerate(entries):
            acc += e.size()
            if acc >= total // 2:
                return max(1, min(i, len(entries) - 2))
        return len(entries) // 2

    def _ensure_allocation(self) -> None:
        if self._alloc_attr() is None:
            self._grow_allocation()

    # ------------------------------------------------------------------
    # silme
    # ------------------------------------------------------------------
    def remove(self, name: str) -> int:
        path, found = self._descend(name)
        if not found:
            raise NtfsError(tr("Bulunamadi: {}", name))
        node, entries, end_child, idx = path[-1]
        target = entries[idx]
        ref = target.ref & 0xFFFFFFFFFFFF
        if target.child is None:
            del entries[idx]
            if node != ROOT and not entries:
                # Bos yaprak: dengeli yeniden kurulum. Girisler DISKTEN okunur
                # ve silme henuz yazilmadi; hedef disarida birakilmazsa geri
                # gelir. Eskiden burada exclude yoktu: kayit serbest kaliyor,
                # giris indekste sahipsiz duruyordu — Windows dizini "bozuk ve
                # okunamaz" saydi (olculdu 2026-09-29).
                self.rebuild(exclude=name)
                return ref
            self.store(node, entries, end_child)
            return ref
        # ara dugum: sol alt agacin en buyuk girisi (oncul) yerine gecer
        chain = []
        sub = target.child
        for _guard in range(64):
            s_entries, s_end = self.load(sub)
            chain.append((sub, s_entries, s_end))
            if s_end is None:
                break
            sub = s_end
        leaf, l_entries, l_end = chain[-1]
        if len(l_entries) <= 1:
            # oncul alinirsa yaprak bosalir: dengeli yeniden kurulum
            self.rebuild(exclude=name)
            return ref
        pred = l_entries.pop()
        self.store(leaf, l_entries, l_end)
        entries[idx] = Entry(pred.ref, pred.key, target.child, pred.data)
        if node == ROOT and not self.root_capacity(entries, end_child):
            self.rebuild(exclude=name)
            return ref
        self.store(node, entries, end_child)
        return ref

    # ------------------------------------------------------------------
    # dengeli yeniden kurulum (guvenli yol)
    # ------------------------------------------------------------------
    def all_entries(self) -> List[Entry]:
        out: List[Entry] = []

        def walk(node: int) -> None:
            entries, end_child = self.load(node)
            for e in entries:
                if e.child is not None:
                    walk(e.child)
                out.append(Entry(e.ref, e.key, None, e.data))
            if end_child is not None:
                walk(end_child)

        walk(ROOT)
        return out

    def rebuild(self, exclude: Optional[str] = None,
                extra: Optional[Entry] = None) -> None:
        items = [e for e in self.all_entries()
                 if exclude is None or self.ckey(e) != self.ckey(exclude)]
        if extra is not None:
            items.append(extra)
        items.sort(key=self.ckey)
        # tum INDX bloklari bosaltilir
        if self._alloc_attr() is not None:
            bmp = self._bitmap()
            self._set_bitmap(bytes(len(bmp)))
        if self.root_capacity(items, None):
            self.store(ROOT, items, None)
            return
        self._ensure_allocation()
        # Yapraklar %75 doldurulur: ardindan gelen eklemeler hemen bolme yapmasin
        fill = self.node_capacity() * 3 // 4
        level_items: List[Entry] = items
        last_child: Optional[int] = None
        while True:
            if self.root_capacity(level_items, last_child):
                self.store(ROOT, level_items, last_child)
                return
            promoted: List[Entry] = []
            current: List[Entry] = []
            i = 0
            while i < len(level_items):
                e = level_items[i]
                if current and body_size(current + [e], e.child) > fill \
                        and i < len(level_items) - 1:
                    vcn = self.alloc_node()
                    self.store(vcn, current, e.child)
                    promoted.append(Entry(e.ref, e.key, vcn, e.data))
                    current = []
                    i += 1
                    continue
                current.append(e)
                i += 1
            vcn = self.alloc_node()
            self.store(vcn, current, last_child)
            level_items, last_child = promoted, vcn


def verify_tree(writer, dir_no: int) -> List[str]:
    """Dizin indeksinin tutarliligi; bos liste = sorun yok.

    ntfs-3g bu kurallarin bir kismini denetlemez; Windows ise ihlali
    "dosya veya dizin bozuk" ile karsilar (olculdu). Bu yuzden testler
    platformdan bagimsiz olarak bu denetimi kosar:
      * girisler (sirali gezintide) $UpCase siralamasinda artan,
      * tum yapraklar ayni derinlikte,
      * her giris kullanimdaki bir kaydi gosterir ve kayitta ayni adli bir
        $FILE_NAME (bu dizin ust dizin olarak) vardir,
      * $BITMAP:$I30 tam olarak kullanilan INDX bloklarini isaretler,
      * her INDX blogunun basligindaki VCN kendi VCN'idir.
    """
    from .ntfsread import AT_FILE_NAME
    di = DirIndex(writer, dir_no)
    problems: List[str] = []
    names: List[Entry] = []
    depths = set()
    used = set()

    def walk(node: int, depth: int) -> None:
        entries, end_child = di.load(node)
        if node != ROOT:
            used.add(node // di.vcns_per_block)
            block = di._read_block(node)
            if struct.unpack_from("<Q", block, 0x10)[0] != node:
                problems.append(f"INDX VCN uyusmuyor: {node}")
        children = [e.child for e in entries if e.child is not None]
        if end_child is not None:
            children.append(end_child)
        if not children:
            depths.add(depth)
        elif len(children) != len(entries) + 1:
            problems.append(f"dugum {node}: alt dugum sayisi tutarsiz")
        for e in entries:
            if e.child is not None:
                walk(e.child, depth + 1)
            names.append(e)
        if end_child is not None:
            walk(end_child, depth + 1)

    walk(ROOT, 0)
    keys = [di.ckey(e) for e in names]
    for i in range(len(keys) - 1):
        if not keys[i] < keys[i + 1]:
            problems.append(f"sira bozuk: {names[i].name!r} >= {names[i + 1].name!r}")
            break
    if len(depths) > 1:
        problems.append(f"yaprak derinlikleri farkli: {sorted(depths)}")
    if di._alloc_attr() is not None:
        bmp = di._bitmap()
        marked = {i for i in range(len(bmp) * 8) if bmp[i >> 3] >> (i & 7) & 1}
        if marked != used:
            problems.append(f"$BITMAP tutarsiz: fazla {sorted(marked - used)[:5]} "
                            f"eksik {sorted(used - marked)[:5]}")
    for e in names:
        no = e.ref & 0xFFFFFFFFFFFF
        raw = writer._raw_record(no)
        if not struct.unpack_from("<H", raw, 0x16)[0] & 1:
            problems.append(f"sahipsiz giris {e.name!r}: kayit {no} kullanilmiyor")
            continue
        fns = [writer._fn_info(it[2]) for it in writer._record_attrs(no)[0]
               if it[0] == AT_FILE_NAME]
        if not any(parent == dir_no and fn == e.name for parent, fn, _ns in fns):
            problems.append(f"giris {e.name!r}: kayit {no} bu adi tasimiyor")
    return problems
