# 0053 — Sifreli ve kapsayici birimlerin taninmasi, genis imza kumesi

Tarih: 2026-09-29
Durum: **uygulandi** — Linux (gercek arac goruntuleri + blkid karsilastirmasi)
ve Windows (VirtualBox win10, t49) dogrulandi. macOS'ta kosulmadi.
Ilgili: CLAUDE.md fiziksel disk kurali 4 ("bilinmiyor" asla "risk yok" gibi
sunulmaz), [ADR 0025](0025-bekleyen-islem-kuyrugu.md), analiz:
`.claude/docs/dosya-sistemi-genisletme.md` Asama 1.

## Baglam

BitLocker, LUKS, LVM ve RAID bolumleri "Bilinmeyen" gorunuyordu. Kullanici
boyle bir bolumu "bos/tanimsiz" sanip bicimlendirebilirdi; icindeki veri
bizim goremedigimiz sifreli veri ya da baska birimlerdir. Ayrica btrfs,
XFS ve F2FS taniniyor ama doluluklari okunmuyordu.

## Karar

1. `FSInfo`'ya uc **bayrak**: `encrypted`, `container`, `maybe_encrypted`.
   Karar gorunen addan degil bayraktan verilir (ad cevrilir).
2. Sifreli/kapsayici imzalar **dosya sistemi imzalarindan once** yoklanir.
   Gerekce: ucta ustverisi olan bir RAID1 uyesi (md 1.0 / 0.90) basinda
   gecerli bir ext4 tasir; onu "ext4" diye gostermek bicimlendirmeye davet
   etmektir. `blkid` de ayni onceligi kullanir.
3. `operations.risk_notes(session, queue)` yikici adimlarin (bicimlendir,
   sil, guvenli sil, boyutlandir; tablo olustur/sil/donustur tum bolumler
   icin) bu bayrakli bolumlere dokunup dokunmadigini soyler. Uygula penceresi
   bunlari kirmizi uyari olarak listeler. Tespit onbellekten okunur, aygita
   yeniden gidilmez.
4. Imzasi olmayan ama ilk 4 KiB'i rastgele gorunen (entropi > 7.5 bit/bayt)
   bolum `maybe_encrypted` isaretlenir (VeraCrypt, duz dm-crypt). Kesin
   degildir; yalnizca uyari uretir, hicbir islemi engellemez.

## Taninan turler

| Tur | Imza | Bayrak | Ek bilgi |
|---|---|---|---|
| LUKS1 / LUKS2 | `LUKS\xba\xbe` @0 | sifreli | LUKS2 etiketi, UUID |
| BitLocker | OEM `-FVE-FS-` veya FVE GUID | sifreli | |
| CoreStorage | `CS` @88, surum 1 | sifreli + kapsayici | |
| LVM2 PV | `LABELONE` + `LVM2 001` (ilk 4 sektor) | kapsayici | PV UUID |
| Linux RAID | md sihirli sayisi @0 / 4K / son (1.0) / son-64K (0.90) **ve surum alani** | kapsayici | dizi adi |
| APFS | `NXSB` @32 | kapsayici | toplam boyut |
| ZFS | uberblock `0x00bab10c` (iki bayt sirasi) | kapsayici | |
| bcache | sihirli UUID @4K+24 | kapsayici | |
| HFS+ / HFSX / HFS | `H+`/`HX`/`BD` @1024 | | etiket (katalog B-agacinin ilk kaydi), doluluk |
| UDF | ECMA-167 VRS `BEA01`→`NSR02/03` (2K/4K/512 adim) | | etiket (LVD) — ISO koprusunde UDF oncelikli |
| ReFS | OEM `ReFS` + `FSRS` | | |
| JFS, ReiserFS, bcachefs, NILFS2, EROFS, Minix, SquashFS | cekirdek imzalari | | JFS/ReiserFS etiketi |
| btrfs, XFS, F2FS | (vardi) | | **doluluk eklendi** (F2FS: yeni kontrol noktasi) |

Olcumle bulunan: JFS etiketi 0x88'de degil **0x98**'de (0x88 UUID'dir; ilk
surum etiket yerine cop gosteriyordu). F2FS etiketi 0x7C'de UTF-16LE.
Artik veri korumasi: RAID sihirli sayisi tek basina yetmez, surum alani
(1.x ya da 0.90) da tutmali.

Yan duzeltme: ext 64bit birimde bos blok sayisinin ust yarisi (0x158)
okunmuyordu — 16 TiB ustu birimde doluluk yanlis cikardi.

## Dogrulama

* Gercek araclarla uretilen 15 goruntu (mkfs.btrfs/xfs/f2fs/hfsplus/udf/jfs/
  minix, mksquashfs, cryptsetup luksFormat LUKS1/2, mkswap): tur ve etiket
  `blkid -p` ile **ayni**. HFS+ gunluklu, HFSX, UDF 4K blok dahil.
* Gercek araci olmayanlar (BitLocker, LVM, md 1.2/1.0/0.90, APFS, ZFS,
  bcachefs, ReiserFS, ReFS) belgelenmis yerlesime gore kurulan yapay
  baslikla; `blkid` saglama denetimleri yuzunden yapay basliklari kabul
  etmedigi icin bunlar hakemsiz sinandi (ReFS haric).
* `run_all` t49 (her platformda): 5 bayrakli bolum + saglam FAT16 + artik
  RAID sihirli sayisi; `risk_notes` bolum ve tablo adimlari icin.
  Linux ve Windows'ta gecti.
* Uygula penceresi ekran goruntusuyle denetlendi (LUKS2 + LVM2 uyarilari).

## Sinirlar

Yalnizca **taninma**. Bu birimlerin icini acmak (LUKS cozme, LVM mantiksal
birimleri, APFS birimleri) kapsam disidir; APFS salt okuma Asama 4'tedir.
