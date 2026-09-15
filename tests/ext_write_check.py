"""ext yazma dogrulamasi — her adimdan sonra `e2fsck -nf`.

Yazma gelistirmesinin guvenlik agi: birim her degisiklikten sonra bagimsiz
araca sorulur. `e2fsck` yoksa kosum **basarisiz sayilir** (atlanmaz), cunku
dogrulanmamis ext yazmasinin "gecti" demesi tehlikelidir.

    python3 -m tests.ext_write_check            # ext2, ext3, ext4
    python3 -m tests.ext_write_check ext4

Yalnizca **goruntu dosyalari** uzerinde calisir; fiziksel diske dokunmaz.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from diskultimate.core.ext import format_ext  # noqa: E402
from diskultimate.core.extread import ExtFS  # noqa: E402
from diskultimate.core.extcsum import verify_volume  # noqa: E402
from diskultimate.core.extwrite import ExtWriter  # noqa: E402
from diskultimate.core.image import DiskImage, PartitionView  # noqa: E402
from diskultimate.paths import scratch  # noqa: E402

MIB = 1024 * 1024
TMP = scratch("extwrite")
START = 2048
PART_MB = 128


class Basarisiz(Exception):
    pass


def _fsck(image: str, adim: str) -> None:
    """Bolumu ayiklayip `e2fsck -nf` ile denetler."""
    tool = shutil.which("e2fsck")
    if not tool:
        raise Basarisiz("e2fsck bulunamadi — ext yazmasi dogrulanamaz")
    part = image + ".part"
    with open(image, "rb") as src, open(part, "wb") as out:
        src.seek(START * 512)
        remaining = PART_MB * MIB
        while remaining > 0:
            block = src.read(min(4 * MIB, remaining))
            if not block:
                break
            out.write(block)
            remaining -= len(block)
    try:
        r = subprocess.run([tool, "-nf", part], capture_output=True, text=True)
        if r.returncode != 0:
            metin = (r.stdout + r.stderr).strip()
            raise Basarisiz(f"[{adim}] e2fsck rc={r.returncode}\n{metin[:900]}")
    finally:
        try:
            os.unlink(part)
        except OSError:
            pass


def _open(disk: DiskImage):
    view = PartitionView(disk, START, PART_MB * MIB // 512)
    fs = ExtFS(view)
    return fs, ExtWriter(fs)


def kosum(version: str) -> None:
    path = os.path.join(TMP, f"{version}.img")
    disk = DiskImage.create(path, (PART_MB + 2) * MIB, overwrite=True)
    try:
        format_ext(PartitionView(disk, START, PART_MB * MIB // 512),
                   version, label=f"DU{version.upper()}")
        _fsck(path, "bicimlendirme")

        fs, w = _open(disk)
        ok, reason = w.write_support()
        if not ok:
            raise Basarisiz(f"yazma desteklenmiyor: {reason}")

        # 1) kucuk dosya (yalnizca dogrudan bloklar)
        kucuk = b"DiskUltimate ext yazma denemesi\n" * 8
        w.write_file("/okubeni.txt", kucuk)
        w.flush()
        _fsck(path, "kucuk dosya")

        # 2) klasor
        w.mkdir("/veri")
        w.flush()
        _fsck(path, "mkdir")

        # 3) dolayli blok gerektiren dosya (>12 blok)
        buyuk = bytes(range(256)) * 1024          # 256 KB
        w.write_file("/veri/buyuk.bin", buyuk)
        w.flush()
        _fsck(path, "dolayli blok")

        # 4) uzun adli dosya
        uzun = "Cok Uzun Bir Dosya Adi Ornegi 2026 - deneme.bin"
        w.write_file(f"/veri/{uzun}", b"x" * 5000)
        w.flush()
        _fsck(path, "uzun ad")

        # 5) cok sayida giris (dizin blogu doldurma yolu)
        for i in range(60):
            w.write_file(f"/veri/dosya_{i:03d}.dat", b"%d" % i)
        w.flush()
        _fsck(path, "60 giris")

        # --- okuyucu ile dogrula ---
        fs2, _ = _open(disk)
        if fs2.read_data(fs2.resolve("/okubeni.txt")) != kucuk:
            raise Basarisiz("kucuk dosya geri okumada farkli")
        if fs2.read_data(fs2.resolve("/veri/buyuk.bin")) != buyuk:
            raise Basarisiz("dolayli bloklu dosya geri okumada farkli")
        adlar = {e.name for e in fs2.read_dir(fs2.resolve("/veri"))}
        if uzun not in adlar:
            raise Basarisiz("uzun ad listelenmedi")
        if len([a for a in adlar if a.startswith("dosya_")]) != 60:
            raise Basarisiz(f"60 giris beklendi, {len(adlar)} bulundu")

        # 6) yeniden adlandirma
        w.rename("/okubeni.txt", "okundu.txt")
        w.flush()
        _fsck(path, "rename")

        # 7) silme
        for i in range(60):
            w.remove(f"/veri/dosya_{i:03d}.dat")
        w.remove(f"/veri/{uzun}")
        w.remove("/veri/buyuk.bin")
        w.flush()
        _fsck(path, "dosya silme")

        w.remove("/veri")
        w.remove("/okundu.txt")
        w.flush()
        _fsck(path, "klasor silme")

        fs3, _ = _open(disk)
        kalan = {e.name for e in fs3.read_dir(fs3.read_inode(2))
                 if e.name not in (".", "..")}
        if kalan != {"lost+found"}:
            raise Basarisiz(f"silme sonrasi kok temiz degil: {kalan}")
    finally:
        disk.close()
        if os.environ.get("DISKULTIMATE_KEEP_TEST_FILES") != "1":
            try:
                os.unlink(path)
            except OSError:
                pass


class _HamDosya:
    """Ham goruntu dosyasi uzerinde `BlockDevice` benzeri erisim."""

    sector_size = 512
    readonly = False

    def __init__(self, path):
        self.fh = open(path, "r+b")
        self.fh.seek(0, 2)
        self._n = self.fh.tell()

    @property
    def size(self):
        return self._n

    @property
    def sector_count(self):
        return self._n // 512

    def read(self, off, n):
        self.fh.seek(off)
        return self.fh.read(n)

    def write(self, off, data):
        self.fh.seek(off)
        self.fh.write(data)

    def flush(self):
        self.fh.flush()

    def close(self):
        self.fh.close()


def kosum_mkfs() -> None:
    """`mkfs.ext4` ile uretilen birime yazar - **metadata_csum yolu**.

    Kendi bicimlendiricimiz metadata_csum acmaz, bu yuzden o yol yalnizca
    gercek `mkfs.ext4` ciktisinda sinanabilir. Uretilen birim ayrica `64bit`,
    `extent` ve `flex_bg` tasir; gercek dunyada karsilasilan yerlesim budur
    (kullanicinin SD kartinin ext4 bolumu de boyledir).
    """
    tool = shutil.which("mkfs.ext4")
    if not tool:
        raise Basarisiz("mkfs.ext4 bulunamadi - metadata_csum yolu sinanamaz")
    path = os.path.join(TMP, "mkfs.img")
    with open(path, "wb") as fh:
        fh.truncate(PART_MB * MIB)
    r = subprocess.run([tool, "-q", "-F", "-L", "CSUMTEST", path],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise Basarisiz("mkfs.ext4 basarisiz: " + r.stderr[:200])

    def fsck_tam(adim):
        e2 = shutil.which("e2fsck")
        rr = subprocess.run([e2, "-nf", path], capture_output=True, text=True)
        if rr.returncode != 0:
            raise Basarisiz("[%s] e2fsck rc=%d\n%s"
                            % (adim, rr.returncode,
                               (rr.stdout + rr.stderr)[:700]))

    dev = _HamDosya(path)
    try:
        fs = ExtFS(dev)
        rapor = verify_volume(fs, max_inodes=64, max_dirs=10)
        if not rapor.get("metadata_csum"):
            raise Basarisiz("mkfs.ext4 metadata_csum acmadi; test anlamsiz")
        for alan in ("ustblok", "grup_tanimlayici", "bitmap", "inode",
                     "dizin_blogu"):
            yanlis = rapor.get(alan, (0, 0))[1]
            if yanlis:
                raise Basarisiz("saglama hesabi yanlis: %s (%d hata)"
                                % (alan, yanlis))

        w = ExtWriter(fs)
        ok, reason = w.write_support()
        if not ok:
            raise Basarisiz("metadata_csum birimde yazma reddedildi: " + reason)
        fsck_tam("baslangic")
        w.write_file("/okubeni.txt", b"metadata_csum ile yazma\n")
        w.mkdir("/veri")
        w.write_file("/veri/buyuk.bin", bytes(range(256)) * 512)
        for i in range(40):
            w.write_file("/veri/d_%02d.dat" % i, b"x" * (100 + i))
        w.flush()
        fsck_tam("yazma")
        w.rename("/okubeni.txt", "okundu.txt")
        w.flush()
        fsck_tam("rename")
        for i in range(40):
            w.remove("/veri/d_%02d.dat" % i)
        w.remove("/veri/buyuk.bin")
        w.remove("/veri")
        w.remove("/okundu.txt")
        w.flush()
        fsck_tam("silme")
    finally:
        dev.close()
        if os.environ.get("DISKULTIMATE_KEEP_TEST_FILES") != "1":
            try:
                os.unlink(path)
            except OSError:
                pass


def main(argv) -> int:
    versions = [a for a in argv[1:]] or ["ext2", "ext3", "ext4"]
    print("=== ext yazma dogrulamasi (her adimda e2fsck -nf) ===\n")
    hata = 0
    for v in versions:
        print(f"  {v} ...", end="", flush=True)
        try:
            kosum(v)
            print(" TAMAM")
        except Basarisiz as exc:
            print(" BASARISIZ")
            print(f"      {exc}")
            hata += 1
        except Exception as exc:                       # beklenmeyen
            print(" HATA")
            print(f"      {type(exc).__name__}: {exc}")
            hata += 1
    if not argv[1:]:
        # metadata_csum yolu yalnizca gercek mkfs.ext4 ciktisinda sinanabilir:
        # kendi bicimlendiricimiz bu ozelligi acmaz.
        print("  metadata_csum (mkfs.ext4) ...", end="", flush=True)
        try:
            kosum_mkfs()
            print(" TAMAM")
        except Basarisiz as exc:
            print(" BASARISIZ")
            print(f"      {exc}")
            hata += 1
        versions = versions + ["metadata_csum"]
    print(f"\n{len(versions) - hata}/{len(versions)} kosum dogrulandi")
    return 1 if hata else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
