# DiskUltimate

DiskGenius benzeri gorsel disk yonetim araci: disk goruntuleri, sanal diskler ve
**sistemdeki gercek diskler** uzerinde calisir. Python 3 + PyQt5, harici bagimlilik yok.

![surum](https://img.shields.io/badge/surum-0.3.0-blue) ![python](https://img.shields.io/badge/python-3.8%2B-green) ![platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey) ![test](https://img.shields.io/badge/test-18%2F18-brightgreen)

Bolum tablolari ve dosya sistemleri **sifirdan, saf Python ile** yazilir — FAT12/16/32,
exFAT, ext2/3/4 ve NTFS dahil. Uretilen birimler `fsck.vfat`, `fsck.exfat`, `e2fsck`,
`ntfsfix` ve Windows `chkdsk` ile capraz dogrulanir.

## Yetenekler

**Disk ve bolum**
- Seyrek goruntu olusturma, acma, yeniden boyutlandirma
- Sanal disk destegi: **VHD, VDI, VMDK, QCOW2** okuma; VHD olusturma ve yazma
- **Fiziksel diskler** — takili her disk listelenir ve acilir (Windows / Linux / macOS);
  USB bellek ve SD kart uygulama acikken takilsa da listede **kendiliginden** belirir
- MBR (4 birincil + genisletilmis + mantiksal) ve GPT (128 bolum, CRC32, yedek baslik)
- **MBR ↔ GPT donusumu** — bolum verileri yerinde kalir
- **Bolumu boyutlandirma / tasima** — fareyle suruklenebilir serit; FAT ve exFAT
  veri korunarak kucultulur/buyutulur, NTFS ve ext Windows'ta yerel araca devredilir
- 4K hizalama denetimi

**Dosya sistemleri**
- **FAT12 / FAT16 / FAT32 ve exFAT: saf Python** — bicimlendirme ve tam okuma/yazma,
  harici arac gerekmez, uc platformda da calisir
- **ext2 / ext3 / ext4 ve NTFS: saf Python bicimlendirme** — JBD2 gunlugu, MFT,
  `$UpCase`/`$AttrDef` tablolari dahil. Oncelik sirasi: (1) isletim sisteminin kendi
  araci, (2) harici `mkfs.*`, (3) saf Python — boylece sekiz dosya sistemi de
  **uc platformda** olusturulabilir
- Tespit: FAT, exFAT, NTFS, ext2/3/4, btrfs, XFS, F2FS, ISO9660, Linux takas
- Dosya gezgini: listeleme, uzun ad (LFN/UTF-16), okuma, yazma, klasor, silme,
  yeniden adlandirma, disa/ice aktarma, onizleme

**Yedekleme ve klonlama**
- `.dub` yedek bicimi — sikistirmali, sifir bloklarini atlar
  (400 MB'lik bos bolum → 34 KB yedek)
- **Yedek dogrudan acilir** — geri yuklemeye gerek yok: bolumler, klasorler ve
  dosyalar salt okunur olarak gezilir, dosyalar disa aktarilabilir
- **Yedegi hedefe yazma** — yeni goruntu dosyasina veya **fiziksel diske**
  (fiziksel hedefte alti katmanli onay gecerlidir)
- Disk ve bolum klonlama, seyreklik korunarak

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

**Wayland notu:** Qt5'in yerel Wayland eklentisinde modal pencereler bos ciziliyor,
bu yuzden uygulama Wayland oturumlarinda otomatik olarak XWayland (`xcb`) uzerinde
acilir. Zorlamak isterseniz: `DISKULTIMATE_QPA=wayland python3 main.py`

**Tipik akis:** `Dosya > Yeni goruntu...` ile boyut ve bolum tablosunu sec →
haritada bos alana sag tikla > `Yeni bolum...` → dosya sistemini sec →
`Dosya Gezgini` sekmesinden icerige dosya ekle.

## Test

```bash
python3 -m tests.run_all        # cekirdek: 18 test
python3 -m tests.platform_check # capraz platform denetimi (beklenen: 0 bulgu)
python3 -m tests.ui_smoke       # arayuz: ornek goruntu + 18 ekran goruntusu
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
