"""Ceviri denetimi ve `.po` sozluk uretimi.

    python3 -m tests.i18n_check            # denetle (her oturum / CI)
    python3 -m tests.i18n_check --write en # en.po'yu kaynaktan tazele
    python3 -m tests.i18n_check --list     # cevrilecek metinleri bas

## Ne dogrulanir

1. **Eksik ceviri yok** — kaynaktaki her `tr` / `mark` / `trn` / `trc` metni
   her dil dosyasinda cevrilidir. `#, fuzzy` isaretli giris de eksik sayilir:
   calisma aninda **kullanilmaz**, yani kullanici Turkce gorur.
2. **Yer tutucular tutar** — `{}` sayisi ve `{ad}` adlari kaynakla cevirinin
   arasinda ayni; tutmazsa `str.format` calisma aninda patlardi.
3. **HTML etiketleri korunur** — `<b>`, `<br>` gibi etiketler kaybolmamis.
4. **Cogul bicimleri tam** — `Plural-Forms` kac bicim diyorsa o kadar
   `msgstr[n]` dolu.
5. **Eylem metinleri tek yerde** — `MainWindow` icindeki her `self.act_*`
   nesnesinin `_retranslate_actions()` icinde bir satiri var.
6. **Dil degisimi metni gercekten degistirir** — sozluk yuklendiginde `tr()`
   cevrilmis metni dondurur, kaynak dile donunce geri gelir.

Bayatlamis (`#~`) girisler **hata degildir**: `.po` biciminde bunlar arsivdir,
kaynak metin geri gelirse cevirisi yeniden bulunur.

Metin **hic sarilmamissa** bu denetim onu goremez (kaynakta olmayan bir seyi
bilemez); onun icin sozde-yerellestirme vardir
(`DISKULTIMATE_LANG=qps`, `tests/ui_smoke.py -> sozde_denetimi`).
"""
from __future__ import annotations

import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src", "diskultimate")
CATALOGS = os.path.join(SRC, "i18n", "catalogs")
sys.path.insert(0, os.path.join(ROOT, "src"))

from diskultimate.i18n import po        # noqa: E402

PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
HTML_TAG = re.compile(r"</?[a-zA-Z]+[^>]*>")

VARSAYILAN_BASLIK = {
    "Project-Id-Version": "DiskUltimate 0.5.0-beta",
    "Report-Msgid-Bugs-To": "",
    "Language": "",
    "MIME-Version": "1.0",
    "Content-Type": "text/plain; charset=UTF-8",
    "Content-Transfer-Encoding": "8bit",
    "Plural-Forms": "nplurals=2; plural=(n != 1);",
    "X-Source-Language": "tr",
    "X-Generator": "tests/i18n_check.py",
}


# --------------------------------------------------------------------------
# Kaynak tarama
# --------------------------------------------------------------------------
def source_files():
    for base, dirs, files in os.walk(SRC):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in sorted(files):
            if name.endswith(".py"):
                yield os.path.join(base, name)


def uses_i18n(tree) -> set:
    """Dosyanin i18n'den aldigi adlar ({'tr'}, {'mark'} ...).

    Bu denetim onemli: `core/ext.py` icinde blok bitmap'ini isaretleyen yerel
    bir `mark()` islevi var ve onun metinleri ceviriye girmemeli.
    """
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module \
                and node.module.endswith("i18n"):
            names |= {alias.name for alias in node.names}
    return names


def _sabit(node):
    """Dugum sabit bir dize ise degerini, degilse None dondurur."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def collect_entries():
    """Kaynaktaki butun cevrilecek metinler — `po.Entry` listesi.

    `tr`/`mark` duz giris, `trn` cogul girisi, `trc` baglamli giris uretir.
    Ayni metin birden cok yerde geciyorsa tek giriste toplanir ve butun
    konumlar `#:` satirlarina yazilir.
    """
    girisler = {}
    for path in source_files():
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source, filename=path)
        available = uses_i18n(tree)
        if not available:
            continue
        rel = os.path.relpath(path, ROOT).replace("\\", "/")
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            ad = node.func.id
            if ad not in available or ad not in ("tr", "mark", "trn", "trc"):
                continue
            if not node.args:
                continue
            baglam = None
            cogul = None
            if ad == "trc":
                baglam = _sabit(node.args[0])
                metin = _sabit(node.args[1]) if len(node.args) > 1 else None
            elif ad == "trn":
                metin = _sabit(node.args[0])
                cogul = _sabit(node.args[1]) if len(node.args) > 1 else None
                if cogul is None:
                    continue
            else:
                metin = _sabit(node.args[0])
            if metin is None or (ad == "trc" and baglam is None):
                continue
            anahtar = (baglam, metin)
            giris = girisler.get(anahtar)
            if giris is None:
                giris = po.Entry(metin, context=baglam, plural=cogul)
                girisler[anahtar] = giris
            elif cogul and giris.plural is None:
                giris.plural = cogul
            giris.references.append(f"{rel}:{node.lineno}")
    for giris in girisler.values():
        giris.references = sorted(set(giris.references))
    return [girisler[k] for k in sorted(girisler, key=lambda k: (k[1], k[0] or ""))]


def collect_strings():
    """{metin: [dosya:satir, ...]} — geriye donuk uyum icin duz liste."""
    return {e.msgid: e.references for e in collect_entries()}


# --------------------------------------------------------------------------
# Sozluk dosyalari
# --------------------------------------------------------------------------
def catalog_path(code: str) -> str:
    return os.path.join(CATALOGS, f"{code}.po")


def languages():
    if not os.path.isdir(CATALOGS):
        return []
    return sorted(n[:-3] for n in os.listdir(CATALOGS) if n.endswith(".po"))


def load(code: str) -> po.Catalog:
    try:
        with open(catalog_path(code), "r", encoding="utf-8") as fh:
            return po.parse(fh.read())
    except FileNotFoundError:
        basliklar = dict(VARSAYILAN_BASLIK)
        basliklar["Language"] = code
        return po.Catalog(headers=basliklar)


def save(code: str, catalog: po.Catalog) -> None:
    os.makedirs(CATALOGS, exist_ok=True)
    with open(catalog_path(code), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(po.dump(catalog))


def nplurals(catalog: po.Catalog) -> int:
    match = re.search(r"nplurals\s*=\s*(\d+)", catalog.plural_forms or "")
    return int(match.group(1)) if match else 2


def refresh(code: str) -> dict:
    """Sozlugu kaynaga gore tazeler (`msgmerge` esdegeri).

    Ceviriler korunur; kaynakta kalmayan giris **silinmez**, bayatlatilir;
    benzer yeni bir giris varsa cevirisi oraya tasinip `fuzzy` isaretlenir.
    """
    catalog = load(code)
    if not catalog.headers:
        catalog.headers = dict(VARSAYILAN_BASLIK, Language=code)
    catalog.headers.setdefault("Language", code)
    sayac = po.merge(catalog, collect_entries())
    save(code, catalog)
    sayac.update(catalog.counts())
    return sayac


# --------------------------------------------------------------------------
# Denetimler
# --------------------------------------------------------------------------
def placeholders(text: str) -> list:
    """Metindeki yer tutucular; `{{` kacisi sayilmaz."""
    cleaned = text.replace("{{", "\x00").replace("}}", "\x01")
    return sorted(PLACEHOLDER.findall(cleaned))


def check_language(code: str, kaynak) -> list:
    problems = []
    catalog = load(code)
    girisler = catalog.by_key()
    forms = nplurals(catalog)

    eksik, fuzzy = [], []
    for giris in kaynak:
        mevcut = girisler.get(giris.key)
        if mevcut is None or not mevcut.translated:
            (fuzzy if (mevcut is not None and mevcut.fuzzy) else eksik).append(giris)
            continue
        if giris.plural is not None:
            dolu = [x for x in mevcut.plurals if x]
            if len(dolu) < forms:
                problems.append(
                    f"  cogul bicimi eksik ({len(dolu)}/{forms}): {giris.msgid[:45]!r}")
        if placeholders(giris.msgid) != placeholders(mevcut.msgstr or
                                                    (mevcut.plurals or [""])[0]):
            problems.append(
                f"  yer tutucu uyusmuyor: {giris.msgid[:45]!r}")
        beklenen = sorted(HTML_TAG.findall(giris.msgid))
        gelen = sorted(HTML_TAG.findall(mevcut.msgstr or
                                        (mevcut.plurals or [""])[0]))
        if beklenen != gelen:
            problems.append(f"  HTML etiketi uyusmuyor: {giris.msgid[:45]!r}")

    for giris in eksik[:6]:
        yer = giris.references[0] if giris.references else "?"
        problems.append(f"  eksik ceviri: {giris.msgid[:55]!r} ({yer})")
    if len(eksik) > 6:
        problems.append(f"  ... {len(eksik) - 6} eksik ceviri daha")
    for giris in fuzzy[:6]:
        problems.append(
            f"  fuzzy (gozden gecirilmeli, arayuzde Turkce gorunur): "
            f"{giris.msgid[:45]!r}")
    if len(fuzzy) > 6:
        problems.append(f"  ... {len(fuzzy) - 6} fuzzy giris daha")
    return problems


def check_actions() -> list:
    """`self.act_*` eylemlerinin hepsi `_retranslate_actions()` icinde mi?"""
    path = os.path.join(SRC, "ui", "main_window.py")
    with open(path, "r", encoding="utf-8") as fh:
        source = fh.read()
    tree = ast.parse(source)
    created, retranslated = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) \
                and isinstance(node.value.func, ast.Name) \
                and node.value.func.id == "QAction":
            for target in node.targets:
                if isinstance(target, ast.Attribute) \
                        and target.attr.startswith("act_"):
                    created.add(target.attr)
        if isinstance(node, ast.FunctionDef) and node.name == "_retranslate_actions":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Attribute) and sub.attr.startswith("act_"):
                    retranslated.add(sub.attr)
    return [f"  _retranslate_actions() icinde yok: self.{name}"
            for name in sorted(created - retranslated)]


def check_runtime() -> list:
    """Sozluk yuklenince `tr()` gercekten cevirir mi?"""
    from diskultimate import i18n

    problems = []
    onceki = i18n.current_language()
    try:
        for code in languages():
            catalog = load(code)
            ornek = next((e for e in catalog.entries
                          if e.translated and "{" not in e.msgid
                          and e.msgstr != e.msgid and e.context is None
                          and e.plural is None), None)
            i18n.set_language(code, remember=False)
            if i18n.current_language() != code:
                problems.append(f"  dil secilemedi: {code}")
                continue
            if ornek and i18n.tr(ornek.msgid) != ornek.msgstr:
                problems.append(f"  {code}: tr() cevirmedi: {ornek.msgid[:40]!r}")
            i18n.set_language("tr", remember=False)
            if ornek and i18n.tr(ornek.msgid) != ornek.msgid:
                problems.append("  kaynak dile donulunce metin geri gelmedi")
        # sozde dil: metin donusmeli, yer tutucu bozulmamali
        i18n.set_language(i18n.PSEUDO_LANGUAGE, remember=False)
        deneme = i18n.tr("Bolum {} bicimlendir", 3)
        if not deneme.startswith("[!") or "3" not in deneme:
            problems.append(f"  sozde dil bozuk: {deneme!r}")
    finally:
        i18n.set_language(onceki, remember=False)
    return problems


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--write":
        if len(args) < 2:
            print("kullanim: --write <dil kodu>")
            return 2
        sayac = refresh(args[1])
        print(f"{args[1]}.po tazelendi: {sayac['toplam']} giris "
              f"({sayac['korunan']} korundu, {sayac['yeni']} yeni, "
              f"{sayac['fuzzy']} fuzzy tasindi, "
              f"{sayac['bayatlayan']} bayatladi)")
        return 0
    if args and args[0] == "--list":
        for giris in collect_entries():
            yer = giris.references[0] if giris.references else "?"
            print(f"{yer:<48} {giris.msgid[:80]!r}")
        return 0

    kaynak = collect_entries()
    cogullar = sum(1 for e in kaynak if e.plural is not None)
    baglamlar = sum(1 for e in kaynak if e.context is not None)
    print(f"Kaynakta {len(kaynak)} cevrilecek metin bulundu "
          f"({cogullar} cogul, {baglamlar} baglamli).")
    failures = 0

    codes = languages()
    if not codes:
        print("UYARI: hicbir ceviri dosyasi yok (i18n/catalogs bos)")
    for code in codes:
        problems = check_language(code, kaynak)
        sayim = load(code).counts()
        if problems:
            failures += 1
            print(f"\n{code}.po: {len(problems)} sorun")
            for line in problems:
                print(line)
        else:
            print(f"{code}.po: TAMAM ({sayim['cevrili']} ceviri, "
                  f"{sayim['bayat']} bayat giris arsivde)")

    for name, problems in (("eylem metinleri", check_actions()),
                           ("calisma zamani", check_runtime())):
        if problems:
            failures += 1
            print(f"\n{name}: {len(problems)} sorun")
            for line in problems:
                print(line)
        else:
            print(f"{name}: TAMAM")

    print("\nSonuc:", "BASARILI" if not failures else f"{failures} baslikta sorun")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
