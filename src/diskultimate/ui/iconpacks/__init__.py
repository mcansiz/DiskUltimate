"""Gomulu ucuncu taraf ikon paketleri (ADR 0046).

Her alt modul `tools/iconpacks.py` ile **uretilir** ve paketin yol verisini,
surumunu ve lisans metnini tasir. Burada o veri `svgpath` ile
`QPainterPath`e cevrilip cizilir; QtSvg ve ag gerekmez.

Paketler tek renklidir: renk cizim aninda verilir (temaya gore murekkep
rengi), SVG'deki `currentColor` gibi.
"""
from __future__ import annotations

import importlib
from typing import Dict, List, Optional, Tuple

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen

from ..svgpath import parse

MODULES = ("tabler", "phosphor", "lucide", "bootstrap", "material",
           "simpleicons")

_modules: Dict[str, object] = {}
_paths: Dict[Tuple[str, str], Optional[tuple]] = {}


def module(key: str):
    """Paket modulu (ilk istekte yuklenir; yoksa None)."""
    if key not in _modules:
        try:
            _modules[key] = importlib.import_module(f".{key}", __name__)
        except ImportError:
            _modules[key] = None
    return _modules[key]


def has(key: str, name: str) -> bool:
    mod = module(key)
    return bool(mod is not None and name in getattr(mod, "ICONS", {}))


def _compiled(key: str, name: str) -> Optional[tuple]:
    """(viewBox, [(QPainterPath, kip, kalinlik)]) — ayristirma bir kez yapilir."""
    cache_key = (key, name)
    if cache_key not in _paths:
        mod = module(key)
        entry = getattr(mod, "ICONS", {}).get(name) if mod else None
        if entry is None:
            _paths[cache_key] = None
        else:
            viewbox, items = entry
            parts = []
            for d, mode, width, rule in items:
                path = parse(d)
                # SVG varsayilani "nonzero"; QPainterPath'inki OddEven'dir.
                path.setFillRule(Qt.OddEvenFill if rule == "e"
                                 else Qt.WindingFill)
                parts.append((path, mode, width))
            _paths[cache_key] = (viewbox, parts)
    return _paths[cache_key]


def draw(painter: QPainter, key: str, name: str, size: float,
         color: QColor) -> bool:
    """Ikonu `size` piksellik kareye cizer. Ikon yoksa False."""
    compiled = _compiled(key, name)
    if compiled is None:
        return False
    viewbox, parts = compiled
    vx, vy, vw, vh = viewbox[:4]
    scale = size / max(vw, vh, 1e-6)
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.translate((size - vw * scale) / 2, (size - vh * scale) / 2)
    painter.scale(scale, scale)
    painter.translate(-vx, -vy)
    for path, mode, width in parts:
        if "f" in mode:
            painter.fillPath(path, color)
        if "s" in mode:
            pen = QPen(color, width)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.strokePath(path, pen)
    painter.restore()
    return True


def notices() -> List[dict]:
    """Uygulamaya giren paketlerin lisans bilgisi (Hakkinda penceresi icin)."""
    out = []
    for key in MODULES:
        mod = module(key)
        if mod is None:
            continue
        out.append({"key": key, "title": mod.TITLE, "version": mod.VERSION,
                    "license": mod.LICENSE_NAME, "url": mod.URL,
                    "text": mod.LICENSE, "count": len(mod.ICONS)})
    return out
