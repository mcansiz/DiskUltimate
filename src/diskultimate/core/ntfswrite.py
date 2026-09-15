"""NTFS **yazma** destegi (1. asama).

Okuma `ntfsread.py`, bicimlendirme `ntfs.py` icindedir. Bu modul var olan bir
NTFS birimine dosya/klasor ekler, siler ve yeniden adlandirir.

## NTFS'e yazmak neden ext'ten zor

ext'te dizin duz bir kayit zinciridir; NTFS'te **sirali bir B+ agaci**dir.
Yeni giris dogru yere sokulmali, dizin dolunca dugum bolunmeli ve
`$INDEX_ROOT`'tan `$INDEX_ALLOCATION`'a tasinmalidir. Yanlis yapilirsa `chkdsk`
indeksi bozuk sayar ve dosya "yok" gorunur.

Bu surumun kapsami bilincli olarak dar tutuldu:

  * Yalnizca indeksi **`$INDEX_ROOT` icinde duran** (yani `$INDEX_ALLOCATION`
    tasimayan) dizinlere yazilir. Buyuk dizinlerde islem **reddedilir**.
  * Yeni girisin sigmasi icin ust dizinin FILE kaydinda yer olmalidir; yoksa
    reddedilir (indeksi tasimak ayri bir istir).
  * Sikistirilmis/sifrelenmis akislar desteklenmez.

Reddetme sessiz degildir: nedeni metinle dondurulur. **Yanlis yazip bozmaktansa
yazmamak yeglenir.**

Yazilan her birim `ntfsfix` ve mumkunse `ntfs-3g` ile baglanarak dogrulanir
(`tests/ntfs_write_check.py`).
"""
from __future__ import annotations

import datetime
import struct
from typing import List, Optional, Tuple

from .ntfs import upcase_table
from .ntfsread import (AT_BITMAP, AT_DATA, AT_END, AT_FILE_NAME,
                       AT_INDEX_ALLOCATION, AT_INDEX_ROOT,
                       AT_STANDARD_INFORMATION, FILE_ATTR_DIRECTORY,
                       FILE_MAGIC, INDEX_ENTRY_END, INDEX_ENTRY_NODE,
                       MFT_FLAG_DIRECTORY, MFT_FLAG_IN_USE, MFT_RECORD_ROOT,
                       Attribute, MftRecord, NtfsError, NtfsFS)

MFT_BITMAP_RECORD = 0          # $MFT kaydi; kendi $BITMAP'i kayit tahsisini tutar
BITMAP_RECORD = 6              # $Bitmap: kume tahsisi
MFTMIRR_RECORD = 1

# 0-15 sistem dosyalarinin kayitlaridir. 16-23 "ileride kullanilmak uzere"
# ayrilmis sayilir ama `$MFT` bitmap'inde **bos** isaretlidir; asil yetkili
# bitmap oldugu icin oradan da tahsis edilir.
FIRST_USER_RECORD = 16
MFT_GROW_RECORDS = 32          # MFT dolunca bir seferde eklenecek kayit sayisi

STD_INFO_SIZE = 0x48           # NTFS 3.1 $STANDARD_INFORMATION
INDEX_ENTRY_HEADER = 0x10


def _now_filetime() -> int:
    """Simdiki zamani Windows FILETIME olarak dondurur."""
    delta = datetime.datetime.now() - datetime.datetime(1601, 1, 1)
    return int(delta.total_seconds() * 10_000_000)


def _align8(n: int) -> int:
    return (n + 7) & ~7


class NtfsWriter:
    """Bir `NtfsFS` uzerine yazma islemleri ekler."""

    def __init__(self, fs: NtfsFS):
        self.fs = fs
        self.dev = fs.dev
        self.cs = fs.cluster_size
        self._upcase = upcase_table()

    # ------------------------------------------------------------------
    # Destek denetimi
    # ------------------------------------------------------------------
    def write_support(self) -> Tuple[bool, str]:
        if getattr(self.dev, "readonly", False):
            return False, "Kaynak salt okunur acildi."
        return True, ""

    def _require_writable(self) -> None:
        ok, reason = self.write_support()
        if not ok:
            raise NtfsError(reason)

    # ------------------------------------------------------------------
    # Siralama (collation) — NTFS dizin girisleri sirali olmak zorundadir
    # ------------------------------------------------------------------
    def collation_key(self, name: str) -> List[int]:
        """`$UpCase` ile buyuk harfe cevrilmis kod birimleri."""
        out = []
        for ch in name:
            code = ord(ch)
            if code * 2 + 2 <= len(self._upcase):
                out.append(struct.unpack_from("<H", self._upcase, code * 2)[0])
            else:
                out.append(code)
        return out

    # ------------------------------------------------------------------
    # Bitmap tahsisi ($Bitmap ve $MFT'nin $BITMAP'i)
    # ------------------------------------------------------------------
    def _bitmap_attr(self) -> Attribute:
        attr = self.fs.record(BITMAP_RECORD).find(AT_DATA)
        if attr is None:
            raise NtfsError("$Bitmap okunamadi")
        return attr

    def _read_bitmap(self, attr: Attribute) -> bytearray:
        return bytearray(self.fs.read_attribute(attr))

    def _write_attr_data(self, attr: Attribute, data: bytes) -> None:
        """Yerlesik olmayan bir oznitelugun icerigini yerinde gunceller."""
        if attr.resident:
            raise NtfsError("Yerlesik oznitelik bu yoldan yazilamaz")
        pos = 0
        for lcn, count in attr.runs:
            span = count * self.cs
            if lcn < 0:
                pos += span
                continue
            chunk = data[pos:pos + span]
            if not chunk:
                break
            self.dev.write(lcn * self.cs, chunk.ljust(
                min(span, len(data) - pos), b"\x00"))
            pos += span

    @staticmethod
    def _bit(buf: bytearray, index: int) -> bool:
        return bool(buf[index >> 3] & (1 << (index & 7)))

    @staticmethod
    def _set_bit(buf: bytearray, index: int, value: bool) -> None:
        if value:
            buf[index >> 3] |= (1 << (index & 7))
        else:
            buf[index >> 3] &= ~(1 << (index & 7)) & 0xFF

    def alloc_clusters(self, count: int) -> List[Tuple[int, int]]:
        """Ardisik kume arar; bulamazsa parcali tahsis eder."""
        attr = self._bitmap_attr()
        bmp = self._read_bitmap(attr)
        total_clusters_on_disk = self.fs.total_sectors * self.fs.sector_size // self.cs
        limit = min(len(bmp) * 8, total_clusters_on_disk)

        runs: List[Tuple[int, int]] = []
        remaining = count
        i = 0
        while i < limit and remaining:
            if self._bit(bmp, i):
                i += 1
                continue
            start = i
            while i < limit and remaining and not self._bit(bmp, i):
                self._set_bit(bmp, i, True)
                i += 1
                remaining -= 1
            runs.append((start, i - start))
        if remaining:
            raise NtfsError("Diskte yeterli bos kume yok")
        self._write_attr_data(attr, bytes(bmp))
        return runs

    def free_clusters(self, runs: List[Tuple[int, int]]) -> None:
        attr = self._bitmap_attr()
        bmp = self._read_bitmap(attr)
        for lcn, count in runs:
            if lcn < 0:
                continue
            for k in range(count):
                if lcn + k < len(bmp) * 8:
                    self._set_bit(bmp, lcn + k, False)
        self._write_attr_data(attr, bytes(bmp))

    def _mft_bitmap(self) -> Attribute:
        attr = self.fs.record(MFT_BITMAP_RECORD).find(AT_BITMAP)
        if attr is None:
            raise NtfsError("$MFT bitmap'i okunamadi")
        return attr

    def alloc_record(self) -> int:
        """Bos bir MFT kaydi tahsis eder; gerekirse `$MFT`'yi buyutur."""
        for attempt in (0, 1):
            attr = self._mft_bitmap()
            bmp = bytearray(self.fs.read_attribute(attr))
            limit = min(len(bmp) * 8, self.fs.mft_size // self.fs.record_size)
            for i in range(FIRST_USER_RECORD, limit):
                if not self._bit(bmp, i):
                    self._set_bit(bmp, i, True)
                    if attr.resident:
                        self._patch_resident(MFT_BITMAP_RECORD, attr, bytes(bmp))
                    else:
                        self._write_attr_data(attr, bytes(bmp))
                    return i
            if attempt == 0:
                self._extend_mft()
        raise NtfsError("Bos MFT kaydi yok ve $MFT buyutulemedi")

    def _extend_mft(self) -> None:
        """`$MFT`'yi birkac kayit kadar buyutur.

        Yeni kayitlar icin kume tahsis edilir, `$MFT`'nin kendi `$DATA`
        oznitelugunun veri kosullari yeniden kodlanir ve boyut alanlari
        guncellenir. Yeni alan sifirlanir: NTFS sifir kaydi "kullanilmiyor"
        sayar.
        """
        rec0 = self.fs.record(MFT_BITMAP_RECORD)
        data = rec0.find(AT_DATA)
        if data is None or data.resident:
            raise NtfsError("$MFT veri oznitelugu okunamadi")

        grow_bytes = MFT_GROW_RECORDS * self.fs.record_size
        grow_clusters = max(1, (grow_bytes + self.cs - 1) // self.cs)
        new_runs = self.alloc_clusters(grow_clusters)

        runs = list(data.runs)
        for lcn, count in new_runs:
            if runs and runs[-1][0] >= 0 and runs[-1][0] + runs[-1][1] == lcn:
                runs[-1] = (runs[-1][0], runs[-1][1] + count)   # bitisikse birlestir
            else:
                runs.append((lcn, count))

        new_size = data.data_size + grow_clusters * self.cs
        raw = self._raw_record(MFT_BITMAP_RECORD)
        pos = self._find_attr_offset(raw, data)
        attr_len = struct.unpack_from("<I", raw, pos + 4)[0]
        mapping_off = struct.unpack_from("<H", raw, pos + 0x20)[0]
        pairs = _encode_runs(runs)
        if mapping_off + len(pairs) > attr_len:
            raise NtfsError(
                "$MFT buyutulemedi: veri kosullari kayda sigmiyor "
                "(bu surumde $MFT kaydi genisletilemez).")

        total_clusters = sum(c for _l, c in runs)
        struct.pack_into("<Q", raw, pos + 0x18, max(0, total_clusters - 1))
        struct.pack_into("<QQQ", raw, pos + 0x28,
                         total_clusters * self.cs, new_size, new_size)
        for i in range(mapping_off, attr_len):
            raw[pos + i] = 0
        raw[pos + mapping_off:pos + mapping_off + len(pairs)] = pairs
        self.write_record(MFT_BITMAP_RECORD, raw)

        # Yeni alani sifirla, sonra okuyucunun kume zincirini tazele
        for lcn, count in new_runs:
            zero_block = b"\x00" * self.cs
            for k in range(count):
                self.dev.write((lcn + k) * self.cs, zero_block)
        self.fs._cache.clear()
        self.fs._load_mft()

    def free_record(self, number: int) -> None:
        attr = self._mft_bitmap()
        bmp = bytearray(self.fs.read_attribute(attr))
        if number < len(bmp) * 8:
            self._set_bit(bmp, number, False)
            if attr.resident:
                self._patch_resident(MFT_BITMAP_RECORD, attr, bytes(bmp))
            else:
                self._write_attr_data(attr, bytes(bmp))

    # ------------------------------------------------------------------
    # FILE kaydi yazimi
    # ------------------------------------------------------------------
    def _record_offset(self, number: int) -> int:
        """Kaydin diskteki bayt ofseti ($MFT kume zinciri uzerinden)."""
        target = number * self.fs.record_size
        pos = 0
        for lcn, count in self.fs._mft_runs:
            span = count * self.cs
            if target < pos + span and lcn >= 0:
                return lcn * self.cs + (target - pos)
            pos += span
        raise NtfsError(f"MFT kaydi diskte bulunamadi: {number}")

    def _apply_fixup_out(self, raw: bytearray, usn: int) -> None:
        """Fixup dizisini **kurar**: her sektorun son iki bayti diziye tasinir.

        Okuma tarafinin tersidir. Yapilmazsa NTFS kaydi "bozuk" sayar.
        """
        usa_off, usa_count = struct.unpack_from("<HH", raw, 4)
        struct.pack_into("<H", raw, usa_off, usn)
        for i in range(1, usa_count):
            end = i * self.fs.sector_size - 2
            if end + 2 > len(raw):
                break
            raw[usa_off + i * 2:usa_off + i * 2 + 2] = raw[end:end + 2]
            struct.pack_into("<H", raw, end, usn)

    def write_record(self, number: int, raw: bytearray) -> None:
        """FILE kaydini fixup kurarak diske yazar."""
        if raw[:4] != FILE_MAGIC:
            raise NtfsError("FILE imzasi yok")
        usn = (struct.unpack_from("<H", raw, struct.unpack_from("<H", raw, 4)[0])[0]
               + 1) & 0xFFFF
        if usn in (0, 0xFFFF):
            usn = 1
        self._apply_fixup_out(raw, usn)
        self.dev.write(self._record_offset(number), bytes(raw))
        self.fs._cache.pop(number, None)
        self._sync_mirror(number, raw)

    def _sync_mirror(self, number: int, raw: bytes) -> None:
        """Ilk dort kayit `$MFTMirr` icinde de tutulur."""
        if number >= 4:
            return
        try:
            mirr = self.fs.record(MFTMIRR_RECORD).find(AT_DATA)
            if mirr is None or mirr.resident or not mirr.runs:
                return
            lcn = mirr.runs[0][0]
            if lcn >= 0:
                self.dev.write(lcn * self.cs + number * self.fs.record_size, raw)
        except NtfsError:
            pass

    def _patch_resident(self, rec_no: int, attr: Attribute,
                        value: bytes) -> None:
        """Yerlesik bir oznitelugun degerini **ayni uzunlukta** gunceller."""
        raw = bytearray(self._raw_record(rec_no))
        pos = self._find_attr_offset(raw, attr)
        value_len, value_off = struct.unpack_from("<IH", raw, pos + 0x10)
        if len(value) != value_len:
            raise NtfsError("Yerlesik oznitelik boyutu degisemez")
        raw[pos + value_off:pos + value_off + value_len] = value
        self.write_record(rec_no, raw)

    def _raw_record(self, number: int) -> bytearray:
        raw = bytearray(self.fs._run_read(
            self.fs._mft_runs, number * self.fs.record_size,
            self.fs.record_size))
        self.fs._apply_fixup(raw)
        return raw

    @staticmethod
    def _find_attr_offset(raw: bytearray, attr: Attribute) -> int:
        """Bir oznitelugun kayit icindeki ofsetini bulur."""
        pos = struct.unpack_from("<H", raw, 0x14)[0]
        while pos + 8 <= len(raw):
            kind = struct.unpack_from("<I", raw, pos)[0]
            if kind == AT_END:
                break
            length = struct.unpack_from("<I", raw, pos + 4)[0]
            if length < 16:
                break
            name_len = raw[pos + 9]
            name_off = struct.unpack_from("<H", raw, pos + 0x0A)[0]
            name = raw[pos + name_off:pos + name_off + name_len * 2].decode(
                "utf-16-le", "replace") if name_len else ""
            if kind == attr.kind and name == attr.name:
                return pos
            pos += length
        raise NtfsError("Oznitelik kayitta bulunamadi")

    # ------------------------------------------------------------------
    # Oznitelik kurucular
    # ------------------------------------------------------------------
    @staticmethod
    def _attr_header(kind: int, length: int, resident: bool, name: str = "",
                     attr_id: int = 0) -> bytearray:
        name_bytes = name.encode("utf-16-le")
        head = bytearray(length)
        struct.pack_into("<II", head, 0, kind, length)
        head[8] = 0 if resident else 1
        head[9] = len(name)
        struct.pack_into("<HHH", head, 0x0A,
                         0x40 if not resident else 0x18, 0, attr_id)
        if name:
            off = 0x40 if not resident else 0x18
            head[off:off + len(name_bytes)] = name_bytes
        return head

    def _resident_attr(self, kind: int, value: bytes, name: str = "",
                       attr_id: int = 0, indexed: int = 0) -> bytes:
        name_bytes = name.encode("utf-16-le")
        value_off = _align8(0x18 + len(name_bytes))
        length = _align8(value_off + len(value))
        attr = bytearray(length)
        struct.pack_into("<II", attr, 0, kind, length)
        attr[8] = 0
        attr[9] = len(name)
        struct.pack_into("<HHH", attr, 0x0A, 0x18, 0, attr_id)
        struct.pack_into("<IHBB", attr, 0x10, len(value), value_off, indexed, 0)
        if name:
            attr[0x18:0x18 + len(name_bytes)] = name_bytes
        attr[value_off:value_off + len(value)] = value
        return bytes(attr)

    def _nonresident_attr(self, kind: int, runs: List[Tuple[int, int]],
                          data_size: int, name: str = "",
                          attr_id: int = 0) -> bytes:
        name_bytes = name.encode("utf-16-le")
        mapping_off = _align8(0x40 + len(name_bytes))
        pairs = _encode_runs(runs)
        length = _align8(mapping_off + len(pairs))
        total_clusters = sum(c for _l, c in runs)
        attr = bytearray(length)
        struct.pack_into("<II", attr, 0, kind, length)
        attr[8] = 1
        attr[9] = len(name)
        struct.pack_into("<HHH", attr, 0x0A, 0x40, 0, attr_id)
        struct.pack_into("<QQHHI", attr, 0x10, 0, max(0, total_clusters - 1),
                         mapping_off, 0, 0)
        allocated_bytes = total_clusters * self.cs
        struct.pack_into("<QQQ", attr, 0x28, allocated_bytes, data_size, data_size)
        if name:
            attr[0x40:0x40 + len(name_bytes)] = name_bytes
        attr[mapping_off:mapping_off + len(pairs)] = pairs
        return bytes(attr)

    def _std_info(self, file_attr: int) -> bytes:
        now = _now_filetime()
        value = bytearray(STD_INFO_SIZE)
        struct.pack_into("<QQQQ", value, 0, now, now, now, now)
        struct.pack_into("<I", value, 0x20, file_attr)
        return bytes(value)

    def _file_name_value(self, parent_ref: int, name: str,
                         file_attr: int, size: int = 0,
                         allocated: int = 0) -> bytes:
        raw_name = name.encode("utf-16-le")
        value = bytearray(0x42 + len(raw_name))
        now = _now_filetime()
        struct.pack_into("<Q", value, 0, parent_ref)
        struct.pack_into("<QQQQ", value, 8, now, now, now, now)
        struct.pack_into("<QQ", value, 0x28, allocated, size)
        struct.pack_into("<I", value, 0x38, file_attr)
        value[0x40] = len(name)
        value[0x41] = 1                       # Win32 ad turu
        value[0x42:] = raw_name
        return bytes(value)

    def _build_record(self, number: int, sequence: int, flags: int,
                      attrs: List[bytes]) -> bytearray:
        size = self.fs.record_size
        usa_count = size // self.fs.sector_size + 1
        usa_off = 0x30
        attrs_off = _align8(usa_off + usa_count * 2)
        raw = bytearray(size)
        raw[0:4] = FILE_MAGIC
        struct.pack_into("<HH", raw, 4, usa_off, usa_count)
        struct.pack_into("<HHHH", raw, 0x10, sequence, 1, attrs_off, flags)
        pos = attrs_off
        for a in attrs:
            if pos + len(a) + 8 > size:
                raise NtfsError(
                    "Kayit dolu: bu surumde oznitelikler tek FILE kaydina sigmali")
            raw[pos:pos + len(a)] = a
            pos += len(a)
        struct.pack_into("<I", raw, pos, AT_END)
        struct.pack_into("<II", raw, 0x18, _align8(pos + 8), size)
        struct.pack_into("<Q", raw, 0x20, 0)
        struct.pack_into("<H", raw, 0x28, len(attrs) + 1)
        return raw

    # ------------------------------------------------------------------
    # Dizin indeksi ($INDEX_ROOT icinde)
    # ------------------------------------------------------------------
    def _index_root(self, rec: MftRecord) -> Attribute:
        root = rec.find(AT_INDEX_ROOT, "$I30")
        if root is None:
            raise NtfsError("Dizin indeksi bulunamadi")
        return root

    # --- $INDEX_ALLOCATION (INDX bloklari) ------------------------------
    @staticmethod
    def _node_has_children(value: bytes) -> bool:
        return bool(struct.unpack_from("<B", value, 0x1C)[0] & 1)

    def _leaf_vcn(self, rec: MftRecord, key_name: str) -> Optional[int]:
        """Anahtarin ait oldugu yaprak dugumun VCN'ini bulur.

        Kok dugumden baslayip, anahtardan **buyuk** ilk girisin alt dugumune
        iner. Yaprakta alt dugum yoktur; o zaman `None` doner ve giris kokun
        kendisine yazilir.
        """
        alloc = rec.find(AT_INDEX_ALLOCATION, "$I30")
        if alloc is None:
            return None
        root = self._index_root(rec)
        if not self._node_has_children(root.value):
            return None
        wanted = self.collation_key(key_name)
        buf, base, end = root.value, 0x10, None
        vcn: Optional[int] = None
        guard = 0
        while guard < 64:
            guard += 1
            entries_off, index_len = struct.unpack_from("<II", buf, base)
            pos = base + entries_off
            limit = base + index_len
            child: Optional[int] = None
            while pos + INDEX_ENTRY_HEADER <= min(limit, len(buf)):
                entry_len, key_len, flags = struct.unpack_from("<HHH", buf, pos + 8)
                if entry_len < INDEX_ENTRY_HEADER:
                    break
                child = (struct.unpack_from("<Q", buf, pos + entry_len - 8)[0]
                         if flags & INDEX_ENTRY_NODE else None)
                if flags & INDEX_ENTRY_END:
                    child = child
                    break
                entry_label = self._entry_name(bytes(buf[pos:pos + entry_len]))
                if wanted <= self.collation_key(entry_label):
                    child = child
                    break
                pos += entry_len
            if child is None:
                return vcn                    # yaprak: daha asagi inilmez
            vcn = child
            block = self.fs._index_block(alloc, vcn)
            if block is None:
                raise NtfsError(f"INDX blogu okunamadi (VCN {vcn})")
            buf, base = block, 0x18

    def _write_indx(self, alloc: Attribute, vcn: int, buf: bytearray) -> None:
        """INDX blogunu fixup kurarak yerine yazar."""
        usa_off = struct.unpack_from("<H", buf, 4)[0]
        usn = (struct.unpack_from("<H", buf, usa_off)[0] + 1) & 0xFFFF
        if usn in (0, 0xFFFF):
            usn = 1
        self._apply_fixup_out(buf, usn)
        target = vcn * self.cs
        pos = 0
        for lcn, count in alloc.runs:
            span = count * self.cs
            if target < pos + span and lcn >= 0:
                self.dev.write(lcn * self.cs + (target - pos), bytes(buf))
                return
            pos += span
        raise NtfsError(f"INDX blogu diskte bulunamadi (VCN {vcn})")

    def _indx_entries(self, block: bytes) -> Tuple[List[bytes], bytes]:
        """INDX govdesindeki girisler ve son (END) giris."""
        entries_off, index_len = struct.unpack_from("<II", block, 0x18)
        pos = 0x18 + entries_off
        limit = 0x18 + index_len
        out: List[bytes] = []
        tail = b""
        while pos + INDEX_ENTRY_HEADER <= min(limit, len(block)):
            entry_len, _key_len, flags = struct.unpack_from("<HHH", block, pos + 8)
            if entry_len < INDEX_ENTRY_HEADER:
                break
            chunk = bytes(block[pos:pos + entry_len])
            if flags & INDEX_ENTRY_END:
                tail = chunk
                break
            out.append(chunk)
            pos += entry_len
        return out, tail

    def _rewrite_indx(self, alloc: Attribute, vcn: int,
                      entries: List[bytes], tail: bytes) -> None:
        """Girisleri sirali yazarak INDX blogunu yeniden kurar."""
        block = self.fs._index_block(alloc, vcn)
        if block is None:
            raise NtfsError(f"INDX blogu okunamadi (VCN {vcn})")
        entries = sorted(entries, key=lambda e: self.collation_key(
            self._entry_name(e)))
        body = b"".join(entries) + tail

        # `entries_offset` **korunur**. INDX blogunda guncelleme dizisi (USA)
        # INDEX_HEADER ile girisler arasinda durur; ofseti 0x10'a zorlamak o
        # diziyi ezer ve blok bozulur (ilk yazimda bu yapildi: ntfsfix
        # "File name overflow from index entry" dedi).
        entries_off, _old_len, allocated_size = struct.unpack_from(
            "<III", block, 0x18)
        if len(body) > allocated_size - entries_off:
            raise NtfsError(
                "Dizin indeks blogu doldu; bu surumde B+ dugumu bolunemez. "
                "Daha az giris deneyin.")
        new_block = bytearray(block)
        struct.pack_into("<I", new_block, 0x18 + 4, entries_off + len(body))
        start = 0x18 + entries_off
        new_block[start:start + len(body)] = body
        for i in range(start + len(body), len(new_block)):
            new_block[i] = 0
        self._write_indx(alloc, vcn, new_block)

    def _parse_index_entries(self, value: bytes) -> List[bytes]:
        """Indeks govdesindeki girisleri (son giris haric) listeler."""
        entries_off, index_len = struct.unpack_from("<II", value, 0x10)
        pos = 0x10 + entries_off
        end = 0x10 + index_len
        out: List[bytes] = []
        while pos + INDEX_ENTRY_HEADER <= min(end, len(value)):
            entry_len, _key_len, flags = struct.unpack_from("<HHH", value, pos + 8)
            if entry_len < INDEX_ENTRY_HEADER:
                break
            if flags & INDEX_ENTRY_END:
                break
            out.append(bytes(value[pos:pos + entry_len]))
            pos += entry_len
        return out

    @staticmethod
    def _entry_name(entry: bytes) -> str:
        name_len = entry[INDEX_ENTRY_HEADER + 0x40]
        start = INDEX_ENTRY_HEADER + 0x42
        return entry[start:start + name_len * 2].decode("utf-16-le", "replace")

    def _make_index_entry(self, mft_ref: int, sequence: int, key: bytes) -> bytes:
        length = _align8(INDEX_ENTRY_HEADER + len(key))
        entry = bytearray(length)
        struct.pack_into("<Q", entry, 0, (sequence << 48) | mft_ref)
        struct.pack_into("<HHH", entry, 8, length, len(key), 0)
        entry[INDEX_ENTRY_HEADER:INDEX_ENTRY_HEADER + len(key)] = key
        return bytes(entry)

    def _rebuild_index_root(self, entries: List[bytes]) -> bytes:
        """Girislerden yeni bir `$INDEX_ROOT` degeri kurar (sirali)."""
        entries = sorted(entries, key=lambda e: self.collation_key(
            self._entry_name(e)))
        body = b"".join(entries)
        end = bytearray(INDEX_ENTRY_HEADER)
        struct.pack_into("<HHH", end, 8, INDEX_ENTRY_HEADER, 0, INDEX_ENTRY_END)
        body += bytes(end)

        value = bytearray(0x20 + len(body))
        struct.pack_into("<III", value, 0, AT_FILE_NAME, 1, self.fs.index_size)
        value[0x0C] = max(1, self.fs.index_size // self.cs)
        struct.pack_into("<IIIB", value, 0x10, 0x10, 0x10 + len(body),
                         0x10 + len(body), 0)
        value[0x20:] = body
        return bytes(value)

    def _replace_index_root(self, dir_no: int, new_value: bytes) -> None:
        """Ust dizinin `$INDEX_ROOT` oznitelugunu yeni degerle degistirir."""
        rec = self.fs.record(dir_no)
        root = self._index_root(rec)
        raw = self._raw_record(dir_no)
        pos = self._find_attr_offset(raw, root)
        old_len = struct.unpack_from("<I", raw, pos + 4)[0]

        new_attr = self._resident_attr(AT_INDEX_ROOT, new_value, name="$I30",
                                        attr_id=struct.unpack_from(
                                            "<H", raw, pos + 0x0E)[0],
                                        indexed=0)
        bytes_in_use = struct.unpack_from("<I", raw, 0x18)[0]
        delta = len(new_attr) - old_len
        if bytes_in_use + delta + 8 > self.fs.record_size:
            raise NtfsError(
                "Dizin kaydi doldu: bu surumde indeks $INDEX_ALLOCATION'a "
                "tasinamaz. Daha az giris deneyin.")
        tail = bytes(raw[pos + old_len:bytes_in_use])
        new_raw = bytearray(raw)
        new_raw[pos:pos + len(new_attr)] = new_attr
        new_raw[pos + len(new_attr):pos + len(new_attr) + len(tail)] = tail
        end = pos + len(new_attr) + len(tail)
        for i in range(end, self.fs.record_size):
            new_raw[i] = 0
        struct.pack_into("<I", new_raw, 0x18, _align8(end))
        self.write_record(dir_no, new_raw)

    def index_add(self, dir_no: int, name: str, mft_ref: int, sequence: int,
                  file_attr: int, size: int, allocated: int) -> None:
        """Dizin indeksine yeni giris ekler.

        Indeks `$INDEX_ROOT` icinde duruyorsa oraya, B+ agacina tasmissa
        anahtarin ait oldugu **yaprak INDX blogu** icine eklenir. Dugum
        bolunmesi gerekiyorsa islem reddedilir.
        """
        rec = self.fs.record(dir_no)
        if any(e.name.lower() == name.lower()
               for e in self.fs.listdir_record(rec)):
            raise NtfsError(f"Zaten var: {name}")
        key = self._file_name_value(
            (rec.sequence << 48) | dir_no, name, file_attr, size, allocated)
        entry = self._make_index_entry(mft_ref, sequence, key)

        vcn = self._leaf_vcn(rec, name)
        if vcn is None:
            entries = self._parse_index_entries(self._index_root(rec).value)
            entries.append(entry)
            self._replace_index_root(dir_no, self._rebuild_index_root(entries))
            return
        alloc = rec.find(AT_INDEX_ALLOCATION, "$I30")
        block = self.fs._index_block(alloc, vcn)
        if block is None:
            raise NtfsError(f"INDX blogu okunamadi (VCN {vcn})")
        entries, tail = self._indx_entries(block)
        entries.append(entry)
        self._rewrite_indx(alloc, vcn, entries, tail)

    def index_remove(self, dir_no: int, name: str) -> int:
        """Girisi siler ve hedefin MFT numarasini dondurur."""
        rec = self.fs.record(dir_no)
        vcn = self._leaf_vcn(rec, name)

        if vcn is None:
            entries = self._parse_index_entries(self._index_root(rec).value)
            remaining, target = [], None
            for e in entries:
                if self._entry_name(e).lower() == name.lower() and target is None:
                    target = struct.unpack_from("<Q", e, 0)[0] & 0xFFFFFFFFFFFF
                else:
                    remaining.append(e)
            if target is None:
                raise NtfsError(f"Bulunamadi: {name}")
            self._replace_index_root(dir_no, self._rebuild_index_root(remaining))
            return target

        alloc = rec.find(AT_INDEX_ALLOCATION, "$I30")
        block = self.fs._index_block(alloc, vcn)
        if block is None:
            raise NtfsError(f"INDX blogu okunamadi (VCN {vcn})")
        entries, tail = self._indx_entries(block)
        remaining, target = [], None
        for e in entries:
            if self._entry_name(e).lower() == name.lower() and target is None:
                target = struct.unpack_from("<Q", e, 0)[0] & 0xFFFFFFFFFFFF
            else:
                remaining.append(e)
        if target is None:
            raise NtfsError(f"Bulunamadi: {name}")
        self._rewrite_indx(alloc, vcn, remaining, tail)
        return target

    # ------------------------------------------------------------------
    # Ust duzey islemler
    # ------------------------------------------------------------------
    @staticmethod
    def _split(path: str) -> Tuple[str, str]:
        norm = "/" + "/".join(p for p in path.replace("\\", "/").split("/") if p)
        if norm == "/":
            raise NtfsError("Kok dizin uzerinde islem yapilamaz")
        i = norm.rfind("/")
        return (norm[:i] or "/"), norm[i + 1:]

    def _dir_number(self, path: str) -> int:
        rec = self.fs.resolve(path)
        if not rec.is_dir:
            raise NtfsError(f"Dizin degil: {path}")
        return rec.number

    def write_file(self, path: str, data: bytes) -> None:
        """Dosya olusturur (varsa once siler)."""
        self._require_writable()
        parent_path, name = self._split(path)
        dir_no = self._dir_number(parent_path)
        try:
            self.remove(path)
        except NtfsError:
            pass

        rec_no = self.alloc_record()
        sequence = 1
        parent_ref = (self.fs.record(dir_no).sequence << 48) | dir_no

        # Kucuk veri kayda yerlesir; buyuk veri kumelere yazilir.
        resident_limit = self.fs.record_size - 0x200
        if len(data) <= resident_limit:
            data_attr = self._resident_attr(AT_DATA, data, attr_id=2)
            runs, allocated = [], 0
        else:
            clusters = (len(data) + self.cs - 1) // self.cs
            runs = self.alloc_clusters(clusters)
            pos = 0
            for lcn, count in runs:
                span = count * self.cs
                chunk = data[pos:pos + span]
                self.dev.write(lcn * self.cs, chunk.ljust(
                    min(span, len(data) - pos), b"\x00"))
                pos += span
            allocated = clusters * self.cs
            data_attr = self._nonresident_attr(AT_DATA, runs, len(data),
                                               attr_id=2)

        attrs = [
            self._resident_attr(AT_STANDARD_INFORMATION,
                                self._std_info(0x20), attr_id=0),
            self._resident_attr(AT_FILE_NAME,
                                self._file_name_value(parent_ref, name, 0x20,
                                                      len(data), allocated),
                                attr_id=1, indexed=1),
            data_attr,
        ]
        raw = self._build_record(rec_no, sequence, MFT_FLAG_IN_USE, attrs)
        self.write_record(rec_no, raw)
        self.index_add(dir_no, name, rec_no, sequence, 0x20,
                       len(data), allocated)
        self.fs._cache.clear()

    def mkdir(self, path: str) -> None:
        """Bos bir klasor olusturur."""
        self._require_writable()
        parent_path, name = self._split(path)
        dir_no = self._dir_number(parent_path)
        rec_no = self.alloc_record()
        sequence = 1
        parent_ref = (self.fs.record(dir_no).sequence << 48) | dir_no

        attrs = [
            self._resident_attr(AT_STANDARD_INFORMATION,
                                self._std_info(FILE_ATTR_DIRECTORY), attr_id=0),
            self._resident_attr(AT_FILE_NAME,
                                self._file_name_value(parent_ref, name,
                                                      FILE_ATTR_DIRECTORY),
                                attr_id=1, indexed=1),
            self._resident_attr(AT_INDEX_ROOT, self._rebuild_index_root([]),
                                name="$I30", attr_id=2),
        ]
        raw = self._build_record(rec_no, sequence,
                                 MFT_FLAG_IN_USE | MFT_FLAG_DIRECTORY, attrs)
        self.write_record(rec_no, raw)
        self.index_add(dir_no, name, rec_no, sequence, FILE_ATTR_DIRECTORY, 0, 0)
        self.fs._cache.clear()

    def remove(self, path: str) -> None:
        """Dosyayi veya **bos** klasoru siler."""
        self._require_writable()
        parent_path, name = self._split(path)
        dir_no = self._dir_number(parent_path)
        rec = self.fs.resolve(path)
        if rec.is_dir:
            if self.fs.listdir_record(rec):
                raise NtfsError(f"Klasor bos degil: {name}")
        else:
            data = rec.find(AT_DATA)
            if data is not None and not data.resident:
                self.free_clusters(data.runs)

        target = self.index_remove(dir_no, name)
        raw = self._raw_record(target)
        flags = struct.unpack_from("<H", raw, 0x16)[0]
        struct.pack_into("<H", raw, 0x16, flags & ~MFT_FLAG_IN_USE)
        sequence = struct.unpack_from("<H", raw, 0x10)[0]
        struct.pack_into("<H", raw, 0x10, (sequence + 1) & 0xFFFF or 1)
        self.write_record(target, raw)
        self.free_record(target)
        self.fs._cache.clear()

    def rename(self, path: str, new_name: str) -> None:
        """Ayni dizin icinde yeniden adlandirir."""
        self._require_writable()
        parent_path, name = self._split(path)
        if "/" in new_name or "\\" in new_name:
            raise NtfsError("Yeni ad yol icermemeli")
        rec = self.fs.resolve(path)
        data = rec.find(AT_DATA)
        size = data.data_size if data is not None else 0
        allocated = data.allocated_size if (data and not data.resident) else 0
        file_attr = FILE_ATTR_DIRECTORY if rec.is_dir else 0x20
        dir_no = self._dir_number(parent_path)
        sequence = rec.sequence

        self.index_remove(dir_no, name)
        self.index_add(dir_no, new_name, rec.number, sequence, file_attr,
                       size, allocated)
        self.fs._cache.clear()

    def flush(self) -> None:
        f = getattr(self.dev, "flush", None)
        if f:
            f()


def _encode_runs(runs: List[Tuple[int, int]]) -> bytes:
    """(lcn, kume) listesini NTFS veri kosullarina cevirir."""
    out = bytearray()
    prev = 0
    for lcn, count in runs:
        delta = lcn - prev
        prev = lcn
        len_b = _signed_bytes(count, unsigned=True)
        off_b = _signed_bytes(delta)
        out.append((len(off_b) << 4) | len(len_b))
        out += len_b
        out += off_b
    out.append(0)
    return bytes(out)


def _signed_bytes(value: int, unsigned: bool = False) -> bytes:
    """Sayiyi en az bayt ile kodlar (NTFS veri kosulu bicimi)."""
    if value == 0:
        return b"\x00"
    length = 1
    while True:
        try:
            return value.to_bytes(length, "little", signed=not unsigned)
        except OverflowError:
            length += 1
            if length > 8:
                raise NtfsError("Veri kosulu degeri cok buyuk")
