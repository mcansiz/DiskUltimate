"""BitLocker birimini **kilit açma** — saf Python (ADR 0099).

Bu modül unutulmuş bir parolayı *kırmaz*. Kullanıcı parolayı ya da 48 haneli
kurtarma anahtarını verirse birimi açıp içeriğini (NTFS/FAT) okunabilir kılar;
DiskGenius'un yaptığı gibi *kilit açma*, kurtarma değildir. Yanlış parola
AES-CCM MAC doğrulamasında anlaşılır (sessiz bozulma yok).

Katmanlar:
  * Kripto temelleri: AES (128/256), AES-XTS (plain64), AES-CBC+EBOIV,
    AES-CCM (RFC 3610, MAC doğrulamalı). SHA-256 ``hashlib``'ten.
  * Metaveri çözümleme + anahtar türetme (parola germe, kurtarma anahtarı).
  * ``BitLockerVolume`` — kaynağın üstüne binen, okurken düz metin üreten
    ``BlockDevice``.

Çalışma zamanı bağımlılığı yoktur (CLAUDE.md). Çekirdek PyQt import etmez.

Referans: cryptsetup ``lib/bitlk`` (byte düzeyi yerleşim ve segment eşlemesi).
Kripto temelleri kamuya açık vektörlerle doğrulanır (``tests/bitlocker_check``);
uçtan uca gerçek imaj doğrulaması CI'da ``cryptsetup bitlkOpen`` ile yapılır.
"""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field
from typing import List, Optional

from ..i18n import mark, tr
from .image import BlockDevice


class BitLockerError(Exception):
    """BitLocker çözümleme / kilit açma hatası."""


@dataclass
class UnlockKey:
    """Kullanıcının verdiği sır — parola ya da 48 haneli kurtarma anahtarı."""
    password: Optional[str] = None
    recovery: Optional[str] = None

    def __bool__(self) -> bool:
        return bool(self.password or self.recovery)


# ======================================================================
# AES (saf Python, T-tabloları)
# ======================================================================
_SBOX = bytes.fromhex(
    "637c777bf26b6fc53001672bfed7ab76ca82c97dfa5947f0add4a2af9ca472c0"
    "b7fd9326363ff7cc34a5e5f171d8311504c723c31896059a071280e2eb27b275"
    "09832c1a1b6e5aa0523bd6b329e32f8453d100ed20fcb15b6acbbe394a4c58cf"
    "d0efaafb434d338545f9027f503c9fa851a3408f929d38f5bcb6da2110fff3d2"
    "cd0c13ec5f974417c4a77e3d645d197360814fdc222a908846eeb814de5e0bdb"
    "e0323a0a4906245cc2d3ac629195e479e7c8376d8dd54ea96c56f4ea657aae08"
    "ba78252e1ca6b4c6e8dd741f4bbd8b8a703eb5664803f60e613557b986c11d9e"
    "e1f8981169d98e949b1e87e9ce5528df8ca1890dbfe6426841992d0fb054bb16")
_INV_SBOX = bytearray(256)
for _i, _v in enumerate(_SBOX):
    _INV_SBOX[_v] = _i
_INV_SBOX = bytes(_INV_SBOX)

_RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36,
         0x6C, 0xD8, 0xAB, 0x4D)


def _xtime(a: int) -> int:
    a <<= 1
    if a & 0x100:
        a ^= 0x11B
    return a & 0xFF


def _mul(a: int, b: int) -> int:
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        b >>= 1
        a = _xtime(a)
    return p


def _build_tables():
    te = [[0] * 256 for _ in range(4)]
    td = [[0] * 256 for _ in range(4)]
    for x in range(256):
        s = _SBOX[x]
        s2, s3 = _xtime(s), _xtime(s) ^ s
        te[0][x] = (s2 << 24) | (s << 16) | (s << 8) | s3
        for j in range(1, 4):
            te[j][x] = ((te[j - 1][x] << 24) | (te[j - 1][x] >> 8)) & 0xFFFFFFFF
        si = _INV_SBOX[x]
        m9, mb, md, me = _mul(si, 9), _mul(si, 11), _mul(si, 13), _mul(si, 14)
        td[0][x] = (me << 24) | (m9 << 16) | (md << 8) | mb
        for j in range(1, 4):
            td[j][x] = ((td[j - 1][x] << 24) | (td[j - 1][x] >> 8)) & 0xFFFFFFFF
    return te, td


_TE, _TD = _build_tables()


class AES:
    """AES-128 / AES-256 tek blok (16 bayt) şifreleme/çözme."""

    def __init__(self, key: bytes):
        nk = len(key) // 4
        if nk not in (4, 8):
            raise BitLockerError(tr("Desteklenmeyen AES anahtar boyu: {} bayt",
                                    len(key)))
        self.rounds = nk + 6
        self._expand(key, nk)

    def _expand(self, key: bytes, nk: int):
        nr = self.rounds
        words = list(struct.unpack(">%dI" % nk, key))
        for i in range(nk, 4 * (nr + 1)):
            t = words[i - 1]
            if i % nk == 0:
                t = ((t << 8) | (t >> 24)) & 0xFFFFFFFF
                t = ((_SBOX[(t >> 24) & 0xFF] << 24) |
                     (_SBOX[(t >> 16) & 0xFF] << 16) |
                     (_SBOX[(t >> 8) & 0xFF] << 8) |
                     _SBOX[t & 0xFF])
                t ^= _RCON[i // nk - 1] << 24
            elif nk > 6 and i % nk == 4:
                t = ((_SBOX[(t >> 24) & 0xFF] << 24) |
                     (_SBOX[(t >> 16) & 0xFF] << 16) |
                     (_SBOX[(t >> 8) & 0xFF] << 8) |
                     _SBOX[t & 0xFF])
            words.append(words[i - nk] ^ t)
        self.ek = words
        # Eşdeğer ters şifre: tur anahtarları ters sırada + orta turlara
        # InvMixColumns uygulanır.
        dk = [0] * (4 * (nr + 1))
        for i in range(nr + 1):
            for j in range(4):
                dk[4 * i + j] = words[4 * (nr - i) + j]
        for i in range(4, 4 * nr):
            w = dk[i]
            dk[i] = (_TD[0][_SBOX[(w >> 24) & 0xFF]] ^
                     _TD[1][_SBOX[(w >> 16) & 0xFF]] ^
                     _TD[2][_SBOX[(w >> 8) & 0xFF]] ^
                     _TD[3][_SBOX[w & 0xFF]])
        self.dk = dk

    def encrypt_block(self, block: bytes) -> bytes:
        ek = self.ek
        s0, s1, s2, s3 = struct.unpack(">4I", block)
        s0 ^= ek[0]; s1 ^= ek[1]; s2 ^= ek[2]; s3 ^= ek[3]
        te0, te1, te2, te3 = _TE
        k = 4
        for _ in range(self.rounds - 1):
            t0 = (te0[(s0 >> 24) & 0xFF] ^ te1[(s1 >> 16) & 0xFF] ^
                  te2[(s2 >> 8) & 0xFF] ^ te3[s3 & 0xFF] ^ ek[k])
            t1 = (te0[(s1 >> 24) & 0xFF] ^ te1[(s2 >> 16) & 0xFF] ^
                  te2[(s3 >> 8) & 0xFF] ^ te3[s0 & 0xFF] ^ ek[k + 1])
            t2 = (te0[(s2 >> 24) & 0xFF] ^ te1[(s3 >> 16) & 0xFF] ^
                  te2[(s0 >> 8) & 0xFF] ^ te3[s1 & 0xFF] ^ ek[k + 2])
            t3 = (te0[(s3 >> 24) & 0xFF] ^ te1[(s0 >> 16) & 0xFF] ^
                  te2[(s1 >> 8) & 0xFF] ^ te3[s2 & 0xFF] ^ ek[k + 3])
            s0, s1, s2, s3 = t0, t1, t2, t3
            k += 4
        sb = _SBOX
        o0 = ((sb[(s0 >> 24) & 0xFF] << 24) | (sb[(s1 >> 16) & 0xFF] << 16) |
              (sb[(s2 >> 8) & 0xFF] << 8) | sb[s3 & 0xFF]) ^ ek[k]
        o1 = ((sb[(s1 >> 24) & 0xFF] << 24) | (sb[(s2 >> 16) & 0xFF] << 16) |
              (sb[(s3 >> 8) & 0xFF] << 8) | sb[s0 & 0xFF]) ^ ek[k + 1]
        o2 = ((sb[(s2 >> 24) & 0xFF] << 24) | (sb[(s3 >> 16) & 0xFF] << 16) |
              (sb[(s0 >> 8) & 0xFF] << 8) | sb[s1 & 0xFF]) ^ ek[k + 2]
        o3 = ((sb[(s3 >> 24) & 0xFF] << 24) | (sb[(s0 >> 16) & 0xFF] << 16) |
              (sb[(s1 >> 8) & 0xFF] << 8) | sb[s2 & 0xFF]) ^ ek[k + 3]
        return struct.pack(">4I", o0 & 0xFFFFFFFF, o1 & 0xFFFFFFFF,
                           o2 & 0xFFFFFFFF, o3 & 0xFFFFFFFF)

    def decrypt_block(self, block: bytes) -> bytes:
        dk = self.dk
        s0, s1, s2, s3 = struct.unpack(">4I", block)
        s0 ^= dk[0]; s1 ^= dk[1]; s2 ^= dk[2]; s3 ^= dk[3]
        td0, td1, td2, td3 = _TD
        k = 4
        for _ in range(self.rounds - 1):
            t0 = (td0[(s0 >> 24) & 0xFF] ^ td1[(s3 >> 16) & 0xFF] ^
                  td2[(s2 >> 8) & 0xFF] ^ td3[s1 & 0xFF] ^ dk[k])
            t1 = (td0[(s1 >> 24) & 0xFF] ^ td1[(s0 >> 16) & 0xFF] ^
                  td2[(s3 >> 8) & 0xFF] ^ td3[s2 & 0xFF] ^ dk[k + 1])
            t2 = (td0[(s2 >> 24) & 0xFF] ^ td1[(s1 >> 16) & 0xFF] ^
                  td2[(s0 >> 8) & 0xFF] ^ td3[s3 & 0xFF] ^ dk[k + 2])
            t3 = (td0[(s3 >> 24) & 0xFF] ^ td1[(s2 >> 16) & 0xFF] ^
                  td2[(s1 >> 8) & 0xFF] ^ td3[s0 & 0xFF] ^ dk[k + 3])
            s0, s1, s2, s3 = t0, t1, t2, t3
            k += 4
        ib = _INV_SBOX
        o0 = ((ib[(s0 >> 24) & 0xFF] << 24) | (ib[(s3 >> 16) & 0xFF] << 16) |
              (ib[(s2 >> 8) & 0xFF] << 8) | ib[s1 & 0xFF]) ^ dk[k]
        o1 = ((ib[(s1 >> 24) & 0xFF] << 24) | (ib[(s0 >> 16) & 0xFF] << 16) |
              (ib[(s3 >> 8) & 0xFF] << 8) | ib[s2 & 0xFF]) ^ dk[k + 1]
        o2 = ((ib[(s2 >> 24) & 0xFF] << 24) | (ib[(s1 >> 16) & 0xFF] << 16) |
              (ib[(s0 >> 8) & 0xFF] << 8) | ib[s3 & 0xFF]) ^ dk[k + 2]
        o3 = ((ib[(s3 >> 24) & 0xFF] << 24) | (ib[(s2 >> 16) & 0xFF] << 16) |
              (ib[(s1 >> 8) & 0xFF] << 8) | ib[s0 & 0xFF]) ^ dk[k + 3]
        return struct.pack(">4I", o0 & 0xFFFFFFFF, o1 & 0xFFFFFFFF,
                           o2 & 0xFFFFFFFF, o3 & 0xFFFFFFFF)


def _xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


# ======================================================================
# AES-CCM (RFC 3610) — VMK/FVEK çözme, MAC doğrulamalı
# ======================================================================
def ccm_decrypt(key: bytes, nonce: bytes, ciphertext: bytes,
                tag: bytes) -> bytes:
    """AES-CCM çözer ve MAC'i doğrular. BitLocker: nonce 12, tag 16, AAD yok.

    Yanlış anahtar MAC uyuşmazlığı verir → ``BitLockerError``."""
    aes = AES(key)
    tag_len = len(tag)
    L = 15 - len(nonce)                      # nonce 12 → L = 3
    # Sayaç blokları (CTR)
    a0 = bytes([L - 1]) + nonce + b"\x00" * L
    s0 = aes.encrypt_block(a0)
    keystream = bytearray()
    counter = 1
    while len(keystream) < len(ciphertext):
        ai = bytes([L - 1]) + nonce + counter.to_bytes(L, "big")
        keystream += aes.encrypt_block(ai)
        counter += 1
    plaintext = _xor(ciphertext, bytes(keystream[:len(ciphertext)]))
    # CBC-MAC doğrulama
    flags = ((tag_len - 2) // 2) << 3 | (L - 1)   # AAD yok → üst bit 0
    b0 = bytes([flags]) + nonce + len(plaintext).to_bytes(L, "big")
    x = aes.encrypt_block(b0)
    data = plaintext + b"\x00" * (-len(plaintext) % 16)
    for i in range(0, len(data), 16):
        x = aes.encrypt_block(_xor(x, data[i:i + 16]))
    expected = _xor(x[:tag_len], s0[:tag_len])
    if expected != tag:
        raise BitLockerError(tr("Parola ya da kurtarma anahtarı yanlış "
                                "(doğrulama başarısız)."))
    return plaintext


# ======================================================================
# AES-XTS (plain64 tweak) ve AES-CBC+EBOIV — birim şifre çözme
# ======================================================================
def _gf_alpha(t: bytes) -> bytes:
    """GF(2^128) içinde x ile çarpma (küçük-endian, azaltma 0x87)."""
    out = bytearray(16)
    carry = 0
    for i in range(16):
        b = t[i]
        out[i] = ((b << 1) | carry) & 0xFF
        carry = b >> 7
    if carry:
        out[0] ^= 0x87
    return bytes(out)


class _XtsCipher:
    def __init__(self, fvek: bytes):
        half = len(fvek) // 2
        self.data = AES(fvek[:half])
        self.tweak = AES(fvek[half:])

    def decrypt_unit(self, unit_no: int, data: bytes) -> bytes:
        tweak = self.tweak.encrypt_block(unit_no.to_bytes(16, "little"))
        out = bytearray(len(data))
        for i in range(0, len(data), 16):
            pp = _xor(data[i:i + 16], tweak)
            cc = self.data.decrypt_block(pp)
            out[i:i + 16] = _xor(cc, tweak)
            tweak = _gf_alpha(tweak)
        return bytes(out)


class _CbcEboivCipher:
    def __init__(self, fvek: bytes, sector_size: int):
        self.data = AES(fvek)
        self.sector_size = sector_size

    def decrypt_unit(self, unit_no: int, data: bytes) -> bytes:
        # EBOIV: IV = E(key, LE128(bayt ofseti))
        iv = self.data.encrypt_block(
            (unit_no * self.sector_size).to_bytes(16, "little"))
        out = bytearray(len(data))
        prev = iv
        for i in range(0, len(data), 16):
            c = data[i:i + 16]
            out[i:i + 16] = _xor(self.data.decrypt_block(c), prev)
            prev = c
        return bytes(out)


# ======================================================================
# Anahtar türetme (parola germe, kurtarma anahtarı)
# ======================================================================
_STRETCH_ITERATIONS = 0x100000


def _stretch(initial_hash: bytes, salt: bytes) -> bytes:
    """BitLocker anahtar germe zinciri → 32 baytlık VMK çözme anahtarı."""
    last = bytes(32)
    head = initial_hash + salt
    for count in range(_STRETCH_ITERATIONS):
        last = hashlib.sha256(last + head + count.to_bytes(8, "little")).digest()
    return last


def password_key(password: str, salt: bytes) -> bytes:
    h = hashlib.sha256(hashlib.sha256(password.encode("utf-16-le")).digest()).digest()
    return _stretch(h, salt)


def parse_recovery_password(text: str) -> bytes:
    """48 haneli kurtarma parolasını 16 baytlık ara anahtara çevirir."""
    digits = text.replace("-", "").replace(" ", "")
    if len(digits) != 48 or not digits.isdigit():
        raise BitLockerError(tr("Kurtarma anahtarı 48 rakamdan oluşmalıdır "
                                "(6'şarlı 8 grup)."))
    groups = [int(digits[i:i + 6]) for i in range(0, 48, 6)]
    out = bytearray()
    for g in groups:
        if g % 11 != 0 or g // 11 > 0xFFFF:
            raise BitLockerError(tr("Kurtarma anahtarı geçersiz "
                                    "(grup 11'e bölünmüyor)."))
        out += (g // 11).to_bytes(2, "little")
    return bytes(out)


def recovery_key(text: str, salt: bytes) -> bytes:
    inter = parse_recovery_password(text)
    return _stretch(hashlib.sha256(inter).digest(), salt)


# ======================================================================
# FVE metaveri çözümleme
# ======================================================================
FVE_SIGNATURE = b"-FVE-FS-"
FVE_METADATA_SIZE = 0x10000
STATE_NORMAL = 0x0004

# Entry (datum) türleri
_T_VMK = 0x0002
_T_FVEK = 0x0003
_T_VOLUME_HEADER = 0x000F
_T_DESCRIPTION = 0x0007
# Entry değer türleri
_V_STRETCH_KEY = 0x0003
_V_ENCRYPTED_KEY = 0x0005
_V_OFFSET_SIZE = 0x000F

# VMK koruma kodları (bitlk.h: protection << 8)
_PROT_NAMES = {
    0x0000: mark("Temiz anahtar"),
    0x0100: mark("TPM"),
    0x0200: mark("Açılış anahtarı (USB)"),
    0x0500: mark("TPM + PIN"),
    0x0800: mark("Kurtarma parolası"),
    0x1000: mark("Akıllı kart"),
    0x2000: mark("Parola"),
}
# Kilit açmak için kullanıcıdan bir sır alınabilenler
_UNLOCKABLE = {0x0800, 0x2000}

# Şifreleme algoritması kodları (FVE başlığı ofset 100)
CIPHERS = {
    0x8000: ("AES-CBC 128 + Elephant", "cbc-elephant", 16),
    0x8001: ("AES-CBC 256 + Elephant", "cbc-elephant", 32),
    0x8002: ("AES-CBC 128", "cbc-eboiv", 16),
    0x8003: ("AES-CBC 256", "cbc-eboiv", 32),
    0x8004: ("AES-XTS 128", "xts", 32),
    0x8005: ("AES-XTS 256", "xts", 64),
}


@dataclass
class _Entry:
    type: int
    value: int
    payload: bytes            # 8 baytlık başlıktan sonrası


def _parse_entries(data: bytes) -> List[_Entry]:
    """TLV listesi: her girdi 2:boy 2:tür 2:değer 2:sürüm + gövde."""
    out: List[_Entry] = []
    off = 0
    while off + 8 <= len(data):
        size, etype, evalue = struct.unpack_from("<HHH", data, off)
        if size < 8 or off + size > len(data):
            break
        out.append(_Entry(etype, evalue, data[off + 8:off + size]))
        off += size
    return out


@dataclass
class Protector:
    """Bir koruyucu (protector) — gizli veri taşımaz, yalnızca tanım."""
    kind: int                 # koruma kodu (0x2000 = parola, ...)
    name: str                 # mark() ile işaretli görünen ad
    guid: str = ""
    unlockable: bool = False   # kullanıcı sırrıyla açılabilir mi
    _salt: bytes = b""
    _enc_nonce: bytes = b""
    _enc_mac: bytes = b""
    _enc_payload: bytes = b""


@dataclass
class BitLockerInfo:
    """``inspect()`` çıktısı — koruyucular ve birim düzeni (sır yok)."""
    cipher_code: int = 0
    cipher_name: str = ""
    cipher_mode: str = ""
    fvek_len: int = 0
    sector_size: int = 512
    volume_size: int = 0
    volume_header_offset: int = 0
    volume_header_size: int = 0
    metadata_offsets: List[int] = field(default_factory=list)
    protectors: List[Protector] = field(default_factory=list)
    _fvek_entry: Optional[_Entry] = None

    @property
    def unlockable(self) -> bool:
        return any(p.unlockable for p in self.protectors)

    @property
    def supported_cipher(self) -> bool:
        return self.cipher_mode in ("xts", "cbc-eboiv")


def _read(dev: BlockDevice, offset: int, length: int) -> bytes:
    data = dev.read(offset, length)
    if len(data) < length:
        raise BitLockerError(tr("Birim beklenenden kısa."))
    return data


def _find_metadata(dev: BlockDevice):
    """Önyükleme sektöründen metaveri konumlarını bulup ilk geçerli
    FVE bloğunu çözer. (metadata_offsets, fve_block_bytes, header_offset)."""
    boot = _read(dev, 0, 512)
    if boot[3:11] != FVE_SIGNATURE and boot[3:11] != b"MSWIN4.1":
        raise BitLockerError(tr("Bu birimde BitLocker imzası yok."))
    sector_size = struct.unpack_from("<H", boot, 11)[0] or 512
    sb_off = 0xA0 if boot[3:11] == FVE_SIGNATURE else 0x1A8
    meta_off = list(struct.unpack_from("<3Q", boot, sb_off + 16))
    for base in meta_off:
        try:
            block = _read(dev, base, FVE_METADATA_SIZE)
        except BitLockerError:
            continue
        if block[:8] == FVE_SIGNATURE:
            return meta_off, block, sector_size
    raise BitLockerError(tr("BitLocker metaverisi okunamadı."))


def inspect(dev: BlockDevice) -> BitLockerInfo:
    """Birimi çözümler, koruyucuları listeler. Sır üretmez, parola istemez."""
    meta_off, block, boot_sector_size = _find_metadata(dev)
    # FVE bilgi bloğu başlığı
    volume_size = struct.unpack_from("<Q", block, 16)[0]
    header_size = struct.unpack_from("<I", block, 28)[0]
    header_offset = struct.unpack_from("<Q", block, 56)[0]
    # Metaveri başlığı (ofset 64)
    cipher_code = struct.unpack_from("<H", block, 100)[0]
    name, mode, fvek_len = CIPHERS.get(cipher_code, ("?", "", 0))
    meta_size = struct.unpack_from("<I", block, 64)[0]
    entries = _parse_entries(block[112:64 + meta_size])

    info = BitLockerInfo(
        cipher_code=cipher_code, cipher_name=name, cipher_mode=mode,
        fvek_len=fvek_len, sector_size=boot_sector_size,
        volume_size=volume_size, volume_header_offset=header_offset,
        volume_header_size=header_size, metadata_offsets=meta_off)

    for e in entries:
        if e.type == _T_VMK:
            info.protectors.append(_parse_vmk(e))
        elif e.type == _T_FVEK:
            info._fvek_entry = e
    return info


def _parse_vmk(entry: _Entry) -> Protector:
    guid = entry.payload[:16].hex()
    protection = struct.unpack_from("<H", entry.payload, 26)[0]
    kind = protection & 0xFF00
    p = Protector(kind=kind, name=_PROT_NAMES.get(kind, mark("Bilinmeyen")),
                  guid=guid, unlockable=kind in _UNLOCKABLE)
    # VMK alt girdileri (ofset 28'den)
    for sub in _parse_entries(entry.payload[28:]):
        if sub.value == _V_STRETCH_KEY:
            p._salt = sub.payload[4:20]
            for inner in _parse_entries(sub.payload[20:]):
                if inner.value == _V_ENCRYPTED_KEY:
                    _fill_enc(p, inner.payload)
        elif sub.value == _V_ENCRYPTED_KEY:
            _fill_enc(p, sub.payload)
    return p


def _fill_enc(p: Protector, payload: bytes):
    p._enc_nonce = payload[:12]
    p._enc_mac = payload[12:28]
    p._enc_payload = payload[28:]


def _unwrap_key(vmk_key: bytes, nonce: bytes, mac: bytes,
                payload: bytes) -> bytes:
    """CCM ile çöz, iç anahtar datumundan ham anahtarı al (gövde 12. bayttan)."""
    blob = ccm_decrypt(vmk_key, nonce, payload, mac)
    return blob[12:]


# ======================================================================
# Şifre çözen blok aygıtı
# ======================================================================
class BitLockerVolume(BlockDevice):
    """Kilidi açılmış BitLocker biriminin düz-metin görünümü (salt okunur)."""

    def __init__(self, source: BlockDevice, info: BitLockerInfo, fvek: bytes):
        self._src = source
        self.info = info
        self.sector_size = info.sector_size or 512
        self._size = info.volume_size or source.size
        if info.cipher_mode == "xts":
            self._cipher = _XtsCipher(fvek[:info.fvek_len])
        elif info.cipher_mode == "cbc-eboiv":
            self._cipher = _CbcEboivCipher(fvek[:info.fvek_len],
                                           self.sector_size)
        else:
            raise BitLockerError(tr("{} bu sürümde desteklenmiyor.",
                                    info.cipher_name))
        # ZERO (şifrelenmemiş / sıfır dönen) bölgeler: orijinal başlık konumu
        # + 3 FVE metaveri bloğu. [başlangıç, bitiş) bayt aralıkları.
        self._zero = []
        if info.volume_header_offset:
            self._zero.append((info.volume_header_offset,
                               info.volume_header_offset + info.volume_header_size))
        for off in info.metadata_offsets:
            self._zero.append((off, off + FVE_METADATA_SIZE))

    @property
    def sector_count(self) -> int:
        return self._size // self.sector_size

    @property
    def size(self) -> int:
        return self._size

    def _in_zero(self, pos: int) -> bool:
        return any(a <= pos < b for a, b in self._zero)

    def _decrypt_unit(self, unit_no: int, src_pos: int) -> bytes:
        """Tek birimi (sektör) kaynaktan okuyup çözer (ZERO denetimi hariç)."""
        enc = self._src.read(src_pos, self.sector_size)
        if len(enc) < self.sector_size:
            enc += b"\x00" * (self.sector_size - len(enc))
        return self._cipher.decrypt_unit(unit_no, enc)

    def read(self, offset: int, length: int) -> bytes:
        if offset < 0 or length < 0:
            raise BitLockerError(tr("Geçersiz okuma."))
        end = min(offset + length, self._size)
        if end <= offset:
            return b""
        ss = self.sector_size
        hoff = self.info.volume_header_offset
        hsize = self.info.volume_header_size
        out = bytearray()
        pos = (offset // ss) * ss
        while pos < end:
            if hoff and pos < hsize:
                # Yeri değiştirilmiş birim başlığı: fiziksel konum hoff'ta,
                # tweak orijinal fiziksel sektöre göre. (ZERO denetimi mantıksal
                # konuma uygulanır; bu kaynak her zaman çözülür.)
                plain = self._decrypt_unit((hoff + pos) // ss, hoff + pos)
            elif self._in_zero(pos):
                plain = b"\x00" * ss
            else:
                plain = self._decrypt_unit(pos // ss, pos)
            out += plain
            pos += ss
        start = offset % ss
        return bytes(out[start:start + (end - offset)])


# ======================================================================
# Kilit açma — tek giriş noktası
# ======================================================================
def unlock(dev: BlockDevice, password: Optional[str] = None,
           recovery: Optional[str] = None,
           info: Optional[BitLockerInfo] = None) -> BitLockerVolume:
    """Parola ya da kurtarma anahtarıyla birimi açar.

    Doğru sır CCM MAC doğrulamasından geçer; yanlışsa ``BitLockerError``.
    """
    info = info or inspect(dev)
    if not info.supported_cipher:
        raise BitLockerError(tr("{} bu sürümde desteklenmiyor "
                                "(yalnızca AES-XTS ve AES-CBC).",
                                info.cipher_name))
    if info._fvek_entry is None:
        raise BitLockerError(tr("FVEK girdisi bulunamadı."))

    last_err: Optional[BitLockerError] = None
    for p in info.protectors:
        if not p._enc_payload:
            continue
        if p.kind == 0x2000 and password:
            key = password_key(password, p._salt)
        elif p.kind == 0x0800 and recovery:
            key = recovery_key(recovery, p._salt)
        else:
            continue
        try:
            vmk = _unwrap_key(key, p._enc_nonce, p._enc_mac, p._enc_payload)
        except BitLockerError as exc:
            last_err = exc
            continue
        fe = info._fvek_entry
        fvek = _unwrap_key(vmk, fe.payload[:12], fe.payload[12:28],
                           fe.payload[28:])
        return BitLockerVolume(dev, info, fvek)

    if last_err is not None:
        raise last_err
    if password and not any(p.kind == 0x2000 for p in info.protectors):
        raise BitLockerError(tr("Bu birimde parola koruyucusu yok; "
                                "kurtarma anahtarı gerekir."))
    raise BitLockerError(tr("Parola ya da kurtarma anahtarı gerekli."))
