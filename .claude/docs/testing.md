# Test

## Calistirma

```bash
python3 -m tests.run_all              # cekirdek testleri (harici kutuphane gerekmez)
```

> Windows'ta `python3` yoksa `python` kullanin. PyQt5 birden fazla Python
> surumu kuruluysa arayuz testleri icin dogru yorumlayiciyi secin
> (ornegin `py -3.12 -m tests.ui_smoke`).

Testler **proje dizini icinde** calisir: uretilen goruntuler `<proje>/.tmp/tests`
altina yazilir (bkz. `src/diskultimate/paths.py`). `/tmp` kullanilmaz — cogu sistemde
`tmpfs`, yani RAM uzerindedir ve 1 GB'lik test goruntuleri belleği doldurur.

Baska bir konum icin: `DISKULTIMATE_SCRATCH=/baska/yol python3 -m tests.run_all`
Temizlik: `rm -rf .tmp`

## Kapsam

| Test | Dogruladigi |
|---|---|
| `t01_image_temel` | Goruntu olusturma/okuma/yazma, `PartitionView` sinir denetimi, boyut cozumleme |
| `t02_mbr` | 4 birincil bolum, genisletilmis bolum, EBR zinciri, geri okuma, silme |
| `t03_gpt` | Baslik ve giris CRC32'si, koruyucu MBR, yedek baslik konumu, silme |
| `t04_fat_bicimlendirme` | FAT12/16/32 bicimlendirme, LFN, klasor, buyuk dosya, disa/ice aktarma, yeniden adlandirma, silme, tespit, **`fsck.vfat` dogrulamasi** |
| `t05_harici_bicimlendirme` | exFAT / NTFS / ext4 `mkfs` akisi ve goruntu seyrekliginin korunmasi |
| `t06_oturum_uctan_uca` | `DiskSession`: goruntu -> GPT -> bolum -> bicimlendirme -> dosya -> yeniden acma |
| `t07_mbr_oturum` | MBR akisi, 4 birincil bolum siniri, onyukleme bayragi |
| `t08_hata_geri_alma` | Basarisiz bicimlendirmenin tabloda hayalet bolum birakmamasi |
| `t09_exfat_saf_python` | exFAT bicimlendirme, LFN, dosya islemleri, gomulu upcase saglamasi, **`fsck.exfat` dogrulamasi** |
| `t10_sema_donusumu` | MBR↔GPT donusumunun bolum verisini ve tip eslemesini korumasi, hizalama raporu |
| `t11_yedekleme_ve_klonlama` | `.dub` yedek boyutu, bozma → geri yukleme, disk klonu, seyreklik |
| `t12_guvenli_silme` | Silmenin bolum disina tasmamasi, bos alan silmenin mevcut dosyalari korumasi |
| `t13_kurtarma` | Silinmis dosya (uzun ad dahil) kurtarma, kayip bolum tarama, imza tabanli carving |
| `t14_sanal_diskler` | VHD olusturma/yazma/okuma, **VBoxManage dogrulamasi**, VBoxManage ile uretilen VDI/VMDK okuma |
| `t15_yazma_basarimi` | Buyuk dosya yazma/okuma hizi — tahsis dongusu regresyon korumasi (ADR 0010) |
| `t16_ext_ailesi` | ext2/ext3/ext4 saf Python bicimlendirme, JBD2 gunlugu, **`e2fsck` dogrulamasi** (ADR 0017) |
| `t17_ntfs` | NTFS saf Python bicimlendirme, MFT/`$UpCase`/`$AttrDef`, **`ntfsfix`/`ntfsinfo` dogrulamasi** (ADR 0018) |
| `t18_bolum_boyutlandirma` | Bolum kucultme / buyutme / tasima; FAT ve exFAT veri korumasi, guvenlik kapilari, **`fsck` dogrulamasi** (ADR 0019) |
| `t19_klasor_kopyalama` | Klasor kopyalamanin dort dosya sisteminde **ayni yerlesimi** uretmesi ve ilerleme bildirimi (ADR 0021) |
| `t20_acik_aygit_kutugu` | Uygulamanin acik tuttugu aygitin listelemede yeniden yoklanmamasi — donma korumasi (ADR 0021) |
| `t21_birim_kilidi_kutukten_etkilenmez` | Windows birim kilidinin acik aygit kutugune takilmamasi — kilit olmazsa her yazma reddedilir (ADR 0022) |
| `t22_yetki_yukseltme` | Yonetici/root yukseltmesi: durum sorgusu, yeniden baslatma komutu, betiksiz durumda **sunulmamasi** (ADR 0023) |
| `t23_ntfs_bitmap_aralikli_yazma` | NTFS `$Bitmap`: aralikli yazmanin sonucu tam yazmayla **birebir ayni** olmali; pencere siniri, bitisik parca birlestirme, kismi tahsis birakmama (ADR 0024) |
| `t24_yedek_onizleme` | `.dub` yedeginin icerigi **geri yuklenmeden** okunuyor: bolumler, dosya sistemleri, kok klasor; disk/bolum yedegi ayrimi; bos ile okunamayan ayrimi |

## Ortam guvenligi (onemli)

`tests.run_all` baslamadan once test dizinini denetler:

- **Seyrek dosya destegi olculur.** Desteklenmiyorsa (VirtualBox paylasilan
  klasoru `vboxsf`, FAT32 vb.) goruntuler tum boyutlariyla diske yazilir.
- **Bos alan denetlenir:** seyrek destekleniyorsa 2 GB, desteklenmiyorsa 12 GB.
  Yetersizse testler **baslamaz** (cikis kodu 2).
- Her test kendi goruntulerini siler; pik kullanim en buyuk tek test kadardir.
  Dosyalari incelemek icin: `DISKULTIMATE_KEEP_TEST_FILES=1`

> Bu denetim, paylasilan klasorde calistirilan testlerin ana makinenin diskini
> doldurup sistemi kilitlemesi yasandigi icin eklendi (bkz. worklog, 4. oturum).

## Tanilama denetimi

```bash
python3 -m tests.diag_check
```

Donma yakalayicinin **gercekten yakaladigini** dogrular (13 denetim):

| Denetim | Dogruladigi |
|---|---|
| oturum gunlugu | `.claude/logs/runtime/session-*.log` olusuyor |
| `span` gecmisi | olculen islem gecmis tamponuna giriyor |
| YAVAS isareti | 200 ms ustu islem gunluge `YAVAS` olarak giriyor |
| `track=False` | sik tekrarlanan islemler gecmis tamponunu doldurmuyor |
| donma yakalama | nabiz kesilince rapor uretiliyor |
| rapor icerigi | raporda **o an acik islem** ve arayuz yigini var |
| rapor kapanisi | arayuz toparlaninca sure raporun sonuna yaziliyor |
| geri cagirma | arayuz bilgilendiriliyor (islem gunlugune satir) |
| elle dokum | `dump_now()` dosya yaziyor |

Testin ciktilari `<proje>/.tmp/diag` altina yazilir; proje gunluk dizini
kirlenmez.

### Donma suphesi olan bir durumu elle incelemek

```bash
DISKULTIMATE_DIAG_VERBOSE=1 python3 main.py     # her olcum gunluge
DISKULTIMATE_DIAG_STALL_MS=500 python3 main.py  # daha hassas esik
DISKULTIMATE_DIAG=0 python3 main.py             # tanilamayi kapat
```

Uygulama takiliyken **Araclar > Tanilama > Simdi yigin dokumu al** ile elle de
kanit alinabilir. Donma bittikten sonra rapor **Araclar > Tanilama > Son donma
raporunu goster** altindadir.

## Capraz platform denetimi

```bash
python3 -m tests.platform_check
```

Kaynak agacini tarar: sabit POSIX yollari, `tempfile`, dogrudan `subprocess` /
`shutil.which`, endian isaretsiz `struct` cagrilari, `core/` icine PyQt sizintisi
ve **tanimlayici dili**.

Tanimlayici dili denetimi CLAUDE.md'deki *"Turkce arayuz metni, Ingilizce kod
adi"* kuralini zorlar: fonksiyon adlari, parametreler, yerel degiskenler ve
modul duzeyi sabitler Turkce sozcuk parcasi tasiyamaz. Arayuzde gorunen
**metin** Turkce kalir — denetim yalnizca `ast.Name` dugumlerine bakar, dize ve
yorumlara dokunmaz.

> Kural bir kez gevsemisti: 2026-09-15 denetiminde 13 dosyada 74 fonksiyon ve
> 656 yerel degisken Turkce adliydi; en cok sapan dosyalar **en yeni** dosyalardi.
> Bu yuzden ceviriden once denetim yazildi.
Denetleyicinin kendisi kasitli ihlal dosyasiyla dogrulanmistir; beklenen cikti
**"Toplam bulgu: 0"**.

## Linux dogrulama ortami (VMware misafiri)

Harici dogrulayicilarin tamami yalnizca Linux'ta bulunur. Projenin dogrulama
ortami bir **Linux Mint** misafiridir; ana makine (Windows) uzerinde `fsck`
araclarindan hicbiri yoktur ve tablolar orada ATLANDI gosterir.

```bash
sudo apt install python3-pyqt5 e2fsprogs dosfstools exfatprogs ntfs-3g
```

**Testleri paylasilan klasorde calistirmayin.** `vmhgfs`/`vboxsf` seyrek dosya
desteklemez; goruntuler tum boyutlariyla yazilir ve **ana makinenin** diski
dolabilir. Kaynagi misafirin yerel diskine kopyalayin:

```bash
cp -r /mnt/hgfs/<paylasim>/{src,tests,main.py} ~/du-test/
cd ~/du-test && python3 -m tests.run_all && python3 -m tests.fs_matrix
```

Fiziksel disk testleri icin misafire **bos** bir disk eklenir (orn. `/dev/sdb`);
sistem diski (`/dev/sda`) asla hedef gosterilmez.

## Dosya sistemi yetenek matrisi

```bash
python3 -m tests.fs_matrix            # sekiz bicim
python3 -m tests.fs_matrix ext4 fat32 # secilenler
```

Her bicim icin ayni adimlar kosulur — **bicimlendir → tespit → oku → yaz →
harici fsck** — ve sonuc tek tabloda toplanir. Harici arac yoksa adim
**ATLANDI** yazilir, asla TAMAM sayilmaz.

Linux Mint 22.3 uzerinde olculen durum (2026-09-15):

| Bicim | Bicimlendir | Tespit | Oku | Yaz | Harici dogrulama |
|---|---|---|---|---|---|
| fat12/16/32 | ✅ | ✅ | ✅ | ✅ | ✅ `fsck.vfat` |
| exfat | ✅ | ✅ | ✅ | ✅ | ✅ `fsck.exfat` |
| ntfs | ✅ | ✅ | okuyucu yok | okuyucu yok | ✅ `ntfsfix` |
| ext2/3/4 | ✅ | ✅ | ✅ | salt okunur | ✅ `e2fsck` |

## ext yazma dogrulamasi

```bash
python3 -m tests.ext_write_check          # ext2, ext3, ext4
```

Her adimdan sonra `e2fsck -nf` kosar: bicimlendirme, kucuk dosya, mkdir,
dolayli bloklu 256 KB dosya, uzun ad, 60 giris, rename, silme. `e2fsck` yoksa
kosum **basarisiz sayilir** (atlanmaz) — dogrulanmamis ext yazmasinin "gecti"
demesi tehlikelidir.

Yalnizca goruntu dosyalari uzerinde calisir.

## NTFS yazma dogrulamasi

```bash
python3 -m tests.ntfs_write_check       # ntfsfix
sudo python3 -m tests.ntfs_write_check  # + ntfs-3g ile baglama
```

Iki yerlesim sinanir: **kendi bicimlendiricimiz** (indeks `$INDEX_ROOT` icinde)
ve **`mkntfs`** (indeks B+ agacina tasmis). `ntfsfix` yoksa kosum basarisiz
sayilir. `ntfs-3g` baglama adimi root gerektirir; yoksa atlanir ve bu acikca
yazilir.

## Harici dogrulama (elle)

```bash
# Bolumu ayikla (dd yerine tasinabilir yol icin tests/run_all.py::_fsck'e bakin)
dd if=disk.img of=.tmp/p1.img bs=512 skip=2048 status=none

fsck.vfat  -n -v .tmp/p1.img     # FAT12/16/32
fsck.exfat -n    .tmp/p1.img     # exFAT  -> "clean" beklenir
fdisk -l disk.img                # MBR/GPT tablosu
VBoxManage showhdinfo disk.vhd   # uretilen sanal diskin gecerliligi
```

## Arayuz duman testi

```bash
python3 -m tests.ui_smoke                      # offscreen, ekrana pencere acmaz
DISKULTIMATE_QPA=xcb python3 -m tests.ui_smoke # gercek cizim yolu
```

> Duman testi `DISKULTIMATE_NO_ELEVATION_PROMPT=1` ayarlar: yetki yukseltme
> teklifi modal bir penceredir ve otomatik kosumu kilitlerdi (ADR 0023). Ayni
> degisken elle kosumda da teklifi kapatir.

> Tema dalindaki **sekme genisligi** denetimi (ADR 0012) PyQt5'in bazi
> surumlerinde calismaz: `tabSizeHint` korumali bir isleve dokunur ve
> `RuntimeError: no access to protected functions...` atar (Ubuntu 24.04).
> O durumda yalnizca bu denetim atlanir, test devam eder — eskiden butun duman
> testi burada duruyor ve sonraki adimlar Linux'ta hic kosmuyordu.

Ornek bir 4 GB / dort bolumlu goruntu uretir ve `<proje>/.tmp/screenshots` altina
**yirmi PNG** kaydeder: ana pencere, sekmeler, coklu goruntu ve diyaloglar
(bolum boyutlandirma penceresi ve suruklemesi dahil). Arayuzde degisiklik
yapildiginda bu goruntuler gozle denetlenir — ozellikle:
- disk haritasinda metin ile doluluk cubugunun cakismamasi
- arac cubugu ve dosya listesi **ikonlarinin gorunur** olmasi (koyu ikon temasi tuzagi)
- acilir liste / sayi kutusu oklarinin cizilmesi (stil sayfasi tuzagi)
- dugme metinlerinin Turkce olmasi
- **sekme basliklarinin kirpilmamasi** (stil sayfasi font-weight tuzagi, ADR 0012)
- **kopyalama ilerleme penceresinin** cizilmesi (`17-kopyalama-ilerleme.png`):
  buyuk bir dosya yazilirken hicbir sey gosterilmemesi kullaniciya donma gibi
  gorunmustu (ADR 0021)
- **yedek icerik listesinin** dolu olmasi (`18-yedek-bilgisi.png`): bolum
  satirlari, dosya sistemi/etiket sutunlari ve kok klasor girisleri; bos bolum
  `(bos)`, okunamayan icerik ise nedeniyle yazilir

> Windows'ta platform eklentisi otomatik olarak `windows` secilir: Qt'nin
> `offscreen` eklentisi orada hic font yuklemez ve goruntulerde metin gorunmez.
> Kosum sonunda font ailesi sayisi yazdirilir; 0 ise uyari verilir.

```bash
python3 main.py disk.img                     # gercek ekranda, dosya acarak
```

## Bilinen sinirlar (test disi)
- NTFS kullanim orani okunamiyor (MFT cozumlemesi yok) — tabloda `-` gosterilir.
- `t05_harici_bicimlendirme` sistemde `mkfs.*` yoksa **atlanir**. Atlanan test
  basarili sayilmaz: ozet `17/18 basarili · 1 atlandi` der ve hangi testin
  neden atlandigini yazar. Cikis kodu yine 0'dir (kirmizi kosum degildir).
  Harici arac yolunu dogrulamak icin Linux ortaminda kosulmalidir.
- **NTFS** icerigi listelenemiyor; bicimlendirme (saf Python) ve tespit calisir.
  NTFS okuyucusu v0.4 hedefi.
- **ext2/3/4 salt okunurdur**: listeleme, okuma ve disa aktarma calisir; yazma,
  silme ve yeniden adlandirma yoktur (gunluk tutarliligi gerektirir).
  FAT ve exFAT tam okuma/yazma destekler.
- **macOS uzerinde testler hic calistirilmadi**; yalnizca statik denetimden gecti.
  Windows uzerinde kosuldu — hangi surumun nerede kosuldugu icin
  [cross-platform.md](cross-platform.md) test tablosuna bakin.
- Silinmis dosya kurtarma, veriyi **ardisik** varsayar; parcalanmis dosyalarda
  sonuc eksik olabilir (arayuzde "kurtarilabilirlik" yuzdesi bunu belirtir).
- 2 TiB uzeri MBR bolumleri desteklenmez (bicim sinirlamasi); GPT kullanilmalidir.
- Uygulama Wayland oturumlarinda XWayland (`xcb`) uzerinde acilir; yerel Wayland
  eklentisinde modal pencereler bos cizilir (bkz. ADR 0005).
