# 0057 — ext yazma: htree'ye dogru ekleme, extent agaci, dizin buyumesi

Tarih: 2026-09-29
Durum: **uygulandi** — Linux (e2fsck) ve Windows (VirtualBox win10; cikti
Linux'ta e2fsck ile) dogrulandi. macOS'ta kosulmadi.
Ilgili: [0017](0017-saf-python-ext-ve-ntfs.md), [0052](0052-ext-saf-python-boyutlandirma.md)

## Baglam — olculen hatalar

1. **htree bozulmasi (veri butunlugu):** yazici yeni girise yer ararken dizin
   bloklarini duz sirayla tariyordu. Indeksli (htree) dizinin 0. blogunda
   ".." kaydinin "bos" gorunen kuyrugu aslinda `dx_root` indeksidir; giris
   oraya yaziliyor, indeks eziliyordu. `mkfs.ext4` + `e2fsck -D` ile
   indekslenmis dizine tek dosya yazmak e2fsck'i "HTREE directory ...
   invalid / unordered hash table" dedirtti. Cekirdek birden fazla bloklu
   her dizini indeksledigi icin **gercek ext4 birimlerinde kalabalik bir
   klasore dosya kopyalamak dizini bozuyordu**.
2. Extent'li dizin dolunca yazma reddediliyordu; dolayli dizin 12 blokta
   (4K'da ~48 KB) duruyordu.
3. ext4'te yeni dosyalar dolayli blok duzeniyle yaziliyordu.
4. Extent'li dosya silinince agacin ic/yaprak dugumleri serbest
   birakilmiyordu (derinligi > 0 olan dosyada sizinti).
5. Baslatilmamis (`INODE_UNINIT`/`BLOCK_UNINIT`) gruplar hic kullanilmiyordu:
   1K bloklu 200 MB birimde 3000 dosyada "Bos inode kalmadi".
6. Disk dolunca yazma yarida kaliyor, ayrilan inode bitmap'te sahipsiz
   kaliyordu (e2fsck "inode bitmap differences").

## Karar

* `core/exthtree.py`: dizin karmasi (legacy, half-MD4, TEA ve imzasiz
  turevleri) e2fsprogs `dirhash.c` ile birebir. **Dogrulama:** `debugfs
  dx_hash` ile 420 karsilastirma (3 algoritma × sifir/rastgele tohum × 70
  ad: Turkce, emoji, 16/32 bayt sinirlari) — fark 0. Karma tohumu ustbloktan,
  imzasiz bayrak `s_flags`tan.
* `ExtWriter.dir_add`: indeksli dizinde karmanin yapragina ekler; yaprak
  doluysa girisler karmaya gore siralanip ikiye bolunur, ust dugume
  "devam biti" dogru (`hash2 + continued`) yeni giris eklenir, dx saglamasi
  (`dt_reserved` + sifir sayilan 4 bayt) guncellenir. Indeks dugumu doluysa
  (4K'da ~500 yaprak) dizin **guvenle dogrusala cevrilir**: dx bloklari
  gecerli bos dogrusal bloklara (saglama kuyruguyla) yeniden yazilir,
  `INDEX_FL` kalkar. Indeksiz cok bloklu dizin gecerli bir ext4 durumudur.
  Seviye ekleyen indeks bolme bilerek yapilmadi.
* Sifreli (fscrypt) dizine yazma reddedilir.
* `_dir_append_block`: extent'li dizinde agac yeniden kurulur
  (`extmove.build_extent_tree`, adaptor ile ayni kod); dolayli dizinde
  dogrudan → tek → cift kat.
* ext4'te yeni dosyalar extent kullanir (aralik ≤ 32768 blok, gerekirse
  derin agac); silmede dugumler de birakilir.
* Baslatilmamis gruplar ilk kullanimda baslatilir: inode tablosu
  `ITABLE_ZEROED` degilse sifirlanir, blok bitmap'i cekirdek kuraliyla
  (flex_bg ile baska gruplarin metaverisi dahil) gerceklenir, sayaclar
  bitmap'ten duzeltilir. `itable_unused` 0 yerine dogru degere iner.
* Yer on denetimi: veri + agac + dizin buyumesi icin gereken blok ust
  siniri islem **baslamadan** denetlenir; beklenmeyen hatada ayrilan inode
  geri verilir.

## Dogrulama

* 3000 ekleme × {half_md4, tea, legacy, imzasiz half_md4, imzasiz tea,
  csum'suz} htree dizini: indeksli kaldi, e2fsck temiz, tum dosyalar okundu.
  1K blokta kok indeks doldu → dogrusala cevrildi, e2fsck temiz.
* 2500 girisli dogrusal dizin ext2 (dolayli, cift kata) ve ext4 (extent).
* Parcali 8 MB dosya → 2 seviyeli extent agaci; silme sonrasi sizinti yok.
* Disk doldurma: 782 dosyada temiz hata ("10 blok gerekli, 8 bos"),
  e2fsck temiz.
* Windows: `tests/fixtures/ext4_htree.img.gz` uzerine 900 + 600 dosya,
  3 MB dosya, 300 silme — Linux'ta e2fsck temiz, htree korunmus, 600 giris.
  (Yol boyunca bir yanlis alarm: `debugfs ls -l` blogun ilk kaydi silinince
  inode'u 0 yapilan kaydi da listeler; cekirdek de ayni sekilde siler.)
* `run_all` t55 (her platformda: her girisin kendi karmasinin yapraginda
  durdugu denetlenir — `ExtWriter.htree_leaf_for`), `ext_write_check` 4/4,
  `ext_resize_check --quick` 11/11. Linux 53/55, Windows 52/55 (3 harici
  arac karsilastirmasi atlandi).
