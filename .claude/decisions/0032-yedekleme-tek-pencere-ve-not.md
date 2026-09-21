# ADR 0032 — Yedekleme/geri yukleme tek pencerede; yedek dosyasina kullanici notu

**Tarih:** 2026-09-17
**Durum:** Kabul edildi
**Ilgili:** ADR 0009 (`.dub` yedek bicimi), ADR 0014 (fiziksel disk destegi),
ADR 0025 (bekleyen islem kuyrugu), ADR 0031 (plan onizlemesi ve uygulama
penceresi)

## Baglam

Kullanici DiskGenius'un "Backup Disk to Image File" ve "Restore Disk From
Image File" pencerelerinin ekran goruntulerini koydu ve sunu istedi:

> *"yedek dosya bilgisi menusunu de tek bir form kontrolu olarak ayarlansin,
> surekli farkli formlar ve popup menuler aciliyor, bunlarin hepsini tek bir
> formda istiyorum. Formun ustunde secilmis olan dub dosyasi, dosya bilgileri
> ve icerigi, sonra asagida fiziksel diskler... ek olarak dub yedek dosyasina
> bilgi notu da ekleyebilelim. Yedek alma ve yukleme islemi partitionlara
> tiklaninca yapiliyor halbuki biz burada diskin yedegini aliyoruz; disk
> secili oldugunda yedek alma islemleri de secilebilir olmali."*

Eski akis gercekten dagilmisti. "Yedegi bir diske yaz" icin kullanicinin
gordugu sira suydu:

1. menuden eylem, 2. dosya secme penceresi, 3. "nereye yazilsin?" soru
kutusu, 4. hedefi soran ikinci kutu, 5. yikici islem onayi, 6. sistem
diskinde ad yazma kutusu, 7. ayri ilerleme penceresi, 8. bitis bilgisi
penceresi. Sekiz pencere; hicbirinde "neyi nereye yaziyorum" bir arada
gorunmuyordu. Yedek dosyasinin bilgisi ise bambaska bir pencereydi
(`BackupInfoDialog`).

Ayrica **eylemlerin etkinligi yanlis kosula baglanmisti**: "Diski yedekle"
yalnizca acik bir goruntu varken etkindi. Kullanici agacta bir fiziksel disk
sectiginde menu pasif kaliyor, once diski acmasi gerekiyordu — oysa yedek
almak icin diski acmaya gerek yok, okumak yeter.

## Karar

### 1. Tek pencere: `ui/dialogs/backup.py`

Yedek alma, geri yukleme ve **yedek dosyasini inceleme** ayni formda:

```
Islem: (•) Yedek al   ( ) Geri yukle
┌ Yedek dosyasi (.dub) ─────────────────────────────┐
│ [yol.....................................] [Sec...] │
│ Kaynak boyut / Yedek boyut / Olusturma /  │ Icerik │
│ Sikistirma / Dosya sistemi                │ (agac) │
│ Not: [...................] 26/320 bayt  [Notu kaydet] │
└────────────────────────────────────────────────────┘
┌ Disk / bolum ─────────────────────────────────────┐
│ [bolum haritasi]                                   │
│ Acik goruntuler / Fiziksel diskler (agac)          │
└────────────────────────────────────────────────────┘
Secenekler: sikistirma · silme onayi · sistem diski adi
[durum satiri] [ilerleme cubugu]         [Baslat] [Kapat]
```

Kaynak ve hedef **pencerenin icinde** secilir; acik goruntuler ve fiziksel
diskler tek agacta durur. Geri yuklemede "Yeni goruntu dosyasi..." de bir
hedeftir — mevcut hicbir diske dokunmadan yedegi acmanin yolu budur.

Ilerleme ayri bir pencere degil, formun altindaki cubuktur; sonuc da orada
yazar. Boylece islem **izlenebilir** kalir.

### 2. Yikici onay formun **icinde**

CLAUDE.md yikici islemler icin onay ve sistem diskinde "kullanici disk adini
yazarak dogrular" der. Bu kural ayri pencere gerektirmez; onay formun icinde
alinir:

* "Hedefteki butun veriler silinecek; bunu anliyorum" kutusu isaretlenmeden
  **Baslat etkin olmaz**.
* Hedef sistem diskiyse ayrica disk adi yazilir; yazilana kadar Baslat pasif
  kalir ve nedeni dugmenin ipucunda gorunur.
* Bagli bolum, eksik disk bilgisi, yazma korumasi ve "hedef cok kucuk"
  durumlari **satir olarak** gorunur; islem baslamaz.

Kazanc yalnizca pencere sayisi degil: kullanici onayi verirken hedefi,
kaynagi, boyutlari ve uyarilari ayni ekranda gorur. Eskiden onay kutusu
acildiginda hedef haritasi ekranda degildi.

Yedek **almak** yikici degildir (kaynak salt okunur acilir, tek yazilan yer
yedek dosyasidir), bu yuzden orada onay kutusu istenmez.

### 3. Yedek dosyasina kullanici notu

`.dub` basliginin bos alanina 320 baytlik bir **not** alani eklendi
(ofset 136, bkz. `specs/dub.md`). Not:

* yedek alinirken yazilir,
* var olan bir yedekte `clone.write_remark()` ile **veriye dokunmadan**
  degistirilebilir,
* yedegi acan herkes tarafindan gorunur — yanina ayri bir metin dosyasi
  tasimak gerekmez.

Surum numarasi **artirilmadi**. Eski yedeklerde alan sifirdir ve not "yok"
okunur; yeni yedekler eski surumlerde de acilir. Surum artsaydi eski surum
yeni dosyayi tumden reddederdi — oysa degisen sey, okunmadiginda hicbir sey
kaybettirmeyen bir etiket.

### 4. Sikistirma duzeyi secilebilir

Arayuzde dort secenek: Yok / Hizli / Normal / Yuksek (zlib 0/1/6/9).
"Yok" sikistirmayi kapatir ama **sifir bloklar gene yer kaplamaz** — bicimin
asil kazanci oradadir.

### 5. Eylemlerin etkinligi duzeltildi

"Diski yedekle" ve "Diski geri yukle" artik **acik goruntu ya da bilinen bir
fiziksel disk** varken etkin. Kaynak pencerede secildigi icin menunun acik
oturum sarti kosmasi anlamsizdi.

## Sonuclar

* `ui/dialogs/tools.py` icindeki `BackupInfoDialog` **kaldirildi** — yerini
  ayni formdaki bilgi/icerik alani aldi.
* `main_window` icindeki `backup()`, `restore()`, `_restore_to_physical()`,
  `_restore_to_new_image()` ve `show_backup_info()` akislari tek bir
  `open_backup_dialog()` cagrisina indi (~200 satir eksildi).
* Olculdu (Linux misafiri): `tests.run_all` t39 notun yazilmasini,
  sonradan degistirilmesini, bayt kirpmasinin cok baytli karakteri bolmemesini
  ve sikistirma duzeylerinin dosya boyutunu degistirmesini dogrular;
  `tests.ui_smoke` pencerenin iki kipini cizer, onay kutusu isaretlenmeden
  geri yuklemenin etkin olmadigini denetler ve **gercek bir yedek alir**
  (notun dosyaya yazildigi dogrulanir).
