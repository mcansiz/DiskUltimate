# ADR 0013 — Ozel tema kaldirildi, sistem gorunumu kullaniliyor

**Tarih:** 2026-09-13 · **Durum:** Kabul edildi

## Baglam
Arayuz, DiskGenius'a benzemesi icin elle yazilmis bir Qt stil sayfasi kullaniyordu
(sabit renkler, `Fusion` stili zorlamasi, ikon temasi degistirme). Kullanici
"bazi seyler belli olmuyor" geri bildirimi verdi: sabit acik renkler masaustunun
kendi temasiyla (ozellikle koyu temalarla) catisiyordu.

Ayrica Windows'ta stil sayfasindaki `font-weight` sekme basliklarini kirpmisti
(ADR 0012) — ozel temanin bakim maliyeti gorunur hale geldi.

## Karar
Uygulama **sistemin kendi Qt gorunumunu** kullanir:
- `app.setStyleSheet("")` — hicbir stil sayfasi uygulanmaz
- `setStyle("Fusion")` zorlamasi kaldirildi
- Ikon temasi degistirilmez (masaustunun kendi ikonlari kullanilir)

Sabit renkler kaldirildi; widget'lar rengi **paletten** alir:
`theme.palette_color(widget, "text"|"dim"|"base"|"window"|"highlight")`.

Tek istisna **dosya sistemi renkleridir** (`fs_color`): FAT32 mavi, ext4 turuncu
gibi renkler anlamsaldir ve temadan bagimsiz sabit kalir. Disk haritasinda blok
uzerindeki metnin rengi, o blogun zeminine gore acik/koyu secilir.

## Ileriye donuk
`theme.apply_theme(app, name)` ve `THEMES` sozlugu yerinde birakildi. Eski stil
sayfasi `STYLESHEET` olarak duruyor ama uygulanmiyor. "Tema" bolumu eklendiginde
kullanici `system` / `diskultimate` arasinda secim yapabilecek; altyapi hazir.
Gecici deneme icin: `DISKULTIMATE_THEME=diskultimate python3 main.py`

## Dogrulama
Koyu sistem temasi altinda duman testi calistirildi; ana pencere, disk haritasi,
tablo, agac ve diyaloglarin tamami okunabilir.
