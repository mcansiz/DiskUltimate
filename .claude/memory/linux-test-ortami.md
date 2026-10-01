---
name: linux-test-ortami
description: "2026-09-29'dan beri Linux VM yok; Linux testleri ana makinede YALNIZCA goruntu dosyalariyla, fiziksel/Windows testleri VirtualBox win10'da"
metadata:
  type: feedback
---

**2026-09-29 kullanici karari:** *"sanal linux elimde yok; ana pc de sanal disk
dosyasinda calismalarini veya virtualbox win10 calismalarini yap."*

- Linux tarafi testler (`tests.run_all`, fsck/e2fsck/xfs_repair/btrfs check ile
  dogrulama) **ana makinede, yalnizca goruntu dosyalari** uzerinde kosar
  (`.tmp/tests/`). Ana makinenin **fiziksel diskine yazan hicbir deneme
  yapilmaz**; `tests.physical_*` ana makinede kosmaz.
- Fiziksel disk ve Windows testleri: [[virtualbox-win10-test]] (ikinci disk
  `du-test-disk.vdi`).
- Ana makinede root yok (sudo sifreli) → `mount` ile cekirdek dogrulamasi
  yapilamaz; dogrulama kullanici alani araclariyla (`e2fsck -fn`,
  `xfs_repair -n -f`, `btrfs check`, `fsck.vfat`, `blkid`) yapilir. Cekirdegin
  bagladigi iddia edilmez.

**Eski durum:** Mint 22.3 VM (`ssh pc@192.168.42.131`) — artik kullanilamiyor.

**Why:** proje diske yazar; ana makinede yalnizca goruntu dosyasi guvenlidir.
**How to apply:** testleri onay beklemeden goruntu dosyalariyla ana makinede
kos; fiziksel disk gerekiyorsa VirtualBox win10'a git.
