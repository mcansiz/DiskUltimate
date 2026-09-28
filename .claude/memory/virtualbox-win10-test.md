---
name: virtualbox-win10-test
description: Linux ana makinedeki VirtualBox "win10 " misafiri — guestcontrol ile pc/1234, embeddable Python, testler C:\du-test'te
metadata:
  type: reference
---

Linux ana makinede (bu repo /home/pc/Belgeler/GitHub) VirtualBox misafiri
**`"win10 "`** (adin sonunda bosluk var). Kullanici 2026-09-28'de testler
icin kullanilmasini istedi; [[windows-test-ortami]] ise baska bir makinedeki
VMware misafiridir.

- Giris: `VBoxManage guestcontrol "win10 " run --username pc --password 1234
  --exe 'C:\Windows\System32\cmd.exe' -- /c "<komut>"`. SSH (127.0.0.1:2222)
  yanit vermiyor. `--cwd` yok sayiliyor; `cd /d` kullan. `--exe` ile verilen
  argumanlarda argv0 yazilmaz.
- Paylasim: `\\VBoxSvr\GitHub` (misafirde W:/Z:) = ana makinenin
  `~/Belgeler/GitHub`. Testler paylasimda degil `C:\du-test\DiskUltimate`
  kopyasinda kosar (`robocopy ... /MIR /XD .git sessions .tmp __pycache__`).
- Python `C:\Python312` **embeddable**: `python312._pth` cwd ve PYTHONPATH'i
  yok sayar. `C:\du-test\kos.bat <modul>` sarmalayicisi `runpy` ile kosar,
  ciktiyi `C:\du-test\<modul>.txt`e yazar. `sys.argv[0]` gercek betik yolu
  olmali (t22).
- `ui_smoke` icin `QT_QPA_PLATFORM=offscreen`; font yok, goruntulerde metin
  gorunmez.
- Ikinci disk `du-test-disk.vdi` bagli (SATA 2) — fiziksel disk testi icin
  aday; henuz kullanilmadi, sistem diski asla hedeflenmez.
