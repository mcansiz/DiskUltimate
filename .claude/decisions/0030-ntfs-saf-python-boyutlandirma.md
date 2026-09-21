# ADR 0030 — NTFS boyutlandirmasi saf Python ile, her platformda

**Tarih:** 2026-09-17
**Durum:** Kabul edildi
**Ilgili:** ADR 0017 (saf Python ext ve NTFS), ADR 0018 (NTFS platform
stratejisi), ADR 0019 (bolum boyutlandirma), ADR 0024 (NTFS bitmap araliklı
yazma)

## Baglam

Kullanici bildirdi: *"linuxda NTFS boyutlandirmasi yapamiyorum, bu
crossplatform icin eksik bir durum."*

Dogruydu. `core/resize.py` NTFS'i su sekilde ele aliyordu:

* Windows + **fiziksel disk** → isletim sisteminin `Resize-Partition` komutu
  (`platform.native_resize_supported()` yalnizca `IS_WINDOWS` doner).
* Diger her durum → `FsResizeInfo(kind="unsupported")`, yani islem
  **reddedilir**.

Yani Linux ve macOS'ta NTFS hic boyutlandirilamiyordu; goruntu dosyalarinda
ise **hicbir platformda** calismiyordu — Windows'taki yol bile disk numarasi
isteyen bir PowerShell komutuna bagliydi.

Bu, projenin iki temel iddiasiyla celisiyordu: "Windows / Linux / macOS" ve
"gorunt dosyasi ile gercek disk ayni kodla islenir".

## Secenekler

1. **Linux'ta `ntfsresize` (ntfs-3g) cagirmak.** Kolay, ama: goruntu
   dosyalarinda calismaz (arac ofset kabul etmez, `losetup` root ister),
   macOS'ta paket yoktur ve calisma zamani bagimliligi yaratir — CLAUDE.md
   "cekirdek saf Python" der. Windows'ta karsiligi yoktur; yani ucuncu bir
   platform dali daha acardi.
2. **Saf Python NTFS boyutlandirma.** Daha fazla is, ama tek kod yolu: her
   platformda, goruntu dosyasinda ve fiziksel diskte ayni davranis.

Proje zaten NTFS'i **bicimlendiriyor** (`ntfs.py`), **okuyor** (`ntfsread.py`)
ve **yaziyor** (`ntfswrite.py`): kume tahsisi, veri kosulu kodlama, FILE kaydi
yazma ve fixup altyapisi hazirdi. Eksik olan parca kucuktu.

## Karar

**2 secildi.** `core/ntfsresize.py` NTFS birimini saf Python ile kucultup
buyutur; `resize.py` bunu FAT/exFAT ile ayni akista cagirir.

### Neyin guncellendigi

Bir NTFS biriminin boyutu dort yerde yazar ve hepsi tutmak zorundadir:

| Yapi | Ne yapilir |
|---|---|
| Onyukleme sektoru (0x28) | `sektor_sayisi - 1` yazilir (son sektor yedek icindir) |
| Yedek onyukleme sektoru | birimin **yeni** son sektorune kopyalanir |
| `$Bitmap` | kume sayisi kadar bit; artan bitler **dolu** isaretlenir |
| `$BadClus:$Bad` | birim boyutunda "delik" akis; boyutu guncellenir |

Ayrica `$LogFile` 0xFF ile doldurulur (bos gunluk). Eski gunluk kayitlari artik
var olmayan kume numaralarina gonderme yapabilir; yeniden oynatilirsa birimi
bozar. `ntfsresize` de ayni seyi yapar.

### Kucultmede tasima

Gercek bir Windows biriminde meta veri ve dosya parcalari her yere dagilmistir;
`$MFTMirr` cogu kez birimin ortasindadir. "Sinirin otesinde dolu kume varsa
reddet" demek kucultmeyi pratikte imkansiz kilardi. Bu yuzden sinir otesindeki
**kume araliklari tasinir**: yeni yer tahsis edilir, veri kopyalanir,
oznitelugun veri kosullari yeniden yazilir. Yalnizca etkilenen parcalar tasinir
(100 GB'lik bir dosyanin sonu sinirin otesindeyse yalnizca o son tasinir).

Iki incelik olculdu ve koda yazildi:

* **Once butun tahsisler, sonra kopyalar.** Tahsis `$Bitmap`'e yazar; bitmap'in
  kendisi tasiniyorsa araya giren bir tahsis, az once kopyalanmis bir bolgeyi
  bayatlatir. Iki asamali dongu bunu onler.
* **Sinir otesi once "dolu" isaretlenir.** Boylece tasima sirasinda yapilan
  tahsisler oraya dusemez. Ayri bir "ust sinir" parametresi tasimaya gerek
  kalmaz.
* **`$MFT` kendini tasiyabilir.** Kayit yazmadan once `fs._mft_runs` yeni
  zincire cevrilir; yoksa guncellenmis kayit **eski** yerine yazilirdi.
  Onyukleme sektorundeki `mft_lcn` / `mftmirr_lcn` de guncellenir.

### Reddedilen durumlar (sessizce gecilmez)

* sikistirilmis / sifrelenmis / seyrek akislar,
* yeni veri kosullari kendi FILE kaydina sigmiyorsa (bu surumde
  `$ATTRIBUTE_LIST` uretilmez),
* birim "kirli" isaretliyse — once `chkdsk` / `ntfsfix` calismalidir.

`ntfswrite.py` ile ayni ilke: **yanlis yazip bozmaktansa yazmamak yeglenir.**

### Windows fiziksel disklerde ne degisti

Hicbir sey. `fs_resize_info()` sirasi korundu: Windows + fiziksel disk yolunda
hala `Resize-Partition` tercih edilir. Isletim sistemi bagli birimi kendisi
cozer, birim kilidiyle ugrasmaz. Saf Python yolu **goruntu dosyalarinda ve
Windows disi platformlarda** devreye girer.

## Sonuc

Linux misafirinde olculdu (Mint 22.3, 2026-09-17):

* `tests.run_all` t35 (dosya sistemi) ve t36 (bolum tablosuyla birlikte) —
  kucultme, buyutme, veri korunmasi, `ntfsinfo -m` ve `ntfsfix -n`.
* `tests/physical_ntfs_resize.py` — `/dev/sdb` uzerinde **`mkfs.ntfs` ile
  olusturulmus** 4 GB'lik birim ntfs-3g ile doldurulup 2 GB'a kucultuldu,
  sonra 3 GB'a buyutuldu. Her adimdan sonra `ntfsfix -n`, `ntfsinfo -m` ve
  ntfs-3g ile baglanip **sekiz dosyanin sha256 ozeti** karsilastirildi: ayni.
  Kucultmede 5243 kume tasindi.

Boyutlandirma artik: FAT12/16/32, exFAT, **NTFS** → saf Python, her platformda.
ext ailesi hala harici arac ister; bu ayri bir istir.
