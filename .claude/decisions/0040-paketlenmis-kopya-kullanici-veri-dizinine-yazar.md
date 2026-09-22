# 0040 — Paketlenmis kopya kullanicinin veri dizinine yazar

Tarih: 2026-09-21
Durum: kabul edildi

## Sorun

`paths.PROJECT_ROOT` `__file__` uzerinden hesaplanir. Kaynaktan calisirken
dogrudur (CLAUDE.md: uretilen her sey proje dizininde kalir), ama
**PyInstaller onefile** kopyada `__file__` gecici cikartma dizinini
(`<sistem gecici dizini>/_MEIxxxx/...`) gosterir. Uc dizin yukari cikilinca
proje koku sistemin gecici dizini cikiyordu.

Ana makinede olculdu: paketlenmis kopya tanilama gunlugunu sistemin gecici
dizini altindaki `.claude/logs/` klasorune yazdi. Sonuclari:

- gunlukler uygulamanin disinda, her acilista silinebilen bir yerde duruyor —
  "kapandi, acilmadi" turu bir sikayette bakilacak yer yok;
- **root olarak** calisan kopya ayni yere yazinca dosyalarin sahibi degisiyor,
  normal kullanicinin kopyasi kendi gunlugunu yazamaz hale geliyor;
- gecici calisma alani (`scratch`) da oraya dusuyor, yani diskte kalan artik
  proje disinda birikiyor.

## Karar

`paths` artik paketlenmis kopyayi ayirir (`sys.frozen`):

- kaynaktan calisirken: proje dizini — degisiklik yok;
- paketlenmis kopyada: `core.platform.user_data_dir()` —
  Linux `$XDG_STATE_HOME` (yoksa `~/.local/state`), Windows `%LOCALAPPDATA%`,
  macOS `~/Library/Application Support`, hepsinin altinda `DiskUltimate/`.

`LOG_DIR` sabiti yerine `log_root()` islevi kullanilir; `diagnostics` ve
arayuz bunu cagirir. `DISKULTIMATE_LOG_DIR` ustunlugunu korur ve yetkili
kopyaya da gecirilir — boylece root kopyasinin gunlugu kullanicinin
gunlukleriyle **ayni klasorde** olur (ADR 0039).

Isletim sistemi farki yine tek yerde: yol secimi `core/platform.py` icindedir,
`paths` yalnizca cagirir (`tests.platform_check`: 0 bulgu).
