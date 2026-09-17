"""Guvenli silme (veri imha).

Uyari: bu islemler geri alinamaz. Cagrilmadan once arayuzde onay alinir.
Tum islemler yalnizca verilen aygit penceresi (goruntu veya bolum) uzerinde
calisir; disina tasmaz.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, List, Optional

from .image import BlockDevice
from .ptable import human_size
from ..i18n import mark, tr

Progress = Optional[Callable[[str, int], None]]
CHUNK = 4 * 1024 * 1024


class WipeError(Exception):
    pass


@dataclass
class WipeMethod:
    key: str
    label: str
    passes: List[str]        # 'zero' | 'one' | 'random'
    description: str

    @property
    def pass_count(self) -> int:
        return len(self.passes)


WIPE_METHODS = [
    WipeMethod("zero", mark("Sifirla (1 gecis)"), ["zero"],
               mark("Tum alani 0x00 ile doldurur. Hizli; gunluk kullanim icin "
                    "yeterli.")),
    WipeMethod("random", mark("Rastgele (1 gecis)"), ["random"],
               mark("Tum alani rastgele veriyle doldurur. Eski icerigi desen "
                    "analizine karsi gizler.")),
    WipeMethod("dod", mark("DoD 5220.22-M (3 gecis)"), ["zero", "one", "random"],
               mark("0x00, 0xFF ve rastgele veri. Kurumsal imha olcutlerinde "
                    "yaygin.")),
    WipeMethod("dod7", mark("DoD 5220.22-M ECE (7 gecis)"),
               ["zero", "one", "random", "random", "zero", "one", "random"],
               mark("Yedi gecisli genisletilmis surum. Cok yavas.")),
]
WIPE_BY_KEY = {m.key: m for m in WIPE_METHODS}


def _pattern_block(kind: str, size: int) -> bytes:
    if kind == "zero":
        return b"\x00" * size
    if kind == "one":
        return b"\xFF" * size
    return os.urandom(size)


def wipe_device(device: BlockDevice, method: str = "zero",
                progress: Progress = None, verify: bool = False) -> dict:
    """Aygitin tamamini secilen yontemle siler."""
    yontem = WIPE_BY_KEY.get(method)
    if yontem is None:
        raise WipeError(tr("Bilinmeyen silme yontemi: {}", method))
    if getattr(device, "readonly", False):
        raise WipeError(tr("Aygit salt okunur"))
    total = device.size
    pass_count = yontem.pass_count
    for gecis, kind in enumerate(yontem.passes, 1):
        yazilan = 0
        fixed_block = _pattern_block(kind, CHUNK) if kind != "random" else None
        while yazilan < total:
            length = min(CHUNK, total - yazilan)
            block = (fixed_block[:length] if fixed_block is not None
                    else os.urandom(length))
            device.write(yazilan, block)
            yazilan += length
            if progress:
                taban = 100 * (gecis - 1) / pass_count
                pay = (100 / pass_count) * (yazilan / total)
                progress(tr("Gecis {}/{} ({}) — {} / {}",
                            gecis, pass_count, kind, human_size(yazilan),
                            human_size(total)),
                         int(taban + pay))
    f = getattr(device, "flush", None)
    if f:
        f()
    result = {"method": yontem.label, "passes": pass_count, "bytes": total,
             "verified": False}
    if verify and yontem.passes[-1] == "zero":
        result["verified"] = _verify_zero(device, progress)
    if progress:
        progress(tr("Tamamlandi"), 100)
    return result


def _verify_zero(device: BlockDevice, progress: Progress = None) -> bool:
    okunan = 0
    total = device.size
    while okunan < total:
        length = min(CHUNK, total - okunan)
        if device.read(okunan, length).strip(b"\x00"):
            return False
        okunan += length
        if progress:
            progress(tr("Dogrulaniyor... {}", human_size(okunan)), int(99 * okunan / total))
    return True


def wipe_free_space(fs_access, progress: Progress = None) -> dict:
    """Dosya sisteminin **bos** alanini doldurup siler.

    Mevcut dosyalara dokunmaz; silinmis dosyalarin artik verisini yok eder.
    Gecici bir dolgu dosyasi yazip sonra siler.
    """
    if not getattr(fs_access, "writable", False):
        raise WipeError(tr("Bu bolum yazilabilir degil"))
    istatistik = fs_access.stats()
    free = istatistik.get("free_bytes", 0)
    if free <= 0:
        return {"bytes": 0, "files": 0}
    # Dosya sistemini tamamen doldurmamak icin guvenlik payi birakilir
    cluster = max(4096, istatistik.get("cluster_size", 4096))
    pay = max(8 * cluster, free // 100)
    target = max(0, free - pay)
    name_base = "/_dolgu_%d.tmp"
    chunk = 32 * 1024 * 1024
    yazilan = 0
    files: List[str] = []
    i = 0
    try:
        while yazilan < target:
            length = min(chunk, target - yazilan)
            if length <= 0:
                break
            name = name_base % i
            try:
                fs_access.write_file(name, b"\x00" * length)
            except Exception:
                break
            files.append(name)
            yazilan += length
            i += 1
            if progress:
                progress(tr("Bos alan dolduruluyor... {} / {}",
                            human_size(yazilan), human_size(target)), int(90 * yazilan / max(1, target)))
    finally:
        for name in files:
            try:
                fs_access.remove(name)
            except Exception:
                pass
    if progress:
        progress(tr("Tamamlandi"), 100)
    return {"bytes": yazilan, "files": len(files)}


def wipe_partition_table(device: BlockDevice, progress: Progress = None) -> None:
    """Yalnizca bolum tablosu yapilarini siler (bolum verileri kalir)."""
    bas = min(2048, device.sector_count)
    device.zero_sectors(0, bas)
    kuyruk = device.sector_count - 34
    if kuyruk > bas:
        device.zero_sectors(kuyruk, 34)
    f = getattr(device, "flush", None)
    if f:
        f()
    if progress:
        progress(tr("Bolum tablosu silindi"), 100)
