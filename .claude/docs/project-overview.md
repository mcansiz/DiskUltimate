# DiskUltimate — Proje Genel Bakis

**Amac:** Disk goruntuleri (`.img`, VHD/VDI/VMDK/QCOW2) ve **sistemdeki gercek
diskler** uzerinde, DiskGenius benzeri gorsel bir arayuzle bolumleme,
bicimlendirme, yedekleme, kurtarma ve dosya erisimi saglamak.

**Durum:** v0.4.0 — **Windows ve Linux** (ikisinde de kosuldu), **18/18** cekirdek
testi basarili; sekiz dosya sisteminde de okuma/yazma. DiskGenius ozellik karsilastirmasi: [diskgenius-parity.md](diskgenius-parity.md)

> Surumun tek kaynagi koddaki `ui/main_window.py::APP_VERSION`. Bu satir
> degistiginde buradaki "Durum" ve README rozeti de guncellenir.

## Neden bu proje
Disk yapisi uzerinde tam kontrol: onyuklenebilir USB imajlari hazirlamak, gomulu
sistem kartlari icin goruntu uretmek, bolum ve dosya sistemi yapilarini ogrenmek,
silinmis veriyi kurtarmak. Goruntu dosyalariyla calisirken hicbir yetki
gerekmez; gercek disklere erisim ise acikca istenmesi gereken, katmanli onaydan
gecen ayri bir moddur.

## Temel ilkeler

1. **Goruntu dosyasi varsayilandir, fiziksel disk istege baglidir.**
   Uygulama goruntu dosyalariyla yonetici/root yetkisi olmadan tam islevlidir.
   Fiziksel disk erisimi v0.3 ile eklendi ([ADR 0014](../decisions/0014-fiziksel-disk-destegi.md))
   ve alti katmanli bir kapidan gecer: listeleme zararsizdir → varsayilan salt
   okunur → yazma icin `confirm=True` → sistem diski icin `allow_system=True` →
   bilgisi eksik diskte yazma **reddedilir** → bagli bolumde ayrica uyarilir.
2. **Yikici hicbir islem sessizce olmaz.** Bicimlendirme, silme, boyutlandirma ve
   tablo yazma onay ister; basarisiz bicimlendirme tabloyu geri alir.
3. **Cekirdek saf Python.** Bolum tablolari ve sekiz dosya sisteminin
   bicimlendirmesi disaridan bagimsizdir; harici `mkfs.*` araclari yalnizca
   varsa ve oncelikliyse kullanilir.
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

### v0.3.0 — tamamlandi
- [x] **Fiziksel disk destegi** — sistemdeki diskleri listeleme, salt okunur acma,
      katmanli onayla yazma ([ADR 0014](../decisions/0014-fiziksel-disk-destegi.md)).
      Windows ve Linux'ta uctan uca dogrulandi; isletim sistemine bildirim
      ([ADR 0015](../decisions/0015-isletim-sistemine-bildirim.md)) ve Windows
      birim kilitleme ([ADR 0016](../decisions/0016-windows-birim-kilitleme.md)) dahil
- [x] **ext2/3/4 ve NTFS saf Python bicimlendirme** — boylece **sekiz dosya sistemi
      de uc platformda** olusturulabilir ([ADR 0017](../decisions/0017-saf-python-ext-ve-ntfs.md),
      [ADR 0018](../decisions/0018-ntfs-platform-stratejisi.md))
- [x] **Bolum boyutlandirma ve tasima** — suruklenebilir serit; FAT/exFAT veri
      korunarak, NTFS/ext Windows'ta yerel araca devredilir
      ([ADR 0019](../decisions/0019-bolum-boyutlandirma.md))
- [x] **Coklu oturum** — birden fazla goruntu/disk ayni anda acik
- [x] **Ozel tema kaldirildi**, sistemin Qt gorunumu kullaniliyor ([ADR 0013](../decisions/0013-tema-kaldirildi.md))
- [x] **Salt okunur acilma teshisi** — neden gosterilir, kilit durumunda yeniden denenir

### v0.4.0 — tamamlandi
- [x] **ext2/3/4 okuyucu** — extent agaci, dolayli blok, sembolik bag izleme
- [x] **ext2/3/4 yazici** — `metadata_csum` (CRC-32C) dahil; her adim `e2fsck` ile
      dogrulanir (`tests/ext_write_check.py`)
- [x] **NTFS okuyucu** — MFT, fixup, veri kosullari, `$ATTRIBUTE_LIST`, B+ indeks
- [x] **NTFS yazici** — `$Bitmap`/`$MFT` tahsisi, `$MFT` kendiliginden buyume,
      INDX giris ekleme; `ntfsfix` + `ntfs-3g` ile dogrulanir
      (`tests/ntfs_write_check.py`)
- [x] **Sekiz dosya sisteminde de B O Y** — `tests/fs_matrix.py` 8/8
- [x] **`.dub` yedegi dogrudan gezilir** (geri yuklemeye gerek yok) ve fiziksel
      diske yazilabilir
- [x] Takilan USB/SD aygitlar listede **kendiliginden** belirir

### v0.5 — planlanan
- [ ] Bolum bolme / birlestirme (boyutlandirma altyapisi hazir; bolme = kucult + yeni bolum)
- [ ] Onaltilik duzenleyici (yazma destegi — su an goruntuleyici salt okunur)
- [ ] Dosya sistemi tutarlilik denetimi (chkdsk/fsck esdegeri)
- [ ] Birincil ↔ mantiksal bolum donusumu, bolum gizleme
- [ ] Bozuk sektor (okuma hatasi) taramasi — fiziksel disk destegi geldi

> **macOS kapsam disi birakildi.** Kod yollari yerinde ama dogrulanacak makine
> yok; olculmemis bir platformun destegini ilan etmemek icin README ve matris
> yalnizca **Windows ve Linux** diyor.

### Bilinen sinirlar (yazma)
- **ext:** extent agaci buyutme yok, cok katli dolayli blok yok (~4 MB ustu
  tek dosya), `bigalloc`/`inline_data` reddedilir.
- **NTFS:** B+ dugum bolme yok (dizin basina ~25-30 giris), sikistirilmis akis
  yok, `$ATTRIBUTE_LIST` yazimi yok.

Bunlarin hicbiri sessizce basarisiz olmaz; nedeni metinle bildirilip reddedilir.

### v0.6 ve sonrasi — fikirler
- [ ] Sanal disk bicimleri arasinda dogrudan donusturucu
- [ ] Onyukleme sektoru sablonlari (syslinux / GRUB yerlestirme)
- [ ] Bicimlendirilmis bolumde dizin yapisini yeniden kurma
- [ ] Goruntuyu fiziksel aygita yazma (Rufus/Etcher tarzi)

## Kapsam disi

Bu maddeler bilincli olarak disarida birakilmistir; gerekceleri
[diskgenius-parity.md](diskgenius-parity.md) icindedir:
S.M.A.R.T. izleme, bozuk sektor onarimi, RAID kurtarma, dinamik disk (LDM),
surucu harfi atama, UEFI onyukleme girisi yonetimi, WinPE kurtarma ortami.

## Ilgili belgeler
- Ozellik matrisi (DiskGenius): [diskgenius-parity.md](diskgenius-parity.md)
- Pazar/kaynak analizi: [feature-analysis.md](feature-analysis.md)
- Capraz platform: [cross-platform.md](cross-platform.md)
- Mimari: [architecture.md](architecture.md)
- Tutarlilik denetimi ve iyilestirme plani: [consistency-audit.md](consistency-audit.md)
- Is gunlugu: [worklog.md](worklog.md)
- Test: [testing.md](testing.md)
- Kararlar: [../decisions/](../decisions/)
- Bicim spesifikasyonlari: [../specs/](../specs/)
