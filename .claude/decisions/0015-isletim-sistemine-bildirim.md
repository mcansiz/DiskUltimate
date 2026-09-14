# ADR 0015 — Fiziksel diske yazdiktan sonra isletim sistemine bildirim

**Tarih:** 2026-09-13 · **Durum:** Uygulandi · **Dogrulama:** Pop!_OS misafirinde olculdu

## Belirti
Windows misafirinde fiziksel disk yazma testi **basariyla** tamamlandi: GPT tablosu
yazildi, FAT32 ve exFAT bolumler olusturuldu, dosyalar yazilip geri okundu, disk
kapatilip yeniden acildiginda veri yerindeydi.

Ancak hemen ardindan Windows'un kendi araci diski hala **RAW** gosteriyordu:

```
Number FriendlyName  GB PartitionStyle IsSystem
     1 VBOX HARDDISK  2 RAW               False
```

`Get-Partition -DiskNumber 1` hicbir bolum dondurmedi.

## Kok neden
Ham (raw) blok yazma, isletim sisteminin **bolum tablosu onbellegini** guncellemez.
Cekirdek/disk yoneticisi diski acildigi andaki haliyle tanimaya devam eder:

- **Windows:** disk "RAW" kalir, Gezgin'de birim gorunmez.
- **Linux:** `/dev/sdb1` gibi bolum aygitlari olusmaz veya eski haliyle kalir.

Gercek kullanimda bunun sonucu agirdir: kullanici diski bicimlendirir, islem
"basarili" der, ama birim hicbir yerde gorunmez — arac bozuk sanilir.

Bu kusur **yalnizca gercek donanimda** ortaya cikti; goruntu dosyalariyla yapilan
tum testler bunu gormezdi, cunku goruntu dosyasinin isletim sistemi nezdinde bir
bolum tablosu onbellegi yoktur.

## Karar
`PhysicalDisk.rescan_partitions()` eklendi:

| Platform | Cagri |
|---|---|
| Windows | `DeviceIoControl(IOCTL_DISK_UPDATE_PROPERTIES = 0x00070140)` |
| Linux | `ioctl(fd, BLKRRPART = 0x125F)` |
| macOS | `diskutil rescan <aygit>` |

- Yalnizca **yazma modunda** anlamlidir; salt okunur diskte cagrilmaz.
- Once `flush()` yapilir, sonra bildirim gonderilir.
- Basarisizlik **olumcul degildir** (bazi sistemlerde disk mesgulken reddedilir);
  islem geri dondurulmez, yalnizca `False` doner.
- **Yalnizca kapanista cagrilir** (`PhysicalDisk.close()`), islem ortasinda DEGIL.

## Duzeltmenin ilk surumu hatasiydi: 433

Ilk uygulamada `DiskSession` bolum tablosu yazildiktan **hemen sonra** da
`notify_os()` cagiriyordu. Windows'ta bu, sonraki adimi bozdu:

```
[2] GPT bolum tablosu olusturuluyor
[3] FAT32 bolum olusturuluyor (512.00 MB)
DISK HATASI: Yazma hatasi (Windows 433)
```

`433 = ERROR_NO_SUCH_DEVICE`. Sebep: `IOCTL_DISK_UPDATE_PROPERTIES` aygiti
yeniden taratir ve o anda **acik olan disk tutamacini gecersiz kilar**. Yani
bildirim, uzerinde calisilan tutamaci elimizden aliyordu.

**Kural:** rescan yalnizca tum yazma islemleri bittikten sonra, disk kapatilirken
yapilir. `DiskSession` icinde islem ortasinda cagrilmaz.

## Dogrulama (Pop!_OS misafiri, `/dev/sdb`)
`tests/physical_write_test.py` icine 7. adim eklendi. Kosum sonucu:

```
[7] Isletim sistemine bolum tablosu degisikligi bildiriliyor
    -> rescan cagrisi: basarili
lsblk -f : └─sdb1  exfat  1.0  DUEXFAT  6E50-625E
blkid    : TYPE="exfat" PARTLABEL="DiskUltimate Test"
mount    : basarili, exfat.txt okundu
```

Bildirimden once `sdb1` aygiti yoktu; sonrasinda cekirdek bolumu olusturdu,
dosya sistemini tanidi ve bagladi.

## Ders
Goruntu dosyasi ile gercek disk arasindaki fark yalnizca "nereye yazildigi" degil:
gercek diskte **isletim sistemiyle durum esgudumu** de gerekir. Bir ozelligi
gercek donanimda calistirmadan "tamam" saymamak gerekiyor.
