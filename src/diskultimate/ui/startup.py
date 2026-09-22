"""Acilis akisi: pencere acilmadan once yetki yukseltmesi.

`main.py` ince kalir; arayuze ait karar ve pencereler burada durur.
"""
from __future__ import annotations

import os
import sys

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QProgressDialog

from ..core import diagnostics
from ..core.platform import (elevation_available, elevation_name, is_elevated,
                             relaunch_elevated)
from ..i18n import tr

NO_ROOT_FLAG = "--no-root"


def elevate_at_startup(app_name: str = "DiskUltimate") -> bool:
    """Pencere acilmadan once root'a gecmeyi dener.

    True donerse yetkili kopya basladi ve **bu kopya kapanmalidir**.

    Uygulama fiziksel disklerde root olmadan ise yaramadigi icin yetki
    kosulsuz istenir (ADR 0042). Cikis kapilari:
        --no-root                            tek seferlik
        DISKULTIMATE_AUTO_ROOT=0             kalici (ortam)
        DISKULTIMATE_NO_ELEVATION_PROMPT=1   otomatik kosumlar/testler

    Yetki verilmezse uygulama **yine acilir**: goruntu dosyalariyla
    calismak icin yetki gerekmez.
    """
    if is_elevated() or NO_ROOT_FLAG in sys.argv:
        return False
    if os.environ.get("DISKULTIMATE_AUTO_ROOT") == "0":
        return False
    if os.environ.get("DISKULTIMATE_NO_ELEVATION_PROMPT") == "1":
        diagnostics.info("acilista yetki istegi ortam degiskeniyle bastirildi")
        return False
    allowed, reason = elevation_available()
    if not allowed:
        diagnostics.info(f"acilista yetki istenmedi: {reason}")
        return False

    launch, error = relaunch_elevated()
    if launch is None:
        diagnostics.warn(f"acilista yetki alinamadi: {error}")
        return False

    # Beklemek zorunlu: pkexec yetkilendirmeyi kendi ebeveynine bakarak yapar
    # (ADR 0039). Bekleme sirasinda kullanici ne oldugunu gormeli.
    dialog = QProgressDialog(
        tr("{} yetkisi isteniyor. Parola sorulursa girin.", elevation_name()),
        tr("Yetkisiz devam et"), 0, 0, None)
    dialog.setWindowTitle(app_name)
    dialog.setMinimumDuration(0)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)
    outcome = {"started": False}

    def check_state() -> None:
        state, message = launch.poll()
        if state == launch.WAITING:
            if dialog.wasCanceled():
                launch.cleanup()
                diagnostics.info("acilista yetki beklemekten vazgecildi")
                dialog.close()
            return
        if state == launch.STARTED:
            outcome["started"] = True
            diagnostics.info("yetkili kopya acildi; acilis kopyasi kapaniyor")
        else:
            diagnostics.warn(f"yetkili kopya baslatilamadi: {message}")
        launch.cleanup()
        dialog.close()

    poll_timer = QTimer()
    poll_timer.setInterval(250)
    poll_timer.timeout.connect(check_state)
    poll_timer.start()
    dialog.exec_()
    poll_timer.stop()
    return outcome["started"]


