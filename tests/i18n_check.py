"""Ceviri denetimi ve sozluk uretimi.

    python3 -m tests.i18n_check            # denetle (CI / her oturum)
    python3 -m tests.i18n_check --write en # en.json'u kaynaktan tazele
    python3 -m tests.i18n_check --list     # cevrilecek metinleri bas

## Ne dogrulanir

1. **Eksik ceviri yok** — kaynaktaki her `tr(...)`/`mark(...)` metni her dil
   dosyasinda bulunur.
2. **Bayat giris yok** — dil dosyasinda kaynakta artik olmayan metin kalmaz;
   yoksa dosya zamanla cope doner ve eksik cevirileri gizler.
3. **Yer tutucular tutar** — `{}` sayisi ve `{ad}` adlari kaynakla cevirinin
   arasinda ayni; tutmazsa `str.format` calisma aninda patlardi.
4. **HTML etiketleri korunur** — `<b>`, `<br>` gibi etiketler kaybolmamis.
5. **Eylem metinleri tek yerde** — `MainWindow` icindeki her `self.act_*`
   nesnesinin `_retranslate_actions()` icinde bir satiri var. Olmayan bir
   eylem dil degistiginde eski dilde kalirdi.
6. **Dil degisimi metni gercekten degistirir** — sozluk yuklendiginde
   `tr()` cevrilmis metni dondurur, kaynak dile donunce geri gelir.

Metin **cevrilmemis kalmissa** bu denetim onu yakalamaz (kaynakta olmayan bir
seyi bilemez); onun icin `.claude/docs/testing.md` icindeki elle gozden gecirme
yordami vardir.
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src", "diskultimate")
CATALOGS = os.path.join(SRC, "i18n", "catalogs")

PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
HTML_TAG = re.compile(r"</?[a-zA-Z]+[^>]*>")


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


def collect_strings():
    """{metin: [dosya:satir, ...]} — cevrilecek butun kaynak metinler."""
    found = {}
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
            if node.func.id not in available or node.func.id not in ("tr", "mark"):
                continue
            if not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                found.setdefault(first.value, []).append(f"{rel}:{node.lineno}")
    return found


# --------------------------------------------------------------------------
# Sozluk dosyalari
# --------------------------------------------------------------------------
def catalog_path(code: str) -> str:
    return os.path.join(CATALOGS, f"{code}.json")


def languages():
    if not os.path.isdir(CATALOGS):
        return []
    return sorted(n[:-5] for n in os.listdir(CATALOGS) if n.endswith(".json"))


def load(code: str) -> dict:
    try:
        with open(catalog_path(code), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}


def write(code: str, data: dict) -> None:
    os.makedirs(CATALOGS, exist_ok=True)
    with open(catalog_path(code), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


def refresh(code: str) -> tuple:
    """Sozlugu kaynaga gore tazeler: eksikleri bos ekler, bayatlari atar."""
    strings = collect_strings()
    current = load(code)
    fresh = {}
    added = removed = 0
    for text in sorted(strings):
        if text in current:
            fresh[text] = current[text]
        else:
            fresh[text] = ""
            added += 1
    removed = len([k for k in current if k not in strings])
    write(code, fresh)
    return added, removed, len(fresh)


# --------------------------------------------------------------------------
# Denetimler
# --------------------------------------------------------------------------
def placeholders(text: str) -> list:
    """Metindeki yer tutucular; `{{` kacisi sayilmaz."""
    cleaned = text.replace("{{", "\x00").replace("}}", "\x01")
    return sorted(PLACEHOLDER.findall(cleaned))


def check_language(code: str, strings: dict) -> list:
    problems = []
    catalog = load(code)
    missing = [t for t in strings if t not in catalog or not catalog[t]]
    stale = [t for t in catalog if t not in strings]
    for text in missing[:8]:
        problems.append(f"  eksik ceviri: {text[:60]!r} ({strings[text][0]})")
    if len(missing) > 8:
        problems.append(f"  ... {len(missing) - 8} eksik ceviri daha")
    for text in stale[:8]:
        problems.append(f"  bayat giris (kaynakta yok): {text[:60]!r}")
    if len(stale) > 8:
        problems.append(f"  ... {len(stale) - 8} bayat giris daha")
    for text, translated in catalog.items():
        if text not in strings or not translated:
            continue
        if placeholders(text) != placeholders(translated):
            problems.append(
                f"  yer tutucu uyusmuyor: {text[:45]!r} -> {translated[:45]!r}")
        if sorted(HTML_TAG.findall(text)) != sorted(HTML_TAG.findall(translated)):
            problems.append(
                f"  HTML etiketi uyusmuyor: {text[:45]!r} -> {translated[:45]!r}")
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
    eksik = sorted(created - retranslated)
    return [f"  _retranslate_actions() icinde yok: self.{name}"
            for name in eksik]


def check_runtime() -> list:
    """Sozluk yuklenince `tr()` gercekten cevirir mi?"""
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from diskultimate import i18n

    problems = []
    source_before = i18n.current_language()
    try:
        for code in languages():
            catalog = load(code)
            sample = next((k for k, v in catalog.items()
                           if v and "{" not in k and k != v), None)
            i18n.set_language(code, remember=False)
            if i18n.current_language() != code:
                problems.append(f"  dil secilemedi: {code}")
                continue
            if sample and i18n.tr(sample) != catalog[sample]:
                problems.append(f"  {code}: tr() cevirmedi: {sample[:40]!r}")
            i18n.set_language("tr", remember=False)
            if sample and i18n.tr(sample) != sample:
                problems.append("  kaynak dile donulunce metin geri gelmedi")
    finally:
        i18n.set_language(source_before, remember=False)
    return problems


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--write":
        if len(args) < 2:
            print("kullanim: --write <dil kodu>")
            return 2
        added, removed, total = refresh(args[1])
        print(f"{args[1]}.json tazelendi: {total} giris "
              f"(+{added} eksik, -{removed} bayat)")
        return 0
    if args and args[0] == "--list":
        for text, places in sorted(collect_strings().items()):
            print(f"{places[0]:<48} {text[:80]!r}")
        return 0

    strings = collect_strings()
    print(f"Kaynakta {len(strings)} cevrilecek metin bulundu.")
    failures = 0

    codes = languages()
    if not codes:
        print("UYARI: hicbir ceviri dosyasi yok (i18n/catalogs bos)")
    for code in codes:
        problems = check_language(code, strings)
        if problems:
            failures += 1
            print(f"\n{code}.json: {len(problems)} sorun")
            for line in problems:
                print(line)
        else:
            print(f"{code}.json: TAMAM ({len(load(code))} giris)")

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
