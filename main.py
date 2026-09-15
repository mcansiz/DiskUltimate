#!/usr/bin/env python3
"""DiskUltimate — disk goruntusu yonetim araci. Giris noktasi."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))


def selected_platform() -> str:
    """Kullanilacak Qt platform eklentisini belirler.

    Karar `core.platform.preferred_qt_platform()` icinde, isletim sistemine gore
    verilir (Linux/Wayland -> XWayland). Kullanici her zaman gecersiz kilabilir:
        DISKULTIMATE_QPA=wayland python3 main.py
        QT_QPA_PLATFORM=wayland  python3 main.py
    """
    requested = os.environ.get("DISKULTIMATE_QPA", "").strip()
    if requested:
        return requested
    if os.environ.get("QT_QPA_PLATFORM"):
        return os.environ["QT_QPA_PLATFORM"]
    from diskultimate.core.platform import preferred_qt_platform
    return preferred_qt_platform()


_qpa_platform = selected_platform()
if _qpa_platform:
    os.environ["QT_QPA_PLATFORM"] = _qpa_platform

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from diskultimate.ui.main_window import APP_NAME, MainWindow  # noqa: E402
from diskultimate.ui.theme import apply_theme  # noqa: E402


def main() -> int:
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("DiskUltimate")
    # Gorunum: sistemin kendi Qt temasi. Ozel stil sayfasi uygulanmaz;
    # "Tema" bolumu eklendiginde buradan secilecek (bkz. theme.apply_theme).
    theme = apply_theme(app, os.environ.get("DISKULTIMATE_THEME", "system"))

    window = MainWindow()
    window.log(f"Qt platformu: {app.platformName()} | stil: "
                f"{app.style().objectName()} | tema: {theme}")
    window.show()
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        window.open_path(sys.argv[1])
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
