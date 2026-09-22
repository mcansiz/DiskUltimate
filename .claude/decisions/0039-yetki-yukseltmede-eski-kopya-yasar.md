# 0039 — Yetki yukseltmesinde eski kopya, yenisi acilana kadar yasar

Tarih: 2026-09-21
Durum: kabul edildi

## Sorun

Linux'ta paketlenmis (PyInstaller) kopya acilirken "root olarak yeniden
baslatilsin mi?" diye soruyor, kullanici **Evet** deyince uygulama
kapaniyor ve **bir daha acilmiyordu**. Hicbir hata gorunmuyordu; tanilama
gunlugu de son satir olarak yalnizca "root olarak yeniden baslatiliyor"
yaziyordu.

Neden: `relaunch_elevated()` pkexec'i `Popen` ile baslatip hemen
`QApplication.quit()` cagiriyordu.

**pkexec yetkilendirmeyi kendi EBEVEYNINE bakarak yapar** — polkit oznesi
`getppid()` ile bulunur. Ebeveyn oncesinde olurse:

- ebeveyn `1`'e dusmusse pkexec "Refusing to render service to dead parents"
  deyip ciker;
- ara bir surec toplayici (systemd --user) varsa ozne **yanlis** cozulur,
  parola penceresi hic acilmaz ve pkexec sonsuza dek bekler.

Ana makinede olculdu: ebeveyn hemen kapaninca pkexec root olarak asili kaldi
(dakikalarca, hicbir cikti vermeden); ebeveyn yasarken ayni komut calisti.

Ikinci kusur da ayni yerdeydi: `Popen` "baslatildi" sayildigi icin
yetkilendirme reddedilse bile kullaniciya **hicbir sey soylenmiyordu**.

## Karar

Yukseltme artik bir **el sikismadir**: eski kopya, yenisi "acildim" diyene
kadar acik kalir.

- `core/platform.relaunch_elevated()` bool yerine bir `ElevatedLaunch`
  tutamaci dondurur. `poll()` uc durum verir: `bekliyor`, `basladi`, `hata`.
- Yeni kopya, penceresi gorundugu anda `signal_elevated_ready()` ile
  ortamdan gelen dosyayi yazar (`DISKULTIMATE_HANDOFF`). Degisken okunurken
  silinir; alt surecler bildirimi devralmaz.
- Arayuz (`main_window._await_elevated`) bu sirada belirsiz bir ilerleme
  penceresi gosterir ve 250 ms'de bir yoklar. Bekleme arayuzu kilitlemez
  (CLAUDE.md: arayuz is parcaciginda suresi ongorulemeyen is yapilmaz).
  `basladi` gorununce bu kopya kapanir — iki kopya ayni aygita dokunmaz
  (ADR 0021).
- pkexec bildirimsiz cikarsa **hata gosterilir**: 126 = parola penceresi
  iptal edildi, 127 = polkit reddetti, digerleri cikis koduyla birlikte.
  Yetkili kopyanin ciktisi dosyaya alinir (boruya degil: dolan bir boru
  saatlerce calisan kopyayi kilitlerdi) ve hatanin sonuna eklenir.
- Windows/macOS degismedi: yetki penceresini isletim sistemi yonetir ve
  sonucu zaten esanlamli bildirir; orada `poll()` dogrudan `basladi` der.

## Alternatifler

- **Once kapan, `sh -c` ile pkexec'i baslat.** Ara kabuk yasadigi icin
  pkexec calisirdi, ama yetkilendirme reddedilince kullanici yine bos
  ekranda kalirdi: hatayi gosterecek kimse olmaz.
- **Ebeveyni kisa bir sure (1 sn) yasat.** Yaristan kurtarirdi ama parola
  penceresi acikken kullanici 1 sn'den uzun surer; ayrica sonucu yine
  ogrenemezdik.
- **`sudo`/`su` ile terminalden yukselt.** Grafik oturumda parola sorulacak
  yer yoktur.

## Sonuc

Ana makinede olculdu: pkexec ebeveyn yasarken calisiyor (`uid=0`, `DISPLAY`
tasindi), yeni kopya bildirimi yaziyor, bekleyen kopya `basladi` gorup
kapaniyor. Denetim: `tests.run_all` icindeki `t22_yetki_yukseltme` el
sikismayi ve cikis kodu -> mesaj esleme dogrular; gercek bir polkit penceresi
acmaz.
