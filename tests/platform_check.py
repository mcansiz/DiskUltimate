"""Capraz platform uyumluluk denetimi (statik).

Windows ve macOS uzerinde dogrudan test calistirilamadigi icin, kodda platforma
bagli kalan noktalari tarar. `python3 -m tests.platform_check`
"""
from __future__ import annotations

import ast
import os
import re
import sys

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KAYNAK = os.path.join(KOK, "src")

# Platform katmani: isletim sistemi farkliliklarini BARINDIRMASI beklenen dosyalar.
# Muafiyet yalnizca ilgili kurallar icin verilir; tempfile / subprocess / endian
# kurallari bu dosyalarda da gecerlidir.
PLATFORM_KATMANI = {"platform.py", "physical.py"}

# (desen, aciklama, izinli dosyalar)
KURALLAR = [
    (r"/tmp/|/var/tmp", "Sabit POSIX gecici dizin yolu", set()),
    (r"/usr/|/etc/|/dev/sd|/dev/nvme",
     "Sabit POSIX sistem yolu", PLATFORM_KATMANI),
    (r"\bos\.fork\b|\bos\.getuid\b|\bos\.geteuid\b|\bpwd\b|\bgrp\b",
     "POSIX'e ozgu islev", PLATFORM_KATMANI),
    (r"st_blocks", "Windows'ta bulunmayan stat alani", {"platform.py"}),
    (r"subprocess\.(run|Popen|call)\(", "Dogrudan surec cagrisi "
     "(platform.run_tool kullanilmali)", {"platform.py"}),
    (r"shutil\.which\(", "Dogrudan arac arama (platform.find_tool kullanilmali)",
     {"platform.py"}),
    (r"\btempfile\b", "Sistem gecici dizini (paths.scratch kullanilmali)", set()),
    (r"[\"']([A-Za-z]:\\\\|\\\\\\\\)", "Sabit Windows yolu", PLATFORM_KATMANI),
    (r"\.decode\(\)|\.encode\(\)",
     "Kodlamasiz encode/decode (platforma gore degisir)", set()),
    (r"open\([^)]*[\"']r[\"']\s*\)", "Metin modunda kodlamasiz acma", set()),
]

# Mutlaka acikca belirtilmesi gereken: struct bicimlerinde endian isareti
STRUCT_DESENI = re.compile(r"struct\.(pack|unpack)(_into|_from)?\(\s*[\"']([^\"'])")


def kaynak_dosyalar():
    for kok, _dizinler, dosyalar in os.walk(KAYNAK):
        if "__pycache__" in kok:
            continue
        for ad in sorted(dosyalar):
            if ad.endswith(".py"):
                yield os.path.join(kok, ad)
    for ad in ("main.py",):
        yield os.path.join(KOK, ad)


def main() -> int:
    bulgular = []
    struct_bulgulari = []
    for yol in kaynak_dosyalar():
        ad = os.path.basename(yol)
        with open(yol, encoding="utf-8") as fh:
            satirlar = fh.readlines()
        for no, satir in enumerate(satirlar, 1):
            kod = satir.split("#")[0]
            if not kod.strip():
                continue
            for desen, aciklama, izinli in KURALLAR:
                if ad in izinli:
                    continue
                if re.search(desen, kod):
                    bulgular.append((os.path.relpath(yol, KOK), no, aciklama,
                                     kod.strip()[:80]))
            for eslesme in STRUCT_DESENI.finditer(kod):
                if eslesme.group(3) not in "<>!=@":
                    struct_bulgulari.append(
                        (os.path.relpath(yol, KOK), no, kod.strip()[:80]))

    # AST ile: PyQt ceviriyi core'a sizdirmis mi?
    sizinti = []
    for yol in kaynak_dosyalar():
        if os.sep + "core" + os.sep not in yol:
            continue
        with open(yol, encoding="utf-8") as fh:
            agac = ast.parse(fh.read(), filename=yol)
        for dugum in ast.walk(agac):
            if isinstance(dugum, (ast.Import, ast.ImportFrom)):
                adlar = ([a.name for a in dugum.names]
                         if isinstance(dugum, ast.Import) else [dugum.module or ""])
                for a in adlar:
                    if a.startswith("PyQt"):
                        sizinti.append((os.path.relpath(yol, KOK), dugum.lineno))

    print("=== Capraz platform denetimi ===\n")
    print("Platform katmani (isletim sistemi farki barindirmasi beklenen): "
          + ", ".join(sorted(PLATFORM_KATMANI)) + "\n")
    if bulgular:
        print(f"Platforma bagli {len(bulgular)} nokta:")
        for yol, no, aciklama, kod in bulgular:
            print(f"  {yol}:{no}  {aciklama}\n      {kod}")
    else:
        print("Platforma bagli kalip bulunamadi.")

    print()
    if struct_bulgulari:
        print(f"Endian isareti olmayan {len(struct_bulgulari)} struct cagrisi "
              "(makine siralamasina baglidir):")
        for yol, no, kod in struct_bulgulari:
            print(f"  {yol}:{no}\n      {kod}")
    else:
        print("Tum struct cagrilari endian isareti tasiyor.")

    print()
    if sizinti:
        print(f"KATMAN IHLALI: core/ icinde PyQt import edilmis ({len(sizinti)}):")
        for yol, no in sizinti:
            print(f"  {yol}:{no}")
    else:
        print("Katman ayrimi korunuyor: core/ icinde PyQt yok.")

    hata = len(bulgular) + len(struct_bulgulari) + len(sizinti)
    print(f"\nToplam bulgu: {hata}")
    return 1 if hata else 0


if __name__ == "__main__":
    sys.exit(main())
