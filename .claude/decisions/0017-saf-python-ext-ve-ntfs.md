# ADR 0017 — ext2/3/4 ve NTFS saf Python'a tasindi

**Tarih:** 2026-09-13 · **Durum:** ✅ Tamamlandi — ext ailesi ve NTFS

> Karar ilk yazildiginda NTFS kismiydi. NTFS uc katmanli strateji ile
> tamamlandi ([ADR 0018](0018-ntfs-platform-stratejisi.md)); `t17_ntfs`
> testi gecer ve uretilen birim Windows `chkdsk` ile temiz dogrulanir.

## Baglam
Kullanici DiskGenius'un bicimlendirme listesini gosterdi: NTFS, FAT32, FAT16,
FAT12, exFAT, EXT4, EXT3, EXT2 — sekiz bicim. Bizde Windows'ta yalnizca dordu
cikiyordu (FAT ailesi + exFAT), cunku NTFS ve ext harici `mkfs.*` araclarina
bagliydi ve o araclar Windows'ta yok.

Kullanicinin degerlendirmesi: *"linuxda oluyor windowsda olmuyor, kabul
etmiyorum; bu uygulama 3 platformda da calisacak."*

## Arastirma bulgusu
Kullanicinin onerisiyle KDE Partition Manager ve GParted incelendi. **Ikisi de
kendi bicimlendiricisini yazmiyor**; harici `mkfs.*` araclarini cagiriyorlar
(calisma aninda tespit ederek). Yani bu alanda "devlerin" cozumu de bizim
basladigimiz yerdi; saf Python yol onlarin otesine geciyor.

NTFS'in tek acik referansi `ntfsprogs/mkntfs.c` (GPL). Bicim Microsoft
tarafindan belgelenmemistir.

## Karar
Once arayuz duzeltildi: bicimlendirme listesi artik **tum** dosya sistemlerini
gosterir; kullanilamayanlar secilemez ve yaninda **nedeni** yazar
(`formatter.all_kinds` + `FsKind.reason_for`). "Secenek yok" yerine "neden yok".

Ardindan iki bicim saf Python'a tasindi:

### ext2 / ext3 / ext4 — TAMAM (`core/ext.py`)
Superblok, grup tanimlayicilari, blok/inode bitmapleri, inode tablosu, kok dizin
ve `lost+found`, ext3/ext4 icin JBD2 gunlugu.

Yol boyunca uc hata bulundu ve duzeltildi:
1. **`resize_inode` bayragi** aciktı ama inode 7 kurulmamisti → `e2fsck`
   "resize inode not valid". Tutarsiz bayrak kaldirildi.
2. **`s_journal_uuid` dolduruldu** → e2fsck "Dis gunluk bulunamiyor". Ic gunlukte
   bu alan **bos** kalmali; yalnizca `s_journal_inum` yazilir.
3. **Gunluk yazim sirasi** — gunluk alani sifirlanirken inode'un dolayli tablo
   blogu da siliniyordu (o blok araligin ortasinda). Once alan sifirlanip
   superblok yaziliyor, dolayli tablo sonra dolduruluyor.

Gunluk boyutu tek dolayli blogun kapasitesiyle (12 + blok/4) sinirlandi; iki
kademeli esleme bu surumde uygulanmadi.

**Dogrulama:** `fsck.ext2/3/4 -nf` uctü de **temiz**; Pop!_OS misafirinde
cekirdek uctü de **bagladi**, dosya/klasor yazildi, `lost+found` olustu ve
yazma sonrasi fsck yine temiz.

### NTFS — KISMI (`core/ntfs.py`)
Onyukleme sektoru ve yedegi, MFT (32 kayit), $MFTMirr, $LogFile, $Volume,
$AttrDef, $Bitmap, $Boot, $BadClus, $Secure, $UpCase, $Extend, kok dizin.
$AttrDef ve $UpCase tablolari gomulu (upcase Python'dan uretilip 244 istisnayla
duzeltilir; sonuc `mkfs.ntfs` ciktisiyla **birebir ayni**, kaynak maliyeti 3,5 KB).

Bulunan ve duzeltilen hatalar:
1. **Dizin girisleri sirasizdi.** NTFS dizinleri siralama kuralina gore sirali
   olmak zorunda; sirasiz dizinde `ntfs_pathname_to_inode` dosyayi bulamiyor ve
   birim "$Secure yok" diye acilmiyordu. Girisler $UpCase anahtariyla siralandi.
2. **Sira numaralari** kayit numarasina esitlendi; MFT basvurulari bu numarayi
   ust bitlerde tasir.
3. **Oznitelik kimlikleri cakisiyordu** (SI ve FN ikisi de 0) → `chkdsk`
   "attribute record is corrupt". Kimlikler kayit icinde otomatik atanir.
4. **Rezerve kayitlarda (12-15) $FILE_NAME vardi**; referansta yok, kaldirildi.
5. **$BadClus:$Bad** seyrek bayragiyla yazilmisti; referans bicim bunun yerine
   **ofset alani olmayan** bir veri kosusu kullanir (tahsis edilmemis aralik).

**Dogrulama durumu:**
| Ortam | Sonuc |
|---|---|
| `ntfsinfo -m` (ntfs-3g) | ✅ cikis 0, birim ve etiket okunuyor |
| `ntfsfix -n` | ✅ "processed successfully" |
| Windows `chkdsk` — Asama 1 | ✅ "The type of the file system is NTFS", 32 kayit temiz |
| Windows `chkdsk` — Asama 2 | ⚠️ `$Extend` alt yapilari eksik ($ObjId, $O indeksi) |
| Windows `Get-Volume` | ⚠️ `FileSystemType: Unknown` (birim otomatik baglanmiyor) |

Yani NTFS **yapisal olarak gecerli** (Linux araclari kabul ediyor, Windows
bicimi taniyor) ancak Windows'un tam kabulu icin `$Extend` altindaki
$ObjId/$Quota/$Reparse yapilari ve $O indeksi eksik.

## Sonuc
Bicimlendirme listesi artik uc platformda da **sekiz bicim** gosteriyor; yedisi
(FAT12/16/32, exFAT, ext2/3/4) tam dogrulanmis durumda. NTFS yazma yolu calisiyor
ama Windows tarafinda tamamlanmayi bekliyor — bu, `.claude/docs/diskgenius-parity.md`
icinde acikca isaretlenmistir.
