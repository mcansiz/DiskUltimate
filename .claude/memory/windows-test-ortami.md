---
name: windows-test-ortami
description: VMware Win10 misafirinde Windows yazma testleri; Admin hesabi, cevrimdisi paket kurulumu, paylasilan klasor
metadata:
  type: project
---

Windows'a ozgu yazma testleri (NTFS, fiziksel disk) **VMware Player**
misafirinde kosulur: VM `ltsc`, Windows 10 Enterprise 2016 LTSB x64,
`DESKTOP-GLH638P`. Kanal: `vmrun -T player` (VMware Tools); ag kullanilmaz.

**Kurallar (kullanici, 2026-09-17):**
- **Hesap:** yazma testleri icin **`Admin`** hesabi. `vmrun` ile dogrudan
  yukseltilmis gelir (High Integrity, `Administrators` etkin). `user` hesabi
  `vmrun`'da Orta Duzey kalir (yukseltme icin `schtasks /rl highest /ru user
  /rp <parola>`).
- **Cevrimdisi:** misafir **internete acilmaz** (`ethernet0.startConnected =
  FALSE`). Paketler ana makinede indirilir, paylasilan klasore konur, misafirde
  `pip install --no-index --find-links <klasor>` ile **offline** kurulur.
- **Paylasim:** ana makine `D:\pythonProjeler\DiskUltimate` → misafir
  `\\vmware-host\Shared Folders\DiskUltimate` (proje koku alt klasor
  `...\DiskUltimate\DiskUltimate`). HGFS **oturuma baglidir**; `vmrun` batch
  oturuminda gorunmeyebilir — otomasyonda `copyFileFromHostToGuest` kullan.
- **Fiziksel disk hedefi:** **ikinci** disk `\\.\PhysicalDrive1` (10 GB);
  sistem diski `\\.\PhysicalDrive0` asla hedeflenmez. CreateFile ile acilir.

**Kosumdan once misafirde artik surec birakilmaz.** `tests.ui_smoke`
misafirde bir kez 23. ekran goruntusunden sonra kilitlendi (CPU 0); sebebi
onceki kosumdan kalan ikinci bir `python.exe` idi. Ayni aygita iki yerden
dokunmak surucu yiginini asili birakiyor (ADR 0021). Otomasyonda her kosumdan
once `taskkill /f /im python.exe` calistirilir.

Parola depoya yazilmaz (`.claude/` commit ediliyor). Detay ve prosedur:
`.claude/docs/testing.md` "Windows dogrulama ortami". Ilgili: fiziksel disk
guvenlik kurallari (CLAUDE.md), [[yetki-yukseltme]].
