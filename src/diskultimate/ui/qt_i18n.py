"""Qt'nin **kendi** metinlerinin cevirisi (standart dugmeler, dosya diyalogu).

## Sorun

Arayuz Turkceyken onay kutularinda "Yes" / "No" gorunuyordu (kullanici
bildirimi, 2026-09-28). Bu metinler bizim sozluklerimizden degil Qt'den gelir:
`QMessageBox.Yes` dugmesinin yazisini `QPlatformTheme` uretir ve Qt'nin
ceviri dosyasina (`qtbase_tr.qm`) bakar. Uygulama o dosyayi hic yuklemiyordu.

## Iki katman

1. **qtbase_<dil>.qm** — Qt kurulumundaki resmi ceviri. Dosya diyalogu, sag
   tik menusu ("Kopyala/Yapistir") gibi butun Qt metinlerini kapsar. PyQt5
   tekerlegiyle gelir; ama dagitim paketlerinde (ayri `qttranslations`) ya da
   paketlenmis exe'de **eksik olabilir**.
2. **Yedek cevirmen** (`_ButtonTranslator`) — standart dugme metinlerini
   bizim `.po` sozluklerimizden cevirir. `.qm` bulunmasa da Evet/Hayir/Tamam/
   Iptal dogru gorunur. Son yuklenen cevirmene once bakildigi icin bu katman
   Qt'ninkinin ustundedir: dugmeler her platformda ayni kelimeyi kullanir.

Dil degisince (`i18n.add_listener`) iki katman da yenilenir; Qt acik
pencerelere `LanguageChange` gonderir, yeni acilan diyaloglar yeni dilde
gelir.
"""
from __future__ import annotations

import os
from typing import List, Optional

from PyQt5.QtCore import QCoreApplication, QLibraryInfo, QTranslator

from .. import i18n
from ..core import diagnostics
from ..i18n import mark, tr

# Qt kaynak metni -> bizim kaynak metnimiz (Turkce, `tr()` ile cevrilir)
BUTTONS = {
    "OK": mark("Tamam"), "&OK": mark("&Tamam"),
    "Save": mark("Kaydet"), "&Save": mark("&Kaydet"),
    "Save All": mark("Tumunu kaydet"),
    "Open": mark("Ac"), "&Open": mark("&Ac"),
    "&Yes": mark("&Evet"), "Yes": mark("Evet"),
    "Yes to &All": mark("&Tumune evet"),
    "&No": mark("&Hayir"), "No": mark("Hayir"),
    "N&o to All": mark("T&umune hayir"),
    "Abort": mark("Durdur"), "Retry": mark("Yeniden dene"),
    "Ignore": mark("Yok say"), "Close": mark("Kapat"),
    "&Close": mark("&Kapat"),
    "Cancel": mark("Iptal"), "&Cancel": mark("&Iptal"),
    "Discard": mark("Vazgec"), "Don't Save": mark("Kaydetme"),
    "Help": mark("Yardim"), "Apply": mark("Uygula"), "Reset": mark("Sifirla"),
    "Restore Defaults": mark("Varsayilanlara don"),
    "Show Details...": mark("Ayrintilari goster..."),
    "Hide Details...": mark("Ayrintilari gizle..."),
}
# Bu metinleri ureten Qt baglamlari
CONTEXTS = ("QPlatformTheme", "QDialogButtonBox", "QMessageBox",
            "QGnomeTheme", "QWizard")

_installed: List[QTranslator] = []


class _ButtonTranslator(QTranslator):
    """Standart dugme metinlerini `.po` sozluklerimizden cevirir."""

    def translate(self, context, source, disambiguation=None, n=-1):
        if context in CONTEXTS and source in BUTTONS:
            # Ingilizce dahil her dil bizim sozlugumuzden: `tr` kaynak dilde
            # Turkceyi, `en`de en.po'daki karsiligi dondurur.
            return tr(BUTTONS[source])
        return ""                      # bos = "bende yok", Qt siradakine bakar

    def isEmpty(self):
        return False


def _qt_translation_dirs() -> List[str]:
    dirs = [QLibraryInfo.location(QLibraryInfo.TranslationsPath)]
    try:
        import PyQt5
        dirs.append(os.path.join(os.path.dirname(PyQt5.__file__), "Qt5",
                                 "translations"))
    except Exception:                  # noqa: BLE001
        pass
    return [d for d in dirs if d and os.path.isdir(d)]


def _load_qtbase(code: str) -> Optional[QTranslator]:
    """qtbase_<dil>.qm varsa yukler; yoksa None (yedek katman yeter)."""
    lang = "tr" if code == i18n.SOURCE_LANGUAGE else code
    if lang in (i18n.PSEUDO_LANGUAGE, "en"):
        return None
    for folder in _qt_translation_dirs():
        translator = QTranslator()
        if translator.load(f"qtbase_{lang}", folder):
            return translator
    return None


def apply(code: str = None) -> dict:
    """Qt cevirmenlerini etkin dile gore (yeniden) kurar.

    Dondurur: {"qtbase": yuklendi_mi, "language": kod}.
    """
    app = QCoreApplication.instance()
    if app is None:
        return {"qtbase": False, "language": code}
    code = code or i18n.current_language()
    for translator in _installed:
        app.removeTranslator(translator)
    _installed.clear()
    qtbase = _load_qtbase(code)
    if qtbase is not None:
        app.installTranslator(qtbase)
        _installed.append(qtbase)
    buttons = _ButtonTranslator()
    app.installTranslator(buttons)        # son kurulan once sorulur
    _installed.append(buttons)
    return {"qtbase": qtbase is not None, "language": code}


def install() -> dict:
    """Uygulama acilisinda bir kez: kurar ve dil degisimini dinler."""
    result = apply()
    # Paketlenmis kopyada .qm'nin gercekten geldigi buradan okunur
    diagnostics.info(f"qt cevirisi: qtbase={'var' if result['qtbase'] else 'yok'}"
                     f" dil={result['language']}")
    i18n.add_listener(lambda code: apply(code))
    return result
