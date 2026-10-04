"""Windows surucu harfi: MBR mantiksal bolumde dogru bolume atanir (ADR 0086).

Yalnizca test VM'inde ya da GitHub Actions makinesinde calisir
(`DU_TEST_VM=1`): bos bir VHD takilir (`tests/long/device.py`), uzerinde
MBR + birincil + genisletilmis + iki mantiksal bolum olusturulur.

Olculen:
* Windows'un bolum numaralari bizimkilerden farkli mi (bulgunun kaniti);
* butun harfler kaldirilir; **ikinci mantiksal** bolume harf atanir ->
  harf yalnizca o ofsetteki bolumde gorunur, digerleri harfsiz kalir;
* harf kaldirilir -> o bolum yine harfsiz.

Kullanim (yonetici, VM misafirinde):
    set DU_TEST_VM=1
    python -m tests.physical_drive_letter_test
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(KOK, "src"))
sys.path.insert(0, KOK)

from diskultimate.core import operations as ops  # noqa: E402
from diskultimate.core import physical as ph  # noqa: E402
from diskultimate.core.ptable import MBR_EXTENDED_TYPES  # noqa: E402
from diskultimate.core.session import DiskSession  # noqa: E402
from tests.long import device as dev  # noqa: E402

MIB = 1024 * 1024


def windows_harita(disk_no: int) -> dict:
    """{ofset: (Windows numarasi, harf)} — Windows'un kendi gorusu."""
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"Get-Partition -DiskNumber {disk_no} | ForEach-Object "
         "{ \"$($_.Offset) $($_.PartitionNumber) $($_.DriveLetter)\" }"],
        capture_output=True, text=True, errors="replace")
    harita = {}
    for satir in r.stdout.splitlines():
        parca = satir.split()
        if len(parca) >= 2 and parca[0].isdigit():
            harf = parca[2] if len(parca) > 2 and parca[2].isalpha() else ""
            harita[int(parca[0])] = (int(parca[1]), harf)
    return harita


def main() -> int:
    if not sys.platform.startswith("win"):
        print("ATLANDI: yalnizca Windows")
        return 0
    dev.require_ci()
    is_dizini = tempfile.mkdtemp(prefix="du_harf_")
    yol, kaldir = dev.create(300, is_dizini)
    hatalar = []
    try:
        disk_no = int("".join(ch for ch in yol if ch.isdigit()))
        info = ph.find_disk(yol)
        dev.check_safe(info)
        s = DiskSession.open_physical(info, readonly=False, confirm=True)
        kuyruk = ops.OperationQueue()
        kuyruk.add(ops.create_table_op("mbr"))
        kuyruk.add(ops.create_op(2048, 60 * 2048, 512, fs_key="fat32", label="BIRINCIL"))
        kuyruk.add(ops.create_op(62 * 2048, 200 * 2048, 512, extended=True))
        sonuc = kuyruk.apply(s)
        assert sonuc.ok, sonuc.summary()
        s.reload()
        ic = [r for r in s.free_regions() if 62 * 2048 <= r.start_lba < 262 * 2048][0]
        kuyruk = ops.OperationQueue()
        kuyruk.add(ops.create_op(ic.start_lba, 60 * 2048, 512, fs_key="fat32",
                                 label="MANTIK1", logical=True))
        sonuc = kuyruk.apply(s)
        assert sonuc.ok, sonuc.summary()
        s.reload()
        ic = [r for r in s.free_regions() if 62 * 2048 <= r.start_lba < 262 * 2048][0]
        kuyruk = ops.OperationQueue()
        kuyruk.add(ops.create_op(ic.start_lba, 60 * 2048, 512, fs_key="fat32",
                                 label="MANTIK2", logical=True))
        sonuc = kuyruk.apply(s)
        assert sonuc.ok, sonuc.summary()
        s.reload()
        bolumler = [(p.index, p.start_lba * 512, p.logical)
                    for p in s.partitions if p.type_id not in MBR_EXTENDED_TYPES]
        s.close()
        time.sleep(4)                     # Windows birimleri baglasin

        harita = windows_harita(disk_no)
        print("bizim numara -> ofset -> Windows numarasi, harf")
        farkli = False
        for index, ofset, mantiksal in bolumler:
            w = harita.get(ofset)
            print(f"  {index} {'mantiksal' if mantiksal else 'birincil '} "
                  f"@{ofset}: Windows {w}")
            if w and w[0] != index:
                farkli = True
        print("Windows numaralari farkli:", farkli)

        info = ph.find_disk(yol)
        for index, ofset, _ in bolumler:
            ok, mesaj = ph.unmount_partition(info, index, offset=ofset)
            if not ok:
                hatalar.append(f"harf kaldirilamadi #{index}: {mesaj}")
        harita = windows_harita(disk_no)
        harfli = {o: h for o, (_, h) in harita.items() if h}
        if harfli:
            hatalar.append(f"kaldirmadan sonra harf kaldi: {harfli}")

        hedef_index, hedef_ofset, _ = [b for b in bolumler if b[2]][-1]
        ok, nokta = ph.mount_partition(info, hedef_index, offset=hedef_ofset)
        print(f"ikinci mantiksal (#{hedef_index}) harf atandi: {ok} {nokta}")
        harita = windows_harita(disk_no)
        harfli = {o: h for o, (_, h) in harita.items() if h}
        if not ok or list(harfli) != [hedef_ofset]:
            hatalar.append(f"harf yanlis bolumde: beklenen ofset {hedef_ofset}, "
                           f"Windows {harfli}")
        elif nokta.rstrip(":\\") != harfli[hedef_ofset]:
            hatalar.append(f"donen harf {nokta} Windows'takiyle ayni degil "
                           f"({harfli[hedef_ofset]})")
        if ph.partition_mount_point(info, hedef_index, hedef_ofset) != nokta:
            hatalar.append("sorgu atanan harfi gostermiyor")

        ok, mesaj = ph.unmount_partition(info, hedef_index, offset=hedef_ofset)
        harita = windows_harita(disk_no)
        harfli = {o: h for o, (_, h) in harita.items() if h}
        print(f"harf kaldirildi: {ok} {mesaj} -> harfli bolumler {harfli}")
        if not ok or harfli:
            hatalar.append(f"kaldirma: {ok} {mesaj} {harfli}")
    finally:
        kaldir()
    for h in hatalar:
        print("HATA:", h)
    print("SONUC:", "BASARILI" if not hatalar else "BASARISIZ")
    return 1 if hatalar else 0


if __name__ == "__main__":
    sys.exit(main())
