# ADR 0033 — Kuyruktaki adimlarin hedefi bolum numarasi degil, baslangic LBA'sidir

**Tarih:** 2026-09-17
**Durum:** Kabul edildi
**Ilgili:** ADR 0025 (bekleyen islem kuyrugu), ADR 0031 (plan onizlemesi ve
uygulama penceresi)

## Baglam

Kullanici Linux misafirinde `/dev/sdb` uzerinde iki bolumu silmek istedi:
"Bolum 1 sil" ve "Bolum 2 sil" adimlarini kuyruga aldi, Uygula dedi. Birinci
adim gecti, ikincisi durdu:

```
17:05:13 kuyruga eklendi (1): Bolum 1 sil — DU NTFS, 8.37 GB
17:05:16 kuyruga eklendi (2): Bolum 2 sil — Bolum 2, 1.63 GB
17:05:20 WARNING operations.step(kind=delete) — 0 ms
         HATA PartitionTableError: 2 numarali bolum yok
17:05:20 uygulama sonucu: 1 adim uygulandi, 'Bolum 2 sil' adiminda durdu
```

Neden: **bolum numarasi kalici bir kimlik degildir.** Numara tabloda saklanmaz;
tablo okunurken yerlesime gore bastan verilir (`gpt.py`: kullanilan girisler
sirayla 1, 2, 3...). Birinci bolum silinince ikinci bolum **1 numara** olur ve
kuyrukta bekleyen "2 numarali bolumu sil" adiminin hedefi ortadan kalkar.

Bu yalnizca silmeye ozgu degil. Numarayi kaydirabilecek her adim ayni sorunu
uretir:

| Adim | Numaralara etkisi |
|---|---|
| bolum sil | sonrakiler bir asagi kayar |
| yeni bolum | diskte **onune** dusuyorsa sonrakiler bir yukari kayar |
| boyutlandir (tasima ile) | siralama basi degisirse numaralar yer degistirir |
| tablo olustur / sil, disk guvenli sil | butun numaralar gider |

Yani hata tek bir senaryonun kusuru degil, kuyrugun **kimlik modelinin** kusuru.

## Secenekler

1. **Adimlari numara kaymayacak sirada calistirmak** (orn. silmeleri yuksek
   numaradan basa dogru). Kullanicinin verdigi sirayi bozar, yalnizca silmeyi
   kurtarir, "yeni bolum" ve "tasima" durumlarini cozmez.
2. **Her adimdan sonra kuyruktaki numaralari kaydirmak.** Her adim turu icin
   ayri kaydirma kurali yazmak gerekir; bir kural unutuldugunda hata sessizce
   **yanlis bolume** islem yapmaya doner. Bu, durmaktan kotudur.
3. **Kalici bir kimlik kullanmak.** Numara yerine degismeyen bir olcut.

## Karar

**3 secildi: hedef `at_lba` — bolumun baslangic sektoru.**

Iki bolum ayni sektorde baslayamaz; baslangic LBA'si o tabloda tekildir ve
bolum tasinmadikca degismez. Adimlar hedefi hem numara (gosterim icin) hem de
LBA (kimlik icin) olarak tasir:

* `operations.resolve_target()` her adimi calistirmadan **once** capayi o anki
  bolum listesinde arar ve `params["index"]` alanini tazeler.
* Capa bulunamazsa adim **durur**: *"Hedef bolum bulunamadi (LBA {});
  yerlesim degismis olabilir"*. Yanlis bolume islem yapmaktansa durmak
  yeglenir — burada "yanlis bolum" baskasinin verisi demektir.
* Bir bolum tasindiginda (`resize` baslangici degistirdiginde) kuyruktaki
  sonraki adimlarin capalari yeni yere tasinir (`_follow_move`).
* Capasiz adimlar (eski kuyruklar, testler) eskisi gibi numaradan calisir;
  davranis degismez.

Onizleme de ayni olcutu kullanir (`planview.project`): ekranda gorunen ile
uygulanan **ayni bolum** olmalidir, yoksa onizleme guven vermez.

### Gosterilen numara degismez

Adimin basligi kuyruga alindigi andaki numarayi gosterir ("Bolum 2 sil").
Uygulama sirasinda o bolum 1 numaraya dusse bile baslik degismez — kullanici
adimi eklerken neye tikladiysa onu gorur. Degistirilen sey yalnizca icerideki
hedef cozumudur; gunluge "adim hedefi tazelendi: LBA 4096 -> bolum 1" diye
yazilir.

### Onizlemede numaralar neden yeniden verilmiyor

Uygulamadan sonra disk numaralari bastan verecektir; onizleme bunu **taklit
etmez**, bolumleri eski numaralariyla gosterir. Nedeni secim guvenligidir:
onizleme satirlari ana penceredeki secimle ayni numara uzayini paylasir; plan
numaralari yeniden verse, "Bolum 1" satirina tiklamak gercek tabloda **baska
bir bolumu** secerdi. Numaralarin uygulamadan sonra oturmasi kabul edilir,
yanlis bolumu secmek edilmez.

## Sonuc

`tests.run_all` icine `t40_kuyrukta_bolum_numarasi_kaymasi` eklendi; kullanicinin
karsilastigi senaryo (iki silme adimi) dahil alti durum sinaniyor. Capa
cozumleyicisi kapatilinca test **kullanicinin gordugu hatayla** basarisiz
oluyor (dogrulandi: *"1 adim uygulandi, 'Bolum 2 sil' adiminda durdu: 2 numarali
bolum yok"*), acikken geciyor.
