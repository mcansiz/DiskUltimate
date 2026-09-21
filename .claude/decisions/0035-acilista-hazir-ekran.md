# ADR 0035 — Acilista hazir ekran: ilk disk secilir, disk satiri tiklanabilir

**Tarih:** 2026-09-17
**Durum:** Kabul edildi
**Ilgili:** ADR 0014 (fiziksel disk destegi), ADR 0025 (salt okunurluk normal
kiptir), ADR 0026 (disk genel bakisi ve bolum yoklamasi)

## Baglam

Kullanici bildirdi: *"uygulama ilk acildiginda partitionlara tiklamayinca
hicbir sey secemiyorum; partitionlara tiklayinca [hazir ekran] gorunuyor. Buna
gerek yok, direkt oyle baslayabilir. Ilk hali bence bug gibi duruyor."*

Hakliydi. Acilistaki durum suydu:

* Bolum tablosu **bos**, harita genel bakista, bilgi paneli metin.
* Arac cubugunun neredeyse tamami **pasif** (Yeni bolum, Bicimlendir,
  Boyutlandir, Sil, Yedekle...).
* Agacta bir **diske** tiklamak yalnizca bilgi panelini dolduruyordu; ekran
  yine olu kaliyordu.
* Bir **bolume** tiklamak ise diski salt okunur aciyor ve her sey
  canlaniyordu.

Yani "calisir hale gelmek" icin kullanicinin bolume tiklamasi gerekiyordu ve
bunun kesfedilecek bir yani yoktu. Diskin kendisine tiklamanin diski acmamasi
da tutarsizdi.

## Karar

### 1. Disk satirina tiklamak diski acar

`_tree_clicked` icinde `phys` satiri artik bolum satiriyla ayni seyi yapar:
disk **salt okunur** acilir ve ekran o diske gecer. Disk zaten acikken
tiklamak onu etkin kaynak yapar (eski davranis), etkin kaynak zaten oysa
bilgi panelini tazeler.

Guvenlik acisindan yeni bir sey yoktur: bolume tiklamak zaten ayni seyi
yapiyordu, acma her zaman salt okunurdur ve yazma yetkisi yalnizca Uygula
aninda alinir (ADR 0025).

### 2. Acilista ilk **uygun** disk secilip acilir

Ilk disk taramasi bittiginde, acik bir kaynak yoksa ilk uygun disk salt
okunur acilir. Olcut acilabilirliktir, buyukluk ya da tur degil
(`MainWindow.first_openable_disk`):

* bilgisi eksik okunan disk (yetki yok) **atlanir**,
* bolum tablosu okunamamis disk (`survey.error`) **atlanir**,
* kalan ilk disk secilir — sira isletim sisteminin sirasidir (sda, sdb...).

Uygun disk yoksa hicbir sey acilmaz; ekran eski haliyle kalir.

Uc kural bu davranisi zararsiz tutar:

1. **Bir kez calisir.** Kullanici diski kapattiginda pencere onu yeniden
   acmaya kalkmaz — yoksa "kapat" dugmesi ise yaramaz gorunurdu.
2. **Sessiz basarisizlik.** Acilmazsa gunluge yazilir, hata penceresi
   acilmaz: acilista, kullanicinin istemedigi bir isin bedelini ona
   odetmek dogru degildir.
3. **Cizimden sonra.** `QTimer.singleShot(0, ...)` ile siraya alinir; pencere
   once gorunur, sonra disk acilir.

### Neden "acmadan yalnizca gostermek" degil

Bolum yoklamasi (ADR 0026) zaten tabloyu okuyor; tabloyu **acmadan** da
doldurabilirdik. Reddedildi: o zaman ekran dolu gorunur ama butun islemler
pasif kalirdi — kullanicinin sikayet ettigi "olu ekran" duygusu, bu sefer
daha da kafa karistirici bir bicimde surerdi. Acilan disk gercekten
calisilabilir bir kaynaktir.

### Neden sistem diski de secilebiliyor

Sira isletim sisteminin sirasidir ve cogu makinede ilk disk sistem diskidir.
Salt okunur acmak zararsizdir; sistem diskinin butun koruma katmanlari
(yazma onayi, disk adini yazarak dogrulama) yazma anindadir ve degismedi.
"Sistem diskini atla" kurali, tek diskli makinelerde ekranin yine bos
kalmasi demek olurdu.

## Sonuc

Gercek aygitlarla olculdu (Mint 22.3, root, offscreen):

```
gorulen disk    : ['sda', 'sdb']
acilan oturum   : sda           salt okunur: True
sema / bolum    : GPT / 3 bolum tablo satiri: 3
secili bolum    : 1             harita kipi: 1 (tek disk)
etkin islemler  : ['Yeni bolum...', 'Bicimlendir...', 'Bolumu sil',
                   'Diski yedekle...']
durum cubugu    : Secili: Bolum 1 — Bolum 1 (1.00 MB)
```

Acma taramadan sonra ~70 ms surdu; donma yakalayici hicbir sey bildirmedi.

Duman testinde secim **politikasi** ayrica sinanir (`first_openable_disk`):
bilgisi eksik ve tablosu okunamayan diskler atlanir, uygun disk yoksa `None`
doner, ikinci cagri hicbir sey yapmaz.
