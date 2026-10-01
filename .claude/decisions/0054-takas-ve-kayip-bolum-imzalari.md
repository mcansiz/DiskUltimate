# 0054 — Takas bicimlendirme, kayip bolum imzalari, geri eklemede veri koruma

Tarih: 2026-09-29
Durum: **uygulandi** — Linux ve Windows (VirtualBox win10) dogrulandi,
macOS'ta kosulmadi.
Ilgili: [0053](0053-sifreli-ve-kapsayici-birim-tanima.md), analiz Asama 1.

## 1. Linux takas bicimlendirme (`core/swap.py`)

Takas "dosya sistemi" tek baslik sayfasidir. Saf Python yazici, `mkswap -L
-U` ile ayni UUID/etiketle **bayt bayt ayni** ilk sayfayi uretir (olculdu).
`FS_KINDS`e `swap` eklendi: MBR 0x82, GPT takas GUID'i. Ilk 1024 bayt
(bootbits) korunur. Sayfa boyutu 4096 (x86/arm64 cogunlugu).

## 2. Kayip bolum taramasi: yeni imzalar

Yoklamaya tek sektor yerine adaydan itibaren 68 KiB pencere verilir; btrfs
ustblogu 64 KiB'dedir, ext/HFS+/F2FS 1 KiB'de. Boyutu ustbloktan kesin
okunabilen turler eklendi: ext2/3/4, XFS, btrfs, HFS+/HFSX, APFS, F2FS,
Linux takas, ReFS. **Boyutu bilinemeyenler (LUKS, UDF, LVM) bilerek
aranmaz**: tabloya tahmini boyutla eklenen bolum yanlis bolumdur.

Yinelenme korumasi: bulunmus bir bolumun icine dusen aday atlanir. Derin
taramada (64 KiB adim) ext/XFS/APFS/btrfs yedek ustbloklari aksi halde ayri
"kayip bolum" gibi gorunurdu. ext'te ek olarak `s_block_group_nr == 0`,
btrfs'te `bytenr == 64 KiB` (yansi degil) denetlenir.

Etiket: NTFS ve HFS+ etiketi ustveri dosyasindadir; bulunan her aday icin
`fsdetect.detect` bir kez calistirilir.

## 3. Bulunan hata: geri ekleme kurtarilan dosya sistemini siliyordu

`session.create_partition` fs belirtilmeyen yeni bolumun **ilk ve son 2
MB'ini** siler (eski imza "hayalet" gorunmesin diye — yeni bolum icin
dogru). Kayip bolumu tabloya geri eklemek de bu yoldan gidiyordu: hem
`adopt_lost_partition` hem arayuzun kuyruga koydugu `create_op`. Sonuc:
kurtarilan FAT/NTFS'in onyukleme sektoru (NTFS'te sondaki yedegi de),
ext'in ustblogu ve GDT'si tabloya eklenirken yok ediliyordu. Eslik
belgesinde "Bulunan bolumu tabloya ekleme ✅" yaziyordu; test yalnizca
taramayi sinadigi icin fark edilmemisti.

Ayrica bolum turu dosya sistemine gore secilmiyordu: kurtarilan NTFS
MBR'de 0x83 (Linux) turuyle ekleniyor, Windows onu baglamiyordu.

Duzeltme: `create_partition(wipe=False)`; kuyruk adimi `create_op(...,
keep_data=True, found_fs=<tur>)` — baslik "Kayip bolumu geri ekle", ayrinti
"veri korunur", tur `convert.FS_TO_MBR/FS_TO_GPT`den (yeni turler eklendi).
Plan onizlemesi bolumu bulunan dosya sistemiyle gosterir.

## Dogrulama

* Bos tablolu disk goruntusune gercek araclarla uretilmis 7 dosya sistemi
  (ext4, ext2 1K, btrfs, XFS, HFS+, F2FS, takas) yerlestirildi: hizli ve
  derin tarama yedisini de dogru baslangic/boyut/etiketle buldu, yineleme yok.
* `run_all` t50 (takas; GPT/MBR turu; mkswap ile bayt karsilastirmasi) ve
  t51 (ext4 + takas + NTFS: tabloyu sil → tara (hizli+derin) → iki yoldan
  geri ekle → tur 0x83/0x82/0x07, dosya sistemi ve etiketler okunuyor).
* Linux 49/51 (2 Windows'a ozgu atlandi), Windows 49/51 (t48/t50'nin harici
  arac karsilastirmasi atlandi; islemler calisti).
