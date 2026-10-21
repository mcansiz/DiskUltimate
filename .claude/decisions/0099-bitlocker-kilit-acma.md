# 0099 — BitLocker birimini kilit açma (parola / kurtarma anahtarı ile)

Tarih: 2026-10-21
Durum: **uygulanıyor** — çekirdek + kripto + bağlama bu oturumda; uçtan uca
gerçek imaj doğrulaması CI'da (`cryptsetup bitlkOpen`/`bitlkDump`) ve Windows
misafirinde üretilen örnekle.
İlgili: [0053](0053-sifreli-ve-kapsayici-birimler.md) (sifreli/kapsayici tespiti),
[0025](0025-islem-kuyrugu.md) (kaynak salt okunur), [0014](0014-fiziksel-disk-destegi.md).

## Kullanıcı isteği

"Windows için BitLocker şifresi unutulursa bunu düzeltebilir miyiz?"

Netleştirme: unutulmuş parola **kırılamaz** (BitLocker'ın amacı budur) ve bir
kırıcı yazılmayacak. Yapılacak olan, DiskGenius'un da yaptığı şey: kullanıcı
**parolayı** ya da **48 haneli kurtarma anahtarını** girerse birimi açıp
içeriğini (NTFS/FAT) okumak — yani *kilit açma*, kurtarma değil.

## Karar

Yeni modül `core/bitlocker.py` (saf Python, PyQt import etmez, çalışma zamanı
bağımlılığı yok — CLAUDE.md). Üç katman:

1. **Kripto temelleri** — AES (128/256, T-tabloları), AES-XTS (plain64),
   AES-CBC + EBOIV, AES-CCM (RFC 3610 doğrulamalı). SHA-256 `hashlib`'ten.
   Kamuya açık vektörlerle (FIPS-197, IEEE 1619 XTS, RFC 3610 CCM) tam
   doğrulanır — `tests/bitlocker_check.py`.
2. **Metaveri + anahtar türetme** — FVE bilgi bloğu ve 3 metaveri kopyası
   çözümlenir; koruyucular (protector) listelenir **gizli veri sızdırmadan**
   (`inspect()`): parola, kurtarma parolası, TPM, TPM+PIN, açılış anahtarı,
   akıllı kart, temiz anahtar. Parola germe (key stretch):
   `h = SHA256(SHA256(utf16le(parola)))`, sonra 0x100000 turluk zincir
   (last32 + initial32 + salt16 + count8). Kurtarma: 48 hane → 8×uint16 = 16
   bayt → `SHA256` → aynı zincir. VMK ve FVEK AES-256-CCM ile çözülür (MAC
   doğrulanır — yanlış parola burada anlaşılır, çöp FVEK üretilmez).
3. **Şifre çözen blok aygıtı** — `BitLockerVolume(BlockDevice)` kaynağın
   üstüne biner, `read()` çağrısında düz metni üretir. Eşleme (cryptsetup
   `_activate` ile birebir):
   * mantıksal sektör = fiziksel sektör (1:1), tweak = sektör;
   * ilk `volume_header_size` bayt fiziksel `volume_header_offset`'ten okunur,
     tweak = **orijinal** fiziksel sektör;
   * orijinal `volume_header_offset` bölgesi ve **3 FVE metaveri bloğu** (her
     biri 0x10000) ZERO segmenttir — şifresiz, sıfır döner.

Bağlama: `open_filesystem(view, info, unlock=...)` BitLocker görünce `unlock`
verilmişse birimi sarmalar ve içteki dosya sistemini (NTFS/FAT) açar; anahtar
yoksa eskisi gibi `UnsupportedAccess` (artık "parola/kurtarma anahtarı girin"
der). `session.filesystem(index, unlock=...)` anahtarı GUI'den taşır.

## Kapsam (bu sürüm)

- **AES-XTS** (Win10/11 varsayılanı, 0x8004/0x8005): tam. En yaygın hedef.
- **AES-CBC + EBOIV** (0x8002/0x8003): tam.
- **AES-CBC + Elephant diffuser** (Vista/7, 0x8000/0x8001): **henüz yok** —
  anlamlı hata verilir, çöp veri döndürülmez. Elephant ayrı bir iştir.
- Yalnızca **okuma**. Şifreli birime yazma yok (kaynak salt okunur — ADR 0025).
- TPM/akıllı kart koruyucuları: anahtar donanımdan gelir, kilit açılamaz;
  `inspect()` bunları listeler ki kullanıcı neyin gerektiğini görsün.

## Güvenlik / dürüstlük

- Parola/kurtarma anahtarı bellekte tutulur, loga/metaveriye yazılmaz
  (CLAUDE.md: gizli bilgi dökümlere girmez).
- Yanlış parola CCM MAC doğrulamasında anlaşılır → net hata, sessiz bozulma
  yok ("bilinmiyor asla risk yok sayılmaz" ilkesinin kripto karşılığı).
- Kaynak her zaman salt okunur açılır.

## Test planı

- Birim: kripto vektörleri + kurtarma-parolası çözümü + TLV round-trip
  (ana makinede, `tests/bitlocker_check.py`, `run_all`'a eklenir).
- Uçtan uca: Windows misafirinde `manage-bde` ile bilinen parolalı küçük bir
  VHD/bölüm imajı üretilir; CI'da (VM — ADR 0080) `cryptsetup bitlkDump` ile
  metaveri, `bitlkOpen` ile düz metin çapraz doğrulanır. Ana makinede root
  yok, çekirdek bağlama yapılmaz (CLAUDE.md); doğrulama kullanıcı alanı
  araçlarıyla. Bu doğrulama yapılana kadar özellik "test edildi" denmez.
