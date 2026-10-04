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
    if sys.platform == "darwin" and os.environ.get("DU_UZUN_CEKIRDEK") == "1":
        return "skip", "macOS'ta fsck aygit uzerinden (surucu denetimi icinde)"
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


def _hash_tree(root: str, manifest: Manifest, sep: str = "/") -> List[str]:
    """Baglanmis birimdeki dosyalari isletim sisteminin surucusuyle ozetler."""
    errors: List[str] = []
    for path, (size_e, sha1) in sorted(manifest.entries.items()):
        full = root.rstrip("/\\") + sep + path.lstrip("/").replace("/", sep)
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
            errors.append(f"{path}: surucu farkli icerik goruyor ({n} bayt)")
        if len(errors) > 20:
            break
    return errors


def kernel_mount_check(fs_key: str, image_path: str, offset: int, size: int,
                       manifest: Manifest) -> Tuple[str, str]:
    """Isletim sisteminin KENDI surucusuyle salt okunur baglayip ozetleri ve
    (Windows'ta) chkdsk'i dogrular.

    Yalnizca CI makinesinde (ADR 0080; CLAUDE.md test kurali): Linux'ta
    sifresiz sudo + `mount`, Windows'ta yonetici + `Mount-DiskImage`.
    """
    if os.environ.get("DU_UZUN_CEKIRDEK") != "1":
        return "skip", "cekirdek baglama istenmedi (DU_UZUN_CEKIRDEK=1)"
    if sys.platform.startswith("win"):
        return windows_mount_check(fs_key, image_path, offset, size, manifest)
    if sys.platform == "darwin":
        return mac_mount_check(fs_key, image_path, offset, size, manifest)
    if not sudo_ok():
        return "skip", "sifresiz sudo yok"
    kind = KERNEL_TYPE.get(fs_key)
    if not kind:
        return "skip", f"{fs_key} icin cekirdek surucusu tanimli degil"
    mnt = tempfile.mkdtemp(prefix="du_mnt_")
    opts = f"ro,loop,offset={offset},sizelimit={size}"
    if kind in ("vfat", "exfat", "ntfs3"):
        opts += f",uid={os.getuid()},gid={os.getgid()}"
    if kind == "vfat":
        # vfat varsayilan iocharset'i (iso8859-1) s/i/Yunanca/Kiril adlari
        # temsil edemez; dosya "yok" gorunur (CI'da olculdu). Uzun adlar UTF-16.
        opts += ",utf8"
    r = subprocess.run(["sudo", "-n", "mount", "-t", kind, "-o", opts, image_path, mnt],
                       capture_output=True, text=True)
    if r.returncode != 0:
        os.rmdir(mnt)
        msg = (r.stdout + r.stderr).strip()
        if "unknown filesystem type" in msg:
            return "skip", f"cekirdekte {kind} surucusu yok"
        return "fail", f"mount -t {kind}: {msg[-400:]}"
    try:
        errors = _hash_tree(mnt, manifest)
    finally:
        subprocess.run(["sudo", "-n", "umount", mnt], capture_output=True)
        os.rmdir(mnt)
    if errors:
        return "fail", "; ".join(errors[:10])
    return "ok", f"{kind}: {len(manifest.entries)} dosya cekirdekle ayni"


# --------------------------------------------------------------------------
# Windows: ham goruntu + VHD alt bilgisi = sabit VHD -> Mount-DiskImage
# --------------------------------------------------------------------------
WINDOWS_FS = {"fat12", "fat16", "fat32", "exfat", "ntfs", "udf"}


def _powershell(script: str, timeout: int = 600) -> Tuple[int, str]:
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                        "-ExecutionPolicy", "Bypass", "-Command", script],
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def windows_mount_check(fs_key: str, image_path: str, offset: int, size: int,
                        manifest: Manifest) -> Tuple[str, str]:
    """Windows'un kendi surucusu: salt okunur bagla, chkdsk, dosya ozetleri.

    Cagiran goruntuyu KAPATMIS olmali (Windows acik dosyayi yeniden
    adlandirmaz). Ham goruntunun sonuna 512 baytlik VHD alt bilgisi eklenir
    (sabit VHD = ham veri + alt bilgi), is bitince kaldirilir.
    """
    if fs_key not in WINDOWS_FS:
        return "skip", f"Windows {fs_key} okumaz"
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "src"))
    from diskultimate.core.vdisk import build_vhd_footer
    disk_size = os.path.getsize(image_path)
    vhd = os.path.splitext(image_path)[0] + "_kontrol.vhd"
    # Windows seyrek dosyayi VHD olarak takmaz (0xC03A001A "virtual disk
    # system limitation"; CI'da olculdu). Bayrak kaldirilir; kalkmazsa
    # seyrek olmayan bir kopya takilir.
    subprocess.run(["fsutil", "sparse", "setflag", image_path, "0"],
                   capture_output=True)
    q = subprocess.run(["fsutil", "sparse", "queryflag", image_path],
                       capture_output=True, text=True)
    copy = "NOT" not in q.stdout.upper() and "DEGIL" not in q.stdout.upper()
    if copy:
        with open(image_path, "rb") as src, open(vhd, "wb") as dst:
            for block in iter(lambda: src.read(8 * MIB), b""):
                dst.write(block)
            dst.write(build_vhd_footer(disk_size))
    else:
        with open(image_path, "ab") as fh:
            fh.write(build_vhd_footer(disk_size))
        os.replace(image_path, vhd)
    try:
        code, out = _powershell(f"""
$ErrorActionPreference = 'Stop'
$img = Mount-DiskImage -ImagePath '{vhd}' -Access ReadOnly -PassThru
Start-Sleep 3
$disk = $img | Get-Disk
$p = Get-Partition -DiskNumber $disk.Number | Where-Object {{ $_.Offset -eq {offset} }}
if (-not $p) {{ throw "bolum yok (ofset {offset}): " + ((Get-Partition -DiskNumber $disk.Number | ForEach-Object {{ $_.Offset }}) -join ',') }}
$v = $p | Get-Volume
$yol = if ($p.DriveLetter -and $p.DriveLetter -ne [char]0) {{ "$($p.DriveLetter):" }} else {{ $v.Path.TrimEnd('\') }}
"YOL=$yol"
"FS=$($v.FileSystem) SAGLIK=$($v.HealthStatus)"
""")
        if code != 0 or "YOL=" not in out:
            return "fail", f"Windows baglamadi: {out[-500:]}"
        root = [l for l in out.splitlines() if l.startswith("YOL=")][0][4:].strip()
        info = [l for l in out.splitlines() if l.startswith("FS=")]
        errors = _hash_tree(root, manifest, sep="\\")
        r = subprocess.run(["chkdsk", root], capture_output=True, text=True,
                           timeout=1800)
        chk = (r.stdout + r.stderr).strip()
        if errors:
            return "fail", f"Windows surucusu: {'; '.join(errors[:8])}"
        if r.returncode != 0:
            return "fail", f"chkdsk cikis {r.returncode}: {chk[-600:]}"
        return "ok", (f"Windows: {info[0] if info else ''}, "
                      f"{len(manifest.entries)} dosya ayni, chkdsk temiz")
    finally:
        _powershell(f"Dismount-DiskImage -ImagePath '{vhd}' | Out-Null")
        if copy:
            os.unlink(vhd)
        else:
            os.replace(vhd, image_path)
            with open(image_path, "r+b") as fh:
                fh.truncate(disk_size)


# --------------------------------------------------------------------------
# macOS: hdiutil ile aygit, diskutil ile salt okunur baglama, fsck_*
# --------------------------------------------------------------------------
MAC_FS = {"fat12": "msdos", "fat16": "msdos", "fat32": "msdos", "exfat": "exfat",
          "hfsplus": "hfs", "udf": "udf", "ntfs": "ntfs"}


def mac_mount_check(fs_key: str, image_path: str, offset: int, size: int,
                    manifest: Manifest) -> Tuple[str, str]:
    """macOS'un kendi surucusu (Asama 4; ADR 0080)."""
    if fs_key not in MAC_FS:
        return "skip", f"macOS {fs_key} baglamaz"
    import plistlib
    r = subprocess.run(["hdiutil", "attach", "-nomount", "-readonly", "-plist",
                        "-imagekey", "diskimage-class=CRawDiskImage", image_path],
                       capture_output=True)
    if r.returncode != 0:
        return "fail", f"hdiutil attach: {r.stderr.decode(errors='replace')[-400:]}"
    entities = plistlib.loads(r.stdout).get("system-entities", [])
    disk = min((e["dev-entry"] for e in entities), key=len)
    try:
        target = None
        for e in entities:
            dev = e["dev-entry"]
            if dev == disk:
                continue
            info = subprocess.run(["diskutil", "info", "-plist", dev], capture_output=True)
            data = plistlib.loads(info.stdout) if info.returncode == 0 else {}
            if data.get("PartitionMapPartitionOffset") == offset:
                target = dev
                break
        if target is None:
            return "fail", f"macOS bolumu bulamadi (ofset {offset})"
        fsck = {"msdos": ["fsck_msdos", "-n"], "exfat": ["fsck_exfat", "-n"],
                "hfs": ["fsck_hfs", "-fn"]}.get(MAC_FS[fs_key])
        note = ""
        if fsck:
            raw = target.replace("/dev/disk", "/dev/rdisk")
            fr = subprocess.run(fsck + [raw], capture_output=True, text=True)
            if fr.returncode != 0:
                return "fail", f"{fsck[0]} cikis {fr.returncode}: {(fr.stdout + fr.stderr)[-500:]}"
            note = f", {fsck[0]} temiz"
        mnt = tempfile.mkdtemp(prefix="du_mnt_")
        mr = subprocess.run(["diskutil", "mount", "readOnly", "-mountPoint", mnt, target],
                            capture_output=True, text=True)
        if mr.returncode != 0:
            # Teshis: dogrudan mount'un hatasi, diskutil'in gordugu tur ve
            # surucunun sistem gunlugune yazdiklari (neden bilinmeden "hata"
            # demek yetmez).
            dm = subprocess.run(["mount", "-r", "-t", MAC_FS[fs_key], target, mnt],
                                capture_output=True, text=True)
            info = subprocess.run(["diskutil", "info", target], capture_output=True,
                                  text=True).stdout
            tur = [l.strip() for l in info.splitlines()
                   if "Type (Bundle)" in l or "File System Personality" in l]
            kayit = subprocess.run(["log", "show", "--last", "2m", "--style", "compact",
                                    "--predicate", f'eventMessage CONTAINS[c] "{MAC_FS[fs_key]}"'],
                                   capture_output=True, text=True).stdout.splitlines()[-8:]
            subprocess.run(["diskutil", "unmount", "force", mnt], capture_output=True)
            os.rmdir(mnt)
            return "fail", (f"diskutil mount: {(mr.stdout + mr.stderr)[-300:]} | "
                            f"mount -t {MAC_FS[fs_key]}: {(dm.stdout + dm.stderr).strip()[-300:]} | "
                            f"diskutil: {tur} | gunluk: {' / '.join(kayit)[-700:]}")
        try:
            errors = _hash_tree(mnt, manifest)
        finally:
            subprocess.run(["diskutil", "unmount", "force", mnt], capture_output=True)
            try:
                os.rmdir(mnt)
            except OSError:
                pass
        if errors:
            return "fail", f"macOS surucusu: {'; '.join(errors[:8])}"
        return "ok", f"macOS {MAC_FS[fs_key]}: {len(manifest.entries)} dosya ayni{note}"
    finally:
        subprocess.run(["hdiutil", "detach", disk, "-force"], capture_output=True)
