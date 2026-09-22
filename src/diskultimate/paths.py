"""Proje icindeki standart yollar.

Kural: uygulama ve testler, sistem gecici dizinine (`/tmp`) degil proje
dizinindeki `.tmp/` altina yazar. Boylece uretilen her sey proje dizininde
kalir ve tek yerden temizlenebilir.

Gecersiz kilma: `DISKULTIMATE_SCRATCH` ortam degiskeni verilirse o kullanilir.

Paketlenmis kopya (PyInstaller) ayri tutulur: orada proje dizini yoktur.
`__file__` gecici cikartma dizinini gosterdigi icin buradan uretilen yol
uygulamanin **disina**, sistemin gecici dizinine dusuyordu: gunlukler her
acilista silinen bir yere yaziliyor, root olarak calisildiginda da sahiplik
degistiriyordu. Paketlenmis kopyada kullanicinin veri dizini kullanilir
(`core.platform.user_data_dir`).
"""
from __future__ import annotations

import os
import sys

# src/diskultimate/paths.py -> src/diskultimate -> src -> <proje kok>
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CLAUDE_DIR = os.path.join(PROJECT_ROOT, ".claude")
LOG_DIR = os.path.join(CLAUDE_DIR, "logs")      # yalnizca kaynaktan calisirken

# PyInstaller ve benzerleri bu bayragi koyar.
IS_FROZEN = bool(getattr(sys, "frozen", False))


def data_root() -> str:
    """Uygulamanin urettigi dosyalarin kok dizini."""
    if IS_FROZEN:
        from .core.platform import user_data_dir
        return user_data_dir()
    return PROJECT_ROOT


def log_root() -> str:
    """Gunluk kok dizini; `runtime/` ve `freeze/` bunun altindadir."""
    if IS_FROZEN:
        return os.path.join(data_root(), "logs")
    return LOG_DIR


def scratch_root() -> str:
    """Gecici calisma alaninin kokunu dondurur (gerekirse olusturur)."""
    path = os.environ.get("DISKULTIMATE_SCRATCH") or os.path.join(data_root(), ".tmp")
    os.makedirs(path, exist_ok=True)
    return path


def scratch(*parts: str) -> str:
    """Gecici alan altinda bir alt yol dondurur ve dizinini olusturur."""
    path = os.path.join(scratch_root(), *parts)
    os.makedirs(path, exist_ok=True)
    return path
