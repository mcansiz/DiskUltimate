# 0044 — Uygulama ikonu dosyadan gelir (islem ikonlari cizilmeye devam eder)

Tarih: 2026-09-21
Durum: kabul edildi
Ilgili: ADR 0025 (ikonlar QPainter ile cizilir), ADR 0027 (ceviri sozlukleri
paket verisi olarak tasinir)

## Sorun

Uygulamanin kendi ikonu yoktu: pencerede, gorev cubugunda ve `dist/` altindaki
exe'de isletim sisteminin varsayilan ikonu gorunuyordu. `DiskUltimate.spec`
bunu yorum satirinda belirtmisti ("Ikon yok").

Kullanici bir `favicon.ico` verdi (SSD + anahtar + yenileme oklari).

## Karar

Uygulamanin **marka ikonu** dosyadan okunur; `ui/icons.py` icindeki **islem
ikonlari** (bicimlendir, boyutlandir, onyukleme bayragi...) cizilmeye devam eder.
Bu bir geri adim degil, iki farkli seyin ayrilmasidir:

| | Islem ikonlari | Uygulama ikonu |
|---|---|---|
| Kaynak | Kod (QPainter) | Sanat eseri (`.ico`) |
| Kac tane | 50+ | 1 |
| Kim kullanir | Yalnizca arayuz | Arayuz **ve paketleyici** |
| Neden boyle | Platform ikon temasina guvenilemez; SVG icin `QtSvg` her dagitimda yok | Kodla yeniden uretilemez; exe kaynagi / macOS paketi / masaustu girdisi **dosya** ister, orada QPainter calismaz |

Calisma zamani bagimliligi eklenmez: `.ico` okuyucusu Qt'nin kendi
`imageformats` eklentisindedir. SVG'den farki budur — `PyQt5.QtSvg` ayri bir
pakettir ve Ubuntu 24.04'te kurulu degildi (olculdu, ADR 0025).

## Kaynak dosyada bulunan kusur

Verilen `favicon.ico`'nun **alti boyutu da tamamen opakti**: saydamlik yerine
dama deseni (128/256 px) ve duz acik gri `#f3f3f3` (16..64 px) piksel olarak
gomulmustu — birisi saydamlik onizlemesini duzlestirerek disa aktarmis.

Oldugu gibi kullanilsaydi gorev cubugunda, baslik cubugunda ve koyu temada
ikonun arkasinda **acik gri bir kare** gorunurdu. Sessiz bir kusurdur: dosya
gecerlidir, hicbir sey hata vermez, yalnizca cirkin gorunur.

Bu yuzden arka plan geri kazandirilir: kenarlardan tasma dolgusu (flood fill)
ile **yalnizca kenara bagli** acik pikseller silinir. Ikonun icindeki beyaz
("SSD" yazisi, metal parlama) kenara bagli olmadigi icin korunur; sinir
pikselleri kismi alfa alir, boylece halka (halo) kalmaz.

## Yerlesim

```
assets/branding/favicon.ico      kullanicinin verdigi ozgun dosya (dokunulmaz)
assets/branding/uret_ikon.py     uretici — tek komutla asagidakilerin hepsi
src/diskultimate/ui/resources/
    app-icon.ico                 calisma zamani + Windows exe (16..256, saydam)
    app-icon-<n>.png             Linux masaustu girdisi / hicolor kurulumu
    app-icon.icns                macOS paketi
```

Uretilenler depoya **girer**: paketleme makinesinde PyQt5 bulunmayabilir ve
uretim kaynak dosyaya baglidir. Kaynak degisirse tek komutla yenilenir:

```
python3 assets/branding/uret_ikon.py assets/branding/favicon.ico \
        src/diskultimate/ui/resources
```

Harici arac kullanilmaz (ImageMagick / iconutil / Pillow yok) — `.ico` yazici
ve `.icns` yazici arac icinde, ~60 satir. Gerekce: bu araclar uc platformda da
kurulu degildir ve "bagimlilik yok" kurali gelistirme araclarini kapsamasa da
paketleme makinesini zorlastirmanin karsiligi yok.

## Yol cozumu

`ui/appicon.py` paketin yanindaki `resources/` klasorune bakar — `i18n`
sozlukleriyle **ayni yontem**. PyInstaller paketinde de ayni goreli yerde
durdugu icin ("paketlenmis miyiz" diye) ayri bir dal gerekmez.

Ikon uygulama duzeyinde verilir (`app.setWindowIcon`), pencere basina degil:
butun ust duzey pencereler ve diyaloglar miras alir.

## Sinanmasi

`tests/ui_smoke.py` uc sey denetler: dosya yerinde mi, butun boyutlar var mi
(eksik boyut Qt'ye olcekletir ve bulaniklastirir), ve **saydam mi**. Ucuncusu
yukaridaki kusurun tekrarini yakalar — ozgun `favicon.ico` bu denetimden
gecemiyordu (olculdu: 32 px'te 0 saydam piksel).
