"""Uygulama oturumu: acik disk goruntusu + bolum tablosu + dosya sistemleri.

GUI katmani yalnizca bu sinifi kullanir; alt seviye modullere dokunmaz.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from . import clone as clone_mod
from . import diagnostics
from . import convert as convert_mod
from . import recovery as recovery_mod
from . import wipe as wipe_mod
from .filesystem import FileSystemAccess, open_filesystem
from .formatter import FS_BY_KEY, format_partition, wipe_partition
from .fsdetect import FSInfo, detect
from .gpt import GPTTable
from .image import BlockDevice, DiskImage, PartitionView
from .mbr import MBRTable
from .physical import (DiskInfo, PhysicalDisk, PhysicalDiskError,
                       can_access, fill_mount_points, list_disks,
                       open_device_paths)
from .platform import (IS_WINDOWS, native_format_supported,
                       native_resize_supported, windows_format_volume,
                       windows_partition_size_limits, windows_resize_partition)
from .resize import (FsResizeInfo, ResizeError, ResizePlan, ResizeWindow,
                     apply_resize, fs_resize_info_for, plan_resize, window_for)
from .ptable import (FreeRegion, Partition, PartitionTable,
                     PartitionTableError, human_size)
from .vdisk import detect_format, format_label, open_disk
from ..i18n import tr


class SessionError(Exception):
    pass


@dataclass
class BackupPreview:
    """Bir `.dub` yedeginin geri yuklenmeden okunan icerigi.

    `partitions` bos ve `filesystem` doluysa yedek **tek bir bolumun**
    yedegidir (disk degil). `root_entries` anahtari bolum numarasidir; bolum
    tablosu olmayan yedekte `-1` kullanilir.
    """

    info: object                                 # clone.BackupInfo
    scheme: str                                  # 'mbr' | 'gpt' | ''
    partitions: List[Partition]
    filesystem: Optional[FSInfo]
    # Deger None ise icerik okunamadi, [] ise bolum gercekten bos
    root_entries: Dict[int, Optional[List[str]]]

    @property
    def is_whole_disk(self) -> bool:
        """Yedek bir **diskin** mi yoksa tek bir **bolumun** mu yedegi?

        Olcut bolum tablosunun varligi DEGILDIR: FAT onyukleme sektoru de
        0xAA55 ile biter, bu yuzden tek bir FAT bolumunun yedegi "bos MBR" gibi
        gorunur (olculdu: `bolum.dub` -> scheme='mbr', 0 bolum). Ayirt edici
        olan gercekten bolum bulunup bulunmadigidir.
        """
        return bool(self.partitions)


@dataclass
class DiskSurvey:
    """Bir fiziksel diskin **acilmadan once** okunan bolum ozeti.

    `list_disks()` hicbir sektor okumaz (guvenlik katmani 1) ve okumamalidir:
    o islev 3 saniyede bir calisir. Bolumleri gormek icin ise bolum tablosunu
    okumak sart. Bu yuzden yoklama **ayri bir adimdir**: yalnizca disk listesi
    degistiginde veya elle yenilemede yapilir, arka plan is parcaciginda
    calisir ve aygiti okuduktan hemen sonra kapatir (ADR 0026).

    `error` doluysa tablo okunamamistir (yetki yok, ortam yok, bozuk tablo);
    bu bir kusur degil bilgidir ve arayuzde oyle gosterilir.
    """

    path: str
    scheme: str = ""                                   # 'mbr' | 'gpt' | ''
    partitions: List[Partition] = None
    error: str = ""

    def __post_init__(self):
        if self.partitions is None:
            self.partitions = []

    @property
    def scheme_name(self) -> str:
        return {"mbr": "MBR", "gpt": "GPT"}.get(self.scheme,
                                                tr("Bolum tablosu yok"))


class DiskSession:
    """Acik bir `.img` dosyasini ve uzerindeki yapiyi temsil eder."""

    def __init__(self, image: BlockDevice):
        self.image = image
        self.table: Optional[PartitionTable] = None
        self._fs_cache: Dict[int, FileSystemAccess] = {}
        self._fs_info: Dict[int, FSInfo] = {}
        self.reload()

    # -- acma / olusturma ----------------------------------------------------
    @classmethod
    def open(cls, path: str, readonly: bool = False) -> "DiskSession":
        """Ham goruntuyu veya sanal disk dosyasini (VHD/VDI/VMDK/QCOW2) acar."""
        return cls(open_disk(path, readonly=readonly))

    @classmethod
    def open_physical(cls, disk, readonly: bool = True, confirm: bool = False,
                      allow_system: bool = False) -> "DiskSession":
        """Sistemdeki gercek bir diski acar.

        Varsayilan **salt okunur**. Yazma icin `readonly=False` ve `confirm=True`
        gerekir; sistem diskinde ayrica `allow_system=True`.
        """
        return cls(PhysicalDisk(disk, readonly=readonly, confirm=confirm,
                                allow_system=allow_system))

    @staticmethod
    def list_physical_disks(include_removable: bool = True):
        """Sistemdeki diskleri listeler (veri okumadan; bkz. physical.list_disks)."""
        return list_disks(include_removable=include_removable)

    @staticmethod
    def has_disk_privileges() -> bool:
        return can_access()

    @staticmethod
    def survey_disk(info) -> DiskSurvey:
        """Bir fiziksel diskin bolum tablosunu **salt okunur** okur.

        Aygit acilir, tablo okunur ve **hemen kapatilir**; hicbir sey yazilmaz.
        Uygulamanin kendi acik tuttugu disk yoklanmaz: ayni aygita ikinci bir
        tutamac acmak surucu yiginini bloklayabiliyor (ADR 0021). O durumda
        arayuz zaten oturumun kendi bolum listesine sahiptir.
        """
        path = getattr(info, "path", str(info))
        if path in open_device_paths():
            return DiskSurvey(path=path, error="uygulamada acik")
        with diagnostics.span("disk.survey", path=path):
            device = None
            try:
                device = PhysicalDisk(info, readonly=True)
                table = None
                if GPTTable.is_present(device):
                    table = GPTTable.read(device)
                elif MBRTable.is_present(device):
                    table = MBRTable.read(device)
                if table is None:
                    return DiskSurvey(path=path)
                parts = table.sorted_partitions()
                for part in parts:
                    try:
                        found = detect(PartitionView(device, part.start_lba,
                                                     part.sector_count))
                    except Exception:
                        continue
                    part.fs_type = found.fs_type
                    part.fs_label = found.label
                    part.fs_used = found.used_bytes
                    part.fs_total = found.total_bytes
                # Baglama noktalari disk listesinden gelir (OS sorgusu yok).
                fill_mount_points(info if hasattr(info, "mount_map") else None,
                                  parts)
                return DiskSurvey(path=path, scheme=table.scheme,
                                  partitions=parts)
            except Exception as exc:
                return DiskSurvey(path=path, error=str(exc))
            finally:
                if device is not None:
                    try:
                        device.close()
                    except Exception:
                        pass

    @property
    def is_physical(self) -> bool:
        return isinstance(self.image, PhysicalDisk)

    @property
    def disk_info(self):
        """Fiziksel disk ise DiskInfo, degilse None."""
        return self.image.info if self.is_physical else None

    @property
    def image_format(self) -> str:
        """Acik kaynagin bicimi: 'physical' | 'raw' | 'vhd' | 'vdi' | ..."""
        if self.is_physical:
            return "physical"
        return detect_format(self.path)

    @property
    def format_name(self) -> str:
        if self.is_physical:
            info = self.image.info
            return tr("Fiziksel disk — {}", info.model or info.name)
        return format_label(self.image_format)

    @classmethod
    def create(cls, path: str, size_bytes: int, sparse: bool = True,
               scheme: str = "", overwrite: bool = False) -> "DiskSession":
        image = DiskImage.create(path, size_bytes, sparse=sparse,
                                 overwrite=overwrite)
        session = cls(image)
        if scheme:
            session.create_table(scheme)
        return session

    # -- durum ---------------------------------------------------------------
    @property
    def path(self) -> str:
        return getattr(self.image, "path", "")

    @property
    def name(self) -> str:
        if self.is_physical:
            return self.image.info.name
        return os.path.basename(self.path)

    @property
    def readonly(self) -> bool:
        return self.image.readonly

    @property
    def readonly_reason(self) -> str:
        """Kaynak neden salt okunur? Bos metin = yazilabilir.

        Arayuz bu metni kullaniciya gosterir; "degisiklik yapilamaz" demek
        yetmez, **neden** ve **ne yapmasi gerektigi** soylenmelidir.
        """
        if not self.readonly:
            return ""
        if self.is_physical:
            # "Yazma modunda acin" tavsiyesi kaldirildi: oyle bir secim artik
            # yok (ADR 0025). Disk hep salt okunur acilir ve yazma yetkisi
            # yalnizca bekleyen islemler uygulanirken alinir.
            return (tr("Fiziksel diskler guvenlik gerekcesiyle salt okunur acilir. "
                    "Degisiklikler bekleyen islem olarak birikir ve diske "
                    "ancak Uygula ile yazilir."))
        fmt = self.image_format
        if fmt in ("vdi", "qcow2"):
            return (tr("{} bu surumde yalnizca okunabilir; yazma destegi yol "
                       "haritasinda.", self.format_name))
        if fmt == "vmdk":
            return (tr("Seyrek (sparse) VMDK bu surumde salt okunur. Duz (flat) "
                    "VMDK ve VHD yazilabilir."))
        return (getattr(self.image, "readonly_reason", "")
                or "Kaynak salt okunur acildi")

    @property
    def scheme(self) -> str:
        return self.table.scheme if self.table else "none"

    @property
    def scheme_name(self) -> str:
        return {"mbr": "MBR", "gpt": "GPT"}.get(self.scheme,
                                                tr("Bolum tablosu yok"))

    @property
    def partitions(self) -> List[Partition]:
        return self.table.sorted_partitions() if self.table else []

    def free_regions(self, min_bytes: int = 1024 * 1024) -> List[FreeRegion]:
        if not self.table:
            return []
        return self.table.free_regions(min_sectors=max(1, min_bytes // self.image.sector_size))

    # -- tablo islemleri -----------------------------------------------------
    def notify_os(self) -> bool:
        """Fiziksel diskte bolum tablosu degisikligini isletim sistemine bildirir.

        **Yalnizca tum islemler bittikten sonra** cagrilmalidir. Windows'ta bu
        cagri aygiti yeniden taratir ve o anda ACIK olan disk tutamacini
        gecersiz kilar; islem ortasinda cagrilirsa sonraki yazma
        `ERROR_NO_SUCH_DEVICE` (433) ile basarisiz olur. Bu yuzden normalde
        `PhysicalDisk.close()` icinden, kapanis sirasinda cagrilir.
        """
        if self.is_physical and not self.readonly:
            try:
                return self.image.rescan_partitions()
            except Exception:
                return False
        return False

    @diagnostics.timed("session.reload")
    def reload(self) -> None:
        """Bolum tablosunu ve dosya sistemi bilgilerini diskten yeniden okur."""
        self.close_filesystems()
        self.table = None
        try:
            if GPTTable.is_present(self.image):
                self.table = GPTTable.read(self.image)
            elif MBRTable.is_present(self.image):
                self.table = MBRTable.read(self.image)
        except PartitionTableError:
            self.table = None
        self._fs_info.clear()
        if self.table:
            for part in self.table.partitions:
                info = self.detect_fs(part)
                part.fs_type = info.fs_type
                part.fs_label = info.label
                part.fs_used = info.used_bytes
                part.fs_total = info.total_bytes
        if self.is_physical and self.table:
            fill_mount_points(self.disk_info, self.table.partitions)

    def create_table(self, scheme: str) -> PartitionTable:
        """Yeni bolum tablosu kurar (mevcut bolumler kaybolur)."""
        self._require_writable()
        scheme = scheme.lower()
        self.close_filesystems()
        if scheme == "mbr":
            self.table = MBRTable.create(self.image)
        elif scheme == "gpt":
            self.table = GPTTable.create(self.image)
        else:
            raise SessionError(tr("Bilinmeyen sema: {}", scheme))
        self.reload()
        return self.table

    def clear_table(self) -> None:
        """Bolum tablosunu tamamen siler."""
        self._require_writable()
        self.close_filesystems()
        self.image.zero_sectors(0, min(34, self.image.sector_count))
        if self.image.sector_count > 68:
            self.image.zero_sectors(self.image.sector_count - 34, 34)
        self.image.flush()
        self.reload()

    # -- bolum islemleri -----------------------------------------------------
    def create_partition(self, start_lba: int, sector_count: int,
                         fs_key: str = "", label: str = "",
                         name: str = "", bootable: bool = False,
                         type_id: int = 0, type_guid: str = "",
                         logical: Optional[bool] = None,
                         progress: Optional[Callable[[str, int], None]] = None) -> Partition:
        """Bolum olusturur ve istege bagli olarak bicimlendirir."""
        self._require_table()
        self._require_writable()
        kind = FS_BY_KEY.get(fs_key.lower()) if fs_key else None
        if self.scheme == "mbr":
            tid = type_id or (kind.mbr_type if kind else 0x83)
            part = self.table.add_partition(start_lba, sector_count,
                                            type_id=tid, bootable=bootable,
                                            logical=logical)
        else:
            guid = type_guid or (kind.gpt_type if kind else
                                 "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7")
            part = self.table.add_partition(start_lba, sector_count,
                                            type_guid=guid, name=name or label,
                                            bootable=bootable)
        if fs_key:
            try:
                self.format_partition(part.index, fs_key, label, progress=progress)
            except Exception:
                # bicimlendirme basarisizsa bolum tabloda birakilmaz
                try:
                    self.table.delete_partition(part.index)
                    self.reload()
                except Exception:
                    pass
                raise
        else:
            wipe_partition(self.view(part))
            self.reload()
        return self.table.get(part.index) if self.table else part

    def delete_partition(self, index: int, wipe: bool = True) -> None:
        self._require_table()
        self._require_writable()
        part = self.table.get(index)
        self._fs_cache.pop(index, None)
        if wipe:
            try:
                wipe_partition(self.view(part))
            except Exception:
                pass
        self.table.delete_partition(index)
        self.reload()

    def _try_native_format(self, index: int, fs_key: str, label: str,
                           cluster_bytes: int, progress) -> Optional[str]:
        """Isletim sisteminin kendi bicimlendiricisini dener (yalnizca fiziksel disk).

        Windows'ta ntfs-3g bulunmadigi icin NTFS'i en guvenilir sekilde Windows'un
        kendi araci olusturur. Basarisiz olursa None doner ve dahili (saf Python)
        yol devreye girer.
        """
        if not (self.is_physical and IS_WINDOWS and native_format_supported(fs_key)):
            return None
        numaralar = "".join(ch for ch in self.image.info.name if ch.isdigit())
        if not numaralar:
            return None
        if progress:
            progress(tr("Windows bicimlendiricisi cagriliyor..."), 20)
        # Bolum tablosu degisikliginin goruldugunden emin olmak icin bildir
        try:
            self.image.rescan_partitions()
        except Exception:
            pass
        ok, message = windows_format_volume(int(numaralar), index, fs_key, label,
                                          cluster_bytes)
        if not ok:
            return None
        if progress:
            progress(message, 100)
        return {"ntfs": "NTFS", "exfat": "exFAT", "fat32": "FAT32",
                "fat16": "FAT16"}.get(fs_key, fs_key.upper())

    def format_partition(self, index: int, fs_key: str, label: str = "",
                         cluster_bytes: int = 0, quick: bool = True,
                         progress: Optional[Callable[[str, int], None]] = None) -> str:
        self._require_table()
        self._require_writable()
        part = self.table.get(index)
        self._fs_cache.pop(index, None)

        yerel = self._try_native_format(index, fs_key, label, cluster_bytes,
                                        progress)
        if yerel:
            kind = FS_BY_KEY.get(fs_key.lower())
            if kind:
                if self.scheme == "mbr":
                    part.type_id = kind.mbr_type
                else:
                    part.type_guid = kind.gpt_type
                self.table.write()
            self.reload()
            return yerel
        result = format_partition(self.view(part), fs_key, label=label,
                                  cluster_bytes=cluster_bytes, quick=quick,
                                  progress=progress)
        kind = FS_BY_KEY.get(fs_key.lower())
        if kind:
            if self.scheme == "mbr":
                part.type_id = kind.mbr_type
            else:
                part.type_guid = kind.gpt_type
            self.table.write()
        self.reload()
        return result

    def set_bootable(self, index: int, value: bool = True) -> None:
        self._require_table()
        self._require_writable()
        if hasattr(self.table, "set_bootable"):
            self.table.set_bootable(index, value)
        else:
            self.table.get(index).bootable = value
            self.table.write()
        self.reload()

    def set_partition_name(self, index: int, name: str) -> None:
        self._require_table()
        self._require_writable()
        if self.scheme != "gpt":
            raise SessionError(tr("Bolum adi yalnizca GPT semasinda desteklenir"))
        self.table.set_name(index, name)
        self.reload()

    def set_partition_type(self, index: int, type_id: int = 0,
                           type_guid: str = "") -> None:
        self._require_table()
        self._require_writable()
        if self.scheme == "mbr":
            self.table.set_type(index, type_id)
        else:
            self.table.set_type(index, type_guid)
        self.reload()

    # -- erisim --------------------------------------------------------------
    def view(self, part: Partition) -> PartitionView:
        return PartitionView(self.image, part.start_lba, part.sector_count,
                             label=part.display_name)

    def view_by_index(self, index: int) -> PartitionView:
        self._require_table()
        return self.view(self.table.get(index))

    def detect_fs(self, part: Partition) -> FSInfo:
        if part.index in self._fs_info:
            return self._fs_info[part.index]
        try:
            with diagnostics.span("session.detect_fs", part=part.index):
                info = detect(self.view(part))
        except Exception:
            info = FSInfo()
        self._fs_info[part.index] = info
        return info

    def filesystem(self, index: int) -> Optional[FileSystemAccess]:
        """Bolumun dosya sistemi erisimini dondurur (onbellekli)."""
        self._require_table()
        if index in self._fs_cache:
            return self._fs_cache[index]
        part = self.table.get(index)
        with diagnostics.span("session.open_filesystem", part=index):
            fs = open_filesystem(self.view(part), self.detect_fs(part))
        if fs is not None:
            self._fs_cache[index] = fs
        return fs

    def close_filesystems(self) -> None:
        for fs in self._fs_cache.values():
            try:
                fs.flush()
            except Exception:
                pass
        self._fs_cache.clear()
        self._fs_info.clear()

    # -- ham erisim ----------------------------------------------------------
    def read_sector(self, lba: int, count: int = 1) -> bytes:
        return self.image.read_sectors(lba, count)

    def write_sector(self, lba: int, data: bytes) -> None:
        self._require_writable()
        self.image.write_sectors(lba, data)
        self.image.flush()

    # -- onyukleme kodu ------------------------------------------------------
    def boot_code(self):
        """Ilk sektordeki onyukleme kodunun kimligi (`bootloader.BootCode`)."""
        from .bootloader import identify_boot_code

        return identify_boot_code(self.read_sector(0))

    def clear_boot_code(self) -> None:
        """Ilk 440 bayti sifirlar — bolum tablosuna **dokunmaz**.

        GRUB'u bir diskten kaldirmanin dogru yolu budur. `exxos-easy-grub-
        manager` bunu `dd if=/dev/zero of=$dev bs=440 count=1` ile yapiyordu;
        burada ayni is, fiziksel diskin butun koruma katmanlarindan gecerek
        (`_require_writable`, sistem diski onayi, bagli bolum uyarisi)
        yapilir ve **bekleyen islem kuyruguna** girer.
        """
        from .bootloader import clear_boot_code

        self._require_writable()
        with diagnostics.span("session.clear_boot_code", reason=self.name):
            sector = bytearray(self.read_sector(0))
            self.write_sector(0, clear_boot_code(bytes(sector)))
            diagnostics.info(f"onyukleme kodu silindi: {self.name}")

    def resize_image(self, new_size: int) -> None:
        self._require_writable()
        if self.is_physical:
            raise SessionError(tr("Fiziksel diskin boyutu degistirilemez"))
        if not isinstance(self.image, DiskImage):
            raise SessionError(
                tr("{} dosyalarinin boyutu bu surumde degistirilemez",
                   self.format_name))
        self.close_filesystems()
        self.image.resize(new_size)
        if self.scheme == "gpt" and self.table:
            self.table.write()   # yedek GPT yeni sona tasinir
        self.reload()

    # ======================================================================
    # Bolum yeniden boyutlandirma / tasima
    # ======================================================================
    def resize_window(self, index: int) -> ResizeWindow:
        """Bolumun icinde hareket edebilecegi alan (komsu bos alanlar dahil)."""
        self._require_table()
        return window_for(self.table, self.table.get(index))

    def resize_info(self, index: int) -> FsResizeInfo:
        """Bolumdeki dosya sisteminin boyutlandirma sinirlari."""
        self._require_table()
        part = self.table.get(index)
        info = fs_resize_info_for(self, part)
        if info.kind == "native":
            ok, lower, upper, _ = self._native_size_limits(index)
            if ok:
                ss = self.table.sector_size
                info.min_sectors = max(1, lower // ss)
                info.max_sectors = upper // ss
        return info

    def plan_resize(self, index: int, new_start_lba: int,
                    new_sector_count: int) -> ResizePlan:
        """Istenen yerlesimi dogrular; uygulamadan once cagrilir."""
        self._require_table()
        return plan_resize(self, index, new_start_lba, new_sector_count)

    def resize_partition(self, index: int, new_start_lba: int,
                         new_sector_count: int, confirm: bool = False,
                         progress: Optional[Callable[[str, int], None]] = None
                         ) -> Partition:
        """Bolumu yeniden boyutlandirir ve/veya tasir.

        Yikici bir islemdir: `confirm=True` verilmeden calismaz. Fiziksel
        disklerde once isletim sisteminin kendi boyutlandiricisi denenir
        (NTFS/ext saf Python'da boyutlandirilamadigi icin).
        """
        self._require_table()
        self._require_writable()
        if not confirm:
            raise SessionError(tr("Yeniden boyutlandirma icin onay gerekli "
                               "(confirm=True)"))
        plan = self.plan_resize(index, new_start_lba, new_sector_count)
        if not plan.changed:
            return self.table.get(index)
        if plan.fs.kind == "native" and not plan.moves:
            result = self._try_native_resize(index, new_sector_count, progress)
            if result:
                return self.table.get(index)
        return apply_resize(self, plan, progress=progress)

    def _native_size_limits(self, index: int) -> tuple:
        """Windows'un bolum icin bildirdigi (tamam, en_kucuk, en_buyuk, mesaj)."""
        if not (self.is_physical and native_resize_supported()):
            return False, 0, 0, tr("Yerel boyutlandirici yok")
        info = self.disk_info
        numara = getattr(info, "disk_number", None)
        if numara is None:
            return False, 0, 0, tr("Disk numarasi bilinmiyor")
        return windows_partition_size_limits(numara, index)

    def _try_native_resize(self, index: int, sector_count: int,
                           progress) -> bool:
        """Windows `Resize-Partition` yolu; basarisizsa False doner."""
        if not (self.is_physical and native_resize_supported()):
            return False
        info = self.disk_info
        numara = getattr(info, "disk_number", None)
        if numara is None:
            return False
        if progress:
            progress(tr("Windows boyutlandiricisi calisiyor..."), 20)
        ok, message = windows_resize_partition(
            numara, index, sector_count * self.table.sector_size)
        if not ok:
            raise SessionError(tr("Windows boyutlandiricisi basarisiz: {}", message))
        if progress:
            progress(tr("Yenileniyor..."), 90)
        self.reload()
        if progress:
            progress(tr("Tamamlandi"), 100)
        return True

    # ======================================================================
    # Sema donusumu
    # ======================================================================
    def can_convert_to(self, scheme: str) -> tuple:
        """Hedef semaya donusum yapilabilir mi? (uygun_mu, aciklama)"""
        if not self.table:
            return False, tr("Once bir bolum tablosu olusturun")
        if self.table.scheme == scheme:
            return False, tr("Tablo zaten {} biciminde", scheme.upper())
        if scheme == "gpt":
            return convert_mod.check_mbr_to_gpt(self.image, self.table)
        if scheme == "mbr":
            return convert_mod.check_gpt_to_mbr(self.image, self.table)
        return False, tr("Bilinmeyen sema: {}", scheme)

    def convert_scheme(self, scheme: str, progress=None) -> PartitionTable:
        """Bolum tablosunu MBR <-> GPT donusturur (veri yerinde kalir)."""
        self._require_table()
        self._require_writable()
        self.close_filesystems()
        if scheme == "gpt":
            convert_mod.mbr_to_gpt(self.image, self.table, progress=progress)
        elif scheme == "mbr":
            convert_mod.gpt_to_mbr(self.image, self.table, progress=progress)
        else:
            raise SessionError(tr("Bilinmeyen sema: {}", scheme))
        self.reload()
        return self.table

    def alignment_report(self) -> List[dict]:
        """Bolumlerin 4K / 1 MiB hizalama durumu."""
        if not self.table:
            return []
        return convert_mod.alignment_report(self.table, self.image.sector_size)

    # ======================================================================
    # Yedekleme / geri yukleme / klonlama
    # ======================================================================
    def backup_partition(self, index: int, dest_path: str, compress: bool = True,
                         progress=None, remark: str = "",
                         level: int = clone_mod.LEVEL_NORMAL):
        part = self.table.get(index) if self.table else None
        if part is None:
            raise SessionError(tr("Bolum bulunamadi"))
        return clone_mod.backup(self.view(part), dest_path, compress=compress,
                                fs_type=part.fs_type, label=part.fs_label or part.name,
                                progress=progress, remark=remark, level=level)

    def backup_disk(self, dest_path: str, compress: bool = True, progress=None,
                    remark: str = "", level: int = clone_mod.LEVEL_NORMAL):
        return clone_mod.backup(self.image, dest_path, compress=compress,
                                fs_type=self.scheme_name, label=self.name,
                                progress=progress, remark=remark, level=level)

    def restore_partition(self, index: int, src_path: str, progress=None):
        self._require_writable()
        part = self.table.get(index) if self.table else None
        if part is None:
            raise SessionError(tr("Bolum bulunamadi"))
        self._fs_cache.pop(index, None)
        result = clone_mod.restore(src_path, self.view(part), progress=progress)
        self.reload()
        return result

    def restore_disk(self, src_path: str, progress=None):
        self._require_writable()
        self.close_filesystems()
        result = clone_mod.restore(src_path, self.image, progress=progress)
        self.reload()
        return result

    def clone_to(self, dest_path: str, size_bytes: int = 0, progress=None) -> str:
        """Tum goruntuyu yeni bir dosyaya klonlar."""
        return clone_mod.clone_to_new_image(self.image, dest_path,
                                            size_bytes=size_bytes, progress=progress)

    def clone_partition_to(self, index: int, target_index: int, progress=None) -> int:
        """Bir bolumu ayni goruntudeki baska bir bolume kopyalar."""
        self._require_writable()
        kaynak = self.table.get(index)
        target = self.table.get(target_index)
        if kaynak.index == target.index:
            raise SessionError(tr("Kaynak ve hedef ayni bolum"))
        self._fs_cache.pop(target_index, None)
        result = clone_mod.clone(self.view(kaynak), self.view(target), progress=progress)
        self.reload()
        return result

    @staticmethod
    def backup_info(path: str):
        return clone_mod.read_backup_info(path)

    @staticmethod
    def backup_preview(path: str) -> "BackupPreview":
        """Yedegin **icerigini** geri yuklemeden ozetler.

        `backup_info` yalnizca baslgi okur: boyut, tarih, sikistirma. Bu islev
        bir adim ileri gider ve yedegi `DubImage` uzerinden **gercekten acar**:
        bolum tablosu cozulur, her bolumun dosya sistemi tespit edilir ve kok
        klasordeki ilk girisler listelenir. Hicbir sey diske yazilmaz.

        Boylece kullanici "bu yedekte ne var?" sorusunu, yedegi acmadan veya
        bir diske yazmadan yanitlayabilir.
        """
        session = DiskSession.open(path)
        try:
            if not session.is_backup:
                raise SessionError(tr("Bu dosya bir DiskUltimate yedegi degil"))
            partitions = list(session.partitions)
            entries: Dict[int, Optional[List[str]]] = {}
            for part in partitions:
                entries[part.index] = DiskSession._root_names(
                    session.filesystem(part.index))
            fs_info = None
            if not partitions:
                # Bolum tablosu yok: yedek tek bir BOLUMUN yedegidir, dosya
                # sistemi dogrudan goruntunun basindadir.
                fs_info = detect(session.image)
                entries[-1] = DiskSession._root_names(
                    open_filesystem(session.image, fs_info))
            return BackupPreview(info=session.image.info,
                                 scheme=session.scheme,
                                 partitions=partitions,
                                 filesystem=fs_info,
                                 root_entries=entries)
        finally:
            session.close()

    @staticmethod
    def _root_names(fs, limit: int = 200) -> Optional[List[str]]:
        """Kok klasordeki girisler; okunamiyorsa **None**.

        `None` ile `[]` ayrimi onemlidir: biri "icerigi okuyamiyoruz", oteki
        "bolum gercekten bos". Ikisini ayni gostermek kullaniciyi yaniltir —
        bos bir NTFS bolumu "desteklenmiyor" sanilirdi.

        Onizleme bilgidir, garanti degildir: desteklenmeyen veya bozuk bir dosya
        sistemi yuzunden **butun pencere** kaybolmamalidir, bu yuzden hata
        yutulur ve `None` donulur.
        """
        if fs is None or not getattr(fs, "readable", False):
            return None
        try:
            nodes = fs.listdir("/")
        except Exception:
            return None
        out = []
        for node in nodes[:limit]:
            out.append(f"{node.name}/" if node.is_dir else node.name)
        return out

    @staticmethod
    def is_backup_file(path: str) -> bool:
        """Dosya bir `.dub` yedegi mi (imzaya bakar)."""
        return clone_mod.is_backup_file(path)

    @property
    def is_backup(self) -> bool:
        """Acik oturum bir `.dub` yedegi mi? (salt okunur, gezilebilir)"""
        return isinstance(self.image, clone_mod.DubImage)

    @staticmethod
    def backup_physical(disk, dest_path: str, compress: bool = True,
                        progress=None, remark: str = "",
                        level: int = clone_mod.LEVEL_NORMAL):
        """Fiziksel diski **salt okunur** acip `.dub` dosyasina yedekler.

        Yedek almak icin diski oturum olarak acmak gerekmez: okumak zararsizdir
        ve aygit is bitince hemen kapatilir. Boylece kullanici yedekleme
        penceresinden herhangi bir diski dogrudan secebilir (ADR 0032).
        """
        device = PhysicalDisk(disk, readonly=True)
        try:
            return clone_mod.backup(device, dest_path, compress=compress,
                                    fs_type="", label=disk.name,
                                    progress=progress, remark=remark,
                                    level=level)
        finally:
            device.close()

    @staticmethod
    def restore_to_physical(src_path: str, disk, allow_system: bool = False,
                            progress=None):
        """Yedegi **fiziksel diske** yazar.

        Fiziksel disk guvenlik kapilarinin tamamindan gecer: `confirm=True` ve
        gerekiyorsa `allow_system=True` olmadan aygit yazma modunda acilmaz
        (bkz. ADR 0014). Yazma bitince isletim sistemine bolum tablosu
        degisikligi bildirilir.
        """
        device = PhysicalDisk(disk, readonly=False, confirm=True,
                              allow_system=allow_system)
        try:
            return clone_mod.restore(src_path, device, progress=progress)
        finally:
            device.close()

    @staticmethod
    def restore_to_new_image(src_path: str, dest_path: str, progress=None) -> str:
        """Yedegi **yeni** bir goruntu dosyasina acar ve yolunu dondurur.

        Mevcut hicbir disk veya goruntu uzerine yazilmaz; hedef dosya yedegin
        kaydettigi boyutta olusturulur. `restore_disk`ten farki, once hedefi
        yaratmasidir — kullanici "yedegi acmak" istediginde beklenen islem budur.
        """
        info = clone_mod.read_backup_info(src_path)
        image = DiskImage.create(dest_path, info.total_bytes, overwrite=True)
        try:
            clone_mod.restore(src_path, image, progress=progress)
        finally:
            image.close()
        return dest_path

    # ======================================================================
    # Guvenli silme
    # ======================================================================
    def wipe_partition_data(self, index: int, method: str = "zero", progress=None):
        self._require_writable()
        part = self.table.get(index) if self.table else None
        if part is None:
            raise SessionError(tr("Bolum bulunamadi"))
        self._fs_cache.pop(index, None)
        result = wipe_mod.wipe_device(self.view(part), method=method, progress=progress)
        self.reload()
        return result

    def wipe_disk(self, method: str = "zero", progress=None):
        self._require_writable()
        self.close_filesystems()
        result = wipe_mod.wipe_device(self.image, method=method, progress=progress)
        self.reload()
        return result

    def wipe_free_space(self, index: int, progress=None):
        fs = self.filesystem(index)
        if fs is None:
            raise SessionError(tr("Bolumde okunabilir dosya sistemi yok"))
        result = wipe_mod.wipe_free_space(fs, progress=progress)
        self.reload()
        return result

    # ======================================================================
    # Kurtarma
    # ======================================================================
    def scan_deleted(self, index: int, progress=None):
        """Bolumdeki silinmis dosya girislerini tarar."""
        fs = self.filesystem(index)
        if fs is None or not hasattr(fs, "fs"):
            raise SessionError(
                tr("Bu bolumde silinmis dosya taramasi desteklenmiyor "
                "(yalnizca FAT ve exFAT)"))
        return recovery_mod.scan_deleted(fs.fs, progress=progress)

    def recover_deleted(self, index: int, item, dest_path: str) -> int:
        fs = self.filesystem(index)
        if fs is None or not hasattr(fs, "fs"):
            raise SessionError(tr("Kurtarma desteklenmiyor"))
        return recovery_mod.recover_deleted(fs.fs, item, dest_path)

    def scan_lost_partitions(self, deep: bool = False, progress=None):
        """Tabloda olmayan dosya sistemlerini arar."""
        bilinen = [(p.start_lba, p.end_lba) for p in self.partitions]
        return recovery_mod.scan_lost_partitions(
            self.image, deep=deep, progress=progress, known_ranges=bilinen)

    def adopt_lost_partition(self, lost, fs_key: str = "") -> Partition:
        """Taramada bulunan bir bolumu tabloya ekler (bicimlendirmeden)."""
        self._require_table()
        self._require_writable()
        return self.create_partition(lost.start_lba, lost.sector_count,
                                     fs_key="", name=lost.label or "")

    def carve_files(self, index: int = -1, keys=None, progress=None):
        """Imza tabanli dosya kurtarma; index<0 ise tum goruntu taranir."""
        target = self.image if index < 0 else self.view(self.table.get(index))
        return recovery_mod.carve_files(target, keys=keys, progress=progress)

    def extract_carved(self, item, dest_dir: str, index: int = -1) -> str:
        target = self.image if index < 0 else self.view(self.table.get(index))
        return recovery_mod.extract_carved(target, item, dest_dir)

    # -- ozet ----------------------------------------------------------------
    def summary(self) -> Dict[str, str]:
        used = sum(p.size for p in self.partitions if not p.logical)
        summary = {
            tr("Dosya"): self.path,
            tr("Bicim"): self.format_name,
            tr("Boyut"): human_size(self.image.size),
            tr("Sektor"): f"{self.image.sector_count} x {self.image.sector_size} B",
            tr("Bolum tablosu"): self.scheme_name,
            tr("Bolum sayisi"): str(len(self.partitions)),
            tr("Bolumlenmis"): f"{human_size(used)} (%{100*used/max(1,self.image.size):.1f})",
            tr("Erisim"): self._access_text(),
        }
        if self.is_physical:
            info = self.image.info
            summary[tr("Aygit")] = info.path
            summary[tr("Durum")] = info.risk_text
        return summary

    # ======================================================================
    # Yazma moduna gecis (bekleyen islem kuyrugu icin)
    # ======================================================================
    def can_become_writable(self) -> tuple:
        """(Yazma moduna gecilebilir mi, gecilemiyorsa neden).

        Kaynak salt okunur aciliyorsa bu **her zaman** bir kusur degildir:
        `.dub` yedegi bir arsivdir, VDI/QCOW2 bu surumde yazilamaz. Bunlar
        gecilemez; fiziksel disk ve yazilabilir goruntu dosyasi gecilebilir.
        """
        if not self.readonly:
            return True, ""
        if self.is_backup:
            return False, (tr("Yedek dosyasi (.dub) bir arsivdir; uzerine "
                           "yazilamaz. Yedegi bir diske veya yeni bir "
                           "goruntuye yazin."))
        if self.is_physical:
            return True, ""
        fmt = self.image_format
        if fmt in ("vdi", "qcow2"):
            return False, (tr("{} bu surumde yalnizca okunabilir; yazma "
                              "destegi yol haritasinda.", self.format_name))
        if fmt == "vmdk" and getattr(self.image, "readonly", False):
            return False, (tr("Seyrek (sparse) VMDK bu surumde salt okunur."))
        return True, ""

    def become_writable(self, confirm: bool = False,
                        allow_system: bool = False) -> None:
        """Ayni kaynagi **yazma modunda** yeniden acar.

        Bekleyen islem kuyrugunun temeli budur: kaynak hep salt okunur acilir,
        yazma yetkisi yalnizca kuyruk uygulanacagi anda ve tek seferde alinir
        (ADR 0025). Fiziksel diskin butun koruma katmanlari burada calisir —
        `confirm` ve gerekiyorsa `allow_system` olmadan aygit acilmaz.

        Zaten yazilabilirse hicbir sey yapmaz.
        """
        if not self.readonly:
            return
        ok, reason = self.can_become_writable()
        if not ok:
            raise SessionError(reason)
        if self.is_physical:
            info = self.image.info
            self.close_filesystems()
            self.image.close()
            self.image = PhysicalDisk(info, readonly=False, confirm=confirm,
                                      allow_system=allow_system)
        else:
            path = self.path
            self.close_filesystems()
            self.image.close()
            self.image = open_disk(path, readonly=False)
            if self.image.readonly:
                raise SessionError(
                    f"{os.path.basename(path)} yazma modunda acilamadi: "
                    + (getattr(self.image, "readonly_reason", "")
                       or "dosya baska bir program tarafindan kullaniliyor "
                          "olabilir"))
        diagnostics.info(f"kaynak yazma moduna alindi: {self.name}")
        self.reload()

    def close(self) -> None:
        self.close_filesystems()
        self.image.close()

    # -- ic yardimcilar ------------------------------------------------------
    def _require_table(self) -> None:
        if not self.table:
            raise SessionError(tr("Once bir bolum tablosu olusturun (MBR veya GPT)"))

    def _access_text(self) -> str:
        """Bilgi panelindeki "Erisim" satiri.

        "Salt okunur" demek artik yetmez: kaynaklarin tamami boyle acilir
        (ADR 0025). Ayirt edici olan, **degistirilebilir olup olmadigidir**.
        """
        if not self.readonly:
            return tr("Okuma/Yazma (acik)")
        can_write, why_not = self.can_become_writable()
        if can_write:
            return tr("Salt okunur — degisiklikler Uygula ile yazilir")
        return tr("Degistirilemez — {}", why_not)

    def _require_writable(self) -> None:
        if self.image.readonly:
            raise SessionError(self.readonly_reason or "Kaynak salt okunur acildi")

    def __enter__(self) -> "DiskSession":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
