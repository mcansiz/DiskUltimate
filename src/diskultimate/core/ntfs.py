"""Saf Python NTFS bicimlendirici (mkfs).

NTFS, desteklenen dosya sistemleri icinde en karmasik olanidir: acik bir
spesifikasyonu yoktur ve yapisi MFT kayitlari, duzeltme dizileri (fixup) ve
oznitelik akislari uzerine kuruludur. Bu modul, `mkfs.ntfs`in bulunmadigi
platformlarda (Windows, macOS) da NTFS olusturabilmek icin bicimi dogrudan
uygular.

Kapsam: **olusturma**. Okuma destegi yoktur (yol haritasinda).

Yerlesim (spec: .claude/specs/ntfs.md):
    LCN 0        $Boot (8 KiB) — onyukleme sektoru ve yedegi
    LCN 4...     $MFT
    ardindan     $MFTMirr, $LogFile, $UpCase, $AttrDef, $Bitmap, kok dizin
    son sektor   onyukleme sektorunun yedegi
"""
from __future__ import annotations

import base64
import struct
import zlib
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from .image import BlockDevice

NTFS_OEM = b"NTFS    "
FILE_MAGIC = b"FILE"
INDX_MAGIC = b"INDX"
MFT_RECORD_SIZE = 4096
INDEX_RECORD_SIZE = 4096

# --- oznitelik turleri ---
AT_STANDARD_INFORMATION = 0x10
AT_ATTRIBUTE_LIST = 0x20
AT_FILE_NAME = 0x30
AT_OBJECT_ID = 0x40
AT_SECURITY_DESCRIPTOR = 0x50
AT_VOLUME_NAME = 0x60
AT_VOLUME_INFORMATION = 0x70
AT_DATA = 0x80
AT_INDEX_ROOT = 0x90
AT_INDEX_ALLOCATION = 0xA0
AT_BITMAP = 0xB0
AT_END = 0xFFFFFFFF

# --- sistem MFT kayitlari ---
MFT_MFT = 0
MFT_MFTMIRR = 1
MFT_LOGFILE = 2
MFT_VOLUME = 3
MFT_ATTRDEF = 4
MFT_ROOT = 5
MFT_BITMAP = 6
MFT_BOOT = 7
MFT_BADCLUS = 8
MFT_SECURE = 9
MFT_UPCASE = 10
MFT_EXTEND = 11
MFT_RESERVED_COUNT = 16          # 0..15 sistem icin ayrilir

# --- dosya oznitelikleri ---
FILE_ATTR_READONLY = 0x0001
FILE_ATTR_HIDDEN = 0x0002
FILE_ATTR_SYSTEM = 0x0004
FILE_ATTR_ARCHIVE = 0x0020
FILE_ATTR_DIRECTORY = 0x10000000   # $FILE_NAME icinde kullanilan bayrak

MFT_FLAG_IN_USE = 0x0001
MFT_FLAG_DIRECTORY = 0x0002

FNAME_POSIX, FNAME_WIN32, FNAME_DOS, FNAME_WIN32_DOS = 0, 1, 2, 3

NT_EPOCH_FARKI = 11644473600      # 1601 -> 1970 saniye farki


class NtfsError(Exception):
    pass


def _nt_time(unix_saniye: float) -> int:
    """Unix zamanini NT zamanina cevirir (100 ns birimi, 1601 baslangicli)."""
    return int((unix_saniye + NT_EPOCH_FARKI) * 10_000_000)


# --------------------------------------------------------------------------
# Gomulu sabitler
# --------------------------------------------------------------------------
_ATTRDEF_B64 = ""        # format() ilk cagrildiginda doldurulur
_UPCASE_ISTISNA = ""


def _load_constants() -> None:
    """Gomulu $AttrDef ve upcase istisna tablosunu yukler."""
    global _ATTRDEF_B64, _UPCASE_ISTISNA
    if _ATTRDEF_B64:
        return
    from ._ntfs_data import ATTRDEF_B64, UPCASE_ISTISNA
    _ATTRDEF_B64 = ATTRDEF_B64
    _UPCASE_ISTISNA = UPCASE_ISTISNA


def attrdef_table() -> bytes:
    """$AttrDef icerigi (2560 bayt) — oznitelik tanimlari tablosu."""
    _load_constants()
    return zlib.decompress(base64.b64decode(_ATTRDEF_B64))


def upcase_table() -> bytes:
    """$UpCase icerigi (128 KiB): 65536 UTF-16 buyuk harf eslemesi.

    Tablo Python'un Unicode eslemesinden uretilir; Windows'un tablosundan
    ayrilan 244 giris gomulu istisna listesinden duzeltilir, boylece sonuc
    `mkfs.ntfs` ciktisiyla birebir ayni olur.
    """
    _load_constants()
    table = bytearray(65536 * 2)
    for i in range(65536):
        upper = chr(i).upper()
        value = ord(upper) if len(upper) == 1 and ord(upper) < 65536 else i
        struct.pack_into("<H", table, i * 2, value)
    for chunk in _UPCASE_ISTISNA.split(","):
        if not chunk:
            continue
        code, value = chunk.split(":")
        struct.pack_into("<H", table, int(code, 16) * 2, int(value, 16))
    return bytes(table)


# --------------------------------------------------------------------------
@dataclass
class NtfsLayout:
    cluster_size: int
    sector_size: int
    total_sectors: int
    total_clusters: int
    mft_lcn: int
    mft_clusters: int
    mftmirr_lcn: int
    mftmirr_clusters: int
    logfile_lcn: int
    logfile_clusters: int
    upcase_lcn: int
    upcase_clusters: int
    attrdef_lcn: int
    bitmap_lcn: int
    bitmap_clusters: int
    root_index_lcn: int
    secure_lcn: int


class NtfsFormatter:
    """NTFS birimi olusturur."""

    def __init__(self, dev: BlockDevice, label: str = "", cluster_size: int = 0):
        self.dev = dev
        self.label = label[:32]
        self.sector_size = dev.sector_size
        self.cluster_size = cluster_size or self._default_cluster()
        if self.cluster_size % self.sector_size:
            raise NtfsError("Kume boyutu sektor boyutunun kati olmalidir")
        import time
        self.now = _nt_time(time.time())
        import os as _os
        self.serial = int.from_bytes(_os.urandom(8), "little")
        self.layout = self._compute_layout()
        self._cluster_used: Dict[int, bool] = {}

    def _default_cluster(self) -> int:
        size = self.dev.size
        if size <= 512 * 1024 * 1024:
            return 4096
        if size <= 1024 ** 3:
            return 4096
        if size <= 2 * 1024 ** 3:
            return 4096
        return 4096          # NTFS varsayilani modern boyutlarda 4 KiB

    # ---- yerlesim ---------------------------------------------------------
    def _compute_layout(self) -> NtfsLayout:
        ss = self.sector_size
        cs = self.cluster_size
        spc = cs // ss
        total_sectors = self.dev.size // ss
        # son sektor yedek onyukleme sektoru icin ayrilir
        usable_sectors = total_sectors - 1
        total_clusters = usable_sectors // spc
        if total_clusters < 64:
            raise NtfsError("Bolum NTFS icin cok kucuk")

        def clusters(nbytes: int) -> int:
            return max(1, (nbytes + cs - 1) // cs)

        mft_record_count = 32                       # 16 sistem + yedek alan
        mft_cluster = clusters(mft_record_count * MFT_RECORD_SIZE)
        logfile_bytes = max(2 * 1024 * 1024, min(64 * 1024 * 1024,
                                                self.dev.size // 100))
        logfile_cluster = clusters(logfile_bytes)
        upcase_cluster = clusters(128 * 1024)
        attrdef_cluster = clusters(2560)
        bitmap_bytes = (total_clusters + 7) // 8
        bitmap_cluster = clusters(bitmap_bytes)

        imlec = 4                                    # $Boot 0-1, 2-3 bos birakilir
        mft_lcn = imlec
        imlec += mft_cluster
        logfile_lcn = imlec
        imlec += logfile_cluster
        upcase_lcn = imlec
        imlec += upcase_cluster
        attrdef_lcn = imlec
        imlec += attrdef_cluster
        bitmap_lcn = imlec
        imlec += bitmap_cluster
        root_index_lcn = imlec
        imlec += clusters(INDEX_RECORD_SIZE)
        secure_lcn = imlec
        imlec += 1
        if imlec >= total_clusters - 2:
            raise NtfsError("Bolum NTFS metaverisi icin yetersiz")
        # $MFTMirr ilk dort MFT kaydini tutar; kayit boyutuna gore yer ayrilir
        mftmirr_cluster = clusters(4 * MFT_RECORD_SIZE)
        mftmirr_lcn = total_clusters - mftmirr_cluster

        return NtfsLayout(
            cluster_size=cs, sector_size=ss, total_sectors=usable_sectors,
            total_clusters=total_clusters, mft_lcn=mft_lcn, mft_clusters=mft_cluster,
            mftmirr_lcn=mftmirr_lcn, mftmirr_clusters=mftmirr_cluster,
            logfile_lcn=logfile_lcn,
            logfile_clusters=logfile_cluster, upcase_lcn=upcase_lcn,
            upcase_clusters=upcase_cluster, attrdef_lcn=attrdef_lcn,
            bitmap_lcn=bitmap_lcn, bitmap_clusters=bitmap_cluster,
            root_index_lcn=root_index_lcn, secure_lcn=secure_lcn)

    # ---- dusuk seviye yardimcilar ----------------------------------------
    def _write_clusters(self, lcn: int, veri: bytes) -> None:
        self.dev.write(lcn * self.cluster_size, veri)

    def _zero_clusters(self, lcn: int, adet: int) -> None:
        cs = self.cluster_size
        chunk = max(1, (4 * 1024 * 1024) // cs)
        free = b"\x00" * (chunk * cs)
        kalan, cur = adet, lcn
        while kalan > 0:
            n = min(chunk, kalan)
            self.dev.write(cur * cs, free[: n * cs])
            cur += n
            kalan -= n

    @staticmethod
    def _apply_fixup(record: bytearray, usa_offset: int, usa_count: int,
                     usn: int, sector_size: int) -> None:
        """Duzeltme dizisini (Update Sequence Array) uygular.

        Her sektorun son iki bayti diziye tasinir, yerine USN yazilir. NTFS
        surucusu kaydin tam olarak yazildigini boyle dogrular; bu adim
        atlanirsa kayit gecersiz sayilir.
        """
        struct.pack_into("<H", record, usa_offset, usn)
        for i in range(usa_count - 1):
            last = (i + 1) * sector_size - 2
            if last + 2 > len(record):
                break
            struct.pack_into("<H", record, usa_offset + 2 + i * 2,
                             struct.unpack_from("<H", record, last)[0])
            struct.pack_into("<H", record, last, usn)

    @staticmethod
    def _data_runs(runs: List[Tuple[int, int]]) -> bytes:
        """Veri kosularini (LCN, uzunluk) esleme ciftlerine cevirir."""
        cikti = bytearray()
        onceki_lcn = 0
        for lcn, length in runs:
            fark = lcn - onceki_lcn
            length_bytes = _signed_bytes(length, unsigned=True)
            ofset_baytlari = _signed_bytes(fark)
            cikti.append((len(ofset_baytlari) << 4) | len(length_bytes))
            cikti += length_bytes
            cikti += ofset_baytlari
            onceki_lcn = lcn
        cikti.append(0)
        return bytes(cikti)

    # ---- oznitelik olusturucular -----------------------------------------
    def _attr_resident(self, kind: int, value: bytes, name: str = "",
                       indexed: int = 0, attr_id: int = 0) -> bytes:
        name_bytes = name.encode("utf-16-le")
        name_offset = 0x18
        value_offset = name_offset + len(name_bytes)
        value_offset = (value_offset + 7) & ~7
        total = value_offset + len(value)
        total = (total + 7) & ~7
        attr = bytearray(total)
        struct.pack_into("<IIBBHHH", attr, 0, kind, total, 0,
                         len(name), name_offset if name else 0, 0, attr_id)
        struct.pack_into("<IHBB", attr, 0x10, len(value), value_offset,
                         indexed, 0)
        if name_bytes:
            attr[name_offset:name_offset + len(name_bytes)] = name_bytes
        attr[value_offset:value_offset + len(value)] = value
        return bytes(attr)

    def _attr_nonresident(self, kind: int, runs: List[Tuple[int, int]],
                          data_size: int, name: str = "", attr_id: int = 0,
                          sparse: bool = False,
                          alloc_size: Optional[int] = None) -> bytes:
        name_bytes = name.encode("utf-16-le")
        name_offset = 0x40
        kosular = self._data_runs(runs) if runs else b"\x00"
        esleme_ofseti = name_offset + len(name_bytes)
        esleme_ofseti = (esleme_ofseti + 7) & ~7
        total = esleme_ofseti + len(kosular)
        total = (total + 7) & ~7
        last_vcn = (sum(u for _l, u in runs) - 1) if runs else 0
        allocated = (alloc_size if alloc_size is not None
                    else sum(u for _l, u in runs) * self.cluster_size)
        attr = bytearray(total)
        struct.pack_into("<IIBBHHH", attr, 0, kind, total, 1,
                         len(name), name_offset if name else 0,
                         0x8000 if sparse else 0, attr_id)
        struct.pack_into("<QQHHI", attr, 0x10, 0, last_vcn, esleme_ofseti, 0, 0)
        struct.pack_into("<QQQ", attr, 0x28, allocated, data_size, data_size)
        if name_bytes:
            attr[name_offset:name_offset + len(name_bytes)] = name_bytes
        attr[esleme_ofseti:esleme_ofseti + len(kosular)] = kosular
        return bytes(attr)

    def _std_info(self, file_attr: int = FILE_ATTR_HIDDEN | FILE_ATTR_SYSTEM) -> bytes:
        value = bytearray(72)
        struct.pack_into("<QQQQ", value, 0, self.now, self.now, self.now, self.now)
        struct.pack_into("<I", value, 32, file_attr)
        struct.pack_into("<I", value, 64, 0)            # security id
        return bytes(value)

    def _file_name(self, parent_ref: int, name: str, file_attr: int,
                   allocated: int = 0, size: int = 0,
                   name_type: int = FNAME_WIN32_DOS) -> bytes:
        name_bytes = name.encode("utf-16-le")
        value = bytearray(0x42 + len(name_bytes))
        struct.pack_into("<Q", value, 0, self._mref(parent_ref))
        struct.pack_into("<QQQQ", value, 8, self.now, self.now, self.now, self.now)
        struct.pack_into("<QQ", value, 0x28, allocated, size)
        struct.pack_into("<I", value, 0x38, file_attr)
        value[0x40] = len(name)
        value[0x41] = name_type
        value[0x42:] = name_bytes
        return bytes(value)

    @staticmethod
    def _sequence(rec_no: int) -> int:
        """Sistem kayitlarinda sira numarasi kayit numarasina esittir (0 -> 1).

        MFT basvurulari (mref) sira numarasini ust 16 bitte tasir; yanlis sira
        numarasi `ntfs_inode_open` tarafindan "dosya yok" olarak reddedilir.
        """
        return rec_no if rec_no else 1

    def _mref(self, rec_no: int) -> int:
        return (rec_no & 0xFFFFFFFFFFFF) | (self._sequence(rec_no) << 48)

    def _make_record(self, rec_no: int, bayraklar: int, oznitelikler: List[bytes],
                     baglanti: int = 1) -> bytes:
        record = bytearray(MFT_RECORD_SIZE)
        usa_offset = 0x30
        usa_count = MFT_RECORD_SIZE // self.sector_size + 1
        attrs_offset = (usa_offset + usa_count * 2 + 7) & ~7
        record[0:4] = FILE_MAGIC
        struct.pack_into("<HH", record, 4, usa_offset, usa_count)
        struct.pack_into("<Q", record, 8, 0)                 # $LogFile LSN
        struct.pack_into("<HHHH", record, 0x10, self._sequence(rec_no),
                         baglanti, attrs_offset, bayraklar)
        imlec = attrs_offset
        for i, attr in enumerate(oznitelikler):
            if imlec + len(attr) + 8 > MFT_RECORD_SIZE:
                raise NtfsError(f"MFT kaydi {rec_no} tasti")
            # Oznitelik kimligi kayit icinde BENZERSIZ olmalidir; elle verilen
            # degerler cakisabildigi icin burada sirayla yeniden atanir
            # (cakisma `chkdsk` tarafindan "attribute record is corrupt" olarak
            # bildirilir).
            duzeltilmis = bytearray(attr)
            struct.pack_into("<H", duzeltilmis, 0x0E, i)
            record[imlec:imlec + len(attr)] = bytes(duzeltilmis)
            imlec += len(attr)
        struct.pack_into("<I", record, imlec, AT_END)
        struct.pack_into("<I", record, imlec + 4, 0)
        kullanilan = imlec + 8
        struct.pack_into("<II", record, 0x18, kullanilan, MFT_RECORD_SIZE)
        struct.pack_into("<Q", record, 0x20, 0)              # taban kayit
        struct.pack_into("<H", record, 0x28, len(oznitelikler) + 1)
        struct.pack_into("<I", record, 0x2C, rec_no)
        self._apply_fixup(record, usa_offset, usa_count, 1, self.sector_size)
        return bytes(record)


def _signed_bytes(value: int, unsigned: bool = False) -> bytes:
    """Sayiyi en az sayida bayta sigdirir (veri kosulari icin)."""
    if value == 0:
        return b"\x00"
    if unsigned:
        length = (value.bit_length() + 8) // 8
        return value.to_bytes(length, "little")
    length = (value.bit_length() + 8) // 8
    while True:
        try:
            return value.to_bytes(length, "little", signed=True)
        except OverflowError:
            length += 1


# ==========================================================================
# Bicimlendirme akisi
# ==========================================================================
def _index_entry(mft_ref: int, key: bytes, child_node: bool = False,
                 sira: int = 1) -> bytes:
    """Dizin girisi: MFT basvurusu + $FILE_NAME anahtari."""
    length = 0x10 + len(key)
    length = (length + 7) & ~7
    entry = bytearray(length)
    struct.pack_into("<QHHH", entry, 0,
                     (mft_ref & 0xFFFFFFFFFFFF) | (sira << 48),
                     length, len(key), 0x01 if child_node else 0x00)
    entry[0x10:0x10 + len(key)] = key
    return bytes(entry)


def _index_end_entry(child_vcn: Optional[int] = None) -> bytes:
    """Dizin sonu isaretcisi; alt dugum varsa VCN eklenir."""
    length = 0x18 if child_vcn is not None else 0x10
    entry = bytearray(length)
    bayraklar = 0x02 | (0x01 if child_vcn is not None else 0x00)
    struct.pack_into("<QHHH", entry, 0, 0, length, 0, bayraklar)
    if child_vcn is not None:
        struct.pack_into("<Q", entry, length - 8, child_vcn)
    return bytes(entry)


class _NtfsBuilder(NtfsFormatter):
    """NtfsFormatter'a bicimlendirme akisini ekler."""

    SYSTEM_FILES = [
        (MFT_MFT, "$MFT"), (MFT_MFTMIRR, "$MFTMirr"), (MFT_LOGFILE, "$LogFile"),
        (MFT_VOLUME, "$Volume"), (MFT_ATTRDEF, "$AttrDef"), (MFT_ROOT, "."),
        (MFT_BITMAP, "$Bitmap"), (MFT_BOOT, "$Boot"), (MFT_BADCLUS, "$BadClus"),
        (MFT_SECURE, "$Secure"), (MFT_UPCASE, "$UpCase"), (MFT_EXTEND, "$Extend"),
    ]

    def format(self, progress: Optional[Callable[[str, int], None]] = None) -> Dict:
        L = self.layout

        def report(message: str, percent: int) -> None:
            if progress:
                progress(message, percent)

        report("NTFS yerlesimi hazirlaniyor...", 5)
        report("Sabit tablolar yaziliyor ($UpCase, $AttrDef)...", 15)
        self._write_clusters(L.upcase_lcn, upcase_table())
        self._write_clusters(L.attrdef_lcn, attrdef_table())

        report("Islem gunlugu ($LogFile) hazirlaniyor...", 30)
        self._write_logfile()

        report("Kok dizin olusturuluyor...", 45)
        self._write_root_index()

        report("Kume bitmap'i yaziliyor...", 60)
        self._write_bitmap()

        report("MFT kayitlari olusturuluyor...", 70)
        records = self._build_mft_records()
        self._write_mft(records)

        report("MFT yedegi yaziliyor...", 85)
        self._write_mftmirr(records)

        report("Onyukleme sektoru yaziliyor...", 95)
        self._write_boot()

        f = getattr(self.dev, "flush", None)
        if f:
            f()
        report("Tamamlandi", 100)
        return {
            "cluster_size": L.cluster_size, "clusters": L.total_clusters,
            "mft_lcn": L.mft_lcn, "mft_clusters": L.mft_clusters,
            "logfile_clusters": L.logfile_clusters,
            "serial": f"{self.serial:016X}", "label": self.label,
        }

    # ---- veri alanlari ---------------------------------------------------
    def _write_logfile(self) -> None:
        """$LogFile alanini 0xFF ile doldurur (bos gunluk)."""
        L = self.layout
        cs = L.cluster_size
        chunk = max(1, (4 * 1024 * 1024) // cs)
        dolgu = b"\xFF" * (chunk * cs)
        kalan, cur = L.logfile_clusters, L.logfile_lcn
        while kalan > 0:
            n = min(chunk, kalan)
            self.dev.write(cur * cs, dolgu[: n * cs])
            cur += n
            kalan -= n

    def _collation_key(self, name: str) -> List[int]:
        """$FILE_NAME siralama anahtari: $UpCase ile buyuk harfe cevrilmis kodlar.

        NTFS dizin girisleri bu sıraya gore **sirali** olmak zorundadir; sirasiz
        bir dizinde B-agaci aramasi dosyayi bulamaz ve `ntfs_pathname_to_inode`
        "dosya yok" doner.
        """
        table = upcase_table()
        return [struct.unpack_from("<H", table, ord(ch) * 2)[0] for ch in name]

    def _root_index_entries(self) -> bytes:
        """Kok dizindeki sistem dosyasi girisleri (siralama kuralina uygun)."""
        girisler = bytearray()
        sirali = sorted((k for k in self.SYSTEM_FILES if k[0] != MFT_ROOT),
                        key=lambda oge: self._collation_key(oge[1]))
        for rec_no, name in sirali:
            feature = FILE_ATTR_HIDDEN | FILE_ATTR_SYSTEM
            if rec_no == MFT_EXTEND:
                feature |= FILE_ATTR_DIRECTORY
            key = self._file_name(MFT_ROOT, name, feature)
            girisler += _index_entry(rec_no, key,
                                     sira=self._sequence(rec_no))
        girisler += _index_end_entry()
        return bytes(girisler)

    def _write_root_index(self) -> None:
        """Kok dizin icin INDX kaydi yazar (buyuk dizin)."""
        L = self.layout
        girisler = self._root_index_entries()
        record = bytearray(INDEX_RECORD_SIZE)
        usa_offset = 0x28
        usa_count = INDEX_RECORD_SIZE // L.sector_size + 1
        girisler_ofseti = (usa_offset + usa_count * 2 + 7) & ~7

        record[0:4] = INDX_MAGIC
        struct.pack_into("<HH", record, 4, usa_offset, usa_count)
        struct.pack_into("<Q", record, 8, 0)                    # LSN
        struct.pack_into("<Q", record, 0x10, 0)                 # VCN
        # INDEX_HEADER 0x18'de baslar; ofsetler bu noktaya GOREdir
        struct.pack_into("<III", record, 0x18,
                         girisler_ofseti - 0x18,
                         girisler_ofseti - 0x18 + len(girisler),
                         INDEX_RECORD_SIZE - 0x18)
        record[0x24] = 0                                        # yaprak dugum
        record[girisler_ofseti:girisler_ofseti + len(girisler)] = girisler
        self._apply_fixup(record, usa_offset, usa_count, 1, L.sector_size)
        self._write_clusters(L.root_index_lcn, bytes(record))

    def _used_clusters(self) -> List[Tuple[int, int]]:
        """Metaverinin kapladigi (lcn, adet) araliklari."""
        L = self.layout
        return [
            (0, 4),                                   # $Boot + hizalama
            (L.mft_lcn, L.mft_clusters),
            (L.logfile_lcn, L.logfile_clusters),
            (L.upcase_lcn, L.upcase_clusters),
            (L.attrdef_lcn, 1),
            (L.bitmap_lcn, L.bitmap_clusters),
            (L.root_index_lcn, max(1, INDEX_RECORD_SIZE // L.cluster_size)),
            (L.secure_lcn, 1),
            (L.mftmirr_lcn, L.mftmirr_clusters),
        ]

    def _write_bitmap(self) -> None:
        """Kume kullanim bitmap'ini yazar."""
        L = self.layout
        bitmap = bytearray((L.total_clusters + 7) // 8)
        for lcn, adet in self._used_clusters():
            for c in range(lcn, min(lcn + adet, L.total_clusters)):
                bitmap[c >> 3] |= 1 << (c & 7)
        # bitmap'in son baytindaki fazla bitler dolu isaretlenir
        for c in range(L.total_clusters, len(bitmap) * 8):
            bitmap[c >> 3] |= 1 << (c & 7)
        self._write_clusters(L.bitmap_lcn, bytes(bitmap))

    # ---- MFT kayitlari ---------------------------------------------------
    def _build_mft_records(self) -> List[bytes]:
        L = self.layout
        cs = L.cluster_size
        records: List[bytes] = []

        def system_file(rec_no: int, name: str, size: int = 0,
                      allocated: int = 0, is_dir: bool = False) -> bytes:
            feature = FILE_ATTR_HIDDEN | FILE_ATTR_SYSTEM
            if is_dir:
                feature |= FILE_ATTR_DIRECTORY
            return self._attr_resident(
                AT_FILE_NAME,
                self._file_name(MFT_ROOT, name, feature, allocated, size),
                indexed=1)

        # 0: $MFT
        mft_size = L.mft_clusters * cs
        mft_bitmap = bytearray(max(8, (32 + 7) // 8))
        for i in range(MFT_RESERVED_COUNT):
            mft_bitmap[i >> 3] |= 1 << (i & 7)
        records.append(self._make_record(MFT_MFT, MFT_FLAG_IN_USE, [
            self._attr_resident(AT_STANDARD_INFORMATION, self._std_info()),
            system_file(MFT_MFT, "$MFT", mft_size, mft_size),
            self._attr_nonresident(AT_DATA, [(L.mft_lcn, L.mft_clusters)],
                                   mft_size, attr_id=1),
            self._attr_resident(AT_BITMAP, bytes(mft_bitmap), attr_id=2),
        ]))

        # 1: $MFTMirr
        mirr_size = 4 * MFT_RECORD_SIZE
        records.append(self._make_record(MFT_MFTMIRR, MFT_FLAG_IN_USE, [
            self._attr_resident(AT_STANDARD_INFORMATION, self._std_info()),
            system_file(MFT_MFTMIRR, "$MFTMirr", mirr_size,
                      L.mftmirr_clusters * cs),
            self._attr_nonresident(AT_DATA,
                                   [(L.mftmirr_lcn, L.mftmirr_clusters)],
                                   mirr_size, attr_id=1),
        ]))

        # 2: $LogFile
        log_size = L.logfile_clusters * cs
        records.append(self._make_record(MFT_LOGFILE, MFT_FLAG_IN_USE, [
            self._attr_resident(AT_STANDARD_INFORMATION, self._std_info()),
            system_file(MFT_LOGFILE, "$LogFile", log_size, log_size),
            self._attr_nonresident(AT_DATA, [(L.logfile_lcn, L.logfile_clusters)],
                                   log_size, attr_id=1),
        ]))

        # 3: $Volume
        volume_info = struct.pack("<QBBH", 0, 3, 1, 0)   # NTFS 3.1, bayrak yok
        volume_attrs = [
            self._attr_resident(AT_STANDARD_INFORMATION, self._std_info()),
            system_file(MFT_VOLUME, "$Volume"),
        ]
        if self.label:
            volume_attrs.append(self._attr_resident(
                AT_VOLUME_NAME, self.label.encode("utf-16-le"), attr_id=1))
        volume_attrs.append(self._attr_resident(AT_VOLUME_INFORMATION,
                                                volume_info, attr_id=2))
        volume_attrs.append(self._attr_resident(AT_DATA, b"", attr_id=3))
        records.append(self._make_record(MFT_VOLUME, MFT_FLAG_IN_USE, volume_attrs))

        # 4: $AttrDef
        attrdef = attrdef_table()
        records.append(self._make_record(MFT_ATTRDEF, MFT_FLAG_IN_USE, [
            self._attr_resident(AT_STANDARD_INFORMATION, self._std_info()),
            system_file(MFT_ATTRDEF, "$AttrDef", len(attrdef), cs),
            self._attr_nonresident(AT_DATA, [(L.attrdef_lcn, 1)], len(attrdef),
                                   attr_id=1),
        ]))

        # 5: kok dizin
        records.append(self._make_record(MFT_ROOT,
                                          MFT_FLAG_IN_USE | MFT_FLAG_DIRECTORY,
                                          self._root_attrs()))

        # 6: $Bitmap
        bitmap_size = (L.total_clusters + 7) // 8
        records.append(self._make_record(MFT_BITMAP, MFT_FLAG_IN_USE, [
            self._attr_resident(AT_STANDARD_INFORMATION, self._std_info()),
            system_file(MFT_BITMAP, "$Bitmap", bitmap_size,
                      L.bitmap_clusters * cs),
            self._attr_nonresident(AT_DATA, [(L.bitmap_lcn, L.bitmap_clusters)],
                                   bitmap_size, attr_id=1),
        ]))

        # 7: $Boot
        records.append(self._make_record(MFT_BOOT, MFT_FLAG_IN_USE, [
            self._attr_resident(AT_STANDARD_INFORMATION, self._std_info()),
            system_file(MFT_BOOT, "$Boot", 8192, 4 * cs),
            self._attr_nonresident(AT_DATA, [(0, max(1, 8192 // cs))], 8192,
                                   attr_id=1),
        ]))

        # 8: $BadClus — seyrek, birim boyutunda
        volume_size = L.total_clusters * cs
        records.append(self._make_record(MFT_BADCLUS, MFT_FLAG_IN_USE, [
            self._attr_resident(AT_STANDARD_INFORMATION, self._std_info()),
            system_file(MFT_BADCLUS, "$BadClus"),
            self._attr_resident(AT_DATA, b"", attr_id=1),
            self._bad_stream(volume_size),
        ]))

        # 9: $Secure — $SDS akisi ve iki dizin ($SDH karma, $SII kimlik).
        # ntfs-3g bu iki dizini arar; yoksa birimi acamaz.
        # 0x08: kayit bir "gorunum indeksi" tasir ($SDH/$SII)
        records.append(self._make_record(MFT_SECURE, MFT_FLAG_IN_USE | 0x08, [
            self._attr_resident(AT_STANDARD_INFORMATION, self._std_info()),
            system_file(MFT_SECURE, "$Secure"),
            self._attr_nonresident(AT_DATA, [(L.secure_lcn, 1)], 0, name="$SDS",
                                   attr_id=1),
            self._attr_resident(AT_INDEX_ROOT,
                                self._small_index_root(0, 0x12),
                                name="$SDH", attr_id=2),
            self._attr_resident(AT_INDEX_ROOT,
                                self._small_index_root(0, 0x10),
                                name="$SII", attr_id=3),
        ]))

        # 10: $UpCase
        upcase_size = 128 * 1024
        records.append(self._make_record(MFT_UPCASE, MFT_FLAG_IN_USE, [
            self._attr_resident(AT_STANDARD_INFORMATION, self._std_info()),
            system_file(MFT_UPCASE, "$UpCase", upcase_size,
                      L.upcase_clusters * cs),
            self._attr_nonresident(AT_DATA, [(L.upcase_lcn, L.upcase_clusters)],
                                   upcase_size, attr_id=1),
        ]))

        # 11: $Extend (bos dizin)
        free_index = self._small_index_root()
        records.append(self._make_record(MFT_EXTEND,
                                          MFT_FLAG_IN_USE | MFT_FLAG_DIRECTORY, [
            self._attr_resident(AT_STANDARD_INFORMATION,
                                self._std_info(FILE_ATTR_HIDDEN | FILE_ATTR_SYSTEM)),
            system_file(MFT_EXTEND, "$Extend", is_dir=True),
            self._attr_resident(AT_INDEX_ROOT, free_index, name="$I30", attr_id=1),
        ]))

        # 12..15: ayrilmis kayitlar.
        # Bunlarda $FILE_NAME BULUNMAZ — kok dizinde listelenmezler. Ad eklemek
        # `chkdsk` tarafindan "Attribute record (30) is corrupt" olarak bildirilir.
        for no in range(12, MFT_RESERVED_COUNT):
            records.append(self._make_record(no, MFT_FLAG_IN_USE, [
                self._attr_resident(AT_STANDARD_INFORMATION, self._std_info()),
                self._attr_resident(AT_DATA, b"", attr_id=1),
            ]))
        return records

    def _bad_stream(self, volume_size: int) -> bytes:
        """$BadClus:$Bad — birim boyutunda, hic kume ayrilmamis akis.

        Referans bicimde bu akis **seyrek bayragi kullanmaz**; bunun yerine
        ofset alani sifir uzunlukta olan tek bir veri kosusu yazilir. Boyle bir
        kosu "tahsis edilmemis" (delik) anlamina gelir:

            02 ff 3f   ->  uzunluk alani 2 bayt (0x3fff kume), ofset alani yok

        Seyrek bayragi (0x8000) konulursa baslikta 8 baytlik `compressed_size`
        alani beklenir ve ad/esleme ofsetleri kayar; `chkdsk` bunu bozuk sayar.
        """
        L = self.layout
        last_vcn = L.total_clusters - 1
        name = "$Bad".encode("utf-16-le")
        name_offset = 0x40
        esleme_ofseti = (name_offset + len(name) + 7) & ~7
        length_bytes = _signed_bytes(L.total_clusters, unsigned=True)
        kosular = bytes([len(length_bytes)]) + length_bytes + b"\x00"
        total = (esleme_ofseti + len(kosular) + 7) & ~7
        attr = bytearray(total)
        struct.pack_into("<IIBBHHH", attr, 0, AT_DATA, total, 1,
                         4, name_offset, 0, 0)
        struct.pack_into("<QQHHI", attr, 0x10, 0, last_vcn, esleme_ofseti, 0, 0)
        struct.pack_into("<QQQ", attr, 0x28, volume_size, volume_size, 0)
        attr[name_offset:name_offset + len(name)] = name
        attr[esleme_ofseti:esleme_ofseti + len(kosular)] = kosular
        return bytes(attr)

    def _small_index_root(self, kind: int = AT_FILE_NAME,
                          siralama: int = 1) -> bytes:
        """Bos $INDEX_ROOT (alt dugum yok).

        `tur` indekslenen oznitelik turu, `siralama` karsilastirma kuralidir:
        1 = dosya adi, 0x10 = ULONG ($SII), 0x12 = guvenlik karmasi ($SDH).
        """
        L = self.layout
        girisler = _index_end_entry()
        value = bytearray(0x20 + len(girisler))
        struct.pack_into("<IIIBBBB", value, 0, kind, siralama,
                         INDEX_RECORD_SIZE,
                         max(1, INDEX_RECORD_SIZE // L.cluster_size), 0, 0, 0)
        struct.pack_into("<IIII", value, 0x10, 0x10, 0x10 + len(girisler),
                         0x10 + len(girisler), 0)
        value[0x20:] = girisler
        return bytes(value)

    def _root_attrs(self) -> List[bytes]:
        """Kok dizinin oznitelikleri.

        Tum girisler $INDEX_ROOT icine sigdigi surece ayri bir INDX kaydi
        (index allocation) olusturulmaz — bu, dizin yapisini belirgin sekilde
        basitlestirir ve NTFS icin gecerli bir bicimdir ("kucuk dizin").
        """
        L = self.layout
        girisler = self._root_index_entries()
        root_value = bytearray(0x20 + len(girisler))
        struct.pack_into("<IIIBBBB", root_value, 0, AT_FILE_NAME, 1,
                         INDEX_RECORD_SIZE,
                         max(1, INDEX_RECORD_SIZE // L.cluster_size), 0, 0, 0)
        struct.pack_into("<IIII", root_value, 0x10, 0x10, 0x10 + len(girisler),
                         0x10 + len(girisler), 0)     # bayrak 0 = kucuk dizin
        root_value[0x20:] = girisler

        return [
            self._attr_resident(AT_STANDARD_INFORMATION,
                                self._std_info(FILE_ATTR_HIDDEN | FILE_ATTR_SYSTEM)),
            self._attr_resident(
                AT_FILE_NAME,
                self._file_name(MFT_ROOT, ".",
                                FILE_ATTR_HIDDEN | FILE_ATTR_SYSTEM
                                | FILE_ATTR_DIRECTORY),
                indexed=1),
            self._attr_resident(AT_INDEX_ROOT, bytes(root_value), name="$I30",
                                attr_id=1),
        ]

    def _write_mft(self, records: List[bytes]) -> None:
        L = self.layout
        veri = bytearray(L.mft_clusters * L.cluster_size)
        for i, record in enumerate(records):
            ofset = i * MFT_RECORD_SIZE
            if ofset + MFT_RECORD_SIZE > len(veri):
                raise NtfsError("MFT alani yetersiz")
            veri[ofset:ofset + MFT_RECORD_SIZE] = record
        self._write_clusters(L.mft_lcn, bytes(veri))

    def _write_mftmirr(self, records: List[bytes]) -> None:
        """Ilk dort MFT kaydinin yedegi."""
        L = self.layout
        veri = bytearray(L.mftmirr_clusters * L.cluster_size)
        for i, record in enumerate(records[:4]):
            veri[i * MFT_RECORD_SIZE:(i + 1) * MFT_RECORD_SIZE] = record
        self._write_clusters(L.mftmirr_lcn, bytes(veri))

    # ---- onyukleme sektoru ------------------------------------------------
    def _boot_sector(self) -> bytes:
        L = self.layout
        ss = L.sector_size
        boot = bytearray(ss)
        boot[0:3] = b"\xEB\x52\x90"
        boot[3:11] = NTFS_OEM
        struct.pack_into("<HBHBHHBHHHII", boot, 11,
                         ss, L.cluster_size // ss, 0, 0, 0, 0,
                         0xF8, 0, 63, 255, 0, 0)
        struct.pack_into("<I", boot, 0x24, 0x00800080)      # kullanilmiyor
        struct.pack_into("<Q", boot, 0x28, L.total_sectors)
        struct.pack_into("<Q", boot, 0x30, L.mft_lcn)
        struct.pack_into("<Q", boot, 0x38, L.mftmirr_lcn)
        # pozitif: kume cinsinden, negatif: 2^|n| bayt
        if MFT_RECORD_SIZE >= L.cluster_size:
            struct.pack_into("<b", boot, 0x40, MFT_RECORD_SIZE // L.cluster_size)
        else:
            struct.pack_into("<b", boot, 0x40,
                             -(MFT_RECORD_SIZE.bit_length() - 1))
        struct.pack_into("<b", boot, 0x44,
                         1 if L.cluster_size <= INDEX_RECORD_SIZE else -12)
        struct.pack_into("<Q", boot, 0x48, self.serial)
        struct.pack_into("<H", boot, ss - 2, 0xAA55)
        return bytes(boot)

    def _write_boot(self) -> None:
        L = self.layout
        boot = self._boot_sector()
        # $Boot alani 8 KiB'dir; ilk sektor onyukleme sektorudur
        region = bytearray(max(8192, L.cluster_size))
        region[0:len(boot)] = boot
        self._write_clusters(0, bytes(region))
        # yedek: birimin son sektoru (toplam_sektor konumunda)
        self.dev.write(L.total_sectors * L.sector_size, boot)


def format_ntfs(dev: BlockDevice, label: str = "", cluster_size: int = 0,
                progress: Optional[Callable[[str, int], None]] = None) -> Dict:
    """Kisayol: verilen aygiti NTFS olarak bicimlendirir."""
    return _NtfsBuilder(dev, label=label, cluster_size=cluster_size).format(progress)
