"""NTFS uyumluluk gerileme testleri (denetim N1-N7, 2026-10-06).

Eski testler yalnizca kendi bicimlendiricimizin birimlerini kullaniyordu;
mkdosfs'in FAT32'si yanlis okununca ayni korlugun NTFS'te de oldugu goruldu.
Bu modul girdiyi **gercek mkntfs** ile uretir, ntfs-3g kullanici alani
araclariyla (`ntfscp`, `ntfsls`, `ntfscat`, `ntfsfix -n`) doldurur ve
dogrular. Root gerekmez; hicbir sey baglanmaz.

    python3 -m tests.regress_ntfs            # hepsi
    python3 -m tests.regress_ntfs t03 t08    # yalnizca secilenler

Arac yoksa test ATLANDI olur; asla sessizce gecmez.
Gecici alan: `paths.scratch("regress_ntfs")`.
"""
from __future__ import annotations

import hashlib
import os
import random
import shutil
import struct
import subprocess
import sys
import time
import traceback

sys.path.insert(0, os.environ.get("DISKULTIMATE_SRC") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from diskultimate.core.image import DiskImage  # noqa: E402
from diskultimate.core.ntfsread import NtfsError, NtfsFS  # noqa: E402
from diskultimate.core.ntfswrite import NtfsWriter  # noqa: E402
from diskultimate.paths import scratch  # noqa: E402

MIB = 1024 * 1024
TMP = scratch("regress_ntfs")
KEEP = os.environ.get("DISKULTIMATE_KEEP_TEST_FILES") == "1"
RESULTS = []


class Atlandi(Exception):
    """Test bu ortamda kosamadi (arac yok, arac reddetti)."""


def test(fn):
    RESULTS.append(fn)
    return fn


# --------------------------------------------------------------------------
# Yardimcilar
# --------------------------------------------------------------------------
def need(*tools: str) -> None:
    for t in tools:
        if shutil.which(t) is None:
            raise Atlandi(f"{t} kurulu degil")


def run(*args, check: bool = False) -> subprocess.CompletedProcess:
    r = subprocess.run([str(a) for a in args], capture_output=True)
    if check and r.returncode:
        raise AssertionError(f"{args[0]} basarisiz: {r.stderr.decode('utf-8', 'replace')[-300:]}")
    return r


def path(name: str) -> str:
    return os.path.join(TMP, name)


def mkntfs(name: str, size_mb: int, *extra: str, sectors: int = 0) -> str:
    """Gercek mkntfs ile goruntu dosyasi. Arac reddederse ATLANDI."""
    need("mkntfs")
    p = path(name)
    if os.path.exists(p):
        os.remove(p)
    with open(p, "wb") as fh:
        fh.truncate(size_mb * MIB)
    args = ["mkntfs", "-F", "-Q", "-p", "2048", "-H", "255", "-S", "63",
            "-L", "DU", *extra, p]
    if sectors:
        args.append(str(sectors))
    r = run(*args)
    if r.returncode:
        raise Atlandi(f"mkntfs {' '.join(extra)} reddetti: "
                      f"{r.stderr.decode('utf-8', 'replace')[-200:]}")
    return p


def open_image(p: str) -> DiskImage:
    with open(p, "rb") as fh:
        bps = struct.unpack_from("<H", fh.read(512), 11)[0]
    return DiskImage(p, sector_size=bps)


def ntfsfix_ok(p: str) -> None:
    need("ntfsfix")
    r = run("ntfsfix", "-n", p)
    out = (r.stdout + r.stderr).decode("utf-8", "replace")
    assert r.returncode == 0 and "does not match" not in out, \
        f"ntfsfix -n birimi reddetti:\n{out[-600:]}"


def ntfsls(p: str, d: str = "/") -> set:
    need("ntfsls")
    r = run("ntfsls", "-p", d, p)
    assert r.returncode == 0, f"ntfsls {d}: {r.stderr[-300:]!r}"
    return {ln for ln in r.stdout.decode("utf-8", "replace").splitlines() if ln}


def ntfscat(p: str, f: str) -> bytes:
    need("ntfscat")
    r = run("ntfscat", p, f)
    assert r.returncode == 0, f"ntfscat {f}: {r.stderr[-300:]!r}"
    return r.stdout


def ntfscp(p: str, data: bytes, dest: str, *extra: str) -> None:
    need("ntfscp")
    src = path(f"_ntfscp_{os.getpid()}.src")
    with open(src, "wb") as fh:
        fh.write(data)
    run("ntfscp", "-q", *extra, p, src, dest, check=True)


def sha(p: str) -> str:
    h = hashlib.sha1()
    with open(p, "rb") as fh:
        for blok in iter(lambda: fh.read(4 * MIB), b""):
            h.update(blok)
    return h.hexdigest()


def raw_record(fs: NtfsFS, number: int) -> bytes:
    """Kaydi diskten fixup UYGULANMADAN okur."""
    return fs._run_read(fs._mft_runs, number * fs.record_size, fs.record_size)


def assert_usa_512(fs: NtfsFS, number: int) -> None:
    raw = raw_record(fs, number)
    usa_count = struct.unpack_from("<H", raw, 6)[0]
    assert usa_count == fs.record_size // 512 + 1, \
        f"kayit {number}: usa_count {usa_count}, beklenen {fs.record_size // 512 + 1}"


def free_runs(fs: NtfsFS):
    bm = fs.read_attribute(fs.record(6).find(0x80))
    out, start = [], None
    for i in range(fs.cluster_count):
        free = not (bm[i >> 3] >> (i & 7)) & 1
        if free and start is None:
            start = i
        elif not free and start is not None:
            out.append((start, i - start))
            start = None
    if start is not None:
        out.append((start, fs.cluster_count - start))
    return out


def orphan_clusters(fs: NtfsFS) -> set:
    """$Bitmap'te dolu ama hicbir kullanimdaki kaydin sahiplenmedigi kumeler."""
    bm = fs.read_attribute(fs.record(6).find(0x80))
    mbm = fs.read_attribute(fs.record(0).find(0xB0))
    owned = set(range(0, max(1, 8192 // fs.cluster_size)))   # $Boot
    for no in range(fs.mft_size // fs.record_size):
        if not (no >> 3 < len(mbm) and mbm[no >> 3] >> (no & 7) & 1):
            continue
        rec = fs.record(no)
        if not rec.in_use:
            continue
        for a in rec.attributes:
            if a.resident:
                continue
            for lcn, count in a.runs:
                if lcn >= 0:
                    owned.update(range(lcn, lcn + count))
    used = {i for i in range(fs.cluster_count) if bm[i >> 3] >> (i & 7) & 1}
    return used - owned


def fragmented_volume(name: str, size_mb: int, leave: int, piece: int,
                      fill_big: bool) -> str:
    """mkntfs -c 512 birimi, bos alani `piece` baytlik deliklere bolunmus.

    Kendi yazicimizla buyuk bir dolgu, ardindan disk dolana kadar `piece`
    baytlik dosyalar yazilir ve ciftler silinir. `fill_big`: geri kalan
    buyuk bos kosular da doldurulur, yalnizca delikler kalir.
    """
    p = mkntfs(name, size_mb, "-c", "512")
    d = open_image(p)
    fs = NtfsFS(d)
    w = NtfsWriter(fs)
    free = fs.stats()["free_bytes"] // 512
    w.mkdir("/h")
    w.write_file("/filler.bin", b"F" * ((free - leave) * 512))
    n = 0
    try:
        while True:
            w.write_file(f"/h/s{n:05d}", b"s" * piece)
            n += 1
    except NtfsError:
        pass
    for i in range(0, n, 2):
        w.remove(f"/h/s{i:05d}")
    if fill_big:
        hole = (piece + 511) // 512
        fs = NtfsFS(d)
        w = NtfsWriter(fs)
        for k, (_lcn, count) in enumerate(r for r in free_runs(fs) if r[1] > hole):
            w.write_file(f"/pad{k}", b"p" * (count * 512))
    w.flush()
    d.close()
    ntfsfix_ok(p)
    return p


# --------------------------------------------------------------------------
# N1 — guncelleme dizisi (fixup) adimi her zaman 512
# --------------------------------------------------------------------------
NAMES_4K = ["KOK.TXT", "sinir_02_511.bin", "sinir_03_512.bin", "sinir_15_1048577.bin",
            ".gizli", "Büyük Harf ğüşıöç.txt", "日本語のファイル名.txt", "x" * 100]


@test
def t01_4k_sektor_ntfs3g_birimini_okuma():
    """N1: mkntfs -s 4096 + ntfs-3g ile doldurulan birim eksiksiz/dogru okunur"""
    need("ntfscp")
    p = mkntfs("t01.img", 64, "-s", "4096", "-c", "4096")
    rnd = random.Random(1)
    files = {}
    sizes = [0, 1, 100, 511, 512, 513, 700, 1000, 4095, 4096, 4097, 20000]
    for i in range(320):                      # kok dizin $INDEX_ALLOCATION'a tasar
        name = NAMES_4K[i] if i < len(NAMES_4K) else f"dosya_{i:04d}.dat"
        size = 1048577 if name.startswith("sinir_15") else rnd.choice(sizes)
        data = rnd.randbytes(size)
        ntfscp(p, data, "/" + name)
        files[name] = data
    d = open_image(p)
    fs = NtfsFS(d)
    root = fs.resolve("/")
    assert fs.record(root.number).find(0xA0, "$I30") is not None, \
        "kok dizin INDX bloguna tasmadi; test anlamsiz"
    names = {e.name for e in fs.listdir("/")}
    missing = sorted(set(files) - names)
    assert not missing, f"okuyucu {len(missing)} dosyayi gormedi: {missing[:5]}"
    for name, data in files.items():
        assert fs.read_file("/" + name) == data, f"icerik yanlis: {name}"
        assert b"".join(fs.iter_file("/" + name)) == data, f"akis yanlis: {name}"
    d.close()


@test
def t02_4k_sektor_yazma_ntfs3g_kabul_eder():
    """N1: 4K sektorlu mkntfs birimine yazilan kayitlar 512 adimli; ntfs-3g okur"""
    need("ntfscp", "ntfscat", "ntfsls", "ntfsfix")
    p = mkntfs("t02.img", 64, "-s", "4096", "-c", "4096")
    for i in range(60):
        ntfscp(p, b"%d" % i * 50, f"/eski_{i:03d}.txt")
    d = open_image(p)
    w = NtfsWriter(NtfsFS(d))
    ok, reason = w.write_support()
    assert ok, reason
    w.mkdir("/klasor")
    w.write_file("/klasor/yeni.bin", b"Q" * 30000)
    w.write_file("/kucuk.txt", b"merhaba")
    for i in range(40):
        w.write_file(f"/klasor/f{i:03d}.txt", b"%d" % i * 30)
    w.remove("/eski_001.txt")
    w.rename("/eski_002.txt", "adi_degisti.txt")
    w.flush()
    fs = NtfsFS(d)
    for path_ in ("/klasor", "/klasor/yeni.bin", "/kucuk.txt"):
        assert_usa_512(fs, fs.resolve(path_).number)
    d.close()
    ntfsfix_ok(p)
    assert ntfscat(p, "/klasor/yeni.bin") == b"Q" * 30000
    assert ntfscat(p, "/kucuk.txt") == b"merhaba"
    assert ntfscat(p, "/adi_degisti.txt") == b"2" * 50
    listed = ntfsls(p, "/klasor")
    assert {f"f{i:03d}.txt" for i in range(40)} <= listed, "ntfs-3g dizini eksik goruyor"
    assert "eski_001.txt" not in ntfsls(p, "/")


@test
def t03_bicimlendirici_4k_sektor():
    """N1: kendi bicimlendiricimiz 4K sektorde usa_count = kayit/512 + 1 yazar"""
    from diskultimate.core.ntfs import format_ntfs
    from diskultimate.core.ntfsfix import ntfs_check
    p = path("t03.img")
    d = DiskImage.create(p, 128 * MIB, overwrite=True, sector_size=4096)
    format_ntfs(d, "DU4K", cluster_size=4096)
    fs = NtfsFS(d)
    for no in (0, 1, 3, 5, 6, 9):
        assert_usa_512(fs, no)
    w = NtfsWriter(fs)
    w.write_file("/a.txt", b"4K" * 3000)
    w.flush()
    h = ntfs_check(d)
    assert not h.problems(), h.problems()
    d.close()
    if shutil.which("ntfsfix") and shutil.which("ntfscat"):
        ntfsfix_ok(p)
        assert ntfscat(p, "/a.txt") == b"4K" * 3000
    else:
        raise Atlandi("ntfsfix/ntfscat yok: yalnizca kendi denetimimiz kostu")


# --------------------------------------------------------------------------
# N5 — $MFTMirr butun kapsami esitlenir
# --------------------------------------------------------------------------
def _write_mix(w: NtfsWriter) -> None:
    w.mkdir("/dir")
    for i in range(40):
        w.write_file(f"/dir/f{i}.bin", bytes([i]) * (3000 + i))
    for i in range(6):
        w.write_file(f"/r{i}.txt", b"x" * (10 + i))
    for i in range(5):
        w.remove(f"/dir/f{i}.bin")
    w.rename("/r0.txt", "r0b.txt")
    w.flush()


@test
def t04_buyuk_kume_mftmirr():
    """N5: -c 64K/128K/2M mkntfs birimine yazma sonrasi $MFTMirr == $MFT"""
    from diskultimate.core.ntfsfix import ntfs_check
    need("ntfsfix", "ntfsls")
    for cluster, size_mb in ((512, 32), (4096, 64), (65536, 256),
                             (131072, 256), (2097152, 1024)):
        p = mkntfs(f"t04_{cluster}.img", size_mb, "-c", str(cluster))
        d = open_image(p)
        _write_mix(NtfsWriter(NtfsFS(d)))
        h = ntfs_check(d)
        assert not h.mirror_mismatch, f"-c {cluster}: {h.problems()}"
        d.close()
        ntfsfix_ok(p)
        assert {f"f{i}.bin" for i in range(5, 40)} <= ntfsls(p, "/dir"), cluster
        os.remove(p)


@test
def t05_buyuk_kume_buyutme_mftmirr():
    """N5: -c 64K birimi buyutulunce ($Bitmap/$BadClus degisir) ayna esit kalir"""
    from diskultimate.core.ntfsresize import ntfs_resize
    need("ntfsfix", "ntfsls")
    p = mkntfs("t05.img", 256, "-c", "65536", sectors=300000)
    d = open_image(p)
    _write_mix(NtfsWriter(NtfsFS(d)))
    ntfs_resize(d, d.sector_count)
    d.close()
    ntfsfix_ok(p)
    assert {f"f{i}.bin" for i in range(5, 40)} <= ntfsls(p, "/dir")


@test
def t06_tespit_buyuk_kume_boyu():
    """fsdetect: spc > 0x80 (128 KiB kume) dogru cozulur"""
    from diskultimate.core.fsdetect import detect
    p = mkntfs("t06.img", 256, "-c", "131072")
    d = open_image(p)
    info = detect(d)
    d.close()
    assert info.cluster_size == 131072, info.cluster_size


# --------------------------------------------------------------------------
# N2 — hazirda bekletme / temiz olmayan gunluk / kirli / surum kapisi
# --------------------------------------------------------------------------
def _refused_everywhere(p: str, why: str, usedmap_none: bool = True) -> None:
    from diskultimate.core.filesystem import NtfsAccess
    from diskultimate.core.ntfsresize import (NtfsResizeError, ntfs_repair,
                                              ntfs_resize, ntfs_size_info)
    from diskultimate.core.usedmap import fs_used_ranges
    before = sha(p)
    d = open_image(p)
    acc = NtfsAccess(d)
    assert not acc.writable, f"{why}: yazma izni verildi"
    assert acc.write_reason, why
    try:
        acc.write_stream("/x.txt", __import__("io").BytesIO(b"x"), 1)
        raise AssertionError(f"{why}: yazma reddedilmedi")
    except NtfsError:
        pass
    info = ntfs_size_info(d)
    assert info.dirty and info.note, f"{why}: boyutlandirma bilgisi engel gostermiyor"
    for fn in (lambda: ntfs_resize(d, d.sector_count - 8192),
               lambda: ntfs_repair(d)):
        try:
            fn()
            raise AssertionError(f"{why}: boyutlandirma/onarim reddedilmedi")
        except NtfsResizeError:
            pass
    ranges = fs_used_ranges(d, "NTFS")
    if usedmap_none:
        assert ranges is None, f"{why}: yalnizca-kullanilan yedek $Bitmap'e guvendi"
    else:
        assert ranges is not None, why
    d.close()
    assert sha(p) == before, f"{why}: reddedilen islem birime yazdi"


def _accepted(p: str) -> None:
    from diskultimate.core.filesystem import NtfsAccess
    from diskultimate.core.usedmap import fs_used_ranges
    d = open_image(p)
    acc = NtfsAccess(d)
    assert acc.writable, acc.write_reason
    assert fs_used_ranges(d, "NTFS") is not None
    d.close()


@test
def t07_hazirda_bekletme_kapisi():
    """N2: hiberfil.sys "hibr"/"HIBR"/kisa -> red; "wake"/sifir -> izin"""
    need("ntfscp")
    for tag, header, refused in (("hibr", b"hibr", True), ("HIBR", b"HIBR", True),
                                 ("kisa", None, True), ("wake", b"wake", False),
                                 ("sifir", b"\0\0\0\0", False)):
        p = mkntfs(f"t07_{tag}.img", 32)
        _accepted(p)
        data = b"\x01" * 100 if header is None else header + b"\0" * 8188
        ntfscp(p, data, "/hiberfil.sys")
        if refused:
            _refused_everywhere(p, f"hiberfil {tag}")
        else:
            _accepted(p)
        os.remove(p)


def _logfile_write(p: str, patch) -> None:
    from diskultimate.core.ntfs import logfile_restart_page
    d = open_image(p)
    fs = NtfsFS(d)
    attr = fs.record(2).find(0x80)
    page = bytearray(logfile_restart_page(attr.data_size))
    patch(page)
    cs = fs.cluster_size
    for off in (0, 4096):                         # iki yeniden baslatma sayfasi
        pos = 0
        for lcn, count in attr.runs:
            if off < pos + count * cs:
                d.write(lcn * cs + off - pos, bytes(page))
                break
            pos += count * cs
    d.close()


@test
def t08_temiz_olmayan_gunluk_kapisi():
    """N2: $LogFile'da etkin istemci + temiz bayragi yok -> yazma/boyut/yedek reddi"""
    p = mkntfs("t08.img", 32)
    _logfile_write(p, lambda page: None)          # temiz sayfa: izin
    _accepted(p)

    def unclean(page):
        struct.pack_into("<H", page, 0x30 + 14, 0)    # RESTART_VOLUME_IS_CLEAN yok
    _logfile_write(p, unclean)
    _refused_everywhere(p, "kirli gunluk")

    def garbage(page):
        page[0:4] = b"XXXX"                            # ne RSTR ne 0xFF: bilinmiyor
    _logfile_write(p, garbage)
    _refused_everywhere(p, "okunamayan gunluk")


def _patch_volume_info(p: str, fn) -> None:
    d = open_image(p)
    fs = NtfsFS(d)
    attr = fs.record(3).find(0x70)
    value = bytearray(attr.value)
    fn(value)
    NtfsWriter(fs)._patch_resident(3, attr, bytes(value))
    d.close()


@test
def t09_kirli_bayrak_ve_surum_kapisi():
    """N2/N7: kirli bayrak -> red; NTFS 1.2 -> yazma reddi (yedek serbest)"""
    p = mkntfs("t09.img", 32)
    _patch_volume_info(p, lambda v: struct.pack_into("<H", v, 10, 1))
    _refused_everywhere(p, "kirli bayrak")
    p = mkntfs("t09b.img", 32)
    _patch_volume_info(p, lambda v: (v.__setitem__(8, 1), v.__setitem__(9, 2)))
    from diskultimate.core.filesystem import NtfsAccess
    d = open_image(p)
    acc = NtfsAccess(d)
    assert not acc.writable and "1.2" in acc.write_reason, acc.write_reason
    d.close()
    _refused_everywhere(p, "NTFS 1.2", usedmap_none=False)


# --------------------------------------------------------------------------
# N4 — $ATTRIBUTE_LIST ile parcalara bolunmus akis
# --------------------------------------------------------------------------
@test
def t10_oznitelik_listeli_parcali_dosya():
    """N4: ntfs-3g'nin parcali (cok uzantili) dosyasi tam okunur; degistirme reddedilir"""
    need("ntfscp", "ntfscat", "ntfsfix")
    p = fragmented_volume("t10.img", 16, 6000, 1500, fill_big=True)
    data = random.Random(10).randbytes(1200 * 512)
    ntfscp(p, data, "/frag.bin", "-m")
    d = open_image(p)
    fs = NtfsFS(d)
    rec = fs.resolve("/frag.bin")
    attr = rec.find(0x80)
    parts = [a for a in rec.all_attributes() if a.kind == 0x80 and not a.name]
    if not any(a.kind == 0x20 for a in rec.attributes) or len(parts) < 2:
        raise Atlandi("ntfs-3g bu surumde oznitelik listesi uretmedi")
    assert fs.read_file("/frag.bin") == data, "parcali dosya kesik/yanlis okundu"
    assert b"".join(fs.iter_file("/frag.bin")) == data
    assert fs.read_attribute_range(attr, len(data) - 5000, 5000) == data[-5000:]
    w = NtfsWriter(fs)
    for islem in (lambda: w.remove("/frag.bin"),
                  lambda: w.rename("/frag.bin", "baska.bin"),
                  lambda: w.write_file("/frag.bin", b"yeni")):
        try:
            islem()
            raise AssertionError("oznitelik listeli kayit degistirildi")
        except NtfsError:
            pass
    d.close()
    ntfsfix_ok(p)
    assert ntfscat(p, "/frag.bin") == data


# --------------------------------------------------------------------------
# N6 — kumeden buyuk INDX blogu parcali $INDEX_ALLOCATION'da
# --------------------------------------------------------------------------
@test
def t11_parcali_indeks_ayirmasi_512_kume():
    """N6: 512 B kumede INDX bloklari parcali kosulara dogru yazilir"""
    from diskultimate.core.ntfsindex import verify_tree
    need("ntfsls", "ntfsfix", "ntfscat")
    p = fragmented_volume("t11.img", 16, 3000, 1500, fill_big=True)
    d = open_image(p)
    w = NtfsWriter(NtfsFS(d))
    w.mkdir("/big")
    for i in range(120):
        w.write_file(f"/big/file_{i:03d}.txt", b"%d" % i * 20)
    w.flush()
    fs = NtfsFS(d)
    rec = fs.resolve("/big")
    alloc = rec.find(0xA0, "$I30")
    assert alloc is not None and len(alloc.runs) > 1, "indeks parcali degil; test anlamsiz"
    assert min(c for _l, c in alloc.runs) * fs.cluster_size < fs.index_size, \
        "kosular bloktan buyuk; bolunen blok yok"
    problems = verify_tree(NtfsWriter(fs), rec.number)
    assert not problems, problems
    names = {e.name for e in fs.listdir("/big")}
    assert names == {f"file_{i:03d}.txt" for i in range(120)}
    d.close()
    ntfsfix_ok(p)
    assert {f"file_{i:03d}.txt" for i in range(120)} <= ntfsls(p, "/big")
    for i in range(0, 120, 11):
        assert ntfscat(p, f"/big/file_{i:03d}.txt") == b"%d" % i * 20


# --------------------------------------------------------------------------
# N3 — $MFT'nin kendisi $ATTRIBUTE_LIST tasiyor
# --------------------------------------------------------------------------
@test
def t12_mft_oznitelik_listesi():
    """N3: $ATTRIBUTE_LIST'li $MFT'nin tum kayitlari okunur; boyutlandirma reddedilir"""
    from diskultimate.core.ntfsresize import NtfsResizeError, ntfs_resize
    need("ntfscp", "ntfsls", "ntfsfix", "ntfscat")
    p = fragmented_volume("t12.img", 32, 26000, 4000, fill_big=False)
    src = path(f"_tiny_{os.getpid()}.src")
    with open(src, "wb") as fh:
        fh.write(b"tiny")
    names = [f"t{i:05d}" for i in range(3500)]
    for n in names:
        run("ntfscp", "-q", p, src, "/" + n, check=True)
    d = open_image(p)
    fs = NtfsFS(d)
    if not any(a.kind == 0x20 for a in fs.record(0).attributes):
        raise Atlandi("ntfs-3g $MFT icin oznitelik listesi uretmedi")
    assert not getattr(fs, "mft_incomplete", False)
    covered = sum(c for _l, c in fs._mft_runs) * fs.cluster_size
    assert covered >= fs.mft_size, "$MFT zinciri eksik kuruldu"
    listed = {e.name for e in fs.listdir("/")}
    missing = set(names) - listed
    assert not missing, f"{len(missing)} kayit gorunmedi (or. {sorted(missing)[:3]})"
    for n in names[::250] + names[-3:]:
        assert fs.read_file("/" + n) == b"tiny", n
    w = NtfsWriter(fs)
    ok, reason = w.write_support()
    assert ok, reason
    try:
        w.write_file("/sonradan.txt", b"sonra")
        written = True
    except NtfsError:
        written = False                    # reddetmek serbest, bozmak degil
    w.flush()
    before = sha(p)
    try:
        ntfs_resize(d, d.sector_count - 4096)
        raise AssertionError("$MFT'si dagilmis birim kucultuldu")
    except NtfsResizeError:
        pass
    d.close()
    assert sha(p) == before, "reddedilen boyutlandirma birime yazdi"
    ntfsfix_ok(p)
    if written:
        assert ntfscat(p, "/sonradan.txt") == b"sonra"
    assert set(names[-50:]) <= ntfsls(p, "/")


@test
def t13_kucultme_okunamayan_kaydi_atlamaz():
    """N3: kullanimdaki okunamayan kayit varken kucultme hicbir sey yazmadan durur"""
    from diskultimate.core.ntfs import format_ntfs
    from diskultimate.core.ntfsresize import NtfsResizeError, ntfs_resize
    p = path("t13.img")
    d = DiskImage.create(p, 64 * MIB, overwrite=True)
    format_ntfs(d, "DU", cluster_size=4096)
    w = NtfsWriter(NtfsFS(d))
    w.write_file("/dolgu.bin", b"d" * (40 * MIB))
    w.write_file("/hedef.bin", b"h" * MIB)      # siniri asan yerde
    w.remove("/dolgu.bin")                       # veri yeni boyuta sigsin
    w.flush()
    fs = NtfsFS(d)
    no = fs.resolve("/hedef.bin").number
    pos = no * fs.record_size
    for lcn, count in fs._mft_runs:
        if pos < count * fs.cluster_size:
            d.write(lcn * fs.cluster_size + pos, b"BAAD")
            break
        pos -= count * fs.cluster_size
    d.close()
    before = sha(p)
    d = DiskImage(p)
    try:
        ntfs_resize(d, (16 * MIB) // 512)
        raise AssertionError("okunamayan kayit atlanip kucultuldu")
    except NtfsResizeError as exc:
        assert str(no) in str(exc), exc
    d.close()
    assert sha(p) == before, "reddedilen kucultme birime yazdi"


# --------------------------------------------------------------------------
# N7 — EFS sifreli akis, silmede sizinti
# --------------------------------------------------------------------------
@test
def t14_sifreli_akis_okunmaz():
    """N7: ATTR_ENCRYPTED $DATA sifreli metin olarak disa aktarilmaz"""
    p = mkntfs("t14.img", 32)
    d = open_image(p)
    fs = NtfsFS(d)
    w = NtfsWriter(fs)
    w.write_file("/gizli.bin", b"S" * 20000)
    fs = NtfsFS(d)
    rec = fs.resolve("/gizli.bin")
    raw = w._raw_record(rec.number)
    pos = w._find_attr_offset(raw, rec.find(0x80))
    struct.pack_into("<H", raw, pos + 0x0C,
                     struct.unpack_from("<H", raw, pos + 0x0C)[0] | 0x4000)
    w.write_record(rec.number, raw)
    fs = NtfsFS(d)
    for fn in (lambda: fs.read_file("/gizli.bin"),
               lambda: b"".join(fs.iter_file("/gizli.bin")),
               lambda: fs.read_attribute_range(fs.resolve("/gizli.bin").find(0x80), 0, 10)):
        try:
            fn()
            raise AssertionError("sifreli akis okundu")
        except NtfsError:
            pass
    d.close()


@test
def t15_silme_sizinti_birakmaz():
    """N7: adli akis, dizin $INDEX_ALLOCATION'i silinince kumeler geri doner"""
    need("ntfscp", "ntfsfix")
    p = mkntfs("t15.img", 32, "-c", "4096")
    ntfscp(p, b"A" * 40000, "/ads.bin")
    ntfscp(p, b"B" * 30000, "/ads.bin", "-N", "akis")
    d = open_image(p)
    fs = NtfsFS(d)
    assert fs.resolve("/ads.bin").find(0x80, "akis") is not None, "adli akis olusmadi"
    assert not orphan_clusters(fs), "baslangicta sahipsiz kume var"
    w = NtfsWriter(fs)
    w.mkdir("/d")
    for i in range(150):
        w.write_file(f"/d/dosya_{i:03d}.txt", b"x" * 10)
    assert NtfsFS(d).resolve("/d").find(0xA0, "$I30") is not None
    for i in range(150):
        w.remove(f"/d/dosya_{i:03d}.txt")
    w.remove("/d")
    w.remove("/ads.bin")
    w.flush()
    fs = NtfsFS(d)
    orphans = orphan_clusters(fs)
    assert not orphans, f"{len(orphans)} sahipsiz kume (or. {sorted(orphans)[:5]})"
    d.close()
    ntfsfix_ok(p)


def _add_hardlink(w: NtfsWriter, dir_path: str, existing: str, new: str) -> None:
    """Ayni dizinde ikinci ad (sabit bag): kayda ikinci $FILE_NAME + dizin
    girisi, bag sayisi 2 — cekirdegin `ln` ile yazdigi yapi."""
    rec = w.fs.resolve(dir_path.rstrip("/") + "/" + existing)
    dir_no = w._dir_number(dir_path)
    items, raw = w._record_attrs(rec.number)
    data = rec.find(0x80)
    size = data.data_size
    alloc = 0 if data.resident else data.allocated_size
    used_ids = [struct.unpack_from("<H", it[2], 0x0E)[0] for it in items]
    parent_ref = (w.fs.record(dir_no).sequence << 48) | dir_no
    fn = w._resident_attr(0x30, w._file_name_value(parent_ref, new, 0x20, size, alloc),
                          attr_id=max(used_ids) + 1, indexed=1)
    items.append([0x30, "", bytearray(fn)])
    w._write_record_attrs(rec.number, raw, items, links=2)
    w.index_add(dir_no, new, rec.number, rec.sequence, 0x20, size, alloc)
    w.fs._cache.clear()


@test
def t19_ayni_dizinde_sabit_bag_silme_ve_ad_degistirme():
    """Ayni klasordeki iki sabit bagdan biri silinince/adi degisince digeri kalir

    Yabanci matris (ntfs3 `ln a b` sonra bizim `remove(b)`): `a` da kayboldu,
    kayit serbest kaldi. Silme ayni dizindeki BUTUN adlari uzun+8.3 cifti
    sayiyordu; rename de ayni varsayimla obur bagi siliyordu.
    """
    need("ntfscp", "ntfsfix", "ntfsls", "ntfscat")
    p = mkntfs("t18.img", 32)
    veri = bytes(range(256)) * 300            # yerlesik olmayan veri
    ntfscp(p, veri, "/bag_a.bin")
    d = open_image(p)
    w = NtfsWriter(NtfsFS(d))
    _add_hardlink(w, "/", "bag_a.bin", "bag_b.bin")
    w.flush()
    d.close()
    ntfsfix_ok(p)                              # yapi gecerli: referans kabul ediyor
    assert {"bag_a.bin", "bag_b.bin"} <= ntfsls(p), ntfsls(p)
    assert ntfscat(p, "/bag_b.bin") == veri

    d = open_image(p)
    w = NtfsWriter(NtfsFS(d))
    w.remove("/bag_b.bin")
    w.flush()
    fs = NtfsFS(d)
    assert [e.name for e in fs.listdir("/") if e.name.startswith("bag_")] == ["bag_a.bin"]
    assert fs.read_file("/bag_a.bin") == veri
    assert not orphan_clusters(fs), "veri kumeleri serbest birakilmis"
    d.close()
    ntfsfix_ok(p)
    assert ntfscat(p, "/bag_a.bin") == veri

    # ad degistirme: obur bag yerinde kalmali
    d = open_image(p)
    w = NtfsWriter(NtfsFS(d))
    _add_hardlink(w, "/", "bag_a.bin", "bag_c.bin")
    w.rename("/bag_c.bin", "bag_d.bin")
    w.flush()
    names = sorted(e.name for e in NtfsFS(d).listdir("/") if e.name.startswith("bag_"))
    assert names == ["bag_a.bin", "bag_d.bin"], names
    d.close()
    ntfsfix_ok(p)
    assert ntfscat(p, "/bag_a.bin") == veri and ntfscat(p, "/bag_d.bin") == veri


@test
def t16_4k_birim_512_gorunumde_boyutlandirma():
    """mkntfs -s 4096 birimi 512 B sektorlu gorunumde buyutulup kucultulur"""
    from diskultimate.core.ntfsresize import ntfs_resize, ntfs_size_info
    need("ntfsfix", "ntfscat", "ntfscp")
    p = mkntfs("t16.img", 128, "-s", "4096", sectors=8192)     # 32 MiB birim
    ntfscp(p, b"K" * 70000, "/korunan.bin")
    d = DiskImage(p)                                 # gorunum: 512 B sektor
    assert d.sector_size == 512
    info = ntfs_size_info(d)
    assert info.volume_sectors == 8192 * 8, info.volume_sectors
    ntfs_resize(d, d.sector_count)                   # 128 MiB
    assert NtfsFS(DiskImage(p, sector_size=4096)).total_sectors == 128 * MIB // 4096 - 1
    d.close()
    ntfsfix_ok(p)
    assert ntfscat(p, "/korunan.bin") == b"K" * 70000
    d = DiskImage(p)
    ntfs_resize(d, 48 * MIB // 512)
    d.close()
    # Bolum tablosu kucultmesinin karsiligi: ntfsfix yedek onyukleme
    # sektorunu aygitin sonunda arar
    with open(p, "r+b") as fh:
        fh.truncate(48 * MIB)
    ntfsfix_ok(p)
    assert ntfscat(p, "/korunan.bin") == b"K" * 70000


def _ntfs4k_disk(name: str, part_mb: int, disk_mb: int):
    """512 B sektorlu disk goruntusu, 2048'de mkntfs -s 4096 bolumu.

    Dondurur: (disk yolu, bolumdeki dosyanin icerigi)."""
    from diskultimate.core.mbr import MBRTable
    need("ntfscp")
    part = mkntfs(name + "_bolum.img", part_mb, "-s", "4096")
    data = random.Random(17).randbytes(300000)
    ntfscp(part, data, "/korunan.bin")
    disk = path(name + ".img")
    d = DiskImage.create(disk, disk_mb * MIB, overwrite=True)
    MBRTable.create(d).add_partition(2048, part_mb * MIB // 512, type_id=0x07)
    with open(part, "rb") as fh:
        pos = 0
        for blok in iter(lambda: fh.read(4 * MIB), b""):
            if blok.strip(b"\0"):
                d.write(2048 * 512 + pos, blok)
            pos += len(blok)
    d.close()
    os.remove(part)
    return disk, data


def _extract(disk: str, start_lba: int, count: int, out: str) -> str:
    with open(disk, "rb") as src, open(out, "wb") as dst:
        src.seek(start_lba * 512)
        left = count * 512
        while left:
            blok = src.read(min(4 * MIB, left))
            dst.write(blok)
            left -= len(blok)
    return out


@test
def t17_4k_ntfs_bolumu_saga_tasima():
    """mkntfs -s 4096 bolumu 512 B diskte saga tasininca gizli sektor/yedek dogru"""
    from diskultimate.core.session import DiskSession
    need("ntfsfix", "ntfscat")
    disk, data = _ntfs4k_disk("t17", 64, 160)
    s = DiskSession.open(disk)
    try:
        part = s.table.get(1)
        new_start = 2048 + 32 * 2048
        s.resize_partition(1, new_start, part.sector_count, confirm=True)
    finally:
        s.close()
    out = _extract(disk, new_start, 64 * MIB // 512, path("t17_tasindi.img"))
    with open(out, "rb") as fh:
        boot = fh.read(4096)
        total = struct.unpack_from("<Q", boot, 0x28)[0]
        fh.seek(total * 4096)
        yedek = fh.read(512)
    hidden = struct.unpack_from("<I", boot, 28)[0]
    assert hidden == new_start * 512 // 4096, f"gizli sektor {hidden}"
    assert yedek == boot[:512], "yedek onyukleme sektoru asilla ayni degil"
    ntfsfix_ok(out)
    assert ntfscat(out, "/korunan.bin") == data


@test
def t18_4k_ntfs_kayip_bolum_boyu():
    """Kayip bolum taramasi mkntfs -s 4096 birimin boyunu aygit sektoruyle verir"""
    from diskultimate.core.recovery import scan_lost_partitions
    disk, _data = _ntfs4k_disk("t18", 64, 160)
    d = DiskImage(disk)
    d.write(446, b"\0" * 64)                     # bolum tablosu silindi
    found = [p for p in scan_lost_partitions(d) if p.fs_type == "NTFS"]
    d.close()
    assert found, "NTFS bulunamadi"
    p = found[0]
    assert p.start_lba == 2048, p.start_lba
    assert p.sector_count == 64 * MIB // 512, p.sector_count


def _index_fields(fs: NtfsFS, rec_no: int, name: str = "$I30") -> dict:
    """$INDEX_ROOT basligi ve $INDEX_ALLOCATION/$BITMAP tutarlilik olculeri."""
    rec = fs.record(rec_no)
    v = rec.find(0x90, name).value
    out = {"tur": struct.unpack_from("<I", v, 0)[0],
           "siralama": struct.unpack_from("<I", v, 4)[0],
           "blok": struct.unpack_from("<I", v, 8)[0],
           "blok_kume": v[12]}
    alloc = rec.find(0xA0, name)
    if alloc is not None:
        clusters = sum(c for _l, c in alloc.runs)
        bmp = fs.read_attribute(rec.find(0xB0, name))
        blocks = alloc.data_size // out["blok"]
        out["ayrilan_kume_kati"] = alloc.allocated_size == clusters * fs.cluster_size
        out["veri_blok_kati"] = alloc.data_size % out["blok"] == 0
        out["veri<=ayrilan"] = alloc.data_size <= alloc.allocated_size
        out["baslatilan==veri"] = alloc.initialized_size == alloc.data_size
        out["bitmap_kapsar"] = len(bmp) * 8 >= blocks and len(bmp) % 8 == 0
        out["bitmap_fazla_bit_yok"] = not any(
            bmp[i >> 3] >> (i & 7) & 1 for i in range(blocks, len(bmp) * 8))
    return out


@test
def t20_kendi_dizinimiz_indeks_alanlari_mkntfs_ile_ayni():
    """mkdir'in $INDEX_ROOT/$INDEX_ALLOCATION alanlari mkntfs kokuyle ayni (ntfs3)"""
    need("ntfscp", "ntfsls", "ntfsfix")
    for cluster in (512, 4096, 65536, 2097152):
        p = mkntfs(f"t20_{cluster}.img", 1024 if cluster > 65536 else 256,
                   "-c", str(cluster))
        for i in range(60):                       # mkntfs kokunu INDX'e tasir
            ntfscp(p, b"k", f"/kok_{i:03d}_uzun_bir_dosya_adi.txt")
        d = open_image(p)
        w = NtfsWriter(NtfsFS(d))
        w.mkdir("/yeni_dizin")
        w.mkdir("/bos_dizin")
        for i in range(60):
            w.write_file(f"/yeni_dizin/bizim_{i:03d}_uzun_bir_dosya_adi.txt", b"b")
        w.flush()
        fs = NtfsFS(d)
        ref = _index_fields(fs, 5)
        ours = _index_fields(fs, fs.resolve("/yeni_dizin").number)
        empty = _index_fields(fs, fs.resolve("/bos_dizin").number)
        d.close()
        assert ref["blok_kume"] == (8 if cluster > 4096 else max(1, 4096 // cluster)), ref
        for k, v in ref.items():
            assert ours.get(k) == v, f"-c {cluster}: {k}: mkntfs {v}, bizim {ours.get(k)}"
        for k in ("tur", "siralama", "blok", "blok_kume"):
            assert empty[k] == ref[k], f"-c {cluster} bos dizin {k}: {empty[k]} != {ref[k]}"
        # ntfs-3g de yeni dizine yazabilmeli ve hepsini listelemeli
        for i in range(40):
            ntfscp(p, b"n", f"/yeni_dizin/ntfs3g_{i:03d}.txt")
        ntfsfix_ok(p)
        listed = ntfsls(p, "/yeni_dizin")
        want = ({f"bizim_{i:03d}_uzun_bir_dosya_adi.txt" for i in range(60)}
                | {f"ntfs3g_{i:03d}.txt" for i in range(40)})
        assert want <= listed, f"-c {cluster}: ntfsls eksik {sorted(want - listed)[:3]}"
        d = open_image(p)
        assert want <= {e.name for e in NtfsFS(d).listdir("/yeni_dizin")}
        d.close()
        os.remove(p)


@test
def t21_bicimlendirici_buyuk_kume_indeks_alanlari():
    """Kendi bicimlendiricimiz 64 KiB kumede kok/$Secure/$Extend indeks alani = 8"""
    from diskultimate.core.ntfs import format_ntfs
    p = path("t21.img")
    d = DiskImage.create(p, 256 * MIB, overwrite=True)
    format_ntfs(d, "DU64K", cluster_size=65536)
    fs = NtfsFS(d)
    for rec_no, name in ((5, "$I30"), (9, "$SII"), (9, "$SDH"), (11, "$I30")):
        f = _index_fields(fs, rec_no, name)
        assert f["blok"] == 4096 and f["blok_kume"] == 8, (rec_no, name, f)
        for k, v in f.items():
            if isinstance(v, bool):
                assert v, (rec_no, name, k, f)
    w = NtfsWriter(fs)
    w.mkdir("/d")
    for i in range(60):
        w.write_file(f"/d/dosya_{i:03d}_uzun_bir_ad.txt", b"x")
    w.flush()
    fs = NtfsFS(d)
    f = _index_fields(fs, fs.resolve("/d").number)
    assert f["blok_kume"] == 8 and all(v for v in f.values() if isinstance(v, bool)), f
    d.close()
    if shutil.which("ntfsfix") and shutil.which("ntfsls"):
        ntfsfix_ok(p)
        assert len(ntfsls(p, "/d") - {".", ".."}) == 60
    else:
        raise Atlandi("ntfsfix/ntfsls yok: yalnizca alan denetimi kostu")


# --------------------------------------------------------------------------
def cleanup(prefix: str) -> None:
    """Testin urettigi dosyalari siler (adlari test onekiyle baslar)."""
    if KEEP:
        return
    for ad in os.listdir(TMP):
        if ad.startswith(prefix) or ad.endswith(f"_{os.getpid()}.src"):
            try:
                os.remove(os.path.join(TMP, ad))
            except OSError:
                pass


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    secili = [fn for fn in RESULTS
              if not argv or any(fn.__name__.startswith(a) for a in argv)]
    tamam, basarisiz, atlanan = 0, [], []
    for fn in secili:
        print(f"  {fn.__name__}: {(fn.__doc__ or '').strip()} ... ", end="", flush=True)
        t0 = time.time()
        try:
            fn()
            print(f"TAMAM ({time.time() - t0:.1f} sn)")
            tamam += 1
        except Atlandi as exc:
            print(f"ATLANDI ({exc})")
            atlanan.append((fn.__name__, str(exc)))
        except Exception as exc:                          # noqa: BLE001
            print("BASARISIZ")
            traceback.print_exc()
            basarisiz.append((fn.__name__, str(exc)))
        finally:
            cleanup(fn.__name__.split("_")[0])
    print(f"\nSonuc: {tamam}/{len(secili)} basarili"
          + (f" · {len(atlanan)} atlandi" if atlanan else ""))
    for ad, neden in atlanan:
        print(f"  - {ad} atlandi: {neden}")
    for ad, hata in basarisiz:
        print(f"  ! {ad}: {hata}")
    return 1 if basarisiz else 0


if __name__ == "__main__":
    sys.exit(main())
