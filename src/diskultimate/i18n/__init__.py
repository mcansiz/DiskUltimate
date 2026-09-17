"""Cok dilli arayuz metinleri.

## Nasil calisir

Kaynak dil **Turkce**dir: kodun icindeki metin hem gosterilecek yazidir hem de
ceviri anahtaridir. Boylece ceviri dosyasi eksik ya da bozuk olsa bile uygulama
anlamli metinle calisir — hicbir yerde "MAIN_WINDOW_TITLE" gibi bir anahtar
gorunmez.

    from ..i18n import tr
    tr("Bolum tablosunu sil")                 # -> "Delete partition table"
    tr("{} bolum bulundu", len(parts))        # -> "3 partitions found"

Ceviriler `i18n/catalogs/<kod>.json` icinde `{"kaynak metin": "ceviri"}`
seklinde durur. Yeni bir dil eklemek icin:

1. `catalogs/<kod>.json` dosyasini olusturun (`python3 -m tests.i18n_check
   --write <kod>` bos iskeleti uretir),
2. `LANGUAGE_NAMES` icine dilin kendi adini yazin.

Koda dokunmak gerekmez; dosya bulunursa dil menusunde cikar.

## Neden Qt Linguist (.ts/.qm) degil

`QTranslator` yalnizca `QObject.tr()` cagrilarini cevirir; bu projede metnin
onemli bir bolumu `core/` icinde uretilir ve `core/` PyQt import etmez
(CLAUDE.md katman kurali). Ayrica `.qm` uretmek Qt'nin `lrelease` aracini
gerektirir — "harici bagimlilik yok" kuralina aykiridir. Gerekce:
`.claude/decisions/0027-cok-dilli-arayuz.md`.

## Is parcaciklari

`set_language()` yalnizca arayuz is parcacigindan cagrilir. Okuma (`tr`) her
yerden guvenlidir: sozluk yer degistirmeyle guncellenir, tek tek yazilmaz.
"""
from __future__ import annotations

import json
import os
import re
from typing import Callable, Dict, List, Optional, Tuple

# Kaynak dil: kodun icindeki metinlerin dili. Ceviri dosyasi yoktur.
SOURCE_LANGUAGE = "tr"

# Dillerin **kendi dillerindeki** adlari. Menude boyle gorunurler.
LANGUAGE_NAMES: Dict[str, str] = {
    "tr": "Türkçe",
    "en": "English",
    "de": "Deutsch",
}

# Dil secimi ayar dosyasinda bu anahtarla durur.
SETTING_KEY = "language"

# Ortam degiskeniyle gecersiz kilma: DISKULTIMATE_LANG=en python3 main.py
ENV_NAME = "DISKULTIMATE_LANG"

# Sozde-yerellestirme (pseudolocalization) — bir **kalite araci**, gercek dil
# degil. Sozluk dosyasi yoktur; metin calisma aninda donusturulur. Dil menusunde
# gorunmez, yalnizca ortam degiskeniyle acilir:
#     DISKULTIMATE_LANG=qps python3 main.py
# Ne yakalar (ADR 0027, bolum "kalite kapisi"):
#   1. `tr()` ile SARILMAMIS metin — donusmeden Turkce kalir, gozle hemen belli
#      olur; statik denetimin goremedigi tek bosluk turu budur.
#   2. Yerlesim tasmasi — metin ~%30 uzatilir (Almanca'nin en kotu hali).
#   3. Parca parca birlestirilen metin — her parca ayri ayri koseli ayrac alir.
PSEUDO_LANGUAGE = "qps"

# ASCII harflerin okunabilir ama gozle ayirt edilir karsiliklari.
_PSEUDO_MAP = str.maketrans(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "ȧƀƈḓḗƒɠħīĵķŀḿƞǿƥɋřşŧŭṽẇẋẏẑȦƁƇḒḖƑƓĦĪĴĶĿḾȠǾƤɊŘŞŦŬṼẆẊẎẐ")

# Dokunulmayacak bolumler: yer tutucular ({}, {ad}, {:08X}, {{, }}),
# HTML etiketleri ve HTML varliklari (&nbsp;). Bunlar bozulursa metin
# calisma aninda patlardi — sozde dil bile olsa.
_PROTECTED = re.compile(r"(\{\{|\}\}|\{[^{}]*\}|<[^>]+>|&[a-zA-Z]+;)")

CATALOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalogs")

_language = SOURCE_LANGUAGE
_catalog: Dict[str, str] = {}
_listeners: List[Callable[[str], None]] = []
_ready = False


# --------------------------------------------------------------------------
# Ceviri
# --------------------------------------------------------------------------
def tr(text: str, *args, **kwargs) -> str:
    """Metni etkin dile cevirir; gerekirse bicimlendirir.

    `args`/`kwargs` verilirse sonuc `str.format` ile doldurulur:

        tr("Bolum {}", index)
        tr("{ad} silindi", ad=name)

    Ceviri bozuksa (ornegin yer tutucu sayisi tutmuyorsa) **kaynak metin**
    kullanilir: eksik bir ceviri uygulamayi durdurmamalidir.
    """
    if _language == PSEUDO_LANGUAGE:
        translated = pseudo(text)
    else:
        translated = _catalog.get(text, text)
    if not (args or kwargs):
        return translated
    try:
        return translated.format(*args, **kwargs)
    except (IndexError, KeyError, ValueError):
        try:
            return text.format(*args, **kwargs)
        except (IndexError, KeyError, ValueError):
            return text


def pseudo(text: str) -> str:
    """Metni sozde-yerellestirilmis haline cevirir.

        "Bolumu sil"  ->  "[!Ɓǿŀŭḿŭ şīŀ···!]"

    Koseli ayraclar kirpilmayi, noktalar uzama payini gosterir. Yer tutucular
    ve HTML etiketleri **oldugu gibi** birakilir.
    """
    parcalar = _PROTECTED.split(text)
    govde = "".join(p if i % 2 else p.translate(_PSEUDO_MAP)
                    for i, p in enumerate(parcalar))
    pay = "·" * max(1, int(len(text) * 0.3))
    return f"[!{govde}{pay}!]"


def mark(text: str) -> str:
    """Metni **cevirmeden** ceviri sozluguna girmesi icin isaretler.

    Modul duzeyinde uretilen veriler (silme yontemleri, bolum turu adlari,
    islem basliklari) uygulama acilirken, dil secilmeden once olusur. Bunlari
    orada cevirmek ise yaramaz: metin bir kez uretilir ve dil degisince
    guncellenmez. Cozum gettext'in `N_()` yaklasimidir — kaynakta **isaretle**,
    gosterirken **cevir**:

        WipeMethod("zero", mark("Sifirla (1 gecis)"), ...)   # tanim
        combo.addItem(tr(method.label), method.key)          # gosterim

    Islev metni oldugu gibi dondurur; tek isi ceviri toplayicisina gorunmektir.
    """
    return text


def translate(text: str) -> str:
    """Bicimlendirme yapmadan ceviri (hazir uretilmis metinler icin)."""
    return _catalog.get(text, text)


# --------------------------------------------------------------------------
# Dil secimi
# --------------------------------------------------------------------------
def current_language() -> str:
    """Etkin dilin kodu."""
    return _language


def language_name(code: str) -> str:
    """Dilin kendi dilindeki adi (bilinmiyorsa kodun kendisi)."""
    if code == PSEUDO_LANGUAGE:
        return "Pseudo (QA)"
    return LANGUAGE_NAMES.get(code, code)


def catalog_languages() -> List[str]:
    """`catalogs/` icinde ceviri dosyasi bulunan diller."""
    try:
        names = os.listdir(CATALOG_DIR)
    except OSError:
        return []
    return sorted(n[:-5] for n in names if n.endswith(".json"))


def available_languages() -> List[Tuple[str, str]]:
    """Secilebilir diller: [(kod, ad)] — kaynak dil her zaman ilk siradadir."""
    codes = [SOURCE_LANGUAGE] + [c for c in catalog_languages()
                                 if c != SOURCE_LANGUAGE]
    # Sozde dil bir kalite aracidir; menude yalnizca **zaten etkinse** gorunur
    # (QA kosumunda hangi kipte oldugu belli olsun diye).
    if _language == PSEUDO_LANGUAGE:
        codes.append(PSEUDO_LANGUAGE)
    return [(code, language_name(code)) for code in codes]


def load_catalog(code: str) -> Dict[str, str]:
    """Bir dilin ceviri sozlugunu okur. Dosya yoksa/bozuksa bos sozluk doner."""
    if code in (SOURCE_LANGUAGE, PSEUDO_LANGUAGE):
        return {}          # sozde dil sozluk kullanmaz, uretir
    try:
        with open(os.path.join(CATALOG_DIR, f"{code}.json"), "r",
                  encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    # Yalnizca dize->dize girisler; bozuk satirlar sessizce atlanir.
    return {k: v for k, v in data.items()
            if isinstance(k, str) and isinstance(v, str) and v}


def set_language(code: str, remember: bool = True) -> str:
    """Etkin dili degistirir; gecerli olarak ayarlanan kodu dondurur.

    `remember=True` secimi ayar dosyasina yazar (bir sonraki acilista gecerli
    olur). Dil gercekten degistiyse dinleyiciler bilgilendirilir.
    """
    global _language, _catalog, _ready
    code = (code or "").strip().lower() or SOURCE_LANGUAGE
    if code not in (SOURCE_LANGUAGE, PSEUDO_LANGUAGE) \
            and code not in catalog_languages():
        code = SOURCE_LANGUAGE
    changed = code != _language or not _ready
    _catalog = load_catalog(code)
    _language = code
    _ready = True
    if remember:
        from ..core import settings

        settings.set_value(SETTING_KEY, code)
    if changed:
        for listener in list(_listeners):
            try:
                listener(code)
            except Exception:       # bir dinleyicinin hatasi digerlerini kesmez
                pass
    return code


def preferred_language() -> str:
    """Acilista kullanilacak dili belirler.

    Sira: ortam degiskeni > kayitli secim > isletim sisteminin dili > Turkce.
    Isletim sisteminin dili icin ceviri yoksa kaynak dile dusulur — yarim
    cevrilmis bir arayuz gostermektense Turkce gostermek daha durustur.
    """
    requested = os.environ.get(ENV_NAME, "").strip().lower()
    if requested:
        return requested
    try:
        from ..core import settings

        saved = settings.get(SETTING_KEY, "")
    except Exception:
        saved = ""
    if saved:
        return str(saved)
    from ..core.platform import system_language

    detected = system_language()
    if detected and (detected == SOURCE_LANGUAGE
                     or detected in catalog_languages()):
        return detected
    return SOURCE_LANGUAGE


def initialize() -> str:
    """Dili acilista bir kez belirler ve yukler. Secilen kodu dondurur.

    Secim **kaydedilmez**: kullanici menuden secmedikce ayar dosyasina yazmak,
    isletim sisteminin dili degistiginde uygulamanin eski dilde kalmasina yol
    acardi.
    """
    return set_language(preferred_language(), remember=False)


# --------------------------------------------------------------------------
# Degisiklik bildirimi
# --------------------------------------------------------------------------
def add_listener(callback: Callable[[str], None]) -> None:
    """Dil degistiginde cagrilacak islev ekler (arayuzun metinlerini tazeler)."""
    if callback not in _listeners:
        _listeners.append(callback)


def remove_listener(callback: Callable[[str], None]) -> None:
    if callback in _listeners:
        _listeners.remove(callback)


def catalog_size() -> int:
    """Etkin sozlukteki ceviri sayisi (tani amacli)."""
    return len(_catalog)
