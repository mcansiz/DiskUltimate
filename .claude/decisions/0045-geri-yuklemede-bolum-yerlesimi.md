# 0045 — Geri yuklemede bolum yerlesimi; not yalnizca yedek alinirken

Tarih: 2026-09-28
Durum: kabul edildi (Windows VM'de sinandi; Linux VM ve fiziksel disk bekliyor)
Ilgili: ADR 0032 (tek pencereli yedekleme), ADR 0030 (NTFS boyutlandirma)

## Sorun

Kullanici DiskGenius'un "Goruntu Dosyasindan Diske Geri Yukle" ve "Diski
Goruntu Dosyasina Yedekle" pencerelerini ornek verdi ve iki sey istedi:

1. **Not geri yuklemede yazilmamali.** Pencere geri yukleme kipinde not
   duzenleyicisini ve "Notu kaydet" dugmesini gosteriyordu; geri yukleyen
   kullanici yedek dosyasini degistirebiliyordu. DiskGenius'ta "Aciklama"
   yalnizca yedek alma penceresinde yazilir, geri yuklemede bilgi olarak
   gorunur.
2. **Hedef disk yeniden boyutlandirilabilmeli.** DiskGenius'ta "Bolumleri
   Yonet" yedekteki bolumleri hedef diske gore buyutur/kucultur. Bizde geri
   yukleme bayt bayt yaziyordu: hedef kucukse reddediliyor, buyukse fazlasi
   bos kaliyor ve GPT yedek basligi eski disk sonunda kaliyordu.

## Karar

### Not
Geri yukleme kipinde not duzenleyicisi gizlenir; yedegin notu bilgi alaninda
salt okunur "Aciklama" satirinda durur. "Notu kaydet" kaldirildi.
`clone.write_remark` cekirdekte kalir (t39 onu sinar) ama arayuzden
cagrilmaz.

### Yerlesim (`core/restoreplan.py`)
- `RestoreLayout` yedekteki her bolum icin eski/yeni (baslangic, sektor) ve
  dosya sistemi sinirlarini tasir. Sinirlar **yedegin icinden** okunur
  (`fs_resize_info`), `backup_preview` onizlemeyle birlikte arka planda uretir.
- Hazir duzenler: yedekteki gibi (sigiyorsa varsayilan), gerektigi kadar
  kucult (sigmiyorsa varsayilan), son bolumu genislet, diske orantili yay.
  Tek bolum `ResizePartitionDialog` ile surukleyerek boyutlandirilir.
- Tanimlanamayan dosya sistemi ("ham" — BitLocker da boyle gorunur)
  **kucultulmez**; ext gibi boyutlandiramadiklarimizin boyutu sabittir, yeri
  degisebilir.
- Uygulama sirasi: onyukleme alani -> bolumler yeni yerine (kucultulen
  bolumde FS bellekteki yazma katmaninda kucultulur, yedek salt okunurdur) ->
  tablo hedefe gore yeniden yazilir -> buyutulen bolumde FS hedefte buyutulur,
  yalnizca yeri degisende onyukleme sektorundeki bolum konumu duzeltilir.
- Yerlesim degismemis ve hedef ayni boyuttaysa eski bayt bayt yol kullanilir
  (bolumler arasi bosluklar dahil birebir kopya).
- Yeni goruntuye geri yuklerken goruntunun **boyutu** secilir.
- Bolum yedegi daha buyuk bolume yazilinca FS bolumu doldurur ve gizli
  sektor / PartitionOffset hedef bolume gore duzeltilir (eskiden duzeltilmiyordu:
  baska konumdaki bolume yazilan yedek Windows'ta baglanamazdi).

## Sinirlar / bilinen eksikler
- Kucultmede bellekteki katman yalnizca yazilan meta veriyi tutar; NTFS'te
  sondan tasinan kumeler de buraya duser. Cok dolu buyuk NTFS'i cok kucultmek
  bellek ister.
- Bolumler arasindaki, bolume ait olmayan bosluklardaki veri yeni yerlesimde
  kopyalanmaz (DiskGenius "Tum dosyalar" kipi de boyle).
- Bolum yedegini **daha kucuk** bolume yazmak hala reddedilir.
- Fiziksel diske yerlesimli geri yukleme VM'de sinanmadi (bkz. worklog).
