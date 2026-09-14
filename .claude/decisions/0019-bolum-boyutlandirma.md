# ADR 0019 — Bolum yeniden boyutlandirma ve tasima

**Durum:** Kabul edildi · 2026-09-14

## Baglam
Kullanici DiskGenius'un "Bolumu Boyutlandir" penceresini ornek gosterdi: bolum
kapsayici alan icinde renkli bir serit olarak cizilir, kenarlari fareyle
surukleyerek boyut ve konum ayarlanir. Bizde bu ozellik hic yoktu.

Bolum boyutlandirmanin zor kismi bolum tablosu degil, **dosya sistemidir**.
Tabloyu degistirmek iki satirlik istir; dosya sistemini o yeni boyuta
uyarlamamak ise sessiz veri kaybi demektir.

## Karar

### 1. Kapsayici alan modeli
Kullanici serbest LBA girmez; bolumun **onceki bolumun sonundan sonraki bolumun
basina kadar** olan alan icinde calisir (`resize.window_for`). Boylece cakisma
bastan imkansiz olur ve DiskGenius'taki "Onundeki Boslak / Arkasindaki Bosluk"
alanlari dogrudan bu pencereden turer.

### 2. Dosya sistemi sinirlari plan asamasinda hesaplanir
`fs_resize_info()` her bolum icin **asgari** (verinin gerektirdigi) ve **azami**
(dosya sisteminin destekledigi) sektor sayisini dondurur. `plan_resize()` bu
sinirlarin disina cikan istegi **reddeder** — kullaniciya "olabilir ama veri
kaybedebilirsin" denmez.

| Dosya sistemi | Kucultme | Buyutme | Tasima |
|---|---|---|---|
| FAT12/16/32 | ✅ saf Python | ✅ saf Python | ✅ |
| exFAT | ✅ saf Python | ✅ saf Python | ✅ |
| Ham / bicimlendirilmemis | ✅ | ✅ | ✅ |
| NTFS, ext2/3/4 | Windows'ta yerel arac | bolum buyur, FS buyumez (uyarilir) | ⛔ |

### 3. Saf Python FAT boyutlandirmasi — tablo kaydirma
FAT'ta toplam sektor sayisi degisince kume sayisi degisir, kume sayisi degisince
**FAT tablosunun boyutu** degisir, o da veri bolgesinin baslangicini kaydirir.
Uc yol vardi:

1. Yerlesimi sabit tut → bolum yalnizca tablonun artigi kadar (~%1) buyuyebilir.
   Pratikte ise yaramaz.
2. Kume boyutunu buyut → tum veriyi yeniden yerlestirmek gerekir.
3. **Secilen:** tablo buyudugunde kok dizin + veri bolgesini `num_fats × fark`
   sektor ileri kaydir. **Kume numaralari degismedigi** icin FAT icerigi oldugu
   gibi kopyalanir; yalnizca kullanilan kumeler tasinir.

Kucultmede tablo oldugu gibi birakilir (fazla tablo zararsizdir), bu yuzden
kucultme **veri kopyalamaz** ve aninda biter.

### 4. exFAT — bitmap en dusuk bos alana tasinir
exFAT'ta ayni kaydirma FAT bolgesi icin yapilir. Ek olarak ayirma bitmap'i
buyumek zorunda kalabilir. Ilk uygulamada bitmap **yeni eklenen kumelerin
sonuna** tasindi; bu, birimin sonradan tekrar kucultulmesini imkansiz kildi
(en yuksek kullanilan kume artik bitmap'ti). Duzeltme: bitmap **en dusuk** bos
ardisik alana tasinir (`_exfat_free_run`).

### 5. Islem sirasi
```
kucultme:  dosya sistemi  →  (tasima)  →  bolum tablosu
buyutme:   bolum tablosu  →  dosya sistemi
```
Kucultmede once dosya sistemi kuculur; tablo yazimi basarisiz olursa bolum hala
gecerli (sadece fazladan yeri olan) bir dosya sistemi icerir. Buyutmede tersi:
once tablo, sonra dosya sistemi — dosya sistemi buyutulemezse bolum yine
tutarlidir, sadece bir kismi kullanilmaz.

### 6. Tasima sonrasi "gizli sektor" duzeltmesi
Bolum tasindiginda FAT/NTFS BPB'sindeki ofset 28 (`BPB_HiddSec`) ve exFAT'taki
ofset 64 (`PartitionOffset`) eski konumu gosterir; Windows bu durumda birimi
baglayamaz. `_patch_partition_offset()` her tasimadan sonra bu alanlari (ve
exFAT saglamasini, NTFS'in son sektordeki yedek onyukleme kopyasini) duzeltir.

### 7. Arayuz: serit + sayilar cift yonlu bagli
`ResizeBar` yalnizca cizim ve surukleme yapar, **hicbir dogrulama icermez**
(sinirlar disaridan verilir). Pencere hicbir sey yazmaz; yalnizca istenen
yerlesimi dondurur. Tum dogrulama `plan_resize()`, tum yazma `apply_resize()`
icindedir — GUI'siz de test edilebilir.

## Sonuclar
- FAT32 200 MB → 80 MB → 450 MB → +100 MB tasima: `fsck.vfat` temiz.
- exFAT 200 MB → 100 MB → 800 MB → 300 MB → tasima: `fsck.exfat` temiz.
- GPT'de tasima+buyutme sonrasi bolum GUID'i korunuyor, tablo geri okunuyor.
- Regresyon testi: `t18_bolum_boyutlandirma`.

## Reddedilenler
- **Kucultmeyi "kullanici onayiyla" zorlama:** dosya sistemi sinirini asan
  kucultme hicbir onayla yapilmiyor. Onay, veri kaybini kullanicinin sorunu
  haline getirmenin yolu degil.
- **NTFS/ext icin saf Python boyutlandirma:** $MFT/$Bitmap ve grup
  tanimlayicilarinin yeniden yerlestirilmesini gerektirir; Windows'ta zaten
  `Resize-Partition` var. ADR 0018'deki uc katmanli strateji burada da gecerli.
