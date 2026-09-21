# DiskUltimate

DiskGenius benzeri gorsel disk yonetim araci: disk goruntuleri, sanal diskler ve
**sistemdeki gercek diskler** uzerinde calisir. Python 3 + PyQt5, harici bagimlilik yok.

**Desteklenen platformlar: Windows ve Linux** — ikisinde de testler kosulmustur.
Cekirdek saf Python ve macOS kod yollari (`diskutil`) yazilmis durumda, ancak
**macOS'ta hicbir test calistirilmamistir**; bu yuzden destegi ilan edilmiyor.

![surum](https://img.shields.io/badge/surum-0.4.0-blue) ![python](https://img.shields.io/badge/python-3.8%2B-green) ![platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-brightgreen) ![test](https://img.shields.io/badge/test-28%2F28-brightgreen) ![dil](https://img.shields.io/badge/dil-tr%20%7C%20en%20%7C%20de-blue)

Bolum tablolari ve dosya sistemleri **sifirdan, saf Python ile** yazilir. Sekiz
dosya sisteminin (FAT12/16/32, exFAT, NTFS, ext2/3/4) tamaminda **bicimlendirme,
okuma ve yazma** calisir; uretilen birimler `fsck.vfat`, `fsck.exfat`, `e2fsck`,
`ntfsfix`/`ntfs-3g` ve Windows `chkdsk` ile capraz dogrulanir.

## Yetenekler

**Disk ve bolum**
- Seyrek goruntu olusturma, acma, yeniden boyutlandirma
- Sanal disk destegi: **VHD, VDI, VMDK, QCOW2** okuma; VHD olusturma ve yazma
- **Fiziksel diskler** — takili her disk listelenir ve acilir (Windows / Linux / macOS);
  USB bellek ve SD kart uygulama acikken takilsa da listede **kendiliginden** belirir
- MBR (4 birincil + genisletilmis + mantiksal) ve GPT (128 bolum, CRC32, yedek baslik)
- **MBR ↔ GPT donusumu** — bolum verileri yerinde kalir
- **Bolumu boyutlandirma / tasima** — fareyle suruklenebilir serit; FAT, exFAT
  ve **NTFS** veri korunarak kucultulur/buyutulur (saf Python, her platformda ve
  goruntu dosyalarinda); ext hala harici arac ister. Windows'ta fiziksel diskte
  isletim sisteminin kendi boyutlandiricisi tercih edilir.
- 4K hizalama denetimi

- **Yedekleme ve geri yukleme tek pencerede** — ustte `.dub` dosyasinin
  bilgisi, **notu** ve icerigi; altta acik goruntuler ve fiziksel diskler.
  Sikistirma duzeyi secilir, ilerleme ayni formda izlenir.
- **Planlanan yerlesim ana ekranda** — bekleyen adimlar harita ve tabloya
  islenir; yeni bolum belirir, silinecek kalkar. "Uygula" penceresi adimlari
  sirayla ve **adim basina ilerleme cubuguyla** calistirir.

**Dosya sistemleri**
- **FAT12 / FAT16 / FAT32 ve exFAT: saf Python** — bicimlendirme ve tam okuma/yazma,
  harici arac gerekmez, uc platformda da calisir
- **ext2 / ext3 / ext4 ve NTFS: saf Python bicimlendirme** — JBD2 gunlugu, MFT,
  `$UpCase`/`$AttrDef` tablolari dahil. Oncelik sirasi: (1) isletim sisteminin kendi
  araci, (2) harici `mkfs.*`, (3) saf Python — boylece sekiz dosya sistemi de
  **uc platformda** olusturulabilir
- **NTFS okuma ve yazma (saf Python)** — MFT cozumleme, fixup dizileri, veri
  kosullari, `$ATTRIBUTE_LIST`, B+ agac dizin indeksi. Yazma: dosya/klasor
  olusturma, silme, yeniden adlandirma, `$MFT` kendiliginden buyutulur;
  `ntfsfix` temiz ve `ntfs-3g` ile baglanip dogrulandi
- Tespit: FAT, exFAT, NTFS, ext2/3/4, btrfs, XFS, F2FS, ISO9660, Linux takas
- **ext2/3/4 okuma ve yazma (saf Python)** — okuma: extent agaci, dolayli blok,
  sembolik bag izleme, 64 bit blok. Yazma: dosya/klasor olusturma, silme,
  yeniden adlandirma; **`metadata_csum` (CRC-32C saglamalar) dahil**. Her adim
  `e2fsck` ile dogrulanir; `mkfs.ext4` ciktisi ve gercek SD kart yerlesimi
  uzerinde sinanmistir
- Dosya gezgini: listeleme, uzun ad (LFN/UTF-16), okuma, yazma, klasor, silme,
  yeniden adlandirma, disa/ice aktarma, onizleme
  (sekiz dosya sisteminde de okuma ve yazma)

**Yedekleme ve klonlama**
- `.dub` yedek bicimi — sikistirmali, sifir bloklarini atlar
  (400 MB'lik bos bolum → 34 KB yedek)
- **Yedek dogrudan acilir** — geri yuklemeye gerek yok: bolumler, klasorler ve
  dosyalar salt okunur olarak gezilir, dosyalar disa aktarilabilir
- **Yedegi hedefe yazma** — yeni goruntu dosyasina veya **fiziksel diske**
  (fiziksel hedefte alti katmanli onay gecerlidir)
- Disk ve bolum klonlama, seyreklik korunarak

**Onyukleme**
- **Onyukleyici yoneticisi** — diskteki isletim sistemlerini ve onyukleme
  kodunu (GRUB 2 / GRUB Legacy / Windows / SYSLINUX / LILO) gosterir.
  Tespit **her platformda** calisir: bolumler projenin kendi dosya sistemi
  surucileriyle okunur, `mount` ve yonetici yetkisi gerekmez; goruntu
  dosyalarindaki sistemler de gorunur
- **Onyukleme kodunu kaldirma** — ilk 440 bayti sifirlar, bolum tablosunu ve
  dosyalari **korur**; bekleyen islem olarak kuyruga girer
- **GRUB yonetimi (Linux)** — kurulum, onyukleme menusunu yeniden uretme,
  `os-prober`i acma, ayarlari yedekleme/geri yukleme ve tek dugmeyle onarim.
  Onarim GRUB'u yalnizca **zaten bulundugu** diske yazar
- **UEFI onyukleme duzenleyici** — bellenimdeki girisleri listeler, sirayi
  degistirir, etkin/gizli bayragini ve menu bekleme suresini ayarlar.
  Aygit yolu `efibootmgr` bicimindedir. Hicbir sey aninda yazilmaz: once
  degisiklik listesi gosterilir, sonra **yedek alinir**, sonra yazilir
- Onyukleme duzenini JSON olarak disa/ice aktarma

**Veri kurtarma**
- Silinmis dosya tarama ve kurtarma (FAT + exFAT, **uzun adlar dahil**),
  kurtarilabilirlik degerlendirmesi
- Kayip bolum tarama ve tabloya geri ekleme
- Imza tabanli dosya kurtarma (carving) — 13 dosya turu

**Bakim**
- Guvenli silme: sifir / rastgele / DoD 3 gecis / DoD 7 gecis + dogrulama
- Bos alan silme (mevcut dosyalara dokunmadan)
- Sektor onaltilik goruntuleyici, islem gunlugu

## Kurulum

```bash
pip install PyQt5        # tum platformlar
python3 main.py
```

Linux'ta dagitim paketi de kullanilabilir: `sudo apt install python3-pyqt5`

Opsiyonel — yalnizca NTFS/ext bicimlendirmesi ve capraz dogrulama icin (Linux):
```bash
sudo apt install dosfstools exfatprogs ntfs-3g e2fsprogs
```

**Windows ve macOS'ta** sekiz dosya sistemi de harici arac olmadan olusturulabilir;
bicimlendirme penceresi kullanilamayan bir secenegi gizlemez, yanina **nedenini** yazar.

Fiziksel disklere erisim yonetici/root yetkisi ister. Yetki yoksa diskler yine
listelenir ama acilamaz ve anlamli bir hata verilir.

## Kullanim

```bash
python3 main.py                 # bos baslat
python3 main.py disk.img        # dosya acarak baslat
```

**Dil.** Arayuz Turkce, Ingilizce ve Almanca calisir. Secim
**Araclar > Dil** menusundedir ve **aninda** uygulanir — uygulama yeniden
baslatilmaz, acik disk ve bekleyen islem kuyrugu kaybolmaz. Secim saklanir.
Acilisda sira: `DISKULTIMATE_LANG` > kayitli secim > isletim sisteminin dili >
Turkce.

```bash
DISKULTIMATE_LANG=en python3 main.py    # arayuzu Ingilizce ac
```

Yeni dil eklemek icin kod degistirmek gerekmez; ayrintilar:
`.claude/decisions/0027-cok-dilli-arayuz.md`.

**Wayland notu:** Qt5'in yerel Wayland eklentisinde modal pencereler bos ciziliyor,
bu yuzden uygulama Wayland oturumlarinda otomatik olarak XWayland (`xcb`) uzerinde
acilir. Zorlamak isterseniz: `DISKULTIMATE_QPA=wayland python3 main.py`

**Tipik akis:** `Dosya > Yeni goruntu...` ile boyut ve bolum tablosunu sec →
haritada bos alana sag tikla > `Yeni bolum...` → dosya sistemini sec →
`Dosya Gezgini` sekmesinden icerige dosya ekle.

## Test

```bash
python3 -m tests.run_all        # cekirdek: 28 test
python3 -m tests.platform_check # capraz platform denetimi (beklenen: 0 bulgu)
python3 -m tests.i18n_check     # ceviri sozlukleri (eksik/bayat/yer tutucu)
python3 -m tests.diag_check     # tanilama / donma yakalayici (13/13)
python3 -m tests.ui_smoke       # arayuz: ornek goruntu + ekran goruntuleri
```

Uretilen birimler bagimsiz araclarla capraz dogrulanir: FAT icin `fsck.vfat`,
exFAT icin `fsck.exfat`, ext icin `e2fsck`, NTFS icin `ntfsfix`/`ntfsinfo`,
bolum tablolari icin `fdisk -l`, sanal diskler icin `VBoxManage showhdinfo`.
Fiziksel disk testleri ana makinede degil, VirtualBox misafirine eklenen **bos bir
sanal disk** uzerinde yapilir (`tests/physical_probe.py`, `tests/physical_write_test.py`).
Testler proje icindeki `.tmp/` klasorunde calisir (`/tmp` kullanilmaz); temizlik
icin `rm -rf .tmp`.

## Proje belgeleri

Tum tasarim notlari, kararlar ve is gunlugu depo icindeki `.claude/` klasorundedir:

- [`.claude/docs/diskgenius-parity.md`](.claude/docs/diskgenius-parity.md) — **DiskGenius ozellik karsilastirmasi**
- [`.claude/docs/feature-analysis.md`](.claude/docs/feature-analysis.md) — pazar ve kaynak analizi
- [`.claude/docs/platform-matrix.md`](.claude/docs/platform-matrix.md) — **platform / bicim yetenek matrisi** (okuma-yazma, kutuphaneler)
- [`.claude/docs/cross-platform.md`](.claude/docs/cross-platform.md) — capraz platform notlari
- [`.claude/docs/project-overview.md`](.claude/docs/project-overview.md) — kapsam ve yol haritasi
- [`.claude/docs/architecture.md`](.claude/docs/architecture.md) — modul yapisi ve veri akisi
- [`.claude/docs/worklog.md`](.claude/docs/worklog.md) — is gunlugu
- [`.claude/docs/testing.md`](.claude/docs/testing.md) — test yontemi
- [`.claude/decisions/`](.claude/decisions/) — teknik kararlar (ADR)
- [`.claude/specs/`](.claude/specs/) — MBR / GPT / FAT bicim notlari

## Guvenlik

Fiziksel disk erisimi alti katmanli bir kapidan gecer ve bu katmanlar gevsetilmez:

1. **Listeleme zararsizdir** — hicbir sektor okunmaz, hicbir yazma yapilmaz.
   (Windows'ta boyut/model yalnizca aygit tutamaci uzerinden sorgulanabildigi
   icin salt okunur bir tutamac acilip hemen kapatilir; veri okunmaz.)
2. **Varsayilan salt okunur** — yazma icin `readonly=False` *ve* `confirm=True`.
3. **Sistem diski** — ayrica `allow_system=True`; arayuzde kullanici disk adini
   yazarak dogrular.
4. **Bilgisi eksik disk** (yetki yok) — yazma **reddedilir**; "bilinmiyor" durumu
   asla "risk yok" gibi sunulmaz.
5. **Bagli bolum** varsa yazma oncesi ayrica uyarilir.
6. Yeni bir yikici islem eklenirken bu katmanlardan gectigi **test edilir**.

Bicimlendirme, silme ve boyutlandirma onay ister; basarisiz bicimlendirme bolum
tablosunu eski haline dondurur. Bolum kucultme, dosya sisteminin verisi sigmiyorsa
**hicbir onayla** yapilmaz — reddedilir.

Gerekceler: [`.claude/decisions/0014-fiziksel-disk-destegi.md`](.claude/decisions/0014-fiziksel-disk-destegi.md),
[`.claude/decisions/0019-bolum-boyutlandirma.md`](.claude/decisions/0019-bolum-boyutlandirma.md)

## Lisans

[GNU General Public License v3.0](LICENSE) — bu yazilimi kullanabilir, degistirebilir
ve dagitabilirsiniz; turetilen calismalar da ayni lisansla **acik kaynak** kalmak
zorundadir.
