# MBR Bicim Notlari (uygulama referansi)

Uygulama: [`src/diskultimate/core/mbr.py`](../../src/diskultimate/core/mbr.py)

## Onyukleme sektoru (LBA 0, 512 bayt)

| Ofset | Boyut | Alan |
|---|---|---|
| 0x000 | 440 | Onyukleme kodu (korunur, uzerine yazilmaz) |
| 0x1B8 | 4 | Disk imzasi (rastgele uretilir) |
| 0x1BC | 2 | Ayrilmis |
| 0x1BE | 16x4 | Bolum girisleri |
| 0x1FE | 2 | `0x55 0xAA` imzasi |

## Bolum girisi (16 bayt)

| Ofset | Boyut | Alan |
|---|---|---|
| 0x00 | 1 | Durum: `0x80` onyuklenebilir, `0x00` degil |
| 0x01 | 3 | Baslangic CHS |
| 0x04 | 1 | Bolum turu (`MBR_TYPES`) |
| 0x05 | 3 | Bitis CHS |
| 0x08 | 4 | Baslangic LBA (little-endian) |
| 0x0C | 4 | Sektor sayisi |

**CHS:** 255 kafa / 63 sektor geometrisi kullanilir. Sinir asilirsa `FE FF FF`
yazilir — modern isletim sistemleri LBA alanlarini okur.

**Sinir:** LBA ve sektor sayisi 32 bit oldugundan 512 baytlik sektorde azami
~2 TiB. Asilirsa uygulama hata verip GPT onerir.

## Genisletilmis bolum ve EBR zinciri
- Kapsayici bolum turu `0x05` (CHS) veya `0x0F` (LBA). Uygulama `0x0F` kullanir.
- Her mantiksal bolumden once bir EBR sektoru bulunur (1 MiB hizalama payi icinde).
- EBR'de yalnizca ilk iki giris anlamlidir:
  - **Giris 0:** mantiksal bolum — LBA alani **kendi EBR'sine gore** goreli.
  - **Giris 1:** zincirdeki bir sonraki EBR — LBA alani **genisletilmis bolumun
    baslangicina gore** goreli; tur `0x0F`. Zincir sonunda sifirdir.
- Mantiksal bolumler 5'ten baslayarak numaralandirilir (`fdisk` ile ayni).

## Dogrulama
`fdisk -l disk.img` ciktisi birincil ve mantiksal bolumleri eksiksiz listelemelidir.
`tests/run_all.py::t02_mbr` zinciri yazip geri okuyarak dogrular.
