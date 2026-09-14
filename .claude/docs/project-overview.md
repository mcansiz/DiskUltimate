# DiskUltimate — Proje Genel Bakis

**Amac:** `.img` ham disk goruntu dosyalari uzerinde, DiskGenius benzeri gorsel bir
arayuzle disk olusturma, bolumleme, bicimlendirme ve dosya erisimi saglamak.

**Durum:** v0.2.0 — capraz platform (Windows/Linux/macOS), 15/15 cekirdek testi basarili.
DiskGenius ozellik karsilastirmasi: [diskgenius-parity.md](diskgenius-parity.md)

## Neden bu proje
Gercek diske dokunmadan, root yetkisi olmadan, tam kontrollu bir disk yapisi
kurabilmek: onyuklenebilir USB imajlari hazirlamak, gomulu sistem kartlari icin
goruntu uretmek, bolum/dosya sistemi yapilarini ogrenmek ve incelemek.

## Temel ilkeler
1. **Yalnizca goruntu dosyasi.** Hicbir kod `/dev/sd*` benzeri blok aygita yazmaz.
2. **root gerekmez.** Loop aygiti, `mount`, `sudo` kullanilmaz.
3. **Cekirdek saf Python.** Bolum tablolari ve FAT surucusu disaridan bagimsiz;
   `mkfs.*` araclari yalnizca ek dosya sistemleri icin, varsa kullanilir.
4. **Katman ayrimi.** `core/` GUI bilmez, `ui/` disk bicimini bilmez.

## Surum kapsamlari

### v0.1.0 — tamamlandi
- [x] Seyrek (sparse) `.img` olusturma, acma, yeniden boyutlandirma
- [x] MBR bolum tablosu: 4 birincil + genisletilmis + EBR zinciri (mantiksal bolumler)
- [x] GPT bolum tablosu: birincil + yedek baslik, CRC32, koruyucu MBR, 128 giris
- [x] FAT12 / FAT16 / FAT32 bicimlendirme (saf Python, `fsck.vfat` ile dogrulandi)
- [x] FAT icerigine tam erisim: listeleme, okuma, yazma, LFN, klasor, silme, yeniden adlandirma
- [x] exFAT / NTFS / ext2-3-4 bicimlendirme (`mkfs.*` araclari, gecici birim uzerinden)
- [x] Dosya sistemi tespiti: FAT, exFAT, NTFS, ext2/3/4, btrfs, XFS, F2FS, ISO9660, takas
- [x] DiskGenius tarzi arayuz: gorsel bolum haritasi, bolum tablosu, dosya gezgini,
      onaltilik goruntuleyici, islem gunlugu
- [x] Uzun islemler icin ayri is parcacigi + ilerleme penceresi
- [x] Yikici islemlerde onay diyaloglari, basarisiz bicimlendirmede geri alma

### v0.2.0 — tamamlandi
- [x] **Capraz platform**: Windows / Linux / macOS (bkz. [cross-platform.md](cross-platform.md))
- [x] **exFAT saf Python** — bicimlendirme + tam okuma/yazma, `fsck.exfat` ile dogrulandi
- [x] **MBR ↔ GPT donusumu** (veri yerinde kalir, uygunluk on denetimi)
- [x] **Yedekleme / geri yukleme** — `.dub` bicimi, sikistirmali, sifir bloklari atlar
- [x] **Disk ve bolum klonlama** (seyreklik korunarak)
- [x] **Guvenli silme** — sifir / rastgele / DoD 3 / DoD 7 + bos alan silme
- [x] **Silinmis dosya kurtarma** — FAT ve exFAT, uzun adlar dahil
- [x] **Kayip bolum tarama** — imza tabanli, tabloya geri ekleme
- [x] **Imza tabanli dosya kurtarma (carving)** — 13 dosya turu
- [x] **Sanal disk destegi** — VHD/VDI/VMDK/QCOW2 okuma, VHD olusturma
- [x] **4K hizalama denetimi**
- [x] FAT yazma basarimi duzeltmesi (>100 s → 0.04 s)

### v0.3 — planlanan
- [ ] Bolum boyutlandirma ve tasima (FAT/exFAT icin veri koruyarak)
- [ ] Bolum bolme / birlestirme
- [ ] ext2/3/4 **okuyucu** (icerik listeleme ve disa aktarma)
- [ ] Onaltilik duzenleyici (yazma destegi)
- [ ] Dosya sistemi tutarlilik denetimi (chkdsk/fsck esdegeri)
- [ ] Birincil ↔ mantiksal bolum donusumu, bolum gizleme

### v0.4 ve sonrasi — fikirler
- [ ] NTFS okuyucu (MFT cozumleme)
- [ ] Sanal disk bicimleri arasinda dogrudan donusturucu
- [ ] Onyukleme sektoru sablonlari (syslinux / GRUB yerlestirme)
- [ ] Bicimlendirilmis bolumde dizin yapisini yeniden kurma

## Ilgili belgeler
- Ozellik matrisi (DiskGenius): [diskgenius-parity.md](diskgenius-parity.md)
- Pazar/kaynak analizi: [feature-analysis.md](feature-analysis.md)
- Capraz platform: [cross-platform.md](cross-platform.md)
- Mimari: [architecture.md](architecture.md)
- Is gunlugu: [worklog.md](worklog.md)
- Test: [testing.md](testing.md)
- Kararlar: [../decisions/](../decisions/)
- Bicim spesifikasyonlari: [../specs/](../specs/)
