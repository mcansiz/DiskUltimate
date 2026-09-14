# Windows 10 (VirtualBox) Test Ortami

## Kurulu durum (2026-09-13)

Bu VM'de ortam **hazir**; asagidaki kurulum adimlarini tekrar yapmaniza gerek yok.

| | |
|---|---|
| **Python** | `C:\Python312\python.exe` — 3.12.8 (tasinabilir/embeddable) |
| **pip** | `C:\Python312\Scripts\pip.exe` — 26.2.1 |
| **PyQt5** | 5.15.11 (Qt 5.15.2), `C:\Python312\Lib\site-packages` |
| **PATH** | `C:\Python312;C:\Python312\Scripts;...` (kullanici duzeyi, kalici) |
| **Proje paylasimi** | `Z:\` = `\\VBOXSVR\duproje` → ana makinedeki proje klasoru |
| **Test kopyasi** | `C:\du-test` (kaynak + test alani) |

Kullanim:

```powershell
python --version          # Python 3.12.8
pip install <paket>       # pip calisir
Z:\windows-setup\DiskUltimate-baslat.bat    # arayuzu baslatir
```

> **Not:** PATH degisikligi kaliciddir ama **zaten acik olan** PowerShell/CMD
> pencereleri eski ortami tasir. Yeni bir pencere acin.

> `python` komutu Microsoft Store'a yonlendiriyorsa: Windows'un "uygulama yurutme
> takma adi" stub'i devrededir. `C:\Python312` PATH'in **basinda** olmalidir —
> su an oyle ayarlanmistir.

---


Ana makinede **SSH port yonlendirmesi hazir**: `127.0.0.1:2222` → VM `22`
Proje, VM'e **`duproje`** adiyla paylasilan klasor olarak baglandi.

## Fiziksel disk sondasi (yalnizca okuma)

`disk-testi-yonetici.bat` dosyasina **sag tik > "Yonetici olarak calistir"**.

PowerShell betikleri varsayilan olarak kapalidir (`ExecutionPolicy Restricted`);
`.bat` sarmalayicisi bu kisiti atlar. Betik dosyasi kullanmadan, yonetici
PowerShell'de tek komutla da calistirilabilir:

```powershell
cd C:\du-test; python -m tests.physical_probe
```

Sonda hicbir diske yazmaz; diskleri listeler, salt okunur acar ve bolum
tablosunu cozumler.

**Yazma testi** (yalnizca VM'e eklenen 2 GB'lik bos test diskinde):

```powershell
cd C:\du-test
python tests\physical_write_test.py \\.\PhysicalDrive1 --onayla
```

Bu betik alti olcut saglanmadikca calismaz (sistem diski degil, bagli bolum yok,
bilgiler eksiksiz, 8 GB alti, aygit acikca verilmis, `--onayla` bayragi).
Sistem diski hedef gosterilirse reddeder.

> **Not:** Tasinabilir Python'da `python -m tests.x` calismaz (calisma dizini
> otomatik olarak modul yoluna eklenmez); betikler **dogrudan dosya olarak**
> calistirilir: `python tests\physical_probe.py`

---

## Sifirdan kurulum (yeni bir VM icin)

1. VM'i baslatin ve Windows'a giris yapin.

2. **Guest Additions** kurulu degilse kurun (paylasilan klasor icin gerekir):
   VirtualBox penceresi > `Aygitlar` > `Misafir Eklentileri CD kalibini tak`,
   sonra CD'deki `VBoxWindowsAdditions.exe` calistirin ve yeniden baslatin.

3. Yonetici PowerShell acin (Baslat > "PowerShell" > sag tik > Yonetici olarak calistir):

   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass -Force
   \\VBOXSVR\duproje\windows-setup\ssh-kur.ps1
   ```

4. Betigin yazdigi **kullanici adini** not alin. Hesabin parolasi yoksa belirleyin:

   ```powershell
   net user KULLANICIADI *
   ```

5. Kullanici adini bana soyleyin — ana makineden baglanip testleri calistiracagim.

## SSH olmadan da test edebilirsiniz

Ayni PowerShell'de:

```powershell
\\VBOXSVR\duproje\windows-setup\testleri-calistir.ps1
```

Bu betik kaynagi `C:\du-test` altina kopyalar ve testleri **orada** calistirir.

## ONEMLI: testleri paylasilan klasorde calistirmayin

`\\VBOXSVR\...` (vboxsf) **seyrek dosya desteklemez**. Test goruntuleri tum
boyutlariyla diske yazilir, ana makinenin diski dolar ve sistem kilitlenebilir —
bu daha once yasandi.

`tests/run_all.py` artik bunu kendisi denetler: seyrek dosya desteklenmiyorsa ve
yeterli bos alan yoksa **testler baslamadan durur**. Yine de dogru yol, testleri
VM'in kendi diskinde (`C:\du-test`) calistirmaktir.
