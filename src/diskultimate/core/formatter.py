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
from .ptable import GPT_UNUSED

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

    @property
    def available(self) -> bool:
        return self.internal or bool(find_tool(self.key))

    def reason_for(self, size_bytes: int = 0) -> str:
        """Bu bicim neden kullanilamiyor? Bos metin = kullanilabilir.

        Arayuz tum bicimleri listeler; kullanilamayanlar bu aciklamayla birlikte
        gri gosterilir. Boylece kullanici "secenek yok" yerine "neden yok"
        bilgisini gorur.
        """
        if not self.available:
            adaylar = tool_names(self.key)
            if not adaylar:
                return f"{PLATFORM_NAME} uzerinde bu bicim icin arac yok"
            return f"{' veya '.join(adaylar)} kurulu degil"
        if size_bytes:
            from .ptable import human_size
            if self.min_bytes and size_bytes < self.min_bytes:
                return f"en az {human_size(self.min_bytes)} gerekir"
            if self.max_bytes and size_bytes > self.max_bytes:
                return f"en fazla {human_size(self.max_bytes)} destekler"
        return ""

    @property
    def tool_hint(self) -> str:
        adaylar = tool_names(self.key)
        if not adaylar:
            return f"{PLATFORM_NAME} uzerinde {self.label} icin harici arac yok"
        return f"gerekli arac: {' veya '.join(adaylar)}"


MSBASIC = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
LINUXFS = "0FC63DAF-8483-4772-8E79-3D69D8477DE4"

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
        raise FormatError("Goruntu salt okunur acildi")
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
        raise FormatError(f"Bilinmeyen dosya sistemi: {fs_key}")
    if getattr(view, "readonly", False):
        raise FormatError("Goruntu salt okunur acildi")
    if kind.min_bytes and view.size < kind.min_bytes:
        raise FormatError(
            f"{kind.label} icin en az {kind.min_bytes // (1024*1024)} MB gerekir")

    def report(msg: str, pct: int) -> None:
        if progress:
            progress(msg, pct)

    wipe_signatures(view)

    if kind.internal:
        report(f"{kind.label} bicimlendiriliyor...", 10)
        spc = max(1, cluster_bytes // view.sector_size) if cluster_bytes else 0
        if kind.key == "exfat":
            offset = getattr(view, "start_lba", 0)
            ExFatFS.format(view, label=label, cluster_sectors=spc,
                           partition_offset=offset, progress=progress)
        elif kind.key == "ntfs":
            format_ntfs(view, label=label,
                        cluster_size=cluster_bytes if cluster_bytes else 0,
                        progress=progress)
        elif kind.key.startswith("ext"):
            format_ext(view, version=kind.key, label=label,
                       block_size=cluster_bytes if cluster_bytes in
                       (1024, 2048, 4096) else 0, progress=progress)
        else:
            FatFS.format(view, fat_type=int(kind.key[3:]), label=label,
                         cluster_sectors=spc, quick=quick)
        report("Tamamlandi", 100)
        return kind.label

    return _format_external(view, kind, label, cluster_bytes, quick, report)


def _format_external(view: BlockDevice, kind: FsKind, label: str,
                     cluster_bytes: int, quick: bool,
                     report: Callable[[str, int], None]) -> str:
    tool = find_tool(kind.key)
    if not tool:
        raise FormatError(
            f"{kind.label} bu sistemde bicimlendirilemiyor ({kind.tool_hint})")
    from ..paths import scratch
    tmp_path = os.path.join(scratch("format"), f"du_fmt_{os.getpid()}_{id(view):x}.img")
    try:
        report("Gecici birim hazirlaniyor...", 5)
        with open(tmp_path, "wb") as fh:
            fh.truncate(view.size)

        cmd = _build_command(tool, kind, tmp_path, label, cluster_bytes, view)
        report(f"{kind.label} olusturuluyor ({kind.tool})...", 20)
        proc = run_tool(cmd)
        if proc.returncode != 0:
            raise FormatError(
                f"{os.path.basename(tool)} hata verdi:\n{(proc.stderr or proc.stdout).strip()[:800]}")

        report("Bolume yaziliyor...", 60)
        _copy_back(tmp_path, view, report)
        report("Tamamlandi", 100)
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
            report("Bolume yaziliyor...", 60 + int(39 * written / total))
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
