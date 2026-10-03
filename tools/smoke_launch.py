"""Paketlenmis uygulamayi kisa sure acip gunlugunu denetler (GELISTIRME ARACI).

    python3 tools/smoke_launch.py <surum> <komut> [arguman...]

CI (`.github/workflows/release.yml`) her paketi bununla sinar: uygulama
ekransiz (`minimal`) kipte, yetki sormadan (`--no-root`) baslatilir,
birkac saniye sonra kapatilir; kullanicinin veri dizinindeki **en yeni**
oturum gunlugunde "<surum> baslatildi" satiri ve Python hata izi aranir.
Gunluk kullanicinin veri dizinindedir (paketlenmis kopya, ADR 0040/0050).

Cikis: 0 = acildi, 1 = gunluk yok / surum yok / hata izi var.
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from diskultimate.core.platform import user_data_dir  # noqa: E402

WAIT_SECONDS = 20


def newest_session_log(since: float) -> str:
    pattern = os.path.join(user_data_dir(), "logs", "runtime", "session-*.log")
    logs = [p for p in glob.glob(pattern) if os.path.getmtime(p) >= since - 1]
    return max(logs, key=os.path.getmtime) if logs else ""


def stop_tree(process: subprocess.Popen) -> None:
    """Baslatilan surecin butun agacini kapatir."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                       capture_output=True)
    else:
        import signal
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=10)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    version, command = sys.argv[1], sys.argv[2:]
    # "minimal": pakette her zaman bulunan ekransiz Qt eklentisi. "offscreen"
    # boyut icin PyInstaller paketinden atiliyor (DiskUltimate.spec).
    env = dict(os.environ, QT_QPA_PLATFORM="minimal",
               DISKULTIMATE_NO_ELEVATION_PROMPT="1")
    start = time.time()
    print(f"baslatiliyor: {' '.join(command)} --no-root")
    # Cikti boruya degil dosyaya: AppImage'in alt sureci boruyu acik tutar ve
    # okuyan taraf sonsuza kadar bekler (olculdu). Ayrica surec grubunun
    # TAMAMI kapatilir; yalnizca baslaticiyi oldurmek uygulamayi ortada birakir.
    out_path = os.path.join(ROOT, ".tmp", "smoke_launch.out")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as out:
        if os.name == "nt":
            process = subprocess.Popen(
                command + ["--no-root"], env=env, stdout=out,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            process = subprocess.Popen(command + ["--no-root"], env=env,
                                       stdout=out, stderr=subprocess.STDOUT,
                                       start_new_session=True)
        try:
            process.wait(timeout=WAIT_SECONDS)
            print(f"uygulama erken kapandi (cikis {process.returncode})")
        except subprocess.TimeoutExpired:
            stop_tree(process)
    text = open(out_path, encoding="utf-8", errors="replace").read()
    if text.strip():
        print("--- cikti ---\n" + text[-3000:])

    log = newest_session_log(start)
    if not log:
        print(f"HATA: oturum gunlugu yok ({user_data_dir()})")
        return 1
    content = open(log, encoding="utf-8", errors="replace").read()
    print(f"--- {log} ---\n" + content[-3000:])
    if f"{version} baslatildi" not in content:
        print(f"HATA: gunlukte '{version} baslatildi' yok")
        return 1
    if "Traceback" in content or "Traceback" in text:
        print("HATA: Python hata izi var")
        return 1
    print("TAMAM: uygulama acildi")
    return 0


if __name__ == "__main__":
    sys.exit(main())
