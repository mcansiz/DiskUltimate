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
| E1 | c | Günlüğü işlenmemiş (needs_recovery) birime yazma: kernel günlüğü oynatınca yazdıklarımız geri alınır | açık |
| E2 | c | `remove()` bağ sayısına bakmıyor: sabit bağlı dosyada inode ve veri serbest kalır (DOĞRULANDI); üzerine yazma links=1, uid/gid=0, 0644 dayatıyor | açık |
| E3 | c | Aygıt/fifo/soket ve hızlı symlink inode'larının i_block'u blok işaretçisi sanılıp serbest bırakılıyor (DOĞRULANDI: c 1:3 → blok 259) | açık |
| E4 | c | Silmede veri dizin girişinden önce serbest; UTF-8 olmayan adda giriş bulunamayınca bloklar boşta kalıyor (DOĞRULANDI) | açık |
| E5 | c | casefold dizinleri: htree karması katlanmamış adla; aynı ad farklı harfle eklenebiliyor | açık |
| E6 | c | stable_inodes'lu birimde küçültme inode numarasını değiştiriyor (resize2fs reddeder) | açık |
| E7 | c | xattr bloğu / EA inode silme ve üzerine yazmada sızıyor | açık |
| E8 | c | usedmap ve yazıcı s_state (temiz kapatılmamış) denetlemiyor | açık |
| E9 | c | MMP ve journal_dev yazıcıda reddedilmiyor; journal_dev "ext2" görünüyor | açık |
| E10 | c | rename tür baytı FT_REG; dir_nlink; dizin delikleri | açık |
| E11 | b | Dolaylı blok delikleri sonraki verileri kaydırıyor (DOĞRULANDI ext3) | açık |
| E12 | b | Yazılmamış (unwritten) extent eski disk içeriği döndürüyor (DOĞRULANDI) | açık |
| E13 | b | Hızlı symlink sınırı `<= 60`, doğrusu `< 60` (DOĞRULANDI) | açık |
| E14 | b | inline_data inode'ları blok haritası sanılıyor (yabancı matris: ext4-inline dosyalar listede yok) | açık |
| E15 | a | ext4/ext3 ayrımı yalnızca extents/huge_file'a göre | açık |

## FAT / exFAT

| # | Sınıf | Bulgu | Durum |
|---|---|---|---|
| F0 | c | FAT yazıcısı olmayan klasöre yazınca kümeleri ayırıp hata veriyor: 586 küme sızıntısı + FSInfo yanlış (DOĞRULANDI, matris) | açık |
| X1 | c | exFAT NoFatChain dizinleri FAT üzerinden okunuyor/yazılıyor: 127 dosyalık dizinde 42 görünüyor, yazınca 85'e iniyor (DOĞRULANDI). Linux ve Windows böyle dizin üretir | açık |
| X2 | c | exFAT NameLength UTF-16 birimi değil kod noktası (DOĞRULANDI: emoji ad kesiliyor; okumada sonda NUL) | açık |
| X3 | c | exFAT ValidDataLength yok sayılıyor; rename VDL=DataLength yazıp çöpü içerik yapıyor | açık |
| X4 | c | exFAT iki FAT / TexFAT / ActiveFat yok sayılıyor | açık |
| F5 | c | FAT12/16'da dizin girişinin üst küme sözcüğü (OS/2 EA) kullanılıyor; rename içeriği siliyor | açık |
| F6 | c | FAT32 ExtFlags (aynalama kapalı, etkin FAT) yok sayılıyor | açık |
| F7 | c | Kayıp bölüm taramasında sektör birimi karışıyor (bps 4096 / exFAT shift) | açık |
| F8 | a | FAT etiketi: kök dizin etiket girişi okunmuyor/yazılmıyor ("NO NAME"); yedek önyükleme sabit 6 | açık |
| F9 | a | Kısa adlar latin-1 (CP437 olmalı) | açık |
| X10 | a | exFAT ad karşılaştırması upcase tablosu yerine str.lower | açık |
| X11 | b | exFAT silinmiş dosya kurtarma hep bitişik varsayıyor | açık |

## NTFS

| # | Sınıf | Bulgu | Durum |
|---|---|---|---|
| N1 | c | Düzeltme (fixup) adımı sektör boyutu; Windows/ntfs-3g hep 512 (DOĞRULANDI: mkntfs -s 4096 + bizim yazma → ntfs-3g bağlamıyor) | açık |
| N2 | c | Hazırda bekleme / temiz olmayan $LogFile için yazma kapısı yok; resize $LogFile'ı siliyor; "bilinmiyor" temiz sayılıyor (Hızlı Başlangıç!) | açık |
| N3 | c | $ATTRIBUTE_LIST'li $MFT: uzantı kayıtları kayboluyor; küçültme veriyi kesiyor | açık |
| N4 | c/b | Çok uzantılı öznitelik yalnızca ilk uzantı okunuyor; silmede sızıntı | açık |
| N5 | c | $MFTMirr yalnızca 0-3 senkron (DOĞRULANDI: -c 131072 sonrası ntfs-3g reddediyor) | açık |
| N6 | c | INDX bloğu tek bitişik koşu varsayımıyla yazılıyor (küme < 4K) | açık |
| N7 | b | EFS şifreli dosya şifreli metin olarak dışa aktarılıyor | açık |

## Bölüm tabloları

| # | Sınıf | Bulgu | Durum |
|---|---|---|---|
| P1 | c | 4Kn diskte GPT→MBR tabloyu siliyor (MBR 512 bayt yazılıyor) | açık |
| P2 | c | Bayat GPT geçerli MBR'ye üstün geliyor (koruyucu 0xEE ve CRC denetimi yok) (DOĞRULANDI); usedmap canlı veriyi atlıyor | açık |
| P3 | c | GPT CRC sonuçları kullanılmıyor; bozuk birincil iyi yedeğin üstüne yazılıyor | açık |
| P4 | c | Her tablo yazımında yuvalar yeniden numaralanıyor (fstab/GRUB kırılır) (DOĞRULANDI) | açık |
| P5 | c | Hibrit MBR her GPT yazımında siliniyor | açık |
| P6 | c | FirstUsableLBA 2048'e zorlanıyor (34/40'taki bölüm dışarıda kalıyor) (DOĞRULANDI) | açık |
| P7 | c | GPT yazımı yedek konumunu son bölüme karşı denetlemiyor | açık |
| P8 | a | Görüntüler hep 512 B sektörle açılıyor (4Kn görüntü) | açık |

Ayrıntılı alt ajan raporları bu oturumun dökümündedir
(`.claude/sessions/live/4bbcf40c-...jsonl`).
