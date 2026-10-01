# 0060 — HFS+ / HFSX salt okuma (saf Python)

Tarih: 2026-09-29
Durum: **uygulandi** — ana makinede goruntu dosyalariyla dogrulandi.
**macOS'ta sinanmadi** (ortam yok).
Ilgili: [0053](0053-sifreli-ve-kapsayici-birim-tanima.md) (HFS+ taninmasi),
`.claude/docs/dosya-sistemi-genisletme.md` Asama 2.

## Karar

`core/hfsplus.py` — Apple TN1150'ye gore:

* Birim basligi (1024), catal (ForkData) + kapsam tasmasi B-agaci,
  katalog B-agaci (ara/yaprak dugum, degisken uzunluklu indeks anahtari).
* **Listeleme ad karsilastirmasi gerektirmez:** bir klasorun cocuklari
  yaprak duzeyinde ardisiktir; (kimlik, "") anahtarina inilip ust kimlik
  degisene kadar yaprak zinciri izlenir. Boylece Apple'in katlama tablosunu
  B-agaci inisinde birebir uygulamak gerekmez.
* Ad aramasi listelenen cocuklar uzerinde: NFD + (HFS+) `casefold`; HFSX
  (ya da katalog karsilastirma turu 0xBC) buyuk/kucuk harf duyarli.
  Gosterim adi NFC, adin icindeki '/' ':' olarak gosterilir (Finder gibi).
* Sabit bag (dosya "iNode<n>", klasor "dir_<n>"), sembolik bag, gizli
  bayragi (Finder kIsInvisible veya '.' ile baslayan ad); ozel sabit bag
  klasorleri kokte listelenmez.
* decmpfs zlib (tur 1/3/4); LZVN/LZFSE icin acik hata.
* Eski HFS sarmalayicisina gomulu HFS+ (fsdetect de "BD" icinde "H+" gorunce
  HFS+ bildirir).
* Gunluk yeniden oynatilmaz; kirli gunluk `journal_dirty` ile bildirilir.
* `filesystem.HfsAccess` — salt okunur; `write_reason` yazma asamasina kadar
  nedenini soyler.

## Neden bu test verisi

Ana makinede cekirdek baglamasi yok; HFS+'a icerik yazan kullanici alani
araci gerekiyordu:

* **libhfsp (`hfsplus` paketi, hpmount/hpcopy)** — `hpcopy` yalnizca birimden
  disari kopyalar; `hpmount` modern mkfs.hfsplus biriminde %100 CPU'da
  takildi (2002 tarihli). Elendi.
* **xorriso `-hfsplus`** (libisofs) — ISO icine APM bolumunde gercek bir
  HFS+ agaci yazar: bagimsiz bir uygulama, icerik tamamen kontrol altinda.
  Secildi.
* **mkfs.hfsplus** (hfsprogs, kullanici alanina acildi) — gunluklu HFS+,
  HFSX. `-w` (sarmalayici) `/usr/share/misc/hfsbootdata` istedigi icin
  sarmalayici sentetik MDB ile sinandi.
* 7z HFS+ okuyabiliyor; capraz karsilastirma icin kullanilabilir (su an
  kaynak dizinle birebir karsilastirma yeterli).

## Dogrulama

* 6000 dosya / 40 klasor / 3 duzeyli katalog (xorriso): hepsi kaynakla
  birebir, 1,4 sn.
* t59: 1504 dosya (3 duzey), sembolik bag, Turkce ad (NFD/NFC, buyuk/kucuk
  harf; ı ile I ayri), GPT bolumunde uctan uca `DiskSession.filesystem`,
  disari aktarma, gunluklu birim (gizli .journal dosyalari), HFSX,
  sentetik sarmalayici.

## Sinanmayanlar (acikca)

* macOS'un urettigi birim (sabit bag, decmpfs, kapsam tasmasi): uretecek
  arac yok. Sabit bag/decmpfs kodu spesifikasyondan yazildi; HFS+ yazma
  maddesi bu yapilari uretip fsck.hfsplus ile sinayacak.
* APM (Apple Partition Map) bolum tablosu okunmuyor: Mac diskleri GPT
  kullanir; APM yalnizca PowerPC Mac ve hibrit ISO/DMG'de.
