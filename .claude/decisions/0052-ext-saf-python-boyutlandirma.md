# 0052 — ext2/3/4 saf Python boyutlandirma (buyutme, kucultme, tasima)

Tarih: 2026-09-29
Durum: **uygulandi** — Linux (goruntu dosyalari, e2fsck) ve Windows
(VirtualBox win10, sonuc Linux'ta e2fsck ile) dogrulandi. macOS'ta kosulmadi.
Ilgili: [0019](0019-bolum-boyutlandirma.md), [0030](0030-ntfs-saf-python-boyutlandirma.md),
[0017](0017-saf-python-ext-ve-ntfs.md)

## Baglam

Kullanici bildirimi: *"linuxda ext4 ile disk buyutme calismiyor, bu kabul
edilemez."* Kok neden olculdu: `resize.fs_resize_info` ext icin saf Python
yol sunmuyordu; "yerel" yol ise yalnizca Windows'un `Resize-Partition`
aracina gidiyordu ve o arac ext'i **tanimaz**. Yani ext boyutlandirmasi
hicbir platformda calismiyordu — Linux'ta bile, `resize2fs` kurulu olsa da.

## Karar

`resize2fs`'in cevrimdisi karsiligi saf Python ile yazildi; her platformda
ve goruntu dosyalarinda ayni kod calisir. Harici arac katmani eklenmedi:
ayni aygita ikinci bir yerden dokunmamak (ADR 0021) ve uc platformda ayni
sonucu almak icin.

| Modul | Gorev |
|---|---|
| `core/extlayout.py` | Yerlesim geometrisi: yedek ustblok gruplari (sparse_super), klasik GDT + ayrilmis bloklar, **meta_bg** yerlesimi, crc16/crc32c saglamalari. Okuyucu, yazici ve boyutlandirici artik ayni hesabi kullanir. |
| `core/extresize.py` | Buyutme: son grubu uzatma, yeni gruplar, ayrilmis GDT tuketme + resize inode (7) yeniden kurma, yetmezse **meta_bg'ye gecis** (resize2fs de boyle yapar). |
| `core/extmove.py` | Kucultme: kesilen bolgedeki veri/extent dugumu/dolayli tablo/xattr/gunluk bloklarini tasima, silinecek gruplardaki **inode'lari yeniden numaralama** ve onlara isaret eden dizin girislerini (htree dahil) duzeltme; tum metadata_csum saglamalarinin yeniden hesabi. |
| `core/resize.py` | `kind="ext"`: sinirlar, uygulama sirasi, tasima (ext konum tutmaz; ilk sektor onyukleyiciye ait oldugu icin yamalanmaz). |

Tasarim kararlari:

* **Yeni gruplarin metaverisi kendi icine** yerlestirilir. flex_bg birimde de
  gecerlidir; boylece eski gruplarin hicbir blogu yer degistirmez.
* **Saglamali birimde inode tablosu sifirlanmaz** (`INODE_UNINIT`, cekirdek
  ilk baglamada arka planda sifirlar). Saglamasiz birimde (ext2/ext3)
  sifirlanmak zorunda — yoksa e2fsck eski cop veriyi inode sanar.
* **Extent kullanan dosyanin agaci bastan kurulur** (`build_extent_tree`):
  kesilen araliklar parca parca tasinabilir, giris sayisi artarsa agac
  derinlesir. Yama yerine yeniden kurmak daha az durum tasir.
* **Tasinacak inode'lar bastan bellege okunur**, silinecek gruplarin kalan
  bolgedeki metaverisi (flex_bg'de 16 grubun inode tablosu ilk grupta, 4K
  blokta ~32 MB) ancak ondan sonra serbest birakilir. Ilk surum bunu en
  sona birakiyordu: guvenliydi ama buyuk kucultmede "bos alan yok" diyordu;
  erken birakmak ise tasinacak inode'lar okunmadan o bloklari baska veriye
  verebilirdi.

## Bulunan hatalar (yol boyunca, hepsi olcumle)

1. **meta_bg'de klasik GDT kopyalari** meta bolgesindeki yedek gruplara da
   yaziliyordu → o grubun blok bitmap'i eziliyordu (81 ve 125. gruplar).
2. **dx (htree) saglamasi** — cekirdek `ext4_dx_csum` `dt_reserved`'dan
   sonra sifir sayilan 4 bayti da katar; atlaninca e2fsck "root node fails
   checksum".
3. **Ayrilmis GDT ust siniri** — kucultmede bosalan GDT bloklari ayrilmis
   alana eklenirken blok basina adres sayisi (1K'da 256) asildi → e2fsck
   ustblogu bozuk saydi. Asan kisim artik serbest birakilir.
4. **Mevcut yazicida (`extwrite`) iki eski hata:** `RO_BIGALLOC = 0x0100`
   aslinda `quota` bayragiydi (bigalloc 0x0200) — bigalloc birime yaziliyor,
   kotali birim tesadufen reddediliyordu. Duzeltilince kotali birime yazma
   acildi ve e2fsck "kota guncellenmeli" dedi; kota artik **acikca** ve
   nedeniyle reddedilir. Ayrica eski `uninit_bg` (crc16) birimde grup
   saglamasi hic guncellenmiyordu; artik iki algoritma da desteklenir.
5. Okuyucu ve yazici GDT'yi hep ustbloktan sonra bitisik varsayiyordu;
   resize2fs ile buyutulmus (meta_bg) gercek birimlerde yanlis tanimlayiciyi
   okurlardi.

## Dogrulama

* `tests/ext_resize_check.py` — **28/28 temiz**: mkfs.ext2/3/4 ile
  olusturulan gercek birimler (64bit, flex_bg, metadata_csum, orphan_file,
  1K blok, ^resize_inode, meta_bg, uninit_bg/crc16, ^64bit, ^flex_bg);
  buyutme (30 GB'a, meta_bg'ye gecis dahil), zincirli buyutme, gunluk
  tasiyan kucultme, 596 inode'un yeniden numaralandigi kucultme, 800 girisli
  htree dizininin kendisinin tasindigi kucultme. Her adimda `e2fsck -fn`
  temiz ve `debugfs rdump` ciktisi kaynakla birebir.
* `tests/run_all` t48 — kendi bicimlendiricimizin ext2/3/4 birimleri:
  buyut → asgariye kucult → tasi+buyut; icerik ve e2fsck.
* **Windows (VirtualBox win10):** t48 islemleri calisti (e2fsck misafirde
  yok → denetim atlandi). Ek olarak ana makinede mkfs.ext4/ext3 ile
  hazirlanan uc goruntu Windows'ta 2 GB'a buyutulup en kucuge
  kucultuldu (596 inode tasindi); ana makinede `e2fsck -fn` uc goruntude
  de **temiz**, icerik birebir.
* macOS: test ortami yok, **kosulmadi**. Kod platformdan bagimsizdir (yalnizca
  `BlockDevice` okuma/yazma).

## Sinirlar (acikca reddedilir)

`bigalloc`, ayri gunluk aygiti, `mmp`, `sparse_super2`, temiz kapatilmamis
birim (islenmemis gunluk), kucultmede `inline_data`/`ea_inode`, kotali
birimde inode tasima. Boyutlandirma cevrimdisidir: bagli (mount edilmis)
birimde calistirilmamalidir; fiziksel diskte bagli bolum uyarisi (CLAUDE.md
guvenlik kurali 5) gecerlidir. Cevrimici buyutme cekirdek isidir.
