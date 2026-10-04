# 0081 — Akis (parca parca) dosya yazma

Tarih: 2026-10-04
Durum: **uygulandi** — uzun testler planinin (ADR 0080) Asama 0'i.
Ilgili: [0073](0073-goruntu-konumu-tasima-kesintisi-ext4-ozellikleri.md)
(dosyanin tek parca okunmasi siniri), [0080](0080-uzun-testler-plani.md).

## Sorun

Alti yazicinin (FAT, exFAT, NTFS, ext2/3/4, HFS+, UDF) hepsi dosyayi tek
`bytes` olarak aliyordu; `import_file` yerel dosyayi `fh.read()` ile tamamen
okuyordu. 5 GB dosya 5 GB bellek isterdi (macOS runner 7 GB). Tarama ayrica
uc hata buldu:

* **FAT32'de 4 GiB-1 siniri denetlenmiyordu:** 4 GiB+ dosya once tamamen
  yaziliyor, dizin girisinde `struct.error` ile dusuyor, kumeler bosa
  cikmiyordu. Birim dolunca da ayni sizinti.
* **NTFS:** cok parcali dosyada "kosullar kayda sigmiyor" hatasi veri
  YAZILDIKTAN sonra cikiyordu.
* **ext:** yarida kalan yazmada inode geri veriliyor, veri bloklari
  verilmiyordu.

## Karar

* `core/streamio.py`: `read_exact` (kaynak erken biterse `StreamError` —
  eksik dosya sifirla doldurulmaz), `group_runs` (ardisik birimleri
  birlestirir), `write_runs` (4 MiB parcalar, son birim sifirla doldurulur).
* Her yazicida `write_stream(path, src, size)`; `write_file(path, data)`
  artik `write_stream(path, BytesIO(data), len(data))`. Siralama degismedi:
  boyuttan yer ayir -> veriyi parca parca yaz -> ustveri.
* Denetimler yer ayrilmadan once: FAT 4 GiB-1 ve bos alan; NTFS kaydi
  veri yazilmadan kurulur (sigmazsa veri yazilmaz). Hata olunca ayrilan
  alan geri verilir (FAT `free_chain`, exFAT `free_chain`, NTFS mevcut geri
  alma, ext `free_blocks`, HFS+ `free_runs`, UDF `free`).
* Kucuk dosyalar boyuttan karar verilip kayda gomulur (NTFS resident,
  UDF embedded) — yalnizca o kucuk kisim bellege okunur.
* Erisim katmani: taban `import_file` yerel dosyayi acip `write_stream`e
  verir; alt siniflardaki `fh.read()` kopyalari silindi.

## Olculen

* 2 GiB dosya `import_file` ile (dosya gezgininin yolu): en yuksek bellek
  ext4 53 MB, NTFS 33 MB, exFAT 32 MB (eskiden >= 2 GiB). Hiz 667 / 555 /
  318 MB/s (ardisik kumeler tek yazimda). Icerik SHA-1 ile ayni;
  e2fsck / ntfsfix / fsck.exfat temiz.
* Parcali yazma (bosluklara dagilan dosya) ve yarida kalan kaynak her
  yazicida sinandi: geri okuma esit, yarim dosya yok, sizinti yok;
  e2fsck (ext2/ext4), fsck.vfat, fsck.exfat, ntfsfix temiz. HFS+/UDF dis
  denetimi bu makinede arac yok — CI'da (hfsprogs, udftools).

## Sinirlar

* ext tahsisi hala her blok icin bir tamsayi listesi tutar (5 GiB ~ 1.3M
  oge, ~50 MB). Kabul edildi; uzun testler olcer.
* Okuma tarafi (`read`) hala tum dosyayi dondurur; `extract` akistir.
