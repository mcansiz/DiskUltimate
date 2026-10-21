"""BitLocker kilit açma denetimi (ADR 0099).

Kripto temelleri kamuya açık vektörlerle (FIPS-197 AES) ve round-trip ile
doğrulanır. Tam zincir (metaveri → parola germe → CCM → FVEK → XTS → okuma)
sentetik ama yapısal olarak gerçek bir BitLocker birimi kurularak sınanır.

Uçtan uca GERÇEK imaj doğrulaması bu testin kapsamı DEĞİLDİR: o CI'da
``cryptsetup bitlkOpen`` ile, Windows misafirinde ``manage-bde`` ile üretilen
örnek üzerinde yapılır (ADR 0099 test planı). Burada "test edildi" denen şey:
kripto doğru ve çözümleyici kendi ürettiği gerçekçi yapıyı açıyor.

Çalıştırma:  python3 -m tests.bitlocker_check
"""
from __future__ import annotations

import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from diskultimate.core import bitlocker as bl          # noqa: E402
from diskultimate.core.bitlocker import (AES, BitLockerError,  # noqa: E402
                                         ccm_decrypt, password_key,
                                         parse_recovery_password)
from diskultimate.core.image import BlockDevice         # noqa: E402

_fail = 0


def check(name: str, ok: bool):
    global _fail
    print(("  TAMAM  " if ok else "  HATA   ") + name)
    if not ok:
        _fail += 1


# ---- test yardımcıları (modül içindekilerin aynadaki şifreleme tarafı) ----
def _xor(a, b):
    return bytes(x ^ y for x, y in zip(a, b))


def xts_encrypt_unit(fvek, unit_no, data):
    half = len(fvek) // 2
    dkey, tkey = AES(fvek[:half]), AES(fvek[half:])
    tweak = tkey.encrypt_block(unit_no.to_bytes(16, "little"))
    out = bytearray(len(data))
    for i in range(0, len(data), 16):
        cc = dkey.encrypt_block(_xor(data[i:i + 16], tweak))
        out[i:i + 16] = _xor(cc, tweak)
        tweak = bl._gf_alpha(tweak)
    return bytes(out)


def ccm_encrypt(key, nonce, pt, tag_len=16):
    a = AES(key)
    L = 15 - len(nonce)
    s0 = a.encrypt_block(bytes([L - 1]) + nonce + b"\x00" * L)
    flags = ((tag_len - 2) // 2) << 3 | (L - 1)
    x = a.encrypt_block(bytes([flags]) + nonce + len(pt).to_bytes(L, "big"))
    data = pt + b"\x00" * (-len(pt) % 16)
    for i in range(0, len(data), 16):
        x = a.encrypt_block(_xor(x, data[i:i + 16]))
    tag = _xor(x[:tag_len], s0[:tag_len])
    ks = bytearray()
    c = 1
    while len(ks) < len(pt):
        ks += a.encrypt_block(bytes([L - 1]) + nonce + c.to_bytes(L, "big"))
        c += 1
    return _xor(pt, bytes(ks[:len(pt)])), tag


class _MemDev(BlockDevice):
    def __init__(self, data: bytearray):
        self._d = data

    @property
    def sector_count(self):
        return len(self._d) // self.sector_size

    @property
    def size(self):
        return len(self._d)

    def read(self, offset, length):
        return bytes(self._d[offset:offset + length])


def _entry(etype, evalue, payload):
    return struct.pack("<HHHH", 8 + len(payload), etype, evalue, 1) + payload


def _enc_key_entry(wrap_key, raw_key):
    """CCM ile sarılmış anahtar datumu: iç gövde 12 baytlık başlık + anahtar."""
    blob = struct.pack("<HHHH", 12 + len(raw_key), 0, 1, 1) + b"\x00\x00\x00\x00" + raw_key
    nonce = os.urandom(12)
    ct, tag = ccm_encrypt(wrap_key, nonce, blob)
    return nonce + tag + ct


def build_volume(password="Parola123!", cipher=0x8004, fvek_len=32,
                 header_offset=0x2000, sector_size=512):
    """Yapısal olarak gerçek, parola korumalı küçük bir BitLocker birimi kurar.

    Döner: (dev, fvek, vmk, salt, plaintext_sector, plaintext_lba)
    """
    size = 2 * 1024 * 1024
    dev = bytearray(size)
    meta_offsets = [0x10000, 0x20000, 0x30000]

    fvek = os.urandom(fvek_len)
    vmk = os.urandom(32)
    salt = os.urandom(16)

    wrap = password_key(password, salt)
    vmk_enc = _enc_key_entry(wrap, vmk)
    fvek_enc = _enc_key_entry(vmk, fvek)

    # VMK girdisi: guid(16)+modified(8)+unknown(2)+protection(2=0x2000) + alt
    stretch_payload = b"\x00\x00\x00\x00" + salt + _entry(0, 0x0005, vmk_enc)
    vmk_head = os.urandom(16) + b"\x00" * 8 + b"\x00\x00" + struct.pack("<H", 0x2000)
    vmk_entry = _entry(0x0002, 0x0008,
                       vmk_head + _entry(0, 0x0003, stretch_payload))
    fvek_entry = _entry(0x0003, 0x0005, fvek_enc)
    entries = vmk_entry + fvek_entry

    # FVE metaveri bloğu
    block = bytearray(bl.FVE_METADATA_SIZE)
    block[0:8] = bl.FVE_SIGNATURE
    struct.pack_into("<Q", block, 16, size)                 # volume size
    struct.pack_into("<I", block, 28, sector_size)          # header size
    struct.pack_into("<Q", block, 56, header_offset)        # header offset
    meta_size = 48 + len(entries)
    struct.pack_into("<I", block, 64, meta_size)
    struct.pack_into("<H", block, 100, cipher)
    block[112:112 + len(entries)] = entries
    for mo in meta_offsets:
        dev[mo:mo + bl.FVE_METADATA_SIZE] = block

    # Önyükleme sektörü
    dev[3:11] = bl.FVE_SIGNATURE
    struct.pack_into("<H", dev, 11, sector_size)
    struct.pack_into("<3Q", dev, 0xA0 + 16, *meta_offsets)

    # Düz-metin bir sektör seç, XTS ile şifreleyip diske koy.
    plain = bytes(range(256)) * (sector_size // 256)
    lba = 0x40000 // sector_size            # metaveri/başlık bölgelerinin dışı
    enc = xts_encrypt_unit(fvek[:fvek_len], lba, plain)
    dev[lba * sector_size:lba * sector_size + sector_size] = enc

    # Yeri değiştirilmiş birim başlığı: mantıksal 0, fiziksel header_offset,
    # tweak orijinal fiziksel sektöre göre.
    hdr_plain = b"HDR!" + bytes(sector_size - 4)
    henc = xts_encrypt_unit(fvek[:fvek_len], header_offset // sector_size, hdr_plain)
    dev[header_offset:header_offset + sector_size] = henc

    return _MemDev(dev), fvek, vmk, salt, plain, lba, hdr_plain


def main():
    print("=== BitLocker kilit acma denetimi ===\n")

    print("Kripto temelleri:")
    a = AES(bytes.fromhex("000102030405060708090a0b0c0d0e0f"))
    check("AES-128 FIPS-197 sifrele",
          a.encrypt_block(bytes.fromhex("00112233445566778899aabbccddeeff")).hex()
          == "69c4e0d86a7b0430d8cdb78070b4c55a")
    check("AES-128 FIPS-197 coz",
          a.decrypt_block(bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a")).hex()
          == "00112233445566778899aabbccddeeff")
    a2 = AES(bytes.fromhex(
        "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"))
    check("AES-256 FIPS-197 sifrele",
          a2.encrypt_block(bytes.fromhex("00112233445566778899aabbccddeeff")).hex()
          == "8ea2b7ca516745bfeafc49904b496089")

    fvek = os.urandom(32)
    pt = os.urandom(512)
    ct = xts_encrypt_unit(fvek, 99, pt)
    xc = bl._XtsCipher(fvek)
    check("AES-XTS round-trip", xc.decrypt_unit(99, ct) == pt)
    check("AES-XTS tweak duyarli", xc.decrypt_unit(100, ct) != pt)

    key, nonce, msg = os.urandom(32), os.urandom(12), os.urandom(44)
    cct, tag = ccm_encrypt(key, nonce, msg)
    check("AES-CCM round-trip", ccm_decrypt(key, nonce, cct, tag) == msg)
    try:
        ccm_decrypt(key, nonce, cct, bytes(16))
        check("AES-CCM yanlis MAC reddi", False)
    except BitLockerError:
        check("AES-CCM yanlis MAC reddi", True)

    raw = os.urandom(16)
    vals = struct.unpack("<8H", raw)
    digits = "-".join("%06d" % (v * 11) for v in vals)
    check("Kurtarma anahtari cozumu", parse_recovery_password(digits) == raw)
    try:
        parse_recovery_password("123")
        check("Kurtarma anahtari gecersiz reddi", False)
    except BitLockerError:
        check("Kurtarma anahtari gecersiz reddi", True)

    print("\nTam zincir (sentetik birim):")
    dev, fvek, vmk, salt, plain, lba, hdr_plain = build_volume()
    info = bl.inspect(dev)
    check("inspect: AES-XTS 128", info.cipher_name == "AES-XTS 128")
    check("inspect: parola koruyucusu listelendi",
          any(p.kind == 0x2000 for p in info.protectors))
    check("inspect: acilabilir", info.unlockable)

    vol = bl.unlock(dev, password="Parola123!")
    check("unlock: dogru parola birimi acti", isinstance(vol, bl.BitLockerVolume))
    check("unlock: FVEK dogru", vol._cipher.data is not None)
    got = vol.read(lba * 512, 512)
    check("okuma: sifreli sektor cozuldu", got == plain)
    # metaveri bolgesi ZERO
    check("okuma: metaveri bolgesi sifir", vol.read(0x10000, 512) == bytes(512))
    # yeri degistirilmis baslik (mantiksal 0)
    check("okuma: birim basligi yeri degisti",
          vol.read(0, 512) == hdr_plain)

    try:
        bl.unlock(dev, password="yanlis")
        check("unlock: yanlis parola reddi", False)
    except BitLockerError:
        check("unlock: yanlis parola reddi", True)

    print()
    if _fail:
        print("SONUC: %d HATA" % _fail)
        return 1
    print("SONUC: TAMAM")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
