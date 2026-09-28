# 0046 — Secilebilir ikon setleri; ucuncu taraf paketler yol verisi olarak gomulur

Tarih: 2026-09-28
Durum: kabul edildi (VM testi bekliyor)
Ilgili: ADR 0025 (ikonlar QPainter ile cizilir), ADR 0044 (uygulama ikonu dosyadan)

## Sorun

Kullanici ikon katalogunu (`.claude/logs/ikonlar/`) inceledi ve sunlari istedi:
- **Araclar > Ikon seti** menusunden sekiz setten biri secilebilsin: Klasik,
  Kutucuk, Cizgi, Tabler, Phosphor, Lucide, Bootstrap, Material.
- Isletim sistemi amblemleri **Simple Icons** olsun.

ADR 0025'in gerekcesi hala gecerli: `PyQt5.QtSvg` her dagitimda yok ve
calisma zamani bagimliligi eklenmez. Paketler ise SVG olarak dagitilir.

## Karar

1. **Paketler yol verisi olarak gomulur.** `tools/iconpacks.py` (gelistirme
   araci) sabitlenmis paket surumlerini jsDelivr'den indirir, `circle/rect/
   line/...` ogelerini `path` verisine cevirir ve `ui/iconpacks/<paket>.py`
   modullerini uretir. Her modul paketin **lisans metnini** tasir.
2. **Calisma zamani SVG okumaz**: `ui/svgpath.py` yol verisini saf Python'da
   `QPainterPath`e cevirir (M L H V C S Q T A Z; yay -> kubik Bezier).
   QtSvg ile 237 ikonda karsilastirildi: en kotu piksel farki %0.3.
3. **Canli ikon motoru**: `icons.icon()` bir `QIconEngine` dondurur; her
   boyamada etkin seti okur. Set degisince yalnizca yeniden boyama yeterli —
   ~100 cagri yerinin hicbiri degismedi.
4. **Kutucuk ve Cizgi aileleri** ayni sembol geometrisini paylasir
   (`ui/iconsets.py`); katalogdaki onizlemeyle birebir aynidir.
5. Pakette karsiligi olmayan ikon **klasik setten** cizilir; bos ikon yok.
6. Secim `settings` icinde `icon_set` anahtariyla saklanir.
7. Lisans bildirimi: **Yardim > Ucuncu taraf lisanslari**.

## Isletim sistemi amblemleri

Linux ve macOS Simple Icons'tan. **Windows Simple Icons'ta yok**: Simple
Icons butun Microsoft logolarini Microsoft hukuk ekibinin talebiyle
v13.0.0'da kaldirdi (simple-icons#11236; 12.4.0'da var, 12.5.0'dan itibaren
yok — dogrulandi). Eski surumden almak o talebi dolanmak olurdu; Windows
icin uygulamanin kendi geometrik amblemi (dort kare) kullanilir.

Katalog onizlemesinde Windows gorunuyordu: jsDelivr'in `@latest` etiketi eski
bir surumu gosteriyordu. Arac bu yuzden **surum sabitler**.

## Lisanslar

Tabler MIT, Phosphor MIT, Lucide ISC, Bootstrap MIT, Material Symbols
Apache-2.0, Simple Icons CC0. Remix Icon v1.0 lisansi logo/marka kullanimini
yasakliyor — alinmadi. Font Awesome (CC BY) atif gerektirdigi icin alinmadi.
Isletim sistemi logolari sahiplerinin ticari markasidir; yalnizca diski
tanitmak icin gosterilir.
