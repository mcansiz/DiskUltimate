"""ext4 `metadata_csum` saglamalari.

`metadata_csum` acikken her metaveri yapisi bir CRC-32C tasir. Yazan taraf
bunlarin **tamamini** dogru guncellemek zorundadir; biri yanlis olursa `e2fsck`
birimi bozuk sayar. Bu modul saglamalari hem **hesaplar** hem **dogrular**.

Dogrulama yonu onemlidir: yazmaya baslamadan once, var olan saglam bir birim
uzerinde hesaplarimizin tuttugunu gosterebiliriz. `verify_volume()` tam da bunu
yapar ve hicbir sey yazmaz.

Saglama zinciri (cekirdekteki `ext4_chksum` ile ayni):

    tohum = crc32c(~0, s_uuid)            # veya csum_seed ozelligi varsa s_checksum_seed
    grup  = crc32c(crc32c(tohum, grup_no), tanimlayici[csum alani sifirlanmis])
    inode = crc32c(crc32c(crc32c(tohum, inode_no), i_generation), kayit[...])

`crc32c(crc, veri)` burada **ham** surumdur: basta/sonda ters cevirme yoktur,
cunku ara degerler zincirlenir.
"""
from __future__ import annotations

import struct
from typing import Dict, List, Tuple

from .crc32c import TABLE

INCOMPAT_CSUM_SEED = 0x2000
RO_METADATA_CSUM = 0x0400
RO_GDT_CSUM = 0x0010            # eski "uninit_bg" — farkli (crc16) algoritma

SB_CHECKSUM_OFFSET = 0x3FC
GD_CHECKSUM_OFFSET = 0x1E
INODE_CSUM_LO = 0x7C
INODE_CSUM_HI = 0x82
GOOD_OLD_INODE_SIZE = 128
DIRENT_TAIL_SIZE = 12
DIRENT_TAIL_FT = 0xDE


def raw_crc32c(crc: int, data: bytes) -> int:
    """Ters cevirmesiz CRC-32C adimi — cekirdekteki `crc32c()` ile ayni."""
    for b in data:
        crc = TABLE[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return crc & 0xFFFFFFFF


class ExtChecksums:
    """Bir ext birimi icin saglama hesaplari."""

    def __init__(self, fs):
        self.fs = fs
        self.enabled = bool(fs.ro_compat & RO_METADATA_CSUM)
        self.seed = self._compute_seed()

    @classmethod
    def for_new(cls, uuid: bytes, inode_size: int) -> "ExtChecksums":
        """Henuz diske yazilmamis bir birim icin (bicimlendirici).

        Normal kurucu tohumu diskteki ustbloktan okur; bicimlendirme sirasinda
        ustblok en son yazildigi icin tohum dogrudan UUID'den hesaplanir.
        """
        class _Geometry:
            pass
        fs = _Geometry()
        fs.inode_size = inode_size
        fs.ro_compat = RO_METADATA_CSUM
        fs.feature_incompat = 0
        cs = cls.__new__(cls)
        cs.fs = fs
        cs.enabled = True
        cs.seed = raw_crc32c(0xFFFFFFFF, bytes(uuid))
        return cs

    def _compute_seed(self) -> int:
        sb = self.fs.dev.read(1024, 1024)
        if self.fs.feature_incompat & INCOMPAT_CSUM_SEED:
            # Tohum dogrudan ustblokta saklanir (UUID degistirilebilsin diye)
            return struct.unpack_from("<I", sb, 0x270)[0]
        uuid = sb[0x68:0x78]
        return raw_crc32c(0xFFFFFFFF, uuid)

    # ------------------------------------------------------------------
    # Ustblok
    # ------------------------------------------------------------------
    def superblock(self, sb: bytes) -> int:
        """Ustblok saglamasi: bastan `s_checksum` alanina kadar."""
        return raw_crc32c(0xFFFFFFFF, bytes(sb[:SB_CHECKSUM_OFFSET]))

    # ------------------------------------------------------------------
    # Grup tanimlayicisi
    # ------------------------------------------------------------------
    def group_desc(self, group: int, desc: bytes) -> int:
        """Grup tanimlayici saglamasi (16 bit)."""
        crc = raw_crc32c(self.seed, struct.pack("<I", group))
        crc = raw_crc32c(crc, bytes(desc[:GD_CHECKSUM_OFFSET]))
        crc = raw_crc32c(crc, b"\x00\x00")           # saglama alani sifir sayilir
        rest = GD_CHECKSUM_OFFSET + 2
        if len(desc) > rest:
            crc = raw_crc32c(crc, bytes(desc[rest:]))
        return crc & 0xFFFF

    # ------------------------------------------------------------------
    # Inode
    # ------------------------------------------------------------------
    def inode_seed(self, ino: int, generation: int) -> int:
        crc = raw_crc32c(self.seed, struct.pack("<I", ino))
        return raw_crc32c(crc, struct.pack("<I", generation))

    def _has_csum_hi(self, raw: bytes) -> bool:
        """`i_checksum_hi` bu inode'a sigiyor mu? (extra_isize'a bagli)"""
        if self.fs.inode_size <= GOOD_OLD_INODE_SIZE:
            return False
        extra = struct.unpack_from("<H", raw, 0x80)[0]
        return (INODE_CSUM_HI + 2) <= (GOOD_OLD_INODE_SIZE + extra)

    def inode(self, ino: int, raw: bytes) -> Tuple[int, int]:
        """(lo, hi) saglama parcalarini dondurur. hi yoksa -1."""
        generation = struct.unpack_from("<I", raw, 0x64)[0]
        crc = self.inode_seed(ino, generation)
        crc = raw_crc32c(crc, bytes(raw[:INODE_CSUM_LO]))
        crc = raw_crc32c(crc, b"\x00\x00")
        offset = INODE_CSUM_LO + 2
        hi_var = self._has_csum_hi(raw)
        if hi_var:
            # 0x7E..128 arasi, sonra **128..0x82 arasi** (i_extra_isize), sonra
            # saglama alani sifir sayilir. Ortadaki parca ilk yazimda
            # atlanmisti ve hicbir inode saglamasi tutmuyordu.
            crc = raw_crc32c(crc, bytes(raw[offset:GOOD_OLD_INODE_SIZE]))
            crc = raw_crc32c(crc, bytes(raw[GOOD_OLD_INODE_SIZE:INODE_CSUM_HI]))
            offset = INODE_CSUM_HI
            crc = raw_crc32c(crc, b"\x00\x00")
            offset += 2
        if len(raw) > offset:
            crc = raw_crc32c(crc, bytes(raw[offset:self.fs.inode_size]))
        return crc & 0xFFFF, ((crc >> 16) & 0xFFFF) if hi_var else -1

    # ------------------------------------------------------------------
    # Bitmap'ler
    # ------------------------------------------------------------------
    def bitmap(self, data: bytes) -> int:
        return raw_crc32c(self.seed, bytes(data))

    # ------------------------------------------------------------------
    # Dizin blogu (ext4_dir_entry_tail)
    # ------------------------------------------------------------------
    def dir_block(self, dir_ino: int, generation: int, block: bytes) -> int:
        """Dizin blogu saglamasi: son 12 bayt haric her sey."""
        crc = self.inode_seed(dir_ino, generation)
        return raw_crc32c(crc, bytes(block[:len(block) - DIRENT_TAIL_SIZE]))

    @staticmethod
    def is_tail(block: bytes, block_size: int) -> bool:
        """Blogun sonunda saglama kuyrugu var mi?"""
        pos = block_size - DIRENT_TAIL_SIZE
        if pos < 0:
            return False
        ino, rec_len, name_len, ftype = struct.unpack_from("<IHBB", block, pos)
        return ino == 0 and rec_len == DIRENT_TAIL_SIZE and \
            name_len == 0 and ftype == DIRENT_TAIL_FT


# ----------------------------------------------------------------------
# Dogrulama — hicbir sey yazmaz
# ----------------------------------------------------------------------
def verify_volume(fs, max_inodes: int = 400,
                  max_dirs: int = 60) -> Dict[str, object]:
    """Var olan bir birimin saglamalarini bizim hesabimizla karsilastirir.

    Amac: **yazmadan once** hesaplarin dogrulugunu kanitlamak. Saglam bir
    birimde tum alanlar tutmali. Donen sozlukte her yapi turu icin
    (dogru, yanlis) sayilari bulunur.
    """
    cs = ExtChecksums(fs)
    out: Dict[str, object] = {"metadata_csum": cs.enabled,
                              "csum_seed_ozelligi": bool(
                                  fs.feature_incompat & INCOMPAT_CSUM_SEED),
                              "seed": f"0x{cs.seed:08X}"}
    if not cs.enabled:
        out["not"] = "metadata_csum kapali; saglama yok"
        return out

    dev, bs = fs.dev, fs.block_size

    # --- ustblok ---
    sb = dev.read(1024, 1024)
    stored = struct.unpack_from("<I", sb, SB_CHECKSUM_OFFSET)[0]
    out["ustblok"] = (1, 0) if cs.superblock(sb) == stored else (0, 1)

    # --- grup tanimlayicilari ---
    good = bad = 0
    for g in range(fs.group_count):
        off = fs.descriptor_offset(g)
        desc = dev.read(off, fs.desc_size)
        stored = struct.unpack_from("<H", desc, GD_CHECKSUM_OFFSET)[0]
        if cs.group_desc(g, desc) == stored:
            good += 1
        else:
            bad += 1
    out["grup_tanimlayici"] = (good, bad)

    # --- bitmap'ler ---
    good = bad = skipped = 0
    block_bytes = fs.blocks_per_group // 8
    inode_bytes = (fs.inodes_per_group + 7) // 8
    for g in range(fs.group_count):
        off = fs.descriptor_offset(g)
        desc = dev.read(off, fs.desc_size)
        flags = struct.unpack_from("<H", desc, 0x12)[0]
        for field_lo, block_field, size, uninit in ((0x18, 0x00, block_bytes, 0x0002),
                                                 (0x1A, 0x04, inode_bytes, 0x0001)):
            if flags & uninit:
                # BLOCK_UNINIT / INODE_UNINIT: bitmap diskte tutulmaz, cekirdek
                # onu uretir. Diskteki baytlarla karsilastirmak anlamsizdir.
                skipped += 1
                continue
            stored = struct.unpack_from("<H", desc, field_lo)[0]
            if fs.desc_size >= 64:
                hi_off = 0x38 if field_lo == 0x18 else 0x3A
                stored |= struct.unpack_from("<H", desc, hi_off)[0] << 16
            blk = struct.unpack_from("<I", desc, block_field)[0]
            data = dev.read(blk * bs, size)
            computed = cs.bitmap(data)
            if fs.desc_size < 64:
                computed &= 0xFFFF
            if computed == stored:
                good += 1
            else:
                bad += 1
    out["bitmap"] = (good, bad)
    out["bitmap_uninit_atlandi"] = skipped

    # --- inode'lar ---
    good = bad = 0
    for ino in range(1, min(fs.inodes_count, max_inodes) + 1):
        try:
            node = fs.read_inode(ino)
        except Exception:
            continue
        if node.links == 0 and node.mode == 0:
            continue                       # kullanilmayan inode
        off = fs._inode_table_block((ino - 1) // fs.inodes_per_group) * bs + \
            ((ino - 1) % fs.inodes_per_group) * fs.inode_size
        raw = dev.read(off, fs.inode_size)
        lo, hi = cs.inode(ino, raw)
        s_lo = struct.unpack_from("<H", raw, INODE_CSUM_LO)[0]
        ok = (lo == s_lo)
        if hi >= 0:
            s_hi = struct.unpack_from("<H", raw, INODE_CSUM_HI)[0]
            ok = ok and (hi == s_hi)
        good, bad = (good + 1, bad) if ok else (good, bad + 1)
    out["inode"] = (good, bad)

    # --- dizin bloklari ---
    good = bad = kuyruksuz = 0
    count = 0
    stack = [INO_ROOT_DEFAULT]
    seen = set()
    while stack and count < max_dirs:
        ino = stack.pop()
        if ino in seen:
            continue
        seen.add(ino)
        try:
            node = fs.read_inode(ino)
            if not node.is_dir:
                continue
            entries = fs.read_dir(node)
        except Exception:
            continue
        count += 1
        generation = struct.unpack_from(
            "<I", dev.read(fs._inode_table_block((ino - 1) // fs.inodes_per_group) * bs
                           + ((ino - 1) % fs.inodes_per_group) * fs.inode_size, fs.inode_size),
            0x64)[0]
        for blk in _dir_block_list(fs, node):
            data = dev.read(blk * bs, bs)
            if not ExtChecksums.is_tail(data, bs):
                kuyruksuz += 1
                continue
            stored = struct.unpack_from("<I", data, bs - 4)[0]
            if cs.dir_block(ino, generation, data) == stored:
                good += 1
            else:
                bad += 1
        for e in entries:
            if e.name not in (".", "..") and e.file_type == 2:
                stack.append(e.inode)
    out["dizin_blogu"] = (good, bad)
    out["kuyruksuz_dizin_blogu"] = kuyruksuz
    return out


INO_ROOT_DEFAULT = 2


def _dir_block_list(fs, node) -> List[int]:
    """Dizinin veri bloklari (extent veya dolayli)."""
    if node.uses_extents:
        return [phys for _log, phys, length in fs._extent_blocks(node)
                for phys in range(phys, phys + length)]
    out = [b for b in struct.unpack_from("<12I", node.raw_block, 0) if b]
    ind = struct.unpack_from("<I", node.raw_block, 48)[0]
    if ind:
        table = fs.dev.read(ind * fs.block_size, fs.block_size)
        for i in range(fs.block_size // 4):
            b = struct.unpack_from("<I", table, i * 4)[0]
            if b:
                out.append(b)
    return out
