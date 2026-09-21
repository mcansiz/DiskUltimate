"""Bekleyen islemleri **sirasiyla gostererek** uygulayan pencere.

## Neden ayri bir pencere

Onceki surumde uygulama iki parcaya bolunmustu: once adimlari metin olarak
listeleyen bir onay kutusu, sonra tek cubuklu genel bir ilerleme penceresi.
Islem baslayinca liste kayboluyordu; kullanici "simdi hangi adimdayiz, hangisi
bitti, nerede takildi" sorularinin hicbirini goremiyordu. Yikici islemlerde bu
bilgi en cok gereken yerdir.

Bu pencere ikisini birlestirir ve **acik kalir**:

  * Adimlar uygulanacak **sira** ile listelenir; her adimin kendi ilerleme
    cubugu vardir.
  * Calisan adim isaretlenir, biten adim yesil onay alir, basarisiz adim
    kirmizi kalir ve altinda hata metni gorunur.
  * Bir adim durursa **sonrakiler calistirilmaz** ve listede "calistirilmadi"
    olarak durur (`OperationQueue.apply` boyle davranir; pencere bunu yalnizca
    gosterir).

Onay hala zorunludur ve hala **parti basinadir** (CLAUDE.md): pencere acildiginda
hicbir sey calismaz, kullanici "Uygula" demeden diske dokunulmaz.

## Is parcaciklari

Uzun suren is `QThread` icinde kosar (CLAUDE.md "Arayuz Donmasi" kurali).
Cekirdek geri cagrilari is parcacigindan gelir; her biri bir sinyale
donusturulur, boylece arayuz nesnelerine yalnizca arayuz parcacigi dokunur.
"""
from __future__ import annotations

from typing import Callable, Optional

from PyQt5.QtCore import QSize, Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout,
                             QHeaderView, QLabel, QProgressBar, QPushButton,
                             QTableWidget, QTableWidgetItem, QVBoxLayout,
                             QWidget)

from ...core import operations as ops
from ..icons import icon as app_icon
from ..theme import palette_color
from ...i18n import tr

STATE_WAITING = 0
STATE_RUNNING = 1
STATE_DONE = 2
STATE_FAILED = 3
STATE_SKIPPED = 4

COLOR_FAILED = "#c0392b"
COLOR_DONE = "#1e8449"


class _ApplyWorker(QThread):
    """Kuyrugu arka planda calistirir; her olayi bir sinyale cevirir."""

    stepStarted = pyqtSignal(int)
    stepProgress = pyqtSignal(int, str, int)
    stepDone = pyqtSignal(int, str)
    overall = pyqtSignal(str, int)
    finishedResult = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, session, queue: ops.OperationQueue):
        super().__init__()
        self.session = session
        self.queue = queue

    def run(self):
        try:
            result = self.queue.apply(
                self.session,
                progress=lambda msg, pct: self.overall.emit(msg, pct),
                on_step=lambda i, op: self.stepStarted.emit(i),
                on_step_progress=lambda i, op, msg, pct:
                    self.stepProgress.emit(i, msg, pct),
                on_step_done=lambda i, op, err: self.stepDone.emit(i, err))
            self.finishedResult.emit(result)
        except Exception as exc:                      # kullanici hatayi gormeli
            self.failed.emit(str(exc))


class ApplyDialog(QDialog):
    """Adim listesi + adim basina ilerleme cubugu olan uygulama penceresi.

    `prepare` arayuz parcaciginda cagrilir ve kaynagi yazma moduna alir
    (sistem diski onayi, bagli birim uyarisi...). `False` donerse hicbir sey
    calismaz — yetki kapilari bu pencerenin disinda, eskisi gibi durur.
    """

    def __init__(self, parent, session, queue: ops.OperationQueue,
                 prepare: Optional[Callable[[], bool]] = None):
        super().__init__(parent)
        self.session = session
        self.queue = queue
        self.prepare = prepare
        self.result_value = None
        self.error: Optional[str] = None
        self.started = False
        self._worker: Optional[_ApplyWorker] = None
        self._items = queue.items

        self.setWindowTitle(tr("Bekleyen islemleri uygula"))
        self.setModal(True)
        self.resize(820, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        self.header = QLabel(self._intro_text())
        self.header.setWordWrap(True)
        self.header.setTextFormat(Qt.RichText)
        layout.addWidget(self.header)

        self.table = QTableWidget(len(self._items), 4, self)
        self.table.setHorizontalHeaderLabels(
            [tr("Adim"), tr("Hedef"), tr("Durum"), tr("Ilerleme")])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setIconSize(QSize(20, 20))
        self.table.setAlternatingRowColors(True)
        # Odak cercevesi: tablo secilemez oldugu halde etkin hucrenin
        # etrafina noktali bir kutu ciziliyordu ve satiri sikisik
        # gosteriyordu.
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.verticalHeader().setDefaultSectionSize(38)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setHighlightSections(False)
        self.table.setColumnWidth(1, 130)
        self.table.setColumnWidth(2, 140)
        self.table.setColumnWidth(3, 170)
        for column in (2, 3):
            head = self.table.horizontalHeaderItem(column)
            if head is not None:
                head.setTextAlignment(int(Qt.AlignCenter))
        self._bars = []
        for row, operation in enumerate(self._items):
            self._fill_row(row, operation)
        layout.addWidget(self.table, 1)

        self.status = QLabel(tr("Hicbir sey calistirilmadi; diske "
                               "dokunulmadi."))
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(34)         # iki satirlik metin ziplamasin
        self.status.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(self.status)

        self.overall_bar = QProgressBar()
        self.overall_bar.setRange(0, 100)
        self.overall_bar.setFormat(tr("Genel: %p%"))
        self.overall_bar.setAlignment(Qt.AlignCenter)
        self.overall_bar.setMinimumHeight(24)    # yuzde metni cubuga sigsin
        layout.addWidget(self.overall_bar)
        layout.addSpacing(4)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.btn_run = QPushButton(app_icon("apply"), tr("Uygula"))
        self.btn_run.setDefault(True)
        self.btn_run.clicked.connect(self._start)
        self.btn_close = QPushButton(tr("Vazgec"))
        self.btn_close.clicked.connect(self.reject)
        buttons.addWidget(self.btn_run)
        buttons.addWidget(self.btn_close)
        layout.addLayout(buttons)

    # ------------------------------------------------------------------
    def _intro_text(self) -> str:
        where = getattr(self.session, "name", "")
        risky = self.queue.destructive_count
        text = tr("Asagidaki <b>{} adim</b> sirayla <b>{}</b> uzerinde "
                   "calistirilacak.", len(self._items), where)
        if risky:
            text += "<br><span style='color:%s'>%s</span>" % (
                COLOR_FAILED,
                tr("<b>{} adim veri kaybettirebilir</b> ve uygulandiktan "
                   "sonra geri alinamaz.", risky))
        return text

    def _fill_row(self, row: int, operation) -> None:
        name_item = QTableWidgetItem(f"{row + 1}. {operation.title}")
        name_item.setIcon(app_icon(operation.icon))
        tip = operation.detail or operation.title
        if operation.destructive:
            name_item.setForeground(QColor(COLOR_FAILED))
            tip += tr("\n(Bu adim veri kaybettirebilir)")
        name_item.setToolTip(tip)
        self.table.setItem(row, 0, name_item)
        self.table.setItem(row, 1, QTableWidgetItem(operation.target))
        state_item = QTableWidgetItem(tr("Bekliyor"))
        state_item.setTextAlignment(int(Qt.AlignCenter))
        state_item.setForeground(palette_color(self, "dim"))
        self.table.setItem(row, 2, state_item)
        # Cubuk dogrudan hucreye konursa hucre kenarlarina yapisir ve
        # satirlar birbirine girmis gorunur. Kenar payli bir kapsayici
        # icinde durur; boylece her satirda ayni bosluk olur.
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setTextVisible(False)
        bar.setFixedHeight(12)
        holder = QWidget()
        holder_layout = QHBoxLayout(holder)
        holder_layout.setContentsMargins(10, 12, 10, 12)
        holder_layout.addWidget(bar)
        self.table.setCellWidget(row, 3, holder)
        self._bars.append(bar)

    def _set_state(self, row: int, state: int, text: str = "") -> None:
        if not (0 <= row < self.table.rowCount()):
            return
        labels = {STATE_WAITING: tr("Bekliyor"),
                     STATE_RUNNING: tr("Calisiyor..."),
                     STATE_DONE: tr("Tamamlandi"),
                     STATE_FAILED: tr("Basarisiz"),
                     STATE_SKIPPED: tr("Calistirilmadi")}
        cell = self.table.item(row, 2)
        cell.setText(text or labels[state])
        font = cell.font()
        font.setBold(state in (STATE_RUNNING, STATE_FAILED))
        cell.setFont(font)
        if state == STATE_DONE:
            cell.setForeground(QColor(COLOR_DONE))
        elif state == STATE_FAILED:
            cell.setForeground(QColor(COLOR_FAILED))
        elif state == STATE_RUNNING:
            cell.setForeground(palette_color(self, "highlight"))
        else:
            cell.setForeground(palette_color(self, "dim"))

    # ------------------------------------------------------------------
    def _start(self) -> None:
        if self.started:
            return
        if self.prepare is not None and not self.prepare():
            # Yetki verilmedi ya da kullanici vazgecti: pencere acik kalir,
            # kuyruk bozulmaz.
            self.status.setText(tr("Uygulama baslatilmadi."))
            return
        self.started = True
        self.btn_run.setEnabled(False)
        self.btn_close.setEnabled(False)
        # Pencere bayraklari **degistirilmez**: `exec_` icinde bayrak
        # degistirmek pencereyi kisa sureligine gizleyip yeniden gostermek
        # demektir ve kipliligi bozabilir. Kapanma zaten `closeEvent` ve
        # `reject` ile engelleniyor.
        self.status.setText(tr("Uygulaniyor..."))

        self._worker = _ApplyWorker(self.session, self.queue)
        self._worker.stepStarted.connect(self._on_step_started)
        self._worker.stepProgress.connect(self._on_step_progress)
        self._worker.stepDone.connect(self._on_step_done)
        self._worker.overall.connect(self._on_overall)
        self._worker.finishedResult.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_step_started(self, row: int) -> None:
        self._set_state(row, STATE_RUNNING)
        self.table.scrollToItem(self.table.item(row, 0))

    def _on_step_progress(self, row: int, message: str, percent: int) -> None:
        if 0 <= row < len(self._bars):
            bar = self._bars[row]
            if percent < 0:                       # yuzde hesaplanamiyor
                if bar.maximum() != 0:
                    bar.setRange(0, 0)
            else:
                if bar.maximum() == 0:
                    bar.setRange(0, 100)
                bar.setValue(max(0, min(100, percent)))
        if message:
            self.status.setText(message)

    def _on_step_done(self, row: int, error: str) -> None:
        if 0 <= row < len(self._bars):
            bar = self._bars[row]
            if bar.maximum() == 0:
                bar.setRange(0, 100)
            bar.setValue(0 if error else 100)
        self._set_state(row, STATE_FAILED if error else STATE_DONE)
        if error:
            cell = self.table.item(row, 0)
            cell.setToolTip(error)
            for rest in range(row + 1, self.table.rowCount()):
                self._set_state(rest, STATE_SKIPPED)

    def _on_overall(self, message: str, percent: int) -> None:
        if percent < 0:
            if self.overall_bar.maximum() != 0:
                self.overall_bar.setRange(0, 0)
            return
        if self.overall_bar.maximum() == 0:
            self.overall_bar.setRange(0, 100)
        self.overall_bar.setValue(max(0, min(100, percent)))

    # ------------------------------------------------------------------
    def _on_finished(self, result) -> None:
        self.result_value = result
        self.overall_bar.setValue(100)
        if result.ok:
            self.status.setText(result.summary())
        else:
            self.status.setText(
                tr("<b>Durdu:</b> {}<br>Tamamlanan adimlar geri alinmaz; "
                   "duran adim ve sonrasi bekleyen listesinde kaldi.",
                   result.summary()))
        self._finish()

    def _on_failed(self, message: str) -> None:
        self.error = message
        self.status.setText(tr("<b>Uygulama basarisiz:</b> {}", message))
        self._finish()

    def _finish(self) -> None:
        self.btn_close.setText(tr("Kapat"))
        self.btn_close.setEnabled(True)
        self.btn_close.setDefault(True)
        self.btn_close.clicked.disconnect()
        self.btn_close.clicked.connect(self.accept)

    # ------------------------------------------------------------------
    def closeEvent(self, event):
        """Calisirken pencere kapatilamaz: yarida kesmek birimi bozar."""
        if self.started and self._worker is not None and self._worker.isRunning():
            event.ignore()
            return
        super().closeEvent(event)

    def reject(self) -> None:
        if self.started and self._worker is not None and self._worker.isRunning():
            return
        super().reject()

    def exec_(self) -> int:
        outcome = super().exec_()
        if self._worker is not None:
            self._worker.wait()
        return outcome


def run_apply(parent, session, queue, prepare=None):
    """Pencereyi acar; (calistirildi_mi, sonuc_veya_hata) dondurur."""
    dlg = ApplyDialog(parent, session, queue, prepare=prepare)
    dlg.exec_()
    if not dlg.started:
        return False, None
    if dlg.error:
        return False, dlg.error
    return True, dlg.result_value
