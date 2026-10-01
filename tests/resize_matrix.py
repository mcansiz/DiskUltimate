"""Boyutlandirma matrisi — var olan her dosya sisteminde tasima/buyutme/kucultme.

Kullanici karari (2026-09-29): *"3 platformda var olan disk tiplerinde
bicimlendirme, boyutlandirma gibi tum islemleri yapabilmeliyiz."* Bu denetim
her dosya sistemi x bolum tablosu icin ayni adim dizisini `DiskSession`
uzerinden (arayuzun Uygula'da kullandigi yol) kosar:

    1. kucult          (baslangic sabit)
    2. buyut
    3. saga tasi       (boyut sabit)
    4. sola tasi + kucult
    5. sola tasi + buyut (ilk baslangica)

Her adimdan sonra:
  * butun dosyalarin SHA-1'i ilk yazilanla ayni (kendi okuyucumuz),
  * birim yazilabilir: yeni bir dosya yazilip geri okunur,
  * varsa harici denetim temiz (`fsck.vfat -n`, `fsck.exfat -n`,
    `ntfsfix -n`, `e2fsck -fn`) — Windows'ta bu araclar yoktur; o yuzden
    `--keep` son goruntuleri saklar, Windows'ta VHD olarak takilip
    Windows'un kendi surucusuyle, Linux'ta araclarla ayrica denetlenir.

Yalnizca goruntu dosyasi kullanir. Calistirma:
    python3 -m tests.resize_matrix           # tam matris
    python3 -m tests.resize_matrix --quick   # yalnizca MBR
    python3 -m tests.resize_matrix --keep    # son goruntuleri sakla
"""
from __future__ import annotations

import hashlib
import os
import random
import shutil
import subprocess
import sys
import time
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from diskultimate.core.session import DiskSession  # noqa: E402
from diskultimate.paths import scratch  # noqa: E402

MIB = 1024 * 1024
TMP = scratch("resize_matrix")
QUICK = "--quick" in sys.argv
KEEP = "--keep" in sys.argv

# anahtar: (bolum MiB, kucuk, buyuk, orta, saga kayma, buyuk dosya MiB)
FILESYSTEMS = {
    "fat12": (16, 8, 30, 12, 20, 2),
    "fat16": (160, 64, 400, 120, 200, 12),
    "fat32": (160, 64, 400, 120, 200, 12),
    "exfat": (160, 64, 400, 120, 200, 12),
    "ntfs": (160, 64, 400, 120, 200, 12),
    "ext2": (160, 64, 400, 120, 200, 12),
    "ext3": (160, 64, 400, 120, 200, 12),
    "ext4": (160, 64, 400, 120, 200, 12),
}
CHECKERS = {
    "fat": ("fsck.vfat", ["-n"]),
    "exfat": ("fsck.exfat", ["-n"]),
    "ntfs": ("ntfsfix", ["-n"]),
    "ext": ("e2fsck", ["-fn"]),
}


def _tool(name: str) -> str:
    return shutil.which(name) or shutil.which(name, path="/sbin:/usr/sbin") or ""


def _family(key: str) -> str:
    return "fat" if key.startswith("fat") else "ext" if key.startswith("ext") else key


def _content(big_mib: int, seed: int) -> dict:
    rnd = random.Random(seed)
    files = {"/buyuk.bin": rnd.randbytes(big_mib * MIB),
             "/a/b/c/derin.txt": b"derin dizin\n" * 50,
             "/turkce_çğüşöı.txt": "Türkçe içerik — ğüşiöç\n".encode("utf-8") * 20}
    for i in range(120):
        files[f"/kucuk/dosya_{i:03d}.dat"] = rnd.randbytes(rnd.randrange(0, 9000))
    return files


def _digest(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _write(fs, files: dict) -> None:
    made = set()
    for path in sorted(files):
        parts = path.strip("/").split("/")[:-1]
        cur = ""
        for p in parts:
            cur += "/" + p
            if cur not in made:
                try:
                    fs.mkdir(cur)
                except Exception:
                    pass                          # zaten var
                made.add(cur)
        fs.write_file(path, files[path])
    fs.flush()


def _verify(session, expected: dict, label: str) -> None:
    session.close_filesystems()
    fs = session.filesystem(1)
    assert fs is not None, f"{label}: dosya sistemi acilamadi"
    for path, digest in expected.items():
        got = _digest(fs.read(path))
        assert got == digest, f"{label}: {path} ozeti farkli"
    session.close_filesystems()


def _external_check(img: str, start_lba: int, count: int, family: str) -> str:
    """Bolumu ayri dosyaya cikarip harici aracla denetler; '' = atlandi."""
    name, flags = CHECKERS[family]
    tool = _tool(name)
    if not tool:
        return ""
    part = img + ".part"
    with open(img, "rb") as src, open(part, "wb") as dst:
        src.seek(start_lba * 512)
        left = count * 512
        while left > 0:
            block = src.read(min(8 * MIB, left))
            if not block:
                break
            dst.write(block)
            left -= len(block)
    try:
        r = subprocess.run([tool] + flags + [part], capture_output=True, text=True)
        if r.returncode != 0:
            raise AssertionError(f"{name}: {r.stdout[-800:]}{r.stderr[-400:]}")
    finally:
        os.unlink(part)
    return name


def run_case(key: str, scheme: str) -> str:
    base, small, big, mid, shift, big_file = FILESYSTEMS[key]
    img = os.path.join(TMP, f"{key}_{scheme}.img")
    disk_mib = max(64, base + shift + big + 16) if key != "fat12" else 96
    s = DiskSession.create(img, disk_mib * MIB, overwrite=True)
    s.create_table(scheme)
    start = 2048
    s.create_partition(start, base * MIB // 512, fs_key=key,
                       label=f"M{key[:5]}{scheme[0]}".upper())
    files = _content(big_file, zlib.crc32(f"{key}/{scheme}".encode()))
    fs = s.filesystem(1)
    _write(fs, files)
    s.close_filesystems()
    expected = {p: _digest(d) for p, d in files.items()}
    _verify(s, expected, f"{key}/{scheme} ilk")

    steps = [
        ("kucult", start, small),
        ("buyut", start, big),
        ("saga tasi", start + shift * MIB // 512, big),
        ("sola tasi+kucult", start + (shift // 2) * MIB // 512, mid),
        ("sola tasi+buyut", start, big),
    ]
    checked = set()
    info = s.resize_info(1)
    assert info.kind in ("fat", "exfat", "ntfs", "ext"), (key, info.kind)
    for n, (label, new_start, size_mib) in enumerate(steps, 1):
        tag = f"{key}/{scheme} {n}.{label}"
        count = size_mib * MIB // 512
        t0 = time.monotonic()
        s.resize_partition(1, new_start, count, confirm=True)
        part = s.table.get(1)
        assert part.start_lba == new_start and part.sector_count == count, \
            (tag, part.start_lba, part.sector_count)
        _verify(s, expected, tag)
        # yazilabilirlik: her adimdan sonra yeni dosya
        extra = f"/adim_{n}.txt"
        data = f"{tag}\n".encode("utf-8") * 100
        fs = s.filesystem(1)
        fs.write_file(extra, data)
        fs.flush()
        s.close_filesystems()
        expected[extra] = _digest(data)
        _verify(s, expected, tag + " (yazma sonrasi)")
        s.image.flush() if hasattr(s.image, "flush") else None
        name = _external_check(img, part.start_lba, part.sector_count,
                               _family(key))
        if name:
            checked.add(name)
        print(f"    {tag}: {time.monotonic() - t0:.1f} sn", flush=True)
    s.close()
    if KEEP:
        # Windows/Linux capraz denetimi icin: yol -> SHA-1
        with open(img + ".sha1.txt", "w", encoding="utf-8", newline="\n") as fh:
            for path in sorted(expected):
                fh.write(f"{expected[path]} {path}\n")
    else:
        os.unlink(img)
    return ", ".join(sorted(checked)) or "harici arac yok"


def main() -> int:
    os.makedirs(TMP, exist_ok=True)
    schemes = ["mbr"] if QUICK else ["mbr", "gpt"]
    only = [a for a in sys.argv[1:] if not a.startswith("--")]
    keys = [k for k in FILESYSTEMS if not only or k in only]
    ok, failed = 0, []
    for key in keys:
        for scheme in schemes:
            print(f"  {key}/{scheme} ...", flush=True)
            try:
                how = run_case(key, scheme)
                print(f"  {key}/{scheme}: TAMAM ({how})", flush=True)
                ok += 1
            except Exception as exc:              # noqa: BLE001
                import traceback
                traceback.print_exc()
                print(f"  {key}/{scheme}: BASARISIZ — {exc}", flush=True)
                failed.append(f"{key}/{scheme}")
    total = ok + len(failed)
    print(f"\nSonuc: {ok}/{total} basarili")
    for f in failed:
        print(f"  - {f}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
