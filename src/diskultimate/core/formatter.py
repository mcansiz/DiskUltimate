"""Bicimlendirme dagiticisi.

FAT12/16/32 saf Python ile bicimlendirilir (harici arac gerekmez).
exFAT / NTFS / ext2-3-4 icin sistemdeki `mkfs.*` araclari kullanilir:
bolum gecici seyrek bir dosyada bicimlendirilip sonuc goruntuye geri yazilir.
Boylece root yetkisi veya loop aygiti gerekmez.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, List, Optional

from .exfat import ExFatFS
from .ext import format_ext
from .ntfs import format_ntfs
from .fat import FatFS
from .image import BlockDevice
from .platform import PLATFORM_NAME, find_tool, run_tool, tool_names
from .ptable import GPT_UNUSED, human_size
from .hfsformat import HfsFormatError, format_hfsplus
from .swap import SwapError, format_swap
from .udfformat import UdfFormatError, format_udf
from .xfsformat import XfsFormatError, format_xfs
from ..i18n import mark, tr

CHUNK = 4 * 1024 * 1024


class FormatError(Exception):
    pass


@dataclass
class FsKind:
    key: str                 # 'fat32'
    label: str               # gorunen ad
    internal: bool           # saf Python destegi var mi
    tool: str = ""           # harici arac adi
    min_bytes: int = 0
    max_bytes: int = 0       # 0 = sinirsiz
    mbr_type: int = 0x83
    gpt_type: str = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"

    native_only: bool = False   # yalnizca isletim sisteminin araci (ReFS)

    @property
    def available(self) -> bool:
        if self.native_only:
            from .platform import native_format_supported
            return native_format_supported(self.key)
        return self.internal or bool(find_tool(self.key))

    def reason_for(self, size_bytes: int = 0) -> str:
        """Bu bicim neden kullanilamiyor? Bos metin = kullanilabilir.

        Arayuz tum bicimleri listeler; kullanilamayanlar bu aciklamayla birlikte
        gri gosterilir. Boylece kullanici "secenek yok" yerine "neden yok"
        bilgisini gorur.
        """
        if self.native_only and not self.available:
            from .platform import refs_format_support
            return refs_format_support()[1]
        if not self.available:
            adaylar = tool_names(self.key)
            if not adaylar:
                return tr("{} uzerinde bu bicim icin arac yok", PLATFORM_NAME)
            return tr("{} kurulu degil", tr(" veya ").join(adaylar))
        if size_bytes:
            from .ptable import human_size
            if self.min_bytes and size_bytes < self.min_bytes:
                return tr("en az {} gerekir", human_size(self.min_bytes))
            if self.max_bytes and size_bytes > self.max_bytes:
                return tr("en fazla {} destekler", human_size(self.max_bytes))
        return ""

    @property
    def tool_hint(self) -> str:
        adaylar = tool_names(self.key)
        if not adaylar:
            return tr("{} uzerinde {} icin harici arac yok",
                      PLATFORM_NAME, tr(self.label))
        return tr("gerekli arac: {}", tr(" veya ").join(adaylar))


MSBASIC = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
SWAP_GUID = "0657FD6D-A4AB-43C4-84E5-0933C84B4F4F"
MIN_SWAP_BYTES = 10 * 4096
LINUXFS = "0FC63DAF-8483-4772-8E79-3D69D8477DE4"
APPLE_HFS = "48465300-0000-11AA-AA11-00306543ECAC"

FS_KINDS: List[FsKind] = [
    FsKind("fat32", "FAT32", True, min_bytes=33 * 1024 * 1024,
           max_bytes=2 * 1024 ** 4, mbr_type=0x0C, gpt_type=MSBASIC),
    FsKind("fat16", "FAT16", True, min_bytes=2 * 1024 * 1024,
           max_bytes=4 * 1024 ** 3, mbr_type=0x0E, gpt_type=MSBASIC),
    FsKind("fat12", "FAT12", True, min_bytes=64 * 1024,
           max_bytes=32 * 1024 * 1024, mbr_type=0x01, gpt_type=MSBASIC),
    FsKind("exfat", "exFAT", True,
           min_bytes=1024 * 1024, mbr_type=0x07, gpt_type=MSBASIC),
    FsKind("ntfs", "NTFS", True,
           min_bytes=16 * 1024 * 1024, mbr_type=0x07, gpt_type=MSBASIC),
    FsKind("ext4", "ext4", True,
           min_bytes=8 * 1024 * 1024, mbr_type=0x83, gpt_type=LINUXFS),
    FsKind("ext3", "ext3", True,
           min_bytes=8 * 1024 * 1024, mbr_type=0x83, gpt_type=LINUXFS),
    FsKind("ext2", "ext2", True,
           min_bytes=1024 * 1024, mbr_type=0x83, gpt_type=LINUXFS),
    # HFS+ gunluksuz (ADR 0061); macOS gunlugu ilk baglamada acabilir.
    FsKind("hfsplus", "HFS+", True, min_bytes=1024 * 1024,
           mbr_type=0xAF, gpt_type=APPLE_HFS),
    # XFS v5 (crc, ftype, finobt, bigtime, inobtcount; ADR 0067)
    FsKind("xfs", "XFS", True, min_bytes=300 * 1024 * 1024,
           mbr_type=0x83, gpt_type=LINUXFS),
    # ReFS: yalnizca Windows'un kendi Format-Volume'u, yalnizca fiziksel disk
    # (ADR 0072). Diger platformlarda ve uygun olmayan surumlerde gri + neden.
    FsKind("refs", "ReFS", False, mbr_type=0x07, gpt_type=MSBASIC, native_only=True),
    # UDF 2.01, sabit disk yerlesimi (ADR 0064): Windows/macOS/Linux ortak
    FsKind("udf", "UDF", True, min_bytes=1024 * 1024,
           mbr_type=0x07, gpt_type=MSBASIC),
    # Gorunen ad cevrilir (arayuz `tr(kind.label)`); ayni ad fsdetect'in
    # urettigi `fs_type` oldugu icin veri olarak cevrilmemis kalir.
    FsKind("swap", mark("Linux Takas"), True, min_bytes=MIN_SWAP_BYTES,
           mbr_type=0x82, gpt_type=SWAP_GUID),
]

FS_BY_KEY = {k.key: k for k in FS_KINDS}


def all_kinds(size_bytes: int = 0) -> List[tuple]:
    """Tum dosya sistemlerini (kind, neden) ciftleri olarak dondurur.

    `neden` bos ise bicim kullanilabilir. Arayuz bu listeyi oldugu gibi gosterir.
    """
    return [(k, k.reason_for(size_bytes)) for k in FS_KINDS]


def available_kinds(size_bytes: int = 0) -> List[FsKind]:
    """Verilen boyut icin kullanilabilir dosya sistemlerini dondurur."""
    out = []
    for k in FS_KINDS:
        if not k.available:
            continue
        if size_bytes:
            if k.min_bytes and size_bytes < k.min_bytes:
                continue
            if k.max_bytes and size_bytes > k.max_bytes:
                continue
        out.append(k)
    return out


# exFAT ana + yedek onyukleme bolgesi 24 sektor tutar; ext ustblogu 1024.
# Bu araligi sifirlamak, bilinen tum onyukleme/ustblok imzalarini kapsar.
SIGNATURE_BYTES = 128 * 1024


def wipe_signatures(view: BlockDevice) -> None:
    """Bicimlendirmeden once **eski dosya sistemi imzalarini** siler.

    Neden gerekli: her dosya sistemi kendi alanini yazar ama otekinin imzasini
    silmez. Ornegin exFAT onyukleme sektoru ofset 0'dadir; ext ilk 1024 bayti
    **rezerve birakir** ve ona dokunmaz. exFAT bir bolum ext4'e cevrilince eski
    `EXFAT` imzasi yerinde kalir ve tespit birimi yanlis tanir (gercekte
    yasandi: ext4 bolum exFAT sanildi ve exFAT surucusu cokerek acildi).

    `mkfs` araclari da ayni seyi yapar (`wipefs` davranisi). Basi ve **son
    sektoru** temizlenir: NTFS yedek onyukleme sektoru orada durur.
    """
    if getattr(view, "readonly", False):
        raise FormatError(tr("Goruntu salt okunur acildi"))
    ss = view.sector_size
    head = min(SIGNATURE_BYTES, view.size)
    zero = b"\x00" * ss
    for offset in range(0, head, ss):
        view.write(offset, zero)
    if view.size >= 2 * ss:                      # son sektor (NTFS yedegi)
        view.write(view.size - ss, zero)


def format_partition(view: BlockDevice, fs_key: str, label: str = "",
                     cluster_bytes: int = 0, quick: bool = True,
                     progress: Optional[Callable[[str, int], None]] = None) -> str:
    """Bolumu istenen dosya sistemiyle bicimlendirir.

    Dondurulen deger: olusan dosya sistemi adi ('FAT32', 'ext4', ...)
    progress(mesaj, yuzde) geri cagrisi opsiyoneldir.
    """
    kind = FS_BY_KEY.get(fs_key.lower())
    if kind is None:
        raise FormatError(tr("Bilinmeyen dosya sistemi: {}", fs_key))
    if getattr(view, "readonly", False):
        raise FormatError(tr("Goruntu salt okunur acildi"))
    if kind.min_bytes and view.size < kind.min_bytes:
        raise FormatError(
            tr("{} icin en az {} gerekir",
               tr(kind.label), human_size(kind.min_bytes)))

    def report(msg: str, pct: int) -> None:
        if progress:
            progress(msg, pct)

    if kind.native_only:
        # ReFS: saf Python ya da harici mkfs yolu yok; oturum fiziksel diskte
        # Windows'un Format-Volume'unu cagirir (session._try_native_format).
        raise FormatError(kind.reason_for() or tr(
            "{} yalnizca fiziksel diskte, Windows'un kendi araciyla "
            "olusturulabilir; goruntu dosyasinda kullanilamaz.", kind.label))

    wipe_signatures(view)

    if kind.internal:
        report(tr("{} bicimlendiriliyor...", tr(kind.label)), 10)
        spc = max(1, cluster_bytes // view.sector_size) if cluster_bytes else 0
        # Bolumun diskteki baslangici onyukleme sektorune yazilir (FAT/NTFS
        # "gizli sektor", exFAT PartitionOffset). Eskiden yalnizca exFAT
        # yaziyordu; FAT ve NTFS'te alan 0 kaliyordu.
        offset = getattr(view, "start_lba", 0)
        if kind.key == "exfat":
            ExFatFS.format(view, label=label, cluster_sectors=spc,
                           partition_offset=offset, progress=progress)
        elif kind.key == "ntfs":
            format_ntfs(view, label=label,
                        cluster_size=cluster_bytes if cluster_bytes else 0,
                        progress=progress, partition_offset=offset)
        elif kind.key == "hfsplus":
            try:
                format_hfsplus(view, label=label, progress=progress)
            except HfsFormatError as exc:
                raise FormatError(str(exc)) from exc
        elif kind.key == "xfs":
            try:
                format_xfs(view, label=label, progress=progress)
            except XfsFormatError as exc:
                raise FormatError(str(exc)) from exc
        elif kind.key == "udf":
            try:
                format_udf(view, label=label, progress=progress)
            except UdfFormatError as exc:
                raise FormatError(str(exc)) from exc
        elif kind.key == "swap":
            try:
                format_swap(view, label=label)
            except SwapError as exc:
                raise FormatError(str(exc)) from exc
        elif kind.key.startswith("ext"):
            format_ext(view, version=kind.key, label=label,
                       block_size=cluster_bytes if cluster_bytes in
                       (1024, 2048, 4096) else 0, progress=progress)
        else:
            FatFS.format(view, fat_type=int(kind.key[3:]), label=label,
                         cluster_sectors=spc, quick=quick,
                         hidden_sectors=offset)
        report(tr("Tamamlandi"), 100)
        return kind.label

    return _format_external(view, kind, label, cluster_bytes, quick, report)


def _format_external(view: BlockDevice, kind: FsKind, label: str,
                     cluster_bytes: int, quick: bool,
                     report: Callable[[str, int], None]) -> str:
    tool = find_tool(kind.key)
    if not tool:
        raise FormatError(
            tr("{} bu sistemde bicimlendirilemiyor ({})", kind.label, kind.tool_hint))
    from ..paths import scratch
    tmp_path = os.path.join(scratch("format"), f"du_fmt_{os.getpid()}_{id(view):x}.img")
    try:
        report(tr("Gecici birim hazirlaniyor..."), 5)
        with open(tmp_path, "wb") as fh:
            fh.truncate(view.size)

        cmd = _build_command(tool, kind, tmp_path, label, cluster_bytes, view)
        report(tr("{} olusturuluyor ({})...", kind.label, kind.tool), 20)
        proc = run_tool(cmd)
        if proc.returncode != 0:
            raise FormatError(
                tr("{} hata verdi:\n{}",
                   os.path.basename(tool),
                   (proc.stderr or proc.stdout).strip()[:800]))

        report(tr("Bolume yaziliyor..."), 60)
        _copy_back(tmp_path, view, report)
        report(tr("Tamamlandi"), 100)
        return kind.label
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _build_command(tool: str, kind: FsKind, path: str, label: str,
                   cluster_bytes: int, view: BlockDevice) -> List[str]:
    cmd = [tool]
    if kind.key == "exfat":
        if label:
            cmd += ["-n", label[:15]]
        if cluster_bytes:
            cmd += ["-c", str(cluster_bytes)]
        cmd += [path]
    elif kind.key == "ntfs":
        cmd += ["-Q", "-F"]
        if label:
            cmd += ["-L", label[:32]]
        if cluster_bytes:
            cmd += ["-c", str(cluster_bytes)]
        cmd += [path]
    elif kind.key.startswith("ext"):
        cmd += ["-F", "-q"]
        if label:
            cmd += ["-L", label[:16]]
        if cluster_bytes:
            cmd += ["-b", str(min(max(cluster_bytes, 1024), 4096))]
        cmd += [path]
    else:
        cmd += [path]
    return cmd


def _copy_back(tmp_path: str, view: BlockDevice,
               report: Callable[[str, int], None]) -> None:
    """Gecici birimi bolume kopyalar; sifir bloklar atlanarak seyreklik korunur."""
    total = view.size
    written = 0
    with open(tmp_path, "rb") as fh:
        while written < total:
            n = min(CHUNK, total - written)
            block = fh.read(n)
            if len(block) < n:
                block += b"\x00" * (n - len(block))
            if block.strip(b"\x00"):
                view.write(written, block)
            else:
                # hedefte eski veri varsa temizle, yoksa dokunma (seyreklik korunur)
                if view.read(written, n).strip(b"\x00"):
                    view.write(written, block)
            written += n
            report(tr("Bolume yaziliyor..."), 60 + int(39 * written / total))
    flush = getattr(view, "flush", None)
    if flush:
        flush()


def wipe_partition(view: BlockDevice, sectors: int = 4096) -> None:
    """Bolumun bas ve son metaverisini siler (dosya sistemi imzasini kaldirir)."""
    n = min(sectors, view.sector_count)
    view.zero_sectors(0, n)
    if view.sector_count > 2 * n:
        view.zero_sectors(view.sector_count - n, n)
    flush = getattr(view, "flush", None)
    if flush:
        flush()
