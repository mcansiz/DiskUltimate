"""Gercek aygit yolu (Asama 5, ADR 0080): test icin gecici bir blok aygiti.

YALNIZCA GitHub Actions makinesinde calisir (`GITHUB_ACTIONS=true`): ana
makinede fiziksel diske yazilmaz (CLAUDE.md test kurali). Uretilen aygit
kucuk ve yenidir; uygulamanin fiziksel disk kapilarindan gecer
(`DiskSession.open_physical(..., readonly=False, confirm=True)`).

* Linux: `scsi_debug` cekirdek modulu RAM'de gercek bir SCSI disk acar
  (/dev/sdX); sd surucusu, BLKRRPART, bolum aygitlari gercektir.
* Windows: sabit VHD `Mount-DiskImage` ile YAZILABILIR takilir ->
  \\\\.\\PhysicalDriveN; Windows birimleri kendisi baglar, uygulamanin birim
  kilitleme yolu (ADR 0075) gercekten sinanir.
* macOS: `hdiutil attach -nomount` -> /dev/diskN.
"""
from __future__ import annotations

import glob
import os
import plistlib
import subprocess
import sys
import time
from typing import Callable, Tuple

from .data import MIB

MAX_DEVICE = 4 * 1024 * MIB


class DeviceError(Exception):
    pass


def require_ci() -> None:
    """GitHub Actions makinesi ya da acikca beyan edilmis test VM'i
    (`DU_TEST_VM=1`; CLAUDE.md: fiziksel disk testleri VM'de)."""
    if os.environ.get("GITHUB_ACTIONS") == "true" or os.environ.get("DU_TEST_VM") == "1":
        return
    raise DeviceError("aygit kipi yalnizca GitHub Actions makinesinde ya da test "
                      "VM'inde (DU_TEST_VM=1) calisir — ana makinede fiziksel diske "
                      "yazilmaz (CLAUDE.md)")


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace", **kw)


def create(size_mb: int, workdir: str) -> Tuple[str, Callable[[], None]]:
    """(aygit yolu, kaldirma islevi)."""
    require_ci()
    if size_mb * MIB > MAX_DEVICE:
        raise DeviceError("test aygiti en fazla 4 GiB")
    if sys.platform.startswith("linux"):
        before = set(glob.glob("/sys/block/sd*"))
        r = _run(["modprobe", "scsi_debug", f"dev_size_mb={size_mb}",
                  "num_tgts=1", "max_luns=1", "sector_size=512"])
        if r.returncode != 0:
            raise DeviceError(f"scsi_debug yuklenemedi: {r.stderr.strip()}")
        for _ in range(40):
            new = [p for p in glob.glob("/sys/block/sd*") if p not in before]
            if new:
                break
            time.sleep(0.25)
        else:
            raise DeviceError("scsi_debug aygiti gorunmedi")
        path = "/dev/" + os.path.basename(new[0])
        _run(["udevadm", "settle"])
        return path, lambda: _run(["modprobe", "-r", "scsi_debug"])
    if sys.platform.startswith("win"):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))), "src"))
        from diskultimate.core.vdisk import build_vhd_footer
        vhd = os.path.join(workdir, "aygit.vhd")
        size = size_mb * MIB
        with open(vhd, "wb") as fh:
            fh.truncate(size)
            fh.seek(size)
            fh.write(build_vhd_footer(size))
        _run(["fsutil", "sparse", "setflag", vhd, "0"])
        r = _run(["powershell", "-NoProfile", "-Command",
                  f"(Mount-DiskImage -ImagePath '{vhd}' -PassThru | Get-Disk).Number"])
        if r.returncode != 0 or not r.stdout.strip().isdigit():
            raise DeviceError(f"VHD takilamadi: {r.stdout}{r.stderr}")
        path = f"\\\\.\\PhysicalDrive{r.stdout.strip()}"

        def cleanup():
            _run(["powershell", "-NoProfile", "-Command",
                  f"Dismount-DiskImage -ImagePath '{vhd}' | Out-Null"])
            try:
                os.unlink(vhd)
            except OSError:
                pass
        return path, cleanup
    if sys.platform == "darwin":
        raw = os.path.join(workdir, "aygit.img")
        with open(raw, "wb") as fh:
            fh.truncate(size_mb * MIB)
        r = subprocess.run(["hdiutil", "attach", "-nomount", "-plist", "-imagekey",
                            "diskimage-class=CRawDiskImage", raw], capture_output=True)
        if r.returncode != 0:
            raise DeviceError(f"hdiutil: {r.stderr.decode(errors='replace')}")
        disk = min((e["dev-entry"] for e in plistlib.loads(r.stdout)["system-entities"]),
                   key=len)

        def cleanup():
            _run(["hdiutil", "detach", disk, "-force"])
            try:
                os.unlink(raw)
            except OSError:
                pass
        return disk, cleanup
    raise DeviceError(f"desteklenmeyen platform: {sys.platform}")


def check_safe(info) -> None:
    """Uygulamanin kendi kapilarina ek olarak: yalnizca bu test aygiti."""
    require_ci()
    problems = []
    if info.is_system:
        problems.append("sistem diski")
    if info.size > MAX_DEVICE:
        problems.append(f"boyut {info.size // MIB} MiB > 4 GiB")
    if not info.info_complete:
        problems.append("bilgi eksik (yetki?)")
    if info.mounted:
        problems.append(f"bagli bolum: {info.mounted}")
    if problems:
        raise DeviceError("aygit test aygiti olarak kabul edilmedi: " + ", ".join(problems))
