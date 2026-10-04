"""UDF (OSTA UDF 1.02 - 2.60, ECMA-167) salt okuma.

Yerlesim:
  * Birim tanima dizisi 32 KiB'den: BEA01 ... NSR02/NSR03 ... TEA01.
  * Capa (AVDP, etiket 2) blok 256'da (yedekleri N-256, N-1); blok boyutu
    bilinmedigi icin 512/1024/2048/4096 denenir: etiket kimligi 2 ve etiket
    konumu 256 olan ilk aday dogrudur.
  * Ana birim tanimlayici dizisi (VDS): Birincil (1), Bolum (5, bolum no +
    baslangic), Mantiksal Birim (6: blok boyu, dosya kumesi konumu, bolum
    haritalari), Sonlandirici (8).
  * Bolum haritalari: tip 1 (fiziksel), tip 2 "*UDF Sparable Partition"
    (fiziksel gibi okunur; kusurlu paket yeniden eslemesi uygulanir),
    "*UDF Metadata Partition" (2.50+: mantiksal bloklar metadata dosyasinin
    kapsamlari uzerinden eslenir). "*UDF Virtual Partition" (VAT, bir kez
    yazilir CD-R) desteklenmez.
  * Dosya Kumesi (256) -> kok dizin ICB'si. Dosya Girisi FE (261) ya da
    Genisletilmis FE (266); kapsam turu ICB etiketinde: kisa (short_ad),
    uzun (long_ad), genis (ext_ad), gomulu (veri girisin icinde).
  * Dizin verisi Dosya Tanimlayicilari (FID, 257) dizisidir; adlar OSTA CS0
    d-string (8 = Latin-1, 16 = UTF-16BE).
"""
from __future__ import annotations

import datetime
import os
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .image import BlockDevice
from ..i18n import tr

TAG_AVDP, TAG_PVD, TAG_PD, TAG_LVD, TAG_USD, TAG_TD, TAG_LVID = 2, 1, 5, 6, 7, 8, 9
TAG_FSD, TAG_FID, TAG_AED, TAG_FE, TAG_EFE = 256, 257, 258, 261, 266
FT_DIRECTORY, FT_FILE, FT_SYMLINK = 4, 5, 12
ALLOC_SHORT, ALLOC_LONG, ALLOC_EXT, ALLOC_EMBEDDED = 0, 1, 2, 3
EXTENT_RECORDED, EXTENT_ALLOCATED, EXTENT_SPARSE, EXTENT_NEXT = 0, 1, 2, 3
FID_HIDDEN, FID_DIRECTORY, FID_DELETED, FID_PARENT = 1, 2, 4, 8


class UdfError(Exception):
    pass


def dstring(raw: bytes) -> str:
    """OSTA CS0 d-string (sabit alan; son bayt uzunluk)."""
    if not raw or not raw[-1]:
        return ""
    return cs0(raw[:raw[-1]])


def cs0(raw: bytes) -> str:
    """OSTA CS0 d-karakterleri: ilk bayt 8 (Latin-1) ya da 16 (UTF-16BE)."""
    if not raw:
        return ""
    if raw[0] == 16:
        body = raw[1:]
        return body[:len(body) // 2 * 2].decode("utf-16-be", "replace")
    if raw[0] in (8, 254, 255):
        return raw[1:].decode("latin-1", "replace")
    return raw.decode("latin-1", "replace")


def timestamp(raw: bytes) -> Optional[datetime.datetime]:
    """ECMA-167 zaman damgasi (12 bayt)."""
    try:
        tz_type, year, month, day, hour, minute, second, cs, hus, us = \
            struct.unpack_from("<HhBBBBBBBB", raw, 0)
        if not year or not month or not day:
            return None
        offset = tz_type & 0x0FFF
        if offset & 0x0800:
            offset -= 0x1000
        tz = datetime.timezone.utc
        if (tz_type >> 12) == 1 and -1440 <= offset <= 1440:
            tz = datetime.timezone(datetime.timedelta(minutes=offset))
        micro = min(999999, cs * 10000 + hus * 100 + us)
        return datetime.datetime(year, month, day, hour, minute, second, micro,
                                 tzinfo=tz).astimezone()
    except (ValueError, OverflowError, struct.error):
        return None


@dataclass
class UdfEntry:
    name: str
    is_dir: bool
    size: int = 0
    icb: Tuple[int, int] = (0, 0)          # (mantiksal blok, bolum referansi)
    mtime: Optional[datetime.datetime] = None
    hidden: bool = False
    symlink: str = ""
    mode: int = 0
    extents: List[Tuple[int, int, int, int]] = field(default_factory=list)
    embedded: bytes = b""                   # gomulu veri (kucuk dosya)


@dataclass
class _Partition:
    kind: str                               # "physical" | "sparable" | "metadata"
    number: int                             # bolum tanimlayicisindaki numara
    start: int = 0                          # fiziksel blok
    length: int = 0
    packet: int = 0                         # sparable: paket boyu
    sparing: Dict[int, int] = field(default_factory=dict)
    meta_file: Tuple[int, int] = (0, 0)     # metadata: dosya konumu (bolum icinde)
    meta_extents: List[Tuple[int, int]] = field(default_factory=list)


class UdfFS:
    """UDF birimi (salt okunur)."""

    def __init__(self, dev: BlockDevice):
        self.dev = dev
        self.block_size = self._find_anchor()
        bs = self.block_size
        avdp = self._read_block(256)
        vds_len, vds_loc = struct.unpack_from("<II", avdp, 16)
        self.label = ""
        self.volume_id = ""
        self.revision = 0
        self._pd: Dict[int, Tuple[int, int]] = {}
        # bolum no -> erisim turu, alan bitmap'i / tablosu (bolume gore short_ad)
        self.pd_detail: Dict[int, Dict[str, object]] = {}
        lvd: Optional[bytes] = None
        for i in range(max(1, min(256, vds_len // bs))):
            d = self._read_block(vds_loc + i)
            tag = struct.unpack_from("<H", d, 0)[0]
            if tag == TAG_PVD and not self.volume_id:
                self.volume_id = dstring(d[24:56])
            elif tag == TAG_PD:
                num, = struct.unpack_from("<H", d, 22)
                start, length = struct.unpack_from("<II", d, 188)
                self._pd[num] = (start, length)
                self.pd_detail[num] = {
                    "access": struct.unpack_from("<I", d, 184)[0],
                    "contents": d[25:31],
                    "space_table": struct.unpack_from("<II", d, 56),
                    "space_bitmap": struct.unpack_from("<II", d, 64),
                    "freed_table": struct.unpack_from("<II", d, 72),
                    "freed_bitmap": struct.unpack_from("<II", d, 80)}
            elif tag == TAG_LVD:
                lvd = d
            elif tag == TAG_TD:
                break
        if lvd is None or not self._pd:
            raise UdfError(tr("UDF mantiksal birim ya da bolum tanimlayicisi yok"))
        self.label = dstring(lvd[84:84 + 128]) or self.volume_id
        lbs = struct.unpack_from("<I", lvd, 212)[0]
        if lbs != bs:
            raise UdfError(tr("UDF mantiksal blok boyu desteklenmiyor: {}", lbs))
        self.revision = struct.unpack_from("<H", lvd, 216 + 24)[0]   # domain suffix
        self.domain_flags = lvd[216 + 26]      # bit 0 donanim, bit 1 yazilim kilidi
        self._lvid = struct.unpack_from("<II", lvd, 432)
        self.partitions: List[_Partition] = self._partition_maps(lvd)
        fsd_len, fsd_block, fsd_part = struct.unpack_from("<IIH", lvd, 248)
        fsd = self._read_logical(fsd_block, fsd_part)
        if struct.unpack_from("<H", fsd, 0)[0] != TAG_FSD:
            raise UdfError(tr("UDF dosya kumesi tanimlayicisi bulunamadi"))
        root_block, root_part = struct.unpack_from("<IH", fsd, 400 + 4)
        self.root_icb = (root_block, root_part)
        self._cache: Dict[Tuple[int, int], List[UdfEntry]] = {}

    # ---- yerlesim ---------------------------------------------------------
    def _find_anchor(self) -> int:
        for bs in (2048, 512, 1024, 4096):
            try:
                d = self.dev.read(256 * bs, 16)
            except Exception:                  # noqa: BLE001
                continue
            if len(d) == 16 and struct.unpack_from("<H", d, 0)[0] == TAG_AVDP \
                    and struct.unpack_from("<I", d, 12)[0] == 256:
                return bs
        raise UdfError(tr("UDF capa tanimlayicisi (AVDP) bulunamadi"))

    def _read_block(self, block: int, count: int = 1) -> bytes:
        return self.dev.read(block * self.block_size, count * self.block_size)

    def _partition_maps(self, lvd: bytes) -> List[_Partition]:
        count = struct.unpack_from("<I", lvd, 268)[0]
        out: List[_Partition] = []
        pos = 440
        for _ in range(count):
            kind, length = lvd[pos], lvd[pos + 1]
            if kind == 1:
                num = struct.unpack_from("<H", lvd, pos + 4)[0]
                out.append(_Partition("physical", num))
            elif kind == 2:
                ident = lvd[pos + 5:pos + 28].rstrip(b"\x00")
                num = struct.unpack_from("<H", lvd, pos + 38)[0]
                if ident.startswith(b"*UDF Sparable Partition"):
                    p = _Partition("sparable", num)
                    p.packet = struct.unpack_from("<H", lvd, pos + 40)[0]
                    n_tables = lvd[pos + 42]
                    table_size = struct.unpack_from("<I", lvd, pos + 44)[0]
                    locs = struct.unpack_from(f"<{n_tables}I", lvd, pos + 48)
                    p.sparing = self._sparing_table(locs, table_size)
                    out.append(p)
                elif ident.startswith(b"*UDF Metadata Partition"):
                    p = _Partition("metadata", num)
                    p.meta_file = (struct.unpack_from("<I", lvd, pos + 40)[0], 0)
                    out.append(p)
                elif ident.startswith(b"*UDF Virtual Partition"):
                    raise UdfError(tr("UDF sanal bolum (VAT, bir kez yazilir disk) "
                                      "bu surumde okunamiyor"))
                else:
                    raise UdfError(tr("Bilinmeyen UDF bolum haritasi: {}",
                                      ident.decode("latin-1", "replace")))
            else:
                raise UdfError(tr("Bilinmeyen UDF bolum haritasi turu: {}", kind))
            pos += max(length, 6)
        for p in out:
            if p.number not in self._pd:
                raise UdfError(tr("UDF bolum tanimlayicisi eksik: {}", p.number))
            p.start, p.length = self._pd[p.number]
        # metadata bolumu: metadata dosyasinin kapsamlari (ayni fiziksel bolum)
        for ref, p in enumerate(out):
            if p.kind == "metadata":
                phys = next(i for i, q in enumerate(out)
                            if q.kind != "metadata" and q.number == p.number)
                entry = self._file_entry(p.meta_file[0], phys)
                p.meta_extents = [(blk, ln) for blk, ln, _pr, typ in
                                  [(e[0], e[1], e[2], e[3]) for e in entry.extents]
                                  if typ != EXTENT_SPARSE]
        return out

    def _sparing_table(self, locs, size: int) -> Dict[int, int]:
        for loc in locs:
            try:
                d = self.dev.read(loc * self.block_size, max(size, 56))
            except Exception:                  # noqa: BLE001
                continue
            if d[16 + 1:16 + 1 + 20].startswith(b"*UDF Sparing Table"):
                n = struct.unpack_from("<H", d, 48)[0]
                table = {}
                for i in range(n):
                    orig, mapped = struct.unpack_from("<II", d, 56 + i * 8)
                    if orig < 0xFFFFFFF0:
                        table[orig] = mapped
                return table
        return {}

    def _physical(self, block: int, ref: int) -> int:
        """Mantiksal blok (bolum referansi ile) -> fiziksel blok."""
        if ref >= len(self.partitions):
            raise UdfError(tr("Gecersiz UDF bolum referansi: {}", ref))
        p = self.partitions[ref]
        if p.kind == "metadata":
            bpb = self.block_size
            pos = block * bpb
            for start, length in p.meta_extents:
                if pos < length:
                    phys_ref = next(i for i, q in enumerate(self.partitions)
                                    if q.kind != "metadata" and q.number == p.number)
                    return self._physical(start + pos // bpb, phys_ref)
                pos -= length
            raise UdfError(tr("UDF metadata blogu disarida: {}", block))
        if p.kind == "sparable" and p.sparing and p.packet:
            base = block - block % p.packet
            mapped = p.sparing.get(base)
            if mapped is not None:
                return mapped + block % p.packet
        return p.start + block

    def _read_logical(self, block: int, ref: int, count: int = 1) -> bytes:
        if count == 1:
            return self._read_block(self._physical(block, ref))
        return b"".join(self._read_block(self._physical(block + i, ref))
                        for i in range(count))

    # ---- dosya girisi --------------------------------------------------------
    def _file_entry(self, block: int, ref: int) -> UdfEntry:
        d = self._read_logical(block, ref)
        tag = struct.unpack_from("<H", d, 0)[0]
        if tag not in (TAG_FE, TAG_EFE):
            raise UdfError(tr("UDF dosya girisi bozuk (etiket {}, blok {})", tag, block))
        ftype = d[16 + 11]
        flags = struct.unpack_from("<H", d, 16 + 18)[0]
        alloc_type = flags & 7
        uid, gid, perms = struct.unpack_from("<III", d, 36)
        size = struct.unpack_from("<Q", d, 56)[0]
        if tag == TAG_FE:
            mtime = timestamp(d[84:96])
            l_ea, alloc_len = struct.unpack_from("<II", d, 168)
            base = 176
        else:
            mtime = timestamp(d[92:104])
            l_ea, alloc_len = struct.unpack_from("<II", d, 208)
            base = 216
        ads = d[base + l_ea:base + l_ea + alloc_len]
        if base + l_ea + alloc_len > len(d):
            raise UdfError(tr("UDF dosya girisi bozuk (blok {})", block))
        entry = UdfEntry(name="", is_dir=ftype == FT_DIRECTORY, size=size,
                         icb=(block, ref), mtime=mtime, mode=perms)
        if alloc_type == ALLOC_EMBEDDED:
            entry.embedded = bytes(ads[:size])
        else:
            entry.extents = self._decode_ads(ads, alloc_type, ref)
        if ftype == FT_SYMLINK:
            entry.symlink = self._symlink_target(self._read_entry(entry, 4096))
        return entry

    def _decode_ads(self, ads: bytes, alloc_type: int, ref: int
                    ) -> List[Tuple[int, int, int, int]]:
        """[(mantiksal blok, bayt uzunlugu, bolum ref, kapsam turu)]"""
        out: List[Tuple[int, int, int, int]] = []
        size = {ALLOC_SHORT: 8, ALLOC_LONG: 16, ALLOC_EXT: 20}.get(alloc_type)
        if size is None:
            raise UdfError(tr("Bilinmeyen UDF kapsam turu: {}", alloc_type))
        pos, guard = 0, 0
        while pos + size <= len(ads):
            raw_len = struct.unpack_from("<I", ads, pos)[0]
            length, kind = raw_len & 0x3FFFFFFF, raw_len >> 30
            if alloc_type == ALLOC_SHORT:
                blk = struct.unpack_from("<I", ads, pos + 4)[0]
                pref = ref
            elif alloc_type == ALLOC_LONG:
                blk, pref = struct.unpack_from("<IH", ads, pos + 4)
            else:
                blk, pref = struct.unpack_from("<IH", ads, pos + 12)
            pos += size
            if not length:
                break
            if kind == EXTENT_NEXT:
                # sonraki kapsam tanimlayicilari baska blokta (AED, etiket 258)
                guard += 1
                if guard > 10000:
                    raise UdfError(tr("UDF kapsam zinciri dongude"))
                aed = self._read_logical(blk, pref)
                alloc_len = struct.unpack_from("<I", aed, 20)[0]
                ads = aed[24:24 + alloc_len]
                pos = 0
                continue
            out.append((blk, length, pref, kind))
        return out

    def iter_entry(self, entry: UdfEntry, chunk: int = 4 * 1024 * 1024):
        """Dosya girisinin verisini parca parca verir (ADR 0081)."""
        remaining = entry.size
        if entry.embedded or not entry.extents:
            yield entry.embedded[:remaining]
            return
        bs = self.block_size
        chunk = max(bs, chunk - chunk % bs)
        for blk, length, pref, kind in entry.extents:
            if remaining <= 0:
                return
            take = min(length, remaining)
            p = self.partitions[pref] if pref < len(self.partitions) else None
            off = 0
            while off < take:
                n = min(chunk, take - off)
                nblocks = (n + bs - 1) // bs
                if kind != EXTENT_RECORDED:
                    data = bytes(n)                 # ayrilmis ama yazilmamis
                elif p is not None and p.kind == "physical":
                    data = self._read_block(p.start + blk + off // bs, nblocks)[:n]
                else:
                    data = self._read_logical(blk + off // bs, pref, nblocks)[:n]
                yield data
                off += n
            remaining -= take

    def _read_entry(self, entry: UdfEntry, max_bytes: int = -1) -> bytes:
        limit = entry.size if max_bytes < 0 else min(entry.size, max_bytes)
        if entry.embedded or not entry.extents:
            return entry.embedded[:limit]
        out = bytearray()
        bs = self.block_size
        for blk, length, pref, kind in entry.extents:
            if len(out) >= limit:
                break
            take = min(length, limit - len(out))
            if kind != EXTENT_RECORDED:
                out += bytes(take)                  # ayrilmis ama yazilmamis / seyrek
                continue
            nblocks = (take + bs - 1) // bs
            p = self.partitions[pref] if pref < len(self.partitions) else None
            if p is not None and p.kind == "physical":
                out += self._read_block(p.start + blk, nblocks)[:take]
            else:
                out += self._read_logical(blk, pref, nblocks)[:take]
        return bytes(out[:limit])

    @staticmethod
    def _symlink_target(data: bytes) -> str:
        """Yol bilesenleri (ECMA-167 4/14.16): tur(1) uzunluk(1) surum(2) ad."""
        parts: List[str] = []
        pos = 0
        absolute = False
        while pos + 4 <= len(data):
            kind, length = data[pos], data[pos + 1]
            ident = data[pos + 4:pos + 4 + length]
            pos += 4 + length
            if kind in (1, 2):
                absolute = True
                parts = []
            elif kind == 3:
                parts.append("..")
            elif kind == 4:
                parts.append(".")
            elif kind == 5:
                parts.append(cs0(ident))
        return ("/" if absolute else "") + "/".join(parts)

    # ---- dizinler --------------------------------------------------------------
    def _dir_entries(self, icb: Tuple[int, int]) -> List[UdfEntry]:
        hit = self._cache.get(icb)
        if hit is not None:
            return hit
        entry = self._file_entry(*icb)
        data = self._read_entry(entry)
        out: List[UdfEntry] = []
        pos = 0
        while pos + 38 <= len(data):
            tag = struct.unpack_from("<H", data, pos)[0]
            if tag != TAG_FID:
                break
            chars = data[pos + 18]
            l_fi = data[pos + 19]
            blk, pref = struct.unpack_from("<IH", data, pos + 20 + 4)
            l_iu = struct.unpack_from("<H", data, pos + 36)[0]
            name_raw = data[pos + 38 + l_iu:pos + 38 + l_iu + l_fi]
            pos += (38 + l_iu + l_fi + 3) & ~3
            if chars & (FID_PARENT | FID_DELETED):
                continue
            child = UdfEntry(name=cs0(name_raw), is_dir=bool(chars & FID_DIRECTORY),
                             icb=(blk, pref), hidden=bool(chars & FID_HIDDEN))
            out.append(child)
        if len(self._cache) > 256:
            self._cache.clear()
        self._cache[icb] = out
        return out

    def _load(self, entry: UdfEntry) -> UdfEntry:
        """FID'den gelen kaydi dosya girisiyle tamamlar (boyut, tarih, kapsam)."""
        full = self._file_entry(*entry.icb)
        full.name = entry.name
        full.hidden = entry.hidden or entry.name.startswith(".")
        return full

    def root(self) -> UdfEntry:
        return UdfEntry(name="", is_dir=True, icb=self.root_icb)

    def resolve(self, path: str) -> UdfEntry:
        node = self.root()
        for part in [p for p in path.replace("\\", "/").split("/") if p]:
            if not node.is_dir:
                raise UdfError(tr("Dizin degil: {}", path))
            children = self._dir_entries(node.icb)
            match = next((c for c in children if c.name == part), None)
            if match is None:
                low = part.lower()
                match = next((c for c in children if c.name.lower() == low), None)
            if match is None:
                raise UdfError(tr("Bulunamadi: {}", path))
            node = match
        return self._load(node) if node.icb != self.root_icb else \
            self._file_entry(*self.root_icb)

    def listdir(self, path: str = "/") -> List[UdfEntry]:
        node = self.resolve(path)
        if not node.is_dir:
            raise UdfError(tr("Dizin degil: {}", path))
        return [self._load(c) for c in self._dir_entries(node.icb)]

    def read_file(self, path: str, max_bytes: int = -1) -> bytes:
        entry = self.resolve(path)
        if entry.is_dir:
            raise UdfError(tr("Dizin okunamaz: {}", path))
        return self._read_entry(entry, max_bytes)

    def extract(self, path: str, dest: str) -> str:
        entry = self.resolve(path)
        if entry.is_dir:
            os.makedirs(dest, exist_ok=True)
            for child in self._dir_entries(entry.icb):
                self.extract(path.rstrip("/") + "/" + child.name,
                             os.path.join(dest, child.name))
            return dest
        os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
        with open(dest, "wb") as fh:
            for piece in self.iter_entry(entry):     # akis (ADR 0081)
                fh.write(piece)
        if entry.mtime:
            ts = entry.mtime.timestamp()
            os.utime(dest, (ts, ts))
        return dest

    def stats(self) -> Dict[str, int]:
        total = sum(p.length for p in self.partitions if p.kind != "metadata") \
            * self.block_size
        free = -1
        length, loc = self._lvid
        if length and loc:
            try:
                d = self._read_block(loc)
                if struct.unpack_from("<H", d, 0)[0] == TAG_LVID:
                    n = struct.unpack_from("<I", d, 72)[0]
                    frees = struct.unpack_from(f"<{n}I", d, 80)
                    free = sum(f for f in frees if f != 0xFFFFFFFF) * self.block_size
            except Exception:                  # noqa: BLE001
                free = -1
        used = total - free if free >= 0 else total
        return {"total_bytes": total, "used_bytes": used,
                "free_bytes": max(free, 0), "cluster_size": self.block_size}
