"""Saf Python FAT12/FAT16/FAT32 surucusu.

Yetenekler:
  * Bicimlendirme (mkfs)               -> FatFS.format()
  * Dizin listeleme, LFN (uzun ad)     -> listdir()
  * Dosya okuma / disa aktarma         -> read_file(), extract()
  * Dosya yazma / ice aktarma          -> write_file(), import_file()
  * Klasor olusturma / silme / yeniden adlandirma
  * Kullanim istatistigi               -> stats()

Harici bagimlilik veya root yetkisi gerektirmez; dogrudan bolum penceresi
(PartitionView) uzerinde calisir.
"""
from __future__ import annotations

import datetime
import io
import os
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .image import BlockDevice
from .platform import restore_owner
from .streamio import CHUNK, group_runs, write_runs
from ..i18n import tr

MAX_FILE_SIZE = 0xFFFFFFFF          # dizin girisindeki boyut alani 32 bit

ATTR_READ_ONLY = 0x01
ATTR_HIDDEN = 0x02
ATTR_SYSTEM = 0x04
ATTR_VOLUME_ID = 0x08
ATTR_DIRECTORY = 0x10
ATTR_ARCHIVE = 0x20
ATTR_LFN = 0x0F

FREE_ENTRY = 0xE5
END_OF_DIR = 0x00

FAT12_MAX = 4084
FAT16_MAX = 65524
FAT32_MAX = 268435444

ILLEGAL_SFN = b'"*+,./:;<=>?[\\]|'


class FatError(Exception):
    pass


@dataclass
class DirEntry:
    name: str                      # uzun ad (varsa) yoksa 8.3
    short_name: str = ""
    attr: int = 0
    cluster: int = 0
    size: int = 0
    mtime: Optional[datetime.datetime] = None
    ctime: Optional[datetime.datetime] = None
    slot_offset: int = 0           # dizin verisi icindeki bayt ofseti
    slot_count: int = 1            # LFN dahil 32 baytlik giris sayisi
    path: str = ""

    @property
    def is_dir(self) -> bool:
        return bool(self.attr & ATTR_DIRECTORY)

    @property
    def is_volume(self) -> bool:
        return bool(self.attr & ATTR_VOLUME_ID) and not self.is_dir

    @property
    def is_hidden(self) -> bool:
        return bool(self.attr & ATTR_HIDDEN)

    @property
    def is_readonly(self) -> bool:
        return bool(self.attr & ATTR_READ_ONLY)

    @property
    def attr_text(self) -> str:
        flags = [("R", ATTR_READ_ONLY), ("H", ATTR_HIDDEN), ("S", ATTR_SYSTEM),
                 ("D", ATTR_DIRECTORY), ("A", ATTR_ARCHIVE)]
        return "".join(ch for ch, bit in flags if self.attr & bit) or "-"


# --------------------------------------------------------------------------
# Tarih/saat donusumleri
# --------------------------------------------------------------------------
def _to_fat_datetime(dt: datetime.datetime) -> Tuple[int, int]:
    year = max(1980, min(2107, dt.year))
    date = ((year - 1980) << 9) | (dt.month << 5) | dt.day
    time = (dt.hour << 11) | (dt.minute << 5) | (dt.second // 2)
    return date, time


def _from_fat_datetime(date: int, time: int) -> Optional[datetime.datetime]:
    if not date:
        return None
    try:
        return datetime.datetime(
            1980 + ((date >> 9) & 0x7F), max(1, (date >> 5) & 0x0F),
            max(1, date & 0x1F), (time >> 11) & 0x1F, (time >> 5) & 0x3F,
            (time & 0x1F) * 2)
    except ValueError:
        return None


def _lfn_checksum(short11: bytes) -> int:
    s = 0
    for ch in short11:
        s = (((s & 1) << 7) + (s >> 1) + ch) & 0xFF
    return s


def _norm(path: str) -> List[str]:
    return [p for p in path.replace("\\", "/").split("/") if p not in ("", ".")]


# --------------------------------------------------------------------------
class FatFS:
    """Bagli bir FAT birimi."""

    def __init__(self, dev: BlockDevice):
        self.dev = dev
        self._fat_cache: Optional[bytearray] = None
        self._fat_dirty = False
        self._free_count: Optional[int] = None
        self._next_free = 2
        self._mount()

    # ---- baglama -----------------------------------------------------------
    def _mount(self) -> None:
        boot = self.dev.read(0, 512)
        if boot[510:512] != b"\x55\xAA":
            raise FatError(tr("FAT onyukleme sektoru imzasi yok"))
        self.bytes_per_sector = struct.unpack_from("<H", boot, 11)[0]
        self.sectors_per_cluster = boot[13]
        self.reserved_sectors = struct.unpack_from("<H", boot, 14)[0]
        self.num_fats = boot[16]
        self.root_entries = struct.unpack_from("<H", boot, 17)[0]
        total16 = struct.unpack_from("<H", boot, 19)[0]
        self.media = boot[21]
        fat16_size = struct.unpack_from("<H", boot, 22)[0]
        total32 = struct.unpack_from("<I", boot, 32)[0]
        if self.bytes_per_sector not in (512, 1024, 2048, 4096):
            raise FatError(tr("Gecersiz sektor boyutu"))
        if self.sectors_per_cluster == 0 or self.num_fats == 0:
            raise FatError(tr("Gecersiz BPB"))
        self.total_sectors = total16 or total32
        self.fat_size = fat16_size or struct.unpack_from("<I", boot, 36)[0]
        self.root_dir_sectors = (self.root_entries * 32 + self.bytes_per_sector - 1) \
            // self.bytes_per_sector
        self.first_data_sector = (self.reserved_sectors
                                  + self.num_fats * self.fat_size
                                  + self.root_dir_sectors)
        data_sectors = self.total_sectors - self.first_data_sector
        if data_sectors <= 0:
            raise FatError(tr("Gecersiz FAT yerlesimi"))
        self.cluster_count = data_sectors // self.sectors_per_cluster
        if self.cluster_count < FAT12_MAX + 1:
            self.fat_type = 12
        elif self.cluster_count < FAT16_MAX + 1:
            self.fat_type = 16
        else:
            self.fat_type = 32
        self.root_cluster = struct.unpack_from("<I", boot, 44)[0] if self.fat_type == 32 else 0
        self.fsinfo_sector = struct.unpack_from("<H", boot, 48)[0] if self.fat_type == 32 else 0
        label_off = 0x47 if self.fat_type == 32 else 0x2B
        self.label = boot[label_off:label_off + 11].decode("latin-1").strip()
        vid_off = 0x43 if self.fat_type == 32 else 0x27
        self.volume_id = struct.unpack_from("<I", boot, vid_off)[0]

    @property
    def cluster_bytes(self) -> int:
        return self.sectors_per_cluster * self.bytes_per_sector

    @property
    def fs_type_name(self) -> str:
        return f"FAT{self.fat_type}"

    @property
    def eoc(self) -> int:
        return {12: 0xFF8, 16: 0xFFF8, 32: 0x0FFFFFF8}[self.fat_type]

    @property
    def max_cluster(self) -> int:
        return self.cluster_count + 1  # 2 tabanli

    @property
    def readonly(self) -> bool:
        return getattr(self.dev, "readonly", False)

    # ---- FAT tablosu -------------------------------------------------------
    def _load_fat(self) -> bytearray:
        if self._fat_cache is None:
            self._fat_cache = bytearray(self.dev.read(
                self.reserved_sectors * self.bytes_per_sector,
                self.fat_size * self.bytes_per_sector))
        return self._fat_cache

    def get_fat(self, cluster: int) -> int:
        fat = self._load_fat()
        if self.fat_type == 32:
            return struct.unpack_from("<I", fat, cluster * 4)[0] & 0x0FFFFFFF
        if self.fat_type == 16:
            return struct.unpack_from("<H", fat, cluster * 2)[0]
        off = cluster + (cluster // 2)
        val = struct.unpack_from("<H", fat, off)[0]
        return (val >> 4) if (cluster & 1) else (val & 0x0FFF)

    def set_fat(self, cluster: int, value: int) -> None:
        fat = self._load_fat()
        if self.fat_type == 32:
            old = struct.unpack_from("<I", fat, cluster * 4)[0]
            struct.pack_into("<I", fat, cluster * 4,
                             (old & 0xF0000000) | (value & 0x0FFFFFFF))
        elif self.fat_type == 16:
            struct.pack_into("<H", fat, cluster * 2, value & 0xFFFF)
        else:
            off = cluster + (cluster // 2)
            val = struct.unpack_from("<H", fat, off)[0]
            if cluster & 1:
                val = (val & 0x000F) | ((value & 0x0FFF) << 4)
            else:
                val = (val & 0xF000) | (value & 0x0FFF)
            struct.pack_into("<H", fat, off, val)
        self._fat_dirty = True

    def flush(self) -> None:
        """FAT onbellegini tum kopyalara yazar."""
        if self._fat_dirty and self._fat_cache is not None:
            for i in range(self.num_fats):
                off = (self.reserved_sectors + i * self.fat_size) * self.bytes_per_sector
                self.dev.write(off, bytes(self._fat_cache))
            self._fat_dirty = False
            self._update_fsinfo()
        f = getattr(self.dev, "flush", None)
        if f:
            f()

    def _update_fsinfo(self) -> None:
        if self.fat_type != 32 or not self.fsinfo_sector:
            return
        try:
            free = self.free_clusters()
            off = self.fsinfo_sector * self.bytes_per_sector
            sector = bytearray(self.dev.read(off, self.bytes_per_sector))
            if sector[0:4] == b"RRaA":
                struct.pack_into("<I", sector, 488, free)
                self.dev.write(off, bytes(sector))
        except Exception:
            pass

    def chain(self, start: int) -> List[int]:
        """Kume zincirini dondurur."""
        out: List[int] = []
        cur = start
        guard = self.cluster_count + 8
        while 2 <= cur <= self.max_cluster and len(out) < guard:
            out.append(cur)
            cur = self.get_fat(cur)
            if cur >= self.eoc or cur == 0:
                break
        return out

    def free_clusters(self) -> int:
        """Bos kume sayisi (onbellekli).

        Sayim FAT tablosu uzerinde toplu yapilir; tahsis/serbest birakma
        sirasinda sayac guncellendigi icin yeniden taranmaz.
        """
        if self._free_count is None:
            self._free_count = self._count_free()
        return self._free_count

    def _count_free(self) -> int:
        import array
        import sys as _sys
        fat = self._load_fat()
        last = self.max_cluster
        if self.fat_type == 32:
            arr = array.array("I")
            ham = bytes(fat[8:(last + 1) * 4])
            arr.frombytes(ham[:len(ham) - len(ham) % 4])
            if _sys.byteorder != "little":
                arr.byteswap()
            return sum(1 for v in arr if (v & 0x0FFFFFFF) == 0)
        if self.fat_type == 16:
            arr = array.array("H")
            ham = bytes(fat[4:(last + 1) * 2])
            arr.frombytes(ham[:len(ham) - len(ham) % 2])
            if _sys.byteorder != "little":
                arr.byteswap()
            return arr.count(0)
        return sum(1 for c in range(2, last + 1) if self.get_fat(c) == 0)

    def alloc_cluster(self, prev: Optional[int] = None) -> int:
        """Bos bir kume ayirir.

        `_next_free` ipucundan taranir; liste uretilmez, boylece ardisik
        tahsisler amortize O(1) olur (buyuk dosya yazmanin sicak yolu).
        """
        start = max(2, getattr(self, "_next_free", 2))
        upper = self.max_cluster
        for span in (range(start, upper + 1), range(2, min(start, upper + 1))):
            for c in span:
                if self.get_fat(c) == 0:
                    self.set_fat(c, self.eoc | 0x7)
                    if prev:
                        self.set_fat(prev, c)
                    self._next_free = c + 1
                    if self._free_count is not None:
                        self._free_count -= 1
                    return c
        raise FatError(tr("Birimde bos kume kalmadi"))

    def free_chain(self, start: int) -> None:
        serbest = 0
        for c in self.chain(start):
            self.set_fat(c, 0)
            serbest += 1
            if c < getattr(self, "_next_free", 2):
                self._next_free = c        # bosalan yeri bir sonraki tahsiste kullan
        if self._free_count is not None:
            self._free_count += serbest

    # ---- kume G/C ----------------------------------------------------------
    def cluster_offset(self, cluster: int) -> int:
        return ((self.first_data_sector + (cluster - 2) * self.sectors_per_cluster)
                * self.bytes_per_sector)

    def read_cluster(self, cluster: int) -> bytes:
        return self.dev.read(self.cluster_offset(cluster), self.cluster_bytes)

    def write_cluster(self, cluster: int, data: bytes) -> None:
        if len(data) < self.cluster_bytes:
            data = data + b"\x00" * (self.cluster_bytes - len(data))
        self.dev.write(self.cluster_offset(cluster), data[:self.cluster_bytes])

    # ---- dizin verisi ------------------------------------------------------
    @property
    def root_dir_offset(self) -> int:
        return ((self.reserved_sectors + self.num_fats * self.fat_size)
                * self.bytes_per_sector)

    def _read_dir(self, cluster: Optional[int]) -> Tuple[bytearray, List[int]]:
        if cluster is None:  # FAT12/16 sabit kok dizin
            return bytearray(self.dev.read(self.root_dir_offset,
                                           self.root_dir_sectors * self.bytes_per_sector)), []
        chain = self.chain(cluster)
        if not chain:
            raise FatError(tr("Bozuk dizin kume zinciri"))
        buf = bytearray()
        for c in chain:
            buf += self.read_cluster(c)
        return buf, chain

    def _write_dir(self, cluster: Optional[int], data: bytearray,
                   chain: List[int]) -> None:
        if cluster is None:
            cap = self.root_dir_sectors * self.bytes_per_sector
            if len(data) > cap:
                raise FatError(tr("Kok dizin dolu (FAT16 giris siniri)"))
            self.dev.write(self.root_dir_offset, bytes(data).ljust(cap, b"\x00"))
            return
        need = max(1, (len(data) + self.cluster_bytes - 1) // self.cluster_bytes)
        while len(chain) < need:
            chain.append(self.alloc_cluster(chain[-1]))
        padded = bytes(data).ljust(need * self.cluster_bytes, b"\x00")
        for i, c in enumerate(chain[:need]):
            self.write_cluster(c, padded[i * self.cluster_bytes:(i + 1) * self.cluster_bytes])

    # ---- giris cozumleme ---------------------------------------------------
    def _parse_dir(self, data: bytes, base_path: str = "/") -> List[DirEntry]:
        out: List[DirEntry] = []
        lfn_parts: Dict[int, str] = {}
        lfn_checksum = -1
        lfn_start = -1
        for off in range(0, len(data) - 31, 32):
            raw = data[off:off + 32]
            first = raw[0]
            if first == END_OF_DIR:
                break
            if first == FREE_ENTRY:
                lfn_parts.clear()
                lfn_start = -1
                continue
            attr = raw[11]
            if attr == ATTR_LFN:
                seq = first & 0x3F
                if first & 0x40:
                    lfn_parts.clear()
                    lfn_checksum = raw[13]
                    lfn_start = off
                chars = raw[1:11] + raw[14:26] + raw[28:32]
                text = chars.decode("utf-16-le", "ignore")
                cut = text.find("￿")
                if cut >= 0:
                    text = text[:cut]
                cut = text.find("\x00")
                if cut >= 0:
                    text = text[:cut]
                lfn_parts[seq] = text
                continue

            short11 = raw[0:11]
            short = self._decode_short(short11, raw[12])
            name = short
            slot_off = off
            slots = 1
            if lfn_parts and _lfn_checksum(short11) == lfn_checksum and lfn_start >= 0:
                name = "".join(lfn_parts[k] for k in sorted(lfn_parts))
                slot_off = lfn_start
                slots = (off - lfn_start) // 32 + 1
            lfn_parts = {}
            lfn_start = -1

            cluster = (struct.unpack_from("<H", raw, 20)[0] << 16) | \
                struct.unpack_from("<H", raw, 26)[0]
            size = struct.unpack_from("<I", raw, 28)[0]
            mdate, mtime = struct.unpack_from("<HH", raw, 24)[1], struct.unpack_from("<H", raw, 22)[0]
            mdate = struct.unpack_from("<H", raw, 24)[0]
            cdate = struct.unpack_from("<H", raw, 16)[0]
            ctime = struct.unpack_from("<H", raw, 14)[0]
            entry = DirEntry(
                name=name, short_name=short, attr=attr, cluster=cluster,
                size=size, mtime=_from_fat_datetime(mdate, mtime),
                ctime=_from_fat_datetime(cdate, ctime),
                slot_offset=slot_off, slot_count=slots,
                path=(base_path.rstrip("/") + "/" + name) if name not in (".", "..") else base_path)
            out.append(entry)
        return out

    @staticmethod
    def _decode_short(short11: bytes, nt_flags: int = 0) -> str:
        base = short11[:8].decode("latin-1").rstrip()
        ext = short11[8:].decode("latin-1").rstrip()
        if short11[0] == 0x05:
            base = "\xE5" + base[1:]
        if nt_flags & 0x08:
            base = base.lower()
        if nt_flags & 0x10:
            ext = ext.lower()
        return f"{base}.{ext}" if ext else base

    # ---- gezinme -----------------------------------------------------------
    def _dir_cluster(self, path: str) -> Optional[int]:
        """Yol icin dizin kume numarasi (kok icin FAT32'de root_cluster,
        FAT12/16'da None)."""
        parts = _norm(path)
        cluster: Optional[int] = self.root_cluster if self.fat_type == 32 else None
        cur = "/"
        for part in parts:
            entries = self._list_raw(cluster, cur)
            match = None
            for e in entries:
                if e.name.lower() == part.lower() or e.short_name.lower() == part.lower():
                    match = e
                    break
            if match is None:
                raise FatError(tr("Yol bulunamadi: {}", path))
            if not match.is_dir:
                raise FatError(tr("Dizin degil: {}", path))
            cluster = match.cluster if match.cluster else (
                self.root_cluster if self.fat_type == 32 else None)
            cur = cur.rstrip("/") + "/" + part
        return cluster

    def _list_raw(self, cluster: Optional[int], base_path: str) -> List[DirEntry]:
        data, _chain = self._read_dir(cluster)
        return self._parse_dir(data, base_path)

    def listdir(self, path: str = "/", include_hidden: bool = True,
                include_system: bool = False) -> List[DirEntry]:
        """Dizin icerigini dondurur ('.', '..' ve birim etiketi haric)."""
        cluster = self._dir_cluster(path)
        entries = self._list_raw(cluster, path if path.startswith("/") else "/" + path)
        out = []
        for e in entries:
            if e.name in (".", "..") or e.is_volume:
                continue
            if not include_hidden and e.is_hidden:
                continue
            if not include_system and (e.attr & ATTR_SYSTEM) and not include_hidden:
                continue
            out.append(e)
        out.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        return out

    def find(self, path: str) -> DirEntry:
        parts = _norm(path)
        if not parts:
            return DirEntry(name="/", attr=ATTR_DIRECTORY,
                            cluster=self.root_cluster, path="/")
        parent = "/" + "/".join(parts[:-1])
        cluster = self._dir_cluster(parent)
        for e in self._list_raw(cluster, parent):
            if e.name.lower() == parts[-1].lower() or e.short_name.lower() == parts[-1].lower():
                return e
        raise FatError(tr("Bulunamadi: {}", path))

    def exists(self, path: str) -> bool:
        try:
            self.find(path)
            return True
        except FatError:
            return False

    # ---- dosya okuma -------------------------------------------------------
    def read_file(self, path: str, max_bytes: int = -1) -> bytes:
        entry = self.find(path)
        if entry.is_dir:
            raise FatError(tr("Dizin dosya olarak okunamaz"))
        return self.read_entry(entry, max_bytes)

    def iter_file(self, path: str, chunk: int = CHUNK):
        """Dosyayi parca parca verir; bellek dosya boyutundan bagimsiz (ADR 0081)."""
        entry = self.find(path)
        if entry.is_dir:
            raise FatError(tr("Dizin dosya olarak okunamaz"))
        remaining = entry.size
        if not entry.cluster or remaining <= 0:
            return
        cb = self.cluster_bytes
        per = max(1, chunk // cb)
        for start, count in group_runs(self.chain(entry.cluster)):
            for i in range(0, count, per):
                n = min(per, count - i)
                data = self.dev.read(self.cluster_offset(start + i), n * cb)
                data = data[:remaining]
                remaining -= len(data)
                yield data
                if remaining <= 0:
                    return

    def read_entry(self, entry: DirEntry, max_bytes: int = -1) -> bytes:
        if entry.cluster == 0 or entry.size == 0:
            return b""
        limit = entry.size if max_bytes < 0 else min(entry.size, max_bytes)
        out = bytearray()
        for c in self.chain(entry.cluster):
            out += self.read_cluster(c)
            if len(out) >= limit:
                break
        return bytes(out[:limit])

    def extract(self, path: str, dest: str) -> str:
        """Birimdeki dosyayi yerel diske kopyalar."""
        entry = self.find(path)
        if entry.is_dir:
            return self.extract_tree(path, dest)
        if os.path.isdir(dest):
            dest = os.path.join(dest, entry.name)
        os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
        remaining = entry.size
        with open(dest, "wb") as fh:
            for c in self.chain(entry.cluster) if entry.cluster else []:
                if remaining <= 0:
                    break
                blob = self.read_cluster(c)
                fh.write(blob[:remaining])
                remaining -= len(blob)
        restore_owner(dest)     # yetkili kopyada dosya root'a ait kalmasin
        if entry.mtime:
            ts = entry.mtime.timestamp()
            os.utime(dest, (ts, ts))
        return dest

    def extract_tree(self, path: str, dest_dir: str) -> str:
        entry = self.find(path) if _norm(path) else DirEntry(
            name="root", attr=ATTR_DIRECTORY, cluster=self.root_cluster)
        target = os.path.join(dest_dir, entry.name) if _norm(path) else dest_dir
        os.makedirs(target, exist_ok=True)
        for child in self.listdir(path):
            child_path = path.rstrip("/") + "/" + child.name
            if child.is_dir:
                self.extract_tree(child_path, target)
            else:
                self.extract(child_path, os.path.join(target, child.name))
        return target

    # ---- yazma yardimcilari ------------------------------------------------
    def _make_short_name(self, name: str, existing: List[str]) -> Tuple[bytes, bool]:
        """8.3 kisa ad uretir. (11 bayt, lfn_gerekli)"""
        upper = name.upper()
        base, _dot, ext = upper.rpartition(".")
        if not _dot:
            base, ext = upper, ""

        def clean(part: str) -> str:
            out = ""
            for ch in part:
                b = ch.encode("latin-1", "replace")
                if ch == " " or b in ILLEGAL_SFN or ord(ch) < 0x20 or ord(ch) > 0x7E:
                    out += "_"
                else:
                    out += ch
            return out

        cbase, cext = clean(base), clean(ext)[:3]
        lossy = (cbase != base or cext != ext or len(base) > 8 or len(ext) > 3
                 or name != upper or " " in name)
        short_base = cbase[:8] or "_"
        if not lossy:
            cand = (short_base.ljust(8) + cext.ljust(3)).encode("latin-1")
            if cand.decode("latin-1").strip().replace(" ", "") and \
               self._decode_short(cand).lower() not in [e.lower() for e in existing]:
                return cand, False
        taken = {e.upper() for e in existing}
        for i in range(1, 1000):
            suffix = f"~{i}"
            stem = (short_base[:8 - len(suffix)] + suffix).ljust(8)
            cand_text = (stem.strip() + ("." + cext if cext else "")).upper()
            if cand_text not in taken:
                return (stem + cext.ljust(3)).encode("latin-1"), True
        raise FatError(tr("Kisa ad uretilemedi"))

    def _build_entry_bytes(self, name: str, short11: bytes, attr: int,
                           cluster: int, size: int,
                           when: Optional[datetime.datetime] = None,
                           need_lfn: bool = True) -> bytes:
        when = when or datetime.datetime.now()
        date, time = _to_fat_datetime(when)
        entry = bytearray(32)
        entry[0:11] = short11
        entry[11] = attr
        struct.pack_into("<H", entry, 14, time)
        struct.pack_into("<H", entry, 16, date)
        struct.pack_into("<H", entry, 18, date)
        struct.pack_into("<H", entry, 20, (cluster >> 16) & 0xFFFF)
        struct.pack_into("<H", entry, 22, time)
        struct.pack_into("<H", entry, 24, date)
        struct.pack_into("<H", entry, 26, cluster & 0xFFFF)
        struct.pack_into("<I", entry, 28, size)
        if not need_lfn:
            return bytes(entry)
        checksum = _lfn_checksum(short11)
        encoded = name.encode("utf-16-le")
        chars = [encoded[i:i + 2] for i in range(0, len(encoded), 2)]
        chars.append(b"\x00\x00")
        while len(chars) % 13:
            chars.append(b"\xFF\xFF")
        total = len(chars) // 13
        blocks = []
        for n in range(total):
            part = chars[n * 13:(n + 1) * 13]
            lfn = bytearray(32)
            lfn[0] = (n + 1) | (0x40 if n == total - 1 else 0)
            lfn[11] = ATTR_LFN
            lfn[13] = checksum
            payload = b"".join(part)
            lfn[1:11] = payload[0:10]
            lfn[14:26] = payload[10:22]
            lfn[28:32] = payload[22:26]
            blocks.append(bytes(lfn))
        blocks.reverse()
        return b"".join(blocks) + bytes(entry)

    def _insert_entry(self, dir_path: str, blob: bytes) -> None:
        cluster = self._dir_cluster(dir_path)
        data, chain = self._read_dir(cluster)
        need = len(blob) // 32
        pos = self._find_free_slots(data, need)
        if pos < 0:
            if cluster is None:
                raise FatError(tr("Kok dizin dolu"))
            # Yeni kume sona eklenir ve bos yuva aramasi TEKRARLANIR: sondaki
            # bos yuvalar yeni kumeyle birlesir. Giris dogrudan yeni kumenin
            # basina yazilirsa aradaki 0x00 ("dizin sonu") yuvasi okuyucuyu
            # orada durdurur ve giris hic gorunmez (matris testinde olculdu).
            data += bytearray(self.cluster_bytes * ((need * 32) // self.cluster_bytes + 1))
            pos = self._find_free_slots(data, need)
        data[pos:pos + len(blob)] = blob
        end = pos + len(blob)
        if end < len(data) and data[end] not in (END_OF_DIR, FREE_ENTRY):
            pass
        self._write_dir(cluster, data, chain)

    @staticmethod
    def _find_free_slots(data: bytearray, need: int) -> int:
        run = 0
        start = -1
        for off in range(0, len(data) - 31, 32):
            first = data[off]
            if first in (END_OF_DIR, FREE_ENTRY):
                if run == 0:
                    start = off
                run += 1
                if run >= need:
                    return start
            else:
                run = 0
                start = -1
        return -1

    def _existing_names(self, dir_path: str) -> List[str]:
        cluster = self._dir_cluster(dir_path)
        names = []
        for e in self._list_raw(cluster, dir_path):
            names.append(e.name)
            names.append(e.short_name)
        return names

    # ---- yazma islemleri ---------------------------------------------------
    def write_file(self, path: str, data: bytes, overwrite: bool = True) -> DirEntry:
        """Birime dosya yazar (ust dizin var olmali)."""
        return self.write_stream(path, io.BytesIO(data), len(data), overwrite)

    def write_stream(self, path: str, src, size: int,
                     overwrite: bool = True) -> DirEntry:
        """Kaynaktan `size` bayti parca parca okuyup dosya olarak yazar.

        Bellek dosya boyutundan bagimsizdir (ADR 0081). Sinir ve bos alan
        **kume ayrilmadan once** denetlenir: eskiden 4 GiB ustu dosya once
        tamamen yazilip dizin girisinde hata veriyor, kumeler bosa
        cikmiyordu.
        """
        if self.readonly:
            raise FatError(tr("Birim salt okunur"))
        parts = _norm(path)
        if not parts:
            raise FatError(tr("Gecersiz dosya yolu"))
        if size > MAX_FILE_SIZE:
            raise FatError(tr("FAT en fazla 4 GiB - 1 bayt dosya alir ({} bayt "
                              "istendi)", size))
        name = parts[-1]
        parent = "/" + "/".join(parts[:-1])
        if self.exists(path):
            if not overwrite:
                raise FatError(tr("Dosya zaten var: {}", path))
            self.remove(path)
        first_cluster = 0
        if size:
            need = (size + self.cluster_bytes - 1) // self.cluster_bytes
            if need > self.free_clusters():
                raise FatError(tr("Birimde yer yok: {} kume gerekli, {} bos",
                                  need, self.free_clusters()))
            clusters = []
            prev = None
            try:
                for _ in range(need):
                    c = self.alloc_cluster(prev)
                    clusters.append(c)
                    prev = c
                first_cluster = clusters[0]
                write_runs(src, size, group_runs(clusters), self.cluster_bytes,
                           lambda start, off, data: self.dev.write(
                               self.cluster_offset(start) + off, data))
            except Exception:
                if clusters:
                    self.free_chain(clusters[0])      # yarim dosya kume sizdirmasin
                raise
        short11, need_lfn = self._make_short_name(name, self._existing_names(parent))
        blob = self._build_entry_bytes(name, short11, ATTR_ARCHIVE,
                                       first_cluster, size, need_lfn=need_lfn)
        self._insert_entry(parent, blob)
        self.flush()
        return self.find(path)

    def import_file(self, local_path: str, dest_dir: str = "/",
                    name: Optional[str] = None) -> DirEntry:
        name = name or os.path.basename(local_path)
        with open(local_path, "rb") as fh:
            return self.write_stream(dest_dir.rstrip("/") + "/" + name, fh,
                                     os.fstat(fh.fileno()).st_size)

    def import_tree(self, local_dir: str, dest_dir: str = "/") -> int:
        """Yerel klasoru birime kopyalar; kopyalanan dosya sayisini dondurur."""
        count = 0
        base = os.path.basename(os.path.normpath(local_dir))
        target = dest_dir.rstrip("/") + "/" + base
        if not self.exists(target):
            self.mkdir(target)
        for item in sorted(os.listdir(local_dir)):
            src = os.path.join(local_dir, item)
            if os.path.isdir(src):
                count += self.import_tree(src, target)
            elif os.path.isfile(src):
                self.import_file(src, target)
                count += 1
        return count

    def mkdir(self, path: str) -> DirEntry:
        if self.readonly:
            raise FatError(tr("Birim salt okunur"))
        parts = _norm(path)
        if not parts:
            raise FatError(tr("Gecersiz klasor yolu"))
        name = parts[-1]
        parent = "/" + "/".join(parts[:-1])
        if self.exists(path):
            raise FatError(tr("Zaten var: {}", path))
        cluster = self.alloc_cluster()
        self.write_cluster(cluster, b"\x00" * self.cluster_bytes)
        # "." ve ".." girisleri
        parent_cluster = self._dir_cluster(parent) or 0
        if self.fat_type == 32 and parent_cluster == self.root_cluster:
            parent_cluster = 0
        now = datetime.datetime.now()
        dot = self._build_entry_bytes(".", b".          ", ATTR_DIRECTORY,
                                      cluster, 0, now, need_lfn=False)
        dotdot = self._build_entry_bytes("..", b"..         ", ATTR_DIRECTORY,
                                         parent_cluster, 0, now, need_lfn=False)
        self.write_cluster(cluster, dot + dotdot)
        short11, need_lfn = self._make_short_name(name, self._existing_names(parent))
        blob = self._build_entry_bytes(name, short11, ATTR_DIRECTORY, cluster, 0,
                                       now, need_lfn=need_lfn)
        self._insert_entry(parent, blob)
        self.flush()
        return self.find(path)

    def makedirs(self, path: str) -> None:
        cur = ""
        for part in _norm(path):
            cur = cur + "/" + part
            if not self.exists(cur):
                self.mkdir(cur)

    def remove(self, path: str, recursive: bool = False) -> None:
        """Dosya veya (bos) klasoru siler."""
        if self.readonly:
            raise FatError(tr("Birim salt okunur"))
        parts = _norm(path)
        if not parts:
            raise FatError(tr("Kok dizin silinemez"))
        entry = self.find(path)
        if entry.is_dir:
            children = self.listdir(path)
            if children and not recursive:
                raise FatError(tr("Klasor bos degil"))
            for child in children:
                self.remove(path.rstrip("/") + "/" + child.name, recursive=True)
        parent = "/" + "/".join(parts[:-1])
        cluster = self._dir_cluster(parent)
        data, chain = self._read_dir(cluster)
        # girisi yeniden bul (ofsetler degismis olabilir)
        for e in self._parse_dir(data, parent):
            if e.name.lower() == parts[-1].lower():
                entry = e
                break
        for i in range(entry.slot_count):
            data[entry.slot_offset + i * 32] = FREE_ENTRY
        self._write_dir(cluster, data, chain)
        if entry.cluster:
            self.free_chain(entry.cluster)
        self.flush()

    def rename(self, path: str, new_name: str) -> DirEntry:
        entry = self.find(path)
        parts = _norm(path)
        parent = "/" + "/".join(parts[:-1])
        data = self.read_entry(entry) if not entry.is_dir else b""
        if entry.is_dir:
            # klasorde yalnizca giris degistirilir, veri yerinde kalir
            cluster = self._dir_cluster(parent)
            buf, chain = self._read_dir(cluster)
            for i in range(entry.slot_count):
                buf[entry.slot_offset + i * 32] = FREE_ENTRY
            self._write_dir(cluster, buf, chain)
            short11, need_lfn = self._make_short_name(new_name, self._existing_names(parent))
            blob = self._build_entry_bytes(new_name, short11, entry.attr,
                                           entry.cluster, 0, entry.mtime,
                                           need_lfn=need_lfn)
            self._insert_entry(parent, blob)
            self.flush()
        else:
            self.remove(path)
            self.write_file(parent.rstrip("/") + "/" + new_name, data)
        return self.find(parent.rstrip("/") + "/" + new_name)

    def set_label(self, label: str) -> None:
        """Birim etiketini onyukleme sektorunde gunceller."""
        label11 = label.upper().encode("latin-1", "replace")[:11].ljust(11, b" ")
        off = 0x47 if self.fat_type == 32 else 0x2B
        boot = bytearray(self.dev.read(0, 512))
        boot[off:off + 11] = label11
        self.dev.write(0, bytes(boot))
        if self.fat_type == 32:
            try:
                backup = bytearray(self.dev.read(6 * self.bytes_per_sector, 512))
                if backup[510:512] == b"\x55\xAA":
                    backup[off:off + 11] = label11
                    self.dev.write(6 * self.bytes_per_sector, bytes(backup))
            except Exception:
                pass
        self.label = label.strip()
        self.flush()

    # ---- istatistik --------------------------------------------------------
    def stats(self) -> Dict[str, int]:
        free = self.free_clusters()
        total = self.cluster_count
        return {
            "total_bytes": total * self.cluster_bytes,
            "free_bytes": free * self.cluster_bytes,
            "used_bytes": (total - free) * self.cluster_bytes,
            "cluster_size": self.cluster_bytes,
            "total_clusters": total,
            "free_clusters": free,
        }

    # ======================================================================
    # Bicimlendirme
    # ======================================================================
    @staticmethod
    def choose_fat_type(total_sectors: int, bytes_per_sector: int = 512) -> int:
        size = total_sectors * bytes_per_sector
        if size < 16 * 1024 * 1024:
            return 12
        if size < 512 * 1024 * 1024:
            return 16
        return 32

    @staticmethod
    def default_cluster_sectors(total_sectors: int, fat_type: int,
                                bytes_per_sector: int = 512) -> int:
        size = total_sectors * bytes_per_sector
        mb = size / (1024 * 1024)
        if fat_type == 32:
            if mb <= 260:
                kb = 0.5
            elif mb <= 8192:
                kb = 4
            elif mb <= 16384:
                kb = 8
            elif mb <= 32768:
                kb = 16
            else:
                kb = 32
        elif fat_type == 16:
            if mb <= 16:
                kb = 2
            elif mb <= 128:
                kb = 2
            elif mb <= 256:
                kb = 4
            elif mb <= 512:
                kb = 8
            elif mb <= 1024:
                kb = 16
            elif mb <= 2048:
                kb = 32
            else:
                kb = 64
        else:
            kb = 4 if mb > 8 else 1
        return max(1, int(kb * 1024 // bytes_per_sector))

    @staticmethod
    def format(dev: BlockDevice, fat_type: int = 0, label: str = "",
               cluster_sectors: int = 0, num_fats: int = 2,
               hidden_sectors: int = 0, volume_id: int = 0,
               quick: bool = True) -> "FatFS":
        """Bolumu FAT olarak bicimlendirir.

        fat_type: 0=otomatik, 12/16/32
        cluster_sectors: 0=otomatik
        quick: True ise yalnizca metaveri sifirlanir
        """
        bps = dev.sector_size
        total = dev.sector_count
        if total < 128:
            raise FatError(tr("Bolum FAT icin cok kucuk"))
        if fat_type == 0:
            fat_type = FatFS.choose_fat_type(total, bps)
        if cluster_sectors == 0:
            cluster_sectors = FatFS.default_cluster_sectors(total, fat_type, bps)

        limits = {12: FAT12_MAX, 16: FAT16_MAX, 32: FAT32_MAX}
        mins = {12: 1, 16: FAT12_MAX + 1, 32: FAT16_MAX + 1}

        # gecerli bir kume boyutu bulana kadar buyut
        for _ in range(10):
            layout = FatFS._compute_layout(total, bps, fat_type, cluster_sectors, num_fats)
            clusters = layout["clusters"]
            if clusters > limits[fat_type]:
                if cluster_sectors >= 128:
                    raise FatError(tr("FAT{} bu boyut icin uygun degil; FAT32 "
                                      "secin", fat_type))
                cluster_sectors *= 2
                continue
            if clusters < mins[fat_type]:
                if cluster_sectors > 1:
                    cluster_sectors //= 2
                    continue
                raise FatError(
                    tr("Bolum FAT{} icin cok kucuk (kume sayisi {})",
                       fat_type, clusters))
            break
        else:
            raise FatError(tr("Uygun FAT yerlesimi hesaplanamadi"))

        if volume_id == 0:
            now = datetime.datetime.now()
            volume_id = ((now.year << 16) | (now.month << 8) | now.day) ^ \
                ((now.hour << 16) | (now.minute << 8) | now.second) ^ int(now.microsecond)
            volume_id &= 0xFFFFFFFF

        reserved = layout["reserved"]
        fat_size = layout["fat_size"]
        root_entries = layout["root_entries"]
        root_sectors = layout["root_sectors"]

        boot = bytearray(bps)
        boot[0:3] = b"\xEB\x58\x90" if fat_type == 32 else b"\xEB\x3C\x90"
        boot[3:11] = b"MSWIN4.1"
        struct.pack_into("<H", boot, 11, bps)
        boot[13] = cluster_sectors
        struct.pack_into("<H", boot, 14, reserved)
        boot[16] = num_fats
        struct.pack_into("<H", boot, 17, root_entries)
        struct.pack_into("<H", boot, 19, total if total < 0x10000 and fat_type != 32 else 0)
        boot[21] = 0xF8
        struct.pack_into("<H", boot, 22, fat_size if fat_type != 32 else 0)
        struct.pack_into("<H", boot, 24, 63)
        struct.pack_into("<H", boot, 26, 255)
        struct.pack_into("<I", boot, 28, hidden_sectors)
        struct.pack_into("<I", boot, 32, 0 if (total < 0x10000 and fat_type != 32) else total)
        label11 = (label.upper().encode("latin-1", "replace")[:11] or b"NO NAME").ljust(11, b" ")
        if fat_type == 32:
            struct.pack_into("<I", boot, 36, fat_size)
            struct.pack_into("<H", boot, 40, 0)
            struct.pack_into("<H", boot, 42, 0)
            struct.pack_into("<I", boot, 44, 2)
            struct.pack_into("<H", boot, 48, 1)
            struct.pack_into("<H", boot, 50, 6)   # BPB_BkBootSec
            boot[64] = 0x80
            boot[66] = 0x29
            struct.pack_into("<I", boot, 67, volume_id)
            boot[71:82] = label11
            boot[82:90] = b"FAT32   "
        else:
            boot[36] = 0x80
            boot[38] = 0x29
            struct.pack_into("<I", boot, 39, volume_id)
            boot[43:54] = label11
            boot[54:62] = f"FAT{fat_type}".ljust(8).encode("latin-1")
        struct.pack_into("<H", boot, 510, 0xAA55)

        if not quick:
            dev.zero_sectors(0, total)

        dev.write_sectors(0, bytes(boot))
        if fat_type == 32:
            # FSInfo (sektor 1) ve yedek onyukleme (sektor 6,7)
            fsinfo = bytearray(bps)
            fsinfo[0:4] = b"RRaA"
            fsinfo[484:488] = b"rrAa"
            data_clusters = layout["clusters"]
            struct.pack_into("<I", fsinfo, 488, data_clusters - 1)  # kok kumesi kullanildi
            struct.pack_into("<I", fsinfo, 492, 3)
            struct.pack_into("<H", fsinfo, 510, 0xAA55)
            dev.write_sectors(1, bytes(fsinfo))
            dev.write_sectors(6, bytes(boot))
            dev.write_sectors(7, bytes(fsinfo))
            # rezerve alanin kalani sifirlansin
            if reserved > 8:
                dev.zero_sectors(8, reserved - 8)
            if reserved > 2:
                dev.zero_sectors(2, 4)

        # FAT tablolarini olustur
        fat_bytes = bytearray(fat_size * bps)
        if fat_type == 32:
            struct.pack_into("<I", fat_bytes, 0, 0x0FFFFFF8)
            struct.pack_into("<I", fat_bytes, 4, 0x0FFFFFFF)
            struct.pack_into("<I", fat_bytes, 8, 0x0FFFFFFF)  # kok dizin kumesi
        elif fat_type == 16:
            struct.pack_into("<H", fat_bytes, 0, 0xFFF8)
            struct.pack_into("<H", fat_bytes, 2, 0xFFFF)
        else:
            fat_bytes[0:3] = b"\xF8\xFF\xFF"
        for i in range(num_fats):
            dev.write_sectors(reserved + i * fat_size, bytes(fat_bytes))

        # kok dizin alanini sifirla
        first_data = reserved + num_fats * fat_size + root_sectors
        if fat_type == 32:
            dev.zero_sectors(first_data, cluster_sectors)
        elif root_sectors:
            dev.zero_sectors(reserved + num_fats * fat_size, root_sectors)

        fs = FatFS(dev)
        if label:
            fs._write_volume_label(label)
        fs.flush()
        return fs

    def _write_volume_label(self, label: str) -> None:
        """Kok dizine birim etiketi girisi ekler."""
        name11 = label.upper().encode("latin-1", "replace")[:11].ljust(11, b" ")
        blob = self._build_entry_bytes(label, name11, ATTR_VOLUME_ID, 0, 0,
                                       need_lfn=False)
        self._insert_entry("/", blob)

    @staticmethod
    def _compute_layout(total: int, bps: int, fat_type: int,
                        spc: int, num_fats: int) -> Dict[str, int]:
        reserved = 32 if fat_type == 32 else 1
        root_entries = 0 if fat_type == 32 else 512
        root_sectors = (root_entries * 32 + bps - 1) // bps
        tmp1 = total - (reserved + root_sectors)
        tmp2 = (256 * spc) + num_fats
        if fat_type == 32:
            tmp2 //= 2
        fat_size = max(1, (tmp1 + tmp2 - 1) // tmp2)
        data_sectors = total - (reserved + num_fats * fat_size + root_sectors)
        clusters = max(0, data_sectors // spc)
        return {"reserved": reserved, "fat_size": fat_size,
                "root_entries": root_entries, "root_sectors": root_sectors,
                "clusters": clusters, "spc": spc}
