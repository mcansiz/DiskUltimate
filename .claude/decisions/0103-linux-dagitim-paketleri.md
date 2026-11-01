# 0103 — Linux dağıtım paketleri (.deb, .rpm, AUR) ve AppImage güncellemesi

Tarih: 2026-11-01
Durum: **uygulandı (CI'da doğrulandı)** — paketler temiz dağıtım
kapsayıcılarında kuruluyor, açılıyor ve iz bırakmadan kaldırılıyor.
Gerçek masaüstünde polkit parola penceresi **henüz denenmedi**.
İlgili: [0050](0050-linux-dagitimi-appimage-ve-musl.md) (AppImage; .deb'i
"ileride ek seçenek" diye not etmişti), [0042](0042-acilista-kosulsuz-yetki.md)
(açılışta yetki), [0102](0102-pyqt6-gecisi-uyum-katmani.md) (PyQt5 yolu
korunur), [0101](0101-live-iso.md).

## Kullanıcı isteği

"Flathub'da yayınlayabilir miyiz?" → UDisks2 denemesi (worklog 2026-10-28)
Flatpak'in mümkün ama uygulamanın yetki modelini baştan değiştirmeyi
gerektirdiğini gösterdi. Kullanıcı: "flatpak bizim için uygun değil gibi,
başka önerin var mı?" Öneri: dağıtımın kendi paketleri + AUR + AppImage
güncellemesi. Kullanıcı: "önerini uygula".

Flathub'ın asıl kazancı tek yerden kurulum ve otomatik güncellemeydi. Bu
paketler bunu korumalı ortam olmadan sağlar; yetki modeli (açılışta
`pkexec`) olduğu gibi kalır.

## Karar

### 1. Mimariden bağımsız .deb ve .rpm

`tools/linux_packages.py` tek bir dosya düzeni (`stage()`) tanımlar; .deb
(`dpkg-deb`), .rpm (`rpmbuild`) ve AUR PKGBUILD aynı düzeni kurar.

| Yol | İçerik |
|---|---|
| `/usr/bin/diskultimate` | başlatıcı (`python3 /usr/share/diskultimate/main.py`) |
| `/usr/lib/diskultimate/diskultimate-root` | pkexec yardımcısı |
| `/usr/share/diskultimate/` | `main.py`, `src/`, `system-install` işareti |
| `/usr/share/polkit-1/actions/io.github.mcansiz.DiskUltimate.policy` | 10 dilde parola penceresi metni |
| `/usr/share/applications/`, `metainfo/`, `icons/hicolor/` | menü, yazılım merkezi, ikonlar (16–256) |

- **Python ve PyQt dağıtımdan gelir.** Paket ~1 MB, glibc sınırı yok.
  Bağımlılık "PyQt6 ya da PyQt5" — 0102'de korunan PyQt5 yolu burada iş
  görüyor: Ubuntu 22.04'te `python3-pyqt6` yok, paket PyQt5 ile açılıyor.
- **Bayt kodu kurulumda derlenir** (deb `postinst`, rpm `%post`): her Python
  sürümüne uyar; kullanıcı `/usr/share` altına `__pycache__` yazamadığı
  için aksi halde her açılışta bütün modüller yeniden derlenirdi. Kaldırırken
  temizlik **Python ile** yapılır: openSUSE kapsayıcısında `find` yoktu,
  `find`'lı temizlik sessizce düşüp klasörü bırakırdı (ölçüldü).
- **Arch:** AUR'a `PKGBUILD` (+ `.SRCINFO`). Kaynak etiketin GitHub arşivi;
  `package()` aynı `stage()`'i çağırır, bayt kodu orada derlenir (Arch'ın
  Python'u tektir).
- Sürüm eşlemesi: `0.11.0-beta` → deb/rpm `0.11.0~beta`, Arch `0.11.0beta`;
  üçünde de ön sürüm son sürümden küçük sıralanır.

### 2. pkexec yardımcısı ve polkit tanımı

Önceki yol `pkexec env AD=DEĞER ... python3 main.py` idi; polkit penceresi
"`/usr/bin/env` çalıştırmak için kimlik doğrulama" gösteriyordu. Polkit
eylemi bir **program yoluna** bağlanır (`org.freedesktop.policykit.exec.path`);
bu yüzden sabit yollu bir yardımcı gerekir.

- `paths.SYSTEM_INSTALL` (işaret dosyası `system-install`: `format`,
  `helper`) → `platform.linux_pkexec_command` yardımcıyı kullanır:
  `pkexec <yardımcı> AD=DEĞER ... -- <uygulama argümanları>`.
  İşaret yoksa (kaynak, AppImage) eski yol değişmez.
- **Yardımcı programı seçtirmez.** Yalnızca `DISPLAY`, `XAUTHORITY`,
  `WAYLAND_DISPLAY`, `XDG_RUNTIME_DIR`, `QT_QPA_PLATFORM`, `DISKULTIMATE_*`
  kabul eder; başka argüman gelirse çıkış 2 (t112 sınar).
- **Eylem `auth_admin`, `auth_admin_keep` değil.** `DISKULTIMATE_LOG_DIR`
  root olarak yazılır; `_keep` ile aynı oturumdaki herhangi bir süreç
  birkaç dakika boyunca parolasız bir "root olarak şuraya yaz" kapısı
  bulurdu. Uygulama açılışta bir kez sorduğu için (0042) bedeli yok.
- İşaret dosyası `IS_PACKAGED`'i de açar: günlük ve geçici alan
  kullanıcının dizinine gider (`/usr/share` yazılamaz).

### 3. AppImage kendini günceller

`appimagetool -u "gh-releases-zsync|mcansiz|DiskUltimate|latest-all|DiskUltimate-*-x86_64.AppImage.zsync"`
ve release'e `.zsync` dosyası. Gear Lever ve AppImageUpdate bunu okuyup
yalnızca değişen blokları indirir.

- **`latest-all`, `latest` değil:** bütün sürümlerimiz ön sürüm; `latest`
  GitHub'ın `/releases/latest` ucunu kullanır ve ön sürümleri görmez.
  `latest-pre|all` AppImageUpdate'e 2025-09'da girdi (PR #254,
  2.0.0-alpha-1-20251018); Gear Lever de tanıyor (`GithubUpdater.py`).
- Güncelleme bilgisi **eski** AppImage'ın içinde okunur: 0.11.0-beta'da yok,
  ilk etkili güncelleme bu değişikliği içeren sürümden **sonraki** sürüm.
- Release: `zsyncmake` (paket `zsync`) kurulur, `.zsync` yoksa derleme düşer.

### 4. Kaynak arşivi küçüldü

`.gitattributes`: `/.claude/ export-ignore`. GitHub'ın "Source code"
arşivi ve `git archive` artık oturum dökümlerini içermez: 206 MB → ~7 MB
(sıkıştırılmış). AUR her kurulumda bu arşivi indirir. Depo değişmez,
`.claude/` public kalır.

### 5. Masaüstü girdisi ortak ve doğru yazımla

`desktop_entry()` hem paketlerde hem AppImage'da kullanılır; 10 dilde.
Eski AppImage girdisi ASCII'ydi ("Datentragerverwaltung", "Disk
Yoneticisi") — CLAUDE.md yazım kuralına aykırıydı.

## Doğrulama

`.github/workflows/linux-packages.yml` (release'e bağlı, şart): .deb/.rpm
derlenir; `desktop-file-validate`, `appstreamcli validate`, `xmllint`
geçer. Her dağıtım kapsayıcısında: kendi paket yöneticisiyle kurulum
(bağımlılıklar oradan) → `__pycache__` var mı → `linux_pkexec_command`
yardımcıyı mı çağırıyor → `smoke_launch` (dağıtımın PyQt'siyle açılış) →
kaldırma → iz yok mu. Run 41304523609 (commit 70aa906): 7/7 yeşil.

| Kapsayıcı | Bağlama (ölçülen) |
|---|---|
| debian:12 | PyQt6 6.4.2 / Qt 6.4.2 |
| debian:13 | PyQt6 6.9.0 / Qt 6.8.2 |
| ubuntu:22.04 | **PyQt5** 5.15.6 / Qt 5.15.3 (`python3-pyqt6` yok) |
| ubuntu:24.04 | PyQt6 6.6.1 / Qt 6.4.2 |
| fedora:latest | PyQt6 6.11.0 / Qt 6.11.2 |
| opensuse/tumbleweed | PyQt6 6.12.0 / Qt 6.12.0 (kapsayıcıda `find` yok — temizlik Python'la) |
| archlinux:latest (makepkg) | PyQt6 6.12.0 / Qt 6.12.0 |

Yerel: `t112` (düzen, işaret, pkexec komutu, yardımcının ret yolu),
`platform_check` 0, `diag_check` 13/13; paketten çıkan ağaç `smoke_launch`
ile açıldı ve günlüğü `~/.local/state/DiskUltimate` altına yazdı.

**Sınanmayan:**
- Gerçek polkit parola penceresi ve yardımcı üzerinden root kopyanın
  açılması (masaüstü + polkit ajanı gerekir; Linux VM yok). CI'da
  kapsayıcılar root olarak koşuyor.
- RHEL/Alma/Rocky (EPEL'de PyQt bulunurluğu ölçülmedi).
- AppImage güncellemesinin uçtan uca çalışması (iki ardışık sürüm gerekir).
- Smoke testi yalnızca ana pencerenin açılışını sınar; dağıtımların eski
  PyQt6 sürümlerinde (6.4) bütün pencereler gezilmedi.

## Kullanıcıya kalan

- **AUR:** paket adı `diskultimate`, kullanıcının AUR hesabıyla gönderilir.
  Release'ten sonra `python3 tools/linux_packages.py aur` etiket arşivini
  indirip `dist/aur/PKGBUILD` + `.SRCINFO` üretir; bunlar
  `ssh://aur@aur.archlinux.org/diskultimate.git` deposuna push edilir.
- **Depo (apt/dnf ile otomatik güncelleme):** openSUSE Build Service sonraki
  adım; şimdilik paketler release dosyası.

## Elenenler

- **Flatpak / Flathub:** UDisks2 ile mümkün ama Linux yetki modelinin ikinci
  bir sürümü, sistem diski ve bağlama bilgisinin UDisks2'den okunması,
  önyükleyici/UEFI özelliklerinin kapatılması ve izin incelemesi riski
  (worklog 2026-10-28, 2026-11-01).
- **Snap:** aynı korumalı ortam sorunu; "classic" kip elle onay ister.
- **PyPI / pipx:** `pyproject.toml` yok; PyQt6 tekerlekleri yine yeni glibc
  ister. Dağıtım paketi daha iyi.
- **fpm vb. paketleme aracı:** ek Ruby bağımlılığı; `dpkg-deb` ve
  `rpmbuild` derleme makinesinde zaten var.
