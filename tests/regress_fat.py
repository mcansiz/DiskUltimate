"""FAT / exFAT yabanci birim regresyonlari (uyumluluk denetimi, ADR 0094).

    python3 -m tests.regress_fat

Girdiler gercek araclarla uretilir (mkfs.fat, mkfs.exfat); araclarin
uretemedigi yapilar (NoFatChain dizin, VDL < DataLength, OS/2 EA sozcugu,
ExtFlags aynalama kapali, bozuk FAT) dogrudan yazilir. Her yazmadan sonra
dis fsck (fsck.vfat -n / fsck.exfat -n) 0 dondurmeli. Arac yoksa test
ATLANDI olur, sessizce gecmez.

Kapsanan bulgular: F0, X1, X2, X3, X4, F5, F6, F7, F8, F9, X10, X11, FAT
tutarlilik kapisi (picozed) ve BPB sektor boyutu != aygit sektoru iken
FAT/exFAT boyutlandirma.
"""
from __future__ import annotations

import io
import os
import shutil
import struct
import subprocess
import sys
import traceback
from typing import Callable, List

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from diskultimate.core import recovery, resize, usedmap  # noqa: E402
from diskultimate.core.exfat import (E_FILE, E_STREAM, EOC, ExFatError,  # noqa: E402
                                     ExFatFS, entry_set_checksum)
from diskultimate.core.fat import FatError, FatFS  # noqa: E402
from diskultimate.core.filesystem import ExFatAccess, FatAccess  # noqa: E402
from diskultimate.core.fsdetect import detect  # noqa: E402
from diskultimate.core.image import DiskImage  # noqa: E402
from diskultimate.paths import scratch  # noqa: E402

MIB = 1024 * 1024
WORK = scratch("regress_fat")
TESTS: List[Callable[[], None]] = []


class Atlandi(Exception):
    pass


def test(fn):
    TESTS.append(fn)
    return fn


# --------------------------------------------------------------------------
# Yardimcilar
# --------------------------------------------------------------------------
def need(tool: str) -> str:
    yol = shutil.which(tool) or (os.path.exists("/usr/sbin/" + tool) and "/usr/sbin/" + tool)
    if not yol:
        raise Atlandi(f"{tool} kurulu degil")
    return yol


def run(cmd: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          universal_newlines=True)


def blank(name: str, size: int) -> str:
    yol = os.path.join(WORK, name)
    if os.path.exists(yol):
        os.unlink(yol)
    with open(yol, "wb") as fh:
        fh.truncate(size)
    return yol


def mkfat(name: str, size_mb: float, *args: str) -> str:
    tool = need("mkfs.fat")
    yol = blank(name, int(size_mb * MIB))
    r = run([tool, "-I", "--mbr=no"] + list(args) + [yol])
    if r.returncode != 0:
        raise Atlandi("mkfs.fat reddetti: " + r.stdout.strip()[-200:])
    return yol


def mkexfat(name: str, size_mb: float, *args: str) -> str:
    tool = need("mkfs.exfat")
    yol = blank(name, int(size_mb * MIB))
    r = run([tool] + list(args) + [yol])
    if r.returncode != 0:
        raise Atlandi("mkfs.exfat reddetti: " + r.stdout.strip()[-200:])
    return yol


def fsck_fat(yol: str, expect_ok: bool = True) -> str:
    r = run([need("fsck.vfat"), "-n", yol])
    if expect_ok and r.returncode != 0:
        raise AssertionError("fsck.vfat -n %s (rc=%d):\n%s"
                             % (os.path.basename(yol), r.returncode, r.stdout))
    return r.stdout


def fsck_exfat(yol: str) -> None:
    r = run([need("fsck.exfat"), "-n", yol])
    if r.returncode != 0:
        raise AssertionError("fsck.exfat -n %s (rc=%d):\n%s"
                             % (os.path.basename(yol), r.returncode, r.stdout))


def raises(exc, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except exc:
        return
    raise AssertionError("%s beklenirdi: %s(%s)" % (exc.__name__, getattr(fn, "__name__", fn),
                                                ", ".join(repr(x)[:60] for x in a)))


def payload(n: int, seed: int = 1) -> bytes:
    import random
    return random.Random(seed).randbytes(n) if hasattr(random.Random, "randbytes") \
        else bytes(random.Random(seed).getrandbits(8) for _ in range(n))


class ShortSource(io.RawIOBase):
    """Bildirilen boyuttan kisa kaynak: yazma ortasinda hata uretir."""

    def __init__(self, n: int):
        self.left = n

    def read(self, n=-1):
        k = min(self.left, 4096 if n < 0 else n)
        self.left -= k
        return b"\xAB" * k


# --------------------------------------------------------------------------
# F0: hata yollarinda kume sizintisi
# --------------------------------------------------------------------------
@test
def t01_f0_fat_hata_yollari():
    """FAT: olmayan klasor / gecersiz ad / kisa kaynak / dolu kok -> kume sizmaz"""
    for ft in ("32", "16"):
        yol = mkfat(f"f0_{ft}.img", 64, "-F", ft)
        d = DiskImage(yol)
        fs = FatFS(d)
        bos0 = fs.free_clusters()
        raises(FatError, fs.write_file, "/yok/a.bin", b"x" * 300000)
        raises(FatError, fs.mkdir, "/yok/alt")
        raises(FatError, fs.write_file, "/a:b.txt", b"x" * 300000)
        raises(FatError, fs.write_file, "/" + "u" * 256, b"x")
        raises(Exception, fs.write_stream, "/kisa.bin", ShortSource(1000), 300000)
        raises(FatError, fs.write_file, "/dev.bin", b"\0" * (bos0 + 1) * fs.cluster_bytes)
        assert fs.free_clusters() == bos0, (fs.free_clusters(), bos0)
        veri = payload(300000)
        fs.write_file("/ok.bin", veri)
        d.close()
        out = fsck_fat(yol)
        assert "Reclaimed" not in out, out
        d = DiskImage(yol)
        fs = FatFS(d)
        assert fs.read_file("/ok.bin") == veri
        assert fs.free_clusters() == bos0 - (300000 + fs.cluster_bytes - 1) // fs.cluster_bytes
        d.close()

    # FAT16 sabit kok dizin dolu: buyuk dosya kume ayirmadan reddedilmeli
    yol = mkfat("f0_kok.img", 32, "-F", "16", "-r", "16")
    d = DiskImage(yol)
    fs = FatFS(d)
    fs.mkdir("/D")
    for i in range(4096):                     # kok dizin dolana kadar
        try:
            fs.write_file(f"/F{i}.TXT", b"")
        except FatError:
            break
    else:
        raise AssertionError("kok dizin dolmadi")
    bos0 = fs.free_clusters()
    raises(FatError, fs.write_file, "/BIG.BIN", b"y" * 300000)
    raises(FatError, fs.mkdir, "/E")
    assert fs.free_clusters() == bos0
    fs.write_file("/D/X.BIN", b"z" * 5000)
    d.close()
    fsck_fat(yol)


@test
def t02_f0_exfat_hata_yollari():
    """exFAT: olmayan klasor / gecersiz ad / kisa kaynak / yer yok -> bitmap sizmaz"""
    yol = mkexfat("f0_ex.img", 64)
    d = DiskImage(yol)
    fs = ExFatFS(d)
    bos0 = fs.free_cluster_count()
    raises(ExFatError, fs.write_file, "/yok/a.bin", b"x" * 300000)
    raises(ExFatError, fs.mkdir, "/yok/alt")
    raises(ExFatError, fs.write_file, "/a:b.txt", b"x" * 300000)
    raises(ExFatError, fs.write_file, "/" + "u" * 256, b"x")
    raises(Exception, fs.write_stream, "/kisa.bin", ShortSource(1000), 300000)
    raises(ExFatError, fs.write_file, "/dev.bin",
           b"\0" * (bos0 + 1) * fs.cluster_bytes)
    assert fs.free_cluster_count() == bos0
    veri = payload(300000)
    fs.write_file("/ok.bin", veri)
    d.close()
    fsck_exfat(yol)
    d = DiskImage(yol)
    fs = ExFatFS(d)
    assert fs.read_file("/ok.bin") == veri
    assert fs.free_cluster_count() == bos0 - (300000 + fs.cluster_bytes - 1) // fs.cluster_bytes
    d.close()


# --------------------------------------------------------------------------
# X1: NoFatChain dizinleri
# --------------------------------------------------------------------------
def _craft_nfc_dir(fs: ExFatFS, name: str, n: int, files: int,
                   block_next: bool) -> int:
    """Linux exfat_alloc_new_dir / Windows gibi bitisik (NoFatChain) dizin.

    Dizin kumelerinin FAT girisleri bayat birakilir (ilkinde EOC, digerinde
    cop): FAT'ten okuyan bir surucu yalnizca ilk kumeyi gorur.
    """
    cb = fs.cluster_bytes
    bas = fs._find_free_run(n + 1)
    for c in range(bas, bas + n):
        fs._set_used(c, True)
    fs.set_fat(bas, EOC)
    fs.set_fat(bas + 1, 0x12345678)
    veri = bytearray(n * cb)
    pos = 0
    for i in range(files):
        blob = fs._build_entry_set(f"dosya{i:03d}.txt", 0x20, 0, 0, False)
        veri[pos:pos + len(blob)] = blob
        pos += len(blob)
    assert pos <= len(veri)
    d = fs.dev
    d.write(fs.cluster_offset(bas), bytes(veri))
    fs._insert_entry("/", fs._build_entry_set(name, 0x10, bas, n * cb, True))
    if block_next:
        # hemen ardindaki kume dolu: buyume FAT zincirine cevirmeli
        fs._set_used(bas + n, True)
        d.write(fs.cluster_offset(bas + n), b"B" * cb)
        fs._insert_entry("/", fs._build_entry_set(name + "_tikac", 0x20, bas + n,
                                                  cb, True))
    fs.flush()
    return bas


@test
def t03_x1_nofatchain_dizin():
    """exFAT NoFatChain dizin: 127 dosya okunur; buyutme bitisik kalir ya da FAT zincirine cevrilir"""
    yol = mkexfat("x1.img", 64, "-c", "4K")
    d = DiskImage(yol)
    fs = ExFatFS(d)
    bos0 = fs.free_cluster_count()
    cb = fs.cluster_bytes
    a = _craft_nfc_dir(fs, "ncdir", 3, 127, block_next=True)
    b = _craft_nfc_dir(fs, "ncdir2", 3, 127, block_next=False)
    d.close()
    fsck_exfat(yol)                       # uretilen yapi gecerli mi (kahin)

    d = DiskImage(yol)
    fs = ExFatFS(d)
    for p in ("/ncdir", "/ncdir2"):
        assert len(fs.listdir(p)) == 127, (p, len(fs.listdir(p)))
        assert fs.exists(p + "/dosya126.txt")
    veri = {}
    # ncdir2'ye bos dosyalar: veri kumesi ayrilmaz, ardindaki kume bos kalir
    # ve dizin bitisik buyur. ncdir'de ardindaki kume dolu: FAT'e cevrilir.
    for p, boyut in (("/ncdir2", 0), ("/ncdir", 3000)):
        for i in range(2):
            veri[f"{p}/yeni{i}.bin"] = payload(boyut + i * bool(boyut), seed=i + len(p))
            fs.write_file(f"{p}/yeni{i}.bin", veri[f"{p}/yeni{i}.bin"])
    e1, e2 = fs.find("/ncdir"), fs.find("/ncdir2")
    assert e1.cluster == a and not e1.contiguous and e1.size == 4 * cb, e1
    assert fs.chain(a) == [a, a + 1, a + 2] + fs.chain(a)[3:] and len(fs.chain(a)) == 4
    assert e2.cluster == b and e2.contiguous and e2.size == 4 * cb, e2
    for p in ("/ncdir", "/ncdir2"):
        assert len(fs.listdir(p)) == 129
    for k, v in veri.items():
        assert fs.read_file(k) == v, k
    fs.rename("/ncdir/dosya005.txt", "adi_degisti.txt")
    fs.remove("/ncdir2/dosya010.txt")
    silinen = [x for x in recovery.scan_deleted(fs) if x.name == "dosya010.txt"]
    assert silinen, "NoFatChain dizindeki silinmis giris taranmadi"
    d.close()
    fsck_exfat(yol)

    d = DiskImage(yol)
    fs = ExFatFS(d)
    assert fs.exists("/ncdir/adi_degisti.txt")
    fs.remove("/ncdir", recursive=True)
    fs.remove("/ncdir2", recursive=True)
    fs.remove("/ncdir_tikac")
    assert fs.free_cluster_count() == bos0, (fs.free_cluster_count(), bos0)
    d.close()
    fsck_exfat(yol)


# --------------------------------------------------------------------------
# X2 / X10: UTF-16 ad uzunlugu ve upcase karsilastirmasi
# --------------------------------------------------------------------------
@test
def t04_x2_emoji_adlar():
    """exFAT NameLength UTF-16 birimi: emoji adlar kesilmez, sonda NUL kalmaz"""
    yol = mkexfat("x2.img", 64)
    d = DiskImage(yol)
    fs = ExFatFS(d)
    adlar = ["\U0001F600.txt", "a\U0001F600b", "\U0001F600" * 8 + ".bin",
             "abcdefghijklmn\U0001F600", "x" * 253 + "\U0001F600"[:1]]
    for i, ad in enumerate(adlar):
        fs.write_file("/" + ad, payload(100 + i, seed=i))
    d.close()
    fsck_exfat(yol)
    d = DiskImage(yol)
    fs = ExFatFS(d)
    bulunan = {e.name for e in fs.listdir("/")}
    assert bulunan == set(adlar), bulunan ^ set(adlar)
    raw = fs._read_dir_path("/")
    for e in fs.listdir("/"):
        units = len(e.name.encode("utf-16-le")) // 2
        assert raw[e.slot_offset + 32 + 3] == units, (e.name, raw[e.slot_offset + 35])
        assert fs.read_file(e.path) == payload(100 + adlar.index(e.name),
                                               seed=adlar.index(e.name))
    raises(ExFatError, fs.write_file, "/" + "x" * 254 + "\U0001F600", b"")
    fs.rename("/\U0001F600.txt", "\U0001F680\U0001F680.txt")
    d.close()
    fsck_exfat(yol)
    d = DiskImage(yol)
    assert ExFatFS(d).exists("/\U0001F680\U0001F680.txt")
    d.close()


@test
def t05_x10_upcase_esleme():
    """exFAT ad esleme birimin upcase tablosuyla (Kelvin isareti 'k' degildir)"""
    yol = mkexfat("x10.img", 64)
    d = DiskImage(yol)
    fs = ExFatFS(d)
    fs.write_file("/k.txt", b"kucuk")
    assert not fs.exists("/K.txt"), "str.lower eslemesi: Kelvin = k sanildi"
    fs.write_file("/K.txt", b"kelvin")
    fs.write_file("/ÄBC.txt", b"umlaut")
    assert fs.read_file("/äbc.TXT") == b"umlaut"
    assert fs.read_file("/K.TXT") == b"kucuk"
    assert fs.read_file("/K.txt") == b"kelvin"
    d.close()
    fsck_exfat(yol)


# --------------------------------------------------------------------------
# X3: ValidDataLength
# --------------------------------------------------------------------------
def _patch_stream(fs: ExFatFS, path: str, **alanlar) -> None:
    e = fs.find(path)
    veri = fs._read_dir_path("/")
    s = e.slot_offset + 32
    assert veri[s] == E_STREAM
    if "vdl" in alanlar:
        struct.pack_into("<Q", veri, s + 8, alanlar["vdl"])
    son = e.slot_offset + e.slot_count * 32
    struct.pack_into("<H", veri, e.slot_offset + 2,
                     entry_set_checksum(bytes(veri[e.slot_offset:son])))
    fs._write_dir(fs.root_cluster, veri)


@test
def t06_x3_valid_data_length():
    """exFAT VDL < DataLength: VDL otesi sifir okunur; yeniden adlandirma VDL'yi korur"""
    yol = mkexfat("x3.img", 64, "-c", "4K")
    d = DiskImage(yol)
    fs = ExFatFS(d)
    veri = payload(3 * 4096, seed=7)
    fs.write_file("/v.bin", veri)
    _patch_stream(fs, "/v.bin", vdl=5000)
    d.close()
    fsck_exfat(yol)
    beklenen = veri[:5000] + bytes(len(veri) - 5000)
    d = DiskImage(yol)
    fs = ExFatFS(d)
    assert fs.read_file("/v.bin") == beklenen
    assert b"".join(fs.iter_file("/v.bin", chunk=4096)) == beklenen
    hedef = os.path.join(WORK, "x3_out.bin")
    fs.extract("/v.bin", hedef)
    assert open(hedef, "rb").read() == beklenen
    fs.rename("/v.bin", "w.bin")
    e = fs.find("/w.bin")
    assert e.valid_size == 5000 and e.size == len(veri), (e.valid_size, e.size)
    assert fs.read_file("/w.bin") == beklenen
    d.close()
    fsck_exfat(yol)


# --------------------------------------------------------------------------
# X4: iki FAT / ActiveFat
# --------------------------------------------------------------------------
@test
def t07_x4_texfat_kapisi():
    """exFAT NumberOfFats=2 / ActiveFat: yazma ve boyutlandirma reddedilir, etkin bitmap secilir"""
    yol = mkexfat("x4.img", 64)
    d = DiskImage(yol)
    fs = ExFatFS(d)
    fs.write_file("/a.txt", b"merhaba")
    asil_bitmap = fs.bitmap_cluster
    # ikinci bitmap girisi (BitmapFlags bit0 = 1 -> ikinci FAT'in bitmap'i)
    giris = bytearray(32)
    giris[0] = 0x81
    giris[1] = 0x01
    struct.pack_into("<I", giris, 20, 0x1234)
    struct.pack_into("<Q", giris, 24, fs.bitmap_length)
    fs._insert_entry("/", bytes(giris))
    boot = bytearray(d.read(0, 512))
    boot[110] = 2
    d.write(0, bytes(boot))
    d.flush()
    fs = ExFatFS(d)
    assert fs.bitmap_cluster == asil_bitmap, fs.bitmap_cluster
    assert fs.write_block_reason
    raises(ExFatError, fs.write_file, "/b.txt", b"x")
    raises(ExFatError, fs.remove, "/a.txt")
    raises(ExFatError, fs.rename, "/a.txt", "c.txt")
    assert fs.read_file("/a.txt") == b"merhaba"
    acc = ExFatAccess(d)
    assert not acc.writable and acc.write_reason == fs.write_block_reason
    assert usedmap.fs_used_ranges(d, "exFAT") is None
    assert resize.fs_resize_info(d, "exFAT").kind == "unsupported"
    raises(resize.ResizeError, resize.exfat_resize, d, d.sector_count)
    boot[106] |= 0x01                          # ActiveFat = 1
    d.write(0, bytes(boot))
    fs = ExFatFS(d)
    assert fs.active_fat == 1 and fs.bitmap_cluster == 0x1234, fs.bitmap_cluster
    d.close()


# --------------------------------------------------------------------------
# F5: FAT12/16'da ust kume sozcugu (OS/2 EA)
# --------------------------------------------------------------------------
@test
def t08_f5_os2_ea_sozcugu():
    """FAT12/16: 0x14'teki OS/2 EA tutamaci kume sanilmaz; rename icerigi ve tutamaci korur"""
    for ft, mb in (("16", 64), ("12", 8)):
        yol = mkfat(f"f5_{ft}.img", mb, "-F", ft)
        d = DiskImage(yol)
        fs = FatFS(d)
        veri = payload(20000, seed=5)
        fs.write_file("/EA.BIN", veri)
        e = fs.find("/EA.BIN")
        off = fs.root_dir_offset + e.slot_offset + (e.slot_count - 1) * 32
        d.write(off + 0x14, struct.pack("<H", 0x0042))
        d.close()
        fsck_fat(yol)
        d = DiskImage(yol)
        fs = FatFS(d)
        assert fs.read_file("/EA.BIN") == veri
        fs.rename("/EA.BIN", "Yeni Ad.bin")
        e = fs.find("/Yeni Ad.bin")
        off = fs.root_dir_offset + e.slot_offset + (e.slot_count - 1) * 32
        assert struct.unpack("<H", d.read(off + 0x14, 2))[0] == 0x0042
        assert fs.read_file("/Yeni Ad.bin") == veri
        fs.remove("/Yeni Ad.bin")
        sil = [x for x in recovery.scan_deleted(fs) if x.size == len(veri)]
        assert sil and sil[0].cluster == e.cluster < 0x10000, sil
        hedef = os.path.join(WORK, f"f5_{ft}_kurtar.bin")
        recovery.recover_deleted(fs, sil[0], hedef)
        assert open(hedef, "rb").read() == veri
        d.close()
        fsck_fat(yol)


# --------------------------------------------------------------------------
# F6: FAT32 ExtFlags (aynalama kapali)
# --------------------------------------------------------------------------
@test
def t09_f6_extflags_etkin_fat():
    """FAT32 aynalama kapali (ExtFlags 0x81): etkin FAT1 okunur/yazilir, FAT0 dokunulmaz"""
    yol = mkfat("f6.img", 64, "-F", "32")
    d = DiskImage(yol)
    fs = FatFS(d)
    a = payload(50000, seed=6)
    fs.write_file("/A.BIN", a)
    bps, res, fsz = fs.bytes_per_sector, fs.reserved_sectors, fs.fat_size
    bk = fs.backup_boot_sector
    for sek in (0, bk):
        boot = bytearray(d.read(sek * bps, bps))
        struct.pack_into("<H", boot, 40, 0x81)
        d.write(sek * bps, bytes(boot))
    cop = b"\xF8\xFF\xFF\x0F\xFF\xFF\xFF\x0F" + bytes(fsz * bps - 8)
    d.write(res * bps, cop)                     # FAT0 bayat: tumu bos
    d.flush()
    fs = FatFS(d)
    assert fs.active_fat == 1 and not fs.fat_mirroring
    assert not fs.fat_problem(), fs.fat_problem()
    assert fs.read_file("/A.BIN") == a
    b = payload(70000, seed=66)
    fs.write_file("/B.BIN", b)
    assert d.read(res * bps, fsz * bps) == cop, "aynalama kapaliyken FAT0 yazildi"
    aralik = usedmap.fs_used_ranges(d, "FAT32")
    kume = fs.find("/B.BIN").cluster
    bayt = fs.cluster_offset(kume)
    assert aralik and any(o <= bayt < o + n for o, n in aralik), "usedmap FAT0'i okudu"
    d.close()
    # Kahin: FAT1'i FAT0'a kopyalayip aynalamayi acinca fsck temiz olmali
    kopya = os.path.join(WORK, "f6_kopya.img")
    shutil.copyfile(yol, kopya)
    d = DiskImage(kopya)
    d.write(res * bps, d.read((res + fsz) * bps, fsz * bps))
    for sek in (0, bk):
        boot = bytearray(d.read(sek * bps, bps))
        struct.pack_into("<H", boot, 40, 0)
        d.write(sek * bps, bytes(boot))
    d.close()
    fsck_fat(kopya)
    d = DiskImage(kopya)
    assert FatFS(d).read_file("/B.BIN") == b
    d.close()


# --------------------------------------------------------------------------
# F7: kayip bolum taramasinda birim
# --------------------------------------------------------------------------
@test
def t10_f7_kayip_bolum_birimleri():
    """Kayip bolum: -S 4096 FAT16, FAT12 ve -s 4096 exFAT boyutu aygit sektoruyle; FAT12 FAT12 gorunur"""
    parcalar = [
        (2048, mkfat("f7_fat16.img", 32, "-F", "16", "-S", "4096", "-s", "1"), "FAT16"),
        (2048 + 65536 + 2048, mkfat("f7_fat12.img", 4, "-F", "12"), "FAT12"),
        (2048 + 65536 + 2048 + 8192 + 2048,
         mkexfat("f7_exfat.img", 32, "-s", "4096"), "exFAT"),
    ]
    disk = blank("f7_disk.img", 160 * MIB)
    with open(disk, "r+b") as out:
        for lba, kaynak, _ in parcalar:
            out.seek(lba * 512)
            out.write(open(kaynak, "rb").read())
    d = DiskImage(disk, readonly=True)
    bulunan = recovery.scan_lost_partitions(d)
    d.close()
    sonuc = sorted((p.start_lba, p.sector_count, p.fs_type) for p in bulunan)
    beklenen = sorted((lba, os.path.getsize(k) // 512, t) for lba, k, t in parcalar)
    assert sonuc == beklenen, (sonuc, beklenen)


# --------------------------------------------------------------------------
# F8 / F9: etiket ve CP437
# --------------------------------------------------------------------------
@test
def t11_f8_fat_etiketi():
    """FAT etiketi kok dizinden okunur ('NO NAME' bos); set_label kok + BPB + BkBootSec"""
    fatlabel = need("fatlabel")
    yol = mkfat("f8.img", 64, "-F", "32", "-n", "ETIKET", "-b", "12")
    d = DiskImage(yol)
    fs = FatFS(d)
    assert fs.label == "ETIKET", fs.label
    assert detect(d).label == "ETIKET"
    fs.set_label("YENI")
    assert d.read(12 * 512 + 0x47, 11) == b"YENI       "
    d.close()
    fsck_fat(yol)
    assert run([fatlabel, yol]).stdout.strip() == "YENI"
    d = DiskImage(yol)
    assert FatFS(d).label == "YENI"
    FatFS(d).set_label("")
    d.close()
    fsck_fat(yol)
    d = DiskImage(yol)
    assert FatFS(d).label == "" and detect(d).label == ""
    d.close()
    yol = mkfat("f8_16.img", 32, "-F", "16")
    d = DiskImage(yol)
    assert FatFS(d).label == "", FatFS(d).label         # mkfs: "NO NAME"
    FatFS(d).set_label("ON ALTI")
    d.close()
    fsck_fat(yol)
    assert run([fatlabel, yol]).stdout.strip() == "ON ALTI"


@test
def t12_f9_kisa_ad_cp437():
    """Kisa adlar CP437 cozulur; ilk bayt 0x05 = 0xE5"""
    yol = mkfat("f9.img", 32, "-F", "16")
    d = DiskImage(yol)
    fs = FatFS(d)
    fs.write_file("/ABC.TXT", b"cp437")
    e = fs.find("/ABC.TXT")
    assert e.slot_count == 1
    d.write(fs.root_dir_offset + e.slot_offset, b"\x05\x81")
    d.close()
    fsck_fat(yol)
    d = DiskImage(yol)
    fs = FatFS(d)
    adlar = [x.name for x in fs.listdir("/")]
    assert "σüC.TXT" in adlar, adlar
    assert fs.read_file("/σüC.TXT") == b"cp437"
    fs.rename("/σüC.TXT", "duz.txt")
    d.close()
    fsck_fat(yol)


# --------------------------------------------------------------------------
# X11: FAT zinciri hayatta kalan silinmis exFAT dosyasi
# --------------------------------------------------------------------------
@test
def t13_x11_exfat_parcali_kurtarma():
    """exFAT silinmis parcali dosya FAT zincirinden kurtarilir (bitisik varsayilmaz)"""
    yol = mkexfat("x11.img", 64, "-c", "4K")
    d = DiskImage(yol)
    fs = ExFatFS(d)
    cb = fs.cluster_bytes
    bas = fs._find_free_run(16)
    cift = [bas + 2 * i for i in range(8)]
    tek = [bas + 2 * i + 1 for i in range(8)]
    icerik = {}
    for ad, kumeler, tohum in (("frag.bin", cift, 11), ("dolgu.bin", tek, 12)):
        veri = payload(8 * cb - 100, seed=tohum)
        icerik[ad] = veri
        for i, c in enumerate(kumeler):
            fs._set_used(c, True)
            fs.set_fat(c, kumeler[i + 1] if i + 1 < len(kumeler) else EOC)
            d.write(fs.cluster_offset(c), veri[i * cb:(i + 1) * cb].ljust(cb, b"\0"))
        fs._insert_entry("/", fs._build_entry_set(ad, 0x20, kumeler[0], len(veri), False))
    fs.flush()
    d.close()
    fsck_exfat(yol)
    # Linux/Windows gibi silme: InUse biti ve bitmap temizlenir, FAT kalir
    d = DiskImage(yol)
    fs = ExFatFS(d)
    e = fs.find("/frag.bin")
    veri = fs._read_dir_path("/")
    for i in range(e.slot_count):
        veri[e.slot_offset + 32 * i] &= 0x7F
    fs._write_dir(fs.root_cluster, veri)
    for c in cift:
        fs._set_used(c, False)
    fs.flush()
    d.close()
    fsck_exfat(yol)
    d = DiskImage(yol, readonly=True)
    fs = ExFatFS(d)
    sil = [x for x in recovery.scan_deleted(fs) if x.name == "frag.bin"]
    assert sil and not sil[0].contiguous and sil[0].condition == "iyi", sil
    hedef = os.path.join(WORK, "x11_kurtar.bin")
    recovery.recover_deleted(fs, sil[0], hedef)
    assert open(hedef, "rb").read() == icerik["frag.bin"]
    d.close()


# --------------------------------------------------------------------------
# FAT tutarlilik kapisi (picozed)
# --------------------------------------------------------------------------
@test
def t14_fat_tutarlilik_kapisi():
    """Bozuk FAT (picozed: FAT[0] onyukleme baytlari, aralik disi girdiler): okunur, yazilmaz"""
    for ft, args in (("32", ["-F", "32", "-s", "8", "-R", "6", "-b", "3"]),
                     ("16", ["-F", "16"])):
        yol = mkfat(f"tut_{ft}.img", 100, *args)
        d = DiskImage(yol)
        fs = FatFS(d)
        assert not fs.fat_problem() and not detect(d).damaged
        dosyalar = {f"/F{i}.BIN": payload(40000 + i, seed=i) for i in range(3)}
        for k, v in dosyalar.items():
            fs.write_file(k, v)
        bps, res, fsz, maxc = (fs.bytes_per_sector, fs.reserved_sectors,
                               fs.fat_size, fs.max_cluster)
        boot = d.read(0, 8)
        for kopya in range(fs.num_fats):
            taban = (res + kopya * fsz) * bps
            if ft == "32":
                d.write(taban, boot)                         # FAT[0], FAT[1]
                d.write(taban + 2 * 4, struct.pack("<I", 29542))  # kok -> disari
                d.write(taban + (maxc - 3) * 4, struct.pack("<II", maxc + 9, maxc + 70))
            else:
                d.write(taban, boot[:4])
                d.write(taban + (maxc - 3) * 2, struct.pack("<HH", maxc + 9, maxc + 70))
        d.close()
        out = fsck_fat(yol, expect_ok=False)
        assert "corrupt" in out.lower() or "out of range" in out.lower(), out
        d = DiskImage(yol)
        fs = FatFS(d)
        assert fs.fat_problem(), "tutarsiz FAT fark edilmedi"
        for k, v in dosyalar.items():
            assert fs.read_file(k) == v, k
        raises(FatError, fs.write_file, "/YENI.BIN", b"x")
        raises(FatError, fs.mkdir, "/K")
        raises(FatError, fs.remove, "/F0.BIN")
        raises(FatError, fs.rename, "/F0.BIN", "G.BIN")
        raises(FatError, fs.set_label, "X")
        acc = FatAccess(d)
        assert not acc.writable and acc.write_reason
        assert usedmap.fs_used_ranges(d, "FAT" + ft) is None
        assert detect(d).damaged
        assert resize.fs_resize_info(d, "FAT" + ft).kind == "unsupported"
        raises(resize.ResizeError, resize.fat_resize, d, d.sector_count - 2048)
        d.close()


# --------------------------------------------------------------------------
# BPB sektor boyutu != aygit sektoru: boyutlandirma ve tasima
# --------------------------------------------------------------------------
def _fill_fat(yol: str) -> dict:
    d = DiskImage(yol)
    fs = FatFS(d)
    fs.mkdir("/alt")
    icerik = {f"/alt/d{i}.bin": payload(30000 * (i + 1), seed=20 + i) for i in range(3)}
    icerik["/kok.bin"] = payload(123457, seed=30)
    for k, v in icerik.items():
        fs.write_file(k, v)
    d.close()
    return icerik


def _check_fat(yol: str, icerik: dict) -> FatFS:
    d = DiskImage(yol)
    fs = FatFS(d)
    for k, v in icerik.items():
        assert fs.read_file(k) == v, k
    d.close()
    return fs


@test
def t15_fat_boyutlandirma_sektor_boyutu():
    """FAT12/16/32 -S 1024/2048/4096: buyut + kucult + tasima (gizli sektor) -> fsck temiz"""
    boyut = {"12": 6, "16": 64, "32": 300}
    for ss in ("1024", "2048", "4096"):
        for ft in ("12", "16", "32"):
            ad = f"rs_{ft}_{ss}.img"
            ek = ["-s", "1"] if ft == "32" else []
            yol = mkfat(ad, boyut[ft], "-F", ft, "-S", ss, *ek)
            icerik = _fill_fat(yol)
            eski = os.path.getsize(yol) // 512
            # --- buyut ---
            with open(yol, "r+b") as fh:
                fh.truncate(int(eski * 1.5) * 512)
            d = DiskImage(yol)
            bilgi = resize.fs_resize_info(d, "FAT" + ft)
            assert bilgi.kind == "fat", (ad, bilgi.note)
            hedef = min(int(eski * 1.5), bilgi.max_sectors or int(eski * 1.5))
            resize.fat_resize(d, hedef)
            fs = FatFS(d)
            toplam = fs.total_sectors * fs.bytes_per_sector
            assert eski * 512 <= toplam <= hedef * 512, (ad, eski, toplam, hedef)
            d.close()
            try:
                fsck_fat(yol)
                _check_fat(yol, icerik)
            except AssertionError as exc:
                raise AssertionError(f"{ad} buyutme: {exc}")
            # --- kucult ---
            d = DiskImage(yol)
            bilgi = resize.fs_resize_info(d, "FAT" + ft)
            simdi = toplam // 512
            hedef = max(bilgi.min_sectors, int(simdi * 0.8))
            if hedef < simdi:
                resize.fat_resize(d, hedef)
                d.close()
                with open(yol, "r+b") as fh:
                    fh.truncate(hedef * 512)
                try:
                    fsck_fat(yol)
                    _check_fat(yol, icerik)
                except AssertionError as exc:
                    raise AssertionError(f"{ad} kucultme: {exc}")
            else:
                d.close()
            # --- tasima sonrasi gizli sektor yamasi ---
            d = DiskImage(yol)
            resize._patch_hidden_sectors(d, 4096)
            d.close()
            try:
                fsck_fat(yol)
            except AssertionError as exc:
                raise AssertionError(f"{ad} tasima: {exc}")


@test
def t16_exfat_boyutlandirma_sektor_boyutu():
    """exFAT -s 4096 ve 512: buyut + kucult (aygit sektoru <-> exFAT sektoru) -> fsck temiz"""
    for ss in ("4096", "512"):
        yol = mkexfat(f"rs_ex_{ss}.img", 64, "-s", ss, "-c", "4K")
        d = DiskImage(yol)
        fs = ExFatFS(d)
        icerik = {f"/f{i}.bin": payload(50000 * (i + 1), seed=40 + i) for i in range(3)}
        for k, v in icerik.items():
            fs.write_file(k, v)
        d.close()
        eski = os.path.getsize(yol) // 512
        with open(yol, "r+b") as fh:
            fh.truncate(int(eski * 1.5) * 512)
        d = DiskImage(yol)
        bilgi = resize.fs_resize_info(d, "exFAT")
        assert bilgi.kind == "exfat", bilgi.note
        resize.exfat_resize(d, int(eski * 1.5))
        fs = ExFatFS(d)
        toplam = fs.volume_length * fs.bytes_per_sector
        assert eski * 512 < toplam <= int(eski * 1.5) * 512, (ss, toplam)
        d.close()
        fsck_exfat(yol)
        d = DiskImage(yol)
        bilgi = resize.fs_resize_info(d, "exFAT")
        hedef = max(bilgi.min_sectors, int(eski * 1.1))
        resize.exfat_resize(d, hedef)
        d.close()
        with open(yol, "r+b") as fh:
            fh.truncate(hedef * 512)
        fsck_exfat(yol)
        d = DiskImage(yol)
        fs = ExFatFS(d)
        for k, v in icerik.items():
            assert fs.read_file(k) == v, k
        d.close()


@test
def t17_kirli_bayrak_kapisi():
    """Windows'un temiz ayirmadigi FAT32/FAT16/exFAT: yazma reddedilir, harita yok

    VirtualBox Windows 10'da olculdu: guc kesilince FAT32'de BPB 0x41 = 0x01,
    exFAT'te VolumeFlags = 0x0002 kaldi; Windows'un son yazdiklari diske
    inmemisti. Program bu birimlere yaziyor ve kullanilan alan haritasina
    guveniyordu.
    """
    from diskultimate.core.filesystem import ExFatAccess, FatAccess
    from diskultimate.core.fsdetect import detect

    for ad, args, off in (("k32.img", ("-F", "32"), 0x41), ("k16.img", ("-F", "16"), 0x25)):
        yol = mkfat(ad, 64, *args)
        d = DiskImage(yol)
        FatFS(d).write_file("/a.txt", b"temiz")
        d.flush()
        assert FatAccess(d).writable, "temiz birim yazilabilir olmali"
        assert usedmap.fs_used_ranges(d) is not None
        boot = bytearray(d.read(0, 512))
        boot[off] |= 0x01
        d.write(0, bytes(boot))
        d.flush()
        fs = FatFS(d)
        assert fs.dirty_state() and fs.write_block_reason
        raises(FatError, fs.write_file, "/b.txt", b"x")
        assert fs.read_file("/a.txt") == b"temiz"
        assert not FatAccess(d).writable
        assert usedmap.fs_used_ranges(d) is None
        assert detect(d).unclean
        raises(resize.ResizeError, resize.fat_resize, d, d.sector_count - 2048)
        # FAT[1] temiz kapatma biti (bayrak temizlenince yine kirli sayilmali)
        boot[off] &= 0xFE
        d.write(0, bytes(boot))
        fs = FatFS(d)
        assert not fs.dirty_state()
        fs.set_fat(1, fs.get_fat(1) & ~(0x08000000 if fs.fat_type == 32 else 0x8000))
        fs.flush()
        assert FatFS(d).dirty_state(), "FAT[1] ClnShut 0 kirli sayilmali"
        d.close()

    yol = mkexfat("kx.img", 64)
    d = DiskImage(yol)
    ExFatFS(d).write_file("/a.txt", b"temiz")
    d.flush()
    assert ExFatAccess(d).writable and usedmap.fs_used_ranges(d) is not None
    boot = bytearray(d.read(0, 512))
    boot[106] |= 0x02                          # VolumeDirty (saglama disi alan)
    d.write(0, bytes(boot))
    d.flush()
    fs = ExFatFS(d)
    assert fs.write_block_reason
    raises(ExFatError, fs.write_file, "/b.txt", b"x")
    assert fs.read_file("/a.txt") == b"temiz"
    assert not ExFatAccess(d).writable
    assert usedmap.fs_used_ranges(d) is None
    assert detect(d).unclean
    d.close()


# --------------------------------------------------------------------------
def main() -> int:
    tamam, basarisiz, atlanan = 0, [], []
    for fn in TESTS:
        ad = fn.__name__
        print(f"  {ad}: {(fn.__doc__ or '').strip().splitlines()[0]} ... ",
              end="", flush=True)
        try:
            fn()
            print("TAMAM")
            tamam += 1
        except Atlandi as exc:
            print(f"ATLANDI ({exc})")
            atlanan.append(ad)
        except Exception as exc:                 # noqa: BLE001
            print("BASARISIZ")
            traceback.print_exc()
            basarisiz.append((ad, str(exc).splitlines()[0] if str(exc) else repr(exc)))
    print(f"\nSonuc: {tamam}/{len(TESTS)} basarili"
          + (f" · {len(atlanan)} atlandi" if atlanan else ""))
    for ad, hata in basarisiz:
        print(f"  ! {ad}: {hata}")
    if not os.environ.get("DISKULTIMATE_KEEP_TEST_FILES"):
        shutil.rmtree(WORK, ignore_errors=True)
    return 1 if basarisiz else 0


if __name__ == "__main__":
    sys.exit(main())
