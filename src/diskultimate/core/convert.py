"""Bolum tablosu semasi donusumu: MBR <-> GPT (veri kaybi olmadan).

Donusum yalnizca **tabloyu** yeniden yazar; bolumlerin diskteki yeri ve icerigi
degismez. Bu yuzden islem oncesi uygunluk denetimi sarttir: hedef sema kaynak
yerlesimi tasiyamiyorsa donusum yapilmaz.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from .gpt import GPTTable, gpt_reserved
from .image import BlockDevice
from .mbr import MAX_MBR_SECTORS, MBRTable
from .ptable import (GPT_UNUSED, MBR_EXTENDED_TYPES, Partition, PartitionTable,
                     PartitionTableError, human_size)
from . import fsregistry
from ..i18n import tr

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

# Dosya sistemine gore MBR tip bayti / GPT tur GUID'i (tur taninmadiginda
# kullanilir). Tek kaynak: `fsregistry` (ADR 0056).
FS_TO_MBR = fsregistry.mbr_types()
FS_TO_GPT = fsregistry.gpt_types()

# 512 bayt sektorde gecerli eski sabitler (geri uyumluluk). Gercek deger
# sektor boyuna gore `gpt_reserved(...)` ile hesaplanir: 4Kn'de (6, 5).
GPT_RESERVED_HEAD = 34      # koruyucu MBR + baslik + 128 giris
GPT_RESERVED_TAIL = 33      # yedek giris dizisi + yedek baslik


class ConvertError(Exception):
    pass


def _partitions_for_convert(table: PartitionTable) -> List[Partition]:
    """Donusumde tasinacak bolumler (MBR genisletilmis kapsayicisi haric)."""
    return [p for p in table.sorted_partitions()
            if not (p.scheme == "mbr" and p.type_id in MBR_EXTENDED_TYPES)]


def _inside_partition(lba: int, parts) -> bool:
    return any(p.start_lba <= lba <= p.end_lba for p in parts)


# --------------------------------------------------------------------------
# MBR -> GPT
# --------------------------------------------------------------------------
def check_mbr_to_gpt(device: BlockDevice, table: PartitionTable) -> Tuple[bool, str]:
    """Donusumun yapilabilirligini denetler. (uygun_mu, aciklama)"""
    if table.scheme != "mbr":
        return False, tr("Kaynak tablo MBR degil")
    parts = _partitions_for_convert(table)
    if len(parts) > 128:
        return False, tr("GPT en fazla 128 bolum tasiyabilir")
    if any(p.type_id == 0xEE for p in parts):
        return False, tr("Diskte koruyucu/hibrit MBR girisi (0xEE) var; "
                         "MBR'den GPT'ye donusturulemez")
    head, tail = gpt_reserved(device.sector_size)

    first = min((p.start_lba for p in parts), default=device.sector_count)
    if first < head:
        return False, (tr("Ilk bolum LBA {} konumunda basliyor; GPT giris "
                          "dizisi icin disk basinda en az {} sektor bos "
                          "olmalidir", first, head))
    last = max((p.end_lba for p in parts), default=0)
    if last >= device.sector_count - tail:
        gerekli = human_size(tail * device.sector_size)
        return False, (tr("Disk sonunda yedek GPT icin {} bos alan gerekiyor; "
                          "son bolum LBA {} konumunda bitiyor", gerekli, last))
    return True, tr("{} bolum GPT'ye tasinabilir", len(parts))


def mbr_to_gpt(device: BlockDevice, table: PartitionTable,
               progress=None) -> GPTTable:
    """MBR tablosunu GPT'ye donusturur. Bolum verileri yerinde kalir.

    Yeni tablo once bellekte kurulur ve dogrulanir; GPT yapilari yazilir,
    koruyucu MBR **en son** yazilir. Yarida kalirsa eski MBR gecerlidir
    (0xEE olmadan GPT yok sayilir).
    """
    uygun, reason = check_mbr_to_gpt(device, table)
    if not uygun:
        raise ConvertError(reason)

    def report(message: str, percent: int) -> None:
        if progress:
            progress(message, percent)

    report(tr("Mevcut bolumler okunuyor..."), 10)
    kaynak = _partitions_for_convert(table)
    ebr_sektorleri = [p.ebr_lba for p in table.partitions if p.logical and p.ebr_lba]

    report(tr("GPT olusturuluyor..."), 50)
    gpt = GPTTable(device)
    gpt._fresh_pmbr = True
    gpt._zero_bootcode = True       # eski DOS onyukleme kodu GPT'de anlamsiz
    first = min((p.start_lba for p in kaynak), default=gpt.default_first_usable())
    gpt._first_usable = min(gpt.default_first_usable(), first)
    # Numaralar korunur (gdisk gibi): birincil 1-4, mantiksal 5+. Boylece
    # sda5 donusumden sonra da sda5'tir.
    for p in kaynak:
        gpt.partitions.append(Partition(
            index=p.index, start_lba=p.start_lba, sector_count=p.sector_count,
            scheme="gpt",
            type_guid=(MBR_TO_GPT.get(p.type_id) or FS_TO_GPT.get(p.fs_type)
                       or MSBASIC),
            part_guid=GPT_UNUSED, name=(p.fs_label or p.name or "")[:36],
            bootable=p.bootable, sector_size=device.sector_size))
    try:
        gpt.build()                  # dogrulama: hicbir sey yazilmaz
    except PartitionTableError as exc:
        raise ConvertError(str(exc)) from exc
    gpt.write()

    # EBR sektorleri artik anlamsiz; bolum verisinin disinda kaldiklari icin
    # temizlenmeleri guvenli ve tabloyu yanlis okumayi onler
    head, tail = gpt_reserved(device.sector_size, gpt.entry_count, gpt.entry_size)
    for lba in ebr_sektorleri:
        if lba < head or lba >= device.sector_count - tail:
            continue
        if not _inside_partition(lba, gpt.partitions):
            try:
                device.zero_sectors(lba, 1)
            except Exception:
                pass
    report(tr("Tamamlandi"), 100)
    return gpt


# --------------------------------------------------------------------------
# GPT -> MBR
# --------------------------------------------------------------------------
def check_gpt_to_mbr(device: BlockDevice, table: PartitionTable) -> Tuple[bool, str]:
    if table.scheme != "gpt":
        return False, tr("Kaynak tablo GPT degil")
    parts = table.sorted_partitions()
    if len(parts) > 4:
        return False, (tr("MBR en fazla 4 birincil bolum tasir; tabloda {} "
                          "bolum var. Once bolum sayisini azaltin.", len(parts)))
    for p in parts:
        if p.start_lba < 1:
            return False, tr("Bolum {} LBA 0'da basliyor; MBR bu yerlesimi "
                             "tasiyamaz", p.index)
        if p.end_lba > MAX_MBR_SECTORS or p.sector_count > MAX_MBR_SECTORS:
            return False, (tr("Bolum {} 2 TiB sinirinin otesinde bitiyor; MBR "
                              "bu yerlesimi tasiyamaz", p.index))
    return True, tr("{} bolum MBR'ye tasinabilir", len(parts))


def _gpt_structures(device: BlockDevice, table: PartitionTable) -> List[tuple]:
    """Silinecek GPT yapilari: (LBA, sektor) — sektor boyuna gore."""
    head, tail = gpt_reserved(device.sector_size,
                              getattr(table, "entry_count", 128),
                              getattr(table, "entry_size", 128))
    n = device.sector_count
    out = [(1, head - 1), (max(1, n - tail), min(tail, n - 1))]
    out += list(getattr(table, "found_structures", []))
    return out


def gpt_to_mbr(device: BlockDevice, table: PartitionTable,
               progress=None) -> MBRTable:
    """GPT tablosunu MBR'ye donusturur. Bolum verileri yerinde kalir.

    Sira (P1): her sey once denetlenir ve yeni MBR bellekte kurulur; once
    MBR yazilir, GPT yapilari **ondan sonra** silinir. Eskiden GPT once
    siliniyor, MBR 4Kn diskte yazilamadiginda disk tablosuz kaliyordu.
    Silme hicbir bolumun icine degmez (4Kn'de 34/33 sektor sabiti son
    bolumun kuyruguna denk gelebiliyordu).
    """
    uygun, reason = check_gpt_to_mbr(device, table)
    if not uygun:
        raise ConvertError(reason)

    def report(message: str, percent: int) -> None:
        if progress:
            progress(message, percent)

    report(tr("Mevcut bolumler okunuyor..."), 10)
    parts = table.sorted_partitions()
    import random
    mbr = MBRTable(device)
    mbr.disk_signature = random.getrandbits(32)
    # Dosya sistemi biliniyorsa tip bayti ondan secilir: "Microsoft Temel Veri"
    # GUID'i FAT32'yi de NTFS'i de kapsadigi icin tek basina yeterince belirgin degil.
    for p in parts:
        mbr.partitions.append(Partition(
            index=p.index if 1 <= p.index <= 4 else 0,
            start_lba=p.start_lba, sector_count=p.sector_count, scheme="mbr",
            type_id=(FS_TO_MBR.get(p.fs_type)
                     or GPT_TO_MBR.get(p.type_guid.upper()) or 0x83),
            bootable=p.bootable, sector_size=device.sector_size))
    try:
        writes = mbr.build()         # dogrulama: hicbir sey yazilmaz
    except PartitionTableError as exc:
        raise ConvertError(str(exc)) from exc

    report(tr("MBR yaziliyor..."), 40)
    for lba, data in writes:
        device.write_sectors(lba, data)

    report(tr("GPT yapilari siliniyor..."), 70)
    n = device.sector_count
    for start, count in _gpt_structures(device, table):
        for lba in range(start, min(start + count, n)):
            if lba == 0 or _inside_partition(lba, parts):
                continue
            device.zero_sectors(lba, 1)
    flush = getattr(device, "flush", None)
    if flush:
        flush()
    report(tr("Tamamlandi"), 100)
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
