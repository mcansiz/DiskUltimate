"""Senaryo motoru: adim zinciri, olcum, dogrulama, JSON rapor."""
from __future__ import annotations

import hashlib
import json
import os
import platform as _platform
import shutil
import sys
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))

from diskultimate.core import operations as ops  # noqa: E402
from diskultimate.core.session import DiskSession  # noqa: E402

from . import verify  # noqa: E402
from .data import GIB, MIB, Content, Dataset, FileSpec, Manifest  # noqa: E402

FOUR_GIB_1 = 0xFFFFFFFF


@dataclass
class FsPlan:
    key: str
    max_volume: int = 0          # 0 = sinirsiz
    max_file: int = 0            # 0 = sinirsiz
    resize: str = "full"         # full | grow | none
    writable: bool = True
    recover: bool = False        # silinmis dosya kurtarma destekli
    lost_scan: str = ""          # bos = kayip bolum taramasi destekli; dolu = neden yok


FS_PLANS: Dict[str, FsPlan] = {p.key: p for p in (
    FsPlan("fat12", max_volume=32 * MIB, max_file=FOUR_GIB_1, recover=True),
    FsPlan("fat16", max_volume=4 * GIB - 64 * MIB, max_file=FOUR_GIB_1, recover=True),
    FsPlan("fat32", max_file=FOUR_GIB_1, recover=True),
    FsPlan("exfat", recover=True),
    FsPlan("ntfs"),
    FsPlan("ext4"), FsPlan("ext3"), FsPlan("ext2"),
    FsPlan("hfsplus", resize="none"),
    FsPlan("udf", resize="none",
           lost_scan="kayip bolum taramasi UDF'i aramiyor: boyut basliktan "
                     "guvenilir okunamiyor (recovery._probe_modern, ADR 0054)"),
    FsPlan("xfs", resize="grow", writable=False),
)}
TABLES = ("mbr", "gpt", "mbr-mantiksal")


class StepSkip(Exception):
    """Adim bu dosya sistemi / ortam icin uygulanamaz (neden yazilir)."""


class StepFail(Exception):
    """Adim beklenen sonucu vermedi."""


def peak_rss_mb() -> float:
    try:
        if sys.platform.startswith("win"):
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t)] + [
                    (n, ctypes.c_size_t) for n in (
                        "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                        "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                        "PagefileUsage", "PeakPagefileUsage")]
            pmc = PMC()
            pmc.cb = ctypes.sizeof(PMC)
            ctypes.windll.psapi.GetProcessMemoryInfo(
                ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
            return pmc.PeakWorkingSetSize / MIB
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return rss / MIB if sys.platform == "darwin" else rss / 1024
    except Exception:                                    # noqa: BLE001
        return -1.0


class HashingReader:
    """Kaynaktan okunanin ozetini yazarken cikarir (veri iki kez uretilmez)."""

    def __init__(self, src):
        self.src = src
        self.h = hashlib.sha1()

    def read(self, n: int = -1) -> bytes:
        data = self.src.read(n)
        self.h.update(data)
        return data


@dataclass
class Ctx:
    fs: FsPlan
    table: str
    profile: str
    seed: int
    workdir: str
    dataset: Dataset
    image: str = ""
    session: Optional[DiskSession] = None
    part_lba: int = -1
    original_lba: int = -1
    part_sectors: int = 0
    disk_bytes: int = 0
    manifest: Manifest = field(default_factory=Manifest)
    steps: List[dict] = field(default_factory=list)
    kernel_check: bool = False
    device: str = ""                # bos degilse gercek aygit kipi (Asama 5)

    @property
    def scheme(self) -> str:
        return "gpt" if self.table == "gpt" else "mbr"

    def part(self):
        for p in self.session.partitions:
            if p.start_lba == self.part_lba:
                return p
        raise StepFail(f"LBA {self.part_lba} uzerinde bolum yok "
                       f"({[(p.index, p.start_lba) for p in self.session.partitions]})")

    def access(self):
        self.session.close_filesystems()
        fs = self.session.filesystem(self.part().index)
        if fs is None:
            raise StepFail("dosya sistemi acilamadi")
        return fs

    def apply(self, *operations) -> None:
        queue = ops.OperationQueue()
        for op in operations:
            queue.add(op)
        result = queue.apply(self.session)
        if not result.ok:
            raise StepFail(f"kuyruk: {result.summary()}")
        self.session.close_filesystems()

    def reopen(self) -> None:
        """Oturumu yeniden acar (dogrulama icin kapatildiktan sonra)."""
        if self.device:
            from diskultimate.core.physical import find_disk
            self.session = DiskSession.open_physical(find_disk(self.device),
                                                     readonly=False, confirm=True)
        else:
            self.session = DiskSession.open(self.image)

    def free_disk(self) -> int:
        return shutil.disk_usage(self.workdir).free

    def write_spec(self, access, spec: FileSpec) -> None:
        reader = HashingReader(Content(spec))
        access.write_stream(spec.path, reader, spec.size)
        self.manifest.entries[spec.path] = (spec.size, reader.h.hexdigest())


@dataclass
class Step:
    name: str
    fn: Callable[[Ctx], Optional[str]]
    verify: bool = True                 # adimdan sonra tam dogrulama


def run_verify(ctx: Ctx, rec: dict) -> None:
    """Kendi okuyucu (zorunlu) + dis fsck + cekirdek baglama."""
    part = ctx.part()
    ss = ctx.session.image.sector_size
    offset, size = part.start_lba * ss, part.sector_count * ss
    t = time.time()
    if ctx.fs.writable:
        count, total, errors = verify.verify_manifest(ctx.access(), ctx.manifest)
        rec["dogrulama"] = {"dosya": count, "bayt": total,
                            "sn": round(time.time() - t, 2)}
        if errors:
            raise StepFail("kendi okuyucu: " + "; ".join(errors[:5]))
    ctx.session.close_filesystems()
    ctx.session.image.flush()
    status, detail = verify.external_fsck(ctx.fs.key, ctx.image, offset, size)
    rec["fsck"] = {"durum": status, "ayrinti": detail[-300:]}
    if status == "fail":
        raise StepFail("dis denetim: " + detail[-500:])
    if ctx.kernel_check and ctx.fs.writable:
        # Windows acik dosyayi yeniden adlandirmaz (VHD'ye cevirme); macOS'ta
        # hdiutil ayni dosyayi baglar — oturum kapatilip sonra yeniden acilir.
        ctx.session.close()
        try:
            status, detail = verify.kernel_mount_check(ctx.fs.key, ctx.image, offset,
                                                       size, ctx.manifest)
        finally:
            ctx.reopen()
        rec["cekirdek"] = {"durum": status, "ayrinti": detail[-300:]}
        if status == "fail":
            raise StepFail("cekirdek: " + detail[-500:])


def run(ctx: Ctx, steps: List[Step], stop_on_fail: bool = True) -> dict:
    started = time.time()
    ok = True
    for step in steps:
        rec = {"ad": step.name, "durum": "tamam"}
        before = ctx.manifest.total_bytes
        t = time.time()
        try:
            note = step.fn(ctx)
            if note:
                rec["not"] = note
            if step.verify:
                run_verify(ctx, rec)
        except StepSkip as exc:
            rec["durum"] = "atlandi"
            rec["neden"] = str(exc)
        except Exception as exc:                          # noqa: BLE001
            rec["durum"] = "hata"
            rec["hata"] = f"{type(exc).__name__}: {exc}"[:2000]
            rec["iz"] = traceback.format_exc()[-4000:]
            ok = False
        rec["sn"] = round(time.time() - t, 2)
        rec["bellek_mb"] = round(peak_rss_mb(), 1)
        moved = max(ctx.manifest.total_bytes, before)
        if rec["sn"] > 0 and step.name in ("doldur",):
            rec["mb_s"] = round(moved / MIB / rec["sn"], 1)
        ctx.steps.append(rec)
        print(f"  {step.name:<14} {rec['durum']:<8} {rec['sn']:>8.1f} sn  "
              f"bellek {rec['bellek_mb']:.0f} MB"
              + (f"  ({rec.get('neden') or rec.get('hata')})"
                 if rec["durum"] != "tamam" else ""), flush=True)
        if rec["durum"] == "hata" and stop_on_fail:
            break
    return {
        "senaryo": f"{ctx.fs.key}-{ctx.table}", "fs": ctx.fs.key, "tablo": ctx.table,
        "profil": ctx.profile, "tohum": ctx.seed,
        "platform": f"{_platform.system()} {_platform.release()} "
                    f"{_platform.machine()} py{_platform.python_version()}",
        "basladi": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started)),
        "sure_sn": round(time.time() - started, 1),
        "dosya": len(ctx.manifest.entries),
        "veri_mb": round(ctx.manifest.total_bytes / MIB, 1),
        "tamam": ok, "adimlar": ctx.steps,
        "tekrar": (f"python -m tests.long --profil {ctx.profile} --fs {ctx.fs.key} "
                   f"--tablo {ctx.table} --tohum {ctx.seed}"),
    }


def save(result: dict, path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
