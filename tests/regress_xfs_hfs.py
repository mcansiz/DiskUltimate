"""XFS buyutme (kucuk blok / buyuk sektor) ve HFS+ eski Yunanca ayristirma
regresyon testleri.

    python3 -m tests.regress_xfs_hfs

1. XFS: 1 KiB blokta AG basliklari (4 sektor) iki bloga tasar; yeni AG'de
   bnobt koku AGI/AGFL'nin ustune yaziliyor, rmapbt OWN_FS kaydi 1 blok
   kaliyordu (xfs_repair: "Reverse Mapping BTree record corruption").
2. HFS+: Linux hfsplus surucusu (ve Mac OS 8.1-10.2) Unicode 2.1
   ayristirmasiyla "ά"yi 03B1 030D saklar; okuyucu 030D'yi birlestiremedigi
   icin ad listede bozuk gorunuyor, yol aramasi tutmuyordu.

Arac yoksa (mkfs.xfs, xfs_repair) ilgili test ATLANDI yazar, sessizce gecmez.
"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import traceback
import unicodedata

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from diskultimate.core.image import DiskImage  # noqa: E402
from diskultimate.paths import scratch  # noqa: E402

MIB = 1024 * 1024


class Skip(Exception):
    pass


def _work(name: str) -> str:
    return os.path.join(scratch("regress_xfs_hfs"), name)


def _tool(name: str) -> str:
    exe = shutil.which(name) or shutil.which(name, path="/sbin:/usr/sbin")
    if not exe:
        raise Skip(f"{name} yok")
    return exe


# ---------------------------------------------------------------------------
# XFS
# ---------------------------------------------------------------------------
def _fake_sb(bs: int, ss: int, ro_compat: int):
    from diskultimate.core.xfsgrow import _Sb
    raw = bytearray(512)
    raw[:4] = b"XFSB"
    struct.pack_into(">I", raw, 4, bs)
    struct.pack_into(">II", raw, 84, 8192, 4)
    struct.pack_into(">H", raw, 100, 5)
    struct.pack_into(">H", raw, 102, ss)
    raw[124] = 13
    struct.pack_into(">I", raw, 212, ro_compat)
    return _Sb(bytes(raw))


def test_xfs_layout_formula():
    """Yeni AG yerlesimi cekirdek formulune uyar (XFS_AGFL_BLOCK + 1 ...)."""
    from diskultimate.core.xfsgrow import (OWN_FS, RO_FINOBT, RO_REFLINK,
                                           RO_RMAPBT, _layout, _rmap_records,
                                           new_ag_blocks)
    for bs, ss in ((512, 512), (1024, 512), (1024, 1024), (2048, 512),
                   (2048, 2048), (4096, 512), (4096, 4096)):
        for ro in (0, RO_FINOBT, RO_FINOBT | RO_RMAPBT,
                   RO_FINOBT | RO_RMAPBT | RO_REFLINK):
            sb = _fake_sb(bs, ss, ro)
            lay = _layout(sb)
            agfl_block = (3 * ss) // bs
            assert lay["bno"] == agfl_block + 1, (bs, ss, lay)
            assert lay["cnt"] == lay["bno"] + 1 and lay["ino"] == lay["bno"] + 2
            if lay["rmap"]:
                recs = _rmap_records(lay)
                assert recs[0] == (0, lay["bno"], OWN_FS), (bs, ss, recs)
                pos = 0
                for s0, c, _o in recs:              # bosluksuz, cakismasiz
                    assert s0 == pos, (bs, ss, recs)
                    pos = s0 + c
                assert pos == lay["prealloc"] + lay["agfl"], (bs, ss, recs)
            blocks, _free = new_ag_blocks(sb, bytes(512), 5, 4000)
            spans = sorted((b * bs, b * bs + len(d)) for b, d in blocks)
            for (a0, a1), (b0, _b1) in zip(spans, spans[1:]):
                assert a1 <= b0, (bs, ss, ro, spans)  # yazimlar cakismaz
            head = dict(blocks)[0]
            assert len(head) >= 4 * ss
            assert head[ss:ss + 4] == b"XAGF" and head[2 * ss:2 * ss + 4] == b"XAGI"
            assert head[3 * ss:3 * ss + 4] == b"XAFL"


def _xfs_tree(root: str) -> dict:
    files = {
        "a.txt": b"merhaba\n",
        "Ελληνικά αρχεία.bin": bytes(range(256)) * 40,
        "Türkçe ğüşİı.txt": "içerik\n".encode("utf-8"),
        "buyuk.bin": bytes((i * 7) & 0xFF for i in range(3 * MIB)),
    }
    for i in range(120):
        files[f"klasor/alt/f_{i:03d}.dat"] = bytes([i]) * (i * 37)
    for rel, data in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(data)
    return {"/" + k: v for k, v in files.items()}


def _xfs_protofile(root: str, path: str) -> set:
    """`mkfs.xfs -p <klasor>` bilmeyen surumler (xfsprogs < 6.x, Ubuntu 24.04
    6.6) icin klasik protofile. Bicim bosluga gore ayrildigi icin bosluklu
    adlar alinamaz; alinan goreli yollari dondurur."""
    lines = ["/dev/null", "0 0", "d--755 0 0"]
    taken = set()

    def walk(directory: str, rel: str) -> None:
        for name in sorted(os.listdir(directory)):
            full = os.path.join(directory, name)
            if any(c.isspace() for c in name):
                continue
            if os.path.isdir(full):
                lines.append(f"{name} d--755 0 0")
                walk(full, rel + name + "/")
                lines.append("$")
            else:
                lines.append(f"{name} ---644 0 0 {full}")
                taken.add("/" + rel + name)

    walk(root, "")
    lines.append("$")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return taken


XFS_CASES = [
    ("b1024", ["-b", "size=1024"]),
    ("b1024-s1024", ["-b", "size=1024", "-s", "size=1024"]),
    ("b2048", ["-b", "size=2048"]),
    ("b2048-s2048", ["-b", "size=2048", "-s", "size=2048"]),
    ("b4096", []),
    ("b4096-s4096", ["-s", "size=4096"]),
    ("b1024-normap", ["-b", "size=1024", "-m", "rmapbt=0"]),
    ("b1024-noreflink", ["-b", "size=1024", "-m", "reflink=0"]),
    ("b1024-sade", ["-b", "size=1024", "-m", "rmapbt=0,reflink=0,finobt=0"]),
    ("b4096-sade", ["-m", "rmapbt=0,reflink=0,finobt=0"]),
]


def _xfs_grow_case(tag: str, opts):
    from diskultimate.core.xfs import XfsFS
    from diskultimate.core.xfsgrow import xfs_grow
    mkfs, repair = _tool("mkfs.xfs"), _tool("xfs_repair")
    root = _work(f"xfs_{tag}_kaynak")
    shutil.rmtree(root, ignore_errors=True)
    expected = _xfs_tree(root)
    img = _work(f"xfs_{tag}.img")
    if os.path.exists(img):
        os.unlink(img)
    with open(img, "wb") as fh:
        fh.truncate(320 * MIB)
    # agsize=100m: 3 tam + 20 MiB'lik kisa son AG -> hem uzatma hem yeni AG
    r = subprocess.run([mkfs, "-q", "-f", *opts, "-d", "agsize=100m", "-p", root, img],
                       capture_output=True, text=True)
    if r.returncode != 0 and "Is a directory" in r.stderr:
        # Eski mkfs.xfs klasoru protofile sanar: klasik protofile ile doldur.
        proto = img + ".proto"
        taken = _xfs_protofile(root, proto)
        expected = {k: v for k, v in expected.items() if k in taken}
        r = subprocess.run([mkfs, "-q", "-f", *opts, "-d", "agsize=100m", "-p", proto, img],
                           capture_output=True, text=True)
        os.unlink(proto)
    if r.returncode != 0:
        raise Skip(f"mkfs.xfs {' '.join(opts)} reddetti: {r.stderr.strip()[-200:]}")
    with open(img, "r+b") as fh:
        fh.truncate(900 * MIB)
    d = DiskImage(img)
    try:
        plan = xfs_grow(d, 900 * MIB)
    finally:
        d.close()
    assert plan["new_agcount"] > plan["old_agcount"], plan
    r = subprocess.run([repair, "-n", "-f", img], capture_output=True, text=True)
    assert r.returncode == 0, f"xfs_repair ({tag}): " + (r.stdout + r.stderr)[-1500:]
    d = DiskImage(img, readonly=True)
    try:
        fs = XfsFS(d)
        for path, data in expected.items():
            assert fs.read_file(path) == data, (tag, path)
    finally:
        d.close()
    os.unlink(img)
    shutil.rmtree(root, ignore_errors=True)


def _make_xfs_test(tag, opts):
    def t():
        _xfs_grow_case(tag, opts)
    t.__name__ = f"test_xfs_grow_{tag}"
    t.__doc__ = f"mkfs.xfs {' '.join(opts) or '(varsayilan)'} -> buyut -> xfs_repair -n"
    return t


def test_xfs_grow_v4_refused():
    """v4 (crc=0) birim reddedilir ve dokunulmaz."""
    from diskultimate.core.xfsgrow import XfsGrowError, xfs_grow
    mkfs = _tool("mkfs.xfs")
    img = _work("xfs_v4.img")
    if os.path.exists(img):
        os.unlink(img)
    with open(img, "wb") as fh:
        fh.truncate(320 * MIB)
    r = subprocess.run([mkfs, "-q", "-f", "-m", "crc=0", "-b", "size=1024", img],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise Skip(f"mkfs.xfs -m crc=0 reddetti: {r.stderr.strip()[-200:]}")
    with open(img, "rb") as fh:
        before = fh.read(MIB)
    with open(img, "r+b") as fh:
        fh.truncate(900 * MIB)
    d = DiskImage(img)
    try:
        xfs_grow(d, 900 * MIB)
        raise AssertionError("v4 XFS buyutuldu")
    except XfsGrowError:
        pass
    finally:
        d.close()
    with open(img, "rb") as fh:
        assert fh.read(MIB) == before
    os.unlink(img)


# ---------------------------------------------------------------------------
# HFS+
# ---------------------------------------------------------------------------
# Linux fs/hfsplus/tables.c (Unicode 2.1) icinde 030D tasiyan ayristirmalar
LINUX_TONOS = {
    0x0385: [0x00A8, 0x030D], 0x0386: [0x0391, 0x030D], 0x0388: [0x0395, 0x030D],
    0x0389: [0x0397, 0x030D], 0x038A: [0x0399, 0x030D], 0x038C: [0x039F, 0x030D],
    0x038E: [0x03A5, 0x030D], 0x038F: [0x03A9, 0x030D],
    0x0390: [0x03B9, 0x0308, 0x030D], 0x03AC: [0x03B1, 0x030D],
    0x03AD: [0x03B5, 0x030D], 0x03AE: [0x03B7, 0x030D], 0x03AF: [0x03B9, 0x030D],
    0x03B0: [0x03C5, 0x0308, 0x030D], 0x03CC: [0x03BF, 0x030D],
    0x03CD: [0x03C5, 0x030D], 0x03CE: [0x03C9, 0x030D], 0x03D3: [0x03D2, 0x030D],
}


def linux_decompose(text: str) -> str:
    """Linux hfsplus_asc2uni'nin Yunanca tonos'lu harfler icin urettigi bicim;
    gerisi Unicode 3.2 NFD (bu testteki adlar icin ikisi ayni)."""
    from diskultimate.core.hfsunicode import hfs_nfd
    out = []
    for ch in text:
        seq = LINUX_TONOS.get(ord(ch))
        out.append("".join(map(chr, seq)) if seq else hfs_nfd(ch))
    return "".join(out)


def test_hfs_legacy_tonos_display():
    """Unicode 2.1 (030D) ve 3.2 (0301) ayristirmasi ayni NFC ada doner."""
    from diskultimate.core.hfsunicode import fold_key, hfs_display, hfs_nfd, legacy_tonos
    for cp, seq in LINUX_TONOS.items():
        raw = "".join(map(chr, seq))
        want = unicodedata.normalize("NFC", chr(cp))
        assert hfs_display(raw) == want, (hex(cp), raw, hfs_display(raw))
        assert fold_key(hfs_nfd(legacy_tonos(raw))) == fold_key(hfs_nfd(chr(cp))), hex(cp)
    # 3.2 bicimi ve Latin harf uzerindeki gercek 030D degismez
    assert hfs_display(hfs_nfd("ά")) == "ά"
    assert legacy_tonos("a̍") == "a̍"
    assert hfs_display(linux_decompose("Ελληνικά αρχεία.bin")) == "Ελληνικά αρχεία.bin"


def test_hfs_linux_written_greek_name():
    """Linux surucusunun yazdigi (030D) Yunanca ad listelenir, okunur,
    buyuk/kucuk harf duyarsiz bulunur, ustune yazilir ve silinir."""
    from diskultimate.core.hfsformat import format_hfsplus
    from diskultimate.core.hfsplus import HfsPlusFS
    from diskultimate.core.hfswrite import HfsWriter
    name = "Ελληνικά αρχεία.bin"
    img = _work("hfs_tonos.img")
    if os.path.exists(img):
        os.unlink(img)
    with open(img, "wb") as fh:
        fh.truncate(16 * MIB)
    d = DiskImage(img)
    format_hfsplus(d, label="YABANCI")
    d.close()

    d = DiskImage(img)
    w = HfsWriter(HfsPlusFS(d))
    w.mkdir("/belgeler")
    w.mkdir("/belgeler/alt dizin")
    w.write_file("/belgeler/alt dizin/UPPER.TXT", b"upper\n")
    w.write_file("/belgeler/alt dizin/Русский файл.txt", b"ru\n")
    # cekirdegin asc2uni'si gibi: ad 2.1 bicimiyle (03B1 030D) saklanir
    w._disk_name = lambda n: linux_decompose(n.replace(":", "/"))
    w.write_file("/belgeler/alt dizin/" + name, b"yunanca\n" * 100)
    w.mkdir("/Ελληνικά")
    w.write_file("/Ελληνικά/ά.txt", b"a\n")
    del w._disk_name
    w.flush()
    d.close()

    d = DiskImage(img)
    fs = HfsPlusFS(d)
    raw = [e.raw_name for e in fs.listdir("/belgeler/alt dizin")]
    assert linux_decompose(name) in raw, raw          # gercekten 030D saklandi
    names = sorted(e.name for e in fs.listdir("/belgeler/alt dizin"))
    assert names == sorted(["UPPER.TXT", "Русский файл.txt", name]), names
    assert fs.read_file("/belgeler/alt dizin/" + name) == b"yunanca\n" * 100
    assert fs.read_file("/belgeler/alt dizin/" + name.upper()) == b"yunanca\n" * 100
    assert fs.read_file("/Ελληνικά/ά.txt") == b"a\n"
    assert [e.name for e in fs.listdir("/")].count("Ελληνικά") == 1

    # yazici: ayni (NFC) adla ustune yazma eski kaydi bulur, ikinci kayit acmaz
    fsck = shutil.which("fsck.hfsplus") or shutil.which("fsck.hfsplus", path="/sbin:/usr/sbin")
    baseline = _hfs_problems(fsck, img) if fsck else set()
    w = HfsWriter(fs)
    w.write_file("/belgeler/alt dizin/" + name, b"yeni\n")
    assert len(w.fs.listdir("/belgeler/alt dizin")) == 3
    assert w.fs.read_file("/belgeler/alt dizin/" + name) == b"yeni\n"
    w.rename("/Ελληνικά/ά.txt", "β.txt")
    w.remove("/belgeler/alt dizin/" + name)
    w.flush()
    d.close()

    d = DiskImage(img, readonly=True)
    fs = HfsPlusFS(d)
    names = sorted(e.name for e in fs.listdir("/belgeler/alt dizin"))
    assert names == ["UPPER.TXT", "Русский файл.txt"], names
    assert [e.name for e in fs.listdir("/Ελληνικά")] == ["β.txt"]
    d.close()
    if fsck:
        # Cekirdegin yazdigi Unicode 2.1 (U+030D) adlari Apple'in fsck_hfs'i
        # zaten "Illegal name" sayar — Linux'un yazdigi gercek birimde de.
        # Bizim islemlerimiz YENI bir sorun eklememeli.
        after = _hfs_problems(fsck, img)
        assert after <= baseline, (sorted(after - baseline), sorted(baseline))
    os.unlink(img)


def _hfs_problems(fsck: str, img: str) -> set:
    """fsck_hfs ciktisindaki sorun satirlari (asama basliklari haric)."""
    r = subprocess.run([fsck, "-n", "-f", img], capture_output=True, text=True)
    if r.returncode == 0:
        return set()
    out = set()
    for line in (r.stdout + r.stderr).splitlines():
        t = line.strip()
        if not t or t.startswith(("** Checking", "Executing", "The volume name",
                                  "** /", "** The volume")):
            continue
        out.add(t)
    return out or {"cikis kodu %d" % r.returncode}


TESTS = [test_xfs_layout_formula] + [_make_xfs_test(t, o) for t, o in XFS_CASES] + [
    test_xfs_grow_v4_refused, test_hfs_legacy_tonos_display,
    test_hfs_linux_written_greek_name]


def main() -> int:
    failed = skipped = 0
    for fn in TESTS:
        try:
            fn()
            print(f"TAMAM      {fn.__name__}")
        except Skip as exc:
            skipped += 1
            print(f"ATLANDI    {fn.__name__}: {exc}")
        except Exception:                               # noqa: BLE001
            failed += 1
            print(f"BASARISIZ  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(TESTS) - failed - skipped} tamam, {skipped} atlandi, {failed} basarisiz")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
