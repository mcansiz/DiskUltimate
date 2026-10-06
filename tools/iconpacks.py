"""Ikon paketlerinden gomulu veri modulleri uretir (GELISTIRME ARACI).

    python3 tools/iconpacks.py            # indir + src/diskultimate/ui/iconpacks/*.py yaz
    python3 tools/iconpacks.py --check    # yalnizca eksik ikonlari listele

Kullanici bu araca **ihtiyac duymaz**: uretilen `.py` dosyalari depoya girer
ve calisma aninda ag, SVG ya da QtSvg gerekmez (ADR 0046). Arac
jsDelivr'den paketin **sabitlenmis surumunu** indirir; ayni surum her zaman
ayni ciktiyi verir.

SVG ogeleri (`circle`, `rect`, `line`, `ellipse`, `polyline`, `polygon`)
burada `path` verisine cevrilir; calisma zamani yalnizca yol verisi okur
(`ui/svgpath.py`).
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "src", "diskultimate", "ui", "iconpacks")
CDN = "https://cdn.jsdelivr.net/npm/"

# paket: (npm adi, surum, ikon yolu kalibi, lisans dosyasi, lisans adi, baslik)
PACKS = {
    "tabler": ("@tabler/icons", "3.48.0", "icons/outline/{}.svg", "LICENSE", "MIT", "Tabler Icons"),
    "phosphor": ("@phosphor-icons/core", "2.1.1", "assets/regular/{}.svg", "LICENSE", "MIT", "Phosphor Icons"),
    "lucide": ("lucide-static", "1.48.0", "icons/{}.svg", "LICENSE", "ISC", "Lucide"),
    "bootstrap": ("bootstrap-icons", "1.13.1", "icons/{}.svg", "LICENSE", "MIT", "Bootstrap Icons"),
    "material": ("@material-symbols/svg-400", "0.47.5", "outlined/{}.svg", "LICENSE", "Apache-2.0", "Material Symbols"),
    "simpleicons": ("simple-icons", "16.33.0", "icons/{}.svg", "LICENSE.md", "CC0-1.0", "Simple Icons"),
}

# uygulama adi -> (tabler, phosphor, lucide, bootstrap, material)
MAP = {
    "apply": ("flag", "flag", "flag", "flag", "flag"),
    "stop": ("player-stop", "stop", "circle-stop", "stop-circle", "stop_circle"),
    "pending": ("hourglass", "hourglass", "hourglass", "hourglass-split", "hourglass_empty"),
    "discard": ("circle-x", "x-circle", "circle-x", "x-circle", "cancel"),
    "undo": ("arrow-back-up", "arrow-counter-clockwise", "undo-2", "arrow-counterclockwise", "undo"),
    "redo": ("arrow-forward-up", "arrow-clockwise", "redo-2", "arrow-clockwise", "redo"),
    "refresh": ("refresh", "arrows-clockwise", "refresh-cw", "arrow-repeat", "refresh"),
    "table": ("table", "table", "table", "table", "table"),
    "table-clear": ("table-off", "x-square", "grid-2x2-x", "x-square", "grid_off"),
    "convert": ("arrows-exchange", "arrows-left-right", "arrow-left-right", "arrow-left-right", "swap_horiz"),
    "partition-new": ("square-plus", "plus-square", "square-plus", "plus-square", "add_box"),
    "partition-delete": ("square-minus", "minus-square", "square-minus", "dash-square", "indeterminate_check_box"),
    "format": ("brush", "paint-brush-broad", "paintbrush", "brush", "format_paint"),
    "resize": ("arrows-horizontal", "arrows-out-line-horizontal", "move-horizontal", "arrows", "arrow_range"),
    "label": ("tag", "tag", "tag", "tag", "label"),
    "rename": ("pencil", "pencil-simple", "pencil", "pencil", "edit"),
    "type": ("hash", "hash", "hash", "hash", "tag"),
    "boot": ("star", "star", "star", "star", "star"),
    "wipe": ("eraser", "eraser", "eraser", "eraser", "ink_eraser"),
    "wipe-free": ("eraser-off", "broom", "brush-cleaning", "eraser-fill", "cleaning_services"),
    "image-resize": ("arrow-autofit-width", "arrows-out-simple", "scaling", "arrows-angle-expand", "aspect_ratio"),
    "fs-repair": ("tool", "wrench", "wrench", "wrench", "build"),
    "mount": ("plug-connected", "plugs-connected", "plug", "plug", "cable"),
    "unmount": ("player-eject", "eject", "unplug", "eject", "eject"),
    "disk": ("server-2", "hard-drive", "hard-drive", "hdd", "hard_drive"),
    "disk-system": ("server", "hard-drives", "server", "hdd-stack", "storage"),
    "disk-removable": ("device-usb", "usb", "usb", "usb-drive", "usb"),
    "image": ("disc", "disc", "disc", "disc", "album"),
    "backup": ("archive", "archive", "archive", "archive", "archive"),
    "partition": ("chart-pie", "chart-pie-slice", "chart-pie", "pie-chart", "pie_chart"),
    "search": ("search", "magnifying-glass", "search", "search", "search"),
    "recover": ("restore", "clock-counter-clockwise", "history", "clock-history", "history"),
    "clone": ("copy", "copy", "copy", "copy", "content_copy"),
    "info": ("info-circle", "info", "info", "info-circle", "info"),
    "warning": ("alert-triangle", "warning", "triangle-alert", "exclamation-triangle", "warning"),
    "shield": ("shield-lock", "shield-check", "shield-check", "shield-lock", "shield"),
    "save": ("device-floppy", "floppy-disk", "save", "floppy", "save"),
    "open": ("folder-open", "folder-open", "folder-open", "folder2-open", "folder_open"),
    "folder": ("folder", "folder", "folder", "folder", "folder"),
    "folder-new": ("folder-plus", "folder-plus", "folder-plus", "folder-plus", "create_new_folder"),
    "folder-add": ("folder-down", "folder-simple-plus", "folder-input", "folder-symlink", "drive_folder_upload"),
    "file": ("file", "file", "file", "file-earmark", "draft"),
    "up": ("arrow-up", "arrow-up", "arrow-up", "arrow-up", "arrow_upward"),
    "export": ("file-export", "export", "upload", "box-arrow-up", "upload"),
    "import": ("file-import", "download-simple", "download", "box-arrow-in-down", "download"),
    "trash": ("trash", "trash", "trash-2", "trash", "delete"),
    "bootloader": ("power", "power", "power", "power", "power_settings_new"),
    "efi": ("cpu", "cpu", "cpu", "cpu", "memory"),
    "boot-order": ("list-numbers", "list-numbers", "list-ordered", "list-ol", "format_list_numbered"),
}
PACK_ORDER = ("tabler", "phosphor", "lucide", "bootstrap", "material")

# Isletim sistemi amblemleri (Simple Icons). Windows YOK: Simple Icons butun
# Microsoft logolarini Microsoft hukuk ekibinin talebiyle v13.0.0'da kaldirdi
# (simple-icons#11236). Eski surumden almak o talebi dolanmak olur; Windows
# icin uygulamanin kendi geometrik amblemi kullanilir (ui/iconsets.py).
OS_MAP = {"linux": "linux", "macos": "apple"}

NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read()


def _f(value, default=0.0) -> float:
    try:
        return float(re.findall(NUM, str(value))[0])
    except (IndexError, ValueError):
        return default


def _fmt(v: float) -> str:
    return ("%.3f" % v).rstrip("0").rstrip(".") or "0"


def element_to_d(tag: str, a: dict) -> str:
    """SVG temel sekillerini yol verisine cevirir."""
    if tag == "path":
        return a.get("d", "")
    if tag in ("circle", "ellipse"):
        cx, cy = _f(a.get("cx")), _f(a.get("cy"))
        rx = _f(a.get("r", a.get("rx")))
        ry = _f(a.get("r", a.get("ry")))
        return (f"M{_fmt(cx - rx)} {_fmt(cy)}a{_fmt(rx)} {_fmt(ry)} 0 1 0 "
                f"{_fmt(2 * rx)} 0a{_fmt(rx)} {_fmt(ry)} 0 1 0 {_fmt(-2 * rx)} 0z")
    if tag == "line":
        return (f"M{_fmt(_f(a.get('x1')))} {_fmt(_f(a.get('y1')))}"
                f"L{_fmt(_f(a.get('x2')))} {_fmt(_f(a.get('y2')))}")
    if tag == "rect":
        x, y = _f(a.get("x")), _f(a.get("y"))
        w, h = _f(a.get("width")), _f(a.get("height"))
        rx = _f(a.get("rx", a.get("ry", 0)))
        ry = _f(a.get("ry", a.get("rx", 0)))
        if not rx:
            return f"M{_fmt(x)} {_fmt(y)}h{_fmt(w)}v{_fmt(h)}h{_fmt(-w)}z"
        return (f"M{_fmt(x + rx)} {_fmt(y)}h{_fmt(w - 2 * rx)}"
                f"a{_fmt(rx)} {_fmt(ry)} 0 0 1 {_fmt(rx)} {_fmt(ry)}v{_fmt(h - 2 * ry)}"
                f"a{_fmt(rx)} {_fmt(ry)} 0 0 1 {_fmt(-rx)} {_fmt(ry)}h{_fmt(-(w - 2 * rx))}"
                f"a{_fmt(rx)} {_fmt(ry)} 0 0 1 {_fmt(-rx)} {_fmt(-ry)}v{_fmt(-(h - 2 * ry))}"
                f"a{_fmt(rx)} {_fmt(ry)} 0 0 1 {_fmt(rx)} {_fmt(-ry)}z")
    if tag in ("polyline", "polygon"):
        pts = re.findall(NUM, a.get("points", ""))
        pairs = [(pts[i], pts[i + 1]) for i in range(0, len(pts) - 1, 2)]
        if not pairs:
            return ""
        d = "M" + " L".join(f"{x} {y}" for x, y in pairs)
        return d + ("z" if tag == "polygon" else "")
    return ""


def parse_svg(data: bytes):
    """(viewBox, [(d, kip, kalinlik, kural)]) — kip: 's' cizgi, 'f' dolgu."""
    text = re.sub(r"<!--.*?-->", "", data.decode("utf-8"), flags=re.S)
    root = ET.fromstring(text)
    vb = [float(v) for v in re.findall(NUM, root.get("viewBox", "0 0 24 24"))]
    items = []

    def walk(node, inherited):
        style = dict(inherited)
        for key in ("fill", "stroke", "stroke-width", "fill-rule"):
            if node.get(key) is not None:
                style[key] = node.get(key)
        if node.get("transform"):
            raise ValueError("transform desteklenmiyor")
        tag = node.tag.split("}")[-1]
        if tag in ("title", "desc", "defs", "linearGradient", "radialGradient"):
            return
        d = element_to_d(tag, node.attrib)
        if d:
            fill = style.get("fill", "#000")
            stroke = style.get("stroke", "none")
            mode = ""
            if fill not in ("none", "transparent"):
                mode += "f"
            if stroke not in ("none", "transparent"):
                mode += "s"
            if mode:
                items.append((d, mode, _f(style.get("stroke-width", 1), 1.0),
                              "e" if style.get("fill-rule") == "evenodd" else ""))
        for child in node:
            walk(child, style)

    walk(root, {})
    return vb, items


def build(check_only: bool = False) -> int:
    missing = 0
    os.makedirs(OUT, exist_ok=True)
    for key, (npm, version, pattern, license_file, license_name, title) in PACKS.items():
        base = f"{CDN}{npm}@{version}/"
        if key == "simpleicons":
            wanted = {ours: theirs for ours, theirs in OS_MAP.items()}
        else:
            column = PACK_ORDER.index(key)
            wanted = {ours: names[column] for ours, names in MAP.items()}
        icons, viewbox = {}, None
        for ours, theirs in sorted(wanted.items()):
            try:
                vb, items = parse_svg(fetch(base + pattern.format(theirs)))
            except Exception as exc:                  # noqa: BLE001
                print(f"  ! {key}/{theirs} ({ours}): {exc}")
                missing += 1
                continue
            viewbox = viewbox or vb
            icons[ours] = (vb, items)
        print(f"{key}: {len(icons)}/{len(wanted)} ikon")
        if check_only:
            continue
        license_text = fetch(base + license_file).decode("utf-8", "replace").strip()
        write_module(key, title, npm, version, license_name, license_text, icons)
    return missing


def write_module(key, title, npm, version, license_name, license_text, icons):
    lines = [
        f'"""{title} {version} — gomulu ikon verisi (OTOMATIK URETILDI).',
        "",
        "Elle duzenlemeyin; `python3 tools/iconpacks.py` ile yeniden uretin.",
        f"Kaynak: https://www.npmjs.com/package/{npm} ({license_name}).",
        "Paketin lisans metni asagida `LICENSE` sabitindedir ve uygulamanin",
        '"Ucuncu taraf lisanslari" penceresinde gosterilir.',
        '"""',
        "# flake8: noqa",
        "",
        f"TITLE = {title!r}",
        f"VERSION = {version!r}",
        f"LICENSE_NAME = {license_name!r}",
        f"URL = {('https://www.npmjs.com/package/' + npm)!r}",
        f"LICENSE = {license_text!r}",
        "",
        "# ad: (viewBox, [(yol, kip, cizgi_kalinligi, dolgu_kurali)])",
        "# kip: 'f' dolgu, 's' cizgi; kural: 'e' evenodd",
        "ICONS = {",
    ]
    for name in sorted(icons):
        vb, items = icons[name]
        lines.append(f"    {name!r}: ({tuple(vb)!r}, [")
        for d, mode, width, rule in items:
            lines.append(f"        ({d!r}, {mode!r}, {width!r}, {rule!r}),")
        lines.append("    ]),")
    lines.append("}")
    path = os.path.join(OUT, f"{key}.py")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"  -> {os.path.relpath(path, ROOT)}")


if __name__ == "__main__":
    sys.exit(1 if build(check_only="--check" in sys.argv) else 0)
