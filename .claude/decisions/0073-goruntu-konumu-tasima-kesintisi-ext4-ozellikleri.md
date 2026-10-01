# 0073 — Goruntu konumu, tasima kesintisi, Qt cevirisi, ext4 ozellikleri, ekleme ilerlemesi

Tarih: 2026-10-01
Durum: **uygulandi** — Linux'ta goruntu dosyalariyla, Windows'ta VBox win10'da
`tests.run_all` ile sinandi.
Kaynak: `.claude/logs/2026-10-01-ext4-genisletme-analizi.md` (H1–H5) ve
kullanicinin "dosya yuklerken ne kadar kaldigini gormek istiyorum" istegi.

## 1. Goruntu `/dev` icine olusturulmaz (H1)

Varsayilan klasor acik oturumun klasorunden geliyordu; uygulama fiziksel
diski (`/dev/nvme0n1`) acmis oldugu icin bu `/dev` (devtmpfs, bellekte) oldu
ve 20 GB'lik goruntu RAM'i doldurdu.

* `platform.user_home()` — pkexec/sudo altinda **cagiran** kullanicinin evi
  (`/root` degil). `default_image_dir()` bunu kullanir.
* `platform.suggested_image_dir(yol, fiziksel)` — fiziksel aygit ya da aygit
  yolu (`/dev/...`, `\\.\...`) ise varsayilan klasor.
* `platform.image_location_problem(klasor, boyut, seyrek) -> (engel, uyari)`:
  * engel: aygit klasoru, `/proc` `/sys` `devtmpfs` gibi sahte dosya
    sistemleri; seyrek olmayan goruntu icin yetersiz yer;
  * uyari: tmpfs/ramfs (RAM kullanir, yeniden baslatmada silinir); seyrek
    goruntu icin bos yer goruntu boyundan az.
  Bagli dosya sistemi turu `/proc/self/mounts`'tan okunur (alt surec yok).
* Yeni goruntu penceresi engelde reddeder, uyarida sorar. `new_vhd`,
  `clone_disk` ayni klasor kuralini kullanir.

## 2. Tasima kesintisi dogru bildirilir (H2)

Sola tasimada hedef kaynakla ortusur; kayma miktarindan fazlasi
kopyalandiktan sonra kaynagin basi ezilir. Eskiden ENOSPC sonrasinda mesaj
"0 adim uygulandi" idi, bolum ise ne eski ne yeni yerinde okunabiliyordu.

* `resize.MoveInterrupted(ResizeError)`: `moved_sectors`, `source_intact`
  (`kayma >= toplam` ya da `tasinan <= kayma`).
* Mesaj iki turludur: "bolum eski yerinde saglam, tablo degismedi" ya da
  "bolum su an BOZUK; tasima: sektor A -> B, N sektor, M tamamlandi;
  yedekten donun ya da yer acip bu sayilarla tamamlatin". Sayilar tanilama
  gunlugune de yazilir.

## 3. Bos alan kopyalanmaz, yer on denetimi (H3)

* `_move_data` her parcada kaynak ve hedefi okur, **ayniysa yazmaz** (en
  yaygin durum: ikisi de sifir). Seyrek goruntu sismez. Olculdu (kullanicinin
  20 GB'lik diskinin seyrek kopyasi, ext4 7,75 -> 9,77 GB sola tasima):
  goruntu 1,0 GB -> 1,5 GB (eskiden tam boy 21 GB), sure 17,6 -> 8,6 sn;
  1963 dosya birebir, e2fsck temiz.
* Seyrek normal dosya goruntusunde tasimadan once: gereken yer =
  kaynak araliginin **ayrilmis** bayti (`SEEK_DATA/SEEK_HOLE`, ihtiyatli),
  bos yer yetmezse hicbir sey yazilmadan ret.

## 4. Qt'nin kendi metinleri bosalmaz (H4)

`qt_i18n._ButtonTranslator.translate` bilmedigi metne `""` donduruyordu.
PyQt bunu **bos ama gecerli** ceviri olarak iletir: dosya penceresinin butun
etiketleri ve tur sutunu bosaldi, "%1 File" yer tutucusunu kaybettigi icin
her acilista `QString::arg: Argument missing` uyarisi basildi. Artik `None`
(null QString) doner; Qt siradaki cevirmene (qtbase_tr) gecer.
`tests.ui_smoke` dosya penceresi metinlerinin bos olmadigini ve "%1"in
korundugunu denetler (eski kodda basarisiz oluyor).

## 5. ext4 bicimlendirici mkfs.ext4 varsayilanlarina yaklasti (H5)

ADR 0017'de extent kapaliydi, cunku yazici extent bilmiyordu. Yazici
(ADR 0057), tasiyici ve buyutucu artik mkfs.ext4 birimlerinin tamamini
isliyor; geride kalan bicimlendiriciydi.

* ext4 (varsayilan): `extent`, `flex_bg` (16 grup), `metadata_csum`
  (ustblok, GDT, bitmap, inode, dizin kuyrugu; `ITABLE_ZEROED`,
  `itable_unused`). Kok, lost+found ve gunluk inode'lari extent agaci.
  Gunluk boyu mkfs tablosu, ust sinir 4 x 32768 blok (inode icinde derinlik
  0; mkfs 128 GB ustunde 1 GB secer, burada 512 MB).
* **Bilincli disarida:** `64bit` (16 TiB alti icin gereksiz) ve
  `resize_inode` (cevrimdisi buyutucu onsuz calisir; cekirdegin cevrimici
  buyutmesi meta_bg'ye gecer). Ikisi de sonradan eklenebilir.
* `modern=False` eski ext4 setini (3.18 oncesi cekirdekler) uretir.
* ext2/ext3 degismedi.

Dogrulama: 4 MB–20 GB, 1K/4K blok `e2fsck -fn` temiz; kendi saglama
dogrulayicimiz (`extcsum.verify_volume`) her yapida tutuyor;
`tests.ext_write_check` 4/4; `tests.ext_resize_check` 32/32 — yeni
"du-ext4" satirlari birimi **libext2fs ile doldurup** (debugfs write)
buyutur, zincirli buyutur (meta_bg'ye gecis) ve en kucuge indirir.
Cekirdekle baglama yapilmadi (ana makinede root yok).

## 6. Dosya ekleme ilerlemesi ve kalan sure

Tek dosyada cubuk belirsizdi, cok dosyada yalnizca dosya bitince ilerliyordu.
Dosya sistemi yazicilari dosyayi tek parca yazdigi icin "dosyanin ne kadari
yazildi" bilgisi disari cikmiyordu.

* `BlockDevice.write` icin istege bagli **yazma gozlemcisi**
  (`aygit.write_observer`): her yazimdan sonra bayt sayisi bildirilir,
  gozlemci varken buyuk yazimlar 4 MB'lik parcalara bolunur. Gozlemci yokken
  davranis ayni. Boylece ilerleme her dosya sisteminde (FAT, exFAT, ext,
  NTFS, HFS+, UDF, XFS...) tek yerden gelir.
* `FileSystemAccess.import_files(yollar, hedef, progress)` ve `import_tree`
  ilerlemeyi dosyanin icinde de verir; ~1 MB'ta bir bildirilir (FAT sektor
  sektor yazar: 40 MB = 80 000 yazim).
* Ilerleme penceresi (`dialogs/task.TaskDialog`) **butun uzun islemler
  icin** "Gecen: 0:04 · Kalan: yaklasik 0:09" gosterir. Kalan sure olcum
  basindan bu yana ortalama hizla hesaplanir; ilk 2 sn ve %1'den once tahmin
  yapilmaz, yuzde geri giderse olcum yeniden baslar, belirsiz islerde yalnizca
  gecen sure yazilir.
* Sinir: dosya yazilmadan once tek parca okunur (yazicilarin arayuzu); cok
  buyuk tek dosyada okuma suresince cubuk %0'da durur, gecen sure yurur.
