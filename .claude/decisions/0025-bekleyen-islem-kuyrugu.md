# ADR 0025 — Bekleyen islem kuyrugu, tek "Uygula", cizilen ikon seti

**Tarih:** 2026-09-15
**Durum:** Kabul edildi
**Ilgili:** ADR 0004 (yikici islem guvenligi), ADR 0013 (tema kaldirildi),
ADR 0014 (fiziksel disk destegi), ADR 0021/0022 (acik aygit, birim kilidi)

## Baglam

Kullanici `example guı/` altina benzer araclarin ekran goruntulerini koydu ve
sunu istedi: *"islemler yapilir, yapilan adimlar uygula butonu ile sirayla
uygulanir, Acronis ornegindeki bayrak butonu gibi. Bizdeki salt okunur
ozelligine gerek kalmaz. Ikonlar da tum platformlarda kullanilabilsin."*

Incelenen dort arac da ayni kalibi kullaniyor:

| Arac | Kuyruk dugmeleri |
|---|---|
| Acronis Disk Director | `Commit pending operations (1)` — damali bayrak |
| EaseUS Partition Master | `Undo / Redo / Apply / Refresh` + "Pending Operations" paneli |
| AOMEI Partition Assistant | `Apply / Discard / Undo / Redo` |
| Macrorit Partition Expert | `Undo / Redo / Commit / Reload Disks` |
| MiniTool Partition Wizard | `Apply / Undo` + "0 Operations Pending" |

Bizim modelimiz tekildi: her tiklama aninda diske yaziyordu ve fiziksel disk
**acilirken** "salt okunur mu, yazma modu mu" diye soruyordu.

## Karar

### 1. Islemler once kuyruga girer (`core/operations.py`)

`OperationQueue` sirali adimlar tutar. Adim eklemek **diske dokunmaz**; adimlar
serbestce cikarilabilir, yeniden siralanabilir, tumden iptal edilebilir.
`apply()` hepsini sirayla calistirir.

Kuyruklanan islemler: bolum tablosu olustur/sil/donustur, bolum olustur/sil/
bicimlendir/boyutlandir, etiket, bolum adi, bolum turu, onyukleme bayragi,
guvenli silme, bos alan silme, goruntu boyutu.

**Kuyruklanmayanlar** ve nedeni: dosya gezgini islemleri (kopyala/sil) bir
dosya sisteminin **icinde** olur, yerlesimi degistirmez — Acronis de "Browse
files" islemini kuyruklamaz. Yedekleme, klonlama ve kurtarma da kendi
ilerleme pencereleriyle dogrudan calisir.

### 2. Salt okunurluk **normal kip** oldu

Kullanicinin sezgisi dogruydu, ama "gerek kalmaz" biraz fazla: salt okunurluk
**kip secimi** olmaktan cikti, **yetenek bilgisi** olarak kaldi.

- Fiziksel disk **her zaman** salt okunur acilir. Acilista yazma modu sorusu,
  sistem diski onayi ve bagli bolum uyarisi **kalkti** — hepsi artik uygulama
  aninda sorulur.
- `DiskSession.become_writable()` kaynagi o anda yazma moduna alir; fiziksel
  diskin butun koruma katmanlari (ADR 0014) orada calisir.
- `can_become_writable()` gecilemeyecek kaynaklari nedeniyle bildirir:
  `.dub` yedegi bir arsivdir, VDI/QCOW2 bu surumde yazilamaz, seyrek VMDK
  salt okunurdur. Bunlar icin arayuz "DEGISTIRILEMEZ" der ve **nedenini**
  yazar.

Kazanc yalnizca sadelik degil: yazma modunda acik duran bir disk, hicbir sey
yapilmasa bile birimleri kilitli tutuyordu ve bu ADR 0022'deki hatanin
zeminiydi. Artik kilit yalnizca uygulama suresince var.

### 3. Yikici islem onayi **parti basina**

CLAUDE.md kurali "yikici islemler oncesi onay diyalogu zorunludur" diyordu ve
bu, islem basina bir kutu demekti. Uc bolum olusturup ikisini bicimlendiren
kullanici bes kutu goruyor, besinciyi okumuyordu.

Onay kalkmadi, **yeri** degisti: Uygula tek bir pencere acar, **butun adimlari
numarali listeler**, yikici olanlari kalin gosterir ve kacinin veri
kaybettirebilecegini sayar. Daha az tiklama, daha cok bilgi.

### 4. Kismi basari gizlenmez

`apply()` bir adim patlarsa **durur** — sonraki adimlar cogu zaman oncekinin
sonucuna dayanir. Tamamlananlar geri alinmaz (bicimlendirilmis bolum geri
gelmez) ve bu acikca soylenir; duran adim ve sonrasi kuyrukta kalir ki
kullanici duzeltip yeniden deneyebilsin.

### 5. Ikonlar cizilir (`ui/icons.py`)

Uc yol degerlendirildi:

| Yol | Sonuc |
|---|---|
| Sistem `QStyle` ikonlari | **Yetersiz.** Platforma gore bambaska gorunur; "bicimlendir", "boyutlandir", "onyukleme bayragi" gibi islemlerin karsiligi yok. |
| SVG (`PyQt5.QtSvg`) | **Elendi.** Ubuntu 24.04'te ayri pakettir ve kurulu degildi (olculdu: `ImportError`). Harici bagimlilik yok kurali geregi kullanilamaz. |
| `QPainter` ile cizim | **Secildi.** Uc platformda ayni gorunur, bagimlilik yok, her boyutta keskin. |

34 ikon, 16 birimlik izgarada (`u = size / 16`) cizilir. Cogu **taban + rozet**
bilesimidir (bolum serviti + yesil arti = yeni bolum, kirmizi carpi = sil),
boylece 16 pikselde bile ayirt edilir. Notr cizgiler paletten alinir; anlamsal
renkler (yesil/kirmizi/turuncu) sabittir ve koyu temada acilir — dosya sistemi
renklerindeki kural (ADR 0013) burada da gecerli.

Arac cubugu ayrica sadelestirildi: eskiden 14 dugme vardi ve pencere `>>`
tasma okuna dusuyordu; simdi kuyruk dugmeleri + dort bolum islemi + iki
gorunum dugmesi var, kalani menulerde.

#### Ikinci tur: butun ikonlar cizime gecti, boyutlar buyudu

Ilk turda yalnizca ana pencere donusturulmustu; **dosya gezgini** hala
`QStyle` ikonlari kullaniyordu, yani o bolum platforma gore degisiyordu.
Kalan on iki ikon da cizildi (`up`, `export`, `import`, `folder`,
`folder-add`, `folder-new`, `file`, `trash`) ve `theme.standard_icon()`
**kaldirildi** — artik uygulamada hicbir `QStyle` ikonu yok. Toplam 42 ikon.

Boyutlar buyutuldu: ana arac cubugu 18 -> **32 px**, dosya gezgini 16 -> 20,
agaclar 20, bolum tablosu renk cipleri 11 -> 13. Arac cubugu 32 px ikonla
866 px genislik ister; 1024 px pencerede bile tasma oku cikmaz (olculdu).

Ikonlar artik **cok boyutlu** uretiliyor: her `QIcon` 16/20/24/32/48 px
pixmap tasir. Tek pixmap tutup Qt'ye olcekletmek bulaniklik uretiyordu —
arac cubugu 24 isterken elimizde 18 piksellik goruntu vardi.

#### "Her platformda ayni" — olculdu

`icons.digest()` butun cizimlerin **piksel** ozetini verir. Sonuc:

```
Windows 10 · Qt 5.15.2  · windowsvista   1ca31e30c5d79d2e0ed72a2132810cdd
Ubuntu 24.04 · Qt 5.15.13 · fusion        1ca31e30c5d79d2e0ed72a2132810cdd
```

Olcum bir kez yanlis kuruldu ve duzeltildi: ilk surum **PNG baytlarini**
karsilastiriyordu ve ayni makinede bile `offscreen` ile `windows` eklentisi
farkli deger uretiyordu — cunku `QPixmap` bicimi ekrana gore secilir. Piksel
degerleri aynidir; bu yuzden karsilastirma once sabit bir bicime (`ARGB32`)
cevirir. Yanlis olcum "platformlar farkli" dedirtiyordu; dogru olcum
aksini gosterdi.

## Arayuz duzeni

```
[Uygula (3)] [Geri al] [Vazgec] | [Yeni bolum] [Bicimlendir] [Boyutlandir] [Sil] | [Disk bilgisi] [Yenile]
+-----------------------+-------------------------------------------+
| Disk ve Bolumler      |  disk haritasi                            |
|  agac                 |  bolum tablosu  (bekleyen bolumler ⏳)     |
+-----------------------+  sekmeler                                 |
| Bekleyen islemler (3) |                                           |
|  1. Bolum 1 bicimle.. |                                           |
|  2. Bolum 1 onyukle.. |                                           |
|  3. Bolum 2 sil       |                                           |
+-----------------------+-------------------------------------------+
```

Bekleyen adimi etkileyen bolumler tabloda **italik + kum saati** gosterilir;
ipucu o bolumdeki tum bekleyen adimlari listeler. Boylece plan yalnizca yan
listede degil, verinin yaninda da gorunur.

## Duzeltme — kuyruga girmeyen yazmalar (2026-09-15, ayni gun)

Ilk surumde bir acik kalmisti: kaynak artik **her zaman** salt okunur aciliyor,
ama **kuyruga girmeyen** islemler (geri yukleme, klonlama) hala yazilabilir bir
oturum bekliyordu. Linux'ta bir `.dub` yedegini `/dev/sdb` diskine yazma
denemesi "SALT OKUNUR acilir" hatasiyla dusuyordu.

- `restore()` artik once `_make_writable()` cagirir; fiziksel diskin sistem
  diski ve bagli bolum kapilari orada calisir.
- Kayip bolumu tabloya ekleme kuyruga alindi.
- Yaniltici metinler duzeltildi: `readonly_reason` artik var olmayan "yazma
  modunda ac" secimini onermiyor; agacta `[salt okunur]` yerine yalnizca
  gercekten degistirilemeyen kaynakta `[degistirilemez]` yazar.

**Ikinci ornek (ayni gun):** dosya gezgini de kacmisti. Dosya islemleri
kuyruga girmedigi icin `fs.writable` hep `False` kaliyor, "Dosya ekle" dugmesi
hep pasif oluyordu — fiziksel diske dosya eklemek hic mumkun degildi (Linux
Mint, kullanici bildirimi). Cozum: `FileBrowser.ensure_writable` geri
cagirmasi ilk yazma denemesinde yetkiyi alir ve **taze** dosya sistemi
dondurur. Yazma dugmeleri de artik pasif degil: pasif dugme "bu is hic
yapilamaz" der ki dogru degil.

Bu arada bir guvenlik metni duzeltildi: bagli bolum uyarisi her platformda
"bu bolumler gecici olarak cikarilacak" diyordu; bu **yalnizca Windows'ta
dogru**. Linux/macOS'ta hicbir sey cikarilmaz ve bagli bir dosya sistemine
ham yazmak onu bozar. `physical.locks_volumes_on_write()` ile metin artik
platformun gercegini soyluyor.

Ders: bir kip secimini kaldirirken, o kipe **dolayli** bagli olan yollari da
taramak gerekir. Arayuzdeki butun dogrudan yazan cagrilar tarandi; geriye
yalnizca `recover_deleted` kaldi ve o oturuma degil yerel klasore yazar.
Regresyon `t26_dogrudan_yazma_yollari` ile kapatildi.

## Sinir — dürüst kalan nokta

Bekleyen durum **isaretlenir, simule edilmez**. Yani "bicimlendir" adimi
kuyruktayken tabloda dosya sistemi hala eskisini gosterir, yaninda kum saati
durur. Acronis sonucu haritada onizler; bunu yapmak icin bolum tablosunu ve
dosya sistemlerini bellekte bir gölge aygit uzerinde calistirmak gerekir —
ayri ve buyuk bir is. Yanlis bir onizleme gostermektense isaretlemek yeglendi.

## Dogrulama

- `t25_islem_kuyrugu`: kuyruga eklemek diski **degistirmiyor** (sha256 ayni),
  adimlar sirayla isliyor, basarisiz adim kuyrugu durduruyor ve duran adim
  listede kaliyor, salt okunur kaynakta uygulama reddediliyor, `.dub` yedegi
  yazma moduna gecemiyor ve nedenini soyluyor.
- `ui_smoke`: kuyruk ekleme/isaret/geri alma/iptal denetleniyor, kuyruk
  dolarken goruntu dosyasinin boyutu degismiyor, 34 ikonun hepsi ciziliyor.
  Ekran goruntusu: `21-bekleyen-islemler.png`, ikon tabakasi
  `20-ikon-seti.png`.
- Windows: `run_all` 25/25, `platform_check` 0 bulgu, `diag_check` 13/13.
- Linux: `run_all` 23/25 + 2 atlandi (Windows dali), `platform_check` 0 bulgu,
  `ui_smoke` tamam.
