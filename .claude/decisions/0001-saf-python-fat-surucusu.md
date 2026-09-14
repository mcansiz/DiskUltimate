# ADR 0001 — FAT surucusu sifirdan, saf Python yazildi

**Tarih:** 2026-09-13 · **Durum:** Kabul edildi

## Baglam
Goruntu icindeki bir bolumu bicimlendirmek ve icerigine erismek gerekiyordu.
Secenekler:
1. `loop` aygiti + `mount` (root gerekir)
2. `mtools` (harici paket, yalnizca FAT, kurulu olmayabilir)
3. `pyfatfs` benzeri ucuncu parti kutuphane
4. Sifirdan saf Python FAT surucusu

## Karar
4. secenek: FAT12/16/32 surucusu proje icinde yazildi (`core/fat.py`).

## Gerekce
- **root gerekmiyor.** Proje hedefi, yonetici yetkisi olmadan calismak.
- **Sifir bagimlilik.** Python 3 + PyQt5 disinda hicbir sey kurulmasi gerekmiyor.
- **Tam kontrol.** Kume boyutu, FAT turu, etiket, hizalama gibi parametreler
  dogrudan belirlenebiliyor; DiskGenius benzeri bir arac icin sart.
- **Ogrenilebilirlik.** Bolum/dosya sistemi yapisini gosteren bir arac icin bicimin
  kod icinde acikca durmasi bir deger.

## Bedeli
- Daha fazla kod ve hata riski → `fsck.vfat` ile dogrulama testi zorunlu tutuldu.
- exFAT/NTFS/ext4 icin ayni yaklasim cok pahali; onlar icin ADR 0002'ye bakiniz.

## Dogrulama
`tests/run_all.py::t04_fat_bicimlendirme` uretilen her birimi `fsck.vfat -n` ile
denetler. Bu test sayesinde `BPB_BkBootSec` ofset hatasi yakalandi.
