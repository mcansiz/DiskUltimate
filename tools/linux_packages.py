"""Linux dagitim paketleri: .deb, .rpm ve AUR PKGBUILD (GELISTIRME ARACI, ADR 0103).

    python3 tools/linux_packages.py deb rpm
        dist/DiskUltimate-<surum>-all.deb, dist/DiskUltimate-<surum>-noarch.rpm
    python3 tools/linux_packages.py aur [--source ARSIV] [--url ADRES]
        dist/aur/PKGBUILD + .SRCINFO (AUR deposuna kopyalanir)
    python3 tools/linux_packages.py stage --format arch HEDEF
        dosya duzenini HEDEF altina kurar (PKGBUILD'in package() adimi)

Kullanici bu araca ihtiyac duymaz. Paketler mimariden bagimsizdir: icinde
yalnizca Python kaynagi vardir; Python ve PyQt dagitimdan gelir. Bu yuzden
AppImage'in glibc 2.34 alt siniri burada yoktur ve PyQt5 yolu (ADR 0102)
eski dagitimlarda isler: bagimlilik "PyQt6 ya da PyQt5"tir.

Dosya duzeni (uc paket de ayni `stage()` cagrisini kullanir):

    /usr/bin/diskultimate                         baslatici
    /usr/lib/diskultimate/diskultimate-root       pkexec yardimcisi
    /usr/share/diskultimate/                      main.py, src/, system-install
    /usr/share/polkit-1/actions/<APP_ID>.policy   parola penceresinde uygulama adi
    /usr/share/applications/<APP_ID>.desktop
    /usr/share/metainfo/<APP_ID>.metainfo.xml     yazilim merkezleri icin
    /usr/share/icons/hicolor/NxN/apps/<APP_ID>.png

`system-install` isaret dosyasi uygulamaya dagitim paketinden calistigini
soyler (`paths.SYSTEM_INSTALL`): veri kullanicinin dizinine yazilir ve
yetki yardimci uzerinden istenir (`platform.linux_pkexec_command`).

.deb icin `dpkg-deb`, .rpm icin `rpmbuild` gerekir (Ubuntu: `apt install rpm`).
"""
from __future__ import annotations

import datetime
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, ".tmp", "linux-packages")

APP_ID = "io.github.mcansiz.DiskUltimate"
NAME = "diskultimate"
REPO = "mcansiz/DiskUltimate"
HOMEPAGE = f"https://github.com/{REPO}"
LICENSE_ID = "GPL-3.0-only"
MAINTAINER = "mcansiz <mcansiz@users.noreply.github.com>"

SHARE = "/usr/share/diskultimate"
HELPER = "/usr/lib/diskultimate/diskultimate-root"
LAUNCHER = "/usr/bin/diskultimate"
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)

SUMMARY = "Visual disk manager for disks and disk images"
DESCRIPTION = (
    "DiskUltimate partitions, formats, backs up, clones and recovers disks "
    "and disk images (raw, VHD, VDI, VMDK, QCOW2). It edits MBR and GPT "
    "partition tables, converts between them without data loss, browses "
    "and edits FAT, exFAT, NTFS and ext file systems, wipes disks securely "
    "and recovers deleted files and lost partitions. Destructive operations "
    "are queued and run together with one Apply.")

# Bagimliliklar. PyQt6 once denenir, yoksa PyQt5 (ui/qt, ADR 0102).
DEB_DEPENDS = "python3 (>= 3.8), python3-pyqt6 | python3-pyqt5"
DEB_RECOMMENDS = "pkexec | policykit-1"
DEB_SUGGESTS = "e2fsprogs, dosfstools, ntfs-3g, xfsprogs, btrfs-progs, udisks2"
# Fedora: python3-pyqt6 / python3-qt5; openSUSE: python3-PyQt6 / python3-qt5.
RPM_REQUIRES = ("python3 >= 3.8",
                "(python3-pyqt6 or python3-PyQt6 or python3-qt5 or python3-PyQt5)")
RPM_RECOMMENDS = ("polkit",)
RPM_SUGGESTS = ("e2fsprogs", "dosfstools", "ntfs-3g", "xfsprogs", "btrfs-progs",
                "udisks2")
ARCH_DEPENDS = ("python", "python-pyqt6")
ARCH_OPTDEPENDS = (
    "polkit: graphical password prompt for disk access",
    "e2fsprogs: format ext2/3/4",
    "ntfs-3g: format NTFS",
    "xfsprogs: format XFS",
    "btrfs-progs: format Btrfs",
    "udisks2: mount partitions without root",
)

# Dil kodu -> (GenericName, Comment). Arayuz metni kuralina gore dilin
# dogru yazimiyla (CLAUDE.md); eski AppImage girdisi ASCII'ydi.
DESKTOP_TEXT = {
    "": ("Disk Manager",
         "Partition, format, back up, clone and recover disks and disk images"),
    "tr": ("Disk Yöneticisi",
           "Diskleri ve disk görüntülerini bölümleyin, biçimlendirin, "
           "yedekleyin, klonlayın ve kurtarın"),
    "de": ("Datenträgerverwaltung",
           "Datenträger und Disk-Images partitionieren, formatieren, sichern, "
           "klonen und wiederherstellen"),
    "fr": ("Gestionnaire de disques",
           "Partitionner, formater, sauvegarder, cloner et récupérer des "
           "disques et des images disque"),
    "it": ("Gestore dischi",
           "Partizionare, formattare, eseguire il backup, clonare e recuperare "
           "dischi e immagini disco"),
    "es": ("Gestor de discos",
           "Particionar, formatear, respaldar, clonar y recuperar discos e "
           "imágenes de disco"),
    "ru": ("Менеджер дисков",
           "Разметка, форматирование, резервное копирование, клонирование и "
           "восстановление дисков и образов дисков"),
    "zh_CN": ("磁盘管理器", "对磁盘和磁盘映像进行分区、格式化、备份、克隆和恢复"),
    "ja": ("ディスクマネージャー",
           "ディスクとディスクイメージのパーティション作成、フォーマット、"
           "バックアップ、クローン、復旧"),
    "ko": ("디스크 관리자", "디스크와 디스크 이미지의 파티션 분할, 포맷, 백업, 복제 및 복구"),
}

# Polkit penceresinin metni: (eylem aciklamasi, mesaj).
POLKIT_TEXT = {
    "": ("Run DiskUltimate with administrator rights",
         "DiskUltimate needs administrator rights to read and write physical disks."),
    "tr": ("DiskUltimate'i yönetici yetkisiyle çalıştır",
           "DiskUltimate'in fiziksel diskleri okuyup yazabilmesi için yönetici "
           "yetkisi gerekiyor."),
    "de": ("DiskUltimate mit Administratorrechten ausführen",
           "DiskUltimate benötigt Administratorrechte, um physische Datenträger "
           "zu lesen und zu beschreiben."),
    "fr": ("Exécuter DiskUltimate avec les droits d'administrateur",
           "DiskUltimate a besoin des droits d'administrateur pour lire et "
           "écrire sur les disques physiques."),
    "it": ("Eseguire DiskUltimate con i privilegi di amministratore",
           "DiskUltimate richiede i privilegi di amministratore per leggere e "
           "scrivere i dischi fisici."),
    "es": ("Ejecutar DiskUltimate con privilegios de administrador",
           "DiskUltimate necesita privilegios de administrador para leer y "
           "escribir en los discos físicos."),
    "ru": ("Запустить DiskUltimate с правами администратора",
           "DiskUltimate требуются права администратора для чтения и записи "
           "физических дисков."),
    "zh_CN": ("以管理员权限运行 DiskUltimate", "DiskUltimate 需要管理员权限才能读写物理磁盘。"),
    "ja": ("管理者権限で DiskUltimate を実行",
           "DiskUltimate が物理ディスクを読み書きするには管理者権限が必要です。"),
    "ko": ("관리자 권한으로 DiskUltimate 실행",
           "DiskUltimate가 물리 디스크를 읽고 쓰려면 관리자 권한이 필요합니다."),
}

LAUNCHER_SH = f"""#!/bin/sh
# DiskUltimate baslaticisi (dagitim paketi, ADR 0103).
exec /usr/bin/python3 {SHARE}/main.py "$@"
"""

# pkexec bu dosyayi root olarak calistirir; polkit tanimi bu yola baglidir.
# Baslatilacak program burada sabittir: cagiran yalnizca ortam degiskeni ve
# uygulama argumani verebilir. Kabul edilen degiskenler `_linux_relaunch`in
# tasidiklaridir; baska bir sey gelirse durulur. Eylem `auth_admin`dir
# (`_keep` degil): parola her seferinde sorulur, yoksa ayni oturumdaki
# herhangi bir surec birkac dakika parolasiz root yazicisi bulurdu
# (DISKULTIMATE_LOG_DIR root olarak yazilir).
HELPER_SH = f"""#!/bin/sh
# DiskUltimate'i root olarak baslatir; pkexec cagirir (ADR 0103).
# Argumanlar: [AD=DEGER ...] -- [uygulama argumanlari]
while [ $# -gt 0 ]; do
  case "$1" in
    --) shift; break ;;
    DISPLAY=*|XAUTHORITY=*|WAYLAND_DISPLAY=*|XDG_RUNTIME_DIR=*|QT_QPA_PLATFORM=*|DISKULTIMATE_*=*)
      export "$1" ;;
    *) echo "diskultimate-root: beklenmeyen arguman: $1" >&2; exit 2 ;;
  esac
  shift
done
exec /usr/bin/python3 -E -s {SHARE}/main.py "$@"
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def app_version() -> str:
    text = open(os.path.join(ROOT, "src", "diskultimate", "ui", "main_window.py"),
                encoding="utf-8").read()
    return re.search(r'^APP_VERSION = "([^"]+)"', text, re.M).group(1)


def pkg_version(version: str, fmt: str) -> str:
    """0.11.0-beta -> deb/rpm 0.11.0~beta, arch 0.11.0beta.

    Uc bicimde de on surum son surumden kucuk siralanir: `~` deb ve rpm'de
    "her seyden once" demektir; pacman'in vercmp'i harfle devam eden
    surumu ciplak surumden eski sayar (0.11.0beta < 0.11.0).
    """
    return version.replace("-", "" if fmt == "arch" else "~")


def release_date() -> str:
    """Metainfo surum tarihi: SOURCE_DATE_EPOCH > son commit > bugun."""
    epoch = os.environ.get("SOURCE_DATE_EPOCH", "")
    if epoch.isdigit():
        stamp = datetime.datetime.fromtimestamp(int(epoch), datetime.timezone.utc)
        return stamp.strftime("%Y-%m-%d")
    try:
        out = subprocess.run(["git", "-C", ROOT, "log", "-1", "--format=%cs"],
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and re.match(r"\d{4}-\d\d-\d\d$", out.stdout.strip()):
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return datetime.date.today().isoformat()


def desktop_entry(exec_name: str, icon: str) -> str:
    """.desktop girdisi; AppImage (tools/appimage.py) de bunu kullanir."""
    lines = ["[Desktop Entry]", "Type=Application", "Name=DiskUltimate"]
    for lang, (generic, _) in DESKTOP_TEXT.items():
        lines.append(f"GenericName{f'[{lang}]' if lang else ''}={generic}")
    for lang, (_, comment) in DESKTOP_TEXT.items():
        lines.append(f"Comment{f'[{lang}]' if lang else ''}={comment}")
    lines += [f"Exec={exec_name} %F", f"Icon={icon}", "Terminal=false",
              "Categories=System;Filesystem;",
              "Keywords=disk;partition;format;backup;clone;recovery;ntfs;gpt;"]
    return "\n".join(lines) + "\n"


def _xml(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _lang_attr(lang: str) -> str:
    return f' xml:lang="{lang}"' if lang else ""


def polkit_policy() -> str:
    desc = "\n".join(f"    <description{_lang_attr(k)}>{_xml(v[0])}</description>"
                     for k, v in POLKIT_TEXT.items())
    msg = "\n".join(f"    <message{_lang_attr(k)}>{_xml(v[1])}</message>"
                    for k, v in POLKIT_TEXT.items())
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE policyconfig PUBLIC "-//freedesktop//DTD PolicyKit Policy Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/PolicyKit/1/policyconfig.dtd">
<policyconfig>
  <vendor>DiskUltimate</vendor>
  <vendor_url>{HOMEPAGE}</vendor_url>
  <icon_name>{APP_ID}</icon_name>
  <action id="{APP_ID}.run">
{desc}
{msg}
    <defaults>
      <allow_any>auth_admin</allow_any>
      <allow_inactive>auth_admin</allow_inactive>
      <allow_active>auth_admin</allow_active>
    </defaults>
    <annotate key="org.freedesktop.policykit.exec.path">{HELPER}</annotate>
    <annotate key="org.freedesktop.policykit.exec.allow_gui">true</annotate>
  </action>
</policyconfig>
"""


def metainfo(version: str) -> str:
    shots = (("main-window", "Disks, partition map and file browser"),
             ("pending-operations", "Operations are queued and applied together"),
             ("resize-partition", "Resizing a partition"),
             ("deleted-files", "Recovering deleted files"),
             ("hex-viewer", "Hex viewer"))
    default = ' type="default"'
    screenshots = "\n".join(
        f'    <screenshot{default if i == 0 else ""}>\n'
        f"      <caption>{caption}</caption>\n"
        f"      <image>https://raw.githubusercontent.com/{REPO}/main/docs/"
        f"screenshots/en/{name}.png</image>\n    </screenshot>"
        for i, (name, caption) in enumerate(shots))
    summary = "\n".join(f"  <summary{_lang_attr(k)}>{_xml(v[1])}</summary>"
                        for k, v in DESKTOP_TEXT.items())
    kind = "development" if "-" in version else "stable"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop-application">
  <id>{APP_ID}</id>
  <metadata_license>CC0-1.0</metadata_license>
  <project_license>{LICENSE_ID}</project_license>
  <name>DiskUltimate</name>
{summary}
  <description>
    <p>{_xml(DESCRIPTION)}</p>
    <p>Physical disks need administrator rights; the application asks for
    them when it starts. Disk image files work without them.</p>
  </description>
  <developer id="io.github.mcansiz">
    <name>mcansiz</name>
  </developer>
  <launchable type="desktop-id">{APP_ID}.desktop</launchable>
  <url type="homepage">{HOMEPAGE}</url>
  <url type="bugtracker">{HOMEPAGE}/issues</url>
  <categories>
    <category>System</category>
  </categories>
  <screenshots>
{screenshots}
  </screenshots>
  <content_rating type="oars-1.1"/>
  <releases>
    <release version="{pkg_version(version, 'deb')}" date="{release_date()}" type="{kind}">
      <url>{HOMEPAGE}/releases/tag/v{version}</url>
    </release>
  </releases>
</component>
"""


def _write(path: str, text: str, mode: int = 0o644) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.chmod(path, mode)


def stage(dest: str, fmt: str) -> None:
    """Dosya duzenini `dest` altina kurar (dest = paketin koku)."""
    if fmt not in ("deb", "rpm", "arch"):
        raise SystemExit(f"HATA: bilinmeyen bicim {fmt!r} (deb | rpm | arch)")
    version = app_version()
    root = lambda p: os.path.join(dest, p.lstrip("/"))      # noqa: E731
    share = root(SHARE)
    if os.path.exists(share):
        shutil.rmtree(share)
    shutil.copytree(os.path.join(ROOT, "src", "diskultimate"),
                    os.path.join(share, "src", "diskultimate"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(os.path.join(ROOT, "main.py"), share)
    _write(os.path.join(share, "system-install"),
           f"format={fmt}\nhelper={HELPER}\n")
    _write(root(LAUNCHER), LAUNCHER_SH, 0o755)
    _write(root(HELPER), HELPER_SH, 0o755)
    _write(root(f"/usr/share/polkit-1/actions/{APP_ID}.policy"), polkit_policy())
    _write(root(f"/usr/share/applications/{APP_ID}.desktop"),
           desktop_entry(NAME, APP_ID))
    _write(root(f"/usr/share/metainfo/{APP_ID}.metainfo.xml"), metainfo(version))
    for size in ICON_SIZES:
        target = root(f"/usr/share/icons/hicolor/{size}x{size}/apps/{APP_ID}.png")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(os.path.join(ROOT, "src", "diskultimate", "ui", "resources",
                                     f"app-icon-{size}.png"), target)
        os.chmod(target, 0o644)
    # Lisans her dagitimin bekledigi yere.
    license_dir = {"deb": f"/usr/share/doc/{NAME}",
                   "rpm": f"/usr/share/licenses/{NAME}",
                   "arch": f"/usr/share/licenses/{NAME}"}[fmt]
    license_name = "copyright" if fmt == "deb" else "LICENSE"
    os.makedirs(root(license_dir), exist_ok=True)
    shutil.copyfile(os.path.join(ROOT, "LICENSE"),
                    os.path.join(root(license_dir), license_name))
    # Kaynak agacindaki izinler (umask, Windows'tan gelen x biti) pakete
    # tasinmaz: klasorler 755, dosyalar 644, yalnizca iki betik 755.
    os.chmod(dest, 0o755)
    for dirpath, dirnames, filenames in os.walk(dest):
        for d in dirnames:
            os.chmod(os.path.join(dirpath, d), 0o755)
        for f in filenames:
            os.chmod(os.path.join(dirpath, f), 0o644)
    for p in (root(LAUNCHER), root(HELPER)):
        os.chmod(p, 0o755)


# Bayt kodu kurulumda, hedef sistemin Python'uyla derlenir: paket her
# Python surumune uyar ve kullanici /usr/share altina __pycache__ yazamadigi
# icin aksi halde her acilista butun moduller yeniden derlenirdi.
COMPILE_SH = f"python3 -m compileall -q {SHARE} >/dev/null 2>&1 || true"
# Temizlik de Python'la: `find` her sistemde yok (openSUSE kapsayicisi,
# 2026-11-01) ve sessizce dusunce paket kaldirilinca klasor kalirdi.
CLEAN_SH = ("python3 -c \"import pathlib, shutil; "
            f"[shutil.rmtree(p) for p in list(pathlib.Path('{SHARE}').rglob('__pycache__'))]\" "
            ">/dev/null 2>&1 || true")


def _du_kib(path: str) -> int:
    total = 0
    for dirpath, _, files in os.walk(path):
        for f in files:
            total += os.lstat(os.path.join(dirpath, f)).st_size
    return (total + 1023) // 1024


def build_deb(version: str) -> str:
    dest = os.path.join(WORK, "deb")
    shutil.rmtree(dest, ignore_errors=True)
    stage(dest, "deb")
    control = os.path.join(dest, "DEBIAN")
    os.makedirs(control)
    long_desc = "\n".join(" " + line for line in _wrap(DESCRIPTION, 72))
    _write(os.path.join(control, "control"), f"""Package: {NAME}
Version: {pkg_version(version, 'deb')}-1
Architecture: all
Maintainer: {MAINTAINER}
Installed-Size: {_du_kib(dest)}
Depends: {DEB_DEPENDS}
Recommends: {DEB_RECOMMENDS}
Suggests: {DEB_SUGGESTS}
Section: admin
Priority: optional
Homepage: {HOMEPAGE}
Description: {SUMMARY.lower()}
{long_desc}
""")
    _write(os.path.join(control, "postinst"),
           f'#!/bin/sh\nset -e\nif [ "$1" = configure ]; then\n  {COMPILE_SH}\nfi\n',
           0o755)
    _write(os.path.join(control, "prerm"), f"#!/bin/sh\nset -e\n{CLEAN_SH}\n", 0o755)
    out = os.path.join(ROOT, "dist", f"DiskUltimate-{version}-all.deb")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    subprocess.run(["dpkg-deb", "--root-owner-group", "-Zxz", "--build", dest, out],
                   check=True)
    return out


def _wrap(text: str, width: int):
    line = ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            yield line
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        yield line


def build_rpm(version: str) -> str:
    top = os.path.join(WORK, "rpm")
    shutil.rmtree(top, ignore_errors=True)
    stage_dir = os.path.join(top, "stage")
    stage(stage_dir, "rpm")
    files = []
    for dirpath, dirnames, filenames in os.walk(stage_dir):
        rel = "/" + os.path.relpath(dirpath, stage_dir)
        if rel in (SHARE, os.path.dirname(HELPER)):
            files.append(rel)          # klasorun tamami bizim
            dirnames[:] = []
            continue
        for f in sorted(filenames):
            files.append(os.path.join(rel, f))
    files.sort()
    spec = os.path.join(top, f"{NAME}.spec")
    requires = "\n".join(f"Requires: {r}" for r in RPM_REQUIRES)
    recommends = "\n".join(f"Recommends: {r}" for r in RPM_RECOMMENDS)
    suggests = "\n".join(f"Suggests: {r}" for r in RPM_SUGGESTS)
    _write(spec, f"""%global debug_package %{{nil}}
%global __os_install_post %{{nil}}
Name: {NAME}
Version: {pkg_version(version, 'rpm')}
Release: 1
Summary: {SUMMARY}
License: {LICENSE_ID}
URL: {HOMEPAGE}
BuildArch: noarch
AutoReqProv: no
{requires}
{recommends}
{suggests}

%description
{chr(10).join(_wrap(DESCRIPTION, 72))}

%install
cp -a {stage_dir}/. %{{buildroot}}/

%post
{COMPILE_SH}

%preun
if [ "$1" -eq 0 ]; then
  {CLEAN_SH}
fi

%files
{chr(10).join(files)}
""")
    subprocess.run(["rpmbuild", "-bb", "--define", f"_topdir {top}",
                    "--define", "_build_id_links none", spec], check=True)
    built = [os.path.join(dp, f) for dp, _, fs in os.walk(os.path.join(top, "RPMS"))
             for f in fs if f.endswith(".rpm")]
    out = os.path.join(ROOT, "dist", f"DiskUltimate-{version}-noarch.rpm")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    shutil.copyfile(built[0], out)
    return out


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def build_aur(version: str, source: str = "", url: str = "") -> str:
    """PKGBUILD + .SRCINFO. Kaynak, etiketin GitHub arsividir; `.claude/`
    arsive girmez (.gitattributes export-ignore), indirme kucuk kalir.

    `--source` verilmezse etiket arsivi indirilir (etiket push edilmis
    olmalidir). CI denemesinde yerel `git archive` ciktisi verilir.
    """
    tag = f"v{version}"
    url = url or f"{HOMEPAGE}/archive/refs/tags/{tag}.tar.gz"
    if not source:
        import urllib.request
        source = os.path.join(WORK, f"{tag}.tar.gz")
        os.makedirs(WORK, exist_ok=True)
        log(f"indiriliyor: {url}")
        with urllib.request.urlopen(url) as r, open(source, "wb") as f:
            shutil.copyfileobj(r, f)
    with tarfile.open(source) as tar:
        top = tar.getnames()[0].split("/")[0]
    digest = _sha256(source)
    pkgver = pkg_version(version, "arch")
    optdepends = "\n".join(f"  '{d}'" for d in ARCH_OPTDEPENDS)
    out_dir = os.path.join(ROOT, "dist", "aur")
    os.makedirs(out_dir, exist_ok=True)
    _write(os.path.join(out_dir, "PKGBUILD"), f"""# Maintainer: {MAINTAINER}
# Uretildi: tools/linux_packages.py aur (ADR 0103) -- elle degistirmeyin.
pkgname={NAME}
pkgver={pkgver}
_tag={tag}
pkgrel=1
pkgdesc="{SUMMARY}"
arch=('any')
url="{HOMEPAGE}"
license=('{LICENSE_ID}')
depends=({' '.join(repr(d) for d in ARCH_DEPENDS)})
makedepends=('python')
optdepends=(
{optdepends}
)
source=("$pkgname-$pkgver.tar.gz::{url.replace(tag, '$_tag')}")
sha256sums=('{digest}')

package() {{
  cd "$srcdir/{top.replace(version, '${_tag#v}')}"
  python tools/linux_packages.py stage --format arch "$pkgdir"
  python -m compileall -q -d {SHARE} "$pkgdir{SHARE}"
}}
""")
    srcinfo = [f"pkgbase = {NAME}", f"\tpkgdesc = {SUMMARY}", f"\tpkgver = {pkgver}",
               "\tpkgrel = 1", f"\turl = {HOMEPAGE}", "\tarch = any",
               f"\tlicense = {LICENSE_ID}", "\tmakedepends = python"]
    srcinfo += [f"\tdepends = {d}" for d in ARCH_DEPENDS]
    srcinfo += [f"\toptdepends = {d}" for d in ARCH_OPTDEPENDS]
    srcinfo += [f"\tsource = {NAME}-{pkgver}.tar.gz::{url}",
                f"\tsha256sums = {digest}", "", f"pkgname = {NAME}", ""]
    _write(os.path.join(out_dir, ".SRCINFO"), "\n".join(srcinfo))
    return out_dir


def main(argv) -> int:
    if not argv:
        print(__doc__)
        return 2
    if argv[0] == "stage":
        if len(argv) != 4 or argv[1] != "--format":
            print("kullanim: stage --format deb|rpm|arch HEDEF")
            return 2
        stage(os.path.abspath(argv[3]), argv[2])
        return 0
    version = app_version()
    if argv[0] == "aur":
        opts = dict(zip(argv[1::2], argv[2::2]))
        out = build_aur(version, opts.get("--source", ""), opts.get("--url", ""))
        log(f"Hazir: {out}/PKGBUILD")
        return 0
    for kind in argv:
        builder = {"deb": build_deb, "rpm": build_rpm}.get(kind)
        if builder is None:
            print(f"HATA: bilinmeyen hedef {kind!r} (deb | rpm | aur | stage)")
            return 2
        out = builder(version)
        log(f"Hazir: {out} ({os.path.getsize(out) / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
