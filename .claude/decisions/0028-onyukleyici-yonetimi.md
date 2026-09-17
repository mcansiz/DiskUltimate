# ADR 0028 — Onyukleyici (GRUB) yonetimi: inceleme her yerde, degistirme Linux'ta

**Tarih:** 2026-09-17
**Durum:** Kabul edildi
**Ilgili:** ADR 0003 (katman ayrimi), ADR 0004 (yikici islem guvenligi),
ADR 0008 (capraz platform stratejisi), ADR 0014 (fiziksel disk destegi),
ADR 0020 (donma yakalayici), ADR 0025 (bekleyen islem kuyrugu),
ADR 0029 (UEFI onyukleme duzenleyici)

## Baglam

Kullanici [exxos-easy-grub-manager](https://github.com/exxosuk/exxos-easy-grub-manager)
uygulamasinin projeye entegre edilmesini istedi: *"gerekli uygulama durumlarini
analiz et, projeyi degistirebilirsin bize gore uygun olsun, capraz platform
destegi saglansin."*

Kaynak uygulama tek dosyalik (1144 satir) bir PyQt5 aracidir, GPL-3.0 —
DiskUltimate ile ayni lisans, yani kod devralmak lisans acisindan
serbesttir. Yetenekleri:

| Yetenek | Kaynaktaki gerceklestirme |
|---|---|
| Onyuklenebilir bolumleri bul | `lsblk -J` + her bolumu `mount -o ro` ile acip dosyalara bak |
| Diskte GRUB var mi | `dd if=$dev bs=512 count=1 \| strings \| grep -i GRUB` |
| GRUB kur / kaldir | `pkexec grub-install $dev` / `dd if=/dev/zero of=$dev bs=440 count=1` |
| Menuyu uret | `pkexec update-grub` |
| Ayarlari yedekle/geri yukle | `shutil.copy` + `pkexec cp` |
| Tumunu onar | yedek al, os-prober'i ac, GRUB'un **zaten oldugu** disklere kur, menuyu uret |

### Kaynaktaki uc sorun

1. **Yalnizca Linux'ta ve yalnizca root'ken calisir.** Bolum incelemesi
   `mount` cagrisina dayanir; DiskUltimate Windows ve macOS'ta da calisir ve
   orada bu yol bastan kapalidir.
2. **`strings | grep GRUB` yaniltilabilir.** Komut butun 512 baytta arar;
   onyukleme kodu yalnizca **ilk 440 bayttir**, gerisi bolum tablosudur.
   Tablodaki rastgele dort bayt "GRUB" olursa disk GRUB'lu sanilir.
3. **`dd ... bs=440` dogru ama kuyruga girmez.** Tiklama aninda diske yazar;
   bu, ADR 0025'in butun kuyruk modelini ve ADR 0014'un fiziksel disk koruma
   katmanlarini atlar.

Ayrica DiskUltimate'in elinde kaynagin sahip olmadigi bir sey var: **kendi
dosya sistemi surucileri** (FAT, exFAT, NTFS, ext2/3/4). Bir bolumu okumak
icin isletim sistemine baglatmasi gerekmiyor.

## Karar

### 1. Inceleme ile degistirme ayrilir

| Nerede | Ne yapar | Nerede calisir |
|---|---|---|
| `core/bootloader.py` | Onyukleme kodunu tanir, bolumlerdeki sistemleri bulur, `grub.cfg` ve varsayilanlari cozer | **Her platform**, yetki gerekmeden, goruntu dosyalarinda da |
| `core/grub.py` | `grub-install`, `update-grub`, yedekle/geri yukle, onar | Yalnizca calisan Linux sistemi |
| `core/platform.py` | Arac yollari, yetki yukseltme, sistem dosyasi yazma | Isletim sistemi farkinin **tek** durdugu yer |

Bu ayrim CLAUDE.md'nin capraz platform kuralinin dogrudan sonucudur, ama asil
kazanci baska: **inceleme test edilebilir hale gelir.** Uretilmis bir `.img`
uzerinde "bu bolumde Linux Mint kurulu, sunda yalnizca yedek var" dogrulamasi
Windows'ta bile kosar (`tests.run_all` t31).

### 2. Isletim sistemi tespiti dosya sistemi turune degil, **dosyalara** bakar

Dosya sisteminin turu kanit degildir: ext4 bir bolum, kurulu bir sistem kadar
kolaylikla bir yedek diski olabilir. Her aday acilir ve gercek bir kurulumun
tasidigi dosyalar aranir:

- Windows: `\Windows\System32\config\SYSTEM` (kayit defteri kovani)
- Windows onyukleme bolumu: `\bootmgr` + `\Boot\BCD`
- Linux: `/etc` + bir surum dosyasi (`os-release`, `lsb-release`, `mx-version`, ...)
- ESP: `\EFI` klasoru (tur GUID'i yanlis olsa bile)

Arama **buyuk/kucuk harf duyarsizdir** (`bootloader.find_path`): ayni dizin
NTFS'te `Windows`, FAT'te `WINDOWS`, ext4'te `windows` yazilabilir.

Sistem bulunmayan bolum icin **neden** saklanir ve arayuzde gosterilir.
"Listede yok" demek, kullanicinin aradigi sistemi neden goremedigini
anlatmaz.

### 3. Onyukleme kodu yalnizca ilk 440 bayttan taninir

`identify_boot_code()` sektorun **yalnizca** `[0:440]` dilimine bakar ve
onyukleyicilerin kendi hata iletilerini arar (`GRUB \0Geom\0Hard Disk...`,
`Invalid partition table`, `ISOLINUX`, `LILO`). Bu dizeler surumler arasi
degismez ve surum numarasindan daha guvenilir bir parmak izidir.

Test bu farki dogrudan olcer: bolum tablosunun icine `GRUB` yazilir ve diskin
GRUB'lu **sanilmamasi** beklenir (t30).

### 4. Onyukleme kodunu kaldirmak kuyruga girer, GRUB kurmak girmez

Ayrim kuyrugun tanimindan gelir: kuyruk **acik bir diske yazilan** adimlari
tasir.

- **Kaldirma** gercekten sektor yazmadir → `operations.KINDS["clear_boot_code"]`,
  yikici isaretli. `session.clear_boot_code()` ilk 440 bayti sifirlar ve bolum
  tablosunu **korur**; `_require_writable()` uzerinden ADR 0014'un butun
  kapilarindan gecer.
- **Kurma** acik diske degil calisan sisteme is yaptirir: paket dosyalarini
  okur, `/boot` altina modul yazar, aygita gomer. Kuyruga koymak kuyrugun
  anlamini bozardi. Kendi onayi ve ilerleme penceresiyle calisir.

`grub-install`i yeniden yazmak **denenmedi ve denenmeyecek**: o arac hedef
diskin BIOS/UEFI kipini, kurulacak modulleri ve `/boot` icerigini bilir.
Yeniden gerceklestirmek, calisan bir makineyi acilmaz birakma riskini
ustlenmek olurdu.

### 5. "Tumunu onar" yalnizca GRUB'un **zaten oldugu** diske yazar

Kaynak uygulamanin dogru kararidir ve korunmustur. Makinedeki her diske GRUB
yazmak onarim degildir; calisan diskleri degistirmektir — baska bir makineye
takilan bir yedek disk birden bu sistemi acmaya calisirdi. Hicbir diskte GRUB
yoksa hedef **secilmez**, kullaniciya soylenir.

### 6. Yapilandirma dosyasi satir satir duzenlenir

`GrubDefaults` dosyayi satir listesi olarak tutar: yorumlar, bos satirlar ve
sira korunur. Yoruma alinmis bir anahtar **yerinde** acilir, sona
eklenmez. Sozluge cevirip geri yazmak kullanicinin kendi notlarini silerdi —
bir yapilandirma dosyasini duzenleyen aracin en cok kizdiran davranisi budur.

`GRUB_DISABLE_OS_PROBER` ayrica ele alinir: Debian 12 ve Ubuntu 22.04'ten beri
varsayilan `true`dur ve "GRUB Windows'u gormuyor" sikayetinin bir numarali
nedeni budur.

### 7. Okunamayan ayar "kapali" degildir

`GrubStatus.summary()` varsayilanlar dosyasi okunamadiginda os-prober icin
**"Bilinmiyor"** yazar. Bu, CLAUDE.md'nin "bilgisi eksik disk" kuralinin ayni
gerekceyle buradaki karsiligidir: bilinmeyeni bilinen gibi sunmak yaniltir.

## Sonuclar

**Kazanilan**

- Onyukleme incelemesi Windows, Linux ve macOS'ta ayni kodla calisir; goruntu
  dosyalarinda da calisir ve `mount` ile root gerektirmez.
- Onyukleme kodunun kaldirilmasi artik fiziksel disk koruma katmanlarindan
  geciyor ve tek "Uygula" ile, bolum tablosunu bozmadan calisiyor.
- Tanima, bolum tablosundaki rastgele baytlarla yaniltilamiyor.

**Kabul edilen sinirlar**

- GRUB **kurulumu** Windows ve macOS'ta yoktur; arayuz dugmeyi pasif yapar ve
  nedenini ipucunda yazar. Calismayacak bir dugmeyi etkin gostermek yaniltici
  olurdu.
- Paket guncellemesi yalnizca `apt` tabanli dagitimlarda yapilir; digerlerinde
  hata degil "yapilamadi" denir, cunku onarimin zorunlu parcasi degildir.
- `btrfs`, `xfs` ve `f2fs` bolumlerde sistem tespiti yapilamaz: projenin bu
  dosya sistemleri icin surucusu yok. Tur taninir, icerik okunamaz ve bu
  **neden** olarak yazilir.

## Olcum

`tests.run_all` icinde (Linux Mint 22.3 misafirinde kosuldu):

- **t30** — onyukleme kodu tanima; bolum tablosuna yazilan `GRUB` baytinin
  yanlis eslesme uretmedigi; kaldirmanin bolum tablosunu, MBR imzasini ve
  bolumun acilabilirligini koruduguf; adimin kuyruktan calistigi.
- **t31** — ESP tanima ve `.efi` yukleyicilerinin listelenmesi; kurulu Linux
  ile ayni dosya sistemindeki veri bolumunun ayirt edilmesi; harf
  buyuklugunden bagimsiz yol aramasi.
- **t32** — `GrubDefaults` duzenlemesinde yorumlarin, siranin ve yerinde
  guncellemenin korunmasi; `grub.cfg` menu/alt menu cozumlemesi.
