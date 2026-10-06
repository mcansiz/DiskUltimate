"""Yabanci girdi testleri (ADR 0094): baska araclarin urettigi birimler.

Kendi bicimlendiricimizle uretilen birimler okuyucu ve yaziciyla ayni
varsayimlari paylasir; o varsayim yanlissa test de ayni yanlisi yapar ve
gecer. PicoZed SD kartindaki az kumeli FAT32 boyle kacti: biz FAT32'yi hep
>= 65 525 kumeyle uretiyorduk, okuyucu da tipi kume sayisindan seciyordu.

Bu paket birimi **gercek** `mkfs.*` araciyla uretir, cekirdekle doldurur ve
bizim her yolumuzu cekirdege / fsck'ye karsi denetler:

    mkfs -> blkid -> doldur (cekirdek) -> tespit -> oku -> yedek (tam +
    kullanilan, copla dolu hedefe) -> yaz -> boyutlandir/tasi

Calistirma (bkz. __main__.py):
    python3 -m tests.yabanci                     # tum varyantlar
    python3 -m tests.yabanci --sec fat32,ext4-b1024
    python3 -m tests.yabanci --tur 5 --cekirdek  # 5 tur, farkli tohumlar (CI)

Durustluk kurali (tests/long ile ayni): arac, yetki ya da surucu yoksa sonuc
"atlandi" + neden; asla "tamam" sayilmaz.
"""
