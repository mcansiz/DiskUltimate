# ADR 0027 — Cok dilli arayuz (kaynak metin anahtar, saf Python sozluk)

**Tarih:** 2026-09-17
**Durum:** Kabul edildi
**Ilgili:** ADR 0013 (sistem temasi), ADR 0025 (bekleyen islem kuyrugu)

## Baglam

Kullanici: *"projeye coklu dil secenegi ekleyelim."*

Arayuz bastan sona Turkce yazilmisti (CLAUDE.md: *"Turkce arayuz metni,
Ingilizce kod/degisken adi"*). Metin yalnizca `ui/` icinde degil, `core/`
icinde de uretiliyordu: bolum turu adlari, silme yontemleri, ilerleme
bildirimleri ve **butun hata mesajlari**. Arayuzu tek basina cevirmek, yarisi
Ingilizce yarisi Turkce bir pencere uretirdi — en cok goruleni olan hata
kutusu Turkce kalirdi.

## Karar

Cevirinin **kaynak dili Turkce**dir. Kodun icindeki metin hem gosterilecek
yazidir hem de ceviri anahtaridir:

```python
from ..i18n import tr

tr("Bolum tablosunu sil")            # -> "Delete partition table"
tr("Bolum {} bicimlendir", index)    # -> "Format partition 3"
```

Ceviriler `src/diskultimate/i18n/catalogs/<kod>.json` icinde
`{"kaynak metin": "ceviri"}` bicimindedir. Ilk surumde **tr / en / de**.

### Neden anahtar degil de kaynak metin

`tr("MAIN_WINDOW_TITLE")` bicimindeki anahtarlarla:

- ceviri dosyasi eksikse arayuzde anahtar gorunur,
- kodu okuyan, metnin ne dedigini bilmez,
- ayni metin iki anahtara boluner ve ceviriler ayrisir.

Kaynak metin anahtar olunca sozluk **hic yuklenmese bile** uygulama dogru
calisir; en kotu durum "ceviri yok, Turkce gorunuyor"dur.

### Neden Qt Linguist (.ts/.qm) degil

1. `QTranslator` yalnizca `QObject.tr()` cagrilarini cevirir. Metnin buyuk
   bolumu `core/` icinde uretilir ve `core/` **PyQt import etmez** (CLAUDE.md
   katman kurali). Cekirdegi Qt'ye baglamak, bu kurali kirmak demekti.
2. `.qm` uretmek Qt'nin `lrelease` aracini gerektirir; proje "harici
   bagimlilik yok" diyor ve `lrelease` PyQt5 tekerleginde gelmiyor.
3. JSON sozluk elle de duzenlenebilir; ceviri katkisi icin ozel arac gerekmez.

Bedeli: cogul ekleri (plural forms) icin gettext'in `ngettext` altyapisi yok.
Ingilizce ve Almanca icin metinler "{} partitions" bicimde yazildi; gercek bir
cogul sorunu cikarsa `tr()` icine plural destegi eklenecek.

## Modul duzeyindeki metinler — `mark()`

Bazi metinler uygulama acilirken, **dil secilmeden once** uretilir: bolum turu
tablolari (`MBR_TYPES`), silme yontemleri (`WIPE_METHODS`), imza adlari
(`SIGNATURES`), islem turleri (`operations.KINDS`). Bunlari tanim yerinde
cevirmek ise yaramaz — metin bir kez uretilir ve dil degisince guncellenmez.

gettext'in `N_()` yaklasimi kullanildi: kaynakta **isaretle**, gosterirken
**cevir**.

```python
WipeMethod("zero", mark("Sifirla (1 gecis)"), ...)   # tanim: cevrilmez
combo.addItem(tr(method.label), method.key)          # gosterim: cevrilir
```

`mark()` metni oldugu gibi dondurur; tek isi ceviri toplayicisina gorunmektir.

## Bekleyen islem basliklari gec cevrilir

`Operation` artik hazir metin saklamaz; **kalip + argumanlari** saklar ve
`title` okundugunda cevirir:

```python
Operation("format", title_text=mark("Bolum {} bicimlendir"), title_args=(index,))
```

Boylece kuyruk doldurulduktan sonra dil degistirilirse bekleyen adimlarin
basliklari da yeni dile doner. Hazir metin saklansaydi eski dilde kalirlardi.

## Dil degisimi: yeniden baslatma YOK

Cogu uygulama dil degisince yeniden baslatilmayi ister. Burada bu kabul
edilemez: acik bir fiziksel disk ve **bekleyen islem kuyrugu** olabilir;
yeniden baslatmak ikisini de kaybettirir (ADR 0025).

Bunun yerine metinler **yerinde** yenilenir:

- `i18n.add_listener(...)` dil degisimini ana pencereye bildirir,
- `MainWindow.retranslate()` menuleri, arac cubugunu, sekmeleri, agaci, durum
  cubugunu ve alt bilesenleri (`FileBrowser`, `PartitionTableWidget`,
  `HexViewer`) yeniden yazar,
- diyaloglar zaten her acilista kuruldugu icin kendiliginden dogru dilde gelir.

Eylem metinlerinin **tek kaynagi** `MainWindow._retranslate_actions()`tir:
`_build_actions()` eylemi bos metinle kurar, metni bu islev doldurur. Yeni bir
eylem eklenip buraya satiri yazilmazsa `tests/i18n_check.py` bunu **hata olarak
bildirir**.

## Metinden karar cikarmak yasak

Iki yerde kod, uretilen **metnin icinde arama** yapiyordu:

```python
kilit = "kilitlenmis" in reason          # image.DiskImage nedeni
"[YIKICI]" in line                       # kuyruk adim listesi
```

Metin cevrilince bu aramalar bos doner ve mantik sessizce bozulur. Ikisi de
veriye tasindi: `DiskImage.readonly_locked` bayragi ve
`OperationQueue.describe_rows()` -> `(metin, yikici mi)` ciftleri. **Kural:
gorunen metin bir karar girdisi degildir.**

## Dil secimi ve saklanmasi

Sira: `DISKULTIMATE_LANG` > kayitli secim > isletim sisteminin dili > Turkce.

- Kayitli secim `core/settings.py` ile isletim sisteminin kullanici ayar
  klasorune yazilir (`platform.config_dir()`), proje dizinine degil: uygulama
  salt okunur bir klasorden calistirilabilir.
- Isletim sisteminin dili `platform.system_language()` ile okunur (Windows'ta
  `GetUserDefaultUILanguage`, POSIX'te `LANG`/`LC_*`). `locale.getdefaultlocale()`
  kullanilmadi: 3.11'den beri kullanimdan kaldirildi ve Windows'ta **arayuz**
  dilini degil bolge ayarini verir.
- Acilisdaki secim **kaydedilmez**; yalnizca kullanici menuden secerse yazilir.
  Yoksa sistemin dili degistiginde uygulama eski dilde kalirdi.
- Dili olmayan bir sistemde (orn. Fransizca) kaynak dile dusulur: yarim
  cevrilmis arayuz gostermektense Turkce gostermek daha durusttur.

## Testler

`python3 -m tests.i18n_check` (yeni):

| Denetim | Dogruladigi |
|---|---|
| eksik ceviri | Kaynaktaki her `tr`/`mark` metni her dil dosyasinda var |
| bayat giris | Dil dosyasinda kaynakta olmayan metin kalmamis |
| yer tutucu | `{}` sayisi ve `{ad}` adlari kaynakla ceviride ayni |
| HTML etiketi | `<b>`, `<br>` gibi etiketler ceviride korunmus |
| eylem metinleri | Her `self.act_*` icin `_retranslate_actions()` satiri var |
| calisma zamani | Sozluk yuklenince `tr()` ceviriyor, kaynak dile donunce geri geliyor |

`tests/ui_smoke.py` icine `dil_denetimi()` eklendi: her dile gecilir, arac
cubugu/sekme/menu/sutun metinlerinin **gercekten degistigi** dogrulanir, ekran
goruntusu alinir ve Turkce'ye donulur.

Cekirdek testleri (`tests.run_all`) Turkce mesaj iceriklerine bakar; bu yuzden
`i18n` acilista **kendiliginden** dil secmez — secim yalnizca `main.py` icinde
`i18n.initialize()` ile yapilir. Testler kaynak dilde kosar.

## Yeni bir dil eklemek

```bash
python3 -m tests.i18n_check --write fr     # catalogs/fr.json iskeletini uretir
# fr.json doldurulur
# i18n/__init__.py -> LANGUAGE_NAMES icine "fr": "Français"
python3 -m tests.i18n_check                # eksik/bayat/yer tutucu denetimi
```

Menu `catalogs/` klasorunu okur; kodda baska bir degisiklik gerekmez.

## Sonuclar

- Cevrilen metin sayisi: **1062** (arayuz + cekirdek hata mesajlari).
- Dil degisimi acik disk ve bekleyen kuyruk ile **kesintisiz** calisir.
- `core/` hala PyQt'siz; `i18n` saf Python.
- Paketlenmis (PyInstaller) surumde `i18n/catalogs/*.json` **veri dosyasi
  olarak eklenmelidir**; yoksa yalnizca Turkce calisir (uygulama coker degil).

## Ek (2026-09-17): sozde-yerellestirme bir kalite kapisi olarak

Bu kararin yapisal bir acigi vardi: `i18n_check` yalnizca **isaretlenmis**
metni gorur, hic sarilmamis olani bilemez. Nitekim karardan sonra eklenen
onyukleyici ozelliginde dort bosluk elle taramayla bulundu.

Cozum sektorun standardi: **sozde-yerellestirme**. `DISKULTIMATE_LANG=qps`
ile metin `[!Ɓǿŀŭḿŭ şīŀ···!]` bicimine girer; yer tutucular ve HTML
dokunulmadan kalir. Sarilmamis metin donusmedigi icin **gozle ve otomatik**
bulunur. `tests/ui_smoke.py -> sozde_denetimi()` bunu her kosumda denetler:
eylem, menu, sekme ve sutun metinleri sozde degilse test duser.

Sozde dil bir **arac**tir, gercek dil degil: dil menusunde gorunmez, ayar
dosyasina yazilmaz, yalnizca ortam degiskeniyle acilir.

Ayrintili degerlendirme (sektor karsilastirmasi, `.po` secenegi, olcumler):
`.claude/docs/i18n-raporu.md` bolum 12-13.
