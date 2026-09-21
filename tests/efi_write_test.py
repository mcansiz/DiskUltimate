"""UEFI bellenimine YAZMA testi — yalnizca atilabilir bir misafirde.

Bu betik makinenin **kalici bellenim degiskenlerini degistirir**. Onyukleme
degiskenleri diskte degil anakart uzerindedir; yanlis bir duzen makineyi
acilmaz birakabilir ve o noktada duzeltmek icin gereken sey (calisan bir
isletim sistemi) artik yoktur. Bu yuzden `tests.run_all` icine **alinmadi**;
`tests/physical_write_test.py` gibi ayri ve acik onayla calisir.

Kullanim:
    sudo python3 -m tests.efi_write_test --onayla

Calismasi icin gerekenler:
  1. `--onayla` bayragi
  2. Makine UEFI kipinde acilmis olmali (`/sys/firmware/efi`)
  3. Bellenim degiskenleri **yazilabilir** olmali (root)

## Neden guvenli

Her degisiklik **geri alinir** ve her adim bagimsiz bir araca
(`efibootmgr`) karsi dogrulanir. Yazilan uc sey de onyukleme girislerinin
**kendisi degildir**:

- `Timeout` — menu bekleme suresi, acilisi etkilemez
- `BootOrder` — sira; ayni turda eski haline dondurulur
- `BootNext` — tek seferlik secim; yazilir ve **silinir** (silme yolunu olcer)

Girislerin (`Boot0000`...) baytlarina dokunulmaz; test sonunda bunlarin
bayt bayt korundugu ayrica dogrulanir.

## Bozulursa

VMware misafirinde: ana makinede `mint.nvram` silinir, VMware varsayilan
degiskenleri yeniden uretir ve misafir `\\EFI\\BOOT\\BOOTX64.EFI` yedek
yolundan acilir. Gercek donanimda: bellenim kurulumundan onyukleme sirasi
elle duzeltilir. Her iki durumda da test basinda alinan JSON yedegi
(`efistore.load_backup`) duzeni geri koymak icin yeterlidir.
"""
from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from diskultimate.core import efistore, platform  # noqa: E402
from diskultimate.paths import scratch  # noqa: E402

STEPS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    STEPS.append((name, ok, detail))
    print(f"  [{'OK  ' if ok else 'HATA'}] {name}"
          + (f" — {detail}" if detail else ""), flush=True)


def efibootmgr() -> str:
    """Bagimsiz dogrulayici: sistemin kendi araci ne diyor?

    Kendi ciktimizi kendi kodumuzla dogrulamak bir sey kanitlamaz; olcut
    baska bir programin ayni degiskenleri ayni gormesidir.
    """
    try:
        result = subprocess.run(["efibootmgr", "-v"], capture_output=True,
                                text=True, timeout=30)
        return result.stdout
    except Exception as exc:                        # noqa: BLE001
        return f"(efibootmgr calistirilamadi: {exc})"


def line_of(text: str, key: str) -> str:
    for line in text.splitlines():
        if line.startswith(key):
            return line.strip()
    return ""


def main(argv) -> int:
    if "--onayla" not in argv:
        print(__doc__)
        print("Onay bayragi verilmedi; hicbir sey yazilmadi.")
        return 2

    print("=== UEFI bellenimine yazma olcumu ===\n", flush=True)
    readable, writable, reason = platform.efivars_state()
    print(f"bellenim : {platform.firmware_type()}")
    print(f"okunur   : {readable}")
    print(f"yazilir  : {writable}" + (f"  ({reason})" if reason else ""))
    print(flush=True)
    if not writable:
        print("YAZILAMIYOR — root olarak ve UEFI kipinde calistirin.")
        return 2

    # -- 1. oku, yedekle, bagimsiz dogrula ---------------------------------
    start = efistore.load()
    backup_path = os.path.join(scratch("efi"), "uefi-yedek.json")
    efistore.save_backup(start, backup_path)
    check("duzen okundu ve yedeklendi",
          bool(start.boot_entries) and os.path.isfile(backup_path),
          f"{len(start.boot_entries)} giris -> {backup_path}")

    reference = efibootmgr()
    start_order = line_of(reference, "BootOrder:")
    ours = "BootOrder: " + ",".join(f"{n:04X}" for n in start.boot_order)
    check("BootOrder efibootmgr ile ayni", ours == start_order,
          f"{ours!r} vs {start_order!r}")

    missing = [o.name for o in start.ordered("Boot") if o.name not in reference]
    check("butun girisler efibootmgr ciktisinda var", not missing, str(missing))

    for option in start.ordered("Boot"):
        # `efibootmgr -v` aciklamayi giris adindan sonra yazar; ikisinin ayni
        # satirda bulunmasi cozumlemenin dogrulugunu gosterir.
        row = line_of(reference, option.name)
        if option.description and row:
            check(f"{option.name} aciklamasi ayni",
                  option.description in row, f"{option.description!r}")

    # -- 2. Timeout (acilisi etkilemeyen en zararsiz degisken) -------------
    original_timeout = start.timeout
    wanted = 7 if original_timeout != 7 else 3
    edited = efistore.load()
    edited.timeout = wanted
    plan = efistore.changes(start, edited)
    check("Timeout plani yalnizca Timeout icerir",
          [c.name for c in plan] == ["Timeout"], str([c.name for c in plan]))
    report = efistore.apply(plan)
    check("Timeout yazildi", report.ok, report.summary())

    reread = efistore.load()
    check("Timeout geri okundu", reread.timeout == wanted,
          f"{reread.timeout} (beklenen {wanted})")
    check("Timeout efibootmgr ile dogrulandi",
          str(wanted) in line_of(efibootmgr(), "Timeout:"),
          line_of(efibootmgr(), "Timeout:"))

    restored = efistore.load()
    # None **oldugu gibi** geri konur; 0 yazmak farkli bir durumdur ve
    # duzeni degistirir (bkz. `efistore.changes`, Timeout silme dali).
    restored.timeout = original_timeout
    report = efistore.apply(efistore.changes(reread, restored))
    check("Timeout geri alindi", report.ok, report.summary())
    check("Timeout baslangictaki degerinde",
          efistore.load().timeout == original_timeout,
          f"{efistore.load().timeout} (beklenen {original_timeout})")

    # -- 3. BootOrder ------------------------------------------------------
    current = efistore.load()
    if len(current.boot_order) >= 2:
        swapped = list(current.boot_order)
        swapped[0], swapped[1] = swapped[1], swapped[0]
        edited = efistore.load()
        edited.boot_order = swapped
        plan = efistore.changes(current, edited)
        check("BootOrder degisikligi yikici isaretli",
              bool(plan) and plan[0].destructive,
              str([(c.name, c.destructive) for c in plan]))
        report = efistore.apply(plan)
        check("BootOrder yazildi", report.ok, report.summary())

        reread = efistore.load()
        check("BootOrder geri okundu", reread.boot_order == swapped,
              str([f"{n:04X}" for n in reread.boot_order]))
        expected = "BootOrder: " + ",".join(f"{n:04X}" for n in swapped)
        check("BootOrder efibootmgr ile dogrulandi",
              line_of(efibootmgr(), "BootOrder:") == expected,
              line_of(efibootmgr(), "BootOrder:"))

        # Geri alma burada kritiktir: makine bu siradan aciliyor.
        restored = efistore.load()
        restored.boot_order = list(current.boot_order)
        report = efistore.apply(efistore.changes(reread, restored))
        check("BootOrder geri alindi", report.ok, report.summary())
    else:
        check("BootOrder denemesi atlandi", True, "iki giristen az")

    # -- 4. BootNext: yazma VE silme yolu ---------------------------------
    current = efistore.load()
    if current.boot_order:
        pick = current.boot_order[-1]
        edited = efistore.load()
        edited.boot_next = pick
        report = efistore.apply(efistore.changes(current, edited))
        check("BootNext yazildi", report.ok, report.summary())
        reread = efistore.load()
        check("BootNext geri okundu", reread.boot_next == pick,
              str(reread.boot_next))
        check("BootNext efibootmgr ile dogrulandi",
              f"{pick:04X}" in line_of(efibootmgr(), "BootNext:"),
              line_of(efibootmgr(), "BootNext:"))

        cleared = efistore.load()
        cleared.boot_next = None
        plan = efistore.changes(reread, cleared)
        check("BootNext icin SILME plani uretildi",
              bool(plan) and plan[0].action == "delete",
              str([(c.name, c.action) for c in plan]))
        report = efistore.apply(plan)
        check("BootNext silindi", report.ok, report.summary())
        check("BootNext gercekten yok", efistore.load().boot_next is None)

    # -- 5. baslangictaki duruma donuldu mu -------------------------------
    end = efistore.load()
    difference = efistore.changes(start, end)
    check("duzen baslangictaki haliyle AYNI", not difference,
          str([(c.name, c.action) for c in difference]))

    damaged = [n for n, option in start.boot_entries.items()
               if n not in end.boot_entries
               or end.boot_entries[n].to_bytes() != option.to_bytes()]
    check("onyukleme girisleri bayt bayt korundu", not damaged, str(damaged))
    check("efibootmgr baslangictaki sirayi gosteriyor",
          line_of(efibootmgr(), "BootOrder:") == start_order,
          f"{line_of(efibootmgr(), 'BootOrder:')!r} vs {start_order!r}")

    failed = [s for s in STEPS if not s[1]]
    print(f"\nSonuc: {len(STEPS) - len(failed)}/{len(STEPS)} gecti", flush=True)
    for name, _ok, detail in failed:
        print(f"  ! {name}: {detail}", flush=True)
    if failed:
        print(f"\nDuzeni geri koymak icin yedek: {backup_path}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
