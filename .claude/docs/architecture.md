# Mimari

```
main.py                       Giris noktasi (QApplication + MainWindow)
└── src/diskultimate/
    ├── paths.py              Proje ici yollar (.tmp, .claude/logs)
    ├── core/                 SAF PYTHON — PyQt import etmez
    │   ├── platform.py       Isletim sistemi farklari (seyrek dosya, arac arama, Qt eklentisi)
    │   ├── image.py          BlockDevice arayuzu, DiskImage, PartitionView
    │   ├── vdisk.py          Sanal diskler: VHD / VDI / VMDK / QCOW2
    │   ├── ptable.py         Partition/FreeRegion modeli, tip tablolari, boyut yardimcilari
    │   ├── mbr.py            MBRTable: oku/yaz/olustur, EBR zinciri
    │   ├── gpt.py            GPTTable: oku/yaz/olustur, CRC32, yedek baslik
    │   ├── convert.py        MBR ↔ GPT donusumu, 4K hizalama raporu
    │   ├── fat.py            FatFS: FAT12/16/32 bicimlendirme + tam dosya erisimi
    │   ├── exfat.py          ExFatFS: exFAT bicimlendirme + tam dosya erisimi
    │   ├── fsdetect.py       Imza tabanli dosya sistemi tespiti (FSInfo)
    │   ├── formatter.py      Bicimlendirme dagiticisi (dahili FAT/exFAT + harici mkfs)
    │   ├── filesystem.py     FileSystemAccess arayuzu, FatAccess, ExFatAccess
    │   ├── clone.py          .dub yedek bicimi, geri yukleme, klonlama
    │   ├── wipe.py           Guvenli silme (sifir/rastgele/DoD), bos alan silme
    │   ├── recovery.py       Silinmis dosya, kayip bolum, imza tabanli kurtarma
    │   └── session.py        DiskSession — GUI'nin gordugu tek cephe
    └── ui/                   YALNIZCA SUNUM — disk bicimi bilgisi icermez
        ├── theme.py          Renk paleti, dosya sistemi renkleri, ikon temasi, stil
        ├── main_window.py    Menu, arac cubugu, agac, yerlesim, is akislari
        ├── widgets/
        │   ├── disk_map.py        Gorsel bolum haritasi (QPainter)
        │   ├── partition_table.py Bolum listesi tablosu
        │   ├── file_browser.py    Klasor agaci + dosya listesi + islemler
        │   └── hex_view.py        Sektor onaltilik goruntuleyici
        └── dialogs/
            ├── base.py            exec_dialog — guvenli diyalog gosterimi
            ├── new_image.py       Yeni goruntu / sanal disk sihirbazi
            ├── partition.py       Bolum olusturma + bicimlendirme
            ├── tools.py           Silme, kurtarma, imza tarama, bilgi pencereleri
            ├── task.py            QThread + ilerleme penceresi
            └── preview.py         Dosya onizleme (metin / onaltilik)
```

## Veri akisi

```
Kullanici -> MainWindow -> DiskSession -> PartitionTable (MBR/GPT) -> DiskImage -> .img
                              |
                              +-------> PartitionView -> FatFS / mkfs -> bolum alani
                              |
                              +-------> FileSystemAccess -> FileBrowser
```

**Onemli kural:** GUI hicbir zaman `MBRTable`, `GPTTable`, `FatFS` ile dogrudan
konusmaz; her sey `DiskSession` uzerinden gecer. Boylece yeni bir sema veya dosya
sistemi eklendiginde arayuz kodu degismez.

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
| Yeni arayuz paneli | `ui/widgets/` + `main_window.tabs` |
| Isletim sistemi farki | **yalnizca** `core/platform.py` |
