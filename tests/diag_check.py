"""Tanilama altyapisinin denetimi.

Calistirma:  python3 -m tests.diag_check

Dogrulananlar:
1. Oturum gunlugu olusur ve `span()` sureyi yazar; esigi asan islem YAVAS
   olarak isaretlenir.
2. Donma yakalayici, nabiz kesildiginde rapor uretir; raporda arayuz is
   parcaciginin yigini ve **o an acik islem** bulunur.
3. Nabiz geri geldiginde rapor kapanir ve geri cagirma tetiklenir.
4. Yigin dokumu elle de alinabilir.
5. Tanilama `DISKULTIMATE_DIAG=0` ile kapatildiginda `span()` maliyetsizdir.
"""
from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from diskultimate.core import diagnostics  # noqa: E402
from diskultimate.paths import scratch  # noqa: E402

RESULTS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    mark = "OK  " if ok else "HATA"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    # Gunlukler test calismasinda proje gunluk dizinini kirletmesin.
    os.environ["DISKULTIMATE_LOG_DIR"] = scratch("diag")

    print("=== Tanilama denetimi ===\n")
    log = diagnostics.configure()
    check("oturum gunlugu olustu", bool(log) and os.path.isfile(log), log)

    with diagnostics.span("test.fast"):
        pass
    with diagnostics.span("test.slow"):
        time.sleep(0.3)
    history = diagnostics.recent(5)
    check("span gecmise yazildi", any("test.slow" in h for h in history))

    with open(log, "r", encoding="utf-8") as fh:
        text = fh.read()
    check("yavas islem gunluge YAVAS olarak girdi", "YAVAS test.slow" in text)

    # track=False: gecmisi doldurmaz ama yavassa yine gorunur
    for _ in range(5):
        with diagnostics.span("test.quiet", track=False):
            pass
    check("track=False gecmisi doldurmadi",
          not any("test.quiet" in h for h in diagnostics.recent(10)))

    # -- donma yakalayici ---------------------------------------------------
    caught = []
    dog = diagnostics.start_watchdog(
        stall_ms=400, callback=lambda path, sec: caught.append((path, sec)))
    check("yakalayici calisiyor", dog is not None and dog.is_alive())

    stop = threading.Event()

    def heartbeat() -> None:
        while not stop.is_set():
            diagnostics.beat()
            time.sleep(0.05)

    pulse = threading.Thread(target=heartbeat, daemon=True)
    pulse.start()
    time.sleep(0.4)
    stop.set()
    pulse.join()

    # Nabiz kesik: bu sirada acik bir islem de var olsun ki raporda gorunsun
    with diagnostics.span("test.blocking", reason="donma"):
        time.sleep(1.4)
    # Nabiz geri geliyor: yakalayici raporu kapatana kadar atmaya devam et.
    # (Tek bir `beat()` atip beklemek ikinci bir donma raporu dogururdu.)
    stop.clear()
    pulse = threading.Thread(target=heartbeat, daemon=True)
    pulse.start()
    deadline = time.monotonic() + 3.0
    while not caught and time.monotonic() < deadline:
        time.sleep(0.05)
    stop.set()
    pulse.join()

    check("donma yakalandi", dog.stall_count >= 1,
          f"{dog.stall_count} kez, en uzun {dog.worst_ms / 1000.0:.1f} sn")
    check("geri cagirma tetiklendi", bool(caught))

    report = caught[0][0] if caught else diagnostics.last_report()
    check("rapor dosyasi yazildi", bool(report) and os.path.isfile(report), report)
    with open(report, "r", encoding="utf-8", errors="replace") as fh:
        body = fh.read()
    check("raporda acik islem var", "test.blocking" in body)
    check("raporda yigin var", "is parcacigi MainThread" in body)
    check("rapor kapanisi yazildi", "yaniti geri verdi" in body)

    dump = diagnostics.dump_now("test")
    check("elle yigin dokumu", os.path.isfile(dump))

    state = diagnostics.status()
    check("durum ozeti dolu", state["enabled"] and state["stalls"] >= 1)
    diagnostics.stop_watchdog()

    basarisiz = [r for r in RESULTS if not r[1]]
    print(f"\nSonuc: {len(RESULTS) - len(basarisiz)}/{len(RESULTS)} gecti")
    return 1 if basarisiz else 0


if __name__ == "__main__":
    sys.exit(main())
