"""Bolum uzerindeki dosya sistemini imzalardan tespit eder."""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional, Tuple

from . import diagnostics
from .image import BlockDevice


@dataclass
class FSInfo:
    fs_type: str = ""        # 'FAT32', 'NTFS', 'ext4', ...
    label: str = ""
    total_bytes: int = -1
    used_bytes: int = -1
    cluster_size: int = -1
    uuid: str = ""
    # Guvenlik bayraklari (CLAUDE.md: "bilinmiyor" asla "risk yok" gibi
    # sunulmaz; karar metinden degil bayraktan verilir).
    encrypted: bool = False      # icerik sifreli (BitLocker, LUKS, ...)
    container: bool = False      # icinde baska birimler var (LVM, RAID, ZFS, APFS)
    maybe_encrypted: bool = False  # imza yok ama ilk bloklar rastgele gorunuyor
    # Temiz ayrilmamis birim (NTFS: kirli bayrak ya da temiz kapatilmamis
    # gunluk). Linux suruculeri boyle bir birimi baglamayi reddeder.
    unclean: bool = False
    hibernated: bool = False     # Windows hazirda bekletmede (hiberfil.sys)
    damaged: bool = False        # yapisal tutarsizlik (FAT: fat_problem); yazma kapali

    @property
    def free_bytes(self) -> int:
        if self.total_bytes < 0 or self.used_bytes < 0:
            return -1
        return self.total_bytes - self.used_bytes

    @property
    def known(self) -> bool:
        return bool(self.fs_type) and self.fs_type != "Bilinmeyen"


def _safe_read(dev: BlockDevice, offset: int, length: int) -> bytes:
    try:
        if offset + length > dev.size:
            return b""
        return dev.read(offset, length)
    except Exception:
        return b""


def _clean(raw: bytes) -> str:
    return raw.decode("latin-1", "ignore").strip().strip("\x00").strip()


def detect(dev: BlockDevice) -> FSInfo:
    """Bolumun ilk sektorlerine bakarak dosya sistemini belirler."""
    boot = _safe_read(dev, 0, 512)
    if len(boot) < 512:
        return FSInfo(fs_type="")

    # --- sifreli / kapsayici (dosya sistemi imzalarindan once) ---
    special = _container_or_encrypted(dev, boot)
    if special is not None:
        return special
    # --- exFAT ---
    if boot[3:11] == b"EXFAT   ":
        return _exfat(dev, boot)
    # --- NTFS ---
    if boot[3:11] == b"NTFS    ":
        return _ntfs(dev, boot)
    # --- FAT (FAT12/16/32) ---
    fat = _fat(dev, boot)
    if fat is not None:
        return fat
    # --- ext2/3/4 (superblok 1024. bayttan itibaren) ---
    sb = _safe_read(dev, 1024, 1024)
    if len(sb) == 1024 and struct.unpack_from("<H", sb, 56)[0] == 0xEF53:
        return _ext(sb)
    # --- Linux takas alani ---
    if _safe_read(dev, 4086, 10) in (b"SWAPSPACE2", b"SWAP-SPACE"):
        return _swap(dev)
    # --- btrfs ---
    if _safe_read(dev, 0x10000 + 0x40, 8) == b"_BHRfS_M":
        return _btrfs(dev)
    # --- XFS ---
    if boot[:4] == b"XFSB":
        return _xfs(boot, dev)
    # --- HFS+ / HFSX / HFS ---
    hfs = _safe_read(dev, 1024, 512)
    if hfs[:2] in (b"H+", b"HX") and struct.unpack_from(">H", hfs, 2)[0] in (4, 5):
        return _hfsplus(dev, hfs)
    if hfs[:2] == b"BD" and hfs[0x7C:0x7E] in (b"H+", b"HX"):
        # Eski HFS sarmalayicisina gomulu HFS+ (klasik Mac OS diskleri)
        try:
            from .hfsplus import HfsPlusFS
            inner = HfsPlusFS._embedded(dev, hfs)
            return _hfsplus(inner, _safe_read(inner, 1024, 512))
        except Exception:                      # noqa: BLE001
            pass
    if hfs[:2] == b"BD":
        return FSInfo(fs_type="HFS", label=_pascal(hfs[36:64]), total_bytes=dev.size)
    # --- UDF (ISO9660 koprusu olabilir; UDF oncelikli) ---
    udf = _udf(dev)
    if udf is not None:
        return udf
    # --- ISO9660 ---
    if _safe_read(dev, 32769, 5) == b"CD001":
        return FSInfo(fs_type="ISO9660", label=_clean(_safe_read(dev, 32808, 32)),
                      total_bytes=dev.size)
    # --- F2FS ---
    if _safe_read(dev, 1024, 4) == b"\x10\x20\xF5\xF2":
        return _f2fs(dev)
    # --- JFS ---
    jfs = _safe_read(dev, 32768, 256)
    if jfs[:4] == b"JFS1":
        return FSInfo(fs_type="JFS", label=_clean(jfs[0x98:0xA8]) or _clean(jfs[0x65:0x70]),  # s_label; eski: s_fpack
                      total_bytes=dev.size)
    # --- ReiserFS ---
    for base in (0x10000, 0x2000):
        magic = _safe_read(dev, base + 52, 10)
        if magic.startswith((b"ReIsErFs", b"ReIsEr2Fs", b"ReIsEr3Fs")):
            return FSInfo(fs_type="ReiserFS", label=_clean(_safe_read(dev, base + 100, 16)),
                          total_bytes=dev.size)
    # --- bcachefs / bcache ---
    bc = _safe_read(dev, 4096 + 24, 16)
    if bc == BCACHEFS_MAGIC:
        return FSInfo(fs_type="bcachefs", total_bytes=dev.size)
    if bc == BCACHE_MAGIC:
        return FSInfo(fs_type="bcache", total_bytes=dev.size, container=True)
    # --- NILFS2 / EROFS / Minix / SquashFS ---
    sb2 = _safe_read(dev, 1024, 32)
    if len(sb2) == 32 and struct.unpack_from("<H", sb2, 6)[0] == 0x3434:
        return FSInfo(fs_type="NILFS2", total_bytes=dev.size)
    if len(sb2) == 32 and struct.unpack_from("<I", sb2, 0)[0] == 0xE0F5E1E2:
        return FSInfo(fs_type="EROFS", total_bytes=dev.size)
    if len(sb2) == 32 and (struct.unpack_from("<H", sb2, 16)[0] in
                           (0x137F, 0x138F, 0x2468, 0x2478)
                           or struct.unpack_from("<H", sb2, 24)[0] == 0x4D5A):
        return FSInfo(fs_type="Minix", total_bytes=dev.size)
    if boot[:4] == b"hsqs":
        return FSInfo(fs_type="SquashFS", total_bytes=dev.size,
                      used_bytes=min(dev.size, struct.unpack_from("<Q", boot, 40)[0]))
    # --- ZFS ---
    if _zfs(dev):
        return FSInfo(fs_type="ZFS", total_bytes=dev.size, container=True)

    if boot == b"\x00" * 512:
        return FSInfo(fs_type="")  # bicimlendirilmemis
    info = FSInfo(fs_type="Bilinmeyen")
    # Imzasi olmayan sifreli birimler (VeraCrypt/TrueCrypt, duz dm-crypt)
    # rastgele veriye benzer. Kesin degildir; yalnizca uyari icin kullanilir.
    info.maybe_encrypted = _looks_random(_safe_read(dev, 0, 4096))
    return info


# ----------------------------------------------------------------------
# Sifreli birimler ve kapsayicilar (ADR 0053)
# ----------------------------------------------------------------------
BCACHE_MAGIC = bytes.fromhex("c68573f64e1a45ca8265f57f48ba6d81")
BCACHEFS_MAGIC = bytes.fromhex("c68573f666ce90a9d96a60cf803df7ef")
MD_MAGIC = 0xA92B4EFC
# BitLocker (Vista / To Go) meta veri GUID'i 4967D63B-2E29-4AD8-8399-F6A339E3D001
FVE_GUID = bytes.fromhex("3bd66749292ed84a8399f6a339e3d001")


def _container_or_encrypted(dev: BlockDevice, boot: bytes) -> Optional[FSInfo]:
    """Dosya sisteminden ONCE bakilir: sifreli/kapsayici birimin icinde
    (veya yaninda) gecerli gorunen bir dosya sistemi imzasi bulunabilir —
    ornegin ucta RAID ustverisi olan bir RAID1 uyesi ext4 gibi gorunur.
    Onu "ext4" diye gostermek, bicimlendirmeye davet etmektir."""
    # LUKS1 / LUKS2
    if boot[:6] == b"LUKS\xba\xbe":
        version = struct.unpack_from(">H", boot, 6)[0]
        label = _clean(boot[24:72]) if version >= 2 else ""
        return FSInfo(fs_type="LUKS" + (str(version) if version in (1, 2) else ""),
                      label=label, uuid=_clean(boot[168:208]),
                      total_bytes=dev.size, encrypted=True)
    # BitLocker
    if boot[3:11] == b"-FVE-FS-" or FVE_GUID in boot:
        return FSInfo(fs_type="BitLocker", total_bytes=dev.size, encrypted=True)
    # LVM2 fiziksel birim: etiket ilk 4 sektorden birinde
    head = _safe_read(dev, 0, 2048)
    for sector in range(4):
        blk = head[sector * 512:(sector + 1) * 512]
        if blk[:8] == b"LABELONE" and blk[24:32] == b"LVM2 001":
            return FSInfo(fs_type="LVM2", uuid=_clean(blk[32:64]),
                          total_bytes=dev.size, container=True)
    # Linux yazilim RAID (md): 1.1 basta, 1.2 4 KiB'de, 1.0 ve 0.90 sonda
    size = dev.size
    spots = [0, 4096]
    if size >= 16384:
        spots.append(((size // 512 - 16) & ~7) * 512)            # 1.0
    if size >= 131072:
        spots.append((size & ~(65536 - 1)) - 65536)              # 0.90
    for off in spots:
        raw = _safe_read(dev, off, 64)
        if len(raw) >= 64 and struct.unpack_from("<I", raw, 0)[0] == MD_MAGIC:
            major, minor = struct.unpack_from("<II", raw, 4)
            v1 = major == 1 and off in (0, 4096) or (major == 1 and off > 4096
                                                        and off % 4096 == 0)
            v090 = major == 0 and minor == 90
            if not (v1 or v090):
                continue          # sihirli sayi tek basina yetmez (artik veri)
            name = _clean(raw[32:64]) if major == 1 else ""
            return FSInfo(fs_type="Linux RAID", label=name, total_bytes=size,
                          container=True)
    # APFS kapsayicisi
    if boot[32:36] == b"NXSB":
        bs = struct.unpack_from("<I", boot, 36)[0]
        count = struct.unpack_from("<Q", boot, 40)[0]
        total = bs * count if 4096 <= bs <= 65536 else size
        return FSInfo(fs_type="APFS", total_bytes=min(total, size) or size,
                      cluster_size=bs, container=True)
    # Apple CoreStorage (FileVault 2 / Fusion, macOS 10.7-10.12)
    if boot[88:90] == b"CS" and struct.unpack_from("<H", boot, 90)[0] == 1:
        return FSInfo(fs_type="CoreStorage", total_bytes=size, container=True,
                      encrypted=True)
    # ReFS
    if boot[3:11] == b"ReFS\x00\x00\x00\x00" and boot[16:20] == b"FSRS":
        return FSInfo(fs_type="ReFS", total_bytes=size)
    return None


def _looks_random(data: bytes) -> bool:
    """Shannon entropisi 7.5 bit/bayt ustu mu (4 KiB ornekte)."""
    if len(data) < 4096 or not data.strip(b"\x00"):
        return False
    import math
    counts = [0] * 256
    for b in data:
        counts[b] += 1
    n = len(data)
    entropy = -sum(c / n * math.log2(c / n) for c in counts if c)
    return entropy > 7.5


def _pascal(raw: bytes) -> str:
    n = raw[0] if raw else 0
    return raw[1:1 + n].decode("mac_roman", "ignore")


def _btrfs(dev: BlockDevice) -> FSInfo:
    sb = _safe_read(dev, 0x10000, 0x1000)
    total = struct.unpack_from("<Q", sb, 0x70)[0]
    used = struct.unpack_from("<Q", sb, 0x78)[0]
    info = FSInfo(fs_type="btrfs", label=_clean(sb[0x12B:0x12B + 256]),
                  total_bytes=total or dev.size,
                  cluster_size=struct.unpack_from("<I", sb, 0x90)[0])
    if 0 <= used <= info.total_bytes:
        info.used_bytes = used
    try:
        import uuid as _uuid
        info.uuid = str(_uuid.UUID(bytes=sb[0x20:0x30]))
    except Exception:
        pass
    return info


def _xfs(boot: bytes, dev: BlockDevice) -> FSInfo:
    bs = struct.unpack_from(">I", boot, 4)[0]
    dblocks = struct.unpack_from(">Q", boot, 8)[0]
    free = struct.unpack_from(">Q", boot, 0x90)[0]
    info = FSInfo(fs_type="XFS", label=_clean(boot[0x6C:0x78]),
                  total_bytes=bs * dblocks or dev.size, cluster_size=bs)
    if free <= dblocks:
        # sb_fdblocks tembel sayaclarla yaklasiktir; gosterim icin yeterli
        info.used_bytes = (dblocks - free) * bs
    try:
        import uuid as _uuid
        info.uuid = str(_uuid.UUID(bytes=boot[32:48]))
    except Exception:
        pass
    return info


def _hfsplus(dev: BlockDevice, hdr: bytes) -> FSInfo:
    bs = struct.unpack_from(">I", hdr, 40)[0]
    total = struct.unpack_from(">I", hdr, 44)[0]
    free = struct.unpack_from(">I", hdr, 48)[0]
    fs_type = "HFSX" if hdr[:2] == b"HX" else "HFS+"
    info = FSInfo(fs_type=fs_type, total_bytes=bs * total or dev.size,
                  used_bytes=(total - free) * bs if free <= total else -1,
                  cluster_size=bs)
    info.label = _hfsplus_label(dev, hdr, bs)
    return info


def _hfsplus_label(dev: BlockDevice, hdr: bytes, bs: int) -> str:
    """Birim adi katalog B-agacinin ilk yaprak kaydinin anahtarindadir.

    Katalog kayitlari (ust_kimlik, ad) sirasiyla dizilir; en kucuk anahtar
    kok klasorun kaydidir: ust kimlik 1, ad = birim adi.
    """
    try:
        # catalogFile fork verisi: ofset 0x110; ilk extent 0x110+16
        start = struct.unpack_from(">I", hdr, 0x110 + 16)[0]
        header_node = _safe_read(dev, start * bs, 512)
        node_size = struct.unpack_from(">H", header_node, 14 + 18)[0]
        first_leaf = struct.unpack_from(">I", header_node, 14 + 10)[0]
        if not node_size or not first_leaf:
            return ""
        node = _safe_read(dev, start * bs + first_leaf * node_size, node_size)
        rec = struct.unpack_from(">H", node, node_size - 2)[0]
        parent = struct.unpack_from(">I", node, rec + 2)[0]
        if parent != 1:
            return ""
        length = struct.unpack_from(">H", node, rec + 6)[0]
        return node[rec + 8:rec + 8 + 2 * length].decode("utf-16-be", "ignore")
    except Exception:
        return ""


def _udf(dev: BlockDevice) -> Optional[FSInfo]:
    """ECMA-167 birim tanima dizisi: 32 KiB'den itibaren BEA01 ... NSR02/03."""
    found_bea = False
    for step in (2048, 4096, 512):
        found_bea = False
        for k in range(32):
            off = 32768 + k * step
            ident = _safe_read(dev, off + 1, 5)
            if ident == b"BEA01":
                found_bea = True
            elif ident in (b"NSR02", b"NSR03") and found_bea:
                info = FSInfo(fs_type="UDF", total_bytes=dev.size)
                info.label = _udf_label(dev)
                return info
            elif ident == b"TEA01" or (not ident.strip(b"\x00") and k > 2):
                break
    return None


def _udf_label(dev: BlockDevice) -> str:
    """Mantiksal birim tanimlayicisindan (LVD, etiket 6) birim adi."""
    for bs in (2048, 4096, 512):
        avdp = _safe_read(dev, 256 * bs, 32)
        if len(avdp) < 32 or struct.unpack_from("<H", avdp, 0)[0] != 2:
            continue
        length, loc = struct.unpack_from("<II", avdp, 16)
        for i in range(min(64, max(1, length // bs))):
            d = _safe_read(dev, (loc + i) * bs, 512)
            if len(d) < 512:
                break
            tag = struct.unpack_from("<H", d, 0)[0]
            if tag == 6:
                return _dstring(d[84:84 + 128])
            if tag == 8:
                break
        return ""
    return ""


def _dstring(raw: bytes) -> str:
    """OSTA CS0 d-string: ilk bayt 8 (Latin-1) veya 16 (UTF-16BE), son bayt uzunluk."""
    if not raw:
        return ""
    n = raw[-1]
    if n == 0:
        return ""
    body = raw[1:n]
    if raw[0] == 16:
        return body.decode("utf-16-be", "ignore")
    return body.decode("latin-1", "ignore")


def _f2fs(dev: BlockDevice) -> FSInfo:
    sb = _safe_read(dev, 1024, 3072)
    bs = 1 << struct.unpack_from("<I", sb, 16)[0]
    count = struct.unpack_from("<Q", sb, 36)[0]
    label = sb[0x7C:0x7C + 1024].decode("utf-16-le", "ignore").split("\x00")[0]
    info = FSInfo(fs_type="F2FS", label=label, total_bytes=count * bs or dev.size,
                  cluster_size=bs)
    # Dolu blok sayisi guncel kontrol noktasindadir (iki kopyadan yenisi).
    try:
        cp_addr = struct.unpack_from("<I", sb, 0x4C)[0]
        per_seg = 1 << struct.unpack_from("<I", sb, 20)[0]
        best = None
        for addr in (cp_addr, cp_addr + per_seg):
            cp = _safe_read(dev, addr * bs, 32)
            if len(cp) < 32:
                continue
            ver, _user, valid = struct.unpack_from("<QQQ", cp, 0)
            if best is None or ver > best[0]:
                best = (ver, valid)
        if best and best[1] <= count:
            info.used_bytes = best[1] * bs
    except Exception:
        pass
    return info


def _zfs(dev: BlockDevice) -> bool:
    """vdev etiketinin uberblock halkasinda 0x00bab10c (iki bayt sirasi)."""
    for label in (0, 256 * 1024):
        for k in range(4):
            raw = _safe_read(dev, label + 128 * 1024 + k * 1024, 8)
            if len(raw) == 8 and 0x00BAB10C in (struct.unpack("<Q", raw)[0],
                                                  struct.unpack(">Q", raw)[0]):
                return True
    return False


def _fat(dev: BlockDevice, boot: bytes) -> Optional[FSInfo]:
    if boot[510:512] != b"\x55\xAA":
        return None
    if boot[0] not in (0xEB, 0xE9, 0xE8):
        return None
    bps = struct.unpack_from("<H", boot, 11)[0]
    spc = boot[13]
    if bps not in (512, 1024, 2048, 4096) or spc not in (1, 2, 4, 8, 16, 32, 64, 128):
        return None
    reserved = struct.unpack_from("<H", boot, 14)[0]
    num_fats = boot[16]
    root_entries = struct.unpack_from("<H", boot, 17)[0]
    total16 = struct.unpack_from("<H", boot, 19)[0]
    fat16_size = struct.unpack_from("<H", boot, 22)[0]
    total32 = struct.unpack_from("<I", boot, 32)[0]
    fat32_size = struct.unpack_from("<I", boot, 36)[0]
    if num_fats == 0 or reserved == 0:
        return None

    total = total16 or total32
    fat_size = fat16_size or fat32_size
    if total == 0 or fat_size == 0:
        return None
    root_sectors = (root_entries * 32 + bps - 1) // bps
    data_sectors = total - (reserved + num_fats * fat_size + root_sectors)
    if data_sectors <= 0:
        return None
    clusters = data_sectors // spc
    # FAT32 BPB yapisindan taninir (BPB_FATSz16 == 0), kume sayisindan degil:
    # mkdosfs -F 32 kucuk bolumde 65525'ten az kumeli FAT32 uretebilir.
    if fat16_size == 0:
        fs_type, label_off = "FAT32", 0x47
    elif clusters < 4085:
        fs_type, label_off = "FAT12", 0x2B
    else:
        fs_type, label_off = "FAT16", 0x2B
    label, damaged = _fat_label(dev, boot, fs_type)
    info = FSInfo(fs_type=fs_type, label=label,
                  total_bytes=total * bps, cluster_size=spc * bps,
                  damaged=damaged)
    # Windows'un kirli bayragi (BS_NTRes bit0-1); FAT[1] bitleri FatFS'te
    info.unclean = bool((boot[0x41] if fs_type == "FAT32" else boot[0x25]) & 0x03)
    # FAT32 ExtFlags: aynalama kapaliysa (bit 7) etkin FAT bit 0-3'tedir
    active = 0
    if fs_type == "FAT32":
        ext_flags = struct.unpack_from("<H", boot, 40)[0]
        if ext_flags & 0x80 and (ext_flags & 0x0F) < num_fats:
            active = ext_flags & 0x0F
    info.used_bytes = _fat_used(dev, bps, spc, reserved + active * fat_size,
                                fat_size, clusters, fs_type)
    return info


def _fat_label(dev: BlockDevice, boot: bytes, fs_type: str) -> Tuple[str, bool]:
    """(etiket, tutarsiz_mi). Etiket: kok dizindeki etiket girisi, yoksa BPB
    etiketi (blkid ile ayni sira). Tutarsizlik FatFS.fat_problem'dir.

    BPB etiketi yalnizca genisletilmis imza 0x29 varsa gecerlidir; "NO NAME"
    etiket yok demektir.
    """
    try:
        from .fat import FatFS
        fs = FatFS(dev)
        return fs.label, bool(fs.fat_problem())
    except Exception:                      # noqa: BLE001
        pass
    sig_off, label_off = (0x42, 0x47) if fs_type == "FAT32" else (0x26, 0x2B)
    if boot[sig_off] != 0x29:
        return "", False
    label = _clean(boot[label_off:label_off + 11])
    return ("" if label == "NO NAME" else label), False


def _fat_used(dev, bps, spc, reserved, fat_size, clusters, fs_type) -> int:
    """FAT tablosunu tarayarak kullanilan alani hesaplar."""
    try:
        raw = dev.read(reserved * bps, min(fat_size * bps, 64 * 1024 * 1024))
    except Exception:
        return -1
    used = 0
    if fs_type == "FAT32":
        n = min(clusters + 2, len(raw) // 4)
        for i in range(2, n):
            if struct.unpack_from("<I", raw, i * 4)[0] & 0x0FFFFFFF:
                used += 1
    elif fs_type == "FAT16":
        n = min(clusters + 2, len(raw) // 2)
        for i in range(2, n):
            if struct.unpack_from("<H", raw, i * 2)[0]:
                used += 1
    else:
        n = min(clusters + 2, (len(raw) * 2) // 3)
        for i in range(2, n):
            off = (i * 3) // 2
            if off + 2 > len(raw):
                break
            val = struct.unpack_from("<H", raw, off)[0]
            val = (val >> 4) if (i & 1) else (val & 0x0FFF)
            if val:
                used += 1
    return used * spc * bps


def _swap(dev: BlockDevice) -> FSInfo:
    """Linux takas alani basligini cozumler.

    Yerlesim (linux/include/linux/swap.h, `union swap_header`):
        0      bootbits (1024 bayt)
        1024   version (4)
        1028   last_page (4)
        1032   nr_badpages (4)
        1036   sws_uuid (16)
        1052   sws_volume (16)   <- ETIKET
        4086   magic "SWAPSPACE2"

    Etiket 1052'dedir; 1040 gibi yanlis bir ofset UUID'nin ortasina denk gelir
    ve arayuzde bozuk karakter gorunur.
    """
    import uuid as _uuid

    header = _safe_read(dev, 1024, 44)
    etiket = ""
    kimlik = ""
    page_count = 0
    if len(header) >= 44:
        page_count = struct.unpack_from("<I", header, 4)[0]
        try:
            ham_uuid = header[12:28]
            if ham_uuid.strip(b"\x00"):
                kimlik = str(_uuid.UUID(bytes=ham_uuid))
        except (ValueError, TypeError):
            kimlik = ""
    etiket = _clean(_safe_read(dev, 1052, 16))
    # sws_volume ASCII disi bayt iceriyorsa etiket yok sayilir
    if any(ord(ch) < 32 or ord(ch) > 126 for ch in etiket):
        etiket = ""
    total = page_count * 4096 if page_count else dev.size
    return FSInfo(fs_type="Linux Takas", label=etiket, uuid=kimlik,
                  total_bytes=min(total, dev.size) or dev.size,
                  used_bytes=0)


def _exfat(dev: BlockDevice, boot: bytes) -> FSInfo:
    bps = 1 << boot[108]
    spc = 1 << boot[109]
    total = struct.unpack_from("<Q", boot, 72)[0]
    cluster_count = struct.unpack_from("<I", boot, 92)[0]
    serial = struct.unpack_from("<I", boot, 100)[0]
    info = FSInfo(fs_type="exFAT", total_bytes=total * bps, cluster_size=bps * spc,
                  uuid=f"{serial:08X}")
    # VolumeDirty / MediaFailure (spec 3.1.13): temiz ayrilmamis
    info.unclean = bool(struct.unpack_from("<H", boot, 106)[0] & 0x0006)
    info.used_bytes = _exfat_used(dev, boot, bps, spc, cluster_count)
    info.label = _exfat_label(dev, boot, bps, spc)
    return info


def _exfat_label(dev, boot, bps, spc) -> str:
    """Kok dizindeki 0x83 birim etiketi girisini okur."""
    try:
        cluster_heap = struct.unpack_from("<I", boot, 88)[0]
        root_cluster = struct.unpack_from("<I", boot, 96)[0]
        raw = dev.read((cluster_heap + (root_cluster - 2) * spc) * bps, bps * spc)
        for i in range(0, len(raw), 32):
            if raw[i] == 0x83:
                n = raw[i + 1]
                return raw[i + 2:i + 2 + n * 2].decode("utf-16-le", "ignore")
            if raw[i] == 0x00:
                break
    except Exception:
        pass
    return ""


def _exfat_used(dev, boot, bps, spc, cluster_count) -> int:
    try:
        cluster_heap = struct.unpack_from("<I", boot, 88)[0]
        root_cluster = struct.unpack_from("<I", boot, 96)[0]
        # kok dizinde ayirma bitmap girisi (0x81) aranir
        off = (cluster_heap + (root_cluster - 2) * spc) * bps
        raw = dev.read(off, bps * spc)
        for i in range(0, len(raw), 32):
            if raw[i] == 0x81:
                first = struct.unpack_from("<I", raw, i + 20)[0]
                length = struct.unpack_from("<Q", raw, i + 24)[0]
                bmp = dev.read((cluster_heap + (first - 2) * spc) * bps,
                               min(length, 32 * 1024 * 1024))
                used = sum(bin(b).count("1") for b in bmp[: (cluster_count + 7) // 8])
                return used * spc * bps
    except Exception:
        pass
    return -1


def _ntfs(dev: BlockDevice, boot: bytes) -> FSInfo:
    bps = struct.unpack_from("<H", boot, 11)[0]
    spc = boot[13]
    total = struct.unpack_from("<Q", boot, 40)[0]
    serial = struct.unpack_from("<Q", boot, 72)[0]
    # 0x80 ustu deger 2'nin kuvvetidir (2 MiB'a kadar kume; ntfsread ile ayni)
    spc = spc if spc <= 0x80 else 1 << (256 - spc)
    info = FSInfo(fs_type="NTFS", total_bytes=(total + 1) * bps,
                  cluster_size=bps * spc, uuid=f"{serial:016X}")
    # Doluluk ve etiket onyukleme sektorunde YOKTUR: ikisi de ustveri
    # dosyalarindadir ($Bitmap ve $Volume). FAT/exFAT/ext'te bu bilgi hemen
    # elde oldugu icin NTFS uzun sure "doluluk bilinmiyor" kalmisti — harita
    # cubugu yalnizca NTFS bolumlerde cizilmiyordu.
    #
    # MFT'yi acmak birkac okuma, bitmap ise birim basina ~kume_sayisi/8 bayt
    # (200 GB'de ~6 MB) okur; bu yuzden olculur ve basarisizlik olumcul
    # sayilmaz: bilgi alinamazsa -1 ("bilinmiyor") kalir, tespit gecerlidir.
    with diagnostics.span("fs.ntfs_meta", track=False, size=info.total_bytes):
        try:
            from .ntfsread import NtfsFS
            fs = NtfsFS(dev)
            used = fs.used_bytes()
            if 0 <= used <= info.total_bytes:
                info.used_bytes = used
            info.label = fs.label
            from .ntfsfix import quick_state
            info.unclean, info.hibernated = quick_state(fs)
        except Exception as exc:
            diagnostics.debug(f"NTFS ustverisi okunamadi: {exc}")
    return info


def _ext(sb: bytes) -> FSInfo:
    import uuid as _uuid
    blocks = struct.unpack_from("<I", sb, 4)[0]
    free = struct.unpack_from("<I", sb, 12)[0]
    log_bs = struct.unpack_from("<I", sb, 24)[0]
    block_size = 1024 << log_bs
    label = _clean(sb[120:136])
    feat_compat = struct.unpack_from("<I", sb, 92)[0]
    feat_incompat = struct.unpack_from("<I", sb, 96)[0]
    feat_ro = struct.unpack_from("<I", sb, 100)[0]
    # blkid kurali (E15); ayri gunluk aygiti "jbd" olur ve ext erisimine
    # (startswith("ext")) yonlenmez: dosya sistemi degildir (E9).
    from .extlayout import ext_kind
    fs_type = ext_kind(feat_compat, feat_incompat, feat_ro)
    blocks_hi = struct.unpack_from("<I", sb, 0x150)[0] if len(sb) > 0x154 else 0
    if not feat_incompat & 0x0080:                    # 64bit yoksa ust yari anlamsiz
        blocks_hi = 0
    total_blocks = blocks | (blocks_hi << 32)
    if feat_incompat & 0x0080 and len(sb) > 0x15C:
        # 64bit birimde bos blok sayisinin ust yarisi 0x158'dedir; okunmayinca
        # 16 TiB ustu birimde doluluk yanlis cikiyordu.
        free |= struct.unpack_from("<I", sb, 0x158)[0] << 32
    try:
        fs_uuid = str(_uuid.UUID(bytes=sb[104:120]))
    except Exception:
        fs_uuid = ""
    return FSInfo(fs_type=fs_type, label=label,
                  total_bytes=total_blocks * block_size,
                  used_bytes=(total_blocks - free) * block_size,
                  cluster_size=block_size, uuid=fs_uuid)
