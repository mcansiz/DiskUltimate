"""Linux AppImage uretir (GELISTIRME ARACI, ADR 0050).

    python3 tools/appimage.py            # dist/DiskUltimate-<surum>-x86_64.AppImage
    python3 tools/appimage.py --keep     # AppDir'i incelemek icin silme

Kullanici bu araca ihtiyac duymaz; uretilen AppImage tek basina calisir.

## Neden PyInstaller degil

PyInstaller ikilisi **derlendigi makinenin glibc surumune** baglidir;
gelistirme makinesi glibc 2.43 ve eski dagitim tabani (Mint VM) artik yok.
Bunun yerine `niess/python-appimage`in manylinux2014 (glibc 2.17) uzerinde
derlenmis tasinabilir Python'u ve PyPI'nin manylinux PyQt5 tekerlekleri
kullanilir: eski bir derleme makinesi gerekmeden eski dagitimlarda da calisir.
Cikan pakette gereken en yuksek glibc surumu **olculur** ve basilir.

## Adimlar

1. Sabitlenmis Python AppImage'i ve `appimagetool` indirilir (SHA-256
   denetlenir) -> `.tmp/appimage/araclar/`.
2. Python acilir, PyQt5 sabit surumlerle kurulur.
3. Kullanilmayan Qt kutuphaneleri, eklentileri ve Python modulleri silinir;
   kalan her ELF dosyasinin `NEEDED` bagimliliklari pakette ya da sistemde
   bulunmali (yoksa durulur).
4. Uygulama (`main.py`, `src/diskultimate`), `AppRun`, `.desktop`, ikon.
5. `appimagetool` ile paketlenir.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, ".tmp", "appimage")
TOOLS = os.path.join(WORK, "araclar")

PYTHON = ("python3.12.14-cp312-cp312-manylinux2014_x86_64.AppImage",
          "https://github.com/niess/python-appimage/releases/download/python3.12/",
          "fd6b81d037c786608c7407dd752a2b59381bf172b9c84bc8f0166d9f34cd53c4")
APPIMAGETOOL = ("appimagetool-x86_64.AppImage",
                "https://github.com/AppImage/appimagetool/releases/download/1.9.1/",
                "ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0")
PIP_PACKAGES = ("PyQt5==5.15.11", "PyQt5-Qt5==5.15.19", "PyQt5-sip==12.19.0")
PY = "python3.12"

# Uygulamanin kullandigi Qt modulleri: QtCore, QtGui, QtWidgets. Platform
# eklentileri (xcb, wayland) bunlarin disinda su kutuphaneleri ister.
QT_LIBS_KEEP = {
    "Core", "Gui", "Widgets", "DBus", "XcbQpa", "WaylandClient", "Svg",
}
QT_PLUGIN_DIRS_KEEP = {
    "platforms", "platforminputcontexts", "platformthemes", "imageformats",
    "iconengines", "xcbglintegrations", "wayland-decoration-client",
    "wayland-graphics-integration-client", "wayland-shell-integration",
}
PLATFORMS_KEEP = ("libqxcb.so", "libqwayland-generic.so", "libqoffscreen.so",
                  "libqminimal.so")
PYQT_KEEP = ("QtCore", "QtGui", "QtWidgets", "sip")
STDLIB_DROP = ("test", "idlelib", "tkinter", "turtledemo", "ensurepip",
               "lib2to3", "pydoc_data", "unittest/test")

DESKTOP = """[Desktop Entry]
Type=Application
Name=DiskUltimate
GenericName=Disk Manager
GenericName[tr]=Disk Yoneticisi
GenericName[de]=Datentragerverwaltung
Comment=Partition, format, back up, clone and recover disks and disk images
Comment[tr]=Disk ve disk goruntulerini bolumleyin, bicimlendirin, yedekleyin, kurtarin
Exec=DiskUltimate %F
Icon=diskultimate
Terminal=false
Categories=System;Utility;Filesystem;
Keywords=disk;partition;format;backup;clone;recovery;ntfs;gpt;
"""

APPRUN = r"""#!/bin/sh
# DiskUltimate AppImage baslaticisi (ADR 0050).
HERE="$(dirname "$(readlink -f "$0")")"
: "${APPDIR:=$HERE}"
export APPDIR

# Paket glibc ister. musl sistemlerde (Alpine, Void-musl) glibc yukleyicisi
# yoktur; tek satirlik "not found" yerine anlasilir mesaj verilir.
if [ ! -e /lib64/ld-linux-x86-64.so.2 ] && [ ! -e /lib/ld-linux-x86-64.so.2 ] \
   && [ ! -e /lib/x86_64-linux-gnu/ld-linux-x86-64.so.2 ]; then
  echo "DiskUltimate: this package needs glibc. On musl systems run it from source:" >&2
  echo "  apk add python3 py3-qt5 && python3 main.py" >&2
  exit 1
fi

# Kullanicinin kendi Python ortami pakete karismasin.
unset PYTHONHOME PYTHONPATH PYTHONSTARTUP
exec "$APPDIR/opt/python3.12/bin/python3.12" -s -E \
     "$APPDIR/usr/share/diskultimate/main.py" "$@"
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fetch(item) -> str:
    name, base, digest = item
    os.makedirs(TOOLS, exist_ok=True)
    path = os.path.join(TOOLS, name)
    if not os.path.isfile(path) or sha256(path) != digest:
        log(f"indiriliyor: {name}")
        with urllib.request.urlopen(base + name) as r, open(path + ".part", "wb") as f:
            shutil.copyfileobj(r, f)
        os.replace(path + ".part", path)
    if sha256(path) != digest:
        raise SystemExit(f"HATA: {name} SHA-256 tutmuyor")
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return path


def app_version() -> str:
    text = open(os.path.join(ROOT, "src", "diskultimate", "ui", "main_window.py"),
                encoding="utf-8").read()
    return re.search(r'^APP_VERSION = "([^"]+)"', text, re.M).group(1)


def run(cmd, **kw) -> None:
    subprocess.run(cmd, check=True, **kw)


def trim(appdir: str) -> None:
    """Kullanilmayan Qt ve Python parcalarini siler."""
    site = os.path.join(appdir, "opt", PY, "lib", PY, "site-packages")
    pyqt = os.path.join(site, "PyQt5")
    qt = os.path.join(pyqt, "Qt5")
    for name in os.listdir(os.path.join(qt, "lib")):
        m = re.match(r"libQt5(\w+?)\.so", name)
        if m and m.group(1) not in QT_LIBS_KEEP:
            os.remove(os.path.join(qt, "lib", name))
    plugins = os.path.join(qt, "plugins")
    for name in os.listdir(plugins):
        if name not in QT_PLUGIN_DIRS_KEEP:
            shutil.rmtree(os.path.join(plugins, name))
    for name in os.listdir(os.path.join(plugins, "platforms")):
        if name not in PLATFORMS_KEEP:
            os.remove(os.path.join(plugins, "platforms", name))
    for sub in ("qml", "translations"):
        path = os.path.join(qt, sub)
        if sub == "translations" and os.path.isdir(path):
            # Qt'nin kendi dugme metinleri (Evet/Hayir) icin qtbase_* gerekir.
            for name in os.listdir(path):
                if not re.match(r"qtbase_(tr|de|en)\.qm$", name):
                    os.remove(os.path.join(path, name))
        elif os.path.isdir(path):
            shutil.rmtree(path)
    # Eklentiler istege baglidir: silinmis bir Qt kutuphanesine baglanan
    # eklenti de silinir (orn. imageformats/libqpdf.so -> Qt5Pdf; CI'daki
    # tekerlekte vardi, yereldekinde yoktu — 2026-10-03).
    kept = set(os.listdir(os.path.join(qt, "lib")))
    for dirpath, _, files in os.walk(plugins):
        for name in files:
            full = os.path.join(dirpath, name)
            out = subprocess.run(["readelf", "-dW", full], capture_output=True,
                                 text=True).stdout
            needs = re.findall(r"\(NEEDED\)\s+Shared library: \[(libQt5[^\]]+)\]", out)
            gone = [n for n in needs if n not in kept]
            if gone:
                log(f"  eklenti siliniyor: {os.path.relpath(full, qt)} ({', '.join(gone)})")
                os.remove(full)
    for name in os.listdir(pyqt):
        full = os.path.join(pyqt, name)
        mod = name.split(".")[0]
        if (name.endswith(".so") or name.endswith(".pyi")) and mod not in PYQT_KEEP:
            os.remove(full)
    for name in ("bindings", "uic", "pyrcc_main.py", "pylupdate_main.py",
                 "pyrcc.abi3.so", "pylupdate.abi3.so"):
        full = os.path.join(pyqt, name)
        if os.path.isdir(full):
            shutil.rmtree(full)
        elif os.path.exists(full):
            os.remove(full)
    for name in os.listdir(site):
        if name.startswith(("pip", "setuptools", "wheel")):
            full = os.path.join(site, name)
            shutil.rmtree(full) if os.path.isdir(full) else os.remove(full)
    stdlib = os.path.join(appdir, "opt", PY, "lib", PY)
    for sub in STDLIB_DROP:
        shutil.rmtree(os.path.join(stdlib, sub), ignore_errors=True)
    tcl = os.path.join(appdir, "usr", "share", "tcltk")
    shutil.rmtree(tcl, ignore_errors=True)
    for dirpath, dirnames, _ in os.walk(appdir):
        for d in list(dirnames):
            if d == "__pycache__":
                shutil.rmtree(os.path.join(dirpath, d))
                dirnames.remove(d)


def elf_files(appdir: str):
    for dirpath, _, files in os.walk(appdir):
        for name in files:
            path = os.path.join(dirpath, name)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            with open(path, "rb") as f:
                if f.read(4) == b"\x7fELF":
                    yield path


def check_dependencies(appdir: str) -> str:
    """Her ELF'in NEEDED listesi pakette ya da sistemde var mi; en yuksek glibc."""
    bundled = {os.path.basename(p) for p in elf_files(appdir)}
    for dirpath, _, files in os.walk(appdir):
        bundled.update(f for f in files if ".so" in f)
    missing = {}
    glibc = []
    for path in elf_files(appdir):
        out = subprocess.run(["readelf", "-dW", path], capture_output=True,
                             text=True).stdout
        for need in re.findall(r"\(NEEDED\)\s+Shared library: \[([^\]]+)\]", out):
            if need.startswith("libQt5") or need.startswith("libicu"):
                if need not in bundled:
                    missing.setdefault(need, []).append(os.path.relpath(path, appdir))
        sym = subprocess.run(["readelf", "-VW", path], capture_output=True,
                             text=True).stdout
        glibc += [tuple(int(x) for x in v.split("."))
                  for v in re.findall(r"GLIBC_(\d+\.\d+(?:\.\d+)?)", sym)]
    if missing:
        for need, users in missing.items():
            log(f"  EKSIK {need}: {users[:3]}")
        raise SystemExit("HATA: silinen bir Qt kutuphanesine hala ihtiyac var")
    top = max(glibc) if glibc else ()
    return ".".join(str(x) for x in top)


def build(keep: bool = False) -> str:
    version = app_version()
    python_ai = fetch(PYTHON)
    tool = fetch(APPIMAGETOOL)

    appdir = os.path.join(WORK, "DiskUltimate.AppDir")
    shutil.rmtree(appdir, ignore_errors=True)
    shutil.rmtree(os.path.join(WORK, "squashfs-root"), ignore_errors=True)
    log("Python aciliyor...")
    run([python_ai, "--appimage-extract"], cwd=WORK, stdout=subprocess.DEVNULL)
    os.rename(os.path.join(WORK, "squashfs-root"), appdir)
    for name in os.listdir(appdir):          # Python'un kendi baslaticisi/ikonu
        if name.endswith((".desktop", ".png")) or name in ("AppRun", ".DirIcon"):
            os.remove(os.path.join(appdir, name))
    for sub in ("applications", "icons", "metainfo"):
        shutil.rmtree(os.path.join(appdir, "usr", "share", sub), ignore_errors=True)

    log("PyQt5 kuruluyor: " + " ".join(PIP_PACKAGES))
    py = os.path.join(appdir, "opt", PY, "bin", PY)
    run([py, "-s", "-m", "pip", "install", "--no-cache-dir", "--quiet",
         "--no-warn-script-location", *PIP_PACKAGES])

    log("Kullanilmayan parcalar siliniyor...")
    trim(appdir)

    log("Uygulama kopyalaniyor...")
    share = os.path.join(appdir, "usr", "share", "diskultimate")
    os.makedirs(share)
    shutil.copy2(os.path.join(ROOT, "main.py"), share)
    shutil.copy2(os.path.join(ROOT, "LICENSE"), share)
    shutil.copytree(os.path.join(ROOT, "src", "diskultimate"),
                    os.path.join(share, "src", "diskultimate"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    with open(os.path.join(appdir, "AppRun"), "w", newline="\n") as f:
        f.write(APPRUN)
    os.chmod(os.path.join(appdir, "AppRun"), 0o755)
    with open(os.path.join(appdir, "diskultimate.desktop"), "w") as f:
        f.write(DESKTOP)
    icon = os.path.join(ROOT, "src", "diskultimate", "ui", "resources",
                        "app-icon-256.png")
    shutil.copy2(icon, os.path.join(appdir, "diskultimate.png"))
    os.symlink("diskultimate.png", os.path.join(appdir, ".DirIcon"))
    hicolor = os.path.join(appdir, "usr", "share", "icons", "hicolor",
                           "256x256", "apps")
    os.makedirs(hicolor)
    shutil.copy2(icon, os.path.join(hicolor, "diskultimate.png"))

    log("Bagimliliklar denetleniyor...")
    glibc = check_dependencies(appdir)
    log(f"  gereken en yuksek glibc: {glibc}")

    os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
    out = os.path.join(ROOT, "dist", f"DiskUltimate-{version}-x86_64.AppImage")
    log("Paketleniyor...")
    env = dict(os.environ, ARCH="x86_64", VERSION=version)
    run([tool, "--appimage-extract-and-run", "--no-appstream", appdir, out],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    if not keep:
        shutil.rmtree(appdir)
    log(f"Hazir: {out} ({os.path.getsize(out) / 1e6:.1f} MB)")
    log(f"SHA-256: {sha256(out)}")
    return out


if __name__ == "__main__":
    build(keep="--keep" in sys.argv)
