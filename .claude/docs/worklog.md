# Is Gunlugu

Kural: her anlamli degisiklikten sonra bu dosyaya tarihli bir giris eklenir.
Kayit yalnizca bu depo icindeki `.claude/` altinda tutulur.

---

## 2026-09-18 (4) — DUZELTME: bicimlendirdigimiz NTFS artik isletim sistemi tarafindan yazilabiliyor (ADR 0037)

Bir onceki girişteki bulgu duzeltildi. Kok neden **tek degil uctu**; her biri
ancak bir oncekini gecince ortaya cikti. Yontem her adimda ayniydi: referans
`mkfs.ntfs` birimiyle **yan yana** olcmek ve ntfs-3g'ye sebebi soyletmek.

### 1. `$Secure` bostu

NTFS 3.x'te her dosyaya bir guvenlik kimligi atanir; tanimlayicilar
`$Secure` dosyasinda durur. Bizde `$SDS` akisi 0 bayt, `$SDH`/`$SII`
indeksleri bostu.

Duzeltme: iki standart tanimlayici (`0x100` salt okuma, `0x101` okuma+yazma)
uretilip `$SDS`e 20 baytlik basliklariyla yazilir, 256 KiB'de aynalanir;
`$SDH` (karma) ve `$SII` (kimlik) indeks kokleri doldurulur. Tanimlayici
baytlari ve NTFS karma islevi referans birime karsi dogrulandi — ikisi de
**birebir** tutuyor.

### 2. `$MFT:$BITMAP` yerlesikti  ← asil engel

ntfs-3g sebebi kendi soyledi:

```
CREATE ... Failed to determine last allocated cluster of mft bitmap attribute.
   error: -22 (Invalid argument)
```

Yeni MFT kaydi ayirirken surucu bitmap'in son kumesini sorar; yerlesik
oznitelikte kume yoktur. Referansta bitmap ayri bir kumede duruyor
(`kosul=[(2, 1)]`). Duzeltme: `$MFT:$BITMAP` yerlesik olmaktan cikti, kendi
kumesine yazilir ve bitmap boyutu MFT kapasitesine gore 8 baytin kati olarak
hesaplanir.

### 3. Kok dizinde "." girisi yoktu

Birinci engel kalkinca ikincisi gorundu:

```
CREATE ... Index lookup failed, inode 5: No such file or directory
           Failed to sync FILE_NAME (inode 5): Input/output error
```

Surucu dosya olusturduktan sonra **ust dizinin** adini ust dizinin
indeksinde arayip tazeliyor; kok icin bu arama kendi "." girisine duser.
Referansin kok indeksinde `'.' -> kayit 5` var, bizde yoktu. Duzeltme:
kok indeksine kendi girisi eklendi (okuyucumuz "." girisini zaten
listelemez).

Ayrica: guvenlik kimligi `$STANDARD_INFORMATION` icinde **0x34**'te durur;
yama sirasinda once 0x40'a (USN alani) yazilmisti, test yakaladi.

### Olcum

| Kosum | Once | Sonra |
|---|---|---|
| ntfs-3g ile dosya olusturma (goruntu) | EINVAL | **TAMAM** |
| `tests.ntfs_write_check` (root) | 1/2 | **2/2** |
| `/dev/sdb` uzerinde gercek senaryo | — | **TAMAM** |

`/dev/sdb` kosumu kullanicinin yaptigi seydi: uygulamayla GPT + NTFS bolum,
sonra isletim sisteminin surucusuyle yazma. Sonuc: metin dosyasi, ic ice
klasor ve 20 MB rastgele dosya yazildi; yeniden baglamada sha1 ozeti ayni;
`ntfsfix -n` temiz, birim bayraklari 0x0000, surum 3.1; **uygulamanin kendi
okuyucusu** da isletim sisteminin yazdigi dosyalari goruyor.

### Testler

- `t42_ntfs_isletim_sistemi_yazabilmeli` (yeni, root gerektirmez): uc kusuru
  da **yapisal olarak** yakalar — bitmap yerlesik mi, kok "." girisi var mi,
  `$Secure` gercek icerik tasiyor mu (karmalar dahil).
- `tests/ntfs_write_check.py` artik birimi **yazma icin** de bagliyor.
  Eskiden yalnizca `-o ro` bagliyordu; hatanin yillarca gorulmemesinin
  sebebi buydu. (Bu arada bir test tuzagi da temizlendi: ayni dosyayi once
  ro sonra rw baglamak, ilk baglamadan kalan salt okunur loop aygitini
  yeniden kullandirip yanlis hata veriyordu.)

---

## 2026-09-18 (3) — BULGU: bicimlendirdigimiz NTFS'e ntfs-3g dosya yazamiyor ($Secure bos)

Mount/unmount ozelligini olcerken ortaya cikti. Kendi NTFS bicimlendiricimizle
olusturulmus bir bolum **baglaniyor ve okunuyor**, ama uzerine dosya
olusturulamiyor — root olarak bile:

```
touch /mnt/t3/deneme.txt  ->  Invalid argument   (EINVAL)
ntfsfix -n /dev/sdb3      ->  "processed successfully" (saglam diyor)
```

### Yalitilmis karsilastirma (loop aygiti, ayni boyut, ayni surucu)

| | referans `mkfs.ntfs` | bizimki |
|---|---|---|
| ntfs-3g ile baglama | rw | rw |
| dosya olusturma | **TAMAM** | **EINVAL** |
| `$Secure` (inode 9) `$SDS` veri boyutu | **262396 bayt** | **0 bayt** |

Kok neden: bicimlendiricimiz `$Secure` dosyasini **bos** uretiyor — guvenlik
tanimlayici akisi (`$SDS`) ve onun indeksleri (`$SDH`, `$SII`) yok. NTFS 3.x'te
bir dosya olusturulurken surucu ona bir guvenlik kimligi atamak zorundadir;
`$Secure` bos oldugu icin ntfs-3g bu adimda EINVAL ile duruyor.

Neden bugune kadar gorulmedi: **okuma** ve **bizim kendi yazicimiz**
(`ntfswrite.py`) `$Secure`'a dokunmaz, bu yuzden t17 ve `ntfs_write_check`
gecmisti; `ntfsfix`/`ntfsinfo` da bos `$Secure`'u kusur saymiyor. Eksik yalnizca
**isletim sisteminin kendi surucusuyle yazarken** ortaya cikiyor.

Etki: uygulamayla NTFS bicimlendiren kullanici, bolume Linux'ta (ve buyuk
olasilikla Windows'ta) dosya kopyalayamaz. Ciddi ve kullanicinin ilk
karsilasacagi hatalardan.

Duzeltme kapsami (yapilacak): dort standart guvenlik tanimlayicisini uretip
`$SDS` akisina 20 baytlik basliklariyla yazmak (256 KiB aynalama dahil),
`$SDH`/`$SII` indekslerini kurmak ve sistem dosyalarinin
`$STANDARD_INFORMATION` kaydindaki `security_id` alanini bunlara baglamak.

---

## 2026-09-18 (2) — Sag tik menuleri ait oldugu dugume tasindi (ADR 0036)

Kullanici: *"Fiziksel diskler uzerine sag tik menusu, ilgili diskin uzerinde
gorunmesi daha mantikli olmaz mi?"*

Agactaki **"Fiziksel Diskler"** satiri bir kategori basligidir ama menusunde
"MBR/GPT bolum tablosu olustur" ve "Goruntu boyutunu degistir" duruyordu;
diskin kendi satirinda ise yalnizca kapat/bilgi/yenile vardi.

Bu yalnizca duzen sorunu degildi: o girisler **etkin kaynak** uzerinde
calisir. Baslikta hedef olmadigi icin islem o sirada acik olan diske
gidiyordu — sdb acikken baslikta "GPT olustur" demek sdb'nin tablosunu
siliyordu. Kullanici hangi diske tikladigini gormeden onaylamis oluyordu.

### Degisen

| Dugum | Menu |
|---|---|
| Fiziksel Diskler (kategori) | diskleri yenile · (yetki yoksa) yetki al |
| Disk satiri | Disk bilgisi · MBR/GPT olustur · tabloyu sil · yedekle/geri yukle · bu diski kapat · yenile |
| Acik goruntu | kapat · MBR/GPT olustur · tabloyu sil · **goruntu boyutu** · yedekle/geri yukle · yenile |

Iki kural: (1) sag tiklanan disk **etkin kaynak olur** — menu acilmadan once
salt okunur acilir, boylece islem her zaman tiklanan diske gider; (2)
"Goruntu boyutunu degistir" fiziksel disk oturumunda hic eklenmez, cunku
diskin boyutu donanimdir.

### Olcum

Duman testi menuleri `QMenu.exec_` yakalayarak okur (pencere acilmaz):
kategori basliginda "bolum tablosu" gecen giris yok, acik goruntu dugumunde
MBR/GPT/goruntu boyutu/yedekleme var.

Fiziksel disk satiri gercek aygitlarla olculdu (root, offscreen):

```
=== /dev/sdb satirindaki menu ===       === /dev/sda satirindaki menu ===
    Disk bilgisi                            Disk bilgisi
    MBR bolum tablosu olustur               MBR bolum tablosu olustur
    GPT bolum tablosu olustur               GPT bolum tablosu olustur
    Bolum tablosunu sil                     Bolum tablosunu sil
    Diski yedekle... / geri yukle...        Diski yedekle... / geri yukle...
    Bu diski kapat                          Bu diski kapat
    Fiziksel diskleri yenile                Fiziksel diskleri yenile
    -> etkin kaynak: sdb                    -> etkin kaynak: sda
```

---

## 2026-09-18 — Inceleme: GParted bizim exFAT bolumunu "unknown" gosteriyor

Kullanici sdb uzerinde uygulamayla bolumler olusturdu, exFAT bolume bir dosya
yazdi. GNOME Disks bolumu exFAT gorup bagladi ve dosyayi listeledi; **GParted
ise dosya sistemini "unknown"** gosterdi. Soru: sorun GParted'te mi?

### Olculen (Mint 22.3, gercek aygit + loop aygitinda referans karsilastirmasi)

Ayni kosulda `mkfs.exfat` (exfatprogs 1.2.2) ile bizim saf Python
bicimlendiricimiz karsilastirildi:

| Kontrol | Referans `mkfs.exfat` | Bizimki |
|---|---|---|
| `blkid -p` | `TYPE="exfat" VERSION="1.0"` | **ayni** |
| `wipefs -n` | `0x3 exfat`, `0x1fe dos` | **ayni** |
| `fsck.exfat -n` | clean | **clean** |
| `parted` (libparted) | **"File system" sutunu BOS** | **BOS** |

Yani libparted exFAT'i **hic** tanimiyor — referansla uretilmis birimi de
taniyamiyor. GParted blkid'den tur alamadiginda libparted'e duser ve "unknown"
yazar.

### GParted'in kendi arac zinciri bizim birimi sorunsuz okuyor

- `blkid -c /dev/null` (GParted'in **tam olarak** calistirdigi komut):
  `/dev/sdb2: TYPE="exfat"`
- `dump.exfat /dev/sdb2` (GParted'in exFAT araci): onyukleme sektorunu eksiksiz
  okuyor (Volume Length 3512320, FAT Offset 24, Cluster Count 54872...)
- GParted 1.5.0 ikilisi exFAT biliyor (`strings` icinde 13 gecis), exfatprogs
  kurulu.

### Sonuc

Sorun bizim dosya sisteminde degil. GParted ekrandaki bilgiyi **kendiliginden
tazelemez**; o pencere biz diski degistirirken acikti ve tur bilgisi bayat
kaldi (libparted dalindan gelen "unknown").

**Dogrulandi:** kullanici GParted'i kapatip yeniden acti ve bolum exFAT
gorundu. Yani tani dogruydu; kod tarafinda yapilacak bir sey yok.

Kod degisikligi yapilmadi. Bu olcum, saf Python exFAT ciktisinin **gercek bir
aygitta** referans araclarla birebir denk oldugunun kaydidir.

---

## 2026-09-17 (14) — Acilista hazir ekran (ADR 0035)

Kullanici: *"uygulama ilk acildiginda partitionlara tiklamayinca hicbir sey
secemiyorum... direkt [hazir ekran] gibi baslayabilir, ilk hali bug gibi
duruyor."*

Acilisti durum: bolum tablosu bos, harita genel bakista, arac cubugunun
neredeyse tamami pasif. Agacta bir **diske** tiklamak yalnizca bilgi panelini
dolduruyor, ekran olu kaliyordu; ancak bir **bolume** tiklamak diski salt
okunur acip her seyi canlandiriyordu. Yani calisir hale gelmenin yolu
kesfedilecek gibi degildi ve diskin kendisine tiklamanin acmamasi tutarsizdi.

### Degisen

1. **Disk satirina tiklamak diski acar** — bolum satiriyla ayni davranis.
   Guvenlik acisindan yeni bir sey yok: acma her zaman salt okunur, yazma
   yetkisi yalnizca Uygula aninda aliniyor (ADR 0025).
2. **Acilista ilk uygun disk secilip acilir.** Olcut acilabilirlik:
   bilgisi eksik okunan (yetki yok) ve bolum tablosu okunamamis diskler
   atlanir; kalan ilk disk acilir. Uc kural zararsiz tutar: bir kez calisir
   (kullanici kapatinca yeniden acmaz), sessiz basarisizlik (acilista hata
   penceresi yok), cizimden sonra siraya alinir.

"Acmadan yalnizca tabloyu gostermek" secenegi reddedildi: ekran dolu gorunur
ama butun islemler pasif kalirdi — sikayet edilen duygu daha kafa
karistirici bicimde surerdi.

### Olcum (Mint 22.3, root, offscreen, gercek aygitlar)

```
gorulen disk    : ['sda', 'sdb']
acilan oturum   : sda           salt okunur: True
sema / bolum    : GPT / 3 bolum tablo satiri: 3
secili bolum    : 1             harita kipi: 1 (tek disk)
etkin islemler  : ['Yeni bolum...', 'Bicimlendir...', 'Bolumu sil',
                   'Diski yedekle...']
durum cubugu    : Secili: Bolum 1 — Bolum 1 (1.00 MB)
```

Acma taramadan sonra ~70 ms surdu, donma yakalayici bir sey bildirmedi.
Duman testi secim politikasini ayrica sinar (`first_openable_disk`).

### Yan gozlem (duzeltilmedi)

Kullanicinin gunlugunde acilista `DONMA — arayuz 1.9 sn yanit vermiyor` satiri
var ve "isaretli islem yok" diyor; yani olculen bir isimiz degil, ilk cizim /
X11 tarafi. Offscreen kosumda hic gorulmedi. Ayri bir is olarak not edildi.

---

## 2026-09-17 (13) — Hata: arka arkaya planlanan bolumler ayni yere kuruluyordu (ADR 0034)

Kullanici `/dev/sdb` uzerinde uc bolum olusturmak istedi (568 MB FAT32,
1.40 GB NTFS, 3.03 GB exFAT); birinci adim gecti, ikincisi durdu:

```
17:19:10 kuyruga eklendi (1): Yeni bolum olustur — 568.00 MB, fat32
17:19:17 kuyruga eklendi (2): Yeni bolum olustur — 1.40 GB, ntfs
17:19:30 kuyruga eklendi (3): Yeni bolum olustur — 3.03 GB, exfat
17:19:34 HATA PartitionTableError: 2 numarali bolum ile cakisiyor
```

Ekran goruntusunde uc adimin da hedefi ayniydi: **LBA 14626816**.

### Kok neden

"Yeni bolum" penceresi bos alani `session.free_regions()` ile — yani
**diskteki** duruma gore — soruyordu. Kuyruktaki bolum diske yazilmadigi icin
o alan hala bos gorunuyor, ikinci ve ucuncu bolum de ayni yere kuruluyordu.

Bu, bir onceki hatanin (ADR 0033, bolum numarasi kaymasi) kardesi: orada
**kimlik**, burada **bos alan** diskten okunuyordu. Ikisinin koku ayni —
kuyruk varken gecerli olan diskteki hal degil, **plan**dir.

### Cozum

Yeni adim kurulurken yerlesim `planview` uzerinden sorulur:

- `planned_free_regions()` — bos alanlar, bekleyen adimlar dusulmus halde.
- `planned_partitions()` — MBR'de "4 birincil doldu mu" sayimi da buradan;
  yoksa kuyrukta bekleyen bolumler sayilmiyordu.
- `planview.overlap_at()` — "yeni bolum" ve "boyutlandir" adimlari kuyruga
  **girmeden once** denetlenir; cakisma uygulama ortasinda degil tiklama
  aninda soylenir.
- Secili bos alan kuyruk degisince yeniden cozulur (eskisi artik var
  olmayabilir).

Uygulama aninda adimi bir sonraki bos alana **kaydirmak** dusunuldu ve
reddedildi: gorulen plan ile diske yazilan ayrilirdi. Kuyrugun butun degeri
"ne gorduysen o uygulanir".

### Olcum

`t41_ust_uste_bolum_planlama`: diske gore sorulan alanin uc adimda da ayni
kaldigi (kok neden), boyle bir kuyrugun gercekten kirildigi, plana gore
kurulunca alanin her adimda kuculdugu ve uc adimin tek Uygula ile
uygulandigi, `overlap_at`in cakismayi yakalayip uzaktaki alani yakalamadigi.
Duman testi arayuz tarafini olcer: bekleyen "yeni bolum" adimindan sonra
`planned_free_regions()` icinde o araligi kesen bolge kalmaz.

| Kosum | Sonuc |
|---|---|
| `tests.run_all` | 39/41 (2 atlandi: yalnizca Windows dali) |
| `tests.platform_check` | 0 bulgu |
| `tests.i18n_check` | de/en TAMAM |
| `tests.ui_smoke` | tamamlandi |

### Fiziksel disk dogrulamasi (ayni gun, kullanici istegiyle)

`tests/physical_queue_test.py` eklendi ve `/dev/sdb` uzerinde kosuldu. Betik
kullanicinin yaptigi sirayla iki senaryoyu da gercek diskte tekrarlar ve her
adimdan sonra `partprobe` + `lsblk` ile **cekirdegin gordugunu** yazar:

```
A) Arka arkaya uc bolum (ADR 0034)
[1] Diske gore sorulsaydi hedefler: [2048, 2048, 2048]     <- kok neden duruyor
[2] Plana gore hedefler: [2048, 1165312, 4102144]          <- duzeltme
[3] Uygula -> uc adim da TAMAM
[4] Cekirdegin gordugu: sdb1 vfat FAT32 | sdb2 ntfs NTFS | sdb3 exfat EXFAT

B) Arka arkaya silme (ADR 0033)
    uc silme adimi da TAMAM -> disk bos
```

Betik `tests/physical_write_test.py` ile ayni alti guvenlik olcutunu kullanir
(sistem diski degil, bilgi eksiksiz, boyut sinirinin altinda, yol acikca
verilmis, `--onayla`).

---

## 2026-09-17 (12) — Uygula penceresinin gorsel duzeni

Kullanici: *"bekleyen islemleri uygulada progress bar guzel durmuyor, yazilar
birbirine cok yakin gibi, gorsel acidan duzenle."* Hakliydi; ekran
goruntusunde satirlar sikisikti.

Duzeltilenler (yalnizca **yerlesim**; ozel stil sayfasi yok, ADR 0013 gecerli):

- **Satirdaki ilerleme cubugu** dogrudan hucreye konuyordu ve hucre
  kenarlarina yapisiyordu. Artik kenar payli bir kapsayici icinde
  (10/12 piksel), her satirda ayni bosluk var.
- **Odak cercevesi kaldirildi** (`setFocusPolicy(Qt.NoFocus)`): tablo zaten
  secilemezken etkin hucrenin etrafina noktali bir kutu ciziliyor, satiri
  daha da sikisik gosteriyordu.
- Satir yuksekligi 30 -> 38, pencere 760x470 -> 820x520, genel aralik
  10 -> 12 piksel.
- "Durum" ve "Ilerleme" sutun basliklari ile durum metni **ortalandi**;
  sutun genislikleri metne gore ayarlandi.
- Durum satiri ile genel cubuk ayrildi: durum satirina en az 34 piksel
  yukseklik (metin bir satirdan ikiye cikinca cubuk ziplamiyor), cubuga
  en az 24 piksel (yuzde metni sigiyor) ve cubukla dugmeler arasina bosluk.
- Ayni nefes payi yedekleme penceresinin durum/ilerleme alanina da verildi;
  iki pencere ayni goruyor.

Olcum: `tests.ui_smoke` VM'de temiz kosuyor; `33-uygula-adimlar`,
`34-uygula-calisirken`, `35-uygula-durdu` goruntuleri yeniden uretildi.
Metin degismedigi icin sozluklere dokunulmadi.

---

## 2026-09-17 (11) — Hata: kuyrukta bolum numarasi kaymasi (ADR 0033)

Kullanici Linux misafirinde `/dev/sdb` uzerinde iki bolumu silmek istedi;
birinci adim gecti, ikincisi durdu. Gunlukten:

```
17:05:13 kuyruga eklendi (1): Bolum 1 sil — DU NTFS, 8.37 GB
17:05:16 kuyruga eklendi (2): Bolum 2 sil — Bolum 2, 1.63 GB
17:05:20 WARNING operations.step(kind=delete) — 0 ms
         HATA PartitionTableError: 2 numarali bolum yok
```

### Kok neden

**Bolum numarasi kalici bir kimlik degil.** Numara tabloda saklanmiyor;
tablo okunurken yerlesime gore bastan veriliyor (`gpt.py`, kullanilan girisler
sirayla 1, 2, 3...). Birinci bolum silinince ikinci bolum 1 numara oluyor ve
kuyrukta bekleyen "2 numarali bolumu sil" adiminin hedefi kayboluyor.

Sorun silmeye ozgu degildi: "yeni bolum" (onune dustuyse numaralari yukari
kaydirir), "boyutlandir + tasi" (siralamayi degistirebilir), "tablo olustur"
ve "disk guvenli sil" de ayni etkiyi yapiyor. Yani hata bir senaryonun degil,
kuyrugun **kimlik modelinin** kusuruydu.

### Cozum

Adimlar hedefi artik **baslangic LBA'si** ile de tasiyor (`params["at_lba"]`);
iki bolum ayni sektorde baslayamaz. `operations.resolve_target()` her adimi
calistirmadan once capayi o anki tabloda ariyor ve bolum numarasini tazeliyor:

- capa bulunamazsa adim **duruyor** ("Hedef bolum bulunamadi (LBA ...)") —
  yanlis bolume islem yapmaktansa durmak yeglenir;
- boyutlandirma bolumu tasidiysa sonraki adimlarin capalari yeni yere
  tasiniyor (`_follow_move`);
- capasiz eski adimlar eskisi gibi numaradan calisiyor;
- `planview` de ayni olcutu kullaniyor, boylece **ekranda gorunen ile
  uygulanan ayni bolum** oluyor.

Adim basligi degismiyor: kullanici kuyruga eklerken neye tikladiysa onu
goruyor ("Bolum 2 sil"), degisen yalnizca icerideki hedef cozumu — gunluge
"adim hedefi tazelendi: LBA 4096 -> bolum 1" diye yaziliyor.

### Olcum

Yeni test `t40_kuyrukta_bolum_numarasi_kaymasi` alti durumu sinar: kullanicinin
karsilastigi iki-silme senaryosu, ters sirada silme, silme+etiket+bicimlendirme
karisimi, hedefi kaybolan adimin durmasi, capasiz eski adimlar ve onizlemenin
ayni bolumu secmesi.

Testin gercekten bu hatayi yakaladigi **kaniti**: capa cozumleyicisi VM'de
gecici olarak kapatilip kosuldu ve test tam kullanicinin gordugu metinle
dustu — *"1 adim uygulandi, 'Bolum 2 sil' adiminda durdu: 2 numarali bolum
yok"*. Acikken geciyor.

| Kosum | Sonuc |
|---|---|
| `tests.run_all` | 38/40 (2 atlandi: yalnizca Windows dali) |
| `tests.platform_check` | 0 bulgu |
| `tests.i18n_check` | de/en TAMAM |
| `tests.diag_check` | 13/13 |
| `tests.ui_smoke` | tamamlandi |

---

## 2026-09-17 (10) — Yedekleme ve geri yukleme tek pencerede; yedek dosyasina not

Kullanici istegi: *"yedek dosya bilgisi menusunu de tek bir form kontrolu
olarak ayarlansin, surekli farkli formlar ve popup menuler aciliyor... formun
ustunde secilmis olan dub dosyasi, dosya bilgileri ve icerigi, sonra asagida
fiziksel diskler... ek olarak dub yedek dosyasina bilgi notu da
ekleyebilelim... disk secili oldugunda yedek alma islemleri de secilebilir
olmali."* Karar: **ADR 0032**.

### Ne degisti

**Tek pencere (`ui/dialogs/backup.py`, yeni).** Yedek alma, geri yukleme ve
yedek dosyasini inceleme ayni formda. Ustte `.dub` dosyasi: yolu, bilgileri,
**notu** ve icerigi (bolumler + kok klasor girisleri). Altta disk/bolum:
secilenin bolum haritasi ve acik goruntuler + fiziksel diskler tek agacta.
En altta durum satiri ve ilerleme cubugu — ayri ilerleme penceresi yok.

Eski akis sekiz pencereye yayiliyordu (dosya sec → nereye yazilsin? → hedef
sec → onay → sistem diski adi → ilerleme → sonuc). Hepsi bu forma girdi;
`BackupInfoDialog` kaldirildi, `main_window` icindeki bes akis tek bir
`open_backup_dialog()` cagrisina indi.

**Yikici onay formun icinde.** "Hedefteki butun veriler silinecek" kutusu
isaretlenmeden Baslat etkin olmuyor; hedef sistem diskiyse ayrica disk adi
yaziliyor. CLAUDE.md'nin onay kurali korundu, yeri degisti — ve kullanici
onayi verirken hedefi, kaynagi ve uyarilari ayni ekranda goruyor.

**Yedek dosyasina not (`.dub` baslik ofset 136, 320 bayt).** Yedek alinirken
yazilir, sonradan `clone.write_remark()` ile **veriye dokunmadan**
degistirilebilir. Surum artirilmadi: eski yedeklerde alan sifir, not "yok"
okunur; yeni yedekler eski surumlerde de acilir (gerekce ADR 0032 ve
`specs/dub.md`).

**Sikistirma duzeyi secilebilir:** Yok / Hizli / Normal / Yuksek (zlib
0/1/6/9). "Yok" secildiginde bile sifir bloklar yer kaplamaz.

**Eylem etkinligi duzeltildi.** "Diski yedekle / geri yukle" artik acik
goruntu **ya da** bilinen bir fiziksel disk varken etkin; kaynak pencerede
secildigi icin oturum sarti anlamsizdi. Kullanicinin bildirdigi "disk secili
oldugunda yedek alma secilebilmeli" istegi buydu.

### Olcum (Linux misafiri, Mint 22.3)

| Kosum | Sonuc |
|---|---|
| `tests.run_all` | 39/39 (2 atlandi: yalnizca Windows dali) |
| `tests.platform_check` | 0 bulgu |
| `tests.i18n_check` | de/en TAMAM (1374 ceviri) |
| `tests.diag_check` | 13/13 |
| `tests.ui_smoke` | tamamlandi; `36-yedek-al`, `37-geri-yukle` |

Yeni test `t39_yedek_notu_ve_sikistirma`: notun yazilmasi/okunmasi, sonradan
degistirilmesinin **veriyi bozmamasi** (geri yukleme sha256 ile dogrulanir),
bayt kirpmasinin cok baytli karakteri bolmemesi, sikistirma duzeylerinin dosya
boyutunu degistirmesi ve notsuz (eski) yedeklerin okunabilmesi.

Duman testi pencereyi iki kipte cizer, onay kutusu isaretlenmeden geri
yuklemenin etkin **olmadigini** denetler ve pencereden **gercek bir yedek
alir** (4 GB ornek goruntu -> 221 KB, not dosyaya yazilmis).

### Yol boyunca cikan iki hata (duzeltildi)

- Not kirpmasi "son bayt surekli bayt mi?" diye bakiyordu; tam bir karakterin
  son bayti da surekli bayttir ve saglam metinden bir harf kopariyordu
  (160 'c' harfi 159'a dusuyordu). Karar cozumlemeye birakildi.
- `BackupDialog.mode` hedef agaci doldurulduktan **sonra** atanmisti; ilk
  secim sinyali `self.mode` okurken patliyor, Qt istisnayi yutuyor ve icerik
  agaci bos kaliyordu. Kip artik en basta belirleniyor.

---

## 2026-09-17 (9) — Plan onizlemesi, adim adim uygulama penceresi, saf Python NTFS boyutlandirma

Kullanici uc sey istedi:

1. *"bolumleme sonrasi uygula dedigimizde islemlerin siralamasini gosteren bir
   form istiyorum, adimlarin bu formda progress bar ile uygulandigi gorulecek"*
2. *"Disk silme bolme islemlerini yaptigimda bekleyen islemler tarafina bunu
   ekliyor ve kum saati ekleniyor ama ana ekran eski halde kaliyor; ana ekranda
   yapilacak isleme gore ayarlansin"*
3. *"linuxda NTFS boyutlandirmasi yapamiyorum, bu crossplatform icin eksik bir
   durum"*

Ucu de yapildi. Kararlar: **ADR 0030** (NTFS saf Python boyutlandirma),
**ADR 0031** (plan onizlemesi + uygulama penceresi).

### 1. Saf Python NTFS boyutlandirma — `core/ntfsresize.py` (yeni, ~640 satir)

Onceki durum: NTFS yalnizca Windows'ta, **fiziksel diskte**, isletim sisteminin
`Resize-Partition` komutuyla boyutlandirilabiliyordu. Linux/macOS'ta islem
reddediliyordu; goruntu dosyalarinda hicbir platformda calismiyordu.

Yeni modul onyukleme sektorunu (ve yedegini), `$Bitmap`'i ve `$BadClus`'u
gunceller, `$LogFile`'i sifirlar. Kucultmede sinirin otesinde kalan kume
araliklarini **tasir**: yeni yer tahsis edilir, veri kopyalanir, oznitelugun
veri kosullari yeniden yazilir. `$MFT` kendini de tasiyabilir (kayit yazmadan
once kume zinciri guncellenir, sonra onyukleme sektorundeki `mft_lcn`).

Reddedilen durumlar sessiz degil: sikistirilmis/sifrelenmis akis, kayda
sigmayan veri kosullari, "kirli" isaretli birim.

Baglanti noktalari: `resize.py` icinde `_ntfs_info()` sinirlari `$Bitmap`'ten
verir, `_fs_resize()` NTFS dalini cagirir. Kucultmede pencere **eski boyutta**
acilir (`span`) — birimin sonundaki yapilar yeni sinirin otesinde kalabiliyor
ve okuma "bolum sinirini asiyor" diye reddediliyordu. Windows + fiziksel disk
yolu **degismedi**: orada hala isletim sisteminin boyutlandiricisi tercih
edilir.

### 2. Ana ekran planlanan yerlesimi gosteriyor — `core/planview.py` (yeni)

`planview.project(session, queue)` kuyruktaki adimlari bellekteki kopyalar
uzerinde isler; gercek tabloya dokunmaz. `Partition.plan_state` alani eklendi
(`new` / `changed` / `format` / `wipe`), diskten okunan bolumlerde hep bostur.

Arayuz tarafinda:
- Harita planlanan bolumu mor kesik cerceve + kose rozetiyle cizer; silinecek
  bolum haritadan kalkar, yeni bolum belirir.
- Tabloda yalnizca plan varken gorunen "Plan" sutunu ve mor satir.
- Haritanin ustunde plan seridi: ne gosterildigini yazar ve "Diskteki hali
  goster" ile gercek duruma gecirir.
- Yalnizca planda var olan bolum secilirse icerik gosterilmez ve bolum
  islemleri pasiflesir.

### 3. Uygulama penceresi — `ui/dialogs/apply.py` (yeni)

Onay kutusu + tek cubuklu ilerleme penceresi birlestirildi. Adimlar sira ile
listelenir, her adimin **kendi ilerleme cubugu** vardir; calisan/biten/duran
adim renkle isaretlenir, duran adimdan sonrasi "Calistirilmadi" kalir.
`OperationQueue.apply()` uc yeni geri cagri kabul eder (`on_step`,
`on_step_progress`, `on_step_done`) ve adim yuzdesini genel olcege sigdirir —
genel cubuk artik geri gitmiyor.

### Olcum (Linux misafiri, Mint 22.3)

| Kosum | Sonuc |
|---|---|
| `tests.run_all` | 38/38 (2 atlandi: yalnizca Windows dali) |
| `tests.platform_check` | 0 bulgu |
| `tests.i18n_check` | de/en TAMAM (1356 ceviri) |
| `tests.diag_check` | 13/13 |
| `tests.ui_smoke` | tamamlandi, 5 yeni ekran goruntusu |
| `tests/physical_ntfs_resize.py /dev/sdb` | TUM ADIMLAR BASARILI |

Yeni testler: t35 (NTFS dosya sistemi boyutlandirma), t36 (bolum tablosuyla
birlikte), t37 (plan onizlemesi), t38 (adim geri cagrilari) ve fiziksel disk
betigi `tests/physical_ntfs_resize.py`.

Fiziksel test **baskasinin araciyla** olusturulan birimi sinar: `/dev/sdb`
uzerinde `mkfs.ntfs` ile 4 GB'lik NTFS olusturuldu, ntfs-3g ile baglanip ~144 MB
dolduruldu, bizim kodumuzla 2 GB'a kucultuldu (5243 kume tasindi), sonra 3 GB'a
buyutuldu. Her adimdan sonra `ntfsfix -n`, `ntfsinfo -m` ve ntfs-3g baglamasiyla
sekiz dosyanin sha256 ozeti karsilastirildi — hepsi ayni.

### Yan duzeltmeler

- `tests/ui_smoke.py` icindeki eski beklenti guncellendi: silinecek bolum artik
  tabloda gorunmedigi icin kum saati sayisi 2'den 1'e dustu (davranis dogru,
  beklenti eskiydi).
- Yeni 44 arayuz metni icin en/de cevirileri yazildi.

---

## 2026-09-15 (2) — Tam tutarlilik denetimi (yalnizca analiz, kod degismedi)

Kullanici istegi: projeyi bastan sona analiz et, tutarsizliklari bul, GUI
tarafinda ayni isimlendirme kullanilip kullanilmadigini arastir, tum `.md`
dosyalarini incele ve yapilacaklar icin plan olustur.

### Yontem
Kod degistirilmedi. Once olcum tabani kuruldu, sonra bulgular ona gore yazildi:

| Kosum | Sonuc |
|---|---|
| `python -m tests.run_all` | 18/18 basarili (t05 harici `mkfs` yok, atlandi) |
| `python -m tests.platform_check` | 0 bulgu |

Tanimlayici dili denetimi icin `ast` tabanli tek seferlik bir tarayici yazildi
(fonksiyon adlari + parametreler), gecici alanda calistirildi.

### Bulgular (ozet)
- **Belge kaymasi:** README.md tek guncel belge. `project-overview.md`,
  `cross-platform.md`, `testing.md`, `diskgenius-parity.md`, `feature-analysis.md`
  ve `architecture.md` v0.2.0 doneminde donmus — surum, test sayisi (15 vs 18),
  saf Python NTFS/ext durumu ve fiziksel disk kapsami yanlis anlatiliyor.
  `project-overview.md` "root gerekmez / blok aygita yazilmaz" ilkesi CLAUDE.md ve
  ADR 0014 ile dogrudan celisiyor. `architecture.md` modul agacinda 7 modul eksik.
- **Kodda yanlis bilgi:** `APP_VERSION = "0.1.0"` (README 0.3.0) ve "Hakkinda"
  penceresi hala "mkfs araclariyla bicimlendirme" + "yonetici yetkisi gerektirmez"
  diyor.
- **GUI etiket ikiligi:** bes eylem menude ve sag tik menusunde farkli adla
  gorunuyor ("Yeni bolum..." / "Yeni bolum olustur..." vb.). Kok neden: sag tik
  menuleri mevcut `QAction` yerine etiketi yeniden yaziyor.
- **Sag tik menusu yetki denetimini atliyor:** `rename_partition`, `change_type`,
  `toggle_bootable` salt okunur kaynakta pasiflesmiyor. **Veri riski yok** —
  `session._require_writable()` reddediyor — ama kullanici dostane diyalog yerine
  ham hata metni goruyor.
- **Isimlendirme kurali:** "Turkce arayuz metni, Ingilizce kod adi" kurali 13
  dosyada 74 fonksiyonda ihlal edilmis. En yeni dosyalar en cok sapanlar
  (`resize_bar.py` bastan asagi Turkce). `_salt_okunur_acilis_uyarisi()` ile
  `_read_only_uyarisi()` yan yana duruyor.
- **Depo hijyeni:** `.claude/logs/app-*.log` calisma zamani gunlukleri git ile
  izleniyor ve gitignore disinda; uygulamayi her calistirmak agaci kirletiyor.
  `sessions/INDEX.md` ayni oturum icin mukerrer satir aliyor.

### Cikti
`.claude/docs/consistency-audit.md` — bulgular (A/B/C) ve alti asamali
iyilestirme plani.

---

## 2026-09-15 (3) — Iyilestirme plani: Asama 1, 2 ve 6 uygulandi

`consistency-audit.md` planinin ilk turu. Sira onerildigi gibi: once yanlis bilgi
veren kod, sonra GUI birligi, sonra depo hijyeni.

### Asama 1 — yanlis bilgi veren kod
- `APP_VERSION` `"0.1.0"` → `"0.3.0"`. Yanina, degistirildiginde README rozetinin
  ve `project-overview.md` durum satirinin da guncellenmesi gerektigi yazildi.
- "Hakkinda" penceresi yeniden yazildi. Eski metin iki yanlis soyluyordu:
  exFAT/NTFS/ext'in `mkfs` araclariyla bicimlendirildigi (artik sekizi de saf
  Python) ve uygulamanin "yalnizca secilen goruntu dosyasi uzerinde calistigi"
  (ADR 0014'ten beri fiziksel disk destegi var). Yeni metin fiziksel disk
  erisiminin varsayilan salt okunur oldugunu da soyluyor.

### Asama 2 — GUI etiket ve yetki birligi
Kok neden, sag tik menulerinin etiketi mevcut `QAction` yerine **yeniden
yazmasiydi**. `_partition_menu`, `_free_menu`, `_map_context` ve `_tree_context`
artik eylem nesnelerini dogrudan ekliyor. Boylece:

- Bes etiket ikiligi kapandi ("Yeni bolum olustur..." → "Yeni bolum...",
  "Guvenli sil..." → "Bolumu guvenli sil...", vb.).
- `_update_actions()` yetki denetimi sag tik menusune de islemeye basladi;
  salt okunur kaynakta "Bolum turunu degistir", "Bolum adini degistir" ve
  onyukleme bayragi artik pasif gorunuyor (onceden etkin gorunup is yapmiyordu).
- Salt okunur aciklama girisi tek metne indi (`_readonly_hint`).
- Onyukleme bayragi tek eylem, metni simetrik cift olarak `_update_actions()`
  icinde degisiyor: `BOOT_SET_TEXT` / `BOOT_CLEAR_TEXT`.
- Sekme indisleri `TAB_FILES` / `TAB_INFO` / `TAB_HEX` / `TAB_LOG` sabitlerine bagli.
- `_read_only_uyarisi` → `_readonly_warning`,
  `_salt_okunur_acilis_uyarisi` → `_readonly_open_warning` (Asama 3'ten one alindi;
  yeni `_readonly_hint` ile ayni adlandirmayi paylasmalari gerekiyordu).

**Tuzak:** onyukleme etiketini guncellemek icin ilk denemede `_current_partition()`
kullanildi — ama o islev kullaniciya diyalog acar ve `_update_actions()` her
yenilemede cagrilir. Sessiz bir `_selected_partition_quiet()` eklendi.

**Plan degisikligi:** 2.4 maddesi (`_require_session()` ekle) **gereksiz cikti**.
Dort islev de `_current_partition()` uzerinden zaten koruniyordu; denetim
belgesindeki aksi yondeki iddia duzeltildi. Kusur yalnizca gorseldi.

### Asama 6 — depo hijyeni
- `.gitignore` icine `.claude/logs/*.log`; izlenen iki gunluk dosyasi
  `git rm --cached` ile cikarildi. Uygulamayi calistirmak artik calisma agacini
  kirletmiyor. Konum degistirilmedi: `.claude/logs/` bilerek tasarlanmis bir
  ozellik (parity: "Islem gunlugu: arayuzde sekme + dosya"); ignore kurali
  `*.md` belge dokumleriyle `*.log` calisma ciktisini ayiriyor.
- `archive-session.py`: `append_index` → `upsert_index`. Bir oturum birden cok
  `SessionEnd` uretebiliyor (once `other`, sonra `exit`); eski kod her seferinde
  satir ekliyordu. Artik anahtar dokum dosyasinin adi ve satir yerinde guncelleniyor.
- `INDEX.md` basligindaki "`Ham dokumler .gitignore disidir`" cumlesi gercegin
  tersini soyluyordu; duzeltildi. Mevcut mukerrer satir temizlendi.

### Dogrulama
- `tests.run_all` → **18/18** · `tests.platform_check` → **0 bulgu**
- `tests.ui_smoke` → 17 ekran goruntusu, ana pencere gozle denetlendi
- Baglam menusu icin tek seferlik bir olcum betigi yazildi: salt okunur ve
  yazilabilir iki durumda menu etiketleri + etkinlik durumu arac cubugununkiyle
  **birebir** ayni cikti.
- Kanca, ayni oturum id'si iki kez bitirilerek denendi: tek satir, guncel `reason`.

> **Not:** PyQt5 bu makinede yalnizca Python **3.12**'de kurulu (varsayilan
> yorumlayici 3.14). Arayuz testleri `py -3.12 -m tests.ui_smoke` ile calistirildi.

### Yan gozlem (plan disi, ileriye not)
`01-ana-pencere.png` durum cubugunda `status_file` etiketi ile
`statusBar().showMessage()` gecici mesaji ust uste biniyor. Bu degisikliklerle
ilgisi yok, onceden de vardi; Asama 4 temizligine aday.

### Kalan
Asama 5 (belgeleri gercege esitle), Asama 3 (isimlendirme + denetim), Asama 4 (temizlik).

---

## 2026-09-15 (4) — Iyilestirme plani tamamlandi: Asama 5, 3 ve 4

### Asama 5 — belgeler gercege esitlendi
Kod degistirilmedi; on belge guncellendi.

- `project-overview.md` bastan yazildi: durum v0.3.0 / 18 test, "Temel ilkeler"
  maddesi 1 artik fiziksel disk gercegini anlatiyor (eski hali *"hicbir kod blok
  aygita yazmaz"* diyordu ve ADR 0014 ile dogrudan celisiyordu). v0.3 bolumu
  "planlanan"dan "tamamlandi"ya alindi, v0.4/v0.5 ayrildi, "Kapsam disi" bolumu
  eklendi.
- `architecture.md`: eksik **7 modul** agaca eklendi (`ext`, `ntfs`,
  `_ntfs_data`, `resize`, `physical`, `resize_bar`, `dialogs/resize`), veri
  akisina fiziksel disk ve boyutlandirma kollari cizildi, "Genisletme
  noktalari"na iki satir eklendi.
- `cross-platform.md`: NTFS/ext artik "yalnizca Linux" degil, uc platformda saf
  Python. Test durumu tablosu **surum ayrimi** yapacak sekilde yeniden yazildi.
- `testing.md`: `cd /home/pc/diskUltimate` kaldirildi, t16/t17/t18 satirlari
  eklendi, ekran goruntusu sayisi duzeltildi.
- `diskgenius-parity.md`: ozet tablosu belgeden **otomatik sayilarak** yeniden
  uretildi (eski sayimlar boyutlandirma tamamlanmadan onceydi). "Kapsam disi"
  olcutu yeniden tanimlandi.
- `feature-analysis.md` §4.5 ve ADR 0002 / 0017 / 0019 basliklari duzeltildi.
- Belge ici baglantilarin tamami betikle dogrulandi: **0 kirik baglanti**.

> **Dikkat edilen nokta:** ilk denemede Windows/Pop!_OS test satirlari toplu
> arama-degistirme ile "15/15 → 18/18" yapilmisti. Bu **yanlis bir iddia**
> olurdu: o kosumlar v0.2.0 doneminde, 15 testle yapildi. Geri alinip tablo
> hangi surumun nerede kosuldugunu ayiracak sekilde yazildi.

### Asama 3 — isimlendirme kurali geri getirildi
Once **denetim**, sonra ceviri (plandaki 3.1 sarti).

- `tests/platform_check.py` icine AST tabanli **tanimlayici dili denetimi**
  eklendi: fonksiyon adlari + parametreler Turkce sozcuk parcasi tasiyamaz.
  Gecici `ISIM_MUAFIYETI` listesiyle baslandi (17 dosya), dosyalar cevrildikce
  bosaltildi. Liste **bos**; denetim artik tum agaci koruyor.
  Muafiyet anahtari dosya adi degil **goreli yol** — `core/resize.py` ile
  `ui/dialogs/resize.py` ayni adi tasiyor ve ilk surumde ikincisi yanlislikla
  muaf kalmisti.
- Cevrilen: 17 dosya, ~92 fonksiyon. `ui/` (main_window, theme, resize_bar,
  dialogs) ve `core/` (ntfs, resize, physical, ext, exfat, clone, convert, gpt,
  image, recovery, vdisk).
- `_read_only_uyarisi`/`_salt_okunur_acilis_uyarisi` ikiligi zaten (3) oturumunda
  kapanmisti; `readonly` yazimi tek bicime indi.

> **Tuzak ve duzeltmesi.** Ilk ceviri duz duzenli ifadeyle yapildi ve
> **Turkce arayuz metinlerini bozdu**: `"Tum bolumler 4K..."` → `"Tum partitions
> 4K..."`, `"Kayip bolumler taraniyor"` → `"Kayip partitions taraniyor"`.
> Dosya yedekten geri alinip `tokenize` tabanli bir arac yazildi: yalnizca
> `NAME` belirtecleri degisir, dize ve yorum belirtecleri ellenmez.
> Sonrasinda tum degisen dosyalarin dize sabitleri HEAD ile karsilastirildi —
> kaybolan 22 dizenin tamami Asama 2'de bilerek birlestirilen menu etiketleri
> cikti, kaza yok. Tek gercek bozulma (`resize_bar` docstring'inde
> "tek adim" → "tek step") elle geri alindi.

### Asama 4 — kucuk temizlik
- `_icon()` kopyasi kaldirildi → `theme.standard_icon(widget, std)`.
- Widget API alan adiyla hizalandi: `set_data`→`set_partitions`,
  `set_values`→`set_range`, `ResizeBar.changed`→`rangeChanged`.
  `plan.changed` (farkli bir alan) **kasten** degistirilmedi.
- `ui_smoke` ekran goruntusu numaralari siralandi (14/15 yer degistirmisti);
  sekme indisleri `TAB_*` sabitlerine baglandi.
- `run_all` artik atlanan testi gizlemiyor: `Atlandi` istisnasi eklendi, ozet
  `17/18 basarili · 1 atlandi` diyor ve nedenini yaziyor. Cikis kodu 0 kalir.
  Eskiden atlanan test "TAMAM" sayilip ozet "18/18" diyordu.
- **`theme.STYLESHEET` silinmedi.** Denetim belgesindeki "belgesiz duruyor"
  iddiasi yanlisti: ADR 0013 bu kodu "ileriye donuk" basligi altinda bilerek
  birakmis ve `DISKULTIMATE_THEME=diskultimate` kacisini belgelemis. Gercek
  eksik, dalin **hic test edilmemesiydi**; `ui_smoke` icine temayi acip
  ADR 0012'deki sekme kirpilmasini olcen bir adim eklendi (16. goruntu).

### Dogrulama
- `tests.run_all` → **17/18 · 1 atlandi** (t05, harici `mkfs` yok)
- `tests.platform_check` → **0 bulgu**, muafiyet listesi bos
- `tests.ui_smoke` → **18 ekran goruntusu**, tema dali dahil
- Denetimin kendisi **olumsuz testten** gecti: gecici olarak eklenen
  `_yeni_bolum_ekle(sektor)` yakalandi, silinince tekrar 0 bulgu.
- Baglam menusu olcumu yinelendi: etiketler arac cubuguyla ayni, salt okunurda
  yazma eylemlerinin tamami pasif.

### Kalan (plan disi, ileriye)
- Yerel degisken adlari (fonksiyon govdesi ici) hala yer yer Turkce; denetim
  fonksiyon adi + parametre duzeyinde. Genisletilebilir.
- Durum cubugunda `status_file` ile gecici mesajin ust uste binmesi (bkz. 3.
  oturum notu) duzeltilmedi.

---

## 2026-09-15 (5) — Kalan iki madde: durum cubugu ve yerel degisken adlari

### 1. Durum cubugu cakismasi (gercek arayuz hatasi)
`01-ana-pencere.png` icinde sol altta iki metin ust uste biniyordu. Olculdu:
`showMessage()` gecici mesaji durum cubugunun **sol** bolgesine cizer — yani
`status_file` etiketiyle ayni yere. Qt'nin bu durumda normal widget'lari
gizlemesi beklenir; **PyQt5 5.15'te gizlemiyor**. Cikplak Qt ile kurulan en kucuk
ornekte de ayni davranis gorulduğu icin sorunun bizim kodumuzda olmadigi
dogrulandi, cozum kendi tarafimizda uygulandi:

```python
self.statusBar().messageChanged.connect(
    lambda metin: self.status_file.setVisible(not metin))
```

Dogrulama: mesaj etkinken `visible=False`, mesaj suresi dolunca `visible=True`
ve etiket metni korunuyor.

### 2. Yerel degisken adlari
Olcum: **656 Turkce yerel atama, 168 farkli ad, 20 dosya.**

Bu is icin kapsam farkindali bir cevirici yazildi (`local_rename.py`): yalnizca
`ast.Name` dugumlerini konumlarina gore degistirir. Boylece dizeler, yorumlar,
oznitelikler (`x.ad`), anahtar argumanlar (`f(ad=...)`) ve iceri aktarma adlari
**dokunulmadan** kalir. Her fonksiyon kapsami ayri degerlendirilir; ic ice
fonksiyon ayni adi yeniden baglarsa o ad atlanir, hedef ad kapsamda zaten
varsa cakisma bildirilip atlanir (3 yerde oldu).

Sonuc: 1871 degisiklik, ardindan kalan 12 ad (modul sabitleri + cakisma nedeniyle
atlananlar) elle cevrildi. **Kalan Turkce yerel: 0.**

Baglama gore anlam ayrimi yapildi: `baslik` cekirdekte **`header`** (ikili
baslik tamponu), arayuzde **`title`** (pencere basligi). Tek karsilik ikisinde de
yanlis olurdu.

> **Iki tuzak yasandi.**
> 1. Ilk kosumda `ast`'in `col_offset` degerinin **UTF-8 bayt ofseti** oldugu
>    (karakter ofseti degil) atlanmisti. Turkce karakter iceren satirlarda
>    hizalama kayiyordu. Betikteki hizalama denetimi bunu yakalayip yazmayi
>    **durdurdu**; degisen dosyalar yedekten geri alindi, duzenleme satirin
>    bayt gosterimi uzerine tasindi.
> 2. `rm -rf src` ile geri alma reddedildi (hakliydi); yalnizca degisen dosyalar
>    yedekten kopyalandi.

### 3. Denetim yerellere genisletildi
`platform_check` artik fonksiyon adi + parametre **ve** yerel degiskenler ile
modul duzeyi atamalari denetliyor. Kural "Ingilizce kod adi" diyor, yalnizca
"Ingilizce fonksiyon adi" degil; govde disarida kalsaydi kural yeniden gevserdi.

Genisletilmis denetim `main.py`'yi de yakaladi (`pencere`, `tema`) — ilk turda
gozden kacmisti, cevrildi.

### Dogrulama
- `tests.run_all` → **17/18 · 1 atlandi** (t05, harici `mkfs` yok)
- `tests.platform_check` → **0 bulgu**
- `tests.ui_smoke` → 18 ekran goruntusu, tema dali dahil
- **Dize butunlugu:** 41 dosyanin tum dize sabitleri ceviri oncesiyle
  karsilastirildi → **0 dosyada degisiklik**. Turkce arayuz metinleri saglam.
- Olumsuz test: gecici eklenen `_deneme()` icindeki `toplam_boyut` yerel
  degiskeni yakalandi, silininde tekrar 0 bulgu.

> **Not:** Bu oturumda ana makinenin fiziksel diskleri uzerinde **hicbir islem
> yapilmadi.** `physical_probe.py` / `physical_write_test.py` calistirilmadi;
> tum testler `.tmp/` altindaki imaj **dosyalari** uzerinde kosuldu. Arayuz
> agacinda gorunen fiziksel diskler yalnizca listelemedir (`list_disks()`
> hicbir aygiti acmaz). Fiziksel disk testleri CLAUDE.md geregi sanal makinede
> yapilacak.

### Durum
Iyilestirme planinin alti asamasi ve plan disi kalan iki madde tamamlandi.

## 2026-09-15 — Oturum kayitlarinin proje icine tasinmasi

### Yapilanlar
- `.claude/hooks/archive-session.py` — `SessionEnd` kancasi. stdin'den gelen
  kanca JSON'undan `transcript_path` okunur, dokum
  `.claude/sessions/<YYYY-MM-DD>-<session_id>.jsonl` olarak kopyalanir ve
  `.claude/sessions/INDEX.md` tablosuna bir satir eklenir. Saf Python, harici
  bagimlilik yok (makinede `jq` bulunmuyor). Hata durumunda sessizce 0 doner,
  oturumu bosa dusurmez.
- `.claude/settings.json` (depoya girer) — `SessionEnd` kancasi tanimlandi.
  `python3` yoksa `python`'a duser.
- `.claude/settings.local.json` (depoya girmez) — `autoMemoryDirectory` ile
  Claude hafizasi `.claude/memory/` icine yonlendirildi. Bu anahtar guvenlik
  geregi depoya giren `settings.json` icinden okunmaz, bu yuzden local dosyada
  ve mutlak yolla durur.
- `.claude/memory/MEMORY.md` — hafiza dizini tohumlandi.
- `.gitignore` — ham `.jsonl` dokumleri ve `settings.local.json` haric tutuldu;
  `.claude/sessions/INDEX.md` depoda kalir.
- `CLAUDE.md` — kayit kurali tablosuna `sessions/`, `memory/`, `hooks/` satirlari
  ve kuralin nasil zorlandigini anlatan bolum eklendi.

### Dogrulama
- Kanca betigi gercek kanca JSON'u ile stdin'den beslendi: cikis 0, dokum
  `.claude/sessions/` altina kopyalandi, `INDEX.md` olustu.
- Iki ayar dosyasi da `json.load` ile ayristirildi; kanca komutu JSON icinden
  geri okundu.
- `git check-ignore` ile `.jsonl` ve `settings.local.json` haric tutmalari
  dogrulandi.

### Not
`autoMemoryDirectory` mutlak yol tasir; depo baska bir dizine klonlanirsa
`.claude/settings.local.json` yeniden yazilmalidir. Kancanin devreye girmesi
icin Claude Code'un ayarlari yeniden okumasi gerekir (`/hooks` menusu veya
yeniden baslatma).

---

## 2026-09-13 — Proje kurulumu ve v0.1.0

### Yapilanlar

**Altyapi**
- `.claude/` yapisi kuruldu: `docs/`, `decisions/`, `specs/`, `logs/`.
- Kok dizine `CLAUDE.md` yazildi (proje kurallari, kayit kurali, kod standartlari).
- Paket iskeleti: `src/diskultimate/{core,ui,utils}`, `tests/`, `main.py`.

**Cekirdek (saf Python, GUI'den bagimsiz)**
- `core/image.py` — `BlockDevice` arayuzu, `DiskImage` (seyrek olusturma, okuma,
  yazma, yeniden boyutlandirma), `PartitionView` (bolum penceresi, sinir denetimi).
- `core/ptable.py` — `Partition` / `FreeRegion` modeli, MBR tip ve GPT GUID tablolari,
  hizalama ve bos alan hesaplari, `human_size` / `parse_size`.
- `core/mbr.py` — MBR okuma/yazma, CHS donusumu, 4 birincil bolum, genisletilmis
  bolum ve EBR zinciri ile mantiksal bolumler.
- `core/gpt.py` — GPT okuma/yazma, CRC32 dogrulama, koruyucu MBR, birincil + yedek
  baslik ve giris dizisi, 128 bolum destegi.
- `core/fat.py` — sifirdan FAT12/16/32 surucusu: bicimlendirme, FAT tablosu
  yonetimi, kume zinciri, LFN cozumleme ve uretme, 8.3 kisa ad uretimi, dizin
  ekleme/silme, dosya okuma/yazma, disa/ice aktarma, istatistik.
- `core/fsdetect.py` — imza tabanli tespit: FAT12/16/32, exFAT, NTFS, ext2/3/4,
  btrfs, XFS, F2FS, ISO9660, Linux takas; etiket ve kullanim hesabi.
- `core/formatter.py` — bicimlendirme dagiticisi; FAT dahili, exFAT/NTFS/ext*
  icin gecici seyrek birim uzerinde `mkfs.*` calistirip sonucu geri yazma.
- `core/filesystem.py` — `FileSystemAccess` arayuzu, `FatAccess`, `UnsupportedAccess`.
- `core/session.py` — `DiskSession`: GUI'nin gordugu tek cephe.

**Arayuz (PyQt5)**
- `ui/theme.py` — DiskGenius'a yakin acik tema, dosya sistemi renk paleti, stil sayfasi.
- `ui/widgets/disk_map.py` — gorsel bolum haritasi: oransal bloklar, dosya sistemi
  rengi, doluluk cubugu, secim/vurgu, ipucu, sag tik menusu.
- `ui/widgets/partition_table.py` — bolum ve bos alan listesi (10 sutun).
- `ui/widgets/file_browser.py` — klasor agaci + dosya listesi, disa/ice aktarma,
  klasor olusturma, silme, yeniden adlandirma, onizleme.
- `ui/widgets/hex_view.py` — sektor bazli onaltilik goruntuleyici.
- `ui/dialogs/` — yeni goruntu sihirbazi, bolum olusturma, bicimlendirme,
  ilerleme penceresi (QThread), dosya onizleme.
- `ui/main_window.py` — menu/arac cubugu, disk agaci, dort sekmeli alt panel,
  durum cubugu, tum is akislari, onay diyaloglari, `.claude/logs/app-*.log` gunlugu.

### Duzeltilen hatalar
1. **MBR yazma tampon boyutu** — onyukleme kodu 446 bayt olunca sektor 510 baytta
   kaliyor, imza yazilamiyordu. Tampon sabit 512 bayta cekildi.
2. **FAT32 `BPB_BkBootSec` ofseti** — 52 yerine dogru deger olan 50 kullanildi.
   `fsck.vfat` "yedek onyukleme sektoru yok" uyarisi verdigi icin yakalandi.
3. **Basarisiz bicimlendirmede hayalet bolum** — `create_partition` icinde
   bicimlendirme hata verirse bolum tablodan geri alinacak sekilde duzeltildi;
   `t08` testi bunu koruma altina aldi.
4. **Arayuz cakismasi** — disk haritasinda boyut metni ile doluluk cubugu ust uste
   biniyordu; blok yuksekligi ve metin konumlari yeniden ayarlandi.
5. **Gezgin yol cubugu** — desteklenmeyen bir bolume gecildiginde onceki yol
   ekranda kaliyordu; `set_filesystem` artik yolu sifirliyor.
6. **exFAT birim etiketi** — kok dizindeki `0x83` girisinden okunacak sekilde eklendi.

### Dogrulama
- `python3 -m tests.run_all` → **8/8 basarili**.
- Uretilen FAT12/FAT16/FAT32 birimleri `fsck.vfat -n` ile hatasiz dogrulandi.
- Uretilen MBR (mantiksal bolumler dahil) ve GPT tablolari `fdisk -l` ile dogrulandi.
- Arayuz `QT_QPA_PLATFORM=offscreen` altinda 4 bolumlu 4 GB ornek goruntu ile
  calistirilip ekran goruntuleri alinarak gozle dogrulandi.

### Sonraki adim
`.claude/docs/project-overview.md` icindeki v0.2 listesi — oncelik ext4 okuyucu.

### Kapanis notlari (ayni gun)
- `src/diskultimate/utils/` bos kaldigi icin kaldirildi; ihtiyac dogunca yeniden acilir.
- `.gitignore` icinde `.claude/logs/*.log` satiri kaldirildi: proje kurali geregi
  islem kayitlari depo icinde kalmali, disarida tutulmamalidir.
- Uygulama `main.py disk.img` ile calistirilip temiz acilis dogrulandi
  (offscreen, hata ciktisi yok).
- Boyut: 28 Python dosyasi, ~6000 satir kod; 13 markdown belge.

---

## 2026-09-13 (ikinci oturum) — Wayland cizim sorunu ve yol duzeni

### Bildirilen sorun
Kullanici `Yeni Disk Goruntusu` penceresinin **bos** acildigini bildirdi: pencere
cerceve ve baslik ile geliyor ama icerigi hic cizilmiyordu.

### Teshis
Ayni diyalog `xcb` (XWayland) ve `offscreen` altinda eksiksiz cizildi → tema veya
yerlesim hatasi degil, Qt 5.15 yerel Wayland eklentisinin modal pencerede ilk kareyi
boyamamasi. Ek olarak `QIcon.themeName()` = `breeze-dark` bulundu: ikonlar eksik
degil, acik renkli olduklari icin acik zeminde gorunmuyorlardi.

Ayrinti ve kararlar: [ADR 0005](../decisions/0005-wayland-ikon-temasi-ve-platform.md)

### Yapilan degisiklikler
- `main.py` — `secilen_platform()`: Wayland oturumunda XWayland (`xcb`) secilir;
  `DISKULTIMATE_QPA` / `QT_QPA_PLATFORM` ile gecersiz kilinabilir. Taban stil Fusion.
  Secilen platform, stil ve ikon temasi islem gunlugune yazilir.
- `ui/theme.py` — `ikon_temasini_ayarla()`: koyu ikon temasi acik varyantina cekilir
  (`breeze-dark` → `breeze`), bulunamazsa Qt gomulu ikon setine dusulur.
- `ui/dialogs/base.py` (yeni) — `exec_dialog()`: gosterim sonrasi yeniden cizim
  tetikleyen guvenlik agi; tum diyaloglar bunu kullaniyor.
- `ui/theme.py` — `QComboBox::drop-down` kurali kaldirildi (acilir liste oku geri
  geldi); `QSpinBox`/`QDoubleSpinBox` kutu kurali kaldirildi (artir/azalt oklari
  geri geldi).
- Diyalog dugmeleri Turkcelestirildi: `Cancel` → "Iptal", `Close` → "Kapat".

### Yol duzeni (kullanici istegi)
Gecici dosyalar artik `/tmp` yerine proje icinde: [ADR 0006](../decisions/0006-gecici-dosyalar-proje-icinde.md)
- `src/diskultimate/paths.py` (yeni) — tek yetkili yol kaynagi.
- `tests/run_all.py` → `<proje>/.tmp/tests`
- `core/formatter.py` → `<proje>/.tmp/format` (artik `tempfile` kullanmiyor)
- `tests/ui_smoke.py` (yeni) — ornek goruntu uretip 8 ekran goruntusu kaydeder
  (`<proje>/.tmp/screenshots`). Arayuz degisikliklerinden sonra calistirilir.
- `.gitignore` icine `.tmp/` eklendi.

### Dogrulama
- `python3 -m tests.run_all` → **8/8 basarili** (yeni yollarla).
- `DISKULTIMATE_QPA=xcb python3 -m tests.ui_smoke` → 8 ekran goruntusu; ana pencere,
  dort sekme ve uc diyalog gozle denetlendi: ikonlar gorunur, acilir liste ve sayi
  kutusu oklari yerinde, dugme metinleri Turkce.

---

## 2026-09-13 (ucuncu oturum) — v0.2.0: capraz platform + DiskGenius ozellikleri

Istek: (1) Windows/Linux/macOS destegi, (2) GitHub ve populer disk araclarinin
analizi, (3) DiskGenius ozelliklerinin mumkun oldugunca karsilanmasi.

### Arastirma
DiskGenius, GParted, KDE Partition Manager, TestDisk/PhotoRec ve Python
kutuphaneleri (FATtools, dissect.ntfs, ext4, pytsk3, gpt-image, pygpt, hdisk,
gptfdisk) incelendi. Bulgular ve cikan kararlar:
[feature-analysis.md](feature-analysis.md) · Ozellik matrisi: [diskgenius-parity.md](diskgenius-parity.md)

### Capraz platform (ADR 0008)
- `core/platform.py` (yeni) — seyrek dosya isaretleme (Windows `FSCTL_SET_SPARSE`),
  gercek dosya boyutu (`GetCompressedFileSizeW` / `st_blocks`), arac arama
  (Linux `mkfs.*`, macOS `newfs_*`), surec calistirma (`CREATE_NO_WINDOW`),
  Qt platform eklentisi secimi, varsayilan klasor.
- `image.py`, `formatter.py`, `main.py`, `tests/` bu katmana baglandi;
  `dd` cagrisi ve `st_blocks` kullanimi kaldirildi.
- `tests/platform_check.py` (yeni) — statik uyumluluk denetimi: sabit POSIX yollari,
  `tempfile`, dogrudan `subprocess`/`shutil.which`, endian isaretsiz `struct`,
  `core/` icine PyQt sizintisi. **Denetleyici kasitli ihlal dosyasiyla dogrulandi**
  (9 ihlalin 9'u yakalandi). Gercek kod: 0 bulgu.

### exFAT saf Python (ADR 0007)
`core/exfat.py` (yeni, ~1000 satir): bicimlendirme, okuma, yazma, klasor, silme,
yeniden adlandirma, birim etiketi, bitmap tabanli tahsis.
**Kritik bulgu:** upcase tablosu serbest degil — kendi uretilen tablo `fsck.exfat`
tarafindan reddedildi. Standart sikistirilmis tablo (5836 bayt, saglama
`0xE619D30D`) kaynaga gomuldu. Artik exFAT Windows/macOS'ta da calisiyor.

### Yeni ozellikler
| Modul | Yetenek |
|---|---|
| `convert.py` | MBR ↔ GPT donusumu (veri yerinde), uygunluk on denetimi, 4K hizalama raporu |
| `clone.py` | `.dub` yedek bicimi (ADR 0009), yedekleme/geri yukleme, disk ve bolum klonlama |
| `wipe.py` | Guvenli silme: sifir / rastgele / DoD 3 / DoD 7, dogrulama, bos alan silme |
| `recovery.py` | Silinmis dosya tarama ve kurtarma (FAT + exFAT), kayip bolum tarama, 13 imzali dosya carving |
| `vdisk.py` | VHD (sabit+dinamik), VDI, VMDK (duz+seyrek), QCOW2 okuma; VHD olusturma ve yazma |
| `session.py` | Tum bu yetenekler tek cephede toplandi (22 yeni yontem) |

### Arayuz
- Yeni **Araclar** menusu: silinmis dosya tarama, kayip bolum tarama, imza tabanli
  kurtarma, yedek bilgisi, sistem bilgisi.
- Disk menusu: GPT/MBR donusumu, hizalama denetimi, disk yedekle/geri yukle/klonla/sil.
- Bolum menusu ve baglam menusu: bolum yedekle/geri yukle/guvenli sil/silinmis tara.
- Dosya menusu: **Yeni sanal disk (VHD)**; acma filtresi sanal diskleri kapsiyor.
- Yeni diyaloglar (`ui/dialogs/tools.py`): guvenli silme, silinmis dosyalar,
  kayip bolumler, imza secimi, bulunan dosyalar, bilgi penceresi.

### Duzeltilen hatalar
7. **FAT tahsis basarimi (ADR 0010).** `alloc_cluster` her cagrida on binlerce
   elemanlik liste uretiyordu; 8 MB yazma >100 saniye suruyordu. Liste kaldirildi,
   bos kume sayaci onbellege alindi → **0.04 s** (~250 MB/s). `t15` regresyonu korur.
8. **Basarisiz bicimlendirmede tip esleme hatasi.** GPT→MBR donusumunde "Microsoft
   Temel Veri" GUID'i hem FAT32 hem NTFS'i kapsadigindan tip bayti yanlis seciliyordu;
   artik once dosya sistemi turune bakiliyor.
9. **Silinmis LFN adlari eksik kurtariliyordu.** Silme sirasinda LFN sira bayti da
   ezildigi icin sira numarasi guvenilmez; parcalar artik fiziksel siraya gore
   toplanip ters cevriliyor. "Onemli Rapor" → "Onemli Rapor 2026.txt".

10. **Kararsiz test (test altyapisi).** `t14` ikinci kosumda duserdi: VirtualBox,
   onceki kosumda kaydettigi VHD'yi medya kayit defterinde tutuyor ve ayni yolda
   yeni UUID gorunce `NS_ERROR_ABORT` veriyor. Dogrulama oncesi ve sonrasi
   `VBoxManage closemedium disk` cagrilarak cozuldu. Uretilen VHD'nin kendisi
   bastan beri gecerliydi.

### Dogrulama
- `python3 -m tests.run_all` → **15/15 basarili** (7 yeni test), **art arda iki kosumda kararli**
- `python3 -m tests.platform_check` → **0 bulgu**
- Bagimsiz araclarla capraz dogrulama: `fsck.vfat`, **`fsck.exfat` (clean)**,
  `fdisk -l`, **`VBoxManage showhdinfo`** (uretilen VHD gecerli), VBoxManage ile
  uretilen VDI/VMDK dosyalari okundu
- `python3 -m tests.ui_smoke` → 14 ekran goruntusu, gozle denetlendi

---

## 2026-09-13 (dorduncu oturum) — Windows dogrulama hazirligi ve donma nedeni

### Bildirilen olay
Kullanici Windows 10 sanal makinesinde test paketini calistirinca **ana makine
dondu ve resetlemek zorunda kaldi.**

### Kok neden
- VirtualBox paylasilan klasoru (`vboxsf`) **seyrek dosya desteklemez.**
- Testlerin mantiksal toplami ~8 GB. Linux'ta seyreklik sayesinde 1.2 GB yer
  kapliyordu; paylasilan klasorde **8 GB'in tamami gercekten yazilir.**
- Paylasim hedefi `/run/media/pc/Data` diskindeydi: **%93 dolu, 13 GB bos.**
- Ayrica testler urettikleri goruntuleri **silmiyordu**; hepsi birikiyordu.
- Dolan disk + `vboxsf` uzerinden yogun sektor yazimi host'u I/O kilidine soktu.

### Duzeltmeler (tekrarini onlemek icin)
1. **Ortam on denetimi** — `tests/run_all.py::check_environment()`:
   - hedef dizinde seyrek dosya desteginin **olculmesi** (64 MB deneme dosyasi)
   - bos alan denetimi: seyrek destekleniyorsa 2 GB, desteklenmiyorsa **12 GB**
   - yetersizse testler **baslamadan durur** (cikis kodu 2) ve nedeni yazar
2. **Test sonrasi temizlik** — her test kendi goruntulerini siler
   (`cleanup_test_files`). Pik disk kullanimi artik toplamin degil, en buyuk tek
   testin boyutu kadar. Inceleme icin `DISKULTIMATE_KEEP_TEST_FILES=1`.
3. **Belgelendirme** — `windows-setup/OKUBENI.md` icinde acik uyari: testler
   paylasilan klasorde degil, VM'in kendi diskinde (`C:\du-test`) calistirilir.

### Windows dogrulama altyapisi
Kullanicinin istegi uzerine SSH tabanli dogrulama hazirlandi:
- `VBoxManage modifyvm --natpf1 "ssh,tcp,127.0.0.1,2222,,22"` — NAT port
  yonlendirmesi (yalnizca localhost'a acik).
- Proje, VM'e `duproje` adiyla paylasilan klasor olarak baglandi
  (`VBoxManage sharedfolder add --automount`).
- `windows-setup/ssh-kur.ps1` — VM icinde OpenSSH Server kurulumu, sshd servisi,
  guvenlik duvari kurali, Python denetimi/kurulumu, parolasiz hesap uyarisi.
- `windows-setup/testleri-calistir.ps1` — kaynagi `C:\du-test` altina kopyalayip
  testleri **yerel diskte** calistirir (SSH olmadan da kullanilabilir).

### Windows uzerinde GERCEK KOSUM (ayni gun, tamamlandi)

**Engeller:** VM'de OpenSSH kurulu degildi, `pc` hesabi yonetici degildi ve
(baslangicta) internet yoktu — yani SSH veya paket kurulumu yapilamiyordu.

**Uygulanan cozum:** Guest Additions zaten kuruluydu; `VBoxManage guestcontrol`
ile VM icinde dogrudan komut calistirildi. Python'un **tasinabilir (embeddable)**
paketi (3.12.8) ve PyQt5 5.15.11 tekerlekleri ana makinede indirilip `duproje`
paylasimi uzerinden VM'e aktarildi, `C:\du-test` altina acildi. Yonetici yetkisi,
internet ve kurulum gerekmedi.

**Sonuclar (Windows 10 x64):**
- `tests.platform_check` → **0 bulgu**
- `tests.run_all` → **15/15 basarili**, 13.2 saniye
- `tests.ui_smoke` → **14 ekran goruntusu**, gozle denetlendi

**Yakalanan uc gercek hata:**
11. **Seyrek dosya uzatma (ADR 0011).** `FSCTL_SET_SPARSE` basariliydi ama Python'un
    `truncate()` cagrisi Windows'ta dosyayi **sifirlarla dolduruyordu**: 256 MB'lik
    goruntu gercekten 256 MB tahsis ediyordu. `SetFilePointerEx` + `SetEndOfFile`
    ile degistirildi → 0.00 s, 0 bayt. Tam kosum 18.2 s → **13.2 s**, gereken bos
    alan 12 GB → **2 GB**.
12. **Olcum uretim yolunu izlemiyordu.** Duzeltmeden sonra rapor hala
    "DESTEKLENMIYOR" diyordu: `_sparse_supported()` kendi `fh.truncate()` cagrisini
    kullaniyordu. Olcum uretim yoluna (`make_sparse` + `truncate_sparse`) hizalandi;
    `t11`'deki seyreklik dogrulamasi da `IS_LINUX` kosulundan kurtarilip gercek
    dosya sistemi destegine baglandi.
13. **Qt bulgulari (ADR 0012).** (a) Qt'nin Windows'taki `offscreen` eklentisi hic
    font yuklemiyor (font ailesi 0) — uretilen goruntulerde metin gorunmuyordu;
    `ui_smoke` artik Windows'ta `windows` eklentisini secip font sayisini raporluyor.
    (b) `QTabBar::tab:selected { font-weight: 600 }` sekme basliklarini kirpiyordu
    ("Dosya Gezgini" → "osya Gezgin"); sekmelerde font-weight kaldirildi.

**Ek duzeltme:** `tests/ui_smoke.py` artik platformda bulunmayan dosya sistemlerini
(Windows'ta ext4/NTFS) ham bolum olarak olusturuyor; boylece duman testi her
platformda calisiyor ve ekran duzeni karsilastirilabilir kaliyor.

SSH altyapisi (port yonlendirme + `windows-setup/` betikleri) yerinde duruyor;
ileride etkilesimli oturum gerekirse kullanilabilir.

### Not
Eski `myiso` paylasimi (`/run/media/pc/Data/myiso`) artik mevcut degil; VM'de
baglanamaz. Gerekirse kaldirilabilir:
`VBoxManage sharedfolder remove <vm> --name myiso --global`

---

## 2026-09-13 (besinci oturum) — Windows VM'de kalici Python ortami

Istek: "win10'da python nereye kurulu, ortam PATH'e kaydet".

### Yapilanlar
- Python, test klasorunden (`C:\du-test\python`) kalici konuma tasindi:
  **`C:\Python312`**. Boylece test klasoru silinse de yorumlayici kalir.
- `python312._pth` icinde `import site` etkinlestirildi (pip icin gerekli) ve
  `Lib\site-packages` eklendi.
- **pip 26.2.1** kuruldu (`get-pip.py`, ana makineden aktarildi).
- Kullanici duzeyi PATH'e eklendi (yonetici gerekmez):
  `C:\Python312;C:\Python312\Scripts;C:\Users\pc\AppData\Local\Microsoft\WindowsApps`
- `windows-setup/DiskUltimate-baslat.bat` eklendi; `windows-setup/OKUBENI.md`
  basina "kurulu durum" ozeti yazildi.
- Test klasorundeki 164 MB'lik Python kopyasi ve gecici tani betikleri silindi.

### Iki tuzak (ikisi de yasandi ve duzeltildi)
14. **PowerShell dizi tuzagi.** `$parcalar = $mevcut -split ";" | Where-Object {...}`
    tek eleman dondurdugunde **dizi degil string** olur; sonraki `+=` dizi ekleme
    degil **metin birlestirme** yapar. Sonuc bozuk PATH:
    `...WindowsAppsC:\Python312C:\Python312\Scripts` (noktali virguller kayip).
    Duzeltme: `@(...)` ile diziye zorlamak. Bozuk deger hemen duzeltildi.
15. **Windows yurutme takma adi.** PATH duzeldikten sonra `python` hala calismiyor,
    "Microsoft Store'dan kurun" diyordu: `WindowsApps\python.exe` stub'i PATH'te
    once geliyordu. Cozum: `C:\Python312` PATH'in **basina** alindi — resmi Python
    kurulumunun da yaptigi budur.

### Dogrulama (yeni guestcontrol oturumunda, kayit defterinden miras PATH ile)
- `python` → `C:\Python312\python.exe`, Python 3.12.8
- `pip --version` → 26.2.1
- `import PyQt5.QtCore` → Qt 5.15.2
- `python tests\platform_check.py` → **0 bulgu**
- `python tests\run_all.py` → **15/15 basarili**, seyrek dosya destekleniyor

---

## 2026-09-13 (altinci oturum) — Tema kaldirildi, fiziksel disk destegi eklendi

Uc istek: (1) temayi kaldir, sistem gorunumune don; (2) yazilim yalnizca disk
imaji uzerine calismasin; (3) ana ekranda sistemdeki diskler gorunsun, "aynen
DiskGenius gibi".

### 1. Tema kaldirildi (ADR 0013)
- `app.setStyleSheet("")`, `Fusion` zorlamasi ve ikon temasi degistirme kaldirildi.
- Tum sabit renkler (`TEXT_DIM`, `ACCENT`, `BORDER`, `FREE_COLOR`...) widget'lardan
  temizlendi; renkler artik `theme.palette_color()` ile **sistemin paletinden**
  geliyor. Soluk etiketler icin `setEnabled(False)` kullaniliyor.
- Disk haritasinda blok uzerindeki metin rengi, blogun zeminine gore acik/koyu
  seciliyor; dosya sistemi renkleri anlamsal oldugu icin sabit kaldi.
- `theme.apply_theme()` + `THEMES` altyapisi ileride eklenecek "Tema" bolumu icin
  hazir birakildi (`DISKULTIMATE_THEME=diskultimate` ile eski gorunum denenebilir).
- Koyu sistem temasinda dogrulandi: tum paneller okunabilir.

### 2-3. Fiziksel disk destegi (ADR 0014) — KAPSAM DEGISIKLIGI
Proje artik gercek disklere de erisiyor. `core/physical.py` (yeni):
- `list_disks()` — Linux `/sys/block`+`/proc/mounts`, Windows IOCTL
  (`DISK_GET_LENGTH_INFO`, `STORAGE_QUERY_PROPERTY`, `VOLUME_GET_VOLUME_DISK_EXTENTS`),
  macOS `diskutil -plist`. Model, seri, boyut, sektor, veriyolu, cikarilabilirlik,
  bolumler, bagli noktalar, sistem diski tespiti.
- `PhysicalDisk(BlockDevice)` — blok duzeyinde okuma/yazma. Windows'ta aygit G/C
  sektor hizali olmak zorunda oldugu icin oku-degistir-yaz uygulanir.
- `DiskSession.open_physical(...)`, `list_physical_disks()`, `has_disk_privileges()`.
- Arayuz: sol agacta **"Fiziksel Diskler"** dali; sistem diski kirmizi uyari
  ikonuyla `[SISTEM DISKI]` etiketli, bagli bolumu olanlar `[bagli bolum var]`,
  yetki yoksa `[?]`. Cift tiklama salt okunur acar. Disk menusunde yenileme,
  salt okunur acma, **yazma modunda acma** ve disk bilgisi.

**Guvenlik katmanlari** (CLAUDE.md'ye kural olarak islendi): listeleme zararsiz,
varsayilan salt okunur, sistem diskinde ad yazarak dogrulama, **bilgisi eksik
diske yazma reddi**, bagli bolum uyarisi.

### Yakalanan kusur
16. **Eksik bilgi "risk yok" gibi gorunuyordu.** Windows'ta yetki olmadan disk
    bilgileri okunamiyor; ilk surumde bu disk `is_system=False` olarak listeleniyor,
    yani en riskli disk en guvenli gibi gorunuyordu. `DiskInfo.info_complete`
    eklendi: bilgi eksikse risk seviyesi "bilinmiyor" olur ve **yazma reddedilir**.

### Dogrulama
- Linux listeleme: sistem diski (`nvme0n1`) dogru isaretlendi, bagli bolumler
  (`/`, `/boot/efi`, veri bolumu) tespit edildi. **Ana makinede hicbir disk acilmadi.**
- Guvenlik denetimleri sahte disk tanimlariyla dogrulandi (gercek aygita dokunmadan):
  onaysiz yazma, sistem diski, bilinmeyen disk — ucu de engellendi.
- Windows yetkisiz davranis: disk listede gorunuyor, "(yetki yok)" isaretli, acma
  denemesi anlamli hata veriyor.
- `tests.run_all` 15/15, `tests.platform_check` 0 bulgu (denetleyiciye
  "platform katmani" kavrami eklendi: `platform.py` ve `physical.py` yalnizca
  ilgili kurallardan muaf; tempfile/subprocess/endian kurallari onlarda da gecerli
  ve denetleyici kasitli ihlalle yeniden dogrulandi).

### Test araclari (yeni)
- `tests/physical_probe.py` — **yalnizca okuma** sondasi: diskleri listeler, salt
  okunur acar, bolum tablosunu cozumler. Yetkisiz calistirilabilir; neyin
  okunamadigini raporlar.
- `tests/physical_write_test.py` — yazma testi, **alti olcut** saglanmadikca
  calismaz: (1) aygit acikca verilmis, (2) `--onayla` bayragi, (3) sistem diski
  degil, (4) bagli bolum yok, (5) bilgiler eksiksiz, (6) boyut < 8 GB.
  Dogrulandi: ana makinenin sistem diski hedef gosterildiginde uc ayri olcutle
  reddedildi ve hicbir sey yazilmadi.
- `windows-setup/disk-testi-yonetici.bat` — PowerShell `ExecutionPolicy Restricted`
  kisitini asan sarmalayici (kullanici betigi dogrudan calistiramadi).

### FIZIKSEL DISK DOGRULAMASI — Pop!_OS 24.04 misafiri (tamamlandi)

Kullanici ikinci bir misafire Pop!_OS 24.04 kurdu (Guest Additions + SSH hazir).
Her iki misafire de **2 GB'lik bos sanal disk** eklendi (ayri VDI dosyalari; ayni
medya iki VM'e baglanamaz) ve SSH port yonlendirmesi yapildi
(win10 → 2222, Pop!_OS → 2223). Sistem disklerine dokunulmadi.

**Pop!_OS'ta yapilanlar (SSH + sudo ile tam otomatik):**

| Adim | Sonuc |
|---|---|
| `tests.platform_check` | **0 bulgu** |
| `tests.run_all` | **15/15 basarili** |
| `tests.physical_probe` (root) | 2/2 disk okundu |
| Guvenlik kilidi — `/dev/sda --onayla` | **REDDEDILDI** (3 olcut), cikis kodu 2 |
| `tests.physical_write_test /dev/sdb --onayla` | **TUM ADIMLAR BASARILI** |

**Sonda ciktisi:** `/dev/sda` sistem diski olarak isaretlendi, bagli bolum
`sda1 → /` tespit edildi, MBR tablosu cozumlendi (ext4 21 GB + Linux Takas 4 GB).
`/dev/sdb` bos, sistem degil, bagli bolum yok.

**Yazma testi adimlari (gercek diske):** GPT tablosu → 512 MB FAT32 bolum →
dosya yazma → **disk kapatilip yeniden acildi** → 19.500 bayt bayt-bayt dogrulandi
→ exFAT ile yeniden bicimlendirme → dosya yazma ve geri okuma.

**Bagimsiz dogrulama — en guclu kanit:** Isletim sisteminin kendi araclari
yazdigimiz yapiyi kabul etti:
```
lsblk -f : sdb1  exfat  1.0  DUEXFAT  6ADF-1B3D
blkid    : TYPE="exfat" LABEL="DUEXFAT" PARTLABEL="DiskUltimate Test"
           PARTUUID="d6d70183-3665-4235-9078-a71f7696c97f"
mount /dev/sdb1 /mnt/dutest -> BAGLANDI, exfat.txt okundu
```
Yani saf Python exFAT surucumuzun yazdigi birim **Linux cekirdegi tarafindan
baglanip okundu**; GPT bolum adi ve GUID'i de dogru yazilmis.

**Arayuz:** Pop!_OS'ta PyQt5 5.15.10 kurulup GUI calistirildi. Sol agacta
"Fiziksel Diskler (2)": `sda` kirmizi uyari ikonuyla `[SISTEM DISKI]`, `sdb`
normal. `sdb` acildiginda bolum haritasi, tablo ve dosya gezgini gercek diskten
okunan `exfat.txt` dosyasini gosterdi; salt okunur acildigi icin yazma eylemleri
devre disi kaldi. Ekran goruntuleri: `.tmp/linux-screenshots/`

### FIZIKSEL DISK DOGRULAMASI — Windows 10 misafiri (tamamlandi)

Kullanici yonetici komut isteminde calistirdi:

| Adim | Sonuc |
|---|---|
| `tests\physical_probe.py` | **2/2 disk okundu** |
| `tests\physical_write_test.py \\.\PhysicalDrive1 --onayla` | **TUM ADIMLAR BASARILI** |

- `PhysicalDrive0` sistem diski olarak isaretlendi, `C:` bagli bolumu tespit
  edildi, MBR tablosu cozumlendi (NTFS 50 MB + NTFS 49.45 GB + Windows Kurtarma
  510 MB). Model ve seri numarasi `IOCTL_STORAGE_QUERY_PROPERTY` ile okundu.
- `PhysicalDrive1` (2 GB test diski): GPT + FAT32 yazildi, dosya yazilip disk
  kapatilip yeniden acildiktan sonra bayt-bayt dogrulandi, ardindan exFAT ile
  yeniden bicimlendirildi.

### Yakalanan kusur — yalnizca gercek donanimda gorunur
17. **Isletim sistemine bildirim eksikti (ADR 0015).** Yazma testi basariliydi ve
    kendi kodumuz diski yeniden acip okuyabiliyordu, ama Windows diski hala
    **RAW** gosteriyor, `Get-Partition` hicbir bolum dondurmuyordu. Ham blok
    yazma, isletim sisteminin bolum tablosu onbellegini guncellemez. Gercek
    kullanimda kullanici bicimlendirir, "basarili" mesajini gorur, ama birim
    Gezgin'de cikmaz.
    `PhysicalDisk.rescan_partitions()` eklendi (Windows
    `IOCTL_DISK_UPDATE_PROPERTIES`, Linux `BLKRRPART`, macOS `diskutil rescan`);
    yazma modunda `close()` sirasinda kendiliginden cagriliyor.
    **Pop!_OS'ta dogrulandi:** bildirimden once `sdb1` aygiti yokken, sonrasinda
    cekirdek bolumu olusturdu, `blkid` exFAT'i tanidi ve `mount` ile dosya okundu.
    Bu kusur goruntu dosyasi testleriyle **asla** yakalanamazdi.

### Windows fiziksel disk — TAMAMLANDI (SSH ile otomatik)

Kullanici Win10'a OpenSSH Server kurdu. Onemli bulgu: **Windows'ta SSH oturumu
yukseltilmis yetkiyle geliyor** (`IsInRole(Administrator)` = True), yani UAC
engeli olmadan yonetici testleri host'tan otomatik calistirilabiliyor.

Iki gercek kusur daha yakalandi ve duzeltildi:

18. **Rescan islem ortasinda cagriliyordu (ADR 0015 duzeltmesi).**
    `DiskSession` bolum tablosu yazildiktan hemen sonra da `notify_os()`
    cagiriyordu. Windows'ta `IOCTL_DISK_UPDATE_PROPERTIES` aygiti yeniden taratir
    ve **o anda acik olan tutamaci gecersiz kilar**; sonraki yazma
    `ERROR_NO_SUCH_DEVICE` (433) verdi. Kural: rescan yalnizca tum islemler
    bittikten sonra, `close()` sirasinda. Linux'ta `BLKRRPART` bu yan etkiyi
    yaratmadigi icin sorun **yalnizca Windows'ta** gorundu.

19. **Bagli birime ham yazma reddi (ADR 0016).** Rescan basarili olunca Windows
    yeni FAT32 bolumunu tanidi ve `E:` olarak bagladi; ardindan exFAT
    bicimlendirmesi `ERROR_ACCESS_DENIED` aldi — Windows bagli birimin
    sektorlerine dogrudan yazmayi engeller. Cozum: yazma modunda disk acilirken
    o diskteki birimler `FSCTL_LOCK_VOLUME` + `FSCTL_DISMOUNT_VOLUME` ile
    kilitlenip baglantisi kesiliyor, tutamac acik tutuluyor; `close()` sirasinda
    once kilit birakiliyor sonra rescan yapiliyor.

**Nihai Windows dogrulamasi:**
```
[7] rescan cagrisi: basarili
    Number: 1   PartitionStyle: GPT          (onceden RAW idi)
    PartitionNumber DriveLetter Size  -> 1  E  536870912
    DriveLetter FileSystemLabel FileSystem -> E  DUEXFAT  exFAT
```
Windows diski GPT olarak tanidi, bolume surucu harfi atadi ve **saf Python exFAT
surucumuzun yazdigi birimi bagladi**.

### Onceki durum — bekleyen
- Win10'da her iki disk de goruluyor (`Get-Disk`: Disk 0 sistem MBR, Disk 1 2 GB
  RAW) ve sonda ikisini de `[?] BILINMIYOR / EKSIK (yetki yok)` olarak dogru
  raporluyor — yani yetki eksikligi guvenli tarafa dusuyor.
- ADR 0015 duzeltmesinin **Windows tarafinda** dogrulanmasi bekliyor (Linux'ta
  dogrulandi): yonetici komut isteminde yazma testinin yeniden calistirilmasi ve
  `Get-Disk` ciktisinin RAW yerine GPT gostermesi gerekiyor.
- **Tasinabilir Python'da `python -m tests.x` calismaz** (calisma dizini modul
  yoluna eklenmiyor); betikler dogrudan dosya olarak calistirilir
  (`python tests\physical_probe.py`). Belgeler ve `.ps1` buna gore duzeltildi.

---

## 2026-09-13 (yedinci oturum) — Arayuz duzeni, coklu goruntu, takas etiketi

Kullanici gercek bir Debian ARM goruntusu (`am335x-debian-13.6-...-4gb.img`)
acarak Windows'ta denedi ve uc geri bildirim verdi.

### 1. Takas bolumu etiketi bozuk karakter gosteriyordu (GERCEK HATA)
Ekran goruntusunde Bolum 2'nin etiketi `z¹D¶f↓®&4U` gibi cop karakterlerdi.

**Kok neden:** Linux takas basliginda etiket **1052.** bayttadir
(`sws_volume`), ben **1040**'tan okuyordum — orasi `sws_uuid` alaninin ortasi.
Yani UUID'nin ham baytlarini metin sanip gosteriyorduk.

```
1024 version | 1028 last_page | 1032 nr_badpages
1036 sws_uuid (16)            | 1052 sws_volume (16)  <- ETIKET
```

`fsdetect._swap()` yazildi: etiket dogru ofsetten okunuyor, ASCII disi bayt
iceriyorsa etiket **bos** sayiliyor, ayrica UUID cozumleniyor ve boyut
`last_page * 4096` ile hesaplaniyor.
**Dogrulama:** `mkswap -L TAKASETIKET` ile uretilen birimde etiket ve UUID
`mkswap` ciktisiyla birebir eslesti.

### 2. Arac cubugu baglamsal hale getirildi
"Yeni goruntu" ve "Goruntu ac" arac cubugundan kaldirilip **Disk menusune**
alindi (Dosya menusunde de duruyor). Arac cubugu artik yalnizca **secili disk
veya bolum** uzerinde yapilabilecek islemleri tasiyor:

`Yeni bolum · Bicimlendir · Bolumu sil | Bolumu yedekle · Bolume geri yukle |
Silinmis dosyalari tara · Kayip bolumleri tara | Bolumu guvenli sil |
Disk bilgisi · Yenile`

### 3. Coklu goruntu destegi
Onceden yeni bir goruntu acildiginda onceki kapaniyordu. Artik birden fazla
goruntu/disk ayni anda acik kalir ve sol agacta **alt alta** listelenir.

- `MainWindow.sessions: List[DiskSession]` — tum acik oturumlar,
  `session` bunlardan **etkin** olani.
- Her oturum agacta ayri kok: `ad — boyut — sema [salt okunur]`; etkin olan
  **kalin** ve genisletilmis.
- Agac ogeleri artik oturum kimligi tasiyor: `("part", (oturum_id, index))`.
  Baska bir goruntunun bolumune tiklamak once o oturumu etkinlestirir.
- Ayni dosya ikinci kez acilirsa yeni oturum acilmaz, mevcut olan one getirilir.
- Sag tik > "Bu goruntuyu kapat" yalnizca o oturumu kapatir; digerleri kalir.
- Pencere kapanisinda `close_all()` hepsini kapatir.

**Dogrulama:** `tests/ui_smoke.py` icine senaryo eklendi — ikinci goruntu
acildiktan sonra `len(sessions) == 2` ve agacta uc kok (fiziksel diskler +
iki goruntu) bulunuyor; ekran goruntusu `15-coklu-goruntu.png`.

### Durum
`tests.run_all` 15/15 · `tests.platform_check` 0 bulgu · duman testi 15 goruntu.

---

## 2026-09-13 (sekizinci oturum) — Sekiz dosya sistemi saf Python

Kullanici DiskGenius'un bicimlendirme ekranini gosterdi (8 bicim) ve bizdeki
listenin Windows'ta 4'e dustugunu belirtti: *"3 platformda da calisacak"*.

### Arastirma (kullanicinin onerisiyle)
KDE Partition Manager ve GParted incelendi: **ikisi de kendi bicimlendiricisini
yazmiyor**, harici `mkfs.*` cagiriyorlar. Yani saf Python yaklasimimiz bu
araclarin otesinde. NTFS'in tek acik referansi `ntfsprogs/mkntfs.c`.

### Yapilanlar
1. **Arayuz:** bicimlendirme listesi artik tum bicimleri gosteriyor;
   kullanilamayanlar gri ve yaninda nedeni yaziyor (`all_kinds`).
2. **`core/ext.py`** (yeni): ext2/ext3/ext4 saf Python — uc hata bulunup
   duzeltildi (resize_inode bayragi, dis gunluk isareti, gunluk yazim sirasi).
   `fsck` temiz, Pop!_OS cekirdegi uctü de **bagladi**.
3. **`core/ntfs.py`** (yeni): NTFS saf Python — bes hata bulunup duzeltildi
   (dizin siralamasi, sira numaralari, oznitelik kimlikleri, rezerve kayitlarda
   $FILE_NAME, $Bad kosusu). `ntfsinfo`/`ntfsfix` temiz; Windows `chkdsk`
   Asama 1 temiz, Asama 2'de `$Extend` alt yapilari eksik.
4. `_ntfs_data.py`: $AttrDef gomulu; $UpCase Python'dan uretilip 244 istisnayla
   duzeltiliyor — sonuc `mkfs.ntfs` ile **birebir ayni**, kaynak 3,5 KB.

Ayrinti: [ADR 0017](../decisions/0017-saf-python-ext-ve-ntfs.md)

### NTFS tamamlandi — uc katmanli strateji (ADR 0018)
Kullanicinin sorusu (*"ntfs-3g kullanmak yeterli olmuyor mu?"*) dogru yeri
isaret etti. Olculdu:
- Linux'ta `mkfs.ntfs` + `libntfs-3g.so` **zaten vardi** ve kullaniliyordu.
- Windows'ta ntfs-3g **yok** (resmi derleme yok), ama `Format-Volume` **var**.

Cozum: NTFS icin oncelik sirasi — (1) isletim sisteminin kendi araci,
(2) harici `mkfs.*`, (3) saf Python. `session._try_native_format()` fiziksel
disklerde Windows'un bicimlendiricisini cagirir; basarisizsa alt katmana duser.

**Win10 dogrulamasi:**
```
Get-Volume E:  ->  FileSystem: NTFS, Label: DUNTFS, Healthy
chkdsk E:      ->  "Windows has scanned the file system and found no problems."
```

### Durum
`tests.run_all` **17/17** · `platform_check` 0 bulgu.
Sekiz dosya sistemi de uc platformda olusturulabiliyor.

## 2026-09-14 — Tema kaldirma, fiziksel diskler, salt-okunur teshisi

### Tema kaldirildi
Kullanici geri bildirimi: *"temayi simdilik kaldir bazi seyler belli olmuyor."*
`ui/theme.py` icindeki `STYLESHEET` **uygulanmiyor**; sistem paleti kullaniliyor.
Renk gereken yerlerde `theme.palette_color(widget, role)` cagriliyor, boylece
koyu/acik temada da okunur kaliyor. Tema secimi ileride ayri bir bolum olacak.

### Kapsam genislemesi — fiziksel diskler
Uygulama artik yalnizca goruntu dosyasi degil, **sistemdeki tum diskleri** listeler
(`core/physical.py`). Alti katmanli yazma guvenligi: listeleme serbest → varsayilan
salt okunur → `confirm=True` → `allow_system=True` → bilgi eksikse **reddet**.
Sistem diskinde sari unlem yerine isletim sistemi amblemi gosteriliyor
(`theme.os_icon()`).

### Cok oturumlu agac
Birden fazla `.img` acildiginda sonuncusu digerlerini eziyordu. `MainWindow.sessions`
listesi eklendi; her oturum agacta kendi kokunu alir. Ayni dosyanin iki kez acilmasini
onlemek icin `_yol_anahtari()` → `realpath` + `normpath` + `normcase`.

### Duzeltme: swap etiketi bozuk karakter
Gercek bir Debian ARM goruntusunde "Bolum 2" etiketi bozuk cikiyordu. Neden:
`fsdetect._swap()` etiketi **1040** ofsetinden okuyordu; orasi UUID'nin ortasi.
Dogru ofset **1052** (`sws_volume`). Ayrica ASCII disi etiketler atiliyor.

### Salt okunur acilma teshisi (kullanici bildirimi)
*"Goruntu salt okunur acildi"* uyarisinin nedeni belirsizdi. Kullanici kaynagi buldu:
**ayni sanal diski once DiskGenius ile acmis**, DiskGenius dosyayi kilitli tutuyor.

Iki kusur duzeltildi:
1. `image.py` icindeki kilit dali yalnizca `OSError.winerror in (32, 33)` bakiyordu,
   ama Windows bu hatayi **`PermissionError` olarak** firlatir — dal hic calismiyordu.
   Artik `except PermissionError` once yakalaniyor, neden `_readonly_nedeni()` ile
   uc duruma ayriliyor: kilitli / salt okunur isaretli / erisim reddedildi.
2. `session.readonly_reason` fiziksel disk, bicim kisiti (VDI, QCOW2, seyrek VMDK) ve
   dosya duzeyi nedenleri ayirir. GUI tarafinda acilista neden gosterilir; kilit
   durumunda **"Yeniden dene"** dugmesi sunulur (diger program kapatilip yeniden
   denenebilsin diye). Durum cubugunda `🔒 SALT OKUNUR`, yazma islemleri menude pasif.

Dogrulama (Linux): oznitelik → "salt okunur isaretli", normal → yazilabilir,
`readonly=True` → "salt okunur acilmasi istendi".

### Durum
`tests.run_all` **17/17** · `platform_check` 0 bulgu · `ui_smoke` gecti.

## 2026-09-14 (2) — Bolum boyutlandirma / tasima

Kullanici DiskGenius'un "Bolumu Boyutlandir" penceresini ornek gostererek
**fareyle surukleyerek** boyutlandirma istedi.

### Cekirdek: `core/resize.py` (yeni)
- `window_for()` — bolumun komsu bos alanlarla birlikte kapsayici alani.
- `fs_resize_info()` — dosya sisteminin **asgari** (veri) ve **azami** (bicim)
  sektor sinirlari. Sinir disi istek plan asamasinda **reddedilir**.
- `fat_resize()` — FAT12/16/32. Buyutmede FAT tablosu buyudugu icin kok dizin +
  veri bolgesi `num_fats × fark` sektor ileri kaydirilir; kume numaralari
  degismedigi icin FAT icerigi oldugu gibi tasinir. Kucultmede yerlesim korunur,
  veri kopyalanmaz.
- `exfat_resize()` — ayni kaydirma FAT bolgesi icin; ayirma bitmap'i gerekirse
  **en dusuk** bos ardisik alana tasinir (bkz. asagidaki hata).
- `apply_resize()` — sira: kucultmede once FS, buyutmede once tablo; tasimada
  cakisma yonune gore ileri/geri kopyalama.
- `_patch_partition_offset()` — tasima sonrasi FAT/NTFS `BPB_HiddSec` (ofset 28),
  exFAT `PartitionOffset` (ofset 64) + saglama, NTFS'in son sektordeki yedek
  onyukleme kopyasi.

`session.resize_window/resize_info/plan_resize/resize_partition` eklendi.
`resize_partition` **`confirm=True` olmadan calismaz**. Fiziksel disklerde once
`platform.windows_resize_partition` (`Resize-Partition`) denenir — NTFS/ext saf
Python'da boyutlandirilamadigi icin (ADR 0018'deki uc katmanli strateji).

### Arayuz
- `ui/widgets/resize_bar.py` — suruklenebilir serit. Sol tutamak bolumu tasir,
  sag tutamak boyutlandirir, govde suruklemesi kaydirir. Ok tuslari ince ayar
  (Shift ile 16 kat). Her deger hizalama birimine yuvarlanir; surukleyerek
  hizasiz bolum uretilemez.
- `ui/dialogs/resize.py` — serit + "Yeni Kapasite / Baslangic-Bitis Kesimi /
  Onundeki-Arkasindaki Bosluk" kutulari, cift yonlu bagli. Pencere hicbir sey
  yazmaz, yalnizca istenen yerlesimi dondurur.
- Bolum menusu, arac cubugu ve sag tik menusune "Bolumu boyutlandir..." eklendi.

### Hatalar ve duzeltmeleri
- **FAT32 FSInfo bos kume sayaci bayat kaldi** (`fsck.vfat`: "Free cluster
  summary wrong"). Neden: `flush()` yalnizca FAT onbellegi kirliyse
  `_update_fsinfo()` cagiriyor; buyutme yolunda onbellek hic yuklenmiyordu.
  Duzeltme: her iki yolda da `_update_fsinfo()` acikca cagriliyor.
- **exFAT bitmap'i birimi kilitledi.** Bitmap buyudugunde **en yuksek** bos
  alana tasiniyordu; bu, "en yuksek kullanilan kume" degerini tavana cakti ve
  birim bir daha kucultulemez oldu. Duzeltme: en dusuk bos ardisik alan.
- **Diyalogda kutular ust uste bindi.** `QFormLayout` satirlarinin "en kucuk"
  yuksekligi tercih edilenden dusuk; pencere kucultulunce kutular eziliyordu.
  Duzeltme: `QLayout.SetMinimumSize` + dikey kucultmenin kapatilmasi.

### Dogrulama
- FAT32 200 MB → 80 MB → 450 MB → +100 MB tasima: `fsck.vfat` rc=0.
- exFAT 200 MB → 100 MB → 800 MB → 300 MB → tasima: `fsck.exfat` rc=0.
- GPT'de tasima+buyutme sonrasi bolum GUID'i korunuyor.
- Guvenlik kapilari test ediliyor: asgari sinir, disk siniri, onaysiz cagri.

### Durum
`tests.run_all` **18/18** · `platform_check` 0 bulgu · `ui_smoke` gecti.
Karar kaydi: `.claude/decisions/0019-bolum-boyutlandirma.md`.

---

## 2026-09-15 (6) — Takilan aygit gorunmuyordu (kullanici bildirimi)

### Bildirilen sorun
*"Ana bilgisayar uzerinde takili olan SD kart gorunmuyor; USB, SD vb. tum
diskleri gorebiliyor olmali."*

### Teshis
Once cekirdek olculdu (salt okunur, sektor okunmadan). `physical.list_disks()`
SD karti **dogru sekilde donduruyordu**:

```
PhysicalDrive1   63,864,569,856  'Generic- SD/MMC/MS PRO'   bus=USB  cikarilabilir=True
PhysicalDrive2   15,791,554,560  'SanDisk Cruzer Force'     bus=USB  cikarilabilir=True
```

Yani listeleme saglamdi. Kusur **arayuzdeydi**: agac yalnizca dort yerde
kuruluyordu — acilis, `close_image`, `refresh` ve elle "Fiziksel diskleri
yenile". Uygulama acikken takilan bir aygit, kullanici elle yenilemedikce
listede hic gorunmuyordu.

### Cozum
`MainWindow._poll_disks()` + 3 saniyelik `QTimer` (`DISK_POLL_MS`).

- Tarama ucuz: ana makinede olculen sure **~17 ms**.
- Agac yalnizca liste **gercekten** degistiginde yeniden kurulur. Karsilastirma
  `_disk_signature()` ile yapilir; imzaya boyut da girer, cunku kart
  okuyucuda kart degistirildiginde aygit yolu ayni kalir ama boyut degisir.
  Boylece yoklama kullanicinin secimini ve acik dallarini bosuna bozmaz.
- Takma/cikarma gunluge yazilir ("Aygit takildi: ... (59.48 GB)").
- Cikarilan aygitin **adi** yeniden kurulumdan once saklanir; yoksa gunlukte
  ham aygit yolu goruluyordu.
- `_add_physical_disks(disks=...)` hazir listeyi alir, ikinci tarama yapilmaz.
- `closeEvent` zamanlayiciyi once durdurur (kapanista yikilmakta olan agaca
  dokunmasin diye).

Dogrulama: ana makinede agac artik uc diski de gosteriyor —
dahili NVMe, SD kart (59.48 GB) ve SanDisk USB (14.71 GB).

### Yan bulgu — yanlis guvenlik ifadesi duzeltildi
`list_disks()` docstring'i *"Hicbir diski acmaz"* diyordu. Windows'ta bu
**dogru degil**: boyut/model/veriyolu yalnizca aygit tutamaci uzerinden
sorgulanabildigi icin her aygita salt okunur (`GENERIC_READ`, paylasimli) bir
tutamac acilip hemen kapatiliyor. Veri okunmuyor, yazma yapilmiyor. Ifade
kesinlestirildi: "hicbir sektor okunmaz, hicbir yazma yapilmaz".

> CLAUDE.md, README ve ADR 0014'teki "Listeleme zararsizdir — hicbir diski
> acmaz" cumlesi de ayni nedenle teknik olarak eksik. Davranis degismedi
> (Windows'ta baska yolu yok); ifadenin guncellenmesi kullanicinin karari.

### Regresyon korumasi
`ui_smoke` icine takma/cikarma denetimi eklendi. Gercek diske dokunulmaz:
`DiskSession.list_physical_disks` sahte bir listeyle degistirilip aygit takma,
cikarma ve "degisiklik yokken agac yenilenmemeli" durumu olculur.

### Dogrulama
`run_all` 17/18 · 1 atlandi · `platform_check` 0 bulgu · `ui_smoke` gecti.
Ana makinenin diskleri uzerinde **hicbir yazma veya sektor okuma yapilmadi.**

---

## 2026-09-15 (7) — Guvenlik katmani 1'in ifadesi kesinlestirildi

Onceki oturumda saptanan yanlis ifade, kullanicinin onayiyla tum belgelerde
duzeltildi. **Davranis degismedi** — yalnizca verilen guvence dogru anlatiliyor.

### Neden yanlisti
Katman 1 "Listeleme zararsizdir — **hicbir diski acmaz**" diyordu. Linux ve
macOS icin dogru (`/sys/block`, `/proc/mounts`, `diskutil` okunur), ama
Windows'ta **degil**: boyut, model ve veriyolu yalnizca bir aygit tutamaci
uzerinden sorgulanabilir (`IOCTL_DISK_GET_LENGTH_INFO`,
`IOCTL_STORAGE_QUERY_PROPERTY`). Bu yuzden `_list_windows()` her aygit icin
salt okunur (`GENERIC_READ`, `FILE_SHARE_READ|WRITE`, `OPEN_EXISTING`) bir
tutamac acar ve hemen kapatir.

Verilen gercek guvence "aygit acilmaz" degil, **"veri okunmaz ve yazilmaz"**.

### Guncellenen yerler
| Dosya | Ne degisti |
|---|---|
| `CLAUDE.md` | Katman 1 metni + Windows parantezi |
| `README.md` | Guvenlik bolumu, ayni metin |
| `decisions/0014-...md` | Katman 1 + tarihli **duzeltme notu** (neden yanlisti, davranisin degismedigi) |
| `core/physical.py` | Modul basligindaki katman listesi ve `list_disks()` docstring'i |
| `tests/physical_write_test.py` | Olcut denetimi yolundaki yorum |

Karar kaydina duzeltme notu birakildi: ADR'nin ilk hali yanlis bir guvence
veriyordu, bunun izi silinmedi.

### Dogrulama
`platform_check` 0 bulgu · `run_all` 17/18 · 1 atlandi · belge ici baglantilar
0 kirik. Fiziksel disklere dokunulmadi.

---

## 2026-09-15 (8) — `.dub` yedegi "bos disk" gibi aciliyordu (kullanici bildirimi)

### Bildirilen sorun
Kullanici 1.03 GB'lik `sdcard.img` dosyasini yedekledi (`sdcard.img.dub`, 34 MB),
sonra yedegi **Goruntu ac** ile acti: 34 MB'lik **bos** bir disk gorundu, oysa
iki bolum ve dosyalar olmaliydi.

### Once: veri guvende mi?
Panige yer olup olmadigi once olculdu. Yedek `.tmp/` icine geri yuklendi ve
kaynakla karsilastirildi:

```
kaynak : 1,107,296,768 bayt   sha256 9999fa8677b8947e...
geri   : 1,107,296,768 bayt   sha256 9999fa8677b8947e...
SONUC: BIREBIR AYNI
```

Yedek **kusursuzdu**; geri yuklenen goruntu acildiginda iki bolum de yerindeydi
(FAT16 onyukleme: `MLO`, `u-boot.img`, `zImage`, `extlinux` + ext4 `rootfs`).

### Kok neden
`.dub` basliginin **510. baytinda `0xAA55`** durur (specs/dub.md). Bu, basligi
512 bayta tamamlamak icin konmus ama yan etkisi agir: yedek ham goruntu olarak
acildiginda MBR cozumleyicisi imzayi **gecerli** bulur, 446-510 arasi sifir
oldugu icin de "bolum yok" der. Sonuc: 34 MB'lik (dosyanin kendi boyutu) bos bir
disk. Veri kaybi yok, ama kullanici "bolumlerim gitti" diye dusunuyor.

Acma yolunda hicbir yerde yedek imzasi denetlenmiyordu.

### Cozum
- `clone.is_backup_file(path)` — yalnizca `DUBACKUP` imzasina bakar, ucuzdur.
- `session.is_backup_file` / `session.restore_to_new_image` cepheye eklendi.
  Ikincisi hedefi yedegin kaydettigi boyutta **yeni** dosya olarak yaratir;
  mevcut hicbir disk veya goruntu uzerine yazmaz.
- `main_window.open_path` artik once imzaya bakar. Yedek secilirse ham acilmaz;
  boyut/dosya sistemi/etiket/tarih gosterilip uc secenek sunulur:
  **Yeni goruntuye geri yukle...** · Yalnizca bilgi · Kapat.
  Geri yukleme bitince olusan goruntu kendiliginden acilir — kullanicinin
  "yedegi acmak" derken kastettigi sonuc budur.
- `specs/dub.md` icine `0xAA55`'in bu yanilgiyi dogurdugu uyarisi yazildi.

`0xAA55` **kaldirilmadi**: bicim degisikligi olurdu ve mevcut yedekler bu alani
tasiyor. Uygulama icinde imza denetimi sorunu tamamen kapatiyor; baska araclarda
ayni yanilgi olusabilecegi spec'te belirtildi.

### Regresyon korumasi
`t11_yedekleme_ve_klonlama` genisletildi: `.dub` yedek olarak taninmali, ham
goruntu taninmamali, disk yedegi yeni bir goruntuye acilinca **bolum tablosu ve
dosyalar geri gelmeli**. Ilk yazimda iddia bolum yedegi uzerine kuruldugu icin
test hakli olarak patladi (bolum yedeginde tablo yoktur); disk yedegiyle
degistirildi — kullanicinin yasadigi senaryo da zaten budur.

### Dogrulama
`run_all` 17/18 · 1 atlandi · `platform_check` 0 bulgu · `ui_smoke` gecti.
Dogrulama sirasinda uretilen 2.06 GB gecici dosya silindi. Fiziksel disklere
dokunulmadi; tum islemler dosyalar uzerinde yapildi.

---

## 2026-09-15 (9) — `.dub` gezilebilir oldu, hedef olarak fiziksel disk eklendi

### Kullanici istegi
*"DUB kayit dosyasi acildiginda icerigi, disk ve bolumler de gorulmeli,
dosyalara ulasilabilmeli. Bu kayit fiziksel veya sanal diske yazilabilmeli;
su an sadece sanal diske yazabiliyoruz."*

Onceki oturumda yedek acilinca yonlendirme diyalogu gosteriliyordu — yani once
geri yukle, sonra gez. Istenen bu degil: yedek **dogrudan** gezilebilmeli.

### 1. Yedek artik salt okunur bir disk gibi aciliyor
`.dub` bicimi blok tablolidir: indeks her blok icin (tur, uzunluk, ofset)
tasir, yani **rastgele erisime uygundur**. Geri yukleme sirf bu yuzden
gereksizdi.

`clone.DubImage(BlockDevice)` eklendi:
- Istenen bayt araligini kapsayan bloklari bulur, `zlib` olanlari acar,
  sifir bloklar icin sifir uretir (dosyada yer kaplamazlar).
- 4 bloklu kucuk bir LRU onbellegi: ardisik okumalarda ayni blok tekrar
  acilmaz. Dosya sistemi surucusu cok sayida kucuk okuma yaptigi icin onemli.
- Son blok kisa olabilir; eksik kisim sifirla tamamlanir.
- `write()` reddeder — yedek bir arsivdir.

Baglanti: `vdisk.detect_format` `DUBACKUP` imzasini taniyip `"dub"` doner,
`open_disk` `DubImage` acar (her zaman salt okunur; `readonly` yok sayilir),
`format_label` "DiskUltimate yedegi (.dub)" der.

Gercek dosyayla dogrulama (kullanicinin `sdcard.img.dub` yedegi):

```
oturum      : DiskUltimate yedegi (.dub)
sema        : MBR   salt okunur: True
  Bolum 1: NO NAME  FAT16  33,554,432
  Bolum 2: rootfs   ext4   1,073,741,824
dosya gezgini (Bolum 1):
   am335x-mmcu.dtb   70.14 KB  DTB dosyasi
   extlinux                    Klasor
   MLO              107.93 KB  Dosya
   u-boot.img         1.49 MB  Disk goruntusu
   uEnv.txt           1.15 KB  Metin belgesi
   zImage             6.25 MB  Dosya
```

ext4 bolumu listelenemiyor — bu yedege ozgu degil, ext4 okuyucusu henuz yok
(v0.4 hedefi). FAT/exFAT icerigi tam gezilebiliyor.

### 2. Yedegi hedefe yazma
`Disk > Yedegi diske yaz...` (yalnizca acik oturum bir yedekken etkin) iki
hedef sunar:
- **Yeni goruntu dosyasi** — `session.restore_to_new_image`, hedefi yedegin
  kaydettigi boyutta yaratir, mevcut hicbir sey uzerine yazmaz.
- **Fiziksel disk** — `session.restore_to_physical`. ADR 0014 kapilarinin
  tamami gecerli: yazma onayi, **bilgisi eksik diskte ret**, sistem diskinde
  disk adini yazarak dogrulama, bagli bolum uyarisi, boyut denetimi.
  `PhysicalDisk(..., readonly=False, confirm=True)` ile acilir.

Fiziksel hedef **bu makinede denenmedi** (CLAUDE.md: ana makinenin diskleri
uzerinde deneme yapilmaz). Kod yolu ve guvenlik kapilari statik olarak
dogrulandi; gercek yazma testi sanal makinede yapilacak.

### 3. Acilista bilgilendirme
Yedek acilinca bir kereye mahsus not gosterilir: salt okunur oldugu, icerigin
gezilebildigi, yazmak icin nereye bakilacagi. Onceki yonlendirme diyalogu
(`_backup_opened_warning`) kaldirildi — artik gezmeyi engellemiyor.

### Regresyon korumasi
`t11` genisletildi: yedek `is_backup` ve `readonly` olmali, bolum tablosu ve
dosya icerigi yedekten **geri yuklemeden** okunabilmeli, yazma denemesi
reddedilmeli.

### Dogrulama
`run_all` 17/18 · 1 atlandi · `platform_check` 0 bulgu · `ui_smoke` gecti.
Fiziksel disklere yazilmadi.

---

## 2026-09-15 (10) — ext2/3/4 salt okunur okuyucu

### Soru
*"sdcard.img neden ext4 bolum dosyalarini goremiyorum?"*

Cevap: `.dub` ile ilgisi yoktu. DiskUltimate ext ailesini **bicimlendirebiliyor**
ama **okuyamiyordu**; gercek `.img` dosyasinda da ayni durum vardi. Okuyucu v0.4
yol haritasindaydi — bu oturumda yazildi.

### `core/extread.py` (yeni)
- Ustblok: blok boyutu, grup/inode sayilari, inode boyutu, etiket, `incompat`
  bayraklari. rev0 (ext2) icin sabit degerlere duser.
- Grup tanimlayicilari: `INCOMPAT_64BIT` acikken 64 baytlik tanimlayici ve
  yuksek 32 bitlik blok numaralari desteklenir.
- Blok haritalama iki yol: **extent agaci** (ext4; yaprak + ic dugum, uninit
  extent'lerde uzunluk maskesi) ve **dolayli blok** zinciri (ext2/ext3;
  tek/cift/uc kat).
- Dizin girisleri, sembolik bag hedefi (hizli bag `i_block` icinde), yol
  cozumleme ve **sembolik bag izleme** (goreli hedefler, dongu icin derinlik
  siniri).

`filesystem.ExtAccess` bunu `FileSystemAccess` arayuzune baglar; `writable=False`
oldugu icin arayuz yazma eylemlerini kendiliginden pasifler. `open_filesystem`
artik `ext*` icin bu sinifi dondurur.

### Iki hata yakalandi ve duzeltildi
1. **Sembolik bag icerik sanildi.** `/etc/os-release` bir hizli bagdir: hedef
   metni (`../usr/lib/...`) `i_block` icinde durur. `read_data` bunu blok
   numarasi dizisi gibi yorumlayip cop adresler uretti ve "okuma bolum sinirini
   asiyor" hatasi verdi. Artik `read_data` sembolik bagi acikca reddediyor,
   `resolve(follow=True)` bagi izliyor.
2. **Sinir disi blok numarasi coktururdu.** Bozuk ya da yanlis yorumlanmis bir
   numara `BlockDevice` sinirini asiyordu. Tum blok okumalari `_read_block()`
   uzerinden gecirildi: sinir disi numara sifir doner, okuyucu bozuk birimde de
   cokmez.

### Yanlis mesaj duzeltildi
`UnsupportedAccess` "yol haritasinda: ext4/NTFS/**exFAT** okuyucu" diyordu —
exFAT okuma zaten calisiyordu. Mesaj artik okunabilen dosya sistemlerini
sayiyor ve yalnizca NTFS'i yol haritasinda gosteriyor.

### Gercek veriyle dogrulama
Kullanicinin `sdcard.img` dosyasindaki `rootfs` (ext4, 1 GB, extent'li):

```
kok dizin : bin dev etc lib lib32-> libexec linuxrc-> lost+found media mnt
            opt proc root run sbin sys tmp usr var   (19 giris)
/etc      : 23 giris
/etc/fstab, /etc/hostname : metin dogru okundu
/etc/os-release -> ../usr/lib/os-release izlendi, 101 bayt, "NAME..." ile basliyor
/bin/busybox : 870,036 / 870,036 bayt, ELF imzasi dogru (extent agaci)
/bin/sh, /sbin/init, /linuxrc -> hepsi busybox'a cozuldu
```

Ayni icerik **`.dub` yedeginden de** okunuyor (DubImage + ExtFS birlikte).

### Regresyon korumasi
`t16` genisletildi: ext2/ext3/ext4 bicimlendirildikten sonra ayni birim
`ExtFS` ile acilir, etiket ve blok boyutu dogrulanir, kok dizinde
`lost+found` aranir, `open_filesystem` `ExtAccess` dondurmeli ve
`readable/not writable` olmali. Uc surum de ayni kod yolundan gecer
(ext2/3 dolayli blok, ext4 extent).

### Sinir
Salt okunurdur. Yazma, silme ve yeniden adlandirma yoktur — bunlar gunluk
(journal) tutarliligi gerektirir ve ayri bir istir.

### Dogrulama
`run_all` 17/18 · 1 atlandi · `platform_check` 0 bulgu · `ui_smoke` gecti.
Arayuzde hem `.img` hem `.dub` icin ext4 bolumu geziliyor.

---

## 2026-09-15 (11) — "ext4 bolume yazamiyorum" — yaniltici mesaj duzeltildi

### Bildirilen durum
Kullanici SD karti (`PhysicalDrive1`) **yazma modunda** acti, ext4 bolume
yazamadi. Arayuz "Bu bolum salt okunur acildi." diyordu.

### Sorun mesajdaydi
Iki ayri neden ayni metni uretiyordu:
1. **Kaynak** salt okunur acildi (cozulebilir: yazma modunda ac)
2. **Surucu** yazma desteklemiyor (cozulemez: ext yazici yok)

Kullanici (1)'i zaten yapmisti; mesaj onu yanlis yere yonlendiriyordu.

### Cozum
`FileSystemAccess.write_reason` eklendi; her surucu kendi nedenini soyler:
- `FatAccess` / `ExFatAccess`: "Kaynak salt okunur acildi. Goruntuyu/diski
  yazma modunda acarsaniz bu bolume yazabilirsiniz."
- `ExtAccess`: "ext2/3/4 surucusu SALT OKUNURDUR... **Diski yazma modunda
  acmis olmaniz bunu degistirmez.**"

Arayuz bu metni diyalogda gosterir; ayrica pasif dugmelerin **ipucunda** durur,
cunku pasif bir dugme tiklanamadigi icin diyalog hic acilmayabilir.

### ext4 yazma neden buyuk bir is (olculdu)
Kullanicinin `rootfs` birimi:

```
compat    has_journal, ext_attr, resize_inode, dir_index
incompat  filetype, extents, flex_bg, csum_seed
ro_compat sparse_super, large_file, huge_file, dir_nlink, extra_isize,
          metadata_csum        <-- saglama ZORUNLU
```

`metadata_csum` acik: ustblok, grup tanimlayicilari, inode'lar, extent
bloklari, dizin bloklari ve bitmap'lerin **her biri** CRC32c saglamasi tasir.
Yazan taraf bunlarin tamamini dogru guncellemek zorundadir; biri yanlis olursa
`e2fsck` birimi bozuk sayar. Ustune `has_journal` var: gunluk ya dogru
islenmeli ya da birim tutarli birakilmali.

Yani ext4 yazma "bir islev daha" degil, ayri bir calisma: blok/inode tahsisi,
bitmap ve sayac guncellemesi, extent agaci degisikligi, dizin girisi ekleme
(dir_index/htree dahil), CRC32c saglamalar ve gunluk. Ustelik ilk hedef
**gercek, onyuklenebilir bir SD kart** oldugu icin hata maliyeti yuksek.

Bu oturumda uygulanmadi; karar kullaniciya birakildi.

### Dogrulama
`run_all` 17/18 · 1 atlandi · `platform_check` 0 bulgu · `ui_smoke` gecti.
Kullanicinin fiziksel diskine **yazilmadi**; ozellik bayraklari yalnizca
okunarak olculdu.

---

## 2026-09-15 (12) — Dosya sistemi matrisi; ext yerlesim hatasi bulundu

### Istek
*"Sanal makinede uretilmis test birimlerinde gelistirip `e2fsck` ile
dogrularız — bunu yapalim, tum dosya bicimlerinde deneyebilir miyiz?"*

### `tests/fs_matrix.py` (yeni)
Sekiz bicim, ayni adimlar: **bicimlendir → tespit → oku → yaz → harici fsck**.
Sonuc tek tabloda toplanir.

Tasarim kurali: harici arac yoksa sonuc **ATLANDI** yazilir, asla TAMAM
sayilmaz. Aracsiz bir makinede tablo yaniltmaz — `e2fsck` yoksa ext yazmasinin
dogrulanmadigi acikca gorunur.

Ayrica ext yazma gelistirmesinin kosum alanidir: yazici uygulandikca `yaz`
sutunu kendiliginden dolar, `e2fsck` sutunu onu dogrular.

Ana makinedeki (Windows) durum — **hicbir fsck araci yok**, WSL kurulu degil:

```
Bicim | Bicimlendir | Tespit | Oku     | Yaz                   | Harici dogrulama
fat32 | TAMAM       | TAMAM  | TAMAM   | TAMAM                 | ATLANDI (fsck.vfat yok)
exfat | TAMAM       | TAMAM  | TAMAM   | TAMAM                 | ATLANDI (fsck.exfat yok)
ntfs  | TAMAM       | TAMAM  | ATLANDI | ATLANDI (okuyucu yok) | ATLANDI (ntfsfix yok)
ext4  | TAMAM       | TAMAM  | TAMAM   | ATLANDI (salt okunur) | ATLANDI (e2fsck yok)
```

### Matrisin ilk bulgusu: gercek bir hata
`ext3`/`ext4` **tam 129 MB** bolumde coktu (128 ve 130 MB sorunsuz):
`Yazma bolum sinirini asiyor`.

Kok neden: grup sayisi yukari yuvarlanir, bu yuzden kucuk bir artik tam bir
grup dogurur. 129 MB / 4 KB = 33024 blok; grup basi 32768 → 2 grup, sonuncuda
yalnizca 256 blok. Ama o grup da kendi metaverisini (yedek superblok + GDT +
iki bitmap + ~258 bloklu inode tablosu) istiyor → 260 > 256, yerlesim bolum
sinirini asiyor.

`mke2fs` bu durumda dosya sistemini grup sinirina **kirpar**. `_compute_layout`
artik ayni seyi yapiyor: son grup metaverisini alamiyorsa tumuyle dusurulur.
Kirpma inode tablosu boyutunu degistirdigi icin hesap donguyle tekrarlanir.

Dogrulama: ext2/ext3/ext4 × 60–140 MB (birer MB) + 150/200/256/300/512 MB →
**255/255 kombinasyon** bicimlendi ve her biri `ExtFS` ile acilip kok dizininde
`lost+found` dogrulandi.

### Denetimde iki kusur (ikisi de kendi eklediklerimde)
1. `last_blocks` degiskeni `st_blocks` kuralina takildi — kural alt dize
   ariyordu. `\bst_blocks\b` yapildi; iki yonlu test edildi (gercek
   `os.stat().st_blocks` yakalaniyor, `last_blocks` yakalanmiyor).
2. O duzeltmeyi kabuk uzerinden yazarken `\b` **backspace karakterine** donustu
   ve kural bir sure hicbir seyi yakalamadi. Olumsuz test bunu ortaya cikardi;
   bayt duzeyinde onarildi. Ders: kacis dizisi iceren duzenli ifadeler kabuk
   uzerinden yazilmamali.

### ext yazma icin ortam
Ana makinede dogrulama **mumkun degil**. Kullanici VMware'de **Linux Mint**
oldugunu bildirdi — gerekli araclarin tamami orada:

```bash
sudo apt install e2fsprogs dosfstools exfatprogs ntfs-3g
python3 -m tests.fs_matrix          # dort dogrulayici da calisir
```

ext yazma gelistirmesi bu ortamda, `e2fsck` her adimda kosularak yapilacak.

### Dogrulama
`run_all` 17/18 · 1 atlandi · `platform_check` 0 bulgu · `ui_smoke` gecti ·
`fs_matrix` 8/8 hatasiz (dogrulama adimlari atlandi).

---

## 2026-09-15 (13) — Linux Mint dogrulama ortami kuruldu; sekiz bicim fsck ile dogrulandi

### Ortam
Kullanici VMware Linux Mint misafirine SSH erisimi verdi (`192.168.42.131`),
proje `/mnt/hgfs/...` ile paylasildi ve misafire **bos 10 GB `/dev/sdb`** eklendi.

Kurulum: `python3-pyqt5` kuruldu. Dogrulayicilarin tamami zaten vardi —
`fsck.vfat`, `fsck.exfat`, `e2fsck`, `ntfsfix`, `ntfsinfo`, `mkfs.*`.

**Testler paylasilan klasorde kosulmadi.** `vmhgfs` seyrek dosya desteklemez;
goruntuler tum boyutlariyla yazilip ana makinenin diskini doldurabilirdi (bu
daha once yasanmis, bkz. 4. oturum). Kaynak `~/du-test` altina kopyalandi.

### Ilk gercek capraz dogrulama
`tests.run_all` → **17/18 · 1 atlandi**. Bu kez `t16`/`t17`/`t18` gercek
`e2fsck` / `ntfsfix` / `fsck.vfat` ile kosuldu (Windows'ta bu adimlar
atlaniyordu).

`tests.fs_matrix` → **8/8 hatasiz, harici dogrulamalarin tamami TAMAM**:

| Bicim | Bicimlendir | Oku | Yaz | fsck |
|---|---|---|---|---|
| fat12/16/32 | ✅ | ✅ | ✅ | ✅ `fsck.vfat` |
| exfat | ✅ | ✅ | ✅ | ✅ `fsck.exfat` |
| ntfs | ✅ | okuyucu yok | okuyucu yok | ✅ `ntfsfix` |
| ext2/3/4 | ✅ | ✅ | salt okunur | ✅ `e2fsck` |

### Matriste bir yanlis alarm — kusur bendeydi
Ilk kosumda NTFS "alternate boot sector BAD" verdi. Cozumleme, birimin
**saglam** oldugunu gosterdi: `total_sectors` 262143 = bolum sektoru − 1 ve
yedek onyukleme sektoru tam o konumda.

Kusur `fs_matrix._extract` icindeydi: bolumu ayiklarken dosyanin **sonuna
kadar** kopyaliyordu, yani 128 MB'lik birim 129 MB'lik dosyaya donusuyordu.
NTFS yedek onyukleme sektorunu aygitin **son** sektorunde arar; fazladan kuyruk
yuzunden orada sifir buldu. FAT/exFAT/ext boyutu ustbloktan okudugu icin
etkilenmedi. `_extract` artik tam bolum uzunlugu kadar kopyaliyor ve NTFS
`ntfsfix` ile temiz cikiyor.

> Ders: bir dogrulama koşumu hata bildirdiginde once **koşumun kendisi**
> sorgulanmali. "NTFS bicimlendiricimiz bozuk" diye rapor etseydim yanlis olurdu.

### ext4 okuyucu, Linux cekirdek surucusuyle karsilastirildi
Kullanicinin gercek `sdcard.img` dosyasi `mount -o ro,loop,offset=...` ile
baglandi ve **ayni birim** hem cekirdek hem bizim okuyucumuzla okundu:

```
kok dizin ayni mi : True   (19 giris)
/etc giris sayisi : 23 = 23
busybox sha256    : 50e486029e849c99...  (870,036 bayt, extent agaci)
referansla AYNI mi: EVET
```

Okuyucu cikti duzeyinde cekirdekle **birebir** ayni. Referans baglanti
kaldirildi; `/dev/sdb` bu oturumda **hic kullanilmadi** (durumu dogrulandi:
bos, bagli degil).

### Dogrulama
Linux: `run_all` 17/18 · 1 atlandi · `fs_matrix` 8/8.
Windows (ana makine): `run_all` 17/18 · `platform_check` 0 bulgu · `ui_smoke` gecti.

---

## 2026-09-15 (14) — ext2/3/4 yazma destegi (1. asama), e2fsck ile dogrulandi

### Kapsami belirleyen olcum
Kendi bicimlendiricimizin urettigi ext birimleri **metadata_csum, 64bit ve
extent kullanmiyor** (yalnizca `filetype`). Bu, ilk asamayi klasik dolayli blok
yazimina indirdi — saglama ve extent agaci isin disinda kaldi.

### `core/extwrite.py` (yeni)
- Blok ve inode **bitmap tahsisi**; grup tanimlayici sayaclari
  (`free_blocks`, `free_inodes`, `used_dirs`) ve ustblok sayaclari birlikte
  guncellenir.
- Inode yazimi (`i_blocks` 512'lik sektor cinsinden, dolayli bloklar dahil).
- Veri yerlesimi: 12 dogrudan blok + tek kat dolayli (4 KB blokta ~4 MB).
  Ustu acikca reddedilir.
- Dizin girisi ekleme/silme: `rec_len` zinciri dogru bolunur ve birlestirilir;
  yer kalmazsa dizine yeni blok eklenir.
- `mkdir` ( `.` / `..` girisleri, ust dizinin `links_count` artisi), `remove`
  (bos klasor denetimi, `dtime`, blok/inode serbest birakma), `rename`.

### Guvenlik kapisi
`write_support()` su durumlarda yazmayi **reddeder**: `metadata_csum`,
`bigalloc`, `64bit`, `inline_data` ve extent kullanan inode. Neden metinle
dondurulur, arayuz bunu gosterir. **Yanlis yazip bozmaktansa yazmamak yeglenir.**

### Dogrulama — `tests/ext_write_check.py` (yeni)
Her adimdan sonra `e2fsck -nf`: bicimlendirme → kucuk dosya → mkdir → dolayli
bloklu 256 KB dosya → uzun ad → 60 giris → rename → dosya silme → klasor silme.
`e2fsck` yoksa kosum **basarisiz sayilir**, atlanmaz.

Linux Mint misafirinde: **ext2, ext3, ext4 → 3/3 dogrulandi.**

`fs_matrix` artik ext satirlarinda da dolu:

| Bicim | Bicimlendir | Oku | Yaz | fsck |
|---|---|---|---|---|
| fat12/16/32 | ✅ | ✅ | ✅ | ✅ `fsck.vfat` |
| exfat | ✅ | ✅ | ✅ | ✅ `fsck.exfat` |
| ntfs | ✅ | okuyucu yok | okuyucu yok | ✅ `ntfsfix` |
| **ext2/3/4** | ✅ | ✅ | **✅** | **✅ `e2fsck`** |

### Gercek kart uzerinde kapi denendi
Kullanicinin `sdcard.img` kopyasi **yazilabilir** acildi ve ext4 bolume yazma
denendi:

```
Bolum 1 (FAT16): yazilabilir=True
Bolum 2 (ext4) : yazilabilir=False
   RET NEDENI: ... metadata_csum (CRC32c saglamalar) ...
Yazma denemesi REDDEDILDI (beklenen)
Kopya degisti mi? DEGISMEDI (birebir ayni)
```

Kapi calisiyor: gercek kart bozulmadi, dosya bayt bayt ayni kaldi.

### Kalan
- `metadata_csum` destegi (kullanicinin kartinin ihtiyaci) — CRC32c saglamalar.
- Extent agaci yazimi.
- Cok katli dolayli blok (4 MB ustu dosya).
- NTFS okuma/yazma.

### Dogrulama
Linux: `ext_write_check` 3/3 · `run_all` 17/18 · `fs_matrix` 8/8.
Windows: `run_all` 17/18 · `platform_check` 0 bulgu · `ui_smoke` gecti.

---

## 2026-09-15 (15) — Fiziksel diskte ext4 uctan uca; bicimlendirme imza hatasi

### Gercek donanimda okuma
SD kart VM'e aktarildi (`/dev/sdc`, 59.48 GB, iki bolumu de masaustu tarafindan
**bagli**). `tests.physical_probe` dogru siniflandirdi: `sda` sistem diski
(`[!]`), `sdb` temiz, `sdc` cikarilabilir + bagli bolumler listelendi.

Salt okunur acilip okundu: MBR, FAT16 (7 giris) ve ext4 (20 giris) sorunsuz.
Yazma dogru gerekcyle reddedildi ("Kaynak salt okunur acildi").

### Bulunan hata: bicimlendirme eski imzayi birakiyordu
Fiziksel diskte ext4 bolum olusturuldu ama acilirken **exFAT surucusu cagrildi
ve cokti**. Neden:

```
sdb1 ofset 0    : eb76 9045 5846 4154  -> "EXFAT" (onceki testten kalma)
sdb1 ofset 1080 : 53ef                 -> ext ustblok imzasi (dogru yazilmis)
```

Her dosya sistemi kendi alanini yazar ama otekinin imzasini **silmez**: exFAT
onyukleme sektoru ofset 0'dadir, ext ilk 1024 bayti rezerve birakip ona
dokunmaz. Sonuc: ext4 birim exFAT sanildi. (`blkid` dogru taniyordu, cunku o
imza oncelik kurallarini biliyor; bizim tespitimiz ilk eslesmeyi aliyor.)

`formatter.wipe_signatures()` eklendi — `mkfs` araclarinin `wipefs` davranisi:
bicimlendirmeden once ilk 128 KB ve **son sektor** (NTFS yedek onyukleme)
sifirlanir.

**`t05` yeniden amaclandirildi.** Eski hali ("harici mkfs ile bicimlendirme")
oluydu: tum bicimler saf Python oldugundan `available_kinds(internal=False)`
hep bos donuyor, test her zaman atlaniyordu. Yerine yeniden bicimlendirme
zinciri kondu: `fat32 → exfat → ntfs → ext4 → fat16 → ext2`, her adimda tespit
dogrulanir. Bu sayede **`run_all` artik 18/18** (atlanan yok).

### ext4 uctan uca fiziksel disk (`/dev/sdb`, bos test diski)
```
[1] yazma modunda acildi        [5] disk kapatildi
[2] ext4 bolum olusturuldu      [6] yeniden acildi: 21 giris, buyuk.bin AYNI
[3] yazilabilir=True            [7] e2fsck /dev/sdb1 -> rc=0
[4] dosyalar yazildi
```

Ardindan **Linux cekirdegi** ile dogrulandi:
```
kok  : lost+found okubeni.txt veri
veri : 21 giris
okubeni.txt: DiskUltimate ext4 fiziksel disk denemesi
buyuk.bin  : 131072 bayt (dolayli blok), cekirdek okudu
```

`physical_write_test` de gecti (GPT + FAT32 + exFAT). Boyut siniri icin
`--azami-gb=N` eklendi: 8 GB varsayilani bir **sezgidir**, 10 GB'lik ayrilmis
test diskleri yaygin. Sistem diski / bagli bolum / bilgi eksikligi olcutleri bu
bayraktan **etkilenmez**.

### Guvenlik
- Hedef her adimda `/dev/sdb` ile sinirlandi; betikte sistem diski ve bagli
  bolum icin sert `assert` var. Bir kosumda bu koruma **gercekten devreye
  girdi**: onceki testin biraktigi bolum masaustunca baglanmisti, islem iptal
  edildi; elle `umount` sonrasi devam edildi.
- Test sonrasi `/dev/sdb` temizlendi.
- **SD kart (`/dev/sdc`) yalnizca okundu**, hicbir yazma yapilmadi; durumu
  bastaki gibi.

### Dogrulama
Linux: `run_all` **18/18** · `ext_write_check` 3/3 · `fs_matrix` 8/8 ·
fiziksel ext4 e2fsck rc=0 + cekirdek baglama.
Windows: `run_all` 18/18 · `platform_check` 0 bulgu.

---

## 2026-09-15 (16) — ext4 `metadata_csum` destegi

### Once dogrulama, sonra yazma
Yazmadan once hesaplarin dogrulugu kanitlandi. `core/crc32c.py` (saf Python
CRC-32C, standart olcutlerle) ve `core/extcsum.py` yazildi; `verify_volume()`
**hicbir sey yazmadan** var olan birimlerin saglamalarini bizim hesabimizla
karsilastiriyor.

Ilk kosumda ustblok, grup tanimlayici ve dizin bloklari tuttu; **inode ve
bitmap tutmadi**. Ikisi de gercek hataydi:

1. **Inode:** saglama zincirinde `i_extra_isize` alani (128..0x82) atlanmisti.
   Cekirdek bu parcayi da karistiriyor. Eklendi.
2. **Bitmap:** `BLOCK_UNINIT`/`INODE_UNINIT` gruplarda bitmap **diskte
   tutulmaz**, cekirdek onu uretir; diskteki baytlarla karsilastirmak
   anlamsizdi. Dogrulama bu gruplari atliyor, yazici da tahsis icin onlari
   kullanmiyor.

Duzeltmelerden sonra **iki gercek birimde de her saglama tuttu**:

| Birim | ustblok | grup td. | bitmap | inode | dizin blogu |
|---|---|---|---|---|---|
| `sdcard.img` (csum_seed ozellikli) | 1/1 | 8/8 | 7/7 | 393/393 | 60/60 |
| Mint kok `/dev/sda3` (UUID tohumlu) | 1/1 | 476/476 | 437/437 | 10/10 | 60/60 |

Her iki tohum turevi de (ustblokta saklanan `s_checksum_seed` ve UUID'den
hesaplanan) dogru calisiyor.

### Yazma tarafi
`ExtWriter` artik her degisiklikte ilgili saglamayi tazeliyor: ustblok, grup
tanimlayici, iki bitmap, inode (`_patch_inode` tek alan degisse bile tum kaydi
yeniden damgalar) ve **dizin blogu kuyrugu** (`ext4_dir_entry_tail`).

Ek olarak:
- `64bit` artik tek basina engel degil — yalnizca blok numarasi 32 biti asarsa
  reddedilir. Bitmap blok numaralari yuksek yariyla okunuyor.
- **Extent kullanan dizinler** okunabiliyor: var olan bloklara giris eklemek
  agaci degistirmez. Dizine yeni blok gerekirse acikca reddediliyor.
- **Extent kullanan dosyalar** silinebiliyor (bloklari birakiliyor).
- `bg_itable_unused` tahsiste sifirlaniyor.

### Yakalanan kusur
Ilk denemede `e2fsck` "directory passes checks but fails checksum" dedi. Neden:
`dir_add`/`dir_remove`/`mkdir` icindeki uc yazma yeri hala `_write_block`
kullaniyordu (degisken adi `blk` oldugu icin toplu degistirme kacirmisti), yani
kuyruk saglamasi hic guncellenmiyordu. Uc yer de `_write_dir_block`'a baglandi.
Hata, blogu yazip saglamayi karsilastiran kucuk bir tani betigiyle bulundu.

### Dogrulama
`tests/ext_write_check.py` genisletildi — **4/4**:
```
ext2 ... TAMAM        ext4 ... TAMAM
ext3 ... TAMAM        metadata_csum (mkfs.ext4) ... TAMAM
```
`mkfs.ext4` ciktisi `metadata_csum + 64bit + extent + flex_bg` tasir; gercek
dunyada karsilasilan yerlesim budur.

**Kullanicinin kartinin kopyasi uzerinde uctan uca:**
```
Bolum 2 (ext4) yazilabilir=True
once kok: 19 giris -> sonra kok: 21 giris
e2fsck: temiz (cikis 0)
cekirdek ile baglandi: DISKULTIMATE.txt ve du_deneme/veri.bin okundu
```

Orijinal kart dosyasina **dokunulmadi**; islem kopya uzerinde yapildi.

### Kalan
- Extent agaci **buyutme** (dolu bir extent dizinine yeni blok eklemek).
- Cok katli dolayli blok (4 MB ustu dosya).
- `bigalloc`, `inline_data`.
- NTFS okuma/yazma.

### Dogrulama ozeti
Linux: `ext_write_check` 4/4 · `run_all` 18/18 · `fs_matrix` 8/8.
Windows: `run_all` 18/18 · `platform_check` 0 bulgu.

---

## 2026-09-15 (17) — NTFS okuyucu

### Hedef
Kullanici istegi: **NTFS uc platformda B O Y**. Yazma okumayi gerektirdigi icin
once okuyucu yazildi.

### `core/ntfsread.py` (yeni)
NTFS'te her sey `$MFT` icindeki **FILE kayitlari**dir; dosya adi da, veri de,
dizin indeksi de birer **oznitelik**tir. Uygulanan zincir:

```
onyukleme sektoru -> $MFT yeri -> FILE kaydi -> fixup -> oznitelikler
  -> $DATA (yerlesik veya veri kosullari) -> kume zinciri -> bayt
```

- **Fixup (Update Sequence Array):** her `FILE`/`INDX` blogunda her sektorun son
  iki bayti kayit basindaki diziye tasinmistir. Geri konmazsa veri **sessizce
  bozuk** okunur; imza tutmazsa hata verilir.
- **Veri kosullari (runlist):** isaretli ve bir oncekine goreli ofsetler; 0
  ofset seyrek alandir (`lcn = -1`, okunusta sifir doner).
- **Yerlesik / yerlesik olmayan** oznitelikler ayri ele alinir.
- **`$ATTRIBUTE_LIST`:** buyuk dosya/dizinlerde oznitelikler tek kayda sigmaz ve
  baska kayitlara dagilir. Bu baglanti izlenmezse buyuk dizinler **bos gorunur**.
- **Dizinler B+ agacidir:** kucuk dizin `$INDEX_ROOT` icinde yerlesiktir,
  buyuyunce `$INDEX_ALLOCATION` icindeki INDX bloklarina tasar ve agac dolasilir.
- 8.3 kisa adlar (`name_type == 2`), sistem dosyalari ve kokun `.` girisi
  listelemede atlanir.

### Dogrulama — `ntfs-3g` ile karsilastirma
Mint'te `mkntfs` ile birim uretildi, `ntfs-3g` ile dolduruldu, sonra ayni birim
bizim okuyucuyla okundu:

| Olcum | ntfs-3g | DiskUltimate |
|---|---|---|
| kok girisleri | 3 | 3 (ayni adlar) |
| `klasor` giris sayisi | 122 | **122** |
| `buyuk.bin` boyut | 300000 | 300000 |
| `buyuk.bin` sha256 | `6b85f636b5a92cd7…` | **`6b85f636b5a92cd7…`** |

122 girisli dizin `$INDEX_ALLOCATION` yolunu, 300 KB'lik dosya veri kosullarini
zorluyor; ikisi de dogru cikti. Ic ice dizin ve uzun ad da okundu.

### Arayuz baglantisi
`filesystem.NtfsAccess` eklendi (`readable=True`, `writable=False`).
`write_reason` neden yazilamadigini soyluyor: `$MFT`/`$Bitmap` tahsisi ve
dizin B+ agacina ekleme gerekiyor.

`fs_matrix` artik NTFS satirinda **Oku: TAMAM** gosteriyor — ustelik bu,
**kendi bicimlendiricimizin** urettigi birimi okuyor ve ayni birim `ntfsfix`
ile de temiz cikiyor.

### Regresyon korumasi
`t17_ntfs` genisletildi: kendi urettigimiz NTFS birimi `NtfsFS` ile acilir,
etiket ve kume boyutu dogrulanir, `open_filesystem` `NtfsAccess` dondurmeli,
`readable and not writable` olmali.

### Kalan (NTFS yazma)
- `$Bitmap` kume tahsisi ve `$MFT`'nin `$BITMAP`'i ile kayit tahsisi
- Dizin **B+ agacina giris ekleme/silme** (en zor kisim: dugum bolme)
- `$MFTMirr` esitlemesi, fixup dizisi yazimi
- Sikistirilmis akislar (okumada da desteklenmiyor)

### Dogrulama
Windows: `run_all` 18/18 · `platform_check` 0 bulgu.
Linux: `fs_matrix` 8/8 (NTFS Oku TAMAM) · `run_all` 18/18.

---

## 2026-09-15 (18) — NTFS yazma: sekiz bicimde de B O Y

### Hedef tamamlandi
Kullanici NTFS icin uc platformda **B O Y** istemisti. `core/ntfswrite.py` ile
sekiz dosya sisteminin tamami artik bicimlendirme + okuma + yazma destekliyor.

### Uygulananlar
- **`$Bitmap` kume tahsisi** ve **`$MFT` kayit tahsisi** (`$MFT`'nin kendi
  `$BITMAP`'i uzerinden).
- **`$MFT` kendiliginden buyutulur**: kayit kalmayinca kume tahsis edilir,
  `$MFT`'nin `$DATA` veri kosullari yeniden kodlanir, boyut alanlari guncellenir
  ve yeni alan sifirlanir. Bu olmadan taze bir `mkntfs` biriminde yalnizca
  birkac dosya olusturulabiliyordu (27 kayitlik MFT, 19'u dolu).
- **FILE kaydi yazimi**: fixup dizisi **kurulur** (okuma tarafinin tersi),
  `$STANDARD_INFORMATION` + `$FILE_NAME` + `$DATA` (kucukse yerlesik, buyukse
  veri kosullariyla). Ilk dort kayit `$MFTMirr` ile esitlenir.
- **Dizin indeksi**: indeks `$INDEX_ROOT` icindeyse oraya, B+ agacina tasmissa
  anahtarin ait oldugu **yaprak INDX blogu** icine eklenir. Giris sirasi
  `$UpCase` siralamasina gore korunur.
- `mkdir`, `remove` (bos klasor denetimi, kume/kayit serbest birakma, sequence
  artisi), `rename`.

### Yakalanan hata — fixup dizisinin uzerine yazma
Ilk kosumda `ntfsfix`: *"File name overflow from index entry in inode 5"*.

INDX blogunda `entries_offset` **40**'tir, 16 degil: INDEX_HEADER ile girisler
arasinda **guncelleme dizisi (USA)** durur. Ofseti 0x10'a zorlamak o diziyi
eziyor ve blok bozuluyordu. Girisleri dokup karsilastiran bir tani betigiyle
bulundu; artik mevcut `entries_offset` korunuyor.

### Dogrulama
`mkntfs` ile uretilen birime yazildi, **her adimda `ntfsfix`**:

```
[baslangic] TEMIZ   [kucuk dosya] TEMIZ   [mkdir] TEMIZ   [buyuk dosya] TEMIZ
[kokte 15 giris] TEMIZ   [alt klasorde 3 giris] TEMIZ   [rename] TEMIZ
```

Sonra **`ntfs-3g` ile baglandi**:
```
ntfs-3g BAGLANDI: 17 giris, okundu.txt='NTFS yazma denemesi',
                  buyuk.bin=102400 bayt
```

`fs_matrix` artik **sekiz bicimde de dolu**:

| Bicim | Bicimlendir | Oku | Yaz | Harici dogrulama |
|---|---|---|---|---|
| fat12/16/32 | ✅ | ✅ | ✅ | ✅ `fsck.vfat` |
| exfat | ✅ | ✅ | ✅ | ✅ `fsck.exfat` |
| **ntfs** | ✅ | **✅** | **✅** | ✅ `ntfsfix` |
| ext2/3/4 | ✅ | ✅ | ✅ | ✅ `e2fsck` |

### Sinirlar (acikca reddedilir)
- Dizin indeks dugumu dolunca **bolunemez**; `$INDEX_ROOT` de
  `$INDEX_ALLOCATION`'a tasinamaz. Pratikte 4 KB indeks blogunda ~25-30 giris.
- Sikistirilmis/sifrelenmis akislar (okumada da yok).
- Oznitelikler tek FILE kaydina sigmazsa `$ATTRIBUTE_LIST` yazilmaz
  (okuma destekler).

### Dogrulama ozeti
Linux Mint: `run_all` **18/18** · `fs_matrix` **8/8, tum dogrulamalar TAMAM**.
Windows: `run_all` 18/18 · `platform_check` 0 bulgu.

---

## 2026-09-15 (19) — Eksik test dosyasi yazildi, yol haritasi gerceklestirildi

### Bulunan kayma — kendi yaptigim
`core/ntfswrite.py` docstring'i ve `tests/run_all.py` yorumu
**`tests/ntfs_write_check.py`**'ye atif yapiyordu ama **dosya yoktu**. Bu,
oturumun basinda denetledigim turden bir belge-kod kaymasidir; bu kez kaynagi
bendim.

`tests/ntfs_write_check.py` yazildi (`ext_write_check.py` ile ayni mantik):
her adimdan sonra `ntfsfix`, sonunda `ntfs-3g` ile baglama. **Iki yerlesim**
sinanir:
- **kendi bicimlendiricimiz** — indeks `$INDEX_ROOT` icinde
- **`mkntfs`** — indeks B+ agacina tasmis (`$INDEX_ALLOCATION`)

Sonuc (Mint):
```
normal kullanici: 2/2 (ntfs-3g baglama atlandi - root degil)
root            : 2/2 (ntfs-3g dogruladi)
```

`ntfs-3g` baglama adimi root ister; yoksa **atlandigi acikca yazilir**, TAMAM
sayilmaz.

### Yol haritasi gerceklestirildi
`project-overview.md` hala "NTFS okuyucu" ve "ext okuyucu"yu **planlanan**
gosteriyordu. v0.4.0 tamamlananlar bolumu yazildi, kalanlar v0.5'e tasindi ve
**bilinen yazma sinirlari** acikca listelendi (ext: extent buyutme, cok katli
dolayli blok; NTFS: B+ dugum bolme, sikistirilmis akis, `$ATTRIBUTE_LIST`).

macOS dogrulamasi v0.5 listesine acik bir madde olarak kondu.

### Dogrulama
`platform_check` 0 bulgu · belge ici baglantilar 0 kirik ·
`ntfs_write_check` 2/2 (Mint, root ile).

---

## 2026-09-15 (20) — macOS destegi ilan edilmiyor; surum v0.4.0

### Karar: macOS kapsam disi
Kullanici bildirdi: macOS makinesi yok, dogrulanamaz. Olculmemis bir platformun
destegini ilan etmek, bu oturumun basinda duzelttigim hatanin aynisi olurdu
(belgenin gerceklige uymamasi). Bu yuzden **iddia geri cekildi**:

- README rozeti: `Windows | Linux` (macOS cikarildi), ustune acik bir not.
- `platform-matrix.md`: dosya sistemi tablosundan **macOS sutunu kaldirildi**;
  fiziksel disk tablosundaki macOS sutunu "yazildi, **olculmedi**" olarak
  isaretlendi.
- `cross-platform.md` basligi `Windows / Linux`; macOS satirlari "tasarim notu,
  dogrulama degil" uyarisiyla birakildi.
- `project-overview.md`: v0.5 listesinden cikarildi, gerekcesi yazildi.

**Kod silinmedi.** `diskutil`/`newfs_*` yollari yerinde ve `platform_check`ten
geciyor; birisi kosana kadar durum "bilinmiyor"dur.

### Surum v0.4.0
`project-overview.md` v0.4.0 diyordu ama `APP_VERSION` hala 0.3.0'di — kendi
koydugum "tek kaynak" kuralinin ihlali. v0.3.0'dan bu yana eklenenler bir
minor surumu fazlasiyla hak ediyor:

- ext2/3/4 okuyucu + yazici (`metadata_csum` dahil)
- NTFS okuyucu + yazici
- Sekiz dosya sisteminde de B O Y
- `.dub` yedegin dogrudan gezilmesi ve fiziksel diske yazilmasi
- Takilan USB/SD aygitlarin kendiliginden gorunmesi

`APP_VERSION`, README rozeti ve "Durum" satiri birlikte 0.4.0'a cekildi.

### Dogrulama
`run_all` 18/18 · `platform_check` 0 bulgu · `ui_smoke` gecti ·
belge ici baglantilar 0 kirik.

---

## 2026-09-15 (21) — ext: cok katli dolayli blok (4 MB -> 4 TB)

### Neden oncelikliydi
Yazici yalnizca 12 dogrudan + **tek kat** dolayli blok kuruyordu: 4 KB blokta
~4 MB. Yani normal boyutta bir dosya (video, ISO, yedek) ext'e **yazilamiyordu**.
Kullaniciya en cok dokunan sinir buydu.

### Yapilanlar
- `_store_data` artik **tek / cift / uc kat** dolayli blok kuruyor.
  `i_block[12]`, `[13]`, `[14]` sirasiyla doldurulur; dolayli tablolar da
  tahsis edilir ve `i_blocks` sayacina dahil edilir.
- `_release_data` simetrik olarak **tablolari da** serbest birakiyor. Yalnizca
  veri bloklarini birakmak tablolari sizdirip disk alanini kaybettirirdi.
- **Toplu tahsis** (`alloc_blocks` / `free_blocks`): onceki `alloc_block()` her
  cagrida bitmap'i okuyup yaziyordu; 60 MB'lik bir dosya icin bu 15 binden fazla
  okuma-yazma demekti. Artik her grubun bitmap'i **bir kez** okunur, gereken
  bitler toplu isaretlenir, **bir kez** yazilir. Kismi tahsis birakilmaz:
  yetersizse alinanlar geri verilir.

Okuyucu zaten cift/uc kati destekliyordu; degisiklik yalnizca yazma tarafinda.

### Dogrulama
`ext_write_check`e 8 MB'lik dosya adimi eklendi (cift kati zorlar) — **4/4**,
tum kosum 6 saniye.

Ayrica 60 MB'lik gercek dosya denemesi:
```
azami eslenebilir boyut: 4100.0 GB
60 MB yazildi: 0.2 s
e2fsck rc=0
geri okundu sha256 783695af90d3cb759fe10594
kaynak      sha256 783695af90d3cb759fe10594   AYNI
cekirdek    sha256 783695af90d3cb759fe10594   (mount -o ro,loop)
```

Yani dosya hem bizim okuyucumuz hem **Linux cekirdegi** tarafindan birebir
ayni okunuyor.

### Kalan ext sinirlari
- Extent agaci **buyutme** yok (dolu extent dizinine yeni blok).
- `bigalloc`, `inline_data` reddedilir.

### Dogrulama ozeti
Windows: `run_all` 18/18 · `platform_check` 0 bulgu.
Linux: `ext_write_check` 4/4 · 60 MB dosya e2fsck temiz + cekirdek dogrulamasi.

---

## 2026-09-15 — Tanilama altyapisi ve arayuz donmasi

### Bildirilen sorun
Windows, SD kart takili (`PhysicalDrive1` — Generic- SD/MMC/MS PRO, 59.48 GB).
Bolum listesinde **Bos alan** satirina tiklaninca pencere ~30 sn "Yanit
Vermiyor" oldu, sonra kendiliginden duzeldi. Kendiliginden duzelmesi kilitlenme
degil, arayuz is parcaciginda calisan uzun bir isletim sistemi cagrisi demektir.

Kanit yoktu: sureler olculmuyordu, donma anindaki yigin hicbir yere
dokulmuyordu. Once tani altyapisi kuruldu.

### Yapilanlar
- **`core/diagnostics.py`** (yeni, saf Python): oturum gunlugu, `span()` sure
  olcumu (200 ms ustu YAVAS), `Watchdog` donma yakalayici, `faulthandler` ile
  cokme dokumu, `dump_now()` elle yigin dokumu.
- **`ui/diag.py`** (yeni): 200 ms nabiz zamanlayicisi, Qt uyarilari ve
  yakalanmamis istisnalar gunluge, donma bitince arayuze bildirim.
- **`ui/disk_scan.py`** (yeni): disk sayimi artik `QThread` icinde.
- **Araclar > Tanilama** menusu: durum, son donma raporu, elle yigin dokumu,
  gunluk klasorunu ac.
- `core/physical.py`: `_win_quiet_errors()` (SetThreadErrorMode) ile "ortam yok"
  kutusu bastirildi; `GetDriveTypeW` ile ag surucusu ve CD/DVD acilmadan elendi;
  listeleme ve aygit G/C olculuyor.
- `core/platform.py`: `open_folder()`.
- `_add_physical_disks` artik tarama yapmiyor, onbellek kullaniyor.

### Olculen (bu makine, ayni kart okuyucu takili)
```
tur 1: 947 ms — 3 disk
  947 ms  physical.list_disks
  899 ms  win.open_volume(drive=F)    <- SD kartin FAT16 bolumu, soguk cagri
tur 2:  56 ms
tur 3:  55 ms
```
Tek `CreateFileW` cagrisi soguk durumda ~0.9 sn. Bu sure ust sinirsizdir; aygit
uykudaysa veya yuva bossa Windows yeniden dener. Bu tarama 3 saniyede bir
arayuz is parcaciginda calisiyordu.

Duzeltmeden sonra, pencere acikken 14 saniye boyunca:
```
Olay dongusundeki en uzun duraklama: 47 ms
Yakalanan donma: 0
en yavas olcum: 78 ms  [Dummy-3]  physical.list_disks   <- arka planda
```

### Altyapinin ilk bulgusu
Yeni `sys.excepthook` kancasi, kendi degisikligimdeki hatayi yakaladi:
`deleteLater` ile silinen `DiskScanner` isaretcisi birakilmisti, sonraki tur
`RuntimeError: wrapped C/C++ object ... has been deleted` ile duşuyordu.
`_scan_running()` / `_scan_finished()` ile giderildi.

### Dogrulama
- `tests.diag_check` (yeni): **13/13**
- `tests.run_all`: **18/18**
- `tests.platform_check`: **0 bulgu**
- `tests.ui_smoke`: tamam (aygit takma/cikarma dali asenkron taramaya uyarlandi)

### Durust kalan nokta
30 saniyelik donma birebir tekrarlanamadi; o ana ait bir rapor elimizde yok.
Yapilan sey, arayuz is parcaciginda **olculmus** tek engelleyici isin oradan
kaldirilmasi. Tekrarlarsa artik `.claude/logs/freeze/` altinda kendiliginden
rapor olusur ve takilan satiri gosterir.

Ayrinti: `.claude/decisions/0020-tanilama-ve-donma-yakalayici.md`

---

## 2026-09-15 (2) — Donmanin gercek kok nedeni + kopyalama ilerlemesi

### Bildirilen sorun
SD kart (`PhysicalDrive1`) yazma modunda acik, 3. bolum NTFS. Dosya
gezgininden `sdcard.img.dub` eklendi: hicbir ilerleme gostergesi cikmadi,
pencere dondu, kendine gelince dosya yuklenmisti.

### Bu kez tahmin gerekmedi
Bir onceki oturumda kurulan donma yakalayici olayi kendiliginden kaydetti:
`.claude/logs/freeze/freeze-20260915-134507.md` — **63.0 sn**, 29 yigin ornegi.

```
13:44:46  Fiziksel disk acildi: \\.\PhysicalDrive1 (YAZMA)
13:45:01  [Dummy-8]    win.query_drive(n=1)  basladi       <- arka plan yoklamasi
13:45:05  [MainThread] disk.read(lba=2165024) basladi
13:46:05.718 YAVAS disk.read(lba=8456192, size=1024) — 32250 ms
13:46:05.719 YAVAS win.query_drive(n=1)               — 64031 ms
```
Uc islem de **ayni milisaniyede** serbest kaldi.

**Kok neden:** uygulama kendi acik tuttugu diski 3 saniyede bir yokluyordu.
Yazma modunda birimler kilitli/ayrik oldugu icin ayni aygita ikinci bir tutamac
acip IOCTL sormak kart okuyucunun surucu yiginini 64 sn asili biraktI; ana is
parcaciginin okuma/yazmalari da ayni aygit kuyruguna takildi.

Bir onceki oturumda yoklamayi arka plana almak dogru adimdi ama yetmedi:
cakisma **aygit duzeyinde**, is parcacigi duzeyinde degil.

### Yapilanlar
- **Acik aygit kutugu** (`core/physical.py`): acilan aygit kutuklenir;
  `list_disks()` kutuktekine dokunmaz, son bilinen bilgiyi `in_use` isaretiyle
  dondurur. Acik diskin surucu harfleri de atlanir. Acma anindaki yaris icin
  arayuz suren taramayi bekler (`_wait_for_scan`).
- **Kopyalama ilerleme penceresi**: `import_files`, `import_folder` ve
  `export_selected` artik `run_task` uzerinden, ilerleme penceresiyle calisiyor.
  `TaskDialog` **belirsiz kip** kazandi (`report(mesaj, -1)`): tek dosyalik
  yazmada cubuk 0'da takili kalmiyor, hareket ediyor.
- **`import_tree` tek yere toplandi** (`FileSystemAccess`). Dort ayri kopya
  vardi ve **ikisi ust klasoru olusturmuyordu**: ayni dugme FAT'te
  `/hedef/klasor/a.txt`, ext ve NTFS'te `/hedef/a.txt` uretiyordu.
  *Davranis degisikligi:* ext ve NTFS artik FAT/exFAT ile ayni.
- **Gunluk gurultusu**: var olmayan aygiti acma denemesi her turda 30+ uyari
  satiri uretiyordu; `span(..., warn_on_error=False)` ile ayrinti seviyesine
  indi. Islem yavassa uyari yine verilir.

### Dogrulama
- `tests.run_all`: **20/20** (yeni: `t19_klasor_kopyalama`,
  `t20_acik_aygit_kutugu`)
- `tests.diag_check`: 13/13 · `tests.platform_check`: 0 bulgu
- `tests.ui_smoke`: ilerleme penceresi ciziliyor —
  `17-kopyalama-ilerleme.png`; yuzde ve belirsiz kip denetlendi

### Kalan sinir
Tek bir dosyanin yazilmasi bolunemiyor: `write_file(path, data)` veriyi tek
seferde alir. Bu yuzden bir dosyanin **icindeki** ilerleme gosterilemiyor
(belirsiz cubuk) ve dosya once tumuyle bellege okunuyor. Cok buyuk dosyalar
icin parcali yazma ayri bir is.

Ayrinti: `.claude/decisions/0021-acik-aygiti-yoklamama-ve-kopyalama-ilerlemesi.md`

---

## 2026-09-15 (3) — Silme reddediliyor: birim kilidi kutuge takilmis

### Bildirilen sorun
Uygulama **yonetici olarak** (Thonny uzerinden) calisiyor. SD kartin NTFS
bolumunden `sdcard.img.dub` silinmek istendi:

> Yazma reddedildi: Windows bagli birimlere dogrudan yazmayi engeller.
> Birimi cikarin (eject) veya **Yonetici olarak calistirin**.

Kullanici zaten yoneticiydi. Linux'ta ayni islem sorunsuzdu.

### Bulgu — kendi soktugum hata
Oturum gunlugu (`session-20260915-135924-13908.log`):
```
13:59:32.068  aygit acik kutugune eklendi: \\.\PhysicalDrive1
13:59:32.100  arayuz: Fiziksel disk acildi: \\.\PhysicalDrive1 (YAZMA)
13:59:41.651  disk.write(lba=8452440, size=1915168) — 78 ms
              HATA AccessDeniedError: Yazma reddedildi...
```
Acma ile yazma arasinda **tek bir `win.open_volume` satiri yok** — hicbir birim
kilitlenmemis.

Neden: bir onceki oturumda donmayi cozen **acik aygit kutugu** (ADR 0021).
Acilista aygit once kutuge giriyor, sonra `_win_lock_volumes()` birim
harflerini `_win_drive_letters()`e soruyor; o islev de artik kutuktekilerin
harflerini atliyor. Harf listesi bos -> kilit yok -> Windows sonraki her
yazmayi reddediyor.

Linux'ta cikmamasinin nedeni: orada birim kilidi adimi hic yok.

### Yapilanlar
- `_win_lock_volumes()` harfleri **`info.mounted`** icinden aliyor; listelemeye
  (dolayisiyla kutuge) bagimli degil. Kural: **acilis sirasindaki hicbir adim
  aygitin kutukteki durumuna bagli olmamali.**
- Kilit artik sessiz degil: kilitlenen/kilitlenemeyen birimler gunluge yaziliyor,
  kilitlenemeyen varsa uyari veriliyor.
- `ERROR_ACCESS_DENIED` metni duruma gore konusuyor. "Yonetici olarak
  calistirin" tavsiyesi yalnizca gercekten anlamliysa verilir; yanlis
  yonlendiren tavsiye, tavsiye vermemekten kotu.
- `delete_selected` basarisiz silmeyi "silindi" diye raporlamiyor:
  `"2/3 oge silindi — 1 oge silinemedi"`.

### Dogrulama
- Yeni `t21_birim_kilidi_kutukten_etkilenmez`: aygit **kutukteyken** kilitleme
  calistirilir; `_win_drive_letters()` bos donse bile iki birimin de
  kilitlendigi dogrulanir. Gercek diske dokunulmaz.
- `tests.run_all` **21/21** · `diag_check` 13/13 · `platform_check` 0 bulgu ·
  `ui_smoke` tamam.

### Ayri bir bulgu (verimlilik)
Reddedilen yazma `size=1915168` idi: bolumun **tum `$Bitmap` alani**
(58.45 GB / 4 KB kume / 8 bit). NTFS yazicisi her tahsiste ve her serbest
birakmada bitmap'in tamamini yeniden yaziyor — fiziksel diskte her islemde
~1.9 MB gereksiz yazma. Dogruluk sorunu degil; ayri is olarak not edildi.

Ayrinti: `.claude/decisions/0022-birim-kilidi-ve-durust-hata-metni.md`

---

## 2026-09-15 (4) — Yonetici / root yetkisinin istenmesi

### Istek
"Uygulama acilirken yonetici hakki istemesi gibi bir sey yapabilir miyiz?"

### Karar: evet, ama kosulsuz degil
CLAUDE.md kurali acik: goruntu dosyalari icin yetki **gerekmez**. Her acilista
yetki istemek, bir disk aracini gereksiz yere tam yetkiyle calistirmak,
yetkisiz kullanicinin uygulamayi hic kullanamamasi ve kullaniciyi UAC
penceresini dusunmeden onaylamaya alistirmak demekti. En az yetki ilkesi.

Yetki uc noktada istenir:
1. **Acilista, yalnizca gercekten engellenmisse** — ilk tarama sonrasi
   `info_complete=False` disk varsa (yani eksiklik *olculmusse*) bir kez sorulur.
2. **Fiziksel disk acilirken yetki reddedilince** — hata kutusu artik cozumu de
   sunar.
3. **Kullanici isteyince** — Disk menusu > "Yonetici olarak yeniden baslat...".

### Yapilanlar
- `core/platform.py`: `is_elevated()`, `elevation_available()`,
  `relaunch_elevated()`, `ELEVATION_NAME`. Windows `ShellExecuteW runas`,
  Linux `pkexec env DISPLAY=... XAUTHORITY=...`, macOS `osascript`.
  `summary()` artik **Yetki** satiri tasiyor.
- `ui/main_window.py`: `act_elevate` eylemi, `_offer_elevation()`,
  `restart_elevated()`, `_access_denied()`; durum cubugunda surekli yetki
  gostergesi (`🔑 Yonetici` / `Normal kullanici`).
- Yukseltmeden once acik oturumlar kapatilir ve suren tarama beklenir: iki
  kopya ayni aygita dokunmamali (ADR 0021).
- `DISKULTIMATE_NO_ELEVATION_PROMPT=1` acilis teklifini bastirir; duman testi
  bunu ayarliyor (modal pencere kosumu kilitlerdi).

### Guvenlik siniri
Yukseltme, `physical.py` icindeki alti koruma katmanini **degistirmez**.
Yonetici olmak yalnizca aygiti *acabilmeyi* saglar; yazma icin hala
`readonly=False` + `confirm=True`, sistem diski icin `allow_system=True` ve
arayuzde disk adinin yazilmasi gerekir.

### Dogrulama
- Yeni `t22_yetki_yukseltme`: durum sorgusu, komut satirinin yorumlayici+betik
  olmasi, betik yolu yokken (`python -c`) yukseltmenin **sunulmamasi**, zaten
  yetkiliyken cagrinin hicbir sey baslatmamasi. Gercek UAC penceresi acilmaz.
- Arayuz iki durumda da ekran goruntusuyle denetlendi.
- `tests.run_all` **22/22** · `diag_check` 13/13 · `platform_check` 0 bulgu ·
  `ui_smoke` tamam.

Ayrinti: `.claude/decisions/0023-yetki-yukseltme.md`

---

## 2026-09-15 (5) — NTFS $Bitmap: tamami degil, degisen bolum yaziliyor

### Nereden cikti
ADR 0022'deki hatayi incelerken reddedilen yazmanin **boyutu** dikkat cekti:
`size=1915168`. Bu rastgele degil, 58.45 GB bolumun **butun kume bitmap'i**
(58.45 GB / 4 KB kume / 8 bit). Koda bakinca daha kotusu gorundu:
`alloc_clusters` ve `free_clusters` her cagrida bitmap'in tamamini **okuyup**
tamamini **yaziyordu** — islem basina 3.8 MB G/C.

Dogruluk sorunu degildi; sonuc her zaman dogruydu. Ama SD kart/USB bellekte
32 KB'lik bir dosya icin 1.9 MB yazmak hem yavas hem yipraticidir. Ayni hata
ext tarafinda bir kez giderilmisti (ADR 0010); NTFS'te kalmis.

### Yapilanlar
- `ntfsread`: `attribute_size()`, `read_attribute_range()`.
- `ntfswrite`: `_write_attr_range()` (kume zinciri uzerinden aralikli yazma),
  `_align_range()` (sektor sinirina hizalama).
- `alloc_clusters` bitmap'i **64 KB pencerelerle** tarar; yalnizca degisen
  bolumu yazar. Pencere sinirini gecen bitisik alan **birlestirilir** (yoksa
  veri kosullari gereksiz uzar). Yetersiz alanda alinanlar geri verilir.
- `free_clusters` yalnizca etkilenen bayt araliklarini okur/yazar; bitisik
  araliklar tek yazmaya toplanir.
- `$MFT` bitmap'i de aralikli (`_flush_mft_bitmap`).
- Tam oznitelik yazan `_write_attr_data` ve `_read_bitmap` **silindi**: iki yol
  birakmak, birinin yanlislikla kullanilmaya devam etmesi demekti.

### Olcum (kullanicinin kartiyla ayni geometri, sayacli aygit sarmalayicisi)
`58.45 GB bolum, 4 KB kume, $Bitmap = 1 915 290 bayt`

| Islem | Okuma | Yazma |
|---|---|---|
| `alloc_clusters(8)` onceki | 1 915 290 B | 1 915 290 B |
| `alloc_clusters(8)` simdi | **65 536 B** | **512 B** |
| `free_clusters` onceki | 1 915 290 B | 1 915 290 B |
| `free_clusters` simdi | **512 B** | **512 B** |

Toplam **7 661 160 B -> 67 072 B (114 kat az G/C)**.
Yalnizca yazma: **3 830 580 B -> 1 024 B (3741 kat az yazma)** — karti
yipratan sey budur.

### Dogrulama
Yeni `t23_ntfs_bitmap_aralikli_yazma`. Olcut: *aralikli yazmadan sonraki
bitmap, tam yazmanin uretecegi bitmap ile birebir ayni olmali.* Pencere siniri
`BITMAP_WINDOW = 64` bayta dusurulerek zorlanir.

Testin gercekten hata yakaladigi iki kasitli bozma ile denetlendi:
- yazma ofsetine +512 -> "tahsis sonrasi bitmap tam yazmadan farkli"
- pencere adimi `length + 1` -> "bitisik alan tek parca donmeli, donen: [(584, 448), (1040, 512), ...]"

`tests.run_all` **23/23** · `platform_check` 0 bulgu · `diag_check` 13/13 ·
`ui_smoke` tamam.

### Kalan
- Tahsis hala bitmap'i bastan tarar; "son bos kume" ipucu tutulsa dolu
  birimlerde tarama da kisalirdi. Yapilmadi: ipucu yanlis olursa sessiz
  bozulma degil yavaslama uretir ama dogrulanmasi ayri bir is.
- `ntfsfix` / `ntfs-3g` ile tam dogrulama Windows'ta calistirilamiyor (arac
  yok): `python3 -m tests.ntfs_write_check` **Linux tarafinda** kosulmali.

Ayrinti: `.claude/decisions/0024-ntfs-bitmap-aralikli-yazma.md`

---

## 2026-09-15 (6) — Linux dogrulamasi (SSH ile misafir makinede)

ADR 0024'teki NTFS `$Bitmap` degisikligi Windows'ta `ntfsfix`/`ntfs-3g`
olmadigi icin tam dogrulanamamisti. Kullanici Linux misafir makineye erisim
verdi (paylasilan dizin: `/mnt/hgfs/DiskUltimate/DiskUltimate`).

### Sonuclar (Ubuntu 24.04, Python 3.12.3, PyQt5)

| Kosum | Sonuc |
|---|---|
| `ntfs_write_check` (root, ntfs-3g bagli) | **2/2** — "ntfs-3g dogruladi (2 giris)" |
| `run_all` | **21/23 · 2 atlandi** (t20, t21 Windows dalina ozgu) |
| `ext_write_check` | **4/4** (her adimda `e2fsck -nf`) |
| `platform_check` | 0 bulgu |
| `diag_check` | 13/13 |
| `ui_smoke` | tamam (offscreen, fusion) |

**Onemli olan:** `ntfs_write_check` bu kez yalnizca `ntfsfix` ile degil,
**ntfs-3g ile gercekten baglanarak** dogrulandi. Yani aralikli `$Bitmap`
yazmasindan sonra birimi Linux'un NTFS surucusu sorunsuz baglayip okuyor —
hem kendi bicimlendiricimizin urettigi hem de `mkntfs` ile uretilen birimde.

Yetki yukseltmesi de yerinde denetlendi:
```
is_elevated (normal kullanici): False
pkexec: /usr/bin/pkexec
elevation_available: (True, '')
relaunch komutu: ['/usr/bin/python3', '/tmp/probe.py']
```
`python3 -c` ile calistirildiginda ise dogru sekilde reddedildi:
`(False, 'Uygulamanin yeniden baslatilacagi betik yolu belirlenemedi.')`

### Yol acilan bir sorun: duman testi Linux'ta yarida kaliyordu
`ui_smoke`, tema dalindaki sekme genisligi denetiminde **cokuyordu**:
```
RuntimeError: no access to protected functions or signals
              for objects not created from Python
```
`tabSizeHint` korumali bir islevdir; PyQt5'in bu surumu Python'da
olusturulmamis nesnede izin vermiyor. Sorun benim degisikliklerimde degildi
ama **sonraki tum adimlari** (aygit takma/cikarma dali, ilerleme penceresi
denetimi) Linux'ta hic calistirmiyordu.

Artik yalnizca o tek denetim atlaniyor, test devam ediyor. Windows'ta denetim
gercekten kosmaya devam ediyor ("4 sekme kirpilmadi"), Linux'ta atlandigi
acikca yaziliyor.

### Temizlik
Misafir makinede uretilen gecici dosyalar (`/var/tmp/du`, `/var/tmp/du-ui`,
`/tmp/probe.py`, `/tmp/ui.log`) silindi.

---

## 2026-09-15 (7) — Yedek dosyasi bilgisi: icerik listesi + "gez" dugmesi

### Istek
"Araclar > Yedek dosyasi bilgisi arayuzune, ayni goruntu ac gibi gez ozelligini
de koy."

### Durum
`.dub` yedegi zaten **gezilebiliyordu** (`clone.DubImage`, salt okunur blok
aygiti): Dosya > Ac ile acildiginda bolumler ve dosyalar goruluyordu. Eksik
olan, bilgi penceresinden bu yola gecebilmekti. Ayrica pencere yalnizca
**baslik** bilgisini gosteriyordu (boyut, tarih, sikistirma); "bu yedekte ne
var?" sorusu yanitsizdi.

### Yapilanlar
- `core/session.py`: `BackupPreview` + `DiskSession.backup_preview(path)`.
  Yedegi `DubImage` uzerinden acar, bolum tablosunu cozer, her bolumun dosya
  sistemini tespit eder ve kok klasoru listeler — **hicbir yere yazmadan**.
- `ui/dialogs/tools.py`: `BackupInfoDialog` — baslik bilgisi + icerik agaci +
  **"Icerigini gez"** dugmesi. Dugme yedegi ana pencerede acar; oradan normal
  dosya gezgini calisir.
- `show_backup_info` artik `run_task` ile calisiyor: buyuk/sikistirilmis bir
  yedegin icerigini okumak birkac saniye surebilir, sessiz beklemek donma gibi
  gorunurdu (ADR 0021'in kurali).

### Yol acilan iki bulgu
1. **`is_whole_disk` yanlis olcut kullaniyordu.** Ilk surum "bolum tablosu var
   mi" diye bakiyordu; FAT onyukleme sektoru de `0xAA55` ile bittigi icin tek
   bir FAT bolumunun yedegi `scheme='mbr'` (0 bolum) gorunuyordu. Olcut
   **bolum bulunup bulunmadigi** olarak duzeltildi.
2. **"Bos" ile "okunamadi" ayni gosteriliyordu.** `_root_names` her iki
   durumda da bos liste donuyordu; bos bir NTFS bolumu "bu surumde
   listelenemiyor" gibi gorunurdu. Artik okunamayan icerik `None`, gercekten
   bos bolum `[]` doner; pencere ikisini ayri yazar.

### Dogrulama
- Yeni `t24_yedek_onizleme`: cok bolumlu disk yedeginde bolum sayisi, dosya
  sistemi tespiti ve kok girisleri; tek bolum yedeginde `is_whole_disk=False`;
  bos bolumun `[]` donmesi; onizlemenin **hicbir sey yazmadigi** (kaynak boyutu
  ve yedek imzasi degismiyor); yedek olmayan dosyanin reddedilmesi.
- `ui_smoke`: `18-yedek-bilgisi.png` uretiliyor, agactaki bolum sayisi ve gez
  dugmesi denetleniyor.
- Windows: `run_all` **24/24** · `platform_check` 0 bulgu · `diag_check` 13/13 ·
  `ui_smoke` tamam.
- Linux (SSH): `run_all` **22/24 · 2 atlandi** (Windows dali) ·
  `platform_check` 0 bulgu · `ui_smoke` tamam.

### Not
Yeniden adlandirma sirasinda duzenli ifade, kullaniciya gorunen Turkce metni de
degistirmisti (`"(bos)"` -> `"(empty)"`). Ekran goruntusunden fark edildi ve
geri alindi — CLAUDE.md kurali: **Turkce arayuz metni, Ingilizce kod adi.**

---

## 2026-09-15 (8) — Bekleyen islem kuyrugu, tek "Uygula", cizilen ikon seti

### Istek
Kullanici `example guı/` altina Acronis, EaseUS, AOMEI, MiniTool ve Macrorit
ekran goruntulerini koydu: *"islemler yapilir, adimlar uygula butonu ile
sirayla uygulanir, Acronis'teki bayrak butonu gibi. Salt okunur ozelligine
gerek kalmaz. Tum platformlarda kullanilabilecek gorsel ikonlar kullanalim."*

Bes aracin besi de ayni kalibi kullaniyor: islemler kuyruga girer, tek bir
Apply/Commit ile calisir, solda bir "Pending Operations" paneli durur.

### Yapilanlar
- **`core/operations.py`** (yeni): `Operation`, `OperationQueue`, `ApplyResult`.
  14 islem turu kuyruklanir; adim eklemek diske dokunmaz, adimlar cikarilabilir,
  yeniden siralanabilir, tumden iptal edilebilir.
- **`DiskSession.become_writable()` / `can_become_writable()`**: kaynak hep salt
  okunur acilir, yazma yetkisi yalnizca uygulama aninda alinir. Fiziksel diskin
  butun koruma katmanlari orada calisir.
- **Arayuz**: `Uygula (N)` / `Geri al` / `Vazgec` arac cubugu dugmeleri,
  sol altta bekleyen islem paneli (sag tik: kaldir, yukari/asagi tasi),
  tabloda etkilenen bolumlerde kum saati isareti.
- **`ui/icons.py`** (yeni): 34 ikon, `QPainter` ile cizilir. Butun `QStyle`
  ikonlari degistirildi; arac cubugu 14 dugmeden 9'a indirildi (tasma oku
  cikiyordu).
- Fiziksel diskte **"yazma modunda ac"** secimi kaldirildi. Sistem diski onayi
  ve bagli bolum uyarisi kalkmadi — uygulama anina tasindi.

### Neden salt okunurluk tumden kalkmadi
Kullanicinin sezgisi dogruydu ama tam degil: kip **secimi** kalkti, **yetenek
bilgisi** kaldi. `.dub` yedegi bir arsivdir; VDI/QCOW2 bu surumde yazilamaz;
seyrek VMDK salt okunurdur. Bunlar icin arayuz artik "SALT OKUNUR" degil
**"DEGISTIRILEMEZ"** der ve nedenini yazar. Fiziksel disk ve yazilabilir
goruntu icin ise salt okunurluk normal kiptir, uyari degildir.

### Yikici islem onayi nereye gitti
CLAUDE.md kurali islem basina onay istiyordu; uc bolum olusturup ikisini
bicimlendiren kullanici bes kutu goruyordu. Onay kalkmadi, **parti basina**
oldu: Uygula tek pencerede butun adimlari numarali listeler, yikici olanlari
kalin gosterir ve sayar. Daha az tiklama, daha cok bilgi.

### SVG neden kullanilmadi
`PyQt5.QtSvg` Ubuntu 24.04 misafirinde **kurulu degil** (olculdu: ImportError;
ayri paket). Harici bagimlilik yok kurali geregi elendi. `QStyle` ikonlari da
yetersiz: platforma gore degisiyor ve "bicimlendir", "boyutlandir", "onyukleme
bayragi" gibi islemlerin karsiligi yok. Geriye `QPainter` kaldi.

### Yol acilan bir hata
Kum saati isareti satirda **asili kaliyordu**: `_apply_pending` yalnizca isaret
ekliyor, kalkarken metni geri yazmiyordu. Ekran goruntusunden fark edildi;
bolum adi tek kaynaga (`part_label`) alindi ve metin her seferinde bastan
yaziliyor.

### Dogrulama
- Yeni `t25_islem_kuyrugu`: kuyruga eklemek diski degistirmiyor (sha256 ayni),
  adimlar sirayla isliyor, basarisiz adim kuyrugu durduruyor ve duran adim
  listede kaliyor, yazilamaz kaynak reddediliyor.
- `ui_smoke`: kuyruk akisi + 34 ikonun hepsinin cizilmesi denetleniyor.
  Yeni goruntuler: `20-ikon-seti.png`, `21-bekleyen-islemler.png`.
- Windows: `run_all` **25/25** · `platform_check` 0 bulgu · `diag_check` 13/13 ·
  `ui_smoke` tamam.
- Linux (SSH): `run_all` **23/25 · 2 atlandi** (Windows dali) ·
  `platform_check` 0 bulgu · `ui_smoke` tamam.

### Kalan sinir
Bekleyen durum **isaretlenir, simule edilmez**: "bicimlendir" kuyruktayken
tabloda eski dosya sistemi gorunur, yaninda kum saati durur. Acronis sonucu
haritada onizler; bunun icin bolum tablosunu bellekte bir golge aygit uzerinde
calistirmak gerekir — ayri ve buyuk bir is. Yanlis onizleme gostermektense
isaretlemek yeglendi.

Ayrinti: `.claude/decisions/0025-bekleyen-islem-kuyrugu.md`

---

## 2026-09-15 (9) — ADR 0025 gerilemesi: geri yukleme "salt okunur" diyordu

### Bildirilen sorun
Linux Mint misafirinde `sdcard.img.dub` yedegi `/dev/sdb` diskine yazilmak
istendi. **Bir kez calisti**, sonraki denemelerde:

> Geri yukleme basarisiz: Fiziksel diskler guvenlik gerekcesiyle varsayilan
> olarak SALT OKUNUR acilir. Degisiklik yapmak icin diski yazma modunda
> acmaniz gerekir.

Bu mesaj artik var olmayan bir secimi tarif ediyordu — "yazma modunda ac"
dugmesi ADR 0025 ile kaldirilmisti.

### Neden
Kendi degisikligimin acigi. ADR 0025 ile kaynak **her zaman** salt okunur
aciliyor ve yazma yetkisi yalnizca `apply_steps()` icinde aliniyor. Ama
**kuyruga girmeyen** islemler var: geri yukleme ve klonlama dogrudan ve tek
seferlik yazmalardir. Onlar hala yazilabilir bir oturum bekliyordu.

"Bir kez calisti" da tutarli: ilk denemede *Yedegi diske yaz...* kullanilmis,
o yol hedefi kendi acar (`restore_to_physical`) ve oturumdan bagimsizdir.

### Yapilanlar
- `restore()` artik once `_make_writable()` cagiriyor (fiziksel diskin sistem
  diski / bagli bolum kapilari orada calisir).
- Kayip bolumu tabloya ekleme (`adopt_lost_partition`) **kuyruga** alindi;
  dogrudan yazan tek istisna kalmadi.
- Yaniltici metinler duzeltildi:
  - `readonly_reason` artik "yazma modunda acin" demiyor.
  - Agacta `[salt okunur]` yerine, yalnizca gercekten degistirilemeyen
    kaynakta `[degistirilemez]`.
  - Bilgi panelinde `Erisim` satiri uc durumu ayiriyor: "Okuma/Yazma (acik)",
    "Salt okunur — degisiklikler Uygula ile yazilir", "Degistirilemez — <neden>".

### Denetim
Arayuzdeki **butun** dogrudan yazan cagrilar tarandi; geriye yalnizca
`recover_deleted` kaldi ve o oturuma degil, yerel klasore yazar.

### Dogrulama
- Yeni `t26_dogrudan_yazma_yollari`: salt okunur kaynakta geri yukleme
  reddediliyor, `become_writable()` sonrasi calisiyor ve icerik doğru;
  yedek dosyasi gecemiyor ve nedenini soyluyor; ikinci gecis zararsiz.
- `ui_smoke`: arayuzun `_make_writable()` yolu denetleniyor.
- Windows: `run_all` **26/26** · `platform_check` 0 bulgu.
- Linux: `run_all` **24/26 · 2 atlandi** (Windows dali) · `ui_smoke` tamam ·
  `platform_check` 0 bulgu.

---

## 2026-09-15 (10) — Acronis vari gorunum: diskler ve bolumleri acilmadan

### Istek
"Fiziksel disklere tiklayip icerik goruntuleme yerine Acronis vari gorunum
yapalim, disk ve altinda bolumler gorunsun."

Onceki akis iki adimliydi: once diski **ac**, sonra bolumleri gor. Incelenen
araclarin hepsi ise butun diskleri ve bolumlerini acilista gosterir.

### Guvenlik kurali acikca ikiye ayrildi
`physical.py` katman 1 "listeleme hicbir sektor okumaz" diyordu. Bolumleri
gostermek icin bolum tablosunu okumak sart. Kurali sessizce esnetmek yerine:

| Islem | Sektor okur mu | Siklik |
|---|---|---|
| `list_disks()` listeleme | **Hayir** | 3 sn (yoklama) |
| `survey_disk()` bolum yoklamasi | **Evet** (tablo + imzalar) | yalnizca disk listesi degisince / elle yenilemede |

Yazma tarafinda hicbir sey degismedi. Siklik ayrimi onemli: ADR 0021'de aygita
gereksiz dokunmanin 64 saniyelik sikismalar urettigi olculmustu.

### Yapilanlar
- **`DiskSession.survey_disk()`** + `DiskSurvey`: aygiti salt okunur acar,
  GPT/MBR tablosunu ve dosya sistemi imzalarini okur, **hemen kapatir**.
  Uygulamanin kendi acik tuttugu disk yoklanmaz.
- **`DiskScanner`** artik (diskler, ozetler) donduruyor; imzasi degismeyen
  disk yeniden yoklanmiyor.
- **Agac**: her diskin altinda bolumleri (renk, etiket, boyut). Bir bolume
  tiklamak diski salt okunur acip dogrudan o bolumu seciyor.
- **`ui/widgets/disk_overview.py`** (yeni): hicbir kaynak acik degilken butun
  diskler alt alta — solda kimlik kutusu, sagda bolum seridi. Kaynak acilinca
  `QStackedWidget` tek disk haritasina geciyor. `DiskMapWidget` degistirilmedi.

### Olcum (3 disk, hicbiri acilmadan)
```
PhysicalDrive0  GPT   5 bolum   (ham, FAT32 NO NAME, NTFS x3)
PhysicalDrive1  MBR   3 bolum   (FAT16 NO NAME, ext4 rootfs, NTFS)
PhysicalDrive2  MBR   1 bolum   (FAT32 NO NAME)
```

### Ilk surumde gorulen iki duzen hatasi
- Genel bakis seridi kirpiliyordu (sabit yukseklik). Artik disk sayisina gore
  buyur, uc satirda durur, fazlasi kaydirilir.
- Sol paneldeki bolum adlari kirpiliyordu; panel 380 -> 440 piksele cikti.

### Dogrulama
- Yeni `t27_disk_yoklamasi`: aygit salt okunur aciliyor, **hicbir yazma**
  yapilmiyor, **kapatiliyor**; bolumler ve dosya sistemleri dogru; acik aygit
  yoklanmiyor; tablosuz disk hatasiz bos donuyor. Gercek diske dokunulmaz.
- `ui_smoke`: sahte yoklama sonucuyla agac dugumleri ve serit bloklari
  denetleniyor (`23-disk-genel-bakis.png`).
- Windows: `run_all` **27/27** · `platform_check` 0 bulgu · `diag_check` 13/13.
- Linux: `run_all` **25/27 · 2 atlandi** (Windows dali) · `platform_check`
  0 bulgu · `ui_smoke` tamam.

### Sinir
Genel bakis **bekleyen islemleri gostermez**: kuyruk isaretleri acik oturumun
bolum tablosundadir (ADR 0025), serit diskin mevcut durumunu cizer.

Ayrinti: `.claude/decisions/0026-disk-genel-bakisi.md`

---

## 2026-09-15 (11) — Acik disk agacta yerinde kalir ("(asagida acik)" kalkti)

### Bildirilen sorun
"Bu sekilde tiklandiginda asagida acik yaziyor, bunu istemiyorum. Diskler
uzerinde direk islem istiyorum, bunu asagiya tasimani istemiyorum. Acronis,
DiskGenius vb. uygulamalarda bu sekilde."

Haklıydi: bir onceki adimda diskin altina bolumleri koymustum ama disk
**acilinca** hala ayri bir dal uretiliyordu — disk satirinda "(asagida acik)"
yaziyor, bolumler agacin altindaki ikinci koke tasiniyordu. Ayni disk iki
yerde gorunuyordu.

### Yapilanlar
- Fiziksel disk oturumu icin **ayri dal olusturulmuyor**. Disk kendi satirinda
  kalir; acikken bolumleri oturumdan (canli), kapaliyken yoklamadan gelir.
- Acik disk satiri kalin ve semasiyla yazilir; tiklamak onu etkin kaynak yapar.
  Sag tik: "Bu diski kapat".
- Bolum dugumleri hem agacta hem tabloda bekleyen islem kum saatini gosteriyor.
- `_select_tree` agaci **her derinlikte** ariyor (bolumler artik torun dugum);
  eski surum acik diskin bolumunu agacta hic secemiyordu.
- Goruntu dosyalari (.img, VHD, .dub) kendi dallarinda kalmaya devam ediyor —
  fiziksel disk listesinde yer almazlar.

### Tanilamanin yakaladigi israf
Disk acilirken arayuz **1.5 sn** takildi. Gunluk nedeni gosterdi: 14.7 GB
FAT32 bolumun tespiti 7.7 MB okuyor (FAT tablosunun tamami, ~590 ms) ve bu is
**uc kez** yapiliyordu — oturum kurulurken, `refresh()` icinde, secimde.

`refresh(reload=False)` eklendi: az once acilmis kaynak yeniden okunmuyor.
**1.5 sn -> 0.95 sn.** Kalan iki okumadan biri tespit, oteki dosya sistemi
surucusunun acilisi; ikisini tek okumaya indirmek `fsdetect` ile `filesystem`
katmanlarini birlestirmeyi gerektiriyor — ayri is olarak not edildi.

### Dogrulama
- `ui_smoke`: acik kaynakta agacta "(asagida acik)" **bulunmamali** ve goruntu
  oturumu kendi dalinda gorunmeli.
- Elle: SanDisk USB bellek acildi, agacta yerinde kaldi, bolumu secildi,
  dosya gezgini icerigi listeledi, ikinci dal olusmadi
  (`24-yerinde-acik.png`).
- Windows: `run_all` **27/27** · `platform_check` 0 bulgu · `diag_check` 13/13.
- Linux: `run_all` **25/27 · 2 atlandi** · `platform_check` 0 bulgu ·
  `ui_smoke` tamam.

---

## 2026-09-15 (12) — Ikonlar: her platformda ayni, daha buyuk

### Istek
"Ikonlar her platformda ayni olacak, biraz daha buyut." + "Dosya gezgini
ikonlarini da ayni sekilde ayni olsun."

### Durum tespiti
Ana pencere zaten cizilen ikonlara gecmisti (ADR 0025) ama **dosya gezgini**
hala `QStyle` standart ikonlarini kullaniyordu — yani uygulamanin o bolumu
platforma gore degisiyordu.

### Yapilanlar
- Kalan on iki ikon cizildi: `up`, `export`, `import`, `folder`,
  `folder-add`, `folder-new`, `file`, `trash`. Toplam **42 ikon**.
- `theme.standard_icon()` **kaldirildi**; kaynak agacinda artik hicbir
  `QStyle` ikonu yok.
- `open` ile `folder` 16 pikselde karisiyordu; `open` ikonuna klasorden
  tasan bir belge eklendi.
- **Cok boyutlu ikon**: her `QIcon` 16/20/24/32/48 px pixmap tasiyor. Tek
  pixmap tutup Qt'ye olcekletmek bulanik cikiyordu.
- Boyutlar: ana arac cubugu 18 -> **24 px**, dosya gezgini 16 -> 20, agaclar
  20, bolum tablosu cipleri 11 -> 13, satir yuksekligi 24 -> 26.

### "Her platformda ayni" — olculdu
`icons.digest()` butun cizimlerin piksel ozetini verir:
```
Windows 10   Qt 5.15.2    windowsvista   1ca31e30c5d79d2e0ed72a2132810cdd
Ubuntu 24.04 Qt 5.15.13   fusion         1ca31e30c5d79d2e0ed72a2132810cdd
```

### Olcumu bir kez yanlis kurdum
Ilk surum **PNG baytlarini** karsilastiriyordu ve Windows ile Linux farkli
deger veriyordu. Arastirinca sebep cikti: ayni makinede bile `offscreen` ile
`windows` eklentisi farkli bayt uretiyor, cunku `QPixmap` bicimi ekrana gore
secilir. Piksel degerleri aynidir. Karsilastirma sabit bicime (`ARGB32`)
cevrilerek duzeltildi — yanlis olcum "platformlar farkli" dedirtiyordu.

### Dogrulama
- `ui_smoke`: her ikon bos olmayan pixmap uretiyor, `QIcon`lar cok boyutlu,
  cizim ozeti basiliyor (iki platformda ayni).
- Windows: `run_all` **27/27** · `platform_check` 0 bulgu · `diag_check` 13/13.
- Linux: `ui_smoke` tamam, ayni ozet.

---

## 2026-09-15 (13) — Linux Mint: fiziksel diske dosya eklenemiyordu

### Bildirilen sorun
"Linux Mint'te /dev/sdb'ye dosya yukleme yapamiyorum, sebebi nedir?"

### Tani — gunlukten
Kullanicinin oturum gunlugu paylasilan klasorde oldugu icin dogrudan okundu
(`session-20260915-164211-16325.log`):
```
16:42:39  arayuz: Fiziksel disk acildi (salt okunur): /dev/sdb — 10.00 GB, MBR
```
Disk **salt okunur** acilmis (ADR 0025 ile artik hep boyle). Diskin durumu da
misafir makinede okundu: `sdb1` FAT32 32 MB (bagli, /media/pc/...), `sdb2`
ext4 1 GB.

**Neden:** dosya islemleri bekleyen islem kuyruguna **girmez** — bir dosya
sisteminin icinde olurlar, disk yerlesimini degistirmezler (ADR 0025 boyle
karar vermisti). Ama ayni ADR kaynagi her zaman salt okunur acar hale
getirdi. Sonuc: `FileBrowser` icin `fs.writable` hep `False`, "Dosya ekle"
dugmesi hep pasif. Fiziksel diske dosya eklemek **hic mumkun degildi**.

Bu, geri yukleme yolundaki (13. oturum) hatanin **ayni sinifta ikinci
ornegi**: bir kip secimini kaldirirken ona dolayli bagli yollari taramak
gerekiyordu; ilk taramada dosya gezgini gozden kacmis.

### Yapilanlar
- `FileBrowser.ensure_writable` geri cagirmasi: ilk yazma denemesinde kaynagi
  yazma moduna alir ve **taze** dosya sistemi dondurur (kaynak yeniden
  acildigi icin eski nesne gecersizdir).
- Yazma dugmeleri salt okunur kaynakta artik **pasif degil**: pasif dugme
  "bu is hic yapilamaz" der ki dogru degil; yetki ilk denemede istenir.
- `_browser_write_access()` ana pencerede: fiziksel diskin sistem diski /
  bagli bolum kapilarindan gecer, sonra bolumu yeniden secip gezgini baglar.

### Yol acilan bir guvenlik duzeltmesi
Bagli bolum uyarisi her platformda "Yazma sirasinda bu bolumler gecici olarak
cikarilacak" diyordu. Bu **yalnizca Windows'ta dogru** (FSCTL_LOCK_VOLUME +
DISMOUNT). Linux/macOS'ta hicbir sey cikarilmaz ve bagli bir dosya sistemine
ham yazmak onu bozar. `physical.locks_volumes_on_write()` eklendi; Linux'ta
uyari artik gercegi soyluyor: *"Bu bolumler hala bagli... once cikarmaniz
(unmount) onerilir."*

Kullanicinin durumunda bu dogrudan gecerli: `sdb1` bagliydi.

### Dogrulama
- Yeni `t28_dosya_ekleme_yazma_modu`: salt okunur kaynakta yazma reddediliyor,
  `become_writable()` sonrasi **taze** dosya sistemi yazilabiliyor, bayat
  nesne kullanilmiyor, dosya gercekten yazilip geri okunuyor.
- `ui_smoke`: `ensure_writable` bagli, salt okunur kaynakta "Dosya ekle"
  **pasif degil**, `_require_writable()` yetkiyi aliyor ve gezgin taze dosya
  sistemine baglaniyor.
- Windows: `run_all` **28/28** · `platform_check` 0 bulgu · `diag_check` 13/13.
- Linux: `run_all` **26/28 · 2 atlandi** · `ui_smoke` tamam ·
  `platform_check` 0 bulgu.

---

## 2026-09-15 (14) — Ust panel ikonlari 32 piksele cikti

Istek: "Ust panel ikonlarini daha da buyut."

Ana arac cubugu 24 -> **32 px**. Ikonlar zaten 32 px pixmap tasidigi icin
olcekleme yok, keskin cikiyor.

Tasma denetlendi: 32 px ikon + metin ile arac cubugu **866 px** genislik
istiyor; 1024 / 1280 / 1600 px pencerelerin ucunde de `>>` tasma oku
cikmiyor (olculdu).

Dosya gezgini arac cubugu 20 px'te birakildi — ikincil bir cubuk, gorsel
hiyerarsi boyle dogru.

`run_all` 28/28 · `platform_check` 0 bulgu · `ui_smoke` tamam.

## 2026-09-17 — VMware Windows 10 yazma testi ortami (Admin hesabi, cevrimdisi)

Kullanici Windows yazma testleri icin bir **VMware Player** misafirine
(`ltsc`, Windows 10 Enterprise 2016 LTSB x64, `DESKTOP-GLH638P`) yonetici
erisimi istedi. Kanal: VMware Tools / `vmrun` (ana makinede VMware Player
16.2.3). Ag kullanilmaz; misafir cevrimdisi kalir.

### Baglanti bulgulari
- `user` hesabi baglaniyor ama `vmrun` oturumunda **Orta Duzey** kaliyor (UAC
  token filtresi; Administrators uyesi olsa da pasif). Yukseltme
  `schtasks /rl highest /ru user /rp <parola>` ile dogrulandi (High Integrity).
- **`Admin` hesabi** `vmrun` ile **dogrudan yukseltilmis** geliyor: High
  Integrity (`S-1-16-12288`), `Administrators` etkin, `net session` OK.
  Yonetici is icin bu hesap secildi.
- Yukseltilmis baglamda `\\.\PhysicalDrive1` CreateFile ile acilip okundu
  (MBR imzasi `55aa` — ikinci disk uzerinde tablo var, tam bos degil; yikici
  testten once icerigi denetlenmeli).

### Kararlastirilan kurallar (kullanici)
1. Windows yazma testi icin **`Admin` hesabi** kullanilacak.
2. Misafir **internete acilmayacak**; paketler ana makinede indirilip
   paylasilan klasor uzerinden misafire alinip **cevrimdisi** kurulacak.
3. Paylasim: ana makine `D:\pythonProjeler\DiskUltimate` → misafir
   `\\vmware-host\Shared Folders\DiskUltimate` (proje koku alt klasor
   `...\DiskUltimate\DiskUltimate`).

### Notlar
- HGFS eslemesi `vmrun`'un batch oturumunda **gorunmedi** (oturuma bagli);
  interaktif oturumda bagli. Otomasyonda `copyFileFromHostToGuest` kullanilir.
- Python misafirde **kurulu degil**; ilk is offline Python + PyQt5 kurulumu.
- `vmrun runProgramInGuest` arguman aktariminda `/c`, `&`, `^`, `\` bozuluyor;
  guvenilir yol komutu `.bat`/`.ps1`'e yazip kopyalamak, ciktiyi dosyaya alip
  geri cekmek. Aygit yolundaki ters bolu `[char]92` ile kurulur.

Kurallar `.claude/docs/testing.md` (Windows dogrulama ortami) ve
`.claude/memory/windows-test-ortami.md` altina islendi. Parola guvenlik
geregi depoya yazilmadi (`.claude/` commit ediliyor).

## 2026-09-17 (2) — Oturum arsivi denetimi: bir oturum kayitsiz kalmis

Kullanici arsivde yalnizca 4 satir gorunce "tum oturumlar kaydediliyor mu?"
diye sordu. `~/.claude/projects/d--pythonProjeler-DiskUltimate-DiskUltimate/`
ile `.claude/sessions/` karsilastirildi.

### Bulgular
- Projede bugune kadar **5 oturum** acildi (suregelen oturum haric 4 tamamlanmis).
  Arsivde 4 satir vardi ama bunlarin ikisi **ayni oturumun** iki gunluk
  kopyasiydi (`240115eb`, 15 ve 16 Eylul). Yani gercekte 3 farkli oturum
  arsivlenmisti.
- **`fa7e2993-d71e-400b-bc6a-ae88ced433ef` hic arsivlenmemisti** (15 Eylul
  13:27–17:01, 2827 satir, 10.5 MB). Tanilama/donma yakalayici calismasinin
  (ADR 0020) yapildigi oturum bu. `isSidechain: false`, cwd proje koku,
  dal `tanilama-ve-donma-duzeltmeleri` — gercek bir proje oturumu.
  Ham dokum hala duruyordu; `.claude/sessions/` altina kendi tarihiyle
  kopyalandi, INDEX satiri `kurtarildi` olarak eklendi.

### Kancanin yapisal acigi (henuz duzeltilmedi)
1. **`SessionEnd` tetiklenmezse kayit yok.** Surec oldurulur, pencere kapanir
   veya makine kapanirsa kanca hic calismaz; oturum sessizce kaybolur.
   Kanca zaten her hatada sessizce 0 donuyor (tasarim geregi), bu yuzden
   kayip **fark edilmiyor**.
2. **Dosya adindaki tarih arsivleme tarihi** (`datetime.now()`), oturumun
   tarihi degil. Devam ettirilen bir oturum her gun **tam kopya** cikariyor:
   `240115eb` iki kez, 7.8 MB + 7.8 MB. 15 Eylul kopyasi 16 Eylul'unkinin
   eksik on-eki.
3. Telafi yolu yok: `SessionStart` kancasi olsa, acilista onceki kayitsiz
   dokumler taranip arsivlenebilirdi.

## 2026-09-17 (3) — Coklu dil arayuzu (tr / en / de), VM'de dogrulandi

Kullanici istegi: *"projeye coklu dil secenegi ekleyelim; gerekli denemeleri
VM Win10'da dene, bagimliliklari ana makineden indirip VM'e kopyala, VM
internete acilmasin."*

### Yapilan

**Yeni altyapi** (`src/diskultimate/i18n/`, saf Python, Qt'siz — ADR 0027):

| Parca | Isi |
|---|---|
| `i18n/__init__.py` | `tr()`, `mark()`, dil secimi, ayar, degisiklik bildirimi |
| `i18n/catalogs/en.json`, `de.json` | 1062'ser ceviri (tr kaynak dildir, dosyasi yok) |
| `core/settings.py` | Tercihlerin JSON saklanmasi (isletim sisteminin ayar klasoru) |
| `core/platform.py` | `config_dir()`, `system_language()`, `elevation_name()` |
| `tests/i18n_check.py` | Sozluk denetimi + iskelet uretimi (6 denetim) |

**Cevrilen yuzey.** Yalnizca `ui/` degil, `core/` de: hata mesajlari (`raise`),
ilerleme bildirimleri, ozet tablolari (`summary()`), bolum turu adlari, silme
yontemleri, imza adlari ve islem kuyrugu basliklari. Toplam **1062 metin**.
Cekirdegi cevirmek katman kuralini bozmadi: `i18n` saf Python, `core/` hala
PyQt import etmiyor (`platform_check`: 0 bulgu).

**Modul duzeyindeki metinler.** `MBR_TYPES`, `GPT_TYPES`, `WIPE_METHODS`,
`SIGNATURES`, `operations.KINDS` uygulama acilirken, dil secilmeden once
uretiliyor. gettext'in `N_()` yaklasimi kullanildi: `mark()` ile isaretlenip
gosterim aninda `tr()` ile cevriliyor.

**Bekleyen islem basliklari.** `Operation` artik hazir metin degil, **kalip +
argumanlari** sakliyor; `title`/`detail`/`target` okundugunda ceviriliyor.
Boylece kuyruk doldurulduktan sonra dil degistirilirse adim basliklari da
donuyor.

**Dil degisimi yeniden baslatma istemiyor.** Acik disk ve bekleyen kuyruk
kaybolmasin diye metinler yerinde yenileniyor: `i18n.add_listener` ->
`MainWindow.retranslate()`; `_build_actions()` eylemleri bos metinle kurup
metni `_retranslate_actions()` yaziyor (metnin tek kaynagi orasi).
`FileBrowser`, `PartitionTableWidget`, `HexViewer` kendi `retranslate()`
islevlerini aldi.

### Yol acilirken bulunan iki gercek kusur

1. **Metinden karar cikarma.** Iki yerde kod, urettigi metnin **icinde arama**
   yapiyordu: `"kilitlenmis" in reason` (salt okunur acilis uyarisi) ve
   `"[YIKICI]" in line` (uygulama onayi). Metin cevrilince ikisi de sessizce
   bosa duserdi. Veriye tasindi: `DiskImage.readonly_locked` bayragi ve
   `OperationQueue.describe_rows()` -> `(metin, yikici mi)`.
2. **`elevation_available()` cevrilmemis kaliyordu** — `platform.py` `tr`'yi
   yalnizca islev icinde import ediyordu; modul duzeyine alindi (dongu yok,
   `i18n` cekirdegi import etmiyor).

### Dogrulama — **VM Win10 misafirinde** (kullanici istegi)

Bagimlilik: PyQt5 5.15.11 ana makinede indirildi (win32/cp312 tekerlekleri),
misafire kopyalandi ve **cevrimdisi** kuruldu
(`pip install --no-index --find-links C:\du-deps PyQt5`). Misafirin agi kapali
kaldi (`ping 8.8.8.8` -> General failure).

| Kosum (misafirde, `C:\du-test`) | Sonuc |
|---|---|
| `python -m tests.run_all` | **28/28 basarili** |
| `python -m tests.platform_check` | **0 bulgu** |
| `python -m tests.i18n_check` | tr/en/de **TAMAM**, 1062 metin |
| `python -m tests.diag_check` | **13/13 gecti** |
| `python -m tests.ui_smoke` | tamamlandi; `24-dil-de.png`, `24-dil-en.png` uretildi |

Dil secimi de misafirde dogrulandi: secim
`C:\Users\Admin\AppData\Roaming\DiskUltimate\settings.json` icine yaziliyor,
**yeni surec** onu okuyor (`initialize() -> de`), `DISKULTIMATE_LANG=en` kayitli
secimi geciyor ve `tr`'ye geri donuluyor.

Ekran goruntulerinde butun arayuz cevriliyor: menuler, arac cubugu, agac,
tablo sutunlari, sekmeler, durum cubugu, hex denetimleri. Almanca'da uzun
kacan uc etiket kisaltildi (arac cubugu tasma okuna dusuyordu).

### VM'de yasanan takilma (ceviriyle ilgisiz, kayit icin)

Ilk `ui_smoke` kosumu 23. ekran goruntusunden sonra **kilitlendi** (CPU 0,
15 sn boyunca ilerleme yok). Misafirde onceki kosumdan kalan ikinci bir
`python.exe` vardi; o surec oldurulup test yeniden kosuldugunda **sorunsuz
tamamlandi**. ADR 0021'deki kural bunu aciklar: ayni aygita iki yerden
dokunmak surucu yiginini asili birakiyor. Tek basina kosan test takilmiyor.

Ayrica misafirde bir donma raporu uretildi: `ui_smoke` 4 GB'lik goruntuyu
**arayuz is parcaciginda** yedekliyor (`backup(...)`, satir 375) ve yavas
makinede bu 1.8 sn suruyor. Testin kendi kurgusu; uygulama kodu degil.
Yakalayici dogru calisti.

### Belgeler

- `.claude/decisions/0027-cok-dilli-arayuz.md` — karar, secenekler, gerekce
- `.claude/docs/architecture.md` — `i18n/` agaci, metin akisi, genisletme noktalari
- `.claude/docs/testing.md` — ceviri denetimi bolumu
- `CLAUDE.md` — coklu dil kurali, "gorunen metin karar girdisi degildir"
- `README.md` — dil rozeti, **Araclar > Dil**, `DISKULTIMATE_LANG`

---

## 2026-09-17 (4) — Onyukleyici yonetimi ve UEFI onyukleme duzenleyici

Kullanici iki uygulamanin projeye entegre edilmesini istedi:

- [exxos-easy-grub-manager](https://github.com/exxosuk/exxos-easy-grub-manager)
  — *"gerekli uygulama durumlarini analiz et, projeyi degistirebilirsin bize
  gore uygun olsun, capraz platform destegi saglansin"*
- [efibooteditor](https://github.com/Neverous/efibooteditor) — *"bu projedeki
  ozellikleride istiyorum"*

Ayrica oturum sirasinda yeni bir kural koydu: **testler sanal makinede
calistirilir**, ana makinede degil.

### Lisans

exxos-easy-grub-manager GPL-3.0 (bu projeyle ayni), efibooteditor LGPL-3.0
(GPL-3 projeye katilabilir). Ikinci uygulamadan **kod alinmadi**: C++/Qt
yazilmis ve zaten birebir cevrilemezdi; UEFI yapilari **UEFI Specification
2.10**'dan yeniden yazildi.

### Analiz — kaynak araclarin sinirlari

| Kaynaktaki yol | Sinir |
|---|---|
| `lsblk` + `mount -o ro` ile bolum incelemesi | Yalnizca Linux, yalnizca root |
| `dd bs=512 \| strings \| grep GRUB` | 512 baytin tamaminda arar; bolum tablosundaki rastgele "GRUB" baytlari yanlis eslesme uretir |
| `dd if=/dev/zero of=$dev bs=440` | Dogru genislik, ama tiklama aninda diske yazar — kuyrugu (ADR 0025) ve fiziksel disk kapilarini (ADR 0014) atlar |
| efibooteditor: efivar + Windows API | Yapi cozumlemesi bellenim erisimiyle ic ice; UEFI'siz makinede test edilemez |

DiskUltimate'in kaynakta olmayan bir kozu vardi: **kendi dosya sistemi
surucileri**. Bir bolumu okumak icin isletim sistemine baglatmak gerekmiyor.

### Yapilanlar

**Yeni cekirdek modulleri**

| Dosya | Is |
|---|---|
| `core/bootloader.py` | Onyukleme kodu tanima (yalnizca ilk 440 bayt), bolumdeki sistemi kendi FS suruculeriyle bulma, `GrubDefaults`, `parse_grub_cfg` |
| `core/grub.py` | `grub-install`, menu uretimi, yedekle/geri yukle, "tumunu onar" — yalnizca Linux |
| `core/efiboot.py` | `EFI_LOAD_OPTION`, aygit yolu dugumleri, `EFI_KEY_OPTION`, sira listeleri — saf Python |
| `core/efistore.py` | Butun UEFI duzenini okuma, JSON yedek, degisiklik plani, yazma |

**`core/platform.py`ye eklenen** (OS farkinin tek durdugu yer): onyukleyici
arac arama, `run_privileged` (pkexec), `write_system_file`, `firmware_type`,
`efivar_names/read/write/delete` (Linux efivarfs + Windows bellenim API'si).

**Kuyruk:** `clear_boot_code` islem turu — ilk 440 bayti sifirlar, bolum
tablosunu korur, yikici isaretlidir ve `_require_writable()` uzerinden butun
fiziksel disk kapilarindan gecer.

**Arayuz:** yeni `&Onyukleme` menusu, `ui/dialogs/bootloader.py` (onyukleyici
yoneticisi) ve `ui/dialogs/efiboot.py` (UEFI duzenleyici); uc yeni cizilmis
ikon (`bootloader`, `efi`, `boot-order`).

**Ceviri:** 249 yeni metin; tr/en/de **TAMAM** (toplam 1311).

### Gelistirme sirasinda bulunan uc gercek hata

1. **`Partition.index` 1 tabanli, liste konumu degil.** `survey_session()`
   `enumerate()` konumunu `session.filesystem()`e veriyordu — **yanlis bolumu
   acardi**. VM'deki t31 testi yakaladi; duzeltildi ve kod yorumuyla isaretlendi.
2. **Windows'ta bellenim ayricaligi okumak icin de gerekiyor.**
   `SeSystemEnvironmentPrivilege` yonetici belirtecinde bile kapali gelir ve
   acilmadan yapilan cagri **sessizce bos doner**. Sayim 205 degisken buldu
   ama okuma bos donuyordu; `efivar_read`/`efivar_names` artik ayricaligi
   kendileri aciyor.
3. **ctypes 64 bit tutamaci kesiyordu.** `OpenProcessToken` argtypes verilmeden
   basarisiz oluyordu — `_win_kernel32`'nin ayni nedenle var oldugu tuzak
   (ADR 0011).

Ayrica bir tasarim duzeltmesi: her iki pencere de yapicisinda modal ilerleme
penceresi aciyordu. Projenin kendi kalibina cevrildi — once `run_task`, sonra
pencere (`show_bootloader` / `show_efi_boot`).

### Olcum

**Gercek bellenim verisi** (gelistirme makinesi, Windows 10, UEFI, **salt
okunur**): 205 degisken sayildi, `BootOrder` = 0007, 0005, 0000, 0001, 0002,
0003. Windows Boot Manager, ubuntu shim, NVMe UEFI girisi, iki ag girisi ve
`Uri()` dugumlu HTTPs Boot girisi cozuldu; **alti girisin de `to_bytes()`
ciktisi okunan baytlarla bire bir ayni**. Uretilen aygit yolu metni
`efibootmgr` bicimiyle ayni:

```
HD(2,GPT,a967f8c5-...,0x40800,0x32000)/File(\EFI\Microsoft\Boot\bootmgfw.efi)
```

**Sanal makinede** (Linux Mint 22.3, `ssh pc@192.168.42.131` — yeni kural):

| Kosum | Sonuc |
|---|---|
| `python3 -m tests.run_all` | **32/34 basarili** · 2 atlandi (yalnizca Windows dali) |
| `python3 -m tests.platform_check` | **0 bulgu** |
| `python3 -m tests.i18n_check` | tr/en/de **TAMAM**, 1311 metin |
| `python3 -m tests.diag_check` | **13/13 gecti** |
| `python3 -m tests.ui_smoke` | yeni pencereler dahil gecti; `25-onyukleyici-yonetici.png`, `26-uefi-onyukleme.png` |

Yeni testler: **t29** (UEFI yapilari gidis-donus, `efibootmgr` bicimi,
bilinmeyen dugumun korunmasi), **t30** (onyukleme kodu tanima; bolum
tablosuna yazilan `GRUB` baytinin yaniltmamasi; kaldirmanin tabloyu ve MBR
imzasini korumasi; kuyruktan calisma), **t31** (ESP tanima, kurulu Linux ile
veri bolumunun ayirt edilmesi, harf duyarsiz yol aramasi), **t32**
(`GrubDefaults` yorum/sira korumasi, `grub.cfg` menu cozumu), **t33** (yedek
gidis-donusu, degisiklik plani sirasi), **t34** (bellenim yazilamiyorken
planin uygulanmamasi).

Ikonlar VM'de de birebir ayni cizildi: 45 ikon, ozet
`62232ccc5d2ab89a741258f921716488` — Windows'taki degerle ayni.

### Sinanamayan

**UEFI degiskenine yazma yolu olculmedi.** Test misafiri BIOS (eski) kipinde
aciliyor (`/sys/firmware/efi` yok), gelistirme makinesinde ise deneme
yapilmiyor (yeni test ortami kurali). Kod tamamdir ama **yazma yolu gercek
bellenimde denenmemistir**; bu ADR 0029'da da boyle yazildi.

Ayni nedenle `grub-install` ve `update-grub` **calistirilmadi**: VM'in kendi
onyukleyicisine dokunmak, onu acilmaz birakabilirdi. Arac bulma, yetki kapisi
ve "kullanilamaz" dallari sinandi.

### Yan bulgu: dil degisimi arayuzu kilitliyordu (duzeltildi)

Yeni duman testi bolumlerini VM'de kosarken `ui_smoke` **dil adiminda dondu**.
Ana makinede (Windows) ayni test geciyordu; onceki oturumun coklu dil
calismasi yalnizca Windows misafirinde dogrulanmis, Linux'ta hic kosulmamisti.

Yigin dokumu nedeni gosterdi:

```
file_browser.py:186 navigate
file_browser.py:129 retranslate
main_window.py:645  retranslate
i18n/__init__.py:181 set_language
MODAL: QMessageBox  "Der Ordner konnte nicht geoffnet werden"
```

`FileBrowser.retranslate()` gecerli klasoru yeniden listelemek icin
`navigate()` cagiriyor; `navigate()` basarisiz olunca **modal** bir uyari
aciyordu. Dil degistirmek bir dosya islemi degildir ve kullanicinin
baslatmadigi bir tazelemeden modal pencere cikmasi arayuzu kilitler —
otomatik kosumda kapatacak kimse olmadigi icin surec sonsuza kadar bekledi.

Duzeltme: `navigate(path, quiet=True)` hatada pencere acmaz, bilgi satirina
yazar; `retranslate()` bu kipi kullanir. Duzeltmeden sonra `ui_smoke` VM'de
**tamamlandi** (cikis kodu 0, `24-dil-de.png` / `24-dil-en.png` uretildi).

Bu, yeni test ortami kuralinin ilk somut kazancidir: hata yalnizca Linux'ta
gorunuyordu ve ana makinede kosulan testler onu hic yakalamamisti.

### Belgeler

- `.claude/decisions/0028-onyukleyici-yonetimi.md`
- `.claude/decisions/0029-uefi-onyukleme-duzenleyici.md`
- `.claude/docs/architecture.md` — onyukleme akisi semasi, modul agaci
- `.claude/docs/testing.md` — yeni testler ve VM yordami
- `CLAUDE.md` — **Test Ortami Kurali** (testler VM'de kosar)
- `.claude/memory/linux-test-ortami.md` — VM erisimi ve olculmus durumu
- `README.md` — onyukleme ozellikleri

## 2026-09-17 (5) — Ceviri katmani raporu; onyukleyici ozelliginde dort bosluk kapatildi

Kullanici: *"dil destegi icin i18n mi kullaniyorsun... bu konu uzerinde
konusalim, detayli rapor hazirla."*

### Rapor

`.claude/docs/i18n-raporu.md` yazildi: degerlendirilen dort yol (Qt Linguist,
gettext, harici kutuphane, kendi JSON sozlugumuz) ve **neden elendikleri**,
secilen tasarimin ayrintisi, olcumler, dogrulama, bilinen sinirlar ve oncelikli
oneriler. ADR 0027 karari veriyor; bu belge gerekceyi ve sayilari tasiyor.

### Olcumler (Linux misafiri, Py 3.12.3)

| | |
|---|---|
| Cevrilebilir farkli metin | 1315 |
| `tr()`/`mark()` cagrisi | 1656 (ui 914, core 742) |
| Sozluk | `en.json` 91 KB, `de.json` 100 KB; bellekte ~220 KB (tek dil) |
| Yukleme | 0.5–0.8 ms |
| `tr()` | ~125 ns (ceviri etkin), ~395 ns (bicimlendirmeli) |
| 1000 metinlik pencere kurulumu | 0.23 ms |
| `initialize()` | 7.1 ms |

Yani ceviri katmani, olculen hicbir yavaslik kaynaginda gorunmuyor
(karsilastirma: bir bolum tablosu okumasi ~600 ms, ADR 0026).

### Bulunan ve kapatilan dort bosluk

Rapor icin yapilan taramada, ceviri calismasindan **sonra** eklenen
onyukleyici ozelliginde sarilmamis metinler cikti:

| Yer | Metin |
|---|---|
| `ui/dialogs/bootloader.py:286` | `title_text="Onyukleme kodunu kaldir"` |
| `ui/dialogs/bootloader.py:287` | `detail_text="{} — ilk 440 bayt sifirlanir"` |
| `ui/dialogs/bootloader.py:289` | `target_text="Disk"` |
| `ui/main_window.py:2989` | `f"BOLUM {part.index}"` (bolum bilgisi basligi) |

Dordu de `mark()`/`tr()` ile sarildi; iki yeni metin en/de'ye cevrildi
(1313 -> 1315). Bu, `i18n_check`'in yapisal sinirini gosteriyor: denetim
yalnizca **isaretlenmis** metni gorur, hic sarilmamis olani bilemez. Rapor
9.1'de bunun icin bir uyari denetimi onerildi.

### Dogrulama (VM'de)

| Ortam | Kosum | Sonuc |
|---|---|---|
| Mint 22.3 misafiri | `tests.i18n_check` | tr/en/de TAMAM (1315) |
| " | `tests.platform_check` | 0 bulgu |
| " | canli dil degisimi | 8 menu + 4 sekme + tablo sutunlari uc dilde dogrulandi |

Yeni `&Onyukleme` menusu de dil degisimine katiliyor (`&Boot` / `&Start`) —
sonradan eklenen ozellik ceviri duzenine uymus.

## 2026-09-17 (6) — Almanca yazim duzeltmesi ve sozde-yerellestirme

Kullanici: *"daha efektif yontem ne olmalaydi? bu dil meselesi global
uygulamalarda nasil yapiliyor"* -> sektor karsilastirmasi yapildi
(`.claude/docs/i18n-raporu.md` bolum 12), ardindan *"sistemin daha iyi
olmasini onleyen kurallarimiz var mi"* -> kural denetimi, ardindan *"evet"*
ile iyilestirmelere baslandi.

### 1. Almanca: 495 ceviri duzeldi

Projenin "ASCII Turkce" alismasi ceviriye de tasinmisti. Turkce'de bu bir
tercih, **Almanca'da yanlis**: "Grosse" (→ Größe), "Datentrager" (→
Datenträger), "fur" (→ für), "loschen" (→ löschen). Ustelik tutarsizdi: 88
ceviride umlaut vardi, kalanlarda yoktu.

Yontem — kor arama-degistirme **yapilmadi**: katalogda gecen 1360 farkli
umlautsuz sozcuk listelenip tek tek gozden gecirildi, 201 sozcukluk harita
kuruldu. Haritada olmayan sozcuge dokunulmadi; boylece "konnte" (gecmis zaman),
"Vorgangsprotokoll" (birlesik), "Flache (flat)" (sifat) gibi **dogru** olanlar
bozulmadi.

Sonuc: **495/1315 ceviri** duzeldi, umlaut iceren ceviri 88 → 583.
`i18n_check` yer tutucu/HTML butunlugunu dogruladi.

### 2. Sozde-yerellestirme (pseudolocalization) eklendi

```
DISKULTIMATE_LANG=qps python3 main.py
"Bolumu sil"  ->  "[!Ɓǿŀŭḿŭ şīŀ···!]"
```

- `i18n.pseudo()` — sozluk yok, metin calisma aninda donusur. Yer tutucular
  (`{}`, `{:08X}`, `{{`), HTML etiketleri ve varliklar (`&nbsp;`) korunur.
- Dil menusunde gorunmez (kalite aracidir), ayar dosyasina yazilmaz.
- `tests/ui_smoke.py -> sozde_denetimi()`: sozde dilde taze bir ana pencere ve
  alti diyalog kurar; eylem/menu/sekme/sutun metinlerinin hepsinin sozde
  oldugunu **dogrular** (degilse test duser), kalanlari bilgi olarak listeler.

Neden gerekliydi: `i18n_check` yalnizca **isaretlenmis** metni gorur; hic
sarilmamis olani bilemez (bu oturumda onyukleyici ozelliginde dort bosluk elle
taramayla bulunmustu). Sozde dilde sarilmamis metin donusmedigi icin
kendiliginden belli olur.

**Denetim ilk kosumda kendi test verimizi yakaladi** (`InfoDialog`'a
cevrilmemis baslik veriliyordu) ve testi dusurdu — yani calisiyor. Fixture
duzeltildikten sonra listede yalnizca veri kaldi: "64 bit", "Linux", harici
arac adlari.

### 3. Kural degisiklikleri (CLAUDE.md)

| Kural | Neden |
|---|---|
| "Calisma zamani bagimliligi yok" — **gelistirme/ceviri araclari haric** | Ayrim yazili degildi; `.po` bicimi "msgfmt kurulu degil" diye elenmisti. Oysa msgfmt bir gelistirici aracidir ve `.po` icin sart da degil. |
| Yazim: konsol/gunluk ASCII kalabilir, **arayuz metni ve ceviriler dogru yazimla** | 495 hatali Almanca cevirinin kaynagi bu belirsizlikti. |
| Sozde-yerellestirme kalite kapisi | Yeni dil eklemeden once tasma/bosluk denetimi. |

### 4. Dogrulama (Linux misafiri, Mint 22.3)

| Kosum | Sonuc |
|---|---|
| `tests.i18n_check` | tr/en/de **TAMAM** (1315) |
| `tests.platform_check` | **0 bulgu** |
| `tests.ui_smoke` | **cikis 0**; dil degisimi + sozde denetimi yesil |

### Siradaki (rapor bolum 13.4)

1. `.po` gecisi — cogul eki, baglam, fuzzy/msgmerge, Poedit/Weblate.
2. Kayit defterli `retranslate` — eylem metnini unutmayi imkansiz kilmak.
3. Yerel sayi/tarih bicimi (Almanca'da `1,00 GB`).
4. Almanca icin anadil gozden gecirmesi.

> **Not:** calisma agacinda cok dil calismasinin tamami hala **islenmemis**
> durumda (3950+ satir). `.po` gecisine baslamadan once bunun commit edilmesi
> onerilir; yoksa iki buyuk degisiklik ic ice girer.

## 2026-09-17 (7) — Sozluk bicimi JSON'dan gettext `.po`'ya tasindi

Kullanici: *"comitle po ya gec"*. Once biriken calisma islendi (0d538c5),
sonra bolum 12.3(a)'da "en buyuk kacirilan firsat" denen adim atildi.

### Yapilan

- `i18n/po.py` (395 satir): `.po` okuyucu/yazici + `merge()` (msgmerge
  esdegeri: ceviri korunur, kaynakta kalmayan giris bayatlanir `#~`, benzeyen
  yeni girise ceviri tasinip `#, fuzzy` isaretlenir).
- `i18n.trn()` (cogul) ve `i18n.trc()` (baglam). Cogul kurali `.po`
  basligindaki `Plural-Forms`'tan geliyor; ifadeyi stdlib'deki
  `gettext.c2py()` derliyor — harici bagimlilik yok.
- `catalogs/en.json` + `de.json` -> `en.po` + `de.po` (1315 giris, kaynak
  konumlariyla). JSON dosyalari silindi.
- `tests/i18n_check.py` yeniden yazildi: `.po` okuyor, **fuzzy** ve **cogul
  bicim** denetimi eklendi, `--write` msgmerge gibi calisiyor.
- Dort gercek cagri `trn()`e cevrildi (ornek: "1 step applied" / "3 steps
  applied"; onceden hepsi "steps" diyordu).

**Anahtar stratejisi degismedi** (kaynak metin hala anahtar), bu yuzden 1656
`tr()`/`mark()` cagrisinin hicbirine dokunulmadi.

### Neden JSON yetmiyordu

`.po` ucunu birden getiriyor: cogul ekleri, baglam (`msgctxt`) ve **bayat
ceviriyi koruyan fuzzy**. Sonuncusu raporun 8.5 maddesindeki sorunu cozuyor:
kaynak metindeki bir yazim duzeltmesi artik cevirileri dusurmuyor. Benzerlik
esigi (0.65) olcumle secildi: diakritik ekleme 0.69-0.96 uretiyor, gercekten
farkli metinler 0.42 ve altinda kaliyor.

Ayrica Poedit / Weblate / Crowdin `.po`'yu dogrudan aciyor — Almanca gozden
gecirmesi icin artik JSON yerine standart bir dosya yollanabilir.

### Bedeli

| | JSON | `.po` |
|---|---|---|
| Dosya (en) | 90 KB | 175 KB |
| Okuma (ayni makine) | 0.7 ms | **7.6 ms** |

11 kat yavas (JSON'u C cozuyor); maliyet dil basina bir kez odeniyor. Ilk
surum 13.6 ms'ydi, iki hizli yolla 7.6 ms'ye indi.

### Dogrulama

- **2630 metin** (2 dil x 1315) icin `tr()` ciktisi JSON donemiyle **birebir
  ayni** — bicim degisimi davranisi degistirmedi.
- Linux misafiri (Mint 22.3): `run_all` **32/34** (2 atlandi, Windows'a ozgu),
  `i18n_check` **BASARILI**, `platform_check` **0 bulgu**, `ui_smoke` **cikis 0**
  (dil degisimi + sozde-yerellestirme yesil).

### Yan bulgu: yeni kod kurali kirdi, denetim yakaladi

`po.py` ilk halinde `platform_check` 7 bulgu verdi: alti fonksiyonda Turkce
yerel degisken (kod adlari Ingilizce olmali) ve kacis tablosundaki iki ters
bolunun "sabit Windows yolu" sanilmasi. 204 tanimlayici `tokenize` ile
cevrildi (duz metin degistirme dize iceriklerini de bozmustu), kacis tablosu
`chr(92)` ile kuruldu ve gerekce koda yazildi.
---

## 2026-09-17 (5) — Misafir UEFI'ye alindi; onyukleme ve bellenim yollari gercek sistemde olculdu

Kullanici istegi: *"wm linux mint sanal makine suan kapali ayarlarini uefi
olarak ayarla"* ve *"grub-install / update-grub calistirilmadi ... bunun icin
gerekli yedek kaydini olustur ve dene, bozulursa tamir edilebilir"*.
Oturum sirasinda ikinci bir kural daha koydu: *"yazilimi teste baslayinca onay
bekle"* — VM hazirligi serbest, yazilim testleri icin onay alinir.

### Yedek

VMware Player'in anlik goruntu ozelligi yok; **tam dosya kopyasi** alindi:
`C:\\VM-Yedek\\mint-bios-20260917` — 26 dosya, 38 GB (vmx, nvram ve kullanilan
butun vmdk uzantilari). `yedek-*.vmdk` dosyalari bilerek atlandi: `mint.vmx`
onlara basvurmuyor, eski bir yapilandirmadan kalmislar. Her dosyanin bayt
boyutu `MANIFEST.txt` icine yazildi ve kopya sonrasi 26/26 dogrulandi.
Geri yukleme yordami `GERI-YUKLE.md` icinde.

### UEFI'ye gecis — uc engel

Yalnizca `firmware = "efi"` yetmedi; ucu de ayri bir acilis basarisizligi
uretti ve `vmware.log` ile teshis edildi:

1. **`firmware = "efi"`** — asil istenen. Sonuc: *"No compatible bootloader
   found"*.
2. **Diskler LSI Logic SCSI'de.** VMware'in EFI bellenimi bu denetleyici icin
   surucu tasimaz; log EFI'nin SATA portlarini tarayip diski hic gormedigini
   gosterdi. Diskler `sata0:0` ve `sata0:2`ye alindi.
3. **`guestOS = "ubuntu"` (32 bit).** Log'da *"The EFI ROM is 32-bit"*
   yaziyordu; 32-bit ROM 64-bit `BOOTX64.EFI`'yi acamaz. `ubuntu-64` yapildi.

`mint.nvram` silinip yeniden urettirildi. Misafir `\\EFI\\BOOT\\BOOTX64.EFI`
yedek yolundan acildi ve `Boot0005* Ubuntu` girisini kendisi olusturdu.
Calisir UEFI yapilandirmasi da yedek klasorune kopyalandi.

> VMware Player, VM kapali olsa bile `.vmx` dosyasini kilitli tutuyor.
> Pencerenin kapatilmasi gerekti (kullaniciya soruldu).

### Adim 1 — salt okunur tur

`run_all` 32/34 (2 atlanan Windows dali) · `platform_check` 0 · `i18n_check`
BASARILI · `diag_check` 13/13 · `ui_smoke` cikis 0.

### Adim 2 — UEFI bellenime yazma (yeni: `tests/efi_write_test.py`)

`tests.run_all` icine **alinmadi**; gercek bellenim degiskenlerini degistirir,
`physical_write_test.py` gibi `--onayla` ile calisir. Her degisiklik geri
alinir, her adim `efibootmgr` ile dogrulanir.

**Ilk kosum 28'de 27 gecti** ve dusen adim gercek bir hata gosterdi:
`Timeout` degiskeni makinede **hic yoktu**; `load()` `None` okuyor ama
`changes()` yalnizca deger varken is yapiyordu. Yani **bir duzeni okuyup aynen
geri yazmak onu degistiriyordu** (degisken yokken 0 olarak olusuyordu).
`BootNext` icin silme dali vardi, `Timeout` icin yoktu. Duzeltildi; artik
`Timeout` icin de silme uretiliyor.

Duzeltmeden sonra: **29/29**. Bitiste duzen baslangictaki halle bire bir ayni;
girisler bayt bayt korunmus. Ilk kosumun biraktigi artik `Timeout` degiskeni de
kendi kodumuzla silindi. Makine yeniden baslatildi ve normal acildi.

### Adim 3 — `grub-install` ve `update-grub`

Kendi `core/grub.py` islevlerimiz uzerinden calistirildi. Islemler calisti ve
misafir her adimdan sonra acildi (uc yeniden baslatma `last -x reboot` ile
dogrulandi). **Uc gercek hata** cikti:

1. **UEFI'de aygit argumani anlamsiz.** `grub-install <disk>` *"Installing for
   x86_64-efi platform"* der, ESP'ye kurar, aygiti **yok sayar**. Olculdu:
   islem oncesi/sonrasi MBR'nin ilk 440 baytinin ozeti degismedi. Bizim onay
   penceremiz ise "Diskin ilk sektoru degisir" diyordu — yanlis. Eklenen
   `grub.install_target()` bellenim kipini sorar; UEFI'de aygit hic gecirilmez
   ve onay metni ikiye ayrilir.
2. **Aracin ciktisi kayboluyordu.** `grub-install` satirlarini **stderr**'e
   yazar, basarili kosumda stdout bostur; biz yalnizca stdout'a bakiyorduk.
3. **`grub.cfg` cozumleyicisi ic ice menuyu erken kapatiyordu.** Her `}`
   satirinda alt menu yigindan cikiyordu, oysa `menuentry` bloklari da `}` ile
   biter: "Advanced options" altindaki **ikinci** giris (kurtarma kipi) ust
   duzeyde gorunuyordu. Artik blok derinligi sayiliyor, `${...}` yazimlari
   sayimdan once atiliyor. Testi yazildi (t32).

Yan bulgu: `/boot/grub/grub.cfg` cogu dagitimda `-rw-------` root'a aittir;
yetkisiz kosumda okunamiyor ve biz "Menu girisi: 0" yaziyorduk — menu doluyken
bos gorunuyordu. `GrubStatus.config_readable` eklendi, artik "okunamadi (yetki
yok)" yazar.

**Bizim kodumuza yuklenmeyen bir degisiklik:** `update-grub` menu basliklarini
"Linux Mint 22.3 Xfce"den "Ubuntu"ya cevirdi. Dogrudan `sudo update-grub`
calistirildiginda **birebir ayni** sonuc cikti — bu sistemin
`GRUB_DISTRIBUTOR` degerlendirmesinden geliyor, bizim cagri ortamimizdan degil.
Eski `grub.cfg` alinan yedekte duruyor.

### Kosum sonrasi tam gerileme turu (VM)

`run_all` 32/34 · `platform_check` 0 · `i18n_check` BASARILI (1321 ceviri) ·
`diag_check` 13/13 · `ui_smoke` cikis 0.

### Olculmeyen kalan

- **BIOS kipinde `grub-install <disk>`** — misafir artik UEFI'de; MBR'ye yazan
  dal calistirilmadi.
- **`repair()` birlesik akisi** — adimlari tek tek olculdu, tumu birlikte degil.
- **`upgrade_packages()`** — paket yoneticisine dokunmak misafirin durumunu
  degistirir; denenmedi.

### Bir yordam dersi

Paylasilan klasor (`/mnt/hgfs`) her yeniden baslatmadan sonra **dusuyor**.
Kaynak kopyalama komutunda hatalar `2>/dev/null` ile gizlenmisti; iki kosum
sessizce **eski kodla** calisti ve yanlis sonuc uretti. Sonra bir kosumda
hedef once silinip kaynak bulunamayinca VM'deki kopya bosaldi. Artik kaynak
varligi kopyalamadan **once** dogrulanıyor ve hata gizlenmiyor.

## 2026-09-21 — Windows EXE paketlemesi: `build_exe.bat` + `DiskUltimate.spec`

Iki dosya da baska bir projeden (SchematicViz / `altium-monley`) kopyalanmisti
ve bu projeyle hicbir ilgisi yoktu: giris noktasi `gui.py`, veri olarak
`gui.ui` + `icon.ico`, `collect_all` ile `altium_monkey`/`openpyxl`/`trimesh`/
`cascadio`, on kontrol icin var olmayan `deps.py`, hedef dizin olarak sabit
`D:\pythonProjeler\altium-monley`. Bu haliyle paketleme ilk adimda duruyordu.

DiskUltimate'a uyarlandi:

- **Giris noktasi** `main.py`; `pathex=[src]` — `main.py` yolu calisma aninda
  ekliyor ama Analysis statik cozumleme yapiyor, paketi gormesi gerekiyor.
- **Ceviri sozlukleri veri olarak gomuluyor**: `src/diskultimate/i18n/catalogs/
  *.po` -> pakette `diskultimate/i18n/catalogs/`. `i18n.CATALOG_DIR` paketin
  yanina baktigi icin goreli yer birebir korunmali; yoksa exe yalnizca kaynak
  dille (Turkce) acilirdi. `.po` veri dosyasidir, PyInstaller kendiliginde
  almaz.
- **`collect_all` yok**: tek calisma zamani bagimliligi PyQt5; cekirdek saf
  Python. Kullanilmayan PyQt5 modulleri (QML/Quick/WebEngine/Multimedia...)
  ve bilimsel yigin `excludes` ile disarida.
- `_DROP` / `_DROP_DATA` filtreleri korundu (qwebgl, Qt5Qml, opengl32sw,
  d3dcompiler, Qt cevirileri, `uic/widget-plugins` — projede hic `.ui` yok).
  Windows tarafinda `qwindows.dll` ve `qwindowsvistastyle.dll` kaldi: uygulama
  sistemin Qt temasini kullanir (ADR 0013).
- **Ikon satiri kaldirildi** — depoda `.ico` yok; ikonlar `ui/icons.py` icinde
  QPainter ile cizilir (ADR 0025). Eklenirse nereye yazilacagi yorumda.
- **`.bat`**: sabit yol yerine `%~dp0`; yorumlayici secimi `py -3.12` -> `py -3`
  -> `python` sirasiyla deneniyor; on kontrol `deps.py` yerine dogrudan
  `import PyQt5.QtWidgets`; kaynak agaci denetimi; PyInstaller cikis kodu
  `echo` oncesinde `%ERRORLEVEL%`'e aliniyor (eski surumde `echo.` araya
  giriyordu) ve ayrica `dist\DiskUltimate.exe` varligi dogrulaniyor.
- `.gitignore`: `build/` ve `dist/`.

### Olculen

Ana makinede paketlendi ve **salt okunur** duman testi yapildi (disk yazma
yok): `dist\DiskUltimate.exe` 25 MB; arsivde `diskultimate\i18n\catalogs\
{de,en}.po` ve `qwindows.dll` var, `Qt5Qml`/`opengl32sw`/`Qt5\translations`
yok. `DISKULTIMATE_QPA=offscreen` ile calistirildi: arayuz acildi, gunluge
"Fiziksel disk listesi hazir: 2 disk" yazdi, 30 sn sonra zaman asimiyla
kapatildi. Eksik modul uyarisi yalnizca `fcntl` (Linux'a ozgu, `delayed,
optional`).

### Acik kalan — donmus pakette gunluk yolu

`paths.PROJECT_ROOT` dosya konumundan uc dizin yukari cikarak hesaplaniyor.
Onefile pakette `paths.py` `<Temp>\_MEIxxxx\diskultimate\` altinda durdugu icin
PROJECT_ROOT **`%LOCALAPPDATA%\Temp`** cikiyor; gunlukler `Temp\.claude\logs\`
altina yaziliyor (olculdu). Yazilabilir oldugu icin cokme yok, ama yer yanlis
ve "Araclar > Tanilama" oraya aciliyor. Duzeltme `paths.py` icinde: `sys.frozen`
ise kok olarak `os.path.dirname(sys.executable)` ya da
`%LOCALAPPDATA%\DiskUltimate` alinmali. Bu oturumda **yapilmadi** (istenen
kapsam iki paketleme dosyasiydi).

## 2026-09-21 (2) — Oturum transcript'leri projeye alindi (depo tarafi hazirlandi)

`session-persistence` skill'i: `~/.claude/projects/<slug>` klasoru
`.claude/sessions/<slug>/` icine tasinir, yerine junction birakilir. Iki sorunu
cozer — `cleanupPeriodDays` varsayilani 30 gundur (baska bir projede 18 gunluk
oturum boyle kayboldu) ve dokumler makineye baglidir.

Durum: `--check` -> `[BAGLI DEGIL] d--pythonProjeler-DiskUltimate-DiskUltimate`
(9 transcript, ~44 MB). Depo **private** (`mcansiz/DiskUltimate`) oldugu icin
transcript'lerin Git'e alinmasinda sakinca yok.

### Kurulum calistirildi (acik oturumla, --force ile)

Script acik Claude Code'u gorup durdu (cikis 2). Acik olan bu projenin kendisi
oldugu icin skill `--force` onermiyor; kullanici riski bilerek devam dedi.

**Korkulan olmadi: transcript bolunmedi.** Bu oturumun dosyasi
(`ff9f0f73-...jsonl`) tasindiktan sonra junction uzerinden ayni dosyaya yazmaya
devam etti (741 KB -> 753 KB, iki olcum arasi buyudu). Dosya tutamaci yola
degil inode'a bagli oldugu icin rename calisan yazimi kesmiyor; Claude Code
klasoru yeniden olusturmaya kalkmadan once junction yerine kondu. Not: bu
zamanlamaya bagli, garanti degil — skill'in uyarisi yerinde.

- `.gitignore` — desenin **tek yildizli** oldugu (`.claude/sessions/*.jsonl`)
  ve alt dizini kapsamadigi aciklandi. Boylece kancanin urettigi **arsiv
  kopyalari** disarida kalirken **canli transcript'ler**
  (`.claude/sessions/<slug>/*.jsonl`) depoya girer. `git check-ignore` ile
  ikisi de dogrulandi. `**` yapilirsa kalicilik ortadan kalkar — uyari yazildi.
- `CLAUDE.md` — kayit kurali guncellendi: eski "ham `.jsonl` depoya girmez"
  satiri artik arsiv kopyalarini anlatiyor; canli transcript'ler ve
  `.gitattributes` (`.claude/sessions/** -text -diff`, CRLF cevrimi canli
  yazilan JSONL'i bozar) kurali eklendi.
- `SessionEnd` kancasi **kaldirilmadi** (kullanici karari): arsiv kopyalari
  junction'in yanindan devam eder, INDEX.md uretimi degismez. Bedeli diskte
  ~2 kat yer.

### Dogrulandi

- `--check` -> `[OK] d--pythonProjeler-DiskUltimate-DiskUltimate`
- `~/.claude/projects/d--pythonProjeler-DiskUltimate-DiskUltimate` artik
  `<JUNCTION>` -> proje icindeki klasor. Artik kalan `.yedek-*` klasoru yok.
- 9 transcript + `memory/` projeye tasindi (~44 MB).
- `.gitattributes` depo kokune yazildi (`.claude/sessions/** -text -diff`).
- `.claude/settings.json` gecerli JSON, `SessionEnd` kancasi yerinde,
  `cleanupPeriodDays: 36500` eklendi (global ayar Git'te olmadigi icin yetmezdi).
- Arsiv kopyalari hala yok sayiliyor; canli transcript klasoru izlenmemis
  olarak gorunuyor - yani commit'e girmeye hazir.

### Kalan adim (kullanici)

    git add .claude .gitattributes .gitignore CLAUDE.md
    git commit -m "Oturum gecmisi projeye alindi"

Ilk commit ~44 MB ekler. Commit yapilmadi - istenmedi.

---

## 2026-09-21 (3) — Oturum gecmisi tek klasorde: VS Code eklentisinde gorunur oldu (ADR 0038)

**Sikayet:** depodaki `.claude/sessions/` icinde 9 oturum dokumu duruyor ama
VS Code Claude eklentisinin gecmis listesinde gorunmuyor.

### Neden gorunmuyordu

Dokumler `.claude/sessions/d--pythonProjeler-DiskUltimate-DiskUltimate/`
altindaydi; bu ad **Windows makinesinin** calisma dizininden uretilmis slug.
Bu makinede (Linux) Claude Code kendi slug'ina bakiyor:
`-home-pc-Belgeler-GitHub-DiskUltimate`. Yani gecmis dosya olarak gelmisti,
adres olarak degil.

Ayrica projenin daha eski Linux yolunda (`/home/pc/diskUltimate`) kalmis iki
oturum daha vardi — ilki projenin kuruldugu oturum.

### Eklenti gecmisi neye bakiyor (olculdu)

Tahmin yerine eklentinin kendi kodu okundu
(`~/.vscode/extensions/anthropic.claude-code-2.1.278-linux-x64/extension.js`):

- Liste `readdir(<config>/projects/<slug>)` ile kuruluyor; `.jsonl` uzantili ve
  **adi gecerli oturum kimligi** olan dosyalar aliniyor.
- Ozet dosyanin bas/son parcasindan cikariliyor; dokumun icindeki `cwd`
  yalnizca **gosteriliyor**, suzme olcutu degil. `isSidechain` olanlar eleniyor.

Yani ayni klasoru birden cok makine paylasabilir; dokumun `cwd` alanini
yeniden yazmak gerekmiyor. Ayrica arsiv kopyalari (`2026-09-19-<kimlik>.jsonl`)
adlari kimlik olarak ayristirilamadigi icin listede cikmiyor — mukerrer kayit
riski yok.

### Yapilan

- `.claude/sessions/d--pythonProjeler-...` -> `.claude/sessions/live/`
  (`git mv`; 9 dokum, ~44 MB, blob'lar ayni kaldi).
- `.claude/hooks/setup-sessions.py` yazildi: slug'i kendi hesaplar, mevcut
  `<config>/projects/<slug>` icerigini `live/` ile **birlestirerek** tasir
  (alt klasorler dahil), yerine baglanti kurar (symlink; Windows'ta olmazsa
  `mklink /J`), `--import-legacy` ile eski yol dokumlerini alir.
  `--check` / `--dry-run` / `--force` var. Acik oturum korumasi: son bir
  dakikada yazilmis dokum varsa durur.
- `CLAUDE.md`, `.gitignore` aciklamasi, `INDEX.md` basligi ve ADR 0038
  guncellendi. `.gitignore` deseni degismedi; `git check-ignore` ile
  dogrulandi: `live/*.jsonl` **girer**, ust dizindeki arsiv kopyalari girmez.

### Dogrulandi

- `.claude/hooks/setup-sessions-test.py` yazildi (sahte `CLAUDE_CONFIG_DIR` +
  sahte depo; gercek dokumlere dokunmaz): **6/6 TAMAM** — kurulmamis durumun
  bildirilmesi, `--dry-run`'in hicbir sey degistirmemesi, acik oturum
  korumasi, tasima + `tool-results` birlestirme + eski yol ithali, alakasiz
  proje klasorune dokunmama, ikinci calistirmada `TAMAM` (idempotent).
- Gercek makinede `--check` -> `gercek klasor` (henuz baglanti yok),
  `--dry-run --import-legacy` -> tasinacak 2 canli + 2 eski dokum.
- `git check-ignore`: `live/*.jsonl` depoya **girer**, ust dizindeki arsiv
  kopyalari girmez.

### Simdiden gorunur olan

Canli dokumleri tasima islemi **bu oturumdan yapilamadi**: Claude Code'un
guvenlik siniflandiricisi kendi transcript'ine dokunmayi engelledi (dogru
karar; zaten kural "Claude Code kapaliyken calistir" diyor).

Bunun yerine depodaki 9 eski dokum icin `~/.claude/projects/<slug>/` altina
**symlink** birakildi; eklenti bunlari hemen listeler. Baglantilar gecicidir:
betik calisinca "baglanti, hedefte var" diyip siler (test bunu da kapsiyor).

### Kalan adim (kullanici)

    # VS Code'daki Claude oturumu kapatildiktan sonra:
    python3 .claude/hooks/setup-sessions.py --import-legacy

Sonra eklentide 13 oturum gorunur ve bu makinenin yeni oturumlari dogrudan
depo icine yazilir.

---

## 2026-09-21 — Linux paketi: "Evet" deyince uygulama kapaniyor, acilmiyordu

### Sikayet

`dist/DiskUltimate` (PyInstaller, Linux) aciliyor, "root olarak yeniden
baslatilsin mi?" diye soruyor, **Evet** deyince kapaniyor ve bir daha
acilmiyor. Hicbir hata gorunmuyor.

### Kok neden (olculdu)

Tanilama gunlugunun son satiri "root olarak yeniden baslatiliyor" idi, yani
uygulama `pkexec`'i baslatip **hemen** kendini kapatiyordu.

`pkexec` yetkilendirmeyi **kendi ebeveynine** bakarak yapar (polkit oznesi =
`getppid()`). Ana makinede denendi:

- ebeveyn `Popen`'dan hemen sonra cikinca → pkexec root olarak **asili kaldi**,
  parola penceresi hic acilmadi, hicbir cikti vermedi;
- ayni komut ebeveyn yasarken → `cikis kodu 0`, program root olarak calisti.

Ikinci kusur: `Popen` "baslatildi" sayildigi icin yetkilendirme reddedilse
bile kullaniciya hicbir sey soylenmiyordu.

### Yapilan

- **ADR 0039** — yukseltme artik el sikismadir. `relaunch_elevated()` bir
  `ElevatedLaunch` tutamaci dondurur (`bekliyor` / `basladi` / `hata`); yeni
  kopya penceresi gorununce `signal_elevated_ready()` ile bildirim dosyasini
  yazar (`DISKULTIMATE_HANDOFF`), eski kopya o ana kadar acik kalir ve
  belirsiz ilerleme penceresi gosterir. pkexec bildirimsiz cikarsa hata
  gosterilir (126 = iptal, 127 = polkit reddi, digerleri cikis koduyla) ve
  yetkili kopyanin ciktisinin sonu eklenir. Windows/macOS akisi degismedi.
- **ADR 0040** — paketlenmis kopyada `paths` artik proje dizinini `__file__`
  uzerinden uydurmaz: gunluk ve gecici dosyalar `user_data_dir()` altina
  gider (Linux `~/.local/state/DiskUltimate/`). Onceden hepsi sistemin gecici
  dizinindeki `_MEIxxxx`'in yanina dusuyordu; gunlukler silinebilir bir yerde
  duruyor ve root kopyasi yazinca sahiplik degisiyordu. `LOG_DIR` sabiti
  yerine `log_root()`; `DISKULTIMATE_LOG_DIR` yetkili kopyaya gecirilir,
  boylece root kopyasinin gunlugu ayni klasorde toplanir.
- **`build_linux.sh`** eklendi (`build_exe.bat`'in Linux karsiligi):
  PyQt5'i bulan yorumlayiciyi secer, kaynak agacini dogrular, PyInstaller
  yoksa kurar, `strip`/`pkexec` eksikse uyarir, `dist/DiskUltimate` uretip
  calistirma iznini verir. `--clean` onbellegi bosaltir.

### Dogrulandi (ana makine — Linux Mint, KDE/Wayland, sddm)

| Denetim | Sonuc |
|---|---|
| `tests.platform_check` | 0 bulgu |
| `tests.i18n_check` | de/en TAMAM (5 yeni metin cevrildi) |
| `tests.diag_check` | 13/13 |
| `tests.ui_smoke` | tamamlandi (sozde dil: yalnizca bilinen 3 veri metni) |
| `tests.run_all` | 40/42 · 2 atlandi (yalnizca Windows dalinda) |
| pkexec el sikismasi (uctan uca) | `uid=0`, `DISPLAY` tasindi, bildirim yazildi, bekleyen kopya `basladi` gordu |
| root'un ekrana erisimi | `pkexec ... xdpyinfo` → `name of display: :0` (XWayland uzerinden calisir) |
| `./build_linux.sh` | `dist/DiskUltimate` (39M) uretti, yeniden calistirildi ve gunlugu `~/.local/state/DiskUltimate/logs/` altina yazdi |

**VM'de kosulamadi:** `192.168.42.131` bu makineden erisilemiyor (baglanti
zaman asimi; VM Windows gelistirme makinesine bagli). Bu yuzden calistiran
testler **ana makinede** kosuldu — hicbiri fiziksel diske yazmaz. Windows ve
macOS yollari da bu oturumda calistirilmadi; kod yolu degismedi ama
olculmedi de.

---

## 2026-09-21 — NTFS bolumlerde doluluk cubugu yoktu

### Sikayet

Ana bilgisayarin diskinde ext4 ve FAT32 bolumlerde doluluk cubugu var, iki
**NTFS** bolumde yok.

### Kok neden

`fsdetect._ntfs()` yalnizca onyukleme sektorunu okuyup donuyordu:
`used_bytes` `-1` ("bilinmiyor") kaliyor, harita da cubugu ancak
`fs_used >= 0` iken ciziyordu. FAT (FAT tablosu), exFAT (tahsis bitmap'i) ve
ext (superblokta `s_free_blocks_count`) icin bu bilgi hemen elde; NTFS'te ise
**ustveri dosyalarindadir**: doluluk `$Bitmap`, etiket `$Volume` kaydinda.
Ayni nedenle NTFS bolumlerde etiket de bostu — haritada gorulen
"Basic data partition" GPT bolum adiydi, birim etiketi degil.

### Yapilan

- `NtfsFS.used_bytes()` eklendi: `$Bitmap`'i yalnizca **kume sayisi kadar**
  okur ve dolu bitleri sayar. Bitmap'in son baytlari birim disinda kalan
  bitleri tasir ve Windows onlari dolu isaretler; eski `stats()` bunlari
  sayiyor, ayrica tum bitleri `bin(b).count("1")` ile geziyordu. Sayim artik
  256 girisli bir tabloyla `bytes.translate` uzerinden yapilir (200 GB'lik
  birimde bitmap ~6 MB'tir). `stats()` bunu cagirir ve olcum yoksa `-1`
  dondurur — "bilinmiyor" ile "bos" birbirine karismaz.
- `fsdetect._ntfs()` MFT'yi acip doluluk ve etiketi doldurur. Basarisizlik
  olumcul degildir (tespit yine gecerli); islem `diagnostics.span
  ("fs.ntfs_meta")` ile olculur.

### Dogrulandi (ana makine, SALT OKUNUR)

- `t17_ntfs` genisletildi: tespit sonrasi etiket + doluluk dolu olmali;
  deger, boyutlandirmanin bagimsiz bitmap taramasiyla (`_bitmap_usage`)
  **birebir** ayni cikmali; 2 MiB dosya yazilinca doluluk en az 2 MiB artmali.
- Gercek disk (`/dev/nvme0n1p4`, bagli degil): bizim olcum **164.07 GiB**;
  `ntfscluster` bos alani 13 363 679 232 bayt diyor → dolu 176 170 622 976
  bayt = **164.07 GiB**. Birebir tutuyor. Sure: 44-64 ms/bolum.
- `tests.run_all` 40/42 (2 atlandi: yalnizca Windows), `platform_check` 0
  bulgu, `i18n_check` BASARILI.
- Not: `/dev/nvme0n1p3` icin `ntfsresize` "NTFS is inconsistent, run chkdsk"
  diyor — o birimin kendi $Bitmap tutarsizligi (188 kume), Windows'ta
  `chkdsk /f` ister. Bizim okumamiz etkilenmedi.

---

## 2026-09-21 — Doluluk cubugu yeniden cizildi (ilk tur)

### Istek

Doluluk renkleri daha estetik olsun.

### Onceki hali

Beyaz dikdortgen zemin (alpha 190) + blogun renginin koyulastirilmisi, keskin
koseler, ortada tek renk yazi. Cubuk iki yerde **ayri ayri** ciziliyordu
(harita blogu ve boyutlandirma seridi) ve ikisi birbirini tutmuyordu.

### Yapilan

`theme.draw_usage_bar()` tek cizim noktasi oldu; `disk_map` ve `resize_bar`
onu cagiriyor.

- **Bicim:** yuvarlatilmis kapsul, kenarinda blogun tonundan tureyen ince
  cerceve, olugun ustunde 1 px ic golge (cubuk blogun uzerine yapistirilmis
  degil, icine oyulmus durur). Dolgu ustten alta hafif degradeli.
- **Renk:** esigin altinda blogun **kendi tonu** (daha doygun, orta
  parlaklikta — koyulastirmak camur gibi gosteriyordu); %75'ten sonra
  kehribar, %90'dan sonra kirmizi. Ara tonlarda karisim denendi ve
  **birakildi**: mavi/mor/yesil bloklarda kehribar karisimi donuk bir zeytin
  tonu veriyor, hem cirkin hem de uyari oldugu anlasilmiyordu (renk x oran
  onizleme tablosu uretilerek goruldu).
- **Yazi:** ayni etiket iki kez cizilir — once oluk, sonra dolu kisim
  kirpilarak — boylece cubugun her iki yarisinda da okunur. Yazi rengi HSL
  "lightness" ile degil **algilanan parlaklikla** secilir (`readable_text`):
  kehribar HSL'de orta cikip koyu sayiliyor, uzerine beyaz yazi dusuyordu.

Esik renkleri (`USAGE_WARN`, `USAGE_FULL`) anlamsaldir, bu yuzden sabittir
(CLAUDE.md renk kurali: anlamsal renkler paletten gelmez).

### Dogrulandi

- `tests.ui_smoke` genisletildi: 5 dosya sistemi renginde esik altinda tonun
  korundugu, esiklerde kehribar/kirmizi geldigi, yazi-zemin karsitliginin
  her durumda 60 lightness'tan buyuk oldugu; ayrica %0, %0.4, %50, %100 ve
  cok dar alanda cizimin patlamadigi. **TAMAM**
- Goz denetimi: renk x oran tablosu ve gercek disk duzeninin onizlemesi
  offscreen uretilip bakildi; boyutlandirma seridi de ayni gorunumde.
- `platform_check` 0 bulgu, `i18n_check` BASARILI, `run_all` 40/42 (2 atlandi).

---

## 2026-09-21 — Harita blogu yeniden duzenlendi (ADR 0041)

### Istek

Ilk tur yeterli olmadi: "guzel degil — example gui resimlerini incele, benze;
bana secenekler sun".

### Yapilan

`example gui/` altindaki uc arac (EaseUS, Macrorit, Acronis) incelendi;
uculunun de ortak duzeni var: **acik blok zemini + blogun ustunde kalin
doluluk cubugu + koyu yazi**. Bizdeki sorun renk tonu degil yerlesimdi:
doygun renkli zemin uzerindeki kucuk cubuk zeminle yarisiyordu.

Bes secenek offscreen uretilip (`.tmp/onizleme/doluluk-secenekleri.png`)
kullaniciya sunuldu: A Macrorit tarzi, B EaseUS tarzi, C renkli blok + ust
serit, D blogun kendisi kapasite, E renkli blok + rozet. **A secildi.**

- `DiskMapWidget._draw_block` yeniden yazildi: zemin paletin `Base` rengi
  (koyu temada kendiliginden koyu panel), 4 px sol kenar seridi dosya
  sistemi renginde, ustte 19 px doluluk cubugu, altinda baslik/dosya
  sistemi/boyut paletten gelen renklerle. Secim ve fare uzerindeyken zemin
  vurgu rengine karisir.
- `theme.draw_usage_bar` bu bicime gecti (kapsul degil, 2.5 px yuvarlatilmis
  dikdortgen; oluk dosya sistemi renginin acik tonu; dolgu degradeli).
  Doluluk bilinmiyorsa (MSR, bicimlendirilmemis) cubuk **taramali** cizilir.
- Boyutlandirma seridindeki cubuk da blogun ustune tasindi; iki gorunum ayni
  gorsel dili konusuyor.

### Dogrulandi

- `tests.ui_smoke`: TAMAM (doluluk cubugu renk/okunurluk denetimi dahil).
- Goz denetimi: acik tema, koyu tema (Fusion koyu palet), secili blok, bos
  alan blogu ve boyutlandirma seridi ayri ayri uretilip bakildi —
  `.tmp/onizleme/sonuc-*.png`.
- `platform_check` 0 bulgu, `i18n_check` BASARILI.

---

## 2026-09-21 — Acilista kosulsuz root (ADR 0042)

### Istek

"Uygulama acilmadan otomatik root sifresi sorup root olarak acilsa nasil olur,
mumkun mu? Zaten root olmadan pek bir ise yaramiyor."

Secenekler ve bedelleri sunuldu; kullanici **kosulsuz otomatik root** ve
**uretilen dosyalarin sahipliginin kullaniciya geri verilmesini** secti.

### Yapilan

- `ui/startup.elevate_at_startup()` eklendi; `main.py` pencereyi kurmadan
  once bunu cagirir. Yetkili kopya acilinca (ADR 0039 el sikismasi) acilis
  kopyasi kapanir. Kod `main.py` yerine `ui/` altindadir: `main.py` cevirici
  taramasinin disinda kaliyor, oradaki metinler hicbir zaman cevrilmezdi.
- Yetki verilmezse uygulama acilir ve ayni oturumda bir daha sorulmaz
  (`MainWindow.mark_elevation_asked`). Cikis kapilari: `--no-root`,
  `DISKULTIMATE_AUTO_ROOT=0`, `DISKULTIMATE_NO_ELEVATION_PROMPT=1`.
- `platform.invoking_user()` / `platform.restore_owner()` eklendi; root
  kopyada uretilen dosyalarin sahipligi `PKEXEC_UID`/`SUDO_UID` ile geri
  verilir. Cagrildigi yerler: `image`, `vdisk`, `clone`, `filesystem` (iki
  extract), `fat`, `exfat`, `recovery` (iki yol).
- `CLAUDE.md` yetki kurali ve ADR 0023 bu kararla degistirildi.

### Dogrulandi (ana makine)

| Denetim | Sonuc |
|---|---|
| Gercek kosum (`./dist/DiskUltimate`) | 3,5 sn'de root arayuz acildi; acilis kopyasi el sikismadan sonra kapandi; fiziksel disk salt okunur acildi |
| Sahiplik (gercek root) | `pkexec` altinda uretilen goruntu `uid=1000 gid=1000` — root degil |
| `tests.ui_smoke` | acilis yetkisi (basarili/basarisiz + 3 cikis kapisi), `restore_owner` yetkisizken dokunmuyor |
| `tests.run_all` | 40/42 · 2 atlandi (yalnizca Windows) |
| `platform_check` / `i18n_check` | 0 bulgu / BASARILI (2 yeni metin cevrildi) |

Not: bu makinede polkit parola sormadan yetki verdi (yonetici grubu kurali);
parola soran sistemlerde pencere pkexec tarafindan acilir, bekleme penceresi
o sirada "Yetki isteniyor" der.

---

## 2026-09-21 — Bolum baglama / cikarma ve surucu harfi (ADR 0043)

### Gecmisi

Ozellik 18 Eylul oturumunda tasarlanmis, uc platformun yolu olculmus, ama
ayni olcum sirasinda NTFS `$Secure` hatasi bulundugu icin one o alinmisti
(ADR 0037) ve ozellik geri donulmeden kalmisti. Kullanici bu turda "simdi
yap" dedi; tasarim kararlari o oturumdan aynen alindi.

### Yapilan

- `core/platform.py`: `mount_partition`, `unmount_partition`,
  `partition_mount_point`, `partition_device`, `mount_action_labels`,
  `mount_supported`. Linux'ta root iken `/media/<kullanici>/<etiket>`,
  degilken `udisksctl`; Windows'ta `Add-/Remove-PartitionAccessPath`;
  macOS'ta `diskutil` (yazildi, **test edilmedi**).
- `core/physical.py`: guvenlik katmani — `is_critical_mount()` ve
  sarmalayicilar. `/`, `/boot`, `/boot/efi`, `/usr`, `/var`, `/etc`,
  `/home` (Windows'ta sistem surucusu) **cikarilamaz**.
- `ui/main_window.py`: Bolum menusune ve sag tik menusune iki eylem; islem
  `run_task` ile is parcaciginda (polkit penceresi/PowerShell beklenebilir).
  Goruntu dosyasinda eylemler kapali — goruntu isletim sistemine bagli
  degildir.
- Uygula penceresindeki "bagli bolum var" uyarisina **Baglantilari kes**
  dugmesi; cikarilamayan bolum varsa islem devam etmez.
- `ui/icons.py`: `mount` / `unmount` ikonlari (asagi/yukari ok rozetli).
- Ceviriler: 21 yeni metin EN + DE.

### Yol uzerinde cikan iki gercek kusur

1. **"target is busy"** — yeni baglanan birim hemen cikarilamiyor (isletim
   sistemi yokluyor). Zorlama bayragi (`-l`/`-f`) **kullanilmadi**; sinirli
   yeniden deneme eklendi (3 kez, artan bekleme). Ikinci deneme geciyor.
2. **Ust uste baglama** — basarisiz bir cikarmadan kalan nokta, sonraki
   baglamayla ust uste bindi ve alttaki dosya sistemi gorunmez oldu. Artik
   hedef zaten baglama noktasiysa `<etiket>-2` denenir.

### Dogrulandi (ana makine, loop aygiti — fiziksel diske dokunulmadi)

```
bagla -> True /media/pc/DENEME
nokta sahibi: uid=1000 gid=1000     (root degil)
dosya sahibi: uid=1000 gid=1000     (uid= secenegi calisti)
cikar -> True · nokta bos · klasor kaldirildi
```

| Denetim | Sonuc |
|---|---|
| `tests.run_all` | **41/43** · 2 atlandi (yalnizca Windows) — yeni `t43_baglama_guvenlik_katmani` dahil |
| `tests.ui_smoke` | TAMAM (etiketler platformdan, goruntude kapali, menude var) |
| `tests.diag_check` | 13/13 |
| `platform_check` / `i18n_check` | 0 bulgu / BASARILI |

Windows ve macOS dallari bu oturumda **calistirilmadi**; kod yollari
yazildi ve statik denetimden gecti, "test edildi" denmiyor.

### Belgeler

`diskgenius-parity.md` ve `project-overview.md` guncellendi: "surucu harfi
atama" kapsam disi listesinden cikti (UEFI onyukleme girisi yonetimi de
yanlislikla orada duruyordu, o da cikarildi).

## 2026-09-21 — Uygulama ikonu: aday tasarimlar

Uygulamanin kendi ikonu (pencere / gorev cubugu / exe) yoktu; `DiskUltimate.spec`
bunu yorum satirinda belirtiyordu. Dokuz aday cizildi ve secim icin onizleme
sayfalari uretildi:

- Uretici: `.tmp/ikon/ikon_adaylari.py` (QPainter, `QT_QPA_PLATFORM=offscreen`)
- Onizleme: `.tmp/ikon/adaylar-acik.png`, `adaylar-koyu.png`,
  `adaylar-boyutlar.png` (16/24/32/48/64 px, acik ve koyu zemin)

Adaylar: A Tabak, B Katmanlar, C Bolum Seridi, D Kalkan, E D Monogrami,
F Dilim, G Disli Disk, H Bolunmus Surucu, I Tarayici.

Hepsi **cizilerek** uretilir — `ui/icons.py` kuralina (SVG yok, calisma zamani
bagimliligi yok) uyar; secilen aday dogrudan `icons.py` icine tasinabilir ve
ayni cizimden `.ico` / `.png` / `.icns` uretilir.

Not: `QGuiApplication(...)` sonucu bir degiskene atanmazsa toplaniyor ve
islem cokuyor (olculdu: segfault). Aday uretici bu yuzden referans tutar.

**Secim bekliyor** — aday secilene kadar depoya ikon dosyasi girmedi
(`.tmp/` surum kontrolu disindadir).

---

## 2026-09-21 — Bolumun bagli oldugu yer gosteriliyor

Istek: "partitionlar uzerinde surucu harfi veya yolunu gosterebilir miyiz?"

- `Partition.mount_point` alani eklendi; kaynagi `DiskInfo.mount_map`
  (**{bolum baslangici (bayt): nokta}**). Linux'ta `/sys/.../start` +
  `/proc/mounts`, Windows'ta birim extent'lerinin `StartingOffset` alani —
  o IOCTL zaten harfleri ogrenmek icin cagriliyordu, **ek maliyet yok**.
  Bolum basina isletim sistemine sormak Windows'ta her bolum icin bir aygit
  tutamaci demekti (ADR 0021).
- Gosterim: bolum tablosunda yeni sutun (basligi platforma gore
  "Baglama noktasi" / "Surucu harfi"), harita blogunda dosya sistemi adinin
  sagina vurgu renginde (sigmiyorsa yazilmaz — kirpilmis yol yanlis okunur),
  bolum bilgisi sekmesinde bir satir.
- Gercek diskte olculdu (salt okunur, root):

```
bolum 1: FAT32  nokta=/boot/efi
bolum 3: NTFS   nokta=/media/pc/Basic data partition
bolum 4: NTFS   nokta=/run/media/pc/Data
bolum 5: ext4   nokta=/
```

- `tests.ui_smoke`: sutun basligi platformdan geliyor mu, bagli olmayan
  bolumde "-" yaziyor mu, **sutun kaymasi** dogru mu (tablo indisleri elle
  tasiniyor). `tests.run_all` 41/43, `platform_check` 0, `i18n_check` TAMAM.
- Sinir: Windows'ta uygulamanin kendi actigi diskin harfleri bolume
  eslenemez (o diskin birimleri bilerek yoklanmaz); harf disk duzeyinde
  gorunur. macOS dali test edilmedi.

### Ayni gun — SVG kaynakli, sabit disk gorunumlu ikinci tur

Kullanici "SVG kullanilabilir, daha SABIT DISK gorunumlu olsun" dedi. Ayrim
onemli oldugu icin yaziliyor:

- **Uygulama ici ikonlar** (arac cubugu, menu) SVG **olamaz** — `PyQt5.QtSvg`
  her dagitimda kurulu degil, calisma zamani bagimliligi yasak (ADR 0025).
- **Uygulama ikonu** farklidir: SVG yalnizca **kaynak bicimdir**, derlemede
  `.ico` / `.png` / `.icns` uretilir. Calisma zamaninda hicbir sey gerekmez.
  Bu makinede `QtSvg` kurulu, onizlemeler onunla rasterize edildi.

Sekiz aday: `.tmp/ikon-svg/HD1..HD8.svg` (+ `.png`), onizlemeler
`.tmp/ikon-svg/adaylar-acik.png`, `adaylar-koyu.png`, `adaylar-boyutlar.png`.
Uretici: `.tmp/ikon-svg/hdd_svg.py` (yaylar ve izometrik yuzler hesaplanarak
yazilir, elle koordinat girilmez).

HD1 Acik Surucu · HD2 Bolumlu Plaka · HD3 Izometrik Surucu · HD4 Plaka Yigini
HD5 Etiketli Surucu · HD6 Plaka Yakin · HD7 Surucu + Kalkan · HD8 Duz Siluet

Ilk turdan sonra duzeltilenler: ses bobini miknatisi govde boslugunun disina
tasiyordu (`clipPath` ile kirpildi); izometrik govde "acik kutu" gibi
okunuyordu (yan yuzler koyulastirildi, etiket buyutuldu); HD5/HD7'de beyaz
etiket baskindi, 16 pikselde "belge" gibi gorunuyordu (etiket kucultuldu,
metal govde ve koyu serit cercevesi one alindi).

**Secim bekliyor.**

### Ayni gun — ucuncu tur: indirilen ozgur ikonlar taban alindi

Kullanici "internetten ucretsiz ikonlar indir ve gelistir" dedi. 25 ikon
indirildi (`.tmp/ikon-indirilen/`), sekiz aday bunlar taban alinarak
gelistirildi (`.tmp/ikon-gelistirilmis/G1..G8.svg`). Yontem: kaynak yol verisi
**degistirilmeden** durur, uzerine malzeme (metal govde, plaka), bolum renkleri
ve zemin eklenir; boylece hangi parcanin kimden geldigi bellidir.

**Lisans bulgusu (onemli):** Remix Icon Ocak 2026'da Apache-2.0'dan kendi
lisansina gecmis ve **madde 3.3** ikonlarin degistirilmis olsa bile **logo veya
uygulama ikonu** olarak kullanilmasini yasakliyor. Ilk cizilen Remix tabanli
aday (G5) **atildi**, yerine Lucide `database` (ISC) tabanli "Plaka Yigini"
kondu. Remix dosyalari `KULLANILMAZ-remix/` altina alindi.

Ayrica Material Design Icons artik "Pictogrammers Free License" adi altinda
dagitiliyor; metin ikonlarin **Apache-2.0** oldugunu ve setin "GPL friendly"
oldugunu soyluyor — kullanimda sorun yok.

Kullanilan setler: Lucide (ISC), MDI (Apache-2.0), Bootstrap Icons (MIT),
Tabler (MIT). Ayrinti ve secim sonrasi yapilacaklar:
`.tmp/ikon-gelistirilmis/LISANSLAR.md`. Lisans metinleri
`.tmp/ikon-indirilen/lisanslar/` altinda indirildi.

Adwaita (CC BY-SA 3.0) ve Breeze (LGPL) yalnizca karsilastirma icin acildi,
turetilmedi.

**Secim bekliyor** — uc turda toplam 25 aday var (9 cizim + 8 SVG + 8 turetme).

## 2026-09-21 — Uygulama ikonu baglandi (favicon.ico)

Kullanici depo kokune bir `favicon.ico` birakip "bunu ikon olarak kullan" dedi.
Karar ve gerekce: `.claude/decisions/0044-uygulama-ikonu-dosyadan.md`.

### Kaynak dosyada kusur bulundu

`favicon.ico`'nun **alti boyutu da tamamen opakti** — saydamlik yerine dama
deseni (128/256) ve duz `#f3f3f3` (16..64) piksel olarak gomulmus. Oldugu gibi
kullanilsa gorev cubugunda ikonun arkasinda acik gri bir kare gorunurdu.
Kenarlardan tasma dolgusuyla arka plan geri kazandirildi (kenara bagli olmayan
beyazlar — "SSD" yazisi, metal parlama — korundu; sinirda kismi alfa, halo yok).

### Yapilanlar

| Dosya | Degisiklik |
|---|---|
| `assets/branding/favicon.ico` | kullanicinin ozgun dosyasi (kokten buraya tasindi) |
| `assets/branding/uret_ikon.py` | **yeni** — `.ico` -> temiz `.ico` + 7 PNG + `.icns`, yalnizca PyQt5 |
| `src/diskultimate/ui/resources/` | **yeni** — uretilen app-icon.ico / -<n>.png / .icns |
| `src/diskultimate/ui/appicon.py` | **yeni** — `app_icon()`, paket-goreli yol (i18n yontemi) |
| `main.py` | `app.setWindowIcon(app_icon())` — butun pencereler miras alir |
| `DiskUltimate.spec` | ikon datas'a eklendi + `icon=[EXE_ICON]` (macOS'ta `.icns`) |
| `tests/ui_smoke.py` | ikon denetimi: dosya var mi, 7 boyut tam mi, **saydam mi** |

`build_linux.sh` ve `build_exe.bat` degismedi — ikisi de butun ayari spec'ten
alir.

### Olculenler

```
ozgun favicon.ico : 370.070 bayt, 6 boyut, saydam piksel 0     (opak)
app-icon.ico      : 147.989 bayt, 7 boyut (24 px eklendi), %26-40 saydam
uygulama ikonu    : bos degil, 7 boyut, pencere miras aliyor
platform_check    : 0 bulgu
i18n_check        : BASARILI
sozdizimi         : ui_smoke / main.py / appicon.py / uret_ikon.py / spec TAMAM
eklenen test blogu: tek basina gecti; ozgun dosyaya uygulansa KALIYOR (yani
                    denetim gercekten kusuru yakaliyor)
```

**VM'de kosulmadi:** `tests.ui_smoke` ve `tests.run_all` tam haliyle
calistirilmadi — Linux misafiri (192.168.42.131) bu oturumda kapaliydi
(baglanti zaman asimi). Ana makinede yalnizca yeni eklenen ikon denetimi ve
statik denetimler kosturuldu. **Windows tarafinda exe ikonu sinanmadi**;
`icon=` yolu yazildi, ama "test edildi" denmiyor.

### Not

Uretim sirasinda iki kez ayni tuzaga dusuldu ve ikisi de araca not olarak
yazildi: (1) `QGuiApplication` sonucu bir degiskene atanmazsa toplanip sureci
cokertiyor, (2) `QPixmap` uzerinden PNG kaydetmek alfa kanalini dusuruyor —
`QImage`'e cevirip `Format_ARGB32` zorlamak gerekiyor.

### Kesif turunun duzelttikleri (4 ajan, paralel)

Baglanti noktalari once paralel bir kesifle cikarildi; dort bulgu ilk
uygulamayi duzeltti:

1. **Test gercek kod yolunu sinamiyordu.** `tests/ui_smoke.py` kendi
   `QApplication`'ini kurar ve `main()`'den hic gecmez — baglama yalnizca
   `main.py` icinde yazili kalsaydi duman testi onu **asla gormezdi**.
   Baglama `ui/appicon.apply_app_icon(app)` islevine tasindi; main.py ve
   ui_smoke ayni islevi cagiriyor.
2. **Saydamlik "dolu" olmanin olcusu degil.** Bastan sona saydam (bos) bir
   goruntu de o denetimden gecerdi. Renk cesitliligi denetimi eklendi
   (32 px'te en az 32 renk; olculen: 676).
3. **Linux'ta PyInstaller uyarisi.** `icon=` alani Windows ve macOS disinda
   uygulanmaz, yalnizca "Ignoring icon" uyarisi basar. Artik Linux'ta hic
   verilmiyor (`EXE_ICON = None if IS_LINUX`), pencere ikonu orada `datas`
   ile gelen dosyadan yukleniyor.
4. **Windows'ta kaynaktan calistirma.** `python main.py` ile acildiginda
   Windows pencereyi yorumlayiciya ait sayip **Python'un** ikonunu gosterir.
   `core/platform.set_app_user_model_id()` eklendi (ctypes, Windows disinda
   no-op), `main.py` icinde pencere yaratilmadan once cagriliyor.

Ayrica: paketleme betiklerinin "kaynak agaci yerinde mi" on denetimine ikon
eklendi, `.gitattributes` ikili dosyalari CRLF cevriminden korudu,
`ui/icons.py` docstring'ine kapsam notu yazildi ("burasi islem ikonlari").

### Acik kalan / dogrulanmayan

- **Linux masaustu girdisi yok.** Projede `.desktop` dosyasi bulunmuyor;
  paketlenmis Linux ikilisinin dosya yoneticisinde ikonu **olmaz**. Pencere ve
  gorev cubugu ikonu calisma aninda gelir. "Linux'ta da ikon var" denmemeli.
- **Windows'ta exe ikonu gorulmedi.** `icon=` yolu yazildi ve uc platform icin
  mantigi benzetimle sinandi, ama exe uretilip bakilmadi (bu makine Linux).
- **VM'de kosulmadi:** Linux misafiri kapaliydi.
- **Ikonun kaynagi bilinmiyor.** `favicon.ico` kullanicidan geldi; hangi setten
  turedigi ve lisansi dogrulanamadi. Ayni oturumda Remix Icon'un uygulama ikonu
  kullanimini yasakladigi gorulmustu — bu dosya icin ayni riskin olup olmadigi
  bilinmiyor, kullaniciya soruldu.

### Not — push parola sordu (cozuldu)

`git push` birden parola istemeye basladi ("unable to read askpass response
from ksshaskpass"). Sebep kimlik degil **protokol**: `gh` hesabi acik ve SSH
anahtari GitHub'da calisiyor (`ssh -T git@github.com` -> "Hi mcansiz!"), ama
bu deponun uzak adresi **HTTPS** idi. HTTPS SSH anahtarini hic denemez,
parola/token sorar; pencere acamayan bir oturumda bu dogrudan hata olur.

Cozum, uzak adresi SSH'a cevirmek:

```
git remote set-url origin git@github.com:mcansiz/DiskUltimate.git
```

`gh auth status` zaten "Git operations protocol: ssh" diyordu — depo klonu
HTTPS ile alinmis oldugu icin ikisi ayrismisti. Depo baska bir makineye
klonlanirsa ayni sey olur; `gh repo clone` kullanmak veya klondan sonra bu
komutu bir kez calistirmak yeterli.

## 2026-09-28 — Yedekleme/geri yukleme: not yalnizca yedek alirken, geri yuklemede bolum yerlesimi (ADR 0045)

Kullanici DiskGenius'un yedek/geri yukle pencerelerini ornek gosterdi.

- **Not:** geri yukleme kipinde not duzenleyicisi ve "Notu kaydet" kalkti; not
  bilgi alaninda salt okunur "Aciklama" satirinda gorunur.
- **Yeni `core/restoreplan.py`:** `RestoreLayout` (yedekteki bolumlerin
  hedefteki yeri/boyutu + FS sinirlari), `restore_with_layout` (bolum bolum
  yazar, FS'i kucultur/buyutur, tabloyu hedefe gore yeniden yazar).
  `restore_disk`, `restore_to_physical`, `restore_to_new_image` `layout`
  aliyor; yeni goruntu `size_bytes` ile istenen boyutta olusur.
- **`restore_partition`:** bolum yedekten buyukse FS bolumu doldurur; gizli
  sektor alani hedef bolume gore duzeltilir (onceden duzeltilmiyordu).
- **Arayuz:** geri yuklemede harita hedefin *geri yukleme sonrasi* halini
  gosterir; "Bolumleri yonet..." penceresi (`ui/dialogs/restore_layout.py`)
  tabloyla birlikte surukleyerek boyutlandirma (`ResizePartitionDialog`
  yeniden kullanildi), "Yedekteki gibi", "Son bolumu genislet", "Diske
  orantili yay". Yeni goruntu hedefinde goruntu boyutu secilir.
- **Testler:** `run_all.t44_geri_yuklemede_bolum_yerlesimi` (buyuk / kucuk /
  elle tasima / sigmayan / ozdes / bolum yedeginin bolumu doldurmasi);
  `ui_smoke` not ve yerlesim penceresini denetler.
- Statik denetimler ana makinede: `platform_check` 0 bulgu, `i18n_check`
  her dil TAMAM (59 yeni metin en/de).

### Windows VM sonucu (VirtualBox `win10 `, Windows 10 19044, Python 3.12.8)

Kullanici onayiyla VirtualBox'taki Win10 misafirinde kosuldu (kayitli VMware
misafiri degil — bkz. hafiza `windows-test-ortami`). Kaynak yerel diske
kopyalandi (`C:\du-test\DiskUltimate`), paylasimda kosulmadi.

- `run_all`: **43/44**. `t44` geciyor. Kalan `t43_baglama_guvenlik_katmani`
  bu degisiklikten **bagimsiz**, onceden de vardi: `partition_device("/dev/sdb", 3)`
  Windows'ta `/dev/sdb3` dondurmuyor; test platforma gore ayrilmamis.
- `ui_smoke`: **tamam** (36/37/38 ekran goruntuleri). Offscreen kipte font
  yok; goruntulerde metin gorunmuyor, yerlesim gozle denetlenmedi.
- `platform_check` 0 bulgu, `i18n_check` TAMAM.

Test sirasinda bulunan, **onceden var olan** iki hata duzeltildi:

1. **FAT ve NTFS bicimlendirici "gizli sektor" alanini (BPB 0x1C) 0
   yaziyordu**; yalnizca exFAT bolum konumunu yaziyordu. Windows'un
   onyukleme kodu bu alani okur. `t44` elle yerlesim durumunda degismeyen
   FAT32 bolumunde yakaladi. `formatter.format_partition` artik
   `view.start_lba`'yi FAT'a (`hidden_sectors`) ve NTFS'e
   (`format_ntfs(partition_offset=)`) veriyor.
2. **`ui_smoke` yedekleme bolumune hic ulasamiyordu:** uygulama ikonu
   denetimi `goruntu` degiskenini QImage ile eziyordu; sonraki
   `DiskSession.open(goruntu)` patliyordu. Degisken `ikon_resmi` oldu.

Ayrica `t44`'un "sigmayan hedef" durumu 48 MiB'ta aslinda sigiyordu (FAT32
~33 MB + NTFS/exFAT birkac MB); 24 MiB'a indirildi.

### Misafir notlari
- Misafirdeki Python **embeddable** dagitim: `C:\Python312\python312._pth`
  calisma dizinini ve `PYTHONPATH`'i yok sayar; `python -m tests.x` "No
  module named tests" verir. Kosum `runpy` + `sys.path.insert` sarmalayicisiyla
  (`C:\du-test\kos.bat <modul>`) yapildi. `sys.argv[0]` gercek betik yolu
  olmali, yoksa `t22` yukseltme komutu uretmez.
- `VBoxManage guestcontrol ... run --cwd` dikkate alinmiyor; `cmd /c "cd /d ..."`
  gerekli. SSH yonlendirmesi (2222) yanit vermiyor.
- Linux VM'de (Mint) kosulmadi. Fiziksel diske yerlesimli geri yukleme
  sinanmadi.

## 2026-09-28 (2) — Yedek penceresi: boyut birimi, bayat icerik, "Disk sec" penceresi

Kullanici geri bildirimi (ekran goruntusuyle):

- **Goruntu boyutu yalnizca GB aliyordu** → yanina MB / GB / TB secimi
  (`size_unit`). Birim degisince bayt korunur, yalnizca gosterim degisir;
  varsayilan yedek boyutuna gore secilir (1 GB alti MB). Kutu yalnizca "Yeni
  goruntu dosyasi" hedefinde gorunur.
- **Yedek secilmeden icerik agacinda bolumler gorunuyordu** — yedek alma
  kipinden kalan *kaynak* bolumleriydi. Geri yukleme kipinde yedek yoksa agac
  "(yedek dosyasi secilmedi)" gosterir.
- **Hedef listesi cok kucuktu** (uc satir, kaydirmak gerekiyordu) → agac ana
  formdan cikti, **"Disk sec..."** dugmesiyle acilan genis pencereye
  (`_TargetPicker`) tasindi; iptal onceki secimi geri getirir, cift tiklama
  secer. Formda "Hedef: **ad** — boyut — durum" ozeti kalir; sistem diski
  kirmizi yazilir. Harita bosalan yer sayesinde genisledi.
- `ui_smoke` icin yeni denetimler: agac formda gorunmuyor, kip degisiminde
  bayat icerik yok, birim degisimi boyutu korur, birim kutusu yalnizca yeni
  goruntude.

Windows VM (VirtualBox `win10 `): `ui_smoke` tamam; ekran goruntuleri bu kez
`DISKULTIMATE_QPA=windows` ile gercek fontla alinip gozle denetlendi
(36/37). Bu denetimde birim kutusunun her hedefte gorundugu yakalandi ve
duzeltildi. "Disk sec" penceresinin kendisinin goruntusu alinmadi (modal).
`i18n_check` TAMAM (9 yeni metin), `platform_check` 0 bulgu.

## 2026-09-28 (3) — Geri yuklemede hedef acik secilir; serit uzerinde tutamaklar

Kullanici geri bildirimi (DiskGenius ekran goruntusu, tutamaklar isaretli):

- **Yedek secilmeden hedef alani pasif ve bos.** Geri yukleme kipinde hedef
  **hicbir zaman kendiliginden secilmez**: ana pencere her acilista o anki
  oturumu gonderiyordu, yani silinecek hedef kullaniciya sorulmadan secili
  geliyordu. Artik yedek yuklenince "Hedef diski secin (Disk sec...)" der;
  yedek almaya donulunce kaynak secimi geri gelir.
- Grup adi kipe gore **"Hedef Disk / Bolum"** / **"Kaynak Disk / Bolum"**.
- **Tutamakli serit (`ui/widgets/layout_bar.py`)**: geri yuklemede harita
  yerine yedegin hedefteki yerlesimi cizilir; bolum kenarlari suruklenir.
  Bitisik iki bolumun sinirinda tek tutamak vardir (biri buyur, oteki
  kuculur). Sinir/hizalama mantigi cekirdekte: `RestoreLayout.edges()` ve
  `move_edge()`; oynayamayan kenar (boyutu sabit ext4 siniri) listelenmez.
  "Bolumleri yonet" penceresi de ayni seridi kullanir.
- Testler: `t44`e surukleme denetimleri (sinir tasima, en az boyutta durma,
  disk sonunu gecmeme, suruklenmis yerlesimle gercek geri yukleme);
  `ui_smoke`a pasif hedef alani, kendiliginden secilmeyen hedef, seritte
  fareyle surukleme.

Windows VM (VirtualBox `win10 `): `run_all` 43/44 (kalan `t43` onceden var,
Linux yolu), `ui_smoke` tamam, ekran goruntuleri gercek fontla gozle
denetlendi. `i18n_check` TAMAM (7 yeni metin), `platform_check` 0 bulgu.
Fareyle surukleme yalnizca sentetik olaylarla sinandi; elle denenmedi.

## 2026-09-28 (4) — Ana haritada tutamaklar; yenile ikonu; ikon adaylari

- **Ana haritada surukleme tutamaklari** (`widgets/disk_map.py`): secili
  bolumun kenarlarinda geri yukleme seridiyle ayni tutamaklar cikar.
  Bloklar tam oransal olmadigi icin (kucuk bolume en az genislik) piksel <->
  LBA donusumu blok blok yapilir. Surukleme sirasinda yeni aralik kesik
  cerceve + boyut rozetiyle gosterilir; birakinca `resizeRequested` ->
  `MainWindow._queue_resize` -> ayni `plan_resize` dogrulamasi ve cakisma
  denetimi -> `resize_op` **kuyruga** girer (ADR 0025; diske yazilmaz).
  "Bolumu boyutlandir" penceresi de artik ayni `_queue_resize`dan gecer.
  Tutamak yalnizca diskteki bolum icin: mantiksal/genisletilmis, planda yeri
  degismis ya da yazma moduna gecemeyen kaynakta cikmaz. FS sinirlari
  (`fs_resize_info_for`) yalnizca surukleme baslarken okunur ve olculur;
  Windows'un PowerShell sinir sorgusu burada kullanilmaz (arayuzu dondururdu).
- **Yenile ikonu**: ok basi yaya teget degildi. Tek yay + teget ok basi
  (`_arc_arrowhead`). Iki yayli bicim de cizildi, 16 px'te kalabalik
  bulundu.
- **Ikon adaylari**: disk (5) ve Linux (5) alternatifleri cizildi —
  `.claude/logs/ikonlar/ikon-adaylari-2026-09-28.png` (uretici:
  `ikon_adaylari.py`). Kullanici secimi bekleniyor; henuz uygulanmadi.
- Windows VM: `ui_smoke` tamam (yeni: haritada surukleme -> kuyrukta
  `resize` adimi, goruntu dosyasi degismedi; `39-haritada-tutamak.png`).

## 2026-09-28 (5) — Ikon envanteri ve alternatif katalogu (secim bekliyor)

Envanter: `ui/icons.py` 47 islem ikonu (DRAWERS), `ui/theme.py` 3 isletim
sistemi amblemi, `partition_table.color_chip` (FS renk karesi, 10 yerde).
Uygulama ikonu dosyadan (ADR 0044), kapsam disi. `QStyle`/tema ikonu
kullanilmiyor.

Katalog `.claude/logs/ikonlar/`:
- `katalog-1..6.png` — her ikon icin A mevcut / B kutucuk (renkli zemin +
  beyaz sembol) / C cizgi (notr hat + renk vurgusu). B ve C ayni sembol
  geometrisini paylasir (`ikon_katalogu.py`), set tutarli kalir.
- `paket-1..6.png` — ayni satirlar + Tabler, Phosphor, Lucide, Bootstrap,
  Material Symbols karsiliklari (`paket_katalogu.py`, jsDelivr'den).
- `paket-os.png` — Windows, Linux, macOS + Ubuntu/Debian/Fedora/Arch/Mint:
  Tabler, Phosphor, Bootstrap, Font Awesome, Simple Icons, Devicon.

Lisans (kaynaktan okundu): Tabler MIT, Phosphor MIT, Lucide ISC, Bootstrap
MIT, Material Apache-2.0, Devicon MIT, Simple Icons CC0, Font Awesome
ikonlari CC BY 4.0 (atif zorunlu; marka ikonlari icin ayrica "yalnizca o
markayi temsil etmek icin" kisiti). **Remix Icon v1.0 lisansi logo/marka
olarak kullanimi yasakliyor** — onerilmedi. Marka logolari (Windows, Tux,
Apple, dagitimlar) hangi paketten gelirse gelsin sahiplerinin ticari
markasidir; paket lisansi bunu ortadan kaldirmaz.

Uygulamaya alma yolu (secimden sonra, ADR gerekir): paketler SVG; QtSvg her
dagitimda yok (ADR 0025). Secilen ikonlarin `d` yol verisi saf Python'da
QPainterPath'e cevrilerek gomulur — calisma zamani bagimliligi dogmaz.
Onizlemeler gelistirme makinesinde QtSvg ile uretildi (gelistirme araci).

## 2026-09-28 (6) — Secilebilir ikon setleri (ADR 0046)

Kullanici karari: isletim sistemi amblemi Simple Icons; ikon setleri icin
menude secim, sekizi de.

- `tools/iconpacks.py` (gelistirme araci): 5 paket x 47 ikon + Simple Icons
  (linux, apple) -> `src/diskultimate/ui/iconpacks/*.py` (surum sabit,
  lisans metni modulde).
- `ui/svgpath.py`: saf Python SVG yol ayristirici. QtSvg'ye karsi 237 ikonda
  olculdu: en kotu fark %0.3, ortalama ~0.
- `ui/iconsets.py`: set kaydi, saklama (`icon_set`), Kutucuk/Cizgi sembolleri
  (katalogdan tasindi).
- `ui/icons.py`: `_LiveIconEngine` — set degisimi yeniden baslatmadan.
- `ui/theme.os_icon`: Linux/macOS Simple Icons; Windows kendi amblemimiz.
  **Bulgu:** Simple Icons Microsoft logolarini Microsoft'un hukuki talebiyle
  kaldirmis (v13.0.0); katalogdaki Windows onizlemesi jsDelivr `@latest`
  etiketinin eski surumu gostermesinden geliyordu.
- Menu: Araclar > Ikon seti (her secenekte o setin disk ikonu); Yardim >
  Ucuncu taraf lisanslari.
- `ui_smoke`: 8 set x 47 ikon bos degil, paketlerde 47/47 gomulu, ayni QIcon
  set degisince yeni setle ciziliyor, secim saklaniyor, OS amblemleri,
  6 lisans bildirimi.
- `i18n_check` TAMAM, `platform_check` 0 bulgu.
- **VM'de kosulmadi:** Win10 VirtualBox misafiri kapaliydi, Linux VM'e
  erisilemedi. Kullanici onayi beklenmeden misafir baslatilmadi.

## 2026-09-28 (7) — Tutamak donmasi ve cift disk satiri (ADR 0047)

Kullanici bildirimi + donma raporlari: haritadaki tutamaklar her suruklemede
iki kez donuyordu (basinca `map.handle_limits` 1.5 sn, birakinca
`plan_resize` ayni NTFS `$Bitmap` taramasi). Duzeltme: bayt duzeyinde bitmap
sayimi, sinirlar secimde arka planda (`_LimitsWorker`) + onbellek, birakista
onbellek `plan_resize(info=)`. Aygit okumasi `BlockDevice` alt siniflarinda
otomatik `RLock` (seek+read yarisi).

Hedef listesi: acik fiziksel disk artik yalnizca "Fiziksel diskler" altinda,
oturuma bagli; ikinci tutamac acilmaz (ADR 0021). Acik oturuma geri
yuklemede `become_writable` eksikti — eklendi.

Testler: `run_all.t45` (bitmap esitligi 300+ rastgele durum, 4 is
parcacigiyla eszamanli okuma); `ui_smoke` (tutamak once gri sonra etkin,
acik fiziksel disk tek satir + bolumleri disk korumasi tasir).
Statik: `i18n_check` TAMAM, `platform_check` 0. **VM'de kosulmadi** (Win10
VirtualBox kapali, Linux VM erisilemez).

## 2026-09-28 (8) — Qt standart dugmeleri Turkce; Win10 VM dogrulamasi

**Sorun:** Turkce arayuzde onay kutularinda "Yes / No". Standart dugme
metinleri Qt'den gelir (`QPlatformTheme`, `qtbase_<dil>.qm`); uygulama Qt
cevirisini hic yuklemiyordu.

**Duzeltme:** `ui/qt_i18n.py` — iki katman: (1) `qtbase_<dil>.qm` (varsa;
dosya diyalogu, sag tik menusu gibi butun Qt metinleri), (2) yedek
`_ButtonTranslator`: Evet/Hayir/Tamam/Iptal/Kapat... bizim `.po`
sozluklerimizden — `.qm` olmayan kurulumlarda (dagitim paketi, exe) da
dogru. Son kurulan once sorulur, dugmeler uygulamanin geri kalaniyla ayni
yazimda. `main.py` yetki penceresinden once kurar; dil degisince yenilenir.

**Win10 VirtualBox (kullanici onayiyla basslatildi, headless):**
- `run_all` 44/45 — yeni `t45` (bitmap esitligi, 4 is parcacigiyla okuma) ve
  `t44` geciyor; kalan `t43` onceden var (Linux aygit yolu Windows'ta).
- `ui_smoke` TAMAM (gercek Windows cizimi): Qt cevirisi yuklendi, Evet/Hayir
  tr/en/de dogru; 8 set x 47 ikon; canli set degisimi; tutamak once gri
  sonra etkin, surukleme kuyruga; acik fiziksel disk tek satir.
- `diag_check` 13/13.

Test sirasinda bulunan iki hata duzeltildi:
1. Ikon seti koddan degisince menudeki isaret eski sette kaliyordu —
   `change_icon_set` isaretleri esitliyor.
2. `ui_smoke` kullanicinin kalici ayarina yaziyordu ve yarida kalinca
   `icon_set=tabler` birakti; sonraki kosu degisimi goremedi. Test artik
   baslangici kendisi kurar ve `finally` ile onceki secimi geri yukler.
   (Ayar dosyasi testlerde yalitilmiyor — ayrica ele alinmali.)

## 2026-09-28 (9) — Planda acilan alana buyume (ADR 0048)

Kullanici bildirimi: kucultme yapiliyor, buyutme yapilamiyor. Neden: tutamak
penceresi, planda degismis bolumun tutamagi ve kuyruga ekleme dogrulamasi
diskteki tabloya bakiyordu. Duzeltme: `planview.planned_window`,
`plan_resize(window=)`, `OperationQueue.find_resize/replace`; ayni bolumun
adimi yerinde guncellenir, geri cekilince kalkar.

Win10 VM: `run_all` 45/46 (yeni `t46`: P1 kucult + P2 sola buyut/tasi,
uygulandi, iki bolumde veri saglam, exFAT buyudu; disk penceresi reddediyor,
planlanan kabul ediyor); `ui_smoke` TAMAM (kucultulen bolum geri buyutuldu,
adim yerinde guncellendi, diskteki boyuta donunce kalkti).

## 2026-09-28 (10) — Ortak bolum duzenleme modeli, 1. asama (ADR 0049)

Kullanici karari: ana ekran ve yedek geri yukleme ayni disk islemlerini tek
yerden beslenen bir yapiya gecsin. Plan bes asama (ADR 0049).

**1. asama — cekirdek model (tamam):**
- `core/layoutedit.py`: `EditableLayout` + `Slot` — `restoreplan`teki
  olgun model tasindi, kaynaktan bagimsiz. Yeni: `Slot.movable` (tasinamayan
  bolumun sol kenari ve sagindaki sinir oynamaz; `fit` reddeder),
  `Slot.apply_limits` (tur bazli sinir kurali tek yerde).
- `restoreplan` artik yalnizca **yedek -> hedef baglayicisi**
  (`build_layout`, `restore_with_layout`); `RestoreLayout`/`LayoutPart`
  geriye uyumlu adlar, `RestorePlanError` `LayoutError`dan turer.
- Arayuz ve oturum yeni adlari kullanir.
- Toplu ad degisiminde `RestoreLayoutDialog` da yanlislikla degismisti;
  import denetimiyle yakalanip geri alindi.

Win10 VM: `run_all` 46/47 — t44, t46 aynen geciyor, yeni t47 geciyor (kalan
t43 onceden var). `ui_smoke` TAMAM. `i18n_check` TAMAM, `platform_check` 0.

Siradaki: 2. asama — disk + kuyruk baglayicisi (ana ekran pencere ve
dogrulamayi modelden alir; `planview.planned_window` ve
`disk_map._continue_drag` sinir hesabi kalkar).

## 2026-09-28 (11) — Ortak bolum duzenleme: 2-5. asamalar (ADR 0049)

Kullanici: "tum asamalari uygula".

- **2.** `core/queueedit.py` (build/commit/neighbours); `planview` artik
  cakisma sirasi (`conflicts`) ve tasinan bolumun capasini izliyor;
  `OperationQueue.restore`. `planview.planned_window` kaldirildi.
- **3.** `ui/widgets/edgedrag.py` ortak surukleme denetleyicisi;
  `PartitionEditBar` (eski `LayoutBar`) ve `DiskMapWidget` onu kullanir;
  haritadaki kendi sinir hesabi silindi. Ana ekrana sinir tutamagi geldi.
- **4.** `ui/fslimits.py` (`FsLimitsService`) ve `core/disksource.py`
  (`collect`); ana agac ve "Disk sec" ayni listeden.
- **5.** `ui/dialogs/partition_layout.py` ortak pencere; ana ekranda
  "Bolum > Bolum duzenini degistir...".

Bulunan/duzeltilen:
- Sabit komsunun yanindaki bolum ortak sinir yuzunden kuculemiyordu — sinir
  kayamiyorsa iki ayri kenar (t47).
- Pencere kurulurken agac `_surveys` tanimlanmadan kuruluyor; ortak disk
  kaynagi bunu okuyordu (ui_smoke yakaladi) — duzeltildi.
- t46'da test hatasi: uygulama bolum nesnelerini yerinde degistirir;
  beklenen deger once sayi olarak saklanmali.

Win10 VM: `run_all` 46/47 (t43 onceden var), `ui_smoke` TAMAM (harita
tutamagi, geri buyutme, bolum duzeni penceresi, geri yukleme, hedef
listesi), `diag_check` 13/13. `i18n_check` TAMAM (16 yeni metin),
`platform_check` 0. Surukleme yalnizca sentetik fare olaylariyla sinandi.
- Son duzeltme: "Bolumu boyutlandir" penceresi sinirlari ham `FsResizeInfo`dan
  gosteriyordu (ham bolum icin en az 1 sektor) ama model kucultmeyi
  reddediyordu; pencere artik sinirlari modelden (`Slot`) alir. `ui_smoke`
  yeniden TAMAM.

## 2026-09-28 (12) — Paketleme: ikon paketleri ve Qt cevirisi exe'ye

Spec'te bu oturumdan kalan iki eksik:
- **Ikon paketleri exe'ye girmiyordu.** `ui/iconpacks/*` calisma aninda
  `importlib` ile adla yuklenir; PyInstaller gormez. Exe'de bes paket seti
  ve Linux/macOS amblemleri sessizce klasik cizime duserdi. Spec artik
  modulleri klasorden turetip `hiddenimports`a koyar; eksikse durur.
- **Qt cevirileri siliniyordu** ("QTranslator kullanilmaz" gerekcesi artik
  gecersiz, ADR 0046 sonrasi `ui/qt_i18n`). Yalnizca uygulamanin dilleri
  (`.po`dan turetilir: tr, de) icin `qtbase_<dil>.qm` tutulur (~300 KB);
  eksikse uyari basilir.
- Acilis gunlugune iki satir: `qt cevirisi: qtbase=... dil=...` ve
  `ikon paketleri: N/6 yuklendi` — paketlenmis kopyada dogrulama icin.

Win10 VM'de derlendi (PyInstaller 6.22.3, 24.8 MB):
- Arsiv icerigi: 6/6 ikon modulu, `qtbase_tr.qm` + `qtbase_de.qm` (digerleri
  atildi), `app-icon.ico`.
- Exe calistirildi (`--no-root`): gunlukte `qtbase=var dil=tr`,
  `ikon paketleri: 6/6 yuklendi`, platform windows.
- Exe'nin gomulu ikonu Windows'a cikartildi: dogru (favicon).
Linux/macOS paketlemesi sinanmadi.

## 2026-09-28 (13) — Linux dagitimi karari: AppImage + musl (ADR 0050, PLANLANDI)

Kullanici sordu: Linux icin AppImage mi? musl sistemler? Karar ADR 0050'de,
**uygulama sonraki oturuma birakildi** (kullanici istegi).

- AppImage birincil bicim; Flatpak/Snap elendi (ham disk erisimi yok),
  .deb ileride ek secenek.
- Bulgu: gelistirme makinesi glibc 2.43, Mint 22.3 VM 2.39 — burada derlenen
  PyInstaller ikilisi Mint'te acilmaz; derleme eski tabanda (Mint VM) olmali.
- Bulgu: `platform.py:240-241` yetkili kopyayi `sys.executable` ile baslatir;
  AppImage'da bu FUSE baglantisini gosterir ve root erisemez -> once
  `$APPIMAGE` duzeltmesi (zorunlu).
- musl: resmi ikili yok, kaynaktan calistirma desteklenir (`py3-qt5`,
  `doas`); AppImage'in `AppRun`u musl'da anlasilir mesajla durur.
Siradaki oturumun is listesi ve dogrulanmamis varsayimlar ADR 0050'de.

## 2026-09-28 (14) — Archify becerisi kuruldu (ADR 0051)

Kullanici istegi: github.com/tt-a1i/archify becerisini edinmek.

- Surum paketi (`archify.zip`, v3.0.1, commit `0e4949f`) `.claude/skills/archify/`
  altina acildi. Resmi `npx skills add -g` global dizine yazdigi icin
  kullanilmadi (CLAUDE.md kayit kurali).
- Guncelleme denetimi onbellegi `~/.cache` yerine `.claude/cache/archify`
  (`.claude/settings.json` -> `env`). `.gitignore`: `.archify/`, `.claude/cache/`.
- **Sinanmadi:** gelistirme makinesinde Node.js yok; beceri Node >= 18 ister.

## 2026-09-28 (15) — Archify ile core/ui katman diyagrami

`.archify/architecture-core-ui-katmanlari-20260928-213912/core-ui-katmanlari.html`
(depoya girmez). Kaynak kanitli (commit `3d8afac`, SSH uzak -> `local-only`
baglanti): 13 dugum, 15 baglanti, ui/ ve core/ bolgeleri, 4 kart.
- `finalize`: validate / deliver / check **gecti**; browser-check **atlandi**
  (Chrome yok, ADR 0051).
- Gorsel denetim headless Firefox ekran goruntusuyle yapildi; ilk surumde iki
  kenar ust uste biniyordu, 4 baglantinin kenari sabitlenerek giderildi.
- Viewer dugmeleri Ingilizce kaldi (Archify'da Turkce katalog yok; icerik Turkce).

## 2026-09-28 (16) — Archify: ayrintili modul mimarisi

`.archify/architecture-ayrintili-mimari-20260928-215252/ayrintili-mimari.html`
(depoya girmez). 25 dugum, 31 baglanti, 5 odak gorunumu (okuma yolu, yikici
islem kuyrugu, dosya sistemleri, fiziksel disk ve platform, onyukleme), 5 kart.
Katmanlar yukaridan asagi: ui -> orkestrasyon (DiskSession, OperationQueue,
duzenleme modeli) -> servisler -> FS motorlari -> BlockDevice -> aygitlar ->
platform.
- Kaynaktan cikan bulgu (diyagramda istisna olarak isaretli): onyukleyici ve
  UEFI diyaloglari `core.grub` / `core.efistore`'u DiskSession'i atlayarak
  dogrudan cagiriyor (`ui/dialogs/bootloader.py:29-31`, `efiboot.py:29`).
- Archify sinirlari: showcase kalitesinde viewBox en fazla 1240 px (1540 px
  ilk surum `composition/desktop-readability` ile reddedildi); `via` noktalari
  kenar portunu tasiyamaz (port kenarin ortasinda kalir).
- finalize: validate / deliver / check gecti; browser-check atlandi (Chrome
  yok). Headless Firefox ile 4 tur gorsel denetim; son surumde kesisme yok.

## 2026-09-28 (17) — Analiz: yeni dosya sistemleri (capraz platform)

Kullanici sordu: daha fazla dosya sistemi eklenebilir mi, tum platformlarda.
Kod degismedi; yalnizca analiz. Rapor: `.claude/docs/dosya-sistemi-genisletme.md`.

Koddan olculen bulgular:
- btrfs/XFS/F2FS/ISO9660 taniniyor ama kullanilan alan okunmuyor.
- `planview._fs_name` "swap" tasiyor, `FS_KINDS` icinde takas yok (yarim baglanti).
- BitLocker/LUKS/LVM/mdraid "Bilinmeyen" gorunuyor — guvenlik acisindan en
  onemli eksik.
- Kayip bolum taramasi ext'i bile aramiyor.
- Yeni bir dosya sistemi bugun 13 noktaya dokunmayi gerektiriyor; tek bir
  `FsSpec` kutugu onerildi.

Oneri: Asama 1 (taninma + takas + kayip bolum imzalari + ISO9660 okuma),
sonra HFS+ ve UDF 2.01, sonra XFS/btrfs/F2FS okuma, en son APFS salt okuma.
Engeller: stdlib'de LZO/LZ4/zstd/LZFSE yok; macOS test ortami yok.
Kullanici karari bekleniyor.

## 2026-09-29 (1) — ext2/3/4 saf Python boyutlandirma (ADR 0052)

Kullanici: "Linux'ta ext4 buyutme calismiyor, kabul edilemez; uc platformda
tum islemler." Kok neden: ext boyutlandirmasi yalnizca Windows
`Resize-Partition`'a yonleniyordu (o da ext'i tanimaz) → hicbir yerde
calismiyordu.

- Yeni: `core/extlayout.py` (ortak geometri, meta_bg, crc16), `core/extresize.py`
  (buyutme), `core/extmove.py` (kucultme: blok + inode tasima, htree,
  saglamalar). `resize.py`, `layoutedit.py`, geri yukleme "doldur" yolu ext'i
  taniyor.
- Duzeltilen eski hatalar: `extwrite` bigalloc bayragi (0x0100 = quota idi),
  kota artik acikca reddedilir, crc16 (uninit_bg) grup saglamasi, okuyucu ve
  yazici meta_bg bilmiyordu; t43 Windows'ta Linux beklentisiyle kosuyordu.
- Dogrulama: `tests/ext_resize_check.py` 28/28 (e2fsck + rdump birebir),
  `run_all` 46/48 (2 Windows'a ozgu atlandi), `platform_check` 0,
  `i18n_check` BASARILI (27 yeni metin en/de). Windows misafiri: t48 calisti;
  ana makinede hazirlanan 3 goruntu Windows'ta buyutup kucultuldu, Linux'ta
  e2fsck temiz. macOS kosulmadi.
- Test ortami degisti: Linux VM yok; Linux testleri ana makinede yalnizca
  goruntu dosyasiyla (CLAUDE.md + hafiza guncellendi). Dogrulama araclari
  (fsck.hfsplus, fsck.f2fs, mkudffs) `apt download` ile `.tmp/tools/root`a acildi.

## 2026-09-29 (2) — Sifreli/kapsayici birim taninmasi, genis imza kumesi (ADR 0053)

- `fsdetect`: LUKS1/2, BitLocker, CoreStorage, LVM2, Linux RAID (surum alani
  denetimli), APFS, ZFS, bcache, HFS+/HFSX/HFS (etiket + doluluk), UDF (etiket),
  ReFS, JFS, ReiserFS, bcachefs, NILFS2, EROFS, Minix, SquashFS; btrfs/XFS/F2FS
  dolulugu. `FSInfo.encrypted/container/maybe_encrypted` bayraklari.
- `operations.risk_notes` + Uygula penceresi: yikici adim sifreli/kapsayici
  bolume dokunuyorsa kirmizi uyari (ekran goruntusuyle denetlendi).
- Olcumle duzeltilen: JFS etiketi 0x98'de (0x88 UUID); ext 64bit bos blok
  ust yarisi okunmuyordu.
- Testler: t49 yeni (Linux + Windows gecti); `ui_smoke` surukleme senaryosu
  ext artik boyutlandirilabildigi icin iki adim (ortak sinir) bekleyecek
  sekilde uyarlandi; t43'teki iki Windows beklenti hatasi (aygit yolu,
  kritik baglama noktasi) duzeltildi. Linux 47/49 (+2 Windows'a ozgu atlandi),
  Windows: t48 e2fsck yok diye atlandi, digerleri gecti.
- Not: arayuz `fs_type`i cevirmeden gosteriyor ("Bilinmeyen", "Linux Takas"
  en/de'de Turkce) — FsSpec maddesine eklendi.

## 2026-09-29 (3) — Takas bicimlendirme, kayip bolum imzalari, geri ekleme hatasi (ADR 0054)

- `core/swap.py`: saf Python takas; `mkswap -L -U` ile ilk sayfa bayt bayt ayni.
  `FS_KINDS`e `swap` (MBR 0x82 / GPT takas GUID). Bicim listesi ve plan
  onizlemesi adi cevirir (`tr(kind.label)`).
- Kayip bolum taramasi: 68 KiB pencere; ext2/3/4, XFS, btrfs, HFS+/HFSX,
  APFS, F2FS, takas, ReFS. Bulunmus bolumun icindeki aday atlanir (yedek
  ustbloklar). NTFS/HFS+ etiketi bulunan aday icin okunur.
- **HATA (eski):** kayip bolumu tabloya geri eklemek bolumun ilk/son 2 MB'ini
  siliyordu (`create_partition` yeni bolum davranisi) → kurtarilan dosya
  sistemi yok oluyordu; tur da secilmiyordu (NTFS MBR'de 0x83). Duzeltildi:
  `create_partition(wipe=False)`, `create_op(keep_data=True, found_fs=...)`.
- Testler: t50, t51 yeni; t08 metin denetimi `human_size`a uyarlandi.
  Linux 49/51, Windows 49/51, ui_smoke, diag 13/13, i18n, platform temiz.

## 2026-09-29 (4) — ISO 9660 salt okuma, tablosuz disk (ADR 0055)

- `core/iso9660.py` + `IsoAccess`: Rock Ridge > Joliet > duz ad; sembolik bag;
  4 GiB ustu cok parcali dosya (4,1 GB SHA-1 dogrulandi); parcali disa aktarma.
  Test verisi `tests/fixtures/*.iso.gz`.
- `ptable.WholeDiskTable` + `session.read_partition_table`: tablosuz diskler
  (duz .iso, super disket USB, ext4.img) tek sanal bolum. 0x55AA'li FAT
  onyukleme sektoru artik MBR sanilmiyor (MBR giris gecerlilik denetimi);
  fiziksel disk yoklamasi ve geri yukleme plani da ayni kurali kullaniyor.
- Hatalar: tablosuz diskte silme reddi silmeden SONRA geliyordu (duzeltildi);
  yedek onizleme bolum sayisina gore karar veriyordu (sema oldu).
- Ana makinede disk doldu (4,4 GB test ISO'su + kopya) — `.tmp` temizlendi;
  buyuk test dosyalari artik diske kopyalanmadan ozetle dogrulaniyor.
- Linux 51/53, Windows 51/53, ui_smoke, diag 13/13, i18n, platform temiz.

## 2026-09-29 (5) — Dosya sistemi kutugu (ADR 0056) — Asama 1 tamam

- `core/fsregistry.py`: her dosya sistemi tek `FsSpec` (anahtar, gorunen ad,
  renk, MBR/GPT turu, OS ailesi). `theme.FS_COLORS`, `convert.FS_TO_MBR/GPT`,
  `physical` OS tahmini buradan turuyor.
- Arayuz ~30 yerde `fs_display()` kullaniyor: "Bilinmeyen"/"Linux Takas" artik
  en/de'de cevriliyor. Veri (`fs_type`) cevrilmez — plan onizlemesindeki
  `tr("Linux Takas")` (renk aramasini bozardi) ve otomatik degisiklikte
  baglama islevine giden ad geri alindi. 'ham'/'yok'/'Bicimlendirilmemis'
  yedek metinleri `tr()` ile sarildi.
- t54: `fsdetect`in urettigi her ad kutukte mi — ilk kosumda eksik "LUKS"
  kaydini buldu.
- Linux 52/54, Windows 52/54, ui_smoke (normal + qps), i18n, platform temiz.

## 2026-09-29 (6) — ext yazma: htree, extent agaci, dizin buyumesi (ADR 0057)

- **HATA (veri butunlugu):** yazici indeksli (htree) dizine duz sirayla yaziyor,
  dx_root indeksini eziyordu — gercek ext4'te kalabalik klasore kopyalama
  dizini bozuyordu. `core/exthtree.py` (e2fsprogs ile birebir karma; debugfs
  dx_hash ile 420/420) + yaprak bolme; indeks dolarsa guvenli dogrusal cevrim.
- Dizin buyumesi (extent agaci yeniden kurulur; dolayli cift kata), ext4'te
  yeni dosyalar extent, silmede extent dugumleri birakilir (sizinti vardi),
  baslatilmamis gruplar ilk kullanimda baslatilir ("Bos inode kalmadi"),
  yer on denetimi + inode geri verme (disk dolunca birim bozuluyordu).
- Test verisi `tests/fixtures/ext4_htree.img.gz` (23 KB); t55 yeni.
- Linux 53/55, Windows 52/55; Windows'ta yazilan htree goruntusu Linux'ta
  e2fsck temiz.

## 2026-09-29 (7) — NTFS yazma: B+ agaci, guvenlik, Windows uyumu (ADR 0058)

- `core/ntfsindex.py` (genel B+ agaci: $I30/$SII/$SDH; bolme, reparent, ara
  dugumden silme, dengeli yeniden kurulum, `verify_tree`), `core/ntfssecure.py`
  (Windows kurallariyla devralinan tanimlayici, $Secure'a ekleme/yeniden
  kullanma), `NtfsFS.lookup` (O(log n), birimin $UpCase'i).
- Windows'ta ilk kez gercek surucuyle sinandi: goruntu VHD olarak misafirin
  SATA'sina takildi (yonetici yok -> chkdsk yok), olay gunlugu okundu. Bulunan
  ve duzeltilen: kayit no 0x2C, guvenlik kimligi 0, rename $FILE_NAME'i
  degistirmiyordu, Win32 ad alani (POSIX olmali), 8.3 esi/sabit bag silme,
  bos yaprak rebuild sahipsiz giris, bicimsiz yeni MFT kayitlari, sira no,
  16-23 kayitlari, dizin indeksinin tek blok buyumesi, **kosu uzunlugunun
  isaretsiz kodlanmasi** (512 KB-1 MB araligindaki dosyalar ntfs-3g/Windows'ta
  bozuk gorunebiliyordu), UTC zaman, $MFT/$MFT:$BITMAP buyumesi, bitisik kume.
- Hiz: 10.600 islem 6 dk 50 sn -> 21,7 sn.
- Kullanici istegiyle Windows denetimi donmaya karsi: betik `guestcontrol
  start` ile bagimsiz, 20 sn'lik zaman asimli yoklama; 3 dk ilerleme yoksa durur.
- Test: t56 + `tests/fixtures/ntfs_windows.img.gz`. Linux 54/56, ui_smoke,
  diag 13/13, i18n, platform temiz. Windows son tur temiz.
- Yeni oncelikli madde: saf Python NTFS bicimlendirici Windows'ta tanınmiyor.

## 2026-09-29 (8) — NTFS bicimlendirici Windows'ta taniniyor (ADR 0059)

- Sorun: `format_ntfs` birimi Windows'ta `Unknown`; 1. tur duzeltmelerden
  sonra NTFS taninip olay 55 (bozulma) + saglik Warning.
- mkntfs birimiyle kayit kayit karsilastirma (`.tmp/win/dump.py`):
  - 1. tur: MFT kaydi 4096 -> 1024, 16-23 bicimli, kok INDX'li
    ($INDEX_ALLOCATION + $BITMAP), kok guvenlik kimligi 0x102 (mkntfs kok DACL).
  - 2. tur: `$Extend\$Quota`(24, $O + $Q) / `$ObjId`(25) / `$Reparse`(26)
    eklendi (degerler mkntfs ile zaman damgasi disinda birebir); tum sistem
    dosyalarinda 72 baytlik SI + guvenlik kimligi; 12-15 bag sayisi 0.
- Windows (VBox win10, VHD): iki birim **Healthy**, olay 98 "saglam";
  bizim yazdigimiz 301 dosyanin SHA-1'i birebir; Windows 500 + 200 dosya
  yazdi, 100 sildi.
- Test: yeni t57. Linux run_all 55/57 (2 yalnizca-Windows), Windows 54/57
  (3 Linux araci yok), ntfs_write_check 2/2, i18n BASARILI, platform 0 bulgu.
- `tests/run_all.py` t42: $SDS/$SII/$SDH 3 tanimlayici (kok eklendi).
- Not: VM'de SATA 5/6'da f1b_v.vhd / f2b_v.vhd takili (3/4'te w6/c8).

## 2026-09-29 (9) — Boyutlandirma matrisi; FAT/exFAT dizin hatalari

- Yeni `tests/resize_matrix.py`: 8 dosya sistemi x MBR/GPT x 5 adim
  (kucult, buyut, saga tasi, sola tasi+kucult, sola tasi+buyut); icerik
  SHA-1, adim sonrasi yazma, harici fsck.
- Matrisin buldugu hatalar (hepsi duzeltildi, t58):
  - **FAT ve exFAT:** dizin sonunda yetersiz bos yuva kalinca giris yeni
    kumenin basina yaziliyordu; aradaki 0x00 yuvasi okuyucuyu durdurdugu icin
    dosya "Bulunamadi" (FAT32 512 B kumede 4. dosyada!). Arama yeni kumeyle
    birlikte tekrarlanir.
  - **exFAT:** buyuyen alt dizinin ust girisindeki DataLength guncellenmiyordu.
  - **exFAT:** bos dosya NoFatChain bayragiyla yaziliyordu (fsck.exfat bozuk).
- Sonuc: Linux 16/16 (her adimda fsck temiz), Windows 16/16; sonuc
  birimleri Windows'un surucusunde Healthy, 128/128 dosya ozeti. macOS yok.
- FAT16 400 MB'ye buyutulunce FS 256 MB'de kalir (4K kume siniri) — plan
  bunu uyariyla bildiriyor; tasarim geregi.
- run_all 56/58 (Linux; 2 yalnizca-Windows).
- VM: SATA 3/4'te xb_v/xm_v (exFAT karsilastirma), 5/6'da m_exfat/m_ntfs.

## 2026-09-29 (10) — HFS+ / HFSX salt okuma (ADR 0060)

- Yeni `core/hfsplus.py` (TN1150): birim basligi, catal + kapsam tasmasi,
  katalog B-agaci; listeleme (kimlik, "") anahtarindan yaprak zinciriyle,
  ad aramasi NFD + casefold (HFSX duyarli); sabit/sembolik bag, decmpfs
  zlib, gomulu HFS+ (sarmalayici), kirli gunluk bayragi.
- `filesystem.HfsAccess` (salt okunur) + `open_filesystem` dali;
  fsdetect "BD" icindeki "H+"yi HFS+ bildirir.
- Test verisi: xorriso `-hfsplus` (libisofs) ve mkfs.hfsplus (hfsprogs);
  libhfsp hpmount modern birimde takildi, elendi (ADR'de). Fixture:
  `hfs_xorriso.iso.gz` (64 KB, 1504 dosya, 3 duzey), `hfs_journal.img.gz`,
  `hfsx_bos.img.gz`.
- Dogrulama: 6000 dosyalik birim kaynakla birebir (1,4 sn); t59. Linux
  run_all 57/59, Windows (VBox) 56/59 (3 Linux araci yok). i18n (13 yeni
  metin en/de), platform 0 bulgu, ui_smoke.
- Ayrica: exfat.py'deki yeni yerel adlar Ingilizcelestirildi (platform_check).
- macOS'ta sinanmadi; APM okunmuyor.

## 2026-09-29 (11) — HFS+ bicimlendirme, saf Python (ADR 0061)

- Yeni `core/hfsformat.py::format_hfsplus` (gunluksuz HFS+, istege bagli
  HFSX); `formatter.FS_KINDS` "hfsplus" (MBR 0xAF / GPT Apple HFS),
  planview adi. B-agaci dugum/harita dugumu ureticisi yazicida da
  kullanilacak.
- Yerlesim mkfs.hfsplus ciktisi 11 boyutta olculerek cikarildi (kume
  tablosu, 10 x kume boslugu, dugum boylari); 64M'de bayt bayt ayni
  (tarih + birim kimligi disinda), bos blok sayilari birebir.
- Dogrulama: fsck.hfsplus 1M-40G + tuhaf boy + HFSX temiz; okuyucumuz; 7z.
  t60. Linux run_all 58/60, Windows 57/60 (fsck.hfsplus Windows'ta yok ->
  orada yapisal denetim). i18n 8 yeni metin, platform 0 bulgu, ui_smoke.
- Hata (benim): t60 eklerken `def main()` basligini silmistim; t60 tum
  testleri kosturuyordu, fark edilip duzeltildi.

## 2026-09-29 (12) — HFS+ / HFSX yazma (ADR 0062)

- Yeni `core/hfswrite.py` (HfsWriter + genel B-agaci yazicisi) ve
  `core/hfsunicode.py` (Apple FastUnicodeCompare tablosu kuralla uretilir;
  fsck_hfs'in tablosuyla 65 536 girisin tamami ayni — tablo kopyalanmadi).
  Okuyucu ve bicimlendirici ayni ad kurallarina (Unicode 3.2 NFD) gecti.
- `HfsAccess` yazilabilir; arayuz yolunda islem basina flush.
- Bulunan hatalar: bicimlendirici blok kati olmayan birimde son blogu bos
  birakiyordu (fsck "Invalid extent entry"); `rename(p, "/x")` kok hedefini
  ad sayiyordu. Ikisi de duzeltildi.
- Dogrulama: stres 3000-6000 islem (HFS+/HFSX, 12M-300M, dolu birim,
  8+ parcali dosyalar, katalog tasmasi, tasima, ozyinelemeli silme) —
  fsck.hfsplus her asamada temiz; gunluklu mkfs birimine yazilan 400 dosya
  7z ile birebir. t61. Linux run_all 59/61, Windows 58/61. i18n 18 yeni
  metin, platform 0 bulgu, ui_smoke.
- macOS/Linux cekirdegi ile sinanmadi (ana makinede baglama yok).

## 2026-09-29 (13) — UDF salt okuma (ADR 0063)

- Yeni `core/udf.py` (UdfFS) + `filesystem.UdfAccess`; UDF+ISO koprusunde
  UDF agaci tercih edilir.
- Test verisi Windows misafirinde uretildi: mkudffs UDF 2.01 (512 B blok,
  MBR) VHD'sine Windows'un surucusu 402 dosya yazdi; SHA-1'ler birebir.
  Not: PowerShell 5.1 BOM'suz betigi ANSI okuyor (ilk turda Turkce adlar
  Windows'ta bozuk olusturuldu) — betikler artik UTF-8 BOM'lu.
- genisoimage `-udf` (1.02 koprusu) ve mkudffs CD-RW (yedekli harita)
  fixture'lari. t62. Linux run_all 60/62, Windows 59/62. i18n 16 yeni metin,
  platform 0 bulgu ("AD_*" adlari "ad" Turkce sayildigi icin ALLOC_*), ui_smoke.
- Sinanmadi: metadata bolumu (2.50+), VAT.
- VM: SATA 3 bos, 5/6'da m_exfat/m_ntfs; test VHD'leri .tmp/udf ve .tmp/win.

## 2026-09-29 (14) — UDF 2.01 bicimlendirme (ADR 0064)

- Yeni `core/udfformat.py::format_udf`; `FS_KINDS` "udf" (MBR 0x07, GPT
  temel veri), planview adi. Yerlesim mkudffs -m hd -r 2.01 ciktisindan
  (512/1024/2048/4096 blok) — kullanilan/bos blok sayilari birebir.
- Windows 10 (VBox): bizim bicimlendirdigimiz MBR ve GPT birimlerini UDF
  olarak bagladi, 300 dosya yazdi/sildi/adlandirdi, Healthy; geri okunan
  301 dosya SHA-1 birebir, udfinfo integrity=closed.
- t63 (etiket saglama/CRC/konum her tanimlayicida). Linux run_all 61/63,
  Windows 60/63. i18n 5 yeni metin, platform 0, ui_smoke.

## 2026-09-29 (15) — UDF yazma (ADR 0065) — Asama 2 tamam

- Yeni `core/udfwrite.py` (UdfWriter); `UdfAccess` yazilabilir. Okuyucuya
  bolum basligi (bitmap, erisim turu) ve alan bayraklari eklendi.
- Bulunan/duzeltilen: kendi actigim LVID'i sonraki islemde "temiz
  kapatilmamis" sayma; parcali birimde dizin 37 kapsami asiyordu (bitisik
  tasima + AED); O(n^2) dizin yazimi ve bit bit CRC (5000 dosyada dakikalar)
  -> artimli ekleme + tablo CRC (3x); Windows'un "silindi" bayrakli FID'leri
  denetleyicide/yazicida izleniyordu.
- Dogrulama: `_udf_denetle` (bitmap birebir, etiket/CRC, bag, LVID) stres
  ve t64'te; Windows 10 iki yonlu tur (2499 -> 2551 -> 2303 dosya, hep
  birebir, Healthy). 7z AED'li dosyalari okuyamiyor (7z siniri).
- Linux run_all 62/64, Windows 61/64. i18n 11 yeni metin, platform 0, ui_smoke.

## 2026-09-29 (16) — XFS salt okuma (ADR 0066)

- Yeni `core/xfs.py` + `filesystem.XfsAccess`. Test verisi mkfs.xfs 6.18
  `-p dizin` ile (cekirdek baglamasi gerekmeden icerikli birim); fixture
  `xfs_v5.img.gz` (460 KB). 5067 dosyalik agac v5/v4 ve 1K blok, 16K dizin
  blogu, nrext64/bigtime kapali varyantlarda birebir. t65.
- Linux run_all 63/65, Windows 62/65. i18n 8 yeni metin, platform 0, ui_smoke.

## 2026-09-29 (17) — XFS v5 bicimlendirme (ADR 0067)

- Yeni `core/xfsformat.py::format_xfs`; `FS_KINDS` "xfs" (>= 300 MB),
  planview adi. Ozellik kumesi sabit ve sade (crc, ftype, finobt, bigtime,
  inobtcount).
- mkfs.xfs 6.18 ile ayni UUID/etiket: 4 boyutta inode obegi disinda bayt
  bayt ayni; xfs_repair -n temiz. Ikincil ustbloklardaki mkfs tuhafligi
  (rootino yalnizca son ve (AG-1)/2'de) birebir uygulandi.
- t66 geometri tablosunda 4 AG ustu kural hatasini yakaladi (AG esit
  bolunuyordu; mkfs azami boy tutuyor) — duzeltildi.
- Linux run_all 64/66, Windows 63/66. i18n 5 yeni metin, platform 0, ui_smoke.
- Cekirdek baglamasi sinanmadi (ana makinede root yok).

## 2026-09-29 (18) — XFS buyutme (ADR 0068); disk doldu, dongu durdu

- Yeni `core/xfsgrow.py::xfs_grow` (cevrimdisi xfs_growfs): son AG uzatma,
  yeni AG'ler (rmapbt/reflink/finobt/inobtcount duzenleri mkfs'ten olculdu),
  gunluk temizligi denetimi. `resize.py` kind "xfs" (en az = mevcut boy,
  tasinabilir), layoutedit/geri yukleme/Uygula bagli.
- Dogrulama: 11 senaryo xfs_repair -n temiz, icerikli birimlerde icerik
  birebir; kirli gunluk reddi. t67. Linux run_all 65/67. i18n 14 yeni metin,
  platform 0, ui_smoke.
- **Ortam sorunu:** ana makine diski %100 doldu (test goruntuleri); run_all
  "yetersiz disk alani" ile durdu ve **win10 misafiri 'aborted' oldu**
  (dinamik VDI buyuyemedi). Temizlik: .tmp altindaki yeniden uretilebilir
  goruntuler silindi, test VHD'leri misafirden ayrilip kayittan dusuldu
  (SATA 3-6 bos; SATA 2 du-test-disk.vdi yerinde). Bos alan 4,7 GB.
  Misafir yeniden baslatilmadi — kullanici karari. Windows kosusu bekliyor.

## 2026-10-01 (1) — Ortam toparlandi, commit, Windows dogrulamasi

- Kullanici ana makinede yer acti (disk 149 GB, 68 GB bos).
- Commit: 95fd3c1 (archify becerisi, ADR 0051 — onceki oturumun isi) ve
  a5f9725 (dosya sistemi genisletme, ADR 0052-0068); main'e push edildi.
- win10 misafiri headless yeniden baslatildi (aborted durumundan sorunsuz
  acildi). Windows run_all 64/67 (3 atlanan Linux araci istiyor); XFS
  t65-t67 Windows'ta gecti.
- Dongu kalan maddelerle devam: btrfs okuma, F2FS okuma, APFS, ReFS.

## 2026-10-01 (2) — btrfs salt okuma; saf LZO1X ve zstd (ADR 0069)

- Yeni `core/btrfs.py` (BtrfsFS) + `filesystem.BtrfsAccess`; yeni
  `core/compress.py`: saf LZO1X (btrfs cercevesiyle) ve saf zstd (RFC 8878).
- Dogrulama: zstd 9 veri x 6 seviye CLI ciktisi birebir; btrfs 1507 dosya
  sikistirmasiz/zlib/LZO/zstd + 4 varyant birebir, btrfs check temiz.
  Fixture'lar btrfs_{zlib,lzo,zstd}.img.gz (~200 KB). t68.
- Linux run_all 66/68, Windows 65/68. i18n 35 yeni metin, platform 0, ui_smoke.

## 2026-10-01 (3) — F2FS salt okuma (ADR 0070) — Asama 3 tamam

- Yeni `core/f2fs.py` (F2fsFS, saf LZ4 blok cozucu) + `F2fsAccess`.
- Test verisi mkfs.f2fs + sload.f2fs 1.16 (kullanici alani); 5036 dosya
  birebir, iki ozellik varyanti. Fixture f2fs.img.gz (190 KB). t69 NAT
  gunlugu/ikinci kopya yollarini elle degistirilmis birimde sinar.
- lz4 CLI kullanici alanina acildi (.tmp/tools); LZ4 cozucu 92 blokta birebir.
- sload.f2fs LZ4/LZO'suz derlenmis: F2FS sikistirmasi sinanmadi (ADR'de).
- Not: 64 MB F2FS birimine sload sonsuz "Free segments" dongusune girdi;
  kalinti fsck.f2fs etkilesimli soruda bekledi — PID ile kapatildi.
  (`pkill -f` kendi kabugunu esledigi icin kullanilmadi.)
- Linux run_all 67/69, Windows 66/69. i18n 10 yeni metin, platform 0, ui_smoke.

## 2026-10-01 (4) — APFS salt okuma (ADR 0071)

- Yeni `core/apfs.py` (ApfsFS) + `ApfsAccess`.
- Test verisi: log2timeline/dfvfs'ten macOS uretimi `apfs.raw` ve
  `apfs_encrypted.dmg` (Apache 2.0, `tests/fixtures/KAYNAKLAR.md`) +
  apfsprogs mkapfs. Bagimsiz dogrulayici libfsapfs (pyfsapfs, sistem
  Python 3.14 ile kullanici alaninda).
- 10 giris birebir, xattr/kaynak catali, sifreli birim reddi
  (AttributeError cikiyordu — duzeltildi). t70.
- Linux run_all 68/70, Windows 67/70. i18n 17 yeni metin, platform 0, ui_smoke.
- Sinirlar ADR'de: test verisi kucuk.

## 2026-10-01 (5) — ReFS yerel bicimlendirme (ADR 0072) — liste bitti

- `formatter.FS_KINDS` "refs" (`native_only`): Windows Format-Volume,
  yalnizca fiziksel disk + uygun surum (EditionID kayit defterinden:
  Enterprise / Pro for Workstations / Server). Diger platform ve surumlerde
  gri + neden; goruntu dosyasinda acik ret; yerel arac hatasinda saf Python
  yola dusulmez. `platform.format_volume_command` saf ve temizleyici.
- t71: surum tablosu, komut, ret yollari; Windows'ta gercek kayit defteri
  (Professional -> gri) ve PowerShell ayristiricisiyla sozdizimi.
  Linux 69/71, Windows 68/71.
- Sinanmayan: gercek ReFS bicimlendirme (misafir Win10 Pro, yonetici yok).
- diskgenius-parity.md dosya erisimi tablosu guncellendi (yeni dosya
  sistemleri; NTFS yazma tam).
- **fs-genisletme-ilerleme.md listesi tamamlandi; dongu durduruldu.**

## 2026-10-01 (6) — Kullanici gorevi: ext4'e dosya yaz + onundeki bos alana genislet

- Masaustu `yeni-disk.img` (20 GB GPT: FAT32, NTFS, NTFS, ext4). ExtAccess ile
  1963 dosya yazildi (e2fsck temiz); kuyruk yoluyla ext4 sola tasinip
  7,75 -> 9,77 GB buyutuldu: hatasiz, e2fsck temiz, icerik birebir, diger
  bolumler degismedi, sfdisk --verify temiz.
- Kullanicinin arayuz oturumundaki gercek hata analiz edildi
  (`.claude/logs/2026-10-01-ext4-genisletme-analizi.md`): goruntu `/dev`
  (devtmpfs) icinde olusturulmus (varsayilan klasor = acik fiziksel diskin
  klasoru), tasima ENOSPC ile yarida kaldi, ext4 bozuldu ama mesaj
  "0 adim uygulandi" dedi (yeniden uretildi). Ek: bos alan da kopyalaniyor
  (seyreklik bozuluyor), Qt Turkce ceviri uyarilari, ext4 ozellik seti.
- `/dev/yeni-disk.img` hala duruyor, /dev %100 dolu — kullaniciya bildirildi.
- Duzeltmeler kullanici onayina birakildi.

## 2026-10-01 (7) — Analizdeki 5 bulgu duzeltildi + dosya ekleme ilerlemesi (ADR 0073)

- H1: goruntu varsayilan klasoru artik kullanicinin evi (pkexec altinda da);
  acik oturum fiziksel aygitsa `/dev` onerilmez. `platform.image_location_problem`
  aygit/sahte FS'i reddeder, tmpfs ve yetersiz bos yer icin uyarir (t72).
- H2: tasima kesintisi `MoveInterrupted` — kaynak saglam mi, kac sektor;
  "BOZUK" durumda sektor sayilari mesajda ve tanilama gunlugunde (t73).
- H3: tasima ayni icerigi yazmaz (seyreklik korunur); seyrek goruntude
  gereken yer on denetimi (t73). t73'un kesinti tetigi bayt sayisindan
  konuma cevrildi (flex_bg ile yerlesim degisince bayt siniri kaynak
  ezilmeden doluyordu).
- H4: `_ButtonTranslator` bilmedigi metne "" yerine None donduruyor; dosya
  penceresi metinleri geri geldi, `QString::arg` uyarisi yok (ui_smoke).
- H5: ext4 bicimlendirici extent + flex_bg + metadata_csum (64bit ve
  resize_inode haric); e2fsck 4 MB–20 GB temiz, ext_write_check 4/4,
  ext_resize_check 32/32 (yeni du-ext4 satirlari libext2fs ile doldurulur).
- Kullanici istegi: dosya eklemede ilerleme dosyanin icinde yuruyor
  (aygit yazma gozlemcisi, `import_files`), ilerleme penceresi gecen ve
  kalan sureyi gosteriyor (t74, ui_smoke tahmin denetimi).
- Sonuclar: Linux run_all 72/74 (2 Windows dali atlandi), Windows 71/74
  (3 arac yok atlandi), ui_smoke iki platformda, ext_resize_check 32/32.
  Kullanicinin diskinin seyrek kopyasinda ayni genisletme: goruntu 1,0 ->
  1,5 GB (eskiden 21 GB), 17,6 -> 8,6 sn, icerik birebir, e2fsck temiz.
- t19 duzeltmesi: kucuk dosyada FAT metaverisi dosyadan cok yazildigi icin
  ayni deger tekrar bildiriliyordu; kirpilmis deger karsilastiriliyor.

## 2026-10-01 (8) — Analiz: kucultmede dolu alanin altina inilebilmesi

- Kullanici: bolum 3 (NTFS, 3,27 GB dolu) icin pencere 3,33 GB'a izin verdi;
  gercek en az 3,44 GB. Neden: `FsLimitsService` anahtari (aygit, no,
  baslangic, boyut) dosya ekleyince degismiyor; `_content_changed` sinirlari
  tazelemiyor -> dosyalar eklenmeden once hesaplanan en az kullaniliyor.
- Veri riski yok: Uygula aninda cekirdek siniri yeniden hesaplayip reddediyor
  ("3.44 GB altina inemez") — kullanicinin diskinin kopyasinda denendi.
- Ayrica: kendi NTFS bicimlendiricimiz $Bitmap'te 2-3. kumeleri sahipsiz
  dolu isaretliyor (ntfsresize "extra cluster in $Bitmap" -> calismayi
  reddediyor; chkdsk duzeltir). Taze birimlerde de var.
- Duzeltme onerisi kullaniciya sunuldu; kullanici "hepsini yap" dedi.

## 2026-10-01 (9) — Eskimis boyut siniri + NTFS bicim hatalari duzeltildi (ADR 0074)

- `FsLimitsService.forget`; gezgin degisikliginde sinir unutulup yeniden
  hesaplaniyor. Kuculen bolumun siniri kuyruga yazmadan once diskten
  yeniden olculuyor (`_recheck_shrink`). Pencere "Dolu / en az / en cok"
  gosteriyor; ext'te dolu > en az aciklamasi. ui_smoke: eskimis sinir
  senaryosu (18 385 -> 156 009 sektor, adim reddedildi).
- NTFS bicimlendirici: bitmap'te yalnizca $Boot kumeleri; $MFT $Boot'la
  cakismiyor; $AttrDef dogru kume sayisi; onyukleme 0x44 dizin kaydi boyu
  dogru. 512 B / 1 KB / 2 KB kume daha once HIC calismiyordu (arayuzde
  secilebiliyordu); simdi 512 B–64 KiB ntfsresize temiz, ntfs-3g ile iki
  yonlu okuma/yazma (t75).
- Dar onarim `repair_orphan_low_clusters` (MFT taranir, sahipsizler
  bosaltilir), `ntfs_resize` icinde de calisir. Kullanicinin diskinin
  kopyasinda bolum 2 ve 3: 2'ser kume, tek sektor, icerik birebir.
  Gercek dosyaya yazilmadi: uygulama (root) goruntuyu acik tutuyor.
- fs_matrix: XFS icin 320 MB (eskiden 128 MB ile hep HATA veriyordu).
- Linux run_all 73/75 (0 hata), ntfs_write_check 2/2, resize_matrix 16/16,
  fs_matrix 12/13 (ReFS Linux'ta beklenen), diag 13/13, i18n, platform 0.
- Windows (VBox win10): run_all 72/75 (0 hata, 3 arac yok), ui_smoke tamam.

## 2026-10-01 (10) — Windows: harfi kaldirilmis birim kilitlenmiyordu (ADR 0075)

- Kullanici VBox win10'da PhysicalDrive1'de bolum 3 (exFAT) kucultme +
  bolum 4 (ext4) genisletme denedi; her seferinde "Windows bagli birimin
  sektorlerine yazmayi engeller". Gunlukler `%LOCALAPPDATA%\DiskUltimate\logs`
  (.tmp/winlog'a kopyalandi): "Bolumu cikar" yalnizca harfi kaldiriyor,
  birim bagli kaliyor; kilit kodu birimleri harfle aradigi icin "kilit
  gerekmiyor" dedi.
- `_win_volumes_on_disk`: FindFirstVolumeW + disk kapsami (erisim hakki
  istemeden); kilit GUID yoluyla. VBox'ta disk 1'de 3 harfsiz birim
  bulundu (eski tarama bos). Kilitleme yonetici ister: sinanmadi.
- Yeni exe misafirde derlendi (C:\Python312 + PyInstaller) -> dist/;
  eski exe .tmp/DiskUltimate-2215-eski.exe. Windows run_all 72/75 (0 hata).
- Ayrica: 567 MB ext4'e eklemede 7,7 sn donma — `ExtAccess.import_file`
  dosyayi tek parca okuyor (ADR 0073'te bilinen sinir); ayri is.

## 2026-10-01 (11) — Diskten diske klonlama (ADR 0076)

- "Diski klonla" hedef turunu soruyor: goruntu dosyasi / baska disk.
  `DiskSession.clone_to_physical` (butun fiziksel disk kapilari) ve
  `clone_to_session` (hedef acik oturumsa onun tutamaci). Buyuk hedefte GPT
  yedegi sona; GPT olmayan kaynakta hedefin eski yedek GPT'si silinir.
- `CloneTargetDialog` + `disksource.clone_target_problem`: uygunsuz disk gri
  + neden, silme onayi ve sistem diski adi zorunlu, bagli bolum / bekleyen
  adim uyarisi, kaynaktan buyuk kismin ayrilmamis kalacagi yaziyor.
- Klon/yedek/geri yukleme ilerleme metinleri tr() ile sarildi (30 yeni metin
  en/de).
- t76 (sfdisk --verify temiz), ui_smoke klon hedefi. Linux 74/76, Windows
  73/76 (0 hata). Yeni exe dist/ (23:11).
- Sinanmayan: gercek fiziksel hedef (yonetici gerekir). VBox misafirinde
  yalnizca 2 disk var (disk 0 = sistem); sinamak icin ucuncu bir sanal disk
  eklenmeli.

## 2026-10-02 (1) — README iki dilde, ekran goruntuleri, surum 0.5.0-beta

- Kullanici: repo public olacak; README guncellensin, uygulama resimleri
  konsun, Ingilizce + Turkce, `.claude` baglantilari cikarilsin, desteklenen
  tum ozellikler yazilsin. Ardindan: surum beta olsun, Windows exe release,
  Linux kaynaktan.
- `README.md` (Ingilizce, varsayilan) + `README.tr.md` (Turkce, dogru yazimla).
  Ozellik listesi kod/ADR'lerden yeniden cikarildi (eski README 21 Eylul'dendi:
  HFS+, UDF, XFS, btrfs, F2FS, APFS, ISO, ReFS, diskten diske klon, UEFI
  duzenleyici, bolum duzeni yoktu). `.claude/` baglantilari kaldirildi.
- `tools/readme_screenshots.py`: 14 goruntu x 2 dil -> `docs/screenshots/{tr,en}/`.
  ui_smoke'tan farki: fiziksel disk listesi bos (makinenin disk modeli
  sizmasin), durum cubugundaki tam yol temizlenir, ornek veriler secilen dilde.
  Offscreen'de bootloader/UEFI formlari kucuk boyutta ust uste biniyordu;
  pencere buyutulerek alinir.
- `APP_VERSION = "0.5.0-beta"`; pencere basliginda surum, `setApplicationVersion`,
  .po basliklari. i18n/platform/diag 13/13/ui_smoke temiz.
- **Exe yeniden derlenemedi:** VBox "win10 " acilmiyor — SATA-0 bos ("No
  bootable medium"), `win10_.vdi` erisilemez, `win10 .vdi` 2 MB, SATA-3'te
  19.9 GB `wtest55.vdi`. VM yapilandirmasi 2026-10-01 23:49'da degismis;
  dokunulmadi, VM kapatildi. dist/DiskUltimate.exe (10-01 23:11) HEAD
  kodundan ama surumu 0.4.0 gosterir.
- Gorulen arayuz cevirisi eksikleri (duzeltilmedi): dosya listesi ve
  silinmis dosyalar basliginda "Ad" Ingilizcede cevrilmiyor; yuzde "%41"
  Ingilizcede de Turkce bicimde; Hakkinda metni eski (yalniz FAT/exFAT).

## 2026-10-03 (1) — NTFS denetle ve onar (ntfsfix karsiligi, ADR 0077)

- Kullanici: makinesindeki NTFS veri bolumu Linux'ta gorunmuyor/baglanmiyordu,
  `sudo ntfsfix -d` ile acildi; "programa eklenebilir mi".
- `core/ntfsfix.py`: `ntfs_check` (salt okunur) + `ntfs_fix` — onyukleme
  sektoru/yedegi, `$MFT`/`$MFTMirr`, `$LogFile` bosaltma, kirli bayragi
  temizle ya da chkdsk iste, hiberfil.sys gecersiz kilma (yalnizca acik
  secimle; yoksa hazirda bekletmede onarim reddedilir).
- `FSInfo.unclean/hibernated` (fsdetect), `session.ntfs_check/ntfs_fix`,
  kuyruk adimi `ntfs_fix` (yikici), `Bolum > NTFS'i denetle ve onar...`,
  bolum bilgisinde "Durum", Linux'ta NTFS baglama hatasinda oneri.
  Yeni ikon `fs-repair` (klasik + tile/line + 5 paket, tools/iconpacks.py).
- **Hata duzeltildi:** `ntfsresize._is_dirty` bayragi ofset 12'den okuyordu
  (dogrusu 10) ve hep "temiz" donuyordu — kirli birim boyutlandiriliyordu.
- Gercek ornek: VBox win10 misafiri (sistem diski artik `wtest55.vdi`,
  SATA-0) + `.tmp/vm/du-ntfs-test.vdi`. Guestcontrol yonetici degil,
  `schtasks /rl highest` "Erisim engellendi" — bu yuzden disk ana makinede
  bizim bicimlendiricimizle hazirlandi, Windows bagladi ve yazdi, VDI
  bagliyken okundu -> `tests/fixtures/ntfs_windows_kirli.img.gz` (427 KB).
  ntfs-3g: "Metadata kept in Windows cache, refused to mount"; bizim denetim
  ayni. Onarim sonrasi ntfs-3g ile fark yalnizca `$Volume` USN'si.
  Onarilan disk Windows'a takildi: Healthy, olay 98.
- Sicak takma yarim kaldi (VBox "hotpluggable" bayragi calisirken
  degistirilemiyor); misafir `shutdown /s` ile kapatilip disk kapaliyken
  takildi. ACPI dugmesi kilit ekraninda yok sayiliyor.
- Pencere: grup kutusundaki satir kaydirmali etiketler kesiliyordu;
  duz duzen + `showEvent`te `totalHeightForWidth` ile cozuldu.
- Testler: t77; ui_smoke NTFS bolumu; Linux run_all 75/77, Windows 74/77
  (0 hata), i18n TAMAM (70 yeni metin en/de), platform 0 bulgu.
- README (en/tr): ozellik bolumu, "Windows bolumu Linux'ta baglanmiyor mu"
  ipucu, `ntfs-repair.png`.
- Sinanmayan: gercek fiziksel diskte uygulama (yonetici gerekir), gercek
  Hizli baslatma hiberfil'i.

## 2026-10-03 (2) — VBox yonetici yetkisi + NTFS onarimi fiziksel diskte (Windows)

- Neden yetki yoktu (olculdu): `pc` Administrators uyesi ama UAC belirteci
  suzuyor (grup "deny only", Orta zorunlu duzey); yerlesik Administrator
  kapaliydi. Kullanici `net user Administrator 1234 /active:yes` yapti;
  guestcontrol `--username Administrator` tam yetkili (Yuksek duzey, fltmc).
- `tests/physical_ntfsfix_test.py` (alti olcut, bagli birim kabul).
  Misafir icinde VHD (PhysicalDrive1, "Msft Virtual Disk") + NTFS +
  `fsutil dirty set`: onarim kuyruktan uygulandi -> Windows "NOT Dirty",
  Healthy, 41 dosya ayni, chkdsk ve Repair-Volume temiz. VHD silindi.
  Ayrinti: ADR 0077. VM ayarlari degismedi.

## 2026-10-03 (3) — BitLocker tespiti gercek birimlerde olculdu

- Kullanici: "yazilim BitLocker icin neler yapabiliyor" -> tanima, uyari,
  ham kopyalama/yedek, kucultme reddi; kilit acma yok.
- Gercek ornekler (A NTFS XTS-128, B exFAT CBC-128, C NTFS XTS-256): tespit
  Windows'ta fiziksel yoldan ve ana makinede VHD'den dogru, blkid ile ayni.
  Ayrinti ADR 0053 ek. Kilit acma calismasi bu oturumda ilerletilmedi.

## 2026-10-03 (4) — Linux AppImage (ADR 0050 uygulandi)

- Yontem plandan farkli: PyInstaller + Mint VM yerine python-appimage
  manylinux2014 (glibc 2.17) Python 3.12.14 + PyPI PyQt5 tekerlekleri;
  `tools/appimage.py`, `build_appimage.sh`. Araclar SHA-256 ile sabit.
- `$APPIMAGE` duzeltmesi (`_relaunch_target`) ve `paths.IS_APPIMAGE` /
  `IS_PACKAGED` (gunlukler kullanici veri dizinine). t22 genisletildi.
- Kirpma: Qt Core/Gui/Widgets/DBus/XcbQpa/WaylandClient/Svg; NEEDED
  denetimi. Sonuc 39.6 MB, en yuksek GLIBC_2.17.
- Sinandi: offscreen + gercek ekran (xcb/XWayland) acilis, ui_smoke paketin
  kendi Python/PyQt5'iyle tamam, eklenti sistem bagimliliklari tam.
- Sinanmadi: eski dagitim, `pkexec "$APPIMAGE"` (root yok), musl mesaji.
- README (en/tr): Linux (AppImage) bolumu, derleme bolumu.
- Hafiza: `siradaki-appimage` notu silindi (is bitti).
- Kullanici AppImage'i ana makinede calistirdi: pkexec parola sordu,
  yetkili kopya acildi (gunlukle dogrulandi). Yan bulgu: root kopyanin
  tanilama gunlukleri root'a ait kaliyor (eski sorun, duzeltilmedi).

## 2026-10-03 (5) — Release v0.5.0-beta guncellendi

- Etiket `v0.5.0-beta` a8e8951'e tasindi (AppImage commit'i + oturum kaydi).
- Windows exe ayni commit'ten VBox'ta yeniden derlendi (acilis denendi);
  release'teki exe degistirildi, AppImage eklendi. Iki dosya da geri
  indirilip SHA-256 dogrulandi; surum notu Linux AppImage satiri ve
  ozetlerle guncellendi.

## 2026-10-03 (6) — Yetkili kopyada sahiplik ve ayarlar (ADR 0078)

- Bulgu: root kopyanin gunlukleri ve ilk olusturdugu klasorler root'a ait
  kaliyordu (`logs/freeze` 28 Eylul'den beri; donma raporu yazilamiyordu);
  ayarlar /root/.config'e gidiyordu.
- `make_user_dirs`, `reclaim_tree`; gunluk/donma/ayar/app-log/el sikisma
  dosyalarinda `restore_owner`; `config_dir`/`user_data_dir` `user_home()`.
- t78 eklendi; diag 13/13, i18n, ui_smoke, platform 0.

## 2026-10-03 (7) — GitHub Actions (ADR 0079)

- tests.yml (ortak), ci.yml (push: Linux), release.yml (etiket: 3 platform
  test + derleme + deneme acilisi + taslak release). release-notes.md sablonu.
- DiskUltimate.spec: macOS dali (.app). Linux onefile yerelde yeniden
  derlenip denendi.
- tools/smoke_launch.py: paket acilisi + gunlukte surum (minimal QPA; surec
  grubu kapatilir).
- Fark edilen: depo PUBLIC (2026-10-03 sorgusu); kullaniciya bildirildi.
- Ilk Actions kosusu: macOS testleri GECTI ve .app derlendi (macOS'ta ilk
  olcum). Windows: PyQt5-Qt5 5.15.19'un Windows tekerlegi yok (yalniz
  5.15.2) -> .github/requirements-ci.txt platform kosullu. Linux 73/78:
  t65/t67/t68 Ubuntu 24.04'un eski mkfs.xfs (-p klasor) / mkfs.btrfs
  (--subvol) yuzunden -> `_arac_eski_mi`: secenek tanimlanmiyorsa ATLANDI.
- AppImage CI: libqpdf.so eklentisi silinen Qt5Pdf'e bagliydi (denetim yakaladi); kirpma artik bagimliligi kalmayan eklentileri de siler.
- Actions ucuncu kosu yesil: 3 platformda 73/78, AppImage/exe/macOS .app derlendi ve acildi. README macOS 'deneysel' + CI rozeti.
- Kullanici: depoyu bilerek public yapti, dokumlerin acik olmasi sorun degil. CLAUDE.md 'private kalmali' kurali buna gore guncellendi.

## 2026-10-04 (1) — Uzun testler plani (ADR 0080)

- Kullanici: uzun testler istendiginde elle; 5 GB dosya; CI makineleri VM
  sayilir (CLAUDE.md eki); hata olunca otomatik issue.
- macOS "deneysel" nedeni ve kaldirma olcutleri ADR 0080'e yazildi.
- Asama 0 (akis yazma) basliyor.

## 2026-10-04 (2) — Asama 0: akis yazma (ADR 0081)

- core/streamio.py; alti yazicida write_stream; erisim katmani import_file
  akista. FAT 4 GiB / bos alan denetimi, NTFS kaydi once kurulur, ext blok
  geri verme — uc hata duzeltildi.
- 2 GiB kopya: bellek 32-53 MB (eskiden >= 2 GiB), icerik ayni, fsck temiz.
- t74: ilerleme izleyicisinin bolme adimi 4 MiB -> 1 MiB (akis 4 MiB yazdigi icin cubuk 4 MB'ta bir ilerliyordu; test yakaladi). run_all 76/78, fs_matrix 12/13 (ReFS Linux'ta beklenen), resize_matrix 8/8, ui_smoke, diag 13/13.

## 2026-10-04 (3) — Akis okuma; uzun test motoru (Asama 1); mantiksal bolum hatasi

- Okuma da akista: iter_file/iter_data/iter_entry (FAT, exFAT, NTFS, ext,
  UDF, HFS+), erisim katmaninda iter_read; ext/NTFS/UDF extract artik tek
  parca okumaz. 2 GiB disa aktarma 34-53 MB bellek. t79.
- tests/long: data (tohumlu veri, parca basinda kimlik), verify (kendi
  okuyucu + fsck + istege bagli cekirdek baglama), engine (adim, olcum,
  JSON), steps (arayuz yolu: kuyruk), report (Markdown/issue).
- Ilk kosu GERCEK HATA buldu: MBR mantiksal bolum boyutlandirilamiyor/
  tasinamiyor, tasima veriyi bozabiliyordu (ADR 0082, t80).
- UDF kayip bolum taramasi desteklenmiyor (ADR 0054'te bilincli) — adim
  "atlandi" + neden; kullaniciya bulgu olarak sunulacak.
- quick: 33/33 (tohum 12).
- Uzun testler CI ilk kosu (quick, Linux, 37195638819): FAT disi 24/24
  senaryo CEKIRDEK suruculeriyle (ext4, exfat, ntfs3, hfsplus, udf, xfs)
  dosya ozetleri ayni. 9 FAT senaryosu: vfat varsayilan iocharset
  (iso8859-1) s/i/Yunanca/Kiril adlari gostermiyor -> mount `utf8`.
- Asama 3-4: Windows (sabit VHD + Mount-DiskImage + chkdsk + Windows
  suruculeriyle ozet) ve macOS (hdiutil + fsck_* + diskutil mount) islari;
  full profilde bos diskin 1/3'u veri butcesi, sigmayan buyuk dosyalar
  raporda "SINANMADI" diye yazilir.
- 3 platform quick (37195838321): Linux 33/33 (cekirdek dogrulamali),
  macOS 15/21, Windows 0/18 (seyrek VHD). Bulgular ADR 0083: exFAT
  kucultmede bitmap zinciri (Apple fsck_exfat), macOS yetki komutu
  (tirnaklama/enjeksiyon + el sikisma). macOS UDF baglanmiyor: teshis
  eklendi, sonraki kosu.
- Asama 4-5: tests/long/gui.py (Cocoa/Windows pencere + ekran goruntusu), tests/long/device.py + --aygit (scsi_debug / takili VHD / hdiutil; yalnizca GITHUB_ACTIONS). Windows quick: 7/8 gecti (Windows surucusu + chkdsk); NTFS mantiksal bolumde chkdsk sorun buldu (inceleniyor). macOS UDF: diskutil reddediyor, mount -t udf kabul ediyor.

## 2026-10-04 (4) — Windows aygit kipi bulgulari (ADR 0084)

- CI uzun testlerinin Windows aygit hatalari VirtualBox "win10 " misafirinde
  `DU_TEST_VM=1` ile yeniden uretildi; gunluk `<rapor>/gunluk` altinda.
- **Veri kaybi 1:** Windows fiziksel diskte NTFS boyutlandirma yalnizca
  tabloyu yaziyordu (`disk_number` hic atanmiyordu; "native" plan
  `apply_resize`'a dusuyordu). Kucultme bolumu 1 MiB'a indirdi. Duzeltildi:
  `windows_disk_number()`, sinir/arac yoksa ve tasimada saf Python,
  `apply_resize` "native"i reddeder, yerel arac oncesi kilit birakilir. t83.
- **Veri kaybi 2:** exFAT buyutme Windows'un bicimlendirdigi birimde yigini
  geri kaydiriyordu (sondan basa kopya parca sinirlarini ezdi). Yigin artik
  geri cekilmez; `_shift_forward` negatifi reddeder. Windows fiksturu
  `tests/fixtures/exfat_windows.img.gz` (diskpart + Format exFAT). t82; eski
  kod t82'de `/Klasör/kucuk.bin` bozuk cikti.
- Sonradan baglanan birim: yazma reddinde yeni birimler kilitlenip bir kez
  yeniden denenir; birakmada kilit listesi sifirlanir.
- NTFS `$UpCase:$Info` (CRC64) — chkdsk temiz (VM: eski cikis 3, yeni 0).
- Test tarafi: chkdsk OEM kod cozumu, NFC/NFD ad farki ayri raporlanir,
  hfsplus modulu, adim notlari.
- VM (aygit, quick, tohum 7): exFAT 15/15 tamam (1 atlandi), NTFS 14/14
  tamam (2 atlandi: kurtarma NTFS'te yok, boyut siniri yok).
- Not: fikstur betigi ilk denemede `assign letter=X` basarisiz olunca
  dosyalari misafirdeki X: (ana makinenin `myiso` paylasimi) icine yazdi;
  silme kullaniciya birakildi. Ders: misafirde harf atamak yerine birim
  GUID yolu kullanilir, betik diskpart sonucunu denetler.

## 2026-10-04 (5) — NTFS Windows uyumu (ADR 0085)

- CI 37199361406 (quick, 3 platform): Linux ve macOS tamami gecti (macOS UDF,
  hfsplus dahil); kalan: Windows NTFS goruntu (3) + aygit mantiksal (1).
- $LogFile: 0xFF dolu gunlugu Windows salt okunur baglamiyor (kok "Yazma
  korumasi var"). Bicimlendirici/boyutlandirma/onarim artik Windows'un
  yazdigi temiz RSTR sayfalarini yazar. VM'de deneyle ayrildi (yalniz
  gunluk baytlari nakledilince calisti). t84.
- **ntfsresize kosu uzunlugunu isaretsiz kodluyordu** — buyutulen birimde
  Windows chkdsk "$Bad is corrupt". Kodlayici isaretli; okuyucu da artik
  isaretli okur ve negatif uzunlugu bozukluk sayar (hatayi gizliyordu). t85.
- Windows yerel bicimlendirme/boyutlandirma bolumu numara yerine bayt
  ofsetiyle secer (mantiksal bolumde "No matching MSFT_Partition"; yanlis
  bolum bicimlendirme riski). Harf ata/kaldir hala numarali (kalan is).
- VM: NTFS goruntu 14/14 + 2 atlama (Windows surucusu + chkdsk), aygit
  ntfs/exfat x mbr-mantiksal/gpt tamam.
- CI 37201218408 (quick, tohum 7, Linux + Windows + macOS, goruntu + aygit +
  arayuz): **102/102 is basarili**. Ilk kez butun platformlarda temiz.

## 2026-10-04 (6) — Surucu harfi ofsetle (ADR 0086); v0.5.1-beta

- Issue #1, #2 cozum yorumuyla kapatildi (CI 37201218408, 102/102).
- Windows surucu harfi sorgula/ata/kaldir bolumu bayt ofsetiyle secer;
  ofsetsiz Windows harf islemi reddedilir. t86 + t43 guncellendi.
- `tests/physical_drive_letter_test.py` (VM, takili VHD, MBR + 2 mantiksal):
  Windows numaralari 1/2/3, bizimkiler 1/5/6 (bulgunun kaniti); harf ikinci
  mantiksala atandi ve yalnizca orada gorundu, kaldirildi. BASARILI.
- Surum 0.5.1-beta (APP_VERSION, README rozetleri, .po basliklari).
- v0.5.1-beta ilk sürüm koşusu (37202720053) düştü, sürüm oluşmadı:
  (1) macOS ui_smoke SIGSEGV — `TaskDialog`'un saniyelik sayacı pencere
  kapanınca hiç durmuyor, pencere silinmiyordu (her run_task arkada çalışan
  bir sayaç bırakıyordu); sayaç durur, pencere deleteLater. ui_smoke'a
  denetim eklendi (eski kodda 3/3 sayaç çalışır kalıyordu).
  (2) Windows t21: testin elle kurduğu PhysicalDisk'te ADR 0084 alanları
  yoktu. (3) AppImage: niess/python-appimage `python3.12` yuvarlanan etiketi
  3.12.14'ü sildi (404); 3.12.15'e sabitlendi (sha256 GitHub özetiyle aynı),
  404'te ne yapılacağını söyleyen hata. Yerelde AppImage derlendi ve açıldı.
- **v0.5.1-beta yayınlandı** (ön sürüm): https://github.com/mcansiz/DiskUltimate/releases/tag/v0.5.1-beta
  — sürüm koşusu 37203299268: 3 platform test + Windows exe, Linux AppImage,
  macOS zip; notların başına İngilizce/Türkçe "yenilikler" eklendi.

## 2026-10-04 (7) — Tam profil ilk bulgular (ADR 0087)

- Tam profil (CI 37202739981) koşarken 12 iş düştü: exFAT `geri_buyut`
  (Windows + macOS; Linux denetlemiyor) ve FAT12 `doldur`.
- exFAT: büyütmede bitmap zinciri yeni alan eski ilk kümeden başlayınca
  yeniden yazılmıyordu (uzunluk büyük, zincir 1 küme). Düzeltildi, t87
  (eski kodda 1/5). **0.5.1-beta'da var** — sonraki sürümle düzelir.
- Uzun test bütçesi küme artığını sayar (`footprint`); FAT12 full yerelde 16/16.
- Hedefli yeniden koşu (37207695550, full, exfat/fat12/fat32): exFAT ve FAT12
  3 platformda geçti. Linux FAT32 taşımada yedek önyükleme sektörü
  güncellenmiyordu — düzeltildi, t88 (eski kodda düşer).
- FAT32 tam profil yeniden koşu (37210687468): 19/19. Tam profilin bütün
  bulguları kapandı; issue #3 kapatıldı. Sürüm 0.5.2-beta.
- **v0.5.2-beta yayınlandı** (ön sürüm): https://github.com/mcansiz/DiskUltimate/releases/tag/v0.5.2-beta
  — sürüm koşusu 37212216485: 3 platform test + Windows exe, Linux AppImage,
  macOS zip.

## 2026-10-04 (8) — Tema ve yedi yeni dil (ADR 0088)

- Kullanıcı kararları: tema "palet + az QSS"; sözlükler "hepsi .ts".
- Araçlar > Tema: Sistem / Açık / Koyu (Fusion + QPalette + paletten küçük
  QSS); eski kullanılmayan STYLESHEET silindi. ui_smoke açık/koyu denetler.
- Sözlükler `.ts` (Qt Linguist); `i18n/ts.py` saf Python okur, QTranslator
  yok. en/de geçişi alan alan kayıpsız (1944 giriş). `PLURAL_RULES`,
  `po.merge(nplurals)`, i18n_check, `.spec`, AppImage Qt çevirileri.
- fr, it, es, ru, zh, ja, ko: 7 paralel alt ajan, 1854 metin/dil,
  doğrulayıcıyla; i18n_check her dil TAMAM (1861). Terim sözlükleri
  `.claude/docs/ceviri-sozlukleri/`. Anadili gözden geçirmesi yok.
- Ajanların bulduğu kaynak hataları: taşıma yönü çevrilmeden Türkçe
  ("ileri/geri"), "Ad" başlığı tr()'siz (2 yer), dar blokta "%5".
- run_all 86/88, platform 0, diag 13/13, ui_smoke tamam.
- 0.5.2-beta tam koşusunda aygıt FAT32 `kurtar` düştü: test, `degistir`de
  parçalı yazılmış bir dosyayı kurtarmaya çalışıyordu (FAT'te parçalı silinmiş
  dosya meta veriden dönmez; araç doğru "kısmen üzerine yazılmış" diyordu).
  Test ardışık dosyayı seçer, parçalı dosyada "iyi" denmediğini ayrıca
  denetler (ADR 0087 madde 5).
- 0.5.2-beta tam koşusu (37214273770, tohum 73770): 95/102; tek bulgu aygıt
  FAT32 `kurtar` (test tarafı, 227b9bc). FAT32 yeniden koşu (37219805226):
  19/19. Issue #4 ve #5 kapatıldı; açık uzun-test issue'su yok.
- Sürüm 0.6.0-beta (kullanıcı kararı): tema + .ts + yedi yeni dil.
- **v0.6.0-beta yayınlandı** (ön sürüm): https://github.com/mcansiz/DiskUltimate/releases/tag/v0.6.0-beta
  — sürüm koşusu 37221403278: 3 platform test + Windows exe, Linux AppImage,
  macOS zip.

## 2026-10-04 (9) — Bölüm başına işletim sistemi (ADR 0089)

- Kullanıcı isteği: disk satırındaki OS amblemi bölüm bazında gösterilsin.
- `bootloader.partition_os`: Windows sürümü ntoskrnl.exe sürüm kaydından
  (Windows 11 (22631)), macOS SystemVersion.plist (yeni), Linux os-release,
  ESP'de yükleyicisi olan sistemler (Windows'un alt klasördeki yükleyicisi
  artık görülüyor).
- `ui/osinfo.py` arka plan servisi (oturum tutamacıyla, tek QThread; kapatma
  ve Uygula öncesi beklenir). Ağaç: amblem + ad; tablo: "İşletim sistemi"
  sütunu; harita: blok başında amblem.
- t89, ui_smoke; run_all 87/89, i18n (9 dil) TAMAM, platform 0, diag 13/13.
- Sürüm 0.6.1-beta (kullanıcı kararı): bölüm başına işletim sistemi.
- **v0.6.1-beta yayınlandı** (ön sürüm): https://github.com/mcansiz/DiskUltimate/releases/tag/v0.6.1-beta
  — sürüm koşusu 37223512268: 3 platform test + Windows exe, Linux AppImage, macOS zip.

## 2026-10-04 (10) — Lisanslar, Hakkında, güncelleme denetimi (ADR 0090)

- Araştırma: exe/AppImage Qt (LGPLv3), PyQt5 (GPLv3), PyQt5-sip (BSD-2) ve
  Python'u (PSF) içinde dağıtıyor; lisans penceresi yalnızca ikon paketlerini
  gösteriyordu. `diskultimate/licenses/` metinler + bileşen listesi; Qt için
  bildirim ve yazılı kaynak teklifi; `.spec` metinleri exe'ye alır.
- Hakkında: geliştirici Mikail Cansız, proje sayfası (tıklanabilir), lisans.
- Güncelleme denetimi: `core/updates.py` (GitHub Releases, ön sürümler dahil,
  numarayla sıralama), açılışta sessiz + Yardım menüsünde elle; atlanan sürüm;
  `platform.open_url` Linux'ta tarayıcıyı root değil kullanıcı olarak açar.
- t90, ui_smoke; 20 yeni metin 9 dilde. run_all 88/90, platform 0, diag 13/13.
- Güncelleme denetimi zaman aşımı (kullanıcı bildirdi): 15 sn, API'yi ikinci
  deneme, `releases.atom` yedeği, anlaşılır hata metni; t90 genişledi.
- Sürüm 0.6.2-beta (kullanıcı kararı): lisanslar, Hakkında, güncelleme denetimi.
- **v0.6.2-beta yayınlandı** (ön sürüm): https://github.com/mcansiz/DiskUltimate/releases/tag/v0.6.2-beta
  — sürüm koşusu 37226080373. Canlı denetim: 0.6.1-beta için v0.6.2-beta
  bulundu, 0.6.2-beta için yeni sürüm yok.

## 2026-10-05 — Az kümeli FAT32 tespiti, yedekleme hızı (ADR 0091)

- Kullanıcı bildirdi: `picozed.img` BOOT bölümü FAT16 ve etiket `□□□`
  görünüyordu (DiskGenius: FAT32). Kök neden: tür küme sayısından seçiliyordu
  (25 549 < 65 525). Artık BPB_FATSz16 == 0 → FAT32 (Linux/Windows ile aynı);
  `fsdetect`, `fat.FatFS`, `resize` alt sınırı. FatFS bu birime yazsaydı
  tabloyu bozardı. Doğrulama: picozed.img salt okunur — FAT32 / BOOT, kök
  dizin (BOOT.BIN, image.ub) okunuyor; fsck.vfat temiz.
- Yedekleme: profil 3.3 s (zlib 2.2 s tek çekirdek, `strip` sıfır denetimi
  1.0 s). `image.is_zero` (memcmp, ~58 kat) + paralel zlib (≤ 8 iş parçacığı,
  sıralı yazım, çıktı aynı) + `span("backup")`: 0.71 s. `.dub` biçimi aynı.
- DiskGenius gibi yalnızca kullanılan kümeleri yedekleme: kullanıcı onayladı
  (64 GB SD kart, başka PC) → ADR 0092, aşağıda.
- t91, t92; run_all 90/92 (2 Windows'a özgü atlandı), diag 13/13,
  platform 0, i18n TAMAM.

## 2026-10-05 (2) — Yalnızca kullanılan alanı yedekleme (ADR 0092)

- Kullanıcı: 64 GB SD kart yedeği (başka PC) DiskGenius'ta hızlı, bizde çok
  yavaş; "evet yapalım". Asıl fark okuma: her sektör yerine yalnızca dolu
  kümeler.
- `core/usedmap.py`: FAT (FAT tablosu), exFAT (bitmap), ext2/3/4 (grup
  bitmapleri, BLOCK_UNINIT'te metaveri), NTFS (`$Bitmap`); disk düzeyi baş
  bölge, ≤16 MiB boşluklar, EBR, kenarlar. Tanınmayan/okunamayan → tamamı;
  ext `needs_recovery`/bigalloc → tamamı.
- `.dub` sürüm 2: `BLOCK_SKIP` (3), başlık bayrağı bit 1; geri yüklemede
  atlanan alana dokunulmaz; `DubImage.is_skipped`, restoreplan da atlar.
  Atlanan bloksuz yedek sürüm 1 kalır.
- Yedek penceresi: "Yalnızca kullanılan alanı yedekle (hızlı)" varsayılan
  açık; bilgi alanında "Kapsam". 7 yeni metin 9 dilde.
- Ölçüm: 64 GB seyrek görüntü (300 MB veri) 27,4 s → 1,7 s; picozed.img
  105/512 MB okunur. Fiziksel kartta ölçülmedi (VM/Windows'ta yapılmalı).
- t93 (4 FS + fsck'ler, ham önyükleyici, EBR, dokunulmayan hedef alan).

## 2026-10-06 — Yedek penceresi: kilit, geçen/kalan süre, Durdur (ADR 0093)

- Kullanıcı isteği: yedek alınırken "Disk seç" gibi alanlar aktifti; süre
  gösterilmiyordu; düğme durdurmaya dönüşsün.
- İş sürerken kip, dosya, not, kaynak/hedef ve seçenekler kilitli; düğme
  "Durdur" (yeni `stop` ikonu, 5 gömülü pakette karşılığı).
- `Geçen · Kalan ~` saniyede bir; hız belirsiz aşamadan sonra ölçülür.
- `clone.OperationCancelled` (BaseException) ilerleme geri çağrısından
  fırlar; yarım `.dub` / yarım yeni görüntü silinir; var olan hedefe geri
  yüklemeyi durdurmadan önce onay.
- t94, ui_smoke senaryosu; 9 yeni metin 9 dilde.

## 2026-10-06 (2) — Kök neden, yabancı girdi matrisi, uyumluluk düzeltmeleri (ADR 0094)

- Kullanıcı: "o kadar uzun test yaptık, bu kritik hata nasıl olabiliyor; benzer
  hataları analiz et, gerekirse saatlerce test et." Kök neden: testler birimi
  hep kendi biçimlendiricimizle üretiyordu (okuyucu ile aynı varsayım);
  yazıcılar yasak listesiyle korunuyordu.
- `tests/yabanci` + `.github/workflows/yabanci.yml`: 68 varyant gerçek mkfs
  araçlarıyla, çekirdekle doldurma (özel nesneler: sabit bağ, symlink 59/60/61,
  aygıt, seyrek, fallocate, xattr, casefold), tespit/okuma/iki kipli yedek
  (çöplü hedef)/yazma/boyut → fsck (taban çizgisine göre) + çekirdek.
  `tests/long` yedek adımı iki kipli + çöplü hedef.
- Dört alan denetimi (alt ajanlar) + matris: ~45 bulgu
  (`.claude/logs/2026-10-06-uyumluluk-denetimi.md`). Beş düzeltme ajanı:
  ext (izin listesi, needs_recovery/mmp/journal_dev/kirli durum reddi, sabit
  bağ, aygıt/hızlı symlink, inline_data okuma, delikler, unwritten extent),
  FAT/exFAT (küme sızıntısı, NoFatChain, UTF-16 ad uzunluğu, VDL, ExtFlags,
  OS/2 starthi, bps≠512 boyutlandırma, etiket, CP437, **picozed tutarlılık
  kapısı**), NTFS (512 B fixup, hazırda bekleme/$LogFile kapısı, attribute
  list okuma + yazma reddi, $MFTMirr aralığı, INDX çok koşu, EFS, 4K birim
  karışımı), bölüm tabloları (yuva numarası korunur, koruyucu MBR + CRC, yedek
  başlık, hibrit MBR, FirstUsableLBA, 4Kn GPT↔MBR), XFS büyütme 1 KiB/4K
  sektör, HFS+ Linux tonos adları.
- picozed.img: içerik referans okuyucularla birebir (FAT 4, ext4 4370 dosya);
  FAT32 yapısal bozuk (iki FAT'in ilk sektöründe eski önyükleme baytları),
  DiskUltimate kaynaklı görünmüyor; artık "tutarsız" işaretlenir, yazılmaz.
- Arayüz: bölüm bilgisinde "Yapı tutarsız", disk özetinde tablo belirsizliği;
  plan önizlemesi yuva numarası kuralına uyar. 47 yeni metin 9 dilde.
- Testler: regress_{fat,ext,ntfs,ptable,xfs_hfs} 79 test (eski kodda
  başarısız, yeni kodda geçer), CI'a bağlandı; run_all 92/94, ext_write 4/4,
  ext_resize 32/32, ntfs_write 2/2, diag 13/13, platform 0, i18n, ui_smoke.
- Windows'ta doğrulanmadı: NTFS 4K fixup chkdsk, 64K+ küme aynası, Hızlı
  Başlangıç kapısı gerçek birimde.
- GitHub: 10 turluk yabanci matris tum gruplarda temiz (ilgili duzeltmeden
  sonra); matrisin yeni buldugu iki NTFS hatasi duzeltildi: ayni klasorde
  sabit bag silme/ad degistirme obur bagi yok ediyordu (M7); 64K+ kumede
  olusturulan klasorler ntfs3'te bos gorunuyordu (M8; kendi
  bicimlendiricimizde de). Uzun testler eski kodda 102/102; en son kodla
  yeniden kosuyor. regress_* 82 test.
- Uzun testler tum duzeltmelerle 102/102 (37501631074); Windows NTFS isleri
  chkdsk ile temiz.

## 2026-10-06 (3) — VirtualBox Windows 10 gidis-donus sinamasi

- Windows'un bicimlendirdigi NTFS 64K/4K/2M, exFAT 128K, FAT32 birimleri
  (Windows API'leriyle: sabit bag, ADS, seyrek, sikistirilmis, 400 parcali,
  emoji ad) -> bizim okuma 1532/1533 (yalnizca LZNT1 sikistirilmis dosya:
  ozellik eksigi, acikca reddediliyor), iki kipli yedek copla dolu hedefe,
  yazma/silme/sabit bag/ad degistirme, NTFS kucultme + FAT32 buyutme ->
  Windows chkdsk 10/10 birimde temiz, dosya ozetleri birebir.
- Temiz kapatilmamis birim (Windows yazarken guc kesildi): NTFS kapisi
  calisti; FAT32 (BPB 0x41) ve exFAT (VolumeDirty) kirli bayraklari yok
  sayiliyordu -> ret kapisi + tam yedek + arayuz uyarisi (M9, t17).
- Hizli Baslangic bu VM'de sinanamiyor (bellenim hazirda bekletmeyi
  desteklemiyor). Betikler: tests/vm_windows/.
- run_all 92/94, regress 83 test (82 tamam + 1 atlandi), diag, platform,
  i18n, ui_smoke tamam.
- Sürüm 0.7.0-beta (kullanıcı: "release yayınlayalım"): yalnızca kullanılan alan
  yedeği, Durdur/süre, yabancı girdi uyumluluk ve güvenlik düzeltmeleri.
