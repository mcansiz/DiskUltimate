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

  * **Ustte yedek dosyasi**: yolu, bilgileri (boyut, tarih, sikistirma),
    **notu** ve icerigi (bolumler, kok klasor girisleri).
  * **Altta hedef/kaynak**: acik goruntuler ve fiziksel diskler tek agacta,
    secilenin bolum haritasi ustunde cizili.
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
from typing import Callable, List, Optional

from PyQt5.QtCore import QSize, Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QFileDialog,
                             QFormLayout, QGroupBox, QHBoxLayout, QHeaderView,
                             QLabel, QLineEdit, QPlainTextEdit, QProgressBar,
                             QPushButton, QRadioButton, QSplitter, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout, QWidget)

from ...core import clone as clone_mod
from ...core.ptable import human_size
from ...core.session import DiskSession
from ..icons import icon as app_icon
from ..theme import fs_color
from ..widgets.disk_map import DiskMapWidget
from ..widgets.partition_table import color_chip
from ...i18n import tr

MODE_BACKUP = "backup"
MODE_RESTORE = "restore"

COLOR_WARN = "#c0392b"
COLOR_OK = "#1e8449"

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

    def __init__(self, func: Callable):
        super().__init__()
        self.func = func

    def run(self):
        try:
            self.done.emit(self.func(lambda m, p: self.progress.emit(m, p)))
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
        splitter.setSizes([360, 300])
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
        for key in ("size", "file", "created", "compress", "fs"):
            value = QLabel("-")
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.info_labels[key] = value
        self.info_form.addRow(tr("Kaynak boyut:"), self.info_labels["size"])
        self.info_form.addRow(tr("Yedek boyut:"), self.info_labels["file"])
        self.info_form.addRow(tr("Olusturma:"), self.info_labels["created"])
        self.info_form.addRow(tr("Sikistirma:"), self.info_labels["compress"])
        self.info_form.addRow(tr("Dosya sistemi:"), self.info_labels["fs"])
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

        note_row = QHBoxLayout()
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
        self.btn_save_remark = QPushButton(tr("Notu kaydet"))
        self.btn_save_remark.setToolTip(
            tr("Notu var olan yedek dosyasina yazar; veri bloklarina "
               "dokunulmaz"))
        self.btn_save_remark.clicked.connect(self._save_remark)
        side.addWidget(self.btn_save_remark)
        side.addStretch(1)
        note_row.addLayout(side)
        layout.addLayout(note_row)
        return group

    def _build_target_group(self) -> QWidget:
        group = QGroupBox(tr("Disk / bolum"))
        layout = QVBoxLayout(group)
        self.map = DiskMapWidget()
        self.map.setMinimumHeight(110)
        layout.addWidget(self.map)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("Hedef"), tr("Boyut"), tr("Durum")])
        self.tree.setIconSize(QSize(18, 18))
        self.tree.setRootIsDecorated(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.setColumnWidth(1, 110)
        self.tree.setColumnWidth(2, 220)
        self.tree.currentItemChanged.connect(lambda *_: self._on_target_changed())
        layout.addWidget(self.tree, 1)
        return group

    def _build_options_group(self) -> QWidget:
        group = QGroupBox(tr("Secenekler"))
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
        """Acik goruntuleri ve fiziksel diskleri tek agaca doldurur."""
        self.tree.clear()
        self.targets = []
        selected_item = None

        if self.sessions:
            root = QTreeWidgetItem(self.tree, [tr("Acik goruntuler"), "", ""])
            root.setFirstColumnSpanned(True)
            root.setExpanded(True)
            for item_session in self.sessions:
                target = _Target("image", item_session.name,
                                 item_session.image.size, session=item_session,
                                 partitions=list(item_session.partitions),
                                 scheme=item_session.scheme)
                node = self._add_row(root, target, app_icon("image"))
                if item_session is session and partition_index < 0:
                    selected_item = node
                for part in item_session.partitions:
                    part_target = _Target(
                        "partition", tr("Bolum {}", part.index), part.size,
                        session=item_session, index=part.index,
                        partitions=[part], scheme=item_session.scheme)
                    child = self._add_row(node, part_target,
                                          color_chip(fs_color(part.fs_type)),
                                          extra=part.fs_type or "-")
                    if item_session is session and part.index == partition_index:
                        selected_item = child
                node.setExpanded(True)

        if self.disks:
            root = QTreeWidgetItem(self.tree, [tr("Fiziksel diskler"), "", ""])
            root.setFirstColumnSpanned(True)
            root.setExpanded(True)
            for disk in self.disks:
                survey = self.surveys.get(disk.path)
                parts = list(getattr(survey, "partitions", []) or [])
                target = _Target("physical", disk.display_name, disk.size,
                                 disk=disk, partitions=parts,
                                 scheme=getattr(survey, "scheme", ""))
                node = self._add_row(
                    root, target,
                    app_icon("disk-system" if disk.is_system else "disk"),
                    extra=self._disk_state(disk))
                if disk.path == disk_path:
                    selected_item = node
                for part in parts:
                    QTreeWidgetItem(node, [tr("Bolum {} — {}", part.index,
                                              part.fs_type or tr("ham")),
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
        self.confirm.setVisible(not backup)
        self.btn_save_remark.setVisible(not backup)
        self.tree.setHeaderLabels(
            [tr("Kaynak") if backup else tr("Hedef"), tr("Boyut"), tr("Durum")])
        self.new_image_item.setHidden(backup)
        if backup:
            self._show_source_content(self.current_target())
        self.btn_start.setText(tr("Yedegi al") if backup
                               else tr("Geri yukle"))
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
            self.content.clear()
            self._update_info()
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
        self.remark.blockSignals(True)
        self.remark.setPlainText(self.info.remark)
        self.remark.blockSignals(False)
        self._on_remark_changed()
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
        if preview.partitions:
            for part in preview.partitions:
                node = QTreeWidgetItem(self.content, [
                    tr("Bolum {}", part.index), part.fs_type or "-",
                    part.name or part.fs_label or "-", human_size(part.size)])
                node.setIcon(0, color_chip(fs_color(part.fs_type), 12))
                self._add_entries(node, preview.root_entries.get(part.index),
                                  part.fs_type)
                node.setExpanded(True)
        else:
            fs_type = (getattr(preview.filesystem, "fs_type", "")
                       or preview.info.fs_type)
            node = QTreeWidgetItem(self.content, [
                tr("Tek bolum yedegi"), fs_type or "-",
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
            self.info_labels["fs"].setText(info.fs_type or "-")
            return
        target = self.current_target()
        if self.mode == MODE_BACKUP and target is not None:
            self.info_labels["size"].setText(human_size(target.size))
            self.info_labels["file"].setText(tr("(yedek alininca belli olur)"))
            self.info_labels["created"].setText("-")
            self.info_labels["compress"].setText(self._level_text())
            self.info_labels["fs"].setText(
                target.scheme.upper() if target.scheme else "-")
            return
        for label in self.info_labels.values():
            label.setText("-")

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

    def _save_remark(self) -> None:
        path = self.path_edit.text().strip()
        if not path or not os.path.isfile(path):
            return
        try:
            written = clone_mod.write_remark(path, self.remark.toPlainText())
        except Exception as exc:                      # noqa: BLE001
            self.status.setText(tr("Not kaydedilemedi: {}", exc))
            return
        self.info = clone_mod.read_backup_info(path)
        self.status.setText(tr("Not kaydedildi ({} bayt).",
                               len(written.encode("utf-8"))))

    # ==================================================================
    # Hedef degisimi ve dogrulama
    # ==================================================================
    def _on_target_changed(self) -> None:
        target = self.current_target()
        self.map.clear()
        if target is not None and target.partitions:
            sector = 512
            total = max(1, target.size // sector)
            self.map.set_disk(f"{target.label} — {human_size(target.size)}",
                              total, target.partitions, [])
        if self.mode == MODE_BACKUP:
            self._show_source_content(target)
        self._update_info()
        self._update_buttons()

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
                tr("Bolum {}", part.index), part.fs_type or "-",
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
        if target is None:
            return tr("Once bir kaynak/hedef secin.")
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
        if target.kind != "new" and self.info.total_bytes > target.size:
            return tr("Hedef cok kucuk: yedek {}, hedef {}.",
                      human_size(self.info.total_bytes),
                      human_size(target.size))
        if target.kind == "physical":
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
        self.btn_start.setEnabled(not problem and not self._running)
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
        if not keep_buttons:
            self._running = True
            self.btn_start.setEnabled(False)
            self.btn_close.setEnabled(False)
        worker = _Worker(func)
        self._worker = worker
        worker.progress.connect(self._on_progress)
        worker.done.connect(lambda value: self._finish(on_done, value, None,
                                                       keep_buttons))
        worker.failed.connect(lambda message: self._finish(on_done, None,
                                                           message, keep_buttons))
        worker.start()

    def _finish(self, on_done, value, error, keep_buttons: bool) -> None:
        if not keep_buttons:
            self._running = False
            self.btn_close.setEnabled(True)
        if error:
            self.status.setText(tr("Basarisiz: {}", error))
            self.status.setStyleSheet(f"color: {COLOR_WARN}")
            self.bar.setValue(0)
            self._result_shown = True
        else:
            self.status.setStyleSheet("")
            on_done(value)
        self._update_buttons()

    def _on_progress(self, message: str, percent: int) -> None:
        self.status.setText(message)
        if percent < 0:
            if self.bar.maximum() != 0:
                self.bar.setRange(0, 0)
            return
        if self.bar.maximum() == 0:
            self.bar.setRange(0, 100)
        self.bar.setValue(max(0, min(100, percent)))

    def _start(self) -> None:
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

        if target.kind == "physical":
            disk = target.disk

            def task(report):
                return DiskSession.backup_physical(
                    disk, path, compress=compress, progress=report,
                    remark=remark, level=level)
        elif target.kind == "partition":
            session, index = target.session, target.index

            def task(report):
                return session.backup_partition(
                    index, path, compress=compress, progress=report,
                    remark=remark, level=level)
        else:
            session = target.session

            def task(report):
                return session.backup_disk(path, compress=compress,
                                           progress=report, remark=remark,
                                           level=level)

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
            self._run(lambda report: DiskSession.restore_to_new_image(
                path, dest, progress=report), self._restore_done)
            return

        if target.kind == "physical":
            disk = target.disk
            allow_system = target.is_system_disk

            def task(report):
                return DiskSession.restore_to_physical(
                    path, disk, allow_system=allow_system, progress=report)
        elif target.kind == "partition":
            session, index = target.session, target.index

            def task(report):
                return session.restore_partition(index, path, progress=report)
        else:
            session = target.session

            def task(report):
                return session.restore_disk(path, progress=report)

        self.bar.setValue(0)
        self._run(task, self._restore_done)

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
