# 0074 — Eskimis boyut siniri; NTFS bicimlendirici kume hatalari ve onarim

Tarih: 2026-10-01
Durum: **uygulandi** — Linux'ta goruntu dosyalariyla (ntfs-3g araclari
bagimsiz dogrulayici), Windows'ta VBox win10'da `run_all` + `ui_smoke`.
Ilgili: [0047](0047-sinirlar-arka-planda-ve-aygit-kilidi.md) (sinir servisi),
[0049](0049-ortak-bolum-duzenleme-modeli.md), [0030](0030-ntfs-saf-python-boyutlandirma.md).

## 1. Kucultmede dolu alanin altina inilebiliyordu

Kullanici: 3,27 GB dolu NTFS (gercek en az 3,44 GB) pencerede 3,33 GB'a
planlanabildi.

Neden: `FsLimitsService` sonucu (aygit, no, baslangic, boyut) anahtariyla
onbellege alir. Bolum 7,13 GB'a buyutulunce sinir bos haliyle hesaplandi;
sonra 3,45 GB dosya eklendi — anahtar degismedigi icin eski en az boyut
kaldi. Dosya gezgininin `contentChanged` sinyali yalnizca doluluk
gostergesini tazeliyordu.

Veri riski yoktu: Uygula aninda cekirdek siniri diskten yeniden hesaplayip
reddediyor ("3.44 GB altina inemez") — kullanicinin diskinin kopyasinda
denendi. Sorun imkansiz adimin kuyruga girebilmesiydi.

Karar (uc katman):
* `FsLimitsService.forget(oturum, no)`; gezgin degisikliginde cagrilir ve
  sinir arka planda yeniden hesaplanir. Unutulan anahtar icin suren hesabin
  sonucu atilir (hesap degisiklikten once baslamis olabilir).
* `MainWindow._recheck_shrink`: kuculen ve sinirini dosya sistemi veren
  bolum icin kuyruga yazmadan once sinir **diskten** yeniden olculur; altina
  inen adim kuyruga girmez, mesaj en az / dolu / istenen boyutu soyler.
  Gezgin disi degisiklikler (baska program, ayni aygit) icin de gecerli.
  Bedel: kucultmede kisa bir "sinirlar okunuyor" penceresi (NTFS'te bitmap
  taramasi).
* Boyutlandirma penceresi "Dolu: X · Sinirlar: en az Y · en cok Z" gosterir.
  ext'te en az < dolu olabilir (grup basina yonetim alani kuculunce azalir);
  eski "yeni boyut kullanilan alandan kucuk" uyarisi bunun yerine bunu
  aciklar. Ayrica "ileri/geri" kelimesi cevrilmeden metne konuyordu — iki
  ayri cumle oldu.

## 2. NTFS bicimlendirici hatalari

`ntfsresize --info` kendi bicimimizi "inconsistent" sayiyordu; kume
boyutu secilince birim hic acilmiyordu.

| Hata | Etki | Duzeltme |
|---|---|---|
| Bitmap'te kume 0-3 sabit dolu, `$Boot` 8 KiB | 4 KiB kumede 2-3 sahipsiz dolu; ntfsresize calismaz, chkdsk duzeltmek ister | yalnizca `$Boot`'un kumeleri (`ceil(8192/kume)`) |
| `$MFT` kume 4'te, `$Boot` 1 KiB kumede 0-7 | 512 B / 1 KB kumede onyukleme alani `$MFT`'nin ustune yaziliyordu | `$MFT` en erken `$Boot`'tan sonra (4 KiB'te yerlesim ayni) |
| `$AttrDef` tek kume (2560 bayt) | 4 KiB alti kumede kisa; ntfs-3g "unexpected length" | gereken kume sayisi |
| Onyukleme 0x44 (dizin kaydi boyu) her zaman `1` | 4 KiB alti kumede okuyucu dizin kaydini kume boyunda sanip dizinleri okuyamiyordu | `4096 // kume` (MFT alani 0x40 ile ayni kural) |

Sonuc: 512 B – 64 KiB her kume boyutunda ntfsresize temiz; ntfs-3g'nin
yazdigini biz, bizim yazdigimizi (300 dosyalik INDX'li klasor dahil) ntfs-3g
okuyor. 512 B / 1 KB / 2 KB arayuzde secilebiliyordu ve **daha once hic
calismiyordu**.

## 3. Var olan birimler: dar onarim

`ntfsresize.repair_orphan_low_clusters`: kume 0-3'te dolu ama `$Boot`'a ait
olmayan bit varsa **butun MFT** taranir, hicbir kaydin sahiplenmedigi
kumeler bosaltilir. Aday yoksa (Windows'un ya da yeni bicimlendiricinin
birimi) MFT taranmaz, hicbir sey yazilmaz. Genel "sahipsiz kume" onarimi
(chkdsk) bilincli olarak yapilmadi: sahiplik hesabinda bir eksik, kullanilan
kumeyi bos gosterip veri bozar.

* `ntfs_resize` her boyutlandirmada bunu calistirir (zaten yaziyor).
* `ntfs_repair(view)` tek basina cagrilabilir.
* Kullanicinin diskinin kopyasinda: bolum 2 ve 3'te 2'ser kume, bolum basina
  tek sektor degisti, 11 dosya birebir, ntfsresize temiz. **Gercek dosyaya
  uygulanmadi**: uygulama (root) goruntuyu acik tutuyordu; ayni dosyaya iki
  yerden yazilmaz (ADR 0021).

## Sinanmayan
* Windows chkdsk ile dogrulama (misafirde yonetici yok).
