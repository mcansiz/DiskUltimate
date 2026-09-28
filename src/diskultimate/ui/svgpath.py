"""SVG yol verisini (`d`) saf Python'da `QPainterPath`e cevirir.

## Neden

Ikon paketleri (Tabler, Phosphor, Lucide, Bootstrap, Material, Simple Icons)
SVG olarak dagitilir. `PyQt5.QtSvg` her dagitimda kurulu degildir (ADR
0025) ve calisma zamani bagimliligi eklenmez (CLAUDE.md). Oysa ikonlarin
hepsi tek bir seyden olusur: **yol verisi**. Bu modul o veriyi dogrudan
`QPainterPath`e cevirir; cizim `QPainter` ile yapilir, yani ikonlar her
boyutta keskin kalir ve uc platformda ayni gorunur.

Desteklenen komutlar: M L H V C S Q T A Z (buyuk/kucuk harf). `A` (eliptik
yay) kubik Bezier'lere cevrilir (SVG 1.1 ek F.6 donusumu). Sayilar paketlerin
sikistirilmis bicimini de okur: `.54.5` iki sayidir, yay bayraklari bitisik
yazilabilir (`a1 1 0 00-.11.135`).
"""
from __future__ import annotations

import math
import re
from typing import Iterator, List, Tuple

from PyQt5.QtCore import QPointF
from PyQt5.QtGui import QPainterPath

_TOKEN = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_ARGS = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2,
         "A": 7, "Z": 0}


class SvgPathError(ValueError):
    pass


def _tokens(d: str) -> List[str]:
    return _TOKEN.findall(d)


def _read_arc_args(tokens: List[str], i: int) -> Tuple[List[float], int]:
    """Yay argumanlari: bayraklar tek hanelidir ve bitisik yazilabilir."""
    out: List[float] = []
    while len(out) < 7:
        if i >= len(tokens):
            raise SvgPathError("eksik yay argumani")
        tok = tokens[i]
        if len(out) in (3, 4):
            # Bayrak her zaman tek hanedir: "00", "01.5", "1.5" -> ilk hane
            # bayrak, kalani sonraki argumandir.
            if tok[0] not in "01":
                raise SvgPathError(f"gecersiz yay bayragi: {tok}")
            out.append(float(tok[0]))
            if len(tok) > 1:
                tokens[i] = tok[1:]
            else:
                i += 1
            continue
        out.append(float(tok))
        i += 1
    return out, i


def parse(d: str) -> QPainterPath:
    """SVG `d` dizgesinden `QPainterPath` uretir."""
    path = QPainterPath()
    tokens = _tokens(d)
    i = 0
    cmd = ""
    x = y = 0.0
    start_x = start_y = 0.0
    last_ctrl = None            # S/T yansitmasi icin son kontrol noktasi
    last_cmd = ""
    while i < len(tokens):
        tok = tokens[i]
        if tok.isalpha():
            cmd = tok
            i += 1
            if cmd in "Zz":
                path.closeSubpath()
                x, y = start_x, start_y
                last_cmd, last_ctrl = cmd, None
                continue
        elif not cmd:
            raise SvgPathError("komutsuz sayi")
        upper = cmd.upper()
        rel = cmd.islower()
        if upper == "A":
            args, i = _read_arc_args(tokens, i)
        else:
            n = _ARGS[upper]
            if i + n > len(tokens):
                raise SvgPathError(f"eksik arguman: {cmd}")
            args = [float(t) for t in tokens[i:i + n]]
            i += n
        ox, oy = (x, y) if rel else (0.0, 0.0)

        if upper == "M":
            x, y = ox + args[0], oy + args[1]
            path.moveTo(x, y)
            start_x, start_y = x, y
            cmd = "l" if rel else "L"          # ardisik ciftler cizgidir
            last_ctrl = None
        elif upper == "L":
            x, y = ox + args[0], oy + args[1]
            path.lineTo(x, y)
            last_ctrl = None
        elif upper == "H":
            x = (x if rel else 0.0) + args[0]
            path.lineTo(x, y)
            last_ctrl = None
        elif upper == "V":
            y = (y if rel else 0.0) + args[0]
            path.lineTo(x, y)
            last_ctrl = None
        elif upper == "C":
            c1 = (ox + args[0], oy + args[1])
            c2 = (ox + args[2], oy + args[3])
            x, y = ox + args[4], oy + args[5]
            path.cubicTo(QPointF(*c1), QPointF(*c2), QPointF(x, y))
            last_ctrl = c2
        elif upper == "S":
            if last_cmd.upper() in ("C", "S") and last_ctrl is not None:
                c1 = (2 * x - last_ctrl[0], 2 * y - last_ctrl[1])
            else:
                c1 = (x, y)
            c2 = (ox + args[0], oy + args[1])
            x, y = ox + args[2], oy + args[3]
            path.cubicTo(QPointF(*c1), QPointF(*c2), QPointF(x, y))
            last_ctrl = c2
        elif upper == "Q":
            c = (ox + args[0], oy + args[1])
            x, y = ox + args[2], oy + args[3]
            path.quadTo(QPointF(*c), QPointF(x, y))
            last_ctrl = c
        elif upper == "T":
            if last_cmd.upper() in ("Q", "T") and last_ctrl is not None:
                c = (2 * x - last_ctrl[0], 2 * y - last_ctrl[1])
            else:
                c = (x, y)
            x, y = ox + args[0], oy + args[1]
            path.quadTo(QPointF(*c), QPointF(x, y))
            last_ctrl = c
        elif upper == "A":
            rx, ry, rot, large, sweep = args[:5]
            ex, ey = ox + args[5], oy + args[6]
            for c1, c2, end in _arc_to_beziers(x, y, rx, ry, rot, large,
                                               sweep, ex, ey):
                path.cubicTo(QPointF(*c1), QPointF(*c2), QPointF(*end))
            x, y = ex, ey
            last_ctrl = None
        last_cmd = cmd
    return path


def _arc_to_beziers(x1, y1, rx, ry, phi_deg, large, sweep, x2, y2
                    ) -> Iterator[tuple]:
    """SVG eliptik yayini kubik Bezier parcalarina boler (en cok 90 derece)."""
    if (x1, y1) == (x2, y2):
        return
    rx, ry = abs(rx), abs(ry)
    if rx == 0 or ry == 0:
        yield (x1, y1), (x2, y2), (x2, y2)
        return
    phi = math.radians(phi_deg)
    cos_p, sin_p = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2.0, (y1 - y2) / 2.0
    x1p = cos_p * dx + sin_p * dy
    y1p = -sin_p * dx + cos_p * dy
    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1:                                  # yaricap yetmiyorsa buyut
        s = math.sqrt(lam)
        rx, ry = rx * s, ry * s
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    coef = math.sqrt(max(0.0, num / den)) if den else 0.0
    if bool(large) == bool(sweep):
        coef = -coef
    cxp = coef * rx * y1p / ry
    cyp = -coef * ry * x1p / rx
    cx = cos_p * cxp - sin_p * cyp + (x1 + x2) / 2.0
    cy = sin_p * cxp + cos_p * cyp + (y1 + y2) / 2.0

    def angle(ux, uy, vx, vy):
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a

    t1 = angle(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dt = angle((x1p - cxp) / rx, (y1p - cyp) / ry,
               (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi
    segments = max(1, int(math.ceil(abs(dt) / (math.pi / 2) - 1e-9)))
    delta = dt / segments
    k = 4.0 / 3.0 * math.tan(delta / 4.0)

    def point(t):
        ct, st = math.cos(t), math.sin(t)
        return (cx + rx * ct * cos_p - ry * st * sin_p,
                cy + rx * ct * sin_p + ry * st * cos_p)

    def deriv(t):
        ct, st = math.cos(t), math.sin(t)
        return (-rx * st * cos_p - ry * ct * sin_p,
                -rx * st * sin_p + ry * ct * cos_p)

    t = t1
    for _ in range(segments):
        p0, p3 = point(t), point(t + delta)
        d0, d3 = deriv(t), deriv(t + delta)
        c1 = (p0[0] + k * d0[0], p0[1] + k * d0[1])
        c2 = (p3[0] - k * d3[0], p3[1] - k * d3[1])
        yield c1, c2, p3
        t += delta
