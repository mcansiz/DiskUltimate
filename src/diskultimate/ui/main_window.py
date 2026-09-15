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
from ..core.clone import is_backup_file
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
from .theme import fs_color, os_icon, palette_color, standard_icon
from .widgets.disk_map import DiskMapWidget
from .widgets.file_browser import FileBrowser
from .widgets.hex_view import HexViewer
from .widgets.partition_table import PartitionTableWidget, color_chip

APP_NAME = "DiskUltimate"
# Surumun tek kaynagi burasidir. Degistirildiginde README.md'deki surum rozeti
# ve .claude/docs/project-overview.md "Durum" satiri da guncellenir.
APP_VERSION = "0.3.0"

# Sekme sirasi tek yerden tanimlanir; `tabs.setCurrentIndex` cagrilari ciplak
# sayi kullanmaz, boylece sekme sirasi degisince sessizce yanlis sekme acilmaz.
TAB_FILES = 0
TAB_INFO = 1
TAB_HEX = 2
TAB_LOG = 3

# Onyukleme bayragi eylemi tek eylemdir, metni secime gore degisir.
BOOT_SET_TEXT = "Onyukleme bayragini koy"
BOOT_CLEAR_TEXT = "Onyukleme bayragini kaldir"

# Fiziksel disk listesinin yoklanma araligi (ms). USB/SD aygitlar uygulama
# acikken takilabildigi icin liste kendiliginden tazelenir. Tarama ucuzdur
# (~20 ms) ve agac yalnizca liste GERCEKTEN degistiginde yeniden kurulur.
DISK_POLL_MS = 3000


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

        # Takilan/cikarilan aygitlar kendiliginden yakalansin.
        self._last_disk_signature = self._disk_signature(
            self._physical_cache.values())
        self._disk_timer = QTimer(self)
        self._disk_timer.setInterval(DISK_POLL_MS)
        self._disk_timer.timeout.connect(self._poll_disks)
        self._disk_timer.start()

    # ==================================================================
    # Arayuz kurulumu
    # ==================================================================
    def _icon(self, standard) -> QIcon:
        return standard_icon(self, standard)

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
        self.disk_map.partitionActivated.connect(
            lambda i: self.tabs.setCurrentIndex(TAB_FILES))
        self.disk_map.freeActivated.connect(lambda s, n: self.create_partition())
        self.disk_map.contextMenuRequested.connect(self._map_context)
        sag_duzen.addWidget(self.disk_map)

        bottom_splitter = QSplitter(Qt.Vertical)
        self.part_table = PartitionTableWidget()
        self.part_table.setMinimumHeight(120)
        self.part_table.partitionSelected.connect(self.select_partition)
        self.part_table.freeSelected.connect(self.select_free)
        self.part_table.partitionActivated.connect(
            lambda i: self.tabs.setCurrentIndex(TAB_FILES))
        self.part_table.contextMenuRequested.connect(self._table_context)
        bottom_splitter.addWidget(self.part_table)

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
        bottom_splitter.addWidget(self.tabs)
        bottom_splitter.setSizes([180, 420])
        sag_duzen.addWidget(bottom_splitter, 1)
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
        # `showMessage()` gecici mesaji durum cubugunun **sol** bolgesine cizer —
        # yani `status_file` ile ayni yere. Qt'nin bu durumda normal widget'lari
        # gizlemesi beklenir ama PyQt5 5.15'te gizlemiyor; iki metin ust uste
        # binip okunmaz hale geliyordu. Gorunurlugu mesaja gore kendimiz
        # yonetiyoruz: mesaj varken etiket saklanir, mesaj bitince geri gelir.
        self.statusBar().messageChanged.connect(
            lambda metin: self.status_file.setVisible(not metin))

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
        # Metin secili bolume gore `_update_actions()` icinde guncellenir;
        # asagidaki ikisi birbirinin tam karsiligidir.
        self.act_boot = QAction(BOOT_SET_TEXT, self)
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
        self.act_write_backup = QAction("Yedegi diske yaz...", self)
        self.act_write_backup.setToolTip(
            "Acik .dub yedegini yeni bir goruntu dosyasina veya "
            "fiziksel diske yazar")
        self.act_write_backup.triggered.connect(self.write_backup_to_target)
        self.act_backup_info = QAction("Yedek dosyasi bilgisi...", self)
        self.act_backup_info.triggered.connect(self.show_backup_info)
        self.act_sysinfo = QAction("Sistem bilgisi", self)
        self.act_sysinfo.triggered.connect(self.show_system_info)

        # --- Yardim ---
        self.act_about = QAction("Hakkinda", self)
        self.act_about.triggered.connect(self.about)

        menu = self.menuBar()
        m_file = menu.addMenu("&Dosya")
        m_file.addAction(self.act_new)
        m_file.addAction(self.act_new_vhd)
        m_file.addAction(self.act_open)
        m_file.addAction(self.act_close)
        m_file.addSeparator()
        m_file.addAction(self.act_quit)

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
        m_disk.addAction(self.act_write_backup)
        m_disk.addAction(self.act_clone_disk)
        m_disk.addAction(self.act_wipe_disk)
        m_disk.addSeparator()
        m_disk.addAction(self.act_resize_img)
        m_disk.addAction(self.act_refresh)

        m_part = menu.addMenu("&Bolum")
        m_part.addAction(self.act_create_part)
        m_part.addAction(self.act_format)
        m_part.addAction(self.act_resize_part)
        m_part.addAction(self.act_delete_part)
        m_part.addSeparator()
        m_part.addAction(self.act_label)
        m_part.addAction(self.act_rename_part)
        m_part.addAction(self.act_type_part)
        m_part.addAction(self.act_boot)
        m_part.addSeparator()
        m_part.addAction(self.act_backup_part)
        m_part.addAction(self.act_restore_part)
        m_part.addAction(self.act_wipe_part)

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
    def log(self, message: str) -> None:
        zaman = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{zaman}] {message}")
        self.statusBar().showMessage(message, 6000)
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            gun = datetime.datetime.now().strftime("%Y-%m-%d")
            with open(os.path.join(LOG_DIR, f"app-{gun}.log"), "a", encoding="utf-8") as fh:
                fh.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} {message}\n")
        except OSError:
            pass

    def error(self, title: str, message: str) -> None:
        QMessageBox.critical(self, title, message)
        self.log(f"HATA — {title}: {message}")

    # ==================================================================
    # Goruntu islemleri
    # ==================================================================
    def new_image(self) -> None:
        default = os.path.dirname(self.session.path) if self.session else ""
        dlg = NewImageDialog(self, default)
        if exec_dialog(dlg) != NewImageDialog.Accepted:
            return
        v = dlg.values()

        def task(progress):
            progress("Goruntu dosyasi olusturuluyor...", 10)
            session = DiskSession.create(v["path"], v["size"], sparse=v["sparse"],
                                        scheme=v["scheme"], overwrite=True)
            if v["auto_partition"]:
                progress("Bolum olusturuluyor...", 40)
                free = session.free_regions()
                if free:
                    en_buyuk = max(free, key=lambda r: r.sector_count)
                    session.create_partition(en_buyuk.start_lba, en_buyuk.sector_count,
                                            fs_key=v["fs"], label=v["label"],
                                            name=v["label"],
                                            progress=lambda m, p: progress(m, 40 + p // 2))
            return session

        ok, result = run_task(self, "Yeni disk goruntusu", task)
        if not ok:
            self.error("Goruntu olusturulamadi", str(result))
            return
        self._add_session(result)
        self.log(f"Goruntu olusturuldu: {v['path']} ({human_size(v['size'])})")
        self.refresh()

    def open_image(self) -> None:
        desen = " ".join(f"*.{u}" for u in IMAGE_EXTENSIONS)
        path, _ = QFileDialog.getOpenFileName(
            self, "Disk goruntusu ac", "",
            f"Disk goruntuleri ({desen});;Ham goruntu (*.img *.raw *.dd *.bin);;"
            "Sanal diskler (*.vhd *.vhdx *.vdi *.vmdk *.qcow2);;Tum dosyalar (*)")
        if not path:
            return
        self.open_path(path)

    def open_path(self, path: str) -> None:
        mevcut = self._find_open(path)
        if mevcut is not None:
            self.log(f"Zaten acik, one getirildi: {path}")
            self.session = mevcut
            self.refresh()
            return
        try:
            self._add_session(DiskSession.open(path))
        except Exception as exc:
            self.error("Goruntu acilamadi", str(exc))
            return
        # Salt okunur acildiysa kullanici bunu bicimlendirmeye calisirken degil,
        # HEMEN ogrenmeli. Yedek dosyasinin salt okunur olmasi ise bir kusur
        # degil, dogasidir; onun icin ayri ve bilgilendirici bir not gosterilir.
        if self.session.is_backup:
            self._backup_opened_note()
        elif self.session.readonly and not self.session.is_physical:
            self._readonly_open_warning(path)
        self.log(f"Goruntu acildi: {path} — {self.session.format_name}, "
                 f"{human_size(self.session.image.size)}, {self.session.scheme_name}")
        self.refresh()

    def _backup_opened_note(self) -> None:
        """Yedek acildiginda bir kereye mahsus bilgilendirme.

        Yedek artik **gezilebilir** (bkz. `clone.DubImage`): bolum tablosu ve
        dosyalar geri yukleme olmadan gorulur. Ancak salt okunurdur ve bu bir
        kusur degil, dogasidir; kullanici nasil yazacagini burada ogrenir.
        """
        info = getattr(self.session.image, "info", None)
        if info is None:
            return
        self.log(f"Yedek acildi (salt okunur): {os.path.basename(info.path)} — "
                 f"kaynak {human_size(info.total_bytes)}, "
                 f"yedek {human_size(info.file_size)}")
        if getattr(self, "_backup_note_shown", False):
            return
        self._backup_note_shown = True
        box = QMessageBox(QMessageBox.Information, "Yedek dosyasi acildi",
                           f"<b>{os.path.basename(info.path)}</b> bir DiskUltimate "
                           "yedegidir. Icerigi <b>salt okunur</b> olarak "
                           "gezebilirsiniz: bolumler, klasorler ve dosyalar "
                           "gorunur, dosyalari disa aktarabilirsiniz.<br><br>"
                           f"<b>Kaynak boyut:</b> {human_size(info.total_bytes)}<br>"
                           f"<b>Yedek boyut:</b> {human_size(info.file_size)}<br>"
                           f"<b>Olusturma:</b> "
                           f"{info.created.strftime('%Y-%m-%d %H:%M') if info.created else '-'}"
                           "<br><br>Yedegi bir <b>diske veya goruntuye yazmak</b> "
                           "icin: <i>Disk &gt; Yedegi diske yaz...</i>",
                           QMessageBox.NoButton, self)
        write_btn = box.addButton("Diske yaz...", QMessageBox.AcceptRole)
        box.addButton("Simdilik gez", QMessageBox.RejectRole)
        box.exec_()
        if box.clickedButton() is write_btn:
            self.write_backup_to_target()

    # ------------------------------------------------------------------
    # Yedegi hedefe yazma (goruntu dosyasi veya fiziksel disk)
    # ------------------------------------------------------------------
    def write_backup_to_target(self) -> None:
        """Acik yedegi bir hedefe yazar: yeni goruntu dosyasi veya fiziksel disk.

        Hedef fiziksel diskse `open_physical`in tum guvenlik kapilari gecerlidir
        (yazma onayi, sistem diskinde ad dogrulama, bagli bolum uyarisi).
        """
        if not (self.session and self.session.is_backup):
            QMessageBox.information(self, "Yedek acik degil",
                                    "Once bir .dub yedek dosyasi acin.")
            return
        info = self.session.image.info
        box = QMessageBox(QMessageBox.Question, "Yedek nereye yazilsin?",
                           f"<b>{os.path.basename(info.path)}</b> "
                           f"({human_size(info.total_bytes)}) nereye yazilsin?"
                           "<br><br>Hedefteki <b>tum veriler</b> uzerine yazilir.",
                           QMessageBox.NoButton, self)
        file_btn = box.addButton("Yeni goruntu dosyasi...", QMessageBox.AcceptRole)
        disk_btn = box.addButton("Fiziksel disk...", QMessageBox.ActionRole)
        box.addButton("Vazgec", QMessageBox.RejectRole)
        box.exec_()
        if box.clickedButton() is file_btn:
            self._restore_to_new_image(info.path, info)
        elif box.clickedButton() is disk_btn:
            self._restore_to_physical(info)

    def _restore_to_physical(self, info) -> None:
        """Yedegi secili fiziksel diske yazar (katmanli onaydan gecerek)."""
        path = self._selected_disk_path()
        disk = getattr(self, "_physical_cache", {}).get(path or "")
        if disk is None:
            QMessageBox.information(
                self, "Disk secili degil",
                "Once sol agactan hedef fiziksel diski secin.\n\n"
                "Disk gorunmuyorsa: Disk > Fiziksel diskleri yenile.")
            return
        if not disk.info_complete:
            self.error("Disk bilgisi eksik",
                       f"{disk.name} hakkinda yeterli bilgi alinamadi "
                       "(yonetici yetkisi gerekebilir). Bilgisi eksik bir diske "
                       "yazma reddedilir.")
            return
        if info.total_bytes > disk.size:
            self.error("Disk cok kucuk",
                       f"Yedek {human_size(info.total_bytes)}, hedef disk "
                       f"{human_size(disk.size)}.")
            return

        metin = (f"<b>{disk.display_name}</b><br>{human_size(disk.size)}<br><br>"
                 f"<b>Durum:</b> {disk.risk_text}<br><br>"
                 f"Yedek: {os.path.basename(info.path)} "
                 f"({human_size(info.total_bytes)})<br><br>"
                 "Bu diskteki <b>tum veriler silinecek</b> ve yedek icerigi "
                 "yazilacak. Islem <b>geri alinamaz</b>.<br><br>Devam edilsin mi?")
        if QMessageBox.warning(self, "Fiziksel diske yazma onayi", metin,
                               QMessageBox.Yes | QMessageBox.No,
                               QMessageBox.No) != QMessageBox.Yes:
            return
        allow_system = False
        if disk.is_system:
            name, ok = QInputDialog.getText(
                self, "Sistem diski onayi",
                f"{disk.path} ISLETIM SISTEMI DISKIDIR.\n\n"
                "Uzerine yazmak isletim sistemini acilamaz hale getirir.\n"
                "Devam etmek icin disk adini yazin: " + disk.name)
            if not ok or name.strip() != disk.name:
                self.log("Sistem diski yazma onayi verilmedi, islem iptal edildi")
                return
            allow_system = True
        elif disk.mounted:
            if QMessageBox.warning(
                    self, "Bagli bolum uyarisi",
                    f"Bu diskte bagli bolumler var:\n{', '.join(disk.mounted)}\n\n"
                    "Yazmadan once bolumleri cikarmaniz onerilir.\n\n"
                    "Yine de devam edilsin mi?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No) != QMessageBox.Yes:
                return

        backup_path = info.path

        def task(progress):
            return DiskSession.restore_to_physical(
                backup_path, disk, allow_system=allow_system, progress=progress)

        ok, result = run_task(self, "Yedek diske yaziliyor", task)
        if not ok:
            self.error("Yazma basarisiz", str(result))
            return
        self.log(f"Yedek diske yazildi: {os.path.basename(backup_path)} -> "
                 f"{disk.path} ({human_size(info.total_bytes)})")
        QMessageBox.information(
            self, "Yazma tamamlandi",
            f"{os.path.basename(backup_path)} -> {disk.display_name}\n\n"
            "Isletim sisteminin yeni bolum tablosunu gormesi icin diski "
            "cikarip yeniden takmaniz gerekebilir.")
        self.refresh_disks()

    def _restore_to_new_image(self, backup_path: str, info) -> None:
        """Yedegi yeni bir goruntu dosyasina acar ve acilan goruntuyu gosterir.

        Boylece "yedegi acmak" kullanicinin bekledigi sonucu verir: icerik
        gezilebilir hale gelir, mevcut hicbir disk uzerine yazilmaz.
        """
        onerilen = os.path.splitext(backup_path)[0]
        if not onerilen.lower().endswith((".img", ".raw", ".dd")):
            onerilen += ".img"
        target, _ = QFileDialog.getSaveFileName(
            self, "Geri yuklenecek goruntu dosyasi", onerilen,
            "Disk goruntusu (*.img *.raw *.dd);;Tum dosyalar (*)")
        if not target:
            return
        if os.path.abspath(target) == os.path.abspath(backup_path):
            self.error("Gecersiz hedef", "Hedef, yedek dosyasinin kendisi olamaz.")
            return

        def task(progress):
            return DiskSession.restore_to_new_image(backup_path, target,
                                                    progress=progress)

        ok, result = run_task(self, "Yedek geri yukleniyor", task)
        if not ok:
            self.error("Geri yukleme basarisiz", str(result))
            return
        self.log(f"Yedek geri yuklendi: {os.path.basename(backup_path)} -> "
                 f"{target} ({human_size(info.total_bytes)})")
        self.open_path(target)

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
        self.part_table.set_partitions([], [])
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
        for session in list(self.sessions):
            try:
                session.close()
            except Exception:
                pass
        self.sessions.clear()
        self.session = None

    def resize_image(self) -> None:
        if not self._require_session():
            return
        mevcut = self.session.image.size
        metin, ok = QInputDialog.getText(
            self, "Goruntu boyutu",
            f"Mevcut boyut: {human_size(mevcut)}\n\nYeni boyut (orn. 4 GB, 512 MB):",
            text=human_size(mevcut))
        if not ok:
            return
        try:
            new = parse_size(metin)
        except Exception:
            self.error("Gecersiz boyut", f"Boyut cozumlenemedi: {metin}")
            return
        if new < mevcut:
            cevap = QMessageBox.warning(
                self, "Kucultme uyarisi",
                "Goruntuyu kucultmek sondaki verileri kalici olarak siler.\n"
                "Devam edilsin mi?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if cevap != QMessageBox.Yes:
                return
        try:
            self.session.resize_image(new)
        except Exception as exc:
            self.error("Boyutlandirma basarisiz", str(exc))
            return
        self.log(f"Goruntu boyutu degistirildi: {human_size(mevcut)} -> {human_size(new)}")
        self.refresh()

    # ==================================================================
    # Tablo islemleri
    # ==================================================================
    def create_table(self, scheme: str) -> None:
        if not self._require_session():
            return
        name = {"mbr": "MBR", "gpt": "GPT"}[scheme]
        if self.session.partitions:
            cevap = QMessageBox.warning(
                self, f"{name} tablosu olustur",
                f"Yeni bir {name} bolum tablosu olusturulacak.\n\n"
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
        self.log(f"{name} bolum tablosu olusturuldu")
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
            selected = [r for r in bolgeler if r.start_lba == self.selected_free[0]]
            bolge = selected[0] if selected else max(bolgeler, key=lambda r: r.sector_count)
        else:
            bolge = max(bolgeler, key=lambda r: r.sector_count)

        table = self.session.table
        ic_genisletilmis = False
        genisletilmis_var = False
        birincil_musait = True
        if self.session.scheme == "mbr":
            ext = table.extended_partition()
            genisletilmis_var = ext is not None
            birincil_musait = table.can_add_primary()
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

        def task(progress):
            progress("Bolum olusturuluyor...", 10)
            if v["kind"] == "extended":
                return self.session.table.create_extended(v["start_lba"], v["sector_count"])
            return self.session.create_partition(
                v["start_lba"], v["sector_count"], fs_key=v["fs"],
                label=v["label"], name=v["name"], bootable=v["bootable"],
                logical=(v["kind"] == "logical") if v["kind"] else None,
                progress=progress)

        ok, result = run_task(self, "Bolum olusturuluyor", task)
        if not ok:
            self.error("Bolum olusturulamadi", str(result))
            self.refresh()
            return
        fs_name = FS_BY_KEY[v["fs"]].label if v["fs"] else "bicimlendirilmemis"
        self.log(f"Bolum olusturuldu: LBA {v['start_lba']}, "
                 f"{human_size(v['sector_count'] * 512)}, {fs_name}")
        self.refresh()
        if isinstance(result, Partition):
            self.select_partition(result.index)

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

        def task(progress):
            return self.session.format_partition(
                index, v["fs"], label=v["label"], cluster_bytes=v["cluster"],
                quick=v["quick"], progress=progress)

        ok, result = run_task(self, "Bicimlendiriliyor", task)
        if not ok:
            self.error("Bicimlendirme basarisiz", str(result))
            self.refresh()
            return
        self.log(f"Bolum {index} bicimlendirildi: {result}"
                 + (f" (etiket: {v['label']})" if v["label"] else ""))
        self.refresh()
        self.select_partition(index)

    def resize_partition(self) -> None:
        """Bolumu suruklemeli pencereyle kucultur, buyutur veya tasir."""
        part = self._current_partition()
        if part is None:
            return
        try:
            window = self.session.resize_window(part.index)
            info = self.session.resize_info(part.index)
        except Exception as exc:                       # noqa: BLE001
            self.error("Boyutlandirma hazirlanamadi", str(exc))
            return
        if not info.resizable and not info.movable:
            QMessageBox.information(
                self, "Boyutlandirilamaz",
                f"Bu bolum boyutlandirilamiyor.\n\n{info.note}")
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

        dlg = ResizePartitionDialog(part, window, info,
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

        def task(progress):
            return self.session.resize_partition(
                index, v["start_lba"], v["sector_count"], confirm=True,
                progress=progress)

        ok, result = run_task(self, "Bolum boyutlandiriliyor", task)
        if not ok:
            self.error("Boyutlandirma basarisiz", str(result))
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
        state = "kaldirildi" if part.bootable else "ayarlandi"
        self.log(f"Bolum {part.index} onyukleme bayragi {state}")
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
        name, ok = QInputDialog.getText(self, "Bolum adi", "Yeni ad:", text=part.name)
        if not ok:
            return
        try:
            self.session.set_partition_name(part.index, name.strip())
        except Exception as exc:
            self.error("Ad degistirilemedi", str(exc))
            return
        self.log(f"Bolum {part.index} adi degistirildi: {name}")
        self.refresh()
        self.select_partition(part.index)

    def change_type(self) -> None:
        part = self._current_partition()
        if part is None:
            return
        if self.session.scheme == "mbr":
            secenekler = [f"0x{k:02X} — {v}" for k, v in sorted(MBR_TYPES.items())]
            mevcut = f"0x{part.type_id:02X} — {part.type_name}"
            choice, ok = QInputDialog.getItem(self, "Bolum turu", "Tur:", secenekler,
                                                secenekler.index(mevcut) if mevcut in secenekler else 0,
                                                False)
            if not ok:
                return
            try:
                self.session.set_partition_type(part.index,
                                                type_id=int(choice.split(" ")[0], 16))
            except Exception as exc:
                self.error("Tur degistirilemedi", str(exc))
                return
        else:
            secenekler = [f"{v} — {k}" for k, v in GPT_TYPES.items() if v != "Bos"]
            choice, ok = QInputDialog.getItem(self, "Bolum turu", "Tur:",
                                                secenekler, 0, False)
            if not ok:
                return
            try:
                self.session.set_partition_type(part.index,
                                                type_guid=choice.split(" — ")[1])
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
        etiket, ok = QInputDialog.getText(self, "Birim etiketi", "Yeni etiket:",
                                             text=fs.label)
        if not ok:
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
        name = scheme.upper()
        uygun, reason = self.session.can_convert_to(scheme)
        if not uygun:
            QMessageBox.information(self, f"{name} donusumu yapilamaz", reason)
            return
        cevap = QMessageBox.warning(
            self, f"{name} donusumu",
            f"{reason}\n\nBolum verileri yerinde kalir, yalnizca bolum tablosu "
            f"{name} bicimine yeniden yazilir.\n\n"
            "Islem sirasinda kesinti olursa tablo bozulabilir; onemli veriler icin "
            "once yedek alin.\n\nDevam edilsin mi?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if cevap != QMessageBox.Yes:
            return
        ok, result = run_task(self, f"{name} donusumu",
                             lambda progress: self.session.convert_scheme(scheme, progress))
        if not ok:
            self.error("Donusum basarisiz", str(result))
            self.refresh()
            return
        self.log(f"Bolum tablosu {name} bicimine donusturuldu")
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
            state = "Hizali (1 MiB)" if r["aligned_1m"] else (
                "4K hizali" if r["aligned_4k"] else
                f"HIZASIZ — 4K icinde {r['offset_in_4k']} bayt kayma")
            satirlar[f"Bolum {r['index']} ({r['name']})"] = f"LBA {r['start_lba']} — {state}"
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
            default = f"{self.session.name}-bolum{part.index}.dub"
            target_name = f"Bolum {part.index}"
        else:
            default = f"{self.session.name}.dub"
            target_name = "Tum disk"
        path, _ = QFileDialog.getSaveFileName(
            self, "Yedek dosyasi", os.path.join(os.path.dirname(self.session.path),
                                                default),
            "DiskUltimate yedegi (*.dub)")
        if not path:
            return
        if not path.endswith(".dub"):
            path += ".dub"

        def task(progress):
            if disk:
                return self.session.backup_disk(path, progress=progress)
            return self.session.backup_partition(part.index, path, progress=progress)

        ok, result = run_task(self, f"{target_name} yedekleniyor", task)
        if not ok:
            self.error("Yedekleme basarisiz", str(result))
            return
        self.log(f"{target_name} yedeklendi: {path} "
                 f"({human_size(result.file_size)}, kaynak {human_size(result.total_bytes)})")
        InfoDialog("Yedekleme tamamlandi", result.summary(), self).exec_()

    def restore(self, disk: bool = True) -> None:
        if not self._require_session():
            return
        if not disk:
            part = self._current_partition()
            if part is None:
                return
        path, _ = QFileDialog.getOpenFileName(
            self, "Yedek dosyasi", os.path.dirname(self.session.path),
            "DiskUltimate yedegi (*.dub);;Tum dosyalar (*)")
        if not path:
            return
        try:
            info = self.session.backup_info(path)
        except Exception as exc:
            self.error("Yedek okunamadi", str(exc))
            return
        target_size = self.session.image.size if disk else part.size
        target_name = "tum disk" if disk else f"Bolum {part.index}"
        if info.total_bytes > target_size:
            self.error("Hedef cok kucuk",
                       f"Yedek {human_size(info.total_bytes)}, hedef "
                       f"{human_size(target_size)}")
            return
        cevap = QMessageBox.warning(
            self, "Geri yukleme onayi",
            f"Yedek: {os.path.basename(path)}\n"
            f"Kaynak boyut: {human_size(info.total_bytes)}\n"
            f"Dosya sistemi: {info.fs_type or '-'}\n"
            f"Olusturma: {info.created.strftime('%Y-%m-%d %H:%M') if info.created else '-'}\n\n"
            f"Hedef: {target_name}\n\nHedefteki tum veriler uzerine yazilacak. "
            "Devam edilsin mi?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if cevap != QMessageBox.Yes:
            return

        def task(progress):
            if disk:
                return self.session.restore_disk(path, progress=progress)
            return self.session.restore_partition(part.index, path, progress=progress)

        ok, result = run_task(self, "Geri yukleniyor", task)
        if not ok:
            self.error("Geri yukleme basarisiz", str(result))
            self.refresh()
            return
        self.log(f"{target_name} geri yuklendi: {os.path.basename(path)}")
        self.refresh()

    def clone_disk(self) -> None:
        if self.session is None:
            QMessageBox.information(self, "Goruntu yok", "Once bir goruntu acin.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Klon hedefi",
            os.path.join(os.path.dirname(self.session.path),
                         f"{os.path.splitext(self.session.name)[0]}-klon.img"),
            "Disk goruntusu (*.img)")
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += ".img"

        ok, result = run_task(self, "Disk klonlaniyor",
                             lambda progress: self.session.clone_to(path, progress=progress))
        if not ok:
            self.error("Klonlama basarisiz", str(result))
            return
        self.log(f"Disk klonlandi: {result}")
        cevap = QMessageBox.question(
            self, "Klon hazir", f"Klon olusturuldu:\n{result}\n\nSimdi acilsin mi?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if cevap == QMessageBox.Yes:
            self.open_path(result)

    # ==================================================================
    # Guvenli silme
    # ==================================================================
    def wipe(self, disk: bool = True) -> None:
        if not self._require_session():
            return
        if disk:
            target_name = f"Tum disk ({self.session.name})"
            size = self.session.image.size
            free_region = False
            index = -1
        else:
            part = self._current_partition()
            if part is None:
                return
            target_name = f"Bolum {part.index} ({part.display_name})"
            size = part.size
            index = part.index
            fs = None
            try:
                fs = self.session.filesystem(index)
            except Exception:
                pass
            free_region = bool(fs and getattr(fs, "writable", False))

        dlg = WipeDialog(self, target_name, size, allow_free_space=free_region)
        if exec_dialog(dlg) != WipeDialog.Accepted:
            return
        v = dlg.values()
        if v["scope"] == "free":
            ok, result = run_task(
                self, "Bos alan siliniyor",
                lambda progress: self.session.wipe_free_space(index, progress=progress))
            if not ok:
                self.error("Silme basarisiz", str(result))
                return
            self.log(f"{target_name} bos alani silindi ({human_size(result['bytes'])})")
            self.refresh()
            return

        cevap = QMessageBox.warning(
            self, "Silme onayi",
            f"{target_name}\n{human_size(size)}\n\n"
            f"Yontem: {dlg.method_combo.currentText()}\n\n"
            "TUM VERILER KALICI OLARAK SILINECEK ve kurtarilamayacak.\n\n"
            "Devam edilsin mi?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if cevap != QMessageBox.Yes:
            return

        def task(progress):
            if disk:
                return self.session.wipe_disk(v["method"], progress=progress)
            return self.session.wipe_partition_data(index, v["method"], progress=progress)

        ok, result = run_task(self, "Guvenli silme", task)
        if not ok:
            self.error("Silme basarisiz", str(result))
            self.refresh()
            return
        self.log(f"{target_name} silindi — {result['method']}, "
                 f"{human_size(result['bytes'])}")
        self.refresh()

    # ==================================================================
    # Kurtarma
    # ==================================================================
    def scan_deleted(self) -> None:
        part = self._current_partition()
        if part is None:
            return
        index = part.index
        ok, result = run_task(
            self, "Silinmis dosyalar taraniyor",
            lambda progress: self.session.scan_deleted(index, progress=progress))
        if not ok:
            self.error("Tarama basarisiz", str(result))
            return
        if not result:
            QMessageBox.information(self, "Sonuc yok",
                                    "Bu bolumde silinmis dosya girisi bulunamadi.")
            return
        self.log(f"Bolum {index}: {len(result)} silinmis giris bulundu")
        dlg = DeletedFilesDialog(result, self, f"Bolum {index} — Silinmis Dosyalar")
        if exec_dialog(dlg) != DeletedFilesDialog.Accepted:
            return
        secilenler = dlg.selected()
        if not secilenler:
            return
        klasor = QFileDialog.getExistingDirectory(self, "Kurtarma hedefi")
        if not klasor:
            return

        def task(progress):
            basarili = 0
            for i, oge in enumerate(secilenler, 1):
                progress(f"Kurtariliyor: {oge.name}",
                       int(100 * i / len(secilenler)))
                try:
                    self.session.recover_deleted(index, oge,
                                                 os.path.join(klasor, oge.name))
                    basarili += 1
                except Exception:
                    pass
            return basarili

        ok, count = run_task(self, "Dosyalar kurtariliyor", task)
        if not ok:
            self.error("Kurtarma basarisiz", str(count))
            return
        self.log(f"{count}/{len(secilenler)} dosya kurtarildi -> {klasor}")
        QMessageBox.information(self, "Kurtarma tamamlandi",
                                f"{count} dosya kurtarildi:\n{klasor}")

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
        ok, result = run_task(
            self, "Kayip bolumler taraniyor",
            lambda progress: self.session.scan_lost_partitions(deep=derin, progress=progress))
        if not ok:
            self.error("Tarama basarisiz", str(result))
            return
        if not result:
            QMessageBox.information(
                self, "Sonuc yok",
                "Bolum tablosunda olmayan bir dosya sistemi bulunamadi.")
            return
        self.log(f"Kayip bolum taramasi: {len(result)} aday bulundu")
        dlg = LostPartitionsDialog(result, self)
        if exec_dialog(dlg) != LostPartitionsDialog.Accepted:
            return
        selected = dlg.selected()
        if not selected:
            return
        if not self._require_session():
            return
        try:
            part = self.session.adopt_lost_partition(selected)
        except Exception as exc:
            self.error("Bolum eklenemedi", str(exc))
            return
        self.log(f"Kayip bolum tabloya eklendi: LBA {selected.start_lba} "
                 f"({human_size(selected.size)}, {selected.fs_type})")
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
        ok, result = run_task(
            self, "Imza taramasi",
            lambda progress: self.session.carve_files(index, keys=anahtarlar,
                                                    progress=progress))
        if not ok:
            self.error("Tarama basarisiz", str(result))
            return
        if not result:
            QMessageBox.information(self, "Sonuc yok",
                                    "Secilen turlerde dosya imzasi bulunamadi.")
            return
        self.log(f"Imza taramasi ({kapsam}): {len(result)} dosya bulundu")
        result_dlg = CarvedFilesDialog(result, self)
        if exec_dialog(result_dlg) != CarvedFilesDialog.Accepted:
            return
        secilenler = result_dlg.selected()
        if not secilenler:
            return
        klasor = QFileDialog.getExistingDirectory(self, "Cikarma hedefi")
        if not klasor:
            return

        def task(progress):
            sayac = 0
            for i, oge in enumerate(secilenler, 1):
                progress(f"Cikariliyor: {oge.suggested_name}",
                       int(100 * i / len(secilenler)))
                try:
                    self.session.extract_carved(oge, klasor, index)
                    sayac += 1
                except Exception:
                    pass
            return sayac

        ok, count = run_task(self, "Dosyalar cikariliyor", task)
        if ok:
            self.log(f"{count} dosya cikarildi -> {klasor}")
            QMessageBox.information(self, "Tamamlandi",
                                    f"{count} dosya cikarildi:\n{klasor}")

    # ==================================================================
    # Diger araclar
    # ==================================================================
    def new_vhd(self) -> None:
        default = os.path.dirname(self.session.path) if self.session else ""
        dlg = NewImageDialog(self, default)
        dlg.setWindowTitle("Yeni Sanal Disk (VHD)")
        dlg.path_edit.setText(os.path.join(default or os.path.expanduser("~"),
                                           "yeni-disk.vhd"))
        if exec_dialog(dlg) != NewImageDialog.Accepted:
            return
        v = dlg.values()
        path = v["path"]
        if not path.lower().endswith(".vhd"):
            path = os.path.splitext(path)[0] + ".vhd"

        def task(progress):
            progress("Sanal disk olusturuluyor...", 10)
            disk = VhdImage.create_fixed(path, v["size"], sparse=v["sparse"],
                                         overwrite=True)
            disk.close()
            session = DiskSession.open(path)
            if v["scheme"]:
                session.create_table(v["scheme"])
                if v["auto_partition"]:
                    progress("Bolum olusturuluyor...", 40)
                    free = session.free_regions()
                    if free:
                        en_buyuk = max(free, key=lambda r: r.sector_count)
                        session.create_partition(
                            en_buyuk.start_lba, en_buyuk.sector_count,
                            fs_key=v["fs"], label=v["label"], name=v["label"],
                            progress=lambda m, p: progress(m, 40 + p // 2))
            return session

        ok, result = run_task(self, "Sanal disk olusturuluyor", task)
        if not ok:
            self.error("Sanal disk olusturulamadi", str(result))
            return
        self._add_session(result)
        self.log(f"VHD olusturuldu: {path} ({human_size(v['size'])})")
        self.refresh()

    def show_backup_info(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Yedek dosyasi", "", "DiskUltimate yedegi (*.dub);;Tum dosyalar (*)")
        if not path:
            return
        try:
            info = DiskSession.backup_info(path)
        except Exception as exc:
            self.error("Yedek okunamadi", str(exc))
            return
        InfoDialog("Yedek Dosyasi Bilgisi", info.summary(), self).exec_()

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
    def _add_session(self, session: DiskSession) -> None:
        """Yeni acilan oturumu listeye ekler ve etkin yapar."""
        self.sessions.append(session)
        self.session = session
        self.selected_partition = None
        self.selected_free = None

    @staticmethod
    def _path_key(path: str) -> str:
        """Yol karsilastirma anahtari.

        `normcase` Windows'ta buyuk/kucuk harf ve ayirici farkini giderir:
        `C:\\du\\a.img` ile `c:/du/a.img` ayni dosyadir. Bu normallestirme
        olmadan ayni dosya ikinci kez acilmaya calisilir ve Windows dosyayi
        kilitledigi icin **salt okunur** duser.
        """
        if not path:
            return ""
        try:
            gercek = os.path.realpath(path)
        except OSError:
            gercek = os.path.abspath(path)
        return os.path.normcase(os.path.normpath(gercek))

    def _find_open(self, path: str) -> Optional[DiskSession]:
        """Ayni kaynak zaten acik mi?"""
        target = self._path_key(path)
        if not target:
            return None
        for s in self.sessions:
            if s.path and self._path_key(s.path) == target:
                return s
        return None

    def activate_session(self, session: DiskSession) -> None:
        """Acik oturumlardan birini etkin yapar."""
        if session not in self.sessions or session is self.session:
            if session is self.session:
                return
            return
        self.session = session
        self.selected_partition = None
        self.selected_free = None
        self.refresh()

    # ==================================================================
    # Fiziksel diskler
    # ==================================================================
    def refresh_disks(self) -> None:
        """Disk listesini yeniden tarar."""
        self._build_tree(self.session.partitions if self.session else [])
        count = len(getattr(self, "_physical_cache", {}))
        self.log(f"Fiziksel disk listesi yenilendi: {count} disk")

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
        info = getattr(self, "_physical_cache", {}).get(path or "")
        if info is None:
            QMessageBox.information(self, "Disk secili degil",
                                    "Agactan bir fiziksel disk secin.")
            return
        satirlar = [f"FIZIKSEL DISK — {info.name}", "=" * 52]
        for key, value in info.summary().items():
            satirlar.append(f"{key:<16}: {value}")
        if info.partitions:
            satirlar += ["", "BOLUM AYGITLARI", "-" * 52]
            satirlar += [f"  {b}" for b in info.partitions]
        satirlar += ["", info.risk_text]
        if not DiskSession.has_disk_privileges():
            satirlar += ["", ("UYARI: Uygulama yonetici/root yetkisi olmadan calisiyor; "
                              "disk icerigi okunamayabilir.")]
        self.info_view.setPlainText("\n".join(satirlar))
        self.tabs.setCurrentIndex(TAB_INFO)
        self.status_sel.setText(f"Secili: {info.name} ({human_size(info.size)})")
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
        target = self._session_by_id(veri[1])
        if target is None:
            return
        self.session = target
        self.close_image()

    def _tree_double_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        veri = item.data(0, Qt.UserRole)
        if veri and veri[0] == "phys":
            self.open_physical(write=False, path=veri[1])

    def open_physical(self, write: bool = False, path: Optional[str] = None) -> None:
        """Secili fiziksel diski acar. Yazma modu ayrica onay ister."""
        path = path or self._selected_disk_path()
        info = getattr(self, "_physical_cache", {}).get(path or "")
        if info is None:
            QMessageBox.information(self, "Disk secili degil",
                                    "Agactan bir fiziksel disk secin.")
            return

        allow_system = False
        if write:
            metin = (f"<b>{info.display_name}</b><br>{human_size(info.size)}<br><br>"
                     f"<b>Durum:</b> {info.risk_text}<br><br>"
                     "Disk <b>yazma modunda</b> acilacak. Bu moddaki bolum, "
                     "bicimlendirme ve silme islemleri <b>gercek diske</b> uygulanir "
                     "ve geri alinamaz.<br><br>Devam edilsin mi?")
            if QMessageBox.warning(self, "Yazma modu onayi", metin,
                                   QMessageBox.Yes | QMessageBox.No,
                                   QMessageBox.No) != QMessageBox.Yes:
                return
            if info.is_system:
                name, ok = QInputDialog.getText(
                    self, "Sistem diski onayi",
                    f"{info.path} ISLETIM SISTEMI DISKIDIR.\n\n"
                    "Bu diske yazmak isletim sistemini acilamaz hale getirebilir.\n"
                    "Devam etmek icin disk adini yazin: " + info.name)
                if not ok or name.strip() != info.name:
                    self.log("Sistem diski yazma onayi verilmedi, islem iptal edildi")
                    return
                allow_system = True
            elif info.mounted:
                if QMessageBox.warning(
                        self, "Bagli bolum uyarisi",
                        f"Bu diskte bagli bolumler var:\n{', '.join(info.mounted)}\n\n"
                        "Bagli bir diske yazmak dosya sistemini bozabilir. "
                        "Once bolumleri cikarmaniz (unmount) onerilir.\n\nYine de devam edilsin mi?",
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.No) != QMessageBox.Yes:
                    return

        mevcut = self._find_open(info.path)
        if mevcut is not None:
            self.session = mevcut
            self.session.close()
            self.sessions.remove(mevcut)
        try:
            self._add_session(DiskSession.open_physical(
                info, readonly=not write, confirm=write, allow_system=allow_system))
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
        self.log(f"Fiziksel disk acildi: {info.path} ({kip}) — "
                 f"{human_size(info.size)}, {self.session.scheme_name}")
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
        size = sector_count * (self.session.image.sector_size if self.session else 512)
        self.status_sel.setText(f"Secili: Bos alan — {human_size(size)}")
        self.info_view.setPlainText(
            f"Bolumlenmemis alan\n"
            f"{'-' * 40}\n"
            f"Baslangic LBA   : {start_lba}\n"
            f"Sektor sayisi   : {sector_count}\n"
            f"Boyut           : {human_size(size)}\n\n"
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
        session = self.session
        partitions = session.partitions
        free = session.free_regions()
        self.disk_map.set_disk(
            f"{session.name} — {human_size(session.image.size)} — {session.scheme_name}",
            session.image.sector_count, partitions, free)
        self.part_table.set_partitions(partitions, free)
        self._build_tree(partitions)
        self.setWindowTitle(f"{session.name} — {APP_NAME}")
        self.status_file.setText(session.path)
        state = (f"{session.scheme_name} | {human_size(session.image.size)} | "
                 f"{len(partitions)} bolum")
        if session.readonly:
            state += "  |  🔒 SALT OKUNUR"
            self.status_scheme.setToolTip(session.readonly_reason)
        else:
            self.status_scheme.setToolTip("")
        self.status_scheme.setText(state)

        if self.selected_partition is not None and \
                any(p.index == self.selected_partition for p in partitions):
            self.select_partition(self.selected_partition)
        elif partitions:
            self.select_partition(partitions[0].index)
        elif free:
            self.select_free(free[0].start_lba, free[0].sector_count)
        else:
            self.status_sel.setText("")
            self.browser.set_filesystem(None)
            self.hex_view.set_device(session.image, "Tum goruntu")
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
            self.part_table.set_partitions(self.session.partitions, self.session.free_regions())
            self.part_table.select_partition(index)
            self._show_partition_info(part)
        except Exception:
            pass

    # ==================================================================
    # Agac
    # ==================================================================
    def _build_tree(self, partitions=None, disks=None) -> None:
        """Agaci kurar: once fiziksel diskler, sonra ACIK TUM goruntuler."""
        self.tree.clear()
        self._add_physical_disks(disks)
        for session in self.sessions:
            self._add_session_node(session)

    # ------------------------------------------------------------------
    # Takilan / cikarilan aygitlarin kendiliginden yakalanmasi
    # ------------------------------------------------------------------
    @staticmethod
    def _disk_signature(disks) -> tuple:
        """Disk listesinin karsilastirilabilir ozeti.

        Yalnizca bu ozet degistiginde agac yeniden kurulur; boylece yoklama
        kullanicinin secimini ve acik dallarini bosu bosuna bozmaz. Boyut da
        ozete girer: kart okuyucuda kart degistirildiginde yol ayni kalir ama
        boyut degisir.
        """
        return tuple(sorted((d.path, d.size, d.model, tuple(d.mounted))
                            for d in disks))

    def _poll_disks(self) -> None:
        """Disk listesini yoklar; degistiyse agaci tazeler.

        USB bellek veya SD kart uygulama **acikken** takilabilir. Liste yalnizca
        acilista ve elle yenilemede kuruldugu icin yeni aygit gorunmuyordu.
        Yoklama ucuzdur (tipik olarak ~20 ms) ve yalnizca gercek bir degisiklik
        oldugunda arayuze dokunur.
        """
        try:
            disks = DiskSession.list_physical_disks()
        except Exception:
            return          # gecici hata arayuzu bozmasin; sonraki tur dener
        signature = self._disk_signature(disks)
        if signature == self._last_disk_signature:
            return
        before = {s[0] for s in self._last_disk_signature}
        now = {s[0] for s in signature}
        # Cikarilan aygitin **adi** yalnizca eski listede var; agac yeniden
        # kurulmadan once saklanir, yoksa gunlukte ham aygit yolu gorunur.
        old_names = {d.path: d.name for d in self._physical_cache.values()}
        self._last_disk_signature = signature
        by_path = {d.path: d for d in disks}
        for path in sorted(now - before):
            d = by_path[path]
            self.log(f"Aygit takildi: {d.name} — {d.model or 'bilinmeyen'} "
                     f"({human_size(d.size)})")
        for path in sorted(before - now):
            self.log(f"Aygit cikarildi: {old_names.get(path, path)}")
        self._build_tree(self.session.partitions if self.session else [],
                         disks=disks)
        self._update_actions()

    def _add_session_node(self, session: DiskSession) -> None:
        """Bir acik goruntu/disk icin agac dali olusturur."""
        etkin = session is self.session
        title = (f"{session.name} — {human_size(session.image.size)} — "
                  f"{session.scheme_name}")
        if session.readonly:
            title += "  [salt okunur]"
        root = QTreeWidgetItem(self.tree, [title])
        root.setIcon(0, self._icon(QStyle.SP_DriveHDIcon if session.is_physical
                                  else QStyle.SP_DriveFDIcon))
        root.setData(0, Qt.UserRole, ("session", id(session)))
        root.setToolTip(0, f"{session.path or session.name}\n{session.format_name}")
        f = root.font(0)
        f.setBold(etkin)            # etkin oturum kalin gosterilir
        root.setFont(0, f)

        for p in session.partitions:
            metin = f"Bolum {p.index}: {p.display_name}"
            item = QTreeWidgetItem(root, [metin])
            item.setIcon(0, color_chip(fs_color(p.fs_type), 12))
            item.setData(0, Qt.UserRole, ("part", (id(session), p.index)))
            item.setToolTip(0, f"{p.fs_type or 'Bicimlendirilmemis'} — "
                               f"{human_size(p.size)}")
        for r in session.free_regions():
            item = QTreeWidgetItem(root, [f"Bos alan ({human_size(r.size)})"])
            item.setForeground(0, palette_color(self.tree, "dim"))
            item.setData(0, Qt.UserRole, ("free", (id(session), r.start_lba)))
        root.setExpanded(etkin)

    def _session_by_id(self, session_id: int) -> Optional[DiskSession]:
        for s in self.sessions:
            if id(s) == session_id:
                return s
        return None

    def _add_physical_disks(self, disks=None) -> None:
        """Agacin ustune sistemdeki fiziksel diskleri ekler (veri okumadan).

        `disks` verilirse yeniden taranmaz; yoklama dongusu zaten elde ettigi
        listeyi buraya gecirir ve ikinci bir tarama yapilmaz.
        """
        if disks is None:
            try:
                disks = DiskSession.list_physical_disks()
            except Exception as exc:
                self.log(f"Disk listesi alinamadi: {exc}")
                return
        diskler = disks
        self._physical_cache = {d.path: d for d in diskler}
        root = QTreeWidgetItem(self.tree, [f"Fiziksel Diskler ({len(diskler)})"])
        root.setIcon(0, self._icon(QStyle.SP_ComputerIcon))
        root.setData(0, Qt.UserRole, ("physroot", 0))
        f = root.font(0); f.setBold(True); root.setFont(0, f)

        for d in diskler:
            metin = f"{d.name} — {d.model or 'bilinmeyen'} ({human_size(d.size)})"
            item = QTreeWidgetItem(root, [metin])
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
        root.setExpanded(True)
        if not diskler:
            free = QTreeWidgetItem(root, ["(disk bulunamadi)"])
            free.setDisabled(True)

    def _select_tree(self, key) -> None:
        """Etkin oturumun dalinda verilen ogeyi secer."""
        if not self.session:
            return
        kind, value = key
        target = (kind, (id(self.session), value))
        for i in range(self.tree.topLevelItemCount()):
            root = self.tree.topLevelItem(i)
            for j in range(root.childCount()):
                child = root.child(j)
                if child.data(0, Qt.UserRole) == target:
                    self.tree.blockSignals(True)
                    self.tree.setCurrentItem(child)
                    self.tree.blockSignals(False)
                    return

    def _tree_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        veri = item.data(0, Qt.UserRole)
        if not veri:
            return
        kind, value = veri
        if kind == "phys":
            self.show_disk_info(value)
            return
        if kind == "physroot":
            self.info_view.setPlainText(self._physical_summary_text())
            return
        if kind == "part":
            session_id, index = value
            target = self._session_by_id(session_id)
            if target is None:
                return
            if target is not self.session:
                self.session = target            # baska goruntunun bolumu secildi
                self.selected_partition = index
                self.refresh()
            else:
                self.select_partition(index)
        elif kind == "free":
            session_id, lba = value
            target = self._session_by_id(session_id)
            if target is None:
                return
            if target is not self.session:
                self.session = target
                self.refresh()
            for r in self.session.free_regions():
                if r.start_lba == lba:
                    self.select_free(r.start_lba, r.sector_count)
                    return
        elif kind == "session":
            target = self._session_by_id(value)
            if target is None:
                return
            if target is not self.session:
                self.session = target
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
    def _readonly_hint(self, menu: QMenu) -> None:
        """Salt okunur kaynakta menunun basina tek bir aciklama girisi koyar.

        Yazma eylemleri gorunur ama pasiftir; kullanici nedeni buradan ogrenir.
        Metin tek yerde durur, boylece menuden menuye degismez.
        """
        if not self.session or not self.session.readonly:
            return
        info = menu.addAction("(salt okunur — neden?)", self._readonly_warning)
        f = info.font()
        f.setItalic(True)
        info.setFont(f)
        menu.addSeparator()

    def _partition_menu(self, part: Partition) -> QMenu:
        """Bolum baglam menusu.

        Etiketler ve etkin/pasif durumu **mevcut `QAction` nesnelerinden** gelir;
        burada yeniden yazilmaz. Boylece menu, arac cubugu ve sag tik menusu ayni
        adi gosterir ve `_update_actions()` yetki denetimi tek yerde kalir.
        Secim, widget'lar menuyu istemeden once yapildigi icin eylemlerin hedefi
        sag tiklanan bolumdur.
        """
        menu = QMenu(self)
        open_act = menu.addAction("Dosya gezgininde ac")
        open_act.triggered.connect(lambda: (self.select_partition(part.index),
                                      self.tabs.setCurrentIndex(TAB_FILES)))
        menu.addSeparator()
        self._readonly_hint(menu)
        for act in (self.act_format, self.act_resize_part, self.act_label,
                    self.act_rename_part, self.act_type_part, self.act_boot):
            menu.addAction(act)
        menu.addSeparator()
        for act in (self.act_backup_part, self.act_restore_part,
                    self.act_scan_deleted, self.act_wipe_part):
            menu.addAction(act)
        menu.addSeparator()
        menu.addAction(self.act_delete_part)
        return menu

    def _free_menu(self) -> QMenu:
        menu = QMenu(self)
        self._readonly_hint(menu)
        menu.addAction(self.act_create_part)
        return menu

    def _map_context(self, target, pos) -> None:
        if not self.session:
            return
        if target is None:
            menu = QMenu(self)
            menu.addAction(self.act_refresh)
            menu.exec_(pos)
            return
        kind, obj = target
        (self._partition_menu(obj) if kind == "part" else self._free_menu()).exec_(pos)

    def _table_context(self, target, pos) -> None:
        self._map_context(target, pos)

    def _tree_context(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if not item or not self.session:
            return
        veri = item.data(0, Qt.UserRole)
        if not veri:
            return
        kind, value = veri
        kuresel = self.tree.viewport().mapToGlobal(pos)
        if kind == "part":
            session_id, index = value
            target = self._session_by_id(session_id)
            if target is None:
                return
            if target is not self.session:
                self.session = target
                self.selected_partition = index
                self.refresh()
            else:
                self.select_partition(index)
            self._partition_menu(self.session.table.get(index)).exec_(kuresel)
        elif kind == "free":
            self._free_menu().exec_(kuresel)
        elif kind == "session":
            menu = QMenu(self)
            # `act_close` etkin oturumu kapatir; buradaki giris sag tiklanan
            # oturumu kapatir. Ayri islem oldugu icin ayri etiket tasir.
            menu.addAction("Bu goruntuyu kapat", self._close_tree_session)
            menu.addSeparator()
            menu.addAction(self.act_refresh)
            menu.exec_(kuresel)
        else:
            menu = QMenu(self)
            self._readonly_hint(menu)
            menu.addAction(self.act_mbr)
            menu.addAction(self.act_gpt)
            menu.addSeparator()
            menu.addAction(self.act_resize_img)
            menu.addAction(self.act_refresh)
            menu.exec_(kuresel)

    # ==================================================================
    # Bilgi panelleri
    # ==================================================================
    def _disk_summary_text(self) -> str:
        if not self.session:
            return ""
        satirlar = ["DISK GORUNTUSU", "=" * 52]
        for key, value in self.session.summary().items():
            satirlar.append(f"{key:<16}: {value}")
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
        for key, value in alanlar:
            satirlar.append(f"{key:<18}: {value}")
        self.info_view.setPlainText("\n".join(satirlar))

    # ==================================================================
    # Yardimcilar
    # ==================================================================
    def _selected_partition_quiet(self) -> Optional[Partition]:
        """Secili bolumu **diyalog acmadan** dondurur.

        `_current_partition()` kullaniciyi uyarir; bu surum yalnizca durum
        sorgulamak icindir ve `_update_actions()` gibi sik cagrilan yerlerde
        kullanilir.
        """
        if (self.session is None or not self.session.table
                or self.selected_partition is None):
            return None
        try:
            return self.session.table.get(self.selected_partition)
        except Exception:
            return None

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
            self._readonly_warning()
            return False
        return True

    def _readonly_open_warning(self, path: str) -> None:
        """Dosya salt okunur acildiginda nedeni gosterir ve yeniden denemeyi sunar."""
        reason = self.session.readonly_reason
        self.log(f"DIKKAT: salt okunur acildi — {reason}")
        kilit = "kilitlenmis" in reason or "kullaniliyor" in reason
        metin = (f"<b>{os.path.basename(path)}</b> salt okunur acildi; "
                 "bu dosyada degisiklik yapilamaz.<br><br>"
                 f"<b>Neden:</b> {reason}<br>"
                 f"<b>Yol:</b> {path}<br>"
                 f"<b>Bicim:</b> {self.session.format_name}")
        if kilit:
            metin += ("<br><br>Dosyayi kullanan diger programi (baska bir disk "
                      "araci, yedekleme yazilimi vb.) kapatip <b>Yeniden dene</b>ye "
                      "basin.")
            box = QMessageBox(QMessageBox.Warning, "Salt okunur acildi", metin,
                               QMessageBox.NoButton, self)
            yeniden = box.addButton("Yeniden dene", QMessageBox.AcceptRole)
            box.addButton("Salt okunur devam et", QMessageBox.RejectRole)
            box.exec_()
            if box.clickedButton() is yeniden:
                self.close_image()
                self.open_path(path)
            return
        QMessageBox.warning(self, "Salt okunur acildi", metin)

    def _readonly_warning(self) -> None:
        """Salt okunur nedenini gosterir; mumkunse cozumu de sunar."""
        reason = self.session.readonly_reason
        name = self.session.name
        if self.session.is_physical:
            info = self.session.disk_info
            cevap = QMessageBox.question(
                self, "Disk salt okunur",
                f"<b>{name}</b> salt okunur acik.<br><br>{reason}<br><br>"
                "Simdi <b>yazma modunda</b> acilsin mi?<br>"
                "<i>(Yazma modunda bolum, bicimlendirme ve silme islemleri "
                "gercek diske uygulanir.)</i>",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if cevap == QMessageBox.Yes and info is not None:
                self.open_physical(write=True, path=info.path)
            return
        QMessageBox.information(
            self, "Salt okunur",
            f"<b>{name}</b> salt okunur acik; degisiklik yapilamaz.<br><br>{reason}")

    def _update_actions(self) -> None:
        acik = self.session is not None
        yazilabilir = acik and not self.session.readonly
        has_table = acik and self.session.table is not None
        part_selected = acik and self.selected_partition is not None
        self.act_close.setEnabled(acik)
        self.act_refresh.setEnabled(acik)
        self.act_mbr.setEnabled(yazilabilir)
        self.act_gpt.setEnabled(yazilabilir)
        self.act_clear_table.setEnabled(yazilabilir and has_table)
        self.act_resize_img.setEnabled(yazilabilir)
        self.act_create_part.setEnabled(yazilabilir)
        for act in (self.act_format, self.act_resize_part, self.act_delete_part,
                    self.act_boot, self.act_type_part, self.act_label):
            act.setEnabled(yazilabilir and part_selected)
        self.act_rename_part.setEnabled(
            yazilabilir and part_selected and self.session.scheme == "gpt")
        # Onyukleme bayragi: metin secili bolumun durumunu yansitir. Burada
        # `_current_partition()` kullanilmaz — o islev diyalog acar ve bu islev
        # her yenilemede cagrilir.
        selected = self._selected_partition_quiet()
        self.act_boot.setText(
            BOOT_CLEAR_TEXT if selected is not None and selected.bootable
            else BOOT_SET_TEXT)
        # donusum / bakim
        self.act_to_gpt.setEnabled(yazilabilir and self.session.scheme == "mbr")
        self.act_to_mbr.setEnabled(yazilabilir and self.session.scheme == "gpt")
        self.act_alignment.setEnabled(has_table)
        # yedekleme ve klonlama
        self.act_backup_disk.setEnabled(acik)
        self.act_clone_disk.setEnabled(acik)
        self.act_restore_disk.setEnabled(yazilabilir)
        # Yedek yazma yalnizca acik oturum bir .dub yedegi iken anlamli.
        self.act_write_backup.setEnabled(acik and self.session.is_backup)
        self.act_backup_part.setEnabled(acik and part_selected)
        self.act_restore_part.setEnabled(yazilabilir and part_selected)
        # silme
        self.act_wipe_disk.setEnabled(yazilabilir)
        self.act_wipe_part.setEnabled(yazilabilir and part_selected)
        # fiziksel diskler
        disk_selected = self._selected_disk_path() is not None
        self.act_refresh_disks.setEnabled(True)
        self.act_open_disk_ro.setEnabled(disk_selected)
        self.act_open_disk_rw.setEnabled(disk_selected)
        self.act_disk_info.setEnabled(disk_selected)
        # kurtarma
        self.act_scan_deleted.setEnabled(acik and part_selected)
        self.act_scan_lost.setEnabled(acik)
        self.act_carve.setEnabled(acik)

    def about(self) -> None:
        QMessageBox.about(
            self, f"{APP_NAME} hakkinda",
            f"<h3>{APP_NAME} {APP_VERSION}</h3>"
            "<p>Disk goruntusu, sanal disk ve <b>sistemdeki gercek diskler</b> "
            "uzerinde bolumleme, bicimlendirme, yedekleme ve kurtarma araci.</p>"
            "<p><b>Teknoloji:</b> Python 3 + PyQt5, harici bagimlilik yok<br>"
            "<b>Bolum tablolari:</b> MBR (mantiksal bolumler dahil), GPT, "
            "MBR&nbsp;&harr;&nbsp;GPT donusumu<br>"
            "<b>Bicimlendirme:</b> FAT12/16/32, exFAT, ext2/3/4 ve NTFS — "
            "sekizi de saf Python, uc platformda<br>"
            "<b>Dosya erisimi:</b> FAT ve exFAT tam okuma/yazma</p>"
            "<p>Goruntu dosyalari yonetici yetkisi gerektirmez. Fiziksel disk "
            "erisimi yonetici/root ister ve <b>varsayilan olarak salt "
            "okunurdur</b>; yazma ayrica onay ister.</p>")

    def closeEvent(self, event) -> None:
        # Yoklama zamanlayicisi once durur: kapanis sirasinda tetiklenirse
        # yikilmakta olan agaca dokunmaya calisirdi.
        timer = getattr(self, "_disk_timer", None)
        if timer is not None:
            timer.stop()
        self.close_all()
        super().closeEvent(event)
