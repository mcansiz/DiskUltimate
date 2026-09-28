"""Bolum kenari surukleme denetleyicisi — iki gorunumun ortak motoru.

ADR 0049, 3. asama. Eskiden iki ayri surukleme kodu vardi: geri yukleme
seridi (`EditableLayout.move_edge`) ve ana harita (kendi sinir hesabini
yapan `_continue_drag`). Sinir hatalari birinde duzeltilip otekinde
kaliyordu. Bu denetleyici:

* kenarlari **modelden** okur (`EditableLayout.edges`), hareketi modele
  birakir (`move_edge`) — sinir, hizalama, en az/en cok, tasinabilirlik tek
  yerde (cekirdekte);
* tutamaklari cizer, fareyi isler, birakinca degisen bolumleri bildirir.

Gorunum yalnizca iki donusum verir: `to_x(lba)` ve `to_lba(x)`. Oransal serit
dogrusal donusum verir; blok harita (kucuk bolume en az genislik) blok blok
donusum verir. Boylece ayni denetleyici ikisinde de calisir.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

from PyQt5.QtCore import QObject, QPoint, QRect, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen

from ...core.ptable import human_size
from ..theme import darken, palette_color
from ...i18n import tr

HANDLE_W = 9


def draw_handle(painter: QPainter, widget, x: int, cy: int,
                highlight: bool, enabled: bool = True) -> None:
    """Surukleme tutamagi. `enabled=False`: gorunur ama henuz suruklenemez
    (sinirlar arka planda hesaplaniyor) — gri cizilir."""
    color = palette_color(widget, "highlight")
    if not enabled:
        color = QColor(palette_color(widget, "dim"))
    elif not highlight:
        color = darken(color, 115)
    box = QRect(x - HANDLE_W // 2, cy - 11, HANDLE_W, 22)
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setBrush(color)
    painter.setPen(QPen(darken(color, 150), 1))
    painter.drawRoundedRect(box, 3, 3)
    painter.setPen(QPen(QColor(255, 255, 255, 220), 1))
    for dy in (-4, 0, 4):
        painter.drawLine(box.left() + 2, cy + dy, box.right() - 2, cy + dy)
    painter.restore()


class EdgeDragController(QObject):
    """Bir gorunumdeki bolum kenarlarini surukletir.

    Sinyaller:
      * `changed()` — surukleme suruyor, model degisti (gorunum yeniden cizer)
      * `committed(list)` — birakildi; degisen bolum numaralari ve
        `before` sozlugu (`{no: (baslangic, sektor)}`) `last_before`da
    """

    changed = pyqtSignal()
    committed = pyqtSignal(list)

    def __init__(self, widget, to_x: Callable[[int], int],
                 to_lba: Callable[[int], int],
                 band: Callable[[], Tuple[int, int]]):
        super().__init__(widget)
        self.widget = widget
        self.to_x = to_x
        self.to_lba = to_lba
        self.band = band               # () -> (ust, alt) y araligi
        self.layout = None
        self.focus: Optional[int] = None   # yalniz bu bolumun kenarlari
        self.busy = False                  # sinirlar hesaplaniyor (gri)
        self.ghost = False                 # degisen araligi cerceveyle goster
        self.last_before: Dict[int, Tuple[int, int]] = {}
        self._hover = None
        self._drag = None
        self._before: Dict[int, Tuple[int, int]] = {}

    # -- veri ---------------------------------------------------------------
    def set_layout(self, layout, focus: Optional[int] = None,
                   busy: bool = False) -> None:
        self.layout = layout
        self.focus = focus
        self.busy = busy
        self._drag = None
        self._hover = None

    @property
    def dragging(self) -> bool:
        return self._drag is not None

    def edges(self) -> List[tuple]:
        """Gosterilecek kenarlar (odak varsa yalniz ona degenler)."""
        if self.layout is None:
            return []
        out = self.layout.edges()
        if self.focus is not None:
            out = [e for e in out if self.focus in (e[2], e[3])]
        return out

    def busy_edges(self) -> List[int]:
        """Sinirlar hazir degilken gri cizilecek x konumlari (odak bolumu)."""
        if not self.busy or self.layout is None or self.focus is None:
            return []
        try:
            slot = self.layout.get(self.focus)
        except Exception:                     # noqa: BLE001
            return []
        return [self.to_x(slot.new_start), self.to_x(slot.new_end + 1)]

    def edge_positions(self) -> List[Tuple[int, tuple]]:
        return [(self.to_x(e[0]), e) for e in self.edges()]

    # -- fare ---------------------------------------------------------------
    def _edge_at(self, pos: QPoint):
        top, bottom = self.band()
        if not top - 4 <= pos.y() <= bottom + 4:
            return None
        best = None
        for x, edge in self.edge_positions():
            distance = abs(x - pos.x())
            if distance <= HANDLE_W and (best is None or distance < best[0]):
                best = (distance, edge)
        return best[1] if best else None

    def hover(self, pos: QPoint) -> bool:
        """Imleci ve ipucunu ayarlar; kenar uzerindeyse True."""
        if self._drag is not None:
            return True
        edge = self._edge_at(pos)
        if edge != self._hover:
            self._hover = edge
            self.widget.update()
        if edge is not None:
            self.widget.setCursor(Qt.SizeHorCursor)
            self.widget.setToolTip(tr("Surukleyerek boyutlandirin; birakinca "
                                      "bekleyen islemlere eklenir"))
            return True
        top, bottom = self.band()
        if self.busy and top <= pos.y() <= bottom and any(
                abs(x - pos.x()) <= HANDLE_W for x in self.busy_edges()):
            self.widget.setCursor(Qt.BusyCursor)
            self.widget.setToolTip(tr("Dosya sistemi sinirlari hesaplaniyor..."))
            return True
        return False

    def press(self, pos: QPoint) -> bool:
        edge = self._edge_at(pos)
        if edge is None:
            return False
        self._drag = edge
        self._before = {s.index: (s.new_start, s.new_count)
                        for s in self.layout.parts}
        return True

    def move(self, pos: QPoint) -> bool:
        if self._drag is None:
            return False
        _lba, kind, left, right = self._drag
        if self.layout.move_edge(kind, left, right, self.to_lba(pos.x())):
            # Ayni kenari izle (konumu degisti, turu degismis olabilir)
            for edge in self.layout.edges():
                if (edge[2], edge[3]) == (left, right):
                    self._drag = edge
                    break
            self.widget.update()
            self.changed.emit()
        return True

    def release(self, pos: QPoint) -> bool:
        if self._drag is None:
            return False
        self._drag = None
        changed = [s.index for s in self.layout.parts
                   if (s.new_start, s.new_count) != self._before.get(s.index)]
        self.widget.update()
        if changed:
            self.last_before = dict(self._before)
            self.committed.emit(changed)
        return True

    def leave(self) -> None:
        if self._drag is None and self._hover is not None:
            self._hover = None
            self.widget.update()

    # -- cizim --------------------------------------------------------------
    def paint(self, painter: QPainter) -> None:
        if self.layout is None:
            return
        top, bottom = self.band()
        cy = (top + bottom) // 2
        if self.ghost and self._drag is not None:
            self._paint_ghosts(painter, top, bottom)
        for x in self.busy_edges():
            draw_handle(painter, self.widget, x, cy, False, enabled=False)
        if self.busy:
            return
        for x, edge in self.edge_positions():
            active = edge == self._hover or (
                self._drag is not None and (edge[2], edge[3]) ==
                (self._drag[2], self._drag[3]))
            draw_handle(painter, self.widget, x, cy, active)

    def _paint_ghosts(self, painter: QPainter, top: int, bottom: int) -> None:
        """Degisen bolumlerin yeni araligi: kesik cerceve + boyut rozeti."""
        vurgu = palette_color(self.widget, "highlight")
        ss = self.layout.sector_size
        for slot in self.layout.parts:
            if (slot.new_start, slot.new_count) == self._before.get(slot.index):
                continue
            left, right = self.to_x(slot.new_start), self.to_x(slot.new_end + 1)
            ghost = QRect(left, top + 1, max(4, right - left), bottom - top - 2)
            fill = QColor(vurgu)
            fill.setAlpha(45)
            painter.fillRect(ghost, fill)
            painter.setPen(QPen(vurgu, 2, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(ghost.adjusted(0, 0, -1, -1))
            text = human_size(slot.new_count * ss)
            font = painter.font()
            font.setPointSize(9)
            font.setBold(True)
            painter.setFont(font)
            width = painter.fontMetrics().width(text) + 12
            badge = QRect(max(2, min(self.widget.width() - width - 2,
                                     (left + right) // 2 - width // 2)),
                          bottom - 22, width, 18)
            painter.fillRect(badge, vurgu)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(badge, Qt.AlignCenter, text)
