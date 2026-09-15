# ADR 0023 — Yonetici / root yetkisinin istenmesi

**Tarih:** 2026-09-15
**Durum:** Kabul edildi
**Ilgili:** ADR 0014 (fiziksel disk destegi), ADR 0022 (birim kilidi)

## Baglam

Fiziksel disk erisimi Windows'ta Yonetici, Linux'ta root yetkisi ister. Yetki
yoksa diskler listede gorunur ama acilamaz; kullaniciya yalnizca bir hata
gosteriliyordu. Cozum kullanicinin uygulamayi kapatip "Yonetici olarak
calistir" ile yeniden acmasiydi — uygulama bunu kendisi yapabilecekken.

Kullanici sordu: "uygulama acilirken yonetici yetkisi istemesi gibi bir sey
yapabilir miyiz?"

## Karar

Evet, ama **kosulsuz degil.**

### Neden her acilista istenmiyor

CLAUDE.md'nin acik kurali: *"Goruntu dosyalarinda root/sudo gerekmez."*
Kullanicilarin buyuk bolumu `.img` / VHD dosyalariyla calisir. Her acilista
yetki istemek:

- gereksiz yere tam yetkiyle calisan bir **disk aracı** demektir — bu araca
  yanlislikla verilen bir komut makineyi acilamaz hale getirebilir,
- yetkisi olmayan kullanicinin uygulamayi hic kullanamamasina yol acar,
- kullaniciyi UAC penceresini dusunmeden onaylamaya alistirir.

En az yetki ilkesi burada da gecerlidir.

### Ne zaman isteniyor

1. **Acilista, yalnizca gercekten engellenmisse.** Ilk disk taramasi bittikten
   sonra `info_complete=False` olan bir disk varsa — yani yetki eksikligi
   *olculmusse* — bir kez sorulur. Disk yoksa veya yetki zaten varsa hicbir sey
   sorulmaz.
2. **Fiziksel disk acilmaya calisilip yetki reddedilince.** Hata kutusu artik
   cozumu de sunar.
3. **Kullanici isteyince:** Disk menusu > "`<Yonetici|root>` olarak yeniden
   baslat...". Zaten yetkiliyken pasiftir, nedeni ipucunda yazar.

### Nasil isteniyor

`core/platform.py` icinde (platform farki baska yere sizmaz):

| Platform | Yontem |
|---|---|
| Windows | `ShellExecuteW(..., "runas", ...)` — isletim sisteminin UAC penceresi |
| Linux | `pkexec env DISPLAY=... XAUTHORITY=... python3 main.py` — polkit penceresi |
| macOS | `osascript ... with administrator privileges` |

`pkexec` ortami temizledigi icin grafik oturum degiskenleri elle verilir; yoksa
yukseltilen surec ekrana baglanamaz.

Yukseltme **yeni bir surec baslatir**; eski surec kendini kapatir. Kapanmadan
once acik oturumlar kapatilir ve suren disk taramasi beklenir: iki kopya ayni
aygita dokunmamalidir (ADR 0021).

### Kullanici her zaman durumu gorur

Durum cubugunda surekli bir gosterge durur: `🔑 Yonetici` veya
`Normal kullanici`. Ipucunda ne yapilabilecegi yazar. "Sistem bilgisi"
penceresine de bir **Yetki** satiri eklendi.

## Guvenlik siniri

Yetki yukseltme, `physical.py` icindeki alti koruma katmanini **degistirmez**.
Yonetici olarak calismak yalnizca aygiti *acabilmeyi* saglar; yazma icin
`readonly=False` + `confirm=True`, sistem diski icin ayrica `allow_system=True`
ve arayuzde disk adinin yazilmasi gerekir. Yukseltme bir kapiyi acar, otekileri
acmaz.

## Otomatik kosumlar

Modal bir pencere duman testini kilitlerdi.
`DISKULTIMATE_NO_ELEVATION_PROMPT=1` acilis teklifini bastirir; `tests/ui_smoke`
bunu ayarlar.

## Dogrulama

`t22_yetki_yukseltme`: durum sorgusu, yeniden baslatma komutunun yorumlayici +
betik olmasi, betik yolu yokken (`python -c`) yukseltmenin **sunulmamasi**,
zaten yetkiliyken cagrinin hicbir sey baslatmamasi. Gercek bir UAC/polkit
penceresi acilmaz.

Arayuz iki durumda da elle denetlendi (ekran goruntusu): yetkiliyken menu pasif
ve durum `🔑 Yonetici`; yetkisizken menu etkin ve durum `Normal kullanici`.
