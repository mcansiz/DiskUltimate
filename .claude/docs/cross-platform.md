# Capraz Platform Notlari (Windows / Linux)

> **macOS destegi ilan edilmiyor.** Kod yollari yazilmis ve statik denetimden
> geciyor, ancak o platformda hicbir test calistirilmadi ve calistirilacak bir
> makine yok. Asagidaki macOS satirlari **tasarim notu**dur, dogrulama degil.

DiskUltimate uc masaustu platformunda ayni kod tabaniyla calisir. Platforma bagli
her sey tek modulde toplanmistir: [`core/platform.py`](../../src/diskultimate/core/platform.py).

## Neden calisir

| Katman | Durum |
|---|---|
| Python 3.8+ | Uc platformda da ayni |
| PyQt5 | Windows/macOS/Linux resmi tekerlek (wheel) paketleri var |
| Bolum tablolari (MBR/GPT) | Saf Python, yalnizca `struct` + `zlib` |
| FAT12/16/32, exFAT | **Saf Python** — harici arac gerekmez |
| Yedekleme, klonlama, silme, kurtarma | Saf Python |
| Sanal diskler (VHD/VDI/VMDK/QCOW2) | Saf Python |
| ext2/3/4, NTFS **bicimlendirme** | **Saf Python** — uc platformda (ADR 0017, 0018) |

Yani **sekiz dosya sistemi de uc platformda olusturulabilir**. Bicimlendirme
oncelik sirasi: (1) isletim sisteminin kendi araci, (2) harici `mkfs.*`,
(3) saf Python. Arayuz kullanilamayan bir secenegi **gizlemez**, yanina nedenini
yazar (`formatter.all_kinds` her bicim icin bir "neden" metni dondurur).

**Icerik okuma** hala yalnizca FAT ve exFAT icindir; ext/NTFS okuyuculari v0.4
hedefidir.

## Platforma bagli noktalar ve cozumleri

| Konu | Windows | macOS | Linux |
|---|---|---|---|
| **Seyrek dosya** | `FSCTL_SET_SPARSE` + **`SetEndOfFile`** (`truncate()` sifirla doldurur!) | dosya sistemi kendiliginden yapar | kendiliginden |
| **Ham diske yazma** | Bagli birimler once `FSCTL_LOCK_VOLUME` + `DISMOUNT` ile kilitlenmeli | dogrudan yazilir | dogrudan yazilir |
| **Tablo degisikligi bildirimi** | `IOCTL_DISK_UPDATE_PROPERTIES` (acik tutamaci gecersiz kilar — yalnizca kapanista) | `ioctl BLKRRPART` | `diskutil rescan` |
| **Gercek dosya boyutu** | `GetCompressedFileSizeW` | `st_blocks` | `st_blocks` |
| **Harici arac arama** | `format.com` (NTFS icin yerel arac) | `newfs_exfat` vb. aranir | `mkfs.*`, `sbin` dizinleri dahil |
| **Surec calistirma** | `CREATE_NO_WINDOW` ile konsol penceresi acilmaz | standart | standart |
| **Qt platform eklentisi** | Qt karar verir | Qt karar verir | Wayland'da XWayland secilir (ADR 0005) |
| **Varsayilan klasor** | `~/Documents` | `~/Documents` | `~/Documents` veya `~` |

## Onemli kod kurallari

1. `core/` icinde **dogrudan** `subprocess`, `shutil.which`, `tempfile` veya sabit
   yol kullanilmaz — `core/platform.py` ve `paths.py` uzerinden gecilir.
2. Tum `struct` bicimleri acik endian isareti tasir (`<` veya `>`): VHD big-endian,
   digerleri little-endian. Isaretsiz bicim makine siralamasina baglidir ve
   big-endian mimaride sessizce bozulur.
3. Dosyalar her zaman ikili modda acilir; metin dosyalarinda kodlama acikca verilir.
4. Yol birlestirme `os.path.join` ile yapilir; ayirici sabit yazilmaz.

Bu kurallar otomatik denetlenir:

```bash
python3 -m tests.platform_check
```

Denetleyici kaynak agacini tarar ve ihlalleri satir numarasiyla bildirir; ayrica
`core/` icine PyQt sizintisi olup olmadigina bakar. Denetleyicinin kendisi kasitli
ihlal dosyasiyla dogrulanmistir.

## Test durumu

Test sayisi surumle birlikte artti (v0.2.0'da 15, v0.3.0'da 18). Asagidaki
tablo **hangi surumun** hangi platformda kosuldugunu ayirir; eski bir kosum yeni
test sayisiyla anilmaz.

| Platform | Cekirdek testleri | Arayuz | Not |
|---|---|---|---|
| Linux (x86_64) | ✅ **18/18** (v0.3.0) | ✅ | Gelistirme ortami; `fsck.vfat`/`fsck.exfat`/`e2fsck`/`ntfsfix`/`fdisk`/`VBoxManage` ile capraz dogrulama |
| Windows 10 (x64, ana makine) | ✅ **17/18 · 1 atlandi** (v0.3.0, 2026-09-15) | ✅ | `t05` harici `mkfs` bulunmadigi icin atlandi — saf Python yollarinin tamami kosuldu. PyQt5 yalnizca Python 3.12'de kurulu; arayuz testi `py -3.12` ile kosuldu |
| Windows 10 (x64, VirtualBox misafiri) | ✅ 15/15 (v0.2.0, 2026-09-13) | ✅ **dogrulandi** | Tasinabilir Python 3.12.8 + PyQt5 5.15.11; 13.2 s. **Fiziksel disk yazma burada dogrulandi.** v0.3.0 testleri bu misafirde **yeniden kosulmadi** |
| Pop!_OS 24.04 (x64) | ✅ 15/15 (v0.2.0, 2026-09-13) | ✅ **dogrulandi** | Python 3.12.3 + PyQt5 5.15.10; **fiziksel disk okuma/yazma dogrulandi**. v0.3.0 testleri burada da **yeniden kosulmadi** |
| macOS | ⚠️ hic calistirilmadi | ⚠️ | Yalnizca statik denetimden gecti |

**Durust degerlendirme:**
- **Windows 10: cekirdek ve arayuz dogrulandi.** VirtualBox misafirinde tasinabilir
  Python 3.12.8 + PyQt5 5.15.11 ile **o gunku 15 testin tamami** gecti,
  `platform_check` 0 bulgu, arayuz duman testi ekran goruntuleri uretti ve gozle
  denetlendi. v0.3.0'in uc yeni testi (ext, NTFS, boyutlandirma) bu misafirde
  **henuz kosulmadi** — ana makinedeki Windows 10 kosumu 18/18 verdi. Bu kosumlarda
  **uc gercek hata yakalanip duzeltildi**:
  [ADR 0011](../decisions/0011-windows-seyrek-dosya.md) (seyrek dosya uzatma),
  [ADR 0012](../decisions/0012-qt-windows-bulgulari.md) (offscreen font, sekme kirpilmasi).
- **Fiziksel disk erisimi: Windows ve Linux'ta uctan uca dogrulandi.** Win10
  misafirinde `\\.\PhysicalDrive1` uzerine GPT + FAT32 + exFAT yazildi; Windows
  diski GPT olarak tanidi, `E:` harfini atadi ve exFAT birimini bagladi. Bu
  yolda iki Windows'a ozgu kisit bulunup cozuldu: rescan'in acik tutamaci
  gecersiz kilmasi ([ADR 0015](../decisions/0015-isletim-sistemine-bildirim.md))
  ve bagli birime ham yazma reddi
  ([ADR 0016](../decisions/0016-windows-birim-kilitleme.md)).
- **Fiziksel disk erisimi: Linux'ta uctan uca dogrulandi.** Pop!_OS misafirinde
  bos bir test diskine (`/dev/sdb`) GPT tablosu, FAT32 ve exFAT yazildi; sonuc
  `lsblk`, `blkid` ve `mount` ile bagimsiz dogrulandi (cekirdek birimi bagladi ve
  dosyayi okudu). Sistem diski (`/dev/sda`) hedef gosterildiginde uc olcutle
  reddedildi. Windows'ta yonetici yetkisi gerektiren kisim bekliyor.
- **macOS: calistirilmadi.** Kod yollari soyutlanmis ve statik denetimden gecmis
  durumda; `newfs_exfat`/`newfs_msdos` aranir ama exFAT zaten saf Python oldugu
  icin harici araca ihtiyac yoktur.

## Windows uzerinde test etme

VirtualBox Win10 misafiri icin hazir altyapi: [`windows-setup/`](../../windows-setup/)

**Uygulanan yontem (2026-09-13):** VM'de internet ve yonetici yetkisi olmadan da
calisir. Guest Additions uzerinden (`VBoxManage guestcontrol`) komut calistirildi;
Python'un **tasinabilir (embeddable)** paketi ve PyQt5 tekerlekleri ana makinede
indirilip paylasim uzerinden VM'e aktarildi. Boylece SSH/yonetici/kurulum
gerekmeden tam dogrulama yapildi.

**VM'de kalici ortam:** Python `C:\Python312` altina yerlestirildi, pip kuruldu ve
kullanici PATH'ine **basa** eklendi (`C:\Python312;C:\Python312\Scripts;...`).
Basa eklemek sart: Windows'un `WindowsApps\python.exe` yurutme takma adi stub'i
aksi halde gercek yorumlayiciyi golgeleyip Microsoft Store'a yonlendiriyor.

- SSH: host `127.0.0.1:2222` → VM `22` (NAT port yonlendirmesi)
- Proje paylasimi: `duproje` → `\\VBOXSVR\duproje`
- `ssh-kur.ps1`: OpenSSH Server + guvenlik duvari + Python
- `testleri-calistir.ps1`: kaynagi `C:\du-test`e kopyalar, testleri **yerel diskte** calistirir

**Testleri paylasilan klasorde calistirmayin** — `vboxsf` seyrek dosya desteklemez;
goruntuler tum boyutlariyla yazilir ve host diski dolabilir.

## Paketleme (onerilen)

```bash
pip install pyinstaller
pyinstaller --noconsole --name DiskUltimate --add-data "src:src" main.py
```

Windows'ta `--add-data "src;src"` (noktali virgul) kullanilir.
