# FAT Bicim Notlari (uygulama referansi)

Uygulama: [`src/diskultimate/core/fat.py`](../../src/diskultimate/core/fat.py)

## Tur secimi
Kume sayisina gore belirlenir (hesaplama sonrasi):

| Kume sayisi | Tur |
|---|---|
| < 4085 | FAT12 |
| 4085 – 65524 | FAT16 |
| > 65524 | FAT32 |

Bicimlendirme sirasinda kume boyutu, sayi gecerli araliga girene kadar ikiye
katlanir veya yariya indirilir (`FatFS.format` icindeki dongu).

## BPB — ortak alanlar

| Ofset | Boyut | Alan |
|---|---|---|
| 0 | 3 | Atlama komutu (`EB 58 90` FAT32, `EB 3C 90` FAT12/16) |
| 3 | 8 | OEM adi |
| 11 | 2 | Sektor basina bayt |
| 13 | 1 | Kume basina sektor |
| 14 | 2 | Ayrilmis sektor sayisi (FAT32: 32, digerleri: 1) |
| 16 | 1 | FAT kopyasi sayisi (2) |
| 17 | 2 | Kok dizin girisi sayisi (FAT32: 0, digerleri: 512) |
| 19 | 2 | Toplam sektor (16 bit; tasarsa 0) |
| 21 | 1 | Ortam tanimlayici (`0xF8`) |
| 22 | 2 | FAT boyutu (16 bit; FAT32'de 0) |
| 32 | 4 | Toplam sektor (32 bit) |

## FAT32'ye ozel

| Ofset | Alan |
|---|---|
| 36 | FAT boyutu (32 bit) |
| 44 | Kok dizin kumesi (2) |
| 48 | FSInfo sektoru (1) |
| **50** | **Yedek onyukleme sektoru (6)** |
| 67 | Birim seri numarasi |
| 71 | Birim etiketi (11 bayt) |
| 82 | `FAT32   ` |

> **Dikkat:** 50. ofset bir kez 52 olarak yazildi ve `fsck.vfat` "yedek onyukleme
> sektoru yok" uyarisi verdi. Ofset degisikliklerinde `fsck` testi zorunludur.

FAT12/16'da: 39 seri no · 43 etiket · 54 tur metni.

## FAT girisleri
- FAT12: 12 bit paketli — `off = c + c//2`, tek kumede yuksek 12 bit.
- FAT16: 2 bayt · FAT32: 4 bayt (ust 4 bit ayrilmis, korunur).
- `FAT[0]` ortam tanimlayici, `FAT[1]` zincir sonu; veri kumeleri 2'den baslar.
- Zincir sonu: FAT12 `0xFF8`, FAT16 `0xFFF8`, FAT32 `0x0FFFFFF8` ve uzeri.

## Dizin girisi (32 bayt)
`0-10` 8.3 ad · `11` oznitelik · `12` NT kucuk harf bayraklari · `14-15` olusturma
saati · `16-17` olusturma tarihi · `20-21` kume yuksek · `22-23` yazma saati ·
`24-25` yazma tarihi · `26-27` kume dusuk · `28-31` boyut.

Ilk bayt `0x00` dizin sonu, `0xE5` silinmis giris.

## Uzun ad (LFN)
Oznitelik `0x0F`, her giris 13 UTF-16 karakter tasir, **ters sirada** yazilir
(sonuncu girisin sira numarasinda `0x40` biti bulunur). `13.` bayt, ilgili 8.3
adin saglama toplamidir:

```
s = 0
her bayt icin: s = (((s & 1) << 7) + (s >> 1) + bayt) & 0xFF
```

Saglama tutmuyorsa LFN yok sayilip 8.3 ad kullanilir — bozuk dizinlerde guvenli davranis.

## Bicimlendirme adimlari
1. Yerlesimi hesapla (`_compute_layout`: ayrilmis + FAT boyutu + kok dizin).
2. Onyukleme sektorunu yaz; FAT32'de FSInfo (LBA 1) ve yedekleri (LBA 6, 7).
3. FAT kopyalarini ilk girisleriyle birlikte yaz (FAT32'de kok kumesi = zincir sonu).
4. Kok dizin alanini sifirla.
5. Istege bagli birim etiketi girisini kok dizine ekle.
