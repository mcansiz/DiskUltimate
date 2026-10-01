# 0064 — UDF 2.01 bicimlendirme (saf Python)

Tarih: 2026-09-29
Durum: **uygulandi** — Windows 10'un kendi UDF surucusuyle ve udftools
(`udfinfo`) ile dogrulandi. macOS'ta sinanmadi.
Ilgili: [0063](0063-udf-okuma.md)

## Neden UDF

UDF, uc isletim sisteminin de **kutudan cikar cikmaz okuyup yazdigi** tek
dosya sistemi (Windows Vista+, macOS, Linux `udf`); 4 GB'tan buyuk dosya ve
Unicode ad destekler. exFAT'in lisansli/eski sistemlerde eksik oldugu
durumlarda tasinabilir disk icin ortak paydadir.

## Karar

`core/udfformat.py::format_udf`; `formatter.FS_KINDS` "udf" (MBR 0x07, GPT
Microsoft temel veri — Windows'un UDF bolumu icin bekledigi turler).

Yerlesim `mkudffs -m hd -r 2.01` (udftools 2.3) ciktisi 512/1024/2048/4096
blok ve 16M-1G boyutlarda olculerek cikarildi; blok cinsinden sabittir:
VRS 32 KiB (BEA01/NSR03/TEA01), ana VDS 96 (PVD, LVD, PD, USD, IUVD, TD),
LVID 128 (8 KiB), capa 256 / N-257 / N-1, yedek VDS N-160, bolum 257'den
N-520 blok. Bolumde alan bitmap'i (etiket 264, CRC yalnizca 8 bayt), FSD,
sistem akis dizini ve kok dizin EFE'leri (FID girisin icinde gomulu).
USD, mkudffs'in "sil" bolgelerini listeler.

* Blok boyu aygitin sektor boyu (Windows UDF'yi sektor boyuyla ayni blokla
  bekler).
* Etiket: CS0 8 bit (Latin-1'e sigarsa) ya da 16 bit; LVD/FSD 126/63,
  PVD 30/15 karakter.
* Kimlikler "*DiskUltimate"; UUID rastgele (VSID'in ilk 16 karakteri).

## Dogrulama

* udfinfo: 16M/512'de kullanilan 11, bos 32237 blok — mkudffs ile ayni;
  64M/4096, 1G, blok kati olmayan boy; bütünlük "closed", 2.01.
* 7z: 512 bloklu birimleri aciyor; 4096 blokta mkudffs'inkini de acamiyor
  (7z siniri).
* **Windows 10:** MBR ve GPT diskte bizim bicimlendirdigimiz birimler
  UDF olarak baglandi, Healthy; Windows 300 dosya yazdi, 30'unu sildi,
  birini adlandirdi — Healthy kaldi; geri okunan 301 dosya SHA-1 birebir,
  udfinfo `integrity=closed`, dosya/dizin sayilari tutarli.
* t63: etiket saglama/CRC/konum her tanimlayicida, yerlesim sayilari,
  okuyucu, udfinfo (varsa), DiskSession MBR/GPT.
