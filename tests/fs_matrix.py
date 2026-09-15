"""Dosya sistemi yetenek matrisi — sekiz bicim, ayni testler.

Her dosya sistemi icin ayni adimlar kosulur ve sonuc tek bir tabloda toplanir:

    bicimlendir -> tespit -> oku -> yaz -> harici dogrulama (fsck)

Amac iki yonlu:
  1. "Hangi bicimde ne calisiyor?" sorusunun tek yerden, olculerek yanitlanmasi.
  2. **ext yazma gelistirmesi icin kosum alani.** Yazma uygulandikca bu tablodaki
     `yaz` sutunu kendiliginden dolar; `e2fsck` sutunu da onu dogrular.

Calistirma:
    python3 -m tests.fs_matrix            # tum bicimler
    python3 -m tests.fs_matrix ext4 fat32 # yalnizca secilenler

**Durustluk kurali:** harici dogrulama araci yoksa sonuc "ATLANDI" yazilir,
asla "TAMAM" sayilmaz. Boylece aracsiz bir makinede (orn. Windows) tablo
yaniltmaz — `e2fsck` yoksa ext yazmasinin dogrulanmadigi acikca gorunur.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from diskultimate.core.filesystem import open_filesystem  # noqa: E402
from diskultimate.core.formatter import FS_KINDS, format_partition  # noqa: E402
from diskultimate.core.fsdetect import detect  # noqa: E402
from diskultimate.core.image import DiskImage, PartitionView  # noqa: E402
from diskultimate.paths import scratch  # noqa: E402

MIB = 1024 * 1024
TMP = scratch("fsmatrix")
START_LBA = 2048

# (bicim anahtari, birim boyutu MB) — her bicimin en az gereksinimi farkli
BOYUTLAR = {
    "fat12": 32, "fat16": 64, "fat32": 128, "exfat": 128,
    "ntfs": 128, "ext2": 64, "ext3": 128, "ext4": 128,
}

# Harici dogrulayicilar: (bicim -> [(arac, argumanlar)])
DOGRULAYICI = {
    "fat12": [("fsck.vfat", ["-n", "-f"])],
    "fat16": [("fsck.vfat", ["-n", "-f"])],
    "fat32": [("fsck.vfat", ["-n", "-f"])],
    "exfat": [("fsck.exfat", ["-n"])],
    "ntfs": [("ntfsfix", ["-n"]), ("ntfsinfo", ["-m"])],
    "ext2": [("e2fsck", ["-nf"])],
    "ext3": [("e2fsck", ["-nf"])],
    "ext4": [("e2fsck", ["-nf"])],
}

ATLANDI = "ATLANDI"


class Sonuc:
    """Tek bir bicimin olcum sonucu."""

    def __init__(self, key: str):
        self.key = key
        self.format = "-"
        self.detect = "-"
        self.read = "-"
        self.write = "-"
        self.fsck = "-"
        self.note = ""

    @property
    def failed(self) -> bool:
        return any(v.startswith("HATA")
                   for v in (self.format, self.detect, self.read,
                             self.write, self.fsck))


def _extract(image_path: str, dest: str, length: int) -> None:
    """Bolumu ayri bir dosyaya kopyalar (harici arac bolum ofsetini bilmez).

    **Tam `length` bayt** kopyalanir, dosyanin sonuna kadar degil. NTFS yedek
    onyukleme sektorunu aygitin **son** sektorunde arar; bolumden sonrasi da
    kopyalanirsa dosya birimden buyuk olur ve `ntfsfix` yedegi bulamayip
    "alternate boot sector BAD" der. Ilk surumde bu hata yapilmisti ve saglam
    bir NTFS birimi bozuk gibi raporlandi.
    """
    kalan = length
    with open(image_path, "rb") as src, open(dest, "wb") as out:
        src.seek(START_LBA * 512)
        while kalan > 0:
            block = src.read(min(4 * MIB, kalan))
            if not block:
                break
            out.write(block)
            kalan -= len(block)


def _verify(key: str, image_path: str, length: int) -> str:
    """Harici fsck varsa calistirir. Yoksa ATLANDI — asla TAMAM sayilmaz."""
    adaylar = DOGRULAYICI.get(key, [])
    arac = next(((t, a) for t, a in adaylar if shutil.which(t)), None)
    if arac is None:
        isimler = "/".join(t for t, _ in adaylar) or "-"
        return f"{ATLANDI} ({isimler} yok)"
    tool, args = arac
    part = image_path + ".part"
    _extract(image_path, part, length)
    try:
        r = subprocess.run([tool] + args + [part], capture_output=True, text=True)
        if r.returncode != 0:
            ciktı = (r.stdout + r.stderr).strip().splitlines()
            return f"HATA rc={r.returncode}: {ciktı[-1][:60] if ciktı else ''}"
        return f"TAMAM ({tool})"
    finally:
        try:
            os.unlink(part)
        except OSError:
            pass


def _write_suite(fs) -> str:
    """Yazma yolunu dener. Surucu salt okunursa nedenini kisaltip dondurur."""
    if not fs.writable:
        neden = (fs.write_reason or "").split(":")[0].strip()
        return f"{ATLANDI} (salt okunur: {neden[:34]})"
    icerik = bytes(range(256)) * 64          # 16 KB
    uzun = "Cok Uzun Bir Dosya Adi Ornegi 2026.bin"
    fs.mkdir("/veri")
    fs.write_file(f"/veri/{uzun}", icerik)
    if fs.read(f"/veri/{uzun}") != icerik:
        return "HATA: geri okuma farkli"
    girisler = {n.name for n in fs.listdir("/veri")}
    if uzun not in girisler:
        return f"HATA: uzun ad listelenmedi ({girisler})"
    fs.rename(f"/veri/{uzun}", "yeni.bin")
    if {n.name for n in fs.listdir("/veri")} != {"yeni.bin"}:
        return "HATA: yeniden adlandirma"
    fs.remove("/veri/yeni.bin")
    if fs.listdir("/veri"):
        return "HATA: silme"
    fs.flush()
    return "TAMAM"


def olc(key: str) -> Sonuc:
    s = Sonuc(key)
    mb = BOYUTLAR.get(key, 128)
    path = os.path.join(TMP, f"{key}.img")
    disk = DiskImage.create(path, (mb + 2) * MIB, overwrite=True)
    try:
        view = PartitionView(disk, START_LBA, mb * MIB // 512)
        try:
            ad = format_partition(view, key, label=f"DU{key.upper()}"[:11])
            s.format = f"TAMAM ({ad})"
        except Exception as exc:
            s.format = f"HATA: {exc}"
            return s

        view2 = PartitionView(disk, START_LBA, mb * MIB // 512)
        info = detect(view2)
        s.detect = f"TAMAM ({info.fs_type})" if info.fs_type else "HATA: tespit yok"

        fs = open_filesystem(view2, info)
        if fs is None:
            s.read = "HATA: erisim nesnesi yok"
            return s
        if not fs.readable:
            s.read = f"{ATLANDI} (okuyucu yok)"
            s.write = f"{ATLANDI} (okuyucu yok)"
        else:
            try:
                fs.listdir("/")
                s.read = "TAMAM"
            except Exception as exc:
                s.read = f"HATA: {exc}"
                return s
            try:
                s.write = _write_suite(fs)
            except Exception as exc:
                s.write = f"HATA: {exc}"
        try:
            fs.flush()
        except Exception:
            pass
    finally:
        disk.close()

    s.fsck = _verify(key, path, mb * MIB)
    if os.environ.get("DISKULTIMATE_KEEP_TEST_FILES") != "1":
        try:
            os.unlink(path)
        except OSError:
            pass
    return s


def main(argv) -> int:
    istenen = [a.lower() for a in argv[1:]]
    keys = [k.key for k in FS_KINDS]
    if istenen:
        bilinmeyen = [a for a in istenen if a not in keys]
        if bilinmeyen:
            print(f"Bilinmeyen bicim: {', '.join(bilinmeyen)}")
            print(f"Secenekler: {', '.join(keys)}")
            return 2
        keys = [k for k in keys if k in istenen]

    print("=== Dosya sistemi yetenek matrisi ===")
    print(f"Test alani: {TMP}\n")
    sonuclar = []
    for key in keys:
        print(f"  {key} ...", end="", flush=True)
        sonuc = olc(key)
        sonuclar.append(sonuc)
        print(" bitti")

    basliklar = ("Bicim", "Bicimlendir", "Tespit", "Oku", "Yaz", "Harici dogrulama")
    satirlar = [(s.key, s.format, s.detect, s.read, s.write, s.fsck)
                for s in sonuclar]
    genislik = [max(len(str(r[i])) for r in [basliklar] + satirlar)
                for i in range(len(basliklar))]
    ayirici = "-+-".join("-" * w for w in genislik)
    print("\n" + " | ".join(h.ljust(genislik[i]) for i, h in enumerate(basliklar)))
    print(ayirici)
    for r in satirlar:
        print(" | ".join(str(c).ljust(genislik[i]) for i, c in enumerate(r)))

    hatali = [s for s in sonuclar if s.failed]
    atlanan = sum(1 for s in sonuclar
                  if ATLANDI in s.write or ATLANDI in s.fsck)
    print(f"\n{len(sonuclar) - len(hatali)}/{len(sonuclar)} bicim hatasiz")
    if atlanan:
        print(f"{atlanan} bicimde atlanan adim var (arac yok veya yazici yok) — "
              "bunlar dogrulanmis SAYILMAZ.")
    for s in hatali:
        print(f"  ! {s.key}: "
              + "; ".join(v for v in (s.format, s.detect, s.read, s.write, s.fsck)
                          if v.startswith("HATA")))
    return 1 if hatali else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
