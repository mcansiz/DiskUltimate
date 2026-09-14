# ADR 0007 — exFAT saf Python ile uygulandi

**Tarih:** 2026-09-13 · **Durum:** Kabul edildi

## Baglam
exFAT baslangicta harici `mkfs.exfat` ile bicimlendiriliyordu (ADR 0002). Capraz
platform hedefi geldiginde bu yaklasim tikandi: **Windows ve macOS'ta `mkfs.exfat`
yok.** exFAT ise tasinabilir diskler icin fiili standart — destegi harici araca
birakmak, uygulamayi o platformlarda FAT32'nin 4 GB dosya siniriyla birakirdi.

## Karar
exFAT bicimlendirme, okuma ve yazma `core/exfat.py` icinde sifirdan uygulandi.
`formatter.FS_KINDS` icinde exFAT artik `internal=True`.

## Uygulama kapsami
- Bicimlendirme: onyukleme bolgesi + yedegi, saglama sektoru, FAT, ayirma bitmap'i,
  upcase tablosu, kok dizin
- Okuma: dizin listeleme, UTF-16 uzun adlar, ardisik ve FAT zincirli dosyalar
- Yazma: dosya olusturma, klasor, silme, yeniden adlandirma, birim etiketi
- Bitmap tabanli kume tahsisi (once ardisik blok aranir, yoksa dagitik + FAT zinciri)

## Kritik bulgu: upcase tablosu serbest degil
Ilk uygulamada tablo Python'un `str.upper()` esleminden uretildi ve saglamasi
tutarli yazildi. `fsck.exfat` bunu **reddetti**:
`ERROR: corrupted upcase table 0 (expected: 0x495eba0f)`.

exfatprogs ve Windows, spec'teki **onerilen** tabloyu bekliyor. Cozum: standart
tablonun sikistirilmis hali (5836 bayt, saglama `0xE619D30D`) kaynaga gomuldu
(zlib+base64) ve sikistirma acma islevi yazildi.

**Ders:** "Bicim serbest birakiyor" diye okunan alanlar, gercek uygulamalar
tarafindan katı bicimde denetlenebiliyor. Bagimsiz bir dogrulayici (`fsck`) olmadan
bu hata fark edilmezdi.

## Dogrulama
`tests.run_all::t09` — bicimlendirme, dosya islemleri, yeniden baglama, gomulu
tablonun saglamasi ve `fsck.exfat -n` ("clean") denetimi.
