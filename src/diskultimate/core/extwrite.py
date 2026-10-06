"""ext2 / ext3 / ext4 **yazma** destegi (1. asama).

Okuma `extread.py` icindedir; bu modul onun uzerine yazmayi ekler:
dosya olusturma, klasor olusturma, silme ve yeniden adlandirma.

## Kapsam ve neden sinirli

Bir ext birimine yazmak, veriyi yazmaktan ibaret degildir: blok ve inode
bitmap'leri, grup tanimlayici sayaclari ve ustblok sayaclari **ayni anda**
tutarli kalmalidir.

Ozellik denetimi bir **izin listesidir** (e2fsprogs `EXT2_LIB_FEATURE_*_SUPP`
gibi): yalnizca yazicinin dogru isledigi bilinen bitler kabul edilir;
bilinmeyen (gelecekteki) her bit ve su ozellikler yazmayi reddettirir:
`bigalloc`, `inline_data`, kota, `encrypt`, `verity`, `ea_inode`, `mmp`,
ayri gunluk aygiti, sikistirma, `dirdata`, `shared_blocks`. Eskiden bir yasak
listesiydi; casefold, encrypt, ea_inode, verity, mmp yazilabiliyordu
(denetim 2026-10-06, E1/E9).

Ayrica birim **temiz** olmalidir: `s_state` VALID, hata biti yok, gunlukte
islenmemis kayit (needs_recovery) yok, yetim listesi bos. Aksi halde cekirdek
baglarken gunlugu bizim yazdiklarimizin uzerine oynatir ya da bitmap'ler
guvenilmezdir.

`64bit` **yalnizca** birim 4 milyar bloktan buyukse reddedilir.
casefold birimde yalnizca casefold **olmayan** dizinlere ad eklenir
(katlanmis ad karmasi uygulanmadi).

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

import io
import struct
import time
from typing import Dict, List, Optional, Tuple

from .extcsum import (DIRENT_TAIL_FT, DIRENT_TAIL_SIZE, GD_CHECKSUM_OFFSET,
                      INODE_CSUM_HI, INODE_CSUM_LO, SB_CHECKSUM_OFFSET,
                      ExtChecksums)
from . import exthtree
from .extlayout import ExtCsum, ExtGeometry, unsupported_features
from .extread import (EXTENTS_FL, INO_ROOT, S_IFDIR, S_IFMT, S_IFREG,
                      ExtError, ExtFS, ExtInode, _normalize)
from .streamio import group_runs, write_runs
from ..i18n import tr

# Ozellik bitleri
INCOMPAT_RECOVER = 0x0004
INCOMPAT_JOURNAL_DEV = 0x0008
INCOMPAT_FILETYPE = 0x0002
INCOMPAT_64BIT = 0x0080
INCOMPAT_MMP = 0x0100
INCOMPAT_EA_INODE = 0x0400
INCOMPAT_INLINE_DATA = 0x8000
INCOMPAT_ENCRYPT = 0x10000
INCOMPAT_CASEFOLD = 0x20000
RO_METADATA_CSUM = 0x0400
RO_BIGALLOC = 0x0200
RO_DIR_NLINK = 0x0020
# Kota dosyalari yazmada guncellenmez; e2fsck kullanimi "guncellenmeli" der.
# (Eskiden RO_BIGALLOC yanlislikla 0x0100 idi ve kotayi tesadufen reddediyordu.)
RO_QUOTA = 0x0100
RO_PROJECT = 0x2000
RO_VERITY = 0x8000
RO_ORPHAN_PRESENT = 0x10000

# --- Yazicinin dogru isledigi ozellikler (izin listesi) ---------------------
# compat: dir_prealloc, imagic_inodes, has_journal, ext_attr, resize_inode,
# dir_index, sparse_super2, fast_commit, stable_inodes (yazici inode
# numarasi degistirmez), orphan_file (bos olmasi ayrica denetlenir)
WRITE_COMPAT = 0x0001 | 0x0002 | 0x0004 | 0x0008 | 0x0010 | 0x0020 | \
    0x0200 | 0x0400 | 0x0800 | 0x1000
# incompat: filetype, meta_bg, extents, 64bit, flex_bg, csum_seed, largedir,
# casefold (dizin duzeyinde ayrica korunur)
WRITE_INCOMPAT = 0x0002 | 0x0010 | 0x0040 | 0x0080 | 0x0200 | 0x2000 | \
    0x4000 | INCOMPAT_CASEFOLD
# ro_compat: sparse_super, large_file, huge_file, gdt_csum, dir_nlink,
# extra_isize, metadata_csum, orphan_present (ayrica denetlenir: bos olmali)
WRITE_RO_COMPAT = 0x0001 | 0x0002 | 0x0008 | 0x0010 | 0x0020 | 0x0040 | \
    0x0400 | RO_ORPHAN_PRESENT

STATE_VALID = 0x0001
STATE_ERROR = 0x0002
STATE_ORPHAN = 0x0004
SB_STATE = 0x3A
SB_LAST_ORPHAN = 0xE8

# inode bayraklari
IMMUTABLE_FL = 0x00000010
APPEND_FL = 0x00000020
CASEFOLD_FL = 0x40000000
HUGE_FILE_FL = 0x00040000
LINK_MAX = 65000                       # EXT4_LINK_MAX

FT_REG = 1
FT_DIR = 2
FT_SYMLINK = 7

# Dizin girisi basligi: inode(4) + rec_len(2) + name_len(1) + file_type(1)
DIRENT_HEAD = 8
INDEX_FL = 0x00001000          # htree indeksli dizin
ENCRYPT_FL = 0x00000800        # fscrypt: adlar sifreli
INCOMPAT_EXTENTS_W = 0x0040
MAX_EXTENT_LEN = 32768


def _align4(n: int) -> int:
    return (n + 3) & ~3


class ExtWriter:
    """Bir `ExtFS` uzerine yazma islemleri ekler."""

    def __init__(self, fs: ExtFS):
        self.fs = fs
        self.dev = fs.dev
        self.bs = fs.block_size
        self.csum = ExtChecksums(fs)
        # Grup tanimlayici saglamasi iki algoritmadan biri olabilir:
        # metadata_csum (crc32c) veya eski uninit_bg/gdt_csum (crc16).
        # Yalnizca birincisi guncelleniyordu; crc16'li birimde her yazma
        # tanimlayiciyi "bozuk" birakiyordu.
        self.gcsum = ExtCsum(ExtGeometry(fs.dev.read(1024, 1024)))
        self.group_csum = bool(self.gcsum.kind)
        # extmove'un extent agaci okuyucu/kurucusu `_Volume` arayuzu bekler
        # (bs, read_block, write_block, csum.kind/seed). Ayni kod iki yerde
        # yazilmasin diye ince bir adaptor verilir.
        writer = self

        class _TreeIO:
            bs = self.bs
            csum = self.gcsum

            @staticmethod
            def read_block(block):
                return writer._read_block(block)

            @staticmethod
            def write_block(block, data):
                writer._write_block(block, bytes(data))

        self._tree_io = _TreeIO

    # ------------------------------------------------------------------
    # Destek denetimi
    # ------------------------------------------------------------------
    def write_support(self) -> Tuple[bool, str]:
        """(destekleniyor mu, neden) — neden yalnizca desteklenmiyorsa dolu."""
        fs = self.fs
        if getattr(self.dev, "readonly", False):
            return False, tr("Kaynak salt okunur acildi.")
        sb = self.dev.read(1024, 1024)
        state = struct.unpack_from("<H", sb, SB_STATE)[0]
        # Ayri gunluk aygiti bir dosya sistemi degildir: ustblogu ext'e benzer
        # ama icinde dizin/inode yoktur.
        if fs.feature_incompat & INCOMPAT_JOURNAL_DEV:
            return False, tr("Bu bir ext gunluk aygiti (journal_dev); dosya "
                             "sistemi degildir, icine yazilamaz.")
        if fs.feature_incompat & INCOMPAT_RECOVER:
            return False, tr(
                "Birimin gunlugunde islenmemis kayitlar var (needs_recovery: "
                "temiz kapatilmamis). Yazilirsa cekirdek baglarken gunlugu "
                "yazdiklarimizin uzerine oynatir. Once birimi Linux'ta baglayip "
                "duzgun ayirin veya e2fsck ile onarin.")
        if fs.feature_incompat & INCOMPAT_MMP:
            return False, tr(
                "Birim coklu baglama korumasi (mmp) kullaniyor; baska bir "
                "makinede bagli olabilir. Yazma reddedildi.")
        if not state & STATE_VALID or state & (STATE_ERROR | STATE_ORPHAN) \
                or struct.unpack_from("<I", sb, SB_LAST_ORPHAN)[0] \
                or fs.ro_compat & RO_ORPHAN_PRESENT:
            return False, tr(
                "Birim temiz degil (temiz kapatilmamis, hata kaydi ya da "
                "islenmemis yetim inode var); bitmap'lere guvenilemez. Once "
                "e2fsck ile denetleyin.")
        missing = []
        for name in unsupported_features(fs.compat, fs.feature_incompat,
                                         fs.ro_compat, WRITE_COMPAT,
                                         WRITE_INCOMPAT, WRITE_RO_COMPAT):
            if name in ("quota", "project"):
                name = tr("disk kotasi (quota)")
            if name not in missing:
                missing.append(name)
        if fs.feature_incompat & INCOMPAT_64BIT and fs.blocks_count > 0xFFFFFFFF:
            # `64bit` ozelligi tek basina engel degil: sorun ancak blok numarasi
            # 32 biti asarsa cikar. Saglamalar ve 64 baytlik grup tanimlayicisi
            # desteklenir.
            missing.append(tr("64bit (4 milyar bloktan buyuk birim)"))
        if missing:
            return False, tr(
                "Bu birim su ozellikleri kullaniyor ve bu surumde yazma "
                "desteklenmiyor: {}. Yanlis yazip birimi bozmamak icin islem "
                "reddedildi.", ", ".join(missing))
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
            raise ExtError(tr("Blok boyutu uyusmuyor"))
        if block <= 0 or block >= self.fs.blocks_count:
            raise ExtError(tr("Sinir disi blok yazimi: {}", block))
        self.dev.write(block * self.bs, bytes(data))

    def _stream_blocks(self, blocks: List[int], src, size: int) -> None:
        """Kaynaktan `size` bayti ayrilmis bloklara parca parca yazar.

        Ardisik bloklar tek yazimda birlesir (ADR 0081). Yazma yarida kalirsa
        bloklar geri verilir: ayrilmis ama sahipsiz blok e2fsck'te "block
        bitmap differences" olurdu.
        """
        def write(start: int, off: int, data: bytes) -> None:
            last = start + (off + len(data)) // self.bs
            if start <= 0 or last > self.fs.blocks_count:
                raise ExtError(tr("Sinir disi blok yazimi: {}", start))
            self.dev.write(start * self.bs + off, data)
        try:
            write_runs(src, size, group_runs(blocks), self.bs, write)
        except Exception:
            self.free_blocks(blocks)
            raise

    # ------------------------------------------------------------------
    # Grup tanimlayicisi ve ustblok sayaclari
    # ------------------------------------------------------------------
    def _gd_offset(self, group: int) -> int:
        return self.fs.descriptor_offset(group)

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
        if not self.group_csum:
            return
        off = self._gd_offset(group)
        desc = self.dev.read(off, self.fs.desc_size)
        value = self.gcsum.group_desc(group, desc)
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
        """Grup tahsise hazir mi? Baslatilmamissa once baslatilir.

        `BLOCK_UNINIT`/`INODE_UNINIT` gruplarda bitmap diskte tutulmaz
        (mkfs tembel baslatma). Eskiden bu gruplar atlaniyordu; gercek bir
        ext4'te bu, pratikte yalnizca ilk gruplarin kullanilmasi demekti
        ("Bos inode kalmadi" — 1K bloklu 200 MB birimde 3000 dosyada olculdu).
        """
        if not self.group_csum:
            return True
        flags = struct.unpack("<H", self.dev.read(
            self._gd_offset(group) + self.BG_FLAGS, 2))[0]
        flag = self.BG_INODE_UNINIT if inode_map else self.BG_BLOCK_UNINIT
        if flags & flag:
            if inode_map:
                self._init_inode_group(group, flags)
            else:
                self._init_block_group(group, flags)
        return True

    BG_ITABLE_ZEROED = 0x0004

    def _init_inode_group(self, group: int, flags: int) -> None:
        fs = self.fs
        ipg = fs.inodes_per_group
        itb = (ipg * fs.inode_size + self.bs - 1) // self.bs
        if not flags & self.BG_ITABLE_ZEROED:
            # Inode tablosu eski veri tasiyabilir; kullanima acilmadan sifirlanir
            table = fs._inode_table_block(group)
            zero = bytes(self.bs)
            for k in range(itb):
                self._write_block(table + k, zero)
            flags |= self.BG_ITABLE_ZEROED
        bmp = bytearray(self.bs)
        for i in range(ipg, self.bs * 8):
            bmp[i >> 3] |= 1 << (i & 7)
        self._write_block(self._bitmap_block(group, inode_map=True), bmp)
        flags &= ~self.BG_INODE_UNINIT
        self._gd_set(group, self.BG_FLAGS, flags)
        self._gd_set(group, 0x1C, ipg)                     # itable_unused (lo)
        if fs.desc_size >= 64:
            self.dev.write(self._gd_offset(group) + 0x32, b"\x00\x00")
        self._refresh_bitmap_csum(group, inode_map=True)
        self._refresh_group_csum(group)

    def _init_block_group(self, group: int, flags: int) -> None:
        """Blok bitmap'ini cekirdegin kuraliyla gercekler.

        Kural (ext4_init_block_bitmap): ustblok/GDT yedegi + (flex_bg ile)
        bu gruba dusen TUM gruplarin bitmap ve inode tablolari + son grupta
        sinir otesi dolgu. Serbest sayac bitmap'ten yeniden hesaplanir.
        """
        from .extlayout import ExtGeometry
        fs = self.fs
        geo = ExtGeometry(self.dev.read(1024, 1024))
        geo.desc_size = fs.desc_size
        start = geo.group_first_block(group)
        size = geo.group_block_count(group)
        used = set()
        super_blk, old_start, old_count, new_blk = geo.base_meta(group)
        if super_blk is not None:
            used.add(super_blk - start)
        used.update(b - start for b in range(old_start, old_start + old_count))
        if new_blk is not None:
            used.add(new_blk - start)
        itb = (fs.inodes_per_group * fs.inode_size + self.bs - 1) // self.bs
        for other in range(fs.group_count):
            for blk, count in ((self._bitmap_block(other, False), 1),
                               (self._bitmap_block(other, True), 1),
                               (fs._inode_table_block(other), itb)):
                for b in range(blk, blk + count):
                    if start <= b < start + size:
                        used.add(b - start)
        bmp = bytearray(self.bs)
        for i in used:
            bmp[i >> 3] |= 1 << (i & 7)
        for i in range(size, self.bs * 8):
            bmp[i >> 3] |= 1 << (i & 7)
        self._write_block(self._bitmap_block(group, inode_map=False), bmp)
        free = size - len(used)
        old_free = self._gd_get(group, 0x0C)
        self._gd_set(group, self.BG_FLAGS, flags & ~self.BG_BLOCK_UNINIT)
        if free != old_free:
            self._gd_set(group, 0x0C, free)
            self._sb_set(0x0C, self._sb_get(0x0C) + free - old_free)
            self._refresh_super_csum()
        self._refresh_bitmap_csum(group, inode_map=False)
        self._refresh_group_csum(group)

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
            raise ExtError(tr("Bos blok yetersiz: {} istendi, {} bulundu",
                              count, len(out)))
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
        raise ExtError(tr("Bos blok kalmadi"))

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
                    unused = self._gd_get(group, 0x1C)
                    if self.group_csum and unused > fs.inodes_per_group - (i + 1):
                        # "hic kullanilmamis kuyruk" yalnizca bu inode'un
                        # otesidir (cekirdek de boyle gunceller)
                        self._gd_set(group, 0x1C, fs.inodes_per_group - (i + 1))
                        self._refresh_group_csum(group)
                    self._bump_free_inodes(group, -1)
                    if is_dir:
                        self._bump_used_dirs(group, +1)
                    return ino
        raise ExtError(tr("Bos inode kalmadi"))

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
                    flags: int = 0, uid: int = 0, gid: int = 0,
                    i_block: Optional[bytes] = None) -> None:
        """Inode kaydini yazar. `i_block` verilirse (extent koku) `blocks`
        yok sayilir ve EXTENTS_FL bayragi beklenir."""
        now = int(time.time())
        raw = bytearray(self.fs.inode_size)
        struct.pack_into("<HHIIIII", raw, 0, mode, uid, size & 0xFFFFFFFF,
                         now, now, now, 0)
        struct.pack_into("<HH", raw, 0x18, gid, links)
        # i_blocks 512 baytlik sektor cinsindendir (dolayli bloklar dahil)
        struct.pack_into("<I", raw, 0x1C, sectors)
        struct.pack_into("<I", raw, 0x20, flags)
        if i_block is not None:
            raw[0x28:0x28 + 60] = i_block[:60]
        else:
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

    def _store_data(self, src, size: int) -> Tuple[List[int], int]:
        """Veriyi bloklara yazar; (i_block girdileri, 512'lik sektor sayisi).

        ext2/3 yerlesimi: 12 dogrudan blok, sonra tek / cift / uc kat dolayli.
        4 KB blokta tek kat ~4 MB'a, cift kat ~4 GB'a kadar gider. Dolayli
        tablolar da yer kaplar ve `i_blocks` sayacina dahildir.
        """
        bs = self.bs
        if size > self._max_bytes():
            raise ExtError(
                tr("Dosya cok buyuk: en fazla {} GB", self._max_bytes() >> 30))
        needed = (size + bs - 1) // bs
        if needed == 0:
            return [0] * 15, 0

        # Tum veri bloklari tek seferde tahsis edilir: blok basina bitmap
        # okuyup yazmak buyuk dosyalarda kabul edilemez yavaslikta.
        data_blocks = self.alloc_blocks(needed)
        self._stream_blocks(data_blocks, src, size)

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
    @property
    def uses_extents(self) -> bool:
        return bool(self.fs.feature_incompat & INCOMPAT_EXTENTS_W)

    def _store_extents(self, ino: int, src, size: int) -> Tuple[bytes, int]:
        """Veriyi yazar ve extent agacini kurar: (i_block, 512'lik sektor).

        ext4'te yeni dosyalar eskiden dolayli blok duzeniyle yaziliyordu
        (cekirdek okur ama ext4'e yabancidir, parcalidir). Bloklar bitisik
        araliklara toplanir; aralik en fazla 32768 blok olabilir.
        """
        from .extmove import build_extent_tree
        bs = self.bs
        needed = (size + bs - 1) // bs
        extents: List[Tuple[int, int, int, bool]] = []
        if needed:
            blocks = self.alloc_blocks(needed)
            self._stream_blocks(blocks, src, size)
            start, run = blocks[0], 1
            logical = 0
            for blk in blocks[1:]:
                if blk == start + run and run < MAX_EXTENT_LEN:
                    run += 1
                    continue
                extents.append((logical, run, start, False))
                logical += run
                start, run = blk, 1
            extents.append((logical, run, start, False))
        root, nodes = build_extent_tree(self._tree_io, ino, 0, extents,
                                        self.alloc_block)
        return root, (needed + len(nodes)) * (bs // 512)

    def _extent_nodes(self, node: ExtInode) -> Tuple[list, List[int]]:
        from .extmove import read_extents
        return read_extents(self._tree_io, node.raw_block)

    def _release_data(self, node: ExtInode) -> None:
        """Inode'un tuttugu tum bloklari serbest birakir.

        Dolayli tablolar da serbest birakilir: yalnizca veri bloklarini
        birakmak, tablolari sizdirip disk alanini kaybettirir.
        """
        if not node.has_block_map:
            return                  # aygit/fifo/soket, hizli bag, inline
        if node.uses_extents:
            # Extent'li inode: veri bloklari VE agacin ic/yaprak dugumleri
            # birakilir. Eskiden yalnizca veri birakiliyordu; derinligi > 0
            # olan dosya silinince dugum bloklari sizip bitmap'te dolu kaliyordu.
            leaves, nodes = self._extent_nodes(node)
            free: List[int] = list(nodes)
            for _logical, length, physical, _u in leaves:
                free.extend(range(physical, physical + length))
            self.free_blocks(free)
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
        """Dizinin veri bloklari: liste sirasi = mantiksal blok, delik = 0.

        Dizinde delik olabilir (e2fsck kabul eder, cekirdek atlar). Eskiden
        delikler listeden atiliyordu; htree'nin mantiksal blok numaralari
        bir kayiyor, yanlis bloga yaziliyordu (denetim E10). Cagiranlar 0'i
        atlar.
        """
        if node.is_inline:
            raise ExtError(tr("inline_data dizinine yazma desteklenmiyor"))
        count = (node.size + self.bs - 1) // self.bs
        if node.uses_extents:
            out = [0] * count
            for logical, physical, length in self.fs._extent_blocks(node):
                for k in range(length):
                    if logical + k < count:
                        out[logical + k] = physical + k
            return out
        return self.fs._indirect_blocks(node, count)

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

    # --- dizin blogu icinde yer bulma --------------------------------
    def _insert_in_block(self, buf: bytearray, raw_name: bytes, ino: int,
                         ftype: int) -> bool:
        """Girisi blogun bos kuyruguna koyar; sigmazsa False."""
        needed = _align4(DIRENT_HEAD + len(raw_name))
        limit = self._dir_usable_size()
        pos = 0
        while pos + DIRENT_HEAD <= limit:
            e_ino, rec_len, name_len, _ft = struct.unpack_from("<IHBB", buf, pos)
            if rec_len < DIRENT_HEAD or pos + rec_len > self.bs:
                return False
            used = _align4(DIRENT_HEAD + name_len) if e_ino else 0
            if rec_len - used >= needed:
                if e_ino:
                    struct.pack_into("<H", buf, pos + 4, used)
                    new_pos = pos + used
                else:
                    new_pos, used = pos, 0
                struct.pack_into("<IHBB", buf, new_pos, ino, rec_len - used,
                                 len(raw_name), ftype)
                buf[new_pos + DIRENT_HEAD:new_pos + DIRENT_HEAD + len(raw_name)] = raw_name
                return True
            pos += rec_len
        return False

    def dir_add(self, parent: int, name: str, ino: int, ftype: int) -> None:
        """Dizine yeni bir giris ekler.

        Indeksli (htree) dizinde giris ad karmasinin dustugu yapraga girer;
        yaprak doluysa bolunur. Eskiden bloklar duz sirayla taraniyor, ilk
        bosluga yaziliyordu: dx_root'taki ".." kaydinin "bos" kuyrugu aslinda
        indekstir — giris oraya yazilinca indeks eziliyordu (e2fsck: "HTREE
        directory ... invalid"; olculdu 2026-09-29).
        """
        raw_name = name.encode("utf-8")
        node = self.fs.read_inode(parent)
        self._check_can_add(node, raw_name)
        ftype = self._entry_ftype(ftype)
        if node.flags & INDEX_FL:
            if self._htree_add(parent, node, raw_name, ino, ftype):
                return
            # Indeks dugumu dolu (cok nadir): dizin guvenle dogrusala cevrilir
            self._htree_to_linear(parent, node)
            node = self.fs.read_inode(parent)

        for blk in self._dir_blocks(node):
            if not blk:
                continue                       # dizin deligi
            buf = self._read_block(blk)
            if self._insert_in_block(buf, raw_name, ino, ftype):
                self._write_dir_block(parent, blk, buf)
                return

        # Hicbir blokta yer yok: dizine yeni blok ekle
        new_block, buf = self._new_dir_block()
        struct.pack_into("<IHBB", buf, 0, ino, self._dir_usable_size(),
                         len(raw_name), ftype)
        buf[DIRENT_HEAD:DIRENT_HEAD + len(raw_name)] = raw_name
        self._write_dir_block(parent, new_block, buf)
        self._dir_append_block(parent, node, new_block)

    # --- htree --------------------------------------------------------
    def _hash_params(self, root: bytes) -> Tuple[int, Tuple[int, int, int, int]]:
        sb = self.dev.read(1024, 1024)
        seed = struct.unpack_from("<4I", sb, 0xEC)
        version = root[0x1C]
        if version <= exthtree.HASH_TEA and \
                struct.unpack_from("<I", sb, 0x160)[0] & 0x0002:
            version += 3                     # EXT2_FLAGS_UNSIGNED_HASH
        return version, seed

    def _dir_map(self, node: ExtInode) -> List[int]:
        """Mantiksal blok -> fiziksel blok listesi."""
        return self._dir_blocks(node)

    def _dx_stamp(self, dir_ino: int, buf: bytearray, count_off: int) -> None:
        if not self.csum.enabled:
            return
        limit, count = struct.unpack_from("<HH", buf, count_off)
        tail = count_off + limit * 8
        if tail + 8 > self.bs:
            return
        gen = self._inode_generation(dir_ino)
        crc = self.csum.inode_seed(dir_ino, gen)
        from .extcsum import raw_crc32c
        crc = raw_crc32c(crc, bytes(buf[:count_off + count * 8]))
        crc = raw_crc32c(crc, bytes(buf[tail:tail + 4]))
        crc = raw_crc32c(crc, b"\x00\x00\x00\x00")
        struct.pack_into("<I", buf, tail + 4, crc)

    def _leaf_entries(self, buf: bytes) -> List[Tuple[int, bytes, int]]:
        out = []
        pos = 0
        limit = self._dir_usable_size()
        while pos + DIRENT_HEAD <= limit:
            e_ino, rec_len, name_len, ft = struct.unpack_from("<IHBB", buf, pos)
            if rec_len < DIRENT_HEAD:
                break
            if e_ino:
                out.append((e_ino, bytes(buf[pos + DIRENT_HEAD:pos + DIRENT_HEAD + name_len]), ft))
            pos += rec_len
        return out

    def _pack_leaf(self, items: List[Tuple[int, bytes, int]]) -> bytearray:
        buf = bytearray(self.bs)
        limit = self._dir_usable_size()
        pos = 0
        for i, (e_ino, raw, ft) in enumerate(items):
            size = _align4(DIRENT_HEAD + len(raw))
            rec_len = size if i < len(items) - 1 else limit - pos
            struct.pack_into("<IHBB", buf, pos, e_ino, rec_len, len(raw), ft)
            buf[pos + DIRENT_HEAD:pos + DIRENT_HEAD + len(raw)] = raw
            pos += size
        if not items:
            struct.pack_into("<IHBB", buf, 0, 0, limit, 0, 0)
        if self.csum.enabled:
            struct.pack_into("<IHBB", buf, self.bs - DIRENT_TAIL_SIZE,
                             0, DIRENT_TAIL_SIZE, 0, DIRENT_TAIL_FT)
        return buf

    def _htree_add(self, dir_ino: int, node: ExtInode, raw_name: bytes,
                   ino: int, ftype: int) -> bool:
        phys = self._dir_map(node)
        if not phys or not phys[0]:
            return False
        root = self._read_block(phys[0])
        version, seed = self._hash_params(root)
        if version not in (0, 1, 2, 3, 4, 5):
            return False                     # casefold/siphash: dogrusala cevir
        info_len = root[0x1D]
        levels = root[0x1E]
        target, _minor = exthtree.dirhash(version, raw_name, seed)

        # Indeks boyunca in: her seviyede (mantiksal blok, tampon, sayac ofseti, sira)
        frames = []
        lblk, buf, off = 0, bytearray(root), 0x18 + info_len
        for _lvl in range(levels + 1):
            items = exthtree.entries(buf, off)
            pos = exthtree.find_slot(items, target)
            frames.append((lblk, buf, off, items, pos))
            lblk = items[pos][1]
            if _lvl < levels:
                if lblk >= len(phys) or not phys[lblk]:
                    return False
                buf, off = bytearray(self._read_block(phys[lblk])), exthtree.DX_NODE_COUNT
        if lblk >= len(phys) or not phys[lblk]:
            return False
        leaf_blk = phys[lblk]
        leaf = self._read_block(leaf_blk)
        if self._insert_in_block(leaf, raw_name, ino, ftype):
            self._write_dir_block(dir_ino, leaf_blk, leaf)
            return True

        # Yaprak dolu: ust dugumde yer yoksa bolme yapilamaz -> dogrusala
        p_lblk, p_buf, p_off, p_items, p_pos = frames[-1]
        limit, count = exthtree.count_limit(p_buf, p_off)
        if count >= limit:
            return False

        # Girisler karmaya gore siralanip ikiye bolunur
        items = self._leaf_entries(leaf) + [(ino, raw_name, ftype)]
        keyed = sorted(((exthtree.dirhash(version, raw, seed), e, raw, ft)
                        for e, raw, ft in items), key=lambda t: t[0])
        sizes = [_align4(DIRENT_HEAD + len(t[2])) for t in keyed]
        total, acc, split = sum(sizes), 0, 0
        for i, sz in enumerate(sizes):
            if acc + sz > total // 2 and i:
                split = i
                break
            acc += sz
        split = split or len(keyed) // 2 or 1
        hash2 = keyed[split][0][0]
        continued = 1 if keyed[split - 1][0][0] == hash2 else 0
        first = [(e, raw, ft) for _k, e, raw, ft in keyed[:split]]
        second = [(e, raw, ft) for _k, e, raw, ft in keyed[split:]]
        usable = self._dir_usable_size()
        if sum(sizes[:split]) > usable or sum(sizes[split:]) > usable:
            return False

        new_blk = self.alloc_block()
        new_lblk = len(phys)
        self._write_dir_block(dir_ino, leaf_blk, self._pack_leaf(first))
        self._write_dir_block(dir_ino, new_blk, self._pack_leaf(second))
        self._dir_append_block(dir_ino, self.fs.read_inode(dir_ino), new_blk)

        p_items.insert(p_pos + 1, (hash2 + continued, new_lblk))
        exthtree.write_entries(p_buf, p_off, p_items)
        self._dx_stamp(dir_ino, p_buf, p_off)
        p_phys = phys[p_lblk]
        self._write_block(p_phys, p_buf)
        return True

    def htree_leaf_for(self, dir_ino: int, name: str) -> Optional[int]:
        """Adin karmasinin indekste dustugu yaprak blogu (fiziksel).

        Dogrulama icindir: e2fsck olmayan platformda (Windows) indeksin
        tutarliligi, her girisin kendi karmasinin yapraginda durmasiyla
        denetlenir. Indeksli degilse None.
        """
        node = self.fs.read_inode(dir_ino)
        if not node.flags & INDEX_FL:
            return None
        phys = self._dir_map(node)
        root = self._read_block(phys[0])
        version, seed = self._hash_params(root)
        target, _m = exthtree.dirhash(version, name.encode("utf-8"), seed)
        buf, off = root, 0x18 + root[0x1D]
        for level in range(root[0x1E] + 1):
            items = exthtree.entries(buf, off)
            lblk = items[exthtree.find_slot(items, target)][1]
            if level < root[0x1E]:
                buf, off = self._read_block(phys[lblk]), exthtree.DX_NODE_COUNT
        return phys[lblk]

    def _htree_to_linear(self, dir_ino: int, node: ExtInode) -> None:
        """Indeksi kaldirir: dx bloklari gecerli (bos) dogrusal bloklara doner.

        Indeksiz cok bloklu dizin gecerli bir ext4 durumudur (cekirdek dogrusal
        arar). metadata_csum'da dogrusal blok saglama kuyrugu ister; dx
        bloklarinda kuyruk yoktur, bu yuzden yeniden yazilirlar.
        """
        phys = self._dir_map(node)
        if not phys or not phys[0]:
            raise ExtError(tr("Dizin indeksi bozuk: inode {}", dir_ino))
        root = self._read_block(phys[0])
        levels = root[0x1E]
        info_len = root[0x1D]
        dx_lblocks = {0}
        frontier = [(bytearray(root), 0x18 + info_len)]
        for _lvl in range(levels):
            nxt = []
            for buf, off in frontier:
                for _h, lb in exthtree.entries(buf, off):
                    if lb < len(phys) and phys[lb] and lb not in dx_lblocks:
                        dx_lblocks.add(lb)
                        nxt.append((bytearray(self._read_block(phys[lb])),
                                    exthtree.DX_NODE_COUNT))
            frontier = nxt
        usable = self._dir_usable_size()
        for lb in sorted(dx_lblocks):
            buf = bytearray(self.bs)
            if lb == 0:
                dot, dotdot = root[0:12], root[12:24]
                buf[0:12] = dot
                buf[12:24] = dotdot
                struct.pack_into("<H", buf, 12 + 4, usable - 12)
            else:
                struct.pack_into("<IHBB", buf, 0, 0, usable, 0, 0)
            if self.csum.enabled:
                struct.pack_into("<IHBB", buf, self.bs - DIRENT_TAIL_SIZE,
                                 0, DIRENT_TAIL_SIZE, 0, DIRENT_TAIL_FT)
            self._write_dir_block(dir_ino, phys[lb], buf)
        flags = struct.unpack("<I", self.dev.read(self._inode_offset(dir_ino) + 0x20, 4))[0]
        self._patch_inode(dir_ino, 0x20, "<I", flags & ~INDEX_FL)

    def _dir_append_block(self, ino: int, node: ExtInode, block: int) -> None:
        """Dizin inode'una sonraki mantiksal blok olarak `block`u baglar.

        Extent'li dizinde agac yeniden kurulur (derinlik gerekirse artar);
        dolayli dizinde dogrudan -> tek -> cift kat dolayli tablo acilir.
        Eskiden extent'li dizin dolunca yazma reddediliyor, dolayli dizin 12
        blokta (4K'da ~48 KB, birkac yuz giris) duruyordu.
        """
        raw = bytearray(self.dev.read(self._inode_offset(ino), self.fs.inode_size))
        size = struct.unpack_from("<I", raw, 0x04)[0]
        lblk = size // self.bs
        sectors = struct.unpack_from("<I", raw, 0x1C)[0]
        per = self.bs // 512
        flags = struct.unpack_from("<I", raw, 0x20)[0]
        if flags & EXTENTS_FL:
            from .extmove import _merge_extents, build_extent_tree, read_extents
            leaves, nodes = read_extents(self._tree_io, bytes(raw[0x28:0x28 + 60]))
            leaves = _merge_extents(list(leaves) + [(lblk, 1, block, False)])
            gen = struct.unpack_from("<I", raw, 0x64)[0]
            self.free_blocks(list(nodes))
            root, new_nodes = build_extent_tree(self._tree_io, ino, gen, leaves,
                                                self.alloc_block)
            raw[0x28:0x28 + 60] = root
            sectors += (1 + len(new_nodes) - len(nodes)) * per
        else:
            n = self.bs // 4
            if lblk < 12:
                struct.pack_into("<I", raw, 0x28 + lblk * 4, block)
                sectors += per
            elif lblk < 12 + n:
                ind = struct.unpack_from("<I", raw, 0x28 + 12 * 4)[0]
                if not ind:
                    ind = self.alloc_block()
                    self._write_block(ind, bytes(self.bs))
                    struct.pack_into("<I", raw, 0x28 + 12 * 4, ind)
                    sectors += per
                table = self._read_block(ind)
                struct.pack_into("<I", table, (lblk - 12) * 4, block)
                self._write_block(ind, table)
                sectors += per
            elif lblk < 12 + n + n * n:
                k = lblk - 12 - n
                dind = struct.unpack_from("<I", raw, 0x28 + 13 * 4)[0]
                if not dind:
                    dind = self.alloc_block()
                    self._write_block(dind, bytes(self.bs))
                    struct.pack_into("<I", raw, 0x28 + 13 * 4, dind)
                    sectors += per
                outer = self._read_block(dind)
                mid = struct.unpack_from("<I", outer, (k // n) * 4)[0]
                if not mid:
                    mid = self.alloc_block()
                    self._write_block(mid, bytes(self.bs))
                    struct.pack_into("<I", outer, (k // n) * 4, mid)
                    self._write_block(dind, outer)
                    sectors += per
                table = self._read_block(mid)
                struct.pack_into("<I", table, (k % n) * 4, block)
                self._write_block(mid, table)
                sectors += per
            else:
                raise ExtError(tr("Dizin cok buyuk"))
        struct.pack_into("<I", raw, 0x04, size + self.bs)
        struct.pack_into("<I", raw, 0x1C, sectors)
        self._stamp_inode_csum(ino, raw)
        self.dev.write(self._inode_offset(ino), bytes(raw))
        self.fs._inode_cache.pop(ino, None)

    def dir_remove(self, parent: int, name, ino: int = 0) -> None:
        """Girisi siler: onceki kaydin `rec_len` degeri uzerine alinir.

        `name` str ya da diskteki ham bayt olabilir (UTF-8 olmayan adlar
        yalnizca ham baytla bulunur — denetim E4). `ino` verilirse giris
        ayrica inode numarasiyla eslesmelidir.
        """
        node = self.fs.read_inode(parent)
        target = name if isinstance(name, bytes) else name.encode("utf-8")
        for blk in self._dir_blocks(node):
            if not blk:
                continue                       # dizin deligi
            buf = self._read_block(blk)
            pos, prev = 0, -1
            limit = self._dir_usable_size()
            while pos + DIRENT_HEAD <= limit:
                e_ino, rec_len, name_len, _ft = struct.unpack_from("<IHBB", buf, pos)
                if rec_len < DIRENT_HEAD:
                    break
                if e_ino and (not ino or e_ino == ino) and \
                        buf[pos + DIRENT_HEAD:pos + DIRENT_HEAD + name_len] == target:
                    if prev >= 0:
                        prev_len = struct.unpack_from("<H", buf, prev + 4)[0]
                        struct.pack_into("<H", buf, prev + 4, prev_len + rec_len)
                    else:
                        struct.pack_into("<I", buf, pos, 0)   # inode = 0
                    self._write_dir_block(parent, blk, buf)
                    return
                prev = pos
                pos += rec_len
        raise ExtError(tr("Dizin girisi bulunamadi: {}",
                          target.decode("utf-8", "replace")))

    def _check_can_add(self, dir_node: ExtInode, raw_name: bytes) -> None:
        """Dizine ad eklenebilir mi? Yazmadan ONCE cagrilir (yarim islem
        birakmamak icin)."""
        if _align4(DIRENT_HEAD + len(raw_name)) > self._dir_usable_size() or \
                len(raw_name) > 255:
            raise ExtError(tr("Dosya adi cok uzun"))
        if dir_node.flags & ENCRYPT_FL:
            raise ExtError(tr("Bu dizin sifreli (fscrypt); adlar anahtar "
                              "olmadan yazilamaz."))
        if dir_node.flags & CASEFOLD_FL:
            # Karma katlanmis (casefold) adla hesaplanir ve ayni ad farkli
            # harfle tekrar eklenemez; ikisi de uygulanmadi (denetim E5).
            raise ExtError(tr(
                "Bu klasor buyuk/kucuk harf duyarsiz (casefold); bu surumde "
                "icine ad eklenemez."))
        if dir_node.flags & IMMUTABLE_FL:
            raise ExtError(tr("Klasor degistirilemez (immutable) olarak "
                              "isaretli."))

    @staticmethod
    def _check_mutable(node: ExtInode, name: str) -> None:
        if node.flags & (IMMUTABLE_FL | APPEND_FL):
            raise ExtError(tr("Oge degistirilemez (immutable/append-only) "
                              "olarak isaretli: {}", name))

    def _find_entry(self, parent: ExtInode, name: str):
        for e in self.fs.read_dir(parent):
            if e.name == name and e.name not in (".", ".."):
                return e
        return None

    def _entry_ftype(self, ftype: int) -> int:
        # filetype ozelligi yoksa bu bayt ad uzunlugunun ust yarisidir.
        return ftype if self.fs.feature_incompat & INCOMPAT_FILETYPE else 0

    # ------------------------------------------------------------------
    # Ust duzey islemler
    # ------------------------------------------------------------------
    @staticmethod
    def _split(path: str) -> Tuple[str, str]:
        norm = _normalize(path)
        if norm == "/":
            raise ExtError(tr("Kok dizin uzerinde islem yapilamaz"))
        i = norm.rfind("/")
        return (norm[:i] or "/"), norm[i + 1:]

    def _require_space(self, blocks: int, inodes: int = 0) -> None:
        """Islem baslamadan yer denetimi — yarida kalan yazma birimi bozar.

        Olculdu (2026-09-29): dolu birimde inode ayrildiktan sonra veri
        tahsisi basarisiz oluyor, inode bitmap'te dolu ama kullanilmayan
        kaliyordu (e2fsck "inode bitmap differences"). Tahmin ust siniridir:
        veri + extent dugumleri + dizin buyumesi (yaprak bolme / yeni blok).
        """
        sb = self.dev.read(1024, 1024)
        free = struct.unpack_from("<I", sb, 0x0C)[0]
        if self.fs.feature_incompat & 0x0080:
            free |= struct.unpack_from("<I", sb, 0x158)[0] << 32
        free_inodes = struct.unpack_from("<I", sb, 0x10)[0]
        if blocks > free:
            raise ExtError(tr("Yeterli bos alan yok: {} blok gerekli, {} bos",
                              blocks, free))
        if inodes > free_inodes:
            raise ExtError(tr("Bos inode kalmadi"))

    def _blocks_needed(self, size: int) -> int:
        data = (size + self.bs - 1) // self.bs
        tree = data // max(1, (self.bs - 12) // 12) + 2 if self.uses_extents \
            else data // (self.bs // 4) + 3
        return data + tree + 3                 # +3: dizin blogu/bolme/agac

    def write_file(self, path: str, data: bytes) -> None:
        """Dosya olusturur veya uzerine yazar."""
        self.write_stream(path, io.BytesIO(data), len(data))

    def write_stream(self, path: str, src, size: int) -> None:
        """Kaynaktan `size` bayti parca parca yazar (ADR 0081)."""
        self._require_writable()
        parent_path, name = self._split(path)
        parent = self.fs.resolve(parent_path)
        if not parent.is_dir:
            raise ExtError(tr("Dizin degil: {}", parent_path))

        existing = self._find_entry(parent, name)
        if existing is not None:
            old = self.fs.read_inode(existing.inode)
            if old.is_dir:
                raise ExtError(tr("Ayni adda klasor var: {}", name))
            if not old.is_regular or not old.has_block_map:
                # Bag/aygit/fifo ustune "dosya" yazmak tur baytini ve inode'u
                # tutarsiz birakirdi.
                raise ExtError(tr("Ayni adda dosya olmayan bir oge var: {}",
                                  name))
            self._check_mutable(old, name)
            self._overwrite(old, src, size)
            return
        self._check_can_add(parent, name.encode("utf-8"))
        self._require_space(self._blocks_needed(size), inodes=1)
        ino = self.alloc_inode(is_dir=False)
        try:
            self._write_file_body(ino, src, size, True, parent, name)
        except Exception:
            # ayrilan inode geri verilir; bitmap'te sahipsiz kalmasin
            try:
                self.free_inode(ino, was_dir=False)
            except Exception:              # noqa: BLE001
                pass
            raise

    def _overwrite(self, old: ExtInode, src, size: int) -> None:
        """Var olan dosyanin icerigini degistirir; **inode ustverisi korunur**.

        Eskiden inode bastan yaziliyordu: bag sayisi 1, uid/gid 0, kip 0644,
        govde ici xattr'lar ve xattr blogu kayboluyordu (denetim E2/E7).
        Simdi yalnizca boyut, blok sayisi, esleme ve zamanlar degisir.

        Yer yetiyorsa yeni veri ONCE yazilir, inode ona cevrilir, eski bloklar
        en son birakilir: yarida kalan yazma eski icerigi bozmaz. Yer
        yetmiyorsa dosya once bosaltilir (tutarli ama bos kalir).
        """
        ino = old.number
        old_blocks = (old.blocks512 * 512 + self.bs - 1) // self.bs
        need = self._blocks_needed(size)
        sb = self.dev.read(1024, 1024)
        free = struct.unpack_from("<I", sb, 0x0C)[0]
        if self.fs.feature_incompat & INCOMPAT_64BIT:
            free |= struct.unpack_from("<I", sb, 0x158)[0] << 32
        if need > free:
            self._require_space(need - old_blocks)
            self._set_content(ino, 0, b"", 0)          # once bosalt
            self._release_data(old)
            old = self.fs.read_inode(ino)
        if self.uses_extents:
            root, sectors = self._store_extents(ino, src, size)
            self._set_content(ino, size, root, sectors, extents=True)
        else:
            i_block, sectors = self._store_data(src, size)
            raw = b"".join(struct.pack("<I", b) for b in i_block)
            self._set_content(ino, size, raw, sectors)
        self._release_data(old)
        self.fs._inode_cache.clear()

    def _set_content(self, ino: int, size: int, i_block: bytes, sectors: int,
                     extents: bool = False) -> None:
        """Inode'un yalnizca icerikle ilgili alanlarini degistirir."""
        off = self._inode_offset(ino)
        raw = bytearray(self.dev.read(off, self.fs.inode_size))
        now = int(time.time())
        acl = struct.unpack_from("<I", raw, 0x68)[0]
        if self.fs.feature_incompat & INCOMPAT_64BIT:
            acl |= struct.unpack_from("<H", raw, 0x76)[0] << 32
        if acl:
            sectors += self.bs // 512          # xattr blogu i_blocks'a dahil
        if not extents and not i_block:
            i_block = bytes(60)
            if self.uses_extents and struct.unpack_from(
                    "<I", raw, 0x20)[0] & EXTENTS_FL:
                # bos extent koku: cekirdek ve e2fsck bunu bekler
                i_block = struct.pack("<HHHHI", 0xF30A, 0, 4, 0, 0) + bytes(48)
                extents = True
        struct.pack_into("<I", raw, 0x04, size & 0xFFFFFFFF)
        struct.pack_into("<I", raw, 0x6C, (size >> 32) & 0xFFFFFFFF)
        struct.pack_into("<I", raw, 0x1C, sectors & 0xFFFFFFFF)
        struct.pack_into("<H", raw, 0x74, 0)                 # i_blocks_hi
        struct.pack_into("<II", raw, 0x0C, now, now)          # ctime, mtime
        flags = struct.unpack_from("<I", raw, 0x20)[0] & ~(EXTENTS_FL | HUGE_FILE_FL)
        if extents:
            flags |= EXTENTS_FL
        struct.pack_into("<I", raw, 0x20, flags)
        raw[0x28:0x28 + 60] = i_block[:60].ljust(60, b"\x00")
        self._stamp_inode_csum(ino, raw)
        self.dev.write(off, bytes(raw))
        self.fs._inode_cache.pop(ino, None)

    def _write_file_body(self, ino: int, src, size: int, is_new: bool,
                         parent, name: str) -> None:
        """Veriyi ve inode'u yazar, yeni dosyayi dizine baglar."""
        if self.uses_extents:
            root, sectors = self._store_extents(ino, src, size)
            self.write_inode(ino, mode=S_IFREG | 0o644, size=size, links=1,
                             blocks=[], sectors=sectors, flags=EXTENTS_FL,
                             i_block=root)
        else:
            i_block, sectors = self._store_data(src, size)
            self.write_inode(ino, mode=S_IFREG | 0o644, size=size, links=1,
                             blocks=i_block, sectors=sectors)
        if is_new:
            self.dir_add(parent.number, name, ino, FT_REG)
        self.fs._inode_cache.clear()

    def mkdir(self, path: str) -> None:
        """Bos bir klasor olusturur (`.` ve `..` girisleriyle)."""
        self._require_writable()
        parent_path, name = self._split(path)
        parent = self.fs.resolve(parent_path)
        if not parent.is_dir:
            raise ExtError(tr("Dizin degil: {}", parent_path))
        if any(e.name == name for e in self.fs.read_dir(parent)):
            raise ExtError(tr("Zaten var: {}", name))
        self._check_can_add(parent, name.encode("utf-8"))
        parent_links = self._parent_links_after_mkdir(parent)

        self._require_space(4, inodes=1)
        ino = self.alloc_inode(is_dir=True)
        blk, buf = self._new_dir_block()
        # "." kaydi
        struct.pack_into("<IHBB", buf, 0, ino, 12, 1, self._entry_ftype(FT_DIR))
        buf[8:9] = b"."
        # ".." kaydi kullanilabilir alanin sonuna kadar uzanir (saglama
        # kuyrugu varsa onun oncesine kadar)
        struct.pack_into("<IHBB", buf, 12, parent.number,
                         self._dir_usable_size() - 12, 2,
                         self._entry_ftype(FT_DIR))
        buf[20:22] = b".."
        # Inode ONCE yazilir: dizin blogu saglamasi i_generation'a baglidir.
        self.write_inode(ino, mode=S_IFDIR | 0o755, size=self.bs, links=2,
                         blocks=[blk] + [0] * 14, sectors=self.bs // 512)
        self._write_dir_block(ino, blk, buf)
        self.dir_add(parent.number, name, ino, FT_DIR)
        # ust dizinin baglanti sayisi ".." yuzunden bir artar
        self._patch_inode(parent.number, 0x1A, "<H", parent_links)
        self.fs._inode_cache.clear()

    def _parent_links_after_mkdir(self, parent: ExtInode) -> int:
        """Alt klasor eklenince ust dizinin yeni bag sayisi.

        `dir_nlink`: 65000'i asan (ya da zaten 1 olan) sayac 1 olur ("sayilmiyor"
        — cekirdek `ext4_inc_count`). Ozellik yoksa sinir asilamaz.
        """
        links = parent.links
        if self.fs.ro_compat & RO_DIR_NLINK:
            if links == 1 or links + 1 > LINK_MAX:
                return 1
            return links + 1
        if links + 1 > LINK_MAX:
            raise ExtError(tr("Klasorde en fazla {} alt klasor olabilir",
                              LINK_MAX - 2))
        return links + 1

    def remove(self, path: str) -> None:
        """Dosyayi veya **bos** klasoru siler.

        Sira: once dizin girisi kaldirilir, sonra (bag sayisi sifira inerse)
        veri ve inode birakilir. Eskiden veri once birakiliyordu; giris
        bulunamazsa (UTF-8 olmayan ad) bloklar bos ama giris duruyordu (E4).
        Sabit bagli dosyada yalnizca bag sayisi azalir (E2).
        """
        self._require_writable()
        parent_path, name = self._split(path)
        parent = self.fs.resolve(parent_path)
        target = self._find_entry(parent, name)
        if target is None:
            raise ExtError(tr("Bulunamadi: {}", path))
        node = self.fs.read_inode(target.inode)
        self._check_mutable(node, name)
        if parent.flags & (IMMUTABLE_FL | APPEND_FL):
            raise ExtError(tr("Oge degistirilemez (immutable/append-only) "
                              "olarak isaretli: {}", parent_path))

        if node.is_dir:
            remaining = [e.name for e in self.fs.read_dir(node)
                         if e.name not in (".", "..")]
            if remaining:
                raise ExtError(tr("Klasor bos degil: {} ({} giris)",
                                  name, len(remaining)))

        self.dir_remove(parent.number, target.raw_name or name, target.inode)
        now = int(time.time())
        if not node.is_dir and node.links > 1:
            self._patch_inode(target.inode, 0x1A, "<H", node.links - 1)
            self._patch_inode(target.inode, 0x0C, "<I", now)       # ctime
            self.fs._inode_cache.clear()
            return

        # Hizli bag, aygit/fifo/soket: i_block blok numarasi degildir (E3).
        if node.has_block_map:
            self._release_data(node)
        self._release_xattr_block(node)
        self._patch_inode(target.inode, 0x1A, "<H", 0)          # links = 0
        self._patch_inode(target.inode, 0x14, "<I", now)        # dtime
        self.free_inode(target.inode, was_dir=node.is_dir)
        if node.is_dir and parent.links > 2:
            # dir_nlink'te 1 "sayilmiyor" demektir ve oyle kalir
            self._patch_inode(parent.number, 0x1A, "<H", parent.links - 1)
        self.fs._inode_cache.clear()

    def _release_xattr_block(self, node: ExtInode) -> None:
        """Ek oznitelik blogunun referans sayisini azaltir; 0'a inerse birakir.

        Eskiden silinen inode'un xattr blogu bitmap'te dolu kaliyordu (E7).
        Blok birden cok inode'ca paylasilabilir (h_refcount).
        """
        acl = node.file_acl
        if not acl or acl >= self.fs.blocks_count:
            return
        buf = self._read_block(acl)
        if struct.unpack_from("<I", buf, 0)[0] != 0xEA020000:
            return                              # bozuk/tanimsiz: dokunma
        refcount = struct.unpack_from("<I", buf, 4)[0]
        if refcount > 1:
            struct.pack_into("<I", buf, 4, refcount - 1)
            if self.csum.enabled:
                from .extcsum import raw_crc32c
                struct.pack_into("<I", buf, 0x10, 0)
                crc = raw_crc32c(self.csum.seed, struct.pack("<Q", acl))
                crc = raw_crc32c(crc, bytes(buf))
                struct.pack_into("<I", buf, 0x10, crc)
            self._write_block(acl, buf)
        else:
            self.free_blocks([acl])

    def rename(self, path: str, new_name: str) -> None:
        """Ayni dizin icinde yeniden adlandirir.

        Yeni giris ONCE eklenir, eski giris sonra kaldirilir: ekleme yer
        bulamazsa birim degismemis olur (eskiden once siliniyordu; ekleme
        basarisiz olunca dosya hicbir dizinde kalmiyordu). Dizin girisinin
        tur bayti korunur (eskiden bag/aygit da FT_REG yaziliyordu — E10).
        """
        self._require_writable()
        parent_path, name = self._split(path)
        if "/" in new_name or new_name in ("", ".", ".."):
            raise ExtError(tr("Yeni ad yol icermemeli"))
        parent = self.fs.resolve(parent_path)
        target = None
        for e in self.fs.read_dir(parent):
            if e.name == new_name:
                raise ExtError(tr("Zaten var: {}", new_name))
            if e.name == name and target is None and name not in (".", ".."):
                target = e
        if target is None:
            raise ExtError(tr("Bulunamadi: {}", path))
        node = self.fs.read_inode(target.inode)
        self._check_mutable(node, name)
        if parent.flags & (IMMUTABLE_FL | APPEND_FL):
            raise ExtError(tr("Oge degistirilemez (immutable/append-only) "
                              "olarak isaretli: {}", parent_path))
        self._check_can_add(parent, new_name.encode("utf-8"))
        ftype = target.file_type
        if self.fs.feature_incompat & INCOMPAT_FILETYPE and not ftype:
            ftype = self._ftype_for(node)
        self._require_space(3)
        self.dir_add(parent.number, new_name, target.inode, ftype)
        self.dir_remove(parent.number, target.raw_name or name, target.inode)
        self.fs._inode_cache.clear()

    @staticmethod
    def _ftype_for(node: ExtInode) -> int:
        """inode kipinden dizin girisi tur bayti (EXT2_FT_*)."""
        return {0x8000: 1, 0x4000: 2, 0x2000: 3, 0x6000: 4, 0x1000: 5,
                0xC000: 6, 0xA000: 7}.get(node.mode & S_IFMT, 0)

    def flush(self) -> None:
        f = getattr(self.dev, "flush", None)
        if f:
            f()
