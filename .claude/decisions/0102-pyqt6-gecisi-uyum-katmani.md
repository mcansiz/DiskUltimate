# 0102 — PyQt6 geçişi: uyum katmanı (`ui/qt`) ve tam nitelikli enum'lar

Tarih: 2026-10-31
Durum: **A–E uygulandı** (kod ve paketleme iki bağlamada çalışır; etiket
sürümleri hâlâ PyQt5). F (varsayılanı PyQt6'ya çevirme, PyQt5'i bırakma)
kullanıcı kararı bekliyor — alt sistem sürümleri yükselir.
İlgili: `.claude/docs/pyqt6-gecis-raporu.md` (iş yükü ve riskler),
ADR 0050 (Flatpak elenmişti), worklog 2026-10-28 (3)–(4).

## Bağlam

Flathub'da PyQt5'in dayandığı tek çalışma ortamı (KDE 5.15-24.08) 2026
baharından beri EOL; Qt 5.15'in açık kaynak desteği de bitti. Kullanıcı
kararı (2026-10-31, "önerini uygulayalım"): Flathub kararından bağımsız
olarak geçiş başlasın; geçiş döneminde iki bağlama birden desteklensin.

## Karar

1. **Arayüz kodu PyQt'yi doğrudan import etmez.** `ui/qt/` paketi bağlamayı
   bir kez seçer; alt modüller (`QtCore`, `QtGui`, `QtWidgets`, `QtTest`)
   seçilenin `*` içeriğini verir. Kod `from ..qt.QtWidgets import (...)`
   yazar — eski satırların yalnızca kaynağı değişti.
   - Seçim: `DISKULTIMATE_QT=pyqt5|pyqt6`; yoksa önce PyQt5, sonra PyQt6
     (dağıtılan paketler henüz PyQt5 taşır).
   - Qt6'nın QtGui'ye taşıdığı `QAction`, `QActionGroup`, `QShortcut`,
     `QFileSystemModel` `ui/qt/QtWidgets`'ten de alınır (qtpy'nin yaptığı gibi).
   - Yardımcılar: `qt.QT6`, `qt.API`, sürüm dizgeleri, `enum_value()`,
     `translations_path()`, `package_dir()`.
   - qtpy kullanılmadı: çalışma zamanı bağımlılığı olurdu (CLAUDE.md).
2. **Enum'lar tam nitelikli yazılır** (`Qt.AlignmentFlag.AlignLeft`,
   `QMessageBox.StandardButton.Yes`). Bu yazım PyQt5'te de çalışır (ölçüldü),
   yani dönüşüm geri alınabilir ve tek seferlik sıçrama gerektirmez.
3. **Denetim: `tests.qt6_check`** (`--write` düzeltir). Doğru enum'u PyQt6'ya
   sorar, tam adın PyQt5'te aynı sayıya çözüldüğünü doğrular; kendi alt
   sınıflarımızı (`NewImageDialog.Accepted`) sınıf tanımlarından Qt atasına
   kadar izler. Ayrıca: `exec_`, ezilmiş `def exec_`, PyQt'nin doğrudan
   importu, `ui/qt`'den alınıp PyQt6'da olmayan adlar, `QFontDatabase()`.
   PyQt6 yoksa enum kuralı ATLANDI yazar. Bilerek korunan Qt5 dalı satır
   sonuna `# qt6_check: izin` yazılarak geçilir.
4. **CI:** `ci.yml` → `linux-qt6` işi (PyQt5 + PyQt6 kurulu): `qt6_check` +
   `ui_smoke` (`DISKULTIMATE_QT=pyqt6`). Sabit sürümler
   `.github/requirements-ci-qt6.txt`.

## Ölçülen farklar (PyQt5 5.15.11 ↔ PyQt6 6.12.0)

| Fark | Bu projede | Çözüm |
|---|---|---|
| Kısa enum yok | ~600 yer, 44 dosya | `qt6_check --write` |
| Küçük harfli enum üyeleri (`Qt.transparent`, GlobalColor) | 4 | aynı araç (büyük harf varsayımı kaldırıldı) |
| Örnek üzerinden enum (`palette().Window`) | 2 | `palette().window().color()` |
| `getattr(QPalette, ad)` | 1 (`theme.build_palette`) | `getattr(QPalette.ColorRole, ad)` |
| `QtCriticalMsg` vb. modül düzeyinde yok | `ui/diag.py`, `tests/long/explore.py` | `QtMsgType.QtCriticalMsg` |
| `exec_()` yok | 39 çağrı | `exec()` (PyQt5'te de var) |
| **Ezilmiş `def exec_`** | 5 diyalog (TaskDialog işçiyi orada başlatır!) | `def exec` — çağrılar `exec()` olunca eski ezme sessizce devre dışı kalırdı |
| `QMouseEvent.x()/y()/globalPos()` yok (`pos()` var) | 6 | `event.pos().x()` |
| `QFontMetrics.width()` yok | 7 | `horizontalAdvance()` (Qt 5.11+) |
| `AA_EnableHighDpiScaling`, `AA_UseHighDpiPixmaps` yok | `main.py` | yalnızca varsa ayarlanır |
| `QIcon.Mode`, `KeyboardModifier`, `MouseButton` artık `int` değil | `int(mode)` ikon önbelleği | `enum_value()` |
| `QFontDatabase()` örneği yok | `ui_smoke` | Qt6'da statik çağrı |
| `QLibraryInfo.location()` yok | `qt_i18n` | `translations_path()` |
| `DialogCode`, `StandardButton`, `Key`, `ItemDataRole`, `AlignmentFlag` | — | IntEnum/IntFlag: `int` ile kıyas ve `UserRole + 1` çalışır |

## D. Görsel doğrulama (2026-10-31)

Araç: `.github/workflows/qt6-gorsel.yml` (elle) — `ui_smoke` PyQt5/PyQt6 ×
`QT_SCALE_FACTOR` 1/1.25/1.5, 46 ekran görüntüsü (açık/koyu tema dahil).
Gezintiye `linux-goruntu-hepsi-qt6` ve `windows-goruntu-arayuz-qt6` eklendi.

- **İşlev:** PyQt6 ile `ui_smoke` Linux (xvfb+xcb ×1/1.25/1.5), Windows
  (gerçek `windows` eklentisi ×1/1.5), macOS (×1/1.5) geçti (koşu 41200374563).
  PyQt6 gezintisi Linux ve Windows'ta yeşil (41199413821).
- **Ekransız kip karşılaştırmaya uygun değil:** PyQt5 offscreen 12 pt Noto /
  100 DPI, PyQt6 9 pt / 96 DPI seçer; Windows'ta ikisi de yazı tipi bulamaz
  (PyQt5 metin çizmez, PyQt6 kutu çizer). Gerçek xcb'de ikisi aynı:
  Sans Serif 9 pt, 17 px, 96 DPI (ölçüldü). Görsel iş akışı bu yüzden
  gerçek eklentilerle koşar.
- **Görünüm:** çizim hatası yok (disk haritası, şerit, tutamaklar, tema,
  onaltılık, ikonlar; ×1.5 dahil). Qt6 Fusion kenar payları daha sıkı:
  Linux'ta diyaloglar ~%5–8 küçük; Windows'ta Qt6 stili biraz daha geniş.
- **Kesirli ölçekte ikonlar Qt6'da daha keskin:** `QIcon.pixmap(16)` ×1.5'te
  24 fiziksel piksel + DPR 1.5 döner (ikon motoru tam boyutta çizer);
  PyQt5 16 px verip büyütür (bulanık). `ui_smoke` artık mantıksal boyutu ölçer.
- **Sınanamayan:** Windows 11'de Qt 6.7+ varsayılan `windows11` stili
  (GitHub makinesi Server 2022); gerçek HiDPI monitörde göz kontrolü.
- **PyQt6'ya özgü olmayan iki bulgu:** macOS `ui_smoke` aralıklı
  `Abort trap: 6` (0.10.2'de PyQt5 ile de görüldü; `tests.yml`'e
  `PYTHONFAULTHANDLER` eklendi); PyQt5 gerçek Windows eklentisinde test
  bitince 127 ile çıkar (kapanış; offscreen CI etkilenmez).

## E. Paketleme (2026-10-31)

Tek ayar: `DISKULTIMATE_QT` (pyqt5 | pyqt6). `release.yml` elle
başlatılınca `qt` girdisi seçilir; **etiket sürümleri PyQt5** (girdi boş).
Deneme: `gh workflow run release.yml -f qt=pyqt6` (release oluşturmaz).

- `tools/appimage.py`: bağlamaya göre paket/klasör/`libQtN` önekleri;
  PyQt6 sürümleri `.github/requirements-ci-qt6.txt`ten (CI'da sınanan =
  paketlenen). Qt-dışı kullanılmayan kitaplıklar (FFmpeg `libav*`) ve tireli
  Qt adları (`libQt6FFmpegStub-crypto`) temizlenir.
- **`libxcb-cursor.so.0` gömülür** (Qt6 xcb eklentisi ister, Qt5 istemezdi).
  Derleme makinesinden alınmaz: Ubuntu 24.04'ünkü `__isoc23_strtol@GLIBC_2.38`
  ister ve alt sınırı 2.34 → 2.38 çıkarıyordu. Debian 12 paketi (SHA-256
  Debian `Packages` dizinine karşı doğrulandı) yalnızca GLIBC_2.8 ister;
  `.deb` saf Python ile açılır.
- `DiskUltimate.spec`: excludes/drop desenleri ve çeviri yolu bağlamaya göre;
  macOS `LSMinimumSystemVersion` PyQt6'da **14.4** (ölçülen minos; tekerlek
  etiketi 11.0 yanıltıcı). `release.yml` her derlemede minos'u yazdırır.
- `smoke_launch.py --qt`: paket `DISKULTIMATE_QT`'siz başlatılır, günlükteki
  `qt baglamasi:` satırı denetlenir (paketin kendi seçimi).
- Lisanslar: `licenses.components(pyqt)`, `qt_source_url(sürüm)`; Hakkında
  metninde "PyQt" (9 sözlük güncellendi). Live ISO: `libxcb-cursor0`,
  `libxcb-util1`.

Deneme derlemesi 41203127471 (PyQt6 6.12.0 / Qt 6.12.0) — hepsi yeşil:

| | PyQt5 (0.10.2) | PyQt6 | Alt sınır (PyQt6, ölçülen) |
|---|---|---|---|
| Testler | — | Linux/Windows/macOS tam takım | — |
| AppImage | 40 MB | 45,0 MB | glibc **2.34** (PyQt5: 2.17) |
| Windows exe | 28 MB | 29,7 MB | Windows 10 1809+ (Qt 6) |
| macOS zip | 28 MB | 26,3 MB | macOS **14.4** (PyQt5: 11) |
| Live ISO | 487 MB | 491,7 MB | BIOS/UEFI/SB'de `APP-WINDOW` (gerçek xcb) |

Python alt sınırı 3.10 (PyQt6 6.11+). Daha geniş destek gerekirse PyQt6
6.9.x (manylinux_2_28 → glibc 2.28; Python 3.9) sabitlenebilir — Qt 6.9
güncelleme almaz; karar F'de.

## Sonraki aşamalar

- **E. Paketleme:** AppImage (`tools/appimage.py`), PyInstaller spec, macOS,
  live ISO; `libxcb-cursor0`; boyut ölçümü; lisans metinleri (PyQt6-sip).
  Hakkında penceresindeki "PyQt5" metni ve `licenses` anahtarları o zaman.
- **F. PyQt5'i bırakma:** etiket sürümlerini PyQt6'ya çevir (release.yml
  varsayılanı), `ui/qt` sırasını PyQt6 önce yap, README/CLAUDE.md alt
  sınırları (yukarıdaki ölçülen tablo), sonra PyQt5 dalını kaldır.
  Raporun tahmini (macOS 12 / glibc 2.28) ölçümle yükseldi.
