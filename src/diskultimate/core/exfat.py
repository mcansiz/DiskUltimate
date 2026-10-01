"""Saf Python exFAT surucusu (bicimlendirme + okuma + yazma).

Windows ve macOS'ta `mkfs.exfat` bulunmadigi icin exFAT destegi harici araca
birakilamaz; bu modul bicimi dogrudan uygular. Boylece exFAT her uc platformda
da kullanilabilir.

Yerlesim (spec: .claude/specs/exfat.md):
    Sektor 0..11    Ana onyukleme bolgesi (VBR + 8 genisletilmis + OEM + saglama)
    Sektor 12..23   Yedek onyukleme bolgesi (ayni icerik)
    FatOffset..     FAT bolgesi
    ClusterHeap..   Veri bolgesi: bitmap, upcase tablosu, kok dizin, dosyalar
"""
from __future__ import annotations

import datetime
import os
import struct
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .image import BlockDevice
from .platform import restore_owner
from ..i18n import tr

ENTRY_SIZE = 32

# Dizin girisi turleri
E_BITMAP = 0x81
E_UPCASE = 0x82
E_LABEL = 0x83
E_FILE = 0x85
E_STREAM = 0xC0
E_NAME = 0xC1

ATTR_READ_ONLY = 0x01
ATTR_HIDDEN = 0x02
ATTR_SYSTEM = 0x04
ATTR_DIRECTORY = 0x10
ATTR_ARCHIVE = 0x20

EOC = 0xFFFFFFFF
BAD_CLUSTER = 0xFFFFFFF7


class ExFatError(Exception):
    pass


@dataclass
class ExEntry:
    name: str
    attr: int = 0
    cluster: int = 0
    size: int = 0
    mtime: Optional[datetime.datetime] = None
    ctime: Optional[datetime.datetime] = None
    contiguous: bool = True          # NoFatChain bayragi
    slot_offset: int = 0             # dizin verisindeki bayt ofseti
    slot_count: int = 0              # giris kumesindeki 32 baytlik giris sayisi
    path: str = ""

    @property
    def is_dir(self) -> bool:
        return bool(self.attr & ATTR_DIRECTORY)

    @property
    def is_hidden(self) -> bool:
        return bool(self.attr & ATTR_HIDDEN)

    @property
    def is_readonly(self) -> bool:
        return bool(self.attr & ATTR_READ_ONLY)

    @property
    def attr_text(self) -> str:
        bayraklar = [("R", ATTR_READ_ONLY), ("H", ATTR_HIDDEN), ("S", ATTR_SYSTEM),
                     ("D", ATTR_DIRECTORY), ("A", ATTR_ARCHIVE)]
        return "".join(c for c, bit in bayraklar if self.attr & bit) or "-"


# --------------------------------------------------------------------------
# Saglama ve karma islevleri (spec'te tanimli)
# --------------------------------------------------------------------------
def boot_checksum(data: bytes, bytes_per_sector: int) -> int:
    """Onyukleme bolgesi saglamasi: 0..10. sektorler, 3 alan haric."""
    total = 0
    for i in range(11 * bytes_per_sector):
        if i in (106, 107, 112):      # VolumeFlags ve PercentInUse haric
            continue
        total = (((total << 31) | (total >> 1)) + data[i]) & 0xFFFFFFFF
    return total


def table_checksum(data: bytes) -> int:
    """Upcase tablosu saglamasi (32 bit)."""
    total = 0
    for b in data:
        total = (((total << 31) | (total >> 1)) + b) & 0xFFFFFFFF
    return total


def entry_set_checksum(entries: bytes) -> int:
    """Dizin giris kumesi saglamasi (16 bit); SetChecksum alani atlanir."""
    total = 0
    for i, b in enumerate(entries):
        if i in (2, 3):
            continue
        total = (((total << 15) | (total >> 1)) + b) & 0xFFFF
    return total


def name_hash(upcased_name: str) -> int:
    """Dosya adi karmasi (16 bit), buyuk harfe cevrilmis ad uzerinden."""
    total = 0
    for b in upcased_name.encode("utf-16-le"):
        total = (((total << 15) | (total >> 1)) + b) & 0xFFFF
    return total


# exFAT spec'inde tanimli **onerilen** buyuk harf tablosunun sikistirilmis hali
# (5836 bayt, saglama 0xE619D30D). exfatprogs ve Windows bu tabloyu bekler; kendi
# uretilmis bir tablo `fsck.exfat` tarafindan "corrupted upcase table" sayilir.
# Burada zlib+base64 ile saklanir; `tests.run_all` saglamayi dogrular.
_UPCASE_STANDARD_B64 = (
    "eNqt13XQFje7BvBssovLU9zy5A48eNDi7l6sQHEoVkqBUkoppRR3d3d3d17gxd3dKaWUUtz9Phc9Z87M93FmvmP7m/xxzc6d"
    "TTaTFSE8IYUSvghEDBFTxBKxRRwRV8QT8UUCkVCExCcikUgskoikIplILlKIlCKVSC3SCC3CwggSVqQV6UREpBcZREaRSWQW"
    "WURW4UQ2kV3kEDlFLpFbfCryiLwin8gvCoiCopAoLIqIoqKYKC5KiJKilCgtyoiyopwoLyqIiqKSqCyqiM9EVVFNVBc1RE3x"
    "uaglaos64gtRV9QT9UUD0VA0Eo1FE9H0f1X/s+gmfhHdRQ/RU/QSvUUf0Vf0E/3FADFQDBKDxRAxVAwTw8UIMVKMEqPFGDFW"
    "jBPjxQQxUUwSk8UUMVVME9PFDDFTzBKzxRwxV8wT88UCsVAsEovFErFULBPLxQqxUqwSq8UasVasE+vFBrFRbBKbxRYRJbaK"
    "bWK7iBY7xE6xS+wWe8ResU/sFwfEQXFIHBZHxFFxTBwXJ8RJcUqcFmfEWXFOnBcXxEVxSVwWV8RVce1/WP/in+q7eFh+T4IP"
    "MSAWxIF4kABCkAiSQDJIAakgDYSBIC1EIANkgizgIDvk9HJ5uSEP5IMCXkEoDEWhOJSE0lAWykNFrxJUgapQHWpCLagDdaE+"
    "NITG0BSaQQtoBa2hDbSF9tABOkIn6AxdvJ/gZ/gFenilZS+vN/SF/t4AGOQN9obAMG+4N8Ib6Y2CMd5Y77k33pvgTYSicoo3"
    "1ZvmkZzhzYTZMBfmewtgkbfYW+ItheXeCljlrfbWwDrY4G2Ezd4WLwq2eS+8aG+Ht9Pb5e329qDt8/ajHfQOoR2BY3ACTsEZ"
    "OAcX4BIM967CdbgBN+EW3IY7cBfuw0PvkfcY7Sk8x/Vewmt4C+8Bm19+OHyIAbEgDsSDBBCCRJAEkkEKSAVpIAwkrUwLEcgA"
    "mSALOMgOOSE35JF5ZT6ZXxaQBWVLV1gWlkVlK1dclpAlobQsI8vKclABKkEVqArVZQ1ZE2vU36stP6xMXTnCqy9Heg1lI9lY"
    "NpFjvC9lMznWayFbylbyKzkBq9RGNnNtZTvZXk71Osjv5DTve9kJa9VZ/ii7yJ9kV/mz7CZbuO6yh5zv9ZK95SKvr+wn+8sB"
    "crlXRn5YsbJymBwuR8iRcpTc4I2RY+U4OV5OkBPlJDlZTpFT5TQ5Xc6QM+UsOVvOkXPlPDlfLpAL5SK5WC6RS+UyuVyukCvl"
    "KrlarpFr5Tq5Xm6QG+UmuVlukVFyq9wmt8touUPulLvkbrlH7pX75H55QB6Uh+RheUQelcfkcXlCnpSn5Gl5Rp6V5+R5eUFe"
    "lJfkZXlFXpXX5HX5q7whf5M35e/ylvxD3pZ/yjvyL3lX3pP35QP5UD6Sj+UT+VQ+k8/lC/lSvpKv5Rv5Vr6T7yXj0e8pqZTy"
    "VaBiqJgqloqt4qi4Kp6KrxKohCqkPlGJVGKVRCVVyVRylUKlVKlUapVGaRVWRpGyKq1KpyIqvcqgMqpMKrPKorIqp7Kp7CqH"
    "yqlyqdzqU5VH5VX5VH5VQBVUhVRhVUQVVcVUcVVClVSlVGlVRpVV5VR5VUFVVJVUZVVFfaaqqmqquqqhaqrPVS1VW9VRX6i6"
    "qp6qrxqohqqRaqyaqKbqS9VMNVctVEvVSn2lWquvVRv1jWqr2qn26lvVQX2nOqrvVSf1g+qsflRd1E+qq3qn3itW3VUP1VP1"
    "Ur1VH9VX9VP91QA1UA1Sg9UQNVQNU8PVCDVSjVKj1Rg1Vo1T49UENVFNUpPVFDVVTVPT1Qw1U81Ss9UcNVfNU/PVArVQLVKL"
    "1RL09e89rfxv1M/5L+qH/n31Y+q4OqFOqlPqtDqjzqpz6jxchMtwFa7DDbgJt+A23IG7cB8eqkfqlXqinqpn6rl6Aa/Ua3j7"
    "H/dA4MEvfeX7fuDH8GP6sfzYfhw/rh/Pj+8n8BP6If8TP5Gf2E/iJ/WT+cn9FH5KP5Wf2k/jaz/sG59866f10/kRP72fwc/o"
    "Z/Iz+1n8rL7zs/nZ/Rz/5/p/Nb6m0AxaQCtoDW2gLbSHDtAROkFn6AJdoRt0h57Q2+/j9/X7+f39Af5Af5A/GIbCcBgJo2Es"
    "jIeJMBmmwnSYCbNhLsyHhbAYlsJyWAmrYS2sh42wGaJgG0T7O2AX7IF9cAAOwRGI9o/DSTgNZ+E8XITLcBWuww24CbfgNtyB"
    "u3AfHsJjeArP4SW8hrfwHvDZF0jA7Q1iQCyIA/EgAYQgESQJkgbJguRBiiBlkCpIHaQJdBAOTECBDdIG6YJIkD7IEGQMMgWZ"
    "gyxB1sAF2YLsQY4gZ5AryB18GuQJ8gb5gvxBgaBgUCgoHBQJigbFguJBiaBkUCooHZQJygblgvJBhaBiUCmoHFQJPguqBtWC"
    "6kGNoGbweVArqB3UCb4I6gb1gvpBg6Bh0ChoHDQJmv6/9s/8PHlz11330D11L91b99F9dT/dXw/QA/UgPVgP0UP1MD1cj9Aj"
    "9Sg9Wo/RY/U4PV5P0BP1JD1ZT9FT9TQ9Xc/QM/UsPVvP0XP1PD1fL9AL9SK9WC/RS/UyvVyv0Cv1Kr1ar9Fr9Tq9Xm/QG/Um"
    "vVlv0VF6q96mt+tovUPv1Lv0br1H79X79H59QB/Uh/RhfUQf1cf0cX1Cn9Sn9Gl9Rp/V5/R5fUFf1Jf0ZX1FX9XX9HX9q76h"
    "f9M39e/6lv5D39Z/6jv6L31X39P39QP9UD/Sj/UT/VQ/08/1C/1Sv9Kv9Rv9Vr/T7zV/+Lj/8HoPY5OGY0AsiAPxIAGEIBEk"
    "gWSQAlJBGvhwEKSFCGSATJAFHGSHnJAb8kA+KACFoAgUgxJQCspAOagAlaAKVIXqUBNqQR2oC/WhITSGptAMWkAraA1toC20"
    "hw7QETpBZ+gCXaEbdIee0Bv6Qn8YCINhKAyHkTAaxsL48ITwxPCk8OTwlPDU8LTw9PCM8EyYDXNhPiyExbAUlsNKWA1rYT1s"
    "hM0QBdsgGnbCbtgL++EgHIajcBxOwmk4C+fhIlyGq3AdbsBNuAW34Q7chfvwEB7DU3gOL+F1+E34bfhd+H2Yw7FMbBPHxDXx"
    "THyTwCQ0/5xTmJQmlUlt0hhtkpnk/5DDxphMJrPJYrIaZ7KZ7CbHR7mAKWgKmcKmiClqipniH+UKpqKpZCqbKuYzU86U/4dc"
    "1VQz1U09U9M0MLVMI1PHNDF1kesjN0RujNzafG3amG9MW9POtDfffpQ3my1mvzlgDppD5qK5ZF6aV+aO+cu8Nm9Md9PDDDSD"
    "zGAzxAw1w8xwM+KjPNFMMpPNFDPVTDPTzYyP8kKzyCw2S8xSs8wsNys+yhvNJrPaRJm1Zp1Zbzb8nT+MKcpsNdvMdhNtdpid"
    "ZpfZbfaYvWbff451lzlijppj5ry5YE6aU+a0OWPOmnN/5w/zuGyumKvmmrlt/jQ3zG/mprlrbpk//s4f5nfX3DP3zQPz0Dwy"
    "j80T89Q8M8/Ni7/n/2HuT8w7894wfuzxe0OKfAooBsWkWBSb4lBcikfxKQElpBB9QokoMSWhpJSMklMKSkmpKDWlIY2tb4jI"
    "UlpKRxFKTxkoI2WizJSFspKjbJSdclBOykW56VPKQ3kpH+WnAlSQClFhKkJFqRgVpxJUkkpRaSpDZakclacKVJEqUWWqQp9R"
    "VapG1akG1aTPqRbVpjr0BdWlelSfGlBDakSNqQk1pS+pGTWnFtSSWtFX1Jq+pjb0DbWldtSevqUO9B11pO+pE/1AnelH6kI/"
    "UVf6mbrRL9SdelBP6kW9qQ/1pX7UnwbQQBpEg2kIDaVhNJxG0EgaRaNpDI2lcTSeJtBEmkSTaQpNpWk0nWbQTJpFs2kOzaV5"
    "NJ8W0EJaRItpCS2lZbScVtBKWkWraQ2tpXW0njbQRtpEm2kLRdFW2kbbKZp20E7aRbtpD+2lfbSfDtBBOkSH6QgdpWN0nE7Q"
    "STpFp+kMnaVzdJ4u0EW6RJfpCl2la3SdfqUb9BvdpN/pFv1Bt+lPukN/0V26R/fpAT2kR/SYntBTekbP6QW9pFf0mt7QW3pH"
    "74lJWM9Kq6xvAxvDxrSxbGwbx8a18Wx8m8AmtCH7iU1kE9skNqlNZpPbFDalTWVT2zRW27A1lqy1aW06G7HpbQab0WaymW0W"
    "m9U6m81mtzlsTpvL5raf2jw2r81n89sCtqAtZAvbIraoLWaL2xK2pC1lS9sytqwtZ8vbCrairWQr2yr2M9RVs9VtDVvTfm5r"
    "2dq2jv3C1rX1bH3bwDa0jWxj28Q2tV/aZra5bWFb2lb2K9vafm3b2G9sW9vOtrff/svzPW0v29v2AebKan1kQ2RjZFNkc2RL"
    "JCqyNbItsj0SHdkR2RnZFdkd2RPZG9kX2R85EDkYORQ5HDkSORo5FmEuF1M4Dz/CyvkucDFcTBfLxXZxXFwXz8V3CVxCF3Kf"
    "uEQusUvikrpkLrlL4VK6VC61S+M0XpTGkbMurUvnIi69y+Ayukwus8visjrnsrnsrolrCs1cc9fCtXSt3FfwNXwD7Vx7963r"
    "4L5zHd33rpP7AX50XdxPrqv72XVzv7jurofrCb2hL/SHgTAYhsJwGAmjYSyMh4kwGabCdJgJs2EuzIeFsBiWwnJYCathLayH"
    "jbAZomAbRMNO2A17YT8chMNwFI7DSTgNZ+E8XITLcBWuww246X53t9wf7rb7091xf7m77p677x64h+6Re+yeuKfumXvuXriX"
    "7pV77d64t+6de+/YzQzNCs0OzQnNDc0LzQ8tCC0MLQotDi0JLQ0tCy0PrQitDK0KrQ6tCa0NrQutD20IbQxtCm0ObQlFhbaG"
    "toW2h6JDO0I7Q7tCu0N7QsypT1pOy+k4wuk5A2fkTJyZs3BWdpyNs3MOzsm5ODd/ynk4L+fj/FyAC3IhbsANuRE35ibclL/k"
    "ZtycW3BLbsVfcWv+mtvwN9yW23F7/pY78Hfckb/nTvwDd+YfuQv/xF35Z+7Gv3B37sE9uRf35j7cl/txfx7AA3kQD+YhPJSH"
    "8XAewSN5FI/mMTyWx/F4nsATeRJP5ik8lafxdJ7BM3kWz+Y5PJfn8XxewAt5ES/mJbyUl/FyXsEreRWv5jW8ltfxet7AG3kT"
    "b+YtHMVbeRtv52jewTt5F+/mPbyX9/F+PsAH+RAf5iN8lI/xcT7BJ/kUn+YzfJbP8Xm+wBf5El/mK3yVr/F1/pVv8G98k3/n"
    "W/wH3+Y/+Q7/xXf5Ht/nB/yQH/FjfsJP+Rk/5xf8kl/xa37Db/kdv2fmfwNZziFG"
)
UPCASE_STANDARD_CHECKSUM = 0xE619D30D
_upcase_cache: Optional[bytes] = None


def standard_upcase_table() -> bytes:
    """exFAT standart (sikistirilmis) buyuk harf tablosunu dondurur."""
    global _upcase_cache
    if _upcase_cache is None:
        import base64
        import zlib
        _upcase_cache = zlib.decompress(base64.b64decode(_UPCASE_STANDARD_B64))
    return _upcase_cache


def expand_upcase(table: bytes) -> Dict[int, int]:
    """Tabloyu kod -> buyuk harf kodu eslemesine acar.

    Tam tablo 131072 bayttir. Daha kisaysa sikistirilmistir: 0xFFFF kacis
    degerini izleyen sayi, o kadar karakterin kendisiyle eslendigini belirtir.
    Yalnizca degisen karakterler eslemede tutulur.
    """
    esleme: Dict[int, int] = {}
    if len(table) >= 65536 * 2:
        for i in range(65536):
            value = struct.unpack_from("<H", table, i * 2)[0]
            if value != i:
                esleme[i] = value
        return esleme
    indeks = 0
    pos = 0
    while pos + 1 < len(table):
        value = struct.unpack_from("<H", table, pos)[0]
        pos += 2
        if value == 0xFFFF and pos + 1 < len(table):
            adet = struct.unpack_from("<H", table, pos)[0]
            pos += 2
            indeks += adet          # bu araliktaki karakterler degismez
            continue
        if value != indeks:
            esleme[indeks] = value
        indeks += 1
    return esleme


def _upcase_map(table: bytes) -> Dict[int, int]:
    return expand_upcase(table)


# --------------------------------------------------------------------------
# Zaman donusumleri
# --------------------------------------------------------------------------
def _to_exfat_time(dt: datetime.datetime) -> Tuple[int, int]:
    yil = max(1980, min(2107, dt.year))
    tarih = ((yil - 1980) << 9) | (dt.month << 5) | dt.day
    saat = (dt.hour << 11) | (dt.minute << 5) | (dt.second // 2)
    return (tarih << 16) | saat, (dt.second % 2) * 100


def _from_exfat_time(value: int) -> Optional[datetime.datetime]:
    if not value:
        return None
    tarih, saat = value >> 16, value & 0xFFFF
    try:
        return datetime.datetime(
            1980 + ((tarih >> 9) & 0x7F), max(1, (tarih >> 5) & 0x0F),
            max(1, tarih & 0x1F), (saat >> 11) & 0x1F, (saat >> 5) & 0x3F,
            (saat & 0x1F) * 2)
    except ValueError:
        return None


def _norm(path: str) -> List[str]:
    return [p for p in path.replace("\\", "/").split("/") if p not in ("", ".")]


# --------------------------------------------------------------------------
class ExFatFS:
    """Bagli bir exFAT birimi."""

    def __init__(self, dev: BlockDevice):
        self.dev = dev
        self._bitmap: Optional[bytearray] = None
        self._bitmap_dirty = False
        self._upcase: Optional[Dict[int, int]] = None
        self._mount()

    # ---- baglama ----------------------------------------------------------
    def _mount(self) -> None:
        boot = self.dev.read(0, 512)
        if boot[3:11] != b"EXFAT   ":
            raise ExFatError(tr("exFAT imzasi bulunamadi"))
        self.partition_offset = struct.unpack_from("<Q", boot, 64)[0]
        self.volume_length = struct.unpack_from("<Q", boot, 72)[0]
        self.fat_offset = struct.unpack_from("<I", boot, 80)[0]
        self.fat_length = struct.unpack_from("<I", boot, 84)[0]
        self.cluster_heap_offset = struct.unpack_from("<I", boot, 88)[0]
        self.cluster_count = struct.unpack_from("<I", boot, 92)[0]
        self.root_cluster = struct.unpack_from("<I", boot, 96)[0]
        self.volume_serial = struct.unpack_from("<I", boot, 100)[0]
        self.volume_flags = struct.unpack_from("<H", boot, 106)[0]
        self.bytes_per_sector = 1 << boot[108]
        self.sectors_per_cluster = 1 << boot[109]
        self.num_fats = boot[110]
        self.percent_in_use = boot[112]
        if self.cluster_count == 0 or self.sectors_per_cluster == 0:
            raise ExFatError(tr("Gecersiz exFAT parametreleri"))
        self.label = ""
        self.bitmap_cluster = 0
        self.bitmap_length = 0
        self.upcase_cluster = 0
        self.upcase_length = 0
        self._read_root_metadata()

    def _read_root_metadata(self) -> None:
        """Kok dizindeki bitmap, upcase ve etiket girislerini okur."""
        veri = self._read_chain_data(self.root_cluster)
        for off in range(0, len(veri) - 31, ENTRY_SIZE):
            kind = veri[off]
            if kind == 0x00:
                break
            if kind == E_BITMAP:
                self.bitmap_cluster = struct.unpack_from("<I", veri, off + 20)[0]
                self.bitmap_length = struct.unpack_from("<Q", veri, off + 24)[0]
            elif kind == E_UPCASE:
                self.upcase_cluster = struct.unpack_from("<I", veri, off + 20)[0]
                self.upcase_length = struct.unpack_from("<Q", veri, off + 24)[0]
            elif kind == E_LABEL:
                n = veri[off + 1]
                self.label = veri[off + 2:off + 2 + n * 2].decode("utf-16-le", "ignore")

    @property
    def cluster_bytes(self) -> int:
        return self.sectors_per_cluster * self.bytes_per_sector

    @property
    def fs_type_name(self) -> str:
        return "exFAT"

    @property
    def readonly(self) -> bool:
        return getattr(self.dev, "readonly", False)

    def cluster_offset(self, cluster: int) -> int:
        return ((self.cluster_heap_offset + (cluster - 2) * self.sectors_per_cluster)
                * self.bytes_per_sector)

    # ---- FAT --------------------------------------------------------------
    def get_fat(self, cluster: int) -> int:
        off = self.fat_offset * self.bytes_per_sector + cluster * 4
        return struct.unpack("<I", self.dev.read(off, 4))[0]

    def set_fat(self, cluster: int, value: int) -> None:
        off = self.fat_offset * self.bytes_per_sector + cluster * 4
        self.dev.write(off, struct.pack("<I", value & 0xFFFFFFFF))

    def chain(self, start: int, count: Optional[int] = None,
              contiguous: bool = False) -> List[int]:
        """Kume zinciri. `contiguous` ise FAT okunmadan ardisik varsayilir."""
        if start < 2:
            return []
        if contiguous and count:
            return list(range(start, start + count))
        out: List[int] = []
        cur = start
        sinir = self.cluster_count + 2
        while 2 <= cur < sinir and len(out) <= self.cluster_count:
            out.append(cur)
            if count and len(out) >= count:
                break
            cur = self.get_fat(cur)
            if cur >= EOC or cur == 0:
                break
        return out

    # ---- ayirma bitmap'i --------------------------------------------------
    def _load_bitmap(self) -> bytearray:
        if self._bitmap is None:
            clusters = (self.bitmap_length + self.cluster_bytes - 1) // self.cluster_bytes
            veri = bytearray()
            for c in self.chain(self.bitmap_cluster, clusters):
                veri += self.dev.read(self.cluster_offset(c), self.cluster_bytes)
            self._bitmap = veri[:self.bitmap_length]
        return self._bitmap

    def is_used(self, cluster: int) -> bool:
        bm = self._load_bitmap()
        i = cluster - 2
        if i < 0 or i >= self.cluster_count:
            return True
        return bool(bm[i >> 3] & (1 << (i & 7)))

    def _set_used(self, cluster: int, value: bool) -> None:
        bm = self._load_bitmap()
        i = cluster - 2
        if i < 0 or i >= self.cluster_count:
            return
        if value:
            bm[i >> 3] |= (1 << (i & 7))
        else:
            bm[i >> 3] &= ~(1 << (i & 7))
        self._bitmap_dirty = True

    def free_cluster_count(self) -> int:
        bm = self._load_bitmap()
        kullanilan = sum(bin(b).count("1") for b in bm)
        return max(0, self.cluster_count - kullanilan)

    def _find_free_run(self, count: int) -> Optional[int]:
        """`count` adet ardisik bos kume arar."""
        seri = 0
        for c in range(2, self.cluster_count + 2):
            if not self.is_used(c):
                seri += 1
                if seri >= count:
                    return c - count + 1
            else:
                seri = 0
        return None

    def alloc_clusters(self, count: int) -> Tuple[int, bool]:
        """Kume ayirir. (ilk_kume, ardisik_mi) dondurur."""
        if count <= 0:
            return 0, True
        bas = self._find_free_run(count)
        if bas is not None:
            for c in range(bas, bas + count):
                self._set_used(c, True)
            return bas, True
        # ardisik yer yok: dagitik ayir ve FAT zinciri kur
        secilen: List[int] = []
        for c in range(2, self.cluster_count + 2):
            if not self.is_used(c):
                secilen.append(c)
                if len(secilen) == count:
                    break
        if len(secilen) < count:
            raise ExFatError(tr("Birimde yeterli bos alan yok"))
        for i, c in enumerate(secilen):
            self._set_used(c, True)
            self.set_fat(c, secilen[i + 1] if i + 1 < len(secilen) else EOC)
        return secilen[0], False

    def free_chain(self, start: int, count: int, contiguous: bool) -> None:
        for c in self.chain(start, count, contiguous):
            self._set_used(c, False)
            if not contiguous:
                self.set_fat(c, 0)

    def flush(self) -> None:
        """Bitmap ve doluluk yuzdesini diske yazar."""
        if self._bitmap_dirty and self._bitmap is not None:
            clusters = self.chain(
                self.bitmap_cluster,
                (self.bitmap_length + self.cluster_bytes - 1) // self.cluster_bytes)
            dolgu = bytes(self._bitmap).ljust(len(clusters) * self.cluster_bytes, b"\x00")
            for i, c in enumerate(clusters):
                self.dev.write(self.cluster_offset(c),
                               dolgu[i * self.cluster_bytes:(i + 1) * self.cluster_bytes])
            self._bitmap_dirty = False
            self._update_percent()
        f = getattr(self.dev, "flush", None)
        if f:
            f()

    def _update_percent(self) -> None:
        try:
            free = self.free_cluster_count()
            percent = int(100 * (self.cluster_count - free) / max(1, self.cluster_count))
            for taban in (0, 12 * self.bytes_per_sector):
                sector = bytearray(self.dev.read(taban, self.bytes_per_sector))
                if sector[3:11] != b"EXFAT   ":
                    continue
                sector[112] = min(100, max(0, percent))
                self.dev.write(taban, bytes(sector))
            self.percent_in_use = percent
        except Exception:
            pass

    # ---- dizin verisi -----------------------------------------------------
    def _read_chain_data(self, cluster: int, count: Optional[int] = None,
                         contiguous: bool = False) -> bytearray:
        veri = bytearray()
        for c in self.chain(cluster, count, contiguous):
            veri += self.dev.read(self.cluster_offset(c), self.cluster_bytes)
        return veri

    def _write_dir(self, cluster: int, veri: bytearray) -> None:
        clusters = self.chain(cluster)
        gereken = max(1, (len(veri) + self.cluster_bytes - 1) // self.cluster_bytes)
        while len(clusters) < gereken:
            new, _ = self.alloc_clusters(1)
            self.set_fat(clusters[-1], new)
            self.set_fat(new, EOC)
            clusters.append(new)
        dolgu = bytes(veri).ljust(gereken * self.cluster_bytes, b"\x00")
        for i, c in enumerate(clusters[:gereken]):
            self.dev.write(self.cluster_offset(c),
                           dolgu[i * self.cluster_bytes:(i + 1) * self.cluster_bytes])

    def _parse_dir(self, veri: bytes, base_path: str) -> List[ExEntry]:
        """Giris kumelerini (0x85 + 0xC0 + 0xC1...) cozumler."""
        out: List[ExEntry] = []
        i = 0
        while i + ENTRY_SIZE <= len(veri):
            kind = veri[i]
            if kind == 0x00:
                break
            if kind != E_FILE:
                i += ENTRY_SIZE
                continue
            ikincil = veri[i + 1]
            total = (ikincil + 1) * ENTRY_SIZE
            if i + total > len(veri):
                break
            cluster_data = veri[i:i + total]
            attr = struct.unpack_from("<H", cluster_data, 4)[0]
            olusturma = _from_exfat_time(struct.unpack_from("<I", cluster_data, 8)[0])
            degistirme = _from_exfat_time(struct.unpack_from("<I", cluster_data, 12)[0])
            stream = cluster_data[ENTRY_SIZE:2 * ENTRY_SIZE]
            if len(stream) < ENTRY_SIZE or stream[0] != E_STREAM:
                i += total
                continue
            bayraklar = stream[1]
            name_length = stream[3]
            first_cluster = struct.unpack_from("<I", stream, 20)[0]
            size = struct.unpack_from("<Q", stream, 24)[0]
            name = ""
            for n in range(2, ikincil + 1):
                entry = cluster_data[n * ENTRY_SIZE:(n + 1) * ENTRY_SIZE]
                if entry[0] != E_NAME:
                    continue
                name += entry[2:32].decode("utf-16-le", "ignore")
            name = name[:name_length]
            out.append(ExEntry(
                name=name, attr=attr, cluster=first_cluster, size=size,
                mtime=degistirme, ctime=olusturma,
                contiguous=bool(bayraklar & 0x02),
                slot_offset=i, slot_count=ikincil + 1,
                path=base_path.rstrip("/") + "/" + name))
            i += total
        return out

    # ---- gezinme ----------------------------------------------------------
    def _dir_cluster(self, path: str) -> int:
        cluster = self.root_cluster
        cur = "/"
        for chunk in _norm(path):
            eslesen = None
            for e in self._parse_dir(self._read_chain_data(cluster), cur):
                if e.name.lower() == chunk.lower():
                    eslesen = e
                    break
            if eslesen is None:
                raise ExFatError(tr("Yol bulunamadi: {}", path))
            if not eslesen.is_dir:
                raise ExFatError(tr("Dizin degil: {}", path))
            cluster = eslesen.cluster
            cur = cur.rstrip("/") + "/" + chunk
        return cluster

    def listdir(self, path: str = "/") -> List[ExEntry]:
        cluster = self._dir_cluster(path)
        girisler = self._parse_dir(self._read_chain_data(cluster),
                                   path if path.startswith("/") else "/" + path)
        girisler.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        return girisler

    def find(self, path: str) -> ExEntry:
        parcalar = _norm(path)
        if not parcalar:
            return ExEntry(name="/", attr=ATTR_DIRECTORY, cluster=self.root_cluster,
                           path="/")
        upper = "/" + "/".join(parcalar[:-1])
        for e in self._parse_dir(self._read_chain_data(self._dir_cluster(upper)), upper):
            if e.name.lower() == parcalar[-1].lower():
                return e
        raise ExFatError(tr("Bulunamadi: {}", path))

    def exists(self, path: str) -> bool:
        try:
            self.find(path)
            return True
        except ExFatError:
            return False

    # ---- okuma ------------------------------------------------------------
    def read_file(self, path: str, max_bytes: int = -1) -> bytes:
        entry = self.find(path)
        if entry.is_dir:
            raise ExFatError(tr("Dizin dosya olarak okunamaz"))
        return self.read_entry(entry, max_bytes)

    def read_entry(self, entry: ExEntry, max_bytes: int = -1) -> bytes:
        if not entry.cluster or not entry.size:
            return b""
        sinir = entry.size if max_bytes < 0 else min(entry.size, max_bytes)
        cluster_count = (entry.size + self.cluster_bytes - 1) // self.cluster_bytes
        out = bytearray()
        for c in self.chain(entry.cluster, cluster_count, entry.contiguous):
            out += self.dev.read(self.cluster_offset(c), self.cluster_bytes)
            if len(out) >= sinir:
                break
        return bytes(out[:sinir])

    def extract(self, path: str, dest: str) -> str:
        entry = self.find(path)
        if entry.is_dir:
            return self.extract_tree(path, dest)
        if os.path.isdir(dest):
            dest = os.path.join(dest, entry.name)
        os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
        kalan = entry.size
        cluster_count = (entry.size + self.cluster_bytes - 1) // self.cluster_bytes
        with open(dest, "wb") as fh:
            for c in self.chain(entry.cluster, cluster_count, entry.contiguous):
                if kalan <= 0:
                    break
                block = self.dev.read(self.cluster_offset(c), self.cluster_bytes)
                fh.write(block[:kalan])
                kalan -= len(block)
        restore_owner(dest)     # yetkili kopyada dosya root'a ait kalmasin
        if entry.mtime:
            ts = entry.mtime.timestamp()
            os.utime(dest, (ts, ts))
        return dest

    def extract_tree(self, path: str, dest_dir: str) -> str:
        parcalar = _norm(path)
        target = os.path.join(dest_dir, parcalar[-1]) if parcalar else dest_dir
        os.makedirs(target, exist_ok=True)
        for cocuk in self.listdir(path):
            lower = path.rstrip("/") + "/" + cocuk.name
            if cocuk.is_dir:
                self.extract_tree(lower, target)
            else:
                self.extract(lower, os.path.join(target, cocuk.name))
        return target

    # ---- yazma ------------------------------------------------------------
    def _upcase_name(self, name: str) -> str:
        if self._upcase is None:
            cluster_count = (self.upcase_length + self.cluster_bytes - 1) // self.cluster_bytes
            table = bytes(self._read_chain_data(self.upcase_cluster, cluster_count))
            self._upcase = _upcase_map(table[:self.upcase_length])
        return "".join(chr(self._upcase.get(ord(ch), ord(ch))) for ch in name)

    def _build_entry_set(self, name: str, attr: int, cluster: int, size: int,
                         contiguous: bool, when: Optional[datetime.datetime] = None) -> bytes:
        when = when or datetime.datetime.now()
        zaman, _on_ms = _to_exfat_time(when)
        name_entries = (len(name) + 14) // 15
        ikincil = 1 + name_entries

        file_entry = bytearray(ENTRY_SIZE)
        file_entry[0] = E_FILE
        file_entry[1] = ikincil
        struct.pack_into("<H", file_entry, 4, attr)
        struct.pack_into("<I", file_entry, 8, zaman)    # olusturma
        struct.pack_into("<I", file_entry, 12, zaman)   # degistirme
        struct.pack_into("<I", file_entry, 16, zaman)   # erisim

        if not cluster:
            # Bos dosyada NoFatChain bayragi olmaz (fsck.exfat: "empty, but
            # has no Fat chain" -> bozuk).
            contiguous = False
        stream = bytearray(ENTRY_SIZE)
        stream[0] = E_STREAM
        stream[1] = 0x01 | (0x02 if contiguous else 0x00)   # AllocationPossible|NoFatChain
        stream[3] = len(name)
        struct.pack_into("<H", stream, 4, name_hash(self._upcase_name(name)))
        struct.pack_into("<Q", stream, 8, size)     # ValidDataLength
        struct.pack_into("<I", stream, 20, cluster)
        struct.pack_into("<Q", stream, 24, size)    # DataLength

        name_blocks = bytearray()
        kodlanmis = name.encode("utf-16-le")
        for i in range(name_entries):
            entry = bytearray(ENTRY_SIZE)
            entry[0] = E_NAME
            chunk = kodlanmis[i * 30:(i + 1) * 30]
            entry[2:2 + len(chunk)] = chunk
            name_blocks += entry

        entry_set = bytes(file_entry) + bytes(stream) + bytes(name_blocks)
        saglama = entry_set_checksum(entry_set)
        entry_set = bytearray(entry_set)
        struct.pack_into("<H", entry_set, 2, saglama)
        return bytes(entry_set)

    def _insert_entry(self, dir_path: str, blob: bytes) -> None:
        cluster = self._dir_cluster(dir_path)
        veri = self._read_chain_data(cluster)
        old_size = len(veri)
        gereken = len(blob) // ENTRY_SIZE
        pos = self._find_free_slots(veri, gereken)
        if pos < 0:
            # Sondaki bos yuvalar yeni kumeyle birlesir; arama tekrarlanir.
            # Giris dogrudan yeni kumenin basina yazilirsa aradaki 0x00
            # ("dizin sonu") girisi okuyucuyu durdurur (matris testinde olculdu).
            veri += bytearray(self.cluster_bytes * ((len(blob) // self.cluster_bytes) + 1))
            pos = self._find_free_slots(veri, gereken)
        veri[pos:pos + len(blob)] = blob
        self._write_dir(cluster, veri)
        if len(veri) > old_size:
            self._set_dir_size(dir_path, len(veri))

    def _set_dir_size(self, dir_path: str, size: int) -> None:
        """Buyuyen alt dizinin ust girisindeki DataLength/ValidDataLength.

        exFAT'ta dizinin boyutu ust dizindeki akis girisinde durur; zincir
        buyuyup boyut eski kalirsa fsck.exfat ve Windows dizini tutarsiz
        sayar. Kok dizinin boyut girisi yoktur.
        """
        parcalar = _norm(dir_path)
        if not parcalar:
            return
        upper = "/" + "/".join(parcalar[:-1])
        entry = self.find(dir_path)
        parent_cluster = self._dir_cluster(upper)
        veri = self._read_chain_data(parent_cluster)
        bas = entry.slot_offset
        stream = bas + ENTRY_SIZE
        struct.pack_into("<Q", veri, stream + 8, size)     # ValidDataLength
        struct.pack_into("<Q", veri, stream + 24, size)    # DataLength
        end = bas + entry.slot_count * ENTRY_SIZE
        struct.pack_into("<H", veri, bas + 2, entry_set_checksum(bytes(veri[bas:end])))
        self._write_dir(parent_cluster, veri)

    @staticmethod
    def _find_free_slots(veri: bytearray, gereken: int) -> int:
        seri = 0
        bas = -1
        for off in range(0, len(veri) - ENTRY_SIZE + 1, ENTRY_SIZE):
            kind = veri[off]
            if kind == 0x00 or not (kind & 0x80):     # bos veya kullanim disi
                if seri == 0:
                    bas = off
                seri += 1
                if seri >= gereken:
                    return bas
            else:
                seri = 0
                bas = -1
        return -1

    def write_file(self, path: str, data: bytes, overwrite: bool = True) -> ExEntry:
        if self.readonly:
            raise ExFatError(tr("Birim salt okunur"))
        parcalar = _norm(path)
        if not parcalar:
            raise ExFatError(tr("Gecersiz dosya yolu"))
        name, upper = parcalar[-1], "/" + "/".join(parcalar[:-1])
        if self.exists(path):
            if not overwrite:
                raise ExFatError(tr("Dosya zaten var: {}", path))
            self.remove(path)
        first_cluster, ardisik = 0, True
        if data:
            gereken = (len(data) + self.cluster_bytes - 1) // self.cluster_bytes
            first_cluster, ardisik = self.alloc_clusters(gereken)
            for i, c in enumerate(self.chain(first_cluster, gereken, ardisik)):
                chunk = data[i * self.cluster_bytes:(i + 1) * self.cluster_bytes]
                self.dev.write(self.cluster_offset(c),
                               chunk.ljust(self.cluster_bytes, b"\x00"))
        blob = self._build_entry_set(name, ATTR_ARCHIVE, first_cluster, len(data), ardisik)
        self._insert_entry(upper, blob)
        self.flush()
        return self.find(path)

    def import_file(self, local_path: str, dest_dir: str = "/",
                    name: Optional[str] = None) -> ExEntry:
        entry_name = name or os.path.basename(local_path)
        with open(local_path, "rb") as fh:
            return self.write_file(dest_dir.rstrip("/") + "/" + entry_name, fh.read())

    def import_tree(self, local_dir: str, dest_dir: str = "/") -> int:
        sayac = 0
        taban = os.path.basename(os.path.normpath(local_dir))
        target = dest_dir.rstrip("/") + "/" + taban
        if not self.exists(target):
            self.mkdir(target)
        for oge in sorted(os.listdir(local_dir)):
            kaynak = os.path.join(local_dir, oge)
            if os.path.isdir(kaynak):
                sayac += self.import_tree(kaynak, target)
            elif os.path.isfile(kaynak):
                self.import_file(kaynak, target)
                sayac += 1
        return sayac

    def mkdir(self, path: str) -> ExEntry:
        if self.readonly:
            raise ExFatError(tr("Birim salt okunur"))
        parcalar = _norm(path)
        if not parcalar:
            raise ExFatError(tr("Gecersiz klasor yolu"))
        name, upper = parcalar[-1], "/" + "/".join(parcalar[:-1])
        if self.exists(path):
            raise ExFatError(tr("Zaten var: {}", path))
        cluster, _ardisik = self.alloc_clusters(1)
        self.set_fat(cluster, EOC)
        self.dev.write(self.cluster_offset(cluster), b"\x00" * self.cluster_bytes)
        blob = self._build_entry_set(name, ATTR_DIRECTORY, cluster, self.cluster_bytes, False)
        self._insert_entry(upper, blob)
        self.flush()
        return self.find(path)

    def makedirs(self, path: str) -> None:
        cur = ""
        for chunk in _norm(path):
            cur += "/" + chunk
            if not self.exists(cur):
                self.mkdir(cur)

    def remove(self, path: str, recursive: bool = False) -> None:
        if self.readonly:
            raise ExFatError(tr("Birim salt okunur"))
        parcalar = _norm(path)
        if not parcalar:
            raise ExFatError(tr("Kok dizin silinemez"))
        entry = self.find(path)
        if entry.is_dir:
            cocuklar = self.listdir(path)
            if cocuklar and not recursive:
                raise ExFatError(tr("Klasor bos degil"))
            for cocuk in cocuklar:
                self.remove(path.rstrip("/") + "/" + cocuk.name, recursive=True)
        upper = "/" + "/".join(parcalar[:-1])
        cluster = self._dir_cluster(upper)
        veri = self._read_chain_data(cluster)
        for e in self._parse_dir(veri, upper):
            if e.name.lower() == parcalar[-1].lower():
                entry = e
                break
        for i in range(entry.slot_count):       # InUse bitini temizle
            veri[entry.slot_offset + i * ENTRY_SIZE] &= 0x7F
        self._write_dir(cluster, veri)
        if entry.cluster:
            cluster_count = max(1, (entry.size + self.cluster_bytes - 1) // self.cluster_bytes)
            self.free_chain(entry.cluster, cluster_count, entry.contiguous)
        self.flush()

    def rename(self, path: str, new_name: str) -> ExEntry:
        entry = self.find(path)
        parcalar = _norm(path)
        upper = "/" + "/".join(parcalar[:-1])
        cluster = self._dir_cluster(upper)
        veri = self._read_chain_data(cluster)
        for e in self._parse_dir(veri, upper):
            if e.name.lower() == parcalar[-1].lower():
                entry = e
                break
        for i in range(entry.slot_count):
            veri[entry.slot_offset + i * ENTRY_SIZE] &= 0x7F
        self._write_dir(cluster, veri)
        blob = self._build_entry_set(new_name, entry.attr, entry.cluster, entry.size,
                                     entry.contiguous, entry.mtime)
        self._insert_entry(upper, blob)
        self.flush()
        return self.find(upper.rstrip("/") + "/" + new_name)

    def set_label(self, label: str) -> None:
        """Kok dizindeki birim etiketi girisini gunceller."""
        label = label[:11]
        cluster = self.root_cluster
        veri = self._read_chain_data(cluster)
        entry = bytearray(ENTRY_SIZE)
        entry[0] = E_LABEL
        entry[1] = len(label)
        kodlanmis = label.encode("utf-16-le")
        entry[2:2 + len(kodlanmis)] = kodlanmis
        for off in range(0, len(veri) - ENTRY_SIZE + 1, ENTRY_SIZE):
            if veri[off] in (E_LABEL, E_LABEL & 0x7F):
                veri[off:off + ENTRY_SIZE] = entry
                break
        else:
            pos = self._find_free_slots(veri, 1)
            if pos < 0:
                pos = len(veri)
                veri += bytearray(self.cluster_bytes)
            veri[pos:pos + ENTRY_SIZE] = entry
        self._write_dir(cluster, veri)
        self.label = label
        self.flush()

    def stats(self) -> Dict[str, int]:
        free = self.free_cluster_count()
        return {
            "total_bytes": self.cluster_count * self.cluster_bytes,
            "free_bytes": free * self.cluster_bytes,
            "used_bytes": (self.cluster_count - free) * self.cluster_bytes,
            "cluster_size": self.cluster_bytes,
            "total_clusters": self.cluster_count,
            "free_clusters": free,
        }

    # ======================================================================
    # Bicimlendirme
    # ======================================================================
    @staticmethod
    def default_cluster_sectors(total_sectors: int, bytes_per_sector: int = 512) -> int:
        size = total_sectors * bytes_per_sector
        if size <= 256 * 1024 * 1024:
            cluster = 4 * 1024
        elif size <= 32 * 1024 ** 3:
            cluster = 32 * 1024
        else:
            cluster = 128 * 1024
        return max(1, cluster // bytes_per_sector)

    @staticmethod
    def format(dev: BlockDevice, label: str = "", cluster_sectors: int = 0,
               partition_offset: int = 0, volume_serial: int = 0,
               progress=None) -> "ExFatFS":
        """Bolumu exFAT olarak bicimlendirir (harici arac gerekmez)."""
        def report(message: str, percent: int) -> None:
            if progress:
                progress(message, percent)

        bps = dev.sector_size
        total = dev.sector_count
        if total < 2048:
            raise ExFatError(tr("Bolum exFAT icin cok kucuk (en az 1 MB)"))
        spc = cluster_sectors or ExFatFS.default_cluster_sectors(total, bps)
        bps_shift = bps.bit_length() - 1
        spc_shift = spc.bit_length() - 1

        # Yerlesim: FAT 24. sektorde baslar, veri bolgesi kume sinirina hizalanir
        fat_offset = 24
        cluster_count = 0
        fat_length = 1
        for _ in range(8):
            heap_offset = fat_offset + fat_length
            artik = heap_offset % spc
            if artik:
                heap_offset += spc - artik
            kalan = total - heap_offset
            if kalan <= 0:
                raise ExFatError(tr("Bolum exFAT icin cok kucuk"))
            new_cluster = kalan // spc
            new_fat = ((new_cluster + 2) * 4 + bps - 1) // bps
            if new_fat == fat_length and new_cluster == cluster_count:
                break
            fat_length, cluster_count = new_fat, new_cluster
        heap_offset = fat_offset + fat_length
        artik = heap_offset % spc
        if artik:
            heap_offset += spc - artik
        cluster_count = (total - heap_offset) // spc
        if cluster_count < 8:
            raise ExFatError(tr("Bolum exFAT icin cok kucuk"))

        if volume_serial == 0:
            simdi = datetime.datetime.now()
            volume_serial = (int(simdi.timestamp()) ^ (simdi.microsecond << 8)) & 0xFFFFFFFF

        cluster_bytes = spc * bps
        bitmap_bytes = (cluster_count + 7) // 8
        bitmap_cluster = (bitmap_bytes + cluster_bytes - 1) // cluster_bytes
        upcase = standard_upcase_table()
        upcase_cluster = (len(upcase) + cluster_bytes - 1) // cluster_bytes

        bitmap_first = 2
        upcase_first = bitmap_first + bitmap_cluster
        root_first = upcase_first + upcase_cluster
        root_cluster = 1
        kullanilan = bitmap_cluster + upcase_cluster + root_cluster
        if kullanilan + 1 >= cluster_count:
            raise ExFatError(tr("Bolum exFAT metaverisi icin yetersiz"))

        report(tr("exFAT onyukleme bolgesi yaziliyor..."), 10)
        # --- onyukleme sektoru ---
        boot = bytearray(bps)
        boot[0:3] = b"\xEB\x76\x90"
        boot[3:11] = b"EXFAT   "
        struct.pack_into("<Q", boot, 64, partition_offset)
        struct.pack_into("<Q", boot, 72, total)
        struct.pack_into("<I", boot, 80, fat_offset)
        struct.pack_into("<I", boot, 84, fat_length)
        struct.pack_into("<I", boot, 88, heap_offset)
        struct.pack_into("<I", boot, 92, cluster_count)
        struct.pack_into("<I", boot, 96, root_first)
        struct.pack_into("<I", boot, 100, volume_serial)
        struct.pack_into("<H", boot, 104, 0x0100)      # surum 1.00
        struct.pack_into("<H", boot, 106, 0)           # VolumeFlags
        boot[108] = bps_shift
        boot[109] = spc_shift
        boot[110] = 1                                   # FAT sayisi
        boot[111] = 0x80                                # DriveSelect
        boot[112] = int(100 * kullanilan / cluster_count)
        struct.pack_into("<H", boot, 510, 0xAA55)

        genisletilmis = bytearray(bps)
        struct.pack_into("<I", genisletilmis, bps - 4, 0xAA550000)
        oem = bytearray(bps)
        struct.pack_into("<I", oem, bps - 4, 0xAA550000)
        rezerve = bytearray(bps)

        bolge = bytearray()
        bolge += boot
        for _ in range(8):
            bolge += genisletilmis
        bolge += oem
        bolge += rezerve
        saglama = boot_checksum(bytes(bolge), bps)
        saglama_sektoru = struct.pack("<I", saglama) * (bps // 4)

        for taban in (0, 12):
            dev.write_sectors(taban, bytes(bolge))
            dev.write_sectors(taban + 11, saglama_sektoru)

        report(tr("FAT bolgesi hazirlaniyor..."), 30)
        # --- FAT ---
        fat = bytearray(fat_length * bps)
        struct.pack_into("<I", fat, 0, 0xFFFFFFF8)
        struct.pack_into("<I", fat, 4, 0xFFFFFFFF)
        for cluster, adet in ((bitmap_first, bitmap_cluster), (upcase_first, upcase_cluster),
                           (root_first, root_cluster)):
            for i in range(adet):
                c = cluster + i
                value = EOC if i == adet - 1 else c + 1
                struct.pack_into("<I", fat, c * 4, value)
        dev.write_sectors(fat_offset, bytes(fat))

        report(tr("Ayirma bitmap'i yaziliyor..."), 55)
        # --- ayirma bitmap'i ---
        bitmap = bytearray((cluster_count + 7) // 8)
        for c in range(2, 2 + kullanilan):
            i = c - 2
            bitmap[i >> 3] |= (1 << (i & 7))
        veri_ofseti = heap_offset * bps
        dev.write(veri_ofseti + (bitmap_first - 2) * cluster_bytes,
                  bytes(bitmap).ljust(bitmap_cluster * cluster_bytes, b"\x00"))

        report(tr("Buyuk harf tablosu yaziliyor..."), 70)
        dev.write(veri_ofseti + (upcase_first - 2) * cluster_bytes,
                  upcase.ljust(upcase_cluster * cluster_bytes, b"\x00"))

        report(tr("Kok dizin olusturuluyor..."), 85)
        # --- kok dizin: etiket + bitmap + upcase girisleri ---
        root = bytearray(root_cluster * cluster_bytes)
        off = 0
        if label:
            etiket = label[:11]
            root[off] = E_LABEL
            root[off + 1] = len(etiket)
            kodlanmis = etiket.encode("utf-16-le")
            root[off + 2:off + 2 + len(kodlanmis)] = kodlanmis
            off += ENTRY_SIZE
        root[off] = E_BITMAP
        struct.pack_into("<I", root, off + 20, bitmap_first)
        struct.pack_into("<Q", root, off + 24, bitmap_bytes)
        off += ENTRY_SIZE
        root[off] = E_UPCASE
        struct.pack_into("<I", root, off + 4, table_checksum(upcase))
        struct.pack_into("<I", root, off + 20, upcase_first)
        struct.pack_into("<Q", root, off + 24, len(upcase))
        dev.write(veri_ofseti + (root_first - 2) * cluster_bytes, bytes(root))

        f = getattr(dev, "flush", None)
        if f:
            f()
        report(tr("Tamamlandi"), 100)
        return ExFatFS(dev)
