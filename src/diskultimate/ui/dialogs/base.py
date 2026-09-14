"""Diyalog gosterim yardimcisi.

Qt5'in yerel Wayland eklentisinde modal pencerelerin ilk karesi bazi
bilesikleyicilerde boyanmadan kaliyor. Uygulama varsayilan olarak XWayland
(xcb) uzerinde acildigi icin bu sorun yasanmaz; yine de kullanici
`DISKULTIMATE_QPA=wayland` ile yerel Wayland'i zorlarsa diye gosterim sonrasi
bir yeniden cizim tetiklenir.
"""
from __future__ import annotations

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QDialog


def exec_dialog(dlg: QDialog) -> int:
    """Diyalogu modal olarak calistirir ve sonucunu dondurur."""
    dlg.adjustSize()
    QTimer.singleShot(0, dlg.update)
    return dlg.exec_()
