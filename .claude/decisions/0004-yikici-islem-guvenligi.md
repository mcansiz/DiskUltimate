# ADR 0004 — Yikici islem guvenligi

**Tarih:** 2026-09-13 · **Durum:** Kabul edildi

## Baglam
Bicimlendirme, bolum silme ve tablo yazma geri alinamaz islemlerdir.

## Kurallar
1. **Hedef daraltma.** Kod yalnizca kullanicinin actigi `.img` dosyasina yazar;
   blok aygit yolu kabul eden hicbir arayuz yok.
2. **Onay zorunlu.** Bicimlendirme, bolum silme, tablo olusturma/silme ve goruntu
   kucultme islemlerinde ne kaybedilecegini acikca yazan onay diyalogu gosterilir.
3. **Sinir denetimi.** `DiskImage.read/write` ve `PartitionView.read/write` her
   cagride goruntu/bolum sinirini denetler; tasan islem hata verir.
4. **Atomiklik.** Bicimlendirme basarisiz olursa bolum tablodan geri alinir
   (`session.create_partition`), tabloda hayalet giris kalmaz.
5. **Salt okunur geri cekilme.** Dosya yazma izni yoksa goruntu salt okunur acilir
   ve tum degistirme eylemleri arayuzde pasiflesir.
6. **Izlenebilirlik.** Her islem `.claude/logs/app-<tarih>.log` dosyasina ve
   arayuzdeki "Islem Gunlugu" sekmesine yazilir.

## Test
`t08_hata_geri_alma` 4. kurali; `t01_image_temel` 3. kurali koruma altina alir.
