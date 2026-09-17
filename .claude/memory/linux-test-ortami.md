---
name: linux-test-ortami
description: "Testler sanal makinede kosar; Linux misafiri Mint 22.3 (ssh pc@192.168.42.131, parola 1234), 2026-09-17'den beri UEFI kipinde"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 92330433-46df-4323-b693-bb19ed258a02
  modified: 2026-09-17T12:25:24.396Z
---

Kullanici 2026-09-17'de kural olarak koydu: **testler ana makinede degil sanal
makinede calistirilir.** Linux hedefi Mint 22.3, `ssh pc@192.168.42.131`,
kullanici `pc`, parola `1234`. Ana makinede Windows'tan baglanirken `sshpass`
yok; PuTTY'nin `plink -batch -ssh -l pc -pw 1234` komutu kullanilir
(`/c/Program Files/PuTTY/plink`, dosya kopyalama icin `pscp`).

**Ikinci kural (ayni gun):** *"yazilimi teste baslayinca onay bekle"* — VM
hazirligi serbest, ama **yazilimin kendi testlerini calistirmadan once
kullanicidan onay alinir.**

## VM'in olculmus durumu

Python 3.12.3 + PyQt5 kurulu. `grub-install`, `update-grub`, `os-prober`,
`efibootmgr`, `pkexec` var. Diskler: `sda` (60 GB, sistem: 1 MB BIOS boot +
513 MB ESP + ext4 kok) ve `sdb` (10 GB, bos NTFS — yikici denemeler icin).

**2026-09-17'de BIOS'tan UEFI'ye cevrildi.** Gereken uc degisiklik (ikisi
beklenmedikti):

1. `firmware = "efi"` — asil istenen.
2. **Diskler LSI Logic SCSI'den SATA'ya alindi.** VMware'in EFI bellenimi
   LSI Logic parallel SCSI icin surucu tasimaz; disk gorunmuyor ve
   *"No compatible bootloader found"* veriyordu.
3. **`guestOS = "ubuntu"` → `"ubuntu-64"`.** VMware EFI ROM'unun bit
   genisligini bu satirdan secer; 32-bit ROM 64-bit `BOOTX64.EFI`'yi
   acamiyordu.

`mint.nvram` silinip yeniden urettirildi. Misafir ilk UEFI acilisinda
`\EFI\BOOT\BOOTX64.EFI` yedek yolundan acildi ve `Boot0005* Ubuntu` girisini
kendisi olusturdu.

## Yedek

`C:\VM-Yedek\mint-bios-20260917` — 26 dosya, 38 GB (vmx, nvram ve kullanilan
butun vmdk uzantilari). Geri yukleme yordami ayni klasordeki
`GERI-YUKLE.md` icinde; `MANIFEST.txt` her dosyanin bayt boyutunu tasir.
Calisir UEFI yapilandirmasi ayrica `uefi-calisir-yapilandirma/` altinda.

## Neden

Proje diske yazan bir arac; onyukleyici ve bellenim islemleri yanlis gittiginde
makineyi acilmaz birakir. Ana makine bu riski tasimamali. Windows misafiri icin
[[windows-test-ortami]].
