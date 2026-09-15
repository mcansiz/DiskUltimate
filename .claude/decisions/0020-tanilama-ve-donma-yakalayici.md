# ADR 0020 — Tanilama altyapisi ve donma yakalayici

**Tarih:** 2026-09-15
**Durum:** Kabul edildi

## Baglam

Kullanici Windows'ta soyle bir sorun bildirdi: SD kart takiliyken (`PhysicalDrive1`,
`Generic- SD/MMC/MS PRO`, 59.48 GB) bolum listesinde **Bos alan** satirina
tiklandiginda pencere **yaklasik 30 saniye** "Yanit Vermiyor" durumuna dusuyor,
sonra kendiliginden duzeliyor.

Kendiliginden duzelmesi onemli bir ipucudur: bu bir kilitlenme (deadlock) degil,
**arayuz is parcaciginda calisan uzun bir isletim sistemi cagrisidir**. Ama
elimizde hicbir kanit yoktu:

- Uygulama yalnizca kullaniciya gosterdigi olaylari (`app-<tarih>.log`) yaziyordu;
  islem sureleri, hangi cagrinin ne kadar surdugu kayitli degildi.
- Donma anindaki yigin (stack) hicbir yere dokulmuyordu. Donma bittiginde geriye
  bakilabilecek hicbir iz kalmiyordu.
- Sorun ancak belirli bir donanimla (kart okuyucu + USB bellek) ortaya ciktigi
  icin gelistirme makinesinde birebir tekrarlanamiyordu.

Bu yuzden once **tani altyapisi**, sonra duzeltme yapilmasina karar verildi.

## Karar

### 1. `core/diagnostics.py` — saf Python tani cekirdegi

Katman kurali korunur: modul Qt bilmez, `core/` icinde durur ve testten
cagrilabilir.

- **Oturum gunlugu** — her calistirmada `.claude/logs/runtime/session-<zaman>-<pid>.log`.
- **`span()`** — bir islemin suresini olcer. `SLOW_MS` (200 ms) esigini asan her
  islem `YAVAS` olarak gunluge girer. Kapaliyken maliyeti sifira yakindir.
- **`Watchdog`** — ayri bir is parcacigi. Arayuz duzenli olarak `beat()` cagirir;
  nabiz `STALL_MS` (1500 ms) boyunca gelmezse **butun is parcaciklarinin yigini**
  `.claude/logs/freeze/freeze-<zaman>.md` dosyasina dokulur. Donma surdukce
  2 saniyede bir yeniden ornek alinir — "tek cagrida mi asili, yoksa dongude mi"
  sorusu boylece yanitlanir.
- **Cokme yakalayici** — `faulthandler` ile olumcul hatalarda (ctypes erisim
  ihlali gibi) yigin diske yazilir.

Raporda uc sey yan yana durur: **o an acik islem** (`span` yigini), **yigin**
ve **donmadan onceki son islemler**. Ucu birlikte "ne yapilirken, nerede,
neyin ardindan takildi" sorusunu yanitlar.

### 2. `ui/diag.py` — Qt tarafi

- Arayuz is parcaciginda 200 ms'lik bir `QTimer` nabzi. Arayuz bloke olunca bu
  zamanlayici da durur; yakalayicinin olcusu tam olarak budur.
- `qInstallMessageHandler` ile Qt uyarilari gunluge.
- `sys.excepthook` ile yakalanmamis istisnalar gunluge. PyQt5 bir yuvadaki
  istisnayi yalnizca terminale basar; terminalsiz calistirmada iz kalmazdi.
- Donma bittiginde arayuze sinyal gider, islem gunlugunde "arayuz N sn yanit
  vermedi" satiri gorunur.

Arayuzde **Araclar > Tanilama** menusu: durum, son donma raporu, elle yigin
dokumu, gunluk klasorunu ac.

### 3. Donmanin giderilmesi

Olcum, sorunun **disk listeleme** yolunda oldugunu gosterdi. Gelistirme
makinesinde (ayni kart okuyucu takiliyken) olculenler:

```
tur 1: 947 ms — 3 disk          <- ilk (soguk) tarama
  947 ms  physical.list_disks
  899 ms  win.open_volume(drive=F)   <- SD kartin FAT16 bolumu
tur 2:  56 ms
tur 3:  55 ms
```

Yani tek bir `CreateFileW` cagrisi soguk durumda ~0.9 saniye surdu. Bu sure
**ust sinirsizdir**: aygit uykudaysa, kart cikarilma anindaysa veya yuva bossa
Windows yeniden dener ve cagri onlarca saniye bloklar.

Uc degisiklik yapildi:

1. **Tarama arayuz is parcacigindan cikti** (`ui/disk_scan.py`). Liste artik
   `QThread` icinde toplanir, sonuc sinyal ile arayuze gelir. Tarama ne kadar
   surerse sursun pencere yanit verir. Listeleme salt okunur oldugu icin
   (guvenlik katmani 1) bu yeni bir risk getirmez.
2. **Ortam yok kutusu bastirildi** (`_win_quiet_errors`). `SetThreadErrorMode`
   ile `SEM_FAILCRITICALERRORS | SEM_NOOPENFILEERRORBOX` verilir; bos kart
   yuvasinda cagri kutu acmadan hemen `ERROR_NOT_READY` doner. Yalnizca cagiran
   is parcacigini etkiler.
3. **Acilmadan elenen suruculer** (`GetDriveTypeW`). Ag surucusu ve CD/DVD artik
   hic acilmaz: ag surucusunde tutamac acmak sunucu yanit vermiyorsa saniyelerce
   asili kalirdi.

Ayrica agac her yeniden kuruldugunda isletim sistemine gidilmiyor:
`_add_physical_disks` artik son taramanin onbellekini kullanir.

## Sonuc

Pencere acikken olculen degerler:

```
Olay dongusundeki en uzun duraklama: 47 ms
Yakalanan donma: 0
en yavas olcum: 78 ms  [Dummy-3]  physical.list_disks   <- arka plan
```

Tarama artik arka plan is parcaciginda (`Dummy-N`) gorunuyor ve arayuz
duraklamasi 50 ms'nin altinda.

## Sinirlar / durust kalan nokta

Kullanicinin yasadigi **30 saniyelik** donma birebir tekrarlanamadi; o an hangi
cagrinin bloke ettigini kesin soyleyen bir rapor elimizde yok. Yapilan sey
sudur: arayuz is parcaciginda bilinen ve **olculmus** tek engelleyici is (disk
sayimi) oradan kaldirildi ve tekrarlarsa artik kanit kendiliginden toplanir —
rapor donmanin oldugu satiri gosterir.

## Alternatifler

- **`faulthandler.dump_traceback_later`** — tek basina yeterli degil: sureyi
  yeniler, rapora "o an acik islem" bilgisini koymaz ve toparlanmayi
  raporlamaz. Yine de cokmeler icin acik birakildi.
- **Taramayi tumden birakmak** (yalnizca elle yenileme) — USB/SD uygulama
  acikken takilabildigi icin geri adim olurdu.
- **Yoklama araligini uzatmak** (3 sn -> 30 sn) — donmayi seyreklestirir,
  ortadan kaldirmaz.
