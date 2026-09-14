# ADR 0016 — Windows'ta ham yazma icin birim kilitleme

**Tarih:** 2026-09-13 · **Durum:** Uygulandi · **Dogrulama:** Win10 misafirinde olculdu

## Belirti
ADR 0015 duzeltmesinden sonra Windows'ta yazma testi 5. adima kadar geldi, sonra:

```
[6] exFAT ile yeniden bicimlendirme
DISK HATASI: Yazma reddedildi ... (ERROR_ACCESS_DENIED)
```

## Kok neden
Adim 5'te disk kapatilirken `rescan_partitions()` calisti ve Windows yeni yazilan
FAT32 bolumunu tanidi — ona **`E:` surucu harfi atayip bagladi**. Windows, bagli
bir birimin sektorlerine dogrudan (ham) yazmayi **reddeder**; birimi kullanan
dosya sistemi surucusu ile cakismayi onlemek icin.

Yani kendi duzeltmemizin basarisi (Windows'un bolumu tanimasi) bir sonraki adimi
engelledi. Bu, disk araclarinin hepsinin karsilastigi standart bir kisittir.

## Karar
Disk **yazma modunda acilirken** o diskteki tum birimler kilitlenir ve baglantisi
kesilir (`PhysicalDisk._win_lock_volumes`):

1. Disk numarasindan surucu harfleri bulunur
   (`IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS`).
2. Her birim icin `\\.\X:` yazma erisimiyle acilir.
3. `FSCTL_LOCK_VOLUME` (0x00090018) — birim kilitlenir.
4. `FSCTL_DISMOUNT_VOLUME` (0x00090020) — baglanti kesilir.
5. Tutamac **acik tutulur**: kilit ancak tutamac kapaninca serbest kalir, bu
   sayede baska bir surec birimi yeniden baglayamaz.
6. `close()` sirasinda once kilitler birakilir (`FSCTL_UNLOCK_VOLUME`), sonra
   `rescan_partitions()` cagrilir; boylece isletim sistemi yeni yapiyi taniyip
   birimleri yeniden baglayabilir.

Kilitlenemeyen birim varsa tutamac birakilir ve islem sürer; yazma zaten
anlamli bir hata ile reddedilir.

## Kullanici acisindan sonuc
Bir disk yazma modunda acildiginda uzerindeki birimler **gecici olarak erisilemez**
olur (DiskGenius ve benzerlerinde de boyledir). Bu yuzden yazma modu arayuzde ayri
bir onay ister ve bagli bolum varsa ek uyari gosterilir.

## Dogrulama (Win10 misafiri, `\\.\PhysicalDrive1`)
Onceki testin olusturdugu birim `E:` olarak bagliyken test yeniden calistirildi:

```
[6] exFAT ile yeniden bicimlendirme  -> exFAT, dosya: b'exFAT da calisiyor\n'
[7] rescan cagrisi: basarili
    Number: 1   PartitionStyle: GPT
    PartitionNumber DriveLetter Size       -> 1  E  536870912
    DriveLetter FileSystemLabel FileSystem -> E  DUEXFAT  exFAT
```

Windows diski **GPT** olarak tanidi, bolume surucu harfi atadi ve **saf Python
exFAT surucumuzun yazdigi birimi exFAT olarak bagladi**.

## Ek: test olcutu
`tests/physical_write_test.py` icine `--bagli-birimlere-izin-ver` bayragi eklendi.
Yalnizca "bagli bolum" olcutunu gevsetir (onceki kosumun birimi bagli kalmis
olabilir); sistem diski korumasi dahil diger bes olcut aynen gecerlidir.
