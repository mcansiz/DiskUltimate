# Mimari

```
main.py                       Giris noktasi (QApplication + MainWindow)
└── src/diskultimate/
    ├── paths.py              Proje ici yollar (.tmp, .claude/logs)
    ├── i18n/                 COK DILLI METINLER — saf Python, Qt'siz
    │   ├── __init__.py       tr() / trn() / trc() / mark(), dil secimi, bildirim
    │   ├── po.py             gettext `.po` okuyucu/yazici + msgmerge esdegeri
    │   └── catalogs/         <dil>.po sozlukleri (tr kaynak dildir, dosyasi yok)
    ├── core/                 SAF PYTHON — PyQt import etmez
    │   ├── platform.py       Isletim sistemi farklari (seyrek dosya, arac arama, Qt eklentisi)
    │   ├── physical.py       Fiziksel diskler: listeleme, acma, katmanli yazma guvenligi
    │   ├── image.py          BlockDevice arayuzu, DiskImage, PartitionView
    │   ├── vdisk.py          Sanal diskler: VHD / VDI / VMDK / QCOW2
    │   ├── ptable.py         Partition/FreeRegion modeli, tip tablolari, boyut yardimcilari
    │   ├── mbr.py            MBRTable: oku/yaz/olustur, EBR zinciri
    │   ├── gpt.py            GPTTable: oku/yaz/olustur, CRC32, yedek baslik
    │   ├── convert.py        MBR ↔ GPT donusumu, 4K hizalama raporu
    │   ├── fat.py            FatFS: FAT12/16/32 bicimlendirme + tam dosya erisimi
    │   ├── exfat.py          ExFatFS: exFAT bicimlendirme + tam dosya erisimi
    │   ├── ext.py            ext2/3/4 saf Python bicimlendirme (JBD2 gunlugu dahil)
    │   ├── extread.py        ext2/3/4 okuyucu (extent + dolayli blok, sembolik bag)
    │   ├── extwrite.py       ext2/3/4 yazici (bitmap tahsisi, dizin girisi, sayaclar)
    │   ├── extcsum.py        ext4 metadata_csum saglamalari (hesap + dogrulama)
    │   ├── crc32c.py         CRC-32C (Castagnoli) — saf Python
    │   ├── ntfs.py           NTFS saf Python bicimlendirme (MFT, $UpCase, $AttrDef)
    │   ├── ntfsread.py       NTFS okuyucu (MFT, fixup, veri kosullari, B+ indeks)
    │   ├── ntfswrite.py      NTFS yazici ($Bitmap/$MFT tahsisi, INDX giris ekleme)
    │   ├── ntfsresize.py     NTFS kucultme/buyutme (kume tasima, $Bitmap, $BadClus)
    │   ├── _ntfs_data.py     NTFS icin gomulu sabit tablolar
    │   ├── resize.py         Bolum boyutlandirma / tasima (FAT + exFAT + NTFS yerlesimi)
    │   ├── fsdetect.py       Imza tabanli dosya sistemi tespiti (FSInfo)
    │   ├── formatter.py      Bicimlendirme dagiticisi (sekiz FS; yerel arac → mkfs → saf Python)
    │   ├── filesystem.py     FileSystemAccess arayuzu; FatAccess, ExFatAccess, ExtAccess
    │   ├── clone.py          .dub yedek bicimi (kullanici notu dahil), geri yukleme, klonlama
    │   ├── wipe.py           Guvenli silme (sifir/rastgele/DoD), bos alan silme
    │   ├── recovery.py       Silinmis dosya, kayip bolum, imza tabanli kurtarma
    │   ├── diagnostics.py    Gunluk, sure olcumu (span), donma yakalayici, cokme dokumu
    │   ├── operations.py     Bekleyen islem kuyrugu (planla -> Uygula)
    │   ├── planview.py       Kuyruk uygulaninca olusacak yerlesim (ana ekran onizlemesi)
    │   ├── bootloader.py     Onyukleme kodu tanima, bolumdeki sistemi bulma, grub.cfg cozumu
    │   ├── grub.py           GRUB kurulumu/menu uretimi/yedek/onarim (yalnizca Linux)
    │   ├── efiboot.py        UEFI yapilari: EFI_LOAD_OPTION, aygit yolu, kisayol (spec)
    │   ├── efistore.py       UEFI duzeninin okunmasi, yedeklenmesi, degisiklik plani, yazma
    │   ├── settings.py       Kullanici tercihleri (dil) — isletim sisteminin ayar klasoru
    │   └── session.py        DiskSession — GUI'nin gordugu tek cephe
    └── ui/                   YALNIZCA SUNUM — disk bicimi bilgisi icermez
        ├── theme.py          Dosya sistemi renkleri, palet yardimcilari, isletim sistemi ikonlari
        ├── icons.py          Uygulamanin kendi ikon seti (QPainter ile cizilir)
        ├── diag.py           Tanilamanin Qt tarafi: nabiz, Qt uyarilari, istisna kancasi
        ├── disk_scan.py      Fiziksel disk listesini arka planda toplayan QThread
        ├── main_window.py    Menu, arac cubugu, agac, yerlesim, is akislari
        ├── widgets/
        │   ├── disk_map.py        Gorsel bolum haritasi (QPainter)
        │   ├── partition_table.py Bolum listesi tablosu
        │   ├── file_browser.py    Klasor agaci + dosya listesi + islemler
        │   ├── disk_overview.py   Butun disklerin genel bakisi (Acronis duzeni)
        │   ├── resize_bar.py      Suruklenebilir boyutlandirma seridi
        │   └── hex_view.py        Sektor onaltilik goruntuleyici (salt okunur)
        └── dialogs/
            ├── base.py            exec_dialog — guvenli diyalog gosterimi
            ├── new_image.py       Yeni goruntu / sanal disk sihirbazi
            ├── partition.py       Bolum olusturma + bicimlendirme
            ├── resize.py          Bolum boyutlandirma / tasima penceresi
            ├── tools.py           Silme, kurtarma, imza tarama, yedek icerigi, bilgi pencereleri
            ├── bootloader.py      Onyukleyici yoneticisi (GRUB, onyukleme kodu)
            ├── efiboot.py         UEFI onyukleme duzenleyici (girisler, sira, yedek)
            ├── apply.py           Bekleyen adimlari sirayla uygulayan pencere
            ├── backup.py          Yedek al / geri yukle / yedegi incele (tek form)
            ├── task.py            QThread + ilerleme penceresi
            └── preview.py         Dosya onizleme (metin / onaltilik)
```

## Veri akisi

```
Kullanici -> MainWindow -> DiskSession -> PartitionTable (MBR/GPT) -> BlockDevice
                              |                                            |
                              |                     DiskImage (.img) / VhdImage vb.
                              |                     PhysicalDisk (\\.\PhysicalDriveN, /dev/sdX)
                              |
                              +-------> PartitionView -> FatFS / ExFatFS / ExtFS -> bolum alani
                              |
                              +-------> FileSystemAccess -> FileBrowser
                              |
                              +-------> resize.apply_resize -> bolum yerlesimi
```

**Onemli kural:** GUI hicbir zaman `MBRTable`, `GPTTable`, `FatFS` ile dogrudan
konusmaz; her sey `DiskSession` uzerinden gecer.

**Yikici islemler iki asamalidir** (ADR 0025):

```
Kullanici -> MainWindow -> OperationQueue     (diske DOKUNULMAZ)
                                |
                          "Uygula" + tek onay
                                v
                    DiskSession.become_writable()      <- fiziksel disk kapilari
                                v
                    queue.apply(session) -> DiskSession -> BlockDevice
```

Kaynak hep salt okunur acilir; yazma yetkisi yalnizca uygulama suresince alinir.
Kuyruk bos kaldiginda hicbir aygit yazilabilir acik kalmaz. Boylece yeni bir sema veya dosya
sistemi eklendiginde arayuz kodu degismez.

## Arayuz is parcacigi kurali (ZORUNLU)

Arayuz is parcaciginda **suresi ongorulemeyen is yapilmaz.** Bir is su uclerden
birine dokunuyorsa suresi ongorulemez sayilir:

- isletim sisteminin aygit arayuzleri (disk sayimi, birim acma, IOCTL),
- gercek disk G/C (uyuyan disk, yavas USB, cikarilmakta olan kart),
- tum bolumu / tum diski tarayan isler (kurtarma, silme, klonlama).

Bu isler ya `dialogs/task.run_task` (ilerlemeli, iptal edilebilir) ya da bir
`QThread` icinde calisir; sonuc sinyal ile arayuze doner. Kural bir kez
cignenmisti: fiziksel disk sayimi 3 saniyede bir arayuz is parcaciginda
calisiyordu ve tek bir `CreateFileW` cagrisi pencereyi kilitliyordu
(ADR 0020).

**Ikinci kural — ayni aygita iki yerden dokunulmaz.** Isi arka plana almak tek
basina yetmez: cakisma **aygit duzeyindedir**. Uygulamanin acik tuttugu bir
diske ikinci bir tutamac acip IOCTL sormak surucu yiginini dakikalarca asili
birakabilir ve o sirada asil is parcaciginin okumalari da ayni kuyruga takilir.
Bu yuzden acik aygitlar `core/physical.py` icinde kutuklenir ve `list_disks()`
onlara dokunmaz (ADR 0021).

Kutugun sinirini bilmek gerekir: **acilis sirasindaki hicbir adim aygitin
kutukteki durumuna bagli olmamalidir.** Kutuk *disaridan* gelen yoklamalari
durdurmak icindir. Bu bir kez cignendi: Windows birim kilidi birim harflerini
listelemeye soruyordu, listeleme de kutuktekileri atliyordu; sonuc olarak hicbir
birim kilitlenmedi ve **her yazma** reddedildi (ADR 0022).

**Ucuncu kural — uzun is sessiz kalmaz.** Kullanicinin baslattigi her uzun
islem ilerleme penceresi gosterir. Yuzde hesaplanamiyorsa `report(mesaj, -1)`
ile belirsiz (hareketli) cubuk kullanilir; 0'da duran cubuk "takildi"
izlenimi verir.

## Tanilama akisi

```
arayuz is parcacigi                arka plan
------------------                 ---------
QTimer (200 ms) --> beat() ----->  Watchdog
                                      |  nabiz 1.5 sn gelmezse
                                      v
                          .claude/logs/freeze/freeze-<zaman>.md
                          (acik islem + butun yiginlar + son islemler)

span("disk.read", ...) ---------> .claude/logs/runtime/session-<zaman>.log
                                  (200 ms ustu YAVAS olarak isaretlenir)
```

`core/diagnostics.py` Qt bilmez; nabiz, Qt uyarilari ve istisna kancasi
`ui/diag.py` icindedir. Arayuzden erisim: **Araclar > Tanilama**.

## Cok dilli metin akisi (ADR 0027)

```
kaynak metin (Turkce)            catalogs/en.json
   tr("Bolumu sil")   ------>    {"Bolumu sil": "Delete partition"}
        |                                  |
        +---------- i18n.tr() -------------+
                        |
                  gosterilen metin
```

- **Kaynak dil Turkce**: kodun icindeki metin hem yazi hem ceviri anahtaridir.
  Sozluk yuklenmese bile arayuz anlamli calisir.
- `core/` de `tr()` kullanir (hata mesajlari, ilerleme bildirimleri); `i18n`
  saf Python oldugu icin katman kurali bozulmaz.
- Modul duzeyinde uretilen metinler (`MBR_TYPES`, `WIPE_METHODS`,
  `operations.KINDS`) `mark()` ile **isaretlenir**, gosterim aninda `tr()` ile
  cevrilir: dil degisince guncellenmeleri baska turlu mumkun olmazdi.
- Dil degisince uygulama yeniden baslatilmaz. `i18n.add_listener` ->
  `MainWindow.retranslate()` butun kalici bilesenleri yerinde tazeler;
  diyaloglar zaten her acilista kurulur.
- **Gorunen metin karar girdisi degildir:** metnin icinde arama yapan iki yer
  (`"kilitlenmis" in reason`, `"[YIKICI]" in line`) veriye tasindi
  (`DiskImage.readonly_locked`, `OperationQueue.describe_rows()`).

## Onyukleme akisi (ADR 0028 / 0029)

Onyukleme isleri **inceleme** ve **degistirme** olarak ikiye ayrilir; ayrimin
karsiligi hangi platformda ne calistigidir.

```
                    ┌──────────────────────────────────────────┐
  HER PLATFORM      │ core/bootloader.py                       │
  (yazma yok)       │  identify_boot_code()  ilk 440 bayt      │
                    │  detect_os()           kendi FS suruculeri│
                    │  survey_session()      bolum bolum       │
                    │  GrubDefaults / parse_grub_cfg()          │
                    └──────────────────────────────────────────┘
                                     │
          ┌──────────────────────────┼──────────────────────────┐
          ▼                          ▼                          ▼
 ┌─────────────────┐      ┌────────────────────┐    ┌─────────────────────┐
 │ operations.py   │      │ core/grub.py       │    │ core/efistore.py    │
 │ clear_boot_code │      │ install / update   │    │ load / backup /     │
 │  → KUYRUGA      │      │ repair / restore   │    │ changes / apply     │
 │  (yikici)       │      │  → yalnizca Linux  │    │  → bellenim         │
 └─────────────────┘      └────────────────────┘    └─────────────────────┘
          │                          │                          │
          └──────────────┬───────────┴──────────────┬───────────┘
                         ▼                          ▼
              core/platform.py (OS farkinin TEK durdugu yer)
              find_boot_tool · run_privileged · write_system_file
              firmware_type · efivar_read/write/delete/names
```

Uc kural:

1. **Inceleme diske yazmaz** ve `mount` gerektirmez; goruntu dosyalarinda da
   calisir. Bu yuzden testleri aygit olmadan kosar.
2. **Sektor yazan is kuyruga girer.** Onyukleme kodunun kaldirilmasi
   `session.clear_boot_code()` uzerinden `_require_writable()`e ugrar ve
   fiziksel diskin butun koruma katmanlarindan gecer. `grub-install` sektor
   yazmaz, calisan sisteme is yaptirir; kuyruga girmez.
3. **Bellenim diskten ayridir.** UEFI degiskenleri anakart uzerindedir; ne
   `DiskSession` ne de kuyruk onlari gorur. Kendi plan/onay/yedek akisi
   vardir ve yazmadan once yedek **zorunludur**.

## Adresleme sozlesmesi
- Tum bolum sinirlari **LBA (sektor)** cinsindendir.
- Bayta cevirme yalnizca `BlockDevice` uygulamalarinda yapilir.
- `PartitionView`, bolumun disk icindeki yerini kapsuller: dosya sistemi kodu her
  zaman 0 ofsetinden basladigini varsayar.
- Bolum hizalama 1 MiB (`ptable.ALIGN_BYTES`), yani 512 bayt sektorde 2048 sektor.

## Genisletme noktalari

| Eklenecek sey | Dokunulacak yer |
|---|---|
| Yeni dosya sistemi bicimlendirme | `formatter.FS_KINDS` + gerekirse `_build_command` |
| Yeni dosya sistemi okuma | `filesystem.py` icinde yeni `FileSystemAccess` sinifi + `open_filesystem` |
| Yeni bolum semasi | `ptable.PartitionTable` turevi + `session.reload/create_table` |
| Yeni imza tespiti | `fsdetect.detect` |
| Yeni kapsayici bicimi (sanal disk) | `vdisk.py` icinde `_BaseVirtualDisk` turevi + `detect_format` |
| Yeni kurtarma dosya turu | `recovery.SIGNATURES` listesine bir giris |
| Yeni silme yontemi | `wipe.WIPE_METHODS` listesine bir giris |
| Yeni dosya sistemi boyutlandirma | `resize.fs_resize_info` + `resize.apply_resize` dagiticisi |
| Yeni fiziksel disk platformu | `physical.py` icinde listeleme/acma dali + `platform.py` |
| Yeni arayuz paneli | `ui/widgets/` + `main_window.tabs` |
| Isletim sistemi farki | **yalnizca** `core/platform.py` |
| Yeni uzun surecek islem | `dialogs/task.run_task` veya `QThread` — arayuz is parcaciginda **degil** |
| Yeni olcum noktasi | `diagnostics.span(...)` / `@diagnostics.timed(...)` |
| Yeni yikici islem | `operations.KINDS` + `RUNNERS` + bir `*_op()` uretici; arayuz `enqueue()` cagirir |
| Yeni ikon | `ui/icons.py` icinde bir cizim islevi + `DRAWERS` girisi |
| Yeni dil | `i18n/catalogs/<kod>.json` + `i18n.LANGUAGE_NAMES` girisi (ADR 0027; ayrinti: `.claude/docs/i18n-raporu.md`) |
| Yeni arayuz metni | `tr("...")` ile sarilir; modul duzeyindeyse `mark("...")` |
| Yeni `QAction` | `_build_actions()` kurar, metni `_retranslate_actions()` yazar |
| Diskten acilmadan okunacak yeni bilgi | `DiskSession.survey_disk` (salt okunur, aygiti hemen birakir) |
