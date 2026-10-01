# 0055 — ISO 9660 salt okuma ve tablosuz disk (tum disk tek dosya sistemi)

Tarih: 2026-09-29
Durum: **uygulandi** — Linux ve Windows (VirtualBox win10) dogrulandi,
macOS'ta kosulmadi.
Ilgili: [0053](0053-sifreli-ve-kapsayici-birim-tanima.md), [0054](0054-takas-ve-kayip-bolum-imzalari.md)

## 1. ISO 9660 (`core/iso9660.py`, `filesystem.IsoAccess`)

Saf Python, salt okuma. Kaynak ECMA-119 + Joliet + SUSP/RRIP (acik belgeler).

* Ad onceligi: **Rock Ridge** (POSIX ad, sembolik bag, `CE` devami) >
  **Joliet** (UCS-2) > duz 8.3 (`;1` surum eki ve sondaki nokta atilir).
* 4 GiB ustu dosyalar birden cok dizin kaydiyla yazilir (bayrak 0x80);
  ardisik ayni adli kayitlar birlestirilir. Olculdu: 4,1 GB'lik dosya iki
  parca, SHA-1 kaynakla ayni (parca sinirinda bilerek veri vardi).
* Disa aktarma parca parca (4 MB) yazar; dosya bellege alinmaz.
* UDF/ISO koprusu (UDF olarak taninir): UDF okuyucusu gelene kadar ISO agaci
  okunur.
* Test verisi `tests/fixtures/fx_{rr,j,plain}.iso.gz` (~8 KB): ISO ureticisi
  her platformda yok. Onemli ayrinti: `xorriso -as mkisofs` `-R` verilmese de
  Rock Ridge yazar; Joliet-yalniz ve duz ornekler `genisoimage` ile uretildi.

## 2. Tablosuz disk (`ptable.WholeDiskTable`, `session.read_partition_table`)

Duz `.iso`, tablosuz ("super disket") USB bellek, `mkfs` ile uretilmis
`ext4.img` gibi goruntuler eskiden "bolum tablosu yok, GPT olusturulsun
mu?" diye karsilaniyordu; icindeki dosya sistemi gorunmuyordu.

Karar: tablo yoksa ve diskin basinda taninan bir dosya sistemi varsa, tum
disk **sanal tek bolum** olarak gosterilir (sema `none`). Icerik okunur,
yazilir, bicimlendirilir, yedeklenir. Bolum ekleme/silme, boyutlandirma ve
MBR↔GPT donusumu nedeniyle reddedilir. "Yeni bolum" arayuzu, tablo kurmanin
dosya sistemini silecegini soyler (varsayilan "Hayir").

Secim kurali (`read_partition_table`, oturum + fiziksel disk yoklamasi):
GPT > **gecerli** MBR (durum bayti 0x00/0x80, girisler disk icinde ve
cakismasiz — Linux msdos ayristiricisi ile ayni) > 0x55AA'li ilk sektor bir
onyukleme sektoru ise (FAT/exFAT/NTFS/ReFS/BitLocker) tablosuz disk > bos MBR
> 0x55AA yoksa taninan dosya sistemi → tablosuz disk.

Eski hata: 0x55AA goren her sektor MBR sayiliyordu; FAT onyukleme
sektorunun **kodu bolum girisi diye okunuyordu** (oturumda, fiziksel disk
yoklamasinda ve geri yukleme planinda).

Ince nokta: bos MBR'nin arkasinda eski bir ext4 ustblogu kalabilir (MBR
kurmak 1024. bayta dokunmaz). O disk "tablosuz ext4" degil, bos MBR'dir —
test t53 bunu denetler.

## Yolda bulunan hatalar

* Tablosuz diskte "bolumu sil" reddediliyordu ama `delete_partition`
  bolumun basini/sonunu **tabloya danismadan once** siliyordu; ret geldiginde
  veri gitmisti. Ret artik silmeden once.
* Yedek onizleme "bolum listesi bossa bolum yedegi" varsayimina dayaniyordu;
  olcut sema oldu (`none` = bolum yedegi).

## Dogrulama

t52 (ISO: RR/Joliet/duz, etiket, Unicode ad, sembolik bag, disa aktarma),
t53 (duz ISO, super disket FAT yazma + yasak islemler, ext4.img, bos MBR +
eski ext4). Linux 51/53 (+2 Windows'a ozgu), Windows 51/53 (+2 Linux aracli
karsilastirma atlandi), ui_smoke, diag 13/13, i18n, platform temiz.
