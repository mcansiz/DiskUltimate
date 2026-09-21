# ADR 0034 — Yeni adimlar diskteki duruma degil, **planlanan** yerlesime gore kurulur

**Tarih:** 2026-09-17
**Durum:** Kabul edildi
**Ilgili:** ADR 0025 (bekleyen islem kuyrugu), ADR 0031 (plan onizlemesi),
ADR 0033 (adim hedefi LBA)

## Baglam

Kullanici `/dev/sdb` uzerinde arka arkaya uc bolum olusturmak istedi: 568 MB
FAT32, 1.40 GB NTFS, 3.03 GB exFAT. Uygula dendiginde birinci adim gecti,
ikincisi durdu:

```
17:19:10 kuyruga eklendi (1): Yeni bolum olustur — 568.00 MB, fat32
17:19:17 kuyruga eklendi (2): Yeni bolum olustur — 1.40 GB, ntfs
17:19:30 kuyruga eklendi (3): Yeni bolum olustur — 3.03 GB, exfat
17:19:34 HATA PartitionTableError: 2 numarali bolum ile cakisiyor
```

Ekran goruntusunde uc adimin da hedefi **ayni**: `LBA 14626816`.

Neden: "Yeni bolum" penceresi bos alani `session.free_regions()` ile
soruyordu — yani **diskteki** duruma gore. Kuyruktaki bolum diske yazilmadigi
icin o alan hala bos gorunuyor; ikinci ve ucuncu bolum de ayni yere kuruluyor.
Uygulama sirasinda birincisi yazilinca otekiler cakisiyor.

Bu, ADR 0033'teki hatanin kardesi: orada **kimlik** (bolum numarasi) diskten
okunuyordu, burada **bos alan**. Ikisinin de koku ayni: kuyruk varken "gercek"
artik diskteki hal degil, **plan**dir.

## Karar

Yeni bir adim kurulurken yerlesim sorusu `planview` uzerinden sorulur:

* `MainWindow.planned_free_regions()` — bos alanlar, bekleyen adimlar
  dusulmus halde. Kuyruk bossa diskteki liste doner (davranis degismez).
* `MainWindow.planned_partitions()` — bolumler, kuyruk uygulandiktan sonraki
  hali. MBR'de "4 birincil bolum doldu mu" sayimi da buradan yapilir; yoksa
  kuyrukta bekleyen bolumler sayilmaz ve besinci bolum kuyruga girebilirdi.
* `planview.overlap_at()` — bir aralik planlanan bir bolumu kesiyor mu?
  "Yeni bolum" ve "boyutlandir" adimlari kuyruga **girmeden once** bununla
  denetlenir. Kullanici pencerede kendi basina baslangic/boyut verdiginde de
  cakisma **tiklama aninda** soylenir, uygulama ortasinda degil.
* Secili bos alan kuyruk degisince yeniden cozulur: eski baslangici kapsayan
  bolgeye, yoksa en buyugune gecilir. Yoksa secim eski (artik var olmayan)
  bolgede kalir ve pencere yanlis yeri onerir.

### Neden "uygulama aninda duzeltmek" degil

`apply()` sirasinda cakisan bir adimi kaydirmak (bir sonraki bos alana atmak)
dusunuldu ve **reddedildi**: kullanicinin gordugu plan ile diske yazilan sey
ayrilirdi. Kuyrugun butun degeri "ne gorduysen o uygulanir"dir. Cakisma bir
kaza degil, planin kendisinin hatalidir; dogru yer **kurulma anidir**.

### Onizleme neden cakismayi cizmiyor

`planview` adimlari dogrulamaz, yalnizca gosterir (ADR 0031). Ust uste iki
bolum planlandiysa ikisi de cizilir. Artik boyle bir kuyruk arayuzden
kurulamadigi icin bu durum yalnizca elle uretilmis kuyruklarda gorulebilir;
onizlemeyi "dogrulayici" yapmak, onizlemenin hicbir sey uretmedigi durumlar
yaratirdi.

## Sonuc

`t41_ust_uste_bolum_planlama` dort seyi olcer:

1. Diske gore sorulan bos alan uc adimda da **ayni** kalir (kok neden),
2. boyle bir kuyruk gercekten kirilir ("cakisiyor" ile durur),
3. plana gore sorulunca alan her adimda kuculur ve uc adim tek Uygula ile
   sorunsuz uygulanir,
4. `overlap_at` planlanan bolumle cakismayi yakalar, uzaktaki alani yakalamaz
   ve bolumu kendisiyle cakisti saymaz.

Duman testi ise arayuz tarafini olcer: bekleyen "yeni bolum" adimindan sonra
`planned_free_regions()` icinde o araligi kesen bir bolge kalmaz.
