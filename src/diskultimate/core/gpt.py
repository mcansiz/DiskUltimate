"""GPT (GUID Partition Table) okuma/yazma.

Yerlesim (spec: .claude/specs/gpt.md):
  LBA 0        Koruyucu MBR (tip 0xEE)
  LBA 1        Birincil GPT basligi
  LBA 2..33    128 x 128 bayt bolum girisi
  LBA n-33..   Yedek giris dizisi
  LBA n-1      Yedek GPT basligi
"""
from __future__ import annotations

import struct
import uuid
import zlib
from typing import List, Optional

from .image import BlockDevice
from .ptable import (GPT_TYPES, GPT_UNUSED, Partition, PartitionTable,
                     PartitionTableError)
from ..i18n import tr

GPT_SIGNATURE = b"EFI PART"
GPT_REVISION = 0x00010000
HEADER_SIZE = 92
ENTRY_SIZE = 128
ENTRY_COUNT = 128

ATTR_REQUIRED = 1 << 0        # sistem icin gerekli
ATTR_NO_BLOCK_IO = 1 << 1
ATTR_LEGACY_BIOS = 1 << 2
ATTR_READ_ONLY = 1 << 60
ATTR_HIDDEN = 1 << 62
ATTR_NO_AUTOMOUNT = 1 << 63


def guid_to_bytes(guid: str) -> bytes:
    """GUID metnini karma-endian 16 bayta cevirir."""
    return uuid.UUID(guid).bytes_le


def bytes_to_guid(raw: bytes) -> str:
    return str(uuid.UUID(bytes_le=raw)).upper()


def new_guid() -> str:
    return str(uuid.uuid4()).upper()


class GPTTable(PartitionTable):
    scheme = "gpt"

    def __init__(self, device: BlockDevice):
        super().__init__(device)
        self.disk_guid = new_guid()
        self.entry_count = ENTRY_COUNT
        self.entry_size = ENTRY_SIZE
        self._first_usable = 0
        self._last_usable = 0
        self.header_backup_ok = True

    # -- yerlesim hesaplari --------------------------------------------------
    @property
    def entry_sectors(self) -> int:
        total = self.entry_count * self.entry_size
        ss = self.sector_size
        return (total + ss - 1) // ss

    @property
    def entries_lba(self) -> int:
        return 2

    @property
    def backup_header_lba(self) -> int:
        return self.device.sector_count - 1

    @property
    def backup_entries_lba(self) -> int:
        return self.backup_header_lba - self.entry_sectors

    def first_usable_lba(self) -> int:
        if self._first_usable:
            return max(self._first_usable, self.align_sectors)
        return max(self.entries_lba + self.entry_sectors, self.align_sectors)

    def last_usable_lba(self) -> int:
        if self._last_usable:
            return self._last_usable
        return self.backup_entries_lba - 1

    # -- okuma ---------------------------------------------------------------
    @classmethod
    def is_present(cls, device: BlockDevice) -> bool:
        try:
            return device.read_sectors(1)[:8] == GPT_SIGNATURE
        except Exception:
            return False

    @classmethod
    def read(cls, device: BlockDevice) -> "GPTTable":
        table = cls(device)
        header = device.read_sectors(1)
        if header[:8] != GPT_SIGNATURE:
            # birincil bozuksa yedek baslikla dene
            try:
                header = device.read_sectors(device.sector_count - 1)
            except Exception:
                header = b""
            if header[:8] != GPT_SIGNATURE:
                raise PartitionTableError(tr("Gecerli GPT basligi bulunamadi"))
            table.header_backup_ok = False

        (_sig, _rev, hdr_size, hdr_crc, _res, _my_lba, _other_lba,
         first_usable, last_usable, disk_guid, entries_lba,
         num_entries, entry_size, entries_crc) = struct.unpack_from(
            "<8sIIII QQ QQ 16s Q III", header, 0)

        # baslik CRC dogrulamasi
        check = bytearray(header[:hdr_size])
        struct.pack_into("<I", check, 16, 0)
        table.header_crc_ok = (zlib.crc32(bytes(check)) & 0xFFFFFFFF) == hdr_crc

        table.disk_guid = bytes_to_guid(disk_guid)
        table._first_usable = first_usable
        table._last_usable = last_usable
        table.entry_count = num_entries
        table.entry_size = entry_size

        nbytes = num_entries * entry_size
        nsectors = (nbytes + device.sector_size - 1) // device.sector_size
        raw = device.read_sectors(entries_lba, nsectors)[:nbytes]
        table.entries_crc_ok = (zlib.crc32(raw) & 0xFFFFFFFF) == entries_crc

        index = 1
        for i in range(num_entries):
            entry = raw[i * entry_size:(i + 1) * entry_size]
            type_guid = bytes_to_guid(entry[0:16])
            if type_guid == GPT_UNUSED:
                continue
            part_guid = bytes_to_guid(entry[16:32])
            start, end, attrs = struct.unpack_from("<QQQ", entry, 32)
            name = entry[56:128].decode("utf-16-le", "ignore").split("\x00")[0]
            table.partitions.append(Partition(
                index=index, start_lba=start, sector_count=end - start + 1,
                scheme="gpt", type_guid=type_guid, part_guid=part_guid,
                name=name, attributes=attrs,
                bootable=bool(attrs & ATTR_LEGACY_BIOS),
                sector_size=device.sector_size))
            index += 1
        return table

    # -- olusturma -----------------------------------------------------------
    @classmethod
    def create(cls, device: BlockDevice) -> "GPTTable":
        min_sectors = 2 + 2 * (ENTRY_COUNT * ENTRY_SIZE // device.sector_size) + 2
        if device.sector_count < min_sectors:
            raise PartitionTableError(tr("Disk GPT icin cok kucuk"))
        table = cls(device)
        table.disk_guid = new_guid()
        table._first_usable = max(2 + table.entry_sectors, table.align_sectors)
        table._last_usable = table.backup_entries_lba - 1
        table.write()
        return table

    # -- yazma ---------------------------------------------------------------
    def _build_entries(self) -> bytes:
        buf = bytearray(self.entry_count * self.entry_size)
        parts = sorted(self.partitions, key=lambda p: p.start_lba)
        if len(parts) > self.entry_count:
            raise PartitionTableError(
                tr("GPT en fazla {} bolum destekler", self.entry_count))
        for i, p in enumerate(parts):
            off = i * self.entry_size
            attrs = p.attributes
            if p.bootable:
                attrs |= ATTR_LEGACY_BIOS
            else:
                attrs &= ~ATTR_LEGACY_BIOS
            p.attributes = attrs
            buf[off:off + 16] = guid_to_bytes(p.type_guid)
            buf[off + 16:off + 32] = guid_to_bytes(
                p.part_guid if p.part_guid != GPT_UNUSED else new_guid())
            struct.pack_into("<QQQ", buf, off + 32, p.start_lba, p.end_lba, attrs)
            name = p.name.encode("utf-16-le")[:70]
            buf[off + 56:off + 56 + len(name)] = name
        return bytes(buf)

    def _build_header(self, my_lba: int, other_lba: int, entries_lba: int,
                      entries_crc: int) -> bytes:
        hdr = bytearray(self.sector_size)
        struct.pack_into("<8sIIII", hdr, 0, GPT_SIGNATURE, GPT_REVISION,
                         HEADER_SIZE, 0, 0)
        struct.pack_into("<QQQQ16sQIII", hdr, 24,
                         my_lba, other_lba,
                         self.first_usable_lba(), self.last_usable_lba(),
                         guid_to_bytes(self.disk_guid), entries_lba,
                         self.entry_count, self.entry_size, entries_crc)
        crc = zlib.crc32(bytes(hdr[:HEADER_SIZE])) & 0xFFFFFFFF
        struct.pack_into("<I", hdr, 16, crc)
        return bytes(hdr)

    def _write_protective_mbr(self) -> None:
        sector = bytearray(self.device.read_sectors(0))
        if struct.unpack_from("<H", sector, 510)[0] != 0xAA55:
            sector = bytearray(self.sector_size)
        # bolum giris alanini temizle, tek bir 0xEE girisi koy
        for i in range(4):
            sector[446 + i * 16:446 + (i + 1) * 16] = b"\x00" * 16
        total = min(self.device.sector_count - 1, 0xFFFFFFFF)
        entry = (b"\x00" + b"\x00\x02\x00" + b"\xEE" + b"\xFF\xFF\xFF"
                 + struct.pack("<II", 1, total))
        sector[446:462] = entry
        struct.pack_into("<H", sector, 510, 0xAA55)
        self.device.write_sectors(0, bytes(sector))

    def write(self) -> None:
        self._last_usable = self.backup_entries_lba - 1
        if not self._first_usable:
            self._first_usable = max(2 + self.entry_sectors, self.align_sectors)
        entries = self._build_entries()
        entries_crc = zlib.crc32(entries) & 0xFFFFFFFF
        pad = (-len(entries)) % self.sector_size
        entries_padded = entries + b"\x00" * pad

        self._write_protective_mbr()
        # birincil
        self.device.write_sectors(
            1, self._build_header(1, self.backup_header_lba, self.entries_lba,
                                  entries_crc))
        self.device.write_sectors(self.entries_lba, entries_padded)
        # yedek
        self.device.write_sectors(self.backup_entries_lba, entries_padded)
        self.device.write_sectors(
            self.backup_header_lba,
            self._build_header(self.backup_header_lba, 1,
                               self.backup_entries_lba, entries_crc))
        self.header_backup_ok = True
        flush = getattr(self.device, "flush", None)
        if flush:
            flush()

    def repair_backup(self) -> None:
        """Yedek GPT'yi birincilden yeniden olusturur."""
        self.write()

    # -- bolum islemleri -----------------------------------------------------
    def add_partition(self, start_lba: int, sector_count: int,
                      type_guid: str = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7",
                      name: str = "", bootable: bool = False,
                      attributes: int = 0, **_ignored) -> Partition:
        self.check_range(start_lba, sector_count)
        if len(self.partitions) >= self.entry_count:
            raise PartitionTableError(tr("GPT giris dizisi dolu"))
        part = Partition(index=0, start_lba=start_lba, sector_count=sector_count,
                         scheme="gpt", type_guid=type_guid.upper(),
                         part_guid=new_guid(), name=name, bootable=bootable,
                         attributes=attributes, sector_size=self.sector_size)
        self.partitions.append(part)
        self._renumber()
        self.write()
        return part

    def delete_partition(self, index: int) -> None:
        part = self.get(index)
        self.partitions = [p for p in self.partitions if p is not part]
        self._renumber()
        self.write()

    def set_name(self, index: int, name: str) -> None:
        self.get(index).name = name[:36]
        self.write()

    def set_type(self, index: int, type_guid: str) -> None:
        self.get(index).type_guid = type_guid.upper()
        self.write()

    def set_attribute(self, index: int, bit: int, value: bool) -> None:
        part = self.get(index)
        if value:
            part.attributes |= bit
        else:
            part.attributes &= ~bit
        self.write()

    def known_types(self):
        return dict(GPT_TYPES)