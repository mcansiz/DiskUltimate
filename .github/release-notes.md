> [!WARNING]
> **Beta.** DiskUltimate writes to disks. Back up important data first and try new operations on a disk image before using them on a real disk.

## Download

| Platform | File | Notes |
|---|---|---|
| **Windows 10/11 (64-bit)** | `DiskUltimate-{VERSION}-windows-x64.exe` | Single portable file. Asks for administrator rights at start-up (decline to work with disk images only). Not code-signed: SmartScreen → **More info → Run anyway**. |
| **Linux (64-bit)** | `DiskUltimate-{VERSION}-x86_64.AppImage` | `chmod +x` and run; Python and Qt are inside. Needs glibc 2.17+. Asks for root through `pkexec`. Without FUSE: `--appimage-extract-and-run`. |
{MACOS_ROW}
Running from source works everywhere: see the [README](https://github.com/{REPO}#download-and-install). Checksums: `SHA256SUMS`.

Built and tested automatically by GitHub Actions from tag `{TAG}` ([run]({RUN_URL})).

## Türkçe

- **Windows:** `DiskUltimate-{VERSION}-windows-x64.exe` — taşınabilir tek dosya; açılışta yönetici yetkisi ister. İmzasız olduğu için SmartScreen'de **Ek bilgi → Yine de çalıştır**.
- **Linux:** `DiskUltimate-{VERSION}-x86_64.AppImage` — `chmod +x` ile çalıştırılabilir yapın; glibc 2.17+ gerekir, `pkexec` ile root ister.
{MACOS_TR}
