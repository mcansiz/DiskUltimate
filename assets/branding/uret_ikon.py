# -*- coding: utf-8 -*-
"""Uygulama ikonunu tek kaynaktan uretir: `.ico` -> temiz `.ico` + PNG + `.icns`.

Kullanici `favicon.ico` birakti; dosyanin **alti boyutu da tamamen opakti** —
saydamlik yerine dama deseni (128/256) ve duz acik gri (16..64) piksel olarak
gomulmustu. Oldugu gibi kullanilsa gorev cubugunda ve masaustunde ikonun
arkasinda acik gri bir kare gorunurdu. Bu arac arka plani geri kazandirir ve
uc platformun istedigi bicimleri tek kaynaktan uretir:

    Windows  -> app-icon.ico   (16/24/32/48/64 DIB + 128/256 PNG, alfa kanalli)
    Linux    -> app-icon-<n>.png (hicolor kurulumu ve .desktop icin)
    macOS    -> app-icon.icns

Harici arac gerekmez (ImageMagick / iconutil / Pillow yok); yalnizca PyQt5.
Kaynak degisirse tek komutla hepsi yeniden uretilir.

Kullanim:
    python3 tools/uret_ikon.py <kaynak.ico> <hedef-dizin>
"""
from __future__ import annotations

import os
import struct
import sys
from collections import Counter, deque

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PNG_SIZES = (16, 24, 32, 48, 64, 128, 256)

# macOS .icns tur etiketleri: (etiket, piksel kenari). ic11/ic12/ic13 "retina"
# turleridir: 16@2x=32, 32@2x=64, 128@2x=256.
ICNS_TYPES = ((b"icp4", 16), (b"icp5", 32), (b"ic11", 32), (b"ic12", 64),
              (b"ic07", 128), (b"ic13", 256), (b"ic08", 256))

T_HARD = 18     # bu kadar yakinsa kesin arka plan  -> alfa 0
T_SOFT = 52     # bu araliktakiler yumusak gecis    -> kismi alfa

_APP = None     # QGuiApplication referansi: tutulmazsa toplanir ve surec coker


def _qt_app():
    """Qt uygulamasini bir kez kurar ve **referansini saklar**."""
    global _APP
    from PyQt5.QtGui import QGuiApplication
    if _APP is None:
        _APP = QGuiApplication.instance() or QGuiApplication(sys.argv)
    return _APP


def _bg_colors(img):
    """Kenar seridindeki baskin renkler = arka plan adaylari."""
    w, h = img.width(), img.height()
    edge = [img.pixel(x, y) for x in range(w) for y in (0, h - 1)]
    edge += [img.pixel(x, y) for y in range(h) for x in (0, w - 1)]
    rgb = [((p >> 16) & 255, (p >> 8) & 255, p & 255) for p in edge]
    common = Counter(rgb).most_common(6)
    floor = max(2, len(rgb) * 0.03)
    return [c for c, n in common if n >= floor] or [common[0][0]]


def strip_background(img):
    from PyQt5.QtGui import QImage
    img = img.convertToFormat(QImage.Format_ARGB32)
    w, h = img.width(), img.height()
    bg = _bg_colors(img)

    def dist(p):
        r, g, b = (p >> 16) & 255, (p >> 8) & 255, p & 255
        return min(max(abs(r - c[0]), abs(g - c[1]), abs(b - c[2])) for c in bg)

    px = [[img.pixel(x, y) for y in range(h)] for x in range(w)]
    alpha = [[255] * h for _ in range(w)]
    seen = [[False] * h for _ in range(w)]
    queue = deque()
    for x in range(w):
        for y in (0, h - 1):
            queue.append((x, y))
    for y in range(h):
        for x in (0, w - 1):
            queue.append((x, y))

    while queue:
        x, y = queue.popleft()
        if x < 0 or y < 0 or x >= w or y >= h or seen[x][y]:
            continue
        d = dist(px[x][y])
        if d > T_SOFT:
            continue                       # nesneye geldik, dur
        seen[x][y] = True
        if d <= T_HARD:
            alpha[x][y] = 0                # kesin arka plan
        else:                              # yumusak gecis kenari
            alpha[x][y] = min(255, int(255.0 * (d - T_HARD) / (T_SOFT - T_HARD)))
        if d <= T_HARD:                    # yalnizca kesin arka plandan yayil
            queue.extend(((x+1, y), (x-1, y), (x, y+1), (x, y-1)))

    out = QImage(w, h, QImage.Format_ARGB32)
    out.fill(0)
    for x in range(w):
        for y in range(h):
            a = alpha[x][y]
            if a:
                out.setPixel(x, y, (a << 24) | (px[x][y] & 0x00FFFFFF))
    return out


def clean_icon(ico_path, sizes=(16, 24, 32, 48, 64, 128, 256)):
    """ICO'nun her boyutunu temizler; dict[boyut] = QImage dondurur."""
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QIcon
    _qt_app()
    icon = QIcon(ico_path)
    have = {s.width() for s in icon.availableSizes()}
    result = {}
    for size in sizes:
        if size in have:
            img = icon.pixmap(size, size).toImage()
        else:                              # ico'da yok: en yakin ustten kucult
            src = min((s for s in have if s >= size), default=max(have))
            img = icon.pixmap(src, src).toImage()
            img = strip_background(img).scaled(
                size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            result[size] = img
            continue
        result[size] = strip_background(img)
    return result


def _png(img):
    from PyQt5.QtCore import QBuffer, QByteArray
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QBuffer.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(data)


def _dib(img):
    """32 bit BMP/DIB: BITMAPINFOHEADER + alttan ustte BGRA + AND maskesi."""
    w, h = img.width(), img.height()
    xor = bytearray()
    for y in range(h - 1, -1, -1):                 # DIB satirlari alttan ustte
        for x in range(w):
            p = img.pixel(x, y)
            xor += bytes(((p) & 255, (p >> 8) & 255, (p >> 16) & 255, (p >> 24) & 255))
    stride = ((w + 31) // 32) * 4                  # 1 bpp, satir 4 bayta hizali
    mask = bytearray()
    for y in range(h - 1, -1, -1):
        row = bytearray(stride)
        for x in range(w):
            if (img.pixel(x, y) >> 24) == 0:       # tamamen saydam -> maske biti 1
                row[x // 8] |= 0x80 >> (x % 8)
        mask += row
    header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0,
                         len(xor) + len(mask), 0, 0, 0, 0)
    return header + bytes(xor) + bytes(mask)


def write_ico(images, path, png_from=128):
    """images: {boyut: QImage}. png_from ve ustu PNG, altindakiler DIB."""
    entries, blobs = [], []
    offset = 6 + 16 * len(images)
    for size in sorted(images):
        img = images[size]
        blob = _png(img) if size >= png_from else _dib(img)
        entries.append(struct.pack("<BBBBHHII",
                                   size % 256, size % 256, 0, 0, 1, 32,
                                   len(blob), offset))
        blobs.append(blob)
        offset += len(blob)
    with open(path, "wb") as fh:
        fh.write(struct.pack("<HHH", 0, 1, len(images)))
        fh.write(b"".join(entries))
        fh.write(b"".join(blobs))
    return path


def generate(ico_path, out_dir, base="app-icon"):
    """Kaynak .ico'yu temizler ve uc bicimi de uretir; yazilan yollari dondurur."""
    _qt_app()
    cleaned = clean_icon(ico_path, PNG_SIZES)
    os.makedirs(out_dir, exist_ok=True)
    written = []

    ico = os.path.join(out_dir, "%s.ico" % base)
    write_ico(cleaned, ico)
    written.append(ico)

    for size in PNG_SIZES:
        path = os.path.join(out_dir, "%s-%d.png" % (base, size))
        with open(path, "wb") as fh:
            fh.write(_png(cleaned[size]))
        written.append(path)

    chunks = []
    for tag, size in ICNS_TYPES:
        blob = _png(cleaned[size])
        chunks.append(tag + struct.pack(">I", len(blob) + 8) + blob)
    payload = b"".join(chunks)
    icns = os.path.join(out_dir, "%s.icns" % base)
    with open(icns, "wb") as fh:
        fh.write(b"icns" + struct.pack(">I", len(payload) + 8) + payload)
    written.append(icns)
    return written


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__.strip().splitlines()[-1])
    for path in generate(sys.argv[1], sys.argv[2]):
        print("  %8d bayt  %s" % (os.path.getsize(path), path))
