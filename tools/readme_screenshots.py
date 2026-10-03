"""README ekran goruntulerini uretir (Turkce ve Ingilizce).

Calistirma:
    python3 tools/readme_screenshots.py

Ciktilar: <proje>/docs/screenshots/<dil>/ altina yazilir.

`tests.ui_smoke`'tan farki: goruntuler **yayina** gider. Bu yuzden
  * makinenin fiziksel diskleri listelenmez (model/seri bilgisi sizmasin),
  * durum cubugundaki tam dosya yolu temizlenir,
  * ornek goruntunun bolum adlari ve dosyalari secilen dilde yazilir.
"""
from __future__ import annotations

import os
import sys

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(KOK, "src"))

os.environ.setdefault("QT_QPA_PLATFORM",
                      "windows" if sys.platform.startswith("win") else "offscreen")
os.environ["DISKULTIMATE_NO_ELEVATION_PROMPT"] = "1"
os.environ["DISKULTIMATE_DIAG"] = "0"

from PyQt5.QtWidgets import QApplication  # noqa: E402

from diskultimate import i18n  # noqa: E402
from diskultimate.core.session import DiskSession  # noqa: E402
from diskultimate.paths import scratch  # noqa: E402

MIB = 1024 * 1024

# Ornek goruntunun icerigi: dil -> (dosya adi, bolumler, klasor, dosyalar)
VERI = {
    "tr": {
        "image": "ornek.img",
        "parts": (("fat32", 512, "SISTEM", "EFI Sistem"),
                  ("ext4", 1024, "LINUX", "Linux kok"),
                  ("ntfs", 800, "VERI", "Windows Veri"),
                  ("exfat", 600, "TASINABILIR", "Paylasim")),
        "folder": "/Belgeler",
        "files": (("okubeni.txt", b"DiskUltimate ornek dosyasi\n"),
                  ("Yillik Rapor 2026.pdf", b"%PDF-1.7\n" + bytes(380000)),
                  ("tatil-fotograflari.zip", b"PK\x03\x04" + bytes(1500000)),
                  ("gunluk.log", b"gunluk satiri\n" * 20000)),
        "deleted": (("Onemli Rapor 2026.docx", "/Belgeler", 1843200, "iyi", 1843200),
                    ("tatil-fotograflari.zip", "/Yedek", 48234496,
                     "kismen uzerine yazilmis", 20000000),
                    ("notlar.txt", "", 2048, "iyi", 2048),
                    ("eski.bin", "", 512000, "kayip", 0)),
        "remark": "Ana diskin haftalik yedegi",
        "backup": "haftalik-yedek.dub",
    },
    "en": {
        "image": "sample.img",
        "parts": (("fat32", 512, "SYSTEM", "EFI System"),
                  ("ext4", 1024, "LINUX", "Linux root"),
                  ("ntfs", 800, "DATA", "Windows Data"),
                  ("exfat", 600, "PORTABLE", "Shared")),
        "folder": "/Documents",
        "files": (("readme.txt", b"DiskUltimate sample file\n"),
                  ("Annual Report 2026.pdf", b"%PDF-1.7\n" + bytes(380000)),
                  ("holiday-photos.zip", b"PK\x03\x04" + bytes(1500000)),
                  ("journal.log", b"log line\n" * 20000)),
        "deleted": (("Important Report 2026.docx", "/Documents", 1843200, "good", 1843200),
                    ("holiday-photos.zip", "/Backup", 48234496,
                     "partially overwritten", 20000000),
                    ("notes.txt", "", 2048, "good", 2048),
                    ("old.bin", "", 512000, "lost", 0)),
        "remark": "Weekly backup of the main disk",
        "backup": "weekly-backup.dub",
    },
}


def ornek_goruntu(dil: str) -> str:
    """Dort bolumlu 4 GB ornek goruntu (bolum adlari secilen dilde)."""
    from diskultimate.core.formatter import available_kinds

    v = VERI[dil]
    yol = os.path.join(scratch("readme", dil), v["image"])
    s = DiskSession.create(yol, 4096 * MIB, scheme="gpt", overwrite=True)
    mevcut = {k.key for k in available_kinds()}
    for key, boyut, etiket, ad in v["parts"]:
        r = s.free_regions()[0]
        s.create_partition(r.start_lba, boyut * MIB // 512,
                           fs_key=key if key in mevcut else "",
                           label=etiket, name=ad)
    fs = s.filesystem(1)
    fs.mkdir("/EFI")
    fs.mkdir("/EFI/BOOT")
    fs.write_file("/EFI/BOOT/BOOTX64.EFI", b"MZ" + bytes(200000))
    fs.mkdir(v["folder"])
    for ad, veri in v["files"]:
        fs.write_file(f"{v['folder']}/{ad}", veri)
    s.close()
    return yol


def cek(dil: str, app) -> None:
    from diskultimate.core import operations as ops
    from diskultimate.core import efiboot as efi
    from diskultimate.core import efistore
    from diskultimate.core import bootloader as bl
    from diskultimate.core.ptable import FreeRegion
    from diskultimate.core.recovery import DeletedFile, LostPartition
    from diskultimate.ui.dialogs.apply import ApplyDialog
    from diskultimate.ui.dialogs.backup import MODE_BACKUP, BackupDialog
    from diskultimate.ui.dialogs.bootloader import BootloaderDialog
    from diskultimate.ui.dialogs.efiboot import EfiBootDialog
    from diskultimate.ui.dialogs.partition import CreatePartitionDialog, FormatDialog
    from diskultimate.ui.dialogs.resize import ResizePartitionDialog
    from diskultimate.ui.dialogs.tools import (CarveOptionsDialog,
                                               DeletedFilesDialog,
                                               LostPartitionsDialog, WipeDialog)
    from diskultimate.ui.main_window import TAB_HEX, MainWindow

    i18n.set_language(dil, remember=False)
    v = VERI[dil]
    goruntu = ornek_goruntu(dil)
    hedef = os.path.join(KOK, "docs", "screenshots", dil)
    os.makedirs(hedef, exist_ok=True)

    pencere = MainWindow()
    pencere.resize(1400, 860)
    pencere.show()
    pencere.open_path(goruntu)
    app.processEvents()

    def kaydet(widget, ad: str) -> None:
        pencere.statusBar().clearMessage()
        pencere.status_file.setText(os.path.basename(goruntu))
        for _ in range(3):
            app.processEvents()
        widget.grab().save(os.path.join(hedef, ad))
        print(f"  {dil}/{ad}")

    # 1. Ana pencere: harita + tablo + dosya gezgini
    pencere.select_partition(1)
    pencere.browser.navigate(v["folder"])
    kaydet(pencere, "main-window.png")

    # 2. Onaltilik goruntuleyici
    pencere.tabs.setCurrentIndex(TAB_HEX)
    kaydet(pencere, "hex-viewer.png")
    pencere.tabs.setCurrentIndex(0)

    # 3. Bolum boyutlandirma (suruklenen serit)
    part1 = pencere.session.table.get(2)
    d = ResizePartitionDialog(
        part1, pencere.session.resize_window(2), pencere.session.resize_info(2),
        align_sectors=pencere.session.table.align_sectors,
        used_bytes=part1.fs_used, parent=pencere)
    d.show()
    d.bar._dragging = "sag"
    d.bar._apply(d.bar.start, max(d.bar.min_count, d.bar.count * 2 // 3))
    kaydet(d, "resize-partition.png")
    d.close()

    # 4. Yeni bolum / bicimlendirme
    d = CreatePartitionDialog(FreeRegion(6014976, 2373598, 512), "gpt", pencere)
    d.show()
    kaydet(d, "new-partition.png")
    d.close()
    d = FormatDialog(pencere.session.table.get(1), pencere)
    d.show()
    kaydet(d, "format.png")
    d.close()

    # 5. Bekleyen islemler: planlanan yerlesim ve Uygula penceresi
    bos = pencere.session.free_regions()[0]
    pencere.enqueue(ops.create_op(bos.start_lba, 400 * MIB // 512, 512,
                                  fs_key="ntfs", label="NEW" if dil == "en" else "YENI"))
    pencere.enqueue(ops.delete_op(3, v["parts"][2][3]))
    pencere.enqueue(ops.format_op(4, "exfat", label=v["parts"][3][2],
                                  fs_name="exFAT"))
    app.processEvents()
    kaydet(pencere, "pending-operations.png")
    d = ApplyDialog(pencere, pencere.session, pencere.queue)
    d.show()
    d._on_step_started(0)
    d._on_step_done(0, "")
    d._on_step_started(1)
    d._on_step_progress(1, i18n.tr("Bolum tablosu yaziliyor..."), 45)
    d._on_overall("2/3", 55)
    kaydet(d, "apply.png")
    d.close()
    pencere.queue.clear()
    pencere._refresh_pending()
    app.processEvents()

    # 6. Yedekleme
    d = BackupDialog(pencere, mode=MODE_BACKUP, sessions=list(pencere.sessions),
                     disks=[], surveys={}, session=pencere.session)
    d.show()
    d._set_path(os.path.join("/backups" if os.sep == "/" else "D:\\Backups",
                             v["backup"]))
    d.remark.setPlainText(v["remark"])
    d.level_buttons["high"].setChecked(True)
    kaydet(d, "backup.png")
    d.close()

    # 7. Onyukleyici yoneticisi ve UEFI duzenleyici
    d = BootloaderDialog(pencere, pencere.session, pencere.enqueue)
    d.adopt(bl.survey_session(pencere.session))
    d.show()
    d.resize(1040, 760)
    kaydet(d, "bootloader.png")
    d.close()

    durum = efistore.BootState(firmware="uefi", readable=True, writable=True,
                               source="firmware")
    yol = [efi.make_hard_drive_node(1, 2048, 1048576, "gpt",
                                    "a967f8c5-ed93-4c8d-927d-61ca376a5e9b")]
    durum.entries["Boot"] = {
        0: efi.LoadOption(number=0, description="Windows Boot Manager",
                          path_nodes=yol + [efi.make_file_node(
                              "/EFI/Microsoft/Boot/bootmgfw.efi")]),
        1: efi.LoadOption(number=1, description="Ubuntu",
                          path_nodes=yol + [efi.make_file_node(
                              "/EFI/ubuntu/shimx64.efi")]),
        2: efi.LoadOption(number=2, description="UEFI Shell",
                          path_nodes=yol + [efi.make_file_node(
                              "/EFI/tools/shell.efi")]),
    }
    durum.entries["Boot"][2].active = False
    durum.orders["Boot"] = [1, 0, 2]
    durum.timeout = 5
    durum.boot_current = 1
    d = EfiBootDialog(pencere)
    d.adopt(durum)
    d.show()
    d.resize(1100, 720)
    d.tree.setCurrentItem(d.tree.topLevelItem(0))
    kaydet(d, "uefi-boot.png")
    d.close()

    # 8. Veri kurtarma
    silinmis = [DeletedFile(ad, f"{klasor}/{ad}", boyut, 1200 + i * 700,
                            condition=durum_, recoverable_bytes=kurtarilir,
                            fs_type="FAT32")
                for i, (ad, klasor, boyut, durum_, kurtarilir) in enumerate(v["deleted"])]
    d = DeletedFilesDialog(silinmis, pencere,
                           i18n.tr("Bolum {}", 1) + " — " + i18n.tr("Silinmis Dosyalar"))
    d.show()
    kaydet(d, "deleted-files.png")
    d.close()

    d = LostPartitionsDialog([
        LostPartition(2048, 1048576, "FAT32", v["parts"][0][2]),
        LostPartition(1050624, 2097152, "NTFS", v["parts"][2][2]),
        LostPartition(3147776, 1228800, "exFAT", v["parts"][3][2]),
    ], pencere)
    d.show()
    kaydet(d, "lost-partitions.png")
    d.close()

    d = CarveOptionsDialog(pencere, i18n.tr("Bolum {}", 2))
    d.show()
    kaydet(d, "file-carving.png")
    d.close()

    # 9. Guvenli silme
    d = WipeDialog(pencere, i18n.tr("Bolum {}", 1), 512 * MIB, allow_free_space=True)
    d.show()
    d.resize(680, d.height())
    kaydet(d, "secure-wipe.png")
    d.close()

    # 10. NTFS denetle ve onar (ADR 0077): Windows'un temiz kapatmadigi birim
    from diskultimate.core import ntfsfix as nf
    from diskultimate.ui.dialogs.ntfsfix import NtfsFixDialog
    d = NtfsFixDialog(pencere, i18n.tr("Bolum {}", 3) + f" ({v['parts'][2][3]})",
                      nf.NtfsHealth(dirty=True, logfile=nf.LOG_UNCLEAN,
                                    version="3.1"))
    d.show()
    kaydet(d, "ntfs-repair.png")
    d.close()

    pencere.close()
    pencere.deleteLater()
    app.processEvents()


def main() -> int:
    # Yayina giden goruntude makinenin diskleri gorunmez
    DiskSession.list_physical_disks = staticmethod(lambda *a, **k: [])

    app = QApplication(sys.argv)
    from diskultimate.ui.appicon import apply_app_icon
    from diskultimate.ui.theme import apply_theme
    apply_theme(app, "system")
    apply_app_icon(app)
    diller = sys.argv[1:] or ["tr", "en"]
    for dil in diller:
        cek(dil, app)
    i18n.set_language(i18n.SOURCE_LANGUAGE, remember=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
