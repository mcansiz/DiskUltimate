"""Proje icindeki standart yollar.

Kural: uygulama ve testler, sistem gecici dizinine (`/tmp`) degil proje
dizinindeki `.tmp/` altina yazar. Boylece uretilen her sey proje dizininde
kalir ve tek yerden temizlenebilir.

Gecersiz kilma: `DISKULTIMATE_SCRATCH` ortam degiskeni verilirse o kullanilir.
"""
from __future__ import annotations

import os

# src/diskultimate/paths.py -> src/diskultimate -> src -> <proje kok>
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CLAUDE_DIR = os.path.join(PROJECT_ROOT, ".claude")
LOG_DIR = os.path.join(CLAUDE_DIR, "logs")


def scratch_root() -> str:
    """Gecici calisma alaninin kokunu dondurur (gerekirse olusturur)."""
    yol = os.environ.get("DISKULTIMATE_SCRATCH") or os.path.join(PROJECT_ROOT, ".tmp")
    os.makedirs(yol, exist_ok=True)
    return yol


def scratch(*parts: str) -> str:
    """Gecici alan altinda bir alt yol dondurur ve dizinini olusturur."""
    yol = os.path.join(scratch_root(), *parts)
    os.makedirs(yol, exist_ok=True)
    return yol
