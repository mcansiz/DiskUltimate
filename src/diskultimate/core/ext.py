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
    ext4 = ext3 + extent / 64bit olmayan modern bayraklar
"""
from __future__ import annotations

import struct
import time
import uuid as _uuid
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .image import BlockDevice

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
        toplam = self.inodes_per_group * self.inode_size
        return (toplam + self.block_size - 1) // self.block_size


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
                 bytes_per_inode: int = 16384, reserved_percent: float = 5.0):
        if version not in ("ext2", "ext3", "ext4"):
            raise ExtError(f"Bilinmeyen surum: {version}")
        self.dev = dev
        self.version = version
        self.label = label[:16]
        self.bytes_per_inode = max(1024, bytes_per_inode)
        self.reserved_percent = max(0.0, min(50.0, reserved_percent))
        self.block_size = block_size or self._default_block_size()
        if self.block_size not in (1024, 2048, 4096):
            raise ExtError("Blok boyutu 1024, 2048 veya 4096 olmalidir")
        self.uuid = _uuid.uuid4()
        self.now = int(time.time())
        self.layout = self._compute_layout()
        self._journal_blocks = self._journal_size() if version != "ext2" else 0

    # ---- yerlesim hesabi --------------------------------------------------
    def _default_block_size(self) -> int:
        boyut = self.dev.size
        if boyut < 3 * 1024 * 1024:
            return 1024
        if boyut < 512 * 1024 * 1024:
            return 1024 if boyut < 16 * 1024 * 1024 else 4096
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
        azami = 12 + self.layout.block_size // 4
        istenen = 1024 if bloklar < 32768 else 4096
        return min(istenen, azami)

    def _compute_layout(self) -> ExtLayout:
        bs = self.block_size
        toplam_blok = self.dev.size // bs
        if toplam_blok < 64:
            raise ExtError("Bolum ext icin cok kucuk")
        ilk_veri = 1 if bs == 1024 else 0
        blok_grup_basina = bs * 8              # blok bitmap tek blok
        kullanilabilir = toplam_blok - ilk_veri
        grup_sayisi = max(1, (kullanilabilir + blok_grup_basina - 1) // blok_grup_basina)

        inode_boyutu = 128 if self.version == "ext2" else 256
        toplam_inode = max(16, (self.dev.size // self.bytes_per_inode))
        inode_grup_basina = (toplam_inode + grup_sayisi - 1) // grup_sayisi
        # inode tablosu blok sinirina hizalanmali ve 8'in kati olmali
        inode_grup_basina = max(16, ((inode_grup_basina + 7) // 8) * 8)
        azami = (bs * 8)                       # inode bitmap tek blok
        inode_grup_basina = min(inode_grup_basina, azami)
        toplam_inode = inode_grup_basina * grup_sayisi

        gdt_bayt = grup_sayisi * 32
        gdt_blok = (gdt_bayt + bs - 1) // bs
        rezerve = int(toplam_blok * self.reserved_percent / 100.0)

        return ExtLayout(block_size=bs, block_count=toplam_blok,
                         inode_count=toplam_inode,
                         blocks_per_group=blok_grup_basina,
                         inodes_per_group=inode_grup_basina,
                         inode_size=inode_boyutu, group_count=grup_sayisi,
                         first_data_block=ilk_veri, gdt_blocks=gdt_blok,
                         reserved_blocks=rezerve)

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
            # extent bayragi ACILMAZ: bu surumde inode'lar dogrudan blok
            # eslemesi kullanir; extent bayragi acilirsa cekirdek yeni
            # inode'larda extent bekler ve tutarsizlik dogar.
            ro_compat |= RO_HUGE_FILE | RO_DIR_NLINK | RO_EXTRA_ISIZE
        return compat, incompat, ro_compat

    # ---- yardimcilar ------------------------------------------------------
    def _write_block(self, blok: int, veri: bytes) -> None:
        bs = self.layout.block_size
        if len(veri) < bs:
            veri = veri + b"\x00" * (bs - len(veri))
        self.dev.write(blok * bs, veri[:bs])

    def _zero_blocks(self, ilk: int, adet: int) -> None:
        bs = self.layout.block_size
        parca = max(1, (4 * 1024 * 1024) // bs)
        sifir = b"\x00" * (parca * bs)
        kalan = adet
        cur = ilk
        while kalan > 0:
            n = min(parca, kalan)
            self.dev.write(cur * bs, sifir[: n * bs])
            cur += n
            kalan -= n

    def _group_start(self, grup: int) -> int:
        L = self.layout
        return L.first_data_block + grup * L.blocks_per_group

    def _group_overhead(self, grup: int) -> int:
        """Grubun basindaki metaveri blok sayisi."""
        L = self.layout
        ek = 0
        if _has_super(grup):
            ek += 1 + L.gdt_blocks + self._reserved_gdt()
        ek += 2 + L.inode_table_blocks      # blok bitmap + inode bitmap + tablo
        return ek

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
        def bildir(mesaj: str, yuzde: int) -> None:
            if progress:
                progress(mesaj, yuzde)

        L = self.layout
        bs = L.block_size
        bildir(f"{self.version} yerlesimi hazirlaniyor...", 5)

        # --- blok tahsis defteri ---
        kullanilan: Dict[int, bool] = {}

        def isaretle(ilk: int, adet: int = 1) -> None:
            for b in range(ilk, ilk + adet):
                kullanilan[b] = True

        # ilk bloklar (blocksize=1024 ise blok 0 onyukleme icin)
        if L.first_data_block == 1:
            isaretle(0, 1)

        grup_meta: List[dict] = []
        for g in range(L.group_count):
            bas = self._group_start(g)
            imlec = bas
            if _has_super(g):
                isaretle(imlec, 1 + L.gdt_blocks + self._reserved_gdt())
                imlec += 1 + L.gdt_blocks + self._reserved_gdt()
            blok_bitmap = imlec
            inode_bitmap = imlec + 1
            inode_tablo = imlec + 2
            isaretle(blok_bitmap, 2 + L.inode_table_blocks)
            grup_meta.append({
                "start": bas, "block_bitmap": blok_bitmap,
                "inode_bitmap": inode_bitmap, "inode_table": inode_tablo,
                "data_start": inode_tablo + L.inode_table_blocks,
            })

        bildir("Metaveri alanlari sifirlaniyor...", 15)
        for g, meta in enumerate(grup_meta):
            self._zero_blocks(meta["inode_table"], L.inode_table_blocks)

        # --- kok dizin ve lost+found veri bloklari ---
        veri_imlec = grup_meta[0]["data_start"]
        kok_blok = veri_imlec
        isaretle(kok_blok, 1)
        lost_blok = veri_imlec + 1
        isaretle(lost_blok, 1)
        sonraki = veri_imlec + 2

        # --- gunluk (ext3/ext4) ---
        gunluk_bloklari: List[int] = []
        gunluk_dolayli = 0
        if self._journal_blocks:
            bildir("Gunluk (journal) ayriliyor...", 25)
            # 12 dogrudan blogu asan kisim icin bir dolayli blok daha gerekir
            gerekli = self._journal_blocks + (1 if self._journal_blocks > 12 else 0)
            # ardisik yer bul
            aday = sonraki
            while aday + gerekli < L.block_count:
                if not any(kullanilan.get(b) for b in range(aday, aday + gerekli)):
                    break
                aday += 1
            if aday + gerekli >= L.block_count:
                self._journal_blocks = 0        # yer yok: gunluksuz olustur
            else:
                isaretle(aday, gerekli)
                if self._journal_blocks > 12:
                    # ilk 12 dogrudan, sonra dolayli tablo blogu, sonra kalanlar
                    gunluk_bloklari = list(range(aday, aday + 12))
                    gunluk_dolayli = aday + 12
                    gunluk_bloklari += list(range(aday + 13, aday + gerekli))
                else:
                    gunluk_bloklari = list(range(aday, aday + gerekli))

        if gunluk_bloklari:
            bildir("Gunluk alani hazirlaniyor...", 35)
            # Once TUM gunluk araligi sifirlanir, superblok yazilir; dolayli
            # tablo blogu bu araligin ortasindadir ve inode yaziminda
            # doldurulacaktir. Sira ters olursa tablo silinir ve e2fsck
            # "gecersiz gunluk" der.
            self._write_journal(gunluk_bloklari, gunluk_dolayli)

        bildir("Inode tablosu yaziliyor...", 40)
        self._write_root_inode(grup_meta, kok_blok, lost_blok, gunluk_bloklari,
                               gunluk_dolayli)

        bildir("Dizin bloklari yaziliyor...", 55)
        self._write_root_dir(kok_blok, lost_blok)
        self._write_lost_found(lost_blok)

        bildir("Bitmapler yaziliyor...", 75)
        grup_bos_blok, toplam_bos_blok = self._write_bitmaps(grup_meta, kullanilan)

        bildir("Superbloklar yaziliyor...", 90)
        self._write_superblocks(grup_meta, grup_bos_blok, toplam_bos_blok,
                                bool(gunluk_bloklari))

        f = getattr(self.dev, "flush", None)
        if f:
            f()
        bildir("Tamamlandi", 100)
        return {
            "version": self.version, "block_size": bs,
            "blocks": L.block_count, "inodes": L.inode_count,
            "groups": L.group_count, "journal_blocks": len(gunluk_bloklari),
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
        for i, b in enumerate(bloklar[:12]):
            struct.pack_into("<I", inode, 0x28 + i * 4, b)
        if L.inode_size > 128:
            struct.pack_into("<H", inode, 0x80, 32)      # i_extra_isize
        return bytes(inode)

    def _write_root_inode(self, grup_meta: List[dict], kok_blok: int,
                          lost_blok: int, gunluk: List[int],
                          gunluk_dolayli: int = 0) -> None:
        L = self.layout
        bs = L.block_size

        # kok dizin (inode 2): "." ".." "lost+found" -> 3 baglanti
        kok = self._pack_inode(S_IFDIR | 0o755, bs, 3, [kok_blok])
        self.dev.write(self._inode_offset(grup_meta, INO_ROOT), kok)

        # lost+found (inode 11)
        lost = self._pack_inode(S_IFDIR | 0o700, bs, 2, [lost_blok])
        self.dev.write(self._inode_offset(grup_meta, INO_FIRST), lost)

        # gunluk inode'u (8): 12 dogrudan blok + tek dolayli tablo
        if gunluk:
            boyut = len(gunluk) * bs
            j = bytearray(self._pack_inode(S_IFREG | 0o600, boyut, 1, gunluk[:12]))
            toplam_blok = len(gunluk) + (1 if gunluk_dolayli else 0)
            struct.pack_into("<I", j, 0x1C, (toplam_blok * bs) // 512)
            if gunluk_dolayli:
                kalan = gunluk[12:]
                if len(kalan) > bs // 4:
                    raise ExtError("Gunluk tek dolayli blogun kapasitesini asiyor")
                tablo = bytearray(bs)
                for i, b in enumerate(kalan):
                    struct.pack_into("<I", tablo, i * 4, b)
                self._write_block(gunluk_dolayli, bytes(tablo))
                struct.pack_into("<I", j, 0x28 + 12 * 4, gunluk_dolayli)
            self.dev.write(self._inode_offset(grup_meta, INO_JOURNAL), bytes(j))

    # ---- dizin bloklari ---------------------------------------------------
    @staticmethod
    def _dir_entry(ino: int, ad: str, tur: int, rec_len: int) -> bytes:
        ham = ad.encode("utf-8")
        giris = bytearray(rec_len)
        struct.pack_into("<IHBB", giris, 0, ino, rec_len, len(ham), tur)
        giris[8:8 + len(ham)] = ham
        return bytes(giris)

    def _write_root_dir(self, blok: int, lost_blok: int) -> None:
        bs = self.layout.block_size
        veri = bytearray()
        veri += self._dir_entry(INO_ROOT, ".", FT_DIR, 12)
        veri += self._dir_entry(INO_ROOT, "..", FT_DIR, 12)
        kalan = bs - len(veri)
        veri += self._dir_entry(INO_FIRST, "lost+found", FT_DIR, kalan)
        self._write_block(blok, bytes(veri))

    def _write_lost_found(self, blok: int) -> None:
        bs = self.layout.block_size
        veri = bytearray()
        veri += self._dir_entry(INO_FIRST, ".", FT_DIR, 12)
        veri += self._dir_entry(INO_ROOT, "..", FT_DIR, bs - 12)
        self._write_block(blok, bytes(veri))

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
        grup_bos: List[int] = []
        toplam_bos = 0

        for g, meta in enumerate(grup_meta):
            bas = self._group_start(g)
            son = min(bas + L.blocks_per_group, L.block_count)
            adet = son - bas
            bitmap = bytearray(bs)
            bos = 0
            for i in range(adet):
                blok = bas + i
                if kullanilan.get(blok):
                    bitmap[i >> 3] |= 1 << (i & 7)
                else:
                    bos += 1
            # gruptaki kullanilmayan bit alani dolu isaretlenir
            for i in range(adet, bs * 8):
                bitmap[i >> 3] |= 1 << (i & 7)
            self._write_block(meta["block_bitmap"], bytes(bitmap))
            grup_bos.append(bos)
            toplam_bos += bos

            # inode bitmap: ilk grupta rezerve inode'lar dolu
            ibitmap = bytearray(bs)
            if g == 0:
                for i in range(INO_FIRST):        # 1..11 arasi rezerve + lost+found
                    ibitmap[i >> 3] |= 1 << (i & 7)
            for i in range(L.inodes_per_group, bs * 8):
                ibitmap[i >> 3] |= 1 << (i & 7)
            self._write_block(meta["inode_bitmap"], bytes(ibitmap))

        return grup_bos, toplam_bos

    # ---- superblok ve GDT -------------------------------------------------
    def _pack_superblock(self, grup_no: int, bos_blok: int,
                         bos_inode: int, gunluk_var: bool) -> bytes:
        L = self.layout
        compat, incompat, ro_compat = self._features()
        sb = bytearray(1024)
        struct.pack_into("<IIIIIIIIIIII", sb, 0,
                         L.inode_count, L.block_count, L.reserved_blocks,
                         bos_blok, bos_inode, L.first_data_block,
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
        ad = self.label.encode("utf-8")[:16]
        sb[0x78:0x78 + len(ad)] = ad
        struct.pack_into("<I", sb, 0xCC, 0)               # prealloc
        struct.pack_into("<H", sb, 0xCE, self._reserved_gdt())
        if gunluk_var:
            # s_journal_uuid (0xD0) BOS birakilir: dolu olmasi "harici gunluk"
            # anlamina gelir ve e2fsck "Dis gunluk bulunamiyor" hatasi verir.
            # Ic gunluk icin yalnizca s_journal_inum yazilir.
            struct.pack_into("<I", sb, 0xE0, INO_JOURNAL)
        struct.pack_into("<I", sb, 0xFC, 1)               # def_hash_version = half_md4
        struct.pack_into("<H", sb, 0x15C, 32)             # s_min_extra_isize
        struct.pack_into("<H", sb, 0x15E, 32)             # s_want_extra_isize
        return bytes(sb)

    def _pack_gdt(self, grup_meta: List[dict], grup_bos_blok: List[int]) -> bytes:
        L = self.layout
        tablo = bytearray(L.gdt_blocks * L.block_size)
        for g, meta in enumerate(grup_meta):
            off = g * 32
            bos_inode = L.inodes_per_group - (INO_FIRST if g == 0 else 0)
            kullanilan_dizin = 2 if g == 0 else 0     # kok + lost+found
            struct.pack_into("<IIIHHH", tablo, off,
                             meta["block_bitmap"], meta["inode_bitmap"],
                             meta["inode_table"],
                             min(grup_bos_blok[g], 0xFFFF),
                             min(bos_inode, 0xFFFF), kullanilan_dizin)
        return bytes(tablo)

    def _write_superblocks(self, grup_meta: List[dict], grup_bos_blok: List[int],
                           toplam_bos_blok: int, gunluk_var: bool) -> None:
        L = self.layout
        bos_inode = L.inode_count - INO_FIRST
        gdt = self._pack_gdt(grup_meta, grup_bos_blok)

        for g in range(L.group_count):
            if not _has_super(g):
                continue
            sb = self._pack_superblock(g, toplam_bos_blok, bos_inode, gunluk_var)
            if g == 0:
                self.dev.write(1024, sb)
                gdt_blok = L.first_data_block + 1
            else:
                taban = self._group_start(g)
                self._write_block(taban, sb if L.block_size > 1024
                                  else sb[:L.block_size])
                if L.block_size > 1024:
                    # superblok blogun basinda; 1024 bayttan sonrasi bos
                    pass
                gdt_blok = taban + 1
            self.dev.write(gdt_blok * L.block_size, gdt)


def format_ext(dev: BlockDevice, version: str = "ext4", label: str = "",
               block_size: int = 0,
               progress: Optional[Callable[[str, int], None]] = None) -> Dict:
    """Kisayol: verilen aygiti ext2/3/4 olarak bicimlendirir."""
    return ExtFormatter(dev, version=version, label=label,
                        block_size=block_size).format(progress=progress)
