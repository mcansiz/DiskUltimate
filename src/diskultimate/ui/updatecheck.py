"""Guncelleme denetimi — arayuz tarafi (ADR 0090).

Iki yol:

* **Acilista, sessiz:** ayar aciksa (varsayilan) ve `DISKULTIMATE_UPDATE_CHECK`
  "0" degilse pencere acildiktan birkac saniye sonra arka planda denetlenir.
  Yeni surum yoksa ya da ag yoksa hicbir pencere acilmaz (yalnizca gunluk).
  Yeni surum varsa sorulur; "Bu surumu atla" denirse o surum bir daha
  sessizce sorulmaz.
* **Elle (Yardim > Guncellemeleri denetle):** sonuc her durumda gosterilir.

Ag cagrisi `core/updates.py` icinde, burada yalnizca is parcacigi ve pencere.
Baglanti `platform.open_url` ile acilir (Linux'ta root olarak degil).
"""
from __future__ import annotations

import os

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import QMessageBox

from ..core import diagnostics, settings
from ..core import updates
from ..core.platform import open_url
from ..i18n import tr

AUTO_KEY = "update_check"          # acilista denetle (varsayilan: evet)
SKIP_KEY = "update_skip"           # sessiz denetimde bir daha sorulmayan surum


def auto_enabled() -> bool:
    if os.environ.get(updates.ENV_NAME, "").strip() == "0":
        return False
    return bool(settings.get(AUTO_KEY, True))


class UpdateWorker(QThread):
    """Arka planda denetler; sonuc `done(yeni, en_son, hata)`."""

    done = pyqtSignal(object, object, str)

    def __init__(self, current: str, parent=None):
        super().__init__(parent)
        self.current = current

    def run(self):
        try:
            with diagnostics.span("update_check"):
                newer, latest = updates.check(self.current)
            self.done.emit(newer, latest, "")
        except Exception as exc:                  # noqa: BLE001
            self.done.emit(None, None, str(exc))


def offer(parent, release, current: str, silent: bool) -> None:
    """Yeni surumu bildirir; kullanici isterse GitHub sayfasini acar."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Information)
    box.setWindowTitle(tr("Yeni surum var"))
    box.setText(tr("<b>{}</b> yayinlandi (kullandiginiz surum: {}).<br><br>"
                   "Indirmek icin GitHub sayfasini acmak ister misiniz?",
                   release.version, current))
    open_button = box.addButton(tr("GitHub sayfasini ac"), QMessageBox.AcceptRole)
    skip_button = (box.addButton(tr("Bu surumu atla"), QMessageBox.RejectRole)
                   if silent else None)
    box.addButton(tr("Daha sonra"), QMessageBox.RejectRole)
    box.setDefaultButton(open_button)
    box.exec_()
    if box.clickedButton() is open_button:
        if not open_url(release.url):
            QMessageBox.information(parent, tr("Yeni surum var"),
                                    tr("Tarayici acilamadi. Adres: {}", release.url))
    elif skip_button is not None and box.clickedButton() is skip_button:
        settings.set_value(SKIP_KEY, release.tag)
