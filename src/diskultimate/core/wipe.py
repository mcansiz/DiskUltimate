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
    WipeMethod("zero", "Sifirla (1 gecis)", ["zero"],
               "Tum alani 0x00 ile doldurur. Hizli; gunluk kullanim icin yeterli."),
    WipeMethod("random", "Rastgele (1 gecis)", ["random"],
               "Tum alani rastgele veriyle doldurur. Eski icerigi desen analizine karsi gizler."),
    WipeMethod("dod", "DoD 5220.22-M (3 gecis)", ["zero", "one", "random"],
               "0x00, 0xFF ve rastgele veri. Kurumsal imha olcutlerinde yaygin."),
    WipeMethod("dod7", "DoD 5220.22-M ECE (7 gecis)",
               ["zero", "one", "random", "random", "zero", "one", "random"],
               "Yedi gecisli genisletilmis surum. Cok yavas."),
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
        raise WipeError(f"Bilinmeyen silme yontemi: {method}")
    if getattr(device, "readonly", False):
        raise WipeError("Aygit salt okunur")
    toplam = device.size
    gecis_sayisi = yontem.pass_count
    for gecis, tur in enumerate(yontem.passes, 1):
        yazilan = 0
        sabit_blok = _pattern_block(tur, CHUNK) if tur != "random" else None
        while yazilan < toplam:
            uzunluk = min(CHUNK, toplam - yazilan)
            blok = (sabit_blok[:uzunluk] if sabit_blok is not None
                    else os.urandom(uzunluk))
            device.write(yazilan, blok)
            yazilan += uzunluk
            if progress:
                taban = 100 * (gecis - 1) / gecis_sayisi
                pay = (100 / gecis_sayisi) * (yazilan / toplam)
                progress(f"Gecis {gecis}/{gecis_sayisi} ({tur}) — "
                         f"{human_size(yazilan)} / {human_size(toplam)}",
                         int(taban + pay))
    f = getattr(device, "flush", None)
    if f:
        f()
    sonuc = {"method": yontem.label, "passes": gecis_sayisi, "bytes": toplam,
             "verified": False}
    if verify and yontem.passes[-1] == "zero":
        sonuc["verified"] = _verify_zero(device, progress)
    if progress:
        progress("Tamamlandi", 100)
    return sonuc


def _verify_zero(device: BlockDevice, progress: Progress = None) -> bool:
    okunan = 0
    toplam = device.size
    while okunan < toplam:
        uzunluk = min(CHUNK, toplam - okunan)
        if device.read(okunan, uzunluk).strip(b"\x00"):
            return False
        okunan += uzunluk
        if progress:
            progress(f"Dogrulaniyor... {human_size(okunan)}", int(99 * okunan / toplam))
    return True


def wipe_free_space(fs_access, progress: Progress = None) -> dict:
    """Dosya sisteminin **bos** alanini doldurup siler.

    Mevcut dosyalara dokunmaz; silinmis dosyalarin artik verisini yok eder.
    Gecici bir dolgu dosyasi yazip sonra siler.
    """
    if not getattr(fs_access, "writable", False):
        raise WipeError("Bu bolum yazilabilir degil")
    istatistik = fs_access.stats()
    bos = istatistik.get("free_bytes", 0)
    if bos <= 0:
        return {"bytes": 0, "files": 0}
    # Dosya sistemini tamamen doldurmamak icin guvenlik payi birakilir
    kume = max(4096, istatistik.get("cluster_size", 4096))
    pay = max(8 * kume, bos // 100)
    hedef = max(0, bos - pay)
    ad_taban = "/_dolgu_%d.tmp"
    parca = 32 * 1024 * 1024
    yazilan = 0
    dosyalar: List[str] = []
    i = 0
    try:
        while yazilan < hedef:
            uzunluk = min(parca, hedef - yazilan)
            if uzunluk <= 0:
                break
            ad = ad_taban % i
            try:
                fs_access.write_file(ad, b"\x00" * uzunluk)
            except Exception:
                break
            dosyalar.append(ad)
            yazilan += uzunluk
            i += 1
            if progress:
                progress(f"Bos alan dolduruluyor... {human_size(yazilan)} / "
                         f"{human_size(hedef)}", int(90 * yazilan / max(1, hedef)))
    finally:
        for ad in dosyalar:
            try:
                fs_access.remove(ad)
            except Exception:
                pass
    if progress:
        progress("Tamamlandi", 100)
    return {"bytes": yazilan, "files": len(dosyalar)}


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
        progress("Bolum tablosu silindi", 100)
