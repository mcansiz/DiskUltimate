# 0048 — Boyutlandirma planlanan yerlesime gore yapilir; ayni bolumun adimi yerinde guncellenir

Tarih: 2026-09-28
Durum: kabul edildi (Win10 VM'de sinandi)
Ilgili: ADR 0025 (kuyruk), ADR 0031 (plan onizlemesi), ADR 0033 (capa),
ADR 0034 (planlanan bos alan), ADR 0047

## Sorun

Kullanici: "bolumleri kucultebiliyorum, bos alan olusuyor ama bir bolumu
buyutemiyorum". Uc engel:

1. Tutamak penceresi `session.resize_window` — **diskteki** tablo. Kuyrukta
   acilan bos alan orada yok; komsu bolum o alana buyuyemiyordu.
2. Planda yeri degismis bolume tutamak hic verilmiyordu; kucultulen bolum
   geri buyutulemiyordu.
3. Kuyruga eklerken `plan_resize` de diskteki pencereyle dogruluyor ve
   "kapsayici alanin disinda" diye reddediyordu.

Uygulama tarafinda engel yoktu: her adim o anki gercek tabloya gore
yeniden dogrulanir, tasinan bolumun capasi sonraki adimlarda guncellenir
(`_follow_move`).

## Karar

- `planview.planned_window`: bolumun planlanan komsulari arasindaki pencere.
  Tutamaklar, "Bolumu boyutlandir" penceresi ve kuyruga ekleme dogrulamasi
  (`plan_resize(window=)`) bunu kullanir. Uygulama aninda `window` verilmez.
- Bolumun bekleyen boyutlandirmasi varsa (`OperationQueue.find_resize`) yeni
  adim eklenmez, **ayni sirada** degistirilir (`replace`): sona eklenseydi
  o adimin actigi alana dayanan komsu adim sirasini kaybederdi. Diskteki
  boyutuna geri cekilirse adim kaldirilir.
- "Diskteki hali" gosterilirken kuyruk doluysa tutamak verilmez (planla
  celisen konumdan baslardi). Mantiksal/genisletilmis bolumler disk
  penceresinde kalir.
