# ADR 0012 — Qt'nin Windows'taki iki davranis farki

**Tarih:** 2026-09-13 · **Durum:** Uygulandi · **Dogrulama:** Win10 VM'de olculdu

Arayuz ilk kez Windows uzerinde calistirildiginda iki sorun ortaya cikti.

## 1. `offscreen` eklentisi Windows'ta font yuklemiyor

Ilk kosumda uretilen ekran goruntulerinde **duzen dogruydu ama hicbir metin
gorunmuyordu.** Olcum:

| Platform eklentisi | Font ailesi | `drawText` sonrasi koyu piksel |
|---|---|---|
| `offscreen` (Windows) | **0** | **0** — metin cizilmedi |
| `windows` (gercek) | 114 | 106 — metin cizildi |
| `xcb` (Linux) | 373 | — |

Qt su uyariyi veriyordu: *"Qt no longer ships fonts... switch to fontconfig"*.
Linux'ta fontconfig sistem fontlarini sagladigi icin `offscreen` calisir; Windows'ta
eklenti sistem font kaynagina baglanmaz.

**Karar:** `tests/ui_smoke.py` platform eklentisini isletim sistemine gore secer —
Windows'ta `windows`, digerlerinde `offscreen`. Ayrica kosum sonunda yuklenen font
ailesi sayisi yazdirilir ve sifirsa "goruntulerde metin gorunmez" uyarisi verilir.

Bu yalnizca **duman testini** etkiler; uygulama normal calistirildiginda zaten
gercek platform eklentisini kullanir.

## 2. Stil sayfasindaki `font-weight` sekme metnini kirpiyor

`QTabBar::tab:selected { font-weight: 600 }` kurali Windows'ta sekme basliklarini
kirpti: *"Dosya Gezgini" → "osya Gezgin"*. Qt sekme genisligini **normal** fontla
olcup metni **kalin** cizdigi icin fark tasiyor. Linux'ta font metrikleri daha dar
oldugundan sorun gorunmuyordu.

Once "tum sekmeler kalin olsun" denendi — **daha kotu oldu**, bu kez dort sekmenin
hepsi kirpildi.

**Karar:** Sekmelerde `font-weight` hic kullanilmaz. Secili sekme yalnizca zemin ve
renkle vurgulanir; `min-width: 96px` metnin sigmasini garantiler. Her iki platformda
dogrulandi.

## Ders
Bir stil sayfasi kurali yalnizca **cizimi** degistiriyor gorunse de, widget'in
boyut hesabini degistirmiyorsa kirpmaya yol acar. Tek bir platformda gozle test
etmek bunu yakalamaz.
