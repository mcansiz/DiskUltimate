# 0067 — XFS v5 bicimlendirme (saf Python)

Tarih: 2026-09-29
Durum: **uygulandi** — mkfs.xfs 6.18 ile bayt bayt karsilastirildi,
xfs_repair temiz. Cekirdek baglamasi (ana makinede root yok) ile
**sinanmadi**.
Ilgili: [0066](0066-xfs-okuma.md)

## Karar

`core/xfsformat.py::format_xfs`; `FS_KINDS` "xfs" (en az 300 MB — mkfs'in
alt siniri; MBR 0x83, GPT Linux veri).

**Ozellik kumesi bilincli olarak sade ve sabit:** crc (v5), ftype, finobt,
bigtime, inobtcount. rmapbt, reflink, seyrek inode, nrext64, exchange,
parent ve metadir kapali. Gerekce: her ozellik ayri meta yapi (rmap/refcount
B-agaclari, seyrek obek maskeleri) demektir; bu kume Linux 5.10+ cekirdekte
tam desteklidir (bigtime 5.10). mkfs varsayilani (rmapbt+reflink+sparse+
nrext64) ile ayni degil — bu birim o ozellikleri kullanmaz.

Yerlesim ve geometri mkfs.xfs 6.18 ciktisindan olculdu:
* AG basi: ustblok/AGF/AGI/AGFL sektorleri, blok 1-4 bnobt/cntbt/inobt/
  finobt kokleri, 5-8 AGFL bloklari (AGFL sira 1..4). AG0 blok 12-19 ilk
  inode obegi (96 kok dizin, 97 rbm, 98 rsum, 61 bos inode — hepsi CRC'li,
  rastgele nesil). Gunluk (agcount/2). AG'de blok 5'ten; AGFL gunlukten sonra.
* Geometri: 4 x (2^28-1) bloga kadar 4 esit AG (ceil), ustunde azami AG
  boyu ve ceil AG sayisi; 16 MB'tan kucuk son AG atilir. Gunluk
  blok/2048, [64 MB, 2 GB-10 MB]. imaxpct 25, >= 1 TiB 5, >= 50 TiB 1.
* fdblocks = serbest + AGFL bloklari. **Ikincil ustbloklar bilincli
  "bayat":** rbm/rsum NULL, inprogress 1, sayaclar 0; kok inode numarasi
  yalnizca son AG ve (AG-1)/2'deki ikincilde (4/5/8 AG ile olculdu).
* Gunluk: tek sektorluk unmount kaydi (dongu 1), geri kalan gunluk sifir.
* Tum CRC'ler standart CRC-32C, kucuk uclu (ustblokta dogrulandi).

## Dogrulama
* Ayni UUID ve etiketle mkfs ciktisi: 300M, 320M, 333 333 333 B (tuhaf son
  AG), 7,7 GB — **inode obegi (zaman/nesil/CRC) disinda her blok ayni**.
* xfs_repair -n: hepsi temiz.
* Geometri tablosu 300 MB - 5 TB mkfs -N ile ayni (ilk surum 4 AG'ye
  sigmayan boyutlarda AG'leri esit boluyordu; mkfs azami boyu tutuyor —
  t66 yakaladi, duzeltildi).
* Okuyucumuz bicimlendirilen birimi aciyor. t66. Linux 64/66, Windows 63/66.
