# 0084 — Windows aygit kipi bulgulari: tablo-yalniz NTFS boyutlandirma, exFAT geri kaydirma, gec baglanan birim, NTFS $UpCase:$Info

Tarih: 2026-10-04
Durum: **uygulandi**. Ilgili: [0080](0080-uzun-testler-plani.md),
[0083](0083-uzun-test-bulgulari-exfat-ve-macos-yetki.md),
[0030](0030-ntfs-saf-python-boyutlandirma.md), [0075](0075-windows-birimleri-guid-ile-kilitlenir.md),
[0025](0025-bekleyen-islem-kuyrugu.md).

Uzun testlerin Windows aygit kipi (CI 37196618597, sonra VirtualBox "win10 "
misafirinde `DU_TEST_VM=1` ile yeniden uretildi) dort gercek hata buldu.
Ikisi **veri kaybettiriyordu** ve goruntu kipinde hic gorunmemisti.

## 1. Windows fiziksel diskte NTFS boyutlandirma yalnizca tabloyu yaziyordu

ADR 0030 "Windows + fiziksel disk: `Resize-Partition` tercih edilir" dedi.
Yol `session.disk_info.disk_number` okuyordu; bu alan **hicbir yerde
atanmiyordu**. Sonuc:

* `fs_resize_info` yine `kind="native"` donuyordu (en kucuk 1 sektor),
* `_try_native_resize` numara yok diye sessizce `False` donuyordu,
* plan `apply_resize`'a dusuyordu; "native" FS listelerinin hicbirinde
  olmadigi icin **yalnizca bolum tablosu** yaziliyordu.

Olculdu (VM gunlugu): buyutmede NTFS 145 MiB'ta kaldi (tablo 181 MiB);
ardindan kucultme hedefi "en aza" = **1 MiB** oldu, tablo 1 MiB'a indi,
birim okunamaz hale geldi ("Okuma bolum sinirini asiyor"). Bu hata v0.3.0'dan
beri vardi; 0.5.0-beta dahil yayinlanan Windows surumleri fiziksel diskte
NTFS boyutlandirirken veri kaybettirir.

Duzeltme:
* `DiskSession.windows_disk_number()` — aygit yolundan (bicimlendiricinin
  zaten kullandigi yol); yerel bicimlendirici de bunu kullanir.
* `fs_resize_info_for` numara yoksa "native" **secmez**.
* `resize_info`: Windows sinir bildirmezse (`Get-PartitionSupportedSize`
  basarisiz) saf Python NTFS sinirlarina doner — "en kucuk 1 sektor" asla
  kullaniciya/kuyruga ulasmaz.
* `resize_partition`: yerel arac calistirilamazsa veya **tasima** istenirse
  saf Python yolu (ADR 0030) kullanilir; Windows'un araci tasiyamaz.
* `apply_resize` "native" plani **reddeder** — son savunma hatti.
* Yerel arac calismadan once bu surecin birim kilitleri birakilir
  (`PhysicalDisk.release_volumes`): kilitli birimde `Resize-Partition` ve
  `Get-PartitionSupportedSize` calismaz. Kilit sonraki ham yazmada yeniden
  alinir (madde 3).

VM'de olculdu: NTFS buyut/kucult/tasi/geri buyut + degistir/klon/yedek/
donustur/bos alan/kayip bolum — hepsi tamam, dosya ozetleri ayni; boyutlar
Windows'un araciyla (145 -> 181 -> 148 -> 185 MiB), tasima saf Python ile.
Test: t83 (sahte fiziksel oturum; numara yok / sinir yok / arac yok / tasima).

## 2. exFAT buyutme Windows'un bicimlendirdigi birimde veriyi eziyordu

Windows kume yiginini FAT'in hemen ardina degil hizali bir sinira koyar
(48 MB: FAT 128+96, yigin 256). `exfat_resize` buyutmede yerlesimi yeniden
hesapliyor, yigini FAT'in ardina (or. 240) koyuyordu: `kaydirma` **negatif**.
`_shift_forward` sondan basa kopyalar; geri kaydirmada her 4 MiB'lik yazma bir
sonraki parcanin henuz okunmamis kaynaginin son |kaydirma| sektorunu ezer.
Bizim bicimlendiricimizde bosluk yok — goruntu testleri bunu hic gormedi.

Duzeltme: yigin **asla geri cekilmez** (`max(yeni, eski)`); buyuyen FAT
bosluga sigiyorsa yigin yerinde kalir. `_shift_forward` negatif kaydirmayi
reddeder. Test: t82, Windows 10'un diskpart ile bicimlendirdigi fikstur
(`tests/fixtures/exfat_windows.img.gz`) + 12 MiB bizim dosya: 56 MB'a buyutme
(eski kod: `/Klasör/kucuk.bin` bozuk), 200 MB (yigin ileri kayar), kucultme;
her adimda ozetler ve `fsck.exfat -n`.

## 3. Disk acildiktan sonra baglanan birim kilitlenmiyordu

Kilit disk yazilabilir acilirken alinir (ADR 0075). Bolum olusturulduktan
sonra Windows yeni birimi kendiliginden baglar (ya da yerel bicimlendirici
baglar); bu birim kilitsizdi, ham yazma yarida "Yazma reddedildi" ile
kesiliyordu. `PhysicalDisk._win_write` ERROR_ACCESS_DENIED'da bir kez yeni
birimleri kilitleyip yeniden dener; kilitli aygitlar izlenir, ayni birim
ikinci kez acilmaz. Gunlukte "sonradan baglanan birimler kilitleniyor".

## 4. NTFS $UpCase:$Info eksikti (Windows chkdsk)

Windows 8+ `$UpCase` kaydinda 32 baytlik `$Info` adli veri akisi bekler
(boyut, CRC64, surum). Yoksa chkdsk "bad on-disk uppercase table" der ve
cikis kodu 3 verir. CRC64: yansitilmis, polinom 0x9A6C9329AC4BC9B5, baslangic
ve son XOR tum birler; Windows fiksturuyle bayt bayt ayni. VM: eski
bicimlendirici cikis 3, yeni cikis 0. **Eski surumle bicimlendirilmis NTFS
birimleri Windows'ta bir kez `chkdsk /F` ister** (veri kaybi yok).

## Test tarafi

* chkdsk ciktisi OEM kod sayfasinda; `text=True` (ANSI) tanimsiz baytta
  `UnicodeDecodeError` veriyordu -> `encoding="oem"`, tum cagrilarda
  `errors="replace"`.
* Dosya adi bulunamazsa NFC/NFD esdegeri denenir ve "ad bicimi farki" diye
  ayri raporlanir (Linux hfsplus, macOS UDF bulgularini ayirmak icin).
* Imaj islerinde `linux-modules-extra` (hfsplus modulu yoktu, denetim
  sessizce atlaniyordu).
* Adim notlari eski boyutu `_resize` oncesi alir ("181 -> 181" duzeldi);
  aygit kipinde klon boyutu aygittan.
* `DU_TEST_VM=1`: aygit kipi CI disinda yalnizca test VM'inde acilir.

## Kalan

* Windows'ta saf Python NTFS boyutlandirmasi (tasima) VM'de dosya ozetleriyle
  dogrulandi; Windows surucusunun chkdsk'i bu adimdan sonra CI'da
  (aygit isi) olculecek.
* Yayinlanmis 0.5.0-beta ikilileri bu duzeltmelerden once — yeni surum
  gerekir (madde 1 veri kaybi).
