"""Fiziksel disk sondasi — YALNIZCA OKUMA.

Sistemdeki diskleri listeler, salt okunur acar ve bolum tablosunu cozumler.
**Hicbir diske yazmaz.** Yonetici/root yetkisi olmadan da calisir; o durumda
neyin okunamadigini raporlar.

    python3 -m tests.physical_probe          # Linux/macOS (root degilse sinirli)
    sudo python3 -m tests.physical_probe     # tam bilgi
    python  -m tests.physical_probe          # Windows (Yonetici PowerShell'de)
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from diskultimate.core.physical import (AccessDeniedError,  # noqa: E402
                                        PhysicalDisk, PhysicalDiskError,
                                        can_access, list_disks)
from diskultimate.core.platform import PLATFORM_NAME  # noqa: E402
from diskultimate.core.ptable import human_size  # noqa: E402
from diskultimate.core.session import DiskSession  # noqa: E402


def main() -> int:
    yetki = can_access()
    print(f"Platform: {PLATFORM_NAME} | yonetici/root yetkisi: "
          f"{'VAR' if yetki else 'YOK'}")
    if not yetki:
        print("  (yetki olmadan disk icerigi okunamaz; liste sinirli olabilir)")
    print()

    diskler = list_disks()
    print(f"Bulunan disk: {len(diskler)}")
    for d in diskler:
        isaret = {"sistem": "[!]", "bagli": "[*]", "bilinmiyor": "[?]",
                  "normal": "   "}[d.risk_level]
        print(f"\n{isaret} {d.name}")
        for anahtar, deger in d.summary().items():
            print(f"      {anahtar:<16}: {deger}")

    if not diskler:
        print("\nDisk bulunamadi.")
        return 0

    print("\n" + "=" * 60)
    print("SALT OKUNUR acma ve bolum tablosu cozumleme")
    print("=" * 60)
    basarili = 0
    for d in diskler:
        print(f"\n  {d.name} ({d.path})")
        try:
            with PhysicalDisk(d, readonly=True) as disk:
                print(f"    acildi: {human_size(disk.size)}, "
                      f"{disk.sector_count} x {disk.sector_size} B")
                ilk = disk.read_sectors(0, 1)
                imza = ilk[510:512].hex()
                print(f"    sektor 0 imzasi : {imza} "
                      f"({'0x55AA — gecerli' if imza == '55aa' else 'imza yok'})")
                oturum = DiskSession(disk)
                print(f"    bolum tablosu   : {oturum.scheme_name}")
                for p in oturum.partitions:
                    print(f"      {p.index}. {p.fs_type or 'ham':<8} "
                          f"{human_size(p.size):>10}  LBA {p.start_lba:<12} "
                          f"{p.type_name}")
                if not oturum.partitions:
                    print("      (bolum yok)")
                oturum.close_filesystems()
                basarili += 1
        except AccessDeniedError as exc:
            print(f"    YETKI YOK: {exc}")
        except PhysicalDiskError as exc:
            print(f"    HATA: {exc}")
        except Exception as exc:            # beklenmedik durumlari da gorelim
            print(f"    BEKLENMEDIK: {type(exc).__name__}: {exc}")

    print(f"\nOzet: {basarili}/{len(diskler)} disk okunabildi. "
          "Hicbir diske yazilmadi.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
