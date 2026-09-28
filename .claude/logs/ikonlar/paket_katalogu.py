"""Ucretsiz ikon paketlerinden karsilastirma sayfalari (yalnizca onizleme).

Paketler SVG'dir; uygulama QtSvg'ye dayanamaz (ADR 0025). Buradaki cizim
yalnizca **secim yapmak icin**: gelistirme makinesinde QtSvg ile PNG'ye
cevrilir. Secilen ikonlar uygulamaya alinacaksa yol verisi (SVG `d`) saf
Python'da QPainterPath'e cevrilir — calisma zamani bagimliligi dogmaz.

    python3 .claude/logs/ikonlar/paket_katalogu.py <cikis_klasoru>
"""
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ikon_katalogu as K                                    # noqa: E402
from PyQt5.QtCore import QByteArray, QRect, QRectF, Qt       # noqa: E402
from PyQt5.QtGui import QColor, QFont, QImage, QPainter      # noqa: E402
from PyQt5.QtSvg import QSvgRenderer                         # noqa: E402

CDN = "https://cdn.jsdelivr.net/npm/"
PACKS = {
    "Tabler (MIT)": CDN + "@tabler/icons@latest/icons/outline/{}.svg",
    "Phosphor (MIT)": CDN + "@phosphor-icons/core@latest/assets/regular/{}.svg",
    "Lucide (ISC)": CDN + "lucide-static@latest/icons/{}.svg",
    "Bootstrap (MIT)": CDN + "bootstrap-icons@latest/icons/{}.svg",
    "Material (Apache)": CDN + "@material-symbols/svg-400@latest/outlined/{}.svg",
}
OS_PACKS = {
    "Tabler (MIT)": CDN + "@tabler/icons@latest/icons/outline/{}.svg",
    "Phosphor (MIT)": CDN + "@phosphor-icons/core@latest/assets/regular/{}.svg",
    "Bootstrap (MIT)": CDN + "bootstrap-icons@latest/icons/{}.svg",
    "Font Awesome (CC BY)": CDN + "@fortawesome/fontawesome-free@latest/svgs/brands/{}.svg",
    "Simple Icons (CC0)": CDN + "simple-icons@latest/icons/{}.svg",
    "Devicon (MIT, renkli)": CDN + "devicon@latest/icons/{}.svg",
}

# anahtar: (tabler, phosphor, lucide, bootstrap, material)
MAP = {
    "apply": ("flag", "flag", "flag", "flag", "flag"),
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
    "resize": ("arrows-horizontal", "arrows-out-line-horizontal", "move-horizontal", "arrows", "width"),
    "label": ("tag", "tag", "tag", "tag", "label"),
    "rename": ("pencil", "pencil-simple", "pencil", "pencil", "edit"),
    "type": ("hash", "hash", "hash", "hash", "tag"),
    "boot": ("star", "star", "star", "star", "star"),
    "wipe": ("eraser", "eraser", "eraser", "eraser", "ink_eraser"),
    "wipe-free": ("eraser-off", "broom", "brush-cleaning", "eraser-fill", "cleaning_services"),
    "image-resize": ("arrow-autofit-width", "arrows-out-simple", "scaling", "arrows-angle-expand", "aspect_ratio"),
    "mount": ("plug-connected", "plugs-connected", "plug", "plug", "cable"),
    "unmount": ("player-eject", "eject", "unplug", "eject", "eject"),
    "disk": ("server-2", "hard-drive", "hard-drive", "hdd", "hard_drive"),
    "disk-system": ("server", "hard-drives", "server", "hdd-stack", "storage"),
    "disk-removable": ("device-usb", "usb", "usb", "usb-drive", "usb"),
    "image": ("disc", "disc", "disc", "disc", "album"),
    "backup": ("archive", "archive", "archive", "archive", "archive"),
    "partition": ("chart-pie", "chart-pie-slice", "chart-pie", "pie-chart", "pie_chart"),
    "search": ("search", "magnifying-glass", "search", "search", "search"),
    "recover": ("restore", "clock-counter-clockwise", "history", "clock-history", "restore"),
    "clone": ("copy", "copy", "copy", "copy", "content_copy"),
    "info": ("info-circle", "info", "info", "info-circle", "info"),
    "warning": ("alert-triangle", "warning", "triangle-alert", "exclamation-triangle", "warning"),
    "shield": ("shield-lock", "shield-check", "shield-check", "shield-lock", "shield"),
    "save": ("device-floppy", "floppy-disk", "save", "floppy", "save"),
    "open": ("folder-open", "folder-open", "folder-open", "folder2-open", "folder_open"),
    "folder": ("folder", "folder", "folder", "folder", "folder"),
    "folder-new": ("folder-plus", "folder-plus", "folder-plus", "folder-plus", "create_new_folder"),
    "folder-add": ("folder-down", "folder-notch-plus", "folder-input", "folder-symlink", "drive_folder_upload"),
    "file": ("file", "file", "file", "file-earmark", "draft"),
    "up": ("arrow-up", "arrow-up", "arrow-up", "arrow-up", "arrow_upward"),
    "export": ("file-export", "export", "upload", "box-arrow-up", "upload"),
    "import": ("file-import", "download-simple", "download", "box-arrow-in-down", "download"),
    "trash": ("trash", "trash", "trash-2", "trash", "delete"),
    "bootloader": ("power", "power", "power", "power", "power_settings_new"),
    "efi": ("cpu", "cpu", "cpu", "cpu", "memory"),
    "boot-order": ("list-numbers", "list-numbers", "list-ordered", "list-ol", "format_list_numbered"),
}

# isletim sistemi: (ad, marka rengi, tabler, phosphor, bootstrap, fa, simple, devicon)
OS_ROWS = [
    ("Windows", "#0078D4", "brand-windows", "windows-logo", "windows", "windows", "windows", "windows11/windows11-original"),
    ("Linux (Tux)", "#222222", None, "linux-logo", None, "linux", "linux", "linux/linux-original"),
    ("macOS / Apple", "#555555", "brand-apple", "apple-logo", "apple", "apple", "apple", "apple/apple-original"),
    ("Ubuntu", "#E95420", "brand-ubuntu", None, "ubuntu", "ubuntu", "ubuntu", "ubuntu/ubuntu-original"),
    ("Debian", "#A81D33", "brand-debian", None, None, "debian", "debian", "debian/debian-original"),
    ("Fedora", "#51A2DA", "brand-fedora", None, None, "fedora", "fedora", "fedora/fedora-original"),
    ("Arch", "#1793D1", "brand-arch", None, None, None, "archlinux", "archlinux/archlinux-original"),
    ("Linux Mint", "#87CF3E", "brand-mint", None, None, None, "linuxmint", "linuxmint/linuxmint-original"),
]

CACHE = os.path.join(os.environ.get("TMPDIR", "/tmp"), "du-icon-cache")


def fetch(url):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, url.replace("/", "_").replace(":", ""))
    if os.path.exists(path):
        data = open(path, "rb").read()
        return data or None
    try:
        data = urllib.request.urlopen(url, timeout=20).read()
    except Exception:
        data = b""
    open(path, "wb").write(data)
    return data or None


def svg_draw(data, color, keep_colors=False):
    text = data.decode("utf-8", "replace")
    if not keep_colors:
        text = text.replace("currentColor", color)
        start = text.find("<svg")
        tag_end = text.find(">", start)
        if start >= 0 and "fill=" not in text[start:tag_end]:
            text = text[:start] + f'<svg fill="{color}"' + text[start + 4:]
    renderer = QSvgRenderer(QByteArray(text.encode("utf-8")))

    def draw(p, u, dark):
        renderer.render(p, QRectF(0, 0, 16 * u, 16 * u))
    return draw if renderer.isValid() else None


def page(title, headers, rows, path, sizes=(16, 24, 32), name_w=150, col_w=118):
    width = name_w + col_w * len(headers) + 10
    row_h = 70
    img = QImage(width, 46 + row_h * len(rows) + 10, QImage.Format_ARGB32)
    img.fill(QColor("#ffffff"))
    p = QPainter(img); p.setRenderHint(QPainter.Antialiasing)
    f = QFont("DejaVu Sans"); f.setPixelSize(13); f.setBold(True); p.setFont(f)
    p.setPen(QColor("#111")); p.drawText(8, 20, title)
    f.setPixelSize(10); p.setFont(f)
    for i, h in enumerate(headers):
        p.drawText(QRect(name_w + i * col_w, 26, col_w - 6, 18), Qt.AlignLeft | Qt.AlignVCenter, h)
    f.setBold(False); f.setPixelSize(11); p.setFont(f)
    y = 46
    for label, sub, cells in rows:
        p.setPen(QColor("#222")); p.drawText(QRect(8, y, name_w - 8, row_h // 2), Qt.AlignVCenter, label)
        p.setPen(QColor("#999")); p.drawText(QRect(8, y + row_h // 2 - 8, name_w - 8, row_h // 2), Qt.AlignVCenter, sub)
        for c, cell in enumerate(cells):
            x0 = name_w + c * col_w
            for r, (bg, dark) in enumerate((("#f6f7f9", False), ("#2b2f35", True))):
                yy = y + 2 + r * 33
                p.fillRect(QRect(x0, yy, col_w - 6, 31), QColor(bg))
                fn = cell(dark) if cell else None
                if fn is None:
                    p.setPen(QColor("#bbb")); p.drawText(QRect(x0, yy, col_w - 6, 31), Qt.AlignCenter, "—")
                    continue
                xx = x0 + 5
                for s in sizes:
                    im = QImage(s, s, QImage.Format_ARGB32); im.fill(Qt.transparent)
                    q = QPainter(im); q.setRenderHint(QPainter.Antialiasing); fn(q, s / 16.0, dark); q.end()
                    p.drawImage(xx, yy + (31 - s) // 2, im); xx += s + 6
        y += row_h
    p.end(); img.save(path); return path


def pack_cell(tmpl, name, color, keep=False):
    if not name:
        return None
    data = fetch(tmpl.format(name))
    if not data:
        return None

    def make(dark):
        if color is None:
            ink = "#d6dae0" if dark else "#3b4652"
        elif dark and QColor(color).lightness() < 90:
            ink = "#e6e8eb"          # koyu marka rengi koyu zeminde kaybolmasin
        else:
            ink = color
        return svg_draw(data, ink, keep_colors=keep)
    return make


def const(fn):
    return lambda dark: fn


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else HERE
    headers = ["A mevcut", "B kutucuk", "C cizgi"] + list(PACKS)
    lookup = {k: (label, color, sym) for _t, rows in K.ITEMS for k, label, color, sym in rows}
    for n, (title, rows) in enumerate(K.ITEMS, 1):
        page_rows = []
        for key, label, color, sym in rows:
            if key not in MAP:
                continue
            cells = [const(K.current(key)), const(K.fam_b(sym, color)), const(K.fam_c(sym, color))]
            cells += [pack_cell(t, nm, None) for t, nm in zip(PACKS.values(), MAP[key])]
            page_rows.append((label, key, cells))
        if page_rows:
            print(page(f"Paketler {n}. {title}", headers, page_rows,
                       os.path.join(out, f"paket-{n}.png")))
    # isletim sistemleri
    os_headers = ["A mevcut", "B kutucuk", "C cizgi"] + list(OS_PACKS)
    own = {"Windows": "os:windows", "Linux (Tux)": "os:linux", "macOS / Apple": "os:macos"}
    rows = []
    for name, brand, *names in OS_ROWS:
        cells = []
        if name in own:
            key = own[name]; _l, color, sym = lookup[key]
            cells += [const(K.current(key)), const(K.fam_b(sym, color)), const(K.fam_c(sym, color))]
        else:
            cells += [None, None, None]
        for (pname, tmpl), nm in zip(OS_PACKS.items(), names):
            cells.append(pack_cell(tmpl, nm, brand, keep=pname.startswith("Devicon")))
        rows.append((name, brand, cells))
    print(page("Isletim sistemleri ve dagitimlar", os_headers, rows,
               os.path.join(out, "paket-os.png"), col_w=112))
