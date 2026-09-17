"""Onyukleyici cozumlemesi — hangi diskte ne var, hangi bolumde hangi sistem.

## Neden bu modul var

`exxos-easy-grub-manager` bu isi kabuk araclariyla yapiyordu: `lsblk` ile
bolumleri listele, `mount -o ro` ile her birini bagla, dosyalara bak, `dd |
strings | grep GRUB` ile MBR'ye bak. Bu yaklasim yalnizca **Linux'ta**, yalnizca
**root**ken ve yalnizca **calisan makinede** ise yarar.

DiskUltimate'in kendi dosya sistemi surucileri (FAT, exFAT, NTFS, ext2/3/4)
zaten sektorden okuyabiliyor. Bu modul onlari kullanir; sonuc:

- **Her platformda calisir** — Windows'ta da bir Linux kokunu tanir.
- **Baglama gerekmez** — bolum bagli olmasa da okunur, yetki gereksinimi
  yalnizca aygiti acmaya kalir.
- **Goruntu dosyalarinda da calisir** — `.img` icindeki sistemler gorunur.
- Test edilebilir: uretilmis bir goruntu uzerinde dogrulanabilir.

## Ne yapmaz

Disk **yazmaz**. GRUB kurmak (`grub-install`) ya da yapilandirma uretmek
(`update-grub`) calisan sistemin araclarini gerektirir; onlar
`core/platform.py` icindedir. Buradaki her sey **okuma**dir.

Gerekce: `.claude/decisions/0028-onyukleyici-yonetimi.md`
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from ..i18n import mark, tr

# MBR'nin ilk 440 bayti onyukleme kodudur; gerisi bolum tablosu ve imzadir.
BOOT_CODE_BYTES = 440
MBR_SIGNATURE_OFFSET = 440
PARTITION_TABLE_OFFSET = 446

# EFI Sistem Bolumu — GPT tur GUID'i ve MBR tur bayti
ESP_GUID = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
ESP_TYPE_ID = 0xEF

# Onyukleme kodu turleri. Anahtar koda gomulu, ad kullaniciya gosterilir.
BOOT_CODE_NAMES = {
    "grub2": mark("GRUB 2"),
    "grub-legacy": mark("GRUB Legacy"),
    "windows": mark("Windows onyukleyicisi"),
    "syslinux": mark("SYSLINUX / ISOLINUX"),
    "lilo": mark("LILO"),
    "freebsd": mark("FreeBSD onyukleyicisi"),
    "empty": mark("Bos (onyukleme kodu yok)"),
    "unknown": mark("Taninmayan onyukleme kodu"),
}

# Imza taramasi: (aranan bayt dizisi, tur). Sira onemlidir — ilk eslesen kazanir.
# Dizeler onyukleyicilerin kendi hata iletileridir ve surumler arasi degismez;
# bu yuzden surum numarasindan daha guvenilir bir parmak izidirler.
_SIGNATURES = [
    (b"GRUB \x00Geom\x00Hard Disk\x00Read\x00 Error", "grub2"),
    (b"GRUB \x00Geom\x00Read\x00 Error", "grub-legacy"),
    (b"Loading stage1.5", "grub-legacy"),
    (b"GRUB", "grub2"),
    (b"ISOLINUX", "syslinux"),
    (b"SYSLINUX", "syslinux"),
    (b"LILO", "lilo"),
    (b"\xfa\xeb", "unknown"),           # yalnizca sirayi bozmamak icin
]

# Windows onyukleyicisinin MBR iletileri (NT 5 / 6 / 10 hepsinde ayni).
_WINDOWS_SIGNATURES = [
    b"Invalid partition table",
    b"Error loading operating system",
    b"Missing operating system",
    b"BOOTMGR",
    b"NTLDR",
]


@dataclass
class BootCode:
    """Bir diskin ilk sektorundeki onyukleme kodunun kimligi."""

    kind: str = "unknown"
    detail: str = ""

    @property
    def label(self) -> str:
        return tr(BOOT_CODE_NAMES.get(self.kind, BOOT_CODE_NAMES["unknown"]))

    @property
    def is_grub(self) -> bool:
        return self.kind in ("grub2", "grub-legacy")

    @property
    def is_empty(self) -> bool:
        return self.kind == "empty"


def identify_boot_code(sector: bytes) -> BootCode:
    """Ilk sektorden onyukleme kodunu tanir.

    Yalnizca ilk 440 bayta bakilir: gerisi bolum tablosudur ve icindeki
    rastgele baytlar yanlis eslesme uretebilir. `dd | strings | grep GRUB`
    tam olarak bu hatayi yapiyordu.
    """
    code = bytes(sector[:BOOT_CODE_BYTES])
    if not code or not any(code):
        return BootCode("empty")
    for pattern in _WINDOWS_SIGNATURES:
        if pattern in code:
            return BootCode("windows", pattern.decode("ascii", "replace"))
    for pattern, kind in _SIGNATURES:
        if pattern == b"\xfa\xeb":
            continue
        if pattern in code:
            return BootCode(kind, pattern.split(b"\x00")[0].decode("ascii",
                                                                  "replace"))
    if b"\xeb\x58\x90" in code[:4] or b"BTX" in code:
        return BootCode("freebsd")
    return BootCode("unknown")


def clear_boot_code(sector: bytes) -> bytes:
    """Ilk 440 bayti sifirlar; bolum tablosunu ve imzayi **korur**.

    Onyukleyiciyi kaldirmanin dogru yolu budur. `dd if=/dev/zero of=... bs=512
    count=1` yazmak bolum tablosunu da silerdi ve diskteki butun veriyi
    erisilmez kilardi.
    """
    body = bytearray(sector)
    if len(body) < BOOT_CODE_BYTES:
        body.extend(b"\x00" * (BOOT_CODE_BYTES - len(body)))
    body[0:BOOT_CODE_BYTES] = b"\x00" * BOOT_CODE_BYTES
    return bytes(body)


# ==========================================================================
# Dosya sisteminde yol arama (buyuk/kucuk harf duyarsiz)
# ==========================================================================
def _child(fs, path: str, name: str) -> str:
    """`path` icinde `name` adli girdiyi harf buyuklugunu yok sayarak arar."""
    try:
        entries = fs.listdir(path)
    except Exception:
        return ""
    lowered = name.lower()
    for node in entries:
        if node.name.lower() == lowered:
            return node.path
    return ""


def find_path(fs, *names: str) -> str:
    """Ic ice girdileri harf buyuklugunu yok sayarak izler.

    NTFS'te `Windows`, FAT'te `WINDOWS`, ext4'te `windows` olabilir; ayni
    aramanin uc yazimi vardir. Yoksa bos dize doner.
    """
    current = "/"
    for name in names:
        current = _child(fs, current, name)
        if not current:
            return ""
    return current


def read_text(fs, path: str, limit: int = 65536) -> str:
    """Bolumden kucuk bir metin dosyasi okur (okunamazsa bos dize).

    Kodlama **her zaman UTF-8**'dir ve bozuk baytlar degistirilir: bir
    yapilandirma dosyasindaki tek bozuk bayt yuzunden tespitin cokmesi kabul
    edilemez.
    """
    try:
        raw = fs.read(path, limit)
    except Exception:
        return ""
    return raw.decode("utf-8", "replace")


# ==========================================================================
# Bolumdeki isletim sistemi
# ==========================================================================
@dataclass
class OsInstall:
    """Bir bolumde bulunan isletim sistemi (ya da bulunmama nedeni)."""

    # `Partition.index` ile ayni: **1 tabanli** bolum numarasi. Liste konumu
    # degildir — `session.filesystem()` de bu numarayi bekler ve ikisini
    # karistirmak yanlis bolumu acar.
    index: int = -1
    device: str = ""                 # gosterim adi
    fs_type: str = ""
    label: str = ""
    size: int = 0
    os_name: str = ""                # bos = isletim sistemi yok
    os_kind: str = ""                # windows | linux | macos | esp | ''
    reason: str = ""                 # os_name bossa neden
    loaders: List[str] = field(default_factory=list)   # ESP'deki .efi dosyalari
    is_esp: bool = False

    @property
    def bootable(self) -> bool:
        return bool(self.os_name)


# Linux dagitimlarinin surum dosyalari; sirayla denenir.
_RELEASE_FILES = ("os-release", "lsb-release", "mx-version", "debian_version",
                  "redhat-release", "SuSE-release", "arch-release")


def _linux_name(fs) -> str:
    """Bagli olmayan bir Linux kokunden dagitim adini cikarir."""
    path = find_path(fs, "etc", "os-release")
    if path:
        for line in read_text(fs, path).splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
        for line in read_text(fs, path).splitlines():
            if line.startswith("NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    path = find_path(fs, "etc", "lsb-release")
    if path:
        for line in read_text(fs, path).splitlines():
            if line.startswith("DISTRIB_DESCRIPTION="):
                return line.split("=", 1)[1].strip().strip('"')
    path = find_path(fs, "etc", "mx-version")
    if path:
        first = read_text(fs, path, 256).splitlines()
        if first:
            return first[0].strip()
    return tr("Linux")


def _windows_name(fs, label: str) -> str:
    """Windows kurulumunun adi (etiket varsa onunla birlikte)."""
    return f"Windows ({label})" if label else "Windows"


def detect_os(fs, fs_type: str = "", label: str = "") -> Dict[str, str]:
    """Acik bir dosya sisteminde isletim sistemi arar.

    Doner: `{"name": ..., "kind": ..., "reason": ...}`. `name` bossa `reason`
    neden bulunamadigini soyler — "bulamadim" demek yetmez, kullanici neyin
    atlandigini bilmelidir.

    Dosya sisteminin turu **kanit degildir**: ext4 bir bolum, kurulu bir
    sistem kadar kolaylikla bir yedek diski olabilir. Bu yuzden her aday
    acilir ve gercek bir kurulumun tasidigi dosyalar aranir.
    """
    if fs is None:
        return {"name": "", "kind": "",
                "reason": tr("Dosya sistemi okunamadi")}

    kind = (fs_type or getattr(fs, "fs_type", "") or "").lower()

    # -- Windows ----------------------------------------------------------
    if kind.startswith("ntfs"):
        if find_path(fs, "Windows", "System32", "config", "SYSTEM"):
            return {"name": _windows_name(fs, label), "kind": "windows",
                    "reason": ""}
        if find_path(fs, "bootmgr") and find_path(fs, "Boot", "BCD"):
            return {"name": tr("Windows onyukleme bolumu"), "kind": "windows",
                    "reason": ""}
        return {"name": "", "kind": "",
                "reason": tr("Isletim sistemi kurulu degil (veri bolumu)")}

    # -- Linux ------------------------------------------------------------
    if kind.startswith("ext") or kind in ("btrfs", "xfs", "f2fs"):
        etc = find_path(fs, "etc")
        if not etc:
            return {"name": "", "kind": "",
                    "reason": tr("Kok dosya sistemi degil ({} yok)", "/etc")}
        for name in _RELEASE_FILES:
            if find_path(fs, "etc", name):
                return {"name": _linux_name(fs), "kind": "linux", "reason": ""}
        return {"name": "", "kind": "",
                "reason": tr("Isletim sistemi kurulu degil (veri bolumu)")}

    # -- EFI Sistem Bolumu -------------------------------------------------
    if kind.startswith("fat"):
        if find_path(fs, "EFI"):
            return {"name": tr("EFI Sistem Bolumu"), "kind": "esp", "reason": ""}
        return {"name": "", "kind": "",
                "reason": tr("Isletim sistemi kurulamayan bir dosya sistemi")}

    return {"name": "", "kind": "",
            "reason": tr("Isletim sistemi kurulamayan bir dosya sistemi")}


def list_efi_loaders(fs, limit: int = 64) -> List[str]:
    """ESP uzerindeki `\\EFI\\<uretici>\\*.efi` dosyalarini listeler.

    UEFI'de her isletim sistemi kendi klasorunu acar (`\\EFI\\ubuntu`,
    `\\EFI\\Microsoft`); bulunan dosyalar onyukleme girisi olustururken
    dogrudan secilebilir.
    """
    root = find_path(fs, "EFI")
    if not root:
        return []
    found: List[str] = []
    try:
        vendors = fs.listdir(root)
    except Exception:
        return []
    for vendor in vendors:
        if not vendor.is_dir:
            continue
        try:
            entries = fs.listdir(vendor.path)
        except Exception:
            continue
        for node in entries:
            if node.is_dir or not node.name.lower().endswith(".efi"):
                continue
            found.append("\\EFI\\" + vendor.name + "\\" + node.name)
            if len(found) >= limit:
                return found
    return found


def is_esp(partition) -> bool:
    """Bolum bir EFI Sistem Bolumu mu (tur kimliginden)?"""
    guid = (getattr(partition, "type_guid", "") or "").upper()
    if guid and guid.replace("{", "").replace("}", "") == ESP_GUID:
        return True
    return getattr(partition, "type_id", 0) == ESP_TYPE_ID


# ==========================================================================
# GRUB yapilandirmasi
# ==========================================================================
class GrubDefaults:
    """`GRUB_...=deger` bicimindeki varsayilanlar dosyasi.

    Dosya **satir satir** tutulur: yorumlar, bos satirlar ve sira korunur.
    Sozluge cevirip geri yazmak kullanicinin kendi notlarini silerdi — bir
    yapilandirma dosyasini duzenleyen aracin en cok kizdiran davranisi budur.
    """

    def __init__(self, text: str = ""):
        self.lines: List[str] = text.splitlines() if text else []

    def get(self, key: str, default: str = "") -> str:
        for line in self.lines:
            stripped = line.strip()
            if stripped.startswith(key + "="):
                return _unquote(stripped.split("=", 1)[1])
        return default

    def is_commented(self, key: str) -> bool:
        """Anahtar yalnizca yorum satirinda mi geciyor?"""
        active = any(line.strip().startswith(key + "=") for line in self.lines)
        commented = any(line.strip().lstrip("#").strip().startswith(key + "=")
                        for line in self.lines)
        return commented and not active

    def set(self, key: str, value: str) -> None:
        """Anahtari yazar: varsa yerinde gunceller, yorumdaysa acar, yoksa ekler."""
        text = f"{key}={value}"
        for index, line in enumerate(self.lines):
            if line.strip().startswith(key + "="):
                self.lines[index] = text
                return
        for index, line in enumerate(self.lines):
            if line.strip().lstrip("#").strip().startswith(key + "="):
                self.lines[index] = text
                return
        if self.lines and self.lines[-1].strip():
            self.lines.append("")
        self.lines.append(text)

    def render(self) -> str:
        return "\n".join(self.lines) + "\n"

    def as_dict(self) -> Dict[str, str]:
        """Etkin (yoruma alinmamis) butun anahtarlar."""
        found: Dict[str, str] = {}
        for line in self.lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            if key.strip():
                found[key.strip()] = _unquote(value)
        return found


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


@dataclass
class MenuEntry:
    """`grub.cfg` icindeki tek bir menu satiri."""

    title: str = ""
    classes: List[str] = field(default_factory=list)
    submenu: str = ""
    index: int = 0

    @property
    def display(self) -> str:
        return f"{self.submenu} > {self.title}" if self.submenu else self.title


def parse_grub_cfg(text: str) -> List[MenuEntry]:
    """`grub.cfg` icindeki menu girislerini cikarir.

    Tam bir kabuk cozumleyicisi degildir — `grub.cfg` bir betiktir ve butunuyle
    yorumlanamaz. Amac menude **ne gorunecegini** gostermektir; bunun icin
    `menuentry` ve `submenu` satirlarinin basliklari yeter.
    """
    entries: List[MenuEntry] = []
    stack: List[str] = []
    index = 0
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("}") and stack:
            stack.pop()
            continue
        for keyword in ("menuentry", "submenu"):
            if not stripped.startswith(keyword + " "):
                continue
            title = _quoted(stripped[len(keyword):])
            if keyword == "submenu":
                stack.append(title)
            else:
                entries.append(MenuEntry(title=title,
                                         classes=_classes(stripped),
                                         submenu=" > ".join(stack),
                                         index=index))
                index += 1
                # Tek satirlik `menuentry ... { ... }` yazimi yigini bozmasin.
                if stripped.endswith("}"):
                    continue
            break
    return entries


def _quoted(text: str) -> str:
    """Satirdaki ilk tirnakli dizeyi dondurur."""
    for quote in ("'", '"'):
        start = text.find(quote)
        if start < 0:
            continue
        end = text.find(quote, start + 1)
        if end > start:
            return text[start + 1:end]
    return text.strip().split("{")[0].strip()


def _classes(text: str) -> List[str]:
    """`--class ubuntu --class gnu-linux` bicimindeki sinif adlari."""
    found = []
    parts = text.split()
    for index, part in enumerate(parts):
        if part == "--class" and index + 1 < len(parts):
            found.append(parts[index + 1])
    return found


# ==========================================================================
# Disk ozeti
# ==========================================================================
@dataclass
class DiskBoot:
    """Bir diskin onyukleme acisindan durumu."""

    path: str = ""
    name: str = ""
    size: int = 0
    scheme: str = ""
    boot_code: BootCode = field(default_factory=BootCode)
    systems: List[OsInstall] = field(default_factory=list)
    esp_index: int = -1
    readable: bool = True
    reason: str = ""                  # okunamadiysa neden

    @property
    def has_grub(self) -> bool:
        return self.boot_code.is_grub

    @property
    def os_names(self) -> List[str]:
        return [s.os_name for s in self.systems if s.os_name]

    def summary(self) -> str:
        if not self.readable:
            return self.reason or tr("Disk okunamadi")
        names = self.os_names
        return ", ".join(names) if names else tr("Isletim sistemi bulunamadi")


def survey_session(session, progress=None) -> DiskBoot:
    """Acik bir oturumdaki diski onyukleme acisindan inceler.

    Her bolum acilir, dosya sistemi taninir ve icinde gercek bir kurulum olup
    olmadigina bakilir. Uzun surebilir (NTFS ve ext dizin okumasi) — bu yuzden
    `progress` alir ve arayuzden **is parcaciginda** cagrilir.
    """
    result = DiskBoot(path=session.path, name=session.name,
                      size=getattr(session.image, "size", 0),
                      scheme=session.scheme)
    try:
        result.boot_code = identify_boot_code(session.read_sector(0))
    except Exception as exc:
        result.readable = False
        result.reason = str(exc)
        return result

    partitions = session.partitions
    total = max(1, len(partitions))
    for position, part in enumerate(partitions):
        number = part.index                 # 1 tabanli; liste konumu DEGIL
        if progress:
            progress(tr("Bolum {} inceleniyor...", number),
                     int(position * 100 / total))
        entry = OsInstall(index=number,
                          device=getattr(part, "name", "") or tr("Bolum {}",
                                                                 number),
                          size=getattr(part, "size_bytes", 0))
        try:
            info = session.detect_fs(part)
            entry.fs_type = info.fs_type
            entry.label = info.label
        except Exception as exc:
            entry.reason = str(exc)
            result.systems.append(entry)
            continue

        entry.is_esp = is_esp(part) or (entry.fs_type.startswith("FAT")
                                        and _looks_like_esp(session, number))
        try:
            fs = session.filesystem(number)
        except Exception as exc:
            entry.reason = str(exc)
            result.systems.append(entry)
            continue

        found = detect_os(fs, entry.fs_type, entry.label)
        entry.os_name = found["name"]
        entry.os_kind = found["kind"]
        entry.reason = found["reason"]
        if entry.is_esp or found["kind"] == "esp":
            entry.is_esp = True
            entry.loaders = list_efi_loaders(fs)
            if result.esp_index < 0:
                result.esp_index = number
        result.systems.append(entry)

    if progress:
        progress(tr("Tamamlandi"), 100)
    return result


def _looks_like_esp(session, index: int) -> bool:
    """Tur kimligi yanlis olsa da FAT bolumde `\\EFI` klasoru var mi?"""
    try:
        fs = session.filesystem(index)
    except Exception:
        return False
    return bool(fs is not None and find_path(fs, "EFI"))
