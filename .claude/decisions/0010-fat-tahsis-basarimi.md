# ADR 0010 — FAT kume tahsisinde basarim duzeltmesi

**Tarih:** 2026-09-13 · **Durum:** Uygulandi

## Belirti
Bos alan silme testi 120 saniyede tamamlanmadi. Olcum yapildiginda FAT32 birime
**8 MB dosya yazmanin 100 saniyeden uzun surdugu** gorundu.

## Kok neden
`FatFS.alloc_cluster` her cagrisinda arama sirasini bir **liste** olarak uretiyordu:

```python
for c in list(range(start, self.max_cluster + 1)) + list(range(2, start)):
```

100.000 kumelik bir birimde bu, her kume tahsisinde 100.000 elemanlik iki liste
olusturmak demekti. 8 MB dosya = 2048 kume = ~200 milyon gereksiz islem.

Ikinci sorun: `free_clusters()` her `flush()` cagrisinda FAT'i kume kume tarayarak
sifirdan sayiyordu; `write_file` her yazimda `flush()` cagiriyor.

## Duzeltme
1. Liste uretimi kaldirildi; `_next_free` ipucundan baslayan iki `range` dongusu
   kullanildi. Ardisik tahsisler boylece amortize O(1) oldu.
2. Bos kume sayaci onbellege alindi (`_free_count`); tahsis/serbest birakma
   sirasinda guncelleniyor. Ilk sayim `array` modulu ile toplu yapiliyor
   (FAT32 `array('I')`, FAT16 `array('H').count(0)`), big-endian makinede
   `byteswap()` uygulaniyor.
3. Serbest birakilan kume `_next_free`'den kucukse ipucu geri cekiliyor, boylece
   bosalan alan yeniden kullaniliyor.

## Sonuc

| Islem | Once | Sonra |
|---|---|---|
| FAT32 8 MB yazma | >100 s | 0.04 s |
| FAT32 64 MB yazma | — | 0.25 s (~250 MB/s) |
| FAT32 64 MB okuma | — | 0.20 s (~327 MB/s) |

exFAT tarafi olculdu ve zaten hizliydi (~860 MB/s): tek bir toplu tahsis yapiyor.

## Regresyon korumasi
`tests.run_all::t15_yazma_basarimi` — 32 MB yazma ve okuma 10 saniyeyi asarsa test
basarisiz olur. Bu sinir, gercek degerin (0.1–0.3 s) cok uzerinde tutuldu ki yavas
makinelerde yanlis alarm vermesin ama algoritmik geri gidis yakalansin.
