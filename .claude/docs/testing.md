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

Ornek bir 4 GB / dort bolumlu goruntu uretir ve `<proje>/.tmp/screenshots` altina
**on sekiz PNG** kaydeder: ana pencere, sekmeler, coklu goruntu ve diyaloglar
(bolum boyutlandirma penceresi ve suruklemesi dahil). Arayuzde degisiklik
yapildiginda bu goruntuler gozle denetlenir — ozellikle:
- disk haritasinda metin ile doluluk cubugunun cakismamasi
- arac cubugu ve dosya listesi **ikonlarinin gorunur** olmasi (koyu ikon temasi tuzagi)
- acilir liste / sayi kutusu oklarinin cizilmesi (stil sayfasi tuzagi)
- dugme metinlerinin Turkce olmasi
- **sekme basliklarinin kirpilmamasi** (stil sayfasi font-weight tuzagi, ADR 0012)

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
- ext2/3/4 ve NTFS icerikleri listelenemiyor; **bicimlendirme** (saf Python) ve
  tespit calisir. FAT ve exFAT icerigi tam desteklenir. Okuyucular v0.4 hedefi.
- **macOS uzerinde testler hic calistirilmadi**; yalnizca statik denetimden gecti.
  Windows uzerinde kosuldu — hangi surumun nerede kosuldugu icin
  [cross-platform.md](cross-platform.md) test tablosuna bakin.
- Silinmis dosya kurtarma, veriyi **ardisik** varsayar; parcalanmis dosyalarda
  sonuc eksik olabilir (arayuzde "kurtarilabilirlik" yuzdesi bunu belirtir).
- 2 TiB uzeri MBR bolumleri desteklenmez (bicim sinirlamasi); GPT kullanilmalidir.
- Uygulama Wayland oturumlarinda XWayland (`xcb`) uzerinde acilir; yerel Wayland
  eklentisinde modal pencereler bos cizilir (bkz. ADR 0005).
