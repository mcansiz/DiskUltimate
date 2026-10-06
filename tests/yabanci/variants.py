"""Yabanci girdi varyantlari: gercek `mkfs` araclariyla uretilen birimler.

Her varyant baska bir aracin **mesru olarak** uretebilecegi bir yerlesimdir.
Amac kendi bicimlendiricimizin hic uretmedigi ama sahada karsimiza cikan
birimleri okuyucu/yazici/yedekleyicimize vermek (ADR 0094). PicoZed SD
kartindaki az kumeli FAT32 (`mkdosfs -F 32 -s 8`, 25 549 kume) bu sinifin
ilk ornegiydi: kendi bicimlendiricimiz FAT32'yi hep >= 65 525 kumeyle
urettigi icin testler onu hic gormedi.

Alanlar:
  id      benzersiz ad (rapor ve --sec icin)
  fs      fs anahtari (tests/long/verify FSCK / KERNEL_TYPE tablolariyla ayni)
  mb      bolum boyutu (MiB)
  cmd     arac komutu; {img} bolum dosyasi, {label} etiket, {root} doldurma
          klasoru (yalnizca `-d` gibi secenekler icin)
  blkid   blkid'in vermesi beklenen (TYPE, VERSION) — None: yalnizca TYPE
  note    neden bu varyant var
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class Variant:
    id: str
    fs: str
    mb: int
    cmd: List[str]
    note: str = ""
    label: str = "YABANCI"
    # mkfs'ten sonra uygulanacak duzeltme (tune2fs gibi); {img}
    post: List[List[str]] = field(default_factory=list)
    # en kucuk arac surumu gerekiyorsa aciklama (rapor icin)
    needs: str = ""


def _fat(id_, mb, args, note, label="YABANCI"):
    # -h 2048: bolum basi (gizli sektor) gercek bir bolumdeki gibi; -I: dosya
    # uzerinde calis. --mbr=no: mkfs.fat 4.2 dosyaya MBR yazmayi dener.
    return Variant(id_, args[0], mb,
                   ["mkfs.fat", "-I", "-h", "2048", "-n", "{label}"] + args[1:]
                   + ["{img}"], note, label)


VARIANTS: List[Variant] = [
    # ------------------------------------------------------------------ FAT32
    _fat("fat32-picozed", 100, ["fat32", "-F", "32", "-s", "8"],
         "PicoZed BOOT: 25549 kume (< 65525) — kume sayisiyla FAT16 saniliyordu"),
    _fat("fat32-s2-az", 64, ["fat32", "-F", "32", "-s", "2"],
         "~32 bin kume, FAT32"),
    _fat("fat32-s1", 128, ["fat32", "-F", "32", "-s", "1"], "512 B kume"),
    _fat("fat32-s64", 1024, ["fat32", "-F", "32", "-s", "64"],
         "32 KiB kume, ~32 bin kume"),
    _fat("fat32-s128", 2048, ["fat32", "-F", "32", "-s", "128"],
         "64 KiB kume (Windows'un kabul ettigi en buyuk)"),
    _fat("fat32-S4096", 256, ["fat32", "-F", "32", "-S", "4096", "-s", "1"],
         "mantiksal sektor 4096 (aygit 512): bps != aygit sektoru"),
    _fat("fat32-S2048", 256, ["fat32", "-F", "32", "-S", "2048"],
         "mantiksal sektor 2048"),
    _fat("fat32-f1", 128, ["fat32", "-F", "32", "-f", "1"], "tek FAT kopyasi"),
    _fat("fat32-R64-b12", 128, ["fat32", "-F", "32", "-R", "64", "-b", "12"],
         "64 ayrilmis sektor, yedek onyukleme 12. sektorde"),
    _fat("fat32-hizasiz", 128, ["fat32", "-F", "32", "-a"],
         "veri bolgesi hizalanmamis"),
    _fat("fat32-buyuk", 4096, ["fat32", "-F", "32"],
         "4 GiB varsayilan (1 milyon kume; FAT tablosu buyuk)"),
    _fat("fat32-f0", 128, ["fat32", "-F", "32", "--media-type=0xF0"],
         "ortam bayti 0xF0"),
    # ------------------------------------------------------------------ FAT16
    _fat("fat16-s1", 16, ["fat16", "-F", "16", "-s", "1"], "512 B kume"),
    _fat("fat16-s64", 2000, ["fat16", "-F", "16", "-s", "64"],
         "32 KiB kume, ~64 bin kume"),
    _fat("fat16-r1024", 64, ["fat16", "-F", "16", "-r", "1024"],
         "1024 kok dizin girisi"),
    _fat("fat16-r16", 64, ["fat16", "-F", "16", "-r", "16"],
         "16 kok dizin girisi (tek sektor) — kok dizin dolar"),
    _fat("fat16-S2048", 128, ["fat16", "-F", "16", "-S", "2048"],
         "mantiksal sektor 2048"),
    _fat("fat16-f1", 64, ["fat16", "-F", "16", "-f", "1"], "tek FAT kopyasi"),
    _fat("fat16-az", 8, ["fat16", "-F", "16", "-s", "2"],
         "4085'ten az kume ile FAT16 (cekirdek kume sayisiyla FAT12 der)"),
    # ------------------------------------------------------------------ FAT12
    _fat("fat12-4m", 4, ["fat12", "-F", "12"], "kucuk FAT12"),
    _fat("fat12-s8", 16, ["fat12", "-F", "12", "-s", "8"], "4 KiB kume"),
    _fat("fat12-S4096", 32, ["fat12", "-F", "12", "-S", "4096"],
         "mantiksal sektor 4096"),
    _fat("fat12-r512", 8, ["fat12", "-F", "12", "-r", "512"], "512 kok giris"),
    # ------------------------------------------------------------------ exFAT
    Variant("exfat-varsayilan", "exfat", 256, ["mkfs.exfat", "-L", "{label}", "{img}"],
            "exfatprogs varsayilani"),
    Variant("exfat-c512", "exfat", 64, ["mkfs.exfat", "-c", "512", "-L", "{label}", "{img}"],
            "sektor boyutunda kume"),
    Variant("exfat-c4k", "exfat", 128, ["mkfs.exfat", "-c", "4K", "-L", "{label}", "{img}"],
            "4 KiB kume"),
    Variant("exfat-c1m", "exfat", 1024, ["mkfs.exfat", "-c", "1M", "-L", "{label}", "{img}"],
            "1 MiB kume"),
    Variant("exfat-c32m", "exfat", 2048, ["mkfs.exfat", "-c", "32M", "-L", "{label}", "{img}"],
            "32 MiB kume (en buyuk), ~64 kume"),
    Variant("exfat-b4m", "exfat", 512, ["mkfs.exfat", "-b", "4M", "-L", "{label}", "{img}"],
            "kume yigini 4 MiB sinira hizali"),
    Variant("exfat-s4096", "exfat", 256, ["mkfs.exfat", "-s", "4096", "-L", "{label}", "{img}"],
            "sektor boyutu 4096", needs="exfatprogs -s"),
    Variant("exfat-unicode", "exfat", 128, ["mkfs.exfat", "-L", "{label}", "{img}"],
            "unicode etiket", label="ÇALIŞMA"),
    # ------------------------------------------------------------------- NTFS
    # mkntfs dosya uzerinde: -F zorla, -p/-H/-S geometri (bolum 2048'de).
    Variant("ntfs-varsayilan", "ntfs", 512, ["mkntfs", "-F", "-Q", "-p", "2048", "-H", "255",
                                             "-S", "63", "-L", "{label}", "{img}"],
            "mkntfs varsayilani (4 KiB kume)"),
    Variant("ntfs-c512", "ntfs", 256, ["mkntfs", "-F", "-Q", "-c", "512", "-p", "2048", "-H",
                                       "255", "-S", "63", "-L", "{label}", "{img}"],
            "512 B kume"),
    Variant("ntfs-c64k", "ntfs", 1024, ["mkntfs", "-F", "-Q", "-c", "65536", "-p", "2048", "-H",
                                        "255", "-S", "63", "-L", "{label}", "{img}"],
            "64 KiB kume"),
    Variant("ntfs-c2m", "ntfs", 2048, ["mkntfs", "-F", "-Q", "-c", "2097152", "-p", "2048",
                                       "-H", "255", "-S", "63", "-L", "{label}", "{img}"],
            "2 MiB kume (spc kodlamasi > 0x80)"),
    Variant("ntfs-s4096", "ntfs", 512, ["mkntfs", "-F", "-Q", "-s", "4096", "-p", "2048", "-H",
                                        "255", "-S", "63", "-L", "{label}", "{img}"],
            "sektor boyutu 4096 (aygit 512)"),
    # -------------------------------------------------------------------- ext
    Variant("ext2-b1024", "ext2", 128, ["mke2fs", "-q", "-F", "-t", "ext2", "-b", "1024",
                                        "-L", "{label}", "{img}"],
            "1 KiB blok, first_data_block=1, dolaylı bloklar"),
    Variant("ext2-b4096-dizinsiz", "ext2", 256, ["mke2fs", "-q", "-F", "-t", "ext2", "-O",
                                                 "^dir_index", "-L", "{label}", "{img}"],
            "dir_index yok"),
    Variant("ext3-b2048", "ext3", 256, ["mke2fs", "-q", "-F", "-t", "ext3", "-b", "2048",
                                        "-L", "{label}", "{img}"],
            "2 KiB blok, gunluk"),
    Variant("ext4-varsayilan", "ext4", 512, ["mke2fs", "-q", "-F", "-t", "ext4",
                                             "-L", "{label}", "{img}"], "mke2fs varsayilani"),
    Variant("ext4-b1024", "ext4", 256, ["mke2fs", "-q", "-F", "-t", "ext4", "-b", "1024",
                                        "-L", "{label}", "{img}"], "1 KiB blok ext4"),
    Variant("ext4-64bit-metabg", "ext4", 512, ["mke2fs", "-q", "-F", "-t", "ext4", "-O",
                                               "64bit,meta_bg,^resize_inode",
                                               "-L", "{label}", "{img}"],
            "meta_bg: GDT gruplara dagitik"),
    Variant("ext4-bigalloc", "ext4", 1024, ["mke2fs", "-q", "-F", "-t", "ext4", "-O", "bigalloc",
                                            "-C", "65536", "-L", "{label}", "{img}"],
            "bigalloc: bitmap biti kume (64 KiB)"),
    Variant("ext4-inline", "ext4", 256, ["mke2fs", "-q", "-F", "-t", "ext4", "-O", "inline_data",
                                         "-L", "{label}", "{img}"],
            "kucuk dosyalar inode icinde"),
    Variant("ext4-gdtcsum", "ext4", 512, ["mke2fs", "-q", "-F", "-t", "ext4", "-O",
                                          "^metadata_csum,uninit_bg", "-L", "{label}", "{img}"],
            "gdt_csum (crc16), metadata_csum yok"),
    Variant("ext4-flexsiz", "ext4", 512, ["mke2fs", "-q", "-F", "-t", "ext4", "-O", "^flex_bg",
                                          "-L", "{label}", "{img}"],
            "flex_bg yok: metaveri her grubun kendisinde"),
    Variant("ext4-G4", "ext4", 512, ["mke2fs", "-q", "-F", "-t", "ext4", "-G", "4",
                                     "-L", "{label}", "{img}"], "flex_bg 4 grup"),
    Variant("ext4-I128", "ext4", 256, ["mke2fs", "-q", "-F", "-t", "ext4", "-I", "128",
                                       "-L", "{label}", "{img}"],
            "128 bayt inode (extra_isize yok)"),
    Variant("ext4-I1024", "ext4", 256, ["mke2fs", "-q", "-F", "-t", "ext4", "-I", "1024",
                                        "-L", "{label}", "{img}"], "1024 bayt inode"),
    Variant("ext4-gunluksuz", "ext4", 256, ["mke2fs", "-q", "-F", "-t", "ext4", "-O",
                                            "^has_journal", "-L", "{label}", "{img}"],
            "gunluksuz ext4"),
    Variant("ext4-sparse2", "ext4", 512, ["mke2fs", "-q", "-F", "-t", "ext4", "-O",
                                          "sparse_super2", "-L", "{label}", "{img}"],
            "sparse_super2: yedek ustblok yalnizca 2 grupta"),
    Variant("ext4-extentsiz", "ext4", 256, ["mke2fs", "-q", "-F", "-t", "ext4", "-O",
                                            "^extent,^64bit", "-L", "{label}", "{img}"],
            "ext4 ama extent yok (dolayli bloklar)"),
    Variant("ext4-casefold", "ext4", 256, ["mke2fs", "-q", "-F", "-t", "ext4", "-O", "casefold",
                                           "-E", "encoding=utf8", "-L", "{label}", "{img}"],
            "buyuk/kucuk harf duyarsiz dizinler"),
    Variant("ext4-encrypt", "ext4", 256, ["mke2fs", "-q", "-F", "-t", "ext4", "-O", "encrypt",
                                          "-L", "{label}", "{img}"],
            "sifreleme ozelligi acik (dosyalar sifresiz)"),
    Variant("ext4-largedir", "ext4", 256, ["mke2fs", "-q", "-F", "-t", "ext4", "-O", "large_dir",
                                           "-L", "{label}", "{img}"], "3 seviyeli htree izni"),
    Variant("ext4-quota", "ext4", 256, ["mke2fs", "-q", "-F", "-t", "ext4", "-O",
                                        "quota,project", "-L", "{label}", "{img}"],
            "kota inode'lari"),
    Variant("ext4-orphanfile", "ext4", 256, ["mke2fs", "-q", "-F", "-t", "ext4", "-O",
                                             "orphan_file", "-L", "{label}", "{img}"],
            "orphan_file (e2fsprogs 1.47)", needs="e2fsprogs >= 1.47"),
    Variant("ext4-N64", "ext4", 128, ["mke2fs", "-q", "-F", "-t", "ext4", "-N", "64",
                                      "-L", "{label}", "{img}"],
            "cok az inode (dolunca yazma reddedilmeli)"),
    Variant("ext4-tembelsiz", "ext4", 512, ["mke2fs", "-q", "-F", "-t", "ext4", "-E",
                                            "lazy_itable_init=0,lazy_journal_init=0",
                                            "-L", "{label}", "{img}"],
            "inode tablolari sifirlanmis (ITABLE_ZEROED)"),
    Variant("ext4-huge", "ext4", 1024, ["mke2fs", "-q", "-F", "-t", "ext4", "-T", "huge",
                                        "-L", "{label}", "{img}"], "-T huge (seyrek inode)"),
    # ---------------------------------------------------------------- digerleri
    Variant("xfs-varsayilan", "xfs", 512, ["mkfs.xfs", "-q", "-f", "-L", "{label}", "{img}"],
            "mkfs.xfs varsayilani (v5)", label="YABANCI"),
    Variant("xfs-b1024", "xfs", 512, ["mkfs.xfs", "-q", "-f", "-b", "size=1024",
                                      "-L", "{label}", "{img}"], "1 KiB blok"),
    Variant("hfsplus-varsayilan", "hfsplus", 256, ["mkfs.hfsplus", "-v", "{label}", "{img}"],
            "hfsprogs, gunluksuz"),
    Variant("hfsplus-gunluk", "hfsplus", 256, ["mkfs.hfsplus", "-J", "-v", "{label}", "{img}"],
            "gunluklu HFS+ (cekirdek salt okunur baglar)"),
    Variant("udf-b512", "udf", 256, ["mkudffs", "--media-type=hd", "--blocksize=512",
                                     "--label={label}", "{img}"], "UDF 512 B blok"),
    Variant("udf-b2048", "udf", 256, ["mkudffs", "--media-type=hd", "--blocksize=2048",
                                      "--label={label}", "{img}"], "UDF 2 KiB blok"),
    Variant("btrfs-varsayilan", "btrfs", 512, ["mkfs.btrfs", "-q", "-f", "-L", "{label}",
                                               "{img}"], "btrfs (okuma/yedek)"),
    Variant("f2fs-varsayilan", "f2fs", 512, ["mkfs.f2fs", "-q", "-f", "-l", "{label}",
                                             "{img}"], "F2FS (tespit/yedek)"),
]

# fs anahtari -> blkid TYPE
BLKID_TYPE = {
    "fat12": "vfat", "fat16": "vfat", "fat32": "vfat", "exfat": "exfat",
    "ntfs": "ntfs", "ext2": "ext2", "ext3": "ext3", "ext4": "ext4",
    "xfs": "xfs", "hfsplus": "hfsplus", "udf": "udf", "btrfs": "btrfs",
    "f2fs": "f2fs",
}

# blkid (TYPE, VERSION) -> fsdetect'in beklenen adi. vfat icin surum blkid'in
# kendi karari degil cekirdekle ayni kuraldir (fat_length == 0 -> FAT32,
# degilse kume sayisi); bu yuzden referans olarak alinir.
def expected_name(blkid_type: str, blkid_version: str) -> Optional[str]:
    if blkid_type == "vfat":
        return blkid_version or None            # FAT12 / FAT16 / FAT32
    return {"exfat": "exFAT", "ntfs": "NTFS", "ext2": "ext2", "ext3": "ext3",
            "ext4": "ext4", "xfs": "XFS", "hfsplus": "HFS+", "udf": "UDF",
            "btrfs": "btrfs", "f2fs": "F2FS"}.get(blkid_type)


def select(names: str) -> List[Variant]:
    """--sec: virgulle id ya da onek (ornek: fat32,ext4-b1024). Bos: hepsi."""
    if not names or names == "all":
        return list(VARIANTS)
    keys = [n.strip() for n in names.split(",") if n.strip()]
    return [v for v in VARIANTS
            if any(v.id == k or v.id.startswith(k + "-") or v.fs == k for k in keys)]


def groups() -> List[Tuple[str, str]]:
    """CI matrisi icin gruplar: (ad, --sec degeri). ext4 iki ise bolunur."""
    ext4 = [v.id for v in VARIANTS if v.fs == "ext4"]
    half = len(ext4) // 2
    return [
        ("fat32", "fat32"), ("fat16-12", "fat16,fat12"), ("exfat", "exfat"),
        ("ntfs", "ntfs"), ("ext2-3", "ext2,ext3"),
        ("ext4-a", ",".join(ext4[:half])), ("ext4-b", ",".join(ext4[half:])),
        ("diger", "xfs,hfsplus,udf,btrfs,f2fs"),
    ]
