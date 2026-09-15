# ADR 0022 — Birim kilidi kutukten bagimsizdir, hata metni gercegi soyler

**Tarih:** 2026-09-15
**Durum:** Kabul edildi
**Ilgili:** ADR 0021 (acik aygit kutugu), ADR 0016 (Windows birim kilitleme)

## Baglam

Kullanici, uygulamayi **yonetici olarak** calistirip SD kartin NTFS bolumunden
`sdcard.img.dub` dosyasini silmek istedi. Su hata cikti:

> Yazma reddedildi: Windows bagli birimlere dogrudan yazmayi engeller.
> Birimi cikarin (eject) veya **Yonetici olarak calistirin**.

Kullanici zaten yoneticiydi. Ayni islem Linux'ta sorunsuz calisiyordu.

## Bulgu

Oturum gunlugu (`session-20260915-135924-13908.log`):

```
13:59:32.068  aygit acik kutugune eklendi: \\.\PhysicalDrive1
13:59:32.100  arayuz: Fiziksel disk acildi: \\.\PhysicalDrive1 (YAZMA)
13:59:41.651  disk.write(lba=8452440, size=1915168) — 78 ms
              HATA AccessDeniedError: Yazma reddedildi...
```

Acma ile yazma arasinda **tek bir `win.open_volume` satiri yok**: hicbir birim
kilitlenmemis. Oysa ADR 0016'nin tum amaci buydu — Windows, bagli bir birimin
sektorlerine dogrudan yazmayi ancak birim kilitli/ayrik ise kabul eder.

Neden kilitlenmedi? ADR 0021 ile gelen **acik aygit kutugu** yuzunden. Acilis
sirasi soyleydi:

```
_open_device()            -> aygit acildi
_register_open(info)      -> aygit KUTUGE girdi
_win_lock_volumes()       -> harfleri _win_drive_letters() ile soruyor
                             ...ama o islev artik KUTUKTEKI aygitlarin
                             harflerini atliyor  ->  harf listesi BOS
                          -> hicbir birim kilitlenmedi
```

Yani bir onceki oturumda donmayi cozen degisiklik, yazmayi bozdu. Klasik
etkilesim hatasi: iki dogru kural birbirine dokundu.

Linux'ta sorun cikmamasinin nedeni de acik: orada birim kilidi diye bir adim
yok, bu kod yolu hic calismiyor.

## Karar

### 1. Kilit, harfleri kutuge sormaz

`_win_lock_volumes()` birim harflerini **`info.mounted`** icinden alir. Bu bilgi
listeleme sirasinda zaten toplanmistir; yeniden sormak hem gereksiz hem de —
gorulduğu gibi — kirilgandir. Yol ile acilmis, `mounted` bilgisi olmayan bir
aygit icin son care olarak eski yol kullanilir.

Kural olarak: **acilis sirasindaki hicbir adim, aygitin kutukteki durumuna
bagli olmamalidir.** Kutuk *disaridan* gelen yoklamalari durdurmak icindir.

### 2. Kilit sessiz degil

Hangi birimin kilitlendigi, hangisinin kilitlenemedigi gunluge yazilir;
kilitlenemeyen varsa uyari verilir. Kilit basarisizligi olumcul degildir ama
**sonraki her yazma hatasinin nedenidir**; iz birakmamasi kabul edilemez.

### 3. Hata metni gercek nedeni soyler

`ERROR_ACCESS_DENIED` icin tek bir sabit metin vardi ve her durumda "Yonetici
olarak calistirin" diyordu. Yeni metin duruma gore konusur:

- kilitlenemeyen birim varsa: hangisi oldugunu ve o birimi kullanan programlari
  kapatmak gerektigini soyler,
- hic birim kilitlenmemisse: diski kapatip yeniden acmayi onerir,
- kilitli birimler varsa: yazilan alanin baska bir birime ait olabilecegini
  soyler.

Yanlis yonlendiren tavsiye, tavsiye vermemekten kotudur: kullanici yonetici
oldugu halde "Yonetici olarak calistirin" okudu.

### 4. Basarisiz silme "silindi" diye raporlanmaz

`delete_selected` hata kutusunu gosterdikten sonra bile islem gunlugune
"N oge silindi" yaziyordu. Artik basarililar sayilir:
`"2/3 oge silindi — 1 oge silinemedi"`.

## Dogrulama

- `t21_birim_kilidi_kutukten_etkilenmez`: aygit **kutukteyken** kilitleme
  calistirilir; `_win_drive_letters()` bos donse bile iki birimin de
  kilitlendigi, `FSCTL_LOCK_VOLUME` ve `FSCTL_DISMOUNT_VOLUME` cagrildigi
  dogrulanir. Gercek diske dokunulmaz.
- `tests.run_all` 21/21 · `diag_check` 13/13 · `platform_check` 0 bulgu ·
  `ui_smoke` tamam.

## Not — ayri bir verimlilik sorunu

Basarisiz olan yazma `size=1915168` idi: bolumun **tum `$Bitmap` alani**
(58.45 GB / 4 KB kume / 8 bit = 1 915 168 bayt). NTFS yazicisi her tahsis ve
her serbest birakma icin bitmap'in tamamini yeniden yaziyor. Dogruluk sorunu
degil ama fiziksel diskte her islemde ~1.9 MB gereksiz yazma demek. Ayri bir
is olarak not edildi.
