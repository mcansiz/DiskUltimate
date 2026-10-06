"""ext2 / ext3 / ext4 **salt okunur** okuyucu.

Bicimlendirme `ext.py` icindedir; bu modul yalnizca okur: dizin listeleme,
dosya icerigi ve temel oznitelikler. Yazma yoktur — bir ext birimini
degistirmek gunluk (journal) tutarliligi gerektirir ve kapsam disidir.

Desteklenen yerlesimler:
  * **extent agaci** (ext4 varsayilani, `EXT4_EXTENTS_FL`) — yaprak ve ic dugum
  * **dolayli blok** zinciri (ext2/ext3 ve extent'siz ext4) — tek/cift/uc kat
  * 64 bit blok numaralari (`INCOMPAT_64BIT`, 64 baytlik grup tanimlayicisi)
  * hizli sembolik bag (hedef `i_block` icinde) ve blok tabanli sembolik bag
  * **inline_data**: kucuk dosya/dizin icerigi inode icinde (`i_block` 60
    bayt + govdedeki "system.data" ek ozniteligi) — yalnizca okuma
  * yazilmamis (unwritten) extent ve dolayli blok delikleri sifir okunur

Saf Python, harici bagimlilik yok. Tum `struct` bicimleri acik endian tasir.
"""
from __future__ import annotations

import datetime
import struct
from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Tuple

from .image import BlockDevice
from .extlayout import ExtGeometry
from ..i18n import tr

EXT_MAGIC = 0xEF53
SUPERBLOCK_OFFSET = 1024

INCOMPAT_FILETYPE = 0x0002
INCOMPAT_EXTENTS = 0x0040
INCOMPAT_64BIT = 0x0080

INO_ROOT = 2

S_IFMT = 0xF000
S_IFDIR = 0x4000
S_IFREG = 0x8000
S_IFLNK = 0xA000

EXTENT_MAGIC = 0xF30A
EXTENTS_FL = 0x00080000
INLINE_DATA_FL = 0x10000000
HUGE_FILE_FL = 0x00040000
RO_HUGE_FILE = 0x0008
# inline dizinde i_block'un ilk 4 bayti ust dizinin inode numarasidir
INLINE_DIR_PARENT = 4
INLINE_SIZE = 60                     # i_block boyu (EXT4_MIN_INLINE_DATA_SIZE)
XATTR_MAGIC = 0xEA020000
XATTR_INDEX_SYSTEM = 7               # "system." oneki; inline veri adi "data"

# Dizin girisi tur baytlari (INCOMPAT_FILETYPE etkinken anlamli)
FT_DIR = 2
FT_SYMLINK = 7


class ExtError(Exception):
    """ext biriminin okunmasiyla ilgili hatalar."""


def _normalize(path: str) -> str:
    """`.` ve `..` parcalarini duzler (os.path kullanilmaz: ayirici sabit '/')."""
    out: List[str] = []
    for part in path.replace("\\", "/").split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            if out:
                out.pop()
            continue
        out.append(part)
    return "/" + "/".join(out)


@dataclass
class ExtInode:
    number: int
    mode: int
    size: int
    atime: int
    ctime: int
    mtime: int
    flags: int
    links: int
    uid: int
    gid: int
    raw_block: bytes          # i_block: 60 bayt
    file_acl: int = 0         # ek oznitelik (xattr) blogu; 0 = yok
    blocks512: int = 0        # i_blocks (512 baytlik birim)
    body: bytes = b""         # 128. bayttan sonrasi (govde ici xattr'lar)

    @property
    def is_dir(self) -> bool:
        return (self.mode & S_IFMT) == S_IFDIR

    @property
    def is_regular(self) -> bool:
        return (self.mode & S_IFMT) == S_IFREG

    @property
    def is_symlink(self) -> bool:
        return (self.mode & S_IFMT) == S_IFLNK

    @property
    def uses_extents(self) -> bool:
        return bool(self.flags & EXTENTS_FL)

    @property
    def is_inline(self) -> bool:
        return bool(self.flags & INLINE_DATA_FL)

    @property
    def is_fast_symlink(self) -> bool:
        """Hedef `i_block` icinde mi? (e2fsprogs `ext2fs_is_fast_symlink`).

        Sinir `< 60`'tir: 60 karakterlik hedef sonundaki NUL ile 61 bayt
        eder ve bloga yazilir. Eskiden `<= 60` idi; 60 karakterlik bagin
        blok numarasi metin diye okunuyordu.
        """
        return self.is_symlink and not self.is_inline and self.size < INLINE_SIZE

    @property
    def has_block_map(self) -> bool:
        """`i_block` blok isaretcisi / extent agaci mi tasiyor?

        Aygit, fifo ve soketin `i_block`u aygit numarasi tasir; hizli bag ve
        inline inode'unki veri. Bunlarin "bloklari" serbest birakilirsa
        baska dosyalarin bloklari bosa cikar (denetim E3).
        """
        kind = self.mode & S_IFMT
        if kind not in (S_IFREG, S_IFDIR, S_IFLNK):
            return False
        if self.is_inline or self.is_fast_symlink:
            return False
        return True


@dataclass
class ExtDirEntry:
    inode: int
    name: str
    file_type: int
    # Diskteki ham ad. `name` UTF-8 olmayan baytlarda "replace" ile cozulur
    # ve geri kodlanamaz; silme/yeniden adlandirma girisi bu baytlarla bulur.
    raw_name: bytes = b""


class ExtFS:
    """Bir ext2/3/4 birimini salt okunur acar."""

    def __init__(self, device: BlockDevice):
        self.dev = device
        self._read_superblock()
        self._inode_cache: Dict[int, ExtInode] = {}

    # ------------------------------------------------------------------
    # Ustblok ve grup tanimlayicilari
    # ------------------------------------------------------------------
    def _read_superblock(self) -> None:
        sb = self.dev.read(SUPERBLOCK_OFFSET, 1024)
        if len(sb) < 1024:
            raise ExtError(tr("Ustblok okunamadi"))
        if struct.unpack_from("<H", sb, 0x38)[0] != EXT_MAGIC:
            raise ExtError(tr("ext imzasi yok"))

        self.inodes_count = struct.unpack_from("<I", sb, 0x00)[0]
        blocks_lo = struct.unpack_from("<I", sb, 0x04)[0]
        log_block_size = struct.unpack_from("<I", sb, 0x18)[0]
        self.block_size = 1024 << log_block_size
        self.blocks_per_group = struct.unpack_from("<I", sb, 0x20)[0]
        self.inodes_per_group = struct.unpack_from("<I", sb, 0x28)[0]
        self.first_data_block = struct.unpack_from("<I", sb, 0x14)[0]
        self.free_blocks = struct.unpack_from("<I", sb, 0x0C)[0]

        rev = struct.unpack_from("<I", sb, 0x4C)[0]
        if rev >= 1:
            self.inode_size = struct.unpack_from("<H", sb, 0x58)[0] or 128
            self.first_ino = struct.unpack_from("<I", sb, 0x54)[0] or 11
            self.feature_incompat = struct.unpack_from("<I", sb, 0x60)[0]
            self.ro_compat = struct.unpack_from("<I", sb, 0x64)[0]
            self.compat = struct.unpack_from("<I", sb, 0x5C)[0]
        else:                                   # ext2 rev0: sabit degerler
            self.inode_size, self.first_ino, self.feature_incompat = 128, 11, 0
            self.ro_compat = self.compat = 0

        self.label = sb[0x78:0x88].rstrip(b"\x00").decode("utf-8", "ignore")

        # 64 bit ozelligi acikken grup tanimlayicisi 64 bayt olabilir.
        desc_size = struct.unpack_from("<H", sb, 0xFE)[0]
        self.desc_size = desc_size if (
            self.feature_incompat & INCOMPAT_64BIT and desc_size >= 64) else 32

        blocks_hi = struct.unpack_from("<I", sb, 0x150)[0] \
            if self.feature_incompat & INCOMPAT_64BIT else 0
        self.blocks_count = (blocks_hi << 32) | blocks_lo

        if self.block_size <= 0 or self.blocks_per_group <= 0:
            raise ExtError(tr("Ustblok degerleri tutarsiz"))
        self.group_count = max(
            1, (self.blocks_count - self.first_data_block
                + self.blocks_per_group - 1) // self.blocks_per_group)

        # Grup tanimlayicilari: klasik duzende ustblogun hemen ardinda,
        # `meta_bg` birimde (resize2fs ile buyutulmus birimler) meta
        # gruplarina dagitilmis. Yerlesim hesabi `extlayout` icinde tektir.
        self.geometry = ExtGeometry(sb)
        self.geometry.desc_size = self.desc_size
        self.gdt_block = self.geometry.primary_gdt_block(0)
        parts = []
        bs = self.block_size
        for index in range(self.geometry.desc_blocks):
            parts.append(self.dev.read(
                self.geometry.primary_gdt_block(index) * bs, bs))
        self._gdt = b"".join(parts)[:self.group_count * self.desc_size]

    def descriptor_offset(self, group: int) -> int:
        """Grup tanimlayicisinin birincil kopyasinin bayt ofseti."""
        return self.geometry.descriptor_offset(group)

    def _inode_table_block(self, group: int) -> int:
        off = group * self.desc_size
        if off + 12 > len(self._gdt):
            raise ExtError(tr("Grup tanimlayicisi yok: {}", group))
        lo = struct.unpack_from("<I", self._gdt, off + 0x08)[0]
        hi = struct.unpack_from("<I", self._gdt, off + 0x28)[0] \
            if self.desc_size >= 64 else 0
        return (hi << 32) | lo

    # ------------------------------------------------------------------
    # Inode
    # ------------------------------------------------------------------
    def read_inode(self, number: int) -> ExtInode:
        hit = self._inode_cache.get(number)
        if hit is not None:
            return hit
        if number < 1 or number > self.inodes_count:
            raise ExtError(tr("Gecersiz inode numarasi: {}", number))
        group, index = divmod(number - 1, self.inodes_per_group)
        offset = (self._inode_table_block(group) * self.block_size
                  + index * self.inode_size)
        raw = self.dev.read(offset, max(128, self.inode_size))
        if len(raw) < 128:
            raise ExtError(tr("Inode okunamadi: {}", number))
        mode, uid, size_lo, atime, ctime, mtime = struct.unpack_from("<HHIIII", raw, 0)
        gid, links = struct.unpack_from("<HH", raw, 0x18)
        flags = struct.unpack_from("<I", raw, 0x20)[0]
        size_hi = struct.unpack_from("<I", raw, 0x6C)[0]
        size = size_lo | (size_hi << 32) if (mode & S_IFMT) == S_IFREG else size_lo
        acl = struct.unpack_from("<I", raw, 0x68)[0] | (
            struct.unpack_from("<H", raw, 0x76)[0] << 32
            if self.feature_incompat & INCOMPAT_64BIT else 0)
        blocks512 = struct.unpack_from("<I", raw, 0x1C)[0]
        if self.ro_compat & RO_HUGE_FILE:
            blocks512 |= struct.unpack_from("<H", raw, 0x74)[0] << 32
            if flags & HUGE_FILE_FL:
                blocks512 *= self.block_size // 512
        node = ExtInode(number=number, mode=mode, size=size, atime=atime,
                        ctime=ctime, mtime=mtime, flags=flags, links=links,
                        uid=uid, gid=gid, raw_block=bytes(raw[0x28:0x28 + 60]),
                        file_acl=acl, blocks512=blocks512,
                        body=bytes(raw[128:self.inode_size]))
        self._inode_cache[number] = node
        return node

    # ------------------------------------------------------------------
    # Blok haritalama
    # ------------------------------------------------------------------
    def _extent_blocks(self, node: ExtInode) -> List[Tuple[int, int, int]]:
        """Extent agacindan (mantiksal, fiziksel, uzunluk) uclulerini toplar.

        Yazilmamis (unwritten) araliklar da dahildir: bloklar ayrilmistir
        (dizin/saglama/serbest birakma icin dogru). Veri okurken
        `_extent_map` kullanilir; orada bu araliklar sifirdir.
        """
        return [(lg, ph, ln) for lg, ph, ln, _u in self._extent_map(node)]

    def _extent_map(self, node: ExtInode) -> List[Tuple[int, int, int, bool]]:
        """(mantiksal, fiziksel, uzunluk, yazilmamis) dortluleri."""
        out: List[Tuple[int, int, int, bool]] = []

        def walk(buf: bytes) -> None:
            if len(buf) < 12 or struct.unpack_from("<H", buf, 0)[0] != EXTENT_MAGIC:
                return
            entries, _max, depth = struct.unpack_from("<HHH", buf, 2)
            for i in range(entries):
                off = 12 + i * 12
                if off + 12 > len(buf):
                    break
                if depth == 0:                       # yaprak: ext4_extent
                    logical, length, start_hi, start_lo = struct.unpack_from(
                        "<IHHI", buf, off)
                    unwritten = length > 32768       # fallocate: yazilmamis
                    if unwritten:
                        # Cekirdek bu araligi SIFIR dondurur; diskte eski
                        # icerik durur (denetim E12).
                        length -= 32768
                    out.append((logical, (start_hi << 32) | start_lo, length,
                                unwritten))
                else:                                # ic dugum: ext4_extent_idx
                    logical, leaf_lo, leaf_hi = struct.unpack_from("<IIH", buf, off)
                    block = (leaf_hi << 32) | leaf_lo
                    walk(self._read_block(block))

        walk(node.raw_block)
        return out

    def _indirect_blocks(self, node: ExtInode, needed: int) -> List[int]:
        """ext2/ext3 dolayli blok zincirini duz bir listeye acar.

        Sifir isaretci (seyrek dosya) kapsadigi blok sayisi kadar **sifir**
        uretir. Eskiden bos liste ekleniyordu; sonraki veri one kayiyordu
        (denetim E11: ext3 seyrek dosyada dogrulandi).
        """
        per = self.block_size // 4
        out = list(struct.unpack_from("<12I", node.raw_block, 0))[:needed]

        def expand(block: int, level: int) -> None:
            # level 1: veri isaretcileri tablosu; 2/3: tablo tablolari
            span = per ** level
            if not block:
                out.extend([0] * min(span, needed - len(out)))
                return
            raw = self._read_block(block)
            ptrs = struct.unpack_from(f"<{per}I", raw, 0)
            if level == 1:
                out.extend(ptrs[:needed - len(out)])
                return
            for ptr in ptrs:
                if len(out) >= needed:
                    return
                expand(ptr, level - 1)

        for i, level in ((12, 1), (13, 2), (14, 3)):
            if len(out) >= needed:
                break
            expand(struct.unpack_from("<I", node.raw_block, i * 4)[0], level)
        return out[:needed]

    def _read_block(self, block: int) -> bytes:
        """Tek blok okur. Sinir disi numara sifir doner.

        Bozuk ya da yanlis yorumlanmis bir blok numarasi yuzunden okuma
        `BlockDevice` sinirini asip istisna firlatmamalidir; okuyucu bozuk
        birimde de cokmeden calismalidir.
        """
        if block <= 0 or block >= self.blocks_count:
            return b"\x00" * self.block_size
        return self.dev.read(block * self.block_size, self.block_size)

    def iter_data(self, node: ExtInode, chunk: int = 4 * 1024 * 1024):
        """Inode verisini parca parca verir (ADR 0081).

        `read_data` her blok icin sozluk girdisi kurar (5 GiB'ta ~1.3 milyon);
        bu yol extent'leri dogrudan dolasir. Bosluklar (seyrek) sifirdir.
        """
        if node.is_symlink:
            raise ExtError(tr("Sembolik bagin icerigi okunamaz"))
        size = node.size
        if size <= 0:
            return
        if node.is_inline:
            yield self.inline_data(node)[:size]
            return
        bs = self.block_size
        total = (size + bs - 1) // bs
        runs: List[Tuple[int, int]] = []          # (fiziksel ya da 0=bosluk, blok)
        if node.uses_extents:
            pos = 0
            for logical, physical, length, unwritten in sorted(
                    self._extent_map(node)):
                if logical >= total or logical + length <= pos:
                    continue
                if logical > pos:
                    runs.append((0, logical - pos))
                skip = max(0, pos - logical)
                n = min(length - skip, total - max(logical, pos))
                runs.append((0 if unwritten else physical + skip, n))
                pos = max(logical, pos) + n
            if pos < total:
                runs.append((0, total - pos))
        else:
            for block in self._indirect_blocks(node, total):
                if runs and ((block == 0 and runs[-1][0] == 0) or
                             (block and runs[-1][0] and
                              runs[-1][0] + runs[-1][1] == block)):
                    runs[-1] = (runs[-1][0], runs[-1][1] + 1)
                else:
                    runs.append((block, 1))
        per = max(1, chunk // bs)
        remaining = size
        for physical, count in runs:
            for i in range(0, count, per):
                n = min(per, count - i)
                data = (bytes(n * bs) if not physical else
                        self.dev.read((physical + i) * bs, n * bs))
                data = data[:remaining]
                remaining -= len(data)
                yield data
                if remaining <= 0:
                    return

    def read_data(self, node: ExtInode, max_bytes: int = -1) -> bytes:
        """Inode'un veri icerigini dondurur.

        Sembolik bag **icerik degildir**: hedef metni `i_block` icinde durur ve
        blok numarasi gibi yorumlanirsa cop adresler uretir. Bu yuzden acikca
        reddedilir; hedef icin `symlink_target()` kullanilir.
        """
        if node.is_symlink:
            raise ExtError(
                tr("Sembolik bagin icerigi okunamaz; hedefi icin symlink_target() "
                "kullanin veya resolve(..., follow=True) ile izleyin"))
        return self._read_content(node, max_bytes)

    def _read_content(self, node: ExtInode, max_bytes: int = -1) -> bytes:
        """Inode icerigi (tur denetimi yok; dizin ve yavas bag icin de)."""
        size = node.size if max_bytes < 0 else min(node.size, max_bytes)
        if size <= 0:
            return b""
        if node.is_inline:
            return self.inline_data(node)[:size]
        bs = self.block_size
        out = bytearray()

        if node.uses_extents:
            # Extent'ler mantiksal blok sirasina gore yerlestirilir; bosluklar
            # (seyrek dosya) ve yazilmamis araliklar sifirla doldurulur.
            parts: Dict[int, int] = {}
            for logical, physical, length, unwritten in self._extent_map(node):
                if unwritten:
                    continue
                for k in range(length):
                    parts[logical + k] = physical + k
            block_total = (size + bs - 1) // bs
            for i in range(block_total):
                physical = parts.get(i)
                out += self._read_block(physical) if physical else b"\x00" * bs
                if len(out) >= size:
                    break
        else:
            needed = (size + bs - 1) // bs
            for block in self._indirect_blocks(node, needed):
                out += self._read_block(block) if block else b"\x00" * bs
                if len(out) >= size:
                    break
        return bytes(out[:size])

    # ------------------------------------------------------------------
    # Dizinler
    # ------------------------------------------------------------------
    def read_dir(self, node: ExtInode) -> List[ExtDirEntry]:
        if not node.is_dir:
            raise ExtError(tr("Dizin degil"))
        if node.is_inline:
            return self._read_inline_dir(node)
        data = self._read_content(node)
        out: List[ExtDirEntry] = []
        bs = self.block_size
        # Her blok kendi icinde ayristirilir: dizin deligi (sifir blok) ya da
        # bozuk bir kayit yalnizca o blogu atlatir, dizinin geri kalanini degil.
        for base in range(0, len(data), bs):
            self._parse_entries(data[base:base + bs], out)
        return out

    def _parse_entries(self, data: bytes, out: List[ExtDirEntry]) -> None:
        pos = 0
        while pos + 8 <= len(data):
            ino, rec_len, name_len, ftype = struct.unpack_from("<IHBB", data, pos)
            if rec_len < 8 or pos + rec_len > len(data):
                break                       # bozuk giris: bu blokta ileri gitme
            if ino and name_len:
                raw_name = bytes(data[pos + 8:pos + 8 + name_len])
                out.append(ExtDirEntry(inode=ino,
                                       name=raw_name.decode("utf-8", "replace"),
                                       file_type=ftype, raw_name=raw_name))
            pos += rec_len

    # ------------------------------------------------------------------
    # inline_data (yalnizca okuma)
    # ------------------------------------------------------------------
    def _inline_xattr_value(self, node: ExtInode) -> bytes:
        """Govdedeki "system.data" ek ozniteliginin degeri (yoksa bos).

        Govde ici xattr alani `128 + i_extra_isize`ten baslar: 4 baytlik
        sihirli sayi, ardindan girisler; deger ofseti ilk girise goredir.
        """
        body = node.body
        if len(body) < 4:
            return b""
        extra = struct.unpack_from("<H", body, 0)[0]
        start = extra
        if start + 4 > len(body) or \
                struct.unpack_from("<I", body, start)[0] != XATTR_MAGIC:
            return b""
        first = start + 4
        pos = first
        while pos + 16 <= len(body):
            name_len, index, value_offs, value_inum, value_size = \
                struct.unpack_from("<BBHII", body, pos)
            if name_len == 0 and index == 0 and value_offs == 0 and \
                    value_inum == 0:
                break                                  # son giris
            name = body[pos + 16:pos + 16 + name_len]
            if index == XATTR_INDEX_SYSTEM and name == b"data" and not value_inum:
                begin = first + value_offs
                return bytes(body[begin:begin + value_size])
            pos += (16 + name_len + 3) & ~3
        return b""

    def inline_data(self, node: ExtInode) -> bytes:
        """inline inode'un ham icerigi: `i_block` (60 bayt) + xattr devami."""
        return bytes(node.raw_block[:INLINE_SIZE]) + self._inline_xattr_value(node)

    def _read_inline_dir(self, node: ExtInode) -> List[ExtDirEntry]:
        """inline dizin: ilk 4 bayt ust dizin, sonra girisler (i_block'ta 56
        bayt, devami xattr degerinde; her bolum kendi icinde ayristirilir).
        "." ve ".." diskte yoktur, tutarlilik icin uretilir."""
        parent = struct.unpack_from("<I", node.raw_block, 0)[0]
        out = [ExtDirEntry(node.number, ".", FT_DIR, b"."),
               ExtDirEntry(parent, "..", FT_DIR, b"..")]
        self._parse_entries(node.raw_block[INLINE_DIR_PARENT:INLINE_SIZE], out)
        self._parse_entries(self._inline_xattr_value(node), out)
        return out

    def symlink_target(self, node: ExtInode) -> str:
        """Sembolik bagin hedefini dondurur (hizli bag `i_block` icindedir)."""
        if node.is_fast_symlink:
            return node.raw_block[:node.size].decode("utf-8", "replace")
        return self._read_content(node).decode("utf-8", "replace")

    # ------------------------------------------------------------------
    # Yol cozumleme
    # ------------------------------------------------------------------
    def resolve(self, path: str, follow: bool = True,
                _depth: int = 0) -> ExtInode:
        """Yolu inode'a cozer.

        `follow` ise ara ve son sembolik baglar izlenir. Bag hedefi goreli
        olabilir (ornegin iki ust dizine giden bir hedef), bu yuzden o ana
        kadarki dizine gore cozulur. Donguye karsi derinlik sinirlidir.
        """
        if _depth > 16:
            raise ExtError(tr("Sembolik bag dongusu: {}", path))
        node = self.read_inode(INO_ROOT)
        parts = [p for p in path.replace("\\", "/").split("/") if p]
        for i, part in enumerate(parts):
            if not node.is_dir:
                raise ExtError(tr("Dizin degil: {}", part))
            for entry in self.read_dir(node):
                if entry.name == part:
                    node = self.read_inode(entry.inode)
                    break
            else:
                raise ExtError(tr("Bulunamadi: {}", path))
            if node.is_symlink and (follow or i < len(parts) - 1):
                target = self.symlink_target(node)
                if target.startswith("/"):
                    resolved = target
                else:
                    prefix = "/".join(parts[:i])
                    resolved = f"/{prefix}/{target}" if prefix else f"/{target}"
                rest = parts[i + 1:]
                if rest:
                    resolved = resolved.rstrip("/") + "/" + "/".join(rest)
                return self.resolve(_normalize(resolved), follow=follow,
                                    _depth=_depth + 1)
        return node

    # ------------------------------------------------------------------
    def stats(self) -> Dict[str, int]:
        total = self.blocks_count * self.block_size
        free = self.free_blocks * self.block_size
        return {"total_bytes": total, "used_bytes": max(0, total - free),
                "free_bytes": free, "cluster_size": self.block_size}

    @staticmethod
    def timestamp(value: int) -> Optional[datetime.datetime]:
        try:
            return datetime.datetime.fromtimestamp(value) if value else None
        except (OverflowError, OSError, ValueError):
            return None


def detect_ext(device: BlockDevice) -> bool:
    """Aygitta bir ext ustblogu var mi?"""
    try:
        sb = device.read(SUPERBLOCK_OFFSET, 64)
        return len(sb) >= 58 and struct.unpack_from("<H", sb, 0x38)[0] == EXT_MAGIC
    except Exception:
        return False
