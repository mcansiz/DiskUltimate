# 0094 — Kök neden: kendi kendini doğrulayan testler; yabancı girdi matrisi

Tarih: 2026-10-06
Durum: **uygulandı** — test altyapısı ve bulguların düzeltmeleri (79 regresyon testi);
[denetim notu](../logs/2026-10-06-uyumluluk-denetimi.md).
İlgili: [0080](0080-uzun-testler-plani.md), [0091](0091-fat32-yapisal-tespit-ve-hizli-yedek.md).

## Kullanıcı sorusu

"O kadar uzun test yaptık, bu kritik ölümcül hata nasıl olabiliyor? Buna
benzer oluşabilecek hataları da analiz et; gerekirse saatlerce test yap."

## Kök neden

`picozed.img` FAT32'si (mkdosfs -F 32 -s 8, 25 549 küme) bizde FAT16
göründü; yazma birimi bozacaktı. Testler bunu yakalayamazdı, çünkü:

1. **Girdiyi hep biz ürettik.** run_all, fs_matrix ve uzun testler (ADR
   0080) birimi kendi biçimlendiricimizle oluşturuyor. Biçimlendirici
   FAT32'yi ≥ 65 525 kümeyle üretir; okuyucu da türü küme sayısından
   seçiyordu. İki taraf **aynı yanlış varsayımı** paylaşınca test geçer.
2. **Harici doğrulama tek yönlüydü.** fsck ve çekirdek bağlama "bizim
   yazdığımız birim geçerli mi" sorusunu yanıtlıyordu. "Başkasının ürettiği
   geçerli birimi biz doğru okuyor muyuz / bozmadan yazıyor muyuz" sorusunu
   soran test yoktu.
3. **Yazıcılar yasak listesiyle korunuyordu.** Tanınmayan özellik, durum ya
   da yerleşim "bilinen kötü" listesinde değilse yazmaya izin veriliyordu.
   Bu, CLAUDE.md'deki fiziksel disk kuralı 4'ün ("bilinmiyor" asla "risk
   yok" sayılmaz) dosya sistemi düzeyinde çiğnenmesiydi.

Uzun testlerin süresi bu sınıfı kapsamıyordu: saatlerce koşan test aynı
kapalı döngüde kaldığı sürece yeni yerleşim görmez.

## Karar

**Yabancı girdi matrisi** (`tests/yabanci`, `.github/workflows/yabanci.yml`):

- 68 varyant, **gerçek** araçlarla: mkfs.fat (küme 512 B–64 KiB, mantıksal
  sektör 2048/4096, tek FAT, ayrılmış alan, hizasız, FAT16'da az küme...),
  mkfs.exfat (küme 512 B–32 MiB, sınır hizası, 4K sektör, unicode etiket),
  mkntfs (küme 512 B–2 MiB, 4K sektör), mke2fs (1/2/4 KiB blok, meta_bg,
  bigalloc, inline_data, gdt_csum, flex_bg yok, 128/1024 B inode,
  günlüksüz, sparse_super2, extent yok, casefold, encrypt, large_dir, kota,
  orphan_file, az inode...), XFS, HFS+, UDF, btrfs, F2FS.
- Doldurma **çekirdekle** (CI'da sudo + mount): kenar boyutlar, unicode ve
  uzun adlar, derin dizinler, 300 girişli dizin, silinip araya büyük dosya
  yazılarak parçalanma.
- Her adım üç katmanla doğrulanır: kendi okuyucumuz (liste + özet), dış
  fsck (**taban çizgisine göre**: mkfs sonrası temiz olmayan birimde fsck
  "referans kabul etmiyor" sayılır), çekirdekle bağlama.
- Adımlar: tespit (blkid'e karşı tür **ve etiket**), okuma, tam + yalnızca
  kullanılan alan yedeği **çöple dolu** hedefe, yazma, büyütme/küçültme/
  taşıma. Yazıcı ya da boyutlandırıcı **reddedebilir**; reddettikten sonra
  birim sağlam kalmalıdır.
- `--tur N`: her tur farklı tohum → farklı ağaç/parçalanma; CI'da grup
  başına iş, saatlerce koşabilir.

**Denetim:** dört alanın (ext, FAT/exFAT, NTFS, bölüm tabloları) referans
uygulamalara (Linux çekirdeği, e2fsprogs, ntfs-3g, util-linux, spec'ler)
karşı okunması; bulgular sınıflandırılıp düzeltiliyor (denetim notu).

**İlke (yeni):** yazıcılar **izin listesi** kullanır. Yalnızca doğrulanmış
özellik/durum bileşimine yazılır; geri kalanı açıklamalı reddedilir. Kullanılan
alan haritası da aynı ilkeyle: güvenilmeyen durumda "tamamını yedekle".

## Uzun testlere ek

`tests/long` yedek adımı artık iki kipi de dener ve **çöple dolu** hedefe
geri yükler (sıfır hedef, yanlışlıkla atlanan bloğu gizleyebilirdi).
