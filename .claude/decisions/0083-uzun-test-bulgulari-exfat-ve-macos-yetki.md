# 0083 — Uzun test bulgulari: exFAT bitmap zinciri, macOS yetki komutu

Tarih: 2026-10-04
Durum: **uygulandi**. Ilgili: [0080](0080-uzun-testler-plani.md),
[0042](0042-acilista-kosulsuz-yetki.md), [0039](0039-yetki-yukseltmede-eski-kopya-yasar.md).

## 1. exFAT kucultme: ayirma bitmap'inin zinciri kisalmiyordu

macOS CI'da Apple `fsck_exfat`: "Cluster chain for Main Bitmap has too many
clusters for its size" (kucultmeden sonra). `_exfat_set_bitmap_length`
yalnizca uzunluk alanini dusuruyordu; bitmap'in FAT zinciri ve dolu bitleri
eski boyda kaliyordu. Linux `fsck.exfat` bunu denetlemiyor (o yuzden yerelde
hic gorunmedi). Duzeltme: yeni uzunluga gereken kumede zincir sonlandirilir,
artan kumeler FAT'tan cikarilip bos isaretlenir. Yerelde 4 KiB kumeyle
sinandi: zincir [2,3,4,5] -> [2], artanlar bos, fsck.exfat temiz.

## 2. macOS yetki: Windows tirnaklamasi ve el sikisma yoklugu

* `do shell script` /bin/sh calistirir; komut `subprocess.list2cmdline`
  (Windows kurali) ile birlestiriliyordu. Olculdu: yolda `$HOME` acildi,
  ters tirnak icindeki komut CALISTI (`/Users/a b/$HOME/\`echo X\`` ->
  `/Users/a b//home/pc/X`). Simdi `shlex` (POSIX).
* macOS dali el sikismasiz `ElevatedLaunch()` donduruyordu: acilis kopyasi
  hemen kapaniyor, parola penceresinde Iptal denince uygulama tamamen
  kayboluyordu (ADR 0042: yetki verilmezse yine acilir). Simdi Linux'la ayni
  model: handoff dosyasi + gunluk dizini env ile tasinir, osascript sureci
  izlenir, `-128` "Yetki verilmedi" olur.
* t81: uretilen kabuk komutu /bin/sh ile GERCEKTEN calistirilir; argv ve
  env karakteri karakterine ayni. Gercek parola penceresi otomatik
  sinanamaz (ADR 0080) — bir Mac'te elle denenene kadar acik.

## 3. Test tarafinda duzeltilenler (uygulama hatasi degil)

* Linux vfat cekirdek baglama `utf8` ister (iso8859-1 s/i/Yunanca/Kiril
  adlari gostermiyor).
* Windows seyrek dosyayi VHD olarak takmaz (0xC03A001A): bayrak kaldirilir.
