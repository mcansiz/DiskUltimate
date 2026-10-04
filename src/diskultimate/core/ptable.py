"""Bolum tablosu ortak modeli (seremoniden bagimsiz).

MBR ve GPT uygulamalari bu modeldeki `Partition` nesnelerini uretir/tuketir.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from ..i18n import mark, tr
from ..i18n import tr

MIB = 1024 * 1024
ALIGN_BYTES = 1 * MIB  # bolum hizalama: 1 MiB (modern standart)


class PartitionTableError(Exception):
    pass


# --- MBR tip kodlari ------------------------------------------------------
MBR_TYPES = {
    0x00: mark("Bos"),
    0x01: mark("FAT12"),
    0x04: mark("FAT16 (<32M)"),
    0x05: mark("Genisletilmis (CHS)"),
    0x06: mark("FAT16"),
    0x07: mark("NTFS / exFAT"),
    0x0B: mark("FAT32 (CHS)"),
    0x0C: mark("FAT32 (LBA)"),
    0x0E: mark("FAT16 (LBA)"),
    0x0F: mark("Genisletilmis (LBA)"),
    0x11: mark("Gizli FAT12"),
    0x16: mark("Gizli FAT16"),
    0x1B: mark("Gizli FAT32"),
    0x1C: mark("Gizli FAT32 (LBA)"),
    0x27: mark("Windows Kurtarma"),
    0x42: mark("Windows Dinamik"),
    0x82: mark("Linux Takas"),
    0x83: mark("Linux"),
    0x85: mark("Linux Genisletilmis"),
    0x8E: mark("Linux LVM"),
    0xA5: mark("FreeBSD"),
    0xAF: mark("Mac OS X HFS+"),
    0xEE: mark("GPT Koruyucu"),
    0xEF: mark("EFI Sistem (FAT)"),
    0xFD: mark("Linux RAID"),
}
MBR_EXTENDED_TYPES = (0x05, 0x0F, 0x85)

# --- GPT tip GUID'leri ----------------------------------------------------
GPT_UNUSED = "00000000-0000-0000-0000-000000000000"
GPT_TYPES = {
    GPT_UNUSED: mark("Bos"),
    "C12A7328-F81F-11D2-BA4B-00A0C93EC93B": mark("EFI Sistem Bolumu"),
    "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7": mark("Microsoft Temel Veri"),
    "E3C9E316-0B5C-4DB8-817D-F92DF00215AE": mark("Microsoft Ayrilmis (MSR)"),
    "DE94BBA4-06D1-4D40-A16A-BFD50179D6AC": mark("Windows Kurtarma"),
    "0FC63DAF-8483-4772-8E79-3D69D8477DE4": mark("Linux Dosya Sistemi"),
    "0657FD6D-A4AB-43C4-84E5-0933C84B4F4F": mark("Linux Takas"),
    "E6D6D379-F507-44C2-A23C-238F2A3DF928": mark("Linux LVM"),
    "933AC7E1-2EB4-4F13-B844-0E14E2AEF915": mark("Linux /home"),
    "21686148-6449-6E6F-744E-656564454649": mark("BIOS Onyukleme"),
    "48465300-0000-11AA-AA11-00306543ECAC": mark("Apple HFS+"),
    "516E7CB4-6ECF-11D6-8FF8-00022D09712B": mark("FreeBSD"),
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
    # Isletim sisteminin bu bolumu bagladigi yer: Linux/macOS'ta dizin
    # ("/media/pc/VERI"), Windows'ta surucu harfi ("E:"). Bos = bagli degil.
    # Diskten okunmaz; fiziksel disk taramasindan gelir (ADR 0043).
    mount_point: str = ""
    # Bolumde kurulu isletim sistemi ("Ubuntu 24.04", "Windows 11 (22631)")
    # ve turu (windows | linux | macos | esp | ""). Diskten okunmaz; arka
    # planda dosya sistemi icinden bulunur (`bootloader.partition_os`,
    # `ui/osinfo.py`).
    os_name: str = ""
    os_kind: str = ""
    sector_size: int = 512
    # Bekleyen islem onizlemesinde bu bolumun durumu ("" = diskteki hali).
    # Degerler `planview.STATE_*`; diskten okunan bolumlerde hep bostur.
    plan_state: str = ""

    @property
    def end_lba(self) -> int:
        return self.start_lba + self.sector_count - 1

    @property
    def size(self) -> int:
        return self.sector_count * self.sector_size

    @property
    def type_name(self) -> str:
        if self.scheme == "gpt":
            return tr(GPT_TYPES.get(self.type_guid.upper(), mark("Bilinmeyen")))
        return tr(MBR_TYPES.get(self.type_id, f"0x{self.type_id:02X}"))

    @property
    def display_name(self) -> str:
        if self.name:
            return self.name
        if self.fs_label:
            return self.fs_label
        return tr("Bolum {}", self.index)

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
        raise PartitionTableError(tr("{} numarali bolum yok", index))

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
            raise PartitionTableError(tr("Bolum boyutu sifir olamaz"))
        if start_lba < self.first_usable_lba():
            raise PartitionTableError(
                tr("Baslangic cok erken (en az LBA {})", self.first_usable_lba()))
        if start_lba + sector_count - 1 > self.last_usable_lba():
            raise PartitionTableError(tr("Bolum disk sonunu asiyor"))
        for p in self.partitions:
            if p.index == ignore_index:
                continue
            if p.overlaps(start_lba, sector_count):
                raise PartitionTableError(
                    tr("{} numarali bolum ile cakisiyor", p.index))

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
        raise ValueError(tr("Bos boyut"))
    mult = {"B": 1, "KB": 1024, "K": 1024, "MB": 1024 ** 2, "M": 1024 ** 2,
            "GB": 1024 ** 3, "G": 1024 ** 3, "TB": 1024 ** 4, "T": 1024 ** 4}
    unit = default_unit
    for suffix in ("TB", "GB", "MB", "KB", "T", "G", "M", "K", "B"):
        if text.endswith(suffix):
            unit = suffix
            text = text[: -len(suffix)].strip()
            break
    return int(float(text) * mult[unit])


class WholeDiskTable(PartitionTable):
    """Bolum tablosu olmayan, tum diski tek dosya sistemi kaplayan disk.

    Tablosuz USB bellek ("super disket"), duz `.iso`, `mkfs` ile uretilmis
    `ext4.img` gibi dosya sistemi goruntuleri. Arayuz diski tek bir bolum
    olarak gosterir; icerik okunur, bicimlendirilir, yedeklenir. Tabloya
    **yazilacak** bir sey yoktur: bolum ekleme/silme reddedilir, `write()`
    hicbir sey yapmaz (tur bilgisi saklanacak yer yok).
    """

    scheme = "none"

    def __init__(self, device):
        super().__init__(device)
        self.partitions = [Partition(index=1, start_lba=0,
                                     sector_count=device.sector_count,
                                     scheme="none", type_id=0,
                                     type_guid=GPT_UNUSED,
                                     sector_size=device.sector_size)]

    def write(self) -> None:
        return None

    def _refuse(self):
        raise PartitionTableError(tr(
            "Bu diskte bolum tablosu yok; dosya sistemi tum diski kapliyor. "
            "Bolum eklemek/silmek icin once bolum tablosu olusturun (icindeki "
            "dosya sistemi silinir)."))

    def add_partition(self, start_lba: int, sector_count: int, **kwargs) -> Partition:
        self._refuse()

    def delete_partition(self, index: int) -> None:
        self._refuse()

    def first_usable_lba(self) -> int:
        return 0

    def free_regions(self, min_sectors: int = 1) -> List[FreeRegion]:
        return []
