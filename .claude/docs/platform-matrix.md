# Platform / Bicim Yetenek Matrisi

Tarih: 2026-09-15 · Kaynak: koddan okunarak ve kosularak cikarildi
(`tests/fs_matrix.py`, `tests/ext_write_check.py`, `tests/run_all.py`).

> **Ana bulgu:** cekirdek saf Python oldugu icin **yetenekler platformdan
> bagimsizdir**. Platform farki yalnizca (a) fiziksel diske erisim yonteminde ve
> (b) opsiyonel harici araclarda ortaya cikar. Harici arac artik hicbir bicim
> icin **zorunlu degildir**.
>
> **macOS sutunu kaldirildi.** Kod yollari (`diskutil`, `newfs_*`) yerinde ve
> `platform_check`ten geciyor, ama o platformda **tek bir test bile
> calistirilmadi** ve calistirilacak bir makine yok. Olculmemis bir sutunu
> tabloda tutmak, "destekleniyor" izlenimi verdigi icin yaniltici olurdu.
> Destek **ilan edilmiyor**; kod silinmedi, birisi kosana kadar bilinmiyor
> sayiliyor.

---

## 1. Dosya sistemleri

`B` = bicimlendirme · `O` = icerik okuma · `Y` = icerik yazma
(dosya/klasor olusturma, silme, yeniden adlandirma)

| Dosya sistemi | Windows | Linux | Uygulama | Dogrulayan arac |
|---|---|---|---|---|
| **FAT12** | B O Y | B O Y | saf Python (`fat.py`) | `fsck.vfat` |
| **FAT16** | B O Y | B O Y | saf Python (`fat.py`) | `fsck.vfat` |
| **FAT32** | B O Y | B O Y | saf Python (`fat.py`) | `fsck.vfat` |
| **exFAT** | B O Y | B O Y | saf Python (`exfat.py`) | `fsck.exfat` |
| **ext2** | B O Y | B O Y | saf Python (`ext` + `extread` + `extwrite`) | `e2fsck` |
| **ext3** | B O Y | B O Y | ayni | `e2fsck` |
| **ext4** | B O Y | B O Y | ayni + `extcsum` (metadata_csum) | `e2fsck` |
| **NTFS** | B O Y | B O Y | `ntfs.py` + `ntfsread.py` + `ntfswrite.py` | `ntfsfix`, `ntfs-3g`, `chkdsk` |

**Yalnizca tespit** (icerik okunmaz): btrfs, XFS, F2FS, ISO9660, Linux takas.

### NTFS yazmanin sinirlari (uc platformda ayni)

| Durum | Neden |
|---|---|
| Dizin indeks dugumu dolu | B+ dugumu **bolunemez**; `$INDEX_ROOT` de `$INDEX_ALLOCATION`'a tasinamaz |
| Sikistirilmis / sifrelenmis akis | Okumada da desteklenmez |
| Oznitelikler tek FILE kaydina sigmiyorsa | `$ATTRIBUTE_LIST` yazimi yok (okuma destekler) |

Pratikte bir dizine sigan giris sayisi indeks blogunun boyutuyla sinirlidir
(4 KB blokta ~25-30 giris). `$MFT` dolunca **kendiliginden buyutulur**.

### ext yazmanin sinirlari (uc platformda ayni)

Yazma su durumlarda **acikca reddedilir** — yanlis yazip birimi bozmamak icin:

| Durum | Neden |
|---|---|
| `bigalloc`, `inline_data` | Farkli tahsis/yerlesim kurallari |
| `64bit` **ve** birim > 4 milyar blok | Blok numarasi 32 biti asar |
| Dolu bir **extent** dizinine yeni blok gerekmesi | Extent agaci buyutme yok |
| Tek dosya > ~4 MB (4 KB blokta) | Cok katli dolayli blok yok |

Desteklenen: `metadata_csum` (CRC-32C saglamalar), `64bit` (kucuk birimlerde),
extent'li dizinlere var olan bloklar icinde giris ekleme, extent'li dosyalari
silme.

---

## 2. Disk / kapsayici bicimleri

| Bicim | Okuma | Yazma | Olusturma | Not |
|---|---|---|---|---|
| **`.img` / `.raw` / `.dd`** | ✅ | ✅ | ✅ | Seyrek (sparse) dosya olarak |
| **VHD** (Microsoft) | ✅ | ✅ | ✅ | Sabit ve dinamik |
| **VMDK** (VMware) | ✅ | 🟡 | — | Yalnizca **duz (flat)** VMDK yazilir; seyrek olan salt okunur |
| **VDI** (VirtualBox) | ✅ | — | — | Salt okunur |
| **QCOW2** (QEMU) | ✅ | — | — | Salt okunur |
| **`.dub`** (kendi yedek bicimimiz) | ✅ | — | ✅ | Yedek **salt okunur disk gibi** acilip gezilir; yazma yerine "geri yukle" |

Uc platformda da aynidir (saf Python: `vdisk.py`, `clone.py`).

---

## 3. Bolum tablolari

| Sema | Okuma | Yazma | Not |
|---|---|---|---|
| **MBR** | ✅ | ✅ | 4 birincil + genisletilmis + EBR zinciri (mantiksal bolumler) |
| **GPT** | ✅ | ✅ | 128 giris, CRC32, yedek baslik, koruyucu MBR |
| **MBR ↔ GPT donusumu** | ✅ | ✅ | Bolum verisi yerinde kalir |

Uc platformda da ayni (`mbr.py`, `gpt.py`, `convert.py` — saf Python).

---

## 4. Fiziksel diskler

Tek fark burada: her isletim sistemi kendi arayuzunu dayatir.

| Islev | Windows | Linux | macOS (yazildi, **olculmedi**) |
|---|---|---|---|
| Disk listeleme | `IOCTL` (`ctypes`) | `/sys/block` + `/proc/mounts` | `diskutil list -plist` |
| Salt okunur acma | `CreateFileW` | `open()` | `open()` |
| Yazma modunda acma | `CreateFileW` + **birim kilitleme** (`FSCTL_LOCK_VOLUME`, ADR 0016) | `open()` | `open()` |
| Tabloyu isletim sistemine bildirme | `IOCTL_DISK_UPDATE_PROPERTIES` (ADR 0015) | `ioctl BLKRRPART` | `diskutil rescan` |
| Gercek (seyrek) dosya boyutu | `GetCompressedFileSizeW` | `st_blocks` | `st_blocks` |
| Seyrek dosya olusturma | `FSCTL_SET_SPARSE` + `SetEndOfFile` | kendiliginden | kendiliginden |

Alti katmanli yazma guvenligi (ADR 0014) uc platformda da aynidir.

---

## 5. Kullanilan kutuphaneler

### Zorunlu

| Katman | Bagimlilik |
|---|---|
| `core/` (tum disk mantigi) | **Yalnizca Python standart kutuphanesi** |
| `ui/` (arayuz) | **PyQt5** |

`core/` icinde kullanilan standart moduller:

| Modul | Ne icin |
|---|---|
| `struct` | Tum ikili yapilar (MBR, GPT, BPB, inode, extent…) — her bicim acik endian isareti tasir |
| `ctypes` | Windows aygit G/C (`CreateFileW`, `DeviceIoControl`) |
| `zlib` | GPT/`.dub` icin CRC32 ve `.dub` sikistirmasi |
| `os`, `stat`, `shutil` | Dosya islemleri, seyreklik, disk alani |
| `dataclasses`, `typing` | Veri modelleri |
| `datetime`, `time` | Zaman damgalari |
| `uuid` | GPT GUID, ext UUID |
| `subprocess` | Harici arac calistirma (**yalnizca** `platform.py` icinde) |
| `fcntl` | Linux `ioctl` |
| `msvcrt` | Windows dosya kilitleme |
| `plistlib` | macOS `diskutil` ciktisi |
| `base64`, `array`, `random`, `re`, `sys` | Gomulu tablolar, guvenli silme, ayristirma |

**CRC-32C** Python'da yoktur; `core/crc32c.py` icinde saf Python olarak
yazildi (ext4 `metadata_csum` icin). Standart olcutlerle dogrulanir.

### Opsiyonel — yalnizca hiz ve capraz dogrulama icin

Bunlarin **hicbiri zorunlu degildir**; yoksa saf Python yolu kullanilir.

| Arac | Windows | Linux | macOS | Ne icin |
|---|---|---|---|---|
| `Format-Volume` (PowerShell) | ✅ yerlesik | — | — | NTFS/exFAT/FAT bicimlendirmede **once** denenir |
| `mkfs.exfat` / `mkexfatfs` | — | apt | — | exFAT (2. secenek) |
| `newfs_exfat` | — | — | yerlesik | exFAT (2. secenek) |
| `mkfs.ntfs` / `mkntfs` | — | apt | — | NTFS (2. secenek) |
| `mkfs.ext2/3/4` | — | apt | — | ext (2. secenek) |
| `fsck.vfat`, `fsck.exfat`, `e2fsck`, `ntfsfix` | — | apt | kismen | **Yalnizca test dogrulamasi** |

Bicimlendirme oncelik sirasi: **(1) isletim sisteminin kendi araci →
(2) harici `mkfs.*` → (3) saf Python.** Ucuncu basamak her zaman vardir, bu
yuzden sekiz bicim de uc platformda olusturulabilir.

---

## 6. Dogrulama durumu — ne nerede gercekten kosuldu

| Platform | Kosum | Durum |
|---|---|---|
| **Windows 10** (ana makine) | `run_all` 18/18, `platform_check` 0 bulgu, `ui_smoke` | ✅ Kosuldu |
| **Linux Mint 22.3** (VMware) | `run_all` 18/18, `fs_matrix` 8/8 (tum `fsck`'ler), `ext_write_check` 4/4, `ntfs_write_check` 2/2, fiziksel disk okuma+yazma | ✅ Kosuldu |
| **macOS** | — | ⛔ **Destek ilan edilmiyor** |

> **macOS:** kod yollari (`diskutil`, `newfs_*`) yerinde ve `platform_check`ten
> geciyor, ama o platformda **hicbir test calistirilmadi** ve calistirilacak
> makine yok. Bu yuzden destek **ilan edilmiyor**. Kod silinmedi; birisi
> kosana kadar durumu "bilinmiyor"dur.

Ek olarak Linux'ta gercek donanimda dogrulananlar:
- ext4 okuyucu, **Linux cekirdek surucusuyle** karsilastirildi (ayni dizin
  listesi, 870 KB'lik ikili dosyada ayni SHA256).
- ext4 yazma, `/dev/sdb` test diskinde: `e2fsck` temiz, cekirdek bagladi.
- ext4 `metadata_csum` yazma, kullanicinin SD kart kopyasinda: `e2fsck` temiz.
