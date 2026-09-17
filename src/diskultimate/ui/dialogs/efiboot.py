"""UEFI onyukleme duzenleyicisi — girisler, sira, bekleme suresi.

`efibooteditor`in yaptigi isin karsiligi; farklari:

- **Hicbir sey aninda yazilmaz.** Butun degisiklikler bellekte birikir;
  "Bellenime yaz" once tam listeyi gosterir, sonra uygular. Bu, projenin
  bekleyen islem kuyrugundaki ayni ilkedir (ADR 0025) — burada kuyruk
  bellenime aittir, diske degil, bu yuzden ayri tutulur.
- **Yazmadan once yedek zorunludur.** Yanlis yazilmis bir onyukleme duzeni
  makineyi acilmaz birakabilir ve duzeltmek icin calisan bir sistem gerekir.
- Bellenime erisilemeyen makinelerde (BIOS kipi, yetki yok, macOS) pencere
  yine acilir ve **nedenini soyler**; ayrica alinmis bir yedek dosyasi
  incelenebilir.
"""
from __future__ import annotations

import copy
from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFontDatabase
from PyQt5.QtWidgets import (QAbstractItemView, QComboBox, QDialog,
                             QDialogButtonBox, QFileDialog, QFormLayout,
                             QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                             QLineEdit, QMessageBox, QPlainTextEdit,
                             QPushButton, QSpinBox, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout, QWidget)

from ...core import efistore
from ...i18n import tr
from ..icons import icon as app_icon
from .base import exec_dialog
from .task import run_task

# Yedek dosyasinin varsayilan adi ve suzgeci
BACKUP_FILTER = "JSON (*.json);;" + "*"


class EfiBootDialog(QDialog):
    """UEFI onyukleme girislerini gosterir ve duzenler."""

    def __init__(self, parent):
        super().__init__(parent)
        self.original: Optional[efistore.BootState] = None
        self.state: Optional[efistore.BootState] = None
        self.setWindowTitle(tr("UEFI Onyukleme Duzenleyici"))
        self.setMinimumSize(900, 580)
        self._build()
        # Okuma burada **yapilmaz**: yapici icinde modal ilerleme penceresi
        # acmak, henuz gosterilmemis pencerenin uzerine pencere koymaktir.
        # Cagiran once okur ve `adopt()` ile verir (bkz. `show_efi_boot`).

    # ------------------------------------------------------------------
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        self.header = QLabel()
        self.header.setWordWrap(True)
        layout.addWidget(self.header)

        body = QHBoxLayout()
        layout.addLayout(body, 1)

        # -- girisler ----------------------------------------------------
        entries = QGroupBox(tr("Onyukleme girisleri"))
        entries_layout = QVBoxLayout(entries)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("Sira"), tr("Giris"), tr("Aciklama"),
                                   tr("Aygit yolu")])
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tree.itemChanged.connect(self._entry_toggled)
        self.tree.currentItemChanged.connect(self._selection_changed)
        entries_layout.addWidget(self.tree)

        row = QHBoxLayout()
        self.btn_up = QPushButton(app_icon("boot-order"), tr("Yukari"))
        self.btn_up.clicked.connect(lambda: self._move(-1))
        row.addWidget(self.btn_up)
        self.btn_down = QPushButton(app_icon("boot-order"), tr("Asagi"))
        self.btn_down.clicked.connect(lambda: self._move(1))
        row.addWidget(self.btn_down)
        self.btn_rename = QPushButton(app_icon("rename"), tr("Adi degistir..."))
        self.btn_rename.clicked.connect(self._rename)
        row.addWidget(self.btn_rename)
        self.btn_delete = QPushButton(app_icon("trash"), tr("Girisi sil"))
        self.btn_delete.clicked.connect(self._delete)
        row.addWidget(self.btn_delete)
        row.addStretch(1)
        entries_layout.addLayout(row)
        body.addWidget(entries, 1)

        # -- yan panel ---------------------------------------------------
        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)

        info_box = QGroupBox(tr("Bellenim"))
        self.info_form = QFormLayout(info_box)
        side_layout.addWidget(info_box)

        settings = QGroupBox(tr("Ayarlar"))
        settings_form = QFormLayout(settings)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(0, 65535)
        self.timeout_spin.setSuffix(tr(" saniye"))
        self.timeout_spin.valueChanged.connect(self._timeout_changed)
        settings_form.addRow(tr("Menu bekleme:"), self.timeout_spin)
        self.next_combo = QComboBox()
        self.next_combo.currentIndexChanged.connect(self._boot_next_changed)
        settings_form.addRow(tr("Sonraki acilis:"), self.next_combo)
        side_layout.addWidget(settings)

        detail_box = QGroupBox(tr("Secili giris"))
        detail_layout = QVBoxLayout(detail_box)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        detail_layout.addWidget(self.detail)
        side_layout.addWidget(detail_box, 1)

        backup_box = QGroupBox(tr("Yedek"))
        backup_layout = QVBoxLayout(backup_box)
        self.btn_export = QPushButton(app_icon("export"), tr("Disa aktar..."))
        self.btn_export.clicked.connect(self.export_backup)
        backup_layout.addWidget(self.btn_export)
        self.btn_import = QPushButton(app_icon("import"),
                                      tr("Yedek dosyasini ac..."))
        self.btn_import.clicked.connect(self.open_backup)
        backup_layout.addWidget(self.btn_import)
        side_layout.addWidget(backup_box)

        body.addWidget(side)

        buttons = QDialogButtonBox()
        self.btn_apply = buttons.addButton(tr("Bellenime yaz..."),
                                           QDialogButtonBox.ApplyRole)
        self.btn_apply.setIcon(app_icon("efi"))
        self.btn_apply.clicked.connect(self.apply_changes)
        self.btn_revert = buttons.addButton(tr("Degisiklikleri geri al"),
                                            QDialogButtonBox.ResetRole)
        self.btn_revert.clicked.connect(self.reload)
        close = buttons.addButton(QDialogButtonBox.Close)
        close.setText(tr("Kapat"))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    # Yukleme
    # ------------------------------------------------------------------
    def reload(self) -> None:
        """Duzeni bellenimden yeniden okur ve degisiklikleri atar."""
        ok, result = run_task(self, tr("Onyukleme duzeni okunuyor"),
                              lambda report: efistore.load(progress=report))
        if not ok:
            QMessageBox.warning(self, tr("Okunamadi"), str(result))
            return
        self.adopt(result)

    def adopt(self, state: efistore.BootState) -> None:
        """Okunan durumu benimser; duzenlenen kopya ayri tutulur.

        `original` karsilastirma icin **dokunulmadan** saklanir: ne
        degistigini bilmenin tek yolu budur, "kirli" bayraklari tutmak
        degil.
        """
        self.original = state
        self.state = copy.deepcopy(state)
        self._fill()

    def _fill(self) -> None:
        state = self.state
        if state is None:
            return
        self.tree.blockSignals(True)
        self.tree.clear()
        for position, option in enumerate(state.ordered("Boot"), 1):
            item = QTreeWidgetItem(self.tree)
            in_order = option.number in state.boot_order
            item.setText(0, str(position) if in_order else "-")
            item.setText(1, option.name)
            item.setText(2, option.description or tr("(adsiz)"))
            item.setText(3, option.path_text)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Checked if option.active else Qt.Unchecked)
            item.setData(0, Qt.UserRole, option.number)
            if option.number == state.boot_current:
                item.setIcon(1, app_icon("boot"))
                item.setToolTip(1, tr("Bu makine su an bu giristen acildi"))
            if not in_order:
                item.setToolTip(0, tr("Giris var ama onyukleme sirasinda "
                                      "degil; bellenim onu denemez."))
        self.tree.blockSignals(True)
        self.tree.blockSignals(False)

        while self.info_form.rowCount():
            self.info_form.removeRow(0)
        for key, value in state.summary().items():
            label = QLabel(value)
            label.setWordWrap(True)
            self.info_form.addRow(f"{key}:", label)

        orphans = state.orphans("Boot")
        if orphans:
            warning = QLabel(tr("Sirada karsiligi olmayan {} giris var: {}",
                                len(orphans),
                                ", ".join(f"{n:04X}" for n in orphans)))
            warning.setWordWrap(True)
            self.info_form.addRow(tr("Uyari:"), warning)

        self.timeout_spin.blockSignals(True)
        self.timeout_spin.setValue(state.timeout or 0)
        self.timeout_spin.blockSignals(False)

        self.next_combo.blockSignals(True)
        self.next_combo.clear()
        self.next_combo.addItem(tr("(ayarli degil)"), None)
        for option in state.ordered("Boot"):
            self.next_combo.addItem(f"{option.name} — {option.description}",
                                    option.number)
        if state.boot_next is not None:
            index = self.next_combo.findData(state.boot_next)
            if index >= 0:
                self.next_combo.setCurrentIndex(index)
        self.next_combo.blockSignals(False)

        editable = state.writable
        for widget in (self.btn_up, self.btn_down, self.btn_delete,
                       self.btn_rename, self.timeout_spin, self.next_combo,
                       self.btn_apply):
            widget.setEnabled(editable)
            if not editable:
                widget.setToolTip(state.reason)
        self.tree.setEnabled(True)

        source = (tr("calisan makine") if state.source == "firmware"
                  else tr("yedek dosyasi"))
        if state.readable:
            self.header.setText(
                tr("<b>{} giris</b> okundu ({}). {}",
                   len(state.boot_entries), source,
                   tr("Degisiklikler <b>yazana kadar</b> uygulanmaz.")
                   if editable else state.reason))
        else:
            self.header.setText(tr("<b>Onyukleme duzeni okunamadi.</b> {}",
                                   state.reason))
        self._selection_changed()

    # ------------------------------------------------------------------
    # Duzenleme
    # ------------------------------------------------------------------
    def _current_number(self) -> Optional[int]:
        item = self.tree.currentItem()
        return None if item is None else item.data(0, Qt.UserRole)

    def _selection_changed(self, *args) -> None:
        number = self._current_number()
        state = self.state
        if state is None or number is None:
            self.detail.setPlainText("")
            return
        option = state.boot_entries.get(number)
        if option is None:
            self.detail.setPlainText("")
            return
        lines = [
            f"{tr('Degisken')}: {option.name}",
            f"{tr('Aciklama')}: {option.description}",
            f"{tr('Oznitelik')}: {option.attributes:#010x}"
            + (f"  {tr('etkin')}" if option.active else f"  {tr('etkin degil')}")
            + (f"  {tr('gizli')}" if option.hidden else ""),
            f"{tr('Aygit yolu')}: {option.path_text}",
        ]
        if option.file_path:
            lines.append(f"{tr('Dosya')}: {option.file_path}")
        partition = option.partition
        if partition:
            lines.append(f"{tr('Bolum')}: {partition['number']} "
                         f"({partition['scheme'].upper()}) "
                         f"LBA {partition['start_lba']}")
            if partition["guid"]:
                lines.append(f"GUID: {partition['guid']}")
        if option.optional_text:
            lines.append(f"{tr('Ek veri')}: {option.optional_text}")
        lines.append("")
        lines.append(tr("Yol dugumleri:"))
        for node in option.path_nodes:
            if node.is_end:
                continue
            lines.append(f"  {node.describe()}: {node.text()}")
        self.detail.setPlainText("\n".join(lines))

    def _entry_toggled(self, item: QTreeWidgetItem, column: int) -> None:
        """Isaret kutusu girisin **etkin** bayragini degistirir."""
        if column != 0 or self.state is None:
            return
        number = item.data(0, Qt.UserRole)
        option = self.state.boot_entries.get(number)
        if option is None:
            return
        wanted = item.checkState(0) == Qt.Checked
        if not self.state.writable:
            item.setCheckState(0, Qt.Checked if option.active else Qt.Unchecked)
            return
        option.active = wanted

    def _move(self, delta: int) -> None:
        """Secili girisi onyukleme sirasinda tasir."""
        number = self._current_number()
        if number is None or self.state is None:
            return
        order = list(self.state.boot_order)
        if number not in order:
            QMessageBox.information(
                self, tr("Sirada degil"),
                tr("Bu giris onyukleme sirasinda yer almiyor, bu yuzden "
                   "tasinamaz."))
            return
        index = order.index(number)
        target = index + delta
        if not (0 <= target < len(order)):
            return
        order.insert(target, order.pop(index))
        self.state.boot_order = order
        self._fill()
        self._select(number)

    def _select(self, number: int) -> None:
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            if item.data(0, Qt.UserRole) == number:
                self.tree.setCurrentItem(item)
                return

    def _rename(self) -> None:
        from PyQt5.QtWidgets import QInputDialog

        number = self._current_number()
        if number is None or self.state is None:
            return
        option = self.state.boot_entries.get(number)
        if option is None:
            return
        text, ok = QInputDialog.getText(self, tr("Giris adi"),
                                        tr("Menude gorunecek ad:"),
                                        QLineEdit.Normal, option.description)
        if not ok:
            return
        option.description = text
        self._fill()
        self._select(number)

    def _delete(self) -> None:
        number = self._current_number()
        if number is None or self.state is None:
            return
        option = self.state.boot_entries.get(number)
        if option is None:
            return
        answer = QMessageBox.warning(
            self, tr("Girisi sil"),
            tr("<b>{}</b> ({}) girisi silinecek.<br><br>Bu giristen acilan "
               "sistem bellenim menusunde artik gorunmez. Diskteki dosyalar "
               "silinmez.<br><br>Degisiklik yazilana kadar uygulanmaz.",
               option.description or option.name, option.name),
            QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Cancel)
        if answer != QMessageBox.Ok:
            return
        self.state.boot_entries.pop(number, None)
        self.state.boot_order = [n for n in self.state.boot_order if n != number]
        if self.state.boot_next == number:
            self.state.boot_next = None
        self._fill()

    def _timeout_changed(self, value: int) -> None:
        if self.state is not None:
            self.state.timeout = value

    def _boot_next_changed(self, index: int) -> None:
        if self.state is not None:
            self.state.boot_next = self.next_combo.itemData(index)

    # ------------------------------------------------------------------
    # Yedek
    # ------------------------------------------------------------------
    def export_backup(self) -> Optional[str]:
        if self.state is None:
            return None
        path, _ = QFileDialog.getSaveFileName(
            self, tr("Onyukleme duzenini disa aktar"),
            "uefi-onyukleme-yedegi.json", BACKUP_FILTER)
        if not path:
            return None
        try:
            # Yedek **okunan** durumdan alinir, duzenlenenden degil:
            # amac degisiklikten once neyin var oldugunu saklamaktir.
            efistore.save_backup(self.original or self.state, path)
        except OSError as exc:
            QMessageBox.warning(self, tr("Yazilamadi"), str(exc))
            return None
        QMessageBox.information(self, tr("Yedek alindi"),
                                tr("Onyukleme duzeni kaydedildi:\n{}", path))
        return path

    def open_backup(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Yedek dosyasini ac"), "", BACKUP_FILTER)
        if not path:
            return
        try:
            state = efistore.load_backup(path)
        except (OSError, ValueError, efistore.EfiStoreError) as exc:
            QMessageBox.warning(self, tr("Okunamadi"), str(exc))
            return
        self.adopt(state)
        QMessageBox.information(
            self, tr("Yedek acildi"),
            tr("Yedek dosyasi goruntuleniyor. Yedekten dogrudan bellenime "
               "yazilamaz; once calisan makinenin duzenini okuyun."))

    # ------------------------------------------------------------------
    # Yazma
    # ------------------------------------------------------------------
    def apply_changes(self) -> None:
        """Degisiklikleri listeler, yedek alir ve bellenime yazar."""
        if self.state is None or self.original is None:
            return
        if not self.state.writable:
            QMessageBox.information(self, tr("Yazilamiyor"), self.state.reason)
            return
        plan = efistore.changes(self.original, self.state)
        if not plan:
            QMessageBox.information(self, tr("Degisiklik yok"),
                                    tr("Yazilacak bir degisiklik bulunmuyor."))
            return

        rows = "<br>".join(
            (f"<b>{c.description}</b>" if c.destructive else c.description)
            for c in plan)
        answer = QMessageBox.warning(
            self, tr("Bellenime yaz"),
            tr("Asagidaki <b>{} degisiklik</b> anakartin kalici bellegine "
               "yazilacak:<br><br>{}<br><br>"
               "Yanlis bir onyukleme duzeni makineyi <b>acilmaz</b> hale "
               "getirebilir. Yazmadan once yedek dosyasi istenecek.",
               len(plan), rows),
            QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Cancel)
        if answer != QMessageBox.Ok:
            return

        if not self.export_backup():
            QMessageBox.information(
                self, tr("Yazilmadi"),
                tr("Yedek alinmadigi icin hicbir sey yazilmadi."))
            return

        def work(report):
            return efistore.apply(plan, progress=report)

        ok, result = run_task(self, tr("Bellenime yaziliyor"), work)
        if not ok:
            QMessageBox.critical(self, tr("Yazilamadi"), str(result))
            self.reload()
            return
        if result.ok:
            QMessageBox.information(self, tr("Yazildi"), result.summary())
        else:
            QMessageBox.critical(self, tr("Kismen yazildi"), result.summary())
        self.reload()


def show_efi_boot(parent) -> None:
    """Bellenim duzenini okur, sonra UEFI duzenleyicisini acar.

    Okuma her degiskeni tek tek sorar ve bazi bellenimlerde yavastir; bu
    yuzden pencereden once, ilerleme gostererek yapilir (ADR 0020).
    """
    ok, state = run_task(parent, tr("Onyukleme duzeni okunuyor"),
                         lambda report: efistore.load(progress=report))
    if not ok:
        QMessageBox.warning(parent, tr("Okunamadi"), str(state))
        return
    dialog = EfiBootDialog(parent)
    dialog.adopt(state)
    exec_dialog(dialog)
