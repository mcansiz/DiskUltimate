"""`QtTest` — secili baglamadan (bkz. `ui/qt/__init__.py`)."""
# flake8: noqa
from . import QT6

if QT6:
    from PyQt6.QtTest import *
else:
    from PyQt5.QtTest import *
