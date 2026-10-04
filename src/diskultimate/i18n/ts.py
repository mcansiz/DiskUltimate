"""Qt Linguist `.ts` (XML) sozluklerini okuyup yazar — saf Python (ADR 0088).

Sozlukler 2026-10-04'ten beri `.ts` bicimindedir (kullanici karari; once
`.po`, ADR 0027 eki). Bicim degisti, **calisma zamani degismedi**: `.ts`
Qt'nin `QTranslator`'una verilmez — `core/` PyQt import etmez ve `.qm`
uretmek `lrelease` ister. Dosya burada `xml.etree` ile okunur ve `po.Entry`
/ `po.Catalog` modeline cevrilir; `tr()`/`trn()`/`trc()`, birlestirme
(`po.merge`) ve denetim (`tests/i18n_check`) ayni modelle calisir.

## Eslesme

| Bizde | `.ts` |
|---|---|
| baglamsiz giris | `<context><name>DiskUltimate</name>` |
| `trc(baglam, ...)` | `<context><name>baglam</name>` |
| kaynak metin (anahtar) | `<source>` |
| `trn` ikinci bicimi (`msgid_plural`) | `<extra-po-msgid_plural>` (`lconvert` ile ayni) |
| cogul ceviriler | `<message numerus="yes">` + `<numerusform>` |
| `fuzzy` / cevrilmemis | `<translation type="unfinished">` |
| bayat (`#~`) | `<translation type="vanished">` |
| `#.` aciklama | `<extracomment>` |
| `#:` kaynak konumu | `<location filename=".." line=".."/>` (dosyaya gore goreli) |

Qt Linguist dosyayi dogrudan acar; cogul bicim sayisini `language`
ozniteliginden bilir. Cogul kurali dosyada yazmaz — calisma zamani kurali
`i18n.PLURAL_RULES` tablosundan alir.
"""
from __future__ import annotations

import posixpath
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional
from xml.sax.saxutils import escape, quoteattr

from .po import Catalog, Entry

DEFAULT_CONTEXT = "DiskUltimate"
# Kaynak konumlari depo kokune gore tutulur ("src/diskultimate/ui/x.py:12");
# `.ts` icinde dosyanin kendi klasorune gore goreli yazilir (Qt Linguist
# kaynagi boyle bulur).
CATALOG_REL = "src/diskultimate/i18n/catalogs"

# Dil kodu -> `.ts` `language` ozniteligi (Qt Linguist'in tanidigi bicim).
TS_LANGUAGE = {"zh": "zh_CN", "tr": "tr_TR"}


def ts_language(code: str) -> str:
    return TS_LANGUAGE.get(code, code)


def code_from_ts_language(value: str) -> str:
    value = (value or "").strip()
    for code, ts_code in TS_LANGUAGE.items():
        if value == ts_code:
            return code
    return value.split("_")[0].lower()


def _ref_to_location(ref: str):
    path, _, line = ref.rpartition(":")
    if not path:
        path, line = ref, ""
    rel = posixpath.relpath(path, CATALOG_REL)
    return rel, line


def _location_to_ref(filename: str, line: str) -> str:
    path = posixpath.normpath(posixpath.join(CATALOG_REL, filename))
    return f"{path}:{line}" if line else path


# --------------------------------------------------------------------------
# Okuma
# --------------------------------------------------------------------------
def parse(text: str) -> Catalog:
    """`.ts` metnini `Catalog`'a cevirir. `headers['Language']` dil kodudur."""
    root = ET.fromstring(text)
    catalog = Catalog(headers={"Language": code_from_ts_language(
        root.get("language", ""))})
    for context in root.iter("context"):
        name = (context.findtext("name") or "")
        ctx: Optional[str] = None if name == DEFAULT_CONTEXT else name
        for message in context.iter("message"):
            catalog.entries.append(_parse_message(message, ctx))
    return catalog


def _parse_message(message, ctx: Optional[str]) -> Entry:
    source = message.findtext("source") or ""
    plural = message.findtext("extra-po-msgid_plural")
    numerus = message.get("numerus") == "yes"
    if numerus and plural is None:
        plural = source                  # Qt'nin kendi %n girisi: tek kaynak
    translation = message.find("translation")
    kind = translation.get("type", "") if translation is not None else "unfinished"
    entry = Entry(source, context=ctx, plural=plural if numerus else None)
    if translation is not None:
        if numerus:
            entry.plurals = [f.text or "" for f in translation.findall("numerusform")]
        else:
            entry.msgstr = translation.text or ""
    entry.obsolete = kind in ("vanished", "obsolete")
    has_text = any(entry.plurals) if numerus else bool(entry.msgstr)
    entry.fuzzy = kind == "unfinished" and has_text
    entry.comments = [c.text or "" for c in message.findall("extracomment")]
    entry.references = [_location_to_ref(loc.get("filename", ""), loc.get("line", ""))
                        for loc in message.findall("location")]
    return entry


# --------------------------------------------------------------------------
# Yazma — lupdate'e yakin, kararli bicim (fark okunur kalsin)
# --------------------------------------------------------------------------
def _text(value: str) -> str:
    # Tirnaklar da kacirilir (lupdate boyle yazar); satir sonu oldugu gibi.
    return escape(value, {'"': "&quot;", "'": "&apos;"})


def dump(catalog: Catalog, code: str, nplurals: int = 2) -> str:
    """`Catalog`'u `.ts` metnine cevirir. Baglamlar ilk gorulme sirasiyla."""
    groups: Dict[str, List[Entry]] = {}
    for entry in catalog.entries:
        groups.setdefault(entry.context if entry.context is not None
                          else DEFAULT_CONTEXT, []).append(entry)
    out = ['<?xml version="1.0" encoding="utf-8"?>', "<!DOCTYPE TS>",
           f'<TS version="2.1" language={quoteattr(ts_language(code))} '
           f'sourcelanguage="{ts_language("tr")}">']
    for name, entries in groups.items():
        out.append("<context>")
        out.append(f"    <name>{_text(name)}</name>")
        for entry in entries:
            out += _dump_message(entry, nplurals)
        out.append("</context>")
    out.append("</TS>")
    return "\n".join(out) + "\n"


def _dump_message(entry: Entry, nplurals: int) -> List[str]:
    numerus = entry.plural is not None
    lines = ['    <message numerus="yes">' if numerus else "    <message>"]
    for ref in entry.references:
        filename, line = _ref_to_location(ref)
        attr = f" line={quoteattr(line)}" if line else ""
        lines.append(f"        <location filename={quoteattr(filename)}{attr}/>")
    lines.append(f"        <source>{_text(entry.msgid)}</source>")
    if numerus:
        lines.append(f"        <extra-po-msgid_plural>{_text(entry.plural)}"
                     f"</extra-po-msgid_plural>")
    for comment in entry.comments:
        lines.append(f"        <extracomment>{_text(comment)}</extracomment>")
    if entry.obsolete:
        kind = ' type="vanished"'
    elif not entry.translated:
        kind = ' type="unfinished"'
    else:
        kind = ""
    if numerus:
        forms = list(entry.plurals or [])
        forms += [""] * max(0, nplurals - len(forms))
        lines.append(f"        <translation{kind}>")
        for form in forms:
            lines.append(f"            <numerusform>{_text(form)}</numerusform>")
        lines.append("        </translation>")
    else:
        lines.append(f"        <translation{kind}>{_text(entry.msgstr)}</translation>")
    lines.append("    </message>")
    return lines
