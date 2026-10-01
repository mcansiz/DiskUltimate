# 0062 — HFS+ / HFSX yazma (saf Python)

Tarih: 2026-09-29
Durum: **uygulandi** — ana makinede goruntu dosyalariyla; fsck.hfsplus
(hfsprogs 540.1) ve 7z ile dogrulandi. **macOS'ta sinanmadi.**
Ilgili: [0060](0060-hfsplus-okuma.md), [0061](0061-hfsplus-bicimlendirme.md)

## Karar

`core/hfswrite.py` — `HfsWriter`: `mkdir`, `write_file` (uzerine yazma),
`remove` (ozyinelemeli), `rename` (ad ya da yol: klasorler arasi tasima),
`flush`. `filesystem.HfsAccess` artik yazilabilir; arayuz yolunda her
islemden sonra `flush` (dosya gezgini flush'i yalnizca oturum kapanirken
cagiriyor; arada bitmap ve baslik eski kalmasin).

### B-agaci (katalog, kapsam tasmasi, oznitelik — tek genel kod)
* Ekleme: yaprakta sirali; tasarsa bayt olarak ortadan bolunur, sag dugumun
  ilk anahtari ust dugume; kok bolunurse yeni kok. Ust anahtar her zaman
  cocugun ilk anahtari.
* Silme: bos yaprak serbest, kardes baglari ve ust kayit duzeltilir, tek
  cocuklu kok coker. Birlestirme yok (HFS+ gerektirmez; fsck kabul eder).
* Dugum haritasi baslik + harita dugumleri; bos dugum yoksa agac dosyasi
  kume boyu kadar buyur. Katalog/oznitelik dosyasi 8 kapsami asarsa tasma
  agacina yazilir (dolu, parcali birimde olculdu); tasma agacinin kendisi
  tasamaz (TN1150) -> acik hata.

### Siralama: Apple katlama tablosu *uretilir*
HFS+ adlari FastUnicodeCompare ile siralanir; tablo tutmazsa fsck "invalid
key order" der. Apple'in tablosu (APSL) projeye **kopyalanmadi**:
`core/hfsunicode.py` tabloyu kuralla uretir (sayfa kumesi, Unicode 3.2,
onceden birlesik harfler katlanmaz, Gurcuce 2.0 eslemesi, esi 3.0+ ile
tanimlanan 27 harf, yok sayilan bicim karakterleri). fsck_hfs ikilisinden
gelistirme sirasinda cikarilan tabloyla **65 536 girisin tamami ayni**;
t61 uretilen tablonun SHA-1'ini sabitler (Python surumu degisse de).
Adlar Unicode 3.2 NFD ile saklanir (Apple'in disarida biraktigi araliklar
haric).

### Birim kurallari
* Her kayit icin iplik kaydi; klasor valence'i; baslikta dosya/klasor
  sayilari, sonraki kimlik, bos blok, writeCount, degistirme tarihi.
* Degisiklik basinda "temiz kapatildi" biti kalkar, `flush`ta geri gelir
  (macOS'un baglama davranisi) — yarida kalan islem fsck'a zorlanir.
* Ayirma: once `nextAllocation` ipucunda tam yer, sonra bayt duzeyinde
  bitisik arama (hizli), sonra parcali (en fazla kapsamlar tasmaya).
* Reddedilenler: kirli gunluk, temiz kapatilmamis birim, yazilim/donanim
  kilidi (xorriso birimleri kilitli). Sabit bagli dosya silme/tasima acik
  hata. Temiz gunluklu birime yazilir (gunluk bos kalir; macOS oynatacak
  bir sey bulmaz).

## Bulunan hatalar
* Bicimlendirici birim boyu blok kati degilse son blogu bos birakiyordu;
  dosya onu alinca fsck "Invalid extent entry". mkfs gibi son blok her
  zaman dolu.
* `rename(p, "/x")` kokteki hedef yolu ad saniyordu.

## Dogrulama
* Stres (`.tmp/hfs/stress.py`): 64M/300M/20M/12M/13.3M, HFS+ ve HFSX,
  3000-6000 islem; 3 duzeyli katalog, katalog buyumesi (katalog tasma
  kayitlari dahil), dolu birimde geri alma, 8+ parcali dosyalar (300+
  tasma kaydi), 200 klasorler arasi tasima, her seyi ozyinelemeli silme.
  Her asamada fsck.hfsplus "appears to be OK", okuyucu birebir; hepsi
  silinince sayaclar 0, bos blok = yeni birim - bir katalog kumesi.
* mkfs'in gunluklu birimine 400 dosya: 7z ile disari alinan icerik birebir
  (fsck'in "minor repair" uyarisi dokunulmamis birimde de var: Linux mkfs
  gunluklu birime "10.0" yaziyor).
* t61 (her platformda; fsck varsa onunla).

## Sinanmayan
* macOS ve Linux cekirdek surucusu (ana makinede baglama yok).
* Sabit bag ve sikistirilmis dosya yazma (bilerek kapsam disi).
