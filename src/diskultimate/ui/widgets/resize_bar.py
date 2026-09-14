"""Fareyle surukleyerek bolum boyutlandirma cubugu (DiskGenius tarzi).

Kapsayici alan (bolum + onundeki/arkasindaki bos alan) tek bir serit olarak
cizilir. Serit uc parcadir:

    [ onundeki bosluk ][ BOLUM ][ arkasindaki bosluk ]

Iki tutamak vardir: bolumun **sol** ve **sag** kenari. Sol kenari surukleme
bolumu **tasir** (boyut sabit kalmaz, secime gore degisir), sag kenar yalnizca
boyutu degistirir. Tum degerler sektor cinsinden tutulur ve hizalama birimine
(varsayilan 1 MiB) yuvarlanir; boylece surukleyerek hizasiz bolum uretilemez.
"""
from __future__ import annotations

from typing import Optional, Tuple

from PyQt5.QtCore import QRect, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPen
from PyQt5.QtWidgets import QSizePolicy, QWidget

from ...core.ptable import human_size
from ..theme import FREE_COLOR, darken, fs_color, lighten, palette_color

TUTAMAK = 7           # tutamagin piksel genisligi
KENAR = 12            # seridin sol/sag bosluğu
YUKSEKLIK = 74


class ResizeBar(QWidget):
    """Suruklenebilir bolum boyutlandirma seridi."""

    changed = pyqtSignal(int, int)        # (yeni_start_lba, yeni_sector_count)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(YUKSEKLIK + 26)
        self.setMaximumHeight(YUKSEKLIK + 26)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

        self.window_start = 0
        self.window_count = 1
        self.start = 0
        self.count = 1
        self.sector_size = 512
        self.align = 2048                 # 1 MiB
        self.min_count = 1
        self.max_count = 1
        self.fs_type = ""
        self.label = ""
        self.used_bytes = -1
        self.can_move = True

        self._surukleme: Optional[str] = None      # 'sol' | 'sag' | 'govde'
        self._basla_x = 0
        self._basla_start = 0
        self._basla_count = 0
        self._uzerinde: Optional[str] = None

    # -- veri ---------------------------------------------------------------
    def setup(self, window_start: int, window_count: int, start: int,
              count: int, sector_size: int, align: int, min_count: int,
              max_count: int, fs_type: str = "", label: str = "",
              used_bytes: int = -1, can_move: bool = True) -> None:
        self.window_start = window_start
        self.window_count = max(1, window_count)
        self.start = start
        self.count = count
        self.sector_size = sector_size
        self.align = max(1, align)
        self.min_count = max(1, min_count)
        self.max_count = max(self.min_count, max_count)
        self.fs_type = fs_type
        self.label = label
        self.used_bytes = used_bytes
        self.can_move = can_move
        self.update()

    def set_values(self, start: int, count: int) -> None:
        """Disaridan (sayi kutularindan) gelen degeri uygular."""
        self.start, self.count = start, count
        self.update()

    # -- olcek --------------------------------------------------------------
    def _serit(self) -> QRect:
        return QRect(KENAR, 4, max(1, self.width() - 2 * KENAR), YUKSEKLIK)

    def _x(self, lba: int) -> int:
        s = self._serit()
        oran = (lba - self.window_start) / self.window_count
        return s.left() + int(round(oran * s.width()))

    def _lba(self, x: int) -> int:
        s = self._serit()
        oran = (x - s.left()) / max(1, s.width())
        return self.window_start + int(round(oran * self.window_count))

    def _hizala(self, lba: int) -> int:
        return (lba // self.align) * self.align

    @property
    def window_end(self) -> int:
        return self.window_start + self.window_count

    # -- cizim --------------------------------------------------------------
    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        s = self._serit()
        metin = palette_color(self, "text")
        sonuk = palette_color(self, "dim")

        # bos alan zemini
        p.fillRect(s, QColor(FREE_COLOR))
        p.setPen(QPen(darken(QColor(FREE_COLOR), 130), 1))
        p.drawRect(s.adjusted(0, 0, -1, -1))

        sol = max(s.left(), self._x(self.start))
        sag = min(s.right() + 1, self._x(self.start + self.count))
        blok = QRect(sol, s.top(), max(2, sag - sol), s.height())

        renk = fs_color(self.fs_type)
        gecis = QLinearGradient(blok.topLeft(), blok.bottomLeft())
        gecis.setColorAt(0.0, lighten(renk, 135))
        gecis.setColorAt(1.0, darken(renk, 112))
        p.fillRect(blok, QBrush(gecis))
        p.setPen(QPen(darken(renk, 150), 1))
        p.drawRect(blok.adjusted(0, 0, -1, -1))

        # kullanilan alan cubugu (dosya sistemi doluluk orani)
        if self.used_bytes >= 0 and self.count > 0:
            toplam = self.count * self.sector_size
            oran = min(1.0, self.used_bytes / max(1, toplam))
            dolu = QRect(blok.left() + 4, blok.bottom() - 13,
                         max(0, int((blok.width() - 8) * oran)), 7)
            p.fillRect(QRect(blok.left() + 4, blok.bottom() - 13,
                             blok.width() - 8, 7),
                       QColor(255, 255, 255, 90))
            if dolu.width() > 0:
                p.fillRect(dolu, darken(renk, 165))

        # blok yazisi
        if blok.width() > 56:
            p.setPen(QColor("#ffffff") if renk.value() < 190 else QColor("#20262e"))
            yazi = QFont(self.font())
            yazi.setPointSizeF(max(7.5, yazi.pointSizeF()))
            p.setFont(yazi)
            baslik = self.label or (self.fs_type or "Bolum")
            p.drawText(blok.adjusted(6, 5, -6, 0), Qt.AlignLeft | Qt.AlignTop,
                       baslik)
            p.drawText(blok.adjusted(6, 21, -6, 0), Qt.AlignLeft | Qt.AlignTop,
                       human_size(self.count * self.sector_size))

        # tutamaklar
        for ad, x in (("sol", sol), ("sag", sag)):
            if ad == "sol" and not self.can_move:
                continue
            self._tutamak_ciz(p, x, s, ad == self._uzerinde or ad == self._surukleme)

        # bos alan etiketleri
        p.setFont(self.font())
        p.setPen(sonuk)
        on = (self.start - self.window_start) * self.sector_size
        arka = (self.window_end - self.start - self.count) * self.sector_size
        taban = QRect(s.left(), s.bottom() + 4, s.width(), 18)
        if on > 0:
            p.drawText(taban, Qt.AlignLeft | Qt.AlignVCenter,
                       f"◀ onunde {human_size(on)}")
        if arka > 0:
            p.drawText(taban, Qt.AlignRight | Qt.AlignVCenter,
                       f"arkasinda {human_size(arka)} ▶")
        if on <= 0 and arka <= 0:
            p.setPen(sonuk)
            p.drawText(taban, Qt.AlignHCenter | Qt.AlignVCenter,
                       "bolum kapsayici alanin tamamini kapliyor")

    def _tutamak_ciz(self, p: QPainter, x: int, s: QRect, vurgulu: bool) -> None:
        renk = palette_color(self, "highlight")
        if not vurgulu:
            renk = darken(renk, 110)
        kutu = QRect(x - TUTAMAK // 2, s.top() - 3, TUTAMAK, s.height() + 6)
        p.fillRect(kutu, renk)
        p.setPen(QPen(darken(renk, 150), 1))
        p.drawRect(kutu.adjusted(0, 0, -1, -1))
        p.setPen(QPen(QColor(255, 255, 255, 200), 1))
        orta = kutu.center().y()
        for dy in (-4, 0, 4):
            p.drawLine(kutu.left() + 2, orta + dy, kutu.right() - 2, orta + dy)

    # -- fare ---------------------------------------------------------------
    def _tutamak_bul(self, x: int) -> Optional[str]:
        sol = self._x(self.start)
        sag = self._x(self.start + self.count)
        if self.can_move and abs(x - sol) <= TUTAMAK:
            return "sol"
        if abs(x - sag) <= TUTAMAK:
            return "sag"
        if self.can_move and sol < x < sag:
            return "govde"
        return None

    def mouseMoveEvent(self, event) -> None:
        if self._surukleme is None:
            self._uzerinde = self._tutamak_bul(event.x())
            imlec = {"sol": Qt.SizeHorCursor, "sag": Qt.SizeHorCursor,
                     "govde": Qt.OpenHandCursor}.get(self._uzerinde, Qt.ArrowCursor)
            self.setCursor(imlec)
            self.update()
            return
        self._surukle(event.x())

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        self._surukleme = self._tutamak_bul(event.x())
        self._basla_x = event.x()
        self._basla_start = self.start
        self._basla_count = self.count
        if self._surukleme == "govde":
            self.setCursor(Qt.ClosedHandCursor)

    def mouseReleaseEvent(self, event) -> None:
        self._surukleme = None
        self.setCursor(Qt.ArrowCursor)
        self.update()

    def _surukle(self, x: int) -> None:
        fark = self._lba(x) - self._lba(self._basla_x)
        if self._surukleme == "sag":
            yeni_count = self._hizala(self._basla_count + fark)
            yeni_start = self.start
        elif self._surukleme == "sol":
            yeni_start = self._hizala(self._basla_start + fark)
            yeni_count = self._basla_start + self._basla_count - yeni_start
        elif self._surukleme == "govde":
            yeni_start = self._hizala(self._basla_start + fark)
            yeni_count = self._basla_count
        else:
            return
        self._uygula(yeni_start, yeni_count)

    def _uygula(self, start: int, count: int) -> None:
        """Sinirlara kirparak degerleri uygular ve sinyal yayar."""
        count = max(self.min_count, min(self.max_count, count))
        start = max(self.window_start, start)
        if start + count > self.window_end:
            if self._surukleme == "sag":
                count = self.window_end - start
            else:
                start = self.window_end - count
        start = max(self.window_start, self._hizala(start))
        count = max(self.min_count, min(self.max_count, count))
        if start + count > self.window_end:
            count = self.window_end - start
        if (start, count) == (self.start, self.count):
            return
        self.start, self.count = start, count
        self.update()
        self.changed.emit(start, count)

    # -- klavye -------------------------------------------------------------
    def keyPressEvent(self, event) -> None:
        """Ok tuslariyla ince ayar: tek adim = bir hizalama birimi."""
        adim = self.align * (16 if event.modifiers() & Qt.ShiftModifier else 1)
        if event.key() == Qt.Key_Left:
            self._surukleme = "sag"
            self._uygula(self.start, self.count - adim)
        elif event.key() == Qt.Key_Right:
            self._surukleme = "sag"
            self._uygula(self.start, self.count + adim)
        elif event.key() == Qt.Key_Home and self.can_move:
            self._surukleme = "govde"
            self._uygula(self.start - adim, self.count)
        elif event.key() == Qt.Key_End and self.can_move:
            self._surukleme = "govde"
            self._uygula(self.start + adim, self.count)
        else:
            super().keyPressEvent(event)
            return
        self._surukleme = None

    def sizeHint(self) -> QSize:
        return QSize(520, YUKSEKLIK + 26)
