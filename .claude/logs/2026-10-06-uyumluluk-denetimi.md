# 2026-10-06 — Yabancı girdi uyumluluk denetimi (ADR 0094)

Tetikleyen: PicoZed `picozed.img` — `mkdosfs -F 32 -s 8` ile üretilmiş, 25 549
kümeli FAT32, bizde FAT16 görünüyordu (ADR 0091). Kök neden: testler birimleri
yalnızca kendi biçimlendiricimizle üretiyordu; okuyucu ile yazıcı aynı
varsayımı paylaştığı için yanlış varsayım testten geçti.

Bu not dört alanın salt okunur denetimini (alt ajanlar; gerçek mkfs/mkntfs/
sfdisk, fsck.*, ntfs-3g ile kısmen yeniden üretildi) ve `tests/yabanci`
matrisinin ilk bulgularını toplar. Durum sütunu düzeltme ilerledikçe güncellenir.

Sınıf: **(c)** yazma/boyutlandırma/yedek birimi bozar · **(b)** yanlış veri
okunur · **(a)** yalnızca görüntü/ret.

## ext2/3/4

Yazıcı bir izin listesi değil **yasak listesi** kullanıyor
(`extwrite.write_support`: yalnızca bigalloc, quota/project, inline_data,
>2^32 blok 64bit reddediliyor). casefold, encrypt, ea_inode, verity,
stable_inodes, needs_recovery, mmp, s_state=ERROR → yazılabilir (DOĞRULANDI).

| # | Sınıf | Bulgu | Durum |
|---|---|---|---|
| E1 | c | Günlüğü işlenmemiş (needs_recovery) birime yazma: kernel günlüğü oynatınca yazdıklarımız geri alınır | **düzeltildi** (yazıcı reddeder) |
| E2 | c | `remove()` bağ sayısına bakmıyor: sabit bağlı dosyada inode ve veri serbest kalır (DOĞRULANDI); üzerine yazma links=1, uid/gid=0, 0644 dayatıyor | **düzeltildi** |
| E3 | c | Aygıt/fifo/soket ve hızlı symlink inode'larının i_block'u blok işaretçisi sanılıp serbest bırakılıyor (DOĞRULANDI: c 1:3 → blok 259) | **düzeltildi** |
| E4 | c | Silmede veri dizin girişinden önce serbest; UTF-8 olmayan adda giriş bulunamayınca bloklar boşta kalıyor (DOĞRULANDI) | **düzeltildi** |
| E5 | c | casefold dizinleri: htree karması katlanmamış adla; aynı ad farklı harfle eklenebiliyor | **ret kapısı** (casefold dizinine ad eklenmez) |
| E6 | c | stable_inodes'lu birimde küçültme inode numarasını değiştiriyor (resize2fs reddeder) | **düzeltildi** (küçültme reddedilir) |
| E7 | c | xattr bloğu / EA inode silme ve üzerine yazmada sızıyor | **düzeltildi** (xattr bloğu); ea_inode birimi **ret kapısı** |
| E8 | c | usedmap ve yazıcı s_state (temiz kapatılmamış) denetlemiyor | **düzeltildi** |
| E9 | c | MMP ve journal_dev yazıcıda reddedilmiyor; journal_dev "ext2" görünüyor | **düzeltildi** ("jbd" olarak görünür) |
| E10 | c | rename tür baytı FT_REG; dir_nlink; dizin delikleri | **düzeltildi** |
| E11 | b | Dolaylı blok delikleri sonraki verileri kaydırıyor (DOĞRULANDI ext3) | **düzeltildi** |
| E12 | b | Yazılmamış (unwritten) extent eski disk içeriği döndürüyor (DOĞRULANDI) | **düzeltildi** |
| E13 | b | Hızlı symlink sınırı `<= 60`, doğrusu `< 60` (DOĞRULANDI) | **düzeltildi** |
| E14 | b | inline_data inode'ları blok haritası sanılıyor (yabancı matris: ext4-inline dosyalar listede yok) | **düzeltildi** (okuma); yazma **ret kapısı** |
| E15 | a | ext4/ext3 ayrımı yalnızca extents/huge_file'a göre | **düzeltildi** |

## FAT / exFAT

| # | Sınıf | Bulgu | Durum |
|---|---|---|---|
| F0 | c | FAT yazıcısı olmayan klasöre yazınca kümeleri ayırıp hata veriyor: 586 küme sızıntısı + FSInfo yanlış (DOĞRULANDI, matris) | **düzeltildi** |
| X1 | c | exFAT NoFatChain dizinleri FAT üzerinden okunuyor/yazılıyor: 127 dosyalık dizinde 42 görünüyor, yazınca 85'e iniyor (DOĞRULANDI). Linux ve Windows böyle dizin üretir | **düzeltildi** (okuma+yazma, gerekirse zincire çevrilir) |
| X2 | c | exFAT NameLength UTF-16 birimi değil kod noktası (DOĞRULANDI: emoji ad kesiliyor; okumada sonda NUL) | **düzeltildi** |
| X3 | c | exFAT ValidDataLength yok sayılıyor; rename VDL=DataLength yazıp çöpü içerik yapıyor | **düzeltildi** |
| X4 | c | exFAT iki FAT / TexFAT / ActiveFat yok sayılıyor | **ret kapısı** (doğru bitmap okunur) |
| F5 | c | FAT12/16'da dizin girişinin üst küme sözcüğü (OS/2 EA) kullanılıyor; rename içeriği siliyor | **düzeltildi** |
| F6 | c | FAT32 ExtFlags (aynalama kapalı, etkin FAT) yok sayılıyor | **düzeltildi** |
| F7 | c | Kayıp bölüm taramasında sektör birimi karışıyor (bps 4096 / exFAT shift) | **düzeltildi** |
| F8 | a | FAT etiketi: kök dizin etiket girişi okunmuyor/yazılmıyor ("NO NAME"); yedek önyükleme sabit 6 | **düzeltildi** |
| F9 | a | Kısa adlar latin-1 (CP437 olmalı) | **düzeltildi** |
| X10 | a | exFAT ad karşılaştırması upcase tablosu yerine str.lower | **düzeltildi** |
| X11 | b | exFAT silinmiş dosya kurtarma hep bitişik varsayıyor | **düzeltildi** |

## NTFS

| # | Sınıf | Bulgu | Durum |
|---|---|---|---|
| N1 | c | Düzeltme (fixup) adımı sektör boyutu; Windows/ntfs-3g hep 512 (DOĞRULANDI: mkntfs -s 4096 + bizim yazma → ntfs-3g bağlamıyor) | **düzeltildi** (okuma da düzeldi) |
| N2 | c | Hazırda bekleme / temiz olmayan $LogFile için yazma kapısı yok; resize $LogFile'ı siliyor; "bilinmiyor" temiz sayılıyor (Hızlı Başlangıç!) | **düzeltildi** |
| N3 | c | $ATTRIBUTE_LIST'li $MFT: uzantı kayıtları kayboluyor; küçültme veriyi kesiyor | **düzeltildi** (okuma); yazma/boyut **ret kapısı** |
| N4 | c/b | Çok uzantılı öznitelik yalnızca ilk uzantı okunuyor; silmede sızıntı | **düzeltildi** (okuma); yazma **ret kapısı** |
| N5 | c | $MFTMirr yalnızca 0-3 senkron (DOĞRULANDI: -c 131072 sonrası ntfs-3g reddediyor) | **düzeltildi** |
| N6 | c | INDX bloğu tek bitişik koşu varsayımıyla yazılıyor (küme < 4K) | **düzeltildi** |
| N7 | b | EFS şifreli dosya şifreli metin olarak dışa aktarılıyor | **ret kapısı** (EFS okuma); silme sızıntıları **düzeltildi** |

## Bölüm tabloları

| # | Sınıf | Bulgu | Durum |
|---|---|---|---|
| P1 | c | 4Kn diskte GPT→MBR tabloyu siliyor (MBR 512 bayt yazılıyor) | **düzeltildi** |
| P2 | c | Bayat GPT geçerli MBR'ye üstün geliyor (koruyucu 0xEE ve CRC denetimi yok) (DOĞRULANDI); usedmap canlı veriyi atlıyor | **düzeltildi** |
| P3 | c | GPT CRC sonuçları kullanılmıyor; bozuk birincil iyi yedeğin üstüne yazılıyor | **düzeltildi** |
| P4 | c | Her tablo yazımında yuvalar yeniden numaralanıyor (fstab/GRUB kırılır) (DOĞRULANDI) | **düzeltildi** |
| P5 | c | Hibrit MBR her GPT yazımında siliniyor | **korunur** + **ret kapısı** |
| P6 | c | FirstUsableLBA 2048'e zorlanıyor (34/40'taki bölüm dışarıda kalıyor) (DOĞRULANDI) | **düzeltildi** |
| P7 | c | GPT yazımı yedek konumunu son bölüme karşı denetlemiyor | **düzeltildi** |
| P8 | a | Görüntüler hep 512 B sektörle açılıyor (4Kn görüntü) | **düzeltildi** |

## Matris ve sonraki analizde çıkanlar (2026-10-06)

| # | Sınıf | Bulgu | Durum |
|---|---|---|---|
| M1 | c | **picozed.img FAT32'si yapısal olarak bozuk** (her iki FAT'in ilk sektöründe eski önyükleme sektörü baytları; kök zincir 29542 > azami küme). fsck.fat ve 7-Zip reddediyor, biz sessizce "sağlam" gösteriyorduk. Bozulma dosyalardan önce var (zincirler çöp girdileri atlıyor; görüntü tarihi depodan eski) — DiskUltimate kaynaklı görünmüyor | **tutarlılık kapısı**: okunur, yazma/boyut reddedilir, kullanılan alan haritası yok (tam yedek), arayüzde "Yapı tutarsız" |
| M2 | c | FAT/exFAT boyutlandırma BPB sektörü ≠ aygıt sektörü (-S 2048/4096): birimi bozuyordu (matris) | **düzeltildi** |
| M3 | c | NTFS boyutlandırma/taşıma -s 4096'da birim karışımı (toplam sektör 8 kat) | **düzeltildi** |
| M4 | c | XFS büyütme 1 KiB blok: yeni AG kökleri başlıkların üstüne; 2048/4096 sektörde süperblok CRC'si 512 bayt | **düzeltildi** |
| M5 | b | HFS+ çekirdeğin yazdığı Yunanca (tonos U+030D) adlar listede yok | **düzeltildi** (okuma). Yazıcı macOS biçimini (TN1150, Unicode 3.2) korur; Linux çekirdeği bizim yazdığımız tonos'lu adları bulamaz — bilinçli tercih |
| M6 | c | FAT yazıcısı olmayan klasöre yazınca küme sızdırıyordu (= F0) | **düzeltildi** |
| M7 | c | NTFS: aynı klasördeki sabit bağlardan biri silinince/yeniden adlandırılınca **öteki de kayboluyordu** (kayıt serbest; 10 turluk matris, 5 varyant × 10 tur). Silme dizindeki bütün adları uzun+8.3 çifti sayıyordu | **düzeltildi** (regress_ntfs t19) |
| M8 | c | NTFS 64 KiB / 2 MiB küme: bizim oluşturduğumuz klasörlerin içi Linux ntfs3'te **boş görünüyordu** ($INDEX_ROOT "dizin bloğu başına küme" alanı küme > 4 KiB'ta 512 B biriminde 8 olmalı, 1 yazılıyordu). Kendi biçimlendiricimizin kök/$Secure/$Extend dizinlerinde de vardı. ntfs-3g ve bizim okuyucu alana bakmadığı için fsck geçiyordu | **düzeltildi** (regress_ntfs t20, t21) |

Doğrulama: `tests/regress_{fat,ext,ntfs,ptable,xfs_hfs}.py` (82 test; eski kodda başarısız, yeni kodda geçer), yabancı matris. Windows (GitHub, uzun testler 37501631074): kendi biçimlendirdiğimiz NTFS birimlerinde yeni yazıcı Mount-DiskImage + chkdsk ile temiz (4 KiB küme, 512 B sektör). **Windows'ta henüz doğrulanmadı:** NTFS 4K sektör fixup'ı chkdsk ile, 64K+ küme ayna, Hızlı Başlangıç kapısı gerçek birimde.

Ayrıntılı alt ajan raporları bu oturumun dökümündedir
(`.claude/sessions/live/4bbcf40c-...jsonl`).
