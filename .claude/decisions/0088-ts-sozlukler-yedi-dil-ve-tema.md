# 0088 — Sözlükler Qt Linguist `.ts`; yedi yeni dil; Sistem/Açık/Koyu tema

Tarih: 2026-10-04
Durum: **uygulandı**. İlgili: [0027](0027-cok-dilli-arayuz.md),
[0013](0013-tema-kaldirildi.md), [0012](0012-qt-windows-bulgulari.md).

## 1. Tema — kullanıcı kararı: "Palet + az QSS"

Kullanıcı QSS ile koyu/açık tema istedi. ADR 0013'te sabit renkli QSS
kaldırılmıştı: masaüstü temasıyla çakışıp öğeleri okunmaz yapıyor, Windows'ta
sekme başlıklarını kırpıyordu. Seçenekler sunuldu, kullanıcı **palet + az
QSS**'i seçti:

* Araçlar > Tema: **Sistem / Açık / Koyu**; seçim `settings["theme"]`'de,
  `DISKULTIMATE_THEME` ile zorlanabilir. Sistem = eski davranış (stil sayfası
  yok, masaüstünün stili ve paleti — ilk uygulamada saklanıp geri yüklenir).
* Açık/Koyu = Fusion + `QPalette` (Active/Inactive/Disabled renkleri) + paletten
  türetilen iki satırlık QSS (ipucu kenarlığı, odak çerçevesi). Pencere başına
  QSS yok; kendi çizdiğimiz öğeler zaten `palette_color` kullanıyor.
* Eski kullanılmayan `STYLESHEET`/"diskultimate" teması ve sabitleri silindi.
* "Açık" kaynak metni başka yerde "On" (açık/kapalı) diye çevrili: tema adları
  `trc("tema", ...)` bağlamıyla ayrı girişler.
* ui_smoke: her temada pencere açılır, palet açık/koyu mu, metin zeminden
  ayrışıyor mu, menü işareti ve kayıt; ekran görüntüsü `16-tema-acik.png`,
  `16b-tema-koyu.png`.

## 2. Sözlük biçimi — kullanıcı kararı: "Hepsi .ts'e geçsin"

ADR 0027 `.ts`'i `QTranslator` yüzünden elemişti (çekirdek PyQt import etmez,
`.qm` için `lrelease`). Kullanıcıya `.po` ile devam önerildi; kullanıcı
**bütün sözlüklerin `.ts`** olmasını seçti. Uzlaşma: biçim `.ts`, çalışma
zamanı **değişmedi** — `i18n/ts.py` dosyayı `xml.etree` ile `po.Entry`/
`po.Catalog` modeline okur; `QTranslator`/`.qm` kullanılmaz, katman kuralı
korunur, derleme adımı yok.

| Bizde | `.ts` |
|---|---|
| bağlamsız | `<context><name>DiskUltimate</name>` |
| `trc` bağlamı | `<context><name>…</name>` |
| `msgid_plural` | `<extra-po-msgid_plural>` (`lconvert` kalıbı) |
| çoğul | `numerus="yes"` + `numerusform` |
| fuzzy / boş | `type="unfinished"` |
| bayat | `type="vanished"` |
| `#:` | `<location filename="../../ui/x.py" line=".."/>` |

Çoğul kuralı `.ts`'te yazmaz: `i18n.PLURAL_RULES` (gettext ifadesi; Qt'nin
numerus sayılarıyla aynı — ru 3, zh/ja/ko 1, fr `n > 1`). `po.merge` çoğul
biçim sayısını dilden alır (önce sabit 2'ydi).

Geçiş doğrulaması: en/de `.po` → `.ts` → okuma, 1944 girişin her alanı
(bağlam, kaynak, çoğul, çeviri, fuzzy, bayat, yorum, konum) **aynı**.
Qt'nin kendi araçları (`lconvert`, Linguist) bu makinede kurulu değil;
dosyanın Linguist'te açılışı **burada sınanmadı** — biçim `lupdate` çıktısına
göre kuruldu.

Paketleme: `.spec` `*.ts` toplar; Qt çevirileri (`qtbase_*.qm`) sözlüklerden
türetilir — Çince Qt'de `qtbase_zh_CN.qm` (`qt_i18n.QT_FILE_LOCALE`). AppImage
Qt çevirilerini elle listeden (tr|de|en) değil sözlük klasöründen seçer.

## 3. Yedi yeni dil

fr, it, es, ru, zh (Basitleştirilmiş), ja, ko — 1854 metin her biri. Yedi
alt ajan paralel çevirdi: kaynak Türkçe + gözden geçirilmiş İngilizce,
13 parça, her parçadan sonra doğrulayıcı (yer tutucu, HTML, `&` kısayolu,
baş/son satır sonu, dile göre çoğul biçim sayısı). CJK menü kısayolları
Windows kalıbında (`ファイル(&F)`). Terim sözlükleri:
`.claude/docs/ceviri-sozlukleri/<dil>.md`.

**Anadili konuşan gözden geçirmesi yapılmadı** (README'de yazıyor).

Çeviriler sırasında bulunan kaynak hataları (hepsi düzeltildi):
* `core/resize.py` taşıma özeti yönü çevrilmemiş Türkçe kelimeyle
  ("ileri"/"geri") cümleye koyuyordu — her dilde Türkçe görünüyordu; iki ayrı
  tam cümle oldu, "Bolum {}:" başlığı da `tr()`'ye alındı.
* Dosya gezgini ve silinmiş dosya penceresinde ilk sütun başlığı `"Ad"`
  `tr()`'siz yazılmıştı.
* Disk haritasında dar bloklarda yüzde Türkçe sırayla (`%5`) sabitti.

Ekran görüntüleriyle bakıldı (fr, ru, zh, ja, ko): yazı tipleri (Noto CJK)
doğru, menüler çevrili. Uzun dillerde araç çubuğunun son iki düğmesi taşma
okuna düşüyor ve tablo başlıkları kırpılıyor (Türkçede de kısmen var) —
ayrı iş olarak duruyor.
