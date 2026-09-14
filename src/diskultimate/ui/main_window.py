"""DiskUltimate ana penceresi."""
from __future__ import annotations

import datetime
import os
from typing import Dict, List, Optional, Tuple

from PyQt5.QtCore import QSize, Qt, QTimer
from PyQt5.QtGui import QColor, QFontDatabase, QIcon, QKeySequence
from PyQt5.QtWidgets import (QAction, QActionGroup, QApplication, QFileDialog,
                             QFormLayout, QHBoxLayout, QHeaderView,
                             QInputDialog, QLabel, QMainWindow, QMenu,
                             QMessageBox, QPlainTextEdit, QSplitter, QStyle,
                             QTabWidget, QToolBar, QTreeWidget, QTreeWidgetItem,
                             QVBoxLayout, QWidget)

from ..core.formatter import FS_BY_KEY
from ..core.physical import AccessDeniedError, PhysicalDiskError, SystemDiskError
from ..core.platform import IMAGE_EXTENSIONS, PLATFORM_NAME
from ..core.platform import summary as platform_summary
from ..core.vdisk import VhdImage
from ..paths import LOG_DIR
from ..core.image import DiskImage
from ..core.ptable import (GPT_TYPES, MBR_TYPES, FreeRegion, Partition,
                           human_size, parse_size)
from ..core.session import DiskSession, SessionError
from .dialogs.base import exec_dialog
from .dialogs.new_image import NewImageDialog
from .dialogs.partition import CreatePartitionDialog, FormatDialog
from .dialogs.resize import ResizePartitionDialog
from .dialogs.task import run_task
from .dialogs.tools import (CarveOptionsDialog, CarvedFilesDialog,
                            DeletedFilesDialog, InfoDialog,
                            LostPartitionsDialog, WipeDialog)
from .theme import fs_color, os_icon, palette_color
from .widgets.disk_map import DiskMapWidget
from .widgets.file_browser import FileBrowser
from .widgets.hex_view import HexViewer
from .widgets.partition_table import PartitionTableWidget, color_chip

APP_NAME = "DiskUltimate"
APP_VERSION = "0.1.0"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # Birden fazla goruntu/disk ayni anda acik kalabilir; `session` bunlardan
        # etkin olanidir, `sessions` tumunu tutar (agacta alt alta gosterilir).
        self.sessions: List[DiskSession] = []
        self.session: Optional[DiskSession] = None
        self.selected_partition: Optional[int] = None
        self.selected_free: Optional[Tuple[int, int]] = None
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 800)
        self._physical_cache: Dict[str, object] = {}
        self._build_ui()
        self._build_actions()
        self._build_tree([])        # acik goruntu olmadan da diskler listelenir
        self.info_view.setPlainText(self._physical_summary_text())
        self._update_actions()
        self.log(f"{APP_NAME} {APP_VERSION} baslatildi")

    # ==================================================================
    # Arayuz kurulumu
    # ==================================================================
    def _icon(self, standard) -> QIcon:
        return self.style().standardIcon(standard)

    def _build_ui(self) -> None:
        merkez = QWidget()
        self.setCentralWidget(merkez)
        duzen = QVBoxLayout(merkez)
        duzen.setContentsMargins(0, 0, 0, 0)
        duzen.setSpacing(0)

        ana_splitter = QSplitter(Qt.Horizontal)
        duzen.addWidget(ana_splitter, 1)

        # --- sol: disk agaci ---
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("Disk ve Bolumler")
        self.tree.setMinimumWidth(220)
        self.tree.setMaximumWidth(360)
        self.tree.itemClicked.connect(self._tree_clicked)
        self.tree.itemDoubleClicked.connect(self._tree_double_clicked)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_context)
        ana_splitter.addWidget(self.tree)

        # --- sag: harita + tablo + sekmeler ---
        sag = QWidget()
        sag_duzen = QVBoxLayout(sag)
        sag_duzen.setContentsMargins(6, 6, 6, 6)
        sag_duzen.setSpacing(6)

        self.disk_map = DiskMapWidget()
        self.disk_map.partitionSelected.connect(self.select_partition)
        self.disk_map.freeSelected.connect(self.select_free)
        self.disk_map.partitionActivated.connect(lambda i: self.tabs.setCurrentIndex(0))
        self.disk_map.freeActivated.connect(lambda s, n: self.create_partition())
        self.disk_map.contextMenuRequested.connect(self._map_context)
        sag_duzen.addWidget(self.disk_map)

        alt_splitter = QSplitter(Qt.Vertical)
        self.part_table = PartitionTableWidget()
        self.part_table.setMinimumHeight(120)
        self.part_table.partitionSelected.connect(self.select_partition)
        self.part_table.freeSelected.connect(self.select_free)
        self.part_table.partitionActivated.connect(lambda i: self.tabs.setCurrentIndex(0))
        self.part_table.contextMenuRequested.connect(self._table_context)
        alt_splitter.addWidget(self.part_table)

        self.tabs = QTabWidget()
        self.browser = FileBrowser()
        self.browser.statusMessage.connect(self.log)
        self.browser.contentChanged.connect(self._content_changed)
        self.tabs.addTab(self.browser, "Dosya Gezgini")

        self.info_view = QPlainTextEdit()
        self.info_view.setReadOnly(True)
        mono = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        mono.setPointSize(9)
        self.info_view.setFont(mono)
        self.tabs.addTab(self.info_view, "Bolum Bilgisi")

        self.hex_view = HexViewer()
        self.tabs.addTab(self.hex_view, "Onaltilik Goruntuleyici")

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(mono)
        self.tabs.addTab(self.log_view, "Islem Gunlugu")
        alt_splitter.addWidget(self.tabs)
        alt_splitter.setSizes([180, 420])
        sag_duzen.addWidget(alt_splitter, 1)
        ana_splitter.addWidget(sag)
        ana_splitter.setSizes([250, 1030])

        # --- durum cubugu ---
        self.status_file = QLabel("Disk goruntusu acik degil")
        self.status_scheme = QLabel("")
        self.status_sel = QLabel("")
        for lbl in (self.status_scheme, self.status_sel):
            lbl.setEnabled(False)      # paletten soluk renk; sabit renk yazilmaz
        self.statusBar().addWidget(self.status_file, 1)
        self.statusBar().addPermanentWidget(self.status_scheme)
        self.statusBar().addPermanentWidget(self.status_sel)

    def _build_actions(self) -> None:
        S = QStyle
        # --- Dosya ---
        self.act_new = QAction(self._icon(S.SP_FileIcon), "Yeni goruntu...", self)
        self.act_new.setShortcut(QKeySequence.New)
        self.act_new.triggered.connect(self.new_image)
        self.act_open = QAction(self._icon(S.SP_DirOpenIcon), "Goruntu ac...", self)
        self.act_open.setShortcut(QKeySequence.Open)
        self.act_open.triggered.connect(self.open_image)
        self.act_close = QAction("Goruntuyu kapat", self)
        self.act_close.triggered.connect(self.close_image)
        self.act_quit = QAction("Cikis", self)
        self.act_quit.setShortcut(QKeySequence.Quit)
        self.act_quit.triggered.connect(self.close)

        # --- Disk ---
        self.act_mbr = QAction("MBR bolum tablosu olustur", self)
        self.act_mbr.triggered.connect(lambda: self.create_table("mbr"))
        self.act_gpt = QAction("GPT bolum tablosu olustur", self)
        self.act_gpt.triggered.connect(lambda: self.create_table("gpt"))
        self.act_clear_table = QAction("Bolum tablosunu sil", self)
        self.act_clear_table.triggered.connect(self.clear_table)
        self.act_resize_img = QAction("Goruntu boyutunu degistir...", self)
        self.act_resize_img.triggered.connect(self.resize_image)
        self.act_refresh = QAction(self._icon(S.SP_BrowserReload), "Yenile", self)
        self.act_refresh.setShortcut(QKeySequence.Refresh)
        self.act_refresh.triggered.connect(self.refresh)

        # --- Bolum ---
        self.act_create_part = QAction(self._icon(S.SP_FileDialogNewFolder),
                                       "Yeni bolum...", self)
        self.act_create_part.triggered.connect(self.create_partition)
        self.act_format = QAction("Bicimlendir...", self)
        self.act_format.triggered.connect(self.format_partition)
        self.act_resize_part = QAction("Bolumu boyutlandir...", self)
        self.act_resize_part.setToolTip(
            "Bolumu fareyle surukleyerek kucult, buyut veya tasi")
        self.act_resize_part.triggered.connect(self.resize_partition)
        self.act_delete_part = QAction(self._icon(S.SP_TrashIcon), "Bolumu sil", self)
        self.act_delete_part.triggered.connect(self.delete_partition)
        self.act_boot = QAction("Onyukleme bayragini degistir", self)
        self.act_boot.triggered.connect(self.toggle_bootable)
        self.act_rename_part = QAction("Bolum adini degistir...", self)
        self.act_rename_part.triggered.connect(self.rename_partition)
        self.act_type_part = QAction("Bolum turunu degistir...", self)
        self.act_type_part.triggered.connect(self.change_type)
        self.act_label = QAction("Birim etiketini degistir...", self)
        self.act_label.triggered.connect(self.change_label)

        # --- Donusum ve bakim ---
        self.act_to_gpt = QAction("Bolum tablosunu GPT'ye donustur", self)
        self.act_to_gpt.triggered.connect(lambda: self.convert_scheme("gpt"))
        self.act_to_mbr = QAction("Bolum tablosunu MBR'ye donustur", self)
        self.act_to_mbr.triggered.connect(lambda: self.convert_scheme("mbr"))
        self.act_alignment = QAction("Hizalama denetimi (4K)", self)
        self.act_alignment.triggered.connect(self.show_alignment)

        # --- Yedekleme / klonlama ---
        self.act_backup_disk = QAction("Diski yedekle...", self)
        self.act_backup_disk.triggered.connect(lambda: self.backup(disk=True))
        self.act_restore_disk = QAction("Diski geri yukle...", self)
        self.act_restore_disk.triggered.connect(lambda: self.restore(disk=True))
        self.act_clone_disk = QAction("Diski klonla...", self)
        self.act_clone_disk.triggered.connect(self.clone_disk)
        self.act_backup_part = QAction(self._icon(QStyle.SP_DialogSaveButton),
                                       "Bolumu yedekle...", self)
        self.act_backup_part.triggered.connect(lambda: self.backup(disk=False))
        self.act_restore_part = QAction(self._icon(QStyle.SP_DialogOpenButton),
                                        "Bolume geri yukle...", self)
        self.act_restore_part.triggered.connect(lambda: self.restore(disk=False))

        # --- Guvenli silme ---
        self.act_wipe_disk = QAction("Diski guvenli sil...", self)
        self.act_wipe_disk.triggered.connect(lambda: self.wipe(disk=True))
        self.act_wipe_part = QAction(self._icon(QStyle.SP_DialogDiscardButton),
                                     "Bolumu guvenli sil...", self)
        self.act_wipe_part.triggered.connect(lambda: self.wipe(disk=False))

        # --- Kurtarma ---
        self.act_scan_deleted = QAction(self._icon(QStyle.SP_FileDialogContentsView),
                                        "Silinmis dosyalari tara...", self)
        self.act_scan_deleted.triggered.connect(self.scan_deleted)
        self.act_scan_lost = QAction(self._icon(QStyle.SP_FileDialogDetailedView),
                                     "Kayip bolumleri tara...", self)
        self.act_scan_lost.triggered.connect(self.scan_lost)
        self.act_carve = QAction("Imza tabanli dosya kurtarma...", self)
        self.act_carve.triggered.connect(self.carve_files)

        # --- Fiziksel diskler ---
        self.act_refresh_disks = QAction("Fiziksel diskleri yenile", self)
        self.act_refresh_disks.triggered.connect(self.refresh_disks)
        self.act_open_disk_ro = QAction("Secili diski ac (salt okunur)", self)
        self.act_open_disk_ro.triggered.connect(lambda: self.open_physical(False))
        self.act_open_disk_rw = QAction("Secili diski YAZMA modunda ac...", self)
        self.act_open_disk_rw.triggered.connect(lambda: self.open_physical(True))
        self.act_disk_info = QAction(self._icon(QStyle.SP_MessageBoxInformation),
                                     "Disk bilgisi", self)
        self.act_disk_info.triggered.connect(lambda: self.show_disk_info())

        # --- Diger araclar ---
        self.act_new_vhd = QAction("Yeni sanal disk (VHD)...", self)
        self.act_new_vhd.triggered.connect(self.new_vhd)
        self.act_backup_info = QAction("Yedek dosyasi bilgisi...", self)
        self.act_backup_info.triggered.connect(self.show_backup_info)
        self.act_sysinfo = QAction("Sistem bilgisi", self)
        self.act_sysinfo.triggered.connect(self.show_system_info)

        # --- Yardim ---
        self.act_about = QAction("Hakkinda", self)
        self.act_about.triggered.connect(self.about)

        menu = self.menuBar()
        m_dosya = menu.addMenu("&Dosya")
        m_dosya.addAction(self.act_new)
        m_dosya.addAction(self.act_new_vhd)
        m_dosya.addAction(self.act_open)
        m_dosya.addAction(self.act_close)
        m_dosya.addSeparator()
        m_dosya.addAction(self.act_quit)

        m_disk = menu.addMenu("D&isk")
        m_disk.addAction(self.act_new)
        m_disk.addAction(self.act_new_vhd)
        m_disk.addAction(self.act_open)
        m_disk.addAction(self.act_close)
        m_disk.addSeparator()
        m_disk.addAction(self.act_refresh_disks)
        m_disk.addAction(self.act_open_disk_ro)
        m_disk.addAction(self.act_open_disk_rw)
        m_disk.addAction(self.act_disk_info)
        m_disk.addSeparator()
        m_disk.addAction(self.act_mbr)
        m_disk.addAction(self.act_gpt)
        m_disk.addAction(self.act_clear_table)
        m_disk.addSeparator()
        m_disk.addAction(self.act_to_gpt)
        m_disk.addAction(self.act_to_mbr)
        m_disk.addAction(self.act_alignment)
        m_disk.addSeparator()
        m_disk.addAction(self.act_backup_disk)
        m_disk.addAction(self.act_restore_disk)
        m_disk.addAction(self.act_clone_disk)
        m_disk.addAction(self.act_wipe_disk)
        m_disk.addSeparator()
        m_disk.addAction(self.act_resize_img)
        m_disk.addAction(self.act_refresh)

        m_bolum = menu.addMenu("&Bolum")
        m_bolum.addAction(self.act_create_part)
        m_bolum.addAction(self.act_format)
        m_bolum.addAction(self.act_resize_part)
        m_bolum.addAction(self.act_delete_part)
        m_bolum.addSeparator()
        m_bolum.addAction(self.act_label)
        m_bolum.addAction(self.act_rename_part)
        m_bolum.addAction(self.act_type_part)
        m_bolum.addAction(self.act_boot)
        m_bolum.addSeparator()
        m_bolum.addAction(self.act_backup_part)
        m_bolum.addAction(self.act_restore_part)
        m_bolum.addAction(self.act_wipe_part)

        m_arac = menu.addMenu("&Araclar")
        m_arac.addAction(self.act_scan_deleted)
        m_arac.addAction(self.act_scan_lost)
        m_arac.addAction(self.act_carve)
        m_arac.addSeparator()
        m_arac.addAction(self.act_backup_info)
        m_arac.addAction(self.act_sysinfo)

        m_yardim = menu.addMenu("&Yardim")
        m_yardim.addAction(self.act_about)

        # Arac cubugu yalnizca SECILI disk/bolum uzerinde yapilabilecek islemleri
        # tasir; goruntu acma/olusturma Dosya ve Disk menulerindedir.
        tb = QToolBar("Islemler")
        tb.setIconSize(QSize(18, 18))
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        tb.addAction(self.act_create_part)
        tb.addAction(self.act_format)
        tb.addAction(self.act_resize_part)
        tb.addAction(self.act_delete_part)
        tb.addSeparator()
        tb.addAction(self.act_backup_part)
        tb.addAction(self.act_restore_part)
        tb.addSeparator()
        tb.addAction(self.act_scan_deleted)
        tb.addAction(self.act_scan_lost)
        tb.addSeparator()
        tb.addAction(self.act_wipe_part)
        tb.addSeparator()
        tb.addAction(self.act_disk_info)
        tb.addAction(self.act_refresh)
        self.addToolBar(tb)
        self.toolbar = tb

    # ==================================================================
    # Gunluk
    # ==================================================================
    def log(self, mesaj: str) -> None:
        zaman = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{zaman}] {mesaj}")
        self.statusBar().showMessage(mesaj, 6000)
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            gun = datetime.datetime.now().strftime("%Y-%m-%d")
            with open(os.path.join(LOG_DIR, f"app-{gun}.log"), "a", encoding="utf-8") as fh:
                fh.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} {mesaj}\n")
        except OSError:
            pass

    def error(self, baslik: str, mesaj: str) -> None:
        QMessageBox.critical(self, baslik, mesaj)
        self.log(f"HATA — {baslik}: {mesaj}")

    # ==================================================================
    # Goruntu islemleri
    # ==================================================================
    def new_image(self) -> None:
        varsayilan = os.path.dirname(self.session.path) if self.session else ""
        dlg = NewImageDialog(self, varsayilan)
        if exec_dialog(dlg) != NewImageDialog.Accepted:
            return
        v = dlg.values()

        def gorev(ilerle):
            ilerle("Goruntu dosyasi olusturuluyor...", 10)
            oturum = DiskSession.create(v["path"], v["size"], sparse=v["sparse"],
                                        scheme=v["scheme"], overwrite=True)
            if v["auto_partition"]:
                ilerle("Bolum olusturuluyor...", 40)
                bos = oturum.free_regions()
                if bos:
                    en_buyuk = max(bos, key=lambda r: r.sector_count)
                    oturum.create_partition(en_buyuk.start_lba, en_buyuk.sector_count,
                                            fs_key=v["fs"], label=v["label"],
                                            name=v["label"],
                                            progress=lambda m, p: ilerle(m, 40 + p // 2))
            return oturum

        ok, sonuc = run_task(self, "Yeni disk goruntusu", gorev)
        if not ok:
            self.error("Goruntu olusturulamadi", str(sonuc))
            return
        self._add_session(sonuc)
        self.log(f"Goruntu olusturuldu: {v['path']} ({human_size(v['size'])})")
        self.refresh()

    def open_image(self) -> None:
        desen = " ".join(f"*.{u}" for u in IMAGE_EXTENSIONS)
        yol, _ = QFileDialog.getOpenFileName(
            self, "Disk goruntusu ac", "",
            f"Disk goruntuleri ({desen});;Ham goruntu (*.img *.raw *.dd *.bin);;"
            "Sanal diskler (*.vhd *.vhdx *.vdi *.vmdk *.qcow2);;Tum dosyalar (*)")
        if not yol:
            return
        self.open_path(yol)

    def open_path(self, yol: str) -> None:
        mevcut = self._find_open(yol)
        if mevcut is not None:
            self.log(f"Zaten acik, one getirildi: {yol}")
            self.session = mevcut
            self.refresh()
            return
        try:
            self._add_session(DiskSession.open(yol))
        except Exception as exc:
            self.error("Goruntu acilamadi", str(exc))
            return
        # Salt okunur acildiysa kullanici bunu bicimlendirmeye calisirken degil,
        # HEMEN ogrenmeli.
        if self.session.readonly and not self.session.is_physical:
            self._salt_okunur_acilis_uyarisi(yol)
        self.log(f"Goruntu acildi: {yol} — {self.session.format_name}, "
                 f"{human_size(self.session.image.size)}, {self.session.scheme_name}")
        self.refresh()

    def close_image(self) -> None:
        """Etkin oturumu kapatir; diger acik goruntuler listede kalir."""
        if self.session:
            self.log(f"Kapatildi: {self.session.path or self.session.name}")
            try:
                self.session.close()
            except Exception:
                pass
            if self.session in self.sessions:
                self.sessions.remove(self.session)
        self.session = self.sessions[-1] if self.sessions else None
        self.selected_partition = None
        self.selected_free = None
        if self.session:
            self.refresh()          # kalan oturumlardan birine gec
            return
        self._build_tree([])        # diskler listede kalir
        self.disk_map.clear()
        self.part_table.set_data([], [])
        self.browser.set_filesystem(None)
        self.hex_view.set_device(None)
        self.info_view.setPlainText(self._physical_summary_text())
        self.setWindowTitle(APP_NAME)
        self.status_file.setText("Disk goruntusu acik degil")
        self.status_scheme.setText("")
        self.status_sel.setText("")
        self._update_actions()

    def close_all(self) -> None:
        """Tum acik oturumlari kapatir."""
        for oturum in list(self.sessions):
            try:
                oturum.close()
            except Exception:
                pass
        self.sessions.clear()
        self.session = None

    def resize_image(self) -> None:
        if not self._require_session():
            return
        mevcut = self.session.image.size
        metin, tamam = QInputDialog.getText(
            self, "Goruntu boyutu",
            f"Mevcut boyut: {human_size(mevcut)}\n\nYeni boyut (orn. 4 GB, 512 MB):",
            text=human_size(mevcut))
        if not tamam:
            return
        try:
            yeni = parse_size(metin)
        except Exception:
            self.error("Gecersiz boyut", f"Boyut cozumlenemedi: {metin}")
            return
        if yeni < mevcut:
            cevap = QMessageBox.warning(
                self, "Kucultme uyarisi",
                "Goruntuyu kucultmek sondaki verileri kalici olarak siler.\n"
                "Devam edilsin mi?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if cevap != QMessageBox.Yes:
                return
        try:
            self.session.resize_image(yeni)
        except Exception as exc:
            self.error("Boyutlandirma basarisiz", str(exc))
            return
        self.log(f"Goruntu boyutu degistirildi: {human_size(mevcut)} -> {human_size(yeni)}")
        self.refresh()

    # ==================================================================
    # Tablo islemleri
    # ==================================================================
    def create_table(self, scheme: str) -> None:
        if not self._require_session():
            return
        ad = {"mbr": "MBR", "gpt": "GPT"}[scheme]
        if self.session.partitions:
            cevap = QMessageBox.warning(
                self, f"{ad} tablosu olustur",
                f"Yeni bir {ad} bolum tablosu olusturulacak.\n\n"
                f"Mevcut {len(self.session.partitions)} bolumun tanimi silinir "
                "ve icerige erisilemez hale gelir.\n\nDevam edilsin mi?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if cevap != QMessageBox.Yes:
                return
        try:
            self.session.create_table(scheme)
        except Exception as exc:
            self.error("Tablo olusturulamadi", str(exc))
            return
        self.log(f"{ad} bolum tablosu olusturuldu")
        self.refresh()

    def clear_table(self) -> None:
        if not self._require_session():
            return
        cevap = QMessageBox.warning(
            self, "Bolum tablosunu sil",
            "Bolum tablosu tamamen silinecek. Bolumlerdeki veriler diskte kalir "
            "ancak tablo olmadan erisilemez.\n\nDevam edilsin mi?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if cevap != QMessageBox.Yes:
            return
        try:
            self.session.clear_table()
        except Exception as exc:
            self.error("Tablo silinemedi", str(exc))
            return
        self.log("Bolum tablosu silindi")
        self.refresh()

    # ==================================================================
    # Bolum islemleri
    # ==================================================================
    def create_partition(self) -> None:
        if not self._require_session():
            return
        if not self.session.table:
            cevap = QMessageBox.question(
                self, "Bolum tablosu yok",
                "Bu goruntude bolum tablosu yok. Simdi GPT olusturulsun mu?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if cevap != QMessageBox.Yes:
                return
            self.create_table("gpt")
            if not self.session.table:
                return

        bolgeler = self.session.free_regions()
        if not bolgeler:
            QMessageBox.information(self, "Bos alan yok",
                                    "Yeni bolum icin yeterli bos alan bulunamadi.")
            return
        if self.selected_free:
            secili = [r for r in bolgeler if r.start_lba == self.selected_free[0]]
            bolge = secili[0] if secili else max(bolgeler, key=lambda r: r.sector_count)
        else:
            bolge = max(bolgeler, key=lambda r: r.sector_count)

        tablo = self.session.table
        ic_genisletilmis = False
        genisletilmis_var = False
        birincil_musait = True
        if self.session.scheme == "mbr":
            ext = tablo.extended_partition()
            genisletilmis_var = ext is not None
            birincil_musait = tablo.can_add_primary()
            if ext is not None and ext.start_lba <= bolge.start_lba <= ext.end_lba:
                ic_genisletilmis = True
            if not birincil_musait and not ic_genisletilmis:
                QMessageBox.information(
                    self, "Bolum eklenemez",
                    "MBR tablosunda 4 birincil bolum dolu.\n"
                    "Daha fazla bolum icin genisletilmis bolum icinde mantiksal "
                    "bolum olusturun.")
                return

        dlg = CreatePartitionDialog(bolge, self.session.scheme, self,
                                    can_primary=birincil_musait,
                                    has_extended=genisletilmis_var,
                                    inside_extended=ic_genisletilmis)
        if exec_dialog(dlg) != CreatePartitionDialog.Accepted:
            return
        v = dlg.values()

        def gorev(ilerle):
            ilerle("Bolum olusturuluyor...", 10)
            if v["kind"] == "extended":
                return self.session.table.create_extended(v["start_lba"], v["sector_count"])
            return self.session.create_partition(
                v["start_lba"], v["sector_count"], fs_key=v["fs"],
                label=v["label"], name=v["name"], bootable=v["bootable"],
                logical=(v["kind"] == "logical") if v["kind"] else None,
                progress=ilerle)

        ok, sonuc = run_task(self, "Bolum olusturuluyor", gorev)
        if not ok:
            self.error("Bolum olusturulamadi", str(sonuc))
            self.refresh()
            return
        fs_ad = FS_BY_KEY[v["fs"]].label if v["fs"] else "bicimlendirilmemis"
        self.log(f"Bolum olusturuldu: LBA {v['start_lba']}, "
                 f"{human_size(v['sector_count'] * 512)}, {fs_ad}")
        self.refresh()
        if isinstance(sonuc, Partition):
            self.select_partition(sonuc.index)

    def format_partition(self) -> None:
        part = self._current_partition()
        if part is None:
            return
        dlg = FormatDialog(part, self)
        if exec_dialog(dlg) != FormatDialog.Accepted:
            return
        v = dlg.values()
        cevap = QMessageBox.warning(
            self, "Bicimlendirme onayi",
            f"Bolum {part.index} ({human_size(part.size)}) "
            f"{FS_BY_KEY[v['fs']].label} olarak bicimlendirilecek.\n\n"
            "Bolumdeki tum veriler kalici olarak silinir.\n\nDevam edilsin mi?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if cevap != QMessageBox.Yes:
            return

        index = part.index

        def gorev(ilerle):
            return self.session.format_partition(
                index, v["fs"], label=v["label"], cluster_bytes=v["cluster"],
                quick=v["quick"], progress=ilerle)

        ok, sonuc = run_task(self, "Bicimlendiriliyor", gorev)
        if not ok:
            self.error("Bicimlendirme basarisiz", str(sonuc))
            self.refresh()
            return
        self.log(f"Bolum {index} bicimlendirildi: {sonuc}"
                 + (f" (etiket: {v['label']})" if v["label"] else ""))
        self.refresh()
        self.select_partition(index)

    def resize_partition(self) -> None:
        """Bolumu suruklemeli pencereyle kucultur, buyutur veya tasir."""
        part = self._current_partition()
        if part is None:
            return
        try:
            pencere = self.session.resize_window(part.index)
            bilgi = self.session.resize_info(part.index)
        except Exception as exc:                       # noqa: BLE001
            self.error("Boyutlandirma hazirlanamadi", str(exc))
            return
        if not bilgi.resizable and not bilgi.movable:
            QMessageBox.information(
                self, "Boyutlandirilamaz",
                f"Bu bolum boyutlandirilamiyor.\n\n{bilgi.note}")
            return

        kullanilan = -1
        try:
            fs = self.session.filesystem(part.index)
            if fs is not None:
                istatistik = fs.stats() or {}
                kullanilan = istatistik.get("used_bytes", -1)
        except Exception:                              # noqa: BLE001
            kullanilan = -1
        self.session.close_filesystems()

        dlg = ResizePartitionDialog(part, pencere, bilgi,
                                    align_sectors=self.session.table.align_sectors,
                                    used_bytes=kullanilan, parent=self)
        if exec_dialog(dlg) != ResizePartitionDialog.Accepted:
            return
        v = dlg.values()
        try:
            plan = self.session.plan_resize(part.index, v["start_lba"],
                                            v["sector_count"])
        except Exception as exc:                       # noqa: BLE001
            self.error("Yeni yerlesim gecersiz", str(exc))
            return
        if not plan.changed:
            return

        metin = [f"<b>{plan.summary()}</b>", ""]
        metin += [f"• {u}" for u in plan.warnings]
        metin.append("")
        metin.append("Devam edilsin mi?")
        cevap = QMessageBox.warning(
            self, "Boyutlandirma onayi", "<br>".join(metin),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if cevap != QMessageBox.Yes:
            return

        index = part.index

        def gorev(ilerle):
            return self.session.resize_partition(
                index, v["start_lba"], v["sector_count"], confirm=True,
                progress=ilerle)

        ok, sonuc = run_task(self, "Bolum boyutlandiriliyor", gorev)
        if not ok:
            self.error("Boyutlandirma basarisiz", str(sonuc))
            self.refresh()
            return
        self.log(f"Bolum {index} boyutlandirildi: {plan.summary()}")
        self.refresh()
        self.select_partition(index)

    def delete_partition(self) -> None:
        part = self._current_partition()
        if part is None:
            return
        cevap = QMessageBox.warning(
            self, "Bolum silme onayi",
            f"Bolum {part.index} ({part.display_name}, {human_size(part.size)}) "
            "silinecek.\n\nIcerdigi tum veriler kaybolur.\n\nDevam edilsin mi?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if cevap != QMessageBox.Yes:
            return
        try:
            self.session.delete_partition(part.index)
        except Exception as exc:
            self.error("Bolum silinemedi", str(exc))
            return
        self.log(f"Bolum {part.index} silindi ({human_size(part.size)})")
        self.selected_partition = None
        self.refresh()

    def toggle_bootable(self) -> None:
        part = self._current_partition()
        if part is None:
            return
        try:
            self.session.set_bootable(part.index, not part.bootable)
        except Exception as exc:
            self.error("Bayrak degistirilemedi", str(exc))
            return
        durum = "kaldirildi" if part.bootable else "ayarlandi"
        self.log(f"Bolum {part.index} onyukleme bayragi {durum}")
        self.refresh()
        self.select_partition(part.index)

    def rename_partition(self) -> None:
        part = self._current_partition()
        if part is None:
            return
        if self.session.scheme != "gpt":
            QMessageBox.information(self, "Desteklenmiyor",
                                    "Bolum adi yalnizca GPT semasinda saklanir.\n"
                                    "MBR icin birim etiketini degistirin.")
            return
        ad, tamam = QInputDialog.getText(self, "Bolum adi", "Yeni ad:", text=part.name)
        if not tamam:
            return
        try:
            self.session.set_partition_name(part.index, ad.strip())
        except Exception as exc:
            self.error("Ad degistirilemedi", str(exc))
            return
        self.log(f"Bolum {part.index} adi degistirildi: {ad}")
        self.refresh()
        self.select_partition(part.index)

    def change_type(self) -> None:
        part = self._current_partition()
        if part is None:
            return
        if self.session.scheme == "mbr":
            secenekler = [f"0x{k:02X} — {v}" for k, v in sorted(MBR_TYPES.items())]
            mevcut = f"0x{part.type_id:02X} — {part.type_name}"
            secim, tamam = QInputDialog.getItem(self, "Bolum turu", "Tur:", secenekler,
                                                secenekler.index(mevcut) if mevcut in secenekler else 0,
                                                False)
            if not tamam:
                return
            try:
                self.session.set_partition_type(part.index,
                                                type_id=int(secim.split(" ")[0], 16))
            except Exception as exc:
                self.error("Tur degistirilemedi", str(exc))
                return
        else:
            secenekler = [f"{v} — {k}" for k, v in GPT_TYPES.items() if v != "Bos"]
            secim, tamam = QInputDialog.getItem(self, "Bolum turu", "Tur:",
                                                secenekler, 0, False)
            if not tamam:
                return
            try:
                self.session.set_partition_type(part.index,
                                                type_guid=secim.split(" — ")[1])
            except Exception as exc:
                self.error("Tur degistirilemedi", str(exc))
                return
        self.log(f"Bolum {part.index} turu degistirildi")
        self.refresh()
        self.select_partition(part.index)

    def change_label(self) -> None:
        part = self._current_partition()
        if part is None:
            return
        fs = self.session.filesystem(part.index)
        if fs is None or not fs.writable:
            QMessageBox.information(
                self, "Desteklenmiyor",
                "Bu dosya sisteminde etiket degistirme desteklenmiyor.\n"
                "Bolumu yeniden bicimlendirerek etiket verebilirsiniz.")
            return
        etiket, tamam = QInputDialog.getText(self, "Birim etiketi", "Yeni etiket:",
                                             text=fs.label)
        if not tamam:
            return
        try:
            fs.set_label(etiket.strip())
        except Exception as exc:
            self.error("Etiket degistirilemedi", str(exc))
            return
        self.log(f"Bolum {part.index} etiketi: {etiket}")
        self.refresh()
        self.select_partition(part.index)


    # ==================================================================
    # Sema donusumu ve hizalama
    # ==================================================================
    def convert_scheme(self, scheme: str) -> None:
        if not self._require_session():
            return
        ad = scheme.upper()
        uygun, neden = self.session.can_convert_to(scheme)
        if not uygun:
            QMessageBox.information(self, f"{ad} donusumu yapilamaz", neden)
            return
        cevap = QMessageBox.warning(
            self, f"{ad} donusumu",
            f"{neden}\n\nBolum verileri yerinde kalir, yalnizca bolum tablosu "
            f"{ad} bicimine yeniden yazilir.\n\n"
            "Islem sirasinda kesinti olursa tablo bozulabilir; onemli veriler icin "
            "once yedek alin.\n\nDevam edilsin mi?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if cevap != QMessageBox.Yes:
            return
        ok, sonuc = run_task(self, f"{ad} donusumu",
                             lambda ilerle: self.session.convert_scheme(scheme, ilerle))
        if not ok:
            self.error("Donusum basarisiz", str(sonuc))
            self.refresh()
            return
        self.log(f"Bolum tablosu {ad} bicimine donusturuldu")
        self.refresh()

    def show_alignment(self) -> None:
        if self.session is None or not self.session.table:
            QMessageBox.information(self, "Bolum yok", "Once bir bolum tablosu acin.")
            return
        rapor = self.session.alignment_report()
        if not rapor:
            QMessageBox.information(self, "Hizalama", "Tabloda bolum yok.")
            return
        satirlar = {}
        for r in rapor:
            durum = "Hizali (1 MiB)" if r["aligned_1m"] else (
                "4K hizali" if r["aligned_4k"] else
                f"HIZASIZ — 4K icinde {r['offset_in_4k']} bayt kayma")
            satirlar[f"Bolum {r['index']} ({r['name']})"] = f"LBA {r['start_lba']} — {durum}"
        hizasiz = [r for r in rapor if not r["aligned_4k"]]
        InfoDialog("Hizalama Denetimi", satirlar, self,
                   note=("Tum bolumler 4K sinirinda hizali." if not hizasiz else
                         f"{len(hizasiz)} bolum 4K sinirinda hizali degil; SSD ve "
                         "ileri bicim disklerde basarim dusebilir.")).exec_()

    # ==================================================================
    # Yedekleme / geri yukleme / klonlama
    # ==================================================================
    def backup(self, disk: bool = True) -> None:
        if self.session is None:
            QMessageBox.information(self, "Goruntu yok", "Once bir goruntu acin.")
            return
        if not disk:
            part = self._current_partition()
            if part is None:
                return
            varsayilan = f"{self.session.name}-bolum{part.index}.dub"
            hedef_ad = f"Bolum {part.index}"
        else:
            varsayilan = f"{self.session.name}.dub"
            hedef_ad = "Tum disk"
        yol, _ = QFileDialog.getSaveFileName(
            self, "Yedek dosyasi", os.path.join(os.path.dirname(self.session.path),
                                                varsayilan),
            "DiskUltimate yedegi (*.dub)")
        if not yol:
            return
        if not yol.endswith(".dub"):
            yol += ".dub"

        def gorev(ilerle):
            if disk:
                return self.session.backup_disk(yol, progress=ilerle)
            return self.session.backup_partition(part.index, yol, progress=ilerle)

        ok, sonuc = run_task(self, f"{hedef_ad} yedekleniyor", gorev)
        if not ok:
            self.error("Yedekleme basarisiz", str(sonuc))
            return
        self.log(f"{hedef_ad} yedeklendi: {yol} "
                 f"({human_size(sonuc.file_size)}, kaynak {human_size(sonuc.total_bytes)})")
        InfoDialog("Yedekleme tamamlandi", sonuc.summary(), self).exec_()

    def restore(self, disk: bool = True) -> None:
        if not self._require_session():
            return
        if not disk:
            part = self._current_partition()
            if part is None:
                return
        yol, _ = QFileDialog.getOpenFileName(
            self, "Yedek dosyasi", os.path.dirname(self.session.path),
            "DiskUltimate yedegi (*.dub);;Tum dosyalar (*)")
        if not yol:
            return
        try:
            bilgi = self.session.backup_info(yol)
        except Exception as exc:
            self.error("Yedek okunamadi", str(exc))
            return
        hedef_boyut = self.session.image.size if disk else part.size
        hedef_ad = "tum disk" if disk else f"Bolum {part.index}"
        if bilgi.total_bytes > hedef_boyut:
            self.error("Hedef cok kucuk",
                       f"Yedek {human_size(bilgi.total_bytes)}, hedef "
                       f"{human_size(hedef_boyut)}")
            return
        cevap = QMessageBox.warning(
            self, "Geri yukleme onayi",
            f"Yedek: {os.path.basename(yol)}\n"
            f"Kaynak boyut: {human_size(bilgi.total_bytes)}\n"
            f"Dosya sistemi: {bilgi.fs_type or '-'}\n"
            f"Olusturma: {bilgi.created.strftime('%Y-%m-%d %H:%M') if bilgi.created else '-'}\n\n"
            f"Hedef: {hedef_ad}\n\nHedefteki tum veriler uzerine yazilacak. "
            "Devam edilsin mi?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if cevap != QMessageBox.Yes:
            return

        def gorev(ilerle):
            if disk:
                return self.session.restore_disk(yol, progress=ilerle)
            return self.session.restore_partition(part.index, yol, progress=ilerle)

        ok, sonuc = run_task(self, "Geri yukleniyor", gorev)
        if not ok:
            self.error("Geri yukleme basarisiz", str(sonuc))
            self.refresh()
            return
        self.log(f"{hedef_ad} geri yuklendi: {os.path.basename(yol)}")
        self.refresh()

    def clone_disk(self) -> None:
        if self.session is None:
            QMessageBox.information(self, "Goruntu yok", "Once bir goruntu acin.")
            return
        yol, _ = QFileDialog.getSaveFileName(
            self, "Klon hedefi",
            os.path.join(os.path.dirname(self.session.path),
                         f"{os.path.splitext(self.session.name)[0]}-klon.img"),
            "Disk goruntusu (*.img)")
        if not yol:
            return
        if not os.path.splitext(yol)[1]:
            yol += ".img"

        ok, sonuc = run_task(self, "Disk klonlaniyor",
                             lambda ilerle: self.session.clone_to(yol, progress=ilerle))
        if not ok:
            self.error("Klonlama basarisiz", str(sonuc))
            return
        self.log(f"Disk klonlandi: {sonuc}")
        cevap = QMessageBox.question(
            self, "Klon hazir", f"Klon olusturuldu:\n{sonuc}\n\nSimdi acilsin mi?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if cevap == QMessageBox.Yes:
            self.open_path(sonuc)

    # ==================================================================
    # Guvenli silme
    # ==================================================================
    def wipe(self, disk: bool = True) -> None:
        if not self._require_session():
            return
        if disk:
            hedef_ad = f"Tum disk ({self.session.name})"
            boyut = self.session.image.size
            bos_alan = False
            index = -1
        else:
            part = self._current_partition()
            if part is None:
                return
            hedef_ad = f"Bolum {part.index} ({part.display_name})"
            boyut = part.size
            index = part.index
            fs = None
            try:
                fs = self.session.filesystem(index)
            except Exception:
                pass
            bos_alan = bool(fs and getattr(fs, "writable", False))

        dlg = WipeDialog(self, hedef_ad, boyut, allow_free_space=bos_alan)
        if exec_dialog(dlg) != WipeDialog.Accepted:
            return
        v = dlg.values()
        if v["scope"] == "free":
            ok, sonuc = run_task(
                self, "Bos alan siliniyor",
                lambda ilerle: self.session.wipe_free_space(index, progress=ilerle))
            if not ok:
                self.error("Silme basarisiz", str(sonuc))
                return
            self.log(f"{hedef_ad} bos alani silindi ({human_size(sonuc['bytes'])})")
            self.refresh()
            return

        cevap = QMessageBox.warning(
            self, "Silme onayi",
            f"{hedef_ad}\n{human_size(boyut)}\n\n"
            f"Yontem: {dlg.method_combo.currentText()}\n\n"
            "TUM VERILER KALICI OLARAK SILINECEK ve kurtarilamayacak.\n\n"
            "Devam edilsin mi?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if cevap != QMessageBox.Yes:
            return

        def gorev(ilerle):
            if disk:
                return self.session.wipe_disk(v["method"], progress=ilerle)
            return self.session.wipe_partition_data(index, v["method"], progress=ilerle)

        ok, sonuc = run_task(self, "Guvenli silme", gorev)
        if not ok:
            self.error("Silme basarisiz", str(sonuc))
            self.refresh()
            return
        self.log(f"{hedef_ad} silindi — {sonuc['method']}, "
                 f"{human_size(sonuc['bytes'])}")
        self.refresh()

    # ==================================================================
    # Kurtarma
    # ==================================================================
    def scan_deleted(self) -> None:
        part = self._current_partition()
        if part is None:
            return
        index = part.index
        ok, sonuc = run_task(
            self, "Silinmis dosyalar taraniyor",
            lambda ilerle: self.session.scan_deleted(index, progress=ilerle))
        if not ok:
            self.error("Tarama basarisiz", str(sonuc))
            return
        if not sonuc:
            QMessageBox.information(self, "Sonuc yok",
                                    "Bu bolumde silinmis dosya girisi bulunamadi.")
            return
        self.log(f"Bolum {index}: {len(sonuc)} silinmis giris bulundu")
        dlg = DeletedFilesDialog(sonuc, self, f"Bolum {index} — Silinmis Dosyalar")
        if exec_dialog(dlg) != DeletedFilesDialog.Accepted:
            return
        secilenler = dlg.selected()
        if not secilenler:
            return
        klasor = QFileDialog.getExistingDirectory(self, "Kurtarma hedefi")
        if not klasor:
            return

        def gorev(ilerle):
            basarili = 0
            for i, oge in enumerate(secilenler, 1):
                ilerle(f"Kurtariliyor: {oge.name}",
                       int(100 * i / len(secilenler)))
                try:
                    self.session.recover_deleted(index, oge,
                                                 os.path.join(klasor, oge.name))
                    basarili += 1
                except Exception:
                    pass
            return basarili

        ok, sayi = run_task(self, "Dosyalar kurtariliyor", gorev)
        if not ok:
            self.error("Kurtarma basarisiz", str(sayi))
            return
        self.log(f"{sayi}/{len(secilenler)} dosya kurtarildi -> {klasor}")
        QMessageBox.information(self, "Kurtarma tamamlandi",
                                f"{sayi} dosya kurtarildi:\n{klasor}")

    def scan_lost(self) -> None:
        if self.session is None:
            QMessageBox.information(self, "Goruntu yok", "Once bir goruntu acin.")
            return
        derin = QMessageBox.question(
            self, "Tarama derinligi",
            "Derin tarama (64 KB adim) yapilsin mi?\n\n"
            "Hayir: hizli tarama (1 MB adim) — cogu durumda yeterlidir.\n"
            "Evet: yavas ama hizasiz bolumleri de bulur.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes
        ok, sonuc = run_task(
            self, "Kayip bolumler taraniyor",
            lambda ilerle: self.session.scan_lost_partitions(deep=derin, progress=ilerle))
        if not ok:
            self.error("Tarama basarisiz", str(sonuc))
            return
        if not sonuc:
            QMessageBox.information(
                self, "Sonuc yok",
                "Bolum tablosunda olmayan bir dosya sistemi bulunamadi.")
            return
        self.log(f"Kayip bolum taramasi: {len(sonuc)} aday bulundu")
        dlg = LostPartitionsDialog(sonuc, self)
        if exec_dialog(dlg) != LostPartitionsDialog.Accepted:
            return
        secili = dlg.selected()
        if not secili:
            return
        if not self._require_session():
            return
        try:
            part = self.session.adopt_lost_partition(secili)
        except Exception as exc:
            self.error("Bolum eklenemedi", str(exc))
            return
        self.log(f"Kayip bolum tabloya eklendi: LBA {secili.start_lba} "
                 f"({human_size(secili.size)}, {secili.fs_type})")
        self.refresh()
        self.select_partition(part.index)

    def carve_files(self) -> None:
        if self.session is None:
            QMessageBox.information(self, "Goruntu yok", "Once bir goruntu acin.")
            return
        index = self.selected_partition if self.selected_partition is not None else -1
        kapsam = (f"Bolum {index}" if index > 0 else "Tum goruntu")
        dlg = CarveOptionsDialog(self, kapsam)
        if exec_dialog(dlg) != CarveOptionsDialog.Accepted:
            return
        anahtarlar = dlg.selected_keys()
        if not anahtarlar:
            QMessageBox.information(self, "Secim yok", "En az bir dosya turu secin.")
            return
        ok, sonuc = run_task(
            self, "Imza taramasi",
            lambda ilerle: self.session.carve_files(index, keys=anahtarlar,
                                                    progress=ilerle))
        if not ok:
            self.error("Tarama basarisiz", str(sonuc))
            return
        if not sonuc:
            QMessageBox.information(self, "Sonuc yok",
                                    "Secilen turlerde dosya imzasi bulunamadi.")
            return
        self.log(f"Imza taramasi ({kapsam}): {len(sonuc)} dosya bulundu")
        sonuc_dlg = CarvedFilesDialog(sonuc, self)
        if exec_dialog(sonuc_dlg) != CarvedFilesDialog.Accepted:
            return
        secilenler = sonuc_dlg.selected()
        if not secilenler:
            return
        klasor = QFileDialog.getExistingDirectory(self, "Cikarma hedefi")
        if not klasor:
            return

        def gorev(ilerle):
            sayac = 0
            for i, oge in enumerate(secilenler, 1):
                ilerle(f"Cikariliyor: {oge.suggested_name}",
                       int(100 * i / len(secilenler)))
                try:
                    self.session.extract_carved(oge, klasor, index)
                    sayac += 1
                except Exception:
                    pass
            return sayac

        ok, sayi = run_task(self, "Dosyalar cikariliyor", gorev)
        if ok:
            self.log(f"{sayi} dosya cikarildi -> {klasor}")
            QMessageBox.information(self, "Tamamlandi",
                                    f"{sayi} dosya cikarildi:\n{klasor}")

    # ==================================================================
    # Diger araclar
    # ==================================================================
    def new_vhd(self) -> None:
        varsayilan = os.path.dirname(self.session.path) if self.session else ""
        dlg = NewImageDialog(self, varsayilan)
        dlg.setWindowTitle("Yeni Sanal Disk (VHD)")
        dlg.path_edit.setText(os.path.join(varsayilan or os.path.expanduser("~"),
                                           "yeni-disk.vhd"))
        if exec_dialog(dlg) != NewImageDialog.Accepted:
            return
        v = dlg.values()
        yol = v["path"]
        if not yol.lower().endswith(".vhd"):
            yol = os.path.splitext(yol)[0] + ".vhd"

        def gorev(ilerle):
            ilerle("Sanal disk olusturuluyor...", 10)
            disk = VhdImage.create_fixed(yol, v["size"], sparse=v["sparse"],
                                         overwrite=True)
            disk.close()
            oturum = DiskSession.open(yol)
            if v["scheme"]:
                oturum.create_table(v["scheme"])
                if v["auto_partition"]:
                    ilerle("Bolum olusturuluyor...", 40)
                    bos = oturum.free_regions()
                    if bos:
                        en_buyuk = max(bos, key=lambda r: r.sector_count)
                        oturum.create_partition(
                            en_buyuk.start_lba, en_buyuk.sector_count,
                            fs_key=v["fs"], label=v["label"], name=v["label"],
                            progress=lambda m, p: ilerle(m, 40 + p // 2))
            return oturum

        ok, sonuc = run_task(self, "Sanal disk olusturuluyor", gorev)
        if not ok:
            self.error("Sanal disk olusturulamadi", str(sonuc))
            return
        self._add_session(sonuc)
        self.log(f"VHD olusturuldu: {yol} ({human_size(v['size'])})")
        self.refresh()

    def show_backup_info(self) -> None:
        yol, _ = QFileDialog.getOpenFileName(
            self, "Yedek dosyasi", "", "DiskUltimate yedegi (*.dub);;Tum dosyalar (*)")
        if not yol:
            return
        try:
            bilgi = DiskSession.backup_info(yol)
        except Exception as exc:
            self.error("Yedek okunamadi", str(exc))
            return
        InfoDialog("Yedek Dosyasi Bilgisi", bilgi.summary(), self).exec_()

    def show_system_info(self) -> None:
        satirlar = dict(platform_summary())
        satirlar["Qt platformu"] = QApplication.instance().platformName()
        satirlar["Arayuz stili"] = QApplication.instance().style().objectName()
        if self.session:
            satirlar["Acik dosya bicimi"] = self.session.format_name
        InfoDialog("Sistem Bilgisi", satirlar, self,
                   note=("FAT12/16/32 ve exFAT saf Python ile desteklenir ve her "
                         "platformda calisir. NTFS ve ext2/3/4 bicimlendirmesi "
                         "sistemdeki mkfs araclarini gerektirir.")).exec_()

    # ==================================================================
    # Oturum yonetimi (coklu goruntu)
    # ==================================================================
    def _add_session(self, oturum: DiskSession) -> None:
        """Yeni acilan oturumu listeye ekler ve etkin yapar."""
        self.sessions.append(oturum)
        self.session = oturum
        self.selected_partition = None
        self.selected_free = None

    @staticmethod
    def _yol_anahtari(yol: str) -> str:
        """Yol karsilastirma anahtari.

        `normcase` Windows'ta buyuk/kucuk harf ve ayirici farkini giderir:
        `C:\\du\\a.img` ile `c:/du/a.img` ayni dosyadir. Bu normallestirme
        olmadan ayni dosya ikinci kez acilmaya calisilir ve Windows dosyayi
        kilitledigi icin **salt okunur** duser.
        """
        if not yol:
            return ""
        try:
            gercek = os.path.realpath(yol)
        except OSError:
            gercek = os.path.abspath(yol)
        return os.path.normcase(os.path.normpath(gercek))

    def _find_open(self, yol: str) -> Optional[DiskSession]:
        """Ayni kaynak zaten acik mi?"""
        hedef = self._yol_anahtari(yol)
        if not hedef:
            return None
        for s in self.sessions:
            if s.path and self._yol_anahtari(s.path) == hedef:
                return s
        return None

    def activate_session(self, oturum: DiskSession) -> None:
        """Acik oturumlardan birini etkin yapar."""
        if oturum not in self.sessions or oturum is self.session:
            if oturum is self.session:
                return
            return
        self.session = oturum
        self.selected_partition = None
        self.selected_free = None
        self.refresh()

    # ==================================================================
    # Fiziksel diskler
    # ==================================================================
    def refresh_disks(self) -> None:
        """Disk listesini yeniden tarar."""
        self._build_tree(self.session.partitions if self.session else [])
        sayi = len(getattr(self, "_physical_cache", {}))
        self.log(f"Fiziksel disk listesi yenilendi: {sayi} disk")

    def _selected_disk_path(self) -> Optional[str]:
        item = self.tree.currentItem()
        if item:
            veri = item.data(0, Qt.UserRole)
            if veri and veri[0] == "phys":
                return veri[1]
        return None

    def show_disk_info(self, path: Optional[str] = None) -> None:
        """Secili fiziksel diskin bilgisini bilgi panelinde gosterir."""
        path = path or self._selected_disk_path()
        bilgi = getattr(self, "_physical_cache", {}).get(path or "")
        if bilgi is None:
            QMessageBox.information(self, "Disk secili degil",
                                    "Agactan bir fiziksel disk secin.")
            return
        satirlar = [f"FIZIKSEL DISK — {bilgi.name}", "=" * 52]
        for anahtar, deger in bilgi.summary().items():
            satirlar.append(f"{anahtar:<16}: {deger}")
        if bilgi.partitions:
            satirlar += ["", "BOLUM AYGITLARI", "-" * 52]
            satirlar += [f"  {b}" for b in bilgi.partitions]
        satirlar += ["", bilgi.risk_text]
        if not DiskSession.has_disk_privileges():
            satirlar += ["", ("UYARI: Uygulama yonetici/root yetkisi olmadan calisiyor; "
                              "disk icerigi okunamayabilir.")]
        self.info_view.setPlainText("\n".join(satirlar))
        self.tabs.setCurrentIndex(1)
        self.status_sel.setText(f"Secili: {bilgi.name} ({human_size(bilgi.size)})")
        self._update_actions()

    def _physical_summary_text(self) -> str:
        diskler = list(getattr(self, "_physical_cache", {}).values())
        satirlar = ["SISTEMDEKI DISKLER", "=" * 52,
                    f"Yonetici/root yetkisi: "
                    f"{'VAR' if DiskSession.has_disk_privileges() else 'YOK'}", ""]
        for d in diskler:
            isaret = {"sistem": "[!]", "bagli": "[*]",
                      "bilinmiyor": "[?]", "normal": "   "}[d.risk_level]
            satirlar.append(f"{isaret} {d.name:<12} {human_size(d.size):>10}  "
                            f"{d.model[:30]}")
            satirlar.append(f"      {d.risk_text}")
        if not diskler:
            satirlar.append("  (disk bulunamadi)")
        satirlar += ["", "Bir diski acmak icin uzerine cift tiklayin.",
                     "Diskler varsayilan olarak SALT OKUNUR acilir."]
        return "\n".join(satirlar)

    def _close_tree_session(self) -> None:
        """Agacta secili goruntuyu kapatir."""
        item = self.tree.currentItem()
        if not item:
            return
        veri = item.data(0, Qt.UserRole)
        if not veri or veri[0] != "session":
            return
        hedef = self._session_by_id(veri[1])
        if hedef is None:
            return
        self.session = hedef
        self.close_image()

    def _tree_double_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        veri = item.data(0, Qt.UserRole)
        if veri and veri[0] == "phys":
            self.open_physical(write=False, path=veri[1])

    def open_physical(self, write: bool = False, path: Optional[str] = None) -> None:
        """Secili fiziksel diski acar. Yazma modu ayrica onay ister."""
        path = path or self._selected_disk_path()
        bilgi = getattr(self, "_physical_cache", {}).get(path or "")
        if bilgi is None:
            QMessageBox.information(self, "Disk secili degil",
                                    "Agactan bir fiziksel disk secin.")
            return

        allow_system = False
        if write:
            metin = (f"<b>{bilgi.display_name}</b><br>{human_size(bilgi.size)}<br><br>"
                     f"<b>Durum:</b> {bilgi.risk_text}<br><br>"
                     "Disk <b>yazma modunda</b> acilacak. Bu moddaki bolum, "
                     "bicimlendirme ve silme islemleri <b>gercek diske</b> uygulanir "
                     "ve geri alinamaz.<br><br>Devam edilsin mi?")
            if QMessageBox.warning(self, "Yazma modu onayi", metin,
                                   QMessageBox.Yes | QMessageBox.No,
                                   QMessageBox.No) != QMessageBox.Yes:
                return
            if bilgi.is_system:
                ad, tamam = QInputDialog.getText(
                    self, "Sistem diski onayi",
                    f"{bilgi.path} ISLETIM SISTEMI DISKIDIR.\n\n"
                    "Bu diske yazmak isletim sistemini acilamaz hale getirebilir.\n"
                    "Devam etmek icin disk adini yazin: " + bilgi.name)
                if not tamam or ad.strip() != bilgi.name:
                    self.log("Sistem diski yazma onayi verilmedi, islem iptal edildi")
                    return
                allow_system = True
            elif bilgi.mounted:
                if QMessageBox.warning(
                        self, "Bagli bolum uyarisi",
                        f"Bu diskte bagli bolumler var:\n{', '.join(bilgi.mounted)}\n\n"
                        "Bagli bir diske yazmak dosya sistemini bozabilir. "
                        "Once bolumleri cikarmaniz (unmount) onerilir.\n\nYine de devam edilsin mi?",
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.No) != QMessageBox.Yes:
                    return

        mevcut = self._find_open(bilgi.path)
        if mevcut is not None:
            self.session = mevcut
            self.session.close()
            self.sessions.remove(mevcut)
        try:
            self._add_session(DiskSession.open_physical(
                bilgi, readonly=not write, confirm=write, allow_system=allow_system))
        except SystemDiskError as exc:
            self.error("Sistem diski korumasi", str(exc))
            return
        except AccessDeniedError as exc:
            self.error("Yetki yetersiz", str(exc))
            return
        except PhysicalDiskError as exc:
            self.error("Disk acilamadi", str(exc))
            return
        except Exception as exc:
            self.error("Disk acilamadi", str(exc))
            return
        kip = "YAZMA" if write else "salt okunur"
        self.log(f"Fiziksel disk acildi: {bilgi.path} ({kip}) — "
                 f"{human_size(bilgi.size)}, {self.session.scheme_name}")
        self.refresh()

    # ==================================================================
    # Secim ve tazeleme
    # ==================================================================
    def select_partition(self, index: int) -> None:
        if not self.session or not self.session.table:
            return
        try:
            part = self.session.table.get(index)
        except Exception:
            return
        self.selected_partition = index
        self.selected_free = None
        self.disk_map.select_partition(index)
        self.part_table.select_partition(index)
        self._select_tree(("part", index))
        self.status_sel.setText(
            f"Secili: Bolum {index} — {part.display_name} ({human_size(part.size)})")
        self._show_partition_info(part)
        fs = None
        try:
            fs = self.session.filesystem(index)
        except Exception as exc:
            self.log(f"Dosya sistemi acilamadi: {exc}")
        self.browser.set_filesystem(fs, f"Bolum {index} ({part.fs_type or 'ham'})")
        self.hex_view.set_device(self.session.view(part),
                                 f"Bolum {index} — {part.display_name}")
        self._update_actions()

    def select_free(self, start_lba: int, sector_count: int) -> None:
        self.selected_partition = None
        self.selected_free = (start_lba, sector_count)
        self.disk_map.select_free(start_lba)
        self.part_table.select_free(start_lba)
        self._select_tree(("free", start_lba))
        boyut = sector_count * (self.session.image.sector_size if self.session else 512)
        self.status_sel.setText(f"Secili: Bos alan — {human_size(boyut)}")
        self.info_view.setPlainText(
            f"Bolumlenmemis alan\n"
            f"{'-' * 40}\n"
            f"Baslangic LBA   : {start_lba}\n"
            f"Sektor sayisi   : {sector_count}\n"
            f"Boyut           : {human_size(boyut)}\n\n"
            f"Bu alanda yeni bolum olusturabilirsiniz (Bolum > Yeni bolum).")
        self.browser.set_filesystem(None)
        if self.session:
            self.hex_view.set_device(self.session.image, "Tum goruntu")
        self._update_actions()

    def refresh(self) -> None:
        if not self.session:
            self._update_actions()
            return
        try:
            self.session.reload()
        except Exception as exc:
            self.error("Yenileme hatasi", str(exc))
            return
        oturum = self.session
        bolumler = oturum.partitions
        bos = oturum.free_regions()
        self.disk_map.set_disk(
            f"{oturum.name} — {human_size(oturum.image.size)} — {oturum.scheme_name}",
            oturum.image.sector_count, bolumler, bos)
        self.part_table.set_data(bolumler, bos)
        self._build_tree(bolumler)
        self.setWindowTitle(f"{oturum.name} — {APP_NAME}")
        self.status_file.setText(oturum.path)
        durum = (f"{oturum.scheme_name} | {human_size(oturum.image.size)} | "
                 f"{len(bolumler)} bolum")
        if oturum.readonly:
            durum += "  |  🔒 SALT OKUNUR"
            self.status_scheme.setToolTip(oturum.readonly_reason)
        else:
            self.status_scheme.setToolTip("")
        self.status_scheme.setText(durum)

        if self.selected_partition is not None and \
                any(p.index == self.selected_partition for p in bolumler):
            self.select_partition(self.selected_partition)
        elif bolumler:
            self.select_partition(bolumler[0].index)
        elif bos:
            self.select_free(bos[0].start_lba, bos[0].sector_count)
        else:
            self.status_sel.setText("")
            self.browser.set_filesystem(None)
            self.hex_view.set_device(oturum.image, "Tum goruntu")
            self.info_view.setPlainText(self._disk_summary_text())
        self._update_actions()

    def _content_changed(self) -> None:
        """Dosya gezgininde degisiklik olunca doluluk gostergelerini tazele."""
        index = self.selected_partition
        if index is None or not self.session:
            return
        try:
            self.session._fs_info.pop(index, None)
            part = self.session.table.get(index)
            info = self.session.detect_fs(part)
            part.fs_used, part.fs_total = info.used_bytes, info.total_bytes
            self.disk_map.set_disk(
                f"{self.session.name} — {human_size(self.session.image.size)} — "
                f"{self.session.scheme_name}",
                self.session.image.sector_count, self.session.partitions,
                self.session.free_regions())
            self.disk_map.select_partition(index)
            self.part_table.set_data(self.session.partitions, self.session.free_regions())
            self.part_table.select_partition(index)
            self._show_partition_info(part)
        except Exception:
            pass

    # ==================================================================
    # Agac
    # ==================================================================
    def _build_tree(self, bolumler=None) -> None:
        """Agaci kurar: once fiziksel diskler, sonra ACIK TUM goruntuler."""
        self.tree.clear()
        self._add_physical_disks()
        for oturum in self.sessions:
            self._add_session_node(oturum)

    def _add_session_node(self, oturum: DiskSession) -> None:
        """Bir acik goruntu/disk icin agac dali olusturur."""
        etkin = oturum is self.session
        baslik = (f"{oturum.name} — {human_size(oturum.image.size)} — "
                  f"{oturum.scheme_name}")
        if oturum.readonly:
            baslik += "  [salt okunur]"
        kok = QTreeWidgetItem(self.tree, [baslik])
        kok.setIcon(0, self._icon(QStyle.SP_DriveHDIcon if oturum.is_physical
                                  else QStyle.SP_DriveFDIcon))
        kok.setData(0, Qt.UserRole, ("session", id(oturum)))
        kok.setToolTip(0, f"{oturum.path or oturum.name}\n{oturum.format_name}")
        f = kok.font(0)
        f.setBold(etkin)            # etkin oturum kalin gosterilir
        kok.setFont(0, f)

        for p in oturum.partitions:
            metin = f"Bolum {p.index}: {p.display_name}"
            item = QTreeWidgetItem(kok, [metin])
            item.setIcon(0, color_chip(fs_color(p.fs_type), 12))
            item.setData(0, Qt.UserRole, ("part", (id(oturum), p.index)))
            item.setToolTip(0, f"{p.fs_type or 'Bicimlendirilmemis'} — "
                               f"{human_size(p.size)}")
        for r in oturum.free_regions():
            item = QTreeWidgetItem(kok, [f"Bos alan ({human_size(r.size)})"])
            item.setForeground(0, palette_color(self.tree, "dim"))
            item.setData(0, Qt.UserRole, ("free", (id(oturum), r.start_lba)))
        kok.setExpanded(etkin)

    def _session_by_id(self, oturum_id: int) -> Optional[DiskSession]:
        for s in self.sessions:
            if id(s) == oturum_id:
                return s
        return None

    def _add_physical_disks(self) -> None:
        """Agacin ustune sistemdeki fiziksel diskleri ekler (hicbirini acmadan)."""
        try:
            diskler = DiskSession.list_physical_disks()
        except Exception as exc:
            self.log(f"Disk listesi alinamadi: {exc}")
            return
        self._physical_cache = {d.path: d for d in diskler}
        kok = QTreeWidgetItem(self.tree, [f"Fiziksel Diskler ({len(diskler)})"])
        kok.setIcon(0, self._icon(QStyle.SP_ComputerIcon))
        kok.setData(0, Qt.UserRole, ("physroot", 0))
        f = kok.font(0); f.setBold(True); kok.setFont(0, f)

        for d in diskler:
            metin = f"{d.name} — {d.model or 'bilinmeyen'} ({human_size(d.size)})"
            item = QTreeWidgetItem(kok, [metin])
            item.setData(0, Qt.UserRole, ("phys", d.path))
            if d.risk_level == "sistem":
                # Uyari bilgisi kaybolmasin: ikon isletim sistemi amblemi olur,
                # "sistem diski" uyarisi metin ve renkle verilir.
                if d.os_hint:
                    item.setIcon(0, os_icon(d.os_hint, 16))
                    etiket = f"[{d.os_label} SISTEM DISKI]"
                else:
                    item.setIcon(0, self._icon(QStyle.SP_MessageBoxWarning))
                    etiket = "[SISTEM DISKI]"
                item.setForeground(0, QColor("#c0392b"))
                item.setText(0, f"{metin}  {etiket}")
            elif d.risk_level == "bilinmiyor":
                item.setIcon(0, self._icon(QStyle.SP_MessageBoxQuestion))
                item.setText(0, f"{d.name} — (yetki yok, bilgi okunamadi)")
            elif d.risk_level == "bagli":
                item.setIcon(0, self._icon(QStyle.SP_DriveHDIcon))
                item.setText(0, metin + "  [bagli bolum var]")
            elif d.os_hint:
                item.setIcon(0, os_icon(d.os_hint, 16))
                item.setText(0, f"{metin}  [{d.os_label}]")
            elif d.removable:
                item.setIcon(0, self._icon(QStyle.SP_DriveNetIcon))
            else:
                item.setIcon(0, self._icon(QStyle.SP_DriveHDIcon))
            item.setToolTip(0, f"{d.path}\n{d.risk_text}\n"
                               f"Sektor: {d.sector_size} B | Baglanti: {d.bus or '-'}")
        kok.setExpanded(True)
        if not diskler:
            bos = QTreeWidgetItem(kok, ["(disk bulunamadi)"])
            bos.setDisabled(True)

    def _select_tree(self, key) -> None:
        """Etkin oturumun dalinda verilen ogeyi secer."""
        if not self.session:
            return
        tur, deger = key
        hedef = (tur, (id(self.session), deger))
        for i in range(self.tree.topLevelItemCount()):
            kok = self.tree.topLevelItem(i)
            for j in range(kok.childCount()):
                child = kok.child(j)
                if child.data(0, Qt.UserRole) == hedef:
                    self.tree.blockSignals(True)
                    self.tree.setCurrentItem(child)
                    self.tree.blockSignals(False)
                    return

    def _tree_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        veri = item.data(0, Qt.UserRole)
        if not veri:
            return
        tur, deger = veri
        if tur == "phys":
            self.show_disk_info(deger)
            return
        if tur == "physroot":
            self.info_view.setPlainText(self._physical_summary_text())
            return
        if tur == "part":
            oturum_id, index = deger
            hedef = self._session_by_id(oturum_id)
            if hedef is None:
                return
            if hedef is not self.session:
                self.session = hedef            # baska goruntunun bolumu secildi
                self.selected_partition = index
                self.refresh()
            else:
                self.select_partition(index)
        elif tur == "free":
            oturum_id, lba = deger
            hedef = self._session_by_id(oturum_id)
            if hedef is None:
                return
            if hedef is not self.session:
                self.session = hedef
                self.refresh()
            for r in self.session.free_regions():
                if r.start_lba == lba:
                    self.select_free(r.start_lba, r.sector_count)
                    return
        elif tur == "session":
            hedef = self._session_by_id(deger)
            if hedef is None:
                return
            if hedef is not self.session:
                self.session = hedef
                self.selected_partition = None
                self.selected_free = None
                self.refresh()
                return
            self.selected_partition = None
            self.selected_free = None
            self.info_view.setPlainText(self._disk_summary_text())
            self.hex_view.set_device(self.session.image, "Tum goruntu")
            self.browser.set_filesystem(None)
            self._update_actions()

    # ==================================================================
    # Baglam menuleri
    # ==================================================================
    def _partition_menu(self, part: Partition) -> QMenu:
        menu = QMenu(self)
        yazilabilir = bool(self.session) and not self.session.readonly
        menu.addAction("Dosya gezgininde ac", lambda: (self.select_partition(part.index),
                                                       self.tabs.setCurrentIndex(0)))
        menu.addSeparator()
        if not yazilabilir:
            # Salt okunur kaynakta yazma eylemleri gorunur ama secilemez;
            # nedenini ogrenmek icin tek bir giris birakilir.
            bilgi = menu.addAction("(salt okunur — degisiklik yapilamaz)",
                                   self._read_only_uyarisi)
            f = bilgi.font(); f.setItalic(True); bilgi.setFont(f)
        eylem = menu.addAction("Bicimlendir...", self.format_partition)
        eylem.setEnabled(yazilabilir)
        eylem = menu.addAction("Bolumu boyutlandir...", self.resize_partition)
        eylem.setEnabled(yazilabilir)
        eylem = menu.addAction("Birim etiketini degistir...", self.change_label)
        eylem.setEnabled(yazilabilir)
        if self.session.scheme == "gpt":
            menu.addAction("Bolum adini degistir...", self.rename_partition)
        menu.addAction("Bolum turunu degistir...", self.change_type)
        menu.addAction(
            "Onyukleme bayragini kaldir" if part.bootable else "Onyuklenebilir yap",
            self.toggle_bootable)
        menu.addSeparator()
        menu.addAction("Bolumu yedekle...", lambda: self.backup(disk=False))
        eylem = menu.addAction("Bolume geri yukle...", lambda: self.restore(disk=False))
        eylem.setEnabled(yazilabilir)
        menu.addAction("Silinmis dosyalari tara...", self.scan_deleted)
        eylem = menu.addAction("Guvenli sil...", lambda: self.wipe(disk=False))
        eylem.setEnabled(yazilabilir)
        menu.addSeparator()
        eylem = menu.addAction("Bolumu sil", self.delete_partition)
        eylem.setEnabled(yazilabilir)
        return menu

    def _free_menu(self) -> QMenu:
        menu = QMenu(self)
        yazilabilir = bool(self.session) and not self.session.readonly
        eylem = menu.addAction("Yeni bolum olustur...", self.create_partition)
        eylem.setEnabled(yazilabilir)
        if not yazilabilir:
            bilgi = menu.addAction("(salt okunur — neden?)", self._read_only_uyarisi)
            f = bilgi.font(); f.setItalic(True); bilgi.setFont(f)
        return menu

    def _map_context(self, hedef, konum) -> None:
        if not self.session:
            return
        if hedef is None:
            menu = QMenu(self)
            menu.addAction("Yenile", self.refresh)
            menu.exec_(konum)
            return
        tur, obj = hedef
        (self._partition_menu(obj) if tur == "part" else self._free_menu()).exec_(konum)

    def _table_context(self, hedef, konum) -> None:
        self._map_context(hedef, konum)

    def _tree_context(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if not item or not self.session:
            return
        veri = item.data(0, Qt.UserRole)
        if not veri:
            return
        tur, deger = veri
        kuresel = self.tree.viewport().mapToGlobal(pos)
        if tur == "part":
            oturum_id, index = deger
            hedef = self._session_by_id(oturum_id)
            if hedef is None:
                return
            if hedef is not self.session:
                self.session = hedef
                self.selected_partition = index
                self.refresh()
            else:
                self.select_partition(index)
            self._partition_menu(self.session.table.get(index)).exec_(kuresel)
        elif tur == "free":
            self._free_menu().exec_(kuresel)
        elif tur == "session":
            menu = QMenu(self)
            menu.addAction("Bu goruntuyu kapat", self._close_tree_session)
            menu.addSeparator()
            menu.addAction("Yenile", self.refresh)
            menu.exec_(kuresel)
        else:
            menu = QMenu(self)
            menu.addAction("MBR bolum tablosu olustur", lambda: self.create_table("mbr"))
            menu.addAction("GPT bolum tablosu olustur", lambda: self.create_table("gpt"))
            menu.addSeparator()
            menu.addAction("Goruntu boyutunu degistir...", self.resize_image)
            menu.addAction("Yenile", self.refresh)
            menu.exec_(kuresel)

    # ==================================================================
    # Bilgi panelleri
    # ==================================================================
    def _disk_summary_text(self) -> str:
        if not self.session:
            return ""
        satirlar = ["DISK GORUNTUSU", "=" * 52]
        for anahtar, deger in self.session.summary().items():
            satirlar.append(f"{anahtar:<16}: {deger}")
        satirlar.append("")
        satirlar.append("BOLUMLER")
        satirlar.append("-" * 52)
        for p in self.session.partitions:
            satirlar.append(
                f"  {p.index:>2}. {p.display_name:<22} {p.fs_type or '-':<8} "
                f"{human_size(p.size):>10}  LBA {p.start_lba}-{p.end_lba}")
        if not self.session.partitions:
            satirlar.append("  (bolum yok)")
        return "\n".join(satirlar)

    def _show_partition_info(self, part: Partition) -> None:
        s = self.session
        info = s.detect_fs(part)
        satirlar = [f"BOLUM {part.index}", "=" * 52]
        alanlar = [
            ("Ad", part.display_name),
            ("Sema", s.scheme_name),
            ("Tur", part.type_name),
            ("Dosya sistemi", part.fs_type or "Bicimlendirilmemis"),
            ("Birim etiketi", info.label or "-"),
            ("Boyut", f"{human_size(part.size)} ({part.sector_count} sektor)"),
            ("Baslangic LBA", str(part.start_lba)),
            ("Bitis LBA", str(part.end_lba)),
            ("Bayt ofseti", f"{part.start_lba * part.sector_size:,}".replace(",", ".")),
            ("Onyuklenebilir", "Evet" if part.bootable else "Hayir"),
        ]
        if part.logical:
            alanlar.append(("Konum", f"Mantiksal (EBR: LBA {part.ebr_lba})"))
        if s.scheme == "gpt":
            alanlar.append(("Tur GUID", part.type_guid))
            alanlar.append(("Bolum GUID", part.part_guid))
            alanlar.append(("Oznitelikler", f"0x{part.attributes:016X}"))
        if info.cluster_size > 0:
            alanlar.append(("Kume/blok boyutu", human_size(info.cluster_size)))
        if info.uuid:
            alanlar.append(("UUID / Seri no", info.uuid))
        if info.total_bytes >= 0 and info.used_bytes >= 0:
            oran = 100 * info.used_bytes / max(1, info.total_bytes)
            alanlar.append(("Kullanilan", f"{human_size(info.used_bytes)} (%{oran:.1f})"))
            alanlar.append(("Bos", human_size(info.free_bytes)))
        for anahtar, deger in alanlar:
            satirlar.append(f"{anahtar:<18}: {deger}")
        self.info_view.setPlainText("\n".join(satirlar))

    # ==================================================================
    # Yardimcilar
    # ==================================================================
    def _current_partition(self) -> Optional[Partition]:
        if not self._require_session():
            return None
        if self.selected_partition is None or not self.session.table:
            QMessageBox.information(self, "Bolum secili degil",
                                    "Once listeden veya haritadan bir bolum secin.")
            return None
        try:
            return self.session.table.get(self.selected_partition)
        except Exception:
            return None

    def _require_session(self) -> bool:
        if self.session is None:
            QMessageBox.information(self, "Goruntu yok",
                                    "Once bir disk goruntusu acin veya olusturun.")
            return False
        if self.session.readonly:
            self._read_only_uyarisi()
            return False
        return True

    def _salt_okunur_acilis_uyarisi(self, yol: str) -> None:
        """Dosya salt okunur acildiginda nedeni gosterir ve yeniden denemeyi sunar."""
        neden = self.session.readonly_reason
        self.log(f"DIKKAT: salt okunur acildi — {neden}")
        kilit = "kilitlenmis" in neden or "kullaniliyor" in neden
        metin = (f"<b>{os.path.basename(yol)}</b> salt okunur acildi; "
                 "bu dosyada degisiklik yapilamaz.<br><br>"
                 f"<b>Neden:</b> {neden}<br>"
                 f"<b>Yol:</b> {yol}<br>"
                 f"<b>Bicim:</b> {self.session.format_name}")
        if kilit:
            metin += ("<br><br>Dosyayi kullanan diger programi (baska bir disk "
                      "araci, yedekleme yazilimi vb.) kapatip <b>Yeniden dene</b>ye "
                      "basin.")
            kutu = QMessageBox(QMessageBox.Warning, "Salt okunur acildi", metin,
                               QMessageBox.NoButton, self)
            yeniden = kutu.addButton("Yeniden dene", QMessageBox.AcceptRole)
            kutu.addButton("Salt okunur devam et", QMessageBox.RejectRole)
            kutu.exec_()
            if kutu.clickedButton() is yeniden:
                self.close_image()
                self.open_path(yol)
            return
        QMessageBox.warning(self, "Salt okunur acildi", metin)

    def _read_only_uyarisi(self) -> None:
        """Salt okunur nedenini gosterir; mumkunse cozumu de sunar."""
        neden = self.session.readonly_reason
        ad = self.session.name
        if self.session.is_physical:
            bilgi = self.session.disk_info
            cevap = QMessageBox.question(
                self, "Disk salt okunur",
                f"<b>{ad}</b> salt okunur acik.<br><br>{neden}<br><br>"
                "Simdi <b>yazma modunda</b> acilsin mi?<br>"
                "<i>(Yazma modunda bolum, bicimlendirme ve silme islemleri "
                "gercek diske uygulanir.)</i>",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if cevap == QMessageBox.Yes and bilgi is not None:
                self.open_physical(write=True, path=bilgi.path)
            return
        QMessageBox.information(
            self, "Salt okunur",
            f"<b>{ad}</b> salt okunur acik; degisiklik yapilamaz.<br><br>{neden}")

    def _update_actions(self) -> None:
        acik = self.session is not None
        yazilabilir = acik and not self.session.readonly
        tablo_var = acik and self.session.table is not None
        bolum_secili = acik and self.selected_partition is not None
        self.act_close.setEnabled(acik)
        self.act_refresh.setEnabled(acik)
        self.act_mbr.setEnabled(yazilabilir)
        self.act_gpt.setEnabled(yazilabilir)
        self.act_clear_table.setEnabled(yazilabilir and tablo_var)
        self.act_resize_img.setEnabled(yazilabilir)
        self.act_create_part.setEnabled(yazilabilir)
        for act in (self.act_format, self.act_resize_part, self.act_delete_part,
                    self.act_boot, self.act_type_part, self.act_label):
            act.setEnabled(yazilabilir and bolum_secili)
        self.act_rename_part.setEnabled(
            yazilabilir and bolum_secili and self.session.scheme == "gpt")
        # donusum / bakim
        self.act_to_gpt.setEnabled(yazilabilir and self.session.scheme == "mbr")
        self.act_to_mbr.setEnabled(yazilabilir and self.session.scheme == "gpt")
        self.act_alignment.setEnabled(tablo_var)
        # yedekleme ve klonlama
        self.act_backup_disk.setEnabled(acik)
        self.act_clone_disk.setEnabled(acik)
        self.act_restore_disk.setEnabled(yazilabilir)
        self.act_backup_part.setEnabled(acik and bolum_secili)
        self.act_restore_part.setEnabled(yazilabilir and bolum_secili)
        # silme
        self.act_wipe_disk.setEnabled(yazilabilir)
        self.act_wipe_part.setEnabled(yazilabilir and bolum_secili)
        # fiziksel diskler
        disk_secili = self._selected_disk_path() is not None
        self.act_refresh_disks.setEnabled(True)
        self.act_open_disk_ro.setEnabled(disk_secili)
        self.act_open_disk_rw.setEnabled(disk_secili)
        self.act_disk_info.setEnabled(disk_secili)
        # kurtarma
        self.act_scan_deleted.setEnabled(acik and bolum_secili)
        self.act_scan_lost.setEnabled(acik)
        self.act_carve.setEnabled(acik)

    def about(self) -> None:
        QMessageBox.about(
            self, f"{APP_NAME} hakkinda",
            f"<h3>{APP_NAME} {APP_VERSION}</h3>"
            "<p>Ham disk goruntusu (.img) olusturma, bolumleme, bicimlendirme "
            "ve dosya erisimi araci.</p>"
            "<p><b>Teknoloji:</b> Python 3 + PyQt5<br>"
            "<b>Bolum tablolari:</b> MBR (mantiksal bolumler dahil), GPT<br>"
            "<b>Dosya sistemleri:</b> FAT12/16/32 (saf Python, tam okuma/yazma), "
            "exFAT / NTFS / ext2-3-4 (mkfs araclariyla bicimlendirme)</p>"
            "<p>Yonetici yetkisi gerektirmez; yalnizca secilen goruntu dosyasi "
            "uzerinde calisir.</p>")

    def closeEvent(self, event) -> None:
        self.close_all()
        super().closeEvent(event)
