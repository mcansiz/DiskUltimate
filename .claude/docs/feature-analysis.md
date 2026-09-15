# Pazar ve Kaynak Analizi — Disk Yonetim Araclari

Tarih: 2026-09-13 (5. madde 2026-09-15'te guncellendi) · Amac: DiskUltimate'in ozellik kapsamini belirlemek icin ticari
ve acik kaynak araclarin incelenmesi.

---

## 1. DiskGenius (referans urun, kapali kaynak)

Projenin hedef referansi. Ozellikleri dort ana baslikta toplaniyor:

| Baslik | Ozellikler |
|---|---|
| **Bolum yonetimi** | Olustur, sil, bicimlendir, boyutlandir, tasi, bol, birlestir, gizle, surucu harfi ata, birincil↔mantiksal donusumu, MBR↔GPT donusumu, dinamik diski temele cevirme |
| **Veri kurtarma** | Silinen/bicimlendirilmis/kayip bolumlerden dosya kurtarma, geri donusum kutusu kurtarma, **dosya turune gore kurtarma**, onizleme ve filtreleme, RAW surucu kurtarma, RAID dizilerinden kurtarma |
| **Klonlama ve yedekleme** | Disk/bolum klonlama, sektor sektor kopyalama, goruntu yedegi olusturma ve geri yukleme, Windows'u SSD'ye tasima |
| **Bakim** | Bozuk sektor denetimi ve onarimi, guvenli silme (wipe), HDD/SSD saglik izleme (S.M.A.R.T.), UEFI onyukleme girisi yonetimi, **WinHex tarzi onaltilik sektor duzenleyici**, onyuklenebilir kurtarma ortami olusturma, sanal disk (VMDK/VHD/VHDX/VDI) olusturma ve donusturme |

**Cikarim:** Ucretsiz surumun bile cekirdeginde sektor duzenleyici, klonlama ve
kurtarma var. Bir "bolum yoneticisi"nden cok bir **disk is istasyonu**.

## 2. Acik kaynak GUI araclari

| Arac | Guclu yani | Bizim icin ders |
|---|---|---|
| **GParted** (GNOME) | libparted uzerine kurulu, en genis dagitim destegi; boyutlandirma/tasima/kopyalama | Bolum boyutlandirma, harici FS araclarini cagirma modeli |
| **KDE Partition Manager** | GParted'in yaptigi her seyi yapar; ayrica LVM yonetimi, Btrfs alt birim, yazilim RAID entegrasyonu, ZFS boyutlandirma | LVM/RAID ileri asama hedefi |
| **TestDisk / PhotoRec** | Kayip bolum tablosu onarimi ve imza tabanli dosya kurtarmanin referansi; exFAT dahil genis FS destegi | **Kayip bolum tarama** ve **carving** tasarimimizin esin kaynagi |
| **Rufus / Ventoy / balenaEtcher** | Onyuklenebilir medya yazma | Ileride "goruntuyu aygita yazma" ozelligi icin model |

**Onemli ayrim:** GParted bolum **metaveri onarimini** hedeflemez; TestDisk bunun
icindir. DiskUltimate her ikisini tek arayuzde birlestirmeyi hedefliyor.

## 3. Python kutuphaneleri (kod duzeyinde inceleme)

| Proje | Kapsam | Karar |
|---|---|---|
| **FATtools** (maxpat78) | Python 3 ile FAT12/16/32 **ve exFAT** tam okuma/yazma; 1.1.0'dan itibaren salt okunur NTFS | Yaklasimi dogruladi: saf Python FAT/exFAT uygulanabilir. Biz kendi surucumuzu yazdik (bagimsizlik + Turkce arayuz/kod butunlugu). |
| **dissect.ntfs** (fox-it) | Saf Python NTFS cozumleyici (MFT) | NTFS **okuyucu** icin referans yapi; v0.4 hedefi |
| **ext4** (PyPI), **py-ext4info** | Salt okunur ext4 erisimi | ext4 okuyucu icin referans; v0.3 hedefi |
| **pytsk3** (Sleuth Kit baglayicisi) | FAT/NTFS/HFS+/ext genis destek | Reddedildi: C bagimliligi, exFAT destegi yok, "harici bagimlilik yok" ilkesine aykiri |
| **gpt-image** (swysocki), **pygpt** (pvachon) | GPT goruntu olusturma / okuma | GPT yerlesimimizle ortustugunu dogruladi |
| **hdisk** (csdvrx) | Programatik hibrit MBR+GPT duzenleyici | Hibrit MBR fikri; ileride degerlendirilecek |
| **gptfdisk** (gdisk/sgdisk) | MBR→GPT donusumunun referans uygulamasi, hibrit MBR | **MBR↔GPT donusum** kurallarimizin dogrulama olcutu |

## 4. Bu analizden cikan kararlar

1. **Saf Python FAT + exFAT zorunlu.** Windows ve macOS'ta `mkfs.*` yok; harici araca
   bagli kalmak capraz platform hedefini bastan bozar. → [ADR 0007](../decisions/0007-saf-python-exfat.md)
2. **Kurtarma bir "ek" degil, cekirdek islev.** DiskGenius'un degerinin buyuk kismi
   burada. Uc ayri yetenek olarak uygulandi: silinmis giris tarama, kayip bolum
   tarama, imza tabanli carving.
3. **Yedek bicimi kendi formatimiz olmali.** DiskGenius'un `.pmf` bicimi kapali.
   Blok tabanli, sifir-atlayan, sikistirmali `.dub` bicimi tasarlandi.
   → [ADR 0009](../decisions/0009-dub-yedek-bicimi.md)
4. **Sanal disk destegi beklenen bir ozellik.** VHD/VDI/VMDK/QCOW2 okuma eklendi;
   VHD olusturma da destekleniyor.
5. **S.M.A.R.T. kapsam disi, bozuk sektor taramasi planli.** Bu madde v0.3 ile
   **degisti**: analiz yazildiginda proje yalnizca goruntu dosyalariyla
   calisiyordu, artik fiziksel disk destegi var
   ([ADR 0014](../decisions/0014-fiziksel-disk-destegi.md)). Yeni olcut
   "fiziksel disk gerekiyor mu" degil, **"tasinabilir sekilde yazilabilir mi"**:
   - S.M.A.R.T. her platformda ayri bir ayricalikli ATA/NVMe komut yolu ister —
     kapsam disi kaldi.
   - Bozuk sektor / okuma hatasi taramasi tasinabilir sekilde yapilabilir —
     planli hale getirildi (bkz. [diskgenius-parity.md](diskgenius-parity.md)).

## Kaynaklar

- [DiskGenius resmi site](https://www.diskgenius.com/)
- [DiskGenius incelemesi — PartitionManager.org](https://partitionmanager.org/best-partition-manager/diskgenius-review)
- [GParted](https://sourceforge.net/projects/gparted/)
- [KDE Partition Manager incelemesi](https://partitionmanager.org/best-partition-manager/kde-partition-manager-review)
- [gptfdisk (GPT fdisk)](https://github.com/samangh/gptfdisk)
- [gpt-image](https://github.com/swysocki/gpt-image) · [pygpt](https://github.com/pvachon/pygpt) · [hdisk](https://github.com/csdvrx/hdisk)
- [FATtools](https://github.com/maxpat78/FATtools) · [dissect.ntfs](https://github.com/fox-it/dissect.ntfs) · [ext4 (PyPI)](https://pypi.org/project/ext4/) · [py-ext4info](https://github.com/eccramer/py-ext4info)
