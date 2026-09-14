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
    tablo = bytearray(65536 * 2)
    for i in range(65536):
        ust = chr(i).upper()
        deger = ord(ust) if len(ust) == 1 and ord(ust) < 65536 else i
        struct.pack_into("<H", tablo, i * 2, deger)
    for parca in _UPCASE_ISTISNA.split(","):
        if not parca:
            continue
        kod, deger = parca.split(":")
        struct.pack_into("<H", tablo, int(kod, 16) * 2, int(deger, 16))
    return bytes(tablo)


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
        boyut = self.dev.size
        if boyut <= 512 * 1024 * 1024:
            return 4096
        if boyut <= 1024 ** 3:
            return 4096
        if boyut <= 2 * 1024 ** 3:
            return 4096
        return 4096          # NTFS varsayilani modern boyutlarda 4 KiB

    # ---- yerlesim ---------------------------------------------------------
    def _compute_layout(self) -> NtfsLayout:
        ss = self.sector_size
        cs = self.cluster_size
        spc = cs // ss
        toplam_sektor = self.dev.size // ss
        # son sektor yedek onyukleme sektoru icin ayrilir
        kullanilabilir_sektor = toplam_sektor - 1
        toplam_kume = kullanilabilir_sektor // spc
        if toplam_kume < 64:
            raise NtfsError("Bolum NTFS icin cok kucuk")

        def kume(bayt: int) -> int:
            return max(1, (bayt + cs - 1) // cs)

        mft_kayit_sayisi = 32                       # 16 sistem + yedek alan
        mft_kume = kume(mft_kayit_sayisi * MFT_RECORD_SIZE)
        logfile_bayt = max(2 * 1024 * 1024, min(64 * 1024 * 1024,
                                                self.dev.size // 100))
        logfile_kume = kume(logfile_bayt)
        upcase_kume = kume(128 * 1024)
        attrdef_kume = kume(2560)
        bitmap_bayt = (toplam_kume + 7) // 8
        bitmap_kume = kume(bitmap_bayt)

        imlec = 4                                    # $Boot 0-1, 2-3 bos birakilir
        mft_lcn = imlec
        imlec += mft_kume
        logfile_lcn = imlec
        imlec += logfile_kume
        upcase_lcn = imlec
        imlec += upcase_kume
        attrdef_lcn = imlec
        imlec += attrdef_kume
        bitmap_lcn = imlec
        imlec += bitmap_kume
        root_index_lcn = imlec
        imlec += kume(INDEX_RECORD_SIZE)
        secure_lcn = imlec
        imlec += 1
        if imlec >= toplam_kume - 2:
            raise NtfsError("Bolum NTFS metaverisi icin yetersiz")
        # $MFTMirr ilk dort MFT kaydini tutar; kayit boyutuna gore yer ayrilir
        mftmirr_kume = kume(4 * MFT_RECORD_SIZE)
        mftmirr_lcn = toplam_kume - mftmirr_kume

        return NtfsLayout(
            cluster_size=cs, sector_size=ss, total_sectors=kullanilabilir_sektor,
            total_clusters=toplam_kume, mft_lcn=mft_lcn, mft_clusters=mft_kume,
            mftmirr_lcn=mftmirr_lcn, mftmirr_clusters=mftmirr_kume,
            logfile_lcn=logfile_lcn,
            logfile_clusters=logfile_kume, upcase_lcn=upcase_lcn,
            upcase_clusters=upcase_kume, attrdef_lcn=attrdef_lcn,
            bitmap_lcn=bitmap_lcn, bitmap_clusters=bitmap_kume,
            root_index_lcn=root_index_lcn, secure_lcn=secure_lcn)

    # ---- dusuk seviye yardimcilar ----------------------------------------
    def _write_clusters(self, lcn: int, veri: bytes) -> None:
        self.dev.write(lcn * self.cluster_size, veri)

    def _zero_clusters(self, lcn: int, adet: int) -> None:
        cs = self.cluster_size
        parca = max(1, (4 * 1024 * 1024) // cs)
        bos = b"\x00" * (parca * cs)
        kalan, cur = adet, lcn
        while kalan > 0:
            n = min(parca, kalan)
            self.dev.write(cur * cs, bos[: n * cs])
            cur += n
            kalan -= n

    @staticmethod
    def _apply_fixup(kayit: bytearray, usa_offset: int, usa_count: int,
                     usn: int, sektor_boyutu: int) -> None:
        """Duzeltme dizisini (Update Sequence Array) uygular.

        Her sektorun son iki bayti diziye tasinir, yerine USN yazilir. NTFS
        surucusu kaydin tam olarak yazildigini boyle dogrular; bu adim
        atlanirsa kayit gecersiz sayilir.
        """
        struct.pack_into("<H", kayit, usa_offset, usn)
        for i in range(usa_count - 1):
            son = (i + 1) * sektor_boyutu - 2
            if son + 2 > len(kayit):
                break
            struct.pack_into("<H", kayit, usa_offset + 2 + i * 2,
                             struct.unpack_from("<H", kayit, son)[0])
            struct.pack_into("<H", kayit, son, usn)

    @staticmethod
    def _data_runs(runs: List[Tuple[int, int]]) -> bytes:
        """Veri kosularini (LCN, uzunluk) esleme ciftlerine cevirir."""
        cikti = bytearray()
        onceki_lcn = 0
        for lcn, uzunluk in runs:
            fark = lcn - onceki_lcn
            uzunluk_baytlari = _signed_bytes(uzunluk, isaretsiz=True)
            ofset_baytlari = _signed_bytes(fark)
            cikti.append((len(ofset_baytlari) << 4) | len(uzunluk_baytlari))
            cikti += uzunluk_baytlari
            cikti += ofset_baytlari
            onceki_lcn = lcn
        cikti.append(0)
        return bytes(cikti)

    # ---- oznitelik olusturucular -----------------------------------------
    def _attr_resident(self, tur: int, deger: bytes, ad: str = "",
                       indexed: int = 0, attr_id: int = 0) -> bytes:
        ad_baytlari = ad.encode("utf-16-le")
        ad_ofseti = 0x18
        deger_ofseti = ad_ofseti + len(ad_baytlari)
        deger_ofseti = (deger_ofseti + 7) & ~7
        toplam = deger_ofseti + len(deger)
        toplam = (toplam + 7) & ~7
        attr = bytearray(toplam)
        struct.pack_into("<IIBBHHH", attr, 0, tur, toplam, 0,
                         len(ad), ad_ofseti if ad else 0, 0, attr_id)
        struct.pack_into("<IHBB", attr, 0x10, len(deger), deger_ofseti,
                         indexed, 0)
        if ad_baytlari:
            attr[ad_ofseti:ad_ofseti + len(ad_baytlari)] = ad_baytlari
        attr[deger_ofseti:deger_ofseti + len(deger)] = deger
        return bytes(attr)

    def _attr_nonresident(self, tur: int, runs: List[Tuple[int, int]],
                          veri_boyutu: int, ad: str = "", attr_id: int = 0,
                          sparse: bool = False,
                          ayrilmis_boyut: Optional[int] = None) -> bytes:
        ad_baytlari = ad.encode("utf-16-le")
        ad_ofseti = 0x40
        kosular = self._data_runs(runs) if runs else b"\x00"
        esleme_ofseti = ad_ofseti + len(ad_baytlari)
        esleme_ofseti = (esleme_ofseti + 7) & ~7
        toplam = esleme_ofseti + len(kosular)
        toplam = (toplam + 7) & ~7
        son_vcn = (sum(u for _l, u in runs) - 1) if runs else 0
        ayrilmis = (ayrilmis_boyut if ayrilmis_boyut is not None
                    else sum(u for _l, u in runs) * self.cluster_size)
        attr = bytearray(toplam)
        struct.pack_into("<IIBBHHH", attr, 0, tur, toplam, 1,
                         len(ad), ad_ofseti if ad else 0,
                         0x8000 if sparse else 0, attr_id)
        struct.pack_into("<QQHHI", attr, 0x10, 0, son_vcn, esleme_ofseti, 0, 0)
        struct.pack_into("<QQQ", attr, 0x28, ayrilmis, veri_boyutu, veri_boyutu)
        if ad_baytlari:
            attr[ad_ofseti:ad_ofseti + len(ad_baytlari)] = ad_baytlari
        attr[esleme_ofseti:esleme_ofseti + len(kosular)] = kosular
        return bytes(attr)

    def _std_info(self, dosya_ozniteligi: int = FILE_ATTR_HIDDEN | FILE_ATTR_SYSTEM) -> bytes:
        deger = bytearray(72)
        struct.pack_into("<QQQQ", deger, 0, self.now, self.now, self.now, self.now)
        struct.pack_into("<I", deger, 32, dosya_ozniteligi)
        struct.pack_into("<I", deger, 64, 0)            # security id
        return bytes(deger)

    def _file_name(self, ust_kayit: int, ad: str, dosya_ozniteligi: int,
                   ayrilmis: int = 0, boyut: int = 0,
                   ad_turu: int = FNAME_WIN32_DOS) -> bytes:
        ad_baytlari = ad.encode("utf-16-le")
        deger = bytearray(0x42 + len(ad_baytlari))
        struct.pack_into("<Q", deger, 0, self._mref(ust_kayit))
        struct.pack_into("<QQQQ", deger, 8, self.now, self.now, self.now, self.now)
        struct.pack_into("<QQ", deger, 0x28, ayrilmis, boyut)
        struct.pack_into("<I", deger, 0x38, dosya_ozniteligi)
        deger[0x40] = len(ad)
        deger[0x41] = ad_turu
        deger[0x42:] = ad_baytlari
        return bytes(deger)

    @staticmethod
    def _sequence(kayit_no: int) -> int:
        """Sistem kayitlarinda sira numarasi kayit numarasina esittir (0 -> 1).

        MFT basvurulari (mref) sira numarasini ust 16 bitte tasir; yanlis sira
        numarasi `ntfs_inode_open` tarafindan "dosya yok" olarak reddedilir.
        """
        return kayit_no if kayit_no else 1

    def _mref(self, kayit_no: int) -> int:
        return (kayit_no & 0xFFFFFFFFFFFF) | (self._sequence(kayit_no) << 48)

    def _make_record(self, kayit_no: int, bayraklar: int, oznitelikler: List[bytes],
                     baglanti: int = 1) -> bytes:
        kayit = bytearray(MFT_RECORD_SIZE)
        usa_offset = 0x30
        usa_count = MFT_RECORD_SIZE // self.sector_size + 1
        attrs_offset = (usa_offset + usa_count * 2 + 7) & ~7
        kayit[0:4] = FILE_MAGIC
        struct.pack_into("<HH", kayit, 4, usa_offset, usa_count)
        struct.pack_into("<Q", kayit, 8, 0)                 # $LogFile LSN
        struct.pack_into("<HHHH", kayit, 0x10, self._sequence(kayit_no),
                         baglanti, attrs_offset, bayraklar)
        imlec = attrs_offset
        for i, attr in enumerate(oznitelikler):
            if imlec + len(attr) + 8 > MFT_RECORD_SIZE:
                raise NtfsError(f"MFT kaydi {kayit_no} tasti")
            # Oznitelik kimligi kayit icinde BENZERSIZ olmalidir; elle verilen
            # degerler cakisabildigi icin burada sirayla yeniden atanir
            # (cakisma `chkdsk` tarafindan "attribute record is corrupt" olarak
            # bildirilir).
            duzeltilmis = bytearray(attr)
            struct.pack_into("<H", duzeltilmis, 0x0E, i)
            kayit[imlec:imlec + len(attr)] = bytes(duzeltilmis)
            imlec += len(attr)
        struct.pack_into("<I", kayit, imlec, AT_END)
        struct.pack_into("<I", kayit, imlec + 4, 0)
        kullanilan = imlec + 8
        struct.pack_into("<II", kayit, 0x18, kullanilan, MFT_RECORD_SIZE)
        struct.pack_into("<Q", kayit, 0x20, 0)              # taban kayit
        struct.pack_into("<H", kayit, 0x28, len(oznitelikler) + 1)
        struct.pack_into("<I", kayit, 0x2C, kayit_no)
        self._apply_fixup(kayit, usa_offset, usa_count, 1, self.sector_size)
        return bytes(kayit)


def _signed_bytes(deger: int, isaretsiz: bool = False) -> bytes:
    """Sayiyi en az sayida bayta sigdirir (veri kosulari icin)."""
    if deger == 0:
        return b"\x00"
    if isaretsiz:
        uzunluk = (deger.bit_length() + 8) // 8
        return deger.to_bytes(uzunluk, "little")
    uzunluk = (deger.bit_length() + 8) // 8
    while True:
        try:
            return deger.to_bytes(uzunluk, "little", signed=True)
        except OverflowError:
            uzunluk += 1


# ==========================================================================
# Bicimlendirme akisi
# ==========================================================================
def _index_entry(mft_ref: int, anahtar: bytes, alt_dugum: bool = False,
                 sira: int = 1) -> bytes:
    """Dizin girisi: MFT basvurusu + $FILE_NAME anahtari."""
    uzunluk = 0x10 + len(anahtar)
    uzunluk = (uzunluk + 7) & ~7
    giris = bytearray(uzunluk)
    struct.pack_into("<QHHH", giris, 0,
                     (mft_ref & 0xFFFFFFFFFFFF) | (sira << 48),
                     uzunluk, len(anahtar), 0x01 if alt_dugum else 0x00)
    giris[0x10:0x10 + len(anahtar)] = anahtar
    return bytes(giris)


def _index_end_entry(alt_dugum_vcn: Optional[int] = None) -> bytes:
    """Dizin sonu isaretcisi; alt dugum varsa VCN eklenir."""
    uzunluk = 0x18 if alt_dugum_vcn is not None else 0x10
    giris = bytearray(uzunluk)
    bayraklar = 0x02 | (0x01 if alt_dugum_vcn is not None else 0x00)
    struct.pack_into("<QHHH", giris, 0, 0, uzunluk, 0, bayraklar)
    if alt_dugum_vcn is not None:
        struct.pack_into("<Q", giris, uzunluk - 8, alt_dugum_vcn)
    return bytes(giris)


class _NtfsBuilder(NtfsFormatter):
    """NtfsFormatter'a bicimlendirme akisini ekler."""

    SISTEM_DOSYALARI = [
        (MFT_MFT, "$MFT"), (MFT_MFTMIRR, "$MFTMirr"), (MFT_LOGFILE, "$LogFile"),
        (MFT_VOLUME, "$Volume"), (MFT_ATTRDEF, "$AttrDef"), (MFT_ROOT, "."),
        (MFT_BITMAP, "$Bitmap"), (MFT_BOOT, "$Boot"), (MFT_BADCLUS, "$BadClus"),
        (MFT_SECURE, "$Secure"), (MFT_UPCASE, "$UpCase"), (MFT_EXTEND, "$Extend"),
    ]

    def format(self, progress: Optional[Callable[[str, int], None]] = None) -> Dict:
        L = self.layout

        def bildir(mesaj: str, yuzde: int) -> None:
            if progress:
                progress(mesaj, yuzde)

        bildir("NTFS yerlesimi hazirlaniyor...", 5)
        bildir("Sabit tablolar yaziliyor ($UpCase, $AttrDef)...", 15)
        self._write_clusters(L.upcase_lcn, upcase_table())
        self._write_clusters(L.attrdef_lcn, attrdef_table())

        bildir("Islem gunlugu ($LogFile) hazirlaniyor...", 30)
        self._write_logfile()

        bildir("Kok dizin olusturuluyor...", 45)
        self._write_root_index()

        bildir("Kume bitmap'i yaziliyor...", 60)
        self._write_bitmap()

        bildir("MFT kayitlari olusturuluyor...", 70)
        kayitlar = self._build_mft_records()
        self._write_mft(kayitlar)

        bildir("MFT yedegi yaziliyor...", 85)
        self._write_mftmirr(kayitlar)

        bildir("Onyukleme sektoru yaziliyor...", 95)
        self._write_boot()

        f = getattr(self.dev, "flush", None)
        if f:
            f()
        bildir("Tamamlandi", 100)
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
        parca = max(1, (4 * 1024 * 1024) // cs)
        dolgu = b"\xFF" * (parca * cs)
        kalan, cur = L.logfile_clusters, L.logfile_lcn
        while kalan > 0:
            n = min(parca, kalan)
            self.dev.write(cur * cs, dolgu[: n * cs])
            cur += n
            kalan -= n

    def _collation_key(self, ad: str) -> List[int]:
        """$FILE_NAME siralama anahtari: $UpCase ile buyuk harfe cevrilmis kodlar.

        NTFS dizin girisleri bu sıraya gore **sirali** olmak zorundadir; sirasiz
        bir dizinde B-agaci aramasi dosyayi bulamaz ve `ntfs_pathname_to_inode`
        "dosya yok" doner.
        """
        tablo = upcase_table()
        return [struct.unpack_from("<H", tablo, ord(ch) * 2)[0] for ch in ad]

    def _root_index_entries(self) -> bytes:
        """Kok dizindeki sistem dosyasi girisleri (siralama kuralina uygun)."""
        girisler = bytearray()
        sirali = sorted((k for k in self.SISTEM_DOSYALARI if k[0] != MFT_ROOT),
                        key=lambda oge: self._collation_key(oge[1]))
        for kayit_no, ad in sirali:
            ozellik = FILE_ATTR_HIDDEN | FILE_ATTR_SYSTEM
            if kayit_no == MFT_EXTEND:
                ozellik |= FILE_ATTR_DIRECTORY
            anahtar = self._file_name(MFT_ROOT, ad, ozellik)
            girisler += _index_entry(kayit_no, anahtar,
                                     sira=self._sequence(kayit_no))
        girisler += _index_end_entry()
        return bytes(girisler)

    def _write_root_index(self) -> None:
        """Kok dizin icin INDX kaydi yazar (buyuk dizin)."""
        L = self.layout
        girisler = self._root_index_entries()
        kayit = bytearray(INDEX_RECORD_SIZE)
        usa_offset = 0x28
        usa_count = INDEX_RECORD_SIZE // L.sector_size + 1
        girisler_ofseti = (usa_offset + usa_count * 2 + 7) & ~7

        kayit[0:4] = INDX_MAGIC
        struct.pack_into("<HH", kayit, 4, usa_offset, usa_count)
        struct.pack_into("<Q", kayit, 8, 0)                    # LSN
        struct.pack_into("<Q", kayit, 0x10, 0)                 # VCN
        # INDEX_HEADER 0x18'de baslar; ofsetler bu noktaya GOREdir
        struct.pack_into("<III", kayit, 0x18,
                         girisler_ofseti - 0x18,
                         girisler_ofseti - 0x18 + len(girisler),
                         INDEX_RECORD_SIZE - 0x18)
        kayit[0x24] = 0                                        # yaprak dugum
        kayit[girisler_ofseti:girisler_ofseti + len(girisler)] = girisler
        self._apply_fixup(kayit, usa_offset, usa_count, 1, L.sector_size)
        self._write_clusters(L.root_index_lcn, bytes(kayit))

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
        kayitlar: List[bytes] = []

        def sistem_ad(kayit_no: int, ad: str, boyut: int = 0,
                      ayrilmis: int = 0, dizin: bool = False) -> bytes:
            ozellik = FILE_ATTR_HIDDEN | FILE_ATTR_SYSTEM
            if dizin:
                ozellik |= FILE_ATTR_DIRECTORY
            return self._attr_resident(
                AT_FILE_NAME,
                self._file_name(MFT_ROOT, ad, ozellik, ayrilmis, boyut),
                indexed=1)

        # 0: $MFT
        mft_boyut = L.mft_clusters * cs
        mft_bitmap = bytearray(max(8, (32 + 7) // 8))
        for i in range(MFT_RESERVED_COUNT):
            mft_bitmap[i >> 3] |= 1 << (i & 7)
        kayitlar.append(self._make_record(MFT_MFT, MFT_FLAG_IN_USE, [
            self._attr_resident(AT_STANDARD_INFORMATION, self._std_info()),
            sistem_ad(MFT_MFT, "$MFT", mft_boyut, mft_boyut),
            self._attr_nonresident(AT_DATA, [(L.mft_lcn, L.mft_clusters)],
                                   mft_boyut, attr_id=1),
            self._attr_resident(AT_BITMAP, bytes(mft_bitmap), attr_id=2),
        ]))

        # 1: $MFTMirr
        mirr_boyut = 4 * MFT_RECORD_SIZE
        kayitlar.append(self._make_record(MFT_MFTMIRR, MFT_FLAG_IN_USE, [
            self._attr_resident(AT_STANDARD_INFORMATION, self._std_info()),
            sistem_ad(MFT_MFTMIRR, "$MFTMirr", mirr_boyut,
                      L.mftmirr_clusters * cs),
            self._attr_nonresident(AT_DATA,
                                   [(L.mftmirr_lcn, L.mftmirr_clusters)],
                                   mirr_boyut, attr_id=1),
        ]))

        # 2: $LogFile
        log_boyut = L.logfile_clusters * cs
        kayitlar.append(self._make_record(MFT_LOGFILE, MFT_FLAG_IN_USE, [
            self._attr_resident(AT_STANDARD_INFORMATION, self._std_info()),
            sistem_ad(MFT_LOGFILE, "$LogFile", log_boyut, log_boyut),
            self._attr_nonresident(AT_DATA, [(L.logfile_lcn, L.logfile_clusters)],
                                   log_boyut, attr_id=1),
        ]))

        # 3: $Volume
        birim_bilgisi = struct.pack("<QBBH", 0, 3, 1, 0)   # NTFS 3.1, bayrak yok
        volume_attrs = [
            self._attr_resident(AT_STANDARD_INFORMATION, self._std_info()),
            sistem_ad(MFT_VOLUME, "$Volume"),
        ]
        if self.label:
            volume_attrs.append(self._attr_resident(
                AT_VOLUME_NAME, self.label.encode("utf-16-le"), attr_id=1))
        volume_attrs.append(self._attr_resident(AT_VOLUME_INFORMATION,
                                                birim_bilgisi, attr_id=2))
        volume_attrs.append(self._attr_resident(AT_DATA, b"", attr_id=3))
        kayitlar.append(self._make_record(MFT_VOLUME, MFT_FLAG_IN_USE, volume_attrs))

        # 4: $AttrDef
        attrdef = attrdef_table()
        kayitlar.append(self._make_record(MFT_ATTRDEF, MFT_FLAG_IN_USE, [
            self._attr_resident(AT_STANDARD_INFORMATION, self._std_info()),
            sistem_ad(MFT_ATTRDEF, "$AttrDef", len(attrdef), cs),
            self._attr_nonresident(AT_DATA, [(L.attrdef_lcn, 1)], len(attrdef),
                                   attr_id=1),
        ]))

        # 5: kok dizin
        kayitlar.append(self._make_record(MFT_ROOT,
                                          MFT_FLAG_IN_USE | MFT_FLAG_DIRECTORY,
                                          self._root_attrs()))

        # 6: $Bitmap
        bitmap_boyut = (L.total_clusters + 7) // 8
        kayitlar.append(self._make_record(MFT_BITMAP, MFT_FLAG_IN_USE, [
            self._attr_resident(AT_STANDARD_INFORMATION, self._std_info()),
            sistem_ad(MFT_BITMAP, "$Bitmap", bitmap_boyut,
                      L.bitmap_clusters * cs),
            self._attr_nonresident(AT_DATA, [(L.bitmap_lcn, L.bitmap_clusters)],
                                   bitmap_boyut, attr_id=1),
        ]))

        # 7: $Boot
        kayitlar.append(self._make_record(MFT_BOOT, MFT_FLAG_IN_USE, [
            self._attr_resident(AT_STANDARD_INFORMATION, self._std_info()),
            sistem_ad(MFT_BOOT, "$Boot", 8192, 4 * cs),
            self._attr_nonresident(AT_DATA, [(0, max(1, 8192 // cs))], 8192,
                                   attr_id=1),
        ]))

        # 8: $BadClus — seyrek, birim boyutunda
        birim_boyutu = L.total_clusters * cs
        kayitlar.append(self._make_record(MFT_BADCLUS, MFT_FLAG_IN_USE, [
            self._attr_resident(AT_STANDARD_INFORMATION, self._std_info()),
            sistem_ad(MFT_BADCLUS, "$BadClus"),
            self._attr_resident(AT_DATA, b"", attr_id=1),
            self._bad_stream(birim_boyutu),
        ]))

        # 9: $Secure — $SDS akisi ve iki dizin ($SDH karma, $SII kimlik).
        # ntfs-3g bu iki dizini arar; yoksa birimi acamaz.
        # 0x08: kayit bir "gorunum indeksi" tasir ($SDH/$SII)
        kayitlar.append(self._make_record(MFT_SECURE, MFT_FLAG_IN_USE | 0x08, [
            self._attr_resident(AT_STANDARD_INFORMATION, self._std_info()),
            sistem_ad(MFT_SECURE, "$Secure"),
            self._attr_nonresident(AT_DATA, [(L.secure_lcn, 1)], 0, ad="$SDS",
                                   attr_id=1),
            self._attr_resident(AT_INDEX_ROOT,
                                self._small_index_root(0, 0x12),
                                ad="$SDH", attr_id=2),
            self._attr_resident(AT_INDEX_ROOT,
                                self._small_index_root(0, 0x10),
                                ad="$SII", attr_id=3),
        ]))

        # 10: $UpCase
        upcase_boyut = 128 * 1024
        kayitlar.append(self._make_record(MFT_UPCASE, MFT_FLAG_IN_USE, [
            self._attr_resident(AT_STANDARD_INFORMATION, self._std_info()),
            sistem_ad(MFT_UPCASE, "$UpCase", upcase_boyut,
                      L.upcase_clusters * cs),
            self._attr_nonresident(AT_DATA, [(L.upcase_lcn, L.upcase_clusters)],
                                   upcase_boyut, attr_id=1),
        ]))

        # 11: $Extend (bos dizin)
        bos_index = self._small_index_root()
        kayitlar.append(self._make_record(MFT_EXTEND,
                                          MFT_FLAG_IN_USE | MFT_FLAG_DIRECTORY, [
            self._attr_resident(AT_STANDARD_INFORMATION,
                                self._std_info(FILE_ATTR_HIDDEN | FILE_ATTR_SYSTEM)),
            sistem_ad(MFT_EXTEND, "$Extend", dizin=True),
            self._attr_resident(AT_INDEX_ROOT, bos_index, ad="$I30", attr_id=1),
        ]))

        # 12..15: ayrilmis kayitlar.
        # Bunlarda $FILE_NAME BULUNMAZ — kok dizinde listelenmezler. Ad eklemek
        # `chkdsk` tarafindan "Attribute record (30) is corrupt" olarak bildirilir.
        for no in range(12, MFT_RESERVED_COUNT):
            kayitlar.append(self._make_record(no, MFT_FLAG_IN_USE, [
                self._attr_resident(AT_STANDARD_INFORMATION, self._std_info()),
                self._attr_resident(AT_DATA, b"", attr_id=1),
            ]))
        return kayitlar

    def _bad_stream(self, birim_boyutu: int) -> bytes:
        """$BadClus:$Bad — birim boyutunda, hic kume ayrilmamis akis.

        Referans bicimde bu akis **seyrek bayragi kullanmaz**; bunun yerine
        ofset alani sifir uzunlukta olan tek bir veri kosusu yazilir. Boyle bir
        kosu "tahsis edilmemis" (delik) anlamina gelir:

            02 ff 3f   ->  uzunluk alani 2 bayt (0x3fff kume), ofset alani yok

        Seyrek bayragi (0x8000) konulursa baslikta 8 baytlik `compressed_size`
        alani beklenir ve ad/esleme ofsetleri kayar; `chkdsk` bunu bozuk sayar.
        """
        L = self.layout
        son_vcn = L.total_clusters - 1
        ad = "$Bad".encode("utf-16-le")
        ad_ofseti = 0x40
        esleme_ofseti = (ad_ofseti + len(ad) + 7) & ~7
        uzunluk_baytlari = _signed_bytes(L.total_clusters, isaretsiz=True)
        kosular = bytes([len(uzunluk_baytlari)]) + uzunluk_baytlari + b"\x00"
        toplam = (esleme_ofseti + len(kosular) + 7) & ~7
        attr = bytearray(toplam)
        struct.pack_into("<IIBBHHH", attr, 0, AT_DATA, toplam, 1,
                         4, ad_ofseti, 0, 0)
        struct.pack_into("<QQHHI", attr, 0x10, 0, son_vcn, esleme_ofseti, 0, 0)
        struct.pack_into("<QQQ", attr, 0x28, birim_boyutu, birim_boyutu, 0)
        attr[ad_ofseti:ad_ofseti + len(ad)] = ad
        attr[esleme_ofseti:esleme_ofseti + len(kosular)] = kosular
        return bytes(attr)

    def _small_index_root(self, tur: int = AT_FILE_NAME,
                          siralama: int = 1) -> bytes:
        """Bos $INDEX_ROOT (alt dugum yok).

        `tur` indekslenen oznitelik turu, `siralama` karsilastirma kuralidir:
        1 = dosya adi, 0x10 = ULONG ($SII), 0x12 = guvenlik karmasi ($SDH).
        """
        L = self.layout
        girisler = _index_end_entry()
        deger = bytearray(0x20 + len(girisler))
        struct.pack_into("<IIIBBBB", deger, 0, tur, siralama,
                         INDEX_RECORD_SIZE,
                         max(1, INDEX_RECORD_SIZE // L.cluster_size), 0, 0, 0)
        struct.pack_into("<IIII", deger, 0x10, 0x10, 0x10 + len(girisler),
                         0x10 + len(girisler), 0)
        deger[0x20:] = girisler
        return bytes(deger)

    def _root_attrs(self) -> List[bytes]:
        """Kok dizinin oznitelikleri.

        Tum girisler $INDEX_ROOT icine sigdigi surece ayri bir INDX kaydi
        (index allocation) olusturulmaz — bu, dizin yapisini belirgin sekilde
        basitlestirir ve NTFS icin gecerli bir bicimdir ("kucuk dizin").
        """
        L = self.layout
        girisler = self._root_index_entries()
        kok_deger = bytearray(0x20 + len(girisler))
        struct.pack_into("<IIIBBBB", kok_deger, 0, AT_FILE_NAME, 1,
                         INDEX_RECORD_SIZE,
                         max(1, INDEX_RECORD_SIZE // L.cluster_size), 0, 0, 0)
        struct.pack_into("<IIII", kok_deger, 0x10, 0x10, 0x10 + len(girisler),
                         0x10 + len(girisler), 0)     # bayrak 0 = kucuk dizin
        kok_deger[0x20:] = girisler

        return [
            self._attr_resident(AT_STANDARD_INFORMATION,
                                self._std_info(FILE_ATTR_HIDDEN | FILE_ATTR_SYSTEM)),
            self._attr_resident(
                AT_FILE_NAME,
                self._file_name(MFT_ROOT, ".",
                                FILE_ATTR_HIDDEN | FILE_ATTR_SYSTEM
                                | FILE_ATTR_DIRECTORY),
                indexed=1),
            self._attr_resident(AT_INDEX_ROOT, bytes(kok_deger), ad="$I30",
                                attr_id=1),
        ]

    def _write_mft(self, kayitlar: List[bytes]) -> None:
        L = self.layout
        veri = bytearray(L.mft_clusters * L.cluster_size)
        for i, kayit in enumerate(kayitlar):
            ofset = i * MFT_RECORD_SIZE
            if ofset + MFT_RECORD_SIZE > len(veri):
                raise NtfsError("MFT alani yetersiz")
            veri[ofset:ofset + MFT_RECORD_SIZE] = kayit
        self._write_clusters(L.mft_lcn, bytes(veri))

    def _write_mftmirr(self, kayitlar: List[bytes]) -> None:
        """Ilk dort MFT kaydinin yedegi."""
        L = self.layout
        veri = bytearray(L.mftmirr_clusters * L.cluster_size)
        for i, kayit in enumerate(kayitlar[:4]):
            veri[i * MFT_RECORD_SIZE:(i + 1) * MFT_RECORD_SIZE] = kayit
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
        alan = bytearray(max(8192, L.cluster_size))
        alan[0:len(boot)] = boot
        self._write_clusters(0, bytes(alan))
        # yedek: birimin son sektoru (toplam_sektor konumunda)
        self.dev.write(L.total_sectors * L.sector_size, boot)


def format_ntfs(dev: BlockDevice, label: str = "", cluster_size: int = 0,
                progress: Optional[Callable[[str, int], None]] = None) -> Dict:
    """Kisayol: verilen aygiti NTFS olarak bicimlendirir."""
    return _NtfsBuilder(dev, label=label, cluster_size=cluster_size).format(progress)
