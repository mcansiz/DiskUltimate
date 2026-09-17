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
# Yetki yukseltme teklifi modal bir penceredir; otomatik kosumu kilitler.
os.environ["DISKULTIMATE_NO_ELEVATION_PROMPT"] = "1"

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from diskultimate import i18n  # noqa: E402
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
from diskultimate.ui.main_window import (MainWindow, TAB_HEX,  # noqa: E402
                                         TAB_INFO)
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


def _sozde_diyaloglar(pencere):
    """Sozde dil denetimi icin olusturulan diyaloglar.

    Duman testinin geri kalaninda zaten kurulan pencereler; burada **sozde
    dilde** yeniden kurulup metinleri denetleniyor.
    """
    from diskultimate.core.ptable import FreeRegion

    oturum = pencere.session
    bolum = oturum.table.get(1)
    diyaloglar = [
        NewImageDialog(pencere, os.path.dirname(oturum.path)),
        CreatePartitionDialog(FreeRegion(6014976, 2373598, 512), "gpt", pencere),
        FormatDialog(bolum, pencere),
        # Cagiranin verdigi metinler gercek kodda da `tr()` ile uretilir;
        # fixture de oyle davranmali, yoksa denetim kendi verisine takilir.
        WipeDialog(pencere, i18n.tr("Bolum {}", 1), 512 * MIB,
                   allow_free_space=True),
        CarveOptionsDialog(pencere, i18n.tr("Bolum {}", 1)),
        InfoDialog(i18n.tr("Sistem Bilgisi"), dict(platform_summary()), pencere,
                   note=i18n.tr("Tanilama notu")),
    ]
    try:
        diyaloglar.append(ResizePartitionDialog(
            bolum, oturum.resize_window(bolum.index),
            oturum.resize_info(bolum.index), align_sectors=2048,
            used_bytes=-1, parent=pencere))
    except Exception:
        pass            # boyutlandirilamayan bolumde bu diyalog acilmaz
    return diyaloglar


def sozde_denetimi(app, hedef: str, kaynak=None) -> None:
    """Sozde-yerellestirme ile **sarilmamis** metni calisma aninda arar.

    Statik denetim (`tests/i18n_check.py`) yalnizca `tr()`/`mark()` ile
    isaretlenmis metni gorur; hic sarilmamis olani bilemez. Sozde dilde ise
    sarili her metin `[!...!]` bicimine girer — **girmeyen** metin ya
    sarilmamistir ya da veridir (dosya adi, boyut, dosya sistemi adi).

    Iki duzey:
      * **Kesin denetim** — menu basliklari, eylem metinleri, sekme basliklari
        ve tablo sutunlari saf arayuz metnidir, veri icermez: hepsi sozde
        olmali. Olmayan varsa test **duser**.
      * **Bilgi listesi** — kalan metinler basilir; cogu veridir, gozle bakilir.
    """
    from PyQt5.QtWidgets import (QAbstractButton, QGroupBox, QLabel, QMenu,
                                 QTabWidget)

    onceki = i18n.current_language()
    i18n.set_language(i18n.PSEUDO_LANGUAGE, remember=False)
    pencere = MainWindow()
    pencere._disk_timer.stop()          # yoklama bu denetimde gereksiz
    pencere.resize(1280, 800)
    app.processEvents()

    def sozde(metin: str) -> bool:
        return not metin.strip() or metin.startswith("[!")

    kesin_hata = []

    # 1) eylemler ve menuler
    for ad, nesne in vars(pencere).items():
        if ad.startswith("act_") and hasattr(nesne, "text"):
            if not sozde(nesne.text()):
                kesin_hata.append(f"eylem {ad}: {nesne.text()!r}")
            ipucu = nesne.toolTip()
            if ipucu and not sozde(ipucu):
                kesin_hata.append(f"ipucu {ad}: {ipucu[:50]!r}")
    for menu, _kaynak in pencere._menus:
        if not sozde(menu.title()):
            kesin_hata.append(f"menu: {menu.title()!r}")

    # 2) sekmeler ve tablo sutunlari
    for i in range(pencere.tabs.count()):
        if not sozde(pencere.tabs.tabText(i)):
            kesin_hata.append(f"sekme {i}: {pencere.tabs.tabText(i)!r}")
    for i in range(pencere.part_table.columnCount()):
        baslik = pencere.part_table.horizontalHeaderItem(i)
        if baslik and not sozde(baslik.text()):
            kesin_hata.append(f"sutun {i}: {baslik.text()!r}")
    if not sozde(pencere.tree.headerItem().text(0)):
        kesin_hata.append(f"agac basligi: {pencere.tree.headerItem().text(0)!r}")

    # 3) bilgi listesi: kalan gorunur metinler
    kalan = []
    for w in pencere.findChildren((QLabel, QAbstractButton, QGroupBox, QMenu,
                                   QTabWidget)):
        for metin in (getattr(w, "text", lambda: "")(),
                      getattr(w, "title", lambda: "")()):
            if isinstance(metin, str) and not sozde(metin) \
                    and sum(c.isalpha() for c in metin) >= 3:
                kalan.append(metin.strip()[:60])

    # 4) diyaloglar — arayuz metninin buyuk bolumu burada ve sozde dilde
    #    **yeniden kuruluyorlar**, yani sarilmamis metin hemen belli olur.
    if kaynak is not None and kaynak.session is not None:
        for diyalog in _sozde_diyaloglar(kaynak):
            app.processEvents()
            ad = type(diyalog).__name__
            for w in diyalog.findChildren((QLabel, QAbstractButton, QGroupBox)):
                for metin in (getattr(w, "text", lambda: "")(),
                              getattr(w, "title", lambda: "")()):
                    if isinstance(metin, str) and not sozde(metin) \
                            and sum(c.isalpha() for c in metin) >= 3:
                        kalan.append(f"{ad}: {metin.strip()[:55]}")
            if not sozde(diyalog.windowTitle()):
                kesin_hata.append(f"{ad} basligi: {diyalog.windowTitle()!r}")
            diyalog.close()

    pencere.close()
    i18n.set_language(onceki, remember=False)
    app.processEvents()

    if kalan:
        print(f"  (sozde dil: {len(set(kalan))} metin sarilmamis GORUNUYOR — "
              "cogu veri olabilir)")
        for metin in sorted(set(kalan))[:12]:
            print(f"     ? {metin}")
    assert not kesin_hata, ("sozde dilde cevrilmemis arayuz metni:\n  "
                            + "\n  ".join(kesin_hata))
    print("  (sozde-yerellestirme: eylem/menu/sekme/sutun metinleri tamam)")


def dil_denetimi(pencere, app, hedef: str) -> None:
    """Dil degisimi arayuzde gercekten uygulaniyor mu? (ADR 0027)

    Duman testinin geri kalani Turkce kosar; burada dil gecici olarak
    degistirilir, metinlerin **yerinde** yenilendigi dogrulanir ve kaynak dile
    donulur. Uygulamanin yeniden baslatilmasi gerekmez — gerekseydi acik disk
    kapanirdi.
    """
    diller = [kod for kod, _ad in i18n.available_languages()
              if kod != i18n.SOURCE_LANGUAGE]
    if not diller:
        print("  (ceviri dosyasi yok; dil denetimi atlandi)")
        return
    onceki_uygula = pencere.act_apply.text()
    onceki_sekme = pencere.tabs.tabText(0)
    for kod in diller:
        i18n.set_language(kod, remember=False)
        app.processEvents()
        assert pencere.act_apply.text() != onceki_uygula, \
            f"{kod}: arac cubugu metni degismedi ({pencere.act_apply.text()})"
        assert pencere.tabs.tabText(0) != onceki_sekme, \
            f"{kod}: sekme basligi degismedi ({pencere.tabs.tabText(0)})"
        assert pencere.tree.headerItem().text(0), f"{kod}: agac basligi bos"
        # Menu basliklari ve bolum tablosu sutunlari da yenilenmeli
        basliklar = [m.title() for m, _kaynak in pencere._menus]
        assert all(basliklar), f"{kod}: bos menu basligi: {basliklar}"
        sutun = pencere.part_table.horizontalHeaderItem(0).text()
        assert sutun, f"{kod}: bos sutun basligi"
        app.processEvents()
        pencere.grab().save(os.path.join(hedef, f"24-dil-{kod}.png"))
        print(f"  24-dil-{kod}.png")
    i18n.set_language(i18n.SOURCE_LANGUAGE, remember=False)
    app.processEvents()
    assert pencere.act_apply.text() == onceki_uygula, \
        f"kaynak dile donulunce metin geri gelmedi: {pencere.act_apply.text()}"
    assert pencere.tabs.tabText(0) == onceki_sekme, pencere.tabs.tabText(0)
    print(f"  (dil degisimi: {', '.join(diller)} denendi, Turkce'ye donuldu)")


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
    pencere.tabs.setCurrentIndex(TAB_INFO)
    kaydet(pencere, "03-bolum-bilgisi.png")
    pencere.tabs.setCurrentIndex(TAB_HEX)
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
    bar._dragging = "sag"
    bar._apply(bar.start, max(bar.min_count, bar.count // 2))
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

    # --- kopyalama ilerleme penceresi ---
    # Buyuk bir dosya bolume yazilirken hicbir sey gosterilmiyordu; kullanici
    # bunu donma sanmisti (ADR 0021). Pencere gercekten cizilsin diye burada
    # goruntusu alinir.
    from diskultimate.ui.dialogs.task import TaskDialog

    def sahte_kopya(report):
        report("sdcard.img.dub yaziliyor (1.09 GB)...", 42)
        return 1

    d9 = TaskDialog(pencere, "Kopyalaniyor — 1.09 GB", sahte_kopya)
    d9.show()
    d9._on_progress("sdcard.img.dub yaziliyor (1.09 GB)...", 42)
    kaydet(d9, "17-kopyalama-ilerleme.png")
    assert d9.bar.value() == 42, d9.bar.value()
    # Belirsiz kip: tek adimlik islemde cubuk hareket eder, 0'da takili kalmaz
    d9._on_progress("sdcard.img.dub yaziliyor (1.09 GB)...", -1)
    assert d9.bar.maximum() == 0, "belirsiz kipe gecilmedi"
    d9._on_progress("Tamamlaniyor...", 100)
    assert d9.bar.maximum() == 100 and d9.bar.value() == 100, "yuzdeye donulmedi"
    d9.close()
    print("  (ilerleme penceresi: yuzde ve belirsiz kip denetlendi)")

    # --- onyukleyici yoneticisi (ADR 0028) ---
    # Inceleme pencereden ONCE calisir; pencere hazir sonucu alir. Boylece
    # yapici icinde modal ilerleme penceresi acilmaz.
    from diskultimate.core import bootloader as bl
    from diskultimate.ui.dialogs.bootloader import BootloaderDialog

    rapor = bl.survey_session(pencere.session)
    d10 = BootloaderDialog(pencere, pencere.session, pencere.enqueue)
    d10.adopt(rapor)
    d10.show()
    kaydet(d10, "25-onyukleyici-yonetici.png")
    assert d10.os_tree.topLevelItemCount() == len(rapor.systems), \
        f"{d10.os_tree.topLevelItemCount()} satir, {len(rapor.systems)} bolum"
    assert d10.state_form.rowCount() > 0, "onyukleme durumu bos"
    # GRUB kurulumu bu platformda yoksa dugme PASIF olmali ve nedeni yazmali
    if not d10.status.available:
        assert not d10.btn_install.isEnabled(), \
            "kurulamayacak platformda kurma dugmesi etkin"
        assert d10.btn_install.toolTip(), "neden kullanilamadigi yazilmadi"
    d10.close()
    print(f"  (onyukleyici: {len(rapor.systems)} bolum, onyukleme kodu "
          f"'{rapor.boot_code.kind}')")

    # --- UEFI onyukleme duzenleyici (ADR 0029) ---
    # Bellenime erisilemeyen makinede de acilmali ve NEDENINI soylemeli;
    # bos bir liste gostermek girisler silinmis gibi gorunurdu.
    from diskultimate.core import efiboot as efi
    from diskultimate.core import efistore
    from diskultimate.ui.dialogs.efiboot import EfiBootDialog

    sahte = efistore.BootState(firmware="uefi", readable=True, writable=False,
                               source="file",
                               reason="Yedek dosyasi dogrudan yazilamaz.")
    yol = [efi.make_hard_drive_node(2, 264192, 204800, "gpt",
                                    "a967f8c5-ed93-4c8d-927d-61ca376a5e9b")]
    sahte.entries["Boot"] = {
        0: efi.LoadOption(number=0, description="Windows Boot Manager",
                          path_nodes=yol + [efi.make_file_node(
                              "/EFI/Microsoft/Boot/bootmgfw.efi")]),
        1: efi.LoadOption(number=1, description="Linux Mint",
                          path_nodes=yol + [efi.make_file_node(
                              "/EFI/ubuntu/shimx64.efi")]),
        2: efi.LoadOption(number=2, description="UEFI Shell",
                          path_nodes=yol + [efi.make_file_node(
                              "/EFI/tools/shell.efi")]),
    }
    sahte.entries["Boot"][2].active = False
    sahte.orders["Boot"] = [1, 0]          # 2 sirada degil: gorunmeli ama '-'
    sahte.timeout = 5
    sahte.boot_current = 1

    d11 = EfiBootDialog(pencere)
    d11.adopt(sahte)
    d11.show()
    kaydet(d11, "26-uefi-onyukleme.png")
    assert d11.tree.topLevelItemCount() == 3, d11.tree.topLevelItemCount()
    sirasiz = [d11.tree.topLevelItem(i) for i in range(3)
               if d11.tree.topLevelItem(i).text(0) == "-"]
    assert len(sirasiz) == 1, "sirada olmayan giris isaretlenmedi"
    assert sirasiz[0].toolTip(0), "neden sirada olmadigi yazilmadi"
    # Yazilamayan kaynakta duzenleme dugmeleri pasif olmali
    assert not d11.btn_apply.isEnabled(), "yazilamaz durumda 'yaz' etkin"
    assert not d11.btn_delete.isEnabled(), "yazilamaz durumda 'sil' etkin"
    assert d11.btn_apply.toolTip(), "neden yazilamadigi yazilmadi"
    # Satirlar BootOrder'a gore siralanir: sira [1, 0] oldugu icin ilk satir
    # Boot0001'dir. Windows girisi konumla degil, numarasiyla bulunur.
    assert d11.tree.topLevelItem(0).text(1) == "Boot0001", \
        f"BootOrder'a gore siralanmadi: {d11.tree.topLevelItem(0).text(1)}"
    # Ad bilerek `hedef` degil: disaridaki `hedef` ekran goruntusu dizinidir
    # ve golgelemek sonraki `kaydet()` cagrisini bozar.
    windows_satiri = [d11.tree.topLevelItem(i) for i in range(3)
                      if d11.tree.topLevelItem(i).data(0, Qt.UserRole) == 0]
    assert windows_satiri, "Boot0000 listede yok"
    d11.tree.setCurrentItem(windows_satiri[0])
    app.processEvents()
    ayrinti = d11.detail.toPlainText()
    assert "bootmgfw.efi" in ayrinti, ayrinti[:200]
    assert "HD(2,GPT," in ayrinti, ayrinti[:200]
    d11.close()
    print("  (UEFI duzenleyici: 3 giris, sirasiz giris ve yazma kilidi "
          "denetlendi)")

    # --- bekleyen islem kuyrugu: ekle, goster, geri al, iptal ---
    # Kuyruk dolarken diske HICBIR SEY yazilmaz; bu, modelin tum guvenlik
    # gerekcesidir (ADR 0025), bu yuzden burada da olculur.
    from diskultimate.core import operations as ops

    kuyruk_oncesi = os.path.getsize(goruntu)
    sektor = pencere.session.image.sector_size
    pencere.enqueue(ops.format_op(1, "fat32", label="DENEME", fs_name="FAT32"))
    pencere.enqueue(ops.boot_op(1, True))
    pencere.enqueue(ops.delete_op(2, name="deneme"))
    app.processEvents()
    assert len(pencere.queue) == 3, len(pencere.queue)
    assert pencere.pending_view.topLevelItemCount() == 3
    assert pencere.act_apply.text() == "Uygula (3)", pencere.act_apply.text()
    assert pencere.act_apply.isEnabled() and pencere.act_discard.isEnabled()
    assert os.path.getsize(goruntu) == kuyruk_oncesi,         "kuyruga eklemek goruntuyu degistirdi"
    kaydet(pencere, "21-bekleyen-islemler.png")

    # Tabloda bekleyen isaret gorunmeli
    isaretli = [pencere.part_table.item(r, 0).text()
                for r in range(pencere.part_table.rowCount())
                if pencere.part_table.item(r, 0)
                and "⏳" in pencere.part_table.item(r, 0).text()]
    assert len(isaretli) == 2, f"bekleyen isaret sayisi: {isaretli}"

    pencere.undo_step()
    assert len(pencere.queue) == 2, "geri alma calismadi"
    pencere.queue.clear()
    pencere._refresh_pending()
    app.processEvents()
    assert pencere.pending_view.topLevelItemCount() == 0
    assert not pencere.act_apply.isEnabled(), "bos kuyrukta Uygula etkin kalmamali"
    isaretli = [r for r in range(pencere.part_table.rowCount())
                if pencere.part_table.item(r, 0)
                and "⏳" in pencere.part_table.item(r, 0).text()]
    assert not isaretli, "kuyruk bosalinca isaretler kalmamali"
    print("  (bekleyen islem kuyrugu: ekleme, isaret, geri alma, iptal denetlendi)")

    # --- salt okunur acilan kaynak yazma moduna gecebilmeli ---
    # Kuyruga girmeyen islemler (geri yukleme, klonlama) bunu kullanir.
    # Eksik oldugunda Linux'ta ".dub yedegini /dev/sdb diskine yaz" islemi
    # "salt okunur" hatasiyla dusuyordu (ADR 0025 gerilemesi).
    salt_yol = os.path.join(scratch("ui"), "saltokunur.img")
    s_ro = DiskSession.create(salt_yol, 64 * MIB, scheme="mbr", overwrite=True)
    s_ro.close()
    pencere.open_path(salt_yol)
    app.processEvents()
    pencere.session.close_filesystems()
    pencere.session.image.close()
    pencere.session.image = __import__(
        "diskultimate.core.vdisk", fromlist=["open_disk"]).open_disk(
            salt_yol, readonly=True)
    pencere.session.reload()
    assert pencere.session.readonly, "test salt okunur duruma getirmeli"
    assert pencere.session.can_become_writable()[0], "gecis mumkun olmali"
    assert pencere._make_writable(), "arayuz yazma moduna gecirememeli degil"
    assert not pencere.session.readonly, "_make_writable gecisi yapmadi"
    pencere.close_image()
    app.processEvents()
    print("  (salt okunur kaynak yazma moduna gecti)")

    # --- dosya gezgini: salt okunur kaynakta yazma yetkisi isteyebilmeli ---
    # Dosya islemleri kuyruga girmez; kaynak salt okunur acildigi icin yetkiyi
    # kendileri istemek zorunda. Yoksa fiziksel diske dosya eklenemiyordu
    # (Linux Mint, kullanici bildirimi — ADR 0025 gerilemesi).
    assert pencere.browser.ensure_writable is not None,         "dosya gezgini yazma yetkisi isteyemiyor"
    yazma_yolu = os.path.join(scratch("ui"), "gezgin-salt.img")
    s_g = DiskSession.create(yazma_yolu, 128 * MIB, scheme="mbr", overwrite=True)
    r_g = s_g.free_regions()[0]
    s_g.create_partition(r_g.start_lba, 64 * MIB // 512, fs_key="fat32",
                         label="GEZGIN")
    s_g.close()
    pencere.open_path(yazma_yolu)
    app.processEvents()
    # Kaynagi salt okunur duruma getir
    pencere.session.close_filesystems()
    pencere.session.image.close()
    pencere.session.image = __import__(
        "diskultimate.core.vdisk", fromlist=["open_disk"]).open_disk(
            yazma_yolu, readonly=True)
    pencere.session.reload()
    pencere.select_partition(1)
    app.processEvents()
    assert not pencere.browser.fs.writable, "test salt okunur baslamali"
    # Yazma dugmeleri PASIF OLMAMALI: yetki ilk denemede istenir
    assert pencere.browser.act_import.isEnabled(),         "salt okunur kaynakta 'Dosya ekle' pasif kalmamali"
    assert pencere.browser._require_writable(), "yazma yetkisi alinamadi"
    assert pencere.browser.fs.writable, "gezgin taze dosya sistemine baglanmadi"
    icerik = b"tamam\n"
    pencere.browser.fs.write_file("/gezgin.txt", icerik)
    pencere.browser.fs.flush()
    assert pencere.browser.fs.read("/gezgin.txt") == icerik
    pencere.close_image()
    app.processEvents()
    print("  (dosya gezgini: salt okunur kaynakta yazma yetkisi alindi)")

    # --- ikon seti: hepsi cizilebilmeli ---
    from diskultimate.ui import icons

    bos_olanlar = [ad for ad in icons.names()
                   if icons.icon(ad).pixmap(24, 24).isNull()]
    assert not bos_olanlar, f"cizilemeyen ikon: {bos_olanlar}"
    # Cok boyutlu olmali: arac cubugu 24 isterken 18'lik goruntu olceklenip
    # bulaniklasmamali.
    for boyut in (16, 24, 32):
        pix = icons.icon("apply").pixmap(boyut, boyut)
        assert pix.width() == boyut, f"{boyut} px istendi, {pix.width()} geldi"
    # "Her platformda ayni" olcusu: cizimlerin PIKSEL ozeti. Windows ve
    # Linux'ta ayni deger cikmalidir (olculdu, ADR 0025).
    print(f"  ({len(icons.names())} ikon cizildi — "
          f"cizim ozeti {icons.digest()})")

    # --- yedek dosyasi bilgisi: icerik listesi + "gez" dugmesi ---
    from diskultimate.core.clone import backup
    from diskultimate.ui.dialogs.tools import BackupInfoDialog

    dub_yolu = os.path.join(scratch("ui"), "ornek.dub")
    kaynak_oturum = DiskSession.open(goruntu, readonly=True)
    backup(kaynak_oturum.image, dub_yolu, compress=True)
    kaynak_oturum.close()
    onizleme = DiskSession.backup_preview(dub_yolu)
    d10 = BackupInfoDialog(onizleme, pencere)
    d10.show()
    kaydet(d10, "18-yedek-bilgisi.png")
    assert d10.tree.topLevelItemCount() == len(onizleme.partitions), \
        "yedek icerigi agaca yansimadi"
    assert not d10.browse, "gez dugmesine basilmadan istek olusmamali"
    d10._browse()
    assert d10.browse, "gez dugmesi istegi kaydetmedi"
    d10.close()
    print(f"  (yedek bilgisi: {d10.tree.topLevelItemCount()} bolum listelendi, "
          "gez dugmesi calisiyor)")

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
    kaydet(pencere, "14-coklu-goruntu.png")

    bilgi = dict(platform_summary())
    bilgi["Qt platformu"] = app.platformName()
    bilgi["Arayuz stili"] = app.style().objectName()
    d9 = InfoDialog("Sistem Bilgisi", bilgi, pencere,
                    note="FAT12/16/32 ve exFAT saf Python ile her platformda calisir.")
    d9.show()
    kaydet(d9, "15-sistem-bilgisi.png")
    d9.close()

    # ADR 0013 ozel temayi kaldirdi ama `theme.STYLESHEET` ileride "Tema"
    # bolumu icin bilerek birakildi ve `DISKULTIMATE_THEME=diskultimate` ile
    # halen acilabiliyor. Bu dal hicbir testten gecmiyordu; ADR 0012'deki
    # sekme kirpilmasi tuzagi fark edilmeden geri gelebilirdi.
    from diskultimate.ui.theme import THEMES
    assert set(THEMES) == {"system", "diskultimate"}, THEMES
    onceki = app.styleSheet()
    try:
        secilen = apply_theme(app, "diskultimate")
        assert secilen == "diskultimate", secilen
        assert app.styleSheet(), "diskultimate temasi stil sayfasi uygulamadi"
        app.processEvents()
        tema_penceresi = MainWindow()
        tema_penceresi.resize(1400, 860)
        tema_penceresi.open_path(goruntu)
        app.processEvents()
        # ADR 0012: stil sayfasindaki font-weight sekme basliklarini kirpmisti.
        #
        # `tabSizeHint` korumali (protected) bir islevdir. PyQt5'in bazi
        # surumleri Python'da olusturulmamis bir nesnede buna izin vermez ve
        # `RuntimeError` atar (Ubuntu 24.04 / PyQt5 5.15 boyle). O zaman bu tek
        # denetim atlanir; testin geri kalani kosmaya devam eder — eskiden tum
        # duman testi burada duruyordu ve sonraki adimlar hic calismiyordu.
        cubuk = tema_penceresi.tabs.tabBar()
        try:
            for i in range(cubuk.count()):
                gereken = cubuk.tabSizeHint(i).width()
                gercek = cubuk.tabRect(i).width()
                assert gercek + 1 >= gereken, (
                    f"sekme {i} ({cubuk.tabText(i)!r}) kirpildi: "
                    f"{gercek}px < gereken {gereken}px")
            sonuc = f"{cubuk.count()} sekme kirpilmadi"
        except RuntimeError as exc:
            sonuc = f"sekme genisligi olculemedi, atlandi ({exc})"
        kaydet(tema_penceresi, "16-tema-diskultimate.png")
        tema_penceresi.close_all()
        tema_penceresi.close()
        print(f"  (tema dali denetlendi: {sonuc})")
    finally:
        apply_theme(app, "system")
        app.setStyleSheet(onceki)
        app.processEvents()

    # --- takilan/cikarilan aygit agaca yansiyor mu? ---
    # Gercek diske DOKUNULMAZ: listeleme islevi sahte bir listeyle degistirilip
    # takma/cikarma taklit edilir. USB/SD uygulama acikken takilabildigi icin
    # liste kendiliginden tazelenmeli (onceden yalnizca acilista kuruluyordu).
    from diskultimate.core.physical import DiskInfo

    def _sahte_disk(no: int, model: str, size: int) -> DiskInfo:
        d = DiskInfo(path=f"/sahte/disk{no}", name=f"SahteDisk{no}")
        d.size, d.model, d.removable, d.bus = size, model, True, "USB"
        return d

    gercek_listeleme = DiskSession.list_physical_disks
    durum = {"liste": [_sahte_disk(0, "Dahili", 512 * 1024 ** 3)]}
    DiskSession.list_physical_disks = staticmethod(
        lambda *a, **k: list(durum["liste"]))
    def tarama_bekle(window) -> None:
        """Arka plandaki disk taramasinin bitmesini ve sonucun islenmesini bekler.

        Tarama artik arayuz is parcaciginda yapilmiyor (donma onlemi), bu
        yuzden test de sonucu beklemek zorunda: once is parcacigi biter,
        sonra kuyruga alinmis sinyal `processEvents` ile islenir.
        """
        window.start_disk_scan()
        if window._scanner is not None:
            window._scanner.wait(5000)
        for _ in range(10):
            app.processEvents()

    try:
        hp = MainWindow()
        tarama_bekle(hp)
        # --- Acronis vari gorunum: diskin altinda bolumleri ---
        # Bolumleri gormek icin diski ACMAK GEREKMEZ (ADR 0026). Sahte bir
        # yoklama sonucu verilir; gercek diske dokunulmaz.
        from diskultimate.core.ptable import Partition
        from diskultimate.core.session import DiskSurvey

        def _sahte_bolum(no, fs, etiket, lba, sayi):
            p = Partition(index=no, start_lba=lba, sector_count=sayi,
                          scheme="mbr")
            p.fs_type, p.fs_label = fs, etiket
            return p

        hp._surveys["/sahte/disk0"] = DiskSurvey(
            path="/sahte/disk0", scheme="mbr",
            partitions=[_sahte_bolum(1, "FAT32", "ONYUKLEME", 2048, 200 * 2048),
                        _sahte_bolum(2, "NTFS", "VERI", 411648, 900 * 2048)])
        hp._build_tree([])
        hp._refresh_overview()
        app.processEvents()
        disk_dugumu = hp.tree.topLevelItem(0).child(0)
        assert disk_dugumu.childCount() == 2,             f"diskin altinda bolumler gorunmedi: {disk_dugumu.childCount()}"
        assert "ONYUKLEME" in disk_dugumu.child(0).text(0),             disk_dugumu.child(0).text(0)
        tur, deger = disk_dugumu.child(1).data(0, Qt.UserRole)
        assert tur == "physpart" and deger == ("/sahte/disk0", 2), (tur, deger)
        # Genel bakis seridi de ayni bolumleri cizmeli
        hp.disk_overview.resize(900, 200)
        hp.disk_overview._layout()
        assert len(hp.disk_overview._blocks) == 2, hp.disk_overview._blocks
        hp.grab().save(os.path.join(hedef, "23-disk-genel-bakis.png"))
        print("  23-disk-genel-bakis.png")
        print("  (disk agaci: bolumler acilmadan gorunuyor)")

        # --- acilan fiziksel disk YERINDE kalmali, asagida ikinci dal olmamali ---
        # Eskiden disk acilinca satirinda "(asagida acik)" yazip bolumleri
        # agacin altinda ikinci bir dala tasiyordu (ADR 0026 duzeltmesi).
        sahte_oturum = pencere.session          # acik bir goruntu oturumu
        kok_sayisi_once = hp.tree.topLevelItemCount()
        hp.sessions.append(sahte_oturum)
        hp.session = sahte_oturum
        try:
            hp._build_tree([])
            app.processEvents()
            # Goruntu dosyasi fiziksel disk listesinde olmadigi icin kendi
            # dalinda gorunur; fiziksel disk olsaydi gorunmeyecekti.
            assert hp.tree.topLevelItemCount() == kok_sayisi_once + 1,                 "goruntu oturumu kendi dalinda gorunmeli"
            metinler = [hp.tree.topLevelItem(i).text(0)
                        for i in range(hp.tree.topLevelItemCount())]
            assert not any("asagida acik" in m for m in metinler), metinler
        finally:
            hp.sessions.remove(sahte_oturum)
            hp.session = None
        print("  (acik kaynak agacta yerinde: '(asagida acik)' yok)")
        hp._surveys.clear()
        kok = lambda: hp.tree.topLevelItem(0).text(0)  # noqa: E731
        assert "(1)" in kok(), kok()
        durum["liste"].append(_sahte_disk(1, "SD/MMC kart", 59 * 1024 ** 3))
        tarama_bekle(hp)
        assert "(2)" in kok(), f"takilan aygit agaca eklenmedi: {kok()}"
        imza = hp._last_disk_signature
        tarama_bekle(hp)
        assert hp._last_disk_signature == imza, "degisiklik yokken agac yenilendi"
        durum["liste"].pop()
        tarama_bekle(hp)
        assert "(1)" in kok(), f"cikarilan aygit agactan silinmedi: {kok()}"
        gunluk = hp.log_view.toPlainText()
        assert "Aygit takildi" in gunluk and "Aygit cikarildi" in gunluk
        hp.close()
        print("  (aygit takma/cikarma: agac kendiliginden tazelendi)")
    finally:
        DiskSession.list_physical_disks = gercek_listeleme

    dil_denetimi(pencere, app, hedef)
    sozde_denetimi(app, hedef, pencere)

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
