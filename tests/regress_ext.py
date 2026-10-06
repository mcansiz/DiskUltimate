"""ext2/3/4 regresyon testleri — mke2fs/debugfs ile yapilmis YABANCI birimler.

Uyumluluk denetimi (2026-10-06, `.claude/logs/2026-10-06-uyumluluk-denetimi.md`,
bulgular E1-E15) testlerimizin yalnizca kendi bicimlendiricimizin urettigi
birimleri kullandigini gosterdi. Buradaki her girdi e2fsprogs araclariyla
kurulur (sabit bag, aygit dugumu, 60 karakterlik bag, seyrek dolayli dosya,
yazilmamis extent, inline_data, needs_recovery, ...). Her yazmadan sonra
`e2fsck -fn` 0 donmelidir.

    python3 -m tests.regress_ext            # hepsi
    python3 -m tests.regress_ext r05 r08    # secilenler (onek)

Arac yoksa test ATLANDI olur, asla sessizce gecmez. Yalnizca goruntu
dosyalari kullanilir; root, baglama, losetup gerekmez.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import struct
import subprocess
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from diskultimate.core import usedmap  # noqa: E402
from diskultimate.core.ext import format_ext  # noqa: E402
from diskultimate.core.extmove import ext_shrink  # noqa: E402
from diskultimate.core.extread import ExtError, ExtFS  # noqa: E402
from diskultimate.core.extresize import ExtResizeError, ext_limits  # noqa: E402
from diskultimate.core.extwrite import ExtWriter  # noqa: E402
from diskultimate.core.filesystem import ExtAccess, open_filesystem  # noqa: E402
from diskultimate.core.fsdetect import detect  # noqa: E402
from diskultimate.core.image import DiskImage  # noqa: E402
from diskultimate.paths import scratch  # noqa: E402

MIB = 1024 * 1024
TMP = scratch("regress_ext")
ENV = dict(os.environ, LANG="C", LC_ALL="C")
TESTS = []


class Atlandi(Exception):
    """Gerekli arac yok / ortam bu testi kuramiyor."""


def test(fn):
    TESTS.append(fn)
    return fn


# ----------------------------------------------------------------------
# Yardimcilar
# ----------------------------------------------------------------------
def tool(name: str) -> str:
    path = shutil.which(name) or shutil.which(name, path="/usr/sbin:/sbin")
    if not path:
        raise Atlandi(f"{name} yok")
    return path


def need_e2fsprogs() -> None:
    for name in ("mke2fs", "debugfs", "e2fsck"):
        tool(name)


def path_of(name: str) -> str:
    return os.path.join(TMP, name)


def blob(name: str, size: int, seed: int = 1) -> str:
    """Belirlenimli icerikli kaynak dosya."""
    p = path_of("src_" + name)
    out = bytearray()
    counter = 0
    while len(out) < size:
        out += hashlib.sha256(b"%d:%d" % (seed, counter)).digest()
        counter += 1
    with open(p, "wb") as fh:
        fh.write(bytes(out[:size]))
    return p


def mkfs(name: str, mb: int, *opts: str) -> str:
    p = path_of(name)
    if os.path.exists(p):
        os.unlink(p)
    with open(p, "wb") as fh:
        fh.truncate(mb * MIB)
    r = subprocess.run([tool("mke2fs"), "-q", "-F", *opts, p],
                       capture_output=True, text=True, env=ENV)
    if r.returncode != 0:
        raise Atlandi(f"mke2fs {' '.join(opts)}: {r.stderr.strip()[:160]}")
    return p


def dbg(img: str, cmds, write: bool = True) -> str:
    """debugfs betigi calistirir; komutlar str ya da (ham ad icin) bytes."""
    script = path_of("debugfs.cmd")
    with open(script, "wb") as fh:
        for c in cmds:
            fh.write((c if isinstance(c, bytes) else c.encode()) + b"\n")
    args = [tool("debugfs")] + (["-w"] if write else []) + ["-f", script, img]
    r = subprocess.run(args, capture_output=True, env=ENV)
    return (r.stdout + r.stderr).decode("utf-8", "replace")


def dbg_cat(img: str, inner: str) -> bytes:
    r = subprocess.run([tool("debugfs"), "-R", f"cat {inner}", img],
                       capture_output=True, env=ENV)
    return r.stdout


def fsck(img: str, what: str) -> None:
    r = subprocess.run([tool("e2fsck"), "-fn", img], capture_output=True,
                       text=True, env=ENV)
    assert r.returncode == 0, (what, (r.stdout + r.stderr)[-900:])


def fsck_problems(img: str) -> set:
    """e2fsck'in sorun satirlari (ozet satirlari haric)."""
    r = subprocess.run([tool("e2fsck"), "-fn", img], capture_output=True,
                       text=True, env=ENV)
    out = set()
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith(("Pass ", "e2fsck ")) or \
                os.path.basename(img) in line or "count wrong" in line:
            continue
        out.add(line)
    return out


def digest(img: str) -> str:
    h = hashlib.sha256()
    with open(img, "rb") as fh:
        for chunk in iter(lambda: fh.read(MIB), b""):
            h.update(chunk)
    return h.hexdigest()


class Vol:
    """Yazilabilir acilis; her islemden sonra `check()` diske bosaltip
    e2fsck calistirir (DiskImage yazmalari tamponlar)."""

    def __init__(self, img: str):
        self.path = img
        self.dev = DiskImage(img)
        self.fs = ExtFS(self.dev)
        self.w = ExtWriter(self.fs)

    def check(self, what: str) -> None:
        self.dev.flush()
        fsck(self.path, what)

    def inode(self, path: str):
        self.fs._inode_cache.clear()
        return self.fs.resolve(path, follow=False)

    def close(self) -> None:
        self.dev.close()


def set_incompat_bit(img: str, bit: int) -> None:
    """Ustblokta incompat bitini dogrudan acar (saglamasiz birim icin)."""
    with open(img, "r+b") as fh:
        fh.seek(1024 + 0x60)
        value = struct.unpack("<I", fh.read(4))[0]
        fh.seek(1024 + 0x60)
        fh.write(struct.pack("<I", value | bit))


# ----------------------------------------------------------------------
# Testler
# ----------------------------------------------------------------------
@test
def r01_sabit_bag_ve_ustveri():
    """E2: sabit bagli dosya silme + ustune yazmada inode ustverisi korunur"""
    need_e2fsprogs()
    for kind in ("ext4", "ext3"):
        img = mkfs(f"r01_{kind}.img", 32, "-t", kind)
        src = blob("r01", 3000)
        dbg(img, [f"write {src} f", "ln f hl2", "sif f links_count 2",
                  "sif f uid 1234", "sif f gid 4321", "sif f mode 0100600"])
        fsck(img, "girdi")
        v = Vol(img)
        new = os.urandom(5000)
        v.w.write_file("/hl2", new)
        v.check(f"{kind}: sabit bagin ustune yazma")
        node = v.inode("/f")
        assert (node.links, node.uid, node.gid, node.mode) == \
            (2, 1234, 4321, 0o100600), (node.links, node.uid, node.gid, oct(node.mode))
        assert dbg_cat(img, "/f") == new, "diger bag yeni icerigi gormuyor"
        v.w.remove("/f")
        v.check(f"{kind}: bir bagi silme")
        assert v.fs.read_data(v.inode("/hl2")) == new
        assert v.inode("/hl2").links == 1
        v.w.remove("/hl2")
        v.check(f"{kind}: son bagi silme")
        v.close()


@test
def r02_ozel_inode_ve_baglar():
    """E3/E13/E10: aygit/fifo/hizli-yavas bag silme, yeniden adlandirma"""
    need_e2fsprogs()
    t59, t60, t100 = "b" * 59, "c" * 60, "d" * 100
    for kind, opts in (("ext2", ("-t", "ext2", "-b", "1024")),
                       ("ext4", ("-t", "ext4"))):
        img = mkfs(f"r02_{kind}.img", 32, *opts)
        dbg(img, ["mknod cdev c 1 3", "mknod bdev b 8 1", "mknod pipe p",
                  f"symlink s59 {t59}", f"symlink s60 {t60}",
                  f"symlink s100 {t100}"])
        fsck(img, "girdi")
        v = Vol(img)
        for name, target in (("s59", t59), ("s60", t60), ("s100", t100)):
            got = v.fs.symlink_target(v.inode("/" + name))
            assert got == target, (kind, name, got)
        for old in ("cdev", "pipe", "s59", "s60"):
            v.w.rename("/" + old, old + "_yeni")
            v.check(f"{kind}: {old} yeniden adlandirma (tur bayti)")
        assert v.fs.symlink_target(v.inode("/s60_yeni")) == t60
        for name in ("cdev_yeni", "bdev", "pipe_yeni", "s59_yeni", "s60_yeni",
                     "s100"):
            v.w.remove("/" + name)
            v.check(f"{kind}: {name} silme")
        v.close()


@test
def r03_utf8_olmayan_ad():
    """E4: latin-1 adli dosya silinir/yeniden adlandirilir, bloklar sizmaz"""
    need_e2fsprogs()
    img = mkfs("r03.img", 32, "-t", "ext4")
    src = blob("r03", 9000)
    dbg(img, [b"write " + src.encode() + b" caf\xe9",
              b"write " + src.encode() + b" na\xefve"])
    fsck(img, "girdi")
    v = Vol(img)
    names = [e.name for e in v.fs.read_dir(v.inode("/"))]
    bad = [n for n in names if "�" in n]
    assert len(bad) == 2, names
    first = next(n for n in bad if n.startswith("caf"))
    second = next(n for n in bad if n.startswith("na"))
    v.w.remove("/" + first)
    v.check("utf-8 olmayan ad silme")
    v.w.rename("/" + second, "naive")
    v.check("utf-8 olmayan ad yeniden adlandirma")
    assert v.fs.read_data(v.inode("/naive")) == open(src, "rb").read()
    names = [e.name for e in v.fs.read_dir(v.inode("/"))]
    assert not any("�" in n for n in names), names
    v.close()


@test
def r04_xattr_blogu():
    """E7: xattr blogu silmede birakilir, paylasilanda sayac azalir"""
    need_e2fsprogs()
    src = blob("r04", 3000)
    # metadata_csum'lu ve saglamasiz; 128 baytlik inode -> xattr blokta
    for label, extra in (("csum", ()), ("csumsuz", ("-O", "^metadata_csum"))):
        img = mkfs(f"r04_{label}.img", 32, "-t", "ext4", "-I", "128", *extra)
        dbg(img, [f"write {src} xa", "ea_set xa user.test deger_uzun_bir_metin",
                  f"write {src} xb", "ea_set xb user.diger 12345"])
        fsck(img, "girdi")
        v = Vol(img)
        assert v.inode("/xa").file_acl, "xattr blogu olusmadi"
        v.w.write_file("/xa", os.urandom(7000))
        v.check(f"{label}: xattr'li dosyanin ustune yazma")
        assert v.inode("/xa").file_acl, "ustune yazmada xattr blogu kayboldu"
        out = dbg(img, ["ea_get xa user.test"], write=False)
        assert "deger_uzun_bir_metin" in out, out
        v.w.remove("/xa")
        v.check(f"{label}: xattr'li dosya silme")
        v.w.remove("/xb")
        v.check(f"{label}: ikinci xattr'li dosya silme")
        v.close()

    # Paylasilan blok (h_refcount = 2): saglamasiz birimde elle kurulur
    img = mkfs("r04_ortak.img", 32, "-t", "ext4", "-I", "128",
               "-O", "^metadata_csum")
    dbg(img, [f"write {src} xa", "ea_set xa user.test ortak", f"write {src} xb"])
    fs = ExtFS(DiskImage(img, readonly=True))
    acl = fs.resolve("/xa").file_acl
    bs = fs.block_size
    sectors = fs.resolve("/xb").blocks512 + bs // 512
    fs.dev.close()
    dbg(img, [f"sif xb file_acl {acl}", f"sif xb blocks {sectors}"])
    with open(img, "r+b") as fh:
        fh.seek(acl * bs + 4)
        fh.write(struct.pack("<I", 2))
    fsck(img, "paylasilan xattr girdisi")
    v = Vol(img)
    v.w.remove("/xa")
    v.check("paylasilan xattr: ilk sahip silindi")
    raw = v.dev.read(acl * bs, 8)
    assert struct.unpack_from("<I", raw, 4)[0] == 1, "refcount azalmadi"
    assert v.fs.read_data(v.inode("/xb")) == open(src, "rb").read()
    v.w.remove("/xb")
    v.check("paylasilan xattr: son sahip silindi")
    v.close()


def _refused(img: str, why: str, *, usedmap_none: bool = False) -> None:
    before = digest(img)
    dev = DiskImage(img)
    try:
        fs = ExtFS(dev)
    except ExtError:
        fs = None
    if fs is not None:
        w = ExtWriter(fs)
        ok, reason = w.write_support()
        assert not ok and reason, (why, reason)
        for op in (lambda: w.write_file("/yeni.txt", b"x" * 100),
                   lambda: w.mkdir("/yeni_klasor")):
            try:
                op()
                raise AssertionError(f"{why}: yazma reddedilmedi")
            except ExtError:
                pass
    if usedmap_none:
        assert usedmap._ext(dev) is None, f"{why}: usedmap bitmap'e guvendi"
    dev.flush()
    dev.close()
    assert digest(img) == before, f"{why}: reddedilen islem birimi degistirdi"


@test
def r05_durum_ve_ozellik_kapilari():
    """E1/E8/E9 + izin listesi: kirli/yabanci ozellikli birime yazilmaz"""
    need_e2fsprogs()
    img = mkfs("r05_recovery.img", 32, "-t", "ext4")
    dbg(img, ["feature needs_recovery"])
    _refused(img, "needs_recovery", usedmap_none=True)
    img = mkfs("r05_state0.img", 32, "-t", "ext4")
    dbg(img, ["ssv state 0"])
    _refused(img, "state=0 (temiz degil)", usedmap_none=True)
    img = mkfs("r05_error.img", 32, "-t", "ext2")
    dbg(img, ["ssv state 3"])
    _refused(img, "state=ERROR", usedmap_none=True)
    img = mkfs("r05_orphan.img", 32, "-t", "ext4")
    dbg(img, ["ssv last_orphan 12"])
    _refused(img, "s_last_orphan")
    for feat in ("mmp", "encrypt", "ea_inode", "verity", "inline_data",
                 "quota", "bigalloc"):
        try:
            img = mkfs(f"r05_{feat}.img", 64, "-t", "ext4", "-O", feat)
        except Atlandi:
            continue                  # bu mke2fs ozelligi bilmiyor
        _refused(img, feat)
    img = mkfs("r05_bilinmeyen.img", 32, "-t", "ext4", "-O", "^metadata_csum")
    set_incompat_bit(img, 0x40000)
    _refused(img, "bilinmeyen incompat biti")

    # Ayri gunluk aygiti: ext degil "jbd"; erisim yazilamaz, usedmap guvenmez
    img = mkfs("r05_jdev.img", 16, "-O", "journal_dev", "-b", "4096")
    dev = DiskImage(img, readonly=True)
    info = detect(dev)
    assert info.fs_type == "jbd", info.fs_type
    access = open_filesystem(dev, info)
    assert not isinstance(access, ExtAccess), "gunluk aygiti ext gibi acildi"
    assert not getattr(access, "writable", False)
    assert usedmap._ext(dev) is None
    dev.close()

    # Temiz birimler yazilabilir kalir (davranis degisikligi yok)
    for kind in ("ext2", "ext3", "ext4"):
        img = mkfs(f"r05_temiz_{kind}.img", 32, "-t", kind)
        v = Vol(img)
        assert v.w.write_support()[0], (kind, v.w.write_support())
        v.close()
        own = path_of(f"r05_kendi_{kind}.img")
        with open(own, "wb") as fh:
            fh.truncate(32 * MIB)
        dev = DiskImage(own)
        format_ext(dev, kind)
        dev.flush()
        w = ExtWriter(ExtFS(dev))
        assert w.write_support()[0], (kind, w.write_support())
        dev.close()
    img = mkfs("r05_casefold.img", 32, "-t", "ext4", "-O", "casefold")
    v = Vol(img)
    assert v.w.write_support()[0], v.w.write_support()
    v.close()


@test
def r06_seyrek_dolayli_dosya():
    """E11: dolayli blok deligi sonraki veriyi kaydirmaz (ext3, 1K blok)"""
    need_e2fsprogs()
    img = mkfs("r06.img", 32, "-t", "ext3", "-b", "1024")
    src = blob("r06", 600 * 1024)
    dbg(img, [f"write {src} f", "punch f 12 267", "punch f 300 310"])
    fsck(img, "girdi")
    data = bytearray(open(src, "rb").read())
    for first, last in ((12, 267), (300, 310)):
        data[first * 1024:(last + 1) * 1024] = bytes((last - first + 1) * 1024)
    expect = bytes(data)
    assert dbg_cat(img, "f") == expect, "debugfs kahini tutmuyor"
    fs = ExtFS(DiskImage(img, readonly=True))
    node = fs.resolve("/f")
    assert struct.unpack_from("<I", node.raw_block, 48)[0] == 0, \
        "senaryo kurulamadi: tek kat dolayli isaretci sifir degil"
    assert fs.read_data(node) == expect, "read_data kaydi"
    assert b"".join(fs.iter_data(node, chunk=8192)) == expect, "iter_data kaydi"
    fs.dev.close()


@test
def r07_yazilmamis_extent():
    """E12: fallocate'li (unwritten) aralik eski icerik degil sifir okunur"""
    need_e2fsprogs()
    img = mkfs("r07.img", 32, "-t", "ext4")
    src = blob("r07", 256 * 1024)
    dbg(img, [f"write {src} f", "punch f 16 31", "fallocate f 16 31"])
    fsck(img, "girdi")
    data = bytearray(open(src, "rb").read())
    data[16 * 4096:32 * 4096] = bytes(16 * 4096)
    expect = bytes(data)
    assert dbg_cat(img, "f") == expect, "debugfs kahini tutmuyor"
    fs = ExtFS(DiskImage(img, readonly=True))
    node = fs.resolve("/f")
    if not any(u for _l, _p, _n, u in fs._extent_map(node)):
        raise Atlandi("debugfs yazilmamis extent uretmedi")
    assert fs.read_data(node) == expect, "read_data eski icerik dondurdu"
    assert b"".join(fs.iter_data(node)) == expect, "iter_data eski icerik"
    fs.dev.close()


@test
def r08_inline_data_okuma():
    """E14: inline dosya ve dizinler listelenir, okunur, disa aktarilir"""
    need_e2fsprogs()
    img = mkfs("r08.img", 32, "-t", "ext4", "-O", "inline_data")
    expect = {}
    cmds = ["mkdir d", "mkdir d/ic", "mkdir d/ic/en_ic", "mkdir devam",
            "mkdir bos"]
    for size in (0, 1, 59, 60, 61, 100, 150, 5000):
        p = blob(f"r08_{size}", size, seed=size)
        for where in ("", "d/", "d/ic/en_ic/"):
            cmds.append(f"write {p} {where}f{size}")
            expect[f"/{where}f{size}"] = open(p, "rb").read()
    small = blob("r08_kucuk", 7, seed=99)
    for i in range(5):                     # i_block'u asan girisler -> xattr
        name = f"devam/giris_{i:03d}"
        cmds.append(f"write {small} {name}")
        expect["/" + name] = open(small, "rb").read()
    cmds += ["symlink kisa " + "k" * 30, "symlink uzun " + "u" * 70]
    dbg(img, cmds)
    fsck(img, "girdi")

    dev = DiskImage(img, readonly=True)
    access = ExtAccess(dev)
    fs = access.fs
    inline_dirs = [p for p in ("/d", "/d/ic", "/devam", "/bos")
                   if fs.resolve(p).is_inline]
    assert inline_dirs, "senaryo kurulamadi: inline dizin yok"
    assert fs.resolve("/f100").is_inline, "f100 inline degil"

    seen = {}
    dirs = []

    def walk(path: str) -> None:
        for n in access.listdir(path):
            if n.is_dir:
                dirs.append(n.path)
                walk(n.path)
            elif not n.attr_text.startswith("->"):
                seen[n.path] = access.read(n.path)
                assert b"".join(access.iter_read(n.path)) == seen[n.path], n.path
    walk("/")
    for p, data in expect.items():
        assert p in seen, f"listede yok: {p}"
        assert seen[p] == data, f"icerik farkli: {p}"
    for d in ("/d", "/d/ic", "/d/ic/en_ic", "/devam", "/bos"):
        assert d in dirs, f"dizin listede yok: {d}"
    assert access.listdir("/bos") == []
    out = path_of("r08_disari.bin")
    access.extract("/d/ic/en_ic/f100", out)
    assert open(out, "rb").read() == expect["/d/ic/en_ic/f100"]
    assert fs.symlink_target(fs.resolve("/kisa", follow=False)) == "k" * 30
    assert fs.symlink_target(fs.resolve("/uzun", follow=False)) == "u" * 70
    # parent ".." dogru
    dd = {e.name: e.inode for e in fs.read_dir(fs.resolve("/d/ic"))}
    assert dd[".."] == fs.resolve("/d").number
    dev.close()
    before = digest(img)
    dev = DiskImage(img)                  # yazilabilir acilis: ret nedeni
    rw = ExtAccess(dev)
    assert not rw.writable and "inline_data" in rw.write_reason, rw.write_reason
    dev.close()
    assert digest(img) == before
    _inline_dir_continuation()


def _inline_dir_continuation() -> None:
    """Girisleri i_block'u asip "system.data" xattr degerine tasan inline dizin.

    libext2fs boyle dizin uretmez (yer bitince bloga cevirir), cekirdek
    uretir. Elle kurulur; dogrulugu e2fsck ve debugfs ile denetlenir.
    """
    img = mkfs("r08_devam.img", 32, "-t", "ext4", "-O",
               "inline_data,^metadata_csum")
    small = blob("r08_devam", 7, seed=7)
    dbg(img, ["mkdir devam", f"write {small} devam/giris_a",
              f"write {small} tmp_b", "unlink tmp_b"])
    dev = DiskImage(img)
    fs = ExtFS(dev)
    node = fs.resolve("/devam")
    assert node.is_inline, "senaryo kurulamadi: dizin inline degil"
    a_ino = next(e.inode for e in fs.read_dir(node) if e.name == "giris_a")
    b_ino = a_ino + 1
    assert fs.read_inode(b_ino).size == 7, "tmp_b inode'u beklenen yerde degil"
    group, index = divmod(node.number - 1, fs.inodes_per_group)
    off = fs._inode_table_block(group) * fs.block_size + index * fs.inode_size
    raw = bytearray(dev.read(off, fs.inode_size))
    first = 128 + struct.unpack_from("<H", raw, 0x80)[0] + 4
    name = b"giris_b"
    value = struct.pack("<IHBB", b_ino, 16, len(name), 1) + name + b"\0"
    area = bytearray(fs.inode_size - first)
    struct.pack_into("<BBHIII", area, 0, 4, 7, 24, 0, len(value), 0)
    area[16:20] = b"data"
    area[24:24 + len(value)] = value
    raw[first:] = area
    struct.pack_into("<I", raw, 4, 60 + len(value))
    dev.write(off, bytes(raw))
    dev.flush()
    dev.close()
    fsck(img, "elle kurulan inline dizin devami")
    assert "giris_b" in dbg(img, ["ls devam"], write=False)
    dev = DiskImage(img, readonly=True)
    access = ExtAccess(dev)
    names = sorted(n.name for n in access.listdir("/devam"))
    assert names == ["giris_a", "giris_b"], names
    assert access.read("/devam/giris_b") == open(small, "rb").read()
    dev.close()


@test
def r09_casefold_dizini():
    """E5: casefold dizinine ad eklenmez; diger dizinler yazilabilir"""
    need_e2fsprogs()
    img = mkfs("r09.img", 32, "-t", "ext4", "-O", "casefold")
    dbg(img, ["mkdir cf", "sif cf flags 0x40080000"])
    fsck(img, "girdi")
    before = digest(img)
    v = Vol(img)
    for op in (lambda: v.w.write_file("/cf/Dosya.txt", b"abc"),
               lambda: v.w.mkdir("/cf/alt")):
        try:
            op()
            raise AssertionError("casefold dizinine yazildi")
        except ExtError:
            pass
    v.dev.flush()
    assert digest(img) == before, "reddedilen islem birimi degistirdi"
    v.w.write_file("/duz.txt", b"merhaba")
    v.check("casefold birimde duz dizine yazma")
    v.w.rename("/duz.txt", "Duz2.txt")
    v.check("casefold birimde yeniden adlandirma")
    v.close()


@test
def r10_dizin_deligi():
    """E10: dizin deligi sonrasi girisler listelenir, silme dogru bloga"""
    need_e2fsprogs()
    img = mkfs("r10.img", 16, "-t", "ext4", "-b", "1024", "-O", "^dir_index")
    src = blob("r10", 100)
    names = ["file_%02d_%s" % (i, "x" * 30) for i in range(1, 61)]
    dbg(img, ["mkdir dd"] + [f"write {src} dd/{n}" for n in names])
    fs = ExtFS(DiskImage(img, readonly=True))
    node = fs.resolve("/dd")
    data = fs._read_content(node)
    second = []
    fs._parse_entries(data[1024:2048], second)
    fs.dev.close()
    assert second, "ikinci dizin blogu bos"
    dbg(img, [f"rm dd/{e.name}" for e in second] + ["punch dd 1 1"])
    base = fsck_problems(img)          # e2fsck delikleri de sorun sayar
    remaining = [n for n in names if n not in {e.name for e in second}]
    v = Vol(img)
    listed = [n.name for n in ExtAccess(v.dev).listdir("/dd")]
    assert sorted(listed) == sorted(remaining), (len(listed), len(remaining))
    victim = remaining[-1]             # delikten sonraki blokta
    v.w.remove("/dd/" + victim)
    v.w.write_file("/dd/yeni_dosya", b"abc")
    v.dev.flush()
    out = dbg(img, ["ls dd"], write=False)
    assert victim not in out and "yeni_dosya" in out, out[-400:]
    assert fsck_problems(img) == base, fsck_problems(img) ^ base
    v.close()


@test
def r11_filetype_yok():
    """E10: filetype ozelligi olmayan birimde tur bayti yazilmaz"""
    need_e2fsprogs()
    img = mkfs("r11.img", 32, "-t", "ext2", "-O", "^filetype")
    fsck(img, "girdi")
    v = Vol(img)
    v.w.mkdir("/a")
    v.check("mkdir")
    v.w.write_file("/a/f", b"x" * 3000)
    v.check("dosya yazma")
    v.w.rename("/a/f", "g")
    v.check("yeniden adlandirma")
    v.w.remove("/a/g")
    v.w.remove("/a")
    v.check("silme")
    v.close()


def _shrink_case(stable: bool) -> str:
    opts = ["-t", "ext4", "-b", "1024", "-N", "64"]
    if stable:
        opts += ["-O", "stable_inodes"]
    img = mkfs(f"r12_{int(stable)}.img", 64, *opts)
    src = blob("r12", 500)
    # 40 dosya gruplara yayilir; ilk 30'u silinince kalan 10 dosya yuksek
    # numarali inode'larda durur ve kucultme onlari tasimak zorunda kalir.
    dbg(img, [f"write {src} f{i:02d}" for i in range(40)]
        + [f"rm f{i:02d}" for i in range(30)])
    fsck(img, "girdi")
    return img


@test
def r12_stable_inodes_kucultme():
    """E6: stable_inodes birimde inode numarasi degistiren kucultme reddedilir"""
    need_e2fsprogs()
    img = _shrink_case(False)
    dev = DiskImage(img)
    target = ext_limits(dev)["min_bytes"]
    ext_shrink(dev, target)
    dev.flush()
    dev.close()
    fsck(img, "kontrol: stable_inodes'suz kucultme")

    img = _shrink_case(True)
    before = digest(img)
    dev = DiskImage(img)
    try:
        ext_shrink(dev, target)
        raise AssertionError("stable_inodes kucultmesi reddedilmedi")
    except ExtResizeError as exc:
        assert "stable_inodes" in str(exc), exc
    dev.flush()
    dev.close()
    assert digest(img) == before, "reddedilen kucultme birimi degistirdi"


@test
def r13_boyutlandirma_izin_listesi():
    """Boyutlandirma bilinmeyen ozellik bitini ve kirli birimi reddeder"""
    need_e2fsprogs()
    img = mkfs("r13.img", 32, "-t", "ext4", "-O", "^metadata_csum")
    set_incompat_bit(img, 0x40000)
    dev = DiskImage(img, readonly=True)
    try:
        ext_limits(dev)
        raise AssertionError("bilinmeyen ozellikli birim boyutlandirilabilir")
    except ExtResizeError:
        pass
    dev.close()
    img = mkfs("r13_orphan.img", 32, "-t", "ext4")
    dbg(img, ["ssv last_orphan 12"])
    dev = DiskImage(img, readonly=True)
    try:
        ext_limits(dev)
        raise AssertionError("yetim listeli birim boyutlandirilabilir")
    except ExtResizeError:
        pass
    dev.close()


@test
def r14_tur_tespiti():
    """E15/E9: ext2/3/4/jbd ayrimi blkid kuraliyla; kendi birimlerimiz ayni"""
    need_e2fsprogs()
    cases = [("ext2", ("-t", "ext2")), ("ext3", ("-t", "ext3")),
             ("ext4", ("-t", "ext4")),
             ("ext4", ("-t", "ext4", "-O", "^extent,^huge_file,^64bit")),
             ("ext4", ("-t", "ext3", "-O", "metadata_csum")),
             ("jbd", ("-O", "journal_dev", "-b", "4096"))]
    for i, (want, opts) in enumerate(cases):
        img = mkfs(f"r14_{i}.img", 16, *opts)
        dev = DiskImage(img, readonly=True)
        got = detect(dev).fs_type
        dev.close()
        assert got == want, (opts, got)
    from diskultimate.core.recovery import _probe_modern
    with open(path_of("r14_5.img"), "rb") as fh:
        head = fh.read(0x11000)
    dev = DiskImage(path_of("r14_5.img"), readonly=True)
    assert _probe_modern(head, dev)[0] == "jbd"
    dev.close()
    for kind, modern in (("ext2", True), ("ext3", True), ("ext4", True),
                         ("ext4", False)):
        own = path_of(f"r14_kendi_{kind}_{int(modern)}.img")
        with open(own, "wb") as fh:
            fh.truncate(32 * MIB)
        dev = DiskImage(own)
        format_ext(dev, kind, modern=modern)
        dev.flush()
        assert detect(dev).fs_type == kind, (kind, modern, detect(dev).fs_type)
        dev.close()


# ----------------------------------------------------------------------
def cleanup() -> None:
    if os.environ.get("DISKULTIMATE_KEEP_TEST_FILES") == "1":
        return
    for name in os.listdir(TMP):
        try:
            os.unlink(os.path.join(TMP, name))
        except OSError:
            pass


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    selected = [t for t in TESTS
                if not argv or any(t.__name__.startswith(a) for a in argv)]
    ok, failed, skipped = 0, [], []
    for fn in selected:
        print(f"  {fn.__name__}: {(fn.__doc__ or '').strip()} ... ",
              end="", flush=True)
        try:
            fn()
            print("TAMAM")
            ok += 1
        except Atlandi as exc:
            print(f"ATLANDI ({exc})")
            skipped.append(fn.__name__)
        except Exception as exc:              # noqa: BLE001
            print("BASARISIZ")
            traceback.print_exc()
            failed.append((fn.__name__, str(exc)[:200]))
        finally:
            cleanup()
    print(f"\nSonuc: {ok}/{len(selected)} basarili"
          + (f" · {len(skipped)} atlandi" if skipped else ""))
    for name, err in failed:
        print(f"  ! {name}: {err}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
