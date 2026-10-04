"""DiskUltimate ana penceresi."""
from __future__ import annotations

import dataclasses
import datetime
import html
import os
from typing import Dict, List, Optional, Tuple

from PyQt5.QtCore import QSize, Qt, QTimer
from PyQt5.QtGui import QColor, QFontDatabase, QIcon, QKeySequence
from PyQt5.QtWidgets import (QAction, QActionGroup, QApplication, QDialog,
                             QDialogButtonBox, QFileDialog, QFormLayout,
                             QHBoxLayout, QHeaderView,
                             QInputDialog, QLabel, QMainWindow, QMenu,
                             QMessageBox, QPlainTextEdit, QProgressDialog,
                             QPushButton, QSplitter,
                             QScrollArea, QStackedWidget, QTabWidget,
                             QTextBrowser, QToolBar,
                             QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core.fsregistry import fs_display
from ..core import diagnostics
from ..core import settings
from ..core import platform
from ..core import operations as ops
from ..core.formatter import FS_BY_KEY
from ..core.physical import (AccessDeniedError, PhysicalDiskError,
                             SystemDiskError, locks_volumes_on_write,
                             mount_partition as mount_physical_partition,
                             unmount_partition as unmount_physical_partition)
from ..core.platform import (ELEVATION_NAME, IMAGE_EXTENSIONS,  # noqa: E501
                             elevation_name, mount_action_labels,
                             mount_point_label, mount_supported,
                             PLATFORM_NAME, elevation_available, is_elevated,
                             open_folder, relaunch_elevated)
from ..core.platform import summary as platform_summary
from ..core.vdisk import VhdImage
from ..paths import log_root
from ..core.clone import is_backup_file
from ..core.image import DiskImage
from ..core import disksource, queueedit
from ..core.resize import ResizeWindow
from ..core.ptable import (GPT_TYPES, MBR_EXTENDED_TYPES, MBR_TYPES,
                           FreeRegion, Partition, human_size, parse_size)
from ..core.session import DiskSession, SessionError
from .dialogs.base import exec_dialog
from .diag import install as install_diagnostics
from . import icons as icons_mod
from . import iconpacks, iconsets
from .fslimits import FsLimitsService
from .icons import icon as app_icon
from .disk_scan import DiskScanner, disk_signature
from .dialogs.new_image import NewImageDialog
from .dialogs.partition import CreatePartitionDialog, FormatDialog
from .dialogs.partition_layout import PartitionLayoutDialog
from .dialogs.resize import ResizePartitionDialog
from ..core import planview
from .dialogs.apply import run_apply
from .dialogs.backup import BackupDialog, MODE_BACKUP, MODE_RESTORE
from .dialogs.task import run_task
from .dialogs.tools import (CarveOptionsDialog,
                            CarvedFilesDialog, DeletedFilesDialog, InfoDialog,
                            LostPartitionsDialog, TextViewDialog, WipeDialog)
from .theme import (OS_LOGOS, THEMES, apply_theme, current_theme, fs_color,
                    os_icon, palette_color, theme_label)
from .osinfo import OsInfoService
from . import updatecheck
from ..core.updates import PROJECT_URL
from .widgets.disk_map import DiskMapWidget
from .widgets.disk_overview import DiskOverviewWidget
from .widgets.file_browser import FileBrowser
from .widgets.hex_view import HexViewer
from .widgets.partition_table import PartitionTableWidget, color_chip
from .. import i18n
from ..i18n import mark, tr, trn

APP_NAME = "DiskUltimate"
APP_AUTHOR = "Mikail Cansız"
# Surumun tek kaynagi burasidir. Degistirildiginde README.md'deki surum rozeti
# ve .claude/docs/project-overview.md "Durum" satiri da guncellenir.
APP_VERSION = "0.6.2-beta"

# Sekme sirasi tek yerden tanimlanir; `tabs.setCurrentIndex` cagrilari ciplak
# sayi kullanmaz, boylece sekme sirasi degisince sessizce yanlis sekme acilmaz.
TAB_FILES = 0
TAB_INFO = 1
TAB_HEX = 2
TAB_LOG = 3

# Onyukleme bayragi eylemi tek eylemdir, metni secime gore degisir.
# Kaynak metin olarak durur; gosterilirken tr() ile cevrilir (mark, ADR 0027).
BOOT_SET_TEXT = mark("Onyukleme bayragini koy")
BOOT_CLEAR_TEXT = mark("Onyukleme bayragini kaldir")

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
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.resize(1280, 800)
        self._physical_cache: Dict[str, object] = {}
        # Her oturumun kendi bekleyen islem kuyrugu vardir: adimlar o oturumun
        # bolum numaralarina baglidir, baska bir goruntuye tasinamazlar.
        self._queues: Dict[int, ops.OperationQueue] = {}
        # Harita ve tablo, kuyruk doluyken **planlanan** yerlesimi gosterir.
        # `_plan_layout` o yerlesimi tasir; `_show_plan` kullanicinin
        # "diskteki hali" secimiyle kapanir.
        self._plan_layout = None
        self._show_plan = True
        # Dosya sistemi sinirlari: arka planda bir kez hesaplanir, onbellekte
        # durur (ADR 0047). Harita, boyutlandirma ve "Bolum duzeni" ortak.
        self.limits = FsLimitsService(self)
        self.limits.ready.connect(lambda _key: self._on_limits_ready())
        # Bolum basina kurulu isletim sistemi, arka planda (ADR 0089)
        self.osinfo = OsInfoService(self)
        self.osinfo.ready.connect(self._on_os_ready)
        # Ana ekranin duzenlenebilir yerlesimi (ortak model, ADR 0049)
        self._edit_layout = None
        # Acilistaki otomatik disk secimi bir kez yapilir (ADR 0035).
        self._auto_opened = False
        self.selected_is_planned = False
        self._build_ui()
        self._build_actions()
        self._build_tree([])        # acik goruntu olmadan da diskler listelenir
        self.info_view.setPlainText(self._physical_summary_text())
        self._update_actions()

        # Tanilama: donma yakalayici ve oturum gunlugu. Arayuz kurulduktan
        # hemen sonra baslar, boylece acilistaki takilmalar da kayda girer.
        # Dil degisince butun metinler yerinde yenilenir (ADR 0027).
        i18n.add_listener(lambda _code: self.retranslate())

        self.diagnostics = install_diagnostics(self)
        self.diagnostics.freezeDetected.connect(self._on_freeze)
        self.log(tr("{} {} baslatildi", APP_NAME, APP_VERSION))
        if diagnostics.session_log():
            self.log(tr("Tanilama gunlugu: {}", diagnostics.session_log()))

        # Disk listesi **arka planda** toplanir; bkz. ui/disk_scan.py.
        # Takilan/cikarilan aygitlar da bu yoklama ile yakalanir.
        self._last_disk_signature: tuple = ()
        self._disk_scan_done = False
        self._elevation_asked = False
        # {aygit yolu: DiskSurvey} — agacta bolumleri gostermek icin
        self._surveys: Dict[str, object] = {}
        self._scanner: Optional[DiskScanner] = None
        self._disk_timer = QTimer(self)
        self._disk_timer.setInterval(DISK_POLL_MS)
        self._disk_timer.timeout.connect(lambda: self.start_disk_scan(quiet=True))
        self._disk_timer.start()
        self._refresh_pending()     # "Bekleyen islem yok" basligi bastan dogru
        self.start_disk_scan(quiet=True)

    # ==================================================================
    # Arayuz kurulumu
    # ==================================================================
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
        self.tree.setIconSize(QSize(20, 20))
        self.tree.setHeaderLabel(tr("Disk ve Bolumler"))
        self.tree.itemClicked.connect(self._tree_clicked)
        self.tree.itemDoubleClicked.connect(self._tree_double_clicked)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_context)

        # Bekleyen islem listesi agacin altinda durur (Acronis/EaseUS duzeni):
        # kullanici ne yapacagini gormeden Uygula demesin.
        self.pending_view = QTreeWidget()
        self.pending_view.setIconSize(QSize(20, 20))
        self.pending_view.setHeaderLabels([tr("Bekleyen islemler"), tr("Hedef")])
        self.pending_view.setRootIsDecorated(False)
        self.pending_view.setAlternatingRowColors(True)
        self.pending_view.setMinimumHeight(90)
        self.pending_view.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.pending_view.setColumnWidth(1, 90)
        self.pending_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.pending_view.customContextMenuRequested.connect(self._pending_context)

        sol_splitter = QSplitter(Qt.Vertical)
        sol_splitter.addWidget(self.tree)
        sol_splitter.addWidget(self.pending_view)
        sol_splitter.setSizes([520, 190])
        sol_splitter.setMinimumWidth(260)
        sol_splitter.setMaximumWidth(440)
        ana_splitter.addWidget(sol_splitter)

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
        # Secili bolumun kenarlarinda surukleme tutamaklari (DiskGenius);
        # birakinca boyutlandirma adimi kuyruga girer, diske yazilmaz.
        self.disk_map.layoutCommitted.connect(
            lambda indices: self._commit_edit(
                self._edit_layout, indices,
                before=self.disk_map.edit.last_before))

        # Oturum yokken butun diskler alt alta gosterilir (Acronis duzeni);
        # bir kaynak acilinca onun haritasina gecilir.
        self.disk_overview = DiskOverviewWidget()
        self.disk_overview.partitionSelected.connect(self._open_physical_partition)
        self.disk_overview.partitionActivated.connect(self._open_physical_partition)
        self.disk_overview.diskSelected.connect(self.show_disk_info)
        self.overview_scroll = QScrollArea()
        self.overview_scroll.setWidget(self.disk_overview)
        self.overview_scroll.setWidgetResizable(True)
        self.overview_scroll.setFrameShape(QScrollArea.NoFrame)
        self.overview_scroll.setMaximumHeight(300)

        # Plan seridi: kuyruk doluyken haritanin **planlanan** yerlesimi
        # gosterdigini soyler ve diskteki hale gecmeyi sunar (ADR 0031).
        self.plan_bar = QWidget()
        plan_duzen = QHBoxLayout(self.plan_bar)
        plan_duzen.setContentsMargins(8, 2, 8, 2)
        plan_duzen.setSpacing(8)
        self.plan_label = QLabel("")
        self.plan_label.setWordWrap(True)
        plan_duzen.addWidget(self.plan_label, 1)
        self.plan_toggle = QPushButton("")
        self.plan_toggle.setFlat(True)
        self.plan_toggle.clicked.connect(self.toggle_plan_preview)
        plan_duzen.addWidget(self.plan_toggle)
        self.plan_bar.setVisible(False)
        sag_duzen.addWidget(self.plan_bar)

        self.map_stack = QStackedWidget()
        self.map_stack.addWidget(self.overview_scroll)      # 0: genel bakis
        self.map_stack.addWidget(self.disk_map)             # 1: tek disk
        self.map_stack.setMaximumHeight(300)
        sag_duzen.addWidget(self.map_stack)

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
        self.browser.ensure_writable = self._browser_write_access
        self.tabs.addTab(self.browser, tr("Dosya Gezgini"))

        self.info_view = QPlainTextEdit()
        self.info_view.setReadOnly(True)
        mono = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        mono.setPointSize(9)
        self.info_view.setFont(mono)
        self.tabs.addTab(self.info_view, tr("Bolum Bilgisi"))

        self.hex_view = HexViewer()
        self.tabs.addTab(self.hex_view, tr("Onaltilik Goruntuleyici"))

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(mono)
        self.tabs.addTab(self.log_view, tr("Islem Gunlugu"))
        bottom_splitter.addWidget(self.tabs)
        bottom_splitter.setSizes([180, 420])
        sag_duzen.addWidget(bottom_splitter, 1)
        ana_splitter.addWidget(sag)
        ana_splitter.setSizes([320, 960])

        # --- durum cubugu ---
        self.status_file = QLabel(tr("Disk goruntusu acik degil"))
        self.status_scheme = QLabel("")
        self.status_sel = QLabel("")
        # Yetki durumu surekli gorunur: fiziksel disk islemlerinin cogu buna
        # bagli ve kullanici neden yapamadigini burada gorur.
        self.status_rights = QLabel()
        self._refresh_rights_label()
        for lbl in (self.status_scheme, self.status_sel):
            lbl.setEnabled(False)      # paletten soluk renk; sabit renk yazilmaz
        if not is_elevated():
            self.status_rights.setEnabled(False)
        self.statusBar().addWidget(self.status_file, 1)
        self.statusBar().addPermanentWidget(self.status_rights)
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
        # --- Dosya ---
        self.act_new = QAction(app_icon("image"), "", self)
        self.act_new.setShortcut(QKeySequence.New)
        self.act_new.triggered.connect(self.new_image)
        self.act_open = QAction(app_icon("open"), "", self)
        self.act_open.setShortcut(QKeySequence.Open)
        self.act_open.triggered.connect(self.open_image)
        self.act_close = QAction("", self)
        self.act_close.triggered.connect(self.close_image)
        self.act_quit = QAction("", self)
        self.act_quit.setShortcut(QKeySequence.Quit)
        self.act_quit.triggered.connect(self.close)

        # --- Disk ---
        self.act_mbr = QAction(app_icon("table"), "", self)
        self.act_mbr.triggered.connect(lambda: self.create_table("mbr"))
        self.act_gpt = QAction(app_icon("table"), "", self)
        self.act_gpt.triggered.connect(lambda: self.create_table("gpt"))
        self.act_clear_table = QAction(app_icon("table-clear"), "", self)
        self.act_clear_table.triggered.connect(self.clear_table)
        self.act_resize_img = QAction(app_icon("image-resize"), "", self)
        self.act_resize_img.triggered.connect(self.resize_image)
        self.act_refresh = QAction(app_icon("refresh"), "", self)
        self.act_refresh.setShortcut(QKeySequence.Refresh)
        self.act_refresh.triggered.connect(self.refresh)

        # --- Bolum ---
        self.act_create_part = QAction(app_icon("partition-new"), "", self)
        self.act_create_part.triggered.connect(self.create_partition)
        self.act_format = QAction(app_icon("format"), "", self)
        self.act_format.triggered.connect(self.format_partition)
        self.act_resize_part = QAction(app_icon("resize"), "", self)
        self.act_resize_part.triggered.connect(self.resize_partition)
        self.act_edit_layout = QAction(app_icon("partition"), "", self)
        self.act_edit_layout.triggered.connect(self.edit_partition_layout)
        self.act_delete_part = QAction(app_icon("partition-delete"), "", self)
        self.act_delete_part.triggered.connect(self.delete_partition)
        # Metin secili bolume gore `_update_actions()` icinde guncellenir;
        # asagidaki ikisi birbirinin tam karsiligidir.
        self.act_boot = QAction(app_icon("boot"), "", self)
        self.act_boot.triggered.connect(self.toggle_bootable)
        self.act_rename_part = QAction(app_icon("rename"), "", self)
        self.act_rename_part.triggered.connect(self.rename_partition)
        self.act_type_part = QAction(app_icon("type"), "", self)
        self.act_type_part.triggered.connect(self.change_type)
        self.act_label = QAction(app_icon("label"), "", self)
        self.act_label.triggered.connect(self.change_label)
        # Baglama/cikarma yikici DEGILDIR: kuyruga girmez, dogrudan calisir
        # (ADR 0043). Metinleri platformun kavramindan gelir.
        self.act_mount = QAction(app_icon("mount"), "", self)
        self.act_mount.triggered.connect(self.mount_selected_partition)
        self.act_unmount = QAction(app_icon("unmount"), "", self)
        self.act_unmount.triggered.connect(self.unmount_selected_partition)

        # --- Donusum ve bakim ---
        self.act_to_gpt = QAction(app_icon("convert"), "", self)
        self.act_to_gpt.triggered.connect(lambda: self.convert_scheme("gpt"))
        self.act_to_mbr = QAction(app_icon("convert"), "", self)
        self.act_to_mbr.triggered.connect(lambda: self.convert_scheme("mbr"))
        self.act_alignment = QAction("", self)
        self.act_alignment.triggered.connect(self.show_alignment)

        # --- Yedekleme / klonlama ---
        self.act_backup_disk = QAction(app_icon("backup"), "", self)
        self.act_backup_disk.triggered.connect(
            lambda: self.open_backup_dialog(MODE_BACKUP))
        self.act_restore_disk = QAction(app_icon("backup"), "", self)
        self.act_restore_disk.triggered.connect(
            lambda: self.open_backup_dialog(MODE_RESTORE))
        self.act_clone_disk = QAction(app_icon("clone"), "", self)
        self.act_clone_disk.triggered.connect(self.clone_disk)
        self.act_backup_part = QAction(app_icon("save"), "", self)
        self.act_backup_part.triggered.connect(
            lambda: self.open_backup_dialog(MODE_BACKUP, partition=True))
        self.act_restore_part = QAction(app_icon("open"), "", self)
        self.act_restore_part.triggered.connect(
            lambda: self.open_backup_dialog(MODE_RESTORE, partition=True))

        # --- Guvenli silme ---
        self.act_wipe_disk = QAction(app_icon("wipe"), "", self)
        self.act_wipe_disk.triggered.connect(lambda: self.wipe(disk=True))
        self.act_wipe_part = QAction(app_icon("wipe"), "", self)
        self.act_wipe_part.triggered.connect(lambda: self.wipe(disk=False))

        # --- NTFS denetle ve onar (ntfsfix karsiligi, ADR 0077) ---
        self.act_ntfs_fix = QAction(app_icon("fs-repair"), "", self)
        self.act_ntfs_fix.triggered.connect(lambda: self.ntfs_fix())

        # --- Kurtarma ---
        self.act_scan_deleted = QAction(app_icon("recover"), "", self)
        self.act_scan_deleted.triggered.connect(self.scan_deleted)
        self.act_scan_lost = QAction(app_icon("search"), "", self)
        self.act_scan_lost.triggered.connect(self.scan_lost)
        self.act_carve = QAction(app_icon("recover"), "", self)
        self.act_carve.triggered.connect(self.carve_files)

        # --- Fiziksel diskler ---
        self.act_refresh_disks = QAction(app_icon("refresh"), "", self)
        self.act_refresh_disks.triggered.connect(self.refresh_disks)
        self.act_elevate = QAction(app_icon("shield"), "", self)
        self.act_elevate.triggered.connect(self.restart_elevated)
        self.act_open_disk = QAction(app_icon("disk"), "", self)
        self.act_open_disk.triggered.connect(lambda: self.open_physical())
        self.act_disk_info = QAction(app_icon("info"), "", self)
        self.act_disk_info.triggered.connect(lambda: self.show_disk_info())

        # --- Bekleyen islem kuyrugu ---
        # Acronis'teki damali bayrak dugmesinin karsiligi: adimlar birikir,
        # tek tiklamayla sirayla uygulanir (ADR 0025).
        self.act_apply = QAction(app_icon("apply"), "", self)
        self.act_apply.setShortcut("Ctrl+Return")
        self.act_apply.triggered.connect(self.apply_steps)
        self.act_undo_step = QAction(app_icon("undo"), "", self)
        self.act_undo_step.setShortcut(QKeySequence.Undo)
        self.act_undo_step.triggered.connect(self.undo_step)
        self.act_discard = QAction(app_icon("discard"), "", self)
        self.act_discard.triggered.connect(self.discard_steps)

        # --- Diger araclar ---
        self.act_new_vhd = QAction(app_icon("image"), "", self)
        self.act_new_vhd.triggered.connect(self.new_vhd)
        self.act_write_backup = QAction(app_icon("backup"), "", self)
        self.act_write_backup.triggered.connect(self.write_backup_to_target)
        self.act_backup_info = QAction(app_icon("backup"), "", self)
        self.act_backup_info.triggered.connect(self.show_backup_info)
        self.act_sysinfo = QAction(app_icon("info"), "", self)
        self.act_sysinfo.triggered.connect(self.show_system_info)

        # --- Onyukleme ---
        self.act_bootloader = QAction(app_icon("bootloader"), "", self)
        self.act_bootloader.triggered.connect(self.show_bootloader)
        self.act_efi_boot = QAction(app_icon("efi"), "", self)
        self.act_efi_boot.triggered.connect(self.show_efi_boot)

        # --- Tanilama ---
        self.act_diag_status = QAction("", self)
        self.act_diag_status.triggered.connect(self.show_diagnostics)
        self.act_diag_folder = QAction("", self)
        self.act_diag_folder.triggered.connect(self.open_log_folder)
        self.act_diag_report = QAction("", self)
        self.act_diag_report.triggered.connect(self.show_freeze_report)
        self.act_diag_dump = QAction("", self)
        self.act_diag_dump.triggered.connect(self.dump_stacks)

        # --- Yardim ---
        self.act_about = QAction("", self)
        self.act_about.triggered.connect(self.about)
        self.act_licenses = QAction("", self)
        self.act_licenses.triggered.connect(self.show_licenses)
        # Guncelleme denetimi (ADR 0090)
        self.act_check_updates = QAction("", self)
        self.act_check_updates.triggered.connect(self.check_updates)
        self.act_auto_updates = QAction("", self)
        self.act_auto_updates.setCheckable(True)
        self.act_auto_updates.setChecked(bool(settings.get(updatecheck.AUTO_KEY, True)))
        self.act_auto_updates.toggled.connect(
            lambda on: settings.set_value(updatecheck.AUTO_KEY, bool(on)))

        menu = self.menuBar()
        m_file = menu.addMenu(tr("&Dosya"))
        m_file.addAction(self.act_new)
        m_file.addAction(self.act_new_vhd)
        m_file.addAction(self.act_open)
        m_file.addAction(self.act_close)
        m_file.addSeparator()
        m_file.addAction(self.act_quit)

        m_disk = menu.addMenu(tr("D&isk"))
        m_disk.addAction(self.act_apply)
        m_disk.addAction(self.act_undo_step)
        m_disk.addAction(self.act_discard)
        m_disk.addSeparator()
        m_disk.addAction(self.act_new)
        m_disk.addAction(self.act_new_vhd)
        m_disk.addAction(self.act_open)
        m_disk.addAction(self.act_close)
        m_disk.addSeparator()
        m_disk.addAction(self.act_refresh_disks)
        m_disk.addAction(self.act_elevate)
        m_disk.addAction(self.act_open_disk)
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

        m_part = menu.addMenu(tr("&Bolum"))
        m_part.addAction(self.act_create_part)
        m_part.addAction(self.act_format)
        m_part.addAction(self.act_resize_part)
        m_part.addAction(self.act_edit_layout)
        m_part.addAction(self.act_delete_part)
        m_part.addSeparator()
        m_part.addAction(self.act_label)
        m_part.addSeparator()
        m_part.addAction(self.act_mount)
        m_part.addAction(self.act_unmount)
        m_part.addAction(self.act_rename_part)
        m_part.addAction(self.act_type_part)
        m_part.addAction(self.act_boot)
        m_part.addSeparator()
        m_part.addAction(self.act_ntfs_fix)
        m_part.addSeparator()
        m_part.addAction(self.act_backup_part)
        m_part.addAction(self.act_restore_part)
        m_part.addAction(self.act_wipe_part)

        m_boot = menu.addMenu(tr("&Onyukleme"))
        m_boot.addAction(self.act_bootloader)
        m_boot.addAction(self.act_efi_boot)

        m_arac = menu.addMenu(tr("&Araclar"))
        m_arac.addAction(self.act_scan_deleted)
        m_arac.addAction(self.act_scan_lost)
        m_arac.addAction(self.act_carve)
        m_arac.addSeparator()
        m_arac.addAction(self.act_backup_info)
        m_arac.addAction(self.act_sysinfo)

        # Dil secimi: ceviri dosyasi bulunan her dil kendiliginden listelenir.
        self.m_lang = m_arac.addMenu(tr("Dil"))
        self._build_language_menu()
        # Ikon seti: sekiz set, secim saklanir ve aninda uygulanir (ADR 0046)
        self.m_icons = m_arac.addMenu(tr("Ikon seti"))
        self._build_icon_menu()
        # Tema: Sistem / Acik / Koyu, secim saklanir ve aninda uygulanir (ADR 0088)
        self.m_theme = m_arac.addMenu(tr("Tema"))
        self._build_theme_menu()

        m_tani = m_arac.addMenu(tr("Tanilama"))
        m_tani.addAction(self.act_diag_status)
        m_tani.addAction(self.act_diag_report)
        m_tani.addAction(self.act_diag_dump)
        m_tani.addSeparator()
        m_tani.addAction(self.act_diag_folder)

        m_yardim = menu.addMenu(tr("&Yardim"))
        m_yardim.addAction(self.act_diag_status)
        m_yardim.addSeparator()
        m_yardim.addAction(self.act_check_updates)
        m_yardim.addAction(self.act_auto_updates)
        m_yardim.addSeparator()
        m_yardim.addAction(self.act_licenses)
        m_yardim.addAction(self.act_about)

        # Arac cubugu yalnizca SECILI disk/bolum uzerinde yapilabilecek islemleri
        # tasir; goruntu acma/olusturma Dosya ve Disk menulerindedir.
        tb = QToolBar(tr("Islemler"))
        tb.setIconSize(QSize(32, 32))
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        tb.addAction(self.act_apply)
        tb.addAction(self.act_undo_step)
        tb.addAction(self.act_discard)
        tb.addSeparator()
        # Arac cubugu yalnizca en sik kullanilan bolum islemlerini tasir;
        # kalanlar menulerde ve sag tik menusunde durur. Cok dugme koymak
        # cubugu tasirip ">>" tasma okuna dusuruyordu.
        tb.addAction(self.act_create_part)
        tb.addAction(self.act_format)
        tb.addAction(self.act_resize_part)
        tb.addAction(self.act_delete_part)
        tb.addSeparator()
        tb.addAction(self.act_disk_info)
        tb.addAction(self.act_refresh)
        self.addToolBar(tb)
        self.toolbar = tb
        # Dil degisince yeniden adlandirilacak menuler
        self._menus = []
        self._menus.append((m_file, '&Dosya'))
        self._menus.append((m_disk, 'D&isk'))
        self._menus.append((m_part, '&Bolum'))
        self._menus.append((m_boot, '&Onyukleme'))
        self._menus.append((m_arac, '&Araclar'))
        self._menus.append((m_tani, 'Tanilama'))
        self._menus.append((self.m_lang, 'Dil'))
        self._menus.append((self.m_icons, 'Ikon seti'))
        self._menus.append((self.m_theme, 'Tema'))
        self._menus.append((m_yardim, '&Yardim'))
        self._retranslate_actions()

    def _retranslate_actions(self) -> None:
        """Eylem ve ipucu metinlerini etkin dilde yazar.

        Metinlerin **tek kaynagi** burasidir: `_build_actions()` eylemi
        bos metinle kurar, bu islev doldurur. Dil degistiginde yeniden
        cagrilir. Yeni bir eylem eklendiginde buraya da bir satir
        yazilmalidir; `tests/i18n_check.py` bunu denetler.
        """
        self.act_new.setText(tr("Yeni goruntu..."))
        self.act_open.setText(tr("Goruntu ac..."))
        self.act_close.setText(tr("Goruntuyu kapat"))
        self.act_quit.setText(tr("Cikis"))
        self.act_mbr.setText(tr("MBR bolum tablosu olustur"))
        self.act_gpt.setText(tr("GPT bolum tablosu olustur"))
        self.act_clear_table.setText(tr("Bolum tablosunu sil"))
        self.act_resize_img.setText(tr("Goruntu boyutunu degistir..."))
        self.act_refresh.setText(tr("Yenile"))
        self.act_create_part.setText(tr("Yeni bolum..."))
        self.act_format.setText(tr("Bicimlendir..."))
        self.act_resize_part.setText(tr("Bolumu boyutlandir..."))
        self.act_resize_part.setToolTip(
            tr("Bolumu fareyle surukleyerek kucult, buyut veya tasi"))
        self.act_edit_layout.setText(tr("Bolum duzenini degistir..."))
        self.act_edit_layout.setToolTip(
            tr("Butun bolumleri tek pencerede birlikte buyut, kucult ya da "
               "tasi"))
        self.act_delete_part.setText(tr("Bolumu sil"))
        self.act_boot.setText(tr(BOOT_SET_TEXT))
        self.act_rename_part.setText(tr("Bolum adini degistir..."))
        self.act_type_part.setText(tr("Bolum turunu degistir..."))
        self.act_label.setText(tr("Birim etiketini degistir..."))
        # Windows'ta "bagla" kavrami yoktur; orada degisen sey surucu harfidir.
        mount_text, unmount_text = mount_action_labels()
        self.act_mount.setText(mount_text)
        self.act_unmount.setText(unmount_text)
        self.act_to_gpt.setText(tr("Bolum tablosunu GPT'ye donustur"))
        self.act_to_mbr.setText(tr("Bolum tablosunu MBR'ye donustur"))
        self.act_alignment.setText(tr("Hizalama denetimi (4K)"))
        self.act_backup_disk.setText(tr("Diski yedekle..."))
        self.act_restore_disk.setText(tr("Diski geri yukle..."))
        self.act_clone_disk.setText(tr("Diski klonla..."))
        self.act_backup_part.setText(tr("Bolumu yedekle..."))
        self.act_restore_part.setText(tr("Bolume geri yukle..."))
        self.act_wipe_disk.setText(tr("Diski guvenli sil..."))
        self.act_wipe_part.setText(tr("Bolumu guvenli sil..."))
        self.act_ntfs_fix.setText(tr("NTFS'i denetle ve onar..."))
        self.act_ntfs_fix.setToolTip(tr(
            "Windows'un temiz kapatmadigi NTFS birimini baglanabilir hale "
            "getirir (ntfsfix gibi)"))
        self.act_bootloader.setText(tr("Onyukleyici yoneticisi..."))
        self.act_bootloader.setToolTip(
            tr("Diskteki isletim sistemlerini ve onyukleme kodunu gosterir; "
               "GRUB kurulumunu yonetir."))
        self.act_efi_boot.setText(tr("UEFI onyukleme duzenleyici..."))
        self.act_efi_boot.setToolTip(
            tr("Bellenimdeki onyukleme girislerini ve sirasini duzenler."))
        self.act_scan_deleted.setText(tr("Silinmis dosyalari tara..."))
        self.act_scan_lost.setText(tr("Kayip bolumleri tara..."))
        self.act_carve.setText(tr("Imza tabanli dosya kurtarma..."))
        self.act_refresh_disks.setText(tr("Fiziksel diskleri yenile"))
        self.act_elevate.setText(tr("{} olarak yeniden baslat...", elevation_name()))
        self.act_elevate.setToolTip(
            tr("Fiziksel disklere erisim icin uygulamayi yetkili olarak yeniden "
            "baslatir. Goruntu dosyalari icin gerekmez."))
        self.act_open_disk.setText(tr("Secili diski ac"))
        self.act_open_disk.setToolTip(
            tr("Disk salt okunur acilir. Degisiklikler bekleyen islem olarak "
            "birikir ve ancak Uygula ile diske yazilir."))
        self.act_disk_info.setText(tr("Disk bilgisi"))
        self.act_apply.setText(tr("Uygula"))
        self.act_apply.setToolTip(
            tr("Bekleyen islemleri sirayla uygular. Bu ana kadar diske hicbir sey "
            "yazilmadi."))
        self.act_undo_step.setText(tr("Geri al"))
        self.act_undo_step.setToolTip(tr("Son eklenen bekleyen adimi kaldirir"))
        self.act_discard.setText(tr("Vazgec"))
        self.act_discard.setToolTip(tr("Butun bekleyen adimlari iptal eder"))
        self.act_new_vhd.setText(tr("Yeni sanal disk (VHD)..."))
        self.act_write_backup.setText(tr("Yedegi diske yaz..."))
        self.act_write_backup.setToolTip(
            tr("Acik .dub yedegini yeni bir goruntu dosyasina veya "
            "fiziksel diske yazar"))
        self.act_backup_info.setText(tr("Yedek dosyasi bilgisi..."))
        self.act_sysinfo.setText(tr("Sistem bilgisi"))
        self.act_diag_status.setText(tr("Tanilama durumu..."))
        self.act_diag_status.setToolTip(
            tr("Gunluk yolu, donma sayisi ve o an calisan islemler"))
        self.act_diag_folder.setText(tr("Gunluk klasorunu ac"))
        self.act_diag_report.setText(tr("Son donma raporunu goster..."))
        self.act_diag_dump.setText(tr("Simdi yigin dokumu al"))
        self.act_diag_dump.setToolTip(
            tr("Butun is parcaciklarinin o anki yiginini dosyaya yazar"))
        self.act_about.setText(tr("Hakkinda"))
        self.act_licenses.setText(tr("Ucuncu taraf lisanslari..."))
        self.act_check_updates.setText(tr("Guncellemeleri denetle..."))
        self.act_auto_updates.setText(tr("Acilista guncellemeleri denetle"))

    # ==================================================================
    # Dil
    # ==================================================================
    def _build_language_menu(self) -> None:
        """Dil menusunu doldurur (her dil icin isaretlenebilir bir giris).

        Diller `i18n/catalogs/` icindeki dosyalardan gelir; yeni bir dil
        eklemek icin bu dosyaya dokunmak gerekmez.
        """
        self.m_lang.clear()
        self._lang_group = QActionGroup(self)
        self._lang_group.setExclusive(True)
        for code, name in i18n.available_languages():
            action = QAction(name, self)
            action.setCheckable(True)
            action.setChecked(code == i18n.current_language())
            action.setData(code)
            action.triggered.connect(
                lambda _checked, c=code: self.change_language(c))
            self._lang_group.addAction(action)
            self.m_lang.addAction(action)

    # ==================================================================
    # Ikon seti (ADR 0046)
    # ==================================================================
    def _build_icon_menu(self) -> None:
        """Ikon seti menusu: her set kendi ikonuyla (o setten cizilmis) gorunur."""
        self.m_icons.clear()
        self._icon_group = QActionGroup(self)
        self._icon_group.setExclusive(True)
        current = iconsets.current()
        for key, _text in iconsets.SETS:
            action = QAction(iconsets.label(key), self)
            action.setCheckable(True)
            action.setChecked(key == current)
            action.setData(key)
            # Onizleme: menu satirinda o setin "disk" ikonu
            action.setIcon(QIcon(icons_mod.draw("disk", 16, icon_set=key)))
            action.triggered.connect(
                lambda _checked, k=key: self.change_icon_set(k))
            self._icon_group.addAction(action)
            self.m_icons.addAction(action)

    # ==================================================================
    # Tema (ADR 0088)
    # ==================================================================
    def _build_theme_menu(self) -> None:
        """Tema menusu: Sistem / Acik / Koyu (isaretlenebilir)."""
        self.m_theme.clear()
        self._theme_group = QActionGroup(self)
        self._theme_group.setExclusive(True)
        current = current_theme()
        for key, _text in THEMES:
            action = QAction(theme_label(key), self)
            action.setCheckable(True)
            action.setChecked(key == current)
            action.setData(key)
            action.triggered.connect(
                lambda _checked, k=key: self.change_theme(k))
            self._theme_group.addAction(action)
            self.m_theme.addAction(action)

    def change_theme(self, key: str) -> None:
        """Temayi degistirir ve saklar; yeniden baslatma gerekmez.

        Palet degisince Qt standart pencereleri kendiliginden yeniler; kendi
        cizdigimiz ogeler (disk haritasi, ikonlar) rengi paletten okudugu
        icin onbellek temizlenip yeniden cizdirilir.
        """
        with diagnostics.span("ui.change_theme", key=key):
            applied = apply_theme(QApplication.instance(), key, remember=True)
            icons_mod.clear_cache()
            for action in self._theme_group.actions():
                action.setChecked(action.data() == applied)
            for widget in QApplication.allWidgets():
                widget.update()
        self.log(tr("Tema degistirildi: {}", theme_label(applied)))

    def change_icon_set(self, key: str) -> None:
        """Ikon setini degistirir; arayuz yeniden baslatilmadan yenilenir."""
        if key == iconsets.current():
            return
        with diagnostics.span("ui.change_icon_set", key=key):
            iconsets.set_current(key)
            icons_mod.clear_cache()
            # Menu isareti de esitlenir: set menuden degil koddan (ayar,
            # test) degistiginde eski secenek isaretli kaliyordu.
            for action in self._icon_group.actions():
                action.setChecked(action.data() == key)
            for widget in QApplication.allWidgets():
                widget.update()
        self.log(tr("Ikon seti degistirildi: {}", iconsets.label(key)))

    def show_licenses(self) -> None:
        """Gomulu ucuncu taraf ikon paketlerinin lisanslari.

        MIT/ISC/Apache lisanslari telif ve izin metninin dagitimla birlikte
        verilmesini ister; metinler paket modullerinde durur ve burada
        gosterilir.
        """
        import platform as _py_platform
        from PyQt5.QtCore import PYQT_VERSION_STR, QT_VERSION_STR
        from .. import licenses as lic
        try:
            from PyQt5.sip import SIP_VERSION_STR
        except Exception:                          # noqa: BLE001
            SIP_VERSION_STR = ""
        surumler = {"qt": QT_VERSION_STR, "pyqt5": PYQT_VERSION_STR,
                    "sip": SIP_VERSION_STR, "python": _py_platform.python_version()}
        parts = [tr("<p>Bu uygulamanin indirilebilir surumleri (Windows exe, "
                    "Linux AppImage, macOS) asagidaki bilesenleri icinde "
                    "tasir. Uygulamanin kendisi GNU GPL surum 3 ile "
                    "lisanslidir; kaynak kodu: {}</p>",
                    f"<a href='{PROJECT_URL}'>{PROJECT_URL}</a>")]
        for item in lic.COMPONENTS:
            not_metni = (tr(item["note"], f"<a href='{lic.QT_SOURCE_URL}'>"
                            f"{lic.QT_SOURCE_URL}</a>") if item["note"] else "")
            parts.append(
                f"<h4>{item['name']} {surumler.get(item['key'], '')} — "
                f"{item['license']}</h4>"
                f"<p>{html.escape(item['copyright'])} · "
                f"<a href='{item['url']}'>{item['url']}</a></p>"
                + (f"<p>{not_metni}</p>" if not_metni else "")
                + f"<pre style='white-space:pre-wrap'>"
                  f"{html.escape(lic.text(item))}</pre>")
        parts.append(tr("<p>Bu uygulama asagidaki ikon paketlerinden secilmis "
                    "ikonlari gomulu olarak icerir. Isletim sistemi "
                    "amblemleri sahiplerinin ticari markasidir; yalnizca "
                    "diski tanitmak icin gosterilir.</p>"))
        for info in iconpacks.notices():
            parts.append(
                f"<h4>{info['title']} {info['version']} — {info['license']}</h4>"
                f"<p><a href='{info['url']}'>{info['url']}</a> · "
                + tr("{} ikon", info["count"]) + "</p>"
                f"<pre style='white-space:pre-wrap'>{html.escape(info['text'])}</pre>")
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("Ucuncu taraf lisanslari"))
        dialog.resize(720, 560)
        layout = QVBoxLayout(dialog)
        view = QTextBrowser()
        view.setOpenLinks(False)               # Qt degil open_url acar (root)
        view.anchorClicked.connect(lambda url: platform.open_url(url.toString()))
        view.setHtml("".join(parts))
        layout.addWidget(view)
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(dialog.reject)
        layout.addWidget(box)
        dialog.exec_()

    def change_language(self, code: str) -> None:
        """Arayuz dilini degistirir ve secimi saklar.

        Uygulama yeniden baslatilmaz: butun metinler yerinde yenilenir
        (ADR 0027). Yeniden baslatma istemek, acik bir diskin kapanmasi
        demek olurdu.
        """
        if code == i18n.current_language():
            return
        with diagnostics.span("ui.change_language", code=code):
            applied = i18n.set_language(code)
        self.log(tr("Dil degistirildi: {}", i18n.language_name(applied)))

    def retranslate(self) -> None:
        """Butun gorunen metinleri etkin dilde yeniden yazar.

        `i18n.add_listener` ile dil degisimine baglidir; diyaloglar zaten her
        acilista yeniden kuruldugu icin burada yalnizca **surekli acik olan**
        bilesenler tazelenir.
        """
        self._retranslate_actions()
        for menu, label in getattr(self, "_menus", []):
            menu.setTitle(tr(label))
        self.toolbar.setWindowTitle(tr("Islemler"))
        self.tree.setHeaderLabel(tr("Disk ve Bolumler"))
        self.tabs.setTabText(TAB_FILES, tr("Dosya Gezgini"))
        self.tabs.setTabText(TAB_INFO, tr("Bolum Bilgisi"))
        self.tabs.setTabText(TAB_HEX, tr("Onaltilik Goruntuleyici"))
        self.tabs.setTabText(TAB_LOG, tr("Islem Gunlugu"))
        self._refresh_rights_label()
        self.browser.retranslate()
        self.part_table.retranslate()
        self.hex_view.retranslate()
        self._build_language_menu()
        self._build_icon_menu()
        self._build_theme_menu()
        if self.session is None:
            self.status_file.setText(tr("Disk goruntusu acik degil"))
            self.info_view.setPlainText(self._physical_summary_text())
            self._build_tree([])
        else:
            self.refresh(reload=False)
        self._refresh_pending()
        self.disk_map.update()
        self.disk_overview.update()

    def _refresh_rights_label(self) -> None:
        """Durum cubugundaki yetki rozetini yazar (dile ve yetkiye gore)."""
        name = elevation_name()
        if is_elevated():
            self.status_rights.setText(f"🔑 {name}")
            self.status_rights.setToolTip(
                tr("Uygulama {} yetkisiyle calisiyor; fiziksel disklere "
                   "erisebilir.", name))
        else:
            self.status_rights.setText(tr("Normal kullanici"))
            self.status_rights.setToolTip(
                tr("Fiziksel disk erisimi icin {} gerekir. "
                   "Disk menusu > '{} olarak yeniden baslat'", name, name))
        self.status_rights.setEnabled(is_elevated())

    # ==================================================================
    # Gunluk
    # ==================================================================
    def log(self, message: str) -> None:
        zaman = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{zaman}] {message}")
        self.statusBar().showMessage(message, 6000)
        # Arayuzde gorunen her satir tanilama gunluguna da girer; boylece islem
        # gunlugu ile sure olcumleri **tek dosyada** yan yana okunur.
        diagnostics.info(f"arayuz: {message}")
        try:
            folder = platform.make_user_dirs(log_root())
            gun = datetime.datetime.now().strftime("%Y-%m-%d")
            log_path = os.path.join(folder, f"app-{gun}.log")
            is_new = not os.path.exists(log_path)
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} {message}\n")
            if is_new:
                platform.restore_owner(log_path)   # yetkili kopyada root'a kalmasin
        except OSError:
            pass

    def _on_freeze(self, report: str, seconds: float) -> None:
        """Donma yakalayici arayuzun takildigini bildirdiginde calisir.

        Donma **bittikten sonra** cagrilir (bloke arayuze sinyal ulasamaz), bu
        yuzden kullanici dondugu ani sonradan ogrenir ve raporu nerede
        bulacagini bilir.
        """
        self.log(tr("DIKKAT: arayuz {:.1f} sn yanit vermedi — rapor: {}",
                    seconds, os.path.basename(report)))

    def error(self, title: str, message: str) -> None:
        QMessageBox.critical(self, title, message)
        diagnostics.warn(f"hata diyalogu: {title} — {message}")
        self.log(tr("HATA — {}: {}", title, message))

    # ==================================================================
    # Goruntu islemleri
    # ==================================================================
    def _image_dir(self) -> str:
        """Yeni goruntu/klon icin klasor: acik goruntununki, fiziksel diskte
        kullanicinin belge klasoru (ADR 0073 — eskiden /dev oneriliyordu)."""
        if self.session is None:
            return platform.default_image_dir()
        return platform.suggested_image_dir(self.session.path, self.session.is_physical)

    def new_image(self) -> None:
        default = self._image_dir()
        dlg = NewImageDialog(self, default)
        if exec_dialog(dlg) != NewImageDialog.Accepted:
            return
        v = dlg.values()

        def task(progress):
            progress(tr("Goruntu dosyasi olusturuluyor..."), 10)
            session = DiskSession.create(v["path"], v["size"], sparse=v["sparse"],
                                        scheme=v["scheme"], overwrite=True)
            if v["auto_partition"]:
                progress(tr("Bolum olusturuluyor..."), 40)
                free = session.free_regions()
                if free:
                    en_buyuk = max(free, key=lambda r: r.sector_count)
                    session.create_partition(en_buyuk.start_lba, en_buyuk.sector_count,
                                            fs_key=v["fs"], label=v["label"],
                                            name=v["label"],
                                            progress=lambda m, p: progress(m, 40 + p // 2))
            return session

        ok, result = run_task(self, tr("Yeni disk goruntusu"), task)
        if not ok:
            self.error(tr("Goruntu olusturulamadi"), str(result))
            return
        self._add_session(result)
        self.log(tr("Goruntu olusturuldu: {} ({})", v['path'], human_size(v['size'])))
        self.refresh()

    def open_image(self) -> None:
        desen = " ".join(f"*.{u}" for u in IMAGE_EXTENSIONS)
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Disk goruntusu ac"), "",
            tr("Disk goruntuleri ({});;Ham goruntu (*.img *.raw *.dd "
               "*.bin);;Sanal diskler (*.vhd *.vhdx *.vdi *.vmdk "
               "*.qcow2);;Tum dosyalar (*)", desen))
        if not path:
            return
        self.open_path(path)

    def open_path(self, path: str) -> None:
        mevcut = self._find_open(path)
        if mevcut is not None:
            self.log(tr("Zaten acik, one getirildi: {}", path))
            self.session = mevcut
            self.refresh()
            return
        try:
            self._add_session(DiskSession.open(path))
        except Exception as exc:
            self.error(tr("Goruntu acilamadi"), str(exc))
            return
        # Salt okunur acildiysa kullanici bunu bicimlendirmeye calisirken degil,
        # HEMEN ogrenmeli. Yedek dosyasinin salt okunur olmasi ise bir kusur
        # degil, dogasidir; onun icin ayri ve bilgilendirici bir not gosterilir.
        # Uyari yalnizca kaynak **hic** degistirilemiyorsa anlamlidir: salt
        # okunur acilmak artik normaldir (ADR 0025), degistirilemez olmak degil.
        if self.session.is_backup:
            self._backup_opened_note()
        elif not self.session.can_become_writable()[0]:
            self._readonly_open_warning(path)
        self.log(tr("Goruntu acildi: {} — {}, {}, {}",
                    path, self.session.format_name,
                    human_size(self.session.image.size),
                    self.session.scheme_name))
        self.refresh(reload=False)

    def _backup_opened_note(self) -> None:
        """Yedek acildiginda bir kereye mahsus bilgilendirme.

        Yedek artik **gezilebilir** (bkz. `clone.DubImage`): bolum tablosu ve
        dosyalar geri yukleme olmadan gorulur. Ancak salt okunurdur ve bu bir
        kusur degil, dogasidir; kullanici nasil yazacagini burada ogrenir.
        """
        info = getattr(self.session.image, "info", None)
        if info is None:
            return
        self.log(tr("Yedek acildi (salt okunur): {} — kaynak {}, yedek {}",
                    os.path.basename(info.path), human_size(info.total_bytes),
                    human_size(info.file_size)))
        if getattr(self, "_backup_note_shown", False):
            return
        self._backup_note_shown = True
        box = QMessageBox(QMessageBox.Information, tr("Yedek dosyasi acildi"),
                           tr("<b>{}</b> bir DiskUltimate yedegidir. Icerigi "
                              "<b>salt okunur</b> olarak gezebilirsiniz: "
                              "bolumler, klasorler ve dosyalar gorunur, "
                              "dosyalari disa "
                              "aktarabilirsiniz.<br><br><b>Kaynak boyut:</b> "
                              "{}<br><b>Yedek boyut:</b> "
                              "{}<br><b>Olusturma:</b> {}<br><br>Yedegi bir "
                              "<b>diske veya goruntuye yazmak</b> icin: "
                              "<i>Disk &gt; Yedegi diske yaz...</i>",
                              os.path.basename(info.path),
                              human_size(info.total_bytes),
                              human_size(info.file_size),
                              info.created.strftime('%Y-%m-%d %H:%M') if info.created else '-'),
                           QMessageBox.NoButton, self)
        write_btn = box.addButton(tr("Diske yaz..."), QMessageBox.AcceptRole)
        box.addButton(tr("Simdilik gez"), QMessageBox.RejectRole)
        box.exec_()
        if box.clickedButton() is write_btn:
            self.write_backup_to_target()

    # ------------------------------------------------------------------
    # Yedegi hedefe yazma (goruntu dosyasi veya fiziksel disk)
    # ------------------------------------------------------------------
    def close_image(self) -> None:
        """Etkin oturumu kapatir; diger acik goruntuler listede kalir."""
        if self.session:
            if not self.queue.is_empty and not self._confirm_drop_queue():
                return
            self._queues.pop(id(self.session), None)
            self.log(tr("Kapatildi: {}", self.session.path or self.session.name))
            self.osinfo.wait(self.session)
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
        self.map_stack.setCurrentIndex(0)
        self._refresh_overview()
        self.disk_map.clear()
        self.part_table.set_partitions([], [])
        self.browser.set_filesystem(None)
        self.hex_view.set_device(None)
        self.info_view.setPlainText(self._physical_summary_text())
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.status_file.setText(tr("Disk goruntusu acik degil"))
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
            self, tr("Goruntu boyutu"),
            tr("Mevcut boyut: {}\n\nYeni boyut (orn. 4 GB, 512 MB):",
               human_size(mevcut)),
            text=human_size(mevcut))
        if not ok:
            return
        try:
            new = parse_size(metin)
        except Exception:
            self.error(tr("Gecersiz boyut"), tr("Boyut cozumlenemedi: {}", metin))
            return
        if new < mevcut:
            self.log(tr("DIKKAT: kucultme sondaki verileri siler "
                     "(uygulama onayinda yeniden sorulur)"))
        self.enqueue(ops.resize_image_op(new))

    # ==================================================================
    # Tablo islemleri
    # ==================================================================
    def create_table(self, scheme: str) -> None:
        if not self._require_session():
            return
        if self.session.partitions:
            self.log(tr("DIKKAT: yeni tablo mevcut {} bolumun tanimini siler",
                        len(self.session.partitions)))
        self.enqueue(ops.create_table_op(scheme))

    def clear_table(self) -> None:
        if not self._require_session():
            return
        self.enqueue(ops.clear_table_op())

    # ==================================================================
    # Bolum islemleri
    # ==================================================================
    def create_partition(self) -> None:
        if not self._require_session():
            return
        tablosuz = bool(self.session.table) and self.session.table.scheme == "none"
        if not self.session.table or tablosuz:
            if tablosuz:
                # Tum disk tek dosya sistemi (tablosuz USB, .iso, ext4.img):
                # tablo kurmak o dosya sistemini siler; varsayilan "Hayir".
                cevap = QMessageBox.question(
                    self, tr("Tablosuz disk"),
                    tr("Bu diskte bolum tablosu yok; dosya sistemi ({}) tum "
                       "diski kapliyor. GPT olusturmak bu dosya sistemini "
                       "siler. Devam edilsin mi?",
                       fs_display(self.session.partitions[0].fs_type) or "?"),
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            else:
                cevap = QMessageBox.question(
                    self, tr("Bolum tablosu yok"),
                    tr("Bu goruntude bolum tablosu yok. Simdi GPT olusturulsun mu?"),
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if cevap != QMessageBox.Yes:
                return
            self.create_table("gpt")
            if not self.session.table:
                return

        # Bos alan **plana gore** sorulur: kuyrukta bekleyen "yeni bolum"
        # adimlari diskte gorunmez ama o alani tutar (ADR 0034).
        bolgeler = self.planned_free_regions()
        if not bolgeler:
            QMessageBox.information(
                self, tr("Bos alan yok"),
                tr("Yeni bolum icin yeterli bos alan bulunamadi.\n\n"
                   "Bekleyen adimlar da yer tutar; listeyi bosaltmak alani "
                   "geri verir."))
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
            # Birincil bolum sayisi da plana gore sayilir: kuyruktaki
            # bolumler de yer kaplayacaktir.
            planlananlar = self.planned_partitions()
            ext = next((p for p in planlananlar
                        if not p.logical and p.type_id in MBR_EXTENDED_TYPES),
                       None)
            genisletilmis_var = ext is not None
            birincil_musait = sum(1 for p in planlananlar if not p.logical) < 4
            if ext is not None and ext.start_lba <= bolge.start_lba <= ext.end_lba:
                ic_genisletilmis = True
            if not birincil_musait and not ic_genisletilmis:
                QMessageBox.information(
                    self, tr("Bolum eklenemez"),
                    tr("MBR tablosunda 4 birincil bolum dolu.\n"
                    "Daha fazla bolum icin genisletilmis bolum icinde mantiksal "
                    "bolum olusturun."))
                return

        dlg = CreatePartitionDialog(bolge, self.session.scheme, self,
                                    can_primary=birincil_musait,
                                    has_extended=genisletilmis_var,
                                    inside_extended=ic_genisletilmis)
        if exec_dialog(dlg) != CreatePartitionDialog.Accepted:
            return
        v = dlg.values()

        sector = self.session.image.sector_size
        if self._planned_conflict(v["start_lba"], v["sector_count"]):
            return
        if v["kind"] == "extended":
            self.enqueue(ops.create_op(v["start_lba"], v["sector_count"], sector,
                                       extended=True))
            return
        self.enqueue(ops.create_op(
            v["start_lba"], v["sector_count"], sector, fs_key=v["fs"],
            label=v["label"], name=v["name"], bootable=v["bootable"],
            logical=(v["kind"] == "logical") if v["kind"] else None))

    def format_partition(self) -> None:
        part = self._current_partition()
        if part is None:
            return
        dlg = FormatDialog(part, self)
        if exec_dialog(dlg) != FormatDialog.Accepted:
            return
        v = dlg.values()
        # Hedef **LBA ile** de tasinir: onceki adimlar bolum numaralarini
        # kaydirabilir (ADR 0033).
        self.enqueue(ops.format_op(
            part.index, v["fs"], label=v["label"],
            fs_name=tr(FS_BY_KEY[v["fs"]].label), at_lba=part.start_lba,
            cluster_bytes=v["cluster"], quick=v["quick"]))

    def resize_partition(self) -> None:
        """Bolumu suruklemeli pencereyle kucultur, buyutur veya tasir.

        Pencere ortak modelden beslenir (ADR 0049): sinirlar planlanan
        yerlesime gore, dosya sistemi sinirlari ortak servisten.
        """
        part = self._current_partition()
        if part is None:
            return
        info = self._limits_blocking([part])
        if info is None:
            return
        info = info[part.index]
        if not info.resizable and not info.movable:
            QMessageBox.information(
                self, tr("Boyutlandirilamaz"),
                tr("Bu bolum boyutlandirilamiyor.\n\n{}", info.note))
            return
        model = self._edit_model()
        try:
            slot = model.get(part.index) if model is not None else None
        except Exception:                              # noqa: BLE001
            slot = None
        if slot is None or slot.locked:
            self.error(tr("Boyutlandirma hazirlanamadi"),
                       tr("Bu bolum bu gorunumde duzenlenemez."))
            return
        lower, upper = model.window(part.index)
        window = ResizeWindow(lower, upper, part.sector_size)

        kullanilan = -1
        try:
            fs = self.session.filesystem(part.index)
            if fs is not None:
                istatistik = fs.stats() or {}
                kullanilan = istatistik.get("used_bytes", -1)
        except Exception:                              # noqa: BLE001
            kullanilan = -1
        self.session.close_filesystems()

        before = {s.index: (s.new_start, s.new_count) for s in model.parts}
        # Pencere sinirlari MODELDEN: ham bolumun kucultulmemesi gibi
        # kurallar harita ve pencerede ayni olsun (ADR 0049).
        info = dataclasses.replace(info, min_sectors=slot.min_count,
                                   max_sectors=slot.max_count,
                                   movable=slot.movable)
        dlg = ResizePartitionDialog(slot.as_partition(), window, info,
                                    align_sectors=self.session.table.align_sectors,
                                    used_bytes=kullanilan, parent=self)
        if exec_dialog(dlg) != ResizePartitionDialog.Accepted:
            return
        v = dlg.values()
        slot.new_start, slot.new_count = v["start_lba"], v["sector_count"]
        self._commit_edit(model, [part.index], before=before,
                          fs_infos={part.index: info})

    def edit_partition_layout(self) -> None:
        """Butun bolumleri tek pencerede duzenler (ortak pencere, ADR 0049).

        Geri yuklemedeki "Bolumleri yonet" ile ayni penceredir. Sonuc
        bekleyen islemlere boyutlandirma adimlari olarak eklenir; ayni bolumun
        mevcut adimi yerinde guncellenir, sira uygulanabilir tutulur.
        """
        if not self._require_session() or self.session.table is None:
            return
        disk_parts = [p for p in self.session.table.partitions
                      if not p.logical]
        if self._limits_blocking(disk_parts) is None:
            return
        model = self._edit_model()
        if model is None:
            self.error(tr("Bolum duzeni acilamadi"),
                       tr("Bolum tablosu kuyrukta degisiyor; once bekleyen "
                          "islemleri uygulayin ya da kaldirin."))
            return
        before = {s.index: (s.new_start, s.new_count) for s in model.parts}
        dlg = PartitionLayoutDialog(model, self.session.name, self, mode="disk")
        if exec_dialog(dlg) != PartitionLayoutDialog.Accepted:
            return
        result = dlg.result_layout()
        changed = [s.index for s in result.parts
                   if (s.new_start, s.new_count) != before.get(s.index)]
        if changed:
            self._commit_edit(result, changed, before=before)

    def _queue_resize(self, index: int, start_lba: int,
                      sector_count: int) -> None:
        """Tek bolumu verilen yere/boyuta getiren adimi kuyruga yazar."""
        model = self._edit_model()
        if model is None:
            return
        slot = model.get(index)
        before = {s.index: (s.new_start, s.new_count) for s in model.parts}
        slot.new_start, slot.new_count = start_lba, sector_count
        self._commit_edit(model, [index], before=before)

    # ==================================================================
    # Ortak bolum duzenleme modeli (ADR 0049)
    # ==================================================================
    def _edit_model(self):
        """Etkin oturum + kuyruktan duzenlenebilir yerlesim (yoksa None).

        Dosya sistemi sinirlari **istenmez**, yalnizca hazir olanlar
        kullanilir; hazir olmayan bolum kilitli (`pending`) gelir.
        """
        session = self.session
        if session is None or session.table is None:
            return None
        return queueedit.build(
            session, self.queue,
            limits=lambda part: self.limits.get(session, part, request=False))

    def _limits_blocking(self, parts):
        """Verilen bolumlerin sinirlari; eksikse ilerleme penceresiyle
        arka planda hesaplanir. Iptal/hata: None."""
        session = self.session
        missing = [p for p in parts
                   if self.limits.get(session, p, request=False) is None]
        if missing:
            ok, result = run_task(
                self, tr("Dosya sistemi sinirlari okunuyor"),
                lambda report: [self.limits.compute_now(session, p)
                                for p in missing])
            if not ok:
                self.error(tr("Boyutlandirma hazirlanamadi"), str(result))
                return None
        out = {}
        for p in parts:
            info = self.limits.get(session, p, request=False)
            if info is not None and info.kind == "native":
                # Windows'un kendi siniri (PowerShell): yalnizca pencere
                # icin, gorev penceresinde sorulur.
                ok, native = run_task(
                    self, tr("Dosya sistemi sinirlari okunuyor"),
                    lambda report, i=p.index: session.resize_info(i))
                if ok:
                    info = native
            out[p.index] = info
        return out

    def _refresh_edit_layout(self) -> None:
        """Haritanin tutamaklarini secime ve kuyruga gore tazeler.

        Tutamak **diskteki** (planda da var olan) bolum icindir; kuyrukta
        yeni olan bolumde, "diskteki hali" gosterilirken dolu kuyrukta ve
        yazma moduna gecemeyen kaynakta verilmez. Secili bolum ve planlanan
        komsularinin sinirlari arka planda istenir (sinir tutamagi iki
        bolumu birden degistirir).
        """
        session = self.session
        index = self.selected_partition
        usable = (session is not None and session.table is not None
                  and index is not None and not self.selected_is_planned
                  and session.can_become_writable()[0]
                  and (self.queue.is_empty or self._show_plan))
        model = self._edit_model() if usable else None
        busy = False
        if model is not None:
            for i in queueedit.neighbours(model, index):
                try:
                    disk_part = session.table.get(i)
                except Exception:                      # noqa: BLE001
                    continue
                self.limits.get(session, disk_part, request=True)
            try:
                busy = model.get(index).pending
            except Exception:                          # noqa: BLE001
                busy = False
        self._edit_layout = model
        self.disk_map.set_edit_layout(model, focus=index, busy=busy)

    def _on_os_ready(self, session) -> None:
        """Bolumlerin isletim sistemleri bulundu: agac, tablo ve harita tazelenir.

        Suruklenirken tazelenmez (yerlesim degisiyor); sonuc onbellektedir,
        bir sonraki tazelemede gorunur.
        """
        if self.session is None or self.disk_map.edit.dragging:
            return
        if session is not self.session and session not in self.sessions:
            return
        self.refresh(reload=False)

    def _on_limits_ready(self) -> None:
        for exc in self.limits.errors():
            diagnostics.info(f"bolum sinirlari okunamadi: {exc}")
        if not self.disk_map.edit.dragging:
            self._refresh_edit_layout()

    def _commit_edit(self, model, indices, before=None, fs_infos=None) -> None:
        """Duzenlenen bolumleri kuyruga yazar (harita, pencereler ortak)."""
        if model is None or self.session is None:
            return
        if not self._recheck_shrink(model, indices):
            self.refresh(reload=False)
            return
        infos = dict(fs_infos or {})
        for index in indices:
            if index not in infos:
                try:
                    part = self.session.table.get(index)
                except Exception:                      # noqa: BLE001
                    continue
                info = self.limits.get(self.session, part, request=False)
                if info is not None:
                    infos[index] = info
        try:
            changed = queueedit.commit(self.session, self.queue, model,
                                       indices, before=before,
                                       fs_infos=infos)
        except Exception as exc:                       # noqa: BLE001
            self.error(tr("Yeni yerlesim gecersiz"), str(exc))
            self.refresh(reload=False)
            return
        for operation in changed:
            self.log(tr("Kuyruga eklendi: {}", operation))
        self._refresh_pending()

    def _recheck_shrink(self, model, indices) -> bool:
        """Kuculen bolumun en az boyutunu kuyruga yazmadan once DISKTEN olcer.

        Onbellekteki sinir eskimis olabilir (dosya baska yoldan eklendi, ayni
        aygit baska programca degisti). Uygula aninda cekirdek zaten reddeder;
        bu denetim imkansiz adimin kuyruga hic girmemesi icindir. Yalnizca
        kuculen ve sinirini dosya sistemi veren bolumler olculur.
        """
        session = self.session
        targets = []
        for index in indices:
            try:
                slot = model.get(index)
                part = session.table.get(index)
            except Exception:                          # noqa: BLE001
                continue
            if slot is not None and slot.shrinks and slot.fs_resizable:
                targets.append((slot, part))
        if not targets:
            return True
        for _slot, part in targets:
            self.limits.forget(session, part.index)
        fresh = self._limits_blocking([part for _slot, part in targets])
        if fresh is None:
            return False
        for slot, part in targets:
            info = fresh.get(part.index)
            if info is None or slot.new_count >= info.min_sectors:
                continue
            used = part.fs_used if part.fs_used and part.fs_used > 0 else 0
            self.error(
                tr("Bolum bu kadar kuculemez"),
                tr("Bolum {} en az {} olabilir (dolu: {}); istenen {}.\n\n"
                   "Sinir az once diskten yeniden olculdu; adim kuyruga "
                   "eklenmedi.", part.index,
                   human_size(info.min_sectors * part.sector_size),
                   human_size(used) if used else tr("bilinmiyor"),
                   human_size(slot.new_count * part.sector_size)))
            return False
        return True

    def delete_partition(self) -> None:
        part = self._current_partition()
        if part is None:
            return
        self.enqueue(ops.delete_op(
            part.index, at_lba=part.start_lba,
            name=f"{part.display_name}, {human_size(part.size)}"))

    def toggle_bootable(self) -> None:
        part = self._current_partition()
        if part is None:
            return
        self.enqueue(ops.boot_op(part.index, not part.bootable,
                                 at_lba=part.start_lba))

    def rename_partition(self) -> None:
        part = self._current_partition()
        if part is None:
            return
        if self.session.scheme != "gpt":
            QMessageBox.information(self, tr("Desteklenmiyor"),
                                    tr("Bolum adi yalnizca GPT semasinda saklanir.\n"
                                    "MBR icin birim etiketini degistirin."))
            return
        name, ok = QInputDialog.getText(self, tr("Bolum adi"), tr("Yeni ad:"), text=part.name)
        if not ok:
            return
        self.enqueue(ops.rename_op(part.index, name.strip(),
                                   at_lba=part.start_lba))

    def change_type(self) -> None:
        part = self._current_partition()
        if part is None:
            return
        if self.session.scheme == "mbr":
            secenekler = [f"0x{k:02X} — {tr(v)}"
                          for k, v in sorted(MBR_TYPES.items())]
            mevcut = f"0x{part.type_id:02X} — {part.type_name}"
            choice, ok = QInputDialog.getItem(self, tr("Bolum turu"), tr("Tur:"), secenekler,
                                                secenekler.index(mevcut) if mevcut in secenekler else 0,
                                                False)
            if not ok:
                return
            self.enqueue(ops.type_op(part.index,
                                     type_id=int(choice.split(" ")[0], 16),
                                     name=choice, at_lba=part.start_lba))
            return
        else:
            secenekler = [f"{tr(v)} — {k}"
                          for k, v in GPT_TYPES.items() if v != "Bos"]
            choice, ok = QInputDialog.getItem(self, tr("Bolum turu"), tr("Tur:"),
                                                secenekler, 0, False)
            if not ok:
                return
            self.enqueue(ops.type_op(part.index,
                                     type_guid=choice.split(" — ")[1],
                                     name=choice, at_lba=part.start_lba))

    def change_label(self) -> None:
        part = self._current_partition()
        if part is None:
            return
        fs = self.session.filesystem(part.index)
        if fs is None or not fs.writable:
            QMessageBox.information(
                self, tr("Desteklenmiyor"),
                tr("Bu dosya sisteminde etiket degistirme desteklenmiyor.\n"
                "Bolumu yeniden bicimlendirerek etiket verebilirsiniz."))
            return
        etiket, ok = QInputDialog.getText(self, tr("Birim etiketi"), tr("Yeni etiket:"),
                                             text=fs.label)
        if not ok:
            return
        self.enqueue(ops.label_op(part.index, etiket.strip(),
                                  at_lba=part.start_lba))


    # ==================================================================
    # Baglama / surucu harfi (ADR 0043)
    # ==================================================================
    def _unmount_all(self, info) -> bool:
        """Diskteki tum bolumleri cikarir; hepsi basarili ise True.

        Uygula penceresindeki uyaridan cagrilir. Bir bolum cikarilamazsa
        islem **durur**: bagli bir dosya sistemi altindan ham sektor yazmak
        onu bozar, "cikaramadim ama devam ediyorum" demek riski gizlerdi.
        """
        table = self.session.table if self.session else None
        # (numara, bayt ofseti): Windows bolumu ofsetle bulur (ADR 0086)
        indexes = [(p.index, p.start_lba * table.sector_size)
                   for p in (table.partitions if table else [])]
        if not indexes:
            return True

        def task(report):
            failures = []
            for sira, (index, offset) in enumerate(indexes, 1):
                report(tr("Bolum {} cikariliyor...", index),
                       int(100 * sira / len(indexes)))
                ok, error = unmount_physical_partition(info, index, offset)
                if not ok:
                    failures.append((index, error))
            return failures

        ok, result = run_task(self, tr("Baglantilari kes"), task)
        if not ok:
            self.error(tr("Baglantilari kes"), str(result))
            return False
        if result:
            detay = "\n".join(f"#{i}: {h}" for i, h in result)
            self.log(tr("Cikarilamayan bolum var:") + " " + detay)
            QMessageBox.warning(self, tr("Baglantilari kes"),
                                tr("Bu bolumler cikarilamadi:") + f"\n\n{detay}")
            return False
        self.log(tr("Diskteki baglantilar kesildi"))
        return True

    def mount_selected_partition(self) -> None:
        """Secili bolumu baglar (Windows'ta surucu harfi atar)."""
        self._mount_or_unmount(True)

    def unmount_selected_partition(self) -> None:
        """Secili bolumun baglantisini keser / harfini kaldirir."""
        self._mount_or_unmount(False)

    def _mount_or_unmount(self, mount: bool) -> None:
        """Baglama islemi: kuyruga girmez, dogrudan calisir.

        Yikici degildir — veri yazilmaz, bolum tablosuna dokunulmaz — bu
        yuzden bekleyen islem kuyrugunun disindadir (ADR 0025 yalnizca
        yikici islemleri kuyruga alir).

        Isletim sistemi cagrisi oldugu ve suresi ongorulemedigi icin
        `run_task` ile is parcaciginda calisir (CLAUDE.md donma kurali):
        `udisksctl` polkit penceresi acabilir, PowerShell saniyeler surebilir.
        """
        part = self._current_partition()
        if part is None:
            return
        info = self.session.disk_info
        if info is None:
            QMessageBox.information(
                self, tr("Desteklenmiyor"),
                tr("Baglama yalnizca gercek disklerde anlamlidir; goruntu "
                   "dosyasi isletim sistemine bagli degildir."))
            return
        mount_text, unmount_text = mount_action_labels()
        title = mount_text if mount else unmount_text
        index = part.index
        offset = part.start_lba * self.session.table.sector_size
        label = part.fs_label or part.name
        fs_type = part.fs_type          # veri: baglama secenekleri buna gore

        def task(report):
            report(title, -1)
            if mount:
                return mount_physical_partition(info, index, label, fs_type,
                                                offset=offset)
            return unmount_physical_partition(info, index, offset=offset)

        ok, result = run_task(self, title, task)
        if not ok:
            self.error(title, str(result))
            return
        basarili, detay = result
        if not basarili:
            self.log(tr("{} basarisiz: {}", title, detay))
            if mount and fs_type == "NTFS" and not platform.IS_WINDOWS:
                # Linux'un NTFS'i baglamamasinin en sik nedeni Windows'un
                # birimi temiz kapatmamasidir (Hizli baslatma). Kullaniciya
                # terminale inmeden cozum sunulur (ADR 0077).
                cevap = QMessageBox.question(
                    self, title,
                    tr("{}\n\nWindows bu NTFS birimini temiz kapatmamis "
                       "olabilir (Hizli baslatma, hazirda bekletme, elektrik "
                       "kesintisi). Birimi simdi denetlemek ister misiniz?",
                       detay or tr("Islem basarisiz.")),
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
                if cevap == QMessageBox.Yes:
                    self.ntfs_fix(part)
                return
            QMessageBox.warning(self, title, detay or tr("Islem basarisiz."))
            return
        if mount:
            self.log(tr("Bolum {} baglandi: {}", index, detay))
        else:
            self.log(tr("Bolum {} cikarildi", index))
        # Bagli bolum listesi disk taramasindan gelir; tarama zorlanir ki
        # bilgi paneli ve agac hemen guncellensin (3 sn beklenmesin).
        self.refresh_disks()

    # ==================================================================
    # Sema donusumu ve hizalama
    # ==================================================================
    def convert_scheme(self, scheme: str) -> None:
        if not self._require_session():
            return
        name = scheme.upper()
        uygun, reason = self.session.can_convert_to(scheme)
        if not uygun:
            QMessageBox.information(self, tr("{} donusumu yapilamaz", name), reason)
            return
        self.log(tr("{} donusumu kuyruga alindi: {}", name, reason))
        self.log(tr("Donusumde bolum verileri yerinde kalir; kesinti tabloyu "
                 "bozabilir, onemli veriler icin once yedek alin"))
        self.enqueue(ops.convert_table_op(scheme))

    def show_alignment(self) -> None:
        if self.session is None or not self.session.table:
            QMessageBox.information(self, tr("Bolum yok"), tr("Once bir bolum "
                                                           "tablosu acin."))
            return
        rapor = self.session.alignment_report()
        if not rapor:
            QMessageBox.information(self, tr("Hizalama"), tr("Tabloda bolum yok."))
            return
        satirlar = {}
        for r in rapor:
            state = "Hizali (1 MiB)" if r["aligned_1m"] else (
                "4K hizali" if r["aligned_4k"] else
                f"HIZASIZ — 4K icinde {r['offset_in_4k']} bayt kayma")
            satirlar[tr("Bolum {} ({})", r["index"], r["name"])] = \
                tr("LBA {} — {}", r["start_lba"], state)
        hizasiz = [r for r in rapor if not r["aligned_4k"]]
        InfoDialog(tr("Hizalama Denetimi"), satirlar, self,
                   note=(tr("Tum bolumler 4K sinirinda hizali.") if not hizasiz
                         else tr("{} bolum 4K sinirinda hizali degil; SSD ve "
                                 "ileri bicim disklerde basarim dusebilir.",
                                 len(hizasiz)))).exec_()

    # ==================================================================
    # Yedekleme / geri yukleme / klonlama
    # ==================================================================
    # ==================================================================
    # Yedekleme ve geri yukleme — tek pencere (ADR 0032)
    # ==================================================================
    def open_backup_dialog(self, mode: str = MODE_BACKUP,
                           partition: bool = False,
                           backup_path: str = "") -> None:
        """Yedekleme/geri yukleme penceresini acar.

        Kaynak ve hedef **pencerenin icinde** secilir; bu yuzden burada acik
        oturum sarti yoktur. Secili disk ya da bolum varsa listede onceden
        isaretlenir — kullanici menuden ne bekliyorsa onu bulur.
        """
        index = -1
        if partition and self.selected_partition is not None \
                and not self.selected_is_planned:
            index = self.selected_partition
        if not self.sessions and not self._physical_cache:
            QMessageBox.information(
                self, tr("Kaynak yok"),
                tr("Yedeklenecek bir goruntu veya disk bulunamadi.\n\n"
                   "Bir goruntu acin ya da Disk > Fiziksel diskleri yenile."))
            return
        dlg = BackupDialog(
            self, mode=mode, sessions=list(self.sessions),
            disks=list(self._physical_cache.values()), surveys=self._surveys,
            session=self.session, partition_index=index,
            backup_path=backup_path, disk_path=self._selected_disk_path() or "")
        dlg.exec_()
        if dlg.result_value is not None:
            self.log(dlg.status.text())
        if dlg.opened_path:
            self.open_path(dlg.opened_path)
            return
        if dlg.reload_needed:
            self.refresh()
            self.refresh_disks()

    def write_backup_to_target(self) -> None:
        """Acik `.dub` yedegini bir hedefe yazar (ayni pencere, geri yukleme kipi)."""
        path = ""
        if self.session is not None and self.session.is_backup:
            path = getattr(self.session.image, "info", None)
            path = getattr(path, "path", "") or self.session.path
        self.open_backup_dialog(MODE_RESTORE, backup_path=path)

    def show_backup_info(self) -> None:
        """Yedek dosyasinin bilgisi ve icerigi — ayri pencere yok, ayni form."""
        self.open_backup_dialog(MODE_RESTORE)

    def clone_disk(self) -> None:
        """Diski klonlar: yeni bir goruntu dosyasina ya da baska bir diske."""
        if self.session is None:
            QMessageBox.information(self, tr("Goruntu yok"), tr("Once bir "
                                                             "goruntu acin."))
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(tr("Diski klonla"))
        box.setText(tr("<b>{}</b> nereye klonlansin?", self.session.name))
        to_file = box.addButton(tr("Goruntu dosyasina..."), QMessageBox.AcceptRole)
        to_disk = box.addButton(tr("Baska bir diske..."), QMessageBox.AcceptRole)
        box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(to_file)
        box.exec_()
        if box.clickedButton() is to_disk:
            self._clone_to_disk()
        elif box.clickedButton() is to_file:
            self._clone_to_file()

    def _clone_to_disk(self) -> None:
        """Baska bir fiziksel diske birebir klon (hedef tamamen silinir).

        Kuyruga girmez — geri yukleme gibi baska bir aygita yazar; onay
        hedef secim penceresinde alinir (silme kutusu, sistem diskinde ad).
        Hedef uygulamada aciksa o oturumun tutamaci kullanilir (ADR 0021).
        """
        from .dialogs.clone_target import CloneTargetDialog
        session = self.session
        _images, sources = disksource.collect(
            self.sessions, list(getattr(self, "_physical_cache", {}).values()),
            getattr(self, "_surveys", {}))
        if not sources:
            QMessageBox.information(
                self, tr("Hedef disk yok"),
                tr("Listede fiziksel disk yok. Diskleri yenileyin; Linux'ta "
                   "ve Windows'ta disk listesi yonetici yetkisi ister."))
            return
        source_path = (session.disk_info.path if session.is_physical
                       and session.disk_info is not None else session.path or "")
        dlg = CloneTargetDialog(self, session.name, source_path,
                                session.image.size, sources,
                                pending_steps=len(self.queue))
        if exec_dialog(dlg) != CloneTargetDialog.Accepted or dlg.problem():
            return
        target = dlg.selected()
        allow_system = dlg.allow_system
        if target.session is not None:
            work = (lambda progress: session.clone_to_session(
                target.session, allow_system=allow_system, progress=progress))
        else:
            disk = target.disk
            # Arka plan disk taramasi hedefe dokunmasin (ADR 0021)
            self._wait_for_scan()
            work = (lambda progress: session.clone_to_physical(
                disk, allow_system=allow_system, progress=progress))
        ok, result = run_task(self, tr("Disk klonlaniyor — {}", target.disk.name),
                              work)
        if not ok:
            self.error(tr("Klonlama basarisiz"), str(result))
            self.refresh_disks()
            return
        self.log(tr("Disk klonlandi: {} -> {} ({})", session.name,
                    target.disk.name, human_size(result)))
        self.refresh_disks()
        if target.session is not None:
            self.refresh()
        QMessageBox.information(
            self, tr("Klon hazir"),
            tr("{} diskine klonlandi ({}).\n\nIki disk ayni bilgisayarda "
               "takili kalirsa ayni disk kimligini tasidiklari icin isletim "
               "sistemi birini cevrimdisi yapabilir.", target.disk.name,
               human_size(result)))

    def _clone_to_file(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, tr("Klon hedefi"),
            os.path.join(self._image_dir(),
                         f"{os.path.splitext(self.session.name)[0]}-klon.img"),
            tr("Disk goruntusu (*.img)"))
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += ".img"

        ok, result = run_task(self, tr("Disk klonlaniyor"),
                             lambda progress: self.session.clone_to(path, progress=progress))
        if not ok:
            self.error(tr("Klonlama basarisiz"), str(result))
            return
        self.log(tr("Disk klonlandi: {}", result))
        cevap = QMessageBox.question(
            self, tr("Klon hazir"), tr("Klon olusturuldu:\n{}\n\nSimdi "
                                       "acilsin mi?", result),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if cevap == QMessageBox.Yes:
            self.open_path(result)

    # ==================================================================
    # NTFS denetle ve onar (ADR 0077)
    # ==================================================================
    def ntfs_fix(self, part: Optional[Partition] = None) -> None:
        """Denetimi is parcaciginda yapar, bulgulari gosterir, onarimi
        kuyruga ekler. Diske burada hicbir sey yazilmaz (ADR 0025)."""
        from .dialogs.ntfsfix import NtfsFixDialog

        part = part or self._current_partition()
        if part is None:
            return
        if part.fs_type != "NTFS":
            QMessageBox.information(self, tr("NTFS degil"),
                                    tr("Bu islem yalnizca NTFS bolumlerde "
                                       "kullanilabilir."))
            return
        # Linux/macOS'ta bagli birime yazmak onu bozar; Windows'ta ise
        # uygulama birimi kilitleyip ayirir (ADR 0016, 0075).
        if part.mount_point and not platform.IS_WINDOWS:
            QMessageBox.warning(
                self, tr("Bolum bagli"),
                tr("Bolum {} su anda bagli ({}). Bagli bir NTFS birimi "
                   "onarilamaz; once baglantisini kesin.", part.index,
                   part.mount_point))
            return
        index = part.index
        title = tr("NTFS denetleniyor — Bolum {}", index)
        ok, health = run_task(self, title, lambda report: (
            report(title, -1), self.session.ntfs_check(index))[1])
        if not ok:
            self.error(tr("NTFS denetlenemedi"), str(health))
            return
        for sorun in health.problems():
            self.log(tr("Bolum {}: {}", index, sorun))
        dlg = NtfsFixDialog(self, tr("Bolum {} ({})", index, part.display_name),
                            health)
        if exec_dialog(dlg) != QDialog.Accepted:
            return
        v = dlg.values()
        self.enqueue(ops.ntfs_fix_op(index, at_lba=part.start_lba, **v))

    # ==================================================================
    # Guvenli silme
    # ==================================================================
    def wipe(self, disk: bool = True) -> None:
        if not self._require_session():
            return
        if disk:
            target_name = tr("Tum disk ({})", self.session.name)
            size = self.session.image.size
            free_region = False
            index = -1
            at_lba = -1
        else:
            part = self._current_partition()
            if part is None:
                return
            target_name = tr("Bolum {} ({})", part.index, part.display_name)
            size = part.size
            index = part.index
            at_lba = part.start_lba
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
            self.enqueue(ops.wipe_free_op(index, at_lba=at_lba))
            return
        self.enqueue(ops.wipe_partition_op(
            index, v["method"], method_name=dlg.method_combo.currentText(),
            at_lba=at_lba))

    # ==================================================================
    # Kurtarma
    # ==================================================================
    def scan_deleted(self) -> None:
        part = self._current_partition()
        if part is None:
            return
        index = part.index
        ok, result = run_task(
            self, tr("Silinmis dosyalar taraniyor"),
            lambda progress: self.session.scan_deleted(index, progress=progress))
        if not ok:
            self.error(tr("Tarama basarisiz"), str(result))
            return
        if not result:
            QMessageBox.information(self, tr("Sonuc yok"),
                                    tr("Bu bolumde silinmis dosya girisi bulunamadi."))
            return
        self.log(tr("Bolum {}: {} silinmis giris bulundu", index, len(result)))
        dlg = DeletedFilesDialog(result, self, tr("Bolum {} — Silinmis "
                                                  "Dosyalar", index))
        if exec_dialog(dlg) != DeletedFilesDialog.Accepted:
            return
        secilenler = dlg.selected()
        if not secilenler:
            return
        klasor = QFileDialog.getExistingDirectory(self, tr("Kurtarma hedefi"))
        if not klasor:
            return

        def task(progress):
            basarili = 0
            for i, oge in enumerate(secilenler, 1):
                progress(tr("Kurtariliyor: {}", oge.name),
                       int(100 * i / len(secilenler)))
                try:
                    self.session.recover_deleted(index, oge,
                                                 os.path.join(klasor, oge.name))
                    basarili += 1
                except Exception:
                    pass
            return basarili

        ok, count = run_task(self, tr("Dosyalar kurtariliyor"), task)
        if not ok:
            self.error(tr("Kurtarma basarisiz"), str(count))
            return
        self.log(tr("{}/{} dosya kurtarildi -> {}", count, len(secilenler), klasor))
        QMessageBox.information(self, tr("Kurtarma tamamlandi"),
                                tr("{} dosya kurtarildi:\n{}", count, klasor))

    def scan_lost(self) -> None:
        if self.session is None:
            QMessageBox.information(self, tr("Goruntu yok"), tr("Once bir "
                                                             "goruntu acin."))
            return
        derin = QMessageBox.question(
            self, tr("Tarama derinligi"),
            tr("Derin tarama (64 KB adim) yapilsin mi?\n\n"
            "Hayir: hizli tarama (1 MB adim) — cogu durumda yeterlidir.\n"
            "Evet: yavas ama hizasiz bolumleri de bulur."),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes
        ok, result = run_task(
            self, tr("Kayip bolumler taraniyor"),
            lambda progress: self.session.scan_lost_partitions(deep=derin, progress=progress))
        if not ok:
            self.error(tr("Tarama basarisiz"), str(result))
            return
        if not result:
            QMessageBox.information(
                self, tr("Sonuc yok"),
                tr("Bolum tablosunda olmayan bir dosya sistemi bulunamadi."))
            return
        self.log(tr("Kayip bolum taramasi: {} aday bulundu", len(result)))
        dlg = LostPartitionsDialog(result, self)
        if exec_dialog(dlg) != LostPartitionsDialog.Accepted:
            return
        selected = dlg.selected()
        if not selected:
            return
        if not self._require_session():
            return
        # Bulunan bolumu tabloya eklemek bir kuyruk adimidir: hemen yazmak
        # yerine digerleriyle birlikte Uygula ile islenir.
        # keep_data: alan silinmez (eskiden yeni bolum gibi ilk/son 2 MB
        # siliniyor, kurtarilan dosya sistemi yok ediliyordu).
        self.enqueue(ops.create_op(
            selected.start_lba, selected.sector_count,
            self.session.image.sector_size, name=selected.label or "",
            keep_data=True, found_fs=selected.fs_type))

    def carve_files(self) -> None:
        if self.session is None:
            QMessageBox.information(self, tr("Goruntu yok"), tr("Once bir "
                                                             "goruntu acin."))
            return
        index = self.selected_partition if self.selected_partition is not None else -1
        kapsam = (tr("Bolum {}", index) if index > 0 else tr("Tum goruntu"))
        dlg = CarveOptionsDialog(self, kapsam)
        if exec_dialog(dlg) != CarveOptionsDialog.Accepted:
            return
        anahtarlar = dlg.selected_keys()
        if not anahtarlar:
            QMessageBox.information(self, tr("Secim yok"), tr("En az bir dosya "
                                                           "turu secin."))
            return
        ok, result = run_task(
            self, tr("Imza taramasi"),
            lambda progress: self.session.carve_files(index, keys=anahtarlar,
                                                    progress=progress))
        if not ok:
            self.error(tr("Tarama basarisiz"), str(result))
            return
        if not result:
            QMessageBox.information(self, tr("Sonuc yok"),
                                    tr("Secilen turlerde dosya imzasi bulunamadi."))
            return
        self.log(tr("Imza taramasi ({}): {} dosya bulundu", kapsam, len(result)))
        result_dlg = CarvedFilesDialog(result, self)
        if exec_dialog(result_dlg) != CarvedFilesDialog.Accepted:
            return
        secilenler = result_dlg.selected()
        if not secilenler:
            return
        klasor = QFileDialog.getExistingDirectory(self, tr("Cikarma hedefi"))
        if not klasor:
            return

        def task(progress):
            sayac = 0
            for i, oge in enumerate(secilenler, 1):
                progress(tr("Cikariliyor: {}", oge.suggested_name),
                       int(100 * i / len(secilenler)))
                try:
                    self.session.extract_carved(oge, klasor, index)
                    sayac += 1
                except Exception:
                    pass
            return sayac

        ok, count = run_task(self, tr("Dosyalar cikariliyor"), task)
        if ok:
            self.log(tr("{} dosya cikarildi -> {}", count, klasor))
            QMessageBox.information(self, tr("Tamamlandi"),
                                    tr("{} dosya cikarildi:\n{}", count, klasor))

    # ==================================================================
    # Diger araclar
    # ==================================================================
    def new_vhd(self) -> None:
        default = self._image_dir()
        dlg = NewImageDialog(self, default)
        dlg.setWindowTitle(tr("Yeni Sanal Disk (VHD)"))
        dlg.path_edit.setText(os.path.join(default, "yeni-disk.vhd"))
        if exec_dialog(dlg) != NewImageDialog.Accepted:
            return
        v = dlg.values()
        path = v["path"]
        if not path.lower().endswith(".vhd"):
            path = os.path.splitext(path)[0] + ".vhd"

        def task(progress):
            progress(tr("Sanal disk olusturuluyor..."), 10)
            disk = VhdImage.create_fixed(path, v["size"], sparse=v["sparse"],
                                         overwrite=True)
            disk.close()
            session = DiskSession.open(path)
            if v["scheme"]:
                session.create_table(v["scheme"])
                if v["auto_partition"]:
                    progress(tr("Bolum olusturuluyor..."), 40)
                    free = session.free_regions()
                    if free:
                        en_buyuk = max(free, key=lambda r: r.sector_count)
                        session.create_partition(
                            en_buyuk.start_lba, en_buyuk.sector_count,
                            fs_key=v["fs"], label=v["label"], name=v["label"],
                            progress=lambda m, p: progress(m, 40 + p // 2))
            return session

        ok, result = run_task(self, tr("Sanal disk olusturuluyor"), task)
        if not ok:
            self.error(tr("Sanal disk olusturulamadi"), str(result))
            return
        self._add_session(result)
        self.log(tr("VHD olusturuldu: {} ({})", path, human_size(v['size'])))
        self.refresh()

    # ==================================================================
    # Onyukleme
    # ==================================================================
    def show_bootloader(self) -> None:
        """Onyukleyici yoneticisini acar (acik disk uzerinde calisir)."""
        if not self.session:
            self.error(tr("Disk acik degil"),
                       tr("Onyukleme durumunu incelemek icin once bir disk "
                          "ya da goruntu acin."))
            return
        from .dialogs.bootloader import show_bootloader

        show_bootloader(self, self.session, self.enqueue)

    def show_efi_boot(self) -> None:
        """UEFI onyukleme duzenleyicisini acar.

        Disk acik olmasi gerekmez: bellenim degiskenleri diskte degil,
        anakart uzerindedir.
        """
        from .dialogs.efiboot import show_efi_boot

        show_efi_boot(self)

    def show_system_info(self) -> None:
        satirlar = dict(platform_summary())
        satirlar[tr("Arayuz dili")] = i18n.language_name(i18n.current_language())
        satirlar[tr("Qt platformu")] = QApplication.instance().platformName()
        satirlar[tr("Arayuz stili")] = QApplication.instance().style().objectName()
        if self.session:
            satirlar[tr("Acik dosya bicimi")] = self.session.format_name
        InfoDialog(tr("Sistem Bilgisi"), satirlar, self,
                   note=(tr("FAT12/16/32 ve exFAT saf Python ile desteklenir ve her "
                         "platformda calisir. NTFS ve ext2/3/4 bicimlendirmesi "
                         "sistemdeki mkfs araclarini gerektirir."))).exec_()

    # ==================================================================
    # Yonetici / root yetkisi
    # ==================================================================
    def _offer_elevation(self, blocked) -> None:
        """Acilista, yetki **gercekten eksikse** yeniden baslatmayi onerir.

        Kosulsuz sormak yanlis olurdu: goruntu dosyalari icin yetki gerekmez
        (CLAUDE.md) ve bir disk aracini gereksiz yere tam yetkiyle calistirmak
        riski buyutur. Bu yuzden soru yalnizca **bilgisi okunamayan gercek bir
        disk varken** sorulur ve oturumda bir kez sorulur.
        """
        if self._elevation_asked or is_elevated() or not blocked:
            return
        self._elevation_asked = True
        # Otomatik kosumlarda (duman testi, ekran goruntusu uretimi) modal
        # pencere kosumu kilitlerdi.
        if os.environ.get("DISKULTIMATE_NO_ELEVATION_PROMPT") == "1":
            diagnostics.info("yetki teklifi ortam degiskeniyle bastirildi")
            return
        ok, reason = elevation_available()
        names = ", ".join(d.name for d in blocked[:4])
        if not ok:
            self.log(tr("Yetki eksik ({}) — yukseltme yapilamiyor: {}", names, reason))
            return
        answer = QMessageBox.question(
            self, tr("{} yetkisi gerekiyor", elevation_name()),
            tr("<b>{} fiziksel diskin</b> bilgisi okunamadi "
               "({}).<br><br>Fiziksel disklere erismek icin {} yetkisi "
               "gerekir. Uygulama simdi yetkili olarak yeniden baslatilsin "
               "mi?<br><br><i>Disk goruntusu dosyalari (.img, VHD, VDI...) "
               "icin yetki gerekmez; yalnizca goruntu dosyalariyla "
               "calisacaksaniz <b>Hayir</b> diyebilirsiniz.</i>",
               len(blocked), names, elevation_name()),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if answer == QMessageBox.Yes:
            self.restart_elevated(ask=False)
        else:
            self.log(tr("{} olarak yeniden baslatma reddedildi; fiziksel diskler "
                        "acilamaz", elevation_name()))

    def mark_elevation_asked(self) -> None:
        """Yetki acilista zaten istendi: ayni soru bir daha sorulmasin.

        `main.py` uygulama acilmadan once yetki ister (ADR 0042). Istek
        reddedilirse pencere acilir; disk listesi hazir olunca ayni soruyu
        ikinci kez sormak kullaniciyi bezdirirdi. Disk **acilirken** cikan
        yetki teklifi (`_access_denied`) bundan etkilenmez: orada kullanici
        bir sey yapmaya calismaktadir, teklif yerindedir.
        """
        self._elevation_asked = True

    def _access_denied(self, info, message: str) -> None:
        """Disk yetki yuzunden acilamadi: cozumu de sun.

        Yalnizca hatayi gostermek kullaniciyi tikanik birakir — cozum zaten
        uygulamanin elinde.
        """
        self.log(tr("Disk acilamadi (yetki): {}", info.path))
        ok, reason = elevation_available()
        if not ok:
            self.error(tr("Yetki yetersiz"), f"{message}\n\n{reason}")
            return
        answer = QMessageBox.question(
            self, tr("Yetki yetersiz"),
            tr("{}<br><br>Uygulama <b>{} yetkisiyle</b> yeniden "
               "baslatilsin mi?", message, elevation_name()),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if answer == QMessageBox.Yes:
            self.restart_elevated(ask=False)

    def restart_elevated(self, ask: bool = True) -> None:
        """Uygulamayi yonetici/root olarak yeniden baslatir ve bunu kapatir."""
        ok, reason = elevation_available()
        if not ok:
            QMessageBox.information(self, tr("{} yetkisi", elevation_name()), reason)
            return
        if ask:
            metin = tr("Uygulama kapatilip <b>{} yetkisiyle</b> yeniden "
                       "baslatilacak.<br><br>Devam edilsin mi?",
                       elevation_name())
            if self.sessions:
                metin = (tr("<b>Acik disk/goruntu kapatilacak.</b><br><br>")
                         + metin)
            if QMessageBox.question(
                    self, tr("{} olarak yeniden baslat", elevation_name()), metin,
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No) != QMessageBox.Yes:
                return
        # Aygit tutamaclari once birakilir: yeni surec ayni diski acacak ve
        # iki kopya ayni aygita dokunmamalidir (ADR 0021).
        self._disk_timer.stop()
        self._wait_for_scan()
        self.close_all()
        diagnostics.info(f"{ELEVATION_NAME} olarak yeniden baslatiliyor")
        launch, error = relaunch_elevated()
        if launch is None:
            self._disk_timer.start()
            self.log(tr("Yeniden baslatilamadi: {}", error))
            QMessageBox.warning(self, tr("Yeniden baslatilamadi"), error)
            return
        self._await_elevated(launch)

    def _await_elevated(self, launch) -> None:
        """Yetkili kopya acilana kadar bekler, sonra bu kopyayi kapatir.

        Beklemek zorunludur: Linux'ta `pkexec` yetkilendirmeyi **kendi
        ebeveynine** bakarak yapar. Eski kopya baslatir baslatmaz kapanirsa
        parola penceresi hic acilmaz ve kullanici yalnizca uygulamanin
        kapandigini gorur (ADR 0039). Bekleme arayuzu kilitlemez: sayac
        olayla calisir, pencere olay dongusu doner.
        """
        box = QProgressDialog(
            tr("Yetki penceresi bekleniyor. Parola sorulursa girin."),
            tr("Vazgec"), 0, 0, self)
        box.setWindowTitle(tr("{} olarak yeniden baslat", elevation_name()))
        box.setWindowModality(Qt.WindowModal)
        box.setMinimumDuration(0)
        box.setAutoClose(False)
        box.setAutoReset(False)
        timer = QTimer(self)
        timer.setInterval(250)

        def tick() -> None:
            state, message = launch.poll()
            if state == launch.WAITING:
                if box.wasCanceled():
                    # Yetki penceresi hala acik olabilir; kullanici onu da
                    # kapatmali. Bu kopya calismaya devam eder.
                    timer.stop()
                    launch.cleanup()
                    self._disk_timer.start()
                    self.log(tr("Yetkili kopya beklenmekten vazgecildi; acilirsa "
                                "iki kopyadan birini kapatin."))
                return
            timer.stop()
            box.close()
            if state == launch.STARTED:
                launch.cleanup()
                diagnostics.info("yetkili kopya acildi; bu kopya kapaniyor")
                QApplication.instance().quit()
                return
            launch.cleanup()
            self._disk_timer.start()
            diagnostics.warn(f"yetkili kopya baslatilamadi: {message}")
            self.log(tr("Yeniden baslatilamadi: {}", message))
            QMessageBox.warning(self, tr("Yeniden baslatilamadi"), message)

        timer.timeout.connect(tick)
        timer.start()
        box.show()

    # ==================================================================
    # Plan onizlemesi
    # ==================================================================
    def _compute_plan(self):
        """Kuyruk uygulandiginda ortaya cikacak yerlesimi hesaplar.

        Kuyruk bossa `None` doner — o zaman harita zaten diskteki hali
        gosterir. Hesap bellekte yapilir, diske dokunmaz (`core/planview.py`).
        """
        if self.session is None or self.session.table is None:
            return None
        queue = self.queue
        if queue.is_empty:
            return None
        try:
            return planview.project(self.session, queue)
        except Exception as exc:          # onizleme hicbir zaman engel olmaz
            diagnostics.error("plan onizlemesi hesaplanamadi", exc)
            return None

    def _planned_layout(self):
        """Kuyruk uygulandiginda ortaya cikacak yerlesim (yoksa `None`).

        `_plan_layout` yalnizca tazeleme sirasinda hesaplanir; yeni bir adim
        kurulurken **o anki** kuyruk gecerlidir, bu yuzden gerekirse yeniden
        hesaplanir.
        """
        if self.session is None or self.session.table is None:
            return None
        if self.queue.is_empty:
            return None
        if self._plan_layout is None:
            self._plan_layout = self._compute_plan()
        return self._plan_layout

    def planned_free_regions(self):
        """Bos alanlar — **bekleyen adimlar dusulmus** halde.

        `session.free_regions()` diskteki durumu anlatir; kuyruktaki "yeni
        bolum" adimlari diske yazilmadigi icin orada gorunmez. Yeni bir bolum
        bu listeye gore kurulursa az once planlanan bolumun uzerine denk gelir
        ve uygulama ortasinda "cakisiyor" diye durur (ADR 0034).
        """
        layout = self._planned_layout()
        if layout is not None:
            return layout.free
        return self.session.free_regions()

    def planned_partitions(self):
        """Bolumler — bekleyen adimlar uygulandiktan sonraki hali."""
        layout = self._planned_layout()
        if layout is not None:
            return layout.partitions
        return self.session.partitions

    def _planned_conflict(self, start_lba: int, sector_count: int,
                          ignore_lba: int = -1) -> bool:
        """Aralik planlanan bir bolumu kesiyorsa uyarir ve `True` doner."""
        layout = self._planned_layout()
        if layout is None:
            return False
        clash = planview.overlap_at(layout, start_lba, sector_count,
                                    ignore_lba=ignore_lba)
        if clash is None:
            return False
        self.error(
            tr("Bekleyen adimla cakisiyor"),
            tr("Bu alan <b>{}</b> ile cakisiyor. O bolum henuz diske "
               "yazilmadi ama bekleyen islemler arasinda ve bu alani "
               "tutuyor.<br><br>Once bekleyen adimi kaldirin ya da baska bir "
               "alan secin.", clash.display_name))
        return True

    @staticmethod
    def _scheme_name(scheme: str) -> str:
        return {"mbr": "MBR", "gpt": "GPT"}.get(scheme, scheme.upper())

    def _refresh_plan_views(self):
        """Harita ve tabloyu (varsa) planlanan yerlesime gore cizer.

        Gosterilen (bolumler, bos alanlar) ciftini dondurur; cagiran taraf
        secimi bunun uzerinden tazeler.
        """
        session = self.session
        if session is None:
            return [], []
        self._plan_layout = self._compute_plan()
        plan = self._plan_layout if self._show_plan else None
        partitions = plan.partitions if plan else session.partitions
        free = plan.free if plan else session.free_regions()
        self.osinfo.annotate(session, partitions)
        sema = (session.scheme_name if plan is None
                else self._scheme_name(plan.scheme))
        self.disk_map.set_disk(
            f"{session.name} — {human_size(session.image.size)} — {sema}",
            session.image.sector_count, partitions, free)
        self.part_table.set_partitions(partitions, free)
        self._update_plan_bar()
        if self.selected_partition is not None:
            self.disk_map.select_partition(self.selected_partition)
            self.part_table.select_partition(self.selected_partition)
        elif self.selected_free is not None and free:
            # Secili bos alan kuyruk degisince kuculmus ya da bolunmus
            # olabilir; secim eski baslangicta kalirsa "yeni bolum" yanlis
            # yere kurulur (ADR 0034). Eski baslangici kapsayan bolgeye,
            # yoksa en buyuguene gecilir.
            start = self.selected_free[0]
            match = next((r for r in free if r.start_lba <= start <= r.end_lba),
                         max(free, key=lambda r: r.sector_count))
            self.selected_free = (match.start_lba, match.sector_count)
            self.disk_map.select_free(match.start_lba)
            self.part_table.select_free(match.start_lba)
        # Kuyruk degisti: tutamaklar planlanan yerlesime gore yeniden
        self._refresh_edit_layout()
        return partitions, free

    def _update_plan_bar(self) -> None:
        """Plan seridini tazeler (kuyruk bossa gizlenir)."""
        plan = self._plan_layout
        if plan is None:
            self.plan_bar.setVisible(False)
            return
        self.plan_bar.setVisible(True)
        adet = len(self.queue)
        if self._show_plan:
            self.plan_label.setText(
                tr("<b>Planlanan yerlesim</b> gosteriliyor — {} bekleyen adim "
                   "uygulandiginda disk boyle olacak ({}). Diske henuz "
                   "yazilmadi.", adet, planview.summary(plan)))
            self.plan_toggle.setText(tr("Diskteki hali goster"))
        else:
            self.plan_label.setText(
                tr("<b>Diskteki hali</b> gosteriliyor — {} bekleyen adim "
                   "listede bekliyor.", adet))
            self.plan_toggle.setText(tr("Planlanani goster"))

    def toggle_plan_preview(self) -> None:
        """Planlanan yerlesim ile diskteki hali arasinda gecis yapar."""
        self._show_plan = not self._show_plan
        self.refresh(reload=False)

    def _planned_partition(self, index: int):
        """Onizlemedeki bolum (yalnizca planda varsa)."""
        plan = self._plan_layout
        if plan is None or not self._show_plan:
            return None
        for p in plan.partitions:
            if p.index == index:
                return p
        return None

    # ==================================================================
    # Bekleyen islem kuyrugu
    # ==================================================================
    @property
    def queue(self) -> ops.OperationQueue:
        """Etkin oturumun kuyrugu (yoksa olusturulur)."""
        key = id(self.session) if self.session is not None else 0
        if key not in self._queues:
            self._queues[key] = ops.OperationQueue()
        return self._queues[key]

    def enqueue(self, operation) -> None:
        """Adimi kuyruga koyar. **Diske hicbir sey yazilmaz.**"""
        self.queue.add(operation)
        self.log(tr("Kuyruga eklendi: {}", operation))
        self._refresh_pending()

    def _refresh_pending(self) -> None:
        """Bekleyen islem listesini ve arac cubugunu tazeler."""
        self.pending_view.clear()
        queue = self.queue
        for i, operation in enumerate(queue, 1):
            item = QTreeWidgetItem(self.pending_view,
                                   [f"{i}. {operation.title}", operation.target])
            item.setIcon(0, app_icon(operation.icon))
            tip = operation.detail or operation.title
            if operation.destructive:
                item.setForeground(0, QColor("#c0392b"))
                tip += tr("\n(Bu adim veri kaybettirebilir)")
            item.setToolTip(0, tip)
        # Tabloda da isaretle: kullanici neyin degisecegini listenin disinda da
        # gormeli.
        marks = {}
        for operation in queue:
            index = operation.params.get("index")
            if isinstance(index, int) and index >= 0:
                marks.setdefault(index, []).append(str(operation))
        # Harita/tablo planlanan yerlesime gore yeniden cizilir; ana ekran
        # kuyruktaki degisikligi **hemen** gosterir (ADR 0031).
        if self.session is not None and self.session.table is not None:
            self._refresh_plan_views()
        self.part_table.set_pending(marks)
        count = len(queue)
        self.pending_view.setHeaderLabels(
            [tr("Bekleyen islemler ({})", count) if count
             else tr("Bekleyen islem yok"), tr("Hedef")])
        self.act_apply.setText(tr("Uygula ({})", count) if count
                               else tr("Uygula"))
        self._update_actions()

    def _confirm_drop_queue(self) -> bool:
        """Kaynak kapatilirken bekleyen adimlar varsa uyarir.

        Adimlar oturuma baglidir (bolum numaralari o oturuma aittir), bu yuzden
        kapaninca **kaybolurlar**. Sessizce silmek kullanicinin yaptigi plani
        gorunmez bicimde yok etmek olurdu.
        """
        return QMessageBox.question(
            self, tr("Bekleyen islemler var"),
            tr("<b>{} bekleyen adim</b> henuz uygulanmadi ve kaynak "
               "kapatilinca kaybolacak.<br><br>Diskte hicbir degisiklik "
               "yapilmadi.<br><br>Yine de kapatilsin mi?", len(self.queue)),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No) == QMessageBox.Yes

    def _pending_context(self, pos) -> None:
        item = self.pending_view.itemAt(pos)
        menu = QMenu(self)
        if item is not None:
            row = self.pending_view.indexOfTopLevelItem(item)
            menu.addAction(tr("Bu adimi kaldir"),
                           lambda: self._drop_step(row))
            menu.addSeparator()
            menu.addAction(tr("Yukari tasi"), lambda: self._move_step(row, row - 1))
            menu.addAction(tr("Asagi tasi"), lambda: self._move_step(row, row + 1))
            menu.addSeparator()
        menu.addAction(self.act_apply)
        menu.addAction(self.act_discard)
        menu.exec_(self.pending_view.viewport().mapToGlobal(pos))

    def _drop_step(self, row: int) -> None:
        try:
            operation = self.queue.remove(row)
        except IndexError:
            return
        self.log(tr("Kuyruktan cikarildi: {}", operation.title))
        self._refresh_pending()

    def _move_step(self, row: int, target: int) -> None:
        if not (0 <= target < len(self.queue)):
            return
        self.queue.move(row, target)
        self._refresh_pending()
        self.pending_view.setCurrentItem(self.pending_view.topLevelItem(target))

    def undo_step(self) -> None:
        operation = self.queue.undo()
        if operation is None:
            return
        self.log(tr("Geri alindi: {}", operation.title))
        self._refresh_pending()

    def discard_steps(self) -> None:
        if self.queue.is_empty:
            return
        if QMessageBox.question(
                self, tr("Bekleyen islemleri iptal et"),
                tr("{} bekleyen adim silinecek.<br><br>Diskte hicbir "
                   "degisiklik yapilmadigi icin bu islem "
                   "<b>zararsizdir</b>.<br><br>Devam edilsin mi?", len(self.queue)),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes) != QMessageBox.Yes:
            return
        count = self.queue.clear()
        self.log(trn("{} bekleyen adim iptal edildi",
                     "{} bekleyen adim iptal edildi", count, count))
        self._refresh_pending()

    def apply_steps(self) -> None:
        """Bekleyen adimlari **tek onayla** sirayla uygular.

        Yikici islem onayi buradadir (CLAUDE.md): islem basina degil, parti
        basina. Kullanici tam listeyi gorur — bu, bes ayri uyariyi tek tek
        onaylatmaktan daha bilgilendiricidir.
        """
        if self.session is None or self.queue.is_empty:
            return
        queue = self.queue
        can_write, reason = self.session.can_become_writable()
        if not can_write:
            self.error(tr("Yazilamaz kaynak"), reason)
            return
        # Onay, ilerleme ve sonuc **tek pencerededir**: kullanici adimlari
        # sirayla gorur ve hangisinde durdugunu okur (ADR 0031).
        ran, outcome = run_apply(self, self.session, queue,
                                 prepare=self._make_writable)
        if not ran:
            if outcome:
                self.log(tr("Uygulama basarisiz: {}", outcome))
            self.refresh()
            self._refresh_pending()
            return
        result = outcome
        for operation in result.done:
            self.log(tr("Uygulandi: {}", operation.title))
        if result.ok:
            self.log(result.summary())
        else:
            self.log(tr("DIKKAT: {}", result.summary()))
        self.refresh()
        self._refresh_pending()

    def _make_writable(self) -> bool:
        """Kaynagi yazma moduna alir; fiziksel diskte tum kapilardan gecer."""
        session = self.session
        # Arka plandaki isletim sistemi taramasi ayni tutamactan okur;
        # yazma baslamadan biter (ADR 0089).
        self.osinfo.wait(session)
        if not session.readonly:
            return True
        allow_system = False
        if session.is_physical:
            info = session.disk_info
            if info is not None and info.is_system:
                name, ok = QInputDialog.getText(
                    self, tr("Sistem diski onayi"),
                    tr("{} ISLETIM SISTEMI DISKIDIR.\n\n"
                       "Bu diske yazmak isletim sistemini acilamaz hale "
                       "getirebilir.\nDevam etmek icin disk adini yazin: {}",
                       info.path, info.name))
                if not ok or name.strip() != info.name:
                    self.log(tr("Sistem diski onayi verilmedi, uygulama iptal"))
                    return False
                allow_system = True
            elif info is not None and info.mounted:
                # Uyari metni platformun GERCEGINI soylemeli: Windows birimi
                # kilitleyip ayirir, Linux/macOS ayirmaz. Eskiden her yerde
                # "gecici olarak cikarilacak" yaziyordu; Linux'ta bu yanlisti
                # ve bagli bir dosya sistemini bozma riskini gizliyordu.
                if locks_volumes_on_write():
                    note = tr("Yazma sirasinda bu birimler <b>gecici olarak "
                              "cikarilacak</b> (kilitlenip ayrilir).")
                else:
                    note = tr("Bu bolumler <b>hala bagli</b>. Isletim sistemi "
                              "onlari kullanirken ham sektorlere yazmak dosya "
                              "sistemini <b>bozabilir</b>.<br><br>"
                              "Once bu bolumleri cikarmaniz (unmount) "
                              "onerilir.")
                # Uyarinin yaninda cozum de durur: kullaniciyi baska bir
                # pencereye gonderip "once cikar" demek, ayni bilgiyle iki
                # kez ugrasmasi demektir (ADR 0043).
                box = QMessageBox(self)
                box.setIcon(QMessageBox.Warning)
                box.setWindowTitle(tr("Bagli bolum uyarisi"))
                box.setText(tr("Bu diskte bagli bolumler "
                               "var:<br><b>{}</b><br><br>{}<br><br>Devam "
                               "edilsin mi?", ', '.join(info.mounted), note))
                evet = box.addButton(QMessageBox.Yes)
                hayir = box.addButton(QMessageBox.No)
                kes = (box.addButton(tr("Baglantilari kes"),
                                     QMessageBox.ActionRole)
                       if mount_supported()[0] else None)
                box.setDefaultButton(hayir)
                box.exec_()
                secilen = box.clickedButton()
                if kes is not None and secilen is kes:
                    if not self._unmount_all(info):
                        return False
                elif secilen is not evet:
                    return False
        try:
            self._wait_for_scan()
            session.become_writable(confirm=True, allow_system=allow_system)
        except AccessDeniedError as exc:
            info = session.disk_info
            if info is not None:
                self._access_denied(info, str(exc))
            else:
                self.error(tr("Yetki yetersiz"), str(exc))
            return False
        except Exception as exc:
            self.error(tr("Yazma modu acilamadi"), str(exc))
            return False
        self.log(tr("Yazma modu acildi: {}", session.name))
        return True

    # ==================================================================
    # Tanilama
    # ==================================================================
    def show_diagnostics(self) -> None:
        """Tanilama durumunu gosterir: gunluk yolu, donmalar, acik islemler."""
        state = diagnostics.status()
        if not state["enabled"]:
            QMessageBox.information(
                self, tr("Tanilama kapali"),
                tr("Tanilama DISKULTIMATE_DIAG=0 ile kapatilmis."))
            return
        worst = state["worst_stall_ms"] / 1000.0
        acik = state["open_spans"]
        satirlar = {
            tr("Oturum gunlugu"): state["log"],
            tr("Cokme gunlugu"): state["crash_log"] or "-",
            tr("Donma esigi"): tr("{} sn",
                                  f"{state['stall_threshold_ms'] / 1000.0:.1f}"),
            tr("Yakalanan donma"): str(state["stalls"]),
            tr("En uzun donma"): tr("{} sn", f"{worst:.1f}") if worst else "-",
            tr("Son rapor"): state["report"] or tr("(yok)"),
            tr("Calisma suresi"): tr("{} dk", f"{state['uptime_s'] / 60.0:.1f}"),
            tr("Su an calisan"): acik[0] if acik else tr("(bos)"),
        }
        InfoDialog(tr("Tanilama"), satirlar, self,
                   note=(tr("Arayuz bir saniyeden uzun yanit vermezse butun is "
                         "parcaciklarinin yigini kendiliginden rapor dosyasina "
                         "yazilir. Raporlar gunluk klasorundeki freeze/ "
                         "altindadir."))).exec_()

    def open_log_folder(self) -> None:
        """Gunluk klasorunu isletim sisteminin dosya yoneticisinde acar."""
        folder = diagnostics.log_dir() or log_root()
        if not open_folder(folder):
            QMessageBox.information(self, tr("Gunluk klasoru"), folder)

    def show_freeze_report(self) -> None:
        """En son donma raporunu metin olarak gosterir."""
        path = diagnostics.last_report()
        if not path or not os.path.isfile(path):
            QMessageBox.information(
                self, tr("Donma raporu yok"),
                tr("Bu makinede kayitli donma raporu bulunamadi.\n\n"
                "Arayuz bir saniyeden uzun takilirsa rapor kendiliginden "
                "olusur."))
            return
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        TextViewDialog(
            tr("Donma raporu — {}", os.path.basename(path)), text, self,
            note=(tr("Arayuzun takildigi andaki yigin. En ustteki 'O an acik "
                  "islem' satiri hangi islemin bekledigini soyler."))).exec_()

    def dump_stacks(self) -> None:
        """Anlik yigin dokumu alir (uygulama takiliyken elle kanit toplamak icin)."""
        path = diagnostics.dump_now("kullanici istegi")
        self.log(tr("Yigin dokumu yazildi: {}", path))
        QMessageBox.information(self, tr("Yigin dokumu"), path)

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
        """Disk listesini ve bolum ozetlerini yeniden tarar (arka planda)."""
        self.log(tr("Fiziksel disk listesi taraniyor..."))
        self._last_disk_signature = ()      # sonuc ayni olsa da agac tazelensin
        self._surveys.clear()               # bolum tablolari yeniden okunsun
        self.start_disk_scan()

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
            QMessageBox.information(self, tr("Disk secili degil"),
                                    tr("Agactan bir fiziksel disk secin."))
            return
        satirlar = [tr("FIZIKSEL DISK — {}", info.name), "=" * 52]
        for key, value in info.summary().items():
            satirlar.append(f"{key:<16}: {value}")
        if info.partitions:
            satirlar += ["", tr("BOLUM AYGITLARI"), "-" * 52]
            satirlar += [f"  {b}" for b in info.partitions]
        satirlar += ["", info.risk_text]
        if not DiskSession.has_disk_privileges():
            satirlar += ["", tr("UYARI: Uygulama yonetici/root yetkisi olmadan "
                                "calisiyor; disk icerigi okunamayabilir.")]
        self.info_view.setPlainText("\n".join(satirlar))
        self.tabs.setCurrentIndex(TAB_INFO)
        self.status_sel.setText(tr("Secili: {} ({})", info.name, human_size(info.size)))
        self._update_actions()

    def _physical_summary_text(self) -> str:
        diskler = list(getattr(self, "_physical_cache", {}).values())
        satirlar = [tr("SISTEMDEKI DISKLER"), "=" * 52,
                    tr("Yonetici/root yetkisi: {}",
                       tr("VAR") if DiskSession.has_disk_privileges()
                       else tr("YOK")), ""]
        for d in diskler:
            isaret = {"sistem": "[!]", "bagli": "[*]",
                      "bilinmiyor": "[?]", "normal": "   "}[d.risk_level]
            satirlar.append(f"{isaret} {d.name:<12} {human_size(d.size):>10}  "
                            f"{d.model[:30]}")
            satirlar.append(f"      {d.risk_text}")
        if not diskler:
            satirlar.append(tr("  (disk bulunamadi)"))
        satirlar += ["", tr("Bir diski acmak icin uzerine cift tiklayin."),
                     tr("Diskler varsayilan olarak SALT OKUNUR acilir.")]
        return "\n".join(satirlar)

    def _close_session(self, session) -> None:
        """Verilen oturumu kapatir (agacta sag tiklanan disk/goruntu)."""
        if session is None:
            return
        self.session = session
        self.close_image()

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
        """Secili fiziksel diski **salt okunur** acar.

        `write` artik yok sayilir ve geriye donuk uyum icin durur. Yazma modu
        secimi kaldirildi (ADR 0025): disk her zaman salt okunur acilir,
        yazma yetkisi yalnizca bekleyen islemler uygulanirken alinir. Boylece
        "hicbir sey yapmadim ama disk yazilabilir acik" durumu — ve onunla
        gelen birim kilitleri — ortadan kalkar.
        """
        path = path or self._selected_disk_path()
        info = getattr(self, "_physical_cache", {}).get(path or "")
        if info is None:
            QMessageBox.information(self, tr("Disk secili degil"),
                                    tr("Agactan bir fiziksel disk secin."))
            return

        mevcut = self._find_open(info.path)
        if mevcut is not None:
            self.session = mevcut
            self.session.close()
            self.sessions.remove(mevcut)
        # Acma anindaki yaris: o sirada suren bir yoklama ayni aygita tutamac
        # acmis olabilir. Kutuk bundan sonraki turlari zaten durdurur; bu
        # bekleme, ACMA aninda cakismayi kapatir.
        self._wait_for_scan()
        try:
            self._add_session(DiskSession.open_physical(info, readonly=True))
        except SystemDiskError as exc:
            self.error(tr("Sistem diski korumasi"), str(exc))
            return
        except AccessDeniedError as exc:
            self._access_denied(info, str(exc))
            return
        except PhysicalDiskError as exc:
            self.error(tr("Disk acilamadi"), str(exc))
            return
        except Exception as exc:
            self.error(tr("Disk acilamadi"), str(exc))
            return
        self.log(tr("Fiziksel disk acildi (salt okunur): {} — {}, {}",
                    info.path, human_size(info.size),
                    self.session.scheme_name))
        self.refresh(reload=False)

    # ==================================================================
    # Secim ve tazeleme
    # ==================================================================
    @diagnostics.timed("ui.select_partition")
    def select_partition(self, index: int) -> None:
        if not self.session or not self.session.table:
            return
        planlanan = None
        try:
            part = self.session.table.get(index)
        except Exception:
            # Bolum diskte yok: yalnizca planda olabilir (kuyrukta "yeni
            # bolum" adimi). Secilebilir ve incelenebilir ama uzerinde islem
            # yapilamaz — henuz var degildir.
            planlanan = self._planned_partition(index)
            if planlanan is None:
                return
            part = planlanan
        else:
            shown_parts = self._planned_partition(index)
            if shown_parts is not None:
                part = shown_parts        # boyut/etiket planlanan haliyle
        self.selected_is_planned = planlanan is not None
        self.selected_partition = index
        self.selected_free = None
        self.disk_map.select_partition(index)
        self.part_table.select_partition(index)
        self._select_tree(("part", index))
        self._refresh_edit_layout()      # tutamaklar (ortak model)
        self.status_sel.setText(
            tr("Secili: Bolum {} — {} ({})",
               index, part.display_name, human_size(part.size)))
        self._show_partition_info(part)
        if self.selected_is_planned:
            # Diskte karsiligi yok: icerik gosterilemez, gosteriliyormus gibi
            # de yapilmaz.
            self.browser.set_filesystem(None)
            self.hex_view.set_device(self.session.image, tr("Tum goruntu"))
            self.status_sel.setText(
                tr("Secili: Bolum {} — {} ({}) — planlanan, henuz olusturulmadi",
                   index, part.display_name, human_size(part.size)))
            self._update_actions()
            return
        fs = None
        try:
            fs = self.session.filesystem(index)
        except Exception as exc:
            self.log(tr("Dosya sistemi acilamadi: {}", exc))
        self.browser.set_filesystem(
            fs, tr("Bolum {} ({})", index, fs_display(part.fs_type) or tr("ham")))
        self.hex_view.set_device(self.session.view(part),
                                 tr("Bolum {} — {}", index, part.display_name))
        self._update_actions()

    @diagnostics.timed("ui.select_free")
    def select_free(self, start_lba: int, sector_count: int) -> None:
        self.selected_partition = None
        self.selected_is_planned = False
        self.selected_free = (start_lba, sector_count)
        self.disk_map.select_free(start_lba)
        self.part_table.select_free(start_lba)
        self._select_tree(("free", start_lba))
        self._edit_layout = None
        self.disk_map.set_edit_layout(None)
        size = sector_count * (self.session.image.sector_size if self.session else 512)
        self.status_sel.setText(tr("Secili: Bos alan — {}", human_size(size)))
        self.info_view.setPlainText(
            tr("Bolumlenmemis alan\n{}\nBaslangic LBA   : {}\nSektor sayisi   "
               ": {}\nBoyut           : {}\n\nBu alanda yeni bolum "
               "olusturabilirsiniz (Bolum > Yeni bolum).",
               '-' * 40, start_lba, sector_count, human_size(size)))
        self.browser.set_filesystem(None)
        if self.session:
            self.hex_view.set_device(self.session.image, tr("Tum goruntu"))
        self._update_actions()

    @diagnostics.timed("ui.refresh")
    def refresh(self, reload: bool = True) -> None:
        """Arayuzu etkin kaynaga gore tazeler.

        `reload=False`: kaynak **az once** acildi ya da okundu, bolum tablosu
        ve dosya sistemi bilgisi tazedir. Yeniden okumak bedava degildir —
        olculdu: 14.7 GB FAT32 bolumun tespiti tek basina ~590 ms surer ve
        7.7 MB okur (FAT tablosunun tamami). Disk acilisinda bu is uc kez
        yapiliyordu ve arayuz 1.5 saniye takiliyordu (tanilama yakaladi).
        """
        if not self.session:
            self._update_actions()
            return
        if reload:
            # Disk yeniden okundu: dosya sistemi dolulugu degismis olabilir,
            # eski boyutlandirma sinirlari kullanilmaz.
            self.limits.clear()
            self.osinfo.forget(self.session)
            try:
                self.session.reload()
            except Exception as exc:
                self.error(tr("Yenileme hatasi"), str(exc))
                return
        session = self.session
        partitions = session.partitions
        free = session.free_regions()
        self.map_stack.setCurrentIndex(1)
        self.map_stack.setMinimumHeight(self.disk_map.minimumHeight())
        # Bekleyen adimlar varsa harita ve tablo **planlanan** yerlesimi
        # gosterir: kullanici Uygula demeden sonucu gorur (ADR 0031).
        # Agac diskteki gercek duruma bagli kalir.
        shown_parts, shown_free = self._refresh_plan_views()
        self._build_tree(partitions)
        self.setWindowTitle(f"{session.name} — {APP_NAME} {APP_VERSION}")
        self.status_file.setText(session.path)
        state = tr("{} | {} | {} bolum", session.scheme_name,
                   human_size(session.image.size), len(partitions))
        # Salt okunurluk artik **normal** kiptir: her kaynak boyle acilir ve
        # yazma yalnizca Uygula aninda olur. Bu yuzden kilit isareti yalnizca
        # gercekten degistirilemeyen kaynaklarda gosterilir (ADR 0025).
        can_write, why_not = session.can_become_writable()
        if not can_write:
            state += "  |  🔒 " + tr("DEGISTIRILEMEZ")
            self.status_scheme.setToolTip(why_not)
        else:
            self.status_scheme.setToolTip(
                tr("Degisiklikler bekleyen islem olarak birikir; diske ancak "
                "Uygula ile yazilir."))
        self.status_scheme.setText(state)

        if self.selected_partition is not None and \
                any(p.index == self.selected_partition for p in shown_parts):
            self.select_partition(self.selected_partition)
        elif shown_parts:
            self.select_partition(shown_parts[0].index)
        elif shown_free:
            self.select_free(shown_free[0].start_lba,
                             shown_free[0].sector_count)
        else:
            self.status_sel.setText("")
            self.browser.set_filesystem(None)
            self.hex_view.set_device(session.image, tr("Tum goruntu"))
            self.info_view.setPlainText(self._disk_summary_text())
        self._refresh_pending()

    def _browser_write_access(self):
        """Dosya gezgini yazmak istedi: kaynagi yazma moduna alir.

        Taze `FileSystemAccess` dondurur; kaynak yeniden acildigi icin
        gezginin elindeki eski nesne gecersizdir. Basarisizlikta `None`.

        Dosya islemleri kuyruga girmez (ADR 0025), bu yuzden yetki burada
        istenir — yoksa fiziksel diske dosya eklemek hic mumkun olmazdi.
        """
        index = self.selected_partition
        if self.session is None or index is None:
            return None
        if self.session.readonly:
            can_write, reason = self.session.can_become_writable()
            if not can_write:
                self.error(tr("Yazilamaz kaynak"), reason)
                return None
            if not self._make_writable():
                return None
            self.refresh()
            self.select_partition(index)
        try:
            return self.session.filesystem(index)
        except Exception as exc:
            self.error(tr("Dosya sistemi acilamadi"), str(exc))
            return None

    def _content_changed(self) -> None:
        """Dosya gezgininde degisiklik olunca doluluk gostergelerini tazele."""
        index = self.selected_partition
        if index is None or not self.session:
            return
        # Doluluk degisti: en az boyut yeniden hesaplanmali (ADR 0074)
        self.limits.forget(self.session, index)
        try:
            self.session._fs_info.pop(index, None)
            part = self.session.table.get(index)
            self.limits.get(self.session, part)          # arka planda yeniden
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
    @diagnostics.timed("ui.build_tree")
    def _build_tree(self, partitions=None, disks=None) -> None:
        """Agaci kurar: fiziksel diskler, sonra acik GORUNTU dosyalari.

        Acilan bir fiziksel disk icin **ayri bir dal olusturulmaz**. Eskiden
        disk acildiginda listede "(asagida acik)" yazip bolumleri agacin
        altinda ikinci bir dala tasiyordu; kullanici ayni diski iki yerde
        goruyor ve islemleri asagida yapmak zorunda kaliyordu. Artik disk
        kendi satirinda kalir ve bolumleri dogrudan orada islenir (ADR 0026).

        Goruntu dosyalari (.img, VHD, .dub) fiziksel disk listesinde yer
        almadiklari icin kendi dallarinda gosterilmeye devam eder.
        """
        self.tree.clear()
        self._add_physical_disks(disks)
        for session in self.sessions:
            if session.is_physical:
                continue        # kendi disk satirinda gosteriliyor
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

    def start_disk_scan(self, quiet: bool = False) -> None:
        """Disk listesini **arka planda** tarar.

        Tarama arayuz is parcaciginda yapilmaz: suresi isletim sistemine
        baglidir ve Windows'ta bos kart yuvasi gibi durumlarda onlarca saniye
        surebilir. O sure boyunca pencere donardi.

        Ayni anda yalnizca bir tarama calisir; bitmeyen bir tarama varken
        yoklama turu atlanir.
        """
        if self._scan_running():
            diagnostics.debug("disk taramasi hala suruyor; bu tur atlandi")
            return
        known = {d.path: disk_signature(d)
                 for d in self._physical_cache.values()}
        scanner = DiskScanner(known, self)
        scanner.ready.connect(self._disks_scanned)
        if not quiet:
            scanner.failed.connect(
                lambda message: self.log(tr("Disk listesi alinamadi: {}", message)))
        # Biten tarayici hem silinir hem de **isaretci birakilmaz**: yalnizca
        # `deleteLater` cagrilsaydi `self._scanner` yok edilmis bir C++ nesnesini
        # gosterirdi ve sonraki tur `RuntimeError` ile duserdi.
        scanner.finished.connect(lambda: self._scan_finished(scanner))
        self._scanner = scanner
        scanner.start()

    def _scan_running(self) -> bool:
        """Calisan bir tarama var mi? (yok edilmis nesneye dayanikli)"""
        if self._scanner is None:
            return False
        try:
            return self._scanner.isRunning()
        except RuntimeError:        # C++ tarafi silinmis
            self._scanner = None
            return False

    def _scan_finished(self, scanner) -> None:
        if self._scanner is scanner:
            self._scanner = None
        scanner.deleteLater()

    def _wait_for_scan(self, timeout_ms: int = 10000) -> None:
        """Suren disk taramasinin bitmesini bekler."""
        if self._scan_running():
            diagnostics.info("acma oncesi suren disk taramasi bekleniyor")
            self._scanner.wait(timeout_ms)

    def _disks_scanned(self, disks, surveys=None) -> None:
        """Arka plan taramasinin sonucu — arayuz is parcaciginda calisir."""
        # Yeni okunan bolum ozetleri saklanir; okunmayanlar icin eski ozet
        # gecerli kalir (disk degismediyse yeniden okunmasina gerek yok).
        for path, survey in (surveys or {}).items():
            self._surveys[path] = survey
        signature = self._disk_signature(disks)
        first = not self._disk_scan_done
        self._disk_scan_done = True
        if signature == self._last_disk_signature and not first:
            return
        before = {s[0] for s in self._last_disk_signature}
        now = {s[0] for s in signature}
        # Cikarilan aygitin **adi** yalnizca eski listede var; agac yeniden
        # kurulmadan once saklanir, yoksa gunlukte ham aygit yolu gorunur.
        old_names = {d.path: d.name for d in self._physical_cache.values()}
        self._last_disk_signature = signature
        by_path = {d.path: d for d in disks}
        # Ilk taramada her disk "yeni" gorunur; takma/cikarma yalnizca sonraki
        # turlarda bildirilir.
        if not first:
            for path in sorted(now - before):
                d = by_path[path]
                self.log(tr("Aygit takildi: {} — {} ({})",
                            d.name, d.model or 'bilinmeyen',
                            human_size(d.size)))
            for path in sorted(before - now):
                self.log(tr("Aygit cikarildi: {}", old_names.get(path, path)))
        self._build_tree(self.session.partitions if self.session else [],
                         disks=disks)
        self._refresh_overview()
        if first and self.session is None:
            self.info_view.setPlainText(self._physical_summary_text())
            self.log(tr("Fiziksel disk listesi hazir: {} disk", len(disks)))
            if not is_elevated():
                self.log(tr("Yetki: normal kullanici — fiziksel disk icin {} gerekir",
                            elevation_name()))
            # Ekran bos baslamaz: ilk okunabilir disk secilip **salt okunur**
            # acilir. Cizim once bitsin diye siraya alinir (ADR 0035).
            QTimer.singleShot(0, lambda: self._autoselect_disk(disks))
        self._update_actions()
        # Yetki teklifi ilk taramadan SONRA yapilir: ancak o zaman hangi
        # diskin bilgisinin okunamadigi bilinir.
        if first:
            self._offer_elevation([d for d in disks if not d.info_complete])

    @staticmethod
    def first_openable_disk(disks, surveys):
        """Acilista acilacak disk — yoksa `None`.

        Olcut **acilabilirlik**tir, buyukluk ya da tur degil: bilgisi eksik
        okunan (yetki yok) ya da bolum tablosu okunamamis bir diski acmak
        kullaniciya acilista bir hata penceresi gosterirdi. Ilk uygun disk
        secilir; listedeki sira isletim sisteminin sirasidir (sda, sdb...).
        """
        for info in disks or []:
            if not getattr(info, "info_complete", False):
                continue
            survey = (surveys or {}).get(info.path)
            if survey is None or getattr(survey, "error", ""):
                continue
            return info
        return None

    def _autoselect_disk(self, disks) -> None:
        """Acilista ilk uygun diski secer ve salt okunur acar.

        Yalnizca **bir kez** calisir: kullanici diski kapattiginda pencere
        onu yeniden acmaya kalkmaz. Basarisizlik sessizdir — acilista hata
        penceresi cikarmak, kullanicinin istemedigi bir isin bedelidir.
        """
        if self._auto_opened or self.session is not None:
            return
        self._auto_opened = True
        info = self.first_openable_disk(disks, self._surveys)
        if info is None:
            return
        try:
            self._wait_for_scan()
            self._add_session(DiskSession.open_physical(info, readonly=True))
        except Exception as exc:                       # noqa: BLE001
            diagnostics.info(f"acilista disk acilamadi: {info.path}: {exc}")
            return
        self.log(tr("Fiziksel disk acildi (salt okunur): {} — {}, {}",
                    info.path, human_size(info.size), self.session.scheme_name))
        self.refresh(reload=False)

    def _add_session_node(self, session: DiskSession) -> None:
        """Bir acik goruntu/disk icin agac dali olusturur."""
        etkin = session is self.session
        title = (f"{session.name} — {human_size(session.image.size)} — "
                  f"{session.scheme_name}")
        # Salt okunurluk normal kiptir (ADR 0025); agacta yalnizca **hic**
        # degistirilemeyen kaynak isaretlenir, yoksa her fiziksel diskte
        # gereksiz bir uyari gorunurdu.
        if not session.can_become_writable()[0]:
            title += "  " + tr("[degistirilemez]")
        root = QTreeWidgetItem(self.tree, [title])
        root.setIcon(0, app_icon("backup" if session.is_backup else
                                 ("disk" if session.is_physical else "image"), 16))
        root.setData(0, Qt.UserRole, ("session", id(session)))
        root.setToolTip(0, f"{session.path or session.name}\n{session.format_name}")
        f = root.font(0)
        f.setBold(etkin)            # etkin oturum kalin gosterilir
        root.setFont(0, f)

        self._add_session_partitions(root, session)
        root.setExpanded(etkin)

    def _add_session_partitions(self, parent: QTreeWidgetItem,
                                session: DiskSession) -> None:
        """Acik bir oturumun bolumlerini ve bos alanlarini dugume yazar.

        Hem goruntu dosyasi dali hem de **fiziksel diskin kendi satiri** bunu
        kullanir; boylece iki yerde iki farkli liste olusmaz.
        """
        marks = self._queue_marks(session)
        # Bolumde kurulu sistem (arka planda bulunur, ADR 0089): amblem
        # bolumun basinda, adi metnin sonunda. Bulunamayan bolum dosya
        # sistemi rengindeki kareyle kalir.
        self.osinfo.annotate(session, session.partitions)
        for p in session.partitions:
            metin = tr("Bolum {}: {}", p.index, p.display_name)
            if p.os_name:
                metin += "  —  " + p.os_name
            if p.index in marks:
                metin += "  ⏳"
            item = QTreeWidgetItem(parent, [metin])
            item.setIcon(0, os_icon(p.os_kind, 16) if p.os_kind in OS_LOGOS
                         else color_chip(fs_color(p.fs_type), 12))
            item.setData(0, Qt.UserRole, ("part", (id(session), p.index)))
            tip = tr("{} — {}", fs_display(p.fs_type) or tr("Bicimlendirilmemis"),
                     human_size(p.size))
            if p.os_name:
                tip += "\n" + tr("Isletim sistemi: {}", p.os_name)
            if p.index in marks:
                tip += ("\n\n" + tr("Bekleyen islemler:") + "\n• "
                        + "\n• ".join(marks[p.index]))
            item.setToolTip(0, tip)
        for r in session.free_regions():
            item = QTreeWidgetItem(parent, [tr("Bos alan ({})", human_size(r.size))])
            item.setForeground(0, palette_color(self.tree, "dim"))
            item.setData(0, Qt.UserRole, ("free", (id(session), r.start_lba)))
        parent.setExpanded(True)

    def _queue_marks(self, session) -> dict:
        """{bolum numarasi: [adim metni]} — o oturumun bekleyen adimlari."""
        queue = self._queues.get(id(session))
        if queue is None:
            return {}
        marks = {}
        for operation in queue:
            index = operation.params.get("index")
            if isinstance(index, int) and index >= 0:
                marks.setdefault(index, []).append(str(operation))
        return marks

    def _session_by_id(self, session_id: int) -> Optional[DiskSession]:
        for s in self.sessions:
            if id(s) == session_id:
                return s
        return None

    def _add_physical_disks(self, disks=None) -> None:
        """Agacin ustune sistemdeki fiziksel diskleri ekler (veri okumadan).

        Burada **tarama yapilmaz**: liste ya cagiran tarafindan verilir (arka
        plan taramasinin sonucu) ya da son taramanin onbellekinden gelir. Agac
        her yeniden kuruldugunda isletim sistemine gidilseydi, aygit
        yoklamasinin maliyeti arayuz is parcaciginda tekrar dogardi.
        """
        if disks is None:
            disks = list(self._physical_cache.values())
        self._physical_cache = {d.path: d for d in disks}
        # Ortak disk kaynak modeli (ADR 0049): her fiziksel disk bir kez,
        # uygulamada acik olan (tarama listesinde olmasa bile) oturuma bagli.
        # (Pencere kurulurken agac `_surveys` tanimlanmadan once de kurulur.)
        _images, sources = disksource.collect(
            self.sessions, disks, getattr(self, "_surveys", {}))
        diskler = [src.disk for src in sources]
        open_by_path = {src.path: src.session for src in sources}
        root = QTreeWidgetItem(self.tree, [tr("Fiziksel Diskler ({})", len(diskler))])
        root.setIcon(0, app_icon("disk"))
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
                    etiket = tr("[{} SISTEM DISKI]", d.os_label)
                else:
                    item.setIcon(0, app_icon("warning"))
                    etiket = tr("[SISTEM DISKI]")
                item.setForeground(0, QColor("#c0392b"))
                item.setText(0, f"{metin}  {etiket}")
            elif d.risk_level == "bilinmiyor":
                item.setIcon(0, app_icon("shield"))
                item.setText(0, tr("{} — (yetki yok, bilgi okunamadi)", d.name))
            elif d.risk_level == "bagli":
                item.setIcon(0, app_icon("disk"))
                item.setText(0, metin + "  " + tr("[bagli bolum var]"))
            elif d.os_hint:
                item.setIcon(0, os_icon(d.os_hint, 16))
                item.setText(0, f"{metin}  [{d.os_label}]")
            elif d.removable:
                item.setIcon(0, app_icon("disk-removable"))
            else:
                item.setIcon(0, app_icon("disk"))
            item.setToolTip(0, tr("{}\n{}\nSektor: {} B | Baglanti: {}",
                                  d.path, d.risk_text, d.sector_size,
                                  d.bus or '-'))
            # Acik disk kalin yazilir: uzerinde islem yapilan kaynak odur.
            session = open_by_path.get(d.path)
            if session is not None:
                font = item.font(0)
                font.setBold(session is self.session)
                item.setFont(0, font)
                item.setText(0, item.text(0) + f"  — {session.scheme_name}")
            self._add_survey_nodes(item, d)
        root.setExpanded(True)
        if not diskler:
            free = QTreeWidgetItem(root, [tr("(disk bulunamadi)")])
            free.setDisabled(True)

    def _refresh_overview(self) -> None:
        """Genel bakis seridini son tarama sonucuyla tazeler.

        Yukseklik disk sayisina gore buyur ama bir sinirda durur: uc disk
        rahatca gorunur, fazlasi kaydirilir. Sabit yukseklik verilseydi tek
        diskli makinede bos alan, cok diskli makinede kirpilma olurdu.
        """
        disks = list(self._physical_cache.values())
        self.disk_overview.set_disks(disks, self._surveys)
        if self.map_stack.currentIndex() == 0:
            rows = max(1, min(3, len(disks)))
            self.map_stack.setMinimumHeight(rows * 80 + 16)

    def _add_survey_nodes(self, parent: QTreeWidgetItem, info) -> None:
        """Diskin altina bolumlerini yazar (Acronis/EaseUS duzeni).

        Bolumleri gormek icin diski **acmak gerekmez**: arka plan taramasi
        bolum tablosunu salt okunur okumustur (ADR 0026). Boylece kullanici
        hangi diskte ne oldugunu tek bakista gorur; bir bolume tiklamak o diski
        acar ve dogrudan o bolumu secer.
        """
        # Disk acikSA bolumler **oturumdan** gelir: canli durum budur
        # (kuyruk uygulandiginda burasi da guncellenir). Acik degilse arka
        # plandaki salt okunur yoklamanin sonucu kullanilir.
        session = self._find_open(info.path)
        if session is not None:
            self._add_session_partitions(parent, session)
            return
        survey = self._surveys.get(info.path)
        if survey is None:
            note = QTreeWidgetItem(parent, [tr("(bolumler okunuyor...)")])
            note.setDisabled(True)
            return
        if survey.error:
            note = QTreeWidgetItem(parent, [tr("(bolumler okunamadi: {})",
                                               survey.error)])
            note.setDisabled(True)
            return
        if not survey.partitions:
            note = QTreeWidgetItem(parent, [f"({survey.scheme_name.lower()})"])
            note.setDisabled(True)
            return
        for part in survey.partitions:
            label = part.fs_label or part.name or ""
            text = tr("Bolum {}: {} ({})", part.index,
                      label or fs_display(part.fs_type) or tr("ham"),
                      human_size(part.size))
            node = QTreeWidgetItem(parent, [text])
            node.setIcon(0, color_chip(fs_color(part.fs_type), 12))
            node.setData(0, Qt.UserRole, ("physpart", (info.path, part.index)))
            node.setToolTip(0, tr("{} — {}\nLBA {} - {}\nAcmak icin tiklayin "
                                  "(salt okunur)",
                                  fs_display(part.fs_type) or tr('Bicimlendirilmemis'),
                                  human_size(part.size), part.start_lba,
                                  part.end_lba))
        parent.setExpanded(True)

    def _open_physical_partition(self, path: str, index: int) -> None:
        """Agacta bir fiziksel disk bolumune tiklandiginda calisir.

        Diski (salt okunur) acar ve dogrudan o bolumu secer. Kullanici "once
        diski ac, sonra bolumu bul" adimlarini yapmak zorunda kalmaz — listede
        gordugu sey tiklanabilir olmalidir.
        """
        session = self._find_open(path)
        if session is None:
            self.open_physical(path=path)
            session = self._find_open(path)
            if session is None:
                return              # acilamadi; hata zaten gosterildi
        if session is not self.session:
            self.session = session
            self.refresh(reload=False)
        try:
            self.select_partition(index)
        except Exception:
            self.refresh()

    def _select_tree(self, key) -> None:
        """Etkin oturumun dalinda verilen ogeyi secer.

        Arama **her derinlikte** yapilir: fiziksel diskin bolumleri artik
        "Fiziksel Diskler > disk > bolum" sirasinda, yani torun dugumdur
        (ADR 0026). Yalnizca bir seviye bakan eski surum, acik bir diskin
        bolumunu agacta hic secemiyordu.
        """
        if not self.session:
            return
        kind, value = key
        target = (kind, (id(self.session), value))
        found = self._find_tree_item(target)
        if found is None:
            return
        self.tree.blockSignals(True)
        self.tree.setCurrentItem(found)
        self.tree.blockSignals(False)

    def _find_tree_item_by_kind(self, kind: str, parent=None):
        """Agacta verilen **turde** ilk dugumu bulur (testler ve menuler icin)."""
        if parent is None:
            nodes = [self.tree.topLevelItem(i)
                     for i in range(self.tree.topLevelItemCount())]
        else:
            nodes = [parent.child(i) for i in range(parent.childCount())]
        for node in nodes:
            data = node.data(0, Qt.UserRole)
            if data and data[0] == kind:
                return node
            hit = self._find_tree_item_by_kind(kind, node)
            if hit is not None:
                return hit
        return None

    def _find_tree_item(self, target, parent=None):
        """Agacta verilen `Qt.UserRole` verisine sahip dugumu arar."""
        if parent is None:
            nodes = [self.tree.topLevelItem(i)
                     for i in range(self.tree.topLevelItemCount())]
        else:
            nodes = [parent.child(i) for i in range(parent.childCount())]
        for node in nodes:
            if node.data(0, Qt.UserRole) == target:
                return node
            hit = self._find_tree_item(target, node)
            if hit is not None:
                return hit
        return None

    def _tree_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        veri = item.data(0, Qt.UserRole)
        if not veri:
            return
        kind, value = veri
        if kind == "phys":
            # Disk acik ise uzerine tiklamak onu **etkin kaynak** yapar;
            # islemler dogrudan bu diske uygulanir (ayri bir dal yoktur).
            session = self._find_open(value)
            if session is not None:
                if session is not self.session:
                    self.session = session
                    self.selected_partition = None
                    self.selected_free = None
                    self.refresh()
                else:
                    self.show_disk_info(value)
                return
            # Disk kapali: bilgisi gosterilir **ve salt okunur acilir**.
            # Eskiden yalnizca bilgi paneli doluyordu; bolum tablosu bos,
            # butun islemler pasif kaliyordu ve kullanici "hicbir sey
            # secemiyorum" diyordu. Bolume tiklamak zaten diski aciyordu;
            # diskin kendisine tiklamanin acmamasi tutarsizdi (ADR 0035).
            self.show_disk_info(value)
            self.open_physical(path=value)
            return
        if kind == "physpart":
            self._open_physical_partition(*value)
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
            self.hex_view.set_device(self.session.image, tr("Tum goruntu"))
            self.browser.set_filesystem(None)
            self._update_actions()

    # ==================================================================
    # Baglam menuleri
    # ==================================================================
    def _readonly_hint(self, menu: QMenu) -> None:
        """Degistirilemez kaynakta menunun basina aciklama girisi koyar.

        Eskiden her salt okunur kaynakta gorunurdu; artik salt okunurluk
        **normal** kip oldugu icin (ADR 0025) bu ipucu yalnizca kaynak
        gercekten degistirilemiyorsa anlamlidir — yoksa her fiziksel diskte
        bosu bosuna cikardi.
        """
        if not self.session or self.session.can_become_writable()[0]:
            return
        info = menu.addAction(tr("(degistirilemez — neden?)"), self._readonly_warning)
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
        open_act = menu.addAction(tr("Dosya gezgininde ac"))
        open_act.triggered.connect(lambda: (self.select_partition(part.index),
                                      self.tabs.setCurrentIndex(TAB_FILES)))
        menu.addSeparator()
        self._readonly_hint(menu)
        for act in (self.act_format, self.act_resize_part, self.act_label,
                    self.act_rename_part, self.act_type_part, self.act_boot):
            menu.addAction(act)
        if self.act_mount.isEnabled() or self.act_unmount.isEnabled():
            menu.addSeparator()
            menu.addAction(self.act_mount)
            menu.addAction(self.act_unmount)
        if self.act_ntfs_fix.isEnabled():
            menu.addAction(self.act_ntfs_fix)
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
        if not item:
            return
        veri = item.data(0, Qt.UserRole)
        if not veri:
            return
        kind, value = veri
        kuresel = self.tree.viewport().mapToGlobal(pos)
        if kind in ("part", "free") and not self.session:
            return
        if kind == "physpart":
            # Acik olmayan bir diskin bolumu: once acilir, sonra menu cikar.
            self._open_physical_partition(*value)
            if self.session is not None and self.selected_partition is not None:
                self._partition_menu(
                    self.session.table.get(self.selected_partition)).exec_(kuresel)
            return
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
        elif kind == "phys":
            # Disk islemleri **diskin kendi satirinda** durur. Eskiden bolum
            # tablosu olusturma "Fiziksel Diskler" baslik satirindaydi; orasi
            # bir kategori basligidir, hedefi yoktur ve islem o sirada etkin
            # olan kaynaga gidiyordu — yani sag tiklanan diske degil
            # (ADR 0036).
            session = self._find_open(value)
            if session is None:
                self.open_physical(path=value)
                session = self._find_open(value)
            elif session is not self.session:
                self.session = session
                self.selected_partition = None
                self.selected_free = None
                self.refresh()
            menu = QMenu(self)
            menu.addAction(self.act_disk_info)
            if session is not None:
                menu.addSeparator()
                self._readonly_hint(menu)
                menu.addAction(self.act_mbr)
                menu.addAction(self.act_gpt)
                menu.addAction(self.act_clear_table)
                menu.addSeparator()
                menu.addAction(self.act_backup_disk)
                menu.addAction(self.act_restore_disk)
                menu.addSeparator()
                menu.addAction(tr("Bu diski kapat"),
                               lambda: self._close_session(session))
            menu.addSeparator()
            menu.addAction(self.act_refresh_disks)
            menu.exec_(kuresel)
        elif kind == "physroot":
            # Kategori basligi: yalnizca **butun listeye** ait isler.
            menu = QMenu(self)
            menu.addAction(self.act_refresh_disks)
            if not is_elevated():
                menu.addAction(self.act_elevate)
            menu.exec_(kuresel)
        elif kind == "session":
            target = self._session_by_id(value)
            if target is not None and target is not self.session:
                self.session = target
                self.selected_partition = None
                self.selected_free = None
                self.refresh()
            menu = QMenu(self)
            # `act_close` etkin oturumu kapatir; buradaki giris sag tiklanan
            # oturumu kapatir. Ayri islem oldugu icin ayri etiket tasir.
            menu.addAction(tr("Bu goruntuyu kapat"), self._close_tree_session)
            menu.addSeparator()
            self._readonly_hint(menu)
            menu.addAction(self.act_mbr)
            menu.addAction(self.act_gpt)
            menu.addAction(self.act_clear_table)
            # Goruntu boyutu yalnizca **dosyada** degistirilebilir; fiziksel
            # diskin boyutu donanimdir.
            if target is not None and not target.is_physical:
                menu.addSeparator()
                menu.addAction(self.act_resize_img)
            menu.addSeparator()
            menu.addAction(self.act_backup_disk)
            menu.addAction(self.act_restore_disk)
            menu.addSeparator()
            menu.addAction(self.act_refresh)
            menu.exec_(kuresel)
        else:
            menu = QMenu(self)
            menu.addAction(self.act_refresh_disks)
            menu.exec_(kuresel)

    # ==================================================================
    # Bilgi panelleri
    # ==================================================================
    def _disk_summary_text(self) -> str:
        if not self.session:
            return ""
        satirlar = [tr("DISK GORUNTUSU"), "=" * 52]
        for key, value in self.session.summary().items():
            satirlar.append(f"{key:<16}: {value}")
        satirlar.append("")
        satirlar.append(tr("BOLUMLER"))
        satirlar.append("-" * 52)
        for p in self.session.partitions:
            satirlar.append(
                f"  {p.index:>2}. {p.display_name:<22} "
                f"{fs_display(p.fs_type) or '-':<8} {human_size(p.size):>10}  "
                f"LBA {p.start_lba}-{p.end_lba}")
        if not self.session.partitions:
            satirlar.append(tr("  (bolum yok)"))
        return "\n".join(satirlar)

    def _show_partition_info(self, part: Partition) -> None:
        s = self.session
        info = s.detect_fs(part)
        satirlar = [tr("BOLUM {}", part.index), "=" * 52]
        alanlar = [
            (tr("Ad"), part.display_name),
            (tr("Sema"), s.scheme_name),
            (tr("Tur"), part.type_name),
            (tr("Dosya sistemi"), fs_display(part.fs_type) or tr("Bicimlendirilmemis")),
            (tr("Birim etiketi"), info.label or "-"),
            (mount_point_label(), part.mount_point or tr("Bagli degil")),
            (tr("Boyut"), tr("{} ({} sektor)", human_size(part.size),
                             part.sector_count)),
            (tr("Baslangic LBA"), str(part.start_lba)),
            (tr("Bitis LBA"), str(part.end_lba)),
            (tr("Bayt ofseti"),
             f"{part.start_lba * part.sector_size:,}".replace(",", ".")),
            (tr("Onyuklenebilir"), tr("Evet") if part.bootable else tr("Hayir")),
        ]
        if part.logical:
            alanlar.append((tr("Konum"),
                            tr("Mantiksal (EBR: LBA {})", part.ebr_lba)))
        if s.scheme == "gpt":
            alanlar.append((tr("Tur GUID"), part.type_guid))
            alanlar.append((tr("Bolum GUID"), part.part_guid))
            alanlar.append((tr("Oznitelikler"), f"0x{part.attributes:016X}"))
        if info.cluster_size > 0:
            alanlar.append((tr("Kume/blok boyutu"), human_size(info.cluster_size)))
        if info.uuid:
            alanlar.append((tr("UUID / Seri no"), info.uuid))
        if part.fs_type == "NTFS":
            alanlar.append((tr("Durum"), self._ntfs_state_text(part, info)))
        if info.total_bytes >= 0 and info.used_bytes >= 0:
            oran = 100 * info.used_bytes / max(1, info.total_bytes)
            alanlar.append((tr("Kullanilan"),
                            f"{human_size(info.used_bytes)} (%{oran:.1f})"))
            alanlar.append((tr("Bos"), human_size(info.free_bytes)))
        for key, value in alanlar:
            satirlar.append(f"{key:<18}: {value}")
        self.info_view.setPlainText("\n".join(satirlar))

    # ==================================================================
    # Yardimcilar
    # ==================================================================
    @staticmethod
    def _ntfs_state_text(part: Partition, info) -> str:
        """NTFS biriminin temizlik durumu (bolum bilgisi paneli icin).

        Bagli bir birim kullanimdayken her zaman "kirli" gorunur (Windows ve
        ntfs3 bayragi bagliyken acar); orada uyari yaniltici olurdu.
        """
        if part.mount_point:
            return tr("Bagli — isletim sistemi kullaniyor")
        if info.hibernated:
            return tr("Windows hazirda bekletmede — Bolum > NTFS'i denetle "
                      "ve onar")
        if info.unclean:
            return tr("Temiz kapatilmamis — Linux baglamaz; Bolum > NTFS'i "
                      "denetle ve onar")
        return tr("Temiz")

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
            QMessageBox.information(self, tr("Bolum secili degil"),
                                    tr("Once listeden veya haritadan bir bolum secin."))
            return None
        try:
            return self.session.table.get(self.selected_partition)
        except Exception:
            return None

    def _require_session(self) -> bool:
        """Islem **kuyruga alinabilir** mi?

        Artik "kaynak su an yazilabilir mi" diye sorulmaz: kuyruga eklemek
        diske dokunmaz. Sorulan sey, kaynagin uygulama aninda yazma moduna
        **gecebilecek** olmasidir (ADR 0025). Fiziksel disk her zaman salt
        okunur acilir ve yine de islem kuyruklanabilir; gecemeyecek kaynaklar
        (`.dub` yedegi, VDI/QCOW2) nedeniyle birlikte reddedilir.
        """
        if self.session is None:
            QMessageBox.information(self, tr("Goruntu yok"),
                                    tr("Once bir disk goruntusu acin veya olusturun."))
            return False
        can_write, reason = self.session.can_become_writable()
        if not can_write:
            QMessageBox.information(
                self, tr("Bu kaynak degistirilemez"),
                f"<b>{self.session.name}</b><br><br>{reason}")
            return False
        return True

    def _readonly_open_warning(self, path: str) -> None:
        """Dosya salt okunur acildiginda nedeni gosterir ve yeniden denemeyi sunar."""
        reason = self.session.readonly_reason
        self.log(tr("DIKKAT: salt okunur acildi — {}", reason))
        # Kilit durumu **metinden okunmaz**: metin cevrildiginde arama bos
        # donerdi. Bayrak goruntu nesnesinde tasinir (bkz. image.DiskImage).
        kilit = bool(getattr(self.session.image, "readonly_locked", False))
        metin = tr("<b>{}</b> salt okunur acildi; bu dosyada degisiklik "
                   "yapilamaz.<br><br><b>Neden:</b> {}<br><b>Yol:</b> {}<br>"
                   "<b>Bicim:</b> {}", os.path.basename(path), reason, path,
                   self.session.format_name)
        if kilit:
            metin += tr("<br><br>Dosyayi kullanan diger programi (baska bir "
                        "disk araci, yedekleme yazilimi vb.) kapatip "
                        "<b>Yeniden dene</b>ye basin.")
            box = QMessageBox(QMessageBox.Warning, tr("Salt okunur acildi"), metin,
                               QMessageBox.NoButton, self)
            yeniden = box.addButton(tr("Yeniden dene"), QMessageBox.AcceptRole)
            box.addButton(tr("Salt okunur devam et"), QMessageBox.RejectRole)
            box.exec_()
            if box.clickedButton() is yeniden:
                self.close_image()
                self.open_path(path)
            return
        QMessageBox.warning(self, tr("Salt okunur acildi"), metin)

    def _readonly_warning(self) -> None:
        """Kaynagin neden degistirilemedigini acikca soyler.

        Eskiden burada "yazma modunda acilsin mi?" diye sorulurdu; o secim
        kalkti (ADR 0025). Artik ya kaynak zaten kuyruklanabilir — bu pencere
        hic acilmaz — ya da gercekten degistirilemez ve nedeni budur.
        """
        can_write, why_not = self.session.can_become_writable()
        name = self.session.name
        if can_write:
            QMessageBox.information(
                self, tr("Degisiklikler bekliyor"),
                tr("<b>{}</b> salt okunur acik — bu "
                   "<b>normaldir</b>.<br><br>Yaptiginiz degisiklikler "
                   "bekleyen islem olarak birikir ve diske ancak "
                   "<b>Uygula</b> dediginizde yazilir.", name))
            return
        QMessageBox.information(
            self, tr("Degistirilemez kaynak"),
            tr("<b>{}</b> uzerinde degisiklik yapilamaz.<br><br>{}", name, why_not))

    def _update_actions(self) -> None:
        acik = self.session is not None
        # "Yazilabilir" artik **su anki** durumu degil, kuyruga alinabilirligi
        # anlatir: kaynak uygulama aninda yazma moduna gecebiliyorsa islem
        # eklenebilir (ADR 0025).
        yazilabilir = acik and self.session.can_become_writable()[0]
        has_table = acik and self.session.table is not None
        queue_len = len(self.queue) if acik else 0
        self.act_apply.setEnabled(acik and queue_len > 0)
        self.act_undo_step.setEnabled(queue_len > 0)
        self.act_discard.setEnabled(queue_len > 0)
        # Planlanan (henuz olusturulmamis) bolum uzerinde islem
        # yapilamaz: diskte karsiligi yoktur.
        part_selected = (acik and self.selected_partition is not None
                         and not self.selected_is_planned)
        self.act_close.setEnabled(acik)
        self.act_refresh.setEnabled(acik)
        self.act_mbr.setEnabled(yazilabilir)
        self.act_gpt.setEnabled(yazilabilir)
        self.act_clear_table.setEnabled(yazilabilir and has_table)
        self.act_resize_img.setEnabled(yazilabilir)
        self.act_create_part.setEnabled(yazilabilir)
        self.act_edit_layout.setEnabled(yazilabilir and has_table)
        for act in (self.act_format, self.act_resize_part, self.act_delete_part,
                    self.act_boot, self.act_type_part, self.act_label):
            act.setEnabled(yazilabilir and part_selected)
        self.act_rename_part.setEnabled(
            yazilabilir and part_selected and self.session.scheme == "gpt")
        # Baglama isletim sistemi islemidir: kaynagin salt okunur acilmis
        # olmasi engel degildir, ama yalnizca **gercek diskte** anlamlidir
        # (goruntu dosyasi zaten dosya sistemine bagli degildir).
        can_mount = (part_selected and self.session.disk_info is not None
                     and mount_supported()[0])
        self.act_mount.setEnabled(can_mount)
        self.act_unmount.setEnabled(can_mount)
        # Onyukleme bayragi: metin secili bolumun durumunu yansitir. Burada
        # `_current_partition()` kullanilmaz — o islev diyalog acar ve bu islev
        # her yenilemede cagrilir.
        selected = self._selected_partition_quiet()
        self.act_boot.setText(tr(
            BOOT_CLEAR_TEXT if selected is not None and selected.bootable
            else BOOT_SET_TEXT))
        # donusum / bakim
        self.act_to_gpt.setEnabled(yazilabilir and self.session.scheme == "mbr")
        self.act_to_mbr.setEnabled(yazilabilir and self.session.scheme == "gpt")
        self.act_alignment.setEnabled(has_table)
        # Onyukleyici yoneticisi acik bir disk ister; UEFI duzenleyici
        # istemez — bellenim degiskenleri diskte degil anakarttadir.
        self.act_bootloader.setEnabled(acik)
        # Yedekleme ve geri yukleme: kaynak/hedef **pencerenin icinde**
        # secilir (ADR 0032). Bu yuzden acik oturum sart degildir; agacta
        # secili bir disk de yeter. Eskiden bu eylemler yalnizca acik
        # goruntude etkindi ve kullanici "diski yedekleyecegim" derken once
        # diski acmak zorunda kaliyordu.
        kaynak_var = acik or bool(self._physical_cache)
        self.act_backup_disk.setEnabled(kaynak_var)
        self.act_restore_disk.setEnabled(kaynak_var)
        self.act_clone_disk.setEnabled(acik)
        self.act_write_backup.setEnabled(acik and self.session.is_backup)
        self.act_backup_part.setEnabled(acik and part_selected)
        self.act_restore_part.setEnabled(yazilabilir and part_selected)
        # silme
        self.act_wipe_disk.setEnabled(yazilabilir)
        self.act_wipe_part.setEnabled(yazilabilir and part_selected)
        selected = self._selected_partition_quiet()
        self.act_ntfs_fix.setEnabled(
            yazilabilir and selected is not None and selected.fs_type == "NTFS")
        # fiziksel diskler
        disk_selected = self._selected_disk_path() is not None
        self.act_refresh_disks.setEnabled(True)
        # Zaten yetkiliyken dugme pasiftir; nedeni ipucunda yazar.
        can_elevate, why_not = elevation_available()
        self.act_elevate.setEnabled(can_elevate)
        if why_not:
            self.act_elevate.setToolTip(why_not)
        self.act_open_disk.setEnabled(disk_selected)
        self.act_disk_info.setEnabled(disk_selected)
        # kurtarma
        self.act_scan_deleted.setEnabled(acik and part_selected)
        self.act_scan_lost.setEnabled(acik)
        self.act_carve.setEnabled(acik)

    def about(self) -> None:
        # Baglantilar Qt'ye birakilmaz: Linux'ta uygulama root calisir ve
        # QDesktopServices tarayiciyi root olarak acmaya calisirdi.
        # `platform.open_url` yetkiyi veren kullanici olarak acar (ADR 0090).
        box = QMessageBox(self)
        box.setIconPixmap(self.windowIcon().pixmap(64, 64))
        box.setWindowTitle(tr("{} hakkinda", APP_NAME))
        box.setTextFormat(Qt.RichText)
        box.setText(
            tr("<h3>{} {}</h3><p>Disk goruntusu, sanal disk ve <b>sistemdeki "
               "gercek diskler</b> uzerinde bolumleme, bicimlendirme, "
               "yedekleme ve kurtarma araci.</p><p><b>Teknoloji:</b> Python 3 "
               "+ PyQt5, harici bagimlilik yok<br><b>Bolum tablolari:</b> MBR "
               "(mantiksal bolumler dahil), GPT, MBR&nbsp;&harr;&nbsp;GPT "
               "donusumu<br><b>Bicimlendirme:</b> FAT12/16/32, exFAT, "
               "ext2/3/4 ve NTFS — sekizi de saf Python, uc "
               "platformda<br><b>Dosya erisimi:</b> FAT ve exFAT tam "
               "okuma/yazma</p><p>Goruntu dosyalari yonetici yetkisi "
               "gerektirmez. Fiziksel disk erisimi yonetici/root ister ve "
               "<b>varsayilan olarak salt okunurdur</b>; yazma ayrica onay "
               "ister.</p>", APP_NAME, APP_VERSION)
            + "<p>" + tr("Gelistirici: {}", APP_AUTHOR) + "<br>"
            + tr("Proje sayfasi: {}", f"<a href='{PROJECT_URL}'>{PROJECT_URL}</a>")
            + "<br>" + tr("Lisans: GNU GPL surum 3. Uygulamayla gelen Qt, PyQt5 "
                          "ve Python'un lisanslari: Yardim > Ucuncu taraf "
                          "lisanslari.") + "</p>")
        label = box.findChild(QLabel, "qt_msgbox_label")
        if label is not None:
            label.setOpenExternalLinks(False)
            label.setTextInteractionFlags(Qt.TextBrowserInteraction)
            label.linkActivated.connect(platform.open_url)
        box.exec_()

    # ==================================================================
    # Guncelleme denetimi (ADR 0090)
    # ==================================================================
    def schedule_update_check(self) -> None:
        """Acilista, pencere gorundukten sonra sessiz denetim (ayar aciksa)."""
        if updatecheck.auto_enabled():
            QTimer.singleShot(4000, lambda: self._start_update_check(silent=True))

    def check_updates(self) -> None:
        """Yardim > Guncellemeleri denetle: sonuc her durumda gosterilir."""
        self._start_update_check(silent=False)

    def _start_update_check(self, silent: bool) -> None:
        if getattr(self, "_update_worker", None) is not None:
            return
        worker = updatecheck.UpdateWorker(APP_VERSION, self)
        worker.done.connect(lambda newer, latest, error, s=silent:
                            self._on_update_done(newer, latest, error, s))
        self._update_worker = worker
        if not silent:
            self.status_sel.setText(tr("Guncellemeler denetleniyor..."))
        worker.start()

    def _on_update_done(self, newer, latest, error: str, silent: bool) -> None:
        worker, self._update_worker = self._update_worker, None
        if worker is not None:
            worker.wait()
        if not silent:
            self.status_sel.setText("")
        if error:
            diagnostics.info(f"guncelleme denetimi: {error}")
            if not silent:
                QMessageBox.warning(self, tr("Guncellemeleri denetle"),
                                    tr("Denetlenemedi: {}", error))
            return
        if newer is not None:
            self.log(tr("Yeni surum var: {} (kullanilan: {})", newer.version,
                        APP_VERSION))
            if silent and settings.get(updatecheck.SKIP_KEY, "") == newer.tag:
                return
            updatecheck.offer(self, newer, APP_VERSION, silent)
            return
        diagnostics.info(f"guncelleme denetimi: en son {latest.tag if latest else '-'}")
        if not silent:
            QMessageBox.information(
                self, tr("Guncellemeleri denetle"),
                tr("En guncel surumu kullaniyorsunuz ({}).", APP_VERSION))

    def closeEvent(self, event) -> None:
        # Arka plandaki sinir hesaplari biter (kisa surer); yarida kalan is
        # parcacigi kapanmis aygita ya da yikilmis pencereye ulasmasin.
        self.limits.wait_all()
        self.osinfo.wait_all()
        # Yoklama zamanlayicisi once durur: kapanis sirasinda tetiklenirse
        # yikilmakta olan agaca dokunmaya calisirdi.
        timer = getattr(self, "_disk_timer", None)
        if timer is not None:
            timer.stop()
        # Arka plandaki disk taramasi bitmeden pencere yikilmamali: sinyali
        # yok olmus bir nesneye ulasirdi.
        if self._scan_running():
            try:
                self._scanner.ready.disconnect()
            except TypeError:       # zaten bagli degil
                pass
            self._scanner.wait(5000)
        diag = getattr(self, "diagnostics", None)
        if diag is not None:
            diag.stop()
        diagnostics.info("uygulama kapaniyor")
        self.close_all()
        super().closeEvent(event)
