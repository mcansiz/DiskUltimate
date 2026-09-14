# ADR 0018 — NTFS icin uc katmanli strateji

**Tarih:** 2026-09-13 · **Durum:** Kabul edildi · **Dogrulama:** Win10 misafirinde `chkdsk` temiz

## Baglam
Saf Python NTFS bicimlendiricisi ([ADR 0017](0017-saf-python-ext-ve-ntfs.md))
Linux araclarinca kabul ediliyordu (`ntfsinfo`, `ntfsfix` temiz) ama Windows
birimi otomatik baglamiyordu: `chkdsk` bicimi taniyor, `Get-Volume` ise
`FileSystemType: Unknown` diyordu. Eksik olan `$Extend` altindaki
$ObjId/$Quota/$Reparse yapilari ve $O indeksiydi.

Kullanicinin sorusu yonu degistirdi: *"NTFS-3G / libntfs-3g bunu kullanmak
yeterli olmuyor mu?"*

## Bulgu
- **Linux:** `mkfs.ntfs`, `mkntfs`, `libntfs-3g.so` zaten kurulu ve baslangictan
  beri kullaniliyordu — sorun hic burada degildi.
- **Windows:** ntfs-3g'nin resmi Windows derlemesi yok (GnuWin32 portu 2009'dan
  kalma, 64 bit desteklemiyor), `libntfs-3g.dll` bulunmuyor. Yani "ntfs-3g
  kullanalim" Windows tarafini cozmuyor.
- **Ama Windows'ta NTFS bicimlendiricisi zaten var:** isletim sisteminin kendisi
  (`Format-Volume`, her kurulumda bulunur).

## Karar
NTFS (ve Windows'ta exFAT/FAT) icin **uc katmanli** oncelik:

1. **Isletim sisteminin kendi araci** — Windows'ta `Format-Volume`.
   `session._try_native_format()` yalnizca **fiziksel disklerde** devreye girer;
   bolum nesnesi dogrudan verildigi icin surucu harfi gerekmez.
2. **Harici `mkfs.*`** — Linux/macOS'ta ntfs-3g, e2fsprogs vb.
3. **Saf Python** — hicbiri yoksa (`core/ntfs.py`, `core/ext.py`).

Basarisizlik sessizce bir alt katmana duser; kullanici her durumda bir sonuc alir.

## Neden bu sira
Her platformda **o platformun en olgun araci** kullanilir. NTFS gibi belgelenmemis
ve karmasik bir bicimde, isletim sisteminin kendi uygulamasi her zaman en
uyumlu sonucu verir. Saf Python yol yine de degerlidir: hicbir arac bulunmayan
ortamlarda (ornegin arac kurulu olmayan bir macOS) tek secenektir ve goruntu
dosyalarinda calisir.

## Dogrulama (Win10 misafiri, `\\.\PhysicalDrive1`)
```
Get-Volume -DriveLetter E
  FileSystemLabel : DUNTFS
  FileSystem      : NTFS
  FileSystemType  : NTFS
  HealthStatus    : Healthy

chkdsk E:
  Stage 1/2/3 tamamlandi
  Windows has scanned the file system and found no problems.
  10 dosya, 14 indeks, 0 bozuk sektor
```
Dosya ve klasor yazildi, geri okundu.

## Sonuc
Sekiz dosya sistemi de uc platformda olusturulabiliyor. NTFS artik Windows'ta
**tam uyumlu**; saf Python uygulamasi son catman olarak korunuyor ve Linux
araclarinca dogrulanmis durumda.
