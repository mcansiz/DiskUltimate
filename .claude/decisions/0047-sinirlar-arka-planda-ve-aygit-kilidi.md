# 0047 — Boyutlandirma sinirlari arka planda; aygit G/C'si kilitli; acik disk tek satir

Tarih: 2026-09-28
Durum: kabul edildi (VM testi bekliyor)
Ilgili: ADR 0020 (donma yakalayici), ADR 0021 (ayni aygita iki yerden
dokunulmaz), ADR 0025 (salt okunur acilis), ADR 0046

## Sorun 1 — haritadaki tutamaklar donuyordu

Kullanici: "tutamaklar geri yukleme seridindeki gibi stabil degil, donmalar
oluyor". Donma raporlari (`.claude/logs/freeze/freeze-20260928-1623*`,
`-1624*`) nedeni gosterdi — **her suruklemede iki donma**:

1. basinca `map.handle_limits` -> `fs_resize_info_for` -> `_ntfs_info` ->
   `_bitmap_usage`: **1494 / 1598 ms**
2. birakinca `plan_resize` -> ayni tarama bastan

`_bitmap_usage` NTFS bitmap'ini **bit bit** Python dongusuyle geziyordu
(217 GB NTFS ~ 53 milyon kume). Geri yukleme seridi akiciydi cunku orada
sinirlar yedek onizlemesiyle arka planda bir kez hesaplaniyordu.

## Karar 1

- `bitmap_window_usage`: sayim bayt duzeyinde (`int.bit_count`, son dolu
  bit `bit_length`) — C'de. Eski yontemle esitligi t45'te rastgele veriyle
  sinanir.
- Sinirlar **secim aninda arka planda** (`_LimitsWorker`, QThread) bir kez
  hesaplanir; onbellek anahtari (aygit nesnesi, bolum, baslangic, boyut).
  Hazir olana kadar tutamak gri, suruklenmez; arayuz hic beklemez.
- `plan_resize(..., info=)`: birakista onbellekteki sinir verilir, tekrar
  okunmaz. Uygulama aninda (kuyruk) sinir yine bastan okunur — bayat sinir
  guvenlik acigi olamaz. Tam yenilemede onbellek temizlenir.
- "Bolumu boyutlandir" penceresi de onbellegi kullanir.

## Karar 2 — aygit G/C'si nesne basina kilitli

Arka plan hesabi arayuzun de okudugu **ayni tutamactan** okur. `seek`+`read`
iki cagridir; yarista yanlis sektor okunur. `BlockDevice.__init_subclass__`
alt siniflarin `read`/`write` yontemlerini otomatik olarak `RLock` ile sarar
(`_serialized`): ham goruntu, fiziksel disk, VHD/VDI/VMDK/QCOW2, `.dub`,
bolum gorunumu. Yeni aygit turu icin ayrica bir sey gerekmez. t45 dort is
parcacigiyla ayni tutamaktan okuyup sektor numarasini dogrular.

## Sorun 2 — acik fiziksel disk hedef listesinde iki kez

Uygulamada acik fiziksel disk hem "Acik goruntuler" hem "Fiziksel diskler"
altinda gorunuyordu. Kafa karistirmanin otesinde: "Fiziksel diskler" satiri
secilince `restore_to_physical` / `backup_physical` ayni aygita **ikinci
bir tutamac** aciyordu — ADR 0021'in yasakladigi sey.

## Karar 3

- "Acik goruntuler" yalnizca goruntu dosyalaridir. Her fiziksel disk bir kez,
  "Fiziksel diskler" altinda; uygulamada aciksa satir **o oturuma baglanir**
  ("uygulamada acik"), bolumleri secilebilir ve fiziksel disk korumalarini
  (bilgi eksik, yazma korumali, sistem diski ad onayi) tasir.
- Yazma oturumun kendi tutamacindan: `become_writable(confirm=True,
  allow_system=...)` sonra `restore_disk` / `restore_partition`.
- **Ayrica bulunan hata:** acik oturuma (goruntu dahil) geri yukleme
  `become_writable` cagirmiyordu; oturumlar salt okunur acildigi icin
  (ADR 0025) islem "salt okunur" hatasiyla duracakti. Artik cagriliyor.
