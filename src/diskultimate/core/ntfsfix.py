"""NTFS denetimi ve onarimi — `ntfsfix`in yaptigi isin saf Python karsiligi.

## Neden

Windows "Hizli baslatma" (Fast Startup) ile kapatildiginda, hazirda
bekletmeye girdiginde ya da elektrik kesildiginde NTFS birimi **temiz
ayrilmamis** kalir:

  * `$Volume` icindeki **kirli** bayragi (0x0001) acik kalir — Linux'un
    cekirdek surucusu `ntfs3` boyle bir birimi baglamayi reddeder;
  * `$LogFile` "temiz" isaretlenmemistir — `ntfs-3g` "Metadata kept in
    Windows cache, refused to mount" der;
  * sistem biriminde `hiberfil.sys` hazirda bekletme imzasi tasir.

Kullanici bunu 2026-10-02'de kendi makinesinde yasadi: veri bolumu Linux'ta
gorunmuyor ve baglanmiyordu; `sudo ntfsfix -d <bolum aygiti>` ile acildi.
Bu modul ayni isi, terminale inmeden ve **her platformda** yapar.

## Ne yapar (sirayla — `ntfsfix` ile ayni sira)

1. **Onyukleme sektoru** — asil bozuk, yedek saglamsa yedekten geri yazilir;
   asil saglam, yedek farkliysa yedek tazelenir.
2. **`$MFT` / `$MFTMirr`** — ilk kayitlar karsilastirilir; farkliysa saglam
   olan digerine kopyalanir.
3. **`$LogFile` bosaltilir** (0xFF + temiz yeniden baslatma alani, ADR 0085).
   Windows'un yarim kalan gunlugu artik yeniden oynatilmaz. Zaten temiz ya da
   bos gunluge dokunulmaz.
4. **Kirli bayragi** temizlenir (`ntfsfix -d`) ya da istenirse **acilir**:
   Windows bir sonraki acilista `chkdsk` calistirir (`ntfsfix`in varsayilani).
5. Istenirse **`hiberfil.sys` gecersiz kilinir**: Windows kaydedilmis oturuma
   donmez, soguk acilir.

## Ne yapmaz

Bu bir `chkdsk` degildir: dizin agacini, guvenlik tanimlayicilarini ya da
dosya kayitlarinin tutarliligini denetlemez. `ntfsfix` de yapmaz. Amac
birimi **baglanabilir** hale getirmektir; ardindan Windows'ta `chkdsk`
calistirilmasi onerilir.

## Risk

Gunluk bosaltildiginda Windows'un henuz diske islemedigi son ustveri
degisiklikleri kaybolur. Windows hazirda bekletmedeyse (Hizli baslatma dahil)
birime yazip sonra Windows'u kaldigi yerden acmak **birimi bozar** — bu
yuzden hazirda bekletme imzasi gorulurse onarim, kullanici hiberfil'i
gecersiz kilmayi acikca secmedikce **reddedilir** (`ntfs-3g`in
`remove_hiberfile` kurali).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from .image import BlockDevice
from .ntfs import fill_logfile
from .ntfsread import AT_DATA, NtfsError, NtfsFS
from ..i18n import tr

Progress = Optional[Callable[[str, int], None]]

MFT_RECORD = 0
MFTMIRR_RECORD = 1
LOGFILE_RECORD = 2
VOLUME_RECORD = 3
AT_VOLUME_INFORMATION = 0x70

VOLUME_DIRTY = 0x0001
NTFS_BLOCK = 512                 # guncelleme dizisi adimi (fixup), sabit
LOGFILE_NO_CLIENT = 0xFFFF
RESTART_VOLUME_IS_CLEAN = 0x0002
HIBERFILE_HEADER = 4096          # ntfs-3g'nin okudugu baslik boyu

# Gunluk durumu
LOG_EMPTY = "empty"              # 0xFF ile dolu — bos gunluk, temiz
LOG_CLEAN = "clean"              # yeniden baslatma alani "temiz"
LOG_UNCLEAN = "unclean"          # Windows ayrilmadan kapatildi
LOG_UNKNOWN = "unknown"          # yeniden baslatma sayfasi okunamadi

# Onyukleme sektoru durumu
BOOT_OK = "ok"
BOOT_DIFFERS = "differs"         # yedek asildan farkli
BOOT_BAD = "bad"                 # okunamiyor / NTFS degil


class NtfsFixError(NtfsError):
    """Onarim reddedildi ya da yarida kaldi."""


@dataclass
class NtfsHealth:
    """Bir NTFS biriminin "temiz mi" ozeti. Hicbir sey yazilmadan cikarilir."""

    primary_boot: str = BOOT_OK
    backup_boot: str = BOOT_OK
    mirror_records: int = 0             # karsilastirilan kayit sayisi
    mirror_mismatch: List[int] = field(default_factory=list)
    mirror_error: str = ""
    dirty: Optional[bool] = None        # None = okunamadi
    volume_flags: int = 0
    version: str = ""
    logfile: str = LOG_UNKNOWN
    hibernated: bool = False            # hiberfil.sys hazirda bekletme imzasi
    error: str = ""                     # birim hic acilamadiysa nedeni

    @property
    def needs_repair(self) -> bool:
        return bool(self.dirty or self.logfile in (LOG_UNCLEAN, LOG_UNKNOWN)
                    or self.mirror_mismatch or self.primary_boot != BOOT_OK
                    or self.backup_boot != BOOT_OK)

    @property
    def repairable(self) -> bool:
        """Onarim baslatilabilir mi (hazirda bekletme ayri sorulur)."""
        return self.primary_boot == BOOT_OK or self.backup_boot == BOOT_OK

    @property
    def blocks_linux_mount(self) -> bool:
        """Linux suruculerinin (ntfs3 / ntfs-3g) baglamayi reddedecegi durum."""
        return bool(self.dirty or self.hibernated
                    or self.logfile in (LOG_UNCLEAN, LOG_UNKNOWN))

    def problems(self) -> List[str]:
        """Kullaniciya gosterilecek bulgular (etkin dilde)."""
        out: List[str] = []
        if self.error:
            out.append(self.error)
        if self.primary_boot == BOOT_BAD:
            if self.backup_boot == BOOT_OK:
                out.append(tr("Onyukleme sektoru bozuk; yedegi saglam"))
            else:
                out.append(tr("Onyukleme sektoru ve yedegi okunamiyor"))
        elif self.backup_boot != BOOT_OK:
            out.append(tr("Yedek onyukleme sektoru eksik veya farkli"))
        if self.mirror_mismatch:
            out.append(tr("$MFTMirr, $MFT ile uyusmuyor (kayit {})",
                          ", ".join(str(n) for n in self.mirror_mismatch)))
        if self.mirror_error:
            out.append(self.mirror_error)
        if self.dirty:
            out.append(tr("Birim 'kirli' isaretli (temiz ayrilmamis)"))
        if self.logfile == LOG_UNCLEAN:
            out.append(tr("Islem gunlugu ($LogFile) temiz kapatilmamis"))
        elif self.logfile == LOG_UNKNOWN and not self.error:
            out.append(tr("Islem gunlugu ($LogFile) okunamadi"))
        if self.hibernated:
            out.append(tr("Windows hazirda bekletmede (Hizli baslatma dahil)"))
        return out


# ==========================================================================
# Yardimcilar
# ==========================================================================
def _boot_ok(boot: bytes, dev_bytes: int) -> bool:
    """Bir NTFS onyukleme sektoru akla yatkin mi (ntfs-3g'nin olcutleri)."""
    if len(boot) < 512 or boot[3:11] != b"NTFS    " or boot[510:512] != b"\x55\xAA":
        return False
    ss = struct.unpack_from("<H", boot, 0x0B)[0]
    if ss < 256 or ss > 4096 or ss & (ss - 1):
        return False
    spc = boot[0x0D]
    if spc == 0 or (spc <= 0x80 and spc & (spc - 1)):
        return False
    total = struct.unpack_from("<Q", boot, 0x28)[0]
    if total == 0 or total * ss > dev_bytes:
        return False
    cs = ss * (spc if spc <= 0x80 else 1 << (256 - spc))
    clusters = total * ss // cs
    mft, mirr = struct.unpack_from("<QQ", boot, 0x30)
    return mft < clusters and mirr < clusters


def _geometry(boot: bytes) -> Tuple[int, int, int, int, int, int]:
    """(sektor, kume, toplam sektor, $MFT LCN, $MFTMirr LCN, kayit boyu)."""
    ss = struct.unpack_from("<H", boot, 0x0B)[0]
    spc = boot[0x0D]
    cs = ss * (spc if spc <= 0x80 else 1 << (256 - spc))
    total = struct.unpack_from("<Q", boot, 0x28)[0]
    mft, mirr = struct.unpack_from("<QQ", boot, 0x30)
    raw = boot[0x40]
    signed = raw - 256 if raw > 127 else raw
    rs = (1 << -signed) if signed < 0 else signed * cs
    return ss, cs, total, mft, mirr, rs


def _record_ok(raw: bytes) -> bool:
    """FILE kaydi saglam mi: imza, guncelleme dizisi ve her bloktaki isaret."""
    if len(raw) < 48 or raw[:4] != b"FILE":
        return False
    usa_off, usa_count = struct.unpack_from("<HH", raw, 4)
    if usa_count < 2 or usa_off < 0x28 or usa_off + usa_count * 2 > len(raw):
        return False
    if (usa_count - 1) * NTFS_BLOCK != len(raw):
        return False
    marker = raw[usa_off:usa_off + 2]
    return all(raw[i * NTFS_BLOCK - 2:i * NTFS_BLOCK] == marker
               for i in range(1, usa_count))


def _mirror_count(cs: int, rs: int) -> int:
    """`$MFTMirr`deki kayit sayisi (ntfs-3g: `mftmirr_size`)."""
    return 4 if cs <= 4 * rs else cs // rs


def _read_boots(dev: BlockDevice) -> Tuple[bytes, bytes, int]:
    """(asil, yedek, yedegin bayt ofseti). Yedek, asildaki toplam sektor
    sayisinin gosterdigi yerdedir; asil bozuksa aygitin son sektoru denenir."""
    size = dev.size
    primary = dev.read(0, 512)
    if _boot_ok(primary, size):
        ss = struct.unpack_from("<H", primary, 0x0B)[0]
        total = struct.unpack_from("<Q", primary, 0x28)[0]
        off = total * ss
    else:
        ss = getattr(dev, "sector_size", 512) or 512
        off = size - ss
    backup = dev.read(off, 512) if 0 <= off and off + 512 <= size else b""
    return primary, backup, off


def logfile_state(fs: NtfsFS) -> str:
    """`$LogFile`in durumu: bos / temiz / temiz degil / bilinmiyor.

    ntfs-3g ile ayni olcut: yeniden baslatma alaninda etkin istemci varsa ve
    "birim temiz" bayragi yoksa gunluk temiz degildir. Iki yeniden baslatma
    sayfasindan `current_lsn`i buyuk olan gecerlidir.
    """
    try:
        attr = fs.record(LOGFILE_RECORD).find(AT_DATA)
    except NtfsError:
        return LOG_UNKNOWN
    if attr is None or attr.resident:
        return LOG_UNKNOWN
    head = fs.read_attribute_range(attr, 0, 8192)
    if not head:
        return LOG_UNKNOWN
    pages = []
    for page_off in (0, 4096):
        if head[page_off:page_off + 4] == b"RSTR":
            pages.append(page_off)
    if not pages:
        page = struct.unpack_from("<I", head, 0x10)[0] if len(head) > 0x14 else 4096
        probe = fs.read_attribute_range(attr, 0, min(2 * max(page, 4096), 65536))
        return LOG_EMPTY if probe and probe.count(0xFF) == len(probe) else LOG_UNKNOWN
    best = None
    for page_off in pages:
        page = bytearray(head[page_off:page_off + 4096])
        try:
            fs._apply_fixup(page)
        except NtfsError:
            continue
        ra = struct.unpack_from("<H", page, 0x18)[0]
        if ra + 16 > len(page):
            continue
        lsn = struct.unpack_from("<Q", page, ra)[0]
        in_use = struct.unpack_from("<H", page, ra + 12)[0]
        flags = struct.unpack_from("<H", page, ra + 14)[0]
        if best is None or lsn > best[0]:
            best = (lsn, in_use, flags)
    if best is None:
        return LOG_UNKNOWN
    _lsn, in_use, flags = best
    if in_use != LOGFILE_NO_CLIENT and not flags & RESTART_VOLUME_IS_CLEAN:
        return LOG_UNCLEAN
    return LOG_CLEAN


def _volume_info(fs: NtfsFS):
    """(`$VOLUME_INFORMATION` oznitelugu, bayraklar, surum) — yoksa (None, 0, "")."""
    try:
        attr = fs.record(VOLUME_RECORD).find(AT_VOLUME_INFORMATION)
    except NtfsError:
        return None, 0, ""
    if attr is None or not attr.resident or len(attr.value) < 12:
        return None, 0, ""
    major, minor, flags = struct.unpack_from("<BBH", attr.value, 8)
    return attr, flags, f"{major}.{minor}"


def _hiberfile(fs: NtfsFS):
    """Kok dizindeki `hiberfil.sys`in veri oznitelugu (yoksa None)."""
    try:
        root = fs.record(5)
        entry = fs.lookup(root, "hiberfil.sys")
        if entry is None:
            return None
        return fs.record(entry.mft_ref & 0xFFFFFFFFFFFF).find(AT_DATA)
    except (NtfsError, AttributeError):
        return None


def is_hibernated(fs: NtfsFS) -> bool:
    """`hiberfil.sys` hazirda bekletme imzasi tasiyor mu ("hibr"/"HIBR").

    "wake" Windows'un oturumdan dondugunu gosterir; sifirlar bos dosyadir.
    Sistem disindaki birimlerde dosya yoktur — hazirda bekletme o zaman da
    olabilir (Windows birimi bagli birakir), bu yuzden gunluk durumu ayrica
    denetlenir.
    """
    attr = _hiberfile(fs)
    if attr is None:
        return False
    try:
        head = fs.read_attribute_range(attr, 0, 4)
    except NtfsError:
        return False
    return head in (b"hibr", b"HIBR")


# ==========================================================================
# Denetim (salt okunur)
# ==========================================================================
def quick_state(fs: NtfsFS) -> Tuple[bool, bool]:
    """(temiz ayrilmamis mi, hazirda bekletmede mi) — tespit icin kisa yol.

    Acik bir `NtfsFS` uzerinden birkac okuma yapar (`$Volume`, `$LogFile`in
    ilk 8 KiB'i, kok dizin); bolum listesi cizilirken cagrilabilir.
    """
    _attr, flags, _version = _volume_info(fs)
    unclean = bool(flags & VOLUME_DIRTY) or logfile_state(fs) in (
        LOG_UNCLEAN, LOG_UNKNOWN)
    return unclean, is_hibernated(fs)


def ntfs_check(dev: BlockDevice) -> NtfsHealth:
    """Birimi okuyup sagligini cikarir; **hicbir sey yazmaz**."""
    health = NtfsHealth()
    primary, backup, _off = _read_boots(dev)
    size = dev.size
    health.primary_boot = BOOT_OK if _boot_ok(primary, size) else BOOT_BAD
    if not _boot_ok(backup, size):
        health.backup_boot = BOOT_BAD
    elif health.primary_boot == BOOT_OK and backup[:512] != primary[:512]:
        health.backup_boot = BOOT_DIFFERS
    boot = primary if health.primary_boot == BOOT_OK else backup
    if not _boot_ok(boot, size):
        health.error = tr("NTFS onyukleme sektoru bulunamadi")
        return health

    _ss, cs, _total, mft, mirr, rs = _geometry(boot)
    count = _mirror_count(cs, rs)
    health.mirror_records = count
    a = dev.read(mft * cs, count * rs)
    b = dev.read(mirr * cs, count * rs)
    for i in range(count):
        if a[i * rs:(i + 1) * rs] != b[i * rs:(i + 1) * rs]:
            health.mirror_mismatch.append(i)
    if not _record_ok(a[:rs]) and not _record_ok(b[:rs]):
        health.mirror_error = tr("$MFT ve $MFTMirr'in ilk kaydi bozuk")
        health.error = health.mirror_error
        return health

    if health.primary_boot != BOOT_OK or MFT_RECORD in health.mirror_mismatch:
        # Asil yapilar bozukken okuyucu acilamaz; onarimdan sonra bakilir.
        return health
    try:
        fs = NtfsFS(dev)
    except NtfsError as exc:
        health.error = str(exc)
        return health
    _attr, flags, version = _volume_info(fs)
    if _attr is not None:
        health.volume_flags = flags
        health.version = version
        health.dirty = bool(flags & VOLUME_DIRTY)
    health.logfile = logfile_state(fs)
    health.hibernated = is_hibernated(fs)
    return health


# ==========================================================================
# Onarim
# ==========================================================================
@dataclass
class NtfsFixResult:
    steps: List[str] = field(default_factory=list)   # yapilanlar (etkin dilde)
    before: Optional[NtfsHealth] = None
    after: Optional[NtfsHealth] = None


def ntfs_fix(dev: BlockDevice, clear_dirty: bool = True,
             schedule_chkdsk: bool = False, remove_hibernation: bool = False,
             progress: Progress = None) -> NtfsFixResult:
    """`ntfsfix` adimlarini uygular.

    `clear_dirty`: kirli bayragini temizle (`ntfsfix -d`). `schedule_chkdsk`:
    tersine **ac** — Windows bir sonraki acilista chkdsk calistirir; ama
    Linux'un `ntfs3` surucusu o zamana kadar birimi baglamaz. Ikisi birlikte
    istenemez. `remove_hibernation`: hazirda bekletme imzasi varsa
    `hiberfil.sys`in basligini sifirla; verilmezse onarim reddedilir.
    """
    def report(message: str, percent: int) -> None:
        if progress:
            progress(message, percent)

    if clear_dirty and schedule_chkdsk:
        raise NtfsFixError(tr("Kirli bayragi ayni anda hem temizlenip hem "
                              "acilamaz"))
    if getattr(dev, "readonly", False):
        raise NtfsFixError(tr("Kaynak salt okunur acildi."))

    result = NtfsFixResult()
    report(tr("NTFS birimi denetleniyor..."), 5)
    before = ntfs_check(dev)
    result.before = before
    if not before.repairable:
        raise NtfsFixError(before.error or tr("NTFS onyukleme sektoru bulunamadi"))
    if before.hibernated and not remove_hibernation:
        raise NtfsFixError(
            tr("Windows hazirda bekletmede (Hizli baslatma dahil). Bu birime "
               "yazip sonra Windows'u kaldigi yerden acmak birimi bozar. "
               "Windows'u acip 'Yeniden baslat' ile kapatin ya da onarimda "
               "hazirda bekletme dosyasini gecersiz kilmayi secin."))

    # 1. Onyukleme sektoru
    report(tr("Onyukleme sektoru denetleniyor..."), 15)
    primary, backup, backup_off = _read_boots(dev)
    if before.primary_boot == BOOT_BAD:
        dev.write(0, backup[:512])
        result.steps.append(tr("Onyukleme sektoru yedekten geri yazildi"))
        primary, backup, backup_off = _read_boots(dev)
    elif before.backup_boot != BOOT_OK:
        if backup_off + 512 <= dev.size:
            dev.write(backup_off, primary[:512])
            result.steps.append(tr("Yedek onyukleme sektoru yeniden yazildi"))

    # 2. $MFT / $MFTMirr
    report(tr("$MFT ve $MFTMirr karsilastiriliyor..."), 30)
    _ss, cs, _total, mft, mirr, rs = _geometry(primary)
    count = _mirror_count(cs, rs)
    for i in range(count):
        a = dev.read(mft * cs + i * rs, rs)
        b = dev.read(mirr * cs + i * rs, rs)
        if a == b:
            continue
        if _record_ok(a):
            dev.write(mirr * cs + i * rs, a)
            result.steps.append(tr("$MFTMirr kaydi {} $MFT'den duzeltildi", i))
        elif _record_ok(b):
            dev.write(mft * cs + i * rs, b)
            result.steps.append(tr("$MFT kaydi {} $MFTMirr'den duzeltildi", i))
        else:
            raise NtfsFixError(tr("$MFT kaydi {} her iki kopyada da bozuk; "
                                  "Windows'ta chkdsk gerekli", i))

    fs = NtfsFS(dev)
    from .ntfswrite import NtfsWriter
    writer = NtfsWriter(fs)

    # 3. Islem gunlugu
    report(tr("Islem gunlugu ($LogFile) bosaltiliyor..."), 50)
    state = logfile_state(fs)
    if state not in (LOG_EMPTY, LOG_CLEAN):
        _reset_logfile(fs, progress)
        result.steps.append(tr("Islem gunlugu ($LogFile) bosaltildi"))

    # 4. Kirli bayragi
    report(tr("Birim bayraklari ayarlaniyor..."), 85)
    attr, flags, _version = _volume_info(fs)
    if attr is not None:
        new_flags = flags
        if clear_dirty:
            new_flags &= ~VOLUME_DIRTY
        elif schedule_chkdsk:
            new_flags |= VOLUME_DIRTY
        if new_flags != flags:
            value = bytearray(attr.value)
            struct.pack_into("<H", value, 10, new_flags)
            writer._patch_resident(VOLUME_RECORD, attr, bytes(value))
            result.steps.append(
                tr("Kirli bayragi temizlendi") if clear_dirty else
                tr("Windows'un bir sonraki acilisinda chkdsk calisacak"))

    # 5. Hazirda bekletme
    if before.hibernated and remove_hibernation:
        report(tr("Hazirda bekletme dosyasi gecersiz kiliniyor..."), 92)
        _invalidate_hiberfile(fs)
        result.steps.append(tr("hiberfil.sys gecersiz kilindi; Windows soguk "
                               "acilacak"))

    flush = getattr(dev, "flush", None)
    if flush:
        flush()
    report(tr("Sonuc denetleniyor..."), 97)
    result.after = ntfs_check(dev)
    report(tr("Tamamlandi"), 100)
    return result


def _reset_logfile(fs: NtfsFS, progress: Progress = None) -> None:
    """`$LogFile`i bosaltir (`ntfs_logfile_reset` gibi) ve temiz yeniden
    baslatma alani yazar."""
    attr = fs.record(LOGFILE_RECORD).find(AT_DATA)
    if attr is None or attr.resident:
        raise NtfsFixError(tr("$LogFile okunamadi"))
    def report(done: int, total: int) -> None:
        if progress:
            progress(tr("Islem gunlugu ($LogFile) bosaltiliyor..."),
                     50 + 30 * done // total)

    # 0xFF + temiz yeniden baslatma alani: Windows salt okunur da baglar (ADR 0085)
    fill_logfile(fs.dev.write, attr.runs, fs.cluster_size, attr.data_size,
                 progress=report)


def _invalidate_hiberfile(fs: NtfsFS) -> None:
    """`hiberfil.sys`in ilk 4 KiB'ini sifirlar; dosyanin boyu degismez.

    Windows onyukleyicisi gecerli bir hazirda bekletme basligi bulamazsa
    kaydedilmis oturumu atar ve soguk acar — `powercfg /h off` sonrasi
    durumla ayni. Dosyayi silmekten (ntfs-3g `remove_hiberfile`) daha az
    ustveri degistirir.
    """
    attr = _hiberfile(fs)
    if attr is None:
        return
    if attr.resident:
        raise NtfsFixError(tr("hiberfil.sys beklenmeyen bicimde"))
    cs = fs.cluster_size
    left = HIBERFILE_HEADER
    for lcn, count in attr.runs:
        if left <= 0:
            break
        n = min(count * cs, left)
        if lcn >= 0:
            fs.dev.write(lcn * cs, b"\x00" * n)
        left -= n
