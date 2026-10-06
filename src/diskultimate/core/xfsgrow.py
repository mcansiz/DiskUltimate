"""XFS buyutme — saf Python, cevrimdisi (`xfs_growfs` karsiligi; ADR 0068).

XFS kuculmez; buyutme iki adimdir (cekirdekteki `xfs_growfs_data` gibi):

1. **Son AG uzatilir** (AG boyuna kadar): AGF/AGI boyu, bos alan
   B-agaclarina (bnobt/cntbt) sondaki bos kapsam eklenir ya da uzatilir.
2. **Yeni AG'ler** eklenir: her birinin basinda ustblok kopyasi, AGF, AGI,
   AGFL, bos B-agaci kokleri ve AGFL bloklari. Ozellige gore yerlesim
   (mkfs.xfs 6.18 ile olculdu):
       blok 0 basliklar (4 sektor; kucuk blokta 0..XFS_AGFL_BLOCK), sonra
       bnobt, cntbt, inobt, [finobt], [rmapbt],
       [refcountbt]; AGFL rmapbt varsa 6, yoksa 4 blok; rmapbt kayitlari
       AG'nin kendi meta verisini sahipleriyle (FS -3, AG -5, INOBT -6,
       REFC -8) bitisik ayni sahipler birlestirilerek listeler.

Son AG 64 bloktan kucuk kalacaksa atilir (cekirdek kurali). Birincil
ustblokta dblocks/agcount/fdblocks guncellenir, ikinciller birincilin
kopyasi olur (cekirdek de boyle yapar).

Guvenlik: gunluk **temiz** olmali (son kayit "unmount"); aksi halde
Linux'ta baglayip ayirmak gerekir. v4, gercek zamanli alt birim, metadir
ve bilinmeyen ozellikler reddedilir. Bos alan agaci cok duzeyliyse son AG
uzatilmaz (acik hata).
"""
from __future__ import annotations

import struct
from typing import Callable, Dict, List, Optional, Tuple

from .crc32c import crc32c
from .image import BlockDevice
from ..i18n import tr

NULLAGBLOCK = 0xFFFFFFFF
MIN_AG_BLOCKS = 64
RO_FINOBT, RO_RMAPBT, RO_REFLINK, RO_INOBTCNT = 0x1, 0x2, 0x4, 0x8
KNOWN_RO = RO_FINOBT | RO_RMAPBT | RO_REFLINK | RO_INOBTCNT
INCOMPAT_NEEDSREPAIR = 0x10
KNOWN_INCOMPAT = 0x01 | 0x02 | 0x04 | 0x08 | 0x20 | 0x40 | 0x80
OWN_FS, OWN_AG, OWN_INOBT, OWN_REFC = -3, -5, -6, -8
LOG_MAGIC = 0xFEEDBABE
XLOG_UNMOUNT_TRANS = 0x20


class XfsGrowError(Exception):
    pass


def _crc(buf: bytearray, off: int) -> bytes:
    buf[off:off + 4] = b"\x00\x00\x00\x00"
    struct.pack_into("<I", buf, off, crc32c(bytes(buf)))
    return bytes(buf)


class _Sb:
    """Birincil ustblogun gerekli alanlari."""

    def __init__(self, raw: bytes):
        if raw[:4] != b"XFSB":
            raise XfsGrowError(tr("XFS ustblogu bulunamadi"))
        self.raw = bytearray(raw)
        self.bs = struct.unpack_from(">I", raw, 4)[0]
        self.dblocks = struct.unpack_from(">Q", raw, 8)[0]
        self.rblocks = struct.unpack_from(">Q", raw, 16)[0]
        self.uuid = bytes(raw[32:48])
        self.logstart = struct.unpack_from(">Q", raw, 48)[0]
        self.agblocks, self.agcount = struct.unpack_from(">II", raw, 84)
        self.logblocks = struct.unpack_from(">I", raw, 96)[0]
        self.version = struct.unpack_from(">H", raw, 100)[0] & 0xF
        self.sectsize = struct.unpack_from(">H", raw, 102)[0]
        self.agblklog = raw[124]
        self.fdblocks = struct.unpack_from(">Q", raw, 144)[0]
        self.logsectsize = struct.unpack_from(">H", raw, 194)[0] or 512
        self.ro_compat = struct.unpack_from(">I", raw, 212)[0]
        self.incompat = struct.unpack_from(">I", raw, 216)[0]
        self.meta_uuid = bytes(raw[248:264])
        # metadata uuid (META_UUID ozelligi) etiketlerde kullanilir
        self.tag_uuid = self.meta_uuid if self.incompat & 0x04 else self.uuid

    def fsb_to_byte(self, fsb: int) -> int:
        agno = fsb >> self.agblklog
        agbno = fsb & ((1 << self.agblklog) - 1)
        return (agno * self.agblocks + agbno) * self.bs


def _check_supported(sb: _Sb) -> None:
    if sb.version < 5:
        raise XfsGrowError(tr("XFS v4 birimleri bu surumde buyutulemiyor"))
    if sb.rblocks:
        raise XfsGrowError(tr("Gercek zamanli alt birimli XFS buyutulemiyor"))
    if sb.incompat & INCOMPAT_NEEDSREPAIR:
        raise XfsGrowError(tr("XFS birimi onarim bekliyor (xfs_repair)"))
    if sb.incompat & ~KNOWN_INCOMPAT or sb.ro_compat & ~KNOWN_RO:
        raise XfsGrowError(tr("XFS birimi bu surumun tanimadigi ozellikler "
                              "tasiyor; buyutulemiyor"))
    if not sb.logstart:
        raise XfsGrowError(tr("Harici gunluklu XFS buyutulemiyor"))


# ---------------------------------------------------------------------------
# gunluk
# ---------------------------------------------------------------------------
def log_is_clean(dev: BlockDevice, sb: _Sb) -> bool:
    """Gunlugun son kaydi "unmount" mu? (cekirdegin xlog_find_head'i gibi
    dongu numaralarindan basi bulur, geriye dogru son kayit basligini arar.)"""
    ss = 512
    base = sb.fsb_to_byte(sb.logstart)
    n = sb.logblocks * sb.bs // ss

    def word(i: int) -> Tuple[int, int]:
        w = dev.read(base + i * ss, 8)
        magic, cyc = struct.unpack(">II", w)
        return (cyc, 1) if magic == LOG_MAGIC else (magic, 0)

    first = word(0)[0]
    if first == 0:
        return False                                  # hic yazilmamis gunluk
    lo, hi = 0, n - 1
    if word(hi)[0] == first:
        head = n                                      # tum gunluk ayni dongu
    else:
        while lo < hi:                                # son "first" dongulu sektor
            mid = (lo + hi + 1) // 2
            if word(mid)[0] == first:
                lo = mid
            else:
                hi = mid - 1
        head = lo + 1
    for i in range(head - 1, max(-1, head - 1 - 8192), -1):
        cyc, is_header = word(i)
        if not is_header:
            continue
        hdr = dev.read(base + i * ss, ss)
        ops = struct.unpack_from(">I", hdr, 40)[0]
        size = struct.unpack_from(">I", hdr, 320)[0] or 32768
        hblks = max(1, -(-size // 32768)) if struct.unpack_from(">I", hdr, 8)[0] & 2 else 1
        data = dev.read(base + ((i + hblks) % n) * ss, 16)
        flags = data[9]
        return ops == 1 and data[8] == 0xAA and bool(flags & XLOG_UNMOUNT_TRANS)
    return False


# ---------------------------------------------------------------------------
# yeni AG baslik yapilari
# ---------------------------------------------------------------------------
def _layout(sb: _Sb) -> Dict[str, int]:
    finobt = bool(sb.ro_compat & RO_FINOBT)
    rmap = bool(sb.ro_compat & RO_RMAPBT)
    refc = bool(sb.ro_compat & RO_REFLINK)
    # Basliklar (ustblok, AGF, AGI, AGFL) sektor boyludur; 1 KiB blokta
    # birden cok blok kaplar. Cekirdek: XFS_AGFL_BLOCK = (3 sektor) blok
    # karsiligi, XFS_BNO_BLOCK = XFS_AGFL_BLOCK + 1 (xfs_format.h).
    agfl_block = (3 * sb.sectsize) // sb.bs
    lay = {"hdr": agfl_block + 1, "bno": agfl_block + 1, "cnt": agfl_block + 2,
           "ino": agfl_block + 3}
    nxt = agfl_block + 4
    lay["fino"] = nxt if finobt else 0
    nxt += finobt
    lay["rmap"] = nxt if rmap else 0
    nxt += rmap
    lay["refc"] = nxt if refc else 0
    nxt += refc
    lay["prealloc"] = nxt
    lay["agfl"] = 6 if rmap else 4
    return lay


def _short_block(sb: _Sb, magic: bytes, agno: int, agbno: int, recs: bytes,
                 numrecs: int) -> bytes:
    b = bytearray(sb.bs)
    struct.pack_into(">4sHHII", b, 0, magic, 0, numrecs, NULLAGBLOCK, NULLAGBLOCK)
    struct.pack_into(">QQ", b, 16, (agno * sb.agblocks + agbno) * (sb.bs // 512), 0)
    b[32:48] = sb.tag_uuid
    struct.pack_into(">I", b, 48, agno)
    b[56:56 + len(recs)] = recs
    return _crc(b, 52)


def _rmap_records(lay: Dict[str, int]) -> List[Tuple[int, int, int]]:
    # xfs_rmaproot_init: OWN_FS [0, XFS_BNO_BLOCK), OWN_AG bnobt+cntbt, ...
    items: List[Tuple[int, int, int]] = [(0, lay["hdr"], OWN_FS),
                                         (lay["bno"], 2, OWN_AG),
                                         (lay["ino"], 2 if lay["fino"] else 1, OWN_INOBT),
                                         (lay["rmap"], 1, OWN_AG)]
    if lay["refc"]:
        items.append((lay["refc"], 1, OWN_REFC))
    items.append((lay["prealloc"], lay["agfl"], OWN_AG))
    merged: List[Tuple[int, int, int]] = []
    for s0, c, owner in sorted(items):
        if merged and merged[-1][2] == owner and merged[-1][0] + merged[-1][1] == s0:
            merged[-1] = (merged[-1][0], merged[-1][1] + c, owner)
        else:
            merged.append((s0, c, owner))
    return merged


def new_ag_blocks(sb: _Sb, primary: bytes, agno: int, length: int
                  ) -> Tuple[List[Tuple[int, bytes]], int]:
    """[(AG ici blok, veri)] ve eklenen bos blok (fdblocks'a: serbest + AGFL)."""
    lay = _layout(sb)
    ss = sb.sectsize
    free_start = lay["prealloc"] + lay["agfl"]
    free_len = length - free_start
    if free_len <= 0:
        raise XfsGrowError(tr("Yeni XFS ayirma grubu cok kucuk"))
    # baslik sektorleri 4 x sektor; 1 KiB blokta 2+ blok tutar
    head = bytearray(lay["hdr"] * sb.bs)
    head[0:len(primary)] = primary
    # AGF
    agf = bytearray(ss)
    struct.pack_into(">4sIII", agf, 0, b"XAGF", 1, agno, length)
    struct.pack_into(">III", agf, 16, lay["bno"], lay["cnt"], lay["rmap"])
    struct.pack_into(">III", agf, 28, 1, 1, 1 if lay["rmap"] else 0)
    struct.pack_into(">IIIIII", agf, 40, 1, lay["agfl"], lay["agfl"], free_len, free_len, 0)
    agf[64:80] = sb.tag_uuid
    if lay["rmap"]:
        struct.pack_into(">I", agf, 80, 1)
    if lay["refc"]:
        struct.pack_into(">III", agf, 84, 1, lay["refc"], 1)
    head[ss:2 * ss] = _crc(agf, 216)
    # AGI
    agi = bytearray(ss)
    struct.pack_into(">4sIIIIIIIII", agi, 0, b"XAGI", 1, agno, length, 0, lay["ino"], 1,
                     0, NULLAGBLOCK, NULLAGBLOCK)
    agi[40:296] = b"\xff" * 256
    agi[296:312] = sb.tag_uuid
    if lay["fino"]:
        struct.pack_into(">II", agi, 328, lay["fino"], 1)
    if sb.ro_compat & RO_INOBTCNT:
        struct.pack_into(">II", agi, 336, 1, 1 if lay["fino"] else 0)
    head[2 * ss:3 * ss] = _crc(agi, 312)
    # AGFL
    agfl = bytearray(ss)
    struct.pack_into(">4sI", agfl, 0, b"XAFL", agno)
    agfl[8:24] = sb.tag_uuid
    entries = [NULLAGBLOCK] * ((ss - 36) // 4)
    for i in range(lay["agfl"]):
        entries[1 + i] = lay["prealloc"] + i
    struct.pack_into(f">{len(entries)}I", agfl, 36, *entries)
    head[3 * ss:4 * ss] = _crc(agfl, 32)

    out = [(0, bytes(head))]
    rec = struct.pack(">II", free_start, free_len)
    out.append((lay["bno"], _short_block(sb, b"AB3B", agno, lay["bno"], rec, 1)))
    out.append((lay["cnt"], _short_block(sb, b"AB3C", agno, lay["cnt"], rec, 1)))
    out.append((lay["ino"], _short_block(sb, b"IAB3", agno, lay["ino"], b"", 0)))
    if lay["fino"]:
        out.append((lay["fino"], _short_block(sb, b"FIB3", agno, lay["fino"], b"", 0)))
    if lay["rmap"]:
        recs = b"".join(struct.pack(">IIqQ", s0, c, owner, 0)
                        for s0, c, owner in _rmap_records(lay))
        out.append((lay["rmap"], _short_block(sb, b"RMB3", agno, lay["rmap"], recs,
                                              len(recs) // 24)))
    if lay["refc"]:
        out.append((lay["refc"], _short_block(sb, b"R3FC", agno, lay["refc"], b"", 0)))
    for i in range(lay["agfl"]):
        out.append((lay["prealloc"] + i, bytes(sb.bs)))
    return out, free_len + lay["agfl"]


# ---------------------------------------------------------------------------
# son AG'nin uzatilmasi
# ---------------------------------------------------------------------------
def _extend_last_ag(dev: BlockDevice, sb: _Sb, agno: int, old_len: int,
                    new_len: int) -> List[Tuple[int, bytes]]:
    """Yazilacak (bayt ofseti, veri) listesi; hicbir sey yazmaz."""
    bs = sb.bs
    ss = sb.sectsize
    base = agno * sb.agblocks * bs
    head = dev.read(base, 4 * ss)
    agf = bytearray(head[ss:2 * ss])
    agi = bytearray(head[2 * ss:3 * ss])
    if agf[:4] != b"XAGF" or agi[:4] != b"XAGI":
        raise XfsGrowError(tr("XFS ayirma grubu basligi bozuk: {}", agno))
    bno_root, cnt_root = struct.unpack_from(">II", agf, 16)
    bno_lvl, cnt_lvl = struct.unpack_from(">II", agf, 28)
    if bno_lvl != 1 or cnt_lvl != 1:
        raise XfsGrowError(tr("Son ayirma grubunun bos alan agaci cok duzeyli; "
                              "bu surumde uzatilamiyor"))
    delta = new_len - old_len
    bno = bytearray(dev.read(base + bno_root * bs, bs))
    cnt = bytearray(dev.read(base + cnt_root * bs, bs))
    maxrecs = (bs - 56) // 8

    def recs(block: bytearray) -> List[Tuple[int, int]]:
        n = struct.unpack_from(">H", block, 6)[0]
        return [struct.unpack_from(">II", block, 56 + 8 * i) for i in range(n)]

    free = recs(bno)
    if free and free[-1][0] + free[-1][1] == old_len:
        old = free[-1]
        free[-1] = (old[0], old[1] + delta)
        by_count = [r for r in recs(cnt) if r != old]
    else:
        free.append((old_len, delta))
        by_count = recs(cnt)
    by_count.append(free[-1])
    by_count.sort(key=lambda r: (r[1], r[0]))
    if len(free) > maxrecs:
        raise XfsGrowError(tr("Son ayirma grubunun bos alan agaci dolu"))

    def store(block: bytearray, items: List[Tuple[int, int]]) -> bytes:
        struct.pack_into(">H", block, 6, len(items))
        block[56:] = bytes(bs - 56)
        for i, (s0, c) in enumerate(items):
            struct.pack_into(">II", block, 56 + 8 * i, s0, c)
        return _crc(block, 52)

    struct.pack_into(">I", agf, 12, new_len)
    freeblks = struct.unpack_from(">I", agf, 52)[0] + delta
    struct.pack_into(">II", agf, 52, freeblks, max(c for _s, c in free))
    struct.pack_into(">I", agi, 12, new_len)
    return [(base + bno_root * bs, store(bno, free)),
            (base + cnt_root * bs, store(cnt, by_count)),
            (base + ss, _crc(agf, 216)),
            (base + 2 * ss, _crc(agi, 312))]


# ---------------------------------------------------------------------------
# genel
# ---------------------------------------------------------------------------
def xfs_grow_plan(dev: BlockDevice, new_bytes: int) -> Dict[str, int]:
    sb = _Sb(dev.read(0, 512))
    _check_supported(sb)
    nb = new_bytes // sb.bs
    nag = -(-nb // sb.agblocks)
    mod = nb % sb.agblocks
    if mod and mod < MIN_AG_BLOCKS:
        nag -= 1
        nb = nag * sb.agblocks
    if nag > sb.agcount:
        nb = min(nb, nag * sb.agblocks)
    return {"old_blocks": sb.dblocks, "new_blocks": max(nb, sb.dblocks),
            "old_agcount": sb.agcount, "new_agcount": max(nag, sb.agcount),
            "block_size": sb.bs}


def xfs_limits(dev: BlockDevice) -> Dict[str, int]:
    """Kuculme yok: en az = mevcut boy; en cok = 2^63 (pratikte sinirsiz)."""
    sb = _Sb(dev.read(0, 512))
    return {"min_bytes": sb.dblocks * sb.bs, "max_bytes": 0,
            "growable": _supported_reason(sb) == ""}


def _supported_reason(sb: _Sb) -> str:
    try:
        _check_supported(sb)
    except XfsGrowError as exc:
        return str(exc)
    return ""


def xfs_grow(dev: BlockDevice, new_bytes: int,
             progress: Optional[Callable[[str, int], None]] = None) -> Dict[str, int]:
    """`dev` (en az `new_bytes` boyunda) uzerindeki XFS'i buyutur."""
    sb = _Sb(dev.read(0, 512))
    _check_supported(sb)
    if not log_is_clean(dev, sb):
        raise XfsGrowError(tr("XFS gunlugu temiz degil; birim Linux'ta baglanip "
                              "duzgun ayrilmadan buyutulemez"))
    plan = xfs_grow_plan(dev, new_bytes)
    nb, nag = plan["new_blocks"], plan["new_agcount"]
    if nb <= sb.dblocks:
        return plan
    if nb * sb.bs > dev.size:
        raise XfsGrowError(tr("Aygit istenen boydan kucuk"))
    writes: List[Tuple[int, bytes]] = []
    added = 0
    last = sb.agcount - 1
    old_len = sb.dblocks - last * sb.agblocks
    new_len = min(sb.agblocks, nb - last * sb.agblocks)
    if new_len > old_len:
        writes += _extend_last_ag(dev, sb, last, old_len, new_len)
        added += new_len - old_len
    # yeni birincil: CRC ustblok sektorunun tamamini kapsar (sektor 4 KiB
    # ise 4096 bayt; cekirdek tamponu XFS_FSS_TO_BB(mp, 1) boyludur)
    primary = bytearray(dev.read(0, sb.sectsize))
    struct.pack_into(">Q", primary, 8, nb)
    struct.pack_into(">I", primary, 88, nag)
    new_ag_writes: List[Tuple[int, bytes]] = []
    for agno in range(sb.agcount, nag):
        length = min(sb.agblocks, nb - agno * sb.agblocks)
        blocks, free = new_ag_blocks(sb, bytes(primary), agno, length)
        added += free
        base = agno * sb.agblocks * sb.bs
        new_ag_writes += [(base + b * sb.bs, data) for b, data in blocks]
    struct.pack_into(">Q", primary, 144, sb.fdblocks + added)
    final = _crc(bytearray(primary), 224)
    # yeni AG'lerin ustbloklari son degerlerle (CRC dahil)
    new_ag_writes = [(off, final + data[len(final):]) if (off // sb.bs) % sb.agblocks == 0
                     and off % (sb.agblocks * sb.bs) == 0 else (off, data)
                     for off, data in new_ag_writes]
    total = len(new_ag_writes) + len(writes) + sb.agcount
    done = 0
    for off, data in new_ag_writes + writes:
        dev.write(off, data)
        done += 1
        if progress and done % 16 == 0:
            progress(tr("XFS buyutuluyor..."), 5 + 85 * done // total)
    for agno in range(1, sb.agcount):                 # ikinciller: birincilin kopyasi
        dev.write(agno * sb.agblocks * sb.bs, final)
    dev.write(0, final)                               # birincil en son
    flush = getattr(dev, "flush", None)
    if flush:
        flush()
    if progress:
        progress(tr("Tamamlandi"), 100)
    return plan
