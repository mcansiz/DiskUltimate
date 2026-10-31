"""`QtWidgets` — secili baglamadan (bkz. `ui/qt/__init__.py`).

Qt6 `QAction`, `QActionGroup`, `QShortcut`, `QFileSystemModel` siniflarini
QtGui'ye tasidi; Qt5'teki gibi buradan da alinabilsinler diye eklenir.
"""
# flake8: noqa
from . import QT6

if QT6:
    from PyQt6.QtWidgets import *
    from PyQt6.QtGui import QAction, QActionGroup, QFileSystemModel, QShortcut
else:
    from PyQt5.QtWidgets import *
