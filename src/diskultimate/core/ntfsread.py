"""NTFS **okuyucu** — saf Python, salt okunur.

Bicimlendirme `ntfs.py` icindedir; bu modul var olan bir NTFS birimini okur:
dizin listeleme, dosya icerigi, oznitelikler.

## NTFS'i okumak neden cok adimli

Her sey `$MFT` icindeki **FILE kayitlari**dir; dosya adi da, veri de, dizin
indeksi de birer **oznitelik**tir. Bir dosyayi okumak icin sirayla:

    onyukleme sektoru -> $MFT yeri -> FILE kaydi -> fixup -> oznitelikler
      -> $DATA (yerlesik veya veri kosulari) -> kume zinciri -> bayt

Dizinler B+ agacidir: kucuk dizin `$INDEX_ROOT` icinde yerlesiktir, buyuyunce
`$INDEX_ALLOCATION` icindeki **INDX** bloklarina tasar ve agac dolasilir.

Cok parcali dosya/dizinlerde oznitelikler tek kayda sigmaz; `$ATTRIBUTE_LIST`
onlari baska kayitlara dagitir ve bu modul o baglantiyi izler.

Her `FILE` ve `INDX` blogu **fixup dizisi** tasir: her sektorun son iki bayti
kayit basindaki diziye tasinmistir ve okurken geri konmalidir. Yapilmazsa
veri sessizce bozuk okunur.
"""
from __future__ import annotations

import datetime
import struct
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple

from .image import BlockDevice
from ..i18n import tr

FILE_MAGIC = b"FILE"
INDX_MAGIC = b"INDX"

AT_STANDARD_INFORMATION = 0x10
AT_ATTRIBUTE_LIST = 0x20
AT_FILE_NAME = 0x30
AT_DATA = 0x80
AT_INDEX_ROOT = 0x90
AT_INDEX_ALLOCATION = 0xA0
AT_BITMAP = 0xB0
AT_END = 0xFFFFFFFF

MFT_FLAG_IN_USE = 0x0001
MFT_FLAG_DIRECTORY = 0x0002

FILE_ATTR_READONLY = 0x0001
FILE_ATTR_HIDDEN = 0x0002
FILE_ATTR_SYSTEM = 0x0004
FILE_ATTR_DIRECTORY = 0x10000000

ATTR_COMPRESSED = 0x0001
ATTR_ENCRYPTED = 0x4000
ATTR_SPARSE = 0x8000

INDEX_ENTRY_NODE = 0x0001      # alt dugum var
INDEX_ENTRY_END = 0x0002       # son giris (anahtar yok)

NAME_DOS = 2                   # 8.3 kisa ad — listelemede atlanir

MFT_RECORD_ROOT = 5            # kok dizin


# Bayt -> icindeki 1 bit sayisi. `bytes.translate` ile milyonlarca bayt
# tek cagrida sayilir (bkz. NtfsFS.used_bytes).
_POPCOUNT = bytes(bin(i).count("1") for i in range(256))


class NtfsError(Exception):
    """NTFS okumasiyla ilgili hatalar."""


def _utf16(data: bytes) -> str:
    return data.decode("utf-16-le", "replace")


def _filetime(value: int) -> Optional[datetime.datetime]:
    """Windows FILETIME (1601'den beri 100 ns) -> datetime."""
    if not value:
        return None
    try:
        return (datetime.datetime(1601, 1, 1)
                + datetime.timedelta(microseconds=value // 10))
    except (OverflowError, OSError, ValueError):
        return None


@dataclass
class Attribute:
    kind: int
    name: str
    resident: bool
    flags: int
    value: bytes = b""                       # yerlesikse icerik
    start_vcn: int = 0
    last_vcn: int = 0
    data_size: int = 0
    allocated_size: int = 0
    initialized_size: int = 0
    runs: List[Tuple[int, int]] = field(default_factory=list)  # (lcn, kume)

    @property
    def sparse_or_compressed(self) -> bool:
        return bool(self.flags & (ATTR_COMPRESSED | ATTR_SPARSE))


@dataclass
class NtfsEntry:
    """Bir dizin girisi."""
    name: str
    mft_ref: int
    is_dir: bool
    size: int
    attributes: int
    mtime: Optional[datetime.datetime] = None

    @property
    def is_hidden(self) -> bool:
        return bool(self.attributes & FILE_ATTR_HIDDEN)

    @property
    def is_readonly(self) -> bool:
        return bool(self.attributes & FILE_ATTR_READONLY)

    @property
    def is_system(self) -> bool:
        return bool(self.attributes & FILE_ATTR_SYSTEM)


class MftRecord:
    """Tek bir FILE kaydi ve oznitelikleri."""

    def __init__(self, number: int, raw: bytes, fs: "NtfsFS"):
        self.number = number
        self.raw = raw
        self.fs = fs
        if raw[:4] != FILE_MAGIC:
            raise NtfsError(tr("FILE imzasi yok: kayit {}", number))
        (self.sequence, self.link_count, self.attrs_offset,
         self.flags) = struct.unpack_from("<HHHH", raw, 0x10)
        self.base_ref = struct.unpack_from("<Q", raw, 0x20)[0]
        self.attributes: List[Attribute] = list(self._parse_attributes())

    @property
    def in_use(self) -> bool:
        return bool(self.flags & MFT_FLAG_IN_USE)

    @property
    def is_dir(self) -> bool:
        return bool(self.flags & MFT_FLAG_DIRECTORY)

    # ------------------------------------------------------------------
    def _parse_attributes(self) -> Iterator[Attribute]:
        pos = self.attrs_offset
        raw = self.raw
        while pos + 8 <= len(raw):
            kind = struct.unpack_from("<I", raw, pos)[0]
            if kind == AT_END:
                break
            length = struct.unpack_from("<I", raw, pos + 4)[0]
            if length < 16 or pos + length > len(raw):
                break                        # bozuk kayit: daha ileri gitme
            yield self._parse_one(raw, pos, kind, length)
            pos += length

    def _parse_one(self, raw: bytes, pos: int, kind: int, length: int) -> Attribute:
        non_resident = raw[pos + 8]
        name_len = raw[pos + 9]
        name_off = struct.unpack_from("<H", raw, pos + 0x0A)[0]
        flags = struct.unpack_from("<H", raw, pos + 0x0C)[0]
        name = _utf16(raw[pos + name_off:pos + name_off + name_len * 2]) \
            if name_len else ""

        if not non_resident:
            value_len, value_off = struct.unpack_from("<IH", raw, pos + 0x10)
            value = bytes(raw[pos + value_off:pos + value_off + value_len])
            return Attribute(kind=kind, name=name, resident=True, flags=flags,
                             value=value, data_size=value_len)

        start_vcn, last_vcn = struct.unpack_from("<QQ", raw, pos + 0x10)
        mapping_off = struct.unpack_from("<H", raw, pos + 0x20)[0]
        allocated, data_size, initialized = struct.unpack_from(
            "<QQQ", raw, pos + 0x28)
        runs = _decode_runs(raw[pos + mapping_off:pos + length])
        return Attribute(kind=kind, name=name, resident=False, flags=flags,
                         start_vcn=start_vcn, last_vcn=last_vcn,
                         data_size=data_size, allocated_size=allocated,
                         initialized_size=initialized, runs=runs)

    # ------------------------------------------------------------------
    def find(self, kind: int, name: str = "") -> Optional[Attribute]:
        for a in self.all_attributes():
            if a.kind == kind and a.name == name:
                return a
        return None

    def find_all(self, kind: int) -> List[Attribute]:
        return [a for a in self.all_attributes() if a.kind == kind]

    def all_attributes(self) -> List[Attribute]:
        """Bu kaydin ve `$ATTRIBUTE_LIST` ile bagli kayitlarin oznitelikleri.

        Buyuk dosya ve dizinlerde oznitelikler tek FILE kaydina sigmaz; NTFS
        onlari baska kayitlara dagitir ve `$ATTRIBUTE_LIST` nerede olduklarini
        soyler. Bu baglanti izlenmezse buyuk dizinler bos gorunur.
        """
        attrs = self.attributes
        alist = next((a for a in attrs if a.kind == AT_ATTRIBUTE_LIST), None)
        if alist is None:
            return attrs
        out = list(attrs)
        seen = {self.number}
        data = (alist.value if alist.resident
                else self.fs.read_attribute(alist))
        pos = 0
        while pos + 26 <= len(data):
            kind, entry_len = struct.unpack_from("<IH", data, pos)
            if entry_len < 26:
                break
            ref = struct.unpack_from("<Q", data, pos + 0x10)[0] & 0xFFFFFFFFFFFF
            if ref not in seen:
                seen.add(ref)
                try:
                    baska = self.fs.record(ref)
                    out.extend(a for a in baska.attributes
                               if a.kind != AT_ATTRIBUTE_LIST)
                except NtfsError:
                    pass
            pos += entry_len
        return out


def _decode_runs(data: bytes) -> List[Tuple[int, int]]:
    """Veri kosullarini (runlist) (lcn, kume_sayisi) listesine cevirir.

    Her kosul bir basliksal bayt ile baslar: dusuk yari uzunluk alaninin,
    yuksek yari ofset alaninin bayt sayisidir. Ofset **isaretli** ve bir
    oncekine **gorelidir**; 0 ofset "seyrek" anlamina gelir ve `lcn = -1`
    olarak isaretlenir.
    """
    out: List[Tuple[int, int]] = []
    pos = 0
    lcn = 0
    while pos < len(data):
        head = data[pos]
        if head == 0:
            break
        len_size = head & 0x0F
        off_size = (head >> 4) & 0x0F
        pos += 1
        if len_size == 0 or pos + len_size + off_size > len(data):
            break
        # Uzunluk ISARETLI okunur (Windows, ntfs-3g); negatifse kosu listesi
        # bozuktur. Eskiden isaretsiz okunuyordu: kendi boyutlandiricimizin
        # yanlis kodladigi uzunlugu kabul edip hatayi gizliyordu (ADR 0085).
        count = int.from_bytes(data[pos:pos + len_size], "little", signed=True)
        if count <= 0:
            raise NtfsError(tr("Bozuk veri kosulu: uzunluk {}", count))
        pos += len_size
        if off_size == 0:
            out.append((-1, count))          # seyrek alan: diskte yer kaplamaz
        else:
            delta = int.from_bytes(data[pos:pos + off_size], "little", signed=True)
            pos += off_size
            lcn += delta
            out.append((lcn, count))
    return out


class NtfsFS:
    """NTFS birimini salt okunur acar."""

    def __init__(self, device: BlockDevice):
        self.dev = device
        self._read_boot()
        self._cache: Dict[int, MftRecord] = {}
        self._mft_runs: List[Tuple[int, int]] = []
        self._load_mft()

    # ------------------------------------------------------------------
    def _read_boot(self) -> None:
        boot = self.dev.read(0, 512)
        if len(boot) < 512 or boot[3:11] != b"NTFS    ":
            raise NtfsError(tr("NTFS imzasi yok"))
        self.sector_size = struct.unpack_from("<H", boot, 0x0B)[0]
        spc = boot[0x0D]
        # 0x80..0xFF arasi deger 2'nin kuvveti olarak yorumlanir (buyuk kumeler)
        self.sectors_per_cluster = spc if spc <= 0x80 else 1 << (256 - spc)
        self.cluster_size = self.sector_size * self.sectors_per_cluster
        self.total_sectors = struct.unpack_from("<Q", boot, 0x28)[0]
        self.mft_lcn = struct.unpack_from("<Q", boot, 0x30)[0]
        self.mftmirr_lcn = struct.unpack_from("<Q", boot, 0x38)[0]
        self.record_size = self._sized(boot[0x40])
        self.index_size = self._sized(boot[0x44])
        self.serial = struct.unpack_from("<Q", boot, 0x48)[0]
        if self.cluster_size <= 0 or self.record_size <= 0:
            raise NtfsError(tr("Onyukleme sektoru degerleri tutarsiz"))

    def _sized(self, value: int) -> int:
        """`clusters_per_*` alani: pozitifse kume, negatifse 2^(-deger) bayt."""
        signed = value - 256 if value > 127 else value
        return (1 << -signed) if signed < 0 else signed * self.cluster_size

    # ------------------------------------------------------------------
    def _load_mft(self) -> None:
        """$MFT'nin kendi kaydini okuyup kume zincirini cikarir."""
        offset = self.mft_lcn * self.cluster_size
        raw = bytearray(self.dev.read(offset, self.record_size))
        self._apply_fixup(raw)
        rec = MftRecord(0, bytes(raw), self)
        data = rec.find(AT_DATA)
        if data is None or data.resident:
            raise NtfsError(tr("$MFT veri oznitelugu okunamadi"))
        self._mft_runs = data.runs
        self.mft_size = data.data_size
        self._cache[0] = rec

    def _apply_fixup(self, raw: bytearray) -> None:
        """Guncelleme dizisini geri koyar (FILE ve INDX bloklari icin).

        Her sektorun son iki bayti kayit basindaki diziye tasinmistir; yerine
        bir imza konmustur. Geri konmazsa veri sessizce bozuk okunur.
        """
        if len(raw) < 8:
            return
        usa_off, usa_count = struct.unpack_from("<HH", raw, 4)
        if usa_count == 0 or usa_off + usa_count * 2 > len(raw):
            return
        marker = raw[usa_off:usa_off + 2]
        for i in range(1, usa_count):
            last = i * self.sector_size - 2
            if last + 2 > len(raw):
                break
            if bytes(raw[last:last + 2]) != bytes(marker):
                raise NtfsError(tr("Fixup imzasi tutmuyor (kayit bozuk)"))
            raw[last:last + 2] = raw[usa_off + i * 2:usa_off + i * 2 + 2]

    # ------------------------------------------------------------------
    def _run_read(self, runs: List[Tuple[int, int]], offset: int,
                  length: int) -> bytes:
        """Kume zincirinden bayt araligi okur (seyrek alanlar sifir doner)."""
        out = bytearray()
        cs = self.cluster_size
        pos = 0
        for lcn, count in runs:
            span = count * cs
            if pos + span <= offset:
                pos += span
                continue
            inside = max(0, offset - pos)
            take = min(span - inside, length - len(out))
            if lcn < 0:
                out += b"\x00" * take
            else:
                out += self.dev.read(lcn * cs + inside, take)
            pos += span
            if len(out) >= length:
                break
        return bytes(out[:length])

    def attribute_size(self, attr: Attribute) -> int:
        """Oznitelugun icerik boyutu (yerlesik olsun olmasin)."""
        return len(attr.value) if attr.resident else attr.data_size

    def read_attribute_range(self, attr: Attribute, offset: int,
                             length: int) -> bytes:
        """Oznitelugun yalnizca [offset, offset+length) araligini okur.

        `$Bitmap` gibi cok buyuk ozniteliklerde tek bir bit degistirmek icin
        **tamamini** okumak gerekmez: 58 GB'lik bir bolumde kume bitmap'i
        1.9 MB'dir ve her tahsiste bastan sona okunuyordu (ADR 0024).
        """
        if length <= 0:
            return b""
        if attr.resident:
            return bytes(attr.value[offset:offset + length])
        if attr.flags & ATTR_COMPRESSED:
            raise NtfsError(tr("Sikistirilmis NTFS akisi bu surumde okunamaz"))
        end = min(offset + length, attr.data_size)
        if end <= offset:
            return b""
        data = self._run_read(attr.runs, offset, end - offset)
        # initialized_size sonrasi tanimsizdir, sifir okunur
        if attr.initialized_size < end:
            valid = max(0, attr.initialized_size - offset)
            data = data[:valid].ljust(end - offset, b"\x00")
        return data

    def read_attribute(self, attr: Attribute, max_bytes: int = -1) -> bytes:
        """Bir oznitelugun icerigini dondurur."""
        if attr.resident:
            return attr.value if max_bytes < 0 else attr.value[:max_bytes]
        if attr.flags & ATTR_COMPRESSED:
            raise NtfsError(
                tr("Sikistirilmis NTFS akisi bu surumde okunamaz"))
        size = attr.data_size if max_bytes < 0 else min(attr.data_size, max_bytes)
        data = self._run_read(attr.runs, 0, size)
        # initialized_size sonrasi tanimsizdir, sifir okunur
        if attr.initialized_size < size:
            data = data[:attr.initialized_size].ljust(size, b"\x00")
        return data

    # ------------------------------------------------------------------
    def record(self, number: int) -> MftRecord:
        """MFT kaydini okur (fixup uygulanmis)."""
        hit = self._cache.get(number)
        if hit is not None:
            return hit
        offset = number * self.record_size
        raw = bytearray(self._run_read(self._mft_runs, offset, self.record_size))
        if len(raw) < self.record_size:
            raise NtfsError(tr("MFT kaydi okunamadi: {}", number))
        self._apply_fixup(raw)
        rec = MftRecord(number, bytes(raw), self)
        self._cache[number] = rec
        return rec

    # ------------------------------------------------------------------
    # Dizinler
    # ------------------------------------------------------------------
    def listdir_record(self, rec: MftRecord) -> List[NtfsEntry]:
        """Bir dizin kaydinin girislerini dondurur."""
        if not rec.is_dir:
            raise NtfsError(tr("Dizin degil"))
        root = rec.find(AT_INDEX_ROOT, "$I30")
        if root is None:
            return []
        value = root.value
        if len(value) < 0x20:
            return []
        header_off = 0x10
        entries_off, index_len, _alloc, flags = struct.unpack_from(
            "<IIIB", value, header_off)
        out: List[NtfsEntry] = []
        child_vcns: List[int] = []
        self._walk_entries(value, header_off + entries_off,
                           header_off + index_len, out, child_vcns)

        if flags & 1 and child_vcns:           # buyuk dizin: INDX bloklarini gez
            alloc = rec.find(AT_INDEX_ALLOCATION, "$I30")
            if alloc is not None:
                seen = set()
                stack = list(child_vcns)
                while stack:
                    vcn = stack.pop()
                    if vcn in seen:
                        continue
                    seen.add(vcn)
                    block = self._index_block(alloc, vcn)
                    if block is None:
                        continue
                    e_off, e_len = struct.unpack_from("<II", block, 0x18)
                    self._walk_entries(block, 0x18 + e_off, 0x18 + e_len,
                                       out, stack)
        return out

    def _index_block(self, alloc: Attribute, vcn: int) -> Optional[bytearray]:
        # Indeks blogu kumeden kucukse (orn. 64K kume, 4K indeks) VCN 512
        # baytlik birimle sayilir; eskiden hep kume boyutuyla carpiliyordu.
        unit = self.cluster_size if self.index_size >= self.cluster_size else 512
        offset = vcn * unit
        raw = bytearray(self._run_read(alloc.runs, offset, self.index_size))
        if len(raw) < self.index_size or raw[:4] != INDX_MAGIC:
            return None
        try:
            self._apply_fixup(raw)
        except NtfsError:
            return None
        return raw

    def _walk_entries(self, buf: bytes, pos: int, end: int,
                      out: List[NtfsEntry], children: List[int]) -> None:
        """Bir indeks dugumundeki girisleri toplar."""
        while pos + 0x10 <= min(end, len(buf)):
            mft_ref, entry_len, key_len, flags = struct.unpack_from(
                "<QHHH", buf, pos)
            if entry_len < 0x10:
                break
            if flags & INDEX_ENTRY_NODE and pos + entry_len <= len(buf):
                children.append(struct.unpack_from(
                    "<Q", buf, pos + entry_len - 8)[0])
            if flags & INDEX_ENTRY_END:
                break
            if key_len >= 0x42:
                entry = self._entry_from_key(buf, pos + 0x10, mft_ref)
                if entry is not None:
                    out.append(entry)
            pos += entry_len

    @staticmethod
    def _entry_from_key(buf: bytes, pos: int, mft_ref: int) -> Optional[NtfsEntry]:
        """`$FILE_NAME` anahtarindan dizin girisi uretir."""
        if pos + 0x42 > len(buf):
            return None
        mtime = struct.unpack_from("<Q", buf, pos + 0x18)[0]
        data_size = struct.unpack_from("<Q", buf, pos + 0x30)[0]
        attributes = struct.unpack_from("<I", buf, pos + 0x38)[0]
        name_len = buf[pos + 0x40]
        name_type = buf[pos + 0x41]
        if name_type == NAME_DOS:
            return None                      # 8.3 kisa ad: uzun ad zaten var
        name = _utf16(buf[pos + 0x42:pos + 0x42 + name_len * 2])
        if name == ".":
            return None                      # kok dizinin kendine gonderimi
        if name.startswith("$") and (mft_ref & 0xFFFFFFFFFFFF) < 16:
            return None                      # $MFT, $Bitmap gibi sistem dosyalari
        return NtfsEntry(name=name, mft_ref=mft_ref & 0xFFFFFFFFFFFF,
                         is_dir=bool(attributes & FILE_ATTR_DIRECTORY),
                         size=data_size, attributes=attributes,
                         mtime=_filetime(mtime))

    # ------------------------------------------------------------------
    # Yol cozumleme ve okuma
    # ------------------------------------------------------------------
    # --- siralama: birimin kendi $UpCase tablosu ----------------------
    @property
    def upcase(self) -> bytes:
        """Birimin `$UpCase` tablosu (kayit 10). Okunamazsa standart tablo.

        Dizin indeksi bu tabloya gore siralidir; eski Windows surumlerinin
        tablosu bazi Unicode karakterlerde standarttan farklidir. Uretilmis
        tabloyla siralamak o birimlerde yanlis yaprak/yanlis sira demektir.
        """
        if getattr(self, "_upcase_cache", None) is None:
            table = b""
            try:
                attr = self.record(10).find(AT_DATA, "")
                if attr is not None:
                    table = bytes(self.read_attribute(attr))
            except NtfsError:
                table = b""
            if len(table) < 2 * 0x10000:
                from .ntfs import upcase_table
                table = upcase_table()
            self._upcase_cache = table
        return self._upcase_cache

    def collation_key(self, name: str) -> List[int]:
        table = self.upcase
        out = []
        for ch in name:
            code = ord(ch)
            if code < 0x10000:
                out.append(struct.unpack_from("<H", table, code * 2)[0])
            else:                               # vekil cift: iki birim
                v = code - 0x10000
                out.extend((0xD800 + (v >> 10), 0xDC00 + (v & 0x3FF)))
        return out

    def lookup(self, rec: MftRecord, name: str) -> Optional[NtfsEntry]:
        """Dizinde adi B+ agaci uzerinden arar — O(log n).

        Eskiden her yol bileseni icin dizinin TAMAMI listeleniyordu; buyuk
        dizine dosya yazmak dosya basina ~15 ms'ye cikiyordu (olculdu).
        """
        root = rec.find(AT_INDEX_ROOT, "$I30")
        if root is None:
            return None
        want = self.collation_key(name)
        alloc = rec.find(AT_INDEX_ALLOCATION, "$I30")
        buf, base = root.value, 0x10
        for _guard in range(64):
            e_off, e_len = struct.unpack_from("<II", buf, base)
            pos, end = base + e_off, base + e_len
            child = None
            while pos + 0x10 <= min(end, len(buf)):
                mft_ref, length, key_len, flags = struct.unpack_from("<QHHH", buf, pos)
                if length < 0x10:
                    return None
                child = struct.unpack_from("<Q", buf, pos + length - 8)[0] \
                    if flags & INDEX_ENTRY_NODE else None
                if flags & INDEX_ENTRY_END:
                    break
                n = buf[pos + 0x10 + 0x40]
                key_name = _utf16(buf[pos + 0x10 + 0x42:pos + 0x10 + 0x42 + n * 2])
                have = self.collation_key(key_name)
                if want == have:
                    entry = self._entry_from_key(buf, pos + 0x10, mft_ref)
                    if entry is None:        # 8.3 DOS adi: uzun adli kardesini ara
                        return self._lookup_linear(rec, name)
                    return entry
                if want < have:
                    break
                pos += length
            if child is None or alloc is None:
                return None
            block = self._index_block(alloc, child)
            if block is None:
                return self._lookup_linear(rec, name)
            buf, base = block, 0x18
        return self._lookup_linear(rec, name)

    def _lookup_linear(self, rec: MftRecord, name: str) -> Optional[NtfsEntry]:
        want = self.collation_key(name)
        for e in self.listdir_record(rec):
            if self.collation_key(e.name) == want:
                return e
        return None

    def resolve(self, path: str) -> MftRecord:
        rec = self.record(MFT_RECORD_ROOT)
        for part in [p for p in path.replace("\\", "/").split("/") if p]:
            if not rec.is_dir:
                raise NtfsError(tr("Dizin degil: {}", part))
            target = self.lookup(rec, part)
            if target is None:
                raise NtfsError(tr("Bulunamadi: {}", path))
            rec = self.record(target.mft_ref)
        return rec

    def listdir(self, path: str = "/") -> List[NtfsEntry]:
        return self.listdir_record(self.resolve(path))

    def iter_file(self, path: str, chunk: int = 4 * 1024 * 1024):
        """Dosyanin $DATA akisini parca parca verir (ADR 0081)."""
        rec = self.resolve(path)
        if rec.is_dir:
            raise NtfsError(tr("Klasor okunamaz"))
        data = rec.find(AT_DATA, "")
        if data is None:
            return
        if data.flags & ATTR_COMPRESSED:
            yield self.read_attribute(data)     # sikistirilmis: eski yol
            return
        size = self.attribute_size(data)
        offset = 0
        while offset < size:
            n = min(chunk, size - offset)
            yield self.read_attribute_range(data, offset, n)
            offset += n

    def read_file(self, path: str, max_bytes: int = -1) -> bytes:
        rec = self.resolve(path)
        if rec.is_dir:
            raise NtfsError(tr("Klasor okunamaz"))
        data = rec.find(AT_DATA, "")
        if data is None:
            return b""
        return self.read_attribute(data, max_bytes)

    # ------------------------------------------------------------------
    @property
    def label(self) -> str:
        try:
            rec = self.record(3)             # $Volume
            attr = rec.find(0x60)            # $VOLUME_NAME
            return _utf16(attr.value) if attr else ""
        except NtfsError:
            return ""

    @property
    def cluster_count(self) -> int:
        """Birimdeki kume sayisi ($Bitmap'te anlami olan bit sayisi)."""
        return self.total_sectors // self.sectors_per_cluster

    def used_bytes(self) -> int:
        """$Bitmap'teki dolu kumelerin toplami; okunamazsa -1.

        -1 "bilinmiyor" demektir, "bos" degil: doluluk cubugu ancak gercek
        bir olcum varken cizilir.

        Yalnizca kume sayisi kadar bit sayilir. $Bitmap'in son baytlari birim
        disinda kalan bitleri tasir; Windows onlari dolu isaretler, sayiya
        katilirsa birim oldugundan dolu gorunur.
        """
        need = (self.cluster_count + 7) // 8
        if need <= 0:
            return -1
        try:
            bitmap = self.record(6).find(AT_DATA)   # $Bitmap
            if bitmap is None:
                return -1
            data = self.read_attribute(bitmap, need)
        except Exception:
            return -1
        if len(data) < need:
            return -1
        # Bayt basina bit sayimi tabloyla yapilir: 200 GB'lik bir birimde
        # bitmap 6 MB'tir, bayt basina `bin(b).count()` saniyeler surerdi.
        used = sum(data[:need - 1].translate(_POPCOUNT))
        last = data[need - 1]
        tail_bits = self.cluster_count - (need - 1) * 8
        used += _POPCOUNT[last & ((1 << tail_bits) - 1)]
        return used * self.cluster_size

    def stats(self) -> Dict[str, int]:
        total = self.total_sectors * self.sector_size
        used = self.used_bytes()
        if used < 0:
            return {"total_bytes": total, "used_bytes": -1, "free_bytes": -1,
                    "cluster_size": self.cluster_size}
        used = min(used, total)
        return {"total_bytes": total, "used_bytes": used,
                "free_bytes": total - used, "cluster_size": self.cluster_size}


def detect_ntfs(device: BlockDevice) -> bool:
    try:
        boot = device.read(0, 16)
        return len(boot) >= 11 and boot[3:11] == b"NTFS    "
    except Exception:
        return False
