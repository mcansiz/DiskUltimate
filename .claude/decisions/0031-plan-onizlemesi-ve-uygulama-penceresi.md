# ADR 0031 — Ana ekran planlanan yerlesimi gosterir; uygulama penceresi adim adim

**Tarih:** 2026-09-17
**Durum:** Kabul edildi
**Ilgili:** ADR 0025 (bekleyen islem kuyrugu), ADR 0004 (yikici islem
guvenligi), ADR 0021 (uzun is sessiz kalmaz)

## Baglam

Kullanici iki sey bildirdi:

1. *"bolumleme sonrasi uygula dedigimizde islemlerin siralamasini gosteren bir
   form istiyorum, adimlarin bu formda progress bar ile uygulandigi
   gorulecek."*
2. *"Disk silme bolme islemlerini yaptigimda bekleyen islemler tarafina bunu
   ekliyor ve kum saati ekleniyor ama ana ekran eski halde kaliyor; ana ekranda
   yapilacak isleme gore ayarlansin, benzer uygulamalarda bu sekilde."*

Ikisi de ADR 0025'in birakttigi bosluklardi. Kuyruk dogru calisiyordu —
diske dokunmadan plan biriktiriyordu — ama **gorunurlugu eksikti**:

* Ana harita ve bolum tablosu diskteki **eski** durumu ciziyordu. Kullanici
  "yeni bolum" adimini kuyruga ekledikten sonra ekranda hicbir sey degismiyor,
  yalnizca kenardaki liste buyuyordu.
* Uygulama iki parcaya bolunmustu: once adimlari **metin** olarak listeleyen
  bir onay kutusu, sonra tek cubuklu genel bir ilerleme penceresi. Islem
  baslayinca liste kayboluyordu; "hangi adimdayiz, hangisi bitti, nerede
  takildi" sorularinin hicbiri goremiyordu.

Benzer araclarda (DiskGenius, EaseUS, AOMEI, MiniTool) harita her zaman
**planlanan** yerlesimi gosterir ve uygulama penceresi adim listesini isleme
sirasinda da acik tutar.

## Karar

### 1. Planlanan yerlesim hesaplanir (`core/planview.py`)

`planview.project(session, queue)` kuyruktaki adimlari **bellekteki kopyalar**
uzerinde sirayla isler ve ortaya cikacak (bolumler, bos alanlar, sema)
ucluusunu dondurur. Gercek bolum tablosuna dokunmaz; bu yuzden onizleme
istenildigi kadar sik hesaplanabilir ve bir hata yerlesimi bozamaz.

`Partition` dataclass'ina `plan_state` alani eklendi: `""` (diskteki hali),
`new`, `changed`, `format`, `wipe`. Diskten okunan bolumlerde **her zaman
bostur**; yalnizca onizleme uretimi doldurur.

Onizleme bir **benzetim degil**: dosya sistemi ici ayrintilar (bicimlendirme
sonrasi gercek doluluk) hesaplanmaz, bilinmeyen deger "bilinmiyor" kalir
(`fs_used = -1`). Adimlar **dogrulanmaz** da — bir adim uygulanamayacaksa bunu
uygulama soyler. Onizlemenin yanlis bir "olur" gostermesi, hic gostermemesinden
kotudur.

### 2. Ana ekran planlanani cizer

* **Harita**: planlanan bolum mor kesik cerceve ve kose rozetiyle cizilir
  (`YENI`, `DEGISECEK`, `BICIMLENECEK`, `SILINECEK`). Silinecek bolum haritadan
  **kalkar**, yeni bolum belirir, boyutlandirilan yeni boyutuyla cizilir.
* **Tablo**: satir mor yazilir; yalnizca plan varken gorunen bir "Plan" sutunu
  durumu yazar. Kuyruk bosalinca sutun yeniden gizlenir.
* **Serit**: haritanin ustunde bir satir — "Planlanan yerlesim gosteriliyor —
  3 bekleyen adim uygulandiginda disk boyle olacak... Diske henuz yazilmadi" —
  ve yanindaki dugme ile **diskteki hale** gecilebilir. Kullanici hangi
  gercekligi gordugunu her an bilir.

**Ne degismedi:** agac, dosya gezgini, onaltilik goruntuleyici ve butun
islemler hala **diskteki gercek** durumu kullanir. Yalnizca planda var olan bir
bolum secilirse icerigi gosterilmez ("planlanan, henuz olusturulmadi") ve
bolum islemleri pasiflesir — diskte karsiligi yoktur.

### 3. Tek bir uygulama penceresi (`ui/dialogs/apply.py`)

Onay, ilerleme ve sonuc tek pencerede birlesti:

* Adimlar **uygulanacak sirayla** listelenir; her satirda ikon, hedef, durum ve
  **kendi ilerleme cubugu** vardir.
* Calisan adim isaretlenir, biten yesil "Tamamlandi", basarisiz kirmizi
  "Basarisiz" alir ve sonrakiler "Calistirilmadi" olarak kalir.
* Altta genel cubuk ve o anki adimin mesaji durur.

Onay hala zorunlu ve hala **parti basina** (CLAUDE.md): pencere acildiginda
hicbir sey calismaz, "Uygula" denene kadar diske dokunulmaz. Sistem diski
onayi ve bagli birim uyarisi degismedi — pencere onlari `prepare` geri
cagrisiyla eskisi gibi ana pencereden ister.

Cekirdek tarafinda `OperationQueue.apply()` uc yeni geri cagri kabul eder:
`on_step`, `on_step_progress`, `on_step_done`. Adimin kendi yuzdesi genel
olcege sigdirilir; boylece genel cubuk **geri gitmez** (eskiden her adim
kendi 0-100'unu genel cubuga yaziyordu).

## Sonuc

* `tests.run_all` t37 (onizleme gercek tabloya dokunmaz, silme/olusturma/
  donusum yerlesime yansir) ve t38 (adim geri cagrilari, genel cubuk geri
  gitmiyor) — Linux misafirinde gecti.
* `tests.ui_smoke` yeni ekranlari cizer ve denetler: `31-plan-onizlemesi`,
  `32-diskteki-hali`, `33-uygula-adimlar`, `34-uygula-calisirken`,
  `35-uygula-durdu`. Sozde-yerellestirme denetimine uygulama penceresi de
  eklendi.
* Duman testindeki eski beklenti guncellendi: silinecek bolum artik tabloda
  **yoktur**, bu yuzden kum saati sayisi 2'den 1'e dustu. Beklentiyi degil
  davranisi dogru saymak icin testin kendisi degistirildi ve nedeni yazildi.
