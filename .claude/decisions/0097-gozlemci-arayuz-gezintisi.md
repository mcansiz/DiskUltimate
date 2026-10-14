# 0097 — Gözlemci arayüz gezintisi (gerçek uygulama, gerçek disk, zaman)

Tarih: 2026-10-14
Durum: **uygulandı** — görüntü kipi ana makinede ölçüldü; aygıt kipi GitHub
Actions `windows-2022` üzerinde (sonuçlar worklog'da)
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

## Bilinen sınırlar / sonraki adımlar

* İlk sürüm tek bir yıkıcı akış içerir (biçimlendir + Uygula). Özellik listesi
  adım adım genişletilecek (yeni bölüm, boyutlandırma, yedek/geri yükleme,
  klon, silme, kurtarma taramaları, dil/tema değişimi).
* Linux aygıt kipi (scsi_debug + udisks bağlama) ve macOS henüz yok.
* Video kaydı yok; Windows makinesinde ffmpeg varlığı ilk koşuda ölçülür.
