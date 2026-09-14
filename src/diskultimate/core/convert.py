"""Bolum tablosu semasi donusumu: MBR <-> GPT (veri kaybi olmadan).

Donusum yalnizca **tabloyu** yeniden yazar; bolumlerin diskteki yeri ve icerigi
degismez. Bu yuzden islem oncesi uygunluk denetimi sarttir: hedef sema kaynak
yerlesimi tasiyamiyorsa donusum yapilmaz.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from .gpt import GPTTable
from .image import BlockDevice
from .mbr import MAX_MBR_SECTORS, MBRTable
from .ptable import (GPT_UNUSED, MBR_EXTENDED_TYPES, Partition, PartitionTable,
                     PartitionTableError, human_size)

MSBASIC = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
LINUXFS = "0FC63DAF-8483-4772-8E79-3D69D8477DE4"

# MBR tip bayti -> GPT tur GUID'i
MBR_TO_GPT = {
    0x01: MSBASIC, 0x04: MSBASIC, 0x06: MSBASIC, 0x07: MSBASIC,
    0x0B: MSBASIC, 0x0C: MSBASIC, 0x0E: MSBASIC,
    0x11: MSBASIC, 0x16: MSBASIC, 0x1B: MSBASIC, 0x1C: MSBASIC,
    0x27: "DE94BBA4-06D1-4D40-A16A-BFD50179D6AC",   # Windows Kurtarma
    0x82: "0657FD6D-A4AB-43C4-84E5-0933C84B4F4F",   # Linux takas
    0x83: LINUXFS,
    0x8E: "E6D6D379-F507-44C2-A23C-238F2A3DF928",   # Linux LVM
    0xA5: "516E7CB4-6ECF-11D6-8FF8-00022D09712B",   # FreeBSD
    0xAF: "48465300-0000-11AA-AA11-00306543ECAC",   # Apple HFS+
    0xEF: "C12A7328-F81F-11D2-BA4B-00A0C93EC93B",   # EFI Sistem
    0xFD: "A19D880F-05FC-4D3B-A006-743F0F84911E",   # Linux RAID
}

# GPT tur GUID'i -> MBR tip bayti
GPT_TO_MBR = {
    "C12A7328-F81F-11D2-BA4B-00A0C93EC93B": 0xEF,
    "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7": 0x07,
    "E3C9E316-0B5C-4DB8-817D-F92DF00215AE": 0x0C,
    "DE94BBA4-06D1-4D40-A16A-BFD50179D6AC": 0x27,
    "0FC63DAF-8483-4772-8E79-3D69D8477DE4": 0x83,
    "0657FD6D-A4AB-43C4-84E5-0933C84B4F4F": 0x82,
    "E6D6D379-F507-44C2-A23C-238F2A3DF928": 0x8E,
    "933AC7E1-2EB4-4F13-B844-0E14E2AEF915": 0x83,
    "21686148-6449-6E6F-744E-656564454649": 0x83,
    "48465300-0000-11AA-AA11-00306543ECAC": 0xAF,
    "516E7CB4-6ECF-11D6-8FF8-00022D09712B": 0xA5,
}

# Dosya sistemine gore MBR tip bayti (tur GUID'i taninmadiginda kullanilir)
FS_TO_MBR = {
    "FAT12": 0x01, "FAT16": 0x0E, "FAT32": 0x0C, "exFAT": 0x07, "NTFS": 0x07,
    "ext2": 0x83, "ext3": 0x83, "ext4": 0x83, "btrfs": 0x83, "XFS": 0x83,
    "Linux Takas": 0x82,
}
FS_TO_GPT = {
    "FAT12": MSBASIC, "FAT16": MSBASIC, "FAT32": MSBASIC,
    "exFAT": MSBASIC, "NTFS": MSBASIC,
    "ext2": LINUXFS, "ext3": LINUXFS, "ext4": LINUXFS,
    "btrfs": LINUXFS, "XFS": LINUXFS,
    "Linux Takas": "0657FD6D-A4AB-43C4-84E5-0933C84B4F4F",
}

GPT_RESERVED_HEAD = 34      # koruyucu MBR + baslik + 128 giris
GPT_RESERVED_TAIL = 33      # yedek giris dizisi + yedek baslik


class ConvertError(Exception):
    pass


def _partitions_for_convert(table: PartitionTable) -> List[Partition]:
    """Donusumde tasinacak bolumler (MBR genisletilmis kapsayicisi haric)."""
    return [p for p in table.sorted_partitions()
            if not (p.scheme == "mbr" and p.type_id in MBR_EXTENDED_TYPES)]


# --------------------------------------------------------------------------
# MBR -> GPT
# --------------------------------------------------------------------------
def check_mbr_to_gpt(device: BlockDevice, table: PartitionTable) -> Tuple[bool, str]:
    """Donusumun yapilabilirligini denetler. (uygun_mu, aciklama)"""
    if table.scheme != "mbr":
        return False, "Kaynak tablo MBR degil"
    bolumler = _partitions_for_convert(table)
    if len(bolumler) > 128:
        return False, "GPT en fazla 128 bolum tasiyabilir"

    ilk = min((p.start_lba for p in bolumler), default=device.sector_count)
    if ilk < GPT_RESERVED_HEAD:
        return False, (f"Ilk bolum LBA {ilk} konumunda basliyor; GPT giris dizisi icin "
                       f"disk basinda en az {GPT_RESERVED_HEAD} sektor bos olmalidir")
    son = max((p.end_lba for p in bolumler), default=0)
    if son >= device.sector_count - GPT_RESERVED_TAIL:
        gerekli = human_size(GPT_RESERVED_TAIL * device.sector_size)
        return False, (f"Disk sonunda yedek GPT icin {gerekli} bos alan gerekiyor; "
                       f"son bolum LBA {son} konumunda bitiyor")
    return True, f"{len(bolumler)} bolum GPT'ye tasinabilir"


def mbr_to_gpt(device: BlockDevice, table: PartitionTable,
               progress=None) -> GPTTable:
    """MBR tablosunu GPT'ye donusturur. Bolum verileri yerinde kalir."""
    uygun, neden = check_mbr_to_gpt(device, table)
    if not uygun:
        raise ConvertError(neden)

    def bildir(mesaj: str, yuzde: int) -> None:
        if progress:
            progress(mesaj, yuzde)

    bildir("Mevcut bolumler okunuyor...", 10)
    kaynak = _partitions_for_convert(table)
    tanimlar = [(p.start_lba, p.sector_count,
                 MBR_TO_GPT.get(p.type_id) or FS_TO_GPT.get(p.fs_type) or MSBASIC,
                 p.fs_label or p.name or "", p.bootable)
                for p in kaynak]

    bildir("Eski tablo temizleniyor...", 30)
    ebr_sektorleri = [p.ebr_lba for p in table.partitions if p.logical and p.ebr_lba]
    device.zero_sectors(0, 1)

    bildir("GPT olusturuluyor...", 50)
    gpt = GPTTable.create(device)
    for i, (lba, adet, guid, ad, onyukleme) in enumerate(tanimlar):
        gpt.partitions.append(Partition(
            index=i + 1, start_lba=lba, sector_count=adet, scheme="gpt",
            type_guid=guid, part_guid=GPT_UNUSED, name=ad[:36],
            bootable=onyukleme, sector_size=device.sector_size))
    gpt._renumber()
    gpt.write()

    # EBR sektorleri artik anlamsiz; bolum verisinin disinda kaldiklari icin
    # temizlenmeleri guvenli ve tabloyu yanlis okumayi onler
    for lba in ebr_sektorleri:
        if not any(p.start_lba <= lba <= p.end_lba for p in gpt.partitions):
            try:
                device.zero_sectors(lba, 1)
            except Exception:
                pass
    bildir("Tamamlandi", 100)
    return gpt


# --------------------------------------------------------------------------
# GPT -> MBR
# --------------------------------------------------------------------------
def check_gpt_to_mbr(device: BlockDevice, table: PartitionTable) -> Tuple[bool, str]:
    if table.scheme != "gpt":
        return False, "Kaynak tablo GPT degil"
    bolumler = table.sorted_partitions()
    if len(bolumler) > 4:
        return False, (f"MBR en fazla 4 birincil bolum tasir; tabloda {len(bolumler)} "
                       "bolum var. Once bolum sayisini azaltin.")
    for p in bolumler:
        if p.end_lba > MAX_MBR_SECTORS:
            return False, (f"Bolum {p.index} 2 TiB sinirinin otesinde bitiyor; "
                           "MBR bu yerlesimi tasiyamaz")
    return True, f"{len(bolumler)} bolum MBR'ye tasinabilir"


def gpt_to_mbr(device: BlockDevice, table: PartitionTable,
               progress=None) -> MBRTable:
    """GPT tablosunu MBR'ye donusturur. Bolum verileri yerinde kalir."""
    uygun, neden = check_gpt_to_mbr(device, table)
    if not uygun:
        raise ConvertError(neden)

    def bildir(mesaj: str, yuzde: int) -> None:
        if progress:
            progress(mesaj, yuzde)

    bildir("Mevcut bolumler okunuyor...", 10)
    # Dosya sistemi biliniyorsa tip bayti ondan secilir: "Microsoft Temel Veri"
    # GUID'i FAT32'yi de NTFS'i de kapsadigi icin tek basina yeterince belirgin degil.
    tanimlar = [(p.start_lba, p.sector_count,
                 FS_TO_MBR.get(p.fs_type) or GPT_TO_MBR.get(p.type_guid.upper()) or 0x83,
                 p.bootable)
                for p in table.sorted_partitions()]

    bildir("GPT yapilari siliniyor...", 35)
    device.zero_sectors(1, GPT_RESERVED_HEAD - 1)
    kuyruk = device.sector_count - GPT_RESERVED_TAIL
    if kuyruk > 0:
        device.zero_sectors(kuyruk, GPT_RESERVED_TAIL)

    bildir("MBR olusturuluyor...", 60)
    mbr = MBRTable.create(device)
    for i, (lba, adet, tip, onyukleme) in enumerate(tanimlar):
        mbr.partitions.append(Partition(
            index=i + 1, start_lba=lba, sector_count=adet, scheme="mbr",
            type_id=tip, bootable=onyukleme, sector_size=device.sector_size))
    mbr._renumber_mbr()
    mbr.write()
    bildir("Tamamlandi", 100)
    return mbr


# --------------------------------------------------------------------------
# Hizalama denetimi (DiskGenius "4K hizalama" karsiligi)
# --------------------------------------------------------------------------
def alignment_report(table: PartitionTable, sector_size: int = 512) -> List[dict]:
    """Her bolum icin 4K / 1MiB hizalama durumunu dondurur."""
    rapor = []
    dort_k = max(1, 4096 // sector_size)
    bir_mib = max(1, 1024 * 1024 // sector_size)
    for p in table.sorted_partitions():
        rapor.append({
            "index": p.index,
            "name": p.display_name,
            "start_lba": p.start_lba,
            "aligned_4k": p.start_lba % dort_k == 0,
            "aligned_1m": p.start_lba % bir_mib == 0,
            "offset_in_4k": (p.start_lba % dort_k) * sector_size,
        })
    return rapor
