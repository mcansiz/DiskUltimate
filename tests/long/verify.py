"""Dogrulama katmanlari: kendi okuyucumuz, dis fsck, cekirdek ile baglama.

Durustluk kurali (tests/fs_matrix.py ile ayni): arac ya da yetki yoksa sonuc
"atlandi" + neden yazilir, asla "tamam" sayilmaz.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from typing import List, Optional, Tuple

from .data import MIB, Manifest

COPY_LIMIT = 2 * 1024 * MIB       # sudo yoksa dis denetim icin kopyalanacak en buyuk bolum

# fs anahtari -> (arac, argumanlar)
FSCK = {
    "fat12": ("fsck.vfat", ["-n"]), "fat16": ("fsck.vfat", ["-n"]),
    "fat32": ("fsck.vfat", ["-n"]), "exfat": ("fsck.exfat", ["-n"]),
    "ntfs": ("ntfsfix", ["-n"]), "ext2": ("e2fsck", ["-fn"]),
    "ext3": ("e2fsck", ["-fn"]), "ext4": ("e2fsck", ["-fn"]),
    "hfsplus": ("fsck.hfsplus", ["-f", "-n"]), "xfs": ("xfs_repair", ["-n", "-f"]),
}
FSCK_MAC = {
    "fat12": ("fsck_msdos", ["-n"]), "fat16": ("fsck_msdos", ["-n"]),
    "fat32": ("fsck_msdos", ["-n"]), "exfat": ("fsck_exfat", ["-n"]),
    "hfsplus": ("fsck_hfs", ["-fn"]),
}
# fs anahtari -> cekirdek surucusu
KERNEL_TYPE = {
    "fat12": "vfat", "fat16": "vfat", "fat32": "vfat", "exfat": "exfat",
    "ntfs": "ntfs3", "ext2": "ext4", "ext3": "ext4", "ext4": "ext4",
    "hfsplus": "hfsplus", "udf": "udf", "xfs": "xfs",
}


def _which(name: str) -> Optional[str]:
    return shutil.which(name) or shutil.which(name, path="/usr/sbin:/sbin:/usr/local/sbin")


def sudo_ok() -> bool:
    if not sys.platform.startswith("linux") or not shutil.which("sudo"):
        return False
    return subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0


def verify_manifest(access, manifest: Manifest) -> Tuple[int, int, List[str]]:
    """Butun dosyalari akisla okuyup ozetler: (dosya, bayt, hatalar)."""
    errors: List[str] = []
    count = total = 0
    for path, (size, sha1) in sorted(manifest.entries.items()):
        try:
            h = hashlib.sha1()
            n = 0
            for piece in access.iter_read(path):
                h.update(piece)
                n += len(piece)
        except Exception as exc:                      # noqa: BLE001
            errors.append(f"{path}: okunamadi: {exc}")
            continue
        if n != size:
            errors.append(f"{path}: boyut {n} != {size}")
        elif h.hexdigest() != sha1:
            errors.append(f"{path}: icerik farkli")
        count += 1
        total += n
        if len(errors) > 20:
            errors.append("... (ilk 20 hata)")
            break
    return count, total, errors


def _partition_device(image_path: str, offset: int, size: int):
    """Bolumu dis aracin acabilecegi bir yol olarak verir (baglam yoneticisi).

    sudo varsa salt okunur loop aygiti; yoksa (kucukse) gecici dosyaya kopya.
    """
    class _Ctx:
        def __enter__(self_inner):
            self_inner.loop = self_inner.tmp = None
            if sudo_ok():
                r = subprocess.run(["sudo", "-n", "losetup", "-f", "--show", "-r",
                                    "-o", str(offset), "--sizelimit", str(size),
                                    image_path], capture_output=True, text=True)
                if r.returncode == 0:
                    self_inner.loop = r.stdout.strip()
                    return self_inner.loop, True
            if size > COPY_LIMIT:
                return None, False
            fd, self_inner.tmp = tempfile.mkstemp(prefix="du_bolum_", suffix=".img",
                                                  dir=os.path.dirname(image_path))
            with os.fdopen(fd, "wb") as out, open(image_path, "rb") as src:
                src.seek(offset)
                left = size
                while left > 0:
                    block = src.read(min(8 * MIB, left))
                    if not block:
                        break
                    out.write(block)
                    left -= len(block)
            return self_inner.tmp, False

        def __exit__(self_inner, *exc):
            if self_inner.loop:
                subprocess.run(["sudo", "-n", "losetup", "-d", self_inner.loop],
                               capture_output=True)
            if self_inner.tmp and os.path.exists(self_inner.tmp):
                os.unlink(self_inner.tmp)
    return _Ctx()


def external_fsck(fs_key: str, image_path: str, offset: int, size: int) -> Tuple[str, str]:
    """('ok'|'fail'|'skip', ayrinti)."""
    table = FSCK_MAC if sys.platform == "darwin" else FSCK
    if fs_key not in table:
        return "skip", f"{fs_key} icin dis denetim araci yok"
    tool, args = table[fs_key]
    exe = _which(tool)
    if not exe:
        return "skip", f"{tool} kurulu degil"
    with _partition_device(image_path, offset, size) as (dev, privileged):
        if dev is None:
            return "skip", f"sudo yok ve bolum {size // MIB} MiB > kopyalama siniri"
        cmd = (["sudo", "-n"] if privileged else []) + [exe] + args + [dev]
        r = subprocess.run(cmd, capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    if r.returncode == 0:
        return "ok", f"{tool}: {out.splitlines()[-1] if out else 'temiz'}"
    return "fail", f"{tool} cikis {r.returncode}: {out[-800:]}"


def kernel_mount_check(fs_key: str, image_path: str, offset: int, size: int,
                       manifest: Manifest) -> Tuple[str, str]:
    """Cekirdegin kendi surucusuyle salt okunur baglayip ozetleri dogrular.

    Yalnizca Linux + sifresiz sudo (CI makinesi; ADR 0080). Ana makinede
    kosmaz (CLAUDE.md test kurali).
    """
    if os.environ.get("DU_UZUN_CEKIRDEK") != "1":
        return "skip", "cekirdek baglama istenmedi (DU_UZUN_CEKIRDEK=1)"
    if not sudo_ok():
        return "skip", "sifresiz sudo yok"
    kind = KERNEL_TYPE.get(fs_key)
    if not kind:
        return "skip", f"{fs_key} icin cekirdek surucusu tanimli degil"
    mnt = tempfile.mkdtemp(prefix="du_mnt_")
    opts = f"ro,loop,offset={offset},sizelimit={size}"
    if kind in ("vfat", "exfat", "ntfs3"):
        opts += f",uid={os.getuid()},gid={os.getgid()}"
    r = subprocess.run(["sudo", "-n", "mount", "-t", kind, "-o", opts, image_path, mnt],
                       capture_output=True, text=True)
    if r.returncode != 0:
        os.rmdir(mnt)
        msg = (r.stdout + r.stderr).strip()
        if "unknown filesystem type" in msg:
            return "skip", f"cekirdekte {kind} surucusu yok"
        return "fail", f"mount -t {kind}: {msg[-400:]}"
    errors: List[str] = []
    try:
        for path, (size_e, sha1) in sorted(manifest.entries.items()):
            full = os.path.join(mnt, path.lstrip("/"))
            try:
                h = hashlib.sha1()
                n = 0
                with open(full, "rb") as fh:
                    for block in iter(lambda: fh.read(8 * MIB), b""):
                        h.update(block)
                        n += len(block)
            except OSError as exc:
                errors.append(f"{path}: {exc}")
                continue
            if n != size_e or h.hexdigest() != sha1:
                errors.append(f"{path}: cekirdek farkli icerik goruyor")
            if len(errors) > 20:
                break
    finally:
        subprocess.run(["sudo", "-n", "umount", mnt], capture_output=True)
        os.rmdir(mnt)
    if errors:
        return "fail", "; ".join(errors[:10])
    return "ok", f"{kind}: {len(manifest.entries)} dosya cekirdekle ayni"
