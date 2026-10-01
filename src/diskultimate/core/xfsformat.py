"""XFS v5 bicimlendirme — saf Python (ADR 0067).

Ozellik kumesi (sabit, bilincli olarak sade): crc (v5), ftype, finobt,
bigtime, inobtcount. rmapbt / reflink / seyrek inode / nrext64 / parent /
metadir kapali — cekirdek 5.10+ bu birimi baglar (bigtime 5.10'da geldi).

Yerlesim `mkfs.xfs 6.18 -m crc=1,finobt=1,rmapbt=0,reflink=0,bigtime=1,
inobtcount=1,metadir=0 -i sparse=0,nrext64=0,exchange=0 -n ftype=1,parent=0`
ciktisi olculerek cikarildi; ayni UUID/etiketle mkfs ile bayt bayt
karsilastirilir (zaman, nesil ve CRC alanlari disinda):

  her AG blok 0: sektor 0 ustblok, 1 AGF, 2 AGI, 3 AGFL
       blok 1..4: bnobt, cntbt, inobt, finobt kokleri (v5 kisa basliklar)
       blok 5..8: AGFL icin ayrilmis bloklar (AGFL sirasi 1..4)
  AG0  blok 12..19: ilk inode obegi (64 x 512): 96 kok, 97 rbm, 98 rsum
  AG (agcount/2): blok 5'ten gunluk, ardindan AGFL bloklari
  geometri: <= 4 x (2^28-1) blokta 4 AG (boy ceil(blok/4)), ustunde azami
  boy ve ceil(blok/azami) AG; son AG
  16 MB'tan kucukse atilir; gunluk blok/2048 (64 MB - 2 GB-10 MB);
  imaxpct 25 (>= 1 TiB 5, >= 50 TiB 1).

Butun CRC'ler standart CRC-32C, kucuk uclu. Gunluk: tek sektorluk
"unmount" kaydi (dongu 1), gerisi sifir.
"""
from __future__ import annotations

import os
import struct
import time
import uuid as _uuid
from typing import Callable, Dict, List, Optional, Tuple

from .crc32c import crc32c
from .image import BlockDevice
from ..i18n import tr

BS = 4096
SECT = 512
ISIZE = 512
INOPB = BS // ISIZE
MIB = 1024 * 1024
MIN_BYTES = 300 * MIB
MAX_AG_BLOCKS = (1 << 28) - 1
MIN_AG_BLOCKS = 16 * MIB // BS
MIN_LOG_BLOCKS = 64 * MIB // BS
MAX_LOG_BLOCKS = (2 * 1024 * MIB - 10 * MIB) // BS
NULLAGBLOCK = 0xFFFFFFFF
NULLFSINO = 0xFFFFFFFFFFFFFFFF
INODES_PER_CHUNK = 64
INO_ALIGN = 4                                  # blok (16 KiB inode kumesi)
PREALLOC = 5                                   # basliklar + 4 B-agaci koku
AGFL_BLOCKS = 4
ROOT_CHUNK_BLOCK = 12
VERSIONNUM = 0xB4A5
FEATURES2 = 0x18A
RO_COMPAT = 0x9                                # FINOBT | INOBTCNT
INCOMPAT = 0x9                                 # FTYPE | BIGTIME
BIGTIME_OFFSET = 1 << 31


class XfsFormatError(Exception):
    pass


def xfs_crc(buf: bytearray, off: int) -> None:
    """CRC alanini sifirlayip standart CRC-32C'yi kucuk uclu yazar."""
    buf[off:off + 4] = b"\x00\x00\x00\x00"
    struct.pack_into("<I", buf, off, crc32c(bytes(buf)))


def bigtime(seconds: float) -> int:
    whole = int(seconds)
    ns = int((seconds - whole) * 1_000_000_000)
    return (whole + BIGTIME_OFFSET) * 1_000_000_000 + ns


class Geometry:
    def __init__(self, size: int):
        blocks = size // BS
        if blocks <= 4 * MAX_AG_BLOCKS:                # 4 esit AG
            agcount = 4
            agsize = -(-blocks // agcount)
        else:                                         # azami boy, gerektigi kadar AG
            agsize = MAX_AG_BLOCKS
            agcount = -(-blocks // agsize)
        last = blocks - (agcount - 1) * agsize
        if last < MIN_AG_BLOCKS:                      # kucuk son AG atilir
            agcount -= 1
            blocks = agcount * agsize
            last = agsize
        self.dblocks = blocks
        self.agcount = agcount
        self.agsize = agsize
        self.last_ag = last
        self.agblklog = max(1, (agsize - 1).bit_length())
        log = min(MAX_LOG_BLOCKS, max(MIN_LOG_BLOCKS, blocks // 2048))
        self.logblocks = log
        self.log_ag = agcount // 2
        if PREALLOC + log + AGFL_BLOCKS + 1 > self.ag_length(self.log_ag):
            raise XfsFormatError(tr("Birim XFS gunlugu icin cok kucuk"))
        total = blocks * BS
        self.imax_pct = 1 if total >= 50 * (1 << 40) else 5 if total >= (1 << 40) else 25

    def ag_length(self, agno: int) -> int:
        return self.last_ag if agno == self.agcount - 1 else self.agsize

    def fsb(self, agno: int, agbno: int) -> int:
        return (agno << self.agblklog) | agbno

    def ino(self, agno: int, agino: int) -> int:
        return (agno << (self.agblklog + 3)) | agino


class XfsFormatter:
    def __init__(self, dev: BlockDevice, label: str = "",
                 uuid: Optional[bytes] = None, now: Optional[float] = None):
        if getattr(dev, "sector_size", 512) != SECT:
            raise XfsFormatError(tr("XFS bicimlendirme yalnizca 512 bayt sektorlu "
                                    "aygitlarda destekleniyor"))
        if dev.size < MIN_BYTES:
            raise XfsFormatError(tr("XFS icin en az 300 MB gerekir"))
        self.dev = dev
        self.g = Geometry(dev.size)
        raw_label = label.encode("utf-8")[:12]
        self.label = raw_label
        self.uuid = uuid or _uuid.uuid4().bytes
        self.now = time.time() if now is None else now

    # ---- AG yerlesimi -----------------------------------------------------
    def free_extents(self, agno: int) -> List[Tuple[int, int]]:
        g = self.g
        length = g.ag_length(agno)
        if agno == g.log_ag:
            start = PREALLOC + g.logblocks + AGFL_BLOCKS
            return [(start, length - start)] if length > start else []
        if agno == 0:
            chunk_end = ROOT_CHUNK_BLOCK + INODES_PER_CHUNK // INOPB
            return [(PREALLOC + AGFL_BLOCKS, ROOT_CHUNK_BLOCK - PREALLOC - AGFL_BLOCKS),
                    (chunk_end, length - chunk_end)]
        start = PREALLOC + AGFL_BLOCKS
        return [(start, length - start)]

    def agfl_blocks(self, agno: int) -> List[int]:
        g = self.g
        if agno == g.log_ag:
            base = PREALLOC + g.logblocks
        else:
            base = PREALLOC
        return list(range(base, base + AGFL_BLOCKS))

    # ---- yapilar --------------------------------------------------------------
    def superblock(self, primary: bool, has_root: bool = True) -> bytes:
        """Birincil ya da ikincil ustblok. mkfs ikincilleri erken yazar ve
        yalnizca son AG ile (AG-1)/2'dekini kok inode ile gunceller;
        digerlerinde rootino NULL kalir (olculdu: 4/5/8 AG)."""
        g = self.g
        sb = bytearray(SECT)
        free_total = sum(sum(c for _s, c in self.free_extents(a)) + AGFL_BLOCKS
                         for a in range(g.agcount))
        struct.pack_into(">4sIQQQ", sb, 0, b"XFSB", BS, g.dblocks, 0, 0)
        sb[32:48] = self.uuid
        struct.pack_into(">Q", sb, 48, g.fsb(g.log_ag, PREALLOC))
        struct.pack_into(">QQQ", sb, 56, 96 if has_root else NULLFSINO,
                         97 if primary else NULLFSINO, 98 if primary else NULLFSINO)
        struct.pack_into(">IIIII", sb, 80, 1, g.agsize, g.agcount, 0, g.logblocks)
        struct.pack_into(">HHHH", sb, 100, VERSIONNUM, SECT, ISIZE, INOPB)
        sb[108:120] = self.label.ljust(12, b"\x00")
        sb[120:128] = bytes([12, 9, 9, 3, g.agblklog, 0, 0 if primary else 1,
                             g.imax_pct])
        icount, ifree = (64, 61) if primary else (0, 0)
        fdblocks = free_total if primary else free_total + INODES_PER_CHUNK // INOPB
        struct.pack_into(">QQQQ", sb, 128, icount, ifree, fdblocks, 0)
        struct.pack_into(">I", sb, 180, INO_ALIGN)
        struct.pack_into(">I", sb, 196, 1)                       # logsunit
        struct.pack_into(">IIIIII", sb, 200, FEATURES2, FEATURES2, 0, RO_COMPAT,
                         INCOMPAT, 0)
        xfs_crc(sb, 224)
        return bytes(sb)

    def agf(self, agno: int) -> bytes:
        g = self.g
        ext = self.free_extents(agno)
        b = bytearray(SECT)
        struct.pack_into(">4sIII", b, 0, b"XAGF", 1, agno, g.ag_length(agno))
        struct.pack_into(">III", b, 16, 1, 2, 0)                 # bno, cnt, rmap koku
        struct.pack_into(">III", b, 28, 1, 1, 0)                 # seviyeler
        struct.pack_into(">IIIIII", b, 40, 1, AGFL_BLOCKS, AGFL_BLOCKS,
                         sum(c for _s, c in ext), max((c for _s, c in ext), default=0), 0)
        b[64:80] = self.uuid
        xfs_crc(b, 216)
        return bytes(b)

    def agi(self, agno: int) -> bytes:
        g = self.g
        b = bytearray(SECT)
        count, freecount, newino = (64, 61, 96) if agno == 0 else (0, 0, NULLAGBLOCK)
        struct.pack_into(">4sIIIIIIIII", b, 0, b"XAGI", 1, agno, g.ag_length(agno),
                         count, 3, 1, freecount, newino, NULLAGBLOCK)
        b[40:296] = b"\xff" * 256                                # unlinked listeler
        b[296:312] = self.uuid
        struct.pack_into(">IIII", b, 328, 4, 1, 1, 1)            # finobt kok/seviye, sayilar
        xfs_crc(b, 312)
        return bytes(b)

    def agfl(self, agno: int) -> bytes:
        b = bytearray(SECT)
        struct.pack_into(">4sI", b, 0, b"XAFL", agno)
        b[8:24] = self.uuid
        entries = [NULLAGBLOCK] * ((SECT - 36) // 4)
        for i, blk in enumerate(self.agfl_blocks(agno), 1):
            entries[i] = blk
        struct.pack_into(f">{len(entries)}I", b, 36, *entries)
        xfs_crc(b, 32)
        return bytes(b)

    def btree_block(self, agno: int, agbno: int, magic: bytes, recs: bytes,
                    numrecs: int) -> bytes:
        b = bytearray(BS)
        struct.pack_into(">4sHHII", b, 0, magic, 0, numrecs, NULLAGBLOCK, NULLAGBLOCK)
        struct.pack_into(">QQ", b, 16, (agno * self.g.agsize + agbno) * (BS // SECT), 0)
        b[32:48] = self.uuid
        struct.pack_into(">I", b, 48, agno)
        b[56:56 + len(recs)] = recs
        xfs_crc(b, 52)
        return bytes(b)

    def inode(self, ino: int, mode: int, fmt: int, nlink: int, size: int,
              flags: int, gen: int, data: bytes = b"",
              atime: Optional[int] = None, changecount: int = 2) -> bytes:
        b = bytearray(ISIZE)
        stamp = bigtime(self.now)
        struct.pack_into(">2sHBB", b, 0, b"IN", mode, 3, fmt)
        struct.pack_into(">I", b, 16, nlink)
        if mode:
            struct.pack_into(">QQQ", b, 32, stamp if atime is None else atime,
                             stamp, stamp)
            struct.pack_into(">Q", b, 144, stamp)
            b[83] = 2                                            # aformat: kapsam
            struct.pack_into(">Q", b, 120, 8)                    # flags2: bigtime
            struct.pack_into(">Q", b, 104, changecount)
        struct.pack_into(">Q", b, 56, size)
        struct.pack_into(">HI", b, 90, flags, gen)
        struct.pack_into(">I", b, 96, NULLAGBLOCK)               # next_unlinked
        struct.pack_into(">Q", b, 152, ino)
        b[160:176] = self.uuid
        b[176:176 + len(data)] = data
        xfs_crc(b, 100)
        return bytes(b)

    def root_chunk(self) -> bytes:
        out = bytearray()
        rng = os.urandom(4 * INODES_PER_CHUNK)
        for i in range(INODES_PER_CHUNK):
            ino = 96 + i
            gen = struct.unpack_from(">I", rng, 4 * i)[0]
            if ino == 96:
                sf = struct.pack(">BBI", 0, 0, 96)               # bos kisa bicim dizin
                out += self.inode(ino, 0o040755, 1, 2, len(sf), 0, 0, sf)
            elif ino == 97:
                out += self.inode(ino, 0o100000, 2, 1, 0, 0x0004, gen,
                                  atime=bigtime(0))               # rbm (NEWRTBM)
            elif ino == 98:
                out += self.inode(ino, 0o100000, 2, 1, 0, 0, gen)
            else:
                out += self.inode(ino, 0, 0, 0, 0, 0, gen, changecount=0)
        return bytes(out)

    def log_head(self) -> bytes:
        rec = bytearray(2 * SECT)
        struct.pack_into(">IIIIQQ", rec, 0, 0xFEEDBABE, 1, 2, SECT, 1 << 32, 1 << 32)
        struct.pack_into(">III", rec, 36, 0xFFFFFFFF, 1, 0xB0C0D0D0)   # prev, ops, cycle_data[0]
        struct.pack_into(">I", rec, 300, 1)                      # h_fmt: Linux LE
        rec[304:320] = self.uuid
        struct.pack_into(">I", rec, 320, 32768)                  # h_size
        # veri sektoru: op basligi (ilk kelime dongu no ile degistirilmis)
        struct.pack_into(">IIBBH", rec, SECT, 1, 8, 0xAA, 0x20, 0)
        struct.pack_into("<H", rec, SECT + 12, 0x556E)           # XLOG_UNMOUNT_TYPE
        return bytes(rec)

    # ---- yazma -------------------------------------------------------------------
    def _zero(self, offset: int, length: int) -> None:
        chunk = bytes(min(length, 4 * MIB))
        while length > 0:
            n = min(length, len(chunk))
            self.dev.write(offset, chunk[:n])
            offset += n
            length -= n

    def format(self, progress: Optional[Callable[[str, int], None]] = None) -> Dict:
        g = self.g
        for agno in range(g.agcount):
            if progress:
                progress(tr("XFS ayirma grubu {}/{} yaziliyor...", agno + 1, g.agcount),
                         5 + 70 * agno // g.agcount)
            base = agno * g.agsize * BS
            head = bytearray(BS)
            updated = agno in (0, g.agcount - 1, (g.agcount - 1) // 2)
            head[0:SECT] = self.superblock(agno == 0, has_root=updated)
            head[SECT:2 * SECT] = self.agf(agno)
            head[2 * SECT:3 * SECT] = self.agi(agno)
            head[3 * SECT:4 * SECT] = self.agfl(agno)
            self.dev.write(base, bytes(head))
            ext = self.free_extents(agno)
            bno = b"".join(struct.pack(">II", s, c) for s, c in ext)
            cnt = b"".join(struct.pack(">II", s, c)
                           for s, c in sorted(ext, key=lambda e: (e[1], e[0])))
            self.dev.write(base + 1 * BS, self.btree_block(agno, 1, b"AB3B", bno, len(ext)))
            self.dev.write(base + 2 * BS, self.btree_block(agno, 2, b"AB3C", cnt, len(ext)))
            irec = struct.pack(">IIQ", 96, 61, 0xFFFFFFFFFFFFFFF8) if agno == 0 else b""
            n = 1 if agno == 0 else 0
            self.dev.write(base + 3 * BS, self.btree_block(agno, 3, b"IAB3", irec, n))
            self.dev.write(base + 4 * BS, self.btree_block(agno, 4, b"FIB3", irec, n))
            for blk in self.agfl_blocks(agno):
                self.dev.write(base + blk * BS, bytes(BS))
        if progress:
            progress(tr("XFS kok dizini ve gunlugu yaziliyor..."), 80)
        self.dev.write(ROOT_CHUNK_BLOCK * BS, self.root_chunk())
        log_off = (g.log_ag * g.agsize + PREALLOC) * BS
        self._zero(log_off, g.logblocks * BS)
        self.dev.write(log_off, self.log_head())
        flush = getattr(self.dev, "flush", None)
        if flush:
            flush()
        if progress:
            progress(tr("Tamamlandi"), 100)
        return {"agcount": g.agcount, "agsize": g.agsize, "dblocks": g.dblocks,
                "logblocks": g.logblocks}


def format_xfs(dev: BlockDevice, label: str = "",
               progress: Optional[Callable[[str, int], None]] = None) -> Dict:
    return XfsFormatter(dev, label=label).format(progress)
