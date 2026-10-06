# 0092 — Yalnızca kullanılan alanı yedekleme (DiskGenius gibi)

Tarih: 2026-10-05
Durum: **uygulandı**. İlgili: [0091](0091-fat32-yapisal-tespit-ve-hizli-yedek.md),
[0045](0045-geri-yuklemede-bolum-yerlesimi.md), [0032](0032-yedekleme-tek-pencere-ve-not.md).

## Bağlam

Kullanıcı (başka bir PC'de): 64 GB SD kartın yedeği DiskGenius'ta çok hızlı,
bizde çok yavaş. ADR 0091 sıkıştırmayı paralelleştirdi, ama asıl fark okuma:
biz kartın 64 GB'ının tamamını okuyorduk, DiskGenius yalnızca dosya
sistemlerinin dolu kümelerini okur. SD kart 20–90 MB/s okur: 64 GB ≈ 12–50
dakika; 500 MB veri ≈ 6–25 saniye. Kullanıcı kararı: "evet yapalım".

## Karar

`core/usedmap.py` aygıt için okunacak bayt aralıklarını çıkarır;
`clone.backup(used_ranges=...)` diğer blokları **okumadan** `BLOCK_SKIP` (3)
işaretler. Yedek penceresinde "Yalnızca kullanılan alanı yedekle (hızlı)"
**varsayılan açık**; kapatılırsa eski davranış (tüm sektörler).

Dosya sistemi haritaları:
| FS | Kaynak | Her zaman |
|---|---|---|
| FAT12/16/32 | FAT tablosu (boş olmayan girdi = dolu) | ayrılmış alan, FAT'ler, kök dizin |
| exFAT | ayırma bitmap'i | küme yığınından önceki her şey |
| ext2/3/4 | grup blok bitmap'leri; BLOCK_UNINIT grupta yalnızca metaveri | üstblok, GDT, ayrılmış GDT, bitmapler, inode tabloları |
| NTFS | `$Bitmap` | — |

Disk düzeyi: ilk bölümden önceki alan tamamen (ham U-Boot/SPL burada
durur), bölümler arası ≤ 16 MiB boşluk tamamen, EBR sektörleri, her bölümün
ve diskin ilk/son 1 MiB'ı (NTFS yedek önyükleme sektörü, GPT yedeği).
16 MiB'tan büyük bölümlenmemiş alan **alınmaz**.

Bitmap ~1 MiB'lık gruplarla taranır (`is_zero` dilimi); grupta tek dolu
birim varsa grup alınır. 64 GB'ta ~65 bin tur; yedek zaten 1 MiB blokla
çalıştığı için daha ince harita kazandırmaz.

## İhtiyat kuralları ("bilinmiyor" asla "boş" değildir)

- Tanınmayan FS (XFS, btrfs, HFS+, F2FS, takas, LUKS, BitLocker...) →
  bölüm tamamen.
- Harita çıkarılamazsa (okuma hatası, tutarsız yapı) → bölüm tamamen;
  `diagnostics.warn` ile günlüğe.
- ext: `needs_recovery` (işlenmemiş günlük) → tamamen: diskteki bitmap'e
  yansımamış ayırmalar olabilir, atlanırsa günlük oynatılınca dosya çöp
  gösterir. `bigalloc` → tamamen (bit küme anlatır).
- BLOCK_UNINIT bayrağına yalnızca sağlama toplamlı birimde (gdt_csum /
  metadata_csum) güvenilir — çekirdek de öyle yapar.
- FAT32 girdisinin ayrılmış üst 4 biti de "dolu" sayılır.
- Bölüm tablosu okunamazsa → disk tamamen.

## Geri yükleme

Atlanan bloğa hedefte **dokunulmaz** (sıfır da yazılmaz). `DubImage`
atlanan alanı sıfır okur; `restoreplan._copy_region` `is_skipped()` ile aynı
kuralı uygular (yalnızca küçültülen FS katmanı `_Overlay` hariç — o alan
boyutlandırmada yazılmış olabilir).

Biçim: atlanan blok içeren yedek **sürüm 2**. Eski sürüm bilinmeyen türü
ham blok gibi işleyip `length=0` yazardı (sessiz bozulma); sürüm 2'yi
"desteklenmiyor" diye reddeder. Atlanan blok içermeyen yedek sürüm 1
kalır, eski sürümlerde açılır. Başlık bayrağı bit 1 = kapsam; yedek
bilgisinde "Kapsam" satırı.

Bedel: silinmiş dosya kurtarma / imza taraması yedekte atlanan alanı
göremez — ipucu metni "tüm sektörler"i önerir.

## Ölçüm (Linux ana makine, görüntü dosyası)

- picozed.img (512 MB, FAT32 + ext4): okunan 105 / 512 MB.
- 64 GB seyrek görüntü, FAT32 1 GB + exFAT 60 GB, 300 MB veri: harita
  0,19 s, okunan 310 MB; yedek 27,4 s → 1,7 s. (tmpfs: okuma RAM hızında.
  Gerçek SD kartta fark okuma hızıyla orantılı büyür — örn. 40 MB/s'te
  ~27 dk → ~8 s; **tahmin**, fiziksel kartta ölçülmedi.)
- t93: FAT32/exFAT/ext4/NTFS dört bölüm, geri yüklenen her bölüm fsck.vfat,
  fsck.exfat, e2fsck -fn, ntfsfix -n ile temiz; dosyalar özdeş; ham
  önyükleyici korunur; atlanan alandaki hedef verisi değişmez; EBR'ler
  büyük boşluğa rağmen alınır.
- picozed.img geri yükleme: dolu kümelerde fark 0, BOOT.BIN/image.ub ve
  ext4 `/etc/passwd` özdeş, e2fsck temiz.

Fiziksel kartta (Windows/VM) ölçülmedi — CLAUDE.md test kuralı gereği bu
ayrıca yapılmalı.
