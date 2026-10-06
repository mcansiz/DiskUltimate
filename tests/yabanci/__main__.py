"""Yabanci girdi testleri komut satiri (ADR 0094). Ayrinti: __init__.py.

    python3 -m tests.yabanci [--sec ...] [--tur N] [--tohum S] [--butce-mb M]
                             [--calisma DIR] [--rapor DIR] [--cekirdek]
                             [--liste]

Her varyant icin adimlar ve denetimleri:

  mkfs        gercek arac; yoksa ya da reddederse varyant "atlandi"
  blkid       referans tur/surum/etiket (util-linux)
  doldur      cekirdekle (sudo + mount) ya da kullanici alani araciyla
              (mtools / debugfs) deterministik agac + parcalanma
  taban       dis fsck taban cizgisi: mkfs+doldurma sonrasi temiz olmayan
              birim icin sonraki fsck sonuclari "referans kabul etmiyor"
  tespit      fsdetect turu ve etiketi == blkid
  oku         kendi okuyucu: liste == manifest, her dosyanin ozeti
  yedek       tam + yalnizca kullanilan alan -> copla dolu hedefe geri yukle
              -> kendi okuyucu + dis fsck + cekirdek
  yaz         dizin, dosyalar, silme, yeniden adlandirma -> uc denetim
  boyut       buyut / kucult / saga tasi -> uc denetim (reddetmek serbest,
              bozmak degil)
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import unicodedata
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "src"))

from diskultimate.core import clone as clone_mod  # noqa: E402
from diskultimate.core.image import DiskImage  # noqa: E402
from diskultimate.core.mbr import MBRTable  # noqa: E402
from diskultimate.core.session import DiskSession  # noqa: E402

from ..long import verify  # noqa: E402
from ..long.data import Content, FileSpec, Manifest, content_sha1  # noqa: E402
from .variants import BLKID_TYPE, VARIANTS, Variant, expected_name, select  # noqa: E402

MIB = 1024 * 1024
PART_LBA = 2048
OK, SKIP, FAIL, REFUSED = "tamam", "atlandi", "BASARISIZ", "reddedildi"

# Ek fs anahtarlari (uzun testlerin tablolarinda yok)
verify.FSCK.setdefault("btrfs", ("btrfs", ["check", "--readonly"]))
verify.FSCK.setdefault("f2fs", ("fsck.f2fs", ["--dry-run"]))
verify.KERNEL_TYPE.setdefault("btrfs", "btrfs")
verify.KERNEL_TYPE.setdefault("f2fs", "f2fs")

MBR_TYPE = {"fat12": 0x01, "fat16": 0x06, "fat32": 0x0C, "exfat": 0x07,
            "ntfs": 0x07}


class Skip(Exception):
    pass


class Fail(Exception):
    pass


# --------------------------------------------------------------------------
# Veri kumesi
# --------------------------------------------------------------------------
NAMES = [
    "kisa.txt", "UPPER.TXT", "MiXeD.Txt", "noext", ".gizli",
    "bosluklu ad ve.cok.nokta.tar.gz", "ÇĞİÖŞÜ çğıöşü.dat",
    "Ελληνικά αρχεία.bin", "Русский файл.txt", "日本語のファイル.txt",
    "x" * 100 + ".bin", "tire-ve_alt~cizgi.log", "rakam 2026 (1).pdf",
]
DIRS = ["/belgeler", "/belgeler/alt dizin", "/çalışma/örnek", "/derin/a/b/c/d",
        "/Büyük Harf", "/" + "uzun_dizin_adi_" * 5]
EDGE_SIZES = [0, 1, 511, 512, 513, 1023, 1024, 4095, 4096, 4097, 65535, 65536,
              65537, MIB - 1, MIB, MIB + 1]


def make_dataset(seed: int, budget: int) -> Tuple[List[FileSpec], List[str], List[str]]:
    """(dosyalar, dizinler, sonradan silinecek yollar).

    Ayni tohum ayni agaci uretir. Butce dosyalarin toplam boyutudur.
    """
    rnd = random.Random(seed)
    files: List[FileSpec] = []
    used = [0]
    key = [seed * 100000]

    def add(path: str, size: int) -> bool:
        if used[0] + size > budget:
            return False
        key[0] += 1
        files.append(FileSpec(path, size, key[0]))
        used[0] += size
        return True

    # kokte az dosya: FAT12/16 kok dizini sinirli (-r 16 varyanti)
    add("/KOK.TXT", 1234)
    add("/kök ünicode.txt", 777)
    for i, size in enumerate(EDGE_SIZES):
        add(f"/belgeler/sinir_{i:02d}_{size}.bin", size)
    for i, name in enumerate(NAMES):
        add(f"{DIRS[i % len(DIRS)]}/{name}", rnd.randint(0, 200_000))
    # cok girisli dizin: birden cok kume / htree / B-agaci
    for i in range(300):
        add(f"/kalabalik/dosya_{i:04d}.dat", rnd.randint(0, 6000))
    # parcalanma: 24 dosya yaz, tekleri sil, sonra bosluklara buyuk dosya
    chunk = max(4096, min(2 * MIB, budget // 60))
    for i in range(24):
        add(f"/parca/p{i:02d}.bin", chunk + rnd.randint(0, 4096))
    deleted = [f"/parca/p{i:02d}.bin" for i in range(1, 24, 2)]
    late = [("/parca/buyuk_parcali.bin", chunk * 10 + 12345)]
    # orta ve buyuk dosyalar butceyi doldurur
    for i in range(12):
        add(f"/orta/o{i:02d}.bin", rnd.randint(64 * 1024, 4 * MIB))
    big = max(0, min(64 * MIB, (budget - used[0]) // 3))
    if big > MIB:
        add("/buyuk/buyuk_1.bin", big)
        add("/buyuk/buyuk_2.bin", big // 2 + 4097)
    for path, size in late:
        add(path, size)
    dirs = sorted({os.path.dirname(f.path) for f in files} - {"/"})
    return files, dirs, deleted


def write_spec(root: str, spec: FileSpec) -> None:
    """Dosyayi yazar; yarida kalirsa (ENOSPC, kok dizin dolu) yarim dosyayi
    siler — yoksa diskte manifestte olmayan 0 baytlik dosya kalir."""
    target = root + spec.path
    os.makedirs(os.path.dirname(target), exist_ok=True)
    src = Content(spec)
    try:
        with open(target, "wb") as fh:
            while True:
                piece = src.read(4 * MIB)
                if not piece:
                    break
                fh.write(piece)
    except OSError:
        try:
            os.remove(target)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------
# Yardimcilar
# --------------------------------------------------------------------------
def run(cmd: List[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace", **kw)


def tool(name: str) -> Optional[str]:
    return shutil.which(name) or shutil.which(name, path="/sbin:/usr/sbin")


def _udev_unescape(text: str) -> str:
    """blkid `-o udev` ID_FS_LABEL_ENC: \\xHH kacislari -> UTF-8 metin.

    `-o export` ASCII disi baytlari "M-CM-^G" biciminde verir (exfat-unicode
    etiketi boyle yanlis karsilastirildi); kodlu alan kayipsizdir.
    """
    raw = bytearray()
    i = 0
    while i < len(text):
        if text.startswith("\\x", i) and i + 4 <= len(text):
            raw.append(int(text[i + 2:i + 4], 16))
            i += 4
        else:
            raw += text[i].encode("utf-8")
            i += 1
    return raw.decode("utf-8", "replace")


def blkid_info(path: str) -> Dict[str, str]:
    exe = tool("blkid")
    if not exe:
        return {}
    r = run([exe, "-p", "-o", "udev", path])
    raw = {}
    for line in r.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            raw[k] = v
    out = {"TYPE": raw.get("ID_FS_TYPE", ""), "VERSION": raw.get("ID_FS_VERSION", "")}
    if "ID_FS_LABEL_ENC" in raw:
        out["LABEL"] = _udev_unescape(raw["ID_FS_LABEL_ENC"])
    return out if out["TYPE"] else {}


def sparse_copy(src: str, dst: str, offset: int) -> None:
    """Bolum dosyasini disk goruntusune `offset`ten kopyalar; sifir bloklari
    atlar (hedef seyrek kalir)."""
    zero = bytes(4 * MIB)
    with open(src, "rb") as fin, open(dst, "r+b") as fout:
        pos = 0
        while True:
            block = fin.read(4 * MIB)
            if not block:
                break
            if block != zero[:len(block)]:
                fout.seek(offset + pos)
                fout.write(block)
            pos += len(block)


def fill_garbage(path: str, size: int) -> None:
    chunk = os.urandom(4 * MIB)
    with open(path, "r+b") as fh:
        pos = 0
        while pos < size:
            n = min(len(chunk), size - pos)
            fh.write(chunk[:n])
            pos += n


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


# --------------------------------------------------------------------------
# Doldurma
# --------------------------------------------------------------------------
SPECIAL_FS = ("ext4", "xfs", "btrfs", "f2fs", "hfsplus", "ntfs3")


def _sha1_file(path: str) -> Tuple[int, str]:
    import hashlib
    h = hashlib.sha1()
    n = 0
    with open(path, "rb") as fh:
        while True:
            piece = fh.read(4 * MIB)
            if not piece:
                break
            h.update(piece)
            n += len(piece)
    return n, h.hexdigest()


def add_specials(mnt: str, v: Variant, kind: str, manifest: Manifest,
                 specials: Dict[str, str]) -> List[str]:
    """Denetimin hata buldugu nesne turleri (ADR 0094): sabit bag, symlink
    (59/60/61 karakter siniri), aygit dugumu, fifo, seyrek ve fallocate
    edilmis dosya, buyuk xattr, casefold dizini. Her biri ayri denenir;
    surucu desteklemiyorsa atlanir. Duzenli dosyalarin ozeti cekirdekten
    okunur (referans cekirdegin gordugudur)."""
    if kind not in SPECIAL_FS:
        return []
    base = mnt + "/ozel"
    os.makedirs(base, exist_ok=True)
    made: List[str] = []

    def regular(rel: str) -> None:
        manifest.entries[rel] = _sha1_file(mnt + rel)

    def attempt(name: str, fn) -> None:
        try:
            fn()
            made.append(name)
        except (OSError, subprocess.SubprocessError, NotImplementedError):
            pass

    # Her duzenli dosya OLUSTURULUR OLUSTURMAZ manifeste girer: sonraki adim
    # (os.link, setxattr) surucude desteklenmezse dosya yine diskte kalir ve
    # manifestte yoksa "listede fazla" diye yanlis hata verirdi (ext2 xattr).
    def hardlink():
        write_spec(mnt, FileSpec("/ozel/bag_a.bin", 70_000, 991))
        regular("/ozel/bag_a.bin")
        os.link(base + "/bag_a.bin", base + "/bag_b.bin")
        regular("/ozel/bag_b.bin")

    def symlinks():
        for n in (10, 59, 60, 61, 300):
            target = ("h" * (n - 4)) + ".txt"
            os.symlink(target, f"{base}/sembol_{n}")
            specials[f"/ozel/sembol_{n}"] = "symlink"

    def devices():
        for name, spec in (("aygit_c", "c 1 3"), ("aygit_b", "b 7 0")):
            r = run(["sudo", "-n", "mknod", f"{base}/{name}"] + spec.split())
            if r.returncode != 0:
                raise OSError(r.stderr)
            specials[f"/ozel/{name}"] = "device"
        os.mkfifo(base + "/boru")
        specials["/ozel/boru"] = "fifo"

    def sparse():
        try:
            with open(base + "/seyrek.bin", "wb") as fh:
                fh.seek(9 * MIB + 123)
                fh.write(b"son-blok" * 512)
                fh.seek(3 * MIB)
                fh.write(b"orta" * 1000)
        finally:
            if os.path.exists(base + "/seyrek.bin"):
                regular("/ozel/seyrek.bin")

    def fallocated():
        fd = os.open(base + "/ayrilmis.bin", os.O_CREAT | os.O_WRONLY, 0o644)
        try:
            os.posix_fallocate(fd, 0, 6 * MIB)
            os.pwrite(fd, b"basta-yazili" * 100, 0)
            os.pwrite(fd, b"ortada" * 100, 4 * MIB)
        finally:
            os.close(fd)
            regular("/ozel/ayrilmis.bin")

    def xattr():
        write_spec(mnt, FileSpec("/ozel/xattrli.bin", 5000, 993))
        regular("/ozel/xattrli.bin")
        os.setxattr(base + "/xattrli.bin", "user.deneme", b"x" * 3000)

    def casefold():
        if v.id != "ext4-casefold":
            raise OSError("casefold degil")
        os.makedirs(mnt + "/harf", exist_ok=True)
        r = run(["sudo", "-n", "chattr", "+F", mnt + "/harf"])
        if r.returncode != 0:
            raise OSError(r.stderr)
        for i in range(220):        # htree'ye gecsin
            spec = FileSpec(f"/harf/Dosya_{i:03d}_BuyukKucuk.txt", 300 + i, 5000 + i)
            write_spec(mnt, spec)
            manifest.put(spec)
        specials["/harf"] = "casefold-dir"

    for name, fn in (("sabit-bag", hardlink), ("symlink", symlinks),
                     ("aygit", devices), ("seyrek", sparse),
                     ("fallocate", fallocated), ("xattr", xattr),
                     ("casefold", casefold)):
        attempt(name, fn)
    return made


def populate_kernel(v: Variant, img: str, files, dirs, deleted,
                    manifest: Manifest, specials: Dict[str, str]) -> str:
    kind = verify.KERNEL_TYPE.get(v.fs)
    if not kind or not verify.sudo_ok():
        raise Skip("cekirdek ile doldurma yok (sudo/surucu)")
    mnt = tempfile.mkdtemp(prefix="du_yabanci_")
    opts = "loop,rw"
    if kind in ("vfat", "exfat", "ntfs3"):
        opts += f",uid={os.getuid()},gid={os.getgid()}"
    if kind == "vfat":
        opts += ",utf8"
    if kind == "hfsplus":
        opts += ",force"                 # gunluklu HFS+ yoksa salt okunur
    r = run(["sudo", "-n", "mount", "-t", kind, "-o", opts, img, mnt])
    if r.returncode != 0:
        os.rmdir(mnt)
        msg = (r.stdout + r.stderr).strip()
        if "unknown filesystem type" in msg:
            raise Skip(f"cekirdekte {kind} yok")
        raise Skip(f"mount -t {kind} rw basarisiz: {msg[-300:]}")
    written = failed = 0
    try:
        if kind not in ("vfat", "exfat", "ntfs3"):
            run(["sudo", "-n", "chown", f"{os.getuid()}:{os.getgid()}", mnt])
        late = {"/parca/buyuk_parcali.bin"}
        for spec in files:
            if spec.path in late:
                continue
            try:
                write_spec(mnt, spec)
                manifest.put(spec)
                written += 1
            except OSError:
                failed += 1               # ENOSPC, kok dizin dolu, ad gecersiz
        for path in deleted:
            try:
                os.remove(mnt + path)
                manifest.drop(path)
            except OSError:
                pass
        for spec in files:
            if spec.path in late:
                try:
                    write_spec(mnt, spec)
                    manifest.put(spec)
                    written += 1
                except OSError:
                    failed += 1
        made = add_specials(mnt, v, kind, manifest, specials)
        run(["sync"])
    finally:
        u = run(["sudo", "-n", "umount", mnt])
        os.rmdir(mnt)
        if u.returncode != 0:
            raise Fail(f"umount: {u.stderr[-200:]}")
    return (f"cekirdek {kind}: {written} dosya"
            + (f", {failed} yazilamadi" if failed else "")
            + (f"; ozel: {', '.join(made)}" if made else ""))


def _ascii(path: str) -> bool:
    return all(32 < ord(c) < 127 for c in path)


def _debugfs_listing(img: str, dirs) -> Dict[str, int]:
    """debugfs `ls -p`: yol -> boyut (yalnizca duzenli dosyalar)."""
    out: Dict[str, int] = {}
    for d in sorted(dirs):
        r = run([tool("debugfs"), "-R", f"ls -p {d}", img])
        for line in r.stdout.splitlines():
            parts = line.strip().strip("/").split("/")
            # /ino/mode/uid/gid/ad/boyut/
            if len(parts) >= 6 and parts[1].startswith("10"):
                name = "/".join(parts[4:-1])
                out[(d.rstrip("/") + "/" + name)] = int(parts[-1] or 0)
    return out


def populate_user(v: Variant, img: str, files, dirs, deleted,
                  manifest: Manifest, work: str) -> str:
    """Root olmadan: FAT -> mtools, ext -> debugfs. Yalnizca ASCII, bosluksuz
    adlar (araclarin alintilamasi platforma gore degisir)."""
    tmp = os.path.join(work, "yerel")
    os.makedirs(tmp, exist_ok=True)
    chosen = [f for f in files if _ascii(f.path) and f.size <= 8 * MIB]
    if v.fs.startswith("fat") and tool("mcopy"):
        env = dict(os.environ, MTOOLS_SKIP_CHECK="1", MTOOLS_NO_VFAT="0")
        made = set()
        n = 0
        for spec in chosen:
            d = os.path.dirname(spec.path)
            parts = [p for p in d.split("/") if p]
            for i in range(1, len(parts) + 1):
                sub = "/" + "/".join(parts[:i])
                if sub not in made:
                    run(["mmd", "-i", img, "::" + sub], env=env)
                    made.add(sub)
            local = os.path.join(tmp, "f")
            write_spec(tmp, FileSpec("/f", spec.size, spec.key))
            r = run(["mcopy", "-i", img, local, "::" + spec.path], env=env)
            if r.returncode == 0:
                manifest.put(spec)
                n += 1
        for path in deleted:
            if path in manifest.entries and \
                    run(["mdel", "-i", img, "::" + path], env=env).returncode == 0:
                manifest.drop(path)
        return f"mtools: {n} dosya"
    if v.fs.startswith("ext") and tool("debugfs"):
        cmds = []
        made = set()
        for spec in chosen:
            parts = [p for p in os.path.dirname(spec.path).split("/") if p]
            for i in range(1, len(parts) + 1):
                sub = "/" + "/".join(parts[:i])
                if sub not in made:
                    cmds.append(f"mkdir {sub}")
                    made.add(sub)
            local = os.path.join(tmp, f"k{spec.key}")
            write_spec(tmp, FileSpec(f"/k{spec.key}", spec.size, spec.key))
            cmds.append(f"write {local} {spec.path}")
        script = os.path.join(work, "debugfs.cmd")
        with open(script, "w") as fh:
            fh.write("\n".join(cmds) + "\n")
        r = run([tool("debugfs"), "-w", "-f", script, img])
        if r.returncode != 0:
            raise Skip(f"debugfs: {r.stderr[-200:]}")
        # debugfs inode/yer bitince "Could not allocate inode" yazar ama 0
        # doner (ext4-N64): diskte gercekten olan dosyalar okunarak secilir.
        present = _debugfs_listing(img, {os.path.dirname(f.path) or "/" for f in chosen})
        n = 0
        for spec in chosen:
            if present.get(spec.path) == spec.size:
                manifest.put(spec)
                n += 1
        shutil.rmtree(tmp, ignore_errors=True)
        return f"debugfs: {n}/{len(chosen)} dosya (silme/parcalanma yok)"
    raise Skip("root yok ve bu fs icin kullanici alani doldurma araci yok")


# --------------------------------------------------------------------------
# Denetimler
# --------------------------------------------------------------------------
class Checker:
    """Kendi okuyucu + dis fsck (taban cizgisine gore) + cekirdek."""

    def __init__(self, v: Variant, kernel: bool):
        self.v = v
        self.kernel = kernel
        self.fsck_baseline = None          # (durum, ayrinti)
        self.specials: Dict[str, str] = {}  # yol -> tur (duzenli dosya degil)

    def walk(self, access, path: str = "/") -> Dict[str, int]:
        out: Dict[str, int] = {}
        for node in access.listdir(path):
            if node.name in (".", ".."):
                continue
            if node.is_dir:
                out.update(self.walk(access, node.path))
            else:
                out[nfc(node.path)] = node.size
        return out

    def our_reader(self, disk: str, manifest: Manifest, exact: bool = True) -> str:
        s = DiskSession.open(disk, readonly=True)
        try:
            part = [p for p in s.partitions if p.start_lba == PART_LBA]
            if not part:
                raise Fail("bolum bulunamadi")
            access = s.filesystem(part[0].index)
            if access is None or not access.readable:
                raise Skip(f"okuyucu yok ({part[0].fs_type})")
            listed = self.walk(access)
            expected = {nfc(p): size for p, (size, _) in manifest.entries.items()}
            missing = sorted(set(expected) - set(listed))
            extra = sorted(set(listed) - set(expected))
            if exact and missing:
                raise Fail(f"listede yok ({len(missing)}): {missing[:5]}")
            # Kokteki sistem dosyalari (lost+found icerigi, kota) dosya degil;
            # bilinen fazlaliklar disinda fazla dosya bir okuma hatasidir.
            extra = [e for e in extra if not e.startswith(("/lost+found/",
                                                           "/System Volume"))
                     and e not in self.specials]
            if exact and extra:
                raise Fail(f"listede fazla ({len(extra)}): {extra[:5]}")
            wrong = [p for p in expected if p in listed and listed[p] != expected[p]]
            if wrong:
                raise Fail(f"listede boyut farkli: {wrong[:5]}")
            count, total, errors = verify.verify_manifest(access, manifest)
            if errors:
                raise Fail("icerik: " + "; ".join(errors[:5]))
            return f"{count} dosya, {total // MIB} MiB ozet tutuyor"
        finally:
            s.close()

    def fsck(self, disk: str, offset: int, size: int) -> Tuple[str, str]:
        status, detail = verify.external_fsck(self.v.fs, disk, offset, size)
        if self.fsck_baseline is None:
            self.fsck_baseline = (status, detail)
            return status, detail
        if status == "fail" and self.fsck_baseline[0] == "fail":
            return "skip", "referans fsck taban cizgisinde de temiz degildi"
        return status, detail

    def kernel_check(self, disk: str, offset: int, size: int,
                     manifest: Manifest) -> Tuple[str, str]:
        if not self.kernel:
            return "skip", "--cekirdek verilmedi"
        return verify.kernel_mount_check(self.v.fs, disk, offset, size, manifest)

    def all(self, disk: str, manifest: Manifest, what: str) -> str:
        s = DiskSession.open(disk, readonly=True)
        try:
            part = [p for p in s.partitions if p.start_lba == PART_LBA][0]
            offset, size = part.start_lba * 512, part.sector_count * 512
        finally:
            s.close()
        notes = []
        try:
            notes.append("okuyucu: " + self.our_reader(disk, manifest))
        except Skip as exc:
            notes.append(f"okuyucu atlandi: {exc}")
        st, det = self.fsck(disk, offset, size)
        if st == "fail":
            raise Fail(f"{what}: dis fsck: {det[-600:]}")
        notes.append(f"fsck={st}")
        st, det = self.kernel_check(disk, offset, size, manifest)
        if st == "fail":
            raise Fail(f"{what}: cekirdek: {det[-600:]}")
        notes.append(f"cekirdek={st}" + ("" if st == "ok" else f" ({det[:80]})"))
        return "; ".join(notes)


# --------------------------------------------------------------------------
# Senaryo
# --------------------------------------------------------------------------
def run_variant(v: Variant, seed: int, budget_mb: int, workroot: str,
                kernel: bool) -> dict:
    rec = {"id": v.id, "fs": v.fs, "not": v.note, "tohum": seed, "adimlar": []}
    work = os.path.join(workroot, v.id)
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    part_img = os.path.join(work, "bolum.img")
    disk = os.path.join(work, "disk.img")
    manifest = Manifest()
    checker = Checker(v, kernel)
    state = {}

    def step(name, fn):
        t = time.time()
        entry = {"ad": name}
        try:
            note = fn()
            entry["durum"] = OK
            if note:
                entry["not"] = note
        except Skip as exc:
            entry["durum"] = SKIP
            entry["not"] = str(exc)
        except Fail as exc:
            entry["durum"] = FAIL
            entry["not"] = str(exc)
        except Exception as exc:                          # noqa: BLE001
            entry["durum"] = FAIL
            entry["not"] = f"{type(exc).__name__}: {exc}"
            entry["iz"] = traceback.format_exc()[-2000:]
        entry["sn"] = round(time.time() - t, 1)
        rec["adimlar"].append(entry)
        mark = {OK: "  ", SKIP: "--", FAIL: "!!", REFUSED: "~~"}.get(entry["durum"], "  ")
        print(f"  {mark} {v.id:<22} {name:<9} {entry['durum']:<10} "
              f"{entry['sn']:>6.1f} sn  {entry.get('not', '')[:150]}", flush=True)
        return entry["durum"]

    # --- mkfs -------------------------------------------------------------
    def mkfs():
        exe = tool(v.cmd[0])
        if not exe:
            raise Skip(f"{v.cmd[0]} kurulu degil")
        with open(part_img, "wb") as fh:
            fh.truncate(v.mb * MIB)
        cmd = [exe] + [a.replace("{img}", part_img).replace("{label}", v.label)
                       for a in v.cmd[1:]]
        r = run(cmd)
        if r.returncode != 0:
            raise Skip(f"arac reddetti ({v.needs or 'secenek'}): "
                       f"{(r.stdout + r.stderr).strip()[-300:]}")
        for post in v.post:
            run([a.replace("{img}", part_img) for a in post])
        return " ".join(os.path.basename(a) for a in cmd[:8])

    if step("mkfs", mkfs) != OK:
        rec["sonuc"] = SKIP
        shutil.rmtree(work, ignore_errors=True)
        return rec

    def blkid():
        info = blkid_info(part_img)
        if not info:
            raise Skip("blkid yok")
        state["blkid"] = info
        want = BLKID_TYPE.get(v.fs)
        if info.get("TYPE") != want:
            raise Fail(f"blkid TYPE={info.get('TYPE')} bekleniyordu {want} "
                       "(varyant tanimi hatali)")
        return f"TYPE={info.get('TYPE')} VERSION={info.get('VERSION', '')} " \
               f"LABEL={info.get('LABEL', '')}"

    step("blkid", blkid)

    budget = min(budget_mb * MIB, v.mb * MIB * 45 // 100)
    files, dirs, deleted = make_dataset(seed, budget)

    def populate():
        try:
            return populate_kernel(v, part_img, files, dirs, deleted, manifest,
                                   checker.specials)
        except Skip as first:
            try:
                return populate_user(v, part_img, files, dirs, deleted, manifest, work)
            except Skip as second:
                raise Skip(f"{first}; {second}")

    step("doldur", populate)

    # --- disk goruntusu: MBR + bolum 2048'de + buyutme/tasima payi ----------
    room = max(v.mb * MIB // 2, 64 * MIB)
    disk_size = PART_LBA * 512 + v.mb * MIB + room
    d = DiskImage.create(disk, disk_size, overwrite=True)
    MBRTable.create(d).add_partition(PART_LBA, v.mb * MIB // 512,
                                     type_id=MBR_TYPE.get(v.fs, 0x83))
    d.close()
    sparse_copy(part_img, disk, PART_LBA * 512)
    os.unlink(part_img)
    offset, size = PART_LBA * 512, v.mb * MIB

    def baseline():
        st, det = checker.fsck(disk, offset, size)
        if st == "fail":
            return f"REFERANS fsck bu birimi kabul etmiyor: {det[-200:]}"
        return f"fsck={st}: {det[-120:]}"

    step("taban", baseline)

    def detect():
        s = DiskSession.open(disk, readonly=True)
        try:
            part = [p for p in s.partitions if p.start_lba == PART_LBA]
            if not part:
                raise Fail("bolum yok")
            got, label = part[0].fs_type, part[0].fs_label
        finally:
            s.close()
        info = state.get("blkid") or {}
        want = expected_name(info.get("TYPE", ""), info.get("VERSION", "")) \
            if info else None
        if want and got != want:
            raise Fail(f"tur {got!r}, blkid {want!r} diyor")
        want_label = info.get("LABEL", "")
        if info and nfc(label.strip()) != nfc(want_label.strip()):
            raise Fail(f"etiket {label!r}, blkid {want_label!r} diyor")
        return f"{got} / {label!r}"

    step("tespit", detect)

    def read():
        return checker.our_reader(disk, manifest)

    readable = step("oku", read)

    def backup():
        notes = []
        for used_only in (False, True):
            kip = "kullanilan" if used_only else "tam"
            dub = os.path.join(work, "y.dub")
            dest = os.path.join(work, "geri.img")
            try:
                s = DiskSession.open(disk, readonly=True)
                try:
                    info = s.backup_disk(dub, used_only=used_only)
                finally:
                    s.close()
                h = DiskImage.create(dest, disk_size, overwrite=True)
                h.close()
                fill_garbage(dest, disk_size)
                h = DiskImage(dest)
                try:
                    clone_mod.restore(dub, h)
                finally:
                    h.close()
                note = checker.all(dest, manifest, f"{kip} yedek")
                notes.append(f"{kip} ({os.path.getsize(dub) // MIB} MiB, "
                             f"kapsam={'kullanilan' if info.used_only else 'tum'}): {note}")
            finally:
                for p in (dub, dest):
                    if os.path.exists(p):
                        os.unlink(p)
        return " | ".join(notes)

    step("yedek", backup)

    def write():
        s = DiskSession.open(disk)
        try:
            part = [p for p in s.partitions if p.start_lba == PART_LBA][0]
            access = s.filesystem(part.index)
            if access is None or not access.writable:
                reason = access.write_reason if access is not None else "erisim yok"
                raise Skip(f"yazma desteklenmiyor: {reason}")
            rnd = random.Random(seed + 7)
            done = []
            try:
                access.mkdir("/yeni_dizin")
                for i, size in enumerate((0, 1, 4096, 300_000, 3 * MIB + 17)):
                    spec = FileSpec(f"/yeni_dizin/yeni_{i}.bin", size, seed * 7 + i)
                    access.write_stream(spec.path, Content(spec), spec.size)
                    manifest.put(spec, content_sha1(spec))
                    done.append(spec.path)
                gone = [p for p in sorted(manifest.entries) if p.startswith("/kalabalik/")][:5]
                for p in gone:
                    access.remove(p)
                    manifest.drop(p)
                movable = [p for p in sorted(manifest.entries) if p.startswith("/orta/")]
                if movable:
                    old = movable[0]
                    new = os.path.dirname(old) + "/yeniden_adlandirildi.bin"
                    access.rename(old, os.path.basename(new))
                    manifest.entries[new] = manifest.entries.pop(old)
                spec = FileSpec("/yeni_dizin/sonradan.dat", rnd.randint(1, 50_000), seed * 11)
                access.write_stream(spec.path, Content(spec), spec.size)
                manifest.put(spec, content_sha1(spec))
                # Ozel nesneler (denetim E2/E3/E7/E13): sabit bagin bir adi,
                # aygit/fifo, 59/60 karakterlik symlink, xattr'li dosya silinir;
                # kalan bag okunabilmeli, fsck sizinti gormemeli.
                sp = checker.specials
                for path in ("/ozel/bag_b.bin", "/ozel/xattrli.bin"):
                    if path in manifest.entries:
                        access.remove(path)
                        manifest.drop(path)
                for path in ("/ozel/aygit_c", "/ozel/boru", "/ozel/sembol_59",
                             "/ozel/sembol_60"):
                    if path in sp:
                        access.remove(path)
                        sp.pop(path)
                if "/harf" in sp:              # casefold htree dizinine yaz (E5)
                    for i in range(3):
                        spec = FileSpec(f"/harf/YeniKarisik_{i}.TXT", 777 + i, seed * 13 + i)
                        access.write_stream(spec.path, Content(spec), spec.size)
                        manifest.put(spec, content_sha1(spec))
                access.flush()
            except Exception as exc:                      # noqa: BLE001
                # Yazicinin reddetmesi serbest; ama birim saglam kalmali.
                state["write_error"] = f"{type(exc).__name__}: {exc}"
            s.close_filesystems()
        finally:
            s.close()
        note = checker.all(disk, manifest, "yazma sonrasi")
        if "write_error" in state:
            raise Skip(f"yazici reddetti/yarida kaldi ({state['write_error'][:150]}); "
                       f"birim saglam: {note}")
        return f"{len(done)} dosya + silme + ad degistirme; {note}"

    step("yaz", write)

    def resize():
        box = [DiskSession.open(disk)]
        results = []

        def reopen():
            box[0] = DiskSession.open(disk)
            return box[0]

        def close():
            if box[0] is not None:
                box[0].close()
                box[0] = None

        s = box[0]
        try:
            info = s.resize_info(1)
            if not info.resizable or info.kind in ("raw", "native"):
                raise Skip(f"boyutlandirma yok ({info.kind}: {info.note})")
            part = s.table.get(1)
            d_sectors = disk_size // 512
            plans = []
            grow = min(part.sector_count + room // 512 // 2,
                       info.max_sectors or 1 << 62)
            if grow > part.sector_count:
                plans.append(("buyut", PART_LBA, grow))
            plans.append(("kucult", PART_LBA, None))
            if info.movable:
                plans.append(("saga_tasi", PART_LBA + 16 * 2048, None))
            for name, start, count in plans:
                part = s.table.get(1)
                if count is None:
                    if name == "kucult":
                        target = max(info.min_sectors + 8 * 2048,
                                     part.sector_count * 85 // 100)
                        count = (target + 2047) // 2048 * 2048
                        if count >= part.sector_count:
                            results.append("kucult: yer yok")
                            continue
                    else:
                        count = part.sector_count
                if start + count > d_sectors:
                    results.append(f"{name}: disk yetmez")
                    continue
                try:
                    s.resize_partition(1, start, count, confirm=True)
                    results.append(f"{name}={count // 2048} MiB")
                except Exception as exc:                  # noqa: BLE001
                    results.append(f"{name} reddedildi: {str(exc)[:80]}")
                close()
                # her islemden sonra uc denetim; bolum yeni yerinde
                note = _check_moved(checker, disk, manifest, name)
                results.append(note)
                s = reopen()
                info = s.resize_info(1)
        finally:
            close()
        return " | ".join(results)

    step("boyut", resize)
    rec["sonuc"] = FAIL if any(a["durum"] == FAIL for a in rec["adimlar"]) else OK
    shutil.rmtree(work, ignore_errors=True)
    return rec


def _check_moved(checker: Checker, disk: str, manifest: Manifest, what: str) -> str:
    """Boyutlandirma/tasima sonrasi denetim: bolum artik baska LBA'da olabilir."""
    s = DiskSession.open(disk, readonly=True)
    try:
        part = s.table.get(1)
        offset, size = part.start_lba * 512, part.sector_count * 512
        access = s.filesystem(1)
        if access is not None and access.readable:
            count, _, errors = verify.verify_manifest(access, manifest)
            if errors:
                raise Fail(f"{what}: okuyucu: " + "; ".join(errors[:5]))
    finally:
        s.close()
    st, det = checker.fsck(disk, offset, size)
    if st == "fail":
        raise Fail(f"{what}: dis fsck: {det[-600:]}")
    kst, kdet = checker.kernel_check(disk, offset, size, manifest)
    if kst == "fail":
        raise Fail(f"{what}: cekirdek: {kdet[-600:]}")
    return f"{what} denetim: fsck={st} cekirdek={kst}"


# --------------------------------------------------------------------------
def summary_md(records: List[dict]) -> str:
    steps = ["mkfs", "blkid", "doldur", "taban", "tespit", "oku", "yedek", "yaz", "boyut"]
    sym = {OK: "✅", SKIP: "➖", FAIL: "❌", REFUSED: "↩️"}
    lines = ["| varyant | tohum | " + " | ".join(steps) + " |",
             "|---|---|" + "---|" * len(steps)]
    for r in records:
        by = {a["ad"]: a["durum"] for a in r["adimlar"]}
        lines.append(f"| {r['id']} | {r['tohum']} | " +
                     " | ".join(sym.get(by.get(s, ""), " ") for s in steps) + " |")
    fails = [(r, a) for r in records for a in r["adimlar"] if a["durum"] == FAIL]
    if fails:
        lines += ["", "### Hatalar", ""]
        for r, a in fails:
            lines.append(f"- **{r['id']}** (tohum {r['tohum']}) `{a['ad']}`: "
                         f"{a.get('not', '')[:600]}")
    skips = [(r, a) for r in records for a in r["adimlar"]
             if a["durum"] == SKIP and a["ad"] in ("mkfs", "doldur", "oku", "yaz")]
    if skips:
        lines += ["", "<details><summary>Atlananlar</summary>", ""]
        for r, a in skips:
            lines.append(f"- {r['id']} `{a['ad']}`: {a.get('not', '')[:200]}")
        lines += ["", "</details>"]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m tests.yabanci")
    ap.add_argument("--sec", default="all", help="varyant id/onek/fs, virgulle")
    ap.add_argument("--tur", type=int, default=1, help="her varyant kac kez (farkli tohum)")
    ap.add_argument("--tohum", type=int, default=0, help="0 = zamandan")
    ap.add_argument("--butce-mb", type=int, default=192, help="varyant basina veri ust siniri")
    ap.add_argument("--calisma", default="", help="goruntu klasoru")
    ap.add_argument("--rapor", default="", help="JSON + markdown rapor klasoru")
    ap.add_argument("--cekirdek", action="store_true",
                    help="cekirdekle doldur ve dogrula (sudo; yalnizca CI/VM)")
    ap.add_argument("--liste", action="store_true")
    args = ap.parse_args()

    chosen = select(args.sec)
    if args.liste:
        for v in chosen:
            print(f"{v.id:<24} {v.fs:<8} {v.mb:>5} MiB  {v.note}")
        return 0
    if args.cekirdek:
        os.environ["DU_UZUN_CEKIRDEK"] = "1"
    base = args.tohum or int(time.time()) % 100000
    from diskultimate.paths import scratch
    workroot = args.calisma or scratch("yabanci")
    os.makedirs(workroot, exist_ok=True)
    print(f"{len(chosen)} varyant x {args.tur} tur, tohum {base}, calisma {workroot}, "
          f"cekirdek={'evet' if args.cekirdek else 'hayir'}", flush=True)
    records = []
    t0 = time.time()
    for r in range(args.tur):
        for i, v in enumerate(chosen):
            seed = base + r * 1000 + i
            records.append(run_variant(v, seed, args.butce_mb, workroot, args.cekirdek))
    md = summary_md(records)
    fails = sum(1 for r in records if r.get("sonuc") == FAIL)
    print("\n" + md)
    print(f"{len(records)} senaryo, {fails} hatali, {time.time() - t0:.0f} sn")
    if args.rapor:
        os.makedirs(args.rapor, exist_ok=True)
        with open(os.path.join(args.rapor, "yabanci.json"), "w", encoding="utf-8") as fh:
            json.dump(records, fh, ensure_ascii=False, indent=1)
        with open(os.path.join(args.rapor, "yabanci.md"), "w", encoding="utf-8") as fh:
            fh.write(md)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
