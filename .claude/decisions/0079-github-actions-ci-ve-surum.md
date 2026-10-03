# 0079 — GitHub Actions: her push'ta test, etikette uc platform + taslak release

Tarih: 2026-10-03
Durum: **uygulandi** — ilk kosu sonuclari worklog'da.
Ilgili: [0050](0050-linux-dagitimi-appimage-ve-musl.md) (AppImage),
CLAUDE.md test ortami kurali.

## Karar (kullanici: macOS deneysel, taslak release, her push'ta test)

* `.github/workflows/tests.yml` — ortak test isi (`workflow_call`):
  platform_check, i18n_check, diag_check, run_all, ui_smoke (offscreen).
  Yalnizca goruntu dosyasi testleri; fiziksel disk testleri CI'da kosmaz.
  Linux'ta capraz dogrulama araclari kurulur (e2fsprogs, ntfs-3g, xfsprogs,
  btrfs-progs, f2fs-tools, hfsprogs, udftools...).
* `ci.yml` — `main`e her push / PR: Linux testleri. Yalnizca `.claude/`,
  `*.md`, `docs/` degisen commit'ler tetiklemez (oturum kaydi commit'leri).
* `release.yml` — `v*` etiketi:
  1. etiket = `v` + `APP_VERSION` degilse durur;
  2. testler Ubuntu 24.04 / Windows 2022 / macOS 14 (arm64);
  3. derleme: AppImage (`build_appimage.sh`), Windows exe (PyInstaller,
     spec), macOS `.app` -> zip (spec'in yeni darwin dali: COLLECT + BUNDLE);
  4. her paket `tools/smoke_launch.py` ile acilir, gunlukte "<surum>
     baslatildi" aranir;
  5. **taslak** release + `SHA256SUMS`, notlar `.github/release-notes.md`.
  macOS deneyseldir: testi/derlemesi duserse release yine olusur, yalnizca
  macOS dosyasi eklenmez. Windows ve Linux sarttir.
  Elle calistirma = deneme: derler, dosyalari calistirmaya ekler, release
  olusturmaz.
* Eylemler commit SHA'si ile sabit; PyQt5/PyInstaller surumleri sabit.
* Checkout `.claude/sessions/` (~250 MB) olmadan (sparse).

## Olculen ayrinti

`DiskUltimate.spec` boyut icin Qt'nin `offscreen` eklentisini Linux/macOS
paketinden atiyor; deneme acilisi bu yuzden `minimal` eklentisiyle yapilir
(ilk denemede "Could not find the Qt platform plugin offscreen"). Deneme
betigi ilk surumde AppImage'in alt surecinin actigi boruda takiliyordu;
cikti dosyaya yazilir ve surec grubu butunuyle kapatilir.
