# ext4'ü önündeki boş alana genişletme — hata analizi (2026-10-01)

Kullanıcı arayüzde 20 GB `yeni-disk.img` oluşturdu: FAT32 1 GB, NTFS 3,94 GB,
NTFS 7,33 GB (sonra 5,31 GB'a küçültüldü), ext4 7,75 GB. 3. bölüm küçülünce
ext4'ün **önünde** 2,03 GB boş alan oluştu. ext4'ü bu alana genişletmek =
başlangıcı sola taşımak + büyütmek (7,75 GB veri kopyası).

## Bu oturumda yeniden deneme (masaüstündeki kopya)

* Yedekler: `.tmp/kullanici/yeni-disk.ilk.img` (dokunulmamış), `yeni-disk.dolu.img`
  (dosyalar yazıldıktan sonra).
* `ExtAccess` ile 1963 dosya + 5 klasör (Türkçe adlar, 60 MB dosya) — 13 sn,
  `e2fsck -fn` temiz.
* Arayüzün yolu (resize_info → plan_resize → kuyruk → Uygula): 17,6 sn, hata yok.
  ext4 9,77 GB (2 562 298 blok), `e2fsck` temiz, 1963 dosya SHA-1 birebir,
  1-3. bölümler bayt bayt aynı, `sfdisk --verify` hata yok.

## Kullanıcının arayüz oturumundaki gerçek hata (session-20261001-202529-37929.log)

```
20:25:55 Goruntu olusturuldu: /dev/yeni-disk.img (20.00 GB)
20:29:21 Bolum 4 boyutlandir — 7.75 GB -> 9.77 GB; 2.03 GB geri tasinacak
20:29:33 operations.step(kind=resize) — 6540 ms HATA OSError: [Errno 28] Aygıt üzerinde boş yer yok
         ... resize.py _move_data -> image.write
20:30:02 DIKKAT: 0 adim uygulandi, 'Bolum 4 boyutlandir' adiminda durdu
20:31:15 Bolum 4 bicimlendir — ext4        (kullanıcı bölümü yeniden biçimlendirdi)
```

### H1 — Görüntü `/dev` içinde oluşturuldu (KRİTİK, hâlâ etkili)
* `main_window.new_image`: `default = os.path.dirname(self.session.path)`.
  Uygulama açılışta fiziksel diski (`/dev/nvme0n1`) salt okunur açtığı için
  varsayılan klasör **`/dev`** oldu.
* `/dev` = devtmpfs (bellekte, 6,5 GB). 20 GB seyrek görüntü oraya yazıldı.
* Kanıt (şu an): `/dev/yeni-disk.img` 20 GB duruyor, `/dev` **%100 dolu**
  (6,6 GB RAM). udev yeni aygıt düğümü oluşturamayabilir.
* Aynı kalıp `new_vhd` içinde de var.

### H2 — Taşıma yarıda kesilince bölüm bozuluyor, mesaj "0 adım uygulandı" (KRİTİK)
* Sola taşımada hedef kaynakla örtüşür: ilk 2,03 GB'tan sonra kaynak bölümün
  başı (zaten kopyalanmış kısım) üzerine yazılır. ENOSPC o noktadan sonra
  geldiği için eski konumdaki ext4'ün başı (ustblok dahil) ezildi, tablo ise
  güncellenmedi → bölüm ne eski ne yeni yerde okunabilir.
* Yeniden üretildi (`.tmp/kullanici`, yapay ENOSPC 120 MB'ta): sonuç
  "0 adım uygulandı", tablo eski konumu gösteriyor, eski konumda dosya sistemi
  **tanınmıyor**.
* Kullanıcıya verilen mesaj yanıltıcı: veri kaybı olmuş ama "hiçbir şey
  uygulanmadı" deniyor.

### H3 — Boş alan da kopyalanıyor / seyrek görüntü şişiyor (ORTA; H2'yi tetikledi)
* `_move_data` bölümün tamamını kopyalıyor: 7,75 GB'ın ~230 MB'ı dolu.
* Seyrek görüntüde okunan sıfırlar deliklere yazılınca yer ayrılır → görüntü
  şişer (masaüstündeki kopya da tam boy, 21 GB). tmpfs'te ENOSPC'nin asıl
  nedeni bu.
* Ön denetim yok: taşıma, hedef dosya sisteminde yer kalıp kalmadığına
  bakmadan başlıyor.

### H4 — Qt uyarıları (DÜŞÜK, görsel)
* `QString::arg: Argument missing: , dub/img/vhd/json...` dosya penceresi her
  açıldığında. Qt'nin Türkçe çevirisinde (qtbase_tr) dosya türü metni
  ("%1 File") yer tutucusuz; dosya penceresinin "Tür" sütunu yanlış görünür.

### H5 — ext4 biçimlendirici özellik seti (BİLGİ)
* `extent`, `flex_bg`, `64bit`, `metadata_csum`, `resize_inode` kapalı
  (ADR 0017'de bilinçli: o zaman yazıcı extent desteklemiyordu). Yazıcı artık
  extent destekliyor (ADR 0057); varsayılan özellikler mkfs.ext4'e
  yaklaştırılabilir.

## Önerilen düzeltmeler
1. H1: varsayılan klasör fiziksel aygıtsa `platform.default_image_dir()`;
   `platform` içinde güvensiz konum denetimi (devtmpfs/proc/sys, Windows
   `\\.\`), oluşturmadan önce hedef dosya sisteminde boş yer uyarısı.
2. H2: taşıma hatasında açık mesaj ("bölüm yarıda kopyalandı, X GB taşındı,
   bölüm bozuk olabilir — yedekten geri yükleyin"), kaldığı yerin tanılama
   kaydı; mümkünse devam ettirilebilir taşıma.
3. H3: sıfır bloklarını atla (kaynak ve hedef sıfırsa yazma yok → seyrek
   kalır, kopya ~30 kat küçülür); taşıma öncesi gereken yer ön denetimi.
4. H4: Qt ileti işleyicisinde bu uyarıyı filtrele ya da qtbase çevirisinin
   dosya penceresi metnini düzelt.
5. H5: ext4 varsayılanlarına extent (+flex_bg) ekle; e2fsck ile doğrula.

**Durum (ayni gun):** bes bulgu da duzeltildi — ADR 0073.
