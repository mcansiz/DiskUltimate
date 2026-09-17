# Coklu dil katmani — teknik rapor

**Tarih:** 2026-09-17
**Kapsam:** `src/diskultimate/i18n/`, `core/settings.py`, `tests/i18n_check.py`
ve bunlarin arayuz/cekirdek uzerindeki etkisi
**Karar kaydi:** `.claude/decisions/0027-cok-dilli-arayuz.md` (bu rapor onun
ayrintili gerekcesi ve olcumudur)

---

## 1. Ozet

Arayuz uc dilde calisiyor: **Turkce (kaynak), Ingilizce, Almanca**. Ceviri icin
hazir bir kutuphane (gettext, Qt Linguist, Babel) kullanilmadi; proje icine
**240 satirlik saf Python** bir katman yazildi ve sozlukler duz JSON tutuldu.

| Olcu | Deger |
|---|---|
| Cevrilebilir farkli metin | **1315** |
| Kaynaktaki `tr()` / `mark()` cagrisi | **1656** (ui 914, core 742) |
| Sozluk dosyasi | `en.json` 91 KB, `de.json` 100 KB |
| Bellekte sozluk | ~215–227 KB (dil basina, yalnizca etkin dil yuklu) |
| Sozluk yukleme | 0.5–0.8 ms |
| `tr()` maliyeti | ~125 ns (ceviri etkin), ~395 ns (bicimlendirmeli) |
| Tipik pencere kurulumu (1000 metin) | **0.23 ms** |
| Ceviri katmani kodu | `i18n/__init__.py` 240 satir + `settings.py` 76 satir |
| Denetim | `tests/i18n_check.py` 267 satir, 6 denetim |

Olcumler Linux misafirinde (Mint 22.3, Python 3.12.3) alindi; Windows
misafirinde ayni denetimler **BASARILI** dondu.

---

## 2. Sorunun sekli

"Arayuzu cevirmek" bu projede yalnizca menuleri cevirmek degil. Metnin
**%45'i `core/` icinde uretiliyor**:

| Nerede | Ne | Ornek |
|---|---|---|
| `core/*.py` (742 cagri) | hata mesajlari (`raise`), ilerleme bildirimleri, ozet tablolari, bolum turu adlari, silme yontemleri, imza adlari, islem kuyrugu baslikklari | `"Bolum {} 2 TiB sinirinin otesinde bitiyor; MBR bu yerlesimi tasiyamaz"` |
| `ui/*.py` (914 cagri) | menu, arac cubugu, diyalog, tablo basligi, durum cubugu, ipuclari | `"Bekleyen islemleri sirayla uygular..."` |

Kullanicinin en cok gordugu metin sinifi **hata kutusudur** ve onun icerigi
neredeyse tamamen `core/` kaynaklidir. Yalnizca `ui/` cevrilseydi sonuc "menusu
Ingilizce, hatasi Turkce" bir uygulama olurdu — cevirmemekten daha kotu.

Bu olcu, secilecek yolu dogrudan belirledi: **cozum `core/` icinde de
kullanilabilmeliydi**, ve `core/` PyQt import etmiyor (CLAUDE.md katman kurali).

---

## 3. Degerlendirilen yollar

### 3.1 Qt Linguist — `.ts` / `.qm` + `QTranslator`

Qt'nin kendi yolu. `self.tr("...")` cagrilari `pylupdate5` ile `.ts`
dosyasina toplanir, Linguist ile cevrilir, `lrelease` ile ikili `.qm` uretilir,
`QTranslator` uygulamaya yuklenir.

**Neden elendi:**

1. **`QTranslator` yalnizca `QObject.tr()` zincirini cevirir.** `core/` icindeki
   1656 cagrinin 742'si oradan gecmiyor; cevirmek icin `core/` siniflarini
   `QObject` turevine cevirmek ya da Qt'yi cekirdege sokmak gerekirdi. Ikisi de
   CLAUDE.md'deki "core saf Python, PyQt import etmez" kuralini kirar ve
   cekirdek testlerini (28 test, PyQt'siz kosuyor) Qt'ye bagimli hale getirir.
2. **`lrelease` harici arac.** PyQt5 tekerleginde gelmiyor; `pyqt5-tools` veya
   sistem Qt paketi gerekir. Proje "harici bagimlilik yok" diyor ve Windows
   misafirinde kurulum **cevrimdisi** yapiliyor — derleme adimi gerektiren bir
   ceviri bicimi bu akisi zorlastirir.
3. **Ikili bicim.** `.qm` elle okunamaz/duzenlenemez; bir yazim hatasini
   duzeltmek icin arac zinciri gerekir. `git diff` ile ceviri degisikligi
   gorunmez.
4. Kazanc tarafinda sundugu **cogul eki (plural) ve baglam (context)** destegi
   gercek bir arti — bkz. bolum 8, bugun eksigimiz budur.

### 3.2 GNU gettext — `.po` / `.mo` (Python stdlib `gettext`)

Standart kutuphanede oldugu icin "harici bagimlilik" sorunu yok; `core/` icinde
de calisir.

**Neden elendi:**

1. **`.mo` derlemesi gerekir.** `msgfmt` (gettext araclari) ya da `babel`
   kurulu olmali. `msgfmt.py` stdlib'de **degil** (yalnizca CPython kaynak
   agacindaki `Tools/i18n` altinda). Yani yine bir derleme adimi ve ya harici
   arac ya da depoya kopyalanmis bir yardimci betik.
2. **Dosya bicimi ve arac yuku.** `.po` bicimi guclu ama bu boyutta bir proje
   icin (1315 metin, iki dil) getirdigi disiplin, getirdigi ek adimlara degmedi.
3. **Dil degisimi.** `gettext.translation(...).install()` surec genelinde `_()`
   kurar; **calisirken** dil degistirmek icin yeni bir `Translations` nesnesi
   yuklenip butun metinlerin yeniden okunmasi gerekir — yani bizim
   `retranslate()` mimarimiz yine gerekliydi. gettext bu isi kolaylastirmiyor.

Elde edilen: gettext'in **tek gercek ustunlugu** cogul ekleri ve `pgettext`
baglamiydi. Ikisi de bugun kullanilmiyor (bkz. 8.1, 8.2); gerekirse bizim
katman bunlari kendi icinde de saglayabilir (bkz. 10).

### 3.3 Harici kutuphane (Babel, Fluent, i18n paketleri)

Tek satirla elenir: **projenin ilan ettigi kural "harici bagimlilik yok"**
(`requirements.txt` yalnizca PyQt5). Bir disk aracinin ceviri icin ek paket
istemesi, cevrimdisi kurulum akisini (Windows misafiri) da bozardi.

### 3.4 Kendi JSON sozlugu — **secilen**

```python
from ..i18n import tr
tr("Bolum tablosunu sil")             # -> "Delete partition table"
tr("Bolum {} bicimlendir", index)     # -> "Format partition 3"
```

- Kaynak metnin kendisi anahtardir.
- Sozluk: `i18n/catalogs/<kod>.json`, `{"kaynak metin": "ceviri"}`.
- Saf Python, Qt'siz; `core/` de kullanabiliyor.
- Derleme adimi yok; `git diff` ceviri degisikligini satir satir gosteriyor.
- Kod 240 satir; bakim yuku dusuk ve tamamen bizde.

### 3.5 Karsilastirma

| | Qt Linguist | gettext | Kendi JSON'umuz |
|---|---|---|---|
| `core/` icinde calisir | ✗ (Qt gerekir) | ✓ | ✓ |
| Harici arac gerekir | ✓ `lrelease` | ✓ `msgfmt` | ✗ |
| Derleme adimi | ✓ | ✓ | ✗ |
| Elle okunur/duzenlenir | ✗ (`.qm` ikili) | kismen (`.po`) | ✓ (JSON) |
| `git diff` anlamli | ✗ | ✓ | ✓ |
| Cogul eki destegi | ✓ | ✓ | ✗ (bkz. 8.1) |
| Baglam (ayni metin, iki anlam) | ✓ | ✓ | ✗ (bkz. 8.2) |
| Calisirken dil degisimi | elle | elle | elle (ayni is) |
| Ceviri arac ekosistemi | zengin | cok zengin | yok |

---

## 4. Kaynak metnin anahtar olmasi

Klasik alternatif, sembolik anahtardir: `tr("MAIN_WINDOW_TITLE")`.

Kaynak metni anahtar yapmanin **kazanci**:

- Sozluk yuklenmese, bozulsa veya bir giris eksik olsa bile ekranda **anlamli
  Turkce** kalir; hicbir yerde `MAIN_WINDOW_TITLE` gorunmez. Bir disk aracinda
  "ne yaptigini anlamadigin dugmeye basmak" kabul edilemez.
- Kodu okuyan, metnin ne dedigini gorur; ayri bir sozluge bakmak gerekmez.
- Ayni metin iki yerde geciyorsa **kendiliginden** ayni anahtara duser; ceviri
  bir kez yazilir (1656 cagri -> 1315 farkli metin; yani 341 tekrar tek
  girise indi).

**Bedeli:**

- Kaynak metindeki bir yazim duzeltmesi anahtari degistirir ve o girisin
  cevirisi **duser** (bkz. 8.5). Denetim bunu "eksik ceviri" olarak bildirir,
  sessizce kaybolmaz.
- Ayni Turkce metnin iki yerde farkli cevrilmesi gerekiyorsa ayrim yapilamaz
  (bkz. 8.2).

---

## 5. Tasarimin ayrintisi

### 5.1 API

| Islev | Ne yapar | Nerede kullanilir |
|---|---|---|
| `tr(metin, *args, **kw)` | Cevirir, gerekirse `str.format` uygular | Her yer |
| `mark(metin)` | **Cevirmez**; metni sozluge girmesi icin isaretler | Modul duzeyi veriler |
| `translate(metin)` | Bicimlendirmesiz ceviri | Hazir uretilmis metin |
| `set_language(kod, remember=)` | Dili degistirir, dinleyicileri uyarir | Dil menusu |
| `initialize()` | Acilista dili belirler (kaydetmeden) | Yalnizca `main.py` |
| `available_languages()` | `[(kod, dogal ad)]` | Dil menusu |
| `add_listener(fn)` | Dil degisiminde cagrilir | `MainWindow` |

`tr()` bozuk ceviriye karsi dayaniklidir: yer tutucular tutmazsa once kaynak
metinle bicimlendirmeyi dener, o da olmazsa ham kaynak metni dondurur. **Eksik
ya da hatali bir ceviri uygulamayi durdurmaz.**

### 5.2 `mark()` — neden gerekli

Bazi metinler uygulama acilirken, **dil secilmeden once** uretilir:
`MBR_TYPES`, `GPT_TYPES`, `WIPE_METHODS`, `SIGNATURES`, `operations.KINDS`.
Tanim yerinde cevirmek ise yaramaz: metin bir kez uretilir ve dil degisince
guncellenmez.

gettext'in `N_()` yaklasimi uygulandi — kaynakta **isaretle**, gosterirken
**cevir**:

```python
WipeMethod("zero", mark("Sifirla (1 gecis)"), ...)   # tanim
combo.addItem(tr(method.label), method.key)          # gosterim
```

### 5.3 Bekleyen islem basliklari gec cevrilir

`Operation` hazir metin saklamaz; **kalip + argumanlari** saklar:

```python
Operation("format", title_text=mark("Bolum {} bicimlendir"), title_args=(index,))
```

`title` okundugu anda cevrilir. Boylece kuyruk doldurulduktan sonra dil
degistirilirse bekleyen adimlarin basliklari da yeni dile doner.

### 5.4 Dil secimi ve saklanmasi

```
DISKULTIMATE_LANG  >  kayitli secim  >  isletim sisteminin dili  >  Turkce
```

- Kayit `core/settings.py` ile isletim sisteminin kullanici ayar klasorune
  yazilir (`platform.config_dir()`): Windows `%APPDATA%\DiskUltimate`,
  Linux `$XDG_CONFIG_HOME/diskultimate`, macOS `Library/Application Support`.
  **Proje dizinine yazilmaz** — uygulama salt okunur bir klasorden
  calistirilabilir.
- Sistem dili `platform.system_language()` ile okunur: Windows'ta
  `GetUserDefaultUILanguage`, POSIX'te `LANG`/`LC_*`. `locale.getdefaultlocale()`
  **kullanilmadi**: 3.11'den beri kullanimdan kaldirildi ve Windows'ta arayuz
  dilini degil bolge ayarini verir.
- Acilistaki secim **kaydedilmez**; yalnizca kullanici menuden secerse yazilir.
  Yoksa sistemin dili sonradan degistiginde uygulama eski dilde takili kalirdi.
- Cevirisi olmayan bir sistem dilinde (orn. Fransizca) kaynak dile dusulur:
  yarim cevrilmis arayuz gostermektense Turkce gostermek daha durust.

### 5.5 Dil degisimi: yeniden baslatma yok

Cogu uygulama dil degisince yeniden baslatilmayi ister. Burada bu kabul
edilemezdi: acik bir **fiziksel disk** ve **bekleyen islem kuyrugu** olabilir
(ADR 0025); yeniden baslatmak ikisini de kaybettirir.

```
kullanici "Deutsch" secer
        |
   i18n.set_language("de")          -> sozluk yer degistirir, ayar yazilir
        |
   dinleyicilere haber                (i18n.add_listener)
        |
   MainWindow.retranslate()
        |-- _retranslate_actions()    ~70 eylem metni + ipucu
        |-- menu basliklari           (_menus: (menu, kaynak metin))
        |-- sekme basliklari, agac basligi, arac cubugu
        |-- durum cubugu (yetki rozeti)
        |-- browser / part_table / hex_view . retranslate()
        +-- acik oturum varsa refresh(), yoksa bilgi paneli yeniden uretilir

   diyaloglar: her acilista kuruldugu icin kendiliginden dogru dilde
```

Eylem metinlerinin **tek kaynagi** `_retranslate_actions()`: `_build_actions()`
eylemi bos metinle kurar, metni bu islev doldurur. Yeni bir eylem eklenip
buraya satiri yazilmazsa `tests/i18n_check.py` bunu **hata** sayar.

### 5.6 "Gorunen metin karar girdisi degildir"

Ceviri calismasi iki yerde gercek bir kusur acti — kod, kendi urettigi metnin
**icinde arama** yaparak karar veriyordu:

```python
kilit = "kilitlenmis" in reason        # salt okunur acilis uyarisi
"[YIKICI]" in line                     # uygulama onayi
```

Metin cevrilince ikisi de sessizce bosa duserdi. Veriye tasindi:
`DiskImage.readonly_locked` bayragi ve `OperationQueue.describe_rows()` ->
`(metin, yikici mi)` ciftleri. Kural CLAUDE.md'ye yazildi.

---

## 6. Olcumler (Linux misafiri, Python 3.12.3)

```
de.json: 1315 giris | dosya 100 KB | yukleme 0.8 ms | bellek 227 KB
en.json: 1315 giris | dosya  91 KB | yukleme 0.5 ms | bellek 215 KB

KAYNAK DIL (tr) — sozluk bos:
  ham dize (referans)                        30 ns
  tr(kisa metin)                            107 ns
  tr(kalip, 3)                              382 ns

CEVIRI ETKIN (en) — 1315 girisli sozluk:
  tr(kisa metin)                            125 ns
  tr(kalip, 3)                              393 ns
  tr(uzun HTML metin, 628 karakter)         130 ns
  sozlukte olmayan metin                    121 ns

i18n.initialize()                           7.1 ms   (ayar dosyasi + sozluk)
1000 metinlik pencere kurulumu              0.23 ms
```

**Yorum:** `tr()` bir sozluk aramasi + islev cagrisi; ceviri etkinken ek maliyet
~95 ns. Bir pencerenin butun metinleri 1000 cagri olsa bile toplam 0.23 ms —
olculemeyecek kadar kucuk. Bellekte tek dil duruyor (~220 KB); sozluk dil
degisiminde **yer degistiriyor**, birikmiyor. Acilistaki 7.1 ms'nin cogu ayar
klasorunun olusturulmasi ve ilk dosya okumasi.

Karsilastirma icin: uygulamanin bir bolum tablosu okumasi ~600 ms surebiliyor
(ADR 0026 olcumu). Ceviri katmani, olculen hicbir donma/yavaslik kaynaginda
gorunmuyor.

---

## 7. Dogrulama

### 7.1 `tests/i18n_check.py` — 6 denetim

| Denetim | Neyi yakalar |
|---|---|
| eksik ceviri | Kaynaktaki bir metin sozlukte yok ya da bos |
| bayat giris | Sozlukte kaynakta artik olmayan metin (dosya cope donmesin) |
| yer tutucu | `{}` sayisi / `{ad}` adlari kaynak ile ceviride ayni degil |
| HTML etiketi | `<b>`, `<br>` ceviride kaybolmus/eklenmis |
| eylem metinleri | `self.act_*` var ama `_retranslate_actions()` satiri yok |
| calisma zamani | Sozluk yuklenince `tr()` gercekten ceviriyor, kaynak dile donunce geri geliyor |

Ayrica `--write <kod>` sozluk iskeletini uretir/tazeler, `--list` cevrilecek
metinleri dokum eder.

### 7.2 Arayuz duman testi

`tests/ui_smoke.py` icindeki `dil_denetimi()` her dile gecer, arac cubugu /
sekme / menu / sutun metinlerinin **gercekten degistigini** dogrular, ekran
goruntusu alir (`24-dil-<kod>.png`) ve kaynak dile doner.

### 7.3 Kosum sonuclari

| Ortam | Kosum | Sonuc |
|---|---|---|
| Windows misafiri (VMware Win10, Py 3.12 32-bit) | `run_all` | 28/28 |
| " | `i18n_check` | tr/en/de TAMAM |
| " | `platform_check` | 0 bulgu |
| " | `diag_check` | 13/13 |
| " | `ui_smoke` | tamamlandi, `24-dil-de.png` / `24-dil-en.png` |
| " | dil kaliciligi | secim `%APPDATA%\DiskUltimate\settings.json`'a yazildi, **yeni surec** okudu, `DISKULTIMATE_LANG` onu gecti |
| Linux misafiri (Mint 22.3, Py 3.12.3) | `i18n_check` | tr/en/de TAMAM (1315) |
| " | `platform_check` | 0 bulgu |
| " | canli dil degisimi | 8 menu + 4 sekme + tablo sutunlari uc dilde dogrulandi |

Cekirdek testleri Turkce mesaj icerigine bakiyor (orn. `"birincil" in str(exc)`);
bu yuzden `i18n` **kendiliginden** dil secmez — secim yalnizca `main.py` icinde
`initialize()` ile yapilir ve testler kaynak dilde kosar.

---

## 8. Bilinen sinirlar ve riskler

### 8.1 Cogul eki (plural) yok — **orta oncelik**

`tr("{} bolum bulundu", n)` her sayida ayni kalibi kullanir. Ingilizce'de
"1 partitions" gibi bir sonuc dogar. Su an sorunlu gorunen kalip sayisi az
(metinler "{} partitions" bicimde yazildi) ama **dogru cozum degil**.

Cozum: `tr()` icine gettext'in `ngettext` esdegeri eklenir; sozluk degeri dize
yerine `{"one": ..., "other": ...}` olabilir. Dil basina cogul kurali gerekir
(Turkce tek bicim, Ingilizce/Almanca iki bicim).

### 8.2 Baglam (context) yok — **dusuk oncelik**

Ayni Turkce metin iki farkli yerde farkli cevrilmesi gerekiyorsa ayrim
yapilamaz. Ornek aday: `"Tur"` sutunu (partition type) ile bir dosya turu
alani. Bugun cakisma **gozlenmedi**; cikarsa cozum ya metni ayirmak
(`"Bolum turu"` / `"Dosya turu"`) ya da `tr(metin, context=...)` eklemektir.

### 8.3 Sayi / tarih / birim bicimi yerellestirilmedi — **orta oncelik**

- `human_size()` her dilde `1.00 GB` uretir; Almanca'da ondalik ayirici virgul
  olmaliydi (`1,00 GB`).
- Tarihler `%Y-%m-%d %H:%M` sabit bicimde.
- Yuzdeler `%12.3` bicimde (Turkce onek), Ingilizce'de `12.3%` beklenir. Bazi
  metinlerde ceviride duzeltildi, hepsinde degil.

Bunlar `tr()`'nin degil, bicimlendirme yardimcilarinin isi. Dogru yol
`ptable.human_size()` ve tarih bicimlerini dile duyarli hale getirmek.

### 8.4 Saga yazilan diller (RTL) desteklenmiyor — **bugun kapsam disi**

Arapca/Ibranice eklenirse `QApplication.setLayoutDirection(Qt.RightToLeft)`
ve yerlesim gozden gecirmesi gerekir. Sozluk altyapisi engel degil; arayuz
yerlesimi denenmedi.

### 8.5 Kaynak metin degisirse ceviri duser

Turkce metinde bir yazim duzeltmesi anahtari degistirir; o girisin cevirisi
kaybolur ve `i18n_check` "eksik ceviri" der. **Sessiz kayip yok**, ama duzeltme
elle yapilir. Kucuk duzeltmelerde pratik yol: eski anahtarin karsiligini yeni
anahtara kopyalamak.

### 8.6 Metin uzunlugu tasmasi — **gozlenmis, kismen giderildi**

Almanca metinler Turkce'den uzun. Ilk kosumda arac cubugu tasma okuna
(`»`) dustu; uc etiket kisaltilarak giderildi
("Partitionsgrosse andern" -> "Grosse andern"). Yeni dil eklenirken **ekran
goruntusu ile bakmak sart**; `i18n_check` uzunluk denetlemez.

### 8.7 Ceviri kalitesi

Ingilizce ve Almanca cevirileri bu oturumda yazildi; **bagimsiz bir gozden
gecirmeden gecmedi**. Teknik terimler tutarli secildi (partition/Partition,
sector/Sektor, backup/Sicherung, wipe/sicher loschen) ama ozellikle Almanca
icin bir anadil gozden gecirmesi onerilir. Sozluk duz JSON oldugu icin bu is
kod bilgisi gerektirmiyor.

### 8.8 Paketlenmis surumde veri dosyasi

`i18n/catalogs/*.json` `__file__` uzerinden okunur. PyInstaller ile paket
uretilirse bu dosyalar **veri olarak eklenmelidir**; eklenmezse uygulama
cokmez, yalnizca Turkce calisir. (Depodaki `DiskUltimate.spec` baska bir
projeden kalma; bu proje icin gecerli bir spec degil.)

### 8.9 Denetimin goremedigi sey — **bu raporu hazirlarken kanitlandi**

`i18n_check` yalnizca **isaretlenmis** metni gorur; hic sarilmamis bir metni
bilemez. Rapor icin yapilan taramada, ceviri calismasindan **sonra** eklenen
onyukleyici ozelliginde dort bosluk bulundu:

| Yer | Metin | Sonuc |
|---|---|---|
| `ui/dialogs/bootloader.py:286` | `title_text="Onyukleme kodunu kaldir"` | `mark()` ile sarildi |
| `ui/dialogs/bootloader.py:287` | `detail_text="{} — ilk 440 bayt sifirlanir"` | `mark()` + ceviri eklendi |
| `ui/dialogs/bootloader.py:289` | `target_text="Disk"` | `mark()` ile sarildi |
| `ui/main_window.py:2989` | `f"BOLUM {part.index}"` (bolum bilgisi basligi) | `tr()` ile sarildi |

Dordu de duzeltildi, iki yeni metin uc dile cevrildi (1313 -> 1315) ve VM'de
dogrulandi. **Ders:** yeni ozellik eklerken `Operation(...)` metinleri ve
bilgi paneli basliklari kolayca gozden kaciyor. Onerilen ek denetim: bolum 9.1.

---

## 9. Oneriler (oncelik sirasiyla)

### 9.1 Sarilmamis metin tarayicisi (kolay, yuksek fayda)

Bu raporda elle yapilan tarama `tests/i18n_check.py` icine bir **uyari**
denetimi olarak eklenebilir: `Operation(...)` cagrilarinda `title_text` /
`detail_text` / `target_text` anahtarlarinin ham dize olmasi, ve
`QLabel`/`setText`/`addItem` gibi bilinen sunum cagrilarinda `tr()`/`mark()`
ile sarilmamis, Turkce sozcuk iceren dize bulunmasi. Kesin degil (yanlis
pozitif verir: ikon adlari, gunluk mesajlari) — bu yuzden **hata degil uyari**
olmali ve bilinenler bir muafiyet listesinde tutulmali.

### 9.2 `human_size()` ve tarih bicimini dile duyarli yapmak (orta)

Almanca'da `1,00 GB`, Ingilizce'de `1.00 GB`. Tek dokunus noktasi
`core/ptable.human_size()` ve ozet tablolarindaki `strftime` cagrilari.

### 9.3 Cogul eki destegi (orta)

`tr()` icine `ngettext` esdegeri. Sozluk bicimi geriye uyumlu kalabilir
(dize veya `{"one":..., "other":...}`).

### 9.4 Ceviri gozden gecirmesi (dusuk teknik, yuksek deger)

Ozellikle Almanca. JSON dosyasi dogrudan duzenlenebilir; `i18n_check` yer
tutucu ve HTML bozulmasini yakalar.

### 9.5 CI'ya baglamak

`i18n_check` cikis kodu dondurur (0/1). Bir surekli tumlestirme adimi
eklenirse ceviri kaymasi birlestirmeden once yakalanir.

---

## 10. Yeni dil eklemek

```bash
python3 -m tests.i18n_check --write fr     # catalogs/fr.json iskeleti
#   fr.json doldurulur (bos degerler)
#   i18n/__init__.py -> LANGUAGE_NAMES icine  "fr": "Français"
python3 -m tests.i18n_check                # eksik / bayat / yer tutucu denetimi
python3 -m tests.ui_smoke                  # 24-dil-fr.png ile gozle bak
```

Dil menusu `catalogs/` klasorunu okur; **kodda baska degisiklik gerekmez**.
Tek elle adim, dilin kendi adini `LANGUAGE_NAMES` icine yazmaktir (menude
"Français" gorunsun diye; kod adiyla da calisir).

---

## 11. Cikis kapisi: gettext'e gecmek gerekirse

Bugunku tasarim gettext'e gecisi kolaylastiriyor, zorlastirmiyor:

- Anahtar zaten **kaynak metin** — gettext'in `msgid` mantigiyla ayni.
- `tr()` / `mark()` adlari `_()` / `N_()` ile birebir eslesiyor; degisiklik
  `i18n/__init__.py` icinde kalir, 1656 cagri yerinde durur.
- JSON sozluk -> `.po` donusumu duz bir betiktir (`msgid`/`msgstr` ciftleri).

Yani bu karar **geri donulmez degil**. Cogul eki ve baglam ihtiyaci
buyudugunde gecis maliyeti, bugun gettext'i kurmanin maliyetinden dusuk
kalacaktir.


---

## 12. Sektorde bu is nasil yapiliyor — ve daha etkili ne olurdu

Kullanicinin sorusu uzerine (2026-09-17) yapilan degerlendirme. Olgun bir
yerellestirme altyapisi **bes katmandan** olusur; ev yapimi cozumlerin cogu
yalnizca birincisini yapar — bizimki de oyleydi.

### 12.1 Bes katman

| Katman | Sektorde | Bizde |
|---|---|---|
| 1. Katalog bicimi + anahtar stratejisi | gettext `.po`, Qt `.ts`, Apple `.strings` (kaynak metin anahtar); Android `strings.xml`, .NET `.resx`, Fluent, i18next (sembolik anahtar) | JSON, kaynak metin anahtar |
| 2. Cogul/cinsiyet motoru | **ICU MessageFormat** (`{n, plural, one {...} other {...}}`) — Chrome, iOS, Android, FormatJS, ICU4J | **yok** |
| 3. Yerel veri (CLDR): sayi, tarih, para, siralama | `babel`, ICU | **yok** (`human_size` her dilde `1.00 GB`) |
| 4. Ceviri yonetimi (TMS) | Crowdin, Weblate, Lokalise, Transifex; ceviri bellegi, terim sozlugu, ekran goruntusu baglami | **yok** (JSON elle) |
| 5. Kalite guvencesi | sozde-yerellestirme (`qps-ploc`, `en-XA`), yerel bazli ekran karsilastirma, "sabit metin" lint kurallari | **eklendi** (bkz. 13.2) |

### 12.2 Iki anahtar okulu

- **Kaynak metin anahtar** (gettext, Qt, Apple): katalog olmasa da uygulama
  anlamli calisir. Kucuk/tek kisilik projede dogru secim — bizim sectigimiz.
- **Sembolik anahtar** (Android, .NET, Fluent, i18next): Ingilizce metin
  degisince 30 dilin cevirisi dusmez. Buyuk ekipler ve TMS akisi icin dogru.

Bir disk aracinda katalog bozulunca ekranda `BTN_WIPE_DISK` gormek kabul
edilemez oldugu icin bu proje icin birinci okul dogrudur.

### 12.3 Daha etkili ne olurdu

**a) JSON yerine `.po` bicimi — en buyuk kacirilan firsat.**

Kritik nokta: `.po` kullanmak icin **gettext calisma zamanini kullanmak
zorunlu degil**. `.mo` derlemesi yalnizca bir hiz optimizasyonudur; `.po`
dosyasini dogrudan okuyan ~80 satirlik bir ayristirici yeterlidir. Bedava
gelenler:

- **Cogul ekleri** — `.po` basligindaki `Plural-Forms` ifadesini Python'un
  kendi `gettext.c2py()` islevi derler (stdlib, harici bagimlilik yok).
- **Baglam** (`msgctxt`) — ayni Turkce metnin iki yerde farkli cevrilmesi.
- **Fuzzy + `msgmerge`** — kaynak metin degisince ceviri **silinmez**,
  "fuzzy" isaretlenir. Raporun 8.5 maddesindeki sorunun sektordeki cozumu.
- **Butun arac ekosistemi** — Poedit, Weblate, Crowdin `.po`'yu dogrudan
  konusur. Almanca gozden gecirmesi icin birine JSON yerine Poedit dosyasi
  yollanir.
- Cevirmen yorumu (`#.`) ve kaynak konumu (`#:`) satirlari.

Ayni sifir bagimlilik, ayni `core/` uyumu, ~80 satir fazladan kod.

**Bu secim neden kacirildi:** CLAUDE.md "harici bagimlilik yok" diyor ve bu
kural **calisma zamani** icin yazilmis; `.po` "msgfmt kurulu degil" diye
elenirken kural geliştirme araclarina da uygulandi. Kural degil, kuralin
gereksiz genis okumasi engelledi. Ayrim artik CLAUDE.md'ye yazildi.

**b) Sozde-yerellestirme** — ~30 satir; bkz. 13.2, eklendi.

**c) Elle yazilan `_retranslate_actions()` yerine kayit defteri.** Bugun
"metni yaz, sonra baska yerde yeniden yazmayi unutma" duzeni var ve unutmayi
bir test yakaliyor. Daha iyisi unutmayi **imkansiz** kilmak: eylem kurulurken
kaynak metin nesnenin uzerinde saklanir (`setProperty("i18nText", ...)`),
`retranslate()` butun cocuklari gezip yeniden yazar. Qt'nin `retranslateUi()`
+ `QEvent::LanguageChange` duzeni budur. Test gerekmez, cunku kacirilacak adim
kalmaz.

**d) Kucuk bir yerel veri tablosu** — dil basina ondalik ayirici, tarih
kalibi, yuzde konumu. `babel` gerekmez.

### 12.4 Daha iyi **olmayacak** olanlar

- **Qt Linguist:** metnin %45'i `core/` icinde; cevirmek icin cekirdegi Qt'ye
  baglamak gerekirdi (28 cekirdek testi de Qt'ye bagimli hale gelirdi).
- **Harici kutuphane:** calisma zamani bagimliligi + cevrimdisi kurulum yuku.
- **Sembolik anahtar:** bkz. 12.2.

---

## 13. Rapordan sonra yapilanlar (2026-09-17)

### 13.1 Almanca yazim duzeltmesi — 495 ceviri

Proje alismasi olan "ASCII Turkce" ceviriye de tasinmisti; Almanca'da bu
**yanlis**: "Grosse" (→ Größe), "Datentrager" (→ Datenträger), "fur" (→ für),
"loschen" (→ löschen)... Ustelik tutarsizdi: 88 ceviride umlaut vardi,
kalanlarda yoktu.

Yontem: kor arama-degistirme **yapilmadi**. Katalogda gercekten gecen 1360
farkli umlautsuz sozcuk listelendi ve tek tek gozden gecirildi; 201 sozcukluk
bir duzeltme haritasi kuruldu. Listede olmayan sozcuge dokunulmadi — "konnte"
(gecmis zaman, dogru), "Vorgangsprotokoll" (birlesik, dogru), "Flache (flat)"
(sifat, dogru) gibi tuzaklar bu yuzden bozulmadi.

Sonuc: **495/1315 ceviri** duzeldi; umlaut/ß iceren ceviri 88 → 583.
`i18n_check` yer tutucu ve HTML butunlugunu dogruladi (guvenlik agi isini
yapti).

Kural CLAUDE.md'ye yazildi: *konsol/gunluk ASCII kalabilir, arayuz metni ve
ceviriler dilin dogru yazimiyla.*

### 13.2 Sozde-yerellestirme eklendi

```bash
DISKULTIMATE_LANG=qps python3 main.py
# "Bolumu sil"  ->  "[!Ɓǿŀŭḿŭ şīŀ···!]"
```

- Sozluk dosyasi yok; metin calisma aninda donusturulur (`i18n.pseudo`).
- Yer tutucular (`{}`, `{ad}`, `{:08X}`, `{{`), HTML etiketleri ve varliklar
  (`&nbsp;`) **dokunulmadan** kalir.
- Dil menusunde gorunmez (bir kalite aracidir); yalnizca etkinken listelenir.
- `tests/ui_smoke.py` -> `sozde_denetimi()`: sozde dilde taze bir ana pencere
  ve alti diyalog kurar; eylem/menu/sekme/sutun metinlerinin hepsinin sozde
  oldugunu **dogrular**, kalanlari bilgi olarak listeler.

**Ilk kosumda kendi test verimizi yakaladi** (`InfoDialog`'a cevrilmemis
baslik veriliyordu) ve testi dusurdu — yani calisiyor. Duzeltildikten sonra
listede yalnizca **veri** kaldi: "64 bit", "Linux", harici arac adlari.

### 13.3 Dogrulama (Linux misafiri, Mint 22.3)

| Kosum | Sonuc |
|---|---|
| `tests.i18n_check` | tr/en/de **TAMAM** (1315) |
| `tests.platform_check` | **0 bulgu** |
| `tests.ui_smoke` | **tamamlandi** (cikis 0); sozde denetimi yesil |
| canli dil degisimi | 8 menu + 4 sekme + tablo sutunlari uc dilde dogrulandi |

### 13.4 Siradaki

1. `.po` gecisi (bkz. 12.3a) — cogul, baglam, fuzzy, Poedit/Weblate.
2. Kayit defterli `retranslate` (12.3c).
3. Yerel sayi/tarih bicimi (12.3d).
4. Almanca icin anadil gozden gecirmesi (8.7).
