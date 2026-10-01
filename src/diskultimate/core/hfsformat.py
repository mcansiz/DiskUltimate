"""HFS+ / HFSX bicimlendirme (gunluksuz) — saf Python (`newfs_hfs` karsiligi).

Yerlesim `mkfs.hfsplus` (hfsprogs, Apple newfs_hfs'in Linux portu) ile
olculerek cikarildi (ADR 0061):

    blok 0            onyukleme bloklari (1024 bayt) + birim basligi (1024)
    1 ...             ayirma dosyasi (bitmap, bit 7 = blok 0)
    ...               kapsam tasmasi B-agaci  (dugum 4096)
    ...               oznitelik B-agaci       (dugum 8192)
    [bosluk]          10 x oznitelik kumesi (sigmiyorsa yok) — agaclarin
                      parcalanmadan buyuyebilmesi icin
    ...               katalog B-agaci (dugum: 1 GB alti 4096, ustu 8192)
    son blok          yedek birim basligi (birim sonundan 1024 bayt once)

B-agaci "kume" (clump) boylari: 1 GB altinda birim/128 (en az 8 dugum),
ustunde newfs_hfs'in tablosu. Blok boyutu 4096.

Katalogda iki kayit vardir: (1, birim adi) -> kok klasor (kimlik 2) ve
(2, "") -> kok klasorun iplik kaydi. Kullanilmayan dugumler sifirdir
(baslik ozniteliginde "unused node fix" biti).
"""
from __future__ import annotations

import os
import struct
import time
from typing import Callable, Dict, List, Optional, Tuple

from .hfsunicode import hfs_nfd
from .image import BlockDevice
from ..i18n import tr

BLOCK_SIZE = 4096
EXT_NODE = 4096
ATTR_NODE = 8192
MIB = 1024 * 1024
HFS_EPOCH_OFFSET = 2082844800          # 1904-01-01 -> 1970-01-01 (saniye)
VOL_UNMOUNTED = 1 << 8
VOL_UNUSED_NODE_FIX = 1 << 31
KEY_BIG = 0x02
KEY_VARIABLE_INDEX = 0x04
CATALOG_MAX_KEY = 516
ATTR_MAX_KEY = 266
EXT_MAX_KEY = 10
DEFAULT_LABEL = "untitled"
FIRST_USER_CNID = 16
MIN_BYTES = 1 * MIB

# newfs_hfs `clumptbl`: 1 GB, 2 GB, 4 GB ... 16 TB icin (oznitelik, katalog,
# kapsam) MB. Olculdu: 1G -> 4/4/4, 3G -> 6/6/4, 17G -> 64/32/5, 40G -> 84/49/6.
CLUMP_TABLE = [(4, 4, 4), (6, 6, 4), (8, 8, 4), (11, 11, 5), (64, 32, 5),
               (84, 49, 6), (111, 74, 7), (147, 111, 8), (194, 169, 9),
               (256, 256, 11), (294, 294, 14), (338, 338, 16), (388, 388, 20),
               (446, 446, 25), (512, 512, 32)]
COL_ATTR, COL_CATALOG, COL_EXTENTS = 0, 1, 2


class HfsFormatError(Exception):
    pass


def hfs_time(unix: Optional[float] = None, local: bool = False) -> int:
    """HFS+ zamani (1904'ten saniye). Birim basligindaki olusturma tarihi
    yerel saattir, digerleri UTC (TN1150)."""
    unix = time.time() if unix is None else unix
    value = int(unix) + HFS_EPOCH_OFFSET
    if local:
        value += time.localtime(unix).tm_gmtoff
    return value & 0xFFFFFFFF


def btree_clump(volume_bytes: int, node_size: int, column: int) -> int:
    sectors = volume_bytes // 512
    if sectors < 0x200000:
        clump = max(sectors * 4, 8 * node_size)
    else:
        row = min(len(CLUMP_TABLE) - 1, (sectors >> 21).bit_length() - 1)
        clump = CLUMP_TABLE[row][column] * MIB
    unit = max(node_size, BLOCK_SIZE)
    return (clump + unit - 1) // unit * unit


def hfs_name(text: str) -> bytes:
    """HFSUniStr255: uzunluk(2) + UTF-16BE, ayristirilmis (NFD)."""
    raw = hfs_nfd(text).encode("utf-16-be")
    if len(raw) // 2 > 255:
        raise HfsFormatError(tr("HFS+ adi en fazla 255 karakter olabilir"))
    return struct.pack(">H", len(raw) // 2) + raw


def catalog_key(parent: int, name: str) -> bytes:
    body = struct.pack(">I", parent) + hfs_name(name)
    return struct.pack(">H", len(body)) + body


def folder_record(cnid: int, now: int, valence: int = 0) -> bytes:
    rec = bytearray(88)
    struct.pack_into(">hHII", rec, 0, 1, 0, valence, cnid)
    struct.pack_into(">IIII", rec, 12, now, now, now, now)
    return bytes(rec)


def thread_record(kind: int, parent: int, name: str) -> bytes:
    return struct.pack(">hHI", kind, 0, parent) + hfs_name(name)


def build_node(node_size: int, kind: int, height: int, records: List[bytes],
               flink: int = 0, blink: int = 0) -> bytes:
    """B-agaci dugumu: tanimlayici + kayitlar + sondan geriye ofset tablosu."""
    node = bytearray(node_size)
    struct.pack_into(">IIbBHH", node, 0, flink, blink, kind, height,
                     len(records), 0)
    pos = 14
    offsets = []
    for rec in records:
        offsets.append(pos)
        node[pos:pos + len(rec)] = rec
        pos += len(rec)
    offsets.append(pos)                       # bos alanin basi
    if pos + 2 * len(offsets) > node_size:
        raise HfsFormatError(tr("B-agaci dugumu tasti"))
    for i, off in enumerate(offsets):
        struct.pack_into(">H", node, node_size - 2 * (i + 1), off)
    return bytes(node)


def _bits(used: List[int], nbytes: int, first: int = 0) -> bytes:
    out = bytearray(nbytes)
    for n in used:
        n -= first
        if 0 <= n < nbytes * 8:
            out[n >> 3] |= 0x80 >> (n & 7)
    return bytes(out)


def btree_file(node_size: int, clump: int, max_key: int, attrs: int,
               compare: int, leaves: List[bytes], depth: int = 0) -> bytes:
    """Tum B-agaci dosyasi: baslik dugumu (+ gerekirse harita dugumleri) ve
    verilen yaprak dugumleri (1'den itibaren, tek duzey)."""
    total = clump // node_size
    header_map_bytes = node_size - 256
    map_bytes = node_size - 20                 # harita dugumu kaydi
    extra = max(0, total - header_map_bytes * 8)
    map_nodes = (extra + map_bytes * 8 - 1) // (map_bytes * 8) if extra else 0
    first_map = 1 + len(leaves)
    used = list(range(first_map + map_nodes))
    free = total - len(used)
    if free < 0:
        raise HfsFormatError(tr("B-agaci icin yer yetersiz"))
    leaf_count = sum(struct.unpack_from(">H", n, 10)[0] for n in leaves)
    root = 1 if leaves else 0
    last = len(leaves) if leaves else 0
    header = struct.pack(">HIIIIHHIIHIBBI", depth if leaves else 0, root,
                         leaf_count, root, last, node_size, max_key, total,
                         free, 0, clump, 0, compare, attrs) + bytes(64)
    user = bytes(128)
    hmap = _bits(used, header_map_bytes)
    head = build_node(node_size, 1, 0, [header, user, hmap],
                      flink=first_map if map_nodes else 0)
    out = bytearray(head)
    for leaf in leaves:
        out += leaf
    for i in range(map_nodes):
        start_bit = header_map_bytes * 8 + i * map_bytes * 8
        record = _bits(used, map_bytes, start_bit)
        nxt = first_map + i + 1 if i + 1 < map_nodes else 0
        out += build_node(node_size, 2, 0, [record], flink=nxt)
    out += bytes(clump - len(out))
    return bytes(out)


def _fork(size: int, clump: int, start: int, blocks: int) -> bytes:
    ext = struct.pack(">II", start, blocks) if blocks else bytes(8)
    return struct.pack(">QII", size, clump, blocks) + ext + bytes(56)


def plan_layout(volume_bytes: int) -> Dict[str, int]:
    """Blok yerlesimi (hepsi blok cinsinden)."""
    total = volume_bytes // BLOCK_SIZE
    alloc_blocks = (((total + 7) // 8) + BLOCK_SIZE - 1) // BLOCK_SIZE
    cat_node = 8192 if volume_bytes // 512 >= 0x200000 else 4096
    ext_clump = btree_clump(volume_bytes, EXT_NODE, COL_EXTENTS)
    attr_clump = btree_clump(volume_bytes, ATTR_NODE, COL_ATTR)
    cat_clump = btree_clump(volume_bytes, cat_node, COL_CATALOG)
    ext_b, attr_b, cat_b = (ext_clump // BLOCK_SIZE, attr_clump // BLOCK_SIZE,
                            cat_clump // BLOCK_SIZE)
    cur = 1 + alloc_blocks
    ext_start = cur
    attr_start = ext_start + ext_b
    after_attr = attr_start + attr_b
    gap = 10 * attr_b
    if 2 * gap > total:
        gap = 0
    cat_start = after_attr + gap
    if cat_start + cat_b > total - 1:
        cat_start = after_attr
    if cat_start + cat_b > total - 1:
        raise HfsFormatError(tr("Birim HFS+ icin cok kucuk"))
    next_alloc = min(cat_start + cat_b + 10 * cat_b, total - 1)
    return dict(total=total, alloc_start=1, alloc_blocks=alloc_blocks,
                ext_start=ext_start, ext_blocks=ext_b, ext_clump=ext_clump,
                attr_start=attr_start, attr_blocks=attr_b, attr_clump=attr_clump,
                cat_start=cat_start, cat_blocks=cat_b, cat_clump=cat_clump,
                cat_node=cat_node, next_alloc=next_alloc)


def _write_zero(dev: BlockDevice, offset: int, length: int) -> None:
    chunk = bytes(min(length, 4 * MIB))
    while length > 0:
        n = min(length, len(chunk))
        dev.write(offset, chunk[:n])
        offset += n
        length -= n


def format_hfsplus(dev: BlockDevice, label: str = "", case_sensitive: bool = False,
                   progress: Optional[Callable[[str, int], None]] = None,
                   volume_uuid: Optional[bytes] = None) -> Dict[str, int]:
    """`dev` uzerinde bos, gunluksuz HFS+ (ya da HFSX) birimi olusturur."""
    def report(msg: str, pct: int) -> None:
        if progress:
            progress(msg, pct)

    size = dev.size
    if size < MIN_BYTES:
        raise HfsFormatError(tr("HFS+ icin en az 1 MB gerekir"))
    label = (label or DEFAULT_LABEL).replace(":", "/")
    L = plan_layout(size)
    bs = BLOCK_SIZE
    now = hfs_time()
    report(tr("HFS+ yerlesimi hazirlaniyor..."), 5)

    # --- katalog: kok klasor + iplik kaydi (tek yaprak) ---
    leaf = build_node(L["cat_node"], -1, 1, [
        catalog_key(1, label) + folder_record(2, now),
        catalog_key(2, "") + thread_record(3, 1, label),
    ])
    compare = 0xBC if case_sensitive else 0xCF
    catalog = btree_file(L["cat_node"], L["cat_clump"], CATALOG_MAX_KEY,
                         KEY_BIG | KEY_VARIABLE_INDEX, compare, [leaf], depth=1)
    extents = btree_file(EXT_NODE, L["ext_clump"], EXT_MAX_KEY, KEY_BIG, 0, [])
    attributes = btree_file(ATTR_NODE, L["attr_clump"], ATTR_MAX_KEY,
                            KEY_BIG | KEY_VARIABLE_INDEX, 0, [])

    # --- ayirma bitmap'i ---
    backup_offset = size - 1024
    backup_block = backup_offset // bs
    used = [0]
    for key in ("alloc", "ext", "attr", "cat"):
        used += range(L[key + "_start"], L[key + "_start"] + L[key + "_blocks"])
    # Son ayirma blogu her zaman dolu sayilir (mkfs.hfsplus da oyle; birim
    # boyu blok kati degilse yedek baslik bu blogun disinda kalsa bile).
    # Bos birakilirsa bir dosya onu alir ve fsck "Invalid extent entry" der.
    used.append(L["total"] - 1)
    if backup_block < L["total"]:
        used.append(backup_block)
    alloc = _bits(used, L["alloc_blocks"] * bs)
    free = L["total"] - len(set(used))

    # --- birim basligi ---
    header = bytearray(512)
    struct.pack_into(">2sHI4sI", header, 0, b"HX" if case_sensitive else b"H+",
                     5 if case_sensitive else 4,
                     VOL_UNMOUNTED | VOL_UNUSED_NODE_FIX, b"10.0", 0)
    struct.pack_into(">IIII", header, 16, hfs_time(local=True), now, 0, now)
    struct.pack_into(">IIIIIII", header, 32, 0, 0, bs, L["total"], free,
                     L["next_alloc"], 65536)
    struct.pack_into(">IIIQ", header, 60, 65536, FIRST_USER_CNID, 0, 1)
    header[104:112] = volume_uuid or os.urandom(8)          # finderInfo[6..7]
    header[112:192] = _fork(L["alloc_blocks"] * bs, L["alloc_blocks"] * bs,
                            1, L["alloc_blocks"])
    header[192:272] = _fork(L["ext_clump"], L["ext_clump"], L["ext_start"],
                            L["ext_blocks"])
    header[272:352] = _fork(L["cat_clump"], L["cat_clump"], L["cat_start"],
                            L["cat_blocks"])
    header[352:432] = _fork(L["attr_clump"], L["attr_clump"], L["attr_start"],
                            L["attr_blocks"])

    report(tr("HFS+ metaverisi yaziliyor..."), 30)
    block0 = bytearray(bs)
    block0[1024:1536] = header
    dev.write(0, bytes(block0))
    dev.write(bs, alloc)
    dev.write(L["ext_start"] * bs, extents)
    dev.write(L["attr_start"] * bs, attributes)
    report(tr("HFS+ katalogu yaziliyor..."), 60)
    dev.write(L["cat_start"] * bs, catalog)
    # yedek baslik ve birimin son 512 bayti
    _write_zero(dev, backup_block * bs, size - backup_block * bs)
    dev.write(backup_offset, bytes(header))
    flush = getattr(dev, "flush", None)
    if flush:
        flush()
    report(tr("Tamamlandi"), 100)
    return {"block_size": bs, "total_blocks": L["total"], "free_blocks": free,
            "catalog_node": L["cat_node"]}
