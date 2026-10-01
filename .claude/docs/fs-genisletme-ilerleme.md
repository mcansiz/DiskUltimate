# Dosya Sistemi Genisletme — Ilerleme Listesi

Kaynak: `.claude/docs/dosya-sistemi-genisletme.md` (analiz, 2026-09-28).
Kullanici karari (2026-09-29): *"asama 1 ile basla ve tum asamalari uygula,
loop dongude calis. Linux'ta ext4 buyutme calismiyor, bu kabul edilemez.
3 platformda var olan disk tiplerinde bicimlendirme, boyutlandirma gibi tum
islemleri yapabilmeliyiz."*

Dongu her turda bu dosyayi okur, ilk bitmemis maddeyi alir, bitirince
isaretler ve `worklog.md`'ye yazar.

**Test ortami:** Linux VM yok. Ana makinede yalnizca goruntu dosyalari
(`.tmp/tests/`). Dogrulama araclari: sistemde `e2fsck`, `resize2fs`,
`xfs_repair`, `btrfs`, `fsck.vfat`, `fsck.exfat`, `ntfsfix`, `blkid`,
`xorriso`; kullanici alanina acilmis paketler `.tmp/tools/root/`
(`fsck.hfsplus`, `mkfs.hfsplus`, `fsck.f2fs`, `mkfs.f2fs`, `mkudffs`,
`udfinfo`, `mdadm`) — `LD_LIBRARY_PATH=.tmp/tools/root/usr/lib/x86_64-linux-gnu`.
Windows: VirtualBox `"win10 "`.

Isaretler: `[ ]` bekliyor · `[~]` suruyor · `[x]` bitti · `[-]` kapsam disi (gerekce yazili)

## P0 — ext2/3/4 boyutlandirma (kullanici: "kabul edilemez")
- [x] ext tasima: ext mutlak ofset tasimaz → yalnizca veri tasinir; `movable=True`
- [x] ext buyutme saf Python (`extresize.py`, `extlayout.py`) — meta_bg gecisi dahil
- [x] ext kucultme (`extmove.py`) — blok + inode tasima, htree, saglamalar
- [x] resize.py / layoutedit / geri yukleme doldurma baglantisi
- [x] Testler: `tests/ext_resize_check.py` 28/28, `run_all` t48; Windows capraz dogrulama
- [x] Yan bulgular: extwrite bigalloc/quota bayragi, crc16 grup saglamasi,
      okuyucu/yazici meta_bg destegi; t43 Windows'ta yanlis beklenti

## Asama 1
- [x] Taninma: BitLocker, LUKS1/2, LVM2 PV, mdraid 1.x/0.90, HFS+/HFSX, APFS,
      ReFS, UDF, JFS, ZFS, SquashFS, bcachefs (+CoreStorage, bcache, ReiserFS,
      NILFS2, EROFS, Minix); bayraklar + Uygula uyarisi (ADR 0053)
- [x] btrfs / XFS / F2FS kullanilan alan
- [x] Linux takas bicimlendirme (saf Python) + `FS_KINDS` (ADR 0054; mkswap ile bayt bayt ayni)
- [x] Kayip bolum taramasi: ext, btrfs, XFS, HFS+, APFS, F2FS, takas, ReFS (ADR 0054)
      + HATA: geri ekleme kurtarilan FS'i siliyordu, tur secilmiyordu — duzeltildi
- [x] ISO9660 salt okuma (Joliet + Rock Ridge, cok parcali dosya) + tablosuz disk
      destegi (duz .iso, super disket USB, ext4.img) — ADR 0055
- [x] `FsSpec` kutugu (`core/fsregistry.py`, ADR 0056) + `fs_display()`:
      arayuz adi ceviriyor, veri cevrilmiyor

## Var olan tiplerde eksik islemler
- [x] ext yazma: extent agaci buyutme, htree'ye dogru ekleme (HATA: indeks eziliyordu),
      dizin buyumesi, baslatilmamis gruplar, yer on denetimi — ADR 0057
- [x] NTFS yazma: B+ agaci, guvenlik tanimlayicilari, Windows uyumu (ADR 0058).
      Windows'un kendi surucusuyle dogrulandi; 16 hata bulunup duzeltildi
- [x] exFAT/FAT/NTFS/ext: hepsinde tasima + buyutme + kucultme (matris testi
      `tests/resize_matrix.py`): Linux 16/16 (her adimda fsck.vfat/fsck.exfat/
      ntfsfix/e2fsck temiz), Windows 16/16, sonuc birimleri Windows'un kendi
      surucusunde Healthy + 128 dosya SHA-1 birebir. **macOS: ortam yok, sinanmadi.**
      Matris 3 hata buldu (FAT/exFAT alt dizin buyumesi, exFAT dizin boyutu,
      bos exFAT dosyasi) — duzeltildi, t58

- [x] NTFS bicimlendirici (saf Python) Windows'ta tanınmiyordu (Unknown, sonra
      olay 55): 1 KiB MFT kaydi, INDX'li kok + kok SD, $Extend\$Quota/$ObjId/
      $Reparse, sistem dosyalarinda guvenlik kimligi — ADR 0059. Windows'ta
      Healthy, 301 dosya SHA-1 birebir; t57

## Asama 2
- [x] HFS+ okuma (ADR 0060): HFS+/HFSX, katalog + tasma B-agaci, sabit/sembolik
      bag, decmpfs zlib, HFS sarmalayici; xorriso (libisofs) ve mkfs.hfsplus
      birimleriyle, t59. macOS'ta sinanmadi; APM tablosu okunmuyor (not)
- [x] HFS+ bicimlendirme (gunluksuz, ADR 0061): mkfs.hfsplus ile bayt bayt ayni
      yerlesim (tarih/kimlik disinda), fsck.hfsplus 1M-40G temiz, HFSX API, t60
- [x] HFS+ yazma (ADR 0062): mkdir/yaz/sil/adlandir/tasi, genel B-agaci (bolme,
      silme, kok cokmesi, dosya buyumesi, katalog tasma kapsamlari), Apple katlama
      tablosu kuralla uretilir (65 536 giris ayni). fsck.hfsplus stres temiz, 7z;
      t61. macOS'ta sinanmadi; sabit bag/sikistirma yazma kapsam disi
- [x] UDF 2.01 okuma (ADR 0063): 1.02-2.60, tip1/yedekli/metadata bolum, FE/EFE,
      tum kapsam turleri; Windows'un yazdigi 402 dosya birebir, genisoimage koprusu,
      t62. Metadata bolumu ve VAT sinanmadi (uretici yok)
- [x] UDF 2.01 bicimlendirme (ADR 0064): mkudffs hd yerlesimi (512-4096 blok),
      Windows 10 MBR/GPT'de bagladi + 301 dosya yazdi (Healthy, SHA-1 birebir),
      udfinfo ayni sayilar; t63
- [x] UDF 2.01 yazma (ADR 0065): fiziksel bolum + bitmap; EFE/FE, gomulu/short_ad/
      AED, artimli dizin ekleme, Windows'un silinmis FID'leri; Windows iki yonlu
      (2499 dosya okudu/yazdi, bizim yazdiklarimizi tekrar okudu, Healthy); t64

## Asama 3
- [x] XFS okuma (ADR 0066): v4/v5, kisa bicim/blok/dugum dizin, B-agacli catal,
      uzak bag; mkfs.xfs -p ile 5067 dosya birebir + varyantlar; t65
- [x] XFS bicimlendirme (v5, ADR 0067): sade ozellik kumesi (crc/ftype/finobt/
      bigtime/inobtcount); mkfs.xfs ile inode obegi disinda bayt bayt ayni,
      xfs_repair temiz; t66. Cekirdek baglamasi sinanmadi (root yok)
- [x] XFS buyutme (ADR 0068): cevrimdisi; son AG uzatma + yeni AG (rmapbt/reflink
      dahil), gunluk temizligi denetimi, tasima; xfs_repair temiz, icerik birebir;
      t67. Windows 64/67 (2026-10-01, misafir yeniden baslatildiktan sonra)
- [x] btrfs okuma (ADR 0069): zlib + saf LZO1X + saf zstd (RFC 8878), alt hacim,
      DUP/karisik/4K-64K dugum; 1507 dosya birebir, zstd CLI ile capraz; t68
- [x] F2FS okuma (ADR 0070): NAT + NAT gunlugu, satir ici veri/dizin, dolayli dugumler,
      saf LZ4; 5036 dosya birebir; t69. F2FS sikistirmasi sinanmadi (uretici yok)

## Asama 4
- [x] APFS salt okuma (ADR 0071): macOS uretimi kapsayici (dfvfs, Apache 2.0) libfsapfs
      ile birebir; sifreli birim reddi; mkapfs; t70. Kucuk test verisi — cok duzeyli
      agac, cok birim, decmpfs sinanmadi
- [x] ReFS (ADR 0072): Windows Format-Volume, yalnizca fiziksel disk ve uygun surum
      (Enterprise / Pro for Workstations / Server); digerlerinde gri + neden; t71.
      Gercek ReFS bicimlendirme sinanmadi (misafir Win10 Pro, yonetici yok)

## Tur kaydi
(her tur bir satir: tarih, madde, sonuc)
- 2026-09-29 tur 1: P0 ext boyutlandirma bitti (ADR 0052). Linux 28/28 + t48,
  Windows t48 + capraz e2fsck temiz. macOS kosulmadi.
- 2026-09-29 tur 2: taninma genisletme + doluluk (ADR 0053). t49 Linux+Windows,
  ui_smoke (surukleme testi ext artik boyutlandirilabildigi icin uyarlandi),
  t43'un iki Windows beklenti hatasi duzeltildi → Windows 48/49 + t48 atlandi.
- 2026-09-29 tur 2 (devam): takas + kayip bolum imzalari (ADR 0054). Kayip
  bolum geri eklemenin veriyi sildigi bulundu ve duzeltildi. Linux 49/51,
  Windows 49/51.
- 2026-09-29 tur 2 (devam): ISO9660 + tablosuz disk (ADR 0055). Iki veri
  kaybi hatasi daha bulundu (tablosuz diskte silmeden once ret yok; yedek
  onizleme varsayimi). Linux 51/53, Windows 51/53.
- 2026-09-29 tur 3: FsSpec kutugu (ADR 0056). Asama 1 bitti. Linux 52/54,
  Windows 52/54.
- 2026-09-29 tur 3 (devam): ext yazma htree/extent (ADR 0057). Linux 53/55,
  Windows 52/55 + capraz e2fsck temiz.
- 2026-09-29 tur 4: NTFS yazma (ADR 0058). Windows'ta VHD takma + olay gunlugu
  ile dogrulama; son tur temiz (olay 55/130 yok). Linux 54/56.
- 2026-09-29 tur 5: NTFS bicimlendirici Windows'ta Healthy (ADR 0059);
  boyutlandirma matrisi 16/16 Linux + Windows, 3 FAT/exFAT hatasi (t58).
- 2026-09-29 tur 6: HFS+ okuma/bicimlendirme/yazma (ADR 0060-0062),
  fsck.hfsplus temiz; UDF okuma/bicimlendirme/yazma (ADR 0063-0065),
  Windows iki yonlu. Asama 2 bitti. Linux 62/64, Windows 61/64.
- 2026-09-29 tur 7: XFS okuma (ADR 0066). Linux 63/65, Windows 62/65.
- 2026-09-29 tur 8: XFS bicimlendirme (ADR 0067). Linux 64/66, Windows 63/66.
- 2026-09-29 tur 9: XFS buyutme (ADR 0068). Linux 65/67; Windows kosulamadi
  (ana makine diski doldu, win10 misafiri 'aborted'). Dongu durduruldu.
- 2026-10-01: kullanici diski buyuttu (68 GB bos); commit a5f9725 push edildi;
  misafir yeniden baslatildi, Windows 64/67. Dongu kalan 4 maddeyle surer.
- 2026-10-01 tur 10: btrfs okuma + saf LZO/zstd (ADR 0069). Linux 66/68, Windows 65/68.
- 2026-10-01 tur 11: F2FS okuma (ADR 0070). Linux 67/69, Windows 66/69. Asama 3 bitti.
- 2026-10-01 tur 12: APFS salt okuma (ADR 0071). Linux 68/70, Windows 67/70.
- 2026-10-01 tur 13: ReFS yerel bicimlendirme (ADR 0072). Linux 69/71, Windows 68/71.
  **Liste bitti; dongu durduruldu.**
