# ADR 0011 — Windows'ta seyrek dosya uzatma

**Tarih:** 2026-09-13 · **Durum:** Uygulandi · **Dogrulama:** Win10 VM uzerinde olculdu

## Belirti
Windows 10 (NTFS) uzerinde ilk gercek kosumda test ortami denetimi
`Seyrek dosya: DESTEKLENMIYOR` bildirdi. Testler gecti ama **8 GB'lik test
goruntusu diske gercekten yazildi** ve kosum 18 saniye surdu.

## Inceleme
`FSCTL_SET_SPARSE` cagrisi **basariliydi** (`donus=1`, `GetLastError=0`), yani dosya
seyrek olarak isaretleniyordu. Sorun sonraki adimdaydi: Python'un `file.truncate(n)`
cagrisi Windows'ta CRT'nin `_chsize_s` islevine gider ve dosyayi uzatirken
**sifirlarla doldurur**.

VM icinde dort yontem 256 MB icin olculdu:

| Yontem | Sure | Gercek tahsis |
|---|---|---|
| `FSCTL_SET_SPARSE` + `truncate()` (eski kod) | 0.65 s | **256 MB** |
| `FSCTL_SET_SPARSE` + **`SetEndOfFile`** | **0.00 s** | **0 MB** |
| `FSCTL_SET_SPARSE` + `truncate()` + `FSCTL_SET_ZERO_DATA` | 0.52 s | 0 MB |
| Yalnizca `truncate()` | 0.46 s | 256 MB |

## Karar
`core/platform.truncate_sparse(fileno, size)` eklendi:
- **Windows:** `SetFilePointerEx` + `SetEndOfFile` (anlik, sifir tahsis).
  Basarisiz olursa `os.ftruncate`'e geri cekilir.
- **POSIX:** dogrudan `os.ftruncate` (dosya sistemi zaten seyrek uygular).

`DiskImage.create` ve `VhdImage.create_fixed` artik bunu kullaniyor.
Ayrica tum `kernel32` cagrilari icin **acik `argtypes`/`restype`** tanimlandi;
ctypes varsayilanlari 64 bit tutamaclari ve `DWORD` donus degerlerini bozabiliyordu.

## Sonuc (Win10 VM, tam test kosumu)

| | Once | Sonra |
|---|---|---|
| Seyrek dosya | desteklenmiyor | **destekleniyor** |
| Kosum suresi | 18.2 s | **13.2 s** |
| Gereken bos alan | 12 GB | **2 GB** |

## Ikinci bulgu: olcum uretim yolunu izlemeliydi
Duzeltmeden sonra rapor hala "DESTEKLENMIYOR" diyordu: `tests/run_all.py`
icindeki `_sparse_supported()` olcumu **kendi** `fh.truncate()` cagrisini
kullaniyordu, yani duzeltilen uretim yolunu degil eski yolu olcuyordu.
Olcum `make_sparse` + `truncate_sparse` ikilisine hizalandi.

**Ders:** bir yetenegi olcen test, o yetenegin uretimde kullandigi kod yolunun
aynisini cagirmalidir; aksi halde dogru kodu yanlis raporlar.

Ayrica `t11`'deki seyreklik dogrulamasi artik `IS_LINUX` kosuluna degil, dosya
sisteminin gercek destegine bagli — boylece Windows'ta da denetleniyor.
