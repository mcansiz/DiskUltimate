# 0065 — UDF yazma (saf Python)

Tarih: 2026-09-29
Durum: **uygulandi** — Windows 10'un kendi UDF surucusuyle iki yonlu
dogrulandi. macOS ve Linux cekirdegi ile sinanmadi.
Ilgili: [0063](0063-udf-okuma.md), [0064](0064-udf-bicimlendirme.md)

## Kapsam

`core/udfwrite.py::UdfWriter` — tek **fiziksel** bolum + ayrilmamis alan
bitmap'i + ustune yazilabilir erisim. Sabit disk/USB UDF'si boyledir (bizim
bicimlendiricimiz, mkudffs `-m hd`, Windows). Yedekli (CD-RW), VAT (CD-R) ve
metadata bolumlu (2.50+) birimler acik gerekceyle salt okunur.

Islemler: `mkdir`, `write_file` (uzerine yazma), `remove` (ozyinelemeli;
sabit bagda yalnizca FID, akis dizini/EA girisleri de serbest), `rename`
(ad ya da yol: klasorler arasi tasima, ".." FID'i guncellenir), `flush`.
`filesystem.UdfAccess` yazilabilir; arayuz yolunda islem basina flush.

## Kurallar (olculerek)
* UDF 2.00+ EFE, oncesi FE; mevcut giris yeniden yazilirken **kendi turu**
  korunur.
* Kucuk veri gomulu (512 B blokta <= 296 bayt), buyuk veri short_ad;
  girise sigmayan kapsamlar (512 B blokta 37'den fazla) **AED zinciri**.
* FID'ler blok sinirini gecebilir — Windows'un kendi yazdigi dizinde 403
  FID'in 49'u geciyordu. FID etiket konumu FID'in basladigi blok; ICB
  uygulama alani benzersiz kimligin alt 32 biti.
* **Windows silmede FID'i sikistirmaz**, "silindi" bayragiyla birakir (ICB'si
  serbest blogu gosterebilir). Yazici bunlari ad aramasinda yok sayar,
  dizini yeniden yazarken atar.
* Dizine ekleme artimli: yalnizca son blok(lar) yazilir; buyume bitisik
  uzatilamazsa dizin bitisik yeni bolgeye tasinir, o da olmazsa parcali +
  AED. (Ilk surum her eklemede tum dizini yeniden yaziyordu ve CRC'yi bit
  bit hesapliyordu: 5000 dosyalik dizin dakikalar surdu — tablo tabanli
  CRC ve artimli ekleme ile duzeltildi.)
* LVID degisiklik boyunca "acik", flush'ta "kapali"; bos alan, dosya/dizin
  sayilari, sonraki benzersiz kimlik.
* Yazma reddedilir: LVID acik (temiz kapatilmamis), alan korumasi, erisim
  turu ustune yazilabilir degil, bitmap yok.

## Dogrulama
* `tests/run_all._udf_denetle` (Linux'ta udf fsck yok): her giris/FID/AED
  etiketinin saglama + CRC + konumu, kokten ulasilan bloklarla bitmap'in
  **birebir** esitligi, dizin bag sayisi, LVID sayaclari.
* Stres: 6M-40M, 512/4096 blok, 2500-4000 islem, dolu birim, AED'li
  dosyalar, klasor tasima, ozyinelemeli silme — denetim her asamada temiz.
* 7z: AED'siz dosyalar birebir; AED'li dosyalari 0 bayt cikariyor (7z'nin
  UDF modulu AED'yi izlemiyor; Windows okuyor — asagida).
* **Windows 10:** yazicimizin urettigi disk (2499 dosya, 5'i AED'li,
  Unicode/Turkce adlar, tasinmis klasor) -> Windows hepsini SHA-1 birebir
  okudu, ustune 100 dosya yazdi/sildi/adlandirdi/kopyaladi, Healthy.
  Windows'un degistirdigi birim `_udf_denetle`den gecti; ustune bizim
  yazmamiz (50 dosya, silme, adlandirma, 300 silme) da gecti; Windows son
  hali (2303 dosya) tekrar birebir okudu, Healthy.
* t64.
