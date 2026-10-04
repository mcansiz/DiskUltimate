# 0090 — Üçüncü taraf lisansları (Qt/PyQt5/Python), Hakkında, güncelleme denetimi

Tarih: 2026-10-04
Durum: **uygulandı**. İlgili: [0079](0079-github-actions-ci-ve-surum.md),
[0042](0042-acilista-kosulsuz-yetki.md), [0046](0046-ikon-setleri-ve-gomulu-paketler.md).

## 1. Lisanslar — kullanıcı sorusu: "PyQt5 için ekleme gerekmiyor mu?"

**Gerekiyordu.** "Üçüncü taraf lisansları" yalnızca ikon paketlerini
gösteriyordu; oysa exe ve AppImage Qt'yi, PyQt5'i, sip'i ve Python'u
**içinde** dağıtır (yeniden dağıtım). Kaynaklar ve gereklilikler:

| Bileşen | Lisans | Dağıtımda gereken |
|---|---|---|
| Qt 5.15 (PyQt5-Qt5) | LGPL-3.0 | lisans metni + belirgin bildirim; Qt kaynağı ya da **yazılı teklif** (Qt'nin SSS'si yalnızca bağlantıyı yeterli saymıyor); kullanıcının Qt'yi değiştirip uygulamayı onunla çalıştırabilmesi |
| PyQt5 | GPL-3.0 (ya da ticari) | uygulama GPL olmalı — DiskUltimate GPL-3.0, uyumlu; kaynak GitHub'da |
| PyQt5-sip | BSD-2-Clause | telif + lisans metni ikili dağıtımda |
| Python | PSF-2.0 | PSF telif bildirimi ve lisans metni korunur |

Kaynaklar: Qt "Obligations of the GPL and LGPL"
(qt.io/development/open-source-lgpl-obligations), Qt SSS 3.7, Riverbank
PyQt lisansı, PSF lisans sözleşmesi. Kurulu paketlerin meta verisi:
PyQt5 "GPL v3", PyQt5-Qt5 "LGPL v3", PyQt5-sip "BSD-2-Clause".

Karar: `diskultimate/licenses/` — metinler kurulu paketlerin kendi lisans
dosyalarından (Qt LGPLv3, sip BSD, Python LICENSE.txt) ve depodaki
`LICENSE`'tan (GPLv3; LGPLv3 GPLv3'e ek koşul olduğu için o da gerekir).
Pencere bileşenleri çalışma anındaki sürümle (`QT_VERSION_STR` vb.) ve tam
metinle gösterir; Qt için bildirim + yazılı teklif (3 yıl, proje sayfasında
istek) + resmi arşiv. `.spec` metinleri exe'ye alır (eksikse derleme durur);
AppImage kaynak ağacını kopyaladığı için kendiliğinden girer.

Yeniden bağlama (LGPLv3 §4): onefile exe Qt DLL'lerini geçici klasöre açar;
kullanıcı Qt'yi exe içinde değiştiremez. Uygulamanın tamamı açık kaynak ve
derleme betikleri depoda olduğu için kullanıcı farklı bir Qt ile kaynaktan
çalıştırabilir ya da yeniden paketleyebilir — bildirimde yazılı.

## 2. Hakkında

Geliştirici adı (Mikail Cansız), proje sayfası (tıklanabilir), lisans satırı.
Bağlantılar Qt'ye bırakılmaz (aşağı bakın).

## 3. Güncelleme denetimi — kullanıcı isteği

* `core/updates.py`: GitHub Releases listesi (bütün sürümler ön sürüm,
  `/releases/latest` 404 verir), taslaklar atlanır, en yeni **sürüm
  numarasıyla** seçilir (eski dal düzeltmesi "en yeni" sayılmaz);
  `0.6.1-beta < 0.6.1-rc1 < 0.6.1 < 0.6.2-beta`. Ağ okuması ile karar ayrı.
* Açılışta sessiz (pencere göründükten 4 sn sonra, `QThread`): yeni sürüm
  yoksa/ağ yoksa pencere açılmaz; varsa "GitHub sayfasını aç / Bu sürümü atla
  / Daha sonra". Yardım > Güncellemeleri denetle (sonuç her durumda), Yardım >
  Açılışta güncellemeleri denetle (ayar). `DISKULTIMATE_UPDATE_CHECK=0`
  kapatır; CI, ui_smoke ve smoke_launch ağa çıkmaz.
* `platform.open_url`: Linux'ta uygulama root çalışır (ADR 0042); tarayıcı
  root olarak açılmaz — yetkiyi veren kullanıcının kimliği ve oturum ortamı
  (XDG_RUNTIME_DIR, oturum D-Bus'ı, DISPLAY/WAYLAND_DISPLAY) ile açılır.
  AppImage'ın kütüphane yolları tarayıcıya geçmez. Yalnızca https. Hakkında ve
  lisans penceresindeki bağlantılar da buradan geçer. Root kopyaya artık
  `WAYLAND_DISPLAY` de aktarılır.

Gizlilik: denetim yalnızca GitHub API'sine anonim bir istek atar (kimlik,
istatistik yok); kapatılabilir.

## Doğrulama

t90 (ağsız: sürüm sırası, taslak/ön sürüm, tarih değil numara, ağ hatası,
https dışı reddi, lisans metinleri), ui_smoke (ortam değişkeni, menü), canlı
API ile elle: en son v0.6.1-beta. Pencereler ekran görüntüsüyle kontrol edildi.
Linux'ta root kopyadan tarayıcı açma gerçek pkexec oturumunda **denenmedi**.
