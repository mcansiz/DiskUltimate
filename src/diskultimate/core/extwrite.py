"""ext2 / ext3 / ext4 **yazma** destegi (1. asama).

Okuma `extread.py` icindedir; bu modul onun uzerine yazmayi ekler:
dosya olusturma, klasor olusturma, silme ve yeniden adlandirma.

## Kapsam ve neden sinirli

Bir ext birimine yazmak, veriyi yazmaktan ibaret degildir: blok ve inode
bitmap'leri, grup tanimlayici sayaclari ve ustblok sayaclari **ayni anda**
tutarli kalmalidir. Birim ayrica su ozellikleri tasiyorsa yazma daha da
genisler ve bu surumde **reddedilir**:

  * `bigalloc`, `inline_data` — farkli tahsis/yerlesim kurallari.
  * `64bit` **yalnizca** birim 4 milyar bloktan buyukse (blok numarasi 32 biti
    asarsa). Kucuk birimlerde 64bit sorun degildir.

Desteklenenler:
  * `metadata_csum` — ustblok, grup tanimlayici, inode, bitmap ve dizin blogu
    saglamalari (CRC-32C) yazma sirasinda guncellenir (`extcsum.py`).
  * **extent kullanan dizinler**: var olan bloklara giris eklenebilir/silinebilir
    (agac degismez). Dizine yeni blok gerekirse acikca reddedilir.
  * **extent kullanan dosyalar**: silinebilir (bloklari birakilir). Yeni
    olusturulan dosyalar dolayli blok duzeni kullanir.

Baslatilmamis (`BLOCK_UNINIT`/`INODE_UNINIT`) gruplar tahsis icin atlanir:
bitmap'leri diskte tutulmaz, gerceklemek ayri bir istir.

Reddetme sessiz degildir: `write_support()` nedeni metinle dondurur ve arayuz
bunu kullaniciya gosterir. **Yanlis yazip bozmaktansa yazmamak yeglenir.**

Dogrulama: uretilen her birim `e2fsck -nf` ile denetlenir
(`tests/ext_write_check.py`).
"""
from __future__ import annotations

import struct
import time
from typing import Dict, List, Optional, Tuple

from .extcsum import (DIRENT_TAIL_FT, DIRENT_TAIL_SIZE, GD_CHECKSUM_OFFSET,
                      INODE_CSUM_HI, INODE_CSUM_LO, SB_CHECKSUM_OFFSET,
                      ExtChecksums)
from .extread import (EXTENTS_FL, INO_ROOT, S_IFDIR, S_IFMT, S_IFREG,
                      ExtError, ExtFS, ExtInode, _normalize)

# Desteklenmeyen ozellikler
INCOMPAT_64BIT = 0x0080
INCOMPAT_INLINE_DATA = 0x8000
RO_METADATA_CSUM = 0x0400
RO_BIGALLOC = 0x0100

FT_REG = 1
FT_DIR = 2

# Dizin girisi basligi: inode(4) + rec_len(2) + name_len(1) + file_type(1)
DIRENT_HEAD = 8


def _align4(n: int) -> int:
    return (n + 3) & ~3


class ExtWriter:
    """Bir `ExtFS` uzerine yazma islemleri ekler."""

    def __init__(self, fs: ExtFS):
        self.fs = fs
        self.dev = fs.dev
        self.bs = fs.block_size
        self.csum = ExtChecksums(fs)

    # ------------------------------------------------------------------
    # Destek denetimi
    # ------------------------------------------------------------------
    def write_support(self) -> Tuple[bool, str]:
        """(destekleniyor mu, neden) — neden yalnizca desteklenmiyorsa dolu."""
        fs = self.fs
        if getattr(self.dev, "readonly", False):
            return False, "Kaynak salt okunur acildi."
        missing = []
        if fs.ro_compat & RO_BIGALLOC:
            missing.append("bigalloc")
        if fs.feature_incompat & INCOMPAT_INLINE_DATA:
            missing.append("inline_data")
        if fs.feature_incompat & INCOMPAT_64BIT and fs.blocks_count > 0xFFFFFFFF:
            # `64bit` ozelligi tek basina engel degil: sorun ancak blok numarasi
            # 32 biti asarsa cikar. Saglamalar ve 64 baytlik grup tanimlayicisi
            # desteklenir.
            missing.append("64bit (4 milyar bloktan buyuk birim)")
        if missing:
            return False, (
                "Bu birim su ozellikleri kullaniyor ve bu surumde yazma "
                "desteklenmiyor: " + ", ".join(missing)
                + ". Yanlis yazip birimi bozmamak icin islem reddedildi.")
        return True, ""

    def _require_writable(self) -> None:
        ok, reason = self.write_support()
        if not ok:
            raise ExtError(reason)

    # ------------------------------------------------------------------
    # Ham blok yardimcilari
    # ------------------------------------------------------------------
    def _read_block(self, block: int) -> bytearray:
        return bytearray(self.dev.read(block * self.bs, self.bs))

    def _write_block(self, block: int, data: bytes) -> None:
        if len(data) != self.bs:
            raise ExtError("Blok boyutu uyusmuyor")
        if block <= 0 or block >= self.fs.blocks_count:
            raise ExtError(f"Sinir disi blok yazimi: {block}")
        self.dev.write(block * self.bs, bytes(data))

    # ------------------------------------------------------------------
    # Grup tanimlayicisi ve ustblok sayaclari
    # ------------------------------------------------------------------
    def _gd_offset(self, group: int) -> int:
        return self.fs.gdt_block * self.bs + group * self.fs.desc_size

    def _gd_get(self, group: int, field: int) -> int:
        raw = self.dev.read(self._gd_offset(group) + field, 2)
        return struct.unpack("<H", raw)[0]

    def _gd_set(self, group: int, field: int, value: int) -> None:
        self.dev.write(self._gd_offset(group) + field,
                       struct.pack("<H", value & 0xFFFF))

    def _sb_get(self, field: int) -> int:
        return struct.unpack("<I", self.dev.read(1024 + field, 4))[0]

    def _sb_set(self, field: int, value: int) -> None:
        self.dev.write(1024 + field, struct.pack("<I", value & 0xFFFFFFFF))

    # --- saglamalar (metadata_csum) ------------------------------------
    BG_FLAGS = 0x12
    BG_BLOCK_UNINIT = 0x0002
    BG_INODE_UNINIT = 0x0001

    def _refresh_group_csum(self, group: int) -> None:
        """Grup tanimlayicisinin saglamasini yeniden hesaplar."""
        if not self.csum.enabled:
            return
        off = self._gd_offset(group)
        desc = self.dev.read(off, self.fs.desc_size)
        value = self.csum.group_desc(group, desc)
        self.dev.write(off + GD_CHECKSUM_OFFSET, struct.pack("<H", value))

    def _refresh_super_csum(self) -> None:
        """Ustblok saglamasini yeniden hesaplar."""
        if not self.csum.enabled:
            return
        sb = self.dev.read(1024, 1024)
        value = self.csum.superblock(sb)
        self.dev.write(1024 + SB_CHECKSUM_OFFSET, struct.pack("<I", value))

    def _refresh_bitmap_csum(self, group: int, inode_map: bool) -> None:
        """Bitmap saglamasini grup tanimlayicisina yazar."""
        if not self.csum.enabled:
            return
        fs = self.fs
        size = ((fs.inodes_per_group + 7) // 8) if inode_map             else (fs.blocks_per_group // 8)
        block = self._bitmap_block(group, inode_map)
        value = self.csum.bitmap(self.dev.read(block * self.bs, size))
        off = self._gd_offset(group) + (0x1A if inode_map else 0x18)
        self.dev.write(off, struct.pack("<H", value & 0xFFFF))
        if fs.desc_size >= 64:
            hi = self._gd_offset(group) + (0x3A if inode_map else 0x38)
            self.dev.write(hi, struct.pack("<H", (value >> 16) & 0xFFFF))

    def _group_usable(self, group: int, inode_map: bool) -> bool:
        """Baslatilmamis gruplar tahsis icin kullanilmaz.

        `BLOCK_UNINIT`/`INODE_UNINIT` gruplarda bitmap diskte tutulmaz; cekirdek
        onu uretir. Boyle bir gruba yazmak icin once bitmap'i gerceklemek
        gerekir. Bu surumde daha guvenli olan yol secildi: **baslatilmis
        gruplar kullanilir**, digerleri atlanir.
        """
        if not self.csum.enabled:
            return True
        flags = struct.unpack("<H", self.dev.read(
            self._gd_offset(group) + self.BG_FLAGS, 2))[0]
        return not (flags & (self.BG_INODE_UNINIT if inode_map
                             else self.BG_BLOCK_UNINIT))

    def _bump_free_blocks(self, group: int, delta: int) -> None:
        self._gd_set(group, 0x0C, self._gd_get(group, 0x0C) + delta)
        self._sb_set(0x0C, self._sb_get(0x0C) + delta)
        self.fs.free_blocks += delta
        self._refresh_group_csum(group)
        self._refresh_super_csum()

    def _bump_free_inodes(self, group: int, delta: int) -> None:
        self._gd_set(group, 0x0E, self._gd_get(group, 0x0E) + delta)
        self._sb_set(0x10, self._sb_get(0x10) + delta)
        self._refresh_group_csum(group)
        self._refresh_super_csum()

    def _bump_used_dirs(self, group: int, delta: int) -> None:
        self._gd_set(group, 0x10, self._gd_get(group, 0x10) + delta)
        self._refresh_group_csum(group)

    # ------------------------------------------------------------------
    # Bitmap tahsisi
    # ------------------------------------------------------------------
    def _bitmap_block(self, group: int, inode_map: bool) -> int:
        """Bitmap blogunun numarasi (64bit birimlerde yuksek yari dahil)."""
        base = self._gd_offset(group)
        lo = struct.unpack("<I", self.dev.read(
            base + (0x04 if inode_map else 0x00), 4))[0]
        hi = 0
        if self.fs.desc_size >= 64:
            hi = struct.unpack("<I", self.dev.read(
                base + (0x24 if inode_map else 0x20), 4))[0]
        return (hi << 32) | lo

    @staticmethod
    def _bit_get(buf: bytearray, index: int) -> bool:
        return bool(buf[index >> 3] & (1 << (index & 7)))

    @staticmethod
    def _bit_set(buf: bytearray, index: int, value: bool) -> None:
        if value:
            buf[index >> 3] |= (1 << (index & 7))
        else:
            buf[index >> 3] &= ~(1 << (index & 7)) & 0xFF

    def alloc_blocks(self, count: int) -> List[int]:
        """`count` adet blok **tek taramada** tahsis eder.

        `alloc_block()` her cagrida bitmap'i okuyup yaziyordu; 100 MB'lik bir
        dosya icin bu 25 binden fazla okuma-yazma demekti ve kabul edilemez
        yavasti. Burada her grubun bitmap'i **bir kez** okunur, gereken bitler
        toplu isaretlenir ve **bir kez** yazilir.
        """
        if count <= 0:
            return []
        fs = self.fs
        out: List[int] = []
        for group in range(fs.group_count):
            if len(out) >= count:
                break
            free_in_group = self._gd_get(group, 0x0C)
            if free_in_group == 0 or not self._group_usable(group, inode_map=False):
                continue
            bmp_block = self._bitmap_block(group, inode_map=False)
            bmp = self._read_block(bmp_block)
            first = fs.first_data_block + group * fs.blocks_per_group
            last = min(first + fs.blocks_per_group, fs.blocks_count)
            taken = 0
            for i in range(last - first):
                if len(out) >= count:
                    break
                if not self._bit_get(bmp, i):
                    self._bit_set(bmp, i, True)
                    out.append(first + i)
                    taken += 1
            if taken:
                self._write_block(bmp_block, bmp)
                self._refresh_bitmap_csum(group, inode_map=False)
                self._bump_free_blocks(group, -taken)
        if len(out) < count:
            # Kismi tahsis birakilmaz: alinanlar geri verilir.
            self.free_blocks(out)
            raise ExtError(f"Bos blok yetersiz: {count} istendi, "
                           f"{len(out)} bulundu")
        return out

    def free_blocks(self, blocks: List[int]) -> None:
        """Blok listesini **grup basina tek yazimla** serbest birakir."""
        if not blocks:
            return
        fs = self.fs
        by_group: Dict[int, List[int]] = {}
        for b in blocks:
            if b <= 0 or b >= fs.blocks_count:
                continue
            g = (b - fs.first_data_block) // fs.blocks_per_group
            by_group.setdefault(g, []).append(b)
        for group, items in by_group.items():
            bmp_block = self._bitmap_block(group, inode_map=False)
            bmp = self._read_block(bmp_block)
            released = 0
            for b in items:
                index = (b - fs.first_data_block) % fs.blocks_per_group
                if self._bit_get(bmp, index):
                    self._bit_set(bmp, index, False)
                    released += 1
            if released:
                self._write_block(bmp_block, bmp)
                self._refresh_bitmap_csum(group, inode_map=False)
                self._bump_free_blocks(group, +released)
    def alloc_block(self) -> int:
        """Bos bir veri blogu tahsis eder ve numarasini dondurur."""
        fs = self.fs
        for group in range(fs.group_count):
            if self._gd_get(group, 0x0C) == 0:
                continue
            if not self._group_usable(group, inode_map=False):
                continue
            bmp_block = self._bitmap_block(group, inode_map=False)
            bmp = self._read_block(bmp_block)
            first = fs.first_data_block + group * fs.blocks_per_group
            last = min(first + fs.blocks_per_group, fs.blocks_count)
            for i in range(last - first):
                if not self._bit_get(bmp, i):
                    self._bit_set(bmp, i, True)
                    self._write_block(bmp_block, bmp)
                    self._refresh_bitmap_csum(group, inode_map=False)
                    self._bump_free_blocks(group, -1)
                    return first + i
        raise ExtError("Bos blok kalmadi")

    def free_block(self, block: int) -> None:
        fs = self.fs
        group = (block - fs.first_data_block) // fs.blocks_per_group
        index = (block - fs.first_data_block) % fs.blocks_per_group
        bmp_block = self._bitmap_block(group, inode_map=False)
        bmp = self._read_block(bmp_block)
        if self._bit_get(bmp, index):
            self._bit_set(bmp, index, False)
            self._write_block(bmp_block, bmp)
            self._refresh_bitmap_csum(group, inode_map=False)
            self._bump_free_blocks(group, +1)

    def alloc_inode(self, is_dir: bool) -> int:
        fs = self.fs
        for group in range(fs.group_count):
            if self._gd_get(group, 0x0E) == 0:
                continue
            if not self._group_usable(group, inode_map=True):
                continue
            bmp_block = self._bitmap_block(group, inode_map=True)
            bmp = self._read_block(bmp_block)
            for i in range(fs.inodes_per_group):
                ino = group * fs.inodes_per_group + i + 1
                if ino < fs.first_ino and ino != INO_ROOT:
                    continue                      # ayrilmis inode'lar
                if not self._bit_get(bmp, i):
                    self._bit_set(bmp, i, True)
                    self._write_block(bmp_block, bmp)
                    self._refresh_bitmap_csum(group, inode_map=True)
                    # bg_itable_unused: bu gruptaki "hic kullanilmamis kuyruk"
                    # sayisidir. Kuyruga inode tahsis edince gecersizlesir;
                    # guvenli olan 0 yazmaktir (e2fsck bunu kabul eder).
                    if self.csum.enabled and self._gd_get(group, 0x1C):
                        self._gd_set(group, 0x1C, 0)
                        self._refresh_group_csum(group)
                    self._bump_free_inodes(group, -1)
                    if is_dir:
                        self._bump_used_dirs(group, +1)
                    return ino
        raise ExtError("Bos inode kalmadi")

    def free_inode(self, ino: int, was_dir: bool) -> None:
        fs = self.fs
        group, index = divmod(ino - 1, fs.inodes_per_group)
        bmp_block = self._bitmap_block(group, inode_map=True)
        bmp = self._read_block(bmp_block)
        if self._bit_get(bmp, index):
            self._bit_set(bmp, index, False)
            self._write_block(bmp_block, bmp)
            self._refresh_bitmap_csum(group, inode_map=True)
            self._bump_free_inodes(group, +1)
            if was_dir:
                self._bump_used_dirs(group, -1)

    # ------------------------------------------------------------------
    # Inode yazimi
    # ------------------------------------------------------------------
    def _inode_offset(self, ino: int) -> int:
        fs = self.fs
        group, index = divmod(ino - 1, fs.inodes_per_group)
        return (fs._inode_table_block(group) * self.bs + index * fs.inode_size)

    def write_inode(self, ino: int, *, mode: int, size: int, links: int,
                    blocks: List[int], sectors: int,
                    flags: int = 0, uid: int = 0, gid: int = 0) -> None:
        """Inode kaydini yazar (dolayli blok duzeni; extent kullanilmaz)."""
        now = int(time.time())
        raw = bytearray(self.fs.inode_size)
        struct.pack_into("<HHIIIII", raw, 0, mode, uid, size & 0xFFFFFFFF,
                         now, now, now, 0)
        struct.pack_into("<HH", raw, 0x18, gid, links)
        # i_blocks 512 baytlik sektor cinsindendir (dolayli bloklar dahil)
        struct.pack_into("<I", raw, 0x1C, sectors)
        struct.pack_into("<I", raw, 0x20, flags)
        for i, b in enumerate(blocks[:15]):
            struct.pack_into("<I", raw, 0x28 + i * 4, b)
        if (mode & S_IFMT) == S_IFREG:
            struct.pack_into("<I", raw, 0x6C, (size >> 32) & 0xFFFFFFFF)
        if self.fs.inode_size > 128:
            struct.pack_into("<H", raw, 0x80, 32)      # i_extra_isize
        self._stamp_inode_csum(ino, raw)
        self.dev.write(self._inode_offset(ino), bytes(raw))
        self.fs._inode_cache.pop(ino, None)

    def _stamp_inode_csum(self, ino: int, raw: bytearray) -> None:
        """Inode kaydina saglamasini islemler (yerinde degistirir)."""
        if not self.csum.enabled:
            return
        struct.pack_into("<H", raw, INODE_CSUM_LO, 0)
        if len(raw) > INODE_CSUM_HI + 2:
            struct.pack_into("<H", raw, INODE_CSUM_HI, 0)
        lo, hi = self.csum.inode(ino, bytes(raw))
        struct.pack_into("<H", raw, INODE_CSUM_LO, lo)
        if hi >= 0:
            struct.pack_into("<H", raw, INODE_CSUM_HI, hi)

    def _patch_inode(self, ino: int, field: int, fmt: str, value) -> None:
        """Inode'un tek bir alanini degistirir ve saglamasini tazeler.

        Tek alan degisse bile saglama tum kayit uzerinden hesaplandigi icin
        yeniden damgalanmalidir; aksi halde `e2fsck` "inode checksum invalid"
        der.
        """
        off = self._inode_offset(ino)
        if not self.csum.enabled:
            self.dev.write(off + field, struct.pack(fmt, value))
        else:
            raw = bytearray(self.dev.read(off, self.fs.inode_size))
            struct.pack_into(fmt, raw, field, value)
            self._stamp_inode_csum(ino, raw)
            self.dev.write(off, bytes(raw))
        self.fs._inode_cache.pop(ino, None)

    # ------------------------------------------------------------------
    # Veri yerlesimi (dogrudan + tek kat dolayli)
    # ------------------------------------------------------------------
    def _per_table(self) -> int:
        """Bir dolayli blogun tasidigi isaretci sayisi."""
        return self.bs // 4

    def _max_bytes(self) -> int:
        """Dogrudan + tek/cift/uc kat dolayli ile eslenebilen azami boyut."""
        n = self._per_table()
        return (12 + n + n * n + n * n * n) * self.bs

    def _store_data(self, data: bytes) -> Tuple[List[int], int]:
        """Veriyi bloklara yazar; (i_block girdileri, 512'lik sektor sayisi).

        ext2/3 yerlesimi: 12 dogrudan blok, sonra tek / cift / uc kat dolayli.
        4 KB blokta tek kat ~4 MB'a, cift kat ~4 GB'a kadar gider. Dolayli
        tablolar da yer kaplar ve `i_blocks` sayacina dahildir.
        """
        bs = self.bs
        if len(data) > self._max_bytes():
            raise ExtError(
                f"Dosya cok buyuk: en fazla {self._max_bytes() >> 30} GB")
        needed = (len(data) + bs - 1) // bs
        if needed == 0:
            return [0] * 15, 0

        # Tum veri bloklari tek seferde tahsis edilir: blok basina bitmap
        # okuyup yazmak buyuk dosyalarda kabul edilemez yavaslikta.
        data_blocks = self.alloc_blocks(needed)
        for i, blk in enumerate(data_blocks):
            chunk = data[i * bs:(i + 1) * bs]
            self._write_block(blk, chunk.ljust(bs, b"\x00"))

        i_block = [0] * 15
        i_block[:min(12, needed)] = data_blocks[:12]
        meta = 0
        rest = data_blocks[12:]
        n = self._per_table()

        def write_table(pointers: List[int]) -> int:
            """Isaretcileri bir dolayli bloga yazar, blok numarasini dondurur."""
            nonlocal meta
            blk = self.alloc_blocks(1)[0]
            meta += 1
            table = bytearray(bs)
            for j, p in enumerate(pointers[:n]):
                struct.pack_into("<I", table, j * 4, p)
            self._write_block(blk, table)
            return blk

        if rest:                                    # tek kat dolayli
            i_block[12] = write_table(rest[:n])
            rest = rest[n:]
        if rest:                                    # cift kat dolayli
            inner = [write_table(rest[i:i + n])
                     for i in range(0, min(len(rest), n * n), n)]
            i_block[13] = write_table(inner)
            rest = rest[n * n:]
        if rest:                                    # uc kat dolayli
            middle = []
            for i in range(0, len(rest), n * n):
                part = rest[i:i + n * n]
                inner = [write_table(part[j:j + n])
                         for j in range(0, len(part), n)]
                middle.append(write_table(inner))
            i_block[14] = write_table(middle)

        return i_block, (needed + meta) * (bs // 512)
    def _release_data(self, node: ExtInode) -> None:
        """Inode'un tuttugu tum bloklari serbest birakir.

        Dolayli tablolar da serbest birakilir: yalnizca veri bloklarini
        birakmak, tablolari sizdirip disk alanini kaybettirir.
        """
        if node.uses_extents:
            # Extent'li bir inode silinebilir: agaci **okuyup** bloklarini
            # birakmak yeterlidir, agaci degistirmek gerekmez.
            for _logical, physical, length in self.fs._extent_blocks(node):
                for b in range(physical, physical + length):
                    self.free_block(b)
            return

        n = self._per_table()
        free = []

        def read_table(block: int) -> List[int]:
            table = self._read_block(block)
            return [struct.unpack_from("<I", table, i * 4)[0] for i in range(n)]

        free.extend(b for b in struct.unpack_from("<12I", node.raw_block, 0) if b)
        ind, dind, tind = struct.unpack_from("<III", node.raw_block, 48)

        if ind:
            free.extend(b for b in read_table(ind) if b)
            free.append(ind)
        if dind:
            for mid in read_table(dind):
                if mid:
                    free.extend(b for b in read_table(mid) if b)
                    free.append(mid)
            free.append(dind)
        if tind:
            for outer in read_table(tind):
                if not outer:
                    continue
                for mid in read_table(outer):
                    if mid:
                        free.extend(b for b in read_table(mid) if b)
                        free.append(mid)
                free.append(outer)
            free.append(tind)

        self.free_blocks(free)

    # ------------------------------------------------------------------
    # Dizin girisleri
    # ------------------------------------------------------------------
    def _dir_blocks(self, node: ExtInode) -> List[int]:
        """Dizinin veri bloklari.

        Extent kullanan dizinler de **okunur**: var olan bloklara giris eklemek
        extent agacini degistirmez, yalnizca blogun icerigi yeniden yazilir.
        Dizine yeni blok eklemek ise agaci buyutur ve `_dir_append_block`
        icinde acikca reddedilir.
        """
        if node.uses_extents:
            out: List[int] = []
            for _logical, physical, length in self.fs._extent_blocks(node):
                out.extend(range(physical, physical + length))
            return out
        direct = [b for b in struct.unpack_from("<12I", node.raw_block, 0) if b]
        ind = struct.unpack_from("<I", node.raw_block, 48)[0]
        if ind:
            table = self._read_block(ind)
            for i in range(self.bs // 4):
                b = struct.unpack_from("<I", table, i * 4)[0]
                if b:
                    direct.append(b)
        return direct

    def _inode_generation(self, ino: int) -> int:
        raw = self.dev.read(self._inode_offset(ino), self.fs.inode_size)
        return struct.unpack_from("<I", raw, 0x64)[0]

    def _write_dir_block(self, dir_ino: int, block: int, buf: bytearray) -> None:
        """Dizin blogunu yazar; saglama kuyrugu varsa gunceller.

        `metadata_csum` acikken her dizin blogunun son 12 bayti sahte bir
        kayittir (`ext4_dir_entry_tail`) ve blogun CRC'sini tasir.
        """
        if self.csum.enabled and ExtChecksums.is_tail(buf, self.bs):
            gen = self._inode_generation(dir_ino)
            value = self.csum.dir_block(dir_ino, gen, bytes(buf))
            struct.pack_into("<I", buf, self.bs - 4, value)
        self._write_block(block, buf)

    def _dir_usable_size(self) -> int:
        """Dizin blogunda girislere ayrilabilir bayt sayisi."""
        return self.bs - (DIRENT_TAIL_SIZE if self.csum.enabled else 0)

    def _new_dir_block(self) -> Tuple[int, bytearray]:
        """Bos bir dizin blogu hazirlar (gerekiyorsa saglama kuyruguyla)."""
        block = self.alloc_block()
        buf = bytearray(self.bs)
        if self.csum.enabled:
            struct.pack_into("<IHBB", buf, self.bs - DIRENT_TAIL_SIZE,
                             0, DIRENT_TAIL_SIZE, 0, DIRENT_TAIL_FT)
        return block, buf

    def dir_add(self, parent: int, name: str, ino: int, ftype: int) -> None:
        """Dizine yeni bir giris ekler.

        ext dizin bloklari **bitisik kayitlar** zinciridir: her kaydin `rec_len`
        alani bir sonrakine kadar olan mesafedir ve son kayit blogun sonuna
        kadar uzanir. Yeni giris, mevcut bir kaydin kullanilmayan kuyruguna
        sigiyorsa oraya bolunur; sigmiyorsa dizine yeni bir blok eklenir.
        """
        raw_name = name.encode("utf-8")
        needed = _align4(DIRENT_HEAD + len(raw_name))
        if needed > self.bs:
            raise ExtError("Dosya adi cok uzun")
        node = self.fs.read_inode(parent)

        for blk in self._dir_blocks(node):
            buf = self._read_block(blk)
            pos = 0
            while pos + DIRENT_HEAD <= self.bs:
                e_ino, rec_len, name_len, _ft = struct.unpack_from("<IHBB", buf, pos)
                if rec_len < DIRENT_HEAD:
                    break
                used = _align4(DIRENT_HEAD + name_len) if e_ino else 0
                if rec_len - used >= needed:
                    # kaydi kis, kalan yere yeni girisi koy
                    if e_ino:
                        struct.pack_into("<H", buf, pos + 4, used)
                        new_pos = pos + used
                    else:
                        new_pos, used = pos, 0
                    new_len = rec_len - used
                    struct.pack_into("<IHBB", buf, new_pos, ino, new_len,
                                     len(raw_name), ftype)
                    buf[new_pos + DIRENT_HEAD:new_pos + DIRENT_HEAD + len(raw_name)] = raw_name
                    self._write_dir_block(parent, blk, buf)
                    return
                pos += rec_len

        # Hicbir blokta yer yok: dizine yeni blok ekle
        new_block, buf = self._new_dir_block()
        struct.pack_into("<IHBB", buf, 0, ino, self._dir_usable_size(),
                         len(raw_name), ftype)
        buf[DIRENT_HEAD:DIRENT_HEAD + len(raw_name)] = raw_name
        self._write_dir_block(parent, new_block, buf)
        self._dir_append_block(parent, node, new_block)

    def _dir_append_block(self, ino: int, node: ExtInode, block: int) -> None:
        """Dizin inode'una yeni bir veri blogu baglar."""
        if node.uses_extents:
            raise ExtError(
                "Bu dizin extent kullaniyor ve dolu; yeni blok eklemek extent "
                "agacini degistirmeyi gerektirir, bu surumde desteklenmiyor.")
        direct = list(struct.unpack_from("<12I", node.raw_block, 0))
        for i, b in enumerate(direct):
            if b == 0:
                self._patch_inode(ino, 0x28 + i * 4, "<I", block)
                new_size = node.size + self.bs
                self._patch_inode(ino, 0x04, "<I", new_size)
                old = struct.unpack_from("<I", node.raw_block, 0)  # dokunulmaz
                sectors = struct.unpack("<I", self.dev.read(
                    self._inode_offset(ino) + 0x1C, 4))[0]
                self._patch_inode(ino, 0x1C, "<I", sectors + self.bs // 512)
                return
        raise ExtError("Dizin bu surumde daha fazla buyutulemez "
                       "(yalnizca 12 dogrudan blok)")

    def dir_remove(self, parent: int, name: str) -> None:
        """Girisi siler: onceki kaydin `rec_len` degeri uzerine alinir."""
        node = self.fs.read_inode(parent)
        target = name.encode("utf-8")
        for blk in self._dir_blocks(node):
            buf = self._read_block(blk)
            pos, prev = 0, -1
            limit = self._dir_usable_size()
            while pos + DIRENT_HEAD <= limit:
                e_ino, rec_len, name_len, _ft = struct.unpack_from("<IHBB", buf, pos)
                if rec_len < DIRENT_HEAD:
                    break
                if e_ino and buf[pos + DIRENT_HEAD:pos + DIRENT_HEAD + name_len] == target:
                    if prev >= 0:
                        prev_len = struct.unpack_from("<H", buf, prev + 4)[0]
                        struct.pack_into("<H", buf, prev + 4, prev_len + rec_len)
                    else:
                        struct.pack_into("<I", buf, pos, 0)   # inode = 0
                    self._write_dir_block(parent, blk, buf)
                    return
                prev = pos
                pos += rec_len
        raise ExtError(f"Dizin girisi bulunamadi: {name}")

    # ------------------------------------------------------------------
    # Ust duzey islemler
    # ------------------------------------------------------------------
    @staticmethod
    def _split(path: str) -> Tuple[str, str]:
        norm = _normalize(path)
        if norm == "/":
            raise ExtError("Kok dizin uzerinde islem yapilamaz")
        i = norm.rfind("/")
        return (norm[:i] or "/"), norm[i + 1:]

    def write_file(self, path: str, data: bytes) -> None:
        """Dosya olusturur veya uzerine yazar."""
        self._require_writable()
        parent_path, name = self._split(path)
        parent = self.fs.resolve(parent_path)
        if not parent.is_dir:
            raise ExtError(f"Dizin degil: {parent_path}")

        existing = None
        for e in self.fs.read_dir(parent):
            if e.name == name:
                existing = e
                break
        if existing is not None:
            old = self.fs.read_inode(existing.inode)
            if old.is_dir:
                raise ExtError(f"Ayni adda klasor var: {name}")
            self._release_data(old)
            ino = existing.inode
        else:
            ino = self.alloc_inode(is_dir=False)

        i_block, sectors = self._store_data(data)
        self.write_inode(ino, mode=S_IFREG | 0o644, size=len(data), links=1,
                         blocks=i_block, sectors=sectors)
        if existing is None:
            self.dir_add(parent.number, name, ino, FT_REG)
        self.fs._inode_cache.clear()

    def mkdir(self, path: str) -> None:
        """Bos bir klasor olusturur (`.` ve `..` girisleriyle)."""
        self._require_writable()
        parent_path, name = self._split(path)
        parent = self.fs.resolve(parent_path)
        if not parent.is_dir:
            raise ExtError(f"Dizin degil: {parent_path}")
        if any(e.name == name for e in self.fs.read_dir(parent)):
            raise ExtError(f"Zaten var: {name}")

        ino = self.alloc_inode(is_dir=True)
        blk, buf = self._new_dir_block()
        # "." kaydi
        struct.pack_into("<IHBB", buf, 0, ino, 12, 1, FT_DIR)
        buf[8:9] = b"."
        # ".." kaydi kullanilabilir alanin sonuna kadar uzanir (saglama
        # kuyrugu varsa onun oncesine kadar)
        struct.pack_into("<IHBB", buf, 12, parent.number,
                         self._dir_usable_size() - 12, 2, FT_DIR)
        buf[20:22] = b".."
        # Inode ONCE yazilir: dizin blogu saglamasi i_generation'a baglidir.
        self.write_inode(ino, mode=S_IFDIR | 0o755, size=self.bs, links=2,
                         blocks=[blk] + [0] * 14, sectors=self.bs // 512)
        self._write_dir_block(ino, blk, buf)
        self.dir_add(parent.number, name, ino, FT_DIR)
        # ust dizinin baglanti sayisi ".." yuzunden bir artar
        self._patch_inode(parent.number, 0x1A, "<H", parent.links + 1)
        self.fs._inode_cache.clear()

    def remove(self, path: str) -> None:
        """Dosyayi veya **bos** klasoru siler."""
        self._require_writable()
        parent_path, name = self._split(path)
        parent = self.fs.resolve(parent_path)
        target = None
        for e in self.fs.read_dir(parent):
            if e.name == name:
                target = e
                break
        if target is None:
            raise ExtError(f"Bulunamadi: {path}")
        node = self.fs.read_inode(target.inode)

        if node.is_dir:
            remaining = [e.name for e in self.fs.read_dir(node)
                     if e.name not in (".", "..")]
            if remaining:
                raise ExtError(f"Klasor bos degil: {name} ({len(remaining)} giris)")

        # Hizli sembolik bagin hedefi `i_block` icindedir, blok tutmaz;
        # yalnizca blok tabanli baglar ve normal dosyalar serbest birakilir.
        if not (node.is_symlink and node.size <= 60):
            self._release_data(node)

        self.dir_remove(parent.number, name)
        self._patch_inode(target.inode, 0x1A, "<H", 0)          # links = 0
        self._patch_inode(target.inode, 0x14, "<I", int(time.time()))  # dtime
        self.free_inode(target.inode, was_dir=node.is_dir)
        if node.is_dir:
            self._patch_inode(parent.number, 0x1A, "<H", max(2, parent.links - 1))
        self.fs._inode_cache.clear()

    def rename(self, path: str, new_name: str) -> None:
        """Ayni dizin icinde yeniden adlandirir."""
        self._require_writable()
        parent_path, name = self._split(path)
        if "/" in new_name:
            raise ExtError("Yeni ad yol icermemeli")
        parent = self.fs.resolve(parent_path)
        target = None
        for e in self.fs.read_dir(parent):
            if e.name == new_name:
                raise ExtError(f"Zaten var: {new_name}")
            if e.name == name:
                target = e
        if target is None:
            raise ExtError(f"Bulunamadi: {path}")
        ftype = FT_DIR if self.fs.read_inode(target.inode).is_dir else FT_REG
        self.dir_remove(parent.number, name)
        self.dir_add(parent.number, new_name, target.inode, ftype)
        self.fs._inode_cache.clear()

    def flush(self) -> None:
        f = getattr(self.dev, "flush", None)
        if f:
            f()
