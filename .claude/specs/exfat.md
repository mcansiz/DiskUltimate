# exFAT Bicim Notlari (uygulama referansi)

Uygulama: [`src/diskultimate/core/exfat.py`](../../src/diskultimate/core/exfat.py)

## Yerlesim

| Sektor | Icerik |
|---|---|
| 0 | Ana onyukleme sektoru (VBR) |
| 1–8 | Genisletilmis onyukleme sektorleri (her biri `0xAA550000` ile biter) |
| 9 | OEM parametreleri |
| 10 | Ayrilmis |
| 11 | Onyukleme saglamasi (4 baytlik deger sektor boyunca tekrarlanir) |
| 12–23 | Yedek onyukleme bolgesi (0–11'in aynisi) |
| FatOffset… | FAT bolgesi |
| ClusterHeapOffset… | Veri bolgesi: bitmap → upcase → kok dizin → dosyalar |

## Onyukleme sektoru alanlari

| Ofset | Boyut | Alan |
|---|---|---|
| 3 | 8 | `EXFAT   ` |
| 64 | 8 | PartitionOffset (bolumun diskteki LBA'si) |
| 72 | 8 | VolumeLength (sektor) |
| 80 | 4 | FatOffset (sektor) |
| 84 | 4 | FatLength (sektor) |
| 88 | 4 | ClusterHeapOffset (sektor) |
| 92 | 4 | ClusterCount |
| 96 | 4 | FirstClusterOfRootDirectory |
| 100 | 4 | VolumeSerialNumber |
| 104 | 2 | FileSystemRevision (`0x0100`) |
| 106 | 2 | VolumeFlags |
| 108 | 1 | BytesPerSectorShift |
| 109 | 1 | SectorsPerClusterShift |
| 110 | 1 | NumberOfFats |
| 112 | 1 | PercentInUse |
| 510 | 2 | `0xAA55` |

## Saglama algoritmalari

**Onyukleme saglamasi** — 0–10. sektorler uzerinde; 106, 107 ve 112. baytlar atlanir:
```
toplam = ((toplam << 31) | (toplam >> 1)) + bayt        (32 bit)
```

**Upcase tablosu saglamasi** — ayni dongu, atlama yok.

**Giris kumesi saglamasi** (16 bit) — 2. ve 3. baytlar (SetChecksum) atlanir:
```
toplam = ((toplam << 15) | (toplam >> 1)) + bayt        (16 bit)
```

**Ad karmasi** (NameHash, 16 bit) — buyuk harfe cevrilmis adin UTF-16LE baytlari
uzerinde, giris kumesiyle ayni dongu.

## Buyuk harf (upcase) tablosu — onemli tuzak

Tablo **serbest degildir**. `exfatprogs` ve Windows, spec'te tanimli **onerilen**
tabloyu bekler; kendi uretilen bir tablo `fsck.exfat` tarafindan
`corrupted upcase table` olarak reddedilir.

Bu yuzden standart tablonun sikistirilmis hali (5836 bayt, saglama `0xE619D30D`)
kaynak icinde zlib+base64 olarak saklanir. `tests.run_all::t09` saglamayi dogrular.

Sikistirma bicimi: 16 bitlik degerler dizisi; `0xFFFF` kacis degerini izleyen sayi,
o kadar karakterin **kendisiyle** eslendigini belirtir.

## Dizin girisleri (32 bayt)

| Tur | Anlam |
|---|---|
| `0x83` | Birim etiketi (`[1]` karakter sayisi, `[2..23]` UTF-16) |
| `0x81` | Ayirma bitmap'i (`[20]` ilk kume, `[24]` uzunluk) |
| `0x82` | Upcase tablosu (`[4]` saglama, `[20]` ilk kume, `[24]` uzunluk) |
| `0x85` | Dosya (`[1]` ikincil giris sayisi, `[2]` kume saglamasi, `[4]` oznitelik, `[8/12/16]` zaman damgalari) |
| `0xC0` | Akis uzantisi (`[1]` bayraklar, `[3]` ad uzunlugu, `[4]` ad karmasi, `[20]` ilk kume, `[24]` uzunluk) |
| `0xC1` | Dosya adi (15 UTF-16 karakter) |

`InUse` biti girisin ust bitidir (`0x80`): silinmis girislerde temizlenir — silinmis
dosya taramasinin dayanagi budur.

Akis uzantisindaki `NoFatChain` bayragi (`0x02`) kumelerin ardisik oldugunu belirtir;
bu durumda FAT okunmaz.

## Dogrulama

```bash
fsck.exfat -n <bolum.img>     # "clean" ciktisi beklenir
```
`tests.run_all::t09` bunu otomatik yapar.
