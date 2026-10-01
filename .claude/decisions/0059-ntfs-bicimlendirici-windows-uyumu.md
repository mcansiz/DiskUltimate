# 0059 — Saf Python NTFS bicimlendirici: Windows uyumu

Tarih: 2026-09-29
Durum: **uygulandi** — VirtualBox win10'da Windows'un kendi NTFS surucusuyle
dogrulandi (birimler **Healthy**, bozulma olayi yok).
Ilgili: [0037](0037-ntfs-isletim-sistemi-yazabilmeli.md),
[0058](0058-ntfs-b-agaci-guvenlik-ve-windows-uyumu.md)

## Baglam

`format_ntfs` ile bicimlendirilen birim ntfs-3g ile sorunsuzdu, ama Windows'ta:

1. **Tanınmiyordu** — `Get-Volume` FileSystemType `Unknown`.
2. Tanindiktan sonra (asagidaki 1. tur duzeltmelerle) bos birimde bile
   **olay 55** ("dosya sistemi yapisinda bozulma, cesidi bilinmiyor") ve
   olay 98 ("tam Chkdsk icin cevrimdisi yapilmali"), saglik `Warning`.

`mkntfs` ile ayni boyutta birim uretilip kayit kayit karsilastirildi
(`.tmp/win/dump.py`; mkntfs birimi Windows'ta Healthy'di).

## Olculen farklar ve karar

1. tur (tanınmama):
* **MFT kaydi 4096 bayttı** — Windows 512 bayt sektorlu diskte 1024 bekler.
  `MFT_RECORD_SIZE = 1024`, baslangic MFT'si 64 kayit.
* 16-23 bicimsizdi → tumu bos FILE kaydi olarak yazilir.
* Kok dizin kucuk (yerlesik) indeksteydi; 1 KiB kayda sistem girisleri
  sigmaz → mkntfs gibi INDX blogu + `$INDEX_ALLOCATION` + `$BITMAP:$I30`.
* Kokte guvenlik kimligi yoktu → `SECURITY_ID_ROOT` (0x102), tanimlayicisi
  mkntfs'in kok DACL'i (devralinabilir); `$SDS`/`$SII`/`$SDH` 3 giris.

2. tur (olay 55):
* **`$Extend` bostu.** Windows birimi baglarken `$Extend\$Quota`, `$ObjId`,
  `$Reparse`i acar. Eklendi: kayit 24/25/26 (bayrak 0x0D: kullanimda |
  $Extend | gorunum indeksi), `$Quota:$O` (SID siralama 0x11,
  Administrators → sahip 0x100), `$Quota:$Q` (ULONG, sahip 1 ve 0x100 icin
  varsayilan sinirsiz kota), `$ObjId:$O` ve `$Reparse:$R` (0x13, bos);
  `$Extend:$I30` uc girisi tasir; MFT bitmap'inde 24-26 dolu.
  Deger baytlari mkntfs ile **zaman damgalari disinda birebir** (olculdu).
* $Volume, $AttrDef, $Boot, $UpCase ve 12-15'in `$STANDARD_INFORMATION`i
  48 bayttı (NTFS 1.2 bicimi) ve `$SECURITY_DESCRIPTOR` da yoktu — hic
  guvenlik bilgisi yok. Hepsi 72 bayt + `SECURITY_ID_SYSTEM`.
* 12-15'in bag sayisi 1'di; hicbir dizinde adlari yok → 0.
* Sira numarasi: 0 ve 24+ icin 1 (mkntfs ile ayni).

Eklenmeyenler (gerek gorulmedi, Windows Healthy): `$UpCase:$Info`
(Windows 8+ kendisi yazar), `$UsnJrnl`, `$RmMetadata` (Windows gerektiginde
olusturur).

## Dogrulama

* Ana makine: ntfsfix temiz; `ntfsls` `$Extend` altinda uc dosyayi listeler;
  run_all 55/57 (2 yalnizca-Windows atlandi), yeni **t57** yapilari her
  platformda denetler (ntfs-3g varsa ek olarak onunla).
* **Windows (VirtualBox win10):** MBR + tek NTFS bolumlu (0x07, LBA 2048)
  iki goruntu VHD olarak takildi:
  - BIZBOS (bos): Windows 500 dosyalik klasor olusturdu.
  - BIZDOLU (bizim yazicimizla 300 dosya + 3 MB dosya): 301 dosyanin SHA-1'i
    ana makinedekiyle birebir; Windows 200 dosya ekledi, 100 sildi.
  - Iki birim **Healthy**; Ntfs olay 98 "sağlam, herhangi bir eylem
    gerekmiyor"; olay 55/130 yok.
