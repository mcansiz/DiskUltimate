# 0066 — XFS salt okuma (saf Python)

Tarih: 2026-09-29
Durum: **uygulandi** — mkfs.xfs 6.18 birimleriyle dogrulandi (ana makine,
goruntu dosyasi). Windows'ta fixture ile kosar.
Ilgili: `.claude/docs/dosya-sistemi-genisletme.md` Asama 3.

## Karar

`core/xfs.py::XfsFS` + `filesystem.XfsAccess` (salt okunur):

* v4 ve v5 ustblok; FTYPE (v5 incompat / v4 features2), BIGTIME ve NREXT64
  inode bayraklari; bilinmeyen incompat ozelligi acik hatayla reddedilir.
* Veri catali: yerel, kapsam listesi, B-agaci (inode ici bmdr koku,
  BMA3/BMAP bloklari). Yazilmamis kapsam ve delikler sifir okunur.
* Dizinler: kisa bicim (4/8 bayt inode), veri bloklari XDB3/XDD3
  (v4 XD2B/XD2D), blok dizinde yaprak kuyrugu atlanir; dizin blogu
  `dirblklog` ile birden cok fs blogu olabilir. Yaprak/dugum/bos alan
  indeksleri okunmaz — mantiksal 32 GB altindaki veri bloklarinin
  taranmasi tum girisleri verir.
* Sembolik bag: yerel ya da uzak (v5 XSLM basligi).

## Test verisi

mkfs.xfs 6.18 **`-p dizin`** ile icerikli birim uretir (cekirdek baglamasi
gerekmez). En kucuk XFS 300 MB; seyrek goruntu gzip ile 460 KB
(`tests/fixtures/xfs_v5.img.gz`): 2000 girdilik dizin (B-agacli dugum
dizini), 300 kapsamli seyrek dosya (B-agacli catal), uzak sembolik bag.

## Dogrulama

* 5067 dosyalik agac (5000 girdilik dizin, 1500 kapsamli seyrek dosya,
  3 MB rastgele dosya): v5 ve v4'te tum icerik, dizin listeleri, baglar
  birebir (0,7 sn).
* Varyantlar: 1 KiB blok, 16 KiB dizin blogu, nrext64=0/bigtime=0 — birebir.
* t65 (fixture her platformda; mkfs.xfs varsa varyantlar).

## Sinanmayan
* Genis oznitelikler (xattr) gosterilmiyor; gercek zamanli (rt) alt birim,
  reflink paylasimi ozel durum gerektirmez (okuma icin) ama ayrica sinanmadi.
