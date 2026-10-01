"""ext boyutlandirma — gercek e2fsprogs birimlerine karsi genis denetim.

`tests.run_all` icindeki t48 kendi bicimlendiricimizin birimleriyle calisir
(her platformda). Bu denetim ise `mkfs.ext2/3/4` ile olusturulmus **gercek
dunya** birimlerini kullanir: 64bit, flex_bg, metadata_csum, orphan_file,
resize_inode, meta_bg, eski uninit_bg (crc16), 1K blok. Her adimdan sonra
`e2fsck -fn` temiz olmali ve `debugfs rdump` ile cikarilan agac kaynakla
**birebir** ayni olmali.

Calistirma (e2fsprogs gerekir; yalnizca goruntu dosyasi kullanir):
    python3 -m tests.ext_resize_check          # tam matris (~5 dk)
    python3 -m tests.ext_resize_check --quick  # temsilci alt kume

Senaryolar:
  * buyutme matrisi — ayrilmis GDT tukenince meta_bg'ye gecis dahil
  * zincirli buyutme (var olan meta_bg birim tekrar buyur)
  * kucultme — gunluk (ortadaki grup) tasinir
  * kucultme — inode yeniden numaralama (dusuk gruplar bosaltilir)
  * kucultme — htree dizininin kendisi tasinir (dx saglamalari)
"""
from __future__ import annotations

import hashlib
import os
import random
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from diskultimate.core.extmove import _Shrinker, min_blocks_for  # noqa: E402
from diskultimate.core.extresize import _Volume, ext_grow  # noqa: E402
from diskultimate.core.image import DiskImage  # noqa: E402
from diskultimate.paths import scratch  # noqa: E402

TMP = scratch("ext_resize_check")
QUICK = "--quick" in sys.argv


def _tool(name: str) -> str:
    return shutil.which(name) or shutil.which(name, path="/sbin:/usr/sbin") or ""


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _tree(root: str) -> dict:
    out = {}
    for d, dirs, files in os.walk(root):
        for f in files + [x for x in dirs if os.path.islink(os.path.join(d, x))]:
            p = os.path.join(d, f)
            rel = os.path.relpath(p, root)
            out[rel] = ("L:" + os.readlink(p)) if os.path.islink(p) else \
                hashlib.sha1(open(p, "rb").read()).hexdigest()
    return out


def build_source(root: str) -> None:
    """htree dizini, bag turleri, xattr, seyrek dosya iceren kaynak agac."""
    if os.path.isdir(root):
        return
    rnd = random.Random(7)
    os.makedirs(os.path.join(root, "big"))
    os.makedirs(os.path.join(root, "d", "e", "f"))
    for i in range(3000):
        with open(os.path.join(root, "big", f"entry_with_a_longish_name_{i:05d}.txt"), "w") as f:
            f.write(str(i) * rnd.randint(1, 50))
    for i in range(150):
        with open(os.path.join(root, "d", f"f{i}.bin"), "wb") as f:
            f.write(rnd.randbytes(rnd.randint(1000, 900000)))
    for i in range(40):
        with open(os.path.join(root, "d", "e", "f", f"g{i}.dat"), "wb") as f:
            f.write(rnd.randbytes(rnd.randint(1, 3000000)))
    os.link(os.path.join(root, "d", "f1.bin"), os.path.join(root, "hard1"))
    os.link(os.path.join(root, "d", "f1.bin"), os.path.join(root, "d", "e", "hard2"))
    os.symlink("d/f2.bin", os.path.join(root, "sym_fast"))
    os.symlink("x" * 200, os.path.join(root, "sym_slow"))
    with open(os.path.join(root, "sparse.bin"), "wb") as f:
        f.seek(50 * 1024 * 1024)
        f.write(b"end")
        f.seek(10 * 1024 * 1024)
        f.write(b"mid")
    for i in range(20):
        try:
            os.setxattr(os.path.join(root, "d", f"f{i}.bin"), "user.test",
                        (f"deger{i}").encode() * 20)
        except (OSError, AttributeError):
            break


class Checker:
    def __init__(self):
        self.fail = 0
        self.count = 0

    def verify(self, img: str, expected: dict, label: str, extra: str = "") -> None:
        self.count += 1
        r = _run([_tool("e2fsck"), "-fn", img])
        out = os.path.join(TMP, "out")
        shutil.rmtree(out, ignore_errors=True)
        os.mkdir(out)
        _run([_tool("debugfs"), "-R", f"rdump / {out}", img])
        got = _tree(out)
        got.pop("lost+found", None)
        diff = [k for k, v in expected.items() if got.get(k) != v]
        more = [k for k in got if k not in expected]
        ok = r.returncode == 0 and not diff and not more
        print(("  TAMAM " if ok else "  BOZUK ") + label + (" " + extra if extra else ""))
        if not ok:
            self.fail += 1
            print(f"    e2fsck={r.returncode} eksik/farkli={diff[:5]} fazla={more[:5]}")
            print("    " + r.stdout[-1500:].replace("\n", "\n    "))


def make(img: str, mkfs: str, opts: str, size: str, src: str) -> None:
    if os.path.exists(img):
        os.remove(img)
    _run(["truncate", "-s", size, img])
    if mkfs == "du-ext4":
        make_own(img, opts, src)
        return
    r = _run([_tool(mkfs), "-q", "-F", "-d", src] + opts.split() + [img])
    if r.returncode:
        raise RuntimeError(f"{mkfs} {opts}: {r.stderr}")


def make_own(img: str, opts: str, src: str) -> None:
    """Kendi bicimlendiricimiz + libext2fs ile (debugfs write) doldurma.

    Bicimlendiricinin urettigi birimi baska bir uygulamanin (e2fsprogs)
    yazma yolundan gecirir: extent, flex_bg ve metadata_csum dogru
    kurulmamissa debugfs ya da sonraki e2fsck yakalar (ADR 0073).
    """
    from diskultimate.core.ext import format_ext
    d = DiskImage(img)
    try:
        bs = 1024 if "-b 1024" in opts else 0
        format_ext(d, "ext4", label="DUEXT4", block_size=bs)
    finally:
        d.close()
    cmds = os.path.join(TMP, "cmds")
    with open(cmds, "w") as f:
        for d_, dirs, files in os.walk(src):
            rel = os.path.relpath(d_, src)
            dest = "/" if rel == "." else "/" + rel.replace(os.sep, "/")
            for x in sorted(dirs):
                f.write(f"mkdir {dest.rstrip('/')}/{x}\n")
            for x in sorted(files):
                f.write(f"write {os.path.join(d_, x)} {dest.rstrip('/')}/{x}\n")
    r = _run([_tool("debugfs"), "-w", "-f", cmds, img])
    if r.returncode or "rror" in r.stderr:
        raise RuntimeError(f"debugfs write: {r.stderr[-400:]}")


def grow_to(img: str, size: str) -> int:
    _run(["truncate", "-s", size, img])
    d = DiskImage(img)
    try:
        return ext_grow(d)
    finally:
        d.close()


def shrink_min(img: str) -> tuple:
    d = DiskImage(img)
    try:
        v = _Volume(d)
        bs = v.geo.block_size
        s = _Shrinker(d, min_blocks_for(v), None)
        n = s.run()
    finally:
        d.close()
    _run(["truncate", "-s", str(n * bs), img])
    return n, len(s.ino_map), len(s.block_moves)


def remove_low_entries(img: str, count: int) -> set:
    """`/big` icindeki en dusuk numarali girisleri siler (inode tasima zorlanir)."""
    out = _run([_tool("debugfs"), "-R", "ls -l /big", img]).stdout
    ents = sorted((int(p.split()[0]), p.split()[-1]) for p in out.splitlines()
                  if len(p.split()) >= 9 and p.split()[-1].startswith("entry_"))
    victims = [n for _, n in ents[:count]]
    cmds = os.path.join(TMP, "cmds")
    with open(cmds, "w") as f:
        f.write("".join(f"rm /big/{n}\n" for n in victims))
    _run([_tool("debugfs"), "-w", "-f", cmds, img])
    return set(victims)


def main() -> int:
    for t in ("mkfs.ext4", "e2fsck", "debugfs"):
        if not _tool(t):
            print(f"ATLANDI: {t} yok (e2fsprogs gerekir)")
            return 0
    os.makedirs(TMP, exist_ok=True)
    src_small = os.path.join(TMP, "src_small")
    if not os.path.isdir(src_small):
        os.makedirs(os.path.join(src_small, "a", "b"))
        rnd = random.Random(1)
        for i in range(40):
            with open(os.path.join(src_small, f"f{i}.bin"), "wb") as f:
                f.write(rnd.randbytes(rnd.randint(0, 300000)))
        for i in range(300):
            with open(os.path.join(src_small, "a", "b", f"n{i}.txt"), "w") as f:
                f.write("x" * i)
    src = os.path.join(TMP, "src_big")
    build_source(src)
    small, big = _tree(src_small), _tree(src)
    c = Checker()
    img = os.path.join(TMP, "e.img")

    print("Buyutme matrisi")
    grow_cases = [
        ("mkfs.ext4", "", "64M", "200M"),
        ("mkfs.ext4", "", "64M", "30G"),
        ("mkfs.ext4", "-O ^resize_inode", "64M", "30G"),
        ("mkfs.ext4", "-b 1024", "20M", "900M"),
        ("mkfs.ext4", "-b 1024 -O ^resize_inode", "20M", "900M"),
        ("mkfs.ext4", "-O ^metadata_csum,uninit_bg", "64M", "5G"),
        ("mkfs.ext4", "-O ^64bit", "64M", "5G"),
        ("mkfs.ext4", "-O ^flex_bg", "64M", "2G"),
        ("mkfs.ext4", "-O ^metadata_csum,^uninit_bg", "64M", "1G"),
        ("mkfs.ext3", "", "64M", "1G"),
        ("mkfs.ext2", "", "64M", "1G"),
        ("mkfs.ext2", "-b 1024", "16M", "300M"),
        ("mkfs.ext4", "-O meta_bg,^resize_inode", "64M", "30G"),
        ("mkfs.ext4", "", "100M", "133M"),
        ("du-ext4", "", "64M", "5G"),
        ("du-ext4", "-b 1024", "20M", "900M"),
    ]
    if QUICK:
        grow_cases = [grow_cases[i] for i in (1, 2, 4, 5, 9, 14)]
    for mk, opts, old, new in grow_cases:
        make(img, mk, opts, old, src_small)
        n = grow_to(img, new)
        c.verify(img, small, f"{mk} {opts or '-'} {old}->{new}", f"({n} blok)")

    print("Zincirli buyutme (meta_bg birim tekrar buyur)")
    make(img, "mkfs.ext4", "-O ^resize_inode", "32M", src_small)
    for size in ("3G", "40G", "41G"):
        grow_to(img, size)
    c.verify(img, small, "ext4 ^resize_inode 32M->3G->40G->41G")
    make(img, "du-ext4", "", "32M", src_small)
    for size in ("3G", "40G", "41G"):
        grow_to(img, size)
    c.verify(img, small, "du-ext4 32M->3G->40G->41G")

    print("Kucultme: kendi bicimlendiricimizin birimi")
    make(img, "du-ext4", "", "3G", src_small)
    n, mi, mb = shrink_min(img)
    c.verify(img, small, "du-ext4 3G->en kucuk", f"({n} blok, {mb} blok tasindi)")

    print("Kucultme: gunluk tasinir")
    for mk, opts in [("mkfs.ext4", ""), ("mkfs.ext3", ""), ("mkfs.ext4", "-b 1024"),
                     ("mkfs.ext4", "-O ^metadata_csum,uninit_bg")][:2 if QUICK else 4]:
        make(img, mk, opts, "3G", src)
        n, mi, mb = shrink_min(img)
        c.verify(img, big, f"{mk} {opts or '-'} 3G->en kucuk", f"({n} blok, {mb} blok tasindi)")

    print("Kucultme: inode yeniden numaralama")
    shrink_cases = [
        ("mkfs.ext4", "-N 4400", "3G"),
        ("mkfs.ext4", "-b 1024 -N 6000", "1G"),
        ("mkfs.ext4", "-N 4400 -O ^metadata_csum,uninit_bg", "3G"),
        ("mkfs.ext4", "-N 4400 -O ^flex_bg", "3G"),
        ("mkfs.ext3", "-N 4400", "3G"),
        ("mkfs.ext2", "-N 4400", "3G"),
    ]
    if QUICK:
        shrink_cases = shrink_cases[:2]
    for mk, opts, size in shrink_cases:
        make(img, mk, opts, size, src)
        gone = remove_low_entries(img, 2600)
        exp = {k: v for k, v in big.items() if not (k.startswith("big/") and k[4:] in gone)}
        n, mi, mb = shrink_min(img)
        assert mi > 0, "inode tasima zorlanamadi"
        c.verify(img, exp, f"{mk} {opts} {size}->en kucuk", f"({mi} inode tasindi)")

    print("Kucultme: htree dizininin kendisi tasinir")
    small_txt = os.path.join(TMP, "small.txt")
    with open(small_txt, "w") as f:
        f.write("icerik\n" * 30)
    for mk, opts in [("mkfs.ext4", "-N 4400"), ("mkfs.ext4", "-b 1024 -N 6000"),
                     ("mkfs.ext3", "-N 4400")][:1 if QUICK else 3]:
        make(img, mk, opts, "2G", src)
        cmds = os.path.join(TMP, "cmds")
        with open(cmds, "w") as f:
            f.write("mkdir /late\n" + "".join(
                f"write {small_txt} /late/file_number_{i:04d}.txt\n" for i in range(800)))
        _run([_tool("debugfs"), "-w", "-f", cmds, img])
        _run([_tool("e2fsck"), "-fyD", img])            # /late htree olur
        gone = remove_low_entries(img, 2900)
        exp = {k: v for k, v in big.items() if not (k.startswith("big/") and k[4:] in gone)}
        digest = hashlib.sha1(open(small_txt, "rb").read()).hexdigest()
        exp.update({f"late/file_number_{i:04d}.txt": digest for i in range(800)})
        n, mi, mb = shrink_min(img)
        c.verify(img, exp, f"{mk} {opts} htree tasima", f"({mi} inode tasindi)")

    print(f"\nSonuc: {c.count - c.fail}/{c.count} temiz")
    return 1 if c.fail else 0


if __name__ == "__main__":
    sys.exit(main())
