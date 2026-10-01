# 0058 — NTFS yazma: B+ agaci, guvenlik tanimlayicilari, Windows uyumu

Tarih: 2026-09-29
Durum: **uygulandi** — Linux (ntfs-3g araclari) ve Windows (VirtualBox win10,
VHD olarak baglanip Windows'un kendi NTFS surucusuyle) dogrulandi.
Ilgili: [0017](0017-saf-python-ext-ve-ntfs.md), [0024](0024-ntfs-bitmap-aralikli-yazma.md),
[0037](0037-ntfs-isletim-sistemi-yazabilmeli.md)

## Baglam — olculen eksikler ve hatalar

1. **B+ agaci yoktu:** indeksi `$INDEX_ROOT`a sigmayan ya da INDX blogu dolu
   dizine yazma reddediliyordu (kok tasinca `$INDEX_ALLOCATION` olusturma,
   dugum bolme yok).
2. **Ara dugumdeki giris silinemiyordu:** NTFS B+ agacinda ara dugumler de
   gercek giris tasir; arama o girisi atlayip alta iniyor, "Bulunamadi" diyordu.
3. **Yol cozumu O(n):** her bilesen icin dizinin tamami listeleniyordu —
   buyuk dizine dosya basina ~15 ms (10.600 islem 6 dk 50 sn).
4. **`$MFT` buyumuyordu:** buyutme sabit uzunluklu `$DATA` oznitelugune yeni
   kosu ekliyor, birkac buyutmeden sonra "buyutulemedi"; `$MFT:$BITMAP`in veri
   boyutu hic buyumuyordu — bizim bicimlendiricinin birimi 64 kayitta (48
   kullanici dosyasi) takiliyordu.
5. **Parcali dosya:** kume ayirici ilk bos kumeleri aliyordu; parcali birimde
   3 MB'lik dosya onlarca parcaya bolunup MFT kaydina sigmiyordu.
6. **Windows'ta "dosya bozuk":** yazicinin kayitlarinda NTFS 3.1 kayit numarasi
   alani (0x2C) 0'di; Windows dizini "bozuk ve okunamaz" sayiyordu.
7. **Windows'ta "erisim engellendi":** `$STANDARD_INFORMATION` guvenlik
   kimligi 0'di. ntfs-3g izin denetlemedigi icin Linux'ta hic gorulmemisti.
8. Zaman damgalari yerel saatti (UTC olmali; Windows'ta +3 saat kayma).
9. Siralama uretilmis `$UpCase` tablosuyla yapiliyordu; birimin kendi tablosu
   (eski Windows surumlerinde farkli) kullanilmali.
10. **Yeniden adlandirma yalnizca indeksi degistiriyordu:** kayittaki
    `$FILE_NAME` eski adda kaliyordu; Windows dosyayi eski adiyla gosterdi.
11. **Ad alani Win32 (1) idi:** Windows Win32 adin bir DOS (8.3) adiyla esli
    olmasini bekler; kisa adi olmayan ad POSIX (0) olmali. Windows ilk
    erisimde "onariyordu" (olay 130: "DOS dosya adi ozniteligi yok ...
    isaretleri 0x1 iken 0x0 yapin").
12. **Silme uzun+kisa ad ciftinin birini birakiyordu** ve sabit bagli kaydi
    tamamen serbest birakiyordu.
13. **Bos yaprakta yeniden kurulum silinen girisi geri getiriyordu**
    (`rebuild()` diskten okur, silme henuz yazilmamisti): kayit serbest,
    giris indekste sahipsiz — Windows dizini "bozuk ve okunamaz" saydi.
14. **Yeni MFT kayitlari bicimsizdi** (yalnizca sifir), **sira numarasi**
    yeniden kullanimda 1'e geri sariliyordu, kullanici dosyalari `$MFT`
    ek kayitlarina ayrilan **16-23**e yaziliyordu — Windows "MFT bozuk kayit
    iceriyor" (olay 55).
15. **Dizin indeksi tek blok tek blok, rastgele yere buyuyordu:** parcali
    birimde kosu listesi dizin kaydina sigmiyordu (1670 dosyada).
16. **Kosu uzunlugu isaretsiz kodlaniyordu (eski, yaygin hata):** 138 kume
    tek bayt 0x8A yazilinca ntfs-3g ve Windows onu negatif okuyordu —
    4K kumede ~512 KB-1 MB araligindaki (ve benzeri) her dosya bozuk
    gorunebiliyordu.

## Karar

* `core/ntfsindex.py` — genel B+ agaci (`$I30` dizin ve `$SII`/`$SDH` gorunum
  indeksleri): ekleme + yaprak/ara dugum bolme, kok tasinca reparent
  (once kumeler, sonra kok kuculur, sonra oznitelikler — kayit dolulugu),
  ara dugumden silme (oncul ile degistirme), bos yaprakta dengeli yeniden
  kurulum (%75 doluluk).
* `NtfsFS.lookup` — B+ agaci uzerinden O(log n) arama; `collation_key`
  birimin `$UpCase`ini kullanir.
* `NtfsWriter._set_record_attr` — kayittaki oznitelugu tur/ad sirasini
  koruyarak degistirir/ekler; `$MFT` `$DATA`si ve bitmap'i bununla buyur.
* Kume ayirici once bitisik bosluk arar.
* `core/ntfssecure.py` — devralinan guvenlik tanimlayicisi (Windows kurallari:
  dosyada OBJECT_INHERIT, dizinde CONTAINER_INHERIT + INHERIT_ONLY kopya,
  GENERIC_* -> dosya haklari), `$SDH`de ayni tanimlayici varsa kimligini
  yeniden kullanir, yoksa `$SDS`e (256 KiB aynasiyla) yazar ve `$SII`/`$SDH`e
  ekler. Ust dizinde tanimlayici yoksa mkntfs'in kok tanimlayicisinin aynisi
  ust kabul edilir.
* Kayit numarasi (0x2C), SI'da dizin biti yok, UTC zaman.
* Ad alani POSIX (0). `rename` indeks + kayittaki `$FILE_NAME`i birlikte
  degistirir; `remove` kaydin bu dizindeki tum adlarini (8.3 dahil) kaldirir,
  sabit bagda kaydi yasatir. `_record_attrs/_write_record_attrs` ortak.
* Yeni MFT kayitlari bos FILE kaydi olarak bicimlenir; sira numarasi korunur;
  kullanici kayitlari 24'ten; dizin indeksi ve MFT once onceki parcanin
  ardina, dizin indeksi yarim/64 blokluk adimlarla buyur.
* Kosu uzunlugu isaretli kodlanir.
* `ntfsindex.verify_tree` — sira, yaprak derinligi, sahipsiz giris,
  $BITMAP, INDX VCN; testler her platformda kosar (ntfs-3g bu kurallarin
  bir kismini gormez).

## Dogrulama

* Stres (ana makine): bizim bicimlendirici ve mkntfs birimlerinde 3000 ekleme
  → 1500 silme → 800 ekleme; her adimda `ntfsls` (ntfs-3g'nin kendi B+ agaci
  aramasi) giris kumesi birebir, `ntfsfix -n` temiz, icerik okuma hatasiz.
  Sure 6 dk 50 sn -> 21,7 sn.
* Guvenlik: mkntfs biriminde uretilen dizin/dosya tanimlayicilari Windows'un
  ayni birimde kendi urettikleriyle (267/268) giris giris ayni.
* **Windows (VirtualBox win10):** yonetici yetkisi alinamadigi icin
  `chkdsk` yerine gorunttuler VHD olarak misafirin SATA denetleyicisine
  takildi; Windows'un kendi NTFS surucusu okudu/yazdi, sistem gunlugundeki
  NTFS olaylari (55/98/130) denetlendi. Denetim betigi bagimsiz baslatilip
  20 sn'lik yoklamalarla izlendi (donma 3 dk'da yakalanir).
  Son tur (tum duzeltmelerle), iki birim:
  - mkntfs + bizim yazdiklarimiz (1500 ekle, 750 sil, 1500 ekle — kayit
    yeniden kullanimi, MFT buyumesi, 138 kumelik dosya): 2250 dosya, okuma
    hatasi 0, Windows 300 dosya ekledi.
  - Windows'un kendi yazdigi birim (uzun+8.3 ad ciftleri, iki seviyeli agac)
    + bizim 400 silme, 1500 ekleme, yeniden adlandirma, klasor: 1700 dosya,
    okuma hatasi 0, Windows 200 ekledi 100 sildi.
  - Her iki birim Healthy; olay 55/130 **yok** (onceki turlarda 55 ve 130
    goruldu ve yukaridaki hatalara izlendi).
  - Once: bizim yazdigimiz 1302 dosyanin SHA-1'i Windows'ta birebir.
* Test verisi `tests/fixtures/ntfs_windows.img.gz` (530 KB; Windows'un
  urettigi yapilar, sanal makine olmadan yeniden uretilemez); `run_all` t56.

## Acik kalan

* ~~Saf Python bicimlendirici Windows'ta tanınmiyor~~ — cozuldu, ADR 0059. (MFT
  kayit boyutu 4096 (Windows 512 bayt sektorlu diskte 1024 bekler), 16-23
  bicimsiz, `$Extend` alt kayitlari ve kok guvenlik tanimlayicisi eksik.
* `$ATTRIBUTE_LIST` yok: cok parcali dosya/dizin tek kayda sigmazsa acik
  hata verilir (veri bozulmaz).
