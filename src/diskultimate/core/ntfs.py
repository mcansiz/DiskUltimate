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
from ..i18n import tr

NTFS_OEM = b"NTFS    "
FILE_MAGIC = b"FILE"
INDX_MAGIC = b"INDX"
# 1024 bayt: Windows, 512 bayt sektorlu diskte 4 KiB MFT kaydini TANIMAZ
# (Get-Volume "FileSystemType: Unknown"; olculdu 2026-09-29). 4 KiB kayit
# yalnizca 4K yerel sektorlu disklerde gecerlidir.
MFT_RECORD_SIZE = 1024
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
BOOT_BYTES = 8192              # $Boot: onyukleme sektoru + onyukleme kodu
MFT_BADCLUS = 8
MFT_SECURE = 9
MFT_UPCASE = 10
MFT_EXTEND = 11
MFT_RESERVED_COUNT = 16          # 0..15 sistem icin ayrilir
# $Extend alt dosyalari. Windows birimi baglarken bunlari acar; yoksa birimi
# "yapisi bozuk" sayar (olay 55, ADR 0059). Yerlesim mkntfs ile aynidir.
MFT_QUOTA = 24
MFT_OBJID = 25
MFT_REPARSE = 26
EXTEND_FILES = ((MFT_QUOTA, "$Quota"), (MFT_OBJID, "$ObjId"),
                (MFT_REPARSE, "$Reparse"))

# --- dosya oznitelikleri ---
FILE_ATTR_READONLY = 0x0001
FILE_ATTR_HIDDEN = 0x0002
FILE_ATTR_SYSTEM = 0x0004
FILE_ATTR_ARCHIVE = 0x0020
FILE_ATTR_DIRECTORY = 0x10000000   # $FILE_NAME icinde kullanilan bayrak
FILE_ATTR_VIEW_INDEX = 0x20000000  # kayit gorunum indeksi tasir

MFT_FLAG_IN_USE = 0x0001
MFT_FLAG_DIRECTORY = 0x0002
MFT_FLAG_EXTEND = 0x0004           # $Extend icindeki sistem dosyasi
MFT_FLAG_VIEW_INDEX = 0x0008       # $I30 disinda indeks ($SDH, $O, $Q ...)

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


_UPCASE_INFO: Optional[bytes] = None


def _crc64_ntfs(data: bytes) -> int:
    """$UpCase:$Info'daki CRC64: yansitilmis, polinom 0x9A6C9329AC4BC9B5,
    baslangic ve son XOR tumu 1 (Windows'un bicimlendirdigi birimden
    olculdu: tests/fixtures/ntfs_windows.img.gz -> 0xdadc7e776b1b690c)."""
    table = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ 0x9A6C9329AC4BC9B5 if c & 1 else c >> 1
        table.append(c)
    crc = 0xFFFFFFFFFFFFFFFF
    for b in data:
        crc = table[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFFFFFFFFFF


def upcase_info() -> bytes:
    """`$UpCase:$Info` akisi (32 bayt): uzunluk, tablonun CRC64'u, surum.

    Windows 8 ve sonrasi bu akisi yazar; chkdsk tabloyu onunla dogrular.
    Akis yoksa Windows Server 2022 chkdsk "Errors detected in the uppercase
    file" der (uzun testler, CI, 2026-10-04). Surum alanlari Windows 10'un
    bicimlendirdigi birimdeki gibi sifirdir.
    """
    global _UPCASE_INFO
    if _UPCASE_INFO is None:
        _UPCASE_INFO = struct.pack("<IIQIIIHH", 32, 0, _crc64_ntfs(upcase_table()),
                                   0, 0, 0, 0, 0)
    return _UPCASE_INFO


# $LogFile (LFS 1.1) yeniden baslatma sayfasi. Coklu sektor korumasi NTFS'te
# fiziksel sektorden bagimsiz olarak her 512 baytta bir uygulanir.
LOG_PAGE_SIZE = 4096
NTFS_BLOCK_SIZE = 512
RESTART_VOLUME_IS_CLEAN = 0x0002
LFS_NO_CLIENT = 0xFFFF


def logfile_restart_page(log_size: int, open_count: int = 0) -> bytes:
    """Temiz, bos bir `$LogFile`in yeniden baslatma sayfasi ("RSTR", 4 KiB).

    Yalnizca 0xFF ile dolu ("bos") gunlugu Windows bagladiginda **yazarak**
    baslatir; salt okunur ortamda (yazma korumali kart, salt okunur VHD) birimi
    hic baglamaz — kok dizin "Yazma korumasi var" der (uzun testler, VM,
    2026-10-04, ADR 0085). Bu sayfa Windows 10'un ilk baglamada yazdigiyla alan
    alan aynidir; yalnizca LSN'ler sifirdir (kayit yok) ve "birim temiz"
    bayragi aciktir. Tek istemci: "NTFS".
    """
    page = bytearray(LOG_PAGE_SIZE)
    usa_count = LOG_PAGE_SIZE // NTFS_BLOCK_SIZE + 1
    page[0:4] = b"RSTR"
    # usa ofseti, sayisi, chkdsk LSN, sistem/gunluk sayfa boyu, alan ofseti, surum 1.1
    struct.pack_into("<HHQIIHhh", page, 4, 0x1E, usa_count, 0,
                     LOG_PAGE_SIZE, LOG_PAGE_SIZE, 0x30, 1, 1)
    area, client_off, client_len = 0x30, 0x40, 0xA0
    seq_bits = 64 - (log_size.bit_length() - 3)
    struct.pack_into("<QHHHHIHHqIHHI", page, area,
                     0,                         # current_lsn: kayit yok
                     1,                         # istemci sayisi
                     LFS_NO_CLIENT,             # bos istemci listesi
                     0,                         # kullanimdaki istemci: 0
                     RESTART_VOLUME_IS_CLEAN,
                     seq_bits, client_off + client_len, client_off, log_size,
                     0,                         # son kayit veri uzunlugu
                     48,                        # kayit basligi uzunlugu
                     0x40,                      # kayit sayfasi veri ofseti
                     open_count & 0xFFFFFFFF)
    client = area + client_off
    struct.pack_into("<QQHHH", page, client, 0, 0, LFS_NO_CLIENT, LFS_NO_CLIENT, 0)
    struct.pack_into("<I", page, client + 0x1C, 8)
    page[client + 0x20:client + 0x28] = "NTFS".encode("utf-16le")
    usn = 1
    struct.pack_into("<H", page, 0x1E, usn)
    for i in range(1, usa_count):
        end = i * NTFS_BLOCK_SIZE - 2
        page[0x1E + 2 * i:0x20 + 2 * i] = page[end:end + 2]
        struct.pack_into("<H", page, end, usn)
    return bytes(page)


def fill_logfile(write: Callable[[int, bytes], None],
                 runs: List[Tuple[int, int]], cluster_size: int,
                 log_size: int, open_count: int = 0,
                 progress: Optional[Callable[[int, int], None]] = None) -> None:
    """`$LogFile`i temiz ve bos hale getirir: iki yeniden baslatma sayfasi +
    geri kalani 0xFF.

    `write(bayt_ofseti, veri)` birime yazar; `runs` (lcn, kume) listesidir
    (gunluk parcali olabilir). `progress(yapilan, toplam)` kume sayar.
    """
    head = logfile_restart_page(log_size, open_count) * 2
    total = sum(count for lcn, count in runs if lcn >= 0) or 1
    chunk = max(1, (1024 * 1024) // cluster_size)
    vcn_bytes = done = 0
    for lcn, count in runs:
        if lcn < 0:
            vcn_bytes += count * cluster_size
            continue
        pos = 0
        while pos < count:
            n = min(chunk, count - pos)
            data = bytearray(b"\xFF" * (n * cluster_size))
            start = vcn_bytes + pos * cluster_size
            if start < len(head):
                part = head[start:start + len(data)]
                data[:len(part)] = part
            write((lcn + pos) * cluster_size, bytes(data))
            pos += n
            done += n
            if progress:
                progress(done, total)
        vcn_bytes += count * cluster_size


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
# --------------------------------------------------------------------------
# $Secure — guvenlik tanimlayicilari
# --------------------------------------------------------------------------
# NTFS 3.x'te her dosyanin bir **guvenlik kimligi** vardir; tanimlayicinin
# kendisi `$Secure` dosyasinda durur. Bicimlendirici bu dosyayi bos biraktiginda
# birim okunur ama isletim sisteminin surucusu uzerine **dosya olusturamaz**:
# yeni dosyaya kimlik atamak icin `$Secure` indekslerine bakar ve bulamayinca
# hata verir (olculdu: ntfs-3g "Invalid argument", ADR 0037).
#
# Asagidaki iki tanimlayici `mkntfs`in urettiklerinin aynisidir; baytlari
# referans birimden dogrulanmistir (bkz. tests t42).
SECURITY_ID_SYSTEM = 0x100      # SYSTEM + Administrators: okuma
SECURITY_ID_FULL = 0x101        # SYSTEM + Administrators: okuma + yazma
SDS_MIRROR = 0x40000            # $SDS icerigi 256 KiB'de bir aynalanir
ACCESS_READ = 0x00120089
ACCESS_MODIFY = 0x0012019F
COLLATION_ULONG = 0x10          # $SII: kimlige gore
COLLATION_SECURITY_HASH = 0x12  # $SDH: karmaya gore
COLLATION_SID = 0x11            # $Quota:$O — SID'e gore
COLLATION_ULONGS = 0x13         # $ObjId:$O, $Reparse:$R — ULONG dizisi
QUOTA_DEFAULT_OWNER = 1         # varsayilan kota girisi
QUOTA_ADMINS_OWNER = 0x100      # Administrators kota girisi
INDEX_ENTRY_END = 0x02


def _sid(authority: int, *subs: int) -> bytes:
    """S-1-<yetki>-<alt...> guvenlik kimligi (ikili bicim)."""
    out = bytearray([1, len(subs)])
    out += authority.to_bytes(6, "big")
    for value in subs:
        out += struct.pack("<I", value)
    return bytes(out)


SID_SYSTEM = _sid(5, 18)               # S-1-5-18  LOCAL SYSTEM
SID_ADMINS = _sid(5, 32, 544)          # S-1-5-32-544  BUILTIN\Administrators


def _allow_ace(mask: int, sid: bytes) -> bytes:
    """ACCESS_ALLOWED_ACE: verilen kimlige verilen haklar."""
    return struct.pack("<BBHI", 0, 0, 8 + len(sid), mask) + sid


def security_descriptor(mask: int) -> bytes:
    """Kendine goreli (self-relative) guvenlik tanimlayicisi.

    Sahibi ve grubu Administrators, DACL'i SYSTEM + Administrators. `mask`
    verilen haklardir (`ACCESS_READ` / `ACCESS_MODIFY`).
    """
    aces = _allow_ace(mask, SID_SYSTEM) + _allow_ace(mask, SID_ADMINS)
    acl = struct.pack("<BBHHH", 2, 0, 8 + len(aces), 2, 0) + aces
    owner_offset = 0x14 + len(acl)
    group_offset = owner_offset + len(SID_ADMINS)
    head = struct.pack("<BBHIIII", 1, 0, 0x8004,       # revizyon, SE_SELF_RELATIVE
                       owner_offset, group_offset, 0, 0x14)
    return head + acl + SID_ADMINS + SID_ADMINS


def security_hash(descriptor: bytes) -> int:
    """NTFS guvenlik tanimlayici karmasi (`$SDH` anahtarinin ilk yarisi)."""
    value = 0
    for (word,) in struct.iter_unpack("<I", descriptor):
        value = (word + (((value << 3) | (value >> 29)) & 0xFFFFFFFF)) & 0xFFFFFFFF
    return value


SECURITY_ID_ROOT = 0x102        # kok dizin: mkntfs kokuyle ayni (devralmali)
SID_AUTH_USERS = _sid(5, 11)           # S-1-5-11  Authenticated Users
SID_USERS = _sid(5, 32, 545)           # S-1-5-32-545  BUILTIN\Users


def root_security_descriptor() -> bytes:
    """Kok dizin tanimlayicisi — mkntfs'in koke koydugunun aynisi (olculdu).

    Yoneticiler/SYSTEM tam, kimligi dogrulanmis kullanicilar degistirme,
    Kullanicilar okuma/calistirma; her biri icin devralinacak (OI|CI|IO)
    genel hak kopyasi. Kokte tanimlayici olmazsa Windows'ta yeni ogeler
    erisilemez olur (ADR 0058).
    """
    aces = b""
    for flags, mask, sid in ((0x00, 0x1F01FF, SID_ADMINS), (0x0B, 0x10000000, SID_ADMINS),
                             (0x00, 0x1F01FF, SID_SYSTEM), (0x0B, 0x10000000, SID_SYSTEM),
                             (0x00, 0x1301BF, SID_AUTH_USERS),
                             (0x0B, 0xE0010000, SID_AUTH_USERS),
                             (0x00, 0x1200A9, SID_USERS), (0x0B, 0xA0000000, SID_USERS)):
        aces += struct.pack("<BBHI", 0, flags, 8 + len(sid), mask) + sid
    acl = struct.pack("<BBHHH", 2, 0, 8 + len(aces), 8, 0) + aces
    owner_offset = 0x14 + len(acl)
    group_offset = owner_offset + len(SID_SYSTEM)
    head = struct.pack("<BBHIIII", 1, 0, 0x8004, owner_offset, group_offset, 0, 0x14)
    return head + acl + SID_SYSTEM + SID_SYSTEM


def _sds_entries() -> List[Tuple[int, int, int, bytes]]:
    """(kimlik, karma, ofset, tanimlayici) — $SDS icindeki sirayla."""
    out = []
    offset = 0
    for sec_id, descriptor in ((SECURITY_ID_SYSTEM, security_descriptor(ACCESS_READ)),
                               (SECURITY_ID_FULL, security_descriptor(ACCESS_MODIFY)),
                               (SECURITY_ID_ROOT, root_security_descriptor())):
        out.append((sec_id, security_hash(descriptor), offset, descriptor))
        offset = (offset + 20 + len(descriptor) + 15) & ~15
    return out


def _sds_header(sec_id: int, hash_value: int, offset: int,
                descriptor: bytes) -> bytes:
    """$SDS giris basligi: karma, kimlik, akistaki ofset, toplam uzunluk."""
    return struct.pack("<IIQI", hash_value, sec_id, offset, 20 + len(descriptor))


def secure_sds_stream() -> bytes:
    """`$SDS` akisinin tamami (icerik + 256 KiB'deki aynasi).

    Windows akisi 256 KiB'lik bloklar halinde **iki kez** tutar; `chkdsk` ve
    `ntfs-3g` ikinci kopyayi bekler.
    """
    entries = _sds_entries()
    last = entries[-1]
    used = last[2] + 20 + len(last[3])       # son girisin bittigi yer
    block = bytearray(used)
    for sec_id, hash_value, offset, descriptor in entries:
        # Girisler 16 bayta hizali ofsetlerde durur ama akis **son girisin
        # bittigi yerde biter**: sonuna dolgu eklenmez (referans boyle).
        head = _sds_header(sec_id, hash_value, offset, descriptor)
        block[offset:offset + len(head)] = head
        block[offset + len(head):offset + len(head) + len(descriptor)] = descriptor
    stream = bytearray(SDS_MIRROR + used)
    stream[0:used] = block
    stream[SDS_MIRROR:SDS_MIRROR + used] = block
    return bytes(stream)


def _view_index_root(collation: int, entries: List[bytes],
                     index_size: int, cluster_size: int) -> bytes:
    """Dolu bir "gorunum indeksi" ($SDH / $SII) kokU.

    Dosya adi indekslerinden farki: giris basligi MFT basvurusu degil
    (veri ofseti, veri uzunlugu) tasir ve anahtarin yaninda **veri** durur.
    """
    body = b"".join(entries)
    end = struct.pack("<QHHHH", 0, 0x10, 0, INDEX_ENTRY_END, 0)
    body += end
    value = bytearray(0x20 + len(body))
    struct.pack_into("<IIIBBBB", value, 0, 0, collation, index_size,
                     max(1, index_size // cluster_size), 0, 0, 0)
    struct.pack_into("<IIII", value, 0x10, 0x10, 0x10 + len(body),
                     0x10 + len(body), 0)
    value[0x20:] = body
    return bytes(value)


def sdh_index_root(index_size: int, cluster_size: int) -> bytes:
    """`$SDH`: karma + kimlik anahtarli indeks (karmaya gore sirali)."""
    entries = []
    for sec_id, hash_value, offset, descriptor in sorted(
            _sds_entries(), key=lambda item: (item[1], item[0])):
        key = struct.pack("<II", hash_value, sec_id)
        data = _sds_header(sec_id, hash_value, offset, descriptor)
        # 0x18: anahtardan sonra 8 bayta hizalanmis veri; sonundaki "II"
        # dolgusu referans bicimin parcasidir.
        entry = bytearray(0x30)
        struct.pack_into("<HHIHHHH", entry, 0, 0x18, len(data), 0,
                         0x30, len(key), 0, 0)
        entry[0x10:0x10 + len(key)] = key
        entry[0x18:0x18 + len(data)] = data
        entry[0x2C:0x30] = b"I\x00I\x00"
        entries.append(bytes(entry))
    return _view_index_root(COLLATION_SECURITY_HASH, entries, index_size,
                            cluster_size)


def sii_index_root(index_size: int, cluster_size: int) -> bytes:
    """`$SII`: kimlik anahtarli indeks (kimlige gore sirali)."""
    entries = []
    for sec_id, hash_value, offset, descriptor in sorted(
            _sds_entries(), key=lambda item: item[0]):
        key = struct.pack("<I", sec_id)
        data = _sds_header(sec_id, hash_value, offset, descriptor)
        entry = bytearray(0x28)
        struct.pack_into("<HHIHHHH", entry, 0, 0x14, len(data), 0,
                         0x28, len(key), 0, 0)
        entry[0x10:0x10 + len(key)] = key
        entry[0x14:0x14 + len(data)] = data
        entries.append(bytes(entry))
    return _view_index_root(COLLATION_ULONG, entries, index_size, cluster_size)


def _view_entry(key: bytes, data: bytes) -> bytes:
    """Gorunum indeksi girisi: veri anahtarin hemen ardinda, giris 8'e hizali."""
    data_offset = 0x10 + len(key)
    length = (data_offset + len(data) + 7) & ~7
    entry = bytearray(length)
    struct.pack_into("<HHIHHHH", entry, 0, data_offset, len(data), 0,
                     length, len(key), 0, 0)
    entry[0x10:0x10 + len(key)] = key
    entry[data_offset:data_offset + len(data)] = data
    return bytes(entry)


def quota_o_root(index_size: int, cluster_size: int) -> bytes:
    """`$Quota:$O`: SID -> kota sahibi kimligi (mkntfs: Administrators)."""
    entry = _view_entry(SID_ADMINS, struct.pack("<I", QUOTA_ADMINS_OWNER))
    return _view_index_root(COLLATION_SID, [entry], index_size, cluster_size)


def quota_q_root(index_size: int, cluster_size: int, now: int) -> bytes:
    """`$Quota:$Q`: sahip kimligi -> QUOTA_CONTROL_ENTRY.

    Surum 2, bayrak 1 (varsayilan sinirlar), kullanilan 0, esik ve sinir -1
    (sinirsiz). Administrators girisi sonunda SID'i tasir.
    """
    def control(sid: bytes = b"") -> bytes:
        return struct.pack("<IIQQqqQ", 2, 1, 0, now, -1, -1, 0) + sid
    entries = [
        _view_entry(struct.pack("<I", QUOTA_DEFAULT_OWNER), control()),
        _view_entry(struct.pack("<I", QUOTA_ADMINS_OWNER), control(SID_ADMINS)),
    ]
    return _view_index_root(COLLATION_ULONG, entries, index_size, cluster_size)


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
    attrdef_clusters: int
    bitmap_lcn: int
    bitmap_clusters: int
    root_index_lcn: int
    secure_lcn: int
    secure_clusters: int = 1
    mft_bitmap_lcn: int = 0
    boot_clusters: int = 2


class NtfsFormatter:
    """NTFS birimi olusturur."""

    def __init__(self, dev: BlockDevice, label: str = "", cluster_size: int = 0,
                 partition_offset: int = 0):
        self.dev = dev
        self.label = label[:32]
        # BPB "gizli sektor": bolumun diskteki baslangici. Windows'un NTFS
        # onyukleme kodu bunu okur; 0 kalirsa birim baslatilamaz.
        self.partition_offset = partition_offset
        self.sector_size = dev.sector_size
        self.cluster_size = cluster_size or self._default_cluster()
        if self.cluster_size % self.sector_size:
            raise NtfsError(tr("Kume boyutu sektor boyutunun kati olmalidir"))
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
            raise NtfsError(tr("Bolum NTFS icin cok kucuk"))

        def clusters(nbytes: int) -> int:
            return max(1, (nbytes + cs - 1) // cs)

        mft_record_count = 64                       # 16 sistem + 16-23 ayrilmis + bos
        mft_cluster = clusters(mft_record_count * MFT_RECORD_SIZE)
        logfile_bytes = max(2 * 1024 * 1024, min(64 * 1024 * 1024,
                                                self.dev.size // 100))
        logfile_cluster = clusters(logfile_bytes)
        upcase_cluster = clusters(128 * 1024)
        attrdef_cluster = clusters(2560)
        bitmap_bytes = (total_clusters + 7) // 8
        bitmap_cluster = clusters(bitmap_bytes)

        # $Boot 8 KiB'tir: 4 KiB kumede 2, 1 KiB'te 8 kume. Eskiden kume 0-3
        # sabit "dolu" isaretlenip $MFT 4'ten baslatiliyordu: 4 KiB'te 2-3
        # sahipsiz doluydu (ntfsresize "extra cluster in $Bitmap" deyip
        # reddediyordu), 1 KiB ve altinda $Boot ile $MFT ayni kumeleri
        # paylasiyordu (ADR 0074). Bitmap'te yalnizca $Boot'un kumeleri
        # isaretlenir; $MFT ondan once baslamaz (4 KiB'te yerlesim ayni).
        boot_clusters = clusters(BOOT_BYTES)
        imlec = max(4, boot_clusters)
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
        # $SDS 256 KiB aynalama yuzunden tek kumeden buyuktur
        secure_cluster = clusters(len(secure_sds_stream()))
        imlec += secure_cluster
        # $MFT'nin kendi $BITMAP'i **ayri bir kumede** durur; yerlesik
        # birakilirsa isletim sisteminin surucusu yeni MFT kaydi ayiramaz
        # (ADR 0037).
        mft_bitmap_lcn = imlec
        imlec += 1
        if imlec >= total_clusters - 2:
            raise NtfsError(tr("Bolum NTFS metaverisi icin yetersiz"))
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
            attrdef_clusters=attrdef_cluster,
            bitmap_lcn=bitmap_lcn, bitmap_clusters=bitmap_cluster,
            root_index_lcn=root_index_lcn, secure_lcn=secure_lcn,
            secure_clusters=secure_cluster, mft_bitmap_lcn=mft_bitmap_lcn,
            boot_clusters=boot_clusters)

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

    def _std_info(self, file_attr: int = FILE_ATTR_HIDDEN | FILE_ATTR_SYSTEM,
                  security_id: Optional[int] = None) -> bytes:
        """$STANDARD_INFORMATION.

        `security_id` verilmezse **48 baytlik** (NTFS 1.2) bicim yazilir;
        `mkntfs` de $Volume, $AttrDef, $Boot ve kok dizin icin boyle yapar ve
        surucu o dosyalar icin baglama seceneklerine duser. Kimlik verilirse
        72 baytlik NTFS 3.x bicimi yazilir ve kimlik `$Secure` icindeki bir
        tanimlayiciyi gosterir (ADR 0037).
        """
        if security_id is None:
            value = bytearray(48)
            struct.pack_into("<QQQQ", value, 0, self.now, self.now, self.now,
                             self.now)
            struct.pack_into("<I", value, 32, file_attr)
            return bytes(value)
        value = bytearray(72)
        struct.pack_into("<QQQQ", value, 0, self.now, self.now, self.now, self.now)
        struct.pack_into("<I", value, 0x20, file_attr)
        # Guvenlik kimligi 0x34'tedir; 0x40 USN alanidir. Referans birimde
        # olculdu (t42 bu ofseti dogrular).
        struct.pack_into("<I", value, 0x34, security_id)
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
        """0-23 kayitlarinda sira numarasi kayit numarasina esittir; 0 ve
        24'ten sonrakiler 1 (mkntfs ile ayni).

        MFT basvurulari (mref) sira numarasini ust 16 bitte tasir; yanlis sira
        numarasi `ntfs_inode_open` tarafindan "dosya yok" olarak reddedilir.
        """
        return rec_no if 0 < rec_no < MFT_QUOTA else 1

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
                raise NtfsError(tr("MFT kaydi {} tasti", rec_no))
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

        report(tr("NTFS yerlesimi hazirlaniyor..."), 5)
        report(tr("Sabit tablolar yaziliyor ($UpCase, $AttrDef)..."), 15)
        self._write_clusters(L.upcase_lcn, upcase_table())
        self._write_clusters(L.attrdef_lcn, attrdef_table())

        report(tr("Islem gunlugu ($LogFile) hazirlaniyor..."), 30)
        self._write_logfile()

        report(tr("Kok dizin olusturuluyor..."), 45)
        self._write_root_index()

        report(tr("Guvenlik tanimlayicilari yaziliyor ($Secure)..."), 55)
        self._write_secure()
        self._write_mft_bitmap()

        report(tr("Kume bitmap'i yaziliyor..."), 60)
        self._write_bitmap()

        report(tr("MFT kayitlari olusturuluyor..."), 70)
        records = self._build_mft_records()
        self._write_mft(records)

        report(tr("MFT yedegi yaziliyor..."), 85)
        self._write_mftmirr(records)

        report(tr("Onyukleme sektoru yaziliyor..."), 95)
        self._write_boot()

        f = getattr(self.dev, "flush", None)
        if f:
            f()
        report(tr("Tamamlandi"), 100)
        return {
            "cluster_size": L.cluster_size, "clusters": L.total_clusters,
            "mft_lcn": L.mft_lcn, "mft_clusters": L.mft_clusters,
            "logfile_clusters": L.logfile_clusters,
            "serial": f"{self.serial:016X}", "label": self.label,
        }

    # ---- veri alanlari ---------------------------------------------------
    def _mft_bitmap(self) -> bytes:
        """`$MFT` kayit kullanim bitmap'i.

        Boyut **8 baytin kati** olur (NTFS boyle bekler) ve yalnizca sistem
        kayitlari dolu isaretlenir; gerisi isletim sistemine acik durur.
        """
        records = max(1, self.layout.mft_clusters * self.layout.cluster_size
                      // MFT_RECORD_SIZE)
        size = max(8, ((records + 63) // 64) * 8)
        bitmap = bytearray(size)
        for i in list(range(MFT_RESERVED_COUNT)) + [no for no, _ in EXTEND_FILES]:
            bitmap[i >> 3] |= 1 << (i & 7)
        return bytes(bitmap)

    def _write_mft_bitmap(self) -> None:
        """`$MFT:$BITMAP` akisini kendi kumesine yazar.

        Yerlesik birakilirsa `ntfs-3g` yeni dosya olustururken bitmap'in son
        kumesini soruyor ve bulamayinca duruyordu: *"Failed to determine last
        allocated cluster of mft bitmap attribute"* -> EINVAL (ADR 0037).
        """
        self._write_clusters(self.layout.mft_bitmap_lcn, self._mft_bitmap())

    def _write_secure(self) -> None:
        """`$Secure:$SDS` akisini diske yazar.

        Bos birakilirsa birim okunur ama isletim sisteminin surucusu uzerine
        dosya olusturamaz (ADR 0037).
        """
        self._write_clusters(self.layout.secure_lcn, secure_sds_stream())

    def _write_logfile(self) -> None:
        """$LogFile: temiz yeniden baslatma alani + bos gunluk (ADR 0085)."""
        L = self.layout
        fill_logfile(self.dev.write, [(L.logfile_lcn, L.logfile_clusters)],
                     L.cluster_size, L.logfile_clusters * L.cluster_size,
                     open_count=self.serial)

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
        # Kok dizinin **kendi girisi** ("." -> kayit 5) de listeye girer.
        # Isletim sisteminin surucusu bir dosya olusturduktan sonra ust
        # dizinin adini kendi indeksinde arayip tazeler; kok icin bu arama
        # "." girisine dusur. Giris yoksa olusturma yarida kalir:
        # *"Index lookup failed, inode 5"* -> G/C hatasi (ADR 0037).
        # Okuyucumuz "." girisini zaten listelemez (`ntfsread`).
        sirali = sorted(self.SYSTEM_FILES,
                        key=lambda oge: self._collation_key(oge[1]))
        for rec_no, name in sirali:
            feature = FILE_ATTR_HIDDEN | FILE_ATTR_SYSTEM
            if rec_no in (MFT_EXTEND, MFT_ROOT):
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
            (0, L.boot_clusters),                     # $Boot (8 KiB)
            (L.mft_lcn, L.mft_clusters),
            (L.logfile_lcn, L.logfile_clusters),
            (L.upcase_lcn, L.upcase_clusters),
            (L.attrdef_lcn, L.attrdef_clusters),
            (L.bitmap_lcn, L.bitmap_clusters),
            (L.root_index_lcn, max(1, INDEX_RECORD_SIZE // L.cluster_size)),
            (L.secure_lcn, L.secure_clusters),
            (L.mft_bitmap_lcn, 1),
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

        # 0: $MFT — bitmap'i AYRI KUMEDE durur (bkz. _mft_bitmap)
        mft_size = L.mft_clusters * cs
        records.append(self._make_record(MFT_MFT, MFT_FLAG_IN_USE, [
            self._attr_resident(AT_STANDARD_INFORMATION,
                                self._std_info(security_id=SECURITY_ID_SYSTEM)),
            system_file(MFT_MFT, "$MFT", mft_size, mft_size),
            self._attr_nonresident(AT_DATA, [(L.mft_lcn, L.mft_clusters)],
                                   mft_size, attr_id=1),
            self._attr_nonresident(AT_BITMAP, [(L.mft_bitmap_lcn, 1)],
                                   len(self._mft_bitmap()), attr_id=2),
        ]))

        # 1: $MFTMirr
        mirr_size = 4 * MFT_RECORD_SIZE
        records.append(self._make_record(MFT_MFTMIRR, MFT_FLAG_IN_USE, [
            self._attr_resident(AT_STANDARD_INFORMATION,
                                self._std_info(security_id=SECURITY_ID_SYSTEM)),
            system_file(MFT_MFTMIRR, "$MFTMirr", mirr_size,
                      L.mftmirr_clusters * cs),
            self._attr_nonresident(AT_DATA,
                                   [(L.mftmirr_lcn, L.mftmirr_clusters)],
                                   mirr_size, attr_id=1),
        ]))

        # 2: $LogFile
        log_size = L.logfile_clusters * cs
        records.append(self._make_record(MFT_LOGFILE, MFT_FLAG_IN_USE, [
            self._attr_resident(AT_STANDARD_INFORMATION,
                                self._std_info(security_id=SECURITY_ID_SYSTEM)),
            system_file(MFT_LOGFILE, "$LogFile", log_size, log_size),
            self._attr_nonresident(AT_DATA, [(L.logfile_lcn, L.logfile_clusters)],
                                   log_size, attr_id=1),
        ]))

        # 3: $Volume
        volume_info = struct.pack("<QBBH", 0, 3, 1, 0)   # NTFS 3.1, bayrak yok
        volume_attrs = [
            self._attr_resident(AT_STANDARD_INFORMATION,
                                self._std_info(security_id=SECURITY_ID_SYSTEM)),
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
            self._attr_resident(AT_STANDARD_INFORMATION,
                                self._std_info(security_id=SECURITY_ID_SYSTEM)),
            # 2560 bayt: 4 KiB altindaki kumelerde birden cok kume (eskiden
            # tek kume yaziliyordu, ntfs-3g "unexpected length" diyordu).
            system_file(MFT_ATTRDEF, "$AttrDef", len(attrdef),
                        L.attrdef_clusters * cs),
            self._attr_nonresident(AT_DATA, [(L.attrdef_lcn, L.attrdef_clusters)],
                                   len(attrdef),
                                   attr_id=1),
        ]))

        # 5: kok dizin
        records.append(self._make_record(MFT_ROOT,
                                          MFT_FLAG_IN_USE | MFT_FLAG_DIRECTORY,
                                          self._root_attrs()))

        # 6: $Bitmap
        bitmap_size = (L.total_clusters + 7) // 8
        records.append(self._make_record(MFT_BITMAP, MFT_FLAG_IN_USE, [
            self._attr_resident(AT_STANDARD_INFORMATION,
                                self._std_info(security_id=SECURITY_ID_SYSTEM)),
            system_file(MFT_BITMAP, "$Bitmap", bitmap_size,
                      L.bitmap_clusters * cs),
            self._attr_nonresident(AT_DATA, [(L.bitmap_lcn, L.bitmap_clusters)],
                                   bitmap_size, attr_id=1),
        ]))

        # 7: $Boot
        records.append(self._make_record(MFT_BOOT, MFT_FLAG_IN_USE, [
            self._attr_resident(AT_STANDARD_INFORMATION,
                                self._std_info(security_id=SECURITY_ID_SYSTEM)),
            system_file(MFT_BOOT, "$Boot", BOOT_BYTES, L.boot_clusters * cs),
            self._attr_nonresident(AT_DATA, [(0, L.boot_clusters)], BOOT_BYTES,
                                   attr_id=1),
        ]))

        # 8: $BadClus — seyrek, birim boyutunda
        volume_size = L.total_clusters * cs
        records.append(self._make_record(MFT_BADCLUS, MFT_FLAG_IN_USE, [
            self._attr_resident(AT_STANDARD_INFORMATION,
                                self._std_info(security_id=SECURITY_ID_SYSTEM)),
            system_file(MFT_BADCLUS, "$BadClus"),
            self._attr_resident(AT_DATA, b"", attr_id=1),
            self._bad_stream(volume_size),
        ]))

        # 9: $Secure — $SDS akisi ve iki dizin ($SDH karma, $SII kimlik).
        # ntfs-3g bu iki dizini arar; yoksa birimi acamaz.
        # 0x08: kayit bir "gorunum indeksi" tasir ($SDH/$SII)
        sds = secure_sds_stream()
        records.append(self._make_record(MFT_SECURE, MFT_FLAG_IN_USE | 0x08, [
            self._attr_resident(AT_STANDARD_INFORMATION,
                                self._std_info(security_id=SECURITY_ID_FULL)),
            system_file(MFT_SECURE, "$Secure"),
            self._attr_nonresident(AT_DATA,
                                   [(L.secure_lcn, L.secure_clusters)],
                                   len(sds), name="$SDS", attr_id=1),
            self._attr_resident(AT_INDEX_ROOT,
                                sdh_index_root(INDEX_RECORD_SIZE, cs),
                                name="$SDH", attr_id=2),
            self._attr_resident(AT_INDEX_ROOT,
                                sii_index_root(INDEX_RECORD_SIZE, cs),
                                name="$SII", attr_id=3),
        ]))

        # 10: $UpCase
        upcase_size = 128 * 1024
        records.append(self._make_record(MFT_UPCASE, MFT_FLAG_IN_USE, [
            self._attr_resident(AT_STANDARD_INFORMATION,
                                self._std_info(security_id=SECURITY_ID_SYSTEM)),
            system_file(MFT_UPCASE, "$UpCase", upcase_size,
                      L.upcase_clusters * cs),
            self._attr_nonresident(AT_DATA, [(L.upcase_lcn, L.upcase_clusters)],
                                   upcase_size, attr_id=1),
            self._attr_resident(AT_DATA, upcase_info(), name="$Info", attr_id=2),
        ]))

        # 11: $Extend — $Quota/$ObjId/$Reparse dizini
        records.append(self._make_record(MFT_EXTEND,
                                          MFT_FLAG_IN_USE | MFT_FLAG_DIRECTORY, [
            self._attr_resident(AT_STANDARD_INFORMATION,
                                self._std_info(FILE_ATTR_HIDDEN | FILE_ATTR_SYSTEM,
                                               security_id=SECURITY_ID_FULL)),
            system_file(MFT_EXTEND, "$Extend", is_dir=True),
            self._attr_resident(AT_INDEX_ROOT, self._extend_index_root(),
                                name="$I30", attr_id=1),
        ]))

        # 12..15: ayrilmis kayitlar.
        # Bunlarda $FILE_NAME BULUNMAZ — kok dizinde listelenmezler. Ad eklemek
        # `chkdsk` tarafindan "Attribute record (30) is corrupt" olarak bildirilir.
        for no in range(12, MFT_RESERVED_COUNT):
            # Bag sayisi 0: hicbir dizinde adlari yok (mkntfs/Windows ayni)
            records.append(self._make_record(no, MFT_FLAG_IN_USE, [
                self._attr_resident(AT_STANDARD_INFORMATION,
                                    self._std_info(security_id=SECURITY_ID_SYSTEM)),
                self._attr_resident(AT_DATA, b"", attr_id=1),
            ], baglanti=0))
        # 16..: bos (kullanimda olmayan) FILE kayitlari. mkntfs ve Windows
        # MFT'deki her kaydi bicimli yazar; sifir kayit Windows'ta "bozuk
        # kayit" sayilir (ADR 0058).
        total = L.mft_clusters * cs // MFT_RECORD_SIZE
        extend = self._extend_records()
        for no in range(MFT_RESERVED_COUNT, total):
            records.append(extend.get(no) or self._make_record(no, 0, [], baglanti=0))
        return records

    def _extend_file_attrs(self, rec_no: int, name: str) -> List[bytes]:
        """$Extend alt dosyasinin $STANDARD_INFORMATION + $FILE_NAME'i."""
        feature = (FILE_ATTR_HIDDEN | FILE_ATTR_SYSTEM | FILE_ATTR_ARCHIVE
                   | FILE_ATTR_VIEW_INDEX)
        return [
            self._attr_resident(AT_STANDARD_INFORMATION,
                                self._std_info(feature,
                                               security_id=SECURITY_ID_FULL)),
            self._attr_resident(AT_FILE_NAME,
                                self._file_name(MFT_EXTEND, name, feature),
                                indexed=1),
        ]

    def _extend_records(self) -> Dict[int, bytes]:
        """24-26: $Quota ($O, $Q), $ObjId ($O), $Reparse ($R) — bos gorunum
        indeksleri (kota girisleri mkntfs'in varsayilanlari)."""
        cs = self.layout.cluster_size
        bayrak = MFT_FLAG_IN_USE | MFT_FLAG_EXTEND | MFT_FLAG_VIEW_INDEX
        indeksler = {
            MFT_QUOTA: [("$O", quota_o_root(INDEX_RECORD_SIZE, cs)),
                        ("$Q", quota_q_root(INDEX_RECORD_SIZE, cs, self.now))],
            MFT_OBJID: [("$O", _view_index_root(COLLATION_ULONGS, [],
                                                 INDEX_RECORD_SIZE, cs))],
            MFT_REPARSE: [("$R", _view_index_root(COLLATION_ULONGS, [],
                                                   INDEX_RECORD_SIZE, cs))],
        }
        out: Dict[int, bytes] = {}
        for rec_no, name in EXTEND_FILES:
            attrs = self._extend_file_attrs(rec_no, name)
            for i, (index_name, value) in enumerate(indeksler[rec_no], 1):
                attrs.append(self._attr_resident(AT_INDEX_ROOT, value,
                                                 name=index_name, attr_id=i))
            out[rec_no] = self._make_record(rec_no, bayrak, attrs)
        return out

    def _extend_index_root(self) -> bytes:
        """$Extend:$I30 — alt dosyalarin girisleri, ada gore sirali."""
        girisler = bytearray()
        feature = (FILE_ATTR_HIDDEN | FILE_ATTR_SYSTEM | FILE_ATTR_ARCHIVE
                   | FILE_ATTR_VIEW_INDEX)
        for rec_no, name in sorted(EXTEND_FILES,
                                   key=lambda oge: self._collation_key(oge[1])):
            key = self._file_name(MFT_EXTEND, name, feature)
            girisler += _index_entry(rec_no, key, sira=self._sequence(rec_no))
        girisler += _index_end_entry()
        value = bytearray(0x20 + len(girisler))
        struct.pack_into("<IIIBBBB", value, 0, AT_FILE_NAME, 1, INDEX_RECORD_SIZE,
                         max(1, INDEX_RECORD_SIZE // self.layout.cluster_size),
                         0, 0, 0)
        struct.pack_into("<IIII", value, 0x10, 0x10, 0x10 + len(girisler),
                         0x10 + len(girisler), 0)
        value[0x20:] = girisler
        return bytes(value)

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
        """Kok dizinin oznitelikleri — mkntfs gibi INDX'li ("buyuk") dizin.

        1 KiB MFT kaydina sistem dosyalarinin girisleri sigmaz; kok, girisleri
        `root_index_lcn`deki INDX blogunda tasir: $INDEX_ROOT yalnizca o bloga
        isaret eden END girisi, yaninda $INDEX_ALLOCATION ve $BITMAP:$I30.
        Guvenlik kimligi SECURITY_ID_ROOT (Windows'ta erisim icin sart).
        """
        L = self.layout
        girisler = _index_end_entry(child_vcn=0)
        root_value = bytearray(0x20 + len(girisler))
        struct.pack_into("<IIIBBBB", root_value, 0, AT_FILE_NAME, 1,
                         INDEX_RECORD_SIZE,
                         max(1, INDEX_RECORD_SIZE // L.cluster_size), 0, 0, 0)
        struct.pack_into("<IIII", root_value, 0x10, 0x10, 0x10 + len(girisler),
                         0x10 + len(girisler), 1)     # bayrak 1 = alt dugum var
        root_value[0x20:] = girisler
        index_clusters = max(1, INDEX_RECORD_SIZE // L.cluster_size)
        return [
            self._attr_resident(AT_STANDARD_INFORMATION,
                                self._std_info(FILE_ATTR_HIDDEN | FILE_ATTR_SYSTEM,
                                               security_id=SECURITY_ID_ROOT)),
            self._attr_resident(
                AT_FILE_NAME,
                self._file_name(MFT_ROOT, ".",
                                FILE_ATTR_HIDDEN | FILE_ATTR_SYSTEM
                                | FILE_ATTR_DIRECTORY),
                indexed=1),
            self._attr_resident(AT_INDEX_ROOT, bytes(root_value), name="$I30",
                                attr_id=1),
            self._attr_nonresident(AT_INDEX_ALLOCATION,
                                   [(L.root_index_lcn, index_clusters)],
                                   index_clusters * L.cluster_size, name="$I30",
                                   attr_id=2),
            self._attr_resident(AT_BITMAP, b"\x01" + bytes(7), name="$I30",
                                attr_id=3),
        ]

    def _write_mft(self, records: List[bytes]) -> None:
        L = self.layout
        veri = bytearray(L.mft_clusters * L.cluster_size)
        for i, record in enumerate(records):
            ofset = i * MFT_RECORD_SIZE
            if ofset + MFT_RECORD_SIZE > len(veri):
                raise NtfsError(tr("MFT alani yetersiz"))
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
                         0xF8, 0, 63, 255,
                         self.partition_offset & 0xFFFFFFFF, 0)
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
        # Ayni kural dizin kaydi icin: 4 KiB alti kumede 4096 // kume (512
        # baytta 8). Eskiden her zaman 1 yaziliyordu; okuyucu dizin kaydini
        # kume boyunda (512/1024/2048) saniyor ve dizinleri okuyamiyordu.
        if INDEX_RECORD_SIZE >= L.cluster_size:
            struct.pack_into("<b", boot, 0x44, INDEX_RECORD_SIZE // L.cluster_size)
        else:
            struct.pack_into("<b", boot, 0x44,
                             -(INDEX_RECORD_SIZE.bit_length() - 1))
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
                progress: Optional[Callable[[str, int], None]] = None,
                partition_offset: int = 0) -> Dict:
    """Kisayol: verilen aygiti NTFS olarak bicimlendirir."""
    return _NtfsBuilder(dev, label=label, cluster_size=cluster_size,
                        partition_offset=partition_offset).format(progress)
