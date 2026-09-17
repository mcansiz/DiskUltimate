"""Cok dilli arayuz metinleri.

## Nasil calisir

Kaynak dil **Turkce**dir: kodun icindeki metin hem gosterilecek yazidir hem de
ceviri anahtaridir. Boylece ceviri dosyasi eksik ya da bozuk olsa bile uygulama
anlamli metinle calisir — hicbir yerde "MAIN_WINDOW_TITLE" gibi bir anahtar
gorunmez.

    from ..i18n import tr
    tr("Bolum tablosunu sil")                 # -> "Delete partition table"
    tr("{} bolum bulundu", len(parts))        # -> "3 partitions found"

Ceviriler `i18n/catalogs/<kod>.po` icinde, gettext'in standart `.po`
biciminde durur. Yeni bir dil eklemek icin:

1. `python3 -m tests.i18n_check --write <kod>` dosyayi uretir/tazeler,
2. `LANGUAGE_NAMES` icine dilin kendi adini yazin.

Koda dokunmak gerekmez; dosya bulunursa dil menusunde cikar.

## Neden `.po` (ama gettext calisma zamani degil)

Bicim gettext'in `.po` dosyasidir: cogul ekleri (`Plural-Forms`), baglam
(`msgctxt`) ve bayat ceviriyi koruyan `fuzzy` isareti oradan gelir; Poedit,
Weblate ve Crowdin dosyayi dogrudan okur. Ama `gettext` **modulu**
kullanilmaz: o yalnizca derlenmis `.mo` okur ve derlemek icin `msgfmt`
gerekir. `.po` burada dogrudan okunuyor (`i18n/po.py`), derleme adimi yok.

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

import gettext
import os
import re
from typing import Callable, Dict, List, Optional, Tuple

from . import po

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
_catalog: Dict[str, str] = {}                 # baglamsiz metinler (tr icin hizli yol)
_contexts: Dict[Tuple[str, str], str] = {}    # (baglam, metin) -> ceviri
_plurals: Dict[str, List[str]] = {}           # tekil metin -> [bicim0, bicim1, ...]
_plural_rule: Callable[[int], int] = lambda n: 0 if n == 1 else 1
_listeners: List[Callable[[str], None]] = []
_ready = False

# Kaynak dilin (Turkce) cogul kurali — CLDR: "one" (n == 1) ve "other".
SOURCE_PLURAL_FORMS = "nplurals=2; plural=(n != 1);"


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


def trn(singular: str, plural: str, count: int, *args, **kwargs) -> str:
    """Sayiya gore tekil/cogul bicimi secer ve cevirir.

        trn("{} adim uygulandi", "{} adim uygulandi", n, n)
        # en -> "1 step applied" / "3 steps applied"

    Turkce'de iki bicim cogu zaman aynidir; yine de ikisi de yazilir, cunku
    **ceviri** dillerinde ayrisirlar. Dilin kac bicimi oldugu ve hangisinin
    secilecegi `.po` basligindaki `Plural-Forms` kuralindan gelir.
    """
    if _language == PSEUDO_LANGUAGE:
        secilen = pseudo(singular if count == 1 else plural)
    else:
        bicimler = _plurals.get(singular)
        if bicimler:
            try:
                secilen = bicimler[_plural_rule(count)] or singular
            except (IndexError, TypeError):
                secilen = bicimler[0] or singular
        else:
            secilen = singular if count == 1 else plural
    if not (args or kwargs):
        return secilen
    try:
        return secilen.format(*args, **kwargs)
    except (IndexError, KeyError, ValueError):
        return (singular if count == 1 else plural)


def trc(context: str, text: str, *args, **kwargs) -> str:
    """Baglamli ceviri: ayni kaynak metin iki yerde farkli cevrilebilir.

        trc("bolum turu", "Tur")   ve   trc("dosya turu", "Tur")

    Baglam yalnizca sozluk anahtarinin parcasidir; kullaniciya gosterilmez.
    """
    if _language == PSEUDO_LANGUAGE:
        translated = pseudo(text)
    else:
        translated = _contexts.get((context, text), text)
    if not (args or kwargs):
        return translated
    try:
        return translated.format(*args, **kwargs)
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
    return sorted(n[:-3] for n in names if n.endswith(".po"))


def available_languages() -> List[Tuple[str, str]]:
    """Secilebilir diller: [(kod, ad)] — kaynak dil her zaman ilk siradadir."""
    codes = [SOURCE_LANGUAGE] + [c for c in catalog_languages()
                                 if c != SOURCE_LANGUAGE]
    # Sozde dil bir kalite aracidir; menude yalnizca **zaten etkinse** gorunur
    # (QA kosumunda hangi kipte oldugu belli olsun diye).
    if _language == PSEUDO_LANGUAGE:
        codes.append(PSEUDO_LANGUAGE)
    return [(code, language_name(code)) for code in codes]


def catalog_path(code: str) -> str:
    """Bir dilin `.po` dosyasinin yolu."""
    return os.path.join(CATALOG_DIR, f"{code}.po")


def load_po(code: str) -> po.Catalog:
    """Dilin `.po` dosyasini okur; yoksa/bozuksa bos katalog doner."""
    if code in (SOURCE_LANGUAGE, PSEUDO_LANGUAGE):
        return po.Catalog()
    try:
        with open(catalog_path(code), "r", encoding="utf-8") as fh:
            return po.parse(fh.read())
    except (OSError, ValueError):
        return po.Catalog()


def load_catalog(code: str) -> Dict[str, str]:
    """Baglamsiz cevirileri `{kaynak: ceviri}` olarak dondurur.

    `fuzzy` isaretli girisler **kullanilmaz** (gettext davranisi): ceviri
    dosyada durur ve cevirmene onerilir, ama arayuzde gosterilmez.
    """
    return {e.msgid: e.msgstr for e in load_po(code).entries
            if e.context is None and e.plural is None and e.translated}


def _compile_plural(forms: str) -> Callable[[int], int]:
    """`Plural-Forms` basligindaki ifadeyi calistirilabilir hale getirir.

    Ifadeyi Python'un kendi `gettext.c2py()` islevi derler — standart
    kutuphane; harici bagimlilik yok. Basligi okunamayan dilde Ingilizce
    kurali (n != 1) kullanilir.
    """
    match = re.search(r"plural\s*=\s*([^;]+)", forms or "")
    if match:
        try:
            return gettext.c2py(match.group(1).strip())
        except Exception:
            pass
    return lambda n: 0 if n == 1 else 1


def set_language(code: str, remember: bool = True) -> str:
    """Etkin dili degistirir; gecerli olarak ayarlanan kodu dondurur.

    `remember=True` secimi ayar dosyasina yazar (bir sonraki acilista gecerli
    olur). Dil gercekten degistiyse dinleyiciler bilgilendirilir.
    """
    global _language, _catalog, _contexts, _plurals, _plural_rule, _ready
    code = (code or "").strip().lower() or SOURCE_LANGUAGE
    if code not in (SOURCE_LANGUAGE, PSEUDO_LANGUAGE) \
            and code not in catalog_languages():
        code = SOURCE_LANGUAGE
    changed = code != _language or not _ready
    katalog = load_po(code)
    _catalog = {e.msgid: e.msgstr for e in katalog.entries
                if e.context is None and e.plural is None and e.translated}
    _contexts = {(e.context, e.msgid): e.msgstr for e in katalog.entries
                 if e.context is not None and e.translated}
    _plurals = {e.msgid: e.plurals for e in katalog.entries
                if e.plural is not None and e.translated}
    _plural_rule = _compile_plural(katalog.plural_forms or SOURCE_PLURAL_FORMS)
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
