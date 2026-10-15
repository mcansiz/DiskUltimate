"""Gozlemci arayuz gezintisi (ADR 0097): uygulamayi bir kullanici gibi surer.

    python -m tests.long.explore --senaryo temel                 # goruntu dosyasi
    python -m tests.long.explore --senaryo bolum --aygit         # gercek aygit (CI / test VM)
    python -m tests.long.explore --senaryo hepsi --bosta 4       # butun senaryolar

Senaryolar: temel, bolum, yedek, kurtarma, arayuz (ya da hepsi).

Neden: betik testleri tek bir cagrinin ciktisina bakar; `ui_smoke` disk
yoklamasini durdurur. 2026-10-14'te bulunan hata (acik diskin surucu harfleri
3 sn'de bir gidip geliyor, gezgin en basa donuyordu) ancak **gercek uygulama,
gercek disk ve zaman** birlikteyken gorunuyordu. Bu test:

* uygulamayi `main.start()` ile **ayni acilis yolundan** kurar, gercek Qt
  platformuyla acar; disk yoklamasi calisir;
* menu eylemlerini tetikler, agaca ve gezgine fareyle tiklar — modal
  pencereler gercek `exec_` dongusunde acilir, surucu onlari kullanici gibi
  doldurur; beklenmeyen pencere bir bulgudur;
* her adimdan sonra uygulamayi **bosta** bekletir ve "hicbir sey olmuyorken
  degismemesi gerekenleri" izler; sonucu isletim sisteminin kendi
  araclariyla (Get-Volume/chkdsk, blkid/fsck) ya da dosyayi yeniden okuyarak
  dogrular.

Dosya secme pencereleri (QFileDialog'un statik islevleri) Windows'ta yerel
penceredir, Qt onlari goremez; test onlarin yerine kullanicinin yazacagi
yolu dondurur — uygulama mantigina dokunulmaz.

Aygit kipi gercek bir disk (`tests.long.device`) kullanir ve YALNIZCA GitHub
Actions makinesinde ya da test VM'inde (`DU_TEST_VM=1`) calisir; yikici
adimlar yalnizca o aygita uygulanir (CLAUDE.md test kurali). Ana makineyi
etkileyen dugmelere (onyukleyici kurma, EFI degiskeni yazma) basilmaz.
"""
from __future__ import annotations

import argparse
import glob
import io
import os
import platform as _platform
import random
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import zipfile
from contextlib import contextmanager
from typing import Callable, Dict, List, Optional, Tuple

from .engine import ROOT, peak_rss_mb, save
from .data import MIB

WORKDIR = os.path.join(ROOT, ".tmp", "uzun", "gezinti")
os.environ.setdefault("DISKULTIMATE_NO_ELEVATION_PROMPT", "1")
os.environ.setdefault("DISKULTIMATE_UPDATE_CHECK", "0")
# Dil/tema/ikon secimleri kalici ayara yazilir: test kullanicinin gercek
# ayarlarina dokunmasin (APPDATA yalnizca bu surec ve cocuklari icin).
_CONFIG = os.path.join(WORKDIR, "ayar")
os.makedirs(_CONFIG, exist_ok=True)
os.environ["XDG_CONFIG_HOME"] = _CONFIG
if sys.platform.startswith("win"):
    os.environ["APPDATA"] = _CONFIG
sys.path.insert(0, ROOT)

IS_WINDOWS = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")
IDLE_SECONDS = 12
SHORT_IDLE = 4
DATA_LABEL = "DUVERI"
TARGET_LABEL = "DUFAT"
NEW_LABEL = "DUGUI"
NEW_PART_LABEL = "DUYENI"
FILE_COUNT = 240
BASIC_DATA_GUID = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
SCENARIOS = ("temel", "bolum", "yedek", "kurtarma", "arayuz")
USER_ROLE = 0x0100                 # Qt.UserRole


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


def run_tool(cmd: List[str], timeout: int = 300, check: bool = True) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                       timeout=timeout)
    if check and r.returncode != 0:
        raise StepFail(f"{os.path.basename(cmd[0])} kod {r.returncode}: "
                       f"{(r.stderr or r.stdout).strip()[-600:]}")
    return r.stdout


def powershell(script: str, timeout: int = 300) -> str:
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                        script], capture_output=True, text=True, errors="replace",
                       timeout=timeout)
    if r.returncode != 0:
        raise StepFail(f"PowerShell: {(r.stderr or r.stdout).strip()[:600]}")
    return r.stdout.strip()


# --------------------------------------------------------------------------
# Pencere tanima ve yanitlama
# --------------------------------------------------------------------------
def cls(name: str) -> Callable:
    return lambda w: type(w).__name__ == name


def is_question(widget) -> bool:
    from PyQt5.QtWidgets import QMessageBox

    return (isinstance(widget, QMessageBox)
            and widget.button(QMessageBox.Yes) is not None)


def is_info(widget) -> bool:
    from PyQt5.QtWidgets import QMessageBox

    return (isinstance(widget, QMessageBox)
            and widget.button(QMessageBox.Yes) is None)


def is_input(widget) -> bool:
    from PyQt5.QtWidgets import QInputDialog

    return isinstance(widget, QInputDialog)


def answer_yes(widget) -> None:
    from PyQt5.QtWidgets import QMessageBox

    widget.button(QMessageBox.Yes).click()


def answer_no(widget) -> None:
    from PyQt5.QtWidgets import QMessageBox

    widget.button(QMessageBox.No).click()


def answer_ok(widget) -> None:
    from PyQt5.QtWidgets import QMessageBox

    button = widget.button(QMessageBox.Ok) or widget.defaultButton() \
        or widget.escapeButton()
    button.click()


def answer_role(role) -> Callable:
    def act(widget):
        for button in widget.buttons():
            if widget.buttonRole(button) == role:
                button.click()
                return
        raise StepFail(f"pencerede {role} rollu dugme yok")
    return act


def answer_mounted(widget) -> None:
    """Bagli bolum uyarisi: Windows birimi kendisi kilitler (Evet); Linux'ta
    uyarinin onerdigi gibi "Baglantilari kes"."""
    from PyQt5.QtWidgets import QMessageBox

    if not IS_WINDOWS:
        for button in widget.buttons():
            if widget.buttonRole(button) == QMessageBox.ActionRole:
                button.click()
                return
    widget.button(QMessageBox.Yes).click()


def type_text(value: str) -> Callable:
    def act(dlg):
        dlg.setTextValue(value)
        dlg.accept()
    return act


def pick_item(predicate: Callable[[str], bool]) -> Callable:
    def act(dlg):
        items = dlg.comboBoxItems()
        hits = [i for i in items if predicate(i)]
        if not hits:
            dlg.reject()
            raise StepFail(f"secenek yok: {items[:6]}")
        dlg.setTextValue(hits[0])
        dlg.accept()
    return act


def press_ok(dlg) -> None:
    from PyQt5.QtWidgets import QDialogButtonBox

    for box in dlg.findChildren(QDialogButtonBox):
        ok = box.button(QDialogButtonBox.Ok)
        if ok is not None:
            if not ok.isEnabled():
                raise StepFail(f"{type(dlg).__name__}: Tamam dugmesi kapali")
            ok.click()
            return
    dlg.accept()


def close_dialog(dlg) -> None:
    from PyQt5.QtWidgets import QMessageBox

    if isinstance(dlg, QMessageBox):
        answer_ok(dlg)
    elif getattr(dlg, "btn_close", None) is not None:
        dlg.btn_close.click()
    else:
        dlg.reject()


def iter_tree(tree):
    stack = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
    while stack:
        item = stack.pop(0)
        yield item
        stack.extend(item.child(i) for i in range(item.childCount()))


# --------------------------------------------------------------------------
# Modal pencere surucusu ve dosya secme pencereleri
# --------------------------------------------------------------------------
class Handler:
    def __init__(self, name: str, match: Callable, act: Callable, optional: bool):
        self.name, self.match, self.act, self.optional = name, match, act, optional


class ModalDriver:
    """Acilan modal pencereyi beklenen islemle karsilar.

    Beklenmeyen pencere bir **bulgu**dur: goruntusu alinir, metni yazilir ve
    reddedilerek kapatilir (kullanici "Vazgec"e basmis gibi). Kendi kendine
    kapanan ilerleme penceresine (`TaskDialog`) dokunulmaz — reddedilirse
    `exec_` is parcacigini bekler ve arayuz kilitlenir.
    """

    IGNORE = ("TaskDialog",)

    def __init__(self, shots):
        from PyQt5.QtCore import QTimer

        self.shots = shots
        self.handlers: List[Handler] = []
        self.handled: List[str] = []
        self.unexpected: List[str] = []
        self.errors: List[str] = []
        self.timer = QTimer()
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def expect(self, name: str, match: Callable, act: Callable,
               optional: bool = False) -> None:
        self.handlers.append(Handler(name, match, act, optional))

    @staticmethod
    def describe(widget) -> str:
        from PyQt5.QtWidgets import QMessageBox

        text = widget.windowTitle()
        if isinstance(widget, QMessageBox):
            text += " | " + widget.text().replace("<br>", " ")
        return f"{type(widget).__name__}: {text}"[:300]

    def _run(self, handler: Handler, widget) -> None:
        try:
            handler.act(widget)
        except Exception as exc:                     # noqa: BLE001
            self.errors.append(f"{handler.name}: {type(exc).__name__}: {exc}")
            try:
                if widget.isVisible():
                    close_dialog(widget)
            except Exception:                        # noqa: BLE001
                pass

    def _tick(self) -> None:
        from PyQt5.QtCore import QTimer
        from PyQt5.QtWidgets import QApplication, QDialog, QMessageBox

        widget = QApplication.activeModalWidget()
        # Isaret pencerenin kendisinde durur: `id()` ile tutulunca kapanan
        # pencerenin numarasi yeni pencereye verildi, Uygula penceresi
        # "zaten islendi" sanilip sonsuza dek acik kaldi (2026-10-14).
        if widget is None or widget.property("du_driven") or not widget.isVisible():
            return
        if type(widget).__name__ in self.IGNORE:
            return
        widget.setProperty("du_driven", True)
        for handler in list(self.handlers):
            if handler.match(widget):
                self.handlers.remove(handler)
                self.handled.append(handler.name)
                self.shots(f"pencere-{handler.name}", widget)
                # Islem AYRI bir zamanlayicida calisir: bu yuvanin icinde
                # calissaydi, islemin actigi ic ice pencereyi (Uygula ->
                # "bagli bolum" sorusu) kimse karsilamazdi — Qt bir
                # zamanlayiciyi kendi yuvasi bitmeden yeniden tetiklemez.
                QTimer.singleShot(0, lambda h=handler, w=widget: self._run(h, w))
                return
        self.unexpected.append(self.describe(widget))
        self.shots("beklenmeyen-pencere", widget)
        if isinstance(widget, QMessageBox) and widget.escapeButton() is not None:
            widget.escapeButton().click()
        elif isinstance(widget, QDialog):
            widget.reject()
        else:
            widget.close()


class FileDialogs:
    """QFileDialog'un statik islevleri yerine kullanicinin yazacagi yol."""

    KINDS = {"getSaveFileName": "kaydet", "getOpenFileName": "ac",
             "getOpenFileNames": "ac-coklu", "getExistingDirectory": "klasor"}

    def __init__(self):
        from PyQt5.QtWidgets import QFileDialog

        self.answers: List[Tuple[str, object]] = []
        self.unexpected: List[str] = []
        for attr, kind in self.KINDS.items():
            setattr(QFileDialog, attr, staticmethod(self._make(kind)))

    def expect(self, kind: str, value) -> None:
        self.answers.append((kind, value))

    def _make(self, kind: str):
        def fn(*args, **kwargs):
            title = args[1] if len(args) > 1 else kwargs.get("caption", "")
            if self.answers and self.answers[0][0] == kind:
                value = self.answers.pop(0)[1]
            else:
                self.unexpected.append(f"{kind}: {title}")
                value = [] if kind == "ac-coklu" else ""
            return value if kind == "klasor" else (value, "")
        return fn


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

    def __init__(self, app, window, freeze_dir: str, freeze_before: set):
        self.app = app
        self.window = window
        self.events: List[Tuple[float, str, str]] = []
        self.qt_messages: List[str] = []
        self.freeze_dir, self.freeze_before = freeze_dir, freeze_before
        wrap = self._wrap
        wrap(window, "_build_tree", "agac")
        wrap(window, "refresh", "tazele")
        wrap(window, "_sync_open_mounts", "tarama")
        wrap(window, "log", "gunluk", lambda a, k: str(a[0]) if a else "")
        self._wrap_browser_reset(window.browser)
        wrap(window.browser, "navigate", "gezinti",
             lambda a, k: str(a[0]) if a else "")
        self._hook_exceptions()
        self._hook_qt_messages()
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

    def _wrap_browser_reset(self, browser) -> None:
        """Gezgin yalnizca BASKA bir dosya sistemi verilince sifirlanir; ayni
        nesne yeniden verilince konum korunur (file_browser.set_filesystem)."""
        original = browser.set_filesystem
        events = self.events

        def wrapper(fs=None, title="", *args, **kwargs):
            if fs is None or fs is not browser.fs:
                events.append((time.monotonic(), "gezgin", str(title)))
            return original(fs, title, *args, **kwargs)

        browser.set_filesystem = wrapper

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
# Isletim sisteminin gozunden disk (dogrulama)
# --------------------------------------------------------------------------
def norm_fs(name: str) -> str:
    name = (name or "").strip().lower()
    return "fat" if name in ("vfat", "fat", "fat12", "fat16", "fat32") else name


class Vol:
    def __init__(self, number: int, fs: str, label: str, where: str = "",
                 size: int = 0):
        self.number, self.fs, self.label = number, norm_fs(fs), (label or "").strip()
        self.where, self.size = where, size

    def __repr__(self) -> str:
        return (f"{self.number}:{self.fs or '-'}:{self.label or '-'}"
                + (f"@{self.where}" if self.where else ""))


class ImageHost:
    """Goruntu kipi: dosya cekirdekle **ayrica** acilip okunur."""

    def __init__(self, path: str):
        self.path = path

    def _open(self):
        from diskultimate.core.session import DiskSession
        return DiskSession.open(self.path, readonly=True)

    def volumes(self) -> List[Vol]:
        s = self._open()
        try:
            return [Vol(p.index, p.fs_type, p.fs_label, "", p.size)
                    for p in s.partitions]
        finally:
            s.close()

    def check(self, vol: Vol) -> str:
        return f"{vol}: yeniden okundu"

    def listdir(self, vol: Vol, folder: str) -> List[str]:
        s = self._open()
        try:
            fs = s.filesystem(vol.number)
            return [n.name for n in fs.listdir("/" + folder.strip("/"))]
        finally:
            s.close()

    def read(self, vol: Vol, path: str) -> bytes:
        s = self._open()
        try:
            return s.filesystem(vol.number).read("/" + path.strip("/"))
        finally:
            s.close()


class WindowsHost:
    def __init__(self, number: int):
        self.number = number

    def volumes(self) -> List[Vol]:
        out = powershell(
            "Update-HostStorageCache;"
            f"Get-Partition -DiskNumber {self.number} | ForEach-Object {{"
            " $v = $_ | Get-Volume -ErrorAction SilentlyContinue;"
            " \"$($_.PartitionNumber)|$($_.DriveLetter)|$($v.FileSystem)|"
            "$($v.FileSystemLabel)|$($_.Size)\" }")
        vols = []
        for row in out.splitlines():
            parts = row.split("|")
            if len(parts) == 5 and parts[0].isdigit():
                letter = parts[1].strip().strip("\x00")
                vols.append(Vol(int(parts[0]), parts[2], parts[3],
                                f"{letter}:" if letter else "",
                                int(parts[4]) if parts[4].isdigit() else 0))
        return vols

    def check(self, vol: Vol) -> str:
        if not vol.where:
            return f"{vol}: harf yok, chkdsk atlandi"
        r = subprocess.run(["chkdsk", vol.where], capture_output=True, text=True,
                           errors="replace", timeout=300)
        if r.returncode != 0:
            raise StepFail(f"chkdsk {vol.where} kod {r.returncode}: {r.stdout[-500:]}")
        return f"chkdsk {vol.where} temiz"

    def _path(self, vol: Vol, path: str) -> str:
        if not vol.where:
            raise StepFail(f"{vol}: Windows harf vermedi")
        return vol.where + "\\" + path.strip("/").replace("/", "\\")

    def listdir(self, vol: Vol, folder: str) -> List[str]:
        return os.listdir(self._path(vol, folder))

    def read(self, vol: Vol, path: str) -> bytes:
        with open(self._path(vol, path), "rb") as fh:
            return fh.read()


class LinuxHost:
    def __init__(self, device: str):
        self.device = device

    def part(self, number: int) -> str:
        sep = "p" if self.device[-1].isdigit() else ""
        return f"{self.device}{sep}{number}"

    def volumes(self) -> List[Vol]:
        run_tool(["blockdev", "--rereadpt", self.device], check=False)
        run_tool(["udevadm", "settle"], check=False)
        vols = []
        names = run_tool(["lsblk", "-ln", "-b", "-o", "NAME,SIZE", self.device]).split()
        base = os.path.basename(self.device)
        for name, size in zip(names[::2], names[1::2]):
            if name == base or not name[-1].isdigit():
                continue
            number = int(name[len(base):].lstrip("p"))
            info = dict(line.split("=", 1) for line in run_tool(
                ["blkid", "-p", "-o", "export", "/dev/" + name],
                check=False).splitlines() if "=" in line)
            vols.append(Vol(number, info.get("TYPE", ""), info.get("LABEL", ""),
                            "/dev/" + name, int(size)))
        return vols

    def check(self, vol: Vol) -> str:
        tool = {"fat": ["fsck.vfat", "-n"], "exfat": ["fsck.exfat", "-n"],
                "ntfs": ["ntfsfix", "-n"]}.get(vol.fs)
        if not tool or not shutil.which(tool[0]):
            return f"{vol}: denetim araci yok"
        run_tool(tool + [vol.where])
        return f"{tool[0]} {vol.where} temiz"

    @contextmanager
    def _mounted(self, vol: Vol):
        point = run_tool(["findmnt", "-n", "-o", "TARGET", vol.where],
                         check=False).strip().splitlines()
        if point:
            yield point[0]
            return
        folder = tempfile.mkdtemp(prefix="du-gezinti-")
        cmd = ["mount", "-o", "ro"] + (["-t", "ntfs-3g"] if vol.fs == "ntfs" else [])
        run_tool(cmd + [vol.where, folder])
        try:
            yield folder
        finally:
            run_tool(["umount", folder], check=False)
            os.rmdir(folder)

    def listdir(self, vol: Vol, folder: str) -> List[str]:
        with self._mounted(vol) as point:
            return os.listdir(os.path.join(point, folder.strip("/")))

    def read(self, vol: Vol, path: str) -> bytes:
        with self._mounted(vol) as point:
            with open(os.path.join(point, path.strip("/")), "rb") as fh:
                return fh.read()


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


def write_data_files(folder: str) -> None:
    os.makedirs(os.path.join(folder, "Klasor"), exist_ok=True)
    for n in range(FILE_COUNT):
        with open(os.path.join(folder, "Klasor", f"dosya-{n:03d}.txt"), "w") as fh:
            fh.write(f"dosya {n}\n")
    with open(os.path.join(folder, "okubeni.txt"), "w") as fh:
        fh.write("DiskUltimate gezinti testi\n")


class Device:
    def __init__(self, path: str, host, cleanup: Callable[[], None]):
        self.path, self.host, self.cleanup = path, host, cleanup


def prepare_device(workdir: str) -> Device:
    """Aygit kipi: test aygiti takilir, **isletim sisteminin kendi araclariyla**
    bolumlenir ve bicimlendirilir (kullanicinin ortami)."""
    from . import device

    device.require_ci()
    if IS_WINDOWS:
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
            write_data_files(f"{letter}:\\")
        except Exception:
            cleanup()
            raise
        return Device(path, WindowsHost(number), cleanup)
    if IS_LINUX:
        path, cleanup = device.create(768, workdir)
        host = LinuxHost(path)
        point = "/mnt/du-gezinti-veri"
        try:
            run_tool(["sgdisk", "-Z", path], check=False)
            run_tool(["sgdisk", "-n", "1:2048:+300M", "-t", "1:0700", "-c", "1:veri",
                      "-n", "2:0:+160M", "-t", "2:0700", "-c", "2:hedef", path])
            run_tool(["partprobe", path], check=False)
            run_tool(["udevadm", "settle"], check=False)
            run_tool(["mkfs.ntfs", "-Q", "-F", "-L", DATA_LABEL, host.part(1)])
            run_tool(["mkfs.vfat", "-F", "32", "-n", TARGET_LABEL, host.part(2)])
            os.makedirs(point, exist_ok=True)
            # Veri bolumu bagli kalir: uygulama bagli bolumu gostermeli,
            # Uygula'da uyarmali (CLAUDE.md kural 5).
            run_tool(["mount", "-t", "ntfs-3g", host.part(1), point])
            write_data_files(point)
            run_tool(["sync"])
        except Exception:
            run_tool(["umount", point], check=False)
            cleanup()
            raise

        def cleanup_all():
            run_tool(["umount", point], check=False)
            cleanup()
        return Device(path, host, cleanup_all)
    raise StepSkip("aygit kipi bu platformda yok")


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


def sample_files(folder: str) -> Dict[str, bytes]:
    """Kurtarma/yedek senaryolarinin kaynak dosyalari (icerik karsilastirilir)."""
    rnd = random.Random(1014)
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("icerik.txt", "DiskUltimate imza taramasi\n" * 40)
    files = {"a.txt": b"DiskUltimate a dosyasi\n" * 50,
             "b.bin": bytes(rnd.getrandbits(8) for _ in range(64 * 1024)),
             "c.zip": zbuf.getvalue()}
    os.makedirs(folder, exist_ok=True)
    for name, data in files.items():
        with open(os.path.join(folder, name), "wb") as fh:
            fh.write(data)
    return files


# --------------------------------------------------------------------------
# Senaryo motoru
# --------------------------------------------------------------------------
class Explore:
    def __init__(self, args):
        self.args = args
        self.out = args.rapor
        self.shot_dir = os.path.join(self.out, "goruntuler")
        os.makedirs(self.shot_dir, exist_ok=True)
        self.workdir = WORKDIR
        os.makedirs(self.workdir, exist_ok=True)
        self.steps: List[dict] = []
        self.details: List[str] = []
        self.shot_no = 0
        self.app = self.window = self.observer = self.driver = self.files = None
        self.device: Optional[Device] = None
        self.host = None
        self.image = ""
        self.test_session = None
        self.last_apply = None
        self.data: Dict[str, object] = {}

    # -- kayit -------------------------------------------------------------
    def shot(self, name: str, widget=None) -> str:
        widget = widget or self.window
        if widget is None:
            return ""
        self.shot_no += 1
        # Windows dosya adinda ':' alternatif veri akisi sayilir.
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in name)
        path = os.path.join(self.shot_dir, f"{self.shot_no:03d}-{safe.strip('-')}.png")
        try:
            widget.grab().save(path)
        except Exception:                            # noqa: BLE001
            return ""
        return os.path.basename(path)

    def step(self, name: str, fn: Callable[[], Optional[str]]) -> bool:
        started = time.monotonic()
        rec = {"ad": name, "durum": "tamam"}
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
        problems = []
        if self.observer is not None:
            crashes = self.observer.since(started, {"istisna", "qt-hata"})
            if crashes:
                problems.append("yakalanmamis hata: " + crashes[0][2][-600:])
        if self.driver is not None:
            if self.driver.errors:
                problems.append("pencere islemi: " + "; ".join(self.driver.errors))
                self.driver.errors = []
            if self.driver.unexpected:
                problems.append("beklenmeyen pencere: " + "; ".join(self.driver.unexpected))
                self.driver.unexpected = []
        if self.files is not None and self.files.unexpected:
            problems.append("beklenmeyen dosya penceresi: "
                            + "; ".join(self.files.unexpected))
            self.files.unexpected = []
        if problems:
            rec["durum"] = "hata"
            rec["hata"] = " | ".join([rec.get("hata", "")] + problems).strip(" |")
        rec["sn"] = round(time.monotonic() - started, 1)
        rec["bellek_mb"] = round(peak_rss_mb(), 1)
        shot = self.shot(name)
        if shot:
            rec["goruntu"] = shot
        self.steps.append(rec)
        text = rec.get("not") or rec.get("hata") or rec.get("neden") or ""
        print(f"  {name:<34} {rec['durum']:<8} {text[:220]}", flush=True)
        self.report()                    # yarida kesilse de rapor kalir
        return rec["durum"] == "tamam"

    def run_steps(self, plan) -> None:
        """plan: [(ad, islev, bosta_sn, kritik)]. Kritik adim duserse senaryonun
        kalani "atlandi" yazilir (sonraki adimlar ona dayanir)."""
        blocked = ""
        for name, fn, idle, critical in plan:
            name = getattr(self, "prefix", "") + name
            if blocked:
                self.steps.append({"ad": name, "durum": "atlandi", "sn": 0,
                                   "neden": f"onceki adim basarisiz: {blocked}"})
                print(f"  {name:<34} atlandi  (onceki: {blocked})", flush=True)
                continue
            ok = self.step(name, fn)
            if ok and idle:
                self.step(f"bosta: {name}", lambda s=idle: self.idle(s))
            if not ok and critical:
                blocked = name
        self.report()

    @contextmanager
    def expecting(self, *handlers):
        """Bu blokta acilmasi beklenen pencereler: (ad, eslesme, islem[, istege_bagli])."""
        for h in handlers:
            self.driver.expect(h[0], h[1], h[2], h[3] if len(h) > 3 else False)
        try:
            yield
            missing = [h.name for h in self.driver.handlers if not h.optional]
            if missing:
                raise StepFail("beklenen pencere acilmadi: " + ", ".join(missing))
        finally:
            self.driver.handlers = []

    # -- yardimcilar -------------------------------------------------------
    def idle(self, seconds: float = 0) -> str:
        problems, note = self.observer.idle(seconds or self.args.bosta)
        if problems:
            raise StepFail(" | ".join(problems))
        return note

    def trigger(self, action) -> None:
        if not action.isEnabled():
            raise StepFail(f"eylem kapali: '{action.text()}'")
        action.trigger()
        wait(0.3)

    def tree_item(self, data):
        return self.window._find_tree_item(data)

    def part(self, label: str, required: bool = True):
        session = self.window.session
        for p in session.partitions if session else []:
            if (p.fs_label or "").strip().upper() == label.upper():
                return p
        if not required:
            return None
        names = [(p.index, p.fs_type, p.fs_label) for p in session.partitions] \
            if session else []
        raise StepFail(f"'{label}' etiketli bolum yok: {names}")

    def part_by_index(self, index: int):
        for p in self.window.session.partitions:
            if p.index == index:
                return p
        return None

    def select_partition(self, label: str = "", index: int = 0):
        part = self.part_by_index(index) if index else self.part(label)
        if part is None:
            raise StepFail(f"bolum {index or label} yok")
        item = self.tree_item(("part", (id(self.window.session), part.index)))
        if item is None:
            raise StepFail(f"agacta bolum {part.index} yok")
        click_item(self.window.tree, item)
        if not wait(10, lambda: self.window.selected_partition == part.index):
            raise StepFail(f"bolum {part.index} secilemedi")
        return part

    def queue_len(self) -> int:
        return len(self.window.queue)

    def browse(self, path: str, min_items: int = 0) -> None:
        from PyQt5.QtCore import Qt
        from PyQt5.QtTest import QTest

        browser = self.window.browser
        browser.path_edit.selectAll()
        QTest.keyClicks(browser.path_edit, path)
        QTest.keyClick(browser.path_edit, Qt.Key_Return)
        if not wait(20, lambda: browser.current_path.rstrip("/").lower()
                    == path.rstrip("/").lower()
                    and browser.list.topLevelItemCount() >= min_items):
            raise StepFail(f"gezgin {path}'e girmedi: {browser.current_path}, "
                           f"{browser.list.topLevelItemCount()} oge")

    def browser_select(self, names: List[str]) -> None:
        browser = self.window.browser
        browser.list.clearSelection()
        items = [browser.list.topLevelItem(i)
                 for i in range(browser.list.topLevelItemCount())
                 if browser.list.topLevelItem(i).text(0) in names]
        # `setCurrentItem` secimi temizler: once gecerli oge, sonra digerleri
        # (Ctrl ile coklu secim gibi).
        if items:
            browser.list.setCurrentItem(items[0])
        for item in items:
            item.setSelected(True)
        found = len(items)
        if found != len(names):
            raise StepFail(f"gezginde bulunamadi: {names}; var: {self.browser_names()}")

    def browser_names(self) -> List[str]:
        lst = self.window.browser.list
        return [lst.topLevelItem(i).text(0) for i in range(lst.topLevelItemCount())]

    def apply_queue(self) -> str:
        """Uygula: tek onay penceresi, gerekirse bagli bolum sorusu."""
        w = self.window
        if self.device and w.session is self.test_session:
            check_device(w.session.disk_info, self.device.path)
        count = self.queue_len()
        if not count:
            raise StepFail("kuyruk bos")
        self.last_apply = None

        def run(dlg):
            from PyQt5.QtCore import Qt
            from PyQt5.QtTest import QTest

            QTest.mouseClick(dlg.btn_run, Qt.LeftButton)
            done = wait(900, lambda: not dlg.isVisible() or (
                dlg.started and dlg._worker is not None
                and not dlg._worker.isRunning()))
            wait(1)
            self.last_apply = (dlg.error, dlg.result_value)
            if dlg.isVisible():
                self.shot("uygula-sonuc", dlg)
                dlg.btn_close.click()
            if not done:
                raise StepFail("Uygula 900 sn'de bitmedi")

        with self.expecting(("uygula", cls("ApplyDialog"), run),
                            ("bagli-bolum", is_question, answer_mounted, True)):
            self.trigger(w.act_apply)
        error, result = self.last_apply or ("pencere sonucu yok", None)
        if error:
            raise StepFail(f"Uygula hata verdi: {error}")
        if result is not None and not getattr(result, "ok", True):
            raise StepFail(f"Uygula yarida kaldi: {result.summary()}")
        if self.queue_len():
            raise StepFail(f"kuyrukta {self.queue_len()} adim kaldi")
        if any(s.is_physical and not s.readonly for s in w.sessions):
            raise StepFail("Uygula sonrasi disk yazma kipinde kaldi (ADR 0025)")
        return f"{count} adim uygulandi"

    def os_volumes(self, expect: Dict[str, str] = None, absent=(),
                   timeout: float = 30) -> List[Vol]:
        """Isletim sisteminin gordugu birimler; beklenenler gorunene kadar bekler."""
        expect = {k.upper(): norm_fs(v) for k, v in (expect or {}).items()}
        deadline = time.monotonic() + timeout
        while True:
            vols = off_ui(self.host.volumes)
            by_label = {v.label.upper(): v for v in vols}
            missing = [k for k, fs in expect.items()
                       if k not in by_label or (fs and by_label[k].fs != fs)]
            present = [k for k in absent if k.upper() in by_label]
            if (not missing and not present) or time.monotonic() > deadline:
                break
            wait(2)
        if missing or present:
            raise StepFail(f"isletim sistemi: eksik {missing}, olmamasi gereken "
                           f"{present}; gorulen {vols}")
        return vols

    def os_check(self, vols: List[Vol], labels: List[str]) -> str:
        wanted = [x.upper() for x in labels]
        return "; ".join(off_ui(self.host.check, v) for v in vols
                         if v.label.upper() in wanted)

    def os_vol(self, label: str) -> Vol:
        for v in off_ui(self.host.volumes):
            if v.label.upper() == label.upper():
                return v
        raise StepFail(f"isletim sistemi '{label}' birimini gormuyor")

    # -- ortak adimlar -----------------------------------------------------
    def s_prepare(self) -> str:
        """Aygit kipinde test aygiti uygulamadan ONCE hazirlanir: ilk disk
        taramasi onu gorur (kullanici diski takip programi acmis gibi)."""
        if self.args.aygit:
            self.device = prepare_device(self.workdir)
            self.host = self.device.host
            return f"{self.device.path} ({type(self.host).__name__})"
        return "goruntu kipi: her senaryo kendi goruntusunu acar"

    def s_disk(self, scenario: str) -> Callable[[], str]:
        """Senaryonun test diski: aygit ya da senaryoya ozel taze goruntu."""
        def run() -> str:
            if not self.device:
                self.image = os.path.join(self.workdir, f"gezinti-{scenario}.img")
                # Uygulama acikken kurulur: arayuz parcaciginda kurulsaydi
                # donma yakalayicisi testin kendi isini raporlardi.
                off_ui(build_image, self.image)
                self.host = ImageHost(self.image)
            self.data = {}
            return self.s_open()
        return run

    def s_launch(self) -> str:
        import main as app_main
        from diskultimate.core import diagnostics

        # Dosya/klasor isleri uygulama ACILMADAN once: pencere gosterildikten
        # sonra test kendi kurulumunu yaparken olay dongusu donmuyor; Windows
        # CI'da ilk klasor olusturma 2 sn surdu ve donma yakalayicisi testin
        # kendi isini raporladi (2026-10-14).
        freeze_dir = diagnostics.report_dir()
        freeze_before = set(glob.glob(os.path.join(freeze_dir, "freeze-*")))
        self.files = FileDialogs()
        self.app, self.window = app_main.start([os.path.join(ROOT, "main.py")])
        if self.window is None:
            raise StepFail("yetkili kopya devraldi (beklenmiyordu)")
        diagnostics.beat()
        self.observer = Observer(self.app, self.window, freeze_dir, freeze_before)
        diagnostics.beat()
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
            path = self.device.path
            item = self.tree_item(("phys", path))
            if item is None:
                raise StepFail(f"agacta {path} yok")
            click_item(w.tree, item, double=True)
            ok = wait(30, lambda: w.session is not None and getattr(
                w.session.disk_info, "path", None) == path)
        else:
            w.open_path(self.image)
            ok = wait(30, lambda: w.session is not None and w.session.path
                      and os.path.abspath(w.session.path) == os.path.abspath(self.image))
        if not ok:
            raise StepFail("test diski acilmadi")
        self.test_session = w.session
        self.part(DATA_LABEL)
        return f"{w.session.name}: {len(w.session.partitions)} bolum"

    def activate_test_disk(self) -> None:
        w = self.window
        if w.session is not self.test_session:
            w.activate_session(self.test_session)
            wait(1, lambda: w.session is self.test_session)

    def s_settle(self) -> str:
        return self.observer.settle()

    def s_close(self) -> str:
        with self.expecting(("kapanis-sorusu", is_question, answer_yes, True)):
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

    def apply_steps(self, name: str, verify: Callable[[], str]):
        """Uygula + yerlesme + bosta + dogrulama (kritik)."""
        return [(f"uygula: {name}", self.apply_queue, 0, True),
                (f"yerlesme: {name}", self.s_settle, self.args.bosta, False),
                (f"dogrulama: {name}", verify, 0, True)]

    # ======================================================================
    # Senaryo: temel
    # ======================================================================
    def s_browse(self) -> str:
        self.activate_test_disk()
        self.select_partition(DATA_LABEL)
        self.browse("/Klasor", FILE_COUNT)
        browser = self.window.browser
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

        part = self.select_partition(TARGET_LABEL)

        def fill(dlg):
            index = dlg.fs_combo.findData("exfat")
            if index < 0:
                raise StepFail("bicimlendirme listesinde exFAT yok")
            dlg.fs_combo.setCurrentIndex(index)
            dlg.label_edit.selectAll()
            QTest.keyClicks(dlg.label_edit, NEW_LABEL)
            press_ok(dlg)

        with self.expecting(("bicimlendir", cls("FormatDialog"), fill)):
            self.trigger(self.window.act_format)
        if self.queue_len() != 1:
            raise StepFail(f"kuyrukta {self.queue_len()} adim (1 bekleniyordu)")
        return f"bolum {part.index} -> exFAT {NEW_LABEL} kuyrukta"

    def v_temel(self) -> str:
        part = self.part(NEW_LABEL)
        if (part.fs_type or "").lower() != "exfat":
            raise StepFail(f"uygulama: bolum {part.index} {part.fs_type}")
        vols = self.os_volumes({NEW_LABEL: "exfat", DATA_LABEL: ""})
        files = off_ui(self.host.listdir, self.os_vol(DATA_LABEL), "Klasor")
        if len(files) != FILE_COUNT:
            raise StepFail(f"veri bolumunde {len(files)} dosya ({FILE_COUNT} beklenirdi)")
        return f"{vols}; " + self.os_check(vols, [NEW_LABEL, DATA_LABEL])

    def plan_temel(self):
        return ([("gezgin: klasor", self.s_browse, self.args.bosta, False),
                 ("kuyruk: bicimlendir", self.s_queue_format, 0, True)]
                + self.apply_steps("bicimlendir", self.v_temel))

    # ======================================================================
    # Senaryo: bolum
    # ======================================================================
    def s_resize(self) -> str:
        self.activate_test_disk()
        part = self.select_partition(TARGET_LABEL)
        before = self.queue_len()
        self.data["hedef_index"] = part.index
        self.data["hedef_eski"] = part.sector_count

        def act(dlg):
            dlg.kapasite.setValue(dlg.kapasite.value() + 32)
            wait(0.3)
            if not dlg.basla.isEnabled():
                raise StepFail("boyutlandirma dugmesi kapali kaldi")
            dlg.basla.click()

        with self.expecting(("boyutlandir", cls("ResizePartitionDialog"), act)):
            self.trigger(self.window.act_resize_part)
        if self.queue_len() != before + 1:
            raise StepFail(f"kuyruk {before} -> {self.queue_len()}")
        return f"bolum {part.index} +32 MiB kuyrukta"

    def s_create(self) -> str:
        before = self.queue_len()

        def act(dlg):
            dlg.size_spin.setValue(64)
            dlg.fs_combo.setCurrentIndex(dlg.fs_combo.findData("exfat"))
            dlg.label_edit.setText(NEW_PART_LABEL)
            if getattr(dlg, "name_edit", None) is not None:
                dlg.name_edit.setText("yeni")
            press_ok(dlg)

        with self.expecting(("yeni-bolum", cls("CreatePartitionDialog"), act)):
            self.trigger(self.window.act_create_part)
        if self.queue_len() != before + 1:
            raise StepFail(f"kuyruk {before} -> {self.queue_len()}")
        return "64 MiB exFAT kuyrukta"

    def s_rename(self) -> str:
        if self.window.session.scheme != "gpt":
            raise StepSkip("bolum adi yalnizca GPT'de")
        self.select_partition(TARGET_LABEL)
        before = self.queue_len()
        with self.expecting(("bolum-adi", is_input, type_text("hedef-gui"))):
            self.trigger(self.window.act_rename_part)
        if self.queue_len() != before + 1:
            raise StepFail("ad degisikligi kuyruga girmedi")
        return "ad 'hedef-gui'"

    def s_type(self) -> str:
        self.select_partition(TARGET_LABEL)
        before = self.queue_len()
        gpt = self.window.session.scheme == "gpt"
        chooser = (pick_item(lambda t: BASIC_DATA_GUID in t.upper()) if gpt
                   else pick_item(lambda t: t.lower().startswith("0x0c")))
        with self.expecting(("bolum-turu", is_input, chooser)):
            self.trigger(self.window.act_type_part)
        if self.queue_len() != before + 1:
            raise StepFail("tur degisikligi kuyruga girmedi")
        return "tur: temel veri" if gpt else "tur: 0x0C"

    def s_label(self) -> str:
        self.select_partition(TARGET_LABEL)
        before = self.queue_len()
        with self.expecting(("etiket", is_input, type_text("DUFAT2"))):
            self.trigger(self.window.act_label)
        if self.queue_len() != before + 1:
            raise StepFail("etiket kuyruga girmedi")
        return "etiket DUFAT2"

    def s_boot(self) -> str:
        self.select_partition(DATA_LABEL)
        before = self.queue_len()
        self.trigger(self.window.act_boot)
        if self.queue_len() != before + 1:
            raise StepFail("onyukleme bayragi kuyruga girmedi")
        return "onyukleme bayragi kuyrukta"

    def s_alignment(self) -> str:
        with self.expecting(("hizalama", lambda w: cls("InfoDialog")(w) or is_info(w),
                             close_dialog)):
            self.trigger(self.window.act_alignment)
        return "hizalama raporu acildi"

    def s_layout(self) -> str:
        with self.expecting(("bolum-duzeni", cls("PartitionLayoutDialog"),
                             lambda d: d.reject())):
            self.trigger(self.window.act_edit_layout)
        return "duzen penceresi acilip kapandi"

    def s_ntfs(self) -> str:
        part = self.part(DATA_LABEL)
        if (part.fs_type or "").upper() != "NTFS":
            raise StepSkip(f"veri bolumu {part.fs_type}, NTFS degil")
        self.select_partition(DATA_LABEL)
        if part.mount_point and not IS_WINDOWS:
            # Linux/macOS'ta bagli NTFS'e yazilmaz: uygulama uyarip durur.
            with self.expecting(("ntfs-bagli", is_info, answer_ok)):
                self.trigger(self.window.act_ntfs_fix)
            return f"bagli bolum ({part.mount_point}): uyari gosterildi, onarim acilmadi"
        with self.expecting(("ntfs", cls("NtfsFixDialog"), lambda d: d.reject())):
            self.trigger(self.window.act_ntfs_fix)
        return "NTFS denetimi tamam (onarim kuyruga eklenmedi)"

    def v_bolum(self) -> str:
        s = self.window.session
        idx = self.data["hedef_index"]
        part = self.part_by_index(idx)
        notes = []
        if part is None or part.sector_count <= self.data["hedef_eski"]:
            raise StepFail(f"bolum {idx} buyumedi: {part and part.sector_count}")
        if s.scheme == "gpt" and (part.name or "") != "hedef-gui":
            raise StepFail(f"bolum adi '{part.name}'")
        self.part(NEW_PART_LABEL)
        self.part("DUFAT2")
        notes.append(f"uygulama: bolum {idx} {part.sector_count} sektor")
        vols = self.os_volumes({NEW_PART_LABEL: "exfat", "DUFAT2": "fat",
                                DATA_LABEL: ""})
        notes.append(str(vols))
        notes.append(self.os_check(vols, [NEW_PART_LABEL, "DUFAT2", DATA_LABEL]))
        files = off_ui(self.host.listdir, self.os_vol(DATA_LABEL), "Klasor")
        if len(files) != FILE_COUNT:
            raise StepFail(f"veri bolumunde {len(files)} dosya")
        return "; ".join(notes)

    def s_delete_undo_discard(self) -> str:
        self.select_partition(NEW_PART_LABEL)
        w = self.window
        self.trigger(w.act_delete_part)
        if self.queue_len() != 1:
            raise StepFail("silme kuyruga girmedi")
        self.trigger(w.act_undo_step)
        if self.queue_len() != 0:
            raise StepFail("geri al calismadi")
        self.trigger(w.act_delete_part)
        with self.expecting(("vazgec", is_question, answer_yes)):
            self.trigger(w.act_discard)
        if self.queue_len() != 0:
            raise StepFail("vazgec kuyrugu bosaltmadi")
        self.trigger(w.act_delete_part)
        return "sil -> geri al -> sil -> vazgec -> sil"

    def v_delete(self) -> str:
        if self.part(NEW_PART_LABEL, required=False) is not None:
            raise StepFail("silinen bolum uygulamada duruyor")
        return str(self.os_volumes({DATA_LABEL: ""}, absent=[NEW_PART_LABEL]))

    def s_convert(self, scheme: str) -> Callable[[], str]:
        def run() -> str:
            s = self.window.session
            ok, why = s.can_convert_to(scheme)
            if not ok:
                raise StepSkip(f"{scheme.upper()} donusumu yapilamaz: {why}")
            action = self.window.act_to_mbr if scheme == "mbr" else self.window.act_to_gpt
            self.trigger(action)
            if self.queue_len() != 1:
                raise StepFail("donusum kuyruga girmedi")
            return f"{s.scheme} -> {scheme} kuyrukta"
        return run

    def v_convert(self, scheme: str) -> Callable[[], str]:
        def run() -> str:
            s = self.window.session
            if s.scheme != scheme:
                raise StepFail(f"uygulama {s.scheme} goruyor")
            vols = self.os_volumes({DATA_LABEL: "", "DUFAT2": "fat"})
            files = off_ui(self.host.listdir, self.os_vol(DATA_LABEL), "Klasor")
            if len(files) != FILE_COUNT:
                raise StepFail(f"donusum sonrasi veri bolumunde {len(files)} dosya")
            return f"{scheme}: {vols}; " + self.os_check(vols, [DATA_LABEL, "DUFAT2"])
        return run

    def plan_bolum(self):
        b = SHORT_IDLE
        plan = [("boyutlandir", self.s_resize, b, True),
                ("yeni bolum", self.s_create, b, True),
                ("bolum adi", self.s_rename, b, False),
                ("bolum turu", self.s_type, b, False),
                ("etiket", self.s_label, b, True),
                ("onyukleme bayragi", self.s_boot, b, False),
                ("hizalama", self.s_alignment, 0, False),
                ("bolum duzeni", self.s_layout, 0, False),
                ("ntfs denetimi", self.s_ntfs, 0, False)]
        plan += self.apply_steps("bolum islemleri", self.v_bolum)
        plan += [("sil / geri al / vazgec", self.s_delete_undo_discard, b, True)]
        plan += self.apply_steps("silme", self.v_delete)
        plan += [("donusum: mbr", self.s_convert("mbr"), 0, True)]
        plan += self.apply_steps("mbr", self.v_convert("mbr"))
        plan += [("donusum: gpt", self.s_convert("gpt"), 0, True)]
        plan += self.apply_steps("gpt", self.v_convert("gpt"))
        return plan

    # ======================================================================
    # Senaryo: yedek
    # ======================================================================
    def s_import(self) -> str:
        """Gezginle hedef bolume uc dosya aktarilir (yazma yetkisi burada alinir)."""
        self.activate_test_disk()
        self.select_partition(TARGET_LABEL)
        src = os.path.join(self.workdir, "kaynak")
        self.data["dosyalar"] = sample_files(src)
        self.browse("/")
        self.files.expect("ac-coklu", [os.path.join(src, n)
                                       for n in sorted(self.data["dosyalar"])])
        with self.expecting(("bagli-bolum", is_question, answer_mounted, True)):
            self.trigger(self.window.browser.act_import)
            wait(15, lambda: all(n in self.browser_names()
                                 for n in self.data["dosyalar"]))
        names = self.browser_names()
        missing = [n for n in self.data["dosyalar"] if n not in names]
        if missing:
            raise StepFail(f"aktarilmayan dosyalar: {missing}; gezgin {names}")
        # Linux'ta "Baglantilari kes" veri bolumunu cikardi; yansimasi bir
        # sonraki disk taramasinda gelir — bu adimin sonucudur, bostaki
        # olcume tasmasin. Gezgin bu sirada yerinde kalmali.
        settled = self.observer.settle(quiet=4, limit=30)
        if self.window.browser.current_path.rstrip("/") != "":
            raise StepFail(f"gezgin yer degistirdi: {self.window.browser.current_path}")
        return f"aktarildi: {sorted(self.data['dosyalar'])}; {settled}"

    def backup_dialog_run(self, path: str, kinds=("partition",), target=None,
                          new_image: str = "", confirm: bool = False):
        """BackupDialog'u doldurup calistiran islem (yedek ya da geri yukleme)."""
        def act(dlg):
            restore = dlg.rb_restore.isChecked()
            dlg.path_edit.setText(path)
            if restore:
                if not wait(120, lambda: dlg.preview is not None and (
                        getattr(dlg, "_worker", None) is None
                        or not dlg._worker.isRunning())):
                    raise StepFail("yedek onizlemesi yuklenmedi")
                if new_image:
                    self.files.expect("kaydet", new_image)
                    dlg.tree.setCurrentItem(dlg.new_image_item)
                elif target is not None:
                    item = target(dlg)
                    if item is None:
                        raise StepFail("geri yukleme hedefi listede yok")
                    dlg.tree.setCurrentItem(item)
                if confirm:
                    dlg.confirm.setChecked(True)
            else:
                current = dlg.current_target()
                if current is None or current.kind not in kinds:
                    raise StepFail(f"yedek kaynagi {getattr(current, 'kind', None)}")
                dlg.used_only.setChecked(True)
            if not wait(20, lambda: dlg.btn_start.isEnabled()):
                raise StepFail(f"Baslat kapali: {dlg._problem()}")
            dlg.btn_start.click()
            wait(900, lambda: not dlg._running and dlg._result_shown)
            self.data["yedek_sonuc"] = (dlg.result_value, dlg.status.text())
            self.shot("yedek-sonuc", dlg)
            dlg.btn_close.click()
        return act

    def target_partition(self, index: int):
        def find(dlg):
            for item in iter_tree(dlg.tree):
                data = item.data(0, USER_ROLE)
                if not isinstance(data, int) or isinstance(data, bool):
                    continue
                t = dlg.targets[data]
                if t.kind == "partition" and t.session is self.test_session \
                        and t.index == index:
                    return item
            return None
        return find

    def s_backup_part(self) -> str:
        self.select_partition(TARGET_LABEL)
        path = os.path.join(self.workdir, "hedef.dub")
        if os.path.exists(path):
            os.unlink(path)
        self.data["yedek_sonuc"] = None
        with self.expecting(("bolum-yedegi", cls("BackupDialog"),
                             self.backup_dialog_run(path, ("partition",)))):
            self.trigger(self.window.act_backup_part)
        result = self.data.get("yedek_sonuc") or (None, "")
        if result[0] is None or not os.path.exists(path):
            raise StepFail(f"bolum yedegi alinmadi: {result[1]}")
        return f"{os.path.getsize(path) // 1024} KB — {result[1][:120]}"

    def s_backup_disk(self) -> str:
        path = os.path.join(self.workdir, "disk.dub")
        if os.path.exists(path):
            os.unlink(path)
        self.data["yedek_sonuc"] = None
        with self.expecting(("disk-yedegi", cls("BackupDialog"),
                             self.backup_dialog_run(path, ("image", "physical")))):
            self.trigger(self.window.act_backup_disk)
        result = self.data.get("yedek_sonuc") or (None, "")
        if result[0] is None or not os.path.exists(path):
            raise StepFail(f"disk yedegi alinmadi: {result[1]}")
        return f"{os.path.getsize(path) // 1024} KB — {result[1][:120]}"

    def s_format_target_exfat(self) -> str:
        self.select_partition(TARGET_LABEL)

        def fill(dlg):
            dlg.fs_combo.setCurrentIndex(dlg.fs_combo.findData("exfat"))
            dlg.label_edit.setText("SILINDI")
            press_ok(dlg)

        with self.expecting(("bicimlendir", cls("FormatDialog"), fill)):
            self.trigger(self.window.act_format)
        return "hedef bolum exFAT'a bicimlendirilecek (yedekten donecek)"

    def v_formatted(self) -> str:
        self.part("SILINDI")
        return str(self.os_volumes({"SILINDI": "exfat"}))

    def s_restore_part(self) -> str:
        index = self.part("SILINDI").index
        self.select_partition(index=index)
        path = os.path.join(self.workdir, "hedef.dub")
        self.data["yedek_sonuc"] = None
        with self.expecting(
                ("bolum-geri-yukle", cls("BackupDialog"),
                 self.backup_dialog_run(path, target=self.target_partition(index),
                                        confirm=True)),
                ("bagli-bolum", is_question, answer_mounted, True)):
            self.trigger(self.window.act_restore_part)
        result = self.data.get("yedek_sonuc") or (None, "")
        if result[0] is None:
            raise StepFail(f"geri yukleme olmadi: {result[1]}")
        if any(s.is_physical and not s.readonly for s in self.window.sessions):
            raise StepFail("geri yukleme sonrasi disk yazma kipinde kaldi (ADR 0025)")
        return result[1][:160]

    def v_restore_part(self) -> str:
        self.observer.settle()
        part = self.part(TARGET_LABEL)
        vols = self.os_volumes({TARGET_LABEL: "fat"}, absent=["SILINDI"])
        vol = self.os_vol(TARGET_LABEL)
        for name, data in self.data["dosyalar"].items():
            got = off_ui(self.host.read, vol, name)
            if got != data:
                raise StepFail(f"{name}: geri yuklenen icerik farkli "
                               f"({len(got)} / {len(data)} bayt)")
        return f"bolum {part.index}: {vols}; icerik ayni; " + self.os_check(
            vols, [TARGET_LABEL])

    def s_restore_new_image(self) -> str:
        path = os.path.join(self.workdir, "disk.dub")
        target = os.path.join(self.workdir, "geri.img")
        if os.path.exists(target):
            os.unlink(target)
        self.data["yedek_sonuc"] = None
        with self.expecting(("disk-geri-yukle", cls("BackupDialog"),
                             self.backup_dialog_run(path, new_image=target)),
                            ("dosya-var", is_question, answer_yes, True)):
            self.trigger(self.window.act_restore_disk)
        result = self.data.get("yedek_sonuc") or (None, "")
        if result[0] is None or not os.path.exists(target):
            raise StepFail(f"yeni goruntuye geri yukleme olmadi: {result[1]}")
        if not wait(30, lambda: any(s.path and os.path.abspath(s.path)
                                    == os.path.abspath(target)
                                    for s in self.window.sessions)):
            raise StepFail("geri yuklenen goruntu uygulamada acilmadi")
        return off_ui(self.verify_copy, target)

    def verify_copy(self, path: str) -> str:
        host = ImageHost(path)
        vols = host.volumes()
        labels = {v.label.upper(): v for v in vols}
        if DATA_LABEL not in labels or TARGET_LABEL not in labels:
            raise StepFail(f"{os.path.basename(path)}: bolumler {vols}")
        files = host.listdir(labels[DATA_LABEL], "Klasor")
        if len(files) != FILE_COUNT:
            raise StepFail(f"{os.path.basename(path)}: veri bolumunde {len(files)} dosya")
        for name, data in self.data["dosyalar"].items():
            if host.read(labels[TARGET_LABEL], name) != data:
                raise StepFail(f"{os.path.basename(path)}: {name} farkli")
        return f"{os.path.basename(path)}: {vols}, {len(files)} dosya, icerik ayni"

    def s_clone(self) -> str:
        self.activate_test_disk()
        target = os.path.join(self.workdir, "klon.img")
        if os.path.exists(target):
            os.unlink(target)
        state = {}

        def pick_new(picker):
            picker.select("new")
            picker.btn_ok.click()

        def act(dlg):
            from PyQt5.QtCore import Qt
            from PyQt5.QtTest import QTest

            self.driver.expect("hedef-secici", cls("_DiskPicker"), pick_new)
            QTest.mouseClick(dlg.btn_target, Qt.LeftButton)
            if not wait(10, lambda: dlg.target == "new"):
                raise StepFail("klon hedefi secilemedi")
            dlg.path_edit.setText(target)
            if not wait(10, lambda: dlg.btn_start.isEnabled()):
                raise StepFail(f"Klonla kapali: {dlg.problem()}")
            dlg.btn_start.click()
            wait(900, lambda: not dlg._running and dlg.status.property("result")
                 is not None)
            state["sonuc"] = (dlg.status.property("result"), dlg.status.text())
            self.shot("klon-sonuc", dlg)
            if dlg.btn_open.isVisible():
                dlg.btn_open.click()
            else:
                dlg.btn_close.click()

        with self.expecting(("klon", cls("CloneDialog"), act)):
            self.trigger(self.window.act_clone_disk)
        result = state.get("sonuc") or (None, "")
        if not result[0] or not os.path.exists(target):
            raise StepFail(f"klon olmadi: {result[1]}")
        if not wait(30, lambda: any(s.path and os.path.abspath(s.path)
                                    == os.path.abspath(target)
                                    for s in self.window.sessions)):
            raise StepFail("klon uygulamada acilmadi")
        return off_ui(self.verify_copy, target)

    def s_open_dub(self) -> str:
        from PyQt5.QtWidgets import QMessageBox

        path = os.path.join(self.workdir, "hedef.dub")
        with self.expecting(("dub-notu", lambda w: isinstance(w, QMessageBox),
                             answer_role(QMessageBox.RejectRole), True)):
            self.window.open_path(path)
            wait(10, lambda: self.window.session is not None
                 and self.window.session.is_backup)
        s = self.window.session
        if s is None or not s.is_backup:
            raise StepFail(".dub acilmadi")
        self.select_partition(index=s.partitions[0].index)
        self.browse("/")
        names = self.browser_names()
        missing = [n for n in self.data["dosyalar"] if n not in names]
        if missing:
            raise StepFail(f".dub icinde yok: {missing}")
        return f".dub gezildi: {names}"

    def s_backup_info(self) -> str:
        path = os.path.join(self.workdir, "disk.dub")
        state = {}

        def act(dlg):
            dlg.path_edit.setText(path)
            if not wait(60, lambda: dlg.preview is not None):
                raise StepFail("yedek bilgisi yuklenmedi")
            state["bilgi"] = {k: v.text() for k, v in dlg.info_labels.items()}
            dlg.btn_close.click()

        with self.expecting(("yedek-bilgisi", cls("BackupDialog"), act)):
            self.trigger(self.window.act_backup_info)
        info = state.get("bilgi") or {}
        if not any(info.values()):
            raise StepFail("yedek bilgisi bos")
        return ", ".join(f"{k}={v}" for k, v in info.items() if v)[:200]

    def plan_yedek(self):
        b = SHORT_IDLE
        plan = [("gezgin: dosya aktar", self.s_import, b, True),
                ("bolum yedegi", self.s_backup_part, b, True),
                ("disk yedegi", self.s_backup_disk, b, True),
                ("kuyruk: hedefi bicimlendir", self.s_format_target_exfat, 0, True)]
        plan += self.apply_steps("bicimlendir", self.v_formatted)
        plan += [("bolume geri yukle", self.s_restore_part, 0, True),
                 ("dogrulama: geri yukleme", self.v_restore_part, b, False),
                 ("yeni goruntuye geri yukle", self.s_restore_new_image, b, False),
                 ("diski klonla", self.s_clone, b, False),
                 (".dub ac ve gez", self.s_open_dub, b, False),
                 ("yedek bilgisi", self.s_backup_info, 0, False)]
        return plan

    # ======================================================================
    # Senaryo: kurtarma
    # ======================================================================
    def s_browser_ops(self) -> str:
        """Gezgin: dosya aktar, klasor ac, yeniden adlandir, disa aktar."""
        self.s_import()
        w = self.window
        with self.expecting(("klasor-ac", is_input, type_text("Yeni"))):
            self.trigger(w.browser.act_mkdir)
        self.browser_select(["a.txt"])
        with self.expecting(("yeniden-adlandir", is_input, type_text("a2.txt"))):
            self.trigger(w.browser.act_rename)
        names = self.browser_names()
        if "Yeni" not in names or "a2.txt" not in names:
            raise StepFail(f"gezgin: {names}")
        out = os.path.join(self.workdir, "disa")
        shutil.rmtree(out, ignore_errors=True)
        os.makedirs(out)
        self.browser_select(["b.bin"])
        self.files.expect("klasor", out)
        self.trigger(w.browser.act_export)
        wait(15, lambda: os.path.exists(os.path.join(out, "b.bin")))
        exported = os.path.join(out, "b.bin")
        if not os.path.exists(exported):
            raise StepFail(f"disa aktarma dosya uretmedi: {os.listdir(out)}")
        with open(exported, "rb") as fh:
            if fh.read() != self.data["dosyalar"]["b.bin"]:
                raise StepFail("disa aktarilan b.bin farkli")
        return f"gezgin: {names}; disa aktarma ayni"

    def s_browser_delete(self) -> str:
        self.browser_select(["a2.txt", "b.bin", "c.zip"])
        with self.expecting(("silme-onayi", is_question, answer_yes)):
            self.trigger(self.window.browser.act_delete)
        wait(5, lambda: "b.bin" not in self.browser_names())
        names = self.browser_names()
        if any(n in names for n in ("a2.txt", "b.bin", "c.zip")):
            raise StepFail(f"silinmedi: {names}")
        return f"kalan: {names}"

    def s_scan_deleted(self) -> str:
        self.select_partition(TARGET_LABEL)
        out = os.path.join(self.workdir, "kurtarilan")
        shutil.rmtree(out, ignore_errors=True)
        os.makedirs(out)
        want = len(self.data["dosyalar"]["b.bin"])

        def choose(dlg):
            picked = 0
            for item in iter_tree(dlg.tree):
                entry = item.data(0, USER_ROLE)
                if entry is not None and getattr(entry, "size", 0) == want \
                        and item.isDisabled() is False:
                    item.setSelected(True)
                    picked += 1
            if not picked:
                raise StepFail("silinmis listesinde b.bin boyutunda giris yok")
            self.files.expect("klasor", out)
            press_ok(dlg)

        with self.expecting(("silinmisler", cls("DeletedFilesDialog"), choose),
                            ("kurtarma-tamam", is_info, answer_ok)):
            self.trigger(self.window.act_scan_deleted)
        found = [os.path.join(dp, f) for dp, _d, fs in os.walk(out) for f in fs]
        for path in found:
            with open(path, "rb") as fh:
                if fh.read() == self.data["dosyalar"]["b.bin"]:
                    return f"b.bin kurtarildi ({os.path.basename(path)}), icerik ayni"
        raise StepFail(f"kurtarilan dosyalar b.bin ile tutmuyor: {found}")

    def s_carve(self) -> str:
        out = os.path.join(self.workdir, "imza")
        shutil.rmtree(out, ignore_errors=True)
        os.makedirs(out)

        def choose(dlg):
            picked = 0
            for item in iter_tree(dlg.tree):
                entry = item.data(0, USER_ROLE)
                if entry is not None and str(getattr(entry, "extension", "")) \
                        .lower().strip(".") == "zip":
                    item.setSelected(True)
                    picked += 1
            if not picked:
                raise StepFail("imza taramasi zip bulmadi")
            self.files.expect("klasor", out)
            press_ok(dlg)

        with self.expecting(("imza-secenekleri", cls("CarveOptionsDialog"), press_ok),
                            ("imza-sonuclari", cls("CarvedFilesDialog"), choose),
                            ("cikarma-tamam", is_info, answer_ok)):
            self.trigger(self.window.act_carve)
        for dp, _d, fs in os.walk(out):
            for f in fs:
                try:
                    with zipfile.ZipFile(os.path.join(dp, f)) as zf:
                        if zf.read("icerik.txt").startswith(b"DiskUltimate imza"):
                            return f"zip imzadan cikarildi: {f}"
                except Exception:                    # noqa: BLE001
                    continue
        raise StepFail("cikarilan dosyalarda c.zip icerigi yok")

    def s_delete_target(self) -> str:
        part = self.select_partition(TARGET_LABEL)
        self.data["kayip"] = (part.start_lba, part.sector_count)
        self.trigger(self.window.act_delete_part)
        if self.queue_len() != 1:
            raise StepFail("silme kuyruga girmedi")
        return f"bolum {part.index} (LBA {part.start_lba}) silinecek"

    def v_deleted_target(self) -> str:
        if self.part(TARGET_LABEL, required=False) is not None:
            raise StepFail("silinen bolum uygulamada duruyor")
        return str(self.os_volumes({DATA_LABEL: ""}, absent=[TARGET_LABEL]))

    def s_scan_lost(self) -> str:
        start, _count = self.data["kayip"]

        def choose(dlg):
            for item in iter_tree(dlg.tree):
                entry = item.data(0, USER_ROLE)
                if entry is not None and getattr(entry, "start_lba", -1) == start:
                    dlg.tree.setCurrentItem(item)
                    item.setSelected(True)
                    press_ok(dlg)
                    return
            raise StepFail(f"kayip bolum listesinde LBA {start} yok")

        with self.expecting(("tarama-derinligi", is_question, answer_no),
                            ("kayip-bolumler", cls("LostPartitionsDialog"), choose)):
            self.trigger(self.window.act_scan_lost)
        if self.queue_len() != 1:
            raise StepFail("bulunan bolum kuyruga girmedi")
        return f"LBA {start} bulundu, tabloya eklenecek"

    def v_lost(self) -> str:
        part = self.part(TARGET_LABEL)
        vols = self.os_volumes({TARGET_LABEL: "fat"})
        names = off_ui(self.host.listdir, self.os_vol(TARGET_LABEL), "/")
        if "Yeni" not in names:
            raise StepFail(f"geri gelen bolumde 'Yeni' klasoru yok: {names}")
        return f"bolum {part.index} geri geldi: {names}; " + self.os_check(
            vols, [TARGET_LABEL])

    def s_wipe(self, free: bool) -> Callable[[], str]:
        def run() -> str:
            self.select_partition(TARGET_LABEL)

            def fill(dlg):
                if free:
                    if not dlg.radio_bos.isEnabled():
                        raise StepFail("bos alan silme secilemiyor")
                    dlg.radio_bos.setChecked(True)
                else:
                    dlg.radio_tum.setChecked(True)
                    dlg.method_combo.setCurrentIndex(dlg.method_combo.findData("zero"))
                    if not dlg.verify_check.isEnabled():
                        raise StepFail("sifirlamada dogrulama secilemiyor")
                    dlg.verify_check.setChecked(True)
                press_ok(dlg)

            with self.expecting(("guvenli-sil", cls("WipeDialog"), fill)):
                self.trigger(self.window.act_wipe_part)
            if self.queue_len() != 1:
                raise StepFail("silme kuyruga girmedi")
            if free:
                return "bos alan silme kuyrukta"
            # Pencere secenegi adima tasinmali (eskiden yok sayiliyordu).
            step = self.window.queue.items[-1]
            if not step.params.get("verify"):
                raise StepFail("dogrulama secimi kuyruk adimina gecmedi")
            return "bolum sifirla silinecek, sonra dogrulanacak"
        return run

    def v_wipe_free(self) -> str:
        vols = self.os_volumes({TARGET_LABEL: "fat"})
        names = off_ui(self.host.listdir, self.os_vol(TARGET_LABEL), "/")
        if "Yeni" not in names:
            raise StepFail(f"bos alan silme dosyaya dokundu: {names}")
        return f"{names}; " + self.os_check(vols, [TARGET_LABEL])

    def v_wipe_all(self) -> str:
        part = next((p for p in self.window.session.partitions
                     if p.start_lba == self.data["kayip"][0]), None)
        if part is None:
            raise StepFail("sifirlanan bolum tablodan da gitti")
        if part.fs_type:
            raise StepFail(f"sifirlanan bolumde hala {part.fs_type} goruluyor")
        vols = self.os_volumes({DATA_LABEL: ""}, absent=[TARGET_LABEL])
        return f"bolum {part.index} ham; {vols}"

    def plan_kurtarma(self):
        b = SHORT_IDLE
        plan = [("gezgin: aktar/klasor/ad/disa", self.s_browser_ops, b, True),
                ("gezgin: sil", self.s_browser_delete, b, True),
                ("silinmis dosya kurtar", self.s_scan_deleted, b, False),
                ("imza taramasi", self.s_carve, b, False),
                ("kuyruk: hedefi sil", self.s_delete_target, 0, True)]
        plan += self.apply_steps("bolum sil", self.v_deleted_target)
        plan += [("kayip bolum tara", self.s_scan_lost, 0, True)]
        plan += self.apply_steps("kayip bolum", self.v_lost)
        plan += [("kuyruk: bos alani sil", self.s_wipe(True), 0, True)]
        plan += self.apply_steps("bos alan silme", self.v_wipe_free)
        plan += [("kuyruk: bolumu sifirla", self.s_wipe(False), 0, True)]
        plan += self.apply_steps("bolum silme", self.v_wipe_all)
        return plan

    # ======================================================================
    # Senaryo: arayuz
    # ======================================================================
    def s_languages(self) -> str:
        from diskultimate import i18n

        w = self.window
        start = i18n.current_language()
        codes = [a.data() for a in w._lang_group.actions()]
        for code in codes + [start]:
            # Dil degisince menu yeniden kurulur: eylem her seferinde yeniden aranir.
            action = next((a for a in w._lang_group.actions() if a.data() == code), None)
            if action is None:
                raise StepFail(f"dil menusunde {code} yok")
            action.trigger()
            wait(0.5)
            if i18n.current_language() != code:
                raise StepFail(f"dil {code} olmadi: {i18n.current_language()}")
            self.shot(f"dil-{code}")
        return f"{len(codes)} dil: {', '.join(codes)}"

    def s_themes(self) -> str:
        w = self.window
        keys = [a.data() for a in w._theme_group.actions()]
        for key in keys + ["system"]:
            next(a for a in w._theme_group.actions() if a.data() == key).trigger()
            wait(0.5)
            self.shot(f"tema-{key}")
        return f"temalar: {keys}"

    def s_icons(self) -> str:
        w = self.window
        actions = w._icon_group.actions()
        start = next((a.data() for a in actions if a.isChecked()), None)
        keys = [a.data() for a in actions]
        for key in keys + ([start] if start else []):
            next(a for a in w._icon_group.actions() if a.data() == key).trigger()
            wait(0.5)
            self.shot(f"ikon-{key}")
        return f"ikon setleri: {keys}"

    def s_info_windows(self) -> str:
        from PyQt5.QtWidgets import QDialog, QMessageBox

        w = self.window
        seen = []
        for name, action, match in (
                ("sistem-bilgisi", w.act_sysinfo, cls("InfoDialog")),
                ("tanilama", w.act_diag_status,
                 lambda d: cls("InfoDialog")(d) or isinstance(d, QMessageBox)),
                ("donma-raporu", w.act_diag_report,
                 lambda d: cls("TextViewDialog")(d) or isinstance(d, QMessageBox)),
                ("yigin-dokumu", w.act_diag_dump, is_info),
                ("hakkinda", w.act_about, lambda d: isinstance(d, QMessageBox)),
                ("lisanslar", w.act_licenses, lambda d: type(d) is QDialog)):
            with self.expecting((name, match, close_dialog)):
                self.trigger(action)
            seen.append(name)
        return ", ".join(seen)

    def s_disk_info(self) -> str:
        if not self.device:
            raise StepSkip("fiziksel disk bilgisi yalnizca aygit kipinde")
        item = self.tree_item(("phys", self.device.path))
        if item is None:
            raise StepFail("agacta test diski yok")
        click_item(self.window.tree, item)
        self.trigger(self.window.act_disk_info)
        text = self.window.info_view.toPlainText()
        if not text.strip():
            raise StepFail("disk bilgisi bos")
        return text.strip().splitlines()[0][:120]

    def s_hex(self) -> str:
        from diskultimate.ui.main_window import TAB_HEX

        self.activate_test_disk()
        self.select_partition(DATA_LABEL)
        self.window.tabs.setCurrentIndex(TAB_HEX)
        wait(1)
        self.shot("onaltilik")
        self.window.tabs.setCurrentIndex(0)
        return "onaltilik gorunum acildi"

    def new_image(self, ext: str, action_name: str) -> Callable[[], str]:
        def run() -> str:
            path = os.path.join(self.workdir, f"yeni{ext}")
            if os.path.exists(path):
                os.unlink(path)

            def fill(dlg):
                dlg.path_edit.setText(path)
                dlg.size_spin.setValue(64)
                dlg.unit_combo.setCurrentText("MB")
                dlg.scheme_combo.setCurrentIndex(dlg.scheme_combo.findData("gpt"))
                dlg.fs_combo.setCurrentIndex(dlg.fs_combo.findData("fat32"))
                dlg.label_edit.setText("YENI")
                press_ok(dlg)

            with self.expecting((action_name, cls("NewImageDialog"), fill),
                                ("konum-uyarisi", is_question, answer_yes, True)):
                self.trigger(getattr(self.window, action_name))
            if not wait(60, lambda: self.window.session is not None
                        and self.window.session.path
                        and os.path.abspath(self.window.session.path)
                        == os.path.abspath(path)):
                raise StepFail(f"{os.path.basename(path)} olusmadi/acilmadi")
            parts = [(p.index, p.fs_type, p.fs_label)
                     for p in self.window.session.partitions]
            if not any((t or "").upper().startswith("FAT") for _i, t, _l in parts):
                raise StepFail(f"yeni goruntude FAT bolum yok: {parts}")
            return f"{os.path.basename(path)}: {parts}"
        return run

    def s_resize_image(self) -> str:
        w = self.window
        path = os.path.join(self.workdir, "yeni.img")
        session = next(s for s in w.sessions if s.path and
                       os.path.abspath(s.path) == os.path.abspath(path))
        w.activate_session(session)
        wait(1)
        with self.expecting(("goruntu-boyutu", is_input, type_text("96 MB"))):
            self.trigger(w.act_resize_img)
        self.apply_queue()
        size = os.path.getsize(path)
        if size != 96 * MIB:
            raise StepFail(f"goruntu {size} bayt (96 MiB bekleniyordu)")
        return "yeni.img 64 -> 96 MiB"

    def s_close_image(self) -> str:
        w = self.window
        path = os.path.join(self.workdir, "yeni.vhd")
        session = next(s for s in w.sessions if s.path and
                       os.path.abspath(s.path) == os.path.abspath(path))
        w.activate_session(session)
        wait(1)
        before = len(w.sessions)
        with self.expecting(("bekleyen-var", is_question, answer_yes, True)):
            self.trigger(w.act_close)
        if len(w.sessions) != before - 1:
            raise StepFail("goruntu kapanmadi")
        return "yeni.vhd kapatildi"

    def s_boot_windows(self) -> str:
        """Onyukleyici ve EFI pencereleri acilir, hicbir dugmeye basilmaz."""
        from PyQt5.QtWidgets import QMessageBox

        self.activate_test_disk()
        notes = []
        for name, action, klass in (("onyukleyici", self.window.act_bootloader,
                                     "BootloaderDialog"),
                                    ("efi", self.window.act_efi_boot, "EfiBootDialog")):
            state = {}

            def seen(d, n=name, s=state):
                s[n] = type(d).__name__
                close_dialog(d)

            with self.expecting((name, cls(klass), seen, True),
                                (name + "-uyari", lambda d: isinstance(d, QMessageBox),
                                 seen, True)):
                self.trigger(action)
                wait(3, lambda s=state: bool(s))
            if not state:
                raise StepFail(f"{name}: hicbir pencere acilmadi")
            notes.append(f"{name}: {state[name]}")
        return ", ".join(notes)

    def plan_arayuz(self):
        b = SHORT_IDLE
        return [("diller", self.s_languages, b, False),
                ("temalar", self.s_themes, b, False),
                ("ikon setleri", self.s_icons, b, False),
                ("bilgi pencereleri", self.s_info_windows, b, False),
                ("disk bilgisi", self.s_disk_info, 0, False),
                ("onaltilik gorunum", self.s_hex, 0, False),
                ("yeni goruntu", self.new_image(".img", "act_new"), b, False),
                ("yeni VHD", self.new_image(".vhd", "act_new_vhd"), b, False),
                ("goruntuyu boyutlandir", self.s_resize_image, b, False),
                ("goruntuyu kapat", self.s_close_image, b, False),
                ("onyukleme pencereleri", self.s_boot_windows, b, False)]

    # -- akis --------------------------------------------------------------
    def run(self) -> int:
        import faulthandler

        self.started = time.time()
        # Sert sinir: modal bir pencere karsilanmazsa test sessizce asili
        # kalmaz; butun is parcaciklarinin yigini basilir ve surec biter.
        faulthandler.dump_traceback_later(self.args.sinir, exit=True)
        names = SCENARIOS if self.args.senaryo == "hepsi" else (self.args.senaryo,)
        long_idle = self.args.bosta
        self.run_steps([("hazirlik", self.s_prepare, 0, True),
                        ("acilis", self.s_launch, long_idle, True)])
        # Her senaryo kendi diskiyle baslar; kritik adim engeli senaryo icinde
        # kalir (bir senaryonun dusmesi digerlerini atlatmaz).
        for name in names:
            if self.window is None:
                break
            self.prefix = f"{name}: " if len(names) > 1 else ""
            self.run_steps([("test diski", self.s_disk(name), long_idle, True)]
                           + getattr(self, f"plan_{name}")())
        self.prefix = ""
        if self.window is not None:
            self.step("kapanis", self.s_close)
        if self.device:
            try:
                self.device.cleanup()
            except Exception:                        # noqa: BLE001
                pass
        faulthandler.cancel_dump_traceback_later()
        code = self.report()
        print(f"\nSonuc: {'TAMAM' if code == 0 else 'HATA'} -> {self.out}")
        return code

    def name(self) -> str:
        kind = "aygit" if self.args.aygit else "goruntu"
        plat = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
        return f"gezinti-{plat}-{kind}-{self.args.senaryo}"

    def report(self) -> int:
        started = getattr(self, "started", time.time())
        ok = all(s["durum"] != "hata" for s in self.steps)
        name = self.name()
        result = {"senaryo": name, "fs": "-", "tablo": "-", "profil": "gezinti",
                  "tohum": 0,
                  "platform": f"{_platform.system()} {_platform.release()} "
                              f"{_platform.machine()} py{_platform.python_version()}",
                  "basladi": time.strftime("%Y-%m-%d %H:%M:%S",
                                           time.localtime(started)),
                  "sure_sn": round(time.time() - started, 1), "dosya": 0,
                  "veri_mb": 0, "tamam": ok, "adimlar": self.steps,
                  "tekrar": f"python -m tests.long.explore --senaryo {self.args.senaryo}"
                            + (" --aygit" if self.args.aygit else "")}
        save(result, os.path.join(self.out, f"{name}.json"))
        counts = {k: sum(1 for s in self.steps if s["durum"] == k)
                  for k in ("tamam", "hata", "atlandi")}
        lines = [f"# {name}", "",
                 f"Sonuc: **{'TAMAM' if ok else 'HATA'}** — {counts['tamam']} tamam, "
                 f"{counts['hata']} hata, {counts['atlandi']} atlandi", "",
                 "| Adim | Durum | Not | Goruntu |", "|---|---|---|---|"]
        for s in self.steps:
            text = (s.get("not") or s.get("hata") or s.get("neden") or "")
            text = text.replace("|", "/").replace("\n", " ")[:400]
            lines.append(f"| {s['ad']} | {s['durum']} | {text} | "
                         f"{s.get('goruntu', '')} |")
        if self.observer is not None:
            lines += ["", "## Olay akisi (son 300)", "", "```"]
            t0 = self.observer.events[0][0] if self.observer.events else 0
            for t, kind, detail in self.observer.events[-300:]:
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
    ap.add_argument("--senaryo", default="temel", choices=SCENARIOS + ("hepsi",))
    ap.add_argument("--aygit", action="store_true",
                    help="gercek aygit (takili VHD / scsi_debug); yalnizca CI / test VM'i")
    ap.add_argument("--bosta", type=float, default=IDLE_SECONDS,
                    help="uzun bosta bekleme adiminin suresi (sn)")
    ap.add_argument("--sinir", type=int, default=1500,
                    help="toplam sure siniri (sn); asilirsa yigin basilip cikilir")
    args = ap.parse_args(argv)
    if args.aygit and args.senaryo == "hepsi":
        ap.error("aygit kipinde tek senaryo kosulur (her senaryo taze aygit ister)")
    os.makedirs(args.rapor, exist_ok=True)
    return Explore(args).run()


if __name__ == "__main__":
    sys.exit(main())
