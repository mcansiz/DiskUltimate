"""MBR (Master Boot Record) bolum tablosu okuma/yazma.

Yapi ozeti (spec: .claude/specs/mbr.md):
  0x000..0x1BD  onyukleme kodu (korunur)
  0x1BE..0x1FD  4 adet 16 baytlik birincil bolum girisi
  0x1FE..0x1FF  0x55 0xAA imzasi

Genisletilmis bolum (0x05/0x0F/0x85) icinde EBR zinciri ile mantiksal
bolumler tutulur.
"""
from __future__ import annotations

import struct
from typing import List, Optional

from .image import BlockDevice
from .ptable import (MBR_EXTENDED_TYPES, Partition, PartitionTable,
                     PartitionTableError)
from ..i18n import tr

MBR_SIGNATURE = 0xAA55
ENTRY_OFFSET = 446
ENTRY_SIZE = 16
MAX_PRIMARY = 4

HEADS = 255
SECTORS_PER_TRACK = 63
MAX_MBR_SECTORS = 0xFFFFFFFF  # ~2 TiB @512B


def lba_to_chs(lba: int) -> bytes:
    """LBA -> 3 baytlik CHS. Sinir asilirsa 0xFE 0xFF 0xFF dondurur."""
    if lba > (1023 * HEADS * SECTORS_PER_TRACK + (HEADS - 1) * SECTORS_PER_TRACK + SECTORS_PER_TRACK - 1):
        return b"\xFE\xFF\xFF"
    cyl = lba // (HEADS * SECTORS_PER_TRACK)
    rem = lba % (HEADS * SECTORS_PER_TRACK)
    head = rem // SECTORS_PER_TRACK
    sect = rem % SECTORS_PER_TRACK + 1
    return bytes([head & 0xFF,
                  (sect & 0x3F) | ((cyl >> 2) & 0xC0),
                  cyl & 0xFF])


def _pack_entry(part: Optional[Partition], start_lba: int = 0,
                sector_count: int = 0, type_id: int = 0,
                bootable: bool = False) -> bytes:
    if part is not None:
        start_lba, sector_count = part.start_lba, part.sector_count
        type_id, bootable = part.type_id, part.bootable
    if type_id == 0 or sector_count == 0:
        return b"\x00" * ENTRY_SIZE
    if sector_count > MAX_MBR_SECTORS or start_lba > MAX_MBR_SECTORS:
        raise PartitionTableError(
            tr("MBR 2 TiB sinirini asiyor; GPT kullanin"))
    return (bytes([0x80 if bootable else 0x00])
            + lba_to_chs(start_lba)
            + bytes([type_id])
            + lba_to_chs(start_lba + sector_count - 1)
            + struct.pack("<II", start_lba, sector_count))


def _unpack_entry(raw: bytes):
    status, _c1, type_id, _c2, lba, count = struct.unpack("<B3sB3sII", raw)
    return status, type_id, lba, count


class MBRTable(PartitionTable):
    scheme = "mbr"

    def __init__(self, device: BlockDevice):
        super().__init__(device)
        self.bootcode = b"\x00" * ENTRY_OFFSET
        self.disk_signature = 0

    # -- okuma ---------------------------------------------------------------
    @classmethod
    def is_present(cls, device: BlockDevice) -> bool:
        try:
            sector = device.read_sectors(0)
        except Exception:
            return False
        return struct.unpack_from("<H", sector, 510)[0] == MBR_SIGNATURE

    @classmethod
    def entries_plausible(cls, device: BlockDevice) -> bool:
        """0x55AA imzali ilk sektor gercekten bir MBR mi?

        FAT/NTFS/exFAT onyukleme sektoru de 0x55AA ile biter; tablosuz
        ("super disket") bir USB bellekte ya da tek bolum goruntusunde MBR
        girisi yerinde onyukleme KODU vardir. Linux cekirdeginin msdos
        ayristiricisi ile ayni olcut: durum bayti 0x00/0x80, girisler disk
        icinde ve birbiriyle cakismiyor; en az bir giris dolu.
        """
        try:
            sector = device.read_sectors(0)
        except Exception:
            return False
        ranges = []
        for i in range(MAX_PRIMARY):
            raw = sector[ENTRY_OFFSET + i * ENTRY_SIZE:
                         ENTRY_OFFSET + (i + 1) * ENTRY_SIZE]
            status, type_id, lba, count = _unpack_entry(raw)
            if status not in (0x00, 0x80):
                return False
            if type_id == 0 or count == 0:
                continue
            if lba == 0 or lba + count > device.sector_count + 1:
                return False
            ranges.append((lba, lba + count))
        ranges.sort()
        for (a0, a1), (b0, _b1) in zip(ranges, ranges[1:]):
            if b0 < a1:
                return False
        return bool(ranges)

    @classmethod
    def read(cls, device: BlockDevice) -> "MBRTable":
        table = cls(device)
        sector = device.read_sectors(0)
        if struct.unpack_from("<H", sector, 510)[0] != MBR_SIGNATURE:
            raise PartitionTableError(tr("Gecerli MBR imzasi bulunamadi"))
        table.bootcode = sector[:ENTRY_OFFSET]
        table.disk_signature = struct.unpack_from("<I", sector, 440)[0]

        extended: Optional[Partition] = None
        index = 1
        for i in range(MAX_PRIMARY):
            raw = sector[ENTRY_OFFSET + i * ENTRY_SIZE:
                         ENTRY_OFFSET + (i + 1) * ENTRY_SIZE]
            status, type_id, lba, count = _unpack_entry(raw)
            if type_id == 0 or count == 0:
                continue
            part = Partition(index=index, start_lba=lba, sector_count=count,
                             scheme="mbr", type_id=type_id,
                             bootable=bool(status & 0x80),
                             sector_size=device.sector_size)
            table.partitions.append(part)
            if type_id in MBR_EXTENDED_TYPES:
                extended = part
            index += 1
        if extended is not None:
            table._read_logicals(extended, start_index=5)
        return table

    def _read_logicals(self, extended: Partition, start_index: int = 5) -> None:
        """EBR zincirini takip ederek mantiksal bolumleri okur."""
        ebr_lba = extended.start_lba
        index = start_index
        seen = set()
        while ebr_lba and ebr_lba not in seen and index < 64:
            seen.add(ebr_lba)
            try:
                sector = self.device.read_sectors(ebr_lba)
            except Exception:
                return
            if struct.unpack_from("<H", sector, 510)[0] != MBR_SIGNATURE:
                return
            e0 = sector[ENTRY_OFFSET:ENTRY_OFFSET + ENTRY_SIZE]
            e1 = sector[ENTRY_OFFSET + ENTRY_SIZE:ENTRY_OFFSET + 2 * ENTRY_SIZE]
            status, type_id, rel_lba, count = _unpack_entry(e0)
            if type_id and count:
                self.partitions.append(Partition(
                    index=index, start_lba=ebr_lba + rel_lba, sector_count=count,
                    scheme="mbr", type_id=type_id, bootable=bool(status & 0x80),
                    logical=True, ebr_lba=ebr_lba,
                    sector_size=self.device.sector_size))
                index += 1
            _s2, t2, next_rel, next_count = _unpack_entry(e1)
            if t2 in MBR_EXTENDED_TYPES and next_count:
                ebr_lba = extended.start_lba + next_rel
            else:
                ebr_lba = 0

    # -- olusturma -----------------------------------------------------------
    @classmethod
    def create(cls, device: BlockDevice, keep_bootcode: bool = False) -> "MBRTable":
        """Diskte bos bir MBR tablosu olusturur (mevcut bolumler silinir)."""
        table = cls(device)
        if keep_bootcode and cls.is_present(device):
            table.bootcode = device.read_sectors(0)[:ENTRY_OFFSET]
        import random
        table.disk_signature = random.getrandbits(32)
        table.write()
        return table

    # -- yazma ---------------------------------------------------------------
    def write(self) -> None:
        sector = bytearray(512)
        sector[:ENTRY_OFFSET] = self.bootcode[:ENTRY_OFFSET].ljust(ENTRY_OFFSET, b"\x00")
        struct.pack_into("<I", sector, 440, self.disk_signature & 0xFFFFFFFF)
        primaries = [p for p in self.partitions if not p.logical]
        if len(primaries) > MAX_PRIMARY:
            raise PartitionTableError(tr("MBR en fazla 4 birincil bolum destekler"))
        primaries.sort(key=lambda p: p.start_lba)
        for i in range(MAX_PRIMARY):
            entry = _pack_entry(primaries[i]) if i < len(primaries) else b"\x00" * ENTRY_SIZE
            sector[ENTRY_OFFSET + i * ENTRY_SIZE:
                   ENTRY_OFFSET + (i + 1) * ENTRY_SIZE] = entry
        struct.pack_into("<H", sector, 510, MBR_SIGNATURE)
        self.device.write_sectors(0, bytes(sector))
        self._write_logicals()
        flush = getattr(self.device, "flush", None)
        if flush:
            flush()

    def _write_logicals(self) -> None:
        extended = self.extended_partition()
        logicals = sorted([p for p in self.partitions if p.logical],
                          key=lambda p: p.start_lba)
        if extended is None:
            return
        for i, part in enumerate(logicals):
            ebr_lba = part.ebr_lba or (part.start_lba - self.align_sectors)
            part.ebr_lba = ebr_lba
            sector = bytearray(512)
            sector[ENTRY_OFFSET:ENTRY_OFFSET + ENTRY_SIZE] = _pack_entry(
                None, start_lba=part.start_lba - ebr_lba,
                sector_count=part.sector_count, type_id=part.type_id,
                bootable=part.bootable)
            if i + 1 < len(logicals):
                nxt = logicals[i + 1]
                next_ebr = nxt.ebr_lba or (nxt.start_lba - self.align_sectors)
                nxt.ebr_lba = next_ebr
                sector[ENTRY_OFFSET + ENTRY_SIZE:ENTRY_OFFSET + 2 * ENTRY_SIZE] = _pack_entry(
                    None, start_lba=next_ebr - extended.start_lba,
                    sector_count=nxt.sector_count + (nxt.start_lba - next_ebr),
                    type_id=0x0F)
            struct.pack_into("<H", sector, 510, MBR_SIGNATURE)
            self.device.write_sectors(ebr_lba, bytes(sector))

    # -- bolum islemleri -----------------------------------------------------
    def extended_partition(self) -> Optional[Partition]:
        for p in self.partitions:
            if p.type_id in MBR_EXTENDED_TYPES:
                return p
        return None

    def primary_count(self) -> int:
        return len([p for p in self.partitions if not p.logical])

    def can_add_primary(self) -> bool:
        return self.primary_count() < MAX_PRIMARY

    def last_usable_lba(self) -> int:
        return min(self.device.sector_count, MAX_MBR_SECTORS + 1) - 1

    def add_partition(self, start_lba: int, sector_count: int,
                      type_id: int = 0x0C, bootable: bool = False,
                      name: str = "", logical: Optional[bool] = None,
                      **_ignored) -> Partition:
        extended = self.extended_partition()
        if logical is None:
            inside_ext = (extended is not None
                          and start_lba >= extended.start_lba
                          and start_lba + sector_count - 1 <= extended.end_lba)
            logical = inside_ext
        if logical:
            if extended is None:
                raise PartitionTableError(
                    tr("Mantiksal bolum icin once genisletilmis bolum olusturun"))
            ebr_lba = self.align_down(start_lba) - self.align_sectors
            if ebr_lba < extended.start_lba:
                ebr_lba = extended.start_lba
                start_lba = max(start_lba, ebr_lba + self.align_sectors)
            self._check_logical_range(start_lba, sector_count, extended)
        else:
            if not self.can_add_primary():
                raise PartitionTableError(
                    tr("4 birincil bolum dolu; genisletilmis bolum kullanin"))
            self.check_range(start_lba, sector_count)
            ebr_lba = 0
        part = Partition(index=0, start_lba=start_lba, sector_count=sector_count,
                         scheme="mbr", type_id=type_id, bootable=bootable,
                         logical=bool(logical), ebr_lba=ebr_lba, name=name,
                         sector_size=self.sector_size)
        self.partitions.append(part)
        self._renumber_mbr()
        self.write()
        return part

    def create_extended(self, start_lba: int, sector_count: int) -> Partition:
        """Genisletilmis kapsayici bolum olusturur."""
        if self.extended_partition() is not None:
            raise PartitionTableError(tr("Zaten bir genisletilmis bolum var"))
        return self.add_partition(start_lba, sector_count, type_id=0x0F,
                                  logical=False)

    def _check_logical_range(self, start_lba: int, sector_count: int,
                             extended: Partition) -> None:
        if start_lba < extended.start_lba or start_lba + sector_count - 1 > extended.end_lba:
            raise PartitionTableError(
                tr("Mantiksal bolum genisletilmis bolumun disinda"))
        for p in self.partitions:
            if p.logical and p.overlaps(start_lba, sector_count):
                raise PartitionTableError(tr("{} numarali bolum ile cakisiyor",
                                             p.index))

    def delete_partition(self, index: int) -> None:
        part = self.get(index)
        if part.type_id in MBR_EXTENDED_TYPES:
            # genisletilmis bolum silinince icindeki mantiksallar da gider
            self.partitions = [p for p in self.partitions if not p.logical]
        self.partitions = [p for p in self.partitions if p is not part]
        self._renumber_mbr()
        self.write()

    def set_type(self, index: int, type_id: int) -> None:
        self.get(index).type_id = type_id
        self.write()

    def set_bootable(self, index: int, value: bool = True) -> None:
        part = self.get(index)
        if value:
            for p in self.partitions:
                p.bootable = False
        part.bootable = value
        self.write()

    def free_regions(self, min_sectors: int = 1):
        """Bos alanlar: genisletilmis bolumun ic bosluklari da dahil."""
        regions = super().free_regions(min_sectors)
        extended = self.extended_partition()
        if extended is None:
            return regions
        logicals = sorted([p for p in self.partitions if p.logical],
                          key=lambda p: p.start_lba)
        from .ptable import FreeRegion
        cursor = extended.start_lba
        inner = []
        for p in logicals:
            if p.start_lba > cursor:
                start = self.align_up(cursor + self.align_sectors)
                end = p.start_lba - 1
                if end >= start and (end - start + 1) >= min_sectors:
                    inner.append(FreeRegion(start, end - start + 1, self.sector_size))
            cursor = max(cursor, p.end_lba + 1)
        if cursor <= extended.end_lba:
            start = self.align_up(cursor + (self.align_sectors if logicals or cursor == extended.start_lba else 0))
            if extended.end_lba >= start and (extended.end_lba - start + 1) >= min_sectors:
                inner.append(FreeRegion(start, extended.end_lba - start + 1, self.sector_size))
        return regions + inner

    def _renumber_mbr(self) -> None:
        primaries = sorted([p for p in self.partitions if not p.logical],
                           key=lambda p: p.start_lba)
        logicals = sorted([p for p in self.partitions if p.logical],
                          key=lambda p: p.start_lba)
        for i, p in enumerate(primaries, 1):
            p.index = i
        for i, p in enumerate(logicals, 5):
            p.index = i
