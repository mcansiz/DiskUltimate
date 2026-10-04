# 0082 — MBR mantiksal bolum: boyutlandirma, tasima ve EBR zinciri

Tarih: 2026-10-04
Durum: **uygulandi** — t80; uzun testler (ADR 0080) `mbr-mantiksal` senaryolari.
Ilgili: [0019](0019-bolum-boyutlandirma.md), [0080](0080-uzun-testler-plani.md).

## Bulgu (uzun testlerin ilk kosusu yakaladi)

MBR mantiksal bolumler **hic boyutlandirilamiyor ve tasinamiyordu**, ve tasima
girisimi bolumu bozabiliyordu. Uc ayri kusur:

1. **Cakisma denetimi:** boyutlandirmanin son adimi genel
   `PartitionTable.check_range`'i cagiriyordu; bu araligi butun bolumlerle,
   mantiksal bolumu **kapsayan genisletilmis bolum dahil** karsilastirir. Sonuc
   her zaman "1 numarali bolum ile cakisiyor". `apply_resize`te veri tasimasi
   (adim 2) tablo yazimindan (adim 3) ONCE oldugu icin, tasima girisiminde veri
   cakisan alana kopyalanmis, tablo eski yerde kalmis oluyordu — eski yerdeki
   dosya sistemi kismen uzerine yazilmis.
2. **EBR zinciri:** MBR kurali: ilk mantiksal bolumun EBR'si genisletilmis
   bolumun ILK sektorundedir, okuyucular zinciri oradan izler. `_write_logicals`
   tasinan bolumun EBR'sini "yeni baslangic − 1 MiB"a yaziyordu; zincirin basi
   eski yeri gostermeye devam ediyordu (hata vermeden).
3. **Pencere:** `window_for` onceki mantiksal bolume bitisik baslangica izin
   veriyordu; EBR'ye yer kalmaz, EBR onceki bolumun verisinin uzerine yazilirdi.
   Olusturma (free_regions) bu boslugu zaten birakiyordu.

## Karar

* `MBRTable.check_range`: bolum mantiksalsa `_check_logical_range` (kapsayicinin
  icinde, diger mantiksallarla cakismadan; kendisi haric).
* `_write_logicals`: ilk EBR her zaman `extended.start_lba`; sonraki EBR onceki
  bolumun sonundan sonra olmali, olamiyorsa yazma REDDEDILIR.
* `window_for`: mantiksal bolumde onceki mantiksal bolumden sonra bir hizalama
  birimi bosluk.

## Dogrulama

* t80: iki mantiksal FAT32; ilkini kucult, saga tasi, geri tasi; ikinciyi
  pencerenin basina tasi, buyut — her adimda icerik, diskten yeniden okunan
  zincir ve `sfdisk --verify` temiz. Eski kodda t80 "1 numarali bolum ile
  cakisiyor" diye duser (dogrulandi).
* Uzun testler quick profil: 11 dosya sistemi x {mbr, gpt, mbr-mantiksal} =
  33/33.
