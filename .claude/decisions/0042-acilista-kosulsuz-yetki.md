# 0042 — Acilista kosulsuz yetki, uretilen dosyalarin sahipligi geri verilir

Tarih: 2026-09-21
Durum: kabul edildi — **ADR 0023'un yerini alir**

## Sorun

ADR 0023 en az yetki ilkesini uyguluyordu: yetki yalnizca bilgisi okunamayan
gercek bir disk varsa, disk acilirken reddedilince ya da kullanici menuden
isteyince sorulurdu. Kullanicinin degerlendirmesi: "root olmadan zaten pek
bir ise yaramiyor" — her acilista cikan Evet/Hayir penceresi fazladan bir tik
disinda bir sey uretmiyordu.

## Karar

1. **Yetki acilista, pencere acilmadan ve kosulsuz istenir.** `main.py`
   `ui/startup.elevate_at_startup()` cagirir; yetkili kopya acilinca acilis
   kopyasi kapanir. Bekleme ADR 0039'daki el sikismayla yapilir (pkexec
   ebeveynine bakar, bu yuzden acilis kopyasi hemen olmemelidir).
2. **Yetki verilmezse uygulama yine acilir.** Goruntu dosyalariyla calismak
   icin yetki gerekmez; aci bir "yetkin yok, kapaniyorum" davranisi araci
   gereksiz yere kullanilamaz kilardi. Ayni oturumda ikinci kez sorulmaz
   (`MainWindow.mark_elevation_asked`), ama bir diski **acmaya calisirken**
   cikan teklif durur: orada kullanici bir sey yapmaktadir.
3. **Cikis kapilari var**: `--no-root` (tek seferlik),
   `DISKULTIMATE_AUTO_ROOT=0` (kalici), `DISKULTIMATE_NO_ELEVATION_PROMPT=1`
   (duman testi ve ekran goruntusu uretimi gibi otomatik kosumlar). pkexec
   yoksa yetki istenmez, uygulama dogrudan acilir.
4. **Uretilen dosyalarin sahipligi geri verilir.** Root olarak calisan bir
   arayuzun urettigi her dosya root'a ait olur; kullanici kendi ev dizinine
   aldigi yedegi sonradan silemez, acamaz. pkexec `PKEXEC_UID`, sudo
   `SUDO_UID` birakir; `platform.restore_owner()` bu numarayi kullanarak
   sahipligi cevirir. Cagrildigi yerler: goruntu olusturma (`image`,
   `vdisk`), yedekleme (`clone`), dosya disa aktarma (`filesystem`, `fat`,
   `exfat`) ve kurtarma (`recovery`).

## Bedeli (bilerek kabul edildi)

- En az yetki ilkesinden vazgecilir: goruntu dosyasiyla calisan kullanici da
  parola sorusu gorur.
- Root GUI'ye izin vermeyen bir masaustunde yetkili kopya acilamayabilir;
  bu durumda (2) sayesinde yetkisiz kopya acik kalir.
- Root arayuzun actigi dosya pencereleri kullanicinin degil root'un ev
  dizininde baslar. Sahiplik duzeltmesi dosyalari kurtarir ama bu rahatsizlik
  surer.

## Dogrulandi (ana makine)

- Gercek kosum: `./dist/DiskUltimate` -> 3,5 sn icinde root kopya acildi,
  acilis kopyasi el sikismadan sonra kapandi, fiziksel disk salt okunur
  acildi (gunluk: `~/.local/state/DiskUltimate/logs/runtime/`).
- Sahiplik: `pkexec` altinda uretilen 8 MiB goruntu `uid=1000 gid=1000`
  cikti (root degil).
- `tests.ui_smoke`: basarili/basarisiz yukseltme donusu ve uc cikis kapisi;
  yetkisiz kopyada `restore_owner` hicbir seye dokunmuyor.
