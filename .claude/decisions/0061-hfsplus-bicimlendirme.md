# 0061 — HFS+ bicimlendirme (saf Python, gunluksuz)

Tarih: 2026-09-29
Durum: **uygulandi** — ana makinede goruntu dosyalariyla, fsck.hfsplus
(hfsprogs) ile dogrulandi. **macOS'ta sinanmadi.**
Ilgili: [0060](0060-hfsplus-okuma.md)

## Karar

`core/hfsformat.py::format_hfsplus` — bos HFS+ (ya da `case_sensitive=True`
ile HFSX) birimi. `formatter.FS_KINDS`'e "hfsplus" (MBR 0xAF, GPT Apple HFS
GUID'i) olarak eklendi; arayuzun bicim listesinde gorunur.

Yerlesim **mkfs.hfsplus'in (Apple newfs_hfs'in Linux portu) ciktisi
olculerek** cikarildi — 1M, 2M, 4M, 8M, 64M, 512M, 1000M, 1G, 3G, 17G, 40G:

* Blok 4096. Dugum: kapsam 4096, oznitelik 8192, katalog 1 GB altinda
  4096, ustunde 8192.
* B-agaci kumeleri: 1 GB altinda birim/128 (en az 8 dugum); ustunde
  newfs_hfs tablosu (1G 4/4/4 MB ... 16T 512/512/32 MB; satir =
  floor(log2(GB))). Olculen satirlarla dogrulandi.
* Sira: basliktan sonra ayirma dosyasi, kapsam, oznitelik, **10 x oznitelik
  kumesi bosluk** (birimin yarisina sigmiyorsa yok), katalog; sonraki ayirma
  ipucu katalog + 10 x katalog kumesi. Son blok yedek baslik.
* Katalog: (1, ad) kok klasor + (2, "") iplik kaydi, tek yaprak. Baslik
  ozniteligi 0x80000100 (temiz kapatildi + kullanilmayan dugumler sifir),
  son baglayan "10.0", birim kimligi finderInfo[6..7].
* Cok buyuk agaclar icin (bitmap baslik dugumune sigmazsa) harita
  dugumleri zincirlenir.

Sonuc: 64M birimde mkfs.hfsplus ile **bayt bayt ayni**; fark yalnizca tarih
alanlari ve rastgele birim kimligi. Bos blok sayilari tum olculen
boyutlarda birebir.

## Neden gunluksuz

Madde "gunluksuz" diye tanimliydi: gunluk (journal) yazmak her meta veri
degisikliginde islem kaydi gerektirir; yazici (sonraki madde) gunluksuz
birimde calisacak. macOS gunluksuz HFS+'i sorunsuz baglar; istenirse Disk
Izlencesi gunlugu sonradan acar.

## Dogrulama

* fsck.hfsplus `-f -n`: 1M ... 40G (seyrek), blok katı olmayan boy
  (13 333 333 bayt), HFSX — hepsi "appears to be OK".
* Kendi okuyucumuz: etiket (Turkce, NFD/NFC), bos kok, istatistik.
* 7z: birimi aciyor (blok katı olmayan boyda "sonda veri var" uyarisi —
  birim disinda kalan artik bayt, beklenen).
* t60: yerlesim sayilari mkfs ile, fsck, yedek baslik, GPT/MBR uzerinden
  `DiskSession.create_partition(fs_key="hfsplus")` ve `HfsAccess`.
