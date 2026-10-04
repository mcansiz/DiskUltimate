"""Senaryo adimlari — arayuzun yolu: bolum islemleri kuyruk + Uygula ile."""
from __future__ import annotations

import hashlib
import io
import os
import random
from typing import List

from diskultimate.core import operations as ops
from diskultimate.core.session import DiskSession

from . import verify
from .data import MIB, FileSpec
from .engine import Ctx, Step, StepFail, StepSkip

ALIGN = 2048                      # sektor (1 MiB)
LABEL = "UZUNTEST"


def _align_up(n: int) -> int:
    return (n + ALIGN - 1) // ALIGN * ALIGN


def _align_down(n: int) -> int:
    return n // ALIGN * ALIGN


# --------------------------------------------------------------------------
def olustur(ctx: Ctx):
    if ctx.device:
        # Gercek aygit: uygulamanin fiziksel disk kapilarindan gecilir.
        from diskultimate.core.physical import find_disk
        from . import device as dev
        info = None
        for _ in range(20):
            info = find_disk(ctx.device)
            if info is not None:
                break
            import time
            time.sleep(0.5)
        if info is None:
            raise StepFail(f"aygit listede yok: {ctx.device}")
        dev.check_safe(info)
        ctx.session = DiskSession.open_physical(info, readonly=False, confirm=True)
        ctx.apply(ops.create_table_op(ctx.scheme))
        ctx.session.reload()
        ctx.image = ctx.device
        ctx.disk_bytes = info.size
        return f"aygit {info.path} ({info.model}, {info.size // MIB} MiB), {ctx.scheme}"
    ctx.session = DiskSession.create(ctx.image, ctx.disk_bytes, scheme=ctx.scheme,
                                     overwrite=True)
    return f"disk {ctx.disk_bytes // MIB} MiB, {ctx.scheme}"


def bolumle(ctx: Ctx):
    s = ctx.session
    ss = s.image.sector_size
    region = s.free_regions()[0]
    start = _align_up(region.start_lba)
    count = ctx.part_sectors
    if ctx.table == "mbr-mantiksal":
        ext_count = min(_align_down(region.end_lba - start + 1),
                        _align_up(count * 3 // 2) + ALIGN)
        ctx.apply(ops.create_op(start, ext_count, ss, extended=True))
        start += ALIGN
        ctx.apply(ops.create_op(start, count, ss, fs_key=ctx.fs.key, label=LABEL,
                                name="Uzun test", logical=True))
    else:
        ctx.apply(ops.create_op(start, count, ss, fs_key=ctx.fs.key, label=LABEL,
                                name="Uzun test"))
    s.reload()
    found = [p for p in s.partitions if p.fs_type and p.start_lba >= start]
    if not found:
        raise StepFail(f"bicimli bolum bulunamadi: "
                       f"{[(p.start_lba, p.fs_type) for p in s.partitions]}")
    ctx.part_lba = found[0].start_lba
    ctx.original_lba = ctx.part_lba
    return f"bolum {found[0].sector_count * ss // MIB} MiB, {found[0].fs_type}"


def doldur(ctx: Ctx):
    if not ctx.fs.writable:
        raise StepSkip(f"{ctx.fs.key} yazma desteklenmiyor (yalnizca bicim/boyut)")
    access = ctx.access()
    for d in ctx.dataset.dirs:
        access.mkdir(d)
    for spec in ctx.dataset.files:
        ctx.write_spec(access, spec)
    access.flush()
    return f"{len(ctx.manifest.entries)} dosya, {ctx.manifest.total_bytes // MIB} MiB"


def _resize(ctx: Ctx, start: int, count: int) -> None:
    part = ctx.part()
    ctx.apply(ops.resize_op(part.index, start, count, ctx.session.image.sector_size,
                            at_lba=part.start_lba))
    ctx.session.reload()
    ctx.part_lba = start


def _need_resize(ctx: Ctx, mode: str = "full") -> None:
    if ctx.fs.resize == "none":
        raise StepSkip(f"{ctx.fs.key} boyutlandirmayi desteklemiyor")
    if mode == "full" and ctx.fs.resize != "full":
        raise StepSkip(f"{ctx.fs.key} yalnizca buyutulebilir")


def buyut(ctx: Ctx):
    _need_resize(ctx, "grow")
    part = ctx.part()
    window = ctx.session.resize_window(part.index)
    target = min(_align_down(part.sector_count * 5 // 4),
                 _align_down(window.end_lba - part.start_lba + 1))
    info = ctx.session.resize_info(part.index)
    if info.max_sectors:
        target = min(target, _align_down(info.max_sectors))
    if target <= part.sector_count:
        raise StepSkip("buyutecek yer yok")
    old = part.sector_count          # _resize bolum nesnesini yerinde degistirir
    _resize(ctx, part.start_lba, target)
    return f"{old // ALIGN} -> {target // ALIGN} MiB"


def kucult(ctx: Ctx):
    _need_resize(ctx)
    part = ctx.part()
    info = ctx.session.resize_info(part.index)
    target = _align_up(max(info.min_sectors, 1))
    if target >= part.sector_count:
        raise StepSkip(f"kucultulecek yer yok (en az {target // ALIGN} MiB)")
    old = part.sector_count
    _resize(ctx, part.start_lba, target)
    return f"{old // ALIGN} -> {target // ALIGN} MiB (en aza)"


def saga_tasi(ctx: Ctx):
    _need_resize(ctx)
    part = ctx.part()
    window = ctx.session.resize_window(part.index)
    room = window.end_lba - part.end_lba
    delta = _align_down(min(room, max(part.sector_count // 3, ALIGN)))
    if delta < ALIGN:
        raise StepSkip("sagda yer yok")
    _resize(ctx, part.start_lba + delta, part.sector_count)
    return f"+{delta // ALIGN} MiB"


def sola_tasi(ctx: Ctx):
    _need_resize(ctx)
    part = ctx.part()
    if part.start_lba == ctx.original_lba:
        raise StepSkip("zaten ilk yerinde")
    old = part.start_lba
    _resize(ctx, ctx.original_lba, part.sector_count)
    return f"LBA {old} -> {ctx.original_lba}"


def geri_buyut(ctx: Ctx):
    """En aza kucultulen bolum yeniden buyutulur; sonraki adimlar yer ister.

    Kucultmeden sonra buyutmeyi de sinar (kucuk birimin ustverisi buyurken).
    """
    note = buyut(ctx)
    return "kucultmeden sonra " + note


def degistir(ctx: Ctx):
    if not ctx.fs.writable:
        raise StepSkip(f"{ctx.fs.key} yazma desteklenmiyor")
    rnd = random.Random(ctx.seed + 1)
    access = ctx.access()
    small = sorted(p for p, (size, _) in ctx.manifest.entries.items() if size < MIB)
    gone = rnd.sample(small, len(small) // 4)
    for path in gone:
        access.remove(path)
        ctx.manifest.drop(path)
    rest = [p for p in small if p not in gone]
    renamed = 0
    for path in rnd.sample(rest, len(rest) // 10):
        new_name = "yeni_" + path.rsplit("/", 1)[-1]
        access.rename(path, new_name)
        new_path = path.rsplit("/", 1)[0] + "/" + new_name
        ctx.manifest.entries[new_path] = ctx.manifest.entries.pop(path)
        renamed += 1
    mediums = [p for p, (size, _) in ctx.manifest.entries.items() if MIB <= size < 512 * MIB]
    if mediums:
        path = rnd.choice(mediums)
        size = ctx.manifest.entries[path][0]
        ctx.write_spec(access, FileSpec(path, size, ctx.seed * 7 + 99))
    for i in range(20):
        ctx.write_spec(access, FileSpec(f"/degisim_{i:02d}.dat",
                                        rnd.randint(0, 200_000), ctx.seed * 11 + i))
    access.flush()
    return f"{len(gone)} silindi, {renamed} adlandirildi, 1 uzerine yazildi, 20 eklendi"


def kurtar(ctx: Ctx):
    if not ctx.fs.recover:
        raise StepSkip(f"silinmis dosya kurtarma {ctx.fs.key} icin yok (FAT/exFAT)")
    access = ctx.access()
    adaylar = sorted(p for p, (size, _) in ctx.manifest.entries.items()
                     if 4096 <= size <= 200_000)
    if not adaylar:
        raise StepSkip("uygun dosya yok")
    path = adaylar[0]
    size, sha1 = ctx.manifest.entries[path]
    access.remove(path)
    access.flush()
    ctx.manifest.drop(path)
    ctx.session.close_filesystems()
    index = ctx.part().index
    items = ctx.session.scan_deleted(index)
    name = path.rsplit("/", 1)[-1]
    match = [i for i in items if i.name == name and i.size == size]
    if not match:
        raise StepFail(f"silinen '{name}' taramada bulunamadi ({len(items)} kayit)")
    out = os.path.join(ctx.workdir, "kurtarilan.bin")
    ctx.session.recover_deleted(index, match[0], out)
    with open(out, "rb") as fh:
        got = hashlib.sha1(fh.read()).hexdigest()
    os.unlink(out)
    if got != sha1:
        raise StepFail(f"kurtarilan icerik farkli ({match[0].condition})")
    return f"'{name}' kurtarildi ({size} bayt)"


def _image_bytes(path: str) -> int:
    st = os.stat(path)
    return getattr(st, "st_blocks", 0) * 512 or st.st_size


def klonla(ctx: Ctx):
    # aygit kipinde ctx.image bir aygit yoludur; stat boyut vermez
    need = ctx.session.image.size if ctx.device else _image_bytes(ctx.image)
    if ctx.free_disk() < need * 1.1 + 512 * MIB:
        raise StepSkip(f"disk yetmez: klon ~{need // MIB} MiB ister")
    ctx.session.close_filesystems()
    dest = os.path.join(ctx.workdir, "klon.img")
    ctx.session.clone_to(dest)
    try:
        clone = DiskSession.open(dest, readonly=True)
        try:
            part = [p for p in clone.partitions if p.start_lba == ctx.part_lba]
            if not part:
                raise StepFail("klonda bolum yok")
            if ctx.fs.writable:
                fs = clone.filesystem(part[0].index)
                count, _, errors = verify.verify_manifest(fs, ctx.manifest)
                if errors:
                    raise StepFail("klon: " + "; ".join(errors[:5]))
        finally:
            clone.close()
    finally:
        if os.path.exists(dest):
            os.unlink(dest)
    return f"klon dogrulandi ({need // MIB} MiB)"


def yedek_geri(ctx: Ctx):
    need = _image_bytes(ctx.image)
    if ctx.free_disk() < need * 2.2 + 512 * MIB:
        raise StepSkip(f"disk yetmez: yedek + geri yukleme ~{2 * need // MIB} MiB ister")
    ctx.session.close_filesystems()
    dub = os.path.join(ctx.workdir, "yedek.dub")
    dest = os.path.join(ctx.workdir, "geri.img")
    try:
        ctx.session.backup_disk(dub, compress=True, remark="uzun test")
        DiskSession.restore_to_new_image(dub, dest)
        back = DiskSession.open(dest, readonly=True)
        try:
            part = [p for p in back.partitions if p.start_lba == ctx.part_lba]
            if not part:
                raise StepFail("geri yuklenen diskte bolum yok")
            if ctx.fs.writable:
                count, _, errors = verify.verify_manifest(
                    back.filesystem(part[0].index), ctx.manifest)
                if errors:
                    raise StepFail("geri yukleme: " + "; ".join(errors[:5]))
        finally:
            back.close()
        size = os.path.getsize(dub)
    finally:
        for p in (dub, dest):
            if os.path.exists(p):
                os.unlink(p)
    return f".dub {size // MIB} MiB, geri yuklendi"


def donustur(ctx: Ctx):
    target = "mbr" if ctx.session.scheme == "gpt" else "gpt"
    ok, reason = ctx.session.can_convert_to(target)
    if not ok:
        raise StepSkip(f"{target.upper()} donusumu uygun degil: {reason}")
    ctx.apply(ops.convert_table_op(target))
    ctx.session.reload()
    return f"{ctx.scheme.upper()} -> {target.upper()}"


def bos_alan_sil(ctx: Ctx):
    if not ctx.fs.writable:
        raise StepSkip(f"{ctx.fs.key} icin bos alan silme yok")
    part = ctx.part()
    ctx.apply(ops.wipe_free_op(part.index, at_lba=part.start_lba))
    return "bos alan sifirlandi"


def kayip_bolum(ctx: Ctx):
    if ctx.fs.lost_scan:
        raise StepSkip(ctx.fs.lost_scan)
    part = ctx.part()
    ss = ctx.session.image.sector_size
    ctx.apply(ops.delete_op(part.index, part.name, wipe=False))
    ctx.session.reload()
    found = [lp for lp in ctx.session.scan_lost_partitions(deep=False)
             if lp.start_lba == ctx.part_lba]
    if not found:
        raise StepFail(f"silinen bolum (LBA {ctx.part_lba}) taramada bulunamadi")
    lp = found[0]
    extra = {"logical": True} if ctx.table == "mbr-mantiksal" else {}
    ctx.apply(ops.create_op(lp.start_lba, lp.sector_count, ss, name=lp.label or "",
                            keep_data=True, found_fs=lp.fs_type, **extra))
    ctx.session.reload()
    return f"bulundu ({lp.fs_type}, {lp.sector_count * ss // MIB} MiB) ve geri eklendi"


def sinir(ctx: Ctx):
    if not ctx.fs.max_file:
        raise StepSkip(f"{ctx.fs.key} icin dosya boyutu siniri yok")
    access = ctx.access()
    before = access.stats().get("free_bytes")

    class Okunmamali(io.RawIOBase):
        def read(self, n=-1):
            raise StepFail("siniri asan dosyada kaynak okundu")
    try:
        access.write_stream("/sinir_asan.bin", Okunmamali(), 5 * 1024 * MIB)
    except StepFail:
        raise
    except Exception as exc:                              # noqa: BLE001
        access = ctx.access()
        after = access.stats().get("free_bytes")
        if before != after:
            raise StepFail(f"reddedilen dosya alan sizdirdi ({before} -> {after})")
        return f"5 GiB reddedildi: {exc}"
    raise StepFail("5 GiB dosya kabul edildi (sinir 4 GiB-1)")


STEPS: List[Step] = [
    Step("olustur", olustur, verify=False),
    Step("bolumle", bolumle),
    Step("doldur", doldur),
    Step("buyut", buyut),
    Step("kucult", kucult),
    Step("saga_tasi", saga_tasi),
    Step("sola_tasi", sola_tasi),
    Step("geri_buyut", geri_buyut),
    Step("degistir", degistir),
    Step("kurtar", kurtar),
    Step("klonla", klonla, verify=False),
    Step("yedek_geri", yedek_geri, verify=False),
    Step("donustur", donustur),
    Step("bos_alan_sil", bos_alan_sil),
    Step("kayip_bolum", kayip_bolum),
    Step("sinir", sinir, verify=False),
]
