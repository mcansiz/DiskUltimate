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

## 4. FAT32 tasima: yedek onyukleme sektoru guncellenmiyordu

Hedefli yeniden kosu (CI 37207695550, full): exFAT ve FAT12 her platformda
gecti; Linux FAT32 `saga_tasi` sonrasi fsck.fat "differences between boot
sector and its backup (29:48/08, 30:3c/00)". `_patch_hidden_sectors` gizli
sektor alanini (ofset 28) yalnizca asil sektore yaziyordu; FAT32'nin yedek
sektoru (BPB ofset 50) eski konumu gosteriyordu. Quick'te gorunmedi: fsck.fat
bu farki tek basina "mostly harmless" deyip 0 ile cikiyor; full'de 4 GiB-1
tasma satiriyla birlikte cikti ve istisna (madde 3) yalnizca o kalibi kabul
ettigi icin dogru olarak dustu. Duzeltme: FAT32'de yedek de yazilir. t88
(eski kodda "asil/yedek BPB farkli").

## 5. `kurtar` adimi parcali dosyayi seciyordu (test tarafi)

0.5.2-beta tam kosusu (CI 37214273770, tohum 73770): aygit FAT32 `kurtar`
"kurtarilan icerik farkli (kismen uzerine yazilmis)" — Linux ve Windows.
Yerelde (goruntu, quick, ayni tohum) yeniden uretildi: alfabetik ilk aday
`degistir`de silinen dosyalarin bosluklarina yazilmis **231 kumelik parcali**
bir dosyaydi. FAT'te silinen dosyanin zinciri silinir; parcali dosya meta
veriden geri gelmez (DiskGenius da gelmez) ve arac bunu "kismen uzerine
yazilmis" diye dogru bildiriyordu. Urun hatasi yok.

Test duzeltildi: kurtarilacak dosya silmeden once zinciri **ardisik**
olanlardan secilir ve birebir donmeli; parcali aday varsa o da silinir ve
tarayicinin ona "iyi" **dememesi** denetlenir. Yerelde fat12/16/32 + exfat x
gpt/mbr-mantiksal tamam.
