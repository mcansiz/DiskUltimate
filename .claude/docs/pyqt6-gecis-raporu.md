# PyQt5 → PyQt6 geçiş raporu

Tarih: 2026-10-28. Tetikleyen: Flathub sorusu (worklog 2026-10-28 (3)).
Durum: **rapor** — karar verilmedi, kod değişmedi.

## Neden gündemde

Flathub'da PyQt5'in dayandığı tek çalışma ortamı `org.kde.Platform//5.15-24.08`
2026 baharından beri EOL (kullanıcılar Nisan–Mayıs 2026'dan beri uyarı
alıyor). Daha yeni bir 5.15 dalı yok. Flathub yeni başvuruda EOL çalışma
ortamını kabul etmez (inceleme kuralı; ayrıca doğrulanacak). Sonuç:
**Flathub için Qt6 şart.** Flathub dışında da Qt5'in güvenlik desteği
bitmiş durumda (Qt 5.15'in açık kaynak desteği 2023'te sona erdi).

## Kod taraması (ölçüm)

`src/diskultimate` içinde PyQt5 import eden **38 dosya, ~15 500 satır**.
`core/` PyQt import etmez (CLAUDE.md), bu yüzden geçiş tamamen `ui/`,
`main.py`, `tests/`, `tools/` ve paketlemeyle sınırlı.

| Değişiklik | Yer sayısı | Tür |
|---|---|---|
| Kısa enum `Qt.AlignLeft` → `Qt.AlignmentFlag.AlignLeft` | 258 kullanım, 47 farklı ad | mekanik |
| Sınıf enum'ları (`QMessageBox.Yes`, `QDialogButtonBox.Ok`, `QHeaderView.Stretch`, `QPalette.Window`…) | ~270 kullanım, ~60 farklı ad | mekanik |
| `exec_()` → `exec()` | 37 | mekanik |
| `QAction` / `QShortcut` QtWidgets → QtGui | 2 import satırı | mekanik |
| `fontMetrics().width()` → `horizontalAdvance()` | 5 | mekanik |
| `AA_EnableHighDpiScaling` / `AA_UseHighDpiPixmaps` (Qt6'da yok, hep açık) | 2 (`main.py`) | sil |
| Fare olayı `event.pos()/x()/y()` → `position()` | 26 (16 işleyici) | elle |
| Enum aritmetiği (`UserRole + 1`, `int(Qt.…)`) | 3–4 | elle |
| Sürüm bilgisi (`PYQT_VERSION_STR`, `PyQt5.sip`) — Hakkında penceresi | 1 yer | elle |
| `QtTest` kullanan testler | 5 | mekanik |
| Lisans: `PyQt5-sip` metni → `PyQt6-sip`; `run_all` lisans testi | 2 | elle |

Bulunmayanlar (iyi haber): `QDesktopWidget`, `QRegExp`, `QTextCodec`,
`QVariant`, `QStyle.SP_*`, `QtSvg`, `QtDBus`, `wheelEvent`, `enterEvent`
kullanılmıyor — Qt6'nın en çok kıran API'ları bu projede yok.

**Önemli kolaylık:** tam nitelikli enum adları (`Qt.AlignmentFlag.AlignLeft`)
PyQt5'te de çalışır. Enum dönüşümü PyQt5'i bozmadan yapılabilir; geçiş
tek seferlik büyük bir sıçrama olmak zorunda değildir.

## Paketleme ve dağıtım etkisi

| Yer | Değişiklik |
|---|---|
| `requirements.txt`, `README.md` | `PyQt5>=5.15` → `PyQt6` |
| `tools/appimage.py` | `PIP_PACKAGES`, `PYQT_KEEP` (PyQt6 + PyQt6-Qt6 + PyQt6-sip) |
| `DiskUltimate.spec` | `excludes` listesi, PyInstaller PyQt6 hook'u |
| `release.yml`, `tests.yml`, `gezinti.yml`, `long-tests.yml` | kurulum adımları |
| VirtualBox win10 misafiri | çevrimdışı tekerlekler yeniden (`windows-setup/`) |
| Live ISO | AppImage'ı kullanıyor → AppImage'la birlikte gelir; `tools/live/render_icons.py` derleme aracı |

## Platform alt sınırları — kullanıcıyı etkileyen asıl bedel

| | Bugün (PyQt5) | PyQt6 6.11 / Qt 6.8+ |
|---|---|---|
| Python | 3.8+ | **3.10+** (PyQt6 6.11 Python 3.9'u bıraktı) |
| macOS | 11+ (`LSMinimumSystemVersion`) | **12+** (Qt 6.8; doğrulanacak) |
| Linux glibc (AppImage) | 2.17 (manylinux2014) | **2.28** (PyQt6-Qt6 tekerlekleri manylinux_2_28; doğrulanacak) → CentOS 7, Ubuntu 18.04 düşer |
| Windows | 10/11 | 10 1809+ / 11 |
| musl (Alpine) | `py3-qt5` | `py3-pyqt6` (paket var) |

## Olası sorunlar (risk sırasıyla)

1. **Yüksek DPI hep açık, kesirli ölçek.** Qt6 ölçeği yuvarlamaz (%125, %150).
   QPainter ile çizilen her şey — `ui/icons.py` DRAWERS, disk haritası,
   boyutlandırma çubuğu, bölüm şeridi — yarım piksele düşebilir: bulanık
   çizgi, 1 px kayma, kırpılan metin. En çok gözle doğrulama isteyen kısım.
2. **Tema.** Fusion'ın Qt6 görünümü farklı; Qt 6.5+ sistemin koyu/açık
   tercihini kendisi okur — Sistem/Açık/Koyu (ADR 0088) mantığıyla çakışabilir.
   Windows'ta Qt 6.7+ varsayılan stil `windows11`: "Sistem" teması değişir.
3. **Enum'lar artık Python enum'u.** `int` ile karşılaştırma, ayara enum
   yazma, `exec()` dönüşünü `QDialog.Accepted` ile kıyaslama gibi yerler
   sessizce yanlış dalı seçebilir (hata vermeden). Duman testi + gözlemci
   gezintisi (`tests.long.explore`) bunu yakalamalı.
4. **Linux xcb eklentisi `libxcb-cursor0` ister** (Qt 6.5+). Uygulama xcb'yi
   zorluyor (`main.py`); bu kitaplık birçok kurulumda yok → "Qt platform
   eklentisi yüklenemedi". AppImage'a gömülmeli, live ISO paket listesine eklenmeli.
5. **Boyut.** Qt6 tekerlekleri daha büyük; exe / AppImage / ISO büyür (ölçülecek).
6. **pkexec ile yetkili kopya.** Ortam aktarımı zaten Wayland değişkenlerini
   taşıyor ve xcb zorlanıyor; Qt6'da yeniden sınanmalı (VM).
7. **Windows'ta UAC ile yükseltilmiş kopya**, macOS `.app` imzası/paketi:
   değişmez beklenir ama release hattında ölçülmeli.

## İş yükü tahmini

| Aşama | İçerik | Tahmin |
|---|---|---|
| A. Ortak katman | `ui/qt.py` uyum modülü (PyQt6 varsa onu, yoksa PyQt5); tüm importlar oradan | 1 oturum |
| B. Mekanik dönüşüm | enum'ları tam nitelikli yaz (betikle, ~530 yer), `exec_`, `QAction`, `width` → **PyQt5'te de çalışır**, CI yeşil kalır | 1 oturum |
| C. Elle düzeltmeler | fare olayları, enum aritmetiği, HiDPI öznitelikleri, sürüm/lisans | 1 oturum |
| D. Doğrulama | CI'da PyQt5 + PyQt6 matrisi; `ui_smoke`, `explore`, qps; Windows VM ve macOS'ta göz kontrolü (%100/%125/%150) | 1–2 oturum |
| E. Paketleme | AppImage, exe, macOS, live ISO, `libxcb-cursor0`, boyut ölçümü | 1–2 oturum |
| F. PyQt5'i bırakma | uyum katmanından PyQt5 dalını kaldırma, README/CLAUDE.md alt sınırlar | kısa |

Toplam: **5–8 oturum.** Risk, mekanik kısımda değil; D ve E'de (görünüm ve
paketleme). B aşamasının PyQt5'i bozmaması geçişi geri alınabilir yapar:
herhangi bir noktada durulsa bile uygulama çalışır kalır.

## Öneri

- A+B'yi Flathub kararından bağımsız yapmak mantıklı (Qt5 zaten destek dışı).
- Geçiş sürümünde iki bağlama birden desteklenir; tek sürüm sonra PyQt5 bırakılır.
- Python 3.8/3.9, macOS 11 ve eski glibc kullanıcıları için son PyQt5
  sürümü (0.10.x) ayrı bir indirme olarak kalabilir.
- Alternatif PySide6 (LGPL, Qt'nin resmi bağlaması) — lisans GPL olduğu için
  avantaj sağlamaz, API farkı PyQt6'dan daha fazladır. Önerilmez.
