"""UDF 2.01 bicimlendirme (sabit disk yerlesimi) — saf Python.

Yerlesim `mkudffs -m hd -r 2.01` (udftools 2.3) ciktisi olculerek cikarildi
(ADR 0064); blok boyu aygitin sektor boyudur (512 / 4096; Windows UDF'yi
sektor boyuyla ayni blokla bekler):

    32 KiB            birim tanima dizisi: BEA01, NSR03, TEA01
    96  (16 blok)     ana birim tanimlayici dizisi (PVD, LVD, PD, USD, IUVD, TD)
    128 (8 KiB)       mantiksal birim butunluk dizisi (LVID + TD)
    256               capa (AVDP)
    257 ... N-264     bolum: alan bitmap'i, dosya kumesi (FSD), sistem akis
                      dizini, kok dizin (EFE, FID'ler girisin icinde)
    N-257             capa
    N-160 (16 blok)   yedek birim tanimlayici dizisi
    N-1               capa

Tum etiketler surum 3 (NSR03), CRC-16 (CCITT, 0x1021) ve saglama baytiyla.
"""
from __future__ import annotations

import datetime
import os
import struct
from typing import Callable, Dict, List, Optional, Tuple

from .image import BlockDevice
from ..i18n import tr

UDF_REVISION = 0x0201
IMPL_ID = b"*DiskUltimate"
APP_ID = b"*DiskUltimate"
DOMAIN_ID = b"*OSTA UDF Compliant"
CHARSET = b"OSTA Compressed Unicode"
TAG_VERSION = 3
MVDS_BLOCK = 96
VDS_BLOCKS = 16
LVID_BLOCK = 128
ANCHOR = 256
PARTITION_START = 257
RESERVED_TAIL = 263                         # bolum sonu ile birim sonu arasi
MIN_BLOCKS = 1024


class UdfFormatError(Exception):
    pass


def _crc_table() -> List[int]:
    table = []
    for n in range(256):
        crc = n << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
        table.append(crc)
    return table


_CRC_TABLE = _crc_table()


def crc16(data: bytes) -> int:
    """CRC-16 CCITT (0x1021, baslangic 0) — ECMA-167 etiket CRC'si."""
    crc = 0
    table = _CRC_TABLE
    for byte in data:
        crc = ((crc << 8) & 0xFFFF) ^ table[(crc >> 8) ^ byte]
    return crc


def make_tag(body: bytearray, ident: int, location: int, crc_len: Optional[int] = None,
             serial: int = 1) -> bytes:
    """`body` tum tanimlayicidir (ilk 16 bayt etiket icin ayrilmis)."""
    if crc_len is None:
        crc_len = len(body) - 16
    crc = crc16(bytes(body[16:16 + crc_len]))
    struct.pack_into("<HHBBHHHI", body, 0, ident, TAG_VERSION, 0, 0, serial,
                     crc, crc_len, location)
    body[4] = sum(body[i] for i in range(16) if i != 4) & 0xFF
    return bytes(body)


def regid(ident: bytes, suffix: bytes = b"") -> bytes:
    out = bytearray(32)
    out[1:1 + len(ident)] = ident[:23]
    out[24:24 + len(suffix)] = suffix[:8]
    return bytes(out)


def charspec() -> bytes:
    return b"\x00" + CHARSET.ljust(63, b"\x00")


def cs0_encode(text: str) -> bytes:
    """OSTA CS0: tum karakterler Latin-1'e sigiyorsa 8, degilse 16 bit."""
    try:
        return b"\x08" + text.encode("latin-1")
    except UnicodeEncodeError:
        return b"\x10" + text.encode("utf-16-be")


def dstring(text: str, size: int) -> bytes:
    """Sabit boylu d-string (son bayt uzunluk); sigmayan kisim kesilir."""
    raw = cs0_encode(text) if text else b""
    if raw:
        unit = 2 if raw[0] == 16 else 1
        room = size - 1
        if len(raw) > room:
            raw = raw[:1 + ((room - 1) // unit) * unit]
    out = bytearray(size)
    out[:len(raw)] = raw
    if raw:
        out[size - 1] = len(raw)
    return bytes(out)


def timestamp(when: Optional[datetime.datetime] = None) -> bytes:
    when = when or datetime.datetime.now().astimezone()
    offset = int(when.utcoffset().total_seconds() // 60) if when.utcoffset() else 0
    type_tz = (1 << 12) | (offset & 0x0FFF)
    cs, rest = divmod(when.microsecond, 10000)
    hus, us = divmod(rest, 100)
    return struct.pack("<HhBBBBBBBB", type_tz, when.year, when.month, when.day,
                       when.hour, when.minute, when.second, cs, hus, us)


def udf_permissions(mode: int) -> int:
    """POSIX rwx -> UDF izinleri (diger 0-4, grup 5-9, sahip 10-14)."""
    out = 0
    for shift_posix, shift_udf in ((0, 0), (3, 5), (6, 10)):
        bits = (mode >> shift_posix) & 7
        if bits & 1:
            out |= 1 << shift_udf               # calistir
        if bits & 2:
            out |= 1 << (shift_udf + 1)          # yaz
        if bits & 4:
            out |= 1 << (shift_udf + 2)          # oku
    return out


class _Layout:
    def __init__(self, blocks: int, bs: int):
        self.bs = bs
        self.blocks = blocks
        self.vrs_step = max(2048, bs)
        self.vrs_first = 32768 // bs
        self.vrs_blocks = 3 * max(1, 2048 // bs) + 1
        self.lvid_blocks = max(1, 8192 // bs)
        self.part_start = PARTITION_START
        self.part_len = blocks - PARTITION_START - RESERVED_TAIL
        self.anchor2 = blocks - 257
        self.rvds = blocks - 160
        self.anchor3 = blocks - 1
        self.bitmap_bytes = (self.part_len + 7) // 8
        self.sbd_blocks = (24 + self.bitmap_bytes + bs - 1) // bs
        self.fsd = self.sbd_blocks                  # bolume gore
        self.stream_dir = self.fsd + 1
        self.root = self.fsd + 2
        self.used = self.root + 1

    def erase_extents(self) -> List[Tuple[int, int]]:
        """(blok, blok sayisi) — USD'nin "ayrilmamis alan" listesi."""
        b = self.blocks
        vrs_end = self.vrs_first + self.vrs_blocks
        lvid_end = LVID_BLOCK + self.lvid_blocks
        return [(vrs_end, MVDS_BLOCK - vrs_end),
                (MVDS_BLOCK + VDS_BLOCKS, LVID_BLOCK - MVDS_BLOCK - VDS_BLOCKS),
                (lvid_end, ANCHOR - lvid_end),
                (b - RESERVED_TAIL, 6),
                (b - 256, 96),
                (b - 144, 143)]


class UdfFormatter:
    def __init__(self, dev: BlockDevice, label: str = "", block_size: int = 0,
                 uuid: str = "", now: Optional[datetime.datetime] = None):
        self.dev = dev
        self.bs = block_size or getattr(dev, "sector_size", 512)
        if self.bs not in (512, 1024, 2048, 4096):
            raise UdfFormatError(tr("UDF blok boyu desteklenmiyor: {}", self.bs))
        blocks = dev.size // self.bs
        if blocks < MIN_BLOCKS:
            raise UdfFormatError(tr("Birim UDF icin cok kucuk"))
        if blocks > 0xFFFFFFFF:
            raise UdfFormatError(tr("Birim UDF icin cok buyuk"))
        self.L = _Layout(blocks, self.bs)
        self.label = label or "UDF Volume"
        self.uuid = (uuid or os.urandom(8).hex())[:16]
        self.now = now or datetime.datetime.now().astimezone()
        self.stamp = timestamp(self.now)
        self.impl = regid(IMPL_ID)

    # ---- tanimlayicilar -------------------------------------------------
    def pvd(self, loc: int) -> bytes:
        d = bytearray(512)
        struct.pack_into("<II", d, 16, 1, 0)
        d[24:56] = dstring(self.label, 32)
        struct.pack_into("<HHHHII", d, 56, 1, 1, 2, 3, 1, 1)
        d[72:200] = dstring(self.uuid + "DiskUltimate", 128)
        d[200:264] = charspec()
        d[264:328] = charspec()
        d[344:376] = regid(APP_ID)
        d[376:388] = self.stamp
        d[388:420] = self.impl
        struct.pack_into("<H", d, 488, 1)
        return make_tag(d, 1, loc)

    def lvd(self, loc: int) -> bytes:
        d = bytearray(446)
        struct.pack_into("<I", d, 16, 2)
        d[20:84] = charspec()
        d[84:212] = dstring(self.label, 128)
        struct.pack_into("<I", d, 212, self.bs)
        d[216:248] = regid(DOMAIN_ID, struct.pack("<H", UDF_REVISION))
        struct.pack_into("<IIH", d, 248, self.bs, self.L.fsd, 0)   # FSD long_ad
        struct.pack_into("<II", d, 264, 6, 1)
        d[272:304] = self.impl
        struct.pack_into("<II", d, 432, self.L.lvid_blocks * self.bs, LVID_BLOCK)
        d[440:446] = struct.pack("<BBHH", 1, 6, 1, 0)
        return make_tag(d, 6, loc)

    def pd(self, loc: int) -> bytes:
        d = bytearray(512)
        struct.pack_into("<IHH", d, 16, 3, 1, 0)
        d[24:56] = regid(b"+NSR03")
        struct.pack_into("<II", d, 64, self.L.sbd_blocks * self.bs, 0)  # bitmap
        struct.pack_into("<III", d, 184, 4, self.L.part_start, self.L.part_len)
        d[196:228] = self.impl
        return make_tag(d, 5, loc)

    def usd(self, loc: int) -> bytes:
        ext = self.L.erase_extents()
        d = bytearray(24 + 8 * len(ext))
        struct.pack_into("<II", d, 16, 4, len(ext))
        for i, (blk, n) in enumerate(ext):
            struct.pack_into("<II", d, 24 + 8 * i, n * self.bs, blk)
        return make_tag(d, 7, loc)

    def iuvd(self, loc: int) -> bytes:
        d = bytearray(512)
        struct.pack_into("<I", d, 16, 5)
        d[20:52] = regid(b"*UDF LV Info", struct.pack("<H", UDF_REVISION))
        d[52:116] = charspec()
        d[116:244] = dstring(self.label, 128)
        d[352:384] = self.impl
        return make_tag(d, 4, loc)

    def td(self, loc: int) -> bytes:
        return make_tag(bytearray(512), 8, loc)

    def lvid(self, loc: int) -> bytes:
        d = bytearray(134)
        d[16:28] = self.stamp
        struct.pack_into("<I", d, 28, 1)                         # kapali
        struct.pack_into("<Q", d, 40, 16)                        # sonraki benzersiz kimlik
        struct.pack_into("<II", d, 72, 1, 46)
        struct.pack_into("<II", d, 80, self.L.part_len - self.L.used, self.L.part_len)
        d[88:120] = self.impl
        struct.pack_into("<IIHHH", d, 120, 0, 1, UDF_REVISION, UDF_REVISION,
                         UDF_REVISION)
        return make_tag(d, 9, loc)

    def avdp(self, loc: int) -> bytes:
        d = bytearray(512)
        n = VDS_BLOCKS * self.bs
        struct.pack_into("<IIII", d, 16, n, MVDS_BLOCK, n, self.L.rvds)
        return make_tag(d, 2, loc)

    def sbd(self) -> bytes:
        L = self.L
        bitmap = bytearray(b"\xff" * L.bitmap_bytes)
        for blk in range(L.used):
            bitmap[blk >> 3] &= ~(1 << (blk & 7)) & 0xFF
        for blk in range(L.part_len, L.bitmap_bytes * 8):
            bitmap[blk >> 3] &= ~(1 << (blk & 7)) & 0xFF
        d = bytearray(24) + bitmap
        struct.pack_into("<II", d, 16, L.part_len, L.bitmap_bytes)
        return make_tag(d, 264, 0, crc_len=8)

    def fsd(self) -> bytes:
        d = bytearray(512)
        d[16:28] = self.stamp
        struct.pack_into("<HHIIII", d, 28, 3, 3, 1, 1, 0, 0)
        d[48:112] = charspec()
        d[112:240] = dstring(self.label, 128)
        d[240:304] = charspec()
        d[304:336] = dstring("DiskUltimate", 32)
        struct.pack_into("<IIH", d, 400, self.bs, self.L.root, 0)
        d[416:448] = regid(DOMAIN_ID, struct.pack("<H", UDF_REVISION))
        struct.pack_into("<IIH", d, 464, self.bs, self.L.stream_dir, 0)
        return make_tag(d, 256, self.L.fsd)

    def directory_efe(self, loc: int, file_type: int, perms: int) -> bytes:
        """Yalnizca "ust" FID'i gomulu tasiyan bos dizin (EFE)."""
        fid = bytearray(40)
        struct.pack_into("<HBB", fid, 16, 1, 0x0A, 0)            # dizin + ust
        struct.pack_into("<IIH", fid, 20, self.bs, loc, 0)
        fid = bytearray(make_tag(fid, 257, loc, crc_len=24))
        d = bytearray(216) + fid
        struct.pack_into("<IHHHBB", d, 16, 0, 4, 0, 1, 0, file_type)
        struct.pack_into("<H", d, 34, 3)                          # gomulu
        struct.pack_into("<IIIH", d, 36, 0, 0, perms, 1)
        struct.pack_into("<QQQ", d, 56, len(fid), len(fid), 0)
        for off in (80, 92, 104, 116):
            d[off:off + 12] = self.stamp
        struct.pack_into("<I", d, 128, 1)
        d[168:200] = self.impl
        struct.pack_into("<II", d, 208, 0, len(fid))
        return make_tag(d, 266, loc)

    # ---- yazma ------------------------------------------------------------
    def _write(self, block: int, data: bytes) -> None:
        if len(data) % self.bs:
            data = data + bytes(self.bs - len(data) % self.bs)
        self.dev.write(block * self.bs, data)

    def _zero(self, block: int, count: int) -> None:
        chunk = bytes(min(count * self.bs, 1024 * 1024))
        pos, end = block * self.bs, (block + count) * self.bs
        while pos < end:
            n = min(len(chunk), end - pos)
            self.dev.write(pos, chunk[:n])
            pos += n

    def format(self, progress: Optional[Callable[[str, int], None]] = None) -> Dict:
        L = self.L
        bs = self.bs
        if progress:
            progress(tr("UDF yerlesimi yaziliyor..."), 10)
        # ayrilmis alanlar ve bolumun meta verisi temizlenir (eski imzalar)
        self._zero(0, ANCHOR + 1)
        self._zero(L.part_start, L.used)
        self._zero(L.blocks - RESERVED_TAIL, RESERVED_TAIL)
        for i, ident in enumerate((b"BEA01", b"NSR03", b"TEA01")):
            vrs = bytearray(L.vrs_step)
            vrs[0:7] = b"\x00" + ident + b"\x01"
            self.dev.write(32768 + i * L.vrs_step, bytes(vrs))
        for base in (MVDS_BLOCK, L.rvds):
            seq = [self.pvd, self.lvd, self.pd, self.usd, self.iuvd, self.td]
            for i, make in enumerate(seq):
                self._write(base + i, make(base + i))
        self._write(LVID_BLOCK, self.lvid(LVID_BLOCK))
        self._write(LVID_BLOCK + 1, self.td(LVID_BLOCK + 1))
        for loc in (ANCHOR, L.anchor2, L.anchor3):
            self._write(loc, self.avdp(loc))
        if progress:
            progress(tr("UDF dosya kumesi yaziliyor..."), 60)
        ps = L.part_start
        self._write(ps, self.sbd())
        self._write(ps + L.fsd, self.fsd())
        self._write(ps + L.stream_dir,
                    self.directory_efe(L.stream_dir, 13, 0x7CA5))
        self._write(ps + L.root, self.directory_efe(L.root, 4, 0x3CA5))
        flush = getattr(self.dev, "flush", None)
        if flush:
            flush()
        if progress:
            progress(tr("Tamamlandi"), 100)
        return {"block_size": bs, "blocks": L.blocks, "partition_blocks": L.part_len,
                "free_blocks": L.part_len - L.used}


def format_udf(dev: BlockDevice, label: str = "", block_size: int = 0,
               progress: Optional[Callable[[str, int], None]] = None) -> Dict:
    return UdfFormatter(dev, label=label, block_size=block_size).format(progress)
