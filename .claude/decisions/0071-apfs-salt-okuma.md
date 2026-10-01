# 0071 — APFS salt okuma (saf Python)

Tarih: 2026-10-01
Durum: **uygulandi** — macOS'un urettigi bir kapsayiciyla, bagimsiz okuyucu
libfsapfs'e karsi dogrulandi. **Kucuk test verisiyle sinirli** (asagida).
Ilgili: [0053](0053-sifreli-ve-kapsayici-birim-tanima.md) (APFS taninmasi),
[0060](0060-hfsplus-okuma.md) (decmpfs).

## Karar

`core/apfs.py::ApfsFS` + `filesystem.ApfsAccess` (salt okunur):
* NXSB (Fletcher-64 dogrulamali), checkpoint tanimlayici alanindan en yeni
  gecerli NXSB; kapsayici omap'i ile birim ustbloklari; birim omap'i ile
  dosya sistemi agacinin sanal dugumleri (muhurlu birimde fiziksel).
* Genel B-agaci gezintisi (sabit/degisken boy anahtar-deger, kok dugumde
  btree_info payi).
* Kayitlar: inode (xfield'dan dstream boyu), dizin girisi (karmali ve
  karmasiz anahtar), dosya kapsami (seyrek = 0), xattr (gomulu / akis).
  Sembolik bag `com.apple.fs.symlink`; decmpfs zlib (tur 1/3/4).
* Tek birimli kapsayicida kok = birimin koku; cok birimlide kokte birimler
  klasor. Buyuk/kucuk harf duyarsiz birimde ad aramasi duyarsiz.
* **Sifreli birim** (APFS_FS_UNENCRYPTED yok) listelenir ama icerigi acik
  hatayla reddedilir. LZVN/LZFSE sikistirmasi acik hata.

## Test verisi ve dogrulama

* Ana makinede APFS'e yazan arac yok (apfs-dkms cekirdek modulu root ister).
  macOS'ta olusturulmus kucuk goruntuler log2timeline/dfvfs test verisinden
  alindi (Apache 2.0; `tests/fixtures/KAYNAKLAR.md`, SHA-256'lariyla).
* Bagimsiz dogrulayici: libfsapfs (Debian `python3-fsapfs` + `libfsapfs1t64`,
  kullanici alanina acildi, sistem Python 3.14 ile). `fsapfsinfo -B` bu
  surumde bos cikti veriyor, `fsapfsmount` FUSE'suz derlenmis — bu yuzden
  pyfsapfs kullanildi.
* Sonuc: 10 girisin kip, boyut, SHA-1 ve bag hedefi libfsapfs ile birebir;
  xattr ve kaynak catali okunuyor; sifreli DMG (GPT icinde) dogru ret;
  apfsprogs mkapfs birimi (baska uygulama, apfsck temiz) aciliyor.
* t70.

## Sinanmayan (acikca)
* Cok duzeyli B-agaclari (test birimi kucuk), cok birimli kapsayici,
  muhurlu birim, decmpfs zlib (birimde sikistirilmis dosya yok), anlik
  goruntuler (okunmaz). Daha buyuk macOS uretimi birim bulununca
  genisletilmeli.
