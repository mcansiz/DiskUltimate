"""PyQt5/PyQt6 ortak yazim denetimi (statik) — ADR 0102.

Arayuz kodu iki baglamada da calismali. Bu denetim PyQt6'da kiran yazimlari
bulur:

- kisa enum: `Qt.AlignLeft`, `QMessageBox.Yes` -> PyQt6 yalnizca tam nitelikli
  `Qt.AlignmentFlag.AlignLeft`, `QMessageBox.StandardButton.Yes` kabul eder.
  Kendi alt siniflarimiz da sayilir: `NewImageDialog.Accepted` ->
  `NewImageDialog.DialogCode.Accepted` (sinif tanimlarindan Qt atasi bulunur).
  Dogru enum PyQt6'dan sorulur; tam nitelikli adin PyQt5'te de AYNI degere
  cozuldugu dogrulanir (cozulmezse bulgu, kendiliginden yazilmaz).
- `exec_()` (PyQt6'da yok; `exec()` iki baglamada da var)
- PyQt'yi dogrudan import etmek (`ui/qt` katmani disinda)
- `QFontDatabase()` ornegi (Qt6'da islevler statik)
- `ui/qt.QtX`ten import edilen ad PyQt6'nin o modulunde yok
  (ornek: `QtCriticalMsg` -> PyQt6'da `QtMsgType.QtCriticalMsg`)

    python3 -m tests.qt6_check            # denetle (beklenen: 0 bulgu)
    python3 -m tests.qt6_check --write    # kisa enum ve exec_ duzelt

Bilerek korunan Qt5 dali satir sonuna `# qt6_check: izin` yazilarak gecilir.

Enum denetimi PyQt6 ister (gelistirme araci; kullanici kurmaz). PyQt6 yoksa
o kural ATLANIR ve bu acikca yazilir; diger kurallar yine calisir.
"""
from __future__ import annotations

import ast
import enum
import importlib
import io
import os
import re
import sys
import tokenize
from typing import Dict, List, Optional, Tuple

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULLER = ("QtCore", "QtGui", "QtWidgets", "QtTest")
KATMAN = os.path.join("src", "diskultimate", "ui", "qt") + os.sep


def taranacak_dosyalar() -> List[str]:
    sonuc = [os.path.join(KOK, "main.py")]
    for kok in (os.path.join(KOK, "src", "diskultimate", "ui"),
                os.path.join(KOK, "tests"), os.path.join(KOK, "tools")):
        for dizin, _, adlar in os.walk(kok):
            sonuc += [os.path.join(dizin, a) for a in adlar if a.endswith(".py")]
    return sorted(f for f in sonuc
                  if KATMAN not in os.path.relpath(f, KOK) + os.sep
                  and not f.endswith(os.path.join("tests", "qt6_check.py")))


def _siniflar(paket: str) -> Dict[str, object]:
    out: Dict[str, object] = {}
    for m in MODULLER:
        try:
            mod = importlib.import_module(f"{paket}.{m}")
        except ImportError:
            continue
        for ad in dir(mod):
            if ad.startswith("Q") or ad == "Qt":
                out.setdefault(ad, getattr(mod, ad))
    return out


class Cozucu:
    """Kisa enum adini PyQt6'daki tam nitelikli karsiligina cevirir."""

    def __init__(self) -> None:
        self.q6 = _siniflar("PyQt6")
        try:
            self.q5 = _siniflar("PyQt5")
        except Exception:                                # noqa: BLE001
            self.q5 = {}
        self._onbellek: Dict[Tuple[str, str], Tuple[Optional[str], str]] = {}
        # Kendi siniflarimiz: ad -> (ilk ata adi, sinifin kendi tanimladigi adlar)
        self.atalar: Dict[str, Tuple[str, set]] = {}

    def siniflari_oku(self, kaynak: str) -> None:
        try:
            agac = ast.parse(kaynak)
        except SyntaxError:
            return
        for d in ast.walk(agac):
            if not isinstance(d, ast.ClassDef) or not d.bases:
                continue
            ata = d.bases[0]
            ad = ata.id if isinstance(ata, ast.Name) else getattr(ata, "attr", "")
            kendi = set()
            for g in d.body:
                if isinstance(g, (ast.FunctionDef, ast.ClassDef)):
                    kendi.add(g.name)
                elif isinstance(g, (ast.Assign, ast.AnnAssign)):
                    hedefler = g.targets if isinstance(g, ast.Assign) else [g.target]
                    kendi |= {h.id for h in hedefler if isinstance(h, ast.Name)}
            self.atalar.setdefault(d.name, (ad, kendi))

    def _qt_atasi(self, sinif: str, ad: str) -> Optional[str]:
        """Kendi sinifimizin Qt atasi; zincirde `ad` tanimlanmissa None."""
        for _ in range(10):
            if sinif in self.q6:
                return sinif
            if sinif not in self.atalar:
                return None
            sinif, kendi = self.atalar[sinif]
            if ad in kendi:
                return None
        return None

    def coz(self, sinif: str, ad: str) -> Tuple[Optional[str], str]:
        """(enum sinifi adi | None, hata). Ikisi de bossa sorun yok."""
        anahtar = (sinif, ad)
        if anahtar not in self._onbellek:
            self._onbellek[anahtar] = self._coz(sinif, ad)
        return self._onbellek[anahtar]

    def _coz(self, sinif: str, ad: str) -> Tuple[Optional[str], str]:
        if sinif not in self.q6 and sinif in self.atalar:
            ata = self._qt_atasi(sinif, ad)
            if ata is None:
                return None, ""
            e, hata = self._coz(ata, ad)
            return e, hata.replace(f"{ata}.", f"{sinif}.", 1)
        c6 = self.q6.get(sinif)
        if c6 is None or hasattr(c6, ad):
            return None, ""
        adaylar = []
        for e in dir(c6):
            deger = getattr(c6, e, None)
            if isinstance(deger, enum.EnumMeta) and ad in deger.__members__:
                adaylar.append(e)
        if not adaylar:
            return None, f"{sinif}.{ad} PyQt6'da yok (kaldirilmis API)"
        if len(adaylar) > 1:
            return None, f"{sinif}.{ad} birden cok enum'da: {adaylar}"
        e = adaylar[0]
        c5 = self.q5.get(sinif)
        if c5 is not None:
            try:
                tam = getattr(getattr(c5, e), ad)
                kisa = getattr(c5, ad)
            except AttributeError:
                return None, f"{sinif}.{e}.{ad} PyQt5'te cozulmuyor"
            # `!=` degil: sip, QKeySequence.StandardKey'i QKeySequence'e
            # cevirip karsilastirir ve esit degerlere "farkli" der.
            if int(tam) != int(kisa):
                return None, f"{sinif}.{e}.{ad} PyQt5'te farkli deger"
        return e, ""


def _modul_adlari() -> Dict[str, set]:
    """PyQt6'da her `ui/qt` alt modulunun sundugu adlar."""
    out = {}
    for m in MODULLER:
        try:
            out[m] = set(dir(importlib.import_module(f"PyQt6.{m}")))
        except ImportError:
            out[m] = set()
    # ui/qt/QtWidgets.py, Qt6'nin QtGui'ye tasidigi siniflari da sunar.
    out["QtWidgets"] |= {"QAction", "QActionGroup", "QFileSystemModel", "QShortcut"}
    return out


def import_tara(kaynak: str, adlar: Dict[str, set]):
    """[(satir, mesaj)] — `qt.QtX`ten import edilip PyQt6'da olmayan adlar."""
    try:
        agac = ast.parse(kaynak)
    except SyntaxError:
        return []
    out = []
    for dugum in ast.walk(agac):
        if not isinstance(dugum, ast.ImportFrom) or not dugum.module:
            continue
        parca = dugum.module.split(".")
        if len(parca) < 2 or parca[-2] != "qt" or parca[-1] not in adlar:
            continue
        for ad in dugum.names:
            if ad.name != "*" and ad.name not in adlar[parca[-1]]:
                out.append((dugum.lineno,
                            f"{ad.name} PyQt6.{parca[-1]} icinde yok"))
    return out


def enum_tara(yol: str, kaynak: str, cozucu: Cozucu):
    """[(satir, sutun, eski, yeni|None, mesaj)]"""
    bulgular = []
    try:
        jetonlar = list(tokenize.generate_tokens(io.StringIO(kaynak).readline))
    except (tokenize.TokenError, IndentationError):
        return bulgular
    for i in range(len(jetonlar) - 2):
        a, nokta, b = jetonlar[i], jetonlar[i + 1], jetonlar[i + 2]
        # Kucuk harfli uyeler de var (`Qt.transparent`, Qt.GlobalColor); ad
        # PyQt6 sinifinda varsa (metot dahil) `coz` dokunmaz.
        if (a.type != tokenize.NAME or nokta.string != "." or b.type != tokenize.NAME
                or b.string.startswith("__")):
            continue
        if i and jetonlar[i - 1].string == ".":        # x.Qt.Y: nitelikli zincir
            continue
        e, hata = cozucu.coz(a.string, b.string)
        if e:
            bulgular.append((b.start, f"{a.string}.{b.string}",
                             f"{a.string}.{e}.{b.string}", ""))
        elif hata:
            bulgular.append((b.start, f"{a.string}.{b.string}", None, hata))
    return bulgular


# Bilerek korunan Qt5 dali (ornek: `QFontDatabase() if not QT6`) bu yorumla
# isaretlenir.
ISTISNA = "qt6_check: izin"

DESENLER = [
    (re.compile(r"\.exec_\("), ".exec(", "exec_() PyQt6'da yok"),
    # Ezilen exec_ artik cagrilmaz (cagri yerleri exec()): TaskDialog isciyi
    # orada baslatir, ezme kaybolursa pencere sonsuza dek bekler.
    (re.compile(r"\bdef exec_\("), "def exec(", "exec_ ezilmis; exec olmali"),
    (re.compile(r"\.exec_\b"), ".exec", "exec_ niteligi PyQt6'da yok"),
    (re.compile(r"\bQFontDatabase\(\)"), None, "QFontDatabase() Qt6'da yok (statik islevler)"),
    (re.compile(r"^\s*(from|import)\s+PyQt[56]\b", re.M), None,
     "PyQt dogrudan import edilmis (ui/qt katmani kullanilmali)"),
]


def main() -> int:
    yaz = "--write" in sys.argv[1:]
    try:
        cozucu: Optional[Cozucu] = Cozucu()
        if not cozucu.q6:
            cozucu = None
    except Exception:                                    # noqa: BLE001
        cozucu = None
    if cozucu is None:
        print("PyQt6 kurulu degil: kisa enum kurali ATLANDI "
              "(pip install PyQt6 — yalnizca gelistirme icin)")

    modul_adlari = _modul_adlari() if cozucu is not None else {}
    if cozucu is not None:
        for yol in taranacak_dosyalar():
            with open(yol, encoding="utf-8") as fh:
                cozucu.siniflari_oku(fh.read())
    toplam = duzeltilen = 0
    for yol in taranacak_dosyalar():
        goreli = os.path.relpath(yol, KOK)
        with open(yol, encoding="utf-8") as fh:
            kaynak = fh.read()
        satirlar = kaynak.splitlines(keepends=True)
        degisim: List[Tuple[int, int, str, str]] = []
        if cozucu is not None:
            for (satir, sutun), eski, yeni, hata in enum_tara(yol, kaynak, cozucu):
                if yeni and yaz:
                    bas = sutun - len(eski.split(".")[0]) - 1
                    degisim.append((satir, bas, eski, yeni))
                else:
                    toplam += 1
                    print(f"{goreli}:{satir}: {eski} -> "
                          f"{yeni or '?'}  {hata or 'kisa enum'}")
        for satir, mesaj in import_tara(kaynak, modul_adlari) if modul_adlari else []:
            toplam += 1
            print(f"{goreli}:{satir}: {mesaj}")
        # Once enum'lar: desen duzeltmesi (exec_ -> exec) ayni satirdaki
        # sutunlari kaydirir.
        for satir, bas, eski, yeni in sorted(degisim, reverse=True):
            s = satirlar[satir - 1]
            assert s[bas:bas + len(eski)] == eski, (goreli, satir, eski)
            satirlar[satir - 1] = s[:bas] + yeni + s[bas + len(eski):]
            duzeltilen += 1
        for desen, yerine, mesaj in DESENLER:
            for no, satir in enumerate(satirlar, 1):
                if (not desen.search(satir) or satir.lstrip().startswith("#")
                        or ISTISNA in satir):
                    continue
                if yerine and yaz:
                    satirlar[no - 1] = desen.sub(yerine, satir)
                    duzeltilen += 1
                else:
                    toplam += 1
                    print(f"{goreli}:{no}: {mesaj}")
        yeni_kaynak = "".join(satirlar)
        if yaz and yeni_kaynak != kaynak:
            with open(yol, "w", encoding="utf-8", newline="") as fh:
                fh.write(yeni_kaynak)
    if yaz:
        print(f"Duzeltilen: {duzeltilen}")
    print(f"\nToplam bulgu: {toplam}")
    return 1 if toplam else 0


if __name__ == "__main__":
    sys.exit(main())
