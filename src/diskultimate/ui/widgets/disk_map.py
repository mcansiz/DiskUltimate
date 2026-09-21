"""DiskGenius tarzi gorsel bolum haritasi.

Disk boyunca bolumleri oransal genislikte renkli bloklar halinde cizer;
her blokta dosya sistemi rengi, etiket, boyut ve doluluk cubugu bulunur.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from PyQt5.QtCore import QRect, QSize, Qt, pyqtSignal
from PyQt5.QtGui import (QBrush, QColor, QFont, QLinearGradient, QPainter,
                         QPen, QPixmap)
from PyQt5.QtWidgets import QSizePolicy, QWidget

from ...core.ptable import FreeRegion, Partition, human_size
from ..theme import (PLAN_COLOR, darken, fs_color, lighten,
                     palette_color, plan_label)
from ...i18n import tr

MIN_BLOCK_WIDTH = 64
BLOCK_MARGIN = 8


class Block:
    """Haritadaki tek bir gorsel blok (bolum veya bos alan)."""

    def __init__(self, kind: str, obj, rect: QRect):
        self.kind = kind          # 'part' | 'free'
        self.obj = obj            # Partition | FreeRegion
        self.rect = rect

    @property
    def index(self) -> int:
        return self.obj.index if self.kind == "part" else -1


class DiskMapWidget(QWidget):
    """Bolum haritasi; tiklama ile secim, cift tiklama ve sag tik menusu."""

    partitionSelected = pyqtSignal(int)          # bolum numarasi
    freeSelected = pyqtSignal(int, int)          # start_lba, sector_count
    partitionActivated = pyqtSignal(int)         # cift tiklama
    freeActivated = pyqtSignal(int, int)
    contextMenuRequested = pyqtSignal(object, object)  # (blok turu, nesne), global konum

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(128)
        self.setMaximumHeight(152)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self.setContextMenuPolicy(Qt.DefaultContextMenu)
        self._partitions: List[Partition] = []
        self._free: List[FreeRegion] = []
        self._blocks: List[Block] = []
        self._total_sectors = 0
        self._selected: Optional[Tuple[str, int]] = None
        self._hover: Optional[Block] = None
        self._disk_name = ""

    # -- veri ----------------------------------------------------------------
    def set_disk(self, name: str, total_sectors: int,
                 partitions: List[Partition], free: List[FreeRegion]) -> None:
        self._disk_name = name
        self._total_sectors = max(1, total_sectors)
        self._partitions = [p for p in partitions if not p.logical] + \
                           [p for p in partitions if p.logical]
        self._free = free
        self._layout_blocks()
        self.update()

    def clear(self) -> None:
        self._partitions, self._free, self._blocks = [], [], []
        self._total_sectors = 0
        self._selected = None
        self.update()

    def select_partition(self, index: int) -> None:
        self._selected = ("part", index)
        self.update()

    def select_free(self, start_lba: int) -> None:
        self._selected = ("free", start_lba)
        self.update()

    def selection(self) -> Optional[Tuple[str, int]]:
        return self._selected

    # -- yerlesim ------------------------------------------------------------
    def _segments(self) -> List[Tuple[str, object, int, int]]:
        """(tur, nesne, baslangic, sektor) listesi — sirali, cakismasiz."""
        items: List[Tuple[str, object, int, int]] = []
        for p in self._partitions:
            if p.logical:
                continue
            items.append(("part", p, p.start_lba, p.sector_count))
        for r in self._free:
            # genisletilmis bolum icindeki bos alanlar ust seviyede gosterilmez
            inside = any(p.type_id in (0x05, 0x0F, 0x85) and
                         p.start_lba <= r.start_lba <= p.end_lba
                         for p in self._partitions if p.scheme == "mbr")
            if inside:
                continue
            items.append(("free", r, r.start_lba, r.sector_count))
        items.sort(key=lambda x: x[2])
        return items

    def _layout_blocks(self) -> None:
        self._blocks = []
        items = self._segments()
        if not items or self._total_sectors <= 0:
            return
        area_w = max(60, self.width() - 2 * BLOCK_MARGIN)
        top = 26
        height = max(66, self.height() - top - 14)

        total_sec = sum(i[3] for i in items) or 1
        # once oransal genislik, sonra minimum genislik telafisi
        widths = [max(MIN_BLOCK_WIDTH, int(area_w * i[3] / total_sec)) for i in items]
        fazla = sum(widths) - area_w
        if fazla > 0:
            esnek = [i for i, w in enumerate(widths) if w > MIN_BLOCK_WIDTH]
            total_flex = sum(widths[i] - MIN_BLOCK_WIDTH for i in esnek) or 1
            for i in esnek:
                pay = int(fazla * (widths[i] - MIN_BLOCK_WIDTH) / total_flex)
                widths[i] = max(MIN_BLOCK_WIDTH, widths[i] - pay)
        elif fazla < 0 and widths:
            widths[-1] += -fazla

        x = BLOCK_MARGIN
        for (kind, obj, _s, _n), w in zip(items, widths):
            rect = QRect(x, top, max(8, w - 2), height)
            self._blocks.append(Block(kind, obj, rect))
            x += w

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_blocks()

    # -- cizim ---------------------------------------------------------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), palette_color(self, "base"))

        # ust bilgi seridi
        if self._disk_name:
            painter.setPen(palette_color(self, "dim"))
            f = painter.font(); f.setPointSize(8); painter.setFont(f)
            painter.drawText(QRect(BLOCK_MARGIN, 4, self.width() - 16, 18),
                             Qt.AlignLeft | Qt.AlignVCenter, self._disk_name)

        if not self._blocks:
            painter.setPen(palette_color(self, "dim"))
            painter.drawText(self.rect(), Qt.AlignCenter,
                             tr("Disk goruntusu acik degil"))
            painter.end()
            return

        for block in self._blocks:
            self._draw_block(painter, block)
        painter.end()

    def _draw_block(self, painter: QPainter, block: Block) -> None:
        rect = block.rect
        selected = self._is_selected(block)
        if block.kind == "part":
            part: Partition = block.obj
            base = fs_color(part.fs_type)
            title = part.display_name
            lower = human_size(part.size)
            tip = part.fs_type or tr("Bicimlendirilmemis")
        else:
            free: FreeRegion = block.obj
            base = palette_color(self, "window")
            title = tr("Bos alan")
            lower = human_size(free.size)
            tip = tr("Bolumlenmemis")

        # govde
        grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        if block.kind == "part":
            grad.setColorAt(0.0, lighten(base, 145))
            grad.setColorAt(0.35, lighten(base, 118))
            grad.setColorAt(1.0, base)
        else:
            grad.setColorAt(0.0, lighten(base, 108))
            grad.setColorAt(1.0, base)
        painter.fillRect(rect, QBrush(grad))

        if block.kind == "free":
            painter.save()
            painter.setClipRect(rect)
            pen = QPen(darken(base, 115), 1)
            painter.setPen(pen)
            step = 8
            for i in range(rect.left() - rect.height(), rect.right(), step):
                painter.drawLine(i, rect.bottom(), i + rect.height(), rect.top())
            painter.restore()

        # ust renk seridi (dosya sistemi rengi)
        if block.kind == "part":
            painter.fillRect(QRect(rect.left(), rect.top(), rect.width(), 5),
                             darken(base, 125))

        # cerceve
        vurgu = palette_color(self, "highlight")
        if self._hover is block and not selected:
            painter.setPen(QPen(vurgu, 1))
        elif selected:
            painter.setPen(QPen(vurgu, 2))
        else:
            painter.setPen(QPen(darken(palette_color(self, "window"), 130), 1))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))

        # Plan onizlemesi: henuz diske yazilmamis bolum kesik cerceve ve
        # kose rozetiyle isaretlenir (ADR 0031). Kullanici neyin gercek,
        # neyin planlanan oldugunu **bakar bakmaz** ayirt etmelidir.
        plan = plan_label(block.obj) if block.kind == "part" else ""
        rozet_genisligi = 0
        if plan:
            painter.setPen(QPen(QColor(PLAN_COLOR), 2, Qt.DashLine))
            painter.drawRect(rect.adjusted(2, 2, -3, -3))
            f = painter.font(); f.setPointSize(7); f.setBold(True)
            painter.setFont(f)
            genislik = painter.fontMetrics().width(plan) + 10
            if rect.width() > genislik + 60:
                rozet = QRect(rect.right() - genislik - 5, rect.top() + 8,
                              genislik, 15)
                painter.fillRect(rozet, QColor(PLAN_COLOR))
                painter.setPen(QColor("#ffffff"))
                painter.drawText(rozet, Qt.AlignCenter, plan)
                rozet_genisligi = genislik + 8

        # Metin rengi blok zeminine gore secilir: bolum bloklarinin zemini bizim
        # dosya sistemi rengimizdir, bos alaninki paletten gelir.
        if block.kind == "part":
            zemin_acik = lighten(base, 118).value() > 140
            metin_rengi = QColor("#16202b") if zemin_acik else QColor("#f2f6fa")
        else:
            metin_rengi = palette_color(self, "dim")
        painter.setPen(metin_rengi)
        f = painter.font(); f.setPointSize(9); f.setBold(True); painter.setFont(f)
        metin_alani = QRect(rect.left() + 6, rect.top() + 10,
                            rect.width() - 12 - rozet_genisligi, 16)
        painter.drawText(metin_alani, Qt.AlignLeft | Qt.AlignVCenter,
                         self._elide(painter, title, metin_alani.width()))

        f.setBold(False); f.setPointSize(8); painter.setFont(f)
        painter.setPen(metin_rengi)
        painter.drawText(QRect(rect.left() + 6, rect.top() + 28, rect.width() - 12, 14),
                         Qt.AlignLeft | Qt.AlignVCenter,
                         self._elide(painter, tip, rect.width() - 12))
        painter.drawText(QRect(rect.left() + 6, rect.top() + 43, rect.width() - 12, 14),
                         Qt.AlignLeft | Qt.AlignVCenter, lower)

        # doluluk cubugu
        if block.kind == "part":
            part: Partition = block.obj
            if part.fs_used >= 0 and part.fs_total > 0:
                oran = min(1.0, part.fs_used / part.fs_total)
                cub = QRect(rect.left() + 6, rect.bottom() - 17, rect.width() - 12, 10)
                painter.fillRect(cub, QColor(255, 255, 255, 190))
                painter.setPen(QPen(darken(base, 140), 1))
                painter.drawRect(cub)
                used = QRect(cub.left() + 1, cub.top() + 1,
                             int((cub.width() - 2) * oran), cub.height() - 1)
                if used.width() > 0:
                    painter.fillRect(used, darken(base, 135))
                if rect.width() > 110:
                    painter.setPen(metin_rengi)
                    f2 = painter.font(); f2.setPointSize(7); painter.setFont(f2)
                    painter.drawText(cub.adjusted(0, -1, 0, 0), Qt.AlignCenter,
                                     tr("%{:.0f} dolu", oran*100))

    @staticmethod
    def _elide(painter: QPainter, text: str, width: int) -> str:
        fm = painter.fontMetrics()
        return fm.elidedText(text, Qt.ElideRight, max(10, width))

    def _is_selected(self, block: Block) -> bool:
        if not self._selected:
            return False
        kind, key = self._selected
        if kind != block.kind:
            return False
        if kind == "part":
            return block.obj.index == key
        return block.obj.start_lba == key

    # -- etkilesim -----------------------------------------------------------
    def _block_at(self, pos) -> Optional[Block]:
        for block in self._blocks:
            if block.rect.contains(pos):
                return block
        return None

    def mousePressEvent(self, event):
        block = self._block_at(event.pos())
        if block is None:
            return
        if block.kind == "part":
            self._selected = ("part", block.obj.index)
            self.partitionSelected.emit(block.obj.index)
        else:
            self._selected = ("free", block.obj.start_lba)
            self.freeSelected.emit(block.obj.start_lba, block.obj.sector_count)
        self.update()

    def mouseDoubleClickEvent(self, event):
        block = self._block_at(event.pos())
        if block is None:
            return
        if block.kind == "part":
            self.partitionActivated.emit(block.obj.index)
        else:
            self.freeActivated.emit(block.obj.start_lba, block.obj.sector_count)

    def mouseMoveEvent(self, event):
        block = self._block_at(event.pos())
        if block is not self._hover:
            self._hover = block
            self.setCursor(Qt.PointingHandCursor if block else Qt.ArrowCursor)
            self.setToolTip(self._tooltip(block) if block else "")
            self.update()

    def leaveEvent(self, event):
        self._hover = None
        self.update()

    def contextMenuEvent(self, event):
        block = self._block_at(event.pos())
        if block is None:
            self.contextMenuRequested.emit(None, event.globalPos())
            return
        if block.kind == "part":
            self._selected = ("part", block.obj.index)
            self.partitionSelected.emit(block.obj.index)
        else:
            self._selected = ("free", block.obj.start_lba)
            self.freeSelected.emit(block.obj.start_lba, block.obj.sector_count)
        self.update()
        self.contextMenuRequested.emit((block.kind, block.obj), event.globalPos())

    @staticmethod
    def _tooltip(block: Block) -> str:
        if block.kind == "part":
            p: Partition = block.obj
            satir = [f"<b>{p.display_name}</b>",
                     tr("Tur: {}", p.type_name),
                     tr("Dosya sistemi: {}", p.fs_type or tr("yok")),
                     tr("Boyut: {}", human_size(p.size)),
                     tr("LBA: {} - {}", p.start_lba, p.end_lba)]
            if p.fs_used >= 0 and p.fs_total > 0:
                satir.append(tr("Kullanilan: {} / {}",
                                human_size(p.fs_used), human_size(p.fs_total)))
            return "<br>".join(satir)
        r: FreeRegion = block.obj
        return (tr("<b>Bos alan</b><br>Boyut: {}", human_size(r.size))
                + "<br>" + tr("LBA: {} - {}", r.start_lba, r.end_lba))
