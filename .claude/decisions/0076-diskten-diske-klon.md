# 0076 — Diskten diske klonlama

Tarih: 2026-10-01
Durum: **uygulandi** — goruntu oturumlariyla (ayni kod yolu) Linux ve
Windows'ta sinandi. **Gercek fiziksel hedef sinanmadi**: VBox misafirinde
guestcontrol yonetici yetkisi vermiyor; kullanicinin yonetici exe'siyle
PhysicalDrive1 (du-test-disk.vdi) uzerinde sinanacak.
Ilgili: [0014](0014-fiziksel-disk-destegi.md), [0021](0021-acik-aygiti-yoklamama-ve-kopyalama-ilerlemesi.md),
[0025](0025-bekleyen-islem-kuyrugu.md).

## Karar

"Diski klonla" once hedef turunu sorar: **goruntu dosyasi** (eskisi gibi) ya
da **baska bir disk**.

Cekirdek (`DiskSession`):
* `clone_to_physical(disk, allow_system)` — `PhysicalDisk(readonly=False,
  confirm=True, allow_system=...)`: fiziksel disk kapilarinin tamami.
* `clone_to_session(target)` — hedef uygulamada **aciksa** onun tutamaci
  kullanilir; ayni aygita ikinci tutamac acilmaz (ADR 0021).
* On denetim aygit acilmadan: kaynak = hedef ve kucuk hedef reddedilir.
* `_finish_clone_table`: hedef buyukse
  * GPT kaynak: tablo yeniden yazilir -> yedek baslik hedefin sonuna,
    son kullanilabilir LBA buyur (fazla alan bos alan olarak kullanilir);
  * GPT olmayan kaynak: hedefin son 33 sektoru silinir — onceden GPT olan
    hedefte eski yedek GPT kalirsa bazi araclar MBR diski "GPT'den onarmayi"
    onerir ve tabloyu ezer.
* Kaynak boyunun otesi **kopyalanmaz/silinmez** (ayrilmamis alan; eski veri
  fiziksel olarak kalir). Tamamen sifirlamak buyuk hedefte saatler surer;
  pencere bunu acikca soyler ve Guvenli silmeyi gosterir. DiskGenius da boyle.
* Disk/bolum GUID'leri degistirilmez (birebir klon; onyukleme yapilandirmasi
  bunlara bagli). Iki disk ayni makinede kalirsa isletim sistemi birini
  cevrimdisi yapabilir — sonuc mesajinda yaziyor.

Kuyruga girmez: geri yukleme gibi baska bir aygita yazar; onay hedef
penceresinde alinir.

Arayuz (`dialogs/clone_target.CloneTargetDialog`), uygunluk kurali cekirdekte
(`disksource.clone_target_problem`):
* kaynagin kendisi, bilgisi eksik disk ("bilinmiyor" risk yok sayilmaz),
  donanimsal yazma korumali ve kaynaktan kucuk disk gri + neden;
* "Hedef diskteki BUTUN veriler silinecek; anladim" kutusu zorunlu;
* sistem diskinde disk adini yazmak zorunlu;
* bagli bolum ve bekleyen (klona dahil olmayan) adimlar icin uyari.

Ayrica: klon/yedek/geri yukleme ilerleme metinleri `tr()` ile sarildi
(arayuz Ingilizce/Almanca iken Turkce kaliyordu).

## Dogrulama
* t76: GPT kaynak -> daha buyuk (eskiden GPT) hedef: icerik birebir, yedek
  baslik son sektorde, fazla alan kullanilabilir, `sfdisk --verify` temiz;
  MBR kaynak -> eski GPT hedef: son sektorde "EFI PART" kalmiyor; ret yollari.
* ui_smoke: gri satirlar, onay kutusu ve sistem diski adi zorunlulugu.
