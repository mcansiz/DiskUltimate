# 0101 — Live ISO: Debian tabanı + AppImage katmanı

Tarih: 2026-10-24
Durum: **uygulanıyor** — derleme ve QEMU açılış denemesi GitHub Actions'ta;
ilk koşunun sonucu bekleniyor. Gerçek donanımda **henüz denenmedi**.
İlgili: [0050](0050-linux-dagitimi-appimage-ve-musl.md) (AppImage), [0079](0079-github-actions-ci-ve-surum.md),
[0080](0080-uzun-testler-plani.md) (CI makineleri VM sayılır),
[0014](0014-fiziksel-disk-destegi.md) (güvenlik katmanları).
Araştırma ve boyut ölçümü: `.claude/docs/live-iso-arastirma.md`.

## Kullanıcı isteği

"Minimal bir live CD Linux hazırlayıp uygulamayı ISO olarak yayınlayabilir
miyiz?" Ardından iki ek:
1. "Yazılım sürekli güncelleniyor; her seferinde derleme ortamı kurmak işi
   uzatır, GitHub VM'de bir şey yapabilir miyiz?"
2. "Uygulamayı zaten AppImage olarak yayınlıyoruz; doğrudan onu koyalım,
   açılışta otomatik açılsın, masaüstünde simgesi olsun. Uygulama
   güncellenince yalnızca AppImage güncellensin."

## Karar

**İki katmanlı ISO.**

| Katman | İçerik | Ne zaman kurulur | Süre |
|---|---|---|---|
| Taban `filesystem.squashfs` | Debian 13 + X + jwm + pcmanfm + `mkfs` araçları | `tools/live/base/**` değişince, haftalık (pazartesi), elle | ~10–15 dk (live-build, `debian:trixie` kapsayıcısı) |
| Uygulama `zz-diskultimate.squashfs` | Yayınlanan AppImage + `VERSION` | Her koşu / her sürüm | saniyeler (`tools/live/remaster.py`) |

- live-boot `/live/*.squashfs` dosyalarının **hepsini** sıralayıp üst üste
  bağlar (`.module` dosyası yoksa — live-boot 20250815, `9990-overlay.sh`
  okundu). `zz-` adı katmanı tabanın üstüne koyar.
- Ekleme `xorriso -indev base.iso -outdev out.iso -boot_image any replay
  -map ...` ile yapılır: açılış düzeni (El Torito, hibrit MBR/GPT, EFI)
  tabandan aynen taşınır; `sha256sum.txt` güncellenir (GRUB "Verify
  integrity" girdisi). Root gerekmez.
- **Taban GitHub önbelleğinde durur** (`actions/cache`, anahtar =
  `tools/live/base/**` özeti + ISO hafta numarası). Etiket koşuları ana
  daldaki önbelleği okuyabildiği için taban main üzerinde tazelenir; release
  koşusu bir önceki haftanın tabanını da kabul eder (restore-keys), böylece
  sürüm çıkarmak tabanı beklemez.
- **Uygulama = yayınlanan AppImage** (kullanıcı isteği 2). Kazanç: sınanan
  dosyanın aynısı dağıtılır; AppImage kendi Python 3.12'si + Qt'siyle gelir
  (Debian 13'ün Python 3.13'ü hiç devreye girmez — rapor 4.3 kapanır);
  `paths.IS_PACKAGED` doğru olur (rapor 4.2 kapanır).
- **Oturum:** tty1 root otomatik giriş → `startx` → jwm (görev çubuğu + menü)
  + `pcmanfm --desktop` (masaüstü simgeleri: DiskUltimate, Uçbirim, Yeniden
  başlat, Kapat) + DiskUltimate kendiliğinden açılır. Kapatılırsa simgeden
  yeniden açılır. Root olarak çalışıldığı için ADR 0042 yetki sorusu çıkmaz.
- **GRUB menüsü:** 10 dil (varsayılan English); her girdi `locales=`,
  `keyboard-layouts=` (live-config) ve `diskultimate.lang=` verir. Gelişmiş:
  `toram`, `nomodeset`, fail-safe, bütünlük denetimi, UEFI ayarları.
- **Secure Boot:** `--uefi-secure-boot enable` (Debian imzalı shim + GRUB +
  çekirdek). İmzalı paket bulunamazsa derleme **düşer** (sessizce imzasız
  ISO üretmez).

### Masaüstü seçimi (ölçüldü, Debian 13 paket kapanışı)

Simgeli masaüstü kullanıcı isteğiydi. AppImage tabanına eklenen yük (kurulu):

| Seçenek | Ek paket | Ek kurulu | Ek `.deb` |
|---|---|---|---|
| matchbox (simge yok) | 14 | +5 MB | +2 MB |
| **jwm + pcmanfm** | 76 | +149 MB | **+27 MB** |
| xfwm4 + xfdesktop4 + xfce4-panel | 87 | +115 MB | +21 MB |
| openbox + pcmanfm + tint2 | 96 | +165 MB | +39 MB |
| openbox + pcmanfm-qt + lxqt-panel | 156 | +260 MB | +81 MB |

Xfce biraz küçük ama 4.18+ masaüstü başlatıcılarını GIO meta verisiyle
"güvenilir" işaretlemeyi ister (gvfs yok → her tıklamada soru). pcmanfm'de
`quick_exec=1` yeterli. openbox, imlib2 → libspectre üzerinden Ghostscript
zincirini çekiyor. **jwm + pcmanfm** seçildi.

Simgeler: Adwaita 48'de renkli uçbirim / yeniden başlat / kapat simgesi yok,
SVG yükleyicisi Recommends ile geldiği için tabanda yok →
`tools/live/render_icons.py` QPainter ile PNG çizer (ADR 0025 yöntemi),
PNG'ler depoya girer.

### Güvenlik: açılış belleği sistem diskidir

Live sistemde `/` bir overlay'dir; `physical._list_linux` sistem diskini
`/`'un aygıtından bulduğu için **hiçbir disk** sistem diski sayılmıyordu →
uygulamanın üzerinde çalıştığı USB bellek katman 3 korumasını (disk adını
yazarak onay) almıyordu. Düzeltme: `_linux_boot_medium_disks` live ortamın
bağlama noktalarından (`/run/live/medium`, `/run/live/findiso`, archiso,
dracut, casper) diski bulur; device-mapper (Ventoy) `slaves/`, loop
(findiso) arka dosyanın dosya sistemi üzerinden izlenir. Test: `t110`
(sahte `/sys/class/block`; dd tüm disk, bölüm, Ventoy, findiso, live
olmayan sistem, bilinmeyen aygıt).

## Doğrulama

- `tools/live/boot_check.py`: ISO QEMU'da (KVM) **BIOS**, **UEFI** ve **UEFI +
  Secure Boot** kiplerinde açılır, boş 8 GB ikinci disk eklenir. Oturum
  betiği seri porta `SESSION-START fw=<bios|uefi> sb=<0|1>` ve uygulama
  penceresi (WM_CLASS `DiskUltimate`) görününce `APP-WINDOW <sürüm>` yazar.
  Kip bellenimle karşılaştırılır; Secure Boot için `sb=1` şarttır. Her
  kipte ekran görüntüsü artifact olarak yüklenir.
- Secure Boot denemesi ilk koşularda `continue-on-error`; geçtiği görülünce
  zorunlu yapılacak.
- `remaster.py` yerelde sahte bir BIOS hibrit ISO ile denendi (grub-mkrescue;
  mtools olmadığı için EFI'siz): açılış düzeni raporu yalnızca beklenen
  adres/boyut alanlarında değişti, `sha256sum.txt` doğru güncellendi.

## Reddedilenler

- **Her sürümde tam live-build:** 10–15 dk + Debian aynasına bağımlılık;
  kullanıcı isteği 1'e aykırı.
- **Kalıcı kendi koşucumuz (self-hosted runner):** durum tutardı ama ana
  makinede çalışırdı (ısınma — hafıza notu; diske yazan aracın derlemesi
  ana makinede değil).
- **Önceden kurulmuş GHCR kapsayıcı imajı:** yalnızca `apt install
  live-build` süresini (~30 sn) kazandırır; asıl süre debootstrap + paketler.
- **AppImage yerine Debian'ın `python3-pyqt5`'i + kaynak:** Python 3.13
  sınanmıyor, paketli kopya işareti gerekiyordu; kullanıcı AppImage istedi.
- **AppImage'ı katman yerine ISO kök dizinine koymak:** `toram` ile açılışta
  ortam serbest kalır, dosya kaybolurdu; katmanı live-boot RAM'e kopyalar.

## Açık işler

- İlk CI koşusunun sonucu (taban kuruluyor mu, üç kip açılıyor mu, boyut).
- Gerçek donanımda en az iki makinede deneme; o zamana dek "deneysel".
- `fonts-wqy-microhei`'nin Japonca/Korece glif kapsamı gözle doğrulanmalı.
- Lisans: `*.packages.txt` manifesti release'e girer; kaynak için
  snapshot.debian.org yazılı teklifi README'ye eklenecek.
