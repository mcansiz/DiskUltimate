"""Sistemdeki gercek disklere erisim (Windows / Linux / macOS).

GUVENLIK TASARIMI — bu modul veri kaybina yol acabilecek tek yerdir:

1. **Varsayilan salt okunur.** `PhysicalDisk` yazma iznini ancak `readonly=False`
   *ve* `confirm=True` birlikte verildiginde acar.
2. **Sistem diski korumasi.** Isletim sisteminin kurulu oldugu disk isaretlenir;
   yazmak icin ayrica `allow_system=True` gerekir, aksi halde `SystemDiskError`.
3. **Bagli bolum uyarisi.** Disk uzerinde bagli (mounted) bolum varsa bildirilir;
   arayuz bunu kullaniciya gosterir.
4. **Listeleme zararsizdir.** `list_disks()` yalnizca isletim sisteminin bilgi
   arayuzlerini okur; hicbir diski acmaz.
"""
from __future__ import annotations

import os
import re
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .image import BlockDevice, DiskImageError
from .platform import IS_LINUX, IS_MACOS, IS_WINDOWS, run_tool

SECTOR = 512


class PhysicalDiskError(DiskImageError):
    """Fiziksel disk erisim hatasi."""


class AccessDeniedError(PhysicalDiskError):
    """Yetki yetersiz (root / Yonetici gerekli)."""


class SystemDiskError(PhysicalDiskError):
    """Sistem diskine yazma girisimi acikca onaylanmadi."""


# --------------------------------------------------------------------------
@dataclass
class DiskInfo:
    """Bir fiziksel diskin tanitim bilgisi (disk acilmadan toplanir)."""

    path: str                     # /dev/sda  |  \\.\PhysicalDrive0  |  /dev/disk0
    name: str                     # sda | PhysicalDrive0 | disk0
    size: int = 0
    sector_size: int = SECTOR
    model: str = ""
    serial: str = ""
    bus: str = ""                 # sata / nvme / usb / scsi / sanal
    removable: bool = False
    readonly: bool = False        # aygit donanimsal olarak yazma korumali mi
    is_system: bool = False       # isletim sistemi bu diskte mi
    mounted: List[str] = field(default_factory=list)   # bagli bolum yollari
    partitions: List[str] = field(default_factory=list)
    info_complete: bool = True    # bilgiler eksiksiz okunabildi mi (yetki!)
    os_hint: str = ""             # 'windows' | 'linux' | 'macos' | '' 

    @property
    def os_label(self) -> str:
        return {"windows": "Windows", "linux": "Linux",
                "macos": "macOS"}.get(self.os_hint, "")

    @property
    def display_name(self) -> str:
        ad = self.model.strip() or self.name
        return f"{ad} ({self.name})"

    @property
    def risk_level(self) -> str:
        """'sistem' | 'bagli' | 'bilinmiyor' | 'normal' — arayuzde uyari seviyesi."""
        if self.is_system:
            return "sistem"
        if self.mounted:
            return "bagli"
        if not self.info_complete:
            # Yetki olmadan sistem diski / bagli bolum tespiti yapilamaz.
            # Eksik bilgiyi "risk yok" diye sunmak tehlikelidir.
            return "bilinmiyor"
        return "normal"

    @property
    def risk_text(self) -> str:
        if self.is_system:
            return "ISLETIM SISTEMI DISKI — yazmak makineyi kullanilamaz hale getirir"
        if self.mounted:
            return f"Bagli bolum var ({', '.join(self.mounted[:3])}) — yazmak veri kaybettirir"
        if not self.info_complete:
            return ("Disk bilgileri okunamadi (yetki yok) — sistem diski olup "
                    "olmadigi BILINMIYOR")
        return "Bagli bolum yok"

    def summary(self) -> Dict[str, str]:
        from .ptable import human_size
        return {
            "Aygit": self.path,
            "Bilgi durumu": "Eksiksiz" if self.info_complete else "EKSIK (yetki yok)",
            "Model": self.model or "-",
            "Seri no": self.serial or "-",
            "Boyut": human_size(self.size),
            "Sektor boyutu": f"{self.sector_size} bayt",
            "Baglanti": self.bus or "-",
            "Cikarilabilir": "Evet" if self.removable else "Hayir",
            "Yazma korumali": "Evet" if self.readonly else "Hayir",
            "Sistem diski": ("EVET" if self.is_system else
                             ("Hayir" if self.info_complete else "BILINMIYOR")),
            "Isletim sistemi": self.os_label or "-",
            "Bagli bolumler": ", ".join(self.mounted) if self.mounted else "yok",
        }


# ==========================================================================
# Listeleme
# ==========================================================================
def running_os() -> str:
    """Su an calisan isletim sistemi: 'windows' | 'linux' | 'macos' | ''."""
    if IS_WINDOWS:
        return "windows"
    if IS_MACOS:
        return "macos"
    if IS_LINUX:
        return "linux"
    return ""


def guess_os_from_partitions(partitions) -> str:
    """Bolum listesinden diskteki isletim sistemini tahmin eder.

    Disk acilabildiginde kullanilir; kesin degil, ipucudur.
    """
    fsler = {(p.fs_type or "").lower() for p in partitions}
    turler = {(getattr(p, "type_guid", "") or "").upper() for p in partitions}
    tip_baytlari = {getattr(p, "type_id", 0) for p in partitions}

    MSR = "E3C9E316-0B5C-4DB8-817D-F92DF00215AE"      # Microsoft Reserved
    KURTARMA = "DE94BBA4-06D1-4D40-A16A-BFD50179D6AC"  # Windows Kurtarma
    HFS = "48465300-0000-11AA-AA11-00306543ECAC"
    APFS = "7C3457EF-0000-11AA-AA11-00306543ECAC"

    if MSR in turler or KURTARMA in turler or 0x27 in tip_baytlari:
        return "windows"
    if HFS in turler or APFS in turler or 0xAF in tip_baytlari:
        return "macos"
    if fsler & {"ext2", "ext3", "ext4", "btrfs", "xfs", "f2fs"} or \
            "linux takas" in fsler or 0x83 in tip_baytlari or 0x82 in tip_baytlari:
        return "linux"
    if "ntfs" in fsler:
        return "windows"
    return ""


def list_disks(include_removable: bool = True) -> List[DiskInfo]:
    """Sistemdeki fiziksel diskleri listeler. Hicbir diski acmaz."""
    if IS_LINUX:
        diskler = _list_linux()
    elif IS_WINDOWS:
        diskler = _list_windows()
    elif IS_MACOS:
        diskler = _list_macos()
    else:
        diskler = []
    # Sistem diski, uzerinde su an calisan isletim sistemini barindirir
    for d in diskler:
        if d.is_system and not d.os_hint:
            d.os_hint = running_os()
    if not include_removable:
        diskler = [d for d in diskler if not d.removable]
    return sorted(diskler, key=lambda d: d.name)


# -- Linux -----------------------------------------------------------------
_LINUX_ATLA = ("loop", "ram", "zram", "dm-", "md", "sr", "fd")


def _oku(yol: str, varsayilan: str = "") -> str:
    try:
        with open(yol, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return varsayilan


def _linux_mounts() -> Dict[str, str]:
    """{aygit yolu: baglama noktasi}"""
    sonuc: Dict[str, str] = {}
    for satir in _oku("/proc/mounts").splitlines():
        parcalar = satir.split()
        if len(parcalar) >= 2 and parcalar[0].startswith("/dev/"):
            sonuc[parcalar[0]] = parcalar[1].replace("\\040", " ")
    return sonuc


def _list_linux() -> List[DiskInfo]:
    kok = "/sys/block"
    if not os.path.isdir(kok):
        return []
    baglantilar = _linux_mounts()
    sistem_aygiti = ""
    for aygit, nokta in baglantilar.items():
        if nokta == "/":
            sistem_aygiti = aygit
            break

    diskler: List[DiskInfo] = []
    for ad in sorted(os.listdir(kok)):
        if ad.startswith(_LINUX_ATLA):
            continue
        taban = os.path.join(kok, ad)
        sektor_sayisi = int(_oku(os.path.join(taban, "size"), "0") or 0)
        if sektor_sayisi <= 0:
            continue
        mantiksal = int(_oku(os.path.join(taban, "queue/logical_block_size"), "512") or 512)
        bilgi = DiskInfo(
            path=f"/dev/{ad}",
            name=ad,
            size=sektor_sayisi * 512,      # /sys/block/*/size her zaman 512 birimlidir
            sector_size=mantiksal,
            model=_oku(os.path.join(taban, "device/model")) or _oku(
                os.path.join(taban, "device/name")),
            serial=_oku(os.path.join(taban, "device/serial")),
            removable=_oku(os.path.join(taban, "removable"), "0") == "1",
            readonly=_oku(os.path.join(taban, "ro"), "0") == "1",
        )
        if ad.startswith("nvme"):
            bilgi.bus = "NVMe"
        elif os.path.exists(os.path.join(taban, "device/vendor")):
            bilgi.bus = _oku(os.path.join(taban, "device/vendor")) or "SCSI/SATA"
        if "usb" in os.path.realpath(taban):
            bilgi.bus = "USB"

        # bolumler ve baglama durumu
        for giris in sorted(os.listdir(taban)):
            if giris.startswith(ad) and os.path.isdir(os.path.join(taban, giris)):
                bolum = f"/dev/{giris}"
                bilgi.partitions.append(bolum)
                if bolum in baglantilar:
                    bilgi.mounted.append(f"{giris} → {baglantilar[bolum]}")
                if sistem_aygiti and bolum == sistem_aygiti:
                    bilgi.is_system = True
        if sistem_aygiti and sistem_aygiti.startswith(f"/dev/{ad}"):
            bilgi.is_system = True
        diskler.append(bilgi)
    return diskler


# -- Windows ---------------------------------------------------------------
IOCTL_DISK_GET_LENGTH_INFO = 0x0007405C
IOCTL_DISK_GET_DRIVE_GEOMETRY = 0x00070000
IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1400
IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS = 0x00560000
IOCTL_DISK_UPDATE_PROPERTIES = 0x00070140
FSCTL_LOCK_VOLUME = 0x00090018
FSCTL_UNLOCK_VOLUME = 0x0009001C
FSCTL_DISMOUNT_VOLUME = 0x00090020
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
INVALID_HANDLE = -1
ERROR_ACCESS_DENIED = 5


def _win_handle(path: str, write: bool = False):
    """Windows aygit tutamaci acar. Hata durumunda istisna firlatir."""
    import ctypes
    import ctypes.wintypes as wt

    k32 = ctypes.windll.kernel32
    k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID,
                                wt.DWORD, wt.DWORD, wt.HANDLE]
    k32.CreateFileW.restype = wt.HANDLE
    erisim = GENERIC_READ | (GENERIC_WRITE if write else 0)
    tutamac = k32.CreateFileW(path, erisim,
                              FILE_SHARE_READ | FILE_SHARE_WRITE,
                              None, OPEN_EXISTING, 0, None)
    if tutamac in (INVALID_HANDLE, 0, None) or tutamac == ctypes.c_void_p(-1).value:
        hata = ctypes.get_last_error() or k32.GetLastError()
        if hata == ERROR_ACCESS_DENIED:
            raise AccessDeniedError(
                f"{path} acilamadi: Yonetici yetkisi gerekiyor "
                "(uygulamayi 'Yonetici olarak calistir' ile baslatin)")
        raise PhysicalDiskError(f"{path} acilamadi (Windows hatasi {hata})")
    return tutamac


def _win_ioctl(tutamac, kod: int, giris: bytes = b"", cikis_boyut: int = 256):
    import ctypes
    import ctypes.wintypes as wt

    k32 = ctypes.windll.kernel32
    k32.DeviceIoControl.argtypes = [wt.HANDLE, wt.DWORD, wt.LPVOID, wt.DWORD,
                                    wt.LPVOID, wt.DWORD,
                                    ctypes.POINTER(wt.DWORD), wt.LPVOID]
    k32.DeviceIoControl.restype = wt.BOOL
    tampon = ctypes.create_string_buffer(cikis_boyut)
    donen = wt.DWORD(0)
    giris_tampon = ctypes.create_string_buffer(giris) if giris else None
    ok = k32.DeviceIoControl(wt.HANDLE(tutamac), kod,
                             giris_tampon, len(giris),
                             tampon, cikis_boyut, ctypes.byref(donen), None)
    if not ok:
        return None
    return tampon.raw[:donen.value]


def _win_device_info(tutamac) -> Dict[str, str]:
    """IOCTL_STORAGE_QUERY_PROPERTY ile model/seri/veriyolu okur."""
    # STORAGE_PROPERTY_QUERY: PropertyId=StorageDeviceProperty(0), QueryType=Standard(0)
    sorgu = struct.pack("<II", 0, 0) + b"\x00" * 8
    ham = _win_ioctl(tutamac, IOCTL_STORAGE_QUERY_PROPERTY, sorgu, 1024)
    if not ham or len(ham) < 40:
        return {}
    (_ver, _boyut, _aygit_turu, _degistirici, cikarilabilir, _komut_kuyrugu,
     saticiid_ofset, urunid_ofset, urun_surum_ofset, seri_ofset,
     veriyolu_turu) = struct.unpack_from("<IIBBBBIIIII", ham, 0)

    def metin(ofset: int) -> str:
        if not ofset or ofset >= len(ham):
            return ""
        son = ham.find(b"\x00", ofset)
        return ham[ofset:son if son > 0 else len(ham)].decode("latin-1", "ignore").strip()

    veriyollari = {1: "SCSI", 2: "ATAPI", 3: "ATA", 4: "1394", 5: "SSA", 6: "Fibre",
                   7: "USB", 8: "RAID", 9: "iSCSI", 10: "SAS", 11: "SATA",
                   12: "SD", 13: "MMC", 17: "NVMe"}
    return {
        "model": (metin(saticiid_ofset) + " " + metin(urunid_ofset)).strip(),
        "serial": metin(seri_ofset),
        "bus": veriyollari.get(veriyolu_turu, ""),
        "removable": "1" if cikarilabilir else "0",
    }


def _win_system_disk_numbers() -> List[int]:
    """Windows dizininin bulundugu surucunun disk numaralarini dondurur."""
    import ctypes

    windir = os.environ.get("SystemDrive", "C:")
    try:
        tutamac = _win_handle(f"\\\\.\\{windir.rstrip(chr(92))}", write=False)
    except PhysicalDiskError:
        return []
    try:
        ham = _win_ioctl(tutamac, IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS, b"", 1024)
        if not ham or len(ham) < 8:
            return []
        adet = struct.unpack_from("<I", ham, 0)[0]
        numaralar = []
        for i in range(min(adet, 16)):
            ofset = 8 + i * 24
            if ofset + 4 <= len(ham):
                numaralar.append(struct.unpack_from("<I", ham, ofset)[0])
        return numaralar
    finally:
        _win_close(tutamac)


def _win_close(tutamac) -> None:
    try:
        import ctypes
        ctypes.windll.kernel32.CloseHandle(ctypes.wintypes.HANDLE(tutamac))
    except Exception:
        pass


def _win_drive_letters() -> Dict[int, List[str]]:
    """{disk numarasi: [surucu harfleri]} — bagli bolumleri gostermek icin."""
    import ctypes

    sonuc: Dict[int, List[str]] = {}
    maske = ctypes.windll.kernel32.GetLogicalDrives()
    for i in range(26):
        if not (maske >> i) & 1:
            continue
        harf = chr(ord("A") + i)
        try:
            tutamac = _win_handle(f"\\\\.\\{harf}:", write=False)
        except PhysicalDiskError:
            continue
        try:
            ham = _win_ioctl(tutamac, IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS, b"", 1024)
            if ham and len(ham) >= 12:
                adet = struct.unpack_from("<I", ham, 0)[0]
                for n in range(min(adet, 16)):
                    ofset = 8 + n * 24
                    if ofset + 4 <= len(ham):
                        disk_no = struct.unpack_from("<I", ham, ofset)[0]
                        sonuc.setdefault(disk_no, []).append(f"{harf}:")
        finally:
            _win_close(tutamac)
    return sonuc


def _list_windows() -> List[DiskInfo]:
    diskler: List[DiskInfo] = []
    try:
        sistem_numaralari = set(_win_system_disk_numbers())
        harfler = _win_drive_letters()
    except Exception:
        sistem_numaralari, harfler = set(), {}

    for numara in range(32):
        yol = f"\\\\.\\PhysicalDrive{numara}"
        try:
            tutamac = _win_handle(yol, write=False)
        except AccessDeniedError:
            # Disk var ama yetki yok: yine de listede goster
            diskler.append(DiskInfo(path=yol, name=f"PhysicalDrive{numara}",
                                    model="(yetki yok — Yonetici gerekli)",
                                    is_system=numara in sistem_numaralari,
                                    mounted=harfler.get(numara, []),
                                    info_complete=False))
            continue
        except PhysicalDiskError:
            continue
        try:
            bilgi = DiskInfo(path=yol, name=f"PhysicalDrive{numara}")
            ham = _win_ioctl(tutamac, IOCTL_DISK_GET_LENGTH_INFO, b"", 8)
            if ham and len(ham) >= 8:
                bilgi.size = struct.unpack("<q", ham[:8])[0]
            geo = _win_ioctl(tutamac, IOCTL_DISK_GET_DRIVE_GEOMETRY, b"", 24)
            if geo and len(geo) >= 24:
                bilgi.sector_size = struct.unpack_from("<I", geo, 20)[0] or SECTOR
            ayrinti = _win_device_info(tutamac)
            bilgi.model = ayrinti.get("model", "")
            bilgi.serial = ayrinti.get("serial", "")
            bilgi.bus = ayrinti.get("bus", "")
            bilgi.removable = ayrinti.get("removable") == "1"
            bilgi.is_system = numara in sistem_numaralari
            bilgi.mounted = harfler.get(numara, [])
            if bilgi.size > 0:
                diskler.append(bilgi)
        finally:
            _win_close(tutamac)
    return diskler


# -- macOS -----------------------------------------------------------------
def _list_macos() -> List[DiskInfo]:
    diskler: List[DiskInfo] = []
    try:
        cikti = run_tool(["diskutil", "list", "-plist"], timeout=20)
        if cikti.returncode != 0:
            return []
        import plistlib
        veri = plistlib.loads(cikti.stdout.encode("utf-8", "replace"))
    except Exception:
        return []
    for ad in veri.get("WholeDisks", []):
        bilgi = DiskInfo(path=f"/dev/{ad}", name=ad)
        try:
            ayrinti = run_tool(["diskutil", "info", "-plist", ad], timeout=20)
            import plistlib
            d = plistlib.loads(ayrinti.stdout.encode("utf-8", "replace"))
            bilgi.size = int(d.get("TotalSize", 0))
            bilgi.sector_size = int(d.get("DeviceBlockSize", SECTOR))
            bilgi.model = str(d.get("MediaName", ""))
            bilgi.removable = bool(d.get("Removable", False))
            bilgi.readonly = not bool(d.get("WritableMedia", True))
            bilgi.is_system = bool(d.get("SystemImage", False)) or ad == "disk0"
            nokta = d.get("MountPoint")
            if nokta:
                bilgi.mounted.append(str(nokta))
        except Exception:
            pass
        if bilgi.size > 0:
            diskler.append(bilgi)
    return diskler


def find_disk(path: str) -> Optional[DiskInfo]:
    for d in list_disks():
        if d.path == path or d.name == path:
            return d
    return None


# ==========================================================================
# Aygit erisimi
# ==========================================================================
class PhysicalDisk(BlockDevice):
    """Gercek bir diske blok duzeyinde erisim.

    Varsayilan **salt okunur**. Yazma icin `readonly=False` ve `confirm=True`
    birlikte verilmelidir; sistem diskinde ayrica `allow_system=True` gerekir.
    """

    def __init__(self, info_or_path, readonly: bool = True,
                 confirm: bool = False, allow_system: bool = False):
        bilgi = info_or_path if isinstance(info_or_path, DiskInfo) else find_disk(info_or_path)
        if bilgi is None:
            yol = info_or_path if isinstance(info_or_path, str) else ""
            raise PhysicalDiskError(f"Disk bulunamadi: {yol}")
        self.info = bilgi
        self.path = bilgi.path
        self.sector_size = bilgi.sector_size or SECTOR

        if not readonly:
            if not confirm:
                raise PhysicalDiskError(
                    "Yazma modu acikca onaylanmalidir (confirm=True)")
            if bilgi.readonly:
                raise PhysicalDiskError("Aygit donanimsal olarak yazma korumali")
            if not bilgi.info_complete and not allow_system:
                raise SystemDiskError(
                    f"{bilgi.path} bilgileri okunamadi (yetki yok); sistem diski "
                    "olup olmadigi bilinmiyor. Bilinmeyen bir diske yazma "
                    "reddedildi.")
            if bilgi.is_system and not allow_system:
                raise SystemDiskError(
                    f"{bilgi.path} isletim sistemi diskidir. Yazma islemi "
                    "makineyi acilamaz hale getirebilir; bu diske yazmak icin "
                    "ayrica onay gerekir.")
        self.readonly = readonly
        self._size = bilgi.size
        self._fh = None
        self._win_handle = None
        self._volume_handles: List[object] = []
        self._ac()
        if IS_WINDOWS and not readonly:
            self._win_lock_volumes()

    # -- acma/kapatma ------------------------------------------------------
    def _ac(self) -> None:
        if IS_WINDOWS:
            self._win_handle = _win_handle(self.path, write=not self.readonly)
            return
        mod = os.O_RDONLY if self.readonly else os.O_RDWR
        try:
            fd = os.open(self.path, mod)
        except PermissionError:
            raise AccessDeniedError(
                f"{self.path} acilamadi: yetki yetersiz. Uygulamayi 'sudo' ile "
                "calistirin veya kullaniciyi 'disk' grubuna ekleyin.")
        except FileNotFoundError:
            raise PhysicalDiskError(f"Aygit yok: {self.path}")
        except OSError as exc:
            raise PhysicalDiskError(f"{self.path} acilamadi: {exc}")
        self._fh = os.fdopen(fd, "rb" if self.readonly else "r+b", buffering=0)
        if self._size <= 0:
            self._size = self._fh.seek(0, os.SEEK_END)

    def _win_lock_volumes(self) -> None:
        """Diskteki birimleri kilitler ve baglantisini keser (Windows).

        Windows, **bagli** bir birimin sektorlerine dogrudan yazmayi reddeder
        (ERROR_ACCESS_DENIED). Bicimlendirme araclari bu yuzden once birimi
        kilitler (`FSCTL_LOCK_VOLUME`) ve baglantisini keser
        (`FSCTL_DISMOUNT_VOLUME`). Kilit, tutamac acik kaldigi surece gecerlidir
        ve baska bir surecin birimi yeniden baglamasini engeller.

        Basarisizlik olumcul degildir: kilitlenemeyen birim varsa yazma zaten
        anlamli bir hata ile reddedilir.
        """
        numaralar = "".join(ch for ch in self.info.name if ch.isdigit())
        if not numaralar:
            return
        disk_no = int(numaralar)
        try:
            harfler = _win_drive_letters().get(disk_no, [])
        except Exception:
            harfler = []
        for harf in harfler:
            try:
                tutamac = _win_handle(f"\\\\.\\{harf}", write=True)
            except PhysicalDiskError:
                continue
            kilitlendi = _win_ioctl(tutamac, FSCTL_LOCK_VOLUME, b"", 0) is not None
            _win_ioctl(tutamac, FSCTL_DISMOUNT_VOLUME, b"", 0)
            if kilitlendi:
                self._volume_handles.append(tutamac)
            else:
                # kilitlenemedi: tutamaci birak, birim kullanimda olabilir
                _win_close(tutamac)

    def _win_release_volumes(self) -> None:
        """Kilitli birim tutamaclarini birakir."""
        for tutamac in self._volume_handles:
            try:
                _win_ioctl(tutamac, FSCTL_UNLOCK_VOLUME, b"", 0)
            except Exception:
                pass
            _win_close(tutamac)
        self._volume_handles = []

    def close(self) -> None:
        # Kilitli birimler once birakilir ki rescan sonrasi yeniden baglanabilsinler
        if IS_WINDOWS and self._volume_handles:
            self._win_release_volumes()
        # Yazma modunda kapanirken isletim sistemine degisikligi bildir
        if not self.readonly:
            try:
                self.rescan_partitions()
            except Exception:
                pass
        if self._fh is not None:
            try:
                self._fh.close()
            finally:
                self._fh = None
        if self._win_handle is not None:
            _win_close(self._win_handle)
            self._win_handle = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- BlockDevice arayuzu ----------------------------------------------
    @property
    def sector_count(self) -> int:
        return self._size // self.sector_size

    @property
    def size(self) -> int:
        return self._size

    def read(self, offset: int, length: int) -> bytes:
        if offset + length > self._size:
            raise PhysicalDiskError("Okuma disk sinirini asiyor")
        if IS_WINDOWS:
            return self._win_read(offset, length)
        self._fh.seek(offset)
        veri = self._fh.read(length)
        return veri + b"\x00" * (length - len(veri))

    def write(self, offset: int, data: bytes) -> None:
        if self.readonly:
            raise PhysicalDiskError("Disk salt okunur acildi")
        if offset + len(data) > self._size:
            raise PhysicalDiskError("Yazma disk sinirini asiyor")
        if IS_WINDOWS:
            self._win_write(offset, data)
            return
        self._fh.seek(offset)
        self._fh.write(data)

    def flush(self) -> None:
        if self._fh is not None and not self.readonly:
            self._fh.flush()
            os.fsync(self._fh.fileno())

    def rescan_partitions(self) -> bool:
        """Isletim sistemine bolum tablosunun degistigini bildirir.

        Ham (raw) yazma yapildiginda isletim sistemi bunu kendiliginden fark
        etmez: Windows diski "RAW" gostermeye devam eder, Linux eski bolum
        aygitlarini tutar. Bu cagri olmadan kullanici bicimlendirdigi bolumu
        Gezgin'de goremez.

        Yalnizca yazma modunda anlamlidir; basarisizlik olumcul degildir.
        """
        if self.readonly:
            return False
        self.flush()
        if IS_WINDOWS:
            if self._win_handle is None:
                return False
            sonuc = _win_ioctl(self._win_handle, IOCTL_DISK_UPDATE_PROPERTIES,
                               b"", 0)
            return sonuc is not None
        if IS_LINUX:
            try:
                import fcntl
                BLKRRPART = 0x125F          # bolum tablosunu yeniden oku
                fcntl.ioctl(self._fh.fileno(), BLKRRPART)
                return True
            except (OSError, ImportError):
                return False
        if IS_MACOS:
            try:
                sonuc = run_tool(["diskutil", "rescan", self.path], timeout=30)
                return sonuc.returncode == 0
            except Exception:
                return False
        return False

    # -- Windows: aygit G/C sektor hizali olmak zorundadir -----------------
    def _win_read(self, offset: int, length: int) -> bytes:
        import ctypes
        import ctypes.wintypes as wt

        ss = self.sector_size
        bas = (offset // ss) * ss
        son = ((offset + length + ss - 1) // ss) * ss
        k32 = ctypes.windll.kernel32
        k32.SetFilePointerEx.argtypes = [wt.HANDLE, ctypes.c_longlong,
                                         ctypes.POINTER(ctypes.c_longlong), wt.DWORD]
        k32.SetFilePointerEx.restype = wt.BOOL
        k32.ReadFile.argtypes = [wt.HANDLE, wt.LPVOID, wt.DWORD,
                                 ctypes.POINTER(wt.DWORD), wt.LPVOID]
        k32.ReadFile.restype = wt.BOOL
        if not k32.SetFilePointerEx(wt.HANDLE(self._win_handle),
                                    ctypes.c_longlong(bas), None, 0):
            raise PhysicalDiskError("Disk konumlandirilamadi")
        tampon = ctypes.create_string_buffer(son - bas)
        okunan = wt.DWORD(0)
        if not k32.ReadFile(wt.HANDLE(self._win_handle), tampon, son - bas,
                            ctypes.byref(okunan), None):
            raise PhysicalDiskError(f"Okuma hatasi (Windows {k32.GetLastError()})")
        ham = tampon.raw[:okunan.value]
        ic = offset - bas
        return ham[ic:ic + length].ljust(length, b"\x00")

    def _win_write(self, offset: int, data: bytes) -> None:
        import ctypes
        import ctypes.wintypes as wt

        ss = self.sector_size
        bas = (offset // ss) * ss
        son = ((offset + len(data) + ss - 1) // ss) * ss
        # hizalama disinda kalan kenarlari korumak icin once oku-degistir-yaz
        mevcut = bytearray(self._win_read(bas, son - bas))
        ic = offset - bas
        mevcut[ic:ic + len(data)] = data
        k32 = ctypes.windll.kernel32
        k32.SetFilePointerEx.argtypes = [wt.HANDLE, ctypes.c_longlong,
                                         ctypes.POINTER(ctypes.c_longlong), wt.DWORD]
        k32.SetFilePointerEx.restype = wt.BOOL
        k32.WriteFile.argtypes = [wt.HANDLE, wt.LPVOID, wt.DWORD,
                                  ctypes.POINTER(wt.DWORD), wt.LPVOID]
        k32.WriteFile.restype = wt.BOOL
        if not k32.SetFilePointerEx(wt.HANDLE(self._win_handle),
                                    ctypes.c_longlong(bas), None, 0):
            raise PhysicalDiskError("Disk konumlandirilamadi")
        tampon = ctypes.create_string_buffer(bytes(mevcut))
        yazilan = wt.DWORD(0)
        if not k32.WriteFile(wt.HANDLE(self._win_handle), tampon, len(mevcut),
                             ctypes.byref(yazilan), None):
            hata = k32.GetLastError()
            if hata == ERROR_ACCESS_DENIED:
                raise AccessDeniedError(
                    "Yazma reddedildi: Windows bagli birimlere dogrudan yazmayi "
                    "engeller. Birimi cikarin (eject) veya Yonetici olarak calistirin.")
            raise PhysicalDiskError(f"Yazma hatasi (Windows {hata})")

    def __repr__(self) -> str:
        kip = "salt okunur" if self.readonly else "YAZILABILIR"
        return f"<PhysicalDisk {self.path} {self._size} bayt, {kip}>"


def can_access() -> bool:
    """Uygulamanin diskleri acabilecek yetkisi var mi (kaba denetim)."""
    if IS_WINDOWS:
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return os.geteuid() == 0 if hasattr(os, "geteuid") else False
