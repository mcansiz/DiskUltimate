# 0078 — Yetkili kopya: dosya sahipligi ve kullanicinin dizinleri

Tarih: 2026-10-03
Durum: **uygulandi** — t78 (taklit edilmis yetkili kullanici, kaydedilen
chown cagrilari). Gercek root ile dogrulama kullanicida.
Ilgili: [0042](0042-acilista-kosulsuz-yetki.md), [0040](0040-paketlenmis-kopya-kullanici-veri-dizinine-yazar.md),
[0050](0050-linux-dagitimi-appimage-ve-musl.md), CLAUDE.md "yetkili kopyada
uretilen dosyalarin sahipligi geri verilir".

## Olculen sorunlar

1. **Tanilama gunlukleri root'a ait kaliyordu.** `restore_owner` goruntu,
   yedek ve disa aktarilan dosyalarda vardi; oturum/cokme gunlugu, donma
   raporu, `app-*.log` ve ayar dosyasinda yoktu. Projedeki `.claude/logs`
   altinda Eylul'den beri root'a ait dosyalar vardi.
2. **Klasorler de root'a ait kaliyordu.** `~/.local/state/DiskUltimate/
   logs/freeze` 2026-09-28'den beri root'a aitti: klasoru ilk olusturan
   yetkili kopyaydi, normal kopya oraya **hic donma raporu yazamiyordu**.
3. **Yetkili kopyanin ayarlari /root'a gidiyordu** (koddan): `config_dir`
   ve `user_data_dir` `~` ile hesaplaniyor, pkexec `HOME=/root` yapiyor.
   Uygulama acilista hep yetki istedigi icin (ADR 0042) secilen dil ve
   ikon seti fiilen root'un ayarlarina yaziliyordu.

## Karar

* `platform.make_user_dirs(path)`: `makedirs` + yalnizca **yeni acilan**
  basamaklari yetkiyi veren kullaniciya (`PKEXEC_UID`/`SUDO_UID`) verir.
  Gunluk, donma, gecici alan, veri ve ayar klasorleri bununla olusur.
* Dosyalar olusur olusmaz `restore_owner`: oturum ve cokme gunlugu, donma
  raporu (`_write`, yalnizca yeni dosyada), `app-*.log`, `settings.json`,
  yetki el sikisma dosyasi.
* `platform.reclaim_tree(path)`: yetkili kopya acilirken **yalnizca kendi
  gunluk klasorunde** root'a ait kalmis ogeleri bir kez kullaniciya cevirir
  (sembolik bag izlenmez, `lchown`). Eski surumlerin biraktiklari kendiliginden
  duzelir; gunluge sayisi yazilir.
* `config_dir` / `user_data_dir` `platform.user_home()` ile: yetkili kopyada
  da kullanicinin evi. Ayarlar iki kopyada ortaktir.
* Windows'ta degisiklik yok (`invoking_user` hep None).

## Dogrulama

* t78: yeni basamaklar cevrilir, var olanlar cevrilmez; `reclaim_tree`
  her ogeyi bir kez cevirir, baglanti izlenmez; donma raporu bir kez;
  `settings.json`; ayar/veri klasoru sahte `HOME`a ragmen gercek evde.
  Ilk surumde `reclaim_tree` alt klasorleri iki kez isliyordu — test yakaladi.
* Gercek root: kullanici bir kez yetkiyle acip
  `find ~/.local/state/DiskUltimate .claude/logs -user root` bos donmeli.
