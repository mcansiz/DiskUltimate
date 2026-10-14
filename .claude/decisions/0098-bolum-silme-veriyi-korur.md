# 0098 — Bölüm silme veriyi korur; yazma yeteneği kaynağın kipinden ayrıldı

Tarih: 2026-10-14
Durum: **uygulandı** — gözlemci gezinti testi (ADR 0097) buldu; çekirdek
testleri t102–t104, gezinti `kurtarma` senaryosu görüntü kipinde geçti
İlgili: [0025](0025-bekleyen-islem-kuyrugu.md), [0054](0054-takas-ve-kayip-bolum-imzalari.md),
[0097](0097-gozlemci-arayuz-gezintisi.md)

## Bulgular

Gözlemci gezinti testinin `kurtarma` ve `bolum` senaryoları ilk koşularında
üç kullanıcıya görünür hata buldu; hiçbirini mevcut testler yakalamamıştı
çünkü her biri iki özelliğin **arka arkaya** kullanılmasında ortaya çıkıyor.

1. **Bölüm sil → Kayıp bölümleri tara: bulunamadı.**
   `DiskSession.delete_partition(wipe=True)` bölümün ilk ve son 2 MB'ini
   (`formatter.wipe_partition`, 4096 sektör) sıfırlıyordu. Önyükleme sektörü
   gidince tarama silinen bölümü hiç görmüyordu: uygulamanın kendi kurtarma
   özelliği, kendi silme işlemine işlemiyordu. ADR 0054 aynı silmeyi bulunan
   bölümü **geri eklerken** düzeltmişti; silmenin kendisine dokunulmamıştı.
   Uzun testler ve regresyonlar zaten `wipe=False` geçiyordu.
2. **İmza taramasıyla çıkarılan ZIP açılmıyordu.** ZIP imzasının bitişi
   tanımlı değildi; dosya azami boyuta (256 MB, ya da bölüm sonuna) kesiliyordu.
   Zip okuyucuları "merkezi dizin sonu" kaydını dosyanın **sonunda** arar;
   arkadaki megabaytlarca çöp yüzünden hiçbiri açamıyordu.
3. **Fiziksel diskte etiket değiştirme ve boş alan silme seçilemiyordu.**
   Arayüz `fs.writable` soruyordu; kaynak ADR 0025'ten beri hep salt okunur
   açıldığı için bu her zaman yanlıştı ("Desteklenmiyor"). Uygula sonrası
   salt okunura dönüşle (worklog 2026-10-14 (2)) görüntü dosyalarında da
   aynı olacaktı.

## Karar

1. **Silme yalnızca tablo girdisini kaldırır** (`delete_partition` ve
   `delete_op` varsayılanı `wipe=False`). DiskGenius ve Windows `diskpart`
   böyle yapar; silinen bölüm "Kayıp bölümleri tara" ile geri getirilebilir.
   Veriyi yok etmek isteyen için **Güvenli silme** vardır. Aynı yere yeni
   bölüm oluşturmak zaten kendi başını temizler (`create_partition(wipe=True)`),
   yani eski imza yeni bölüme sızmaz.
2. **ZIP'in sonu merkezi dizin sonu kaydından hesaplanır**
   (`PK\x05\x06` + 18 bayt + yorum uzunluğu, `recovery._zip_end`). Kayıt
   bulunamazsa ZIP diğer bitişli imzalar gibi "güvenilir değil" sayılıp
   atlanır (eskiden 256 MB'lık çöp üretiyordu).
3. **Yazma yeteneği kaynağın şu anki kipinden ayrıldı:**
   `FileSystemAccess.write_possible()` / `label_possible()`. FAT ve exFAT'te
   yalnızca birimin durumu (`write_block_reason`, kirli bayrak vb.) sayılır;
   diğer sürücülerde eski davranış sürer. Etiket ve boş alan silme kuyruğa
   girer, Uygula'da yazma yetkisiyle yazılır.

## Bilinen sınırlar

* NTFS/ext/HFS/UDF'de `write_possible()` hâlâ kaynağın kipine bağlı
  (sürücülerin `write_support()` önce aygıtın salt okunurluğunu soruyor);
  fiziksel diskte bu dosya sistemlerinde boş alan silme seçilemez. Etiket
  zaten yalnızca FAT/exFAT'te var.
* İç içe saklanmış (stored) bir zip, dıştaki zip'i iç kaydın sonunda
  keser — imza taramasının bilinen bir sınırı (PhotoRec de merkezi dizini
  doğrulamadan aynı hatayı yapar).
