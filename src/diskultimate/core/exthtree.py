"""ext3/4 htree (dir_index) — ad karmasi ve indeks yapilari.

Buyuk dizinler cekirdek tarafindan karma agaciyla indekslenir: dizinin 0.
blogu `dx_root`tur ("." ve ".." kayitlarinin arkasinda indeks), girisler
ad karmasina gore yapraklara dagitilir. Indeksli dizine giris eklemek icin
karmayi **birebir** ayni hesaplamak gerekir; yanlis yaprakta duran giris
cekirdek tarafindan bulunamaz, e2fsck "unordered hash table" der.

Karma islevleri e2fsprogs `lib/ext2fs/dirhash.c` ile aynidir (legacy,
half-MD4, TEA ve "imzasiz karakter" turevleri). Dogrulama: `debugfs dx_hash`
ciktisiyla karsilastirilir (tests/ext_write_check).

dx_root yerlesimi (blok 0):
    0x00  "." kaydi (12 bayt)
    0x0C  ".." kaydi (rec_len = blok - 12)
    0x18  dx_root_info: reserved(4) hash_version(1) info_length(1)=8
                        indirect_levels(1) unused_flags(1)
    0x20  dx_countlimit: limit(2) count(2) + ilk girisin blok alani(4)
    0x28  girisler: (hash(4), blok(4)) * (count-1)
Ara dugum (dx_node): sahte dirent (inode 0, rec_len = blok) + countlimit @8.
metadata_csum acikken dizinin sonunda dx_tail (8 bayt) limit'i bir azaltir.
"""
from __future__ import annotations

import struct
from typing import List, Optional, Tuple

M32 = 0xFFFFFFFF

HASH_LEGACY, HASH_HALF_MD4, HASH_TEA = 0, 1, 2
HASH_LEGACY_U, HASH_HALF_MD4_U, HASH_TEA_U = 3, 4, 5
HASH_SIPHASH = 6

DX_ROOT_COUNT = 0x20
DX_NODE_COUNT = 0x08


def _str2hashbuf(msg: bytes, num: int, unsigned: bool) -> List[int]:
    length = len(msg)
    pad = (length | (length << 8)) & M32
    pad = (pad | (pad << 16)) & M32
    val = pad
    out: List[int] = []
    n = min(length, num * 4)
    for i in range(n):
        c = msg[i]
        if not unsigned and c >= 128:
            c -= 256                         # imzali char
        val = (c + (val << 8)) & M32
        if i % 4 == 3:
            out.append(val)
            val = pad
            num -= 1
    num -= 1
    if num >= 0:
        out.append(val)
    while num > 0:
        out.append(pad)
        num -= 1
    return out


def _rotl(x: int, s: int) -> int:
    x &= M32
    return ((x << s) | (x >> (32 - s))) & M32


def _half_md4(buf: List[int], inp: List[int]) -> None:
    a, b, c, d = buf
    F = lambda x, y, z: z ^ (x & (y ^ z))              # noqa: E731
    G = lambda x, y, z: ((x & y) + ((x ^ y) & z)) & M32  # noqa: E731
    H = lambda x, y, z: x ^ y ^ z                      # noqa: E731
    K2, K3 = 0o13240474631, 0o15666365641

    def rnd(f, a, b, c, d, x, s):
        return _rotl((a + f(b, c, d) + x) & M32, s)

    a = rnd(F, a, b, c, d, inp[0], 3); d = rnd(F, d, a, b, c, inp[1], 7)   # noqa: E702
    c = rnd(F, c, d, a, b, inp[2], 11); b = rnd(F, b, c, d, a, inp[3], 19)  # noqa: E702
    a = rnd(F, a, b, c, d, inp[4], 3); d = rnd(F, d, a, b, c, inp[5], 7)   # noqa: E702
    c = rnd(F, c, d, a, b, inp[6], 11); b = rnd(F, b, c, d, a, inp[7], 19)  # noqa: E702

    a = rnd(G, a, b, c, d, inp[1] + K2, 3); d = rnd(G, d, a, b, c, inp[3] + K2, 5)    # noqa: E702
    c = rnd(G, c, d, a, b, inp[5] + K2, 9); b = rnd(G, b, c, d, a, inp[7] + K2, 13)   # noqa: E702
    a = rnd(G, a, b, c, d, inp[0] + K2, 3); d = rnd(G, d, a, b, c, inp[2] + K2, 5)    # noqa: E702
    c = rnd(G, c, d, a, b, inp[4] + K2, 9); b = rnd(G, b, c, d, a, inp[6] + K2, 13)   # noqa: E702

    a = rnd(H, a, b, c, d, inp[3] + K3, 3); d = rnd(H, d, a, b, c, inp[7] + K3, 9)    # noqa: E702
    c = rnd(H, c, d, a, b, inp[2] + K3, 11); b = rnd(H, b, c, d, a, inp[6] + K3, 15)  # noqa: E702
    a = rnd(H, a, b, c, d, inp[1] + K3, 3); d = rnd(H, d, a, b, c, inp[5] + K3, 9)    # noqa: E702
    c = rnd(H, c, d, a, b, inp[0] + K3, 11); b = rnd(H, b, c, d, a, inp[4] + K3, 15)  # noqa: E702

    buf[0] = (buf[0] + a) & M32
    buf[1] = (buf[1] + b) & M32
    buf[2] = (buf[2] + c) & M32
    buf[3] = (buf[3] + d) & M32


def _tea(buf: List[int], inp: List[int]) -> None:
    s = 0
    b0, b1 = buf[0], buf[1]
    a, b, c, d = inp
    for _ in range(16):
        s = (s + 0x9E3779B9) & M32
        b0 = (b0 + ((((b1 << 4) + a) & M32) ^ ((b1 + s) & M32)
                    ^ (((b1 >> 5) + b) & M32))) & M32
        b1 = (b1 + ((((b0 << 4) + c) & M32) ^ ((b0 + s) & M32)
                    ^ (((b0 >> 5) + d) & M32))) & M32
    buf[0] = (buf[0] + b0) & M32
    buf[1] = (buf[1] + b1) & M32


def _legacy(name: bytes, unsigned: bool) -> int:
    hash0, hash1 = 0x12A3FE2D, 0x37ABE8F9
    for ch in name:
        c = ch if unsigned or ch < 128 else ch - 256
        h = (hash1 + (hash0 ^ ((c * 7152373) & M32))) & M32
        if h & 0x80000000:
            h = (h - 0x7FFFFFFF) & M32
        hash1, hash0 = hash0, h
    return (hash0 << 1) & M32


def dirhash(version: int, name: bytes, seed: Tuple[int, int, int, int] = (0, 0, 0, 0)
            ) -> Tuple[int, int]:
    """(karma, ikincil karma) — e2fsprogs `ext2fs_dirhash` ile ayni."""
    buf = [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476]
    if any(seed):
        buf = list(seed)
    unsigned = version in (HASH_LEGACY_U, HASH_HALF_MD4_U, HASH_TEA_U)
    minor = 0
    if version in (HASH_LEGACY, HASH_LEGACY_U):
        h = _legacy(name, unsigned)
    elif version in (HASH_HALF_MD4, HASH_HALF_MD4_U):
        p, left = 0, len(name)
        while left > 0:
            _half_md4(buf, _str2hashbuf(name[p:], 8, unsigned))
            left -= 32
            p += 32
        h, minor = buf[1], buf[2]
    elif version in (HASH_TEA, HASH_TEA_U):
        p, left = 0, len(name)
        while left > 0:
            _tea(buf, _str2hashbuf(name[p:], 4, unsigned))
            left -= 16
            p += 16
        h, minor = buf[0], buf[1]
    else:
        raise ValueError(f"desteklenmeyen dizin karmasi: {version}")
    return h & ~1 & M32, minor


# ----------------------------------------------------------------------
# dx yapilari
# ----------------------------------------------------------------------
def count_limit(buf: bytes, off: int) -> Tuple[int, int]:
    limit, count = struct.unpack_from("<HH", buf, off)
    return limit, count


def entries(buf: bytes, off: int) -> List[Tuple[int, int]]:
    """[(hash, blok)] — ilk girisin karmasi 0 sayilir."""
    _limit, count = count_limit(buf, off)
    out = [(0, struct.unpack_from("<I", buf, off + 4)[0])]
    for i in range(1, count):
        h, blk = struct.unpack_from("<II", buf, off + i * 8)
        out.append((h, blk))
    return out


def write_entries(buf: bytearray, off: int, items: List[Tuple[int, int]]) -> None:
    limit, _count = count_limit(buf, off)
    struct.pack_into("<HH", buf, off, limit, len(items))
    struct.pack_into("<I", buf, off + 4, items[0][1])
    for i, (h, blk) in enumerate(items[1:], 1):
        struct.pack_into("<II", buf, off + i * 8, h, blk)


def find_slot(items: List[Tuple[int, int]], target: int) -> int:
    """Karma `target`in dustugu girisin sirasi (son `hash <= target`)."""
    pos = 0
    for i, (h, _blk) in enumerate(items):
        if i and h > target:
            break
        pos = i
    return pos


def root_info(buf: bytes) -> Tuple[int, int, int]:
    """(hash_version, info_length, indirect_levels)"""
    return buf[0x1C], buf[0x1D], buf[0x1E]
