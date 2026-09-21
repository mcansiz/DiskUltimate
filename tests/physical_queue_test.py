"""Fiziksel diskte **bekleyen islem kuyrugu** testi — yalnizca ayrilmis test diskinde.

Bu betik gercek bir diske yazar ve **yikicidir**: hedef diskin bolum tablosu
silinir. `tests/physical_write_test.py` ile ayni alti olcut gecerlidir (sistem
diski degil, bilgi eksiksiz, boyut sinirinin altinda, yol acikca verilmis,
`--onayla` var; bagli bolum icin `--bagli-birimlere-izin-ver`).

## Neden ayri bir test

Iki hata gercek kullanimda, fiziksel diskte ortaya cikti ve ikisi de
**goruntu dosyasi testlerinde gorunmuyordu** — cunku ikisi de kuyrugun
"diskteki hal mi, plan mi?" sorusunu yanlis yanitlamasindan geliyordu:

* **ADR 0033** — "Bolum 1 sil" + "Bolum 2 sil": ilk adim uygulaninca numaralar
  kayiyor, ikinci adim *"2 numarali bolum yok"* diye duruyordu.
* **ADR 0034** — arka arkaya uc "yeni bolum": ucunun de hedefi ayni LBA
  oluyor, ikinci adim *"2 numarali bolum ile cakisiyor"* diye duruyordu.

Burada ikisi de kullanicinin yaptigi sirayla, gercek diskte tekrarlanir.
Arayuzun yeni adimi **plana gore** kurdugu (`planned_free_regions`) bu betikte
`planview.project()` ile birebir taklit edilir.

Kullanim (Linux misafirinde):
    sudo python3 -m tests.physical_queue_test /dev/sdb --onayla --azami-gb=16
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from diskultimate.core import operations as ops  # noqa: E402
from diskultimate.core import planview  # noqa: E402
from diskultimate.core.physical import find_disk  # noqa: E402
from diskultimate.core.ptable import human_size  # noqa: E402
from diskultimate.core.session import DiskSession  # noqa: E402
from tests.physical_write_test import olcutleri_dogrula  # noqa: E402

MIB = 1024 * 1024
GIB = 1024 * MIB

# Kullanicinin denedigi olculer (worklog 2026-09-17)
PLAN = (("fat32", 568 * MIB), ("ntfs", 1434 * MIB), ("exfat", 3103 * MIB))


def calistir(komut, gerekli=False):
    sonuc = subprocess.run(komut, capture_output=True, text=True)
    if sonuc.returncode != 0 and gerekli:
        raise RuntimeError(f"{' '.join(komut)} basarisiz: {sonuc.stderr}")
    return sonuc


def cekirdek_gorunumu(disk_yolu: str) -> str:
    calistir(["partprobe", disk_yolu])
    time.sleep(2)
    return calistir(["lsblk", "-o", "NAME,SIZE,FSTYPE,LABEL", disk_yolu]).stdout


def adimlari_goster(kuyruk) -> None:
    for satir in kuyruk.describe():
        print(f"    {satir}")


def uygula(session, kuyruk):
    durumlar = []
    sonuc = kuyruk.apply(
        session,
        on_step_done=lambda i, op, err: durumlar.append(
            (op.title, err or "TAMAM")))
    for baslik, durum in durumlar:
        isaret = "+" if durum == "TAMAM" else "!"
        print(f"    {isaret} {baslik}: {durum}")
    return sonuc


def bolum_olustur_senaryosu(bilgi) -> None:
    """ADR 0034: arka arkaya uc bolum ayni bos alani paylasmali."""
    print("\n" + "=" * 62)
    print("A) Arka arkaya uc bolum (ADR 0034)")
    print("=" * 62)
    session = DiskSession.open_physical(bilgi, readonly=False, confirm=True)
    try:
        session.create_table("gpt")
        sektor = session.image.sector_size

        # Kok neden kontrolu: diske gore sorulsaydi uc adim da ayni yere
        # kurulurdu. Bu **yalnizca olcum**, kuyruga girmez.
        diske_gore = [max(session.free_regions(),
                          key=lambda r: r.sector_count).start_lba
                      for _ in PLAN]
        print(f"[1] Diske gore sorulsaydi hedefler: {diske_gore}")
        assert len(set(diske_gore)) == 1, "kok neden artik uretilemiyor"

        # Arayuzun yaptigi: her adim **plana gore** kurulur
        kuyruk = ops.OperationQueue()
        plana_gore = []
        for fs_key, boyut in PLAN:
            plan = planview.project(session, kuyruk)
            bolgeler = plan.free if plan is not None else session.free_regions()
            bolge = max(bolgeler, key=lambda r: r.sector_count)
            adet = min(boyut // sektor, bolge.sector_count)
            cakisan = planview.overlap_at(plan, bolge.start_lba, adet) \
                if plan is not None else None
            assert cakisan is None, f"plan cakismasi: {cakisan}"
            plana_gore.append(bolge.start_lba)
            kuyruk.add(ops.create_op(bolge.start_lba, adet, sektor,
                                     fs_key=fs_key,
                                     label=fs_key.upper()[:11]))
        print(f"[2] Plana gore hedefler: {plana_gore}")
        assert len(set(plana_gore)) == len(PLAN), "hedefler hala ayni"
        adimlari_goster(kuyruk)

        print("[3] Uygula")
        sonuc = uygula(session, kuyruk)
        assert sonuc.ok, f"FIZIKSEL DISKTE KIRIK: {sonuc.summary()}"
        session.reload()
        assert len(session.partitions) == len(PLAN), session.partitions

        sirali = sorted(session.partitions, key=lambda p: p.start_lba)
        for onceki, sonraki in zip(sirali, sirali[1:]):
            assert sonraki.start_lba > onceki.end_lba, "bolumler cakisti"
        for part in sirali:
            print(f"    -> bolum {part.index}: {part.fs_type or '-'} "
                  f"{human_size(part.size)} @ LBA {part.start_lba}")
    finally:
        session.close()
    print("[4] Cekirdegin gordugu:")
    for satir in cekirdek_gorunumu(bilgi.path).strip().splitlines():
        print(f"    {satir}")


def bolum_sil_senaryosu(bilgi) -> None:
    """ADR 0033: arka arkaya silme adimlari numara kaymasina takilmamali."""
    print("\n" + "=" * 62)
    print("B) Arka arkaya silme (ADR 0033)")
    print("=" * 62)
    session = DiskSession.open_physical(find_disk(bilgi.path), readonly=False,
                                        confirm=True)
    try:
        session.reload()
        assert len(session.partitions) >= 2, "once A senaryosu kosmali"
        kuyruk = ops.OperationQueue()
        for part in session.partitions:
            kuyruk.add(ops.delete_op(part.index, at_lba=part.start_lba,
                                     name=part.display_name))
        adimlari_goster(kuyruk)
        sonuc = uygula(session, kuyruk)
        assert sonuc.ok, f"FIZIKSEL DISKTE KIRIK: {sonuc.summary()}"
        session.reload()
        assert not session.partitions, session.partitions
        print("    -> disk bos")
    finally:
        session.close()
    print("Cekirdegin gordugu:")
    for satir in cekirdek_gorunumu(bilgi.path).strip().splitlines():
        print(f"    {satir}")


def main() -> int:
    argv = sys.argv[1:]
    if "--onayla" not in argv:
        print(__doc__)
        print("Bu test diske YAZAR. Onaylamak icin --onayla ekleyin.")
        return 2
    azami = 8 * GIB
    for a in argv:
        if a.startswith("--azami-gb="):
            azami = max(1, int(a.split("=", 1)[1])) * GIB
    yollar = [a for a in argv if not a.startswith("--")]
    if not yollar:
        print("Hedef disk verilmedi.")
        return 2
    bilgi = olcutleri_dogrula(
        yollar[0], azami=azami,
        bagli_izin="--bagli-birimlere-izin-ver" in argv)
    if bilgi is None:
        return 1
    bolum_olustur_senaryosu(bilgi)
    bolum_sil_senaryosu(bilgi)
    print("\nSONUC: TUM ADIMLAR BASARILI")
    return 0


if __name__ == "__main__":
    sys.exit(main())
