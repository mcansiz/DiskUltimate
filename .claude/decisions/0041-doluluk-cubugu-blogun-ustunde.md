# 0041 — Doluluk cubugu blogun ustunde, blok zemini acik

Tarih: 2026-09-21
Durum: kabul edildi

## Sorun

Bolum haritasinda her blok bastan asagi dosya sistemi rengine boyaniyor,
doluluk ise blogun altinda kucuk bir cubukla gosteriliyordu. Kullanicinin
degerlendirmesi: "guzel degil".

Sorun renklerin tonunda degil **yerlesimde**: doygun renkli bir zemin
uzerindeki kucuk cubuk ayni renk ailesinden oldugu icin zeminle yarisiyor,
yazi da renkli zemin uzerinde kalinca harita gurultulu goruluyor. Bes bolum
yan yana geldiginde ekranin tamami renk oluyor.

## Karar

Ornek alinan araclarin (`example gui/`: EaseUS Partition Master, Macrorit
Partition Expert, Acronis Disk Director) uculunun de ortak duzeni benimsendi:

- **Blok zemini acik** — paletin `Base` rengi, altta hafif degrade. Koyu
  sistem temasinda kendiliginden koyu panel olur.
- **Doluluk cubugu blogun ustunde**, blok genisliginde ve kalin (19 px);
  yuzde cubugun ortasinda yazar.
- **Renk yuku cubukta ve sol kenar seridinde.** 4 px'lik sol serit dosya
  sistemi rengini tasir, boylece "hangi bolum ne" bilgisi renkten okunmaya
  devam eder.
- **Yazi paletten gelir** (baslik ve boyut `Text`, dosya sistemi adi `dim`);
  renkli zemine gore kontrast hesaplamak gerekmez.

Cubuk rengi: esigin altinda dosya sisteminin kendi tonu (daha doygun),
**%75'ten sonra kehribar, %90'dan sonra kirmizi**. Ara tonlarda karisim
denendi ve birakildi: mavi/mor/yesil bloklarda kehribar karisimi donuk bir
zeytin tonu veriyor, hem cirkin hem de uyari oldugu anlasilmiyordu.

Doluluk **bilinmiyorsa** (bicimlendirilmemis bolum, MSR, okunamayan birim)
cubuk taramali cizilir ve yuzde yazilmaz — bos cubuk "bombos" diye okunurdu.

Cizim tek yerdedir: `theme.draw_usage_bar()`. Harita blogu ve boyutlandirma
seridi ayni islevi cagirir; eskiden ikisi ayri kod yoluyla ciziliyor ve
birbirini tutmuyordu.

## Alternatifler (gorsel olarak uretilip karsilastirildi)

- **EaseUS tarzi ince cubuk + renkli nokta:** en hafif gorunum, ama yuzde
  cubugun disinda kaldigi icin goz iki yere bakiyor.
- **Renkli blok + ustte tam genislikte serit:** mevcut duzenin evrimi;
  renk yuku yine yuksek kaliyor.
- **Blogun kendisi kapasite (dolu/bos alan):** en dikkat cekici, ama %90
  dolu bir bolumde blogun tamami kirmizi oluyor ve dosya sistemi rengi
  kayboluyor.
- **Renkli blok + kose rozeti:** rozet okunakli ama cubuk hala zeminle
  yarisiyor.

Secim kullaniciya birakildi (onizlemeler `.tmp/onizleme/`); Macrorit duzeni
secildi.
