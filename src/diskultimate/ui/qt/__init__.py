"""Qt baglamasi secimi: PyQt5 ya da PyQt6 (ADR 0102).

Arayuz kodu PyQt'yi dogrudan import etmez; `from ..qt.QtWidgets import ...`
yazar. Bu paket hangi baglamanin kullanilacagina bir kez karar verir:

    DISKULTIMATE_QT=pyqt6 python3 main.py     # zorla
    DISKULTIMATE_QT=pyqt5 python3 main.py

Ortam degiskeni yoksa once PyQt6, sonra PyQt5 denenir: dagitilan paketler
PyQt6 tasir; PyQt5 kaynaktan calistirilan eski sistemler (Python 3.8,
Alpine `py3-qt5`) icin desteklenir. Iki baglamada da calisan yazim:

- enum'lar tam nitelikli: `Qt.AlignmentFlag.AlignLeft`, `QMessageBox.StandardButton.Yes`
  (`tests.qt6_check` denetler, `--write` duzeltir)
- `exec()` (`exec_()` degil), `fontMetrics().horizontalAdvance()`
- fare olayinda `event.pos().x()` (PyQt6 `QMouseEvent.x()` vermez)
- enum'dan sayi gerekirse `enum_value()` (PyQt6'da `int(Qt.KeyboardModifier...)` hata verir)
"""

from __future__ import annotations

import importlib
import os

_CHOICES = ("pyqt6", "pyqt5")


def _select() -> str:
    requested = os.environ.get("DISKULTIMATE_QT", "").strip().lower()
    order = [requested] if requested in _CHOICES else list(_CHOICES)
    error = None
    for name in order:
        try:
            importlib.import_module({"pyqt5": "PyQt5", "pyqt6": "PyQt6"}[name] + ".QtCore")
            return name
        except ImportError as exc:
            error = exc
    raise ImportError(f"PyQt6 ya da PyQt5 gerekli ({error})")


API = _select()
QT6 = API == "pyqt6"
PACKAGE = "PyQt6" if QT6 else "PyQt5"

_core = importlib.import_module(PACKAGE + ".QtCore")
PYQT_VERSION_STR = _core.PYQT_VERSION_STR
QT_VERSION_STR = _core.QT_VERSION_STR
try:
    SIP_VERSION_STR = importlib.import_module(PACKAGE + ".sip").SIP_VERSION_STR
except Exception:                                   # noqa: BLE001
    SIP_VERSION_STR = ""


def package_dir() -> str:
    """Baglamanin kurulu oldugu klasor (Qt cevirileri onun altindadir)."""
    return os.path.dirname(importlib.import_module(PACKAGE).__file__)


def enum_value(value) -> int:
    """Enum/bayrak -> int. PyQt5 enum'lari int'tir; PyQt6'da `.value` gerekir."""
    return value.value if hasattr(value, "value") else int(value)


def translations_path() -> str:
    """Qt'nin kendi ceviri (.qm) klasoru."""
    info = _core.QLibraryInfo
    if QT6:
        return info.path(info.LibraryPath.TranslationsPath)
    return info.location(info.TranslationsPath)
