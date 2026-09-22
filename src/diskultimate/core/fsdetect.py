"""Bolum uzerindeki dosya sistemini imzalardan tespit eder."""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

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
        label = _clean(_safe_read(dev, 0x10000 + 0x12B, 256))
        return FSInfo(fs_type="btrfs", label=label, total_bytes=dev.size)
    # --- XFS ---
    if boot[:4] == b"XFSB":
        return FSInfo(fs_type="XFS", label=_clean(boot[0x6C:0x7C]), total_bytes=dev.size)
    # --- ISO9660 ---
    if _safe_read(dev, 32769, 5) == b"CD001":
        return FSInfo(fs_type="ISO9660", label=_clean(_safe_read(dev, 32808, 32)),
                      total_bytes=dev.size)
    # --- F2FS ---
    if _safe_read(dev, 1024, 4) == b"\x10\x20\xF5\xF2":
        return FSInfo(fs_type="F2FS", total_bytes=dev.size)

    if boot == b"\x00" * 512:
        return FSInfo(fs_type="")  # bicimlendirilmemis
    return FSInfo(fs_type="Bilinmeyen")


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
    if clusters < 4085:
        fs_type, label_off = "FAT12", 0x2B
    elif clusters < 65525:
        fs_type, label_off = "FAT16", 0x2B
    else:
        fs_type, label_off = "FAT32", 0x47
    label = _clean(boot[label_off:label_off + 11])
    info = FSInfo(fs_type=fs_type, label=label,
                  total_bytes=total * bps, cluster_size=spc * bps)
    info.used_bytes = _fat_used(dev, bps, spc, reserved, fat_size, clusters, fs_type)
    return info


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
    if feat_incompat & 0x0040 or feat_ro & 0x0008:   # EXTENTS / HUGE_FILE
        fs_type = "ext4"
    elif feat_compat & 0x0004:                        # HAS_JOURNAL
        fs_type = "ext3"
    else:
        fs_type = "ext2"
    blocks_hi = struct.unpack_from("<I", sb, 0x150)[0] if len(sb) > 0x154 else 0
    total_blocks = blocks | (blocks_hi << 32)
    try:
        fs_uuid = str(_uuid.UUID(bytes=sb[104:120]))
    except Exception:
        fs_uuid = ""
    return FSInfo(fs_type=fs_type, label=label,
                  total_bytes=total_blocks * block_size,
                  used_bytes=(total_blocks - free) * block_size,
                  cluster_size=block_size, uuid=fs_uuid)
