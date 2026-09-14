# ADR 0003 — GUI ile cekirdek arasinda tek cephe: `DiskSession`

**Tarih:** 2026-09-13 · **Durum:** Kabul edildi

## Baglam
Proje zamanla yeni dosya sistemleri, yeni bolum semalari ve yeni araclar
kazanacak. Arayuzun bu degisikliklerden etkilenmemesi gerekiyor.

## Karar
- `core/` **hicbir kosulda PyQt import etmez**; terminalden test edilebilir.
- Arayuz `MBRTable`, `GPTTable`, `FatFS` gibi siniflari dogrudan cagirmaz;
  yalnizca `DiskSession` ile konusur.
- Dosya icerigi erisimi `FileSystemAccess` arayuzu ardinda durur; gezgin hangi
  dosya sistemiyle calistigini bilmez.

## Sonuc
- ext4 okuyucu eklendiginde yalnizca `filesystem.py` icinde yeni bir sinif ve
  `open_filesystem` icinde bir satir degisir; `file_browser.py` degismez.
- Cekirdek testleri arayuz olmadan, `fsck`/`fdisk` ile capraz dogrulanabiliyor.

## Uygulama notu
`DiskSession.reload()` her yapisal degisiklikten sonra tabloyu ve dosya sistemi
bilgilerini diskten yeniden okur. Arayuz kendi durumunu bellekte tutmaz; boylece
"ekranda gorunen" ile "diskte olan" ayrisamaz.
