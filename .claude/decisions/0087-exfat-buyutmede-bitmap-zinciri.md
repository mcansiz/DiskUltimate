# 0087 — exFAT buyutmede bitmap zinciri; uzun test butcesi kume artigini sayar

Tarih: 2026-10-04
Durum: **uygulandi**. Ilgili: [0083](0083-uzun-test-bulgulari-exfat-ve-macos-yetki.md),
[0080](0080-uzun-testler-plani.md).

## 1. exFAT: en aza kucultup geri buyutunce bitmap zinciri kisa kaliyordu

Tam profil uzun test (CI 37202739981, 6.7 GiB exFAT): `geri_buyut` sonrasi
Apple `fsck_exfat` "Cluster chain for Main Bitmap has too few clusters for
its size", Windows surucusu dosyalari listelemiyor. Linux `fsck.exfat` ve
cekirdek bunu denetlemiyor (Linux isleri gecti).

Neden: ADR 0083 ile kucultme bitmap zincirini kisaltir. Buyutmede
`_exfat_extend_bitmap` bitmap'e yeni ardisik alan arar; kisaltmada bosalan
kumeler eski ilk kumenin hemen ardinda oldugu icin bulunan alan cogu zaman
**eski ilk kumeden** baslar. Zincir yalnizca ilk kume degisince yeniden
yaziliyordu: uzunluk alani buyuyor, FAT zinciri 1 kume kaliyordu. Quick
profilde bitmap tek kumeye sigdigi icin hic gorunmedi.

Karar: zincir, kume **listesi** degistiginde yazilir. Test t87: 600 MB, 4 KiB
kume (bitmap 5 kume) -> en aza kucult (1 kume) -> geri buyut: zincir 5 kume,
son kume EOC, bitmap kumeleri dolu isaretli; eski kodda 1/5.

Etki: 0.5.1-beta dahil. Veri yerinde kalir; Windows'ta `chkdsk /F` zinciri
onarir. Yeni surumle duzelir.

## 2. Uzun test butcesi kume artigini sayar

FAT12 tam profil: 1455 dosya (17 MiB) ~21 MiB'lik birime "yer yok" dedi;
8 KiB kumede dosya basi artik ~6 MiB tutuyordu. `data.footprint(boyut,
birim)` butcede ve bolum boyutunda kullanilir (FAT12 8 KiB, FAT16 64 KiB,
digerleri 4 KiB). Yerelde FAT12 full: 16/16 tamam.

## 3. FAT32 4 GiB-1 dosya: fsck.fat 4.2'nin 32 bit tasmasi (bizim hata degil)

Tam profil Linux FAT32 (3 tablo): `fsck.vfat` "/sinir_tam_4GiB-1.bin: File
size is 4294967295 bytes, cluster chain length is 0 bytes". Yerelde
olculdu: dizin girisi ilk kume 3, kullanilan kume 2^20 + kok (zincir tam);
ayni dosya bir kume kisa (4 GiB-4097) iken fsck temiz. Tam 4 GiB-1 dosya her
kume boyunda tam 2^32 baytlik zincir kaplar; fsck.fat uzunlugu 32 bitte
tutup 0'a tasiyor. Windows/macOS full islerinde butce sinir dosyasina
yetmedigi icin yalnizca Linux'ta gorundu.

Karar: `verify._only_fsck_fat_overflow` — fsck.fat ciktisinda **yalnizca**
bu kalip varsa sonuc "ok + aciklama"; baska herhangi bir satir gercek bulgu.
Zincirin dogrulugu ayrica kendi okuyucunun ve cekirdegin dosyayi okuyup
SHA1 ile karsilastirmasiyla kanitlanir.
