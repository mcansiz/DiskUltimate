"""Dosya onizleme penceresi (metin veya hex)."""
from __future__ import annotations

from typing import Optional

from PyQt5.QtGui import QFontDatabase
from PyQt5.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QPlainTextEdit,
                             QTabWidget, QVBoxLayout)

from ...core.filesystem import FileNode
from ...core.ptable import human_size
from ..widgets.hex_view import hexdump


class PreviewDialog(QDialog):
    def __init__(self, node: FileNode, data: bytes, text: Optional[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Onizleme — {node.name}")
        self.resize(820, 560)
        layout = QVBoxLayout(self)
        info = QLabel(f"<b>{node.path}</b> — {human_size(node.size)} "
                       f"(ilk {human_size(len(data))} gosteriliyor)")
        layout.addWidget(info)

        sekmeler = QTabWidget()
        mono = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        mono.setPointSize(9)
        if text is not None:
            metin = QPlainTextEdit(text)
            metin.setReadOnly(True)
            metin.setFont(mono)
            sekmeler.addTab(metin, "Metin")
        hexed = QPlainTextEdit(hexdump(data))
        hexed.setReadOnly(True)
        hexed.setFont(mono)
        hexed.setLineWrapMode(QPlainTextEdit.NoWrap)
        sekmeler.addTab(hexed, "Onaltilik")
        layout.addWidget(sekmeler, 1)

        butonlar = QDialogButtonBox(QDialogButtonBox.Close)
        butonlar.button(QDialogButtonBox.Close).setText("Kapat")
        butonlar.rejected.connect(self.reject)
        layout.addWidget(butonlar)
