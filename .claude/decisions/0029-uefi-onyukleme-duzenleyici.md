# ADR 0029 — UEFI onyukleme duzenleyici: yapiyi cozmek ile bellenime dokunmayi ayirmak

**Tarih:** 2026-09-17
**Durum:** Kabul edildi
**Ilgili:** ADR 0003 (katman ayrimi), ADR 0008 (capraz platform stratejisi),
ADR 0023 (yetki yukseltme), ADR 0025 (bekleyen islem kuyrugu),
ADR 0027 (cok dilli arayuz), ADR 0028 (onyukleyici yonetimi)

## Baglam

Kullanici [efibooteditor](https://github.com/Neverous/efibooteditor)
uygulamasinin yeteneklerinin de istendigini belirtti. O uygulama C++/Qt ile
yazilmis (LGPL-3.0) ve UEFI bellenimindeki onyukleme degiskenlerini duzenler:
giris listesi, sira, etkin/gizli bayraklari, `BootNext`, bekleme suresi,
kisayol tuslari, JSON disa/ice aktarma.

Kod devralinmadi; yapilar **UEFI Specification 2.10**'dan (bolum 3.1.3
`EFI_LOAD_OPTION`, 3.1.6 `EFI_KEY_OPTION`, 10.3-10.6 aygit yolu) yeniden
yazildi. C++'tan Python'a birebir cevirmek zaten mumkun degildi; belirtimden
yazmak hem daha temiz hem de lisans acisindan tartismasiz.

### Isin zor kismi nerede

UEFI onyukleme duzeni iki ayri sey icerir:

1. **Ikili yapilar** — `Boot0000` degiskeninin icindeki `EFI_LOAD_OPTION`,
   icindeki aygit yolu dugum zinciri (`PciRoot(0x0)/Pci(0x1D,0x0)/HD(...)/File(...)`).
   Bu tamamen belirtim isidir; isletim sistemiyle ilgisi yoktur.
2. **Degiskene erisim** — Linux'ta `/sys/firmware/efi/efivars` altinda bir
   dosya, Windows'ta `GetFirmwareEnvironmentVariableExW` cagrisi ve
   `SeSystemEnvironmentPrivilege` ayricaligi, macOS'ta kapali.

Bunlari tek modulde birlestirmek, cozumleyicinin **yalnizca UEFI ile acilmis
bir makinede** test edilebilmesi demekti.

## Karar

### 1. Uc katman

| Modul | Sorumluluk | Test edilebilirligi |
|---|---|---|
| `core/efiboot.py` | UEFI yapilarini cozer ve uretir — saf Python, isletim sistemi bilmez | Her makinede, aygit gerekmeden |
| `core/platform.py` | Degiskeni okur/yazar/siler/sayar (efivarfs, Windows API) | Platforma bagli |
| `core/efistore.py` | Ikisini birlestirir: butun duzeni okur, yedekler, degisiklik plani cikarir, yazar | Yedek dosyasi uzerinden her makinede |

Ayrimin karsiligi olculdu: aygit yolu ve giris cozumlemesi, UEFI ile acilmamis
bir makinede bile `tests.run_all` t29 icinde dogrulanir.

### 2. Cozulemeyen dugum **atilmaz**

Tanimadigimiz bir dugum turu ham haliyle saklanir ve yazarken oldugu gibi geri
konur; metin gosteriminde `Path(tur,alt,onaltilik)` olarak gorunur. Bilinmeyen
bir dugumu dusurmek, tanimadigimiz bir bellenimin onyukleme girisini sessizce
bozmak olurdu.

Ayni gerekceyle yedek dosyasi hem **cozulmus alanlari** hem de **ham baytlari**
tasir; geri okurken ham veri kullanilir, cunku metin gosterimi bilgi kaybeder.

### 3. Metin gosterimi **cevrilmez**

`File(\EFI\ubuntu\shimx64.efi)` her dilde ayni yazilir. Bu bir bicimdir, cumle
degil; cevirmek `efibootmgr` ciktisiyla karsilastirmayi imkansiz kilardi.
Cevrilen sey dugumun **aciklamasidir** ("Disk bolumu", "Dosya yolu") — ADR
0027'nin `mark()` kalibiyla.

### 4. Hicbir sey aninda yazilmaz

Degisiklikler bellekte birikir. "Bellenime yaz" once `efistore.changes()` ile
**tam listeyi** uretir ve gosterir. Bu, ADR 0025'in kuyruk ilkesidir; ayri
tutulmasinin nedeni kuyrugun **diske** ait olmasidir, bellenime degil.

### 5. Yazma sirasi sabittir

1. Girisler yazilir/olusturulur (`Boot0001`, ...)
2. Sira listeleri yazilir (`BootOrder`, ...)
3. **En son** silinenler kaldirilir

Silmeyi basa almak, `BootOrder`in var olmayan bir girisi gosterdigi bir an
yaratir; bazi bellenimler acilista o anda kendi duzenini "onarip" butun sirayi
bozar. Test bu sirayi dogrudan olcer (t33).

Bir adim basarisiz olursa **durulur** ve kismi sonuc raporlanir; sessizce
devam etmek tutarsiz bir duzen birakirdi.

### 6. Yazmadan once yedek **zorunludur**

Arayuz once yedek dosyasi ister; kullanici vermezse hicbir sey yazilmaz.
Gerekce ADR 0004'ten daha serttir: bellenim degiskenleri diskte degil anakart
uzerindedir ve yanlis bir duzen makineyi acilmaz birakabilir — o noktada
duzeltmek icin gereken sey (calisan bir isletim sistemi) artik yoktur.

### 7. "Okunamadi" ile "bos" ayrilir

BIOS kipinde acilmis bir makinede onyukleme degiskeni **yoktur**, bos degildir.
`efivars_state()` her durumda bir **neden** dondurur ve arayuz onu gosterir.
Bos bir liste gostermek, kullaniciya girisleri silinmis gibi gorunurdu.

### 8. Sirada olmayan giris gizlenmez

`BootState.ordered()` sira listesinde gecmeyen girisleri de **sona ekler** ve
arayuz sira sutununda `-` yazip ipucunda nedenini soyler. Ayrica
`orphans()` sirada gecip karsiligi olmayan numaralari listeler — bozuk bir
duzenin en sik belirtisi budur ve disa dusmus bir isletim sistemini bulmanin
tek yolu.

## Windows tarafinda iki tuzak

1. **Ayricalik okumak icin de gerekir.** `SeSystemEnvironmentPrivilege`
   yonetici belirtecinde bile varsayilan olarak kapalidir ve etkinlestirilmeden
   yapilan her cagri **sessizce bos doner** — hata vermez. Bu, gelistirme
   sirasinda bir kez yasandi: sayim 205 degisken buldu ama okuma bos dondu.
   `efivar_read` ve `efivar_names` artik ayricaligi kendileri acar.
2. **ctypes varsayilanlari 64 bit tutamaci keser.** `OpenProcessToken`
   argtypes verilmeden cagrildiginda basarisiz oluyordu; `_win_kernel32`'nin
   ayni nedenle var oldugu tuzak (ADR 0011).

Degisken sayimi belgelenmemis `NtEnumerateSystemEnvironmentValuesEx` ile
yapilir; basarisiz olursa bos liste doner ve cagiran bilinen adlari tek tek
dener — eksik bir liste gostermektense bilinenleri gostermek daha dogrudur.

## Linux tarafinda bir tuzak

`efivarfs` dosyalarinin cogunda "degistirilemez" (immutable) bayragi acik olur
ve kaldirilmadan yazma `EPERM` verir. `chattr -i` calistirmak yerine ayni ioctl
dogrudan `fcntl` ile cagrilir — harici arac gerektirmez, CLAUDE.md'nin "harici
bagimlilik yok" kuralina uyar. Ayrica oznitelik ve veri **tek yazmada**
gitmelidir; efivarfs parcali yazmayi kabul etmez.

## Sonuclar

**Kazanilan**

- Onyukleme girisleri Windows ve Linux'ta ayni kodla okunur, duzenlenir ve
  yedeklenir.
- Cozumleyici gercek bellenim verisine karsi dogrulandi (bkz. Olcum).
- Yedek dosyasi her makinede acilip incelenebilir; bellenime erisimi olmayan
  bir makinede bile duzen gorulebilir.

**Kabul edilen sinirlar**

- macOS'ta bellenim degiskenlerine erisim yoktur; pencere acilir ve nedenini
  soyler.
- Yeni giris **olusturma** su surumde yoktur: var olan girisler duzenlenir,
  silinir, siralanir. Sifirdan giris kurmak icin hedef bolumun GUID'i ve ESP
  uzerindeki `.efi` yolu gerekir; ikisi de `core/bootloader.py` tarafindan
  zaten cikariliyor, ama arayuz akisi bir sonraki adima birakildi.
- Yedekten **dogrudan bellenime yazma** yoktur. Bir makinenin duzenini baska
  bir makineye yazmak, orada var olmayan disklere isaret eden girisler
  birakirdi.

## Olcum

- **Gercek bellenim verisi** (gelistirme makinesi, Windows 10, UEFI, salt
  okunur): 205 degisken sayildi; `BootOrder` = 0007, 0005, 0000, 0001, 0002,
  0003. Windows Boot Manager, ubuntu shim, NVMe UEFI girisi, iki ag girisi ve
  `Uri()` dugumlu HTTPs Boot girisi cozuldu. **Alti girisin de `to_bytes()`
  ciktisi okunan baytlarla bire bir ayni.** Yedek alinip geri okundugunda
  degisiklik plani bos dondu.
- **`tests.run_all` t29** — aygit yolu metninin `efibootmgr` bicimiyle birebir
  ayni oldugu; giris/kisayol/sira gidis-donusu; bilinmeyen dugumun korunmasi;
  GUID karisik siralamasi.
- **`tests.run_all` t33** — yedek gidis-donusu; degisiklik planinin sirasi
  (girisler → sira → silmeler); sirada olmayan girisin gizlenmemesi.
- **`tests.run_all` t34** — bellenim yazilamiyorken planin uygulanmamasi ve
  nedenin bildirilmesi. Bu testin kendisi gercek bellenime dokunur, bu yuzden
  **yalnizca yazmanin zaten kapali oldugu makinede** kosar; Linux Mint
  misafiri BIOS kipinde acildigi icin orada gercekten kostu.

### Yazma yolu olculdu (2026-09-17, ikinci tur)

Ilk yazimda yazma yolu olculememisti: test misafiri BIOS kipinde aciliyordu.
Kullanicinin istegiyle misafir **UEFI kipine cevrildi** (ayrintilar:
`.claude/docs/testing.md` > "Misafiri UEFI kipine almak") ve
`tests/efi_write_test.py` yazildi — `tests.run_all` icine **alinmadi**, cunku
gercek bellenim degiskenlerini degistirir; `physical_write_test.py` gibi acik
onayla calisir.

**Sonuc: 29/29 gecti.** Her adim bagimsiz bir araca (`efibootmgr`) karsi
dogrulandi:

| Olculen | Sonuc |
|---|---|
| `Timeout` yaz / geri al | yazildi, `efibootmgr` dogruladi, geri alindi |
| `BootOrder` sirala / geri al | yazildi, dogrulandi, geri alindi |
| `BootNext` yaz / **sil** | degisken silme yolu da olculdu |
| Girislerin baytlari | islem sonunda **bayt bayt korundu** |
| Duzenin butunu | baslangictaki halle **bire bir ayni** |

Makine her turdan sonra yeniden baslatildi ve normal acildi.

### Olcum bir hata buldu: "yokluk" ile "sifir" ayni degil

Ilk kosumda 28 adimdan biri dustu. `Timeout` degiskeni makinede **hic yoktu**;
`load()` bunu `None` okuyordu ama `changes()` yalnizca `updated.timeout is not
None` oldugunda is yapiyordu — yani bir duzeni okuyup **aynen geri yazmak onu
degistiriyordu** (degisken yokken 0 olarak olusuyordu). `BootNext` icin silme
dali zaten vardi, `Timeout` icin yoktu.

Duzeltildi: `changes()` artik `Timeout` icin de **silme** uretir. Bu, ancak
gercek bir makinede goruldu — uretilmis bir durumda `Timeout` hep vardi.
