# ADR 0005 — Qt platform eklentisi, ikon temasi ve taban stil

**Tarih:** 2026-09-13 · **Durum:** Kabul edildi

## Baglam
KDE Plasma + Wayland oturumunda arayuz iki sorun gosterdi:

1. **Bos diyalog penceresi.** `Yeni Disk Goruntusu` penceresi aciliyor ama icerigi
   hic boyanmiyordu: grup kutulari, etiketler ve butonlar gorunmuyor, yalnizca odakli
   `QLineEdit` ciziliyordu. Pencerenin arka plani bile stil sayfasindaki renge
   boyanmamisti.
2. **Gorunmez ikonlar.** Arac cubugu ve dosya gezgini ikonlari acik zeminde
   secilemiyordu.

## Inceleme
- Ayni diyalog `QT_QPA_PLATFORM=xcb` (XWayland) altinda **eksiksiz** cizildi;
  `offscreen` altinda da sorunsuzdu. Yani sorun tema veya yerlesim degil, Qt 5.15'in
  yerel Wayland eklentisinin modal pencerelerde ilk kareyi boyamamasiydi.
- `QIcon.themeName()` sonucu **`breeze-dark`** cikti. Ikonlar eksik degildi
  (`standardIcon(...).isNull()` hepsi `False`); acik renkli olduklari icin
  uygulamanin acik zemininde kayboluyorlardi.

## Kararlar

**1. Wayland oturumunda XWayland (`xcb`) kullanilir.**
`main.py::secilen_platform()` oturum tipini denetler; Wayland ise ve `DISPLAY`
mevcutsa `QT_QPA_PLATFORM=xcb` ayarlanir. Kullanici `DISKULTIMATE_QPA=wayland`
veya `QT_QPA_PLATFORM=...` ile bunu her zaman gecersiz kilabilir. Secilen platform
islem gunlugune yazilir.

**2. Ikon temasi acik varyanta cekilir.**
`theme.ikon_temasini_ayarla()` tema adi `-dark` ile bitiyorsa acik surumu dener
(`breeze-dark` → `breeze`), bulunamazsa `Adwaita`, o da yoksa Qt'nin gomulu ikon
setine duser.

**3. Taban stil olarak Fusion secilir.**
Masaustu temasindan bagimsiz, stil sayfasiyla tutarli calisan tek taban stil.

**4. Guvenlik agi: `dialogs/base.exec_dialog()`.**
Diyaloglar bu yardimci uzerinden acilir; gosterim sonrasi bir yeniden cizim
tetikler. Kullanici yerel Wayland'i zorlarsa ilk kare sorununu hafifletir.

## Yan bulgular (ayni degisiklikle duzeltildi)
- Stil sayfasindaki `QComboBox::drop-down` kurali acilir liste okunu siliyordu →
  kural kaldirildi, ok geri geldi.
- `QSpinBox`/`QDoubleSpinBox`'a kutu kurali verilince Fusion artir/azalt oklarini
  cizmiyordu → bu iki denetime yalnizca renk kurali birakildi.
- Diyalog dugmeleri Ingilizce kaliyordu (`Cancel`, `Close`) → "Iptal", "Kapat".

## Dogrulama
`python3 -m tests.ui_smoke` sekiz ekran goruntusu uretir; ana pencere, dort sekme ve
uc diyalog gozle denetlenir. `DISKULTIMATE_QPA=xcb` ile gercek cizim yolu test edilir.
