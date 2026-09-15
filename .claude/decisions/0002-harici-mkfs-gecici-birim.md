# ADR 0002 — exFAT/NTFS/ext4 icin gecici birim uzerinde `mkfs`

**Tarih:** 2026-09-13 · **Durum:** ❌ Gecersiz kilindi — [ADR 0007](0007-saf-python-exfat.md) (exFAT) ve [ADR 0017](0017-saf-python-ext-ve-ntfs.md) (ext, NTFS)

> Bu karar v0.1.0 doneminde alindi: exFAT/NTFS/ext bicimlendirmesi harici
> `mkfs.*` araclarina birakiliyordu. v0.2.0'da exFAT, v0.3.0'da ext ve NTFS
> saf Python'a tasindi. Harici arac yolu **silinmedi** — `formatter.py`
> icinde hala oncelik siralamasinin ikinci basamagidir (yerel arac →
> `mkfs.*` → saf Python), ama artik tek secenek degildir. Asagidaki metin
> tarihsel kayit olarak birakilmistir.

## Baglam
FAT disindaki dosya sistemlerini sifirdan yazmak makul degil. Sistemdeki `mkfs.*`
araclari ise dosya icindeki bir **ofsetten** itibaren bicimlendirmeyi tutarli
sekilde desteklemiyor:
- `mkfs.vfat --offset` var (dosfstools 4.2+)
- `mke2fs -E offset=` var ama boyut ayrica verilmeli
- `mkfs.exfat` ve `mkfs.ntfs` ofset kabul etmiyor

## Karar
Bolum boyutunda **seyrek gecici bir dosya** olusturulur, `mkfs` bu dosya uzerinde
ofset 0'dan calistirilir, ardindan sonuc bolum penceresine geri kopyalanir
(`formatter._format_external`).

## Ayrinti: seyreklik korunmasi
Geri kopyalamada 4 MiB'lik bloklar taranir:
- blok sifir degilse → yazilir
- blok tamamen sifirsa → hedefteki karsiligi okunur; o da sifirsa **yazilmaz**

Boylece 200 MB'lik bir ext4 bolumu goruntu dosyasinda yalnizca gercek metaveri
kadar yer kaplar. `t05` testi goruntunun seyrek kaldigini dogrular.

## Alternatifler
- **Loop aygiti:** root gerekir — reddedildi.
- **Her araca ozel ofset bayragi:** araclar arasinda tutarsiz, surum bagimli — reddedildi.

## Sinir
`mkfs.*` kurulu degilse o dosya sistemi arayuzde listelenmez
(`formatter.available_kinds`). FAT her zaman kullanilabilir.
