# DiskGenius Ozellik Karsilastirmasi

Hedef: DiskGenius'un ozelliklerini mumkun oldugunca karsilamak.
Durum isaretleri: ✅ tamam · 🟡 kismi · 📋 planlandi · ⛔ kapsam disi

Guncelleme: 2026-09-13 (v0.2.0)

---

## Disk erisimi

| Ozellik | DiskGenius | DiskUltimate | Not |
|---|---|---|---|
| **Sistemdeki fiziksel diskleri listeleme** | ✅ | ✅ | Model, seri, boyut, veriyolu, bolumler, bagli noktalar |
| **Fiziksel diski acma (salt okunur)** | ✅ | ✅ | Linux'ta dogrulandi: gercek diskin MBR/GPT tablosu okundu |
| **Fiziksel diske yazma** | ✅ | ✅ | Linux'ta dogrulandi: GPT+FAT32+exFAT yazildi, cekirdek bagladi. Katmanli onay: ADR 0014 |
| Sistem diski / bagli bolum uyarisi | ✅ | ✅ | Sistem diskinde ad yazarak dogrulama |
| Cikarilabilir aygit (USB/SD) | ✅ | ✅ | Listede isaretlenir |
| Disk goruntusu dosyalari | ✅ | ✅ | .img/.raw/.dd + VHD/VDI/VMDK/QCOW2 |
| **Birden fazla diski ayni anda acma** | ✅ | ✅ | Sol agacta alt alta; etkin olan kalin |

## Bolum yonetimi

| Ozellik | DiskGenius | DiskUltimate | Not |
|---|---|---|---|
| Bolum olustur / sil | ✅ | ✅ | MBR birincil + genisletilmis + mantiksal, GPT 128 bolum |
| Bicimlendir | ✅ | ✅ | **Sekiz bicim, uc platformda**: FAT12/16/32, exFAT, ext2/3/4 saf Python; NTFS uc katmanli (Windows yerel aracı → mkfs.ntfs → saf Python), `chkdsk` temiz |
| Bolum tablosu olustur (MBR/GPT) | ✅ | ✅ | |
| **MBR ↔ GPT donusumu** | ✅ | ✅ | Veri yerinde kalir; uygunluk on denetimi var |
| Bolum turu / etiket / ad degistirme | ✅ | ✅ | GPT ad, FAT/exFAT birim etiketi |
| Onyukleme (aktif) bayragi | ✅ | ✅ | MBR bayragi, GPT legacy-BIOS oznitelik biti |
| Gorsel bolum haritasi | ✅ | ✅ | Renkli oransal bloklar + doluluk cubugu |
| **4K hizalama denetimi** | ✅ | ✅ | Araclar > Hizalama denetimi |
| **Bolum boyutlandirma / tasima** | ✅ | ✅ | Suruklemeli serit; FAT12/16/32 + exFAT veri koruyarak, NTFS/ext Windows'ta yerel arac |
| Bolum bolme / birlestirme | ✅ | 📋 | Boyutlandirma altyapisi hazir; bolme = kucult + yeni bolum |
| Birincil ↔ mantiksal donusumu | ✅ | 📋 | v0.3 |
| Bolum gizleme | ✅ | 📋 | GPT gizli oznitelik biti hazir, arayuz baglanacak |
| Surucu harfi atama | ✅ | ⛔ | Windows kayit defteri islemi; goruntu dosyasinda karsiligi yok |
| Dinamik disk → temel disk | ✅ | ⛔ | Windows LDM bicimine ozel |

## Dosya erisimi

| Ozellik | DiskGenius | DiskUltimate | Not |
|---|---|---|---|
| Dosya gezgini (listele/ac) | ✅ | ✅ | Klasor agaci + dosya listesi |
| Dosya disa aktarma | ✅ | ✅ | Tekil ve klasor agaci |
| Dosya ice aktarma / silme / yeniden adlandirma | ✅ | ✅ | FAT ve exFAT'te tam |
| Uzun dosya adi (LFN / Unicode) | ✅ | ✅ | FAT LFN + exFAT UTF-16 |
| FAT12/16/32 okuma-yazma | ✅ | ✅ | Saf Python, `fsck.vfat` ile dogrulandi |
| **exFAT okuma-yazma** | ✅ | ✅ | Saf Python, `fsck.exfat` ile dogrulandi |
| NTFS okuma | ✅ | 📋 | v0.4 — MFT cozumleyici |
| ext2/3/4 okuma | ✅ | 📋 | v0.3 |
| Dosya onizleme | ✅ | ✅ | Metin + onaltilik onizleme |

## Veri kurtarma

| Ozellik | DiskGenius | DiskUltimate | Not |
|---|---|---|---|
| **Silinmis dosya kurtarma** | ✅ | ✅ | FAT (silinmis LFN adlari dahil) ve exFAT |
| Kurtarilabilirlik degerlendirmesi | ✅ | ✅ | Kume uzerine yazilmis mi denetimi, yuzde tahmini |
| **Kayip bolum tarama** | ✅ | ✅ | FAT/exFAT/NTFS imzalari, boyut ve etiket okuma, hizli/derin mod |
| Bulunan bolumu tabloya ekleme | ✅ | ✅ | Araclar > Kayip bolumleri tara |
| **Dosya turune gore kurtarma (carving)** | ✅ | ✅ | 13 imza: JPEG, PNG, GIF, PDF, ZIP, RAR, 7z, GZIP, MP3, MP4, EXE, ELF, SQLite |
| Bicimlendirilmis bolumden kurtarma | ✅ | 🟡 | Carving calisir; dizin yapisi yeniden kurma yok |
| RAID dizisinden kurtarma | ✅ | ⛔ | Kapsam disi |

## Klonlama ve yedekleme

| Ozellik | DiskGenius | DiskUltimate | Not |
|---|---|---|---|
| Bolum yedegi (goruntu dosyasina) | ✅ | ✅ | `.dub` bicimi — sikistirmali, sifir bloklari atlar |
| Disk yedegi | ✅ | ✅ | |
| Yedekten geri yukleme | ✅ | ✅ | Boyut denetimi + ozet onayi |
| Disk klonlama | ✅ | ✅ | Seyrekligi koruyarak |
| Bolumden bolume klonlama | ✅ | ✅ | `clone_partition_to` |
| Sektor sektor kopyalama | ✅ | ✅ | Klonlamanin varsayilani |
| Windows'u SSD'ye tasima | ✅ | ⛔ | Fiziksel disk ve onyukleyici islemi gerektirir |

## Bakim ve ileri araclar

| Ozellik | DiskGenius | DiskUltimate | Not |
|---|---|---|---|
| **Guvenli silme (wipe)** | ✅ | ✅ | Sifir / rastgele / DoD 3 gecis / DoD 7 gecis + dogrulama |
| Bos alani silme | ✅ | ✅ | Mevcut dosyalara dokunmadan artik veriyi yok eder |
| **Onaltilik sektor goruntuleyici** | ✅ | 🟡 | Goruntuleme var; **yazma** v0.3'te |
| **Sanal disk destegi** | ✅ | ✅ | VHD/VDI/VMDK/QCOW2 okuma; VHD olusturma ve yazma |
| Sanal disk donusumu | ✅ | 🟡 | Klonlama ile ham→VHD mumkun; dogrudan donusturucu 📋 |
| Bozuk sektor denetimi/onarimi | ✅ | ⛔ | Fiziksel diske ozgu |
| S.M.A.R.T. saglik izleme | ✅ | ⛔ | Fiziksel diske ozgu |
| UEFI onyukleme girisi yonetimi | ✅ | ⛔ | Isletim sistemi NVRAM islemi |
| Onyuklenebilir kurtarma ortami (WinPE) | ✅ | ⛔ | Windows lisans/arac zinciri gerektirir |
| Dosya sistemi denetimi (chkdsk benzeri) | ✅ | 📋 | v0.3 — FAT/exFAT tutarlilik denetimi |
| Islem gunlugu | 🟡 | ✅ | Arayuzde sekme + `.claude/logs/` dosyasi |

---

## Ozet

| Kategori | Karsilanan | Planlanan | Kapsam disi |
|---|---|---|---|
| Bolum yonetimi | 8 | 5 | 2 |
| Dosya erisimi | 7 | 2 | 0 |
| Veri kurtarma | 5 | 0 | 1 (+1 kismi) |
| Klonlama/yedekleme | 6 | 0 | 1 |
| Bakim | 4 | 2 | 4 |

"Kapsam disi" maddelerin tamami **fiziksel diske erisim** gerektirdigi icin, proje
ilkesi (yalnizca goruntu dosyasi, root yok) geregi disarida birakilmistir.
