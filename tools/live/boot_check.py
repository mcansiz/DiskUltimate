"""Live ISO'yu QEMU'da acip uygulamanin geldigini denetler (GELISTIRME ARACI).

    python3 tools/live/boot_check.py <iso> --mode bios|uefi|uefi-sb \
        [--timeout 420] [--out .tmp/live/check]

Yalnizca CI'da / gecici makinede kosar (ADR 0080, 0101): ISO'ya bos bir
sanal disk eklenir, misafir fiziksel diske yazmaz, ana makinenin disklerini
gormez.

Isaretler misafirin oturum betiginden seri porta yazilir
(base/config/includes.chroot/usr/local/lib/diskultimate/):
    DU-LIVE: SESSION-START fw=<bios|uefi> sb=<0|1>   X oturumu basladi
    DU-LIVE: APP-WINDOW <surum>                       uygulama penceresi gorundu
    DU-LIVE: APP-TIMEOUT | APP-MISSING                basarisiz

Basari: APP-WINDOW geldi ve bellenim kipe uyuyor (uefi-sb icin sb=1).
Her durumda ekran goruntusu (<out>/<kip>.png) ve seri gunluk birakilir.
Cikis: 0 = basarili, 1 = basarisiz, 2 = kullanim / arac yok.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Ubuntu 24.04 `ovmf` paketi (CI kosucusu).
OVMF = {
    "uefi": ("/usr/share/OVMF/OVMF_CODE_4M.fd", "/usr/share/OVMF/OVMF_VARS_4M.fd"),
    "uefi-sb": ("/usr/share/OVMF/OVMF_CODE_4M.secboot.fd",
                "/usr/share/OVMF/OVMF_VARS_4M.ms.fd"),
}
MARK = re.compile(r"DU-LIVE: (\S+)(.*)")


def qmp(sock_path: str, *commands: dict) -> None:
    """QMP ile komut gonderir (ekran goruntusu, kapatma). Hata yutulur."""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(15)
        s.connect(sock_path)
        f = s.makefile("rw")
        f.readline()
        for cmd in ({"execute": "qmp_capabilities"},) + commands:
            f.write(json.dumps(cmd) + "\n")
            f.flush()
            while True:
                reply = json.loads(f.readline())
                if "return" in reply or "error" in reply:
                    if "error" in reply:
                        print(f"QMP {cmd['execute']}: {reply['error']}")
                    break
        s.close()
    except (OSError, ValueError) as exc:
        print(f"QMP erisilemedi: {exc}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("iso")
    ap.add_argument("--mode", choices=("bios", "uefi", "uefi-sb"), required=True)
    ap.add_argument("--timeout", type=int, default=420)
    ap.add_argument("--out", default=os.path.join(ROOT, ".tmp", "live", "check"))
    a = ap.parse_args()

    qemu = shutil.which("qemu-system-x86_64")
    if not qemu:
        print("HATA: qemu-system-x86_64 yok")
        return 2
    os.makedirs(a.out, exist_ok=True)
    serial = os.path.join(a.out, f"{a.mode}-serial.log")
    shot = os.path.join(a.out, f"{a.mode}.png")
    sock = os.path.join(a.out, f"{a.mode}.qmp")
    disk = os.path.join(a.out, f"{a.mode}-blank.img")
    for p in (serial, shot, sock):
        if os.path.exists(p):
            os.remove(p)
    with open(disk, "wb") as fh:            # bos 8 GB seyrek ikinci disk
        fh.truncate(8 << 30)

    kvm = os.access("/dev/kvm", os.R_OK | os.W_OK)
    machine = "q35,smm=on" if a.mode == "uefi-sb" else "q35"
    cmd = [qemu, "-machine", machine + (",accel=kvm" if kvm else ""),
           "-m", "2048", "-smp", "2",
           "-cdrom", a.iso, "-boot", "d",
           "-drive", f"file={disk},format=raw,if=virtio",
           "-vga", "std", "-display", "none",
           "-serial", f"file:{serial}",
           "-qmp", f"unix:{sock},server,nowait",
           "-no-reboot"]
    if kvm:
        cmd += ["-cpu", "host"]
    if a.mode != "bios":
        code, vars_src = OVMF[a.mode]
        if not os.path.isfile(code):
            print(f"HATA: OVMF yok: {code}")
            return 2
        vars_copy = os.path.join(a.out, f"{a.mode}-vars.fd")
        shutil.copyfile(vars_src, vars_copy)
        if a.mode == "uefi-sb":
            cmd += ["-global", "driver=cfi.pflash01,property=secure,value=on"]
        cmd += ["-drive", f"if=pflash,format=raw,readonly=on,file={code}",
                "-drive", f"if=pflash,format=raw,file={vars_copy}"]

    print(f"[{a.mode}] KVM={'var' if kvm else 'YOK (yavas)'}\n+ " + " ".join(cmd),
          flush=True)
    proc = subprocess.Popen(cmd)
    start = time.time()
    marks: dict = {}
    seen = 0
    try:
        while time.time() - start < a.timeout and proc.poll() is None:
            time.sleep(2)
            if not os.path.exists(serial):
                continue
            with open(serial, "rb") as fh:
                text = fh.read().decode("utf-8", "replace")
            for line in text.splitlines()[seen:]:
                m = MARK.search(line)
                if m:
                    marks[m.group(1)] = m.group(2).strip()
                    print(f"[{a.mode}] {time.time() - start:5.0f} s  {line.strip()}",
                          flush=True)
            seen = len(text.splitlines())
            if {"APP-WINDOW", "APP-TIMEOUT", "APP-MISSING"} & marks.keys():
                break
        qmp(sock, {"execute": "screendump",
                   "arguments": {"filename": shot, "format": "png"}})
        time.sleep(1)
    finally:
        qmp(sock, {"execute": "quit"})
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
        if os.path.exists(disk):
            os.remove(disk)

    elapsed = time.time() - start
    ok = "APP-WINDOW" in marks
    session = marks.get("SESSION-START", "")
    want_fw = "fw=bios" if a.mode == "bios" else "fw=uefi"
    if ok and want_fw not in session:
        print(f"[{a.mode}] HATA: bellenim kipe uymuyor: '{session}'")
        ok = False
    if ok and a.mode == "uefi-sb" and "sb=1" not in session:
        print(f"[{a.mode}] HATA: Secure Boot acik degil: '{session}'")
        ok = False
    if not marks:
        print(f"[{a.mode}] HATA: seri portta hic isaret yok "
              f"({elapsed:.0f} s; GRUB/cekirdek/X asamasinda kalmis olabilir)")
    print(f"[{a.mode}] {'TAMAM' if ok else 'BASARISIZ'} — {elapsed:.0f} s, "
          f"ekran: {shot if os.path.exists(shot) else 'yok'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
