# 0063 — UDF salt okuma (saf Python)

Tarih: 2026-09-29
Durum: **uygulandi** — Windows'un yazdigi birim ve genisoimage/mkudffs
birimleriyle dogrulandi. macOS'ta sinanmadi.
Ilgili: [0055](0055-iso9660-ve-tablosuz-disk.md) (ISO koprusu),
`.claude/docs/dosya-sistemi-genisletme.md` Asama 2.

## Karar

`core/udf.py::UdfFS` (ECMA-167 + OSTA UDF 1.02-2.60):

* Blok boyu capadan (AVDP, blok 256) bulunur: 2048/512/1024/4096 denenir,
  etiket kimligi 2 **ve** etiket konumu 256 olmali.
* VDS: birincil, bolum, mantiksal birim, sonlandirici. Etiket: LVD
  tanimlayicisi (Windows'un gosterdigi), yoksa PVD.
* Bolum haritalari: tip 1; "*UDF Sparable Partition" (paket yeniden
  eslemesiyle); "*UDF Metadata Partition" (2.50+, metadata dosyasinin
  kapsamlariyla). VAT (bir kez yazilir CD-R) acik hatayla reddedilir.
* FE (261) ve EFE (266); kisa/uzun/genis/gomulu kapsamlar, sonraki kapsam
  zinciri (AED), yazilmamis/seyrek kapsam sifir okunur. Sembolik bag yol
  bilesenleri. FID dizinleri, gizli/silinmis/ust bayraklari, CS0 8/16 bit.
* Bos alan LVID'den.
* `filesystem.UdfAccess` (salt okunur). UDF + ISO koprusunde **UDF agaci
  tercih edilir** (uzun Unicode adlar); UDF okunamazsa (VAT) ISO agacina
  duser.

## Test verisi

* **Windows 10'un kendi UDF surucusu**: mkudffs `-m hd -b 512 -r 2.01
  --bootarea=mbr` birimi VHD olarak VirtualBox misafirine takildi; Windows
  (yonetici gerekmeden) 402 dosya yazdi, sildi, adlandirdi; SHA-1 listesi
  Windows'ta alindi. EFE + kisa kapsam + gomulu veri + 32 KB dizin.
  Ilk denemede Turkce adlar bozuk cikti: **PowerShell 5.1 BOM'suz .ps1'i
  ANSI okuyor** — Windows klasoru gercekten "KlasÃ¶r" adiyla olusturmustu;
  okuyucu dogruydu. Betik UTF-8 BOM'la yeniden kosturuldu.
* genisoimage `-udf`: UDF 1.02 + ISO koprusu (FE).
* mkudffs `-m cdrw`: yedekli bolum haritasi.
* `wrudf` goruntu dosyasinda calismiyor (CD paket yazma araci) — elendi.

## Sinanmayan

* Metadata bolumu (UDF 2.50/2.60): mkudffs sabit disk icin 2.01 ustunu
  uretmiyor, Windows'ta `format /fs:UDF /r:2.50` yonetici istiyor. Kod
  spesifikasyondan; UDF yazma maddesi bunu da uretip sinayacak.
* VAT (CD-R) okunmuyor.
