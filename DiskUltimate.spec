# -*- mode: python ; coding: utf-8 -*-
# DiskUltimate — PyInstaller spec dosyası.
#   Windows:  build_exe.bat  (veya: py -3.12 -m PyInstaller --noconfirm DiskUltimate.spec)
#   Linux:    ./build_linux.sh  (veya: python3 -m PyInstaller --noconfirm DiskUltimate.spec)
#
# Uygulamanın tek çalışma zamanı bağımlılığı PyQt5'tir (CLAUDE.md: çekirdek saf
# Python). Bu yüzden collect_all kullanılmaz — PyInstaller'ın PyQt5 hook'u
# QtWidgets/QtCore/QtGui çekirdeğini zaten toplar; collect_all üstüne
# Designer/QML/çevirileri (~50 MB) gömerdi.
import glob
import os
import re
import sys

IS_LINUX = sys.platform.startswith('linux')

# SPECPATH: PyInstaller'ın spec'e verdiği, bu dosyanın bulunduğu dizin.
# Depo başka bir yola klonlanınca da çalışsın diye sabit yol yazılmaz.
ROOT = os.path.abspath(SPECPATH)          # noqa: F821 (PyInstaller tanımlar)
SRC = os.path.join(ROOT, 'src')

# Çeviri sözlükleri. `i18n.CATALOG_DIR` paketin yanındaki `catalogs/` klasörüne
# bakar; pakette de aynı göreli yerde durmalı, yoksa dil menüsü yalnızca
# kaynak dili (Türkçe) gösterir. `.po` dosyaları veri, kod değil — PyInstaller
# bunları kendiliğinden almaz.
datas = [
    (p, os.path.join('diskultimate', 'i18n', 'catalogs'))
    for p in sorted(glob.glob(os.path.join(SRC, 'diskultimate', 'i18n', 'catalogs', '*.po')))
]
if not datas:
    raise SystemExit('DiskUltimate.spec: src/diskultimate/i18n/catalogs/*.po bulunamadi')

# Uygulama ikonu. Çalışma anında `ui.appicon` paketin yanındaki `resources/`
# klasörüne bakar (çeviri sözlükleriyle aynı yöntem), bu yüzden pakette de aynı
# göreli yerde durmalı. Kaynağı `assets/branding/favicon.ico`; buradaki dosya
# ondan üretilir (assets/branding/uret_ikon.py).
ICON_DIR = os.path.join(SRC, 'diskultimate', 'ui', 'resources')
ICON_ICO = os.path.join(ICON_DIR, 'app-icon.ico')
ICON_ICNS = os.path.join(ICON_DIR, 'app-icon.icns')
if not os.path.isfile(ICON_ICO):
    raise SystemExit('DiskUltimate.spec: %s bulunamadi — uret: '
                     'python3 assets/branding/uret_ikon.py '
                     'assets/branding/favicon.ico src/diskultimate/ui/resources'
                     % ICON_ICO)
datas.append((ICON_ICO, os.path.join('diskultimate', 'ui', 'resources')))

# EXE'ye gömülecek ikon: Windows `.ico`, macOS `.icns` ister. Linux'ta
# PyInstaller bu alanı UYGULAMAZ, yalnızca "Ignoring icon; supported only on
# Windows and macOS!" uyarısı basar — bu yüzden orada hiç verilmez, yoksa her
# Linux paketlemesinde kullanıcıyı tedirgin eden bir uyarı çıkar.
# Linux'ta pencere ikonunun tek kaynağı `datas` ile pakete giren dosyadır.
EXE_ICON = None if IS_LINUX else [ICON_ICNS if sys.platform == 'darwin' else ICON_ICO]


a = Analysis(
    [os.path.join(ROOT, 'main.py')],
    # main.py çalışma anında `src`i yola ekler; Analysis statik çözümleme
    # yaptığı için `diskultimate` paketini burada da göstermek gerekir.
    pathex=[SRC],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Uygulama yalnızca QtWidgets/QtCore/QtGui kullanır (grep ile doğrulandı).
    # Geliştirme ortamında başka projelerden kalan ağır paketler varsa
    # gereksiz yere pakete sürüklenmesinler; temiz ortamda bu liste no-op'tur.
    excludes=[
        # Rakip Qt bağlamaları — ikisi birden yüklenirse çalışma anında çakışır.
        'PyQt6', 'PySide2', 'PySide6',
        # Kullanılmayan PyQt5 modülleri (QML/Quick yığını ~20 MB).
        'PyQt5.QtQml', 'PyQt5.QtQuick', 'PyQt5.QtQuickWidgets',
        'PyQt5.QtWebEngineWidgets', 'PyQt5.QtWebEngineCore', 'PyQt5.QtWebSockets',
        'PyQt5.QtMultimedia', 'PyQt5.QtMultimediaWidgets', 'PyQt5.QtBluetooth',
        'PyQt5.QtNetworkAuth', 'PyQt5.QtPositioning', 'PyQt5.QtSerialPort',
        'PyQt5.QtSql', 'PyQt5.QtTest', 'PyQt5.QtOpenGL', 'PyQt5.Qt3DCore',
        # Bilimsel/çizim yığını — bu uygulama hiçbirini kullanmaz.
        'numpy', 'scipy', 'matplotlib', 'pandas', 'numba', 'llvmlite',
        'PIL', 'lxml', 'IPython',
        # Arayüz Qt ile çizilir; tkinter yalnızca boyut ekler.
        'tkinter',
        # Yalnızca geliştirme/test araçları.
        'pytest', 'unittest',
    ],
    noarchive=False,
    optimize=0,
)

# ---------------------------------------------------------------------------
# Qt hook'unun eklediği ama bu saf-QtWidgets uygulamasının kullanmadığı
# fazlalıklar.
#
# Her platform:
#   qwebgl platform eklentisi Qt5Qml/QmlModels/Quick/WebSockets'i sürüklüyor;
#   opengl32sw (20 MB) + d3dcompiler_47 yazılım-OpenGL yedeği — QtWidgets
#   raster ile çizer, OpenGL context hiç açılmaz.
#
# Linux'a özgü (~4 MB):
#   Wayland istemcisi + tüm wayland-* eklentileri (uygulama XWayland/xcb ile
#   çalışır; bkz. core/platform.preferred_qt_platform), EglFS/linuxfb/vnc/
#   offscreen/minimalegl platformları (gömülü sistemler/headless), evdev/tuio
#   giriş eklentileri (X11 altında gerekmez), nadir resim formatları.
#
# KALMALI: libqxcb, libQt5XcbQpa, libQt5DBus, xcbglintegrations,
#   platforminputcontexts, platformthemes, iconengines, libqjpeg/libqgif/
#   libqico/libqsvg — xcb eklentisi ve ikon yükleme bunlara bağımlı.
#   Windows'ta qwindows.dll ve styles/qwindowsvistastyle.dll da kalmalı:
#   uygulama sistemin Qt temasını kullanır (ADR 0013).
# ---------------------------------------------------------------------------
_DROP_COMMON = (
    r'qwebgl|Qt5Qml|Qt5QmlModels|Qt5Quick|Qt5WebSockets|opengl32sw|d3dcompiler_47'
)
_DROP_LINUX = (
    r'|Qt5WaylandClient|Qt5EglFSDeviceIntegration'
    r'|plugins[/\\]wayland-'
    r'|plugins[/\\]generic[/\\]'
    r'|plugins[/\\]platforms[/\\]libq(vnc|linuxfb|eglfs|minimalegl|offscreen|wayland)'
    r'|plugins[/\\]imageformats[/\\]libq(tiff|webp|icns|tga|wbmp)'
)
_DROP = re.compile(
    '(' + _DROP_COMMON + (_DROP_LINUX if IS_LINUX else '') + ')', re.I,
)
a.binaries = [b for b in a.binaries if not _DROP.search(b[0])]

# Qt'nin kendi çevirileri (~1,6 MB) — uygulama metinlerini kendi `.po`
# sözlüklerinden okur (ADR 0027), QTranslator kullanılmaz. `uic` widget-plugin
# stub'ları da gerekmez: projede hiç `.ui` dosyası yok, arayüz koddan kurulur.
_DROP_DATA = re.compile(r'(Qt5[/\\]translations[/\\]|uic[/\\]widget-plugins[/\\])', re.I)
a.datas = [d for d in a.datas if not _DROP_DATA.search(d[0])]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='DiskUltimate',
    debug=False,
    bootloader_ignore_signals=False,
    # Linux: .so sembol tablolarını soy (özellikle pyenv/kaynaktan derlenmiş
    # libpython 31 MB -> ~8 MB). `strip` komutu için: sudo apt install binutils
    # Windows'ta PE dosyalarına uygulanmaz, zararsız.
    strip=IS_LINUX,
    # onefile zaten her parçayı zlib ile sıkıştırıyor; UPX üstüne pek bir şey
    # eklemez, Qt ile nadiren sorun çıkarır — kapalı.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    # Pencereli uygulama: konsol penceresi açılmaz. Hata ayıklarken
    # geçici olarak True yapıp exe'yi komut satırından çalıştırın.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Uygulamanın kendi ikonu (`ui/icons.py` içindeki çizilen işlem
    # ikonlarından ayrıdır — gerekçe: ui/appicon.py docstring'i).
    icon=EXE_ICON,
)
