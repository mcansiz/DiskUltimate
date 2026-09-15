"""ext2 / ext3 / ext4 **yazma** destegi (1. asama).

Okuma `extread.py` icindedir; bu modul onun uzerine yazmayi ekler:
dosya olusturma, klasor olusturma, silme ve yeniden adlandirma.

## Kapsam ve neden sinirli

Bir ext birimine yazmak, veriyi yazmaktan ibaret degildir: blok ve inode
bitmap'leri, grup tanimlayici sayaclari ve ustblok sayaclari **ayni anda**
tutarli kalmalidir. Birim ayrica su ozellikleri tasiyorsa yazma daha da
genisler ve bu surumde **reddedilir**:

  * `metadata_csum` — her metaveri blogu CRC32c saglamasi tasir; yanlis saglama
    `e2fsck` tarafindan bozukluk sayilir.
  * `64bit` — 64 baytlik grup tanimlayicisi ve yuksek 32 bitlik alanlar.
  * `bigalloc`, `inline_data` — farkli tahsis/yerlesim kurallari.
  * inode **extent** kullaniyorsa (ext4 varsayilani) — extent agaci degisikligi
    ayri bir istir. Okuma extent'i destekler, yazma henuz desteklemez.

Reddetme sessiz degildir: `write_support()` nedeni metinle dondurur ve arayuz
bunu kullaniciya gosterir. **Yanlis yazip bozmaktansa yazmamak yeglenir.**

Dogrulama: uretilen her birim `e2fsck -nf` ile denetlenir
(`tests/ext_write_check.py`).
"""
from __future__ import annotations

import struct
import time
from typing import List, Optional, Tuple

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

    # ------------------------------------------------------------------
    # Destek denetimi
    # ------------------------------------------------------------------
    def write_support(self) -> Tuple[bool, str]:
        """(destekleniyor mu, neden) — neden yalnizca desteklenmiyorsa dolu."""
        fs = self.fs
        if getattr(self.dev, "readonly", False):
            return False, "Kaynak salt okunur acildi."
        missing = []
        if fs.ro_compat & RO_METADATA_CSUM:
            missing.append("metadata_csum (CRC32c saglamalar)")
        if fs.ro_compat & RO_BIGALLOC:
            missing.append("bigalloc")
        if fs.feature_incompat & INCOMPAT_64BIT:
            missing.append("64bit")
        if fs.feature_incompat & INCOMPAT_INLINE_DATA:
            missing.append("inline_data")
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

    def _bump_free_blocks(self, group: int, delta: int) -> None:
        self._gd_set(group, 0x0C, self._gd_get(group, 0x0C) + delta)
        self._sb_set(0x0C, self._sb_get(0x0C) + delta)
        self.fs.free_blocks += delta

    def _bump_free_inodes(self, group: int, delta: int) -> None:
        self._gd_set(group, 0x0E, self._gd_get(group, 0x0E) + delta)
        self._sb_set(0x10, self._sb_get(0x10) + delta)

    def _bump_used_dirs(self, group: int, delta: int) -> None:
        self._gd_set(group, 0x10, self._gd_get(group, 0x10) + delta)

    # ------------------------------------------------------------------
    # Bitmap tahsisi
    # ------------------------------------------------------------------
    def _bitmap_block(self, group: int, inode_map: bool) -> int:
        off = self._gd_offset(group) + (0x04 if inode_map else 0x00)
        return struct.unpack("<I", self.dev.read(off, 4))[0]

    @staticmethod
    def _bit_get(buf: bytearray, index: int) -> bool:
        return bool(buf[index >> 3] & (1 << (index & 7)))

    @staticmethod
    def _bit_set(buf: bytearray, index: int, value: bool) -> None:
        if value:
            buf[index >> 3] |= (1 << (index & 7))
        else:
            buf[index >> 3] &= ~(1 << (index & 7)) & 0xFF

    def alloc_block(self) -> int:
        """Bos bir veri blogu tahsis eder ve numarasini dondurur."""
        fs = self.fs
        for group in range(fs.group_count):
            if self._gd_get(group, 0x0C) == 0:
                continue
            bmp_block = self._bitmap_block(group, inode_map=False)
            bmp = self._read_block(bmp_block)
            first = fs.first_data_block + group * fs.blocks_per_group
            last = min(first + fs.blocks_per_group, fs.blocks_count)
            for i in range(last - first):
                if not self._bit_get(bmp, i):
                    self._bit_set(bmp, i, True)
                    self._write_block(bmp_block, bmp)
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
            self._bump_free_blocks(group, +1)

    def alloc_inode(self, is_dir: bool) -> int:
        fs = self.fs
        for group in range(fs.group_count):
            if self._gd_get(group, 0x0E) == 0:
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
        self.dev.write(self._inode_offset(ino), bytes(raw))
        self.fs._inode_cache.pop(ino, None)

    def _patch_inode(self, ino: int, field: int, fmt: str, value) -> None:
        self.dev.write(self._inode_offset(ino) + field, struct.pack(fmt, value))
        self.fs._inode_cache.pop(ino, None)

    # ------------------------------------------------------------------
    # Veri yerlesimi (dogrudan + tek kat dolayli)
    # ------------------------------------------------------------------
    def _max_bytes(self) -> int:
        return (12 + self.bs // 4) * self.bs

    def _store_data(self, data: bytes) -> Tuple[List[int], int]:
        """Veriyi bloklara yazar; (i_block girdileri, 512'lik sektor sayisi)."""
        bs = self.bs
        if len(data) > self._max_bytes():
            raise ExtError(
                f"Bu surumde en fazla {self._max_bytes() // 1024} KB'lik dosya "
                "yazilabilir (yalnizca dogrudan ve tek kat dolayli blok).")
        needed = (len(data) + bs - 1) // bs
        data_blocks = [self.alloc_block() for _ in range(needed)]
        for i, blk in enumerate(data_blocks):
            chunk = data[i * bs:(i + 1) * bs]
            self._write_block(blk, chunk.ljust(bs, b"\x00"))

        i_block = data_blocks[:12] + [0, 0, 0]
        block_total = needed
        if needed > 12:
            ind = self.alloc_block()
            block_total += 1
            table = bytearray(bs)
            for i, blk in enumerate(data_blocks[12:]):
                struct.pack_into("<I", table, i * 4, blk)
            self._write_block(ind, table)
            i_block = data_blocks[:12] + [ind, 0, 0]
        return i_block, block_total * (bs // 512)

    def _release_data(self, node: ExtInode) -> None:
        """Inode'un tuttugu tum bloklari serbest birakir."""
        if node.uses_extents:
            raise ExtError("Extent kullanan inode bu surumde silinemez")
        direct = list(struct.unpack_from("<12I", node.raw_block, 0))
        ind = struct.unpack_from("<I", node.raw_block, 48)[0]
        for b in direct:
            if b:
                self.free_block(b)
        if ind:
            table = self._read_block(ind)
            for i in range(self.bs // 4):
                b = struct.unpack_from("<I", table, i * 4)[0]
                if b:
                    self.free_block(b)
            self.free_block(ind)

    # ------------------------------------------------------------------
    # Dizin girisleri
    # ------------------------------------------------------------------
    def _dir_blocks(self, node: ExtInode) -> List[int]:
        if node.uses_extents:
            raise ExtError("Extent kullanan dizin bu surumde degistirilemez")
        direct = [b for b in struct.unpack_from("<12I", node.raw_block, 0) if b]
        ind = struct.unpack_from("<I", node.raw_block, 48)[0]
        if ind:
            table = self._read_block(ind)
            for i in range(self.bs // 4):
                b = struct.unpack_from("<I", table, i * 4)[0]
                if b:
                    direct.append(b)
        return direct

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
                    self._write_block(blk, buf)
                    return
                pos += rec_len

        # Hicbir blokta yer yok: dizine yeni blok ekle
        new_block = self.alloc_block()
        buf = bytearray(self.bs)
        struct.pack_into("<IHBB", buf, 0, ino, self.bs, len(raw_name), ftype)
        buf[DIRENT_HEAD:DIRENT_HEAD + len(raw_name)] = raw_name
        self._write_block(new_block, buf)
        self._dir_append_block(parent, node, new_block)

    def _dir_append_block(self, ino: int, node: ExtInode, block: int) -> None:
        """Dizin inode'una yeni bir veri blogu baglar."""
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
            while pos + DIRENT_HEAD <= self.bs:
                e_ino, rec_len, name_len, _ft = struct.unpack_from("<IHBB", buf, pos)
                if rec_len < DIRENT_HEAD:
                    break
                if e_ino and buf[pos + DIRENT_HEAD:pos + DIRENT_HEAD + name_len] == target:
                    if prev >= 0:
                        prev_len = struct.unpack_from("<H", buf, prev + 4)[0]
                        struct.pack_into("<H", buf, prev + 4, prev_len + rec_len)
                    else:
                        struct.pack_into("<I", buf, pos, 0)   # inode = 0
                    self._write_block(blk, buf)
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
        blk = self.alloc_block()

        buf = bytearray(self.bs)
        # "." kaydi
        struct.pack_into("<IHBB", buf, 0, ino, 12, 1, FT_DIR)
        buf[8:9] = b"."
        # ".." kaydi blogun sonuna kadar uzanir
        struct.pack_into("<IHBB", buf, 12, parent.number, self.bs - 12, 2, FT_DIR)
        buf[20:22] = b".."
        self._write_block(blk, buf)

        self.write_inode(ino, mode=S_IFDIR | 0o755, size=self.bs, links=2,
                         blocks=[blk] + [0] * 14, sectors=self.bs // 512)
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
