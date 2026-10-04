"""Arayuzun isletim sisteminin kendi pencere sistemiyle acilmasi (Asama 4).

    python -m tests.long.gui --rapor rapor/arayuz

macOS'ta Cocoa, Windows'ta "windows" Qt eklentisi (offscreen/minimal DEGIL):
uygulama kaynaktan, bir ornek goruntuyle acilir, ekran goruntusu alinir,
gunlukte gercek Qt platformu ve surum satiri aranir. Sonuc uzun testlerle
ayni JSON bicimindedir (ozet tablosunda gorunur). ADR 0080: macOS'tan
"deneysel" etiketinin kalkma olcutlerinden biri.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import platform as _platform
import subprocess
import sys
import time

from .engine import ROOT, peak_rss_mb, save
from .data import MIB

EXPECT = {"darwin": "cocoa", "win32": "windows"}


def sample_image(path: str) -> None:
    from diskultimate.core.session import DiskSession
    s = DiskSession.create(path, 256 * MIB, scheme="gpt", overwrite=True)
    r = s.free_regions()[0]
    p = s.create_partition(r.start_lba, 120 * 2048, fs_key="fat32", label="ARAYUZ",
                           name="Arayuz testi")
    fs = s.filesystem(p.index)
    fs.mkdir("/Belgeler")
    fs.write_file("/Belgeler/okubeni.txt", b"DiskUltimate arayuz testi\n")
    fs.flush()
    s.close()


def screenshot(out: str) -> str:
    if sys.platform == "darwin":
        r = subprocess.run(["screencapture", "-x", out], capture_output=True, text=True)
        return "" if r.returncode == 0 else r.stderr
    if sys.platform.startswith("win"):
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
            "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
            "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
            "$g=[System.Drawing.Graphics]::FromImage($bmp);"
            "$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size);"
            f"$bmp.Save('{out}')")
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True)
        return "" if r.returncode == 0 else r.stderr
    return "bu platformda ekran goruntusu yok"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tests.long.gui")
    ap.add_argument("--rapor", default=os.path.join(ROOT, ".tmp", "uzun", "rapor"))
    ap.add_argument("--bekle", type=int, default=20)
    a = ap.parse_args(argv)
    work = os.path.join(ROOT, ".tmp", "uzun", "arayuz")
    os.makedirs(work, exist_ok=True)
    image = os.path.join(work, "ornek.img")
    png = os.path.join(a.rapor, f"arayuz-{sys.platform}.png")
    os.makedirs(a.rapor, exist_ok=True)
    steps = []
    started = time.time()

    def step(name, ok, **kw):
        rec = {"ad": name, "durum": "tamam" if ok else "hata", "sn": 0,
               "bellek_mb": round(peak_rss_mb(), 1)}
        rec.update(kw)
        steps.append(rec)
        print(f"  {name:<16} {rec['durum']}  {kw.get('not') or kw.get('hata') or ''}")
        return ok

    sample_image(image)
    env = dict(os.environ, DISKULTIMATE_NO_ELEVATION_PROMPT="1")
    env.pop("QT_QPA_PLATFORM", None)
    t0 = time.time()
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "main.py"), "--no-root",
                             image], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            **({"start_new_session": True} if os.name != "nt" else {}))
    time.sleep(a.bekle)
    alive = proc.poll() is None
    step("acilis", alive, **({} if alive else {"hata": f"uygulama kapandi (cikis {proc.returncode})"}))
    err = screenshot(png) if alive else "uygulama acik degil"
    size = os.path.getsize(png) if os.path.exists(png) else 0
    step("ekran_goruntusu", not err and size > 20_000,
         **({"not": f"{size // 1024} KB: {os.path.basename(png)}"} if not err and size > 20_000
            else {"hata": err or f"goruntu cok kucuk ({size} bayt) — bos ekran?"}))
    from tools.smoke_launch import stop_tree
    stop_tree(proc)
    logs = [p for p in glob.glob(os.path.join(ROOT, ".claude", "logs", "runtime",
                                              "session-*.log"))
            if os.path.getmtime(p) >= t0 - 1]
    text = open(max(logs, key=os.path.getmtime), encoding="utf-8",
                errors="replace").read() if logs else ""
    from diskultimate.ui.main_window import APP_VERSION
    step("surum", f"{APP_VERSION} baslatildi" in text,
         **({"not": APP_VERSION} if f"{APP_VERSION} baslatildi" in text
            else {"hata": "gunlukte surum satiri yok"}))
    want = EXPECT.get(sys.platform, "")
    plat = [l for l in text.splitlines() if "Qt platformu:" in l]
    got = plat[-1].split("Qt platformu:")[1].split("|")[0].strip() if plat else ""
    step("qt_platformu", bool(want) and got == want,
         **({"not": got} if got == want else {"hata": f"beklenen {want}, gunlukte '{got}'"}))
    step("hata_izi_yok", "Traceback" not in text,
         **({} if "Traceback" not in text else {"hata": text[text.index("Traceback"):][:800]}))
    ok = all(s["durum"] == "tamam" for s in steps)
    result = {"senaryo": f"arayuz-{want or sys.platform}", "fs": "-", "tablo": "-",
              "profil": "arayuz", "tohum": 0,
              "platform": f"{_platform.system()} {_platform.release()} "
                          f"{_platform.machine()} py{_platform.python_version()}",
              "basladi": time.strftime("%Y-%m-%d %H:%M:%S"),
              "sure_sn": round(time.time() - started, 1), "dosya": 0, "veri_mb": 0,
              "tamam": ok, "adimlar": steps, "tekrar": "python -m tests.long.gui"}
    save(result, os.path.join(a.rapor, f"arayuz-{sys.platform}.json"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
