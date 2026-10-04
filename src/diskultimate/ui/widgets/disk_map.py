"""DiskGenius tarzi gorsel bolum haritasi.

Disk boyunca bolumleri oransal genislikte renkli bloklar halinde cizer;
her blokta dosya sistemi rengi, etiket, boyut ve doluluk cubugu bulunur.

## Tutamaklar

Sahibi `set_edit_layout(model, odak, mesgul)` ile ortak yerlesim modelini
verirse **secili bolumun** kenarlarinda tutamaklar cikar. Surukleme geri
yukleme seridiyle **ayni denetleyicidedir** (`EdgeDragController`, ADR 0049):
sinir, hizalama ve tasinabilirlik cekirdek modelden gelir, burada hesap
yapilmaz. Bloklar tam oransal degildir (kucuk bolume en az genislik); bu
yuzden piksel <-> LBA donusumu **blok blok** yapilir ve denetleyiciye verilir.
Birakinca `layoutCommitted(bolumler)` yayilir; harita hicbir sey yazmaz,
sahibi degisikligi kuyruga koyar.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from PyQt5.QtCore import QRect, QSize, Qt, pyqtSignal
from PyQt5.QtGui import (QBrush, QColor, QFont, QLinearGradient, QPainter,
                         QPen, QPixmap)
from PyQt5.QtWidgets import QSizePolicy, QWidget

from ...core.fsregistry import fs_display
from ...core.ptable import FreeRegion, Partition, human_size
from ..theme import (PLAN_COLOR, blend, darken, draw_usage_bar, fs_color,
                     palette_color, plan_label)
from .edgedrag import EdgeDragController
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
    layoutCommitted = pyqtSignal(list)           # surukleme bitti: bolumler

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
        # Tutamaklar: ortak denetleyici (geri yukleme seridiyle ayni)
        self.edit = EdgeDragController(self, self.lba_to_x, self.x_to_lba,
                                       self._band)
        self.edit.ghost = True
        self.edit.committed.connect(self.layoutCommitted)

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
        self.edit.focus = index
        self.update()

    def select_free(self, start_lba: int) -> None:
        self._selected = ("free", start_lba)
        self.edit.focus = -1              # bos alanin tutamagi yok
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
        self.edit.paint(painter)
        painter.end()

    def _draw_block(self, painter: QPainter, block: Block) -> None:
        rect = block.rect
        selected = self._is_selected(block)
        if block.kind == "part":
            part: Partition = block.obj
            base = fs_color(part.fs_type)
            title = part.display_name
            lower = human_size(part.size)
            tip = fs_display(part.fs_type) or tr("Bicimlendirilmemis")
        else:
            free: FreeRegion = block.obj
            base = palette_color(self, "window")
            title = tr("Bos alan")
            lower = human_size(free.size)
            tip = tr("Bolumlenmemis")

        # Govde: acik panel. Renk yuku doluluk cubugunda ve sol kenar
        # seridindedir (ADR 0041) — blogun tamamini dosya sistemi rengine
        # boyamak, uzerindeki cubukla yarisiyor ve yaziyi bogar.
        panel = palette_color(self, "base")
        vurgu = palette_color(self, "highlight")
        zemin = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        if selected:
            zemin.setColorAt(0.0, blend(panel, vurgu, 0.10))
            zemin.setColorAt(1.0, blend(panel, vurgu, 0.20))
        else:
            zemin.setColorAt(0.0, panel)
            zemin.setColorAt(1.0, blend(panel, palette_color(self, "window"), 0.55))
        painter.fillRect(rect, QBrush(zemin))

        if block.kind == "free":
            painter.save()
            painter.setClipRect(rect)
            painter.setPen(QPen(blend(panel, base, 0.55), 1))
            step = 8
            for i in range(rect.left() - rect.height(), rect.right(), step):
                painter.drawLine(i, rect.bottom(), i + rect.height(), rect.top())
            painter.restore()

        # Sol kenar seridi: dosya sistemi rengi. Blok listesinde hangi bolumun
        # ne oldugu renkten okunmaya devam etsin diye durur.
        painter.fillRect(QRect(rect.left() + 1, rect.top() + 1, 4,
                               rect.height() - 2), base)

        # cerceve
        if self._hover is block and not selected:
            painter.setPen(QPen(vurgu, 1))
        elif selected:
            painter.setPen(QPen(vurgu, 2))
        else:
            painter.setPen(QPen(darken(palette_color(self, "window"), 130), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect.adjusted(0, 0, -1, -1))

        # Doluluk cubugu blogun ustunde durur; yuzde cubugun ortasindadir.
        cubuk = QRect(rect.left() + 9, rect.top() + 8, rect.width() - 18, 19)
        if block.kind == "part":
            part: Partition = block.obj
            oran = -1.0
            if part.fs_used >= 0 and part.fs_total > 0:
                oran = min(1.0, part.fs_used / part.fs_total)
            etiket = ""
            if oran >= 0 and cubuk.width() > 52:
                etiket = (tr("%{:.0f} dolu", oran * 100) if cubuk.width() > 108
                          else tr("%{:.0f}", oran * 100))   # dil "5%" ister
            f = painter.font(); f.setPointSize(8); f.setBold(True)
            painter.setFont(f)
            draw_usage_bar(painter, cubuk, oran, base, etiket)

        # Plan onizlemesi: henuz diske yazilmamis bolum kesik cerceve ve
        # kose rozetiyle isaretlenir (ADR 0031). Kullanici neyin gercek,
        # neyin planlanan oldugunu **bakar bakmaz** ayirt etmelidir.
        plan = plan_label(block.obj) if block.kind == "part" else ""
        rozet_genisligi = 0
        if plan:
            painter.setPen(QPen(QColor(PLAN_COLOR), 2, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect.adjusted(2, 2, -3, -3))
            f = painter.font(); f.setPointSize(7); f.setBold(True)
            painter.setFont(f)
            genislik = painter.fontMetrics().width(plan) + 10
            if rect.width() > genislik + 60:
                rozet = QRect(rect.right() - genislik - 6, rect.top() + 31,
                              genislik, 15)
                painter.fillRect(rozet, QColor(PLAN_COLOR))
                painter.setPen(QColor("#ffffff"))
                painter.drawText(rozet, Qt.AlignCenter, plan)
                rozet_genisligi = genislik + 8

        # Yazi paletten gelir: zemin artik acik panel, dosya sistemi rengi degil.
        metin_rengi = palette_color(self, "text")
        soluk_renk = palette_color(self, "dim")
        sol = rect.left() + 12
        genislik_yazi = rect.width() - 20
        painter.setPen(metin_rengi)
        f = painter.font(); f.setPointSize(9); f.setBold(True); painter.setFont(f)
        metin_alani = QRect(sol, rect.top() + 33,
                            genislik_yazi - rozet_genisligi, 17)
        painter.drawText(metin_alani, Qt.AlignLeft | Qt.AlignVCenter,
                         self._elide(painter, title, metin_alani.width()))

        f.setBold(False); f.setPointSize(8); painter.setFont(f)
        painter.setPen(soluk_renk)
        fs_alani = QRect(sol, rect.top() + 52, genislik_yazi, 15)
        painter.drawText(fs_alani, Qt.AlignLeft | Qt.AlignVCenter,
                         self._elide(painter, tip, genislik_yazi))
        # Isletim sisteminin bu bolumu bagladigi yer (Windows'ta surucu
        # harfi). Dosya sistemi adinin sagina, arta kalan yere yazilir;
        # sigmiyorsa hic yazilmaz — kirpilmis bir yol yanlis okunur.
        nokta = getattr(block.obj, "mount_point", "") if block.kind == "part" else ""
        if nokta:
            kalan = genislik_yazi - painter.fontMetrics().width(tip) - 12
            if kalan >= 34:
                painter.setPen(palette_color(self, "highlight"))
                fm = painter.fontMetrics()
                yazi = (nokta if fm.width(nokta) <= kalan
                        else fm.elidedText(nokta, Qt.ElideLeft, kalan))
                painter.drawText(fs_alani, Qt.AlignRight | Qt.AlignVCenter, yazi)
        painter.setPen(metin_rengi)
        painter.drawText(QRect(sol, rect.top() + 69, genislik_yazi, 15),
                         Qt.AlignLeft | Qt.AlignVCenter,
                         self._elide(painter, lower, genislik_yazi))

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
        if event.button() == Qt.LeftButton and self.edit.press(event.pos()):
            return
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
        if self.edit.move(event.pos()):
            return
        if self.edit.hover(event.pos()):
            return
        block = self._block_at(event.pos())
        if block is not self._hover:
            self._hover = block
            self.setCursor(Qt.PointingHandCursor if block else Qt.ArrowCursor)
            self.setToolTip(self._tooltip(block) if block else "")
            self.update()

    def mouseReleaseEvent(self, event):
        self.edit.release(event.pos())

    def leaveEvent(self, event):
        self._hover = None
        self.edit.leave()
        self.update()

    # -- tutamaklar ----------------------------------------------------------
    def set_edit_layout(self, layout, focus: Optional[int] = None,
                        busy: bool = False) -> None:
        """Ortak yerlesim modelini verir (None: tutamak yok).

        `focus`: yalnizca bu bolumun kenarlari gosterilir. `busy`: sinirlar
        arka planda hesaplaniyor — tutamaklar gri, suruklenmez.
        """
        if self.edit.dragging:
            return                     # surukleme sirasinda model degismez
        self.edit.set_layout(layout, focus=focus, busy=busy)
        self.update()

    def _band(self) -> Tuple[int, int]:
        """Tutamaklarin dikey araligi (bloklarin ust ve alt kenari)."""
        if not self._blocks:
            return 0, 0
        rect = self._blocks[0].rect
        return rect.top(), rect.bottom()

    def _segments_px(self) -> List[Tuple[int, int, int, int]]:
        """(lba_bas, lba_son_haric, x_bas, x_son) — blok blok olcek."""
        out = []
        for block in self._blocks:
            obj = block.obj
            out.append((obj.start_lba, obj.end_lba + 1, block.rect.left(),
                        block.rect.right() + 1))
        return out

    def lba_to_x(self, lba: int) -> int:
        segments = self._segments_px()
        if not segments:
            return 0
        for lba0, lba1, x0, x1 in segments:
            if lba <= lba1:
                if lba <= lba0:
                    return x0
                return x0 + int(round((lba - lba0) / max(1, lba1 - lba0)
                                      * (x1 - x0)))
        return segments[-1][3]

    def x_to_lba(self, x: int) -> int:
        segments = self._segments_px()
        if not segments:
            return 0
        for i, (lba0, lba1, x0, x1) in enumerate(segments):
            nxt = segments[i + 1][2] if i + 1 < len(segments) else x1
            if x <= x0:
                return lba0
            if x <= x1:
                return lba0 + int(round((x - x0) / max(1, x1 - x0)
                                        * (lba1 - lba0)))
            if x < nxt:                               # bloklar arasi bosluk
                return lba1
        return segments[-1][1]

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
                     tr("Dosya sistemi: {}", fs_display(p.fs_type) or tr("yok")),
                     tr("Boyut: {}", human_size(p.size)),
                     tr("LBA: {} - {}", p.start_lba, p.end_lba)]
            if p.fs_used >= 0 and p.fs_total > 0:
                satir.append(tr("Kullanilan: {} / {}",
                                human_size(p.fs_used), human_size(p.fs_total)))
            return "<br>".join(satir)
        r: FreeRegion = block.obj
        return (tr("<b>Bos alan</b><br>Boyut: {}", human_size(r.size))
                + "<br>" + tr("LBA: {} - {}", r.start_lba, r.end_lba))
