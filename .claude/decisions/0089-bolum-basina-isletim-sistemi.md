# 0089 — Bölüm başına kurulu işletim sistemi

Tarih: 2026-10-04
Durum: **uygulandı**. İlgili: [0026](0026-disk-genel-bakisi.md), [0021](0021-acik-aygiti-yoklamama-ve-kopyalama-ilerlemesi.md),
[0028](0028-onyukleyici-yonetimi.md).

## Bağlam

Kullanıcı: "fiziksel diskte gösteriyorum ama bölümlerde Windows, Linux vb.
olabilir; bölüm bazlı göstermeliyiz." Disk satırındaki amblem
(`DiskInfo.os_hint`) bölüm türlerinden bir **tahmindir** ve diske tek bir sistem
yazar; çoklu açılışlı bir diskte yanlış/eksik kalır.

## Karar

* **Çekirdek** (`bootloader.partition_os(session, part)`): bölümün dosya sistemi
  içine bakılır — mevcut `detect_os` (önyükleyici penceresi) genişletildi:
  * Windows sürümü: `Windows/System32/ntoskrnl.exe` PE sürüm kaydı
    (VS_FIXEDFILEINFO, imza 0xFEEF04BD) → derleme numarası → "Windows 11
    (22631)". Kayıt defteri kovanı ayrıştırmaktan basit; XP'den 11'e aynı
    yerde. Sunucu sürümleri aynı çekirdeği paylaşır, bu yüzden derleme
    numarası adla birlikte yazılır.
  * macOS (yeni): HFS+/APFS'te `System/Library/CoreServices/SystemVersion.plist`
    → "macOS 14.6.1".
  * Linux: `/etc/os-release` (değişmedi).
  * ESP: `EFI/<üretici>` altında (iki seviyeye kadar) `.efi` bulunan sistemler
    → "EFI: Windows, ubuntu". `list_efi_loaders` Windows'u görmüyordu
    (yükleyici `Microsoft/Boot/` alt klasöründe).
* `Partition.os_name` / `os_kind`: bağlama noktası gibi diskten okunmaz.
* **Arka plan** (`ui/osinfo.py`, `OsInfoService`): fiziksel diskte okuma süresi
  öngörülemez (CLAUDE.md donma kuralı) — bir oturumun bütün bölümleri tek bir
  `QThread`te, oturumun **kendi tutamacıyla** (ikinci tutamaç yok, ADR 0021)
  incelenir; bitince arayüz bir kez tazelenir. Anahtar (aygıt, başlangıç,
  boyut, dosya sistemi): biçimlendirme/taşıma sonrası eski ad görünmez;
  "Yenile" önbelleği siler. Kapatma ve **Uygula** öncesi tarama beklenir
  (yazma sürerken aynı aygıttan okunmaz).
* **Gösterim:** ağaçta bölüm satırının simgesi sistemin amblemi, metnin sonunda
  adı; bölüm tablosunda "Bölüm"den hemen sonra "İşletim sistemi" sütunu
  (amblemli); disk haritasında blok başlığından önce amblem, ipucunda ad.
  Sistem bulunamayan bölüm dosya sistemi rengindeki kareyle kalır.
* Tablo sütunları sabit sayılar yerine adlandırılmış sabitlerle (`OS_COLUMN`,
  `MOUNT_COLUMN`...): yeni sütun sayıları kaydırıyordu.

## Kapsam dışı

Açılmamış fiziksel diskler (yalnızca yoklamayla listelenenler): orada bölüm
satırları yoklamadan gelir ve yoklama bilerek yalnızca imza okur (ADR 0026);
dosya sistemi gezintisi her 3 saniyelik listelemeye girmemeli. Disk
açıldığında bölüm başına sistem görünür.

## Doğrulama

* t89: tek görüntüde ESP (`EFI/Microsoft/Boot`, `EFI/ubuntu`, `EFI/Boot`),
  NTFS + Windows (ntoskrnl 10.0.22631), ext4 + Ubuntu, HFS+ + macOS, NTFS veri
  → tür/ad doğru; veri bölümünde gerekçe yazılı.
* ui_smoke: tarama arka planda biter; ESP ağaçta, tabloda ve bölüm nesnesinde.
* Ekran görüntüsüyle bakıldı (beş bölümlü görüntü).
