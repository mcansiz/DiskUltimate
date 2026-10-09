# 0096 — Disk klonlama tek formda

Tarih: 2026-10-09
Durum: **uygulandı** (görüntü dosyalarıyla ölçüldü; fiziksel disk hedefi VM'de sınanmadı)
İlgili: [0076](0076-diskten-diske-klon.md), [0021](0021-acik-aygiti-yoklamama-ve-kopyalama-ilerlemesi.md),
[0032](0032-yedekleme-tek-pencere-ve-not.md), [0095](0095-guc-secenekleri-uyku-engeli.md)

## Kullanıcı isteği

"Disk klonlama akışını da ayrı ayrı formlar yerine tek bir form üzerinde
seçip işlemi bu formda takip edelim." — DiskGenius "Clone Disk" penceresi örnek.

## Önceki durum

Dört pencere: "nereye klonlansın?" soru kutusu → hedef disk listesi
(`CloneTargetDialog`) ya da dosya kaydetme penceresi → genel ilerleme penceresi
(`run_task`) → sonuç kutusu. Kaynak hep **açık oturumdu**; açık olmayan bir disk
klonlanamıyordu.

## Karar

`ui/dialogs/clone.py` — `CloneDialog`, tek pencere:

* **Kaynak**: "Kaynak disk seç..." + disk haritası. Açık görüntüler ve fiziksel
  diskler; açık olmayan fiziksel disk iş parçacığında **salt okunur** açılıp iş
  bitince kapanır (`DiskSession.clone_between`, `backup_physical` ile aynı
  ilke). Varsayılan: ana penceredeki açık oturum.
* **Hedef**: fiziksel disk, uygulamada açık görüntü ya da **yeni görüntü
  dosyası** (yol satırı). Hedefin şu anki içeriği haritada "klonla silinecek"
  başlığıyla görünür.
* Onaylar, ilerleme, geçen/kalan süre, **Durdur**, güç seçenekleri (ADR 0095)
  ve sonuç aynı pencerede; iş bitince pencere açık kalır. Dosya hedefinde
  "Klonu aç" düğmesi.

Güvenlik kuralları eski hedef penceresinden aynen taşındı (uygunsuz disk gri ve
nedenli, silme onayı, sistem diskinde ad yazma, bağlı bölüm uyarısı). Onay
hedefe özgüdür: hedef değişince ve her klondan sonra sıfırlanır.

## Bulunan ve kapatılan iki açık

1. **Yarım klon dosyası kalıyordu.** `clone_to_new_image` hata ya da
   durdurmada dosyayı silmiyordu (geri yüklemede t94 ile kapatılmıştı). Artık
   siliniyor; başarıda `restore_owner` ile sahiplik çağıran kullanıcıya
   veriliyor (yetkili kopyada dosya root'a ait kalıyordu).
2. **Kaynağın kendi dosyasına klon.** Yeni dosya `overwrite=True` ile
   yaratıldığı için hedef olarak kaynağın dosyası seçilirse kaynak ilk yazmadan
   önce sıfırlanırdı. `clone_to` bunu `SessionError` ile reddeder; form ayrıca
   uygulamada açık herhangi bir dosyayı hedef kabul etmez.

## Doğrulama

* `tests.run_all` t98: dosyaya ve açık görüntüye klon (veri birebir), durdurmada
  yarım dosya silinir, kaynağın kendi dosyası reddedilir ve kaynak sağlam kalır.
* `tests.ui_smoke` `klon_hedefi_denetimi` (yeni forma taşındı) + sözde dil denetimi.
* Ekran dışı uçtan uca: `sdcard.img` kopyası form üzerinden klonlandı, `cmp`
  birebir aynı.
* **Sınanmadı:** fiziksel disk kaynağı/hedefi (VM'de `du-test-disk.vdi`
  üzerinde yapılmalı).
