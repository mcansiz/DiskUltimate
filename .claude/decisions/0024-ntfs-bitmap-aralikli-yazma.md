# ADR 0024 — NTFS `$Bitmap`: tamamini degil, degisen bolumu yaz

**Tarih:** 2026-09-15
**Durum:** Kabul edildi
**Ilgili:** ADR 0010 (FAT tahsis basarimi), ADR 0017 (saf Python ext ve NTFS),
ADR 0022 (birim kilidi)

## Baglam

ADR 0022'deki hatayi incelerken reddedilen yazmanin boyutu dikkat cekti:

```
disk.write(lba=8452440, size=1915168) — HATA AccessDeniedError
```

`1 915 168` bayt rastgele bir sayi degil: 58.45 GB'lik bolumun **butun kume
bitmap'i** (58.45 GB / 4 KB kume / 8 bit). Tek bir dosya silinirken bitmap'in
tamami diske yaziliyordu.

Koda bakinca daha kotusu gorundu — `alloc_clusters` ve `free_clusters` her
cagrida:

1. `read_attribute(attr)` ile bitmap'in **tamamini okuyor** (1.9 MB),
2. birkac biti degistiriyor,
3. `_write_attr_data` ile **tamamini yaziyor** (1.9 MB).

Yani her tahsis ve her serbest birakma 3.8 MB G/C. Bir dosya eklemek en az bir
tahsis + MFT bitmap yazmasi demektir; silmek de bir serbest birakma.

Bu bir dogruluk sorunu degildi — sonuc her zaman dogruydu. Ama:

- **SD kart / USB bellek yipranir.** Bu ortamlarda yazma dongusu sinirlidir;
  32 KB'lik bir dosya icin 1.9 MB yazmak olculebilir bir israftir.
- **Yavastir.** Kart okuyucu uzerinden 1.9 MB okuma + yazma kullanicinin
  hissedecegi bir gecikmedir; ustelik bu, arayuzun dondugu senaryonun
  tam ortasindaydi.
- Ayni hata ext tarafinda bir kez giderilmisti (ADR 0010, `alloc_blocks` toplu
  tahsisi). NTFS tarafinda kalmis.

## Karar

### 1. Aralikli okuma / yazma

`ntfsread.NtfsFS`:
- `attribute_size(attr)` — yerlesik olsun olmasin icerik boyutu,
- `read_attribute_range(attr, offset, length)` — yalnizca istenen araligi
  okur; `initialized_size` sonrasini yine sifir doldurur.

`ntfswrite.NtfsWriter`:
- `_write_attr_range(attr, data, offset)` — yerlesik olmayan bir oznitelugun
  yalnizca bir araligini, kume zinciri uzerinden yazar,
- `_align_range(low, high, limit)` — yazilan araligi sektor sinirina disa dogru
  genisletir. Aygit yazmalari sektor tanelidir; hizalamayi burada yapmak alt
  katmandaki oku-degistir-yaz davranisini ongorulebilir kilar.

Tam oznitelik yazan `_write_attr_data` ve `_read_bitmap` **silindi**: iki yol
birakmak, birinin yanlislikla kullanilmaya devam etmesi demekti.

### 2. Tahsis pencere pencere tarar

`alloc_clusters` bitmap'i `BITMAP_WINDOW` (64 KB) buyuklugunde pencerelerle
okur, penceredeki degisikligi yazar ve gerekirse bir sonraki pencereye gecer.
Tahsis cogunlukla ilk pencerede biter.

Pencere sinirini gecen bos alan iki parca gorunur; **bitisikse birlestirilir**,
yoksa veri kosullari (run list) gereksiz yere uzar ve $MFT kaydina sigmayabilir.

Yetersiz alan durumunda alinan kumeler **geri verilir**: kismi tahsis
birakilmaz (onceki surumde de birakilmiyordu, bu davranis korundu ve artik
test ediliyor).

### 3. Serbest birakma yalnizca ilgili araliklari okur

`free_clusters` her run icin etkilenen bayt araligini hesaplar, sektor
sinirina hizalar, **cakisan/bitisik araliklari birlestirir** ve her arilik icin
tek okuma + tek yazma yapar.

### 4. `$MFT` bitmap'i de aralikli

`alloc_record` / `free_record` ortak `_flush_mft_bitmap()` uzerinden yazar:
yerlesikse kayit icinde (zaten kucuk), degilse yalnizca degisen bitin sektoru.

## Olcum

Kullanicinin kartiyla ayni geometride (58.45 GB bolum, 4 KB kume,
`$Bitmap` = 1 915 290 bayt), sayac tutan bir aygit sarmalayicisiyla:

| Islem | Okuma | Yazma |
|---|---|---|
| `alloc_clusters(8)` — onceki | 1 915 290 B | 1 915 290 B |
| `alloc_clusters(8)` — simdi | **65 536 B** | **512 B** |
| `free_clusters` — onceki | 1 915 290 B | 1 915 290 B |
| `free_clusters` — simdi | **512 B** | **512 B** |

Tahsis + serbest birakma toplami: **7 661 160 B -> 67 072 B (114 kat az G/C)**.
Yalnizca **yazma** tarafinda: 3 830 580 B -> 1 024 B, yani **3741 kat az
yazma** — karti yipratan sey budur.

## Dogrulama

`t23_ntfs_bitmap_aralikli_yazma`. Olcut net tutuldu:

> **Aralikli yazmadan sonraki bitmap, tam yazmanin uretecegi bitmap ile
> birebir ayni olmalidir.**

Test once bitmap'in tamamini okur, islemi yapar, beklenen bitmap'i bellekte
hesaplar ve diskten okunanla karsilastirir. Pencere siniri de zorlanir:
`BITMAP_WINDOW` gecici olarak 64 bayta dusurulur, boylece kucuk bir test
biriminde bile tahsis birden fazla pencereye yayilir.

Testin **gercekten hata yakaladigi** iki kasitli bozma ile denetlendi:

| Bozma | Sonuc |
|---|---|
| Yazma ofsetine +512 eklendi | `AssertionError: tahsis sonrasi bitmap tam yazmadan farkli` |
| Pencere adimi `length + 1` yapildi (bir bayt atlanir) | `AssertionError: bitisik alan tek parca donmeli, donen: [(584, 448), (1040, 512), ...]` |

Ayrica dagitik serbest birakmada komsu bitlerin bozulmamasi ve basarisiz
tahsisin bitmap'te iz birakmamasi denetlenir.

`tests.run_all` 23/23 · `platform_check` 0 bulgu · `diag_check` 13/13 ·
`ui_smoke` tamam.

## Kalan

- Tahsis hala bitmap'i bastan tarar (ilk pencereden baslar). "Son bulunan bos
  kume" ipucu tutulsa bos alani dolmus birimlerde tarama da kisalirdi. Bu
  surumde yapilmadi: ipucunun yanlis olmasi sessiz bir bozulma degil, yalnizca
  yavaslama uretir ama dogrulanmasi ayri bir is.
- `ntfsfix` / `ntfs-3g` ile tam dogrulama (`tests/ntfs_write_check.py`) Windows
  makinesinde calistirilamaz (arac yok); Linux tarafinda kosulmalidir.
