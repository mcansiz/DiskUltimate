# 0075 — Windows: disk birimleri GUID ile bulunup kilitlenir

Tarih: 2026-10-01
Durum: **uygulandi** — birim taramasi VBox win10'da (yonetici olmadan)
dogrulandi; **kilitleme yonetici ister, guestcontrol ile sinanamadi** —
kullanicinin yonetici olarak calistirdigi exe ile sinanacak.
Ilgili: [0021](0021-acik-aygiti-yoklamama-ve-kopyalama-ilerlemesi.md),
[0043](0043-baglama-ve-surucu-harfi.md) (baglama/cikarma), [0025](0025-bekleyen-islem-kuyrugu.md).

## Ariza (kullanici, VBox win10, PhysicalDrive1 = 2 GB test diski)

Bolum 3 (exFAT) kucultme + bolum 4 (ext4) genisletme kuyrugu her denemede
"0 adim uygulandi — Yazma reddedildi (LBA 1577344): Windows bagli bir birimin
sektorlerine dogrudan yazmayi engeller" ile durdu.

Gunlukler (`%LOCALAPPDATA%\DiskUltimate\logs`):
1. "Diskteki baglantilari kes" bolum 1-4'un **harflerini** kaldirdi
   (`Remove-PartitionAccessPath`). Yazma moduna geciste kilit, listelemede
   ogrenilen eski harflerle (`\\.\E:`) denendi -> Windows hatasi 2 (yok).
2. Yeni oturumda harf olmadigi icin "bagli birim yok, kilit gerekmiyor"
   yazildi; hemen ardindan yazma "bagli birim" diye reddedildi.

Neden: Windows'ta harfi kaldirmak birimi **ayirmaz**; dosya sistemi surucusu
birimi bagli tutar, `\\?\Volume{GUID}` yolundan erisilebilir ve disk
sektorlerine dogrudan yazma engellenir. Kilit kodu birimleri yalnizca surucu
harfiyle buluyordu.

## Karar

`physical._win_volumes_on_disk(disk_no)`:
* `FindFirstVolumeW/FindNextVolumeW` ile butun birimler (harfsizler dahil),
* CD/DVD ve ag birimi `GetDriveTypeW` ile acilmadan elenir,
* tutamac **erisim hakki istenmeden** acilir (yalnizca
  `IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS`); okuma hakkiyla acmak birimi
  baglatirdi,
* gosterim adi `GetVolumePathNamesForVolumeNameW` (harf/klasor) ya da kisa
  GUID.

`_win_lock_volumes` hedefleri bu listeden alir ve her birimi GUID yoluyla
`FSCTL_LOCK_VOLUME` + `FSCTL_DISMOUNT_VOLUME` eder. Tarama basarisiz olursa
eski yol (listelemedeki harfler) kullanilir. Acik aygit kutugune bakilmaz —
2026-09-15 arizasi da harflerin kutuk yuzunden atlanmasiydi.

## Dogrulama
* VBox win10 (yonetici degil): disk 1'de harfsiz 3 birim bulundu; eski harf
  taramasi bos dondu. Disk 0'da C: ve harfsiz sistem birimleri.
* Kilitleme ve yazma: kullanicinin yonetici exe'siyle sinanacak (bu ADR
  sonuc gelince guncellenir).
