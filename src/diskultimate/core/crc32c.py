"""CRC-32C (Castagnoli) — saf Python.

ext4'un `metadata_csum` ozelligi her metaveri yapisini bu saglama ile korur.
Python'un standart kutuphanesinde CRC-32C yoktur (`zlib.crc32` farkli bir
polinom kullanir), bu yuzden tablo tabanli bir uygulama gerekir.

Polinom: 0x1EDC6F41, yansitilmis (reflected) bicimde 0x82F63B78.
Dogrulama olcutu: `crc32c(b"123456789") == 0xE3069283` (RFC 3720 / SCTP).
"""
from __future__ import annotations

POLY = 0x82F63B78          # yansitilmis 0x1EDC6F41


def _build_table() -> list:
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = (crc >> 1) ^ (POLY if crc & 1 else 0)
        table.append(crc)
    return table


TABLE = _build_table()


def crc32c(data: bytes, crc: int = 0) -> int:
    """CRC-32C hesaplar.

    `crc` onceki sonucu surdurmek icindir. ext4 tum saglamalarinda **ters
    cevrilmis** baslangic kullanir (`~0`), bu yuzden cagiranlar genellikle
    `crc32c(veri, 0xFFFFFFFF)` degil `ext4_csum()` yardimcisini kullanir.
    """
    crc ^= 0xFFFFFFFF
    for b in data:
        crc = TABLE[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF


def ext4_csum(seed: int, data: bytes) -> int:
    """ext4 sozlesmesi: tohum zaten "acilmis" bir CRC degeridir.

    ext4 icinde saglamalar zincirlenir — ornegin grup tanimlayicisinin tohumu
    "UUID uzerinden hesaplanmis CRC"dir ve uzerine grup numarasi, sonra
    tanimlayicinin kendisi eklenir. Zincirleme dogru olsun diye ara degerler
    ters cevrilmeden tasinir.
    """
    crc = seed ^ 0xFFFFFFFF
    for b in data:
        crc = TABLE[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF


def self_test() -> None:
    """Bilinen olcutlerle dogrular; hata varsa istisna firlatir."""
    vectors = [
        (b"", 0x00000000),
        (b"123456789", 0xE3069283),
        (b"a", 0xC1D04330),
        (b"\x00" * 32, 0x8A9136AA),
        (b"\xff" * 32, 0x62A8AB43),
    ]
    for data, expected in vectors:
        result = crc32c(data)
        if result != expected:
            raise AssertionError(
                f"CRC32C hatasi: {data[:12]!r} -> {result:08X}, "
                f"{expected:08X} bekleniyordu")
    # zincirleme: parcali hesap butunle ayni olmali
    whole = crc32c(b"DiskUltimate ext4 saglama denemesi")
    chunked = crc32c(b"ext4 saglama denemesi", crc32c(b"DiskUltimate "))
    if whole != chunked:
        raise AssertionError("CRC32C zincirlemesi tutarsiz")


if __name__ == "__main__":
    self_test()
    print("CRC32C oz denetimi gecti")
    print(f'  crc32c(b"123456789") = 0x{crc32c(b"123456789"):08X}')
