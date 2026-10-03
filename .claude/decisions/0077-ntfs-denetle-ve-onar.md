# 0077 — NTFS denetle ve onar (ntfsfix karsiligi)

Tarih: 2026-10-03
Durum: **uygulandi** — goruntu dosyalarinda, Windows 10'un bagli biraktigi
gercek bir birimde ve **Windows'ta fiziksel disk yolundan** sinandi
(asagida). Linux'ta fiziksel diskte uygulanmadi (ana makinede fiziksel diske
yazilmaz, Linux VM yok).
Ilgili: [0025](0025-bekleyen-islem-kuyrugu.md), [0030](0030-ntfs-saf-python-boyutlandirma.md),
[0043](0043-baglama-ve-surucu-harfi.md).

## Neden

Kullanici 2026-10-02'de kendi makinesinde yasadi: Windows ile ayni diskteki
NTFS veri bolumu (`nvme0n1p4`, "Data") Linux'ta gorunmuyor ve baglanmiyordu;
`sudo ntfsfix -d /dev/nvme0n1p4` ile acildi. "Bu ozelligi programa
ekleyebilir miyiz" diye sordu.

Neden: Windows "Hizli baslatma", hazirda bekletme ya da elektrik kesintisi
birimi **temiz ayrilmamis** birakir:

* `$Volume` icinde **kirli** bayragi (0x0001) — cekirdegin `ntfs3` surucusu
  baglamayi reddeder ("volume is dirty and force flag is not set");
* `$LogFile` yeniden baslatma alani temiz degil — `ntfs-3g` "Metadata kept in
  Windows cache, refused to mount" der;
* sistem biriminde `hiberfil.sys` "HIBR" imzasi — `ntfs-3g` "Windows is
  hibernated" der.

## Karar

Saf Python `core/ntfsfix.py`, `ntfsfix`in adimlarini ayni sirayla yapar:

1. Onyukleme sektoru: asil bozuk + yedek saglam -> yedekten geri yaz; asil
   saglam + yedek farkli/bozuk -> yedegi tazele.
2. `$MFT` / `$MFTMirr`: ilk kayitlar (ntfs-3g `mftmirr_size`) bayt bayt
   karsilastirilir; saglam olan (FILE imzasi + guncelleme dizisi) digerine
   kopyalanir; ikisi de bozuksa **reddedilir** (chkdsk gerekir).
3. `$LogFile` 0xFF ile doldurulur (`ntfs_logfile_reset`). Zaten bossa
   dokunulmaz.
4. Kirli bayragi temizlenir (`-d`, varsayilan) **ya da** acilir: Windows bir
   sonraki acilista chkdsk calistirir (`ntfsfix`in varsayilani). Ikisi
   birlikte istenemez.
5. Hazirda bekletme imzasi varsa onarim **reddedilir**; kullanici acikca
   secerse `hiberfil.sys`in ilk 4 KiB'i sifirlanir (Windows soguk acilir).
   Dosyayi silmek (`ntfs-3g remove_hiberfile`) daha cok ustveri degistirir.

Denetim (`ntfs_check`) salt okunurdur. Tespit (`fsdetect`) her NTFS bolumu
icin `FSInfo.unclean` / `hibernated` bayraklarini cikarir (`quick_state`:
`$Volume`, `$LogFile`in ilk 8 KiB'i, kok dizin).

Arayuz:
* **Bolum > NTFS'i denetle ve onar...** (ve sag tik menusu), yalnizca NTFS'te
  etkin. Denetim `run_task` ile is parcaciginda; pencere bulgulari ve
  secenekleri gosterir, onarim **kuyruga** girer (`ntfs_fix`, yikici sayilir:
  gunluk bosaltilinca Windows'un islemedigi son degisiklikler kaybolur).
* Bolum bilgisi panelinde "Durum": Temiz / Temiz kapatilmamis / Hazirda
  bekletmede / Bagli. **Bagli birimde uyari gosterilmez**: Windows ve ntfs3
  bagliyken bayragi zaten acar.
* Linux'ta bagli NTFS onarilmaz (once ayirin). Windows'ta uygulama birimi
  kilitleyip ayirir (ADR 0016, 0075).
* Linux/macOS'ta NTFS baglama basarisiz olursa pencere denetimi onerir.

## Yan bulgu: `_is_dirty` hep "temiz" diyordu

`ntfsresize._is_dirty` bayraklari `$VOLUME_INFORMATION` ofset **12**'den
okuyordu ve `len(value) < 14` denetimi yapiyordu. Yapi 12 bayttir
(`reserved u64, major u8, minor u8, flags u16`), bayraklar ofset **10**'da
(ntfs-3g `layout.h`; bizim bicimlendirici de oraya yaziyor). Sonuc: denetim
her zaman `False` donduruyor, **kirli birim boyutlandiriliyordu**. Ofset
10'a duzeltildi; ntfs-3g ayni bayragi "Volume is scheduled for check" diye
okuyarak dogruladi. t77 bunu da sinar.

## Dogrulama

* Sentetik: kirli bayrak + etkin istemcili `RSTR` sayfasi, bozuk asil/yedek
  onyukleme, bozuk ayna/asil MFT kaydi, "HIBR" basligi — t77.
* **Gercek Windows ornegi** (`tests/fixtures/ntfs_windows_kirli.img.gz`):
  bizim biclendirdigimiz birim VBox win10'a takildi, Windows bagladi ve
  uzerine yazdi; **bagliyken** VDI ana makineden okundu. `ntfsfix` (ntfs-3g
  2022.10.3) bu kopya icin "The disk contains an unclean file system (0, 0).
  Metadata kept in Windows cache, refused to mount" dedi; bizim denetim
  `LOG_UNCLEAN`. Onarimdan sonra ntfs-3g "Mounting volume... OK".
  ntfs-3g'nin `ntfsfix -d` ciktisiyla karsilastirma: yalnizca `$Volume`
  kaydi ve aynasi (4 sektor) farkli — ntfs-3g bayraklar degismese de kaydi
  yeniden yazip USN'yi artiriyor; icerik ayni.
* Kopyada Windows'un yazdigi klasor **yoktu**: Windows ustveriyi henuz diske
  islememisti. Bu, uyari metnindeki veri kaybi riskinin olculmus halidir;
  ntfs-3g'nin onarimi da ayni sonucu verir.
* Windows tarafi: ayni kopyadan GPT disk kuruldu, onarim **kuyrukla**
  uygulandi, VDI'ye cevrilip VBox win10'a takildi. Windows birimi F: olarak
  bagladi: `HealthStatus=Healthy`, NTFS olay 98 "Birim F: saglam. Herhangi
  bir eylem gerekmiyor"; 20 dosya okundu, yeni dosya yazildi.
* **Fiziksel disk, Windows (2026-10-03):** VBox win10'da yerlesik
  Administrator (tam yetki) ile misafir icinde 300 MB VHD -> NTFS T:,
  41 dosya, `fsutil dirty set T:` (Windows'un kendi kirli bayragi; saglik
  "Warning"). `tests/physical_ntfsfix_test.py \\.\PhysicalDrive1 --onayla`:
  denetim kirli + gunluk temiz degil; kuyruk "Uygula" (birim kilitlenip
  ayrildi) basarili. Sonra Windows: `fsutil` "NOT Dirty", saglik "Healthy",
  41 dosyanin SHA1'i ayni, `chkdsk T:` "No further action is required",
  `Repair-Volume -Scan` NoErrorsFound. Kilit birakilinca Windows birimi
  hemen yeniden bagliyor; bagli birimin gunlugu her zaman "temiz degil"
  gorundugu icin testin olcutu bagliyken kirli bayrak + yapilardir.
* run_all: Linux 75/77, Windows 74/77 (0 hata; atlananlar arac yok /
  yalnizca Windows dali).

## Sinirlar

Bu bir chkdsk degildir: dizin agaci, guvenlik tanimlayicilari, bitmap
tutarliligi denetlenmez (`ntfsfix` de yapmaz). Pencere onarimdan sonra
Windows'ta chkdsk onerir. Gercek bir "Hizli baslatma" ornegi (HIBR imzali
hiberfil + sistem birimi) olculmedi; imza ntfs-3g'nin kuralindan alindi.
