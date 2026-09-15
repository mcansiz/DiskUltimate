"""Calisma zamani tanilama: gunluk, sure olcumu ve **donma yakalayici**.

Amac: "uygulama donuyor" turu sikayetlerin tahminle degil **kanitla**
cozulmesi. Uc parca vardir:

1. **Gunluk** — her oturum `.claude/logs/runtime/` altina bir dosya yazar.
   `span()` ile isaretlenen her islem suresiyle birlikte gunluge girer; esigi
   asan islemler `YAVAS` olarak isaretlenir.
2. **Donma yakalayici (`Watchdog`)** — arayuz is parcacigi duzenli olarak
   `beat()` cagirir. Bu nabiz `STALL_MS` boyunca gelmezse arayuzun bloke
   oldugu anlasilir ve o an **butun is parcaciklarinin yigini** bir rapor
   dosyasina dokulur. Donma surdukce ornekleme tekrarlanir; boylece "tek bir
   cagrida mi asili kaldi, yoksa dongude mi" sorusu yanitlanir.
3. **Cokme yakalayici** — `faulthandler` ile olumcul hatalarda (segfault,
   ctypes erisim ihlali) yigin diske yazilir.

Katman kurali: bu modul saf Python'dur, Qt bilmez. Qt tarafina baglanmasi
`ui/diag.py` icindedir.

Kapatma / ayar (ortam degiskenleri):
    DISKULTIMATE_DIAG=0          tanilamayi tumden kapatir
    DISKULTIMATE_DIAG_VERBOSE=1  her span'i (yavas olmasa da) gunluge yazar
    DISKULTIMATE_DIAG_STALL_MS   donma esigi (varsayilan 1500 ms)
    DISKULTIMATE_LOG_DIR         gunluk dizinini degistirir
"""
from __future__ import annotations

import faulthandler
import logging
import os
import sys
import threading
import time
import traceback
from collections import deque
from contextlib import contextmanager
from typing import Any, Deque, Dict, List, Optional, Tuple

from ..paths import LOG_DIR

# -- ayarlar ---------------------------------------------------------------
SLOW_MS = 200.0          # bu suren uzerindeki islem gunluge YAVAS girer
STALL_MS = 1500.0        # arayuz bu kadar nabizsiz kalirsa donma sayilir
SAMPLE_MS = 250.0        # nabiz denetim araligi
RESAMPLE_MS = 2000.0     # donma surerken yeni yigin ornegi araligi
HISTORY_SIZE = 300       # raporda gosterilen son islem sayisi
KEEP_SESSIONS = 12       # saklanan eski oturum gunlugu sayisi
KEEP_REPORTS = 40        # saklanan eski donma raporu sayisi

_lock = threading.RLock()
_logger: Optional[logging.Logger] = None
_session_log = ""
_crash_log = ""
_crash_handle = None
_enabled = False
_verbose = False
_history: Deque[Tuple[float, float, str, str]] = deque(maxlen=HISTORY_SIZE)
_open_spans: Dict[int, List["Span"]] = {}
_watchdog: Optional["Watchdog"] = None
_last_report = ""
_start_time = 0.0


# ==========================================================================
# Kurulum
# ==========================================================================
def log_dir() -> str:
    """Gunluk dizini (gerekirse olusturulur)."""
    base = os.environ.get("DISKULTIMATE_LOG_DIR") or LOG_DIR
    path = os.path.join(base, "runtime")
    os.makedirs(path, exist_ok=True)
    return path


def report_dir() -> str:
    """Donma / cokme raporlarinin dizini."""
    base = os.environ.get("DISKULTIMATE_LOG_DIR") or LOG_DIR
    path = os.path.join(base, "freeze")
    os.makedirs(path, exist_ok=True)
    return path


def _prune(path: str, prefix: str, keep: int) -> None:
    """Eski dosyalari siler; gunluk dizini sinirsiz buyumesin."""
    try:
        names = sorted(n for n in os.listdir(path) if n.startswith(prefix))
    except OSError:
        return
    for name in names[:-keep] if len(names) > keep else []:
        try:
            os.remove(os.path.join(path, name))
        except OSError:
            pass


def configure(*, verbose: Optional[bool] = None, crash_handler: bool = True) -> str:
    """Tanilamayi baslatir ve oturum gunlugunun yolunu dondurur.

    Iki kez cagrilmasi zararsizdir; ikinci cagri ayni yolu dondurur.
    """
    global _logger, _session_log, _enabled, _verbose, _crash_log, _crash_handle
    global _start_time
    with _lock:
        if _logger is not None:
            return _session_log
        if os.environ.get("DISKULTIMATE_DIAG") == "0":
            _enabled = False
            return ""
        _enabled = True
        _verbose = (verbose if verbose is not None
                    else os.environ.get("DISKULTIMATE_DIAG_VERBOSE") == "1")
        _start_time = time.monotonic()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        folder = log_dir()
        _prune(folder, "session-", KEEP_SESSIONS)
        _session_log = os.path.join(folder, f"session-{stamp}-{os.getpid()}.log")

        logger = logging.getLogger("diskultimate")
        logger.setLevel(logging.DEBUG if _verbose else logging.INFO)
        logger.propagate = False
        handler = logging.FileHandler(_session_log, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s.%(msecs)03d %(levelname)-7s [%(threadName)s] %(message)s",
            datefmt="%H:%M:%S"))
        logger.addHandler(handler)
        _logger = logger

        if crash_handler:
            _crash_log = os.path.join(folder, f"crash-{stamp}-{os.getpid()}.log")
            try:
                _crash_handle = open(_crash_log, "w", encoding="utf-8")
                faulthandler.enable(file=_crash_handle, all_threads=True)
            except Exception:       # tanilama hicbir zaman uygulamayi durdurmaz
                _crash_handle = None
        info(f"tanilama basladi — python {sys.version.split()[0]} "
             f"— pid {os.getpid()} — platform {sys.platform}")
        return _session_log


def enabled() -> bool:
    return _enabled


def session_log() -> str:
    """Bu oturumun gunluk dosyasi (tanilama kapaliysa bos dize)."""
    return _session_log


def last_report() -> str:
    """En son yazilan donma raporunun yolu."""
    with _lock:
        if _last_report:
            return _last_report
    try:
        names = sorted(n for n in os.listdir(report_dir()) if n.startswith("freeze-"))
    except OSError:
        return ""
    return os.path.join(report_dir(), names[-1]) if names else ""


# ==========================================================================
# Gunluk
# ==========================================================================
def info(message: str) -> None:
    if _logger is not None:
        _logger.info(message)


def debug(message: str) -> None:
    if _logger is not None:
        _logger.debug(message)


def warn(message: str) -> None:
    if _logger is not None:
        _logger.warning(message)


def error(message: str, exc: Optional[BaseException] = None) -> None:
    if _logger is None:
        return
    if exc is not None:
        _logger.error("%s: %s\n%s", message, exc,
                      "".join(traceback.format_exception(
                          type(exc), exc, exc.__traceback__)))
    else:
        _logger.error(message)


# ==========================================================================
# Sure olcumu — span
# ==========================================================================
class Span:
    """Suresi olculen tek bir islem.

    `span()` ile acilir, cikista kapanir. Acik olanlar is parcacigi basina
    bir yiginda tutulur; donma raporu "o an ne yapiliyordu" sorusunu bu
    yigindan yanitlar.
    """

    __slots__ = ("name", "detail", "started", "thread")

    def __init__(self, name: str, detail: str, thread: int):
        self.name = name
        self.detail = detail
        self.started = time.monotonic()
        self.thread = thread

    def elapsed_ms(self) -> float:
        return (time.monotonic() - self.started) * 1000.0

    def label(self) -> str:
        return f"{self.name}({self.detail})" if self.detail else self.name


def _describe(fields: Dict[str, Any]) -> str:
    return ", ".join(f"{k}={v}" for k, v in fields.items())


@contextmanager
def span(name: str, track: bool = True, warn_on_error: bool = True,
         **fields: Any):
    """Bir islemin suresini olcer ve gunluge yazar.

        with diagnostics.span("disks.scan", count=3):
            ...

    Esigi (`SLOW_MS`) asan islemler `YAVAS` olarak isaretlenir. Tanilama
    kapaliysa hicbir sey yapmaz (olcum maliyeti sifira yakindir).

    `track=False`: cok sik tekrarlanan islemler (sektor okuma gibi) icindir.
    Islem yine "su an calisan" listesine girer ve yavassa gunluge yazilir, ama
    gecmis tamponunu doldurmaz — yoksa donma oncesi asil olaylar tampondan
    tasip kaybolurdu.

    `warn_on_error=False`: basarisizligi **beklenen** yoklamalar icindir (var
    olmayan aygiti acmaya calismak gibi). Hata yine kaydedilir ama uyari degil
    ayrinti seviyesindedir; yoksa her tarama turu onlarca uyari satiri uretirdi.
    """
    if not _enabled:
        yield None
        return
    ident = threading.get_ident()
    record = Span(name, _describe(fields), ident)
    with _lock:
        _open_spans.setdefault(ident, []).append(record)
    failed = ""
    try:
        yield record
    except BaseException as exc:                     # noqa: BLE001 — yeniden atilir
        failed = f" HATA {type(exc).__name__}: {exc}"
        raise
    finally:
        duration = record.elapsed_ms()
        with _lock:
            stack = _open_spans.get(ident)
            if stack:
                stack.pop()
                if not stack:
                    _open_spans.pop(ident, None)
            if track or duration >= SLOW_MS:
                _history.append((time.time(), duration, record.label(),
                                 threading.current_thread().name))
        if failed and warn_on_error:
            warn(f"{record.label()} — {duration:.0f} ms{failed}")
        elif failed:
            debug(f"{record.label()} — {duration:.0f} ms{failed}")
            if duration >= SLOW_MS:
                warn(f"YAVAS {record.label()} — {duration:.0f} ms (basarisiz)")
        elif duration >= SLOW_MS:
            warn(f"YAVAS {record.label()} — {duration:.0f} ms")
        elif _verbose:
            debug(f"{record.label()} — {duration:.0f} ms")


def timed(name: str = "", **fields: Any):
    """`span` icin sarmalayici bezeme (decorator)."""
    def wrapper(func):
        label = name or f"{func.__module__.split('.')[-1]}.{func.__name__}"

        def inner(*args, **kwargs):
            with span(label, **fields):
                return func(*args, **kwargs)
        inner.__name__ = func.__name__
        inner.__doc__ = func.__doc__
        inner.__wrapped__ = func
        return inner
    return wrapper


def current_activity(thread: Optional[int] = None) -> List[str]:
    """Verilen is parcacigindaki acik islemler (en disdan ice dogru)."""
    with _lock:
        if thread is None:
            items: List[Span] = []
            for stack in _open_spans.values():
                items.extend(stack)
        else:
            items = list(_open_spans.get(thread, ()))
        return [f"{s.label()} — {s.elapsed_ms():.0f} ms surdu" for s in items]


def recent(count: int = 30) -> List[str]:
    """Son tamamlanan islemler, en yeniden eskiye."""
    with _lock:
        items = list(_history)[-count:]
    out = []
    for when, duration, label, thread in reversed(items):
        stamp = time.strftime("%H:%M:%S", time.localtime(when))
        out.append(f"{stamp}  {duration:8.0f} ms  [{thread}]  {label}")
    return out


# ==========================================================================
# Donma yakalayici
# ==========================================================================
def _thread_names() -> Dict[int, str]:
    return {t.ident: t.name for t in threading.enumerate() if t.ident is not None}


def stack_dump(only: Optional[int] = None) -> str:
    """Butun is parcaciklarinin (veya yalnizca birinin) yigin dokumu."""
    names = _thread_names()
    frames = sys._current_frames()          # resmi tanilama yolu
    parts: List[str] = []
    for ident, frame in sorted(frames.items()):
        if only is not None and ident != only:
            continue
        name = names.get(ident, "?")
        parts.append(f"--- is parcacigi {name} (id {ident}) ---")
        parts.extend(line.rstrip("\n") for line in traceback.format_stack(frame))
    return "\n".join(parts)


class Watchdog(threading.Thread):
    """Arayuz is parcaciginin nabzini dinler; durursa yigin doker.

    Arayuz bloke oldugunda Qt olay dongusu durur, dolayisiyla arayuz
    tarafindaki zamanlayici da durur. Bu is parcacigi ayri calistigi icin
    bundan etkilenmez ve **tam olarak nerede asili kalindigini** yakalar.
    """

    def __init__(self, stall_ms: float = STALL_MS, sample_ms: float = SAMPLE_MS,
                 callback=None):
        super().__init__(name="diag-watchdog", daemon=True)
        self.stall_ms = stall_ms
        self.sample_ms = sample_ms
        self.callback = callback            # (rapor yolu, saniye) -> None
        self._beat = time.monotonic()
        self._stop = threading.Event()
        self._main = threading.main_thread().ident
        self.stall_count = 0
        self.worst_ms = 0.0

    # -- arayuzden cagrilir ------------------------------------------------
    def beat(self) -> None:
        self._beat = time.monotonic()

    def stop(self) -> None:
        self._stop.set()

    # -- arka plan ---------------------------------------------------------
    def run(self) -> None:
        report: Optional[str] = None
        began = 0.0
        sampled = 0.0
        while not self._stop.wait(self.sample_ms / 1000.0):
            now = time.monotonic()
            silent = (now - self._beat) * 1000.0
            if silent >= self.stall_ms:
                if report is None:
                    began = self._beat
                    report = self._open_report(silent)
                    sampled = now
                elif (now - sampled) * 1000.0 >= RESAMPLE_MS:
                    self._append_sample(report, silent)
                    sampled = now
            elif report is not None:
                total = self._beat - began
                self._close_report(report, total)
                self.stall_count += 1
                self.worst_ms = max(self.worst_ms, total * 1000.0)
                if self.callback is not None:
                    try:
                        self.callback(report, total)
                    except Exception:
                        pass
                report = None

    # -- rapor -------------------------------------------------------------
    def _open_report(self, silent_ms: float) -> str:
        global _last_report
        folder = report_dir()
        _prune(folder, "freeze-", KEEP_REPORTS)
        path = os.path.join(folder, f"freeze-{time.strftime('%Y%m%d-%H%M%S')}.md")
        activity = current_activity(self._main) or ["(isaretli islem yok)"]
        lines = [
            f"# Donma raporu — {time.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            f"Arayuz **{silent_ms / 1000.0:.1f} sn** boyunca nabiz vermedi "
            f"(esik {self.stall_ms / 1000.0:.1f} sn).",
            "",
            "## O an acik islem",
            "",
        ]
        lines += [f"- {a}" for a in activity]
        lines += ["", "## Arayuz is parcaciginin yigini", "", "```"]
        lines.append(stack_dump(self._main) or "(yigin alinamadi)")
        lines += ["```", "", "## Butun is parcaciklari", "", "```",
                  stack_dump(), "```", "",
                  "## Donmadan onceki son islemler", "", "```"]
        lines += recent(40) or ["(kayit yok)"]
        lines += ["```", "", "## Ek ornekler", ""]
        _write(path, "\n".join(lines))
        with _lock:
            _last_report = path
        warn(f"DONMA — arayuz {silent_ms / 1000.0:.1f} sn yanit vermiyor; "
             f"rapor: {path}")
        for line in activity:
            warn(f"  acik islem: {line}")
        return path

    def _append_sample(self, path: str, silent_ms: float) -> None:
        block = ["", f"### {silent_ms / 1000.0:.1f} sn sonra", "", "```",
                 stack_dump(self._main) or "(yigin alinamadi)", "```"]
        _write(path, "\n".join(block), append=True)

    def _close_report(self, path: str, seconds: float) -> None:
        _write(path, f"\n---\n\n**Arayuz {seconds:.1f} sn sonra yaniti geri "
                     f"verdi.**\n", append=True)
        warn(f"DONMA bitti — toplam {seconds:.1f} sn")


def _write(path: str, text: str, append: bool = False) -> None:
    try:
        with open(path, "a" if append else "w", encoding="utf-8") as handle:
            handle.write(text)
            if not text.endswith("\n"):
                handle.write("\n")
    except OSError:
        pass


def start_watchdog(stall_ms: float = 0.0, callback=None) -> Optional[Watchdog]:
    """Donma yakalayiciyi baslatir (zaten calisiyorsa onu dondurur)."""
    global _watchdog
    with _lock:
        if not _enabled:
            return None
        if _watchdog is not None:
            return _watchdog
        threshold = stall_ms or float(
            os.environ.get("DISKULTIMATE_DIAG_STALL_MS") or STALL_MS)
        _watchdog = Watchdog(stall_ms=threshold, callback=callback)
        _watchdog.start()
        info(f"donma yakalayici calisiyor (esik {threshold:.0f} ms)")
        return _watchdog


def watchdog() -> Optional[Watchdog]:
    return _watchdog


def beat() -> None:
    """Arayuz is parcacigindan duzenli cagrilir."""
    if _watchdog is not None:
        _watchdog.beat()


def stop_watchdog() -> None:
    global _watchdog
    with _lock:
        if _watchdog is not None:
            _watchdog.stop()
            _watchdog = None


def dump_now(reason: str = "elle") -> str:
    """Anlik yigin dokumunu rapor dosyasina yazar ve yolunu dondurur."""
    folder = report_dir()
    path = os.path.join(folder, f"dump-{time.strftime('%Y%m%d-%H%M%S')}.md")
    lines = [f"# Yigin dokumu — {time.strftime('%Y-%m-%d %H:%M:%S')} ({reason})",
             "", "## Acik islemler", ""]
    lines += [f"- {a}" for a in (current_activity() or ["(yok)"])]
    lines += ["", "## Butun is parcaciklari", "", "```", stack_dump(), "```",
              "", "## Son islemler", "", "```"]
    lines += recent(40) or ["(kayit yok)"]
    lines.append("```")
    _write(path, "\n".join(lines))
    info(f"yigin dokumu yazildi: {path}")
    return path


def status() -> Dict[str, Any]:
    """Tanilama durumunun ozeti (arayuzde gosterilir)."""
    dog = _watchdog
    return {
        "enabled": _enabled,
        "verbose": _verbose,
        "log": _session_log,
        "crash_log": _crash_log,
        "report": last_report(),
        "stall_threshold_ms": dog.stall_ms if dog else 0.0,
        "stalls": dog.stall_count if dog else 0,
        "worst_stall_ms": dog.worst_ms if dog else 0.0,
        "uptime_s": (time.monotonic() - _start_time) if _start_time else 0.0,
        "open_spans": current_activity(),
    }
