# ADR 0036 — Sag tik menusundeki her islem ait oldugu dugumde durur

**Tarih:** 2026-09-18
**Durum:** Kabul edildi
**Ilgili:** ADR 0026 (disk genel bakisi ve agac duzeni), ADR 0035 (acilista
hazir ekran)

## Baglam

Kullanici sordu: *"Fiziksel diskler uzerine sag tik menusu, ilgili diskin
uzerinde gorunmesi daha mantikli olmaz mi?"*

Agactaki **"Fiziksel Diskler"** satiri bir kategori basligidir; hicbir diski
temsil etmez. Buna ragmen sag tik menusu su girisleri gosteriyordu:

* MBR bolum tablosu olustur
* GPT bolum tablosu olustur
* Goruntu boyutunu degistir...
* Yenile

Diskin **kendi** satirinda ise yalnizca "Bu diski kapat / Disk bilgisi /
Fiziksel diskleri yenile" vardi — yani asil disk islemleri yanlis yerdeydi.

Bu yalnizca duzen sorunu degildi. O girisler **etkin kaynak** uzerinde
calisir. Kategori basliginda hedef diye bir sey olmadigi icin islem, o sirada
acik olan kaynaga gidiyordu: sdb acikken baslikta "GPT olustur" demek sdb'nin
tablosunu siliyordu. Kullanici hangi diske tikladigini gormedigi bir islemi
onaylamis oluyordu.

"Goruntu boyutunu degistir" ise fiziksel disk icin anlamsizdir — diskin boyutu
donanimdir.

## Karar

Her giris ait oldugu dugume tasindi:

| Dugum | Menu |
|---|---|
| **Fiziksel Diskler** (kategori) | Fiziksel diskleri yenile · (yetki yoksa) Yetki al |
| **Disk satiri** (`phys`) | Disk bilgisi · MBR/GPT bolum tablosu olustur · Bolum tablosunu sil · Diski yedekle/geri yukle · Bu diski kapat · Fiziksel diskleri yenile |
| **Acik goruntu** (`session`) | Bu goruntuyu kapat · MBR/GPT olustur · Bolum tablosunu sil · **Goruntu boyutunu degistir** · yedekle/geri yukle · Yenile |
| Bolum / bos alan | degismedi |

Iki kural bunu guvenli kilar:

1. **Sag tiklanan disk etkin kaynak olur.** Menu acilmadan once o disk (gerekirse)
   salt okunur acilir ve etkin kaynaga alinir. Boylece menudeki islem her
   zaman **tiklanan** diske gider; "baska diske gitti" durumu kokten kalkar.
2. **Goruntu boyutu yalnizca dosyada gorunur.** Fiziksel disk oturumunda bu
   giris hic eklenmez.

Diski sag tikla acmak yeni bir risk degildir: bolume sag tiklamak zaten
aciyordu ve ADR 0035'ten beri sol tik da aciyor. Acma her zaman salt
okunurdur; yazma yetkisi yalnizca Uygula aninda alinir (ADR 0025).

## Sonuc

Duman testi menu iceriklerini `QMenu.exec_` yakalayarak dogrular (pencere
acilmaz, aygita dokunulmaz):

* kategori basliginda **"bolum tablosu"** gecen hicbir giris yok, "yenile" var;
* acik goruntu dugumunde MBR, GPT, "Goruntu boyutunu degistir" ve yedekleme
  girisleri var.

Fiziksel disk satiri gercek aygitlarla olculdu (Mint 22.3, root, offscreen):
`/dev/sdb` ve `/dev/sda` satirlarinda sekiz giris de yerinde ve menu
acildiginda etkin kaynak **tiklanan disk** oldu (`etkin kaynak: sdb`,
ardindan `sda`).
