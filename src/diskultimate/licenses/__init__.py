"""Dagitimla birlikte gelen ucuncu taraf bilesenlerin lisanslari (ADR 0090).

Windows exe'si ve Linux AppImage'i Python'u, Qt'yi ve PyQt5'i **icinde**
tasir; bu bir yeniden dagitimdir ve lisanslar sunlari ister:

* **Qt** (LGPLv3): kullaniciya lisans metni ve Qt kullanildigina dair
  belirgin bir bildirim; Qt'nin kaynak kodu ya da onu edinmek icin yazili
  teklif; kullanicinin Qt'yi degistirip uygulamayi onunla calistirabilmesi.
  Uygulamanin tamami GPLv3 acik kaynaktir ve derleme betikleri depodadir:
  kullanici farkli bir Qt ile kaynaktan calistirabilir ya da yeniden
  paketleyebilir.
* **PyQt5** (GPLv3): uygulama da GPLv3 — uyumlu; kaynak GitHub'da.
* **PyQt5-sip** (BSD-2-Clause) ve **Python** (PSF): telif bildirimi ve
  lisans metni dagitimla birlikte verilir.

Metinler bu klasorde duz metin olarak durur, kurulu paketlerin kendi lisans
dosyalarindan alinmistir; "Ucuncu taraf lisanslari" penceresi gosterir.
"""
from __future__ import annotations

import os
from typing import Dict, List

from ..i18n import mark

LICENSE_DIR = os.path.dirname(os.path.abspath(__file__))

# Qt'nin kaynak kodu: LGPLv3 6. bolum. Kaynak bize sorulursa verilir; resmi
# arsiv de ayni surumu tasir. (Qt'nin kendi SSS'si yalnizca baglantiyi yeterli
# saymaz — bu yuzden yazili teklif.)
QT_SOURCE_URL = "https://download.qt.io/archive/qt/5.15/"

COMPONENTS: List[Dict[str, str]] = [
    {"key": "qt", "name": "Qt", "license": "LGPL-3.0", "file": "LGPL-3.0.txt",
     "url": "https://www.qt.io/",
     "copyright": "Copyright (C) The Qt Company Ltd. and other contributors",
     "note": mark("Bu program Qt kutuphanelerini GNU LGPL surum 3 kosullariyla "
                  "kullanir. Qt'yi degistirip uygulamayi onunla calistirma "
                  "hakkiniz vardir: uygulamanin tamami acik kaynaktir ve "
                  "kaynaktan farkli bir Qt ile calistirilabilir. Kullanilan "
                  "Qt surumunun kaynak kodunu en az uc yil boyunca istek "
                  "uzerine saglariz (proje sayfasinda bir istek acin); resmi "
                  "arsiv: {}")},
    {"key": "pyqt5", "name": "PyQt5", "license": "GPL-3.0", "file": "GPL-3.0.txt",
     "url": "https://www.riverbankcomputing.com/software/pyqt/",
     "copyright": "Copyright (c) Riverbank Computing Limited", "note": ""},
    {"key": "sip", "name": "PyQt5-sip", "license": "BSD-2-Clause",
     "file": "PyQt5-sip-BSD-2-Clause.txt",
     "url": "https://pypi.org/project/PyQt5-sip/",
     "copyright": "Copyright (c) Phil Thompson", "note": ""},
    {"key": "python", "name": "Python", "license": "PSF-2.0",
     "file": "Python-PSF-2.0.txt", "url": "https://www.python.org/",
     "copyright": "Copyright (c) 2001 Python Software Foundation; All Rights Reserved",
     "note": ""},
]


def text(component: Dict[str, str]) -> str:
    """Bilesenin lisans metni (dosya yoksa bos dize)."""
    try:
        with open(os.path.join(LICENSE_DIR, component["file"]), "r",
                  encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""
