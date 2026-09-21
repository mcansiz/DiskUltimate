"""GRUB yonetimi — kurulum, yapilandirma uretimi, yedekleme ve onarim.

## Bolunme

| Nerede | Ne |
|---|---|
| `core/bootloader.py` | **Inceleme** — her platformda, yazmadan |
| `core/grub.py` (burasi) | **Degistirme** — yalnizca calisan Linux sisteminde |
| `core/platform.py` | Arac yollari, yetki yukseltme, sistem dosyasi yazma |

Bu ayrim bilerek yapildi. Bir diskteki GRUB'u **gormek** icin o diski okumak
yeter; DiskUltimate bunu Windows'ta da yapar. GRUB'u **kurmak** icin
`grub-install` gerekir ve o arac kurulacak sistemin modullerini, hedef diskin
BIOS/UEFI kipini ve `/boot` icerigini bilir. Bunu yeniden yazmak, calisan bir
makineyi acilmaz birakma riskini uzerimize almak olurdu; yapmiyoruz.

## Neden `grub-install` kuyruga girmez

Bekleyen islem kuyrugu (ADR 0025) **acik bir diske yazilan** adimlari tasir.
`grub-install` acik diske degil, calisan sisteme is yaptirir: paket yoneticisi
dosyalarini okur, `/boot` altina modul yazar, aygita gomer. Kuyruga koymak
kuyrugun anlamini bozardi. Onyukleme kodunun **kaldirilmasi** ise gercekten
sektor yazmadir ve kuyruga girer (`operations.clear_boot_code`).

Gerekce: `.claude/decisions/0028-onyukleyici-yonetimi.md`
"""
from __future__ import annotations

import datetime
import json
import os
import shutil
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import diagnostics, platform
from .bootloader import GrubDefaults, parse_grub_cfg
from ..i18n import tr

# Yedeklerin durdugu klasor: kullanicinin ayar klasoru altinda.
BACKUP_DIR_NAME = "grub-backups"

# `/etc/default/grub` icindeki, os-prober'i kapatan anahtar. Debian 12 ve
# sonrasinda varsayilan olarak `true`dur; bu yuzden yeni kurulumlarda Windows
# menude cikmaz ve "GRUB Windows'u gormuyor" sikayetinin bir numarali nedeni
# budur.
OS_PROBER_KEY = "GRUB_DISABLE_OS_PROBER"


class GrubError(Exception):
    """GRUB islemi yapilamadi."""


# ==========================================================================
# Durum
# ==========================================================================
@dataclass
class GrubStatus:
    """Calisan sistemdeki GRUB kurulumunun durumu."""

    available: bool = False
    reason: str = ""
    tools: Dict[str, str] = field(default_factory=dict)
    firmware: str = ""
    defaults_path: str = ""
    defaults_readable: bool = False
    config_path: str = ""
    config_readable: bool = False
    os_prober_enabled: bool = False
    os_prober_present: bool = False
    menu_entries: int = 0
    version: str = ""

    def summary(self) -> Dict[str, str]:
        missing = [name for name, path in self.tools.items()
                   if not path and name.startswith("grub")]
        # Okunamayan bir ayar "kapali" degildir. Bilinmeyeni bilinen gibi
        # gostermek, CLAUDE.md'nin eksik disk bilgisi kuralinin ayni
        # gerekceyle buradaki karsiligidir.
        if not self.defaults_readable:
            prober = tr("Bilinmiyor (yapilandirma okunamadi)")
        elif self.os_prober_enabled:
            prober = tr("Acik")
        else:
            prober = tr("Kapali — diger sistemler menude cikmaz")
        # `grub.cfg` cogu dagitimda `-rw-------` root'a aittir. Okuyamadigimizda
        # "0 giris" yazmak menuyu bos gostermek olurdu — oysa menu doludur,
        # yalnizca bizim yetkimiz yoktur. Ayni ilke: CLAUDE.md, eksik disk
        # bilgisi kurali.
        if not self.config_path:
            menu = tr("bulunamadi")
        elif not self.config_readable:
            menu = tr("okunamadi (yetki yok)")
        else:
            menu = str(self.menu_entries)
        return {
            tr("GRUB yonetimi"): (tr("Kullanilabilir") if self.available
                                  else (self.reason or tr("Kullanilamaz"))),
            tr("Bellenim"): {"uefi": tr("UEFI"),
                             "bios": tr("BIOS (eski kip)")}.get(
                self.firmware, tr("Bilinmiyor")),
            tr("Surum"): self.version or "-",
            tr("Yapilandirma"): self.config_path or tr("bulunamadi"),
            tr("Menu girisi"): menu,
            tr("os-prober"): prober,
            tr("Eksik arac"): ", ".join(missing) if missing else tr("yok"),
        }


def _config_path() -> str:
    """Uretilen `grub.cfg` dosyasinin yolu (dagitima gore degisir)."""
    for candidate in platform.GRUB_CONFIG_PATHS:
        if "*" in candidate:
            continue
        if os.path.isfile(candidate):
            return candidate
    return ""


def status() -> GrubStatus:
    """GRUB'un bu makinedeki durumunu toplar (hicbir sey degistirmez)."""
    with diagnostics.span("grub.status"):
        available, reason = platform.grub_management_available()
        state = GrubStatus(available=available, reason=reason,
                           tools=platform.boot_tools_present(),
                           firmware=platform.firmware_type(),
                           defaults_path=platform.GRUB_DEFAULTS_PATH)
        state.os_prober_present = bool(state.tools.get("os-prober"))

        defaults = read_defaults()
        if defaults is not None:
            state.defaults_readable = True
            # Anahtar yoksa os-prober **acik** sayilir: GRUB'un kendi
            # varsayilani da budur.
            value = defaults.get(OS_PROBER_KEY, "false").strip().lower()
            state.os_prober_enabled = value not in ("true", "1", "yes")

        state.config_path = _config_path()
        if state.config_path:
            try:
                with open(state.config_path, "r", encoding="utf-8",
                          errors="replace") as fh:
                    state.menu_entries = len(parse_grub_cfg(fh.read()))
                state.config_readable = True
            except OSError:
                state.config_readable = False
                state.menu_entries = 0

        tool = state.tools.get("grub-install", "")
        if tool:
            state.version = _tool_version(tool)
        return state


def _tool_version(tool: str) -> str:
    """`grub-install --version` ciktisindan surum satirini alir."""
    try:
        result = platform.run_tool([tool, "--version"], timeout=20)
    except Exception:                               # noqa: BLE001
        return ""
    return (result.stdout or "").strip().splitlines()[0] if result.stdout else ""


# ==========================================================================
# Yapilandirma dosyasi
# ==========================================================================
def read_defaults() -> Optional[GrubDefaults]:
    """GRUB varsayilanlar dosyasini okur (okunamazsa None).

    Yolu `platform.GRUB_DEFAULTS_PATH` soyler; burada sabit yol yazilmaz.
    """
    try:
        with open(platform.GRUB_DEFAULTS_PATH, "r", encoding="utf-8",
                  errors="replace") as fh:
            return GrubDefaults(fh.read())
    except OSError:
        return None


def write_defaults(defaults: GrubDefaults) -> Tuple[bool, str]:
    """Varsayilanlar dosyasini geri yazar (gerekirse yetki isteyerek)."""
    with diagnostics.span("grub.write_defaults"):
        return platform.write_system_file(platform.GRUB_DEFAULTS_PATH,
                                          defaults.render())


def set_os_prober(enabled: bool) -> Tuple[bool, str]:
    """Diger isletim sistemlerinin taranmasini acar/kapatir.

    Debian 12 ve Ubuntu 22.04'ten beri bu tarama **kapali** gelir; bu yuzden
    yeni kurulan bir Linux'un yaninda duran Windows menude gorunmez. Acmak
    tek basina yeterli degildir, ardindan yapilandirma yeniden uretilmelidir.
    """
    defaults = read_defaults()
    if defaults is None:
        return False, tr("`{}` okunamadi.", platform.GRUB_DEFAULTS_PATH)
    defaults.set(OS_PROBER_KEY, "false" if enabled else "true")
    return write_defaults(defaults)


# ==========================================================================
# Yedekleme
# ==========================================================================
def backup_root() -> str:
    """Yedeklerin durdugu klasor (yoksa olusturulur)."""
    path = os.path.join(platform.config_dir(), BACKUP_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def _copy_into(source: str, target_dir: str) -> str:
    """Bir dosyayi ya da klasoru yedege kopyalar; kopyalanan adi doner."""
    name = os.path.basename(source.rstrip("/\\")) or "grub"
    target = os.path.join(target_dir, name)
    if os.path.isdir(source):
        shutil.copytree(source, target, dirs_exist_ok=True)
    else:
        shutil.copy2(source, target)
    return name


def backup_config(note: str = "") -> Dict:
    """GRUB yapilandirmasini zaman damgali bir klasore kopyalar.

    Kopyalanan her sey bir **bildirim dosyasina** yazilir: geri yuklerken
    neyin nereden geldigi tahmin edilmez. Okunamayan dosya sessizce atlanmaz,
    bildirimde `skipped` altinda nedeniyle birlikte durur.
    """
    with diagnostics.span("grub.backup"):
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        target = os.path.join(backup_root(), stamp)
        os.makedirs(target, exist_ok=True)

        saved: List[str] = []
        skipped: List[Dict[str, str]] = []
        sources = [platform.GRUB_DEFAULTS_PATH, platform.GRUB_SCRIPT_DIR]
        config = _config_path()
        if config:
            sources.append(config)

        for source in sources:
            if not os.path.exists(source):
                skipped.append({"path": source, "reason": tr("bulunamadi")})
                continue
            try:
                _copy_into(source, target)
                saved.append(source)
            except OSError as exc:
                skipped.append({"path": source, "reason": str(exc)})

        manifest = {
            "stamp": stamp,
            "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "note": note,
            "platform": platform.PLATFORM_NAME,
            "firmware": platform.firmware_type(),
            "config_path": config,
            "files": saved,
            "skipped": skipped,
        }
        with open(os.path.join(target, "manifest.json"), "w", encoding="utf-8",
                  newline="\n") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2)
        diagnostics.info(f"grub yedegi: {target} ({len(saved)} oge)")
        manifest["path"] = target
        return manifest


def list_backups() -> List[Dict]:
    """Kayitli yedekler, **yenisi basta**."""
    root = backup_root()
    found: List[Dict] = []
    try:
        names = sorted(os.listdir(root), reverse=True)
    except OSError:
        return found
    for name in names:
        path = os.path.join(root, name)
        manifest_path = os.path.join(path, "manifest.json")
        if not os.path.isdir(path):
            continue
        entry = {"stamp": name, "path": path, "date": name, "files": [],
                 "note": ""}
        try:
            with open(manifest_path, "r", encoding="utf-8") as fh:
                entry.update(json.load(fh))
        except (OSError, ValueError):
            pass
        entry["path"] = path
        found.append(entry)
    return found


def restore_config(stamp: str) -> Tuple[bool, str]:
    """Bir yedegi geri yukler.

    Yalnizca yedekte **bulunan** dosyalar geri konur; eksik olanlar sistemde
    oldugu gibi birakilir. Geri yukleme yapilandirmayi yeniden **uretmez** —
    `grub.cfg` dosyasi da yedekten geldigi icin buna gerek yoktur, ama
    varsayilanlar degistiyse kullanici ayrica `update_config()` calistirmalidir.
    """
    source = os.path.join(backup_root(), stamp)
    if not os.path.isdir(source):
        return False, tr("Yedek bulunamadi: {}", stamp)

    with diagnostics.span("grub.restore", reason=stamp):
        errors: List[str] = []
        defaults_backup = os.path.join(source,
                                       os.path.basename(platform.GRUB_DEFAULTS_PATH))
        if os.path.isfile(defaults_backup):
            try:
                with open(defaults_backup, "r", encoding="utf-8",
                          errors="replace") as fh:
                    text = fh.read()
            except OSError as exc:
                errors.append(str(exc))
            else:
                ok, error = platform.write_system_file(
                    platform.GRUB_DEFAULTS_PATH, text)
                if not ok:
                    errors.append(error)

        scripts_backup = os.path.join(source,
                                      os.path.basename(platform.GRUB_SCRIPT_DIR))
        if os.path.isdir(scripts_backup):
            for name in sorted(os.listdir(scripts_backup)):
                item = os.path.join(scripts_backup, name)
                if not os.path.isfile(item):
                    continue
                code, _, error = platform.run_privileged(
                    ["cp", item, os.path.join(platform.GRUB_SCRIPT_DIR, name)],
                    timeout=60)
                if code != 0:
                    errors.append(error or name)

        if errors:
            return False, "; ".join(errors[:3])
        return True, tr("Yedek geri yuklendi: {}", stamp)


# ==========================================================================
# Kurulum ve yapilandirma uretimi
# ==========================================================================
def install_target() -> str:
    """GRUB'un nereye kurulacagi: 'esp' (UEFI) | 'disk' (BIOS) | '' (bilinmiyor).

    Bu ayrim kozmetik degildir. **UEFI'de `grub-install` aygit argumanini yok
    sayar**: kurulum EFI Sistem Bolumune yapilir ve diskin ilk sektoruna
    dokunulmaz. Olculdu (Linux Mint 22.3, UEFI): araca sistem diski verildi,
    arac *"Installing for x86_64-efi platform"* dedi, ESP dosyalarini yeniden
    yazdi ve MBR'nin ilk 440 bayti **bit bit ayni kaldi**.

    Arayuz bunu bilmezse kullaniciya yanlis bir sey soyler ("diskin ilk
    sektoru degisecek") ve kullanici yanlis diski sectigini sanip bosuna
    tedirgin olur.
    """
    return {"uefi": "esp", "bios": "disk"}.get(platform.firmware_type(), "")


def install(device: str, progress=None) -> Tuple[bool, str]:
    """GRUB'u kurar (`grub-install`).

    BIOS kipinde `device` **diskin kendisidir** ve ilk sektorune yazilir;
    bolume kurmak cogu yapilandirmada calismaz, bu yuzden arayuz yalnizca disk
    secmeye izin verir.

    UEFI kipinde `device` **kullanilmaz**: hedef EFI Sistem Bolumudur. Araca
    anlamsiz bir arguman gecirmek yerine hic gecirilmez — boylece ciktisi da
    kullanicinin gordugu metinle tutarli olur.
    """
    available, reason = platform.grub_management_available()
    if not available:
        return False, reason
    tool = platform.find_boot_tool("grub-install")
    target = install_target()

    if target == "esp":
        command = [tool]
        message = tr("GRUB, EFI Sistem Bolumune kuruluyor...")
    else:
        command = [tool, device]
        message = tr("GRUB {} diskine kuruluyor...", device)
    if progress:
        progress(message, -1)

    with diagnostics.span("grub.install", reason=device or target):
        code, out, error = platform.run_privileged(command, timeout=600)

    # `grub-install` ilerleme ve sonuc satirlarini **stderr'e** yazar; basarili
    # kosumda stdout bos gelir. Ikisini de toplamazsak kullaniciya "kuruldu"
    # disinda hicbir sey gosteremeyiz.
    detail = "\n".join(p for p in (out, error) if p).strip()
    if code == 0:
        done = (tr("GRUB, EFI Sistem Bolumune kuruldu (diskin ilk sektoru "
                   "degismedi).") if target == "esp"
                else tr("GRUB {} diskine kuruldu.", device))
        return True, f"{done}\n{detail}" if detail else done
    return False, detail or tr("`grub-install` basarisiz oldu.")


def update_config(progress=None) -> Tuple[bool, str]:
    """Onyukleme menusunu yeniden uretir (`update-grub` / `grub-mkconfig`).

    Debian ailesinde `update-grub`, digerlerinde `grub-mkconfig -o <cfg>`
    kullanilir; ikisi de yoksa anlasilir bir hata verilir.
    """
    available, reason = platform.grub_management_available()
    if not available:
        return False, reason
    if progress:
        progress(tr("Onyukleme menusu yeniden uretiliyor..."), -1)

    with diagnostics.span("grub.update_config"):
        tool = platform.find_boot_tool("update-grub")
        if tool:
            code, out, error = platform.run_privileged([tool], timeout=600)
        else:
            maker = platform.find_boot_tool("grub-mkconfig")
            if not maker:
                return False, tr("`update-grub` ya da `grub-mkconfig` "
                                 "bulunamadi.")
            target = _config_path() or platform.GRUB_CONFIG_PATHS[0]
            code, out, error = platform.run_privileged(
                [maker, "-o", target], timeout=600)
    if code == 0:
        return True, out or tr("Onyukleme menusu guncellendi.")
    return False, error or out or tr("Menu uretilemedi.")


def upgrade_packages(progress=None) -> Tuple[bool, str]:
    """GRUB paketlerini gunceller (yalnizca apt tabanli dagitimlarda).

    Paket yoneticisi her dagitimda farklidir ve yanlis yoneticiyi calistirmak
    ise yaramaz. `apt-get` yoksa **hata degil**, "yapilamadi" denir: paket
    guncellemesi onarimin zorunlu parcasi degildir.
    """
    manager = platform.find_boot_tool("apt-get")
    if not manager:
        return False, tr("Paket guncellemesi yalnizca apt tabanli "
                         "dagitimlarda yapilabilir.")
    if progress:
        progress(tr("GRUB paketleri guncelleniyor..."), -1)
    with diagnostics.span("grub.upgrade"):
        code, out, error = platform.run_privileged(
            [manager, "install", "--yes", "--only-upgrade",
             "grub-common", "grub2-common"], timeout=900)
    if code == 0:
        return True, out or tr("GRUB paketleri guncel.")
    return False, error or out or tr("Paket guncellemesi basarisiz.")


# ==========================================================================
# Tumunu onar
# ==========================================================================
@dataclass
class RepairStep:
    """Onarimda atilan tek bir adim ve sonucu."""

    title: str
    ok: bool
    detail: str = ""


def repair(targets: List[str], progress=None,
           enable_prober: bool = True) -> Tuple[bool, List[RepairStep]]:
    """Tek dugmeyle onarim: yedek al, os-prober'i ac, kur, menuyu uret.

    `targets` GRUB'un **zaten bulundugu** diskler olmalidir. Makinedeki her
    diske GRUB yazmak onarim degildir; calisan diskleri degistirmektir. Baska
    bir makineye takilan bir yedek disk, birden bu sistemi acmaya calisirdi.
    Hedefi cagiran belirler (`ui/dialogs/bootloader.py`), burasi yalnizca
    verileni uygular.
    """
    steps: List[RepairStep] = []
    if not targets:
        return False, [RepairStep(tr("Hedef disk yok"), False,
                                  tr("GRUB'un kurulu oldugu bir disk "
                                     "bulunamadi."))]

    total = 3 + len(targets)
    done = 0

    def report(message: str) -> None:
        if progress:
            progress(message, int(done * 100 / total))

    with diagnostics.span("grub.repair", reason=",".join(targets)):
        report(tr("Mevcut ayarlar yedekleniyor..."))
        try:
            manifest = backup_config(note=tr("Onarim oncesi kendiliginden"))
            steps.append(RepairStep(tr("Ayarlar yedeklendi"), True,
                                    manifest.get("path", "")))
        except OSError as exc:
            steps.append(RepairStep(tr("Ayarlar yedeklendi"), False, str(exc)))
        done += 1

        if enable_prober:
            report(tr("Diger sistemlerin taranmasi aciliyor..."))
            ok, message = set_os_prober(True)
            steps.append(RepairStep(tr("os-prober acildi"), ok, message))
        done += 1

        for device in targets:
            report(tr("GRUB {} diskine kuruluyor...", device))
            ok, message = install(device)
            steps.append(RepairStep(tr("GRUB kuruldu: {}", device), ok,
                                    message[:400]))
            done += 1

        report(tr("Onyukleme menusu yeniden uretiliyor..."))
        ok, message = update_config()
        steps.append(RepairStep(tr("Onyukleme menusu uretildi"), ok,
                                message[:400]))
        done += 1

    if progress:
        progress(tr("Tamamlandi"), 100)
    # Onarim, **menu uretimi** basarili olduysa basarilidir: yedekleme ya da
    # os-prober adiminin aksamasi menuyu bozmaz.
    return steps[-1].ok, steps


def menu_entries() -> List:
    """Uretilmis `grub.cfg` icindeki menu girisleri (okunamazsa bos liste)."""
    path = _config_path()
    if not path:
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return parse_grub_cfg(fh.read())
    except OSError:
        return []
