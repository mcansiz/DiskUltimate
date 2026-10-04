# 0080 — Uzun (derin) testler: GitHub Actions'ta senaryo zinciri

Tarih: 2026-10-04
Durum: **planlandi** — Asama 0 basliyor.
Ilgili: [0079](0079-github-actions-ci-ve-surum.md), [0073](0073-goruntu-konumu-tasima-kesintisi-ext4-ozellikleri.md)
(dosyanin tek parca okunmasi), `tests/fs_matrix.py`, `tests/resize_matrix.py`.

## Amac

Uygulamanin gercek kod yolunu (`DiskSession` + kuyruk) her bolum tipi ve
desteklenen her dosya sistemi icin, buyuk verilerle, uzun sureli kosturmak;
sonuc raporuna is bitince bakmak.

## Kullanici kararlari (2026-10-04)

1. **Siklik:** zamanlanmis kosu yok; eklenen ozellige gore **istendiginde**
   elle baslatilir (workflow_dispatch). Surum etiketi uzun teste bagli degil.
2. **Buyuk dosya:** 5 GB (FAT32'nin 4 GB sinirini asar; FAT32'de RED
   beklenir).
3. **CI makineleri VM sayilir:** her kosudan sonra silinen gecici
   makinelerde cekirdek baglama (`sudo mount -o loop`), loop aygiti / takili
   VHD uzerinden fiziksel disk yolu kosabilir (CLAUDE.md test kurali eki).
4. **Hata olunca otomatik GitHub issue** acilir (rapor + tekrar komutu).

## Senaryo zinciri (dosya sistemi x MBR/GPT[/VHD])

bolumle -> bicimlendir -> tohumlu veri (cok kucuk dosya, derin klasor,
Unicode ad, 1 GB + 5 GB dosya, %95 doluluk) -> SHA defteri -> buyut ->
kucult (en aza) -> tasi -> sil/yeniden adlandir/yeniden doldur ->
bolum kopyala / disk klonla / .dub yedek + geri yukle -> MBR<->GPT ->
bos alan sil -> (FAT/exFAT) silinmis dosya kurtarma, kayip bolum taramasi.
Her adimdan sonra butun dosyalar ozetle denetlenir; sure, MB/s, en yuksek
bellek kaydedilir.

## Dogrulama katmanlari

* Linux: kendi okuyucumuz + fsck araclari + **cekirdek ile baglama**.
* Windows: VHD `diskpart` ile takilir, Windows'un baglamasi + `chkdsk`.
* macOS: `hdiutil attach`, `fsck_hfs` / `fsck_exfat` / `fsck_msdos`.

## Asamalar

0. **Akis (parca parca) yazma** — yazicilar dosyayi tek parca bellege
   aliyor; 5 GB dosya 5 GB RAM ister (macOS runner 7 GB). Arayuzden buyuk
   dosya kopyalamaya da dogrudan fayda.
1. `tests/long/` senaryo motoru (tohum, ozet defteri, JSON rapor,
   `quick`/`full` profil, yerelde tekrar komutu).
2. Linux is akisi + cekirdek baglama dogrulamasi + issue acma.
3. Windows (VHD, chkdsk, Windows'un baglamasi).
4. macOS — asagidaki "deneysel"i kaldirma olcutleriyle.
5. Fiziksel disk yolu: Linux loop aygiti, Windows takili VHD, macOS
   `hdiutil attach -nomount` aygiti.

## macOS neden "deneysel" ve ne zaman kalkar

CI'da gecen testler disk GORUNTUSU uzerindeki saf Python cekirdegi sinadi;
o kod uc platformda aynidir. macOS'a OZGU kod hic kosmadi: yetki alma
(`osascript`), disk listeleme (`diskutil list/info`), ham aygit acma,
`diskutil rescan`, baglama/ayirma; arayuz Cocoa ile hic acilmadi (deneme
acilisi `minimal` eklentisiyle). Kaldirma olcutleri (hepsi CI'da olculur):

* `hdiutil attach -nomount` ile takilan goruntu gercek `/dev/diskN` olarak
  listelenir, salt okunur acilir, kuyrukla yazilir, `fsck_*` temiz;
* arayuz `cocoa` ile acilir, `screencapture` ekran goruntusu artifact olur;
* yetki alma komutu (osascript) uretimi test edilir. Parola penceresine
  tiklanamayacagi icin GERCEK yetki alma CI'da olculemez — bu tek madde
  bir Mac'te elle denenene kadar acikca yazili kalir.

Imzasizlik (Gatekeeper uyarisi) bir dagitim konusudur, dogruluk degil;
"deneysel" etiketinin nedeni sayilmaz, README'de ayrica belirtilir.
