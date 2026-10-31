> [!WARNING]
> **Beta.** DiskUltimate writes to disks. Back up important data first and try new operations on a disk image before using them on a real disk.

## Download

| Platform | File | Notes |
|---|---|---|
| **Windows 10 (1809+) / 11 (64-bit)** | `DiskUltimate-{VERSION}-windows-x64.exe` | Single portable file. Asks for administrator rights at start-up (decline to work with disk images only). Not code-signed: SmartScreen → **More info → Run anyway**. |
| **Linux (64-bit)** | `DiskUltimate-{VERSION}-x86_64.AppImage` | `chmod +x` and run; Python and Qt 6 are inside. Needs glibc 2.34+ (Ubuntu 22.04, Debian 12, Fedora 35, RHEL 9 or newer). Asks for root through `pkexec`. Without FUSE: `--appimage-extract-and-run`. |
| **Live USB/DVD (64-bit)** | `DiskUltimate-{VERSION}-live-amd64.iso` | Boots any 64-bit PC (BIOS or UEFI, Secure Boot supported) straight into DiskUltimate without installing anything: write it to a USB stick (Rufus in DD mode, balenaEtcher, Ventoy or `dd`) and boot from it. Choose the language in the boot menu; wired network connects automatically, Wi-Fi is picked from the taskbar, and the screen resolution can be changed (Display Settings). Debian 13 based; package list: `DiskUltimate-{VERSION}-live-amd64.packages.txt`. |
{MACOS_ROW}
Running from source works everywhere: see the [README](https://github.com/{REPO}#download-and-install). Checksums: `SHA256SUMS`.

Built and tested automatically by GitHub Actions from tag `{TAG}` ([run]({RUN_URL})).

## Türkçe

- **Windows:** `DiskUltimate-{VERSION}-windows-x64.exe` — taşınabilir tek dosya; açılışta yönetici yetkisi ister. İmzasız olduğu için SmartScreen'de **Ek bilgi → Yine de çalıştır**.
- **Linux:** `DiskUltimate-{VERSION}-x86_64.AppImage` — `chmod +x` ile çalıştırılabilir yapın; glibc 2.34+ gerekir (Ubuntu 22.04, Debian 12, Fedora 35, RHEL 9 ve sonrası), `pkexec` ile root ister.
{MACOS_TR}
- **Live USB/DVD:** `DiskUltimate-{VERSION}-live-amd64.iso` — kurulum gerektirmeden açılır (BIOS ve UEFI, Secure Boot destekli). Rufus (DD kipi), balenaEtcher, Ventoy ya da `dd` ile USB belleğe yazıp ondan açın; dili açılış menüsünden seçin; kablolu ağ kendiliğinden bağlanır, kablosuz ağ görev çubuğundan seçilir, ekran çözünürlüğü Ekran Ayarları'ndan değiştirilir. Debian 13 tabanlı.
