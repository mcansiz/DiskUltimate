"""GPT (GUID Partition Table) okuma/yazma.

Yerlesim (spec: .claude/specs/gpt.md):
  LBA 0        Koruyucu MBR (tip 0xEE)
  LBA 1        Birincil GPT basligi
  LBA 2..33    128 x 128 bayt bolum girisi (4Kn: LBA 2..5)
  LBA n-33..   Yedek giris dizisi (4Kn: n-5..)
  LBA n-1      Yedek GPT basligi

Okuma Linux cekirdeginin `efi.c` kurallarini izler: koruyucu MBR (0xEE)
yoksa disk GPT sayilmaz; birincil baslik CRC/alan denetiminden gecmezse
yedek kullanilir. Bolum numarasi giris yuvasidir ve yazimda korunur.
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


# Koruyucu MBR durumu (Linux `efi.c is_pmbr_valid` ile ayni olcut)
PMBR_NONE = ""
PMBR_PROTECTIVE = "protective"
PMBR_HYBRID = "hybrid"

MAX_ENTRY_BYTES = 4 * 1024 * 1024      # makul giris dizisi siniri


def pmbr_kind(sector: bytes) -> str:
    """LBA 0'daki MBR koruyucu mu, hibrit mi, hicbiri mi?

    Cekirdek GPT'yi ancak 0x55AA imzali ve LBA 1'den baslayan 0xEE girisi
    olan bir MBR'nin arkasinda kabul eder; yoksa diski msdos ayristiricisiyla
    okur. 0xEE disinda dolu giris varsa MBR **hibrittir**.
    """
    if len(sector) < 512 or struct.unpack_from("<H", sector, 510)[0] != 0xAA55:
        return PMBR_NONE
    protective = other = False
    for i in range(4):
        raw = sector[446 + i * 16:446 + (i + 1) * 16]
        ptype = raw[4]
        start = struct.unpack_from("<I", raw, 8)[0]
        if ptype == 0xEE and start == 1:
            protective = True
        elif ptype not in (0x00, 0xEE):
            other = True
    if not protective:
        return PMBR_NONE
    return PMBR_HYBRID if other else PMBR_PROTECTIVE


def hybrid_ranges(sector: bytes) -> List[tuple]:
    """Hibrit MBR'deki 0xEE disi girislerin (baslangic, sektor) listesi."""
    out = []
    for i in range(4):
        raw = sector[446 + i * 16:446 + (i + 1) * 16]
        start, count = struct.unpack_from("<II", raw, 8)
        if raw[4] not in (0x00, 0xEE) and count:
            out.append((start, count))
    return out


def gpt_reserved(sector_size: int, entry_count: int = ENTRY_COUNT,
                 entry_size: int = ENTRY_SIZE) -> tuple:
    """(bas, son) ayrilmis sektor sayisi: MBR + baslik + giris dizisi.

    512 baytta (34, 33); 4Kn'de (6, 5). Sabit 34/33 yalnizca 512 bayt
    sektorde dogrudur.
    """
    entry_sectors = -(-entry_count * entry_size // sector_size)
    return 2 + entry_sectors, 1 + entry_sectors


class _Header:
    """Dogrulanmis bir GPT basligi + giris dizisi."""

    def __init__(self, lba, raw, fields, entries):
        self.lba = lba
        self.raw = raw
        (_sig, _rev, self.hdr_size, self.hdr_crc, _res, self.my_lba,
         self.other_lba, self.first_usable, self.last_usable, self.disk_guid,
         self.entries_lba, self.num_entries, self.entry_size,
         self.entries_crc) = fields
        self.entries = entries


def _check_header(device: BlockDevice, lba: int) -> Optional[_Header]:
    """`lba` konumundaki GPT basligi gecerliyse dondurur, degilse None.

    Cekirdegin `is_gpt_valid` denetimleri: imza, baslik boyu, baslik CRC,
    kendi LBA'si, giris boyu 128, kullanilabilir alan sinirlari ve giris
    dizisi CRC'si.
    """
    last = device.sector_count - 1
    if lba < 1 or lba > last:
        return None
    try:
        raw = device.read_sectors(lba)
    except Exception:                              # noqa: BLE001
        return None
    if raw[:8] != GPT_SIGNATURE:
        return None
    fields = struct.unpack_from("<8sIIII QQ QQ 16s Q III", raw, 0)
    hdr_size, hdr_crc, my_lba = fields[2], fields[3], fields[5]
    first, last_usable = fields[7], fields[8]
    entries_lba, num, esize, ecrc = fields[10], fields[11], fields[12], fields[13]
    if hdr_size < HEADER_SIZE or hdr_size > device.sector_size:
        return None
    check = bytearray(raw[:hdr_size])
    struct.pack_into("<I", check, 16, 0)
    if (zlib.crc32(bytes(check)) & 0xFFFFFFFF) != hdr_crc:
        return None
    if my_lba != lba or esize != ENTRY_SIZE or num == 0 \
            or num * esize > MAX_ENTRY_BYTES:
        return None
    if first > last or last_usable > last or last_usable < first:
        return None
    nbytes = num * esize
    nsectors = -(-nbytes // device.sector_size)
    if entries_lba < 1 or entries_lba + nsectors - 1 > last:
        return None
    try:
        entries = device.read_sectors(entries_lba, nsectors)[:nbytes]
    except Exception:                              # noqa: BLE001
        return None
    if (zlib.crc32(entries) & 0xFFFFFFFF) != ecrc:
        return None
    return _Header(lba, raw, fields, entries)


def locate_headers(device: BlockDevice) -> tuple:
    """(birincil, yedek) gecerli basliklar; gecersiz olan None.

    Yedek once birincilin gosterdigi yerde, sonra diskin son sektorunde
    aranir (goruntu buyutulunce yedek eski sonda kalir).
    """
    primary = _check_header(device, 1)
    candidates = []
    if primary is not None:
        candidates.append(primary.other_lba)
    candidates.append(device.sector_count - 1)
    backup = None
    for lba in dict.fromkeys(candidates):
        backup = _check_header(device, lba) if lba != 1 else None
        if backup is not None:
            break
    return primary, backup


def gpt_signature_present(device: BlockDevice) -> bool:
    """LBA 1'de ya da son sektorde 'EFI PART' imzasi var mi (gecerli olmasa da)?"""
    for lba in (1, device.sector_count - 1):
        try:
            if device.read_sectors(lba)[:8] == GPT_SIGNATURE:
                return True
        except Exception:                          # noqa: BLE001
            continue
    return False


class GPTTable(PartitionTable):
    scheme = "gpt"

    def __init__(self, device: BlockDevice):
        super().__init__(device)
        self.disk_guid = new_guid()
        self.entry_count = ENTRY_COUNT
        self.entry_size = ENTRY_SIZE
        self._first_usable = 0
        self._last_usable = 0
        self._entries_lba = 2
        self.header_backup_ok = True
        self.primary_ok = True
        self.header_crc_ok = True
        self.entries_crc_ok = True
        # Diskten okunan ama gosterilmeyen girisler (baslangic > son ya da
        # disk disi; cekirdek de gostermez). Yazarken aynen korunur.
        self._foreign = {}
        # Okumadaki hibrit MBR: o anda bir GPT bolumune birebir karsilik
        # gelen hibrit girisler. Yazimda bunlar korunamiyorsa yazma reddedilir.
        self.pmbr = PMBR_NONE
        self.hybrid_ranges: List[tuple] = []
        self._hybrid_protected: List[tuple] = []
        # Yeni tablo (olusturma/donusum): koruyucu MBR sifirdan yazilir.
        self._fresh_pmbr = False
        self._zero_bootcode = False
        # Okumada bulunan yapilarin yeri (donusumde silmek icin)
        self.found_structures: List[tuple] = []

    # -- yerlesim hesaplari --------------------------------------------------
    @property
    def entry_sectors(self) -> int:
        total = self.entry_count * self.entry_size
        ss = self.sector_size
        return (total + ss - 1) // ss

    @property
    def entries_lba(self) -> int:
        return self._entries_lba or 2

    @property
    def backup_header_lba(self) -> int:
        return self.device.sector_count - 1

    @property
    def backup_entries_lba(self) -> int:
        return self.backup_header_lba - self.entry_sectors

    def default_first_usable(self) -> int:
        """YENI tablo icin ilk kullanilabilir LBA (1 MiB hizali)."""
        return max(self.entries_lba + self.entry_sectors, self.align_sectors)

    def first_usable_lba(self) -> int:
        # Mevcut tablonun baslikta yazan degeri korunur: sfdisk/gdisk 34'ten
        # baslayan bolum kurabilir; 2048'e zorlamak o bolumu "ilk
        # kullanilabilir LBA'dan once" birakiyordu (P6). Yeni bolumlerin
        # yeri `free_regions` icinde zaten 1 MiB'a hizalanir.
        if self._first_usable:
            return self._first_usable
        return self.default_first_usable()

    def last_usable_lba(self) -> int:
        if self._last_usable:
            return self._last_usable
        return self.backup_entries_lba - 1

    # -- okuma ---------------------------------------------------------------
    @classmethod
    def is_present(cls, device: BlockDevice) -> bool:
        """Cekirdek bu diski GPT olarak okur mu?

        Yalnizca LBA 1'deki imza yetmez (P2): LBA 0'da gecerli koruyucu MBR
        (0xEE) ve CRC'si dogru en az bir baslik gerekir. Uzerine DOS MBR
        yazilmis eski bir GPT boylece MBR'nin onune gecmez.
        """
        try:
            if pmbr_kind(device.read_sectors(0)) == PMBR_NONE:
                return False
            primary, backup = locate_headers(device)
        except Exception:                          # noqa: BLE001
            return False
        return primary is not None or backup is not None

    @classmethod
    def read(cls, device: BlockDevice) -> "GPTTable":
        table = cls(device)
        primary, backup = locate_headers(device)
        if primary is None and backup is None:
            raise PartitionTableError(tr("Gecerli GPT basligi bulunamadi"))
        # Birincil gecerliyse o, degilse yedek kullanilir (cekirdek gibi).
        # Bozuk birincil artik yedegin ustune kopyalanmaz (P3): yazma
        # okunan (gecerli) basliktan yapilir ve birincili onarir.
        used = primary or backup
        table.primary_ok = primary is not None
        table.header_backup_ok = backup is not None
        table.header_crc_ok = primary is not None
        table.entries_crc_ok = primary is not None

        table.disk_guid = bytes_to_guid(used.disk_guid)
        table._first_usable = used.first_usable
        table._last_usable = used.last_usable
        table.entry_count = used.num_entries
        table.entry_size = used.entry_size
        table._entries_lba = primary.entries_lba if primary is not None else 2
        for hdr in (primary, backup):
            if hdr is not None:
                nsect = -(-hdr.num_entries * hdr.entry_size // device.sector_size)
                table.found_structures.append((hdr.lba, 1))
                table.found_structures.append((hdr.entries_lba, nsect))

        lastlba = device.sector_count - 1
        raw = used.entries
        for i in range(used.num_entries):
            entry = raw[i * used.entry_size:(i + 1) * used.entry_size]
            type_guid = bytes_to_guid(entry[0:16])
            if type_guid == GPT_UNUSED:
                continue
            start, end, attrs = struct.unpack_from("<QQQ", entry, 32)
            if start > end or end > lastlba:
                # cekirdek `is_pte_valid` ile ayni: gosterilmez, korunur
                table._foreign[i] = bytes(entry)
                continue
            part_guid = bytes_to_guid(entry[16:32])
            name = entry[56:128].decode("utf-16-le", "ignore").split("\x00")[0]
            # Bolum numarasi = giris yuvasi (cekirdekteki sdXN); bos
            # yuvalar atlanmaz (P4).
            table.partitions.append(Partition(
                index=i + 1, start_lba=start, sector_count=end - start + 1,
                scheme="gpt", type_guid=type_guid, part_guid=part_guid,
                name=name, attributes=attrs,
                bootable=bool(attrs & ATTR_LEGACY_BIOS),
                sector_size=device.sector_size))

        try:
            sector0 = device.read_sectors(0)
        except Exception:                          # noqa: BLE001
            sector0 = b""
        table.pmbr = pmbr_kind(sector0)
        if table.pmbr == PMBR_HYBRID:
            table.hybrid_ranges = hybrid_ranges(sector0)
            current = {(p.start_lba, p.sector_count) for p in table.partitions}
            table._hybrid_protected = [r for r in table.hybrid_ranges
                                       if r in current]
        return table

    def trust_problem(self) -> str:
        """Tablo kuskulu mu? Bos metin = guvenilir.

        Birincil basligi bozuk (yedekten okundu) ya da hibrit MBR'de GPT'de
        olmayan bir alan varsa yalnizca kullanilan alani yedeklemek guvenli
        degildir (usedmap tamamini alir).
        """
        if not self.primary_ok:
            return tr("Birincil GPT basligi bozuk; tablo yedek basliktan okundu")
        for start, count in self.hybrid_ranges:
            end = start + count - 1
            if not any(p.start_lba <= start and end <= p.end_lba
                       for p in self.partitions):
                return tr("Hibrit MBR'de GPT'de olmayan bir alan var (LBA {})",
                          start)
        return ""

    # -- olusturma -----------------------------------------------------------
    @classmethod
    def create(cls, device: BlockDevice) -> "GPTTable":
        head, tail = gpt_reserved(device.sector_size)
        if device.sector_count < head + tail + 1:
            raise PartitionTableError(tr("Disk GPT icin cok kucuk"))
        table = cls(device)
        table.disk_guid = new_guid()
        table._fresh_pmbr = True
        table._first_usable = table.default_first_usable()
        if table._first_usable > table.backup_entries_lba - 1:
            table._first_usable = 2 + table.entry_sectors
        table._last_usable = table.backup_entries_lba - 1
        table.write()
        return table

    # -- yazma ---------------------------------------------------------------
    def first_free_slot(self) -> int:
        used = {p.index for p in self.partitions}
        for slot in range(1, self.entry_count + 1):
            if slot not in used and (slot - 1) not in self._foreign:
                return slot
        raise PartitionTableError(tr("GPT giris dizisi dolu"))

    def _assign_slots(self) -> None:
        """Her bolume bir giris yuvasi: mevcut numara korunur (P4).

        Eskiden her yazimda bolumler baslangica gore siralanip 1'den yeniden
        numaralaniyordu: sfdisk'in 1/3/5 yuvalari bir ad degisikliginden sonra
        1/2/3 oluyor, fstab'daki sda5 aygit adi ve GRUB'un (hd0,gpt5) adresi
        kiriliyordu. Numarasi olmayan (yeni) ya da cakisan bolum ilk bos
        yuvayi alir.
        """
        if len(self.partitions) + len(self._foreign) > self.entry_count:
            raise PartitionTableError(
                tr("GPT en fazla {} bolum destekler", self.entry_count))
        taken = set()
        pending = []
        for p in sorted(self.partitions, key=lambda x: x.start_lba):
            if 1 <= p.index <= self.entry_count and p.index not in taken \
                    and (p.index - 1) not in self._foreign:
                taken.add(p.index)
            else:
                pending.append(p)
        for p in pending:
            p.index = 0
        for p in pending:
            p.index = self.first_free_slot()

    def _check_layout(self, first: int, last: int) -> None:
        """Yazmadan once: bolumler GPT yapilarina ve birbirine degmemeli."""
        for p in self.partitions:
            if p.sector_count <= 0:
                raise PartitionTableError(tr("Bolum boyutu sifir olamaz"))
            if p.start_lba < first:
                raise PartitionTableError(tr(
                    "Bolum {} GPT giris dizisiyle cakisiyor (ilk kullanilabilir "
                    "LBA {}); tablo yazilmadi", p.index, first))
            if p.end_lba > last:
                raise PartitionTableError(tr(
                    "Bolum {} disk sonundaki yedek GPT alanina tasiyor (son "
                    "kullanilabilir LBA {}); tablo yazilmadi", p.index, last))
        ordered = sorted(self.partitions, key=lambda x: x.start_lba)
        for a, b in zip(ordered, ordered[1:]):
            if b.start_lba <= a.end_lba:
                raise PartitionTableError(
                    tr("{} ve {} numarali bolumler cakisiyor", a.index, b.index))

    def _build_entries(self) -> bytes:
        buf = bytearray(self.entry_count * self.entry_size)
        for slot, raw in self._foreign.items():
            off = slot * self.entry_size
            buf[off:off + self.entry_size] = raw[:self.entry_size]
        for p in self.partitions:
            off = (p.index - 1) * self.entry_size
            attrs = p.attributes
            if p.bootable:
                attrs |= ATTR_LEGACY_BIOS
            else:
                attrs &= ~ATTR_LEGACY_BIOS
            p.attributes = attrs
            if p.part_guid == GPT_UNUSED:
                p.part_guid = new_guid()
            buf[off:off + 16] = guid_to_bytes(p.type_guid)
            buf[off + 16:off + 32] = guid_to_bytes(p.part_guid)
            struct.pack_into("<QQQ", buf, off + 32, p.start_lba, p.end_lba, attrs)
            name = p.name.encode("utf-16-le")[:72]
            buf[off + 56:off + 56 + len(name)] = name
        return bytes(buf)

    def _build_header(self, my_lba: int, other_lba: int, entries_lba: int,
                      entries_crc: int, first: int, last: int) -> bytes:
        hdr = bytearray(self.sector_size)
        struct.pack_into("<8sIIII", hdr, 0, GPT_SIGNATURE, GPT_REVISION,
                         HEADER_SIZE, 0, 0)
        struct.pack_into("<QQQQ16sQIII", hdr, 24,
                         my_lba, other_lba, first, last,
                         guid_to_bytes(self.disk_guid), entries_lba,
                         self.entry_count, self.entry_size, entries_crc)
        crc = zlib.crc32(bytes(hdr[:HEADER_SIZE])) & 0xFFFFFFFF
        struct.pack_into("<I", hdr, 16, crc)
        return bytes(hdr)

    def _protective_sector(self) -> Optional[bytes]:
        """Yazilacak LBA 0; None = dokunulmaz (hibrit MBR korunur, P5)."""
        try:
            current = self.device.read_sectors(0)
        except Exception:                          # noqa: BLE001
            current = b""
        if not self._fresh_pmbr and self.pmbr == PMBR_HYBRID \
                and pmbr_kind(current) == PMBR_HYBRID:
            now = {(p.start_lba, p.sector_count) for p in self.partitions}
            lost = [r for r in self._hybrid_protected if r not in now]
            if lost:
                raise PartitionTableError(tr(
                    "Diskte hibrit MBR var ve bu degisiklik hibrit MBR'de de "
                    "kayitli bir bolumu (LBA {}) etkiliyor. Hibrit MBR'yi "
                    "bozmamak icin tablo yazilmadi; once hibrit MBR'yi "
                    "kaldirin (gdisk).", lost[0][0]))
            return None
        sector = bytearray(current) if len(current) == self.sector_size \
            else bytearray(self.sector_size)
        if self._zero_bootcode or \
                struct.unpack_from("<H", sector, 510)[0] != 0xAA55:
            sector = bytearray(self.sector_size)
        # bolum giris alanini temizle, tek bir 0xEE girisi koy
        for i in range(4):
            sector[446 + i * 16:446 + (i + 1) * 16] = b"\x00" * 16
        total = min(self.device.sector_count - 1, 0xFFFFFFFF)
        entry = (b"\x00" + b"\x00\x02\x00" + b"\xEE" + b"\xFF\xFF\xFF"
                 + struct.pack("<II", 1, total))
        sector[446:462] = entry
        struct.pack_into("<H", sector, 510, 0xAA55)
        return bytes(sector)

    def build(self) -> List[tuple]:
        """Yazilacak (LBA, veri) listesi; hicbir sey yazmadan dogrular.

        Butun denetimler (yapi sinirlari, bolum cakismasi, yedek GPT'nin
        son bolume degmesi (P7), hibrit MBR) diske dokunmadan once yapilir.
        """
        backup_header = self.backup_header_lba
        backup_entries = self.backup_entries_lba
        primary_end = self.entries_lba + self.entry_sectors
        first = self._first_usable or self.default_first_usable()
        last = backup_entries - 1
        if primary_end > first or first > last or backup_entries <= primary_end:
            raise PartitionTableError(tr("Disk GPT icin cok kucuk"))
        self._check_layout(first, last)
        self._assign_slots()
        entries = self._build_entries()
        entries_crc = zlib.crc32(entries) & 0xFFFFFFFF
        pad = (-len(entries)) % self.sector_size
        entries_padded = entries + b"\x00" * pad
        writes = [
            (1, self._build_header(1, backup_header, self.entries_lba,
                                   entries_crc, first, last)),
            (self.entries_lba, entries_padded),
            (backup_entries, entries_padded),
            (backup_header, self._build_header(backup_header, 1,
                                               backup_entries, entries_crc,
                                               first, last)),
        ]
        pmbr = self._protective_sector()
        if pmbr is not None:
            # koruyucu MBR en son: yarida kalan yazimda eski LBA 0 (or. DOS
            # MBR) gecerli kalir, 0xEE olmadan GPT yok sayilir
            writes.append((0, pmbr))
        self._first_usable = first
        self._last_usable = last
        return writes

    def write(self) -> None:
        writes = self.build()
        for lba, data in writes:
            self.device.write_sectors(lba, data)
        self.header_backup_ok = True
        self.primary_ok = self.header_crc_ok = self.entries_crc_ok = True
        self._fresh_pmbr = self._zero_bootcode = False
        if self.pmbr != PMBR_HYBRID:
            self.pmbr = PMBR_PROTECTIVE
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
        # yeni bolum ilk bos yuvayi alir; digerleri yerinde kalir (P4)
        part = Partition(index=self.first_free_slot(), start_lba=start_lba,
                         sector_count=sector_count,
                         scheme="gpt", type_guid=type_guid.upper(),
                         part_guid=new_guid(), name=name, bootable=bootable,
                         attributes=attributes, sector_size=self.sector_size)
        self.partitions.append(part)
        try:
            self.write()
        except PartitionTableError:
            self.partitions.remove(part)
            raise
        return part

    def delete_partition(self, index: int) -> None:
        part = self.get(index)
        self.partitions = [p for p in self.partitions if p is not part]
        # numaralar kaydirilmaz: digerleri yuvasinda kalir (P4)
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