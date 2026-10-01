"""Dosya sistemi kutugu — her dosya sistemi TEK bir tanimla kaydolur.

Neden: yeni bir dosya sistemi eklemek 13 ayri yere dokunmayi gerektiriyordu
(renk, MBR/GPT turu, isletim sistemi tahmini, gorunen ad, ...) ve biri
unutulunca dosya sistemi yarim gorunuyordu — takas "planview"da vardi,
bicim listesinde yoktu; "Bilinmeyen" ve "Linux Takas" adlari cevrilmeden
gosteriliyor, Ingilizce/Almanca arayuzde Turkce kaliyordu.

Kurallar:
  * `key` **veridir**: `fsdetect` ciktisi, `Partition.fs_type`. Cevrilmez,
    karsilastirmalarda kullanilir.
  * `display` gorunen addir; `fs_display()` gosterim aninda cevirir
    (ADR 0027: "gorunen metin karar girdisi degildir").
  * Renkler anlamsaldir (CLAUDE.md: dosya sistemi renkleri sabit olabilir).

Tureyen tablolar: `convert.FS_TO_MBR/FS_TO_GPT`, `ui.theme.FS_COLORS`,
`physical.guess_os_from_partitions`. Bicimlendirme yetenegi ayrica
`formatter.FS_KINDS`tadir (bicimlendirilebilen alt kume).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from ..i18n import mark, tr

MSBASIC = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
LINUXFS = "0FC63DAF-8483-4772-8E79-3D69D8477DE4"
SWAP_GUID = "0657FD6D-A4AB-43C4-84E5-0933C84B4F4F"
APPLE_HFS = "48465300-0000-11AA-AA11-00306543ECAC"
APPLE_APFS = "7C3457EF-0000-11AA-AA11-00306543ECAC"
LINUX_LVM = "E6D6D379-F507-44C2-A23C-238F2A3DF928"
LINUX_RAID = "A19D880F-05FC-4D3B-A006-743F0F84911E"
LINUX_LUKS = "CA7D7CCB-63ED-4C53-861C-1742536059CC"

UNKNOWN = "Bilinmeyen"            # fsdetect'in "imza var ama tanimadim" degeri
SWAP = "Linux Takas"


@dataclass(frozen=True)
class FsSpec:
    key: str                  # fsdetect ciktisi ('FAT32', 'ext4', 'Linux Takas')
    display: str              # gorunen ad (mark ile isaretli ise cevrilir)
    color: str                # harita/tablo rengi
    mbr_type: int = 0         # bulunan/kurtarilan bolum icin MBR tur bayti
    gpt_type: str = ""        # ... ve GPT tur GUID'i
    family: str = ""          # 'windows' | 'linux' | 'macos' | '' (ipucu)


REGISTRY: List[FsSpec] = [
    FsSpec("FAT12", "FAT12", "#7fb069", 0x01, MSBASIC, ""),
    FsSpec("FAT16", "FAT16", "#5a9e4b", 0x0E, MSBASIC, ""),
    FsSpec("FAT32", "FAT32", "#3d7fd6", 0x0C, MSBASIC, ""),
    FsSpec("exFAT", "exFAT", "#2f9e9e", 0x07, MSBASIC, ""),
    FsSpec("NTFS", "NTFS", "#7a5bd6", 0x07, MSBASIC, "windows"),
    FsSpec("ReFS", "ReFS", "#5a4bb0", 0x07, MSBASIC, "windows"),
    FsSpec("BitLocker", "BitLocker", "#3b3f8c", 0x07, MSBASIC, "windows"),
    FsSpec("ext2", "ext2", "#d98324", 0x83, LINUXFS, "linux"),
    FsSpec("ext3", "ext3", "#d9731f", 0x83, LINUXFS, "linux"),
    FsSpec("ext4", "ext4", "#d4621a", 0x83, LINUXFS, "linux"),
    FsSpec("btrfs", "btrfs", "#c04a3f", 0x83, LINUXFS, "linux"),
    FsSpec("XFS", "XFS", "#b0543e", 0x83, LINUXFS, "linux"),
    FsSpec("F2FS", "F2FS", "#a0522d", 0x83, LINUXFS, "linux"),
    FsSpec("JFS", "JFS", "#c0784a", 0x83, LINUXFS, "linux"),
    FsSpec("ReiserFS", "ReiserFS", "#b8864f", 0x83, LINUXFS, "linux"),
    FsSpec("bcachefs", "bcachefs", "#a65f3f", 0x83, LINUXFS, "linux"),
    FsSpec("bcache", "bcache", "#8f5a44", 0x83, LINUXFS, "linux"),
    FsSpec("NILFS2", "NILFS2", "#a8704f", 0x83, LINUXFS, "linux"),
    FsSpec("EROFS", "EROFS", "#9a7a5a", 0x83, LINUXFS, "linux"),
    FsSpec("SquashFS", "SquashFS", "#8a9a5b", 0x83, LINUXFS, "linux"),
    FsSpec("Minix", "Minix", "#9c8a6a", 0x81, LINUXFS, "linux"),
    FsSpec(SWAP, mark("Linux Takas"), "#8e8e93", 0x82, SWAP_GUID, "linux"),
    FsSpec("LUKS", "LUKS", "#3d5a3d", 0x83, LINUX_LUKS, "linux"),   # bilinmeyen surum
    FsSpec("LUKS1", "LUKS1", "#3d5a3d", 0x83, LINUX_LUKS, "linux"),
    FsSpec("LUKS2", "LUKS2", "#3d5a3d", 0x83, LINUX_LUKS, "linux"),
    FsSpec("LVM2", "LVM2", "#6b5b3e", 0x8E, LINUX_LVM, "linux"),
    FsSpec("Linux RAID", "Linux RAID", "#7a4e3a", 0xFD, LINUX_RAID, "linux"),
    FsSpec("ZFS", "ZFS", "#4f7f8f", 0xBF, "6A898CC3-1DD2-11B2-99A6-080020736631", ""),
    FsSpec("HFS+", "HFS+", "#8c8c99", 0xAF, APPLE_HFS, "macos"),
    FsSpec("HFSX", "HFSX", "#7d7d8a", 0xAF, APPLE_HFS, "macos"),
    FsSpec("HFS", "HFS", "#9c9ca8", 0xAF, APPLE_HFS, "macos"),
    FsSpec("APFS", "APFS", "#6e6e7a", 0xAF, APPLE_APFS, "macos"),
    FsSpec("CoreStorage", "CoreStorage", "#4a4a57", 0xAF,
           "53746F72-6167-11AA-AA11-00306543ECAC", "macos"),
    FsSpec("UDF", "UDF", "#5f86b3", 0x07, MSBASIC, ""),
    FsSpec("ISO9660", "ISO 9660", "#6a7fa0", 0x00, "", ""),
    FsSpec(UNKNOWN, mark("Bilinmeyen"), "#9aa5b1", 0, "", ""),
]

BY_KEY: Dict[str, FsSpec] = {s.key: s for s in REGISTRY}
UNFORMATTED_COLOR = "#b6bfc9"


def spec(fs_type: str):
    return BY_KEY.get(fs_type or "")


def fs_display(fs_type: str) -> str:
    """Gorunen ad (etkin dilde). Bilinmeyen anahtar oldugu gibi doner."""
    s = BY_KEY.get(fs_type or "")
    if s is None:
        return fs_type or ""
    return tr(s.display)


def fs_color(fs_type: str) -> str:
    s = BY_KEY.get(fs_type or "")
    if s is None:
        return UNFORMATTED_COLOR if not fs_type else BY_KEY[UNKNOWN].color
    return s.color


def colors() -> Dict[str, str]:
    out = {s.key: s.color for s in REGISTRY}
    out[""] = UNFORMATTED_COLOR
    return out


def mbr_types() -> Dict[str, int]:
    return {s.key: s.mbr_type for s in REGISTRY if s.mbr_type}


def gpt_types() -> Dict[str, str]:
    return {s.key: s.gpt_type for s in REGISTRY if s.gpt_type}


def family_keys(family: str) -> set:
    """Aileye ait anahtarlar, kucuk harfle (OS tahmini kucuk harfle karsilastirir)."""
    return {s.key.lower() for s in REGISTRY if s.family == family}
