# GPT Bicim Notlari (uygulama referansi)

Uygulama: [`src/diskultimate/core/gpt.py`](../../src/diskultimate/core/gpt.py)

## Disk yerlesimi (512 bayt sektor)

| LBA | Icerik |
|---|---|
| 0 | Koruyucu MBR — tek giris, tur `0xEE`, baslangic 1, boyut `min(disk-1, 0xFFFFFFFF)` |
| 1 | Birincil GPT basligi |
| 2 – 33 | Giris dizisi (128 giris x 128 bayt = 16 KiB = 32 sektor) |
| 34 – (n-34) | Kullanilabilir alan |
| (n-33) – (n-2) | Yedek giris dizisi |
| n-1 | Yedek GPT basligi |

## Baslik (92 bayt)

| Ofset | Boyut | Alan |
|---|---|---|
| 0 | 8 | `EFI PART` |
| 8 | 4 | Surum `0x00010000` |
| 12 | 4 | Baslik boyutu (92) |
| 16 | 4 | Baslik CRC32 (**hesaplanirken bu alan sifirlanir**) |
| 20 | 4 | Ayrilmis |
| 24 | 8 | Bu basligin LBA'si |
| 32 | 8 | Diger (yedek) basligin LBA'si |
| 40 | 8 | Ilk kullanilabilir LBA |
| 48 | 8 | Son kullanilabilir LBA |
| 56 | 16 | Disk GUID |
| 72 | 8 | Giris dizisinin LBA'si |
| 80 | 4 | Giris sayisi (128) |
| 84 | 4 | Giris boyutu (128) |
| 88 | 4 | Giris dizisi CRC32 |

## Bolum girisi (128 bayt)

| Ofset | Boyut | Alan |
|---|---|---|
| 0 | 16 | Tur GUID (`GPT_TYPES`) — sifir ise giris kullanilmiyor |
| 16 | 16 | Bolum benzersiz GUID |
| 32 | 8 | Ilk LBA |
| 40 | 8 | Son LBA (**dahil**) |
| 48 | 8 | Oznitelik bayraklari |
| 56 | 72 | Bolum adi (UTF-16LE, 36 karaktere kadar) |

## GUID kodlamasi
Ilk uc alan **little-endian**, son iki alan big-endian yazilir. Python'da
`uuid.UUID(...).bytes_le` ve `uuid.UUID(bytes_le=...)` bu donusumu yapar.

## Oznitelik bitleri (uygulamada tanimli)
`0` sistem icin gerekli · `1` blok G/C yok · `2` eski BIOS onyuklenebilir
(arayuzdeki "onyukleme bayragi") · `60` salt okunur · `62` gizli · `63` otomatik baglama yok

## Yazma sirasi
Koruyucu MBR → birincil baslik → birincil girisler → yedek girisler → yedek baslik.
Goruntu yeniden boyutlandirildiginda yedek yapinin yeni sona tasinmasi icin tablo
yeniden yazilir (`session.resize_image`).

## Dogrulama
`fdisk -l` CRC uyarisi vermemelidir. `t03_gpt` her iki CRC'yi, koruyucu MBR turunu
ve yedek basligin konumunu denetler.
