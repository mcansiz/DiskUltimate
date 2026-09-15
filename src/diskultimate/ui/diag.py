"""Tanilama altyapisinin Qt tarafina baglanmasi.

`core/diagnostics.py` saf Python kalir; Qt ile ilgili her sey buradadir:

- **Nabiz** — arayuz is parcaciginda calisan bir `QTimer`. Arayuz bloke
  olursa bu zamanlayici da durur ve arka plandaki `Watchdog` durumu anlar.
- **Qt uyarilari** — `qInstallMessageHandler` ile Qt kendi uyarilarini
  terminale degil oturum gunlugune yazar.
- **Yakalanmamis istisnalar** — `sys.excepthook`; PyQt5 bir yuvadaki (slot)
  istisnayi yalnizca terminale basar, gunlukte iz kalmazdi.
- **Donma bildirimi** — donma bitince arayuze sinyal gonderilir; pencere
  bunu islem gunlugune yazar ve rapor yolunu gosterir.
"""
from __future__ import annotations

import sys
from typing import Optional

from PyQt5.QtCore import (QObject, QTimer, QtCriticalMsg, QtFatalMsg,
                          QtWarningMsg, pyqtSignal, qInstallMessageHandler)

from ..core import diagnostics

HEARTBEAT_MS = 200


class UiDiagnostics(QObject):
    """Arayuz ile donma yakalayici arasindaki koprü."""

    # (rapor yolu, saniye) — donma **bittikten sonra** yayimlanir
    freezeDetected = pyqtSignal(str, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.timer = QTimer(self)
        self.timer.setInterval(HEARTBEAT_MS)
        self.timer.timeout.connect(diagnostics.beat)
        self._previous_hook = None

    # -- kurulum ----------------------------------------------------------
    def start(self) -> None:
        diagnostics.start_watchdog(callback=self._on_freeze)
        self.timer.start()

    def stop(self) -> None:
        self.timer.stop()
        diagnostics.stop_watchdog()
        if self._previous_hook is not None:
            sys.excepthook = self._previous_hook
            self._previous_hook = None

    def install_hooks(self) -> None:
        """Qt iletilerini ve yakalanmamis istisnalari gunluge yonlendirir."""
        qInstallMessageHandler(_qt_message)
        self._previous_hook = sys.excepthook
        sys.excepthook = self._excepthook

    # -- geri cagirmalar ---------------------------------------------------
    def _on_freeze(self, path: str, seconds: float) -> None:
        # Watchdog ayri bir is parcacigindadir; sinyal Qt tarafindan
        # kuyruga alinir ve arayuz is parcaciginda islenir.
        self.freezeDetected.emit(path, seconds)

    def _excepthook(self, kind, value, trace) -> None:
        diagnostics.error(f"yakalanmamis istisna: {kind.__name__}", value)
        if self._previous_hook is not None:
            self._previous_hook(kind, value, trace)


def _qt_message(mode, context, message: str) -> None:
    text = f"Qt: {message}"
    if mode in (QtWarningMsg, QtCriticalMsg, QtFatalMsg):
        diagnostics.warn(text)
    else:
        diagnostics.debug(text)


def install(parent: Optional[QObject] = None) -> UiDiagnostics:
    """Tanilamayi kurar ve baslatir; olusan nesneyi dondurur.

    Nesne cagiran tarafindan saklanmalidir (yoksa cop toplayici zamanlayiciyi
    yok eder).
    """
    diagnostics.configure()
    ui_diag = UiDiagnostics(parent)
    ui_diag.install_hooks()
    ui_diag.start()
    return ui_diag
