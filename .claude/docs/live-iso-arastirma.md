# DiskUltimate Live ISO — Araştırma Raporu

Tarih: 2026-10-24 · Durum: **uygulanıyor** — karar ADR 0101 (iki katmanlı ISO: Debian tabanı + AppImage katmanı, jwm + pcmanfm masaüstü). Bölüm 4.1 düzeltildi; 4.2 ve 4.3 AppImage seçimiyle kapandı.

Soru: DiskUltimate için küçük, kendi başına açılan (live CD/USB) bir Linux
hazırlayıp uygulamayı `.iso` olarak yayınlayabilir miyiz?

> **Kısa cevap: Evet.** Teknik engel yok. DiskGenius'un WinPE sürümünün
> karşılığı olur ve uygulamanın en güçlü işleri (çevrim dışı Windows parola
> sıfırlama, BitLocker açma, sistem diskini klonlama, önyükleyici onarımı,
> açılmayan makineden veri kurtarma) tam da çalışan bir işletim sistemi
> **dışından** yapılması gereken işlerdir. Önerilen yol: **Debian 13 (trixie)
> + `live-build`**, en küçük X11 + pencere yöneticisi, root olarak otomatik
> açılan DiskUltimate. ISO GitHub Actions'ta üretilir ve QEMU'da açılarak
> sınanır. Boyut **~300 MB** (en küçük makul yapı; tam donanım + CJK yazı
> tipleriyle ~460 MB — bölüm 3.1'de ölçüldü), tahmini iş **~7–10 iş günü**.
> Önce koda **üç küçük düzeltme** gerekir (bölüm 4).

---

## 1. Benzer projeler nasıl yapıyor

| Proje | Taban | Araç | Not |
|---|---|---|---|
| **GParted Live** | Debian Sid | `live-build` + DRBL/Clonezilla betikleri | En yakın örnek: tek uygulamalı disk aracı. 1.8.0 (Ocak 2026) çekirdek 6.18; yalnızca 64 bit |
| **Clonezilla Live** | Debian / Ubuntu | `live-build` (aynı ekip) | Metin arayüzü |
| **Rescuezilla** | Ubuntu (her LTS için ayrı ISO) | Kendi betikleri, GitHub Actions'ta derleniyor | 2.6.2 (Mayıs 2026) Ubuntu 26.04 tabanı ekledi; gece uçtan uca test koşuyor |
| **SystemRescue** | Arch Linux | Yamalı `archiso` | Xfce masaüstü; ~1 GB; `sysrescue-customize` ile hazır ISO'ya ekleme yapılabiliyor |

Çıkarım: tek bir grafik disk aracını live ISO olarak dağıtmanın yerleşik yolu
**Debian + live-build**'dir (GParted/Clonezilla 15+ yıldır böyle). Rescuezilla
da derlemeyi GitHub Actions'a koyup her sürümde otomatik ISO çıkarıyor — bizim
`release.yml` düzenimize birebir uyan model bu.

## 2. Seçenekler

| Seçenek | Boyut (tahmin) | Secure Boot | PyQt5 | Bakım | Değerlendirme |
|---|---|---|---|---|---|
| **A. Debian 13 + live-build** | 300–460 MB (ölçüldü, 3.1) | **Var** (imzalı shim + GRUB + çekirdek Debian'dan) | `python3-pyqt5` paketi | Düşük: Debian stable, 2+ yıl destek | **Önerilen** |
| B. Ubuntu tabanı (Rescuezilla gibi) | 800 MB+ | Var | Paket var | Orta: her LTS'te `casper` değişir | Gereksiz büyük |
| C. Arch + archiso (SystemRescue gibi) | 700 MB+ | **Yok** (elle imzalamak gerekir) | Paket var | Yüksek: yuvarlanan dağıtım, tekrar üretilebilirlik zayıf | Uygun değil |
| D. Alpine + mkimage | 200–300 MB | Yok | `py3-qt5` | Orta: musl, donanım testi az | En küçük; Secure Boot yokluğu modern makinelerde BIOS ayarı ister |
| E. Buildroot / Tiny Core | 100–200 MB | Yok | Elle | **Çok yüksek**: her şeyi biz derleriz | Tek kişilik projeye ağır |
| F. mkosi (systemd) | — | UKI ile mümkün | Paket | Orta | Asıl çıktısı disk görüntüsü (DDI); karma BIOS/UEFI ISO olgun değil |
| G. SystemRescue + `sysrescue-customize` | ~1.1 GB | Yok | AppImage'ı eklemek | Düşük | **Hızlı kavram kanıtı** için iyi (birkaç saat), ürün için değil |

**Neden A:**
- **Secure Boot kapatmadan açılır.** Bugünkü makinelerin çoğunda Secure Boot
  açık gelir; kullanıcıdan BIOS'a girip kapatmasını istemek, tam da "makinem
  açılmıyor" diye gelen kullanıcı için kötü bir ilk deneyim. Debian'ın imzalı
  zinciri bunu bedavaya verir. (live-build'e 2024'te "yalnızca shim ile güvenli
  UEFI açılışı" desteği eklendi; OVMF Secure Boot firmware'iyle **sınanmalı**.)
- **Tek dosyalık karma ISO** (`--binary-images iso-hybrid`): CD'ye yazılır,
  `dd`/Rufus/Ventoy ile USB'ye yazılır, BIOS ve UEFI'de açılır.
- Gereken her harici araç Debian'da paketli: `ntfs-3g`, `e2fsprogs`,
  `dosfstools`, `exfatprogs`, `xfsprogs`, `btrfs-progs`, `grub-*`,
  `efibootmgr`, `systemd` (`systemd-inhibit`, ADR 0095).
- GParted Live ile aynı yol → sorunlara bulunmuş çözümler, aynı açılış
  seçenekleri (`toram`, `locales=`, `keyboard-layouts=`).

## 3. Önerilen yapı

```
ISO (iso-hybrid, BIOS + UEFI, Secure Boot)
 └─ GRUB menüsü: Türkçe / English / Deutsch / ... / "RAM'e yükle (toram)" / "Güvenli grafik (nomodeset)"
     └─ Debian 13 çekirdek + initramfs (live-boot)
         └─ squashfs kök (salt okunur) + RAM üzerinde overlay
             └─ tty1'de root otomatik giriş → startx → matchbox → DiskUltimate (tam ekran)
```

Paket seçimi (ilk sürüm):

| Grup | Paketler | Neden |
|---|---|---|
| Uygulama | `python3`, `python3-pyqt5`, DiskUltimate kaynağı `/opt/diskultimate` | AppImage yerine dağıtımın Qt'si: kitaplıklar iki kez taşınmaz |
| Grafik | `xserver-xorg-core`, `xserver-xorg-video-{fbdev,vesa}`, `xinit`, `matchbox-window-manager` | En küçük çalışan X (modesetting çekirdek pakette). openbox değil: ghostscript zincirini çekiyor (3.1). Wayland'da root ile Qt sorunlu |
| Dosya sistemi | `ntfs-3g`, `e2fsprogs`, `dosfstools`, `exfatprogs`, `xfsprogs`, `btrfs-progs` | `mkfs`/doğrulama araçları |
| Önyükleme | `grub-pc-bin`, `grub-efi-amd64-bin`, `efibootmgr` | Önyükleyici onarımı / UEFI değişkeni özellikleri |
| Yazı tipi | `fonts-noto-core`, CJK için `fonts-noto-cjk` **veya** daha küçük `fonts-wqy-microhei` | zh/ja/ko arayüzü (en büyük kalem: ~60–90 MB) |
| Yardımcı | `xterm`, `pciutils`, `usbutils`, `smartmontools` | İleri kullanıcı ve hata bildirimi |
| Donanım | `firmware-linux-free` (+ isteğe bağlı `non-free-firmware`) | Depolama denetleyicileri çoğunlukla çekirdekte; GPU bellenimi olmadan `modesetting`/`fbdev` yine açılır |
| Ağ | **ilk sürümde yok** | İkinci sürüm: yedeği ağ paylaşımına yazmak için NetworkManager |

Dil: GRUB menüsündeki her girdi çekirdek satırına `locales=` ve
`keyboard-layouts=` (live-config) ile `DISKULTIMATE_LANG=<dil>` verir;
uygulama zaten çalışırken dil değiştirebiliyor.

### 3.1 Boyut ölçümü (2026-10-24)

**Yöntem (root gerekmeden):** Debian 13 `main` + `non-free-firmware` paket
dizinleri indirildi; paket listesinin bağımlılık kapanışı hesaplandı
(`Depends`/`Pre-Depends`, `Recommends` yok — live-build'in
`--apt-recommends false` kipi, + `Priority: required` taban); `.deb`'ler
`dpkg-deb -x` ile tek bir köke açıldı (kurulum betikleri çalışmaz),
DiskUltimate kaynağı `/opt` altına kondu ve kök `mksquashfs -comp xz -b 1M`
ile sıkıştırıldı. Çekirdek: `linux-image-6.12.107+deb13-amd64`.

**Kök dosya sistemi (filesystem.squashfs):**

| Yapı | Paket | Açık | squashfs |
|---|---|---|---|
| openbox, kırpılmamış | 346 | 792 MB | **282 MB** |
| matchbox-window-manager, kırpılmamış | 305 | 709 MB | 256 MB |
| matchbox + belge/man/yabancı dil silinmiş | 305 | 634 MB | **228 MB** |
| + OpenGL/Mesa yığını çıkarılmış (sahte paketle, **sınanmadı**) | ~290 | 441 MB | 182 MB |

squashfs'te zstd (seviye 19) xz'den %5 büyük çıktı (296 / 282 MB).

**En büyük kalemler ve nedenleri** (kurulu boyut):

| Kalem | Boyut | Kim çekiyor |
|---|---|---|
| `libllvm19` + `libz3-4` + `mesa-libgallium` | 192 MB | Qt5 → `libgl1` → `libglx-mesa0` (yazılımsal OpenGL). Qt Widgets'ın buna ihtiyacı yok ama paket bağımlılığı zorunlu |
| `linux-image` (modüller zaten `.ko.xz`) | 106 MB | çekirdek; sıkıştırmada küçülmez |
| `libicu76` | 36 MB | `libqt5core5t64`, `xfsprogs` |
| Ghostscript zinciri (`libgs10`, `poppler-data`, `fonts-urw-base35` ...) | ~83 MB | **openbox** → `libimlib2t64` → `libspectre1`. matchbox ile hepsi düşüyor |

**Eklentiler (squashfs'e eklenen, xz):**

| Eklenti | + |
|---|---|
| `fonts-wqy-microhei` (zh/ja/ko için küçük CJK) | +2 MB |
| `fonts-noto-core` + `fonts-noto-mono` | +13 MB |
| `fonts-noto-cjk` | +58 MB |
| non-free bellenim (`firmware-misc-nonfree`, `-amd-graphics`, `-intel-graphics`, `-realtek`, `-iwlwifi`) | +81 MB |
| live-build varsayılanı `Priority: important` paketleri (37 paket) | ~+8 MB |
| Python bayt kodu (`.pyc`, kurulumda üretilir) | ~+4 MB |

**ISO'nun geri kalanı:** `vmlinuz` 12 MB (ölçüldü); `initrd.img` ~30–40 MB
(**tahmin**: depolama/dosya sistemi/USB modülleri 26 MB, zaten sıkıştırılmış,
+ live-boot kullanıcı alanı; bellenim eklenirse büyür); GRUB (BIOS + EFI +
imzalı shim) ve `efi.img` ~10–15 MB (tahmin).

**Toplam ISO:**

| Yapı | ISO |
|---|---|
| Agresif: matchbox + kırpılmış + OpenGL'siz + wqy | **~250 MB** |
| **Önerilen:** matchbox + kırpılmış + wqy + `important` | **~300 MB** |
| Ayarsız live-build: openbox, kırpılmamış, wqy | ~350 MB |
| Tam: önerilen + noto-core + noto-cjk + non-free bellenim | ~460 MB |

Karşılaştırma için GParted Live ~550–600 MB (bellekten, doğrulanmadı; Fluxbox +
GParted + birçok araç). Bölüm 2'deki ilk tahmin (450–600 MB) en küçük yapı
için yüksekmiş.

**Bu ölçümde olmayanlar (sapma kaynakları):** bağımlılık çözücü her `|`
seçeneğinde ilk paketi seçer (apt farklı seçebilir); kurulum betiklerinin
ürettiği dosyalar (ld/font önbellekleri, `/etc` üretimleri) yok; initrd ve
önyükleyici kurulmadı, tahmin edildi; `matchbox-window-manager` ve
`libmatchbox1` (<1 MB) indirilmedi. Gerçek sayı Faz 0'daki CI derlemesinde
çıkacak — beklenen sapma ±%10.

**Kararı etkileyen iki bulgu:**
1. **Pencere yöneticisi boyutu belirliyor:** openbox ghostscript zincirini
   çekiyor (~25 MB squashfs). matchbox tek uygulamalı (kiosk) kullanıma da
   daha uygun: her pencereyi tam ekran açar, diyaloglar üstte durur.
2. **CJK yazı tipi seçimi 56 MB fark ediyor:** `fonts-wqy-microhei` 2 MB,
   `fonts-noto-cjk` 58 MB. wqy'nin Japonca kana ve Korece hangul gliflerini
   eksiksiz kapsadığı `DISKULTIMATE_LANG=ja/ko` ile **gözle doğrulanmalı**.

## 4. Kodda gereken değişiklikler (depodan okunarak bulundu)

### 4.1 Açılış ortamı "sistem diski" sayılmıyor — **güvenlik açığı, zorunlu**

[physical.py:436-440](../../src/diskultimate/core/physical.py#L436-L440)
sistem diskini `/` noktasına bağlı `/dev/...` aygıtıyla buluyor. Live
sistemde `/` bir **overlay**'dir (`/dev/` ile başlamaz) → `system_device`
boş kalır → **hiçbir disk sistem diski işaretlenmez**. Uygulamanın çalıştığı
USB belleğin kendisi yalnızca "bağlı bölüm" uyarısıyla (katman 5) korunur,
katman 3 (disk adını yazarak doğrulama) devreye girmez. Kullanıcı açılış
belleğini silerse sistem çalışırken altından çekilir.

Düzeltme: açılış ortamının bağlandığı yer (`/run/live/medium`; archiso için
`/run/archiso/bootmnt`) ya da `/proc/cmdline` içindeki `boot=live` +
`findiso` bilgisinden açılış diski bulunup `is_system=True` yapılır. `toram`
ile açıldıysa bellek serbest bırakılmıştır, bu da ayrıca belirtilir. CLAUDE.md
kural 6 gereği **test edilir** (sahte `/proc/mounts` + `/sys/block` ağacıyla).

Not: iç diskler bu ortamda sistem diski **değildir** — bu doğru ve aracın
amacı budur. Ama kullanıcı "içinde Windows olan" diski yine bilmeli: ADR
0089'daki bölüm başına `os_hint` arayüzde uyarı olarak gösterilmeli.

### 4.2 Kaynaktan kurulan kopya günlükleri kendi dizinine yazar

[paths.py:44-62](../../src/diskultimate/paths.py#L44-L62): `IS_PACKAGED`
yalnızca PyInstaller/AppImage'da doğru. `/opt/diskultimate`'ten çalışan kopya
"kaynaktan çalışıyor" sanılır ve günlükleri `/opt/diskultimate/.claude/logs`
altına yazar (overlay sayesinde hata vermez ama yanlış yer). Bir paketlenmiş
kopya işareti gerekir (ör. kurulum dizininde bir işaret dosyası ya da
`DISKULTIMATE_PACKAGED=1`). Ayrıca RAM'deki günlük yeniden başlatınca
kaybolur → **Araçlar > Tanılama**'ya "günlükleri bir diske kaydet" eklenmeli
(hata bildirimi için şart).

### 4.3 Python 3.13 hiç sınanmıyor

Debian 13'ün Python'u **3.13**; CI'daki bütün işler 3.12. 3.13'te kaldırılan
modüller (`crypt`, `imghdr`, `telnetlib`, `cgi` ...) `src/` içinde **kullanılmıyor**
(grep ile denetlendi; son commit `crypt`'i zaten bırakmıştı). Yine de
`tests.yml`'e bir `debian:trixie` kapsayıcı işi (sistem `python3-pyqt5` ile)
eklenmeli.

### Zaten hazır olanlar

- **Yetki:** live sistemde root çalışılır → `is_elevated()` doğru döner, ADR
  0042 sorusu hiç çıkmaz; `PKEXEC_UID` olmadığı için `restore_owner` dokunmaz.
- **Bağlama:** `udisksctl` yoksa root olarak `mount`'a düşüyor.
- **UEFI:** UEFI kipinde açılınca `efivarfs` hazır → önyükleyici/UEFI
  özellikleri gerçek donanımda çalışır. Secure Boot açıkken Debian çekirdeği
  *lockdown (integrity)* kipine girer; bu `/dev/mem`'i ve imzasız modülü
  kısıtlar, blok aygıtına ham yazmayı ve efivars'ı kısıtlamaz — **ölçülerek
  doğrulanmalı**.
- `loop`, `sr`, `zram` aygıtları listede zaten atlanıyor
  ([physical.py:410](../../src/diskultimate/core/physical.py#L410)) → live
  sistemin kendi squashfs/loop aygıtları listede görünmez.

## 5. Derleme ve test (CI)

Ana makinede root yok → `live-build` yerelde **çalışmaz** (debootstrap root
ister; yerelde ne `lb` ne `debootstrap` ne `qemu` kurulu). GitHub Actions bunu
zaten çözüyor (ADR 0080: CI makineleri VM sayılır; ağır işler orada —
hafıza notu "Ağır testler GitHub'da").

`release.yml`'e eklenecek iş (taslak):

```yaml
build-iso:
  needs: [version, test-linux]
  runs-on: ubuntu-24.04
  timeout-minutes: 45
  steps:
    - uses: actions/checkout@...
    - name: ISO (debian:trixie kapsayicisinda live-build)
      run: |
        docker run --rm --privileged -v "$PWD:/src" -w /src debian:trixie \
          bash tools/live/build.sh "${{ needs.version.outputs.version }}"
    - name: Acilis denemesi (QEMU: BIOS + UEFI + Secure Boot)
      run: python3 tools/live/boot_check.py dist/DiskUltimate-*-live-amd64.iso
    - uses: actions/upload-artifact@...
```

`boot_check`: ISO QEMU'da (GitHub'ın Linux makinelerinde KVM açık) üç kez
açılır — SeaBIOS, OVMF, OVMF+Secure Boot; uygulama açılınca seri konsola bir
işaret satırı yazar, test bunu bekler (süre sınırlı, sessiz bekleme yok).

**Yan kazanç:** aynı QEMU koşusuna boş bir `qcow2` ikinci disk eklenirse,
2026-09-29'dan beri olmayan **Linux fiziksel disk test ortamı** geri gelir:
`tests.physical_*` live sistemin içinde, gerçek `/dev/vdb` üzerinde, her
koşuda silinen bir makinede çalışır. CLAUDE.md test kuralıyla tam uyumlu.

## 6. Yayınlama

- **Boyut:** GitHub sürüm eki sınırı dosya başına 2 GiB; ~500 MB ISO sığar.
- Yanına `SHA256SUMS` (ve istenirse imza) konur.
- Ad: `DiskUltimate-<sürüm>-live-amd64.iso`; 32 bit / ARM yok (GParted de
  32 biti bıraktı).
- **Lisans yükümlülüğü:** ISO, yüzlerce GPL paketin **ikili** dağıtımıdır.
  Uygulamamız zaten GPL-3; PyQt5 GPL ile uyumlu. Ama Debian paketlerinin
  kaynağını da sunmak gerekir: ya live-build'in `--source true` çıktısı (çok
  büyük, birkaç GB) yayınlanır ya da ISO içine paket listesi (manifest) +
  Debian'ın **snapshot.debian.org** arşivine işaret eden yazılı teklif konur.
  GParted/Clonezilla ikinci yolu izliyor. (Hukuki görüş değildir.)
- `non-free-firmware` eklenirse o paketlerin dağıtım izinleri ayrıca okunur.
- Uygulamanın kendisi de ileride ISO'yu USB'ye yazabilir ("Önyüklenebilir USB
  oluştur" — DiskGenius'taki WinPE USB oluşturmanın karşılığı).

## 7. Riskler

| Risk | Etki | Önlem |
|---|---|---|
| Açılış belleğini silmek | Sistem çalışırken çöker | Bölüm 4.1 — yayından **önce** zorunlu |
| Grafik kartında X açılmaz | Kullanıcı siyah ekran görür | `nomodeset` menü girdisi + `fbdev`/`vesa` sürücüleri |
| Secure Boot zinciri bir güncellemede kırılır | Modern makinede açılmaz | CI'da OVMF+SB açılış testi her sürümde |
| Debian sürüm değişimi (13 → 14) | live-build ayarları kayar | Sürüm sabit (`trixie`), yükseltme ayrı iş |
| Gerçek donanım çeşitliliği | VM'de geçen, makinede kalır | Sürüm notunda "deneysel" + en az iki gerçek makinede deneme |
| Boyut büyümesi | İndirme süresi | CJK yazı tipi seçimi, belge/man silme (`--apt-recommends false`) |

## 8. Yol haritası (tahmin)

| Faz | İş | Süre |
|---|---|---|
| 0 | Kavram kanıtı: CI'da en küçük `lb config` + uygulama otomatik açılış, QEMU'da ekran görüntüsü | 1 gün |
| 1 | Bölüm 4.1 (açılış diski koruması) + test; 4.2 (paketli kopya işareti, günlük kaydet); 4.3 (trixie CI işi) | 2–3 gün |
| 2 | Dil menüsü, yazı tipleri, `nomodeset`/`toram` girdileri, `boot_check` (BIOS/UEFI/SB) | 2–3 gün |
| 3 | `release.yml` entegrasyonu, manifest + kaynak teklifi, README, gerçek makinede deneme | 2 gün |
| 4 (sonra) | Ağ desteği, QEMU içinde `tests.physical_*`, "USB oluştur" özelliği | — |

**Önerilen sonraki adım:** Faz 0'ı bir `workflow_dispatch` işi olarak
yapmak — ana makineye dokunmadan, tek oturumda "açılıyor mu, ne kadar büyük"
sorusunu ölçüye çevirir. Sonuç olumluysa ADR yazılır.

## Kaynaklar

- [Create GParted live from scratch](https://gparted.org/create-gparted-live.php)
- [GParted Live 1.8 (çekirdek 6.18)](https://linuxiac.com/gparted-live-1-8-released-with-linux-kernel-6-18/)
- [GParted Live 1.7.0-8: 32 bit bırakıldı](https://www.theregister.com/2025/07/14/gparted_live_1708/)
- [live-build Debian değişiklik günlüğü (shim-only Secure Boot)](https://blueprints.launchpad.net/debian/+source/live-build/+changelog)
- [Create Your Own Live Linux Distro (iso-hybrid)](https://pendrivelinux.com/create-your-own-live-linux-distribution)
- [Rescuezilla 2.6.2 sürümü](https://github.com/rescuezilla/rescuezilla/releases/tag/2.6.2)
- [Rescuezilla 2.6.1 (çoklu Ubuntu tabanı)](https://www.theregister.com/2025/07/18/rescuezilla_261/)
- [SystemRescue: kaynaktan derleme](https://www.system-rescue.org/manual/Building_SystemRescue_from_Source)
- [sysrescue-customize](https://www.system-rescue.org/scripts/sysrescue-customize/)
- [Arch Wiki: Archiso](https://wiki.archlinux.org/title/Archiso)
- [Alpine: mkimage ile özel ISO](https://wiki.alpinelinux.org/wiki/How_to_make_a_custom_ISO_image_with_mkimage)
- [Arch Wiki: mkosi](https://wiki.archlinux.org/title/mkosi) · [mkosi v25](https://newreleases.io/project/github/systemd/mkosi/release/v25)
