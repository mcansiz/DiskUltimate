"""Platforma bagli islemler tek yerde toplanir (Windows / macOS / Linux).

Cekirdegin geri kalani isletim sistemi ayrimi yapmaz; burayi cagirir.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import List, Optional

IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

PLATFORM_NAME = "Windows" if IS_WINDOWS else ("macOS" if IS_MACOS else
                                              ("Linux" if IS_LINUX else sys.platform))

# Goruntu dosyasi olarak kabul edilen uzantilar (dosya secicide kullanilir)
IMAGE_EXTENSIONS = ["img", "raw", "dd", "bin", "iso", "vhd", "vhdx", "vdi", "vmdk", "qcow2"]


# --------------------------------------------------------------------------
# Seyrek (sparse) dosya
# --------------------------------------------------------------------------
def _win_kernel32():
    """kernel32'yi acik argtypes ile dondurur (ctypes varsayilanlari 64 bit
    tutamaclari bozabilir)."""
    import ctypes
    import ctypes.wintypes as wt

    k32 = ctypes.windll.kernel32
    k32.DeviceIoControl.argtypes = [wt.HANDLE, wt.DWORD, wt.LPVOID, wt.DWORD,
                                    wt.LPVOID, wt.DWORD,
                                    ctypes.POINTER(wt.DWORD), wt.LPVOID]
    k32.DeviceIoControl.restype = wt.BOOL
    k32.SetFilePointerEx.argtypes = [wt.HANDLE, ctypes.c_longlong,
                                     ctypes.POINTER(ctypes.c_longlong), wt.DWORD]
    k32.SetFilePointerEx.restype = wt.BOOL
    k32.SetEndOfFile.argtypes = [wt.HANDLE]
    k32.SetEndOfFile.restype = wt.BOOL
    k32.GetCompressedFileSizeW.argtypes = [wt.LPCWSTR, ctypes.POINTER(wt.DWORD)]
    k32.GetCompressedFileSizeW.restype = wt.DWORD
    return k32, wt


def make_sparse(fileno: int) -> bool:
    """Dosyayi seyrek olarak isaretler. Basarisizsa False doner (olumcul degil).

    Linux/macOS'ta dosya sistemleri seyrekligi kendiliginden uygular; Windows'ta
    NTFS icin FSCTL_SET_SPARSE cagrisi gerekir.
    """
    if not IS_WINDOWS:
        return True
    try:
        import ctypes
        import msvcrt

        k32, wt = _win_kernel32()
        FSCTL_SET_SPARSE = 0x000900C4
        donen = wt.DWORD(0)
        ok = k32.DeviceIoControl(wt.HANDLE(msvcrt.get_osfhandle(fileno)),
                                 FSCTL_SET_SPARSE, None, 0, None, 0,
                                 ctypes.byref(donen), None)
        return bool(ok)
    except Exception:
        return False


def truncate_sparse(fileno: int, size: int) -> None:
    """Dosyayi `size` boyutuna uzatir; mumkunse gercek tahsis yapmadan.

    Windows'ta Python'un `truncate()` cagrisi `_chsize_s` uzerinden dosyayi
    **sifirlarla doldurur**: 256 MB'lik bir goruntu gercekten 256 MB yer kaplar.
    Bunun yerine `SetEndOfFile` kullanilir — seyrek isaretli dosyada anlik ve
    sifir bayt tuketir. Olcum (Win10/NTFS, 256 MB):
        truncate()    -> 0.65 s, 256 MB tahsis
        SetEndOfFile  -> 0.00 s,   0 MB tahsis
    """
    if not IS_WINDOWS:
        os.ftruncate(fileno, size)
        return
    try:
        import ctypes
        import msvcrt

        k32, wt = _win_kernel32()
        handle = wt.HANDLE(msvcrt.get_osfhandle(fileno))
        if k32.SetFilePointerEx(handle, ctypes.c_longlong(size), None, 0):
            if k32.SetEndOfFile(handle):
                return
    except Exception:
        pass
    os.ftruncate(fileno, size)      # geri cekilme: yavas ama dogru


def actual_size(path: str) -> int:
    """Dosyanin diskte gercekten kapladigi alan (seyreklik denetimi icin).

    Bilinemiyorsa mantiksal boyut dondurulur.
    """
    try:
        st = os.stat(path)
        blocks = getattr(st, "st_blocks", None)
        if blocks is not None:
            return blocks * 512
    except OSError:
        return 0
    if IS_WINDOWS:
        try:
            import ctypes

            k32, wt = _win_kernel32()
            yuksek = wt.DWORD(0)
            dusuk = k32.GetCompressedFileSizeW(path, ctypes.byref(yuksek))
            if dusuk != 0xFFFFFFFF:
                return (yuksek.value << 32) | dusuk
        except Exception:
            pass
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def supports_sparse(path: str) -> bool:
    """Hedef konumun seyrek dosya destekleyip desteklemedigini tahmin eder."""
    if IS_WINDOWS:
        return True     # NTFS varsayimi; FAT32'de sessizce yogun olur
    return True


# --------------------------------------------------------------------------
# Harici bicimlendirme araclari
# --------------------------------------------------------------------------
# Her dosya sistemi icin platforma gore aday arac adlari.
EXTERNAL_TOOLS = {
    "exfat": {"linux": ["mkfs.exfat", "mkexfatfs"], "darwin": ["newfs_exfat"], "win32": []},
    "ntfs":  {"linux": ["mkfs.ntfs", "mkntfs"], "darwin": [], "win32": []},
    "ext2":  {"linux": ["mkfs.ext2"], "darwin": [], "win32": []},
    "ext3":  {"linux": ["mkfs.ext3"], "darwin": [], "win32": []},
    "ext4":  {"linux": ["mkfs.ext4"], "darwin": [], "win32": []},
}

_EK_ARAMA_YOLLARI = ["/sbin", "/usr/sbin", "/usr/local/sbin", "/opt/homebrew/sbin",
                     "/usr/local/bin", "/opt/homebrew/bin"]


def _platform_key() -> str:
    if IS_WINDOWS:
        return "win32"
    if IS_MACOS:
        return "darwin"
    return "linux"


def find_tool(fs_key: str) -> Optional[str]:
    """Verilen dosya sistemi icin kullanilabilir `mkfs` aracinin tam yolu."""
    adaylar = EXTERNAL_TOOLS.get(fs_key, {}).get(_platform_key(), [])
    for ad in adaylar:
        yol = shutil.which(ad)
        if yol:
            return yol
        for dizin in _EK_ARAMA_YOLLARI:     # sbin cogu dagitimda PATH'te degil
            tam = os.path.join(dizin, ad)
            if os.path.isfile(tam) and os.access(tam, os.X_OK):
                return tam
    return None


def tool_names(fs_key: str) -> List[str]:
    """Bu platformda aranan arac adlari (kullaniciya mesaj gostermek icin)."""
    return EXTERNAL_TOOLS.get(fs_key, {}).get(_platform_key(), [])


def run_tool(cmd: List[str], timeout: int = 900) -> subprocess.CompletedProcess:
    """Harici araci calistirir; Windows'ta konsol penceresi acmaz."""
    kwargs = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = 0x08000000    # CREATE_NO_WINDOW
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kwargs)


# --------------------------------------------------------------------------
# Arayuz
# --------------------------------------------------------------------------
def preferred_qt_platform() -> str:
    """Kullanilacak Qt platform eklentisi ('' = Qt karar versin).

    Qt5'in yerel Wayland eklentisinde modal pencerelerin ilk karesi
    boyanmadigindan (bkz. ADR 0005) Wayland oturumlarinda XWayland secilir.
    Windows ve macOS'ta karar Qt'ye birakilir.
    """
    if IS_WINDOWS or IS_MACOS:
        return ""
    wayland = (os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
               or bool(os.environ.get("WAYLAND_DISPLAY")))
    if wayland and os.environ.get("DISPLAY"):
        return "xcb"
    return ""


def default_image_dir() -> str:
    """Yeni goruntuler icin varsayilan klasor."""
    for ad in ("Documents", "Belgeler"):
        yol = os.path.join(os.path.expanduser("~"), ad)
        if os.path.isdir(yol):
            return yol
    return os.path.expanduser("~")


def summary() -> dict:
    """Tani amacli platform ozeti."""
    return {
        "Platform": PLATFORM_NAME,
        "Python": sys.version.split()[0],
        "Mimari": "64 bit" if sys.maxsize > 2 ** 32 else "32 bit",
        "Harici araclar": ", ".join(
            f"{k}:{'var' if find_tool(k) else 'yok'}" for k in EXTERNAL_TOOLS),
    }


# --------------------------------------------------------------------------
# Isletim sisteminin kendi bicimlendiricisi
# --------------------------------------------------------------------------
def native_format_supported(fs_key: str) -> bool:
    """Isletim sistemi bu bicimi kendi araciyla olusturabiliyor mu?

    Windows'ta NTFS/FAT/exFAT icin `Format-Volume` her kurulumda bulunur; bu,
    ntfs-3g'nin Windows'ta olmamasinin pratik karsiligidir.
    """
    if IS_WINDOWS:
        return fs_key in ("ntfs", "exfat", "fat32", "fat16")
    return False


def windows_format_volume(disk_number: int, partition_number: int,
                          fs_key: str, label: str = "",
                          cluster_size: int = 0) -> tuple:
    """Windows'un kendi bicimlendiricisiyle bir bolumu bicimlendirir.

    Surucu harfi gerekmez: bolum nesnesi `Format-Volume`e dogrudan verilir.
    (basarili_mi, mesaj) dondurur.
    """
    if not IS_WINDOWS:
        return False, "Yalnizca Windows"
    fs_adi = {"ntfs": "NTFS", "exfat": "exFAT",
              "fat32": "FAT32", "fat16": "FAT"}.get(fs_key)
    if not fs_adi:
        return False, f"{fs_key} Windows araciyla olusturulamaz"
    etiket = (label or "").replace('"', "")
    komut = (f"$ErrorActionPreference='Stop'; "
             f"$p = Get-Partition -DiskNumber {disk_number} "
             f"-PartitionNumber {partition_number}; "
             f"$p | Format-Volume -FileSystem {fs_adi} "
             f"-NewFileSystemLabel \"{etiket}\" -Confirm:$false -Force")
    if cluster_size:
        komut += f" -AllocationUnitSize {cluster_size}"
    komut += " | Out-Null; 'TAMAM'"
    try:
        sonuc = run_tool(["powershell", "-NoProfile", "-NonInteractive",
                          "-Command", komut], timeout=600)
    except Exception as exc:
        return False, str(exc)
    if sonuc.returncode == 0 and "TAMAM" in (sonuc.stdout or ""):
        return True, "Windows bicimlendiricisi kullanildi"
    return False, ((sonuc.stderr or sonuc.stdout or "").strip()[:300]
                   or f"cikis kodu {sonuc.returncode}")


def native_resize_supported() -> bool:
    """Isletim sistemi bir bolumu kendi araciyla yeniden boyutlandirabiliyor mu?

    Windows'ta `Resize-Partition` NTFS'i (ve destekledigi digerlerini) dosya
    sistemiyle birlikte buyutup kucultur; saf Python'da NTFS/ext boyutlandirmasi
    olmadigi icin fiziksel disklerde bu yol tercih edilir.
    """
    return IS_WINDOWS


def windows_partition_size_limits(disk_number: int, partition_number: int) -> tuple:
    """(basarili_mi, en_kucuk_bayt, en_buyuk_bayt, mesaj)."""
    if not IS_WINDOWS:
        return False, 0, 0, "Yalnizca Windows"
    komut = (f"$ErrorActionPreference='Stop'; "
             f"$s = Get-PartitionSupportedSize -DiskNumber {disk_number} "
             f"-PartitionNumber {partition_number}; "
             f"\"$($s.SizeMin) $($s.SizeMax)\"")
    try:
        sonuc = run_tool(["powershell", "-NoProfile", "-NonInteractive",
                          "-Command", komut], timeout=120)
    except Exception as exc:                       # noqa: BLE001
        return False, 0, 0, str(exc)
    if sonuc.returncode != 0:
        return False, 0, 0, (sonuc.stderr or sonuc.stdout or "").strip()[:300]
    try:
        alt, ust = (sonuc.stdout or "").strip().split()
        return True, int(alt), int(ust), ""
    except ValueError:
        return False, 0, 0, "Beklenmeyen cikti"


def windows_resize_partition(disk_number: int, partition_number: int,
                             size_bytes: int) -> tuple:
    """Windows'un kendi boyutlandiricisiyla bolumu yeniden boyutlandirir.

    Dosya sistemi de birlikte boyutlandirilir. (basarili_mi, mesaj) dondurur.
    """
    if not IS_WINDOWS:
        return False, "Yalnizca Windows"
    komut = (f"$ErrorActionPreference='Stop'; "
             f"Resize-Partition -DiskNumber {disk_number} "
             f"-PartitionNumber {partition_number} -Size {int(size_bytes)}; "
             f"'TAMAM'")
    try:
        sonuc = run_tool(["powershell", "-NoProfile", "-NonInteractive",
                          "-Command", komut], timeout=1800)
    except Exception as exc:                       # noqa: BLE001
        return False, str(exc)
    if sonuc.returncode == 0 and "TAMAM" in (sonuc.stdout or ""):
        return True, "Windows boyutlandiricisi kullanildi"
    return False, (sonuc.stderr or sonuc.stdout or "").strip()[:300]
