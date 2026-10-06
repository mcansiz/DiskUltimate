---
name: virtualbox-win10-test
description: "Linux ana makinedeki VirtualBox \"win10 \" misafiri — guestcontrol ile pc/1234, embeddable Python, testler C:\\du-test'te"
metadata:
  node_type: memory
  type: reference
  originSessionId: 4bbcf40c-45fc-4987-bb49-97c541c610b1
  modified: 2026-10-06T19:14:18.690Z
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

**2026-10-03 durumu:** kullanici VM'i duzeltti; sistem diski artik
`wtest55.vdi` (SATA-0). `du-test-disk.vdi` takili degil. NTFS testi icin
`.tmp/vm/du-ntfs-test.vdi`/`du-ntfs-fixed.vdi` SATA-2'ye takildi (silinebilir).
- Guestcontrol **yonetici degil** (olculdu: `pc` Administrators uyesi ama
  UAC belirteci suzuyor — grup "deny only", Orta zorunlu duzey S-1-16-8192;
  EnableLUA=1; yerlesik Administrator hesabi devre disi). `schtasks /rl
  highest` de "Erisim engellendi" diyor.
- **2026-10-03 cozuldu:** kullanici yerlesik hesabi acti (`net user
  Administrator 1234 /active:yes`). `--username Administrator --password
  1234` ile oturum **tam yetkili** gelir (Administrators etkin, S-1-16-12288,
  `fltmc` calisir). Yonetici isleri (diskpart, fsutil, chkdsk, fiziksel disk)
  bu hesapla. `net session` yine hata verir (Sunucu hizmeti) — olcut degil.
  Eski not (yonetici yok): Yoneticisiz yol: diski ana makinede hazirla, Windows
  kendiliginden baglar (normal kullanici okuyup yazabilir).
- Calisirken takilan disk icin `--hotpluggable on` verilemez (VM degistirilemez
  hatasi, yarim kalan takma); misafiri `shutdown.exe /s /t 0` ile kapatip takmak
  guvenli. ACPI dugmesi kilit ekraninda yok sayiliyor.
- Exe derleme: `robocopy \\VBoxSvr\GitHub\DiskUltimate C:\du-test\DiskUltimate /MIR
  /XD .git sessions .tmp __pycache__ build dist` + `C:\Python312\python.exe -m
  PyInstaller DiskUltimate.spec --noconfirm --clean` (normal kullanici yeter).
- **2026-10-06:** misafirde hazirda bekletme / Hizli Baslangic YOK
  (`powercfg /a`: "firmware does not support hibernation") — hibernation
  kapisi burada sinanamaz; "temiz kapatilmamis birim" icin disk SATA-2'ye
  takilip yazarken `VBoxManage controlvm poweroff` kullanildi.
- Hazirlik denetimi: `guestcontrol run ... cmd /c echo hazir` ciktisi `\r`
  tasir; `[ "$r" = hazir ]` hic tutmaz (10 dk bosa bekledi) — `grep -q`.
- Windows'a birim urettirip geri denetletme betikleri: `.tmp/vm/w1006/`
  (uret.ps1 + doldur.py -> isle.py ana makinede -> dogrula.ps1/dogrula.py).
  VHD paylasimdan `C:\du-test`e kopyalanip `Mount-DiskImage` ile baglanir.
- **Misafirdeki X: ana makinenin `/run/media/pc/Data/myiso` paylasimidir**
  (etiket VBOX_myiso; kullanicinin ISO arsivi). 2026-10-04'te bir betik
  `assign letter=X` basarisiz olunca test dosyalarini oraya yazdi. Misafirde
  surucu harfi atanmaz/varsayilmaz: birim `Get-Volume` + etiketle bulunur,
  `\\?\Volume{...}\` yolu .NET API'leriyle kullanilir (New-Item bu yolu
  kabul etmez). PowerShell 5 BOM'suz .ps1'i ANSI okur — Turkce ad varsa
  UTF-8 BOM ile yazilir.
