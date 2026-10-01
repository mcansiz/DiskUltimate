"""Saf Python ext2 / ext3 / ext4 bicimlendirici (mkfs).

Amac: `mkfs.ext*` araclarinin bulunmadigi platformlarda (Windows, macOS) da
ext ailesini bicimlendirebilmek. Okuma destegi bu modulde yoktur; yalnizca
olusturma yapilir (okuma yol haritasinda).

Yerlesim (spec: .claude/specs/ext.md):
    1024. bayt        Superblok
    sonraki blok(lar) Grup tanimlayici tablosu (GDT)
    her grup icin     blok bitmap, inode bitmap, inode tablosu, veri bloklari

Surumler arasindaki fark yalnizca ozellik bayraklaridir:
    ext2 = temel
    ext3 = ext2 + has_journal (JBD2 gunlugu)
    ext4 = ext3 + extent + flex_bg + metadata_csum (mkfs.ext4 varsayilanlari;
           64bit ve resize_inode haric — ADR 0073)
"""
from __future__ import annotations

import struct
import time
import uuid as _uuid
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .extcsum import ExtChecksums
from .image import BlockDevice
from ..i18n import tr

EXT_MAGIC = 0xEF53
JBD2_MAGIC = 0xC03B3998

# --- s_feature_compat ---
COMPAT_DIR_PREALLOC = 0x0001
COMPAT_HAS_JOURNAL = 0x0004
COMPAT_EXT_ATTR = 0x0008
COMPAT_RESIZE_INODE = 0x0010
COMPAT_DIR_INDEX = 0x0020

# --- s_feature_incompat ---
INCOMPAT_FILETYPE = 0x0002
INCOMPAT_EXTENTS = 0x0040
INCOMPAT_64BIT = 0x0080
INCOMPAT_FLEX_BG = 0x0200

# --- s_feature_ro_compat ---
RO_SPARSE_SUPER = 0x0001
RO_LARGE_FILE = 0x0002
RO_HUGE_FILE = 0x0008
RO_DIR_NLINK = 0x0020
RO_EXTRA_ISIZE = 0x0040
RO_METADATA_CSUM = 0x0400

EXTENTS_FL = 0x00080000          # i_flags: i_block bir extent agaci tasir
EXT4_EXT_MAGIC = 0xF30A
EXTENT_MAX_LEN = 32768           # baslatilmis extent'in azami blok sayisi
INODE_EXTENTS = 4                # inode icine sigan extent sayisi (derinlik 0)
LOG_GROUPS_PER_FLEX = 4          # flex_bg: 16 grubun metaverisi bir arada
BG_ITABLE_ZEROED = 0x0004

# inode numaralari
INO_BAD = 1
INO_ROOT = 2
INO_USER_QUOTA = 3
INO_GROUP_QUOTA = 4
INO_BOOT_LOADER = 5
INO_UNDEL_DIR = 6
INO_RESIZE = 7
INO_JOURNAL = 8
INO_FIRST = 11          # ilk kullanilabilir inode (lost+found burada)

# dosya turleri (dizin girisi file_type alani)
FT_UNKNOWN, FT_REG, FT_DIR = 0, 1, 2

S_IFDIR = 0x4000
S_IFREG = 0x8000


class ExtError(Exception):
    pass


@dataclass
class ExtLayout:
    """Hesaplanmis yerlesim parametreleri."""
    block_size: int
    block_count: int
    inode_count: int
    blocks_per_group: int
    inodes_per_group: int
    inode_size: int
    group_count: int
    first_data_block: int
    gdt_blocks: int
    reserved_blocks: int

    @property
    def inode_table_blocks(self) -> int:
        total = self.inodes_per_group * self.inode_size
        return (total + self.block_size - 1) // self.block_size


def _has_super(group: int) -> bool:
    """sparse_super: yedek superblok yalnizca 0, 1 ve 3/5/7'nin kuvvetlerinde."""
    if group in (0, 1):
        return True
    for taban in (3, 5, 7):
        kuvvet = taban
        while kuvvet < group:
            kuvvet *= taban
        if kuvvet == group:
            return True
    return False


class ExtFormatter:
    """ext2/3/4 birimi olusturur."""

    def __init__(self, dev: BlockDevice, version: str = "ext4",
                 block_size: int = 0, label: str = "",
                 bytes_per_inode: int = 16384, reserved_percent: float = 5.0,
                 modern: bool = True):
        if version not in ("ext2", "ext3", "ext4"):
            raise ExtError(tr("Bilinmeyen surum: {}", version))
        self.dev = dev
        self.version = version
        self.label = label[:16]
        self.bytes_per_inode = max(1024, bytes_per_inode)
        self.reserved_percent = max(0.0, min(50.0, reserved_percent))
        self.block_size = block_size or self._default_block_size()
        if self.block_size not in (1024, 2048, 4096):
            raise ExtError(tr("Blok boyutu 1024, 2048 veya 4096 olmalidir"))
        self.uuid = _uuid.uuid4()
        self.now = int(time.time())
        # ext4 mkfs.ext4 ile ayni temel ozellikleri alir (ADR 0073). Eskiden
        # yazici extent bilmedigi icin kapaliydi (ADR 0017); artik yazici,
        # tasiyici ve buyutucu bunlarin hepsini isliyor. modern=False eski
        # (3.18 oncesi cekirdekler icin) ozellik setini uretir.
        modern_set = version == "ext4" and modern
        self.extents = modern_set
        self.flex_bg = modern_set
        self.metadata_csum = modern_set
        self.layout = self._compute_layout()
        self._csum = (ExtChecksums.for_new(self.uuid.bytes, self.layout.inode_size)
                      if self.metadata_csum else None)
        self._journal_blocks = self._journal_size() if version != "ext2" else 0

    # ---- yerlesim hesabi --------------------------------------------------
    def _default_block_size(self) -> int:
        size = self.dev.size
        if size < 3 * 1024 * 1024:
            return 1024
        if size < 512 * 1024 * 1024:
            return 1024 if size < 16 * 1024 * 1024 else 4096
        return 4096

    def _journal_size(self) -> int:
        """JBD2 gunluk boyutu (blok).

        Ust sinir **12 + blok_boyutu/4**'tur: gunluk inode'u 12 dogrudan blok ve
        TEK dolayli blok ile eslenir. Iki kademeli (double indirect) esleme bu
        surumde uygulanmadigi icin daha buyuk gunluk olusturulmaz — boyle bir
        gunluk e2fsck tarafindan "gecersiz gunluk" sayilir.
        """
        bloklar = self.layout.block_count
        if bloklar < 2048:
            return 0
        if self.extents:
            # mkfs.ext4 tablosu (ext2fs_default_journal_size). Extent'li
            # gunluk inode'u en cok 4 x 32768 blok tasir (derinlik 0); mkfs'in
            # 128 GB ustu icin sectigi 1 GB yerine burada 512 MB'ta kalinir.
            for sinir, boy in ((32768, 1024), (256 * 1024, 4096),
                               (512 * 1024, 8192), (4096 * 1024, 16384),
                               (8192 * 1024, 32768), (16384 * 1024, 65536)):
                if bloklar < sinir:
                    return boy
            return INODE_EXTENTS * EXTENT_MAX_LEN
        azami = 12 + self.layout.block_size // 4
        istenen = 1024 if bloklar < 32768 else 4096
        return min(istenen, azami)

    def _compute_layout(self) -> ExtLayout:
        bs = self.block_size
        total_blocks = self.dev.size // bs
        if total_blocks < 64:
            raise ExtError(tr("Bolum ext icin cok kucuk"))
        first_data = 1 if bs == 1024 else 0
        blocks_per_group = bs * 8              # blok bitmap tek blok
        inode_size = 128 if self.version == "ext2" else 256

        # Grup sayisi yukari yuvarlanir, bu yuzden kucuk bir artik bile tam bir
        # grup dogurur. O son grup kendi metaverisini (yedek superblok + GDT +
        # iki bitmap + inode tablosu) almayacak kadar kucuk kalabilir; yerlesim
        # o zaman bolum sinirini asar. `mke2fs` bu durumda dosya sistemini grup
        # sinirina **kirpar**; burada da ayni yapilir. Kirpma, inode tablosu
        # boyutunu degistirdigi icin yeniden hesap gerekir — bu yuzden dongu.
        for _ in range(8):
            usable = total_blocks - first_data
            group_count = max(
                1, (usable + blocks_per_group - 1) // blocks_per_group)

            total_inodes = max(16, (total_blocks * bs // self.bytes_per_inode))
            inodes_per_group = (total_inodes + group_count - 1) // group_count
            # inode tablosu blok sinirina hizalanmali ve 8'in kati olmali
            inodes_per_group = max(16, ((inodes_per_group + 7) // 8) * 8)
            inodes_per_group = min(inodes_per_group, bs * 8)  # inode bitmap tek blok

            gdt_block = (group_count * 32 + bs - 1) // bs
            table_blocks = (inodes_per_group * inode_size + bs - 1) // bs

            last_group = group_count - 1
            last_blocks = usable - last_group * blocks_per_group
            overhead = (1 + gdt_block if _has_super(last_group) else 0) + 2 + table_blocks
            if group_count == 1 or last_blocks > overhead:
                break
            # Son grup metaverisini alamiyor: onu tumuyle dusur.
            trimmed = first_data + last_group * blocks_per_group
            if trimmed <= 64:
                raise ExtError(tr("Bolum ext icin cok kucuk"))
            total_blocks = trimmed

        total_inodes = inodes_per_group * group_count
        reserved = int(total_blocks * self.reserved_percent / 100.0)

        return ExtLayout(block_size=bs, block_count=total_blocks,
                         inode_count=total_inodes,
                         blocks_per_group=blocks_per_group,
                         inodes_per_group=inodes_per_group,
                         inode_size=inode_size, group_count=group_count,
                         first_data_block=first_data, gdt_blocks=gdt_block,
                         reserved_blocks=reserved)

    # ---- ozellik bayraklari ----------------------------------------------
    def _features(self):
        # RESIZE_INODE acilmaz: bayrak, inode 7'de rezerve GDT bloklarini
        # gosteren bir yapi bekler. Onu kurmadan bayragi acmak `e2fsck`
        # tarafindan "resize inode not valid" olarak bildirilir.
        compat = COMPAT_DIR_INDEX | COMPAT_EXT_ATTR
        incompat = INCOMPAT_FILETYPE
        ro_compat = RO_SPARSE_SUPER | RO_LARGE_FILE
        if self.version in ("ext3", "ext4"):
            compat |= COMPAT_HAS_JOURNAL
        if self.version == "ext4":
            ro_compat |= RO_HUGE_FILE | RO_DIR_NLINK | RO_EXTRA_ISIZE
        if self.extents:
            incompat |= INCOMPAT_EXTENTS
        if self.flex_bg:
            incompat |= INCOMPAT_FLEX_BG
        if self.metadata_csum:
            ro_compat |= RO_METADATA_CSUM
        return compat, incompat, ro_compat

    # ---- yardimcilar ------------------------------------------------------
    def _write_block(self, block: int, veri: bytes) -> None:
        bs = self.layout.block_size
        if len(veri) < bs:
            veri = veri + b"\x00" * (bs - len(veri))
        self.dev.write(block * bs, veri[:bs])

    def _zero_blocks(self, first: int, adet: int) -> None:
        bs = self.layout.block_size
        chunk = max(1, (4 * 1024 * 1024) // bs)
        sifir = b"\x00" * (chunk * bs)
        kalan = adet
        cur = first
        while kalan > 0:
            n = min(chunk, kalan)
            self.dev.write(cur * bs, sifir[: n * bs])
            cur += n
            kalan -= n

    def _group_start(self, grup: int) -> int:
        L = self.layout
        return L.first_data_block + grup * L.blocks_per_group

    def _group_overhead(self, grup: int) -> int:
        """Grubun basindaki metaveri blok sayisi."""
        L = self.layout
        overhead = 0
        if _has_super(grup):
            overhead += 1 + L.gdt_blocks + self._reserved_gdt()
        overhead += 2 + L.inode_table_blocks      # blok bitmap + inode bitmap + tablo
        return overhead

    def _reserved_gdt(self) -> int:
        """Buyume icin ayrilan GDT bloklari.

        resize_inode ozelligi uygulanmadigi icin 0 dondurulur; aksi halde
        ayrilan bloklar hicbir inode tarafindan sahiplenilmemis olur.
        """
        return 0

    # ======================================================================
    # Bicimlendirme
    # ======================================================================
    def format(self, progress: Optional[Callable[[str, int], None]] = None) -> Dict:
        def report(message: str, percent: int) -> None:
            if progress:
                progress(message, percent)

        L = self.layout
        bs = L.block_size
        report(tr("{} yerlesimi hazirlaniyor...", self.version), 5)

        # --- blok tahsis defteri ---
        kullanilan: Dict[int, bool] = {}

        def mark(first: int, adet: int = 1) -> None:
            for b in range(first, first + adet):
                kullanilan[b] = True

        # ilk bloklar (blocksize=1024 ise blok 0 onyukleme icin)
        if L.first_data_block == 1:
            mark(0, 1)

        def free_run(goal: int, n: int) -> int:
            """goal'dan itibaren n ardisik bos blogun basi (yoksa -1).

            Engelle karsilasinca engelin arkasina atlanir; her blok en fazla
            bir kez taranir (eski dongu her adimda pencerenin tamamina
            bakiyordu, buyuk gunlukte saniyeler surerdi).
            """
            aday = max(goal, 0)
            while aday + n <= L.block_count:
                engel = next((b for b in range(aday, aday + n)
                              if kullanilan.get(b)), None)
                if engel is None:
                    return aday
                aday = engel + 1
            return -1

        def alloc(goal: int, n: int) -> int:
            first = free_run(goal, n)
            if first < 0:
                raise ExtError(tr("Bolum ext icin cok kucuk"))
            mark(first, n)
            return first

        # ustblok + GDT kopyalari once: diger metaveri bunlarin etrafina oturur
        for g in range(L.group_count):
            if _has_super(g):
                mark(self._group_start(g), 1 + L.gdt_blocks + self._reserved_gdt())

        grup_meta: List[dict] = []
        if self.flex_bg:
            # flex_bg (mkfs.ext4 duzeni): 16 grubun blok bitmap'leri, sonra
            # inode bitmap'leri, sonra inode tablolari, flex grubunun ilk
            # grubunda art arda durur. Diger gruplar tamamen veriye kalir.
            flex = 1 << LOG_GROUPS_PER_FLEX
            for g in range(L.group_count):
                grup_meta.append({"start": self._group_start(g)})
            for first_group in range(0, L.group_count, flex):
                gruplar = range(first_group, min(first_group + flex, L.group_count))
                imlec = self._group_start(first_group)
                for key, adet in (("block_bitmap", 1), ("inode_bitmap", 1),
                                  ("inode_table", L.inode_table_blocks)):
                    for g in gruplar:
                        grup_meta[g][key] = imlec = alloc(imlec, adet)
                        imlec += adet
        else:
            for g in range(L.group_count):
                bas = self._group_start(g)
                imlec = bas
                if _has_super(g):
                    imlec += 1 + L.gdt_blocks + self._reserved_gdt()
                block_bitmap = imlec
                inode_bitmap = imlec + 1
                inode_table = imlec + 2
                mark(block_bitmap, 2 + L.inode_table_blocks)
                grup_meta.append({
                    "start": bas, "block_bitmap": block_bitmap,
                    "inode_bitmap": inode_bitmap, "inode_table": inode_table,
                })
        if any(m["inode_table"] + L.inode_table_blocks > L.block_count
               for m in grup_meta):
            raise ExtError(tr("Bolum ext icin cok kucuk"))

        report(tr("Metaveri alanlari sifirlaniyor..."), 15)
        for g, meta in enumerate(grup_meta):
            self._zero_blocks(meta["inode_table"], L.inode_table_blocks)

        # --- kok dizin ve lost+found veri bloklari ---
        root_block = alloc(self._group_start(0), 1)
        lost_block = alloc(root_block + 1, 1)
        sonraki = lost_block + 1

        # --- gunluk (ext3/ext4) ---
        journal_blocks: List[int] = []
        journal_indirect = 0
        if self._journal_blocks:
            report(tr("Gunluk (journal) ayriliyor..."), 25)
            istenen = self._journal_blocks
            aday = -1
            while istenen >= 1024:
                if self.extents:
                    gerekli = istenen
                else:
                    # 12 dogrudan blogu asan kisim icin bir dolayli blok daha
                    gerekli = istenen + (1 if istenen > 12 else 0)
                aday = free_run(sonraki, gerekli)
                if aday >= 0:
                    break
                istenen //= 2          # yer yok: daha kucuk gunluk dene
            self._journal_blocks = istenen if aday >= 0 else 0
            if self._journal_blocks:
                mark(aday, gerekli)
                if self.extents or self._journal_blocks <= 12:
                    journal_blocks = list(range(aday, aday + gerekli))
                else:
                    # ilk 12 dogrudan, sonra dolayli tablo blogu, sonra kalanlar
                    journal_blocks = list(range(aday, aday + 12))
                    journal_indirect = aday + 12
                    journal_blocks += list(range(aday + 13, aday + gerekli))

        if journal_blocks:
            report(tr("Gunluk alani hazirlaniyor..."), 35)
            # Once TUM gunluk araligi sifirlanir, superblok yazilir; dolayli
            # tablo blogu bu araligin ortasindadir ve inode yaziminda
            # doldurulacaktir. Sira ters olursa tablo silinir ve e2fsck
            # "gecersiz gunluk" der.
            self._write_journal(journal_blocks, journal_indirect)

        report(tr("Inode tablosu yaziliyor..."), 40)
        self._write_root_inode(grup_meta, root_block, lost_block, journal_blocks,
                               journal_indirect)

        report(tr("Dizin bloklari yaziliyor..."), 55)
        self._write_root_dir(root_block, lost_block)
        self._write_lost_found(lost_block)

        report(tr("Bitmapler yaziliyor..."), 75)
        group_free_blocks, total_free_blocks = self._write_bitmaps(grup_meta, kullanilan)

        report(tr("Superbloklar yaziliyor..."), 90)
        self._write_superblocks(grup_meta, group_free_blocks, total_free_blocks,
                                bool(journal_blocks))

        f = getattr(self.dev, "flush", None)
        if f:
            f()
        report(tr("Tamamlandi"), 100)
        return {
            "version": self.version, "block_size": bs,
            "blocks": L.block_count, "inodes": L.inode_count,
            "groups": L.group_count, "journal_blocks": len(journal_blocks),
            "uuid": str(self.uuid), "label": self.label,
        }

    # ---- inode yazma ------------------------------------------------------
    def _inode_offset(self, grup_meta: List[dict], ino: int) -> int:
        L = self.layout
        grup = (ino - 1) // L.inodes_per_group
        indeks = (ino - 1) % L.inodes_per_group
        return (grup_meta[grup]["inode_table"] * L.block_size
                + indeks * L.inode_size)

    def _pack_inode(self, mode: int, size: int, links: int,
                    bloklar: List[int], flags: int = 0) -> bytes:
        L = self.layout
        inode = bytearray(L.inode_size)
        struct.pack_into("<HHIIIIIHHII", inode, 0,
                         mode, 0, size & 0xFFFFFFFF,
                         self.now, self.now, self.now, 0,
                         0, links,
                         (len(bloklar) * L.block_size) // 512, flags)
        if self.extents:
            self._pack_extents(inode, bloklar)
            struct.pack_into("<I", inode, 0x20, flags | EXTENTS_FL)
        else:
            for i, b in enumerate(bloklar[:12]):
                struct.pack_into("<I", inode, 0x28 + i * 4, b)
        if L.inode_size > 128:
            struct.pack_into("<H", inode, 0x80, 32)      # i_extra_isize
            struct.pack_into("<I", inode, 0x90, self.now)  # i_crtime
        return bytes(inode)

    @staticmethod
    def _pack_extents(inode: bytearray, bloklar: List[int]) -> None:
        """i_block'a derinlik 0 extent agaci yazar (en cok 4 extent)."""
        runs: List[List[int]] = []          # [logical, length, physical]
        for i, b in enumerate(bloklar):
            if runs and runs[-1][2] + runs[-1][1] == b \
                    and runs[-1][1] < EXTENT_MAX_LEN:
                runs[-1][1] += 1
            else:
                runs.append([i, 1, b])
        if len(runs) > INODE_EXTENTS:
            raise ExtError(tr("Gunluk tek parca ayrilamadi"))
        struct.pack_into("<HHHHI", inode, 0x28, EXT4_EXT_MAGIC, len(runs),
                         INODE_EXTENTS, 0, 0)
        for i, (logical, length, physical) in enumerate(runs):
            struct.pack_into("<IHHI", inode, 0x28 + 12 + i * 12, logical,
                             length, physical >> 32, physical & 0xFFFFFFFF)

    def _write_inode(self, grup_meta: List[dict], ino: int, raw: bytes) -> None:
        if self._csum:
            raw = bytearray(raw)
            lo, hi = self._csum.inode(ino, raw)
            struct.pack_into("<H", raw, 0x7C, lo)
            if hi >= 0:
                struct.pack_into("<H", raw, 0x82, hi)
        self.dev.write(self._inode_offset(grup_meta, ino), bytes(raw))

    def _write_root_inode(self, grup_meta: List[dict], root_block: int,
                          lost_block: int, journal: List[int],
                          journal_indirect: int = 0) -> None:
        L = self.layout
        bs = L.block_size

        # kok dizin (inode 2): "." ".." "lost+found" -> 3 baglanti
        root = self._pack_inode(S_IFDIR | 0o755, bs, 3, [root_block])
        self._write_inode(grup_meta, INO_ROOT, root)

        # lost+found (inode 11)
        lost = self._pack_inode(S_IFDIR | 0o700, bs, 2, [lost_block])
        self._write_inode(grup_meta, INO_FIRST, lost)

        # gunluk inode'u (8): 12 dogrudan blok + tek dolayli tablo
        if journal and self.extents:
            j = self._pack_inode(S_IFREG | 0o600, len(journal) * bs, 1, journal)
            self._write_inode(grup_meta, INO_JOURNAL, j)
        elif journal:
            size = len(journal) * bs
            j = bytearray(self._pack_inode(S_IFREG | 0o600, size, 1, journal[:12]))
            total_blocks = len(journal) + (1 if journal_indirect else 0)
            struct.pack_into("<I", j, 0x1C, (total_blocks * bs) // 512)
            if journal_indirect:
                kalan = journal[12:]
                if len(kalan) > bs // 4:
                    raise ExtError(tr("Gunluk tek dolayli blogun kapasitesini asiyor"))
                table = bytearray(bs)
                for i, b in enumerate(kalan):
                    struct.pack_into("<I", table, i * 4, b)
                self._write_block(journal_indirect, bytes(table))
                struct.pack_into("<I", j, 0x28 + 12 * 4, journal_indirect)
            self._write_inode(grup_meta, INO_JOURNAL, bytes(j))

    # ---- dizin bloklari ---------------------------------------------------
    @staticmethod
    def _dir_entry(ino: int, name: str, kind: int, rec_len: int) -> bytes:
        ham = name.encode("utf-8")
        entry = bytearray(rec_len)
        struct.pack_into("<IHBB", entry, 0, ino, rec_len, len(ham), kind)
        entry[8:8 + len(ham)] = ham
        return bytes(entry)

    def _dir_space(self) -> int:
        """Girislere kalan alan: metadata_csum'da son 12 bayt saglama kuyrugu."""
        return self.layout.block_size - (12 if self._csum else 0)

    def _write_dir_block(self, dir_ino: int, block: int, veri: bytearray) -> None:
        if self._csum:
            tail = bytearray(12)
            struct.pack_into("<IHBB", tail, 0, 0, 12, 0, 0xDE)
            veri += tail
            struct.pack_into("<I", veri, len(veri) - 4,
                             self._csum.dir_block(dir_ino, 0, bytes(veri)))
        self._write_block(block, bytes(veri))

    def _write_root_dir(self, block: int, lost_block: int) -> None:
        veri = bytearray()
        veri += self._dir_entry(INO_ROOT, ".", FT_DIR, 12)
        veri += self._dir_entry(INO_ROOT, "..", FT_DIR, 12)
        kalan = self._dir_space() - len(veri)
        veri += self._dir_entry(INO_FIRST, "lost+found", FT_DIR, kalan)
        self._write_dir_block(INO_ROOT, block, veri)

    def _write_lost_found(self, block: int) -> None:
        veri = bytearray()
        veri += self._dir_entry(INO_FIRST, ".", FT_DIR, 12)
        veri += self._dir_entry(INO_ROOT, "..", FT_DIR, self._dir_space() - 12)
        self._write_dir_block(INO_FIRST, block, veri)

    # ---- gunluk -----------------------------------------------------------
    def _write_journal(self, bloklar: List[int], dolayli: int = 0) -> None:
        """Gunluk alanini sifirlar ve JBD2 superblogunu (v2) yazar.

        Gunluk bos baslar: `s_start = 0`, `s_sequence = 1`.
        """
        bs = self.layout.block_size
        sb = bytearray(bs)
        # journal_header_t: magic, blocktype(4=superblock v2), sequence
        struct.pack_into(">III", sb, 0, JBD2_MAGIC, 4, 0)
        struct.pack_into(">III", sb, 12, bs, len(bloklar), 1)   # blocksize, maxlen, first
        struct.pack_into(">II", sb, 24, 1, 0)                    # sequence, start
        struct.pack_into(">I", sb, 32, 0)                        # errno
        sb[48:64] = self.uuid.bytes                              # j_uuid
        struct.pack_into(">I", sb, 64, 1)                        # nr_users
        # once tum aralik sifirlanir (dolayli tablo blogu dahil), sonra superblok
        tum = sorted(bloklar + ([dolayli] if dolayli else []))
        if tum:
            self._zero_blocks(tum[0], tum[-1] - tum[0] + 1)
        self._write_block(bloklar[0], bytes(sb))

    # ---- bitmapler --------------------------------------------------------
    def _write_bitmaps(self, grup_meta: List[dict],
                       kullanilan: Dict[int, bool]) -> tuple:
        L = self.layout
        bs = L.block_size
        group_free: List[int] = []
        total_free = 0
        self._bitmap_csums: List[tuple] = []

        for g, meta in enumerate(grup_meta):
            bas = self._group_start(g)
            last = min(bas + L.blocks_per_group, L.block_count)
            adet = last - bas
            bitmap = bytearray(bs)
            free = 0
            for i in range(adet):
                block = bas + i
                if kullanilan.get(block):
                    bitmap[i >> 3] |= 1 << (i & 7)
                else:
                    free += 1
            # gruptaki kullanilmayan bit alani dolu isaretlenir
            for i in range(adet, bs * 8):
                bitmap[i >> 3] |= 1 << (i & 7)
            self._write_block(meta["block_bitmap"], bytes(bitmap))
            group_free.append(free)
            total_free += free

            # inode bitmap: ilk grupta rezerve inode'lar dolu
            ibitmap = bytearray(bs)
            if g == 0:
                for i in range(INO_FIRST):        # 1..11 arasi rezerve + lost+found
                    ibitmap[i >> 3] |= 1 << (i & 7)
            for i in range(L.inodes_per_group, bs * 8):
                ibitmap[i >> 3] |= 1 << (i & 7)
            self._write_block(meta["inode_bitmap"], bytes(ibitmap))
            if self._csum:
                # saglama bitmap'in anlamli kismini kapsar: blok bitmap'i
                # blocks_per_group/8, inode bitmap'i inodes_per_group/8 bayt
                self._bitmap_csums.append((
                    self._csum.bitmap(bytes(bitmap[:L.blocks_per_group // 8])),
                    self._csum.bitmap(bytes(ibitmap[:(L.inodes_per_group + 7) // 8]))))

        return group_free, total_free

    # ---- superblok ve GDT -------------------------------------------------
    def _pack_superblock(self, grup_no: int, free_blocks: int,
                         free_inodes: int, has_journal: bool) -> bytes:
        L = self.layout
        compat, incompat, ro_compat = self._features()
        sb = bytearray(1024)
        struct.pack_into("<IIIIIIIIIIII", sb, 0,
                         L.inode_count, L.block_count, L.reserved_blocks,
                         free_blocks, free_inodes, L.first_data_block,
                         {1024: 0, 2048: 1, 4096: 2}[L.block_size],
                         {1024: 0, 2048: 1, 4096: 2}[L.block_size],
                         L.blocks_per_group, L.blocks_per_group,
                         L.inodes_per_group, 0)
        struct.pack_into("<I", sb, 0x30, self.now)        # s_wtime
        struct.pack_into("<HH", sb, 0x34, 0, 0xFFFF)      # mnt_count, max_mnt_count
        struct.pack_into("<HHH", sb, 0x38, EXT_MAGIC, 1, 1)   # magic, state, errors
        struct.pack_into("<H", sb, 0x3E, 0)               # minor_rev
        struct.pack_into("<II", sb, 0x40, self.now, 0)    # lastcheck, checkinterval
        struct.pack_into("<II", sb, 0x48, 0, 1)           # creator_os=Linux, rev=dynamic
        struct.pack_into("<HH", sb, 0x50, 0, 0)           # resuid, resgid
        struct.pack_into("<I", sb, 0x54, INO_FIRST)       # first_ino
        struct.pack_into("<HH", sb, 0x58, L.inode_size, grup_no)
        struct.pack_into("<III", sb, 0x5C, compat, incompat, ro_compat)
        sb[0x68:0x78] = self.uuid.bytes
        name = self.label.encode("utf-8")[:16]
        sb[0x78:0x78 + len(name)] = name
        struct.pack_into("<I", sb, 0xCC, 0)               # prealloc
        struct.pack_into("<H", sb, 0xCE, self._reserved_gdt())
        if has_journal:
            # s_journal_uuid (0xD0) BOS birakilir: dolu olmasi "harici gunluk"
            # anlamina gelir ve e2fsck "Dis gunluk bulunamiyor" hatasi verir.
            # Ic gunluk icin yalnizca s_journal_inum yazilir.
            struct.pack_into("<I", sb, 0xE0, INO_JOURNAL)
        struct.pack_into("<I", sb, 0xFC, 1)               # def_hash_version = half_md4
        struct.pack_into("<H", sb, 0x15C, 32)             # s_min_extra_isize
        struct.pack_into("<H", sb, 0x15E, 32)             # s_want_extra_isize
        if self.flex_bg:
            sb[0x174] = LOG_GROUPS_PER_FLEX               # s_log_groups_per_flex
        if self._csum:
            sb[0x175] = 1                                 # s_checksum_type = crc32c
            struct.pack_into("<I", sb, 0x3FC, self._csum.superblock(sb))
        return bytes(sb)

    def _pack_gdt(self, grup_meta: List[dict], group_free_blocks: List[int]) -> bytes:
        L = self.layout
        table = bytearray(L.gdt_blocks * L.block_size)
        for g, meta in enumerate(grup_meta):
            off = g * 32
            free_inodes = L.inodes_per_group - (INO_FIRST if g == 0 else 0)
            used_dirs = 2 if g == 0 else 0     # kok + lost+found
            struct.pack_into("<IIIHHH", table, off,
                             meta["block_bitmap"], meta["inode_bitmap"],
                             meta["inode_table"],
                             min(group_free_blocks[g], 0xFFFF),
                             min(free_inodes, 0xFFFF), used_dirs)
            if self._csum:
                # Inode tablolari sifirlandi (ITABLE_ZEROED); kullanilmamis
                # kuyruk itable_unused ile bildirilir — mkfs.ext4 ile ayni.
                # Yazici yeni inode alirken bu sayiyi gunceller (extwrite).
                bb_csum, ib_csum = self._bitmap_csums[g]
                struct.pack_into("<HHHH", table, off + 0x12, BG_ITABLE_ZEROED,
                                 0, 0, 0)
                struct.pack_into("<HHH", table, off + 0x18, bb_csum & 0xFFFF,
                                 ib_csum & 0xFFFF, free_inodes)
                struct.pack_into("<H", table, off + 0x1E,
                                 self._csum.group_desc(g, table[off:off + 32]))
        return bytes(table)

    def _write_superblocks(self, grup_meta: List[dict], group_free_blocks: List[int],
                           total_free_blocks: int, has_journal: bool) -> None:
        L = self.layout
        free_inodes = L.inode_count - INO_FIRST
        gdt = self._pack_gdt(grup_meta, group_free_blocks)

        for g in range(L.group_count):
            if not _has_super(g):
                continue
            sb = self._pack_superblock(g, total_free_blocks, free_inodes, has_journal)
            if g == 0:
                self.dev.write(1024, sb)
                gdt_block = L.first_data_block + 1
            else:
                taban = self._group_start(g)
                self._write_block(taban, sb if L.block_size > 1024
                                  else sb[:L.block_size])
                if L.block_size > 1024:
                    # superblok blogun basinda; 1024 bayttan sonrasi bos
                    pass
                gdt_block = taban + 1
            self.dev.write(gdt_block * L.block_size, gdt)


def format_ext(dev: BlockDevice, version: str = "ext4", label: str = "",
               block_size: int = 0,
               progress: Optional[Callable[[str, int], None]] = None,
               modern: bool = True) -> Dict:
    """Kisayol: verilen aygiti ext2/3/4 olarak bicimlendirir."""
    return ExtFormatter(dev, version=version, label=label,
                        block_size=block_size,
                        modern=modern).format(progress=progress)
