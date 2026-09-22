"""Uygulamanin kendi marka ikonu — **dosyadan** okunur.

## Neden bu ikon cizilmiyor

`ui/icons.py` icindeki islem ikonlari (bicimlendir, boyutlandir, onyukleme...)
QPainter ile cizilir: platformdan bagimsiz ve bagimliliksiz olsunlar diye.
Uygulamanin kendi ikonu farklidir:

- Kullanicinin verdigi bir sanat eseridir, kodla yeniden uretilmez.
- Pencere ikonu disinda **paketleyicinin** de ayni dosyaya ihtiyaci vardir
  (Windows exe kaynagi, macOS paketi, Linux masaustu girdisi) — orada QPainter
  calismaz, dosya gerekir.

Bu yuzden `.ico` depoda durur. Calisma zamani bagimliligi eklemez: `.ico`
okuyucusu Qt'nin kendi imageformats eklentisindedir, PyQt5 disinda bir sey
gerekmez (SVG'den farki budur — `PyQt5.QtSvg` ayri pakettir, bkz. icons.py).

## Yol cozumu

`i18n.CATALOG_DIR` ile ayni yontem: paketin yanindaki klasor. PyInstaller
paketinde de ayni goreli yerde durur (`DiskUltimate.spec` -> datas), bu yuzden
"paketlenmis miyiz" diye ayri bir dal gerekmez.

Kaynak dosya: `assets/branding/favicon.ico`. Buradaki `.ico` ondan **uretilir**:
    python3 assets/branding/uret_ikon.py assets/branding/favicon.ico \
            src/diskultimate/ui/resources
"""
from __future__ import annotations

import os

from PyQt5.QtGui import QIcon

RESOURCE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources")
ICON_PATH = os.path.join(RESOURCE_DIR, "app-icon.ico")

_cache: QIcon = None


def app_icon() -> QIcon:
    """Uygulama ikonunu dondurur. Dosya yoksa **bos** ikon doner, cokmez.

    Bos ikon zararsizdir: Qt o zaman pencereye sistem varsayilanini koyar.
    Ikonun yoklugu uygulamayi calistirmamak icin bir sebep degildir.
    """
    global _cache
    if _cache is None:
        _cache = QIcon(ICON_PATH) if os.path.isfile(ICON_PATH) else QIcon()
    return _cache


def apply_app_icon(app) -> bool:
    """Ikonu uygulama duzeyinde ayarlar; basarili olduysa True doner.

    Bu islev `main.py` disinda da cagrilabilsin diye ayri durur: duman testi
    (`tests/ui_smoke.py`) kendi QApplication'ini kurar ve `main()` icinden
    gecmez. Baglama yalnizca `main.py` icinde yazili olsaydi test gercek kod
    yolunu hic gormez, "ikon var" iddiasi sinanmamis kalirdi.

    Uygulama duzeyinde verilir, pencere basina degil: butun ust duzey
    pencereler ve diyaloglar bunu devralir.
    """
    icon = app_icon()
    if icon.isNull():
        return False
    app.setWindowIcon(icon)
    return True


def icon_file(kind: str = "ico") -> str:
    """Paketleyicinin isteyecegi dosya yolu: 'ico', 'icns' veya '<boyut>' (PNG).

    Ornek: icon_file("icns"), icon_file("256") -> app-icon-256.png
    """
    if kind in ("ico", "icns"):
        return os.path.join(RESOURCE_DIR, "app-icon.%s" % kind)
    return os.path.join(RESOURCE_DIR, "app-icon-%s.png" % kind)
