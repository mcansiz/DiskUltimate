# Dosya Sistemi Genisletme Analizi

**Tarih:** 2026-09-28 · **Durum:** Oneri — kullanici karari bekleniyor
Karar verilince ilgili asamalar icin ADR yazilir.

Soru: *"Projeye daha fazla dosya sistemi ekleyebilir miyiz? Tum platformlarda
calisacak sekilde."*

---

## 1. Bugunku durum (koddan olculdu)

| Dosya sistemi | Taninma | Okuma | Yazma | Bicimlendirme | Boyutlandirma | Kayip bolum taramasi |
|---|---|---|---|---|---|---|
| FAT12/16/32 | ✅ | ✅ | ✅ | ✅ saf | ✅ saf | ✅ |
| exFAT | ✅ | ✅ | ✅ | ✅ saf | ✅ saf | ✅ |
| NTFS | ✅ | ✅ | 🟡 (B+ dugum bolme yok) | ✅ 3 katman (ADR 0018) | ✅ saf (ADR 0030) | ✅ |
| ext2/3/4 | ✅ | ✅ | 🟡 (extent agaci buyutme yok) | ✅ saf | yalnizca yerel arac | ❌ |
| Linux takas | ✅ (+etiket) | — | — | ❌ | — | ❌ |
| btrfs | ✅ ad+etiket | ❌ | ❌ | ❌ | ❌ | ❌ |
| XFS | ✅ ad+etiket | ❌ | ❌ | ❌ | ❌ | ❌ |
| F2FS | ✅ yalnizca ad | ❌ | ❌ | ❌ | ❌ | ❌ |
| ISO9660 | ✅ ad+etiket | ❌ | — | — | — | ❌ |

Gozlemler:
- btrfs/XFS/F2FS/ISO9660 **taniniyor ama kullanilan alan okunmuyor**
  (`fsdetect.py` yalnizca `total_bytes` doldurur) → doluluk cubugu bos.
- `planview._fs_name` icinde `"swap": "Linux Takas"` var ama `FS_KINDS` icinde
  takas yok: yarim kalmis bir baglanti.
- **Sifreli / kapsayici bolumler taninmiyor.** BitLocker, LUKS, LVM2, mdraid
  bolumleri "Bilinmeyen" gorunur. (BitLocker onyukleme sektoru `_fat` tarafindan
  FAT sanilmiyor — `num_fats=0` eler — yani yanlis tanima yok, ama ad da yok.)
  CLAUDE.md guvenlik kurali 4'un ruhuna aykiri: kullanici sifreli bir birimi
  "bilinmeyen, bos olabilir" diye bicimlendirebilir.
- Kayip bolum taramasi (`recovery._probe_signature`) yalnizca FAT/exFAT/NTFS
  arar; ext bile yok.

## 2. "Tum platformlarda" ne demek

Iki ayri soru var ve karistirilmamali:

1. **Bizim kodumuz her platformda calisiyor mu?** — Saf Python yazildigi surece
   **evet**, kosulsuz. Projenin mevcut yaklasimi (ADR 0007, 0017, 0018) budur ve
   yeni dosya sistemleri icin de gecerli olmali. Harici arac/yerel arac yalnizca
   *daha iyi* bir katman olarak eklenir, tek yol olamaz.
2. **Isletim sistemi o dosya sistemini baglayabiliyor mu?** — Bizim elimizde
   degil. Ama tam da bu yuzden deger uretiyoruz: Windows kullanicisi Mac/Linux
   diskini **bizim uzerimizden** okuyabilir.

| Dosya sistemi | Windows yerel | Linux yerel | macOS yerel |
|---|---|---|---|
| Linux takas | — | ✅ | — |
| HFS+ | ❌ (3. parti) | 🟡 gunluklu ise salt okunur | ✅ |
| APFS | ❌ | ❌ (apfs-fuse salt okuma) | ✅ |
| UDF 2.01 | ✅ okuma+yazma | ✅ | ✅ okuma; yazma **olculmedi** |
| XFS | ❌ | ✅ | ❌ |
| btrfs | ❌ (WinBtrfs 3. parti) | ✅ | ❌ |
| F2FS | ❌ | ✅ | ❌ |
| ISO9660 | ✅ salt okuma | ✅ salt okuma | ✅ salt okuma |
| ReFS | 🟡 Server / Pro WS / Win11 Dev Drive | ❌ | ❌ |

**Kural onerisi:** yeni her dosya sistemi icin en az **taninma + okuma** saf
Python'dur. Bicimlendirme saf Python yapilamiyorsa (ReFS gibi) liste yine
gosterir, secenek gri kalir ve `FsKind.reason_for` nedeni yazar (ADR 0017'deki
"secenek yok yerine neden yok" ilkesi).

## 3. Engeller (platformdan bagimsiz)

### 3.1 Sikistirma — standart kutuphanede yok
Calisma zamani bagimliligi yasak; Python 3.8+ hedefleniyor. `zlib` var, digerleri yok:

| Dosya sistemi | Sikistirma | Stdlib'de? |
|---|---|---|
| btrfs | zlib / **LZO** / **zstd** | yalnizca zlib |
| F2FS | **LZO** / **LZ4** / **zstd** | hicbiri |
| APFS / HFS+ (decmpfs) | zlib / **LZVN** / **LZFSE** | yalnizca zlib |

Secenekler: (a) saf Python cozucu yazmak (LZO/LZ4/LZVN ~150-250 satir,
zstd ~900, LZFSE ~800 satir); (b) sikistirilmis dosyada durust hata
("bu dosya zstd ile sikistirilmis, okunamiyor"). Oneri: once (b), kullanim
geldikce (a). Python 3.14'teki `compression.zstd` varsa kullanilir, yoksa (b).

### 3.2 Unicode karsilastirma tablolari
HFS+ buyuk/kucuk harf duyarsiz karsilastirma icin Apple'in kendi katlama
tablosunu, APFS normalizasyon (NFD) ister. `unicodedata` NFD'yi verir; HFS+
tablosu NTFS `$UpCase` gibi gomulur (orada 3,5 KB'a indirilmisti).

### 3.3 Dogrulama ortami — macOS YOK
Test ortami kurali (CLAUDE.md): Linux VM + Windows VM var, **macOS misafiri yok**.
HFS+ ve APFS icin Linux araclari (`fsck.hfsplus`, `apfs-fuse`) ile dogrulanir;
"macOS bagladi" iddiasi **yazilamaz** — acikca "macOS'ta test edilmedi" denir.
(macOS misafiri lisans geregi yalnizca Apple donaniminda calistirilabilir.)

## 4. Adaylarin degerlendirmesi

Maliyet tahmini mevcut modullerle olculdu: `exfat.py` 1012, `fat.py` 1063,
ext ailesi ~1800, NTFS ~3300 satir.

| Aday | Deger | Okuma | Bicimlendirme | Yazma | Acik belge | Linux VM'de dogrulama |
|---|---|---|---|---|---|---|
| **Linux takas** | orta | — | ~80 satir | — | `swap.h` | `blkid`, `swapon` |
| **Taninma genisletme** | **yuksek (guvenlik)** | ~250 satir | — | — | imzalar acik | `blkid` karsilastirmasi |
| **ISO9660 (+Joliet, Rock Ridge)** | orta | ~400 | — | — | ECMA-119 | `xorriso` ile ornek |
| **HFS+** | yuksek (Mac) | ~800 | ~600 | ~1000+ (B-agaci bolme) | Apple TN1150 | `fsck.hfsplus` (hfsprogs) |
| **UDF 2.01** | yuksek (3 OS'ta yerel) | ~700 | ~600 | ~700 | ECMA-167 + OSTA UDF | `udfinfo`, Win VM `chkdsk` |
| **XFS (v5)** | orta (RHEL) | ~900 | ~900 | zor | xfs dokumanlari | `xfs_repair -n` |
| **btrfs** | orta | ~1100 (+sikistirma) | ~1200 | cok zor (CoW, geri basvurular) | btrfs wiki | `btrfs check` |
| **F2FS** | dusuk (Android/SD) | ~700 | ~700 | zor | cekirdek kaynagi | `fsck.f2fs` |
| **APFS** | yuksek (Mac) ama pahali | ~1500 (+LZFSE) | ⛔ | ⛔ | Apple FS Reference | `apfs-fuse`; macOS yok |
| ReFS | dusuk | ⛔ belgesiz | yalnizca Windows yerel araci | ⛔ | yok | Win VM (Dev Drive) |
| ZFS, bcachefs, ReiserFS, JFS, NILFS2, Minix | dusuk | yalnizca taninma | — | — | — | — |

Notlar:
- `crc32c.py` zaten var → btrfs ve XFS v5 sagma toplamlari icin hazir.
- ReiserFS Linux 6.13'te cekirdekten cikarildi; bcachefs 6.17'de ana agactan
  ayrildi → yalnizca taninma yeterli.
- UDF, FAT32'nin 4 GB dosya sinirina takilmadan uc sistemde yerel olarak
  kullanilabilen tek acik bicimdir (exFAT disinda). Windows tarafi `format /FS:UDF`
  ile kiyaslanabilir; `Format-Volume` UDF desteklemez.
- APFS bicimlendirme ve yazma onerilmez: bozuk bir APFS kapsayicisi macOS'ta
  veri kaybina yol acar ve macOS'suz dogrulanamaz. macOS'ta `newfs_apfs`
  yerel katman olarak baglanabilir (ADR 0018 katman 1).

## 5. Onerilen asamalar

### Asama 1 — dusuk maliyet, hemen (tahmin: 1 oturum)
1. **Taninma genisletme** (`fsdetect.py`): BitLocker (`-FVE-FS-`), LUKS1/2,
   LVM2 PV (`LABELONE`/`LVM2 001`), mdraid 1.x, HFS+/HFSX, APFS (`NXSB`),
   ReFS, UDF (`NSR02/03`), JFS, ZFS, SquashFS. Sifreli/kapsayici turler bir
   **bayrakla** isaretlenir (`FSInfo.encrypted` / `container` — metin karar
   girdisi degildir kurali); bicimlendirme/silme onayi bu bayrakla ek uyari verir.
2. btrfs / XFS / F2FS icin **kullanilan alan** superbloktan okunur.
3. **Linux takas bicimlendirme** (saf Python, `FS_KINDS`'e `swap`), MBR 0x82 /
   GPT takas GUID'i zaten `convert.py` icinde.
4. **Kayip bolum taramasi**na ext, btrfs, XFS, HFS+, APFS imzalari.
5. **ISO9660 salt okuma** (Joliet + Rock Ridge ile uzun adlar).

### Asama 2 — Mac ve evrensel tasinabilirlik
6. **HFS+**: okuma → bicimlendirme (gunluksuz) → yazma.
7. **UDF 2.01**: okuma + bicimlendirme + yazma. Windows VM'de `chkdsk` ile,
   Linux VM'de cekirdek baglamasiyla dogrulanir.

### Asama 3 — Linux sunucu dosya sistemleri, once okuma
8. **XFS** okuma, ardindan v5 bicimlendirme.
9. **btrfs** okuma (zlib; LZO/zstd icin durust hata, sonra saf cozucu).
10. **F2FS** okuma.

### Asama 4 — istege bagli
11. **APFS salt okuma** (sifresiz birimler; FileVault birimi "sifreli" diye
    gosterilir, anahtar istenmez).
12. **ReFS**: yalnizca Windows'ta yerel aracla bicimlendirme; digerlerinde gri.

## 6. Her yeni dosya sistemi icin dokunulacak yerler

Bir dosya sistemi eklemek bugun su noktalara dokunmayi gerektiriyor:

| Yer | Ne eklenir |
|---|---|
| `core/fsdetect.py` | imza, etiket, kullanilan alan |
| `core/<fs>.py`, `<fs>read.py`, `<fs>write.py` | surucu (PyQt yok, acik endian) |
| `core/filesystem.py` | `<Fs>Access` sinifi + `open_filesystem` dali |
| `core/formatter.py` | `FsKind` (min/max boyut, MBR/GPT turu) |
| `core/platform.py` | `EXTERNAL_TOOLS` (platforma gore `mkfs.*`) |
| `core/convert.py` | `FS_TO_MBR`, `FS_TO_GPT` |
| `core/planview.py` | `_fs_name` |
| `core/recovery.py` | `_probe_signature` |
| `core/physical.py` | isletim sistemi tahmini (`fsler` kumesi) |
| `ui/theme.py` | `FS_COLORS` |
| `i18n/catalogs/*.po` | yeni metinler (tr/en/de) |
| `tests/` | tNN testi + VM'de `fsck` dogrulamasi |
| `.claude/specs/<fs>.md`, `diskgenius-parity.md`, ADR | belge |

Bu liste 13 noktadir ve biri unutulunca dosya sistemi yarim gorunur (takas
ornegi). Asama 1'in parcasi olarak **tek bir kutuk** onerilir: her dosya
sistemi tek bir tanim nesnesi (`FsSpec`: anahtar, gorunen ad, renk, MBR/GPT
turu, OS ailesi, dedektor, acici, bicimlendirici, boyutlandirici) ile kaydolur;
diger moduller sozlukleri buradan uretir. Boylece yeni dosya sistemi 2-3
dosyaya dokunarak eklenir.

## 7. Acik sorular (kullanici)

1. Oncelik: Asama 1 ile mi baslanir, yoksa dogrudan HFS+ / UDF mi?
2. `FsSpec` kutugu (yeniden duzenleme) Asama 1'e dahil edilsin mi?
3. macOS dogrulama acigi kabul mu? (HFS+/APFS "macOS'ta test edilmedi" etiketiyle)
