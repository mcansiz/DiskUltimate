"""Gozlemci arayuz gezintisi (ADR 0097): uygulamayi bir kullanici gibi surer.

    python -m tests.long.explore --rapor rapor/gezinti          # goruntu dosyasi
    python -m tests.long.explore --aygit --rapor rapor/gezinti  # gercek aygit (CI / test VM)

Neden: betik testleri tek bir cagrinin ciktisina bakar; `ui_smoke` disk
yoklamasini durdurur. 2026-10-14'te bulunan hata (acik diskin surucu harfleri
3 sn'de bir gidip geliyor, gezgin en basa donuyordu) ancak **gercek uygulama,
gercek disk ve zaman** birlikteyken gorunuyordu. Bu test:

* uygulamayi `main.start()` ile **ayni acilis yolundan** kurar, gercek Qt
  platformuyla (Windows'ta "windows") acar; disk yoklamasi calisir;
* agaca tiklar, gezginde gezer, kuyruga islem ekler, Uygula'ya basar —
  modal pencereler gercek `exec_` dongusunde acilir, surucu onlari doldurur;
* her adimdan sonra uygulamayi **bosta** bekletir ve "hicbir sey olmuyorken
  degismemesi gerekenleri" izler: agac yeniden kurulmasi, gorunum tazelenmesi,
  gezginin sifirlanmasi, gunluk satiri, secim/kaydirma, surucu harfleri,
  Windows birim bildirimleri; ayrica yakalanmamis istisna ve donma raporu.

Aygit kipi gercek bir disk (`tests.long.device`, takili VHD) kullanir ve
YALNIZCA GitHub Actions makinesinde ya da test VM'inde (`DU_TEST_VM=1`)
calisir; yikici adimlar yalnizca o aygita uygulanir (CLAUDE.md test kurali).
"""
from __future__ import annotations

import argparse
import glob
import os
import platform as _platform
import subprocess
import sys
import time
import traceback
from typing import Callable, Dict, List, Optional, Tuple

from .engine import ROOT, peak_rss_mb, save
from .data import MIB

os.environ.setdefault("DISKULTIMATE_NO_ELEVATION_PROMPT", "1")
os.environ.setdefault("DISKULTIMATE_UPDATE_CHECK", "0")
sys.path.insert(0, ROOT)

IS_WINDOWS = sys.platform.startswith("win")
IDLE_SECONDS = 12
DATA_LABEL = "DUVERI"
TARGET_LABEL = "DUFAT"
NEW_LABEL = "DUGUI"
FILE_COUNT = 240


class StepSkip(Exception):
    pass


class StepFail(Exception):
    pass


# --------------------------------------------------------------------------
# Olay dongusu
# --------------------------------------------------------------------------
def wait(seconds: float, until: Optional[Callable[[], bool]] = None) -> bool:
    """Gercek olay dongusunu `seconds` boyunca (ya da kosul tutana kadar) doner."""
    from PyQt5.QtCore import QEventLoop, QTimer

    end = time.monotonic() + seconds
    loop = QEventLoop()
    timer = QTimer()
    timer.setInterval(50)

    def tick():
        if (until is not None and until()) or time.monotonic() >= end:
            loop.quit()

    timer.timeout.connect(tick)
    timer.start()
    loop.exec_()
    timer.stop()
    return bool(until is not None and until())


def off_ui(fn, *args, timeout: float = 600):
    """Isletim sistemi aracini arka planda calistirir; olay dongusu doner.

    Arayuz parcaciginda `subprocess.run` cagirmak uygulamanin donma
    yakalayicisini tetikliyordu — test kendi urettigi donmayi bulgu
    sayiyordu (win10, 2026-10-14).
    """
    import threading

    box = {}

    def target():
        try:
            box["value"] = fn(*args)
        except BaseException as exc:                 # noqa: BLE001
            box["error"] = exc

    thread = threading.Thread(target=target, name="gezinti-arac", daemon=True)
    thread.start()
    if not wait(timeout, lambda: not thread.is_alive()):
        raise StepFail(f"arac {timeout:.0f} sn'de bitmedi")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def click_item(tree, item, double: bool = False) -> None:
    """Agac ogesine fareyle tiklar (kullanicinin yaptigi gibi)."""
    from PyQt5.QtCore import Qt
    from PyQt5.QtTest import QTest

    tree.scrollToItem(item)
    wait(0.1)
    pos = tree.visualItemRect(item).center()
    QTest.mouseClick(tree.viewport(), Qt.LeftButton, Qt.NoModifier, pos)
    if double:
        # Gercek cift tiklama bas-birak-cift tik-birak dizisidir. Qt5'te
        # `mouseDClick` yalnizca cift tik olayini gonderir; once basilan oge
        # olmadigi icin oge gorunumu sinyali yutuyordu (win10, 2026-10-14).
        QTest.mouseDClick(tree.viewport(), Qt.LeftButton, Qt.NoModifier, pos)
    wait(0.3)


# --------------------------------------------------------------------------
# Modal pencere surucusu
# --------------------------------------------------------------------------
class ModalDriver:
    """Acilan modal pencereyi beklenen islemle karsilar.

    Beklenmeyen pencere bir **bulgu**dur: goruntusu alinir, metni yazilir ve
    reddedilerek kapatilir (kullanici "Vazgec"e basmis gibi).
    """

    def __init__(self, shots):
        from PyQt5.QtCore import QTimer

        self.shots = shots
        self.handlers: List[Tuple[str, Callable, Callable]] = []
        self.handled: List[str] = []
        self.unexpected: List[str] = []
        self.errors: List[str] = []
        self.timer = QTimer()
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def expect(self, name: str, match: Callable, act: Callable) -> None:
        self.handlers.append((name, match, act))

    def clear(self) -> None:
        self.handlers = []

    @staticmethod
    def describe(widget) -> str:
        from PyQt5.QtWidgets import QMessageBox

        text = widget.windowTitle()
        if isinstance(widget, QMessageBox):
            text += " | " + widget.text().replace("<br>", " ")
        return f"{type(widget).__name__}: {text}"[:300]

    def _run(self, name: str, act: Callable, widget) -> None:
        try:
            act(widget)
        except Exception as exc:                     # noqa: BLE001
            self.errors.append(f"{name}: {type(exc).__name__}: {exc}")

    def _tick(self) -> None:
        from PyQt5.QtCore import QTimer
        from PyQt5.QtWidgets import QApplication, QDialog, QMessageBox

        widget = QApplication.activeModalWidget()
        # Isaret pencerenin kendisinde durur: `id()` ile tutulunca kapanan
        # pencerenin numarasi yeni pencereye verildi, Uygula penceresi
        # "zaten islendi" sanilip sonsuza dek acik kaldi (2026-10-14).
        if widget is None or widget.property("du_driven") or not widget.isVisible():
            return
        widget.setProperty("du_driven", True)
        for entry in list(self.handlers):
            name, match, act = entry
            if match(widget):
                self.handlers.remove(entry)
                self.handled.append(name)
                self.shots(f"pencere-{name}", widget)
                # Islem AYRI bir zamanlayicida calisir: bu yuvanin icinde
                # calissaydi, islemin actigi ic ice pencereyi (Uygula ->
                # "bagli bolum" sorusu) kimse karsilamazdi — Qt bir
                # zamanlayiciyi kendi yuvasi bitmeden yeniden tetiklemez
                # (win10, 2026-10-14).
                QTimer.singleShot(0, lambda: self._run(name, act, widget))
                return
        self.unexpected.append(self.describe(widget))
        self.shots("beklenmeyen-pencere", widget)
        if isinstance(widget, QMessageBox) and widget.escapeButton() is not None:
            widget.escapeButton().click()
        elif isinstance(widget, QDialog):
            widget.reject()
        else:
            widget.close()


def is_question(widget) -> bool:
    from PyQt5.QtWidgets import QMessageBox

    return (isinstance(widget, QMessageBox)
            and widget.button(QMessageBox.Yes) is not None)


def answer_yes(widget) -> None:
    from PyQt5.QtWidgets import QMessageBox

    widget.button(QMessageBox.Yes).click()


# --------------------------------------------------------------------------
# Gozlemci
# --------------------------------------------------------------------------
IDLE_EVENTS = {
    "agac": "agac yeniden kuruldu",
    "tazele": "gorunum tazelendi",
    "gezgin": "dosya gezgini sifirlandi",
    "gezinti": "dosya gezgini klasor degistirdi",
    "gunluk": "gunluge yazildi",
    "harf": "Windows surucu harfleri degisti",
    "aygit": "Windows birim bildirimi geldi",
}


class Observer:
    """Uygulamanin "kendiliginden" yaptigi her seyi zaman damgasiyla yazar."""

    def __init__(self, app, window):
        self.app = app
        self.window = window
        self.events: List[Tuple[float, str, str]] = []
        self.qt_messages: List[str] = []
        wrap = self._wrap
        wrap(window, "_build_tree", "agac")
        wrap(window, "refresh", "tazele")
        wrap(window, "_sync_open_mounts", "tarama")
        wrap(window, "log", "gunluk", lambda a, k: str(a[0]) if a else "")
        wrap(window.browser, "set_filesystem", "gezgin",
             lambda a, k: str(a[1]) if len(a) > 1 else "")
        wrap(window.browser, "navigate", "gezinti",
             lambda a, k: str(a[0]) if a else "")
        self._hook_exceptions()
        self._hook_qt_messages()
        from diskultimate.core import diagnostics
        self.freeze_dir = diagnostics.report_dir()
        self.freeze_before = set(glob.glob(os.path.join(self.freeze_dir, "freeze-*")))
        self._mask = None
        self._native = None
        if IS_WINDOWS:
            self._start_windows_watch()

    def _wrap(self, obj, name: str, kind: str, detail=None) -> None:
        original = getattr(obj, name)
        events = self.events

        def wrapper(*args, **kwargs):
            events.append((time.monotonic(), kind,
                           detail(args, kwargs) if detail else ""))
            return original(*args, **kwargs)

        setattr(obj, name, wrapper)

    def _hook_exceptions(self) -> None:
        previous = sys.excepthook

        def hook(kind, value, trace):
            text = "".join(traceback.format_exception(kind, value, trace))
            self.events.append((time.monotonic(), "istisna", text[-1500:]))
            previous(kind, value, trace)

        sys.excepthook = hook

    def _hook_qt_messages(self) -> None:
        from PyQt5.QtCore import QtCriticalMsg, QtFatalMsg, qInstallMessageHandler

        holder = {}

        def handler(kind, context, message):
            if kind in (QtCriticalMsg, QtFatalMsg):
                self.events.append((time.monotonic(), "qt-hata", message))
            else:
                self.qt_messages.append(message)
            if holder.get("previous"):
                holder["previous"](kind, context, message)

        holder["previous"] = qInstallMessageHandler(handler)

    # -- Windows: surucu harfleri ve birim bildirimleri --------------------
    def _start_windows_watch(self) -> None:
        import ctypes
        import ctypes.wintypes as wt
        from PyQt5.QtCore import QAbstractNativeEventFilter, QTimer

        k32 = ctypes.windll.kernel32
        self._mask = int(k32.GetLogicalDrives())

        def poll():
            mask = int(k32.GetLogicalDrives())
            if mask != self._mask:
                self.events.append((time.monotonic(), "harf",
                                    f"{letters(self._mask)} -> {letters(mask)}"))
                self._mask = mask

        self._mask_timer = QTimer()
        self._mask_timer.setInterval(200)
        self._mask_timer.timeout.connect(poll)
        self._mask_timer.start()
        events = self.events

        class Filter(QAbstractNativeEventFilter):
            def nativeEventFilter(self, kind, message):
                try:
                    msg = wt.MSG.from_address(int(message))
                    # WM_DEVICECHANGE, DBT_DEVICEARRIVAL / DBT_DEVICEREMOVECOMPLETE
                    if msg.message == 0x0219 and msg.wParam in (0x8000, 0x8004) \
                            and msg.lParam:
                        devtype = ctypes.c_uint32.from_address(msg.lParam + 4).value
                        if devtype == 2:            # DBT_DEVTYP_VOLUME
                            mask = ctypes.c_uint32.from_address(msg.lParam + 12).value
                            what = "takildi" if msg.wParam == 0x8000 else "cikarildi"
                            events.append((time.monotonic(), "aygit",
                                           f"{letters(mask)} {what}"))
                except Exception:                    # noqa: BLE001
                    pass
                return False, 0

        self._native = Filter()
        self.app.installNativeEventFilter(self._native)

    # -- durum -------------------------------------------------------------
    def snapshot(self) -> Dict[str, object]:
        w = self.window
        tree_item = w.tree.currentItem()
        browser = w.browser
        list_item = browser.list.currentItem()
        session = w.session
        info = getattr(session, "disk_info", None) if session else None
        return {
            "agac secimi": tree_item.text(0) if tree_item else "",
            "agac kaydirmasi": w.tree.verticalScrollBar().value(),
            "gezgin klasoru": browser.current_path,
            "gezgin kaydirmasi": browser.list.verticalScrollBar().value(),
            "gezgin secimi": list_item.text(0) if list_item else "",
            "bagli bolumler": list(info.mounted) if info is not None else [],
            "bekleyen islem": len(w.queue) if session else 0,
        }

    def since(self, t0: float, kinds=None) -> List[Tuple[float, str, str]]:
        return [e for e in self.events if e[0] >= t0
                and (kinds is None or e[1] in kinds)]

    def idle(self, seconds: float) -> Tuple[List[str], str]:
        """Bosta bekler; (sorunlar, not)."""
        from diskultimate.ui.main_window import DISK_POLL_MS

        t0 = time.monotonic()
        before = self.snapshot()
        wait(seconds)
        after = self.snapshot()
        events = self.since(t0)
        problems = []
        scans = sum(1 for e in events if e[1] == "tarama")
        # Yoklama calismiyorsa bosta beklemek hicbir seyi sinamaz (ui_smoke
        # zamanlayiciyi durduruyordu; bu hata o yuzden kacti).
        need = max(1, int(seconds * 1000 // DISK_POLL_MS) - 1)
        if scans < need:
            problems.append(f"disk yoklamasi calismadi ({scans} tur, en az {need})")
        for kind, text in IDLE_EVENTS.items():
            hits = [e[2] for e in events if e[1] == kind]
            if hits:
                sample = "; ".join(dict.fromkeys(h for h in hits if h))
                problems.append(f"{text}: {len(hits)} kez"
                                + (f" — {sample[:300]}" if sample else ""))
        for key, value in before.items():
            if after[key] != value:
                problems.append(f"{key} degisti: {value!r} -> {after[key]!r}")
        return problems, f"{scans} yoklama turu, {seconds:.0f} sn"

    def settle(self, quiet: float = 5.0, limit: float = 45.0) -> str:
        """Uygulama `quiet` saniye boyunca hicbir sey yapmayana kadar bekler."""
        start = time.monotonic()
        noisy = set(IDLE_EVENTS)
        while time.monotonic() - start < limit:
            last = max((e[0] for e in self.events if e[1] in noisy), default=0.0)
            if time.monotonic() - max(last, start) >= quiet:
                return f"{time.monotonic() - start:.1f} sn'de yerlesti"
            wait(0.5)
        raise StepFail(f"{limit:.0f} sn icinde yerlesmedi: "
                       + "; ".join(f"{e[1]}:{e[2]}" for e in self.since(start)
                                   if e[1] in noisy)[-400:])

    def new_freeze_reports(self) -> List[str]:
        now = set(glob.glob(os.path.join(self.freeze_dir, "freeze-*")))
        return sorted(now - self.freeze_before)


def letters(mask: int) -> str:
    return "".join(chr(65 + i) for i in range(26) if (mask >> i) & 1) or "-"


# --------------------------------------------------------------------------
# Test diski
# --------------------------------------------------------------------------
def build_image(path: str) -> None:
    """Goruntu kipi: GPT, iki FAT32 bolum (biri dolu klasorlu), bos alan."""
    from diskultimate.core.session import DiskSession

    s = DiskSession.create(path, 512 * MIB, scheme="gpt", overwrite=True)
    region = s.free_regions()[0]
    data = s.create_partition(region.start_lba, 200 * 2048, fs_key="fat32",
                              label=DATA_LABEL, name="veri")
    fs = s.filesystem(data.index)
    fs.mkdir("/Klasor")
    for n in range(FILE_COUNT):
        fs.write_file(f"/Klasor/dosya-{n:03d}.txt", f"dosya {n}\n".encode())
    fs.write_file("/okubeni.txt", b"DiskUltimate gezinti testi\n")
    fs.flush()
    region = s.free_regions()[0]
    s.create_partition(region.start_lba, 128 * 2048, fs_key="fat32",
                       label=TARGET_LABEL, name="hedef")
    s.close()


def powershell(script: str, timeout: int = 300) -> str:
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                        script], capture_output=True, text=True, errors="replace",
                       timeout=timeout)
    if r.returncode != 0:
        raise StepFail(f"PowerShell: {(r.stderr or r.stdout).strip()[:600]}")
    return r.stdout.strip()


def prepare_device(workdir: str):
    """Aygit kipi: VHD takilir, **Windows'un kendi araclariyla** bolumlenir.

    (aygit yolu, disk no, veri harfi, kaldirma islevi)
    """
    from . import device

    device.require_ci()
    if not IS_WINDOWS:
        raise StepSkip("aygit kipi su an yalnizca Windows'ta (VHD + birim harfleri)")
    stale = os.path.join(workdir, "aygit.vhd")
    if os.path.exists(stale):
        # Sure siniriyla kesilen onceki kosu VHD'yi takili birakmis olabilir.
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"Dismount-DiskImage -ImagePath '{stale}' "
                        "-ErrorAction SilentlyContinue | Out-Null"],
                       capture_output=True, timeout=120)
    path, cleanup = device.create(768, workdir)
    number = int(path.rsplit("PhysicalDrive", 1)[1])
    try:
        out = powershell(
            f"$n={number};"
            "Initialize-Disk -Number $n -PartitionStyle GPT;"
            "$a=New-Partition -DiskNumber $n -Size 300MB -AssignDriveLetter;"
            f"Format-Volume -Partition $a -FileSystem NTFS -NewFileSystemLabel {DATA_LABEL}"
            " -Confirm:$false | Out-Null;"
            "$b=New-Partition -DiskNumber $n -Size 160MB -AssignDriveLetter;"
            f"Format-Volume -Partition $b -FileSystem FAT32 -NewFileSystemLabel {TARGET_LABEL}"
            " -Confirm:$false | Out-Null;"
            "(Get-Partition -DiskNumber $n -PartitionNumber $a.PartitionNumber).DriveLetter")
        letter = out.strip()[-1:]
        if not letter.isalpha():
            raise StepFail(f"veri bolumune harf atanmadi: {out!r}")
        folder = f"{letter}:\\Klasor"
        os.makedirs(folder, exist_ok=True)
        for n in range(FILE_COUNT):
            with open(os.path.join(folder, f"dosya-{n:03d}.txt"), "w") as fh:
                fh.write(f"dosya {n}\n")
        with open(f"{letter}:\\okubeni.txt", "w") as fh:
            fh.write("DiskUltimate gezinti testi\n")
    except Exception:
        cleanup()
        raise
    return path, number, letter, cleanup


def check_device(info, path: str) -> None:
    """Yikici adimdan once: yalnizca bu testin taktigi kucuk aygit."""
    from . import device

    device.require_ci()
    problems = []
    if info is None or info.path != path:
        problems.append(f"acik disk {getattr(info, 'path', None)} != {path}")
    else:
        if info.is_system:
            problems.append("sistem diski")
        if info.size > device.MAX_DEVICE:
            problems.append(f"boyut {info.size // MIB} MiB")
        if not info.info_complete:
            problems.append("bilgi eksik")
    if problems:
        raise StepFail("aygit test aygiti olarak kabul edilmedi: " + ", ".join(problems))


# --------------------------------------------------------------------------
# Senaryo
# --------------------------------------------------------------------------
class Explore:
    def __init__(self, args):
        self.args = args
        self.out = args.rapor
        self.shot_dir = os.path.join(self.out, "goruntuler")
        os.makedirs(self.shot_dir, exist_ok=True)
        self.workdir = os.path.join(ROOT, ".tmp", "uzun", "gezinti")
        os.makedirs(self.workdir, exist_ok=True)
        self.steps: List[dict] = []
        self.details: List[str] = []
        self.shot_no = 0
        self.app = self.window = self.observer = self.driver = None
        self.device = None          # (yol, disk no, veri harfi, kaldirma)
        self.image = ""

    # -- yardimcilar -------------------------------------------------------
    def shot(self, name: str, widget=None) -> str:
        widget = widget or self.window
        if widget is None:
            return ""
        self.shot_no += 1
        # Windows dosya adinda ':' alternatif veri akisi sayilir.
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in name)
        path = os.path.join(self.shot_dir, f"{self.shot_no:02d}-{safe.strip('-')}.png")
        try:
            widget.grab().save(path)
        except Exception:                            # noqa: BLE001
            return ""
        return os.path.basename(path)

    def step(self, name: str, fn: Callable[[], Optional[str]]) -> bool:
        started = time.monotonic()
        rec = {"ad": name, "durum": "tamam"}
        t0 = time.monotonic()
        try:
            note = fn()
            if note:
                rec["not"] = note
        except StepSkip as exc:
            rec.update(durum="atlandi", neden=str(exc))
        except Exception as exc:                     # noqa: BLE001
            rec.update(durum="hata", hata=f"{type(exc).__name__}: {exc}"[:1500])
            if not isinstance(exc, StepFail):
                self.details.append(f"### {name}\n\n```\n{traceback.format_exc()}```\n")
        if self.observer is not None:
            crashes = self.observer.since(t0, {"istisna", "qt-hata"})
            if crashes:
                rec["durum"] = "hata"
                rec["hata"] = (rec.get("hata", "") + " | yakalanmamis hata: "
                               + crashes[0][2][-600:]).strip(" |")
        if self.driver is not None and self.driver.errors:
            rec["durum"] = "hata"
            rec["hata"] = (rec.get("hata", "") + " | pencere islemi: "
                           + "; ".join(self.driver.errors)).strip(" |")
            self.driver.errors = []
        if self.driver is not None and self.driver.unexpected:
            rec["durum"] = "hata"
            rec["hata"] = (rec.get("hata", "") + " | beklenmeyen pencere: "
                           + "; ".join(self.driver.unexpected)).strip(" |")
            self.driver.unexpected = []
        rec["sn"] = round(time.monotonic() - started, 1)
        rec["bellek_mb"] = round(peak_rss_mb(), 1)
        shot = self.shot(name)
        if shot:
            rec["goruntu"] = shot
        self.steps.append(rec)
        text = rec.get("not") or rec.get("hata") or rec.get("neden") or ""
        print(f"  {name:<28} {rec['durum']:<8} {text[:200]}", flush=True)
        self.report()                    # yarida kesilse de rapor kalir
        return rec["durum"] == "tamam"

    def idle(self, seconds: float = 0) -> str:
        problems, note = self.observer.idle(seconds or self.args.bosta)
        if problems:
            raise StepFail(" | ".join(problems))
        return note

    def tree_item(self, data):
        return self.window._find_tree_item(data)

    def partition_by_label(self, label: str):
        session = self.window.session
        for p in session.partitions if session else []:
            if (p.fs_label or "").strip().upper() == label:
                return p
        names = [(p.index, p.fs_type, p.fs_label) for p in session.partitions] \
            if session else []
        raise StepFail(f"'{label}' etiketli bolum yok: {names}")

    def select_partition(self, label: str):
        part = self.partition_by_label(label)
        item = self.tree_item(("part", (id(self.window.session), part.index)))
        if item is None:
            raise StepFail(f"agacta bolum {part.index} yok")
        click_item(self.window.tree, item)
        if not wait(10, lambda: self.window.selected_partition == part.index):
            raise StepFail(f"bolum {part.index} secilemedi")
        return part

    # -- adimlar -----------------------------------------------------------
    def s_prepare(self) -> str:
        if self.args.aygit:
            self.device = prepare_device(self.workdir)
            path, number, letter, _ = self.device
            return f"{path} (disk {number}), veri bolumu {letter}:"
        self.image = os.path.join(self.workdir, "gezinti.img")
        build_image(self.image)
        return self.image

    def s_launch(self) -> str:
        import main as app_main

        self.app, self.window = app_main.start([os.path.join(ROOT, "main.py")])
        if self.window is None:
            raise StepFail("yetkili kopya devraldi (beklenmiyordu)")
        self.observer = Observer(self.app, self.window)
        self.driver = ModalDriver(self.shot)
        if not wait(60, lambda: self.window._disk_scan_done):
            raise StepFail("ilk disk taramasi 60 sn'de bitmedi")
        wait(3)                                      # acilista otomatik secim
        session = self.window.session
        return (f"Qt {self.app.platformName()}, ilk acilan: "
                f"{session.path if session else 'yok'}")

    def s_open(self) -> str:
        w = self.window
        if self.device:
            path = self.device[0]
            item = self.tree_item(("phys", path))
            if item is None:
                raise StepFail(f"agacta {path} yok")
            click_item(w.tree, item, double=True)
            ok = wait(30, lambda: w.session is not None and getattr(
                w.session.disk_info, "path", None) == path)
        else:
            w.open_path(self.image)
            ok = wait(30, lambda: w.session is not None
                      and os.path.abspath(w.session.path) == os.path.abspath(self.image))
        if not ok:
            raise StepFail("test diski acilmadi")
        self.partition_by_label(DATA_LABEL)
        return f"{w.session.name}: {len(w.session.partitions)} bolum"

    def s_browse(self) -> str:
        from PyQt5.QtCore import Qt
        from PyQt5.QtTest import QTest

        self.select_partition(DATA_LABEL)
        browser = self.window.browser
        browser.path_edit.selectAll()
        QTest.keyClicks(browser.path_edit, "/Klasor")
        QTest.keyClick(browser.path_edit, Qt.Key_Return)
        if not wait(20, lambda: browser.current_path.rstrip("/").lower() == "/klasor"
                    and browser.list.topLevelItemCount() >= FILE_COUNT):
            raise StepFail(f"gezgin /Klasor'e girmedi: {browser.current_path}, "
                           f"{browser.list.topLevelItemCount()} oge")
        wait(0.5)
        middle = browser.list.topLevelItem(browser.list.topLevelItemCount() // 2)
        click_item(browser.list, middle)
        bar = browser.list.verticalScrollBar()
        if bar.value() == 0:
            raise StepFail("liste ortadaki ogeye kaydirilamadi")
        return (f"{browser.list.topLevelItemCount()} oge, kaydirma {bar.value()}, "
                f"secili {middle.text(0)}")

    def s_queue_format(self) -> str:
        from PyQt5.QtTest import QTest
        from PyQt5.QtWidgets import QDialogButtonBox
        from diskultimate.ui.dialogs.partition import FormatDialog

        part = self.select_partition(TARGET_LABEL)

        def fill(dlg):
            index = dlg.fs_combo.findData("exfat")
            if index < 0:
                dlg.reject()
                return
            dlg.fs_combo.setCurrentIndex(index)
            dlg.label_edit.selectAll()
            QTest.keyClicks(dlg.label_edit, NEW_LABEL)
            box = dlg.findChild(QDialogButtonBox)
            box.button(QDialogButtonBox.Ok).click()

        self.driver.expect("bicimlendir", lambda d: isinstance(d, FormatDialog), fill)
        self.window.act_format.trigger()
        self.driver.clear()
        if len(self.window.queue) != 1:
            raise StepFail(f"kuyrukta {len(self.window.queue)} adim (1 bekleniyordu)")
        return f"bolum {part.index} -> exFAT {NEW_LABEL} kuyrukta"

    def s_apply(self) -> str:
        from PyQt5.QtCore import Qt
        from PyQt5.QtTest import QTest
        from diskultimate.ui.dialogs.apply import ApplyDialog

        w = self.window
        if self.device:
            check_device(w.session.disk_info, self.device[0])

        def run(dlg):
            QTest.mouseClick(dlg.btn_run, Qt.LeftButton)
            done = wait(600, lambda: not dlg.isVisible() or (
                dlg.started and dlg._worker is not None
                and not dlg._worker.isRunning()))
            wait(1)
            if dlg.isVisible():
                self.shot("uygula-sonuc", dlg)
                dlg.btn_close.click()
            if not done:
                raise StepFail("Uygula 600 sn'de bitmedi")

        self.driver.expect("uygula", lambda d: isinstance(d, ApplyDialog), run)
        self.driver.expect("bagli-bolum-onayi", is_question, answer_yes)
        t0 = time.monotonic()
        w.act_apply.trigger()
        self.driver.clear()
        logs = [e[2] for e in self.observer.since(t0, {"gunluk"})]
        if len(w.queue):
            raise StepFail(f"kuyruk bosalmadi; gunluk: {logs[-4:]}")
        part = self.partition_by_label(NEW_LABEL)
        if (part.fs_type or "").lower() != "exfat":
            raise StepFail(f"bolum {part.index} {part.fs_type} (exfat bekleniyordu)")
        return f"bolum {part.index} exFAT {NEW_LABEL}; " + " / ".join(logs[-2:])[:300]

    def s_verify_os(self) -> str:
        if not self.device:
            from diskultimate.core.session import DiskSession
            s = DiskSession.open(self.image, readonly=True)
            try:
                found = [(p.fs_type, p.fs_label) for p in s.partitions]
            finally:
                s.close()
            if ("exfat", NEW_LABEL) not in [((t or "").lower(), (l or "").strip())
                                             for t, l in found]:
                raise StepFail(f"yeniden okunan goruntude yok: {found}")
            return f"yeniden okundu: {found}"
        number = self.device[1]
        query = (f"Get-Partition -DiskNumber {number} | Get-Volume | ForEach-Object "
                 "{ \"$($_.DriveLetter)|$($_.FileSystem)|$($_.FileSystemLabel)\" }")
        # Windows yeni birimi bolum tablosunu yeniden okuduktan sonra baglar;
        # birkac saniye taninir.
        deadline = time.monotonic() + 30
        while True:
            out = off_ui(powershell, query)
            rows = [r.split("|") for r in out.splitlines() if r.count("|") == 2]
            hit = [r for r in rows if r[1].lower() == "exfat" and r[2] == NEW_LABEL]
            if hit or time.monotonic() > deadline:
                break
            wait(2)
        if not hit:
            raise StepFail(f"Windows yeni birimi gormuyor (30 sn): {rows}")
        letter = hit[0][0]
        if not letter:
            return f"Windows: {rows} (harf yok, chkdsk atlandi)"
        r = off_ui(lambda: subprocess.run(["chkdsk", f"{letter}:"], capture_output=True,
                                          text=True, errors="replace", timeout=300))
        if r.returncode != 0:
            raise StepFail(f"chkdsk {letter}: kod {r.returncode}: {r.stdout[-600:]}")
        return f"Windows: {rows}; chkdsk {letter}: temiz"

    def s_close(self) -> str:
        self.driver.expect("kapanis-sorusu", is_question, answer_yes)
        self.window.close()
        wait(2)
        reports = self.observer.new_freeze_reports()
        if reports:
            raise StepFail("donma raporu yazildi: " + ", ".join(
                os.path.basename(r) for r in reports))
        crashes = self.observer.since(0, {"istisna", "qt-hata"})
        if crashes:
            raise StepFail(f"{len(crashes)} yakalanmamis hata")
        return f"Qt uyarisi: {len(self.observer.qt_messages)}"

    # -- akis --------------------------------------------------------------
    def run(self) -> int:
        import faulthandler

        self.started = time.time()
        # Sert sinir: modal bir pencere karsilanmazsa test sessizce asili
        # kalmaz; butun is parcaciklarinin yigini basilir ve surec biter.
        faulthandler.dump_traceback_later(self.args.sinir, exit=True)
        idle = lambda: self.idle()                  # noqa: E731
        ok = self.step("hazirlik", self.s_prepare)
        ok = ok and self.step("acilis", self.s_launch)
        if ok:
            self.step("bosta: acilis", idle)
            if self.step("test diskini ac", self.s_open):
                self.step("bosta: disk acik", idle)
                if self.step("gezgin: klasor", self.s_browse):
                    self.step("bosta: gezgin", idle)
                if self.step("kuyruk: bicimlendir", self.s_queue_format) \
                        and self.step("uygula", self.s_apply):
                    self.step("yerlesme", lambda: self.observer.settle())
                    self.step("bosta: uygulama sonrasi", idle)
                    self.step("isletim sistemi dogrulamasi", self.s_verify_os)
            self.step("kapanis", self.s_close)
        if self.device:
            try:
                self.device[3]()
            except Exception:                        # noqa: BLE001
                pass
        faulthandler.cancel_dump_traceback_later()
        code = self.report()
        print(f"\nSonuc: {'TAMAM' if code == 0 else 'HATA'} -> {self.out}")
        return code

    def report(self) -> int:
        started = getattr(self, "started", time.time())
        ok = all(s["durum"] != "hata" for s in self.steps)
        kind = "aygit" if self.args.aygit else "goruntu"
        plat = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
        name = f"gezinti-{plat}-{kind}"
        result = {"senaryo": name, "fs": "-", "tablo": "-", "profil": "gezinti",
                  "tohum": 0,
                  "platform": f"{_platform.system()} {_platform.release()} "
                              f"{_platform.machine()} py{_platform.python_version()}",
                  "basladi": time.strftime("%Y-%m-%d %H:%M:%S",
                                           time.localtime(started)),
                  "sure_sn": round(time.time() - started, 1), "dosya": 0,
                  "veri_mb": 0, "tamam": ok, "adimlar": self.steps,
                  "tekrar": "python -m tests.long.explore"
                            + (" --aygit" if self.args.aygit else "")}
        save(result, os.path.join(self.out, f"{name}.json"))
        lines = [f"# {name}", "", f"Sonuc: **{'TAMAM' if ok else 'HATA'}**", "",
                 "| Adim | Durum | Not | Goruntu |", "|---|---|---|---|"]
        for s in self.steps:
            text = (s.get("not") or s.get("hata") or s.get("neden") or "")
            text = text.replace("|", "/").replace("\n", " ")[:400]
            lines.append(f"| {s['ad']} | {s['durum']} | {text} | "
                         f"{s.get('goruntu', '')} |")
        if self.observer is not None:
            lines += ["", "## Olay akisi (son 200)", "", "```"]
            t0 = self.observer.events[0][0] if self.observer.events else 0
            for t, kind, detail in self.observer.events[-200:]:
                lines.append(f"{t - t0:8.2f}  {kind:<8} {detail[:160]}")
            lines += ["```"]
            if self.observer.qt_messages:
                lines += ["", "## Qt uyarilari", "", "```"]
                lines += list(dict.fromkeys(self.observer.qt_messages))[:50]
                lines += ["```"]
        if self.driver is not None:
            lines += ["", f"Karsilanan pencereler: {', '.join(self.driver.handled) or '-'}"]
        lines += [""] + self.details
        with open(os.path.join(self.out, f"{name}.md"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tests.long.explore")
    ap.add_argument("--rapor", default=os.path.join(ROOT, ".tmp", "uzun", "gezinti-rapor"))
    ap.add_argument("--aygit", action="store_true",
                    help="gercek aygit (takili VHD); yalnizca CI / test VM'i")
    ap.add_argument("--bosta", type=float, default=IDLE_SECONDS,
                    help="her bosta bekleme adiminin suresi (sn)")
    ap.add_argument("--sinir", type=int, default=900,
                    help="toplam sure siniri (sn); asilirsa yigin basilip cikilir")
    args = ap.parse_args(argv)
    os.makedirs(args.rapor, exist_ok=True)
    return Explore(args).run()


if __name__ == "__main__":
    sys.exit(main())
