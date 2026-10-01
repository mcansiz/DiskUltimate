# 0070 — F2FS salt okuma (saf Python)

Tarih: 2026-10-01
Durum: **uygulandi** — mkfs.f2fs + sload.f2fs 1.16 birimleriyle dogrulandi
(ana makine, goruntu dosyasi). F2FS sikistirmasi **sinanmadi**.
Ilgili: [0069](0069-btrfs-okuma-ve-saf-cozuculer.md) (LZO/zstd cozuculeri)

## Karar

`core/f2fs.py::F2fsFS` + `filesystem.F2fsAccess` (salt okunur):
* Ustblok (blok 0/1, 1024. bayt), gecerli checkpoint paketi (baslik ve son
  kopyasi ayni surum, buyuk olan).
* NAT: once sicak veri ozetindeki **NAT gunlugu** (normal ve sikistirilmis
  ozet duzeni), sonra NAT surum bitmap'ine gore iki kopyadan biri; bitmap
  yeri CP_LARGE_NAT_BITMAP ve cp_payload'a gore (cekirdegin __bitmap_ptr'i).
* Inode: extra_attr (i_extra_isize), satir ici xattr (50 ya da esnek boy),
  satir ici veri ve satir ici dizin, 923 + 2 dogrudan + 2 dolayli + 1 cift
  dolayli dugum; ardisik bloklar tek okumada.
* Dizin bloklari (214 giris) karma hesaplanmadan taranir.
* Sifreli dosya acik hatayla reddedilir.
* Sikistirma (COMPRESS_ADDR kumeleri): LZ4 (yeni saf blok cozucu), LZO ve
  zstd (ADR 0069); LZO-RLE desteklenmez (acik hata).

## Dogrulama
* 5036 dosyalik agac (5000 girdilik dizin, 40 MB seyrek dosya — dolayli
  dugumler, 3 MB rastgele, satir ici, uzun bag): birebir, 0,9 sn.
  extra_attr+inode_checksum+sb_checksum ve flexible_inline_xattr+
  inode_crtime varyantlari birebir. fsck.f2fs temiz.
* NAT gunlugu ve ikinci NAT kopyasi sload ciktisinda olusmaz (yalnizca
  cekirdek uretir): t69'da birim elle degistirilerek (giris gunluge, NAT
  blogu ikinci kopyaya, bitmap biti) okuyucunun bu yollari izledigi
  sinandi — oz-tutarlilik denetimi, cekirdek ciktisiyla dogrulama degil.
* LZ4 blok cozucusu lz4 CLI'nin (kullanici alanina acildi) 92 blogunda
  birebir.
* **Sinanmayan:** F2FS sikistirilmis kumeler — sload.f2fs bu derlemede
  LZ4/LZO'suz ("compression algorithm is not supported"); kume baslik
  yerlesimi ve ek alan ofsetleri (algoritma 32, kume logu 33) kaynak koddan.
