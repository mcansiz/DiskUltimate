"""Arayuz duman testi: ornek bir goruntu uretir, pencereleri cizip PNG kaydeder.

Calistirma:
    python3 -m tests.ui_smoke

Ciktilar: <proje>/.tmp/screenshots/ altina yazilir. Ekranda pencere acilmamasi
icin varsayilan olarak `offscreen` platformu kullanilir; gercek cizimi gormek
icin:  DISKULTIMATE_QPA=xcb python3 -m tests.ui_smoke
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

def _qt_platformu() -> str:
    """Duman testi icin Qt platform eklentisi.

    Windows'ta `offscreen` eklentisi sistem fontlarini yuklemez (font ailesi 0,
    metin hic cizilmez), bu yuzden uretilen goruntuler yaniltici olur. Orada
    gercek `windows` eklentisi kullanilir — ekranda kisa sureligine pencere acar.
    """
    istek = os.environ.get("DISKULTIMATE_QPA") or os.environ.get("QT_QPA_PLATFORM")
    if istek:
        return istek
    return "windows" if sys.platform.startswith("win") else "offscreen"


os.environ["QT_QPA_PLATFORM"] = _qt_platformu()

from PyQt5.QtWidgets import QApplication  # noqa: E402

from diskultimate.core.formatter import available_kinds  # noqa: E402
from diskultimate.core.ptable import FreeRegion  # noqa: E402
from diskultimate.core.session import DiskSession  # noqa: E402
from diskultimate.paths import scratch  # noqa: E402
from diskultimate.ui.dialogs.new_image import NewImageDialog  # noqa: E402
from diskultimate.ui.dialogs.resize import ResizePartitionDialog
from diskultimate.ui.dialogs.partition import (CreatePartitionDialog,  # noqa: E402
                                               FormatDialog)
from diskultimate.ui.dialogs.tools import (CarveOptionsDialog,  # noqa: E402
                                           CarvedFilesDialog,
                                           DeletedFilesDialog, InfoDialog,
                                           LostPartitionsDialog, WipeDialog)
from diskultimate.core.platform import summary as platform_summary  # noqa: E402
from diskultimate.core.recovery import CarvedFile, DeletedFile, LostPartition  # noqa: E402
from diskultimate.ui.main_window import MainWindow  # noqa: E402
from diskultimate.ui.theme import apply_theme  # noqa: E402

MIB = 1024 * 1024


def ornek_goruntu() -> str:
    """Dort bolumlu 4 GB ornek goruntu uretir."""
    yol = os.path.join(scratch("ui"), "ornek.img")
    s = DiskSession.create(yol, 4096 * MIB, scheme="gpt", overwrite=True)
    r = s.free_regions()[0]
    p1 = s.create_partition(r.start_lba, 512 * MIB // 512, fs_key="fat32",
                            label="SISTEM", name="EFI Sistem")
    fs = s.filesystem(p1.index)
    fs.mkdir("/EFI")
    fs.mkdir("/EFI/BOOT")
    fs.mkdir("/Belgeler")
    fs.write_file("/EFI/BOOT/BOOTX64.EFI", b"MZ" + bytes(200000))
    fs.write_file("/Belgeler/okubeni.txt", b"DiskUltimate ornek dosyasi\n")
    fs.write_file("/Belgeler/Uzun Dosya Adi Ornegi 2026.log", b"gunluk satiri\n" * 20000)
    # Bu platformda bicimlendirilemeyen dosya sistemleri (orn. Windows'ta ext4)
    # ham bolum olarak olusturulur; ekran duzeni platformlar arasi ayni kalir.
    mevcut = {k.key for k in available_kinds()}
    for key, boyut, etiket, ad in (("ext4", 1024, "LINUX", "Linux kok"),
                                   ("ntfs", 800, "VERI", "Windows Veri"),
                                   ("exfat", 600, "TASINABILIR", "Paylasim")):
        r = s.free_regions()[0]
        s.create_partition(r.start_lba, boyut * MIB // 512,
                           fs_key=key if key in mevcut else "",
                           label=etiket, name=ad)
    s.close()
    return yol


def main() -> int:
    hedef = scratch("screenshots")
    goruntu = ornek_goruntu()
    app = QApplication(sys.argv)
    apply_theme(app, os.environ.get("DISKULTIMATE_THEME", "system"))

    pencere = MainWindow()
    pencere.resize(1400, 860)
    pencere.show()
    pencere.open_path(goruntu)
    app.processEvents()

    def kaydet(widget, ad: str) -> None:
        app.processEvents()
        app.processEvents()
        yol = os.path.join(hedef, ad)
        widget.grab().save(yol)
        print(f"  {ad}")

    print(f"Ekran goruntuleri -> {hedef}")
    kaydet(pencere, "01-ana-pencere.png")
    pencere.select_partition(1)
    pencere.browser.navigate("/Belgeler")
    kaydet(pencere, "02-dosya-gezgini.png")
    pencere.tabs.setCurrentIndex(1)
    kaydet(pencere, "03-bolum-bilgisi.png")
    pencere.tabs.setCurrentIndex(2)
    kaydet(pencere, "04-onaltilik.png")
    pencere.select_partition(2)
    kaydet(pencere, "05-desteklenmeyen-fs.png")

    d = NewImageDialog(pencere, os.path.dirname(goruntu))
    d.show()
    kaydet(d, "06-yeni-goruntu.png")
    d.close()

    d2 = CreatePartitionDialog(FreeRegion(6014976, 2373598, 512), "gpt", pencere)
    d2.show()
    kaydet(d2, "07-yeni-bolum.png")
    d2.close()

    d3 = FormatDialog(pencere.session.table.get(1), pencere)
    d3.show()
    kaydet(d3, "08-bicimlendir.png")
    d3.close()

    part1 = pencere.session.table.get(1)
    d3b = ResizePartitionDialog(
        part1, pencere.session.resize_window(1), pencere.session.resize_info(1),
        align_sectors=pencere.session.table.align_sectors,
        used_bytes=part1.fs_used, parent=pencere)
    d3b.show()
    kaydet(d3b, "08b-bolum-boyutlandir.png")
    # suruklemeyi taklit et: sag tutamagi yarisina cek, sonra eski haline don
    bar = d3b.bar
    bar._surukleme = "sag"
    bar._uygula(bar.start, max(bar.min_count, bar.count // 2))
    assert d3b.kapasite.value() > 0
    kaydet(d3b, "08c-bolum-boyutlandir-surukleme.png")
    d3b.close()

    d4 = WipeDialog(pencere, "Bolum 1 (EFI Sistem)", 512 * MIB, allow_free_space=True)
    d4.show()
    kaydet(d4, "09-guvenli-silme.png")
    d4.close()

    silinmisler = [
        DeletedFile("Onemli Rapor 2026.docx", "/belgeler/Onemli Rapor 2026.docx",
                    1843200, 1200, condition="iyi", recoverable_bytes=1843200,
                    fs_type="FAT32"),
        DeletedFile("tatil-fotograflari.zip", "/yedek/tatil-fotograflari.zip",
                    48234496, 5400, condition="kismen uzerine yazilmis",
                    recoverable_bytes=20000000, fs_type="FAT32"),
        DeletedFile("notlar.txt", "/notlar.txt", 2048, 88, condition="iyi",
                    recoverable_bytes=2048, fs_type="FAT32"),
        DeletedFile("eski.bin", "/eski.bin", 512000, 0, condition="kayip",
                    recoverable_bytes=0, fs_type="FAT32"),
    ]
    d5 = DeletedFilesDialog(silinmisler, pencere, "Bolum 1 — Silinmis Dosyalar")
    d5.show()
    kaydet(d5, "10-silinmis-dosyalar.png")
    d5.close()

    kayiplar = [
        LostPartition(2048, 1048576, "FAT32", "SISTEM"),
        LostPartition(1050624, 2097152, "NTFS", ""),
        LostPartition(3147776, 1228800, "exFAT", "PAYLASIM"),
    ]
    d6 = LostPartitionsDialog(kayiplar, pencere)
    d6.show()
    kaydet(d6, "11-kayip-bolumler.png")
    d6.close()

    d7 = CarveOptionsDialog(pencere, "Bolum 2 (Linux kok, 1.00 GB)")
    d7.show()
    kaydet(d7, "12-imza-tarama.png")
    d7.close()

    bulunanlar = [
        CarvedFile("jpg", "JPEG goruntu", 0x120000, 2458624, "jpg"),
        CarvedFile("png", "PNG goruntu", 0x4A0000, 884736, "png"),
        CarvedFile("pdf", "PDF belgesi", 0x9C0000, 5242880, "pdf"),
    ]
    d8 = CarvedFilesDialog(bulunanlar, pencere)
    d8.show()
    kaydet(d8, "13-bulunan-dosyalar.png")
    d8.close()

    # --- coklu goruntu: ikinci bir imaj acilinca ilki listede kalmali ---
    ikinci = os.path.join(scratch("ui"), "ikinci.img")
    s2 = DiskSession.create(ikinci, 200 * MIB, scheme="mbr", overwrite=True)
    r2 = s2.free_regions()[0]
    s2.create_partition(r2.start_lba, 120 * MIB // 512, fs_key="fat32",
                        label="IKINCI")
    s2.close()
    pencere.open_path(ikinci)
    app.processEvents()
    kok_sayisi = pencere.tree.topLevelItemCount()
    print(f"  (coklu goruntu: {len(pencere.sessions)} oturum, "
          f"{kok_sayisi} agac koku)")
    assert len(pencere.sessions) == 2, "ikinci goruntu acilinca ilki kapanmis"
    kaydet(pencere, "15-coklu-goruntu.png")

    bilgi = dict(platform_summary())
    bilgi["Qt platformu"] = app.platformName()
    bilgi["Arayuz stili"] = app.style().objectName()
    d9 = InfoDialog("Sistem Bilgisi", bilgi, pencere,
                    note="FAT12/16/32 ve exFAT saf Python ile her platformda calisir.")
    d9.show()
    kaydet(d9, "14-sistem-bilgisi.png")
    d9.close()

    pencere.close_image()
    from PyQt5.QtGui import QFontDatabase
    aile_sayisi = len(QFontDatabase().families())
    print(f"\nPlatform: {app.platformName()} | stil: {app.style().objectName()} "
          f"| font ailesi: {aile_sayisi}")
    if aile_sayisi == 0:
        print("UYARI: Hicbir font yuklenemedi; goruntulerde metin GORUNMEZ.")
        print("       Gercek cizim icin: DISKULTIMATE_QPA=windows (veya xcb)")
    print("Duman testi tamamlandi.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
