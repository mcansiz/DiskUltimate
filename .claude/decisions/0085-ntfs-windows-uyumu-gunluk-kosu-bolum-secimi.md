# 0085 — NTFS Windows uyumu: temiz $LogFile, isaretli kosu uzunlugu, bolumun ofsetle secilmesi

Tarih: 2026-10-04
Durum: **uygulandi**. Ilgili: [0084](0084-windows-aygit-bulgulari.md),
[0080](0080-uzun-testler-plani.md), [0030](0030-ntfs-saf-python-boyutlandirma.md),
[0058](0058-ntfs-b-agaci-guvenlik-ve-windows-uyumu.md).

CI uzun test kosusu 37199361406 (quick, tohum 7, 3 platform): Linux ve macOS
islerinin tamami, aygit islerinin biri disinda tamami gecti. Kalan Windows
hatalari VirtualBox "win10 " misafirinde yeniden uretildi; uc ayri neden cikti.

## 1. 0xFF dolu $LogFile: Windows salt okunur baglamiyordu

Belirti: Windows NTFS isleri, "surucu dosyayi listede de gostermiyor". VM:
salt okunur VHD'de kok dizin "Yazma korumasi var", `Get-Volume` dosya sistemi
bos; chkdsk temiz. Ayni goruntu bir kez okuma-yazma baglaninca salt okunur da
calisiyor. Deney: **yalnizca** Windows'un yazdigi `$LogFile` baytlari bizim
goruntuye kopyalaninca salt okunur baglama calisti — neden gunluk.

0xFF dolu ("bos") gunluk ntfs-3g `mkntfs`'in de yazdigidir; Windows onu ilk
baglamada **yazarak** baslatir. Yazma korumali kart, salt okunur VHD, adli
inceleme ortaminda birim hic baglanmaz.

Karar: bicimlendirici, boyutlandirma ve onarim gunluge Windows'un ilk baglamada
yazdigi iki "RSTR" sayfasini yazar (LFS 1.1, "birim temiz", tek istemci
"NTFS", LSN'ler 0); geri kalani 0xFF. Alanlar Windows 10 fiksturuyle (ve VM'de
Windows'un yazdigiyla) birebir ayni — degisen yalnizca LSN, son kayit boyu,
acilis sayaci. `seq_number_bits = 64 - (boyut.bit_length() - 3)` (2 MiB -> 45,
Windows ile ayni). Coklu sektor korumasi 512 baytta bir (fiziksel sektor
degil). VM: salt okunur bagla, okuma-yazma bagla, yeniden salt okunur; uc
seferde de chkdsk temiz. ntfsfix artik temiz gunluge dokunmaz (`LOG_CLEAN`).
Test: t84.

## 2. Boyutlandirma kosu uzunlugunu isaretsiz kodluyordu

Belirti: `buyut` adiminda Windows birimi bozuk sayiyor; chkdsk "Attribute
record (80, $Bad) from file record segment 8 is corrupt". `ntfsresize.
encode_runs` uzunlugu `unsigned=True` ile yaziyordu: 46335 kume = `02 FF B4`,
Windows ve ntfs-3g icin negatif. ADR 0058'de ayni hata `ntfswrite`'ta
duzeltilmisti (138 kume); bu ikinci kodlayici gozden kacmisti. Etki yalniz
`$BadClus` degil — kucultmede tasinan her dosyanin, `$Bitmap`'in ve MFT'nin
kosu listesi ayni kodlayicidan gecer.

Neden bu kadar gec: **bizim okuyucu da uzunlugu isaretsiz okuyordu**; kendi
dogrulamamiz hatali kodlamayi kabul ediyordu. Okuyucu artik isaretli okur ve
negatif/sifir uzunlugu `NtfsError` sayar (Windows ve ntfs-3g gibi). Bunun
bedeli: bu hatayla boyutlandirilmis bir birim artik bizim aracta da "bozuk"
gorunur — dogru olan budur; Windows onu zaten okuyamiyordu.

VM: 145 -> 181 MiB ve 145 -> 60 -> 181 MiB, salt okunur baglama + chkdsk temiz
(once cikis 3). Test: t85 (eski kodda "uzunluk -128" ile duser).

## 3. Windows yerel araclari bolumu numarayla seciyordu

Belirti: aygit kipi, MBR mantiksal bolum: `Resize-Partition -PartitionNumber 5`
-> "No matching MSFT_Partition objects". Windows MBR mantiksal bolumlerini
kendi sirasiyla numaralar; bizim numaramiz (5, 6, ... Linux ile ayni) ile
uyusmaz. Ayni numara `Format-Volume`'e de gidiyordu: numara baska bir
bolume denk gelseydi **yanlis bolum bicimlendirilirdi**.

Karar: `platform.win_partition_selector(disk, ofset)` — bolum bayt ofsetiyle
secilir, bulunamazsa komut `throw` eder. Bicimlendirme, boyut sinirlari ve
boyutlandirma bunu kullanir. VM: ntfs/exfat x mbr-mantiksal/gpt aygit kipi
4/4 senaryo tamam.

Kalan (cozuldu: ADR 0086): surucu harfi ata/kaldir (`_win_assign_letter`, `_win_remove_letter`,
`partition_mount_point`) hala numarayla calisir. Yikici degil (harf yanlis
bolume atanabilir ya da hata verir); guvenlik uyarilari zaten ofsetli
`mount_map`'ten gelir. Ayri is olarak duruyor.

## Sonuc

VM, Windows surucusu + chkdsk dogrulamali: NTFS goruntu kipi 14/14 adim
(2 bilincli atlama), aygit kipi ntfs/exfat x mbr-mantiksal/gpt tamam.
Yayinlanmis 0.5.0-beta: madde 2 (kucultme/buyutme sonrasi Windows "bozuk")
ve ADR 0084 madde 1 nedeniyle yeni surum gerekir.
