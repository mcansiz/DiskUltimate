"""Saf Python sikistirma cozuculeri: LZO1X ve Zstandard (ADR 0069).

Calisma zamani bagimliligi yok kurali geregi (CLAUDE.md) btrfs'in LZO ve
zstd sikistirmasi icin harici paket kullanilmaz. Python 3.14'un
`compression.zstd` modulu varsa hiz icin o kullanilir; yoksa bu cozucu.

* **LZO1X** — Linux `lib/lzo/lzo1x_decompress_safe.c` durum makinesi.
  btrfs cercevesi: toplam boy (u32 LE), ardindan bolumler: boy (u32 LE) +
  LZO1X verisi; bolum basligi sektor sinirini gecmez (gecerse sonraki
  sektore atlanir). Her bolum en fazla bir sektor acar.
* **Zstandard** — RFC 8878: cerceve, ham/RLE/sikistirilmis bloklar,
  Huffman (tek ve dort akis, agacsiz tekrar), FSE tablolari (onceden
  tanimli / RLE / sikistirilmis / tekrar), dizi cozme ve tekrar ofsetleri.
  Sozluk (dictionary) desteklenmez; atlanabilir cerceveler atlanir.
"""
from __future__ import annotations

import struct
from typing import List, Optional, Tuple

from ..i18n import tr


class DecompressError(Exception):
    pass


# =========================================================================
# LZO1X
# =========================================================================
M2_MAX_OFFSET = 0x0800


def _copy_match(out: bytearray, dist: int, length: int) -> None:
    start = len(out) - dist
    if start < 0:
        raise DecompressError(tr("LZO: gecersiz geri basvuru"))
    if dist >= length:
        out += out[start:start + length]
    else:
        for i in range(length):
            out.append(out[start + i])


def lzo1x_decompress(src: bytes, max_out: int = 1 << 30) -> bytes:
    out = bytearray()
    ip = 0
    n = len(src)
    state = 0

    def ext_len(ip: int, base: int) -> Tuple[int, int]:
        zeros = 0
        while src[ip] == 0:
            zeros += 1
            ip += 1
        return zeros * 255 + base + src[ip], ip + 1

    try:
        if src[0] > 17:
            t = src[0] - 17
            ip = 1
            if t < 4:
                out += src[ip:ip + t]
                ip += t
                state = t
            else:
                out += src[ip:ip + t]
                ip += t
                state = 4
        while True:
            t = src[ip]
            ip += 1
            if t < 16:
                if state == 0:
                    if t == 0:
                        t, ip = ext_len(ip, 15)
                    t += 3
                    out += src[ip:ip + t]
                    ip += t
                    state = 4
                    continue
                if state != 4:
                    nxt = t & 3
                    dist = 1 + (t >> 2) + (src[ip] << 2)
                    ip += 1
                    _copy_match(out, dist, 2)
                else:
                    nxt = t & 3
                    dist = 1 + M2_MAX_OFFSET + (t >> 2) + (src[ip] << 2)
                    ip += 1
                    _copy_match(out, dist, 3)
            elif t >= 64:
                nxt = t & 3
                dist = 1 + ((t >> 2) & 7) + (src[ip] << 3)
                ip += 1
                _copy_match(out, dist, (t >> 5) + 1)
            elif t >= 32:
                length = (t & 31) + 2
                if length == 2:
                    length, ip = ext_len(ip, 31)
                    length += 2
                v = src[ip] | (src[ip + 1] << 8)
                ip += 2
                dist = 1 + (v >> 2)
                nxt = v & 3
                _copy_match(out, dist, length)
            else:
                high = (t & 8) << 11
                length = (t & 7) + 2
                if length == 2:
                    length, ip = ext_len(ip, 7)
                    length += 2
                v = src[ip] | (src[ip + 1] << 8)
                ip += 2
                dist = high + (v >> 2)
                nxt = v & 3
                if dist == 0:
                    break                                   # akis sonu
                _copy_match(out, dist + 0x4000, length)
            state = nxt
            if nxt:
                out += src[ip:ip + nxt]
                ip += nxt
            if len(out) > max_out:
                raise DecompressError(tr("LZO: cikti siniri asildi"))
            if ip > n:
                raise DecompressError(tr("LZO: girdi beklenmedik bicimde bitti"))
    except IndexError as exc:
        raise DecompressError(tr("LZO: girdi beklenmedik bicimde bitti")) from exc
    return bytes(out)


def btrfs_lzo_decompress(src: bytes, ram_bytes: int, sector: int = 4096) -> bytes:
    """btrfs LZO cercevesi (fs/btrfs/lzo.c)."""
    if len(src) < 4:
        raise DecompressError(tr("LZO: cerceve cok kisa"))
    total = struct.unpack_from("<I", src, 0)[0]
    pos = 4
    out = bytearray()
    end = min(total, len(src))
    while pos < end and len(out) < ram_bytes:
        if sector - pos % sector < 4:
            pos += sector - pos % sector               # baslik sektor sinirini gecmez
            if pos >= end:
                break
        seg = struct.unpack_from("<I", src, pos)[0]
        pos += 4
        out += lzo1x_decompress(src[pos:pos + seg], sector)
        pos += seg
    return bytes(out[:ram_bytes])


# =========================================================================
# Zstandard (RFC 8878)
# =========================================================================
ZSTD_MAGIC = 0xFD2FB528

LL_DEFAULT = [4, 3, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 1, 1, 1, 2, 2, 2, 2, 2, 2, 2,
              2, 2, 3, 2, 1, 1, 1, 1, 1, -1, -1, -1, -1]
ML_DEFAULT = [1, 4, 3, 2, 2, 2, 2, 2, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
              1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
              -1, -1, -1, -1, -1, -1, -1]
OF_DEFAULT = [1, 1, 1, 1, 1, 1, 2, 2, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
              1, -1, -1, -1, -1, -1]
LL_BASE = list(range(16)) + [16, 18, 20, 22, 24, 28, 32, 40, 48, 64, 128, 256, 512,
                             1024, 2048, 4096, 8192, 16384, 32768, 65536]
LL_BITS = [0] * 16 + [1, 1, 1, 1, 2, 2, 3, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]
ML_BASE = [i + 3 for i in range(32)] + [35, 37, 39, 41, 43, 47, 51, 59, 67, 83, 99,
                                        131, 259, 515, 1027, 2051, 4099, 8195, 16387,
                                        32771, 65539]
ML_BITS = [0] * 32 + [1, 1, 1, 1, 2, 2, 3, 3, 4, 4, 5, 7, 8, 9, 10, 11, 12, 13, 14,
                      15, 16]


def _highbit(v: int) -> int:
    return v.bit_length() - 1


class _RevBits:
    """Geriye dogru bit okuyucu (zstd: son bayttaki ilk 1 biti isarettir)."""

    def __init__(self, data: bytes):
        if not data or data[-1] == 0:
            raise DecompressError(tr("zstd: bit akisi bozuk"))
        self.data = data
        self.pos = (len(data) - 1) * 8 + _highbit(data[-1])

    def read(self, n: int) -> int:
        if n == 0:
            return 0
        v = self.peek(n)
        self.pos -= n
        return v

    def peek(self, n: int) -> int:
        if n == 0:
            return 0
        start = self.pos - n
        if start >= 0:
            b0 = start >> 3
            b1 = (self.pos + 7) >> 3
            return (int.from_bytes(self.data[b0:b1], "little") >> (start & 7)) & ((1 << n) - 1)
        # akisin basini asan okuma: eksik bitler sifirdir
        avail = max(0, self.pos)
        v = (int.from_bytes(self.data[0:(avail + 7) >> 3], "little") & ((1 << avail) - 1)) \
            if avail else 0
        return (v << (n - avail)) & ((1 << n) - 1)

    @property
    def overflow(self) -> bool:
        return self.pos < 0

    @property
    def finished(self) -> bool:
        return self.pos == 0


def _fwd_bits(data: bytes, bitpos: int, n: int) -> int:
    b0 = bitpos >> 3
    return (int.from_bytes(data[b0:b0 + 8].ljust(8, b"\x00"), "little")
            >> (bitpos & 7)) & ((1 << n) - 1)


def _read_ncount(data: bytes, max_symbol: int, max_log: int) -> Tuple[List[int], int, int]:
    """FSE tablo aciklamasi -> (normal sayilar, dogruluk log, tuketilen bayt)."""
    acc = (data[0] & 0x0F) + 5
    if acc > max_log:
        raise DecompressError(tr("zstd: FSE dogruluk logu cok buyuk"))
    bit = 4
    remaining = (1 << acc) + 1
    threshold = 1 << acc
    nbits = acc + 1
    counts: List[int] = []
    previous0 = False
    while remaining > 1 and len(counts) <= max_symbol:
        if previous0:
            while True:
                rep = _fwd_bits(data, bit, 2)
                bit += 2
                counts.extend([0] * rep)
                if rep != 3:
                    break
            if len(counts) > max_symbol:
                break
        maxv = (2 * threshold - 1) - remaining
        low = _fwd_bits(data, bit, nbits - 1)
        if low < maxv:
            count = low
            bit += nbits - 1
        else:
            count = _fwd_bits(data, bit, nbits)
            if count >= threshold:
                count -= maxv
            bit += nbits
        count -= 1
        remaining -= abs(count)
        counts.append(count)
        previous0 = count == 0
        while remaining < threshold:
            nbits -= 1
            threshold >>= 1
    if remaining != 1:
        raise DecompressError(tr("zstd: FSE tablosu bozuk"))
    return counts, acc, (bit + 7) >> 3


def _build_fse(counts: List[int], acc: int) -> List[Tuple[int, int, int]]:
    """[(sembol, bit sayisi, taban)] — RFC 8878 4.1.1."""
    size = 1 << acc
    symbols = [0] * size
    high = size - 1
    nxt = [0] * len(counts)
    for s, c in enumerate(counts):
        if c == -1:
            symbols[high] = s
            high -= 1
            nxt[s] = 1
        else:
            nxt[s] = c
    step = (size >> 1) + (size >> 3) + 3
    mask = size - 1
    pos = 0
    for s, c in enumerate(counts):
        for _ in range(max(c, 0)):
            symbols[pos] = s
            pos = (pos + step) & mask
            while pos > high:
                pos = (pos + step) & mask
    table = []
    for i in range(size):
        s = symbols[i]
        state = nxt[s]
        nxt[s] += 1
        nb = acc - _highbit(state)
        table.append((s, nb, (state << nb) - size))
    return table


def _rle_table(symbol: int) -> List[Tuple[int, int, int]]:
    return [(symbol, 0, 0)]


class _Huffman:
    def __init__(self, weights: List[int]):
        total = sum(1 << (w - 1) for w in weights if w)
        if total == 0:
            raise DecompressError(tr("zstd: Huffman agirliklari bos"))
        max_bits = _highbit(total) + 1
        rest = (1 << max_bits) - total
        if rest & (rest - 1):
            raise DecompressError(tr("zstd: Huffman agirliklari gecersiz"))
        weights = weights + [_highbit(rest) + 1]
        self.bits = max_bits
        rank_count = [0] * (max_bits + 2)
        for w in weights:
            rank_count[w] += 1
        start = [0] * (max_bits + 2)
        nxt = 0
        for w in range(1, max_bits + 1):
            start[w] = nxt
            nxt += rank_count[w] << (w - 1)
        self.table: List[Tuple[int, int]] = [(0, 0)] * (1 << max_bits)
        for sym, w in enumerate(weights):
            if not w:
                continue
            length = (1 << w) >> 1
            entry = (sym, max_bits + 1 - w)
            for u in range(start[w], start[w] + length):
                self.table[u] = entry
            start[w] += length

    def decode_stream(self, data: bytes, count: int) -> bytes:
        br = _RevBits(data)
        out = bytearray()
        table, bits = self.table, self.bits
        for _ in range(count):
            sym, nb = table[br.peek(bits)]
            br.pos -= nb
            out.append(sym)
        if br.pos != 0:
            raise DecompressError(tr("zstd: Huffman akisi tutarsiz"))
        return bytes(out)


def _read_huffman(data: bytes) -> Tuple[_Huffman, int]:
    hb = data[0]
    if hb >= 128:
        n = hb - 127
        weights = []
        for i in range(n):
            byte = data[1 + i // 2]
            weights.append(byte >> 4 if i % 2 == 0 else byte & 0xF)
        return _Huffman(weights), 1 + (n + 1) // 2
    comp = data[1:1 + hb]
    counts, acc, used = _read_ncount(comp, 255, 6)
    table = _build_fse(counts, acc)
    br = _RevBits(comp[used:])
    s1 = br.read(acc)
    s2 = br.read(acc)
    weights: List[int] = []
    while True:
        sym, nb, base = table[s1]
        weights.append(sym)
        s1 = base + br.read(nb)
        if br.overflow:
            weights.append(table[s2][0])
            break
        sym, nb, base = table[s2]
        weights.append(sym)
        s2 = base + br.read(nb)
        if br.overflow:
            weights.append(table[s1][0])
            break
        if len(weights) > 255:
            raise DecompressError(tr("zstd: Huffman agirliklari cok uzun"))
    return _Huffman(weights), 1 + hb


class _ZstdFrame:
    def __init__(self) -> None:
        self.out = bytearray()
        self.huffman: Optional[_Huffman] = None
        self.tables = {"ll": None, "of": None, "ml": None}
        self.rep = [1, 4, 8]

    # -- literaller --------------------------------------------------------
    def literals(self, data: bytes) -> Tuple[bytes, int]:
        b0 = data[0]
        ltype, sf = b0 & 3, (b0 >> 2) & 3
        if ltype in (0, 1):
            if sf in (0, 2):
                size, hsize = b0 >> 3, 1
            elif sf == 1:
                size, hsize = (b0 >> 4) | (data[1] << 4), 2
            else:
                size, hsize = (b0 >> 4) | (data[1] << 4) | (data[2] << 12), 3
            if ltype == 0:
                return data[hsize:hsize + size], hsize + size
            return bytes([data[hsize]]) * size, hsize + 1
        if sf in (0, 1):
            h = int.from_bytes(data[0:3], "little")
            regen, comp, hsize = (h >> 4) & 0x3FF, (h >> 14) & 0x3FF, 3
        elif sf == 2:
            h = int.from_bytes(data[0:4], "little")
            regen, comp, hsize = (h >> 4) & 0x3FFF, (h >> 18) & 0x3FFF, 4
        else:
            h = int.from_bytes(data[0:5], "little")
            regen, comp, hsize = (h >> 4) & 0x3FFFF, (h >> 22) & 0x3FFFF, 5
        body = data[hsize:hsize + comp]
        pos = 0
        if ltype == 2:
            self.huffman, pos = _read_huffman(body)
        elif self.huffman is None:
            raise DecompressError(tr("zstd: tekrar Huffman tablosu yok"))
        streams = body[pos:]
        if sf == 0:
            lits = self.huffman.decode_stream(streams, regen)
        else:
            s1, s2, s3 = struct.unpack_from("<HHH", streams, 0)
            parts = [streams[6:6 + s1], streams[6 + s1:6 + s1 + s2],
                     streams[6 + s1 + s2:6 + s1 + s2 + s3], streams[6 + s1 + s2 + s3:]]
            each = (regen + 3) // 4
            lits = b"".join(self.huffman.decode_stream(p, each if i < 3 else regen - 3 * each)
                            for i, p in enumerate(parts))
        return lits, hsize + comp

    # -- diziler ---------------------------------------------------------------
    def _table(self, kind: str, mode: int, data: bytes, pos: int,
               default: List[int], default_log: int, max_sym: int, max_log: int) -> int:
        if mode == 0:
            self.tables[kind] = _build_fse(default, default_log)
        elif mode == 1:
            self.tables[kind] = _rle_table(data[pos])
            pos += 1
        elif mode == 2:
            counts, acc, used = _read_ncount(data[pos:], max_sym, max_log)
            self.tables[kind] = _build_fse(counts, acc)
            pos += used
        elif self.tables[kind] is None:
            raise DecompressError(tr("zstd: tekrar FSE tablosu yok"))
        return pos

    def block(self, data: bytes) -> None:
        lits, pos = self.literals(data)
        b0 = data[pos]
        if b0 == 0:
            self.out += lits
            return
        if b0 < 128:
            nseq, pos = b0, pos + 1
        elif b0 < 255:
            nseq, pos = ((b0 - 128) << 8) + data[pos + 1], pos + 2
        else:
            nseq, pos = data[pos + 1] + (data[pos + 2] << 8) + 0x7F00, pos + 3
        modes = data[pos]
        pos += 1
        pos = self._table("ll", modes >> 6, data, pos, LL_DEFAULT, 6, 35, 9)
        pos = self._table("of", (modes >> 4) & 3, data, pos, OF_DEFAULT, 5, 31, 8)
        pos = self._table("ml", (modes >> 2) & 3, data, pos, ML_DEFAULT, 6, 52, 9)
        ll_t, of_t, ml_t = self.tables["ll"], self.tables["of"], self.tables["ml"]
        br = _RevBits(data[pos:])
        ll_s = br.read(_highbit(len(ll_t)))
        of_s = br.read(_highbit(len(of_t)))
        ml_s = br.read(_highbit(len(ml_t)))
        out = self.out
        rep = self.rep
        lit_pos = 0
        for i in range(nseq):
            ll_code, ll_nb, ll_base = ll_t[ll_s]
            of_code, of_nb, of_base = of_t[of_s]
            ml_code, ml_nb, ml_base = ml_t[ml_s]
            if of_code > 31 or ll_code > 35 or ml_code > 52:
                raise DecompressError(tr("zstd: gecersiz dizi kodu"))
            ov = (1 << of_code) + br.read(of_code)
            ml = ML_BASE[ml_code] + br.read(ML_BITS[ml_code])
            ll = LL_BASE[ll_code] + br.read(LL_BITS[ll_code])
            if ov > 3:
                offset = ov - 3
                rep[2], rep[1], rep[0] = rep[1], rep[0], offset
            else:
                idx = ov + (1 if ll == 0 else 0)
                if idx == 1:
                    offset = rep[0]
                elif idx == 2:
                    offset = rep[1]
                    rep[1], rep[0] = rep[0], offset
                elif idx == 3:
                    offset = rep[2]
                    rep[2], rep[1], rep[0] = rep[1], rep[0], offset
                else:
                    offset = rep[0] - 1
                    if offset <= 0:
                        raise DecompressError(tr("zstd: gecersiz tekrar ofseti"))
                    rep[2], rep[1], rep[0] = rep[1], rep[0], offset
            out += lits[lit_pos:lit_pos + ll]
            lit_pos += ll
            start = len(out) - offset
            if start < 0:
                raise DecompressError(tr("zstd: ofset ciktinin disinda"))
            if offset >= ml:
                out += out[start:start + ml]
            else:
                chunk = out[start:]
                reps, rem = divmod(ml, offset)
                out += chunk * reps + chunk[:rem]
            if i + 1 < nseq:
                ll_s = ll_base + br.read(ll_nb)
                ml_s = ml_base + br.read(ml_nb)
                of_s = of_base + br.read(of_nb)
        if not br.finished:
            raise DecompressError(tr("zstd: dizi akisi tutarsiz"))
        out += lits[lit_pos:]


def _zstd_pure(src: bytes) -> bytes:
    pos = 0
    result = bytearray()
    while pos + 4 <= len(src):
        magic = struct.unpack_from("<I", src, pos)[0]
        if (magic & 0xFFFFFFF0) == 0x184D2A50:            # atlanabilir cerceve
            size = struct.unpack_from("<I", src, pos + 4)[0]
            pos += 8 + size
            continue
        if magic != ZSTD_MAGIC:
            if result:
                break                                      # sonda dolgu (btrfs)
            raise DecompressError(tr("zstd: cerceve imzasi yok"))
        pos += 4
        fhd = src[pos]
        pos += 1
        fcs_flag, single, checksum, dict_flag = fhd >> 6, (fhd >> 5) & 1, (fhd >> 2) & 1, fhd & 3
        if not single:
            pos += 1                                       # pencere tanimlayicisi
        dict_size = (0, 1, 2, 4)[dict_flag]
        if dict_size and int.from_bytes(src[pos:pos + dict_size], "little"):
            raise DecompressError(tr("zstd: sozluklu cerceve desteklenmiyor"))
        pos += dict_size
        fcs_size = (1 if single else 0, 2, 4, 8)[fcs_flag]
        pos += fcs_size
        frame = _ZstdFrame()
        while True:
            hdr = int.from_bytes(src[pos:pos + 3], "little")
            pos += 3
            last, btype, bsize = hdr & 1, (hdr >> 1) & 3, hdr >> 3
            if btype == 0:
                frame.out += src[pos:pos + bsize]
                pos += bsize
            elif btype == 1:
                frame.out += bytes([src[pos]]) * bsize
                pos += 1
            elif btype == 2:
                frame.block(src[pos:pos + bsize])
                pos += bsize
            else:
                raise DecompressError(tr("zstd: ayrilmis blok turu"))
            if last:
                break
        if checksum:
            pos += 4
        result += frame.out
    return bytes(result)


def zstd_decompress(src: bytes) -> bytes:
    try:
        from compression import zstd as _native          # Python 3.14+
    except ImportError:
        _native = None
    if _native is not None:
        try:
            return _native.decompress(src)
        except Exception:                                  # noqa: BLE001
            pass
    try:
        return _zstd_pure(src)
    except (IndexError, struct.error) as exc:
        raise DecompressError(tr("zstd: girdi beklenmedik bicimde bitti")) from exc
