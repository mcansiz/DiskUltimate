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

from diskultimate.ui.qt.QtCore import Qt  # noqa: E402
from diskultimate.ui.qt.QtWidgets import QApplication  # noqa: E402

from diskultimate import i18n  # noqa: E402
from diskultimate.core import diagnostics  # noqa: E402
from diskultimate.core.platform import (is_elevated,  # noqa: E402
                                        set_app_user_model_id,
                                        signal_elevated_ready)
from diskultimate.ui import qt, qt_i18n  # noqa: E402
from diskultimate.ui.appicon import apply_app_icon  # noqa: E402
from diskultimate.ui.main_window import (APP_NAME, APP_VERSION,  # noqa: E402
                                         MainWindow)
from diskultimate.ui.startup import elevate_at_startup  # noqa: E402
from diskultimate.ui.theme import apply_theme, saved_theme  # noqa: E402

def start(argv=None):
    """Uygulamayi kurar ve ana pencereyi gosterir: (app, window).

    `window` None ise yetkili kopya devraldi, bu kopya kapanmalidir.
    Olay dongusunu calistirmaz: gozlemci arayuz testi (ADR 0097) uygulamayi
    **ayni acilis yolundan** kurup kendisi surer.
    """
    argv = list(sys.argv if argv is None else argv)
    # Tanilama pencereden once acilir: acilista yetki istegi de gunluge girsin
    # (iki kez cagrilmasi zararsizdir, ayni yolu dondurur).
    diagnostics.configure()
    # Arayuz dili: DISKULTIMATE_LANG > menuden kaydedilen > sistem dili >
    # Turkce (ADR 0027). Qt cevirisinden ve yetki penceresinden ONCE: ikisi
    # de etkin dili okur. Bu cagri hicbir surumde yoktu; ortam degiskeni ve
    # kayitli dil acilista yok sayiliyordu (2026-10-18).
    diagnostics.info(f"arayuz dili: {i18n.initialize()}")
    # Paketin gercekte hangi baglamayla acildigi (ADR 0102); smoke_launch
    # --qt bu satiri denetler.
    diagnostics.info(f"qt baglamasi: {qt.API} {qt.PYQT_VERSION_STR} / Qt {qt.QT_VERSION_STR}")
    # Windows gorev cubugu ikonu: kimlik PENCERE YARATILMADAN once atanmali,
    # sonra atamak ise yaramaz (Windows disinda no-op).
    set_app_user_model_id("DiskUltimate.DiskUltimate")
    # Qt6'da yuksek DPI hep aciktir; bu iki oznitelik kaldirildi (ADR 0102).
    for name in ("AA_EnableHighDpiScaling", "AA_UseHighDpiPixmaps"):
        attribute = getattr(Qt.ApplicationAttribute, name, None)
        if attribute is not None:
            QApplication.setAttribute(attribute, True)
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("DiskUltimate")
    # Ikon uygulama duzeyinde verilir: butun ust duzey pencereler ve
    # diyaloglar bunu miras alir, tek tek setWindowIcon gerekmez. Yetki
    # penceresinden (ADR 0042) once cagrilir ki parola sorulan pencere de
    # ikonlu acilsin.
    apply_app_icon(app)
    # Qt'nin kendi metinleri (Evet/Hayir, dosya diyalogu) de arayuz dilinde
    # olsun; yetki penceresinden ONCE ki o da cevrili acilsin.
    qt_i18n.install()
    # Ikon paketleri calisma aninda adla yuklenir; paketlenmis kopyada eksik
    # kalirsa (PyInstaller gormezse) burada gorunur.
    from diskultimate.ui import iconpacks
    diagnostics.info(f"ikon paketleri: {len(iconpacks.notices())}/"
                     f"{len(iconpacks.MODULES)} yuklendi")
    # Gorunum: Sistem (masaustunun kendi temasi) / Acik / Koyu — kayitli
    # secim ya da DISKULTIMATE_THEME (ADR 0088). Yetki penceresinden once ki
    # o da secilen temayla acilsin.
    theme = apply_theme(app, saved_theme())

    if elevate_at_startup(APP_NAME):
        return app, None              # yetkili kopya devraldi

    window = MainWindow()
    if not is_elevated():
        # Yetki acilista istendi ve verilmedi; disk listesi hazir olunca ayni
        # soru bir daha sorulmaz.
        window.mark_elevation_asked()
    window.log(f"Qt platformu: {app.platformName()} | stil: "
                f"{app.style().objectName()} | tema: {theme}")
    window.show()
    # GitHub'da yeni surum var mi — arka planda, sessiz (ADR 0090)
    window.schedule_update_check()
    # Yetkili kopya olarak acildiysak, bizi baslatan kopya kapanmak icin bu
    # bildirimi bekliyor (bkz. core.platform.ElevatedLaunch, ADR 0039).
    signal_elevated_ready()
    if len(argv) > 1 and os.path.isfile(argv[1]):
        window.open_path(argv[1])
    return app, window


def main() -> int:
    app, window = start()
    if window is None:
        return 0
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
