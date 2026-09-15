# ADR 0021 — Acik aygiti yoklamama kurali ve kopyalama ilerlemesi

**Tarih:** 2026-09-15
**Durum:** Kabul edildi
**Ilgili:** ADR 0020 (tanilama altyapisi), ADR 0014 (fiziksel disk destegi)

## Baglam

Kullanici bildirdi: SD kart (`PhysicalDrive1`) **yazma modunda** aciktir,
3. bolum NTFS. Dosya gezgininden `sdcard.img.dub` eklenmek istendi. Hicbir
ilerleme gostergesi cikmadi, pencere dondu; kendine geldiginde dosya yuklenmisti.

Bu kez tahmin gerekmedi: ADR 0020 ile kurulan donma yakalayici olayi
**kendiliginden** kaydetmisti.

## Bulgu — kanit

Rapor: `.claude/logs/freeze/freeze-20260915-134507.md` (63.0 sn, 29 yigin ornegi)

Oturum gunlugundeki olcumler:

```
13:44:46.874  arayuz: Fiziksel disk acildi: \\.\PhysicalDrive1 (YAZMA)
13:45:01.690  [Dummy-8]   win.query_drive(n=1)   basladi     <- arka plan yoklamasi
13:45:05.6    [MainThread] disk.read(lba=2165024, size=4096) basladi
13:45:07      DONMA — arayuz yanit vermiyor
13:46:05.718  [MainThread] YAVAS disk.read(lba=8456192, size=1024) — 32250 ms
13:46:05.719  [Dummy-8]    YAVAS win.query_drive(n=1)        — 64031 ms
13:46:08.409  [MainThread] YAVAS disk.write(lba=2208832, size=35685588) — 2437 ms
13:46:08.461  arayuz: 1 dosya eklendi
```

Uc islem de **ayni milisaniyede** serbest kaldi. Yani ana is parcaciginin
okumalari kendileri yavas degildi: hepsi arka plandaki
`IOCTL_STORAGE_QUERY_PROPERTY` cagrisinin arkasinda, ayni aygitin kuyrugunda
bekliyordu.

Yigin ornekleri de bunu soyluyor: 3.8 - 58.3. saniyeler arasindaki 27 ornegin
hepsinde ana is parcacigi `physical.py:_win_read` icindeki `ReadFile`
cagrisindaydi; yalnizca son iki ornek `_win_write` gosterir.

**Kok neden:** uygulama, **kendi acik tuttugu** diski 3 saniyede bir yokluyordu.
Yazma modunda birimler kilitlenip ayrildigi (`FSCTL_LOCK_VOLUME` /
`FSCTL_DISMOUNT_VOLUME`) icin, ayni aygita ikinci bir tutamac acip IOCTL sormak
kart okuyucunun surucu yiginini 64 saniye asili biraktI.

ADR 0020'de yoklamayi arka plana almak dogru adimdi ama yetmiyor: cakisma
**aygit duzeyinde**, is parcacigi duzeyinde degil.

## Karar

### 1. Acik aygit kutugu — "kendi actigin diski yoklama"

`core/physical.py` icinde surec genelinde bir kutuk tutulur:

- `PhysicalDisk` acilinca `_register_open(info)`, kapaninca `_unregister_open()`.
- `list_disks()` / `_list_windows()` kutuktekilere **dokunmaz**: tutamac acmaz,
  IOCTL sormaz; son bilinen `DiskInfo`yu `in_use=True` isaretiyle dondurur.
- `_win_drive_letters()` acik disklere ait surucu harflerini atlar
  (`busy_drive_letters()`); o harfler zaten bilinmektedir.
- Acma anindaki yaris icin arayuz, fiziksel disk acmadan once suren taramanin
  bitmesini bekler (`MainWindow._wait_for_scan`).

Kutuk `core/` icindedir, arayuzde degil: kural her cagirani korur, yalnizca
yoklama dongusunu degil.

### 2. Kopyalama ilerleme penceresi

Kullanicinin ikinci sikayeti: "kopyalama suruyor gibi bir ibare gelmedi."
Hakliydi — `import_files`, `import_folder` ve `export_selected` arayuz is
parcaciginda, sessizce calisiyordu. Cakisma olmasa bile bir GB'lik dosyayi
fiziksel diske yazmak dakikalar surer ve pencere o sure boyunca donar.

Ucu de artik `dialogs/task.run_task` uzerinden calisir: islem ayri bir is
parcaciginda, onunde ilerleme penceresiyle.

`TaskDialog` artik **belirsiz kip** de destekler: `report(mesaj, -1)` cubugu
hareketli yapar. Tek bir dosyanin yazilmasi tek adimdir, yuzde hesaplanamaz;
cubugu 0'da birakmak "takildi" izlenimi verirdi.

### 3. `import_tree` tek yerde toplandi

Klasor kopyalama dort ayri yerde yazilmisti (FAT, exFAT, ext, NTFS) ve **ikisi
hedefte ust klasoru olusturmuyordu**: ayni dugme FAT'te `/hedef/klasor/a.txt`,
ext ve NTFS'te `/hedef/a.txt` uretiyordu. Tek uygulama
`FileSystemAccess.import_tree` icine tasindi; ilerleme geri cagirmasi da orada.

**Davranis degisikligi:** ext ve NTFS bolumlerinde "Klasor ekle" artik ust
klasoru olusturuyor (FAT/exFAT ile ayni). Onceki davranis tutarsizlikti.

### 4. Beklenen yoklama hatalari gunlugu doldurmuyor

Var olmayan aygiti acmaya calismak (Windows hatasi 2) her turda 30'dan fazla
uyari satiri uretiyordu. `span(..., warn_on_error=False)` bu satirlari ayrinti
seviyesine indirir; islem **yavas** ise uyari yine verilir.

## Sinir — durust kalan nokta

Tek bir dosyanin yazilmasi hala bolunemez: `write_file(path, data)` butun
veriyi tek seferde alir, bu yuzden bir dosyanin **icindeki** ilerleme
gosterilemez (belirsiz cubuk gosterilir) ve dosya once tumuyle belege okunur.
Cok buyuk dosyalar icin parcali yazma ayri bir istir.

## Dogrulama

- `t19_klasor_kopyalama`: dort dosya sisteminde ayni yerlesim + ilerleme
  bildirimlerinin sayisi ve toplami
- `t20_acik_aygit_kutugu`: acik aygit icin `CreateFileW` **hic** cagrilmiyor,
  bilgi korunuyor, `in_use` isaretleniyor (gercek diske dokunulmaz)
- `tests.ui_smoke`: ilerleme penceresi ciziliyor
  (`17-kopyalama-ilerleme.png`), yuzde ve belirsiz kip denetleniyor
