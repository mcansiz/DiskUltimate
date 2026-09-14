"""Sanal disk kapsayicilari: VHD, VDI, VMDK, QCOW2.

Her sinif `BlockDevice` arayuzunu uygular; bu sayede bolum tablosu ve dosya
sistemi katmanlari sanal diski ham `.img` ile ayni sekilde gorur.

Destek durumu:
    VHD sabit (fixed)      okuma + yazma + olusturma
    VHD dinamik            okuma + var olan bloga yazma
    VDI                    okuma
    VMDK duz (flat)        okuma + yazma
    VMDK seyrek (sparse)   okuma
    QCOW2 (sikistirmasiz)  okuma
"""
from __future__ import annotations

import os
import struct
import uuid
from typing import List, Optional, Tuple

from .image import BlockDevice, DiskImage, DiskImageError

SECTOR = 512


class VirtualDiskError(DiskImageError):
    pass


class _BaseVirtualDisk(BlockDevice):
    """Ortak davranis: dosya tutamaci, salt okunurluk, sektor hesaplari."""

    format_name = "sanal disk"

    def __init__(self, path: str, readonly: bool = True,
                 sector_size: int = SECTOR):
        self.path = os.path.abspath(path)
        self.sector_size = sector_size
        self.readonly = readonly
        mod = "rb" if readonly else "r+b"
        try:
            self._fh = open(self.path, mod)
        except PermissionError:
            self._fh = open(self.path, "rb")
            self.readonly = True
        self._size = 0

    @property
    def sector_count(self) -> int:
        return self._size // self.sector_size

    @property
    def size(self) -> int:
        return self._size

    def flush(self) -> None:
        if not self.readonly:
            self._fh.flush()

    def close(self) -> None:
        try:
            if not self.readonly:
                self._fh.flush()
        finally:
            self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {os.path.basename(self.path)} {self._size} bayt>"

    def info(self) -> dict:
        from .ptable import human_size
        return {
            "Bicim": self.format_name,
            "Dosya": os.path.basename(self.path),
            "Sanal boyut": human_size(self._size),
            "Dosya boyutu": human_size(os.path.getsize(self.path)),
            "Erisim": "Salt okunur" if self.readonly else "Okuma/Yazma",
        }

    # -- alt siniflarin uygulamasi ----------------------------------------
    def _read_raw(self, offset: int, length: int) -> bytes:  # pragma: no cover
        raise NotImplementedError

    def read(self, offset: int, length: int) -> bytes:
        if offset < 0 or length < 0:
            raise VirtualDiskError("Negatif ofset/uzunluk")
        if offset + length > self._size:
            raise VirtualDiskError("Okuma sanal disk sinirini asiyor")
        return self._read_raw(offset, length)

    def write(self, offset: int, data: bytes) -> None:
        raise VirtualDiskError(
            f"{self.format_name} bu surumde salt okunur acilir")


# ==========================================================================
# VHD (Microsoft Virtual Hard Disk) — tum alanlar big-endian
# ==========================================================================
VHD_COOKIE = b"conectix"
VHD_DYNAMIC_COOKIE = b"cxsparse"
VHD_FIXED = 2
VHD_DYNAMIC = 3
VHD_DIFFERENCING = 4


def _vhd_checksum(data: bytes) -> int:
    return (~sum(data)) & 0xFFFFFFFF


def _vhd_geometry(total_sectors: int) -> Tuple[int, int, int]:
    """VHD footer icin CHS geometrisi (spec'teki algoritma)."""
    ts = min(total_sectors, 65535 * 16 * 255)
    if ts >= 65535 * 16 * 63:
        return 65535, 16, 255
    if ts >= 65535 * 16 * 63:
        sektor_iz, kafa = 255, 16
    else:
        sektor_iz = 17
        silindir_carpani = ts // sektor_iz
        kafa = max(4, (silindir_carpani + 1023) // 1024)
        if silindir_carpani >= (kafa * 1024) or kafa > 16:
            sektor_iz, kafa = 31, 16
            silindir_carpani = ts // sektor_iz
        if silindir_carpani >= (kafa * 1024):
            sektor_iz, kafa = 63, 16
            silindir_carpani = ts // sektor_iz
    silindir = (ts // sektor_iz) // kafa
    return max(1, silindir), kafa, sektor_iz


def build_vhd_footer(size_bytes: int, disk_type: int = VHD_FIXED,
                     data_offset: int = 0xFFFFFFFFFFFFFFFF) -> bytes:
    import time
    toplam_sektor = size_bytes // SECTOR
    silindir, kafa, sektor_iz = _vhd_geometry(toplam_sektor)
    footer = bytearray(512)
    footer[0:8] = VHD_COOKIE
    struct.pack_into(">IIQ", footer, 8, 0x00000002, 0x00010000, data_offset)
    zaman = int(time.time() - 946684800)          # 2000-01-01 referansi
    struct.pack_into(">I", footer, 24, max(0, zaman))
    footer[28:32] = b"dulT"                        # DiskUltimate
    struct.pack_into(">I", footer, 32, 0x00010000)
    footer[36:40] = b"Wi2k"
    struct.pack_into(">QQ", footer, 40, size_bytes, size_bytes)
    struct.pack_into(">HBB", footer, 56, min(65535, silindir), kafa, sektor_iz)
    struct.pack_into(">I", footer, 60, disk_type)
    footer[68:84] = uuid.uuid4().bytes
    struct.pack_into(">I", footer, 64, _vhd_checksum(bytes(footer)))
    return bytes(footer)


class VhdImage(_BaseVirtualDisk):
    format_name = "VHD"

    def __init__(self, path: str, readonly: bool = True):
        super().__init__(path, readonly)
        dosya_boyutu = os.path.getsize(self.path)
        if dosya_boyutu < 512:
            raise VirtualDiskError("VHD dosyasi cok kucuk")
        self._fh.seek(-512, os.SEEK_END)
        footer = self._fh.read(512)
        if footer[:8] != VHD_COOKIE:
            self._fh.seek(0)
            footer = self._fh.read(512)
            if footer[:8] != VHD_COOKIE:
                raise VirtualDiskError("VHD imzasi bulunamadi")
        self.disk_type = struct.unpack_from(">I", footer, 60)[0]
        self._size = struct.unpack_from(">Q", footer, 48)[0]
        self.data_offset = struct.unpack_from(">Q", footer, 16)[0]
        self.block_size = 0
        self._bat: List[int] = []
        if self.disk_type == VHD_DYNAMIC:
            self._read_dynamic_header()
        elif self.disk_type == VHD_DIFFERENCING:
            raise VirtualDiskError("Farklilik (differencing) VHD desteklenmiyor")
        elif self.disk_type != VHD_FIXED:
            raise VirtualDiskError(f"Bilinmeyen VHD turu: {self.disk_type}")

    @property
    def format_name(self) -> str:  # type: ignore[override]
        return "VHD sabit" if self.disk_type == VHD_FIXED else "VHD dinamik"

    def _read_dynamic_header(self) -> None:
        self._fh.seek(self.data_offset)
        baslik = self._fh.read(1024)
        if baslik[:8] != VHD_DYNAMIC_COOKIE:
            raise VirtualDiskError("VHD dinamik basligi bulunamadi")
        self.bat_offset = struct.unpack_from(">Q", baslik, 16)[0]
        giris_sayisi = struct.unpack_from(">I", baslik, 28)[0]
        self.block_size = struct.unpack_from(">I", baslik, 32)[0]
        self._fh.seek(self.bat_offset)
        ham = self._fh.read(giris_sayisi * 4)
        self._bat = list(struct.unpack(f">{giris_sayisi}I", ham[:giris_sayisi * 4]))
        self.bitmap_sectors = max(1, ((self.block_size // SECTOR) + 7) // 8 // SECTOR
                                  or 1)
        # bitmap her zaman sektor sinirina yuvarlanir
        bitmap_bayt = ((self.block_size // SECTOR) + 7) // 8
        self.bitmap_sectors = (bitmap_bayt + SECTOR - 1) // SECTOR

    def _read_raw(self, offset: int, length: int) -> bytes:
        if self.disk_type == VHD_FIXED:
            self._fh.seek(offset)
            veri = self._fh.read(length)
            return veri + b"\x00" * (length - len(veri))
        out = bytearray()
        kalan = length
        konum = offset
        while kalan > 0:
            blok_no = konum // self.block_size
            blok_ici = konum % self.block_size
            parca = min(kalan, self.block_size - blok_ici)
            if blok_no >= len(self._bat) or self._bat[blok_no] == 0xFFFFFFFF:
                out += b"\x00" * parca            # tahsis edilmemis blok
            else:
                taban = (self._bat[blok_no] + self.bitmap_sectors) * SECTOR
                self._fh.seek(taban + blok_ici)
                veri = self._fh.read(parca)
                out += veri + b"\x00" * (parca - len(veri))
            konum += parca
            kalan -= parca
        return bytes(out)

    def write(self, offset: int, data: bytes) -> None:
        if self.readonly:
            raise VirtualDiskError("Sanal disk salt okunur acildi")
        if offset + len(data) > self._size:
            raise VirtualDiskError("Yazma sanal disk sinirini asiyor")
        if self.disk_type == VHD_FIXED:
            self._fh.seek(offset)
            self._fh.write(data)
            return
        konum, kalan, kaynak = offset, len(data), 0
        while kalan > 0:
            blok_no = konum // self.block_size
            blok_ici = konum % self.block_size
            parca = min(kalan, self.block_size - blok_ici)
            if blok_no >= len(self._bat) or self._bat[blok_no] == 0xFFFFFFFF:
                raise VirtualDiskError(
                    "Dinamik VHD'de yeni blok tahsisi bu surumde desteklenmiyor")
            taban = (self._bat[blok_no] + self.bitmap_sectors) * SECTOR
            self._fh.seek(taban + blok_ici)
            self._fh.write(data[kaynak:kaynak + parca])
            konum += parca
            kaynak += parca
            kalan -= parca

    # -- olusturma ---------------------------------------------------------
    @staticmethod
    def create_fixed(path: str, size_bytes: int, sparse: bool = True,
                     overwrite: bool = False) -> "VhdImage":
        """Sabit boyutlu VHD olusturur (ham veri + 512 baytlik footer)."""
        path = os.path.abspath(path)
        if os.path.exists(path) and not overwrite:
            raise VirtualDiskError(f"Dosya zaten var: {path}")
        size_bytes -= size_bytes % SECTOR
        from .platform import make_sparse, truncate_sparse
        with open(path, "wb") as fh:
            if sparse:
                make_sparse(fh.fileno())
                truncate_sparse(fh.fileno(), size_bytes)
            else:
                fh.truncate(size_bytes)
            fh.seek(size_bytes)
            fh.write(build_vhd_footer(size_bytes, VHD_FIXED))
        return VhdImage(path, readonly=False)


# ==========================================================================
# VDI (Oracle VirtualBox)
# ==========================================================================
VDI_MAGIC = 0xBEDA107F


class VdiImage(_BaseVirtualDisk):
    format_name = "VDI"

    def __init__(self, path: str, readonly: bool = True):
        super().__init__(path, readonly=True)
        self._fh.seek(0)
        baslik = self._fh.read(0x200)
        if struct.unpack_from("<I", baslik, 0x40)[0] != VDI_MAGIC:
            raise VirtualDiskError("VDI imzasi bulunamadi")
        self.image_type = struct.unpack_from("<I", baslik, 0x4C)[0]
        self.blocks_offset = struct.unpack_from("<I", baslik, 0x154)[0]
        self.data_offset = struct.unpack_from("<I", baslik, 0x158)[0]
        self.sector_size = struct.unpack_from("<I", baslik, 0x168)[0] or SECTOR
        self._size = struct.unpack_from("<Q", baslik, 0x170)[0]
        self.block_size = struct.unpack_from("<I", baslik, 0x178)[0]
        self.block_extra = struct.unpack_from("<I", baslik, 0x17C)[0]
        blok_sayisi = struct.unpack_from("<I", baslik, 0x180)[0]
        if self.block_size == 0:
            raise VirtualDiskError("Gecersiz VDI blok boyutu")
        self._fh.seek(self.blocks_offset)
        ham = self._fh.read(blok_sayisi * 4)
        self._bat = list(struct.unpack(f"<{blok_sayisi}I", ham[:blok_sayisi * 4]))

    def _read_raw(self, offset: int, length: int) -> bytes:
        out = bytearray()
        konum, kalan = offset, length
        while kalan > 0:
            blok_no = konum // self.block_size
            blok_ici = konum % self.block_size
            parca = min(kalan, self.block_size - blok_ici)
            giris = self._bat[blok_no] if blok_no < len(self._bat) else 0xFFFFFFFF
            if giris in (0xFFFFFFFF, 0xFFFFFFFE):
                out += b"\x00" * parca
            else:
                taban = (self.data_offset
                         + giris * (self.block_size + self.block_extra)
                         + self.block_extra)
                self._fh.seek(taban + blok_ici)
                veri = self._fh.read(parca)
                out += veri + b"\x00" * (parca - len(veri))
            konum += parca
            kalan -= parca
        return bytes(out)


# ==========================================================================
# VMDK (VMware)
# ==========================================================================
VMDK_SPARSE_MAGIC = b"KDMV"


class VmdkImage(_BaseVirtualDisk):
    format_name = "VMDK"

    def __init__(self, path: str, readonly: bool = True):
        super().__init__(path, readonly)
        self._fh.seek(0)
        imza = self._fh.read(4)
        if imza == VMDK_SPARSE_MAGIC:
            self._init_sparse()
        else:
            self._init_flat()

    def _init_sparse(self) -> None:
        self.readonly = True
        self._fh.seek(0)
        baslik = self._fh.read(512)
        (_sihir, _surum, _bayraklar, kapasite, grain, _tanim_ofset, _tanim_boyut,
         gte_per_gt, _rgd, gd_ofset) = struct.unpack_from("<4sIIQQQQIQQ", baslik, 0)
        self._size = kapasite * SECTOR
        self.grain_sectors = grain or 128
        self.gte_per_gt = gte_per_gt or 512
        self._fh.seek(gd_ofset * SECTOR)
        gd_giris = (kapasite + self.grain_sectors * self.gte_per_gt - 1) // \
            (self.grain_sectors * self.gte_per_gt)
        ham = self._fh.read(max(1, gd_giris) * 4)
        self._gd = list(struct.unpack(f"<{max(1, gd_giris)}I", ham[:max(1, gd_giris) * 4]))
        self._gt_cache: dict = {}
        self.variant = "seyrek (sparse)"

    def _init_flat(self) -> None:
        """Tanimlayici dosya: veri ayri bir `-flat.vmdk` dosyasindadir."""
        self._fh.seek(0)
        metin = self._fh.read(4096).decode("latin-1", "ignore")
        if "createType" not in metin and "RW " not in metin:
            raise VirtualDiskError("VMDK tanimlayicisi cozumlenemedi")
        veri_dosyasi = None
        toplam_sektor = 0
        for satir in metin.splitlines():
            satir = satir.strip()
            if satir.startswith("RW ") and "FLAT" in satir.upper():
                parcalar = satir.split()
                toplam_sektor = int(parcalar[1])
                tirnak = satir.find('"')
                son = satir.find('"', tirnak + 1)
                if tirnak >= 0 and son > tirnak:
                    veri_dosyasi = satir[tirnak + 1:son]
                break
        if not veri_dosyasi:
            raise VirtualDiskError("VMDK duz veri dosyasi bulunamadi")
        tam = os.path.join(os.path.dirname(self.path), veri_dosyasi)
        if not os.path.isfile(tam):
            raise VirtualDiskError(f"VMDK veri dosyasi eksik: {veri_dosyasi}")
        self._fh.close()
        self._fh = open(tam, "rb" if self.readonly else "r+b")
        self._size = toplam_sektor * SECTOR or os.path.getsize(tam)
        self._gd = None
        self.variant = "duz (flat)"

    def _grain_offset(self, sektor: int) -> int:
        gt_basina = self.grain_sectors * self.gte_per_gt
        gd_indeks = sektor // gt_basina
        if gd_indeks >= len(self._gd):
            return 0
        gt_ofset = self._gd[gd_indeks]
        if gt_ofset == 0:
            return 0
        if gt_ofset not in self._gt_cache:
            self._fh.seek(gt_ofset * SECTOR)
            ham = self._fh.read(self.gte_per_gt * 4)
            self._gt_cache[gt_ofset] = list(
                struct.unpack(f"<{self.gte_per_gt}I", ham[:self.gte_per_gt * 4]))
        gt = self._gt_cache[gt_ofset]
        gt_indeks = (sektor % gt_basina) // self.grain_sectors
        return gt[gt_indeks] if gt_indeks < len(gt) else 0

    def _read_raw(self, offset: int, length: int) -> bytes:
        if self._gd is None:                       # duz bicim
            self._fh.seek(offset)
            veri = self._fh.read(length)
            return veri + b"\x00" * (length - len(veri))
        out = bytearray()
        konum, kalan = offset, length
        grain_bayt = self.grain_sectors * SECTOR
        while kalan > 0:
            sektor = konum // SECTOR
            grain_ici = konum % grain_bayt
            parca = min(kalan, grain_bayt - grain_ici)
            grain = self._grain_offset(sektor - (grain_ici // SECTOR))
            if grain == 0:
                out += b"\x00" * parca
            else:
                self._fh.seek(grain * SECTOR + grain_ici)
                veri = self._fh.read(parca)
                out += veri + b"\x00" * (parca - len(veri))
            konum += parca
            kalan -= parca
        return bytes(out)

    def write(self, offset: int, data: bytes) -> None:
        if self._gd is not None or self.readonly:
            raise VirtualDiskError("Seyrek VMDK bu surumde salt okunur")
        self._fh.seek(offset)
        self._fh.write(data)


# ==========================================================================
# QCOW2 (QEMU)
# ==========================================================================
QCOW_MAGIC = b"QFI\xfb"
QCOW_FLAG_COMPRESSED = 1 << 62
QCOW_OFFSET_MASK = 0x00FFFFFFFFFFFE00


class Qcow2Image(_BaseVirtualDisk):
    format_name = "QCOW2"

    def __init__(self, path: str, readonly: bool = True):
        super().__init__(path, readonly=True)
        self._fh.seek(0)
        baslik = self._fh.read(104)
        if baslik[:4] != QCOW_MAGIC:
            raise VirtualDiskError("QCOW2 imzasi bulunamadi")
        surum = struct.unpack_from(">I", baslik, 4)[0]
        if surum not in (2, 3):
            raise VirtualDiskError(f"QCOW surumu desteklenmiyor: {surum}")
        arka_ofset = struct.unpack_from(">Q", baslik, 8)[0]
        if arka_ofset:
            raise VirtualDiskError("Arka plan dosyali (backing) QCOW2 desteklenmiyor")
        self.cluster_bits = struct.unpack_from(">I", baslik, 20)[0]
        self.cluster_size = 1 << self.cluster_bits
        self._size = struct.unpack_from(">Q", baslik, 24)[0]
        sifreleme = struct.unpack_from(">I", baslik, 32)[0]
        if sifreleme:
            raise VirtualDiskError("Sifreli QCOW2 desteklenmiyor")
        l1_boyut = struct.unpack_from(">I", baslik, 36)[0]
        l1_ofset = struct.unpack_from(">Q", baslik, 40)[0]
        self.l2_bits = self.cluster_bits - 3
        self._fh.seek(l1_ofset)
        ham = self._fh.read(l1_boyut * 8)
        self._l1 = list(struct.unpack(f">{l1_boyut}Q", ham[:l1_boyut * 8]))
        self._l2_cache: dict = {}

    def _cluster_offset(self, sanal: int) -> int:
        l2_giris_sayisi = 1 << self.l2_bits
        l1_indeks = sanal >> (self.l2_bits + self.cluster_bits)
        if l1_indeks >= len(self._l1):
            return 0
        l2_ofset = self._l1[l1_indeks] & QCOW_OFFSET_MASK
        if l2_ofset == 0:
            return 0
        if l2_ofset not in self._l2_cache:
            self._fh.seek(l2_ofset)
            ham = self._fh.read(l2_giris_sayisi * 8)
            self._l2_cache[l2_ofset] = list(
                struct.unpack(f">{l2_giris_sayisi}Q", ham[:l2_giris_sayisi * 8]))
        l2 = self._l2_cache[l2_ofset]
        l2_indeks = (sanal >> self.cluster_bits) & (l2_giris_sayisi - 1)
        giris = l2[l2_indeks] if l2_indeks < len(l2) else 0
        if giris & QCOW_FLAG_COMPRESSED:
            raise VirtualDiskError("Sikistirilmis QCOW2 kumesi desteklenmiyor")
        return giris & QCOW_OFFSET_MASK

    def _read_raw(self, offset: int, length: int) -> bytes:
        out = bytearray()
        konum, kalan = offset, length
        while kalan > 0:
            kume_ici = konum % self.cluster_size
            parca = min(kalan, self.cluster_size - kume_ici)
            ofset = self._cluster_offset(konum - kume_ici)
            if ofset == 0:
                out += b"\x00" * parca
            else:
                self._fh.seek(ofset + kume_ici)
                veri = self._fh.read(parca)
                out += veri + b"\x00" * (parca - len(veri))
            konum += parca
            kalan -= parca
        return bytes(out)


# ==========================================================================
# Bicim tespiti
# ==========================================================================
def detect_format(path: str) -> str:
    """Dosya bicimini imzadan belirler: 'raw' | 'vhd' | 'vdi' | 'vmdk' | 'qcow2'."""
    try:
        boyut = os.path.getsize(path)
        with open(path, "rb") as fh:
            bas = fh.read(1024)
            if bas[:4] == QCOW_MAGIC:
                return "qcow2"
            if bas[:4] == VMDK_SPARSE_MAGIC:
                return "vmdk"
            if len(bas) >= 0x44 and struct.unpack_from("<I", bas, 0x40)[0] == VDI_MAGIC:
                return "vdi"
            if bas[:8] == VHD_COOKIE:
                return "vhd"
            metin = bas[:512].decode("latin-1", "ignore")
            if "createType" in metin or "# Disk DescriptorFile" in metin:
                return "vmdk"
            if boyut >= 512:
                fh.seek(-512, os.SEEK_END)
                if fh.read(8) == VHD_COOKIE:
                    return "vhd"
    except OSError:
        pass
    return "raw"


def open_disk(path: str, readonly: bool = False) -> BlockDevice:
    """Yola gore uygun aygit nesnesini acar (ham veya sanal disk)."""
    bicim = detect_format(path)
    if bicim == "raw":
        return DiskImage(path, readonly=readonly)
    if bicim == "vhd":
        return VhdImage(path, readonly=readonly)
    if bicim == "vdi":
        return VdiImage(path)
    if bicim == "vmdk":
        return VmdkImage(path, readonly=readonly)
    if bicim == "qcow2":
        return Qcow2Image(path)
    raise VirtualDiskError(f"Bilinmeyen disk bicimi: {bicim}")


def format_label(bicim: str) -> str:
    return {"raw": "Ham disk goruntusu (.img)", "vhd": "Microsoft VHD",
            "vdi": "VirtualBox VDI", "vmdk": "VMware VMDK",
            "qcow2": "QEMU QCOW2"}.get(bicim, bicim)
