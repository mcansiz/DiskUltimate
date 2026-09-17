"""Onyukleyici yoneticisi — hangi diskte ne acilir, GRUB nerede.

`exxos-easy-grub-manager`in yaptigi isi DiskUltimate'in kendi katmanlariyla
yapar. Iki yerde bilerek ayrilir:

1. **Inceleme her platformda calisir.** Sol listedeki isletim sistemleri
   `core/bootloader.py` ile, projenin kendi dosya sistemi surucileri uzerinden
   bulunur; `mount` ve root gerekmez, goruntu dosyalari da taranir.
2. **Onyukleme kodunu kaldirmak kuyruga girer.** Tiklandigi anda diske
   yazilmaz (ADR 0025); bekleyen islem olarak listelenir ve "Uygula" ile,
   fiziksel diskin butun koruma katmanlarindan gecerek calisir.

GRUB **kurulumu** kuyruga girmez: `grub-install` acik diske degil calisan
sisteme is yaptirir (gerekce `core/grub.py` basligi).
"""
from __future__ import annotations

from typing import List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFontDatabase
from PyQt5.QtWidgets import (QAbstractItemView, QCheckBox, QDialog,
                             QDialogButtonBox, QFormLayout, QGroupBox,
                             QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                             QMessageBox, QPlainTextEdit, QPushButton,
                             QSplitter, QTreeWidget, QTreeWidgetItem,
                             QVBoxLayout, QWidget)

from ...core import bootloader as bl
from ...core import grub as grub_mod
from ...core import operations as ops
from ...core.ptable import human_size
from ...i18n import mark, tr
from ..icons import icon as app_icon
from .base import exec_dialog
from .task import run_task


class BootloaderDialog(QDialog):
    """Acik disk uzerinde onyukleme durumu ve GRUB islemleri."""

    def __init__(self, parent, session, queue_callback=None):
        super().__init__(parent)
        self.session = session
        self.queue_callback = queue_callback
        self.survey: Optional[bl.DiskBoot] = None
        self.status: Optional[grub_mod.GrubStatus] = None
        self.setWindowTitle(tr("Onyukleyici Yoneticisi"))
        self.setMinimumSize(880, 560)
        self._build()
        # Inceleme burada **calistirilmaz**: yapici icinde modal bir ilerleme
        # penceresi acmak, henuz gosterilmemis bir pencerenin uzerine pencere
        # koymak olur. Cagiran once inceler, sonra `adopt()` ile verir
        # (bkz. `show_bootloader`); "Yenile" dugmesi pencere acikken calisir.

    # ------------------------------------------------------------------
    # Kurulum
    # ------------------------------------------------------------------
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        self.header = QLabel()
        self.header.setWordWrap(True)
        layout.addWidget(self.header)

        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter, 1)

        # -- sol: bulunan isletim sistemleri -----------------------------
        left = QGroupBox(tr("Diskteki isletim sistemleri"))
        left_layout = QVBoxLayout(left)
        self.os_tree = QTreeWidget()
        self.os_tree.setHeaderLabels([tr("Bolum"), tr("Isletim sistemi"),
                                      tr("Dosya sistemi"), tr("Boyut")])
        self.os_tree.setRootIsDecorated(False)
        self.os_tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.os_tree.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        left_layout.addWidget(self.os_tree)
        splitter.addWidget(left)

        # -- sag: onyukleme durumu ve islemler ---------------------------
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        state_box = QGroupBox(tr("Onyukleme durumu"))
        self.state_form = QFormLayout(state_box)
        right_layout.addWidget(state_box)

        actions = QGroupBox(tr("Islemler"))
        action_layout = QVBoxLayout(actions)

        self.btn_clear = QPushButton(app_icon("bootloader"),
                                     tr("Onyukleme kodunu kaldir..."))
        self.btn_clear.setToolTip(
            tr("Ilk 440 bayti sifirlar; bolum tablosu ve veriler korunur. "
               "Bekleyen islem olarak kuyruga eklenir."))
        self.btn_clear.clicked.connect(self.queue_clear_boot_code)
        action_layout.addWidget(self.btn_clear)

        self.btn_install = QPushButton(app_icon("bootloader"),
                                       tr("GRUB'u bu diske kur..."))
        self.btn_install.clicked.connect(self.install_grub)
        action_layout.addWidget(self.btn_install)

        self.btn_update = QPushButton(app_icon("refresh"),
                                      tr("Onyukleme menusunu yeniden uret"))
        self.btn_update.clicked.connect(self.update_menu)
        action_layout.addWidget(self.btn_update)

        self.btn_repair = QPushButton(app_icon("shield"), tr("Tumunu onar..."))
        self.btn_repair.setToolTip(
            tr("Yedek alir, diger sistemlerin taranmasini acar, GRUB'u zaten "
               "bulundugu diske yeniden kurar ve menuyu uretir."))
        self.btn_repair.clicked.connect(self.repair_all)
        action_layout.addWidget(self.btn_repair)

        row = QHBoxLayout()
        self.btn_backup = QPushButton(app_icon("backup"), tr("Ayarlari yedekle"))
        self.btn_backup.clicked.connect(self.backup_settings)
        row.addWidget(self.btn_backup)
        self.btn_restore = QPushButton(app_icon("import"), tr("Yedegi geri yukle..."))
        self.btn_restore.clicked.connect(self.restore_settings)
        row.addWidget(self.btn_restore)
        action_layout.addLayout(row)

        self.chk_prober = QCheckBox(
            tr("Diger isletim sistemlerini tara (os-prober)"))
        self.chk_prober.setToolTip(
            tr("Debian 12 ve Ubuntu 22.04'ten beri kapali gelir; kapaliyken "
               "yanindaki Windows menude gorunmez."))
        self.chk_prober.clicked.connect(self.toggle_os_prober)
        action_layout.addWidget(self.chk_prober)

        right_layout.addWidget(actions)
        right_layout.addStretch(1)
        splitter.addWidget(right)
        splitter.setSizes([520, 360])

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(120)
        self.log.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        layout.addWidget(self.log)

        buttons = QDialogButtonBox()
        self.btn_refresh = buttons.addButton(tr("Yenile"),
                                             QDialogButtonBox.ActionRole)
        self.btn_refresh.setIcon(app_icon("refresh"))
        self.btn_refresh.clicked.connect(self.refresh)
        close = buttons.addButton(QDialogButtonBox.Close)
        close.setText(tr("Kapat"))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    # Gunluk
    # ------------------------------------------------------------------
    def note(self, message: str) -> None:
        self.log.appendPlainText(message)

    # ------------------------------------------------------------------
    # Tazeleme
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """Diski inceler ve arayuzu doldurur.

        Inceleme her bolumu acip dizin okur; NTFS ve ext'te bu uzun surebilir,
        bu yuzden arayuz is parcaciginda degil `run_task` icinde calisir
        (ADR 0020).
        """
        session = self.session

        def work(report):
            return bl.survey_session(session, progress=report)

        ok, result = run_task(self, tr("Onyukleme durumu inceleniyor"), work)
        if not ok:
            self.note(tr("Inceleme basarisiz: {}", result))
            return
        self.adopt(result, grub_mod.status())

    def adopt(self, survey: bl.DiskBoot,
              status: Optional[grub_mod.GrubStatus] = None) -> None:
        """Hazir inceleme sonucunu benimser ve arayuzu doldurur."""
        self.survey = survey
        self.status = status if status is not None else grub_mod.status()
        self._fill_systems()
        self._fill_state()
        self._update_buttons()

    def _fill_systems(self) -> None:
        self.os_tree.clear()
        if not self.survey:
            return
        for entry in self.survey.systems:
            item = QTreeWidgetItem(self.os_tree)
            # `entry.index` zaten 1 tabanlidir (bkz. bootloader.OsInstall)
            item.setText(0, tr("Bolum {}", entry.index))
            if entry.os_name:
                item.setText(1, entry.os_name)
                item.setIcon(1, app_icon("bootloader"))
            else:
                # Neden atlandigi **gosterilir**: "listede yok" demek,
                # kullanicinin aradigi sistemi neden goremedigini anlatmaz.
                item.setText(1, entry.reason or tr("-"))
                item.setDisabled(True)
            label = f"{entry.fs_type}" + (f" · {entry.label}" if entry.label
                                          else "")
            if entry.is_esp:
                label += tr(" · EFI")
            item.setText(2, label)
            item.setText(3, human_size(entry.size) if entry.size else "-")
            item.setData(0, Qt.UserRole, entry.index)
            for loader in entry.loaders:
                child = QTreeWidgetItem(item)
                child.setText(1, loader)
                child.setIcon(1, app_icon("efi"))
            if entry.loaders:
                self.os_tree.setRootIsDecorated(True)
                item.setExpanded(True)

    def _fill_state(self) -> None:
        while self.state_form.rowCount():
            self.state_form.removeRow(0)
        if not self.survey:
            return
        code = self.survey.boot_code
        self.state_form.addRow(tr("Onyukleme kodu:"), QLabel(code.label))
        self.state_form.addRow(tr("Bolum tablosu:"),
                               QLabel(self.session.scheme_name))
        esp = (tr("Bolum {}", self.survey.esp_index)
               if self.survey.esp_index >= 0 else tr("yok"))
        self.state_form.addRow(tr("EFI Sistem Bolumu:"), QLabel(esp))
        if self.status:
            for key, value in self.status.summary().items():
                label = QLabel(value)
                label.setWordWrap(True)
                self.state_form.addRow(f"{key}:", label)
            self.chk_prober.setChecked(self.status.os_prober_enabled)

        source = (tr("goruntu dosyasi") if not self.session.is_physical
                  else tr("fiziksel disk"))
        self.header.setText(
            tr("<b>{}</b> ({}) — {}", self.session.name, source,
               self.survey.summary()))

    def _update_buttons(self) -> None:
        """Calismayacak dugmeler **pasif** ve nedeni ipucunda olur."""
        available = bool(self.status and self.status.available)
        reason = self.status.reason if self.status else ""
        for button in (self.btn_install, self.btn_update, self.btn_repair,
                       self.chk_prober, self.btn_restore):
            button.setEnabled(available)
            if not available:
                button.setToolTip(reason)
        self.btn_backup.setEnabled(bool(self.status
                                        and self.status.defaults_readable))
        writable = not self.session.readonly or self.session.is_physical
        self.btn_clear.setEnabled(True)
        if not writable:
            self.btn_clear.setToolTip(self.session.readonly_reason)

    # ------------------------------------------------------------------
    # Kuyruga giren islem
    # ------------------------------------------------------------------
    def queue_clear_boot_code(self) -> None:
        """Onyukleme kodunun silinmesini **bekleyen islem** olarak ekler."""
        if self.queue_callback is None:
            return
        code = self.survey.boot_code if self.survey else bl.BootCode()
        answer = QMessageBox.warning(
            self, tr("Onyukleme kodunu kaldir"),
            tr("<b>{}</b> diskindeki onyukleme kodu ({}) kaldirilacak.<br><br>"
               "Bolum tablosu ve dosyalar <b>korunur</b>; ama bu diskten acilan "
               "sistem artik acilmaz.<br><br>"
               "Islem hemen yapilmaz: bekleyen islemlere eklenir ve "
               "\"Uygula\" dediginizde calisir.",
               self.session.name, code.label),
            QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Cancel)
        if answer != QMessageBox.Ok:
            return
        operation = ops.Operation(
            kind="clear_boot_code",
            title_text=mark("Onyukleme kodunu kaldir"), title_args=(),
            detail_text=mark("{} — ilk 440 bayt sifirlanir"),
            detail_args=(code.label,),
            target_text=mark("Disk"))
        self.queue_callback(operation)
        self.note(tr("Bekleyen islemlere eklendi: onyukleme kodunu kaldir"))

    # ------------------------------------------------------------------
    # Sistem araclarini calistiran islemler
    # ------------------------------------------------------------------
    def _target_device(self) -> str:
        """GRUB'un kurulacagi aygit yolu (yalnizca fiziksel diskte anlamli)."""
        info = self.session.disk_info
        return getattr(info, "path", "") if info is not None else ""

    def install_grub(self) -> None:
        device = self._target_device()
        if not device:
            QMessageBox.information(
                self, tr("Fiziksel disk gerekli"),
                tr("GRUB yalnizca gercek bir diske kurulabilir. Once sol "
                   "agactan bir fiziksel disk acin."))
            return
        answer = QMessageBox.question(
            self, tr("GRUB kur"),
            tr("GRUB <b>{}</b> diskine kurulacak.<br><br>Diskin ilk sektoru "
               "degisir. Devam edilsin mi?", device),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return

        def work(report):
            return grub_mod.install(device, progress=report)

        ok, result = run_task(self, tr("GRUB kuruluyor"), work)
        self._report(ok, result)

    def update_menu(self) -> None:
        def work(report):
            return grub_mod.update_config(progress=report)

        ok, result = run_task(self, tr("Onyukleme menusu uretiliyor"), work)
        self._report(ok, result)

    def toggle_os_prober(self) -> None:
        wanted = self.chk_prober.isChecked()
        ok, message = grub_mod.set_os_prober(wanted)
        self.note(message or (tr("os-prober acildi") if wanted
                              else tr("os-prober kapatildi")))
        if not ok:
            self.chk_prober.setChecked(not wanted)
            QMessageBox.warning(self, tr("Degistirilemedi"), message)
            return
        self.note(tr("Degisikligin etkili olmasi icin menuyu yeniden uretin."))

    def backup_settings(self) -> None:
        def work(report):
            report(tr("Yapilandirma yedekleniyor..."), -1)
            return grub_mod.backup_config()

        ok, result = run_task(self, tr("Yedekleniyor"), work)
        if not ok:
            QMessageBox.warning(self, tr("Yedeklenemedi"), str(result))
            return
        skipped = result.get("skipped") or []
        self.note(tr("Yedek alindi: {}", result.get("path", "")))
        for item in skipped:
            self.note(tr("  atlandi: {} ({})", item.get("path", ""),
                         item.get("reason", "")))
        QMessageBox.information(
            self, tr("Yedek alindi"),
            tr("{} oge yedeklendi.\n\nKonum: {}",
               len(result.get("files") or []), result.get("path", "")))

    def restore_settings(self) -> None:
        backups = grub_mod.list_backups()
        if not backups:
            QMessageBox.information(
                self, tr("Yedek yok"),
                tr("Kayitli yedek bulunamadi. Once \"Ayarlari yedekle\"yi "
                   "kullanin.\n\nKonum: {}", grub_mod.backup_root()))
            return
        rows = [f"{b.get('date', b['stamp'])}  [{b['stamp']}]" for b in backups]
        choice, ok = QInputDialog.getItem(self, tr("Yedegi geri yukle"),
                                          tr("Geri yuklenecek yedek:"), rows,
                                          0, False)
        if not ok:
            return
        stamp = backups[rows.index(choice)]["stamp"]
        answer = QMessageBox.warning(
            self, tr("Geri yukleme onayi"),
            tr("Su anki GRUB yapilandirmasi <b>{}</b> yedegiyle "
               "degistirilecek.<br><br>Mevcut ayarlar kaybolur.", stamp),
            QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Cancel)
        if answer != QMessageBox.Ok:
            return

        def work(report):
            report(tr("Yedek geri yukleniyor..."), -1)
            return grub_mod.restore_config(stamp)

        done, result = run_task(self, tr("Geri yukleniyor"), work)
        self._report(done, result)

    def repair_all(self) -> None:
        """Tek dugmeyle onarim.

        Hedef, GRUB'un **zaten bulundugu** disklerdir. Makinedeki her diske
        GRUB yazmak onarim degil, calisan diskleri degistirmektir.
        """
        targets = self._repair_targets()
        if not targets:
            QMessageBox.information(
                self, tr("Onarilacak disk yok"),
                tr("GRUB'un kurulu oldugu bir disk bulunamadi.\n\n"
                   "Hedefi kendiniz secip \"GRUB'u bu diske kur\" "
                   "kullanabilirsiniz."))
            return
        answer = QMessageBox.question(
            self, tr("Tumunu onar"),
            tr("Sirasiyla su adimlar uygulanacak:<br>"
               "1. Mevcut ayarlar yedeklenir<br>"
               "2. Diger sistemlerin taranmasi acilir<br>"
               "3. GRUB <b>{}</b> diskine yeniden kurulur<br>"
               "4. Onyukleme menusu yeniden uretilir<br><br>"
               "Baska hicbir diske dokunulmaz. Devam edilsin mi?",
               ", ".join(targets)),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return

        def work(report):
            return grub_mod.repair(targets, progress=report)

        ok, result = run_task(self, tr("Onariliyor"), work)
        if not ok:
            QMessageBox.warning(self, tr("Onarim basarisiz"), str(result))
            return
        succeeded, steps = result
        for step in steps:
            self.note(("[+] " if step.ok else "[!] ") + step.title
                      + (f" — {step.detail}" if step.detail else ""))
        if succeeded:
            QMessageBox.information(self, tr("Onarim tamamlandi"),
                                    tr("Onyukleme menusu yeniden uretildi."))
        else:
            QMessageBox.warning(
                self, tr("Onarim eksik kaldi"),
                tr("Bazi adimlar basarisiz oldu; ayrintilar asagidaki "
                   "gunlukte."))
        self.refresh()

    def _repair_targets(self) -> List[str]:
        """Onarilacak diskler: acik disk GRUB tasiyorsa yalnizca o."""
        device = self._target_device()
        if device and self.survey and self.survey.has_grub:
            return [device]
        return [device] if device else []

    def _report(self, ok: bool, result) -> None:
        """Ortak sonuc gosterimi: (basarili_mi, ileti) ciftleri icin."""
        if not ok:
            self.note(tr("Hata: {}", result))
            QMessageBox.warning(self, tr("Islem basarisiz"), str(result))
            return
        succeeded, message = result if isinstance(result, tuple) else (True,
                                                                       str(result))
        for line in str(message).splitlines():
            self.note("  " + line)
        if succeeded:
            self.note(tr("Tamamlandi."))
        else:
            QMessageBox.warning(self, tr("Islem basarisiz"), str(message))
        self.refresh()


def show_bootloader(parent, session, queue_callback=None) -> None:
    """Diski inceler, sonra onyukleyici yoneticisini acar.

    Inceleme **pencereden once** yapilir: her bolum acilip dizin okunur ve bu
    uzun surebilir. Bos bir pencere gosterip icini doldurmak yerine ilerleme
    penceresi gosterilir (ADR 0020/0021).
    """
    ok, survey = run_task(parent, tr("Onyukleme durumu inceleniyor"),
                          lambda report: bl.survey_session(session,
                                                           progress=report))
    if not ok:
        QMessageBox.warning(parent, tr("Inceleme basarisiz"), str(survey))
        return
    dialog = BootloaderDialog(parent, session, queue_callback)
    dialog.adopt(survey)
    exec_dialog(dialog)
