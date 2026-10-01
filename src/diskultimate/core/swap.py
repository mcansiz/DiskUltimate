"""Linux takas alani (swap) olusturma — saf Python (`mkswap` karsiligi).

Takas alaninin "dosya sistemi" tek bir baslik sayfasindan ibarettir
(linux/include/linux/swap.h, `union swap_header`):

    0      bootbits (1024 bayt, dokunulmaz — onyukleyiciye ait olabilir)
    1024   version = 1
    1028   last_page = sayfa_sayisi - 1
    1032   nr_badpages = 0
    1036   sws_uuid (16)
    1052   sws_volume (16)  <- etiket
    S-10   "SWAPSPACE2"     (S = sayfa boyutu)

Sayfa boyutu cekirdegin sayfa boyutudur; x86/x86-64 ve cogu arm64'te 4096.
64K sayfali cekirdekler (bazi arm64/ppc64 dagitimlari) 4K baslikli takasi
kullanmaz; o durumda `page_size=65536` verilmelidir.
"""
from __future__ import annotations

import struct
import uuid as _uuid
from typing import Optional

from .image import BlockDevice
from ..i18n import tr

MAGIC = b"SWAPSPACE2"
MIN_PAGES = 10                   # mkswap ile ayni alt sinir


class SwapError(Exception):
    pass


def format_swap(dev: BlockDevice, label: str = "", page_size: int = 4096,
                uuid: Optional[_uuid.UUID] = None) -> str:
    """`dev` uzerine takas basligi yazar; UUID metnini dondurur."""
    if page_size not in (4096, 8192, 16384, 65536):
        raise SwapError(tr("Gecersiz sayfa boyutu: {}", page_size))
    pages = dev.size // page_size
    if pages < MIN_PAGES:
        raise SwapError(tr("Takas alani icin en az {} sayfa gerekir", MIN_PAGES))
    last_page = min(pages - 1, 0xFFFFFFFF)
    kimlik = uuid or _uuid.uuid4()
    etiket = label.encode("utf-8")[:16]

    page = bytearray(dev.read(0, page_size))
    page[1024:page_size] = bytes(page_size - 1024)      # bootbits korunur
    struct.pack_into("<III", page, 1024, 1, last_page, 0)
    page[1036:1052] = kimlik.bytes
    page[1052:1052 + len(etiket)] = etiket
    page[page_size - 10:page_size] = MAGIC
    dev.write(0, bytes(page))
    flush = getattr(dev, "flush", None)
    if flush:
        flush()
    return str(kimlik)
