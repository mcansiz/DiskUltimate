# 0086 — Windows surucu harfi islemleri bolumu ofsetle secer

Tarih: 2026-10-04
Durum: **uygulandi**. Ilgili: [0085](0085-ntfs-windows-uyumu-gunluk-kosu-bolum-secimi.md),
[0043](0043-baglama-ve-surucu-harfi.md).

## Baglam

ADR 0085 yerel bicimlendirme ve boyutlandirmada bolumu bayt ofsetiyle secti;
surucu harfi ata/kaldir/sorgula hala `-PartitionNumber <bizim numara>`
kullaniyordu. VM'de olculdu (`tests/physical_drive_letter_test.py`, MBR:
birincil + genisletilmis + iki mantiksal):

| bizim | ofset | Windows numarasi |
|---|---|---|
| 1 birincil | 1 MiB | 1 |
| 5 mantiksal | 63 MiB | **2** |
| 6 mantiksal | 124 MiB | **3** |

Eski kodla #5/#6 icin harf atama "bolum yok" hatasi verirdi; daha fazla
mantiksal bolumlu bir diskte numara **baska bir bolume** denk gelir, harf
yanlis bolume atanir ya da baska bir bolumunki kaldirilirdi. Uygula oncesi
"baglantilari kes" (`_unmount_all`) da ayni yoldan gecer: bagli bir mantiksal
bolum "bagli degil" sanilabilirdi (yazma yine birim kilidiyle korunur, ADR
0075, ama kullaniciya yanlis bilgi verilir).

## Karar

* `platform.win_letter_command(eylem, disk, ofset, nokta)` — sorgu/atama/
  kaldirma komutu `win_partition_selector` ile; bolum bulunamazsa `throw`.
* `partition_mount_point`, `mount_partition`, `unmount_partition` (platform ve
  physical) `offset` alir; arayuz `start_lba * sector_size` gecirir.
* Windows'ta ofsetsiz harf islemi **reddedilir** — ozellikle cikarmada "bagli
  degil, basarili" donmez (Uygula bu sonuca guvenir).
* Linux/macOS degismez: bolum aygit adi (`sdb5`, `disk4s5`) bizim numarayla
  zaten aynidir.

## Dogrulama

* t86: komut uretimi (numara yok, ofset var, `throw`, tirnak kacisi).
* t43: ofset physical -> platform katmanina tasinir.
* VM (VirtualBox win10, takili VHD): butun harfler kaldirildi; ikinci
  mantiksal bolume harf atandi -> Windows'ta yalnizca o ofsette harf; harf
  kaldirildi -> hic harf yok. SONUC: BASARILI.
