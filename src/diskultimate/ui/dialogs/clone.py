"""Disk klonlama: secim, onay ve ilerleme **tek formda** (ADR 0096).

Onceki akis dort pencereydi: "nereye?" soru kutusu, hedef listesi ya da
dosya kaydetme penceresi, ilerleme penceresi, sonuc kutusu. Kullanici
isteğiyle (DiskGenius "Clone Disk") kaynak, hedef, onaylar, ilerleme, guc
secenekleri ve sonuc bu pencerede durur; is bitince pencere acik kalir.

Fiziksel disk guvenlik kurallari (CLAUDE.md) eski hedef penceresinden
aynen tasindi:
* uygun olmayan disk (kaynagin kendisi, bilgisi eksik, yazma korumali,
  kaynaktan kucuk) listede gri ve nedeniyle durur, secilemez
  (`disksource.clone_target_problem`);
* silme onayi isaretlenmeden "Klonla" etkin olmaz;
* sistem diski secilirse disk adinin yazilmasi istenir;
* bagli bolumu olan disk icin uyari gosterilir.
Yeni dosya hedefi uygulamada acik bir dosya olamaz (kaynak sifirlanirdi).
"""
from __future__ import annotations

import os
import time
from typing import Callable, List, Optional

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox,
                             QFileDialog, QGridLayout, QGroupBox, QHBoxLayout,
                             QLabel, QLineEdit, QMessageBox, QProgressBar,
                             QPushButton, QTreeWidget, QTreeWidgetItem,
                             QVBoxLayout)

from ...core import clone as clone_mod
from ...core.disksource import DiskSource, clone_target_problem
from ...core.platform import image_location_problem
from ...core.ptable import FreeRegion, human_size
from ...core.session import DiskSession
from ...i18n import tr
from ..icons import icon as app_icon
from ..widgets.disk_map import DiskMapWidget
from ..widgets.power_options import PowerOptions, run_power_action
from .task import Estimator, clock_text

COLOR_WARN = "#c0392b"
COLOR_OK = "#1e8449"
NEW_FILE = "new"          # hedef listesinde "yeni goruntu dosyasi" satiri


class _Worker(QThread):
    """Klonu arka planda kosar; durdurma bir sonraki ilerleme bildiriminde."""

    progress = pyqtSignal(str, int)
    done = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, func: Callable):
        super().__init__()
        self.func = func
        self.stop_requested = False

    def run(self):
        def report(message: str, percent: int) -> None:
            if self.stop_requested:
                raise clone_mod.OperationCancelled()
            self.progress.emit(message, int(percent))

        try:
            self.done.emit(self.func(report))
        except clone_mod.OperationCancelled:
            self.cancelled.emit()
        except Exception as exc:                      # kullanici hatayi gormeli
            self.failed.emit(str(exc))


class _DiskPicker(QDialog):
    """Kaynak/hedef secimi: acik goruntuler, fiziksel diskler (ve hedefte
    yeni goruntu dosyasi). Uygun olmayan satir gri ve nedeniyle durur."""

    def __init__(self, parent, title: str, hint: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(700, 420)
        layout = QVBoxLayout(self)
        label = QLabel(hint)
        label.setWordWrap(True)
        layout.addWidget(label)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("Disk"), tr("Boyut"), tr("Durum")])
        self.tree.setRootIsDecorated(True)
        self.tree.setColumnWidth(0, 330)
        self.tree.setColumnWidth(1, 100)
        layout.addWidget(self.tree, 1)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Ok).setText(tr("Sec"))
        box.button(QDialogButtonBox.Cancel).setText(tr("Iptal"))
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        self.btn_ok = box.button(QDialogButtonBox.Ok)
        layout.addWidget(box)
        self.tree.currentItemChanged.connect(lambda *_: self._update_ok())
        self.tree.itemDoubleClicked.connect(
            lambda item, _c: self.accept() if self._value(item) is not None
            else None)
        self._update_ok()

    def add_group(self, title: str, rows) -> None:
        """rows: (deger, ad, boyut_metni, durum, engel, ikon)."""
        if not rows:
            return
        group = QTreeWidgetItem(self.tree, [title])
        group.setFlags(group.flags() & ~Qt.ItemIsSelectable)
        font = group.font(0)
        font.setBold(True)
        group.setFont(0, font)
        for value, name, size, state, problem, icon in rows:
            item = QTreeWidgetItem(group, [name, size, problem or state])
            item.setIcon(0, app_icon(icon))
            if problem:
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
                item.setToolTip(0, problem)
            else:
                item.setData(0, Qt.UserRole, value)
        group.setExpanded(True)

    def select(self, value) -> None:
        for i in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(i)
            for j in range(group.childCount()):
                if group.child(j).data(0, Qt.UserRole) == value:
                    self.tree.setCurrentItem(group.child(j))
                    return

    @staticmethod
    def _value(item):
        return item.data(0, Qt.UserRole) if item is not None else None

    def _update_ok(self) -> None:
        self.btn_ok.setEnabled(self._value(self.tree.currentItem()) is not None)

    def value(self):
        return self._value(self.tree.currentItem())


class CloneDialog(QDialog):
    """Kaynak diski hedef diske ya da yeni bir goruntu dosyasina klonlar."""

    def __init__(self, parent, images: List[DiskSource],
                 disks: List[DiskSource], source: Optional[DiskSource] = None,
                 pending_steps: int = 0, image_dir: str = "",
                 prepare: Optional[Callable[[], None]] = None):
        super().__init__(parent)
        self.setWindowTitle(tr("Diski klonla"))
        self.setModal(True)
        self.resize(820, 720)
        self.images = list(images)
        self.disks = list(disks)
        self.source: Optional[DiskSource] = source
        self.target = None              # DiskSource | NEW_FILE | None
        self.pending_steps = pending_steps
        self.image_dir = image_dir
        self.prepare = prepare
        self.before_power: Optional[Callable[[], None]] = None
        self.reload_needed = False
        self.opened_path = ""
        self._result_path = ""
        self._worker: Optional[_Worker] = None
        self._running = False
        self._estimator: Optional[Estimator] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # -- kaynak ----------------------------------------------------------
        src_box = QGroupBox(tr("Kaynak disk"))
        src_layout = QVBoxLayout(src_box)
        row = QHBoxLayout()
        self.btn_source = QPushButton(app_icon("disk"), tr("Kaynak disk sec..."))
        self.btn_source.clicked.connect(self._pick_source)
        self.source_label = QLabel("")
        self.source_label.setTextFormat(Qt.RichText)
        row.addWidget(self.btn_source)
        row.addWidget(self.source_label, 1)
        src_layout.addLayout(row)
        self.source_map = DiskMapWidget()
        src_layout.addWidget(self.source_map)
        root.addWidget(src_box)

        # -- hedef -----------------------------------------------------------
        dst_box = QGroupBox(tr("Hedef"))
        dst_layout = QVBoxLayout(dst_box)
        row = QHBoxLayout()
        self.btn_target = QPushButton(app_icon("disk"), tr("Hedef sec..."))
        self.btn_target.clicked.connect(self._pick_target)
        self.target_label = QLabel(tr("Hedef secilmedi"))
        self.target_label.setTextFormat(Qt.RichText)
        row.addWidget(self.btn_target)
        row.addWidget(self.target_label, 1)
        dst_layout.addLayout(row)
        self.target_map = DiskMapWidget()
        dst_layout.addWidget(self.target_map)
        path_row = QHBoxLayout()
        self.path_label = QLabel(tr("Dosya:"))
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText(tr("Yeni goruntu dosyasinin yolu"))
        self.path_edit.textChanged.connect(lambda *_: self._update())
        self.btn_path = QPushButton(tr("Sec..."))
        self.btn_path.clicked.connect(self._pick_path)
        path_row.addWidget(self.path_label)
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(self.btn_path)
        dst_layout.addLayout(path_row)
        root.addWidget(dst_box)

        # -- aciklama ve onaylar --------------------------------------------
        info = QLabel(tr("Kaynagin butun sektorleri (bolum tablosu, bolumler, "
                         "onyukleme alani) hedefe birebir kopyalanir. Kaynak "
                         "salt okunur kalir."))
        info.setWordWrap(True)
        root.addWidget(info)
        self.pending_note = QLabel(
            tr("Bekleyen {} adim klona DAHIL DEGIL: diskin su anki hali "
               "kopyalanir.", pending_steps) if pending_steps else "")
        self.pending_note.setWordWrap(True)
        self.pending_note.setVisible(bool(pending_steps))
        root.addWidget(self.pending_note)
        self.warn = QLabel("")
        self.warn.setWordWrap(True)
        self.warn.setTextFormat(Qt.RichText)
        root.addWidget(self.warn)
        confirm_grid = QGridLayout()
        self.name_label = QLabel("")
        self.name_label.setWordWrap(True)
        self.name_label.setTextFormat(Qt.RichText)
        self.name_edit = QLineEdit()
        self.name_edit.textChanged.connect(lambda *_: self._update())
        confirm_grid.addWidget(self.name_label, 0, 0)
        confirm_grid.addWidget(self.name_edit, 1, 0)
        self.confirm = QCheckBox(tr("Hedef diskteki BUTUN veriler silinecek; "
                                    "anladim"))
        self.confirm.stateChanged.connect(lambda *_: self._update())
        confirm_grid.addWidget(self.confirm, 2, 0)
        root.addLayout(confirm_grid)

        # -- ilerleme --------------------------------------------------------
        root.addStretch(1)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(34)
        root.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setAlignment(Qt.AlignCenter)
        self.bar.setMinimumHeight(24)
        root.addWidget(self.bar)
        self.time_label = QLabel("")
        self.time_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        root.addWidget(self.time_label)
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._tick)

        # -- dugmeler --------------------------------------------------------
        buttons = QHBoxLayout()
        self.power = PowerOptions(self, reason=tr("Disk klonlaniyor"))
        buttons.addWidget(self.power)
        buttons.addStretch(1)
        self.btn_open = QPushButton(tr("Klonu ac"))
        self.btn_open.setVisible(False)
        self.btn_open.clicked.connect(self._open_result)
        self.btn_start = QPushButton(app_icon("apply"), tr("Klonla"))
        self.btn_start.setDefault(True)
        self.btn_start.clicked.connect(self._start_or_stop)
        self.btn_close = QPushButton(tr("Kapat"))
        self.btn_close.clicked.connect(self.reject)
        for button in (self.btn_open, self.btn_start, self.btn_close):
            buttons.addWidget(button)
        root.addLayout(buttons)

        self._show_source()
        self._show_target()

    # ==================================================================
    # Secim
    # ==================================================================
    def _all_sources(self) -> List[DiskSource]:
        return self.images + self.disks

    @staticmethod
    def _source_problem(src: DiskSource) -> str:
        """Kaynak olarak okunabilir mi? (yetkisiz fiziksel disk okunamaz)"""
        if src.kind == "physical" and src.session is None and \
                not getattr(src.disk, "info_complete", True):
            return tr("disk bilgisi okunamadi (yetki yok)")
        return ""

    def target_problem(self, dst: DiskSource) -> str:
        """Bu satir hedef olabilir mi? Olamazsa nedeni."""
        src = self.source
        if src is None:
            return ""
        if dst == src or (dst.path and src.path and
                          os.path.normcase(dst.path) == os.path.normcase(src.path)):
            return tr("kaynak diskin kendisi")
        if dst.kind == "physical":
            return clone_target_problem(src.path, src.size, dst.disk)
        if dst.size < src.size:
            return tr("kaynaktan kucuk ({} < {})", human_size(dst.size),
                      human_size(src.size))
        if dst.session is not None and dst.session.readonly and \
                not dst.session.can_become_writable()[0]:
            return dst.session.can_become_writable()[1]
        return ""

    @staticmethod
    def _state_text(src: DiskSource) -> str:
        tags = []
        if src.is_open:
            tags.append(tr("uygulamada acik"))
        if src.is_system:
            tags.append(tr("SISTEM DISKI"))
        if getattr(src.disk, "mounted", None):
            tags.append(tr("bagli bolum var"))
        return ", ".join(tags)

    @staticmethod
    def _disk_text(src: DiskSource) -> str:
        if src.kind == "physical":
            model = getattr(src.disk, "model", "") or tr("bilinmeyen")
            return f"{getattr(src.disk, 'name', src.label)} — {model}"
        return src.label

    def _pick_source(self) -> None:
        picker = _DiskPicker(self, tr("Kaynak disk sec"),
                             tr("Klonlanacak diski secin. Kaynak salt okunur "
                                "acilir; uzerine hicbir sey yazilmaz."))
        for title, group, icon in ((tr("Acik goruntuler"), self.images, "image"),
                                   (tr("Fiziksel diskler"), self.disks, "disk")):
            picker.add_group(title, [
                (src, self._disk_text(src), human_size(src.size),
                 self._state_text(src), self._source_problem(src), icon)
                for src in group])
        if self.source is not None:
            picker.select(self.source)
        if picker.exec_() == QDialog.Accepted and picker.value() is not None:
            self.source = picker.value()
            if isinstance(self.target, DiskSource) and self.target_problem(self.target):
                self.target = None          # yeni kaynakla uyumsuz hedef duser
            self._show_source()
            self._show_target()
        picker.deleteLater()

    def _pick_target(self) -> None:
        picker = _DiskPicker(self, tr("Hedef sec"),
                             tr("Klonun yazilacagi yeri secin. Hedef diskteki "
                                "her sey silinir."))
        picker.add_group(tr("Yeni goruntu dosyasi"), [
            (NEW_FILE, tr("Yeni goruntu dosyasi (.img)"), "", "", "", "image")])
        picker.add_group(tr("Fiziksel diskler"), [
            (dst, self._disk_text(dst), human_size(dst.size),
             self._state_text(dst), self.target_problem(dst), "disk")
            for dst in self.disks])
        picker.add_group(tr("Acik goruntuler"), [
            (dst, self._disk_text(dst), human_size(dst.size),
             self._state_text(dst), self.target_problem(dst), "image")
            for dst in self.images])
        if self.target is not None:
            picker.select(self.target)
        if picker.exec_() == QDialog.Accepted and picker.value() is not None:
            self.target = picker.value()
            self.confirm.setChecked(False)          # onay hedefe ozgu
            self.name_edit.clear()
            if self.target == NEW_FILE and not self.path_edit.text().strip():
                self.path_edit.setText(self._suggested_path())
            self._show_target()
        picker.deleteLater()

    def _suggested_path(self) -> str:
        base = os.path.splitext(self.source.label if self.source else "disk")[0]
        base = base.replace(os.sep, "_").replace(":", "")
        return os.path.join(self.image_dir or os.getcwd(), f"{base}-klon.img")

    def _pick_path(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, tr("Klon hedefi"), self.path_edit.text() or self._suggested_path(),
            tr("Disk goruntusu (*.img)"))
        if path:
            if not os.path.splitext(path)[1]:
                path += ".img"
            self.path_edit.setText(path)

    # ==================================================================
    # Gosterim
    # ==================================================================
    @staticmethod
    def _show_map(widget: DiskMapWidget, src: Optional[DiskSource],
                  caption: str) -> None:
        widget.clear()
        if src is None:
            return
        sector = 512
        total = max(1, src.size // sector)
        parts = list(src.partitions)
        # Bolumsuz disk: harita "acik degil" yazmasin, tek bos alan gostersin.
        free = [] if parts else [FreeRegion(0, total, sector)]
        widget.set_disk(caption, total, parts, free)

    def _show_source(self) -> None:
        src = self.source
        if src is None:
            self.source_label.setText(tr("Kaynak secilmedi"))
        else:
            self.source_label.setText(
                f"<b>{self._disk_text(src)}</b> — {human_size(src.size)}")
        self._show_map(self.source_map, src,
                       f"{src.label} — {human_size(src.size)}" if src else "")
        self._update()

    def _show_target(self) -> None:
        dst = self.target
        new_file = dst == NEW_FILE
        for widget in (self.path_label, self.path_edit, self.btn_path):
            widget.setVisible(new_file)
        self.target_map.setVisible(dst is not None and not new_file)
        if dst is None:
            self.target_label.setText(tr("Hedef secilmedi"))
            self.target_map.clear()
        elif new_file:
            self.target_label.setText(tr("<b>Yeni goruntu dosyasi</b> — "
                                         "hicbir diske dokunulmaz"))
        else:
            self.target_label.setText(
                f"<b>{self._disk_text(dst)}</b> — {human_size(dst.size)}")
            self._show_map(self.target_map, dst,
                           tr("{} — su anki icerik (klonla silinecek)",
                              dst.label))
        self._update()

    # ==================================================================
    # Dogrulama
    # ==================================================================
    def _open_paths(self) -> List[str]:
        return [s.path for s in self.images if s.path]

    def problem(self) -> str:
        """Klonla dugmesini kapatan neden (bos = hazir)."""
        src, dst = self.source, self.target
        if src is None:
            return tr("Kaynak diski secin.")
        if self._source_problem(src):
            return self._source_problem(src)
        if dst is None:
            return tr("Hedefi secin.")
        if dst == NEW_FILE:
            path = self.path_edit.text().strip()
            if not path:
                return tr("Yeni goruntu dosyasinin yolunu secin.")
            folder = os.path.dirname(os.path.abspath(path))
            if not os.path.isdir(folder):
                return tr("Klasor bulunamadi: {}", folder)
            for other in self._open_paths():
                if os.path.normcase(os.path.abspath(other)) == \
                        os.path.normcase(os.path.abspath(path)):
                    return tr("Bu dosya uygulamada acik; baska bir ad secin.")
            blocker, _warning = image_location_problem(folder, src.size)
            return blocker
        problem = self.target_problem(dst)
        if problem:
            return problem
        if dst.is_system and self.name_edit.text().strip() != \
                getattr(dst.disk, "name", ""):
            return tr("Sistem diski: onaylamak icin disk adini yazin.")
        if not self.confirm.isChecked():
            return tr("Silme onayini isaretleyin.")
        return ""

    def _update(self) -> None:
        src, dst = self.source, self.target
        disk_target = isinstance(dst, DiskSource)
        system = disk_target and dst.is_system
        self.name_label.setVisible(bool(system))
        self.name_edit.setVisible(bool(system))
        self.confirm.setVisible(disk_target)
        if system:
            self.name_label.setText(
                tr("<b>{}</b> isletim sistemi diskidir. Uzerine yazmak sistemi "
                   "acilamaz hale getirir. Onaylamak icin disk adini yazin: "
                   "<b>{}</b>", dst.disk.path, dst.disk.name))
        notes = []
        if disk_target and src is not None:
            notes.append(tr("<b>{}</b> uzerindeki bolum tablosu ve butun "
                            "bolumler kaybolacak.", dst.label))
            if dst.size > src.size:
                # Kopya kaynak boyu kadardir; kalan ayrilmamis alan olur ama
                # eski veri orada fiziksel olarak durur — dogruyu soyle.
                notes.append(tr("Hedef kaynaktan {} buyuk: bu kisim ayrilmamis "
                                "alan olur, eski veri fiziksel olarak orada "
                                "kalir (tamamen yok etmek icin Guvenli silme).",
                                human_size(dst.size - src.size)))
            mounted = list(getattr(dst.disk, "mounted", []) or [])
            if mounted:
                notes.append(tr("Bu diskte bagli bolumler var: {} — yazmadan "
                                "once cikarmaniz onerilir.", ", ".join(mounted)))
        elif dst == NEW_FILE and src is not None:
            path = self.path_edit.text().strip()
            just_made = bool(self._result_path) and path and \
                os.path.abspath(path) == os.path.abspath(self._result_path)
            if path and os.path.exists(path) and not just_made:
                notes.append(tr("{} zaten var; uzerine yazilacak.",
                                os.path.basename(path)))
            if path:
                _blocker, warning = image_location_problem(
                    os.path.dirname(os.path.abspath(path)), src.size)
                if warning:
                    notes.append(warning)
        self.warn.setText("<br>".join(
            f"<span style='color:{COLOR_WARN}'>{n}</span>" for n in notes))
        if self._running:
            return
        problem = self.problem()
        self.btn_start.setEnabled(not problem)
        self.btn_start.setToolTip(problem)
        if not self.status.property("result"):
            self.status.setStyleSheet("")
            self.status.setText(problem)

    # ==================================================================
    # Calistirma
    # ==================================================================
    def _lock(self, locked: bool) -> None:
        for widget in (self.btn_source, self.btn_target, self.path_edit,
                       self.btn_path, self.name_edit, self.confirm):
            widget.setEnabled(not locked)

    def _start_or_stop(self) -> None:
        if self._running:
            self._request_stop()
        else:
            self._start()

    def _start(self) -> None:
        if self.problem():
            return
        src, dst = self.source, self.target
        dest_path = ""
        target = None
        if dst == NEW_FILE:
            dest_path = os.path.abspath(self.path_edit.text().strip())
        else:
            target = dst
        allow_system = bool(target is not None and target.is_system)
        if self.prepare is not None:
            self.prepare()          # arka plan disk taramasi bitsin (ADR 0021)

        def task(report):
            return DiskSession.clone_between(src, target, dest_path=dest_path,
                                             allow_system=allow_system,
                                             progress=report)

        self._running = True
        self.status.setProperty("result", False)
        self.status.setStyleSheet("")
        self.status.setText(tr("Klonlama baslatiliyor..."))
        self.btn_open.setVisible(False)
        self._result_path = ""
        self._lock(True)
        self.btn_close.setEnabled(False)
        self.btn_start.setText(tr("Durdur"))
        self.btn_start.setIcon(app_icon("stop"))
        self.btn_start.setEnabled(True)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self._estimator = Estimator()
        self._tick()
        self._clock.start()
        self.power.begin()
        worker = _Worker(task)
        self._worker = worker
        worker.progress.connect(self._on_progress)
        worker.done.connect(lambda value: self._finish(value, None))
        worker.failed.connect(lambda message: self._finish(None, message))
        worker.cancelled.connect(lambda: self._finish(None, None, cancelled=True))
        worker.start()

    def _request_stop(self) -> None:
        worker = self._worker
        if worker is None or not worker.isRunning() or worker.stop_requested:
            return
        if isinstance(self.target, DiskSource):
            answer = QMessageBox.question(
                self, tr("Islemi durdur"),
                tr("Klon yarida kesilirse hedef disk tutarsiz kalir ve yeniden "
                   "klonlanana ya da bicimlendirilene kadar kullanilamaz. Yine "
                   "de durdurulsun mu?"),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes or not worker.isRunning():
                return
        worker.stop_requested = True
        self.btn_start.setText(tr("Durduruluyor..."))
        self.btn_start.setEnabled(False)

    def _on_progress(self, message: str, percent: int) -> None:
        if self._worker is not None and self._worker.stop_requested:
            return                       # "Durduruluyor..." ezilmesin
        self.status.setText(message)
        if self._estimator is not None:
            self._estimator.update(percent)
        if percent < 0:
            if self.bar.maximum() != 0:
                self.bar.setRange(0, 0)
            return
        if self.bar.maximum() == 0:
            self.bar.setRange(0, 100)
        self.bar.setValue(max(0, min(100, percent)))
        self._tick()

    def _tick(self) -> None:
        if self._estimator is not None:
            self.time_label.setText(self._estimator.text())

    def _finish(self, value, error: Optional[str],
                cancelled: bool = False) -> None:
        success = not error and not cancelled
        power_after = self.power.end(success)
        self._running = False
        self._clock.stop()
        if self._estimator is not None:
            # Bitti: "kalan sure" anlamsiz; toplam sure yazilir.
            self.time_label.setText(tr("Sure: {}", clock_text(
                time.monotonic() - self._estimator.start)))
        self._lock(False)
        self.btn_close.setEnabled(True)
        self.btn_start.setText(tr("Klonla"))
        self.btn_start.setIcon(app_icon("apply"))
        disk_target = isinstance(self.target, DiskSource)
        if disk_target:
            self.reload_needed = True        # hedefin tablosu degisti (ya da yarim)
        self.status.setProperty("result", True)
        if self.bar.maximum() == 0:
            self.bar.setRange(0, 100)
        if cancelled:
            self.bar.setValue(0)
            self.status.setStyleSheet(f"color: {COLOR_WARN}")
            self.status.setText(
                tr("Klonlama durduruldu. Hedef tutarsiz durumda: yeniden "
                   "klonlayin ya da bicimlendirin.") if disk_target else
                tr("Klonlama durduruldu; yarim kalan goruntu dosyasi silindi."))
        elif error:
            self.bar.setValue(0)
            self.status.setStyleSheet(f"color: {COLOR_WARN}")
            self.status.setText(tr("Basarisiz: {}", error))
        else:
            self.bar.setValue(100)
            self.status.setStyleSheet(f"color: {COLOR_OK}")
            if disk_target:
                self.status.setText(tr(
                    "{} diskine klonlandi ({}). Iki disk ayni bilgisayarda "
                    "takili kalirsa ayni disk kimligini tasidiklari icin "
                    "isletim sistemi birini cevrimdisi yapabilir.",
                    self.target.label, human_size(int(value or 0))))
            else:
                self._result_path = str(value)
                self.status.setText(tr("Klon olusturuldu: {}", value))
                self.btn_open.setVisible(True)
        # Onay bir kez kullanilir: ikinci klon icin yeniden isaretlenmeli.
        self.confirm.setChecked(False)
        self._update()
        if power_after:
            QTimer.singleShot(0, lambda: run_power_action(
                self, power_after, prepare=self.before_power))

    def _open_result(self) -> None:
        self.opened_path = self._result_path
        self.accept()

    # ==================================================================
    def closeEvent(self, event):
        """Calisirken pencere kapatilamaz: once Durdur."""
        if self._running:
            event.ignore()
            return
        super().closeEvent(event)

    def reject(self) -> None:
        if self._running:
            return
        super().reject()

    def exec_(self) -> int:
        outcome = super().exec_()
        if self._worker is not None:
            self._worker.wait()
        return outcome
