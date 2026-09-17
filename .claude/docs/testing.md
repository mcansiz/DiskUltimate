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

## Nerede calistirilir (ZORUNLU)

**Calistiran testler sanal makinede kosar** (CLAUDE.md > Test Ortami Kurali).
Linux hedefi Linux Mint 22.3'tur:

```bash
# ana makineden (Windows), PuTTY ile:
plink -batch -ssh -l pc -pw 1234 192.168.42.131 "cd ~/DiskUltimate && python3 -m tests.run_all"
```

Kaynagi misafire aktarmak icin paylasilan klasor **kullanilmaz** (seyrek dosya
desteklemez, ana makinenin diskini doldurur); arsiv kopyalanir:

```bash
tar --exclude=.git --exclude=.tmp --exclude=__pycache__ -czf du.tar.gz .
pscp -pw 1234 du.tar.gz pc@192.168.42.131:/home/pc/
plink -batch -ssh -l pc -pw 1234 192.168.42.131   "rm -rf ~/DiskUltimate && mkdir ~/DiskUltimate && tar -xzf ~/du.tar.gz -C ~/DiskUltimate"
```

Statik denetimler (`platform_check`, `i18n_check`) disk acmaz; ana makinede de
kosabilir, ama sonuc VM'de de dogrulanir.

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
| `t25_islem_kuyrugu` | Bekleyen islem kuyrugu: kuyruga eklemek diski **degistirmiyor** (sha256), adimlar sirayla isliyor, basarisiz adim durduruyor, yazilamaz kaynak reddediliyor (ADR 0025) |
| `t26_dogrudan_yazma_yollari` | Kuyruga **girmeyen** yazmalar (geri yukleme) salt okunur kaynakta `become_writable()` ile calisabiliyor; gecemeyen kaynak nedenini soyluyor (ADR 0025 gerilemesi) |
| `t27_disk_yoklamasi` | Bolumler disk **acilmadan** okunuyor; aygit salt okunur aciliyor, **yazilmiyor** ve hemen kapatiliyor; acik aygit yoklanmiyor (ADR 0026) |
| `t28_dosya_ekleme_yazma_modu` | Salt okunur acilan kaynaga dosya eklenebiliyor: yetki ilk yazmada aliniyor, **taze** dosya sistemi kullaniliyor (ADR 0025 gerilemesi) |

| `t29_uefi_yapilari` | UEFI giris/aygit yolu/kisayol gidis-donusu, `efibootmgr` metin bicimi, bilinmeyen dugumun korunmasi, GUID karisik siralamasi |
| `t30_onyukleme_kodu_ve_sistem_tespiti` | Onyukleme kodu tanima (yalnizca ilk 440 bayt), bolum tablosundaki `GRUB` baytinin yaniltmamasi, kodun kaldirilmasinda tablonun/MBR imzasinin korunmasi, kuyruktan calistirma |
| `t31_isletim_sistemi_tespiti` | ESP tanima ve `.efi` yukleyici listesi, kurulu Linux ile ayni FS'teki veri bolumunun ayirt edilmesi, harf duyarsiz yol aramasi |
| `t32_grub_yapilandirmasi` | `GrubDefaults` duzenlemesinde yorum/sira korumasi ve yerinde guncelleme, `grub.cfg` menu + alt menu cozumu |
| `t33_uefi_yedegi_ve_degisiklik_plani` | UEFI yedeginin bire bir gidis-donusu, degisiklik planinin **guvenli sirasi** (girisler → sira → silmeler), sirada olmayan girisin gizlenmemesi |
| `t34_bellenime_yazma_kapisi` | Bellenim yazilamiyorken planin uygulanmamasi ve nedenin bildirilmesi (**gercek bellenime dokunan tek test**; yazma acikken atlanir) |

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

## Ceviri denetimi

```bash
python3 -m tests.i18n_check               # butun dilleri denetler
python3 -m tests.i18n_check --write <kod> # <kod>.po dosyasini kaynaktan tazeler
python3 -m tests.i18n_check --list        # cevrilecek metinleri listeler
```

Sozlukler gettext `.po` bicimindedir (`i18n/catalogs/<dil>.po`). `--write`
`msgmerge` gibi calisir: mevcut ceviriler korunur, kaynakta kalmayan giris
**silinmez, bayatlatilir** (`#~`), benzeyen yeni bir giris varsa ceviri oraya
tasinip `#, fuzzy` isaretlenir.

Kaynaktaki her `tr(...)` / `mark(...)` metnini toplar (su an **1062**) ve her
dil dosyasi icin dogrular:

| Denetim | Dogruladigi |
|---|---|
| eksik ceviri | Kaynaktaki her metin sozlukte var ve bos degil |
| fuzzy giris | `#, fuzzy` isaretli giris **eksik sayilir**: calisma aninda kullanilmaz, kullanici Turkce gorur |
| cogul bicimi | `Plural-Forms` kac bicim diyorsa o kadar `msgstr[n]` dolu |
| yer tutucu | `{}` sayisi ve `{ad}` adlari kaynakla ceviride ayni |
| HTML etiketi | `<b>`, `<br>` gibi etiketler ceviride korunmus |
| eylem metinleri | Her `self.act_*` icin `_retranslate_actions()` satiri var |
| calisma zamani | Sozluk yuklenince `tr()` ceviriyor, kaynak dile donunce geri geliyor |

> **Cevrilmemis metin** bu denetimle yakalanmaz (kaynakta olmayan bir seyi
> bilemez). Onun icin sozde-yerellestirme vardir — asagida.
> `tests/ui_smoke.py` ayrica her dil icin ekran goruntusu uretir
> (`24-dil-<kod>.png`).

### Sozde-yerellestirme (pseudolocalization)

```bash
DISKULTIMATE_LANG=qps python3 main.py      # arayuzu sahte dilde ac
```

Metin `[!Ɓǿŀŭḿŭ şīŀ···!]` bicimine girer. Yer tutucular (`{}`), HTML
etiketleri ve varliklar **dokunulmadan** kalir.

Uc seyi ayni anda yakalar:

| Belirti | Ne demek |
|---|---|
| Metin Turkce kalmis | `tr()` ile **sarilmamis** — statik denetimin goremedigi tek bosluk turu |
| Metin kutuya sigmamis | Yerlesim tasmasi (metin ~%30 uzatilir; Almanca'nin en kotu hali) |
| Yan yana birden cok `[!...!]` | Metin parca parca birlestirilmis; cevirmen cumleyi kuramaz |

`tests/ui_smoke.py` bunu **otomatik** denetler (`sozde_denetimi`): sozde dilde
taze bir ana pencere ve alti diyalog kurar; eylem, menu, sekme ve sutun
metinlerinin hepsinin sozde oldugunu **dogrular** (degilse test duser), kalan
metinleri bilgi olarak listeler. Listede normalde yalnizca **veri** kalir
(dosya adi, "64 bit", disk modeli, harici arac adlari).

Cekirdek testleri Turkce mesaj icerigine bakar (orn. `"birincil" in str(exc)`).
Bu yuzden `i18n` **kendiliginden** dil secmez: secim yalnizca `main.py` icinde
`initialize()` ile yapilir, testler kaynak dilde kosar.

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

## Windows dogrulama ortami (VMware Player misafiri)

Windows'a ozgu yazma testleri (NTFS `mkfs` yok; fiziksel diske yazma) bir
**VMware Player** misafirinde kosulur. Misafir: **Windows 10 Enterprise 2016
LTSB** (x64), `DESKTOP-GLH638P`, VM adi `ltsc`. Kanal: VMware Tools / `vmrun`
(ana makinede `vmrun -T player ...`); ag kullanilmaz.

**Yonetici hesabi (ZORUNLU).** Yazma testleri **`Admin`** hesabiyla kosulur.
Bu hesap `vmrun` ile acildiginda **dogrudan yukseltilmis** gelir (High
Integrity `S-1-16-12288`, `Administrators` etkin, `net session` calisir).
`user` hesabi da yonetici grubundadir ama `vmrun` oturumunda UAC token
filtresi yuzunden **Orta Duzey** kalir; yukseltmek icin
`schtasks /rl highest /ru user /rp <parola>` gerekir. Bu yuzden yonetici is
icin **`Admin`** kullanilir.

**Cevrimdisi kurali (ZORUNLU).** Misafir **internete acilmaz**
(`ethernet0.startConnected = "FALSE"`). Gerekli paketler (Python, PyQt5, ...)
**ana makinede** indirilir, paylasilan klasore konur ve misafirde **oradan
cevrimdisi** kurulur. Misafirde agdan `pip install <paket>` yapilmaz;
`pip install --no-index --find-links <klasor>` ya da `.whl` tekerlekleri
kullanilir.

**Paylasilan klasor.** Ana makinedeki `D:\pythonProjeler\DiskUltimate`
misafirde `\\vmware-host\Shared Folders\DiskUltimate` olarak baglidir (HGFS,
yazma izinli). Proje koku:
`\\vmware-host\Shared Folders\DiskUltimate\DiskUltimate`.

> HGFS eslemesi **oturuma baglidir**: interaktif (konsol) oturumda gorunur,
> ama `vmrun`'un batch oturumunda `\\vmware-host` **gorunmeyebilir**. `vmrun`
> ile otomatik kosarken kaynagi/paketleri misafire `copyFileFromHostToGuest`
> ile aktarin ya da testleri interaktif oturumda calistirin.

**Testleri paylasilan klasorde calistirmayin** — Linux'taki ile ayni gerekce
(HGFS seyrek dosya desteklemez, ana makine diski dolabilir). Kaynagi misafirin
yerel diskine kopyalayin (orn. `C:\du-test`) ve orada kosun.

Fiziksel disk testleri icin misafire eklenen **ikinci** disk hedeflenir:
`\\.\PhysicalDrive1` (10 GB NVMe); sistem diski `\\.\PhysicalDrive0` asla
hedef gosterilmez. Aygit yolu CreateFile ile acilir (`\\.\PhysicalDriveN`).

## Onyukleme ve UEFI dogrulamasi (ADR 0028 / 0029)

### Neyi aygitsiz sinayabiliriz

Cozumleme bellenim erisiminden ayri durdugu icin (`core/efiboot.py` vs
`core/platform.py`) UEFI yapilarinin tamami **UEFI olmayan bir makinede** bile
dogrulanir. Ayni sekilde onyukleme kodu tanima ve isletim sistemi tespiti
uretilmis bir `.img` uzerinde kosar; `mount` ve root gerekmez.

```bash
python3 -m tests.run_all      # t29 - t34
```

### Gercek bellenime karsi dogrulama (salt okunur)

Cozumleyicinin dogrulugu, makinenin kendi onyukleme girisleriyle
karsilastirilarak olculebilir. **Hicbir sey yazilmaz:**

```bash
python3 - <<'PY'
import sys; sys.path.insert(0, "src")
from diskultimate.core import efistore
durum = efistore.load()
for k, v in durum.summary().items():
    print(f"{k}: {v}")
for giris in durum.ordered("Boot"):
    print(f"{giris.name}  etkin={giris.active}  {giris.description}")
    print(f"    {giris.path_text}")
PY
```

Beklenen: cikti `efibootmgr -v` (Linux) ya da bellenim menusuyle **ayni**
girisleri ayni sirada gostermeli. Aygit yolu metni `efibootmgr` bicimindedir,
bu yuzden satirlar dogrudan karsilastirilabilir.

Bu, gelistirme makinesinde (Windows 10, UEFI) kosuldu: 205 degisken sayildi,
alti onyukleme girisi cozuldu ve **hepsinin `to_bytes()` ciktisi okunan
baytlarla bire bir ayni** cikti.

### Olculemeyen: bellenime yazma

**UEFI degiskenine yazma yolu gercek bellenimde denenmemistir.** Nedeni:

- Linux Mint misafiri **BIOS (eski) kipinde** acilir; `/sys/firmware/efi`
  yoktur, yazilacak degisken de yoktur.
- Gelistirme makinesinde deneme yapilmaz (CLAUDE.md > Test Ortami Kurali).

Sinanmis olan: yazmanin **reddedilmesi** ve nedenin bildirilmesi (t34),
degisiklik planinin uretilmesi ve sirasi (t33). Sinanmamis olan: `efivar_write`
cagrisinin bellenimde gercekten is gormesi. Bu, misafir UEFI kipinde
acilacak sekilde yeniden kurulduktan sonra olculmelidir.

### GRUB islemleri de calistirilmadi

`grub-install` ve `update-grub` test misafirinde **kosturulmadi**: VM'in kendi
onyukleyicisine dokunmak onu acilmaz birakabilirdi. Sinanmis olan arac bulma,
yetki kapisi ve "bu platformda kullanilamaz" dallaridir. Bu islemleri gercekten
olcmek icin **atilabilir** bir misafir gerekir.

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
**yirmi uc PNG** kaydeder: ana pencere, sekmeler, coklu goruntu ve diyaloglar
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
- **bekleyen islem panelinin** dolu olmasi (`21-bekleyen-islemler.png`): sol
  alttaki liste, arac cubugundaki `Uygula (N)` ve tabloda etkilenen bolumlerin
  kum saati isareti (ADR 0025)
- **ikon setinin tamaminin cizilmesi**: `icons.names()` icindeki her ad bos
  olmayan bir pixmap uretmeli, her `QIcon` **cok boyutlu** olmali
  (`20-ikon-seti.png` elle gozle denetlenir)
- **ikonlarin her platformda ayni olmasi**: duman testi `icons.digest()`
  degerini basar. Windows ve Linux ciktilari **ayni** olmalidir
  (`1ca31e30c5d79d2e0ed72a2132810cdd`). Ozet PNG degil **piksel** uzerinden
  hesaplanir; PNG kodlamasi Qt eklentisine gore degisir (ADR 0025)
- **diskin altinda bolumlerin gorunmesi** (`23-disk-genel-bakis.png`): agacta
  `physpart` dugumleri ve genel bakis seridindeki bloklar; hicbir disk
  acilmadan (ADR 0026)

> Windows'ta platform eklentisi otomatik olarak `windows` secilir: Qt'nin
> `offscreen` eklentisi orada hic font yuklemez ve goruntulerde metin gorunmez.
> Kosum sonunda font ailesi sayisi yazdirilir; 0 ise uyari verilir.

```bash
python3 main.py disk.img                     # gercek ekranda, dosya acarak
```

### Neden Linux'ta da kosulmali

Coklu dil calismasi yalnizca Windows misafirinde dogrulanmisti. Onyukleme
ozellikleri eklenirken ayni duman testi Linux'ta kosuldu ve **dil adiminda
dondu**: `FileBrowser.retranslate()` gecerli klasoru yeniden listelerken
basarisiz oluyor ve **modal** bir uyari aciyordu; kapatacak kimse olmadigi
icin surec sonsuza kadar bekledi. Duzeltildi (`navigate(..., quiet=True)`),
bkz. worklog 2026-09-17 (4).

Ders: dil degisimi gibi **kullanicinin baslatmadigi** tazelemeler modal
pencere dogurmamalidir. Yeni bir `retranslate()` yazarken bu denetlenir.

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
