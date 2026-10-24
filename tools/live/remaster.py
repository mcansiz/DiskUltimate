"""Taban ISO'ya uygulama katmanini ekler (GELISTIRME ARACI, ADR 0101).

    python3 tools/live/remaster.py <base.iso> <DiskUltimate.AppImage> <surum> <cikis.iso>

Taban (`tools/live/base/build.sh`) Debian + masaustu tasir, uygulama icermez.
Bu arac her surumde yalnizca su katmani uretir ve ISO'ya ekler:

    /live/zz-diskultimate.squashfs
        opt/diskultimate/DiskUltimate.AppImage
        opt/diskultimate/VERSION

live-boot `/live/*.squashfs` dosyalarinin hepsini sirayla ust uste baglar
(`.module` dosyasi yoksa; live-boot 9990-overlay.sh). Ad `zz-` ile basladigi
icin tabanin ustune biner. Root gerekmez, sure saniyelerle olculur:
taban yeniden kurulmaz.

Onyukleme duzeni (El Torito, hibrit MBR/GPT, EFI) `-boot_image any replay`
ile tabandan aynen kopyalanir. `sha256sum.txt` guncellenir, yoksa GRUB'daki
"Verify integrity" girdisi yeni katmani hatali sayardi.

Gerekenler: xorriso, mksquashfs (squashfs-tools).
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORK = os.path.join(ROOT, ".tmp", "live", "remaster")
LAYER_NAME = "zz-diskultimate.squashfs"


def run(cmd: list) -> subprocess.CompletedProcess:
    print("+ " + " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=True)


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_layer(appimage: str, version: str) -> str:
    tree = os.path.join(WORK, "layer")
    shutil.rmtree(tree, ignore_errors=True)
    app_dir = os.path.join(tree, "opt", "diskultimate")
    os.makedirs(app_dir)
    target = os.path.join(app_dir, "DiskUltimate.AppImage")
    shutil.copyfile(appimage, target)
    os.chmod(target, 0o755)
    with open(os.path.join(app_dir, "VERSION"), "w", encoding="ascii") as fh:
        fh.write(version + "\n")
    layer = os.path.join(WORK, LAYER_NAME)
    if os.path.exists(layer):
        os.remove(layer)
    # AppImage zaten sikistirilmis: xz kazanc saglamayan bloklari sikistirmasiz
    # saklar, bu yuzden ek boyut neredeyse yoktur.
    run(["mksquashfs", tree, layer, "-comp", "xz", "-all-root", "-noappend",
         "-quiet"])
    return layer


def updated_checksums(base_iso: str, layer: str) -> str:
    """Tabandaki sha256sum.txt + yeni katmanin satiri."""
    path = os.path.join(WORK, "sha256sum.txt")
    if os.path.exists(path):
        os.remove(path)
    lines = []
    probe = subprocess.run(["xorriso", "-osirrox", "on", "-indev", base_iso,
                            "-extract", "/sha256sum.txt", path],
                           capture_output=True, text=True)
    if probe.returncode == 0 and os.path.isfile(path):
        os.chmod(path, 0o644)
        with open(path, encoding="utf-8") as fh:
            lines = [ln for ln in fh.read().splitlines()
                     if not ln.endswith("/live/" + LAYER_NAME)]
    lines.append(f"{sha256(layer)}  ./live/{LAYER_NAME}")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def main() -> int:
    if len(sys.argv) != 5:
        print(__doc__)
        return 2
    base_iso, appimage, version, out_iso = sys.argv[1:5]
    for tool in ("xorriso", "mksquashfs"):
        if not shutil.which(tool):
            print(f"HATA: {tool} yok")
            return 1
    os.makedirs(WORK, exist_ok=True)
    layer = build_layer(appimage, version)
    sums = updated_checksums(base_iso, layer)
    if os.path.exists(out_iso):
        os.remove(out_iso)
    os.makedirs(os.path.dirname(os.path.abspath(out_iso)), exist_ok=True)
    run(["xorriso", "-indev", base_iso, "-outdev", out_iso,
         "-boot_image", "any", "replay",
         "-map", layer, "/live/" + LAYER_NAME,
         "-map", sums, "/sha256sum.txt",
         "-chmod", "0444", "/live/" + LAYER_NAME, "/sha256sum.txt", "--"])
    print(f"katman: {os.path.getsize(layer) / 1048576:.1f} MB, "
          f"ISO: {os.path.getsize(out_iso) / 1048576:.1f} MB -> {out_iso}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
