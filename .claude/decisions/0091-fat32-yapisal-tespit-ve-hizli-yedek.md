# 0091 — FAT32 BPB yapısından tanınır; yedekleme çok çekirdekli sıkıştırır

Tarih: 2026-10-05
Durum: **uygulandı** (1, 2); 3 → [0092](0092-yalnizca-kullanilan-alan-yedegi.md) (kullanıcı: "evet yapalım").
İlgili: [0087](0087-exfat-buyutmede-bitmap-zinciri.md) (t88, FAT32 yedek önyükleme).

## 1. Küçük FAT32 bölümü FAT16 görünüyordu

Kullanıcı bildirdi: `picozed.img` (PicoZed/Xilinx SD kartı) BOOT bölümü
DiskGenius'ta FAT32, bizde **FAT16** ve etiket `□□□`.

Ölçüm (salt okunur): 100 MB bölüm, `mkdosfs`, küme 8 sektör, BPB_FATSz16 = 0,
BPB_RootEntCnt = 0, BPB_FATSz32 = 200, BS_FilSysType "FAT32", etiket "BOOT".
Veri kümesi sayısı **25 549** (< 65 525).

Kök neden: `fsdetect._fat` ve `fat.FatFS` türü **yalnızca küme sayısından**
seçiyordu (Microsoft fatgen103 kuralı). Linux çekirdeği (`fat_length == 0`
→ FAT32) ve Windows fastfat (`IsBpbFat32`: BPB_FATSz16 == 0) türü **BPB
yapısından** belirler; `mkdosfs -F 32` küçük bölümde az kümeli FAT32 üretir
ve Windows onu FAT32 bağlar (görüntüde Windows'un yazdığı
`System Volume Information` var). Etiketteki çöp: FAT16 sanılınca etiket
0x2B'den okunuyordu — FAT32'de orası BPB_FATSz32/ExtFlags/RootClus alanları.

Görüntüden daha ağır sonuç: `FatFS` bu birime dosya ekleseydi/silseydi 32
bitlik FAT tablosuna 16 bitlik girdi yazar, kök dizini sabit kök bölgesi
sanıp yanlış yere yazardı — birim bozulurdu.

Karar: BPB_FATSz16 == 0 → FAT32; değilse küme sayısı FAT12/FAT16'yı ayırır
(Linux ile aynı). `resize`: az kümeli FAT32'nin alt sınırı `min(65525,
mevcut küme sayısı)` — birim zaten öyle, boyutlandırma onu daha aşağı itmez.
Test: t91 (eski kodla `(16, 25199)` ile başarısız, yeni kodla fsck.vfat
dahil geçer).

## 2. Yedekleme yavaşlığı

Kullanıcı: "DiskGenius'a göre çok yavaş." picozed.img (512 MB) profili:

| | süre |
|---|---|
| zlib.compress (tek çekirdek, ~40 MB/s) | 2.17 s |
| `block.strip(b"\x00")` sıfır denetimi (~2.3 ms/MB) | 1.01 s |
| okuma | 0.11 s |
| **toplam** | **3.3 s** |

Düzeltmeler (biçim değişmedi, eski yedekler aynen açılır):
- `image.is_zero()`: hazır sıfır tamponuyla `==` (memcmp) — 1 MB'ta 0.04 ms,
  ~58 kat. Yedek, geri yükleme, klonlama, plan geri yükleme, silme
  doğrulaması ve biçimlendirme döngülerinde kullanılır. (`memoryview ==`
  denendi: 1.5 ms/MB, bayt bayt karşılaştırıyor — kullanılmadı.)
- Sıkıştırma `ThreadPoolExecutor` ile (zlib GIL'i bırakır), en çok 8 iş
  parçacığı; okuma ana döngüde sıralı (aygıta tek yerden dokunulur), yazma
  sıralı — çıktı tek iş parçacıklı sürümle **bayt bayt aynı** (t92).
  Bellek sınırı: iş parçacığı başına iki blok. `DISKULTIMATE_BACKUP_THREADS`.
- `diagnostics.span("backup")` — yedekleme artık tanılama günlüğünde ölçülür.

Sonuç (16 çekirdek): 3.3 s → **0.71 s** (düzey 6), 0.46 s (düzey 1).

## 3. Açık: yalnızca kullanılan alanı yedekleme

DiskGenius'un bölüm yedeğinde asıl farkı: dosya sisteminin **dolu
kümelerini** okur; boş alan ve bölümlenmemiş alan okunmaz. Biz hâlâ her
sektörü okuyoruz. Görüntü dosyasında fark küçük (okuma 0.11 s), ama fiziksel
SD kart/USB'de (20–90 MB/s) 16 GB'lık bir kartın tamamını okumak dakikalar
sürer; DiskGenius aynı kartta yalnızca ~100 MB okur. Boş alanda eski veri
varsa yedek de büyür. Bu bir biçim/davranış kararıdır (geri yüklemede
kullanılmayan bloklar sıfır mı yazılır, dokunulmaz mı; "tüm sektörler"
seçeneği), kullanıcıya soruldu.
