"""Bolum tablosu ortak modeli (seremoniden bagimsiz).

MBR ve GPT uygulamalari bu modeldeki `Partition` nesnelerini uretir/tuketir.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

MIB = 1024 * 1024
ALIGN_BYTES = 1 * MIB  # bolum hizalama: 1 MiB (modern standart)


class PartitionTableError(Exception):
    pass


# --- MBR tip kodlari ------------------------------------------------------
MBR_TYPES = {
    0x00: "Bos",
    0x01: "FAT12",
    0x04: "FAT16 (<32M)",
    0x05: "Genisletilmis (CHS)",
    0x06: "FAT16",
    0x07: "NTFS / exFAT",
    0x0B: "FAT32 (CHS)",
    0x0C: "FAT32 (LBA)",
    0x0E: "FAT16 (LBA)",
    0x0F: "Genisletilmis (LBA)",
    0x11: "Gizli FAT12",
    0x16: "Gizli FAT16",
    0x1B: "Gizli FAT32",
    0x1C: "Gizli FAT32 (LBA)",
    0x27: "Windows Kurtarma",
    0x42: "Windows Dinamik",
    0x82: "Linux Takas",
    0x83: "Linux",
    0x85: "Linux Genisletilmis",
    0x8E: "Linux LVM",
    0xA5: "FreeBSD",
    0xAF: "Mac OS X HFS+",
    0xEE: "GPT Koruyucu",
    0xEF: "EFI Sistem (FAT)",
    0xFD: "Linux RAID",
}
MBR_EXTENDED_TYPES = (0x05, 0x0F, 0x85)

# --- GPT tip GUID'leri ----------------------------------------------------
GPT_UNUSED = "00000000-0000-0000-0000-000000000000"
GPT_TYPES = {
    GPT_UNUSED: "Bos",
    "C12A7328-F81F-11D2-BA4B-00A0C93EC93B": "EFI Sistem Bolumu",
    "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7": "Microsoft Temel Veri",
    "E3C9E316-0B5C-4DB8-817D-F92DF00215AE": "Microsoft Ayrilmis (MSR)",
    "DE94BBA4-06D1-4D40-A16A-BFD50179D6AC": "Windows Kurtarma",
    "0FC63DAF-8483-4772-8E79-3D69D8477DE4": "Linux Dosya Sistemi",
    "0657FD6D-A4AB-43C4-84E5-0933C84B4F4F": "Linux Takas",
    "E6D6D379-F507-44C2-A23C-238F2A3DF928": "Linux LVM",
    "933AC7E1-2EB4-4F13-B844-0E14E2AEF915": "Linux /home",
    "21686148-6449-6E6F-744E-656564454649": "BIOS Onyukleme",
    "48465300-0000-11AA-AA11-00306543ECAC": "Apple HFS+",
    "516E7CB4-6ECF-11D6-8FF8-00022D09712B": "FreeBSD",
}


@dataclass
class Partition:
    """Semadan bagimsiz bolum tanimi."""

    index: int                       # 1 tabanli sira numarasi
    start_lba: int
    sector_count: int
    scheme: str = "mbr"              # 'mbr' | 'gpt'
    type_id: int = 0x83              # MBR tip baytı
    type_guid: str = GPT_UNUSED      # GPT tip GUID'i
    part_guid: str = GPT_UNUSED      # GPT benzersiz GUID'i
    name: str = ""                   # GPT bolum adi
    bootable: bool = False
    logical: bool = False            # MBR mantiksal bolum mu
    ebr_lba: int = 0                 # mantiksal bolumun EBR sektoru
    attributes: int = 0              # GPT oznitelik bayraklari
    fs_type: str = ""                # tespit edilen dosya sistemi
    fs_label: str = ""               # dosya sistemi etiketi
    fs_used: int = -1                # kullanilan bayt (-1 = bilinmiyor)
    fs_total: int = -1               # toplam bayt (-1 = bilinmiyor)
    sector_size: int = 512

    @property
    def end_lba(self) -> int:
        return self.start_lba + self.sector_count - 1

    @property
    def size(self) -> int:
        return self.sector_count * self.sector_size

    @property
    def type_name(self) -> str:
        if self.scheme == "gpt":
            return GPT_TYPES.get(self.type_guid.upper(), "Bilinmeyen")
        return MBR_TYPES.get(self.type_id, f"0x{self.type_id:02X}")

    @property
    def display_name(self) -> str:
        if self.name:
            return self.name
        if self.fs_label:
            return self.fs_label
        return f"Bolum {self.index}"

    def overlaps(self, start_lba: int, sector_count: int) -> bool:
        return not (start_lba + sector_count <= self.start_lba
                    or start_lba > self.end_lba)


@dataclass
class FreeRegion:
    """Bolumlenmemis bos alan."""
    start_lba: int
    sector_count: int
    sector_size: int = 512

    @property
    def end_lba(self) -> int:
        return self.start_lba + self.sector_count - 1

    @property
    def size(self) -> int:
        return self.sector_count * self.sector_size


class PartitionTable:
    """MBR/GPT uygulamalari icin ortak taban."""

    scheme = "none"

    def __init__(self, device):
        self.device = device
        self.partitions: List[Partition] = []

    # -- alt siniflarin uygulamasi gerekenler --------------------------------
    def write(self) -> None:  # pragma: no cover - arayuz
        raise NotImplementedError

    def add_partition(self, start_lba: int, sector_count: int, **kwargs) -> Partition:  # pragma: no cover
        raise NotImplementedError

    def delete_partition(self, index: int) -> None:  # pragma: no cover
        raise NotImplementedError

    # -- ortak yardimcilar ---------------------------------------------------
    @property
    def sector_size(self) -> int:
        return self.device.sector_size

    @property
    def align_sectors(self) -> int:
        return max(1, ALIGN_BYTES // self.sector_size)

    def first_usable_lba(self) -> int:
        return self.align_sectors

    def last_usable_lba(self) -> int:
        return self.device.sector_count - 1

    def align_up(self, lba: int) -> int:
        a = self.align_sectors
        return ((lba + a - 1) // a) * a

    def align_down(self, lba: int) -> int:
        a = self.align_sectors
        return (lba // a) * a

    def get(self, index: int) -> Partition:
        for p in self.partitions:
            if p.index == index:
                return p
        raise PartitionTableError(f"{index} numarali bolum yok")

    def sorted_partitions(self) -> List[Partition]:
        return sorted(self.partitions, key=lambda p: p.start_lba)

    def free_regions(self, min_sectors: int = 1) -> List[FreeRegion]:
        """Kullanilabilir bos alan araliklarini dondurur (hizalanmis)."""
        regions: List[FreeRegion] = []
        cursor = self.first_usable_lba()
        limit = self.last_usable_lba()
        for p in self.sorted_partitions():
            if p.logical:
                continue  # mantiksal bolumler genisletilmis bolumun icinde
            if p.start_lba > cursor:
                start = self.align_up(cursor)
                end = p.start_lba - 1
                if end >= start and (end - start + 1) >= min_sectors:
                    regions.append(FreeRegion(start, end - start + 1, self.sector_size))
            cursor = max(cursor, p.end_lba + 1)
        if cursor <= limit:
            start = self.align_up(cursor)
            if limit >= start and (limit - start + 1) >= min_sectors:
                regions.append(FreeRegion(start, limit - start + 1, self.sector_size))
        return regions

    def largest_free_region(self) -> Optional[FreeRegion]:
        regions = self.free_regions()
        return max(regions, key=lambda r: r.sector_count) if regions else None

    def check_range(self, start_lba: int, sector_count: int,
                    ignore_index: int = -1) -> None:
        """Verilen araligin gecerli ve bos oldugunu dogrular."""
        if sector_count <= 0:
            raise PartitionTableError("Bolum boyutu sifir olamaz")
        if start_lba < self.first_usable_lba():
            raise PartitionTableError(
                f"Baslangic cok erken (en az LBA {self.first_usable_lba()})")
        if start_lba + sector_count - 1 > self.last_usable_lba():
            raise PartitionTableError("Bolum disk sonunu asiyor")
        for p in self.partitions:
            if p.index == ignore_index:
                continue
            if p.overlaps(start_lba, sector_count):
                raise PartitionTableError(
                    f"{p.index} numarali bolum ile cakisiyor")

    def total_used_sectors(self) -> int:
        return sum(p.sector_count for p in self.partitions if not p.logical)

    def _renumber(self) -> None:
        for i, p in enumerate(sorted(self.partitions, key=lambda x: (x.logical, x.start_lba)), 1):
            p.index = i


def human_size(nbytes: float) -> str:
    """Bayti okunabilir metne cevirir (GUI ve gunlukler icin)."""
    if nbytes < 0:
        return "-"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    i = 0
    v = float(nbytes)
    while v >= 1024 and i < len(units) - 1:
        v /= 1024.0
        i += 1
    if i == 0:
        return f"{int(v)} {units[i]}"
    return f"{v:.2f} {units[i]}"


def parse_size(text: str, default_unit: str = "MB") -> int:
    """'512MB', '1.5 GB', '2048' gibi metinleri bayta cevirir."""
    text = (text or "").strip().upper().replace(",", ".")
    if not text:
        raise ValueError("Bos boyut")
    mult = {"B": 1, "KB": 1024, "K": 1024, "MB": 1024 ** 2, "M": 1024 ** 2,
            "GB": 1024 ** 3, "G": 1024 ** 3, "TB": 1024 ** 4, "T": 1024 ** 4}
    unit = default_unit
    for suffix in ("TB", "GB", "MB", "KB", "T", "G", "M", "K", "B"):
        if text.endswith(suffix):
            unit = suffix
            text = text[: -len(suffix)].strip()
            break
    return int(float(text) * mult[unit])
