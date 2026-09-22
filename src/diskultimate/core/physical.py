"""Sistemdeki gercek disklere erisim (Windows / Linux / macOS).

GUVENLIK TASARIMI — bu modul veri kaybina yol acabilecek tek yerdir:

1. **Varsayilan salt okunur.** `PhysicalDisk` yazma iznini ancak `readonly=False`
   *ve* `confirm=True` birlikte verildiginde acar.
2. **Sistem diski korumasi.** Isletim sisteminin kurulu oldugu disk isaretlenir;
   yazmak icin ayrica `allow_system=True` gerekir, aksi halde `SystemDiskError`.
3. **Bagli bolum uyarisi.** Disk uzerinde bagli (mounted) bolum varsa bildirilir;
   arayuz bunu kullaniciya gosterir.
4. **Listeleme zararsizdir.** `list_disks()` yalnizca isletim sisteminin bilgi
   arayuzlerini okur; **hicbir sektor okunmaz, hicbir yazma yapilmaz.**
   Windows'ta boyut/model icin salt okunur bir aygit tutamaci acilip hemen
   kapatilir (ayrinti: `list_disks` govdesi).
"""
from __future__ import annotations

import os
import re
import struct
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from contextlib import contextmanager

from . import diagnostics
from .image import BlockDevice, DiskImageError
from .platform import IS_LINUX, IS_MACOS, IS_WINDOWS, run_tool
from .platform import mount_partition as pf_mount
from .platform import partition_mount_point as pf_mount_point
from .platform import unmount_partition as pf_unmount
from ..i18n import tr

SECTOR = 512


class PhysicalDiskError(DiskImageError):
    """Fiziksel disk erisim hatasi."""


class AccessDeniedError(PhysicalDiskError):
    """Yetki yetersiz (root / Yonetici gerekli)."""


class SystemDiskError(PhysicalDiskError):
    """Sistem diskine yazma girisimi acikca onaylanmadi."""


class MediaNotReadyError(PhysicalDiskError):
    """Aygit var ama icinde ortam yok (bos kart yuvasi, bos CD surucusu)."""


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
    # {bolum baslangici (bayt): baglama noktasi / surucu harfi}. Disk
    # listelenirken bir kez doldurulur; bolum basina isletim sistemi sorgusu
    # yapmamak icindir (ADR 0021: acik aygita gereksiz dokunulmaz).
    mount_map: Dict[int, str] = field(default_factory=dict)
    partitions: List[str] = field(default_factory=list)
    info_complete: bool = True    # bilgiler eksiksiz okunabildi mi (yetki!)
    os_hint: str = ""             # 'windows' | 'linux' | 'macos' | ''
    in_use: bool = False          # bu aygiti su an uygulamanin kendisi acik tutuyor

    @property
    def os_label(self) -> str:
        return {"windows": "Windows", "linux": "Linux",
                "macos": "macOS"}.get(self.os_hint, "")

    @property
    def display_name(self) -> str:
        name = self.model.strip() or self.name
        return f"{name} ({self.name})"

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
            return tr("ISLETIM SISTEMI DISKI — yazmak makineyi kullanilamaz "
                      "hale getirir")
        if self.mounted:
            return tr("Bagli bolum var ({}) — yazmak veri kaybettirir",
                      ", ".join(self.mounted[:3]))
        if not self.info_complete:
            return tr("Disk bilgileri okunamadi (yetki yok) — sistem diski olup "
                      "olmadigi BILINMIYOR")
        return tr("Bagli bolum yok")

    def summary(self) -> Dict[str, str]:
        from .ptable import human_size
        return {
            tr("Aygit"): self.path,
            tr("Bilgi durumu"): (tr("Eksiksiz") if self.info_complete
                                 else tr("EKSIK (yetki yok)")),
            tr("Model"): self.model or "-",
            tr("Seri no"): self.serial or "-",
            tr("Boyut"): human_size(self.size),
            tr("Sektor boyutu"): tr("{} bayt", self.sector_size),
            tr("Baglanti"): self.bus or "-",
            tr("Cikarilabilir"): tr("Evet") if self.removable else tr("Hayir"),
            tr("Yazma korumali"): tr("Evet") if self.readonly else tr("Hayir"),
            tr("Sistem diski"): (tr("EVET") if self.is_system else
                                 (tr("Hayir") if self.info_complete
                                  else tr("BILINMIYOR"))),
            tr("Isletim sistemi"): self.os_label or "-",
            tr("Bagli bolumler"): (", ".join(self.mounted) if self.mounted
                                   else tr("yok")),
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


# --------------------------------------------------------------------------
# Acik aygit kutugu — "kendi actigin diski yoklama" kurali
# --------------------------------------------------------------------------
# Neden: uygulama bir diski actiginda (ozellikle YAZMA modunda, birimleri
# kilitleyip ayirarak) ayni aygita ikinci bir tutamac acip IOCTL sormak,
# sorgunun surucu yiginin da **dakikalarca** asili kalmasina yol acabiliyor.
# Daha kotusu, o sirada asil is parcaciginin okuma/yazmalari da ayni aygitin
# kuyruguna takilir: uygulama tumden donar.
#
# Olculmus ornek (2026-09-15, Generic- SD/MMC/MS PRO kart okuyucu, yazma modu):
#     win.query_drive(n=1)        64031 ms   <- arka plan yoklamasi
#     disk.read(lba=2165024)      27859 ms   <- ana is parcacigi, ayni aygit
#     disk.read(lba=8456192)      32250 ms
# Ucu de ayni milisaniyede serbest kaldi. Rapor:
#     .claude/logs/freeze/freeze-20260915-134507.md
#
# Cozum: acik aygitlar burada kutuklenir; `list_disks()` bunlara **dokunmaz**,
# son bilinen bilgiyi dondurur.
_open_lock = threading.RLock()
_open_devices: Dict[str, DiskInfo] = {}


def _register_open(info: DiskInfo) -> None:
    with _open_lock:
        _open_devices[info.path] = info
    diagnostics.info(f"aygit acik kutugune eklendi: {info.path}")


def _unregister_open(path: str) -> None:
    with _open_lock:
        _open_devices.pop(path, None)
    diagnostics.info(f"aygit acik kutugunden cikti: {path}")


def locks_volumes_on_write() -> bool:
    """Yazma modunda acarken birimler **kilitlenip ayriliyor mu**?

    Yalnizca Windows'ta evet (`FSCTL_LOCK_VOLUME` + `FSCTL_DISMOUNT_VOLUME`,
    ADR 0016/0022). Linux ve macOS'ta bagli bir bolumun ham sektorlerine
    yazmak isletim sistemi tarafindan engellenmez ve **dosya sistemini
    bozabilir**; kullaniciya bu gercek soylenmelidir.
    """
    return IS_WINDOWS


def open_device_paths() -> Dict[str, DiskInfo]:
    """Uygulamanin su an acik tuttugu aygitlar {yol: bilgi}."""
    with _open_lock:
        return dict(_open_devices)


# --------------------------------------------------------------------------
# Baglama / cikarma (ADR 0043)
# --------------------------------------------------------------------------
# Islemin kendisi `platform.py` icindedir; burada **guvenlik katmani** durur.
# Baglamak zararsizdir, cikarmak degildir: calisan sistemin kokunu ayirmak
# makineyi kullanilamaz hale getirir. Bu yuzden cikarma kritik baglama
# noktalarinda reddedilir.
CRITICAL_MOUNTS = ("/", "/boot", "/boot/efi", "/usr", "/var", "/etc", "/home",
                   "/nix", "/run")


def is_critical_mount(point: str) -> bool:
    """Bu baglama noktasi calisan sistemin isine yariyor mu?"""
    if not point:
        return False
    if IS_WINDOWS:
        system_root = os.environ.get("SystemRoot") or os.environ.get("windir") or "C:\\"
        return point.strip("\\").upper()[:2] == system_root.upper()[:2]
    duz = os.path.normpath(point)
    return duz in CRITICAL_MOUNTS


def partition_mount_point(info: DiskInfo, index: int) -> str:
    """Bolum su an nereye bagli (bagli degilse bos dize)."""
    return pf_mount_point(info.path, index)


def mount_partition(info: DiskInfo, index: int, label: str = "",
                    fs_type: str = "") -> Tuple[bool, str]:
    """Bolumu baglar / surucu harfi atar.

    Baglama **yikici degildir**: kuyruga girmez, dogrudan calisir (ADR 0043).
    Doner: (basarili, baglama noktasi veya hata metni).
    """
    ok, result = pf_mount(info.path, index, label, fs_type)
    diagnostics.info(f"bolum baglandi: {info.path}#{index} -> {result}" if ok
                     else f"bolum baglanamadi: {info.path}#{index}: {result}")
    return ok, result


def unmount_partition(info: DiskInfo, index: int) -> Tuple[bool, str]:
    """Bolumun baglantisini keser / surucu harfini kaldirir.

    Calisan sistemin kullandigi bir bolum **cikarilmaz**: kok dosya sistemini
    ya da `/boot`u ayirmak makineyi aninda kullanilamaz hale getirir.
    """
    point = pf_mount_point(info.path, index)
    if point and is_critical_mount(point):
        return False, tr("Bu bolum calisan sistemin parcasi ({}); "
                         "cikarilamaz.", point)
    ok, error = pf_unmount(info.path, index)
    diagnostics.info(f"bolum cikarildi: {info.path}#{index}" if ok
                     else f"bolum cikarilamadi: {info.path}#{index}: {error}")
    return ok, error


def fill_mount_points(info: Optional[DiskInfo], partitions) -> None:
    """Bolumlere baglama noktasini / surucu harfini yazar.

    Kaynak, disk listelenirken bir kez toplanan `DiskInfo.mount_map`tir;
    bolum basina isletim sistemine sormayiz — Windows'ta her sorgu bir
    aygit tutamaci demektir ve acik diske dokunmak surucu yiginini
    bloklayabilir (ADR 0021).

    macOS'ta harita bos kalir (disk listesi bolum ofseti vermiyor); orada
    bolum basina `diskutil` sorulur. Bu dal **test edilmedi**.
    """
    if info is None:
        return
    table = getattr(info, "mount_map", None) or {}
    for part in partitions or []:
        sector = part.sector_size or getattr(info, "sector_size", SECTOR) or SECTOR
        point = table.get(part.start_lba * sector, "")
        if not point and IS_MACOS:
            try:
                point = pf_mount_point(info.path, part.index)
            except Exception:
                point = ""
        part.mount_point = point


def busy_drive_letters() -> set:
    """Acik disklere ait surucu harfleri (buyuk harf, iki nokta olmadan)."""
    letters = set()
    for info in open_device_paths().values():
        for item in info.mounted:
            head = item.strip().rstrip(":").rstrip("\\")
            if len(head) == 1 and head.isalpha():
                letters.add(head.upper())
    return letters


def list_disks(include_removable: bool = True) -> List[DiskInfo]:
    """Sistemdeki fiziksel diskleri listeler.

    **Hicbir sektor okunmaz, hicbir yazma yapilmaz.** Linux ve macOS'ta yalnizca
    isletim sisteminin sundugu meta veri okunur. Windows'ta boyut/model/veriyolu
    yalnizca aygit tutamaci uzerinden sorgulanabildigi icin her aygit icin
    **salt okunur** bir tutamac acilir (`GENERIC_READ`, paylasimli) ve hemen
    kapatilir; veri okunmaz. Guvenlik katmani 1'in ("listeleme zararsizdir")
    anlami budur.
    """
    with diagnostics.span("physical.list_disks"):
        if IS_LINUX:
            diskler = _list_linux()
        elif IS_WINDOWS:
            diskler = _list_windows()
        elif IS_MACOS:
            diskler = _list_macos()
        else:
            diskler = []
    # Sistem diski, uzerinde su an calisan isletim sistemini barindirir
    acik = open_device_paths()
    for d in diskler:
        if d.is_system and not d.os_hint:
            d.os_hint = running_os()
        if d.path in acik:
            d.in_use = True
    if not include_removable:
        diskler = [d for d in diskler if not d.removable]
    return sorted(diskler, key=lambda d: d.name)


# -- Linux -----------------------------------------------------------------
_LINUX_ATLA = ("loop", "ram", "zram", "dm-", "md", "sr", "fd")


def _read_text(path: str, default: str = "") -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return default


def _linux_mounts() -> Dict[str, str]:
    """{aygit yolu: baglama noktasi}"""
    result: Dict[str, str] = {}
    for satir in _read_text("/proc/mounts").splitlines():
        parcalar = satir.split()
        if len(parcalar) >= 2 and parcalar[0].startswith("/dev/"):
            result[parcalar[0]] = parcalar[1].replace("\\040", " ")
    return result


def _list_linux() -> List[DiskInfo]:
    root = "/sys/block"
    if not os.path.isdir(root):
        return []
    baglantilar = _linux_mounts()
    system_device = ""
    for aygit, nokta in baglantilar.items():
        if nokta == "/":
            system_device = aygit
            break

    diskler: List[DiskInfo] = []
    for name in sorted(os.listdir(root)):
        if name.startswith(_LINUX_ATLA):
            continue
        taban = os.path.join(root, name)
        sector_count = int(_read_text(os.path.join(taban, "size"), "0") or 0)
        if sector_count <= 0:
            continue
        mantiksal = int(_read_text(os.path.join(taban, "queue/logical_block_size"), "512") or 512)
        info = DiskInfo(
            path=f"/dev/{name}",
            name=name,
            size=sector_count * 512,      # /sys/block/*/size her zaman 512 birimlidir
            sector_size=mantiksal,
            model=_read_text(os.path.join(taban, "device/model")) or _read_text(
                os.path.join(taban, "device/name")),
            serial=_read_text(os.path.join(taban, "device/serial")),
            removable=_read_text(os.path.join(taban, "removable"), "0") == "1",
            readonly=_read_text(os.path.join(taban, "ro"), "0") == "1",
        )
        if name.startswith("nvme"):
            info.bus = "NVMe"
        elif os.path.exists(os.path.join(taban, "device/vendor")):
            info.bus = _read_text(os.path.join(taban, "device/vendor")) or "SCSI/SATA"
        if "usb" in os.path.realpath(taban):
            info.bus = "USB"

        # bolumler ve baglama durumu
        for data in sorted(os.listdir(taban)):
            if data.startswith(name) and os.path.isdir(os.path.join(taban, data)):
                part = f"/dev/{data}"
                info.partitions.append(part)
                if part in baglantilar:
                    info.mounted.append(f"{data} → {baglantilar[part]}")
                    # Bolumun diskteki yeri: `/sys` bunu HER ZAMAN 512
                    # baytlik birimle verir (sektor boyutu 4K olsa da).
                    start = int(_read_text(
                        os.path.join(taban, data, "start"), "0") or 0)
                    info.mount_map[start * 512] = baglantilar[part]
                if system_device and part == system_device:
                    info.is_system = True
        if system_device and system_device.startswith(f"/dev/{name}"):
            info.is_system = True
        diskler.append(info)
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


SEM_FAILCRITICALERRORS = 0x0001
SEM_NOOPENFILEERRORBOX = 0x8000
DRIVE_NO_ROOT_DIR = 1
DRIVE_REMOTE = 4
DRIVE_CDROM = 5
ERROR_NOT_READY = 21


@contextmanager
def _win_quiet_errors():
    """Aygit yoklarken Windows'un "diskte ortam yok" kutusunu bastirir.

    Bos kart okuyucu yuvasi veya ortamsiz CD surucusu uzerinde `CreateFileW`
    varsayilan olarak kullaniciya kutu gosterir ve **cagriyi saniyelerce
    bloklar**; bu, arayuzun donmasinin baslica nedenidir. `SetThreadErrorMode`
    ile cagri kutu acmadan `ERROR_NOT_READY` ile hemen doner.

    Yalnizca **cagiran is parcacigini** etkiler (`SetErrorMode` degil), boylece
    uygulamanin geri kalani etkilenmez.
    """
    if not IS_WINDOWS:
        yield
        return
    import ctypes
    import ctypes.wintypes as wt

    k32 = ctypes.windll.kernel32
    previous = wt.DWORD(0)
    changed = False
    try:
        k32.SetThreadErrorMode.argtypes = [wt.DWORD, ctypes.POINTER(wt.DWORD)]
        k32.SetThreadErrorMode.restype = wt.BOOL
        changed = bool(k32.SetThreadErrorMode(
            SEM_FAILCRITICALERRORS | SEM_NOOPENFILEERRORBOX,
            ctypes.byref(previous)))
    except (AttributeError, OSError):
        changed = False
    try:
        yield
    finally:
        if changed:
            try:
                k32.SetThreadErrorMode(previous, None)
            except OSError:
                pass


def _win_drive_type(letter: str) -> int:
    """`GetDriveTypeW` — aygit acmadan surucu turunu soyler (bloklamaz)."""
    import ctypes
    import ctypes.wintypes as wt

    k32 = ctypes.windll.kernel32
    k32.GetDriveTypeW.argtypes = [wt.LPCWSTR]
    k32.GetDriveTypeW.restype = wt.UINT
    return int(k32.GetDriveTypeW(f"{letter}:\\"))


def _win_handle(path: str, write: bool = False):
    """Windows aygit tutamaci acar. Hata durumunda istisna firlatir."""
    import ctypes
    import ctypes.wintypes as wt

    k32 = ctypes.windll.kernel32
    k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID,
                                wt.DWORD, wt.DWORD, wt.HANDLE]
    k32.CreateFileW.restype = wt.HANDLE
    erisim = GENERIC_READ | (GENERIC_WRITE if write else 0)
    # Ortamsiz yuvalarda kutu acilip cagri bloklanmasin diye sessiz kip.
    with _win_quiet_errors():
        handle = k32.CreateFileW(path, erisim,
                                  FILE_SHARE_READ | FILE_SHARE_WRITE,
                                  None, OPEN_EXISTING, 0, None)
    if handle in (INVALID_HANDLE, 0, None) or handle == ctypes.c_void_p(-1).value:
        error = ctypes.get_last_error() or k32.GetLastError()
        if error == ERROR_ACCESS_DENIED:
            raise AccessDeniedError(
                tr("{} acilamadi: Yonetici yetkisi gerekiyor (uygulamayi "
                   "'Yonetici olarak calistir' ile baslatin)", path))
        if error == ERROR_NOT_READY:
            raise MediaNotReadyError(tr("{}: aygitta ortam yok", path))
        raise PhysicalDiskError(tr("{} acilamadi (Windows hatasi {})", path, error))
    return handle


def _win_ioctl(handle, code: int, data: bytes = b"", out_size: int = 256):
    import ctypes
    import ctypes.wintypes as wt

    k32 = ctypes.windll.kernel32
    k32.DeviceIoControl.argtypes = [wt.HANDLE, wt.DWORD, wt.LPVOID, wt.DWORD,
                                    wt.LPVOID, wt.DWORD,
                                    ctypes.POINTER(wt.DWORD), wt.LPVOID]
    k32.DeviceIoControl.restype = wt.BOOL
    tampon = ctypes.create_string_buffer(out_size)
    donen = wt.DWORD(0)
    entry_buffer = ctypes.create_string_buffer(data) if data else None
    ok = k32.DeviceIoControl(wt.HANDLE(handle), code,
                             entry_buffer, len(data),
                             tampon, out_size, ctypes.byref(donen), None)
    if not ok:
        return None
    return tampon.raw[:donen.value]


def _win_device_info(handle) -> Dict[str, str]:
    """IOCTL_STORAGE_QUERY_PROPERTY ile model/seri/veriyolu okur."""
    # STORAGE_PROPERTY_QUERY: PropertyId=StorageDeviceProperty(0), QueryType=Standard(0)
    sorgu = struct.pack("<II", 0, 0) + b"\x00" * 8
    ham = _win_ioctl(handle, IOCTL_STORAGE_QUERY_PROPERTY, sorgu, 1024)
    if not ham or len(ham) < 40:
        return {}
    (_ver, _size, _device_kind, _degistirici, cikarilabilir, _komut_kuyrugu,
     saticiid_ofset, urunid_ofset, urun_surum_ofset, seri_ofset,
     bus_kind) = struct.unpack_from("<IIBBBBIIIII", ham, 0)

    def metin(ofset: int) -> str:
        if not ofset or ofset >= len(ham):
            return ""
        last = ham.find(b"\x00", ofset)
        return ham[ofset:last if last > 0 else len(ham)].decode("latin-1", "ignore").strip()

    veriyollari = {1: "SCSI", 2: "ATAPI", 3: "ATA", 4: "1394", 5: "SSA", 6: "Fibre",
                   7: "USB", 8: "RAID", 9: "iSCSI", 10: "SAS", 11: "SATA",
                   12: "SD", 13: "MMC", 17: "NVMe"}
    return {
        "model": (metin(saticiid_ofset) + " " + metin(urunid_ofset)).strip(),
        "serial": metin(seri_ofset),
        "bus": veriyollari.get(bus_kind, ""),
        "removable": "1" if cikarilabilir else "0",
    }


def _win_system_disk_numbers() -> List[int]:
    """Windows dizininin bulundugu surucunun disk numaralarini dondurur."""
    import ctypes

    windir = os.environ.get("SystemDrive", "C:")
    try:
        handle = _win_handle(f"\\\\.\\{windir.rstrip(chr(92))}", write=False)
    except PhysicalDiskError:
        return []
    try:
        ham = _win_ioctl(handle, IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS, b"", 1024)
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
        _win_close(handle)


def _win_close(handle) -> None:
    try:
        import ctypes
        ctypes.windll.kernel32.CloseHandle(ctypes.wintypes.HANDLE(handle))
    except Exception:
        pass


def _win_volume_extents() -> Tuple[Dict[int, List[str]], Dict[Tuple[int, int], str]]:
    """{disk numarasi: [surucu harfleri]} — bagli bolumleri gostermek icin.

    Her harf icin aygit tutamaci acilir. Ag surucusu ve CD/DVD **acilmadan**
    elenir: ag surucusunde tutamac acmak sunucu yanit vermiyorsa saniyelerce
    asili kalir, bos CD surucusu ise "ortam yok" diyalogunu tetiklerdi. Kalan
    harfler `_win_quiet_errors()` altinda acilir, boylece bos kart yuvasi
    bloklamak yerine hemen hata dondurur.
    """
    import ctypes

    result: Dict[int, List[str]] = {}
    extents: Dict[Tuple[int, int], str] = {}
    busy = busy_drive_letters()
    maske = ctypes.windll.kernel32.GetLogicalDrives()
    for i in range(26):
        if not (maske >> i) & 1:
            continue
        harf = chr(ord("A") + i)
        # Acik bir diske ait birim: tutamac acmak ya reddedilir (kilitli) ya da
        # aygit kuyruguna takilir. Harf zaten bilindigi icin sorulmaz.
        if harf in busy:
            diagnostics.debug(f"surucu {harf}: uygulamada acik; yoklanmadi")
            continue
        kind = _win_drive_type(harf)
        if kind in (DRIVE_NO_ROOT_DIR, DRIVE_REMOTE, DRIVE_CDROM):
            diagnostics.debug(f"surucu {harf}: atlandi (tur {kind})")
            continue
        try:
            with diagnostics.span("win.open_volume", warn_on_error=False, drive=harf):
                handle = _win_handle(f"\\\\.\\{harf}:", write=False)
        except PhysicalDiskError:
            continue
        try:
            ham = _win_ioctl(handle, IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS, b"", 1024)
            if ham and len(ham) >= 12:
                adet = struct.unpack_from("<I", ham, 0)[0]
                for n in range(min(adet, 16)):
                    ofset = 8 + n * 24
                    if ofset + 4 <= len(ham):
                        disk_no = struct.unpack_from("<I", ham, ofset)[0]
                        result.setdefault(disk_no, []).append(f"{harf}:")
                        # DISK_EXTENT: DiskNumber(4) + dolgu(4) +
                        # StartingOffset(8) + ExtentLength(8). Baslangic
                        # ofseti harfi **bolume** baglar; ayni cagriyi
                        # zaten yapiyoruz, ek maliyeti yok.
                        if ofset + 16 <= len(ham):
                            offset_bytes = struct.unpack_from("<q", ham,
                                                              ofset + 8)[0]
                            extents[(disk_no, offset_bytes)] = f"{harf}:"
        finally:
            _win_close(handle)
    return result, extents


def _win_drive_letters() -> Dict[int, List[str]]:
    """{disk numarasi: [surucu harfleri]} — eski cagiranlar icin."""
    return _win_volume_extents()[0]


def _list_windows() -> List[DiskInfo]:
    diskler: List[DiskInfo] = []
    acik = open_device_paths()
    try:
        system_numbers = set(_win_system_disk_numbers())
        harfler, ofsetler = _win_volume_extents()
    except Exception:
        system_numbers, harfler = set(), {}

    for numara in range(32):
        path = f"\\\\.\\PhysicalDrive{numara}"
        # Uygulamanin kendi acik tuttugu aygita DOKUNULMAZ: ikinci bir tutamac
        # acip IOCTL sormak surucu yiginini dakikalarca bloklayabiliyor ve o
        # sirada asil is parcaciginin okumalari da ayni kuyruga takiliyor
        # (bkz. dosyanin basindaki "Acik aygit kutugu" aciklamasi).
        if path in acik:
            info = acik[path]
            info.in_use = True
            diskler.append(info)
            diagnostics.debug(f"{path} uygulamada acik; yoklanmadi")
            continue
        try:
            with diagnostics.span("win.open_drive", warn_on_error=False, n=numara):
                handle = _win_handle(path, write=False)
        except AccessDeniedError:
            # Disk var ama yetki yok: yine de listede goster
            diskler.append(DiskInfo(path=path, name=f"PhysicalDrive{numara}",
                                    model="(yetki yok — Yonetici gerekli)",
                                    is_system=numara in system_numbers,
                                    mounted=harfler.get(numara, []),
                                    info_complete=False))
            continue
        except PhysicalDiskError:
            continue
        try:
            info = DiskInfo(path=path, name=f"PhysicalDrive{numara}")
            with diagnostics.span("win.query_drive", n=numara):
                ham = _win_ioctl(handle, IOCTL_DISK_GET_LENGTH_INFO, b"", 8)
                if ham and len(ham) >= 8:
                    info.size = struct.unpack("<q", ham[:8])[0]
                geo = _win_ioctl(handle, IOCTL_DISK_GET_DRIVE_GEOMETRY, b"", 24)
                if geo and len(geo) >= 24:
                    info.sector_size = struct.unpack_from("<I", geo, 20)[0] or SECTOR
                ayrinti = _win_device_info(handle)
            info.model = ayrinti.get("model", "")
            info.serial = ayrinti.get("serial", "")
            info.bus = ayrinti.get("bus", "")
            info.removable = ayrinti.get("removable") == "1"
            info.is_system = numara in system_numbers
            info.mounted = harfler.get(numara, [])
            info.mount_map = {off: harf for (disk_no, off), harf
                              in ofsetler.items() if disk_no == numara}
            if info.size > 0:
                diskler.append(info)
        finally:
            _win_close(handle)
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
    for name in veri.get("WholeDisks", []):
        info = DiskInfo(path=f"/dev/{name}", name=name)
        try:
            ayrinti = run_tool(["diskutil", "info", "-plist", name], timeout=20)
            import plistlib
            d = plistlib.loads(ayrinti.stdout.encode("utf-8", "replace"))
            info.size = int(d.get("TotalSize", 0))
            info.sector_size = int(d.get("DeviceBlockSize", SECTOR))
            info.model = str(d.get("MediaName", ""))
            info.removable = bool(d.get("Removable", False))
            info.readonly = not bool(d.get("WritableMedia", True))
            info.is_system = bool(d.get("SystemImage", False)) or name == "disk0"
            nokta = d.get("MountPoint")
            if nokta:
                info.mounted.append(str(nokta))
        except Exception:
            pass
        if info.size > 0:
            diskler.append(info)
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
        info = info_or_path if isinstance(info_or_path, DiskInfo) else find_disk(info_or_path)
        if info is None:
            path = info_or_path if isinstance(info_or_path, str) else ""
            raise PhysicalDiskError(tr("Disk bulunamadi: {}", path))
        self.info = info
        self.path = info.path
        self.sector_size = info.sector_size or SECTOR

        if not readonly:
            if not confirm:
                raise PhysicalDiskError(
                    tr("Yazma modu acikca onaylanmalidir (confirm=True)"))
            if info.readonly:
                raise PhysicalDiskError(tr("Aygit donanimsal olarak yazma korumali"))
            if not info.info_complete and not allow_system:
                raise SystemDiskError(
                    tr("{} bilgileri okunamadi (yetki yok); sistem diski olup "
                       "olmadigi bilinmiyor. Bilinmeyen bir diske yazma "
                       "reddedildi.", info.path))
            if info.is_system and not allow_system:
                raise SystemDiskError(
                    tr("{} isletim sistemi diskidir. Yazma islemi makineyi "
                       "acilamaz hale getirebilir; bu diske yazmak icin "
                       "ayrica onay gerekir.", info.path))
        self.readonly = readonly
        self._size = info.size
        self._fh = None
        self._win_handle = None
        self._volume_handles: List[object] = []
        self._locked_letters: List[str] = []
        self._unlocked_letters: List[str] = []
        self._open_device()
        # Aygit acildigi andan kapanana kadar kutukte durur; listeleme ona
        # dokunmaz. Kayit acmadan SONRA yapilir: acma basarisiz olursa kutukte
        # olmayan bir aygit kalmaz.
        info.in_use = True
        _register_open(info)
        try:
            if IS_WINDOWS and not readonly:
                self._win_lock_volumes()
        except Exception:
            _unregister_open(self.path)
            raise

    # -- acma/kapatma ------------------------------------------------------
    @diagnostics.timed("disk.open_device")
    def _open_device(self) -> None:
        if IS_WINDOWS:
            self._win_handle = _win_handle(self.path, write=not self.readonly)
            return
        mod = os.O_RDONLY if self.readonly else os.O_RDWR
        try:
            fd = os.open(self.path, mod)
        except PermissionError:
            raise AccessDeniedError(
                tr("{} acilamadi: yetki yetersiz. Uygulamayi 'sudo' ile "
                   "calistirin veya kullaniciyi 'disk' grubuna ekleyin.", self.path))
        except FileNotFoundError:
            raise PhysicalDiskError(tr("Aygit yok: {}", self.path))
        except OSError as exc:
            raise PhysicalDiskError(tr("{} acilamadi: {}", self.path, exc))
        self._fh = os.fdopen(fd, "rb" if self.readonly else "r+b", buffering=0)
        if self._size <= 0:
            self._size = self._fh.seek(0, os.SEEK_END)

    @diagnostics.timed("disk.lock_volumes")
    def _win_lock_volumes(self) -> None:
        """Diskteki birimleri kilitler ve baglantisini keser (Windows).

        Windows, **bagli** bir birimin sektorlerine dogrudan yazmayi reddeder
        (ERROR_ACCESS_DENIED). Bicimlendirme araclari bu yuzden once birimi
        kilitler (`FSCTL_LOCK_VOLUME`) ve baglantisini keser
        (`FSCTL_DISMOUNT_VOLUME`). Kilit, tutamac acik kaldigi surece gecerlidir
        ve baska bir surecin birimi yeniden baglamasini engeller.

        Birim harfleri **listelemede zaten ogrenilmistir** (`info.mounted`);
        burada yeniden sorulmaz. Sorulsaydi acik aygit kutugune takilirdi: aygit
        bu noktada kutuge girmis olur, `_win_drive_letters()` de kutuktekilerin
        harflerini atlar — sonuc olarak hicbir birim kilitlenmez ve **sonraki
        her yazma** "Windows bagli birimlere yazmayi engeller" hatasi verir.
        Yasandi (2026-09-15): dosya silme reddedildi, kullanici yonetici
        oldugu halde "Yonetici olarak calistirin" mesajini gordu.

        Basarisizlik olumcul degildir ama **sessiz de degildir**: hangi birimin
        kilitlendigi gunluge yazilir, cunku kilitlenemeyen birim sonraki yazma
        hatalarinin nedenidir.
        """
        letters = [item for item in (self.info.mounted or []) if item]
        if not letters:
            # Bilgi yoksa (orn. dogrudan yol ile acilmissa) son care: sor.
            numaralar = "".join(ch for ch in self.info.name if ch.isdigit())
            if not numaralar:
                return
            try:
                letters = _win_drive_letters().get(int(numaralar), [])
            except Exception:
                letters = []
        if not letters:
            diagnostics.info(f"{self.path}: bagli birim yok, kilit gerekmiyor")
            return
        locked, failed = [], []
        for harf in letters:
            try:
                handle = _win_handle(f"\\\\.\\{harf.rstrip(chr(92))}", write=True)
            except PhysicalDiskError as exc:
                failed.append(tr("{} (acilamadi: {})", harf, exc))
                continue
            kilitlendi = _win_ioctl(handle, FSCTL_LOCK_VOLUME, b"", 0) is not None
            _win_ioctl(handle, FSCTL_DISMOUNT_VOLUME, b"", 0)
            if kilitlendi:
                self._volume_handles.append(handle)
                locked.append(harf)
            else:
                # kilitlenemedi: tutamaci birak, birim kullanimda olabilir
                _win_close(handle)
                failed.append(tr("{} (kilitlenemedi — birim kullanimda)", harf))
        self._locked_letters = locked
        self._unlocked_letters = failed
        diagnostics.info(f"{self.path}: kilitlenen birim {locked or 'yok'}"
                         + (f", kilitlenemeyen {failed}" if failed else ""))
        if failed:
            diagnostics.warn(f"{self.path}: kilitlenemeyen birim var {failed}; "
                             "bu birimin sektorlerine yazma reddedilebilir")

    def _win_release_volumes(self) -> None:
        """Kilitli birim tutamaclarini birakir."""
        for handle in self._volume_handles:
            try:
                _win_ioctl(handle, FSCTL_UNLOCK_VOLUME, b"", 0)
            except Exception:
                pass
            _win_close(handle)
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
        # Aygit serbest: listeleme artik yeniden yoklayabilir.
        self.info.in_use = False
        _unregister_open(self.path)

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
            raise PhysicalDiskError(tr("Okuma disk sinirini asiyor"))
        # Aygit okumasi arayuzun takilabilecegi yerlerden biridir (yavas USB,
        # uyuyan disk). `track=False`: her okuma gecmise yazilmaz, ama okuma
        # suruyorken donma olursa raporda bu satir gorunur.
        with diagnostics.span("disk.read", track=False,
                              lba=offset // self.sector_size, size=length):
            if IS_WINDOWS:
                return self._win_read(offset, length)
            self._fh.seek(offset)
            veri = self._fh.read(length)
            return veri + b"\x00" * (length - len(veri))

    def write(self, offset: int, data: bytes) -> None:
        if self.readonly:
            raise PhysicalDiskError(tr("Disk salt okunur acildi"))
        if offset + len(data) > self._size:
            raise PhysicalDiskError(tr("Yazma disk sinirini asiyor"))
        with diagnostics.span("disk.write", track=False,
                              lba=offset // self.sector_size, size=len(data)):
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
            result = _win_ioctl(self._win_handle, IOCTL_DISK_UPDATE_PROPERTIES,
                               b"", 0)
            return result is not None
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
                result = run_tool(["diskutil", "rescan", self.path], timeout=30)
                return result.returncode == 0
            except Exception:
                return False
        return False

    # -- Windows: aygit G/C sektor hizali olmak zorundadir -----------------
    def _win_read(self, offset: int, length: int) -> bytes:
        import ctypes
        import ctypes.wintypes as wt

        ss = self.sector_size
        bas = (offset // ss) * ss
        last = ((offset + length + ss - 1) // ss) * ss
        k32 = ctypes.windll.kernel32
        k32.SetFilePointerEx.argtypes = [wt.HANDLE, ctypes.c_longlong,
                                         ctypes.POINTER(ctypes.c_longlong), wt.DWORD]
        k32.SetFilePointerEx.restype = wt.BOOL
        k32.ReadFile.argtypes = [wt.HANDLE, wt.LPVOID, wt.DWORD,
                                 ctypes.POINTER(wt.DWORD), wt.LPVOID]
        k32.ReadFile.restype = wt.BOOL
        if not k32.SetFilePointerEx(wt.HANDLE(self._win_handle),
                                    ctypes.c_longlong(bas), None, 0):
            raise PhysicalDiskError(tr("Disk konumlandirilamadi"))
        tampon = ctypes.create_string_buffer(last - bas)
        okunan = wt.DWORD(0)
        if not k32.ReadFile(wt.HANDLE(self._win_handle), tampon, last - bas,
                            ctypes.byref(okunan), None):
            raise PhysicalDiskError(tr("Okuma hatasi (Windows {})", k32.GetLastError()))
        ham = tampon.raw[:okunan.value]
        ic = offset - bas
        return ham[ic:ic + length].ljust(length, b"\x00")

    def _win_write(self, offset: int, data: bytes) -> None:
        import ctypes
        import ctypes.wintypes as wt

        ss = self.sector_size
        bas = (offset // ss) * ss
        last = ((offset + len(data) + ss - 1) // ss) * ss
        # hizalama disinda kalan kenarlari korumak icin once oku-degistir-yaz
        mevcut = bytearray(self._win_read(bas, last - bas))
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
            raise PhysicalDiskError(tr("Disk konumlandirilamadi"))
        tampon = ctypes.create_string_buffer(bytes(mevcut))
        yazilan = wt.DWORD(0)
        if not k32.WriteFile(wt.HANDLE(self._win_handle), tampon, len(mevcut),
                             ctypes.byref(yazilan), None):
            error = k32.GetLastError()
            if error == ERROR_ACCESS_DENIED:
                raise AccessDeniedError(self._write_denied_text(bas))
            raise PhysicalDiskError(tr("Yazma hatasi (Windows {})", error))

    def _write_denied_text(self, offset: int) -> str:
        """ERROR_ACCESS_DENIED icin **gercek** nedeni soyleyen metin.

        Eski metin her durumda "Yonetici olarak calistirin" diyordu. Kullanici
        zaten yonetici oldugunda bu yanlis yonlendiriyordu: asil neden birimin
        kilitlenememis olmasidir (2026-09-15, ADR 0022).
        """
        lba = offset // self.sector_size
        base = (f"Yazma reddedildi (LBA {lba}): Windows **bagli** bir birimin "
                "sektorlerine dogrudan yazmayi engeller.")
        if self._unlocked_letters:
            return (tr("{}\n\nSu birim(ler) kilitlenemedi: {}\nBirimi "
                       "kullanan programlari (Gezgin penceresi, virus "
                       "tarayici, yedekleme) kapatip diski yeniden acin; ya "
                       "da birimi Windows'tan cikarin (eject).",
                       base, ', '.join(self._unlocked_letters)))
        if not self._volume_handles:
            return (tr("{}\n\nBu diskte hicbir birim kilitlenemedi. Diski "
                       "kapatip yeniden yazma modunda acin; sorun surerse "
                       "birimi Windows'tan cikarin (eject).", base))
        return (tr("{}\n\nKilitli birimler: {}. Yazilan alan bu birimlerin "
                   "disinda, baska bir bagli birime ait olabilir.",
                   base, ', '.join(self._locked_letters)))

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
