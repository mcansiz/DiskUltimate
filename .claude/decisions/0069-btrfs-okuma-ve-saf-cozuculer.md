# 0069 — btrfs salt okuma; saf LZO1X ve zstd cozuculeri

Tarih: 2026-10-01
Durum: **uygulandi** — mkfs.btrfs 6.17 birimleriyle (ana makine, goruntu
dosyasi) ve zstd CLI ciktisiyla dogrulandi; Windows'ta fixture ile kosar.
Ilgili: `.claude/docs/dosya-sistemi-genisletme.md` Asama 3 ("LZO/zstd icin
durust hata -> saf cozucu").

## Karar

`core/btrfs.py::BtrfsFS` + `filesystem.BtrfsAccess` (salt okunur):
* Ustblok, sys_chunk_array + chunk agaci ile mantiksal -> fiziksel esleme;
  SINGLE/DUP; cok aygitta bu aygitta kopyasi olan (RAID1/1C3/1C4) kapsamlar.
  Seritli (RAID0/10/5/6), extent-tree-v2, raid-stripe-tree ve sifreli veri
  acik hatayla reddedilir.
* Agac gezintisi yigitla (yapraklar bagli degil); kok agacindan FS agaci
  (5) ve alt hacimler; DIR_INDEX ile listeleme; alt hacim girisleri (konum
  anahtari ROOT_ITEM) baska agaca gecer.
* EXTENT_DATA: satir ici, normal, onceden ayrilmis (sifir), delik/NO_HOLES.
  Sikistirma 1 zlib (stdlib), 2 LZO, 3 zstd.

`core/compress.py` — **calisma zamani bagimliligi yok** kurali geregi saf
Python:
* **LZO1X** (Linux `lzo1x_decompress_safe` durum makinesi) + btrfs cercevesi
  (toplam boy, sektor sinirini gecmeyen bolum basliklari).
* **zstd** (RFC 8878): cerceve, ham/RLE/sikistirilmis blok, Huffman (FSE
  sikistirilmis ve dogrudan agirliklar, tek/dort akis, agacsiz tekrar), FSE
  (onceden tanimli / RLE / sikistirilmis / tekrar), dizi cozme, tekrar
  ofsetleri. Sozluk yok (btrfs kullanmaz). Python 3.14+'ta varsa yerlesik
  `compression.zstd` kullanilir.

## Dogrulama
* zstd: 9 veri turu x 6 seviye (1, 3, 9, 19, ultra 22, fast 5) zstd CLI
  ciktisi birebir; sikistirilmis blok, FSE ve tekrar tablolari, Huffman
  akislari sayacla dogrulandi.
* btrfs: 1507 dosyalik agac (5000+ kapsamli sikistirilmis veri, 1,5 MB
  rastgele, seyrek dosya, alt hacim, bag, Turkce ad) sikistirmasiz / zlib /
  LZO / zstd — hepsi birebir; 4 KiB dugum (derin agac) + DUP, karisik blok
  gruplari, 64 KiB dugum + zstd:15 varyantlari birebir. Butun birimler
  `btrfs check` temiz.
* Hiz: zstd'li agac 1,1 sn; LZO 5,5 sn (bayt bayt durum makinesi).
* t68 (uc sikistirmanin fixture'lari her platformda, ~600 KB).
