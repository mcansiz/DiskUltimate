"""Fiziksel diskte **NTFS denetle ve onar** testi — yalnizca ayrilmis test diskinde.

Onarim (ADR 0077) gercek bir aygitin uzerinden, kullanicinin yolundan
gecer: salt okunur denetim -> kuyruga `ntfs_fix` -> "Uygula" (Windows'ta
birimler kilitlenip ayrilir, ADR 0016/0075). `tests/physical_write_test.py`
ile ayni alti olcut gecerlidir; bagli birim bilerek kabul edilir (onarilacak
birim isletim sistemince baglidir).

Bu betik yalnizca diski onarir ve oncesi/sonrasini basar. Windows'un kendi
gorusu (`fsutil dirty query`, `chkdsk`) cagiran betikte alinir.

Kullanim (yonetici, VM misafirinde):
    python -m tests.physical_ntfsfix_test \\\\.\\PhysicalDrive1 --onayla
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from diskultimate.core import operations as ops  # noqa: E402
from diskultimate.core.physical import find_disk  # noqa: E402
from diskultimate.core.session import DiskSession  # noqa: E402
from tests.physical_write_test import olcutleri_dogrula  # noqa: E402

AZAMI = 2 * 1024 ** 3          # 2 GB ustu test diski sayilmaz


def ozet(session, index: int, etiket: str):
    h = session.ntfs_check(index)
    print(f"{etiket}: kirli={h.dirty} gunluk={h.logfile} hazirda={h.hibernated} "
          f"surum={h.version} onyukleme={h.primary_boot}/{h.backup_boot} "
          f"ayna_fark={h.mirror_mismatch}")
    for sorun in h.problems():
        print(f"  - {sorun}")
    return h


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args or "--onayla" not in sys.argv:
        print(__doc__)
        return 2
    bilgi = olcutleri_dogrula(args[0], azami=AZAMI, bagli_izin=True)
    if bilgi is None:
        return 1

    oku = DiskSession.open_physical(bilgi)                    # salt okunur
    ntfs = [p for p in oku.partitions if p.fs_type == "NTFS"]
    if not ntfs:
        print("HATA: diskte NTFS bolumu yok")
        oku.close()
        return 1
    part = ntfs[0]
    once = ozet(oku, part.index, "ONCE")
    oku.close()
    if not once.needs_repair:
        print("UYARI: birim zaten temiz — onarim yine uygulanir")

    yaz = DiskSession.open_physical(find_disk(bilgi.path), readonly=False,
                                    confirm=True)
    kuyruk = ops.OperationQueue()
    kuyruk.add(ops.ntfs_fix_op(part.index, at_lba=part.start_lba))
    sonuc = kuyruk.apply(yaz, progress=lambda m, p: None)
    print(f"UYGULA: ok={sonuc.ok} {sonuc.summary()}")
    yaz.close()
    if not sonuc.ok:
        return 1

    son_bilgi = find_disk(bilgi.path)
    oku = DiskSession.open_physical(son_bilgi)
    sonra = ozet(oku, part.index, "SONRA")
    oku.close()
    # Windows kilit birakilinca birimi hemen yeniden baglar; bagli NTFS'in
    # gunlugu her zaman "temiz degil"dir (olculdu, 2026-10-03). Bagliyken
    # olcut: kirli bayrak kalkmis, yapilar saglam.
    sorunlu = (sonra.dirty or sonra.mirror_mismatch
               or sonra.primary_boot != "ok" or sonra.backup_boot != "ok")
    if not son_bilgi.mounted and sonra.logfile not in ("empty", "clean"):
        sorunlu = True
    elif son_bilgi.mounted:
        print(f"NOT: birim yeniden baglandi ({', '.join(son_bilgi.mounted)}); "
              "gunluk durumu isletim sistemine ait")
    if sorunlu:
        print("BASARISIZ: onarimdan sonra sorun kaldi")
        return 1
    print("TAMAM")
    return 0


if __name__ == "__main__":
    sys.exit(main())
