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

from PyQt5.QtCore import QRect, Qt  # noqa: E402
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
    from diskultimate.core import operations as ops
    from diskultimate.ui.dialogs.apply import ApplyDialog

    kuyruk = ops.OperationQueue()
    kuyruk.add(ops.format_op(1, "fat32", label="SOZDE", fs_name="FAT32"))
    kuyruk.add(ops.delete_op(2, "Linux kok"))
    diyaloglar.append(ApplyDialog(pencere, oturum, kuyruk))

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
    from PyQt5.QtWidgets import QMessageBox

    def dugmeler():
        """Standart onay kutusunun Evet/Hayir yazilari (& isareti atilir)."""
        kutu = QMessageBox(QMessageBox.Question, "t", "t",
                           QMessageBox.Yes | QMessageBox.No)
        metin = [kutu.button(b).text().replace("&", "")
                 for b in (QMessageBox.Yes, QMessageBox.No)]
        kutu.deleteLater()
        return metin

    # Kullanici bildirimi: Turkce arayuzde "Yes / No" gorunuyordu
    assert dugmeler() == ["Evet", "Hayir"], f"Turkce dugmeler: {dugmeler()}"
    onceki_uygula = pencere.act_apply.text()
    onceki_sekme = pencere.tabs.tabText(0)
    beklenen_dugme = {"en": ["Yes", "No"], "de": ["Ja", "Nein"]}
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
        if kod in beklenen_dugme:
            assert dugmeler() == beklenen_dugme[kod], \
                f"{kod}: standart dugmeler {dugmeler()}"
        app.processEvents()
        pencere.grab().save(os.path.join(hedef, f"24-dil-{kod}.png"))
        print(f"  24-dil-{kod}.png")
    i18n.set_language(i18n.SOURCE_LANGUAGE, remember=False)
    app.processEvents()
    assert pencere.act_apply.text() == onceki_uygula, \
        f"kaynak dile donulunce metin geri gelmedi: {pencere.act_apply.text()}"
    assert pencere.tabs.tabText(0) == onceki_sekme, pencere.tabs.tabText(0)
    assert dugmeler() == ["Evet", "Hayir"], f"geri donuste: {dugmeler()}"
    print(f"  (dil degisimi: {', '.join(diller)} denendi, Turkce'ye donuldu)")


def qt_metin_denetimi(qt_i18n, durum: dict) -> None:
    """Dugme cevirmeni Qt'nin KENDI metinlerini bosaltmamali.

    2026-10-01: cevirmen bilmedigi metne "" donduruyordu; PyQt bunu gecerli
    (bos) ceviri sayip dosya penceresinin butun etiketlerini sildi ve her
    acilista "QString::arg: Argument missing" uyarisi basti (ADR 0073).
    """
    from PyQt5.QtCore import QCoreApplication
    ornekler = (("QFileDialog", "File &name:"), ("QFileDialog", "%1 File"),
                ("QFileDialog", "Look in:"), ("QFileSystemModel", "Name"))
    for ctx, kaynak in ornekler:
        sonuc = QCoreApplication.translate(ctx, kaynak)
        assert sonuc, f"Qt metni bosaldi: {ctx} {kaynak!r}"
        if "%1" in kaynak:
            assert "%1" in sonuc, f"yer tutucu kayip: {ctx} {kaynak!r} -> {sonuc!r}"
    iptal = QCoreApplication.translate("QDialogButtonBox", "&Cancel")
    assert iptal, "dugme cevirisi bos"
    if durum["qtbase"] and durum["language"] not in ("en", "qps"):
        assert QCoreApplication.translate("QFileDialog", "File &name:") != "File &name:", \
            "qtbase yuklendi ama dosya penceresi cevrilmedi"
    print("  (Qt metinleri bos degil, yer tutucular yerinde)")


def tahmin_denetimi() -> None:
    """Ilerleme penceresi: gecen ve kalan sure (sahte saatle)."""
    from diskultimate.ui.dialogs.task import Estimator, clock_text
    assert clock_text(7) == "0:07" and clock_text(760) == "12:40"
    assert clock_text(3909) == "1:05:09"
    saat = [100.0]
    e = Estimator(clock=lambda: saat[0])
    e.update(0)
    saat[0] += 1
    e.update(0.5)
    assert e.remaining() is None                # erken: tahmin yok
    saat[0] += 9
    e.update(25)                                 # 10 sn'de %25
    assert abs(e.remaining() - 30) < 0.01, e.remaining()
    assert "0:30" in e.text() and "0:10" in e.text(), e.text()
    e.update(5)                                  # yeni asama: olcum yeniden
    assert e.remaining() is None
    e.update(-1)                                 # belirsiz: yalnizca gecen sure
    assert e.remaining() is None and "0:10" in e.text()
    print("  (ilerleme penceresi: gecen/kalan sure dogru)")


def klon_hedefi_denetimi() -> None:
    """Diskten diske klon penceresi: uygunsuz disk secilemez, onaylar zorunlu."""
    from types import SimpleNamespace
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QDialogButtonBox
    from diskultimate.core.disksource import DiskSource
    from diskultimate.ui.dialogs.clone_target import CloneTargetDialog

    def disk(name, size, **kw):
        base = dict(path=f"/dev/{name}", name=name, size=size, model="TEST",
                    info_complete=True, readonly=False, is_system=False, mounted=[])
        base.update(kw)
        return DiskSource(kind="physical", path=base["path"], label=name,
                          size=size, disk=SimpleNamespace(**base))
    kaynak = disk("sda", 100 * MIB)
    hedefler = [kaynak, disk("sdb", 50 * MIB), disk("sdc", 200 * MIB, info_complete=False),
                disk("sdd", 200 * MIB, is_system=True, mounted=["/"]),
                disk("sde", 300 * MIB, mounted=["/mnt/x"])]
    d = CloneTargetDialog(None, "sda", "/dev/sda", 100 * MIB, hedefler, pending_steps=2)
    tamam = d.buttons.button(QDialogButtonBox.Ok)
    etkin = [bool(d.list.item(i).flags() & Qt.ItemIsEnabled) for i in range(d.list.count())]
    assert etkin == [False, False, False, True, True], etkin
    assert not tamam.isEnabled()
    d.list.setCurrentRow(4)                      # sde: bagli bolum, buyuk
    assert not tamam.isEnabled(), "silme onayi olmadan etkin"
    assert "/mnt/x" in d.warn.text() and "200.00 MB" in d.warn.text(), d.warn.text()
    d.confirm.setChecked(True)
    assert tamam.isEnabled() and not d.allow_system
    d.list.setCurrentRow(3)                      # sdd: sistem diski
    assert not tamam.isEnabled(), "sistem diski adi yazilmadan etkin"
    d.name_edit.setText("sdd")
    assert tamam.isEnabled() and d.allow_system
    assert d.selected().disk.name == "sdd"
    d.close()
    print("  (klon hedefi: uygunsuz diskler gri, silme onayi ve sistem diski adi zorunlu)")


def main() -> int:
    hedef = scratch("screenshots")
    goruntu = ornek_goruntu()
    app = QApplication(sys.argv)
    apply_theme(app, os.environ.get("DISKULTIMATE_THEME", "system"))
    # Ikon main.py ile AYNI islevden gelir: boylece test gercek kod yolunu
    # sinar, kendi taklidini degil.
    from diskultimate.ui.appicon import apply_app_icon
    assert apply_app_icon(app), "uygulama ikonu ayarlanamadi"
    # Qt'nin kendi metinleri (Evet/Hayir) — main.py ile ayni kurulum
    from diskultimate.ui import qt_i18n
    qt_durumu = qt_i18n.install()
    print(f"  (Qt cevirisi: qtbase {'yuklendi' if qt_durumu['qtbase'] else 'yok'}"
          f", dugmeler sozlukten)")
    qt_metin_denetimi(qt_i18n, qt_durumu)
    tahmin_denetimi()
    klon_hedefi_denetimi()

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

    # --- plan onizlemesi ve uygulama penceresi (ADR 0031) ---
    # Kuyruga adim eklenince ana ekran **planlanan** yerlesimi gostermeli:
    # yeni bolum haritada belirir, silinen kaybolur. Eskiden ekran hic
    # degismiyordu ve kullanici ne olacagini goremiyordu.
    from diskultimate.core import operations as ops
    from diskultimate.ui.dialogs.apply import ApplyDialog

    once_bolum = len(pencere.session.partitions)
    bos_alan = pencere.session.free_regions()[0]
    pencere.enqueue(ops.create_op(bos_alan.start_lba, 400 * MIB // 512, 512,
                                  fs_key="ntfs", label="PLANLI"))
    pencere.enqueue(ops.delete_op(3, "Windows Veri"))
    pencere.enqueue(ops.format_op(1, "exfat", label="YENIDEN",
                                  fs_name="exFAT"))
    app.processEvents()
    assert pencere.plan_bar.isVisible(), "plan seridi gorunmedi"
    plan = pencere._plan_layout
    assert plan is not None, "plan hesaplanmadi"
    assert len(plan.partitions) == once_bolum, (
        f"planlanan bolum sayisi yanlis: {len(plan.partitions)}")
    assert any(p.plan_state == "new" for p in plan.partitions), "yeni bolum yok"
    assert not any(p.index == 3 for p in plan.partitions), "silinen bolum durdu"
    # Bekleyen "yeni bolum" adimi bos alani **tutar**: bir sonraki bolum onun
    # uzerine kurulmamali (ADR 0034). Eskiden ucu de ayni LBA'ya kuruluyordu.
    kalan = pencere.planned_free_regions()
    yeni_bas = bos_alan.start_lba
    yeni_son = yeni_bas + 400 * MIB // 512 - 1
    cakisan = [r for r in kalan
               if not (r.end_lba < yeni_bas or r.start_lba > yeni_son)]
    assert not cakisan, (
        "planlanan bolum bos alani tutmadi: "
        f"{[(r.start_lba, r.end_lba) for r in cakisan]}")
    kaydet(pencere, "31-plan-onizlemesi.png")

    # Diskteki hale donunce gercek yerlesim gorunur
    pencere.toggle_plan_preview()
    app.processEvents()
    assert not pencere._show_plan
    assert len(pencere.session.partitions) == once_bolum
    kaydet(pencere, "32-diskteki-hali.png")
    pencere.toggle_plan_preview()
    app.processEvents()

    d11 = ApplyDialog(pencere, pencere.session, pencere.queue)
    d11.show()
    kaydet(d11, "33-uygula-adimlar.png")
    assert d11.table.rowCount() == len(pencere.queue), "adim listesi eksik"
    # Calisiyor / bitti / hata durumlari cizilebilmeli (is parcacigi
    # baslatmadan, dogrudan sinyal isleyicileri cagrilarak)
    d11._on_step_started(0)
    d11._on_step_progress(0, "Bicimlendiriliyor...", 45)
    d11._on_overall("1/3 — Bolum 1 bicimlendir", 15)
    kaydet(d11, "34-uygula-calisirken.png")
    d11._on_step_done(0, "")
    d11._on_step_started(1)
    d11._on_step_done(1, "Bolum bulunamadi")
    app.processEvents()
    assert d11.table.item(2, 2).text() == i18n.tr("Calistirilmadi"), \
        "duran adimdan sonrasi 'calistirilmadi' isaretlenmedi"
    kaydet(d11, "35-uygula-durdu.png")
    d11.close()
    pencere.queue.clear()
    pencere._refresh_pending()
    app.processEvents()
    assert not pencere.plan_bar.isVisible(), "kuyruk bosalinca serit kalmamali"

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

    # Tabloda bekleyen isaret gorunmeli. **Silinen** bolum artik tabloda
    # yoktur: ana ekran planlanan yerlesimi gosterir (ADR 0031), silinecek
    # bolum haritadan da tablodan da kalkar. Bu yuzden isaret yalnizca
    # kalan/degisen bolumlerde aranir.
    isaretli = [pencere.part_table.item(r, 0).text()
                for r in range(pencere.part_table.rowCount())
                if pencere.part_table.item(r, 0)
                and "⏳" in pencere.part_table.item(r, 0).text()]
    assert len(isaretli) == 1, f"bekleyen isaret sayisi: {isaretli}"
    assert not any(p.index == 2 for p in pencere._plan_layout.partitions),         "silinecek bolum planlanan yerlesimde durdu"
    plan_durumlari = {p.index: p.plan_state
                      for p in pencere._plan_layout.partitions}
    assert plan_durumlari.get(1) == "format", plan_durumlari

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

    # --- ana haritada tutamaklar: surukle -> kuyruga boyutlandirma adimi ---
    # Harita geri yukleme seridiyle AYNI denetleyiciyi ve ortak modeli
    # kullanir (ADR 0049). Diske hicbir sey yazilmaz; birakinca kuyruga girer.
    from PyQt5.QtCore import QEvent, QPoint
    from PyQt5.QtGui import QMouseEvent
    import time
    harita = pencere.disk_map
    fat_bolum = next(p for p in pencere.session.partitions
                     if (p.fs_type or "").upper().startswith("FAT"))

    def sinirlari_bekle():
        """Sinirlar arka planda hesaplanir; arayuz bu sirada beklemez."""
        bitis = time.time() + 20
        while pencere.limits.pending() and time.time() < bitis:
            app.processEvents()
            time.sleep(0.02)
        app.processEvents()
        assert not pencere.limits.pending(), "sinir hesabi bitmedi"

    def sag_kenar(index):
        """Bolumun sag kenarinin x konumu (tek basina ya da ortak sinir)."""
        for x, kenar in harita.edit.edge_positions():
            if kenar[2] == index:
                return x
        return None

    pencere.select_partition(fat_bolum.index)
    app.processEvents()
    assert pencere._edit_layout is not None, "harita ortak modeli almadi"
    sinirlari_bekle()
    assert not harita.edit.busy, "sinirlar hesaplandi ama tutamak etkin degil"
    sag_x = sag_kenar(fat_bolum.index)
    assert sag_x is not None, f"FAT bolumunun sag kenari yok: {harita.edit.edges()}"
    ust, alt = harita._band()
    y = (ust + alt) // 2
    blok = next(b for b in harita._blocks if b.obj.index == fat_bolum.index)
    genislik = max(12, blok.rect.width() // 3)

    def surukle(x0, x1):
        for tur, x in ((QEvent.MouseButtonPress, x0), (QEvent.MouseMove, x1),
                       (QEvent.MouseButtonRelease, x1)):
            app.sendEvent(harita, QMouseEvent(
                tur, QPoint(x, y), Qt.LeftButton,
                Qt.NoButton if tur == QEvent.MouseButtonRelease
                else Qt.LeftButton, Qt.NoModifier))
        app.processEvents()

    boyut_oncesi = os.path.getsize(goruntu)
    surukle(sag_x, sag_x - genislik)
    # FAT'in sagindaki bolum ext4'tur. ext boyutlandirilabildigi icin
    # (ADR 0052) sag kenar ORTAK sinirdir ve iki adim uretir: once FAT
    # kucultulur, sonra ext acilan alana dogru buyur (ADR 0049). ext'in
    # bicimlendirilemedigi ortamda (ham bolum) yine tek adim cikar.
    ilk_sayi = len(pencere.queue)
    assert ilk_sayi in (1, 2), f"surukleme kuyruga eklenmedi: {ilk_sayi}"

    def fat_adimi():
        return next(op for op in pencere.queue if op.kind == "resize"
                    and op.params.get("index") == fat_bolum.index)
    adim = fat_adimi()
    assert pencere.queue[0] is adim, "yer acan adim once gelmeli"
    assert os.path.getsize(goruntu) == boyut_oncesi, "surukleme diske yazdi"
    kaydet(pencere, "39-haritada-tutamak.png")

    # Kucultulen bolum GERI BUYUTULEBILMELI; ikinci surukleme YENI adim
    # eklemez, ayni adimi gunceller; diskteki boyutuna donunce adim kalkar.
    pencere.select_partition(fat_bolum.index)
    sinirlari_bekle()
    sag_x2 = sag_kenar(fat_bolum.index)
    assert sag_x2 is not None, "kucultulen bolumde tutamak yok"
    surukle(sag_x2, sag_x2 + 8)
    assert len(pencere.queue) == ilk_sayi, f"yeni adim eklendi: {len(pencere.queue)}"
    buyuk = fat_adimi().params["sector_count"]
    assert buyuk > adim.params["sector_count"], "geri buyutme olmadi"
    pencere.select_partition(fat_bolum.index)
    sinirlari_bekle()
    sag_x3 = sag_kenar(fat_bolum.index)
    surukle(sag_x3, sag_x3 + 400)
    kalan = [op.params.get("sector_count") for op in pencere.queue
             if op.params.get("index") == fat_bolum.index]
    assert not kalan or kalan[0] >= buyuk, kalan
    print(f"  (geri buyutme: adim yerinde guncellendi, kuyrukta {len(kalan)})")
    pencere.queue.clear()
    pencere._refresh_pending()
    app.processEvents()
    print(f"  (haritada tutamak: surukleme kuyruga eklendi — {adim})")

    # --- ortak "Bolum duzeni" penceresi ana ekranda (ADR 0049, 5. asama) ---
    from diskultimate.ui.dialogs.partition_layout import PartitionLayoutDialog
    from diskultimate.ui.widgets.layout_bar import PartitionEditBar
    pencere._limits_blocking([p for p in pencere.session.table.partitions
                              if not p.logical])
    model = pencere._edit_model()
    assert model is not None and model.parts
    d_duzen = PartitionLayoutDialog(model, pencere.session.name, pencere,
                                    mode="disk")
    assert isinstance(d_duzen.bar, PartitionEditBar), "ortak serit kullanilmiyor"
    d_duzen.show()
    app.processEvents()
    assert d_duzen.windowTitle() == "Bolum Duzeni", d_duzen.windowTitle()
    assert d_duzen.tree.topLevelItemCount() == len(model.parts)
    kaydet(d_duzen, "41-bolum-duzeni.png")
    # son bolumu genislet -> sonuc kuyruga (ortak commit)
    once = {x.index: (x.new_start, x.new_count) for x in model.parts}
    d_duzen._extend_last()
    sonuc = d_duzen.result_layout()
    degisen = [x.index for x in sonuc.parts
               if (x.new_start, x.new_count) != once[x.index]]
    d_duzen.close()
    if degisen:
        pencere._commit_edit(sonuc, degisen, before=once)
        app.processEvents()
        assert len(pencere.queue) == len(degisen), len(pencere.queue)
        assert all(op.kind == "resize" for op in pencere.queue)
    print(f"  (bolum duzeni penceresi: {len(model.parts)} bolum, "
          f"{len(degisen)} degisiklik kuyrukta)")
    pencere.queue.clear()
    pencere._refresh_pending()
    app.processEvents()

    # --- eskimis en az boyut: dosya eklenince sinir yeniden olculur (ADR 0074) ---
    # 2026-10-01: sinir onbellegi (aygit, no, baslangic, boyut) anahtarliydi;
    # dosya eklemek anahtari degistirmedigi icin pencere dosyalar eklenmeden
    # onceki en az boyutu kullandi ve 3,27 GB dolu NTFS 3,33 GB'a planlandi.
    # NTFS: en az boyutu dogrudan dolu kumelerden gelir (kullanicinin durumu)
    ntfs_bolum = next(p for p in pencere.session.partitions
                      if (p.fs_type or "").upper() == "NTFS")
    pencere.select_partition(ntfs_bolum.index)
    sinirlari_bekle()
    disk_bolum = pencere.session.table.get(ntfs_bolum.index)
    eski = pencere.limits.get(pencere.session, disk_bolum, request=False)
    assert eski is not None, "NTFS siniri hesaplanmadi"
    # 1) gezgin disindan yazim (onbellek habersiz): kuyruga yazma ani olcer
    fs_dis = pencere.session.filesystem(ntfs_bolum.index)
    dolgu = min(disk_bolum.size // 2, 64 * MIB)
    fs_dis.write_file("/dolgu.bin", b"\xA5" * dolgu)
    fs_dis.flush()
    pencere.session.close_filesystems()
    model = pencere._edit_model()
    slot = model.get(ntfs_bolum.index)
    assert slot.min_count == eski.min_sectors, "onbellek beklenmedik sekilde tazelendi"
    once = {x.index: (x.new_start, x.new_count) for x in model.parts}
    slot.new_count = eski.min_sectors              # eski siniri tam kullan
    hatalar = []
    asil_error = pencere.error
    pencere.error = lambda baslik, mesaj: hatalar.append((baslik, mesaj))
    try:
        pencere._commit_edit(model, [ntfs_bolum.index], before=once)
        app.processEvents()
    finally:
        pencere.error = asil_error
    taze = pencere.limits.get(pencere.session, disk_bolum, request=False)
    assert taze is not None and taze.min_sectors > eski.min_sectors, (eski, taze)
    assert hatalar and not len(pencere.queue), (hatalar, list(pencere.queue))
    # 2) gezgin yolu: contentChanged sinirlari unutur ve yeniden hesaplatir
    pencere.limits.compute_now(pencere.session, disk_bolum)
    pencere.browser.contentChanged.emit()
    assert pencere.limits.get(pencere.session, disk_bolum, request=False) is None \
        or pencere.limits.pending(), "gezgin degisikligi sinirlari unutmadi"
    sinirlari_bekle()
    assert pencere.limits.get(pencere.session, disk_bolum, request=False) is not None
    fs_dis = pencere.session.filesystem(ntfs_bolum.index)
    fs_dis.remove("/dolgu.bin")
    fs_dis.flush()
    pencere.session.close_filesystems()
    pencere.limits.forget(pencere.session)
    pencere.queue.clear()
    pencere._refresh_pending()
    app.processEvents()
    print(f"  (eskimis sinir: kuyruga yazarken yeniden olculdu — "
          f"{eski.min_sectors} -> {taze.min_sectors} sektor, {len(hatalar)} ret)")

    # --- sag tik menuleri: her islem AIT OLDUGU dugumde (ADR 0036) ---
    # "Fiziksel Diskler" bir kategori basligidir; bolum tablosu olusturma
    # orada durunca islem sag tiklanan diske degil, o sirada etkin olan
    # kaynaga gidiyordu. Menuler exec_ yakalanarak okunur; hicbir pencere
    # acilmaz ve hicbir aygita dokunulmaz.
    from PyQt5.QtWidgets import QMenu

    yakalanan = []
    orijinal_exec = QMenu.exec_
    QMenu.exec_ = lambda self, *a, **k: yakalanan.append(
        [act.text() for act in self.actions() if act.text()])

    def _menu_ac(hedef_tur):
        yakalanan.clear()
        dugum = pencere._find_tree_item_by_kind(hedef_tur)
        assert dugum is not None, f"agacta {hedef_tur} dugumu yok"
        pencere.tree.scrollToItem(dugum)
        pencere._tree_context(pencere.tree.visualItemRect(dugum).center())
        assert yakalanan, f"{hedef_tur} icin menu acilmadi"
        return yakalanan[-1]

    try:
        kok_menu = _menu_ac("physroot")
        assert not any("bolum tablosu" in m.lower() for m in kok_menu),             f"kategori basliginda disk islemi duruyor: {kok_menu}"
        assert any("yenile" in m.lower() for m in kok_menu), kok_menu

        oturum_menu = _menu_ac("session")
        assert any("MBR" in m for m in oturum_menu), oturum_menu
        assert any("GPT" in m for m in oturum_menu), oturum_menu
        assert any("Goruntu boyutunu" in m for m in oturum_menu), oturum_menu
        assert any("yedekle" in m.lower() for m in oturum_menu), oturum_menu
    finally:
        QMenu.exec_ = orijinal_exec
    print(f"  (sag tik: kategori basliginda {len(kok_menu)} giris, "
          f"goruntu dugumunde {len(oturum_menu)} giris)")

    # --- acilista hazir ekran: ilk uygun disk secilir (ADR 0035) ---
    # Politika ayri sinanir: gercek bir aygit acmadan, hangi diskin
    # secilecegini belirleyen kural olculur. Bilgisi eksik ya da bolum
    # tablosu okunamamis disk acilista **acilmaz** — yoksa kullanici
    # acilir acilmaz bir hata penceresiyle karsilasirdi.
    class _SahteDisk:
        def __init__(self, path, info_complete=True):
            self.path = path
            self.name = os.path.basename(path)
            self.size = 10 * 1024 * MIB
            self.info_complete = info_complete

    class _SahteYoklama:
        def __init__(self, error=""):
            self.error = error

    eksik = _SahteDisk("/dev/sdx", info_complete=False)
    okunamaz = _SahteDisk("/dev/sdy")
    saglam = _SahteDisk("/dev/sdz")
    yoklamalar = {"/dev/sdx": _SahteYoklama(),
                  "/dev/sdy": _SahteYoklama("tablo okunamadi"),
                  "/dev/sdz": _SahteYoklama()}
    secim = MainWindow.first_openable_disk([eksik, okunamaz, saglam], yoklamalar)
    assert secim is saglam, f"acilista yanlis disk secildi: {secim}"
    assert MainWindow.first_openable_disk([eksik, okunamaz], yoklamalar) is None,         "acilamayacak disk secildi"
    assert MainWindow.first_openable_disk([], {}) is None
    # Bir kez calisir: kullanici diski kapatinca pencere yeniden acmaz
    assert pencere._auto_opened in (True, False)
    pencere._auto_opened = True
    onceki = pencere.session
    pencere._autoselect_disk([saglam])
    assert pencere.session is onceki, "otomatik secim ikinci kez calisti"
    print("  (acilis secimi: bilgisi eksik ve okunamayan diskler atlaniyor)")

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

    # --- ikon setleri (ADR 0046): 8 set, canli degisim, saklama ---
    from diskultimate.ui import iconpacks, iconsets, svgpath
    from diskultimate.ui.theme import os_icon

    # SVG yol ayrıştırıcısı: sıkıştırılmış sayılar ve bitişik yay bayrakları
    yol = svgpath.parse("M.54 3.87.5 3a2 2 0 0 1 2-2h3a1 1 0 00-.11.135z")
    assert not yol.isEmpty() and yol.elementCount() > 6, yol.elementCount()
    daire = svgpath.parse("M2 12a10 10 0 1 0 20 0a10 10 0 1 0-20 0z")
    kutu = daire.boundingRect()
    assert abs(kutu.width() - 20) < 0.05 and abs(kutu.height() - 20) < 0.05, kutu

    def _dolu(pix):
        img = pix.toImage()
        return any(img.pixelColor(x, y).alpha() > 0
                   for x in range(img.width()) for y in range(img.height()))

    eksikler = []
    for anahtar, _ad in iconsets.SETS:
        for ad in icons.names():
            if not _dolu(icons.draw(ad, 24, dark=False, icon_set=anahtar)):
                eksikler.append(f"{anahtar}/{ad}")
        if anahtar in iconsets.PACK_SETS:
            gomulu = sum(iconpacks.has(anahtar, ad) for ad in icons.names())
            assert gomulu == len(icons.names()), \
                f"{anahtar}: {gomulu}/{len(icons.names())} ikon gomulu"
    assert not eksikler, f"bos cizilen ikon: {eksikler[:10]}"

    # Canli degisim: AYNI QIcon nesnesi yeni setle cizilmeli. Baslangic
    # durumu test belirler: ayar kalicidir ve yarida kalan bir kosu onu
    # "tabler"da birakmisti (degisim gozlenemiyordu). Kullanicinin secimi
    # her durumda geri yuklenir.
    onceki_set = iconsets.current()
    try:
        pencere.change_icon_set("classic")
        app.processEvents()
        ikon_nesnesi = icons.icon("folder")
        klasik = ikon_nesnesi.pixmap(24, 24).toImage()
        pencere.change_icon_set("tabler")
        app.processEvents()
        assert iconsets.current() == "tabler"
        assert ikon_nesnesi.pixmap(24, 24).toImage() != klasik, \
            "ikon seti degisti ama mevcut ikon eski setle ciziliyor"
        from diskultimate.core import settings as ayarlar
        assert ayarlar.get(iconsets.SETTING_KEY) == "tabler", "secim saklanmadi"
        secili = [a.data() for a in pencere._icon_group.actions()
                  if a.isChecked()]
        assert secili == ["tabler"], secili
        assert len(pencere._icon_group.actions()) == 8
        kaydet(pencere, "40-ikon-seti-tabler.png")
    finally:
        pencere.change_icon_set(onceki_set)
        app.processEvents()

    # Isletim sistemi amblemleri: Linux/macOS Simple Icons, Windows kendi
    for isletim in ("windows", "linux", "macos"):
        assert _dolu(os_icon(isletim, 16).pixmap(16, 16)), isletim
    assert iconpacks.has("simpleicons", "linux")
    assert not iconpacks.has("simpleicons", "windows"), \
        "Simple Icons Microsoft logolarini kaldirdi; eski surumden alinmamali"
    bildirimler = iconpacks.notices()
    assert len(bildirimler) == 6 and all(b["text"] for b in bildirimler), \
        [b["key"] for b in bildirimler]
    print(f"  (ikon setleri: {len(iconsets.SETS)} set x {len(icons.names())} "
          f"ikon cizildi, canli degisim ve saklama denetlendi)")

    # --- uygulama ikonu: dosyadan gelir, cizilmez (ui/appicon.py) ---
    #
    # Uc sey denetlenir:
    #   1. dosya yerinde mi (paketleyici de ayni yolu kullanir),
    #   2. butun boyutlar var mi — eksik boyut Qt'ye olcekletir, bulanir,
    #   3. SAYDAM mi. Kaynak dosya opak geldiginde (arka plan piksel olarak
    #      gomulu) gorev cubugunda ikonun arkasinda acik gri bir kare gorunur;
    #      bu sessiz bir kusurdur, testle yakalanir.
    from diskultimate.ui import appicon

    assert os.path.isfile(appicon.ICON_PATH), \
        f"uygulama ikonu yok: {appicon.ICON_PATH}"
    uygulama_ikonu = appicon.app_icon()
    assert not uygulama_ikonu.isNull(), "uygulama ikonu okunamadi"
    bulunan = sorted(b.width() for b in uygulama_ikonu.availableSizes())
    for beklenen in (16, 24, 32, 48, 64, 128, 256):
        assert beklenen in bulunan, f"{beklenen} px ikon eksik ({bulunan})"
    for boyut in (16, 32, 256):
        ikon_resmi = uygulama_ikonu.pixmap(boyut, boyut).toImage()
        saydam = sum(1 for x in range(ikon_resmi.width())
                     for y in range(ikon_resmi.height())
                     if ikon_resmi.pixelColor(x, y).alpha() == 0)
        assert saydam > 0, (f"{boyut} px ikon tamamen opak — arka plan "
                            f"piksel olarak gomulu kalmis")
    # (Degisken adi `goruntu` degil: o ad testin disk goruntusu yoludur ve
    # ezilince asagidaki yedekleme bolumu QImage'i dosya yolu sanip dusuyordu.)
    # Saydamlik TEK BASINA "dolu" olmanin olcusu degildir: bastan sona saydam
    # (yani bos) bir goruntu de o denetimden gecerdi. Renk cesitliligi asil
    # olcudur — bos ya da tek renk bir ikon burada kalir.
    ikon_resmi = uygulama_ikonu.pixmap(32, 32).toImage()
    renkler = {ikon_resmi.pixelColor(x, y).rgba()
               for x in range(32) for y in range(32)
               if ikon_resmi.pixelColor(x, y).alpha() > 0}
    assert len(renkler) >= 32, f"32 px ikon neredeyse bos ({len(renkler)} renk)"
    # Pencere ikonu uygulamadan DEVRALINIR (yukarida apply_app_icon cagrildi);
    # burada ayrica atanmaz, devralmanin gercekten oldugu dogrulanir.
    assert not pencere.windowIcon().isNull(), "pencere ikonu uygulamadan devralinmadi"
    print(f"  (uygulama ikonu: {len(bulunan)} boyut {bulunan}, "
          f"saydam, 32 px'te {len(renkler)} renk)")

    # --- bagla / surucu harfi eylemleri (ADR 0043) ---
    #
    # Gercek bir baglama YAPILMAZ. Sinanan sey arayuz sozlesmesi: etiketler
    # platformun kavramindan geliyor mu, eylemler goruntu dosyasinda kapali
    # mi (goruntu isletim sistemine bagli degildir), menude duruyorlar mi.
    from diskultimate.core.platform import mount_action_labels

    mount_text, unmount_text = mount_action_labels()
    assert pencere.act_mount.text() == mount_text, pencere.act_mount.text()
    assert pencere.act_unmount.text() == unmount_text
    pencere.select_partition(1)
    assert not pencere.act_mount.isEnabled(), \
        "goruntu dosyasinda baglama sunulmamali"
    assert not pencere.act_unmount.isEnabled()
    menu_metinleri = [a.text() for m, _ in pencere._menus
                      for a in m.actions()]
    assert mount_text in menu_metinleri and unmount_text in menu_metinleri, \
        "bagla/cikar menude yok"
    print(f"  (baglama: '{mount_text}' / '{unmount_text}' — goruntude kapali, "
          f"menude var)")

    # --- baglama noktasi sutunu (ADR 0043) ---
    #
    # Sutun basligi platformun kavramini soyler ve bagli olmayan bolumde
    # "-" yazar. Bolum tablosu sutun indislerini elle tasidigi icin, sutun
    # eklendiginde kaymalarin dogru oldugu da burada yakalanir.
    from diskultimate.core.platform import mount_point_label
    from diskultimate.i18n import tr as ceviri
    from diskultimate.ui.widgets.partition_table import MOUNT_COLUMN, columns

    basliklar = columns()
    assert basliklar[MOUNT_COLUMN] == mount_point_label(), basliklar[MOUNT_COLUMN]
    tablo = pencere.part_table
    assert tablo.columnCount() == len(basliklar)
    for sutun, baslik in enumerate(basliklar):
        assert tablo.horizontalHeaderItem(sutun).text() == baslik, (sutun, baslik)
    satir = next(i for i, (tur, _) in enumerate(tablo._rows) if tur == "part")
    hucre = tablo.item(satir, MOUNT_COLUMN)
    assert hucre is not None and hucre.text() == "-", \
        "goruntu dosyasinda baglama noktasi olmamali"
    # Boyut sutunu kaymayi yakalar: bir saga kaydi, degeri hala boyut olmali
    boyut_sutunu = basliklar.index(ceviri("Boyut"))
    assert tablo.item(satir, boyut_sutunu).text().endswith(("B", "KB", "MB", "GB")), \
        tablo.item(satir, boyut_sutunu).text()
    print(f"  (baglama sutunu: '{basliklar[MOUNT_COLUMN]}', "
          f"{tablo.columnCount()} sutun hizali)")

    # --- acilista otomatik yetki yukseltmesi (ADR 0042) ---
    #
    # Gercek bir polkit penceresi ACILMAZ: baslatma islevi taklit edilir ve
    # `main.elevate_at_startup`in karari ile bekleme donusunun dogru calistigi
    # sinanir. Onemli olan iki davranis: yetki alinirsa bu kopya kapanmali,
    # alinamazsa uygulama **yine de acilmali** (goruntu dosyalari icin yetki
    # gerekmez).
    from diskultimate.ui import startup as giris

    class SahteBaslatma:
        WAITING, STARTED, FAILED = "bekliyor", "basladi", "hata"

        def __init__(self, basarili: bool):
            self.basarili = basarili
            self.sayac = 0
            self.temizlendi = False

        def poll(self):
            self.sayac += 1
            if self.sayac < 2:
                return self.WAITING, ""
            return ((self.STARTED, "") if self.basarili
                    else (self.FAILED, "yetki verilmedi"))

        def cleanup(self):
            self.temizlendi = True

    eski = (giris.is_elevated, giris.elevation_available, giris.relaunch_elevated)
    eski_ortam = os.environ.pop("DISKULTIMATE_NO_ELEVATION_PROMPT", None)
    try:
        giris.is_elevated = lambda: False
        giris.elevation_available = lambda: (True, "")

        for basarili in (True, False):
            tutamac = SahteBaslatma(basarili)
            giris.relaunch_elevated = lambda t=tutamac: (t, "")
            assert giris.elevate_at_startup() is basarili, basarili
            assert tutamac.temizlendi, "gecici dosyalar toplanmali"

        # Cikis kapilari: bayrak, ortam degiskeni ve zaten yetkili olma hali
        giris.relaunch_elevated = lambda: (_ for _ in ()).throw(
            AssertionError("cikis kapisi acikken yetki istenmemeli"))
        sys.argv.append(giris.NO_ROOT_FLAG)
        try:
            assert giris.elevate_at_startup() is False, "--no-root"
        finally:
            sys.argv.remove(giris.NO_ROOT_FLAG)
        os.environ["DISKULTIMATE_AUTO_ROOT"] = "0"
        try:
            assert giris.elevate_at_startup() is False, "DISKULTIMATE_AUTO_ROOT=0"
        finally:
            del os.environ["DISKULTIMATE_AUTO_ROOT"]
        giris.is_elevated = lambda: True
        assert giris.elevate_at_startup() is False, "zaten yetkili"
    finally:
        giris.is_elevated, giris.elevation_available, giris.relaunch_elevated = eski
        if eski_ortam is not None:
            os.environ["DISKULTIMATE_NO_ELEVATION_PROMPT"] = eski_ortam
    print("  (acilis yetkisi: basarili/basarisiz ve 3 cikis kapisi denetlendi)")

    # --- yetkili kopyada dosya sahipligi (ADR 0042) ---
    from diskultimate.core import platform as pf_yetki

    if os.name == "posix" and os.geteuid() != 0:
        # Yetkisiz kopyada hicbir sey yapilmamali: kullanicinin dosyalarina
        # dokunan bir kod yolu, yanlis kosulda calisirsa zarar verir.
        assert pf_yetki.invoking_user() is None
        deneme = os.path.join(scratch("sahiplik"), "dosya.bin")
        with open(deneme, "wb") as fh:
            fh.write(b"x")
        assert pf_yetki.restore_owner(deneme) is False, "yetkisizken sahiplik degismemeli"
        assert pf_yetki.restore_owner("") is False
        os.remove(deneme)
        print("  (dosya sahipligi: yetkisiz kopyada dokunulmuyor)")

    # --- doluluk cubugu: renk esikleri ve okunurluk ---
    #
    # Cubuk iki yerde cizilir (harita blogu, boyutlandirma seridi) ve tek bir
    # yardimcidan gelir. Burada sinanan sey renk SECIMIDIR: dolu bir bolum
    # uyari renginde olmali ve yazi her zemin uzerinde okunur kalmali.
    from PyQt5.QtGui import QColor, QPainter, QPixmap
    from diskultimate.ui.theme import (USAGE_FULL, USAGE_FULL_AT, USAGE_WARN,
                                       USAGE_WARN_AT, draw_usage_bar,
                                       fs_color, readable_text, usage_fill)

    for fs_adi in ("FAT32", "exFAT", "NTFS", "ext4", "Bilinmeyen"):
        renk = fs_color(fs_adi)
        normal = usage_fill(renk, 0.40)
        assert normal.hue() == renk.hue(), (
            f"{fs_adi}: esik altinda cubuk blogun tonunda kalmali")
        assert usage_fill(renk, USAGE_WARN_AT) == QColor(USAGE_WARN), fs_adi
        assert usage_fill(renk, USAGE_FULL_AT) == QColor(USAGE_FULL), fs_adi
        assert usage_fill(renk, 1.0) == QColor(USAGE_FULL), fs_adi
        # Yazi rengi zemine gore secilir; ikisi de acik/koyu olmamali.
        for zemin in (normal, QColor(USAGE_WARN), QColor(USAGE_FULL)):
            yazi = readable_text(zemin)
            fark = abs(yazi.lightness() - zemin.lightness())
            assert fark > 60, f"{fs_adi}: yazi/zemin karsitligi zayif ({fark})"

    # Cizim uc degerlerde de patlamamali (sifir, tam, cok dar alan).
    tuval = QPixmap(200, 40)
    tuval.fill(QColor("#ffffff"))
    boyaci = QPainter(tuval)
    for oran in (0.0, 0.004, 0.5, 1.0):
        draw_usage_bar(boyaci, QRect(5, 5, 190, 11), oran,
                       fs_color("NTFS"), "%{:.0f} dolu".format(oran * 100))
    draw_usage_bar(boyaci, QRect(0, 0, 4, 3), 0.5, fs_color("ext4"))   # cok dar
    boyaci.end()
    print(f"  (doluluk cubugu: %{USAGE_WARN_AT*100:.0f} kehribar, "
          f"%{USAGE_FULL_AT*100:.0f} kirmizi; 5 renkte okunurluk denetlendi)")

    # --- yedekleme/geri yukleme: TEK pencere (ADR 0032) ---
    # Eskiden yedek bilgisi ayri bir pencereydi, yedek almak ve geri yuklemek
    # ise bir dizi dosya secme + soru kutusuydu. Artik hepsi ayni formda:
    # ustte dosya + bilgi + icerik + not, altta disk/bolum agaci.
    from diskultimate.core.clone import backup, read_backup_info
    from diskultimate.ui.dialogs.backup import (MODE_BACKUP, MODE_RESTORE,
                                                BackupDialog)

    dub_yolu = os.path.join(scratch("ui"), "ornek.dub")
    kaynak_oturum = DiskSession.open(goruntu, readonly=True)
    backup(kaynak_oturum.image, dub_yolu, compress=True,
           remark="Duman testi yedegi — 2026")
    kaynak_oturum.close()
    assert read_backup_info(dub_yolu).remark.startswith("Duman testi"), \
        "not dosyaya yazilmadi"

    d10 = BackupDialog(pencere, mode=MODE_BACKUP,
                       sessions=list(pencere.sessions),
                       disks=list(pencere._physical_cache.values()),
                       surveys=pencere._surveys, session=pencere.session)
    d10.show()
    app.processEvents()
    assert d10.current_target() is not None, "kaynak secilmedi"
    assert d10.tree.topLevelItemCount() >= 1, "hedef agaci bos"
    # Hedef agaci ana formda degil "Disk sec" penceresinde; formda ozet satiri
    assert not d10.tree.isVisible(), "disk agaci hala ana formda"
    assert d10.target_label.text() not in ("", "-"), "hedef ozeti bos"
    # Yedek alma -> geri yukleme: yedek secilmemisken icerik agacinda
    # kaynagin bolumleri KALMAMALI (kullanici bildirimi, 2026-09-28)
    d10.set_mode(MODE_RESTORE)
    app.processEvents()
    assert d10.content.topLevelItemCount() == 1 and \
        d10.content.topLevelItem(0).isDisabled(), \
        "yedek secilmeden icerik agacinda bolum gorunuyor"
    # Yedek secilmeden hedef alani pasif ve hicbir hedef secili degil
    assert not d10.target_group.isEnabled(), "yedek yokken hedef alani etkin"
    assert d10.current_target() is None, "yedek yokken hedef secili"
    assert d10.target_group.title() == "Hedef Disk / Bolum", \
        d10.target_group.title()
    d10.set_mode(MODE_BACKUP)
    app.processEvents()
    assert d10.current_target() is not None, "yedek alma kaynagi geri gelmedi"
    assert d10.target_group.title() == "Kaynak Disk / Bolum"
    d10._set_path(os.path.join(scratch("ui"), "yeni-yedek.dub"))
    d10.remark.setPlainText("Ana diskin haftalik yedegi")
    d10.level_buttons["high"].setChecked(True)
    app.processEvents()
    assert d10.level() == 9, d10.level()
    assert not d10._problem(), d10._problem()
    # Yedek alma kipinde icerik agaci KAYNAGIN bolumlerini gosterir
    assert d10.content.topLevelItemCount() == len(pencere.session.partitions),         f"kaynak icerigi listelenmedi: {d10.content.topLevelItemCount()}"
    kaydet(d10, "36-yedek-al.png")

    # Gercek yedek: pencere kendi is parcaciginda calistirir ve sonucu
    # **ayni formda** gosterir; ayri bir ilerleme penceresi acilmaz.
    yeni_yedek = d10.path_edit.text()
    d10._start()
    assert d10._worker is not None, "yedekleme is parcacigi baslamadi"
    d10._worker.wait()
    app.processEvents()
    assert d10.result_value is not None, f"yedek alinamadi: {d10.status.text()}"
    assert os.path.isfile(yeni_yedek), "yedek dosyasi olusmadi"
    assert read_backup_info(yeni_yedek).remark == "Ana diskin haftalik yedegi",         "not yeni yedege yazilmadi"
    print(f"  (yedek penceresi: {os.path.basename(yeni_yedek)} "
          f"{d10.result_value.file_size} bayt, not dosyada)")
    d10.close()

    d11 = BackupDialog(pencere, mode=MODE_RESTORE,
                       sessions=list(pencere.sessions),
                       disks=list(pencere._physical_cache.values()),
                       surveys=pencere._surveys, session=pencere.session,
                       backup_path=dub_yolu)
    d11.show()
    app.processEvents()
    if d11._worker is not None:
        d11._worker.wait()
    app.processEvents()
    assert d11.info is not None, "yedek basligi okunmadi"
    # Geri yuklemede hedef kendiliginden SECILMEZ; kullanici "Disk sec" ile
    # secer. Burada agac uzerinden ayni secim yapilir.
    assert d11.current_target() is None, "geri yukleme hedefi kendiliginden secildi"
    assert d11.target_group.isEnabled(), "yedek yuklendi ama hedef alani pasif"
    assert "Hedef diski" in d11._problem(), d11._problem()
    goruntu_dugumu = next(
        it for it in d11.tree.findItems("*", Qt.MatchWildcard | Qt.MatchRecursive)
        if it.data(0, Qt.UserRole) is not None
        and d11.targets[it.data(0, Qt.UserRole)].kind == "image")
    d11.tree.setCurrentItem(goruntu_dugumu)
    app.processEvents()
    # Not yalnizca yedek ALINIRKEN yazilir; geri yuklemede salt okunur
    # "Aciklama" satirinda gorunur, duzenleyici ve "Notu kaydet" yoktur.
    assert d11.info_labels["remark"].text().startswith("Duman testi"), \
        "not bilgi alaninda gorunmuyor"
    assert d11.note_box.isHidden(), "geri yuklemede not duzenleyicisi acik"
    assert not hasattr(d11, "btn_save_remark"), "Notu kaydet hala var"
    # Disk yedegi: hedefe uydurulmus yerlesim ve "Bolumleri yonet"
    assert d11.plan is not None, "geri yukleme yerlesimi kurulmadi"
    # Harita yerine tutamakli serit; kenar surukleme plani degistirir
    assert d11.layout_bar.isVisible() and not d11.map.isVisible()
    from PyQt5.QtCore import QPoint
    from PyQt5.QtGui import QMouseEvent
    from PyQt5.QtCore import QEvent
    serit = d11.layout_bar
    son_kenar = [k for k in serit.edit.edges() if k[1] == "end"][-1]
    x0 = serit._x(son_kenar[0])
    y0 = serit._track().center().y()
    once_plan = [(p.new_start, p.new_count) for p in d11.plan.parts]
    for tur, x in ((QEvent.MouseButtonPress, x0), (QEvent.MouseMove, x0 - 60),
                   (QEvent.MouseButtonRelease, x0 - 60)):
        olay = QMouseEvent(tur, QPoint(x, y0), Qt.LeftButton,
                           Qt.LeftButton if tur != QEvent.MouseButtonRelease
                           else Qt.NoButton, Qt.NoModifier)
        app.sendEvent(serit, olay)
    app.processEvents()
    sonra_plan = [(p.new_start, p.new_count) for p in d11.plan.parts]
    kucultulebilir = d11.plan.get(son_kenar[2]).min_count < \
        d11.plan.get(son_kenar[2]).old_count
    if kucultulebilir:
        assert sonra_plan != once_plan, "seritte surukleme plani degistirmedi"
    assert not d11.plan.validate(), d11.plan.validate()
    d11.plan.reset()
    d11._on_target_changed()
    assert d11.btn_layout.isVisible(), "Bolumleri yonet dugmesi gorunmuyor"
    onceki_hedef = d11.tree.currentItem()
    d11.tree.setCurrentItem(d11.new_image_item)
    app.processEvents()
    assert d11.size_spin.isVisible(), "yeni goruntu boyutu sorulmuyor"
    # Birim degisince boyut korunur (MB/GB/TB)
    once = d11._new_size_bytes()
    d11.size_unit.setCurrentIndex(0)                  # MB
    app.processEvents()
    assert abs(d11._new_size_bytes() - once) < 1024 * 1024, \
        (once, d11._new_size_bytes())
    assert d11.plan.target_sectors * 512 == d11.info.total_bytes, \
        "birim degisimi yerlesimi degistirdi"
    d11.size_spin.setValue(d11.size_spin.value() * 2)
    app.processEvents()
    assert d11.plan.target_sectors > d11.plan.source_sectors, \
        "goruntu boyutu yerlesime yansimadi"
    from diskultimate.ui.dialogs.restore_layout import RestoreLayoutDialog
    d12 = RestoreLayoutDialog(d11.plan, "yeni", d11)
    d12.show()
    app.processEvents()
    assert d12.tree.topLevelItemCount() == len(d11.plan.parts)
    d12._extend_last()
    assert d12.result_layout().changed, "son bolum genisletilmedi"
    assert not d12.result_layout().validate(), d12.result_layout().validate()
    kaydet(d12, "38-bolumleri-yonet.png")
    d12.accept()
    d11.plan = d12.result_layout()
    d11._on_target_changed()
    assert not d11._problem(), d11._problem()
    d11.tree.setCurrentItem(onceki_hedef)
    app.processEvents()
    assert not d11.size_unit.isVisible(), "birim kutusu yeni goruntu disinda gorunuyor"
    assert d11.content.topLevelItemCount() >= 1, "yedek icerigi listelenmedi"
    # Yikici islem: onay kutusu isaretlenmeden Baslat etkin olmamali
    assert not d11.btn_start.isEnabled(), \
        "silme onayi verilmeden geri yukleme etkin"
    d11.confirm.setChecked(True)
    app.processEvents()
    kaydet(d11, "37-geri-yukle.png")
    print(f"  (yedek penceresi: {d11.content.topLevelItemCount()} bolum, "
          f"not {len(d11.info_labels['remark'].text())} karakter)")
    d11.close()

    # --- uygulamada acik fiziksel disk hedef listesinde TEK satir ---
    # Eskiden hem "Acik goruntuler" hem "Fiziksel diskler" altindaydi ve
    # ikinci satir secilince ayni aygita ikinci tutamac aciliyordu (ADR 0021).
    # Gercek disk acilmaz: oturum taklit edilir.
    from diskultimate.core.physical import DiskInfo

    sahte_disk = DiskInfo(path="/dev/sahte-du", name="SAHTE DISK",
                          size=pencere.session.image.size)

    class _SahteFizikselOturum:
        is_physical = True
        disk_info = sahte_disk
        name = "sahte-du"
        image = pencere.session.image
        partitions = list(pencere.session.partitions)
        scheme = pencere.session.scheme
        readonly = True

    sahte = _SahteFizikselOturum()
    d13 = BackupDialog(pencere, mode=MODE_BACKUP,
                       sessions=[pencere.session, sahte], disks=[sahte_disk],
                       surveys={}, session=pencere.session)
    satirlar = [t for t in d13.targets
                if t.kind in ("image", "physical")
                and (t.session is sahte or t.disk is sahte_disk)]
    assert len(satirlar) == 1, [(t.kind, t.label) for t in satirlar]
    assert satirlar[0].kind == "physical" and satirlar[0].session is sahte, \
        "acik disk satiri oturuma baglanmadi (ikinci tutamac acilirdi)"
    bolumler = [t for t in d13.targets
                if t.kind == "partition" and t.session is sahte]
    assert len(bolumler) == len(sahte.partitions), "acik diskin bolumleri yok"
    assert all(t.disk is sahte_disk for t in bolumler), \
        "bolum satirlari fiziksel disk korumalarini tasimiyor"
    gorunen = [d13.tree.topLevelItem(i).text(0)
               for i in range(d13.tree.topLevelItemCount())]
    d13.close()
    print(f"  (hedef listesi: acik fiziksel disk tek satir — {gorunen})")

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
