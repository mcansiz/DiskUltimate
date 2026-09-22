# 0043 — Bolum baglama / cikarma ve surucu harfi

Tarih: 2026-09-21
Durum: kabul edildi
Tasarim: 2026-09-18 oturumunda konusuldu, NTFS `$Secure` hatasi (ADR 0037)
one alindigi icin bekledi; simdi yazildi.

## Sorun

Kullanici bir bolumu bicimlendirdikten sonra icine dosya koymak istediginde
uygulamadan cikip isletim sisteminin araclarina gitmek zorundaydi. Mint'in
Disk uygulamasinda, GParted'da ve DiskGenius'ta bu islem yerinde duruyor.

## Uc platform, uc kavram (olculdu)

| | Baglamak | Cikarmak |
|---|---|---|
| Linux | `udisksctl mount -b` (polkit) veya `mount` (root) | `umount` |
| Windows | **Surucu harfi atamak**: `Add-PartitionAccessPath -AssignDriveLetter` | `Remove-PartitionAccessPath` |
| macOS | `diskutil mount` -> `/Volumes/<ad>` | `diskutil unmount` |

Windows'ta kullanici icin "mount" diye bir sey yoktur: birim zaten dosya
sistemi yiginina baglidir, gorunurlugu belirleyen sey harftir.

## Kararlar

1. **Etiket platformun kavramini kullanir.** Linux/macOS'ta "Bagla / Cikar",
   Windows'ta "Surucu harfi ata / Surucu harfini kaldir"
   (`platform.mount_action_labels()`). Olmayan bir kavramin adini kullanmak
   kullaniciyi yanlis yere goturur.
2. **Kuyruga girmez.** Baglama yikici degildir: sektor yazilmaz, bolum
   tablosuna dokunulmaz. ADR 0025 yalnizca yikici islemleri kuyruga alir;
   bu islem tiklaninca calisir.
3. **Root iken `/media/<kullanici>/<etiket>` altina kendimiz baglariz.**
   Olculdu: `runuser -u <kullanici> -- udisksctl mount` **calismiyor** —
   olusan surec aktif oturuma ait olmadigi icin polkit `allow_active`
   kuralini uygulamiyor ve etkilesimli dogrulama istiyor. Bunun yerine
   baglama noktasi kullaniciya `chown` edilir; sahiplik secenegi kabul eden
   dosya sistemlerinde (FAT/exFAT/NTFS) `uid=`/`gid=` verilir. Root
   degilken (nadir) `udisksctl` kullanilir, orada polkit sorun cikarmaz.
4. **Calisan sistemin bolumu cikarilmaz.** `/`, `/boot`, `/boot/efi`,
   `/usr`, `/var`, `/etc`, `/home` (Windows'ta sistem surucusu) reddedilir.
   Kok dosya sistemini ayirmak makineyi aninda kullanilamaz hale getirir.
5. **Zorlama bayragi yok.** `umount -l` / `-f` kullanilmaz: tembel cikarma,
   dosya sistemi hala yazilirken aygiti serbest birakip veriyi riske atar.
   Bunun yerine **sinirli yeniden deneme** vardir (3 kez, artan bekleme):
   yeni baglanan birimi isletim sistemi bir sure yoklar ve ilk deneme
   "target is busy" diyebilir (olculdu; ikinci deneme geciyor).
6. **Ust uste baglanmaz.** Hedef nokta zaten bir baglama noktasiysa
   `<etiket>-2` denenir. Olculdu: basarisiz bir cikarmadan kalan nokta,
   sonraki baglamayla ust uste binip alttaki dosya sistemini gorunmez
   kilmisti.
7. **Uygula penceresindeki uyarinin yaninda cozum durur.** "Bu diskte bagli
   bolumler var" uyarisina **Baglantilari kes** dugmesi eklendi; basarisiz
   olursa islem devam etmez (bagli bir dosya sistemi altindan ham sektor
   yazmak onu bozar).
8. **macOS dali yazildi ama test EDILMEDI** — projede macOS kosumu yok.

## Baglama noktasinin gosterilmesi

Kullanicinin ikinci istegi: bolumun **nereye bagli oldugu** gorunsun.

- `Partition.mount_point` alani eklendi (diskten okunmaz; disk taramasindan
  gelir).
- Kaynak `DiskInfo.mount_map`tir: **{bolum baslangici (bayt): nokta}**.
  Linux'ta `/sys/block/<disk>/<bolum>/start` + `/proc/mounts` ile, Windows'ta
  `IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS`in **StartingOffset** alaniyla
  doldurulur — o cagri zaten harfleri ogrenmek icin yapiliyordu, ek maliyeti
  yoktur. Bolum basina isletim sistemine sormak Windows'ta her bolum icin bir
  aygit tutamaci demekti (ADR 0021).
- Gosterildigi yerler: bolum tablosunda **yeni sutun** (basligi platforma
  gore "Baglama noktasi" / "Surucu harfi"), harita blogunda dosya sistemi
  adinin sagina (sigmiyorsa yazilmaz), bolum bilgisi sekmesinde bir satir.
- **Sinir:** Windows'ta uygulamanin **kendi actigi** diskin harfleri bolume
  eslenemez — o diskin birimleri yoklanmaz (kilitlenme riski, ADR 0021), harf
  yalnizca disk duzeyinde bilinir. macOS'ta harita bos kalir, bolum basina
  `diskutil` sorulur (test edilmedi).

## Katmanlar

- `core/platform.py`: isletim sistemi cagrilari (`mount_partition`,
  `unmount_partition`, `partition_mount_point`, `partition_device`,
  `mount_action_labels`).
- `core/physical.py`: guvenlik katmani (`is_critical_mount` ve sarmalayicilar).
- `ui/main_window.py`: menu eylemleri; islem `run_task` ile is parcaciginda
  calisir — `udisksctl` polkit penceresi acabilir, PowerShell saniyeler
  surebilir (CLAUDE.md donma kurali).

## Dogrulandi (ana makine, loop aygiti — fiziksel diske dokunulmadi)

Root olarak, uygulamanin kendi kodu ile uretilen GPT + FAT32 goruntu
`losetup -fP` ile baglanip:

```
bagla -> True /media/pc/DENEME
nokta sahibi: uid=1000 gid=1000       (root degil)
dosya sahibi: uid=1000 gid=1000       (uid= secenegi calisti)
cikar -> True                          (ikinci denemede)
cikardiktan sonra nokta: ''            (klasor de kaldirildi)
```

Testler: `t43_baglama_guvenlik_katmani` (aygit yolu turetimi, platform
etiketleri, kritik nokta korumasi, kok bolumde cikarmanin platform katmanina
**hic inmedigi**), `tests.ui_smoke` (etiketler, goruntu dosyasinda kapali
olma, menude bulunma).
