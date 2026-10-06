"""Yedekleme ve geri yukleme — **tek pencere**.

## Neden tek pencere

Onceki akis is basina dagilmisti: menuden bir eylem secilir, bir dosya secme
penceresi acilir, ardindan "nereye yazilsin?" diye bir soru kutusu, sonra
hedefi soran baska bir kutu, arada onay kutulari, en sonunda ayri bir ilerleme
penceresi ve bitince bir bilgi penceresi daha. Kullanici neyi nereye
yazdigini tek bir yerde **goremiyordu**; yedek dosyasinin bilgisi de ayri bir
pencereydi.

Bu pencere hepsini birlestirir ve DiskGenius'un "Backup Disk to Image File" /
"Restore Disk From Image File" duzenini izler:

  * **Ustte yedek dosyasi**: yolu, bilgileri (boyut, tarih, sikistirma,
    aciklama) ve icerigi (bolumler, kok klasor girisleri).
  * **Not yalnizca yedek alinirken yazilir.** Geri yuklemede yedegin notu
    bilgi alaninda salt okunur gorunur; DiskGenius'ta da "Aciklama" yedek
    alma penceresindedir. (Onceki surum geri yuklemede notu duzenletiyor ve
    "Notu kaydet" ile yedek dosyasina yaziyordu — geri yukleyen kullanici
    yedegi degistirmemeli.)
  * **Altta hedef/kaynak**: acik goruntuler ve fiziksel diskler tek agacta,
    secilenin bolum haritasi ustunde cizili.
  * **Geri yuklemede harita yedegin hedefteki yerlesimini** gosterir ve
    "Bolumleri yonet..." ile bolumler hedef diske gore buyutulup
    kucultulebilir (`core.restoreplan`). Yeni goruntu dosyasina geri
    yuklerken goruntunun boyutu da secilir.
  * En altta durum satiri ve ilerleme cubugu — islem pencereyi terk etmeden
    izlenir.

## Guvenlik

Yikici olan yalnizca **geri yukleme**dir ve onayi bu pencerenin **icinde**
alinir (ADR 0032):

  * "Hedefteki butun veriler silinecek" kutusu isaretlenmeden Baslat etkin
    olmaz.
  * Hedef sistem diski ise ayrica disk adinin yazilmasi istenir — CLAUDE.md'nin
    "kullanici disk adini yazarak dogrular" kurali, ayri bir pencerede degil
    burada karsilanir.
  * Bagli bolum varsa uyari satir olarak gorunur.

Yedek **almak** yikici degildir: kaynak salt okunur acilir, hicbir sektor
yazilmaz. Bu yuzden yedek alirken onay kutusu istenmez.
"""
from __future__ import annotations

import os
import time
from typing import Callable, List, Optional

from PyQt5.QtCore import QModelIndex, QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog,
                             QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                             QGroupBox, QHBoxLayout, QHeaderView,
                             QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                             QProgressBar,
                             QPushButton, QRadioButton, QSplitter, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout, QWidget)

from ...core.fsregistry import fs_display
from ...core import clone as clone_mod
from ...core import disksource
from ...core.ptable import human_size
from ...core.session import DiskSession
from ..icons import icon as app_icon
from ..theme import fs_color
from ..widgets.disk_map import DiskMapWidget
from ..widgets.layout_bar import LayoutBar
from ..widgets.partition_table import color_chip
from .restore_layout import RestoreLayoutDialog
from ...i18n import tr

MODE_BACKUP = "backup"
MODE_RESTORE = "restore"

COLOR_WARN = "#c0392b"
COLOR_OK = "#1e8449"
MIB = 1024 ** 2
GIB = 1024 ** 3
TIB = 1024 ** 4
# Yeni goruntu boyutu birimleri: (ad, bayt, en kucuk deger, en buyuk deger)
SIZE_UNITS = (("MB", MIB, 1.0, 64.0 * 1024 * 1024),
              ("GB", GIB, 0.01, 64.0 * 1024),
              ("TB", TIB, 0.01, 64.0))

# Arayuzdeki sikistirma secenekleri -> zlib duzeyi
LEVELS = (
    ("none", clone_mod.LEVEL_NONE),
    ("fast", clone_mod.LEVEL_FAST),
    ("normal", clone_mod.LEVEL_NORMAL),
    ("high", clone_mod.LEVEL_HIGH),
)


class _Worker(QThread):
    """Uzun isi arka planda kosar (CLAUDE.md: arayuz is parcaciginda durmaz)."""

    progress = pyqtSignal(str, int)
    done = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, func: Callable):
        super().__init__()
        self.func = func
        self.stop_requested = False

    def request_stop(self) -> None:
        """Durdurma istegi: bir sonraki ilerleme bildiriminde is kesilir.

        Cekirdekteki uzun isler ilerlemeyi duzenli bildirir (yedekte her
        8 MB'ta); `OperationCancelled` o noktadan firlar ve `with`/`finally`
        bloklari aygiti ve dosyalari kapatir.
        """
        self.stop_requested = True

    def run(self):
        def report(message: str, percent: int) -> None:
            if self.stop_requested:
                raise clone_mod.OperationCancelled()
            self.progress.emit(message, percent)

        try:
            self.done.emit(self.func(report))
        except clone_mod.OperationCancelled:
            self.cancelled.emit()
        except Exception as exc:                      # kullanici hatayi gormeli
            self.failed.emit(str(exc))


class _Target:
    """Yedeklenecek kaynak ya da geri yuklenecek hedef.

    Tek bir soyutlama: acik goruntu, o goruntunun bir bolumu, fiziksel disk ve
    "yeni goruntu dosyasi" ayni listede durur. Boylece pencere "once neyi
    sectin?" diye ayri bir soru sormaz.
    """

    def __init__(self, kind: str, label: str, size: int = 0, session=None,
                 index: int = -1, disk=None, partitions=None, scheme: str = ""):
        self.kind = kind          # 'image' | 'partition' | 'physical' | 'new'
        self.label = label
        self.size = size
        self.session = session
        self.index = index
        self.disk = disk
        self.partitions = partitions or []
        self.scheme = scheme

    @property
    def is_system_disk(self) -> bool:
        return bool(self.disk is not None and getattr(self.disk, "is_system", False))

    @property
    def mounted(self) -> List[str]:
        return list(getattr(self.disk, "mounted", []) or [])

    @property
    def disk_name(self) -> str:
        return getattr(self.disk, "name", "")


class _TargetPicker(QDialog):
    """Hedef/kaynak secim penceresi (DiskGenius "Disk Sec").

    Disk agaci ana formda cok az yer kapliyordu: uc satir gorunuyor, disk
    secmek icin kaydirmak gerekiyordu. Agac artik bu pencerede genis durur;
    ana formda yalnizca secili hedefin tek satirlik ozeti ve bir dugme
    kalir. Agac **ayni nesnedir** (`BackupDialog.tree`); secim sinyalleri ve
    `current_target()` degismeden calisir.
    """

    def __init__(self, parent, tree: QTreeWidget):
        super().__init__(parent)
        self.tree = tree
        self.resize(760, 480)
        layout = QVBoxLayout(self)
        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        layout.addWidget(tree, 1)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        self.btn_ok = box.button(QDialogButtonBox.Ok)
        layout.addWidget(box)
        tree.currentItemChanged.connect(lambda *_: self._update_ok())
        tree.itemDoubleClicked.connect(self._on_double_click)

    def _selectable(self, item) -> bool:
        return item is not None and item.data(0, Qt.UserRole) is not None \
            and not item.isHidden()

    def _update_ok(self) -> None:
        self.btn_ok.setEnabled(self._selectable(self.tree.currentItem()))

    def _on_double_click(self, item, _column) -> None:
        if self._selectable(item):
            self.accept()

    def pick(self, title: str, hint: str) -> bool:
        """Pencereyi acar; iptalde onceki secim geri gelir."""
        self.setWindowTitle(title)
        self.hint.setText(hint)
        previous = self.tree.currentItem()
        self._update_ok()
        self.tree.setFocus()
        if self.exec_() == QDialog.Accepted:
            return True
        if previous is not None and previous is not self.tree.currentItem():
            self.tree.setCurrentItem(previous)
        return False


class BackupDialog(QDialog):
    """Yedek al / geri yukle penceresi (tek form).

    `mode` baslangic kipidir; kullanici pencere icinde degistirebilir. Disk
    listesi disaridan verilir (ana pencerenin arka planda topladigi liste),
    boylece bu pencere hicbir aygita dokunmaz.
    """

    def __init__(self, parent, mode: str = MODE_BACKUP, sessions=None,
                 disks=None, surveys=None, session=None,
                 partition_index: int = -1, backup_path: str = "",
                 disk_path: str = ""):
        super().__init__(parent)
        self.sessions = list(sessions or [])
        self.disks = list(disks or [])
        self.surveys = dict(surveys or {})
        self.targets: List[_Target] = []
        self.preview = None
        self.info = None
        # Geri yukleme yerlesimi: `base_layout` yedektekidir (onizlemeden),
        # `plan` secili hedefe uydurulmus ve kullanicinin duzenledigi kopya.
        self.base_layout = None
        self.plan = None
        self._plan_key = None
        self.result_value = None
        self.opened_path = ""          # islem sonunda acilacak yol (varsa)
        self.reload_needed = False
        self._worker: Optional[_Worker] = None
        self._running = False
        self._result_shown = False   # durum satirinda islem sonucu duruyor mu
        # Kip **once** belirlenir: hedef agaci doldurulurken secim sinyali
        # tetiklenir ve o sirada `self.mode` okunur. Sonradan atandiginda ilk
        # secim sessizce dusuyordu (Qt yuvadaki istisnayi yutar).
        self.mode = mode
        self._run_mode = mode        # calisan isin kipi (sonuc metni icin)

        self.setWindowTitle(tr("Yedekleme ve Geri Yukleme"))
        self.setModal(True)
        self.resize(940, 800)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)
        root.addLayout(self._build_mode_row())

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self._build_file_group())
        splitter.addWidget(self._build_target_group())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 4)
        splitter.setSizes([420, 220])
        root.addWidget(splitter, 1)

        root.addWidget(self._build_options_group())
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(34)     # metin bir satirdan ikiye cikinca
        self.status.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        root.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setAlignment(Qt.AlignCenter)
        self.bar.setMinimumHeight(24)        # yuzde metni cubuga sigsin
        root.addWidget(self.bar)
        # Gecen / kalan sure: is surerken saniyede bir tazelenir (ilerleme
        # gelmese de saat ilerler; kullanici isin durmadigini gorur).
        self.time_label = QLabel("")
        self.time_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        root.addWidget(self.time_label)
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._update_clock)
        self._started_at = 0.0
        self._eta_base = None        # (zaman, yuzde): kalan sure hesabinin basi
        self._eta_last = None        # (zaman, yuzde): son ilerleme
        root.addSpacing(4)
        root.addLayout(self._build_buttons())

        self._fill_targets(session, partition_index, disk_path)
        self.set_mode(mode)
        if backup_path:
            self._set_path(backup_path)

    # ==================================================================
    # Kurulum
    # ==================================================================
    def _build_mode_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("Islem:")))
        self.rb_backup = QRadioButton(tr("Yedek al"))
        self.rb_restore = QRadioButton(tr("Geri yukle"))
        self.rb_backup.toggled.connect(
            lambda on: self.set_mode(MODE_BACKUP if on else MODE_RESTORE))
        row.addWidget(self.rb_backup)
        row.addWidget(self.rb_restore)
        row.addStretch(1)
        self.mode_note = QLabel("")
        self.mode_note.setEnabled(False)
        row.addWidget(self.mode_note)
        return row

    def _build_file_group(self) -> QWidget:
        group = QGroupBox(tr("Yedek dosyasi (.dub)"))
        layout = QVBoxLayout(group)

        row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText(tr("Yedek dosyasi secilmedi"))
        self.path_edit.textChanged.connect(self._on_path_typed)
        self.btn_pick = QPushButton(app_icon("open"), tr("Sec..."))
        self.btn_pick.clicked.connect(self._pick_file)
        row.addWidget(self.path_edit, 1)
        row.addWidget(self.btn_pick)
        layout.addLayout(row)

        body = QHBoxLayout()
        info_box = QWidget()
        self.info_form = QFormLayout(info_box)
        self.info_form.setContentsMargins(0, 0, 0, 0)
        self.info_labels = {}
        for key in ("size", "file", "created", "compress", "scope", "fs",
                    "remark"):
            value = QLabel("-")
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.info_labels[key] = value
        self.info_labels["remark"].setWordWrap(True)
        self.info_labels["remark"].setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.info_form.addRow(tr("Kaynak boyut:"), self.info_labels["size"])
        self.info_form.addRow(tr("Yedek boyut:"), self.info_labels["file"])
        self.info_form.addRow(tr("Olusturma:"), self.info_labels["created"])
        self.info_form.addRow(tr("Sikistirma:"), self.info_labels["compress"])
        self.info_form.addRow(tr("Kapsam:"), self.info_labels["scope"])
        self.info_form.addRow(tr("Dosya sistemi:"), self.info_labels["fs"])
        # Geri yuklemede notun yeri: salt okunur (DiskGenius "Aciklama")
        self.remark_caption = QLabel(tr("Aciklama:"))
        self.info_form.addRow(self.remark_caption, self.info_labels["remark"])
        body.addWidget(info_box, 1)

        content_box = QWidget()
        content_layout = QVBoxLayout(content_box)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.addWidget(QLabel(tr("Icerik:")))
        self.content = QTreeWidget()
        self.content.setHeaderLabels(
            [tr("Ad"), tr("Dosya Sistemi"), tr("Etiket"), tr("Boyut")])
        self.content.setRootIsDecorated(True)
        self.content.header().setSectionResizeMode(0, QHeaderView.Stretch)
        for col, width in ((1, 100), (2, 120), (3, 90)):
            self.content.setColumnWidth(col, width)
        content_layout.addWidget(self.content, 1)
        body.addWidget(content_box, 2)
        layout.addLayout(body, 1)

        # Not yalnizca yedek alinirken yazilir (bkz. modul aciklamasi)
        self.note_box = QWidget()
        note_row = QHBoxLayout(self.note_box)
        note_row.setContentsMargins(0, 0, 0, 0)
        note_row.addWidget(QLabel(tr("Not:")))
        self.remark = QPlainTextEdit()
        self.remark.setFixedHeight(54)
        self.remark.setPlaceholderText(
            tr("Bu yedegin ne oldugunu yazin — dosyanin icinde saklanir"))
        self.remark.textChanged.connect(self._on_remark_changed)
        note_row.addWidget(self.remark, 1)
        side = QVBoxLayout()
        self.remark_count = QLabel("")
        self.remark_count.setEnabled(False)
        side.addWidget(self.remark_count)
        side.addStretch(1)
        note_row.addLayout(side)
        layout.addWidget(self.note_box)
        return group

    def _build_target_group(self) -> QWidget:
        group = QGroupBox(tr("Hedef Disk / Bolum"))
        self.target_group = group
        layout = QVBoxLayout(group)

        # Secili hedefin ozeti + secim dugmesi (agac ayri pencerede)
        pick_row = QHBoxLayout()
        self.target_caption = QLabel("")
        pick_row.addWidget(self.target_caption)
        self.target_label = QLabel("-")
        self.target_label.setTextFormat(Qt.RichText)
        self.target_label.setWordWrap(True)
        pick_row.addWidget(self.target_label, 1)
        self.btn_pick_target = QPushButton(app_icon("disk"), tr("Disk sec..."))
        self.btn_pick_target.clicked.connect(self._pick_target)
        pick_row.addWidget(self.btn_pick_target)
        layout.addLayout(pick_row)

        self.map = DiskMapWidget()
        self.map.setMinimumHeight(110)
        self.map.partitionActivated.connect(lambda _index: self._manage_layout())
        layout.addWidget(self.map)
        # Geri yuklemede harita yerine surukleme tutamakli serit (DiskGenius)
        self.layout_bar = LayoutBar()
        self.layout_bar.hide()
        self.layout_bar.layoutChanged.connect(self._on_layout_dragged)
        self.layout_bar.partitionActivated.connect(
            lambda _index: self._manage_layout())
        layout.addWidget(self.layout_bar)

        # Geri yuklemede: yerlesim ozeti, yeni goruntu boyutu, bolum duzenleme
        self.layout_row = QWidget()
        row = QHBoxLayout(self.layout_row)
        row.setContentsMargins(0, 0, 0, 0)
        self.layout_label = QLabel("")
        self.layout_label.setWordWrap(True)
        row.addWidget(self.layout_label, 1)
        self.size_caption = QLabel(tr("Goruntu boyutu:"))
        row.addWidget(self.size_caption)
        self.size_spin = QDoubleSpinBox()
        self.size_spin.setDecimals(2)
        self.size_spin.setKeyboardTracking(False)
        self.size_spin.setToolTip(
            tr("Olusturulacak goruntu dosyasinin boyutu; bolumler bu boyuta "
               "gore yerlestirilir"))
        self.size_spin.valueChanged.connect(lambda *_: self._on_target_changed())
        row.addWidget(self.size_spin)
        self.size_unit = QComboBox()
        for name, factor, _low, _high in SIZE_UNITS:
            self.size_unit.addItem(name, factor)
        self._unit_index = 1
        self.size_unit.setCurrentIndex(self._unit_index)
        self._apply_unit_range()
        self.size_unit.currentIndexChanged.connect(self._on_unit_changed)
        row.addWidget(self.size_unit)
        self.btn_layout = QPushButton(app_icon("resize"), tr("Bolumleri yonet..."))
        self.btn_layout.setToolTip(
            tr("Yedekteki bolumleri hedef diske gore buyutun, kucultun ya da "
               "tasiyin"))
        self.btn_layout.clicked.connect(self._manage_layout)
        row.addWidget(self.btn_layout)
        layout.addWidget(self.layout_row)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("Hedef"), tr("Boyut"), tr("Durum")])
        self.tree.setIconSize(QSize(18, 18))
        self.tree.setRootIsDecorated(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.setColumnWidth(1, 110)
        self.tree.setColumnWidth(2, 220)
        self.tree.currentItemChanged.connect(lambda *_: self._on_target_changed())
        self.picker = _TargetPicker(self, self.tree)
        return group

    def _build_options_group(self) -> QWidget:
        group = QGroupBox(tr("Secenekler"))
        self.options_group = group
        layout = QVBoxLayout(group)

        self.compress_row = QWidget()
        row = QHBoxLayout(self.compress_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel(tr("Sikistirma:")))
        self.level_group = QButtonGroup(self)
        self.level_buttons = {}
        for key, text in (("none", tr("Yok")), ("fast", tr("Hizli")),
                          ("normal", tr("Normal")), ("high", tr("Yuksek"))):
            button = QRadioButton(text)
            self.level_group.addButton(button)
            self.level_buttons[key] = button
            row.addWidget(button)
        self.level_buttons["normal"].setChecked(True)
        row.addStretch(1)
        layout.addWidget(self.compress_row)

        # DiskGenius gibi: dosya sisteminin bos alani okunmaz (ADR 0092).
        # 64 GB'lik kartta 500 MB veri varsa yedek ~500 MB okur.
        self.used_only = QCheckBox(
            tr("Yalnizca kullanilan alani yedekle (hizli)"))
        self.used_only.setChecked(True)
        self.used_only.setToolTip(tr(
            "FAT, exFAT, ext2/3/4 ve NTFS bolumlerinde yalnizca dolu kumeler "
            "okunur; bos alan ve bolumlenmemis buyuk alan atlanir. Taninmayan "
            "dosya sistemleri yine tumuyle yedeklenir. Silinmis dosyalari "
            "yedekten kurtarmak icin bu secenegi kapatin (tum sektorler)."))
        self.used_only.toggled.connect(lambda *_: self._update_info())
        layout.addWidget(self.used_only)

        self.confirm = QCheckBox(
            tr("Hedefteki butun veriler silinecek; bunu anliyorum"))
        self.confirm.stateChanged.connect(lambda *_: self._update_buttons())
        layout.addWidget(self.confirm)

        name_row = QHBoxLayout()
        self.name_label = QLabel("")
        self.name_label.setWordWrap(True)
        name_row.addWidget(self.name_label)
        self.name_edit = QLineEdit()
        self.name_edit.setMaximumWidth(220)
        self.name_edit.textChanged.connect(lambda *_: self._update_buttons())
        name_row.addWidget(self.name_edit)
        name_row.addStretch(1)
        layout.addLayout(name_row)

        self.warn = QLabel("")
        self.warn.setWordWrap(True)
        self.warn.setTextFormat(Qt.RichText)
        layout.addWidget(self.warn)
        return group

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_start = QPushButton(app_icon("apply"), tr("Baslat"))
        self.btn_start.setDefault(True)
        self.btn_start.clicked.connect(self._start)
        self.btn_close = QPushButton(tr("Kapat"))
        self.btn_close.clicked.connect(self.reject)
        row.addWidget(self.btn_start)
        row.addWidget(self.btn_close)
        return row

    # ==================================================================
    # Hedef listesi
    # ==================================================================
    def _fill_targets(self, session, partition_index: int,
                      disk_path: str) -> None:
        """Acik goruntuleri ve fiziksel diskleri tek agaca doldurur.

        **Her fiziksel disk bir kez gorunur**, "Fiziksel diskler" altinda.
        Uygulamada acik olan fiziksel disk eskiden "Acik goruntuler" altinda
        da listeleniyordu: ayni disk iki yerde gorunuyor (kullanici
        bildirimi, 2026-09-28) ve "Fiziksel diskler" satiri secilirse geri
        yukleme ayni aygita **ikinci bir tutamac** aciyordu — ADR 0021'in
        yasakladigi sey. Artik acik diskin satiri o oturuma baglanir; okuma
        ve yazma oturumun kendi tutamacindan yapilir.
        """
        self.tree.clear()
        self.targets = []
        selected_item = None

        # Liste kurali ortak modelde (core/disksource, ADR 0049): goruntuler
        # ayri, her fiziksel disk bir kez, acik disk oturuma bagli.
        image_sources, physical_sources = disksource.collect(
            self.sessions, self.disks, self.surveys)
        images = [src.session for src in image_sources]

        def add_partitions(node, item_session):
            nonlocal selected_item
            for part in item_session.partitions:
                part_target = _Target(
                    "partition", tr("Bolum {}", part.index), part.size,
                    session=item_session, index=part.index,
                    partitions=[part], scheme=item_session.scheme,
                    disk=item_session.disk_info)
                child = self._add_row(node, part_target,
                                      color_chip(fs_color(part.fs_type)),
                                      extra=fs_display(part.fs_type) or "-")
                if item_session is session and part.index == partition_index:
                    selected_item = child

        if images:
            root = QTreeWidgetItem(self.tree, [tr("Acik goruntuler"), "", ""])
            root.setFirstColumnSpanned(True)
            root.setExpanded(True)
            for item_session in images:
                target = _Target("image", item_session.name,
                                 item_session.image.size, session=item_session,
                                 partitions=list(item_session.partitions),
                                 scheme=item_session.scheme)
                node = self._add_row(root, target, app_icon("image"))
                if item_session is session and partition_index < 0:
                    selected_item = node
                add_partitions(node, item_session)
                node.setExpanded(True)

        if physical_sources:
            root = QTreeWidgetItem(self.tree, [tr("Fiziksel diskler"), "", ""])
            root.setFirstColumnSpanned(True)
            root.setExpanded(True)
            for src in physical_sources:
                disk, item_session = src.disk, src.session
                parts, scheme, size = src.partitions, src.scheme, src.size
                target = _Target("physical", src.label, size,
                                 disk=disk, partitions=parts, scheme=scheme,
                                 session=item_session)
                state = self._disk_state(disk)
                if item_session is not None:
                    state = ", ".join(x for x in (tr("uygulamada acik"), state)
                                      if x)
                node = self._add_row(
                    root, target,
                    app_icon("disk-system" if disk.is_system else "disk"),
                    extra=state)
                if disk.path == disk_path or (item_session is session
                                              and item_session is not None
                                              and partition_index < 0):
                    selected_item = node
                if item_session is not None:
                    add_partitions(node, item_session)
                else:
                    for part in parts:
                        QTreeWidgetItem(node, [tr("Bolum {} — {}", part.index,
                                                  fs_display(part.fs_type) or tr("ham")),
                                               human_size(part.size), ""])
                node.setExpanded(bool(parts))

        # Geri yuklemede "yeni goruntu dosyasi" da bir hedeftir: mevcut hicbir
        # diske dokunmadan yedegin icerigini acmanin yolu budur.
        self.new_image_item = self._add_row(
            None, _Target("new", tr("Yeni goruntu dosyasi..."), 0),
            app_icon("save"), extra=tr("Hicbir diske dokunulmaz"))

        if selected_item is None:
            selected_item = self.tree.topLevelItem(0)
            while selected_item is not None and \
                    selected_item.data(0, Qt.UserRole) is None:
                selected_item = (self.tree.itemBelow(selected_item))
        # Yedek alirken kaynak onceden secilir; geri yuklemede hedef ASLA
        # kendiliginden secilmez (bkz. `_sync_target_state`).
        self._initial_item = selected_item
        if selected_item is not None:
            self.tree.setCurrentItem(selected_item)

    def _add_row(self, parent, target: _Target, icon=None,
                 extra: str = "") -> QTreeWidgetItem:
        index = len(self.targets)
        self.targets.append(target)
        node = QTreeWidgetItem(parent or self.tree,
                               [target.label,
                                human_size(target.size) if target.size else "",
                                extra])
        node.setData(0, Qt.UserRole, index)
        if icon is not None:
            node.setIcon(0, icon)
        return node

    @staticmethod
    def _disk_state(disk) -> str:
        parts = []
        if disk.is_system:
            parts.append(tr("SISTEM DISKI"))
        if not disk.info_complete:
            parts.append(tr("bilgi eksik"))
        if disk.mounted:
            parts.append(tr("{} bagli", len(disk.mounted)))
        if disk.readonly:
            parts.append(tr("yazma korumali"))
        return ", ".join(parts)

    def current_target(self) -> Optional[_Target]:
        item = self.tree.currentItem()
        if item is None:
            return None
        index = item.data(0, Qt.UserRole)
        if index is None:
            return None
        return self.targets[index]

    # ==================================================================
    # Kip ve durum
    # ==================================================================
    def set_mode(self, mode: str) -> None:
        self.mode = mode
        backup = mode == MODE_BACKUP
        self.rb_backup.setChecked(backup)
        self.rb_restore.setChecked(not backup)
        self.mode_note.setText(
            tr("Kaynak salt okunur acilir; hicbir sey silinmez.") if backup
            else tr("Hedefteki veriler yedekle degistirilir."))
        self.compress_row.setVisible(backup)
        self.used_only.setVisible(backup)
        self.confirm.setVisible(not backup)
        self.note_box.setVisible(backup)
        self.remark_caption.setVisible(not backup)
        self.info_labels["remark"].setVisible(not backup)
        self.tree.setHeaderLabels(
            [tr("Kaynak") if backup else tr("Hedef"), tr("Boyut"), tr("Durum")])
        self.target_caption.setText(tr("Kaynak:") if backup else tr("Hedef:"))
        self.new_image_item.setHidden(backup)
        if backup:
            current = self.current_target()
            if current is None or current.kind == "new":
                # "Yeni goruntu" yedek alinirken kaynak olamaz; kaynak yoksa
                # pencere acilirken secilen geri gelir.
                if self._initial_item is not None and \
                        not self._initial_item.isHidden():
                    self.tree.setCurrentItem(self._initial_item)
                else:
                    self._select_first_target()
        else:
            # Geri yuklemede hedef **acikca** secilir: yedek alirken secili
            # olan kaynak sessizce silinecek hedefe donusmemeli.
            self.tree.setCurrentIndex(QModelIndex())
        self._sync_target_state()
        if backup:
            self._show_source_content(self.current_target())
        elif self.preview is not None:
            self._preview_ready(self.preview)
        else:
            # Icerik agaci geri yuklemede **yedegi** anlatir; yedek secilmemisse
            # yedek alma kipinden kalan kaynak bolumleri gosterilmemeli.
            self._show_no_backup()
        self._refresh_plan()
        self.btn_start.setText(self._start_text())
        self._update_info()
        self._update_buttons()

    def _set_path(self, path: str) -> None:
        self.path_edit.setText(path)

    def _on_path_typed(self, text: str) -> None:
        if self.mode == MODE_BACKUP:
            # Yedek alirken icerik agaci **kaynagi** anlatir; dosya yolunun
            # degismesi onu silmemeli (eskiden siliyordu ve liste bosaliyordu).
            self._show_source_content(self.current_target())
            self._update_info()
        elif text and os.path.isfile(text):
            self._load_backup(text)
        else:
            self.info = None
            self.preview = None
            self.base_layout = None
            self.plan = None
            self._show_no_backup()
            self._update_info()
            self._on_target_changed()
        self._sync_target_state()
        self._update_buttons()

    def _pick_file(self) -> None:
        if self.mode == MODE_BACKUP:
            target = self.current_target()
            default = (target.label if target else "yedek").replace(" ", "_")
            path, _ = QFileDialog.getSaveFileName(
                self, tr("Yedek dosyasi"), f"{default}.dub",
                tr("DiskUltimate yedegi (*.dub)"))
            if path and not path.lower().endswith(".dub"):
                path += ".dub"
        else:
            path, _ = QFileDialog.getOpenFileName(
                self, tr("Yedek dosyasi"), "",
                tr("DiskUltimate yedegi (*.dub);;Tum dosyalar (*)"))
        if path:
            self._set_path(path)

    def _load_backup(self, path: str) -> None:
        """Yedegin basligini ve icerigini okur (icerik arka planda)."""
        try:
            self.info = clone_mod.read_backup_info(path)
        except Exception as exc:                      # noqa: BLE001
            self.info = None
            self.status.setText(tr("Yedek okunamadi: {}", exc))
            self._update_info()
            return
        self.base_layout = None
        self.plan = None
        self._plan_key = None
        self._set_new_size(self.info.total_bytes)
        self._update_info()
        self.content.clear()
        QTreeWidgetItem(self.content, [tr("Icerik okunuyor..."), "", "", ""])
        self._run(lambda report: DiskSession.backup_preview(path),
                  self._preview_ready, keep_buttons=True)

    def _preview_ready(self, preview) -> None:
        self.preview = preview
        self.content.clear()
        if preview is None:
            return
        if preview.layout is not self.base_layout:
            self.base_layout = preview.layout
            self._plan_key = None
            self._on_target_changed()
        if preview.partitions:
            for part in preview.partitions:
                node = QTreeWidgetItem(self.content, [
                    tr("Bolum {}", part.index), fs_display(part.fs_type) or "-",
                    part.name or part.fs_label or "-", human_size(part.size)])
                node.setIcon(0, color_chip(fs_color(part.fs_type), 12))
                self._add_entries(node, preview.root_entries.get(part.index),
                                  fs_display(part.fs_type))
                node.setExpanded(True)
        else:
            fs_type = (getattr(preview.filesystem, "fs_type", "")
                       or preview.info.fs_type)
            node = QTreeWidgetItem(self.content, [
                tr("Tek bolum yedegi"), fs_display(fs_type) or "-",
                preview.info.label or "-", human_size(preview.info.total_bytes)])
            node.setIcon(0, color_chip(fs_color(fs_type), 12))
            self._add_entries(node, preview.root_entries.get(-1), fs_type)
            node.setExpanded(True)

    def _add_entries(self, parent: QTreeWidgetItem, names, fs_type: str) -> None:
        """Kok klasor girisleri. `None` "okunamadi", `[]` "gercekten bos"."""
        if names:
            for name in names:
                QTreeWidgetItem(parent, [name, "", "", ""])
            return
        text = (tr("(bos)") if names == []
                else tr("(bicimlendirilmemis)") if not fs_type
                else tr("({} icerigi bu surumde listelenemiyor)", fs_type))
        empty = QTreeWidgetItem(parent, [text, "", "", ""])
        empty.setDisabled(True)

    def _update_info(self) -> None:
        """Bilgi alanini doldurur: geri yuklemede dosyadan, yedeklemede kaynaktan."""
        if self.mode == MODE_RESTORE and self.info is not None:
            info = self.info
            self.info_labels["size"].setText(human_size(info.total_bytes))
            self.info_labels["file"].setText(
                tr("{} (%{:.0f} kazanc)", human_size(info.file_size),
                   100 * (1 - info.ratio)))
            self.info_labels["created"].setText(
                info.created.strftime("%Y-%m-%d %H:%M") if info.created else "-")
            self.info_labels["compress"].setText(
                "zlib" if info.compressed else tr("yok"))
            self.info_labels["scope"].setText(self._scope_text(info.used_only))
            self.info_labels["fs"].setText(fs_display(info.fs_type) or "-")
            self.info_labels["remark"].setText(info.remark or "-")
            return
        target = self.current_target()
        if self.mode == MODE_BACKUP and target is not None:
            self.info_labels["size"].setText(human_size(target.size))
            self.info_labels["file"].setText(tr("(yedek alininca belli olur)"))
            self.info_labels["created"].setText("-")
            self.info_labels["compress"].setText(self._level_text())
            self.info_labels["scope"].setText(
                self._scope_text(self.used_only.isChecked()))
            self.info_labels["fs"].setText(
                target.scheme.upper() if target.scheme else "-")
            return
        for label in self.info_labels.values():
            label.setText("-")

    @staticmethod
    def _scope_text(used_only: bool) -> str:
        return (tr("Yalnizca kullanilan alan") if used_only
                else tr("Tum sektorler"))

    def _level_text(self) -> str:
        for key, button in self.level_buttons.items():
            if button.isChecked():
                return {"none": tr("yok"), "fast": tr("Hizli"),
                        "normal": tr("Normal"), "high": tr("Yuksek")}[key]
        return "-"

    def level(self) -> int:
        for key, value in LEVELS:
            if self.level_buttons[key].isChecked():
                return value
        return clone_mod.LEVEL_NORMAL

    def _on_remark_changed(self) -> None:
        used = len(self.remark.toPlainText().encode("utf-8"))
        self.remark_count.setText(tr("{} / {} bayt", used,
                                     clone_mod.REMARK_SIZE))
        self.remark_count.setStyleSheet(
            f"color: {COLOR_WARN}" if used > clone_mod.REMARK_SIZE else "")

    # ==================================================================
    # Hedef degisimi ve dogrulama
    # ==================================================================
    def _on_target_changed(self) -> None:
        target = self.current_target()
        self._describe_target(target)
        self._refresh_plan()
        self.map.clear()
        self.map.setVisible(self.plan is None)
        self.layout_bar.setVisible(self.plan is not None)
        if self.plan is not None:
            # Geri yuklemede serit hedefin **yeni** halini gosterir; bolum
            # kenarlari surukleyerek boyutlandirilir.
            ss = self.plan.sector_size
            self.layout_bar.set_layout(
                self.plan,
                tr("{} — {} (geri yukleme sonrasi) — kenarlari surukleyerek "
                   "boyutlandirin", target.label,
                   human_size(self.plan.target_sectors * ss)))
        elif target is not None and target.partitions:
            sector = 512
            total = max(1, target.size // sector)
            self.map.set_disk(f"{target.label} — {human_size(target.size)}",
                              total, target.partitions, [])
        if self.mode == MODE_BACKUP:
            self._show_source_content(target)
        self._update_info()
        self._update_buttons()

    def _target_sectors(self, target: Optional[_Target]) -> int:
        """Hedefin sektor sayisi (yeni goruntude secilen boyuttan)."""
        if target is None:
            return 0
        ss = self.base_layout.sector_size if self.base_layout else 512
        if target.kind == "new":
            size = self._new_size_bytes()
            exact = self.info.total_bytes if self.info is not None else 0
            if exact and abs(size - exact) < 0.005 * self._unit_factor():
                # Kutu iki basamak gosterir; dokunulmamis varsayilan yedegin
                # **tam** boyutudur, yuvarlama bolumleri kucultmemeli.
                return exact // ss
            return (size // (1024 * 1024)) * (1024 * 1024) // ss
        if target.kind in ("image", "physical") and target.session is not None:
            return target.session.image.sector_count
        if target.kind == "physical":
            return target.size // (getattr(target.disk, "sector_size", ss) or ss)
        return 0

    # -- yeni goruntu boyutu ------------------------------------------------
    def _unit_factor(self) -> int:
        return SIZE_UNITS[self.size_unit.currentIndex()][1]

    def _apply_unit_range(self) -> None:
        _name, _factor, low, high = SIZE_UNITS[self.size_unit.currentIndex()]
        self.size_spin.blockSignals(True)
        self.size_spin.setRange(low, high)
        self.size_spin.blockSignals(False)

    def _new_size_bytes(self) -> int:
        return int(round(self.size_spin.value() * self._unit_factor()))

    def _on_unit_changed(self, index: int) -> None:
        """Birim degisince **boyut** korunur, yalnizca gosterim degisir."""
        size = int(round(self.size_spin.value() * SIZE_UNITS[self._unit_index][1]))
        self._unit_index = index
        self._apply_unit_range()
        self.size_spin.blockSignals(True)
        self.size_spin.setValue(size / self._unit_factor())
        self.size_spin.blockSignals(False)
        self._on_target_changed()

    def _set_new_size(self, size: int) -> None:
        """Boyutu en okunur birimde yazar (1 GB altinda MB)."""
        index = 2 if size >= TIB else 1 if size >= GIB else 0
        self.size_unit.blockSignals(True)
        self.size_unit.setCurrentIndex(index)
        self.size_unit.blockSignals(False)
        self._unit_index = index
        self._apply_unit_range()
        self.size_spin.blockSignals(True)
        self.size_spin.setValue(size / self._unit_factor())
        self.size_spin.blockSignals(False)

    # -- hedef secimi -------------------------------------------------------
    def _pick_target(self) -> None:
        if self._running:
            return
        backup = self.mode == MODE_BACKUP
        self.picker.pick(
            tr("Kaynak disk sec") if backup else tr("Hedef disk sec"),
            tr("Yedeklenecek goruntuyu, bolumu ya da fiziksel diski secin.")
            if backup else
            tr("Yedegin yazilacagi yeri secin. \"Yeni goruntu dosyasi\" "
               "hicbir diske dokunmaz."))

    def _select_first_target(self) -> None:
        item = self.tree.topLevelItem(0)
        while item is not None and (item.data(0, Qt.UserRole) is None
                                    or item.isHidden()):
            item = self.tree.itemBelow(item)
        if item is not None:
            self.tree.setCurrentItem(item)

    def _describe_target(self, target: Optional[_Target]) -> None:
        """Ana formdaki tek satirlik hedef ozeti."""
        if target is None:
            self.target_label.setText(tr("(secilmedi)"))
            return
        parts = [f"<b>{target.label}</b>"]
        if target.kind == "partition" and target.session is not None:
            parts.append(target.session.name)
        if target.size:
            parts.append(human_size(target.size))
        if target.kind == "physical":
            state = self._disk_state(target.disk)
            if state:
                parts.append(f"<span style='color:{COLOR_WARN}'>{state}</span>"
                             if target.is_system_disk else state)
        elif target.kind == "new":
            parts.append(tr("Hicbir diske dokunulmaz"))
        self.target_label.setText(" — ".join(parts))

    def _sync_target_state(self) -> None:
        """Geri yuklemede yedek secilmeden hedef alani pasiftir."""
        backup = self.mode == MODE_BACKUP
        self.target_group.setTitle(tr("Kaynak Disk / Bolum") if backup
                                   else tr("Hedef Disk / Bolum"))
        ready = backup or self.info is not None
        self.target_group.setEnabled(ready)
        if not ready and self.tree.currentItem() is not None:
            self.tree.setCurrentIndex(QModelIndex())
        self._refresh_plan()

    def _on_layout_dragged(self) -> None:
        """Seritte kenar suruklendi: ozet ve dogrulama tazelenir."""
        self._describe_plan()
        self._update_buttons()

    def _show_no_backup(self) -> None:
        self.content.clear()
        empty = QTreeWidgetItem(self.content,
                                [tr("(yedek dosyasi secilmedi)"), "", "", ""])
        empty.setDisabled(True)

    def _refresh_plan(self) -> None:
        """Secili hedef icin yerlesimi hazirlar (kullanici duzenlemesi korunur).

        Hedef ya da hedefin boyutu degismedikce plan yeniden kurulmaz; boylece
        "Bolumleri yonet" ile yapilan degisiklik baska bir yere tiklayinca
        kaybolmaz.
        """
        target = self.current_target()
        usable = (self.mode == MODE_RESTORE and self.base_layout is not None
                  and target is not None
                  and target.kind in ("image", "physical", "new"))
        is_new = self.mode == MODE_RESTORE and target is not None \
            and target.kind == "new"
        self.layout_row.setVisible(self.mode == MODE_RESTORE
                                   and (target is None
                                        or target.kind != "partition"))
        self.size_caption.setVisible(is_new)
        self.size_spin.setVisible(is_new)
        self.size_unit.setVisible(is_new)
        self.btn_layout.setVisible(usable)
        if not usable:
            self.plan = None
            self._plan_key = None
            self.layout_label.setText("")
            if self.mode == MODE_RESTORE and target is None:
                self.layout_label.setText(
                    tr("Yedegin yazilacagi diski secin: \"Disk sec...\"")
                    if self.info is not None else "")
            if self.mode == MODE_RESTORE and target is not None and \
                    self.info is not None and target.kind != "partition":
                self.layout_label.setText(
                    tr("Yedek bayt bayt yazilir; bolum yerlesimi "
                       "degistirilemez.") if self.preview is not None
                    else tr("Yedek okunuyor..."))
            return
        sectors = self._target_sectors(target)
        key = (id(target), sectors)
        if key != self._plan_key:
            self.plan = self.base_layout.copy()
            self.plan.retarget(sectors)
            self._plan_key = key
        self._describe_plan()

    def _describe_plan(self) -> None:
        plan = self.plan
        problem = plan.validate()
        if problem:
            self.layout_label.setText(
                f"<span style='color:{COLOR_WARN}'>{problem}</span>")
            return
        changes = plan.summary()
        if changes:
            self.layout_label.setText(tr("Yeni yerlesim: {}",
                                         "; ".join(changes)))
        elif plan.target_sectors > plan.source_sectors:
            free = (plan.target_sectors - plan.source_sectors) * plan.sector_size
            self.layout_label.setText(
                tr("Bolumler yedekteki gibi yazilir; {} bos kalir — "
                   "\"Bolumleri yonet\" ile dagitabilirsiniz.",
                   human_size(free)))
        else:
            self.layout_label.setText(
                tr("Bolumler yedekteki yer ve boyutlarinda yazilacak."))

    def _manage_layout(self) -> None:
        if self.plan is None or self._running:
            return
        target = self.current_target()
        dialog = RestoreLayoutDialog(self.plan, target.label, self)
        if dialog.exec_() != QDialog.Accepted:
            return
        self.plan = dialog.result_layout()
        self._on_target_changed()

    def _show_source_content(self, target: Optional[_Target]) -> None:
        """Yedek alirken icerik agaci **kaynagin** bolumlerini gosterir.

        Boylece "ne yedeklenecek?" sorusu da bu pencerede yanitlanir; bos bir
        liste birakmak kullaniciya bir sey anlatmazdi.
        """
        self.content.clear()
        if target is None:
            return
        for part in target.partitions:
            node = QTreeWidgetItem(self.content, [
                tr("Bolum {}", part.index), fs_display(part.fs_type) or "-",
                part.name or part.fs_label or "-", human_size(part.size)])
            node.setIcon(0, color_chip(fs_color(part.fs_type), 12))
        if not target.partitions and target.size:
            QTreeWidgetItem(self.content,
                            [tr("(bolum tablosu okunamadi ya da yok)"), "",
                             "", human_size(target.size)])

    def _problem(self) -> str:
        """Islemi engelleyen ilk sorun ("" ise engel yok)."""
        target = self.current_target()
        path = self.path_edit.text().strip()
        if self.mode == MODE_RESTORE and (not path or self.info is None):
            return tr("Once yedek dosyasini secin.")
        if target is None:
            return (tr("Once bir kaynak secin.") if self.mode == MODE_BACKUP
                    else tr("Hedef diski secin (\"Disk sec...\")."))
        if not path:
            return tr("Once yedek dosyasini secin.")
        if self.mode == MODE_BACKUP:
            if target.kind == "new":
                return tr("Yeni goruntu dosyasi yalnizca geri yuklemede hedeftir.")
            if target.kind == "physical" and not target.disk.info_complete:
                return tr("Disk bilgisi eksik; once yetki alin.")
            return ""
        if self.info is None:
            return tr("Yedek dosyasi okunamadi.")
        if self.plan is not None:
            problem = self.plan.validate()
            if problem:
                return problem
        elif target.kind == "new" and self._target_sectors(target) * 512 < \
                self.info.total_bytes:
            return tr("Goruntu boyutu yedekten kucuk olamaz ({}).",
                      human_size(self.info.total_bytes))
        elif target.kind != "new" and self.info.total_bytes > target.size:
            return tr("Hedef cok kucuk: yedek {}, hedef {}.",
                      human_size(self.info.total_bytes),
                      human_size(target.size))
        # Fiziksel diskin kendisi ya da bir bolumu: ayni koruma kapilari
        if target.kind == "physical" or (target.kind == "partition"
                                         and target.disk is not None):
            if not target.disk.info_complete:
                return tr("Disk bilgisi eksik; bilgisi okunamayan diske "
                          "yazilmaz.")
            if target.disk.readonly:
                return tr("Disk donanimsal olarak yazma korumali.")
        if target.kind != "new" and not self.confirm.isChecked():
            return tr("Silme onayini isaretleyin.")
        if target.is_system_disk and \
                self.name_edit.text().strip() != target.disk_name:
            return tr("Sistem diski: onaylamak icin disk adini yazin.")
        return ""

    def _update_buttons(self) -> None:
        target = self.current_target()
        restore = self.mode == MODE_RESTORE
        system_disk = bool(target is not None and target.is_system_disk
                           and restore)
        self.name_label.setVisible(system_disk)
        self.name_edit.setVisible(system_disk)
        if system_disk:
            self.name_label.setText(
                tr("<b>{}</b> isletim sistemi diskidir. Onaylamak icin disk "
                   "adini yazin: <b>{}</b>", target.disk.path,
                   target.disk_name))
        notes = []
        if target is not None and restore and target.mounted:
            notes.append(tr("Bu diskte bagli bolumler var: {} — yazmadan once "
                            "cikarmaniz onerilir.", ", ".join(target.mounted)))
        if target is not None and self.mode == MODE_BACKUP:
            notes.append(tr("Kaynak yalnizca okunur; yedek dosyasi disinda hicbir "
                            "yere yazilmaz."))
        self.warn.setText(
            f"<span style='color:{COLOR_WARN}'>{notes[0]}</span>"
            if notes and restore else (notes[0] if notes else ""))
        problem = self._problem()
        if self._running:
            # Calisirken dugme "Durdur"dur: durdurma istenene kadar acik.
            self.btn_start.setEnabled(not self._worker.stop_requested)
            self.btn_start.setToolTip("")
        else:
            self.btn_start.setEnabled(not problem)
            self.btn_start.setToolTip(problem)
        # Durum satiri **bayat kalmamali**: engel giderilince metin de gider.
        # Islem sonucu yaziliysa ona dokunulmaz.
        if self._running or self._result_shown:
            return
        self.status.setStyleSheet("")
        self.status.setText(problem)

    # ==================================================================
    # Calistirma
    # ==================================================================
    def _run(self, func, on_done, keep_buttons: bool = False) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        worker = _Worker(func)
        self._worker = worker
        if not keep_buttons:
            self._running = True
            self._run_mode = self.mode
            self.status.setStyleSheet("")   # onceki sonucun rengi kalmasin
            self._lock_inputs(True)
            self.btn_close.setEnabled(False)
            self.btn_start.setText(tr("Durdur"))
            self.btn_start.setIcon(app_icon("stop"))
            self.btn_start.setEnabled(True)
            self._started_at = time.monotonic()
            self._eta_base = self._eta_last = None
            self._update_clock()
            self._clock.start()
        worker.progress.connect(self._on_progress)
        worker.done.connect(lambda value: self._finish(on_done, value, None,
                                                       keep_buttons))
        worker.failed.connect(lambda message: self._finish(on_done, None,
                                                           message, keep_buttons))
        worker.cancelled.connect(lambda: self._finish(on_done, None, None,
                                                      keep_buttons,
                                                      cancelled=True))
        worker.start()

    def _lock_inputs(self, locked: bool) -> None:
        """Is surerken kaynak/hedef, dosya, kip ve secenekler degistirilemez.

        Calisan is baslangicta okunan degerlerle surer; arada degisen bir
        secim yalnizca ekrani yanlis gosterirdi (ve geri yuklemede yanlis
        hedefe dair bir izlenim verirdi).
        """
        for widget in (self.rb_backup, self.rb_restore, self.path_edit,
                       self.btn_pick, self.note_box, self.target_group,
                       self.options_group):
            widget.setEnabled(not locked)
        if not locked:
            self._sync_target_state()   # hedef alani kipe gore yeniden

    def _request_stop(self) -> None:
        worker = self._worker
        if worker is None or not worker.isRunning() or worker.stop_requested:
            return
        target = self.current_target()
        if self._run_mode == MODE_RESTORE and target is not None \
                and target.kind != "new":
            answer = QMessageBox.question(
                self, tr("Islemi durdur"),
                tr("Geri yukleme yarida kesilirse hedef tutarsiz kalir ve "
                   "yeniden geri yuklenene ya da bicimlendirilene kadar "
                   "kullanilamaz. Yine de durdurulsun mu?"),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes or not worker.isRunning():
                return
        worker.request_stop()
        self.btn_start.setText(tr("Durduruluyor..."))
        self.btn_start.setEnabled(False)

    def _start_text(self) -> str:
        return tr("Yedegi al") if self.mode == MODE_BACKUP else tr("Geri yukle")

    # -- sure ---------------------------------------------------------------
    @staticmethod
    def _format_duration(seconds: float) -> str:
        seconds = max(0, int(round(seconds)))
        hours, rest = divmod(seconds, 3600)
        minutes, secs = divmod(rest, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    def _remaining(self, now: float) -> Optional[float]:
        """Kalan sure tahmini; ilerleme yetersizse None.

        Hiz, son belirsiz asamadan (kullanilan alan hesabi gibi) sonra ilk
        yuzdenin geldigi andan olculur: hazirlik suresi hizi bozmasin.
        """
        if self._eta_base is None or self._eta_last is None:
            return None
        t0, p0 = self._eta_base
        t1, p1 = self._eta_last
        if p1 - p0 < 1 or t1 - t0 < 1.0:
            return None
        per_percent = (t1 - t0) / (p1 - p0)
        return max(0.0, per_percent * (100 - p1) - (now - t1))

    def _update_clock(self) -> None:
        if not self._running:
            return
        now = time.monotonic()
        text = tr("Gecen: {}", self._format_duration(now - self._started_at))
        remaining = self._remaining(now)
        if remaining is not None:
            text += " · " + tr("Kalan: ~{}", self._format_duration(remaining))
        else:
            text += " · " + tr("Kalan: hesaplaniyor...")
        self.time_label.setText(text)

    def _finish(self, on_done, value, error, keep_buttons: bool,
                cancelled: bool = False) -> None:
        if not keep_buttons:
            self._running = False
            self._clock.stop()
            self.time_label.setText(tr("Sure: {}", self._format_duration(
                time.monotonic() - self._started_at)))
            self.btn_close.setEnabled(True)
            self.btn_start.setIcon(app_icon("apply"))
            self.btn_start.setText(self._start_text())
            self._lock_inputs(False)
        if cancelled:
            self.status.setText(self._cancelled_text())
            self.status.setStyleSheet(f"color: {COLOR_WARN}")
            self.bar.setRange(0, 100)
            self.bar.setValue(0)
            self._result_shown = True
        elif error:
            self.status.setText(tr("Basarisiz: {}", error))
            self.status.setStyleSheet(f"color: {COLOR_WARN}")
            self.bar.setValue(0)
            self._result_shown = True
        else:
            self.status.setStyleSheet("")
            on_done(value)
        self._update_buttons()

    def _cancelled_text(self) -> str:
        if self._run_mode == MODE_BACKUP:
            return tr("Yedekleme durduruldu; yarim kalan yedek dosyasi silindi.")
        target = self.current_target()
        if target is not None and target.kind == "new":
            return tr("Geri yukleme durduruldu; yarim kalan goruntu dosyasi "
                      "silindi.")
        return tr("Geri yukleme durduruldu. Hedef tutarsiz durumda: yeniden "
                  "geri yukleyin ya da bicimlendirin.")

    def _on_progress(self, message: str, percent: int) -> None:
        if self._worker is not None and self._worker.stop_requested:
            return                       # "Durduruluyor..." ezilmesin
        self.status.setText(message)
        if self._running:
            now = time.monotonic()
            if percent < 0:
                self._eta_base = self._eta_last = None   # belirsiz asama
            elif self._eta_base is None or percent < self._eta_last[1]:
                self._eta_base = self._eta_last = (now, percent)
            else:
                self._eta_last = (now, percent)
        if percent < 0:
            if self.bar.maximum() != 0:
                self.bar.setRange(0, 0)
            return
        if self.bar.maximum() == 0:
            self.bar.setRange(0, 100)
        self.bar.setValue(max(0, min(100, percent)))

    def _start(self) -> None:
        if self._running:
            self._request_stop()
            return
        if self._problem():
            return
        self._result_shown = False
        if self.mode == MODE_BACKUP:
            self._start_backup()
        else:
            self._start_restore()

    def _start_backup(self) -> None:
        target = self.current_target()
        path = self.path_edit.text().strip()
        remark = self.remark.toPlainText()
        level = self.level()
        compress = level > clone_mod.LEVEL_NONE
        used_only = self.used_only.isChecked()

        if target.kind == "physical" and target.session is None:
            disk = target.disk

            def task(report):
                return DiskSession.backup_physical(
                    disk, path, compress=compress, progress=report,
                    remark=remark, level=level, used_only=used_only)
        elif target.kind == "partition":
            session, index = target.session, target.index

            def task(report):
                return session.backup_partition(
                    index, path, compress=compress, progress=report,
                    remark=remark, level=level, used_only=used_only)
        else:
            session = target.session

            def task(report):
                return session.backup_disk(path, compress=compress,
                                           progress=report, remark=remark,
                                           level=level, used_only=used_only)

        self.bar.setValue(0)
        self._run(task, self._backup_done)

    def _backup_done(self, info) -> None:
        self.result_value = info
        self.info = info
        self.bar.setValue(100)
        self.status.setText(
            tr("Yedek alindi: {} — {} (kaynak {}, kazanc %{:.0f})",
               os.path.basename(info.path), human_size(info.file_size),
               human_size(info.total_bytes), 100 * (1 - info.ratio)))
        self.status.setStyleSheet(f"color: {COLOR_OK}")
        self._result_shown = True
        # Yeni yedek hemen incelenebilsin: kip geri yuklemeye alinmaz ama
        # bilgi alani dolar.
        self._update_info()

    def _start_restore(self) -> None:
        target = self.current_target()
        path = self.path_edit.text().strip()

        if target.kind == "new":
            suggested = os.path.splitext(path)[0] + ".img"
            dest, _ = QFileDialog.getSaveFileName(
                self, tr("Geri yuklenecek goruntu dosyasi"), suggested,
                tr("Disk goruntusu (*.img *.raw *.dd);;Tum dosyalar (*)"))
            if not dest:
                return
            if os.path.abspath(dest) == os.path.abspath(path):
                self.status.setText(tr("Hedef, yedek dosyasinin kendisi olamaz."))
                return
            layout = self.plan
            size = (layout.target_sectors * layout.sector_size if layout
                    else self._target_sectors(target) * 512)

            self._run(lambda report: DiskSession.restore_to_new_image(
                path, dest, progress=report, size_bytes=size, layout=layout),
                self._restore_done)
            return
        layout = self.plan

        allow_system = target.is_system_disk
        if target.kind == "physical" and target.session is None:
            disk = target.disk

            def task(report):
                return DiskSession.restore_to_physical(
                    path, disk, allow_system=allow_system, progress=report,
                    layout=layout)
        elif target.kind == "partition":
            session, index = target.session, target.index

            def task(report):
                self._make_writable(session, allow_system)
                return session.restore_partition(index, path, progress=report)
        else:
            # Goruntu ya da UYGULAMADA ACIK fiziksel disk: oturumun kendi
            # tutamaci kullanilir (ikinci tutamac acilmaz, ADR 0021).
            session = target.session

            def task(report):
                self._make_writable(session, allow_system)
                return session.restore_disk(path, progress=report,
                                            layout=layout)

        self.bar.setValue(0)
        self._run(task, self._restore_done)

    @staticmethod
    def _make_writable(session, allow_system: bool) -> None:
        """Acik oturumu yazma moduna alir (is parcaciginda cagrilir).

        Oturumlar salt okunur acilir (ADR 0025); geri yukleme eskiden bunu
        atliyor ve acik bir goruntuye/diske yazmak "salt okunur" hatasiyla
        duruyordu. Onay bu pencerede alindi (silme kutusu, sistem diskinde ad
        yazma); `become_writable` fiziksel diskin butun kapilarindan gecer.
        """
        if session.readonly:
            session.become_writable(confirm=True, allow_system=allow_system)

    def _restore_done(self, value) -> None:
        self.result_value = value
        self.reload_needed = True
        self.bar.setValue(100)
        if isinstance(value, str) and os.path.isfile(value):
            self.opened_path = value
            self.status.setText(tr("Geri yuklendi: {}", value))
        else:
            self.status.setText(tr("Geri yukleme tamamlandi."))
        self.status.setStyleSheet(f"color: {COLOR_OK}")
        self._result_shown = True

    # ==================================================================
    def closeEvent(self, event):
        if self._running:
            event.ignore()
            return
        super().closeEvent(event)

    def reject(self) -> None:
        if self._running:
            return
        super().reject()

    def exec_(self) -> int:
        result = super().exec_()
        if self._worker is not None:
            self._worker.wait()
        return result
