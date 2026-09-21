"""NTFS yazma dogrulamasi — her adimdan sonra `ntfsfix`, sonunda `ntfs-3g`.

`ext_write_check.py` ile ayni mantik: birim her degisiklikten sonra **bagimsiz
araca** sorulur. `ntfsfix` yoksa kosum **basarisiz sayilir** (atlanmaz), cunku
dogrulanmamis bir NTFS yazmasinin "gecti" demesi tehlikelidir.

    python3 -m tests.ntfs_write_check

Iki birim uzerinde kosar:
  * **kendi bicimlendiricimiz** — indeks `$INDEX_ROOT` icinde durur
  * **`mkntfs`** — indeks B+ agacina tasmistir (`$INDEX_ALLOCATION`), gercek
    dunyada karsilasilan yerlesim budur

Yalnizca goruntu dosyalari uzerinde calisir; fiziksel diske dokunmaz.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from diskultimate.core.image import DiskImage, PartitionView  # noqa: E402
from diskultimate.core.ntfs import format_ntfs  # noqa: E402
from diskultimate.core.ntfsread import NtfsFS  # noqa: E402
from diskultimate.core.ntfswrite import NtfsWriter  # noqa: E402
from diskultimate.paths import scratch  # noqa: E402

MIB = 1024 * 1024
TMP = scratch("ntfswrite")
VOLUME_MB = 128


class Basarisiz(Exception):
    pass


class _RawImage:
    """Goruntu dosyasi uzerinde `BlockDevice` benzeri erisim."""

    sector_size = 512
    readonly = False

    def __init__(self, path: str):
        self.fh = open(path, "r+b")
        self.fh.seek(0, 2)
        self._n = self.fh.tell()

    @property
    def size(self) -> int:
        return self._n

    @property
    def sector_count(self) -> int:
        return self._n // 512

    def read(self, off: int, n: int) -> bytes:
        self.fh.seek(off)
        return self.fh.read(n)

    def write(self, off: int, data: bytes) -> None:
        self.fh.seek(off)
        self.fh.write(data)

    def flush(self) -> None:
        self.fh.flush()
        os.fsync(self.fh.fileno())

    def close(self) -> None:
        self.fh.close()


def _ntfsfix(path: str, step: str) -> None:
    tool = shutil.which("ntfsfix")
    if not tool:
        raise Basarisiz("ntfsfix bulunamadi — NTFS yazmasi dogrulanamaz")
    r = subprocess.run([tool, "-n", path], capture_output=True, text=True)
    if r.returncode != 0:
        metin = (r.stdout + r.stderr).strip()
        raise Basarisiz(f"[{step}] ntfsfix rc={r.returncode}\n{metin[:700]}")


def _mount_check(path: str, expect_text: bytes, expect_size: int) -> str:
    """`ntfs-3g` varsa birimi baglar: once okur, sonra **uzerine yazar**.

    Salt okunur baglamak yetmiyor. Bicimlendirdigimiz birim uzun sure
    "ntfs-3g dogruladi" raporu verdi, oysa isletim sisteminin surucusu
    uzerine **dosya olusturamiyordu** (ADR 0037): `$MFT:$BITMAP` yerlesikti
    ve kok dizinde "." girisi yoktu. Ikisi de yalnizca yazma yolunda ortaya
    cikiyor, bu yuzden bu denetim artik yazma da yapar.
    """
    if not shutil.which("ntfs-3g") or os.geteuid() != 0:
        return "ntfs-3g baglama atlandi (arac yok veya root degil)"
    nokta = os.path.join(TMP, "mnt")
    os.makedirs(nokta, exist_ok=True)
    # **Tek** baglama: once salt okunur baglayip sonra yeniden baglamak,
    # ilk baglamadan kalan salt okunur loop aygitini yeniden kullandirip
    # "Read-only file system" hatasi uretiyordu (bu bir test tuzagiydi,
    # birimin kusuru degil).
    r = subprocess.run(["mount", path, nokta], capture_output=True, text=True)
    if r.returncode != 0:
        raise Basarisiz(f"ntfs-3g baglayamadi: {(r.stdout + r.stderr)[:200]}")
    try:
        with open(os.path.join(nokta, "okundu.txt"), "rb") as fh:
            if fh.read() != expect_text:
                raise Basarisiz("ntfs-3g: metin dosyasi farkli")
        boyut = os.path.getsize(os.path.join(nokta, "klasor", "buyuk.bin"))
        if boyut != expect_size:
            raise Basarisiz(f"ntfs-3g: buyuk.bin {boyut}, {expect_size} bekleniyordu")
        girisler = len(os.listdir(nokta))

        # --- yazma: surucu birime dosya/klasor ekleyebilmeli ---
        yeni = os.path.join(nokta, "surucu-yazdi.txt")
        icerik = b"isletim sisteminin surucusu yazdi\n" * 64
        try:
            with open(yeni, "wb") as fh:
                fh.write(icerik)
            os.mkdir(os.path.join(nokta, "surucu-klasor"))
        except OSError as exc:
            raise Basarisiz(f"ntfs-3g birime YAZAMADI: {exc}")
        with open(yeni, "rb") as fh:
            if fh.read() != icerik:
                raise Basarisiz("ntfs-3g: yazilan dosya farkli okundu")
        if "surucu-klasor" not in os.listdir(nokta):
            raise Basarisiz("ntfs-3g: olusturulan klasor listede yok")
    finally:
        subprocess.run(["umount", nokta], capture_output=True)
    _ntfsfix(path, "surucu yazmasi sonrasi")
    return f"ntfs-3g okudu ve YAZDI ({girisler} giris)"


def _make_ours(path: str) -> None:
    disk = DiskImage.create(path, VOLUME_MB * MIB, overwrite=True)
    format_ntfs(PartitionView(disk, 0, disk.sector_count), label="DUNTFS")
    disk.close()


def _make_mkntfs(path: str) -> None:
    tool = shutil.which("mkntfs")
    if not tool:
        raise Basarisiz("mkntfs bulunamadi")
    with open(path, "wb") as fh:
        fh.truncate(VOLUME_MB * MIB)
    r = subprocess.run([tool, "-Q", "-F", "-L", "NTFSW", path],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise Basarisiz(f"mkntfs basarisiz: {r.stderr[:200]}")


def kosum(label: str, builder, root_entries: int) -> str:
    """Bir birim uretip uzerine yazar; her adimda `ntfsfix` kosar.

    `root_entries` koke eklenecek dosya sayisidir. B+ dugumu bolunemedigi icin
    bu sayi indeks blogunun kapasitesiyle sinirlidir; sinir asilirsa yazici
    **acikca reddeder** ve bu test onu hata sayar.
    """
    path = os.path.join(TMP, f"{label}.img")
    builder(path)
    _ntfsfix(path, "baslangic")

    metin = b"NTFS yazma denemesi\n"
    buyuk = bytes(range(256)) * 400          # 100 KB -> kumelere yazilir
    dev = _RawImage(path)
    try:
        fs = NtfsFS(dev)
        w = NtfsWriter(fs)
        ok, reason = w.write_support()
        if not ok:
            raise Basarisiz(f"yazma desteklenmiyor: {reason}")

        w.write_file("/okubeni.txt", metin)
        w.flush()
        _ntfsfix(path, "kucuk dosya")

        w.mkdir("/klasor")
        w.flush()
        _ntfsfix(path, "mkdir")

        w.write_file("/klasor/buyuk.bin", buyuk)
        w.flush()
        _ntfsfix(path, "buyuk dosya (veri kosullari)")

        for i in range(root_entries):
            w.write_file("/d_%02d.dat" % i, b"icerik %d" % i)
        w.flush()
        _ntfsfix(path, f"kokte {root_entries} giris")

        w.rename("/okubeni.txt", "okundu.txt")
        w.flush()
        _ntfsfix(path, "rename")

        # --- kendi okuyucumuzla geri oku ---
        fs2 = NtfsFS(_RawImage(path))
        if fs2.read_file("/klasor/buyuk.bin") != buyuk:
            raise Basarisiz("buyuk dosya geri okumada farkli")
        if fs2.read_file("/okundu.txt") != metin:
            raise Basarisiz("yeniden adlandirilan dosya okunamadi")
        adlar = {e.name for e in fs2.listdir("/")}
        if len([a for a in adlar if a.startswith("d_")]) != root_entries:
            raise Basarisiz(f"kokte {root_entries} giris beklendi: {sorted(adlar)}")

        # --- silme ---
        for i in range(root_entries):
            w.remove("/d_%02d.dat" % i)
        w.remove("/klasor/buyuk.bin")
        w.remove("/klasor")
        w.flush()
        _ntfsfix(path, "silme")

        fs3 = NtfsFS(_RawImage(path))
        kalan = {e.name for e in fs3.listdir("/")}
        if kalan != {"okundu.txt"}:
            raise Basarisiz(f"silme sonrasi kok temiz degil: {kalan}")
    finally:
        dev.close()

    # Baglama denetimi icin dosyalari geri koy
    dev2 = _RawImage(path)
    try:
        fs4 = NtfsFS(dev2)
        w2 = NtfsWriter(fs4)
        w2.mkdir("/klasor")
        w2.write_file("/klasor/buyuk.bin", buyuk)
        w2.flush()
    finally:
        dev2.close()
    _ntfsfix(path, "baglama oncesi")
    sonuc = _mount_check(path, metin, len(buyuk))

    if os.environ.get("DISKULTIMATE_KEEP_TEST_FILES") != "1":
        try:
            os.unlink(path)
        except OSError:
            pass
    return sonuc


def main(argv) -> int:
    print("=== NTFS yazma dogrulamasi (her adimda ntfsfix) ===\n")
    kosumlar = [
        ("kendi-bicimlendiricimiz", _make_ours, 4),
        ("mkntfs", _make_mkntfs, 15),
    ]
    hata = 0
    for label, builder, entries in kosumlar:
        print(f"  {label} ...", end="", flush=True)
        try:
            not_ = kosum(label, builder, entries)
            print(f" TAMAM  ({not_})")
        except Basarisiz as exc:
            print(" BASARISIZ")
            print(f"      {exc}")
            hata += 1
        except Exception as exc:
            print(" HATA")
            print(f"      {type(exc).__name__}: {exc}")
            hata += 1
    print(f"\n{len(kosumlar) - hata}/{len(kosumlar)} kosum dogrulandi")
    return 1 if hata else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
