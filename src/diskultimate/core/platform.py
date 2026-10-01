"""Platforma bagli islemler tek yerde toplanir (Windows / macOS / Linux).

Cekirdegin geri kalani isletim sistemi ayrimi yapmaz; burayi cagirir.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from typing import Dict, List, Optional, Tuple

from ..i18n import tr

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
    for name in adaylar:
        path = shutil.which(name)
        if path:
            return path
        for directory in _EK_ARAMA_YOLLARI:     # sbin cogu dagitimda PATH'te degil
            tam = os.path.join(directory, name)
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
# Yonetici / root yetkisi
# --------------------------------------------------------------------------
# Goruntu dosyalari icin yetki GEREKMEZ (CLAUDE.md). Yukseltme yalnizca
# **fiziksel disk** erisimi icin, kullanicinin acik istegiyle yapilir; uygulama
# kendiliginden ve her acilista yetki istemez.
ELEVATION_NAME = "Yonetici" if IS_WINDOWS else "root"


def is_elevated() -> bool:
    """Uygulama yonetici/root yetkisiyle mi calisiyor?"""
    if IS_WINDOWS:
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    try:
        return os.geteuid() == 0
    except AttributeError:          # bu platformda kavram yok
        return False


def set_app_user_model_id(app_id: str) -> bool:
    """Windows: gorev cubugu bu kimlige gore gruplar ve ikonu ondan alir.

    Kaynaktan calistirildiginda (`python main.py`) Windows pencereyi
    yorumlayiciya ait sayar ve **Python'un** ikonunu gosterir; kimlik
    atandiginda uygulamanin kendi ikonu gorunur. Paketlenmis exe'de kabuk
    ikonu zaten dosyadan gelir, orada da zararsizdir.

    Basarili olduysa True doner. Windows disinda hicbir sey yapmaz: kimlik
    kavrami oraya ozgudur.
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        # Eski Windows surumleri bu cagriyi tanimayabilir. Ikon bir kolayliktir,
        # yokluğu uygulamayi calistirmamak icin sebep degildir.
        return False
    return True


def _relaunch_target() -> List[str]:
    """Uygulamayi yeniden baslatacak komut satiri.

    Donmus (PyInstaller vb.) pakette calistirilabilir dosyanin kendisi,
    kaynaktan calistirildiginda yorumlayici + betik kullanilir.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable] + list(sys.argv[1:])
    script = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
    if not os.path.isfile(script):
        # `python -c ...` gibi durumlarda betik yolu yoktur; ciplak yorumlayiciyi
        # yonetici olarak baslatmak anlamsiz olurdu.
        return []
    return [sys.executable, script] + list(sys.argv[1:])


def elevation_available() -> Tuple[bool, str]:
    """(Yukseltme yapilabilir mi, yapilamiyorsa neden).

    Arayuz bunu menuyu etkin/pasif yapmak ve nedeni gostermek icin sorar;
    calismayacak bir dugmeyi etkin gostermek kullaniciyi yaniltir.
    """
    if is_elevated():
        return False, tr("Uygulama zaten {} yetkisiyle calisiyor.", elevation_name())
    if not _relaunch_target():
        return False, (tr("Uygulamanin yeniden baslatilacagi betik yolu "
                       "belirlenemedi."))
    if IS_WINDOWS:
        return True, ""
    if IS_LINUX:
        if shutil.which("pkexec"):
            return True, ""
        return False, (tr("Grafik yetki penceresi icin `pkexec` gerekiyor "
                       "(polkit paketi). Uygulamayi `sudo python3 main.py` ile "
                       "baslatabilirsiniz."))
    if IS_MACOS:
        return bool(shutil.which("osascript")), (
            "" if shutil.which("osascript") else tr("`osascript` bulunamadi."))
    return False, tr("Bu platformda yetki yukseltme desteklenmiyor.")


# Yetkili kopya "acildim" demek icin bu ortam degiskenindeki dosyayi yazar.
HANDOFF_ENV = "DISKULTIMATE_HANDOFF"

# pkexec cikis kodlari: 126 = yetki verilmedi/iptal, 127 = yetkilendirme hatasi.
PKEXEC_DISMISSED = 126
PKEXEC_ERROR = 127


class ElevatedLaunch:
    """Baslatilan yetkili kopyanin durumu.

    Eski kopya, yenisi "acildim" diyene kadar **yasamak zorundadir**:
    `pkexec` yetkilendirmeyi kendi **ebeveynine** bakarak yapar (polkit oznesi
    = `getppid()`). Baslatir baslatmaz kapanan bir ebeveynde pkexec ya
    "Refusing to render service to dead parents" der ya da ozne yanlis
    cozuldugu icin parola penceresi hic acilmadan sonsuza dek bekler.
    Kullanicinin gordugu sey, uygulamanin kapanip bir daha acilmamasidir
    (ADR 0039).

    `poll()` uc durumdan birini doner; arayuz bunu birkac yuz ms'de bir sorar
    ve yalnizca STARTED gorunce kendini kapatir.
    """

    WAITING = "bekliyor"
    STARTED = "basladi"
    FAILED = "hata"

    def __init__(self, process=None, handoff: str = "", log: str = "") -> None:
        self._process = process
        self._handoff = handoff
        self._log = log

    def poll(self) -> Tuple[str, str]:
        """(durum, hata_metni) — durum STARTED/WAITING iken metin bostur."""
        # Windows/macOS: yetki penceresini isletim sistemi kendi yonetir ve
        # sonucu hemen bildirir; beklenecek bir sey yoktur.
        if self._process is None:
            return self.STARTED, ""
        if self._handoff and os.path.exists(self._handoff):
            return self.STARTED, ""
        code = self._process.poll()
        if code is None:
            return self.WAITING, ""
        return self.FAILED, self._failure(int(code))

    def _failure(self, code: int) -> str:
        """Cikis kodunu kullanicinin anlayacagi cumleye cevirir."""
        if code == PKEXEC_DISMISSED:
            return tr("Yetki verilmedi (parola penceresi iptal edildi).")
        if code == PKEXEC_ERROR:
            text = tr("Yetkilendirme reddedildi (polkit).")
        else:
            text = tr("Yetkili kopya baslatilamadi (cikis kodu {}).", code)
        detail = self._log_tail()
        return f"{text}\n\n{detail}" if detail else text

    def _log_tail(self, lines: int = 6) -> str:
        """Yetkili kopyanin hata ciktisinin sonu (yoksa bos dize).

        Cikti dosyaya alinir, boruya degil: yetkili kopya saatlerce calisir ve
        dolan bir boru onu kilitlerdi.
        """
        try:
            with open(self._log, "r", encoding="utf-8", errors="replace") as fh:
                tail = [satir.rstrip() for satir in fh if satir.strip()]
        except OSError:
            return ""
        return "\n".join(tail[-lines:])

    def cleanup(self) -> None:
        """Bekleme bitince gecici dosyalari toplar (hata olumcul degildir)."""
        for path in (self._handoff, self._log):
            try:
                if path and os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass


def relaunch_elevated() -> Tuple[Optional["ElevatedLaunch"], str]:
    """Uygulamayi yonetici/root olarak yeniden baslatir.

    Doner: (baslatma tutamaci, hata_metni). Tutamac None ise hicbir sey
    baslatilmadi. Tutamac dondugunde cagiran surec **hemen kapanmaz**;
    `poll()` STARTED diyene kadar bekler, sonra kendini kapatir: iki kopya
    ayni diske dokunmamalidir (bkz. `physical.py` acik aygit kutugu).
    """
    ok, reason = elevation_available()
    if not ok:
        return None, reason
    command = _relaunch_target()
    try:
        if IS_WINDOWS:
            started, error = _win_relaunch(command)
            return (ElevatedLaunch() if started else None), error
        if IS_MACOS:
            script = ("do shell script "
                      + _osascript_quote(subprocess.list2cmdline(command))
                      + " with administrator privileges")
            subprocess.Popen(["osascript", "-e", script])
            return ElevatedLaunch(), ""
        return _linux_relaunch(command), ""
    except Exception as exc:
        return None, str(exc)


def _linux_relaunch(command: List[str]) -> "ElevatedLaunch":
    """pkexec ile yetkili kopyayi baslatir.

    pkexec ortami temizler; grafik oturum degiskenleri elle tasinir, yoksa
    yetkili kopya ekrani bulamaz ve sessizce olur. Gunluk dizini de tasinir:
    aksi halde root kopyasinin tanilama gunlugu kendi ev dizinine dusar ve
    olan biten gorunmez.
    """
    from ..paths import log_root, scratch

    folder = scratch("elevate")
    stamp = f"{os.getpid()}-{int(time.time())}"
    handoff = os.path.join(folder, f"ready-{stamp}")
    log = os.path.join(folder, f"stderr-{stamp}.log")

    passthrough = {k: os.environ[k]
                   for k in ("DISPLAY", "XAUTHORITY", "QT_QPA_PLATFORM",
                             "DISKULTIMATE_QPA", "DISKULTIMATE_LANG",
                             "DISKULTIMATE_THEME", "DISKULTIMATE_DIAG",
                             "DISKULTIMATE_DIAG_VERBOSE", "XDG_RUNTIME_DIR")
                   if os.environ.get(k)}
    passthrough[HANDOFF_ENV] = handoff
    passthrough["DISKULTIMATE_LOG_DIR"] = (os.environ.get("DISKULTIMATE_LOG_DIR")
                                           or log_root())
    env_args = [f"{k}={v}" for k, v in passthrough.items()]

    stream = open(log, "wb")
    try:
        process = subprocess.Popen(["pkexec", "env"] + env_args + command,
                                   stdout=stream, stderr=stream)
    finally:
        stream.close()          # tutamac cocukta acik kalir
    return ElevatedLaunch(process, handoff, log)


def signal_elevated_ready() -> str:
    """Yetkili kopya, acildigini kendisini baslatan kopyaya bildirir.

    Eski kopya bu dosyayi gorene kadar ayakta bekler (bkz. `ElevatedLaunch`).
    Bildirim yazilamazsa eski kopya beklemeye devam eder ama kullanici yetkili
    pencereyi yine de gorur; bu yuzden hata olumcul sayilmaz.

    Ortam degiskeni **okunurken silinir**: bu kopyanin baslatacagi surecler
    bildirimi devralmamalidir.

    Doner: yazilan dosya yolu (bildirim istenmemisse bos dize).
    """
    path = os.environ.pop(HANDOFF_ENV, "")
    if not path:
        return ""
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
    except OSError:
        return ""
    return path


def _osascript_quote(text: str) -> str:
    """AppleScript dizesi olarak alintilar."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _win_relaunch(command: List[str]) -> Tuple[bool, str]:
    """ShellExecuteW "runas" — Windows'un UAC penceresini acar."""
    import ctypes
    import ctypes.wintypes as wt

    shell32 = ctypes.windll.shell32
    shell32.ShellExecuteW.argtypes = [wt.HWND, wt.LPCWSTR, wt.LPCWSTR,
                                      wt.LPCWSTR, wt.LPCWSTR, ctypes.c_int]
    shell32.ShellExecuteW.restype = ctypes.c_void_p
    SW_SHOWNORMAL = 1
    result = shell32.ShellExecuteW(
        None, "runas", command[0],
        subprocess.list2cmdline(command[1:]), os.getcwd(), SW_SHOWNORMAL)
    code = int(result or 0)
    if code > 32:
        return True, ""
    # 32'nin altindaki degerler hata kodudur; en sik goruleni kullanicinin
    # UAC penceresinde "Hayir" demesidir.
    if code in (5, 1223):
        return False, tr("Yetki verilmedi (UAC penceresinde iptal edildi).")
    return False, tr("Yeniden baslatilamadi (ShellExecute hatasi {}).", code)


# --------------------------------------------------------------------------
# Birim baglama / surucu harfi
# --------------------------------------------------------------------------
# Uc platform bu isi uc ayri kavramla yapar (olculdu, ADR 0043):
#
#   Linux  : dosya sistemi bir dizine baglanir. Root degilken `udisksctl`
#            (polkit) kullanilir; root iken `/media/<kullanici>/<etiket>`
#            altina kendimiz baglariz. `runuser -u <kullanici> udisksctl`
#            YOLU CALISMAZ: olusan surec aktif oturuma ait olmadigi icin
#            polkit `allow_active` kuralini uygulamaz ve etkilesimli
#            dogrulama ister.
#   Windows: "mount" diye bir kavram yoktur; birim zaten baglidir, gorunurlugu
#            **surucu harfi** belirler. Harf `Add-PartitionAccessPath` ile
#            atanir, `Remove-PartitionAccessPath` ile kaldirilir.
#   macOS  : `diskutil mount` -> /Volumes/<ad>. **Bu dal test EDILMEDI**
#            (projede macOS kosumu yok); hata metinleri bunu soyler.
LINUX_MOUNT_ROOT = "/media"


def mount_action_labels() -> Tuple[str, str]:
    """(baglama eylemi, cikarma eylemi) — platformun kendi kavramiyla.

    Windows'ta "bagla" demek kullaniciyi yanlis yere goturur: orada birim
    zaten baglidir, degisen sey harftir.
    """
    if IS_WINDOWS:
        return tr("Surucu harfi ata"), tr("Surucu harfini kaldir")
    return tr("Bagla"), tr("Cikar")


def mount_point_label() -> str:
    """Baglama noktasi alaninin/sutununun basligi (platformun kavramiyla)."""
    return tr("Surucu harfi") if IS_WINDOWS else tr("Baglama noktasi")


def mount_supported() -> Tuple[bool, str]:
    """(Baglama yapilabilir mi, yapilamiyorsa neden)."""
    if IS_WINDOWS:
        return True, ""
    if IS_LINUX:
        if shutil.which("udisksctl") or shutil.which("mount"):
            return True, ""
        return False, tr("`mount` veya `udisksctl` bulunamadi.")
    if IS_MACOS:
        if shutil.which("diskutil"):
            return True, ""
        return False, tr("`diskutil` bulunamadi.")
    return False, tr("Bu platformda baglama desteklenmiyor.")


def partition_device(disk_path: str, index: int) -> str:
    """Bolumun aygit yolu (Windows'ta bos: orada aygit yolu kullanilmaz).

    `index` bolum tablosundaki 1 tabanli sira numarasidir.
    """
    if index < 1:
        return ""
    if IS_WINDOWS:
        return ""
    if IS_MACOS:
        return f"{disk_path}s{index}"
    # Linux: nvme0n1 -> nvme0n1p3, sdb -> sdb3 (isim rakamla bitiyorsa 'p')
    taban = disk_path.rstrip("/")
    ek = "p" if taban[-1:].isdigit() else ""
    return f"{taban}{ek}{index}"


def _win_disk_number(disk_path: str) -> int:
    r"""`\\.\PhysicalDrive2` -> 2 (bulunamazsa -1)."""
    rakam = "".join(ch for ch in disk_path if ch.isdigit())
    return int(rakam) if rakam else -1


def _powershell(script: str, timeout: int = 60):
    """PowerShell komutu calistirir (Windows'ta her kurulumda vardir)."""
    return run_tool(["powershell", "-NoProfile", "-NonInteractive",
                     "-ExecutionPolicy", "Bypass", "-Command", script],
                    timeout=timeout)


def partition_mount_point(disk_path: str, index: int) -> str:
    r"""Bolum su an nereye bagli? Bagli degilse bos dize.

    Windows'ta surucu harfi doner (`E:\`).
    """
    try:
        if IS_WINDOWS:
            numara = _win_disk_number(disk_path)
            if numara < 0:
                return ""
            result = _powershell(
                f"(Get-Partition -DiskNumber {numara} -PartitionNumber {index}"
                f" -ErrorAction SilentlyContinue).DriveLetter")
            harf = (result.stdout or "").strip()
            return f"{harf}:\\" if harf and harf != "\x00" else ""
        device = partition_device(disk_path, index)
        if not device:
            return ""
        if IS_LINUX:
            gercek = os.path.realpath(device)
            for satir in _read_proc_mounts():
                parcalar = satir.split()
                if len(parcalar) >= 2 and os.path.realpath(parcalar[0]) == gercek:
                    return parcalar[1].replace("\\040", " ")
            return ""
        if IS_MACOS:
            result = run_tool(["diskutil", "info", "-plist", device], timeout=20)
            if result.returncode != 0:
                return ""
            import plistlib
            veri = plistlib.loads(result.stdout.encode("utf-8", "replace"))
            return str(veri.get("MountPoint") or "")
    except Exception:
        return ""
    return ""


def _read_proc_mounts() -> List[str]:
    """`/proc/mounts` satirlari (okunamazsa bos liste)."""
    try:
        with open("/proc/mounts", "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().splitlines()
    except OSError:
        return []


def _is_mount_point(path: str) -> bool:
    """Bu yolun kendisi bir baglama noktasi mi?"""
    for line in _read_proc_mounts():
        parts = line.split()
        if len(parts) >= 2 and parts[1].replace("\\040", " ") == path:
            return True
    return False


def _linux_mount_target(label: str, device: str) -> Tuple[str, Optional[Tuple[int, int]]]:
    """Baglama noktasi ve sahibi.

    Root iken masaustu kullanicisinin bekledigi yere baglanir:
    `/media/<kullanici>/<etiket>`. Kullanici bilinmiyorsa root'un altina
    duser — o zaman da en azindan ongorulebilir bir yerdir.
    """
    owner = invoking_user()
    name = "root"
    if owner is not None:
        try:
            import pwd
            name = pwd.getpwuid(owner[0]).pw_name
        except Exception:
            name = str(owner[0])
    elif os.environ.get("USER"):
        name = os.environ["USER"]
    clean = "".join(ch for ch in (label or "") if ch.isalnum() or ch in "-_. ").strip()
    if not clean:
        clean = os.path.basename(device) or "birim"
    return os.path.join(LINUX_MOUNT_ROOT, name, clean), owner


# Sahiplik secenegi kabul eden dosya sistemleri: baglamadan sonra chown
# ise yaramaz (FUSE/FAT sahipligi bagla-seceneginden alir).
_OWNER_OPTION_FS = ("fat", "vfat", "exfat", "ntfs", "fuseblk", "msdos")


def mount_partition(disk_path: str, index: int, label: str = "",
                    fs_type: str = "") -> Tuple[bool, str]:
    """Bolumu baglar (Windows'ta surucu harfi atar).

    Doner: (basarili, baglama noktasi veya hata metni).
    """
    allowed, reason = mount_supported()
    if not allowed:
        return False, reason
    mevcut = partition_mount_point(disk_path, index)
    if mevcut:
        return True, mevcut
    try:
        if IS_WINDOWS:
            return _win_assign_letter(disk_path, index)
        device = partition_device(disk_path, index)
        if not device or not os.path.exists(device):
            return False, tr("Bolum aygiti bulunamadi: {}", device or "?")
        if IS_MACOS:
            result = run_tool(["diskutil", "mount", device], timeout=60)
            if result.returncode != 0:
                return False, (result.stderr or result.stdout or "").strip()
            return True, partition_mount_point(disk_path, index) or device
        return _linux_mount(device, disk_path, index, label, fs_type)
    except Exception as exc:
        return False, str(exc)


def _linux_mount(device: str, disk_path: str, index: int, label: str,
                 fs_type: str) -> Tuple[bool, str]:
    """Linux baglama: root iken elle, degilken `udisksctl`."""
    elevated = is_elevated()
    if not elevated:
        arac = shutil.which("udisksctl")
        if arac:
            result = run_tool([arac, "mount", "-b", device], timeout=120)
            if result.returncode != 0:
                return False, (result.stderr or result.stdout or "").strip()
            return True, partition_mount_point(disk_path, index) or ""
        return False, tr("Baglamak icin root yetkisi veya `udisksctl` gerekir.")

    target, owner = _linux_mount_target(label, device)
    # Ayni etiketli ikinci bir birim (ya da basarisiz bir cikarmadan kalan
    # nokta) varsa uzerine baglamayiz: ust uste baglama alttaki dosya
    # sistemini gorunmez kilar ve cikarirken kullaniciyi sasirtir — bu
    # denemede bir kez yasandi (ADR 0043).
    if _is_mount_point(target):
        for extra in range(2, 20):
            candidate = f"{target}-{extra}"
            if not _is_mount_point(candidate):
                target = candidate
                break
    os.makedirs(target, exist_ok=True)
    cmd = ["mount"]
    if owner is not None and (fs_type or "").lower().startswith(_OWNER_OPTION_FS):
        cmd += ["-o", f"uid={owner[0]},gid={owner[1]}"]
    cmd += [device, target]
    result = run_tool(cmd, timeout=120)
    if result.returncode != 0:
        try:
            os.rmdir(target)
        except OSError:
            pass
        return False, (result.stderr or result.stdout or "").strip()
    if owner is not None:
        try:                              # noktanin kendisi kullanicinin olsun
            os.chown(target, owner[0], owner[1])
        except OSError:
            pass
    return True, target


def unmount_partition(disk_path: str, index: int) -> Tuple[bool, str]:
    """Bolumun baglantisini keser (Windows'ta surucu harfini kaldirir)."""
    allowed, reason = mount_supported()
    if not allowed:
        return False, reason
    point = partition_mount_point(disk_path, index)
    if not point:
        return True, ""                   # zaten bagli degil
    try:
        if IS_WINDOWS:
            return _win_remove_letter(disk_path, index, point)
        device = partition_device(disk_path, index)
        if IS_MACOS:
            result = run_tool(["diskutil", "unmount", device], timeout=60)
            return ((result.returncode == 0),
                    (result.stderr or result.stdout or "").strip()
                    if result.returncode else "")
        cmd = ([shutil.which("udisksctl"), "unmount", "-b", device]
               if (not is_elevated() and shutil.which("udisksctl"))
               else ["umount", device])
        # Yeni baglanan bir birimi isletim sistemi bir sure yoklar (udev
        # kurallari, masaustu indeksleyicileri); bu sirada cikarma "target is
        # busy" der. Olculdu: ikinci deneme geciyor. Zorlama bayragi
        # (`-l`/`-f`) KULLANILMAZ — tembel cikarma, dosya sistemi hala
        # yazilirken aygiti serbest birakip veriyi riske atar (ADR 0043).
        for attempt in range(3):
            result = run_tool(cmd, timeout=120)
            if result.returncode == 0:
                break
            if attempt < 2:
                time.sleep(0.4 * (attempt + 1))
        if result.returncode != 0:
            return False, (result.stderr or result.stdout or "").strip()
        # Kendi actigimiz bos klasoru birakmayalim. Cekirdek baglantiyi
        # birakirken klasor bir an daha mesgul gorunebilir; bir kez daha
        # denenir, olmazsa bos bir klasor kalir (zararsiz).
        if point.startswith(LINUX_MOUNT_ROOT + os.sep):
            for attempt in range(2):
                try:
                    os.rmdir(point)
                    break
                except OSError:
                    time.sleep(0.3)
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _win_assign_letter(disk_path: str, index: int) -> Tuple[bool, str]:
    """Bolume ilk bos surucu harfini atar."""
    numara = _win_disk_number(disk_path)
    if numara < 0:
        return False, tr("Disk numarasi cozulemedi: {}", disk_path)
    result = _powershell(
        f"Add-PartitionAccessPath -DiskNumber {numara} -PartitionNumber {index}"
        f" -AssignDriveLetter -ErrorAction Stop")
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "").strip()
    return True, partition_mount_point(disk_path, index)


def _win_remove_letter(disk_path: str, index: int, point: str) -> Tuple[bool, str]:
    """Bolumun surucu harfini kaldirir."""
    numara = _win_disk_number(disk_path)
    if numara < 0:
        return False, tr("Disk numarasi cozulemedi: {}", disk_path)
    result = _powershell(
        f"Remove-PartitionAccessPath -DiskNumber {numara} -PartitionNumber {index}"
        f" -AccessPath '{point}' -ErrorAction Stop")
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "").strip()
    return True, ""


# --------------------------------------------------------------------------
# Yetkili kopyada dosya sahipligi
# --------------------------------------------------------------------------
# Uygulama root olarak calistiginda urettigi her dosya root'a ait olur:
# kullanici kendi ev dizinine aldigi yedegi sonradan silemez, acamaz. pkexec
# ve sudo, kendilerini cagiran kullanicinin numarasini ortamda birakir; o
# numara varken uretilen dosyalarin sahipligi kullaniciya geri verilir
# (ADR 0042).
INVOKER_ENV = ("PKEXEC_UID", "SUDO_UID")


def invoking_user() -> Optional[Tuple[int, int]]:
    """Yetkiyi veren kullanicinin (uid, gid) cifti; yoksa None.

    None donmesi olagandir: uygulama zaten kullanici yetkisiyle calisiyordur
    ya da dogrudan root oturumundan baslatilmistir.
    """
    if IS_WINDOWS:
        return None
    try:
        if os.geteuid() != 0:
            return None
    except AttributeError:              # bu platformda kavram yok
        return None
    for name in INVOKER_ENV:
        raw = os.environ.get(name, "")
        if not raw.isdigit():
            continue
        uid = int(raw)
        if uid == 0:
            continue
        try:
            import pwd
            return uid, pwd.getpwuid(uid).pw_gid
        except Exception:
            return uid, uid
    return None


def restore_owner(path: str, recursive: bool = False) -> bool:
    """Uretilen dosyanin sahipligini yetkiyi veren kullaniciya cevirir.

    Yetkili kopya degilsek ya da kullanici numarasi bilinmiyorsa hicbir sey
    yapmaz. Basarisizlik olumcul degildir: dosya yazilmistir, yalnizca sahibi
    root kalir.
    """
    owner = invoking_user()
    if owner is None or not path:
        return False
    uid, gid = owner
    targets = [path]
    if recursive and os.path.isdir(path):
        for root, dirs, files in os.walk(path):
            targets.extend(os.path.join(root, n) for n in dirs + files)
    changed = False
    for target in targets:
        try:
            os.chown(target, uid, gid)
            changed = True
        except OSError:
            pass
    return changed


# --------------------------------------------------------------------------
# Kullanici dizinleri
# --------------------------------------------------------------------------
def user_data_dir(app: str = "DiskUltimate") -> str:
    """Kullaniciya ait kalici veri dizini (gerekirse olusturulur).

    Yalnizca **paketlenmis** kopya icin gerekir: kaynaktan calisirken uretilen
    her sey proje dizininde kalir (CLAUDE.md). Paketlenmis kopyada proje
    dizini yoktur, bu yuzden isletim sisteminin gosterdigi yer kullanilir.
    """
    home = os.path.expanduser("~")
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
    elif IS_MACOS:
        base = os.path.join(home, "Library", "Application Support")
    else:
        base = os.environ.get("XDG_STATE_HOME") or os.path.join(home, ".local", "state")
    path = os.path.join(base, app)
    os.makedirs(path, exist_ok=True)
    return path


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


def open_folder(path: str) -> bool:
    """Verilen klasoru isletim sisteminin dosya yoneticisinde acar.

    Tanilama gunlugune ulasmayi kolaylastirmak icindir; basarisiz olursa
    arayuz yolu metin olarak gosterir. Platform farki burada durur.
    """
    if not path or not os.path.isdir(path):
        return False
    if IS_WINDOWS:
        cmd = ["explorer", os.path.normpath(path)]
    elif IS_MACOS:
        cmd = ["open", path]
    else:
        cmd = ["xdg-open", path]
    try:
        run_tool(cmd, timeout=15)
    except Exception:
        return False
    # explorer.exe basarili durumda bile 1 dondurebilir; cagrinin yapilmis
    # olmasi yeterlidir.
    return True


def default_image_dir() -> str:
    """Yeni goruntuler icin varsayilan klasor."""
    for name in ("Documents", "Belgeler"):
        path = os.path.join(os.path.expanduser("~"), name)
        if os.path.isdir(path):
            return path
    return os.path.expanduser("~")


def config_dir() -> str:
    r"""Kullaniciya ozel ayar klasoru (gerekirse olusturulur).

    Isletim sistemlerinin kendi gelenegi kullanilir; cekirdegin geri kalani
    bu ayrimi bilmez:

        Windows : %APPDATA%\DiskUltimate
        macOS   : ~/Library/Application Support/DiskUltimate
        Linux   : $XDG_CONFIG_HOME/diskultimate (yoksa ~/.config/diskultimate)

    Ayarlar **proje dizinine degil** buraya yazilir: uygulama salt okunur bir
    klasorden (Program Files, /usr/bin) calistirilabilir.
    """
    if IS_WINDOWS:
        base = os.environ.get("APPDATA") or os.path.join(
            os.path.expanduser("~"), "AppData", "Roaming")
        path = os.path.join(base, "DiskUltimate")
    elif IS_MACOS:
        path = os.path.join(os.path.expanduser("~"), "Library",
                            "Application Support", "DiskUltimate")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
            os.path.expanduser("~"), ".config")
        path = os.path.join(base, "diskultimate")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


# Windows LANGID -> dil kodu (yalnizca birincil dil kimligi kullanilir).
_WINDOWS_LANGUAGES = {
    0x01: "ar", 0x02: "bg", 0x04: "zh", 0x05: "cs", 0x06: "da", 0x07: "de",
    0x08: "el", 0x09: "en", 0x0A: "es", 0x0B: "fi", 0x0C: "fr", 0x0D: "he",
    0x0E: "hu", 0x10: "it", 0x11: "ja", 0x12: "ko", 0x13: "nl", 0x15: "pl",
    0x16: "pt", 0x19: "ru", 0x1D: "sv", 0x1F: "tr", 0x22: "uk",
}


def system_language() -> str:
    """Isletim sisteminin arayuz dilini iki harfli kod olarak dondurur.

    Bulunamazsa bos dize doner; cagiran taraf kendi varsayilanina duser.
    `locale.getdefaultlocale()` kullanilmaz: Python 3.11'den beri kullanimdan
    kaldirilmistir ve Windows'ta kullanicinin **arayuz** dilini degil bolge
    ayarini verir.
    """
    if IS_WINDOWS:
        try:
            import ctypes

            langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            return _WINDOWS_LANGUAGES.get(langid & 0x3FF, "")
        except Exception:
            return ""
    for name in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(name, "").strip()
        if not value or value in ("C", "POSIX"):
            continue
        # "tr_TR.UTF-8:en_US" -> "tr"
        code = value.split(":")[0].split(".")[0].split("_")[0].split("-")[0]
        if code:
            return code.lower()
    return ""


def elevation_name() -> str:
    """Yetki adinin **cevrilmis** hali ("Yonetici" / "root").

    `ELEVATION_NAME` sabiti Turkce kalir ve cekirdek testlerinde kullanilir;
    arayuz bu islevi cagirir, boylece dil degisince metin de degisir.
    """
    return tr("Yonetici") if IS_WINDOWS else "root"


def summary() -> dict:
    """Tani amacli platform ozeti."""
    name = elevation_name()
    return {
        tr("Platform"): PLATFORM_NAME,
        tr("Python"): sys.version.split()[0],
        tr("Mimari"): "64 bit" if sys.maxsize > 2 ** 32 else "32 bit",
        tr("Yetki"): (tr("{} (tam erisim)", name) if is_elevated()
                      else tr("Normal kullanici — fiziksel disk icin {} gerekir",
                              name)),
        tr("Harici araclar"): ", ".join(
            f"{k}:{tr('var') if find_tool(k) else tr('yok')}"
            for k in EXTERNAL_TOOLS),
    }


# --------------------------------------------------------------------------
# Isletim sisteminin kendi bicimlendiricisi
# --------------------------------------------------------------------------
def refs_edition_allows(edition_id: str, installation_type: str) -> bool:
    """Windows surumu ReFS olusturabilir mi? (ADR 0072)

    Windows 10 1709'dan beri ReFS **olusturma** yalnizca Enterprise ve Pro
    for Workstations istemci surumlerinde ve Server'da vardir (Home/Pro/
    Education okuyabilir ama bicimlendiremez). Bilinmeyen surum reddedilir
    (yanlis "var" demektense secenek gri kalir).
    """
    edition = (edition_id or "").lower()
    if (installation_type or "").lower() == "server" or edition.startswith("server"):
        return True
    return edition.startswith("enterprise") or edition.startswith("professionalworkstation")


_REFS_SUPPORT: Optional[Tuple[bool, str]] = None


def refs_format_support() -> Tuple[bool, str]:
    """(olusturulabilir_mi, neden). Kayit defterinden okunur — alt surec yok,
    arayuz is parcaciginda guvenle cagrilir; sonuc onbellege alinir."""
    global _REFS_SUPPORT
    if _REFS_SUPPORT is not None:
        return _REFS_SUPPORT
    if not IS_WINDOWS:
        _REFS_SUPPORT = (False, tr("ReFS yalnizca Windows'un kendi araciyla "
                                   "olusturulabilir; {} uzerinde arac yok", PLATFORM_NAME))
        return _REFS_SUPPORT
    edition, kind = "", ""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as key:
            edition = str(winreg.QueryValueEx(key, "EditionID")[0])
            try:
                kind = str(winreg.QueryValueEx(key, "InstallationType")[0])
            except OSError:
                kind = ""
    except Exception:                                  # noqa: BLE001
        edition = ""
    if refs_edition_allows(edition, kind):
        _REFS_SUPPORT = (True, "")
    else:
        _REFS_SUPPORT = (False, tr("Bu Windows surumu ({}) ReFS olusturamiyor; "
                                   "Enterprise, Pro for Workstations ya da Server "
                                   "gerekir", edition or tr("bilinmiyor")))
    return _REFS_SUPPORT


def native_format_supported(fs_key: str) -> bool:
    """Isletim sistemi bu bicimi kendi araciyla olusturabiliyor mu?

    Windows'ta NTFS/FAT/exFAT icin `Format-Volume` her kurulumda bulunur; bu,
    ntfs-3g'nin Windows'ta olmamasinin pratik karsiligidir. ReFS surume
    baglidir (`refs_format_support`).
    """
    if IS_WINDOWS:
        if fs_key == "refs":
            return refs_format_support()[0]
        return fs_key in ("ntfs", "exfat", "fat32", "fat16")
    return False


def format_volume_command(disk_number: int, partition_number: int, fs_key: str,
                          label: str = "", cluster_size: int = 0) -> str:
    """`Format-Volume` komutu (saf islev — test edilebilir)."""
    fs_name = {"ntfs": "NTFS", "exfat": "exFAT", "fat32": "FAT32", "fat16": "FAT",
               "refs": "ReFS"}[fs_key]
    etiket = (label or "").replace('"', "").replace("`", "").replace("$", "")
    komut = (f"$ErrorActionPreference='Stop'; "
             f"$p = Get-Partition -DiskNumber {int(disk_number)} "
             f"-PartitionNumber {int(partition_number)}; "
             f"$p | Format-Volume -FileSystem {fs_name} "
             f"-NewFileSystemLabel \"{etiket}\" -Confirm:$false -Force")
    if cluster_size:
        komut += f" -AllocationUnitSize {int(cluster_size)}"
    return komut + " | Out-Null; 'TAMAM'"


def windows_format_volume(disk_number: int, partition_number: int,
                          fs_key: str, label: str = "",
                          cluster_size: int = 0) -> tuple:
    """Windows'un kendi bicimlendiricisiyle bir bolumu bicimlendirir.

    Surucu harfi gerekmez: bolum nesnesi `Format-Volume`e dogrudan verilir.
    (basarili_mi, mesaj) dondurur.
    """
    if not IS_WINDOWS:
        return False, tr("Yalnizca Windows")
    if fs_key not in ("ntfs", "exfat", "fat32", "fat16", "refs"):
        return False, tr("{} Windows araciyla olusturulamaz", fs_key)
    komut = format_volume_command(disk_number, partition_number, fs_key, label,
                                  cluster_size)
    try:
        result = run_tool(["powershell", "-NoProfile", "-NonInteractive",
                          "-Command", komut], timeout=600)
    except Exception as exc:
        return False, str(exc)
    if result.returncode == 0 and "TAMAM" in (result.stdout or ""):
        return True, tr("Windows bicimlendiricisi kullanildi")
    return False, ((result.stderr or result.stdout or "").strip()[:300]
                   or f"cikis kodu {result.returncode}")


def native_resize_supported() -> bool:
    """Isletim sistemi bir bolumu kendi araciyla yeniden boyutlandirabiliyor mu?

    Windows'ta `Resize-Partition` NTFS'i (ve destekledigi digerlerini) dosya
    sistemiyle birlikte buyutup kucultur; fiziksel disklerde NTFS icin bu yol
    tercih edilir. ext'i tanimaz — ext saf Python yolundan gider
    (`resize.fs_resize_info`, ADR 0052).
    """
    return IS_WINDOWS


def windows_partition_size_limits(disk_number: int, partition_number: int) -> tuple:
    """(basarili_mi, en_kucuk_bayt, en_buyuk_bayt, mesaj)."""
    if not IS_WINDOWS:
        return False, 0, 0, tr("Yalnizca Windows")
    komut = (f"$ErrorActionPreference='Stop'; "
             f"$s = Get-PartitionSupportedSize -DiskNumber {disk_number} "
             f"-PartitionNumber {partition_number}; "
             f"\"$($s.SizeMin) $($s.SizeMax)\"")
    try:
        result = run_tool(["powershell", "-NoProfile", "-NonInteractive",
                          "-Command", komut], timeout=120)
    except Exception as exc:                       # noqa: BLE001
        return False, 0, 0, str(exc)
    if result.returncode != 0:
        return False, 0, 0, (result.stderr or result.stdout or "").strip()[:300]
    try:
        lower, upper = (result.stdout or "").strip().split()
        return True, int(lower), int(upper), ""
    except ValueError:
        return False, 0, 0, tr("Beklenmeyen cikti")


def windows_resize_partition(disk_number: int, partition_number: int,
                             size_bytes: int) -> tuple:
    """Windows'un kendi boyutlandiricisiyla bolumu yeniden boyutlandirir.

    Dosya sistemi de birlikte boyutlandirilir. (basarili_mi, mesaj) dondurur.
    """
    if not IS_WINDOWS:
        return False, tr("Yalnizca Windows")
    komut = (f"$ErrorActionPreference='Stop'; "
             f"Resize-Partition -DiskNumber {disk_number} "
             f"-PartitionNumber {partition_number} -Size {int(size_bytes)}; "
             f"'TAMAM'")
    try:
        result = run_tool(["powershell", "-NoProfile", "-NonInteractive",
                          "-Command", komut], timeout=1800)
    except Exception as exc:                       # noqa: BLE001
        return False, str(exc)
    if result.returncode == 0 and "TAMAM" in (result.stdout or ""):
        return True, tr("Windows boyutlandiricisi kullanildi")
    return False, (result.stderr or result.stdout or "").strip()[:300]


# ==========================================================================
# Onyukleyici araclari ve bellenim degiskenleri
# ==========================================================================
# CLAUDE.md kurali: isletim sistemi farklari **yalnizca bu dosyada** durur.
# Onyukleyici yonetimi bu kuralin en sert sinandigi yerdir: GRUB yalnizca
# Linux'ta kurulur, UEFI degiskenleri Linux'ta bir dosya sistemi, Windows'ta
# bir cekirdek cagrisidir. Yapiyi cozen kod (`core/efiboot.py`) ve cozumleme
# (`core/bootloader.py`) bu ayrimi hic gormez; buradan **ham bayt** alirlar.

# Onyukleyici araclarinin platforma gore adlari.
BOOT_TOOLS = {
    "grub-install": {"linux": ["grub-install", "grub2-install"],
                     "darwin": [], "win32": []},
    "grub-mkconfig": {"linux": ["grub-mkconfig", "grub2-mkconfig"],
                      "darwin": [], "win32": []},
    "update-grub": {"linux": ["update-grub"], "darwin": [], "win32": []},
    "os-prober": {"linux": ["os-prober"], "darwin": [], "win32": []},
    "efibootmgr": {"linux": ["efibootmgr"], "darwin": [], "win32": []},
    "bcdedit": {"linux": [], "darwin": [], "win32": ["bcdedit"]},
}

# GRUB'un yapilandirma dosyalari (Linux). Dagitimlar arasinda ad degisir:
# Debian ailesi `/boot/grub`, Fedora/SUSE `/boot/grub2` kullanir.
GRUB_DEFAULTS_PATH = "/etc/default/grub"
GRUB_SCRIPT_DIR = "/etc/grub.d"
GRUB_CONFIG_PATHS = ["/boot/grub/grub.cfg", "/boot/grub2/grub.cfg",
                     "/boot/efi/EFI/*/grub.cfg"]

# Linux'ta UEFI degiskenleri bir dosya sistemi olarak gorunur.
EFIVARS_DIR = "/sys/firmware/efi/efivars"
EFI_FIRMWARE_DIR = "/sys/firmware/efi"

# ext dosya sistemlerinde "degistirilemez" bayragi (linux/fs.h).
_FS_IOC_GETFLAGS = 0x80086601
_FS_IOC_SETFLAGS = 0x40086602
_FS_IMMUTABLE_FL = 0x00000010


def find_boot_tool(name: str) -> Optional[str]:
    """Onyukleyici aracinin tam yolu (bu platformda yoksa None)."""
    for candidate in BOOT_TOOLS.get(name, {}).get(_platform_key(), []):
        path = shutil.which(candidate)
        if path:
            return path
        for directory in _EK_ARAMA_YOLLARI:     # grub-install cogunlukla sbin'de
            full = os.path.join(directory, candidate)
            if os.path.isfile(full) and os.access(full, os.X_OK):
                return full
    return None


def boot_tools_present() -> Dict[str, str]:
    """Bulunan onyukleyici araclari: {ad: yol}. Arayuz eksikleri boyle gosterir."""
    return {name: (find_boot_tool(name) or "") for name in BOOT_TOOLS}


def grub_management_available() -> Tuple[bool, str]:
    """GRUB kurulabilir/guncellenebilir mi? (evet_mi, neden).

    Cozumleme her platformda yapilir ama **kurulum** calisan sistemin
    araclarini gerektirir. Windows'ta bir Linux diskini inceleyebiliriz,
    ona GRUB kuramayiz — bunu acikca soylemek, calismayan bir dugme
    gostermekten iyidir.
    """
    if not IS_LINUX:
        return False, tr("GRUB kurulumu yalnizca Linux'ta yapilabilir; bu "
                         "sistemde ({}) yalnizca inceleme yapilir.",
                         PLATFORM_NAME)
    if not find_boot_tool("grub-install"):
        return False, tr("`grub-install` bulunamadi (grub-pc ya da "
                         "grub-efi paketi kurulu degil).")
    return True, ""


def run_privileged(command: List[str], timeout: int = 900) -> Tuple[int, str, str]:
    """Komutu gerektiginde yetki yukselterek calistirir.

    Zaten root/yonetici isek dogrudan calistirilir; degilsek Linux'ta
    `pkexec` grafik parola penceresi acar. Doner: (cikis kodu, cikti, hata).

    Bu islev **uzun surebilir** ve kullanicidan parola bekleyebilir; arayuz
    is parcaciginda degil, `dialogs/task.run_task` icinde cagrilmalidir
    (ADR 0020).
    """
    if not command:
        return 1, "", tr("Bos komut")
    if is_elevated():
        full = list(command)
    elif IS_LINUX:
        agent = shutil.which("pkexec")
        if not agent:
            return 1, "", tr("Grafik yetki penceresi icin `pkexec` gerekiyor "
                             "(polkit paketi).")
        # pkexec ortami temizler; araclar sbin'de oldugu icin PATH elle verilir.
        full = [agent, "env", "PATH=/usr/sbin:/usr/bin:/sbin:/bin"] + list(command)
    else:
        return 1, "", tr("Bu islem {} yetkisi gerektiriyor.", elevation_name())
    try:
        result = run_tool(full, timeout=timeout)
    except Exception as exc:                        # noqa: BLE001
        return 1, "", str(exc)
    return (result.returncode, (result.stdout or "").strip(),
            (result.stderr or "").strip())


def write_system_file(path: str, text: str) -> Tuple[bool, str]:
    """Yetki gerektiren bir metin dosyasini yazar (once gecici dosyaya).

    Dogrudan `pkexec tee` gibi bir kabuk hilesine basvurulmaz: icerik once
    proje calisma alanina yazilir, sonra tek bir `cp` ile yerine konur.
    Boylece yarim yazilmis bir yapilandirma dosyasi olusamaz.
    """
    from ..paths import scratch

    staging = os.path.join(scratch("boot"), os.path.basename(path) or "config")
    try:
        with open(staging, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    except OSError as exc:
        return False, str(exc)
    if is_elevated():
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            return True, ""
        except OSError as exc:
            return False, str(exc)
    code, _, error = run_privileged(["cp", staging, path], timeout=60)
    return (code == 0), error


# --------------------------------------------------------------------------
# Bellenim turu
# --------------------------------------------------------------------------
def firmware_type() -> str:
    """Makine nasil acildi: 'uefi' | 'bios' | '' (bilinmiyor).

    Onemlidir: UEFI ile acilmamis bir makinede onyukleme degiskenleri
    **yoktur**, bos degildir. Ikisini ayni gostermek kullaniciyi yaniltir.
    """
    if IS_LINUX:
        return "uefi" if os.path.isdir(EFI_FIRMWARE_DIR) else "bios"
    if IS_WINDOWS:
        try:
            import ctypes
            import ctypes.wintypes as wt

            kind = wt.DWORD(0)
            k32 = ctypes.windll.kernel32
            k32.GetFirmwareType.argtypes = [ctypes.POINTER(wt.DWORD)]
            k32.GetFirmwareType.restype = wt.BOOL
            if k32.GetFirmwareType(ctypes.byref(kind)):
                return {1: "bios", 2: "uefi"}.get(kind.value, "")
        except Exception:                           # noqa: BLE001
            return ""
        return ""
    if IS_MACOS:
        return "uefi"       # Intel Mac'ler EFI ile acilir; degiskenler kapali
    return ""


def efivars_state() -> Tuple[bool, bool, str]:
    """(okunabilir mi, yazilabilir mi, aciklama).

    Aciklama her durumda doludur: erisilemiyorsa **neden** erisilemedigini
    soyler. "Giris bulunamadi" ile "yetki yok" ayni sey degildir.
    """
    kind = firmware_type()
    if kind == "bios":
        return False, False, tr("Makine BIOS (eski) kipinde acilmis; UEFI "
                                "onyukleme degiskenleri yok.")
    if IS_LINUX:
        if not os.path.isdir(EFIVARS_DIR):
            return False, False, tr("`{}` bagli degil; `efivarfs` cekirdek "
                                    "modulu yuklu olmayabilir.", EFIVARS_DIR)
        if not os.access(EFIVARS_DIR, os.R_OK):
            return False, False, tr("Degiskenler okunamiyor; root yetkisi "
                                    "gerekiyor.")
        return True, is_elevated(), ("" if is_elevated() else
                                     tr("Degistirmek icin root yetkisi gerekir."))
    if IS_WINDOWS:
        if kind != "uefi":
            return False, False, tr("Bellenim turu belirlenemedi.")
        if not is_elevated():
            return False, False, tr("Bellenim degiskenleri icin {} yetkisi "
                                    "gerekiyor.", elevation_name())
        ok, reason = _win_enable_firmware_privilege()
        return ok, ok, ("" if ok else reason)
    if IS_MACOS:
        return False, False, tr("macOS bellenim degiskenlerine erisim "
                                "vermiyor.")
    return False, False, tr("Bu platformda bellenim degiskenleri okunamiyor.")


# --------------------------------------------------------------------------
# Bellenim degiskenleri — Linux (efivarfs)
# --------------------------------------------------------------------------
def _linux_var_path(name: str, guid: str) -> str:
    return os.path.join(EFIVARS_DIR, f"{name}-{guid.lower()}")


def _linux_var_names() -> List[Tuple[str, str]]:
    try:
        entries = os.listdir(EFIVARS_DIR)
    except OSError:
        return []
    found = []
    for entry in entries:
        # Ad bicimi: <ad>-<36 karakterlik guid>
        if len(entry) < 38 or entry[-37] != "-":
            continue
        found.append((entry[:-37], entry[-36:]))
    return found


def _linux_clear_immutable(path: str) -> None:
    """efivarfs dosyasindaki "degistirilemez" bayragini kaldirir.

    Cekirdek bircok degiskeni bu bayrakla acar; kaldirilmadan yazma
    `EPERM` verir. `chattr -i` calistirmak yerine ayni ioctl dogrudan
    cagrilir — harici arac gerektirmez.
    """
    try:
        import fcntl
        import array

        with open(path, "rb") as fh:
            flags = array.array("i", [0])
            fcntl.ioctl(fh.fileno(), _FS_IOC_GETFLAGS, flags, True)
            if not (flags[0] & _FS_IMMUTABLE_FL):
                return
        with open(path, "rb") as fh:
            flags[0] &= ~_FS_IMMUTABLE_FL
            fcntl.ioctl(fh.fileno(), _FS_IOC_SETFLAGS, flags, False)
    except Exception:                               # noqa: BLE001
        pass            # bayrak kaldirilamadiysa yazma denemesi zaten hata verir


# --------------------------------------------------------------------------
# Bellenim degiskenleri — Windows
# --------------------------------------------------------------------------
_WIN_PRIVILEGE_DONE = [False]


def _win_enable_firmware_privilege() -> Tuple[bool, str]:
    """SeSystemEnvironmentPrivilege ayricaligini etkinlestirir.

    Windows bellenim degiskeni cagrilarini yalnizca bu ayricalik acikken
    kabul eder ve ayricalik yonetici belirtecinde bile **varsayilan olarak
    kapalidir**.
    """
    if _WIN_PRIVILEGE_DONE[0]:
        return True, ""
    try:
        import ctypes
        import ctypes.wintypes as wt

        class LUID(ctypes.Structure):
            _fields_ = [("LowPart", wt.DWORD), ("HighPart", ctypes.c_long)]

        class LUID_AND_ATTRIBUTES(ctypes.Structure):
            _fields_ = [("Luid", LUID), ("Attributes", wt.DWORD)]

        class TOKEN_PRIVILEGES(ctypes.Structure):
            _fields_ = [("PrivilegeCount", wt.DWORD),
                        ("Privileges", LUID_AND_ATTRIBUTES * 1)]

        advapi = ctypes.windll.advapi32
        k32 = ctypes.windll.kernel32
        TOKEN_ADJUST_PRIVILEGES = 0x0020
        TOKEN_QUERY = 0x0008
        SE_PRIVILEGE_ENABLED = 0x0002

        # argtypes acikca verilir: ctypes varsayilanlari 64 bit tutamaci
        # 32 bite keser ve cagri sessizce basarisiz olur (bkz. _win_kernel32).
        k32.GetCurrentProcess.restype = wt.HANDLE
        advapi.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD,
                                            ctypes.POINTER(wt.HANDLE)]
        advapi.OpenProcessToken.restype = wt.BOOL
        advapi.LookupPrivilegeValueW.argtypes = [wt.LPCWSTR, wt.LPCWSTR,
                                                 ctypes.POINTER(LUID)]
        advapi.LookupPrivilegeValueW.restype = wt.BOOL
        advapi.AdjustTokenPrivileges.argtypes = [
            wt.HANDLE, wt.BOOL, ctypes.POINTER(TOKEN_PRIVILEGES), wt.DWORD,
            wt.LPVOID, wt.LPVOID]
        advapi.AdjustTokenPrivileges.restype = wt.BOOL

        token = wt.HANDLE()
        if not advapi.OpenProcessToken(k32.GetCurrentProcess(),
                                       TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
                                       ctypes.byref(token)):
            return False, tr("Surec belirteci acilamadi.")
        try:
            luid = LUID()
            if not advapi.LookupPrivilegeValueW(None,
                                                "SeSystemEnvironmentPrivilege",
                                                ctypes.byref(luid)):
                return False, tr("Bellenim ayricaligi bulunamadi.")
            privileges = TOKEN_PRIVILEGES()
            privileges.PrivilegeCount = 1
            privileges.Privileges[0].Luid = luid
            privileges.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED
            k32.SetLastError(0)
            advapi.AdjustTokenPrivileges(token, False,
                                         ctypes.byref(privileges), 0, None, None)
            # AdjustTokenPrivileges ayricaligin bir bolumu verilmese de
            # "basarili" doner; gercek sonuc son hata kodundadir.
            if k32.GetLastError() != 0:
                return False, tr("Bellenim ayricaligi verilmedi ({} yetkisi "
                                 "gerekir).", elevation_name())
        finally:
            k32.CloseHandle(token)
        _WIN_PRIVILEGE_DONE[0] = True
        return True, ""
    except Exception as exc:                        # noqa: BLE001
        return False, str(exc)


def _win_guid(guid: str) -> str:
    """Windows API'sinin bekledigi susluparantezli GUID bicimi."""
    clean = guid.strip().strip("{}")
    return "{" + clean + "}"


def _win_var_names() -> List[Tuple[str, str]]:
    """Bellenim degiskenlerini sayar (`NtEnumerateSystemEnvironmentValuesEx`).

    Windows'ta bu sayimin belgelenmis bir karsiligi yoktur; cagri ntdll
    icindedir. Basarisiz olursa bos liste doner ve cagiran **bilinen adlari
    tek tek dener** — eksik bir liste gostermektense bilinenleri gostermek
    daha dogrudur.
    """
    try:
        import ctypes
        import ctypes.wintypes as wt

        ntdll = ctypes.windll.ntdll
        VARIABLE_INFORMATION_NAMES = 1
        size = wt.ULONG(0)
        ntdll.NtEnumerateSystemEnvironmentValuesEx(
            VARIABLE_INFORMATION_NAMES, None, ctypes.byref(size))
        if size.value == 0:
            return []
        buffer = ctypes.create_string_buffer(size.value)
        status = ntdll.NtEnumerateSystemEnvironmentValuesEx(
            VARIABLE_INFORMATION_NAMES, buffer, ctypes.byref(size))
        if status != 0:
            return []
    except Exception:                               # noqa: BLE001
        return []

    found: List[Tuple[str, str]] = []
    raw = buffer.raw[:size.value]
    offset = 0
    # Kayit: ULONG NextEntryOffset; GUID VendorGuid (16 bayt); WCHAR Name[]
    while offset + 20 <= len(raw):
        next_offset = int.from_bytes(raw[offset:offset + 4], "little")
        guid_raw = raw[offset + 4:offset + 20]
        name_raw = raw[offset + 20:offset + next_offset] if next_offset \
            else raw[offset + 20:]
        name = name_raw.decode("utf-16-le", "replace").split("\x00", 1)[0]
        if name:
            found.append((name, _guid_text(guid_raw)))
        if not next_offset:
            break
        offset += next_offset
    return found


def _guid_text(raw: bytes) -> str:
    """16 baytlik EFI GUID'ini metne cevirir (karisik siralama)."""
    ordered = raw[0:4][::-1] + raw[4:6][::-1] + raw[6:8][::-1] + raw[8:16]
    text = ordered.hex()
    return f"{text[0:8]}-{text[8:12]}-{text[12:16]}-{text[16:20]}-{text[20:32]}"


# --------------------------------------------------------------------------
# Bellenim degiskenleri — ortak arayuz
# --------------------------------------------------------------------------
def efivar_names() -> List[Tuple[str, str]]:
    """Butun bellenim degiskenleri: [(ad, guid)]."""
    if IS_LINUX:
        return _linux_var_names()
    if IS_WINDOWS:
        # Windows **okumak** icin de ayricaligi ister; etkinlestirilmeden
        # yapilan her cagri sessizce bos doner.
        _win_enable_firmware_privilege()
        return _win_var_names()
    return []


def efivar_read(name: str, guid: str) -> Tuple[int, bytes]:
    """Bir degiskeni okur: (oznitelikler, veri). Yoksa (0, b"")."""
    if IS_LINUX:
        try:
            with open(_linux_var_path(name, guid), "rb") as fh:
                raw = fh.read()
        except OSError:
            return 0, b""
        if len(raw) < 4:
            return 0, b""
        return int.from_bytes(raw[:4], "little"), raw[4:]
    if IS_WINDOWS:
        try:
            import ctypes
            import ctypes.wintypes as wt

            _win_enable_firmware_privilege()
            k32 = ctypes.windll.kernel32
            k32.GetFirmwareEnvironmentVariableExW.argtypes = [
                wt.LPCWSTR, wt.LPCWSTR, wt.LPVOID, wt.DWORD,
                ctypes.POINTER(wt.DWORD)]
            k32.GetFirmwareEnvironmentVariableExW.restype = wt.DWORD
            buffer = ctypes.create_string_buffer(8192)
            attributes = wt.DWORD(0)
            length = k32.GetFirmwareEnvironmentVariableExW(
                name, _win_guid(guid), buffer, len(buffer),
                ctypes.byref(attributes))
            if length == 0:
                return 0, b""
            return attributes.value, buffer.raw[:length]
        except Exception:                           # noqa: BLE001
            return 0, b""
    return 0, b""


def efivar_write(name: str, guid: str, attributes: int,
                 data: bytes) -> Tuple[bool, str]:
    """Bir degiskeni yazar. (basarili_mi, hata).

    **Yikici olabilir**: yanlis yazilmis bir `BootOrder` makineyi acilmaz
    hale getirebilir. Cagiran once yedek almalidir; arayuz bunu zorunlu
    tutar (bkz. `ui/dialogs/efiboot.py`).
    """
    readable, writable, reason = efivars_state()
    if not writable:
        return False, reason or tr("Bellenim degiskenleri yazilamiyor.")
    if IS_LINUX:
        path = _linux_var_path(name, guid)
        payload = int(attributes).to_bytes(4, "little") + bytes(data)
        _linux_clear_immutable(path)
        try:
            # Oznitelik ve veri **tek yazmada** gitmelidir: efivarfs parcali
            # yazmayi kabul etmez.
            handle = os.open(path, os.O_WRONLY | os.O_CREAT, 0o644)
            try:
                os.write(handle, payload)
            finally:
                os.close(handle)
            return True, ""
        except OSError as exc:
            return False, str(exc)
    if IS_WINDOWS:
        try:
            import ctypes
            import ctypes.wintypes as wt

            k32 = ctypes.windll.kernel32
            k32.SetFirmwareEnvironmentVariableExW.argtypes = [
                wt.LPCWSTR, wt.LPCWSTR, wt.LPVOID, wt.DWORD, wt.DWORD]
            k32.SetFirmwareEnvironmentVariableExW.restype = wt.BOOL
            payload = ctypes.create_string_buffer(bytes(data), len(data)) \
                if data else None
            ok = k32.SetFirmwareEnvironmentVariableExW(
                name, _win_guid(guid), payload, len(data), int(attributes))
            if ok:
                return True, ""
            return False, tr("Windows hata kodu {}", k32.GetLastError())
        except Exception as exc:                    # noqa: BLE001
            return False, str(exc)
    return False, tr("Bu platformda bellenim degiskeni yazilamiyor.")


def efivar_delete(name: str, guid: str) -> Tuple[bool, str]:
    """Bir degiskeni siler (bos veri yazmak silme anlamina gelir)."""
    if IS_LINUX:
        path = _linux_var_path(name, guid)
        _linux_clear_immutable(path)
        try:
            os.unlink(path)
            return True, ""
        except OSError as exc:
            return False, str(exc)
    return efivar_write(name, guid, 0, b"")
