# ADR 0037 — Bicimlendirdigimiz NTFS'e isletim sisteminin surucusu yazabilmeli

**Tarih:** 2026-09-18
**Durum:** Kabul edildi
**Ilgili:** ADR 0017 (saf Python ext ve NTFS), ADR 0018 (NTFS platform
stratejisi), ADR 0030 (NTFS boyutlandirma)

## Baglam

Mount/unmount ozelligi icin olcum yapilirken ortaya cikti: uygulamayla NTFS
bicimlendirilen bir bolum **baglaniyor ve okunuyor**, ama isletim sisteminin
surucusu uzerine dosya olusturamiyordu — root olarak bile:

```
touch /mnt/t3/deneme.txt  ->  Invalid argument (EINVAL)
ntfsfix -n /dev/sdb3      ->  "processed successfully"
```

Yani birim, denetim araclarina gore **saglamdi**; kusur yalnizca yazma
yolunda gorunuyordu. Kullanici acisindan sonuc agirdi: uygulamayla
bicimlendirilen bir NTFS bolume dosya kopyalanamiyordu.

Bugune kadar gorulmemesinin sebebi testlerin bicimiydi: okuma, kendi
yazicimiz (`ntfswrite.py`) ve `ntfsfix`/`ntfsinfo` denetimleri geciyordu;
`ntfs_write_check` ise birimi yalnizca **`-o ro`** baglıyordu.

## Yontem

Her adimda ayni iki sey yapildi:

1. Referans `mkfs.ntfs` birimiyle **yan yana** olcmek (ayni boyut, ayni
   surucu, loop aygiti) — fark nerede?
2. `ntfs-3g -o debug` ile **sebebi surucuye soyletmek** — tahmin etmemek.

Bu, uc ayri eksigi sirayla ortaya cikardi; her biri ancak bir oncekini
gecince gorundu.

## Karar

### 1. `$Secure` gercek icerik tasir

NTFS 3.x'te her dosyaya bir guvenlik kimligi atanir; tanimlayicilar `$Secure`
dosyasinda durur. Bizde `$SDS` akisi 0 bayt, `$SDH`/`$SII` indeksleri bostu.

Artik iki standart tanimlayici uretilir — `0x100` (SYSTEM + Administrators,
okuma) ve `0x101` (ayni kimlikler, okuma + yazma) — 20 baytlik basliklariyla
`$SDS`e yazilir ve icerik 256 KiB'de aynalanir. `$SDH` (karma anahtarli) ve
`$SII` (kimlik anahtarli) indeks kokleri doldurulur.

Tanimlayici baytlari ve NTFS karma islevi (`hash = word + rol32(hash,3)`)
referans birime karsi dogrulandi: ikisi de **birebir** ayni cikiyor. Bu yuzden
kod gomulu bir ikili blok degil, **kurucu**dur — ne uretildigi okunabilir.

### 2. `$MFT:$BITMAP` yerlesik degildir

Asil engel buydu. Surucu yeni MFT kaydi ayirirken bitmap'in son kumesini
sorar; yerlesik oznitelikte kume yoktur:

```
Failed to determine last allocated cluster of mft bitmap attribute. -> EINVAL
```

Referansta bitmap ayri bir kumede durur. Artik bizde de oyle: `$MFT:$BITMAP`
kendi kumesine yazilir ve boyutu MFT kapasitesine gore 8 baytin kati olarak
hesaplanir.

### 3. Kok dizin kendi girisini ("." -> kayit 5) tasir

Birinci engel kalkinca ikincisi gorundu: surucu dosya olusturduktan sonra
**ust dizinin** adini ust dizinin indeksinde arayip tazeliyor; kok dizin icin
bu arama kendi "." girisine duser. Giris yoksa islem yarida kalir
(*"Index lookup failed, inode 5"* -> G/C hatasi).

Artik kok indeksine kendi girisi de yazilir. Kullaniciya gosterilen listede
gorunmez: okuyucumuz "." girisini zaten atlar.

> Yol boyunca bir de ofset hatasi yakalandi: guvenlik kimligi
> `$STANDARD_INFORMATION` icinde **0x34**'tedir, 0x40 USN alanidir. Ilk yama
> yanlis yere yaziyordu; yeni test bunu hemen yakaladi.

## Testler: eksik olan neydi

Bu sinif hatanin tekrar kacmamasi icin iki denetim eklendi:

* **`t42_ntfs_isletim_sistemi_yazabilmeli`** (root gerektirmez): uc kusuru da
  **yapisal** olarak sinar — bitmap yerlesik mi, kok "." girisi var mi,
  `$Secure` gercek icerik tasiyor mu (karmalar ve tanimlayici baytlari dahil).
* **`tests/ntfs_write_check.py`** artik birimi **yazma icin** de bagliyor:
  surucu dosya ve klasor olusturabiliyor mu, yazdigi geri okunuyor mu.
  Salt okunur baglamak, bu kusuru yillarca "dogrulandi" diye raporlamisti.

Ders kayda geciyor: **bir dosya sistemi "okunabiliyor" diye dogrulanmis
sayilmaz.** Yazma yolu ayri bir yoldur ve ayri sinanmalidir.

## Sonuc

Olculdu (Mint 22.3):

| Kosum | Once | Sonra |
|---|---|---|
| ntfs-3g ile dosya olusturma (goruntu) | EINVAL | **TAMAM** |
| `tests.ntfs_write_check` (root) | 1/2 | **2/2** |
| `/dev/sdb` uzerinde gercek senaryo | — | **TAMAM** |

Gercek disk kosumu kullanicinin yaptigi seydi: uygulamayla GPT + NTFS bolum,
sonra isletim sisteminin surucusuyle yazma. Metin dosyasi, ic ice klasor ve
20 MB rastgele dosya yazildi; yeniden baglamada sha1 ozeti ayni; `ntfsfix -n`
temiz, birim bayraklari 0x0000, surum 3.1. Uygulamanin kendi okuyucusu da
isletim sisteminin yazdigi dosyalari goruyor — gidis-donus iki yonde de
calisiyor.

Windows tarafinda `chkdsk` ile dogrulama **yapilmadi**; yapildiginda buraya
yazilacak (Win10 misafiri, CLAUDE.md test ortami kurali).
