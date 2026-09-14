# ADR 0009 — Kendi yedek bicimimiz: `.dub`

**Tarih:** 2026-09-13 · **Durum:** Kabul edildi

## Baglam
Bolum/disk yedegi gerekiyordu. Secenekler:
1. Ham kopya (`.img`) — basit ama bos alan kadar yer kaplar
2. `dd | gzip` — sikistirir ama rastgele erisim yok, kismi geri yukleme zor
3. DiskGenius `.pmf` — kapali bicim, uyumluluk kurulamaz
4. Kendi blok tabanli bicimimiz

## Karar
4. secenek: `.dub` — 512 baytlik baslik + blok indeksi + yalnizca dolu bloklar.
Ayrinti: [spec](../specs/dub.md)

## Gerekceler
- **Bos alan bedava.** Sifir bloklar indekste isaretlenir, dosyada yer kaplamaz.
  Olcum: 400 MB FAT32 bolumu (icinde ~5 MB veri) → **34 KB** yedek.
- **Blok basina sikistirma karari.** Sikisma kazanc saglamazsa blok ham yazilir;
  boylece zaten sikistirilmis veride dosya buyumez.
- **Baslik tek basina anlamli.** Geri yukleme oncesi kaynak boyut, dosya sistemi,
  etiket ve tarih kullaniciya gosterilip onay alinir; hedef kucukse islem reddedilir.
- **Geri yuklemede seyreklik korunur.** Sifir blok hedefte zaten sifirsa yazilmaz.

## Dogrulama
`tests.run_all::t11` — yedek al, bolumu kasitli boz, geri yukle, icerigi bayt bayt
karsilastir; ayrica yedek boyutunun kaynagin dortte birinden kucuk oldugunu denetler.
