"""Uzun testler komut satiri. Ayrinti: tests/long/__init__.py, ADR 0080."""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time

from .data import GIB, MIB, PROFILES, FileSpec, build
from .engine import FS_PLANS, TABLES, Ctx, ROOT, run, save
from .steps import ALIGN, STEPS


def plan_sizes(plan, dataset) -> tuple:
    """(bolum sektoru, disk bayti): veri + %15 ustveri payi; buyutme ve tasima
    icin disk bolumun ~1.6 kati."""
    data = sum(f.size for f in dataset.files)
    part = max(data * 115 // 100 + 48 * MIB, 64 * MIB)
    if plan.key == "xfs":
        part = max(part, 320 * MIB)
    if plan.max_volume:
        part = min(part, plan.max_volume * 2 // 3)
    part_sectors = (part // 512 + ALIGN - 1) // ALIGN * ALIGN
    disk = part_sectors * 512 * 8 // 5 + 16 * MIB
    if plan.max_volume:
        disk = min(disk, plan.max_volume * 3 // 2 + 16 * MIB)
    return part_sectors, disk


def make_dataset(plan, profile, seed: int, budget_gb: float):
    budget = int(budget_gb * GIB) if budget_gb else 0
    if plan.max_volume:
        limit = plan.max_volume * 55 // 100
        budget = min(budget, limit) if budget else limit
    ds = build(seed, profile, max_file=plan.max_file, budget=budget)
    # Tam sinirda dosya: profil siniri asan bir buyuk dosya istiyorsa ve
    # birim alabiliyorsa (FAT32: 4 GiB-1) o boyutta bir dosya eklenir.
    if (plan.max_file and any(b > plan.max_file for b in profile.big)
            and not plan.max_volume
            and (not budget or sum(f.size for f in ds.files) + plan.max_file <= budget)):
        ds.files.insert(0, FileSpec("/sinir_tam_4GiB-1.bin", plan.max_file,
                                    seed * 1_000_003 + 999_999))
    if not plan.writable:
        ds.files = []
    return ds


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tests.long")
    ap.add_argument("--profil", default="quick", choices=sorted(PROFILES))
    ap.add_argument("--fs", default="all", help="virgulle: ntfs,ext4 ya da all")
    ap.add_argument("--tablo", default="all", help="mbr,gpt,mbr-mantiksal ya da all")
    ap.add_argument("--tohum", type=int, default=0, help="0 = zamandan uret")
    ap.add_argument("--butce-gb", type=float, default=0.0,
                    help="senaryo basina en fazla veri (0 = profil)")
    ap.add_argument("--rapor", default="", help="JSON rapor klasoru")
    ap.add_argument("--calisma", default="", help="goruntulerin yazilacagi klasor")
    ap.add_argument("--cekirdek", action="store_true",
                    help="cekirdek ile baglayarak dogrula (Linux + sudo; ADR 0080)")
    ap.add_argument("--devam", action="store_true", help="hatada durma, sonraki adima gec")
    ap.add_argument("--aygit", action="store_true",
                    help="gercek test aygiti uzerinde kos (yalnizca GitHub Actions; "
                         "Linux scsi_debug / Windows takili VHD / macOS hdiutil)")
    ap.add_argument("--liste", action="store_true")
    a = ap.parse_args(argv)

    fs_keys = list(FS_PLANS) if a.fs == "all" else a.fs.split(",")
    tables = list(TABLES) if a.tablo == "all" else a.tablo.split(",")
    if a.liste:
        for k in fs_keys:
            for t in tables:
                print(f"{k}-{t}")
        print("adimlar: " + ", ".join(s.name for s in STEPS))
        return 0
    seed = a.tohum or int(time.time()) % 100000
    profile = PROFILES[a.profil]
    report_dir = a.rapor or os.path.join(ROOT, ".tmp", "uzun", "rapor")
    if a.cekirdek:
        os.environ["DU_UZUN_CEKIRDEK"] = "1"
    # Uygulamanin tanilama gunlugu rapor klasorune: kilit, boyutlandirma ve
    # aygit cagrilari hata incelemesinde gorunsun (CI artifact'ina girer).
    os.environ.setdefault("DISKULTIMATE_LOG_DIR", os.path.join(report_dir, "gunluk"))
    from diskultimate.core import diagnostics
    diagnostics.configure(verbose=True, crash_handler=False)
    failures = 0
    for key in fs_keys:
        plan = FS_PLANS[key]
        for table in tables:
            workdir = a.calisma or os.path.join(ROOT, ".tmp", "uzun", f"{key}-{table}")
            os.makedirs(workdir, exist_ok=True)
            budget_gb = a.butce_gb
            if not budget_gb and profile.name == "full":
                # Klon ve yedek ayni kadar yer ister: veri bos alanin ucte biri
                budget_gb = shutil.disk_usage(workdir).free / 3 / GIB
            ds = make_dataset(plan, profile, seed, budget_gb)
            wanted = [b for b in profile.big if not plan.max_file or b <= plan.max_file]
            dropped = [b for b in wanted if not any(f.size == b for f in ds.files)]
            if dropped and plan.writable:
                print(f"   UYARI: butceye ({budget_gb:.1f} GiB) sigmayan buyuk dosyalar: "
                      + ", ".join(f"{b // MIB} MiB" for b in dropped))
            part_sectors, disk = plan_sizes(plan, ds)
            ctx = Ctx(fs=plan, table=table, profile=profile.name, seed=seed,
                      workdir=workdir, dataset=ds,
                      image=os.path.join(workdir, "disk.img"),
                      part_sectors=part_sectors, disk_bytes=disk,
                      kernel_check=a.cekirdek)
            cleanup = None
            if a.aygit:
                from . import device as dev
                size_mb = min(4096, disk // MIB + 16)
                try:
                    ctx.device, cleanup = dev.create(size_mb, workdir)
                except dev.DeviceError as exc:
                    print(f"   AYGIT OLUSTURULAMADI: {exc}")
                    failures += 1
                    continue
                print(f"   test aygiti: {ctx.device} ({size_mb} MiB)")
            print(f"== {key}-{table} (profil {profile.name}, tohum {seed}, "
                  f"{len(ds.files)} dosya, {sum(f.size for f in ds.files) // MIB} MiB)",
                  flush=True)
            try:
                result = run(ctx, STEPS, stop_on_fail=not a.devam)
            finally:
                if ctx.session is not None:
                    ctx.session.close()
                    ctx.session = None
                if cleanup:
                    cleanup()
            if a.aygit:
                result["senaryo"] += "-aygit"
                result["aygit"] = ctx.device
                result["tekrar"] += " --aygit"
            if dropped and plan.writable:
                result["dusen_dosyalar_mb"] = [b // MIB for b in dropped]
                result["butce_gb"] = round(budget_gb, 1)
            if ctx.session is not None:
                ctx.session.close()
            save(result, os.path.join(report_dir,
                                      f"{key}-{table}{'-aygit' if a.aygit else ''}.json"))
            if not result["tamam"]:
                failures += 1
                print(f"   HATA — tekrar: {result['tekrar']}")
            shutil.rmtree(workdir, ignore_errors=True)
    print(f"\n{failures} senaryo hatali" if failures else "\nButun senaryolar tamam")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
