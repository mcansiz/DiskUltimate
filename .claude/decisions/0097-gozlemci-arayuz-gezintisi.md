# 0097 — Gözlemci arayüz gezintisi (gerçek uygulama, gerçek disk, zaman)

Tarih: 2026-10-14
Durum: **uygulandı** — VirtualBox win10'da eski kodun hatasını yakaladı,
yeni kodda temiz; GitHub Actions 38609339211'de Windows aygıt / Windows
görüntü / Linux Xvfb üçü de geçti. İlk koşuda ayrıca "Uygula sonrası birim
kilitleri bırakılmıyor" hatasını buldu (worklog 2026-10-14 (2)).
İlgili: [0080](0080-uzun-testler.md), [0021](0021-acik-aygiti-yoklamama-ve-kopyalama-ilerlemesi.md),
[0026](0026-bolum-yoklamasi.md), [0096](0096-tek-form-klonlama.md)

## Kullanıcı isteği

"GitHub'da sanal makinede uygulamanın GUI'sini çalıştırıp tüm özellikleri elle
deneyen bir test yapabilir miyiz? Genelde bu işleri betik dosyası ile yapıyoruz
ve buna benzer hataları bulamıyoruz."

## Tetikleyen hata

win10'da açık diskin sürücü harfleri 3 sn'de bir gidip geliyor, dosya gezgini
her seferinde en başa dönüyordu (worklog 2026-10-14). Hata yalnızca şu üçü
**birlikteyken** görünür: gerçek fiziksel disk açık + Windows + disk yoklama
zamanlayıcısı en az iki tur dönmüş. Mevcut testlerin hiçbiri bu birleşimi
kurmuyordu:

* birim testleri tek bir çağrının çıktısına bakar (zaman yok);
* `ui_smoke` yoklama zamanlayıcısını **bilerek durdurur** ve yalnızca görüntü
  dosyası açar (gerçek disk yok);
* `tests.long.gui` uygulamayı açıp ekran görüntüsü alır ama hiçbir şeye
  tıklamaz ve bir şeyi izlemez.

## Karar

`tests/long/explore.py` — uygulamayı **aynı açılış yolundan** (`main.start()`)
kurar, bir kullanıcı gibi sürer ve **gözler**.

1. **Gerçek ortam.** Gerçek Qt platformu (Windows'ta `windows`, Linux'ta Xvfb
   üzerinde `xcb`), yoklama zamanlayıcısı çalışır. Aygıt kipinde
   `tests.long.device` bir VHD takar, Windows'un **kendi araçlarıyla**
   (Initialize-Disk / New-Partition / Format-Volume) bölümler; Windows harf
   atar ve birimleri bağlar — kullanıcının ortamının birebiri.
2. **Kullanıcı gibi sürme.** Ağaca fareyle (QTest) tıklanır, gezginin yol
   satırına yazılır, menü eylemi tetiklenir. Modal pencereler gerçek `exec_`
   döngüsünde açılır; 100 ms'lik bir sürücü etkin modal pencereyi beklenen
   işlemle karşılar (alanları doldurur, düğmeye basar). **Beklenmeyen pencere
   bir bulgudur**: görüntüsü alınır, metni rapora yazılır, "Vazgeç" ile
   kapatılır.
3. **Beklenen çıktı yerine değişmezler.** Her adımdan sonra uygulama **boşta**
   bekletilir (varsayılan 12 sn ≈ 4 yoklama turu) ve şunlar izlenir:
   * ağaç yeniden kuruldu mu, görünüm tazelendi mi, gezgin sıfırlandı ya da
     klasör değiştirdi mi, günlüğe bir şey yazıldı mı;
   * ağaç seçimi/kaydırması, gezgin klasörü/kaydırması/seçimi, açık diskin
     bağlı bölümleri, kuyruk uzunluğu değişti mi;
   * Windows: `GetLogicalDrives` maskesi (200 ms) ve `WM_DEVICECHANGE` birim
     bildirimleri (Gezgin'in en başa dönmesine yol açan sinyal);
   * yakalanmamış istisna, Qt kritik iletisi, yeni donma raporu.
   Boşta bekleme **yoklamanın gerçekten döndüğünü de** doğrular (en az n-1
   tur); dönmüyorsa adım hata verir — `ui_smoke`'un düştüğü tuzak.
4. **Dış doğrulama.** Uygula sonrası durum işletim sisteminin kendi aracıyla
   karşılaştırılır (Get-Volume + `chkdsk`); görüntü kipinde dosya yeniden
   açılıp okunur.
5. **İzlenebilirlik.** Her adımın ve her karşılanan pencerenin ekran görüntüsü,
   JSON (uzun testlerle aynı biçim, `tests.long.report` okur) ve olay akışlı
   Markdown rapor. Rapor her adımdan sonra yazılır; toplam süre sınırı
   (`--sinir`, `faulthandler`) aşılırsa yığın basılıp çıkılır — test sessizce
   asılı kalmaz.

İş akışı: `.github/workflows/gezinti.yml` — her push'ta Windows aygıt,
Windows görüntü ve Linux (Xvfb) görüntü işi; elle tetiklemede boşta bekleme
süresi seçilir. Çıktılar 30 gün saklanır.

## Neden "elle tıklayan" dış otomasyon değil

pywinauto/UIA gibi dış otomasyon platforma özgüdür ve öğe bulma kırılgandır;
asıl kazanç tıklamanın nereden geldiği değil **gerçek ortam + zaman +
değişmezler**dir. Süreç içi sürme aynı Qt olay yolundan geçer (QTest olayları
gerçek fare olayı gibi işlenir) ve üç platformda aynı kodla koşar. Dış
otomasyon gerekirse ayrı bir adım olarak eklenebilir.

## Güvenlik

Aygıt kipi `tests.long.device.require_ci()` ister (GitHub Actions ya da
`DU_TEST_VM=1`); ana makinede fiziksel diske dokunmaz. Yıkıcı adımdan önce
açık diskin **bu testin taktığı** yol olduğu, sistem diski olmadığı, 4 GiB'tan
küçük ve bilgisinin tam olduğu ayrıca denetlenir. Uygulamanın kendi kapıları
(salt okunur açılış, sistem diski onayı, bağlı bölüm uyarısı) atlanmaz —
sürücü onları kullanıcı gibi yanıtlar; sistem diski onayı beklenmeyen pencere
sayılır ve reddedilir.

## Senaryolar (2026-10-14 (3), kullanıcı: "tüm özellikleri test edelim")

| Senaryo | Kapsam |
|---|---|
| `temel` | gezgin, biçimlendir, Uygula |
| `bolum` | boyutlandır, yeni bölüm, ad, tür, etiket, önyükleme bayrağı, hizalama, düzen penceresi, NTFS denetimi, Uygula; sil → geri al → vazgeç → sil; GPT→MBR→GPT (veri korunur) |
| `yedek` | gezginle dosya aktar, bölüm ve disk yedeği, biçimlendir, bölüme geri yükle (içerik bayt bayt), yeni görüntüye geri yükle, klon, `.dub` aç ve gez, yedek bilgisi |
| `kurtarma` | gezgin: klasör/ad/dışa aktar/sil; silinmiş dosya kurtarma (bayt bayt), imza taraması (zip açılır), bölüm sil → kayıp bölüm tara → geri ekle, boş alan silme, bölümü sıfırla |
| `arayuz` | 10 dil, 3 tema, 8 ikon seti, bilgi pencereleri, onaltılık görünüm, yeni görüntü/VHD, görüntü boyutlandırma, görüntü kapatma, önyükleyici/EFI pencereleri (düğmelere basılmaz) |

Her senaryo kendi diskiyle başlar (görüntü kipinde taze dosya); kritik adım
düşerse yalnızca o senaryonun kalanı atlanır. Dosya seçme pencereleri
kullanıcının yazacağı yolla yanıtlanır (`QFileDialog` statik işlevleri
Windows'ta yerel penceredir, Qt göremez). Ayarlar geçici klasöre yazılır.
Aygıt kipi Linux'ta `scsi_debug` + `sgdisk`/`mkfs`/`mount` (root, Xvfb).
CI: Windows/Linux aygıt × 4 senaryo, Windows görüntü `arayuz`, Linux
görüntü `hepsi` — 10 paralel iş.

Testin bu genişlemede bulduğu hatalar: ADR 0098 (bölüm silme veriyi
siliyordu, imzadan çıkan zip açılmıyordu, fiziksel diskte etiket ve boş alan
silme seçilemiyordu) ve geri yükleme/klon sonrası yazma kipi (ADR 0025).

## Bilinen sınırlar / sonraki adımlar

* macOS aygıt kipi yok.
* Dış otomasyon (UIA) ve video kaydı yok; ekran görüntüleri her adımda.
* Yerel güç eylemi (kapat/uyku) ve ana makineyi etkileyen önyükleyici/EFI
  yazmaları bilerek sınanmaz.
