"""HFS+ ad kurallari: ayristirma (NFD) ve buyuk/kucuk harf duyarsiz siralama.

HFS+ katalog anahtarlari (ust kimlik, ad) sirasiyla dizilir; ad sirasi
Apple'in `FastUnicodeCompare`i ile belirlenir (TN1150): her UTF-16 birimi bir
"kucuk harf" tablosundan gecirilir, tabloda 0 olanlar yok sayilir, U+0000
en sona (0xFFFF) gider. Yanlis siralanmis anahtar fsck'ta "invalid key
order" olur ve arama o adi bulamaz.

Tablo burada **uretilir**, Apple'in verisi kopyalanmaz. Kural (olculdu,
65 536 girisin tamami fsck_hfs'in tablosuyla ayni — ADR 0062):

  * yalnizca ust bayti 00 01 03 04 05 10 20 21 FE FF olan karakterler,
  * Unicode 3.2'de tanimli olmayan karakterler degismez,
  * **onceden birlesik (kanonik ayristirilabilir) karakterler degismez** —
    HFS+ adlari zaten ayristirilmis saklar,
  * Gurcuce buyuk harfler (10A0-10C5) Unicode 2.0'daki gibi 10D0-10F5'e,
  * buyuk/kucuk esi Unicode 3.0 ve sonrasinda tanimlanan 27 harf degismez,
  * bicim karakterleri (ZWNJ, ZWJ, LRM/RLM, yon ve BOM) yok sayilir (0).

Ayristirma Unicode 3.2 NFD'sidir; Apple U+2000-2FFF, U+F900-FAFF ve
U+2F800-2FAFF araliklarini ayristirmaz.
"""
from __future__ import annotations

import unicodedata
from typing import List, Optional

_UCD = unicodedata.ucd_3_2_0
_PAGES = {0x00, 0x01, 0x03, 0x04, 0x05, 0x10, 0x20, 0x21, 0xFE, 0xFF}
_IGNORABLE = {0x200C, 0x200D, 0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D,
              0x202E, 0x206A, 0x206B, 0x206C, 0x206D, 0x206E, 0x206F, 0xFEFF}
# Esi Unicode 3.0+ ile tanimlandigi icin Apple tablosunda kucultulmeyenler
_LATE_PAIRS = {0x01A6, 0x01F6, 0x01F7, 0x03D8, 0x03DA, 0x03DC, 0x03DE, 0x03E0,
               0x03F4, 0x048A, 0x048C, 0x048E, 0x04C5, 0x04C9, 0x04CD, 0x04D4,
               0x04D8, 0x04E0, 0x04E8, 0x0500, 0x0502, 0x0504, 0x0506, 0x0508,
               0x050A, 0x050C, 0x050E}
_NO_DECOMPOSE = ((0x2000, 0x2FFF), (0xF900, 0xFAFF), (0x2F800, 0x2FAFF))

_table: Optional[List[int]] = None


def _fold_one(c: int) -> int:
    if c == 0:
        return 0xFFFF
    if c in _IGNORABLE:
        return 0
    if (c >> 8) not in _PAGES or c in _LATE_PAIRS:
        return c
    if 0x10A0 <= c <= 0x10C5:
        return c + 0x30
    ch = chr(c)
    if _UCD.category(ch) == "Cn":
        return c
    dec = unicodedata.decomposition(ch)
    if dec and not dec.startswith("<"):
        return c
    low = ch.lower()
    if len(low) != 1 or _UCD.category(low) == "Cn":
        return c
    return ord(low)


def fold_table() -> List[int]:
    """UTF-16 birimi -> katlanmis deger (0 = yok say)."""
    global _table
    if _table is None:
        _table = [_fold_one(c) for c in range(0x10000)]
    return _table


def _units(name: str) -> List[int]:
    raw = name.encode("utf-16-be")
    return [(raw[i] << 8) | raw[i + 1] for i in range(0, len(raw), 2)]


def compare_units(a: List[int], b: List[int], case_sensitive: bool) -> int:
    """-1 / 0 / 1. HFSX: ikili (UTF-16 birimi) karsilastirma."""
    if case_sensitive:
        return (a > b) - (a < b)
    t = fold_table()
    i = j = 0
    while True:
        c1 = 0
        while not c1 and i < len(a):
            c1 = t[a[i]]
            i += 1
        c2 = 0
        while not c2 and j < len(b):
            c2 = t[b[j]]
            j += 1
        if c1 != c2:
            return -1 if c1 < c2 else 1
        if not c1:
            return 0


def compare_names(a: str, b: str, case_sensitive: bool = False) -> int:
    """Diskte saklanan (NFD) iki adi katalog kuralina gore karsilastirir."""
    return compare_units(_units(a), _units(b), case_sensitive)


def fold_key(name: str) -> tuple:
    """Esitlik denetimi icin katlanmis birim dizisi (yok sayilanlar atilir)."""
    t = fold_table()
    return tuple(v for v in (t[u] for u in _units(name)) if v)


def hfs_nfd(text: str) -> str:
    """Apple'in ayristirmasi: Unicode 3.2 NFD, disari birakilan araliklar haric."""
    out: List[str] = []
    run: List[str] = []
    for ch in text:
        cp = ord(ch)
        if any(lo <= cp <= hi for lo, hi in _NO_DECOMPOSE):
            if run:
                out.append(_UCD.normalize("NFD", "".join(run)))
                run = []
            out.append(ch)
        else:
            run.append(ch)
    if run:
        out.append(_UCD.normalize("NFD", "".join(run)))
    return "".join(out)
