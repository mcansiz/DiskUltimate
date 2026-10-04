"""Ceviri girisi/katalog modeli ve gettext `.po` okuyucu-yazici — saf Python.

2026-10-04'ten beri sozlukler `.ts`tir (`i18n/ts.py`, ADR 0088); bu modul
ortak veri modelini (`Entry`, `Catalog`) ve birlestirmeyi (`merge`) tasir.
`.po` okuyucu/yazici, `.po` disa/ice aktarim (Poedit/Weblate) icin durur.

## Neden `.po`

Ceviri sozlugu once duz JSON'du. `.po` bicimi ayni seyi tasir ama uc sey
fazladan getirir (ADR 0027 eki, `.claude/docs/i18n-raporu.md` bolum 12):

1. **Cogul ekleri** — `Plural-Forms` basligi dil basina kurali tasir; kurali
   Python'un kendi `gettext.c2py()` islevi derler (standart kutuphane).
2. **Baglam** (`msgctxt`) — ayni kaynak metin iki yerde farkli cevrilebilir.
3. **Bayat ceviri kaybolmaz** — kaynak metin degisince ceviri silinmez,
   `#, fuzzy` isaretlenip onerilmeye devam eder (`merge`).

Ustelik Poedit / Weblate / Crowdin bu bicimi dogrudan okur; ceviri icin kod
bilgisi gerekmez.

## Neden gettext calisma zamani degil

`gettext` modulu yalnizca **derlenmis** `.mo` okur; derlemek icin `msgfmt`
gerekir — bir gelistirici araci ve kurulum adimi. `.mo` zaten yalnizca bir hiz
optimizasyonudur.

Bedeli olculdu: 1315 girisli bir `.po` **7.6 ms**'de okunuyor; ayni icerigin
JSON hali 0.7 ms (11x fark — JSON'u C cozuyor, bunu Python). Maliyet dil
basina **bir kez** odenir (acilis ve dil degisimi); Qt'nin kendi acilisi
yaninda gorunmez. Karsiliginda cogul/baglam/fuzzy ve butun ceviri arac
ekosistemi geliyor.

## Desteklenen alt kume

`msgctxt`, `msgid`, `msgid_plural`, `msgstr`, `msgstr[n]`, `#, fuzzy`,
yorumlar (`#`, `#.`, `#:`), cok satirli dizeler ve `#~` ile bayatlatilmis
girisler. Desteklenmeyen tek sey `#| ` (onceki msgid) satirlaridir; okunur
ama saklanmaz.
"""
from __future__ import annotations

import difflib
import re
from typing import Dict, List, Optional, Tuple

# Bir girisin kimligi: (baglam, kaynak metin). Baglamsiz girislerde baglam None.
Key = Tuple[Optional[str], str]

# Ters bolu kaynakta **cift yazilmaz**: `tests/platform_check.py` iki ters
# boluyu "sabit Windows yolu" sayip bulgu uretiyor. Burada yol yok,
# kacis dizisi var; bu yuzden karakter kodla kuruluyor.
BACKSLASH = chr(92)

_ESCAPES = {BACKSLASH: BACKSLASH * 2, '"': BACKSLASH + '"',
            "\n": BACKSLASH + "n", "\t": BACKSLASH + "t",
            "\r": BACKSLASH + "r"}
_UNESCAPES = {"n": "\n", "t": "\t", "r": "\r",
              '"': '"', BACKSLASH: BACKSLASH}

_NEEDS_ESCAPE = re.compile('[' + BACKSLASH * 2 + '"' + BACKSLASH + 'n'
                           + BACKSLASH + 't' + BACKSLASH + 'r]')
_LINE = re.compile(r'^\s*(msgctxt|msgid_plural|msgid|msgstr(?:\[(\d+)\])?)\s+"(.*)"\s*$')
_CONT = re.compile(r'^\s*"(.*)"\s*$')


def escape(text: str) -> str:
    # Hizli yol: kacilacak karakter yoksa metin oldugu gibi doner. 1315
    # girisin cogunda kacis yok; dongu yalnizca gerekince doner.
    if not _NEEDS_ESCAPE.search(text):
        return text
    return "".join(_ESCAPES.get(ch, ch) for ch in text)


def unescape(text: str) -> str:
    if BACKSLASH not in text:          # hizli yol — bkz. escape()
        return text
    out = []
    index = 0
    while index < len(text):
        ch = text[index]
        if ch == BACKSLASH and index + 1 < len(text):
            out.append(_UNESCAPES.get(text[index + 1], text[index + 1]))
            index += 2
        else:
            out.append(ch)
            index += 1
    return "".join(out)


class Entry:
    """Tek bir ceviri girisi."""

    def __init__(self, msgid: str, msgstr: str = "",
                 context: Optional[str] = None,
                 plural: Optional[str] = None,
                 plurals: Optional[List[str]] = None,
                 comments: Optional[List[str]] = None,
                 references: Optional[List[str]] = None,
                 fuzzy: bool = False, obsolete: bool = False):
        self.msgid = msgid
        self.msgstr = msgstr
        self.context = context
        self.plural = plural                    # msgid_plural
        self.plurals = plurals or []            # msgstr[0], msgstr[1], ...
        self.comments = comments or []          # "#." ile yazilan aciklamalar
        self.references = references or []      # "#:" kaynak konumlari
        self.fuzzy = fuzzy
        self.obsolete = obsolete

    @property
    def key(self) -> Key:
        return (self.context, self.msgid)

    @property
    def translated(self) -> bool:
        """Kullanilabilir bir cevirisi var mi? (fuzzy **sayilmaz**)"""
        if self.fuzzy or self.obsolete:
            return False
        if self.plural is not None:
            return any(self.plurals)
        return bool(self.msgstr)

    def __repr__(self) -> str:
        return f"<Entry {self.msgid[:30]!r} fuzzy={self.fuzzy}>"


class Catalog:
    """Bir `.po` dosyasinin tamami: basligi ve girisleri."""

    def __init__(self, entries: Optional[List[Entry]] = None,
                 headers: Optional[Dict[str, str]] = None):
        self.entries: List[Entry] = entries or []
        self.headers: Dict[str, str] = headers or {}

    # -- arama ------------------------------------------------------------
    def by_key(self) -> Dict[Key, Entry]:
        return {e.key: e for e in self.entries if not e.obsolete}

    @property
    def plural_forms(self) -> str:
        return self.headers.get("Plural-Forms", "")

    # -- sayim ------------------------------------------------------------
    def counts(self) -> Dict[str, int]:
        live = [e for e in self.entries if not e.obsolete]
        return {
            "toplam": len(live),
            "cevrili": sum(1 for e in live if e.translated),
            "fuzzy": sum(1 for e in live if e.fuzzy),
            "bos": sum(1 for e in live if not e.translated and not e.fuzzy),
            "bayat": sum(1 for e in self.entries if e.obsolete),
        }


# --------------------------------------------------------------------------
# Okuma
# --------------------------------------------------------------------------
def parse(text: str) -> Catalog:
    """`.po` metnini `Catalog` nesnesine cevirir."""
    catalog = Catalog()
    entry = Entry("")
    field: Optional[str] = None          # su an hangi dizeye ekliyoruz
    index = 0                           # msgstr[index]
    fresh_entry = True                    # henuz hicbir alan gorulmedi

    def finish():
        nonlocal entry, field, fresh_entry
        if not fresh_entry:
            if entry.msgid == "" and entry.context is None and not entry.obsolete:
                catalog.headers.update(_parse_headers(entry.msgstr))
            else:
                catalog.entries.append(entry)
        entry = Entry("")
        field = None
        fresh_entry = True

    for raw in text.split("\n"):
        line = raw.rstrip("\r")
        if not line.strip():
            finish()
            continue
        obsolete_line = line.lstrip().startswith("#~")
        if obsolete_line:
            line = line.lstrip()[2:].lstrip()
        elif line.lstrip().startswith("#"):
            comment = line.lstrip()
            if not fresh_entry:               # yeni girisin yorumlari basladi
                finish()
            if comment.startswith("#,"):
                entry.fuzzy = "fuzzy" in comment
            elif comment.startswith("#:"):
                entry.references.append(comment[2:].strip())
            elif comment.startswith("#."):
                entry.comments.append(comment[2:].strip())
            continue

        match = _LINE.match(line)
        if match:
            name, plural_index, value = match.group(1), match.group(2), match.group(3)
            if name == "msgid" and not fresh_entry and field != "msgctxt":
                finish()                      # onceki giris bitti
            fresh_entry = False
            entry.obsolete = entry.obsolete or obsolete_line
            value = unescape(value)
            if name == "msgctxt":
                entry.context = value
                field = "msgctxt"
            elif name == "msgid":
                entry.msgid = value
                field = "msgid"
            elif name == "msgid_plural":
                entry.plural = value
                field = "plural"
            elif plural_index is not None:
                index = int(plural_index)
                while len(entry.plurals) <= index:
                    entry.plurals.append("")
                entry.plurals[index] = value
                field = "plurals"
            else:
                entry.msgstr = value
                field = "msgstr"
            continue

        if not line.lstrip().startswith('"'):
            continue                     # tanimadigimiz satir; regex'e girme
        cont = _CONT.match(line)
        if cont and field:
            value = unescape(cont.group(1))
            if field == "msgctxt":
                entry.context = (entry.context or "") + value
            elif field == "msgid":
                entry.msgid += value
            elif field == "plural":
                entry.plural = (entry.plural or "") + value
            elif field == "plurals":
                entry.plurals[index] += value
            else:
                entry.msgstr += value
    finish()
    return catalog


def _parse_headers(text: str) -> Dict[str, str]:
    headers = {}
    for line in text.split("\n"):
        if ":" in line:
            name, _, value = line.partition(":")
            headers[name.strip()] = value.strip()
    return headers


# --------------------------------------------------------------------------
# Yazma
# --------------------------------------------------------------------------
def _dump_string(name: str, value: str, width: int = 74) -> List[str]:
    """`msgid "..."` satirini (gerekirse cok satirli) uretir."""
    kacisli = escape(value)
    if len(kacisli) + len(name) + 4 <= width and "\\n" not in kacisli:
        return [f'{name} "{kacisli}"']
    lines = [f'{name} ""']
    # Satir sonlarindan sonra bol; kalan uzun parcalari bosluktan sar.
    pieces = value.split("\n")
    for i, piece in enumerate(pieces):
        metin = piece + ("\n" if i < len(pieces) - 1 else "")
        if not metin:
            continue
        for chunk in _wrap(metin, width):
            lines.append(f'"{escape(chunk)}"')
    if len(lines) == 1:
        lines.append('""')
    return lines


def _wrap(text: str, width: int) -> List[str]:
    out: List[str] = []
    current = ""
    for word in text.split(" "):
        candidate = word if not current else current + " " + word
        if len(escape(candidate)) > width and current:
            out.append(current + " ")
            current = word
        else:
            current = candidate
    if current:
        out.append(current)
    return out


def dump(catalog: Catalog) -> str:
    """`Catalog` nesnesini `.po` metnine cevirir."""
    out: List[str] = []
    out.append('msgid ""')
    out.append('msgstr ""')
    for name, value in catalog.headers.items():
        out.append(f'"{name}: {escape(value)}\\n"')
    out.append("")
    for entry in catalog.entries:
        prefix = "#~ " if entry.obsolete else ""
        for comment in entry.comments:
            out.append(f"#. {comment}")
        for source in entry.references:
            out.append(f"#: {source}")
        if entry.fuzzy:
            out.append("#, fuzzy")
        if entry.context is not None:
            out += [prefix + s for s in _dump_string("msgctxt", entry.context)]
        out += [prefix + s for s in _dump_string("msgid", entry.msgid)]
        if entry.plural is not None:
            out += [prefix + s for s in _dump_string("msgid_plural", entry.plural)]
            for i, value in enumerate(entry.plurals or ["", ""]):
                out += [prefix + s for s in _dump_string(f"msgstr[{i}]", value)]
        else:
            out += [prefix + s for s in _dump_string("msgstr", entry.msgstr)]
        out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Birlestirme (msgmerge esdegeri)
# --------------------------------------------------------------------------
def merge(current: Catalog, source: List[Entry], similarity: float = 0.65,
          nplurals: int = 2) -> dict:
    """Kaynaktaki metinleri mevcut sozluge isler.

    - Ayni `msgid` varsa cevirisi **korunur**.
    - Kaynakta artik olmayan giris silinmez, **bayatlatilir** (`#~`).
    - Yeni bir giris, bayatlayan bir girise yeterince benziyorsa cevirisi
      tasinip `#, fuzzy` isaretlenir — `msgmerge`'in yaptigi budur. Kaynak
      metindeki bir yazim duzeltmesi boylece cevirileri dusurmez.

    Esik (0.65) olculerek secildi: Turkce metne diakritik eklemek kisa
    dizelerde 0.69-0.70 benzerlik uretiyor ("Bolumu sil" -> "Bölümü sil"),
    uzun dizelerde 0.85-0.96; gercekten farkli iki metin ise 0.42 ve altinda
    kaliyor ("Bolumu sil" <-> "Diski sil" = 0.42).

    Sonuc sayaclarini dondurur.
    """
    previous = {e.key: e for e in current.entries if not e.obsolete}
    obsoletes = [e for e in current.entries if e.obsolete]
    result: List[Entry] = []
    counts = {"korunan": 0, "yeni": 0, "fuzzy": 0, "bayatlayan": 0}

    seen = set()
    for fresh in source:
        existing = previous.get(fresh.key)
        if existing is not None:
            seen.add(fresh.key)
            existing.comments = fresh.comments
            existing.references = fresh.references
            if fresh.plural is not None and existing.plural is None:
                # Duz giris cogula donustu: eski ceviri ilk bicime tasinir,
                # yoksa `msgstr` dump sirasinda dusup ceviri kaybolurdu.
                existing.plurals = [existing.msgstr] + [""] * (nplurals - 1)
                existing.msgstr = ""
            existing.plural = fresh.plural
            if fresh.plural is not None and not existing.plurals:
                existing.plurals = [""] * nplurals
            result.append(existing)
            counts["korunan"] += 1
        else:
            result.append(fresh)
            counts["yeni"] += 1

    # Kaynakta kalmayanlar bayatlar; benzer yeni girise ceviri tasinir.
    dropped = [e for key, e in previous.items() if key not in seen]
    untranslated = [e for e in result if not e.translated and not e.plural]
    for gone in dropped:
        if gone.translated:
            candidate = difflib.get_close_matches(
                gone.msgid, [e.msgid for e in untranslated], n=1, cutoff=similarity)
            if candidate:
                target = next(e for e in untranslated if e.msgid == candidate[0])
                target.msgstr = gone.msgstr
                target.fuzzy = True
                untranslated.remove(target)
                counts["fuzzy"] += 1
        gone.obsolete = True
        gone.fuzzy = False
        obsoletes.append(gone)
        counts["bayatlayan"] += 1

    # Ayni msgid bayat listede yeniden belirdiyse bayat kopyasi gereksizdir.
    live = {e.key for e in result}
    obsoletes = [e for e in obsoletes if e.key not in live]

    current.entries = result + obsoletes
    return counts
