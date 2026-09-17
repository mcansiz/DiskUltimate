# ADR 0026 — Diskler ve bolumleri acilmadan gorunur

**Tarih:** 2026-09-15
**Durum:** Kabul edildi
**Ilgili:** ADR 0014 (fiziksel disk destegi), ADR 0021 (acik aygiti yoklamama),
ADR 0025 (bekleyen islem kuyrugu)

## Baglam

Kullanici: *"fiziksel disklere tiklayip icerik goruntuleme yerine Acronis vari
gorunum yapalim, disk ve altinda bolumler gorunsun."*

Onceki akis iki adimliydi: once diski **ac**, sonra bolumleri gor. Oysa
incelenen araclarin hepsi (Acronis, EaseUS, AOMEI, MiniTool, Macrorit) butun
diskleri ve bolumlerini **acilista** gosterir; acmak, uzerinde islem yapmak
icindir, bakmak icin degil.

## Guvenlik kuralindaki degisiklik — acikca

`physical.py` guvenlik katmani 1 soyle diyordu:

> **Listeleme zararsizdir** — hicbir sektor okunmaz, hicbir yazma yapilmaz.

Bolumleri gostermek icin **bolum tablosunu okumak** gerekir; yani sektor
okumak. Kurali sessizce esnetmek yerine ikiye ayirdik:

| Islem | Sektor okur mu? | Ne siklikta? |
|---|---|---|
| `list_disks()` — **listeleme** | **Hayir** | 3 saniyede bir (yoklama) |
| `survey_disk()` — **bolum yoklamasi** | **Evet**, yalnizca tablo + imzalar | Yalnizca disk listesi degistiginde veya elle yenilemede |

Yazma tarafinda hicbir sey degismedi: yoklama aygiti **salt okunur** acar ve
okur okumaz kapatir. `PhysicalDisk` yazma kapilarinin tamami yerinde durur.

Sikliktaki ayrim onemlidir: ADR 0021'de, aygita gereksiz yere dokunmanin
surucu yigininda 64 saniyelik sikismalar urettigi **olculmustu**. Bu yuzden
yoklama yoklama dongusune baglanmadi; disk imzasi (yol, boyut, model, bagli
bolumler) degismedikce tekrarlanmaz. Uygulamanin kendi acik tuttugu disk ise
hic yoklanmaz — o durumda arayuzde zaten oturumun kendi bolum listesi vardir.

## Karar

### 1. `DiskSession.survey_disk(info) -> DiskSurvey`

Aygiti salt okunur acar, GPT/MBR tablosunu cozer, her bolumun dosya sistemini
`fsdetect` ile tespit eder ve aygiti **hemen kapatir**. Basarisizlik hata
degil bilgidir: `error` alani doldurulur (yetki yok, ortam yok, bozuk tablo)
ve arayuz bunu oldugu gibi yazar.

### 2. Arka planda, degisiklik oldukca

`DiskScanner` artik iki sey dondurur: disk listesi ve **degisen** disklerin
ozeti. Degismeyen disk yeniden yoklanmaz. Arayuz is parcaciginda hicbir aygit
islemi yapilmaz (ADR 0020).

### 3. Agacta bolumler

Her fiziksel diskin altinda bolumleri listelenir: dosya sistemi rengi, etiket,
boyut. Bir bolume tiklamak diski (salt okunur) acar ve **dogrudan o bolumu**
secer — "once ac, sonra bul" adimi ortadan kalkar.

### 4. Genel bakis seridi (`ui/widgets/disk_overview.py`)

Hicbir kaynak acik degilken harita alaninda **butun diskler alt alta** gosterilir:
solda kimlik kutusu (ad, sema, boyut, sistem diski uyarisi), sagda bolum seridi.
Bir kaynak acilinca mevcut tek disk haritasina gecilir (`QStackedWidget`).

`DiskMapWidget` **degistirilmedi**: calisan ve test edilmis tek disk cizimi
oldugu gibi kaldi, cok diskli gorunum ayri bir widget oldu.

Yukseklik disk sayisina gore buyur ama uc satirda durur; fazlasi kaydirilir.
Sabit yukseklik tek diskli makinede bos alan, cok diskli makinede kirpilma
uretiyordu (ilk surumde goruldu ve duzeltildi).

## Olcum

Gelistirme makinesinde (3 disk: NVMe sistem diski, SD kart, USB bellek)
yoklama butun diskler icin toplam birkac yuz milisaniye surer ve arka planda
calisir; arayuz duraksamaz. Uc diskin de bolumleri, hicbiri acilmadan
listelendi:

```
PhysicalDrive0  GPT   5 bolum   (ham, FAT32 NO NAME, NTFS, NTFS, NTFS)
PhysicalDrive1  MBR   3 bolum   (FAT16 NO NAME, ext4 rootfs, NTFS)
PhysicalDrive2  MBR   1 bolum   (FAT32 NO NAME)
```

## Dogrulama

- `t27_disk_yoklamasi`: aygit **salt okunur** aciliyor, **hicbir yazma**
  yapilmiyor, aygit **kapatiliyor**, bolumler ve dosya sistemleri dogru
  okunuyor; uygulamanin acik tuttugu aygit yoklanmiyor; bolum tablosu olmayan
  disk hatasiz bos sonuc donduruyor. Gercek diske dokunulmaz —
  `PhysicalDisk` sahte bir surumle degistirilip yazma girisimi hata olarak
  yakalanir.
- `ui_smoke`: sahte bir yoklama sonucuyla agacta bolumlerin gorunmesi, dugum
  verisinin (`physpart`) dogru olmasi ve genel bakis seridinin ayni bolumleri
  cizmesi denetlenir. Goruntu: `23-disk-genel-bakis.png`.
- Windows: `run_all` 27/27 · `platform_check` 0 bulgu · `diag_check` 13/13.
- Linux: `run_all` 25/27 + 2 atlandi (Windows dali) · `platform_check` 0 bulgu.

## Duzeltme — acik disk agacta **yerinde** kalir (ayni gun)

Ilk surumde acilan disk hala ayri bir dal uretiyordu: disk satirinda
"(asagida acik)" yaziyor, bolumleri agacin altinda ikinci bir kokte
gosteriliyordu. Kullanici ayni diski iki yerde goruyor ve islemleri "asagida"
yapmak zorunda kaliyordu — istenen tam tersiydi.

- Fiziksel disk oturumu icin **ayri dal olusturulmuyor**. Disk kendi satirinda
  kalir; acikken bolumleri **oturumdan** (canli durum), kapaliyken yoklamadan
  gelir. Tek bir liste, tek bir yer.
- Acik disk satiri kalin yazilir ve semasini gosterir; uzerine tiklamak onu
  etkin kaynak yapar. Sag tik menusunde "Bu diski kapat" vardir.
- Goruntu dosyalari (.img, VHD, .dub) fiziksel disk listesinde yer almadiklari
  icin kendi dallarinda gosterilmeye devam eder.
- `_select_tree` artik agaci **her derinlikte** arar: bolumler artik torun
  dugumdur, yalnizca bir seviye bakan surum acik diskin bolumunu hic
  secemiyordu.

### Yol acilan olcum
Tanilama, disk acilirken arayuzun **1.5 sn** takildigini yakaladi. Neden:
14.7 GB FAT32 bolumun tespiti 7.7 MB okuyor (FAT tablosunun tamami, ~590 ms)
ve bu is **uc kez** yapiliyordu — `DiskSession` kurulurken bir kez, `refresh()`
icinde bir kez daha, secimde bir kez daha.

`refresh(reload=False)` eklendi: kaynak az once acildiysa tablo yeniden
okunmaz. Olcum 1.5 sn -> **0.95 sn**; kalan iki okumadan biri tespit
(`fsdetect`), oteki dosya sistemi surucusunun kendi acilisidir. Ikisini tek
okumaya indirmek `fsdetect` ile `filesystem` katmanlarini birlestirmeyi
gerektirir — ayri bir is olarak not edildi.

## Sinir

Genel bakis **bekleyen islemleri gostermez**: kuyruktaki adimlar acik oturumun
bolum tablosunda isaretlenir (ADR 0025), serit ise diskin diskteki mevcut
durumunu cizer. Ikisini birlestirmek, ADR 0025'te birakilan "bekleyen durumu
simule etme" isiyle birlikte yapilmalidir.
