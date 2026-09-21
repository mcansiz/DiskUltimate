"""Fiziksel diskte NTFS boyutlandirma testi — yalnizca ayrilmis bos test diskinde.

Bu betik gercek bir diske yazar ve **yikicidir**: hedef diskin bolum tablosu
silinir. `tests/physical_write_test.py` ile ayni alti olcut gecerlidir (sistem
diski degil, bagli bolum yok, bilgi eksiksiz, boyut sinirinin altinda, yol
acikca verilmis, `--onayla` var).

Neden ayri bir test: goruntu dosyasi testleri (`tests.run_all` t35/t36) kendi
bicimlendiricimizin urettigi NTFS birimini boyutlandirir. Burada birim
**baskasinin araciyla** (`mkfs.ntfs`) olusturulur, isletim sistemi tarafindan
baglanip doldurulur ve ancak ondan sonra bizim kodumuzla kucultulur. Gercek
Windows birimlerinde meta veri (ozellikle `$MFTMirr`) birimin ortasina veya
sonuna dusar; tasima kodu ancak boyle bir birimde sinanir.

Dogrulama harici araclarla yapilir: `ntfsfix -n`, `ntfsinfo -m` ve `ntfs-3g`
ile baglayip dosyalarin ozetini karsilastirmak.

Kullanim (Linux misafirinde):
    sudo python3 -m tests.physical_ntfs_resize /dev/sdb --onayla --azami-gb=16
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from diskultimate.core.physical import find_disk  # noqa: E402
from diskultimate.core.ptable import human_size  # noqa: E402
from diskultimate.core.session import DiskSession  # noqa: E402
from tests.physical_write_test import olcutleri_dogrula  # noqa: E402

MIB = 1024 * 1024
GIB = 1024 * MIB


def calistir(komut, gerekli=True):
    """Harici arac cagirir; ciktisini basar."""
    sonuc = subprocess.run(komut, capture_output=True, text=True)
    etiket = " ".join(komut)
    if sonuc.returncode != 0:
        print(f"    ! {etiket} -> cikis {sonuc.returncode}")
        for satir in (sonuc.stdout + sonuc.stderr).strip().splitlines()[-6:]:
            print(f"      {satir}")
        if gerekli:
            raise RuntimeError(f"{etiket} basarisiz")
    else:
        print(f"    + {etiket}")
    return sonuc


def bolum_yolu(disk_yolu: str, index: int) -> str:
    """Linux'ta bolum aygit yolu (/dev/sdb -> /dev/sdb1)."""
    if disk_yolu[-1].isdigit():
        return f"{disk_yolu}p{index}"
    return f"{disk_yolu}{index}"


def ozet(yol: str) -> str:
    h = hashlib.sha256()
    with open(yol, "rb") as fh:
        for blok in iter(lambda: fh.read(1 << 20), b""):
            h.update(blok)
    return h.hexdigest()


def dosyalari_yaz(bagli: str) -> dict:
    """Birime dagilmis dosyalar yazar; (ad -> sha256) dondurur.

    Dosyalar **birden cok ve buyuk** olmali: kume tahsisi birimin sonuna
    dogru yayilsin ki kucultmede gercekten tasima gereksin.
    """
    ozetler = {}
    for i in range(6):
        ad = os.path.join(bagli, f"veri{i}.bin")
        with open(ad, "wb") as fh:
            fh.write(os.urandom(24 * MIB))
        ozetler[f"veri{i}.bin"] = ozet(ad)
    os.makedirs(os.path.join(bagli, "klasor"), exist_ok=True)
    ad = os.path.join(bagli, "klasor", "metin.txt")
    with open(ad, "w", encoding="utf-8") as fh:
        fh.write("DiskUltimate NTFS boyutlandirma testi\n" * 2000)
    ozetler["klasor/metin.txt"] = ozet(ad)
    return ozetler


def dogrula(bolum: str, ozetler: dict) -> None:
    """Birimi harici araclarla denetler ve dosya ozetlerini karsilastirir."""
    calistir(["ntfsfix", "-n", bolum])
    calistir(["ntfsinfo", "-m", bolum])
    bagli = tempfile.mkdtemp(prefix="du-ntfs-")
    calistir(["mount", "-t", "ntfs-3g", "-o", "ro", bolum, bagli])
    try:
        for ad, beklenen in ozetler.items():
            yol = os.path.join(bagli, ad)
            assert os.path.exists(yol), f"dosya kayboldu: {ad}"
            bulunan = ozet(yol)
            assert bulunan == beklenen, f"icerik degisti: {ad}"
        print(f"    + {len(ozetler)} dosyanin ozeti ayni")
    finally:
        calistir(["umount", bagli], gerekli=False)
        os.rmdir(bagli)


def testi_calistir(bilgi) -> int:
    disk_yolu = bilgi.path
    bolum = bolum_yolu(disk_yolu, 1)
    sektor = 512

    print("\n[1] GPT tablosu ve 4 GB'lik bolum olusturuluyor")
    oturum = DiskSession.open_physical(bilgi, readonly=False, confirm=True)
    try:
        oturum.create_table("gpt")
        bos = oturum.free_regions()[0]
        adet = min(bos.sector_count, 4 * GIB // sektor)
        bas = bos.start_lba
        oturum.create_partition(bas, adet, fs_key="", name="DU NTFS")
        sektor = oturum.image.sector_size
    finally:
        oturum.close()
    calistir(["partprobe", disk_yolu], gerekli=False)
    time.sleep(2)

    print("\n[2] Birim BASKASININ araciyla bicimlendiriliyor (mkfs.ntfs)")
    calistir(["mkfs.ntfs", "-f", "-L", "DUNTFS", bolum])

    print("\n[3] Birim baglanip dolduruluyor (~144 MB)")
    bagli = tempfile.mkdtemp(prefix="du-ntfs-")
    calistir(["mount", "-t", "ntfs-3g", bolum, bagli])
    try:
        ozetler = dosyalari_yaz(bagli)
    finally:
        calistir(["umount", bagli])
        os.rmdir(bagli)
    dogrula(bolum, ozetler)

    print("\n[4] Bolum 4 GB -> 2 GB kucultuluyor (saf Python)")
    oturum = DiskSession.open_physical(find_disk(disk_yolu), readonly=False,
                                       confirm=True)
    try:
        info = oturum.resize_info(1)
        print(f"    kind={info.kind}  en az={human_size(info.min_sectors * sektor)}"
              f"  not={info.note or '-'}")
        assert info.kind == "ntfs", f"saf Python NTFS yolu secilmedi: {info.kind}"
        hedef = 2 * GIB // sektor
        oturum.resize_partition(1, bas, hedef, confirm=True,
                                progress=lambda m, p: print(f"      {p:3d}% {m}"))
        oturum.reload()
        print(f"    -> bolum: {human_size(oturum.partitions[0].size)}, "
              f"{oturum.partitions[0].fs_type}")
        assert oturum.partitions[0].sector_count == hedef
    finally:
        oturum.close()
    calistir(["partprobe", disk_yolu], gerekli=False)
    time.sleep(2)
    dogrula(bolum, ozetler)

    print("\n[5] Bolum 2 GB -> 3 GB buyutuluyor")
    oturum = DiskSession.open_physical(find_disk(disk_yolu), readonly=False,
                                       confirm=True)
    try:
        hedef = 3 * GIB // sektor
        oturum.resize_partition(1, bas, hedef, confirm=True,
                                progress=lambda m, p: print(f"      {p:3d}% {m}"))
        oturum.reload()
        assert oturum.partitions[0].sector_count == hedef
        print(f"    -> bolum: {human_size(oturum.partitions[0].size)}")
    finally:
        oturum.close()
    calistir(["partprobe", disk_yolu], gerekli=False)
    time.sleep(2)
    dogrula(bolum, ozetler)

    print("\n[6] Buyuyen alan gercekten kullanilabilir mi?")
    bagli = tempfile.mkdtemp(prefix="du-ntfs-")
    calistir(["mount", "-t", "ntfs-3g", bolum, bagli])
    try:
        yeni = os.path.join(bagli, "buyume.bin")
        with open(yeni, "wb") as fh:
            fh.write(os.urandom(64 * MIB))
        ozetler["buyume.bin"] = ozet(yeni)
    finally:
        calistir(["umount", bagli])
        os.rmdir(bagli)
    dogrula(bolum, ozetler)

    print("\nSONUC: TUM ADIMLAR BASARILI")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    onay = "--onayla" in argv
    azami = 8 * GIB
    for a in argv:
        if a.startswith("--azami-gb="):
            azami = max(1, int(a.split("=", 1)[1])) * GIB
    yollar = [a for a in argv if not a.startswith("--")]
    if not yollar:
        print(__doc__)
        return 2
    if not onay:
        print("Bu test diske YAZAR. Onaylamak icin --onayla ekleyin.")
        return 2
    bilgi = olcutleri_dogrula(yollar[0], azami=azami)
    if bilgi is None:
        return 1
    return testi_calistir(bilgi)


if __name__ == "__main__":
    sys.exit(main())
